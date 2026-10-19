"""Score Analyst detections against the hidden planted key moments: per kind, one-to-one, by time overlap."""
from collections.abc import Sequence
from dataclasses import dataclass

from .analyst import Detection
from .schema import KeyMoment

KINDS = ("goal", "big_chance", "momentum_swing", "pressing_spell", "sprint_burst")
# Seconds of slack when comparing time intervals [A]. Point events get a little; spans must really overlap.
TOLERANCE_S = {"goal": 2.0, "big_chance": 2.0, "sprint_burst": 5.0, "pressing_spell": 0.0, "momentum_swing": 0.0}


@dataclass(frozen=True)
class KindScore:
    kind: str
    true_positives: int
    detections: int
    truth: int

    @property
    def precision(self) -> float | None:
        return None if self.detections == 0 else self.true_positives / self.detections

    @property
    def recall(self) -> float | None:
        return None if self.truth == 0 else self.true_positives / self.truth


def _overlaps(det: Detection, moment: KeyMoment, tolerance_s: float) -> bool:
    return det.start_s - tolerance_s <= moment.end_s and moment.start_s <= det.end_s + tolerance_s


def _same_subject(det: Detection, moment: KeyMoment) -> bool:
    if det.kind != moment.kind or det.team != moment.team:
        return False
    return det.player_id is None or moment.player_id is None or det.player_id == moment.player_id


def match_detections(detections: Sequence[Detection], moments: Sequence[KeyMoment]) -> dict[str, KindScore]:
    used: set[int] = set()
    true_positives = {kind: 0 for kind in KINDS}
    for moment in sorted(moments, key=lambda m: m.start_s):
        tolerance = TOLERANCE_S[moment.kind]
        for index, det in enumerate(detections):
            if index not in used and _same_subject(det, moment) and _overlaps(det, moment, tolerance):
                used.add(index)
                true_positives[moment.kind] += 1
                break
    return {
        kind: KindScore(
            kind=kind,
            true_positives=true_positives[kind],
            detections=sum(d.kind == kind for d in detections),
            truth=sum(m.kind == kind for m in moments),
        )
        for kind in KINDS
    }
