"""Seeded two-car counterfactual simulator (M26 development core)."""
from __future__ import annotations

from math import isnan
from random import Random
from typing import Any, Callable, Mapping, Sequence

from trackshift.rules import api as c3
from trackshift.value.state import STUB_RESPONSE, c3_candidate_actions, reject_stubs_for_final
from .rival_policies import POLICIES, choose_policy_action

SIMULATOR_SCHEMA_VERSION = "m26_simulator_development_v1"


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, Mapping): value = value.get("value", value.get("mean", default))
    try: return float(value)
    except (TypeError, ValueError): return default


def _ahead(state: Mapping[str, Any]) -> bool:
    return _number(state.get("gap_s", (state.get("gap") or {}).get("time_gap_s", 0.0))) < 0.0


def simulate(
    initial_state: Mapping[str, Any],
    segments: Sequence[Mapping[str, Any]],
    event_rules: Mapping[str, Any] | None,
    *,
    our_policy: str = "beam_dp",
    rival_policy: str = "DEFEND_CONSERVE",
    n_episodes: int = 1,
    seed: int = 7,
    transition_fn: Callable[..., Mapping[str, Any]] | None = None,
    pass_fn: Callable[..., Mapping[str, Any]] | None = None,
    legal_actions_fn: Callable[..., Mapping[str, Any]] | None = None,
    final_mode: bool = False,
) -> dict[str, Any]:
    """Run identical seeded environments for a pair of policies.

    A complete run requires caller-supplied public C5 transition and C4 pass
    callbacks. Missing dependencies return a labelled development result.
    """
    if n_episodes < 1:
        raise ValueError("n_episodes must be positive")
    if not segments:
        return {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "SIMULATED", "reason": "no segments"}
    if rival_policy not in POLICIES:
        raise ValueError(f"unsupported rival policy {rival_policy!r}")
    if transition_fn is None or pass_fn is None:
        result = {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": STUB_RESPONSE, "provenance": "STUB", "reason": "C4 pass_fn and C5 transition_fn are required", "summary": {"n_episodes": n_episodes, "seed": seed, "rule_violations": 0}, "episodes": [], "assumptions": ["C4/C5 dependency unavailable; no pass or energy outcome was invented"], "model_versions": {"c3": "PUBLIC", "c4": STUB_RESPONSE, "c5": STUB_RESPONSE}}
        if final_mode: reject_stubs_for_final(result)
        return result
    action_fn = legal_actions_fn or c3.legal_actions
    rng = Random(seed)
    episodes: list[dict[str, Any]] = []
    violations = 0
    for episode_index in range(n_episodes):
        environment = {"time_noise_s": rng.uniform(-0.05, 0.05), "seed": rng.randrange(2**31)}
        state = dict(initial_state)
        trace: list[dict[str, Any]] = []
        pass_lap = None
        repassed = False
        for segment in segments:
            context = dict(state)
            context["speed_kmh"] = segment.get("speed_kmh", state.get("speed_kmh"))
            action_set = action_fn(context, event_rules)
            legal = c3_candidate_actions(action_set)
            if not legal:
                violations += 1
                break
            if our_policy in POLICIES:
                ours = choose_policy_action(our_policy, context, event_rules, legal_actions_fn=action_fn)["action"]
            else:
                # beam_dp is a planner-owned policy label; the caller may
                # replace it later, while this development fallback still uses
                # only the current C3 set.
                ours = legal[len(legal) // 2]
            rival = choose_policy_action(rival_policy, context, event_rules, opponent_action=ours, legal_actions_fn=action_fn)["action"]
            if not any(dict(candidate) == dict(ours) for candidate in legal):
                violations += 1
            next_state = dict(transition_fn(context, ours, segment, environment))
            next_state["time_noise_s"] = environment["time_noise_s"]
            outcome = dict(pass_fn(context, ours, rival, segment, environment))
            if outcome.get("passed") is True and pass_lap is None:
                pass_lap = segment.get("lap")
            if outcome.get("repassed") is True:
                repassed = True
            trace.append({"segment_id": segment.get("segment_id"), "gap_s": next_state.get("gap_s"), "energy_mj": next_state.get("energy_mj"), "action": dict(ours), "rival_action": dict(rival), "provenance": "SIMULATED"})
            state.update(next_state)
        episodes.append({"episode": episode_index, "outcome": "AHEAD" if _ahead(state) else "BEHIND", "pass_lap": pass_lap, "repassed": repassed, "trace": trace, "environment_seed": environment["seed"]})
    ahead_count = sum(item["outcome"] == "AHEAD" for item in episodes)
    result = {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": "COMPLETE", "provenance": "SIMULATED", "summary": {"p_ahead_at_horizon": ahead_count / len(episodes), "mean_final_energy_mj": sum(_number(item["trace"][-1].get("energy_mj")) for item in episodes if item["trace"]) / max(sum(bool(item["trace"]) for item in episodes), 1), "rule_violations": violations, "n_episodes": n_episodes, "seed": seed}, "episodes": episodes, "assumptions": ["rival policy is explicit and fixed, not a learned agent", "energy and pass transitions are supplied model outputs and tagged SIMULATED"], "model_versions": {"c3": "PUBLIC", "c4": "PUBLIC_CALLBACK", "c5": "PUBLIC_CALLBACK"}}
    if final_mode and result["summary"]["rule_violations"] != 0:
        raise RuntimeError("simulator produced a rule violation")
    return result


__all__ = ["SIMULATOR_SCHEMA_VERSION", "simulate"]
