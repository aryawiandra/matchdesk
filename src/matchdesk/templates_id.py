"""Bahasa Indonesia templates. Same facts, evidence and rules as the English ones in `explainer.py`; only the words
differ. Every number is also a Fact, so the Verifier recomputes it. Indonesian writes decimals with a comma."""
import math

from . import metrics
from .analyst import Detection
from .claims import Claim, Fact
from .context import MatchContext
from .geometry import GOAL_Y, PITCH_L

LANG = "id"
SCORED_WORDS = ("mencetak", "gol", "membobol", "menjebol", "menyarangkan")
NOT_A_GOAL = ("mencetak", "membobol", "menjebol", "menyarangkan")
OUTCOME_PHRASE = {"goal": "gol", "saved": "ditepis kiper", "blocked": "diblok", "off_target": "melebar"}
OUTCOME_WORDS = {
    "saved": ("ditepis", "diselamatkan", "tepisan"),
    "blocked": ("diblok", "diblokir", "terblok", "dihadang"),
    "off_target": ("melebar", "meleset", "di luar sasaran"),
}


def _who(ctx: MatchContext, player_id: str, team: str) -> str:
    return f"{ctx.player(player_id).name} ({ctx.club_name(team)})"


def _shot_distance(shot) -> float:
    return math.hypot(PITCH_L - shot.x, shot.y - GOAL_Y)


def _goal(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    shot = ctx.event(detection.event_ids[-1])
    score = ctx.score_after(shot.event_id)
    minute, distance = ctx.minute(shot), _shot_distance(shot)
    main = Claim(
        f"{_who(ctx, shot.player_id, shot.team)} mencetak gol pada menit {minute} dari jarak {distance:.0f} m, "
        f"skor menjadi {score['H']}-{score['A']}.",
        detection.event_ids,
        (Fact("minute", minute, shot.event_id), Fact("shot_distance_m", distance, shot.event_id),
         Fact("score_home", score["H"], shot.event_id), Fact("score_away", score["A"], shot.event_id)),
        (shot.player_id,),
        requires_any=SCORED_WORDS, lang=LANG,
    )
    speed = Claim(
        f"Tembakan itu melaju {shot.shot_speed_kmh:.0f} km/jam.", (shot.event_id,),
        (Fact("shot_speed_kmh", shot.shot_speed_kmh, shot.event_id),), (shot.player_id,), lang=LANG,
    )
    return (main, speed)


def _big_chance(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    shot = ctx.event(detection.event_ids[-1])
    minute, distance = ctx.minute(shot), _shot_distance(shot)
    return (Claim(
        f"{_who(ctx, shot.player_id, shot.team)} mendapat peluang besar pada menit {minute}, "
        f"tembakan dari jarak {distance:.0f} m yang {OUTCOME_PHRASE[shot.outcome]}.",
        (shot.event_id,),
        (Fact("minute", minute, shot.event_id), Fact("shot_distance_m", distance, shot.event_id)),
        (shot.player_id,),
        requires_any=OUTCOME_WORDS[shot.outcome], forbids=NOT_A_GOAL, lang=LANG,
    ),)


def _sprint(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    event = ctx.event(detection.event_ids[0])
    minute = ctx.minute(event)
    return (Claim(
        f"{_who(ctx, detection.player_id, detection.team)} berlari hingga {event.actor_speed_kmh:.0f} km/jam "
        f"pada menit {minute}.",
        (event.event_id,),
        (Fact("actor_speed_kmh", event.actor_speed_kmh, event.event_id), Fact("minute", minute, event.event_id)),
        (detection.player_id,),
        forbids=NOT_A_GOAL, lang=LANG,
    ),)


def _pressing(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    level = metrics.pressure_intensity(ctx.events(), detection.team, detection.end_s)
    minute = ctx.minute_at(detection.end_s)
    level_text = f"{level:.1f}".replace(".", ",")
    return (Claim(
        f"{ctx.club_name(detection.team)} menekan dengan {level_text} tekanan per menit "
        f"selama tiga menit terakhir, hingga menit {minute}.",
        detection.event_ids,
        (Fact("pressure_per_min", level, team=detection.team, at_s=detection.end_s),
         Fact("minute_at", minute, at_s=detection.end_s)),
        requires_any=("menekan", "tekanan", "pressing"), forbids=NOT_A_GOAL, lang=LANG,
    ),)


def _swing(detection: Detection, ctx: MatchContext) -> tuple[Claim, ...]:
    minute = ctx.minute_at(detection.end_s)
    return (Claim(
        f"Momentum beralih ke {ctx.club_name(detection.team)} sekitar menit {minute}.",
        detection.event_ids,
        (Fact("minute_at", minute, at_s=detection.end_s),),
        requires_any=("momentum",), lang=LANG,
    ),)


TEMPLATES = {
    "goal": _goal, "big_chance": _big_chance, "sprint_burst": _sprint,
    "pressing_spell": _pressing, "momentum_swing": _swing,
}
