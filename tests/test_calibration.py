"""CP-15 probability calibration (M11).

Most of this file defends against a single failure mode: **a calibrator that is
worse than useless while every CP-15 gate improves.**

ECE is not a proper scoring rule and is minimised by predicting the base rate, so
a calibrator that has collapsed to a constant scores near-perfectly on the very
metric meant to judge it -- and on a low-base-rate fold Brier rewards the same
shrinkage. A run could therefore report improved calibration on every checkpoint
while shipping a model that tells the planner nothing. The tests that matter here
are the ones that would catch that:

``test_constant_calibrator_is_refused_despite_near_perfect_ece`` pins the trap
directly. ``test_platt_with_an_inverted_slope_is_refused`` pins the other one: a
Platt fit on a low-signal calibration set picks its slope sign essentially at
random, and the wrong sign silently reverses the model's ranking while improving
ECE. ``test_in_sample_isotonic_ece_is_zero_by_identity`` pins why no number in
this checkpoint may be computed on the calibration set.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")
pytest.importorskip("scipy")

from trackshift.pass_model.api import (  # noqa: E402
    EPS,
    METHODS,
    MIN_ISOTONIC_N,
    CalibrationError,
    IsotonicCalibrator,
    PlattCalibrator,
    aggregate_variants,
    assert_calibration_disjoint,
    bin_diagnostics,
    crossfit_calibration_set,
    evaluate_variants,
    expected_calibration_error,
    fit_calibrator,
    logit,
    plan_splits,
    recommended_method,
    select_variant,
    sharpness,
    wilson_interval,
)
from trackshift.pass_model.calibrate import CalibrationSet  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_pass_model import make_frame  # noqa: E402


@pytest.fixture(scope="module")
def frame():
    return make_frame(per_event=60)


def calibration_data(n=2000, seed=0, signal=True):
    """Probabilities and labels where the model is deliberately overconfident."""
    rng = np.random.default_rng(seed)
    truth = rng.uniform(0.02, 0.6, n)
    y = (rng.uniform(0, 1, n) < truth).astype(int)
    # Push toward the extremes: the classic tree-probability distortion.
    p = np.clip(truth ** 1.6 * 1.4, 1e-4, 1 - 1e-4) if signal else rng.uniform(0.1, 0.9, n)
    return p, y


# --------------------------------------------------------------------------
# The hazards that make a wrong result look right
# --------------------------------------------------------------------------

def test_constant_calibrator_is_refused_despite_near_perfect_ece():
    """A constant is perfectly calibrated and useless. It must not be selectable."""
    rng = np.random.default_rng(3)
    n = 1500
    p = rng.uniform(0.05, 0.95, n)
    y = (rng.uniform(0, 1, n) < 0.15).astype(int)   # labels independent of p

    constant = np.full(n, y.mean())
    assert expected_calibration_error(y, constant, bins=10) < 1e-6, (
        "precondition: a constant at the base rate must score ~0 ECE"
    )
    assert sharpness(constant) == pytest.approx(0.0)

    # Isotonic on signal-free data collapses toward exactly that constant.
    calibrator, fit = fit_calibrator("isotonic", p, y)
    mapped = calibrator.predict(p)
    assert sharpness(mapped) < sharpness(p)
    if fit.degenerate:
        assert not fit.usable
        assert any("constant" in r or "distinct output level" in r for r in fit.reasons)


def test_a_degenerate_variant_cannot_win_selection():
    good = {"ok": True, "method": "platt", "ece_measurable": True, "rejected_reason": None,
            "sharpness": 0.02, "metrics": {"brier": 0.11, "ece": 0.04}}
    collapsed = {"ok": True, "method": "isotonic", "ece_measurable": True,
                 "rejected_reason": "output spans only 0.0010", "sharpness": 1e-9,
                 "metrics": {"brier": 0.10, "ece": 0.001}}
    # The collapsed variant has better Brier AND better ECE, and must still lose.
    assert select_variant([collapsed, good])["method"] == "platt"


def test_platt_with_an_inverted_slope_is_refused():
    """A positive slope reverses the ranking. Nothing in the fit forbids it."""
    p = np.linspace(0.05, 0.95, 200)
    inverted = PlattCalibrator(a=+4.0, b=0.0)   # a > 0 => monotone decreasing
    mapped = inverted.predict(p)
    assert mapped[0] > mapped[-1], "precondition: a>0 must invert the mapping"

    from trackshift.pass_model.calibration import _describe
    y = (np.arange(200) % 7 == 0).astype(float)
    fit = _describe("platt", inverted, p, y, {"a": 4.0, "b": 0.0})
    assert fit.monotone_increasing is False
    assert not fit.usable
    assert any("not monotone" in r for r in fit.reasons)


def test_a_well_formed_platt_fit_preserves_ranking_exactly():
    """Platt with a negative slope is strictly monotone, so ROC-AUC is unchanged."""
    from sklearn.metrics import roc_auc_score

    p, y = calibration_data(n=3000, seed=5)
    calibrator, fit = fit_calibrator("platt", p, y)
    assert fit.monotone_increasing
    assert fit.params["a"] < 0, "sklearn's parameterisation: increasing requires a < 0"
    assert roc_auc_score(y, calibrator.predict(p)) == pytest.approx(roc_auc_score(y, p))


def test_isotonic_may_lose_a_little_auc_to_ties_and_that_is_not_a_bug():
    """CP-15 says any AUC change is a bug. True for Platt, false for isotonic.

    Measured OUT of sample, which is the only way the claim is meaningful. Applied
    to unseen data the fitted step function is fixed and weakly monotone, so it can
    only merge neighbouring ranks and AUC cannot rise. In sample it can and does
    rise -- isotonic has fitted those labels -- which is itself a leakage signature
    and the reason no CP-15 number is computed on the calibration set.
    """
    from sklearn.metrics import roc_auc_score

    p_cal, y_cal = calibration_data(n=3000, seed=6)
    p_test, y_test = calibration_data(n=3000, seed=61)
    calibrator, _ = fit_calibrator("isotonic", p_cal, y_cal)
    mapped = calibrator.predict(p_test)
    assert np.unique(mapped).size < np.unique(p_test).size, "isotonic must flatten to steps"
    assert roc_auc_score(y_test, mapped) <= roc_auc_score(y_test, p_test) + 1e-12

    # In sample it goes the other way, which is the point of the docstring above.
    assert roc_auc_score(y_cal, calibrator.predict(p_cal)) >= roc_auc_score(y_cal, p_cal)


def test_in_sample_isotonic_ece_is_zero_by_identity():
    """Why no CP-15 number may be computed on the calibration set."""
    p, y = calibration_data(n=1200, seed=7)
    calibrator, _ = fit_calibrator("isotonic", p, y)
    in_sample = expected_calibration_error(y, calibrator.predict(p), bins=10)
    uncalibrated = expected_calibration_error(y, p, bins=10)
    assert uncalibrated > 1e-3, "precondition: the raw model is genuinely miscalibrated"
    # Not exactly 0: reliability_table rounds bin edges. The identity holds to
    # float noise, which is four orders below any real miscalibration.
    assert in_sample < 1e-6, (
        "PAVA assigns each block its observed mean and the equal-count edges collapse "
        "onto those levels, so predicted == observed in every bin exactly"
    )


def test_realised_bin_count_is_reported_not_the_requested_one():
    """Calibrated output is full of ties, and ties collapse the quantile edges."""
    p = np.repeat([0.1, 0.2, 0.3], 400)
    y = (np.random.default_rng(1).uniform(0, 1, 1200) < p).astype(int)
    diagnostics = bin_diagnostics(y, p, bins=10)
    assert diagnostics["requested_bins"] == 10
    assert diagnostics["realised_bins"] <= 3
    assert diagnostics["realised_bins"] == len(diagnostics["bins"])


def test_ece_is_marked_not_measurable_on_a_tiny_evaluation_set():
    """At n=61 a perfectly calibrated predictor measures ECE ~0.11."""
    rng = np.random.default_rng(11)
    p = rng.uniform(0.05, 0.4, 61)
    y = (rng.uniform(0, 1, 61) < p).astype(int)
    # Calibrated BY CONSTRUCTION, yet ECE is far from zero purely from sampling.
    assert expected_calibration_error(y, p, bins=10) > 0.05
    from trackshift.pass_model.calibration import MIN_ECE_N
    assert 61 < MIN_ECE_N


def test_aggregation_is_count_weighted_not_a_mean_of_means():
    """The CP-14 Brier-skill trap, applied to ECE across folds of unequal size."""
    rows = [
        {"ok": True, "checkpoint": "DETECTION", "family": "logistic", "method": "platt",
         "fold": "tiny", "sharpness": 0.01, "realised_bins": 4,
         "metrics": {"n": 61, "n_positive": 1, "brier": 0.40, "ece": 0.40,
                     "log_loss": 1.0, "roc_auc": 0.5, "pr_auc": 0.2}},
        {"ok": True, "checkpoint": "DETECTION", "family": "logistic", "method": "platt",
         "fold": "big", "sharpness": 0.02, "realised_bins": 10,
         "metrics": {"n": 1228, "n_positive": 160, "brier": 0.10, "ece": 0.02,
                     "log_loss": 0.3, "roc_auc": 0.75, "pr_auc": 0.4}},
    ]
    entry = aggregate_variants(rows)[0]
    unweighted = (0.40 + 0.02) / 2
    weighted = (0.40 * 61 + 0.02 * 1228) / (61 + 1228)
    assert entry["ece"] == pytest.approx(weighted)
    assert abs(entry["ece"] - unweighted) > 0.1, "the two must differ materially"
    assert entry["n"] == 1289


# --------------------------------------------------------------------------
# Calibrator mechanics
# --------------------------------------------------------------------------

def test_isotonic_clips_out_of_range_inputs_instead_of_returning_nan():
    """The 1.9.1 default is out_of_bounds='nan', which silently poisons the test set."""
    p, y = calibration_data(n=1500, seed=9)
    calibrator, _ = fit_calibrator("isotonic", p, y)
    out_of_range = np.array([0.0, 1.0, p.min() / 2, min(1.0, p.max() * 1.5)])
    mapped = calibrator.predict(out_of_range)
    assert np.isfinite(mapped).all(), "out-of-range input produced NaN"


def test_every_calibrator_output_stays_strictly_inside_the_unit_interval():
    """Isotonic emits exact 0.0 and 1.0; one saturated error makes log loss infinite."""
    p, y = calibration_data(n=1500, seed=10)
    for method in METHODS:
        calibrator, _ = fit_calibrator(method, p, y)
        mapped = calibrator.predict(np.array([0.0, 1e-9, 0.5, 1 - 1e-9, 1.0]))
        assert mapped.min() >= EPS
        assert mapped.max() <= 1.0 - EPS


def test_uncalibrated_variant_is_the_identity_apart_from_clipping():
    p = np.array([0.2, 0.5, 0.8])
    calibrator, fit = fit_calibrator("uncalibrated", p, np.array([0, 1, 1]))
    np.testing.assert_allclose(calibrator.predict(p), p)
    assert fit.method == "uncalibrated"


def test_single_class_calibration_set_is_refused_by_both_methods():
    """Both would otherwise return a degenerate constant and score perfectly."""
    p = np.linspace(0.05, 0.9, 80)
    zeros = np.zeros(80)
    with pytest.raises(CalibrationError):
        PlattCalibrator.fit(p, zeros)
    with pytest.raises(CalibrationError):
        IsotonicCalibrator.fit(p, zeros)


def test_bad_input_is_refused():
    with pytest.raises(CalibrationError, match="unknown method"):
        fit_calibrator("temperature", [0.5], [1])
    with pytest.raises(CalibrationError, match="differ in length"):
        fit_calibrator("platt", [0.5, 0.6], [1])
    with pytest.raises(CalibrationError, match="empty"):
        fit_calibrator("platt", [], [])
    with pytest.raises(CalibrationError, match=r"\[0, 1\]"):
        fit_calibrator("platt", [0.5, 1.5], [0, 1])


def test_logit_is_finite_at_the_asymptotes():
    assert np.isfinite(logit([0.0, 1.0])).all()


def test_recommended_method_follows_the_sample_size_rule():
    small, why_small = recommended_method(MIN_ISOTONIC_N - 1)
    large, why_large = recommended_method(MIN_ISOTONIC_N)
    assert small == "platt" and "overfit" in why_small
    assert large == "isotonic" and str(MIN_ISOTONIC_N) in why_large


def test_wilson_interval_brackets_the_observed_rate():
    low, high = wilson_interval(3, 50)
    assert 0.0 <= low <= 3 / 50 <= high <= 1.0
    # Degenerate counts must still yield a usable band, not a zero-width one.
    assert wilson_interval(0, 50)[1] > 0.0
    assert wilson_interval(50, 50)[0] < 1.0
    assert wilson_interval(0, 0) == (0.0, 1.0)


# --------------------------------------------------------------------------
# Cross-fitting and disjointness
# --------------------------------------------------------------------------

def test_crossfit_calibration_set_comes_only_from_training_events(frame):
    plan = plan_splits(frame, seed=42)
    fold = plan.folds[0]
    cal = crossfit_calibration_set(frame, "DETECTION", "logistic", fold, threads=1)
    train_events = {str(v) for v in frame.loc[fold.train, "event"].unique()}
    assert set(cal.source_events) <= train_events
    assert cal.n > 0 and cal.inner_fits >= 2


def test_crossfit_never_draws_from_the_test_or_validation_event(frame):
    plan = plan_splits(frame, seed=42)
    fold = plan.folds[0]
    cal = crossfit_calibration_set(frame, "DETECTION", "logistic", fold, threads=1)
    test_events = {str(v) for v in frame.loc[fold.test, "event"].unique()}
    validation_events = {str(v) for v in frame.loc[fold.validation, "event"].unique()}
    assert not (set(cal.source_events) & test_events)
    assert not (set(cal.source_events) & validation_events)


def test_disjointness_check_catches_a_shared_event(frame):
    """Not a tautology: same event, disjoint rows, must still be refused."""
    plan = plan_splits(frame, seed=42)
    fold = plan.folds[0]
    test_event = str(frame.loc[fold.test, "event"].iloc[0])
    smuggled = CalibrationSet(
        p=np.array([0.5]), y=np.array([1.0]),
        source_events=(test_event,), inner_fits=1, n=1, n_positive=1, base_rate=1.0,
    )
    with pytest.raises(CalibrationError, match="same event"):
        assert_calibration_disjoint(frame, fold, smuggled)


def test_disjointness_check_refuses_an_empty_calibration_set(frame):
    """An empty set would satisfy any set-intersection test vacuously."""
    plan = plan_splits(frame, seed=42)
    empty = CalibrationSet(p=np.array([]), y=np.array([]), source_events=(),
                           inner_fits=0, n=0, n_positive=0, base_rate=float("nan"))
    with pytest.raises(CalibrationError, match="vacuously"):
        assert_calibration_disjoint(frame, plan.folds[0], empty)


def test_evaluate_variants_scores_every_method_on_held_out_data(frame):
    plan = plan_splits(frame, seed=42)
    results, cal = evaluate_variants(frame, "DETECTION", "logistic", plan.folds[0],
                                     threads=1)
    assert cal is not None and cal.n > 0
    assert {r.method for r in results} == set(METHODS)
    for result in results:
        if not result.ok:
            continue
        assert 0.0 <= result.metrics["brier"] <= 1.0
        assert result.metrics["n"] > 0
        assert result.realised_bins is not None


def test_a_failing_variant_is_recorded_not_raised(frame):
    """One broken variant must cost its own row, not the whole comparison."""
    plan = plan_splits(frame, seed=42)
    fold = plan.folds[0]
    broken = type(fold)("broken", frame.index[:0], fold.validation, fold.test)
    results, _ = evaluate_variants(frame, "DETECTION", "logistic", broken, threads=1)
    assert results and all(not r.ok for r in results)
    assert all(r.error for r in results)


def test_calibration_set_is_larger_than_any_single_event_block(frame):
    """The measured reason cross-fitting was chosen over a dedicated event."""
    plan = plan_splits(frame, seed=42)
    fold = plan.folds[0]
    cal = crossfit_calibration_set(frame, "DETECTION", "logistic", fold, threads=1)
    # Like for like: the calibration set is DETECTION rows only, so the single-event
    # comparison must be too, not all three checkpoints of that event.
    detection = frame[frame["decision_checkpoint"] == "DETECTION"]
    per_event = detection.loc[detection.index.intersection(fold.train)].groupby("event").size().max()
    assert cal.n > int(per_event), (
        f"cross-fitted {cal.n} rows from {cal.inner_fits} events vs {int(per_event)} "
        "from the largest single event -- the measured reason for cross-fitting"
    )
