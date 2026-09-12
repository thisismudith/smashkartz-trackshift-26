"""Longitudinal power balance and Energy Store accounting (M14, CP-18).

Every quantity this module produces is ``SIMULATED``. Not ``OBSERVED``, not
``DERIVED``: the car's electrical deployment is not in the public feed, and this
is a model of it. The ``_est`` suffix is part of each name and is never stripped
(AGENTS.md sections 28, 58) -- an estimate that loses its suffix on the way to a
plot is an estimate that will eventually be quoted as a measurement.

The balance (section 28)::

    P_wheel = m*a*v + P_drag + P_rolling + P_gradient
    P_drag     = 0.5 * rho * CdA * v_air**3
    P_rolling  = Crr * m * g * v
    P_gradient = m * g * sin(theta) * v
    P_K        = P_wheel / eta - P_ICE

``v_air`` includes the head-wind component, which is why CP-06 comes first: drag
goes as the cube of air speed, so a 5 m/s headwind at 300 km/h is not a rounding
error.

**Nothing is clamped to the power envelope** (section 28.1). An estimate above
the override cap is not a discovery about the car, it is proof that CdA, mass,
Crr, eta or the ICE map is wrong. Truncating it would convert a visible
calibration bug into an invisible one, so the raw value is kept and the
violation recorded beside it. The violation *rate* is then a first-class
calibration metric in CP-20.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PROVENANCE",
    "PhysicsParameters",
    "SegmentPower",
    "EnergyState",
    "TwinError",
    "kmh_to_mps",
    "air_speed_mps",
    "drag_power_kw",
    "rolling_power_kw",
    "gradient_power_kw",
    "inertial_power_kw",
    "wheel_power_kw",
    "segment_power",
    "integrate_segment",
    "advance_energy_state",
    "trailing_mean",
]

#: Section 28. Never OBSERVED, never DERIVED.
PROVENANCE = "SIMULATED"

_WATTS_PER_KW = 1000.0
_MJ_PER_KWS = 1.0 / 1000.0


class TwinError(ValueError):
    """The balance cannot be evaluated with the inputs supplied."""


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TwinError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise TwinError(f"{name} must be finite, got {number!r}")
    return number


@dataclass(frozen=True)
class PhysicsParameters:
    """The fitted half of the balance. CP-20 moves all of these."""

    mass_kg: float
    cda_m2: float
    crr: float
    eta_drivetrain: float
    p_ice_max_kw: float
    eta_deploy: float = 0.95
    eta_harvest: float = 0.90
    gravity_mps2: float = 9.80665

    def __post_init__(self) -> None:
        if self.mass_kg <= 0:
            raise TwinError(f"mass_kg must be positive, got {self.mass_kg}")
        if self.cda_m2 <= 0:
            raise TwinError(f"cda_m2 must be positive, got {self.cda_m2}; a non-positive "
                            "drag area is not a physical configuration")
        if self.crr < 0:
            raise TwinError(f"crr must not be negative, got {self.crr}")
        for name in ("eta_drivetrain", "eta_deploy", "eta_harvest"):
            value = getattr(self, name)
            if not 0 < value <= 1:
                raise TwinError(f"{name} must lie in (0, 1], got {value}; an efficiency "
                                "above one is energy from nowhere")


@dataclass(frozen=True)
class SegmentPower:
    """The balance evaluated at one sample, with its terms kept separate.

    The terms are exposed individually because CP-20 diagnoses a bad fit by
    which term is wrong, and a single total tells you nothing about that.
    """

    speed_mps: float
    air_speed_mps: float
    p_wheel_kw: float
    p_drag_kw: float
    p_rolling_kw: float
    p_gradient_kw: float
    p_inertial_kw: float
    ers_deploy_power_est_kw: float
    ers_harvest_power_est_kw: float
    #: Internal-combustion power actually used, which is min(available, demand)
    #: rather than the maximum. CP-19 integrates this to get fuel burned, so
    #: reporting the cap here would burn fuel the car never used.
    p_ice_est_kw: float = 0.0
    envelope_cap_kw: float | None = None
    envelope_violation: bool = False
    violation_margin_kw: float | None = None
    air_density_is_fallback: bool = False
    provenance: str = PROVENANCE


@dataclass(frozen=True)
class EnergyState:
    """Energy Store state and the two flow budgets, tracked separately.

    Section 28 keeps these apart deliberately. A car can be out of deploy budget
    with a full store, or the reverse; collapsing them into one number produces
    a twin that is right about total energy and wrong about what the car may
    legally do next.
    """

    ers_soc_est_mj: float
    ers_energy_used_est_mj: float = 0.0
    ers_energy_harvested_est_mj: float = 0.0
    ers_deploy_budget_remaining_est_mj: float | None = None
    ers_harvest_budget_remaining_est_mj: float | None = None
    soc_clipped: bool = False
    provenance: str = PROVENANCE


def kmh_to_mps(speed_kmh: Any) -> float:
    """Convert once, at the boundary.

    The documented order-of-magnitude failure in CP-18 is feeding km/h into a
    term expecting m/s. Doing the conversion in exactly one place, with a test,
    is the whole defence.
    """
    return _finite(speed_kmh, "speed_kmh") / 3.6


def air_speed_mps(speed_mps: float, wind_head_component_mps: Any = 0.0) -> float:
    """Air speed seen by the car. Positive head-wind component increases it."""
    return _finite(speed_mps, "speed_mps") + _finite(wind_head_component_mps or 0.0,
                                                     "wind_head_component_mps")


def drag_power_kw(air_speed: float, rho_kgm3: float, cda_m2: float) -> float:
    """0.5 * rho * CdA * v_air^3, in kW.

    Signed on the cube so that a car moving backwards through the air -- which
    should not happen, but a bad wind value can produce it -- costs energy
    rather than generating it.
    """
    speed = _finite(air_speed, "air_speed")
    magnitude = 0.5 * _finite(rho_kgm3, "rho_kgm3") * _finite(cda_m2, "cda_m2") * abs(speed) ** 3
    return magnitude / _WATTS_PER_KW


def rolling_power_kw(speed_mps: float, mass_kg: float, crr: float, gravity: float) -> float:
    return (crr * mass_kg * gravity * _finite(speed_mps, "speed_mps")) / _WATTS_PER_KW


def gradient_power_kw(speed_mps: float, mass_kg: float, gradient: Any, gravity: float) -> float:
    """Positive gradient is uphill, and costs power.

    ``gradient`` is a slope (rise over run), not an angle: sin(atan(slope)) is
    the correct projection and differs from the slope itself by 0.5% at 10%
    grade, which is inside every other error here but free to get right.
    """
    slope = _finite(gradient or 0.0, "gradient")
    return (mass_kg * gravity * math.sin(math.atan(slope)) * _finite(speed_mps, "speed_mps")) / _WATTS_PER_KW


def inertial_power_kw(speed_mps: float, acceleration_mps2: Any, mass_kg: float) -> float:
    """m*a*v. Positive under acceleration, negative under braking."""
    return (mass_kg * _finite(acceleration_mps2 or 0.0, "acceleration_mps2")
            * _finite(speed_mps, "speed_mps")) / _WATTS_PER_KW


def wheel_power_kw(
    speed_kmh: Any,
    acceleration_mps2: Any,
    parameters: PhysicsParameters,
    *,
    rho_kgm3: float,
    gradient: Any = 0.0,
    wind_head_component_mps: Any = 0.0,
) -> tuple[float, dict[str, float]]:
    """Total power at the wheels, and each term that produced it."""
    speed = kmh_to_mps(speed_kmh)
    air = air_speed_mps(speed, wind_head_component_mps)
    terms = {
        "p_drag_kw": drag_power_kw(air, rho_kgm3, parameters.cda_m2),
        "p_rolling_kw": rolling_power_kw(speed, parameters.mass_kg, parameters.crr,
                                         parameters.gravity_mps2),
        "p_gradient_kw": gradient_power_kw(speed, parameters.mass_kg, gradient,
                                           parameters.gravity_mps2),
        "p_inertial_kw": inertial_power_kw(speed, acceleration_mps2, parameters.mass_kg),
    }
    return sum(terms.values()), terms


def segment_power(
    speed_kmh: Any,
    acceleration_mps2: Any,
    parameters: PhysicsParameters,
    *,
    rho_kgm3: Any = None,
    fallback_rho_kgm3: float = 1.225,
    gradient: Any = 0.0,
    wind_head_component_mps: Any = 0.0,
    p_ice_kw: Any = None,
    envelope_cap_kw: Any = None,
) -> SegmentPower:
    """Evaluate the balance at one sample. Pure; no I/O, no frames.

    ``envelope_cap_kw`` should come from the CP-11 evaluator at this speed under
    the loosest legal mode. It is used **only** to record a violation: the
    returned deployment estimate is never reduced to fit under it.
    """
    is_fallback = rho_kgm3 is None
    rho = fallback_rho_kgm3 if is_fallback else _finite(rho_kgm3, "rho_kgm3")
    if rho <= 0:
        raise TwinError(f"rho_kgm3 must be positive, got {rho}")

    speed = kmh_to_mps(speed_kmh)
    total, terms = wheel_power_kw(
        speed_kmh, acceleration_mps2, parameters,
        rho_kgm3=rho, gradient=gradient, wind_head_component_mps=wind_head_component_mps,
    )

    ice = parameters.p_ice_max_kw if p_ice_kw is None else _finite(p_ice_kw, "p_ice_kw")
    # Under braking the wheel power is negative and the ICE contributes nothing
    # to be offset, so the shortfall is recovery rather than a negative demand.
    if total >= 0:
        demand = total / parameters.eta_drivetrain
        # The engine supplies what is demanded, up to what it has. Recording the
        # maximum instead would have CP-19 burn fuel the car never used.
        ice_used = min(max(0.0, ice), demand)
        deploy = max(0.0, demand - ice_used)
        harvest = 0.0
    else:
        ice_used = 0.0
        deploy = 0.0
        harvest = -total * parameters.eta_drivetrain

    violation = False
    margin = None
    if envelope_cap_kw is not None:
        cap = _finite(envelope_cap_kw, "envelope_cap_kw")
        margin = deploy - cap
        violation = margin > 0
    else:
        cap = None

    return SegmentPower(
        speed_mps=speed,
        air_speed_mps=air_speed_mps(speed, wind_head_component_mps),
        p_wheel_kw=total,
        ers_deploy_power_est_kw=deploy,
        ers_harvest_power_est_kw=harvest,
        p_ice_est_kw=ice_used,
        envelope_cap_kw=cap,
        envelope_violation=violation,
        violation_margin_kw=margin,
        air_density_is_fallback=is_fallback,
        **terms,
    )


def integrate_segment(power_kw: Any, duration_s: Any) -> float:
    """Energy in MJ from a mean power over a duration."""
    return _finite(power_kw, "power_kw") * _finite(duration_s, "duration_s") * _MJ_PER_KWS


def advance_energy_state(
    state: EnergyState,
    deploy_mj: Any,
    harvest_mj: Any,
    parameters: PhysicsParameters,
    *,
    store_capacity_mj: Any = None,
) -> EnergyState:
    """Advance the Energy Store by one segment.

    ``E_next = E_current + eta_h * harvest - deploy / eta_d``. The two
    efficiencies are separate because the round trip is lossy in both
    directions; one number would hide half the loss.

    Clipping at the store bounds is recorded rather than silent: a twin that
    clips constantly is mis-calibrated, and CP-18's gate asks for the clipping
    rate precisely so that cannot pass unnoticed.
    """
    deploy = max(0.0, _finite(deploy_mj, "deploy_mj"))
    harvest = max(0.0, _finite(harvest_mj, "harvest_mj"))

    stored = state.ers_soc_est_mj + parameters.eta_harvest * harvest - deploy / parameters.eta_deploy
    clipped = False
    if stored < 0:
        stored, clipped = 0.0, True
    if store_capacity_mj is not None:
        capacity = _finite(store_capacity_mj, "store_capacity_mj")
        if stored > capacity:
            stored, clipped = capacity, True

    def spend(budget: float | None, amount: float) -> float | None:
        return None if budget is None else max(0.0, budget - amount)

    return EnergyState(
        ers_soc_est_mj=stored,
        ers_energy_used_est_mj=state.ers_energy_used_est_mj + deploy,
        ers_energy_harvested_est_mj=state.ers_energy_harvested_est_mj + harvest,
        ers_deploy_budget_remaining_est_mj=spend(state.ers_deploy_budget_remaining_est_mj, deploy),
        ers_harvest_budget_remaining_est_mj=spend(state.ers_harvest_budget_remaining_est_mj, harvest),
        soc_clipped=state.soc_clipped or clipped,
    )


def trailing_mean(values: Sequence[Any], window: int = 3) -> float | None:
    """Mean of the last ``window`` values. Trailing only.

    acc_x is noisy at 20 m resolution and wants smoothing, but a centred window
    would read the future into a feature the planner must compute live
    (section 12). This is the only smoothing shape permitted here.
    """
    if window < 1:
        raise TwinError("window must be at least 1")
    numbers = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not numbers:
        return None
    tail = numbers[-window:]
    return sum(tail) / len(tail)
