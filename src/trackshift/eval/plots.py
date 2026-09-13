"""Figures for the pass-model evaluation (CP-14 analytics).

Every figure answers a question the numbers alone leave open. They are written
to PNG at a fixed size and DPI so a rerun produces a byte-comparable file and a
report can embed them without reflowing.

Matplotlib only, no seaborn, Agg backend: these run in CI and on a headless box.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "FIGURE_DPI",
    "plot_confusion_grid",
    "plot_roc",
    "plot_precision_recall",
    "plot_threshold_sweep",
    "plot_probability_distribution",
    "plot_checkpoint_comparison",
    "plot_feature_importance",
]

FIGURE_DPI = 140

#: Colour-blind safe. Blue is the model, grey is the reference/no-skill line,
#: orange marks the chosen operating point.
MODEL = "#0072B2"
REFERENCE = "#999999"
ACCENT = "#E69F00"
GOOD = "#009E73"
BAD = "#D55E00"


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def _save(fig, path: Path) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=FIGURE_DPI, bbox_inches="tight")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return str(path)


def plot_confusion_grid(matrices: Sequence[Mapping[str, Any]], path: Path,
                        title: str = "") -> str:
    """One matrix per principled threshold, side by side.

    Side by side on purpose: a single matrix hides that the numbers are a
    function of a threshold somebody chose. Seeing four at once makes the
    precision/recall trade the subject rather than a footnote.
    """
    plt = _pyplot()
    n = len(matrices)
    fig, axes = plt.subplots(1, n, figsize=(3.1 * n, 3.5))
    if n == 1:
        axes = [axes]

    for ax, m in zip(axes, matrices):
        grid = [[m["tn"], m["fp"]], [m["fn"], m["tp"]]]
        total = max(1, m["tn"] + m["fp"] + m["fn"] + m["tp"])
        ax.imshow([[c / total for c in row] for row in grid],
                  cmap="Blues", vmin=0, vmax=1)
        for i, row in enumerate(grid):
            for j, value in enumerate(row):
                share = value / total
                ax.text(j, i, f"{value}\n{share:.1%}", ha="center", va="center",
                        fontsize=9, color="white" if share > 0.5 else "black")
        ax.set_xticks([0, 1], ["pred no", "pred pass"], fontsize=8)
        ax.set_yticks([0, 1], ["no pass", "pass"], fontsize=8)
        ax.set_title(f"{m['strategy']}\nthr={m['threshold']:.3f}  "
                     f"P={m['precision']:.2f} R={m['recall']:.2f}",
                     fontsize=9)
    if title:
        fig.suptitle(title, fontsize=11)
    fig.tight_layout()
    return _save(fig, path)


def plot_roc(curve: Mapping[str, Any], path: Path, title: str = "") -> str:
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(4.4, 4.2))
    ax.plot(curve["fpr"], curve["tpr"], color=MODEL, lw=2,
            label=f"model (AUC {curve['auc']:.3f})")
    ax.plot([0, 1], [0, 1], color=REFERENCE, ls="--", lw=1, label="no skill")
    ax.set_xlabel("false positive rate")
    ax.set_ylabel("true positive rate")
    ax.set_title(title or "ROC", fontsize=11)
    ax.legend(fontsize=8, loc="lower right")
    ax.grid(alpha=0.25)
    return _save(fig, path)


def plot_precision_recall(curve: Mapping[str, Any], path: Path, title: str = "") -> str:
    """PR curve against the prevalence line.

    The baseline matters more than the curve: at a 15% base rate, flat 0.15
    precision *is* no skill, and a curve that looks respectable in isolation can
    be sitting on it.
    """
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(4.4, 4.2))
    ax.plot(curve["recall"], curve["precision"], color=MODEL, lw=2,
            label=f"model (AUC {curve['auc']:.3f})")
    ax.axhline(curve["baseline"], color=REFERENCE, ls="--", lw=1,
               label=f"no skill = base rate {curve['baseline']:.3f}")
    ax.set_xlabel("recall")
    ax.set_ylabel("precision")
    ax.set_ylim(0, 1)
    ax.set_title(title or "Precision-recall", fontsize=11)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    return _save(fig, path)


def plot_threshold_sweep(sweep: Sequence[Mapping[str, Any]],
                         thresholds: Mapping[str, float], path: Path,
                         title: str = "") -> str:
    """Precision, recall and F1 across every threshold, with the chosen ones marked."""
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(6.2, 4.0))
    x = [r["threshold"] for r in sweep]
    ax.plot(x, [r["precision"] for r in sweep], color=MODEL, lw=1.8, label="precision")
    ax.plot(x, [r["recall"] for r in sweep], color=GOOD, lw=1.8, label="recall")
    ax.plot(x, [r["f1"] for r in sweep], color=ACCENT, lw=1.8, label="F1")
    for name, value in thresholds.items():
        ax.axvline(value, color=REFERENCE, ls=":", lw=1)
        ax.text(value, 1.02, name, rotation=90, fontsize=7, va="bottom", ha="center")
    ax.set_xlabel("threshold")
    ax.set_ylabel("score")
    ax.set_ylim(0, 1.0)
    ax.set_title(title or "Operating points", fontsize=11)
    ax.legend(fontsize=8, loc="center right")
    ax.grid(alpha=0.25)
    return _save(fig, path)


def plot_probability_distribution(y_true, p_pred, path: Path, title: str = "") -> str:
    """Predicted probability by actual outcome.

    The most direct picture of separation: if the two histograms sit on top of
    each other the model has learned nothing, whatever the AUC says.
    """
    import numpy as np

    plt = _pyplot()
    y = np.asarray(y_true, dtype=int)
    p = np.asarray(p_pred, dtype=float)
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    bins = np.linspace(0, max(0.05, float(p.max())), 30)
    ax.hist(p[y == 0], bins=bins, alpha=0.65, color=REFERENCE, label="no pass", density=True)
    ax.hist(p[y == 1], bins=bins, alpha=0.65, color=MODEL, label="pass", density=True)
    ax.axvline(float(y.mean()), color=ACCENT, ls="--", lw=1.2,
               label=f"base rate {y.mean():.3f}")
    ax.set_xlabel("predicted P(pass)")
    ax.set_ylabel("density")
    ax.set_title(title or "Separation by outcome", fontsize=11)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    return _save(fig, path)


def plot_checkpoint_comparison(rows: Sequence[Mapping[str, Any]], path: Path) -> str:
    """Brier, skill and ROC-AUC per checkpoint.

    ACTIVATION and BRAKING see strictly more than DETECTION, so the bars should
    improve left to right. If they do not, suspect leakage into DETECTION.
    """
    plt = _pyplot()
    fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.4))
    names = [r["checkpoint"] for r in rows]
    panels = [
        ("brier", "Brier (lower better)", MODEL),
        ("brier_skill_score", "Skill vs base rate (higher better)", GOOD),
        ("roc_auc", "ROC-AUC (higher better)", ACCENT),
    ]
    for ax, (key, label, colour) in zip(axes, panels):
        values = [float(r.get(key) or 0.0) for r in rows]
        ax.bar(names, values, color=colour, alpha=0.85)
        for i, v in enumerate(values):
            ax.text(i, v, f"{v:.4f}", ha="center",
                    va="bottom" if v >= 0 else "top", fontsize=8)
        ax.set_title(label, fontsize=9)
        ax.tick_params(axis="x", labelsize=8, rotation=15)
        ax.grid(alpha=0.25, axis="y")
        if key == "brier_skill_score":
            ax.axhline(0, color=BAD, lw=1)
    fig.tight_layout()
    return _save(fig, path)


def plot_feature_importance(names: Sequence[str], values: Sequence[float],
                            path: Path, title: str = "", top: int = 20) -> str:
    """What the model actually leans on.

    Worth reading against the ablation: a feature that dominates importance but
    shows no held-out delta is being used without earning anything, which
    usually means it is correlated with something that does.
    """
    plt = _pyplot()
    pairs = sorted(zip(names, values), key=lambda kv: kv[1], reverse=True)[:top]
    labels = [k for k, _ in pairs][::-1]
    scores = [v for _, v in pairs][::-1]
    fig, ax = plt.subplots(figsize=(6.0, max(3.0, 0.28 * len(labels))))
    ax.barh(labels, scores, color=MODEL, alpha=0.85)
    ax.set_xlabel("importance")
    ax.set_title(title or "Feature importance", fontsize=11)
    ax.tick_params(axis="y", labelsize=8)
    ax.grid(alpha=0.25, axis="x")
    return _save(fig, path)
