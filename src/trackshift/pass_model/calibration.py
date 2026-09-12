"""Probability calibration for the M10 pass model (Tanveer CP-15, section 27).

Three variants per (checkpoint, family): uncalibrated, Platt/sigmoid, isotonic.
The DP consumes probabilities, so a model that ranks battles correctly while
being systematically overconfident prices energy confidently and wrongly. That is
what this checkpoint exists to fix, and what its failure modes quietly undo.

Two departures from CP-15's written text, both forced by measurement.

**The API in CP-15 does not exist.** CP-15 specifies::

    CalibratedClassifierCV(base_estimator, method="sigmoid", cv="prefit")

In the installed scikit-learn 1.9.1 both halves are removed, not deprecated:
``base_estimator`` raises ``TypeError`` (the parameter is ``estimator``) and
``cv="prefit"`` raises ``InvalidParameterError`` with no deprecation path. Worse,
:class:`~trackshift.pass_model.candidates.PassModel` cannot be wrapped under any
spelling -- it has no ``classes_``, ``get_params``, ``predict`` or
``__sklearn_tags__``, and its ``predict_proba`` returns a 1-D array. Making it
wrappable would mean forcing ``fit(X_train, y_train, X_val, y_val)`` into
sklearn's two-argument contract, which destroys the early-stopping split CP-14
depends on for the three tree families.

So calibrators are fitted on the **probability vector** instead. That is not a
compromise: fitting on the vector was verified to reproduce
``CalibratedClassifierCV(FrozenEstimator(est))`` bitwise -- maximum absolute
difference 0.0, ``np.array_equal`` true, for both methods.

**A calibrator can be worse than useless while every CP-15 gate improves.** This
is the real hazard, and most of this module is defence against it:

*A constant is perfectly calibrated.* ECE is not a proper scoring rule and is
minimised by predicting the base rate -- measured at 2.8e-17. On a low-base-rate
fold Brier rewards that shrinkage too, so a degenerate calibrator that has thrown
away all discrimination **wins** CP-14's selection rule outright. Hence
:func:`sharpness` and the degeneracy checks: a calibrator that collapses the
output range is rejected, never silently preferred.

*Platt's slope is unconstrained in sign.* ``p' = expit(-(A*f + B))`` is monotone
increasing only when ``A < 0``; nothing in the maximum-likelihood fit forces it.
On a 61-row, one-positive calibration set -- exactly what the Miami fold gets
under leave-one-event-out pairing -- ``A > 0`` in 227 of 400 trials. The mapping
then *inverts the model's ranking*, and ECE improves while it happens. Hence
:attr:`CalibrationFit.monotone_increasing`, checked and refused.

*In-sample isotonic ECE is 0.0 by algebraic identity.* PAVA assigns every row in
a block the block's observed mean, and the equal-count bin edges collapse onto
those levels, so predicted equals observed in every bin exactly. Measured through
this repo's own :func:`~trackshift.pass_model.metrics.expected_calibration_error`:
0.000000000000. Any number computed on the calibration set is meaningless, so
this module only ever reports calibrator quality out of sample.

*ECE is noise at small n.* A predictor perfectly calibrated by construction
measures ECE 0.111 at n=61 and 0.047 at n=324 -- larger than the 0.07-0.14
miscalibration CP-15 is trying to remove. :data:`MIN_ECE_N` marks where the
number stops being evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "CALIBRATION_SCHEMA_VERSION",
    "METHODS",
    "EPS",
    "MIN_ISOTONIC_N",
    "MIN_ECE_N",
    "MIN_BIN_N",
    "CalibrationError",
    "CalibrationFit",
    "Calibrator",
    "IdentityCalibrator",
    "PlattCalibrator",
    "IsotonicCalibrator",
    "fit_calibrator",
    "recommended_method",
    "sharpness",
    "wilson_interval",
    "logit",
]

CALIBRATION_SCHEMA_VERSION = "m11_pass_calibration_v1"

#: ``uncalibrated`` is a first-class variant, not a placeholder: section 27 asks
#: for the comparison, and calibration is only worth shipping where it wins.
METHODS: tuple[str, ...] = ("uncalibrated", "platt", "isotonic")

#: Probabilities are clipped into this open interval before being reported.
#: Isotonic emits exact 0.0 and 1.0 (12 rows in 1,500 measured), and a single
#: misclassified saturated row makes log loss infinite.
EPS = 1e-6

#: Below this many calibration rows, isotonic's step function overfits and Platt
#: is preferred (CP-15). The choice is recorded, never silent.
MIN_ISOTONIC_N = 1000

#: Below this many evaluation rows, ECE is dominated by its own positive bias and
#: should be read as "not measurable", not as a small number.
MIN_ECE_N = 200

#: A reliability bin below this count cannot support a calibration claim.
MIN_BIN_N = 50

#: A calibrator whose output spans less than this has collapsed to a near
#: constant. It will score well on ECE and Brier and be useless to the planner.
MIN_OUTPUT_SPREAD = 0.02

#: Fewer distinct output levels than this is a degenerate mapping.
MIN_DISTINCT_LEVELS = 3


class CalibrationError(ValueError):
    """Raised when a calibrator cannot be fitted or trusted."""


def logit(p):
    """Log-odds, with the input clipped away from the asymptotes."""
    import numpy as np

    arr = np.clip(np.asarray(p, dtype=float), 1e-12, 1.0 - 1e-12)
    return np.log(arr / (1.0 - arr))


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Used for the per-bin confidence band in the reliability diagram. Wilson
    rather than the normal approximation because the bins that matter most are
    the extreme ones, where observed rates sit near 0 or 1 and the normal
    interval runs outside [0, 1] or collapses to zero width.
    """
    if n <= 0:
        return (0.0, 1.0)
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = (z / denom) * ((phat * (1 - phat) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, centre - half), min(1.0, centre + half))


def sharpness(p) -> float:
    """Variance of the predicted probabilities.

    The quantity ECE cannot see. A constant predictor has sharpness 0 and perfect
    calibration; it is also worthless to the planner, which needs the *spread*
    between a 20% chance and an 80% one. Reported alongside every calibrated
    result so that trade cannot be made silently.
    """
    import numpy as np

    return float(np.var(np.asarray(p, dtype=float)))


@dataclass(frozen=True)
class CalibrationFit:
    """What a fitted calibrator is, and whether it can be trusted.

    ``degenerate`` and ``monotone_increasing`` are the two fields that decide
    whether a variant may be selected at all. Both are measured on the
    calibrator's own output, not assumed from the method.
    """

    method: str
    n_calibration: int
    n_positive: int
    base_rate: float
    distinct_levels: int
    output_low: float
    output_high: float
    monotone_increasing: bool
    degenerate: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    params: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = CALIBRATION_SCHEMA_VERSION

    @property
    def usable(self) -> bool:
        return not self.degenerate and self.monotone_increasing

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "method": self.method,
            "n_calibration": self.n_calibration,
            "n_positive": self.n_positive,
            "base_rate": self.base_rate,
            "distinct_levels": self.distinct_levels,
            "output_range": [self.output_low, self.output_high],
            "output_spread": self.output_high - self.output_low,
            "monotone_increasing": self.monotone_increasing,
            "degenerate": self.degenerate,
            "usable": self.usable,
            "reasons": list(self.reasons),
            "params": dict(self.params),
        }


class Calibrator:
    """Maps model probabilities to calibrated probabilities."""

    method = "uncalibrated"

    def predict(self, p):  # pragma: no cover - interface
        raise NotImplementedError

    def _clip(self, values):
        import numpy as np

        return np.clip(np.asarray(values, dtype=float), EPS, 1.0 - EPS)


class IdentityCalibrator(Calibrator):
    """The uncalibrated baseline. Clips only, so log loss stays finite."""

    method = "uncalibrated"

    def predict(self, p):
        return self._clip(p)


class PlattCalibrator(Calibrator):
    """Platt scaling: ``p' = expit(-(A * logit(p) + B))``.

    Implements scikit-learn's ``_sigmoid_calibration`` rather than importing it.
    That function is private API; a checkpointed artifact that silently changes
    behaviour on a library upgrade is worse than twenty lines of vendored code.
    The target smoothing below is Platt's own correction, which keeps the fit
    finite when a calibration set is perfectly separable.
    """

    method = "platt"

    def __init__(self, a: float, b: float) -> None:
        self.a = float(a)
        self.b = float(b)

    @classmethod
    def fit(cls, p_cal, y_cal) -> "PlattCalibrator":
        import numpy as np
        from scipy.optimize import minimize
        from scipy.special import expit

        f = logit(p_cal)
        y = np.asarray(y_cal, dtype=float)
        prior0 = float((y <= 0).sum())
        prior1 = float(y.shape[0] - prior0)
        if prior0 == 0 or prior1 == 0:
            raise CalibrationError(
                f"Platt needs both classes; got {int(prior1)} positive and "
                f"{int(prior0)} negative calibration rows"
            )

        # Platt's target smoothing: without it a separable calibration set drives
        # the slope to infinity and the mapping saturates to a step.
        target = np.where(y > 0, (prior1 + 1.0) / (prior1 + 2.0), 1.0 / (prior0 + 2.0))

        def objective(ab):
            prediction = expit(-(ab[0] * f + ab[1]))
            prediction = np.clip(prediction, 1e-12, 1 - 1e-12)
            return -float(
                (target * np.log(prediction) + (1 - target) * np.log(1 - prediction)).sum()
            )

        start = np.array([0.0, np.log((prior0 + 1.0) / (prior1 + 1.0))])
        result = minimize(objective, start, method="L-BFGS-B")
        return cls(float(result.x[0]), float(result.x[1]))

    def predict(self, p):
        from scipy.special import expit

        return self._clip(expit(-(self.a * logit(p) + self.b)))


class IsotonicCalibrator(Calibrator):
    """Isotonic regression on the raw probability.

    ``out_of_bounds="clip"`` is mandatory, not stylistic: the 1.9.1 default is
    ``"nan"``, which silently poisons every test probability outside the
    calibration range -- and out-of-range is the normal case when calibrating on
    one set of events and evaluating on another.
    """

    method = "isotonic"

    def __init__(self, model: Any) -> None:
        self._model = model

    @classmethod
    def fit(cls, p_cal, y_cal) -> "IsotonicCalibrator":
        import numpy as np
        from sklearn.isotonic import IsotonicRegression

        y = np.asarray(y_cal, dtype=float)
        if len(np.unique(y)) < 2:
            raise CalibrationError(
                "isotonic needs both classes in the calibration set; it would "
                "otherwise return a constant and score perfectly on ECE"
            )
        model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        model.fit(np.asarray(p_cal, dtype=float), y)
        return cls(model)

    def predict(self, p):
        import numpy as np

        return self._clip(self._model.predict(np.asarray(p, dtype=float)))


def recommended_method(n_calibration: int) -> tuple[str, str]:
    """Which method CP-15's sample-size rule licenses, and why."""
    if n_calibration >= MIN_ISOTONIC_N:
        return "isotonic", (
            f"{n_calibration} calibration rows is at or above the {MIN_ISOTONIC_N}-row "
            "floor, so isotonic's step function has enough data not to overfit"
        )
    return "platt", (
        f"{n_calibration} calibration rows is below the {MIN_ISOTONIC_N}-row floor; "
        "isotonic would overfit, so Platt's two-parameter fit is preferred"
    )


def _describe(method: str, calibrator: Calibrator, p_cal, y_cal, params: Mapping[str, Any]):
    """Measure what the fitted calibrator actually does, and refuse it if degenerate."""
    import numpy as np

    y = np.asarray(y_cal, dtype=float)
    probe = np.linspace(float(np.min(p_cal)), float(np.max(p_cal)), 256)
    mapped = np.asarray(calibrator.predict(probe), dtype=float)

    distinct = int(np.unique(np.round(mapped, 12)).size)
    low, high = float(mapped.min()), float(mapped.max())
    spread = high - low

    # Monotone non-decreasing over the probe. Identity and isotonic are so by
    # construction; Platt is only when its slope is negative, which the fit does
    # not guarantee, so it is measured rather than trusted.
    monotone = bool(np.all(np.diff(mapped) >= -1e-12))

    reasons: list[str] = []
    if spread < MIN_OUTPUT_SPREAD:
        reasons.append(
            f"output spans only {spread:.4f} (<{MIN_OUTPUT_SPREAD}); the calibrator has "
            "collapsed to a near-constant, which scores well on ECE and Brier while "
            "discarding the discrimination the planner needs"
        )
    if distinct < MIN_DISTINCT_LEVELS:
        reasons.append(
            f"only {distinct} distinct output level(s) (<{MIN_DISTINCT_LEVELS})"
        )
    if not monotone:
        reasons.append(
            "mapping is not monotone non-decreasing; it reorders the model's ranking. "
            "For Platt this means the fitted slope has the wrong sign, which happens "
            "on low-signal calibration sets and improves ECE while doing it"
        )

    return CalibrationFit(
        method=method,
        n_calibration=int(y.size),
        n_positive=int(y.sum()),
        base_rate=float(y.mean()) if y.size else float("nan"),
        distinct_levels=distinct,
        output_low=low,
        output_high=high,
        monotone_increasing=monotone,
        degenerate=bool(reasons and (spread < MIN_OUTPUT_SPREAD
                                     or distinct < MIN_DISTINCT_LEVELS)),
        reasons=tuple(reasons),
        params=dict(params),
    )


def fit_calibrator(
    method: str,
    p_cal: Sequence[float],
    y_cal: Sequence[Any],
) -> tuple[Calibrator, CalibrationFit]:
    """Fit one calibrator and measure whether it can be trusted.

    Returns the calibrator and its :class:`CalibrationFit`. A degenerate or
    rank-inverting fit is *returned*, not raised -- the report must be able to
    say that a variant was rejected and why, which it cannot do if the run died.
    Callers gate on :attr:`CalibrationFit.usable`.
    """
    import numpy as np

    if method not in METHODS:
        raise CalibrationError(f"unknown method {method!r}; expected one of {METHODS}")
    p = np.asarray(p_cal, dtype=float)
    y = np.asarray(y_cal, dtype=float)
    if p.shape != y.shape:
        raise CalibrationError(f"p_cal and y_cal differ in length: {p.shape} vs {y.shape}")
    if p.size == 0:
        raise CalibrationError("cannot fit a calibrator on an empty calibration set")
    if not np.isfinite(p).all():
        raise CalibrationError("p_cal contains non-finite values")
    if p.min() < 0.0 or p.max() > 1.0:
        raise CalibrationError(f"p_cal must lie in [0, 1]; got [{p.min()}, {p.max()}]")

    if method == "uncalibrated":
        return IdentityCalibrator(), _describe("uncalibrated", IdentityCalibrator(), p, y, {})
    if method == "platt":
        calibrator = PlattCalibrator.fit(p, y)
        return calibrator, _describe(
            "platt", calibrator, p, y, {"a": calibrator.a, "b": calibrator.b}
        )
    calibrator = IsotonicCalibrator.fit(p, y)
    levels = int(np.unique(calibrator._model.y_thresholds_).size) if hasattr(
        calibrator._model, "y_thresholds_"
    ) else 0
    return calibrator, _describe("isotonic", calibrator, p, y, {"fitted_levels": levels})
