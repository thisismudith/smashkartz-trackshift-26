"""CP-13 overtake-opportunity dataset (M07).

The leakage test here is non-negotiable: for every DETECTION row, every
activation- and braking-scoped column must be null. A pass model trained on a
DETECTION row that knows the activation speed will score beautifully in
validation and be worthless at the decision point, and nothing else in the
pipeline would catch it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trackshift.features.opportunities import (  # noqa: E402
    AUDIT_ONLY_COLUMNS,
    CHECKPOINTS,
    LABEL_DEFINITION,
    LeakageError,
    OpportunityContext,
    OpportunityError,
    allowed_at_checkpoint,
    assert_checkpoint_scope,
    build_opportunity_rows,
    label_zone_exit_v1,
    opportunity_id,
)

REGISTRY = {
    "gap_at_checkpoint": {"decision_checkpoint": None},
    "p_eligible": {"decision_checkpoint": None},
    "gap_at_activation_s": {"decision_checkpoint": ["ACTIVATION", "BRAKING"]},
    "speed_at_activation_kmh": {"decision_checkpoint": ["ACTIVATION", "BRAKING"]},
    "speed_at_braking_kmh": {"decision_checkpoint": ["BRAKING"]},
}

CUTOFFS = {"DETECTION": 100.0, "ACTIVATION": 320.0, "BRAKING": 900.0}


def context() -> OpportunityContext:
    return OpportunityContext(
        opportunity_id=opportunity_id(2026, "British Grand Prix", "Race", 12, 1, "HAM", "ANT"),
        year=2026, event="British Grand Prix", session="Race", lap=12, zone=1,
        attacker="HAM", defender="ANT", battle_id="b-1", regulation_era="2026",
    )


def honest_builder(checkpoint: str, cutoff: float) -> dict:
    """A builder that respects its cutoff: later fields appear only later."""
    row = {"gap_at_checkpoint": 0.8, "p_eligible": 0.7}
    if checkpoint in ("ACTIVATION", "BRAKING"):
        row["gap_at_activation_s"] = 0.6
        row["speed_at_activation_kmh"] = 290.0
    if checkpoint == "BRAKING":
        row["speed_at_braking_kmh"] = 310.0
    return row


# ------------------------------------------------------------- structure

def test_exactly_three_rows_one_per_checkpoint():
    rows = build_opportunity_rows(context(), CUTOFFS, honest_builder, registry=REGISTRY)
    assert len(rows) == 3
    assert [r["decision_checkpoint"] for r in rows] == list(CHECKPOINTS)
    assert len({r["opportunity_id"] for r in rows}) == 1


def test_feature_cutoff_strictly_increases():
    rows = build_opportunity_rows(context(), CUTOFFS, honest_builder, registry=REGISTRY)
    cutoffs = [r["feature_cutoff_distance_m"] for r in rows]
    assert all(a < b for a, b in zip(cutoffs, cutoffs[1:])), cutoffs


def test_non_increasing_cutoffs_are_refused():
    bad = {"DETECTION": 400.0, "ACTIVATION": 320.0, "BRAKING": 900.0}
    with pytest.raises(OpportunityError, match="strictly increase"):
        build_opportunity_rows(context(), bad, honest_builder, registry=REGISTRY)


def test_null_cutoff_is_refused_rather_than_substituted():
    """While the FIA Detection Lines are unsourced, skip -- never substitute."""
    missing = {"DETECTION": None, "ACTIVATION": 320.0, "BRAKING": 900.0}
    with pytest.raises(OpportunityError, match="null"):
        build_opportunity_rows(context(), missing, honest_builder, registry=REGISTRY)


def test_incomplete_cutoffs_are_refused():
    with pytest.raises(OpportunityError, match="missing"):
        build_opportunity_rows(context(), {"DETECTION": 100.0}, honest_builder, registry=REGISTRY)


def test_label_and_definition_are_stamped_on_every_row():
    rows = build_opportunity_rows(context(), CUTOFFS, honest_builder, label=True, registry=REGISTRY)
    for row in rows:
        assert row["passed_by_outcome_horizon"] is True
        assert row["outcome_horizon"] == LABEL_DEFINITION
        assert row["label_definition"] == LABEL_DEFINITION


# --------------------------------------------------------------- leakage

def test_detection_rows_carry_no_activation_or_braking_column():
    """The non-negotiable test."""
    rows = build_opportunity_rows(context(), CUTOFFS, honest_builder, registry=REGISTRY)
    detection = next(r for r in rows if r["decision_checkpoint"] == "DETECTION")
    for column, entry in REGISTRY.items():
        scope = entry["decision_checkpoint"]
        if scope and "DETECTION" not in scope:
            assert detection.get(column) is None, f"{column} leaked into a DETECTION row"


def test_a_builder_that_reaches_past_its_cutoff_raises():
    def leaky(checkpoint: str, cutoff: float) -> dict:
        # Populates activation speed at every checkpoint, including DETECTION.
        return {"gap_at_checkpoint": 0.8, "speed_at_activation_kmh": 290.0}

    with pytest.raises(LeakageError, match="speed_at_activation_kmh"):
        build_opportunity_rows(context(), CUTOFFS, leaky, registry=REGISTRY)


def test_braking_column_leaking_into_activation_raises():
    def leaky(checkpoint: str, cutoff: float) -> dict:
        row = {"gap_at_checkpoint": 0.8}
        if checkpoint in ("ACTIVATION", "BRAKING"):
            row["speed_at_braking_kmh"] = 310.0
        return row

    with pytest.raises(LeakageError, match="speed_at_braking_kmh"):
        build_opportunity_rows(context(), CUTOFFS, leaky, registry=REGISTRY)


def test_a_null_later_column_is_allowed_so_the_three_rows_stay_union_compatible():
    row = {"gap_at_checkpoint": 0.8, "speed_at_activation_kmh": None}
    assert_checkpoint_scope(row, "DETECTION", registry=REGISTRY)


def test_allowed_at_checkpoint_respects_the_list_contract():
    assert "gap_at_activation_s" not in allowed_at_checkpoint("DETECTION", REGISTRY)
    assert "gap_at_activation_s" in allowed_at_checkpoint("ACTIVATION", REGISTRY)
    assert "speed_at_braking_kmh" not in allowed_at_checkpoint("ACTIVATION", REGISTRY)
    assert "speed_at_braking_kmh" in allowed_at_checkpoint("BRAKING", REGISTRY)
    # A null scope is checkpoint-independent.
    for checkpoint in CHECKPOINTS:
        assert "gap_at_checkpoint" in allowed_at_checkpoint(checkpoint, REGISTRY)


def test_a_string_decision_checkpoint_is_rejected():
    """The registry contract is a list; a bare string would silently mis-scope."""
    with pytest.raises(OpportunityError, match="list"):
        allowed_at_checkpoint("DETECTION", {"x": {"decision_checkpoint": "ACTIVATION"}})


def test_unknown_checkpoint_is_rejected():
    with pytest.raises(OpportunityError):
        allowed_at_checkpoint("APEX", REGISTRY)


def test_audit_columns_are_carried_but_never_count_as_features():
    rows = build_opportunity_rows(
        context(), CUTOFFS, honest_builder,
        pass_attempted=True, outcome_distance_m=812.0, registry=REGISTRY,
    )
    detection = rows[0]
    for column in AUDIT_ONLY_COLUMNS:
        assert column in detection
    # Present on the row, and explicitly exempt from the scope check, because
    # they are retained for audit and excluded from every feature matrix.
    assert_checkpoint_scope(
        {"pass_attempted": True, "outcome_distance_m": 812.0}, "DETECTION", registry=REGISTRY
    )


# ----------------------------------------------------------------- label

def test_zone_exit_v1_is_about_this_pair_only():
    assert label_zone_exit_v1(3, 4) is True     # attacker ahead
    assert label_zone_exit_v1(4, 3) is False
    assert label_zone_exit_v1(3, 3) is False    # not ahead


def test_zone_exit_v1_is_null_when_a_car_has_no_position():
    """A retirement is not a failed pass."""
    assert label_zone_exit_v1(None, 4) is None
    assert label_zone_exit_v1(3, None) is None
    assert label_zone_exit_v1("n/a", 4) is None


def test_opportunity_id_is_stable_and_unique_per_pair_zone_lap():
    first = opportunity_id(2026, "British Grand Prix", "Race", 12, 1, "HAM", "ANT")
    assert first == opportunity_id(2026, "British Grand Prix", "Race", 12, 1, "HAM", "ANT")
    assert first != opportunity_id(2026, "British Grand Prix", "Race", 12, 2, "HAM", "ANT")
    assert first != opportunity_id(2026, "British Grand Prix", "Race", 13, 1, "HAM", "ANT")
    assert " " not in first


def test_real_registry_scopes_the_checkpoint_columns():
    """Against config/feature_registry.yaml, not just the fixture."""
    detection = allowed_at_checkpoint("DETECTION")
    braking = allowed_at_checkpoint("BRAKING")
    assert "speed_at_braking_kmh" not in detection
    assert "speed_at_braking_kmh" in braking
    assert "gap_at_activation_s" not in detection
    assert "p_eligible" in detection
