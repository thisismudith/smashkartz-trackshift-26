from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.rival.api import (  # noqa: E402
    BENCHMARK_SEED,
    ManifestMismatchError,
    ModelUnavailableError,
    REGRESSION_SEED,
    SyntheticConfig,
    build_battle_sequences,
    c10_prediction_evidence,
    fit_model,
    generate,
    load_model,
    rival_state,
)


def _q(value: float | None, unit: str = "s") -> dict[str, object]:
    return {"value": value, "provenance": "DERIVED", "unit": unit, "reason": None if value is not None else "unavailable"}


def _m08_row(battle: str = "battle_a", segment: int = 1, *, event: str = "Australian Grand Prix", fold: str = "fold_0") -> dict[str, object]:
    return {
        "battle_id": battle,
        "segment_index": segment,
        "event": event,
        "year": "2026",
        "session": "Race",
        "normal_race_model_eligible": True,
        "schema_version": "m08_rival_state_features_v1",
        "c9_split_assignment": {"schema_version": "c9_split_assignments_v1", "group_key": battle, "fold_id": fold},
        "pace_residual_delta_s": _q(-0.1),
        "relative_speed_to_ahead_mps": _q(1.2, "m/s"),
        "gap_rate_ahead_s_per_s": _q(None, "s/s"),
        "braking_intensity_delta": _q(None, "ratio"),
        "tyre_degradation_delta": _q(None, "ratio"),
        "wind_head_component_mps": _q(None, "m/s"),
        "fuel_load_delta_kg_est": _q(None, "kg"),
        "ers_energy_delta_kj_est": _q(None, "kJ"),
    }


def test_c9_assignments_keep_battles_isolated_and_require_every_sequence():
    assignment = {"group_key": "battle_a", "fold_id": "fold_0"}
    sequences = build_battle_sequences([_m08_row(fold="fold_0"), _m08_row(segment=2, fold="fold_0")], assignments={"battle_a": assignment}, require_c9=True)
    assert len(sequences) == 1 and sequences[0]["fold_id"] == "fold_0"
    with pytest.raises(ValueError, match="multiple C9 folds"):
        build_battle_sequences([_m08_row(fold="fold_0"), _m08_row(segment=2, fold="fold_1")], require_c9=True)
    missing = _m08_row()
    missing.pop("c9_split_assignment")
    with pytest.raises(ValueError, match="no C9 assignment"):
        build_battle_sequences([missing], require_c9=True)


def test_british_and_forbidden_future_fields_are_rejected():
    with pytest.raises(ValueError, match="British Grand Prix"):
        build_battle_sequences([_m08_row(event="British Grand Prix")])
    forbidden = _m08_row()
    forbidden["pass_result"] = False
    with pytest.raises(ValueError, match="forbidden/future"):
        build_battle_sequences([forbidden])
    c5 = _m08_row()
    c5["fuel_load_delta_kg_est"] = _q(1.0, "kg")
    with pytest.raises(ValueError, match="C5 placeholder"):
        build_battle_sequences([c5])
    coordinates = _m08_row()
    coordinates["x_m"] = 10.0
    with pytest.raises(ValueError, match="forbidden/future"):
        build_battle_sequences([coordinates])


def test_c10_probabilities_are_normalized_and_fitting_is_deterministic():
    train = generate(SyntheticConfig(seed=REGRESSION_SEED, sequences=8, length=12))
    evaluate = generate(SyntheticConfig(seed=BENCHMARK_SEED, sequences=2, length=12))
    first = fit_model(train, kind="hmm", split_version="c9-test")
    second = fit_model(train, kind="hmm", split_version="c9-test")
    assert first.to_dict() == second.to_dict()
    result = rival_state(evaluate[:6], first)
    assert result["provenance"] == "INFERRED"
    assert result["split_version"] == "c9-test"
    assert abs(sum(result["p"].values()) - 1.0) < 1e-12
    with pytest.raises(ModelUnavailableError):
        rival_state(evaluate[:1])


def test_merged_state_behavior_is_explicit():
    rows = generate(SyntheticConfig(seed=REGRESSION_SEED, sequences=5, length=10, merge_conserving_derating=True))
    model = fit_model(rows, kind="hmm", merge_conserving_derating=True, split_version="c9-test")
    assert model.states == ("CONSERVING_OR_DERATING", "BALANCED", "DEPLOYING")
    assert model.merged_states == [["CONSERVING_OR_DERATING", "CONSERVING", "DERATING"]]
    output = rival_state(rows[:4], model)
    assert set(output["p"]) == set(model.states)


def test_cpu_artifact_load_and_manifest_mismatch(tmp_path: Path):
    rows = generate(SyntheticConfig(seed=REGRESSION_SEED, sequences=4, length=8))
    model = fit_model(rows, kind="hsmm", split_version="c9-test")
    path = tmp_path / "selected_model.json"
    path.write_text(json.dumps(model.to_dict()), encoding="utf-8")
    loaded = load_model(path, expected_split_version="c9-test")
    result = rival_state(rows[:3], loaded)
    assert result["p"]
    with pytest.raises(ManifestMismatchError):
        load_model(path, expected_split_version="wrong-c9")
    manifest = tmp_path / "run_manifest.json"
    manifest.write_text(json.dumps({"schema_version": "c10_rival_artifacts_manifest_v1", "m08_schema_version": "m08_rival_state_features_v1", "c9_split_version": "c9-test"}), encoding="utf-8")
    assert load_model(path, manifest_path=manifest).states == model.states
    manifest.write_text(json.dumps({"schema_version": "wrong", "m08_schema_version": "m08_rival_state_features_v1", "c9_split_version": "c9-test"}), encoding="utf-8")
    with pytest.raises(ManifestMismatchError):
        load_model(path, manifest_path=manifest)


def test_cp08_perturbation_preserves_quantity_feature_contract():
    model = fit_model(generate(SyntheticConfig(seed=REGRESSION_SEED, sequences=4, length=8)), kind="hmm", split_version="c9-test")
    rows = [_m08_row(segment=index) for index in range(1, 8)]
    evidence = c10_prediction_evidence(model, build_battle_sequences(rows, require_c9=True))
    assert len(evidence) == 1
    assert evidence[0]["observation_log_likelihood"] is not None
    assert evidence[0]["posterior"] != evidence[0]["perturbed_posterior"]
