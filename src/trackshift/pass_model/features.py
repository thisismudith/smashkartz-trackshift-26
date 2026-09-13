"""Checkpoint-scoped feature matrices for the M10 pass model (Tanveer CP-14).

The feature list is **derived from** ``config/feature_registry.yaml``. It is not
written down here, and that is the point: CP-13 already proved per checkpoint
that a DETECTION row never populates an activation-time column, and it proved it
against the registry's ``decision_checkpoint`` field. If CP-14 then re-declared
its own list, the two could drift and the leakage guarantee would quietly become
a guarantee about a stale list. So the same field selects the columns here.

Six registry facts decide whether a column is a feature at a checkpoint:

``decision_checkpoint``
    Which checkpoints may know it. ``null`` means checkpoint-independent.
    This is the leakage gate: ``gap_at_activation_s`` lists ACTIVATION and
    BRAKING, so it is absent from every DETECTION matrix.
``causal_status``
    ``ACAUSAL`` columns look backwards from the outcome. The label and the audit
    fields are ACAUSAL, and no ACAUSAL column is ever a feature.
``live_safe``
    A column that cannot be produced live cannot be a feature in a model the DP
    consumes live.
``interaction_group``
    ``key`` marks an identifier (``year``, ``event``, ``session``, ``lap``,
    ``battle_id``). Identifiers are registered as CAUSAL and live-safe -- they
    are honest columns -- but training on them is memorisation, not racecraft.
    ``identity`` marks driver identity, held behind a switch so CP-14's
    with-and-without-identity comparison is one flag rather than two code paths.
``AUDIT_ONLY_COLUMNS``
    ``pass_attempted`` and ``outcome_distance_m``, excluded by name as well as by
    causal status, because section 19 names them and a second lock costs nothing.
unregistered
    Refused. CP-02 makes the registry the single namespace; a column nobody
    declared has no recorded provenance, so it cannot enter a feature matrix.
    Refusing is not silent -- the selection reports every rejection and why.

The consequence worth knowing before reading a report: with the registry as it
stands, ``attacker_team`` and ``defender_team`` are **not registered**, so team
identity is unavailable to CP-14 even though the column exists in the table.
Driver identity (``attacker``, ``defender``) is registered and available. The
identity ablation in section 17 is therefore a driver-identity ablation until
the team columns are registered.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..data.registry import FeatureBoundaryError, load_feature_registry, validate_feature_admission
from ..features.opportunities import (
    AUDIT_ONLY_COLUMNS,
    CHECKPOINTS,
    allowed_at_checkpoint,
)

__all__ = [
    "FEATURE_SCHEMA_VERSION",
    "LABEL_COLUMN",
    "KEY_GROUP",
    "IDENTITY_GROUP",
    "STRUCTURAL_COLUMNS",
    "FeatureSelection",
    "FeatureSelectionError",
    "audit_feature_matrix",
    "select_features",
    "assert_model_feature_boundary",
    "build_matrix",
]

FEATURE_SCHEMA_VERSION = "m10_pass_features_v2"
LABEL_COLUMN = "passed_by_outcome_horizon"

#: ``interaction_group`` members that are not modelling signal.
KEY_GROUP = "key"
IDENTITY_GROUP = "identity"

#: Structural columns the opportunity schema carries for provenance and joining.
#:
#: These are refused **unconditionally**, before the registry is consulted, and
#: that is deliberate. Relying on "not registered" to reject them is a trap: the
#: registry is a shared namespace that both owners extend, and a name registered
#: for one purpose collides with a structural column of the same name here. It
#: has already happened once -- ``schema_version`` was an unregistered structural
#: column in CP-13's output and was then registered upstream as an M08 rival-state
#: feature. That entry declares ``live_safe: false`` so it is still refused, but
#: only by luck: a structural column registered as CAUSAL and live-safe would have
#: silently entered every feature matrix, and nothing downstream would have said so.
STRUCTURAL_COLUMNS: frozenset[str] = frozenset({
    "opportunity_id",
    "decision_checkpoint",
    "feature_cutoff_distance_m",
    "feature_cutoff_offset_m",
    "crosses_lap_boundary",
    "outcome_horizon",
    "label_definition",
    "schema_version",
    "regulation_era",
})

#: The sentinel a missing categorical becomes. A NaN in a categorical is a real
#: state ("no team recorded"), and the four libraries disagree on how to hold
#: one -- CatBoost refuses a float NaN in a ``cat_features`` column outright.
MISSING_CATEGORY = "__missing__"

METADATA_ONLY_GROUP = "metadata_only"
FORBIDDEN_MODEL_TOKENS = (
    "future", "outcome", "pass_attempted", "outcome_distance", "position_swap",
    "zone", "event_id", "circuit_id",
)


class FeatureSelectionError(ValueError):
    """Raised when a matrix cannot be built without violating a CP-14 gate."""


@dataclass(frozen=True)
class FeatureSelection:
    """The columns one checkpoint's model may see, and why the rest were refused.

    ``excluded`` is part of the saved artifact, not debug output. A reviewer
    asking "why is speed_at_braking_kmh missing from the DETECTION model" reads
    the answer out of the feature schema instead of re-deriving it.
    """

    checkpoint: str
    numeric: tuple[str, ...]
    categorical: tuple[str, ...]
    identity: tuple[str, ...]
    include_identity: bool
    excluded: dict[str, str] = field(default_factory=dict)
    schema_version: str = FEATURE_SCHEMA_VERSION

    @property
    def columns(self) -> tuple[str, ...]:
        """Every column the model sees, in a stable order.

        Numeric first, then categorical, each sorted. Order is fixed so a schema
        written today matches one written tomorrow from the same registry: an
        artifact whose column order drifts cannot be checked against its manifest.
        """
        return tuple(self.numeric) + tuple(self.categorical)

    def as_schema(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_checkpoint": self.checkpoint,
            "include_identity": self.include_identity,
            "numeric": list(self.numeric),
            "categorical": list(self.categorical),
            "identity": list(self.identity),
            "label": LABEL_COLUMN,
            "n_features": len(self.columns),
            "excluded": dict(sorted(self.excluded.items())),
        }


def _groups(name: str, entry: Mapping[str, Any]) -> set[str]:
    raw = entry.get("interaction_group") or []
    if isinstance(raw, str):
        # A scalar here would make the 'key' test a substring test and silently
        # let identifiers through, so it is refused rather than coerced.
        raise FeatureSelectionError(
            f"feature {name!r} has interaction_group as the string {raw!r}; "
            "the registry contract is a list"
        )
    return {str(item) for item in raw}


def _is_categorical(name: str, entry: Mapping[str, Any], dtypes: Mapping[str, Any] | None) -> bool:
    """Categorical if the frame's dtype says so, else if the registry gives no unit.

    Dtype is the stronger signal and wins when a frame is available. The registry
    fallback exists so a selection can be made from column names alone, which the
    schema-only paths and the tests need.
    """
    if dtypes is not None and name in dtypes:
        kind = getattr(dtypes[name], "kind", None)
        if kind is not None:
            return kind in {"O", "U", "S", "b"}
        return str(dtypes[name]) in {"object", "string", "category", "bool", "str"}
    return entry.get("unit") is None


def select_features(
    checkpoint: str,
    columns: Iterable[str],
    *,
    include_identity: bool = False,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
    dtypes: Mapping[str, Any] | None = None,
    year: int | str | None = None,
    consumer: str = "C4",
    mode: str = "development",
) -> FeatureSelection:
    """Decide which of ``columns`` the model at ``checkpoint`` may train on."""
    if checkpoint not in CHECKPOINTS:
        raise FeatureSelectionError(
            f"unknown decision checkpoint {checkpoint!r}; expected one of {CHECKPOINTS}"
        )
    entries = registry if registry is not None else load_feature_registry()
    allowed = allowed_at_checkpoint(checkpoint, entries)

    numeric: list[str] = []
    categorical: list[str] = []
    identity: list[str] = []
    excluded: dict[str, str] = {}

    for name in columns:
        if name == LABEL_COLUMN:
            excluded[name] = "label"
            continue
        if name in AUDIT_ONLY_COLUMNS:
            excluded[name] = "audit only (section 19); never a feature"
            continue
        if name in STRUCTURAL_COLUMNS:
            # Checked before the registry on purpose -- see STRUCTURAL_COLUMNS.
            excluded[name] = "structural column of the opportunity schema; never a feature"
            continue
        if name in {"drs", "drs_open", "historical_drs_open", "historical_drs_eligible"} or str(name).startswith("historical_drs_"):
            try:
                validate_feature_admission(
                    [name], year=year, consumer=consumer, mode=mode,
                )
            except FeatureBoundaryError as exc:
                if str(mode).lower() in {"final", "release", "replay"}:
                    raise FeatureSelectionError(str(exc)) from exc
                excluded[name] = str(exc)
                continue
        entry = entries.get(name)
        if entry is None:
            excluded[name] = "not in config/feature_registry.yaml (CP-02 owns the namespace)"
            continue
        groups = _groups(name, entry)
        if METADATA_ONLY_GROUP in groups:
            excluded[name] = "rule/display/audit metadata; never a trainable feature"
            continue
        if name not in allowed:
            excluded[name] = (
                f"decision_checkpoint={entry.get('decision_checkpoint')}; "
                f"not knowable at {checkpoint}"
            )
            continue
        if entry.get("causal_status") != "CAUSAL":
            excluded[name] = f"causal_status={entry.get('causal_status')}"
            continue
        if entry.get("live_safe") is not True:
            excluded[name] = f"live_safe={entry.get('live_safe')}"
            continue

        if KEY_GROUP in groups:
            excluded[name] = "interaction_group contains 'key'; an identifier, not signal"
            continue
        if IDENTITY_GROUP in groups:
            identity.append(name)
            if not include_identity:
                excluded[name] = "identity group, disabled for this run (section 17)"
                continue

        if _is_categorical(name, entry, dtypes):
            categorical.append(name)
        else:
            numeric.append(name)

    if not numeric and not categorical:
        raise FeatureSelectionError(
            f"{checkpoint}: no column survived selection. "
            f"Refused: {dict(sorted(excluded.items()))}"
        )
    return FeatureSelection(
        checkpoint=checkpoint,
        numeric=tuple(sorted(numeric)),
        categorical=tuple(sorted(categorical)),
        identity=tuple(sorted(identity)),
        include_identity=include_identity,
        excluded=excluded,
    )


def assert_model_feature_boundary(
    frame,
    *,
    year: int | str | None,
    consumer: str = "C4",
    mode: str = "development",
) -> None:
    """Apply the registry era gate to a model/calibration frame.

    Keeping this beside feature selection prevents a caller that supplies a
    pre-built ``FeatureSelection`` from bypassing the same DRS policy.
    """
    columns = list(getattr(frame, "columns", frame))
    try:
        validate_feature_admission(columns, year=year, consumer=consumer, mode=mode)
    except FeatureBoundaryError as exc:
        raise FeatureSelectionError(str(exc)) from exc


def audit_feature_matrix(frame, selection: FeatureSelection) -> dict[str, Any]:
    """Fail closed on degenerate or identity-encoding trainable features.

    This is deliberately a data audit, not a model-specific heuristic.  It
    catches constants, exact duplicates, affine/deterministic transforms and
    event/circuit identifiers before any candidate sees a matrix.
    """
    import numpy as np

    columns = list(selection.numeric)
    forbidden = [name for name in selection.columns
                 if any(token in str(name).lower() for token in FORBIDDEN_MODEL_TOKENS)]
    if forbidden:
        raise FeatureSelectionError(
            "pre-training feature audit rejected forbidden future/outcome or "
            f"event/zone identifiers: {sorted(forbidden)}"
        )
    if not columns:
        return {"status": "PASS", "numeric_features": [], "constant": [], "duplicates": [], "deterministic_transforms": [], "event_geometry_identifiers": []}

    constants: list[str] = []
    values: dict[str, Any] = {}
    for name in columns:
        series = frame[name]
        numeric = np.asarray(series.dropna(), dtype=float)
        if numeric.size and np.any(~np.isfinite(numeric)):
            raise FeatureSelectionError(f"pre-training feature audit rejected non-finite values in {name}")
        values[name] = numeric
        if numeric.size > 1 and np.all(numeric == numeric[0]):
            constants.append(name)
    if constants:
        raise FeatureSelectionError(f"pre-training feature audit rejected constant numeric feature(s): {sorted(constants)}")

    duplicates: list[tuple[str, str]] = []
    transforms: list[tuple[str, str]] = []
    for index, left in enumerate(columns):
        for right in columns[index + 1:]:
            a, b = values[left], values[right]
            if len(a) != len(b) or not len(a):
                continue
            if np.array_equal(a, b):
                duplicates.append((left, right))
                continue
            if len(a) > 1 and np.ptp(b) > 0:
                slope = (a[-1] - a[0]) / (b[-1] - b[0])
                intercept = a[0] - slope * b[0]
                if np.array_equal(a, slope * b + intercept):
                    transforms.append((left, right))
    if duplicates:
        raise FeatureSelectionError(f"pre-training feature audit rejected exact duplicate feature(s): {duplicates}")
    if transforms:
        raise FeatureSelectionError(f"pre-training feature audit rejected deterministic transform(s): {transforms}")

    event_geometry: list[str] = []
    if "event" in frame.columns and frame["event"].nunique(dropna=True) > 1:
        for name in columns:
            ranges = sorted((float(values.min()), float(values.max())) for values in
                            [group for group in [frame.loc[group.index, name].dropna().to_numpy(dtype=float)
                                                  for _, group in frame.groupby("event", sort=False)] if len(group)])
            if len(ranges) > 1 and all(ranges[i][1] < ranges[i + 1][0] for i in range(len(ranges) - 1)):
                event_geometry.append(name)
    if event_geometry:
        raise FeatureSelectionError(
            "pre-training feature audit rejected event-unique numeric geometry "
            f"identifier(s): {sorted(event_geometry)}"
        )
    return {"status": "PASS", "numeric_features": columns, "constant": [], "duplicates": [],
            "deterministic_transforms": [], "event_geometry_identifiers": []}


def build_matrix(frame, selection: FeatureSelection, *, require_label: bool = True):
    """Return ``(X, y)`` for one checkpoint, dropping rows with no label.

    An unlabelled opportunity is a retirement or a truncated session, not a
    failed pass (see ``label_zone_exit_v1``). Scoring it as a negative would bias
    the base rate downward in exactly the races where the most interesting
    battles ended early, so those rows are dropped here and counted by the caller.

    ``y`` is ``None`` when ``require_label`` is false, which the inference path
    needs and the training path never asks for.
    """
    missing = [name for name in selection.columns if name not in frame.columns]
    if missing:
        raise FeatureSelectionError(
            f"{selection.checkpoint}: frame is missing selected column(s) {missing}"
        )

    subset = frame
    y = None
    if require_label:
        if LABEL_COLUMN not in frame.columns:
            raise FeatureSelectionError(f"frame has no {LABEL_COLUMN} column")
        subset = frame.loc[frame[LABEL_COLUMN].notna()]
        if subset.empty:
            raise FeatureSelectionError(
                f"{selection.checkpoint}: every row has a null label; nothing to fit"
            )
        y = subset[LABEL_COLUMN].astype(bool).astype("int8").to_numpy()

    X = subset.loc[:, list(selection.columns)].copy()
    for name in selection.categorical:
        X[name] = X[name].astype("object").where(X[name].notna(), MISSING_CATEGORY).astype(str)
    for name in selection.numeric:
        # float32 halves the matrix and costs nothing: telemetry-derived features
        # carry nowhere near float64's significant digits.
        X[name] = X[name].astype("float32")
    return X, y
