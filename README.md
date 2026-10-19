# MatchDesk

Evidence-cited, overlay-ready football match intelligence built on a synthetic event stream, with accuracy measured against the simulator's hidden ground truth.

> Status: work in progress for the Microsoft + Premier League "Inside the Game" Developer Hackathon 2026. This README is a stub; the full version (architecture, results, prize mapping, "Not built") lands before submission.

**Synthetic data only.** Every club, player, event and statistic in this repository is generated. No real club, player, crest or broadcast footage is used.

## Layout (planned)

| Path | What |
|---|---|
| `SCHEMA.md` | Event, truth and overlay contracts, v1 |
| `METRICS.md` | Exact formulas for every metric |
| `src/matchdesk/` | Generator (with hidden truth), metrics, later the agent workflow |
| `tests/` | Unit and property tests (80%+ coverage gate) |
| `config/thresholds.toml` | Speed and distance indicator thresholds (assumptions, labelled) |

## Run (generator, available now)

```bash
uv sync
uv run pytest
uv run python -m matchdesk.cli --seed 42 --out data/generated
```

Each run writes `<match>.jsonl` (the live stream) and `<match>.truth.json` (the hidden ground truth, never sent to the agents). Three published sample matches with truth are in `data/published/`.

Check from a clean clone: `scripts/fresh_clone_check.sh .`

## Licence

MIT, see `LICENSE`.
