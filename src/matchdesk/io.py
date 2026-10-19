"""JSONL read/write. The stream and the hidden truth are always separate files."""
import json

from .generator import GeneratedMatch
from .schema import Event, MatchInfo, MatchTruth, parse_event


def to_jsonl(match: GeneratedMatch) -> str:
    lines = [match.info.model_dump_json()] + [e.model_dump_json() for e in match.events]
    return "\n".join(lines) + "\n"


def truth_to_json(truth: MatchTruth) -> str:
    return truth.model_dump_json(indent=2) + "\n"


def read_stream(text: str) -> tuple[MatchInfo, list[Event]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty stream")
    info = MatchInfo.model_validate_json(lines[0])
    return info, [parse_event(json.loads(ln)) for ln in lines[1:]]
