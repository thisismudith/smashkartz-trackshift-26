"""Deterministic, leakage-safe C9 split assignments."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from random import Random
from typing import Any, Iterable, Mapping

SPLIT_SCHEMA_VERSION = "c9_split_assignments_v1"
SUPPORTED_UNITS = {"battle_id", "event", "track", "year"}
_DEMO_EVENT = "british grand prix"


def _normalise_event(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").lower().split())


def _is_british_grand_prix(row: Mapping[str, Any]) -> bool:
    return _normalise_event(row.get("event")) == _DEMO_EVENT


def _slug(value: Any) -> str:
    return "_".join(str(value).strip().lower().split())


def _parse_design(design: str, group_count: int) -> tuple[str, int | None]:
    value = design.strip().lower()
    if value in {"kfold", "group_kfold"}:
        return "kfold", min(5, group_count)
    if value.startswith("kfold:") or value.startswith("group_kfold:"):
        _, count = value.split(":", 1)
        try:
            folds = int(count)
        except ValueError as exc:
            raise ValueError("kfold design must use kfold:<positive integer>") from exc
        return "kfold", folds
    if value == "leave_one_event_out":
        return value, None
    if value == "year_forward":
        return value, None
    raise ValueError("unsupported design; use kfold[:N], leave_one_event_out, or year_forward")


def assert_training_or_calibration_eligible(rows: Iterable[Mapping[str, Any]], purpose: str) -> None:
    """Reject the held-out British Grand Prix from training or calibration."""
    if purpose not in {"training", "calibration"}:
        raise ValueError("purpose must be training or calibration")
    if any(_is_british_grand_prix(row) for row in rows):
        raise ValueError("British Grand Prix is held out and cannot be used for training or calibration")


def _group_rows(rows: Iterable[Mapping[str, Any]], unit: str) -> dict[str, list[dict[str, Any]]]:
    if unit not in SUPPORTED_UNITS:
        raise ValueError(f"unit must be one of {sorted(SUPPORTED_UNITS)}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(unit)
        if value is None or value == "":
            raise ValueError(f"row is missing required split unit: {unit}")
        grouped[str(value)].append(dict(row))
    if not grouped:
        raise ValueError("at least one row is required")
    return grouped


def _single_value(group_rows: list[dict[str, Any]], field: str, group_key: str) -> Any:
    values = {str(row.get(field)) for row in group_rows if row.get(field) is not None}
    if len(values) != 1:
        raise ValueError(f"split group {group_key!r} must have exactly one {field}")
    return next(iter(values))


def _validate_battle_boundaries(grouped: Mapping[str, list[dict[str, Any]]]) -> None:
    """Ensure an optional battle ID cannot be assigned through two groups."""
    battle_groups: dict[str, set[str]] = defaultdict(set)
    for group_key, members in grouped.items():
        for row in members:
            battle_id = row.get("battle_id")
            if battle_id is not None and battle_id != "":
                battle_groups[str(battle_id)].add(group_key)
    duplicated = sorted(battle_id for battle_id, groups in battle_groups.items() if len(groups) > 1)
    if duplicated:
        raise ValueError(f"battle_id appears in more than one split group: {duplicated[0]}")


def make_split(
    rows: Iterable[Mapping[str, Any]], unit: str, design: str, seed: int
) -> list[dict[str, Any]]:
    """Create one persistent, deterministic C9 assignment per split group.

    Assignments name an evaluation fold. Consumers select a fold and derive
    training rows only from all other non-HOLDOUT groups, then call
    ``assert_training_or_calibration_eligible`` before fitting.
    """
    grouped = _group_rows(rows, unit)
    _validate_battle_boundaries(grouped)
    ordered = sorted(grouped)
    parsed_design, fold_count = _parse_design(design, len(ordered))
    if parsed_design == "kfold" and (fold_count is None or fold_count < 2 or fold_count > len(ordered)):
        raise ValueError("kfold requires between 2 and the number of split groups")

    assignments: dict[str, dict[str, Any]] = {}
    demo_groups = {
        key for key, members in grouped.items() if any(_is_british_grand_prix(row) for row in members)
    }
    non_demo = [key for key in ordered if key not in demo_groups]

    if parsed_design == "kfold":
        shuffled = list(non_demo)
        Random(seed).shuffle(shuffled)
        for index, key in enumerate(shuffled):
            assignments[key] = {"fold_id": f"fold_{index % fold_count}", "split_role": "EVALUATION"}
    elif parsed_design == "leave_one_event_out":
        for key in non_demo:
            event = _single_value(grouped[key], "event", key)
            assignments[key] = {"fold_id": f"event_{_slug(event)}", "split_role": "EVALUATION"}
    else:  # year_forward
        for key in non_demo:
            year = _single_value(grouped[key], "year", key)
            try:
                year = int(year)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"split group {key!r} has a non-integer year") from exc
            assignments[key] = {"fold_id": f"year_forward_{year}", "split_role": "EVALUATION"}

    for key in demo_groups:
        assignments[key] = {
            "fold_id": "british_grand_prix_holdout",
            "split_role": "HOLDOUT",
        }

    return [
        {
            "schema_version": SPLIT_SCHEMA_VERSION,
            "split_unit": unit,
            "group_key": key,
            "design": parsed_design,
            "seed": seed,
            **assignments[key],
        }
        for key in ordered
    ]


def build_split_manifest(
    assignments: Iterable[Mapping[str, Any]], unit: str, design: str, seed: int, source_row_count: int
) -> dict[str, Any]:
    """Return a portable manifest that locks the split configuration and assignment order."""
    assignment_rows = [dict(row) for row in assignments]
    canonical = json.dumps(assignment_rows, sort_keys=True, separators=(",", ":"))
    assignment_sha256 = sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "schema_version": SPLIT_SCHEMA_VERSION,
        "assignment_version": f"{SPLIT_SCHEMA_VERSION}:{assignment_sha256[:12]}",
        "unit": unit,
        "design": design,
        "seed": seed,
        "source_row_count": source_row_count,
        "assignment_count": len(assignment_rows),
        "holdout_event": "British Grand Prix",
        "assignment_sha256": assignment_sha256,
    }
