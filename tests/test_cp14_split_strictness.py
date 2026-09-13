"""CP-14 must fail closed on its split unit rather than quietly coarsening it.

CP-14 specifies ``unit="battle_id"``. The planner has an event-level fallback,
which is correct for callers that want the coarser unit and say so, and wrong
for CP-14: an event-level run answers a different question and would answer it
under CP-14's name. These tests pin the refusals and the recorded evidence.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.data.splits import build_split_manifest, make_split
from trackshift.pass_model.folds import (
    SplitPlanError,
    load_assignments,
    plan_splits,
    resolve_unit,
)


def frame(battle_ids, *, events=("Australian Grand Prix", "Italian Grand Prix",
                                 "Japanese Grand Prix", "Monaco Grand Prix")):
    """One row per battle, spread over enough events for leave-one-event-out."""
    rows = []
    for index, battle_id in enumerate(battle_ids):
        rows.append({
            "battle_id": battle_id,
            "event": events[index % len(events)],
            "year": "2026",
            "session": "Race",
            "decision_checkpoint": "DETECTION",
            "passed_by_outcome_horizon": bool(index % 2),
        })
    return pd.DataFrame(rows)


def scorable(battle_ids, *, events=("Australian Grand Prix", "Italian Grand Prix",
                                     "Japanese Grand Prix", "Monaco Grand Prix"),
             years=("2026",), per_battle=18):
    """Like :func:`frame`, but with enough positives per event to be scorable.

    ``_leave_one_event_out`` refuses an event with fewer than
    ``MIN_FOLD_POSITIVES`` positives, because a fold scored against one or two
    positives yields a meaningless ROC-AUC. The split tests need fixtures that
    clear that bar; the ``resolve_unit`` tests above deliberately do not, since
    they count rows.
    """
    rows = []
    for year in years:
        for index, battle_id in enumerate(battle_ids):
            for n in range(per_battle):
                rows.append({
                    "battle_id": None if battle_id is None else f"{battle_id}_{year}",
                    "event": events[index % len(events)],
                    "year": year,
                    "session": "Race",
                    "decision_checkpoint": "DETECTION",
                    # Two thirds positive, so every event clears the threshold.
                    "passed_by_outcome_horizon": n % 3 != 0,
                })
    return pd.DataFrame(rows)


def written_assignment(tmp_path, table, unit="battle_id",
                       design="leave_one_event_out", seed=42):
    """Persist a C9 assignment the way build_split_assignments.py does."""
    columns = [c for c in dict.fromkeys((unit, "event", "year", "session"))
               if c in table.columns]
    rows = table.loc[:, columns].to_dict("records")
    assignments = make_split(rows, unit=unit, design=design, seed=seed)
    manifest = dict(build_split_manifest(assignments, unit, design, seed, len(rows)))
    pd.DataFrame(assignments).to_parquet(tmp_path / "split_assignments.parquet", index=False)
    (tmp_path / "split_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


# --- resolve_unit ----------------------------------------------------------

def test_without_a_requirement_a_partial_battle_id_falls_back_to_event():
    table = frame(["b1", "b2", None, "b4"])
    unit, reason = resolve_unit(table)
    assert unit == "event"
    assert "2 of 4" in reason or "populated on 3" in reason


def test_requiring_battle_id_refuses_a_partially_populated_column():
    table = frame(["b1", "b2", None, "b4"])
    with pytest.raises(SplitPlanError, match="populated on only 3 of 4"):
        resolve_unit(table, require="battle_id")


def test_requiring_battle_id_refuses_a_missing_column():
    table = frame(["b1", "b2"]).drop(columns=["battle_id"])
    with pytest.raises(SplitPlanError, match="no battle_id column"):
        resolve_unit(table, require="battle_id")


def test_a_fully_populated_battle_id_is_accepted():
    unit, _ = resolve_unit(frame(["b1", "b2", "b3", "b4"]), require="battle_id")
    assert unit == "battle_id"


def test_an_empty_string_battle_id_does_not_count_as_populated():
    # notna() alone would accept "" and C9 would then group every such row
    # together under one blank key.
    table = frame(["b1", "", "b3", "b4"])
    with pytest.raises(SplitPlanError, match="populated on only 3 of 4"):
        resolve_unit(table, require="battle_id")


# --- plan_splits -----------------------------------------------------------

def test_requiring_battle_id_without_a_persisted_assignment_is_refused():
    table = scorable(["b1", "b2", "b3", "b4"])
    with pytest.raises(SplitPlanError, match="no persistent C9 assignment"):
        plan_splits(table, seed=42, require_unit="battle_id")


def test_a_persisted_assignment_over_the_wrong_unit_is_refused(tmp_path):
    table = scorable(["b1", "b2", "b3", "b4"])
    written_assignment(tmp_path, table, unit="event")
    with pytest.raises(SplitPlanError, match="is over 'event' but 'battle_id' was required"):
        plan_splits(table, seed=42, require_unit="battle_id",
                    assignments=load_assignments(tmp_path))


def test_a_stale_assignment_missing_a_battle_is_refused(tmp_path):
    written_assignment(tmp_path, scorable(["b1", "b2", "b3", "b4"]))
    # M07 was rebuilt and produced a battle the assignment never saw.
    grown = scorable(["b1", "b2", "b3", "b4", "b5_new"])
    with pytest.raises(SplitPlanError, match="absent from the persisted"):
        plan_splits(grown, seed=42, require_unit="battle_id",
                    assignments=load_assignments(tmp_path))


def test_the_strict_path_succeeds_and_records_the_assignment_version(tmp_path):
    table = scorable(["b1", "b2", "b3", "b4"])
    manifest = written_assignment(tmp_path, table)
    plan = plan_splits(table, seed=42, require_unit="battle_id",
                       assignments=load_assignments(tmp_path))
    assert plan.unit == "battle_id"
    recorded = plan.manifest["persistent_assignment"]
    assert recorded["assignment_version"] == manifest["assignment_version"]
    assert any("read from disk rather than re-derived" in note for note in plan.notes)


def test_a_missing_assignment_directory_names_the_build_command(tmp_path):
    with pytest.raises(SplitPlanError, match="build_split_assignments.py"):
        load_assignments(tmp_path / "absent")


def test_an_assignment_directory_missing_its_columns_is_refused(tmp_path):
    pd.DataFrame({"unrelated": [1]}).to_parquet(
        tmp_path / "split_assignments.parquet", index=False)
    (tmp_path / "split_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SplitPlanError, match="missing 'group_key'"):
        load_assignments(tmp_path)


def test_the_default_path_still_works_for_callers_that_want_the_event_unit(tmp_path):
    # The fallback is not removed, only refused under a requirement: other
    # consumers of plan_splits keep their existing behaviour.
    table = scorable(["b1", None, "b3", "b4"])
    plan = plan_splits(table, seed=42)
    assert plan.unit == "event"


# --- DRS is not a modelling parameter --------------------------------------

def test_drs_features_are_refused_even_if_the_registry_stops_calling_them_metadata():
    """DRS exists only in 2022-2025 and has no 2026 counterpart.

    A model that leans on it learns the DRS era and then carries that lesson
    into a season where the mechanism does not exist. The registry currently
    also tags these ``metadata_only``; this pins the independent refusal so
    dropping that tag cannot quietly re-admit them.
    """
    from trackshift.pass_model.features import (
        EXCLUDED_GROUPS,
        FORBIDDEN_MODEL_TOKENS,
        select_features,
    )

    assert "historical_drs" in EXCLUDED_GROUPS
    assert "drs" in FORBIDDEN_MODEL_TOKENS

    columns = ["gap_at_checkpoint", "historical_drs_open", "historical_drs_eligible",
               "drs_open", "passed_by_outcome_horizon"]
    selection = select_features("DETECTION", columns, include_identity=False)

    for name in ("historical_drs_open", "historical_drs_eligible", "drs_open"):
        assert name not in selection.columns, f"{name} reached the feature matrix"
        assert name in selection.excluded


def test_the_audit_is_a_backstop_for_a_drs_column_that_slips_through():
    """Even if selection were bypassed, the pre-training audit must refuse."""
    import pandas as pd
    from trackshift.pass_model.features import (
        FeatureSelection,
        FeatureSelectionError,
        audit_feature_matrix,
    )

    smuggled = FeatureSelection(
        checkpoint="DETECTION",
        numeric=("gap_at_checkpoint", "historical_drs_open"),
        categorical=(), identity=(), include_identity=False, excluded={},
    )
    frame = pd.DataFrame({"gap_at_checkpoint": [0.4, 0.9, 1.2],
                          "historical_drs_open": [1.0, 0.0, 1.0]})
    with pytest.raises(FeatureSelectionError, match="forbidden"):
        audit_feature_matrix(frame, smuggled)


# --- evidence grading -------------------------------------------------------

def multi_year_frame():
    """A table spanning CP-14's documented years, scorable in every event."""
    return scorable(["b1", "b2", "b3", "b4"],
                    years=("2022", "2023", "2024", "2025", "2026"))


def test_a_single_season_run_grades_interim_not_full(tmp_path):
    """The current state: leakage-safe, real numbers, but not CP-14's split."""
    from trackshift.pass_model.folds import EVIDENCE_INTERIM, grade_evidence

    table = scorable(["b1", "b2", "b3", "b4"])       # 2026 only
    written_assignment(tmp_path, table)
    plan = plan_splits(table, seed=42, require_unit="battle_id",
                       assignments=load_assignments(tmp_path))
    evidence = grade_evidence(table, plan)

    assert evidence["grade"] == EVIDENCE_INTERIM
    assert evidence["is_cp14_acceptance_run"] is False
    assert plan.unit == "battle_id"                   # the guarantee still holds
    assert any("year_table" in r for r in evidence["reasons"])


def test_the_documented_split_grades_full(tmp_path):
    from trackshift.pass_model.folds import EVIDENCE_FULL, grade_evidence

    table = multi_year_frame()
    written_assignment(tmp_path, table)
    plan = plan_splits(table, design="year_table", seed=42, require_unit="battle_id",
                       assignments=load_assignments(tmp_path))
    evidence = grade_evidence(table, plan)

    assert plan.design == "year_table"
    assert evidence["grade"] == EVIDENCE_FULL
    assert evidence["is_cp14_acceptance_run"] is True
    assert evidence["reasons"] == []


def test_a_year_table_missing_training_years_is_downgraded_to_interim(tmp_path):
    """"train 2022-2024" must not be claimed when 2022 and 2023 are absent."""
    from trackshift.pass_model.folds import EVIDENCE_INTERIM, grade_evidence

    table = multi_year_frame()
    table = table[table["year"].isin(["2024", "2025", "2026"])].reset_index(drop=True)
    written_assignment(tmp_path, table)
    plan = plan_splits(table, design="year_table", seed=42, require_unit="battle_id",
                       assignments=load_assignments(tmp_path))
    evidence = grade_evidence(table, plan)

    assert evidence["grade"] == EVIDENCE_INTERIM
    assert any("2022" in r and "2023" in r for r in evidence["reasons"])


def test_an_event_unit_run_grades_reduced():
    from trackshift.pass_model.folds import EVIDENCE_REDUCED, grade_evidence

    table = scorable(["b1", None, "b3", "b4"])
    plan = plan_splits(table, seed=42)                # no requirement -> event unit
    evidence = grade_evidence(table, plan)

    assert evidence["grade"] == EVIDENCE_REDUCED
    assert evidence["is_cp14_acceptance_run"] is False


def test_the_provenance_gate_fails_and_leads_the_gate_list(tmp_path):
    """A reader scanning gates top-down must hit the provenance one first."""
    from trackshift.pass_model.folds import grade_evidence
    from trackshift.pass_model.report import check_gates

    table = scorable(["b1", "b2", "b3", "b4"])
    written_assignment(tmp_path, table)
    plan = plan_splits(table, seed=42, require_unit="battle_id",
                       assignments=load_assignments(tmp_path))
    gates = check_gates([], expected_cells=0, evidence=grade_evidence(table, plan))

    assert gates[0]["passed"] is False
    assert "documented split" in gates[0]["gate"]
    assert "does NOT satisfy CP-14" in gates[0]["detail"]


def test_the_report_banner_states_it_is_not_a_pass(tmp_path):
    from trackshift.pass_model.folds import grade_evidence
    from trackshift.pass_model.report import render_report

    table = scorable(["b1", "b2", "b3", "b4"])
    written_assignment(tmp_path, table)
    plan = plan_splits(table, seed=42, require_unit="battle_id",
                       assignments=load_assignments(tmp_path))
    text = render_report(
        run={"created_utc": "now", "git_commit": "abc", "seed": 42,
             "checkpoints": ["DETECTION"], "families": ["logistic"]},
        dataset={"rows": 4, "label_definition": "zone_exit_v1"},
        split=plan.as_dict(), split_summary=[], aggregated=[], gates=[],
        hardware={}, evidence=grade_evidence(table, plan),
    )
    assert "NOT a CP-14 pass" in text
    assert "INTERIM" in text


# --- thin folds -------------------------------------------------------------

def test_an_event_too_thin_to_score_is_kept_in_training_but_never_a_fold(tmp_path):
    """Monaco in the real 2026 data has 63 opportunities and 1 positive.

    Scored as a test fold that yields a ROC-AUC computed on one positive; used
    as a validation fold it early-stops the boosted models essentially at
    random. It still carries usable training rows, so it is kept in training
    and excluded from the fold rotation rather than dropped outright.
    """
    from trackshift.pass_model.folds import MIN_FOLD_POSITIVES

    table = pd.concat([
        scorable(["b1", "b2", "b3"],
                 events=("Australian Grand Prix", "Italian Grand Prix",
                         "Japanese Grand Prix")),
        # One battle at Monaco with a single positive, as on the real data.
        pd.DataFrame([{
            "battle_id": "b_monaco", "event": "Monaco Grand Prix", "year": "2026",
            "session": "Race", "decision_checkpoint": "DETECTION",
            "passed_by_outcome_horizon": n == 0,
        } for n in range(9)]),
    ], ignore_index=True)

    plan = plan_splits(table, seed=42)
    fold_names = {f.name for f in plan.folds}

    assert "Monaco Grand Prix" not in fold_names, "a 1-positive event was scored"
    assert len(plan.folds) == 3

    monaco_rows = set(table.index[table["event"] == "Monaco Grand Prix"])
    assert monaco_rows & set(plan.folds[0].train), "thin event dropped from training too"
    assert any(f"fewer than {MIN_FOLD_POSITIVES}" in note for note in plan.notes)
    assert any("Monaco Grand Prix (1)" in note for note in plan.notes)


def test_the_guard_refuses_rather_than_building_a_meaningless_rotation():
    """Fewer than three scorable events means no honest fold rotation exists."""
    table = pd.DataFrame([{
        "battle_id": f"b{n}", "event": ("A Grand Prix", "B Grand Prix",
                                        "C Grand Prix", "D Grand Prix")[n % 4],
        "year": "2026", "session": "Race", "decision_checkpoint": "DETECTION",
        "passed_by_outcome_horizon": n == 0,
    } for n in range(8)])
    with pytest.raises(SplitPlanError, match="too thin to score"):
        plan_splits(table, seed=42)
