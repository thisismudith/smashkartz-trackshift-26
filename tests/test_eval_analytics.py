"""Evaluation analytics: confusion matrices, thresholds, curves.

The property under test is that the analytics describe a *probabilistic* model
honestly. A confusion matrix needs a threshold the model does not have, and at
this base rate the obvious default is the wrong one — so the thresholds must be
principled and the trade must stay visible.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

np = pytest.importorskip("numpy")

from trackshift.eval.analytics import (  # noqa: E402
    THRESHOLD_STRATEGIES,
    ConfusionMatrix,
    confusion_at,
    curve_points,
    pick_thresholds,
    threshold_sweep,
)


def sample(n=1000, base_rate=0.15, seed=0):
    """Separable-but-noisy scores, roughly like the real model output."""
    rng = np.random.default_rng(seed)
    y = (rng.random(n) < base_rate).astype(int)
    p = np.clip(rng.normal(np.where(y == 1, 0.30, 0.12), 0.08), 0.001, 0.999)
    return y, p


# --- confusion matrix -------------------------------------------------------

def test_cells_are_counted_correctly():
    y = [1, 1, 0, 0]
    p = [0.9, 0.1, 0.8, 0.2]
    cm = confusion_at(y, p, 0.5)
    assert (cm.tp, cm.fn, cm.fp, cm.tn) == (1, 1, 1, 1)
    assert cm.n == 4


def test_the_threshold_is_inclusive():
    cm = confusion_at([1], [0.5], 0.5)
    assert cm.tp == 1, "a score exactly at the threshold counts as predicted positive"


def test_derived_rates_are_consistent():
    cm = ConfusionMatrix(threshold=0.2, strategy="t", tp=30, fp=70, tn=880, fn=20)
    assert cm.precision == pytest.approx(0.3)
    assert cm.recall == pytest.approx(0.6)
    assert cm.specificity == pytest.approx(880 / 950)
    assert cm.f1 == pytest.approx(2 * 0.3 * 0.6 / 0.9)


def test_empty_denominators_do_not_raise():
    cm = ConfusionMatrix(threshold=0.99, strategy="t", tp=0, fp=0, tn=100, fn=10)
    assert cm.precision == 0.0 and cm.f1 == 0.0


def test_every_cell_carries_its_domain_meaning():
    """A matrix nobody can interpret is a matrix nobody will act on."""
    meaning = confusion_at([1, 0], [0.9, 0.1], 0.5).as_dict()["meaning"]
    assert set(meaning) == {"tp", "fp", "fn", "tn"}
    assert "wasted deployment" in meaning["fp"]
    assert "left on the table" in meaning["fn"]


# --- thresholds -------------------------------------------------------------

def test_four_principled_thresholds_each_with_a_rationale():
    y, p = sample()
    picked = pick_thresholds(y, p, base_rate=0.15)
    assert set(picked) == set(THRESHOLD_STRATEGIES)
    for name in picked:
        assert THRESHOLD_STRATEGIES[name].strip(), f"{name} has no stated rationale"


def test_the_base_rate_threshold_is_the_base_rate():
    y, p = sample()
    assert pick_thresholds(y, p, base_rate=0.15)["base_rate"] == pytest.approx(0.15)


def test_max_f1_really_maximises_f1():
    y, p = sample()
    picked = pick_thresholds(y, p, base_rate=0.15)
    best = confusion_at(y, p, picked["max_f1"]).f1
    for row in threshold_sweep(y, p):
        assert confusion_at(y, p, row["threshold"]).f1 <= best + 1e-9


def test_the_naive_threshold_is_visibly_bad_at_this_base_rate():
    """The reason four thresholds are reported instead of one.

    A calibrated model at a 15% base rate rarely exceeds 0.5, so the default cut
    predicts almost nothing and scores well on accuracy by doing nothing.
    """
    y, p = sample()
    naive = confusion_at(y, p, 0.5)
    at_base = confusion_at(y, p, 0.15)
    assert naive.recall < at_base.recall
    assert naive.accuracy > at_base.accuracy      # accuracy rewards the useless cut
    assert naive.accuracy > 0.8


def test_accuracy_is_reported_so_its_uselessness_is_visible():
    """Predicting the majority class for everything scores ~85% here."""
    y, p = sample()
    everything_negative = confusion_at(y, p, 1.01)
    assert everything_negative.tp == 0
    assert everything_negative.accuracy > 0.8
    assert everything_negative.recall == 0.0


# --- sweep and curves -------------------------------------------------------

def test_the_sweep_spans_the_full_range_and_recall_falls_monotonically():
    y, p = sample()
    sweep = threshold_sweep(y, p, steps=21)
    assert len(sweep) == 21
    assert sweep[0]["threshold"] == 0.0 and sweep[-1]["threshold"] == pytest.approx(1.0)
    recalls = [r["recall"] for r in sweep]
    assert all(a >= b - 1e-9 for a, b in zip(recalls, recalls[1:]))


def test_the_pr_curve_carries_the_prevalence_baseline():
    """A PR curve is unreadable without it: at 15% positives, 0.15 IS no skill."""
    y, p = sample(base_rate=0.15)
    curves = curve_points(y, p)
    assert curves["pr"]["baseline"] == pytest.approx(float(np.mean(y)))
    assert 0.0 <= curves["pr"]["auc"] <= 1.0


def test_a_separating_model_beats_chance_on_both_curves():
    y, p = sample()
    curves = curve_points(y, p)
    assert curves["roc"]["auc"] > 0.5
    assert curves["pr"]["auc"] > curves["pr"]["baseline"]


def test_a_random_scorer_sits_near_chance():
    rng = np.random.default_rng(3)
    y = (rng.random(2000) < 0.15).astype(int)
    curves = curve_points(y, rng.random(2000))
    assert curves["roc"]["auc"] == pytest.approx(0.5, abs=0.06)


# --- plots ------------------------------------------------------------------

def test_every_figure_writes_a_readable_png(tmp_path):
    pytest.importorskip("matplotlib")
    from trackshift.eval.plots import (
        plot_checkpoint_comparison,
        plot_confusion_grid,
        plot_feature_importance,
        plot_precision_recall,
        plot_probability_distribution,
        plot_roc,
        plot_threshold_sweep,
    )

    y, p = sample()
    picked = pick_thresholds(y, p, base_rate=0.15)
    matrices = [confusion_at(y, p, v, strategy=k).as_dict() for k, v in picked.items()]
    curves = curve_points(y, p)

    written = [
        plot_confusion_grid(matrices, tmp_path / "cm.png"),
        plot_roc(curves["roc"], tmp_path / "roc.png"),
        plot_precision_recall(curves["pr"], tmp_path / "pr.png"),
        plot_threshold_sweep(threshold_sweep(y, p), picked, tmp_path / "sweep.png"),
        plot_probability_distribution(y, p, tmp_path / "sep.png"),
        plot_checkpoint_comparison(
            [{"checkpoint": "DETECTION", "brier": 0.11, "brier_skill_score": 0.12,
              "roc_auc": 0.74}], tmp_path / "cmp.png"),
        plot_feature_importance(["a", "b"], [3.0, 1.0], tmp_path / "imp.png"),
    ]
    for path in written:
        blob = Path(path)
        assert blob.exists() and blob.stat().st_size > 1000, blob
        assert blob.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{blob} is not a PNG"


def test_the_confusion_grid_renders_every_threshold(tmp_path):
    pytest.importorskip("matplotlib")
    from trackshift.eval.plots import plot_confusion_grid

    y, p = sample()
    matrices = [confusion_at(y, p, v, strategy=k).as_dict()
                for k, v in pick_thresholds(y, p, base_rate=0.15).items()]
    assert len(matrices) == 4
    assert Path(plot_confusion_grid(matrices, tmp_path / "grid.png")).exists()
