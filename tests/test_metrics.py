import math

import pytest

from matchdesk import metrics
from matchdesk.config import Thresholds
from matchdesk.schema import parse_event

BASE = dict(match_id="M0001", period=1)


def mk(seq, t, team, type_, **kw):
    d = dict(BASE, event_id=f"M0001-{seq:05d}", seq=seq, match_s=t, team=team, type=type_)
    d.update(kw)
    return parse_event(d)


def mk_pass(seq, t, team="H", x=30, y=30, ex=40, ey=30, outcome="complete", dur=1.5, player="H05",
            speed=8.0, dist=100.0):
    d = math.hypot(ex - x, ey - y)
    return mk(seq, t, team, "pass", player_id=player, recipient_id="H06", x=x, y=y, end_x=ex, end_y=ey,
              outcome=outcome, duration_s=dur, ball_speed_kmh=round(3.6 * d / dur, 1),
              actor_speed_kmh=speed, actor_distance_m=dist)


def mk_pressure(seq, t, team="A", target="H05", x=60):
    return mk(seq, t, team, "pressure", player_id="A03", target_id=target, x=x, y=30,
              duration_s=2.0, actor_speed_kmh=15.0, actor_distance_m=500.0)


def test_pass_distance_and_forward():
    p = mk_pass(1, 10, x=30, y=30, ex=60, ey=70 - 40)
    assert metrics.pass_distance(p) == pytest.approx(30.0)
    assert metrics.pass_forward(p) == pytest.approx(30.0)


def test_pass_difficulty_worked_examples_from_metrics_md():
    easy = mk_pass(1, 10, x=30, y=30, ex=30, ey=40)  # 10 m sideways
    assert metrics.pass_difficulty(easy, []) == pytest.approx(0.154, abs=0.002)
    # documented hard case: 40 m long, 30 m forward, pressed, ending inside the box
    hard = mk_pass(3, 20, x=60, y=20, ex=90, ey=20 + math.sqrt(40**2 - 30**2))
    pres = [mk_pressure(4, 17.0, target="H05")]
    assert metrics.pass_difficulty(hard, pres) == pytest.approx(0.905, abs=0.003)


def test_pressure_window_is_four_seconds_inclusive_and_target_specific():
    p = mk_pass(1, 20, x=30, y=30, ex=30, ey=40)
    base = metrics.pass_difficulty(p, [])
    inside = metrics.pass_difficulty(p, [mk_pressure(2, 16.0)])
    outside = metrics.pass_difficulty(p, [mk_pressure(3, 15.9)])
    other = metrics.pass_difficulty(p, [mk_pressure(4, 18.0, target="H09")])
    assert inside > base
    assert outside == pytest.approx(base)
    assert other == pytest.approx(base)


def test_difficulty_band_labels():
    assert metrics.difficulty_band(0.10) == "easy"
    assert metrics.difficulty_band(0.25) == "medium"
    assert metrics.difficulty_band(0.55) == "hard"


def test_pass_accuracy_none_when_empty():
    assert metrics.pass_accuracy([]) is None
    ps = [mk_pass(1, 1), mk_pass(2, 2, outcome="incomplete")]
    assert metrics.pass_accuracy(ps) == 0.5


def test_pax_sign():
    easy = mk_pass(1, 10, x=30, y=30, ex=30, ey=40, outcome="incomplete")
    assert metrics.pass_over_performance([easy], []) < 0


def test_momentum_zero_below_minimum_mass_and_signs():
    assert metrics.momentum_at([], 100) == 0.0
    home = [mk_pass(i, 10 + i, team="H", x=60, ex=95) for i in range(1, 10)]
    assert metrics.momentum_at(home, 30) > 0.9
    away = [mk_pass(i, 10 + i, team="A", x=60, ex=95, player="A05") for i in range(1, 10)]
    assert metrics.momentum_at(away, 30) < -0.9


def test_momentum_is_causal_and_decays():
    ev = [mk_pass(i, 10 + i, team="H", x=60, ex=95) for i in range(1, 10)]
    future = ev + [mk_pass(20 + i, 500 + i, team="A", x=60, ex=95, player="A05") for i in range(10)]
    assert metrics.momentum_at(future, 30) == metrics.momentum_at(ev, 30)
    mix = ev + [mk_pass(40 + i, 400 + i, team="A", x=60, ex=95, player="A05") for i in range(10)]
    assert metrics.momentum_at(mix, 410) < 0


def test_control_chaos_labels():
    calm = [mk_pass(i, 100 + 3 * i) for i in range(1, 15)]
    cc = metrics.control_chaos(calm, 150)
    assert cc.label == "control" and cc.controlling_team == "H"
    chaos = []
    for i in range(10):
        chaos.append(mk_pass(2 * i + 1, 100 + 5 * i, outcome="incomplete"))
        chaos.append(mk(2 * i + 2, 100 + 5 * i, "A", "possession_change", x=40, y=30, cause="incomplete_pass"))
    cc2 = metrics.control_chaos(chaos, 150)
    assert cc2.label == "chaos" and cc2.controlling_team is None
    assert 0 <= cc2.chi <= 1


def test_control_chaos_empty_window_is_neutral():
    cc = metrics.control_chaos([], 100)
    assert cc.chi == pytest.approx(0.25)
    assert cc.label == "control"


def test_pressure_intensity_and_high_press_share():
    evs = [mk_pressure(i, 100 + 10 * i, x=60 if i % 2 else 30) for i in range(1, 7)]
    assert metrics.pressure_intensity(evs, "A", 170) == pytest.approx(3.0)
    assert metrics.pressure_intensity(evs, "H", 170) == 0.0
    assert metrics.high_press_share(evs, "A", 170) == pytest.approx(0.5)
    assert metrics.high_press_share(evs, "H", 170) is None


def test_tempo_and_rhythm():
    evs = [mk_pass(i, 10 * i) for i in range(1, 60)]
    assert metrics.tempo(evs, 590) == pytest.approx(6.0)
    assert metrics.rhythm(evs, 300) == "steady"  # too early: undefined shift -> steady
    burst = evs + [mk_pass(100 + i, 700 + i) for i in range(30)]
    assert metrics.rhythm(burst, 730) == "up"


def test_indicator_chips_speed_and_distance_rules():
    th = Thresholds(sprint_speed_kmh=25.0, distance_step_m=2000.0, speed_chip_cooldown_s=60.0)
    evs = [
        mk_pass(1, 10, speed=24.9, dist=1900.0),
        mk_pass(2, 20, speed=26.0, dist=1999.0),   # speed chip
        mk_pass(3, 40, speed=30.0, dist=2001.0),   # in cooldown, distance chip
        mk_pass(4, 90, speed=30.0, dist=2500.0),   # cooldown over -> speed chip, no new distance chip
    ]
    chips = metrics.indicator_chips(evs, th)
    got = [(c.event_id[-5:], c.kind) for c in chips]
    assert got == [("00002", "speed"), ("00003", "distance"), ("00004", "speed")]


def test_pearson():
    assert metrics.pearson([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert metrics.pearson([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert metrics.pearson([1, 1, 1], [1, 2, 3]) == 0.0
