import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.data.splits import (
    SPLIT_SCHEMA_VERSION,
    assert_training_or_calibration_eligible,
    build_split_manifest,
    make_split,
)


def sample_rows():
    return [
        {"battle_id": "b1", "event": "Bahrain Grand Prix", "track": "Bahrain", "year": 2022},
        {"battle_id": "b1", "event": "Bahrain Grand Prix", "track": "Bahrain", "year": 2022},
        {"battle_id": "b2", "event": "Monaco Grand Prix", "track": "Monaco", "year": 2023},
        {"battle_id": "b3", "event": "British Grand Prix", "track": "Silverstone", "year": 2026},
        {"battle_id": "b4", "event": "Italian Grand Prix", "track": "Monza", "year": 2024},
    ]


def raises(callable_):
    try:
        callable_()
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_kfold_is_deterministic_and_assigns_each_battle_once():
    first = make_split(sample_rows(), "battle_id", "kfold:2", seed=9)
    second = make_split(list(reversed(sample_rows())), "battle_id", "kfold:2", seed=9)
    assert first == second
    assert [row["group_key"] for row in first] == ["b1", "b2", "b3", "b4"]
    assert len({row["group_key"] for row in first}) == len(first)
    british = next(row for row in first if row["group_key"] == "b3")
    assert british["split_role"] == "HOLDOUT"
    assert british["fold_id"] == "british_grand_prix_holdout"


def test_event_track_and_year_units_are_supported():
    for unit in ("event", "track", "year"):
        assignments = make_split(sample_rows(), unit, "kfold:2", seed=3)
        assert all(row["split_unit"] == unit for row in assignments)


def test_battle_id_cannot_cross_split_groups():
    rows = sample_rows()[:2]
    rows[1]["event"] = "Monaco Grand Prix"
    raises(lambda: make_split(rows, "event", "kfold:2", seed=3))


def test_leave_one_event_out_and_year_forward_are_group_stable():
    loo = make_split(sample_rows(), "battle_id", "leave_one_event_out", seed=1)
    b2 = next(row for row in loo if row["group_key"] == "b2")
    assert b2["fold_id"] == "event_monaco_grand_prix"

    forward = make_split(sample_rows(), "battle_id", "year_forward", seed=1)
    b4 = next(row for row in forward if row["group_key"] == "b4")
    assert b4["fold_id"] == "year_forward_2024"


def test_british_grand_prix_is_rejected_from_training_and_calibration():
    british = [row for row in sample_rows() if row["event"] == "British Grand Prix"]
    raises(lambda: assert_training_or_calibration_eligible(british, "training"))
    raises(lambda: assert_training_or_calibration_eligible(british, "calibration"))
    assert_training_or_calibration_eligible(sample_rows()[:2], "training")


def test_manifest_is_versioned_and_builder_persists_outputs(tmp_path):
    assignments = make_split(sample_rows(), "battle_id", "kfold:2", seed=4)
    manifest = build_split_manifest(assignments, "battle_id", "kfold:2", 4, len(sample_rows()))
    assert manifest["schema_version"] == SPLIT_SCHEMA_VERSION
    assert manifest["assignment_version"].startswith(f"{SPLIT_SCHEMA_VERSION}:")

    source = tmp_path / "rows.jsonl"
    source.write_text("".join(json.dumps(row) + "\n" for row in sample_rows()), encoding="utf-8")
    output = tmp_path / "splits"
    script = Path(__file__).resolve().parents[1] / "scripts" / "data" / "build_splits.py"
    subprocess.run(
        [sys.executable, str(script), "--input", str(source), "--output-dir", str(output),
         "--unit", "battle_id", "--design", "kfold:2", "--seed", "4"],
        check=True,
    )
    persisted = [json.loads(line) for line in (output / "split_assignments.jsonl").read_text(encoding="utf-8").splitlines()]
    assert persisted == assignments
    assert json.loads((output / "split_manifest.json").read_text(encoding="utf-8"))["assignment_sha256"] == manifest["assignment_sha256"]
