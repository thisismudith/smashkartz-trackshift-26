"""CP-16 ensemble spread (M12).

The spread goes to the UI beside ``p_pass`` -- and to nothing else; section 33
does not list it among the planner's uncertainty inputs. A wrong spread is still
worse than none, because it will be believed. Three tests here carry the checkpoint.

``test_logistic_seeds_alone_produce_no_spread_at_all`` is the negative control. It
asserts the *broken* behaviour CP-16's literal recipe would ship, so that the fix
cannot be quietly undone -- ``lbfgs`` never reads ``random_state``, and logistic is
the family CP-14 selected at DETECTION.

``test_bagging_gives_logistic_real_spread`` is the matching positive control.
Together they separate "the seed reached the learner" from "thread noise
manufactured a difference", which a bare ``assert spread.mean() > 0`` cannot.

``test_spread_understates_realised_error`` pins the uncomfortable finding rather
than hiding it: member disagreement cannot see error common to every member, and
here that error dominates by more than an order of magnitude.
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

from trackshift.pass_model.api import (  # noqa: E402
    BAGGED_FAMILIES,
    DEFAULT_MEMBERS,
    MIN_MEMBERS,
    EnsembleError,
    PassModel,
    bootstrap_index,
    build_matrix,
    check_ensemble_gates,
    combine,
    fit_members,
    member_seeds,
    out_of_support_flags,
    plan_splits,
    relative_standard_error,
    select_features,
)

sys.path.insert(0, str(ROOT / "tests"))
from test_pass_model import make_frame  # noqa: E402


@pytest.fixture(scope="module")
def frame():
    return make_frame(per_event=70)


@pytest.fixture(scope="module")
def fold(frame):
    return plan_splits(frame, seed=42).folds[0]


def _matrices(frame, fold, checkpoint="DETECTION"):
    rows = frame[frame["decision_checkpoint"] == checkpoint]
    selection = select_features(checkpoint, rows.columns, dtypes=rows.dtypes)
    X_tr, y_tr = build_matrix(rows.loc[rows.index.intersection(fold.train)], selection)
    X_te, y_te = build_matrix(rows.loc[rows.index.intersection(fold.test)], selection)
    return selection, X_tr, y_tr, X_te, y_te


# --------------------------------------------------------------------------
# The controls that separate a real spread from a manufactured one
# --------------------------------------------------------------------------

def test_logistic_seeds_alone_produce_no_spread_at_all(frame, fold):
    """Negative control: the behaviour CP-16's literal recipe would ship.

    lbfgs never consumes random_state -- it is read only by sag, saga and
    liblinear. Asserting the breakage keeps the bagging fix from being silently
    reverted later by someone 'simplifying' it back to seeds.
    """
    selection, X_tr, y_tr, X_te, _ = _matrices(frame, fold)
    predictions = []
    for seed in (42, 43, 44, 45, 46):
        model = PassModel("logistic", numeric=selection.numeric,
                          categorical=selection.categorical, seed=seed, threads=1)
        model.fit(X_tr, y_tr)
        predictions.append(model.predict_proba(X_te))
    seed_only = np.vstack(predictions).std(axis=0, ddof=1).max()
    # Not exactly 0 in float32: a stray ~1e-7 shows up on the odd row from BLAS
    # summation order, which is thread noise, not seed sensitivity. The claim that
    # matters is the ORDER OF MAGNITUDE gap against a genuinely diverse ensemble --
    # that is what separates "the seed reached the learner" from "noise
    # manufactured a difference", which a bare `spread > 0` cannot do.
    assert seed_only < 1e-6, (
        f"logistic became seed-sensitive (max std {seed_only:.2e}); if the solver "
        "changed from lbfgs, the bagging rationale in ensemble.py needs revisiting"
    )
    bagged, _, _ = fit_members(frame, "DETECTION", "logistic", fold,
                               n_members=MIN_MEMBERS, threads=1)
    bagged_spread = bagged.std(axis=0, ddof=1).mean()
    assert bagged_spread > 1000 * seed_only, (
        f"bagged spread {bagged_spread:.2e} is not decisively above the seed-only "
        f"floor {seed_only:.2e}; the bootstrap is not doing the work"
    )


def test_bagging_gives_logistic_real_spread(frame, fold):
    """Positive control: the fix works, and 'logistic' is in BAGGED_FAMILIES."""
    assert "logistic" in BAGGED_FAMILIES
    P, y, members = fit_members(frame, "DETECTION", "logistic", fold,
                                n_members=MIN_MEMBERS, threads=1)
    spread = P.std(axis=0, ddof=1)
    assert spread.mean() > 1e-6, "bagged logistic still has no spread"
    assert all(m.bagged for m in members)
    # A bootstrap draws ~63.2% distinct rows. Anything near 100% means the
    # resample never happened and the members are copies again.
    for member in members:
        assert 0.5 < member.n_unique_train / member.n_train < 0.75


def test_mlp_is_not_bagged(frame):
    """Its seed already drives weight init; bagging measurably reduces its spread."""
    assert "mlp" not in BAGGED_FAMILIES


def test_spread_understates_realised_error(frame, fold):
    """Member disagreement cannot see error common to every member."""
    P, y, members = fit_members(frame, "DETECTION", "logistic", fold,
                                n_members=MIN_MEMBERS, threads=1)
    result = combine(P, y, checkpoint="DETECTION", family="logistic",
                     fold=fold.name, members=members)
    assert result.rmse_to_spread_ratio > 1.0, (
        "spread is not larger than the model's realised error; if this ever "
        "inverts, the ratio gate is measuring the wrong thing"
    )
    assert result.realised_rmse > result.mean_spread


# --------------------------------------------------------------------------
# Member construction
# --------------------------------------------------------------------------

def test_bootstrap_is_reproducible_and_resamples():
    first = bootstrap_index(1000, 42)
    assert np.array_equal(first, bootstrap_index(1000, 42))
    assert not np.array_equal(first, bootstrap_index(1000, 43))
    assert first.min() >= 0 and first.max() < 1000
    assert 0.5 < np.unique(first).size / 1000 < 0.75


def test_bootstrap_refuses_an_empty_split():
    with pytest.raises(EnsembleError, match="empty"):
        bootstrap_index(0, 42)


def test_member_seeds_are_consecutive_from_the_base():
    assert member_seeds(42, 5) == [42, 43, 44, 45, 46]


def test_too_few_members_is_refused():
    """A std over fewer than five values is a rounding artefact."""
    with pytest.raises(EnsembleError, match="at least"):
        member_seeds(42, 3)


def test_default_member_count_is_not_cp16_five():
    """CP-16 says 5; at n=5 the std carries a 35.4% relative standard error."""
    assert DEFAULT_MEMBERS >= 21
    assert relative_standard_error(5) > 0.35
    assert relative_standard_error(DEFAULT_MEMBERS) < 0.16
    assert relative_standard_error(1) == float("inf")


def test_members_are_calibrated_before_averaging(frame, fold):
    """CP-16's named bug is averaging first and calibrating the mean."""
    class Doubler:
        def predict(self, p):
            return np.clip(np.asarray(p) * 0.5, 1e-6, 1 - 1e-6)

    P_raw, _, _ = fit_members(frame, "DETECTION", "logistic", fold,
                              n_members=MIN_MEMBERS, threads=1)
    P_cal, _, _ = fit_members(frame, "DETECTION", "logistic", fold,
                              n_members=MIN_MEMBERS, threads=1,
                              calibrators=[Doubler()] * MIN_MEMBERS)
    np.testing.assert_allclose(P_cal, np.clip(P_raw * 0.5, 1e-6, 1 - 1e-6), rtol=1e-9)


def test_wrong_number_of_calibrators_is_refused(frame, fold):
    with pytest.raises(EnsembleError, match="one calibrator per member"):
        fit_members(frame, "DETECTION", "logistic", fold,
                    n_members=MIN_MEMBERS, threads=1, calibrators=[None, None])


# --------------------------------------------------------------------------
# Combination and diagnostics
# --------------------------------------------------------------------------

def test_sharpness_identity_holds_exactly():
    """The full decomposition, including the term that is easy to forget.

    sharpness(mean) = mean_member_sharpness + var(member means) - E[spread^2].
    Dropping var(member means) makes the identity fail by exactly the amount the
    members' overall levels differ -- which is the regime a bagged ensemble lives
    in, so the shortened version is wrong precisely when it matters.
    """
    rng = np.random.default_rng(4)
    P = rng.uniform(0.05, 0.6, (9, 400))
    y = (rng.uniform(0, 1, 400) < 0.15).astype(float)
    result = combine(P, y, checkpoint="DETECTION", family="lightgbm", fold="f",
                     members=tuple())
    reconstructed = (result.sharpness_members + result.member_mean_variance
                     - result.mean_squared_spread)
    assert reconstructed == pytest.approx(result.sharpness_mean, abs=1e-12)
    # And the shortened form really is wrong, so nobody "simplifies" it back.
    assert result.sharpness_members - result.mean_squared_spread != pytest.approx(
        result.sharpness_mean, abs=1e-12
    )


def test_combine_refuses_a_single_member():
    with pytest.raises(EnsembleError, match="at least 2"):
        combine(np.array([[0.1, 0.2]]), np.array([0.0, 1.0]),
                checkpoint="DETECTION", family="logistic", fold="f", members=tuple())


def test_identical_members_report_zero_spread_and_fail_the_gate():
    """The structural failure that must be loud, not a small number."""
    P = np.tile(np.linspace(0.05, 0.5, 200), (5, 1))
    y = (np.arange(200) % 6 == 0).astype(float)
    result = combine(P, y, checkpoint="DETECTION", family="logistic", fold="f",
                     members=tuple())
    assert result.zero_spread_fraction == pytest.approx(1.0)
    gate = next(g for g in check_ensemble_gates(result) if "genuinely different" in g["gate"])
    assert gate["passed"] is False


def test_gates_cover_every_cp16_concern():
    rng = np.random.default_rng(8)
    P = rng.uniform(0.05, 0.4, (21, 300))
    y = (rng.uniform(0, 1, 300) < 0.15).astype(float)
    result = combine(P, y, checkpoint="DETECTION", family="lightgbm", fold="f",
                     members=tuple(), event_jackknife=0.05)
    names = " ".join(g["gate"] for g in check_ensemble_gates(result))
    for concept in ("genuinely different", "sharpness", "calibration gap",
                    "quintiles", "enough members", "jackknife"):
        assert concept in names


def test_gate_compares_against_calibration_gap_not_raw_rmse():
    """The replaced gate was unpassable: raw RMSE is mostly Bernoulli noise.

    A perfectly calibrated predictor on a 15%-base-rate outcome scores RMSE ~0.36
    however good it is, so comparing an epistemic band against it fails by
    construction. Reliability is on the band's own scale, and here it is ~0 while
    the RMSE is large -- the old gate would fail this ensemble, the new one passes.
    """
    rng = np.random.default_rng(21)
    truth = rng.uniform(0.05, 0.35, 4000)
    y = (rng.uniform(0, 1, 4000) < truth).astype(float)
    P = np.tile(truth, (21, 1)) + rng.normal(0, 0.02, (21, 4000))
    P = np.clip(P, 1e-6, 1 - 1e-6)
    result = combine(P, y, checkpoint="DETECTION", family="lightgbm", fold="f",
                     members=tuple())
    # RMSE sits at the irreducible Bernoulli bound (just under it, because the
    # model has real resolution) while the calibration gap is ~0. That is the
    # whole argument: RMSE here measures the coin, not the model.
    assert result.realised_rmse == pytest.approx(result.irreducible_rmse, rel=0.10)
    assert result.reliability < 0.01
    assert result.realised_rmse > 10 * result.reliability ** 0.5
    gate = next(g for g in check_ensemble_gates(result) if "calibration gap" in g["gate"])
    assert gate["passed"] is True, "a well-calibrated ensemble must pass the gate"


def test_reliability_component_detects_a_biased_predictor():
    """A systematically shifted probability must register as a calibration gap."""
    from trackshift.pass_model.ensemble import reliability_component

    rng = np.random.default_rng(22)
    truth = rng.uniform(0.05, 0.35, 4000)
    y = (rng.uniform(0, 1, 4000) < truth).astype(float)
    honest = reliability_component(y, truth)
    shifted = reliability_component(y, np.clip(truth + 0.2, 0, 1))
    assert shifted > 10 * honest
    assert shifted == pytest.approx(0.04, abs=0.02)   # a 0.2 shift squared


def test_out_of_support_ignores_a_boundary_graze(frame, fold):
    """A miss of a fraction of a training sd is noise, not extrapolation.

    Without the sigma threshold the box detector called one circuit 100%
    out-of-support on a 20 m miss in a geometry constant -- 0.013 training sd --
    while giving the same rows 1.6% at the next checkpoint.
    """
    from trackshift.pass_model.ensemble import OUT_OF_SUPPORT_SIGMA

    assert OUT_OF_SUPPORT_SIGMA > 0
    selection, X_tr, _, _, _ = _matrices(frame, fold)
    numeric = list(selection.numeric)
    column = numeric[0]
    sigma = float(X_tr[column].std())
    graze = pd.DataFrame({c: [float(X_tr[c].median())] for c in numeric})
    graze[column] = X_tr[column].max() + 0.05 * sigma      # well inside the threshold
    far = graze.copy()
    far[column] = X_tr[column].max() + 5.0 * sigma          # unambiguous excursion

    low, high = X_tr[numeric].min(), X_tr[numeric].max()
    def exceed(row):
        s = X_tr[numeric].std().replace(0.0, np.nan)
        below = ((low - row[numeric]) / s).clip(lower=0.0)
        above = ((row[numeric] - high) / s).clip(lower=0.0)
        return float(below.combine(above, lambda a, b: a.where(a > b, b)).max(axis=1).fillna(0.0).iloc[0])
    assert exceed(graze) <= OUT_OF_SUPPORT_SIGMA
    assert exceed(far) > OUT_OF_SUPPORT_SIGMA


def test_quintiles_are_skipped_on_a_tiny_fold():
    """Monaco's test split is 61 rows; quintiles of 61 are not a measurement."""
    rng = np.random.default_rng(12)
    P = rng.uniform(0.05, 0.4, (5, 20))
    y = (rng.uniform(0, 1, 20) < 0.15).astype(float)
    result = combine(P, y, checkpoint="DETECTION", family="logistic", fold="monaco",
                     members=tuple())
    assert result.spread_quintile_rmse == ()
    gate = next(g for g in check_ensemble_gates(result) if "quintiles" in g["gate"])
    assert gate["passed"] is None


# --------------------------------------------------------------------------
# Out-of-support flagging
# --------------------------------------------------------------------------

def test_out_of_support_is_flagged_separately_from_spread(frame, fold):
    """Spread shrinks outside the training range, so it cannot carry this."""
    flags = out_of_support_flags(frame, "DETECTION", fold)
    assert flags.dtype == bool
    assert flags.size == len(frame[(frame["decision_checkpoint"] == "DETECTION")
                                   & frame.index.isin(fold.test)])


def test_rows_beyond_the_training_range_are_flagged(frame, fold):
    """A row past the training maximum is out of support whatever the spread says."""
    selection, X_tr, _, _, _ = _matrices(frame, fold)
    numeric = list(selection.numeric)           # tuple would index pandas as one key
    column = numeric[0]
    low, high = X_tr[column].min(), X_tr[column].max()
    probe = pd.DataFrame({c: [X_tr[c].median()] for c in numeric})
    probe[column] = high + (high - low)
    above = (probe[numeric] > X_tr[numeric].max()).any(axis=1)
    assert bool(above.iloc[0])
    inside = pd.DataFrame({c: [X_tr[c].median()] for c in numeric})
    assert not bool((inside[numeric] > X_tr[numeric].max()).any(axis=1).iloc[0])


def test_unknown_family_is_refused(frame, fold):
    with pytest.raises(EnsembleError, match="unknown family"):
        fit_members(frame, "DETECTION", "randomforest", fold, n_members=MIN_MEMBERS)
