"""Verifier block rate under fault injection: `uv run python scripts/verifier_report.py [n_seeds] [first_seed]`.

For every detection in each generated match, builds the template claims, checks they all pass (false rejects must be
0), then injects each fault (src/matchdesk/faults.py) into every claim and counts how many the Verifier blocks."""
import sys
from collections import Counter

from matchdesk.analyst import Analyst
from matchdesk.config import GeneratorConfig, Thresholds
from matchdesk.context import MatchContext
from matchdesk.explainer import template_claims
from matchdesk.faults import FAULTS, inject
from matchdesk.generator import generate_match
from matchdesk.verifier import verify_claims


def main(n: int, first_seed: int = 0) -> None:
    clean, false_rejects = 0, 0
    applied, blocked = Counter(), Counter()
    for seed in range(first_seed, first_seed + n):
        match = generate_match(GeneratorConfig(seed=seed))
        ctx, analyst, detections = MatchContext(match.info), Analyst(Thresholds()), []
        for event in match.events:
            ctx.add(event)
            detections.extend(analyst.ingest(event))
        for detection in detections:
            for claim in template_claims(detection, ctx):
                clean += 1
                false_rejects += not verify_claims([claim], ctx).ok
                for name in FAULTS:
                    broken = inject(name, claim, ctx)
                    if broken is not None:
                        applied[name] += 1
                        blocked[name] += not verify_claims([broken], ctx).ok
    print(f"seeds {first_seed}..{first_seed + n - 1}; clean claims {clean}, wrongly rejected {false_rejects}")
    print(f"{'fault':18s} {'injected':>9s} {'blocked':>8s} {'block rate':>11s}")
    for name in FAULTS:
        rate = f"{blocked[name] / applied[name]:.3f}" if applied[name] else "n/a"
        print(f"{name:18s} {applied[name]:9d} {blocked[name]:8d} {rate:>11s}")
    total_applied, total_blocked = sum(applied.values()), sum(blocked.values())
    print(f"{'all faults':18s} {total_applied:9d} {total_blocked:8d} {total_blocked / total_applied:11.3f}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30, int(sys.argv[2]) if len(sys.argv) > 2 else 30)
