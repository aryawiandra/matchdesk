import pytest
from pydantic import ValidationError

from matchdesk.schema import Pass, Shot, parse_event

BASE = dict(event_id="M0001-00001", match_id="M0001", seq=1, period=1, match_s=12.3, team="H")


def pass_kwargs(**over):
    d = dict(
        BASE, type="pass", player_id="H04", recipient_id="H08", x=30.0, y=20.0,
        end_x=50.0, end_y=30.0, outcome="complete", duration_s=1.5,
        ball_speed_kmh=53.7, actor_speed_kmh=9.0, actor_distance_m=120.0,
    )
    d.update(over)
    return d


def test_parse_event_dispatches_on_type():
    ev = parse_event(pass_kwargs())
    assert isinstance(ev, Pass)


def test_events_are_frozen():
    ev = parse_event(pass_kwargs())
    with pytest.raises(ValidationError):
        ev.x = 1.0


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        parse_event(pass_kwargs(secret_truth=1))


def test_coordinates_outside_pitch_are_rejected():
    with pytest.raises(ValidationError):
        parse_event(pass_kwargs(x=106.0))
    with pytest.raises(ValidationError):
        parse_event(pass_kwargs(end_y=-0.1))


def test_ball_speed_must_match_distance_over_duration():
    # distance = hypot(20, 10) = 22.36 m over 1.5 s = 53.67 km/h
    parse_event(pass_kwargs(ball_speed_kmh=53.7))
    with pytest.raises(ValidationError):
        parse_event(pass_kwargs(ball_speed_kmh=70.0))


def test_unknown_type_is_rejected():
    with pytest.raises(ValidationError):
        parse_event(dict(BASE, type="dive"))


def test_shot_speed_range():
    ok = dict(
        BASE, type="shot", player_id="H09", x=90.0, y=34.0, end_x=105.0, end_y=30.0,
        outcome="goal", shot_speed_kmh=95.0, actor_speed_kmh=12.0, actor_distance_m=2000.0,
    )
    assert isinstance(parse_event(ok), Shot)
    with pytest.raises(ValidationError):
        parse_event(dict(ok, shot_speed_kmh=250.0))


def test_event_id_must_match_match_id_and_seq():
    with pytest.raises(ValidationError):
        parse_event(pass_kwargs(event_id="M0001-00099"))
