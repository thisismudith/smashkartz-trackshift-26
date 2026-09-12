from __future__ import annotations
import json
from math import log
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trackshift.features.api import build_rival_state_features
from trackshift.rival.api import BENCHMARK_SEED, REGRESSION_SEED, SyntheticConfig, benchmark, evaluate_era_strategies, fit_centroid, generate, rival_state

def _row(**extra):
    return {"battle_id":"b1","segment_index":1,"year":2026,"event":"Australian Grand Prix","session":"Race","lap":2,"segment_id":3,"normal_race_model_eligible":True,"relative_speed_to_ahead_mps":1.0,"gap_rate_ahead_s_per_s":-0.1,"baseline_residual_delta_s":-0.2, **extra}

def test_m08_gates_ineligible_and_preserves_c5_nulls_and_json():
    result=build_rival_state_features([_row(),_row(battle_id="b2",normal_race_model_eligible=False)],split_reference={"version":"c9"})
    assert len(result["rows"]) == 1 and result["excluded_rows_by_reason"] == {"NOT_NORMAL_RACE_ELIGIBLE":1}
    fuel=result["rows"][0]["fuel_load_delta_kg_est"]
    assert fuel["value"] is None and "UNAVAILABLE_C5" in fuel["reason"]
    json.dumps(result)

def test_m08_refuses_non_live_fields_and_has_causal_cutoff():
    try: build_rival_state_features([_row(pass_result=True)])
    except ValueError as exc: assert "forbidden" in str(exc)
    else: raise AssertionError("outcome leaked")
    assert build_rival_state_features([_row()])["rows"][0]["causal_cutoff"]["segment_id"] == 3

def test_synthetic_is_seeded_non_degenerate_and_recoverable():
    rows=generate(SyntheticConfig(seed=BENCHMARK_SEED,sequences=12,length=16))
    assert rows == generate(SyntheticConfig(seed=BENCHMARK_SEED,sequences=12,length=16))
    assert len({r["hidden_state"] for r in rows}) == 4
    report=benchmark(generate(SyntheticConfig(seed=REGRESSION_SEED,sequences=12,length=16)), rows)
    assert report["synthetic_recovery"] > report["chance"] and report["cpu_inference_verified"]
    p=rival_state(rows[:4],fit_centroid(rows))["p"]
    assert abs(sum(p.values())-1) < 1e-12

def test_era_harness_excludes_british_training_and_keeps_2026_only_without_evidence():
    report=evaluate_era_strategies([{"year":2025,"event":"Italian Grand Prix"},{"year":2026,"event":"Australian Grand Prix"}],split_version="c9",rule_configuration_version="rules-v1")
    assert report["selected"] == "2026_only" and report["held_out_2026_n"] == 1
    assert report["metrics"]["2026_only"]["mean_nll"] is None
    assert "unavailable" in report["metrics"]["2026_only"]["reason"]
    assert all(report["metrics"][name]["status"] == "BLOCKED" for name in report["strategies"][1:])
    try: evaluate_era_strategies([{"year":2026,"event":"British Grand Prix","training":True}],split_version="c9",rule_configuration_version="r")
    except ValueError: pass
    else: raise AssertionError("British GP training accepted")


def test_cp08_metrics_use_actual_c10_probability_evidence_and_causal_stability():
    evidence = [{
        "event": "Australian Grand Prix", "year": 2026, "model_version": "m09-test",
        "rule_configuration_version": "rules-2026-v1", "observation_log_likelihood": log(0.8),
        "posterior": {"CONSERVING": 0.7, "BALANCED": 0.3},
        "perturbed_posterior": {"CONSERVING": 0.65, "BALANCED": 0.35},
        "perturbation": {"kind": "causal_feature_delta", "field": "pace_residual_delta_s", "delta": 0.05},
    }, {
        "event": "Japanese Grand Prix", "year": 2026, "model_version": "m09-test",
        "rule_configuration_version": "rules-2026-v1", "next_observation_probability": 0.5,
        "posterior": {"CONSERVING": 0.2, "BALANCED": 0.8},
        "perturbed_posterior": {"CONSERVING": 0.25, "BALANCED": 0.75},
    }]
    report = evaluate_era_strategies(
        [{"year": 2026, "event": "Australian Grand Prix", "rule_configuration_version": "rules-2026-v1"}],
        split_version="c9:test", rule_configuration_version="rules-2026-v1",
        prediction_evidence={"2026_only": evidence},
    )
    metrics = report["metrics"]["2026_only"]
    assert metrics["n"] == 2 and metrics["mean_nll"] == pytest.approx(-0.5 * (log(0.8) + log(0.5)))
    assert metrics["stability"] == pytest.approx(0.05)
    assert metrics["event_coverage"] == ["Australian Grand Prix", "Japanese Grand Prix"]
    assert metrics["year_coverage"] == [2026] and metrics["c9_split_reference"] == "c9:test"
    assert metrics["model_version"] == ["m09-test"]
    assert metrics["calibration"].startswith("UNAVAILABLE")
    assert report["status"] == "BLOCKED"  # historical strategies remain blocked


def test_cp08_rejects_british_evidence_and_keeps_drs_separate():
    with pytest.raises(ValueError, match="British"):
        evaluate_era_strategies(
            [{"year": 2026, "event": "British Grand Prix"}],
            split_version="c9", rule_configuration_version="r",
            prediction_evidence={"2026_only": [{"event": "British Grand Prix", "year": 2026}]},
        )
    report = evaluate_era_strategies(
        [{"year": 2026, "event": "Australian Grand Prix", "historical_drs_eligible": True, "overtake_eligible": None}],
        historical_rows=[{"year": 2025, "event": "Australian Grand Prix", "historical_drs_eligible": True, "overtake_eligible": True}],
        split_version="c9", rule_configuration_version="r",
    )
    assert report["historical_drs_is_not_2026_overtake"]
    assert not report["historical_drs_fields_in_2026_overtake"] and not report["historical_rows_have_2026_overtake_state"]
    assert report["historical_drs_fields_removed_from_2026"] and report["historical_2026_state_fields_removed"]
