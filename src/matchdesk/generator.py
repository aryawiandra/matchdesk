"""Seeded football match generator with hidden truth (SCHEMA.md, METRICS.md).

All randomness comes from one random.Random(seed). Simulation state is mutable and private to one
run; the returned GeneratedMatch is immutable. Numeric constants marked [A] are design defaults,
not claims about real football (see calibration-plan.md)."""
import math
import random
from dataclasses import dataclass

from . import SCHEMA_VERSION
from .config import GeneratorConfig, Thresholds, load_thresholds
from .geometry import HALF_X, PITCH_L, PITCH_W, clamp, in_box, shot_xg, sigmoid, to_frame
from .names import make_players, pick_clubs
from .phases import build_phases, dominance_by_minute, phase_at, trim_phases
from .schema import (
    Event, MatchInfo, MatchTruth, Meta, Pass, PlayerTotals, PossessionChange, Pressure, Shot, Tackle,
)
from .tracking import MPS_TO_KMH, Body, Tracker, make_bodies
from .truth import derive_key_moments

STOPPAGE_RANGE_S = (30.0, 150.0)
PRESSURE_RADIUS_M = 4.5
TACKLE_RADIUS_M = 3.0
SHOT_BASE_X = 68.0
MAX_SHOT_PROB = 0.35
SHOT_SPEED_MEAN_KMH, SHOT_SPEED_SD_KMH = 90.0, 12.0  # [A: design default]
RESTART_AFTER_GOAL_S = (25.0, 40.0)


@dataclass(frozen=True)
class GeneratedMatch:
    info: MatchInfo
    events: tuple[Event, ...]
    truth: MatchTruth


def generate_match(config: GeneratorConfig, thresholds: Thresholds | None = None) -> GeneratedMatch:
    return _Run(config, thresholds or load_thresholds()).run()


class _Run:
    def __init__(self, config: GeneratorConfig, th: Thresholds):
        self.cfg, self.th = config, th
        self.rng = random.Random(config.seed)
        self.match_id = config.resolved_match_id
        home, away = pick_clubs(self.rng)
        self.info = MatchInfo(match_id=self.match_id, clubs={"H": home, "A": away}, players=make_players(self.rng))
        self.bodies = make_bodies(self.rng)
        self.tracker = Tracker(self.bodies, self.rng)
        upper = 2 * (config.period_s + STOPPAGE_RANGE_S[1]) + 60.0
        self.phases = build_phases(self.rng, upper)
        self.events: list[Event] = []
        self.tick_of: dict[str, int] = {}
        self.shot_xg: dict[str, float] = {}
        self.pass_prob: dict[str, float] = {}
        self.score = {"H": 0, "A": 0}
        self.t = 0.0
        self.tick = 0
        self.last_s = 0.0
        self.period = 1
        self.poss = "H"
        self.carrier: Body = self.bodies["H09"]
        self.ball = (HALF_X, PITCH_W / 2)

    # ---------- orchestration
    def run(self) -> GeneratedMatch:
        kick1 = self.rng.choice(("H", "A"))
        end1 = self._play_period(1, 0.0, kick1, "kickoff")
        self._emit(Meta, team="H", match_s=end1, kind="half_time")
        end2 = self._play_period(2, end1, "A" if kick1 == "H" else "H", "second_half_start")
        self._emit(Meta, team="H", match_s=end2, kind="full_time")
        phases = trim_phases(self.phases, end2)
        events = tuple(self.events)
        truth = MatchTruth(
            match_id=self.match_id, seed=self.cfg.seed, score=dict(self.score), phases=phases,
            dominance_by_minute=dominance_by_minute(phases, end2),
            key_moments=derive_key_moments(events, phases, self.bodies, self.tick_of, self.shot_xg, self.th),
            shot_xg=dict(self.shot_xg), pass_success_prob=dict(self.pass_prob),
            player_totals={
                pid: PlayerTotals(distance_m=round(b.dist, 1), top_speed_kmh=round(max(b.hist, default=0.0) * MPS_TO_KMH, 1))
                for pid, b in self.bodies.items()
            },
        )
        return GeneratedMatch(info=self.info, events=events, truth=truth)

    def _play_period(self, period: int, start_s: float, kickoff_team: str, meta_kind: str) -> float:
        self.period, self.t, self.last_s = period, start_s, max(self.last_s, start_s)
        end_s = start_s + self.cfg.period_s + self.rng.uniform(*STOPPAGE_RANGE_S)
        self.tracker.reset(period)
        self.poss, self.ball = kickoff_team, (HALF_X, PITCH_W / 2)
        self.carrier = self.tracker.nearest(kickoff_team, *self.ball)
        self._emit(Meta, team=kickoff_team, match_s=start_s, kind=meta_kind)
        while self.t < end_s:
            self._step()
        return round(max(end_s, self.last_s), 1)

    # ---------- helpers
    def _emit(self, cls, *, team: str, match_s: float | None = None, **fields) -> Event:
        seq = len(self.events)
        when = round(max(self.t if match_s is None else match_s, self.last_s), 1)
        event = cls(
            event_id=f"{self.match_id}-{seq:05d}", match_id=self.match_id, seq=seq,
            period=self.period, match_s=when, team=team, **fields,
        )
        self.last_s = when
        self.tick_of[event.event_id] = max(self.tick - 1, 0)
        self.events.append(event)
        return event

    def _advance_tracking(self) -> None:
        ph = phase_at(self.phases, self.t)
        level = ph.press_A if self.poss == "H" else ph.press_H
        while self.tick < int(self.t):
            self.tracker.step(self.period, self.poss, self.ball, self.carrier.player_id, level)
            self.tick += 1

    def _frame(self, team: str, x: float, y: float) -> tuple[float, float]:
        fx, fy = to_frame(team, self.period, x, y)
        return round(clamp(fx, 0.0, PITCH_L), 1), round(clamp(fy, 0.0, PITCH_W), 1)

    def _load(self, body: Body) -> dict:
        return {"actor_speed_kmh": Tracker.speed_kmh(body), "actor_distance_m": Tracker.distance_m(body)}

    @staticmethod
    def _other(team: str) -> str:
        return "A" if team == "H" else "H"

    # ---------- one decision
    def _step(self) -> None:
        self._advance_tracking()
        ph = phase_at(self.phases, self.t)
        team, rival = self.poss, self._other(self.poss)
        dom = ph.dominance if team == "H" else -ph.dominance
        rival_press = ph.press_A if team == "H" else ph.press_H
        carrier = self.carrier
        self.ball = (carrier.x, carrier.y)
        opp = self.tracker.nearest(rival, carrier.x, carrier.y)
        gap = math.hypot(opp.x - carrier.x, opp.y - carrier.y)
        pressed = self._maybe_pressure(opp, carrier, gap, rival_press)
        pressed = pressed or gap <= 2.0
        tempo = ph.tempo
        if gap <= TACKLE_RADIUS_M and self.rng.random() < 0.12 + 0.4 * rival_press:
            self._tackle(opp, carrier, dom)
            return
        fx, fy = self._frame(team, carrier.x, carrier.y)
        base = 0.0 if fx < SHOT_BASE_X else 0.02 + 0.25 * ((fx - SHOT_BASE_X) / (PITCH_L - SHOT_BASE_X)) ** 2
        if self.rng.random() < min(MAX_SHOT_PROB, base * (0.6 + 0.8 * (dom + 1) / 2)):
            self._shot(carrier, fx, fy)
        else:
            self._pass(carrier, fx, fy, dom, pressed, tempo)

    def _maybe_pressure(self, opp: Body, carrier: Body, gap: float, rival_press: float) -> bool:
        if gap > PRESSURE_RADIUS_M or self.rng.random() >= 0.05 + 0.85 * rival_press ** 2:
            return False
        px, py = self._frame(opp.team, opp.x, opp.y)
        self._emit(
            Pressure, team=opp.team, match_s=self.t - self.rng.uniform(0.5, 1.5), player_id=opp.player_id,
            target_id=carrier.player_id, x=px, y=py, duration_s=round(self.rng.uniform(1.0, 3.5), 1), **self._load(opp),
        )
        return True

    def _tackle(self, tackler: Body, carrier: Body, dom: float) -> None:
        p_won = clamp(0.5 - 0.25 * dom + 0.2 * (tackler.skill - carrier.skill), 0.1, 0.9)
        won = self.rng.random() < p_won
        tx, ty = self._frame(tackler.team, tackler.x, tackler.y)
        self._emit(
            Tackle, team=tackler.team, player_id=tackler.player_id, target_id=carrier.player_id, x=tx, y=ty,
            outcome="won" if won else "lost", **self._load(tackler),
        )
        if won:
            self._change_possession(tackler, "tackle")
        self.t += self.rng.uniform(0.8, 2.0)

    def _change_possession(self, new_carrier: Body, cause: str) -> None:
        px, py = self._frame(new_carrier.team, new_carrier.x, new_carrier.y)
        self._emit(PossessionChange, team=new_carrier.team, x=px, y=py, cause=cause)
        self.poss, self.carrier, self.ball = new_carrier.team, new_carrier, (new_carrier.x, new_carrier.y)

    # ---------- shot
    def _shot(self, shooter: Body, fx: float, fy: float) -> None:
        team, rival = shooter.team, self._other(shooter.team)
        fx = min(fx, 104.0)
        xg = shot_xg(fx, fy)
        roll = self.rng.random()
        if self.rng.random() < xg:
            outcome, end_x, end_y = "goal", 105.0, 34.0 + self.rng.uniform(-3.4, 3.4)
        elif roll < 0.45:
            outcome, end_x, end_y = "saved", 105.0, 34.0 + self.rng.uniform(-3.4, 3.4)
        elif roll < 0.70:
            outcome, end_x, end_y = "blocked", min(104.5, fx + self.rng.uniform(1.0, 4.0)), fy + self.rng.uniform(-2.0, 2.0)
        else:
            side = self.rng.choice((-1.0, 1.0))
            outcome, end_x, end_y = "off_target", 105.0, 34.0 + side * self.rng.uniform(4.0, 9.0)
        speed = clamp(self.rng.gauss(SHOT_SPEED_MEAN_KMH, SHOT_SPEED_SD_KMH), 50.0, 140.0)
        ev = self._emit(
            Shot, team=team, player_id=shooter.player_id, x=fx, y=fy, end_x=round(end_x, 1),
            end_y=round(clamp(end_y, 0.0, PITCH_W), 1), outcome=outcome, shot_speed_kmh=round(speed, 1), **self._load(shooter),
        )
        self.shot_xg[ev.event_id] = round(xg, 4)
        if outcome == "goal":
            self.score[team] += 1
            taker = self.tracker.nearest(rival, HALF_X, PITCH_W / 2)
            self.ball = (HALF_X, PITCH_W / 2)
            self._change_possession_at(taker, "shot_end", self.ball)
            self.t += self.rng.uniform(*RESTART_AFTER_GOAL_S)
            return
        if outcome == "blocked":
            ax, ay = to_frame(team, self.period, end_x, end_y)
            taker = self.tracker.nearest(rival, ax, ay)
        else:
            taker = self.bodies[f"{rival}01"]
        self._change_possession(taker, "shot_end")
        self.t += self.rng.uniform(3.0, 7.0)

    def _change_possession_at(self, new_carrier: Body, cause: str, ball: tuple[float, float]) -> None:
        px, py = self._frame(new_carrier.team, *ball)
        self._emit(PossessionChange, team=new_carrier.team, x=px, y=py, cause=cause)
        self.poss, self.carrier, self.ball = new_carrier.team, new_carrier, ball

    # ---------- pass
    def _pass(self, passer: Body, fx: float, fy: float, dom: float, pressed: bool, tempo: float) -> None:
        team, rival = passer.team, self._other(passer.team)
        recipient, (mx, my) = self._pick_recipient(passer, fx, fy, dom)
        end_x = round(clamp(mx + self.rng.gauss(0, 1.5), 0.0, PITCH_L), 1)
        end_y = round(clamp(my + self.rng.gauss(0, 1.5), 0.0, PITCH_W), 1)
        dist = math.hypot(end_x - fx, end_y - fy)
        fwd = end_x - fx
        logit = (
            3.3 - 0.045 * dist - 0.02 * max(fwd, 0.0) - 0.9 * pressed - 0.6 * in_box(end_x, end_y)
            + 1.6 * (passer.skill - 0.6) + 0.3 * dom
        )
        p_complete = sigmoid(logit)
        complete = self.rng.random() < p_complete
        speed_mps = clamp(7.0 + 0.30 * dist + self.rng.gauss(0, 1.5), 5.0, 32.0)
        duration = max(0.2, round(dist / speed_mps, 2))
        ev = self._emit(
            Pass, team=team, player_id=passer.player_id, recipient_id=recipient.player_id, x=fx, y=fy,
            end_x=end_x, end_y=end_y, outcome="complete" if complete else "incomplete", duration_s=duration,
            ball_speed_kmh=round(3.6 * dist / duration, 1), **self._load(passer),
        )
        self.pass_prob[ev.event_id] = round(p_complete, 4)
        abs_end = to_frame(team, self.period, end_x, end_y)
        elapsed = self.rng.uniform(1.5, 4.5) / tempo + duration
        if complete:
            self.carrier, self.ball = recipient, abs_end
        else:
            taker = self.tracker.nearest(rival, *abs_end)
            near = math.hypot(taker.x - abs_end[0], taker.y - abs_end[1]) <= 4.0
            self._change_possession(taker, "interception" if near else "incomplete_pass")
        self.t += elapsed

    def _pick_recipient(self, passer: Body, fx: float, fy: float, dom: float):
        aggression = 0.6 + 0.5 * dom
        options, weights = [], []
        for b in self.bodies.values():
            if b.team != passer.team or b.player_id == passer.player_id:
                continue
            mx, my = to_frame(b.team, self.period, b.x, b.y)
            d = math.hypot(mx - fx, my - fy)
            w = math.exp(-d / 22.0) * max(0.05, 1.0 + aggression * clamp((mx - fx) / 30.0, -1.0, 1.0))
            if d < 4.0:
                w *= 0.1
            rival = self.tracker.nearest(self._other(b.team), b.x, b.y)
            if math.hypot(rival.x - b.x, rival.y - b.y) <= 3.0:
                w *= 0.6
            options.append((b, (mx, my)))
            weights.append(w)
        pick = self.rng.choices(options, weights=weights, k=1)[0]
        return pick
