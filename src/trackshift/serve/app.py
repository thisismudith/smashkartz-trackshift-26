"""API.md-compatible synthetic development service.

The app deliberately uses the same model functions as the planner and replay
builder. It is useful for UI integration and checkpoint evidence, but it does
not promote synthetic values to observed telemetry or final release evidence.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException

from trackshift.data.registry import FeatureBoundaryError, assert_final_feature_boundary, validate_feature_admission
from trackshift.planner.api import generate_baseline_plans, plan
from trackshift.rival.api import rival_state
from trackshift.rules import api as rules_api
from trackshift.rules.config import RuleConfigError
from trackshift.rules.eligibility import eligibility_margin, project_gap_at_line
from trackshift.sim.api import policy_registry, simulate
from trackshift.value.api import DPConfig, shadow_price
from trackshift.value.counterattack import evaluate_counterattack

from .fixture import (
    EVENT,
    BATTLE_ID,
    RULE_VERSION,
    SYNTHETIC_FIXTURE_VERSION,
    synthetic_era_report,
    synthetic_pass,
    synthetic_rival_model,
    synthetic_rival_rows,
    synthetic_rules,
    synthetic_segments,
    synthetic_state,
    synthetic_transition,
    synthetic_validation,
)

API_VERSION = "1.0.0"


def _versions() -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "git_commit": "synthetic-development",
        "models": {
            "rival": "m09_rival_benchmark_v2",
            "value": "m22_dp_development_v2",
            "planner": "m24_beam_development_v1",
            "simulator": "m26_simulator_development_v1",
            "policies": "m27_rival_policies_v1",
            "rules": RULE_VERSION,
        },
    }


def _clean_rules(rules: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in rules.items() if not str(key).startswith("_")}


def _year(payload: Mapping[str, Any]) -> int:
    nested = payload.get("state") if isinstance(payload.get("state"), Mapping) else {}
    ref = nested.get("ref") if isinstance(nested.get("ref"), Mapping) else {}
    value = payload.get("year", ref.get("year", nested.get("year", 2026)))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 2026


def _guard_payload(payload: Mapping[str, Any], context: str, *, final_mode: bool = False) -> None:
    try:
        validate_feature_admission(
            payload, year=_year(payload), consumer=context, mode="final" if final_mode else "development", provenance=payload,
        )
        if final_mode:
            assert_final_feature_boundary(payload, context, year=_year(payload))
    except FeatureBoundaryError as exc:
        raise HTTPException(status_code=422, detail={"code": "FEATURE_SCHEMA_MISMATCH", "message": str(exc)}) from exc


def _rules(event: str) -> dict[str, Any]:
    try:
        return rules_api.load_event_rules(event)
    except RuleConfigError as exc:
        raise HTTPException(status_code=404, detail={"code": "UNKNOWN_EVENT", "message": str(exc)}) from exc


def _state(payload: Mapping[str, Any]) -> dict[str, Any]:
    supplied = payload.get("state")
    return copy.deepcopy(supplied) if isinstance(supplied, Mapping) else synthetic_state()


def _segments(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    supplied = payload.get("segments")
    if isinstance(supplied, list) and supplied and all(isinstance(item, Mapping) for item in supplied):
        return [dict(item) for item in supplied]
    return synthetic_segments()


def create_app(*, mode: str = "service", final_mode: bool = False) -> FastAPI:
    """Create the deterministic development service.

    ``final_mode=True`` is intentionally rejected at startup until official
    rules and accepted C4/C5 callbacks exist; this is a hard release boundary.
    """
    if final_mode:
        # This raises on unresolved Detection Gap/deployment/store inputs and
        # prevents a route from quietly falling back to the synthetic fixture.
        rules_api.load_event_rules(EVENT, final_mode=True)
    app = FastAPI(title="TrackShift", version=API_VERSION)
    app.state.mode = mode
    app.state.final_mode = final_mode
    app.state.synthetic = True
    app.state.fixture_version = SYNTHETIC_FIXTURE_VERSION
    app.state.rival_model = synthetic_rival_model()

    @app.get("/api/v1/meta")
    def meta() -> dict[str, Any]:
        return {**_versions(), "mode": mode, "stubs": [], "synthetic_fixture": SYNTHETIC_FIXTURE_VERSION, "release_ready": False}

    @app.get("/api/v1/validation")
    def validation() -> dict[str, Any]:
        return {**_versions(), **synthetic_validation(), "era_evaluation": synthetic_era_report()}

    @app.get("/api/v1/track/{event}")
    def track(event: str) -> dict[str, Any]:
        rules = _rules(event)
        points = [{"distance_m": float(i * 180), "x_m": float(i * 10), "y_m": float((i % 2) * 8), "provenance": "SIMULATED"} for i in range(len(synthetic_segments()) + 1)]
        lines: list[dict[str, Any]] = []
        for zone in (rules.get("overtake") or {}).get("zones", []):
            for field, kind in (("detection_line_m", "DETECTION"), ("activation_line_m", "ACTIVATION")):
                node = zone.get(field) if isinstance(zone, Mapping) else None
                if isinstance(node, Mapping) and node.get("value") is not None:
                    lines.append({"kind": kind, "zone": zone.get("zone"), "distance_m": node["value"], "provenance": node.get("value_source"), "source": node.get("source")})
        return {"event": event, "track": "silverstone", "lap_length_m": {"value": 5811.0, "unit": "m", "provenance": "SIMULATED"}, "centreline": points, "segments": synthetic_segments(), "lines": lines, "provenance": "SIMULATED", "versions": _versions()}

    @app.get("/api/v1/rules/{event}/power_envelope")
    def power_envelope(event: str, mode: str = "both", step_kmh: float = 5.0) -> dict[str, Any]:
        rules = _rules(event)
        modes = ("normal", "override") if mode == "both" else (mode,)
        if any(item not in {"normal", "override"} for item in modes) or step_kmh <= 0:
            raise HTTPException(status_code=422, detail={"code": "RULE_KEY_MISSING", "message": "mode and step_kmh are invalid"})
        curves = {}
        for selected in modes:
            grid, table = rules_api.envelope_table(rules, selected, max_speed_kmh=360, step_kmh=step_kmh)
            curves[selected] = [{"speed_kmh": float(speed), "max_power_kw": float(power), "provenance": "RULE"} for speed, power in zip(grid, table)]
        return {"event": event, "curves": curves, "provenance": "RULE", "rule_configuration_version": RULE_VERSION, "versions": _versions()}

    @app.get("/api/v1/rules/{event}")
    def rules(event: str) -> dict[str, Any]:
        return {"event": event, "year": 2026, "config_version": RULE_VERSION, **_clean_rules(_rules(event)), "versions": _versions()}

    @app.get("/api/v1/battles")
    def battles(year: int | None = None, event: str | None = None, session: str | None = None) -> dict[str, Any]:
        if event and event != EVENT:
            return {"battles": [], "versions": _versions()}
        return {"battles": [{"battle_id": BATTLE_ID, "year": 2026, "event": EVENT, "session": "Race", "attacker": "HAM", "defender": "ANT", "provenance": "SIMULATED"}], "versions": _versions()}

    @app.post("/api/v1/rival/state")
    def rival(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "C10 API")
        rows = payload.get("segments") if isinstance(payload.get("segments"), list) else synthetic_rival_rows()[:8]
        return {**rival_state(rows, app.state.rival_model), "synthetic_fixture": SYNTHETIC_FIXTURE_VERSION}

    @app.get("/api/v1/battles/{battle_id}/timeline")
    def timeline(battle_id: str) -> dict[str, Any]:
        if battle_id != BATTLE_ID:
            raise HTTPException(status_code=404, detail={"code": "UNKNOWN_BATTLE", "message": battle_id})
        rows = synthetic_rival_rows()[: len(synthetic_segments())]
        return {"battle_id": battle_id, "segments": [{"segment_id": segment["segment_id"], "gap_s": {"value": 0.72 - i * 0.04, "unit": "s", "provenance": "SIMULATED"}, "energy_mj": {"value": 2.4 - i * 0.08, "unit": "MJ", "provenance": "SIMULATED"}, "rival_state": rival_state(rows[: i + 1], app.state.rival_model), "provenance": "SIMULATED"} for i, segment in enumerate(synthetic_segments())], "causal_cutoff": {"segment_index": len(rows) - 1, "provenance": "DERIVED"}, "versions": _versions()}

    @app.post("/api/v1/rules/legal_actions")
    def legal_actions(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "C3 API", final_mode=bool(app.state.final_mode))
        result = rules_api.legal_actions(_state(payload), _rules(str(payload.get("event", EVENT))), final_mode=bool(app.state.final_mode))
        return {**result, "versions": _versions()}

    @app.post("/api/v1/rules/eligibility")
    def eligibility(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "C6 API", final_mode=bool(app.state.final_mode))
        gap = float(payload.get("gap_s", 0.72))
        rate = float(payload.get("closing_rate_s_per_s", 0.04))
        projection = project_gap_at_line(gap, rate, float(payload.get("time_to_line_s", 2.0)), [rate, rate * 0.8, rate * 1.2])
        return {"p_eligible": {"value": projection.p_eligible, "provenance": "SIMULATED"}, "margin_s": {"value": projection.eligibility_margin_s, "unit": "s", "provenance": "SIMULATED"}, "terms_used": list(projection.terms_used), "versions": _versions()}

    @app.post("/api/v1/pass/predict")
    def pass_predict(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "C4 API", final_mode=bool(app.state.final_mode))
        gap = float(payload.get("gap_s", 0.72))
        deploy = float(payload.get("deploy_level", 0.5))
        probability = 1.0 / (1.0 + __import__("math").exp(4.0 * (gap - 0.35) - deploy))
        return {"p_pass_by_outcome_horizon": {"value": probability, "provenance": "SIMULATED"}, "checkpoint": payload.get("checkpoint", "DETECTION"), "calibration": "synthetic-development", "versions": _versions()}

    @app.post("/api/v1/twin/segment_time")
    def segment_time(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "C5 API", final_mode=bool(app.state.final_mode))
        result = synthetic_transition(_state(payload), payload.get("action") or {}, payload.get("context") or synthetic_segments()[0])
        return {"t_s": {"mean": result["time_delta_s"], "low": result["time_delta_s"] - 0.01, "high": result["time_delta_s"] + 0.01, "unit": "s", "provenance": "SIMULATED"}, "delta_e_mj": {"mean": result["deployed_energy_mj"], "unit": "MJ", "provenance": "SIMULATED"}, "harvested_e_mj": {"mean": result["harvested_energy_mj"], "unit": "MJ", "provenance": "SIMULATED"}, "versions": _versions()}

    @app.post("/api/v1/twin/energy_state")
    def energy_state(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "C5 API", final_mode=bool(app.state.final_mode))
        state = _state(payload)
        energy = state["energy"]["ers_soc_est_mj"]["value"]
        return {"energy_mj": {"mean": energy, "low": max(0.0, energy - 0.2), "high": min(4.0, energy + 0.2), "unit": "MJ", "provenance": "SIMULATED"}, "ers_deployment_kw": {"mean": 180.0, "unit": "kW", "provenance": "SIMULATED"}, "ers_harvest_kw": {"mean": 12.0, "unit": "kW", "provenance": "SIMULATED"}, "causal_cutoff_distance_m": state["ref"]["distance_m"], "versions": _versions()}

    @app.get("/api/v1/value/{event}/shadow_price")
    def value_shadow(event: str, energy_kj: float = 2400.0, time_gap_s: float = 0.72, eligibility: str = "NOT_ARMED") -> dict[str, Any]:
        rules = _rules(event)
        state = synthetic_state()
        state["energy"]["ers_soc_est_mj"]["value"] = energy_kj / 1000.0
        state["gap"]["time_gap_s"]["value"] = time_gap_s
        state["overtake_state"]["armed"] = eligibility.upper() in {"ARMED", "ACTIVE", "ELIGIBLE"}
        result = shadow_price(synthetic_segments(), state, rules, transition_fn=synthetic_transition, config=DPConfig(rule_configuration_version=RULE_VERSION))
        return {"event": event, "energy_kj": energy_kj, "gap_s": time_gap_s, "eligibility": eligibility, "profile": [{"segment_id": segment["segment_id"], "lambda_utility_per_mj": result.get("marginal_value_per_mj"), "provenance": result.get("provenance")} for segment in synthetic_segments()], "shadow_price": result, "provenance": result.get("provenance", "SIMULATED"), "versions": _versions()}

    @app.post("/api/v1/plan")
    def planner(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "M24 API", final_mode=bool(app.state.final_mode))
        state = _state(payload)
        rules = _rules(str(payload.get("event", EVENT)))
        result = plan(_segments(payload), state, rules, transition_fn=synthetic_transition, dp_config=DPConfig(rule_configuration_version=RULE_VERSION), risk=None, final_mode=bool(app.state.final_mode))
        if payload.get("include_baselines") and result.get("status") != "UNAVAILABLE":
            result["baselines"] = generate_baseline_plans(_segments(payload), state, rules)
        return {**result, "versions": _versions()}

    @app.post("/api/v1/simulate")
    def simulation(payload: dict[str, Any]) -> dict[str, Any]:
        _guard_payload(payload, "M26 API", final_mode=bool(app.state.final_mode))
        result = simulate(_state(payload), _segments(payload), _rules(str(payload.get("event", EVENT))), our_policy=str(payload.get("our_policy", "beam_dp")), rival_policy=str(payload.get("rival_policy", "DEFEND_CONSERVE")), n_episodes=min(int(payload.get("n_episodes", 8)), 500), seed=int(payload.get("seed", 7)), transition_fn=synthetic_transition, pass_fn=synthetic_pass)
        return {**result, "versions": _versions()}

    @app.get("/api/v1/simulate/policies")
    def policies() -> dict[str, Any]:
        return {**policy_registry(), "versions": _versions()}

    # The replay builder can call these exact route handlers in-process even
    # when the optional HTTP test client is not installed. The HTTP surface and
    # the replay surface therefore share one implementation, not two serializers.
    app.state.route_handlers = {
        ("GET", "/api/v1/meta"): meta,
        ("GET", "/api/v1/validation"): validation,
        ("GET", f"/api/v1/track/{EVENT}"): lambda: track(EVENT),
        ("GET", f"/api/v1/rules/{EVENT}"): lambda: rules(EVENT),
        ("GET", f"/api/v1/rules/{EVENT}/power_envelope"): lambda mode="both", step_kmh=5.0: power_envelope(EVENT, mode, float(step_kmh)),
        ("GET", "/api/v1/battles"): battles,
        ("GET", f"/api/v1/battles/{BATTLE_ID}/timeline"): lambda: timeline(BATTLE_ID),
        ("GET", "/api/v1/simulate/policies"): policies,
        ("POST", "/api/v1/rules/legal_actions"): legal_actions,
        ("POST", "/api/v1/rules/eligibility"): eligibility,
        ("POST", "/api/v1/pass/predict"): pass_predict,
        ("POST", "/api/v1/twin/segment_time"): segment_time,
        ("POST", "/api/v1/twin/energy_state"): energy_state,
        ("POST", "/api/v1/rival/state"): rival,
        ("POST", "/api/v1/plan"): planner,
        ("POST", "/api/v1/simulate"): simulation,
    }
    return app


app = create_app()

__all__ = ["API_VERSION", "app", "create_app"]
