"""Focused CP-03 tests for C8 dynamic battle episodes."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.splits import make_split  # noqa: E402
from trackshift.features.battles import build_battle_episodes  # noqa: E402


def row(driver: str, ahead: str | None, gap: float | None, lap: int, segment: int, **extra):
    return {
        "year": 2026, "event": "EVT", "session": "Race", "lap": lap,
        "segment_id": segment, "entry_distance_m": float(segment * 100),
        "entry_lap_elapsed_s": float(segment * 2), "segment_time_s_offline": 2.0,
        "driver": driver, "driver_ahead": ahead, "gap_ahead_m_entry": gap,
        "relative_speed_to_ahead_mps": 1.5,
        "normal_race_model_eligible": True, "pit_state": "ON_TRACK",
        "race_control_transition_flag": False, "pit_transition_flag": False,
        **extra,
    }


def test_battle_starts_only_when_the_distance_close_following_criterion_is_met():
    result = build_battle_episodes([
        row("HAM", "ANT", 120.0, 1, 1),
        row("HAM", "ANT", 90.0, 1, 2),
    ])
    assert len(result.episodes) == 1
    episode = result.episodes[0]
    assert episode["duration_segments"] == 1
    assert episode["minimum_distance_gap_m"] == 90.0
    assert episode["minimum_time_gap_s"] is None
    assert result.audit_rows[0]["pairing_exclusion_reason"] == "NOT_CLOSE_FOLLOWING"


def test_pair_switch_terminates_the_old_directed_battle():
    result = build_battle_episodes([
        row("HAM", "ANT", 50.0, 1, 1),
        row("HAM", "VER", 45.0, 1, 2),
    ])
    assert [(episode["attacker"], episode["defender"], episode["bounded_by"]) for episode in result.episodes] == [
        ("HAM", "ANT", "PAIR_SWITCH"), ("HAM", "VER", "SESSION_END"),
    ]


def test_pass_terminates_old_battle_and_former_defender_can_attack_later():
    result = build_battle_episodes([
        row("HAM", "ANT", 40.0, 1, 1),
        # At the next contemporaneous segment ANT now observes HAM ahead.
        row("HAM", "RUS", 55.0, 1, 2),
        row("ANT", "HAM", 45.0, 1, 2),
    ])
    old = next(episode for episode in result.episodes if episode["attacker"] == "HAM" and episode["defender"] == "ANT")
    reversed_role = next(episode for episode in result.episodes if episode["attacker"] == "ANT" and episode["defender"] == "HAM")
    assert old["bounded_by"] == "PASS"
    assert old["pass_completed"] is True
    assert reversed_role["bounded_by"] == "SESSION_END"


def test_pit_and_race_control_transitions_are_hard_boundaries():
    race_control = build_battle_episodes([
        row("HAM", "ANT", 40.0, 1, 1),
        row("HAM", "ANT", 40.0, 1, 2, race_control_transition_flag=True),
        row("HAM", "ANT", 40.0, 1, 3),
    ])
    assert [episode["bounded_by"] for episode in race_control.episodes] == ["RACE_CONTROL_TRANSITION", "SESSION_END"]
    assert all(not step["race_control_transition_flag"] for step in race_control.battle_rows)

    pit = build_battle_episodes([
        row("HAM", "ANT", 40.0, 1, 1),
        row("HAM", "ANT", 40.0, 1, 2, pit_transition_flag=True),
    ])
    assert pit.episodes[0]["bounded_by"] == "PIT_TRANSITION"


def test_ineligible_rows_remain_auditable_but_are_not_battle_rows():
    result = build_battle_episodes([
        row("HAM", "ANT", 40.0, 1, 1, normal_race_model_eligible=False),
        row("HAM", "ANT", 40.0, 1, 2),
    ])
    assert result.audit_rows[0]["pairing_exclusion_reason"] == "NOT_NORMAL_RACE_MODEL_ELIGIBLE"
    assert len(result.battle_rows) == 1
    assert result.battle_rows[0]["segment_id"] == 2


def test_battle_ids_are_stable_unique_and_are_c9_split_compatible():
    rows = [
        row("HAM", "ANT", 40.0, 1, 1),
        row("HAM", "VER", 40.0, 1, 2),
        row("HAM", "ANT", 40.0, 1, 3),
    ]
    first = build_battle_episodes(rows)
    second = build_battle_episodes(list(reversed(rows)))
    first_ids = [episode["battle_id"] for episode in first.episodes]
    assert first_ids == [episode["battle_id"] for episode in second.episodes]
    assert len(first_ids) == len(set(first_ids))
    assert first_ids[0] == "2026_EVT_Race_HAM_ANT_Battle01"
    assignments = make_split(first.episodes, unit="battle_id", design="kfold:2", seed=7)
    assert {entry["group_key"] for entry in assignments} == set(first_ids)
