"""Evaluation analytics for the pass model: curves, matrices, thresholds.

This model predicts a probability and is selected on calibration (§26), so the
analytics have to be built for that and not for a classifier. Three consequences
shape everything here:

**A confusion matrix needs a threshold, and the threshold is a decision.** The
model does not have one. Reporting a single matrix at 0.5 would be reporting a
choice nobody made — and at a ~15% base rate it is a bad choice, since a
calibrated model rarely exceeds 0.5 and the matrix collapses to "predict no
pass, always". So matrices are reported at four *principled* thresholds and the
sweep behind them is plotted, making the trade visible instead of hidden.

**Accuracy is meaningless at this prevalence.** Predicting "no pass" for
everything scores ~85%. It is computed and displayed only so the number is
visibly useless next to precision and recall, which is more honest than omitting
it and letting someone compute it themselves later.

**PR-AUC matters more than ROC-AUC.** With ~15% positives, ROC-AUC is flattered
by the large negative class; the precision-recall curve against the base-rate
line is the one that shows whether the model is useful for finding passes.

Predictions must be **out-of-fold**. Scoring the saved artifact on rows it was
fitted on produces a beautiful, meaningless picture; :func:`collect_oof` refits
per fold so every prediction comes from a model that did not see that row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "ANALYTICS_SCHEMA_VERSION",
    "THRESHOLD_STRATEGIES",
    "ConfusionMatrix",
    "confusion_at",
    "pick_thresholds",
    "threshold_sweep",
    "curve_points",
]

ANALYTICS_SCHEMA_VERSION = "m10_eval_analytics_v1"

#: How each reported threshold is chosen, and why it is worth reporting.
THRESHOLD_STRATEGIES: dict[str, str] = {
    "naive_0.5": (
        "The textbook default. Included to show why it is wrong here: a "
        "calibrated model at a 15% base rate rarely exceeds 0.5, so this "
        "predicts almost no passes and looks accurate by doing nothing."
    ),
    "base_rate": (
        "Threshold at the training base rate. For a calibrated model this is the "
        "natural operating point: flag an opportunity when it is likelier than a "
        "randomly chosen one."
    ),
    "max_f1": (
        "Maximises F1 on this data. The best single-number balance of precision "
        "and recall, and the one to quote if a downstream consumer needs a hard "
        "yes/no."
    ),
    "max_youden": (
        "Maximises sensitivity + specificity - 1. Prevalence-independent, so it "
        "is the fair comparison across events with different pass rates."
    ),
}


@dataclass
class ConfusionMatrix:
    """A confusion matrix with the domain meaning of each cell spelled out."""

    threshold: float
    strategy: str
    tp: int
    fp: int
    tn: int
    fn: int

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.tn + self.fn

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def specificity(self) -> float:
        return self.tn / (self.tn + self.fp) if (self.tn + self.fp) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def accuracy(self) -> float:
        """Present so its uselessness is visible, not so it is used."""
        return (self.tp + self.tn) / self.n if self.n else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": round(self.threshold, 6),
            "strategy": self.strategy,
            "tp": self.tp, "fp": self.fp, "tn": self.tn, "fn": self.fn,
            "precision": round(self.precision, 6),
            "recall": round(self.recall, 6),
            "specificity": round(self.specificity, 6),
            "f1": round(self.f1, 6),
            "accuracy": round(self.accuracy, 6),
            "predicted_positive_rate": round(
                (self.tp + self.fp) / self.n, 6) if self.n else 0.0,
            "meaning": {
                "tp": "flagged an overtake chance and the pass happened",
                "fp": "flagged a chance that did not convert -- a wasted deployment",
                "fn": "missed a pass that did happen -- an opportunity left on the table",
                "tn": "correctly saw no chance",
            },
        }


def confusion_at(y_true, p_pred, threshold: float, strategy: str = "custom") -> ConfusionMatrix:
    import numpy as np

    y = np.asarray(y_true, dtype=int)
    p = np.asarray(p_pred, dtype=float)
    predicted = p >= threshold
    return ConfusionMatrix(
        threshold=float(threshold), strategy=strategy,
        tp=int(((predicted == 1) & (y == 1)).sum()),
        fp=int(((predicted == 1) & (y == 0)).sum()),
        tn=int(((predicted == 0) & (y == 0)).sum()),
        fn=int(((predicted == 0) & (y == 1)).sum()),
    )


def threshold_sweep(y_true, p_pred, steps: int = 101) -> list[dict[str, Any]]:
    """Precision, recall, F1 and Youden's J across the threshold range."""
    import numpy as np

    out = []
    for threshold in np.linspace(0.0, 1.0, steps):
        cm = confusion_at(y_true, p_pred, float(threshold))
        out.append({
            "threshold": round(float(threshold), 4),
            "precision": cm.precision, "recall": cm.recall, "f1": cm.f1,
            "youden": cm.recall + cm.specificity - 1.0,
            "predicted_positive_rate": (cm.tp + cm.fp) / cm.n if cm.n else 0.0,
        })
    return out


def pick_thresholds(y_true, p_pred, base_rate: float | None = None) -> dict[str, float]:
    """The four principled operating points. See THRESHOLD_STRATEGIES."""
    import numpy as np

    sweep = threshold_sweep(y_true, p_pred)
    rate = float(base_rate) if base_rate is not None else float(np.mean(y_true))
    best_f1 = max(sweep, key=lambda r: r["f1"])
    best_j = max(sweep, key=lambda r: r["youden"])
    return {
        "naive_0.5": 0.5,
        "base_rate": rate,
        "max_f1": best_f1["threshold"],
        "max_youden": best_j["threshold"],
    }


def curve_points(y_true, p_pred) -> dict[str, Any]:
    """ROC and precision-recall curves, plus the base-rate reference line."""
    import numpy as np
    from sklearn.metrics import (
        auc,
        precision_recall_curve,
        roc_curve,
    )

    y = np.asarray(y_true, dtype=int)
    p = np.asarray(p_pred, dtype=float)
    fpr, tpr, _ = roc_curve(y, p)
    precision, recall, _ = precision_recall_curve(y, p)
    return {
        "roc": {"fpr": fpr.tolist(), "tpr": tpr.tolist(), "auc": float(auc(fpr, tpr))},
        "pr": {
            "precision": precision.tolist(), "recall": recall.tolist(),
            "auc": float(auc(recall, precision)),
            # A PR curve is only readable against the prevalence line: at 15%
            # positives a flat 0.15 precision IS the no-skill baseline.
            "baseline": float(y.mean()),
        },
    }
