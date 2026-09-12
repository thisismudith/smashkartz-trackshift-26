import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.race_context import enrich_race_context, normalize_pit_state, normalize_race_control_state


def rows(control, pit="ON_TRACK"):
    return [
        {
            "year": "2026", "event": "Test", "session": "Race", "driver": "HAM",
            "session_time_s": index * 20.0, "track_status": status, "pit_state": pit_state,
        }
        for index, (status, pit_state) in enumerate(zip(control, pit if isinstance(pit, list) else [pit] * len(control)))
    ]


def test_normalises_known_source_values_and_fails_closed():
    assert normalize_race_control_state(1) == "GREEN"
    assert normalize_race_control_state("Virtual Safety Car") == "VSC"
    assert normalize_race_control_state(None) == "UNKNOWN"
    assert normalize_pit_state("pit lane") == "PIT_LANE"
    assert normalize_pit_state(None) == "UNKNOWN"


def test_only_uninterrupted_on_track_green_rows_are_eligible():
    output = enrich_race_context(rows(["GREEN", "GREEN", "YELLOW", "GREEN", "GREEN"], ["ON_TRACK"] * 5))
    assert [row["normal_race_model_eligible"] for row in output] == [True, False, False, False, False]
    assert output[2]["normalized_race_control_state"] == "YELLOW"
    assert output[1]["race_control_transition_flag"] is True
    assert output[3]["race_control_transition_flag"] is True


def test_sc_vsc_red_pit_and_unknown_are_all_ineligible():
    output = enrich_race_context(rows(
        ["SC", "VSC", "RED", "GREEN", "GREEN"],
        ["ON_TRACK", "ON_TRACK", "ON_TRACK", "PIT_IN", "UNKNOWN"],
    ), transition_guard_rows=0)
    assert not any(row["normal_race_model_eligible"] for row in output)
    assert output[0]["safety_car_active"] is True
    assert output[1]["virtual_safety_car_active"] is True


def test_pit_transition_is_a_hard_boundary_with_a_guard_window():
    output = enrich_race_context(rows(["GREEN"] * 5, ["ON_TRACK", "ON_TRACK", "PIT_IN", "PIT_LANE", "ON_TRACK"]))
    assert [row["normal_race_model_eligible"] for row in output] == [True, False, False, False, False]
    assert output[1]["pit_transition_flag"] is True
    assert output[4]["pit_transition_flag"] is True


def test_green_flag_elapsed_resets_at_control_transition():
    output = enrich_race_context(rows(["GREEN", "GREEN", "YELLOW", "GREEN", "GREEN"]), transition_guard_rows=0)
    assert [row["green_flag_elapsed_s"] for row in output] == [0.0, 20.0, 0.0, 0.0, 20.0]
