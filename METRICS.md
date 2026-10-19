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
Honesty note: the generator draws completion from a different functional form that also uses player skill and tracking geometry. D is a transparent observable rating, so the AUC of D against incompletion is a consistency check, not independent validation.

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
Away-team momentum is `-M`. Agreement with truth: Pearson r between the per-minute mean of `M` and `dominance_by_minute`.

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

## 9. Tuning log (generator v1, 2026-10-19, 40 seeds, full-length matches) [M]
Self-report of the generator before any comparison with real data. The calibration plan decides which of these are acceptable.

| Quantity | Result (mean over 40 matches) | Note |
|---|---|---|
| Events per match | 2,387 | |
| Pass completion | 0.74 (range 0.69-0.79) | Tuned up from 0.54 by raising the completion intercept; real-world reference to be sourced [A] |
| Shots per match (both teams) | 26.0 | |
| Goals per match | 3.15 | Known high; reference to be sourced. Calibration target |
| Pressure events per team-minute | 1.9 | After making pressure rate depend on press level squared |
| Pressure intensity P inside planted spells | median 3.0 per min (p10 1.5) | |
| P in low-press phases (level < 0.4) | median 1.0, p90 2.5 | Basis for `P_HI = 2.5` |
| Max player speed in a match | 32.6 km/h | Below the 45 km/h schema cap |
| Mean player distance | 10.0 km | Tuned with `JOG_FRACTION` |
| Momentum vs truth dominance (per-minute Pearson r) | 0.73 (min 0.58) | Target for the Analyst: r >= 0.6 on average [A] |
| Pass difficulty AUC against incompletion | 0.64 (min 0.60) | A consistency check against the generator only, not independent validation |
| Planted key moments per match | goal 3.2, momentum_swing 4.2, pressing_spell 3.8, sprint_burst 1.9, big_chance 0.5 | `big_chance` is rare; the evaluation reports it separately |
