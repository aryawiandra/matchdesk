"""Analyst detection quality against the hidden truth: `uv run python scripts/analyst_report.py [n_seeds] [first_seed]`.

Streams each generated match (seeds first..first+n-1, full length) through the Analyst, matches detections to the
planted key moments (src/matchdesk/matching.py) and prints per-kind precision and recall pooled over seeds."""
import sys
import time
from collections import Counter

from matchdesk.analyst import Analyst, Detection
from matchdesk.config import GeneratorConfig, load_thresholds
from matchdesk.generator import generate_match
from matchdesk.matching import KINDS, match_detections


def analyse(events, thresholds) -> list[Detection]:
    analyst = Analyst(thresholds)
    return [d for e in events for d in analyst.ingest(e)]


def main(n: int, first_seed: int = 0) -> None:
    thresholds = load_thresholds()
    tp, det, truth = Counter(), Counter(), Counter()
    seconds = 0.0
    for seed in range(first_seed, first_seed + n):
        match = generate_match(GeneratorConfig(seed=seed))
        start = time.perf_counter()
        found = analyse(match.events, thresholds)
        seconds += time.perf_counter() - start
        for kind, score in match_detections(found, match.truth.key_moments).items():
            tp[kind] += score.true_positives
            det[kind] += score.detections
            truth[kind] += score.truth
    print(f"seeds {first_seed}..{first_seed + n - 1}; Analyst time {seconds / n:.2f} s per match (stream replay, no pacing)")
    print(f"{'kind':16s} {'truth':>6s} {'detected':>9s} {'TP':>5s} {'precision':>10s} {'recall':>8s}")
    for kind in KINDS:
        precision = f"{tp[kind] / det[kind]:.2f}" if det[kind] else "n/a"
        recall = f"{tp[kind] / truth[kind]:.2f}" if truth[kind] else "n/a"
        print(f"{kind:16s} {truth[kind]:6d} {det[kind]:9d} {tp[kind]:5d} {precision:>10s} {recall:>8s}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40, int(sys.argv[2]) if len(sys.argv) > 2 else 0)
