"""Bahasa Indonesia: templates, and the same Verifier gate with Indonesian wording, meaning and number rules."""
import pytest

from matchdesk.claims import Claim, Fact
from matchdesk.explainer import explain, template_claims
from matchdesk.matching import KINDS
from matchdesk.verifier import verify_claims
from tests.test_explainer import first_of, replay
from tests.test_verifier import GOAL_ID, ctx, goal_claim, reasons  # noqa: F401  (ctx is a fixture)

PROPERTY_SEEDS = (11, 12, 13)


def id_claim(text, **overrides):
    return goal_claim(text=text, lang="id", **overrides)


@pytest.fixture(scope="module")
def replayed(full_match):
    return replay(full_match)


# ---- templates
def test_every_detection_in_several_matches_gets_indonesian_claims_the_verifier_accepts():
    from matchdesk.config import GeneratorConfig
    from matchdesk.generator import generate_match

    seen = set()
    for seed in PROPERTY_SEEDS:
        context, found = replay(generate_match(GeneratorConfig(seed=seed)))
        for detection in found:
            claims = template_claims(detection, context, lang="id")
            assert claims and all(c.lang == "id" for c in claims)
            result = verify_claims(claims, context)
            assert result.ok, (detection.kind, [r.reasons for r in result.rejected])
            seen.add(detection.kind)
    assert seen == set(KINDS)


def test_the_indonesian_goal_names_the_scorer_the_club_the_minute_and_the_score(replayed):
    context, found = replayed
    goal = first_of(found, "goal")
    shot = context.event(goal.event_ids[-1])
    text = " ".join(c.text for c in template_claims(goal, context, lang="id"))
    after = context.score_after(shot.event_id)
    for part in (context.player(shot.player_id).name, context.club_name(shot.team), f"menit {context.minute(shot)}",
                 f"{after['H']}-{after['A']}", "mencetak gol"):
        assert part in text


def test_indonesian_decimals_use_a_comma(replayed):
    context, found = replayed
    text = template_claims(first_of(found, "pressing_spell"), context, lang="id")[0].text
    assert "," in text and "tekanan per menit" in text


def test_english_is_still_the_default(replayed):
    context, found = replayed
    goal = first_of(found, "goal")
    assert template_claims(goal, context) == template_claims(goal, context, lang="en")
    assert all(c.lang == "en" for c in template_claims(goal, context))


def test_an_unknown_language_is_an_error(replayed):
    context, found = replayed
    with pytest.raises(ValueError, match="language"):
        template_claims(first_of(found, "goal"), context, lang="xx")


# ---- the gate, in Indonesian
def test_a_good_indonesian_claim_is_accepted(ctx):
    assert verify_claims([id_claim("Wills Hartley mencetak gol pada menit 3 dari jarak 10 m.")], ctx).ok


@pytest.mark.parametrize("phrase", ["luar biasa", "spektakuler", "dramatis", "menakjubkan", "dominan"])
def test_indonesian_hype_is_rejected(ctx, phrase):
    claim = id_claim(f"Gol {phrase} pada menit 3 dari jarak 10 m.")
    assert any("unsupported wording" in r for r in reasons(verify_claims([claim], ctx)))


@pytest.mark.parametrize("word", ["karena", "berkat", "akibat", "sehingga"])
def test_indonesian_causal_stories_are_rejected(ctx, word):
    claim = id_claim(f"Gol pada menit 3 dari jarak 10 m {word} tekanan.")
    assert any("unsupported causal wording" in r for r in reasons(verify_claims([claim], ctx)))


def test_indonesian_has_no_gendered_pronoun_but_an_english_one_is_still_rejected(ctx):
    assert verify_claims([id_claim("Dia mencetak gol pada menit 3 dari jarak 10 m.")], ctx).ok
    claim = id_claim("His gol pada menit 3 dari jarak 10 m.")
    assert any("gendered pronoun" in r for r in reasons(verify_claims([claim], ctx)))


def test_an_english_sentence_in_an_indonesian_claim_is_rejected(ctx):
    claim = id_claim("Wills Hartley scored the goal in minute 3 from 10 m.")
    assert any("language" in r for r in reasons(verify_claims([claim], ctx)))


def test_an_indonesian_sentence_in_an_english_claim_is_rejected(ctx):
    claim = goal_claim(text="Wills Hartley mencetak gol yang pada menit 3 dari jarak 10 m.")
    assert any("language" in r for r in reasons(verify_claims([claim], ctx)))


def test_comma_decimals_are_read_and_checked_like_dot_decimals(ctx):
    press_ids = tuple(f"M0001-{i:05d}" for i in range(3, 15))
    fact = (Fact("pressure_per_min", 6.0, team="A", at_s=300.0),)
    good = Claim("Corvale FC menekan dengan 6,0 tekanan per menit.", press_ids, fact, lang="id")
    bad = Claim("Corvale FC menekan dengan 2,5 tekanan per menit.", press_ids, fact, lang="id")
    assert verify_claims([good], ctx).ok
    assert any("unsupported number 2,5" in r for r in reasons(verify_claims([bad], ctx)))


def test_the_meaning_rule_works_in_indonesian(ctx):
    vague = id_claim("Wills Hartley menyentuh bola pada menit 3 dari jarak 10 m.", requires_any=("mencetak", "gol"))
    assert any("expected one of" in r for r in reasons(verify_claims([vague], ctx)))
    contradicting = id_claim("Wills Hartley mencetak gol pada menit 3 dari jarak 10 m.", forbids=("mencetak",))
    assert any("must not say 'mencetak'" in r for r in reasons(verify_claims([contradicting], ctx)))


def test_claims_in_a_different_language_than_requested_are_rejected(ctx):
    english = goal_claim()
    result = verify_claims([english], ctx, lang="id")
    assert not result.ok and any("language" in r for r in reasons(result))
    assert verify_claims([english], ctx, lang="en").ok


# ---- explain in Indonesian
def test_without_a_model_the_indonesian_template_is_used(replayed):
    context, found = replayed
    result = explain(first_of(found, "goal"), context, lang="id")
    assert result.source == "template" and all(c.lang == "id" for c in result.claims)


def test_a_model_that_answers_in_english_is_blocked_in_indonesian_mode(replayed):
    context, found = replayed
    goal = first_of(found, "goal")
    english = lambda detection, ctx, feedback: template_claims(detection, ctx, lang="en")  # noqa: E731
    result = explain(goal, context, model=english, max_retries=0, lang="id")
    assert result.source == "template" and all(c.lang == "id" for c in result.claims)


# ---- through the pipeline, the workflow and the server
def indonesian_model(detection, ctx, feedback):
    """A well-behaved Indonesian model: same facts, different words."""
    base = template_claims(detection, ctx, lang="id")
    from dataclasses import replace

    return tuple(replace(c, text=c.text.replace("mencetak gol", "menjebol gawang").replace("Tembakan itu melaju", "Bola melesat"))
                 for c in base)


def test_the_pipeline_produces_indonesian_overlays(full_match):
    from matchdesk.pipeline import overlay_stream

    overlays = list(overlay_stream(full_match.events, full_match.info, lang="id"))
    assert overlays and all(o.lang == "id" for o in overlays)
    moments = [o for o in overlays if o.type == "key_moment"]
    assert moments and all("menit" in o.payload.headline or "Momentum" in o.payload.headline for o in moments)
    tags = [o for o in overlays if o.type == "player_tag"]
    assert tags and all(o.a11y_text.startswith("Label pemain") for o in tags)


def test_english_overlays_keep_their_english_accessibility_text(full_match):
    from matchdesk.pipeline import overlay_stream

    tags = [o for o in overlay_stream(full_match.events, full_match.info) if o.type == "player_tag"]
    assert tags and all(o.a11y_text.startswith("Player tag") for o in tags)


def test_only_english_and_indonesian_are_produced(full_match):
    from matchdesk.pipeline import overlay_stream
    from matchdesk.workflow import MatchDeskPipeline

    with pytest.raises(ValueError, match="English"):
        list(overlay_stream(full_match.events, full_match.info, lang="es"))
    with pytest.raises(ValueError, match="English"):
        MatchDeskPipeline(full_match.info, lang="es")


def test_the_workflow_matches_the_reference_in_indonesian_with_and_without_a_model(full_match):
    import asyncio

    from matchdesk.pipeline import overlay_stream
    from matchdesk.workflow import overlay_stream_async

    async def collect(**kwargs):
        return [o async for o in overlay_stream_async(full_match.events, full_match.info, lang="id", **kwargs)]

    for kwargs in ({}, {"model": indonesian_model}):
        assert asyncio.run(collect(**kwargs)) == list(overlay_stream(full_match.events, full_match.info, lang="id", **kwargs))


def test_an_indonesian_model_is_accepted_and_marked_as_a_model_answer(full_match):
    from matchdesk.pipeline import overlay_stream

    moments = [o for o in overlay_stream(full_match.events, full_match.info, lang="id", model=indonesian_model)
               if o.type == "key_moment"]
    assert {o.payload.source for o in moments} == {"model"}
    assert any("menjebol gawang" in o.payload.headline for o in moments)


def test_the_server_accepts_lang_id_and_rejects_other_languages():
    from fastapi.testclient import TestClient

    from matchdesk.feed import create_app
    from matchdesk.sse import decode_frames

    client = TestClient(create_app())
    with client.stream("GET", "/overlays/seed/5?period_s=300&speed=0&lang=id") as response:
        assert response.status_code == 200
        frames = list(decode_frames(response.iter_lines()))
    assert frames and {f["lang"] for f in frames} == {"id"}
    assert client.get("/overlays/seed/5?period_s=300&speed=0&lang=es").status_code == 422
