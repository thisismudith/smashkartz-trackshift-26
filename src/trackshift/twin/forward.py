"""Forward segment model: from the power balance to a predicted segment time.

CP-20 fits against observed segment time, but the balance in
:mod:`trackshift.twin.power_balance` yields *power*, not time. Something has to
turn one into the other or none of ``CdA``, ``Crr``, ``eta`` or ``P_ICE`` is
identifiable from a lap.

The link used here is the work-energy theorem over one segment. Given the entry
speed and the power available, the segment's exit speed follows::

    (1/2) m (v1^2 - v0^2) = (P_avail - P_drag - P_roll - P_grad) * t
    t = 2L / (v0 + v1)                      # mean-speed traversal

Those two are coupled -- time depends on exit speed and exit speed depends on
time -- so it is solved by fixed-point iteration, which converges in a handful
of passes because the dependence is weak.

**Why predict exit speed rather than time directly.** Predicting time from
kinematics alone (``t = 2L/(v0+v1)`` with both speeds observed) is exact and
completely uninformative: no physics parameter appears in it, so the fit has
nothing to grip. Predicting the *exit speed* from the entry speed and the power
available puts every parameter in the path between input and output.

**Corners are not power-limited.** A slow corner's speed is set by grip, not by
drag or engine power, so a power-limited prediction there is wrong in a way no
parameter choice fixes. The model therefore takes an observed cornering-speed
ceiling per segment and applies it, which keeps corner rows in the fit as
constraints on the mass-carrying terms without pretending their speed came from
the power balance.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .power_balance import (
    PhysicsParameters,
    TwinError,
    drag_power_kw,
    gradient_power_kw,
    kmh_to_mps,
    rolling_power_kw,
)

__all__ = ["predict_exit_speed_mps", "predict_segment_time_s",
           "predict_segment_time_grip_or_power", "steady_state_speed_mps", "ForwardError"]

_MAX_ITERATIONS = 12
_TOLERANCE_MPS = 0.01


class ForwardError(ValueError):
    """The forward model cannot be evaluated with the inputs supplied."""


def predict_exit_speed_mps(
    entry_speed_kmh: Any,
    segment_length_m: Any,
    parameters: PhysicsParameters,
    *,
    rho_kgm3: float,
    available_electrical_kw: float = 0.0,
    gradient: Any = 0.0,
    wind_head_component_mps: Any = 0.0,
    speed_ceiling_kmh: Any = None,
) -> tuple[float, float]:
    """Exit speed and traversal time for one segment. Returns (v1_mps, t_s)."""
    v0 = kmh_to_mps(entry_speed_kmh)
    length = float(segment_length_m)
    if length <= 0:
        raise ForwardError(f"segment_length_m must be positive, got {segment_length_m!r}")
    if v0 <= 0:
        # A standing start has no mean-speed traversal; fall back to the ceiling.
        v0 = 1.0

    ceiling = None if speed_ceiling_kmh is None else kmh_to_mps(speed_ceiling_kmh)
    available = max(0.0, parameters.p_ice_max_kw + max(0.0, available_electrical_kw))

    v1 = v0
    for _ in range(_MAX_ITERATIONS):
        v_mean = max(0.5, (v0 + v1) / 2.0)
        t = length / v_mean
        resistive = (
            drag_power_kw(v_mean + float(wind_head_component_mps or 0.0), rho_kgm3, parameters.cda_m2)
            + rolling_power_kw(v_mean, parameters.mass_kg, parameters.crr, parameters.gravity_mps2)
            + gradient_power_kw(v_mean, parameters.mass_kg, gradient, parameters.gravity_mps2)
        )
        net_kw = available * parameters.eta_drivetrain - resistive
        # Work done over the segment, converted from kW*s to J.
        energy_j = net_kw * 1000.0 * t
        v1_squared = v0 * v0 + 2.0 * energy_j / parameters.mass_kg
        # A negative square means the car is decelerating harder than the model
        # can represent; clamp to a crawl rather than taking a root of a
        # negative number and losing the row entirely.
        new_v1 = math.sqrt(v1_squared) if v1_squared > 0.25 else 0.5
        if ceiling is not None:
            new_v1 = min(new_v1, ceiling)
        if abs(new_v1 - v1) < _TOLERANCE_MPS:
            v1 = new_v1
            break
        v1 = new_v1

    v_mean = max(0.5, (v0 + v1) / 2.0)
    return v1, length / v_mean


def predict_segment_time_s(row: Mapping[str, Any], parameters: PhysicsParameters,
                           *, fallback_rho_kgm3: float = 1.225) -> float:
    """Predicted traversal time for one segment row.

    Expects ``entry_speed_kmh`` and ``segment_length_m``. Everything else is
    optional and degrades to a documented default rather than failing the row.
    """
    rho = row.get("air_density_proxy")
    try:
        rho = float(rho)
        if not math.isfinite(rho) or rho <= 0:
            rho = fallback_rho_kgm3
    except (TypeError, ValueError):
        rho = fallback_rho_kgm3

    def number(key: str, default: float = 0.0) -> float:
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    # The ceiling must be a property of the tarmac, not of this lap. C2's field
    # baseline max speed for the segment is exactly that: a prior over every
    # car that has run it. Using the lap's own observed maximum instead makes
    # the prediction partly circular -- it would be told the answer for every
    # grip-limited corner, which is most of them.
    ceiling = row.get("segment_speed_ceiling_kmh")
    if ceiling is None or (isinstance(ceiling, float) and not math.isfinite(ceiling)):
        ceiling = row.get("max_speed_kmh_offline")

    _, seconds = predict_exit_speed_mps(
        row.get("entry_speed_kmh"),
        row.get("segment_length_m"),
        parameters,
        rho_kgm3=rho,
        available_electrical_kw=number("ers_deploy_power_est_kw"),
        gradient=number("gradient"),
        wind_head_component_mps=number("wind_head_component_mps"),
        speed_ceiling_kmh=ceiling,
    )
    return seconds

def steady_state_speed_mps(
    parameters: PhysicsParameters,
    *,
    rho_kgm3: float,
    available_electrical_kw: float = 0.0,
    gradient: Any = 0.0,
    wind_head_component_mps: Any = 0.0,
    ceiling_mps: float = 120.0,
) -> float:
    """Speed at which available power exactly balances resistance.

    Bisection rather than a cubic solve: the resistance curve is monotone in
    speed, so bisection cannot miss the root, and it stays correct if a term is
    later added that makes the closed form intractable.
    """
    available_kw = max(0.0, parameters.p_ice_max_kw + max(0.0, available_electrical_kw))         * parameters.eta_drivetrain

    def surplus(v: float) -> float:
        resistive = (
            drag_power_kw(v + float(wind_head_component_mps or 0.0), rho_kgm3, parameters.cda_m2)
            + rolling_power_kw(v, parameters.mass_kg, parameters.crr, parameters.gravity_mps2)
            + gradient_power_kw(v, parameters.mass_kg, gradient, parameters.gravity_mps2)
        )
        return available_kw - resistive

    low, high = 1.0, max(2.0, ceiling_mps)
    if surplus(high) > 0:
        return high
    for _ in range(40):
        mid = (low + high) / 2.0
        if surplus(mid) > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def predict_segment_time_grip_or_power(
    row: Mapping[str, Any],
    parameters: PhysicsParameters,
    *,
    fallback_rho_kgm3: float = 1.225,
) -> float:
    """Segment time under whichever limit binds: grip or power.

    A segment's mean speed cannot exceed what the tarmac allows, nor what the
    power unit can sustain against drag. Taking the minimum of the two is both
    physically right and the reason this model works where the endpoint-average
    traversal did not:

      * In a corner the grip limit binds, the prediction falls back to the C2
        baseline, and the physics parameters correctly have no influence. A
        corner time says nothing about CdA and should not be asked to.
      * On a straight the power limit binds, and every parameter is in the path
        from input to output -- which is where the fit gets its signal.

    The previous formulation predicted time from the endpoint speeds under a
    linear speed change. A segment running 300 - 100 - 300 km/h through an apex
    has a mean speed nowhere near its endpoint average, so that model was wrong
    by the largest amount exactly where most segments live.
    """
    rho = row.get("air_density_proxy")
    try:
        rho = float(rho)
        if not math.isfinite(rho) or rho <= 0:
            rho = fallback_rho_kgm3
    except (TypeError, ValueError):
        rho = fallback_rho_kgm3

    def number(key: str, default: float | None = 0.0) -> float | None:
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    length = number("segment_length_m", None)
    if not length or length <= 0:
        raise ForwardError("segment_length_m must be positive")

    # Mass is chassis plus fuel, and the fuel part is the only thing that moves
    # lap to lap. Leaving it out pins mass at the chassis minimum for every row,
    # which gives the fit no lever that varies -- and a fit with no varying
    # lever will minimise its own correction, which is exactly what happened.
    fuel = number("fuel_load_kg_est", None)
    if fuel is not None and fuel >= 0:
        parameters = replace(parameters, mass_kg=parameters.mass_kg + fuel)

    # Grip limit: the segment's own baseline mean speed, a property of the track.
    baseline_time = number("segment_baseline_time_s", None)
    grip_mps = (length / baseline_time) if baseline_time and baseline_time > 0 else None

    if grip_mps is None:
        # No baseline for this segment: fall back to the steady-state speed,
        # which is all the physics can say on its own.
        ceiling = number("segment_speed_ceiling_kmh", None)
        v_mean = steady_state_speed_mps(
            parameters, rho_kgm3=rho,
            available_electrical_kw=number("ers_deploy_power_est_kw") or 0.0,
            gradient=number("gradient") or 0.0,
            wind_head_component_mps=number("wind_head_component_mps") or 0.0,
            ceiling_mps=(kmh_to_mps(ceiling) if ceiling else 120.0))
        return length / max(0.5, v_mean)

    # The baseline is a strong predictor and the physics is a correction to it,
    # not a replacement. Capping mean speed at the steady-state speed was the
    # error in the previous version: a car accelerating through a segment is
    # legitimately below terminal speed and one decelerating is legitimately
    # above it, so that cap bound on most segments and made predictions twice
    # as bad as the baseline alone.
    #
    # The cap now binds only where the physics actually says it must -- where
    # sustaining the baseline speed would demand more power than the car has.
    # Drag dominates at those speeds and goes as v^3, so the speed a shortfall
    # costs scales as the cube root of the power ratio.
    available_kw = max(0.0, parameters.p_ice_max_kw
                       + max(0.0, number("ers_deploy_power_est_kw") or 0.0))         * parameters.eta_drivetrain
    # Acceleration is where mass actually bites. At 300 km/h a 50 kg fuel load
    # changes rolling resistance by about 6 kW against 370 kW of drag -- nothing.
    # The same 50 kg accelerating out of a corner costs real time, and a
    # steady-state balance cannot see it. Without this term the fit has no
    # meaningful mass lever and will minimise its own correction.
    entry_mps = kmh_to_mps(number("entry_speed_kmh", 0.0) or 0.0)
    exit_ref = number("segment_exit_speed_ref_kmh", None)
    inertial_kw = 0.0
    if exit_ref is not None and entry_mps > 0:
        exit_mps = kmh_to_mps(exit_ref)
        traversal_s = length / max(0.5, grip_mps)
        # Mean inertial power over the segment: the kinetic-energy change the
        # car must fund, spread across the time it has to fund it.
        inertial_kw = (parameters.mass_kg * (exit_mps ** 2 - entry_mps ** 2)
                       / (2.0 * traversal_s)) / 1000.0

    required_kw = (
        drag_power_kw(grip_mps + (number("wind_head_component_mps") or 0.0),
                      rho, parameters.cda_m2)
        + rolling_power_kw(grip_mps, parameters.mass_kg, parameters.crr,
                           parameters.gravity_mps2)
        + gradient_power_kw(grip_mps, parameters.mass_kg, number("gradient") or 0.0,
                            parameters.gravity_mps2)
        + max(0.0, inertial_kw)
    )
    if available_kw <= 0 or required_kw <= available_kw:
        return length / max(0.5, grip_mps)
    v_mean = grip_mps * (available_kw / required_kw) ** (1.0 / 3.0)
    return length / max(0.5, v_mean)

