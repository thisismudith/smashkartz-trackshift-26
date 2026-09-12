"""CP-19 fuel-load estimator (M34).

Monotonic within a race, ends near empty, uncertainty widening with distance
from the last anchor, and causal: truncating the race leaves earlier estimates
untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.twin.api import (  # noqa: E402
    FuelError,
    consumption_from_ice_work,
    estimate_fuel_curve,
    start_fuel_kg,
)

RACE_LAPS = 52
PER_LAP_KG = 1.8


def race_curve(laps: int = RACE_LAPS, per_lap: float = PER_LAP_KG, **kwargs):
    start = start_fuel_kg(laps, per_lap)
    return estimate_fuel_curve([per_lap] * laps, start, **kwargs)


def test_provenance_is_inferred_never_observed():
    """Section 38. Fuel is not observable in the public feed."""
    assert all(item.provenance == "INFERRED" for item in race_curve())


def test_monotonically_decreasing_within_a_race():
    """There is no refuelling in modern F1."""
    loads = [item.fuel_load_kg_est for item in race_curve()]
    assert all(b <= a for a, b in zip(loads, loads[1:])), loads[:5]


def test_a_full_race_ends_between_zero_and_three_kilograms():
    final = race_curve()[-1].fuel_load_kg_est
    assert 0.0 <= final <= 3.0, final


def test_start_load_covers_the_race_plus_a_reserve():
    """A car that finishes on exactly zero has not been modelled, it has been wished."""
    start = start_fuel_kg(RACE_LAPS, PER_LAP_KG)
    assert start > RACE_LAPS * PER_LAP_KG


def test_start_load_is_bounded_by_the_regulatory_maximum():
    assert start_fuel_kg(100, 2.0, regulatory_maximum_kg=110.0) == pytest.approx(110.0)


def test_uncertainty_widens_with_distance_from_the_last_anchor():
    curve = race_curve()
    assert curve[0].fuel_load_uncertainty_kg < curve[-1].fuel_load_uncertainty_kg
    widths = [item.fuel_load_uncertainty_kg for item in curve]
    assert all(b >= a for a, b in zip(widths, widths[1:]))


def test_uncertainty_grows_in_quadrature_not_linearly():
    """Independent per-lap errors do not add linearly.

    Treating them as if they did makes the interval by lap 50 so wide it says
    nothing at all.
    """
    curve = race_curve()
    at_4, at_16 = curve[3].fuel_load_uncertainty_kg, curve[15].fuel_load_uncertainty_kg
    assert at_16 == pytest.approx(at_4 * 2, rel=1e-9)


def test_an_anchor_resets_the_uncertainty_growth():
    curve = race_curve(anchor_laps=(20,))
    assert curve[19].laps_since_anchor == 0
    assert curve[19].fuel_load_uncertainty_kg == pytest.approx(0.0)
    assert curve[20].fuel_load_uncertainty_kg > 0


def test_the_estimate_is_causal():
    """Truncating the race leaves every earlier estimate identical."""
    full = race_curve()
    short = estimate_fuel_curve([PER_LAP_KG] * 20, start_fuel_kg(RACE_LAPS, PER_LAP_KG))
    for early, late in zip(short, full):
        assert early.fuel_load_kg_est == pytest.approx(late.fuel_load_kg_est)
        assert early.fuel_load_uncertainty_kg == pytest.approx(late.fuel_load_uncertainty_kg)


def test_running_dry_is_clipped_and_recorded():
    curve = estimate_fuel_curve([5.0] * 10, start_kg=20.0)
    assert curve[-1].fuel_load_kg_est == 0.0
    assert any(item.clipped_at_zero for item in curve)


def test_consumption_follows_the_twin_ice_work():
    """Deriving fuel from the twin keeps one story about the lap, not two."""
    light = consumption_from_ice_work(100.0)
    heavy = consumption_from_ice_work(200.0)
    assert heavy == pytest.approx(light * 2)
    assert 0 < light < 10, light
    assert consumption_from_ice_work(-5.0) == 0.0


def test_bad_inputs_are_refused():
    with pytest.raises(FuelError):
        start_fuel_kg(0, 1.8)
    with pytest.raises(FuelError):
        estimate_fuel_curve([1.0], start_kg=-1.0)
    with pytest.raises(FuelError):
        consumption_from_ice_work(100.0, thermal_efficiency=0.0)
