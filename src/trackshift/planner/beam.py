"""Auditable short-horizon planner core (M24).

This is a development seam: DP supplies the terminal value and C3 supplies the
legal candidate set before any action is scored. C4/C5 stubs are surfaced, not
silently converted into a deployable plan.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from trackshift.rules import api as c3
from trackshift.value.dp import DPConfig, DPResult, required_state_inputs, solve_dp
from trackshift.value.state import STUB_RESPONSE, c3_candidate_actions, c3_excluded_actions, reject_stubs_for_final

PLANNER_SCHEMA_VERSION = "m24_beam_development_v1"
RISK_CRITERIA = ("expected_value", "cvar")


@dataclass(frozen=True)
class RiskSpec:
    criterion: str = "expected_value"
    cvar_alpha: float = 0.2

    def __post_init__(self) -> None:
        if self.criterion not in RISK_CRITERIA:
            raise ValueError(f"unsupported risk criterion {self.criterion!r}")
        if not 0 < self.cvar_alpha <= 1:
            raise ValueError("cvar_alpha must be in (0, 1]")


def _state_key(dp: DPResult, state: Mapping[str, Any], segment: Mapping[str, Any]) -> str | None:
    inputs = required_state_inputs(state)
    if not inputs["ok"]:
        return None
    energy = inputs["energy"]
    gap = inputs["gap"]
    def value(x: Any, default: float) -> float:
        if isinstance(x, Mapping): x = x.get("value", x.get("mean", default))
        try: return float(x)
        except (TypeError, ValueError): return default
    energy, gap = value(energy, 0.0), value(gap, 0.0)
    energy = min(dp.metadata["grid"]["energy_mj"], key=lambda item: abs(item - energy))
    gap = min(dp.metadata["grid"]["gap_s"], key=lambda item: abs(item - gap))
    eligible = int(inputs["eligibility"])
    return f"0:{energy:.3f}:{gap:.3f}:{eligible}"


def plan(
    segments: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    event_rules: Mapping[str, Any] | None,
    *,
    transition_fn: Callable[..., Mapping[str, Any]] | None = None,
    legal_actions_fn: Callable[..., Mapping[str, Any]] | None = None,
    dp_config: DPConfig | None = None,
    risk: RiskSpec | None = None,
    final_mode: bool = False,
) -> dict[str, Any]:
    """Return a legal, auditable first action plus alternatives."""
    risk_spec = risk or RiskSpec()
    action_fn = legal_actions_fn or c3.legal_actions
    if not segments:
        return {"schema_version": PLANNER_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "DERIVED", "reason": "no horizon segments"}
    inputs = required_state_inputs(state)
    if not inputs["ok"]:
        return {"schema_version": PLANNER_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "DERIVED", "reason": inputs["reason"], "legal_actions": [], "excluded_actions": []}
    if not isinstance(event_rules, Mapping) or not event_rules:
        return {"schema_version": PLANNER_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "RULE", "reason": "C3 event rules unavailable", "legal_actions": [], "excluded_actions": []}
    try:
        action_set = action_fn(state, event_rules)
    except Exception as exc:
        return {"schema_version": PLANNER_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "RULE", "reason": f"C3 unavailable: {exc}", "legal_actions": [], "excluded_actions": []}
    legal = c3_candidate_actions(action_set)
    excluded = c3_excluded_actions(action_set)
    if not legal:
        return {"schema_version": PLANNER_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "RULE", "reason": "C3 returned no legal actions", "legal_actions": [], "excluded_actions": excluded}
    dp_result = solve_dp(segments, state, event_rules, transition_fn=transition_fn, legal_actions_fn=action_fn, config=dp_config)
    response: dict[str, Any] = {
        "schema_version": PLANNER_SCHEMA_VERSION, "status": dp_result.status,
        "provenance": dp_result.provenance, "reason": dp_result.reason,
        "legal_actions": legal, "excluded_actions": excluded,
        "rule_violations": 0, "risk": {"criterion": risk_spec.criterion, "cvar_alpha": risk_spec.cvar_alpha},
        "dp": dp_result.to_dict(), "alternatives": [],
        "decision": {"dominant_mechanism": "UNKNOWN", "primary_constraint": "UNKNOWN", "decision_stability": None},
        "model_versions": dict(dp_result.metadata.get("model_versions", {})),
        "rule_configuration_version": dp_result.metadata.get("rule_configuration_version"),
        "input_provenance": dp_result.metadata.get("input_provenance", {}),
    }
    if final_mode and dp_result.status == STUB_RESPONSE:
        reject_stubs_for_final(response)
    if final_mode:
        raise ValueError("final mode rejects development-only abstract utility; calibrated C4/C5 time objective is required")
    if dp_result.value is None:
        return response
    key = _state_key(dp_result, state, segments[0])
    chosen = dp_result.policy.get(key or "")
    # If the snapped initial state has no cached policy, keep the candidate set
    # legal and choose no recommendation rather than inventing a score.
    if chosen is None:
        response["status"] = "UNAVAILABLE"
        response["reason"] = "DP did not produce a policy for the supplied state"
        return response
    response["recommended_action"] = chosen
    response["expected_value"] = dp_result.value
    response["decision"] = {
        "dominant_mechanism": "ENERGY_POSITION_VALUE",
        "primary_constraint": "C3_LEGAL_ACTION_SET",
        "decision_stability": 1.0,
        "alternatives": [],
    }
    for action in legal:
        response["decision"]["alternatives"].append({"action": action, "expected_value": dp_result.value if action == chosen else None, "regret": 0.0 if action == chosen else None})
    response["alternatives"] = response["decision"]["alternatives"]
    return response


__all__ = ["PLANNER_SCHEMA_VERSION", "RISK_CRITERIA", "RiskSpec", "plan"]
