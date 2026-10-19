"""The live pipeline: events -> Analyst -> Explainer (+ Verifier) -> Producer -> overlays, in time order."""
from collections.abc import Callable, Iterable, Iterator

from .analyst import Analyst
from .config import Thresholds, load_thresholds
from .context import MatchContext
from .explainer import Explanation, Model, explain
from .overlay import SUPPORTED_LANGS, Lang, Overlay, Persona
from .producer import TICK_S, Producer
from .schema import Event, MatchInfo


def overlay_stream(
    events: Iterable[Event],
    info: MatchInfo,
    thresholds: Thresholds | None = None,
    model: Model | None = None,
    persona: Persona = "fan",
    lang: Lang = "en",
    tick_s: float = TICK_S,
    on_explanation: Callable[[Explanation], None] | None = None,
    max_retries: int = 1,
) -> Iterator[Overlay]:
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"only English and Bahasa Indonesia are produced, not {lang!r}")
    thresholds = thresholds or load_thresholds()
    ctx = MatchContext(info)
    analyst, producer = Analyst(thresholds), Producer(ctx, thresholds)
    next_tick, last_s = tick_s, 0.0
    for event in events:
        ctx.add(event)
        batch: list[Overlay] = []
        while next_tick < event.match_s:
            batch.append(producer.scoreboard(next_tick, tick_s, lang, persona))
            next_tick += tick_s
        for detection in analyst.ingest(event):
            explanation = explain(detection, ctx, model, max_retries, lang)
            if on_explanation is not None:
                on_explanation(explanation)
            batch.extend(producer.for_explanation(explanation, lang, persona))
        batch.sort(key=lambda overlay: overlay.start_s)
        yield from batch
        last_s = event.match_s
    yield producer.scoreboard(last_s, tick_s, lang, persona)
