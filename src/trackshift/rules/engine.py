"""Deterministic 2026 rule engine (M19, CP-11), contract C3.

Illegal actions are **absent** from the returned set, never low-scored
(AGENTS.md sections 31, 32). A planner that scores an illegal action low is a
planner that will eventually pick it; the only safe design is that it never
enters the candidate set.

This module is the sole owner of the speed-dependent electrical power envelope.
``max_electrical_power_kw`` is the one implementation in the system, and no
envelope constant may appear anywhere else (section 32). ``deploy_level`` is a
fraction of the cap **at the current speed**, not of a fixed 350 kW, which is
why :func:`legal_actions` requires ``speed_kmh`` in the state and returns
``cap_kw`` and ``delivered_power_kw`` per action: the planner must account
energy against what is actually deliverable, not against what it requested.

Every value consumed here comes through :func:`trackshift.rules.config.resolve`,
so an unannotated number cannot reach a decision, and strict mode refuses
``UNVERIFIED``/``PROXY_*`` values without this module needing to know the tiers.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .config import FinalModeError, RuleConfigError, assert_final_mode_rules, resolve
from trackshift.data.registry import assert_final_feature_boundary

__all__ = [
    "DEPLOY_LEVELS",
    "LIFT_AMOUNTS",
    "MODES",
    "ENGINE_SCHEMA_VERSION",
    "Action",
    "Excluded",
    "RuleEngineError",
    "UnknownEvent",
    "RuleKeyMissing",
    "max_electrical_power_kw",
    "envelope_curve",
    "envelope_table",
    "verify_envelope_table",
    "separation_speed_kmh",
    "applicable_mode_for",
    "legal_actions",
    "stub_action_set",
]

ENGINE_SCHEMA_VERSION = "c3_rule_engine_v1"

#: Section 31 action space. 5 x 3 = 15 candidates before filtering.
DEPLOY_LEVELS: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75, 1.0)
LIFT_AMOUNTS: tuple[float, ...] = (0.0, 0.25, 0.5)
MODES: tuple[str, ...] = ("normal", "override")

#: Coasting. Must survive every filter at every speed, or the engine is broken.
COAST = (0.0, 0.0)

_KW_TOLERANCE = 1e-9
_MJ_TOLERANCE = 1e-12


class RuleEngineError(RuntimeError):
    """Base class for every refusal this engine makes."""


class UnknownEvent(RuleEngineError):
    """The event has no rule configuration. C3 never falls back to a default."""


class RuleKeyMissing(RuleEngineError):
    """A consumed rule key is absent.

    Never downgraded to a default: a missing limit must not read as "no limit".
    """


# --------------------------------------------------------------------------
# The envelope evaluator. Section 32 makes this module its sole owner.
# --------------------------------------------------------------------------

def envelope_curve(event_rules: Mapping[str, Any], mode: str) -> tuple[np.ndarray, np.ndarray]:
    """Breakpoints and caps for one mode, with provenance enforced by resolve()."""
    if not isinstance(mode, str) or mode.lower() not in MODES:
        raise RuleKeyMissing(
            f"unknown envelope mode {mode!r}; expected one of {MODES}. The mode selects "
            "which curve applies and has no default (section 20.1)."
        )
    key = f"power_envelope.{mode.lower()}"
    try:
        resolved = resolve(event_rules, key)
    except RuleConfigError as exc:
        raise RuleKeyMissing(str(exc)) from exc

    curve = resolved.value
    if not isinstance(curve, Mapping):
        raise RuleKeyMissing(f"rule key '{key}' is not a curve block")
    breakpoints = curve.get("breakpoints_kmh")
    powers = curve.get("max_power_kw")
    if not isinstance(breakpoints, Sequence) or not isinstance(powers, Sequence):
        raise RuleKeyMissing(f"rule key '{key}' needs breakpoints_kmh and max_power_kw")
    if len(breakpoints) != len(powers) or len(breakpoints) < 2:
        raise RuleKeyMissing(
            f"rule key '{key}' has {len(breakpoints)} breakpoint(s) and {len(powers)} "
            "power value(s); a piecewise-linear curve needs at least two of each, paired"
        )

    bps = np.asarray(breakpoints, dtype=float)
    kws = np.asarray(powers, dtype=float)
    if not np.all(np.diff(bps) > 0):
        raise RuleKeyMissing(
            f"rule key '{key}' breakpoints_kmh must be strictly increasing for "
            f"interpolation; got {breakpoints!r}"
        )
    if np.any(kws < 0):
        raise RuleKeyMissing(f"rule key '{key}' has a negative max_power_kw: {powers!r}")
    return bps, kws


def max_electrical_power_kw(speed_kmh: float, mode: str, event_rules: Mapping[str, Any]) -> float:
    """Regulatory electrical power cap at this speed. The ONLY implementation.

    ``np.interp`` clamps outside the breakpoint range, which is the required
    behaviour: below the first breakpoint the cap is the first value, above the
    last it is the last.
    """
    bps, kws = envelope_curve(event_rules, mode)
    return float(np.interp(float(speed_kmh), bps, kws))


def envelope_table(
    event_rules: Mapping[str, Any],
    mode: str,
    *,
    max_speed_kmh: float = 400.0,
    step_kmh: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Precomputed cap over a speed grid, for planners that call this in a hot loop.

    The curve is piecewise linear, so a 1 km/h table is exact at breakpoints and
    negligibly off between them. Check it with :func:`verify_envelope_table`
    before relying on it -- a table that silently disagrees with the function
    would reintroduce the very duplication section 32 forbids.
    """
    if step_kmh <= 0:
        raise ValueError("step_kmh must be positive")
    bps, kws = envelope_curve(event_rules, mode)
    grid = np.arange(0.0, float(max_speed_kmh) + step_kmh, step_kmh)
    return grid, np.interp(grid, bps, kws)


def verify_envelope_table(
    event_rules: Mapping[str, Any],
    mode: str,
    *,
    max_speed_kmh: float = 400.0,
    step_kmh: float = 1.0,
) -> bool:
    """True when the lookup table agrees with the evaluator at every grid point."""
    grid, table = envelope_table(
        event_rules, mode, max_speed_kmh=max_speed_kmh, step_kmh=step_kmh
    )
    direct = np.array([max_electrical_power_kw(s, mode, event_rules) for s in grid])
    return bool(np.allclose(table, direct, rtol=0.0, atol=1e-9))


def separation_speed_kmh(event_rules: Mapping[str, Any]) -> float | None:
    """Speed below which the two curves coincide, so the mode is unobservable.

    Section 20.2: below this the override confers no power advantage, and the
    M35 discriminator must return UNKNOWN rather than NORMAL.
    """
    try:
        resolved = resolve(event_rules, "power_envelope.separation_speed_kmh")
    except RuleConfigError:
        return None
    value = resolved.value
    if isinstance(value, Mapping):
        value = value.get("value")
    return None if value is None else float(value)


# --------------------------------------------------------------------------
# State access. C3 blocks carry {value, provenance, reason}; tests and the DP
# may pass flatter dictionaries, so unwrap either shape without guessing a
# value that was not supplied.
# --------------------------------------------------------------------------

def _unwrap(node: Any) -> Any:
    if isinstance(node, Mapping) and "value" in node:
        return node["value"]
    return node


_MISSING = object()


def _state_get(state: Mapping[str, Any], *path: str, default: Any = None) -> Any:
    """Read a nested C3 path, falling back to the bare leaf name at the top level."""
    node: Any = state
    for part in path:
        node = _unwrap(node)
        if not isinstance(node, Mapping) or part not in node:
            node = _MISSING
            break
        node = node[part]
    if node is not _MISSING:
        return _unwrap(node)
    if path:
        leaf = path[-1]
        if isinstance(state, Mapping) and leaf in state:
            return _unwrap(state[leaf])
    return default


def _require_speed_kmh(state: Mapping[str, Any]) -> float:
    for path in (("ref", "speed_kmh"), ("speed_kmh",), ("power_envelope", "speed_kmh")):
        value = _state_get(state, *path)
        if value is not None:
            return float(value)
    raise RuleKeyMissing(
        "state.speed_kmh is required: the cap is a function of speed, so an action "
        "set built without it would silently apply a constant cap and revert the "
        "whole section 20.1 behaviour."
    )


def _optional_limit(
    event_rules: Mapping[str, Any], key: str, not_applied: list[dict[str, Any]], filter_name: str
) -> float | None:
    """Resolve a numeric limit, recording honestly when it is configured as null.

    A missing *key* raises. A key present with ``value: null`` is a known
    unknown -- the filter is skipped and the skip is reported, rather than the
    engine pretending the constraint does not exist.
    """
    try:
        resolved = resolve(event_rules, key)
    except RuleConfigError as exc:
        raise RuleKeyMissing(str(exc)) from exc
    value = resolved.value
    if isinstance(value, Mapping):
        value = value.get("value")
    if value is None:
        not_applied.append({
            "filter": filter_name,
            "rule": key,
            "value_source": resolved.value_source,
            "reason": f"{key} is null in the configuration ({resolved.source or 'no source'}), "
                      "so this constraint cannot be evaluated and was not applied",
        })
        return None
    return float(value)


def _rule_source(event_rules: Mapping[str, Any], key: str) -> str:
    try:
        return resolve(event_rules, key).source or ""
    except RuleConfigError:
        return ""


# --------------------------------------------------------------------------
# Action set
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    """One legal candidate, with the cap it was evaluated against."""

    deploy_level: float
    lift_amount: float
    applicable_mode: str
    cap_kw: float
    delivered_power_kw: float
    delta_e_mj: float | None


@dataclass(frozen=True)
class Excluded:
    """One removed candidate, naming the rule that removed it (API.md 5.6)."""

    deploy_level: float
    lift_amount: float
    applicable_mode: str
    rule: str
    source: str
    reason: str


def applicable_mode_for(overtake_state: Any, overtake_disabled: bool) -> str:
    """Which envelope curve applies: override only while Overtake is ACTIVE."""
    state = str(overtake_state or "").upper()
    return "override" if state == "ACTIVE" and not overtake_disabled else "normal"


def legal_actions(
    state: Mapping[str, Any],
    event_rules: Mapping[str, Any],
    *,
    stub: bool = False,
    final_mode: bool = False,
) -> dict[str, Any]:
    """Return every legal action for this state, and why the others were removed.

    Parameters
    ----------
    state:
        A C3 StrategicState, or any mapping exposing the same leaves. Must carry
        ``speed_kmh``.
    event_rules:
        Loaded event configuration from :func:`trackshift.rules.config.load_event_rules`.
    stub:
        Return a fixed, correctly shaped set so a planner can develop against C3
        before the regulatory values land (MODELS.md 6.1).
    """
    if final_mode:
        assert_final_feature_boundary(
            state if isinstance(state, Mapping) else {},
            "C3 final action state",
            year=(state.get("year") if isinstance(state, Mapping) else 2026),
        )
        if not isinstance(event_rules, Mapping) or not event_rules:
            raise UnknownEvent("final C3 requires a complete 2026 rule configuration")
        assert_final_mode_rules(event_rules)
        if stub:
            raise FinalModeError("final C3 rejects stub action sets")

    if stub:
        return stub_action_set(state, event_rules)

    if not isinstance(event_rules, Mapping) or not event_rules:
        raise UnknownEvent(
            "no rule configuration supplied; C3 has no default configuration and "
            "must not proceed with an unconfigured event"
        )

    speed_kmh = _require_speed_kmh(state)
    overtake_state = _state_get(state, "overtake_state", "state")
    if overtake_state is None:
        overtake_state = _state_get(state, "overtake_state")
    overtake_disabled = bool(_state_get(state, "race_control", "overtake_disabled", default=False))

    auto_mode = applicable_mode_for(overtake_state, overtake_disabled)
    requested_mode = str(_state_get(state, "power_envelope", "requested_mode", default="auto")).lower()
    if requested_mode not in {"auto", *MODES}:
        raise RuleKeyMissing(
            f"state.power_envelope.requested_mode is {requested_mode!r}; expected 'auto', "
            f"or one of {MODES}"
        )

    # The applicable mode is derived from the state. An explicit request for the
    # override curve is still enumerated when it does not apply, so the
    # eligibility filter can name the rule that refuses it rather than the
    # candidate quietly never existing.
    modes = [auto_mode]
    if requested_mode == "override" and auto_mode != "override":
        modes.append("override")
    elif requested_mode == "normal" and auto_mode != "normal":
        modes = ["normal"]

    not_applied: list[dict[str, Any]] = []
    deploy_limit = _optional_limit(event_rules, "energy.deploy_limit_per_lap_mj", not_applied, "deploy_budget")
    harvest_limit = _optional_limit(event_rules, "energy.harvest_limit_per_lap_mj", not_applied, "harvest_budget")
    store_capacity = _optional_limit(event_rules, "energy.store_capacity_mj", not_applied, "store_capacity")

    soc_mj = _state_get(state, "energy", "ers_soc_est_mj")
    deploy_remaining = _state_get(state, "energy", "ers_deploy_budget_remaining_est_mj")
    harvest_remaining = _state_get(state, "energy", "ers_harvest_budget_remaining_est_mj")
    duration_s = _state_get(state, "ref", "segment_duration_s")
    implied_harvest_mj = _state_get(state, "energy", "implied_harvest_mj")

    if duration_s is None:
        not_applied.append({
            "filter": "deploy_budget",
            "rule": "energy.accounting_window",
            "value_source": None,
            "reason": "state carries no segment_duration_s, so delta_e_mj is unknown and "
                      "energy-flow filters cannot be evaluated for this state",
        })
    if implied_harvest_mj is None:
        not_applied.append({
            "filter": "harvest_budget",
            "rule": "energy.harvest_limit_per_lap_mj",
            "value_source": None,
            "reason": "state carries no implied_harvest_mj; recovery is modelled in CP-18 "
                      "(M14) and until then a lift's recovery cannot be estimated here",
        })

    actions: list[Action] = []
    excluded: list[Excluded] = []

    for mode in modes:
        cap_kw = max_electrical_power_kw(speed_kmh, mode, event_rules)
        for deploy_level in DEPLOY_LEVELS:
            for lift_amount in LIFT_AMOUNTS:
                delivered_kw = float(deploy_level) * cap_kw
                delta_e_mj = (
                    None if duration_s is None
                    else delivered_kw * float(duration_s) / 1000.0
                )
                harvest_mj = (
                    None if implied_harvest_mj is None
                    else float(implied_harvest_mj) * float(lift_amount)
                )

                refusal = _first_refusal(
                    mode=mode,
                    auto_mode=auto_mode,
                    overtake_state=overtake_state,
                    overtake_disabled=overtake_disabled,
                    deploy_level=deploy_level,
                    lift_amount=lift_amount,
                    cap_kw=cap_kw,
                    delivered_kw=delivered_kw,
                    delta_e_mj=delta_e_mj,
                    harvest_mj=harvest_mj,
                    soc_mj=soc_mj,
                    deploy_remaining=deploy_remaining,
                    deploy_limit=deploy_limit,
                    harvest_remaining=harvest_remaining,
                    harvest_limit=harvest_limit,
                    store_capacity=store_capacity,
                    event_rules=event_rules,
                )
                if refusal is None:
                    actions.append(Action(
                        deploy_level=float(deploy_level),
                        lift_amount=float(lift_amount),
                        applicable_mode=mode,
                        cap_kw=cap_kw,
                        delivered_power_kw=delivered_kw,
                        delta_e_mj=delta_e_mj,
                    ))
                else:
                    rule, reason = refusal
                    excluded.append(Excluded(
                        deploy_level=float(deploy_level),
                        lift_amount=float(lift_amount),
                        applicable_mode=mode,
                        rule=rule,
                        source=_rule_source(event_rules, rule),
                        reason=reason,
                    ))

    # Section 31: coasting is always legal. An empty or coast-less set means a
    # filter is wrong; assert rather than quietly repairing it, because a
    # fallback would hide the bug from every consumer downstream.
    if not any((a.deploy_level, a.lift_amount) == COAST for a in actions):
        raise RuleEngineError(
            "coasting (deploy_level 0, lift_amount 0) was filtered out at "
            f"{speed_kmh:g} km/h; it must always be legal, so a filter is wrong. "
            f"Exclusions: {[(e.rule, e.reason) for e in excluded]}"
        )

    return {
        "schema_version": ENGINE_SCHEMA_VERSION,
        "speed_kmh": speed_kmh,
        "applicable_mode": auto_mode,
        "requested_mode": requested_mode,
        "overtake_state": None if overtake_state is None else str(overtake_state),
        "overtake_disabled": overtake_disabled,
        "cap_kw": max_electrical_power_kw(speed_kmh, auto_mode, event_rules),
        "actions": [asdict(a) for a in actions],
        "excluded": [asdict(e) for e in excluded],
        "filters_not_applied": not_applied,
        "provenance": "RULE",
        "stub": False,
    }


def _first_refusal(
    *,
    mode: str,
    auto_mode: str,
    overtake_state: Any,
    overtake_disabled: bool,
    deploy_level: float,
    lift_amount: float,
    cap_kw: float,
    delivered_kw: float,
    delta_e_mj: float | None,
    harvest_mj: float | None,
    soc_mj: Any,
    deploy_remaining: Any,
    deploy_limit: float | None,
    harvest_remaining: Any,
    harvest_limit: float | None,
    store_capacity: float | None,
    event_rules: Mapping[str, Any],
) -> tuple[str, str] | None:
    """The first rule that refuses this candidate, or None if it is legal."""

    # Race control. An OVERTAKE DISABLED window withdraws the override curve;
    # normal deployment is not suspended by it.
    if mode == "override" and overtake_disabled:
        return (
            "race_control.overtake_disabled_conditions",
            "race control has Overtake disabled, so the override envelope is unavailable",
        )

    # Eligibility. The override curve applies only while Overtake is ACTIVE.
    if mode == "override" and str(overtake_state or "").upper() != "ACTIVE":
        return (
            "power_envelope.override",
            f"overtake_state is {overtake_state!r}, not ACTIVE, so the override "
            "envelope does not apply",
        )

    # Power envelope. delivered is a fraction of the cap by construction, so
    # this is a guard: it fires only if a caller supplies a deploy_level above
    # 1.0 or a cap goes negative, either of which must not reach a planner.
    if delivered_kw > cap_kw + _KW_TOLERANCE:
        return (
            f"power_envelope.{mode}",
            f"delivered {delivered_kw:.3f} kW exceeds the {cap_kw:.3f} kW cap at this speed",
        )
    if delivered_kw < -_KW_TOLERANCE:
        return (f"power_envelope.{mode}", f"delivered power {delivered_kw:.3f} kW is negative")

    # Deploy budget. Only evaluable when both the limit and the state are known.
    if delta_e_mj is not None and deploy_level > 0:
        budget = deploy_remaining if deploy_remaining is not None else deploy_limit
        if budget is not None and delta_e_mj > float(budget) + _MJ_TOLERANCE:
            return (
                "energy.deploy_limit_per_lap_mj",
                f"deploying {delta_e_mj:.4f} MJ exceeds the {float(budget):.4f} MJ "
                "remaining in the lap deploy budget",
            )

    # Store capacity. A car cannot deploy energy it does not hold, nor harvest
    # past the store's physical bound. These are separate from the flow limits.
    if delta_e_mj is not None and deploy_level > 0 and soc_mj is not None:
        if delta_e_mj > float(soc_mj) + _MJ_TOLERANCE:
            return (
                "energy.store_capacity_mj",
                f"deploying {delta_e_mj:.4f} MJ exceeds the {float(soc_mj):.4f} MJ "
                "estimated to be in the store",
            )
    if harvest_mj is not None and harvest_mj > 0 and soc_mj is not None and store_capacity is not None:
        if float(soc_mj) + harvest_mj > store_capacity + _MJ_TOLERANCE:
            return (
                "energy.store_capacity_mj",
                f"harvesting {harvest_mj:.4f} MJ would take the store to "
                f"{float(soc_mj) + harvest_mj:.4f} MJ, above its {store_capacity:.4f} MJ capacity",
            )

    # Harvest budget. A separate regulatory constraint from the deploy budget.
    if harvest_mj is not None and harvest_mj > 0:
        budget = harvest_remaining if harvest_remaining is not None else harvest_limit
        if budget is not None and harvest_mj > float(budget) + _MJ_TOLERANCE:
            return (
                "energy.harvest_limit_per_lap_mj",
                f"recovering {harvest_mj:.4f} MJ exceeds the {float(budget):.4f} MJ "
                "remaining in the lap harvest budget",
            )

    return None


def stub_action_set(
    state: Mapping[str, Any] | None = None,
    event_rules: Mapping[str, Any] | None = None,
    *,
    final_mode: bool = False,
) -> dict[str, Any]:
    """A fixed, correctly shaped action set for planner development (MODELS.md 6.1).

    Carries ``stub: true`` so a bundle built on it can be refused at the gate
    rather than shipped as though it were a real rule evaluation.

    ``event_rules`` is still required: the stub short-circuits the *filters*, not
    the envelope. Inventing a cap here would put an envelope constant outside
    ``config/``, which is the one thing section 32 forbids, and a planner
    developed against a fabricated cap would be wrong in exactly the way the
    speed-dependent envelope exists to prevent.
    """
    if final_mode:
        assert_final_feature_boundary(state or {}, "C3 final stub action state")
        raise FinalModeError("final C3 rejects stub action sets")
    if not event_rules:
        raise UnknownEvent(
            "stub_action_set needs event_rules: the cap must come from configuration "
            "even in stub mode (section 32). Load it with load_event_rules()."
        )

    speed_kmh = 0.0
    if state is not None:
        try:
            speed_kmh = _require_speed_kmh(state)
        except RuleKeyMissing:
            speed_kmh = 0.0

    mode = "normal"
    cap_kw = max_electrical_power_kw(speed_kmh, mode, event_rules)

    actions = [
        Action(
            deploy_level=float(deploy_level),
            lift_amount=float(lift_amount),
            applicable_mode=mode,
            cap_kw=cap_kw,
            delivered_power_kw=float(deploy_level) * cap_kw,
            delta_e_mj=None,
        )
        for deploy_level in DEPLOY_LEVELS
        for lift_amount in LIFT_AMOUNTS
    ]
    return {
        "schema_version": ENGINE_SCHEMA_VERSION,
        "speed_kmh": speed_kmh,
        "applicable_mode": mode,
        "requested_mode": "auto",
        "overtake_state": None,
        "overtake_disabled": False,
        "cap_kw": cap_kw,
        "actions": [asdict(a) for a in actions],
        "excluded": [],
        "filters_not_applied": [{
            "filter": "all",
            "rule": None,
            "value_source": None,
            "reason": "stub mode returns a fixed legal set and applies no filters",
        }],
        "provenance": "RULE",
        "stub": True,
    }
