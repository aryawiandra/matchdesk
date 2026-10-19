"""Record a real model's accepted answers for some matches, to replay on servers that have no model:
`uv run python scripts/record_model.py --seeds 42 7 11 --model qwen2.5:3b --out data/recorded`.

Needs Ollama running with the model pulled. Each match is run through the Analyst and the Explainer with the model,
behind the Verifier gate; the answers the model wrote and the Verifier accepted are saved. Replaying them later still
goes through the Verifier again (src/matchdesk/recorded.py)."""
import argparse
import datetime
import json
import urllib.request
from pathlib import Path

from matchdesk.config import GeneratorConfig
from matchdesk.generator import generate_match
from matchdesk.narrator import Narrator, ollama_generate
from matchdesk.recorded import record_answers, save_recording

OLLAMA_URL = "http://localhost:11434"


def ollama_version(host: str) -> str:
    with urllib.request.urlopen(f"{host}/api/version", timeout=5) as response:  # a local server we started ourselves
        return f"ollama {json.load(response)['version']}"


def main(seeds: list[int], model: str, out: Path, host: str) -> None:
    server = ollama_version(host)
    out.mkdir(parents=True, exist_ok=True)
    for seed in seeds:
        config = GeneratorConfig(seed=seed)
        match = generate_match(config)
        narrator = Narrator(ollama_generate(model, host))
        recording = record_answers(match, narrator, period_s=config.period_s, model=model, server=server,
                                   recorded_at=datetime.date.today().isoformat())
        path = out / f"seed-{seed:04d}.json"
        save_recording(recording, path)
        print(f"seed {seed}: {len(recording.answers)} model-written answers, {narrator.calls} model calls, "
              f"{narrator.seconds:.0f} s -> {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument("--model", default="qwen2.5:3b")
    parser.add_argument("--out", type=Path, default=Path("data/recorded"))
    parser.add_argument("--host", default=OLLAMA_URL)
    args = parser.parse_args()
    main(args.seeds, args.model, args.out, args.host)
