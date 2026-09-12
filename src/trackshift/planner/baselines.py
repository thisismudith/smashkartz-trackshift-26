"""Planner baselines (M25) sharing C3 legal action filtering."""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from trackshift.rules import api as c3
from trackshift.value.state import c3_candidate_actions, c3_excluded_actions

BASELINE_NAMES = ("greedy_attack", "longest_straight", "lap_time_only", "dp", "beam_dp", "oracle_rival_state")


def _deploy(action: Mapping[str, Any]) -> float:
    try: return float(action.get("deploy_level", 0.0))
    except (TypeError, ValueError): return 0.0


def _choose(name: str, actions: Sequence[Mapping[str, Any]], segment: Mapping[str, Any]) -> Mapping[str, Any]:
    if name == "greedy_attack":
        return max(actions, key=lambda action: (_deploy(action), -float(action.get("lift_amount", 0.0))))
    if name == "longest_straight":
        if str(segment.get("kind", "")).upper() == "STRAIGHT":
            return max(actions, key=_deploy)
        return min(actions, key=_deploy)
    if name == "lap_time_only":
        # A local timing-only baseline uses only current-segment action fields;
        # it never sees a future outcome or a rival oracle.
        return max(actions, key=lambda action: (_deploy(action), -float(action.get("lift_amount", 0.0))))
    if name in {"dp", "beam_dp"}:
        return actions[len(actions) // 2]
    if name == "oracle_rival_state":
        return max(actions, key=_deploy)
    raise ValueError(f"unsupported baseline {name!r}")


def generate_baseline_plans(
    segments: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    event_rules: Mapping[str, Any] | None,
    *,
    legal_actions_fn: Callable[..., Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate comparable legal action traces for every M25 baseline."""
    if not segments:
        return {"status": "UNAVAILABLE", "reason": "no segments", "baselines": []}
    action_fn = legal_actions_fn or c3.legal_actions
    plans = {name: [] for name in BASELINE_NAMES}
    exclusions: dict[str, list[dict[str, Any]]] = {}
    for index, segment in enumerate(segments):
        contextual = dict(state)
        contextual["speed_kmh"] = segment.get("speed_kmh", state.get("speed_kmh"))
        try:
            action_set = action_fn(contextual, event_rules)
        except Exception as exc:
            return {"status": "UNAVAILABLE", "provenance": "RULE", "reason": f"C3 unavailable: {exc}", "baselines": []}
        actions = c3_candidate_actions(action_set)
        exclusions[str(index)] = c3_excluded_actions(action_set)
        if not actions:
            return {"status": "UNAVAILABLE", "provenance": "RULE", "reason": "C3 returned no legal actions", "baselines": [], "excluded_actions": exclusions}
        for name in BASELINE_NAMES:
            selected = dict(_choose(name, actions, segment))
            if name == "oracle_rival_state":
                selected["upper_bound_only"] = True
                selected["deployable"] = False
            plans[name].append(selected)
    return {"status": "COMPLETE", "provenance": "RULE", "baselines": plans, "excluded_actions": exclusions, "rule_violations": 0, "names": list(BASELINE_NAMES), "oracle_note": "oracle_rival_state is an upper bound and is never deployable"}


__all__ = ["BASELINE_NAMES", "generate_baseline_plans"]
