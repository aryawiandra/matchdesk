"""Typed contracts for the stream and the hidden truth (SCHEMA.md v1)."""
import math
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from . import SCHEMA_VERSION

Team = Literal["H", "A"]
X = Annotated[float, Field(ge=0.0, le=105.0)]
Y = Annotated[float, Field(ge=0.0, le=68.0)]
ActorSpeed = Annotated[float, Field(ge=0.0, le=45.0)]
ActorDistance = Annotated[float, Field(ge=0.0, le=16000.0)]
BALL_SPEED_TOLERANCE_KMH = 0.5


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EventBase(Frozen):
    event_id: str
    match_id: str
    seq: int = Field(ge=0)
    period: Literal[1, 2]
    match_s: float = Field(ge=0.0)
    team: Team

    @model_validator(mode="after")
    def _event_id_matches(self):
        expected = f"{self.match_id}-{self.seq:05d}"
        if self.event_id != expected:
            raise ValueError(f"event_id {self.event_id!r} must equal {expected!r}")
        return self


class Meta(EventBase):
    type: Literal["meta"] = "meta"
    kind: Literal["kickoff", "half_time", "second_half_start", "full_time"]


class Pass(EventBase):
    type: Literal["pass"] = "pass"
    player_id: str
    recipient_id: str
    x: X
    y: Y
    end_x: X
    end_y: Y
    outcome: Literal["complete", "incomplete"]
    duration_s: float = Field(gt=0.0)
    ball_speed_kmh: float = Field(ge=0.0, le=200.0)
    actor_speed_kmh: ActorSpeed
    actor_distance_m: ActorDistance

    @model_validator(mode="after")
    def _ball_speed_matches_geometry(self):
        d = math.hypot(self.end_x - self.x, self.end_y - self.y)
        expected = 3.6 * d / self.duration_s
        if abs(expected - self.ball_speed_kmh) > BALL_SPEED_TOLERANCE_KMH:
            raise ValueError(f"ball_speed_kmh {self.ball_speed_kmh} disagrees with distance/duration {expected:.1f}")
        return self


class Shot(EventBase):
    type: Literal["shot"] = "shot"
    player_id: str
    x: X
    y: Y
    end_x: X
    end_y: Y
    outcome: Literal["goal", "saved", "blocked", "off_target"]
    shot_speed_kmh: float = Field(ge=0.0, le=200.0)
    actor_speed_kmh: ActorSpeed
    actor_distance_m: ActorDistance


class Tackle(EventBase):
    type: Literal["tackle"] = "tackle"
    player_id: str
    target_id: str
    x: X
    y: Y
    outcome: Literal["won", "lost"]
    actor_speed_kmh: ActorSpeed
    actor_distance_m: ActorDistance


class PossessionChange(EventBase):
    type: Literal["possession_change"] = "possession_change"
    x: X
    y: Y
    cause: Literal["tackle", "interception", "incomplete_pass", "shot_end"]


class Pressure(EventBase):
    type: Literal["pressure"] = "pressure"
    player_id: str
    target_id: str
    x: X
    y: Y
    duration_s: float = Field(gt=0.0)
    actor_speed_kmh: ActorSpeed
    actor_distance_m: ActorDistance


Event = Union[Meta, Pass, Shot, Tackle, PossessionChange, Pressure]
_EVENT_ADAPTER = TypeAdapter(Annotated[Event, Field(discriminator="type")])


def parse_event(data: dict) -> Event:
    return _EVENT_ADAPTER.validate_python(data)


class Club(Frozen):
    club_id: str
    name: str


class Player(Frozen):
    player_id: str
    name: str
    shirt: int = Field(ge=1, le=99)
    role: Literal["GK", "DEF", "MID", "FWD"]
    team: Team


class Pitch(Frozen):
    length_m: float = 105.0
    width_m: float = 68.0


class MatchInfo(Frozen):
    kind: Literal["match_info"] = "match_info"
    schema_version: str = SCHEMA_VERSION
    match_id: str
    pitch: Pitch = Pitch()
    clubs: dict[str, Club]
    players: tuple[Player, ...]


KeyMomentKind = Literal["goal", "big_chance", "momentum_swing", "pressing_spell", "sprint_burst"]


class KeyMoment(Frozen):
    kind: KeyMomentKind
    team: Team
    start_s: float
    end_s: float
    event_ids: tuple[str, ...]
    player_id: str | None = None
    magnitude: float = Field(ge=0.0, le=1.0)


class Phase(Frozen):
    start_s: float
    end_s: float
    dominance: float = Field(ge=-1.0, le=1.0)
    tempo: float
    press_H: float
    press_A: float


class PlayerTotals(Frozen):
    distance_m: float
    top_speed_kmh: float


class MatchTruth(Frozen):
    schema_version: str = SCHEMA_VERSION
    match_id: str
    seed: int
    score: dict[str, int]
    phases: tuple[Phase, ...]
    dominance_by_minute: tuple[float, ...]
    key_moments: tuple[KeyMoment, ...]
    shot_xg: dict[str, float]
    pass_success_prob: dict[str, float]
    player_totals: dict[str, PlayerTotals]
