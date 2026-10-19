import math

from hypothesis import HealthCheck, given, settings, strategies as st

from matchdesk import metrics
from matchdesk.config import GeneratorConfig
from matchdesk.generator import generate_match
from matchdesk.validate import validate_stream

SLOW = settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@SLOW
@given(seed=st.integers(min_value=0, max_value=10**6))
def test_any_seed_yields_a_valid_stream(seed):
    m = generate_match(GeneratorConfig(seed=seed, period_s=300.0))
    assert validate_stream(m.info, m.events, m.truth) == []


@SLOW
@given(seed=st.integers(min_value=0, max_value=10**6))
def test_ball_speed_times_duration_equals_distance(seed):
    m = generate_match(GeneratorConfig(seed=seed, period_s=300.0))
    for e in m.events:
        if e.type == "pass":
            d = math.hypot(e.end_x - e.x, e.end_y - e.y)
            assert abs(e.ball_speed_kmh / 3.6 * e.duration_s - d) <= 0.5 / 3.6 * e.duration_s + 1e-6


@SLOW
@given(seed=st.integers(min_value=0, max_value=10**6))
def test_momentum_bounded_and_difficulty_in_unit_interval(seed):
    m = generate_match(GeneratorConfig(seed=seed, period_s=300.0))
    pressures = [e for e in m.events if e.type == "pressure"]
    for e in m.events:
        if e.type == "pass":
            assert 0.0 < metrics.pass_difficulty(e, pressures) < 1.0
    for t in range(0, 600, 60):
        assert -1.0 <= metrics.momentum_at(m.events, t) <= 1.0


@settings(max_examples=200, deadline=None)
@given(
    dist=st.floats(0, 120), fwd=st.floats(-60, 100), pressed=st.booleans(), box=st.booleans(),
)
def test_difficulty_monotone_in_each_input(dist, fwd, pressed, box):
    z = lambda d, f, p, b: metrics.difficulty_logit(d, f, p, b)
    base = z(dist, fwd, pressed, box)
    assert z(dist + 1, fwd, pressed, box) > base
    assert z(dist, fwd, True, box) >= base
    assert z(dist, fwd, pressed, True) >= base
    assert z(dist, fwd + 1, pressed, box) >= base


@SLOW
@given(seed=st.integers(min_value=0, max_value=10**6))
def test_tempo_and_chaos_in_range(seed):
    m = generate_match(GeneratorConfig(seed=seed, period_s=300.0))
    for t in range(60, 600, 60):
        cc = metrics.control_chaos(m.events, t)
        assert 0.0 <= cc.chi <= 1.0
        assert metrics.tempo(m.events, t) >= 0.0
