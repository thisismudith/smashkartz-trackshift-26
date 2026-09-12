"""Cross-fitted calibration for the M10 pass model (Tanveer CP-15).

CP-15 says to fit the calibrator on the validation split and evaluate on test.
That cannot be done honestly here, for a reason CP-15 could not have known: in
CP-14 the validation split is **already consumed by early stopping** for
LightGBM, XGBoost and CatBoost. Fitting a calibrator on the same rows the model
stopped against measures how well the model is calibrated on data it was tuned
on, which is not the question.

The obvious repair -- spend a whole event as a dedicated calibration fold -- was
measured against the real table and is arithmetically unusable. The seven
candidate single-event blocks hold 61 to 1,228 labelled rows at base rates from
1.6% to 27.1%; only one of seven clears CP-15's own ~1,000-row isotonic floor,
and the leave-one-event-out pairing hands the Miami fold a calibration set of
**61 rows containing one positive**. Neither method crashes there. Platt returns
a mapping compressed into [0.012, 0.084]; isotonic returns two levels. Both then
score well on ECE, because a near-constant is well calibrated.

So calibration data is **cross-fitted over the training events** instead. For
each training event the model is refitted on the other training events and
predicts the held-out one, and those out-of-fold predictions are pooled. The
result is 2,379-3,538 rows at 13.2%-17.8% base rate in every fold -- all seven
isotonic-capable -- at the cost of a handful of extra sub-second fits.

Three properties make this the right shape rather than merely the bigger one:

- **No training data is spent.** The outer train/validation/test index sets stay
  byte-identical to CP-14, so CP-15 is purely additive and nothing already
  measured is invalidated.
- **Every calibration row is out-of-sample for the model that scored it.** An
  inner model never sees the event it predicts, which is exactly the relationship
  the test set has to the outer model.
- **Calibration and evaluation never share an event.** Calibration rows come from
  the training events, test rows from the held-out one, so the disjointness
  CP-15 asks to assert is structural rather than incidental.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .calibration import (
    METHODS,
    MIN_BIN_N,
    MIN_ECE_N,
    CalibrationError,
    fit_calibrator,
    recommended_method,
    sharpness,
    wilson_interval,
)
from .candidates import DEFAULT_SEED, PassModel
from .features import build_matrix, select_features
from .metrics import evaluate, reliability_table

__all__ = [
    "CalibrationSet",
    "VariantResult",
    "crossfit_calibration_set",
    "assert_calibration_disjoint",
    "evaluate_variants",
    "select_variant",
    "aggregate_variants",
    "bin_diagnostics",
]


@dataclass(frozen=True)
class CalibrationSet:
    """Pooled out-of-fold predictions, with the provenance that makes them valid."""

    p: Any
    y: Any
    source_events: tuple[str, ...]
    inner_fits: int
    n: int
    n_positive: int
    base_rate: float
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "n_positive": self.n_positive,
            "base_rate": self.base_rate,
            "source_events": list(self.source_events),
            "inner_fits": self.inner_fits,
            "notes": list(self.notes),
        }


@dataclass
class VariantResult:
    """One calibration variant scored on the held-out test event."""

    checkpoint: str
    family: str
    fold: str
    method: str
    ok: bool
    fit: Mapping[str, Any] = field(default_factory=dict)
    metrics: Mapping[str, Any] = field(default_factory=dict)
    sharpness: float | None = None
    realised_bins: int | None = None
    ece_measurable: bool = True
    rejected_reason: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "checkpoint": self.checkpoint,
            "family": self.family,
            "fold": self.fold,
            "method": self.method,
            "ok": self.ok,
            "fit": dict(self.fit),
            "metrics": dict(self.metrics),
            "sharpness": self.sharpness,
            "realised_bins": self.realised_bins,
            "ece_measurable": self.ece_measurable,
            "rejected_reason": self.rejected_reason,
            "error": self.error,
        }


def _event_key(frame, index) -> set[tuple[str, str]]:
    """(year, event) pairs covered by an index.

    Keyed on the pair, not the event name, so the year table is not spuriously
    refused once historical seasons exist: 2022 Monza and 2026 Monza are
    different data and may legitimately sit on opposite sides of a split.
    """
    subset = frame.loc[index, ["year", "event"]]
    return {(str(r["year"]), str(r["event"])) for r in subset.to_dict("records")}


def crossfit_calibration_set(
    frame,
    checkpoint: str,
    family: str,
    fold,
    *,
    seed: int = DEFAULT_SEED,
    threads: int = 1,
    deterministic: bool = True,
    include_identity: bool = False,
) -> CalibrationSet:
    """Pool out-of-fold predictions over this fold's training events."""
    rows = frame[frame["decision_checkpoint"] == checkpoint]
    if rows.empty:
        raise CalibrationError(f"no rows at checkpoint {checkpoint}")
    selection = select_features(
        checkpoint, rows.columns, include_identity=include_identity, dtypes=rows.dtypes
    )

    train_index = rows.index.intersection(fold.train)
    if len(train_index) == 0:
        raise CalibrationError(f"fold {fold.name}: empty training split at {checkpoint}")
    events = sorted({str(v) for v in frame.loc[train_index, "event"].unique()})
    if len(events) < 2:
        raise CalibrationError(
            f"fold {fold.name}: cross-fitting needs at least 2 training events, got {events}"
        )

    validation_index = rows.index.intersection(fold.validation)
    X_val, y_val = (build_matrix(rows.loc[validation_index], selection)
                    if len(validation_index) else (None, None))

    import numpy as np

    event_column = frame["event"].astype(str)
    parts_p, parts_y, used, notes = [], [], [], []
    for held_out in events:
        inner_test = train_index[event_column.loc[train_index] == held_out]
        inner_train = train_index[event_column.loc[train_index] != held_out]
        if len(inner_test) == 0 or len(inner_train) == 0:
            continue
        X_tr, y_tr = build_matrix(rows.loc[inner_train], selection)
        X_te, y_te = build_matrix(rows.loc[inner_test], selection)
        if len(y_tr) == 0 or len(y_te) == 0:
            continue
        if len(set(map(int, y_tr))) < 2:
            notes.append(f"{held_out}: inner training split had a single class; skipped")
            continue
        model = PassModel(
            family,
            numeric=selection.numeric,
            categorical=selection.categorical,
            seed=seed,
            threads=threads,
            deterministic=deterministic,
        )
        # Early stopping uses the OUTER validation event, which is in neither the
        # inner training split nor the held-out event, so it cannot leak into the
        # calibration rows this produces.
        model.fit(X_tr, y_tr, X_val, y_val)
        parts_p.append(np.asarray(model.predict_proba(X_te), dtype=float))
        parts_y.append(np.asarray(y_te, dtype=float))
        used.append(held_out)

    if not parts_p:
        raise CalibrationError(
            f"fold {fold.name}: cross-fitting produced no calibration rows at {checkpoint}"
        )
    p = np.concatenate(parts_p)
    y = np.concatenate(parts_y)
    return CalibrationSet(
        p=p, y=y,
        source_events=tuple(used),
        inner_fits=len(used),
        n=int(y.size),
        n_positive=int(y.sum()),
        base_rate=float(y.mean()),
        notes=tuple(notes),
    )


def assert_calibration_disjoint(frame, fold, calibration_set: CalibrationSet) -> None:
    """Refuse a calibration set that shares an event with the evaluation set.

    Section 27: do not calibrate and evaluate on the same event. Written as
    separable claims so it cannot pass vacuously -- an empty calibration set or a
    fold with no test rows fails here rather than trivially satisfying a
    set-intersection test.
    """
    if calibration_set.n == 0:
        raise CalibrationError("calibration set is empty; disjointness would hold vacuously")
    test_keys = _event_key(frame, fold.test)
    if not test_keys:
        raise CalibrationError(f"fold {fold.name} has no test rows; nothing to evaluate")
    calibration_keys = {
        (str(y), str(e))
        for (y, e) in _event_key(frame, frame.index[frame["event"].astype(str).isin(
            calibration_set.source_events
        )])
    }
    overlap = calibration_keys & test_keys
    if overlap:
        raise CalibrationError(
            f"fold {fold.name}: calibration and evaluation share {sorted(overlap)}. "
            "Section 27 forbids calibrating and evaluating on the same event."
        )


def bin_diagnostics(y_true, p_pred, bins: int = 10) -> dict[str, Any]:
    """Reliability bins with Wilson bands, plus the *realised* bin count.

    The realised count matters and is not the requested one. Equal-count edges
    are built with ``np.unique``, so ties collapse them -- and calibrated output
    is exactly the regime where ties are common. A five-level predictor yields 4
    bins; one with 95% of its mass at a single value yields 1, at which point the
    whole reliability diagram is a single point and any ECE from it is an
    artefact of the binning rather than a measurement of calibration.
    """
    table = reliability_table(y_true, p_pred, bins)
    out = []
    for stats in table:
        successes = int(round(stats.observed_rate * stats.n))
        low, high = wilson_interval(successes, stats.n)
        out.append({
            **stats.as_dict(),
            "wilson_low": low,
            "wilson_high": high,
            "within_band": low <= stats.mean_predicted <= high,
            "assessable": stats.n >= MIN_BIN_N,
        })
    assessable = [b for b in out if b["assessable"]]
    return {
        "requested_bins": bins,
        "realised_bins": len(out),
        "bins": out,
        "assessable_bins": len(assessable),
        "bins_outside_band": sum(1 for b in assessable if not b["within_band"]),
    }


def evaluate_variants(
    frame,
    checkpoint: str,
    family: str,
    fold,
    *,
    seed: int = DEFAULT_SEED,
    threads: int = 1,
    deterministic: bool = True,
    include_identity: bool = False,
    methods: Sequence[str] = METHODS,
    bins: int = 10,
) -> tuple[list[VariantResult], CalibrationSet | None]:
    """Score every calibration variant for one (checkpoint, family, fold)."""
    import numpy as np

    results: list[VariantResult] = []

    def failed(method: str, message: str) -> VariantResult:
        return VariantResult(checkpoint, family, fold.name, method, False, error=message)

    try:
        calibration_set = crossfit_calibration_set(
            frame, checkpoint, family, fold, seed=seed, threads=threads,
            deterministic=deterministic, include_identity=include_identity,
        )
        assert_calibration_disjoint(frame, fold, calibration_set)
    except Exception as exc:
        return [failed(m, f"{type(exc).__name__}: {exc}") for m in methods], None

    try:
        rows = frame[frame["decision_checkpoint"] == checkpoint]
        selection = select_features(
            checkpoint, rows.columns, include_identity=include_identity, dtypes=rows.dtypes
        )
        X_tr, y_tr = build_matrix(rows.loc[rows.index.intersection(fold.train)], selection)
        val_index = rows.index.intersection(fold.validation)
        X_val, y_val = (build_matrix(rows.loc[val_index], selection)
                        if len(val_index) else (None, None))
        test_index = rows.index.intersection(fold.test)
        X_te, y_te = build_matrix(rows.loc[test_index], selection)
        model = PassModel(
            family, numeric=selection.numeric, categorical=selection.categorical,
            seed=seed, threads=threads, deterministic=deterministic,
        ).fit(X_tr, y_tr, X_val, y_val)
        p_test = np.asarray(model.predict_proba(X_te), dtype=float)
        train_rate = float(np.mean(y_tr))
    except Exception as exc:
        return [failed(m, f"{type(exc).__name__}: {exc}") for m in methods], calibration_set

    licensed, why = recommended_method(calibration_set.n)
    for method in methods:
        try:
            calibrator, fit = fit_calibrator(method, calibration_set.p, calibration_set.y)
            calibrated = np.asarray(calibrator.predict(p_test), dtype=float)
            metrics = evaluate(y_te, calibrated, bins=bins, base_rate=train_rate)
            diagnostics = bin_diagnostics(y_te, calibrated, bins)
            rejected = None
            if not fit.usable:
                rejected = "; ".join(fit.reasons) or "calibrator refused"
            elif method == "isotonic" and licensed != "isotonic":
                rejected = why
            results.append(VariantResult(
                checkpoint=checkpoint, family=family, fold=fold.name, method=method,
                ok=True,
                fit={**fit.as_dict(), "licensed_method": licensed, "licence_reason": why},
                metrics={**metrics, "bin_diagnostics": diagnostics},
                sharpness=sharpness(calibrated),
                realised_bins=diagnostics["realised_bins"],
                # ECE at small n measures its own positive bias, not calibration.
                ece_measurable=bool(metrics["n"] >= MIN_ECE_N),
                rejected_reason=rejected,
            ))
        except Exception as exc:
            results.append(failed(method, f"{type(exc).__name__}: {exc}"))
    return results, calibration_set


def select_variant(results: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """Pick the best usable variant, refusing the ones that only look good.

    Calibration-first, as section 26 requires -- but a variant is only eligible
    if it was not rejected (degenerate or rank-inverting) and its ECE was
    measurable. Without that gate a collapsed calibrator wins outright: ECE is
    minimised by a constant at the base rate, and Brier rewards the same
    shrinkage on a low-base-rate fold.

    Ties on ECE break toward **higher sharpness**, so where two variants are
    equally calibrated the one that still discriminates is preferred.
    """
    eligible = [
        r for r in results
        if r.get("ok") and not r.get("rejected_reason") and r.get("ece_measurable")
    ]
    if not eligible:
        return None

    def key(entry: Mapping[str, Any]):
        metrics = entry.get("metrics") or {}
        return (
            float(metrics.get("brier", float("inf"))),
            float(metrics.get("ece", float("inf"))),
            -float(entry.get("sharpness") or 0.0),
        )

    return sorted(eligible, key=key)[0]


def aggregate_variants(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Pool variants across folds, weighting by n.

    Count-weighted, not an unweighted mean of per-fold values. ECE is itself a
    count-weighted mean within a fold, so averaging fold ECEs equally over folds
    whose n differ twentyfold (61 against 1,228) is a mean of means -- the same
    ratio trap CP-14 already hit with Brier skill, and here it moves the headline
    by around 3.5x on identical predictions. It also averages away the n-dependent
    bias that makes small folds look badly calibrated when they are merely small.
    """
    from collections import defaultdict

    grouped: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for entry in results:
        if entry.get("ok"):
            grouped[(entry["checkpoint"], entry["family"], entry["method"])].append(entry)

    out: list[dict[str, Any]] = []
    for (checkpoint, family, method), members in sorted(grouped.items()):
        scored = [m for m in members if (m.get("metrics") or {}).get("n")]
        if not scored:
            continue
        total = sum(m["metrics"]["n"] for m in scored)
        entry: dict[str, Any] = {
            "checkpoint": checkpoint, "family": family, "method": method,
            "folds": len(members), "n": total,
            "n_positive": sum(m["metrics"]["n_positive"] for m in scored),
            "rejected_folds": sum(1 for m in members if m.get("rejected_reason")),
            "folds_ece_measurable": sum(1 for m in members if m.get("ece_measurable")),
        }
        for metric in ("brier", "ece", "log_loss", "roc_auc", "pr_auc"):
            weighted = [
                (m["metrics"][metric], m["metrics"]["n"]) for m in scored
                if m["metrics"].get(metric) is not None
            ]
            entry[metric] = (
                sum(v * n for v, n in weighted) / sum(n for _, n in weighted)
                if weighted else None
            )
        entry["sharpness"] = (
            sum((m.get("sharpness") or 0.0) * m["metrics"]["n"] for m in scored) / total
        )
        entry["realised_bins_min"] = min(
            (m.get("realised_bins") or 0) for m in scored
        )
        out.append(entry)
    return out
