"""Physics calibration hierarchy (M15, CP-20).

All five rungs of section 29, fitted and compared on the same held-out data.
The hierarchy exists so that "the model got better" is a claim with evidence
behind it: each rung must beat the previous on held-out MAE or be rejected
(section 34), and a rung that wins on MAE while breaking physics has not won.

Bounds are physical constraints, not tuning knobs. A fit that runs to a bound is
telling you the model is missing something -- downforce-induced drag, an aero
state -- and the fix is to add the term, never to widen the bound.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "RUNGS",
    "PARAMETER_NAMES",
    "PARAMETER_BOUNDS",
    "MAE_TARGETS",
    "RungResult",
    "CalibrationError",
    "fit_parameters",
    "mean_absolute_error",
    "check_physical_constraints",
    "compare_rungs",
    "error_by_group",
    "residual_share",
]

RUNGS: tuple[str, ...] = (
    "analytical",       # 1. priors only, nothing fitted
    "global",           # 2. one parameter set for every car and circuit
    "team",             # 3. per team
    "event",            # 4. per team, per circuit
    "physics_residual",  # 5. rung 4 plus a learned residual
)

PARAMETER_NAMES: tuple[str, ...] = ("cda_m2", "crr", "eta_drivetrain", "p_ice_max_kw")

#: Physical constraints (section 29). Not tuning knobs.
PARAMETER_BOUNDS: dict[str, tuple[float, float]] = {
    "cda_m2": (0.6, 1.8),
    "crr": (0.005, 0.025),
    "eta_drivetrain": (0.85, 0.98),
    "p_ice_max_kw": (300.0, 500.0),
}

#: Held-out MAE targets per rung, in seconds per segment.
MAE_TARGETS: dict[str, float] = {"global": 0.15, "event": 0.08, "physics_residual": 0.06}

#: A rung-4 fit needs this many clean laps for a (team, event) cell, or it falls
#: back to the team rung and records that it did.
MIN_CLEAN_LAPS_PER_CELL = 30


class CalibrationError(ValueError):
    """The calibration cannot be run as specified."""


@dataclass
class RungResult:
    """One rung's fit, with everything needed to accept or reject it."""

    rung: str
    parameters: dict[str, float]
    mae_s: float
    rmse_s: float
    n_segments: int
    at_bound: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    cells_fitted: int = 1
    cells_fallen_back: int = 0
    residual_share: float | None = None
    accepted: bool | None = None
    rejection_reason: str | None = None

    @property
    def healthy(self) -> bool:
        return not self.at_bound and not self.violations


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise CalibrationError(f"{name} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise CalibrationError(f"{name} must be finite, got {number!r}")
    return number


def mean_absolute_error(observed: Sequence[Any], predicted: Sequence[Any]) -> float:
    pairs = [(_finite(o, "observed"), _finite(p, "predicted")) for o, p in zip(observed, predicted)]
    if not pairs:
        raise CalibrationError("no paired observations to score")
    return sum(abs(o - p) for o, p in pairs) / len(pairs)


def root_mean_square_error(observed: Sequence[Any], predicted: Sequence[Any]) -> float:
    pairs = [(_finite(o, "observed"), _finite(p, "predicted")) for o, p in zip(observed, predicted)]
    if not pairs:
        raise CalibrationError("no paired observations to score")
    return math.sqrt(sum((o - p) ** 2 for o, p in pairs) / len(pairs))


def check_physical_constraints(parameters: Mapping[str, Any],
                               *, tolerance: float = 1e-6) -> tuple[list[str], list[str]]:
    """Return (parameters sitting at a bound, outright physical violations).

    These are reported separately because they mean different things. A value at
    a bound is a modelling gap the optimiser hit; a value outside one, or an
    efficiency above 1, is a broken fit.
    """
    at_bound: list[str] = []
    violations: list[str] = []
    for name, raw in parameters.items():
        if name not in PARAMETER_BOUNDS:
            continue
        value = _finite(raw, name)
        low, high = PARAMETER_BOUNDS[name]
        if value < low - tolerance or value > high + tolerance:
            violations.append(f"{name}={value:g} outside [{low:g}, {high:g}]")
        elif abs(value - low) <= tolerance or abs(value - high) <= tolerance:
            at_bound.append(
                f"{name}={value:g} at a bound. A fit that runs to a bound means the "
                "model is missing physics, not that the bound should move."
            )
    if "eta_drivetrain" in parameters and _finite(parameters["eta_drivetrain"], "eta") > 1:
        violations.append("eta_drivetrain above 1 is energy from nowhere")
    if "cda_m2" in parameters and _finite(parameters["cda_m2"], "cda_m2") <= 0:
        violations.append("cda_m2 must be positive; negative drag is not physical")
    return at_bound, violations


def fit_parameters(
    residual_fn: Callable[[Sequence[float]], Sequence[float]],
    x0: Sequence[float],
    *,
    names: Sequence[str] = PARAMETER_NAMES,
    loss: str = "soft_l1",
    f_scale: float = 0.05,
) -> dict[str, Any]:
    """Fit one parameter set by robust least squares.

    ``soft_l1`` with a small ``f_scale`` is deliberate: a handful of segments
    will always be contaminated by traffic or a missed flag, and a plain L2 loss
    lets those few dominate a fit over thousands of clean ones.
    """
    from scipy.optimize import least_squares

    if len(x0) != len(names):
        raise CalibrationError(f"x0 has {len(x0)} values for {len(names)} parameters")
    lower = [PARAMETER_BOUNDS[name][0] for name in names]
    upper = [PARAMETER_BOUNDS[name][1] for name in names]
    for index, (value, low, high) in enumerate(zip(x0, lower, upper)):
        if not low <= value <= high:
            raise CalibrationError(
                f"start value {names[index]}={value} is outside its physical bounds "
                f"[{low}, {high}]"
            )

    result = least_squares(residual_fn, x0=list(x0), bounds=(lower, upper),
                           loss=loss, f_scale=f_scale)
    parameters = {name: float(value) for name, value in zip(names, result.x)}
    at_bound, violations = check_physical_constraints(parameters)
    return {
        "parameters": parameters,
        "at_bound": at_bound,
        "violations": violations,
        "cost": float(result.cost),
        "success": bool(result.success),
        "jacobian": result.jac,
        "residuals": result.fun,
    }


def residual_share(physics_prediction: Sequence[Any], residual_correction: Sequence[Any]) -> float:
    """Share of the predicted variation contributed by the learned residual.

    Section 29's gate is about 30 percent. Above that the physics is doing too
    little work and rung 5 is papering over rungs 2 to 4 rather than refining
    them, which is exactly the failure the hierarchy exists to expose.
    """
    physics = [abs(_finite(v, "physics_prediction")) for v in physics_prediction]
    residual = [abs(_finite(v, "residual_correction")) for v in residual_correction]
    total = sum(physics) + sum(residual)
    return 0.0 if total == 0 else sum(residual) / total


def error_by_group(observed: Sequence[Any], predicted: Sequence[Any],
                   groups: Sequence[Any]) -> dict[Any, dict[str, float]]:
    """MAE per group. Section 29 wants speed regime and segment type separately.

    A model good on straights and bad in corners has an acceptable overall MAE
    and is unusable for the DP, because the corners are where the energy
    decision is actually resolved.
    """
    buckets: dict[Any, list[tuple[float, float]]] = {}
    for o, p, g in zip(observed, predicted, groups):
        buckets.setdefault(g, []).append((_finite(o, "observed"), _finite(p, "predicted")))
    return {
        key: {
            "n": len(pairs),
            "mae_s": sum(abs(o - p) for o, p in pairs) / len(pairs),
            "rmse_s": math.sqrt(sum((o - p) ** 2 for o, p in pairs) / len(pairs)),
        }
        for key, pairs in sorted(buckets.items(), key=lambda item: str(item[0]))
    }


def compare_rungs(results: Sequence[RungResult]) -> list[RungResult]:
    """Accept or reject each rung against the previous one, in order.

    Section 34: a rung is kept only if it improves held-out error. Sophistication
    is not evidence, and a rung that improves MAE while sitting on a bound or
    breaking a physical constraint is rejected regardless of its score.
    """
    ordered = sorted(results, key=lambda item: RUNGS.index(item.rung))
    best: float | None = None
    for result in ordered:
        reasons: list[str] = []
        if result.violations:
            reasons.append("physical constraint violated: " + "; ".join(result.violations))
        if result.at_bound:
            reasons.append("parameter at a bound: " + "; ".join(result.at_bound))
        target = MAE_TARGETS.get(result.rung)
        if target is not None and result.mae_s > target:
            reasons.append(f"held-out MAE {result.mae_s:.4f} s misses the {target} s target")
        if best is not None and result.mae_s >= best:
            reasons.append(
                f"held-out MAE {result.mae_s:.4f} s does not improve on the previous "
                f"rung's {best:.4f} s"
            )
        if result.cells_fitted == 0:
            reasons.append(
                "fitted no cells, so its predictions are the previous rung's. A rung "
                "that fits nothing has not been evaluated, it has been skipped."
            )
        if result.residual_share is not None and result.residual_share > 0.30:
            reasons.append(
                f"residual model contributes {result.residual_share:.0%} of predicted "
                "variation; above ~30% the physics is doing too little work"
            )
        result.accepted = not reasons
        result.rejection_reason = "; ".join(reasons) if reasons else None
        # The bar is the best MAE seen, accepted or not. Tracking only accepted
        # rungs lets a rejected one hide the comparison from the next.
        best = result.mae_s if best is None else min(best, result.mae_s)
    return ordered
