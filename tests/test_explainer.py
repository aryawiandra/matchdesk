"""Template Explainer and the explain -> verify -> retry -> template-fallback flow."""
import pytest

from matchdesk import explainer
from matchdesk.analyst import Analyst
from matchdesk.claims import Claim
from matchdesk.config import GeneratorConfig, Thresholds
from matchdesk.context import MatchContext
from matchdesk.explainer import explain, template_claims
from matchdesk.generator import generate_match
from matchdesk.matching import KINDS
from matchdesk.verifier import verify_claims

PROPERTY_SEEDS = (11, 12, 13)


def replay(match):
    """Stream a match through context and Analyst; return (context, detections)."""
    context, analyst, found = MatchContext(match.info), Analyst(Thresholds()), []
    for e in match.events:
        context.add(e)
        found.extend(analyst.ingest(e))
    return context, found


@pytest.fixture(scope="module")
def replayed(full_match):
    return replay(full_match)


def first_of(found, kind):
    return next(d for d in found if d.kind == kind)


def bad_claims(*_):
    return [Claim("Somebody scored.", (), ())]


# ---- templates
def test_every_detection_in_several_matches_gets_template_claims_the_verifier_accepts():
    seen = set()
    for seed in PROPERTY_SEEDS:
        context, found = replay(generate_match(GeneratorConfig(seed=seed)))
        for detection in found:
            claims = template_claims(detection, context)
            assert claims, detection
            result = verify_claims(claims, context)
            assert result.ok, (detection.kind, [r.reasons for r in result.rejected])
            seen.add(detection.kind)
    assert seen == set(KINDS)


def test_goal_template_names_the_scorer_the_club_the_minute_and_the_score(replayed):
    context, found = replayed
    goal = first_of(found, "goal")
    shot = context.event(goal.event_ids[-1])
    text = " ".join(c.text for c in template_claims(goal, context))
    assert context.player(shot.player_id).name in text
    assert context.club_name(shot.team) in text
    assert f"minute {context.minute(shot)}" in text
    after = context.score_after(shot.event_id)
    assert f"{after['H']}-{after['A']}" in text


def test_templates_are_deterministic(replayed):
    context, found = replayed
    for detection in found[:40]:
        assert template_claims(detection, context) == template_claims(detection, context)


def test_every_template_claim_cites_events_and_states_only_backed_numbers(replayed):
    context, found = replayed
    for detection in found:
        for claim in template_claims(detection, context):
            assert claim.evidence_ids
            assert verify_claims([claim], context).ok


def test_templates_pin_down_what_happened_and_what_must_not_be_said(replayed):
    context, found = replayed
    goal = template_claims(first_of(found, "goal"), context)[0]
    assert "scored" in goal.requires_any and not goal.forbids
    for kind in ("big_chance", "sprint_burst", "pressing_spell"):
        if not any(d.kind == kind for d in found):
            continue
        claim = template_claims(first_of(found, kind), context)[0]
        assert "scored" in claim.forbids
    swing = template_claims(first_of(found, "momentum_swing"), context)[0]
    assert "momentum" in swing.requires_any


def test_a_big_chance_must_keep_its_outcome_and_may_not_become_a_goal(replayed):
    context, found = replayed
    chance = next((d for d in found if d.kind == "big_chance"), None)
    if chance is None:
        pytest.skip("this match has no big chance")
    claim = template_claims(chance, context)[0]
    outcome = context.event(chance.event_ids[-1]).outcome
    assert any(word in claim.requires_any for word in {"saved": ("saved",), "blocked": ("blocked",),
                                                       "off_target": ("off target",)}[outcome])


# ---- flow
def test_without_a_model_the_template_is_used_and_nothing_is_rejected(replayed):
    context, found = replayed
    result = explain(first_of(found, "goal"), context, model=None)
    assert result.source == "template" and result.attempts == 0 and result.rejections == ()
    assert result.claims and verify_claims(result.claims, context).ok


def test_a_good_model_answer_is_used_as_is(replayed):
    context, found = replayed
    goal = first_of(found, "goal")
    good = template_claims(goal, context)
    result = explain(goal, context, model=lambda d, c, feedback: good)
    assert result.source == "model" and result.attempts == 1 and result.claims == good


def test_a_bad_answer_is_retried_once_with_the_reasons_as_feedback(replayed):
    context, found = replayed
    goal = first_of(found, "goal")
    good, seen_feedback = template_claims(goal, context), []

    def model(detection, ctx, feedback):
        seen_feedback.append(feedback)
        return bad_claims() if not feedback else good

    result = explain(goal, context, model=model)
    assert result.source == "model_retry" and result.attempts == 2
    assert seen_feedback[0] == () and any("no evidence" in reason for reason in seen_feedback[1])
    assert result.rejected_claims == 1


def test_a_model_that_keeps_failing_falls_back_to_the_template(replayed):
    context, found = replayed
    goal = first_of(found, "goal")
    result = explain(goal, context, model=lambda d, c, f: bad_claims())
    assert result.source == "template" and result.attempts == 2
    assert result.claims == template_claims(goal, context)
    assert len(result.rejections) == 2 and result.rejected_claims == 2


def test_zero_retries_means_one_attempt_then_the_template(replayed):
    context, found = replayed
    result = explain(first_of(found, "goal"), context, model=lambda d, c, f: bad_claims(), max_retries=0)
    assert result.source == "template" and result.attempts == 1


def test_a_model_that_raises_is_recovered_from(replayed):
    context, found = replayed

    def exploding(detection, ctx, feedback):
        raise TimeoutError("model unavailable")

    result = explain(first_of(found, "goal"), context, model=exploding)
    assert result.source == "template" and result.attempts == 2
    assert any("explainer error" in reason for rej in result.rejections for reason in rej.reasons)


def test_a_model_answer_with_a_wrong_number_is_blocked(replayed):
    context, found = replayed
    goal = first_of(found, "goal")
    honest = template_claims(goal, context)[0]
    lie = Claim(honest.text.replace("minute", "minute 9"), honest.evidence_ids, honest.facts, honest.player_ids)
    result = explain(goal, context, model=lambda d, c, f: [lie], max_retries=0)
    assert result.source == "template"
    assert lie not in result.claims


def test_a_template_that_fails_verification_is_a_bug_and_raises(replayed, monkeypatch):
    context, found = replayed
    monkeypatch.setattr(explainer, "template_claims", bad_claims)
    with pytest.raises(RuntimeError, match="template"):
        explain(first_of(found, "goal"), context, model=None)
