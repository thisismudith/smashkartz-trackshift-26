"""Overtake-opportunity dataset (M07, CP-13), contract for Chain P.

One row per opportunity per decision checkpoint, with a strict feature cutoff.
Section 19: a ``DETECTION`` row must never carry activation-time or
braking-time quantities. That is enforced here in code against the registry's
``decision_checkpoint`` field, not by convention -- a convention is what a
future contributor breaks by adding one convenient column, and the resulting
model looks excellent right up until it is asked to predict something.

The enforcement direction matters. Rows are built **from a view truncated at
the cutoff**, and then checked. Building the full row and blanking fields
afterwards produces the same table when it works and silently leaks when a
blanking rule is missed, which is why :func:`build_opportunity_rows` takes a
builder callable per checkpoint rather than a finished row.
"""
from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # pragma: no cover - direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trackshift.data.registry import load_feature_registry

__all__ = [
    "CHECKPOINTS",
    "CHECKPOINT_ORDER",
    "LABEL_DEFINITION",
    "AUDIT_ONLY_COLUMNS",
    "OPPORTUNITY_SCHEMA_VERSION",
    "LeakageError",
    "OpportunityError",
    "allowed_at_checkpoint",
    "assert_checkpoint_scope",
    "build_opportunity_rows",
    "label_zone_exit_v1",
    "opportunity_id",
]

OPPORTUNITY_SCHEMA_VERSION = "m07_overtake_opportunities_v1"

#: Section 19 decision checkpoints, in causal order.
CHECKPOINTS: tuple[str, ...] = ("DETECTION", "ACTIVATION", "BRAKING")
CHECKPOINT_ORDER = {name: index for index, name in enumerate(CHECKPOINTS)}

#: Versioned and stored in every row, so a label change is visible in the data
#: rather than only in a commit message.
LABEL_DEFINITION = "zone_exit_v1"

#: Retained for audit, never features (section 19). Passed through the scope
#: check explicitly so that adding one to a feature list fails loudly.
AUDIT_ONLY_COLUMNS: tuple[str, ...] = ("pass_attempted", "outcome_distance_m")


class OpportunityError(ValueError):
    """The opportunity cannot be built as specified."""


class LeakageError(OpportunityError):
    """A checkpoint row carries a column it could not have known yet.

    This is the one failure in CP-13 that must never be downgraded to a warning.
    """


def opportunity_id(year: Any, event: Any, session: Any, lap: Any, zone: Any,
                   attacker: Any, defender: Any) -> str:
    """Stable identity for one attacker-defender approach to one zone on one lap.

    One opportunity per (battle, zone, lap) -- not per segment, which is the
    documented way this dataset ends up an order of magnitude too large.
    """
    parts = [str(year), str(event), str(session), str(lap), str(zone), str(attacker), str(defender)]
    return "-".join(part.replace(" ", "_") for part in parts)


def allowed_at_checkpoint(checkpoint: str, registry: Mapping[str, Mapping[str, Any]] | None = None) -> set[str]:
    """Every registered column a row at this checkpoint may populate.

    A feature whose registry ``decision_checkpoint`` is null is
    checkpoint-independent and allowed everywhere. Otherwise the field lists the
    checkpoints at which it is knowable, and it is allowed only at those: an
    activation-time quantity is listed for ACTIVATION and BRAKING, and its
    absence from DETECTION is what makes the leakage check bite.
    """
    if checkpoint not in CHECKPOINT_ORDER:
        raise OpportunityError(f"unknown decision checkpoint {checkpoint!r}; expected one of {CHECKPOINTS}")
    entries = registry if registry is not None else load_feature_registry()
    allowed: set[str] = set()
    for name, entry in entries.items():
        scope = (entry or {}).get("decision_checkpoint")
        if scope is None:
            allowed.add(name)
            continue
        if isinstance(scope, str):
            raise OpportunityError(
                f"feature {name!r} has decision_checkpoint {scope!r} as a string; the "
                "registry contract is a list of checkpoints or null"
            )
        names = {str(item).upper() for item in scope}
        unknown = names - set(CHECKPOINT_ORDER)
        if unknown:
            raise OpportunityError(
                f"feature {name!r} has unknown decision_checkpoint(s) {sorted(unknown)}; "
                f"expected a subset of {CHECKPOINTS}"
            )
        if checkpoint in names:
            allowed.add(name)
    return allowed


def assert_checkpoint_scope(
    row: Mapping[str, Any],
    checkpoint: str,
    *,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
    ignore: Iterable[str] = (),
) -> None:
    """Raise if this row populates a column it could not have known yet.

    Only *populated* columns offend. A null activation-time column in a
    DETECTION row is the correct representation of "not yet knowable", which is
    exactly what the schema needs so the three rows stay union-compatible.
    """
    allowed = allowed_at_checkpoint(checkpoint, registry)
    skip = set(ignore) | set(AUDIT_ONLY_COLUMNS) | {
        "opportunity_id", "decision_checkpoint", "feature_cutoff_distance_m",
        "outcome_horizon", "label_definition", "schema_version",
        "passed_by_outcome_horizon", "feature_cutoff_offset_m",
        "crosses_lap_boundary",
    }
    entries = registry if registry is not None else load_feature_registry()
    offenders = []
    for column, value in row.items():
        if column in skip or value is None:
            continue
        if column in allowed:
            continue
        if column not in entries:
            # Unregistered columns are CP-02's gate, not this one; let the
            # registry raise with its own message rather than guessing here.
            continue
        scope = (entries[column] or {}).get("decision_checkpoint")
        offenders.append(f"{column} (decision_checkpoint={scope})")
    if offenders:
        raise LeakageError(
            f"{checkpoint} row populates {len(offenders)} column(s) it cannot know yet: "
            + ", ".join(sorted(offenders))
            + ". Build each checkpoint from a view truncated at its cutoff; do not "
            "build the full row and blank fields afterwards."
        )


@dataclass(frozen=True)
class OpportunityContext:
    """Identity shared by the three rows of one opportunity."""

    opportunity_id: str
    year: Any
    event: Any
    session: Any
    lap: Any
    zone: Any
    attacker: Any
    defender: Any
    battle_id: Any = None
    attacker_team: Any = None
    defender_team: Any = None
    regulation_era: Any = None

    def as_row(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "year": self.year,
            "event": self.event,
            "session": self.session,
            "lap": self.lap,
            "zone": self.zone,
            "battle_id": self.battle_id,
            "attacker": self.attacker,
            "defender": self.defender,
            "attacker_team": self.attacker_team,
            "defender_team": self.defender_team,
            "regulation_era": self.regulation_era,
        }


def build_opportunity_rows(
    context: OpportunityContext,
    cutoffs: Mapping[str, Any],
    feature_builder: Callable[[str, float], Mapping[str, Any]],
    *,
    label: Any = None,
    pass_attempted: Any = None,
    outcome_distance_m: Any = None,
    lap_length_m: Any = None,
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Emit exactly three rows for one opportunity, one per checkpoint.

    ``feature_builder(checkpoint, cutoff_m)`` must return features computed only
    from data at or before ``cutoff_m``. Its output is checked against the
    registry before being accepted, so a builder that reaches past its cutoff
    fails here rather than in a model score three checkpoints later.
    """
    missing = [name for name in CHECKPOINTS if name not in cutoffs]
    if missing:
        raise OpportunityError(
            f"cutoffs must cover every checkpoint; missing {missing}. A checkpoint with "
            "no cutoff has no defined feature scope and cannot be built safely."
        )

    resolved: dict[str, float] = {}
    for name in CHECKPOINTS:
        value = cutoffs[name]
        if value is None:
            raise OpportunityError(
                f"cutoff for {name} is null. While the FIA Detection Lines are unsourced "
                "this is expected: skip the opportunity rather than substituting a distance."
            )
        resolved[name] = float(value)

    # Ordering is causal, and lap distance is a wrapping coordinate. The 2026
    # Detection Line sits at Safety Car Line 1, near the end of the lap, so an
    # activation zone is usually early on the *following* lap and its lap
    # distance is numerically smaller. Order by distance travelled since
    # detection instead, and keep the true lap distances in the row -- the
    # checkpoints are still strictly ordered in the only sense that matters.
    ordered = [resolved[name] for name in CHECKPOINTS]
    length = None
    if lap_length_m is not None:
        try:
            length = float(lap_length_m)
        except (TypeError, ValueError):
            length = None
        if length is not None and length <= 0:
            raise OpportunityError(f"lap_length_m must be positive, got {lap_length_m!r}")

    detection = resolved[CHECKPOINTS[0]]
    if length:
        offsets = [(value - detection) % length for value in ordered]
    else:
        offsets = list(ordered)

    if not all(earlier < later for earlier, later in zip(offsets, offsets[1:])):
        raise OpportunityError(
            "checkpoints must be strictly ordered by distance travelled since the "
            f"Detection Line; got cutoffs {ordered} (offsets {offsets})"
            + ("" if length else ". Pass lap_length_m if this opportunity wraps the lap.")
        )

    rows: list[dict[str, Any]] = []
    for index, name in enumerate(CHECKPOINTS):
        cutoff = resolved[name]
        features = dict(feature_builder(name, cutoff))
        assert_checkpoint_scope(features, name, registry=registry)

        row = context.as_row()
        row.update(features)
        row.update({
            "decision_checkpoint": name,
            "feature_cutoff_distance_m": cutoff,
            # Distance travelled since the Detection Line. Strictly increasing
            # even when the opportunity crosses the start line, which the lap
            # distance above is not.
            "feature_cutoff_offset_m": offsets[index],
            "crosses_lap_boundary": bool(length and offsets[index] > 0 and cutoff < detection),
            "outcome_horizon": LABEL_DEFINITION,
            "label_definition": LABEL_DEFINITION,
            "schema_version": OPPORTUNITY_SCHEMA_VERSION,
            "passed_by_outcome_horizon": label,
            # Audit only. Never a feature (section 19).
            "pass_attempted": pass_attempted,
            "outcome_distance_m": outcome_distance_m,
        })
        rows.append(row)
    return rows


def label_zone_exit_v1(
    attacker_position_at_zone_exit: Any,
    defender_position_at_zone_exit: Any,
) -> bool | None:
    """``zone_exit_v1``: the attacker is ahead at the exit of the activation zone.

    Returns ``None`` when either car has no position at the zone exit -- a
    retirement or a missing sample is not a failed pass, and scoring it as one
    would bias the base rate downward in exactly the races where the most
    interesting battles ended early.

    This is about *this pair* only. An attacker who passes the defender and is
    then repassed by a third car still passed the defender; the counterattack
    case is M23, not a label question.
    """
    if attacker_position_at_zone_exit is None or defender_position_at_zone_exit is None:
        return None
    try:
        attacker = float(attacker_position_at_zone_exit)
        defender = float(defender_position_at_zone_exit)
    except (TypeError, ValueError):
        return None
    # Lower race position number is ahead.
    return attacker < defender
