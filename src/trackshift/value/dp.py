"""Development dynamic-programming core (M22).

The development objective is an abstract utility, not elapsed time. All grid
and utility values come from the versioned development configuration and
missing decision-time inputs fail closed instead of becoming zero/false.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from math import isfinite
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from trackshift.rules import api as c3
from .state import STUB_RESPONSE, c3_candidate_actions, c3_excluded_actions

DP_SCHEMA_VERSION = "m22_dp_development_v2"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config/value/dp_development.yaml"


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def load_dp_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the versioned, development-only DP configuration."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"DP development config unavailable: {config_path}") from exc
    if not isinstance(raw, Mapping) or raw.get("development_only") is not True:
        raise ValueError("DP config must explicitly declare development_only: true")
    required = ("schema_version", "energy_grid_mj", "gap_grid_s", "eligibility_states", "terminal_utility", "shadow_finite_difference_delta_mj")
    missing = [key for key in required if key not in raw]
    if missing:
        raise ValueError("DP config missing required field(s): " + ", ".join(missing))
    energy = tuple(_finite(x) for x in raw["energy_grid_mj"])
    gap = tuple(_finite(x) for x in raw["gap_grid_s"])
    if any(x is None for x in energy + gap) or not energy or not gap:
        raise ValueError("DP config grids must contain finite numeric values")
    utility = raw["terminal_utility"]
    if not isinstance(utility, Mapping) or utility.get("units") != "abstract_utility":
        raise ValueError("DP terminal utility must declare abstract_utility units")
    ahead = _finite(utility.get("ahead_value"))
    residual = _finite(utility.get("residual_energy_value_per_mj"))
    delta = _finite(raw.get("shadow_finite_difference_delta_mj"))
    eligibility = tuple(int(x) for x in raw["eligibility_states"])
    if ahead is None or residual is None or delta is None or delta <= 0 or not eligibility:
        raise ValueError("DP utility, eligibility and finite-difference values are invalid")
    return {
        "schema_version": str(raw["schema_version"]), "development_only": True,
        "description": str(raw.get("description", "")),
        "energy_grid_mj": tuple(float(x) for x in energy), "gap_grid_s": tuple(float(x) for x in gap),
        "eligibility_states": eligibility,
        "terminal_utility": {"ahead_value": ahead, "residual_energy_value_per_mj": residual, "units": "abstract_utility"},
        "shadow_finite_difference_delta_mj": delta, "path": str(config_path),
    }


@dataclass(frozen=True)
class DPConfig:
    """Runtime metadata plus values loaded from the versioned config."""

    config_path: str | None = None
    seed: int | None = None
    split_version: str = "not_applicable"
    rule_configuration_version: str | None = None
    model_versions: Mapping[str, str] = field(default_factory=dict)
    energy_grid_mj: tuple[float, ...] | None = None
    gap_grid_s: tuple[float, ...] | None = None
    eligibility_states: tuple[int, ...] | None = None
    ahead_value: float | None = None
    residual_energy_value_per_mj: float | None = None
    shadow_delta_energy_mj: float | None = None
    config_version: str | None = None
    development_only: bool | None = None

    def __post_init__(self) -> None:
        loaded = load_dp_config(self.config_path)
        supplied = (self.energy_grid_mj, self.gap_grid_s, self.eligibility_states, self.ahead_value, self.residual_energy_value_per_mj, self.shadow_delta_energy_mj)
        if any(value is not None for value in supplied):
            raise ValueError("DP grid, utility and shadow delta must be supplied by versioned config; use config_path")
        object.__setattr__(self, "energy_grid_mj", loaded["energy_grid_mj"])
        object.__setattr__(self, "gap_grid_s", loaded["gap_grid_s"])
        object.__setattr__(self, "eligibility_states", loaded["eligibility_states"])
        object.__setattr__(self, "ahead_value", loaded["terminal_utility"]["ahead_value"])
        object.__setattr__(self, "residual_energy_value_per_mj", loaded["terminal_utility"]["residual_energy_value_per_mj"])
        object.__setattr__(self, "shadow_delta_energy_mj", loaded["shadow_finite_difference_delta_mj"])
        object.__setattr__(self, "config_version", loaded["schema_version"])
        object.__setattr__(self, "development_only", True)
        if self.seed is None:
            object.__setattr__(self, "seed", 20260913)

    @property
    def terminal_utility_definition(self) -> dict[str, Any]:
        return {"ahead_value": self.ahead_value, "residual_energy_value_per_mj": self.residual_energy_value_per_mj, "units": "abstract_utility"}


@dataclass
class DPResult:
    schema_version: str
    status: str
    value: float | None
    policy: dict[str, dict[str, Any]]
    value_table: dict[str, float | None]
    excluded_actions: dict[str, list[dict[str, Any]]]
    metadata: dict[str, Any]
    provenance: str
    reason: str | None = None
    #: Per state key, the value of EVERY legal first action -- not just the argmax.
    #: The recursion already computes these to pick a best; keeping them is what
    #: lets a caller report an expected value and a regret for each alternative
    #: instead of a column of "Unavailable". Deliberately NOT in ``to_dict``:
    #: it is one entry per action per visited state and would dwarf the response.
    action_values: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {"schema_version": self.schema_version, "status": self.status, "value": self.value, "policy": self.policy, "value_table": self.value_table, "excluded_actions": self.excluded_actions, "metadata": self.metadata, "provenance": self.provenance, "reason": self.reason}
        return json.loads(json.dumps(payload, allow_nan=False))


def _quantity(state: Mapping[str, Any], *paths: tuple[str, ...]) -> tuple[float | None, str | None, str | None]:
    for path in paths:
        node: Any = state
        present = True
        for part in path:
            if not isinstance(node, Mapping) or part not in node:
                present = False
                break
            node = node[part]
        if not present:
            continue
        provenance = node.get("provenance") if isinstance(node, Mapping) else None
        value = node.get("value", node.get("mean")) if isinstance(node, Mapping) else node
        number = _finite(value)
        if number is None:
            return None, str(provenance or "DERIVED"), f"{'.'.join(path)} is unavailable or non-finite"
        return number, str(provenance or "DERIVED"), None
    return None, None, f"{'.'.join(paths[0])} is missing"


def _eligibility(state: Mapping[str, Any]) -> tuple[int | None, str | None, str | None]:
    candidates: list[tuple[Any, str | None, str]] = []
    for key in ("eligibility", "overtake_eligible"):
        if key in state:
            value = state[key]
            candidates.append((value, value.get("provenance") if isinstance(value, Mapping) else None, key))
    overtake = state.get("overtake_state")
    if isinstance(overtake, Mapping):
        if "armed" in overtake:
            candidates.append((overtake["armed"], overtake.get("provenance"), "overtake_state.armed"))
        elif "state" in overtake:
            candidates.append((overtake["state"], overtake.get("provenance"), "overtake_state.state"))
    if not candidates:
        return None, None, "eligibility is missing"
    value, provenance, source = candidates[0]
    if isinstance(value, Mapping):
        value = value.get("value")
    if isinstance(value, bool):
        return int(value), str(provenance or "RULE"), None
    if isinstance(value, str):
        normalized = value.upper()
        if normalized in {"ARMED", "ACTIVE", "ELIGIBLE"}:
            return 1, str(provenance or "RULE"), None
        if normalized in {"NOT_ARMED", "UNARMED", "DISABLED", "INELIGIBLE"}:
            return 0, str(provenance or "RULE"), None
    numeric = _finite(value)
    if numeric in (0.0, 1.0):
        return int(numeric), str(provenance or "RULE"), None
    return None, str(provenance or "RULE"), f"{source} is unavailable or invalid"


def required_state_inputs(state: Mapping[str, Any]) -> dict[str, Any]:
    energy, energy_prov, energy_reason = _quantity(state, ("energy_mj",), ("energy", "ers_soc_est_mj"))
    gap, gap_prov, gap_reason = _quantity(state, ("gap_s",), ("gap", "time_gap_s"))
    eligibility, eligibility_prov, eligibility_reason = _eligibility(state)
    missing = [reason for reason in (energy_reason, gap_reason, eligibility_reason) if reason]
    return {"ok": not missing, "energy": energy, "gap": gap, "eligibility": eligibility, "provenance": {"energy": energy_prov, "gap": gap_prov, "eligibility": eligibility_prov}, "reason": "; ".join(missing) if missing else None}


def _snap(value: float, grid: Sequence[float]) -> float:
    return min(grid, key=lambda candidate: abs(candidate - value))


def _key(segment_index: int, energy: float, gap: float, eligibility: int) -> str:
    return f"{segment_index}:{energy:.3f}:{gap:.3f}:{eligibility}"


def _with_decision_context(base: Mapping[str, Any], segment: Mapping[str, Any], energy: float, gap: float, eligibility: int) -> dict[str, Any]:
    state = dict(base)
    ref = dict(state.get("ref") or {})
    for name in ("segment_id", "lap", "distance_m", "segment_duration_s"):
        if name in segment:
            ref[name] = segment[name]
    state["ref"] = ref
    if "speed_kmh" in segment:
        state["speed_kmh"] = segment["speed_kmh"]
    energy_block = dict(state.get("energy") or {})
    energy_block["ers_soc_est_mj"] = {"value": energy, "provenance": "SIMULATED", "unit": "MJ", "reason": "DP grid state"}
    state["energy"] = energy_block
    gap_block = dict(state.get("gap") or {})
    gap_block["time_gap_s"] = {"value": gap, "provenance": "DERIVED", "unit": "s", "reason": "DP grid state"}
    state["gap"] = gap_block
    overtake = state.get("overtake_state")
    if isinstance(overtake, Mapping):
        state["overtake_state"] = dict(overtake) | {"armed": bool(eligibility)}
    else:
        state["eligibility"] = eligibility
    return state


def _transition_numbers(next_state: Mapping[str, Any]) -> tuple[float, float, int, float] | None:
    energy, _, _ = _quantity(next_state, ("energy_mj",), ("energy", "ers_soc_est_mj"))
    gap, _, _ = _quantity(next_state, ("gap_s",), ("gap", "time_gap_s"))
    eligibility, _, _ = _eligibility(next_state)
    time_delta, _, _ = _quantity(next_state, ("time_delta_s",), ("segment_time_s",))
    if energy is None or gap is None or eligibility is None or time_delta is None:
        return None
    return energy, gap, eligibility, time_delta


def _unavailable(metadata: dict[str, Any], reason: str, provenance: str = "DERIVED") -> DPResult:
    metadata["unavailable"] = {"value": None, "reason": reason, "provenance": provenance}
    return DPResult(DP_SCHEMA_VERSION, "UNAVAILABLE", None, {}, {}, {}, metadata, provenance, reason)


def solve_dp(
    segments: Sequence[Mapping[str, Any]], initial_state: Mapping[str, Any], event_rules: Mapping[str, Any] | None, *,
    transition_fn: Callable[..., Mapping[str, Any]] | None = None, legal_actions_fn: Callable[..., Mapping[str, Any]] | None = None,
    config: DPConfig | None = None, allow_stubs: bool = True, final_mode: bool = False,
) -> DPResult:
    """Solve the causal development DP with explicit unavailable handling."""
    cfg = config or DPConfig()
    if final_mode:
        raise ValueError("final mode rejects development-only DP output; calibrated time objective is required")
    loaded = load_dp_config(cfg.config_path)
    metadata = {
        "config_version": cfg.config_version, "config_path": loaded["path"], "development_only": True,
        "terminal_utility": cfg.terminal_utility_definition, "grid": {"energy_mj": list(cfg.energy_grid_mj or ()), "gap_s": list(cfg.gap_grid_s or ()), "eligibility": list(cfg.eligibility_states or ())},
        "horizon_segments": len(segments), "rule_configuration_version": cfg.rule_configuration_version, "split_version": cfg.split_version, "model_versions": dict(cfg.model_versions), "seed": cfg.seed,
        "c3_public_boundary": True, "excluded_actions_are_audit_only": True,
    }
    if not isinstance(initial_state, Mapping):
        return _unavailable(metadata, "current StrategicState is missing")
    inputs = required_state_inputs(initial_state)
    metadata["input_provenance"] = inputs["provenance"]
    if not inputs["ok"]:
        return _unavailable(metadata, inputs["reason"] or "required current-state input unavailable")
    if not segments:
        return _unavailable(metadata, "no segments supplied")
    if not isinstance(event_rules, Mapping) or not event_rules:
        return _unavailable(metadata, "C3 event rules unavailable", "RULE")
    if transition_fn is None:
        metadata["dependencies"] = {"C3": "PUBLIC", "C4": STUB_RESPONSE, "C5": STUB_RESPONSE}
        return DPResult(DP_SCHEMA_VERSION, STUB_RESPONSE, None, {}, {}, {}, metadata, "STUB", "C5 segment transition unavailable; provide a public C5 transition_fn")
    action_fn = legal_actions_fn or c3.legal_actions
    values: dict[str, float | None] = {}
    policy: dict[str, dict[str, Any]] = {}
    exclusions: dict[str, list[dict[str, Any]]] = {}
    action_values: dict[str, list[dict[str, Any]]] = {}
    had_stub = False

    def recurse(index: int, energy: float, gap: float, eligibility: int) -> float | None:
        nonlocal had_stub
        energy = _snap(energy, cfg.energy_grid_mj or ())
        gap = _snap(gap, cfg.gap_grid_s or ())
        if index >= len(segments):
            return float(cfg.ahead_value if gap < 0 else 0.0) + float(cfg.residual_energy_value_per_mj) * energy
        state_key = _key(index, energy, gap, eligibility)
        if state_key in values:
            return values[state_key]
        segment = segments[index]
        state = _with_decision_context(initial_state, segment, energy, gap, eligibility)
        try:
            action_set = action_fn(state, event_rules)
        except Exception as exc:
            values[state_key] = None
            exclusions[state_key] = [{"reason": f"C3 unavailable: {exc}", "provenance": "RULE"}]
            return None
        if not isinstance(action_set, Mapping) or action_set.get("available") is False:
            values[state_key] = None
            exclusions[state_key] = [{"reason": str(action_set.get("reason", "C3 unavailable")) if isinstance(action_set, Mapping) else "C3 unavailable", "provenance": "RULE"}]
            return None
        if action_set.get("stub") or action_set.get("marker") == STUB_RESPONSE:
            had_stub = True
        actions = c3_candidate_actions(action_set)
        exclusions[state_key] = c3_excluded_actions(action_set)
        if not actions:
            values[state_key] = None
            return None
        best_value: float | None = None
        best_action: dict[str, Any] | None = None
        scored: list[dict[str, Any]] = []
        for action in actions:
            try:
                next_state = transition_fn(state, dict(action), segment)
                if not isinstance(next_state, Mapping):
                    raise ValueError("C5 transition must return a mapping")
                if next_state.get("marker") == STUB_RESPONSE or next_state.get("status") == STUB_RESPONSE:
                    had_stub = True
                    if not allow_stubs:
                        continue
                numbers = _transition_numbers(next_state)
                if numbers is None:
                    exclusions[state_key].append({"action": dict(action), "reason": "C5 transition omitted energy, gap, eligibility, or time delta", "provenance": "STUB"})
                    continue
                next_energy, next_gap, next_eligibility, time_delta = numbers
                future = recurse(index + 1, next_energy, next_gap, next_eligibility)
                if future is None:
                    continue
                candidate = -time_delta + future
                if not isfinite(candidate):
                    continue
            except Exception as exc:
                exclusions[state_key].append({"action": dict(action), "reason": f"C5 transition unavailable: {exc}", "provenance": "STUB"})
                continue
            scored.append({"action": dict(action), "value": candidate})
            if best_value is None or candidate > best_value:
                best_value, best_action = candidate, dict(action)
        action_values[state_key] = scored
        if best_action is not None:
            policy[state_key] = best_action
        values[state_key] = best_value
        return best_value

    value = recurse(0, float(inputs["energy"]), float(inputs["gap"]), int(inputs["eligibility"]))
    if value is None:
        status, provenance, reason = "UNAVAILABLE", "RULE", "no legal C3 action reached a complete C5 transition"
    elif had_stub:
        status, provenance, reason = STUB_RESPONSE, "STUB", "development result depends on a C3/C4/C5 stub or unavailable dependency"
    else:
        status, provenance, reason = "COMPLETE", "DERIVED", None
    metadata["dependencies"] = {"C3": "PUBLIC", "C4": STUB_RESPONSE, "C5": "PUBLIC_TRANSITION" if not had_stub else STUB_RESPONSE}
    result = DPResult(DP_SCHEMA_VERSION, status, value, policy, values, exclusions, metadata, provenance, reason, action_values)
    return result


__all__ = ["DP_SCHEMA_VERSION", "DEFAULT_CONFIG_PATH", "DPConfig", "DPResult", "load_dp_config", "required_state_inputs", "solve_dp"]
