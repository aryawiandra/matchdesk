"""Overlay contract v1 (SCHEMA.md section 7): timed, typed, schema-validated JSON for a renderer.

Implemented types: scoreboard, key_moment, explanation, player_tag. The committed JSON Schema
(`schema/overlay.schema.json`) is generated from these models and a test keeps the two identical."""
from typing import Annotated, Any, Literal, Union

from pydantic import Field, TypeAdapter, model_validator

from .explainer import Source
from .schema import Frozen, KeyMomentKind, Team, X, Y

Lang = Literal["en", "id", "es"]
Persona = Literal["fan", "analyst"]
EventIds = Annotated[tuple[str, ...], Field(min_length=1)]
NonBlank = Annotated[str, Field(min_length=1, pattern=r"\S")]


class ScoreboardPayload(Frozen):
    score: dict[str, int]
    clock: Annotated[str, Field(pattern=r"^\d{2,3}:\d{2}$")]
    home: NonBlank
    away: NonBlank


class KeyMomentPayload(Frozen):
    kind: KeyMomentKind
    headline: NonBlank
    team: Team
    x: X | None = None
    y: Y | None = None
    source: Source = "template"  # where the words came from; every source passed the Verifier


class Sentence(Frozen):
    text: NonBlank
    evidence_event_ids: EventIds


class ExplanationPayload(Frozen):
    sentences: Annotated[tuple[Sentence, ...], Field(min_length=1)]


class PlayerTagPayload(Frozen):
    name: NonBlank
    shirt: Annotated[int, Field(ge=1, le=99)]
    team: Team
    speed_chip_kmh: float | None = None
    distance_chip_km: float | None = None


class OverlayBase(Frozen):
    overlay_id: NonBlank
    match_id: NonBlank
    start_s: Annotated[float, Field(ge=0.0)]
    end_s: float
    priority: Annotated[int, Field(ge=1, le=5)]
    lang: Lang
    persona: Persona
    evidence_event_ids: tuple[str, ...]
    a11y_text: NonBlank

    @model_validator(mode="after")
    def _ends_after_it_starts(self):
        if self.end_s <= self.start_s:
            raise ValueError("end_s must be after start_s")
        return self


class ScoreboardOverlay(OverlayBase):
    type: Literal["scoreboard"] = "scoreboard"
    payload: ScoreboardPayload


class KeyMomentOverlay(OverlayBase):
    type: Literal["key_moment"] = "key_moment"
    evidence_event_ids: EventIds
    payload: KeyMomentPayload


class ExplanationOverlay(OverlayBase):
    type: Literal["explanation"] = "explanation"
    evidence_event_ids: EventIds
    payload: ExplanationPayload


class PlayerTagOverlay(OverlayBase):
    type: Literal["player_tag"] = "player_tag"
    evidence_event_ids: EventIds
    payload: PlayerTagPayload


Overlay = Union[ScoreboardOverlay, KeyMomentOverlay, ExplanationOverlay, PlayerTagOverlay]
_ADAPTER = TypeAdapter(Annotated[Overlay, Field(discriminator="type")])


def parse_overlay(data: dict) -> Overlay:
    return _ADAPTER.validate_python(data)


def overlay_json_schema() -> dict[str, Any]:
    return _ADAPTER.json_schema(mode="validation")
