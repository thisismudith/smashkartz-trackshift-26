"""Transparent rival policies (M27), all constrained by public C3."""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from trackshift.rules import api as c3
from trackshift.value.state import c3_candidate_actions, c3_excluded_actions

POLICY_SCHEMA_VERSION = "m27_rival_policies_v1"
POLICIES = {
    "DEFEND_CONSERVE": {"description": "holds energy and deploys only at a declared activation opportunity", "parameters": {"deploy_level_default": 0.25}},
    "DEFEND_MIRROR": {"description": "matches attacker deployment one segment late", "parameters": {"lag_segments": 1}},
    "ATTACK_GREEDY": {"description": "uses the maximum currently legal deployment to counterattack", "parameters": {"deploy_level": 1.0}},
}


class UnknownPolicyError(ValueError):
    pass


def policy_registry() -> dict[str, Any]:
    return {"schema_version": POLICY_SCHEMA_VERSION, "policies": [{"name": name, **details} for name, details in POLICIES.items()], "provenance": "RULE", "model_version": POLICY_SCHEMA_VERSION}


def _level(action: Mapping[str, Any]) -> float:
    try: return float(action["deploy_level"])
    except (KeyError, TypeError, ValueError): raise ValueError("C3 action has no finite deploy_level")


def choose_policy_action(
    name: str,
    state: Mapping[str, Any],
    event_rules: Mapping[str, Any] | None,
    *,
    opponent_action: Mapping[str, Any] | None = None,
    legal_actions_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Select from C3's returned legal set; never construct an action directly."""
    if name not in POLICIES:
        raise UnknownPolicyError(f"unsupported rival policy {name!r}; expected {tuple(POLICIES)}")
    if not isinstance(event_rules, Mapping) or not event_rules:
        return {"status": "UNAVAILABLE", "provenance": "RULE", "reason": "C3 event rules unavailable", "action": None, "legal_action_count": 0, "excluded_action_count": 0}
    action_fn = legal_actions_fn or c3.legal_actions
    action_set = action_fn(state, event_rules)
    actions = c3_candidate_actions(action_set)
    if not actions:
        raise RuntimeError("C3 returned no legal actions for rival policy")
    if name == "ATTACK_GREEDY":
        selected = max(actions, key=_level)
    elif name == "DEFEND_CONSERVE":
        candidates = [action for action in actions if _level(action) <= 0.25 + 1e-12] or list(actions)
        selected = min(candidates, key=_level)
    else:  # DEFEND_MIRROR
        if opponent_action is None:
            return {"status": "UNAVAILABLE", "provenance": "SIMULATED", "reason": "DEFEND_MIRROR requires the current opponent action", "action": None, "legal_action_count": len(actions), "excluded_action_count": len(c3_excluded_actions(action_set))}
        target = _level(opponent_action)
        selected = min(actions, key=lambda action: abs(_level(action) - target))
    return {"action": dict(selected), "policy": name, "provenance": "SIMULATED", "legal_action_count": len(actions), "excluded_action_count": len(c3_excluded_actions(action_set))}


__all__ = ["POLICY_SCHEMA_VERSION", "POLICIES", "UnknownPolicyError", "policy_registry", "choose_policy_action"]
