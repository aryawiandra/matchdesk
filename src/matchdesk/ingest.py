"""Ingest a live feed (SSE lines) into the Analyst. Invalid frames fail loudly; nothing is silently skipped."""
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .analyst import Analyst, Detection
from .schema import Event, MatchInfo, parse_event
from .sse import decode_frames


@dataclass(frozen=True)
class Step:
    event: Event
    detections: tuple[Detection, ...]


def parse_frames(lines: Iterable[str]) -> tuple[MatchInfo, Iterator[Event]]:
    """Read the leading `match_info` frame now; return the remaining frames as a lazy, validated event iterator."""
    frames = decode_frames(lines)
    first = next(frames, None)
    if first is None:
        raise ValueError("empty feed: expected a match_info frame")
    if first.get("kind") != "match_info":
        raise ValueError("first frame must be match_info")
    return MatchInfo.model_validate(first), (parse_event(payload) for payload in frames)


def ingest_stream(lines: Iterable[str], analyst: Analyst) -> Iterator[Step]:
    _, events = parse_frames(lines)
    for event in events:
        yield Step(event, analyst.ingest(event))
