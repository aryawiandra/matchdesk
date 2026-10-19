"""Fault injection: corrupt good claims in known ways and check the Verifier blocks every one."""
import pytest

from matchdesk.explainer import template_claims
from matchdesk.faults import FAULTS, inject
from matchdesk.verifier import verify_claims
from tests.test_explainer import first_of, replay


@pytest.fixture(scope="module")
def replayed(full_match):
    return replay(full_match)


@pytest.fixture(scope="module")
def goal_claim(replayed):
    context, found = replayed
    return template_claims(first_of(found, "goal"), context)[0]


def test_the_clean_claim_is_accepted(replayed, goal_claim):
    context, _ = replayed
    assert verify_claims([goal_claim], context).ok


@pytest.mark.parametrize("fault", sorted(FAULTS))
def test_each_fault_changes_the_claim_and_is_blocked(replayed, goal_claim, fault):
    context, _ = replayed
    broken = inject(fault, goal_claim, context)
    assert broken is not None and broken != goal_claim
    result = verify_claims([broken], context)
    assert not result.ok and result.rejected


def test_a_fault_that_does_not_apply_returns_none(replayed):
    context, found = replayed
    swing = template_claims(first_of(found, "momentum_swing"), context)[0]
    assert swing.player_ids == ()
    assert inject("wrong_player", swing, context) is None


def test_unknown_fault_names_are_rejected(replayed, goal_claim):
    context, _ = replayed
    with pytest.raises(KeyError):
        inject("nope", goal_claim, context)


def test_wrong_fact_keeps_text_and_fact_consistent_so_only_recomputation_can_catch_it(replayed, goal_claim):
    context, _ = replayed
    broken = inject("wrong_fact", goal_claim, context)
    reasons = [r for rej in verify_claims([broken], context).rejected for r in rej.reasons]
    assert any(r.startswith("fact ") for r in reasons)
    assert not any("unsupported number" in r for r in reasons)
