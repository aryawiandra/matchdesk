"""Viewer modes built on the same stream: one metric on its own (`metric_tick` overlays, METRICS.md sections 3-6),
and player focus (only what involves one player). Both are deterministic, use no model, and only read events that
have already happened at each overlay's start time."""
from collections.abc import Iterable, Iterator, Sequence

from . import metrics
from .overlay import Lang, MetricTickOverlay, MetricTickPayload, Overlay, Persona
from .producer import TICK_S, clock
from .schema import Event, MatchInfo

METRICS = ("momentum", "control", "pressure", "tempo")
FOCUS_ALWAYS = ("scoreboard", "recap")  # context that stays on screen in player focus
PLAYER_FIELDS = ("player_id", "recipient_id", "target_id")

LABELS = {
    "en": {"momentum": "Momentum (home = +)", "control": "Control share", "pressure": "Presses per minute",
           "tempo": "Actions per minute"},
    "id": {"momentum": "Momentum (tuan rumah = +)", "control": "Porsi penguasaan", "pressure": "Tekanan per menit",
           "tempo": "Aksi per menit"},
}


def involves(event: Event, player_id: str) -> bool:
    return any(getattr(event, name, None) == player_id for name in PLAYER_FIELDS)


def _values(events: Sequence[Event], metric: str, at_s: float) -> list[tuple[float, str | None]]:
    if metric == "momentum":
        return [(metrics.momentum_at(events, at_s), None)]
    if metric == "control":
        state = metrics.control_chaos(events, at_s)
        return [(state.control, state.controlling_team)]
    if metric == "pressure":
        return [(metrics.pressure_intensity(events, team, at_s), team) for team in ("H", "A")]
    return [(metrics.tempo(events, at_s), None)]


def metric_overlays(events: Iterable[Event], info: MatchInfo, metric: str, lang: Lang = "en",
                    persona: Persona = "fan", tick_s: float = TICK_S) -> Iterator[MetricTickOverlay]:
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; choose one of {', '.join(METRICS)}")
    stream = list(events)
    index, at_s, last_s = 0, tick_s, stream[-1].match_s if stream else 0.0
    while at_s <= last_s:
        while index < len(stream) and stream[index].match_s <= at_s:
            index += 1
        seen = stream[:index]
        if seen:
            for value, team in _values(seen, metric, at_s):
                label = LABELS[lang][metric]
                suffix = f"-{team}" if metric == "pressure" else ""
                yield MetricTickOverlay(
                    overlay_id=f"OV-{info.match_id}-metric_tick-{metric}-{int(round(at_s * 10)):06d}{suffix}-{lang}-{persona}",
                    match_id=info.match_id, start_s=at_s, end_s=at_s + tick_s, priority=1, lang=lang, persona=persona,
                    evidence_event_ids=(seen[-1].event_id,), a11y_text=f"{label}: {value:.2f}, {clock(at_s)}.",
                    payload=MetricTickPayload(metric=metric, value=round(value, 4), label=label, team=team),
                )
        at_s += tick_s


def focus_player(overlays: Iterable[Overlay], events: Sequence[Event], player_id: str) -> Iterator[Overlay]:
    by_id = {e.event_id: e for e in events}
    for overlay in overlays:
        if overlay.type in FOCUS_ALWAYS or any(
                (event := by_id.get(eid)) is not None and involves(event, player_id) for eid in overlay.evidence_event_ids):
            yield overlay
