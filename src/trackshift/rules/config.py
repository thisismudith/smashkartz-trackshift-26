"""Loader for the versioned event rule configuration (M18, CP-03).

Regulations are external inputs, not learned parameters (AGENTS.md section 21),
so they live in YAML with a provenance tier and a source string on every value,
and are read through this module rather than being scattered as constants.

The point of the tiers is that a number can be *usable for modelling* while
being *unusable for a claim*. An unsourced power envelope still lets the DP be
built and debugged; it just cannot back a statement that the system is legal by
construction (sections 57, 58). :func:`resolve` enforces that split: in strict
mode it refuses to hand back anything still ``UNVERIFIED`` or ``PROXY_*``.

Typical use::

    rules = load_event_rules("british_grand_prix")
    gap = resolve(rules, "overtake.detection_gap_s")          # -> ResolvedValue
    curve = resolve(rules, "power_envelope.normal")

Strict mode is a property of the configuration, not an argument, so a caller
cannot quietly opt out of it::

    rules = load_event_rules("british_grand_prix", strict=True)
    resolve(rules, "overtake.detection_gap_s")   # raises UnsourcedValue
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "RULES_DIR",
    "TIERS",
    "CLAIMABLE_TIERS",
    "RuleConfigError",
    "UnsourcedValue",
    "ResolvedValue",
    "load_common",
    "load_event_rules",
    "available_events",
    "resolve",
    "unsourced_keys",
    "validate_event_file",
]

ROOT = Path(__file__).resolve().parents[3]
RULES_DIR = ROOT / "config" / "rules"

#: Every recognised provenance tier.
TIERS = {
    "RULE_FIA",              # cited to an FIA article
    "OBSERVED_RCM",          # from race-control messages
    "OBSERVED",              # from another raw source, e.g. corners.json
    "DERIVED_TELEMETRY",     # measured from telemetry
    "PROXY_HISTORICAL_DRS",  # inferred from 2022-2025 DRS; development only
    "UNVERIFIED",            # not yet sourced
}

#: Tiers a public claim may rest on. PROXY_* is excluded deliberately:
#: historical DRS is not 2026 Overtake (section 41).
CLAIMABLE_TIERS = {"RULE_FIA", "OBSERVED_RCM", "OBSERVED", "DERIVED_TELEMETRY"}


class RuleConfigError(ValueError):
    """The configuration is missing, malformed, or internally inconsistent."""


class UnsourcedValue(RuntimeError):
    """A value was requested in strict mode but is not claimable.

    Distinct from a missing key: the number exists and may be fine, it simply
    has not been traced to a source yet.
    """


@dataclass(frozen=True)
class ResolvedValue:
    """A rule value with the provenance needed to decide what it may support."""

    key: str
    value: Any
    value_source: str
    source: str
    note: str | None = None
    event: str | None = None
    from_common: bool = False

    @property
    def claimable(self) -> bool:
        """True when this value may back a public claim (sections 57, 58)."""
        return self.value_source in CLAIMABLE_TIERS

    def __str__(self) -> str:
        origin = "common" if self.from_common else (self.event or "event")
        return f"{self.key}={self.value!r} [{self.value_source}, {origin}]"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise RuleConfigError(f"rule file not found: {path}. See CHECKPOINTS_TANVEER.md CP-03.")
    # utf-8-sig: a file edited on Windows may carry a BOM, which yaml does not
    # strip, yielding a key named "﻿schema_version".
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RuleConfigError(f"{path.name} must parse to a mapping, got {type(data).__name__}")
    return data


@functools.lru_cache(maxsize=None)
def load_common(season: str = "2026", rules_dir: Path | None = None) -> dict:
    return _read_yaml((rules_dir or RULES_DIR) / str(season) / "common.yaml")


@functools.lru_cache(maxsize=None)
def load_event_rules(event: str, season: str = "2026", rules_dir: Path | None = None,
                     strict: bool | None = None) -> dict:
    """Load one event's rules, merged over the season defaults.

    Parameters
    ----------
    event:
        Event key, e.g. ``british_grand_prix``. Matches the file name.
    strict:
        Overrides ``strict_mode`` from common.yaml. Leave as ``None`` to take
        the configured value, so strictness is a property of the configuration
        rather than something each caller decides.
    """
    directory = (rules_dir or RULES_DIR) / str(season)
    common = load_common(season, rules_dir)
    specific = _read_yaml(directory / f"{event}.yaml")

    merged = dict(common)
    merged["_common_keys"] = set(common)
    merged["_event"] = event
    merged["_season"] = str(season)
    for key, value in specific.items():
        merged[key] = value
    # Event-level overrides sit in their own block so the merge stays obvious.
    for key, value in (specific.get("overrides") or {}).items():
        merged[key] = value
    merged["_strict"] = common.get("strict_mode", False) if strict is None else bool(strict)
    return merged


def available_events(season: str = "2026", rules_dir: Path | None = None) -> list[str]:
    directory = (rules_dir or RULES_DIR) / str(season)
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.yaml") if p.stem != "common")


def _walk(node: Any, path: str = "") -> list[tuple[str, dict]]:
    """Yield every (dotted_path, mapping) that looks like a value block.

    A value block is a mapping carrying ``value_source``; everything else is
    just structure.
    """
    found: list[tuple[str, dict]] = []
    if isinstance(node, dict):
        if "value_source" in node:
            found.append((path, node))
        for key, child in node.items():
            if key.startswith("_"):
                continue
            found.extend(_walk(child, f"{path}.{key}" if path else str(key)))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            found.extend(_walk(child, f"{path}[{index}]"))
    return found


def resolve(rules: dict, key: str) -> ResolvedValue:
    """Look up a dotted key and return its value with provenance.

    Raises
    ------
    RuleConfigError
        If the key is absent, or present but carrying no ``value_source``.
        Never returns a bare value for an unannotated key: an unlabelled number
        is exactly what this module exists to prevent.
    UnsourcedValue
        In strict mode, when the value is ``UNVERIFIED`` or ``PROXY_*``.
    """
    node: Any = rules
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            near = [k for k, _ in _walk(rules) if key.split(".")[-1] in k]
            hint = f" Near matches: {', '.join(sorted(near)[:4])}." if near else ""
            raise RuleConfigError(f"no rule key '{key}' for event '{rules.get('_event')}'.{hint}")
        node = node[part]

    if not isinstance(node, dict) or "value_source" not in node:
        raise RuleConfigError(
            f"rule key '{key}' has no value_source. Every regulatory value must carry "
            "its provenance and source (section 21); an unlabelled number cannot be "
            "audited or claimed against."
        )

    tier = node.get("value_source")
    if tier not in TIERS:
        raise RuleConfigError(f"rule key '{key}' has unknown value_source {tier!r}; expected one of {sorted(TIERS)}")

    resolved = ResolvedValue(
        key=key,
        # A curve has no scalar "value"; hand back the whole block so the caller
        # sees breakpoints_kmh and max_power_kw together.
        value=node.get("value", {k: v for k, v in node.items()
                                 if k not in {"value_source", "source", "note"}}),
        value_source=tier,
        source=node.get("source", ""),
        note=node.get("note"),
        event=rules.get("_event"),
        from_common=key.split(".")[0] in (rules.get("_common_keys") or set()),
    )

    if rules.get("_strict") and not resolved.claimable:
        raise UnsourcedValue(
            f"'{key}' is {tier} and strict_mode is on, so it cannot be used: {resolved.source!r}. "
            "Either cite it to an FIA article and set value_source: RULE_FIA, or run with "
            "strict_mode false for development. A claim resting on this value would be "
            "unsupported (AGENTS.md sections 57, 58)."
        )
    return resolved


def unsourced_keys(rules: dict) -> list[str]:
    """Every key that would block strict mode, in file order."""
    return [path for path, node in _walk(rules)
            if node.get("value_source") not in CLAIMABLE_TIERS]


def validate_event_file(path: Path) -> list[str]:
    """Structural problems in one event file. Empty means valid."""
    problems: list[str] = []
    try:
        data = _read_yaml(path)
    except RuleConfigError as exc:
        return [str(exc)]

    for key in ("schema_version", "event", "year"):
        if key not in data:
            problems.append(f"{path.name}: missing required key '{key}'")

    if data.get("event") and data["event"] != path.stem:
        problems.append(f"{path.name}: event key '{data['event']}' does not match the file name")

    for dotted, node in _walk(data):
        tier = node.get("value_source")
        if tier not in TIERS:
            problems.append(f"{path.name}: {dotted} has unknown value_source {tier!r}")
        if "source" not in node:
            problems.append(f"{path.name}: {dotted} has no source string")
        source = str(node.get("source", ""))
        # A cited value whose source is still a TODO is the worst case: it looks
        # sourced and is not.
        if tier == "RULE_FIA" and "TODO" in source.upper():
            problems.append(
                f"{path.name}: {dotted} claims RULE_FIA but its source is still a TODO. "
                "That is worse than UNVERIFIED, because it reads as cited."
            )
        if tier == "UNVERIFIED" and not source.strip():
            problems.append(f"{path.name}: {dotted} is UNVERIFIED with an empty source; "
                            "record what was searched so the gap is auditable")
    return problems
