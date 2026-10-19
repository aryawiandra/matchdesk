"""Command line: `python -m matchdesk.cli --seed 42 --out data/generated`."""
import argparse
import sys
from pathlib import Path

from .config import GeneratorConfig
from .generator import generate_match
from .io import to_jsonl, truth_to_json
from .validate import validate_stream


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="matchdesk.cli", description="Generate a synthetic match and its hidden truth.")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--period-s", type=float, default=2700.0)
    parser.add_argument("--out", type=Path, default=Path("data/generated"))
    args = parser.parse_args(argv)
    if args.seed < 0 or args.period_s <= 0:
        print("error: --seed must be >= 0 and --period-s > 0", file=sys.stderr)
        return 2
    match = generate_match(GeneratorConfig(seed=args.seed, period_s=args.period_s))
    errors = validate_stream(match.info, match.events, match.truth)
    if errors:
        print("error: generated stream failed validation:\n" + "\n".join(errors[:10]), file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    mid = match.info.match_id
    (args.out / f"{mid}.jsonl").write_text(to_jsonl(match), encoding="utf-8")
    (args.out / f"{mid}.truth.json").write_text(truth_to_json(match.truth), encoding="utf-8")
    print(f"{mid}: {len(match.events)} events, score {match.truth.score['H']}-{match.truth.score['A']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
