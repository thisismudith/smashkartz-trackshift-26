"""CP-12 eligibility probability (M21).

The gates the plan pins: p_eligible bounded and saturating in the right
directions, sigma growing with distance to the line, the sigma floor, the
eligibility_margin sign convention, and a trailing window only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.rules.eligibility import (  # noqa: E402
    SIGMA_FLOOR_S,
    TRAILING_WINDOW,
    EligibilityError,
    derive_checkpoint_geometry,
    eligibility_margin,
    normal_cdf,
    project_gap_at_line,
)

RATES = [0.05, 0.04, 0.06, 0.05, 0.05]


def test_normal_cdf_is_a_cdf():
    assert normal_cdf(0.0) == pytest.approx(0.5)
    assert normal_cdf(-8.0) == pytest.approx(0.0, abs=1e-9)
    assert normal_cdf(8.0) == pytest.approx(1.0, abs=1e-9)
    values = [normal_cdf(z / 4) for z in range(-40, 41)]
    assert all(b >= a for a, b in zip(values, values[1:])), "cdf must be non-decreasing"


def test_p_eligible_is_bounded_and_saturates_in_both_directions():
    far = project_gap_at_line(6.0, 0.0, 5.0, RATES, threshold_s=1.0)
    close = project_gap_at_line(0.05, 0.0, 5.0, RATES, threshold_s=1.0)
    for projection in (far, close):
        assert 0.0 <= projection.p_eligible <= 1.0
    assert close.p_eligible > 0.99, "a gap far inside the threshold should be near-certain"
    assert far.p_eligible < 0.01, "a gap far outside the threshold should be near-impossible"


def test_p_eligible_is_monotone_in_the_gap():
    previous = 1.1
    for gap in [g / 10 for g in range(0, 40)]:
        p = project_gap_at_line(gap, 0.0, 3.0, RATES).p_eligible
        assert p <= previous + 1e-12, "closing the gap must not reduce eligibility"
        previous = p


def test_closing_reduces_the_projected_gap():
    holding = project_gap_at_line(1.5, 0.0, 10.0, RATES)
    closing = project_gap_at_line(1.5, 0.05, 10.0, RATES)
    assert closing.mu_s < holding.mu_s
    assert closing.p_eligible > holding.p_eligible


def test_sigma_grows_with_distance_to_the_line():
    """A projection 2 km out must be less certain than one 200 m out."""
    horizons = [1.0, 2.0, 5.0, 10.0, 20.0, 40.0]
    sigmas = [project_gap_at_line(1.2, 0.05, t, RATES).sigma_s for t in horizons]
    assert all(b >= a for a, b in zip(sigmas, sigmas[1:])), sigmas
    assert sigmas[-1] > sigmas[0]


def test_sigma_is_proportional_to_the_horizon_once_off_the_floor():
    """Proportionality is the unfloored behaviour; below the floor it clamps,
    which is the point of the floor and not a violation of the scaling."""
    near = project_gap_at_line(1.2, 0.05, 10.0, RATES)
    far = project_gap_at_line(1.2, 0.05, 20.0, RATES)
    assert not near.sigma_floored and not far.sigma_floored
    assert far.sigma_s == pytest.approx(near.sigma_s * 2, rel=1e-9)


def test_sigma_is_floored_so_a_projection_is_never_certain():
    """Section 22: a perfectly certain projection is not real."""
    steady = project_gap_at_line(1.2, 0.05, 1.0, [0.05] * 5)
    assert steady.sigma_s == pytest.approx(SIGMA_FLOOR_S)
    assert steady.sigma_floored is True
    assert 0.0 < steady.p_eligible < 1.0


def test_no_trailing_history_floors_sigma_rather_than_zeroing_it():
    blind = project_gap_at_line(1.2, 0.05, 8.0, [])
    assert blind.sigma_s == pytest.approx(SIGMA_FLOOR_S)
    assert blind.sigma_floored is True
    assert blind.trailing_n == 0
    assert "trailing_closing_rate_stdev" not in blind.terms_used


def test_only_the_trailing_window_is_used():
    """A centred or unbounded window is the easy way to leak the future."""
    long_history = [9.0] * 20 + RATES
    windowed = project_gap_at_line(1.2, 0.05, 10.0, long_history)
    only_recent = project_gap_at_line(1.2, 0.05, 10.0, RATES)
    assert windowed.trailing_n == TRAILING_WINDOW
    assert windowed.sigma_s == pytest.approx(only_recent.sigma_s)


def test_terms_used_is_reported():
    projection = project_gap_at_line(1.2, 0.05, 10.0, RATES)
    assert "gap_now_s" in projection.terms_used
    assert "closing_rate_s_per_s" in projection.terms_used
    assert "trailing_closing_rate_stdev" in projection.terms_used


def test_eligibility_margin_sign_convention():
    """Positive means eligible."""
    assert eligibility_margin(1.0, 0.4) == pytest.approx(0.6)
    assert eligibility_margin(1.0, 1.6) == pytest.approx(-0.6)
    projection = project_gap_at_line(0.4, 0.0, 0.0, RATES, threshold_s=1.0)
    assert projection.eligibility_margin_s > 0


def test_projection_is_reproducible_from_decision_time_state_alone():
    args = (1.2, 0.05, 8.0, RATES)
    first = project_gap_at_line(*args)
    second = project_gap_at_line(*args)
    assert (first.mu_s, first.sigma_s, first.p_eligible) == (second.mu_s, second.sigma_s, second.p_eligible)


def test_bad_inputs_raise_rather_than_defaulting():
    with pytest.raises(EligibilityError):
        project_gap_at_line(None, 0.05, 5.0, RATES)
    with pytest.raises(EligibilityError):
        project_gap_at_line(1.2, 0.05, -1.0, RATES)
    with pytest.raises(EligibilityError):
        project_gap_at_line(1.2, 0.05, 5.0, RATES, sigma_floor_s=0.0)
    with pytest.raises(EligibilityError):
        project_gap_at_line(float("nan"), 0.05, 5.0, RATES)


# ------------------------------------------------------------- geometry

def test_checkpoint_geometry_legs():
    geometry = derive_checkpoint_geometry(100.0, 320.0, 900.0)
    assert geometry["distance_detection_to_activation"] == pytest.approx(220.0)
    assert geometry["distance_activation_to_brake"] == pytest.approx(580.0)


def test_missing_line_gives_null_not_zero():
    """Null means the line is unknown. Zero would mean the lines coincide."""
    geometry = derive_checkpoint_geometry(None, 320.0, 900.0)
    assert geometry["distance_detection_to_activation"] is None
    assert geometry["distance_activation_to_brake"] == pytest.approx(580.0)
    assert geometry["detection_line_m"] is None


def test_geometry_wraps_across_the_start_line():
    geometry = derive_checkpoint_geometry(5600.0, 200.0, 700.0, lap_length_m=5800.0)
    assert geometry["distance_detection_to_activation"] == pytest.approx(400.0)
