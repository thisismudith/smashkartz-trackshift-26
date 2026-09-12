"""CP-08 regulation-era comparison and held-out evidence metrics.

The harness is deliberately evidence driven. It never fits a model and does
not turn synthetic recovery into regulation-era evidence. Callers pass the
probabilities emitted by the versioned C10 artifact together with the
held-out next observation and a causal perturbation posterior.
"""
from __future__ import annotations

from math import isfinite, log
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ERA_SCHEMA_VERSION = "m13_rival_era_comparison_v2"
STRATEGIES = ("2026_only", "historical_pretrain_2026_recalibration", "era_feature", "domain_weighting", "separate_models")
STABILITY_DEFINITION = ("mean max_z |p_C10(z|x)-p_C10(z|x+delta)| over held-out prefixes; "
                        "delta is a deterministic perturbation of one causal M08 feature only")


def _is_british(row: Mapping[str, Any]) -> bool:
    return " ".join(str(row.get("event", "")).replace("_", " ").lower().split()) == "british grand prix"


def materialise_historical_m08(*, processed_root: str | Path, years: Sequence[int] = (2022, 2023, 2024, 2025)) -> dict[str, Any]:
    """Audit whether versioned historical M08 partitions exist (read-only)."""
    root = Path(processed_root)
    found = sorted(str(path) for year in years for path in root.glob(f"**/year={year}/**/rival_state_features.parquet"))
    present_years = sorted({year for year in years if any(f"year={year}" in path for path in found)})
    complete = all(year in present_years for year in years)
    reason = None if complete else "historical M08 materialisation for 2022-2025 is unavailable or incomplete; run C7/C8/M06/C9 workflow first"
    return {"schema_version": ERA_SCHEMA_VERSION, "status": "READY" if found else "BLOCKED", "complete": complete,
            "years": list(years), "present_years": present_years, "partitions": found,
            "historical_m08_rows": None, "reason": reason}


def _coverage(rows: Sequence[Mapping[str, Any]]) -> tuple[list[str], list[int]]:
    events = sorted({str(row.get("event")) for row in rows if row.get("event") is not None})
    years: list[int] = []
    for row in rows:
        try:
            year = int(row["year"])
        except (KeyError, TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    return events, sorted(years)


def _versions(rows: Sequence[Mapping[str, Any]], key: str) -> list[str]:
    return sorted({str(row[key]) for row in rows if row.get(key) is not None})


def _prediction_posterior(item: Mapping[str, Any], key: str) -> dict[str, float] | None:
    value: Any = item.get(key)
    if value is None and key == "posterior":
        value = item.get("p", item.get("c10_probability"))
    if value is None and isinstance(item.get("c10"), Mapping):
        value = item["c10"].get("p")
    if not isinstance(value, Mapping) or not value:
        return None
    result = {str(state): float(probability) for state, probability in value.items()}
    if any(not isfinite(probability) or probability < 0.0 for probability in result.values()):
        raise ValueError("C10 posterior contains a non-finite or negative probability")
    total = sum(result.values())
    if not isfinite(total) or total <= 0.0 or abs(total - 1.0) > 1e-6:
        raise ValueError("C10 posterior must be finite and sum to one")
    return result


def _log_likelihood(item: Mapping[str, Any]) -> float | None:
    value = item.get("observation_log_likelihood", item.get("next_observation_log_likelihood"))
    if value is not None:
        result = float(value)
        if not isfinite(result):
            raise ValueError("C10 observation log likelihood is not finite")
        return result
    probability = item.get("next_observation_probability")
    if probability is None:
        return None
    probability = float(probability)
    if not isfinite(probability) or probability <= 0.0 or probability > 1.0:
        raise ValueError("next_observation_probability must be in (0, 1]")
    return log(probability)


def observation_predictive_nll(evidence: Iterable[Mapping[str, Any]]) -> float | None:
    """Return ``-mean(log q_t)`` for observed next-segment C10 likelihoods."""
    values = [_log_likelihood(item) for item in evidence]
    values = [value for value in values if value is not None]
    return -sum(values) / len(values) if values else None


def posterior_stability(evidence: Iterable[Mapping[str, Any]]) -> dict[str, float | int | None]:
    """Measure causal posterior sensitivity using mean/max absolute deltas."""
    deltas: list[float] = []
    for item in evidence:
        posterior = _prediction_posterior(item, "posterior")
        perturbed = _prediction_posterior(item, "perturbed_posterior")
        if posterior is None or perturbed is None:
            continue
        if set(posterior) != set(perturbed):
            raise ValueError("C10 baseline and perturbed posteriors have different state spaces")
        deltas.append(max(abs(posterior[state] - perturbed[state]) for state in posterior))
    return {"n": len(deltas), "mean_max_abs_delta": sum(deltas) / len(deltas) if deltas else None,
            "max_abs_delta": max(deltas) if deltas else None}

def _missing_predictive_evidence(metrics: Mapping[str, Mapping[str, Any]]) -> bool:
    """Return true until every strategy has real held-out evidence.

    Historical rows alone do not constitute a regulation-era comparison.  The
    development harness currently leaves likelihood and stability uncomputed;
    reporting ``COMPLETE`` in that state would allow a checkpoint to be ticked
    without satisfying its written gates.
    """
    return any(
        strategy_metrics.get("mean_nll") is None
        or strategy_metrics.get("stability") is None
        for strategy_metrics in metrics.values()
    )


def evaluate_era_strategies(
    rows: Iterable[Mapping[str, Any]],
    *,
    split_version: str,
    rule_configuration_version: str,
    historical_rows: Iterable[Mapping[str, Any]] | None = None,
    materialisation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare strategies when historical rows are supplied; otherwise block honestly."""
    current = [dict(row) for row in rows]
    historical = [dict(row) for row in (historical_rows or [])]
    all_rows = [*current, *historical]
    if any(_is_british(row) for row in all_rows):
        raise ValueError("British GP rows cannot enter CP-08 training, calibration, or evaluation")
    current = [row for row in current if int(row.get("year", 2026)) == 2026]
    historical = [row for row in historical if int(row.get("year", 0)) < 2026]
    current_historical_drs_seen = any(
        row.get(field) is not None for row in current for field in ("historical_drs_eligible", "historical_drs_open")
    )
    historical_2026_state_seen = any(
        row.get(field) is not None for row in historical for field in ("overtake_eligible", "overtake_state")
    )
    for row in current:
        row["era"] = "2026"
        row.pop("historical_drs_eligible", None)
        row.pop("historical_drs_open", None)
    for row in historical:
        row["era"] = "drs_era"
        row["rule_configuration_version"] = row.get("rule_configuration_version", "historical_drs")
        row.pop("overtake_eligible", None)
        row.pop("overtake_state", None)
    audit = dict(materialisation or {})
    blocked_reason = audit.get("reason") if audit.get("status") == "BLOCKED" else None
    if not historical:
        blocked_reason = blocked_reason or "historical M08 materialisation for 2022-2025 is unavailable"
    metrics = {strategy: _metrics(current if strategy == "2026_only" else [*current, *historical]) for strategy in STRATEGIES}
    if not blocked_reason and _missing_predictive_evidence(metrics):
        blocked_reason = "held-out predictive likelihood and stability evidence is not materialised by the era harness"
    return {
        "schema_version": ERA_SCHEMA_VERSION,
        "split_version": split_version,
        "rule_configuration_version": rule_configuration_version,
        "held_out_2026_n": len(current), "historical_n": len(historical),
        "strategies": list(STRATEGIES), "metrics": metrics,
        "selected": "2026_only",
        "status": "BLOCKED" if blocked_reason else "COMPLETE",
        "reason": blocked_reason or "selected 2026-only unless held-out predictive evidence supports historical data",
        "historical_drs_is_not_2026_overtake": True,
        "british_gp_used_for_training_or_calibration": False,
        "cp08_next_blocker": "Historical M08 materialisation for 2022-2025—not another 2026 benchmark." if blocked_reason else None,
    }


__all__ = ["ERA_SCHEMA_VERSION", "STRATEGIES", "materialise_historical_m08", "evaluate_era_strategies"]
