"""1 Hz player-movement simulation. Provides speed and cumulative distance for every event actor
and the truth totals. State is mutable and private to one generator run; outputs are immutable."""
import math
import random
from dataclasses import dataclass, field

from .geometry import HALF_X, PITCH_L, PITCH_W, attack_sign, clamp, flip
from .names import SLOTS

MPS_TO_KMH = 3.6
JOG_NOISE_M = 2.0
JOG_FRACTION = 0.22  # [A: design default, tuned so outfield totals land near 10 km; see calibration plan]
MAX_ACCEL_MPS2 = 2.5
MAX_DECEL_MPS2 = 3.5


@dataclass
class Body:
    player_id: str
    team: str
    role: str
    slot_x: float
    slot_y: float
    pace_mps: float
    skill: float
    x: float = 0.0
    y: float = 0.0
    speed: float = 0.0
    dist: float = 0.0
    hist: list[float] = field(default_factory=list)


def make_bodies(rng: random.Random) -> dict[str, Body]:
    bodies: dict[str, Body] = {}
    for team in ("H", "A"):
        for role, shirt, sx, sy in SLOTS:
            pid = f"{team}{shirt:02d}"
            pace = rng.uniform(28.0, 35.0) / MPS_TO_KMH
            bodies[pid] = Body(pid, team, role, sx, sy, pace, rng.uniform(0.45, 0.85))
    return bodies


def _abs(team: str, period: int, x: float, y: float) -> tuple[float, float]:
    return (x, y) if attack_sign(team, period) > 0 else flip(x, y)


class Tracker:
    def __init__(self, bodies: dict[str, Body], rng: random.Random):
        self.bodies = bodies
        self._rng = rng

    def reset(self, period: int) -> None:
        for b in self.bodies.values():
            b.x, b.y = _abs(b.team, period, b.slot_x, b.slot_y)
            b.speed = 0.0

    def nearest(self, team: str, x: float, y: float, exclude: str | None = None) -> Body:
        return min(
            (b for b in self.bodies.values() if b.team == team and b.player_id != exclude),
            key=lambda b: math.hypot(b.x - x, b.y - y),
        )

    def step(self, period: int, poss_team: str, ball: tuple[float, float], carrier_id: str, press_level: float) -> None:
        """Advance every body by one second toward its role target."""
        defenders = "A" if poss_team == "H" else "H"
        n_chase = 1 + (press_level >= 0.5) + (press_level >= 0.75)
        ranked = sorted(
            (b for b in self.bodies.values() if b.team == defenders and b.role != "GK"),
            key=lambda b: math.hypot(b.x - ball[0], b.y - ball[1]),
        )
        chasers = {b.player_id for b in ranked[:n_chase]}
        supporter = self.nearest(poss_team, ball[0], ball[1], exclude=carrier_id).player_id
        for b in self.bodies.values():
            tx, ty, desired = self._target(b, period, poss_team, ball, carrier_id, chasers, supporter, press_level)
            self._move(b, tx, ty, desired)

    def _target(self, b, period, poss_team, ball, carrier_id, chasers, supporter, press_level):
        if b.player_id == carrier_id:
            return ball[0], ball[1], min(b.pace_mps * 0.7, 3.5)
        if b.player_id in chasers:
            return ball[0], ball[1], b.pace_mps * (0.75 + 0.25 * press_level)
        if b.player_id == supporter:
            return ball[0], ball[1], b.pace_mps * 0.55
        bx, by = ball if attack_sign(b.team, period) > 0 else flip(*ball)
        pull = 0.1 if b.role == "GK" else (0.55 if b.team == poss_team else 0.45)
        tx = clamp(b.slot_x + pull * (bx - HALF_X), 2.0, PITCH_L - 2.0)
        ty = clamp(b.slot_y + 0.35 * (by - PITCH_W / 2), 2.0, PITCH_W - 2.0)
        ax, ay = _abs(b.team, period, tx, ty)
        return ax + self._rng.gauss(0, JOG_NOISE_M), ay + self._rng.gauss(0, JOG_NOISE_M), b.pace_mps * JOG_FRACTION

    def _move(self, b: Body, tx: float, ty: float, desired: float) -> None:
        dx, dy = tx - b.x, ty - b.y
        d = math.hypot(dx, dy)
        speed = clamp(desired, b.speed - MAX_DECEL_MPS2, b.speed + MAX_ACCEL_MPS2)
        step = min(max(speed, 0.0), d)
        if d > 1e-9:
            b.x = clamp(b.x + dx / d * step, 0.0, PITCH_L)
            b.y = clamp(b.y + dy / d * step, 0.0, PITCH_W)
        b.speed = step
        b.dist += step
        b.hist.append(step)

    @staticmethod
    def speed_kmh(b: Body) -> float:
        return round(min(b.speed * MPS_TO_KMH, 45.0), 1)

    @staticmethod
    def distance_m(b: Body) -> float:
        return round(min(b.dist, 16000.0), 1)
