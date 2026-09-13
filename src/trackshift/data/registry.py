"""Loaders and guards for the feature and dataset registries (M31).

The registry is the single namespace for every column TrackShift produces
(AGENTS.md sections 45, 46). Its value depends entirely on staying in step with
what the builders actually write, so :func:`assert_registered` is meant to be a
**hard call at the end of every builder**, not a lint someone runs occasionally.
A dataset that writes an unregistered column should fail its build.

That is deliberate. The alternative -- registering as an afterthought -- produces
a registry that describes an imagined schema while the real Parquet drifts away
from it, at which point every downstream consumer is reading a contract that is
quietly false.
"""
from __future__ import annotations

import difflib
import functools
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

__all__ = [
    "CONFIG_DIR",
    "REQUIRED_FEATURE_KEYS",
    "REQUIRED_DATASET_KEYS",
    "AVAILABILITY_SCOPES",
    "CAUSAL_STATUSES",
    "PROVENANCE_VALUES",
    "LIVE_STATUSES",
    "DATASET_STATUSES",
    "UnregisteredColumns",
    "RegistryError",
    "FeatureBoundaryError",
    "RAW_DRS_FEATURES",
    "HISTORICAL_DRS_FEATURES",
    "validate_feature_admission",
    "assert_final_feature_boundary",
    "load_feature_registry",
    "load_data_registry",
    "feature",
    "dataset",
    "feature_names",
    "assert_registered",
    "live_safe_features",
    "source_gated_features",
    "validate_registries",
]

ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = ROOT / "config"

REQUIRED_FEATURE_KEYS = (
    "name", "definition", "unit", "source", "derivation",
    "allowed_sessions", "supported_years", "live_safe", "provenance",
    "availability_scope", "source_gated", "decision_checkpoint",
    "uncertainty_field", "causal_status", "counterfactual_safe",
    "regulation_version", "regulation_source", "interaction_group",
    "consuming_models",
)

REQUIRED_DATASET_KEYS = (
    "name", "description", "source", "schema_version", "partitioning",
    "primary_key", "producer", "consumers", "live_status", "status",
)

AVAILABILITY_SCOPES = {"all", "partial", "none"}
CAUSAL_STATUSES = {"CAUSAL", "ACAUSAL", None}
PROVENANCE_VALUES = {"OBSERVED", "DERIVED", "INFERRED", "SIMULATED", "RULE"}
LIVE_STATUSES = {"LIVE_SAFE", "MIXED", "OFFLINE_ONLY"}
DATASET_STATUSES = {"built", "planned"}
DECISION_CHECKPOINTS = {"DETECTION", "ACTIVATION", "BRAKING"}


class RegistryError(ValueError):
    """The registry file itself is malformed or internally inconsistent."""


class UnregisteredColumns(RuntimeError):
    """A dataset produced columns that are not in the feature registry."""


class FeatureBoundaryError(RegistryError):
    """A feature crossed a regulation-era or final-mode boundary."""


# ``drs_open`` is the canonicalized raw ``tel.json drs`` channel.  Keep both
# spellings here because raw frames and API payloads can legitimately expose
# the source spelling before canonicalization.  Neither is a 2026 strategic
# signal; 2026 Overtake state comes from the rule engine.
RAW_DRS_FEATURES = frozenset({"drs", "drs_open"})
HISTORICAL_DRS_FEATURES = frozenset({"historical_drs_open", "historical_drs_eligible"})
HISTORICAL_DRS_CONSUMERS = frozenset({"historical_audit", "historical_prior"})
FINAL_MODES = frozenset({"final", "release", "replay"})


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise RegistryError(f"registry not found: {path}. See CHECKPOINTS_TANVEER.md CP-02.")
    # utf-8-sig: a file edited on Windows may carry a BOM, and yaml does not
    # strip it, producing a bewildering key named "﻿schema_version".
    data = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise RegistryError(f"{path.name} must parse to a mapping, got {type(data).__name__}")
    return data


@functools.lru_cache(maxsize=None)
def load_feature_registry(config_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return the feature registry keyed by feature name.

    Cached: builders call this per row-group and re-parsing YAML each time would
    dominate their runtime. Pass an explicit ``config_dir`` in tests to bypass.
    """
    path = (config_dir or CONFIG_DIR) / "feature_registry.yaml"
    data = _load_yaml(path)
    entries = data.get("features") or []
    if not isinstance(entries, list):
        raise RegistryError("feature_registry.yaml: 'features' must be a list")

    registry: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RegistryError(f"feature_registry.yaml: entry {index} is not a mapping")
        name = entry.get("name")
        if not name:
            raise RegistryError(f"feature_registry.yaml: entry {index} has no name")
        if name in registry:
            raise RegistryError(
                f"feature_registry.yaml: duplicate feature '{name}'. "
                "The registry is a single namespace; two entries means two "
                "definitions of the same column."
            )
        registry[name] = entry
    return registry


@functools.lru_cache(maxsize=None)
def load_data_registry(config_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    """Return the dataset registry keyed by dataset name."""
    path = (config_dir or CONFIG_DIR) / "data_registry.yaml"
    data = _load_yaml(path)
    entries = data.get("datasets") or []
    if not isinstance(entries, list):
        raise RegistryError("data_registry.yaml: 'datasets' must be a list")

    registry: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RegistryError(f"data_registry.yaml: entry {index} is not a mapping")
        name = entry.get("name")
        if not name:
            raise RegistryError(f"data_registry.yaml: entry {index} has no name")
        if name in registry:
            raise RegistryError(f"data_registry.yaml: duplicate dataset '{name}'")
        registry[name] = entry
    return registry


def feature(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Look up one feature. Raises :class:`KeyError` with a usable message."""
    registry = load_feature_registry(config_dir)
    try:
        return registry[name]
    except KeyError:
        # Fuzzy, not substring: the mistakes that actually happen are typos and
        # unit-suffix slips (speed_kph for speed_kmh), where neither name
        # contains the other.
        near = difflib.get_close_matches(name, registry, n=5, cutoff=0.6)
        if not near:
            near = sorted(k for k in registry if name.lower() in k.lower() or k.lower() in name.lower())[:5]
        hint = f" Did you mean: {', '.join(near)}?" if near else ""
        raise KeyError(f"'{name}' is not in the feature registry.{hint}") from None


def dataset(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    registry = load_data_registry(config_dir)
    try:
        return registry[name]
    except KeyError:
        raise KeyError(f"'{name}' is not in the dataset registry.") from None


def feature_names(config_dir: Path | None = None) -> set[str]:
    return set(load_feature_registry(config_dir))


def assert_registered(columns: Iterable[str], context: str, config_dir: Path | None = None) -> None:
    """Raise if any column is absent from the feature registry.

    Call this at the end of every builder, on the frame about to be written::

        assert_registered(df.columns, "telemetry_20m build")

    Parameters
    ----------
    columns:
        Column names about to be written. A DataFrame's ``.columns`` works.
    context:
        What is being written, named well enough to find in a log.

    Raises
    ------
    UnregisteredColumns
        Listing every unregistered column, so one run fixes them all rather
        than surfacing them one at a time.
    """
    known = feature_names(config_dir)
    unknown = sorted({str(c) for c in columns} - known)
    if not unknown:
        return
    raise UnregisteredColumns(
        f"{context}: {len(unknown)} column(s) are not in the feature registry: "
        f"{', '.join(unknown)}. Add them to config/feature_registry.yaml in the "
        "same change that produces them (AGENTS.md section 46) -- a dataset that "
        "writes an unregistered column is undocumented output."
    )


def _drs_feature_name(name: object) -> bool:
    text = str(name).lower()
    return text in RAW_DRS_FEATURES or text in HISTORICAL_DRS_FEATURES or text.startswith("historical_drs_")


def _proxy_in_value(value: object) -> bool:
    if isinstance(value, str):
        return "PROXY_HISTORICAL_DRS" in value.upper()
    if isinstance(value, Mapping):
        return any(_proxy_in_value(k) or _proxy_in_value(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_proxy_in_value(item) for item in value)
    return False


def validate_feature_admission(
    columns: Iterable[str] | Mapping[str, object],
    *,
    year: int | str | None,
    consumer: str,
    mode: str = "development",
    provenance: object | None = None,
    config_dir: Path | None = None,
) -> None:
    """Enforce the DRS regulation-era boundary at a consumer boundary.

    Historical DRS channels may be used only by explicitly named 2022--2025
    audit/prior consumers.  They can never enter a 2026 consumer, and no raw
    DRS/proxy value may cross a final/replay/release boundary.  This function
    intentionally accepts mappings as well as column lists so API/state
    payloads receive the same guard as tabular builders.
    """
    mode_name = str(mode).lower()
    if mode_name not in {"development", "audit", "prior", *FINAL_MODES}:
        raise FeatureBoundaryError(f"unknown feature admission mode {mode!r}")
    try:
        numeric_year = int(year) if year is not None else None
    except (TypeError, ValueError) as exc:
        raise FeatureBoundaryError(f"year must be an integer or None, got {year!r}") from exc

    names = list(columns.keys()) if isinstance(columns, Mapping) else [str(column) for column in columns]
    drs_names = sorted({str(name) for name in names if _drs_feature_name(name)})
    proxy_seen = _proxy_in_value(provenance) or (isinstance(columns, Mapping) and _proxy_in_value(columns))

    if mode_name in FINAL_MODES and (drs_names or proxy_seen):
        offenders = drs_names + (["PROXY_HISTORICAL_DRS"] if proxy_seen else [])
        raise FeatureBoundaryError(
            f"{consumer}: final-mode admission rejects regulation-era/proxy input(s): "
            f"{', '.join(offenders)}. Raw and historical DRS are audit/prior-only, "
            "and PROXY_HISTORICAL_DRS is development-fixture provenance only."
        )

    if not drs_names:
        if numeric_year == 2026 and proxy_seen:
            raise FeatureBoundaryError(
                f"{consumer}: PROXY_HISTORICAL_DRS cannot enter a 2026 consumer"
            )
        return

    if numeric_year == 2026:
        raise FeatureBoundaryError(
            f"{consumer}: raw/historical DRS input {', '.join(drs_names)} is forbidden "
            "and cannot enter a 2026 consumer; an all-zero 2026 raw DRS channel "
            "means unavailable, not closed"
        )
    if numeric_year not in {2022, 2023, 2024, 2025}:
        raise FeatureBoundaryError(
            f"{consumer}: DRS input {', '.join(drs_names)} has no permitted regulation era for year {year!r}"
        )
    if consumer not in HISTORICAL_DRS_CONSUMERS or mode_name not in {"development", "audit", "prior"}:
        raise FeatureBoundaryError(
            f"{consumer}: DRS input {', '.join(drs_names)} is permitted only for an explicitly "
            "labelled historical_audit/historical_prior consumer in development/audit/prior mode"
        )


def assert_final_feature_boundary(
    payload: Mapping[str, object],
    context: str,
    *,
    year: int | str | None = None,
    config_dir: Path | None = None,
) -> None:
    """Reject DRS/proxy keys anywhere in a final decision payload."""
    validate_feature_admission(
        payload,
        year=2026 if year is None else year,
        consumer=context,
        mode="final",
        provenance=payload,
        config_dir=config_dir,
    )


def live_safe_features(config_dir: Path | None = None) -> set[str]:
    """Features usable at decision time (section 44)."""
    return {n for n, e in load_feature_registry(config_dir).items() if e.get("live_safe") is True}


def source_gated_features(config_dir: Path | None = None) -> set[str]:
    """Channels awaiting a documented source. Never zero-fill these (section 11)."""
    return {n for n, e in load_feature_registry(config_dir).items() if e.get("source_gated") is True}


def _check_feature(name: str, entry: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []

    missing = [k for k in REQUIRED_FEATURE_KEYS if k not in entry]
    if missing:
        problems.append(f"{name}: missing required key(s): {', '.join(missing)}")

    scope = entry.get("availability_scope")
    if scope not in AVAILABILITY_SCOPES:
        problems.append(f"{name}: availability_scope {scope!r} not in {sorted(AVAILABILITY_SCOPES)}")

    if entry.get("provenance") not in PROVENANCE_VALUES:
        problems.append(f"{name}: provenance {entry.get('provenance')!r} not in {sorted(PROVENANCE_VALUES)}")

    # live_safe may only be unknown for a channel that does not exist yet.
    # Anywhere else, null means nobody decided, which is the state this field
    # exists to prevent (section 44).
    if entry.get("live_safe") is None and scope != "none":
        problems.append(
            f"{name}: live_safe is null but availability_scope is {scope!r}. "
            "Only an unavailable channel may leave live_safe undecided."
        )
    if entry.get("live_safe") is not None and not isinstance(entry.get("live_safe"), bool):
        problems.append(f"{name}: live_safe must be true, false or null")

    if entry.get("causal_status") not in CAUSAL_STATUSES:
        problems.append(f"{name}: causal_status {entry.get('causal_status')!r} not in {sorted(str(c) for c in CAUSAL_STATUSES)}")

    # An ACAUSAL feature uses future information, so it can never be live-safe.
    if entry.get("causal_status") == "ACAUSAL" and entry.get("live_safe") is True:
        problems.append(
            f"{name}: marked ACAUSAL but live_safe true. A feature using future "
            "information cannot be available at decision time (section 44)."
        )

    if scope == "none" and not entry.get("source_gated"):
        problems.append(f"{name}: availability_scope none implies source_gated true")

    checkpoints = entry.get("decision_checkpoint")
    if checkpoints is not None:
        if not isinstance(checkpoints, list):
            problems.append(f"{name}: decision_checkpoint must be a list or null")
        else:
            bad = [c for c in checkpoints if c not in DECISION_CHECKPOINTS]
            if bad:
                problems.append(f"{name}: unknown decision_checkpoint(s): {bad}")

    for key in ("allowed_sessions", "supported_years", "interaction_group", "consuming_models"):
        if key in entry and not isinstance(entry[key], list):
            problems.append(f"{name}: {key} must be a list")

    return problems


def _check_dataset(name: str, entry: Mapping[str, Any]) -> list[str]:
    problems: list[str] = []
    missing = [k for k in REQUIRED_DATASET_KEYS if k not in entry]
    if missing:
        problems.append(f"{name}: missing required key(s): {', '.join(missing)}")
    if entry.get("live_status") not in LIVE_STATUSES:
        problems.append(f"{name}: live_status {entry.get('live_status')!r} not in {sorted(LIVE_STATUSES)}")
    if entry.get("status") not in DATASET_STATUSES:
        problems.append(f"{name}: status {entry.get('status')!r} not in {sorted(DATASET_STATUSES)}")
    if "primary_key" in entry and not isinstance(entry["primary_key"], list):
        problems.append(f"{name}: primary_key must be a list")
    return problems


def _near_duplicate_names(names: Iterable[str]) -> list[str]:
    """Flag names differing only by a trivial suffix.

    Chain P and Chain E name features independently, so ``gap_s`` and ``gap_sec``
    can both appear and silently mean the same thing. The registry is the single
    namespace; near-duplicates defeat it.
    """
    problems = []
    normalised: dict[str, str] = {}
    for name in sorted(names):
        key = name.rstrip("_")
        for suffix in ("_s", "_m", "_kmh", "_mps", "_kw", "_mj", "_kj", "_pct", "_est", "_id"):
            if key.endswith(suffix):
                key = key[: -len(suffix)]
                break
        key = key.replace("_", "")
        if key in normalised and normalised[key] != name:
            problems.append(
                f"near-duplicate feature names: '{normalised[key]}' and '{name}' "
                "differ only by a unit or type suffix"
            )
        else:
            normalised[key] = name
    return problems


def validate_registries(config_dir: Path | None = None) -> list[str]:
    """Return every structural problem found. Empty means valid."""
    problems: list[str] = []
    features = load_feature_registry(config_dir)
    for name, entry in features.items():
        problems.extend(_check_feature(name, entry))
        if _drs_feature_name(name):
            years = entry.get("supported_years") or []
            if 2026 in years or "2026" in years:
                problems.append(
                    f"{name}: raw/historical DRS features cannot list 2026 in supported_years"
                )
            if entry.get("era_policy") != "historical_prior_only":
                problems.append(
                    f"{name}: raw/historical DRS features must declare era_policy "
                    "historical_prior_only"
                )
    problems.extend(_near_duplicate_names(features))
    for name, entry in load_data_registry(config_dir).items():
        problems.extend(_check_dataset(name, entry))
    return problems
