"""Recorded model answers: replay a real model's sentences through the live Verifier gate.

A live model is not reachable from every deployment (ours runs on a laptop). So a real model's accepted answers are
recorded once, per match, and replayed later as if the model had just written them. A recording is data read from a
file, so it is never trusted: every replayed sentence goes through the Verifier again, now, and a tampered or stale
recording ends up as the template. Overlays still say `source = model`, because a model wrote the words; the page says
plainly that this is a recorded run."""
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from .analyst import Analyst, Detection
from .claims import Claim
from .config import Thresholds, load_thresholds
from .context import MatchContext
from .explainer import Model, explain, template_claims
from .generator import GeneratedMatch
from .schema import Frozen

SCHEMA_VERSION = "1"


class Recording(Frozen):
    schema_version: str = SCHEMA_VERSION
    seed: int
    period_s: float
    model: str
    server: str
    recorded_at: str
    answers: dict[str, tuple[str, ...]]  # detection key -> the sentences the model wrote, in claim order


def detection_key(detection: Detection) -> str:
    return f"{detection.kind}:{','.join(detection.event_ids)}"


def record_answers(
    match: GeneratedMatch,
    narrator: Model,
    *,
    period_s: float,
    model: str,
    server: str,
    recorded_at: str,
    thresholds: Thresholds | None = None,
) -> Recording:
    """Run the match through the Analyst and the Explainer with a real model; keep what the model wrote and the
    Verifier accepted. Detections that fell back to the template are not recorded."""
    ctx, analyst = MatchContext(match.info), Analyst(thresholds or load_thresholds())
    answers: dict[str, tuple[str, ...]] = {}
    for event in match.events:
        ctx.add(event)
        for detection in analyst.ingest(event):
            result = explain(detection, ctx, model=narrator)
            if result.source in ("model", "model_retry"):
                answers[detection_key(detection)] = tuple(claim.text for claim in result.claims)
    return Recording(seed=match.truth.seed, period_s=period_s, model=model, server=server,
                     recorded_at=recorded_at, answers=answers)


class RecordedModel:
    """A `Model` that answers from a recording. Facts, evidence and names still come from the template claim."""

    def __init__(self, recording: Recording):
        self._answers = recording.answers

    def __call__(self, detection: Detection, ctx: MatchContext, feedback: Sequence[str]) -> tuple[Claim, ...]:
        sentences = self._answers.get(detection_key(detection))
        if sentences is None:
            raise LookupError("no recorded answer for this detection")
        base = template_claims(detection, ctx)
        if len(sentences) != len(base):
            raise ValueError(f"recorded answer has {len(sentences)} sentences, expected {len(base)}")
        return tuple(replace(claim, text=sentence) for claim, sentence in zip(base, sentences))


def save_recording(recording: Recording, path: Path | str) -> None:
    Path(path).write_text(recording.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_recording(path: Path | str) -> Recording:
    return Recording.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_directory(directory: Path | str) -> dict[int, Recording]:
    """Every `*.json` recording in a folder, by seed. A missing folder means no recordings."""
    folder = Path(directory)
    if not folder.is_dir():
        return {}
    recordings = [load_recording(path) for path in sorted(folder.glob("*.json"))]
    return {recording.seed: recording for recording in recordings}
