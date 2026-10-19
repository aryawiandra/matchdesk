"""Overlay feed endpoint and the static reference renderer."""
import asyncio
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


# ---- the model path and the stream limits
def fake_narrator():
    from matchdesk.narrator import Narrator
    from tests.test_narrator import reword

    return Narrator(lambda system, prompt: reword(prompt))


def model_frames(app_client, query=""):
    with app_client.stream("GET", f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0&explainer=model{query}") as response:
        assert response.status_code == 200
        return list(decode_frames(response.iter_lines()))


def recording_for(seed=SEED, period_s=PERIOD_S):
    from matchdesk.recorded import record_answers

    match = generate_match(GeneratorConfig(seed=seed, period_s=period_s))
    return record_answers(match, fake_narrator(), period_s=period_s, model="fake:1", server="test", recorded_at="2026-10-19")


def test_capabilities_say_whether_a_model_or_a_recording_is_available(client):
    assert client.get("/capabilities").json() == {"model": False, "recorded": None, "max_streams": 20}
    with_model = TestClient(create_app(model=fake_narrator(), max_streams=5))
    assert with_model.get("/capabilities").json() == {"model": True, "recorded": None, "max_streams": 5}
    with_recording = TestClient(create_app(recordings={SEED: recording_for()}))
    assert with_recording.get("/capabilities").json()["recorded"] == {
        "seeds": [SEED], "model": "fake:1", "recorded_at": "2026-10-19", "period_s": PERIOD_S}


def test_the_recorded_explainer_replays_a_models_sentences_through_the_verifier():
    recorded = TestClient(create_app(recordings={SEED: recording_for()}))
    with recorded.stream("GET", f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0&explainer=recorded") as response:
        assert response.status_code == 200
        moments = [p for p in decode_frames(response.iter_lines()) if p["type"] == "key_moment"]
    live = TestClient(create_app(model=fake_narrator()))
    assert moments and {p["payload"]["source"] for p in moments} == {"model"}
    assert [p["payload"]["headline"] for p in moments] == [
        p["payload"]["headline"] for p in model_frames(live) if p["type"] == "key_moment"]


@pytest.mark.parametrize("query, fragment", [
    (f"seed=6&period_s={PERIOD_S}", "no recording for seed 6"),
    (f"seed={SEED}&period_s=600.0", "recorded for"),
])
def test_the_recorded_explainer_says_clearly_when_it_has_nothing_for_the_request(query, fragment):
    recorded = TestClient(create_app(recordings={SEED: recording_for()}))
    seed = query.split("&")[0].split("=")[1]
    rest = query.split("&", 1)[1]
    response = recorded.get(f"/overlays/seed/{seed}?{rest}&speed=0&explainer=recorded")
    assert response.status_code == 400 and fragment in response.json()["detail"]


def test_asking_for_a_recording_when_there_are_none_is_a_clear_error(client):
    response = client.get(f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0&explainer=recorded")
    assert response.status_code == 400 and "no recordings" in response.json()["detail"]


def test_the_renderer_offers_the_model_choice_and_shows_where_words_came_from(client):
    assert "/capabilities" in client.get("/app.js").text
    assert 'id="explainer"' in client.get("/").text
    assert "source" in client.get("/app.js").text
    assert 'value="recorded"' in client.get("/").text


def test_the_default_explainer_is_the_template(client):
    moments = [p for p in frames(client) if p["type"] == "key_moment"]
    assert moments and {p["payload"]["source"] for p in moments} == {"template"}


def test_the_model_explainer_is_used_only_when_asked_and_says_so():
    app_client = TestClient(create_app(model=fake_narrator()))
    moments = [p for p in model_frames(app_client) if p["type"] == "key_moment"]
    assert moments and {p["payload"]["source"] for p in moments} == {"model"}


def test_asking_for_the_model_when_none_is_configured_is_a_clear_error(client):
    response = client.get(f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0&explainer=model")
    assert response.status_code == 400 and "no model" in response.json()["detail"]


def test_the_model_path_is_rate_limited_separately():
    limited = TestClient(create_app(model=fake_narrator(), max_model_streams=0))
    assert limited.get(f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0&explainer=model").status_code == 429
    assert limited.get(f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0").status_code == 200


def test_the_number_of_open_streams_is_capped_and_slots_are_released():
    capped = TestClient(create_app(max_streams=0))
    assert capped.get(f"/overlays/seed/{SEED}?period_s={PERIOD_S}&speed=0").status_code == 429
    assert capped.get(f"/feed/seed/{SEED}?period_s={PERIOD_S}&speed=0").status_code == 429
    one_at_a_time = TestClient(create_app(max_streams=1))
    for _ in range(3):  # each finished stream must free its slot
        with one_at_a_time.stream("GET", f"/overlays/seed/{SEED}?period_s=60&speed=0") as response:
            assert response.status_code == 200
            list(response.iter_lines())


def test_concurrent_requests_get_a_stream_or_a_429_never_an_empty_200():
    import httpx

    app = create_app(max_streams=5)

    async def hit(client, seed):
        async with client.stream("GET", f"/overlays/seed/{seed}?period_s=60&speed=600") as response:
            body = b"".join([chunk async for chunk in response.aiter_bytes()])
            return response.status_code, len(body)

    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await asyncio.gather(*(hit(client, seed) for seed in range(30)))

    results = asyncio.run(go())
    assert all(status == 429 or (status == 200 and size > 0) for status, size in results), results
    assert any(status == 429 for status, _ in results)
    assert any(status == 200 for status, _ in results)


def test_the_app_reads_its_model_from_the_environment(monkeypatch):
    from matchdesk.feed import create_app_from_env

    monkeypatch.delenv("MATCHDESK_MODEL", raising=False)
    assert TestClient(create_app_from_env()).get(
        f"/overlays/seed/{SEED}?period_s=60&speed=0&explainer=model").status_code == 400
    monkeypatch.setenv("MATCHDESK_MODEL", "ollama:qwen2.5:3b")
    configured = TestClient(create_app_from_env())
    assert configured.get("/healthz").status_code == 200  # builds the narrator without calling the model


def test_the_api_routes_still_win_over_the_static_mount(client):
    assert client.get("/healthz").json() == {"status": "ok"}
