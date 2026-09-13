"""Deterministic synthetic fixture used when real service artifacts are absent.

This module is intentionally explicit: its outputs are development evidence,
not observed race data, and its energy/rival values never enter the final-mode
path.
"""
from __future__ import annotations

from typing import Any, Mapping

from trackshift.rival.api import (
    SyntheticConfig,
    build_battle_sequences,
    c10_prediction_evidence,
    evaluate_era_strategies,
    fit_model,
    generate,
    rival_state,
)
from trackshift.rules.api import load_event_rules, max_electrical_power_kw

SYNTHETIC_FIXTURE_VERSION = "trackshift-synthetic-closure-v1"
EVENT = "british_grand_prix"
BATTLE_ID = "synthetic_2026_GBR_Race_HAM_ANT"
RULE_VERSION = "rules-2026-common-v2-fia-iss08-iss20"


def synthetic_segments() -> list[dict[str, Any]]:
    # Keep the route fixture small: the real DP grid is intentionally broad,
    # so a two-segment fixture gives fast deterministic service/replay tests
    # without replacing the production action/value code path.
    speeds = (275.0, 320.0)
    kinds = ("STRAIGHT", "BRAKING")
    return [
        {
            "segment_id": index + 1,
            "segment_index": index,
            "lap": 31,
            "distance_m": float(index * 180),
            "speed_kmh": speed,
            "kind": kind,
            "segment_duration_s": 1.8 if kind == "STRAIGHT" else 2.2,
            "provenance": "SIMULATED",
        }
        for index, (speed, kind) in enumerate(zip(speeds, kinds))
    ]


def synthetic_state() -> dict[str, Any]:
    return {
        "ref": {
            "year": 2026,
            "event": EVENT,
            "session": "Race",
            "lap": 31,
            "segment_id": 1,
            "distance_m": 0.0,
            "rule_configuration_version": RULE_VERSION,
        },
        "energy": {
            "ers_soc_est_mj": {
                "value": 2.4,
                "unit": "MJ",
                "provenance": "SIMULATED",
                "reason": "synthetic fixture; not measured car telemetry",
            }
        },
        "gap": {
            "time_gap_s": {"value": 0.72, "unit": "s", "provenance": "SIMULATED"},
            "relative_speed_mps": {"value": 1.2, "unit": "m/s", "provenance": "SIMULATED"},
            "gap_rate_s_per_s": {"value": -0.04, "unit": "s/s", "provenance": "SIMULATED"},
        },
        "overtake_state": {"state": "NOT_ARMED", "armed": False, "provenance": "RULE"},
        "race_control": {
            "race_control_state": "GREEN",
            "pit_state": "ON_TRACK",
            "normal_race_model_eligible": True,
            "provenance": "SIMULATED",
        },
        "power_envelope": {"requested_mode": "auto", "provenance": "RULE"},
        "rival_state": {"state": "UNKNOWN", "provenance": "INFERRED"},
        "uncertainty": {"level": "synthetic_fixture", "provenance": "SIMULATED"},
        "speed_kmh": 240.0,
    }


def synthetic_rules() -> dict[str, Any]:
    return load_event_rules(EVENT)


def _value(state: Mapping[str, Any], path: tuple[str, ...], default: float) -> float:
    current: Any = state
    for key in path:
        if not isinstance(current, Mapping):
            return default
        current = current.get(key)
    if isinstance(current, Mapping):
        current = current.get("value", current.get("mean", default))
    try:
        return float(current)
    except (TypeError, ValueError):
        return default


def synthetic_transition(
    state: Mapping[str, Any], action: Mapping[str, Any], segment: Mapping[str, Any], *extra: Any,
) -> dict[str, Any]:
    """Causal synthetic C5 transition using the shared C3 envelope evaluator."""
    rules = synthetic_rules()
    speed = float(segment.get("speed_kmh", state.get("speed_kmh", 0.0)))
    armed = bool((state.get("overtake_state") or {}).get("armed", False))
    mode = "override" if armed else "normal"
    cap_kw = max_electrical_power_kw(speed, mode, rules)
    duration_s = float(segment.get("segment_duration_s", 2.0))
    deploy = max(0.0, min(1.0, float(action.get("deploy_level", 0.0))))
    lift = max(0.0, min(1.0, float(action.get("lift_amount", 0.0))))
    deployed_mj = deploy * cap_kw * duration_s / 1000.0
    harvested_mj = lift * 0.035
    energy = max(0.0, min(4.0, _value(state, ("energy", "ers_soc_est_mj"), 2.4) - deployed_mj + harvested_mj))
    gap = _value(state, ("gap", "time_gap_s"), 0.72) - deploy * 0.045 + lift * 0.01
    time_delta = duration_s - deploy * 0.025 + lift * 0.012
    return {
        "energy_mj": energy,
        "gap_s": gap,
        "eligibility": int(bool((state.get("overtake_state") or {}).get("armed", False))),
        "time_delta_s": time_delta,
        "deployed_energy_mj": deployed_mj,
        "harvested_energy_mj": harvested_mj,
        "provenance": "SIMULATED",
    }


def synthetic_pass(
    state: Mapping[str, Any], ours: Mapping[str, Any], rival: Mapping[str, Any],
    segment: Mapping[str, Any], environment: Mapping[str, Any],
) -> dict[str, Any]:
    gap = _value(state, ("gap", "time_gap_s"), 0.72)
    ours_level = float(ours.get("deploy_level", 0.0))
    rival_level = float(rival.get("deploy_level", 0.0))
    passed = gap < 0.42 and ours_level >= 0.5
    return {"passed": passed, "repassed": bool(passed and rival_level > ours_level and gap > 0.2), "provenance": "SIMULATED"}


def synthetic_rival_rows(*, year: int = 2026, event: str = "Australian Grand Prix") -> list[dict[str, Any]]:
    rows = generate(SyntheticConfig(seed=2903, sequences=16, length=24))
    for row in rows:
        row["year"] = year
        row["event"] = event
        row["battle_id"] = row["sequence_id"]
        row["schema_version"] = "m08_rival_state_features_v1"
        row["normal_race_model_eligible"] = True
        row["rule_configuration_version"] = RULE_VERSION if year == 2026 else "historical_drs_era"
    return rows


def synthetic_rival_model():
    return fit_model(
        synthetic_rival_rows(year=2025),
        kind="hsmm",
        split_version="synthetic-c9-v1",
    )


def synthetic_era_report() -> dict[str, Any]:
    model = synthetic_rival_model()
    current = synthetic_rival_rows(year=2026)
    historical = synthetic_rival_rows(year=2025)
    current_sequences = build_battle_sequences(current)
    evidence = c10_prediction_evidence(model, current_sequences)
    report = evaluate_era_strategies(
        current,
        split_version="synthetic-c9-v1",
        rule_configuration_version=RULE_VERSION,
        historical_rows=historical,
        materialisation={"status": "READY", "synthetic": True},
        prediction_evidence={strategy: evidence for strategy in (
            "2026_only", "historical_pretrain_2026_recalibration", "era_feature",
            "domain_weighting", "separate_models",
        )},
    )
    report["provenance"] = "SIMULATED"
    report["synthetic_fixture"] = SYNTHETIC_FIXTURE_VERSION
    report["real_historical_evidence"] = False
    return report


def synthetic_validation() -> dict[str, Any]:
    model = synthetic_rival_model()
    rows = synthetic_rival_rows()
    from trackshift.rival.model import synthetic_recovery

    return {
        "rival": {
            "model_version": model.model_version,
            "n_synthetic_rows": len(rows),
            "synthetic_state_recovery": synthetic_recovery(model, rows),
            "provenance": "SIMULATED",
            "real_state_calibration": "NOT_CLAIMED",
        },
        "value": {"model_version": "m22_dp_development_v2", "status": "SYNTHETIC_DEVELOPMENT", "provenance": "SIMULATED", "n": len(synthetic_segments())},
        "planner": {"model_version": "m24_beam_development_v1", "status": "SYNTHETIC_DEVELOPMENT", "provenance": "SIMULATED", "n": len(synthetic_segments()), "rule_violations": 0},
        "simulator": {"model_version": "m26_simulator_development_v1", "status": "SYNTHETIC_DEVELOPMENT", "provenance": "SIMULATED", "n_episodes": 8, "rule_violations": 0},
        "release_ready": False,
        "reason": "Synthetic development evidence is not a real-race calibration or final release claim",
    }


__all__ = [
    "SYNTHETIC_FIXTURE_VERSION", "EVENT", "BATTLE_ID", "RULE_VERSION",
    "synthetic_segments", "synthetic_state", "synthetic_rules", "synthetic_transition",
    "synthetic_pass", "synthetic_rival_rows", "synthetic_rival_model", "synthetic_era_report",
    "synthetic_validation",
]
