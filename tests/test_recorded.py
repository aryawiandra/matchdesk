"""Recorded model answers: a real model's sentences replayed through the live Verifier gate. A recording is data from a
file, so it is never trusted: every replayed sentence is checked again, now."""
import json

import pytest
from pydantic import ValidationError

from matchdesk.feed import RECORDINGS_DIR
from matchdesk.narrator import Narrator
from matchdesk.pipeline import overlay_stream
from matchdesk.recorded import RecordedModel, Recording, load_directory, load_recording, record_answers, save_recording
from tests.test_narrator import reword

META = dict(period_s=2700.0, model="fake:1", server="test-server", recorded_at="2026-10-19")
KINDS = {"goal", "big_chance", "sprint_burst", "pressing_spell", "momentum_swing"}


def fake() -> Narrator:
    return Narrator(lambda system, prompt: reword(prompt))


def key_moment_sources(overlays):
    return {o.payload.source for o in overlays if o.type == "key_moment"}


# ---- recording
def test_recording_keeps_only_answers_a_model_wrote(full_match):
    recording = record_answers(full_match, fake(), **META)
    assert (recording.seed, recording.period_s, recording.model) == (11, 2700.0, "fake:1")
    assert recording.answers and all(sentences for sentences in recording.answers.values())

    def down(system, prompt):
        raise ConnectionError("model is down")

    assert record_answers(full_match, Narrator(down), **META).answers == {}


def test_each_answer_is_keyed_by_kind_and_cited_events(full_match):
    recording = record_answers(full_match, fake(), **META)
    assert all(key.split(":", 1)[0] in KINDS and key.split(":", 1)[1] for key in recording.answers)


# ---- files
def test_a_recording_survives_a_round_trip_through_a_file(tmp_path, full_match):
    recording = record_answers(full_match, fake(), **META)
    path = tmp_path / "seed-0011.json"
    save_recording(recording, path)
    assert json.loads(path.read_text())["model"] == "fake:1"
    assert load_recording(path) == recording


def test_a_malformed_recording_file_is_rejected(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json")
    with pytest.raises(ValueError):
        load_recording(bad_json)
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps({"surprise": 1}))
    with pytest.raises(ValidationError):
        load_recording(extra)


def test_a_directory_of_recordings_is_loaded_by_seed(tmp_path, full_match):
    save_recording(record_answers(full_match, fake(), **META), tmp_path / "a.json")
    save_recording(Recording(seed=99, answers={}, **META), tmp_path / "b.json")
    (tmp_path / "notes.txt").write_text("not a recording")
    assert set(load_directory(tmp_path)) == {11, 99}
    assert load_directory(tmp_path / "missing") == {}


# ---- the recordings that ship with the repo
SHIPPED = load_directory(RECORDINGS_DIR)


def test_the_repo_ships_recordings_of_a_real_model():
    assert 42 in SHIPPED and len(SHIPPED) >= 5
    assert all(r.model and r.server.startswith("ollama") and r.answers for r in SHIPPED.values())


@pytest.mark.parametrize("seed", sorted(SHIPPED))
def test_a_shipped_recording_still_passes_the_verifier_when_replayed(seed):
    from matchdesk.config import GeneratorConfig
    from matchdesk.generator import generate_match

    recording = SHIPPED[seed]
    match = generate_match(GeneratorConfig(seed=seed, period_s=recording.period_s))
    seen = []
    list(overlay_stream(match.events, match.info, model=RecordedModel(recording), on_explanation=seen.append))
    model_written = [e for e in seen if e.source in ("model", "model_retry")]
    assert seen and len(model_written) / len(seen) >= 0.9, "rules changed: re-record with scripts/record_model.py"


# ---- replay
def test_replaying_a_recording_gives_the_same_overlays_as_the_live_model(full_match):
    recording = record_answers(full_match, fake(), **META)
    live = list(overlay_stream(full_match.events, full_match.info, model=fake()))
    replayed = list(overlay_stream(full_match.events, full_match.info, model=RecordedModel(recording)))
    assert replayed == live
    assert key_moment_sources(replayed) == {"model"}


def test_a_detection_with_no_recorded_answer_falls_back_to_the_template(full_match):
    empty = Recording(seed=11, answers={}, **META)
    overlays = list(overlay_stream(full_match.events, full_match.info, model=RecordedModel(empty)))
    assert key_moment_sources(overlays) == {"template"}


def test_a_tampered_recording_is_blocked_by_the_verifier(full_match):
    recording = record_answers(full_match, fake(), **META)
    tampered = recording.model_copy(update={"answers": {
        key: tuple(sentence.replace("minute", "97th minute") for sentence in sentences)
        for key, sentences in recording.answers.items()}})
    seen = []
    list(overlay_stream(full_match.events, full_match.info, model=RecordedModel(tampered), on_explanation=seen.append))
    blocked = [e for e in seen if e.rejections]
    assert blocked and all(e.source == "template" for e in blocked)
    assert any("unsupported number 97" in r for e in blocked for f in e.rejections for r in f.reasons)


def test_a_recording_with_the_wrong_number_of_sentences_falls_back_to_the_template(full_match):
    recording = record_answers(full_match, fake(), **META)
    two_sentence = {key for key, sentences in recording.answers.items() if len(sentences) > 1}
    assert two_sentence  # goals have a headline and a detail sentence
    cut = recording.model_copy(update={"answers": {
        key: sentences[:1] if key in two_sentence else sentences for key, sentences in recording.answers.items()}})
    seen = []
    list(overlay_stream(full_match.events, full_match.info, model=RecordedModel(cut), on_explanation=seen.append))
    goals = [e for e in seen if e.detection.kind == "goal"]
    assert goals and all(e.source == "template" for e in goals)
    assert any(e.source == "model" for e in seen if e.detection.kind != "goal")
