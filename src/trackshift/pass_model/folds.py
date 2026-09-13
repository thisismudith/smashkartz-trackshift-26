"""Split construction for the M10 benchmark (Tanveer CP-14, AGENTS.md section 40).

Never random. Splits are built on C9 (``trackshift.data.splits``) so the pass
model and everything else in the project draw the same boundaries, and so the
assignment is locked by a manifest hash rather than re-derived per run.

Three designs, and :func:`plan_splits` picks between them from what the table
actually holds rather than from what CP-14 assumed it would hold:

``year_table``
    CP-14's documented split: train 2022-2024, validate 2025, test 2026 with the
    British Grand Prix frozen out. Selected automatically once the table carries
    a pre-2026 year.
``leave_one_event_out``
    One outer fold per event, each event taking a turn as the test set. Selected
    when the table holds a single season, which is the current state while the
    historical C1/C7 spine is still being materialised. This is CP-14's own
    leave-one-track-out check promoted from secondary to primary, which is the
    honest thing to do when the year axis has one value -- but it is **not**
    CP-14's documented split, and :func:`grade_evidence` says so in every
    manifest and report so the distinction cannot be lost between the run and
    the write-up.
``kfold``
    C9 k-fold over ``battle_id``, exactly as CP-14 specifies. Available once
    ``battle_id`` is populated, which the A2 C8 join in
    ``trackshift.features.battle_join`` now does.

**Why the unit matters.** CP-14 asks for ``unit="battle_id"`` to stop one battle
appearing in both train and test -- the same pair of cars, the same lap, scored
twice. Grouping by event would also prevent that, because a battle cannot span
two events, so the fallback keeps the guarantee and loses only granularity. It
is nonetheless refused under ``require_unit="battle_id"``: CP-14 names the unit,
and a run that quietly substitutes a coarser one answers a different question
under the checkpoint's name.

**Three things must hold before a run is a CP-14 pass**, and they fail
independently: the unit must be ``battle_id``, the design must be the
``year_table``, and the years that table names must actually be present. A run
can be perfectly leakage-safe and still not be a CP-14 pass because the
historical seasons do not exist yet. :func:`grade_evidence` separates those
cases into FULL, INTERIM and REDUCED rather than letting "the benchmark ran"
stand in for "the checkpoint passed".

**Why the year table is not built through** ``make_split``. C9 promotes any group
containing a British Grand Prix row to HOLDOUT. For a battle or event unit that
is exactly right. For a *year* unit it would promote the whole of 2026 and
delete the test split. So the year table assigns roles directly and uses C9 for
the two parts that carry the guarantee: the eligibility assertion and the
manifest hash.

**How much of the British Grand Prix is held out.** Two readings are defensible
and the project currently holds both, so this module makes the choice a
parameter rather than inheriting whichever guard happens to be called first:

``DemoScope.TRACK`` (the default here)
    Every British Grand Prix, all seasons. This is what
    ``splits.assert_training_or_calibration_eligible`` enforces unconditionally
    -- it matches on event name with no year check -- and it is the stricter
    claim: the model has never seen Silverstone, so it cannot have memorised the
    circuit's geometry or its habitual overtaking spots. It costs the 2022-2025
    Silverstone races as training data.

``DemoScope.EVENT_YEAR``
    2026 British Grand Prix only, which is ``guards.py``'s default and what
    CP-14's split table literally describes ("train 2022-2024, all events").
    Selecting it means C9's stricter assertion would refuse the resulting
    training split, so it is **not** applied under this scope and the plan says
    so in its notes rather than leaving the two guards silently disagreeing.

The two scopes are identical while the table is 2026-only, which it is today.
The choice starts to matter the moment historical opportunities exist, so it is
recorded in the plan and in the validation report either way.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..data.guards import DemoScope, assert_demo_held_out, is_demo_row
from ..data.splits import (
    assert_training_or_calibration_eligible,
    build_split_manifest,
    make_split,
)

__all__ = [
    "DESIGNS",
    "TRAIN_YEARS",
    "VALIDATION_YEAR",
    "TEST_YEAR",
    "Fold",
    "SplitPlan",
    "SplitPlanError",
    "MIN_FOLD_POSITIVES",
    "EVIDENCE_FULL",
    "EVIDENCE_INTERIM",
    "EVIDENCE_REDUCED",
    "grade_evidence",
    "load_assignments",
    "plan_splits",
    "resolve_unit",
]

DESIGNS = ("auto", "year_table", "leave_one_event_out", "kfold")

TRAIN_YEARS = ("2022", "2023", "2024")
VALIDATION_YEAR = "2025"
TEST_YEAR = "2026"


class SplitPlanError(ValueError):
    """Raised when no honest split can be built from the table as it stands."""


@dataclass(frozen=True)
class Fold:
    """One train / validation / test partition, as positional row indices."""

    name: str
    train: Any
    validation: Any
    test: Any
    notes: str = ""

    def sizes(self) -> dict[str, int]:
        return {
            "train": int(len(self.train)),
            "validation": int(len(self.validation)),
            "test": int(len(self.test)),
        }


@dataclass(frozen=True)
class SplitPlan:
    """Every fold, plus the frozen holdout and the C9 manifest that locks it."""

    design: str
    unit: str
    seed: int
    folds: tuple[Fold, ...]
    holdout: Any
    manifest: Mapping[str, Any]
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "design": self.design,
            "split_unit": self.unit,
            "seed": self.seed,
            "n_folds": len(self.folds),
            "folds": [{"name": f.name, **f.sizes(), "notes": f.notes} for f in self.folds],
            "holdout_rows": int(len(self.holdout)),
            "c9_manifest": dict(self.manifest),
            "notes": list(self.notes),
        }


def resolve_unit(frame, require: str | None = None) -> tuple[str, str]:
    """Return ``(unit, reason)``: ``battle_id`` when usable, else ``event``.

    ``require="battle_id"`` turns the fallback off and raises instead. CP-14
    passes it: an event-level split there would answer a different question from
    the one the checkpoint asks, and would do it under CP-14's name. The
    fallback remains available to callers that genuinely want the coarser unit
    and say so.
    """
    if "battle_id" not in frame.columns:
        if require:
            raise SplitPlanError(
                f"{require!r} was required but the table has no battle_id column. "
                "Run the M07 build with the C8 join, then "
                "scripts/features/build_split_assignments.py.")
        return "event", "no battle_id column; grouping by event"

    present = frame["battle_id"].notna() & (
        frame["battle_id"].astype(str).str.strip() != "")
    if bool(present.all()):
        return "battle_id", "battle_id populated on every row (CP-14 preferred unit)"

    filled = int(present.sum())
    if require:
        raise SplitPlanError(
            f"{require!r} was required but battle_id is populated on only {filled} of "
            f"{len(frame)} rows. C9 refuses a partially populated unit, and falling back "
            "to event would silently answer a different question under CP-14's name. "
            "Fix the A2 C8 join rather than lowering the unit; rows that genuinely have "
            "no episode must be excluded, not relabelled.")
    return "event", (
        f"battle_id populated on {filled} of {len(frame)} rows, so C9 would refuse it; "
        "grouping by event instead, which still prevents a battle spanning two folds "
        "because a battle cannot span two events"
    )


#: What a run has to be before its numbers may be quoted as a CP-14 pass.
EVIDENCE_FULL = "FULL"
EVIDENCE_INTERIM = "INTERIM"
EVIDENCE_REDUCED = "REDUCED"


def grade_evidence(frame, plan: SplitPlan) -> dict[str, Any]:
    """State what this run's numbers are worth, and why.

    CP-14's acceptance gate is not "a benchmark ran". It is a benchmark run on
    the documented split: train 2022-2024, validate 2025, test 2026 without the
    British Grand Prix, grouped by ``battle_id``. Three things can be true
    instead, and they are not equivalent:

    ``FULL``
        The documented split. Only this may be reported as CP-14 passing.
    ``INTERIM``
        The split unit is ``battle_id``, so the leakage guarantee holds and the
        numbers are real, but the year table was not available -- typically
        because the historical seasons are not yet built. An honest benchmark
        that does not answer CP-14's question about generalising across
        regulation eras.
    ``REDUCED``
        The split unit is coarser than ``battle_id``. The leakage guarantee is
        weaker than CP-14 requires and the numbers must not be compared with a
        FULL run.

    Returned rather than raised, because an INTERIM run is worth doing -- it is
    how the pipeline gets exercised before the historical lake lands. What must
    not happen is an INTERIM run being written up as a pass, so the grade
    travels with the numbers into the manifest and the report.
    """
    reasons: list[str] = []
    available = _years(frame)

    if plan.unit != "battle_id":
        grade = EVIDENCE_REDUCED
        reasons.append(
            f"split unit is {plan.unit!r}, not CP-14's 'battle_id'; the "
            "no-battle-across-folds guarantee is coarser than the checkpoint requires")
    elif plan.design != "year_table":
        grade = EVIDENCE_INTERIM
        missing_years = [y for y in (*TRAIN_YEARS, VALIDATION_YEAR) if y not in available]
        reasons.append(
            f"design is {plan.design!r}, not CP-14's documented 'year_table'"
            + (f"; the table holds no {missing_years}" if missing_years else ""))
        reasons.append(
            "the benchmark is leakage-safe and its numbers are real, but it does not "
            "measure generalisation across regulation eras, which is what CP-14's "
            "year table exists to measure")
    else:
        grade = EVIDENCE_FULL
        missing_train = [y for y in TRAIN_YEARS if y not in available]
        if missing_train:
            grade = EVIDENCE_INTERIM
            reasons.append(
                f"year_table ran but training years {missing_train} are absent, so "
                "'train 2022-2024' overstates what the model saw")

    return {
        "grade": grade,
        "is_cp14_acceptance_run": grade == EVIDENCE_FULL,
        "design": plan.design,
        "split_unit": plan.unit,
        "years_present": sorted(available),
        "train_years_required": list(TRAIN_YEARS),
        "validation_year_required": VALIDATION_YEAR,
        "test_year_required": TEST_YEAR,
        "reasons": reasons,
        "note": (
            "Only a FULL run satisfies CP-14. An INTERIM or REDUCED run is evidence "
            "that the pipeline works, not evidence that the checkpoint passed."
        ),
    }


def load_assignments(root) -> dict[str, Any]:
    """Read the persistent C9 assignment artifact written by A2.

    Returns the manifest plus a ``group_key -> role`` map. Reading the
    assignment rather than re-deriving it is the point: two runs that re-derive
    can disagree, and nothing in the artifacts would say why.
    """
    from pathlib import Path

    root = Path(root)
    manifest_path = root / "split_manifest.json"
    assignments_path = root / "split_assignments.parquet"
    if not manifest_path.exists() or not assignments_path.exists():
        raise SplitPlanError(
            f"no persistent C9 assignment under {root}. CP-14 consumes a written "
            "assignment so runs are comparable; build it with\n"
            "  python scripts/features/build_split_assignments.py")

    import json

    import pandas as pd

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    table = pd.read_parquet(assignments_path)
    for column in ("group_key", "split_role", "fold_id"):
        if column not in table.columns:
            raise SplitPlanError(
                f"{assignments_path} is missing {column!r}; it was not written by "
                "build_split_assignments.py")
    return {
        "manifest": manifest,
        "unit": str(manifest.get("unit", "")),
        "roles": dict(zip(table["group_key"].astype(str), table["split_role"].astype(str))),
        "folds": dict(zip(table["group_key"].astype(str), table["fold_id"].astype(str))),
    }


def assert_assignment_covers(frame, assignments: Mapping[str, Any], unit: str) -> None:
    """Refuse a frame carrying a group the persisted assignment never saw.

    A row whose battle is absent from the assignment has no split role, so
    including it would mean inventing one at training time -- exactly what
    persisting the assignment was meant to stop.
    """
    if unit not in frame.columns:
        raise SplitPlanError(f"frame has no {unit!r} column to check against the assignment")
    known = set(assignments["roles"])
    seen = {str(v) for v in frame[unit].dropna().unique()}
    missing = sorted(seen - known)
    if missing:
        raise SplitPlanError(
            f"{len(missing)} {unit} value(s) in the table are absent from the persisted "
            f"C9 assignment (for example {missing[:3]}). The assignment is stale: rebuild "
            "it with scripts/features/build_split_assignments.py after any M07 rebuild.")


def _years(frame) -> set[str]:
    return {str(value) for value in frame["year"].dropna().unique()}


def _demo_mask(frame, scope: DemoScope = DemoScope.TRACK):
    records = frame[["year", "event"]].to_dict("records")
    import numpy as np

    return np.array([is_demo_row(row, scope) for row in records], dtype=bool)


def _c9_manifest(frame, unit: str, design: str, seed: int) -> Mapping[str, Any]:
    """Build the C9 assignment for ``unit`` and lock it with a manifest hash.

    Uses ``leave_one_event_out`` for the assignment shape whatever the modelling
    design is: the manifest exists to pin *which group each row belongs to* and
    that a group never straddles a split, not to choose the roles.
    """
    columns = [c for c in ("battle_id", "event", "year", "session") if c in frame.columns]
    rows = frame.loc[:, columns].to_dict("records")
    assignments = make_split(rows, unit=unit, design="leave_one_event_out", seed=seed)
    manifest = dict(build_split_manifest(assignments, unit, design, seed, len(rows)))
    manifest["modelling_design"] = design
    return manifest


def _guard(frame, index, context: str, scope: DemoScope = DemoScope.TRACK) -> None:
    """Refuse a training or calibration split that contains the demo event.

    Under ``TRACK`` both guards agree and both run. Under ``EVENT_YEAR`` C9's
    assertion would reject a split this scope deliberately permits -- historical
    Silverstone -- so only the scope-aware guard runs, and the caller records
    that the stricter check was skipped by choice rather than by omission.
    """
    if len(index) == 0:
        return
    subset = frame.loc[index, ["year", "event"]]
    assert_demo_held_out(subset, context, scope)
    if scope is DemoScope.TRACK:
        assert_training_or_calibration_eligible(subset.to_dict("records"), "training")


def _year_table(frame, unit: str, seed: int, scope: DemoScope) -> SplitPlan:
    available = _years(frame)
    missing_train = [y for y in TRAIN_YEARS if y not in available]
    if len(missing_train) == len(TRAIN_YEARS):
        raise SplitPlanError(
            f"year_table needs at least one of {list(TRAIN_YEARS)} for training; "
            f"the table holds {sorted(available)}"
        )
    if VALIDATION_YEAR not in available:
        raise SplitPlanError(
            f"year_table needs {VALIDATION_YEAR} for validation; the table holds "
            f"{sorted(available)}. Early stopping and CP-15 calibration both fit on "
            "this split, and section 27 forbids reusing the test event for either."
        )
    if TEST_YEAR not in available:
        raise SplitPlanError(
            f"year_table needs {TEST_YEAR} for the test split; the table holds {sorted(available)}"
        )

    year = frame["year"].astype(str)
    demo = _demo_mask(frame, scope)
    train = frame.index[year.isin(TRAIN_YEARS) & ~demo]
    validation = frame.index[(year == VALIDATION_YEAR) & ~demo]
    test = frame.index[(year == TEST_YEAR) & ~demo]
    holdout = frame.index[demo]

    _guard(frame, train, "pass model year_table training split", scope)
    _guard(frame, validation, "pass model year_table validation split", scope)

    notes = [
        "CP-14 documented split: train 2022-2024, validate 2025, test 2026 excluding "
        "the British Grand Prix.",
    ]
    if missing_train:
        notes.append(f"training years absent from the table: {missing_train}")
    return SplitPlan(
        design="year_table",
        unit=unit,
        seed=seed,
        folds=(Fold("year_table", train, validation, test,
                    "single fold; the year axis defines the roles"),),
        holdout=holdout,
        manifest=_c9_manifest(frame, unit, "year_table", seed),
        notes=tuple(notes),
    )


#: An event scored as a test or validation fold needs at least this many
#: positive labels for its metrics to mean anything. Below it, ROC-AUC and
#: PR-AUC are noise and early stopping against it halts essentially at random.
MIN_FOLD_POSITIVES = 10


def _positives_by_event(frame) -> dict[str, int]:
    """Labelled positives per event, counted once per opportunity."""
    if "passed_by_outcome_horizon" not in frame.columns:
        return {}
    subset = frame
    if "decision_checkpoint" in frame.columns:
        # Three rows share one opportunity and one label; counting rows would
        # treat a single pass as three.
        subset = frame[frame["decision_checkpoint"] == "DETECTION"]
    labelled = subset[subset["passed_by_outcome_horizon"].notna()]
    if labelled.empty:
        return {}
    counts = labelled.groupby(labelled["event"].astype(str))[
        "passed_by_outcome_horizon"].apply(lambda s: int(s.astype(bool).sum()))
    return {str(k): int(v) for k, v in counts.items()}


def _leave_one_event_out(frame, unit: str, seed: int, scope: DemoScope,
                         min_fold_positives: int = MIN_FOLD_POSITIVES) -> SplitPlan:
    demo = _demo_mask(frame, scope)
    holdout = frame.index[demo]
    pool = frame.loc[~demo]
    all_events = sorted({str(value) for value in pool["event"].dropna().unique()})

    # An event too thin to score is kept in training and never used as a test or
    # validation fold. Dropping it entirely would throw away usable training
    # rows; scoring against it would put a meaningless ROC-AUC in the report and
    # let one unmeasurable fold drive the cross-fold variance gate.
    positives = _positives_by_event(pool)
    thin = {e: positives.get(e, 0) for e in all_events
            if positives.get(e, 0) < min_fold_positives}
    events = [e for e in all_events if e not in thin]
    if len(events) < 3:
        raise SplitPlanError(
            f"leave_one_event_out needs at least 3 non-demo events with at least "
            f"{min_fold_positives} positive labels each so every fold has a training "
            f"set, a scorable validation event and a scorable test event; scorable "
            f"events are {events}, too thin to score: {thin}"
        )

    folds: list[Fold] = []
    event_column = frame["event"].astype(str)
    for position, test_event in enumerate(events):
        # The validation event is the next one in sorted order, wrapping. Fixed
        # rather than random so a rerun reproduces the fold exactly, and rotating
        # rather than fixed so no single event is permanently spent on early
        # stopping and never tested against.
        validation_event = events[(position + 1) % len(events)]
        test = frame.index[(event_column == test_event) & ~demo]
        validation = frame.index[(event_column == validation_event) & ~demo]
        train = frame.index[
            ~event_column.isin({test_event, validation_event}) & ~demo
        ]
        _guard(frame, train, f"pass model LOEO fold {test_event} training split", scope)
        _guard(frame, validation,
               f"pass model LOEO fold {test_event} validation split", scope)
        folds.append(Fold(
            name=test_event,
            train=train,
            validation=validation,
            test=test,
            notes=f"validation event: {validation_event}",
        ))

    return SplitPlan(
        design="leave_one_event_out",
        unit=unit,
        seed=seed,
        folds=tuple(folds),
        holdout=holdout,
        manifest=_c9_manifest(frame, unit, "leave_one_event_out", seed),
        notes=(
            "One outer fold per event. A battle cannot span two events, so the "
            "no-battle-across-folds property CP-14 wanted from battle_id holds here too.",
            "Per-fold n varies widely; read every metric against its own n.",
        ) + ((
            f"events kept in training but never scored as a fold, having fewer than "
            f"{min_fold_positives} positive labels: "
            + ", ".join(f"{event} ({count})" for event, count in sorted(thin.items()))
            + ". Scoring a fold against so few positives would put a meaningless "
              "ROC-AUC in the report and let one unmeasurable fold drive the "
              "cross-fold variance gate.",
        ) if thin else ()),
    )


def _kfold(frame, unit: str, seed: int, folds: int, scope: DemoScope) -> SplitPlan:
    if unit != "battle_id":
        raise SplitPlanError(
            "kfold is CP-14's battle_id design and needs a populated battle_id column. "
            "build_opportunities.py does not set one, so every row is null; join C8 in "
            "CP-13 first or use leave_one_event_out."
        )
    demo = _demo_mask(frame, scope)
    holdout = frame.index[demo]
    pool = frame.loc[~demo]
    columns = [c for c in ("battle_id", "event", "year", "session") if c in pool.columns]
    assignments = make_split(pool.loc[:, columns].to_dict("records"),
                             unit=unit, design=f"kfold:{folds}", seed=seed)
    fold_of = {row["group_key"]: row["fold_id"] for row in assignments
               if row["split_role"] == "EVALUATION"}
    group = frame["battle_id"].astype(str).map(fold_of)
    names = sorted({value for value in fold_of.values()})

    built: list[Fold] = []
    for position, name in enumerate(names):
        validation_name = names[(position + 1) % len(names)]
        test = frame.index[(group == name) & ~demo]
        validation = frame.index[(group == validation_name) & ~demo]
        train = frame.index[~group.isin({name, validation_name}) & group.notna() & ~demo]
        _guard(frame, train, f"pass model kfold {name} training split", scope)
        _guard(frame, validation, f"pass model kfold {name} validation split", scope)
        built.append(Fold(name, train, validation, test,
                          f"validation fold: {validation_name}"))

    return SplitPlan(
        design="kfold",
        unit=unit,
        seed=seed,
        folds=tuple(built),
        holdout=holdout,
        manifest=_c9_manifest(frame, unit, f"kfold:{folds}", seed),
        notes=("C9 k-fold over battle_id, CP-14's literal design.",),
    )


def plan_splits(
    frame,
    *,
    design: str = "auto",
    seed: int = 42,
    unit: str | None = None,
    folds: int = 5,
    demo_scope: DemoScope = DemoScope.TRACK,
    require_unit: str | None = None,
    assignments: Mapping[str, Any] | None = None,
    min_fold_positives: int = MIN_FOLD_POSITIVES,
) -> SplitPlan:
    """Build the split plan for ``frame``.

    ``design="auto"`` prefers CP-14's documented year table and falls back to
    leave-one-event-out when the table holds a single season. The fallback is
    recorded in the plan's notes and in the manifest, never silent: a report that
    said "train 2022-2024" over 2026-only data would be a false claim about what
    the model had seen.

    ``demo_scope`` chooses how much of the British Grand Prix is frozen. See the
    module docstring; the default holds out every season, which is the only
    setting where this module and C9 agree.
    """
    if design not in DESIGNS:
        raise SplitPlanError(f"unknown design {design!r}; expected one of {DESIGNS}")
    for column in ("year", "event"):
        if column not in frame.columns:
            raise SplitPlanError(f"frame is missing {column!r}, which every design needs")
    scope = DemoScope(demo_scope)

    resolved_unit, reason = resolve_unit(frame, require=require_unit)
    if unit is not None:
        resolved_unit = unit

    assignment_note = "no persistent C9 assignment supplied; split derived in-run"
    if assignments is not None:
        persisted = str(assignments.get("unit", ""))
        if require_unit and persisted != require_unit:
            raise SplitPlanError(
                f"the persisted C9 assignment is over {persisted!r} but {require_unit!r} "
                "was required. Rebuild it with "
                f"--unit {require_unit} rather than relaxing the requirement here.")
        assert_assignment_covers(frame, assignments, persisted or resolved_unit)
        version = assignments.get("manifest", {}).get("assignment_version", "unknown")
        assignment_note = (
            f"C9 assignment {version} over {persisted!r}, read from disk rather than "
            "re-derived, so this run is comparable with any other that cites it")
    elif require_unit:
        raise SplitPlanError(
            f"{require_unit!r} was required but no persistent C9 assignment was supplied. "
            "CP-14 consumes a written assignment so two runs cannot silently disagree; "
            "build it with scripts/features/build_split_assignments.py")

    if design == "auto":
        available = _years(frame)
        design = "year_table" if (available - {TEST_YEAR}) and VALIDATION_YEAR in available \
            else "leave_one_event_out"

    if design == "year_table":
        plan = _year_table(frame, resolved_unit, seed, scope)
    elif design == "kfold":
        plan = _kfold(frame, resolved_unit, seed, folds, scope)
    else:
        plan = _leave_one_event_out(frame, resolved_unit, seed, scope,
                                    min_fold_positives=min_fold_positives)

    scope_note = (
        f"demo holdout scope: {scope.value} -- "
        + ("every British Grand Prix, all seasons; C9's stricter assertion applied"
           if scope is DemoScope.TRACK else
           "2026 British Grand Prix only. Historical Silverstone is trainable, so C9's "
           "assert_training_or_calibration_eligible (which matches the event name in "
           "every season) was deliberately NOT applied to these splits")
    )
    return SplitPlan(
        design=plan.design,
        unit=plan.unit,
        seed=plan.seed,
        folds=plan.folds,
        holdout=plan.holdout,
        manifest={
            **plan.manifest,
            "demo_scope": scope.value,
            "persistent_assignment": (
                dict(assignments["manifest"]) if assignments is not None else None),
        },
        notes=plan.notes + (f"split unit: {reason}", scope_note, assignment_note),
    )


def summarise(frame, plan: SplitPlan) -> list[dict[str, Any]]:
    """Per-fold row, label and event counts, for the report table."""
    out: list[dict[str, Any]] = []
    for fold in plan.folds:
        entry: dict[str, Any] = {"fold": fold.name, "notes": fold.notes}
        for role, index in (("train", fold.train), ("validation", fold.validation),
                            ("test", fold.test)):
            subset = frame.loc[index]
            labelled = subset["passed_by_outcome_horizon"].dropna()
            entry[f"{role}_rows"] = int(len(subset))
            entry[f"{role}_labelled"] = int(len(labelled))
            entry[f"{role}_positive"] = int(labelled.astype(bool).sum())
            entry[f"{role}_events"] = int(subset["event"].nunique())
        out.append(entry)
    return out


def assert_disjoint(plan: SplitPlan) -> None:
    """Fail if any fold shares a row between train, validation and test.

    Cheap, and it catches the failure mode that would otherwise show up only as
    an implausibly good test score three stages later.
    """
    for fold in plan.folds:
        train, validation, test = set(fold.train), set(fold.validation), set(fold.test)
        for left_name, left, right_name, right in (
            ("train", train, "validation", validation),
            ("train", train, "test", test),
            ("validation", validation, "test", test),
        ):
            overlap = left & right
            if overlap:
                raise SplitPlanError(
                    f"fold {fold.name}: {len(overlap)} row(s) appear in both {left_name} "
                    f"and {right_name}"
                )
        if set(plan.holdout) & (train | validation | test):
            raise SplitPlanError(
                f"fold {fold.name}: the frozen British Grand Prix holdout reached a "
                "modelling split"
            )
