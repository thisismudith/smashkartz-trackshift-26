"""Finite-difference energy shadow price for M22."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Mapping, Sequence

from .dp import DPConfig, DPResult, solve_dp


def shadow_price(
    segments: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    event_rules: Mapping[str, Any] | None,
    *,
    transition_fn: Callable[..., Mapping[str, Any]] | None = None,
    legal_actions_fn: Callable[..., Mapping[str, Any]] | None = None,
    config: DPConfig | None = None,
    delta_energy_mj: float = 0.1,
) -> dict[str, Any]:
    """Compute ΔV/ΔE at the same complete state and state grid.

    Both solves use identical gap, eligibility, horizon, rules, models and
    transition callbacks; only Energy Store energy is perturbed.
    """
    if delta_energy_mj <= 0:
        raise ValueError("delta_energy_mj must be positive")
    cfg = config or DPConfig()
    base = solve_dp(segments, state, event_rules, transition_fn=transition_fn, legal_actions_fn=legal_actions_fn, config=cfg)
    higher = dict(state)
    energy = dict(higher.get("energy") or {})
    current = energy.get("ers_soc_est_mj", higher.get("energy_mj", 0.0))
    if isinstance(current, Mapping):
        current = current.get("value", current.get("mean", 0.0))
    energy["ers_soc_est_mj"] = {"value": float(current or 0.0) + delta_energy_mj, "provenance": "SIMULATED", "unit": "MJ"}
    higher["energy"] = energy
    higher["energy_mj"] = float(current or 0.0) + delta_energy_mj
    bumped = solve_dp(segments, higher, event_rules, transition_fn=transition_fn, legal_actions_fn=legal_actions_fn, config=cfg)
    same_state = all(base.metadata.get(key) == bumped.metadata.get(key) for key in ("grid", "horizon_segments", "rule_configuration_version", "split_version", "model_versions", "seed"))
    if base.value is None or bumped.value is None:
        value = None
    else:
        value = (bumped.value - base.value) / delta_energy_mj
    return {
        "value_s_per_mj": value,
        "value_s_per_kj": None if value is None else value / 1000.0,
        "delta_energy_mj": delta_energy_mj,
        "base": base.to_dict(), "bumped": bumped.to_dict(),
        "same_full_state_except_energy": same_state,
        "provenance": "DERIVED" if value is not None and same_state else base.provenance,
        "status": "COMPLETE" if value is not None and same_state else base.status,
    }


__all__ = ["shadow_price"]
