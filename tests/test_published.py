from pathlib import Path

import pytest

from matchdesk.config import GeneratorConfig
from matchdesk.generator import generate_match
from matchdesk.io import read_stream, to_jsonl, truth_to_json
from matchdesk.validate import validate_stream

PUBLISHED = Path(__file__).resolve().parents[1] / "data" / "published"
SEEDS = (101, 102, 103)


@pytest.mark.parametrize("seed", SEEDS)
def test_published_files_match_a_fresh_regeneration(seed):
    match = generate_match(GeneratorConfig(seed=seed))
    mid = match.info.match_id
    assert (PUBLISHED / f"{mid}.jsonl").read_text(encoding="utf-8") == to_jsonl(match)
    assert (PUBLISHED / f"{mid}.truth.json").read_text(encoding="utf-8") == truth_to_json(match.truth)


@pytest.mark.parametrize("seed", SEEDS)
def test_published_streams_validate(seed):
    info, events = read_stream((PUBLISHED / f"M{seed:04d}.jsonl").read_text(encoding="utf-8"))
    assert validate_stream(info, events) == []
