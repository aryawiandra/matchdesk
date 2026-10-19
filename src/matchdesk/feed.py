"""Live feed: replays a seeded synthetic match as Server-Sent Events. The hidden truth is never sent.

`/overlays/seed/{seed}` runs the Agent Framework pipeline. By default the Explainer writes from templates; with
`explainer=model` it asks the configured local model, behind the same Verifier gate. Open streams are capped (and the
model path has its own, smaller cap) so a crawler or a crowd cannot exhaust the server."""
import asyncio
import os
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path as FsPath
from typing import Literal

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import GeneratorConfig
from .explainer import Model
from .generator import GeneratedMatch, generate_match
from .narrator import narrator_from_spec
from .overlay import Lang, Persona
from .recorded import RecordedModel, Recording, load_directory
from .sse import encode_frame
from .workflow import overlay_stream_async

WEB_DIR = FsPath(__file__).resolve().parents[2] / "web"
RECORDINGS_DIR = FsPath(__file__).resolve().parents[2] / "data" / "recorded"
MAX_SEED = 9999
MIN_PERIOD_S = 60.0
MAX_PERIOD_S = 2700.0
MAX_SPEED = 600.0
DEFAULT_SPEED = 60.0
DEFAULT_MAX_STREAMS = 20
DEFAULT_MAX_MODEL_STREAMS = 1
STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class Slots:
    """A counter with a limit. The event loop is single-threaded, so no lock is needed."""

    def __init__(self, limit: int):
        self.limit = limit
        self.active = 0

    def take(self) -> bool:
        if self.active >= self.limit:
            return False
        self.active += 1
        return True

    def give(self) -> None:
        self.active -= 1


class GuardedStream(StreamingResponse):
    """A streaming response that gives its slots back when it ends, however it ends (finished, cancelled, or never
    started because the client left)."""

    def __init__(self, content: AsyncIterator[str], release: Callable[[], None]):
        super().__init__(content, media_type="text/event-stream", headers=STREAM_HEADERS)
        self._release = release

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


def take_slots(*slots: Slots) -> Callable[[], None] | None:
    """Take every slot or none. Returns the function that gives them back, or None when one is full."""
    taken: list[Slots] = []
    for slot in slots:
        if not slot.take():
            for held in taken:
                held.give()
            return None
        taken.append(slot)
    def release() -> None:
        for held in taken:
            held.give()

    return release


def pace_delay(previous_s: float, current_s: float, speed: float) -> float:
    """Seconds to wait before sending an event: match-time gap divided by speed; zero when unpaced (speed 0)."""
    if speed <= 0:
        return 0.0
    return max(0.0, current_s - previous_s) / speed


async def _frames(match: GeneratedMatch, speed: float) -> AsyncIterator[str]:
    yield encode_frame(match.info.model_dump(mode="json"))
    previous_s = 0.0
    for event in match.events:
        delay = pace_delay(previous_s, event.match_s, speed)
        if delay > 0:
            await asyncio.sleep(delay)
        previous_s = event.match_s
        yield encode_frame(event.model_dump(mode="json"))


async def _overlay_frames(
    match: GeneratedMatch, persona: Persona, speed: float, model: Model | None, lang: Lang,
) -> AsyncIterator[str]:
    previous_s = 0.0
    async for overlay in overlay_stream_async(match.events, match.info, persona=persona, model=model, lang=lang):
        delay = pace_delay(previous_s, overlay.start_s, speed)
        if delay > 0:
            await asyncio.sleep(delay)
        previous_s = max(previous_s, overlay.start_s)
        yield encode_frame(overlay.model_dump(mode="json"))


def _recorded_model(recordings: Mapping[int, Recording], seed: int, period_s: float) -> Model:
    if not recordings:
        raise HTTPException(status_code=400, detail="no recordings are available on this server")
    recording = recordings.get(seed)
    if recording is None:
        raise HTTPException(status_code=400, detail=f"no recording for seed {seed}; recorded seeds: {sorted(recordings)}")
    if recording.period_s != period_s:
        raise HTTPException(status_code=400, detail=f"seed {seed} was recorded for period_s={recording.period_s}")
    return RecordedModel(recording)


def create_app(
    model: Model | None = None,
    max_streams: int = DEFAULT_MAX_STREAMS,
    max_model_streams: int = DEFAULT_MAX_MODEL_STREAMS,
    recordings: Mapping[int, Recording] | None = None,
) -> FastAPI:
    app = FastAPI(title="MatchDesk feed")
    streams, model_streams = Slots(max_streams), Slots(max_model_streams)
    recordings = dict(recordings or {})

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/capabilities")
    async def capabilities() -> dict:
        first = recordings[min(recordings)] if recordings else None
        recorded = None if first is None else {
            "seeds": sorted(recordings), "model": first.model, "recorded_at": first.recorded_at,
            "period_s": first.period_s}
        return {"model": model is not None, "recorded": recorded, "max_streams": max_streams}

    @app.get("/feed/seed/{seed}")
    async def feed(
        seed: int = Path(ge=0, le=MAX_SEED),
        period_s: float = Query(MAX_PERIOD_S, ge=MIN_PERIOD_S, le=MAX_PERIOD_S),
        speed: float = Query(DEFAULT_SPEED, ge=0.0, le=MAX_SPEED),
    ) -> StreamingResponse:
        release = take_slots(streams)
        if release is None:
            raise HTTPException(status_code=429, detail="too many open streams, try again shortly")
        try:
            match = await asyncio.to_thread(generate_match, GeneratorConfig(seed=seed, period_s=period_s))
        except BaseException:
            release()
            raise
        return GuardedStream(_frames(match, speed), release)

    @app.get("/overlays/seed/{seed}")
    async def overlays(
        seed: int = Path(ge=0, le=MAX_SEED),
        period_s: float = Query(MAX_PERIOD_S, ge=MIN_PERIOD_S, le=MAX_PERIOD_S),
        speed: float = Query(DEFAULT_SPEED, ge=0.0, le=MAX_SPEED),
        persona: Persona = Query("fan"),
        lang: Literal["en", "id"] = Query("en"),
        explainer: Literal["template", "model", "recorded"] = Query("template"),
    ) -> StreamingResponse:
        use_model = explainer == "model"
        if use_model and model is None:
            raise HTTPException(status_code=400, detail="no model is configured on this server (set MATCHDESK_MODEL)")
        chosen: Model | None = model if use_model else None
        if explainer == "recorded":
            chosen = _recorded_model(recordings, seed, period_s)
        release = take_slots(streams, *([model_streams] if use_model else []))
        if release is None:
            raise HTTPException(status_code=429, detail="too many open streams, try again shortly")
        try:
            match = await asyncio.to_thread(generate_match, GeneratorConfig(seed=seed, period_s=period_s))
        except BaseException:
            release()
            raise
        return GuardedStream(_overlay_frames(match, persona, speed, chosen, lang), release)

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="renderer")  # last: API routes win
    return app


def create_app_from_env() -> FastAPI:
    """uvicorn factory: `MATCHDESK_MODEL=ollama:<model>` (and optionally `OLLAMA_HOST`) enables the live model
    explainer; recorded model answers are read from `data/recorded` (or `MATCHDESK_RECORDINGS`)."""
    recordings = load_directory(os.environ.get("MATCHDESK_RECORDINGS", RECORDINGS_DIR))
    return create_app(model=narrator_from_spec(os.environ.get("MATCHDESK_MODEL"), os.environ.get("OLLAMA_HOST")),
                      recordings=recordings)
