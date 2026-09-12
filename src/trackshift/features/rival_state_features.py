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
    # M06 keeps unavailable public quantities in a dedicated audit map so it
    # does not have to turn every numeric column into an object column.  Carry
    # that exact public-boundary explanation into M08 instead of replacing it
    # with a plausible value (or a less specific generic reason).
    unavailable = row.get("unavailable_quantities")
    if value is None and isinstance(unavailable, Mapping):
        quantity = unavailable.get(name)
        if isinstance(quantity, Mapping) and "value" in quantity:
            return dict(quantity)
    return _q(value, provenance, unit, reason if value is None else None)


def _defender_alignment_complete(source: Mapping[str, Any]) -> bool:
    """Do not call a missing contemporaneous defender a complete pairing.

    M06 intentionally keeps this detail in its unavailable-quantity audit
    rather than backfilling a prior/future defender.  Preserve that signal in
    M08 reliability, including old M06 artifacts that predate an explicit
    alignment-status scalar.
    """
    status = source.get("defender_alignment_status")
    if status is not None:
        return status == "DIRECT"
    unavailable = source.get("unavailable_quantities")
    quantity = unavailable.get("defender_speed_entry_mps") if isinstance(unavailable, Mapping) else None
    reason = quantity.get("reason", "") if isinstance(quantity, Mapping) else ""
    return "No contemporaneous C1 defender segment" not in str(reason)


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
        # Outcome/future fields are a hard error; source identity/coordinates
        # may exist in C8/M06 for joins but are deliberately not copied below.
        if any(any(token in str(key).lower() for token in ("outcome", "pass_result", "future")) for key in source):
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
            "tyre_degradation_delta": _public_value(source, "tyre_degradation_delta", "DERIVED", "ratio", "tyre context unavailable"),
            "wind_head_component_mps": _public_value(source, "wind_head_component_mps", "DERIVED", "m/s", "weather overlay unavailable"),
            "corner_type": source.get("corner_type"), "corner_phase": source.get("corner_phase"),
            "fuel_load_delta_kg_est": _public_value(source, "fuel_load_delta_kg_est", "INFERRED", "kg", "UNAVAILABLE_C5: no causal materialised C5 overlay"),
            "ers_energy_delta_kj_est": _public_value(source, "ers_energy_delta_kj_est", "SIMULATED", "kJ", "UNAVAILABLE_C5: no causal materialised C5 overlay"),
            "eligibility_probability": _public_value(source, "eligibility_probability", "INFERRED", None, "UNAVAILABLE_C6: no public decision-point value"),
            # C6 is a rule-derived public overlay.  Its absence is meaningful:
            # a missing Detection Line, unaligned race-control message, or an
            # unsupported state is not the same as a false eligibility flag.
            "c6_eligibility": _public_value(source, "c6_eligibility", "RULE", None, "UNAVAILABLE_C6: no public decision-point value"),
            "c9_split_assignment": dict(source.get("c9_split_assignment") or split_reference or {}),
            "reliability": {"normal_race_eligible": True, "pairwise_complete": _defender_alignment_complete(source), "provenance": "DERIVED"},
            "provenance": "DERIVED", "schema_version": RIVAL_FEATURE_SCHEMA_VERSION,
        }
        output.append(row)
    return {"schema_version": RIVAL_FEATURE_SCHEMA_VERSION, "rows": output, "excluded_rows_by_reason": dict(excluded), "split_reference": dict(split_reference or {}), "event_counts": dict(Counter(str(r.get("event")) for r in output))}
