"""Hyperparameter search for the selected pass model (M13, CP-17).

CP-14 picks a family. This tunes it. The search runs on the 2026 opportunity
table, over the same C9 battle_id splits, and reports what the tuning bought.

Three rules hold this honest, and they are the reason this module exists rather
than a loop around ``GridSearchCV``.

**The test split is never tuned on.** 2026-excluding-British-GP is CP-14's test
set. Selecting hyperparameters against it would turn the reported test score
into a training score -- the classic way a benchmark quietly stops measuring
generalisation. Every configuration is scored on the *validation* split of each
fold and the winner is scored once, afterwards, on the test split.

**ROC-AUC is reported but does not select alone.** Section 26 is explicit that
the DP consumes probabilities, so a model with slightly lower ROC-AUC and
materially better calibration wins. Tuning purely for ranking quality optimises
a metric the downstream planner never reads. The objective is therefore a
parameter, and whichever is chosen, the *other* metrics travel with every
result: a configuration that lifts ROC-AUC while degrading Brier is flagged
rather than quietly crowned.

**A gain inside fold-to-fold noise is not a gain.** Configurations are scored
across every fold and the spread is reported, so a winner that beats the
baseline by less than its own variability is marked as such.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

__all__ = [
    "TUNING_SCHEMA_VERSION",
    "OBJECTIVES",
    "SEARCH_SPACES",
    "TuningError",
    "TrialResult",
    "baseline_of",
    "expand_space",
    "rank_trials",
    "select_winner",
]

TUNING_SCHEMA_VERSION = "m13_pass_tuning_v1"

#: Metrics a search may optimise, and whether higher is better.
#: ``brier`` is the section 26 default; ``roc_auc`` is available because ranking
#: quality is sometimes what is being asked for, with the caveat above.
OBJECTIVES: dict[str, bool] = {
    "roc_auc": True,
    "pr_auc": True,
    "brier": False,
    "log_loss": False,
    "ece": False,
}

#: Search spaces, deliberately small.
#:
#: A dataset of a few thousand opportunities does not support a large grid: with
#: six folds the per-configuration standard error is already comparable to the
#: differences between neighbouring settings, so a 500-point search mostly
#: selects noise and reports it as a discovery. These grids vary the knobs that
#: govern capacity and regularisation -- the ones that actually move a small
#: tabular problem -- and leave the rest at CP-14's values.
SEARCH_SPACES: dict[str, dict[str, list[Any]]] = {
    "lightgbm": {
        "num_leaves": [15, 31, 63],
        "learning_rate": [0.02, 0.03, 0.05],
        "min_child_samples": [20, 40, 80],
        "reg_lambda": [0.0, 1.0, 5.0],
    },
    "xgboost": {
        "max_depth": [3, 5, 7],
        "learning_rate": [0.02, 0.03, 0.05],
        "min_child_weight": [1, 5, 10],
        "reg_lambda": [0.5, 1.0, 5.0],
    },
    "catboost": {
        "depth": [4, 6, 8],
        "learning_rate": [0.02, 0.03, 0.05],
        "l2_leaf_reg": [1.0, 3.0, 9.0],
    },
    "logistic": {
        "C": [0.1, 0.3, 1.0, 3.0, 10.0],
    },
    "mlp": {
        "hidden_layer_sizes": [(32,), (64, 32), (128, 64)],
        "alpha": [1e-4, 1e-3, 1e-2],
        "learning_rate_init": [5e-4, 1e-3, 5e-3],
    },
}

#: A winner must beat the baseline by more than this share of the baseline's own
#: fold-to-fold standard deviation before the gain is called real.
NOISE_MULTIPLE = 1.0


class TuningError(ValueError):
    """The search cannot be run as asked."""


@dataclass(frozen=True)
class TrialResult:
    """One configuration scored on one fold's validation split."""

    trial: int
    checkpoint: str
    family: str
    fold: str
    params: Mapping[str, Any]
    metrics: Mapping[str, Any] = field(default_factory=dict)
    n_validation: int | None = None
    ok: bool = True
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trial": self.trial, "checkpoint": self.checkpoint, "family": self.family,
            "fold": self.fold, "params": dict(self.params),
            "metrics": dict(self.metrics), "n_validation": self.n_validation,
            "ok": self.ok, "error": self.error,
        }


def expand_space(space: Mapping[str, Sequence[Any]], *, limit: int | None = None,
                 seed: int = 42) -> list[dict[str, Any]]:
    """Every combination in ``space``, optionally sampled down to ``limit``.

    Sampling is seeded and the full grid is returned in its natural order when
    it fits, so a rerun reproduces the same trials. The baseline (CP-14's own
    parameters) is not included here -- the caller adds it explicitly, so it is
    always trial zero and always comparable.
    """
    if not space:
        return []
    names = sorted(space)
    combos = [dict(zip(names, values))
              for values in itertools.product(*(list(space[n]) for n in names))]
    if limit is None or len(combos) <= limit:
        return combos
    from random import Random

    sampled = list(combos)
    Random(seed).shuffle(sampled)
    return sampled[:limit]


def _values(rows: Sequence[TrialResult], metric: str) -> list[float]:
    out = []
    for row in rows:
        value = (row.metrics or {}).get(metric)
        if value is not None and float(value) == float(value):
            out.append(float(value))
    return out


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _std(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def rank_trials(results: Sequence[TrialResult], *, objective: str,
                checkpoint: str | None = None) -> list[dict[str, Any]]:
    """Aggregate trials across folds and order by ``objective``.

    Aggregated first, because a per-fold ranking ranks folds. Every objective's
    value is carried on every row, not just the one being optimised: the whole
    point of the section 26 caveat is that the reader can see what the winner
    cost on the metrics it was not chasing.
    """
    if objective not in OBJECTIVES:
        raise TuningError(
            f"unknown objective {objective!r}; expected one of {sorted(OBJECTIVES)}")

    grouped: dict[tuple[int, str], list[TrialResult]] = {}
    for row in results:
        if not row.ok:
            continue
        if checkpoint is not None and row.checkpoint != checkpoint:
            continue
        grouped.setdefault((row.trial, row.checkpoint), []).append(row)

    scored: list[dict[str, Any]] = []
    for (trial, cp), rows in grouped.items():
        values = _values(rows, objective)
        if not values:
            continue
        entry: dict[str, Any] = {
            "trial": trial, "checkpoint": cp, "family": rows[0].family,
            "params": dict(rows[0].params), "folds": len(rows),
            "objective": objective,
            "objective_mean": round(_mean(values), 6),
            "objective_std": (round(_std(values), 6) if _std(values) is not None else None),
            "n_validation": sum(int(r.n_validation or 0) for r in rows),
        }
        # Carry every metric, not only the objective.
        for name in OBJECTIVES:
            got = _values(rows, name)
            entry[name] = round(_mean(got), 6) if got else None
        scored.append(entry)

    higher_better = OBJECTIVES[objective]
    scored.sort(key=lambda r: r["objective_mean"], reverse=higher_better)
    for rank, row in enumerate(scored, start=1):
        row["rank"] = rank
    return scored


def baseline_of(ranked: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The untuned CP-14 configuration, which the caller submits as trial 0."""
    return next((r for r in ranked if r["trial"] == 0), None)


def select_winner(ranked: Sequence[Mapping[str, Any]], *, objective: str,
                  calibration_metric: str = "brier") -> dict[str, Any]:
    """The top trial, with the two judgements that keep the result honest.

    ``gain_is_real`` — did it beat the baseline by more than the baseline's own
    fold-to-fold spread? A search over dozens of configurations will always
    produce a leader; whether that leader is distinguishable from the untuned
    model is a separate question and this answers it.

    ``calibration_regressed`` — did chasing the objective cost calibration?
    Section 26 says the DP consumes probabilities, so a ROC-AUC win paid for in
    Brier is not the improvement it appears to be. The verdict does not veto the
    winner; it makes the trade explicit so a person decides.
    """
    if not ranked:
        return {"status": "NO_TRIALS",
                "detail": "no configuration produced a scoreable fit"}

    winner = dict(ranked[0])
    baseline = baseline_of(ranked)
    out: dict[str, Any] = {
        "status": "OK",
        "objective": objective,
        "winner": winner,
        "baseline": dict(baseline) if baseline else None,
    }

    if baseline is None:
        out["status"] = "NO_BASELINE"
        out["detail"] = ("the untuned CP-14 configuration was not submitted as trial 0, "
                         "so there is nothing to say the tuning improved on")
        return out

    higher_better = OBJECTIVES[objective]
    delta = winner["objective_mean"] - baseline["objective_mean"]
    improvement = delta if higher_better else -delta
    noise = baseline.get("objective_std") or 0.0
    out["delta"] = round(delta, 6)
    out["improvement"] = round(improvement, 6)
    out["baseline_fold_std"] = round(noise, 6) if noise else None
    out["gain_is_real"] = bool(improvement > NOISE_MULTIPLE * noise) and improvement > 0

    if winner["trial"] == 0:
        out["verdict"] = "KEEP_BASELINE"
        out["detail"] = ("no configuration beat CP-14's own parameters; the defaults "
                         "are the right choice and the search says so")
    elif not out["gain_is_real"]:
        out["verdict"] = "KEEP_BASELINE"
        out["detail"] = (
            f"the best configuration improves {objective} by {improvement:+.5f}, which "
            f"is inside the baseline's own fold-to-fold spread of {noise:.5f}. A search "
            "always produces a leader; this one is not distinguishable from the untuned "
            "model, so tuning has not earned the added complexity")
    else:
        out["verdict"] = "ADOPT_TUNED"
        out["detail"] = (
            f"improves {objective} by {improvement:+.5f}, beyond the baseline's "
            f"{noise:.5f} fold-to-fold spread")

    # Calibration trade, whatever the objective was.
    base_cal = baseline.get(calibration_metric)
    win_cal = winner.get(calibration_metric)
    if base_cal is not None and win_cal is not None:
        # Every calibration metric here is lower-better.
        regressed = win_cal > base_cal
        out["calibration_metric"] = calibration_metric
        out["calibration_delta"] = round(win_cal - base_cal, 6)
        out["calibration_regressed"] = bool(regressed)
        if regressed and objective != calibration_metric:
            out["detail"] += (
                f". WARNING: {calibration_metric} worsened from {base_cal:.5f} to "
                f"{win_cal:.5f}. Section 26 selects on calibration because the DP "
                "consumes probabilities, not rankings -- a model that orders pairs "
                "better while stating their probabilities worse is not an improvement "
                "for the planner.")
    return out
