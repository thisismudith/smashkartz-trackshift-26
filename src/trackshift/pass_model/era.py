"""Regulation-era handling for the pass model (AGENTS.md section 41).

Deferred, not a checkpoint. CP-17 was reassigned to pass-model fine-tuning
(see ``tuning.py``) because this comparison needs two eras and the
opportunity table holds one. The implementation is kept and tested so it is
ready when 2022-2025 opportunities exist; until then every strategy but the
2026-only baseline reports blocked, with its reason.

The question is narrow and empirical: should 2022-2025 DRS-era data inform the
2026 pass model, and if so how? Section 41 warns that DRS and Overtake are
different mechanisms, so the answer may well be "it should not". That is a
legitimate finding and this module is built to be able to report it.

Five strategies, plus the baseline that makes them meaningful:

``era_feature``
    One model over both eras with ``regulation_era`` as a categorical.
``historical_pretraining``
    Fit on 2022-2025, then continue training on 2026.
``recalibration``
    Historical model, calibrator refit on 2026 only.
``domain_weighting``
    One model, 2026 rows weighted 3-5x against historical at 1x.
``separate_models``
    Independent historical and 2026 models, compared.
``modern_only`` (the baseline)
    2026 only. **If this wins, historical data is not helping**, and section 41
    says to record that plainly rather than forcing the historical rows in.

Two things this module refuses to do quietly.

**It will not pretend an era comparison is possible without two eras.** Every
strategy except ``modern_only`` needs historical rows. With a 2026-only table
the comparison is not "inconclusive", it is unrunnable, and
:func:`assess_feasibility` says so per strategy rather than returning a table of
identical numbers that look like a result.

**It will not compare strategies on different sample sizes without saying so.**
The historical-era training set is far larger than the 2026-only one, so a
strategy that wins partly on volume must report N alongside its metric. Section
34 forbids selecting on sophistication; selecting on an unstated sample-size
advantage is the same mistake wearing different clothes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "ERA_SCHEMA_VERSION",
    "STRATEGIES",
    "MODERN_YEAR",
    "HISTORICAL_YEARS",
    "DEFAULT_MODERN_WEIGHT",
    "EraError",
    "EraStrategy",
    "StrategyFeasibility",
    "assess_feasibility",
    "domain_weights",
    "era_of",
    "rank_strategies",
    "split_by_era",
]

ERA_SCHEMA_VERSION = "m13_era_comparison_v1"

MODERN_YEAR = "2026"
HISTORICAL_YEARS: tuple[str, ...] = ("2022", "2023", "2024", "2025")

#: Section 41 suggests 3-5x. The midpoint is the default; the comparison sweeps
#: it, because a strategy whose verdict flips between 3 and 5 has not earned one.
DEFAULT_MODERN_WEIGHT = 4.0


class EraError(ValueError):
    """The era comparison cannot be run as asked."""


@dataclass(frozen=True)
class EraStrategy:
    """One section 41 strategy and what it needs to be runnable."""

    name: str
    description: str
    needs_historical: bool
    #: Some strategies need a learner that can continue from a fitted state.
    needs_warm_start: bool = False
    #: Families that cannot express this strategy at all.
    unsupported_families: tuple[str, ...] = ()


STRATEGIES: tuple[EraStrategy, ...] = (
    EraStrategy(
        "modern_only",
        "2026 only. The baseline every other strategy must beat; if it wins, "
        "historical data is not helping and section 41 says to say so.",
        needs_historical=False,
    ),
    EraStrategy(
        "era_feature",
        "One model over both eras with regulation_era as a categorical feature.",
        needs_historical=True,
    ),
    EraStrategy(
        "domain_weighting",
        "One model over both eras, 2026 rows weighted against historical at 1x.",
        needs_historical=True,
        unsupported_families=("mlp",),   # sklearn's MLP takes no sample_weight
    ),
    EraStrategy(
        "separate_models",
        "Independent historical and 2026 models, scored on the same 2026 test.",
        needs_historical=True,
    ),
    EraStrategy(
        "recalibration",
        "Historical model, calibrator refit on 2026 only (CP-15's machinery).",
        needs_historical=True,
    ),
    EraStrategy(
        "historical_pretraining",
        "Fit on 2022-2025, then continue training on 2026.",
        needs_historical=True,
        needs_warm_start=True,
        unsupported_families=("logistic",),   # no incremental fit in lbfgs
    ),
)


@dataclass
class StrategyFeasibility:
    """Whether one strategy can actually be run, and what is missing if not."""

    strategy: str
    runnable: bool
    blockers: list[str] = field(default_factory=list)
    families: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy, "runnable": self.runnable,
                "blockers": list(self.blockers), "families": list(self.families)}


def era_of(year: Any) -> str:
    """``"modern"`` for 2026, ``"historical"`` for 2022-2025, else ``"unknown"``.

    Derived from the year rather than read from ``regulation_era`` on the row:
    the column is populated by the builder and a 2026-only build hard-codes it,
    so trusting it would make every row look modern regardless of its year.
    """
    text = str(year).strip()
    if text == MODERN_YEAR:
        return "modern"
    if text in HISTORICAL_YEARS:
        return "historical"
    return "unknown"


def split_by_era(frame) -> dict[str, Any]:
    """``{"modern": rows, "historical": rows, "unknown": rows}``."""
    if "year" not in frame.columns:
        raise EraError("frame has no 'year' column; the era split is derived from it")
    era = frame["year"].map(era_of)
    return {name: frame[era == name] for name in ("modern", "historical", "unknown")}


def assess_feasibility(frame, families: Sequence[str],
                       strategies: Sequence[EraStrategy] = STRATEGIES
                       ) -> list[StrategyFeasibility]:
    """Which strategies this table can actually support, and why not otherwise.

    Called before anything is fitted. A strategy that cannot run is reported as
    blocked with its reason, never as a row of numbers that happen to equal the
    baseline's -- which is what running ``era_feature`` on a single-era table
    would silently produce.
    """
    parts = split_by_era(frame)
    has_historical = len(parts["historical"]) > 0
    modern_rows = len(parts["modern"])

    out: list[StrategyFeasibility] = []
    for strategy in strategies:
        blockers: list[str] = []
        if strategy.needs_historical and not has_historical:
            blockers.append(
                f"needs 2022-2025 rows; the table holds none. Years present: "
                f"{sorted({str(v) for v in frame['year'].unique()})}")
        if not modern_rows:
            blockers.append("needs 2026 rows to test against; the table holds none")
        usable = [f for f in families if f not in strategy.unsupported_families]
        if not usable:
            blockers.append(
                f"every requested family is unsupported for this strategy "
                f"(unsupported: {list(strategy.unsupported_families)})")
        out.append(StrategyFeasibility(
            strategy=strategy.name, runnable=not blockers,
            blockers=blockers, families=usable))
    return out


def domain_weights(frame, modern_weight: float = DEFAULT_MODERN_WEIGHT):
    """Per-row weights: ``modern_weight`` for 2026, 1.0 for historical.

    Returned as a plain array so the caller passes it straight to
    ``PassModel.fit``. Rows of unknown era get 1.0 rather than being dropped:
    dropping them here would silently change the training set between this
    strategy and the others, and the comparison would no longer be like-for-like.
    """
    import numpy as np

    if modern_weight <= 0:
        raise EraError(f"modern_weight must be positive, got {modern_weight}")
    era = frame["year"].map(era_of)
    return np.where(era.values == "modern", float(modern_weight), 1.0)


def rank_strategies(results: Sequence[Mapping[str, Any]], *,
                    metric: str = "brier") -> list[dict[str, Any]]:
    """Aggregate per strategy, then order by the primary metric.

    Aggregated across folds and checkpoints first, because a per-fold ranking is
    not a ranking of strategies -- it lists the same strategy once per fold and
    the top row is then the easiest fold rather than the best approach.

    Section 26 selects on calibration, so the metric is Brier and lower wins.
    ``n_train`` travels with every row because the strategies do not train on the
    same amount of data: a win bought with four extra seasons of rows is a
    different claim from a win on equal footing, and the reader must be able to
    tell them apart without leaving the table.
    """
    by_strategy: dict[str, list[Mapping[str, Any]]] = {}
    for row in results:
        if row.get("ok") and row.get(metric) is not None:
            by_strategy.setdefault(str(row["strategy"]), []).append(row)

    def mean(rows, key):
        values = [float(r[key]) for r in rows if r.get(key) is not None]
        return round(sum(values) / len(values), 6) if values else None

    scored: list[dict[str, Any]] = []
    for strategy, rows in by_strategy.items():
        entry: dict[str, Any] = {
            "strategy": strategy,
            "folds": len(rows),
            "n_train_modern": int(mean(rows, "n_train_modern") or 0),
            "n_train_historical": int(mean(rows, "n_train_historical") or 0),
            "n_test": int(sum(int(r.get("n_test") or 0) for r in rows)),
        }
        for key in (metric, "log_loss", "roc_auc", "pr_auc", "ece"):
            entry[key] = mean(rows, key)
        # Spread across folds, so a strategy that wins on average while being
        # wildly unstable cannot hide behind its mean.
        values = [float(r[metric]) for r in rows]
        entry[f"{metric}_std"] = (
            round((sum((v - entry[metric]) ** 2 for v in values) / (len(values) - 1))
                  ** 0.5, 6) if len(values) > 1 else None)
        scored.append(entry)

    scored.sort(key=lambda r: float(r[metric]))
    baseline = next((r for r in scored if r["strategy"] == "modern_only"), None)
    for rank, row in enumerate(scored, start=1):
        row["rank"] = rank
        if baseline is not None and baseline.get(metric) is not None:
            row["delta_vs_modern_only"] = round(
                float(row[metric]) - float(baseline[metric]), 6)
            row["beats_modern_only"] = row["delta_vs_modern_only"] < 0
    return scored
