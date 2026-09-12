"""CP-14 pass-model benchmark (M10).

Two tests here carry the checkpoint and would justify the file on their own.

``test_detection_matrix_excludes_every_activation_and_braking_feature`` is the
CP-14 half of CP-13's leakage guarantee. CP-13 proved that a DETECTION *row*
never populates an activation-time column; this proves the DETECTION *model*
never selects one. A model that knows the activation speed at the Detection Line
scores beautifully in validation and is worthless at the decision point, and
nothing downstream would catch it -- the probabilities would simply be confident
and wrong.

``test_demo_event_never_reaches_a_training_or_validation_split`` is section 40.
The 2026 British Grand Prix is the demo event and the frozen final test. Training
on it does not crash anything; it just silently voids every held-out claim about
the one event the demo is built on.

The fixtures are synthetic but run against the **real** feature registry, because
a leakage test against a mock registry only proves the mock is consistent.
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

from trackshift.data.guards import DemoEventLeak  # noqa: E402
from trackshift.data.registry import load_feature_registry  # noqa: E402
from trackshift.features.opportunities import (  # noqa: E402
    AUDIT_ONLY_COLUMNS,
    CHECKPOINTS,
)
from trackshift.pass_model.api import (  # noqa: E402
    FAMILIES,
    FeatureSelectionError,
    PassModel,
    STRUCTURAL_COLUMNS,
    SplitPlanError,
    aggregate,
    assert_disjoint,
    available_families,
    build_matrix,
    check_gates,
    configure_threads,
    evaluate,
    expected_calibration_error,
    fit_cell,
    load_fit_artifact,
    plan_hardware,
    plan_splits,
    rank_results,
    reliability_table,
    resolve_unit,
    select_features,
    summarise,
    write_fit_artifact,
)

ACTIVATION_SCOPED = ("gap_at_activation_s", "speed_at_activation_kmh",
                     "distance_activation_to_brake")
BRAKING_SCOPED = ("speed_at_braking_kmh",)

EVENTS = ("australian_grand_prix", "canadian_grand_prix", "italian_grand_prix",
          "japanese_grand_prix", "miami_grand_prix", "British Grand Prix")


def make_frame(*, events=EVENTS, per_event: int = 40, year: str = "2026", seed: int = 7):
    """A synthetic opportunity table in the real CP-13 schema.

    The label is a noisy function of the gap, so a model has real signal to find
    and the benchmark tests exercise a fit that converges rather than one that
    degenerates.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for event in events:
        for index in range(per_event):
            gap = float(rng.uniform(0.1, 2.0))
            probability = 1.0 / (1.0 + np.exp(3.0 * (gap - 0.9)))
            label = bool(rng.random() < probability)
            opportunity = f"{year}|{event}|Race|{index}"
            for checkpoint in CHECKPOINTS:
                row = {
                    "opportunity_id": opportunity,
                    "year": year,
                    "event": event,
                    "session": "Race",
                    "lap": 10 + index % 30,
                    "zone": 1,
                    "battle_id": None,
                    "attacker": f"D{index % 5}",
                    "defender": f"D{(index + 1) % 5}",
                    "attacker_team": f"T{index % 3}",
                    "defender_team": None,
                    "regulation_era": "2026",
                    "decision_checkpoint": checkpoint,
                    "feature_cutoff_distance_m": 5000.0,
                    "feature_cutoff_offset_m": {"DETECTION": 0.0, "ACTIVATION": 400.0,
                                                "BRAKING": 700.0}[checkpoint],
                    "crosses_lap_boundary": True,
                    "outcome_horizon": "zone_exit_v1",
                    "label_definition": "zone_exit_v1",
                    "schema_version": "m07_overtake_opportunities_v1",
                    "gap_at_checkpoint": gap,
                    "closing_rate_s_per_s": float(rng.normal(0.02, 0.05)),
                    "distance_detection_to_activation": 400.0,
                    "distance_remaining_in_zone": 700.0,
                    "p_eligible": float(np.clip(probability + rng.normal(0, 0.05), 0, 1)),
                    "projected_gap_at_detection_s": gap,
                    "projected_gap_sigma_s": 0.1,
                    "eligibility_margin": 1.0 - gap,
                    "passed_by_outcome_horizon": label,
                    "pass_attempted": None,
                    "outcome_distance_m": 5700.0,
                    "gap_at_activation_s": None,
                    "speed_at_activation_kmh": None,
                    "distance_activation_to_brake": None,
                    "speed_at_braking_kmh": None,
                }
                if checkpoint in ("ACTIVATION", "BRAKING"):
                    row["gap_at_activation_s"] = gap * 0.9
                    row["speed_at_activation_kmh"] = float(rng.uniform(240, 330))
                    row["distance_activation_to_brake"] = 300.0
                if checkpoint == "BRAKING":
                    row["speed_at_braking_kmh"] = float(rng.uniform(250, 340))
                rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def frame():
    return make_frame()


# --------------------------------------------------------------------------
# Feature selection: the leakage gate
# --------------------------------------------------------------------------

def test_detection_matrix_excludes_every_activation_and_braking_feature(frame):
    """The CP-14 half of the CP-13 leakage guarantee. Non-negotiable."""
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    for column in ACTIVATION_SCOPED + BRAKING_SCOPED:
        assert column not in selection.columns, (
            f"{column} reached the DETECTION feature matrix; it is not knowable there"
        )
        assert "not knowable at DETECTION" in selection.excluded[column]


def test_activation_sees_activation_columns_but_not_braking(frame):
    rows = frame[frame["decision_checkpoint"] == "ACTIVATION"]
    selection = select_features("ACTIVATION", rows.columns, dtypes=rows.dtypes)
    for column in ACTIVATION_SCOPED:
        assert column in selection.columns
    for column in BRAKING_SCOPED:
        assert column not in selection.columns


def test_feature_count_increases_with_the_checkpoint(frame):
    """Later checkpoints see strictly more. If they do not, the scoping is wrong."""
    counts = []
    for checkpoint in CHECKPOINTS:
        rows = frame[frame["decision_checkpoint"] == checkpoint]
        counts.append(len(select_features(checkpoint, rows.columns, dtypes=rows.dtypes).columns))
    assert counts[0] < counts[1] < counts[2], counts


def test_audit_columns_and_label_are_never_features(frame):
    for checkpoint in CHECKPOINTS:
        rows = frame[frame["decision_checkpoint"] == checkpoint]
        selection = select_features(checkpoint, rows.columns, dtypes=rows.dtypes)
        for column in AUDIT_ONLY_COLUMNS:
            assert column not in selection.columns
            assert "audit only" in selection.excluded[column]
        assert "passed_by_outcome_horizon" not in selection.columns


def test_identifier_columns_are_refused(frame):
    """A model that trains on event or lap memorises races, it does not learn racecraft."""
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    for column in ("year", "event", "session", "lap", "battle_id"):
        assert column not in selection.columns
        assert "identifier" in selection.excluded[column]


def test_identity_is_behind_the_flag(frame):
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    without = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    with_identity = select_features("DETECTION", rows.columns, include_identity=True,
                                    dtypes=rows.dtypes)
    assert "attacker" not in without.columns
    assert "attacker" in with_identity.columns
    assert set(without.columns) < set(with_identity.columns)


def test_unregistered_columns_are_refused_not_guessed(frame):
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    # attacker_team exists in the table but is not registered. Silently including
    # it would put an unprovenanced column into a model artifact.
    assert "attacker_team" not in selection.columns
    assert "feature_registry" in selection.excluded["attacker_team"]


def test_structural_columns_are_refused_even_when_registered(frame):
    """A structural column must not become a feature because someone registered the name.

    This is not hypothetical. ``schema_version`` was an unregistered structural
    column in CP-13's output, and was later registered upstream as an M08
    rival-state feature. That entry happens to declare ``live_safe: false`` so it
    is refused anyway -- but had it been CAUSAL and live-safe, it would have
    entered every feature matrix silently. The registry is a shared namespace;
    a name collision with a structural column must not be able to leak one in.
    """
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    registry = dict(load_feature_registry())
    # Register every structural column as attractively as possible.
    for name in STRUCTURAL_COLUMNS:
        registry[name] = {
            "decision_checkpoint": None,
            "causal_status": "CAUSAL",
            "live_safe": True,
            "interaction_group": [],
            "unit": None,
        }
    selection = select_features("DETECTION", rows.columns, registry=registry, dtypes=rows.dtypes)
    leaked = [c for c in selection.columns if c in STRUCTURAL_COLUMNS]
    assert not leaked, f"structural column(s) reached the feature matrix: {leaked}"
    for name in STRUCTURAL_COLUMNS & set(rows.columns):
        assert "structural column" in selection.excluded[name]


def test_unknown_checkpoint_raises(frame):
    with pytest.raises(FeatureSelectionError):
        select_features("APEX", frame.columns)


def test_feature_schema_is_stable_across_calls(frame):
    rows = frame[frame["decision_checkpoint"] == "BRAKING"]
    first = select_features("BRAKING", rows.columns, dtypes=rows.dtypes).as_schema()
    second = select_features("BRAKING", rows.columns, dtypes=rows.dtypes).as_schema()
    assert first == second


# --------------------------------------------------------------------------
# Matrix construction
# --------------------------------------------------------------------------

def test_build_matrix_drops_unlabelled_rows(frame):
    rows = frame[frame["decision_checkpoint"] == "DETECTION"].copy()
    # The real table carries nulls, so its label column is object dtype. The
    # fixture has none, so pandas infers bool and refuses a null; cast first.
    rows["passed_by_outcome_horizon"] = rows["passed_by_outcome_horizon"].astype(object)
    rows.loc[rows.index[0], "passed_by_outcome_horizon"] = None
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    X, y = build_matrix(rows, selection)
    assert len(X) == len(rows) - 1 == len(y)


def test_build_matrix_casts_numeric_to_float32(frame):
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    X, _ = build_matrix(rows, selection)
    for column in selection.numeric:
        assert X[column].dtype == np.float32


def test_build_matrix_replaces_missing_categoricals_with_a_sentinel(frame):
    rows = frame[frame["decision_checkpoint"] == "DETECTION"].copy()
    rows.loc[rows.index[0], "attacker"] = None
    selection = select_features("DETECTION", rows.columns, include_identity=True,
                                dtypes=rows.dtypes)
    X, _ = build_matrix(rows, selection)
    assert X["attacker"].isna().sum() == 0
    assert "__missing__" in set(X["attacker"])


def test_build_matrix_refuses_a_frame_missing_a_selected_column(frame):
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    with pytest.raises(FeatureSelectionError, match="missing selected column"):
        build_matrix(rows.drop(columns=["gap_at_checkpoint"]), selection)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

def test_perfect_predictions_score_zero_brier():
    y = [0, 1, 0, 1]
    assert evaluate(y, [0.0, 1.0, 0.0, 1.0])["brier"] == pytest.approx(0.0)


def test_reliability_bins_account_for_every_row():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 500)
    y = (rng.uniform(0, 1, 500) < p).astype(int)
    assert sum(b.n for b in reliability_table(y, p, bins=10)) == 500


def test_calibrated_predictions_have_small_ece():
    rng = np.random.default_rng(1)
    p = rng.uniform(0.05, 0.95, 20_000)
    y = (rng.uniform(0, 1, 20_000) < p).astype(int)
    assert expected_calibration_error(y, p, bins=10) < 0.05


def test_systematically_overconfident_predictions_have_large_ece():
    rng = np.random.default_rng(2)
    truth = rng.uniform(0.05, 0.95, 5_000)
    y = (rng.uniform(0, 1, 5_000) < truth).astype(int)
    overconfident = np.clip(truth * 2.0, 0, 1)
    assert expected_calibration_error(y, overconfident, bins=10) > 0.1


def test_single_class_split_returns_none_not_a_fake_auc():
    """A fold with one class has no defined AUC. 0.5 would look like a measurement."""
    result = evaluate([0, 0, 0, 0], [0.1, 0.2, 0.3, 0.4])
    assert result["roc_auc"] is None
    assert result["pr_auc"] is None
    assert result["single_class_split"] is True
    assert result["brier"] == pytest.approx(0.075)


def test_probabilities_outside_the_unit_interval_are_refused():
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        evaluate([0, 1], [0.5, 1.5])


def test_nan_predictions_are_refused():
    with pytest.raises(ValueError, match="non-finite"):
        evaluate([0, 1], [0.5, float("nan")])


def test_reference_rate_defaults_to_the_split_but_can_be_supplied():
    result = evaluate([0, 1, 0, 0], [0.2] * 4, base_rate=0.15)
    assert result["reference_rate"] == pytest.approx(0.15)
    assert result["observed_rate"] == pytest.approx(0.25)


def test_selection_prefers_calibration_over_ranking():
    """Section 26 in one assertion: better Brier wins despite a worse ROC-AUC."""
    calibrated = {"family": "calibrated",
                  "metrics": {"test": {"brier": 0.10, "ece": 0.02, "log_loss": 0.3,
                                       "roc_auc": 0.71}}}
    sharp = {"family": "sharp",
             "metrics": {"test": {"brier": 0.14, "ece": 0.09, "log_loss": 0.4,
                                  "roc_auc": 0.79}}}
    assert [r["family"] for r in rank_results([sharp, calibrated])] == ["calibrated", "sharp"]


def test_broken_results_sort_last_rather_than_raising():
    good = {"family": "good", "metrics": {"test": {"brier": 0.2, "ece": 0.1,
                                                   "log_loss": 0.5, "roc_auc": 0.6}}}
    broken = {"family": "broken", "metrics": {}}
    assert [r["family"] for r in rank_results([broken, good])] == ["good", "broken"]


# --------------------------------------------------------------------------
# Splits
# --------------------------------------------------------------------------

def test_demo_event_never_reaches_a_training_or_validation_split(frame):
    """Section 40. The frozen demo event stays frozen."""
    plan = plan_splits(frame, seed=42)
    for fold in plan.folds:
        for role in (fold.train, fold.validation, fold.test):
            events = set(frame.loc[role, "event"])
            assert "British Grand Prix" not in events
    assert len(plan.holdout) == 3 * 40  # three checkpoints x the BGP opportunities


def test_the_guard_fires_when_the_demo_event_is_forced_into_training(frame):
    """Deliberately break the holdout and confirm the guard is what stops it."""
    import trackshift.pass_model.folds as folds_module

    smuggled = frame.copy()
    with pytest.MonkeyPatch.context() as patch:
        # Convince the planner that no row is the demo event, so nothing is held
        # out and British GP rows land in the training split.
        patch.setattr(folds_module, "_demo_mask",
                      lambda f, scope=None: np.zeros(len(f), dtype=bool))
        with pytest.raises(DemoEventLeak):
            plan_splits(smuggled, seed=42)


def test_leave_one_event_out_gives_one_fold_per_non_demo_event(frame):
    plan = plan_splits(frame, design="leave_one_event_out", seed=42)
    assert plan.design == "leave_one_event_out"
    assert len(plan.folds) == len(EVENTS) - 1
    assert {f.name for f in plan.folds} == set(EVENTS) - {"British Grand Prix"}


def test_every_fold_is_disjoint(frame):
    assert_disjoint(plan_splits(frame, seed=42))


def test_assert_disjoint_catches_an_overlap(frame):
    plan = plan_splits(frame, seed=42)
    broken = type(plan)(
        design=plan.design, unit=plan.unit, seed=plan.seed,
        folds=(type(plan.folds[0])("bad", frame.index[:10], frame.index[:10],
                                   frame.index[20:30]),),
        holdout=plan.holdout, manifest=plan.manifest,
    )
    with pytest.raises(SplitPlanError, match="appear in both"):
        assert_disjoint(broken)


def test_split_unit_falls_back_to_event_when_battle_id_is_null(frame):
    unit, reason = resolve_unit(frame)
    assert unit == "event"
    assert "battle_id populated on 0" in reason


def test_split_unit_uses_battle_id_when_it_is_populated(frame):
    filled = frame.copy()
    filled["battle_id"] = filled["opportunity_id"]
    unit, _ = resolve_unit(filled)
    assert unit == "battle_id"


def test_kfold_refuses_to_run_without_battle_id(frame):
    with pytest.raises(SplitPlanError, match="battle_id"):
        plan_splits(frame, design="kfold", seed=42)


def multi_year_frame():
    return pd.concat([
        make_frame(events=("australian_grand_prix", "canadian_grand_prix",
                           "italian_grand_prix", "British Grand Prix"),
                   per_event=20, year=year, seed=index)
        for index, year in enumerate(("2022", "2023", "2024", "2025", "2026"))
    ], ignore_index=True)


def test_auto_design_picks_the_year_table_once_the_historical_years_exist():
    """CP-14's documented split is selected the moment 2022-2025 opportunities exist."""
    multi = multi_year_frame()
    plan = plan_splits(multi, seed=42)
    fold = plan.folds[0]
    assert plan.design == "year_table"
    assert set(multi.loc[fold.train, "year"]) == {"2022", "2023", "2024"}
    assert set(multi.loc[fold.validation, "year"]) == {"2025"}
    assert set(multi.loc[fold.test, "year"]) == {"2026"}
    assert "British Grand Prix" not in set(multi.loc[fold.test, "event"])


def test_track_scope_holds_out_silverstone_in_every_season():
    """The default. It is the only scope where this module and C9 agree."""
    multi = multi_year_frame()
    fold = plan_splits(multi, seed=42).folds[0]
    for role in (fold.train, fold.validation, fold.test):
        assert "British Grand Prix" not in set(multi.loc[role, "event"])


def test_event_year_scope_leaves_historical_silverstone_trainable():
    """CP-14's split table literally says 'train 2022-2024, all events'."""
    from trackshift.data.guards import DemoScope

    multi = multi_year_frame()
    plan = plan_splits(multi, seed=42, demo_scope=DemoScope.EVENT_YEAR)
    fold = plan.folds[0]
    train = multi.loc[fold.train]
    assert "British Grand Prix" in set(train["event"])
    assert set(train.loc[train["event"] == "British Grand Prix", "year"]) == {
        "2022", "2023", "2024"
    }
    # Only 2026 Silverstone is frozen, and the plan says C9's stricter assertion
    # was skipped on purpose rather than leaving the two guards disagreeing.
    assert set(multi.loc[plan.holdout, "year"]) == {"2026"}
    assert any("deliberately NOT applied" in note for note in plan.notes)


def test_year_table_refuses_to_run_without_a_validation_year():
    """2025 is where early stopping and CP-15's calibrator fit. Without it, stop."""
    no_2025 = pd.concat([
        make_frame(events=("australian_grand_prix", "canadian_grand_prix",
                           "italian_grand_prix", "British Grand Prix"),
                   per_event=20, year=year, seed=index)
        for index, year in enumerate(("2022", "2023", "2024", "2026"))
    ], ignore_index=True)
    with pytest.raises(SplitPlanError, match="2025"):
        plan_splits(no_2025, design="year_table", seed=42)


def test_year_table_refuses_to_run_on_a_single_season(frame):
    with pytest.raises(SplitPlanError, match=r"\['2022', '2023', '2024'\]"):
        plan_splits(frame, design="year_table", seed=42)


def test_split_summary_counts_labels_per_role(frame):
    plan = plan_splits(frame, seed=42)
    for row in summarise(frame, plan):
        assert row["train_labelled"] <= row["train_rows"]
        assert row["test_positive"] <= row["test_labelled"]
        assert row["test_events"] == 1


def test_a_single_season_with_two_events_cannot_be_split(frame):
    tiny = frame[frame["event"].isin(["australian_grand_prix", "British Grand Prix"])]
    with pytest.raises(SplitPlanError, match="at least 3"):
        plan_splits(tiny, seed=42)


# --------------------------------------------------------------------------
# Model families
# --------------------------------------------------------------------------

@pytest.mark.parametrize("family", FAMILIES)
def test_every_available_family_fits_and_emits_probabilities(frame, family):
    if available_families([family])[family] is not None:
        pytest.skip(f"{family} is not installed")
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    X, y = build_matrix(rows, selection)
    model = PassModel(family, numeric=selection.numeric, categorical=selection.categorical,
                      seed=42, threads=1)
    model.fit(X[:150], y[:150], X[150:], y[150:])
    p = model.predict_proba(X[150:])
    assert p.shape == (len(X) - 150,)
    assert float(p.min()) >= 0.0 and float(p.max()) <= 1.0


@pytest.mark.parametrize("family", FAMILIES)
def test_the_same_seed_reproduces_the_same_predictions(frame, family):
    """MODELS.md section 6.3: both owners build locally and compare."""
    if available_families([family])[family] is not None:
        pytest.skip(f"{family} is not installed")
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    X, y = build_matrix(rows, selection)
    runs = []
    for _ in range(2):
        model = PassModel(family, numeric=selection.numeric,
                          categorical=selection.categorical, seed=42, threads=1)
        model.fit(X[:150], y[:150], X[150:], y[150:])
        runs.append(model.predict_proba(X[150:]))
    np.testing.assert_allclose(runs[0], runs[1], rtol=0, atol=0)


def test_unknown_family_is_refused():
    with pytest.raises(ValueError, match="unknown family"):
        PassModel("randomforest", numeric=["a"], categorical=[])


def test_predict_before_fit_raises():
    with pytest.raises(RuntimeError, match="not fitted"):
        PassModel("logistic", numeric=["a"], categorical=[]).predict_proba(
            pd.DataFrame({"a": [1.0]})
        )


def test_a_level_unseen_in_training_becomes_missing_not_a_new_code(frame):
    """A driver the model never saw must not arrive as a fresh integer code."""
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, include_identity=True,
                                dtypes=rows.dtypes)
    X, y = build_matrix(rows, selection)
    model = PassModel("logistic", numeric=selection.numeric,
                      categorical=selection.categorical, seed=42)
    train = X[X["attacker"] != "D4"]
    model.fit(train, y[: len(train)])
    unseen = X[X["attacker"] == "D4"].head(3)
    assert model.predict_proba(unseen).shape == (3,)


def test_configure_threads_sets_every_pool():
    import os

    configure_threads(3)
    assert os.environ["OMP_NUM_THREADS"] == "3"
    assert os.environ["MKL_NUM_THREADS"] == "3"
    with pytest.raises(ValueError):
        configure_threads(0)


# --------------------------------------------------------------------------
# Hardware planning
# --------------------------------------------------------------------------

def test_hardware_plan_never_returns_zero_workers():
    for cores in (1, 2, 4, 16, 128):
        plan = plan_hardware(15, 5_000, cores=cores, free_memory_gb=8.0)
        assert plan.workers >= 1
        assert plan.threads_per_fit >= 1


def test_small_fits_get_fewer_threads_and_more_workers():
    small = plan_hardware(15, 4_000, cores=16, free_memory_gb=32.0)
    large = plan_hardware(15, 500_000, cores=16, free_memory_gb=32.0)
    assert small.threads_per_fit < large.threads_per_fit
    assert small.workers > large.workers


def test_free_memory_caps_the_worker_count():
    plan = plan_hardware(15, 5_000, cores=64, free_memory_gb=1.5)
    assert plan.workers <= 2
    assert any("free memory" in note for note in plan.rationale)


def test_a_tiny_benchmark_does_not_pay_to_spawn_workers():
    """Spawn start-up on Windows exceeds the work for a handful of small fits."""
    plan = plan_hardware(2, 100, cores=16, free_memory_gb=32.0)
    assert plan.use_process_pool is False
    assert any("in-process" in note for note in plan.rationale)


def test_the_plan_records_why(frame):
    assert plan_hardware(15, 5_000, cores=16, free_memory_gb=8.0).rationale


# --------------------------------------------------------------------------
# Benchmark loop, gates and artifacts
# --------------------------------------------------------------------------

def test_fit_cell_captures_a_failure_instead_of_raising(frame):
    """One broken cell must cost its own row in the report, not the whole run."""
    plan = plan_splits(frame, seed=42)
    empty = type(plan.folds[0])("empty", frame.index[:0], frame.index[:0], frame.index[:10])
    result = fit_cell(frame, "DETECTION", "logistic", empty)
    assert result.ok is False
    assert "training split is empty" in result.error


def test_fit_cell_scores_every_split(frame):
    plan = plan_splits(frame, seed=42)
    result = fit_cell(frame, "DETECTION", "logistic", plan.folds[0], threads=1)
    assert result.ok
    for role in ("train", "validation", "test"):
        assert result.metrics[role]["n"] > 0
    assert result.metrics["feature_schema"]["decision_checkpoint"] == "DETECTION"


def test_aggregate_derives_skill_from_aggregated_brier_not_averaged_ratios(frame):
    """Per-fold skill is a ratio and ratios do not average; the columns must agree."""
    plan = plan_splits(frame, seed=42)
    results = [fit_cell(frame, "DETECTION", "logistic", fold, threads=1)
               for fold in plan.folds]
    rows = aggregate(results)
    assert len(rows) == 1
    entry = rows[0]
    assert entry["brier_skill_score"] == pytest.approx(
        1.0 - entry["brier"] / entry["brier_reference"]
    )


def test_gates_flag_a_model_that_loses_to_the_base_rate():
    aggregated = [{
        "checkpoint": "DETECTION", "family": "lightgbm", "folds": 1, "folds_ok": 1,
        "brier": 0.2, "brier_skill_score": -0.05, "log_loss": 0.6, "brier_std": 0.01,
    }]
    gates = check_gates(aggregated, expected_cells=1)
    beat = next(g for g in gates if "constant-base-rate" in g["gate"])
    assert beat["passed"] is False
    assert "lightgbm" in beat["detail"]


def test_gates_report_not_assessable_rather_than_guessing():
    aggregated = [{
        "checkpoint": "DETECTION", "family": "logistic", "folds": 1, "folds_ok": 1,
        "brier": 0.1, "brier_skill_score": 0.2, "log_loss": 0.3, "brier_std": 0.01,
    }]
    gates = check_gates(aggregated, expected_cells=1)
    later = next(g for g in gates if "ACTIVATION and BRAKING" in g["gate"])
    assert later["passed"] is None


def test_artifact_round_trips_and_records_cpu_verification(frame, tmp_path):
    """MODELS.md section 1.1: every artifact must load and score on CPU."""
    rows = frame[frame["decision_checkpoint"] == "DETECTION"]
    selection = select_features("DETECTION", rows.columns, dtypes=rows.dtypes)
    X, y = build_matrix(rows, selection)
    model = PassModel("logistic", numeric=selection.numeric,
                      categorical=selection.categorical, seed=42).fit(X, y)

    manifest = write_fit_artifact(
        tmp_path / "detection" / "logistic",
        model=model, selection=selection, metrics={"cross_validated": {}},
        split_plan={"design": "leave_one_event_out"}, sample_row=X.head(1),
        source_datasets=["data/processed/overtake_opportunities"],
        config_files=["config/feature_registry.yaml"], root=ROOT,
    )
    assert manifest["cpu_inference_verified"] is True
    assert manifest["cpu_inference_round_trip_delta"] == pytest.approx(0.0)
    assert manifest["device_trained_on"] == "cpu"
    assert manifest["seed"] == 42

    reloaded, schema, on_disk = load_fit_artifact(tmp_path / "detection" / "logistic")
    assert schema["decision_checkpoint"] == "DETECTION"
    assert on_disk["feature_schema_version"] == selection.schema_version
    np.testing.assert_allclose(reloaded.predict_proba(X.head(5)),
                               model.predict_proba(X.head(5)))
