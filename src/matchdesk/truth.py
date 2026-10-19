"""Derive the planted key moments (SCHEMA.md section 6) from a finished simulation."""
from .config import Thresholds
from .schema import Event, KeyMoment, Phase

MPS_TO_KMH = 3.6
SWING_EVIDENCE_EVENTS = 5
GOAL_LEAD_EVENTS = 3
MAX_SPEED_KMH = 45.0
EVIDENCE_TYPES = ("pass", "shot", "tackle", "pressure", "possession_change")


def derive_key_moments(events, phases, bodies, tick_of, shot_xg, th: Thresholds) -> tuple[KeyMoment, ...]:
    moments: list[KeyMoment] = []
    moments += _shot_moments(events, shot_xg, th)
    moments += _swing_moments(events, phases, th)
    moments += _pressing_moments(events, phases, th)
    moments += _sprint_moments(events, bodies, tick_of, th)
    return tuple(sorted(moments, key=lambda m: (m.start_s, m.kind)))


def _shot_moments(events: tuple[Event, ...], shot_xg: dict[str, float], th: Thresholds) -> list[KeyMoment]:
    out = []
    for i, e in enumerate(events):
        if e.type != "shot":
            continue
        xg = shot_xg[e.event_id]
        if e.outcome == "goal":
            lead = [p for p in events[:i] if p.team == e.team and p.type in ("pass", "tackle")][-GOAL_LEAD_EVENTS:]
            ids = tuple(p.event_id for p in lead) + (e.event_id,)
            start = lead[0].match_s if lead else e.match_s
            out.append(KeyMoment(kind="goal", team=e.team, start_s=start, end_s=e.match_s, event_ids=ids,
                                 player_id=e.player_id, magnitude=min(1.0, xg)))
        elif xg >= th.big_chance_xg:
            out.append(KeyMoment(kind="big_chance", team=e.team, start_s=e.match_s, end_s=e.match_s,
                                 event_ids=(e.event_id,), player_id=e.player_id, magnitude=min(1.0, xg)))
    return out


def _swing_moments(events, phases: tuple[Phase, ...], th: Thresholds) -> list[KeyMoment]:
    out = []
    for prev, cur in zip(phases, phases[1:]):
        delta = abs(cur.dominance - prev.dominance)
        if delta < th.swing_delta_dominance:
            continue
        after = [e for e in events if e.match_s >= cur.start_s and e.type in EVIDENCE_TYPES][:SWING_EVIDENCE_EVENTS]
        if not after:
            continue
        out.append(KeyMoment(kind="momentum_swing", team="H" if cur.dominance > 0 else "A", start_s=cur.start_s,
                             end_s=after[-1].match_s, event_ids=tuple(e.event_id for e in after), magnitude=min(1.0, delta)))
    return out


def _pressing_moments(events, phases: tuple[Phase, ...], th: Thresholds) -> list[KeyMoment]:
    out = []
    for p in phases:
        if p.end_s - p.start_s < th.pressing_spell_min_s:
            continue
        for team, level in (("H", p.press_H), ("A", p.press_A)):
            if level < th.pressing_spell_level:
                continue
            ids = tuple(e.event_id for e in events if e.type == "pressure" and e.team == team and p.start_s <= e.match_s < p.end_s)
            if ids:
                out.append(KeyMoment(kind="pressing_spell", team=team, start_s=p.start_s, end_s=p.end_s,
                                     event_ids=ids, magnitude=min(1.0, level)))
    return out


def _sprint_moments(events, bodies, tick_of, th: Thresholds) -> list[KeyMoment]:
    out = []
    thr_mps = th.sprint_speed_kmh / MPS_TO_KMH
    by_player: dict[str, list[Event]] = {}
    for e in events:
        pid = getattr(e, "player_id", None)
        if pid is not None:
            by_player.setdefault(pid, []).append(e)
    for pid, body in bodies.items():
        for lo, hi in _runs(body.hist, thr_mps, th.sprint_min_seconds):
            seen = [e for e in by_player.get(pid, []) if lo <= tick_of[e.event_id] <= hi and e.actor_speed_kmh >= th.sprint_speed_kmh]
            if seen:
                peak = max(body.hist[lo:hi + 1]) * MPS_TO_KMH
                out.append(KeyMoment(kind="sprint_burst", team=body.team, start_s=float(lo + 1), end_s=float(hi + 1),
                                     event_ids=tuple(e.event_id for e in seen), player_id=pid,
                                     magnitude=min(1.0, peak / MAX_SPEED_KMH)))
    return out


def _runs(hist: list[float], threshold: float, min_len: int) -> list[tuple[int, int]]:
    runs, start = [], None
    for i, v in enumerate(hist + [0.0]):
        if v >= threshold and start is None:
            start = i
        elif v < threshold and start is not None:
            if i - start >= min_len:
                runs.append((start, i - 1))
            start = None
    return runs
