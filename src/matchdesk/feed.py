"""Live feed: replays a seeded synthetic match as Server-Sent Events. The hidden truth is never sent."""
import asyncio
from collections.abc import AsyncIterator

from fastapi import FastAPI, Path, Query
from fastapi.responses import StreamingResponse

from .config import GeneratorConfig
from .generator import GeneratedMatch, generate_match
from .sse import encode_frame

MAX_SEED = 9999
MIN_PERIOD_S = 60.0
MAX_PERIOD_S = 2700.0
MAX_SPEED = 600.0
DEFAULT_SPEED = 60.0


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


def create_app() -> FastAPI:
    app = FastAPI(title="MatchDesk feed")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/feed/seed/{seed}")
    async def feed(
        seed: int = Path(ge=0, le=MAX_SEED),
        period_s: float = Query(MAX_PERIOD_S, ge=MIN_PERIOD_S, le=MAX_PERIOD_S),
        speed: float = Query(DEFAULT_SPEED, ge=0.0, le=MAX_SPEED),
    ) -> StreamingResponse:
        match = await asyncio.to_thread(generate_match, GeneratorConfig(seed=seed, period_s=period_s))
        return StreamingResponse(
            _frames(match, speed),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
