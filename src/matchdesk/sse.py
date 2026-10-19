"""Server-Sent Events framing for the live feed: one JSON object per `data:` frame (SCHEMA.md section 1)."""
import json
from collections.abc import Iterable, Iterator


def encode_frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _parse(text: str) -> dict:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"malformed SSE data: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("SSE data must be a JSON object")
    return payload


def decode_frames(lines: Iterable[str]) -> Iterator[dict]:
    """Yield one dict per frame. Comments and non-data fields are ignored; multi-line data is joined with
    newlines per the SSE spec; a final frame without a trailing blank line is still delivered."""
    buffer: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if buffer:
                yield _parse("\n".join(buffer))
                buffer = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        if field == "data":
            buffer.append(value[1:] if value.startswith(" ") else value)
    if buffer:
        yield _parse("\n".join(buffer))
