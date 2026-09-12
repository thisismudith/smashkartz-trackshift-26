"""Finite-difference marginal utility for M22.

The development DP has an abstract terminal utility. Consequently its
marginal value is reported as utility/MJ, never as seconds/MJ.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .dp import DPConfig, load_dp_config, required_state_inputs, solve_dp
from .state import STUB_RESPONSE, reject_stubs_for_final


def shadow_price(
    segments: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    event_rules: Mapping[str, Any] | None,
    *,
    transition_fn: Callable[..., Mapping[str, Any]] | None = None,
    legal_actions_fn: Callable[..., Mapping[str, Any]] | None = None,
    config: DPConfig | None = None,
    delta_energy_mj: float | None = None,
    final_mode: bool = False,
) -> dict[str, Any]:
    """Compute Δ abstract utility / Δ energy at the same complete state."""
    cfg = config or DPConfig()
    delta = cfg.shadow_delta_energy_mj if delta_energy_mj is None else float(delta_energy_mj)
    if delta is None or delta <= 0:
        raise ValueError("shadow finite-difference delta must be positive and come from config")
    loaded = load_dp_config(cfg.config_path)
    base = solve_dp(segments, state, event_rules, transition_fn=transition_fn, legal_actions_fn=legal_actions_fn, config=cfg)
    inputs = required_state_inputs(state) if isinstance(state, Mapping) else {"ok": False, "reason": "current StrategicState is missing"}
    current = inputs.get("energy")
    if current is None:
        response = {
            "status": "UNAVAILABLE", "provenance": inputs.get("provenance", {}).get("energy") or "DERIVED",
            "reason": inputs.get("reason") or "energy is unavailable", "marginal_value_per_mj": None,
            "marginal_value_per_kj": None,
        }
        if final_mode:
            raise ValueError("final mode rejects unavailable shadow-price inputs")
        return response
    higher = dict(state)
    energy = dict(higher.get("energy") or {})
    energy["ers_soc_est_mj"] = {"value": float(current) + delta, "provenance": "SIMULATED", "unit": "MJ", "reason": "counterfactual finite-difference perturbation"}
    higher["energy"] = energy
    higher["energy_mj"] = float(current) + delta
    bumped = solve_dp(segments, higher, event_rules, transition_fn=transition_fn, legal_actions_fn=legal_actions_fn, config=cfg)
    same_state = all(base.metadata.get(key) == bumped.metadata.get(key) for key in ("grid", "horizon_segments", "rule_configuration_version", "split_version", "model_versions", "seed", "config_version"))
    marginal = None if base.value is None or bumped.value is None else (bumped.value - base.value) / delta
    complete = marginal is not None and same_state and base.status == "COMPLETE" and bumped.status == "COMPLETE"
    reason = None if complete else ("abstract utility or dependency stub/unavailable input; calibrated C4/C5 time objective required for seconds/MJ" if marginal is not None else (base.reason or bumped.reason or "shadow-price inputs unavailable"))
    response = {
        "schema_version": "m22_shadow_price_development_v2", "status": "COMPLETE" if complete else (STUB_RESPONSE if STUB_RESPONSE in {base.status, bumped.status} else "UNAVAILABLE"),
        "provenance": "DERIVED" if complete else base.provenance,
        "marginal_value_per_mj": marginal, "marginal_value_per_kj": None if marginal is None else marginal / 1000.0,
        "marginal_value_units": "abstract_utility_per_mj", "marginal_value_per_kj_units": "abstract_utility_per_kj",
        "time_based_shadow_price": {"value": None, "unit": "s/MJ", "provenance": "DERIVED", "reason": "not emitted: development terminal utility is abstract and C4/C5 are not verified calibrated time inputs"},
        "delta_energy_mj": delta, "same_full_state_except_energy": same_state,
        "terminal_utility": cfg.terminal_utility_definition, "config_version": cfg.config_version, "config_path": loaded["path"], "development_only": True,
        "base": base.to_dict(), "bumped": bumped.to_dict(), "reason": reason,
    }
    if final_mode:
        reject_stubs_for_final(response)
        if not complete:
            raise ValueError("final mode rejects abstract-only or unavailable shadow price")
    return response


__all__ = ["shadow_price"]
