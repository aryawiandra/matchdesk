"""The pipeline as a Microsoft Agent Framework workflow: Analyst -> Explainer <-> Verifier -> Producer.

Each stage is a custom `Executor`; the graph carries the handoffs. A rejected answer travels back from the Verifier to
the Explainer on a conditional edge (the retry), and when the retries are used up the Explainer hands over a template
that the Verifier checks like any other answer. No model is needed to run it: the Explainer uses templates unless a
model is supplied. `pipeline.overlay_stream` is the synchronous reference this workflow must match exactly.

One workflow run handles one event; the executors keep their state for the whole match (verified in
20-build/sponsor-digest.md section 5), so build one `MatchDeskPipeline` per match."""
import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from typing import Never

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from .analyst import Analyst, Detection
from .claims import Claim
from .config import Thresholds, load_thresholds
from .context import MatchContext
from .explainer import AttemptFailure, Explanation, Model, Source, template_claims
from .overlay import SUPPORTED_LANGS, Lang, Overlay, Persona
from .producer import TICK_S, Producer
from .schema import Event, MatchInfo
from .verifier import verify_claims

DEFAULT_MAX_RETRIES = 1
logger = logging.getLogger("matchdesk")


# ---- messages between executors
@dataclass(frozen=True)
class EndOfMatch:
    last_s: float


@dataclass(frozen=True)
class Pending:
    """A detection that still needs an explanation. `final` means: stop asking the model, use the template."""
    order: int
    detection: Detection
    feedback: tuple[str, ...]
    attempts: int
    failures: tuple[AttemptFailure, ...]
    final: bool


@dataclass(frozen=True)
class Draft:
    pending: Pending
    claims: tuple[Claim, ...]
    source: Source
    error: str | None
    attempts: int


@dataclass(frozen=True)
class Drafts:
    event_s: float
    drafts: tuple[Draft, ...]
    resolved: tuple[tuple[int, Explanation], ...]


@dataclass(frozen=True)
class Round:
    """What leaves the Analyst and the Verifier: explanations settled so far and detections still to settle."""
    event_s: float
    resolved: tuple[tuple[int, Explanation], ...] = ()
    retry: tuple[Pending, ...] = ()
    end: bool = False


def _needs_work(message: Round) -> bool:
    return bool(message.retry)


def _is_settled(message: Round) -> bool:
    return not message.retry


# ---- executors
class AnalystExecutor(Executor):
    def __init__(self, ctx: MatchContext, analyst: Analyst):
        super().__init__(id="analyst")
        self._ctx = ctx
        self._analyst = analyst

    @handler
    async def on_event(self, event: Event, wf: WorkflowContext[Round]) -> None:
        self._ctx.add(event)
        detections = self._analyst.ingest(event)
        retry = tuple(Pending(i, d, (), 0, (), final=False) for i, d in enumerate(detections))
        await wf.send_message(Round(event.match_s, retry=retry))

    @handler
    async def on_end(self, end: EndOfMatch, wf: WorkflowContext[Round]) -> None:
        await wf.send_message(Round(end.last_s, end=True))


class ExplainerExecutor(Executor):
    """Proposes claims: from the model when there is one and retries remain, otherwise from the template."""

    def __init__(self, ctx: MatchContext, model: Model | None, lang: str):
        super().__init__(id="explainer")
        self._ctx = ctx
        self._model = model
        self._lang = lang

    @handler
    async def propose(self, round_: Round, wf: WorkflowContext[Drafts]) -> None:
        drafts = tuple([await self._draft(pending) for pending in round_.retry])
        await wf.send_message(Drafts(round_.event_s, drafts, round_.resolved))

    async def _draft(self, pending: Pending) -> Draft:
        if self._model is None or pending.final:
            return Draft(pending, template_claims(pending.detection, self._ctx, self._lang), "template", None, pending.attempts)
        attempts = pending.attempts + 1
        try:
            # A model call can take seconds; run it in a worker thread so the event loop (and other streams) keep going.
            claims = tuple(await asyncio.to_thread(self._model, pending.detection, self._ctx, pending.feedback))
        except Exception as error:  # a failing model must never blank the screen
            return Draft(pending, (), "model", f"explainer error: {type(error).__name__}: {error}", attempts)
        return Draft(pending, claims, "model", None, attempts)


class VerifierExecutor(Executor):
    """The gate. Accepts an answer, or sends the reasons back to the Explainer, or demands the template."""

    def __init__(self, ctx: MatchContext, max_retries: int, lang: str):
        super().__init__(id="verifier")
        self._ctx = ctx
        self._max_retries = max_retries
        self._lang = lang
        self.suppressed: list[str] = []

    @handler
    async def verify(self, drafts: Drafts, wf: WorkflowContext[Round]) -> None:
        resolved = list(drafts.resolved)
        retry: list[Pending] = []
        for draft in drafts.drafts:
            outcome = self._judge(draft)
            if isinstance(outcome, Pending):
                retry.append(outcome)
            elif outcome is not None:
                resolved.append((draft.pending.order, outcome))
        await wf.send_message(Round(drafts.event_s, tuple(resolved), tuple(retry)))

    def _judge(self, draft: Draft) -> Explanation | Pending | None:
        pending = draft.pending
        if draft.source == "template":
            check = verify_claims(draft.claims, self._ctx, self._lang)
            if not check.ok:
                # A bug, never a reason to stop the match: drop this insight, say so loudly, keep streaming.
                reasons = [r for rejection in check.rejected for r in rejection.reasons]
                message = f"template claims failed verification for {pending.detection.kind}: {reasons}"
                logger.error(message)
                self.suppressed.append(message)
                return None
            return Explanation(pending.detection, draft.claims, "template", draft.attempts, pending.failures)
        if draft.error is not None:
            feedback, rejected = (draft.error,), 0
        else:
            result = verify_claims(draft.claims, self._ctx, self._lang)
            if result.ok:
                source: Source = "model" if draft.attempts == 1 else "model_retry"
                return Explanation(pending.detection, draft.claims, source, draft.attempts, pending.failures)
            feedback = tuple(r for rejection in result.rejected for r in rejection.reasons) or ("no claims returned",)
            rejected = len(result.rejected)
        failures = pending.failures + (AttemptFailure(draft.attempts, feedback, rejected),)
        return Pending(pending.order, pending.detection, feedback, draft.attempts, failures,
                       final=draft.attempts >= self._max_retries + 1)


class ProducerExecutor(Executor):
    def __init__(self, producer: Producer, persona: Persona, lang: Lang, tick_s: float,
                 on_explanation: Callable[[Explanation], None] | None):
        super().__init__(id="producer")
        self._producer = producer
        self._persona = persona
        self._lang = lang
        self._tick_s = tick_s
        self._next_tick = tick_s
        self._on_explanation = on_explanation
        self._explanations: list[Explanation] = []

    @handler
    async def produce(self, round_: Round, wf: WorkflowContext[Never, tuple]) -> None:
        if round_.end:
            final = (self._producer.scoreboard(round_.event_s, self._tick_s, self._lang, self._persona),)
            recap = self._producer.recap(self._explanations, self._lang, self._persona)
            await wf.yield_output(final + ((recap,) if recap is not None else ()))
            return
        batch: list[Overlay] = []
        while self._next_tick < round_.event_s:
            batch.append(self._producer.scoreboard(self._next_tick, self._tick_s, self._lang, self._persona))
            self._next_tick += self._tick_s
        for _, explanation in sorted(round_.resolved, key=lambda pair: pair[0]):
            self._explanations.append(explanation)
            if self._on_explanation is not None:
                self._on_explanation(explanation)
            batch.extend(self._producer.for_explanation(explanation, self._lang, self._persona))
        batch.sort(key=lambda overlay: overlay.start_s)
        await wf.yield_output(tuple(batch))


# ---- the pipeline
class MatchDeskPipeline:
    def __init__(
        self,
        info: MatchInfo,
        thresholds: Thresholds | None = None,
        model: Model | None = None,
        persona: Persona = "fan",
        lang: Lang = "en",
        max_retries: int = DEFAULT_MAX_RETRIES,
        tick_s: float = TICK_S,
        on_explanation: Callable[[Explanation], None] | None = None,
    ):
        if lang not in SUPPORTED_LANGS:
            raise ValueError(f"only English and Bahasa Indonesia are produced, not {lang!r}")
        thresholds = thresholds or load_thresholds()
        ctx = MatchContext(info)
        analyst = AnalystExecutor(ctx, Analyst(thresholds))
        explainer = ExplainerExecutor(ctx, model, lang)
        verifier = VerifierExecutor(ctx, max_retries, lang)
        producer = ProducerExecutor(Producer(ctx, thresholds), persona, lang, tick_s, on_explanation)
        self.workflow = (
            WorkflowBuilder(start_executor=analyst, output_from=[producer])
            .add_edge(analyst, explainer, condition=_needs_work)
            .add_edge(analyst, producer, condition=_is_settled)
            .add_edge(explainer, verifier)
            .add_edge(verifier, explainer, condition=_needs_work)
            .add_edge(verifier, producer, condition=_is_settled)
            .build()
        )
        self.invocations: list[str] = []
        self._verifier = verifier
        self._last_s = 0.0

    @property
    def suppressed(self) -> list[str]:
        """Insights dropped because even the template failed the Verifier (a bug; empty in a healthy run)."""
        return self._verifier.suppressed

    async def feed(self, event: Event) -> tuple[Overlay, ...]:
        overlays = await self._run(event)
        self._last_s = event.match_s
        return overlays

    async def finish(self) -> tuple[Overlay, ...]:
        return await self._run(EndOfMatch(self._last_s))

    async def _run(self, message: Event | EndOfMatch) -> tuple[Overlay, ...]:
        overlays: tuple[Overlay, ...] = ()
        async for event in self.workflow.run(message, stream=True):
            if event.type == "executor_invoked":
                self.invocations.append(event.executor_id)
            elif event.type == "output":
                overlays += tuple(event.data)
        return overlays


async def overlay_stream_async(
    events: Iterable[Event],
    info: MatchInfo,
    thresholds: Thresholds | None = None,
    model: Model | None = None,
    persona: Persona = "fan",
    lang: Lang = "en",
    max_retries: int = DEFAULT_MAX_RETRIES,
    tick_s: float = TICK_S,
    on_explanation: Callable[[Explanation], None] | None = None,
) -> AsyncIterator[Overlay]:
    pipeline = MatchDeskPipeline(info, thresholds, model, persona, lang, max_retries, tick_s, on_explanation)
    for event in events:
        for overlay in await pipeline.feed(event):
            yield overlay
    for overlay in await pipeline.finish():
        yield overlay
