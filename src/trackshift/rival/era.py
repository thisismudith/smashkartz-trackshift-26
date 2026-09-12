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


def _metrics(rows: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]] | None, *, split_version: str,
             rule_configuration_version: str, model_version: str | None = None,
             blocked_reason: str | None = None) -> dict[str, Any]:
    events, years = _coverage(rows)
    evidence_rows = list(evidence or [])
    if any(_is_british(item) for item in evidence_rows):
        raise ValueError("British Grand Prix rows cannot enter CP-08 evaluation evidence")
    for item in evidence_rows:
        try:
            is_2026 = int(item.get("year", 0)) == 2026
        except (TypeError, ValueError):
            is_2026 = False
        if is_2026 and any(item.get(field) is not None for field in ("historical_drs_eligible", "historical_drs_open")):
            raise ValueError("historical DRS cannot populate a 2026 Overtake feature")
    evidence_events, evidence_years = _coverage(evidence_rows)
    versions = _versions(rows, "rule_configuration_version")
    versions.extend(_versions(evidence_rows, "rule_configuration_version"))
    if rule_configuration_version not in versions:
        versions.append(str(rule_configuration_version))
    model_versions = _versions(evidence_rows, "model_version")
    if model_version:
        model_versions.append(str(model_version))
    provenances = _versions(evidence_rows, "provenance")
    reason = blocked_reason
    log_likelihoods = [_log_likelihood(item) for item in evidence_rows]
    log_likelihoods = [value for value in log_likelihoods if value is not None]
    stability_result = posterior_stability(evidence_rows)
    if evidence_rows and not log_likelihoods:
        reason = reason or "held-out C10 observation likelihood is absent"
    if evidence_rows and not stability_result["n"]:
        reason = reason or "causal perturbation posterior is absent"
    if not evidence_rows:
        reason = reason or "C10 predictive probabilities and causal perturbation posteriors are unavailable"
    return {"status": "BLOCKED" if blocked_reason else ("READY" if log_likelihoods and stability_result["n"] else "BLOCKED"),
            "reason": reason, "n": len(log_likelihoods), "row_n": len(rows),
            "mean_log_likelihood": sum(log_likelihoods) / len(log_likelihoods) if log_likelihoods else None,
            "mean_nll": -sum(log_likelihoods) / len(log_likelihoods) if log_likelihoods else None,
            "stability": stability_result["mean_max_abs_delta"], "stability_n": stability_result["n"],
            "stability_max_abs_delta": stability_result["max_abs_delta"], "stability_definition": STABILITY_DEFINITION,
            "calibration": "UNAVAILABLE: no real tactical-state labels", "event_coverage": evidence_events or events,
            "year_coverage": evidence_years or years, "c9_split_reference": str(split_version),
            "model_version": sorted(set(model_versions)) or None, "prediction_provenance": sorted(set(provenances)) or None,
            "rule_configuration_versions": sorted(set(versions))}


def _perturb_row(row: Mapping[str, Any], *, delta: float) -> dict[str, Any]:
    """Perturb one causal M08 feature without touching identity or future data."""
    result = dict(row)
    for name in ("pace_residual_delta_s", "relative_speed_to_ahead_mps", "gap_rate_ahead_s_per_s", "braking_intensity_delta"):
        if name not in result or result[name] is None:
            continue
        value = result[name]
        if isinstance(value, Mapping):
            if value.get("value") is None:
                continue
            updated = dict(value)
            updated["value"] = float(value["value"]) + delta
            result[name] = updated
        else:
            result[name] = float(value) + delta
        result["perturbation"] = {"kind": "causal_feature_delta", "field": name, "delta": delta}
        return result
    return result


def c10_prediction_evidence(model: Any, sequences: Sequence[Mapping[str, Any]], *, perturbation_delta: float = 0.05) -> list[dict[str, Any]]:
    """Materialise deterministic held-out evidence from a loaded C10 model."""
    evidence: list[dict[str, Any]] = []
    for sequence in sequences:
        rows = list(sequence.get("rows", ()))
        if not rows:
            continue
        if _is_british(sequence) or any(_is_british(row) for row in rows):
            raise ValueError("British Grand Prix rows cannot enter CP-08 evaluation evidence")
        try:
            year = int(sequence.get("year", rows[0].get("year", 0)))
        except (TypeError, ValueError):
            year = 0
        if year != 2026 or len(rows) < 2:
            continue
        index = min(5, len(rows) - 1)
        prefix = rows[:index]
        posterior_values = model.predict_distribution(prefix)
        posterior = {state: value for state, value in zip(model.states, posterior_values)}
        perturbed_rows = list(prefix)
        if perturbed_rows:
            perturbed_rows[-1] = _perturb_row(perturbed_rows[-1], delta=perturbation_delta)
        perturbed_values = model.predict_distribution(perturbed_rows)
        perturbed = {state: value for state, value in zip(model.states, perturbed_values)}
        next_row = rows[index]
        evidence.append({"battle_id": sequence.get("sequence_id", next_row.get("battle_id")),
                         "segment_index": next_row.get("segment_index"), "event": sequence.get("event", next_row.get("event")),
                         "year": year, "session": sequence.get("session", next_row.get("session")),
                         "fold_id": sequence.get("fold_id"), "c9_split_assignment": sequence.get("c9_split_assignment"),
                         "model_version": getattr(model, "model_version", None), "provenance": "INFERRED",
                         "rule_configuration_version": next_row.get("rule_configuration_version"),
                         "posterior": posterior, "perturbed_posterior": perturbed,
                         "observation_log_likelihood": model.observation_log_likelihood(next_row, posterior_values), "held_out": True})
    return evidence


def evaluate_era_strategies(rows: Iterable[Mapping[str, Any]], *, split_version: str, rule_configuration_version: str,
                            historical_rows: Iterable[Mapping[str, Any]] | None = None,
                            materialisation: Mapping[str, Any] | None = None,
                            prediction_evidence: Mapping[str, Iterable[Mapping[str, Any]]] | None = None) -> dict[str, Any]:
    """Evaluate documented strategies from real C10 evidence, failing closed."""
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
    historical_ready = bool(historical) and bool(audit.get("complete"))
    historical_reason = audit.get("reason") if audit.get("status") == "BLOCKED" else None
    if not historical_ready:
        historical_reason = historical_reason or "full historical M08 materialisation for 2022-2025 is unavailable"
    supplied = {key: list(value) for key, value in (prediction_evidence or {}).items()}
    metrics: dict[str, Any] = {}
    for strategy in STRATEGIES:
        strategy_rows = current if strategy == "2026_only" else [*current, *historical]
        strategy_block = None if strategy == "2026_only" or historical_ready else historical_reason
        metrics[strategy] = _metrics(strategy_rows, supplied.get(strategy), split_version=split_version,
                                     rule_configuration_version=rule_configuration_version, blocked_reason=strategy_block)
        if strategy != "2026_only" and strategy not in supplied:
            metrics[strategy]["status"] = "BLOCKED"
            metrics[strategy]["reason"] = strategy_block or "strategy-specific C10 evidence is unavailable"
    all_metrics_ready = all(metrics[strategy]["status"] == "READY" for strategy in STRATEGIES)
    blocked_reason = historical_reason if not historical_ready else None
    if not all_metrics_ready:
        blocked_reason = blocked_reason or "one or more strategy C10 evidence sets are unavailable"
    return {"schema_version": ERA_SCHEMA_VERSION, "split_version": split_version,
            "rule_configuration_version": rule_configuration_version, "held_out_2026_n": len(current),
            "historical_n": len(historical), "strategies": list(STRATEGIES), "metrics": metrics,
            "selected": "2026_only", "status": "COMPLETE" if all_metrics_ready and historical_ready else "BLOCKED",
            "reason": blocked_reason or "all strategy evidence is available", "historical_drs_is_not_2026_overtake": True,
            "historical_drs_fields_in_2026_overtake": False,
            "historical_rows_have_2026_overtake_state": False,
            "historical_drs_fields_removed_from_2026": current_historical_drs_seen,
            "historical_2026_state_fields_removed": historical_2026_state_seen,
            "british_gp_used_for_training_or_calibration": False,
            "tactical_state_calibration": "NOT_CLAIMED: no real tactical-state labels",
            "cp08_next_blocker": "Historical M08 materialisation for 2022-2025—not another 2026 benchmark." if blocked_reason else None}


__all__ = ["ERA_SCHEMA_VERSION", "STRATEGIES", "STABILITY_DEFINITION", "materialise_historical_m08",
           "observation_predictive_nll", "posterior_stability", "c10_prediction_evidence", "evaluate_era_strategies"]
