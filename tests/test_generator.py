import json
from collections import Counter

import pytest

from matchdesk.config import GeneratorConfig, load_thresholds
from matchdesk.generator import generate_match
from matchdesk.metrics import pearson
from matchdesk.io import read_stream, to_jsonl, truth_to_json
from matchdesk.validate import validate_stream

KINDS = {"goal", "big_chance", "momentum_swing", "pressing_spell", "sprint_burst"}


def test_stream_passes_all_schema_rules(full_match):
    assert validate_stream(full_match.info, full_match.events, full_match.truth) == []


def test_full_match_has_realistic_volume_and_all_event_families(full_match):
    counts = Counter(e.type for e in full_match.events)
    assert 700 <= len(full_match.events) <= 6000
    for t in ("pass", "shot", "tackle", "possession_change", "pressure", "meta"):
        assert counts[t] > 0, t


def test_same_seed_is_byte_identical(short_match):
    again = generate_match(GeneratorConfig(seed=7, period_s=600.0))
    assert to_jsonl(again) == to_jsonl(short_match)
    assert truth_to_json(again.truth) == truth_to_json(short_match.truth)


def test_different_seed_differs(short_match):
    other = generate_match(GeneratorConfig(seed=8, period_s=600.0))
    assert to_jsonl(other) != to_jsonl(short_match)


def test_stream_contains_no_truth_fields(short_match):
    text = to_jsonl(short_match)
    for leak in ("xg", "dominance", "key_moment", "seed", "press_H", "success_prob"):
        assert leak not in text


def test_truth_is_consistent_with_stream(full_match):
    truth = full_match.truth
    ids = {e.event_id for e in full_match.events}
    goals = sum(1 for e in full_match.events if e.type == "shot" and e.outcome == "goal")
    assert truth.score["H"] + truth.score["A"] == goals
    assert set(truth.shot_xg) <= ids and set(truth.pass_success_prob) <= ids
    shots = {e.event_id for e in full_match.events if e.type == "shot"}
    assert set(truth.shot_xg) == shots
    for km in truth.key_moments:
        assert km.kind in KINDS
        assert km.start_s <= km.end_s
        assert set(km.event_ids) <= ids
        assert 0.0 <= km.magnitude <= 1.0


def test_goal_and_big_chance_moments_match_shots(full_match):
    by_id = {e.event_id: e for e in full_match.events}
    th = load_thresholds()
    for km in full_match.truth.key_moments:
        if km.kind == "goal":
            assert by_id[km.event_ids[-1]].outcome == "goal"
        if km.kind == "big_chance":
            shot = by_id[km.event_ids[0]]
            assert shot.outcome != "goal"
            assert full_match.truth.shot_xg[shot.event_id] >= th.big_chance_xg


def test_sprint_burst_moments_have_visible_speed(full_match):
    th = load_thresholds()
    by_id = {e.event_id: e for e in full_match.events}
    for km in full_match.truth.key_moments:
        if km.kind == "sprint_burst":
            assert any(by_id[i].actor_speed_kmh >= th.sprint_speed_kmh for i in km.event_ids)


def test_phases_tile_the_match_and_dominance_in_range(full_match):
    phases = full_match.truth.phases
    assert phases[0].start_s == 0
    for a, b in zip(phases, phases[1:]):
        assert a.end_s == pytest.approx(b.start_s)
    assert all(-1 <= p.dominance <= 1 for p in phases)
    assert len(full_match.truth.dominance_by_minute) >= 90


def test_player_totals_physically_plausible(full_match):
    totals = full_match.truth.player_totals
    assert len(totals) == 22
    for t in totals.values():
        assert 0 < t.top_speed_kmh <= 45
        assert 2000 < t.distance_m < 16000


def test_dominance_drives_play(full_match):
    xs, ys = [], []
    for p in full_match.truth.phases:
        h = a = 0
        for e in full_match.events:
            if e.type == "pass" and e.outcome == "complete" and e.end_x >= 60 and p.start_s <= e.match_s < p.end_s:
                h, a = (h + 1, a) if e.team == "H" else (h, a + 1)
        if h + a >= 10:
            xs.append(p.dominance)
            ys.append(h / (h + a))
    assert len(xs) >= 6
    assert pearson(xs, ys) > 0.5


def test_jsonl_round_trip(short_match):
    info, events = read_stream(to_jsonl(short_match))
    assert info == short_match.info
    assert tuple(events) == tuple(short_match.events)
    json.loads(truth_to_json(short_match.truth))


def test_validate_stream_detects_corruption(short_match):
    events = list(short_match.events)
    events[5], events[6] = events[6], events[5]
    assert validate_stream(short_match.info, events, short_match.truth) != []


def test_load_thresholds_defaults_and_override(tmp_path):
    th = load_thresholds()
    assert th.sprint_speed_kmh == 25.0 and th.distance_step_m == 2000.0
    p = tmp_path / "t.toml"
    p.write_text("[indicators]\nsprint_speed_kmh = 28.0\ndistance_step_m = 1000.0\nspeed_chip_cooldown_s = 30.0\n"
                 "[key_moments]\nbig_chance_xg = 0.4\nswing_delta_dominance = 0.6\npressing_spell_level = 0.7\n"
                 "pressing_spell_min_s = 120.0\nsprint_min_seconds = 2\n")
    assert load_thresholds(p).sprint_speed_kmh == 28.0
