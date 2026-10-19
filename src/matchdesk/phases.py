"""Latent match phases: the hidden state the generator acts on."""
import random

from .geometry import clamp
from .schema import Phase

MIN_PHASE_S = 240.0
MAX_PHASE_S = 660.0
BALANCED_SHARE = 0.35


def build_phases(rng: random.Random, total_s: float) -> list[Phase]:
    phases: list[Phase] = []
    t = 0.0
    while t < total_s:
        end = t + rng.uniform(MIN_PHASE_S, MAX_PHASE_S)
        if rng.random() < BALANCED_SHARE:
            d = rng.gauss(0.0, 0.12)
        else:
            d = rng.choice((-1.0, 1.0)) * rng.uniform(0.35, 0.85)
        d = clamp(d, -0.9, 0.9)
        phases.append(
            Phase(
                start_s=t,
                end_s=end,
                dominance=round(d, 3),
                tempo=round(rng.uniform(0.8, 1.3), 3),
                press_H=round(clamp(0.20 + 0.45 * max(d, 0.0) + rng.uniform(0.0, 0.35), 0.0, 1.0), 3),
                press_A=round(clamp(0.20 + 0.45 * max(-d, 0.0) + rng.uniform(0.0, 0.35), 0.0, 1.0), 3),
            )
        )
        t = end
    return phases


def trim_phases(phases: list[Phase], end_s: float) -> tuple[Phase, ...]:
    kept = [p for p in phases if p.start_s < end_s]
    last = kept[-1]
    kept[-1] = last.model_copy(update={"end_s": end_s})
    return tuple(kept)


def phase_at(phases: list[Phase], t: float) -> Phase:
    for p in phases:
        if t < p.end_s:
            return p
    return phases[-1]


def dominance_by_minute(phases: tuple[Phase, ...], end_s: float) -> tuple[float, ...]:
    minutes = int(end_s // 60) + 1
    out = []
    for m in range(minutes):
        samples = [phase_at(list(phases), m * 60 + s).dominance for s in range(0, 60, 5)]
        out.append(round(sum(samples) / len(samples), 4))
    return tuple(out)
