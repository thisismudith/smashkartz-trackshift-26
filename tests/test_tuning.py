"""CP-17 pass-model fine-tuning.

The properties that matter are that the test split is never used to select, that
a gain inside fold-to-fold noise is not called a gain, and that chasing a
ranking metric cannot hide a calibration regression (section 26).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.pass_model.candidates import PassModel
from trackshift.pass_model.tuning import (
    OBJECTIVES,
    SEARCH_SPACES,
    TrialResult,
    TuningError,
    baseline_of,
    expand_space,
    rank_trials,
    select_winner,
)


def trial(n, metrics, *, fold="f1", checkpoint="DETECTION", params=None):
    return TrialResult(trial=n, checkpoint=checkpoint, family="lightgbm", fold=fold,
                       params=params or {}, metrics=metrics, n_validation=100)


# --- search space -----------------------------------------------------------

def test_every_family_has_a_search_space():
    from trackshift.pass_model.candidates import FAMILIES

    assert set(SEARCH_SPACES) == set(FAMILIES)


def test_expand_space_returns_the_full_grid_when_it_fits():
    combos = expand_space({"a": [1, 2], "b": [3, 4]})
    assert len(combos) == 4
    assert {"a": 1, "b": 3} in combos


def test_expand_space_sampling_is_seeded_and_reproducible():
    space = {"a": list(range(10)), "b": list(range(10))}
    first = expand_space(space, limit=7, seed=42)
    assert len(first) == 7
    assert first == expand_space(space, limit=7, seed=42)


def test_an_empty_space_yields_no_trials():
    assert expand_space({}) == []


# --- overrides --------------------------------------------------------------

def test_overrides_are_applied_on_top_of_the_cp14_block():
    base = PassModel("lightgbm", numeric=["a"], categorical=[])
    tuned = PassModel("lightgbm", numeric=["a"], categorical=[],
                      overrides={"num_leaves": 63})
    assert tuned.params["num_leaves"] == 63
    # Everything else is untouched, so the measured difference is the knob.
    for key, value in base.params.items():
        if key != "num_leaves":
            assert tuned.params[key] == value


def test_an_unknown_hyperparameter_is_refused_not_ignored():
    """A typo would silently tune nothing and report the untuned model as best."""
    with pytest.raises(ValueError, match="unknown hyperparameter"):
        PassModel("lightgbm", numeric=["a"], categorical=[],
                  overrides={"num_leafs": 63})


# --- ranking ----------------------------------------------------------------

def test_trials_are_aggregated_across_folds_not_ranked_per_fold():
    results = [
        trial(0, {"roc_auc": 0.70, "brier": 0.10}, fold="f1"),
        trial(0, {"roc_auc": 0.72, "brier": 0.11}, fold="f2"),
        trial(1, {"roc_auc": 0.80, "brier": 0.12}, fold="f1"),
        trial(1, {"roc_auc": 0.82, "brier": 0.13}, fold="f2"),
    ]
    ranked = rank_trials(results, objective="roc_auc")
    assert len(ranked) == 2
    assert ranked[0]["trial"] == 1
    assert ranked[0]["objective_mean"] == pytest.approx(0.81)
    assert ranked[0]["folds"] == 2


def test_a_lower_is_better_objective_sorts_the_other_way():
    results = [trial(0, {"brier": 0.20}), trial(1, {"brier": 0.10})]
    assert rank_trials(results, objective="brier")[0]["trial"] == 1


def test_every_metric_is_carried_not_only_the_objective():
    """Section 26's caveat only works if the reader can see what the winner cost."""
    ranked = rank_trials([trial(0, {"roc_auc": 0.7, "brier": 0.1, "ece": 0.05})],
                         objective="roc_auc")
    for metric in OBJECTIVES:
        assert metric in ranked[0]


def test_an_unknown_objective_is_refused():
    with pytest.raises(TuningError, match="unknown objective"):
        rank_trials([trial(0, {"roc_auc": 0.7})], objective="accuracy")


def test_failed_trials_never_enter_the_ranking():
    bad = TrialResult(trial=1, checkpoint="DETECTION", family="lightgbm", fold="f1",
                      params={}, ok=False, error="boom")
    ranked = rank_trials([trial(0, {"roc_auc": 0.7}), bad], objective="roc_auc")
    assert [r["trial"] for r in ranked] == [0]


def test_baseline_is_trial_zero():
    ranked = rank_trials([trial(0, {"roc_auc": 0.7}), trial(1, {"roc_auc": 0.9})],
                         objective="roc_auc")
    assert baseline_of(ranked)["trial"] == 0


# --- selection --------------------------------------------------------------

def ranked_pair(baseline_metrics, best_metrics, *, baseline_std=0.01):
    return [
        {"trial": 1, "params": {"num_leaves": 63},
         "objective_mean": best_metrics["roc_auc"], "objective_std": 0.01,
         **best_metrics},
        {"trial": 0, "params": {},
         "objective_mean": baseline_metrics["roc_auc"], "objective_std": baseline_std,
         **baseline_metrics},
    ]


def test_a_gain_inside_the_baseline_fold_spread_keeps_the_baseline():
    """A search over two dozen configs always produces a leader; that is not
    the same as finding an improvement."""
    ranked = ranked_pair({"roc_auc": 0.720, "brier": 0.13},
                         {"roc_auc": 0.731, "brier": 0.13},
                         baseline_std=0.04)
    decision = select_winner(ranked, objective="roc_auc")
    assert decision["verdict"] == "KEEP_BASELINE"
    assert decision["gain_is_real"] is False
    assert "fold-to-fold spread" in decision["detail"]


def test_a_gain_beyond_the_spread_adopts_the_tuned_configuration():
    ranked = ranked_pair({"roc_auc": 0.70, "brier": 0.13},
                         {"roc_auc": 0.80, "brier": 0.12},
                         baseline_std=0.01)
    decision = select_winner(ranked, objective="roc_auc")
    assert decision["verdict"] == "ADOPT_TUNED"
    assert decision["gain_is_real"] is True


def test_a_calibration_regression_is_flagged_even_when_the_objective_improves():
    """The measured case: ROC-AUC up, Brier down, planner worse off."""
    ranked = ranked_pair({"roc_auc": 0.70, "brier": 0.130},
                         {"roc_auc": 0.80, "brier": 0.142},
                         baseline_std=0.01)
    decision = select_winner(ranked, objective="roc_auc")
    assert decision["calibration_regressed"] is True
    assert decision["calibration_delta"] == pytest.approx(0.012)
    assert "consumes probabilities" in decision["detail"]


def test_no_warning_when_calibration_also_improves():
    ranked = ranked_pair({"roc_auc": 0.70, "brier": 0.14},
                         {"roc_auc": 0.80, "brier": 0.12},
                         baseline_std=0.01)
    decision = select_winner(ranked, objective="roc_auc")
    assert decision["calibration_regressed"] is False
    assert "WARNING" not in decision["detail"]


def test_the_baseline_winning_outright_is_reported_as_such():
    ranked = [
        {"trial": 0, "params": {}, "objective_mean": 0.80, "objective_std": 0.01,
         "roc_auc": 0.80, "brier": 0.10},
        {"trial": 1, "params": {"num_leaves": 63}, "objective_mean": 0.70,
         "objective_std": 0.01, "roc_auc": 0.70, "brier": 0.12},
    ]
    decision = select_winner(ranked, objective="roc_auc")
    assert decision["verdict"] == "KEEP_BASELINE"
    assert "no configuration beat" in decision["detail"]


def test_a_search_with_no_baseline_says_so_rather_than_claiming_a_win():
    ranked = [{"trial": 1, "params": {}, "objective_mean": 0.9, "objective_std": 0.01,
               "roc_auc": 0.9, "brier": 0.1}]
    decision = select_winner(ranked, objective="roc_auc")
    assert decision["status"] == "NO_BASELINE"


def test_no_trials_is_not_a_result():
    assert select_winner([], objective="roc_auc")["status"] == "NO_TRIALS"
