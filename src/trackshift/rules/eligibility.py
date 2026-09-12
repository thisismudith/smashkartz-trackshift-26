"""Eligibility probability (M21, CP-12).

``P(gap at the Detection Line < threshold)`` as a distribution, not a boolean.
Section 22: when the projection is uncertain, a hard yes/no throws away the
only thing the planner can act on. A car 2 km from the line with a noisy
closing rate genuinely might or might not arrive inside the threshold, and the
planner needs the probability to price the energy it would spend chasing it.

Everything here is a pure function of decision-time state. No observed future
gap, no pass outcome, and a **trailing** window only -- section 12 forbids
centred windows for live features, and a centred window is the single easiest
way to leak the future into a feature that looks causal.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from statistics import fmean, pstdev
from typing import Any

__all__ = [
    "SIGMA_FLOOR_S",
    "TRAILING_WINDOW",
    "GapProjection",
    "normal_cdf",
    "project_gap_at_line",
    "eligibility_margin",
    "derive_checkpoint_geometry",
    "EligibilityError",
]

#: A projection is never perfectly certain. Section 22's failure mode is sigma
#: collapsing to zero and p_eligible saturating at 0 or 1, which reads as
#: confidence and is actually a degenerate estimate.
SIGMA_FLOOR_S = 0.05

#: Trailing segments used to estimate closing-rate variability.
TRAILING_WINDOW = 5


class EligibilityError(ValueError):
    """Raised when a projection is asked for without the terms it needs."""


def normal_cdf(z: float) -> float:
    """Phi(z). stdlib erf rather than scipy: this runs inside the DP's loop."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


@dataclass(frozen=True)
class GapProjection:
    """A projected gap at a line, with the terms that produced it.

    ``terms_used`` is not decoration. The baseline projection may start from
    closing rate and trailing variance, but section 22 requires it to expose
    which terms it actually used, so a later richer projection can be compared
    against this one rather than silently replacing it.
    """

    mu_s: float
    sigma_s: float
    p_eligible: float
    threshold_s: float
    time_to_line_s: float
    gap_now_s: float
    closing_rate_s_per_s: float
    sigma_floored: bool
    trailing_n: int
    terms_used: tuple[str, ...] = field(default=())

    @property
    def eligibility_margin_s(self) -> float:
        """Positive means eligible: the projection sits inside the threshold."""
        return self.threshold_s - self.mu_s


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EligibilityError(f"{name} must be a finite number, got {value!r}") from exc
    if not math.isfinite(number):
        raise EligibilityError(f"{name} must be finite, got {number!r}")
    return number


def project_gap_at_line(
    gap_now_s: Any,
    closing_rate_s_per_s: Any,
    time_to_line_s: Any,
    trailing_closing_rates: Sequence[Any] | None = None,
    *,
    threshold_s: Any = 1.0,
    sigma_floor_s: float = SIGMA_FLOOR_S,
) -> GapProjection:
    """Project the gap forward to a line and return it as a distribution.

    ``closing_rate_s_per_s`` is positive when closing, so the gap shrinks::

        mu    = gap_now - closing_rate * time_to_line
        sigma = stdev(trailing closing rates) * time_to_line, floored
        p     = Phi((threshold - mu) / sigma)

    Sigma scales with ``time_to_line_s`` on purpose: a projection 2 km out must
    be less certain than one 200 m out, and a constant sigma would make the two
    indistinguishable to the planner.
    """
    gap = _finite(gap_now_s, "gap_now_s")
    rate = _finite(closing_rate_s_per_s, "closing_rate_s_per_s")
    horizon = _finite(time_to_line_s, "time_to_line_s")
    threshold = _finite(threshold_s, "threshold_s")
    if horizon < 0:
        raise EligibilityError(f"time_to_line_s must not be negative, got {horizon}")
    if sigma_floor_s <= 0:
        raise EligibilityError("sigma_floor_s must be positive; a zero floor permits a degenerate projection")

    terms = ["gap_now_s", "closing_rate_s_per_s", "time_to_line_s"]
    mu = gap - rate * horizon

    trailing = [
        _finite(value, "trailing_closing_rates")
        for value in (trailing_closing_rates or [])
        if value is not None
    ][-TRAILING_WINDOW:]

    if len(trailing) >= 2:
        rate_sigma = pstdev(trailing)
        terms.append("trailing_closing_rate_stdev")
    else:
        # With no trailing history there is no evidence about variability. Fall
        # back to the floor rather than to zero, and say so through sigma_floored.
        rate_sigma = 0.0

    sigma = rate_sigma * horizon
    floored = sigma < sigma_floor_s
    if floored:
        sigma = sigma_floor_s

    return GapProjection(
        mu_s=mu,
        sigma_s=sigma,
        p_eligible=normal_cdf((threshold - mu) / sigma),
        threshold_s=threshold,
        time_to_line_s=horizon,
        gap_now_s=gap,
        closing_rate_s_per_s=rate,
        sigma_floored=floored,
        trailing_n=len(trailing),
        terms_used=tuple(terms),
    )


def eligibility_margin(threshold_s: Any, gap_at_detection_s: Any) -> float:
    """Section 22. Positive means eligible."""
    return _finite(threshold_s, "threshold_s") - _finite(gap_at_detection_s, "gap_at_detection_s")


def derive_checkpoint_geometry(
    detection_line_m: Any,
    activation_line_m: Any,
    brake_onset_m: Any,
    *,
    lap_length_m: Any = None,
) -> dict[str, float | None]:
    """The section 22 distances between the three decision checkpoints.

    Any leg whose endpoints are not both configured comes back ``None``. A null
    here means "the line is not known", which is the honest state while the FIA
    Detection Lines are unsourced -- it must not be a zero, because zero is a
    distance and would read as "the checkpoints coincide".
    """
    def number(value: Any) -> float | None:
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result if math.isfinite(result) else None

    detection, activation, brake = number(detection_line_m), number(activation_line_m), number(brake_onset_m)
    length = number(lap_length_m)

    def leg(start: float | None, end: float | None) -> float | None:
        if start is None or end is None:
            return None
        delta = end - start
        # A negative leg means the lap wrapped between the two lines.
        if delta < 0 and length:
            delta += length
        return delta

    return {
        "distance_detection_to_activation": leg(detection, activation),
        "distance_activation_to_brake": leg(activation, brake),
        "detection_line_m": detection,
        "activation_line_m": activation,
        "brake_onset_m": brake,
    }
