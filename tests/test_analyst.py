"""Analyst core: causal key-moment detection and rolling state from the stream alone."""
import pytest

from matchdesk import metrics
from matchdesk.analyst import P_HI, Analyst
from matchdesk.config import Thresholds
from tests.test_metrics import mk, mk_pass, mk_pressure


def mk_shot(seq, t, team="H", x=95.0, y=34.0, outcome="saved", player="H09", speed=10.0):
    return mk(seq, t, team, "shot", player_id=player, x=x, y=y, end_x=105.0, end_y=34.0, outcome=outcome,
              shot_speed_kmh=90.0, actor_speed_kmh=speed, actor_distance_m=2000.0)


def run(events, th=None):
    analyst = Analyst(th or Thresholds())
    found = []
    for e in events:
        found.extend(analyst.ingest(e))
    return analyst, found


def kinds(found):
    return [d.kind for d in found]


# ---- xG estimate (observable stand-in for the hidden true xG)
def test_xg_estimate_is_higher_close_and_central_than_far_and_wide():
    assert metrics.xg_estimate(100.0, 34.0) > metrics.xg_estimate(70.0, 34.0)
    assert metrics.xg_estimate(100.0, 34.0) > metrics.xg_estimate(100.0, 4.0)
    assert 0.0 < metrics.xg_estimate(60.0, 60.0) < metrics.xg_estimate(104.0, 34.0) < 1.0


# ---- goal
def test_goal_detection_cites_the_shot_and_up_to_three_prior_events_of_that_team():
    events = [mk_pass(i, 10 + i, team="H") for i in range(5)] + [mk_pass(5, 16, team="A", player="A05")]
    events.append(mk_shot(6, 17, outcome="goal"))
    _, found = run(events)
    goal = next(d for d in found if d.kind == "goal")
    assert goal.team == "H"
    assert goal.event_ids[-1] == "M0001-00006"
    assert goal.event_ids[:-1] == ("M0001-00002", "M0001-00003", "M0001-00004")
    assert goal.player_id == "H09"
    assert goal.end_s == pytest.approx(17.0)


def test_goal_is_also_not_reported_as_a_big_chance():
    _, found = run([mk_shot(0, 5.0, outcome="goal")])
    assert kinds(found).count("goal") == 1
    assert "big_chance" not in kinds(found)


# ---- big chance
def test_close_central_miss_is_a_big_chance_and_a_long_wide_one_is_not():
    _, close = run([mk_shot(0, 5.0, x=100.0, y=34.0, outcome="saved")])
    _, far = run([mk_shot(0, 5.0, x=62.0, y=6.0, outcome="off_target")])
    assert kinds(close) == ["big_chance"]
    assert far == []


def test_big_chance_threshold_comes_from_thresholds():
    shot = mk_shot(0, 5.0, x=100.0, y=34.0, outcome="saved")
    _, strict = run([shot], Thresholds(big_chance_xg=0.99))
    assert strict == []


# ---- sprint burst
def test_sprint_burst_needs_threshold_speed_and_respects_cooldown():
    events = [
        mk_pass(0, 100.0, speed=20.0),
        mk_pass(1, 110.0, speed=27.0),
        mk_pass(2, 120.0, speed=28.0),   # inside the 60 s cooldown: silent
        mk_pass(3, 175.0, speed=29.0),   # 110 + 60 = 170 < 175: allowed
    ]
    _, found = run(events)
    bursts = [d for d in found if d.kind == "sprint_burst"]
    assert [b.event_ids for b in bursts] == [("M0001-00001",), ("M0001-00003",)]
    assert bursts[0].player_id == "H05"
    assert bursts[0].magnitude == pytest.approx(27.0 / 45.0)


def test_sprint_cooldown_is_per_player():
    events = [mk_pass(0, 100.0, speed=27.0, player="H05"), mk_pass(1, 101.0, speed=27.0, player="H06")]
    _, found = run(events)
    assert kinds(found) == ["sprint_burst", "sprint_burst"]


# ---- pressing spell
def pressing_events(rate_per_min, seconds, team="A", start_seq=0):
    gap = 60.0 / rate_per_min
    n = int(seconds / gap)
    return [mk_pressure(start_seq + i, round(gap * (i + 1), 1), team=team) for i in range(n)]


def test_sustained_high_pressure_emits_exactly_one_spell():
    events = pressing_events(4.0, 400)
    events.append(mk_pass(len(events), 405.0, team="H"))  # a later event flushes the grid
    _, found = run(events)
    spells = [d for d in found if d.kind == "pressing_spell"]
    assert len(spells) == 1
    spell = spells[0]
    assert spell.team == "A"
    assert spell.end_s - spell.start_s == pytest.approx(180.0)
    assert all(eid.startswith("M0001-") for eid in spell.event_ids)
    assert spell.magnitude > 0


def test_low_pressure_never_emits_a_spell():
    events = pressing_events(1.5, 600)
    events.append(mk_pass(len(events), 605.0, team="H"))
    _, found = run(events)
    assert "pressing_spell" not in kinds(found)
    assert P_HI == pytest.approx(2.5)


def test_spell_rearms_after_pressure_drops():
    first = pressing_events(4.0, 300)
    quiet = [mk_pass(len(first), 500.0, team="H")]
    second = [mk_pressure(len(first) + 1 + i, round(700 + 15.0 * (i + 1), 1), team="A") for i in range(30)]
    tail = [mk_pass(len(first) + 1 + 30, 1200.0, team="H")]
    _, found = run(first + quiet + second + tail)
    assert kinds(found).count("pressing_spell") == 2


# ---- momentum swing
def test_swing_fires_for_the_team_that_takes_over():
    home = [mk_pass(i, 10.0 * (i + 1), team="H", x=70, ex=95) for i in range(30)]          # 10..300 s
    away = [mk_pass(30 + i, 300.0 + 10.0 * (i + 1), team="A", player="A05", x=70, ex=95) for i in range(30)]
    tail = [mk_pass(60, 700.0, team="A", player="A05", x=70, ex=95)]
    _, found = run(home + away + tail)
    swings = [d for d in found if d.kind == "momentum_swing"]
    assert swings and swings[0].team == "A"
    assert swings[0].magnitude > 0
    assert len(swings[0].event_ids) <= 5
    assert swings[0].end_s - swings[0].start_s == pytest.approx(180.0)


def test_swing_compares_momentum_now_with_momentum_180_seconds_earlier():
    home = [mk_pass(i, 10.0 * (i + 1), team="H", x=70, ex=95) for i in range(30)]
    away = [mk_pass(30 + i, 300.0 + 10.0 * (i + 1), team="A", player="A05", x=70, ex=95) for i in range(30)]
    events = home + away + [mk_pass(60, 700.0, team="A", player="A05", x=70, ex=95)]
    _, found = run(events)
    swing = next(d for d in found if d.kind == "momentum_swing")
    delta = metrics.momentum_at(events, swing.end_s) - metrics.momentum_at(events, swing.end_s - 180.0)
    assert abs(delta) >= 0.4
    assert swing.magnitude == pytest.approx(min(1.0, abs(delta)))


def test_steady_play_has_no_swing():
    events = [mk_pass(i, 10.0 * (i + 1), team="H" if i % 2 == 0 else "A", player="H05" if i % 2 == 0 else "A05",
                      x=70, ex=95) for i in range(80)]
    _, found = run(events)
    assert "momentum_swing" not in kinds(found)


# ---- snapshot
def test_snapshot_tracks_score_clock_and_matches_the_reference_metrics():
    events = [
        mk_pass(0, 10.0, team="H", x=70, ex=95),
        mk_shot(1, 12.0, outcome="goal"),
        mk_shot(2, 30.0, team="A", x=95, outcome="goal", player="A09"),
        mk_shot(3, 50.0, outcome="goal"),
        mk_pressure(4, 55.0),
    ]
    analyst, _ = run(events)
    snap = analyst.snapshot()
    assert snap.score == {"H": 2, "A": 1}
    assert snap.match_s == pytest.approx(55.0)
    assert snap.momentum == pytest.approx(metrics.momentum_at(events, 55.0))
    assert snap.control == metrics.control_chaos(events, 55.0)
    assert snap.pressure["A"] == pytest.approx(metrics.pressure_intensity(events, "A", 55.0))
    assert snap.pressure["H"] == 0.0


def test_empty_analyst_snapshot_is_neutral():
    snap = Analyst(Thresholds()).snapshot()
    assert snap.score == {"H": 0, "A": 0} and snap.match_s == 0.0 and snap.momentum == 0.0


# ---- stream discipline
def test_out_of_order_or_gapped_events_are_rejected():
    analyst = Analyst(Thresholds())
    analyst.ingest(mk_pass(0, 1.0))
    with pytest.raises(ValueError, match="seq"):
        analyst.ingest(mk_pass(2, 2.0))
    with pytest.raises(ValueError, match="seq"):
        analyst.ingest(mk_pass(0, 3.0))


def test_time_going_backwards_is_rejected():
    analyst = Analyst(Thresholds())
    analyst.ingest(mk_pass(0, 5.0))
    with pytest.raises(ValueError, match="match_s"):
        analyst.ingest(mk_pass(1, 4.0))


def test_a_second_match_id_is_rejected():
    analyst = Analyst(Thresholds())
    analyst.ingest(mk_pass(0, 1.0))
    other = mk(1, 2.0, "H", "meta", kind="half_time").model_copy(update={"match_id": "M9999"})
    with pytest.raises(ValueError, match="match_id"):
        analyst.ingest(other)


# ---- against the real generator
def final_score(events):
    analyst, _ = run(events)
    return analyst.snapshot().score


def test_every_goal_in_a_generated_match_is_detected(short_match):
    _, found = run(short_match.events)
    shots = [e for e in short_match.events if e.type == "shot" and e.outcome == "goal"]
    goals = [d for d in found if d.kind == "goal"]
    assert [g.event_ids[-1] for g in goals] == [s.event_id for s in shots]
    assert final_score(short_match.events) == short_match.truth.score


def test_detection_is_deterministic(short_match):
    _, a = run(short_match.events)
    _, b = run(short_match.events)
    assert a == b


def test_detections_are_causal_and_time_ordered(short_match):
    analyst = Analyst(Thresholds())
    last = 0.0
    for e in short_match.events:
        for d in analyst.ingest(e):
            assert d.detected_at_s <= e.match_s + 1e-9
            assert d.detected_at_s >= last - 1e-9
            last = d.detected_at_s
