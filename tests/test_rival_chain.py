from __future__ import annotations
import json
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
    try: evaluate_era_strategies([{"year":2026,"event":"British Grand Prix","training":True}],split_version="c9",rule_configuration_version="r")
    except ValueError: pass
    else: raise AssertionError("British GP training accepted")
