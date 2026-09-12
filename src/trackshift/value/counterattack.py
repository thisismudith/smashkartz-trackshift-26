"""Post-pass counterattack valuation (M23 development core)."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .state import STUB_RESPONSE, c3_candidate_actions, c3_excluded_actions
from .dp import required_state_inputs

COUNTERATTACK_SCHEMA_VERSION = "m23_counterattack_development_v1"
EXPLANATIONS = ("PASS_AND_SECURE", "PASS_BUT_EXPOSED", "NO_PASS_BUT_PROTECT", "NO_PASS_AND_LOSE")


def _probability(value: Any) -> float | None:
    if isinstance(value, Mapping):
        value = value.get("value", value.get("mean"))
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if 0.0 <= result <= 1.0 else None


def classify_explanation(p_pass: float | None, p_repass: float | None, *, protect_value: float | None = None) -> str | None:
    """Return an explanation label, never a claimed ground-truth outcome."""
    if p_pass is None or p_repass is None:
        return None
    if p_pass >= 0.5:
        return "PASS_BUT_EXPOSED" if p_repass >= 0.35 else "PASS_AND_SECURE"
    if protect_value is None:
        return None
    return "NO_PASS_BUT_PROTECT" if protect_value >= 0.5 else "NO_PASS_AND_LOSE"


def evaluate_counterattack(
    state: Mapping[str, Any],
    *,
    pass_prediction: Mapping[str, Any] | None = None,
    repass_prediction: Mapping[str, Any] | None = None,
    legal_action_set: Mapping[str, Any] | None = None,
    c3_excluded: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate pass-and-secure versus pass-and-expose from a frozen state.

    C4/C6/C10 predictions are accepted only as public response objects. Missing
    upstream probabilities are preserved as null and marked ``STUB_RESPONSE``.
    """
    inputs = required_state_inputs(state) if isinstance(state, Mapping) else {"ok": False, "reason": "current StrategicState is missing"}
    if not inputs["ok"]:
        return {"schema_version": COUNTERATTACK_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "DERIVED", "reason": inputs["reason"], "legal_actions": [], "excluded_actions": []}
    if legal_action_set is not None and not c3_candidate_actions(legal_action_set):
        return {"schema_version": COUNTERATTACK_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "RULE", "reason": "C3 returned no legal actions", "legal_actions": [], "excluded_actions": c3_excluded_actions(legal_action_set)}
    p_pass = _probability((pass_prediction or {}).get("p_pass_by_outcome_horizon"))
    p_repass = _probability((repass_prediction or {}).get("p_repass_within_horizon", (repass_prediction or {}).get("p_pass_by_outcome_horizon")))
    if p_pass is None or p_repass is None:
        return {
            "schema_version": COUNTERATTACK_SCHEMA_VERSION, "status": STUB_RESPONSE,
            "provenance": "STUB", "reason": "C4 pass or C6/C10 counterattack probability unavailable",
            "p_pass": {"value": None, "provenance": "INFERRED", "reason": "C4 unavailable"},
            "p_repass": {"value": None, "provenance": "INFERRED", "reason": "counterattack term unavailable"},
            "terminal_value": None, "explanation": None, "input_state_provenance": inputs["provenance"],
            "legal_actions": c3_candidate_actions(legal_action_set) if legal_action_set else [],
            "excluded_actions": list(c3_excluded or c3_excluded_actions(legal_action_set)),
        }
    # A pass secures position with probability (1 - repass), but a repass is a
    # negative race-position outcome. The baseline no-pass branch is neutral.
    secure = p_pass * (1.0 - p_repass)
    exposed = p_pass * p_repass
    terminal = secure - exposed
    explanation = classify_explanation(p_pass, p_repass)
    return {
        "schema_version": COUNTERATTACK_SCHEMA_VERSION, "status": "COMPLETE", "provenance": "DERIVED",
        "p_pass": {"value": p_pass, "provenance": pass_prediction.get("provenance", "INFERRED")},
        "p_repass": {"value": p_repass, "provenance": repass_prediction.get("provenance", "INFERRED")},
        "components": {"pass_and_secure": secure, "pass_but_exposed": exposed, "no_pass": 1.0 - p_pass},
        "terminal_value": terminal, "explanation": explanation,
        "explanation_is_not_ground_truth": True,
        "legal_actions": c3_candidate_actions(legal_action_set) if legal_action_set else [],
        "excluded_actions": list(c3_excluded or c3_excluded_actions(legal_action_set)),
        "frozen_state_ref": dict(state.get("ref") or {}),
        "input_state_provenance": inputs["provenance"],
    }


__all__ = ["COUNTERATTACK_SCHEMA_VERSION", "EXPLANATIONS", "classify_explanation", "evaluate_counterattack"]
