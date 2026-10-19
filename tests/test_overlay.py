"""Overlay contract v1: typed models, a committed JSON Schema that matches them, and strict validation."""
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from matchdesk.overlay import (
    ExplanationOverlay, ExplanationPayload, KeyMomentOverlay, KeyMomentPayload, PlayerTagOverlay,
    PlayerTagPayload, ScoreboardOverlay, ScoreboardPayload, Sentence, overlay_json_schema, parse_overlay,
)

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "overlay.schema.json"
COMMON = dict(match_id="M0001", start_s=100.0, end_s=108.0, priority=3, lang="en", persona="fan")


def scoreboard(**kw):
    base = dict(COMMON, overlay_id="OV-M0001-scoreboard-100", type="scoreboard", priority=1, evidence_event_ids=(),
                a11y_text="Ashcombe Town 1, Corvale FC 0, 01:40.",
                payload=ScoreboardPayload(score={"H": 1, "A": 0}, clock="01:40", home="Ashcombe Town", away="Corvale FC"))
    base.update(kw)
    return ScoreboardOverlay(**base)


def key_moment(**kw):
    base = dict(COMMON, overlay_id="OV-M0001-key_moment-00001-en-fan", type="key_moment", priority=5,
                evidence_event_ids=("M0001-00001",), a11y_text="Goal.",
                payload=KeyMomentPayload(kind="goal", headline="Goal.", team="H", x=95.0, y=34.0))
    base.update(kw)
    return KeyMomentOverlay(**base)


def explanation(**kw):
    base = dict(COMMON, overlay_id="OV-M0001-explanation-00001-en-fan", type="explanation",
                evidence_event_ids=("M0001-00001",), a11y_text="It was a goal.",
                payload=ExplanationPayload(sentences=(Sentence(text="It was a goal.", evidence_event_ids=("M0001-00001",)),)))
    base.update(kw)
    return ExplanationOverlay(**base)


def player_tag(**kw):
    base = dict(COMMON, overlay_id="OV-M0001-player_tag-00001-en-fan", type="player_tag", priority=2,
                evidence_event_ids=("M0001-00001",), a11y_text="Player tag: Wills Hartley, shirt 9.",
                payload=PlayerTagPayload(name="Wills Hartley", shirt=9, team="H", speed_chip_kmh=27.0, distance_chip_km=2.0))
    base.update(kw)
    return PlayerTagOverlay(**base)


ALL = [scoreboard, key_moment, explanation, player_tag]


@pytest.mark.parametrize("make", ALL)
def test_each_overlay_type_round_trips_through_json(make):
    overlay = make()
    assert parse_overlay(json.loads(overlay.model_dump_json())) == overlay


def test_the_end_must_come_after_the_start():
    with pytest.raises(ValidationError):
        key_moment(end_s=100.0)


@pytest.mark.parametrize("priority", [0, 6])
def test_priority_is_one_to_five(priority):
    with pytest.raises(ValidationError):
        key_moment(priority=priority)


def test_blank_accessibility_text_is_rejected():
    with pytest.raises(ValidationError):
        key_moment(a11y_text="  ")


@pytest.mark.parametrize("make", [key_moment, explanation, player_tag])
def test_overlays_that_make_claims_must_cite_evidence(make):
    with pytest.raises(ValidationError):
        make(evidence_event_ids=())


def test_every_sentence_must_cite_evidence():
    with pytest.raises(ValidationError):
        Sentence(text="Unsupported.", evidence_event_ids=())
    with pytest.raises(ValidationError):
        ExplanationPayload(sentences=())


def test_scoreboard_needs_no_evidence():
    assert scoreboard().evidence_event_ids == ()


def test_unknown_types_extra_fields_and_bad_enums_are_rejected():
    good = json.loads(key_moment().model_dump_json())
    for bad in (
        dict(good, type="hologram"),
        dict(good, surprise=1),
        dict(good, lang="fr"),
        dict(good, persona="coach"),
        dict(good, payload=dict(good["payload"], kind="own_goal")),
    ):
        with pytest.raises(ValidationError):
            parse_overlay(bad)


def test_pitch_coordinates_must_be_on_the_pitch():
    with pytest.raises(ValidationError):
        KeyMomentPayload(kind="goal", headline="Goal.", team="H", x=200.0, y=34.0)


# ---- the committed JSON Schema
def test_the_committed_schema_matches_the_models():
    committed = json.loads(SCHEMA_PATH.read_text())
    assert committed == overlay_json_schema()


@pytest.mark.parametrize("make", ALL)
def test_dumped_overlays_validate_against_the_committed_schema(make):
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(json.loads(make().model_dump_json()), schema)


def test_the_schema_rejects_what_the_models_reject():
    schema = json.loads(SCHEMA_PATH.read_text())
    good = json.loads(key_moment().model_dump_json())
    for bad in (dict(good, evidence_event_ids=[]), dict(good, priority=9), dict(good, type="hologram"),
                dict(good, surprise=1)):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)
