"""SSE framing, the live feed endpoint, and ingest of a feed into the Analyst."""
import json

import pytest
from fastapi.testclient import TestClient

from matchdesk.analyst import Analyst
from matchdesk.config import GeneratorConfig, Thresholds
from matchdesk.feed import create_app, pace_delay
from matchdesk.generator import generate_match
from matchdesk.ingest import ingest_stream, parse_frames
from matchdesk.sse import decode_frames, encode_frame

PERIOD_S = 300.0
SEED = 5


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


@pytest.fixture(scope="module")
def feed_lines(client):
    with client.stream("GET", f"/feed/seed/{SEED}?period_s={PERIOD_S}&speed=0") as response:
        assert response.status_code == 200
        return list(response.iter_lines())


# ---- framing
def test_encode_frame_is_one_data_line_and_a_blank_line():
    frame = encode_frame({"a": 1, "b": "x"})
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    assert "\n" not in frame[:-2]
    assert json.loads(frame[len("data: "):]) == {"a": 1, "b": "x"}


def test_decode_round_trips_encoded_frames():
    payloads = [{"n": i} for i in range(3)]
    text = "".join(encode_frame(p) for p in payloads)
    assert list(decode_frames(text.splitlines())) == payloads


def test_decode_ignores_comments_and_other_fields_and_flushes_last_frame_without_blank_line():
    lines = [": keep-alive", "event: tick", 'data: {"n": 1}', "", ": again", 'data: {"n": 2}']
    assert list(decode_frames(lines)) == [{"n": 1}, {"n": 2}]


def test_decode_joins_multiline_data_per_the_sse_spec():
    lines = ['data: {"n":', "data: 7}", ""]
    assert list(decode_frames(lines)) == [{"n": 7}]


def test_decode_rejects_malformed_json_and_non_objects():
    with pytest.raises(ValueError, match="malformed"):
        list(decode_frames(["data: {nope", ""]))
    with pytest.raises(ValueError, match="object"):
        list(decode_frames(["data: [1, 2]", ""]))


# ---- parse and ingest
def test_parse_frames_requires_match_info_first():
    lines = encode_frame({"kind": "meta"}).splitlines()
    with pytest.raises(ValueError, match="match_info"):
        parse_frames(lines)
    with pytest.raises(ValueError, match="empty"):
        parse_frames([])


def test_parse_frames_returns_info_and_validated_events(short_match):
    text = "".join(encode_frame(json.loads(s)) for s in
                    [short_match.info.model_dump_json()] + [e.model_dump_json() for e in short_match.events[:20]])
    info, events = parse_frames(text.splitlines())
    assert info.match_id == short_match.info.match_id
    assert [e.event_id for e in events] == [e.event_id for e in short_match.events[:20]]


def test_an_invalid_event_in_the_feed_is_rejected_not_skipped(short_match):
    bad = json.loads(short_match.events[0].model_dump_json())
    bad["x"] = 500.0
    bad["type"] = "pressure"
    text = encode_frame(json.loads(short_match.info.model_dump_json())) + encode_frame(bad)
    _, events = parse_frames(text.splitlines())
    with pytest.raises(ValueError):
        list(events)


# ---- feed endpoint
def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_feed_streams_match_info_then_every_event_in_order(feed_lines):
    expected = generate_match(GeneratorConfig(seed=SEED, period_s=PERIOD_S))
    payloads = list(decode_frames(feed_lines))
    assert payloads[0]["kind"] == "match_info"
    assert [p["event_id"] for p in payloads[1:]] == [e.event_id for e in expected.events]


def test_feed_never_leaks_hidden_truth(feed_lines):
    blob = "\n".join(feed_lines)
    for secret in ("key_moments", "shot_xg", "pass_success_prob", "dominance", "player_totals"):
        assert secret not in blob


def test_feed_has_event_stream_content_type(client):
    with client.stream("GET", f"/feed/seed/{SEED}?period_s=60&speed=0") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("query", [
    "period_s=10", "period_s=99999", "speed=-1", "speed=100000", "period_s=abc",
])
def test_feed_rejects_out_of_range_parameters(client, query):
    assert client.get(f"/feed/seed/{SEED}?{query}").status_code == 422


@pytest.mark.parametrize("seed", ["-1", "10000", "abc"])
def test_feed_rejects_out_of_range_seeds(client, seed):
    assert client.get(f"/feed/seed/{seed}?speed=0").status_code == 422


def test_pace_delay_scales_with_speed_and_is_zero_when_unpaced():
    assert pace_delay(10.0, 40.0, speed=0) == 0.0
    assert pace_delay(10.0, 40.0, speed=60) == pytest.approx(0.5)
    assert pace_delay(40.0, 40.0, speed=60) == 0.0
    assert pace_delay(50.0, 40.0, speed=60) == 0.0


# ---- end to end: feed -> ingest -> Analyst
def test_feed_through_ingest_reaches_the_true_final_score(feed_lines):
    expected = generate_match(GeneratorConfig(seed=SEED, period_s=PERIOD_S))
    analyst = Analyst(Thresholds())
    steps = list(ingest_stream(feed_lines, analyst))
    assert len(steps) == len(expected.events)
    assert analyst.snapshot().score == expected.truth.score
    goals = [d for s in steps for d in s.detections if d.kind == "goal"]
    assert len(goals) == sum(expected.truth.score.values())
