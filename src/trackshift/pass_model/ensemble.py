"""Ensemble spread for the M10 pass model (Tanveer CP-16, section 42).

This number goes to the UI beside ``p_pass``. It is *not* a planner input:
AGENTS.md section 33 lists the planner's uncertainty sources as rival type,
energy-twin parameters and gap noise, and pass-model spread is not among them.
A wrong spread is still worse than no spread, because it will be believed. So the measurements came
first, and three of them changed the design.

**CP-16's recipe produces a constant 0.0 for the family CP-14 selected.**
``LogisticRegression(solver="lbfgs")`` never reads ``random_state`` -- it is
consumed only by ``sag``, ``saga`` and ``liblinear``. Five members at seeds 42-46
are bitwise identical, per-row std exactly 0.0, on **all 21** checkpoint-by-fold
cells measured. CP-14 selected logistic at DETECTION. So members are built on a
**bootstrap of the training rows** as well as a seed (:data:`BAGGED_FAMILIES`),
which takes logistic from 0.0 to 0.0222 mean std. The three boosters are bagged
too -- not because the seed fails there, but because it reaches only the
per-tree row and column samplers and understates disagreement 3-4x. If only some
families were bagged, DETECTION's spread and BRAKING's spread would be different
quantities under one column name. MLP is *not* bagged: its ``random_state``
already seeds weight initialisation, and bagging reduces its spread.

**Five members is too few to report a per-row number.** The standard deviation of
five values has a 36.3% relative standard error and a 95% interval nearly five
times wide; two rows with identical true sigma differ by more than 2x in the
reported figure 20.8% of the time. :data:`DEFAULT_MEMBERS` is 21 (15.9%), which
costs seconds -- the whole five-member sweep ran in 13.3 s.

**Member disagreement is not uncertainty, and this module says so.** On the Monza
fold, seed-and-bag disagreement measured 0.0084 while the realised RMSE of
``p_pass`` was 0.4222 and its systematic bias alone 0.1396. Disagreement among
members that share a training distribution cannot see error that is common to all
of them. Leave-one-training-event-out refitting measured 3.66x larger. Both are
computed and both are reported: ``ensemble_spread`` keeps CP-16's definition so
the API contract is unchanged, and ``event_jackknife_sd`` sits beside it as the
honest figure, with their ratio recorded so nobody reads the smaller one as the
model's confidence.

One CP-16 check is **false by construction** and is recorded as a limitation
rather than asserted: "spread is larger in sparse regions". Outside the training
support a boosted tree returns its boundary leaf, so every member agrees and
spread *shrinks* -- 20 probe points beyond the training maximum gave one distinct
prediction per member at 0.79x the in-support spread. Extrapolation is flagged
separately via :func:`out_of_support_flags`; spread cannot carry that meaning.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .candidates import CANDIDATES, DEFAULT_SEED
from .features import build_matrix, select_features

__all__ = [
    "ENSEMBLE_SCHEMA_VERSION",
    "DEFAULT_MEMBERS",
    "MIN_MEMBERS",
    "BAGGED_FAMILIES",
    "EnsembleError",
    "MemberFit",
    "EnsembleResult",
    "bootstrap_index",
    "member_seeds",
    "fit_members",
    "combine",
    "event_jackknife_sd",
    "out_of_support_flags",
    "relative_standard_error",
    "check_ensemble_gates",
    "reliability_component",
]

ENSEMBLE_SCHEMA_VERSION = "m12_pass_ensemble_v1"

#: CP-16 says five. Five gives a 36.3% relative standard error on the per-row
#: std; 21 gives 15.9% at a cost of seconds. The members are small, as CP-16
#: itself notes, so the count is the cheapest quality lever available.
DEFAULT_MEMBERS = 21

#: Below this the per-row number is a rounding artefact rather than a measurement.
MIN_MEMBERS = 5

#: Families whose members need a bootstrap of the training rows, because the seed
#: alone under-diversifies them (logistic: not at all). MLP is excluded -- its
#: seed already drives weight init, and bagging shrinks its spread.
BAGGED_FAMILIES: frozenset[str] = frozenset({"logistic", "lightgbm", "xgboost", "catboost"})

#: Beneath this the ensemble has lost more discrimination than it should. Audited
#: against the exact identity recorded in :func:`combine`.
MIN_SHARPNESS_RETENTION = 0.90

#: The RMS calibration gap must fit inside one reported standard deviation.
#:
#: This replaces an earlier gate that compared the spread against the fold's raw
#: RMSE. That comparison was wrong and unpassable by construction: the RMSE of a
#: probability against a 0/1 label is dominated by irreducible Bernoulli variance
#: b(1-b), which no amount of model skill removes -- a perfectly calibrated model
#: on a 15%-base-rate event still scores RMSE ~0.36. Measured on five of seven
#: folds the realised MSE was at or *below* b(1-b), i.e. the model beat the
#: no-skill bound, and the old gate still failed them.
#:
#: Reliability -- the Murphy component measuring how far observed rates sit from
#: promised ones -- is the part of the error a spread can legitimately be asked to
#: cover, and it is on the same scale as the spread.
MAX_RELIABILITY_TO_BAND_RATIO = 1.0

#: A per-feature exceedance smaller than this many training standard deviations is
#: noise at the boundary, not extrapolation. Without it the box detector calls
#: Monaco 100% out-of-support on a 20 m miss in one geometry column -- 0.013
#: training sd -- while giving the same 61 rows 1.6% one checkpoint later.
OUT_OF_SUPPORT_SIGMA = 0.25


class EnsembleError(ValueError):
    """Raised when an ensemble cannot be built or its spread cannot be trusted."""


@dataclass(frozen=True)
class MemberFit:
    """One ensemble member: its seed, its resample, and what it predicted."""

    seed: int
    bagged: bool
    n_train: int
    n_unique_train: int
    best_iteration: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "bagged": self.bagged,
            "n_train": self.n_train,
            "n_unique_train": self.n_unique_train,
            "best_iteration": self.best_iteration,
        }


@dataclass
class EnsembleResult:
    """``p_pass``, its spread, and every number needed to judge the spread."""

    checkpoint: str
    family: str
    fold: str
    members: tuple[MemberFit, ...]
    p_pass: Any
    ensemble_spread: Any
    mean_spread: float
    p95_spread: float
    max_spread: float
    zero_spread_fraction: float
    sharpness_mean: float
    sharpness_members: float
    sharpness_retention: float
    member_mean_variance: float
    mean_squared_spread: float
    relative_standard_error: float
    event_jackknife_sd: float | None = None
    jackknife_ratio: float | None = None
    realised_rmse: float | None = None
    rmse_to_spread_ratio: float | None = None
    reliability: float | None = None
    irreducible_rmse: float | None = None
    spread_quintile_rmse: tuple[float, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = ENSEMBLE_SCHEMA_VERSION

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "checkpoint": self.checkpoint,
            "family": self.family,
            "fold": self.fold,
            "n_members": len(self.members),
            "members": [m.as_dict() for m in self.members],
            "mean_spread": self.mean_spread,
            "p95_spread": self.p95_spread,
            "max_spread": self.max_spread,
            "zero_spread_fraction": self.zero_spread_fraction,
            "sharpness_mean": self.sharpness_mean,
            "sharpness_members": self.sharpness_members,
            "sharpness_retention": self.sharpness_retention,
            "member_mean_variance": self.member_mean_variance,
            "mean_squared_spread": self.mean_squared_spread,
            "sharpness_identity_residual": abs(
                self.sharpness_mean
                - (self.sharpness_members + self.member_mean_variance
                   - self.mean_squared_spread)
            ),
            "spread_relative_standard_error": self.relative_standard_error,
            "event_jackknife_sd": self.event_jackknife_sd,
            "jackknife_to_member_ratio": self.jackknife_ratio,
            "realised_rmse": self.realised_rmse,
            "rmse_to_spread_ratio": self.rmse_to_spread_ratio,
            "reliability": self.reliability,
            "rms_calibration_gap": (None if self.reliability is None
                                    else self.reliability ** 0.5),
            "irreducible_rmse": self.irreducible_rmse,
            "spread_quintile_rmse": list(self.spread_quintile_rmse),
            "notes": list(self.notes),
        }


def member_seeds(base_seed: int, n_members: int) -> list[int]:
    """Consecutive seeds from ``base_seed``, as CP-16 specifies (42, 43, ...)."""
    if n_members < MIN_MEMBERS:
        raise EnsembleError(
            f"n_members must be at least {MIN_MEMBERS}; a standard deviation over "
            f"{n_members} values is a rounding artefact, not a measurement"
        )
    return [int(base_seed) + i for i in range(int(n_members))]


def bootstrap_index(n: int, seed: int):
    """A bootstrap resample of ``n`` row positions, reproducible from ``seed``.

    The same seed drives the resample and the learner, so a member is fully
    described by one integer and the artifact stays reproducible.
    """
    import numpy as np

    if n <= 0:
        raise EnsembleError("cannot bootstrap an empty training split")
    return np.random.default_rng(seed).integers(0, n, n)


def relative_standard_error(n_members: int) -> float:
    """Relative standard error of a standard deviation from ``n`` samples.

    ``1 / sqrt(2(n-1))``. At n=5 that is 35.4%, which is why :data:`DEFAULT_MEMBERS`
    is not 5. Reported per ensemble so a reader can size the error bar on the
    error bar.
    """
    if n_members < 2:
        return float("inf")
    return float(1.0 / (2.0 * (n_members - 1)) ** 0.5)


def fit_members(
    frame,
    checkpoint: str,
    family: str,
    fold,
    *,
    n_members: int = DEFAULT_MEMBERS,
    seed: int = DEFAULT_SEED,
    threads: int = 1,
    deterministic: bool = True,
    include_identity: bool = False,
    calibrators: Sequence[Any] | None = None,
):
    """Fit the members and return ``(P, y_test, member_fits)``.

    ``P`` is ``(n_members, n_test)``. When ``calibrators`` is supplied it must
    hold one fitted calibrator per member, applied to that member **before**
    averaging -- CP-16's failure table is explicit that averaging first and
    calibrating the mean is the bug.
    """
    import numpy as np

    from .candidates import PassModel

    if family not in CANDIDATES:
        raise EnsembleError(f"unknown family {family!r}")
    seeds = member_seeds(seed, n_members)
    if calibrators is not None and len(calibrators) != len(seeds):
        raise EnsembleError(
            f"expected one calibrator per member; got {len(calibrators)} for {len(seeds)}"
        )

    rows = frame[frame["decision_checkpoint"] == checkpoint]
    if rows.empty:
        raise EnsembleError(f"no rows at checkpoint {checkpoint}")
    selection = select_features(
        checkpoint, rows.columns, include_identity=include_identity, dtypes=rows.dtypes
    )
    X_tr, y_tr = build_matrix(rows.loc[rows.index.intersection(fold.train)], selection)
    val_index = rows.index.intersection(fold.validation)
    X_val, y_val = (build_matrix(rows.loc[val_index], selection)
                    if len(val_index) else (None, None))
    X_te, y_te = build_matrix(rows.loc[rows.index.intersection(fold.test)], selection)
    if len(y_tr) == 0 or len(y_te) == 0:
        raise EnsembleError(f"fold {fold.name}: empty training or test split")

    bag = family in BAGGED_FAMILIES
    predictions, fits = [], []
    for position, member_seed in enumerate(seeds):
        if bag:
            picks = bootstrap_index(len(y_tr), member_seed)
            X_member, y_member = X_tr.iloc[picks], y_tr[picks]
            if len(set(map(int, y_member))) < 2:
                # A bootstrap can draw a single class at a 15% base rate on a
                # small fold. Resampling with a derived seed keeps the member
                # reproducible without silently dropping it.
                picks = bootstrap_index(len(y_tr), member_seed + 10_000)
                X_member, y_member = X_tr.iloc[picks], y_tr[picks]
            unique = int(np.unique(picks).size)
        else:
            X_member, y_member, unique = X_tr, y_tr, len(y_tr)

        model = PassModel(
            family, numeric=selection.numeric, categorical=selection.categorical,
            seed=member_seed, threads=threads, deterministic=deterministic,
        )
        model.fit(X_member, y_member, X_val, y_val)
        p = np.asarray(model.predict_proba(X_te), dtype=float)
        # Calibrate the MEMBER, then average. The reverse is CP-16's named bug.
        if calibrators is not None:
            p = np.asarray(calibrators[position].predict(p), dtype=float)
        predictions.append(p)
        fits.append(MemberFit(
            seed=member_seed, bagged=bag, n_train=len(y_member),
            n_unique_train=unique, best_iteration=model.best_iteration,
        ))
    return np.vstack(predictions), y_te, tuple(fits)


def combine(
    P,
    y_test,
    *,
    checkpoint: str,
    family: str,
    fold: str,
    members: Sequence[MemberFit],
    event_jackknife: float | None = None,
    notes: Sequence[str] = (),
) -> EnsembleResult:
    """Mean, spread, and the diagnostics that say whether the spread means anything."""
    import numpy as np

    matrix = np.asarray(P, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] < 2:
        raise EnsembleError(f"need at least 2 members; got shape {matrix.shape}")
    y = np.asarray(y_test, dtype=float)

    p_pass = matrix.mean(axis=0)
    spread = matrix.std(axis=0, ddof=1)

    # The sharpness trade is an exact decomposition, not an approximation, which
    # is what makes it auditable. Applying the law of total variance to X[m, j]
    # along both axes and equating:
    #
    #   sharpness_members + var(member means) = mean_j(spread^2) + sharpness_mean
    #
    # so sharpness_mean = sharpness_members + var(member means) - mean_j(spread^2).
    # The middle term is easy to drop and the identity then fails by exactly the
    # amount the members' overall levels differ -- which is precisely the case
    # where a bagged ensemble is interesting, so it is kept and reported.
    sharpness_mean = float(np.var(p_pass))
    sharpness_members = float(np.mean([np.var(row) for row in matrix]))
    member_mean_variance = float(np.var(matrix.mean(axis=1)))
    mean_squared_spread = float(np.mean(matrix.std(axis=0, ddof=0) ** 2))
    retention = sharpness_mean / sharpness_members if sharpness_members > 0 else float("nan")

    errors = p_pass - y
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    base = float(np.mean(y))
    # Irreducible: the Bernoulli variance of the outcome itself. Reported so the
    # raw RMSE is never mistaken for model error.
    irreducible = float(np.sqrt(base * (1.0 - base)))
    try:
        reliability = reliability_component(y, p_pass)
    except Exception:
        reliability = None
    mean_spread = float(spread.mean())
    ratio = rmse / mean_spread if mean_spread > 0 else float("inf")

    # Does a larger spread actually mark a worse prediction? Quintiles of spread
    # against realised RMSE. If this is flat, the spread is not tracking error.
    quintiles: list[float] = []
    if spread.size >= 25:
        order = np.argsort(spread)
        for chunk in np.array_split(order, 5):
            quintiles.append(float(np.sqrt(np.mean(errors[chunk] ** 2))))

    return EnsembleResult(
        checkpoint=checkpoint, family=family, fold=fold, members=tuple(members),
        p_pass=p_pass, ensemble_spread=spread,
        mean_spread=mean_spread,
        p95_spread=float(np.percentile(spread, 95)),
        max_spread=float(spread.max()),
        zero_spread_fraction=float((spread < 1e-12).mean()),
        sharpness_mean=sharpness_mean,
        sharpness_members=sharpness_members,
        sharpness_retention=float(retention),
        member_mean_variance=member_mean_variance,
        mean_squared_spread=mean_squared_spread,
        relative_standard_error=relative_standard_error(matrix.shape[0]),
        event_jackknife_sd=event_jackknife,
        jackknife_ratio=(event_jackknife / mean_spread
                         if event_jackknife is not None and mean_spread > 0 else None),
        realised_rmse=rmse,
        rmse_to_spread_ratio=ratio,
        reliability=reliability,
        irreducible_rmse=irreducible,
        spread_quintile_rmse=tuple(quintiles),
        notes=tuple(notes),
    )


def event_jackknife_sd(
    frame,
    checkpoint: str,
    family: str,
    fold,
    *,
    seed: int = DEFAULT_SEED,
    threads: int = 1,
    deterministic: bool = True,
    include_identity: bool = False,
) -> tuple[float, int]:
    """Leave-one-training-event-out disagreement: the honest uncertainty figure.

    Members that share a training distribution cannot disagree about error common
    to all of them, and that common error dominates here. Refitting with a whole
    event removed perturbs the distribution itself, and measured 3.66x larger than
    seed-and-bag disagreement on the same rows.

    Returns ``(sd, n_refits)``. With seven events the resample caps at six blocks,
    which is a real limit and belongs in the report rather than being smoothed over.
    """
    import numpy as np

    from .candidates import PassModel

    rows = frame[frame["decision_checkpoint"] == checkpoint]
    selection = select_features(
        checkpoint, rows.columns, include_identity=include_identity, dtypes=rows.dtypes
    )
    train_index = rows.index.intersection(fold.train)
    events = sorted({str(v) for v in frame.loc[train_index, "event"].unique()})
    if len(events) < 3:
        raise EnsembleError(
            f"event jackknife needs at least 3 training events; got {len(events)}"
        )
    val_index = rows.index.intersection(fold.validation)
    X_val, y_val = (build_matrix(rows.loc[val_index], selection)
                    if len(val_index) else (None, None))
    X_te, _ = build_matrix(rows.loc[rows.index.intersection(fold.test)], selection)

    event_column = frame["event"].astype(str)
    predictions = []
    for dropped in events:
        keep = train_index[event_column.loc[train_index] != dropped]
        X_tr, y_tr = build_matrix(rows.loc[keep], selection)
        if len(y_tr) == 0 or len(set(map(int, y_tr))) < 2:
            continue
        model = PassModel(
            family, numeric=selection.numeric, categorical=selection.categorical,
            seed=seed, threads=threads, deterministic=deterministic,
        ).fit(X_tr, y_tr, X_val, y_val)
        predictions.append(np.asarray(model.predict_proba(X_te), dtype=float))
    if len(predictions) < 2:
        raise EnsembleError("event jackknife produced fewer than 2 refits")
    return float(np.vstack(predictions).std(axis=0, ddof=1).mean()), len(predictions)


def reliability_component(y_true, p_pred, bins: int = 10) -> float:
    """Murphy reliability: the count-weighted mean squared calibration gap.

    ``sum_k n_k (obar_k - pbar_k)^2 / n``. This is the part of the Brier score that
    measures broken promises, and the only part a predictive band can be asked to
    cover. Resolution can mask it entirely -- one fold here showed zero "excess"
    error over the no-skill bound while carrying the worst calibration gap in the
    table, because its resolution offset it.

    Shares :func:`~trackshift.pass_model.metrics.reliability_table`, so the bin
    edges and their tie-collapsing behaviour match everything else that bins.
    """
    import numpy as np

    from .metrics import reliability_table

    table = reliability_table(y_true, p_pred, bins)
    total = sum(b.n for b in table)
    if total == 0:
        return float("nan")
    return float(
        sum(b.n * (b.observed_rate - b.mean_predicted) ** 2 for b in table) / total
    )


def out_of_support_flags(frame, checkpoint: str, fold, *, include_identity: bool = False):
    """Mark test rows outside the training range of each feature.

    Spread cannot carry this. Beyond the training support a boosted tree returns
    its boundary leaf, so every member agrees and spread *shrinks* -- measured at
    0.79x the in-support value on probe points past the training maximum. So
    extrapolation is reported as its own flag rather than inferred from a small
    spread, which would read as confidence.
    """
    import numpy as np

    rows = frame[frame["decision_checkpoint"] == checkpoint]
    selection = select_features(
        checkpoint, rows.columns, include_identity=include_identity, dtypes=rows.dtypes
    )
    X_tr, _ = build_matrix(rows.loc[rows.index.intersection(fold.train)], selection)
    X_te, _ = build_matrix(rows.loc[rows.index.intersection(fold.test)], selection)
    numeric = list(selection.numeric)
    if not numeric:
        return np.zeros(len(X_te), dtype=bool)
    low, high = X_tr[numeric].min(), X_tr[numeric].max()
    # Scale the exceedance by each feature's training spread. A bare min/max box
    # treats a 20 m miss on a geometry constant the same as a genuine excursion,
    # and that is not a hypothetical: it is why Monaco reads 100% here and 1.6%
    # one checkpoint later on the same 61 rows.
    sigma = X_tr[numeric].std().replace(0.0, np.nan)
    below = ((low - X_te[numeric]) / sigma).clip(lower=0.0)
    above = ((X_te[numeric] - high) / sigma).clip(lower=0.0)
    exceedance = below.combine(above, lambda a, b: a.where(a > b, b)).max(axis=1)
    return np.asarray(exceedance.fillna(0.0) > OUT_OF_SUPPORT_SIGMA, dtype=bool)


def check_ensemble_gates(result: EnsembleResult) -> list[dict[str, Any]]:
    """CP-16's acceptance checks, restated so they can actually fire.

    CP-16's own thresholds ("spread not ~0", "spread not > 0.25") are not on a
    meaningful scale at a 15% base rate, and its "ensemble mean at least as well
    calibrated as any member" reduces to a Jensen identity when tested against the
    member mean -- measured 0.17756 against 0.17765, a 9e-5 mathematical necessity
    that would pass for any ensemble ever built. These are the replacements.
    """
    gates: list[dict[str, Any]] = []

    def gate(name: str, passed: bool | None, detail: str) -> None:
        gates.append({"gate": name, "passed": passed, "detail": detail})

    gate(
        "Members are genuinely different (spread is not structurally zero)",
        result.zero_spread_fraction <= 0.5,
        f"{result.zero_spread_fraction:.1%} of rows have zero spread. Above 50% means "
        "the seed never reached the learner -- lbfgs ignores random_state, so an "
        "unbagged logistic ensemble is five copies of one model.",
    )
    gate(
        f"Ensemble keeps at least {MIN_SHARPNESS_RETENTION:.0%} of member sharpness",
        result.sharpness_retention >= MIN_SHARPNESS_RETENTION,
        f"retention {result.sharpness_retention:.3f} "
        f"(mean {result.sharpness_mean:.5f} of member {result.sharpness_members:.5f}). "
        "Averaging always costs discrimination; the identity sharpness(mean) = "
        "mean_member_sharpness + var(member means) - E[spread^2] makes the trade exact.",
    )
    if result.reliability is None:
        gate("RMS calibration gap fits inside one reported band", None,
             "reliability was not computed for this cell")
    else:
        band = result.mean_spread
        rms_gap = result.reliability ** 0.5
        ratio = rms_gap / band if band > 0 else float("inf")
        gate(
            f"RMS calibration gap fits inside {MAX_RELIABILITY_TO_BAND_RATIO} band(s)",
            ratio <= MAX_RELIABILITY_TO_BAND_RATIO,
            f"RMS calibration gap {rms_gap:.4f} against band {band:.4f} = {ratio:.2f}x. "
            "Reliability, not raw RMSE: a probability scored against a 0/1 label carries "
            f"irreducible Bernoulli error of about {result.irreducible_rmse:.3f} here, "
            "which no band should be asked to cover.",
        )
    if result.spread_quintile_rmse:
        rising = all(a <= b + 1e-9 for a, b in zip(result.spread_quintile_rmse,
                                                   result.spread_quintile_rmse[1:]))
        gate(
            "Realised error rises with spread across quintiles",
            rising,
            "RMSE by spread quintile: "
            + ", ".join(f"{v:.4f}" for v in result.spread_quintile_rmse)
            + ". Flat or falling means the spread is not tracking error at all.",
        )
    else:
        gate("Realised error rises with spread across quintiles", None,
             "fewer than 25 test rows; quintiles are not defined")
    gate(
        "Per-row spread is estimated from enough members to report",
        result.relative_standard_error <= 0.20,
        f"{len(result.members)} members gives a {result.relative_standard_error:.1%} "
        "relative standard error on each per-row std. CP-16's five would give 35.4%.",
    )
    if result.jackknife_ratio is not None:
        gate(
            "Member disagreement is reported beside the event-jackknife figure",
            True,
            f"event-jackknife sd is {result.jackknife_ratio:.2f}x member disagreement. "
            "The larger figure is the honest uncertainty; ensemble_spread keeps CP-16's "
            "definition for the API contract.",
        )
    return gates
