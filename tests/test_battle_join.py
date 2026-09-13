"""A2: the causal C8 battle join and CP-14's fail-closed split unit.

The property under test throughout is that a join which cannot be made stays
unmade. A fabricated ``battle_id`` would place unrelated opportunities in one
C9 split group, and the leak would show up only as an implausibly good CP-14
test score with nothing in the artifacts to explain it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.features.battle_join import (
    BattleIndex,
    BattleJoinError,
    JoinStatus,
    build_number_to_code,
    load_episodes,
)


def episode(battle_id, attacker, defender, start_lap, end_lap, *,
            year="2026", event="Australian Grand Prix", session="Race"):
    return {
        "battle_id": battle_id, "year": year, "event": event, "session": session,
        "attacker": attacker, "defender": defender,
        "start_lap": start_lap, "end_lap": end_lap,
    }


@pytest.fixture
def index():
    return BattleIndex([
        e for e in (
            _mk(episode("B1", "VER", "HAM", 4, 7)),
            _mk(episode("B2", "VER", "HAM", 12, 15)),
            _mk(episode("B3", "NOR", "PIA", 2, 3)),
            # Same pair, another session: must not be reachable from the Race.
            _mk(episode("B4", "VER", "HAM", 5, 6, session="Sprint")),
        )
    ])


def _mk(raw):
    from trackshift.features.battle_join import BattleEpisode

    return BattleEpisode(
        battle_id=raw["battle_id"], year=raw["year"], event=raw["event"],
        session=raw["session"], attacker=raw["attacker"], defender=raw["defender"],
        start_lap=raw["start_lap"], end_lap=raw["end_lap"],
    )


def resolve(index, lap, *, attacker="VER", defender_code="HAM", session="Race"):
    return index.resolve(year="2026", event="Australian Grand Prix", session=session,
                         attacker=attacker, defender_code=defender_code, lap=lap)


def test_a_lap_inside_an_episode_resolves_to_it(index):
    assert resolve(index, 5) == ("B1", JoinStatus.JOINED)
    assert resolve(index, 13) == ("B2", JoinStatus.JOINED)


def test_episode_bounds_are_inclusive(index):
    assert resolve(index, 4)[0] == "B1"
    assert resolve(index, 7)[0] == "B1"


def test_a_lap_between_episodes_is_refused_not_snapped_to_the_nearest(index):
    # Lap 9 falls in the gap between B1 (4-7) and B2 (12-15). Attaching it to
    # either would invent a battle the cars were not in.
    battle_id, status = resolve(index, 9)
    assert battle_id is None
    assert status == JoinStatus.NO_EPISODE_FOR_LAP


def test_an_unknown_pair_is_refused(index):
    battle_id, status = resolve(index, 5, attacker="LEC")
    assert battle_id is None
    assert status == JoinStatus.NO_EPISODE_FOR_PAIR


def test_the_session_is_part_of_the_key(index):
    # B4 covers laps 5-6 but only in the Sprint. The Race lookup must not see it,
    # and lap 5 in the Race belongs to B1.
    assert resolve(index, 5, session="Race")[0] == "B1"
    assert resolve(index, 5, session="Sprint")[0] == "B4"


def test_a_missing_defender_code_is_refused_with_its_own_reason(index):
    for missing in (None, "", "   "):
        battle_id, status = resolve(index, 5, defender_code=missing)
        assert battle_id is None
        assert status == JoinStatus.NO_DEFENDER_CODE


def test_overlapping_episodes_are_refused_rather_than_picked_between():
    overlapping = BattleIndex([
        _mk(episode("B1", "VER", "HAM", 4, 9)),
        _mk(episode("B2", "VER", "HAM", 7, 12)),
    ])
    battle_id, status = resolve(overlapping, 8)
    assert battle_id is None
    assert status == JoinStatus.AMBIGUOUS_EPISODE


def test_number_to_code_is_keyed_per_session():
    mapping = build_number_to_code([
        {"year": "2026", "event": "Australian Grand Prix", "session": "Race",
         "driver": "VER", "driver_number": 1},
        {"year": "2022", "event": "Australian Grand Prix", "session": "Race",
         "driver": "LEC", "driver_number": 1},
    ])
    assert mapping[("2026", "Australian Grand Prix", "Race", "1")] == "VER"
    assert mapping[("2022", "Australian Grand Prix", "Race", "1")] == "LEC"


def test_number_to_code_accepts_the_float_spelling_of_a_car_number():
    mapping = build_number_to_code([
        {"year": "2026", "event": "E", "session": "Race",
         "driver": "VER", "driver_number": "44.0"},
    ])
    assert mapping[("2026", "E", "Race", "44")] == "VER"


def test_one_number_mapping_to_two_codes_in_a_session_is_an_error():
    with pytest.raises(BattleJoinError, match="maps to both"):
        build_number_to_code([
            {"year": "2026", "event": "E", "session": "Race",
             "driver": "VER", "driver_number": 1},
            {"year": "2026", "event": "E", "session": "Race",
             "driver": "HAM", "driver_number": 1},
        ])


def test_episodes_missing_a_join_key_are_refused_not_skipped(tmp_path):
    path = tmp_path / "battle_episodes.jsonl"
    path.write_text(
        json.dumps(episode("B1", "VER", "HAM", 1, 2)) + "\n"
        + json.dumps({"battle_id": "B2", "year": "2026", "event": "E"}) + "\n",
        encoding="utf-8")
    with pytest.raises(BattleJoinError, match="missing"):
        load_episodes(path)


def test_a_missing_c8_file_names_the_command_that_builds_it(tmp_path):
    with pytest.raises(BattleJoinError, match="build_battles.py"):
        load_episodes(tmp_path / "absent.jsonl")


def test_round_trips_through_the_jsonl_c8_writes(tmp_path):
    path = tmp_path / "battle_episodes.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in (
        episode("B1", "VER", "HAM", 4, 7),
        episode("B2", "NOR", "PIA", 1, 3),
    )) + "\n", encoding="utf-8")
    loaded = BattleIndex(load_episodes(path))
    assert len(loaded) == 2
    assert loaded.pairs == 2
    assert resolve(loaded, 5)[0] == "B1"
