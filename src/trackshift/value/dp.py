"""Development dynamic-programming core (M22).

The DP owns no regulations or physics. Legal actions come exclusively from
public C3, and a caller supplies the causal transition obtained from public C5.
Without that transition the result is explicitly ``STUB_RESPONSE`` rather than
an invented energy or time trajectory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any, Callable, Mapping, Sequence

from trackshift.rules import api as c3
from .state import STUB_RESPONSE, c3_candidate_actions, c3_excluded_actions

DP_SCHEMA_VERSION = "m22_dp_development_v1"


@dataclass(frozen=True)
class DPConfig:
    energy_grid_mj: tuple[float, ...] = tuple(round(i * 0.1, 10) for i in range(41))
    gap_grid_s: tuple[float, ...] = tuple(round(-3.0 + i * 0.1, 10) for i in range(61))
    eligibility_states: tuple[int, ...] = (0, 1)
    beta_energy_value: float = 0.01
    seed: int = 20260913
    split_version: str = "not_applicable"
    rule_configuration_version: str | None = None
    model_versions: Mapping[str, str] = field(default_factory=dict)


@dataclass
class DPResult:
    schema_version: str
    status: str
    value: float | None
    policy: dict[str, dict[str, Any]]
    value_table: dict[str, float]
    excluded_actions: dict[str, list[dict[str, Any]]]
    metadata: dict[str, Any]
    provenance: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "status": self.status,
            "value": self.value, "policy": self.policy, "value_table": self.value_table,
            "excluded_actions": self.excluded_actions, "metadata": self.metadata,
            "provenance": self.provenance, "reason": self.reason,
        }


def _number(value: Any, default: float | None = None) -> float | None:
    if isinstance(value, Mapping):
        value = value.get("value", value.get("mean"))
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _snap(value: float, grid: Sequence[float]) -> float:
    return min(grid, key=lambda candidate: abs(candidate - value))


def _state_value(state: Mapping[str, Any], *paths: tuple[str, ...], default: float = 0.0) -> float:
    for path in paths:
        node: Any = state
        for part in path:
            if not isinstance(node, Mapping) or part not in node:
                node = None
                break
            node = node[part]
            if isinstance(node, Mapping) and "value" in node:
                node = node["value"]
        value = _number(node)
        if value is not None:
            return value
    return default


def _key(segment_index: int, energy: float, gap: float, eligibility: int) -> str:
    return f"{segment_index}:{energy:.3f}:{gap:.3f}:{eligibility}"


def _with_decision_context(base: Mapping[str, Any], segment: Mapping[str, Any], energy: float, gap: float, eligibility: int) -> dict[str, Any]:
    state = dict(base)
    ref = dict(state.get("ref") or {})
    for name in ("segment_id", "lap", "distance_m", "segment_duration_s"):
        if name in segment:
            ref[name] = segment[name]
    state["ref"] = ref
    state["speed_kmh"] = segment.get("speed_kmh", state.get("speed_kmh", ref.get("speed_kmh", 0.0)))
    energy_block = dict(state.get("energy") or {})
    energy_block["ers_soc_est_mj"] = {"value": energy, "provenance": "SIMULATED", "unit": "MJ"}
    state["energy"] = energy_block
    gap_block = dict(state.get("gap") or {})
    gap_block["time_gap_s"] = {"value": gap, "provenance": "DERIVED", "unit": "s"}
    state["gap"] = gap_block
    overtake = state.get("overtake_state")
    if isinstance(overtake, Mapping):
        state["overtake_state"] = dict(overtake) | {"armed": bool(eligibility)}
    return state


def _transition_numbers(next_state: Mapping[str, Any], *, current_energy: float, current_gap: float, current_eligibility: int) -> tuple[float, float, int, float]:
    energy = _state_value(next_state, ("energy_mj",), ("energy", "ers_soc_est_mj"), default=current_energy)
    gap = _state_value(next_state, ("gap_s",), ("gap", "time_gap_s"), default=current_gap)
    eligibility = int(bool(next_state.get("eligibility", next_state.get("overtake_eligible", current_eligibility))))
    time_delta = _state_value(next_state, ("time_delta_s",), ("segment_time_s",), default=0.0)
    return energy, gap, eligibility, time_delta


def solve_dp(
    segments: Sequence[Mapping[str, Any]],
    initial_state: Mapping[str, Any],
    event_rules: Mapping[str, Any] | None,
    *,
    transition_fn: Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]] | None = None,
    legal_actions_fn: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]] | None = None,
    config: DPConfig | None = None,
    allow_stubs: bool = True,
) -> DPResult:
    """Solve a small causal DP while retaining C3 exclusions and dependency state."""
    cfg = config or DPConfig()
    metadata = {
        "grid": {"energy_mj": list(cfg.energy_grid_mj), "gap_s": list(cfg.gap_grid_s), "eligibility": list(cfg.eligibility_states)},
        "horizon_segments": len(segments), "rule_configuration_version": cfg.rule_configuration_version,
        "split_version": cfg.split_version, "model_versions": dict(cfg.model_versions),
        "seed": cfg.seed, "causal_cutoff": initial_state.get("ref", {}).get("distance_m", initial_state.get("segment_id")),
        "c3_public_boundary": True, "excluded_actions_are_audit_only": True,
    }
    if not segments:
        return DPResult(DP_SCHEMA_VERSION, "UNAVAILABLE", None, {}, {}, {}, metadata, "DERIVED", "no segments supplied")
    if not isinstance(event_rules, Mapping) or not event_rules:
        return DPResult(DP_SCHEMA_VERSION, "UNAVAILABLE", None, {}, {}, {}, metadata, "RULE", "C3 event rules unavailable")
    if transition_fn is None:
        reason = "C5 segment transition unavailable; provide a public C5 transition_fn"
        metadata["dependencies"] = {"C4": "STUB_RESPONSE", "C5": STUB_RESPONSE}
        return DPResult(DP_SCHEMA_VERSION, STUB_RESPONSE, None, {}, {}, {}, metadata, "STUB", reason)
    action_fn = legal_actions_fn or c3.legal_actions
    values: dict[str, float] = {}
    policy: dict[str, dict[str, Any]] = {}
    exclusions: dict[str, list[dict[str, Any]]] = {}
    had_stub = False

    def recurse(index: int, energy: float, gap: float, eligibility: int) -> float:
        nonlocal had_stub
        energy = _snap(energy, cfg.energy_grid_mj)
        gap = _snap(gap, cfg.gap_grid_s)
        if index >= len(segments):
            return (1.0 if gap < 0 else 0.0) + cfg.beta_energy_value * energy
        state_key = _key(index, energy, gap, eligibility)
        if state_key in values:
            return values[state_key]
        segment = segments[index]
        state = _with_decision_context(initial_state, segment, energy, gap, eligibility)
        try:
            action_set = action_fn(state, event_rules)
        except Exception as exc:
            values[state_key] = float("-inf")
            exclusions[state_key] = [{"reason": f"C3 unavailable: {exc}"}]
            return values[state_key]
        if not isinstance(action_set, Mapping) or action_set.get("available") is False:
            values[state_key] = float("-inf")
            exclusions[state_key] = [{"reason": str(action_set.get("reason", "C3 unavailable")) if isinstance(action_set, Mapping) else "C3 unavailable"}]
            return values[state_key]
        if action_set.get("stub") or action_set.get("marker") == STUB_RESPONSE:
            had_stub = True
        actions = c3_candidate_actions(action_set)
        exclusions[state_key] = c3_excluded_actions(action_set)
        if not actions:
            values[state_key] = float("-inf")
            return values[state_key]
        best_value = float("-inf")
        best_action: dict[str, Any] | None = None
        for action in actions:
            try:
                next_state = transition_fn(state, dict(action), segment)
                if not isinstance(next_state, Mapping):
                    raise ValueError("C5 transition must return a mapping")
                if next_state.get("marker") == STUB_RESPONSE or next_state.get("status") == STUB_RESPONSE:
                    had_stub = True
                    if not allow_stubs:
                        continue
                next_energy, next_gap, next_eligibility, time_delta = _transition_numbers(next_state, current_energy=energy, current_gap=gap, current_eligibility=eligibility)
                candidate = -time_delta + recurse(index + 1, next_energy, next_gap, next_eligibility)
            except Exception as exc:
                exclusions[state_key].append({"action": dict(action), "reason": f"C5 transition unavailable: {exc}", "provenance": "STUB"})
                continue
            if candidate > best_value:
                best_value, best_action = candidate, dict(action)
        if best_action is not None:
            policy[state_key] = best_action
        values[state_key] = best_value
        return best_value

    initial_energy = _snap(_state_value(initial_state, ("energy_mj",), ("energy", "ers_soc_est_mj"), default=0.0), cfg.energy_grid_mj)
    initial_gap = _snap(_state_value(initial_state, ("gap_s",), ("gap", "time_gap_s"), default=0.0), cfg.gap_grid_s)
    initial_eligibility = int(bool(initial_state.get("eligibility", initial_state.get("overtake_eligible", False))))
    value = recurse(0, initial_energy, initial_gap, initial_eligibility)
    if value == float("-inf"):
        status, provenance, reason = "UNAVAILABLE", "RULE", "no legal C3 action reached a valid C5 transition"
    elif had_stub:
        status, provenance, reason = STUB_RESPONSE, "STUB", "development result depends on a C3/C4/C5 stub or unavailable dependency"
    else:
        status, provenance, reason = "COMPLETE", "DERIVED", None
    metadata["dependencies"] = {"C3": "PUBLIC", "C4": STUB_RESPONSE, "C5": "PUBLIC_TRANSITION" if not had_stub else STUB_RESPONSE}
    return DPResult(DP_SCHEMA_VERSION, status, None if value == float("-inf") else value, policy, values, exclusions, metadata, provenance, reason)


__all__ = ["DP_SCHEMA_VERSION", "DPConfig", "DPResult", "solve_dp"]
