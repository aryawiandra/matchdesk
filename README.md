# MatchDesk

Evidence-cited, overlay-ready football match intelligence built on a synthetic event stream, with accuracy measured against the simulator's hidden ground truth.

Built solo for the Microsoft + Premier League "Inside the Game" Developer Hackathon 2026. A seeded simulator produces a football match as an event stream with a hidden ground truth. An Analyst finds key moments from the stream alone, an Explainer writes about them, a Verifier gate recomputes every number and checks every name before anything is shown, and a Producer turns the result into timed JSON overlays that a plain HTML renderer draws. The point is not the model; it is that nothing reaches the screen unless it can be proved from cited events.

**Demo video (1:27):** [YouTube](https://youtu.be/cg7YA-KdHGk) · [MP4 in the repo](demo/matchdesk-demo.mp4)

**Try it:** https://matchdesk-7ae079.azurewebsites.net (choose "AI model, recorded run"). It replays sentences a real model wrote, re-checked by the Verifier on every request. The first load can take up to about 30 seconds (free hosting tier wakes from idle); after that it is fast.

**Synthetic data only.** Every club, player, event and statistic in this repository is generated. No real club, player, crest or broadcast footage is used.

## Layout

| Path | What |
|---|---|
| `SCHEMA.md` | Event, truth and overlay contracts, v1 |
| `METRICS.md` | Exact formulas for every metric |
| `src/matchdesk/` | Generator (with hidden truth), metrics, SSE feed, Analyst, Verifier and Explainer, Producer (overlays), viewer modes, Agent Framework workflow |
| `web/` | Reference overlay renderer (plain HTML, CSS and JS; no libraries, no other origins) |
| `schema/overlay.schema.json` | JSON Schema for overlays, generated from the models |
| `tests/` | Unit and property tests (80%+ coverage gate) |
| `config/thresholds.toml` | Speed and distance indicator thresholds (assumptions, labelled) |

## Run

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

### Two languages

Every overlay, template, Verifier rule, model prompt and recording exists in English and in **Bahasa Indonesia** (`lang=id`). The Indonesian sentences pass through the same gate with Indonesian wording and meaning rules, comma decimals, and a check that the sentence is really in the requested language. The page picks the language from the browser and has a language selector. Spanish is in the overlay contract but refused until it has templates and rules.

### An AI model behind the gate (optional)

The Explainer can ask a local model to reword each template sentence. The model never supplies facts: the facts, the cited events and the named players come from the template, and the Verifier checks the model's wording against them. A wrong number, an uncited or wrong name, a guessed gender, hype, a causal story or a wrong format is rejected with reasons, the model retries once, and then the template is used. Every key-moment card says which it was ("Template" or "Written by an AI model", both "checked by the Verifier").

```bash
brew install ollama && ollama serve &          # once, in its own terminal
ollama pull phi4-mini                          # about 2.5 GB, MIT licence
MATCHDESK_MODEL=ollama:phi4-mini uv run uvicorn --factory matchdesk.feed:create_app_from_env --port 8000
```

Then choose **Explainer: AI model** on the page, or call `/overlays/seed/42?explainer=model`. Measured on 210 detections with `phi4-mini` on an M2 with 8 GB (`scripts/model_report.py`, METRICS.md section 12): with the strict prompt 210 of 210 English answers and 209 of 210 Indonesian answers were accepted first try (mean about 1 s); with a careless prompt, 37% of English calls and 9% of Indonesian calls were rejected, and 14% and 3% of explanations fell back to the template. "0 rejected" means no listed word, not perfect prose: the wording rules are lexical. The live model path is capped at one open stream.

**A server with no model can still show the AI.** `scripts/record_model.py` records the sentences a real model wrote (and the Verifier accepted) for a match; the repo ships recordings for eight seeds, in English and in Indonesian, in `data/recorded/` (made with `phi4-mini` on Ollama 0.40.6, 2026-10-20). `explainer=recorded` replays them. A recording is only data: every replayed sentence goes through the Verifier again, now, so a tampered or stale recording falls back to the template (`tests/test_recorded.py`). The page says plainly that it is a recorded run. This is what the public link uses, because a judge cannot reach a model on a laptop. Run the server with `uvicorn --factory matchdesk.feed:create_app_from_env`; set `MATCHDESK_MODEL` as well to add the live model.

The feed never sends the truth. The Analyst (`src/matchdesk/analyst.py`) computes every number from the stream alone; detection rules and measured quality are in `METRICS.md` section 10.

Check from a clean clone: `scripts/fresh_clone_check.sh .`

### Viewer modes

`GET /overlays/seed/{seed}` also takes `metric=momentum|control|pressure|tempo` (single-metric mode: the scoreboard plus one metric as `metric_tick` overlays, deterministic, formulas in METRICS.md sections 3-6) and `player=H09` (player focus: the scoreboard, the recap and only the moments whose cited events involve that player). Each metric tick cites the latest event already seen, never a later one (`tests/test_modes.py`). The page has a Mode selector and a Player box.

## Results

All numbers are measured, on synthetic data, with the commands shown; limits are in the METRICS.md section named.

| What | Result | Where |
|---|---|---|
| Analyst against the hidden truth (seeds 30-59) | goal 1.00 precision / 1.00 recall; big chance 0.93 / 1.00; momentum swing 0.39 / 0.72; pressing spell 0.56 / 0.59; sprint 0.31 / 0.94 | METRICS.md section 10 |
| Verifier under fault injection (3,426 corrupted claims, 760 clean claims) | 3,424 blocked (99.9%); 0 clean claims wrongly rejected. The 2 misses were an appended number that happened to equal a true one | METRICS.md section 11, `scripts/verifier_report.py 30 30` |
| Real model behind the gate (Phi-4-mini, 210 detections per run) | strict prompt: 210 of 210 English and 209 of 210 Indonesian first try; careless prompt: 37% of English and 9% of Indonesian calls rejected, 14% and 3% of explanations fell back to the template | METRICS.md section 12, `scripts/model_report.py` |
| Tests | 395 passing, 98% coverage, also in a clean clone (`scripts/fresh_clone_check.sh .`) | `tests/` |

Read these with their caveats. The Analyst's swing, spell and sprint detectors are statistical inferences and are far from perfect, and the claims written from them are worded to match (a sprint is "reached X km/h", never "sustained"). The Verifier's wording and meaning rules are lexical, so "0 rejected" means no listed word, not perfect prose.

## Microsoft stack

| Piece | Used for |
|---|---|
| Microsoft Agent Framework (`agent-framework-core`) | The pipeline is a `WorkflowBuilder` graph of custom `Executor`s with conditional edges and a retry loop (`workflow.py`) |
| Agent Framework `Agent` + `OllamaChatClient` | The optional live model call (`narrator.py`) |
| Phi-4-mini (MIT licence, Microsoft), run locally with Ollama | The model whose wording the Verifier checks |
| Azure App Service (free tier) | The public link; it replays recorded Phi-4-mini answers, since a laptop model is not reachable from the internet (`scripts/deploy_appservice.sh`) |

Not used: Azure AI Foundry. The hackathon rules do not require it, and a free deployment was not possible on the student subscription (region policy). Azure Container Apps was also tried and is blocked there (no image builds), which is why the deploy script targets App Service.

## Not built

- A live model on the public link (recorded answers only; the live path needs a model server).
- Spanish (in the overlay contract, refused until it has templates and rules).
- The `stat` overlay type, and `metric_tick` for pass difficulty, ball speed and shot speed (the other four metrics are built).
- Real football data: everything is synthetic by design.
- A semantic check of wording: the Verifier's wording and meaning rules are lexical.
- Reliable momentum-swing, pressing-spell and sprint detection (see Results).

See `THIRD_PARTY.md` for dependencies and the model licence.

## Licence

MIT, see `LICENSE`.
