"""A claim is a sentence plus the evidence it cites and the numbers it states, each recomputable from the events."""
import math
from dataclasses import dataclass

from . import metrics
from .context import MatchContext
from .geometry import GOAL_Y, PITCH_L


@dataclass(frozen=True)
class Fact:
    """One number a claim states. `name` picks how it is recomputed; the other fields say where."""
    name: str
    value: float
    event_id: str | None = None
    team: str | None = None
    at_s: float | None = None


@dataclass(frozen=True)
class Claim:
    text: str
    evidence_ids: tuple[str, ...]
    facts: tuple[Fact, ...] = ()
    player_ids: tuple[str, ...] = ()
    requires_any: tuple[str, ...] = ()  # the sentence must use at least one of these words or phrases (what happened)
    forbids: tuple[str, ...] = ()       # the sentence must use none of these (words that would contradict it)


# Absolute tolerance when comparing a stated fact with the recomputed value [A: display rounding].
TOLERANCE = {
    "minute": 0.0, "minute_at": 0.0, "score_home": 0.0, "score_away": 0.0,
    "shot_distance_m": 0.5, "shot_speed_kmh": 0.5, "actor_speed_kmh": 0.5,
    "pressure_per_min": 0.05, "momentum": 0.05,
}


def _from_event(fact: Fact, ctx: MatchContext) -> float | None:
    event = ctx.event(fact.event_id) if fact.event_id else None
    if event is None:
        return None
    if fact.name == "minute":
        return float(ctx.minute(event))
    if fact.name in ("score_home", "score_away"):
        score = ctx.score_after(event.event_id)
        return None if score is None else float(score["H" if fact.name == "score_home" else "A"])
    if event.type == "shot" and fact.name == "shot_distance_m":
        return math.hypot(PITCH_L - event.x, event.y - GOAL_Y)
    if event.type == "shot" and fact.name == "shot_speed_kmh":
        return event.shot_speed_kmh
    if fact.name == "actor_speed_kmh":
        return getattr(event, "actor_speed_kmh", None)
    return None


def _from_time(fact: Fact, ctx: MatchContext) -> float | None:
    if fact.name == "minute_at":
        return float(ctx.minute_at(fact.at_s))
    if fact.name == "pressure_per_min" and fact.team in ("H", "A"):
        return metrics.pressure_intensity(ctx.events(), fact.team, fact.at_s)
    if fact.name == "momentum":
        return metrics.momentum_at(ctx.events(), fact.at_s)
    return None


def recompute(fact: Fact, ctx: MatchContext) -> float | None:
    """The value the events give for this fact, or None when it cannot be computed (unknown name or missing evidence)."""
    if fact.name not in TOLERANCE:
        return None
    return _from_time(fact, ctx) if fact.at_s is not None else _from_event(fact, ctx)
