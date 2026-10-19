"""Verifier: a claim is published only if its evidence exists and every number it states is recomputed from the events."""
import pytest

from matchdesk import metrics
from matchdesk.claims import Claim, Fact
from matchdesk.context import MatchContext
from matchdesk.verifier import verify_claims
from tests.test_analyst import mk_shot
from tests.test_metrics import mk_pass, mk_pressure

GOAL_ID = "M0001-00001"
SAVED_ID = "M0001-00002"
PASS_ID = "M0001-00000"


@pytest.fixture()
def ctx(short_match):
    context = MatchContext(short_match.info)
    for e in (
        mk_pass(0, 10.0, team="H", player="H05"),
        mk_shot(1, 125.0, team="H", x=95.0, y=34.0, outcome="goal", player="H09", speed=27.0),
        mk_shot(2, 200.0, team="A", x=90.0, y=34.0, outcome="saved", player="A09"),
    ):
        context.add(e)
    for i in range(12):  # 12 pressures by A in the 120 s up to t=300 -> 6.0 per minute
        context.add(mk_pressure(3 + i, 185.0 + 10.0 * i, team="A"))
    return context


def goal_claim(**overrides):
    base = dict(
        text="Wills Hartley scored in minute 3 from 10 m.",
        evidence_ids=(GOAL_ID,),
        facts=(Fact("minute", 3, event_id=GOAL_ID), Fact("shot_distance_m", 10.0, event_id=GOAL_ID)),
        player_ids=("H09",),
    )
    base.update(overrides)
    return Claim(**base)


def reasons(result):
    return [r for rejection in result.rejected for r in rejection.reasons]


def test_a_fully_supported_claim_is_accepted(ctx):
    result = verify_claims([goal_claim()], ctx)
    assert result.ok and len(result.accepted) == 1 and result.rejected == ()


def test_empty_claim_list_is_not_ok(ctx):
    assert not verify_claims([], ctx).ok


def test_blank_text_and_missing_evidence_are_rejected(ctx):
    result = verify_claims([goal_claim(text="  "), goal_claim(evidence_ids=())], ctx)
    assert not result.ok and result.accepted == ()
    assert any("empty text" in r for r in reasons(result))
    assert any("no evidence" in r for r in reasons(result))


def test_an_unknown_or_future_event_id_is_rejected(ctx):
    result = verify_claims([goal_claim(evidence_ids=("M0001-09999",))], ctx)
    assert any("unknown event id M0001-09999" in r for r in reasons(result))


def test_a_fact_that_contradicts_the_events_is_rejected(ctx):
    wrong = goal_claim(text="Wills Hartley scored in minute 3 from 40 m.",
                       facts=(Fact("minute", 3, event_id=GOAL_ID), Fact("shot_distance_m", 40.0, event_id=GOAL_ID)))
    result = verify_claims([wrong], ctx)
    assert any("shot_distance_m" in r and "40" in r for r in reasons(result))


def test_a_number_in_the_text_without_a_backing_fact_is_rejected(ctx):
    claim = goal_claim(text="Wills Hartley scored his 3rd goal of the season in minute 3 from 10 m.",
                       facts=(Fact("minute", 3, event_id=GOAL_ID), Fact("shot_distance_m", 10.0, event_id=GOAL_ID)))
    ok = verify_claims([claim], ctx)
    assert ok.ok  # 3 is backed by the minute fact, so this phrasing slips through by value: documented limit
    unbacked = goal_claim(text="Wills Hartley scored his 7th goal in minute 3 from 10 m.")
    assert any("unsupported number 7" in r for r in reasons(verify_claims([unbacked], ctx)))


def test_rounded_numbers_pass_within_display_precision_only(ctx):
    near = goal_claim(text="Wills Hartley scored in minute 3 from 10 m.",
                      facts=(Fact("minute", 3, event_id=GOAL_ID), Fact("shot_distance_m", 10.4, event_id=GOAL_ID)))
    assert verify_claims([near], ctx).ok
    far = goal_claim(text="Wills Hartley scored in minute 3 from 11 m.",
                     facts=(Fact("minute", 3, event_id=GOAL_ID), Fact("shot_distance_m", 11.0, event_id=GOAL_ID)))
    assert not verify_claims([far], ctx).ok


def test_a_player_who_is_not_in_the_cited_events_is_rejected(ctx):
    result = verify_claims([goal_claim(player_ids=("H03",))], ctx)
    assert any("player H03 is not in the cited events" in r for r in reasons(result))


def test_an_unknown_player_id_is_rejected(ctx):
    result = verify_claims([goal_claim(player_ids=("Z99",))], ctx)
    assert any("unknown player Z99" in r for r in reasons(result))


def test_a_fact_the_cited_event_cannot_have_is_unverifiable(ctx):
    claim = goal_claim(text="The pass left at 20 km/h.", evidence_ids=(PASS_ID,), player_ids=(),
                       facts=(Fact("shot_speed_kmh", 20.0, event_id=PASS_ID),))
    assert any("cannot verify shot_speed_kmh" in r for r in reasons(verify_claims([claim], ctx)))


def test_score_after_a_goal_is_checked(ctx):
    right = goal_claim(text="It made the score 1-0.",
                       facts=(Fact("score_home", 1, event_id=GOAL_ID), Fact("score_away", 0, event_id=GOAL_ID)))
    wrong = goal_claim(text="It made the score 2-0.",
                       facts=(Fact("score_home", 2, event_id=GOAL_ID), Fact("score_away", 0, event_id=GOAL_ID)))
    assert verify_claims([right], ctx).ok
    assert any("score_home" in r for r in reasons(verify_claims([wrong], ctx)))


def test_pressure_and_momentum_facts_are_recomputed_from_the_stream(ctx):
    events = [ctx.event(f"M0001-{i:05d}") for i in range(15)]
    level = metrics.pressure_intensity(events, "A", 300.0)
    assert level == pytest.approx(6.0)
    press_ids = tuple(f"M0001-{i:05d}" for i in range(3, 15))
    good = Claim("Corvale FC pressed at 6.0 pressures per minute.", press_ids,
                 (Fact("pressure_per_min", 6.0, team="A", at_s=300.0),))
    bad = Claim("Corvale FC pressed at 2.0 pressures per minute.", press_ids,
                (Fact("pressure_per_min", 2.0, team="A", at_s=300.0),))
    assert verify_claims([good], ctx).ok
    assert not verify_claims([bad], ctx).ok
    momentum = metrics.momentum_at(events, 300.0)
    claim = Claim(f"Momentum read {momentum:.1f}.", press_ids, (Fact("momentum", round(momentum, 1), at_s=300.0),))
    assert verify_claims([claim], ctx).ok


def test_good_and_bad_claims_are_split_not_merged(ctx):
    result = verify_claims([goal_claim(), goal_claim(evidence_ids=())], ctx)
    assert not result.ok
    assert len(result.accepted) == 1 and len(result.rejected) == 1


def test_evidence_must_have_been_seen_already(short_match):
    context = MatchContext(short_match.info)
    claim = goal_claim()
    assert any("unknown event id" in r for r in reasons(verify_claims([claim], context)))
    context.add(mk_pass(0, 10.0))
    context.add(mk_shot(1, 125.0, outcome="goal", player="H09"))
    assert verify_claims([claim], context).ok
