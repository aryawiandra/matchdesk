"""Reference implementations of the formulas in METRICS.md. Pure functions of the event stream."""
import math
from dataclasses import dataclass
from typing import Sequence

from .config import Thresholds
from .geometry import HALF_X, PITCH_L, in_box

HALF_LIFE_S = 120.0
MIN_MASS = 1.0
PRESSED_WINDOW_S = 4.0
CHAOS_WINDOW_S = 60.0
CHAOS_TURNOVER_CAP = 8.0
CHAOS_CONTROL_MAX = 0.30
CHAOS_CHAOS_MIN = 0.55
PRESSURE_WINDOW_S = 120.0
TEMPO_WINDOW_S = 180.0
TEMPO_BASE_SPAN_S = 600.0
TEMPO_GRID_S = 30.0
RHYTHM_UP, RHYTHM_DOWN = 1.35, 0.70


# ---- section 1-2: passes
def pass_distance(p) -> float:
    return math.hypot(p.end_x - p.x, p.end_y - p.y)


def pass_forward(p) -> float:
    return p.end_x - p.x


def difficulty_logit(distance_m: float, forward_m: float, pressed: bool, into_box: bool) -> float:
    return -2.2 + 0.050 * distance_m + 0.025 * max(forward_m, 0.0) + 1.00 * bool(pressed) + 0.70 * bool(into_box)


def is_pressed(p, pressures: Sequence) -> bool:
    return any(
        q.target_id == p.player_id and 0.0 <= p.match_s - q.match_s <= PRESSED_WINDOW_S for q in pressures
    )


def pass_difficulty(p, pressures: Sequence) -> float:
    z = difficulty_logit(pass_distance(p), pass_forward(p), is_pressed(p, pressures), in_box(p.end_x, p.end_y))
    return 1.0 / (1.0 + math.exp(-z))


def difficulty_band(d: float) -> str:
    return "easy" if d < 0.25 else ("medium" if d < 0.55 else "hard")


def pass_accuracy(passes: Sequence) -> float | None:
    return None if not passes else sum(p.outcome == "complete" for p in passes) / len(passes)


def pass_over_performance(passes: Sequence, pressures: Sequence) -> float:
    return sum((p.outcome == "complete") - (1.0 - pass_difficulty(p, pressures)) for p in passes)


# ---- section 3: momentum
def _weight(e) -> float:
    if e.type == "pass":
        return 0.2 + 0.8 * (e.end_x / PITCH_L) ** 2 if e.outcome == "complete" else 0.0
    if e.type == "shot":
        return 3.0
    if e.type == "tackle":
        return 0.5 if e.outcome == "won" else 0.0
    if e.type == "pressure":
        return 0.4 if e.x >= HALF_X else 0.0
    return 0.0


def momentum_at(events: Sequence, t: float) -> float:
    mass = {"H": 0.0, "A": 0.0}
    for e in events:
        if e.match_s > t:
            continue
        w = _weight(e)
        if w:
            mass[e.team] += w * 0.5 ** ((t - e.match_s) / HALF_LIFE_S)
    total = mass["H"] + mass["A"]
    return 0.0 if total < MIN_MASS else (mass["H"] - mass["A"]) / total


# ---- section 4: control vs chaos
@dataclass(frozen=True)
class ControlChaos:
    chi: float
    control: float
    label: str
    controlling_team: str | None


def control_chaos(events: Sequence, t: float) -> ControlChaos:
    window = [e for e in events if t - CHAOS_WINDOW_S < e.match_s <= t]
    n_pc = sum(e.type == "possession_change" for e in window)
    passes = [e for e in window if e.type == "pass"]
    complete = [e for e in passes if e.outcome == "complete"]
    c_pass = (len(complete) + 1) / (len(passes) + 2)
    chi = 0.5 * min(1.0, n_pc / CHAOS_TURNOVER_CAP) + 0.5 * (1.0 - c_pass)
    label = "control" if chi <= CHAOS_CONTROL_MAX else ("chaos" if chi >= CHAOS_CHAOS_MIN else "transition")
    home = sum(e.team == "H" for e in complete)
    away = len(complete) - home
    team = None
    if label == "control" and home != away:
        team = "H" if home > away else "A"
    return ControlChaos(chi=chi, control=1.0 - chi, label=label, controlling_team=team)


# ---- section 5: pressure
def _recent_pressure(events: Sequence, team: str, t: float, span: float = PRESSURE_WINDOW_S) -> list:
    return [e for e in events if e.type == "pressure" and e.team == team and t - span < e.match_s <= t]


def pressure_intensity(events: Sequence, team: str, t: float) -> float:
    return len(_recent_pressure(events, team, t)) / (PRESSURE_WINDOW_S / 60.0)


def high_press_share(events: Sequence, team: str, t: float) -> float | None:
    recent = _recent_pressure(events, team, t)
    return None if not recent else sum(e.x >= HALF_X for e in recent) / len(recent)


# ---- section 6: tempo and rhythm
def tempo(events: Sequence, t: float) -> float:
    n = sum(e.type in ("pass", "shot", "tackle") and t - TEMPO_WINDOW_S < e.match_s <= t for e in events)
    return n / (TEMPO_WINDOW_S / 60.0)


def rhythm(events: Sequence, t: float) -> str:
    if t < TEMPO_BASE_SPAN_S:
        return "steady"
    grid = [t - TEMPO_WINDOW_S - TEMPO_GRID_S * k for k in range(int(TEMPO_BASE_SPAN_S - TEMPO_WINDOW_S) // int(TEMPO_GRID_S))]
    base = sum(tempo(events, g) for g in grid) / len(grid)
    if base <= 0:
        return "steady"
    shift = tempo(events, t) / base
    return "up" if shift >= RHYTHM_UP else ("down" if shift <= RHYTHM_DOWN else "steady")


# ---- section 7: indicators
@dataclass(frozen=True)
class Chip:
    event_id: str
    player_id: str
    kind: str


def indicator_chips(events: Sequence, th: Thresholds) -> list[Chip]:
    last_speed_chip: dict[str, float] = {}
    last_steps: dict[str, int] = {}
    chips: list[Chip] = []
    for e in events:
        pid = getattr(e, "player_id", None)
        if pid is None or not hasattr(e, "actor_speed_kmh"):
            continue
        if e.actor_speed_kmh >= th.sprint_speed_kmh and e.match_s - last_speed_chip.get(pid, -math.inf) >= th.speed_chip_cooldown_s:
            last_speed_chip[pid] = e.match_s
            chips.append(Chip(e.event_id, pid, "speed"))
        steps = int(e.actor_distance_m // th.distance_step_m)
        if steps > last_steps.get(pid, 0):
            chips.append(Chip(e.event_id, pid, "distance"))
        last_steps[pid] = max(steps, last_steps.get(pid, 0))
    return chips


# ---- evaluation helper
def pearson(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    if n != len(b) or n < 2:
        raise ValueError("series must have equal length >= 2")
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va, vb = sum((x - ma) ** 2 for x in a), sum((y - mb) ** 2 for y in b)
    return 0.0 if va == 0 or vb == 0 else cov / math.sqrt(va * vb)
