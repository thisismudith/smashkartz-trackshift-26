"""The CP-14 validation report, and the gates it reports against.

:func:`check_gates` is CP-14's acceptance list turned into code. Written out as
prose in a checklist, those gates get eyeballed once and then quietly stop being
checked; evaluated here, every run states which passed and which did not, and a
failure is a line in the report rather than an omission.

Two of them are the interesting ones:

*Every model beats a constant-base-rate predictor on Brier.* A model that cannot
is worse than knowing the base rate, and at this dataset size that is a real
possibility rather than a formality.

*ACTIVATION and BRAKING beat DETECTION.* They see strictly more, so they should.
If DETECTION wins, the most likely explanation is leakage into the DETECTION
matrix -- which is why this gate is stated as a suspicion of leakage rather than
as a performance note.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .features import CHECKPOINTS
from .metrics import SELECTION_RULE, rank_results

__all__ = ["check_gates", "render_report", "GateResult"]

#: Above this, leave-one-track-out variance means the model is memorising
#: circuits rather than learning racecraft (CP-14).
BRIER_STD_CEILING = 0.05


class GateResult(dict):
    """A gate outcome: ``passed`` may be ``None`` for "not assessable"."""

    def __init__(self, name: str, passed: bool | None, detail: str) -> None:
        super().__init__(gate=name, passed=passed, detail=detail)

    @property
    def symbol(self) -> str:
        return {True: "PASS", False: "FAIL", None: "n/a"}[self["passed"]]


def _by_checkpoint(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    out: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row["checkpoint"]), []).append(row)
    return out


def check_gates(
    aggregated: Sequence[Mapping[str, Any]],
    *,
    expected_cells: int,
    checkpoints: Sequence[str] = CHECKPOINTS,
    evidence: Mapping[str, Any] | None = None,
) -> list[GateResult]:
    """Evaluate CP-14's acceptance list against an aggregated benchmark."""
    gates: list[GateResult] = []
    completed = [row for row in aggregated if row.get("folds_ok")]

    # Provenance first, because it conditions every gate below it. A model gate
    # passing on the wrong split is not evidence that CP-14 passed, and reading
    # a report top-down should make that impossible to miss.
    if evidence is not None:
        grade = str(evidence.get("grade"))
        gates.append(GateResult(
            "Run uses CP-14's documented split (train 2022-2024 / validate 2025 / "
            "test 2026 excluding British GP, grouped by battle_id)",
            bool(evidence.get("is_cp14_acceptance_run")),
            f"evidence grade {grade}"
            + ("; this run may be quoted as a CP-14 pass"
               if grade == "FULL" else
               "; the gates below are real measurements on a leakage-safe split, but "
               "this run does NOT satisfy CP-14. "
               + " ".join(str(r) for r in evidence.get("reasons", []))),
        ))

    gates.append(GateResult(
        "All benchmark cells produce an artifact with a locked feature schema",
        len(completed) == expected_cells,
        f"{len(completed)} of {expected_cells} (checkpoint x family) cells scored a test split",
    ))

    beaten = [
        row for row in completed
        if row.get("brier") is not None and row.get("brier_skill_score") is not None
        and row["brier_skill_score"] <= 0
    ]
    gates.append(GateResult(
        "Every model beats a constant-base-rate predictor on Brier",
        not beaten,
        "all cells positive on Brier skill" if not beaten else
        "; ".join(f"{r['checkpoint']}/{r['family']} skill={r['brier_skill_score']:.4f}"
                  for r in beaten),
    ))

    details: list[str] = []
    passed: bool | None = True
    for checkpoint, rows in _by_checkpoint(completed).items():
        scores = {r["family"]: r.get("log_loss") for r in rows}
        baseline = scores.get("logistic")
        trees = {f: v for f, v in scores.items()
                 if f in ("lightgbm", "xgboost", "catboost") and v is not None}
        if baseline is None or not trees:
            passed = None if passed else passed
            details.append(f"{checkpoint}: logistic or every tree family missing")
            continue
        losers = [f for f, v in trees.items() if v >= baseline]
        if losers:
            passed = False
            details.append(
                f"{checkpoint}: {', '.join(sorted(losers))} did not beat logistic "
                f"(log loss {baseline:.4f})"
            )
        else:
            details.append(f"{checkpoint}: every tree family beat logistic {baseline:.4f}")
    gates.append(GateResult(
        "LightGBM / XGBoost / CatBoost beat logistic regression on log loss",
        passed,
        # A failure here is about the features, not the learners: three very
        # different boosters all failing to beat a linear baseline means the
        # signal is close to linear, or close to absent.
        " | ".join(details) + ". If not, the features are weak, not the models.",
    ))

    grouped = _by_checkpoint(completed)
    best = {
        checkpoint: min((r["brier"] for r in rows if r.get("brier") is not None), default=None)
        for checkpoint, rows in grouped.items()
    }
    detection = best.get("DETECTION")
    later = [(c, v) for c, v in best.items() if c in ("ACTIVATION", "BRAKING") and v is not None]
    if detection is None or not later:
        gates.append(GateResult(
            "ACTIVATION and BRAKING outperform DETECTION", None,
            "not assessable: a checkpoint produced no scored cell",
        ))
    else:
        worse = [c for c, v in later if v > detection]
        gates.append(GateResult(
            "ACTIVATION and BRAKING outperform DETECTION (they see more)",
            not worse,
            f"best Brier per checkpoint: "
            + ", ".join(f"{c}={v:.4f}" for c, v in sorted(best.items()) if v is not None)
            + ("" if not worse else
               f". {', '.join(worse)} did not beat DETECTION -- suspect leakage into the "
               "DETECTION matrix before concluding anything about the models."),
        ))

    noisy = [
        row for row in completed
        if row.get("brier_std") is not None and row["brier_std"] > BRIER_STD_CEILING
    ]
    multi_fold = [row for row in completed if (row.get("folds_ok") or 0) > 1]
    if not multi_fold:
        gates.append(GateResult(
            f"Leave-one-track-out Brier std below {BRIER_STD_CEILING}", None,
            "single-fold design; cross-fold variance is not defined",
        ))
    else:
        gates.append(GateResult(
            f"Leave-one-track-out Brier std below {BRIER_STD_CEILING}",
            not noisy,
            "all cells within the ceiling" if not noisy else
            "; ".join(f"{r['checkpoint']}/{r['family']} std={r['brier_std']:.4f}" for r in noisy)
            + ". High variance means the model is memorising circuits.",
        ))
    return gates


def _fmt(value: Any, places: int = 4) -> str:
    if value is None:
        return "--"
    if isinstance(value, float):
        return f"{value:.{places}f}"
    return str(value)


def identity_comparison(
    without: Sequence[Mapping[str, Any]], with_identity: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Per-cell Brier delta from adding driver identity (section 17).

    Negative delta means identity helped. A *large* gain is not good news: if
    knowing which drivers are involved carries most of the signal, the model has
    learned who tends to overtake whom rather than when an overtake is on, and it
    will not transfer to a driver line-up it has not seen.
    """
    keyed = {(r["checkpoint"], r["family"]): r for r in with_identity}
    out: list[dict[str, Any]] = []
    for base in without:
        other = keyed.get((base["checkpoint"], base["family"]))
        if other is None or base.get("brier") is None or other.get("brier") is None:
            continue
        out.append({
            "checkpoint": base["checkpoint"],
            "family": base["family"],
            "brier_without": base["brier"],
            "brier_with": other["brier"],
            "delta": other["brier"] - base["brier"],
            "roc_auc_without": base.get("roc_auc"),
            "roc_auc_with": other.get("roc_auc"),
        })
    return out


def render_report(
    *,
    run: Mapping[str, Any],
    dataset: Mapping[str, Any],
    split: Mapping[str, Any],
    split_summary: Sequence[Mapping[str, Any]],
    aggregated: Sequence[Mapping[str, Any]],
    gates: Sequence[Mapping[str, Any]],
    hardware: Mapping[str, Any],
    failures: Sequence[Mapping[str, Any]] = (),
    identity: Sequence[Mapping[str, Any]] = (),
    evidence: Mapping[str, Any] | None = None,
) -> str:
    """Render ``artifacts/validation/pass_model_report.md``."""
    lines: list[str] = [
        "# Pass-model benchmark (M10, CP-14)",
        "",
        f"Generated {run.get('created_utc')} at commit `{run.get('git_commit')}`.",
        "",
    ]

    # The standing of the whole document, before any number in it. A reader who
    # stops after one paragraph must not come away thinking CP-14 passed.
    if evidence is not None:
        grade = str(evidence.get("grade"))
        if evidence.get("is_cp14_acceptance_run"):
            lines += [
                f"**Evidence grade: {grade}.** This run uses CP-14's documented split "
                "and its numbers may be quoted as a CP-14 result.",
                "",
            ]
        else:
            lines += [
                f"> **Evidence grade: {grade} — this is NOT a CP-14 pass.**",
                ">",
                "> The measurements below are real and the split is leakage-safe, but "
                "the run does not meet CP-14's acceptance conditions:",
                ">",
            ]
            lines += [f"> - {reason}" for reason in evidence.get("reasons", [])]
            lines += [
                ">",
                f"> Split unit `{evidence.get('split_unit')}`, design "
                f"`{evidence.get('design')}`, years present "
                f"{evidence.get('years_present')}. CP-14 requires train "
                f"{evidence.get('train_years_required')}, validation "
                f"`{evidence.get('validation_year_required')}`, test "
                f"`{evidence.get('test_year_required')}` excluding the British Grand Prix.",
                "",
            ]

    lines += [
        "| Run | Value |",
        "|---|---|",
        f"| Seed | {run.get('seed')} |",
        f"| Checkpoints | {', '.join(run.get('checkpoints', []))} |",
        f"| Families requested | {', '.join(run.get('families', []))} |",
        f"| Families unavailable | {run.get('families_unavailable') or 'none'} |",
        f"| Driver identity in features | {run.get('include_identity')} |",
        f"| Deterministic fits | {run.get('deterministic')} |",
        f"| Label definition | {dataset.get('label_definition')} |",
        "",
        "## Dataset",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Rows | {dataset.get('rows')} |",
        f"| Opportunities | {dataset.get('opportunities')} |",
        f"| Events | {dataset.get('events')} |",
        f"| Years | {', '.join(map(str, dataset.get('years', [])))} |",
        f"| Labelled opportunities | {dataset.get('labelled_opportunities')} |",
        f"| Unlabelled (retirement or truncated session) | {dataset.get('unlabelled_opportunities')} |",
        f"| Base rate | {_fmt(dataset.get('base_rate'))} |",
        f"| British GP rows (frozen holdout) | {dataset.get('holdout_rows')} |",
        "",
        "## Splits",
        "",
        f"Design **{split.get('design')}**, split unit **{split.get('split_unit')}**, "
        f"seed {split.get('seed')}, {split.get('n_folds')} fold(s). "
        f"C9 assignment `{(split.get('c9_manifest') or {}).get('assignment_version')}`.",
        "",
    ]
    for note in split.get("notes", []):
        lines.append(f"- {note}")
    lines += [
        "",
        "| Fold | Train rows | Train +ve | Val rows | Test rows | Test +ve | Notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in split_summary:
        lines.append(
            f"| {row['fold']} | {row['train_labelled']} | {row['train_positive']} | "
            f"{row['validation_labelled']} | {row['test_labelled']} | {row['test_positive']} | "
            f"{row.get('notes', '')} |"
        )

    lines += [
        "",
        "## Results",
        "",
        "Brier is primary. " + SELECTION_RULE,
        "",
    ]
    for checkpoint in run.get("checkpoints", []):
        rows = [r for r in aggregated if r["checkpoint"] == checkpoint]
        if not rows:
            continue
        ranked = rank_results(rows)
        lines += [
            f"### {checkpoint}",
            "",
            "| Rank | Family | N | +ve | Brier | Brier std | Skill | Log loss | ECE | ROC-AUC | PR-AUC | Folds | s |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for position, row in enumerate(ranked, start=1):
            lines.append(
                f"| {position} | {row['family']} | {row.get('n', '--')} | "
                f"{row.get('n_positive', '--')} | {_fmt(row.get('brier'))} | "
                f"{_fmt(row.get('brier_std'))} | {_fmt(row.get('brier_skill_score'))} | "
                f"{_fmt(row.get('log_loss'))} | {_fmt(row.get('ece'))} | "
                f"{_fmt(row.get('roc_auc'))} | {_fmt(row.get('pr_auc'))} | "
                f"{row.get('folds_ok')}/{row.get('folds')} | {_fmt(row.get('seconds'), 1)} |"
            )
        winner = ranked[0] if ranked else None
        if winner:
            lines += [
                "",
                f"**Selected: {winner['family']}** "
                f"(Brier {_fmt(winner.get('brier'))}, ECE {_fmt(winner.get('ece'))}, "
                f"ROC-AUC {_fmt(winner.get('roc_auc'))}).",
                "",
            ]

    lines += ["## CP-14 acceptance gates", "", "| Gate | Result | Detail |", "|---|---|---|"]
    for gate in gates:
        symbol = {True: "PASS", False: "**FAIL**", None: "n/a"}[gate["passed"]]
        lines.append(f"| {gate['gate']} | {symbol} | {gate['detail']} |")

    if identity:
        lines += [
            "",
            "## Driver identity (section 17)",
            "",
            "Negative delta means identity improved Brier. A large gain is not good news:",
            "it means the model is learning who tends to overtake whom rather than when an",
            "overtake is on, and it will not transfer to an unseen line-up.",
            "",
            "| Checkpoint | Family | Brier without | Brier with | Delta | ROC-AUC without | ROC-AUC with |",
            "|---|---|---|---|---|---|---|",
        ]
        for row in identity:
            lines.append(
                f"| {row['checkpoint']} | {row['family']} | {_fmt(row['brier_without'])} | "
                f"{_fmt(row['brier_with'])} | {_fmt(row['delta'])} | "
                f"{_fmt(row.get('roc_auc_without'))} | {_fmt(row.get('roc_auc_with'))} |"
            )

    if failures:
        lines += ["", "## Cells that did not complete", "",
                  "| Checkpoint | Family | Fold | Error |", "|---|---|---|---|"]
        for row in failures:
            lines.append(
                f"| {row['checkpoint']} | {row['family']} | {row['fold']} | {row['error']} |"
            )

    lines += [
        "",
        "## Hardware",
        "",
        f"{hardware.get('workers')} concurrent fit(s), {hardware.get('threads_per_fit')} "
        f"thread(s) each; process pool: {hardware.get('use_process_pool')}.",
        "",
    ]
    for note in hardware.get("rationale", []):
        lines.append(f"- {note}")

    lines += [
        "",
        "## Reading this report",
        "",
        "- Calibration decides selection, not ROC-AUC. The DP consumes probabilities, so the",
        "  difference between 20% and 80% matters even where the ranking is unchanged.",
        "- Tree probabilities are expected to be miscalibrated here. That is CP-15's job,",
        "  and tuning it away at this stage would hide what CP-15 needs to measure.",
        "- Every metric is reported against its own N. Folds differ in size by more than an",
        "  order of magnitude, so a mean across folds is not a mean across opportunities.",
        "",
    ]
    return "\n".join(lines) + "\n"
