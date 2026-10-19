"""Explainer: turn a detection into verified claims. A model (Foundry, later) may propose claims; the Verifier gates
them; after the allowed retries the deterministic template is used, so the screen never blanks (idea.md section 3)."""
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from . import metrics
from .analyst import Detection
from .claims import Claim, Fact
from .context import MatchContext
from .geometry import GOAL_Y, PITCH_L
from .verifier import verify_claims

Source = Literal["model", "model_retry", "template"]
Model = Callable[[Detection, MatchContext, tuple[str, ...]], Sequence[Claim]]
OUTCOME_PHRASE = {"goal": "a goal", "saved": "saved", "blocked": "blocked", "off_target": "off target"}
# Words a rewording must keep (what happened) or must not use (what would contradict it); see verifier._meaning_reasons.
SCORED_WORDS = ("scored", "scores", "goal", "net", "netted")
NOT_A_GOAL = ("scored", "scores", "scoring", "netted")
OUTCOME_WORDS = {"saved": ("saved", "save"), "blocked": ("blocked", "block"),
                 "off_target": ("off target", "wide", "missed", "miss")}


@dataclass(frozen=True)
class AttemptFailure:
    attempt: int
    reasons: tuple[str, ...]
    rejected_claims: int


@dataclass(frozen=True)
class Explanation:
    detection: Detection
    claims: tuple[Claim, ...]
    source: Source
    attempts: int
    rejections: tuple[AttemptFailure, ...]

    @property
    def rejected_claims(self) -> int:
        return sum(f.rejected_claims for f in self.rejections)


# ---- templates (every number is also a Fact, so the Verifier can recompute it)
def _who(ctx: MatchContext, player_id: str, team: str) -> str:
    return f"{ctx.player(player_id).name} ({ctx.club_name(team)})"


def _shot_distance(shot) -> float:
    return math.hypot(PITCH_L - shot.x, shot.y - GOAL_Y)


def _goal(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    shot = ctx.event(detection.event_ids[-1])
    score = ctx.score_after(shot.event_id)
    minute, distance = ctx.minute(shot), _shot_distance(shot)
    main = Claim(
        f"{_who(ctx, shot.player_id, shot.team)} scored in minute {minute} from {distance:.0f} m, "
        f"making it {score['H']}-{score['A']}.",
        detection.event_ids,
        (Fact("minute", minute, shot.event_id), Fact("shot_distance_m", distance, shot.event_id),
         Fact("score_home", score["H"], shot.event_id), Fact("score_away", score["A"], shot.event_id)),
        (shot.player_id,),
        requires_any=SCORED_WORDS,
    )
    speed = Claim(
        f"The shot left at {shot.shot_speed_kmh:.0f} km/h.", (shot.event_id,),
        (Fact("shot_speed_kmh", shot.shot_speed_kmh, shot.event_id),), (shot.player_id,),
    )
    return (main, speed)


def _big_chance(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    shot = ctx.event(detection.event_ids[-1])
    minute, distance = ctx.minute(shot), _shot_distance(shot)
    return (Claim(
        f"{_who(ctx, shot.player_id, shot.team)} had a big chance in minute {minute}, "
        f"a shot from {distance:.0f} m that was {OUTCOME_PHRASE[shot.outcome]}.",
        (shot.event_id,),
        (Fact("minute", minute, shot.event_id), Fact("shot_distance_m", distance, shot.event_id)),
        (shot.player_id,),
        requires_any=OUTCOME_WORDS[shot.outcome], forbids=NOT_A_GOAL,
    ),)


def _sprint(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    event = ctx.event(detection.event_ids[0])
    minute = ctx.minute(event)
    return (Claim(
        f"{_who(ctx, detection.player_id, detection.team)} reached {event.actor_speed_kmh:.0f} km/h in minute {minute}.",
        (event.event_id,),
        (Fact("actor_speed_kmh", event.actor_speed_kmh, event.event_id), Fact("minute", minute, event.event_id)),
        (detection.player_id,),
        forbids=NOT_A_GOAL,
    ),)


def _pressing(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    level = metrics.pressure_intensity(ctx.events(), detection.team, detection.end_s)
    minute = ctx.minute_at(detection.end_s)
    return (Claim(
        f"{ctx.club_name(detection.team)} pressed at {level:.1f} pressures per minute over the last three minutes, "
        f"up to minute {minute}.",
        detection.event_ids,
        (Fact("pressure_per_min", level, team=detection.team, at_s=detection.end_s),
         Fact("minute_at", minute, at_s=detection.end_s)),
        requires_any=("press", "pressed", "pressing", "pressure", "pressures"), forbids=NOT_A_GOAL,
    ),)


def _swing(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    minute = ctx.minute_at(detection.end_s)
    return (Claim(
        f"Momentum swung toward {ctx.club_name(detection.team)} around minute {minute}.",
        detection.event_ids,
        (Fact("minute_at", minute, at_s=detection.end_s),),
        requires_any=("momentum",),
    ),)


_TEMPLATES = {
    "goal": _goal, "big_chance": _big_chance, "sprint_burst": _sprint,
    "pressing_spell": _pressing, "momentum_swing": _swing,
}


def template_claims(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    builder = _TEMPLATES.get(detection.kind)
    if builder is None:
        raise ValueError(f"no template for key moment kind {detection.kind!r}")
    return builder(detection, ctx)


# ---- explain -> verify -> retry -> template
def explain(detection: Detection, ctx: MatchContext, model: Model | None = None, max_retries: int = 1) -> Explanation:
    failures: list[AttemptFailure] = []
    feedback: tuple[str, ...] = ()
    attempts = 0
    for attempt in range(1, max_retries + 2) if model is not None else ():
        attempts = attempt
        try:
            claims = tuple(model(detection, ctx, feedback))
        except Exception as error:  # a failing model must never blank the screen
            feedback = (f"explainer error: {type(error).__name__}: {error}",)
            failures.append(AttemptFailure(attempt, feedback, 0))
            continue
        result = verify_claims(claims, ctx)
        if result.ok:
            return Explanation(detection, claims, "model" if attempt == 1 else "model_retry", attempt, tuple(failures))
        feedback = tuple(r for rejection in result.rejected for r in rejection.reasons) or ("no claims returned",)
        failures.append(AttemptFailure(attempt, feedback, len(result.rejected)))
    fallback = template_claims(detection, ctx)
    check = verify_claims(fallback, ctx)
    if not check.ok:
        reasons = [r for rejection in check.rejected for r in rejection.reasons]
        raise RuntimeError(f"template claims failed verification for {detection.kind}: {reasons}")
    return Explanation(detection, fallback, "template", attempts, tuple(failures))
