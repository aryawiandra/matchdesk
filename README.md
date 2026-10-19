# MatchDesk

Evidence-cited, overlay-ready football match intelligence built on a synthetic event stream, with accuracy measured against the simulator's hidden ground truth.

> Status: work in progress for the Microsoft + Premier League "Inside the Game" Developer Hackathon 2026. This README is a stub; the full version (architecture, results, prize mapping, "Not built") lands before submission.

**Synthetic data only.** Every club, player, event and statistic in this repository is generated. No real club, player, crest or broadcast footage is used.

## Layout (planned)

| Path | What |
|---|---|
| `SCHEMA.md` | Event, truth and overlay contracts, v1 |
| `METRICS.md` | Exact formulas for every metric |
| `src/matchdesk/` | Generator (with hidden truth), metrics, SSE feed, Analyst, Verifier and Explainer, Producer (overlays) |
| `web/` | Reference overlay renderer (plain HTML, CSS and JS; no libraries, no other origins) |
| `schema/overlay.schema.json` | JSON Schema for overlays, generated from the models |
| `tests/` | Unit and property tests (80%+ coverage gate) |
| `config/thresholds.toml` | Speed and distance indicator thresholds (assumptions, labelled) |

## Run (generator, available now)

```bash
uv sync
uv run pytest
uv run python -m matchdesk.cli --seed 42 --out data/generated
```

Each run writes `<match>.jsonl` (the live stream) and `<match>.truth.json` (the hidden ground truth, never sent to the agents). Three published sample matches with truth are in `data/published/`.

### Live feed and Analyst

```bash
uv run uvicorn --factory matchdesk.feed:create_app --port 8000
curl -N "http://localhost:8000/feed/seed/42?period_s=300&speed=60"   # SSE: match_info, then one event per frame
uv run python scripts/analyst_report.py 30 30                          # Analyst precision/recall vs the hidden truth
```

Open `http://localhost:8000/` for the reference renderer: a live scoreboard, a pitch with a marker at each key moment, and insight cards. Click a sentence to see the event ids behind it. Fan and analyst views differ; the feed itself is `GET /overlays/seed/{seed}?speed=120&persona=fan` (SSE, JSON overlays that validate against `schema/overlay.schema.json`).

### The pipeline is a Microsoft Agent Framework workflow

`src/matchdesk/workflow.py` builds the pipeline as a graph of custom `Executor`s with the framework's `WorkflowBuilder`:

```
Event -> Analyst -> Explainer <-> Verifier -> Producer -> overlays
              \_____________(nothing to explain)_____________/^
```

Edges are conditional. A detection goes to the Explainer; if the Verifier rejects the answer, the reasons travel back to the Explainer on a retry edge; when retries run out the Explainer hands over a deterministic template, which the Verifier checks like any other answer. A quiet event skips the Explainer and the Verifier entirely (on a full match they ran 22 times in 2,381 events). No model is required: the Explainer uses templates unless you pass a model, and the Verifier gate is the same either way. The workflow produces exactly the same overlays and explanations as the synchronous reference `pipeline.overlay_stream`, including when a model raises, answers badly, or recovers on retry (`tests/test_workflow.py`).

### An AI model behind the gate (optional)

The Explainer can ask a local model to reword each template sentence. The model never supplies facts: the facts, the cited events and the named players come from the template, and the Verifier checks the model's wording against them. A wrong number, an uncited or wrong name, a guessed gender, hype, a causal story or a wrong format is rejected with reasons, the model retries once, and then the template is used. Every key-moment card says which it was ("Template" or "Written by an AI model", both "checked by the Verifier").

```bash
brew install ollama && ollama serve &          # once, in its own terminal
ollama pull qwen2.5:3b                         # about 1.9 GB
MATCHDESK_MODEL=ollama:qwen2.5:3b uv run uvicorn --factory matchdesk.feed:create_app_from_env --port 8000
```

Then choose **Explainer: AI model** on the page, or call `/overlays/seed/42?explainer=model`. Measured on 210 detections with `qwen2.5:3b` on an M2 with 8 GB (`scripts/model_report.py`, METRICS.md section 12): with the strict prompt 94% of answers were accepted first try, 5% after a retry with the reasons, and 0.5% fell back to the template (0.91 s mean); with a careless prompt 68% of calls were rejected and 49% of explanations fell back to the template. The gate does real work on a real model. The live model path is capped at one open stream.

**A server with no model can still show the AI.** `scripts/record_model.py` records the sentences a real model wrote (and the Verifier accepted) for a match; the repo ships recordings for eight seeds in `data/recorded/` (made with `qwen2.5:3b` on Ollama 0.40.6, 2026-10-19). `explainer=recorded` replays them. A recording is only data: every replayed sentence goes through the Verifier again, now, so a tampered or stale recording falls back to the template (`tests/test_recorded.py`). The page says plainly that it is a recorded run. This is what the public link uses, because a judge cannot reach a model on a laptop. Run the server with `uvicorn --factory matchdesk.feed:create_app_from_env`; set `MATCHDESK_MODEL` as well to add the live model.

The feed never sends the truth. The Analyst (`src/matchdesk/analyst.py`) computes every number from the stream alone; detection rules and measured quality are in `METRICS.md` section 10.

Check from a clean clone: `scripts/fresh_clone_check.sh .`

## Licence

MIT, see `LICENSE`.
