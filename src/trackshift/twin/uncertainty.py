"""Physics uncertainty (M17, CP-22).

Parameter draws and intervals so C5 can return an ``Uncertain`` rather than a
point estimate (section 42). An interval that is quoted as 80% must actually
contain the observed value about 80% of the time, which is the one property this
module is tested against.

Two sources of error, and both are needed. Parameter covariance from the
least-squares Jacobian captures how well the fit is pinned down; it says nothing
about the model being the wrong shape. Held-out residual variance captures that.
Reporting only the first is the documented way coverage comes out far below
nominal while every interval looks respectable.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "DRAWS",
    "UncertaintyError",
    "ParameterUncertainty",
    "parameter_covariance",
    "draw_parameters",
    "propagate",
    "interval",
    "coverage",
]

#: Section 42. Enough for a stable 10th/90th percentile without stalling the DP.
DRAWS = 200


class UncertaintyError(ValueError):
    """The uncertainty model cannot be built with the inputs supplied."""


@dataclass(frozen=True)
class ParameterUncertainty:
    """Fitted parameters with the covariance that goes with them."""

    names: tuple[str, ...]
    mean: tuple[float, ...]
    covariance: Any
    residual_variance: float
    n_residuals: int


def parameter_covariance(
    jacobian: Any,
    residuals: Sequence[float],
    names: Sequence[str],
) -> ParameterUncertainty:
    """Covariance at the optimum: inv(J.T @ J) * residual variance.

    A rank-deficient Jacobian means two parameters are trading off against each
    other -- CdA against Crr is the classic pair -- and the pseudo-inverse keeps
    that visible as a wide, correlated interval instead of failing outright.
    Widening the bounds would be the wrong response; fixing one from literature
    and fitting the other is the right one.
    """
    import numpy as np

    J = np.asarray(jacobian, dtype=float)
    r = np.asarray(list(residuals), dtype=float)
    if J.ndim != 2:
        raise UncertaintyError(f"jacobian must be 2-D, got shape {J.shape}")
    if J.shape[1] != len(names):
        raise UncertaintyError(
            f"jacobian has {J.shape[1]} columns for {len(names)} parameter names"
        )
    dof = max(1, J.shape[0] - J.shape[1])
    residual_variance = float(r @ r) / dof
    covariance = np.linalg.pinv(J.T @ J) * residual_variance
    return ParameterUncertainty(
        names=tuple(names),
        mean=tuple(float(x) for x in np.zeros(len(names))),
        covariance=covariance,
        residual_variance=residual_variance,
        n_residuals=int(J.shape[0]),
    )


def draw_parameters(
    mean: Sequence[float],
    covariance: Any,
    *,
    draws: int = DRAWS,
    bounds: Sequence[tuple[float, float]] | None = None,
    seed: int = 42,
) -> Any:
    """Draw parameter sets from the fitted covariance, respecting bounds.

    Draws are clipped to the physical bounds rather than rejected: a draw with
    negative drag is not a low-probability world, it is not a world at all, and
    dropping it silently would bias the remaining sample.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    mu = np.asarray(list(mean), dtype=float)
    sample = rng.multivariate_normal(mu, np.asarray(covariance, dtype=float), size=draws)
    if bounds is not None:
        low = np.array([b[0] for b in bounds], dtype=float)
        high = np.array([b[1] for b in bounds], dtype=float)
        sample = np.clip(sample, low, high)
    return sample


def propagate(
    draws: Any,
    predict: Callable[[Sequence[float]], float],
) -> list[float]:
    """Push every draw through the model. Correlation is preserved by construction.

    Each draw is one coherent parameter set, so coupled energy, time and gap
    outputs move together. Sampling each output independently would produce
    combinations the physics cannot produce.
    """
    return [float(predict(list(row))) for row in draws]


def interval(values: Sequence[float], *, low: float = 10.0, high: float = 90.0,
             extra_variance: float = 0.0) -> dict[str, float]:
    """Percentile interval, widened by any held-out residual variance supplied.

    ``extra_variance`` is model misspecification. Parameter uncertainty alone
    systematically under-covers, because it assumes the model shape is right.
    """
    import numpy as np

    array = np.asarray(list(values), dtype=float)
    if array.size == 0:
        raise UncertaintyError("no propagated values to summarise")
    if extra_variance < 0:
        raise UncertaintyError("extra_variance must not be negative")
    inflation = math.sqrt(extra_variance)
    mean = float(array.mean())
    lo = float(np.percentile(array, low)) - inflation
    hi = float(np.percentile(array, high)) + inflation
    return {"mean": mean, "low": lo, "high": hi, "n_draws": int(array.size),
            "spread": hi - lo}


def coverage(observed: Sequence[float], intervals: Sequence[dict[str, float]]) -> dict[str, Any]:
    """Share of observations inside their interval. An 80% interval should score ~0.80."""
    pairs = list(zip(observed, intervals))
    if not pairs:
        raise UncertaintyError("no observations to score coverage against")
    inside = sum(1 for value, band in pairs if band["low"] <= float(value) <= band["high"])
    rate = inside / len(pairs)
    return {
        "n": len(pairs),
        "covered": inside,
        "coverage": rate,
        "mean_spread": sum(band["spread"] for _, band in pairs) / len(pairs),
        "note": ("Far below nominal means parameter covariance alone is being reported and "
                 "model misspecification is not; add held-out residual variance rather than "
                 "widening the percentiles."),
    }
