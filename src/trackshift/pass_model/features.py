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

from ..data.registry import load_feature_registry
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
    "select_features",
    "build_matrix",
]

FEATURE_SCHEMA_VERSION = "m10_pass_features_v1"
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
        entry = entries.get(name)
        if entry is None:
            excluded[name] = "not in config/feature_registry.yaml (CP-02 owns the namespace)"
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

        groups = _groups(name, entry)
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
