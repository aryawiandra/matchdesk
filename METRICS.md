# MatchDesk metrics v1

Every metric is a pure function of the event stream (`SCHEMA.md`). Reference code: `src/matchdesk/metrics.py`; tests assert these formulas. Constants marked [A] are design defaults, not facts about real football; they are tuned against the generator first and then checked against published distributions (`calibration-plan.md`).

Notation: `t` = `match_s`. Coordinates are team-relative (SCHEMA §1). Goal centre (105, 34). Penalty area: `x >= 88.5` and `13.84 <= y <= 54.16`. Opponent half: `x >= 52.5`.

## 1. Pass basics
- `distance_m = hypot(end_x - x, end_y - y)`; `forward_m = end_x - x`.
- Accuracy of a set of passes = complete / total; undefined (null) when total = 0.
- `ball_speed_kmh = 3.6 * distance_m / duration_s`. The event field must equal this within 0.5 km/h.
- Shot speed is the event field `shot_speed_kmh` (no formula; range 0..200 enforced).

## 2. Pass difficulty D in (0, 1)
```
pressed  = 1 if some `pressure` event has target_id == passer and 0 <= t_pass - t_pressure <= 4.0 s, else 0
into_box = 1 if (end_x, end_y) lies in the penalty area, else 0
z = -2.2 + 0.050 * distance_m + 0.025 * max(forward_m, 0) + 1.00 * pressed + 0.70 * into_box
D = 1 / (1 + exp(-z))
```
Bands [A]: easy `D < 0.25`, medium `0.25 <= D < 0.55`, hard `D >= 0.55`.
Worked examples: 10 m sideways, unpressed: z = -1.70, D = 0.154 (easy). 40 m, 30 m forward, pressed, into the box: z = 2.25, D = 0.905 (hard).
Pass over-performance (PAX) for a set of passes = sum over passes of `(1[complete] - (1 - D))`. Positive means the player completed more than difficulty predicts.
Honesty note: the generator draws completion from a logit that uses the same four features as D (distance, forward metres, pressure, into-box) with different coefficients, plus player skill, match dominance and tracking geometry that the stream does not expose. D is a transparent observable rating, so the AUC of D against incompletion is a consistency check against the generator, not independent validation.

## 2b. Observable shot quality (xG estimate)
The true xG is hidden in the truth file. The Analyst scores a shot from its position alone (`metrics.xg_estimate`):
```
d = hypot(105 - x, y - 34);  theta = goal_angle(x, y)                  # angle the goal mouth subtends, radians
xg_est = sigmoid(-2.0 - 0.14 * d + 3.0 * theta)                        # [A: same features as the generator's xG, different coefficients]
```
A shot that is not a goal is a big chance when `xg_est >= big_chance_xg` (0.30, `config/thresholds.toml`). Honesty note: the generator's true xG uses the same two features, so the big-chance score in section 10 checks consistency, not independent validity.

## 3. Momentum M(t) in [-1, 1] (positive favours home)
```
w(e) for an event e of team T:
  complete pass : 0.2 + 0.8 * (end_x / 105)^2
  incomplete pass: 0
  shot          : 3.0
  tackle won    : 0.5
  pressure      : 0.4 if x >= 52.5 else 0
  others        : 0
S_T(t) = sum over events e of team T with t_e <= t of  w(e) * 0.5^((t - t_e) / 120)      # half-life 120 s [A]
M(t)   = (S_H - S_A) / (S_H + S_A)   if S_H + S_A >= 1.0 [A: minimum mass], else 0
```
Away-team momentum is `-M`. Agreement with truth: for minute `i` (0-based) compare `M(60*(i+1))` with `dominance_by_minute[i]`; report Pearson r per match and its mean.

## 4. Control vs chaos
Window `W = 60 s` ending at `t`.
```
n_pc    = number of possession_change events in (t-60, t]
passes  = number of pass events in (t-60, t];  complete = those with outcome complete
c_pass  = (complete + 1) / (passes + 2)                      # Laplace smoothing, neutral 0.5 when empty
chaos   chi = 0.5 * min(1, n_pc / 8) + 0.5 * (1 - c_pass)    # 8 turnovers/min saturates [A]
control = 1 - chi
label   = "control" if chi <= 0.30; "chaos" if chi >= 0.55; else "transition"       # [A]
controlling_team = team with more completed passes in the window if label == "control" and the counts differ, else null
```

## 5. Pressure
Window 120 s ending at `t`, per team T:
```
P_T(t)         = (number of pressure events by T in (t-120, t]) / 2          # per minute
high_press_T   = share of those events with x >= 52.5 (null if none)
delta_P_T(t)   = P_T(t) - P_T(t-120)
pressing_spell = P_T(tau) >= P_HI for every tau on a 10 s grid over a 180 s span          # P_HI = 2.5 per minute [A: tuned on the generator, section 9]
```
Tuning record: section 9.

## 6. Tempo and rhythm
```
tempo(t) = (number of pass, shot, tackle events in (t-180, t]) / 3                 # events per minute
base(t)  = mean of tempo(tau) on a 30 s grid over (t-600, t-180]
shift(t) = tempo(t) / base(t), defined for t >= 600 and base > 0
rhythm   = "up" if shift >= 1.35; "down" if shift <= 0.70; else "steady"            # [A]
```

## 7. Indicators (player tags)
Using `config/thresholds.toml`, evaluated on every event that carries `actor_speed_kmh` and `actor_distance_m`:
- Speed chip: `actor_speed_kmh >= sprint_speed_kmh` and at least `speed_chip_cooldown_s` since this player's last speed chip.
- Distance chip: `floor(actor_distance_m / distance_step_m)` is greater than the same value on this player's previous event (fires once per crossing).
A tag is emitted when (i) the event is a key moment's evidence, (ii) the player is the focus player, or (iii) a chip fires, whether or not the event is a key moment.

## 8. Player card
Touches (events with that `player_id`), pass accuracy, mean D, PAX, top `actor_speed_kmh`, latest `actor_distance_m`, shots and their best `shot_speed_kmh`.

## 9. Tuning log (generator v1, 2026-10-19) [M]
Reproduce with `uv run python scripts/tuning_report.py 40` (seeds 0-39, full-length matches; 12 s). Self-report before any comparison with real data; the calibration plan decides which numbers are acceptable.

| Quantity | Mean (min - max) over 40 matches | Note |
|---|---|---|
| Events per match | 2,389 (2,234 - 2,637) | |
| Pass completion | 0.744 (0.691 - 0.787) | Raised from 0.54 by changing the completion intercept; real-world reference to be sourced [A] |
| Shots per match, both teams | 26.0 (12 - 39) | |
| Goals per match | 3.15 (0 - 6) | Known high versus the sources the plan will use; calibration target |
| Pressure events per team-minute | 1.93 (1.54 - 2.46) | Pressure rate depends on press level squared |
| Pressure intensity P inside planted spells | median 3.0 per min (p10 1.5) | From the first survey run, same seeds |
| P in low-press phases (level < 0.4) | median 1.0, p90 2.5 | Basis for `P_HI = 2.5` |
| Fastest player speed in a match | mean 32.6 km/h (max over matches 33.9) | Schema cap is 45 km/h |
| Mean player distance | 10.03 km (9.66 - 10.32) | Set by `JOG_FRACTION` |
| Momentum vs truth dominance, per-match Pearson r | 0.73 (0.61 - 0.87) | Target for the Analyst: mean r >= 0.6 [A] |
| Pass difficulty AUC against incompletion | 0.64 (0.58 - 0.69) | Consistency check against the generator only |
| Planted key moments per match | goal 3.15, momentum_swing 4.17, pressing_spell 3.75, sprint_burst 1.88, big_chance 0.5 | `big_chance` is rare; the evaluation reports it separately |

## 10. Analyst detection rules (`src/matchdesk/analyst.py`)
The Analyst reads events one at a time and uses no look-ahead and no truth. Each rule fires when it becomes detectable; `detected_at_s` is never later than the current event time.

| Kind | Rule | Evidence |
|---|---|---|
| `goal` | A `shot` with `outcome = goal`. Also increments the score | the shot plus up to the 3 previous events of that team |
| `big_chance` | A non-goal shot with `xg_est >= big_chance_xg` (section 2b) | the shot |
| `sprint_burst` | An event whose `actor_speed_kmh >= sprint_speed_kmh`, at most once per player per `speed_chip_cooldown_s` | that event |
| `pressing_spell` | On a 10 s grid, per team, `P_T(t)` (section 5). Points with `P >= 3.0` [A] count; the run survives while `P >= 2.5` [A: P_HI] and resets below it. Fires once per run after 19 counted points (a 180 s span) | the team's pressure events in the span |
| `momentum_swing` | Every 30 s, `delta = M(t) - M(t - 180 s)` (section 3). Fires when `abs(delta) >= 0.4` [A], for the team that gained, at most once per 240 s [A] | the last 5 events of that team that carry momentum weight |

Parameters marked [A] were swept on seeds 0-29 (`P_SPELL_ON`, `P_HI` hysteresis, swing lookback, delta and refractory, sprint threshold and cooldown) and then scored on held-out seeds 30-59 with no further tuning.

### Measured quality against the planted truth [M: `uv run python scripts/analyst_report.py 30 30`, seeds 30-59, 2026-10-19]
Matching (`src/matchdesk/matching.py`) is one-to-one per kind, same team, time-interval overlap (tolerance: 2 s goals and big chances, 5 s sprints, none for spans); sprints also need the same player.

| Kind | Truth | Detected | Precision | Recall | Tuning seeds 0-29 (P / R) |
|---|---|---|---|---|---|
| goal | 90 | 90 | 1.00 | 1.00 | 1.00 / 1.00 |
| big_chance | 14 | 15 | 0.93 | 1.00 | 1.00 / 1.00 |
| momentum_swing | 137 | 249 | 0.39 | 0.72 | 0.31 / 0.69 |
| pressing_spell | 118 | 125 | 0.56 | 0.59 | 0.52 / 0.50 |
| sprint_burst | 63 | 191 | 0.31 | 0.94 | 0.32 / 1.00 |

How to read these: `goal` is trivial (the shot outcome is in the stream). `big_chance` is a consistency check (section 2b), and its truth sample is small (14-17). The other three are limited by what the stream shows. A swing or spell is a statistical inference from event rates. A sprint needs 3 s of sustained speed in the hidden tracking, while the stream shows one speed reading per event, so many single high readings are not sustained runs and precision stays near 0.3. Claims built on a `sprint_burst` therefore say "reached X km/h", which the event field proves, and never "sustained sprint". Analyst replay time: 0.06 s per full match [M: same run]. A detection with nothing to cite is never emitted (a momentum "swing" for a team with no events is dropped), so every claim can carry evidence ids.

## 11. Claims and the Verifier gate (`claims.py`, `verifier.py`, `explainer.py`)
A **claim** is a sentence, the event ids it cites, the numbers it states as **facts**, and the players it names. The Verifier is pure code (no model) and rejects a claim, with reasons, unless all of these hold:

| Check | Rule |
|---|---|
| Evidence | At least one cited event id, and every id was already seen in the stream (invented and future ids are unknown) |
| Facts | Each stated fact is recomputed from the events and must match within a tolerance: exact for minute and score; 0.5 for distance and speed (km/h, m); 0.05 for pressure per minute and momentum [A: display rounding] |
| Numbers in the text | Every number in the sentence must equal some stated fact within the precision it is written at (an integer allows 0.5, one decimal allows 0.05) |
| Players | Every named player id exists in `match_info` and appears in a cited event (actor, recipient or target) |
| Names in the text | A player named in the sentence (full name or a name part of 4+ letters) must be one the claim cites; a club named must be one the cited events involve |
| Wording | Deterministic lexical rules [A]. Rejected: gendered pronouns (he, she, him, his, her ...) because the data has no gender; hype stems (brilliant, stunning, incredible, dominant, dramatic, spectacular, decisive ... including adverb forms such as "dramatically"); causal phrases (because, due to, thanks to, as a result, caused by, led to) |

Facts the Verifier can recompute: `minute`, `minute_at`, `shot_distance_m`, `shot_speed_kmh`, `actor_speed_kmh`, `score_home`, `score_away`, `pressure_per_min`, `momentum`.

**Flow** (`explainer.explain`): a model proposes claims; if any claim is rejected the reasons go back once as feedback (`max_retries = 1`); if it still fails, or the model raises, the deterministic template is used. Templates state only numbers they also register as facts, and a template that fails the Verifier raises (it is a bug, never shipped). Answers are accepted or rejected as a whole, so a half-checked narrative is not published.

### Measured block rate under fault injection [M: `uv run python scripts/verifier_report.py 30 30`, seeds 30-59, 2026-10-19]
Faults (`faults.py`) corrupt each template claim in a known way. Clean claims wrongly rejected: 0 of 760.

| Fault | Injected | Blocked | Block rate |
|---|---|---|---|
| `no_evidence` (citation removed) | 760 | 760 | 1.000 |
| `fabricated_event` (id that does not exist) | 760 | 760 | 1.000 |
| `wrong_fact` (a stated number changed, text and fact consistent) | 760 | 760 | 1.000 |
| `unbacked_number` (extra number appended) | 760 | 758 | 0.997 |
| `wrong_player` (a player not in the cited events) | 386 | 386 | 1.000 |
| all | 3,426 | 3,424 | 0.999 |

Limits, stated plainly. These faults are mechanical corruptions we wrote, not real model hallucinations. The two misses are `unbacked_number` faults where the appended "97" coincided with a true number (for example a shot speed near 97 km/h). The Verifier judges wording only with the lexical rules in the table above (pronouns, hype stems, causal phrases); a sentence can still be vague, odd or subtly misleading without using a banned word. The Explainer prompt forbids the same things, and the overlay shows the evidence ids so a viewer can check each sentence.

## 12. A real model behind the gate (`narrator.py`, `scripts/model_report.py`)
Model: `qwen2.5:3b` (1.9 GB) on a local Ollama 0.40.6 server, Apple M2 with 8 GB, called through a Microsoft Agent Framework `Agent` with `OllamaChatClient` (temperature 0.3, seed 7). The model only rewrites the template sentences. Facts, cited events and named players are copied from the template claim, so the Verifier judges the model's wording against them. A wrong shape, an invented number, a wrong or uncited name, a guessed gender, or hype is rejected with reasons, the model gets one retry with those reasons, and then the template is used.

[M: `uv run python scripts/model_report.py --seeds 10 --first-seed 30` with and without `--careless`, 210 detections from 10 generated matches, 2026-10-19]

| Prompt | Accepted first try | Accepted after retry | Template fallback | Rejected calls | Latency per detection |
|---|---|---|---|---|---|
| Strict (rules and three examples) | 210 (100%) | 0 | 0 | 0 of 210 | mean 0.93 s, p95 1.52 s |
| Careless (one line, no rules) | 112 (53.3%) | 1 (0.5%) | 97 (46.2%) | 195 of 308 (63.3%) | mean 1.29 s, p95 3.00 s |

Careless-prompt rejection reasons: unsupported wording 149, wrong format 44, gendered pronoun 3, unsupported number 1. Every accepted answer differs from the template (an earlier prompt that told the model to keep everything made it copy the template in 37 of 37 cases [M: same script, 2 matches]).

How to read this. The careless run shows the gate catching real model mistakes, not only the mechanical faults of section 11: nearly half of careless answers were replaced by the template. The strict run has no rejections, so it gives no real-model evidence of retry recovery; the one recovery seen came from the careless prompt. Limits: one small model, one range of seeds, synthetic English data. The wording rules are lexical, not semantic. A model call takes about a second on this machine, so the model path is capped at one open stream (`DEFAULT_MAX_MODEL_STREAMS`), and the public default is the template path.
