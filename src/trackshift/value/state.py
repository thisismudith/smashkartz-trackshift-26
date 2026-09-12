"""CP-09 causal StrategicState adapter and explicitly non-production stubs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from trackshift.contracts.strategic_state import validate_strategic_state
from trackshift.rules import api as rules_api

STUB_RESPONSE = "STUB_RESPONSE"
DISCRETIZATION_VERSION = "strategic_state_discretization_v1"
_FORBIDDEN = ("future", "outcome", "pass_result", "post_battle", "offline_only", "summary")

class StrategicStateAdapterError(ValueError): pass

def _unavailable(reason: str, unit: str | None = None, provenance: str = "SIMULATED") -> dict[str, Any]:
    result = {"value": None, "reason": reason, "provenance": provenance}
    if unit: result["unit"] = unit
    return result

def _check_causal(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if any(token in str(key).lower() for token in _FORBIDDEN):
                raise StrategicStateAdapterError(f"non-causal or offline field rejected: {key}")
            _check_causal(child)
    elif isinstance(value, list):
        for child in value: _check_causal(child)

def _q(row: Mapping[str, Any], name: str, unit: str, provenance: str = "DERIVED") -> dict[str, Any]:
    value = row.get(name)
    return {"value": value, "unit": unit, "provenance": provenance} if value is not None else {"value": None, "unit": unit, "provenance": provenance, "reason": f"{name} unavailable at decision step"}


def c3_candidate_actions(action_set: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return only C3's legal candidates; excluded actions are audit-only.

    ``actions`` is the current C3 engine field.  ``legal_actions`` is accepted
    for API-shaped C3 responses while the public contract is being integrated.
    An unavailable or malformed C3 response is deliberately fail-closed.
    """
    if not isinstance(action_set, Mapping) or action_set.get("available") is False:
        return []
    for key in ("actions", "legal_actions"):
        actions = action_set.get(key)
        if isinstance(actions, list):
            return actions
    return []


def c3_excluded_actions(action_set: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return C3's exclusion audit separately from selectable candidates."""
    if not isinstance(action_set, Mapping):
        return []
    for key in ("excluded", "excluded_actions"):
        excluded = action_set.get(key)
        if isinstance(excluded, list):
            return excluded
    return []


def _c3_unavailable(
    reason: str,
    response: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Preserve an unavailable C3 result without manufacturing an action."""
    result = dict(response or {})
    result["available"] = False
    result["reason"] = str(result.get("reason") or reason)
    result["provenance"] = result.get("provenance") or "RULE"
    result.setdefault("actions", [])
    result.setdefault("excluded", [])
    return result


def _c3_action_set(
    state: Mapping[str, Any], event_rules: Mapping[str, Any],
) -> dict[str, Any]:
    """Call only the public C3 boundary and make unavailable responses explicit."""
    try:
        response = rules_api.legal_actions(state, event_rules)
    except Exception as exc:
        return _c3_unavailable(f"C3 legal_actions unavailable: {exc}")
    if not isinstance(response, Mapping):
        return _c3_unavailable("C3 legal_actions returned a non-object response")
    if response.get("error") is not None:
        return _c3_unavailable(f"C3 legal_actions error: {response['error']}", response)
    if not c3_candidate_actions(response):
        return _c3_unavailable("C3 legal_actions returned no legal action set", response)
    return dict(response)

def battle_step_to_strategic_state(battle_step: Mapping[str, Any], *, event_rules: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Adapt current-step C1/C2/C7/C8/C9 inputs; never derive missing models."""
    _check_causal(battle_step)
    ref_keys = ("year", "event", "session", "lap", "segment_id", "distance_m")
    if any(battle_step.get(k) is None for k in ref_keys):
        raise StrategicStateAdapterError("decision step lacks C1 identity/ref")
    eligible = battle_step.get("normal_race_model_eligible")
    if eligible is None: raise StrategicStateAdapterError("C7 normal_race_model_eligible is required")
    unavailable = "not normal-race eligible" if not eligible else "upstream model unavailable"
    state = {
      "ref": {k: battle_step[k] for k in ref_keys} | {k: battle_step[k] for k in ("geometry_version", "boundary_hash", "source_artifact_version", "rule_configuration_version", "horizon_segments") if k in battle_step},
      "energy": battle_step.get("energy") or {"ers_soc_est_mj": _unavailable(unavailable, "MJ")},
      "tyre": battle_step.get("tyre") or {"compound": battle_step.get("attacker_tyre_compound"), "performance_state": _unavailable(unavailable, "ratio")},
      "gap": {"time_gap_s": _q(battle_step,"time_gap_s","s"), "distance_gap_m": _q(battle_step,"distance_gap_m","m"), "relative_speed_mps": _q(battle_step,"relative_speed_mps","m/s"), "gap_rate_s_per_s": _q(battle_step,"gap_rate_s_per_s","s/s")},
      "overtake_state": battle_step.get("overtake_state") or {"state":"UNKNOWN", "provenance":"RULE", "reason":"C6 unavailable"},
      "race_control": battle_step.get("race_control") or {"normal_race_model_eligible": eligible, "pit_state": battle_step.get("pit_state","UNKNOWN"), "race_control_state": battle_step.get("race_control_state","UNKNOWN"), "provenance":"DERIVED"},
      "power_envelope": battle_step.get("power_envelope") or {"requested_mode":"auto", "provenance":"RULE", "reason":"C3 configuration not supplied"},
      "rival_state": battle_step.get("rival_state") or {"mean": None, "provenance":"INFERRED", "reason": unavailable},
      "uncertainty": battle_step.get("uncertainty") or {"available":False, "reason":unavailable},
      "versions": {"state_discretization": DISCRETIZATION_VERSION, "rules": battle_step.get("rule_configuration_version")},
    }
    if battle_step.get("speed_kmh") is not None:
        state["speed_kmh"] = battle_step["speed_kmh"]
    if event_rules is not None:
        state["legal_actions"] = _c3_action_set(state, event_rules)  # C3 public boundary only
    return validate_strategic_state(state)

def discretize_state(state: Mapping[str, Any], config_path: Path | None = None) -> dict[str, Any]:
    cfg = yaml.safe_load((config_path or Path(__file__).resolve().parents[3] / "config/state_discretization.yaml").read_text())
    def value(path: tuple[str,...]):
        x: Any = state
        for p in path: x = x.get(p, {}) if isinstance(x, Mapping) else {}
        if isinstance(x, Mapping):
            return x.get("value") if "value" in x else x.get("mean") if "mean" in x else None
        return x
    paths={"energy_state_mj":("energy","ers_soc_est_mj"),"tyre_state":("tyre","performance_state"),"time_gap_s":("gap","time_gap_s"),"distance_gap_m":("gap","distance_gap_m"),"relative_speed_mps":("gap","relative_speed_mps"),"gap_rate_s_per_s":("gap","gap_rate_s_per_s"),"overtake_state":("overtake_state","state"),"power_regime":("power_envelope","applicable_mode"),"rival_belief":("rival_state","state"),"uncertainty":("uncertainty","level"),"horizon":("ref","horizon_segments")}
    bins={}
    for name, path in paths.items():
        x, spec = value(path), cfg["dimensions"][name]
        if x is None: bins[name]=None
        elif "categories" in spec: bins[name]=str(x) if str(x) in spec["categories"] else "UNKNOWN"
        else: bins[name]=sum(float(x) >= edge for edge in spec["edges"]) - 1
    return {"schema_version":cfg["schema_version"], "bins":bins}

def dependency_stub(contract: str, reason: str = "dependency unavailable") -> dict[str, Any]:
    return {"marker":STUB_RESPONSE,"contract":contract,"provenance":"STUB","reason":reason,"value":None}

def reject_stubs_for_final(payload: Mapping[str, Any]) -> None:
    if STUB_RESPONSE in json.dumps(payload, sort_keys=True): raise StrategicStateAdapterError("STUB_RESPONSE cannot enter final evaluation or replay bundle")
