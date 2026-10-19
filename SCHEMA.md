# MatchDesk schema v1

Status: v1, 2026-10-19. Machine-checked by `src/matchdesk/schema.py` (pydantic v2, `extra="forbid"`, frozen). If this file and the code disagree, the tests fail; fix both.

Tags: `[A]` assumption (design default, not a claim about real football), `[S]` external source, `[M]` measurement.

## 1. Conventions

| Item | Rule |
|---|---|
| Data | Synthetic only. Clubs, players and numbers are generated. No real names, crests or footage |
| Units | Metres, seconds, km/h. Pitch is 105 m x 68 m |
| Frame | **Team-relative attacking frame** for every coordinate: `x` runs 0..105 toward the opponent goal (opponent goal centre = (105, 34)), `y` 0..68. A team's own events are always in its own frame, so the second-half side swap never shows in the data. Tackle `x,y` is in the tackler's frame. Possession-change `x,y` is in the new possessor's frame |
| Time | `match_s`: seconds of played time since kickoff, continuous across the half-time break (the break is not counted). `period` 1 or 2. Resolution 0.1 s |
| IDs | `match_id` like `M0042`. `event_id` = `<match_id>-<seq:05d>`, `seq` strictly increasing from 0 and gap-free in the stream. `player_id` like `H07` (home shirt 7) or `A11`. `team` is `"H"` or `"A"` |
| Stream | One JSON object per line (JSONL), UTF-8. First line `match_info`, then events in `seq` order. The live service wraps each line as an SSE `data:` frame |
| Hidden truth | Never in the stream. Separate file `<match_id>.truth.json` (section 5) |
| Determinism | Same `seed` and `GeneratorConfig` give a byte-identical stream and truth. Only `random.Random(seed)` is used |

## 2. `match_info` (first line, not an event)

| Field | Type | Notes |
|---|---|---|
| `kind` | `"match_info"` | literal |
| `schema_version` | `"1.0"` | |
| `match_id` | str | |
| `pitch` | `{length_m: 105, width_m: 68}` | |
| `clubs` | `{H: Club, A: Club}` | `Club = {club_id, name}`; invented names only |
| `players` | list of `Player` | 22 entries. `Player = {player_id, name, shirt, role, team}`; `role` in `GK, DEF, MID, FWD`; `name` invented from syllables |
| `period_lengths_s` | not included | Would leak stoppage time; the stream shows `half_time` / `full_time` events when they happen |

## 3. Events

Common fields on every event: `event_id, match_id, seq, period, match_s, type, team`.

| `type` | Extra fields | Notes |
|---|---|---|
| `meta` | `kind` in `kickoff, half_time, second_half_start, full_time` | `team` is the team taking the kickoff, or `"H"` for the others (no meaning) |
| `pass` | `player_id, recipient_id, x, y, end_x, end_y, outcome, duration_s, ball_speed_kmh, actor_speed_kmh, actor_distance_m` | `outcome` in `complete, incomplete`. `duration_s` > 0 is flight time. `ball_speed_kmh` = 3.6 x distance / `duration_s` within 0.5 km/h |
| `shot` | `player_id, x, y, end_x, end_y, outcome, shot_speed_kmh, actor_speed_kmh, actor_distance_m` | `outcome` in `goal, saved, blocked, off_target`. `end_x` is 105 for goal, saved, off_target; for `blocked` it is the block point |
| `tackle` | `player_id, target_id, x, y, outcome, actor_speed_kmh, actor_distance_m` | `player_id` is the tackler (`team`); `target_id` the opponent. `outcome` in `won, lost` |
| `possession_change` | `x, y, cause` | `team` is the new possessor. `cause` in `tackle, interception, incomplete_pass, shot_end` |
| `pressure` | `player_id, target_id, x, y, duration_s, actor_speed_kmh, actor_distance_m` | `player_id` presses opponent `target_id` (the ball carrier). `x,y` is the presser's position in the presser's frame |

Tracking-derived load fields (`actor_speed_kmh`, `actor_distance_m`) describe the acting player (the presser for `pressure`) at the event time: speed over the last tracking second, cumulative distance since kickoff. They stand in for the tracking feed a real production would have. Ranges: speed 0..45, distance 0..16000.

Event order inside one moment: `pressure` (if any) precedes the `pass`/`shot`/`tackle` it relates to; `possession_change` follows the event that caused it with the same `match_s`.

## 4. Stream rules the tests enforce

1. Exactly one `match_info`, then events with `seq` 0..N-1.
2. `match_s` is non-decreasing; `period` never decreases; the stream begins with `meta/kickoff`, contains one `half_time` and one `second_half_start`, ends with `full_time`.
3. All coordinates are inside the pitch; `pass`/`tackle`/`shot`/`pressure` actors belong to the stated team.
4. A `possession_change` is never emitted for the team already in possession.
5. Goals: the count of `shot` with `outcome = goal` equals the truth score.

## 5. Hidden truth (`<match_id>.truth.json`)

| Field | Meaning |
|---|---|
| `schema_version, match_id, seed` | |
| `score` | `{H, A}` final |
| `phases` | list of `{start_s, end_s, dominance, tempo, press_H, press_A}`; `dominance` in [-1, 1], positive favours home. This is the latent state the generator acts on |
| `dominance_by_minute` | list of floats, mean latent dominance for each played minute; the reference for the momentum agreement metric |
| `key_moments` | list of `KeyMoment` (section 6) |
| `shot_xg` | map `event_id` -> true xG used to draw the outcome |
| `pass_success_prob` | map `event_id` -> true completion probability used to draw the outcome |
| `player_totals` | map `player_id` -> `{distance_m, top_speed_kmh}` from the 1 Hz tracking simulation |

## 6. Key-moment taxonomy (the evaluation target)

`KeyMoment = {kind, team, start_s, end_s, event_ids, player_id?, magnitude}`. Five kinds. The Analyst is scored on recovering these from the stream alone.

| `kind` | Planted when | `event_ids` | Visible evidence |
|---|---|---|---|
| `goal` | A shot has `outcome = goal` | the shot, plus up to the 3 previous events of that team | shot event |
| `big_chance` | A shot not scored with true xG >= 0.30 [A: `BIG_CHANCE_XG`] | the shot | shot position and context |
| `momentum_swing` | Consecutive phases differ in dominance by >= 0.70 [A] | the first 5 events after the phase boundary | shift in completed-pass and shot balance |
| `pressing_spell` | A phase of >= 180 s in which one team's `press` >= 0.65 [A] | pressure events of that team in the phase | rate of `pressure` events |
| `sprint_burst` | A player's tracked speed >= `sprint_speed_kmh` for >= 3 consecutive seconds and at least one event of that player falls inside, with `actor_speed_kmh` >= the same threshold | those events | `actor_speed_kmh` on events |

`magnitude` is a [0, 1] severity: xG for `big_chance`/`goal`, |delta dominance| for swings, press level for spells, speed/45 for sprints.

## 7. Overlay contract v1 (draft; implemented with the Producer)

An overlay is one JSON object, validated by `schema/overlay.schema.json` (created with the Producer):
`{overlay_id, match_id, type, start_s, end_s, priority, lang, persona, payload, evidence_event_ids, a11y_text}`.

| `type` | Payload | LLM involved? |
|---|---|---|
| `scoreboard` | score, clock | no |
| `stat` | named stat with value | no |
| `metric_tick` | `{metric, value, label}` for one metric in {pass_difficulty, momentum, control, pressure, ball_speed, shot_speed} | no (deterministic, feeds single-metric mode) |
| `key_moment` | kind, headline | headline: yes, via Verifier |
| `explanation` | sentence list, each with `evidence_event_ids` | yes, via Verifier |
| `player_tag` | name, shirt, optional `speed_chip`, `distance_chip` | no |
| `recap` | sections, each claim sentence with evidence ids | yes, via Verifier (claims only; connective words pass) |

## 8. Versioning

`schema_version` is `"1.0"`. Adding an optional field is a minor bump; changing meaning or units is a major bump and needs a migration note here. v1 has no migrations.
