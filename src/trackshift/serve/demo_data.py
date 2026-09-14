"""Small deterministic deployment fixture for the TrackShift service.

This is deliberately a serving-only fixture: it does not read telemetry, load
model artifacts, or claim that its hidden-state and energy values were
observed.  Keep it coherent so the deployed UI tells one HAM-versus-ANT story.
"""
from __future__ import annotations

import math
from typing import Any

EVENT = "british_grand_prix"
BATTLE_ID = "demo_2026_GBR_Race_HAM_ANT"
API_VERSION = "1.0.0"
FIXTURE_VERSION = "deployment-demo-v1"


def versions() -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "git_commit": "deployment-demo",
        "models": {
            "pass": "demo-pass-2026.01", "rival": "demo-rival-2026.01",
            "twin": "demo-twin-2026.01", "rules": "rules-2026-bgp-demo",
            "value": "demo-value-2026.01", "planner": "demo-planner-2026.01",
        },
    }


def quantity(value: float | None, unit: str | None, provenance: str = "SIMULATED", **extra: Any) -> dict[str, Any]:
    return {"value": value, "unit": unit, "provenance": provenance, **extra}


def _segments() -> list[dict[str, Any]]:
    """A smooth 24-point approach from Vale towards the Hangar Straight."""
    result = []
    for index in range(24):
        progress = index / 23
        # Gap contracts while Hamilton gets a clean exit; energy is an estimate.
        gap = round(0.82 - 0.43 * progress + 0.015 * math.sin(index * 0.8), 3)
        energy = round(2.56 - 0.36 * progress, 3)
        p_pass = round(0.30 + 0.38 * progress, 3)
        result.append({
            "segment_id": 18 + index,
            "distance_m": round(3820 + index * 54.0, 1),
            "gap_s": quantity(gap, "s"),
            "energy_mj": quantity(energy, "MJ"),
            "pass": {"p_pass_by_outcome_horizon": quantity(p_pass, None, "INFERRED")},
            "eligibility": {"state": "ARMED" if index >= 15 else "NOT_ARMED", "provenance": "RULE"},
            "rival_state": {
                "p": {"CONSERVING": round(0.16 + 0.03 * progress, 3), "BALANCED": round(0.27 - 0.04 * progress, 3), "DEPLOYING": round(0.45 - 0.12 * progress, 3), "DERATING": round(0.12 + 0.13 * progress, 3)},
                "model_version": "demo-rival-2026.01", "provenance": "INFERRED",
            },
            "provenance": "SIMULATED",
        })
    return result


def response(method: str, path: str, payload: dict[str, Any] | None = None, query: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Return a response body for every frontend-facing service route."""
    payload, query = payload or {}, query or {}
    base = versions()
    if method == "GET" and path == "/api/v1/meta":
        return {**base, "mode": "demo", "stubs": ["deployment-demo"], "synthetic_fixture": FIXTURE_VERSION, "release_ready": False}
    if method == "GET" and path == "/api/v1/validation":
        return {**base, "rival": {"provenance": "SIMULATED", "status": "DEMO"}, "value": {"provenance": "SIMULATED", "status": "DEMO"}, "planner": {"provenance": "SIMULATED", "rule_violations": 0}, "simulator": {"provenance": "SIMULATED", "rule_violations": 0}, "release_ready": False, "reason": "Deterministic deployment fixture; not an evaluation report.", "is_stub": True}
    if method == "GET" and path == "/api/v1/battles":
        return {"battles": [{"battle_id": BATTLE_ID, "year": 2026, "event": EVENT, "session": "Race", "attacker": "HAM", "defender": "ANT", "provenance": "SIMULATED"}], "is_stub": True, "versions": base}
    if method == "GET" and path == f"/api/v1/battles/{BATTLE_ID}/timeline":
        return {"battle_id": BATTLE_ID, "segments": _segments(), "causal_cutoff": {"segment_index": 23, "provenance": "DERIVED"}, "is_stub": True, "versions": base}
    if method == "GET" and path == f"/api/v1/track/{EVENT}":
        centreline = [{"distance_m": float(i * 240), "x_m": round(210 * math.cos(i / 6), 2), "y_m": round(150 * math.sin(i / 6), 2), "provenance": "DERIVED"} for i in range(25)]
        return {"event": EVENT, "track": "Silverstone", "lap_length_m": quantity(5891.0, "m", "RULE"), "centreline": centreline, "segments": _segments(), "segment_count": 24, "lines": [{"kind": "DETECTION", "zone": "Hangar", "distance_m": 4390.0, "provenance": "RULE", "source": "deployment demo configuration"}, {"kind": "ACTIVATION", "zone": "Hangar", "distance_m": 4635.0, "provenance": "RULE", "source": "deployment demo configuration"}], "provenance": "SIMULATED", "is_stub": True, "versions": base}
    if method == "GET" and path == f"/api/v1/rules/{EVENT}":
        line = lambda value: {"value": value, "value_source": "UNVERIFIED", "source": "deployment demo configuration", "note": "Demo-only configuration; not an FIA claim."}
        return {"event": EVENT, "year": 2026, "config_version": "rules-2026-bgp-demo", "overtake": {"enabled": True, "detection_gap_s": line(1.0), "zones": [{"zone": "Hangar", "name": "Hangar Straight", "detection_line_m": line(4390.0), "activation_line_m": line(4635.0), "zone_end_m": line(5210.0)}]}, "power_envelope": {"normal": {"breakpoints_kmh": [0, 300, 340], "max_power_kw": [350, 350, 0], "value_source": "UNVERIFIED", "source": "deployment demo configuration"}, "override": {"breakpoints_kmh": [0, 300, 340], "max_power_kw": [350, 450, 0], "value_source": "UNVERIFIED", "source": "deployment demo configuration"}}, "unverified_keys": ["demo deployment fixture"], "all_values_verified": False, "is_stub": True, "versions": base}
    if method == "GET" and path == f"/api/v1/rules/{EVENT}/power_envelope":
        speeds = list(range(0, 361, 20))
        cap = lambda speed, override: max(0.0, (450.0 if override and speed >= 300 else 350.0) * (1.0 if speed <= 340 else max(0.0, (360 - speed) / 20)))
        return {"event": EVENT, "curves": {mode: [{"speed_kmh": speed, "max_power_kw": cap(speed, mode == "override"), "provenance": "RULE"} for speed in speeds] for mode in ("normal", "override")}, "provenance": "RULE", "verified": False, "unverified_keys": ["demo deployment fixture"], "rule_configuration_version": "rules-2026-bgp-demo", "is_stub": True, "versions": base}
    if method == "GET" and path == "/api/v1/simulate/policies":
        return {"policies": [{"name": "conservative", "description": "Harvest before the Hangar Straight."}, {"name": "balanced", "description": "Preserve energy for activation."}, {"name": "aggressive", "description": "Deploy on corner exit."}], "provenance": "SIMULATED", "model_version": "demo-simulator-2026.01", "versions": base}
    if method == "POST" and path == "/api/v1/pass/predict":
        return {"p_pass_by_outcome_horizon": quantity(0.68, None, "INFERRED", model="demo-pass-2026.01"), "checkpoint": "ACTIVATION", "decision_checkpoint": "ACTIVATION", "calibration": "deployment-demo", "outcome_horizon": "zone_exit_v1", "features_supplied": ["gap_at_checkpoint", "relative_speed_kmh"], "features_missing": [], "is_stub": True, "versions": base}
    if method == "POST" and path == "/api/v1/rival/state":
        return {"p": {"CONSERVING": 0.19, "BALANCED": 0.23, "DEPLOYING": 0.41, "DERATING": 0.17}, "model_version": "demo-rival-2026.01", "provenance": "INFERRED", "is_stub": True, "versions": base}
    if method == "POST" and path == "/api/v1/rules/eligibility":
        return {"p_eligible": quantity(0.74, None), "eligibility_margin_s": quantity(0.18, "s"), "margin_s": quantity(0.18, "s"), "gap_s": quantity(0.82, "s", "DERIVED"), "terms_used": ["gap", "closing_rate", "time_to_detection_line"], "is_stub": True, "versions": base}
    if method == "POST" and path == "/api/v1/rules/legal_actions":
        actions = [{"deploy_level": level, "lift_amount": 0.0, "label": label, "cap_kw": 350.0, "applicable_mode": "normal"} for level, label in ((0.0, "HARVEST"), (0.5, "BALANCED"), (0.75, "DETECTION_PUSH"), (1.0, "ATTACK_DEPLOY"))]
        return {"legal_actions": actions, "excluded_actions": [], "rule_violations": 0, "provenance": "RULE", "is_stub": True, "versions": base}
    if method == "GET" and path == f"/api/v1/value/{EVENT}/shadow_price":
        profile = [{"segment_id": item["segment_id"], "lambda_s_per_kj": quantity(round(0.00002 + i * 0.0000018, 7), "s/kJ", "SIMULATED")} for i, item in enumerate(_segments())]
        return {"event": EVENT, "energy_kj": float(query.get("energy_kj", 2200)), "gap_s": float(query.get("time_gap_s", 0.82)), "eligibility": query.get("eligibility", "NOT_ARMED"), "profile": profile, "lambda_s_per_kj": quantity(0.000055, "s/kJ"), "provenance": "SIMULATED", "is_stub": True, "versions": base}
    if method == "POST" and path == "/api/v1/twin/segment_time":
        return {"t_s": {"mean": 2.18, "low": 2.12, "high": 2.25, "unit": "s", "provenance": "SIMULATED"}, "delta_e_kj": {"mean": 84.0, "low": 75.0, "high": 93.0, "unit": "kJ", "provenance": "SIMULATED"}, "harvested_e_kj": {"mean": 0.0, "low": 0.0, "high": 0.0, "unit": "kJ", "provenance": "SIMULATED"}, "is_stub": True, "versions": base}
    if method == "POST" and path == "/api/v1/twin/energy_state":
        return {"energy_mj": {"mean": 2.2, "low": 1.95, "high": 2.45, "unit": "MJ", "provenance": "SIMULATED"}, "ers_deployment_kw": {"mean": 310.0, "low": 280.0, "high": 340.0, "unit": "kW", "provenance": "SIMULATED"}, "ers_harvest_kw": {"mean": 18.0, "low": 12.0, "high": 25.0, "unit": "kW", "provenance": "SIMULATED"}, "is_stub": True, "versions": base}
    if method == "POST" and path == "/api/v1/plan":
        action = {"deploy_level": 0.75, "lift_amount": 0.0, "label": "DETECTION_PUSH", "cap_kw": 350.0, "applicable_mode": "normal", "delivered_power_kw": 262.5}
        return {"status": "OK", "recommended_action": action, "legal_actions": [action, {**action, "deploy_level": 0.5, "label": "BALANCED"}], "alternatives": [{"action": {**action, "deploy_level": 0.5, "label": "BALANCED"}, "expected_value": 0.61, "regret": 0.04}], "decision": {"dominant_mechanism": "Close the gap before the Hangar activation line", "primary_constraint": "Preserve enough simulated energy for the activation window", "decision_stability": 0.81}, "expected_value": 0.65, "rule_violations": 0, "rule_configuration_version": "rules-2026-bgp-demo", "provenance": "SIMULATED", "is_stub": True, "versions": base}
    if method == "POST" and path == "/api/v1/simulate":
        return {"status": "OK", "summary": {"p_ahead_at_horizon": 0.68, "mean_final_energy_mj": 1.72, "rule_violations": 0, "n_episodes": 48, "seed": 2026, "cvar_p_ahead": 0.49}, "assumptions": ["Deterministic deployment demo fixture.", "Energy and rival intent are simulated, not measured."], "provenance": "SIMULATED", "is_stub": True, "versions": base}
    if method == "GET" and path == "/internal/config/variables":
        return {"categories": [], "is_stub": True}
    if method == "GET" and path == "/internal/config/events":
        return {"events": [{"year": 2026, "event": EVENT, "label": "British Grand Prix"}], "is_stub": True}
    return None
