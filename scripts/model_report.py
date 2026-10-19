"""How a real local model behaves behind the Verifier gate:
`uv run python scripts/model_report.py --seeds 2 --first-seed 30 --model qwen2.5:3b --show 4`.

Needs Ollama running with the model pulled. For every detection in each generated match the model is asked to reword
the template sentences; the Verifier gate decides. Prints the share accepted first try, after a retry, or replaced by the
template, why answers were rejected, latency per detection, and a few examples."""
import argparse
import statistics
from collections import Counter

from matchdesk.analyst import Analyst
from matchdesk.config import GeneratorConfig, load_thresholds
from matchdesk.context import MatchContext
from matchdesk.explainer import explain, template_claims
from matchdesk.generator import generate_match
from matchdesk.narrator import Narrator, ollama_generate

REASON_MARKERS = ("unsupported number", "gendered pronoun", "unsupported wording", "unsupported causal", "names player",
                  "names club", "numbered sentences", "fact ", "no evidence", "unknown", "cannot verify")


def reason_kind(reason: str) -> str:
    return next((m.strip() for m in REASON_MARKERS if m in reason), reason[:40])


LOOSE_SYSTEM = ("You are an excited football commentator. Rewrite each numbered sentence as one lively sentence. "
                "Reply with one line per sentence formatted as '1. <sentence>'.")


def loose(generate):
    """The same model with a careless prompt: no rules about numbers, names, pronouns or hype."""
    return lambda system, prompt: generate(LOOSE_SYSTEM, prompt)


def main(seeds: int, first_seed: int, model_name: str, show: int, careless: bool) -> None:
    thresholds = load_thresholds()
    sources, reasons = Counter(), Counter()
    calls = rejected_calls = explained = model_answers = reworded = 0
    latencies: list[float] = []
    examples: list[tuple[str, str, str]] = []
    for seed in range(first_seed, first_seed + seeds):
        match = generate_match(GeneratorConfig(seed=seed))
        ctx, analyst = MatchContext(match.info), Analyst(thresholds)
        generate = ollama_generate(model_name)
        narrator = Narrator(loose(generate) if careless else generate)
        for event in match.events:
            ctx.add(event)
            for detection in analyst.ingest(event):
                before = narrator.seconds
                result = explain(detection, ctx, model=narrator)
                latencies.append(narrator.seconds - before)
                explained += 1
                sources[result.source] += 1
                calls += result.attempts
                rejected_calls += len(result.rejections)
                for failure in result.rejections:
                    reasons.update(reason_kind(r) for r in failure.reasons)
                if result.source != "template":
                    model_answers += 1
                    changed = [c.text for c in result.claims] != [c.text for c in template_claims(detection, ctx)]
                    reworded += changed
                    if changed and len(examples) < show:
                        examples.append((detection.kind, result.claims[0].text, result.source))
    mode = "careless prompt (no rules)" if careless else "strict prompt"
    print(f"model {model_name}, {mode}; seeds {first_seed}..{first_seed + seeds - 1}; {explained} detections explained")
    for name in ("model", "model_retry", "template"):
        print(f"  {name:12s} {sources[name]:4d}  {sources[name] / explained:6.1%}")
    print(f"  model calls {calls}, rejected {rejected_calls} ({rejected_calls / calls:.1%} of calls)")
    print(f"  accepted model answers that differ from the template: {reworded}/{model_answers} "
          f"({reworded / max(1, model_answers):.1%}); the rest copied it")
    print("  rejection reasons:", dict(reasons.most_common(8)))
    ordered = sorted(latencies)
    print(f"  latency per detection: mean {statistics.mean(latencies):.2f} s, "
          f"p95 {ordered[int(0.95 * (len(ordered) - 1))]:.2f} s, max {ordered[-1]:.2f} s")
    for kind, text, source in examples:
        print(f"  [{source}] {kind}: {text}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--first-seed", type=int, default=30)
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--show", type=int, default=4)
    parser.add_argument("--careless", action="store_true", help="use a prompt without rules, to see the gate work")
    args = parser.parse_args()
    main(args.seeds, args.first_seed, args.model, args.show, args.careless)
