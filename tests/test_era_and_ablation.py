"""Section 41 era strategies (deferred) and CP-23 feature-group ablation.

The shared property: neither harness may report a result it did not measure. An
era comparison over one era is unrunnable rather than inconclusive, and an
ablation delta inside the noise floor is not evidence either way.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.eval.ablation import (
    DEFAULT_MINIMAL,
    PRIMARY_METRIC,
    AblationResult,
    build_plans,
    noise_floor,
    summarise_group,
)
from trackshift.pass_model.era import (
    STRATEGIES,
    assess_feasibility,
    domain_weights,
    era_of,
    rank_strategies,
    split_by_era,
)


# --- section 41 era strategies (deferred, not a checkpoint) -----------------

def era_frame(years=("2026",), per_year=4):
    return pd.DataFrame([
        {"year": y, "event": f"E{n % 2}", "session": "Race",
         "decision_checkpoint": "DETECTION", "passed_by_outcome_horizon": bool(n % 2)}
        for y in years for n in range(per_year)
    ])


def test_the_era_is_derived_from_the_year_not_the_regulation_era_column():
    """A 2026-only build hard-codes regulation_era, so trusting it would make
    every historical row look modern."""
    assert era_of("2026") == "modern"
    assert era_of(2026) == "modern"
    for year in ("2022", "2023", "2024", "2025"):
        assert era_of(year) == "historical"
    assert era_of("2019") == "unknown"


def test_a_single_era_table_blocks_every_strategy_but_the_baseline():
    feasibility = {f.strategy: f for f in
                   assess_feasibility(era_frame(("2026",)), ["lightgbm"])}
    assert feasibility["modern_only"].runnable is True
    for name in ("era_feature", "domain_weighting", "separate_models",
                 "recalibration", "historical_pretraining"):
        assert feasibility[name].runnable is False, name
        assert any("2022-2025" in b for b in feasibility[name].blockers)


def test_two_eras_unblock_the_comparison():
    feasibility = {f.strategy: f for f in
                   assess_feasibility(era_frame(("2024", "2026")), ["lightgbm"])}
    for name in ("modern_only", "era_feature", "domain_weighting", "separate_models"):
        assert feasibility[name].runnable is True, name


def test_a_table_with_no_2026_rows_blocks_everything():
    feasibility = assess_feasibility(era_frame(("2024",)), ["lightgbm"])
    assert all(not f.runnable for f in feasibility)
    assert any("2026 rows to test against" in b
               for f in feasibility for b in f.blockers)


def test_domain_weighting_is_refused_for_a_family_that_cannot_express_it():
    """sklearn's MLP takes no sample_weight; omitting it beats running unweighted."""
    feasibility = {f.strategy: f for f in
                   assess_feasibility(era_frame(("2024", "2026")), ["mlp"])}
    assert feasibility["domain_weighting"].runnable is False
    assert "mlp" not in feasibility["domain_weighting"].families


def test_domain_weights_are_applied_to_modern_rows_only():
    frame = era_frame(("2024", "2026"), per_year=2)
    weights = domain_weights(frame, modern_weight=4.0)
    modern = frame["year"].map(era_of) == "modern"
    assert set(weights[modern.values]) == {4.0}
    assert set(weights[~modern.values]) == {1.0}


def test_split_by_era_partitions_without_losing_rows():
    frame = era_frame(("2024", "2026"), per_year=3)
    parts = split_by_era(frame)
    assert len(parts["modern"]) + len(parts["historical"]) + len(parts["unknown"]) == len(frame)


def test_strategies_are_ranked_per_strategy_not_per_fold():
    """A per-fold ranking lists one strategy many times and tops out on the
    easiest fold rather than the best approach."""
    results = [
        {"strategy": "modern_only", "ok": True, "brier": 0.10, "fold": "a",
         "n_train_modern": 100, "n_train_historical": 0, "n_test": 10},
        {"strategy": "modern_only", "ok": True, "brier": 0.20, "fold": "b",
         "n_train_modern": 100, "n_train_historical": 0, "n_test": 10},
        {"strategy": "era_feature", "ok": True, "brier": 0.12, "fold": "a",
         "n_train_modern": 100, "n_train_historical": 400, "n_test": 10},
        {"strategy": "era_feature", "ok": True, "brier": 0.14, "fold": "b",
         "n_train_modern": 100, "n_train_historical": 400, "n_test": 10},
    ]
    ranked = rank_strategies(results)
    assert [r["strategy"] for r in ranked] == ["era_feature", "modern_only"]
    assert len(ranked) == 2                      # one row per strategy, not per fold
    assert ranked[0]["brier"] == pytest.approx(0.13)
    assert ranked[0]["folds"] == 2
    assert ranked[0]["beats_modern_only"] is True
    # N travels with the row: a win bought with four extra seasons is a
    # different claim from a win on equal footing.
    assert ranked[0]["n_train_historical"] == 400


def test_ranking_carries_the_fold_spread_so_an_unstable_winner_cannot_hide():
    results = [
        {"strategy": "s", "ok": True, "brier": b, "fold": str(i),
         "n_train_modern": 1, "n_train_historical": 0, "n_test": 1}
        for i, b in enumerate((0.05, 0.25))
    ]
    assert rank_strategies(results)[0]["brier_std"] > 0.1


def test_failed_cells_never_enter_the_ranking():
    results = [
        {"strategy": "a", "ok": True, "brier": 0.2, "n_test": 1},
        {"strategy": "b", "ok": False, "brier": None, "error": "boom", "n_test": 0},
    ]
    assert [r["strategy"] for r in rank_strategies(results)] == ["a"]


def test_every_documented_strategy_is_declared():
    assert {s.name for s in STRATEGIES} == {
        "modern_only", "era_feature", "domain_weighting",
        "separate_models", "recalibration", "historical_pretraining"}


# --- CP-23 ------------------------------------------------------------------

COLUMNS = ("gap_at_checkpoint", "tyre_a", "tyre_b", "weather_a")
GROUPS = {"tyre": ("tyre_a", "tyre_b"), "weather": ("weather_a",)}


def test_plans_cover_baseline_minimal_leave_one_out_and_add_one_in():
    plans = build_plans(GROUPS, all_columns=COLUMNS)
    modes = [(p["mode"], p["group"]) for p in plans]
    assert ("baseline", None) in modes
    assert ("minimal", None) in modes
    assert ("leave_one_out", "tyre") in modes
    assert ("add_one_in", "tyre") in modes


def test_the_minimal_floor_is_not_empty():
    """A floor of nothing is a zero-feature model, which cannot be fitted, and
    every add-one-in delta then comes back unmeasurable."""
    assert DEFAULT_MINIMAL
    plans = build_plans(GROUPS, all_columns=COLUMNS)
    minimal = next(p for p in plans if p["mode"] == "minimal")
    kept = [c for c in COLUMNS if c not in set(minimal["drop"])]
    assert kept == list(DEFAULT_MINIMAL)


def test_add_one_in_keeps_the_floor_plus_its_group():
    plans = build_plans(GROUPS, all_columns=COLUMNS)
    tyre = next(p for p in plans if p["mode"] == "add_one_in" and p["group"] == "tyre")
    kept = {c for c in COLUMNS if c not in set(tyre["drop"])}
    assert kept == {"gap_at_checkpoint", "tyre_a", "tyre_b"}


def test_a_group_covering_every_column_produces_no_leave_one_out_plan():
    plans = build_plans({"everything": COLUMNS}, all_columns=COLUMNS)
    assert not [p for p in plans if p["mode"] == "leave_one_out"]


def result(mode, group, metric, *, seed=42, fold="f1", checkpoint="DETECTION"):
    return AblationResult(checkpoint=checkpoint, family="lightgbm", fold=fold,
                          seed=seed, mode=mode, group=group, metric=metric)


def test_a_delta_inside_the_noise_floor_is_not_called_a_keep():
    results = [
        result("baseline", None, 0.100, seed=42),
        result("baseline", None, 0.120, seed=43),      # 0.02 seed-to-seed spread
        result("leave_one_out", "tyre", 0.105, seed=42),
        result("leave_one_out", "tyre", 0.125, seed=43),
    ]
    verdict = summarise_group(results, checkpoint="DETECTION", group="tyre", n_features=2)
    assert verdict.noise_floor == pytest.approx(0.02)
    assert verdict.verdict in ("UNINFORMATIVE", "REDUNDANT")


def test_a_group_whose_removal_clearly_hurts_is_kept():
    results = []
    for seed in (42, 43, 44):
        for fold in ("f1", "f2"):
            results.append(result("baseline", None, 0.100, seed=seed, fold=fold))
            results.append(result("leave_one_out", "tyre", 0.150, seed=seed, fold=fold))
    verdict = summarise_group(results, checkpoint="DETECTION", group="tyre", n_features=2)
    assert verdict.leave_one_out_delta == pytest.approx(0.05)
    assert verdict.verdict == "KEEP"


def test_a_group_whose_removal_improves_the_metric_is_dropped():
    results = []
    for seed in (42, 43, 44):
        results.append(result("baseline", None, 0.150, seed=seed))
        results.append(result("leave_one_out", "noise", 0.100, seed=seed))
    verdict = summarise_group(results, checkpoint="DETECTION", group="noise", n_features=1)
    assert verdict.verdict == "DROP"


def test_redundant_is_distinguished_from_uninformative():
    """Both show a flat leave-one-out delta and call for different decisions."""
    results = []
    for seed in (42, 43, 44):
        results.append(result("baseline", None, 0.100, seed=seed))
        results.append(result("leave_one_out", "tyre", 0.100, seed=seed))
        results.append(result("minimal", None, 0.200, seed=seed))
        results.append(result("add_one_in", "tyre", 0.120, seed=seed))
    verdict = summarise_group(results, checkpoint="DETECTION", group="tyre", n_features=2)
    assert verdict.add_one_in_delta == pytest.approx(0.08)
    assert verdict.verdict == "REDUNDANT"


def test_an_identity_group_that_is_kept_carries_the_section_17_warning():
    results = []
    for seed in (42, 43, 44):
        results.append(result("baseline", None, 0.100, seed=seed))
        results.append(result("leave_one_out", "identity", 0.200, seed=seed))
    verdict = summarise_group(results, checkpoint="DETECTION", group="identity",
                              n_features=2)
    assert verdict.verdict == "KEEP"
    assert verdict.is_identity is True
    assert "memoris" in verdict.rationale


def test_a_group_with_no_completed_fits_is_unmeasured_not_zero():
    verdict = summarise_group([result("baseline", None, 0.1)],
                              checkpoint="DETECTION", group="absent", n_features=1)
    assert verdict.verdict == "UNMEASURED"
    assert verdict.leave_one_out_delta is None


def test_the_noise_floor_measures_seeds_not_folds():
    """Fold-to-fold spread is the circuits genuinely differing, not noise."""
    results = [
        result("baseline", None, 0.10, seed=42, fold="f1"),
        result("baseline", None, 0.11, seed=43, fold="f1"),
        result("baseline", None, 0.50, seed=42, fold="f2"),
        result("baseline", None, 0.51, seed=43, fold="f2"),
    ]
    # Seed spread is 0.01 in each fold; the 0.4 between folds must not leak in.
    assert noise_floor(results, "DETECTION") == pytest.approx(0.01)


def test_the_primary_metric_is_the_calibration_one():
    assert PRIMARY_METRIC == "brier"
