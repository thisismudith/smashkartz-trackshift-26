"""Validation for raw TracingInsights telemetry laps."""
from __future__ import annotations
from dataclasses import dataclass, field
from math import isfinite
from typing import Any

REQUIRED_FIELDS = ("time", "distance", "speed", "throttle", "brake", "drs", "gear")
QUALITY_FIELDS = ("DriverAhead", "DistanceToDriverAhead", "x", "y", "z", "acc_x", "acc_y", "acc_z")

@dataclass
class ValidationResult:
    status: str
    rejection_code: str = ""
    rejection_reason: str = ""
    source_rows: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "None": return None
    try: result = float(value)
    except (TypeError, ValueError): return None
    return result if isfinite(result) else None

def _reject(code: str, reason: str, rows: int = 0, metrics: dict[str, Any] | None = None) -> ValidationResult:
    return ValidationResult("REJECTED", code, reason, rows, metrics or {})

def validate_telemetry_object(payload: Any) -> ValidationResult:
    """Validate parsed JSON without modifying or repairing the raw arrays."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tel"), dict):
        return _reject("MISSING_TEL_OBJECT", "payload does not contain a tel object")
    tel = payload["tel"]
    for name in REQUIRED_FIELDS:
        if not isinstance(tel.get(name), list): return _reject("MISSING_REQUIRED_FIELD", f"required array field is missing: {name}")
    rows = len(tel["time"])
    for name in REQUIRED_FIELDS:
        if len(tel[name]) != rows: return _reject("ARRAY_LENGTH_MISMATCH", f"{name} has {len(tel[name])} values; expected {rows}", rows)
    if rows < 2: return _reject("INSUFFICIENT_VALID_SAMPLES", "at least two samples are required", rows)
    time_values = [_number(value) for value in tel["time"]]
    if any(value is None for value in time_values): return _reject("NONFINITE_TIME", "time contains a non-finite or non-numeric value", rows)
    if any(b < a for a, b in zip(time_values, time_values[1:])): return _reject("NON_MONOTONIC_TIME", "time decreases between source samples", rows)
    distance_values = [_number(value) for value in tel["distance"]]
    if any(value is None for value in distance_values): return _reject("NONFINITE_DISTANCE", "distance contains a non-finite or non-numeric value", rows)
    if any(b < a for a, b in zip(distance_values, distance_values[1:])): return _reject("NON_MONOTONIC_DISTANCE", "distance decreases between source samples", rows)
    metrics: dict[str, Any] = {"source_time_min": min(time_values), "source_time_max": max(time_values), "source_distance_min": min(distance_values), "source_distance_max": max(distance_values)}
    for name in QUALITY_FIELDS:
        values = tel.get(name)
        metrics[f"missing_rate_{name}"] = None if not isinstance(values, list) or rows == 0 else sum(value is None or value == "None" for value in values) / rows
    for name in ("speed", "throttle", "brake", "drs", "distance"):
        numeric = [value for value in (_number(value) for value in tel[name]) if value is not None]
        metrics[f"{name}_min"] = min(numeric) if numeric else None; metrics[f"{name}_max"] = max(numeric) if numeric else None
    throttle = [_number(value) for value in tel["throttle"]]
    metrics["throttle_above_100_count"] = sum(value is not None and value > 100 for value in throttle)
    return ValidationResult("ACCEPTED", source_rows=rows, metrics=metrics)
