"""Full-time recap: a result sentence proved by the final score, the goals, and (analyst view) the key moments. Every
sentence is verified again before it is shown."""
import json

import pytest

from matchdesk.config import GeneratorConfig
from matchdesk.explainer import explain
from matchdesk.generator import generate_match
from matchdesk.overlay import RecapOverlay, parse_overlay
from matchdesk.pipeline import overlay_stream
from matchdesk.recap import recap_sections
from matchdesk.verifier import verify_claims
from tests.test_explainer import replay

PROPERTY_SEEDS = (11, 12, 13)


def explanations_for(match, lang="en"):
    context, found = replay(match)
    return context, [explain(d, context, lang=lang) for d in found]


def flat(sections):
    return [claim for section in sections for claim in section.claims]


# ---- the recap itself
@pytest.mark.parametrize("lang", ["en", "id"])
def test_every_recap_sentence_passes_the_verifier_in_both_languages_and_views(lang):
    for seed in PROPERTY_SEEDS:
        match = generate_match(GeneratorConfig(seed=seed))
        context, explanations = explanations_for(match, lang)
        for persona in ("fan", "analyst"):
            sections = recap_sections(explanations, context, lang, persona)
            claims = flat(sections)
            assert claims
            result = verify_claims(claims, context, lang)
            assert result.ok, [r.reasons for r in result.rejected]


def test_the_result_sentence_states_the_final_score_and_both_clubs(full_match):
    context, explanations = explanations_for(full_match)
    sections = recap_sections(explanations, context, "en", "fan")
    result = sections[0].claims[0]
    final = full_match.truth.score
    assert f"{final['H']}-{final['A']}" in result.text
    assert context.club_name("H") in result.text and context.club_name("A") in result.text


def test_the_recap_lists_exactly_the_goals_that_were_scored(full_match):
    context, explanations = explanations_for(full_match)
    sections = recap_sections(explanations, context, "en", "fan")
    goals = next(s for s in sections if s.key == "goals")
    shots = [e.event_id for e in full_match.events if e.type == "shot" and e.outcome == "goal"]
    assert [c.evidence_ids[-1] for c in goals.claims] == shots[:len(goals.claims)]
    assert len(goals.claims) == min(len(shots), 6)


def test_the_analyst_view_adds_key_moments_and_the_fan_view_does_not(full_match):
    context, explanations = explanations_for(full_match)
    fan = [s.key for s in recap_sections(explanations, context, "en", "fan")]
    analyst = [s.key for s in recap_sections(explanations, context, "en", "analyst")]
    assert "moments" not in fan and "moments" in analyst
    assert analyst[:len(fan)] == fan


def test_a_goalless_match_has_a_result_and_no_goals_section():
    for seed in range(40):
        match = generate_match(GeneratorConfig(seed=seed, period_s=300.0))
        if not any(e.type == "shot" and e.outcome == "goal" for e in match.events):
            context, explanations = explanations_for(match)
            keys = [s.key for s in recap_sections(explanations, context, "en", "fan")]
            assert keys == ["result"]
            return
    pytest.skip("no goalless short match among the first 40 seeds")


def test_recap_sections_carry_a_title_in_the_requested_language(full_match):
    context, explanations = explanations_for(full_match)
    assert [s.title for s in recap_sections(explanations, context, "en", "fan")][:2] == ["Result", "Goals"]
    assert [s.title for s in recap_sections(explanations, context, "id", "fan")][:2] == ["Hasil", "Gol"]


# ---- the overlay
def recap_overlays(match, **kwargs):
    return [o for o in overlay_stream(match.events, match.info, **kwargs) if o.type == "recap"]


def test_the_pipeline_ends_with_the_scoreboard_and_then_one_recap(full_match):
    overlays = list(overlay_stream(full_match.events, full_match.info))
    assert [o.type for o in overlays[-2:]] == ["scoreboard", "recap"]
    assert len(recap_overlays(full_match)) == 1


def test_the_recap_overlay_round_trips_and_cites_real_events(full_match):
    (recap,) = recap_overlays(full_match, persona="analyst")
    assert isinstance(recap, RecapOverlay)
    assert parse_overlay(json.loads(recap.model_dump_json())) == recap
    known = {e.event_id for e in full_match.events}
    assert recap.evidence_event_ids and set(recap.evidence_event_ids) <= known
    assert all(s.evidence_event_ids for section in recap.payload.sections for s in section.sentences)
    assert recap.a11y_text.strip() and recap.priority == 5


def test_the_recap_is_in_the_requested_language_and_view(full_match):
    (fan_id,) = recap_overlays(full_match, lang="id")
    assert fan_id.lang == "id" and fan_id.payload.sections[0].title == "Hasil"
    (fan,) = recap_overlays(full_match, persona="fan")
    (analyst,) = recap_overlays(full_match, persona="analyst")
    assert len(analyst.payload.sections) > len(fan.payload.sections)


def test_the_recap_arrives_after_everything_else(full_match):
    overlays = list(overlay_stream(full_match.events, full_match.info))
    starts = [o.start_s for o in overlays]
    assert starts == sorted(starts)
    assert overlays[-1].type == "recap" and overlays[-1].start_s == overlays[-2].start_s


def test_the_workflow_produces_the_same_recap(full_match):
    import asyncio

    from matchdesk.workflow import overlay_stream_async

    async def collect():
        return [o async for o in overlay_stream_async(full_match.events, full_match.info, persona="analyst")]

    assert asyncio.run(collect()) == list(overlay_stream(full_match.events, full_match.info, persona="analyst"))
