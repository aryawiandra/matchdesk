"""Producer turns verified explanations into overlays; the pipeline streams them in time order."""
import json

import pytest

from matchdesk.claims import Claim
from matchdesk.config import GeneratorConfig, Thresholds
from matchdesk.explainer import explain, template_claims
from matchdesk.generator import generate_match
from matchdesk.overlay import parse_overlay
from matchdesk.pipeline import overlay_stream
from matchdesk.producer import DURATION_S, PRIORITY, Producer
from tests.test_explainer import first_of, replay


@pytest.fixture(scope="module")
def replayed(full_match):
    return replay(full_match)


@pytest.fixture(scope="module")
def producer(replayed):
    return Producer(replayed[0], Thresholds())


def explained(replayed, kind):
    context, found = replayed
    return explain(first_of(found, kind), context)


def by_type(overlays, type_):
    return [o for o in overlays if o.type == type_]


# ---- context.score_at
def test_score_at_reads_the_running_score_by_time(replayed, full_match):
    context, _ = replayed
    goals = [e for e in full_match.events if e.type == "shot" and e.outcome == "goal"]
    first = goals[0]
    assert context.score_at(first.match_s - 0.01) != context.score_at(first.match_s)
    assert context.score_at(first.match_s)[first.team] == 1
    assert context.score_at(0.0) == {"H": 0, "A": 0}
    assert context.score_at(10 ** 6) == full_match.truth.score


# ---- goal overlays
def test_a_goal_becomes_key_moment_and_player_tag_for_fans_and_adds_detail_for_analysts(replayed, producer):
    explanation = explained(replayed, "goal")
    overlays = producer.for_explanation(explanation)
    assert [o.type for o in overlays] == ["key_moment", "player_tag"]
    analyst = producer.for_explanation(explanation, persona="analyst")
    assert [o.type for o in analyst] == ["key_moment", "explanation", "player_tag"]
    moment = overlays[0]
    assert moment.start_s == pytest.approx(explanation.detection.detected_at_s)
    assert moment.end_s == pytest.approx(moment.start_s + DURATION_S["goal"])
    assert moment.priority == PRIORITY["goal"] == 5
    assert moment.payload.headline == explanation.claims[0].text
    assert moment.payload.kind == "goal" and moment.payload.team == explanation.detection.team
    shot = replayed[0].event(explanation.detection.event_ids[-1])
    assert (moment.payload.x, moment.payload.y) == (shot.x, shot.y)


def test_the_player_tag_names_the_scorer_and_cites_the_shot(replayed, producer):
    explanation = explained(replayed, "goal")
    tag = by_type(producer.for_explanation(explanation), "player_tag")[0]
    shot = replayed[0].event(explanation.detection.event_ids[-1])
    player = replayed[0].player(shot.player_id)
    assert (tag.payload.name, tag.payload.shirt, tag.payload.team) == (player.name, player.shirt, player.team)
    assert tag.evidence_event_ids == (shot.event_id,)
    assert player.name in tag.a11y_text


def test_the_analyst_view_adds_only_the_detail_beyond_the_headline(replayed, producer):
    explanation = explained(replayed, "goal")
    assert len(explanation.claims) == 2
    assert by_type(producer.for_explanation(explanation, persona="fan"), "explanation") == []
    analyst = by_type(producer.for_explanation(explanation, persona="analyst"), "explanation")[0]
    assert analyst.persona == "analyst"
    assert [s.text for s in analyst.payload.sentences] == [explanation.claims[1].text]
    assert analyst.payload.sentences[0].evidence_event_ids == explanation.claims[1].evidence_ids
    assert explanation.claims[0].text not in analyst.a11y_text


def test_a_single_claim_has_no_detail_to_add_in_either_view(replayed, producer):
    explanation = explained(replayed, "momentum_swing")
    assert len(explanation.claims) == 1
    for persona in ("fan", "analyst"):
        assert [o.type for o in producer.for_explanation(explanation, persona=persona)] == ["key_moment"]


def test_key_moment_evidence_is_the_unique_union_of_the_claims_evidence(replayed, producer):
    explanation = explained(replayed, "goal")
    moment = producer.for_explanation(explanation)[0]
    expected = tuple(dict.fromkeys(eid for claim in explanation.claims for eid in claim.evidence_ids))
    assert moment.evidence_event_ids == expected


def test_the_key_moment_says_where_its_words_came_from(replayed, producer):
    context, found = replayed
    goal = first_of(found, "goal")
    from_template = producer.for_explanation(explain(goal, context))[0]
    from_model = producer.for_explanation(explain(goal, context, model=lambda d, c, f: template_claims(d, c)))[0]
    retried = producer.for_explanation(explain(goal, context, model=lambda d, c, f: template_claims(d, c) if f else [
        Claim("Nothing.", (), ())]))[0]
    assert [from_template.payload.source, from_model.payload.source, retried.payload.source] == [
        "template", "model", "model_retry"]


# ---- other kinds
def test_a_pressing_spell_has_no_pitch_point_and_no_player_tag(replayed, producer):
    overlays = producer.for_explanation(explained(replayed, "pressing_spell"), persona="analyst")
    assert [o.type for o in overlays] == ["key_moment"]
    assert overlays[0].payload.x is None and overlays[0].payload.y is None


def test_a_sprint_tag_shows_the_speed_chip_for_the_first_sprint_of_a_player(replayed, producer):
    explanation = explained(replayed, "sprint_burst")
    tag = by_type(producer.for_explanation(explanation), "player_tag")[0]
    event = replayed[0].event(explanation.detection.event_ids[0])
    assert tag.payload.speed_chip_kmh == pytest.approx(event.actor_speed_kmh)


def test_every_kind_gets_a_priority_and_a_duration():
    assert set(DURATION_S) == set(PRIORITY) == {"goal", "big_chance", "sprint_burst", "pressing_spell", "momentum_swing"}
    assert all(1 <= p <= 5 for p in PRIORITY.values()) and all(d > 0 for d in DURATION_S.values())


def test_every_overlay_for_every_detection_validates_and_cites_known_events(replayed, producer):
    context, found = replayed
    ids = set()
    for detection in found:
        explanation = explain(detection, context)
        for persona in ("fan", "analyst"):
            for overlay in producer.for_explanation(explanation, persona=persona):
                assert parse_overlay(json.loads(overlay.model_dump_json())) == overlay
                assert overlay.overlay_id not in ids
                ids.add(overlay.overlay_id)
                assert all(context.event(eid) is not None for eid in overlay.evidence_event_ids)
                assert overlay.a11y_text.strip()


# ---- scoreboard
def test_scoreboard_shows_the_score_and_clock_at_that_time(replayed, producer, full_match):
    goal = next(e for e in full_match.events if e.type == "shot" and e.outcome == "goal")
    before = producer.scoreboard(goal.match_s - 1.0)
    after = producer.scoreboard(goal.match_s + 1.0)
    assert after.payload.score[goal.team] == before.payload.score[goal.team] + 1
    assert after.start_s == pytest.approx(goal.match_s + 1.0) and after.end_s == pytest.approx(after.start_s + 10.0)
    minutes, seconds = divmod(int(goal.match_s + 1.0), 60)
    assert after.payload.clock == f"{minutes:02d}:{seconds:02d}"
    assert after.payload.home == replayed[0].club_name("H") and after.payload.away == replayed[0].club_name("A")
    assert after.evidence_event_ids == ()


# ---- pipeline
@pytest.fixture(scope="module")
def streamed(full_match):
    return list(overlay_stream(full_match.events, full_match.info))


def test_overlays_arrive_in_time_order(streamed):
    starts = [o.start_s for o in streamed]
    assert starts == sorted(starts)


def test_a_scoreboard_tick_every_ten_seconds_ending_at_the_true_score(streamed, full_match):
    ticks = by_type(streamed, "scoreboard")
    assert len(ticks) >= full_match.events[-1].match_s // 10 - 1
    assert ticks[-1].payload.score == full_match.truth.score


def test_every_goal_has_a_goal_key_moment(streamed, full_match):
    goals = [o for o in by_type(streamed, "key_moment") if o.payload.kind == "goal"]
    assert len(goals) == sum(full_match.truth.score.values())


def test_no_overlay_cites_an_event_from_its_own_future(streamed, full_match):
    time_of = {e.event_id: e.match_s for e in full_match.events}
    for overlay in streamed:
        for eid in overlay.evidence_event_ids:
            assert time_of[eid] <= overlay.start_s + 1e-9


def test_the_stream_is_deterministic(full_match, streamed):
    assert list(overlay_stream(full_match.events, full_match.info)) == streamed


def test_explanations_are_reported_to_the_callback(full_match):
    seen = []
    list(overlay_stream(full_match.events, full_match.info, on_explanation=seen.append))
    assert seen and all(e.source == "template" for e in seen)


def test_a_model_that_always_fails_leaves_the_overlays_unchanged(full_match, streamed):
    def exploding(detection, ctx, feedback):
        raise TimeoutError("model unavailable")

    seen = []
    degraded = list(overlay_stream(full_match.events, full_match.info, model=exploding, on_explanation=seen.append))
    assert degraded == streamed
    assert seen and all(e.source == "template" and e.rejections for e in seen)


def test_only_english_is_produced_until_the_localizer_exists(full_match):
    with pytest.raises(ValueError, match="English"):
        list(overlay_stream(full_match.events, full_match.info, lang="id"))


def test_persona_and_language_flow_into_every_overlay():
    match = generate_match(GeneratorConfig(seed=5, period_s=300.0))
    overlays = list(overlay_stream(match.events, match.info, persona="analyst", lang="en"))
    assert overlays and all(o.persona == "analyst" and o.lang == "en" for o in overlays)
