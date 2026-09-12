"""Strict JSON boundary for Owner A decision-time strategic state."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

REQUIRED_BLOCKS = (
    "ref",
    "energy",
    "tyre",
    "gap",
    "overtake_state",
    "race_control",
    "power_envelope",
    "rival_state",
    "uncertainty",
)
PROVENANCE_VALUES = {"OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "RULE"}


class StrategicStateValidationError(ValueError):
    """Raised when a strategic-state payload violates the public boundary."""


def _require_honest_unavailable_values(value: Any, path: str = "state") -> None:
    if isinstance(value, Mapping):
        for key in ("value", "mean"):
            if key in value and value[key] is None:
                reason = value.get("reason")
                provenance = value.get("provenance")
                if not isinstance(reason, str) or not reason.strip():
                    raise StrategicStateValidationError(
                        f"{path}.{key} is null but has no non-empty reason"
                    )
                if provenance not in PROVENANCE_VALUES:
                    raise StrategicStateValidationError(
                        f"{path}.{key} is null but has invalid provenance"
                    )
        for key, child in value.items():
            _require_honest_unavailable_values(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _require_honest_unavailable_values(child, f"{path}[{index}]")


def validate_strategic_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a detached, JSON-compatible StrategicState dictionary.

    This boundary never derives missing values. A producer must provide every
    decision-time block explicitly, including unavailable modelled values with
    a null value, reason, and provenance.
    """
    if not isinstance(payload, Mapping):
        raise StrategicStateValidationError("StrategicState must be a JSON object")

    missing = [name for name in REQUIRED_BLOCKS if name not in payload]
    if missing:
        raise StrategicStateValidationError(
            "StrategicState missing required block(s): " + ", ".join(missing)
        )

    for name in REQUIRED_BLOCKS:
        if payload[name] is None:
            raise StrategicStateValidationError(
                f"StrategicState block {name!r} must be explicit, not null"
            )

    try:
        detached = json.loads(json.dumps(dict(payload), allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise StrategicStateValidationError(
            "StrategicState must contain JSON-compatible values only"
        ) from exc

    _require_honest_unavailable_values(detached)
    return detached
