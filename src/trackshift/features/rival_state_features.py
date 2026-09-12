"""Causal M08 rival-state rows built from public C8/M06 outputs.

This module deliberately accepts already-materialised public-contract rows.
It does not read raw telemetry or reach into C2--C6 implementation modules.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

RIVAL_FEATURE_SCHEMA_VERSION = "m08_rival_state_features_v1"
FORBIDDEN_FIELDS = ("driver_number", "team", "team_colour", "x_m", "y_m", "z_m", "timestamp", "outcome", "pass_result", "future")


def _q(value: Any, provenance: str, unit: str | None, reason: str | None = None) -> dict[str, Any]:
    result = {"value": value, "provenance": provenance, "unit": unit}
    if value is None:
        result["reason"] = reason or "source unavailable"
    return result


def _public_value(row: Mapping[str, Any], name: str, provenance: str, unit: str | None, reason: str) -> dict[str, Any]:
    value = row.get(name)
    if isinstance(value, Mapping) and "value" in value:
        return dict(value)
    return _q(value, provenance, unit, reason if value is None else None)


def build_rival_state_features(rows: Iterable[Mapping[str, Any]], *, split_reference: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Produce one M08 row per eligible C8/M06 row.

    Optional C2/C5/C6/weather materialisations are joined by caller on the
    stable C8 key. Missing values remain API Quantity-compatible nulls.
    """
    output: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()
    for source in rows:
        if source.get("normal_race_model_eligible") is not True:
            excluded["NOT_NORMAL_RACE_ELIGIBLE"] += 1
            continue
        if any(any(token in str(key).lower() for token in FORBIDDEN_FIELDS) for key in source):
            raise ValueError("M08 source contains a forbidden live rival-state field")
        if source.get("battle_id") is None or source.get("segment_index") is None:
            raise ValueError("M08 requires C8 battle_id and segment_index")
        row = {
            "battle_id": source["battle_id"], "segment_index": source["segment_index"],
            "year": source.get("year"), "event": source.get("event"), "session": source.get("session"),
            "lap": source.get("lap"), "segment_id": source.get("segment_id"),
            "normal_race_model_eligible": True,
            "causal_cutoff": {"lap": source.get("lap"), "segment_id": source.get("segment_id"), "provenance": "DERIVED"},
            "relative_speed_to_ahead_mps": _public_value(source, "relative_speed_to_ahead_mps", "DERIVED", "m/s", "pairwise relative speed unavailable"),
            "gap_rate_ahead_s_per_s": _public_value(source, "gap_rate_ahead_s_per_s", "DERIVED", "s/s", "pairwise gap rate unavailable"),
            "pace_residual_delta_s": _public_value(source, "baseline_residual_delta_s", "DERIVED", "s", "UNAVAILABLE_C2: causal residual unavailable"),
            "braking_intensity_delta": _public_value(source, "braking_intensity_delta", "DERIVED", None, "pairwise braking proxy unavailable"),
            "tyre_degradation_delta_s": _public_value(source, "tyre_degradation_delta_s", "DERIVED", "s", "tyre context unavailable"),
            "wind_head_component_mps": _public_value(source, "wind_head_component_mps", "DERIVED", "m/s", "weather overlay unavailable"),
            "corner_type": source.get("corner_type"), "corner_phase": source.get("corner_phase"),
            "fuel_load_delta_kg_est": _public_value(source, "fuel_load_delta_kg_est", "INFERRED", "kg", "UNAVAILABLE_C5: no causal materialised C5 overlay"),
            "ers_energy_delta_kj_est": _public_value(source, "ers_energy_delta_kj_est", "SIMULATED", "kJ", "UNAVAILABLE_C5: no causal materialised C5 overlay"),
            "eligibility_probability": _public_value(source, "eligibility_probability", "INFERRED", None, "UNAVAILABLE_C6: no public decision-point value"),
            "reliability": {"normal_race_eligible": True, "pairwise_complete": source.get("defender_alignment_status") in {None, "DIRECT"}, "provenance": "DERIVED"},
            "provenance": "DERIVED", "schema_version": RIVAL_FEATURE_SCHEMA_VERSION,
        }
        output.append(row)
    return {"schema_version": RIVAL_FEATURE_SCHEMA_VERSION, "rows": output, "excluded_rows_by_reason": dict(excluded), "split_reference": dict(split_reference or {}), "event_counts": dict(Counter(str(r.get("event")) for r in output))}
