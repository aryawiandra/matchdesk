"""The pipeline as a Microsoft Agent Framework workflow: Analyst -> Explainer <-> Verifier -> Producer.

The synchronous `overlay_stream` is the reference. The workflow must produce exactly the same overlays and the same
explanations, including when the model fails, answers badly, or recovers on retry."""
import asyncio
import time

import pytest

from matchdesk.claims import Claim
from matchdesk.explainer import template_claims
from matchdesk.pipeline import overlay_stream
from matchdesk.workflow import MatchDeskPipeline, overlay_stream_async


def run_async(coro):
    return asyncio.run(coro)


async def collect(events, info, **kwargs):
    return [overlay async for overlay in overlay_stream_async(events, info, **kwargs)]


def bad(*_):
    return [Claim("Nothing.", (), ())]


def exploding(detection, ctx, feedback):
    raise TimeoutError("model unavailable")


def flaky(detection, ctx, feedback):
    """Wrong on the first try; right once it is told why."""
    return bad() if not feedback else template_claims(detection, ctx)


def honest(detection, ctx, feedback):
    return template_claims(detection, ctx)


def both(match, **kwargs):
    """Run the reference and the workflow with the same options; return (overlays, explanations) for each."""
    ref_seen, wf_seen = [], []
    ref = list(overlay_stream(match.events, match.info, on_explanation=ref_seen.append, **kwargs))
    wf = run_async(collect(match.events, match.info, on_explanation=wf_seen.append, **kwargs))
    return (ref, ref_seen), (wf, wf_seen)


# ---- shape of the graph
def test_the_workflow_has_four_distinct_executors(short_match):
    pipeline = MatchDeskPipeline(short_match.info)
    assert set(pipeline.workflow.executors) == {"analyst", "explainer", "verifier", "producer"}


def test_only_english_is_produced_until_the_localizer_exists(short_match):
    with pytest.raises(ValueError, match="English"):
        MatchDeskPipeline(short_match.info, lang="id")


# ---- equivalence with the reference
def test_the_workflow_matches_the_reference_on_a_full_match_with_templates(full_match):
    (ref, ref_seen), (wf, wf_seen) = both(full_match)
    assert wf == ref and wf_seen == ref_seen
    assert ref and ref_seen


def test_the_workflow_matches_the_reference_for_the_analyst_view(short_match):
    (ref, _), (wf, _) = both(short_match, persona="analyst")
    assert wf == ref


@pytest.mark.parametrize("model", [exploding, bad, flaky, honest], ids=["exploding", "bad", "flaky", "honest"])
def test_the_workflow_matches_the_reference_for_every_kind_of_model_behaviour(full_match, model):
    (ref, ref_seen), (wf, wf_seen) = both(full_match, model=model)
    assert wf == ref
    assert wf_seen == ref_seen


def test_the_model_sources_are_what_the_behaviour_implies(full_match):
    sources = {name: {e.source for e in both(full_match, model=m)[1][1]}
               for name, m in (("honest", honest), ("flaky", flaky), ("bad", bad), ("exploding", exploding))}
    assert sources == {"honest": {"model"}, "flaky": {"model_retry"}, "bad": {"template"}, "exploding": {"template"}}


def test_zero_retries_matches_the_reference(full_match):
    (ref, ref_seen), (wf, wf_seen) = both(full_match, model=flaky, max_retries=0)
    assert wf == ref and wf_seen == ref_seen
    assert {e.source for e in wf_seen} == {"template"}


# ---- the retry is a real handoff inside the graph
def test_a_rejected_answer_goes_back_to_the_explainer_through_the_verifier(full_match):
    events = list(full_match.events)
    goal_index = next(i for i, e in enumerate(events) if e.type == "shot" and e.outcome == "goal")

    async def go():
        pipeline = MatchDeskPipeline(full_match.info, model=flaky)
        for event in events[:goal_index]:
            await pipeline.feed(event)
        before = list(pipeline.invocations)
        await pipeline.feed(events[goal_index])
        return before, pipeline.invocations[len(before):]

    _, during = run_async(go())
    assert during.count("analyst") == 1
    assert during.count("explainer") == 2 and during.count("verifier") == 2
    assert during.count("producer") == 1


def test_a_slow_model_does_not_block_the_event_loop(full_match):
    events = list(full_match.events)
    goal_index = next(i for i, e in enumerate(events) if e.type == "shot" and e.outcome == "goal")

    def slow(detection, ctx, feedback):
        time.sleep(0.3)
        return template_claims(detection, ctx)

    async def go():
        pipeline = MatchDeskPipeline(full_match.info, model=slow)
        for event in events[:goal_index]:
            await pipeline.feed(event)
        beats = 0

        async def heartbeat():
            nonlocal beats
            while True:
                await asyncio.sleep(0.02)
                beats += 1

        task = asyncio.create_task(heartbeat())
        await pipeline.feed(events[goal_index])
        task.cancel()
        return beats

    assert run_async(go()) >= 8  # about 15 when the loop stays free; about 0 when the model call blocks it


def test_a_quiet_event_skips_the_explainer_and_the_verifier(short_match):
    async def go():
        pipeline = MatchDeskPipeline(short_match.info)
        await pipeline.feed(short_match.events[0])
        return pipeline.invocations

    assert run_async(go()) == ["analyst", "producer"]


def test_a_template_that_fails_verification_is_suppressed_and_logged_not_fatal(full_match, monkeypatch, caplog):
    from matchdesk import workflow as workflow_module

    monkeypatch.setattr(workflow_module, "template_claims", bad)

    async def go():
        pipeline = MatchDeskPipeline(full_match.info)
        overlays = []
        for event in full_match.events:
            overlays += await pipeline.feed(event)
        overlays += await pipeline.finish()
        return overlays, pipeline.suppressed

    with caplog.at_level("ERROR", logger="matchdesk"):
        overlays, suppressed = run_async(go())
    assert suppressed and all("template claims failed verification" in reason for reason in suppressed)
    assert overlays and all(o.type == "scoreboard" for o in overlays)  # the match played on; only the bad insights were dropped
    assert "template claims failed verification" in caplog.text


# ---- lifecycle
def test_finish_adds_the_final_scoreboard_with_the_true_score(full_match):
    async def go():
        pipeline = MatchDeskPipeline(full_match.info)
        for event in full_match.events:
            await pipeline.feed(event)
        return await pipeline.finish()

    final = run_async(go())
    assert len(final) == 1 and final[0].type == "scoreboard"
    assert final[0].payload.score == full_match.truth.score


def test_an_event_out_of_sequence_is_rejected_by_the_analyst(short_match):
    async def go():
        pipeline = MatchDeskPipeline(short_match.info)
        await pipeline.feed(short_match.events[0])
        await pipeline.feed(short_match.events[2])  # seq 1 is missing

    with pytest.raises(Exception, match="seq"):
        run_async(go())
