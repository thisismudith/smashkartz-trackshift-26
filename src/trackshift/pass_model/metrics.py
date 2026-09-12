"""Scoring for the M10 benchmark (Tanveer CP-14, AGENTS.md sections 26 and 55).

Every metric is reported with its ``n``. A Brier score over 63 opportunities and
one over 1,283 are not comparable, and Monaco really does contribute 63
opportunities with a single positive -- so a table that hides ``n`` invites a
conclusion the data cannot support.

**Brier is primary and calibration decides selection.** Section 26 is explicit:
a model with slightly lower ROC-AUC but materially better calibration wins,
because the DP consumes probabilities, not classifications. A ranking metric
cannot see the difference between "20% chance" and "80% chance" as long as the
order is right, and the planner's shadow price depends on exactly that
difference. :func:`rank_results` implements the rule so it is applied
consistently rather than by eye over a table.

**Degenerate folds return ``None``, never a number.** ROC-AUC and PR-AUC are
undefined when a fold has one class. Returning 0.5 or 0.0 there would look like
a measurement and would average into a summary as though it were one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "DEFAULT_BINS",
    "BinStats",
    "evaluate",
    "reliability_table",
    "expected_calibration_error",
    "rank_results",
    "SELECTION_RULE",
]

DEFAULT_BINS = 10

SELECTION_RULE = (
    "Brier ascending, then ECE ascending, then log loss ascending; ROC-AUC breaks "
    "remaining ties only. Section 26: calibration outranks ranking quality because "
    "the DP consumes probabilities."
)


@dataclass(frozen=True)
class BinStats:
    """One reliability bin: what was predicted, what happened, how many."""

    lower: float
    upper: float
    n: int
    mean_predicted: float
    observed_rate: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "lower": self.lower,
            "upper": self.upper,
            "n": self.n,
            "mean_predicted": self.mean_predicted,
            "observed_rate": self.observed_rate,
            "gap": self.observed_rate - self.mean_predicted,
        }


def _as_arrays(y_true: Sequence[Any], p_pred: Sequence[float]):
    import numpy as np

    y = np.asarray(y_true, dtype=float).ravel()
    p = np.asarray(p_pred, dtype=float).ravel()
    if y.shape != p.shape:
        raise ValueError(f"y_true and p_pred differ in length: {y.shape} vs {p.shape}")
    if y.size == 0:
        raise ValueError("cannot score an empty split")
    if not np.isfinite(p).all():
        raise ValueError("p_pred contains non-finite values; a model emitted NaN or inf")
    if p.min() < 0.0 or p.max() > 1.0:
        raise ValueError(f"p_pred must lie in [0, 1]; got [{p.min()}, {p.max()}]")
    return y, p


def reliability_table(
    y_true: Sequence[Any], p_pred: Sequence[float], bins: int = DEFAULT_BINS
) -> list[BinStats]:
    """Equal-count (quantile) reliability bins.

    Equal-count, not equal-width, because predictions on this problem pile up at
    the low end: the base rate is about 15%, so equal-width bins would put most
    of the data in the first bin and score the calibration of the other nine on a
    handful of rows each. CP-15 asks for 10 equal-count bins and this is the same
    function that will serve it.

    Bins are merged where a quantile edge repeats, which happens when a model
    emits one constant probability over many rows.
    """
    import numpy as np

    y, p = _as_arrays(y_true, p_pred)
    if bins < 1:
        raise ValueError(f"bins must be >= 1, got {bins}")
    edges = np.unique(np.quantile(p, np.linspace(0.0, 1.0, bins + 1)))
    if edges.size < 2:  # every prediction identical
        edges = np.array([p[0], np.nextafter(p[0], 1.0) if p[0] < 1.0 else 1.0])

    out: list[BinStats] = []
    index = np.clip(np.searchsorted(edges, p, side="left") - 1, 0, edges.size - 2)
    for slot in range(edges.size - 1):
        mask = index == slot
        count = int(mask.sum())
        if count == 0:
            continue
        out.append(BinStats(
            lower=float(edges[slot]),
            upper=float(edges[slot + 1]),
            n=count,
            mean_predicted=float(p[mask].mean()),
            observed_rate=float(y[mask].mean()),
        ))
    return out


def expected_calibration_error(
    y_true: Sequence[Any], p_pred: Sequence[float], bins: int = DEFAULT_BINS
) -> float:
    """Count-weighted mean absolute gap between predicted and observed rate."""
    table = reliability_table(y_true, p_pred, bins)
    total = sum(b.n for b in table)
    if total == 0:
        return float("nan")
    return sum(b.n * abs(b.observed_rate - b.mean_predicted) for b in table) / total


def evaluate(
    y_true: Sequence[Any],
    p_pred: Sequence[float],
    *,
    bins: int = DEFAULT_BINS,
    base_rate: float | None = None,
) -> dict[str, Any]:
    """Every CP-14 metric for one split, with ``n``.

    ``base_rate`` is the reference predictor's constant probability. Pass the
    **training** base rate: a constant predictor that already knows the test
    base rate is not a baseline anything has to beat, it is a model fitted on the
    test set. Defaults to the split's own rate when not supplied, and the
    manifest records which was used.
    """
    import numpy as np
    from sklearn.metrics import (
        average_precision_score,
        brier_score_loss,
        log_loss,
        roc_auc_score,
    )

    y, p = _as_arrays(y_true, p_pred)
    n = int(y.size)
    positives = int(y.sum())
    observed = float(y.mean())
    single_class = positives in (0, n)

    reference = observed if base_rate is None else float(base_rate)
    constant = np.full(n, reference, dtype=float)
    brier = float(brier_score_loss(y, p))
    brier_reference = float(brier_score_loss(y, constant))

    # log_loss needs the label set stated: a split with one class present would
    # otherwise be scored against a one-column probability matrix and raise.
    labels = [0, 1]
    clipped = np.clip(p, 1e-15, 1 - 1e-15)

    return {
        "n": n,
        "n_positive": positives,
        "observed_rate": observed,
        "reference_rate": reference,
        "brier": brier,
        "brier_reference": brier_reference,
        # Positive means the model beats the constant predictor; 0 means it adds
        # nothing over knowing the base rate.
        "brier_skill_score": (
            float("nan") if brier_reference == 0 else 1.0 - brier / brier_reference
        ),
        "log_loss": float(log_loss(y, clipped, labels=labels)),
        "log_loss_reference": float(log_loss(y, np.clip(constant, 1e-15, 1 - 1e-15), labels=labels)),
        "ece": expected_calibration_error(y, p, bins),
        "roc_auc": None if single_class else float(roc_auc_score(y, p)),
        "pr_auc": None if single_class else float(average_precision_score(y, p)),
        "single_class_split": single_class,
        "reliability": [b.as_dict() for b in reliability_table(y, p, bins)],
        "bins": bins,
    }


def rank_results(results: Sequence[Mapping[str, Any]], key: str = "test") -> list[Mapping[str, Any]]:
    """Order results best-first under :data:`SELECTION_RULE`.

    Entries missing the split, or whose metrics failed, sort last rather than
    raising: a benchmark that cannot report a broken fit is worse than one that
    reports it in last place.
    """
    import math

    def sort_key(entry: Mapping[str, Any]):
        metrics = (entry.get("metrics") or {}).get(key) or {}
        brier = metrics.get("brier")
        if brier is None or (isinstance(brier, float) and math.isnan(brier)):
            return (1, 0.0, 0.0, 0.0, 0.0)
        ece = metrics.get("ece")
        ece = float("inf") if ece is None or math.isnan(ece) else float(ece)
        loss = metrics.get("log_loss")
        loss = float("inf") if loss is None else float(loss)
        auc = metrics.get("roc_auc")
        auc = 0.0 if auc is None else float(auc)
        return (0, float(brier), ece, loss, -auc)

    return sorted(results, key=sort_key)
