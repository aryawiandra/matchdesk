"""Scoring detections against planted key moments (one-to-one, per kind)."""
import pytest

from matchdesk.analyst import Detection
from matchdesk.matching import match_detections
from matchdesk.schema import KeyMoment


def det(kind="goal", team="H", start=100.0, end=100.0, player=None):
    return Detection(kind, team, start, end, ("M0001-00001",), player, 0.5, end)


def moment(kind="goal", team="H", start=100.0, end=100.0, player=None):
    return KeyMoment(kind=kind, team=team, start_s=start, end_s=end, event_ids=("M0001-00001",),
                     player_id=player, magnitude=0.5)


def test_perfect_match_scores_one_for_precision_and_recall():
    scores = match_detections([det()], [moment()])
    goal = scores["goal"]
    assert (goal.true_positives, goal.detections, goal.truth) == (1, 1, 1)
    assert goal.precision == pytest.approx(1.0) and goal.recall == pytest.approx(1.0)


def test_wrong_team_or_kind_does_not_match():
    assert match_detections([det(team="A")], [moment()])["goal"].true_positives == 0
    scores = match_detections([det(kind="big_chance")], [moment(kind="goal")])
    assert scores["goal"].true_positives == 0 and scores["big_chance"].true_positives == 0


def test_matching_is_one_to_one():
    scores = match_detections([det(), det()], [moment()])
    goal = scores["goal"]
    assert goal.true_positives == 1
    assert goal.precision == pytest.approx(0.5) and goal.recall == pytest.approx(1.0)


def test_point_events_allow_a_small_time_tolerance_only():
    assert match_detections([det(start=101.5, end=101.5)], [moment()])["goal"].true_positives == 1
    assert match_detections([det(start=110.0, end=110.0)], [moment()])["goal"].true_positives == 0


def test_interval_kinds_match_on_overlap():
    truth = [moment(kind="pressing_spell", team="A", start=300.0, end=700.0)]
    inside = [det(kind="pressing_spell", team="A", start=400.0, end=580.0)]
    outside = [det(kind="pressing_spell", team="A", start=800.0, end=980.0)]
    assert match_detections(inside, truth)["pressing_spell"].true_positives == 1
    assert match_detections(outside, truth)["pressing_spell"].true_positives == 0


def test_sprint_requires_the_same_player_when_both_are_known():
    truth = [moment(kind="sprint_burst", start=500.0, end=504.0, player="H05")]
    same = [det(kind="sprint_burst", start=502.0, end=502.0, player="H05")]
    other = [det(kind="sprint_burst", start=502.0, end=502.0, player="H06")]
    assert match_detections(same, truth)["sprint_burst"].true_positives == 1
    assert match_detections(other, truth)["sprint_burst"].true_positives == 0


def test_undefined_precision_and_recall_are_none_not_zero():
    scores = match_detections([], [])
    assert set(scores) == {"goal", "big_chance", "momentum_swing", "pressing_spell", "sprint_burst"}
    assert scores["goal"].precision is None and scores["goal"].recall is None
    only_truth = match_detections([], [moment()])["goal"]
    assert only_truth.precision is None and only_truth.recall == pytest.approx(0.0)
