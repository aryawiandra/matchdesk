"""Overlay feed endpoint and the static reference renderer."""
import json

import pytest
from fastapi.testclient import TestClient

from matchdesk.config import GeneratorConfig
from matchdesk.feed import create_app
from matchdesk.generator import generate_match
from matchdesk.overlay import parse_overlay
from matchdesk.pipeline import overlay_stream
from matchdesk.sse import decode_frames

SEED, PERIOD_S = 5, 300.0


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def frames(client, query=""):
    with client.stream("GET", f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0{query}") as response:
        assert response.status_code == 200
        return list(decode_frames(response.iter_lines()))


def test_the_overlay_feed_streams_valid_overlays_in_time_order(client):
    overlays = [parse_overlay(p) for p in frames(client)]
    assert overlays
    assert [o.start_s for o in overlays] == sorted(o.start_s for o in overlays)


def test_the_overlay_feed_equals_the_pipeline_output(client):
    match = generate_match(GeneratorConfig(seed=SEED, period_s=PERIOD_S))
    expected = [json.loads(o.model_dump_json()) for o in overlay_stream(match.events, match.info)]
    assert frames(client) == expected


def test_persona_changes_the_overlays(client):
    fan = frames(client, "&persona=fan")
    analyst = frames(client, "&persona=analyst")
    assert {p["persona"] for p in fan} == {"fan"} and {p["persona"] for p in analyst} == {"analyst"}


def test_the_overlay_feed_never_leaks_hidden_truth(client):
    with client.stream("GET", f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0") as response:
        blob = "\n".join(response.iter_lines())
    for secret in ("key_moments", "shot_xg", "pass_success_prob", "dominance", "player_totals"):
        assert secret not in blob


@pytest.mark.parametrize("query", ["persona=coach", "lang=fr", "period_s=10", "speed=-1"])
def test_bad_parameters_are_rejected(client, query):
    assert client.get(f"/overlays/seed/{SEED}?{query}").status_code == 422


@pytest.mark.parametrize("seed", ["-1", "10000"])
def test_bad_seeds_are_rejected(client, seed):
    assert client.get(f"/overlays/seed/{seed}?speed=0").status_code == 422


# ---- the reference renderer is served by the same app
def test_the_renderer_page_and_script_are_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "MatchDesk" in page.text and "app.js" in page.text
    script = client.get("/app.js")
    assert script.status_code == 200 and "/overlays/seed/" in script.text


def test_the_renderer_loads_nothing_from_other_origins(client):
    for path in ("/", "/app.js", "/style.css"):
        text = client.get(path).text
        assert "http://" not in text and "https://" not in text


def test_the_api_routes_still_win_over_the_static_mount(client):
    assert client.get("/healthz").json() == {"status": "ok"}
