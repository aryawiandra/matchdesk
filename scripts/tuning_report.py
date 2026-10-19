"""Reproduce the METRICS.md section 9 self-report: `uv run python scripts/tuning_report.py [n_seeds]`.

Momentum agreement grid: for minute i (0-based) compare momentum_at(events, 60*(i+1)) with
truth.dominance_by_minute[i]. Pass-difficulty AUC uses all (incomplete, complete) pairs of a
deterministic sample (first 300 incomplete x first 200 complete passes per match)."""
import statistics as st
import sys
from collections import Counter

from matchdesk import metrics
from matchdesk.config import GeneratorConfig
from matchdesk.generator import generate_match


def auc(pos: list[float], neg: list[float]) -> float:
    pos, neg = pos[:300], neg[:200]
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))


def main(n: int) -> None:
    cols: dict[str, list[float]] = {k: [] for k in (
        "events", "pass_completion", "shots", "goals", "pressure_per_team_min", "match_top_speed_kmh",
        "mean_player_distance_m", "momentum_r", "difficulty_auc")}
    moments: Counter = Counter()
    for seed in range(n):
        m = generate_match(GeneratorConfig(seed=seed))
        ev = m.events
        passes = [e for e in ev if e.type == "pass"]
        pressures = [e for e in ev if e.type == "pressure"]
        shots = [e for e in ev if e.type == "shot"]
        cols["events"].append(len(ev))
        cols["pass_completion"].append(metrics.pass_accuracy(passes))
        cols["shots"].append(len(shots))
        cols["goals"].append(sum(s.outcome == "goal" for s in shots))
        cols["pressure_per_team_min"].append(len(pressures) / (ev[-1].match_s / 60) / 2)
        cols["match_top_speed_kmh"].append(max(t.top_speed_kmh for t in m.truth.player_totals.values()))
        cols["mean_player_distance_m"].append(st.mean(t.distance_m for t in m.truth.player_totals.values()))
        k = len(m.truth.dominance_by_minute) - 1
        mm = [metrics.momentum_at(ev, 60 * (i + 1)) for i in range(k)]
        cols["momentum_r"].append(metrics.pearson(mm, m.truth.dominance_by_minute[:k]))
        d = [(metrics.pass_difficulty(p, pressures), p.outcome == "incomplete") for p in passes]
        cols["difficulty_auc"].append(auc([x for x, y in d if y], [x for x, y in d if not y]))
        moments.update(km.kind for km in m.truth.key_moments)
    print(f"seeds 0..{n - 1}")
    for name, vals in cols.items():
        print(f"{name:26s} mean {st.mean(vals):9.3f}  min {min(vals):9.3f}  max {max(vals):9.3f}")
    print("key moments per match:", {k: round(v / n, 2) for k, v in sorted(moments.items())})


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
