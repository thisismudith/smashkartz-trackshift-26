"""Tests for the feature and dataset registries (CP-02).

The registry only has value while it matches what the builders actually write.
So beyond structural validity, these tests check the registry against the real
telemetry_20m Parquet schema -- the drift the checkpoint's own failure table
warns about.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.data.registry import (  # noqa: E402
    AVAILABILITY_SCOPES,
    REQUIRED_DATASET_KEYS,
    REQUIRED_FEATURE_KEYS,
    RegistryError,
    UnregisteredColumns,
    assert_registered,
    dataset,
    feature,
    feature_names,
    live_safe_features,
    load_data_registry,
    load_feature_registry,
    source_gated_features,
    validate_registries,
)

# The seven channels section 11 gates on a documented source.
SOURCE_GATED = {
    "brake_pressure", "steering_angle", "tyre_temperature", "tyre_pressure",
    "brake_temperature", "damage", "fuel_consumption",
}


# --------------------------------------------------------------- structure
def test_registries_load():
    assert load_feature_registry()
    assert load_data_registry()


def test_no_structural_problems():
    problems = validate_registries()
    assert problems == [], "registry problems:\n  " + "\n  ".join(problems)


def test_every_feature_has_every_required_key():
    for name, entry in load_feature_registry().items():
        missing = [k for k in REQUIRED_FEATURE_KEYS if k not in entry]
        assert not missing, f"{name} missing {missing}"


def test_every_dataset_has_every_required_key():
    for name, entry in load_data_registry().items():
        missing = [k for k in REQUIRED_DATASET_KEYS if k not in entry]
        assert not missing, f"{name} missing {missing}"


def test_no_duplicate_feature_names():
    raw = yaml.safe_load((ROOT / "config" / "feature_registry.yaml").read_text(encoding="utf-8-sig"))
    names = [f["name"] for f in raw["features"]]
    assert len(names) == len(set(names))


def test_availability_scopes_are_valid():
    for name, entry in load_feature_registry().items():
        assert entry["availability_scope"] in AVAILABILITY_SCOPES, name


# ------------------------------------------------------------- invariants
def test_live_safe_is_decided_unless_channel_is_unavailable():
    """Null live_safe means nobody decided, which is what the field prevents."""
    for name, entry in load_feature_registry().items():
        if entry["live_safe"] is None:
            assert entry["availability_scope"] == "none", (
                f"{name}: live_safe null but scope {entry['availability_scope']}"
            )


def test_acausal_features_are_never_live_safe():
    """A feature using future information cannot be available at decision time."""
    for name, entry in load_feature_registry().items():
        if entry["causal_status"] == "ACAUSAL":
            assert entry["live_safe"] is not True, f"{name} is ACAUSAL but live_safe"


def test_labels_are_marked_acausal_not_merely_offline():
    """Lap time and classified position are only known after the fact."""
    for name in ("lap_time_s", "race_position", "is_accurate", "lap_deleted"):
        assert feature(name)["causal_status"] == "ACAUSAL", name


def test_all_seven_source_gated_channels_registered_as_unavailable():
    """Pre-registered so nobody silently zero-fills them (section 11)."""
    names = feature_names()
    for channel in SOURCE_GATED:
        assert channel in names, f"{channel} not registered"
        entry = feature(channel)
        assert entry["availability_scope"] == "none", channel
        assert entry["source_gated"] is True, channel
        assert entry["live_safe"] is None, channel


def test_source_gated_helper_matches():
    assert SOURCE_GATED <= source_gated_features()


def test_drs_is_historical_only_and_not_supported_in_2026():
    """The 2026 drs channel is uniformly zero; Overtake state comes from the
    rule engine alone (sections 12, 20, 41)."""
    entry = feature("drs_open")
    assert 2026 not in entry["supported_years"]
    assert "2026" in entry["definition"] or "Overtake" in entry["definition"]


def test_gap_and_position_features_are_race_like_only():
    """There is no car ahead to measure in a flying qualifying lap."""
    for name in ("gap_ahead_m", "driver_ahead_number", "race_position"):
        assert set(feature(name)["allowed_sessions"]) <= {"Sprint", "Race"}, name


def test_live_safe_set_is_non_trivial():
    live = live_safe_features()
    assert "speed_kmh" in live
    assert "lap_time_s" not in live  # known only once the lap is done


# ------------------------------------------------- agreement with real data
def _parquet_columns() -> list[str] | None:
    files = list((ROOT / "data" / "processed" / "telemetry_20m").rglob("*.parquet"))
    if not files:
        return None
    pd = pytest.importorskip("pandas")
    return list(pd.read_parquet(files[0]).columns)


def test_registry_covers_the_real_parquet_schema():
    """The drift this registry exists to prevent.

    Skips when the lake has not been built yet; once CP-04 runs, this becomes
    the binding check.
    """
    columns = _parquet_columns()
    if columns is None:
        pytest.skip("no telemetry_20m Parquet yet; build it in CP-04")
    unregistered = sorted(set(columns) - feature_names())
    assert not unregistered, f"columns written but not registered: {unregistered}"


def test_telemetry_20m_dataset_is_registered_and_built():
    entry = dataset("telemetry_20m")
    assert entry["status"] == "built"
    assert entry["producer"] == "scripts/build_phase2_dataset.py"
    assert "distance_m" in entry["primary_key"]


def test_contracts_owned_by_rishabh_are_registered():
    """C7 and C9 are already delivered and this plan consumes them."""
    assert dataset("race_context")["status"] == "built"
    assert dataset("split_assignments")["status"] == "built"


# ------------------------------------------------------- assert_registered
def test_assert_registered_passes_for_known_columns():
    assert_registered(["speed_kmh", "distance_m", "tyre_compound"], "unit test")


def test_assert_registered_raises_for_unknown_column():
    with pytest.raises(UnregisteredColumns, match="not_a_real_column"):
        assert_registered(["speed_kmh", "not_a_real_column"], "unit test")


def test_assert_registered_lists_every_offender_at_once():
    """One run should fix them all, not surface them one at a time."""
    with pytest.raises(UnregisteredColumns) as exc:
        assert_registered(["nope_one", "nope_two", "nope_three"], "unit test")
    for name in ("nope_one", "nope_two", "nope_three"):
        assert name in str(exc.value)
    assert "3 column(s)" in str(exc.value)


def test_assert_registered_names_the_context():
    with pytest.raises(UnregisteredColumns, match="segments build"):
        assert_registered(["bogus"], "segments build")


def test_assert_registered_accepts_an_empty_iterable():
    assert_registered([], "empty")


def test_assert_registered_works_on_a_dataframe_columns_object():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"speed_kmh": [1.0], "distance_m": [20.0]})
    assert_registered(df.columns, "dataframe")


# --------------------------------------------------------------- lookups
def test_feature_lookup_returns_the_entry():
    assert feature("speed_kmh")["unit"] == "km/h"


def test_unknown_feature_suggests_near_matches():
    with pytest.raises(KeyError, match="Did you mean"):
        feature("speed_kph")


def test_unknown_dataset_raises():
    with pytest.raises(KeyError):
        dataset("no_such_dataset")


# ------------------------------------------------------------- validation
def test_validator_catches_a_bad_entry(tmp_path):
    """The validator has to actually fail on bad input, or the green suite above
    proves nothing."""
    (tmp_path / "feature_registry.yaml").write_text(
        "schema_version: 1\nfeatures:\n"
        "  - name: broken\n"
        "    definition: x\n"
        "    unit: null\n"
        "    source: x\n"
        "    derivation: x\n"
        "    allowed_sessions: []\n"
        "    supported_years: []\n"
        "    live_safe: null\n"          # null with scope 'all' -> undecided
        "    provenance: NOPE\n"          # not a valid provenance
        "    availability_scope: all\n"
        "    source_gated: false\n"
        "    decision_checkpoint: [WRONG]\n"
        "    uncertainty_field: null\n"
        "    causal_status: MAYBE\n"
        "    counterfactual_safe: false\n"
        "    regulation_version: null\n"
        "    regulation_source: null\n"
        "    interaction_group: []\n"
        "    consuming_models: []\n",
        encoding="utf-8",
    )
    (tmp_path / "data_registry.yaml").write_text("schema_version: 1\ndatasets: []\n", encoding="utf-8")
    problems = validate_registries(tmp_path)
    joined = " | ".join(problems)
    assert "provenance" in joined
    assert "live_safe" in joined
    assert "causal_status" in joined
    assert "decision_checkpoint" in joined


def test_duplicate_names_are_rejected_at_load(tmp_path):
    (tmp_path / "feature_registry.yaml").write_text(
        "schema_version: 1\nfeatures:\n  - name: dup\n  - name: dup\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="duplicate"):
        load_feature_registry(tmp_path)


def test_missing_registry_file_is_a_clear_error(tmp_path):
    with pytest.raises(RegistryError, match="CP-02"):
        load_feature_registry(tmp_path)


def test_near_duplicate_names_are_flagged(tmp_path):
    """gap_s and gap_m would silently mean different things under one concept."""
    (tmp_path / "feature_registry.yaml").write_text(
        "schema_version: 1\nfeatures:\n  - name: closing_rate_s\n  - name: closing_rate_m\n",
        encoding="utf-8")
    (tmp_path / "data_registry.yaml").write_text("schema_version: 1\ndatasets: []\n", encoding="utf-8")
    problems = validate_registries(tmp_path)
    assert any("near-duplicate" in p for p in problems)
