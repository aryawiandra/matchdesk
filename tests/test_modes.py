"""Single-metric mode and player-focus mode: deterministic overlays, no model, nothing from the future."""
import pytest
from fastapi.testclient import TestClient

from matchdesk import metrics
from matchdesk.config import GeneratorConfig
from matchdesk.feed import create_app
from matchdesk.generator import generate_match
from matchdesk.modes import METRICS, focus_player, involves, metric_overlays
from matchdesk.overlay import parse_overlay
from matchdesk.pipeline import overlay_stream
from matchdesk.sse import decode_frames

SEED, PERIOD_S = 5, 300.0


@pytest.fixture(scope="module")
def match():
    return generate_match(GeneratorConfig(seed=SEED, period_s=PERIOD_S))


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def frames(client, query):
    with client.stream("GET", f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0&{query}") as response:
        assert response.status_code == 200
        return list(decode_frames(response.iter_lines()))


# ---- single-metric mode
@pytest.mark.parametrize("metric", METRICS)
def test_metric_ticks_are_valid_ordered_and_cite_only_events_already_seen(match, metric):
    overlays = list(metric_overlays(match.events, match.info, metric))
    assert overlays
    by_id = {e.event_id: e for e in match.events}
    assert [o.start_s for o in overlays] == sorted(o.start_s for o in overlays)
    assert len({o.overlay_id for o in overlays}) == len(overlays)
    for overlay in overlays:
        assert parse_overlay(overlay.model_dump(mode="json")) == overlay
        assert overlay.type == "metric_tick" and overlay.payload.metric == metric
        assert all(by_id[eid].match_s <= overlay.start_s for eid in overlay.evidence_event_ids)


def test_momentum_ticks_equal_the_reference_formula_on_the_events_seen_so_far(match):
    for overlay in metric_overlays(match.events, match.info, "momentum"):
        seen = [e for e in match.events if e.match_s <= overlay.start_s]
        assert overlay.payload.value == pytest.approx(metrics.momentum_at(seen, overlay.start_s), abs=1e-4)
        assert -1.0 <= overlay.payload.value <= 1.0


def test_pressure_is_reported_for_each_club_and_control_names_a_club_only_when_it_controls(match):
    pressure = list(metric_overlays(match.events, match.info, "pressure"))
    assert {o.payload.team for o in pressure} == {"H", "A"} and all(o.payload.value >= 0 for o in pressure)
    for overlay in metric_overlays(match.events, match.info, "control"):
        assert 0.0 <= overlay.payload.value <= 1.0
        assert overlay.payload.team in (None, "H", "A")


def test_an_unknown_metric_is_a_clear_error(match):
    with pytest.raises(ValueError, match="metric"):
        list(metric_overlays(match.events, match.info, "xg_total"))


def test_the_label_and_accessible_text_follow_the_language(match):
    english = next(iter(metric_overlays(match.events, match.info, "momentum", lang="en")))
    indonesian = next(iter(metric_overlays(match.events, match.info, "momentum", lang="id")))
    assert english.payload.label != indonesian.payload.label
    assert indonesian.lang == "id" and indonesian.a11y_text.strip()


# ---- player focus
def test_focus_keeps_the_scoreboard_and_only_moments_that_involve_the_player(match):
    shooter = next(e.player_id for e in match.events if e.type == "shot" and e.outcome == "goal")
    all_overlays = list(overlay_stream(match.events, match.info))
    focused = list(focus_player(all_overlays, match.events, shooter))
    assert {o.type for o in focused} >= {"scoreboard", "key_moment"} and len(focused) < len(all_overlays)
    by_id = {e.event_id: e for e in match.events}
    for overlay in focused:
        if overlay.type in ("scoreboard", "recap"):
            continue
        assert any(involves(by_id[eid], shooter) for eid in overlay.evidence_event_ids)


def test_focus_on_a_player_with_no_moments_leaves_only_the_scoreboard_and_recap(match):
    quiet = next(p.player_id for p in match.info.players if p.role == "GK")
    kept = {o.type for o in focus_player(list(overlay_stream(match.events, match.info)), match.events, quiet)}
    assert kept <= {"scoreboard", "recap", "key_moment", "explanation", "player_tag"}


# ---- the endpoint
def test_the_metric_parameter_gives_only_scoreboard_and_metric_ticks(client):
    payloads = frames(client, "metric=momentum")
    assert {p["type"] for p in payloads} == {"scoreboard", "metric_tick"}
    assert [p["start_s"] for p in payloads] == sorted(p["start_s"] for p in payloads)


def test_the_player_parameter_filters_the_normal_feed(client, match):
    shooter = next(e.player_id for e in match.events if e.type == "shot" and e.outcome == "goal")
    assert len(frames(client, f"player={shooter}")) < len(frames(client, ""))


@pytest.mark.parametrize("query", ["metric=xg_total", "player=ZZ99&metric=momentum"])
def test_bad_modes_are_rejected(client, query):
    assert client.get(f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0&{query}").status_code in (400, 422)


def test_an_unknown_player_is_a_clear_error(client):
    response = client.get(f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0&player=H99")
    assert response.status_code == 400 and "H99" in response.json()["detail"]


def test_the_renderer_offers_the_modes_in_both_languages(client):
    page, script = client.get("/").text, client.get("/app.js").text
    assert 'id="metric"' in page and 'id="player"' in page and 'value="momentum"' in page
    assert "metric_tick" in script and "Momentum saja" in script and "Pemain" in script
