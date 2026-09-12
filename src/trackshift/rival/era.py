"""CP-08 regulation-era comparison harness.

Historical M08 is intentionally a separate input. The harness never maps
historical DRS fields onto 2026 Overtake state and fails closed when the
2022–2025 materialisation is absent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ERA_SCHEMA_VERSION = "m13_rival_era_comparison_v1"
STRATEGIES = ("2026_only", "historical_pretrain_2026_recalibration", "era_feature", "domain_weighting", "separate_models")


def _is_british(row: Mapping[str, Any]) -> bool:
    return " ".join(str(row.get("event", "")).replace("_", " ").lower().split()) == "british grand prix"


def materialise_historical_m08(*, processed_root: str | Path, years: Sequence[int] = (2022, 2023, 2024, 2025)) -> dict[str, Any]:
    """Audit whether versioned historical M08 partitions exist.

    This function is intentionally read-only. Historical M08 must be produced
    by the existing C7/C8/M06/C9 workflow before CP-08 can claim a comparison.
    """
    root = Path(processed_root)
    found = sorted(str(path) for year in years for path in root.glob(f"**/year={year}/**/rival_state_features.parquet"))
    return {"schema_version": ERA_SCHEMA_VERSION, "status": "READY" if found else "BLOCKED", "years": list(years), "partitions": found, "historical_m08_rows": None, "reason": None if found else "historical M08 materialisation for 2022-2025 is unavailable; run C7/C8/M06/C9 workflow first"}


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"n": len(rows), "mean_log_likelihood": None, "mean_nll": None, "stability": None, "calibration": "UNAVAILABLE: no real tactical-state labels", "rule_configuration_versions": sorted({str(row.get("rule_configuration_version")) for row in rows if row.get("rule_configuration_version") is not None})}


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
    if any(_is_british(row) and row.get("training") for row in [*current, *historical]):
        raise ValueError("British GP cannot enter rival training/calibration")
    current = [row for row in current if int(row.get("year", 2026)) == 2026]
    historical = [row for row in historical if int(row.get("year", 0)) < 2026]
    for row in current:
        row["era"] = "2026"
        row.pop("historical_drs_eligible", None)
        row.pop("historical_drs_open", None)
    for row in historical:
        row["era"] = "drs_era"
        row["rule_configuration_version"] = row.get("rule_configuration_version", "historical_drs")
    audit = dict(materialisation or {})
    blocked_reason = audit.get("reason") if audit.get("status") == "BLOCKED" else None
    if not historical:
        blocked_reason = blocked_reason or "historical M08 materialisation for 2022-2025 is unavailable"
    metrics = {strategy: _metrics(current if strategy == "2026_only" else [*current, *historical]) for strategy in STRATEGIES}
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
