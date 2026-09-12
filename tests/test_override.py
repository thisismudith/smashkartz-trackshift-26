"""CP-18b override / ERS-mode discriminator (M35).

The gates: the discriminability gate fires first, the margin scales with the
twin's uncertainty, the historical false-positive rate is measurable, and the
output is never OBSERVED.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.twin.api import (  # noqa: E402
    NORMAL,
    OVERRIDE,
    UNKNOWN,
    OverrideError,
    discriminate,
    historical_false_positive_rate,
)

SEPARATION = 290.0
SIGMA = 20.0


def call(speed, deploy, cap=350.0, sigma=SIGMA, k=2.0):
    return discriminate(speed, deploy, cap, separation_speed_kmh=SEPARATION,
                        sigma_kw=sigma, k_sigma=k)


def test_below_the_separation_speed_the_answer_is_unknown_not_normal():
    """Returning NORMAL here would be a fabricated claim (section 20.2)."""
    for speed in (0.0, 150.0, 289.9, SEPARATION):
        result = call(speed, 500.0)
        assert result.discriminable is False
        assert result.ers_mode_inferred == UNKNOWN
        assert result.override_active_inferred is None
        assert "separation speed" in result.reason


def test_the_gate_is_the_first_check_not_the_last():
    """Even an absurd excess below separation stays UNKNOWN."""
    assert call(200.0, 10_000.0).ers_mode_inferred == UNKNOWN


def test_clear_excess_above_separation_reads_as_override():
    result = call(330.0, 350.0 + 5 * SIGMA)
    assert result.discriminable is True
    assert result.ers_mode_inferred == OVERRIDE
    assert result.override_active_inferred > 0.99


def test_deployment_at_the_cap_reads_as_normal():
    result = call(330.0, 350.0)
    assert result.ers_mode_inferred == NORMAL
    assert result.override_active_inferred == pytest.approx(0.5, abs=1e-9)


def test_the_margin_scales_with_the_twin_uncertainty_not_a_fixed_kw():
    """A tight threshold on a poorly calibrated twin produces confident nonsense."""
    excess = 350.0 + 60.0
    assert call(330.0, excess, sigma=10.0).ers_mode_inferred == OVERRIDE
    assert call(330.0, excess, sigma=50.0).ers_mode_inferred == NORMAL


def test_a_zero_uncertainty_twin_is_refused():
    with pytest.raises(OverrideError, match="confident nonsense"):
        call(330.0, 400.0, sigma=0.0)


def test_detection_does_not_rise_when_the_twin_is_harmlessly_miscalibrated():
    """Raise the estimate 3%, as a 3% mass error would. Detections must not jump."""
    speeds = [300.0 + i for i in range(40)]
    deploys = [340.0 + i for i in range(40)]
    base = sum(1 for s, d in zip(speeds, deploys) if call(s, d).ers_mode_inferred == OVERRIDE)
    skewed = sum(1 for s, d in zip(speeds, deploys)
                 if call(s, d * 1.03).ers_mode_inferred == OVERRIDE)
    assert skewed - base <= 2, (
        f"detections rose from {base} to {skewed} under a 3% miscalibration; "
        "the margin is too tight and the discriminator is reading calibration error"
    )


def test_never_emits_observed_provenance():
    assert call(330.0, 500.0).provenance == "INFERRED"
    assert call(100.0, 500.0).provenance == "INFERRED"


def test_historical_false_positive_rate_is_measurable():
    """2022-2025 is a control group with a known answer: no override existed."""
    historical = [call(320.0, 350.0 + delta) for delta in (-40, -20, 0, 10, 120)]
    report = historical_false_positive_rate(historical)
    assert report["discriminable"] == 5
    assert report["false_positives"] == 1          # only the +120 kW sample clears 2 sigma
    assert report["false_positive_rate"] == pytest.approx(0.2)
    assert "did not exist before 2026" in report["note"]


def test_undiscriminable_samples_are_excluded_from_the_rate():
    mixed = [call(100.0, 900.0), call(320.0, 350.0 + 120)]
    report = historical_false_positive_rate(mixed)
    assert report["samples"] == 2 and report["discriminable"] == 1
    assert report["false_positive_rate"] == pytest.approx(1.0)
