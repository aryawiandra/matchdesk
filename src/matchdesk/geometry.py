"""Pitch geometry shared by the generator and the metrics (METRICS.md notation)."""
import math

PITCH_L = 105.0
PITCH_W = 68.0
GOAL_Y = 34.0
GOAL_W = 7.32
HALF_X = 52.5
BOX_X = 88.5
BOX_Y0 = 13.84
BOX_Y1 = 54.16


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def in_box(x: float, y: float) -> bool:
    return x >= BOX_X and BOX_Y0 <= y <= BOX_Y1


def flip(x: float, y: float) -> tuple[float, float]:
    """Mirror a point between the two attacking frames (an involution)."""
    return PITCH_L - x, PITCH_W - y


def attack_sign(team: str, period: int) -> int:
    """+1 when the team attacks toward +x in absolute coordinates."""
    return 1 if (team == "H") == (period == 1) else -1


def to_frame(team: str, period: int, x: float, y: float) -> tuple[float, float]:
    return (x, y) if attack_sign(team, period) > 0 else flip(x, y)


def goal_angle(x: float, y: float) -> float:
    """Angle in radians subtended by the goal mouth from (x, y) in the attacking frame."""
    dx = PITCH_L - x
    dy = y - GOAL_Y
    return math.atan2(GOAL_W * dx, dx * dx + dy * dy - (GOAL_W / 2) ** 2)


def shot_xg(x: float, y: float) -> float:
    """True xG used by the generator [A: design function, calibrated per calibration-plan.md]."""
    dx = PITCH_L - x
    d = math.hypot(dx, y - GOAL_Y)
    return sigmoid(-2.3 - 0.15 * d + 3.5 * goal_angle(x, y))
