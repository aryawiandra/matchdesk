"""Analyst: a deterministic, causal stream processor. Code computes every number (idea.md section 3).

`Analyst.ingest(event)` returns the key moments that became detectable at that event, using only
events seen so far (no look-ahead, no hidden truth). Detection rules: METRICS.md section 10.
"""
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass

from . import metrics
from .config import Thresholds
from .schema import Event, KeyMomentKind, Team

P_HI = 2.5                         # pressing spell: a run survives while pressure stays at or above this (per minute) [A: METRICS.md section 5]
P_SPELL_ON = 3.0                   # a grid point counts toward a spell at or above this [A: swept on seeds 0-29, METRICS.md section 10]
GRID_S = 10.0                      # evaluation grid for spells and swings
SPELL_SPAN_S = 180.0
SPELL_POINTS = int(SPELL_SPAN_S / GRID_S) + 1
SPELL_FULL_SCALE_PER_MIN = 5.0     # pressure level that maps to magnitude 1.0 [A]
SWING_EVERY_GRID_POINTS = 3        # swing check every 30 s
SWING_LOOKBACK_S = 180.0           # compare M(t) with M(t - 180 s)
SWING_LOOKBACK_POINTS = int(SWING_LOOKBACK_S / GRID_S)   # in 10 s grid steps
SWING_DELTA = 0.4                  # [A: swept on seeds 0-29, METRICS.md section 10]
SWING_REFRACTORY_S = 240.0
SWING_EVIDENCE_EVENTS = 5
GOAL_CONTEXT_EVENTS = 3
SPEED_FULL_SCALE_KMH = 45.0
TEAMS: tuple[Team, ...] = ("H", "A")


@dataclass(frozen=True)
class Detection:
    kind: KeyMomentKind
    team: Team
    start_s: float
    end_s: float
    event_ids: tuple[str, ...]
    player_id: str | None
    magnitude: float
    detected_at_s: float


@dataclass(frozen=True)
class Snapshot:
    match_s: float
    score: dict[str, int]
    momentum: float
    control: metrics.ControlChaos
    pressure: dict[str, float]
    tempo: float
    rhythm: str


class Analyst:
    def __init__(self, thresholds: Thresholds):
        self._th = thresholds
        self._events: list[Event] = []
        self._match_id: str | None = None
        self._last_s = 0.0
        self._score = {"H": 0, "A": 0}
        self._recent_ids: dict[str, deque[str]] = {t: deque(maxlen=GOAL_CONTEXT_EVENTS) for t in TEAMS}
        self._last_sprint_s: dict[str, float] = {}
        self._press_times: dict[str, list[float]] = {t: [] for t in TEAMS}
        self._press_ids: dict[str, list[str]] = {t: [] for t in TEAMS}
        self._spell_run = {t: 0 for t in TEAMS}
        self._spell_fired = {t: False for t in TEAMS}
        self._grid_k = 1
        self._momentum_by_k: dict[int, float] = {}
        self._last_swing_s = float("-inf")

    # ---- public
    def ingest(self, event: Event) -> tuple[Detection, ...]:
        self._check_order(event)
        found = self._advance_grid(event.match_s)
        found.extend(self._on_event(event))
        self._events.append(event)
        self._last_s = event.match_s
        return tuple(found)

    def snapshot(self) -> Snapshot:
        t = self._last_s
        return Snapshot(
            match_s=t,
            score=dict(self._score),
            momentum=metrics.momentum_at(self._events, t),
            control=metrics.control_chaos(self._events, t),
            pressure={team: metrics.pressure_intensity(self._events, team, t) for team in TEAMS},
            tempo=metrics.tempo(self._events, t),
            rhythm=metrics.rhythm(self._events, t),
        )

    # ---- stream discipline
    def _check_order(self, event: Event) -> None:
        if self._match_id is None:
            self._match_id = event.match_id
        elif event.match_id != self._match_id:
            raise ValueError(f"match_id {event.match_id!r} differs from {self._match_id!r}")
        if event.seq != len(self._events):
            raise ValueError(f"seq {event.seq} is not the next expected seq {len(self._events)}")
        if event.match_s < self._last_s:
            raise ValueError(f"match_s {event.match_s} goes back before {self._last_s}")

    # ---- event-driven detections (goal, big chance, sprint burst)
    def _on_event(self, e: Event) -> list[Detection]:
        found: list[Detection] = []
        if e.type == "shot":
            found.extend(self._shot_detections(e))
        found.extend(self._sprint_detections(e))
        if e.type == "pressure":
            self._press_times[e.team].append(e.match_s)
            self._press_ids[e.team].append(e.event_id)
        if e.type != "meta":
            self._recent_ids[e.team].append(e.event_id)
        return found

    def _shot_detections(self, shot) -> list[Detection]:
        xg = metrics.xg_estimate(shot.x, shot.y)
        if shot.outcome == "goal":
            self._score[shot.team] += 1
            context = tuple(self._recent_ids[shot.team])
            return [Detection("goal", shot.team, shot.match_s, shot.match_s, context + (shot.event_id,),
                              shot.player_id, xg, shot.match_s)]
        if xg >= self._th.big_chance_xg:
            return [Detection("big_chance", shot.team, shot.match_s, shot.match_s, (shot.event_id,),
                              shot.player_id, xg, shot.match_s)]
        return []

    def _sprint_detections(self, e: Event) -> list[Detection]:
        pid = getattr(e, "player_id", None)
        speed = getattr(e, "actor_speed_kmh", None)
        if pid is None or speed is None or speed < self._th.sprint_speed_kmh:
            return []
        if e.match_s - self._last_sprint_s.get(pid, float("-inf")) < self._th.speed_chip_cooldown_s:
            return []
        self._last_sprint_s[pid] = e.match_s
        return [Detection("sprint_burst", e.team, e.match_s, e.match_s, (e.event_id,), pid,
                          min(1.0, speed / SPEED_FULL_SCALE_KMH), e.match_s)]

    # ---- grid-driven detections (pressing spell, momentum swing)
    def _advance_grid(self, until_s: float) -> list[Detection]:
        found: list[Detection] = []
        while GRID_S * self._grid_k < until_s:
            g = GRID_S * self._grid_k
            found.extend(self._spell_step(g))
            if self._grid_k % SWING_EVERY_GRID_POINTS == 0:
                found.extend(self._swing_step(g))
            self._grid_k += 1
        return found

    def _pressure_per_min(self, team: str, g: float) -> float:
        times = self._press_times[team]
        count = bisect_right(times, g) - bisect_right(times, g - metrics.PRESSURE_WINDOW_S)
        return count / (metrics.PRESSURE_WINDOW_S / 60.0)

    def _spell_step(self, g: float) -> list[Detection]:
        found: list[Detection] = []
        for team in TEAMS:
            level = self._pressure_per_min(team, g)
            if level < P_HI:
                self._spell_run[team], self._spell_fired[team] = 0, False
                continue
            if level >= P_SPELL_ON:
                self._spell_run[team] += 1
            if self._spell_run[team] >= SPELL_POINTS and not self._spell_fired[team]:
                self._spell_fired[team] = True
                found.append(self._spell_detection(team, g, level))
        return found

    def _spell_detection(self, team: str, g: float, level: float) -> Detection:
        start = g - SPELL_SPAN_S
        ids = tuple(eid for t, eid in zip(self._press_times[team], self._press_ids[team]) if start <= t <= g)
        return Detection("pressing_spell", team, start, g, ids, None,
                         min(1.0, level / SPELL_FULL_SCALE_PER_MIN), g)

    def _swing_step(self, g: float) -> list[Detection]:
        m_now = metrics.momentum_at(self._events, g)
        self._momentum_by_k[self._grid_k] = m_now
        before = self._momentum_by_k.get(self._grid_k - SWING_LOOKBACK_POINTS)
        if before is None or g - self._last_swing_s < SWING_REFRACTORY_S:
            return []
        delta = m_now - before
        if abs(delta) < SWING_DELTA:
            return []
        self._last_swing_s = g
        team = "H" if delta > 0 else "A"
        return [Detection("momentum_swing", team, g - SWING_LOOKBACK_S, g,
                          self._swing_evidence(team, g), None, min(1.0, abs(delta)), g)]

    def _swing_evidence(self, team: str, g: float) -> tuple[str, ...]:
        weighted = [e.event_id for e in self._events
                    if e.team == team and e.match_s <= g and metrics.event_weight(e) > 0]
        return tuple(weighted[-SWING_EVIDENCE_EVENTS:])
