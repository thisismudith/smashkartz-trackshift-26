"""Focused CP-05 orchestration guards; all fixtures are local and tiny."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("materialize_chain_s", ROOT / "scripts" / "features" / "materialize_chain_s.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _write_partition(root: Path, circuit: str, rows: list[dict]) -> None:
    target = root / f"circuit={circuit}"
    target.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(target / "segments.parquet", index=False)


def _write_lake(root: Path, event: str, session: str = "Race") -> None:
    target = root / "year=2026" / f"event={event.replace(' ', '_')}" / f"session={session}"
    target.mkdir(parents=True)
    pd.DataFrame([{
        "year": "2026", "event": event, "session": session, "driver": "AAA", "lap": 1,
        "track_status": "1", "lap_start_session_s": 0.0, "pit_in_session_s": None,
        "pit_out_session_s": None,
    }]).to_parquet(target / "telemetry_20m.parquet", index=False)


def test_discovery_excludes_british_and_records_incomplete_spanish(tmp_path):
    segments, lake = tmp_path / "segments", tmp_path / "lake"
    _write_partition(segments, "australian", [
        {"year": "2026", "event": "Australian Grand Prix", "session": "Race"},
        {"year": "2026", "event": "British Grand Prix", "session": "Race"},
    ])
    _write_lake(lake, "Australian Grand Prix")
    _write_lake(lake, "British Grand Prix")
    selected, skipped = MODULE.discover_partitions(segments, lake, ["2026"], ["British Grand Prix"])
    assert [(item.event, item.session) for item in selected] == [("Australian Grand Prix", "Race")]
    assert any(item["event"] == "British Grand Prix" and item["code"] == "EXCLUDED_EVENT" for item in skipped)
    assert any(item["event"] == "Spanish Grand Prix" and item["code"] == "NO_USABLE_C1_RACE_OR_SPRINT" for item in skipped)


def test_manifest_version_mismatch_fails_clearly(tmp_path):
    (tmp_path / "run_manifest.json").write_text('{"schema_version": "old"}', encoding="utf-8")
    with pytest.raises(ValueError, match="manifest version mismatch"):
        MODULE.assert_output_root_compatible(tmp_path)


def test_c6_unavailable_is_not_false_or_zero():
    quantity = MODULE._c6_quantity(None)
    assert quantity == {
        "value": None, "provenance": "RULE", "unit": None,
        "reason": "UNAVAILABLE_C6: no matching public C6 segment-entry overlay",
    }


def test_m08_retains_c5_reason_and_references_the_c9_assignment():
    source = {
        "battle_id": "b1", "segment_index": 1, "year": "2026", "event": "Australian Grand Prix",
        "session": "Race", "lap": 2, "segment_id": 3, "normal_race_model_eligible": True,
        "unavailable_quantities": {
            "fuel_load_delta_kg_est": {"value": None, "provenance": "INFERRED", "unit": "kg", "reason": "UNAVAILABLE_C5: fixture"},
        },
        "c6_eligibility": {"value": None, "provenance": "RULE", "unit": None, "reason": "UNAVAILABLE_C6: fixture"},
        "c9_split_assignment": {"group_key": "b1", "fold_id": "fold_0", "split_role": "EVALUATION"},
    }
    row = MODULE.build_rival_state_features([source], split_reference={"assignment_version": "c9-v1"})["rows"][0]
    assert row["fuel_load_delta_kg_est"]["reason"] == "UNAVAILABLE_C5: fixture"
    assert row["c6_eligibility"]["value"] is None and "UNAVAILABLE_C6" in row["c6_eligibility"]["reason"]
    assert row["c9_split_assignment"]["group_key"] == "b1"


def test_m08_marks_missing_contemporaneous_defender_as_incomplete():
    source = {
        "battle_id": "b1", "segment_index": 1, "year": "2026", "event": "Australian Grand Prix",
        "session": "Race", "lap": 2, "segment_id": 3, "normal_race_model_eligible": True,
        "unavailable_quantities": {
            "defender_speed_entry_mps": {"value": None, "provenance": "DERIVED", "unit": "m/s", "reason": "No contemporaneous C1 defender segment"},
        },
    }
    assert MODULE.build_rival_state_features([source])["rows"][0]["reliability"]["pairwise_complete"] is False


def test_global_c9_is_stable_and_battle_safe_under_reordered_input():
    rows = [
        {"battle_id": "b2", "year": "2026", "event": "Australian Grand Prix"},
        {"battle_id": "b1", "year": "2026", "event": "Canadian Grand Prix"},
        {"battle_id": "b1", "year": "2026", "event": "Canadian Grand Prix"},
    ]
    first = MODULE.make_split(rows, "battle_id", "kfold:2", 7)
    assert first == MODULE.make_split(list(reversed(rows)), "battle_id", "kfold:2", 7)
    assert len({row["group_key"] for row in first}) == 2


def test_rerun_discovery_is_deterministic_on_fixture_inputs(tmp_path):
    segments, lake = tmp_path / "segments", tmp_path / "lake"
    _write_partition(segments, "australian", [{"year": "2026", "event": "Australian Grand Prix", "session": "Race"}])
    _write_lake(lake, "Australian Grand Prix")
    first = MODULE.discover_partitions(segments, lake, ["2026"], ["British Grand Prix"])
    second = MODULE.discover_partitions(segments, lake, ["2026"], ["British Grand Prix"])
    assert first == second
