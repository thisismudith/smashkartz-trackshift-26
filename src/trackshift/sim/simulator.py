"""Seeded two-car counterfactual simulator (M26 development core)."""
from __future__ import annotations

from math import isnan
from random import Random
from typing import Any, Callable, Mapping, Sequence

from trackshift.rules import api as c3
from trackshift.data.registry import assert_final_feature_boundary
from trackshift.value.state import STUB_RESPONSE, c3_candidate_actions, reject_stubs_for_final
from trackshift.value.dp import required_state_inputs, _with_decision_context
from .rival_policies import POLICIES, choose_policy_action

SIMULATOR_SCHEMA_VERSION = "m26_simulator_development_v1"
# beam_dp is the planner's label, documented on the route; the rest are the rival
# policies this core can actually run. Anything else is refused rather than quietly
# answered with a different policy's trajectory.
ACCEPTED_OUR_POLICIES = frozenset(POLICIES) | {"beam_dp"}
# required_state_inputs prefers these flat spellings over the nested StrategicState
# blocks, so a copy left on the state would outrank every later advance.
_FLAT_STATE_ALIASES = ("energy_mj", "gap_s", "eligibility", "overtake_eligible")


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, Mapping): value = value.get("value", value.get("mean", default))
    try: return float(value)
    except (TypeError, ValueError): return default


def _ahead(state: Mapping[str, Any]) -> bool:
    inputs = required_state_inputs(state)
    if not inputs["ok"]:
        raise ValueError(inputs["reason"] or "gap unavailable")
    return float(inputs["gap"]) < 0.0


def _advance(state: Mapping[str, Any], inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Carry one segment's outcome into the blocks the next segment reads.

    A transition returns flat keys, but C3 and the transition itself read the nested
    StrategicState path, so copying the flat keys onto the state left every segment
    re-running from the initial energy and gap. This is the DP's own advance, so a
    simulated trajectory and a planned one mean the same thing by "state"; the empty
    segment keeps the reference frame with the loop, which is the only place that
    knows which segment is next.
    """
    base = {name: value for name, value in state.items() if name not in _FLAT_STATE_ALIASES}
    advanced = _with_decision_context(base, {}, inputs["energy"], inputs["gap"], inputs["eligibility"])
    for block, key in (("energy", "ers_soc_est_mj"), ("gap", "time_gap_s")):
        # The DP labels these as its grid state, and its gap as DERIVED. Here both
        # numbers came out of a supplied simulation and must not read as measured.
        advanced[block][key] = dict(advanced[block][key]) | {"provenance": "SIMULATED", "reason": "advanced by the supplied C5 transition"}
    return advanced


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
    inputs = required_state_inputs(initial_state)
    if not inputs["ok"]:
        return {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "SIMULATED", "reason": inputs["reason"], "summary": {"n_episodes": n_episodes, "seed": seed, "rule_violations": 0}, "episodes": []}
    if not isinstance(event_rules, Mapping) or not event_rules:
        return {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "RULE", "reason": "C3 event rules unavailable", "summary": {"n_episodes": n_episodes, "seed": seed, "rule_violations": 0}, "episodes": []}
    if final_mode:
        assert_final_feature_boundary(initial_state, "simulator final state")
        for segment in segments:
            assert_final_feature_boundary(segment, "simulator final segment")
        c3.assert_final_mode_rules(event_rules)
        raise ValueError("final mode requires calibrated C4/C5 public callbacks; development simulator cannot certify them")
    if rival_policy not in POLICIES:
        raise ValueError(f"unsupported rival policy {rival_policy!r}")
    if our_policy not in ACCEPTED_OUR_POLICIES:
        return {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "SIMULATED", "reason": f"unsupported our_policy {our_policy!r}; expected {tuple(sorted(ACCEPTED_OUR_POLICIES))}", "our_policy": our_policy, "summary": {"n_episodes": n_episodes, "seed": seed, "rule_violations": 0}, "episodes": []}
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
                choice = choose_policy_action(our_policy, context, event_rules, legal_actions_fn=action_fn)
                ours = choice.get("action")
                if ours is None:
                    # A policy that cannot run for our car (DEFEND_MIRROR needs an
                    # opponent action that is only chosen after ours) says so rather
                    # than falling through to somebody else's action.
                    return {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "SIMULATED", "reason": choice.get("reason") or f"{our_policy} returned no action", "our_policy": our_policy, "summary": {"n_episodes": n_episodes, "seed": seed, "rule_violations": violations}, "episodes": episodes}
            else:
                # beam_dp is a planner-owned policy label; the caller may
                # replace it later, while this development fallback still uses
                # only the current C3 set.
                ours = legal[len(legal) // 2]
            rival = choose_policy_action(rival_policy, context, event_rules, opponent_action=ours, legal_actions_fn=action_fn)["action"]
            if not any(dict(candidate) == dict(ours) for candidate in legal):
                violations += 1
            next_state = dict(transition_fn(context, ours, segment, environment))
            next_inputs = required_state_inputs(next_state)
            if not next_inputs["ok"]:
                return {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": "UNAVAILABLE", "provenance": "SIMULATED", "reason": next_inputs["reason"], "summary": {"n_episodes": n_episodes, "seed": seed, "rule_violations": violations}, "episodes": episodes}
            outcome = dict(pass_fn(context, ours, rival, segment, environment))
            if outcome.get("passed") is True and pass_lap is None:
                pass_lap = segment.get("lap")
            if outcome.get("repassed") is True:
                repassed = True
            trace.append({"segment_id": segment.get("segment_id"), "gap_s": next_inputs["gap"], "energy_mj": next_inputs["energy"], "action": dict(ours), "rival_action": dict(rival), "provenance": "SIMULATED"})
            state = _advance(state, next_inputs)
        episodes.append({"episode": episode_index, "outcome": "AHEAD" if _ahead(state) else "BEHIND", "pass_lap": pass_lap, "repassed": repassed, "trace": trace, "environment_seed": environment["seed"]})
    ahead_count = sum(item["outcome"] == "AHEAD" for item in episodes)
    distinct = len({repr(item["trace"]) for item in episodes})
    assumptions = ["rival policy is explicit and fixed, not a learned agent", "energy and pass transitions are supplied model outputs and tagged SIMULATED"]
    summary = {"p_ahead_at_horizon": ahead_count / len(episodes), "n_distinct_episodes": distinct, "mean_final_energy_mj": sum(_number(item["trace"][-1].get("energy_mj")) for item in episodes if item["trace"]) / max(sum(bool(item["trace"]) for item in episodes), 1), "rule_violations": violations, "n_episodes": n_episodes, "seed": seed}
    if distinct < 2:
        # One trajectory reported n times is not a sample, so the fraction of it that
        # ends ahead is not a probability. Say that where the number would have gone,
        # rather than publish a 0 or a 1 that reads as a frequency over futures.
        summary["p_ahead_at_horizon"] = None
        if not any(item["trace"] for item in episodes):
            summary["p_ahead_at_horizon_reason"] = "no segment was simulated: C3 returned no legal action, so every episode is the initial state unchanged"
        elif len(episodes) == 1:
            summary["p_ahead_at_horizon_reason"] = "one episode is a single rollout, not a frequency over sampled futures"
        else:
            summary["p_ahead_at_horizon_reason"] = f"all {len(episodes)} episodes produced the same trajectory: the supplied C5 transition and C4 pass callbacks did not respond to the per-episode environment, so this is one deterministic rollout reported {len(episodes)} times"
            assumptions.append("the supplied transition and pass callbacks are deterministic; the episode set is one rollout, not a distribution")
    if our_policy not in POLICIES:
        assumptions.append(f"{our_policy} was not run: this development core has no planner, so our car took the midpoint of the C3 legal set in every segment")
    result = {"schema_version": SIMULATOR_SCHEMA_VERSION, "status": "COMPLETE", "provenance": "SIMULATED", "our_policy": our_policy, "our_policy_resolved": "POLICY" if our_policy in POLICIES else "C3_MIDPOINT_FALLBACK", "summary": summary, "episodes": episodes, "assumptions": assumptions, "model_versions": {"c3": "PUBLIC", "c4": "PUBLIC_CALLBACK", "c5": "PUBLIC_CALLBACK"}}
    return result


__all__ = ["SIMULATOR_SCHEMA_VERSION", "simulate"]
