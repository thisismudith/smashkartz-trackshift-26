"""Causal fuel-load estimator (M34, CP-19).

Fuel is not observable in the public feed. This is an estimator, tagged
``INFERRED`` and labelled a proxy wherever it surfaces (sections 11, 38). It is
the only permitted source of fuel context for a live feature, which means it
must never be presented as a measurement -- the registry entry says so and the
API returns the provenance with the value.

Causal by construction::

    fuel_kg(lap k) = start_fuel_kg - sum(consumption per lap up to k)

There is no refuelling in modern F1, so the load decreases monotonically within
a race and resets only at the start. Uncertainty grows with laps since the last
anchor, because each lap's consumption estimate adds its own error and nothing
re-anchors it.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PROVENANCE",
    "FuelEstimate",
    "FuelError",
    "consumption_from_ice_work",
    "start_fuel_kg",
    "estimate_fuel_curve",
    "LAP_TIME_EFFECT_S_PER_KG",
]

#: Section 38. An inference, never an observation.
PROVENANCE = "INFERRED"

#: Widely quoted lap-time sensitivity, used only as a sanity check on the
#: fitted coefficient in CP-19's gate -- never to generate the estimate.
LAP_TIME_EFFECT_S_PER_KG = 0.03

#: Specific energy of F1 fuel. Converts ICE work to mass burned.
_FUEL_ENERGY_MJ_PER_KG = 43.0


class FuelError(ValueError):
    """The fuel curve cannot be estimated with the inputs supplied."""


@dataclass(frozen=True)
class FuelEstimate:
    """One lap's estimated load, with the uncertainty that goes with it."""

    lap: int
    fuel_load_kg_est: float
    fuel_load_uncertainty_kg: float
    laps_since_anchor: int
    consumption_kg: float
    clipped_at_zero: bool = False
    provenance: str = PROVENANCE


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FuelError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise FuelError(f"{name} must be finite, got {number!r}")
    return number


def consumption_from_ice_work(ice_work_mj: Any, *, thermal_efficiency: float = 0.50) -> float:
    """Fuel mass burned for a lap's internal-combustion work.

    2026 power units are around 50% thermally efficient, so a megajoule at the
    crank costs roughly two megajoules of fuel. Passing the twin's own ICE work
    keeps the fuel curve consistent with the power balance rather than being a
    second, independent guess about the same lap.
    """
    if not 0 < thermal_efficiency <= 1:
        raise FuelError(f"thermal_efficiency must lie in (0, 1], got {thermal_efficiency}")
    work = max(0.0, _finite(ice_work_mj, "ice_work_mj"))
    return work / thermal_efficiency / _FUEL_ENERGY_MJ_PER_KG


def start_fuel_kg(
    race_laps: Any,
    consumption_per_lap_kg: Any,
    *,
    regulatory_maximum_kg: Any = None,
    reserve_kg: float = 1.0,
) -> float:
    """Starting load for a race, bounded by the regulatory maximum.

    ``reserve_kg`` is the sample the scrutineers need at the end: a car that
    finishes on exactly zero has not been modelled, it has been wished.
    """
    laps = _finite(race_laps, "race_laps")
    per_lap = _finite(consumption_per_lap_kg, "consumption_per_lap_kg")
    if laps <= 0 or per_lap <= 0:
        raise FuelError("race_laps and consumption_per_lap_kg must both be positive")
    required = laps * per_lap + reserve_kg
    if regulatory_maximum_kg is not None:
        limit = _finite(regulatory_maximum_kg, "regulatory_maximum_kg")
        return min(required, limit)
    return required


def estimate_fuel_curve(
    consumption_by_lap: Sequence[Any] | Iterable[Any],
    start_kg: Any,
    *,
    per_lap_uncertainty_kg: float = 0.15,
    anchor_laps: Iterable[int] = (),
) -> list[FuelEstimate]:
    """The causal fuel curve for one stint or race.

    ``consumption_by_lap[i]`` is the fuel burned during lap ``i + 1``. The
    estimate for lap *k* uses only laps up to *k*, so truncating the race at any
    lap leaves every earlier estimate unchanged.

    Uncertainty accumulates in quadrature with laps since the last anchor.
    Independent per-lap errors do not add linearly, and treating them as if they
    did would produce intervals so wide by lap 50 that they say nothing.
    """
    consumption = [max(0.0, _finite(value, "consumption_by_lap")) for value in consumption_by_lap]
    remaining = _finite(start_kg, "start_kg")
    if remaining < 0:
        raise FuelError(f"start_kg must not be negative, got {remaining}")
    if per_lap_uncertainty_kg < 0:
        raise FuelError("per_lap_uncertainty_kg must not be negative")

    anchors = {int(lap) for lap in anchor_laps}
    out: list[FuelEstimate] = []
    since_anchor = 0

    for index, burned in enumerate(consumption, start=1):
        remaining -= burned
        clipped = remaining < 0
        if clipped:
            remaining = 0.0
        since_anchor = 0 if index in anchors else since_anchor + 1
        out.append(FuelEstimate(
            lap=index,
            fuel_load_kg_est=remaining,
            # Quadrature, not a linear sum: per-lap errors are independent.
            fuel_load_uncertainty_kg=per_lap_uncertainty_kg * math.sqrt(since_anchor),
            laps_since_anchor=since_anchor,
            consumption_kg=burned,
            clipped_at_zero=clipped,
        ))
    return out
