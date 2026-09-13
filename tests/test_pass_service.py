"""CP-24 pass-model serving and the replay bundle's stub gate.

Two properties carry this file. The route must refuse a request whose
checkpoint cannot know a feature it supplied -- the serving half of CP-13's
leakage guarantee -- and the bundle must report the stubs it actually used
rather than asserting it used none.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

pytest.importorskip("fastapi")

from trackshift.serve.app import create_app  # noqa: E402
from trackshift.serve.pass_service import (  # noqa: E402
    FORBIDDEN_AT,
    CheckpointViolation,
    ModelUnavailable,
    NotModelEligible,
    PassPredictor,
    load_predictor,
)
from trackshift.serve.replay import request_json  # noqa: E402


class StubModel:
    """Returns a fixed probability; the tests are about the guards, not the fit."""

    def __init__(self, value: float = 0.42) -> None:
        self.value = value
        self.seen = None

    def predict_proba(self, X):
        self.seen = X
        return [self.value]


def predictor(checkpoint="DETECTION", **kwargs):
    return PassPredictor(
        checkpoint=checkpoint, family="lightgbm", model=StubModel(),
        numeric=kwargs.pop("numeric", ("gap_at_checkpoint", "p_eligible")),
        categorical=kwargs.pop("categorical", ("corner_type",)),
        manifest=kwargs.pop("manifest", {}),
        artifact_version="v1/detection/lightgbm", **kwargs)


# --- checkpoint scope -------------------------------------------------------

@pytest.mark.parametrize("feature", sorted(FORBIDDEN_AT["DETECTION"]))
def test_detection_refuses_every_feature_it_cannot_know(feature):
    with pytest.raises(CheckpointViolation, match="cannot be known at DETECTION"):
        predictor("DETECTION").predict({"gap_at_checkpoint": 0.7, feature: 300.0})


def test_activation_refuses_only_the_braking_feature():
    at_activation = predictor("ACTIVATION")
    # Knowable at ACTIVATION, so accepted.
    at_activation.predict({"gap_at_checkpoint": 0.7, "speed_at_activation_kmh": 300.0})
    with pytest.raises(CheckpointViolation):
        at_activation.predict({"gap_at_checkpoint": 0.7, "speed_at_braking_kmh": 280.0})


def test_braking_forbids_nothing():
    assert FORBIDDEN_AT["BRAKING"] == frozenset()
    out = predictor("BRAKING").predict(
        {"gap_at_checkpoint": 0.7, "speed_at_braking_kmh": 280.0})
    assert out["p_pass_by_outcome_horizon"]["value"] == pytest.approx(0.42)


def test_a_null_forbidden_feature_is_not_a_violation():
    """Callers routinely send the full shape with nulls; only a value is a claim."""
    predictor("DETECTION").predict(
        {"gap_at_checkpoint": 0.7, "speed_at_activation_kmh": None})


# --- eligibility ------------------------------------------------------------

def test_an_ineligible_state_is_refused():
    with pytest.raises(NotModelEligible, match="green-flag normal-race rows"):
        predictor().predict({"gap_at_checkpoint": 0.7,
                             "normal_race_model_eligible": False})


def test_the_flag_is_also_read_from_a_nested_state():
    with pytest.raises(NotModelEligible):
        predictor().predict({"gap_at_checkpoint": 0.7,
                             "state": {"normal_race_model_eligible": False}})


def test_an_absent_flag_is_treated_as_eligible():
    """Refusing everything unflagged would make the route unusable."""
    out = predictor().predict({"gap_at_checkpoint": 0.7})
    assert out["p_pass_by_outcome_horizon"]["value"] == pytest.approx(0.42)


# --- response shape ---------------------------------------------------------

def test_the_probability_carries_provenance_and_its_artifact():
    out = predictor().predict({"gap_at_checkpoint": 0.7})
    quantity = out["p_pass_by_outcome_horizon"]
    assert quantity["provenance"] == "INFERRED"
    assert quantity["model"] == "M10/lightgbm"
    assert quantity["artifact_version"] == "v1/detection/lightgbm"
    assert out["is_stub"] is False


def test_missing_features_are_named_not_silently_imputed():
    """A probability from two of three features is a different claim from one
    produced from all three, and the caller cannot tell without this."""
    out = predictor().predict({"gap_at_checkpoint": 0.7})
    assert out["features_supplied"] == ["gap_at_checkpoint"]
    assert set(out["features_missing"]) == {"p_eligible", "corner_type"}


def test_the_evidence_grade_travels_with_the_prediction():
    out = predictor(manifest={"run": {"evidence_grade": "INTERIM"}}).predict(
        {"gap_at_checkpoint": 0.7})
    assert out["evidence_grade"] == "INTERIM"


def test_the_row_is_built_in_the_models_own_column_order():
    p = predictor()
    p.predict({"gap_at_checkpoint": 0.7, "p_eligible": 0.5, "corner_type": "SLOW_LEFT"})
    assert list(p.model.seen.columns) == ["gap_at_checkpoint", "p_eligible", "corner_type"]


# --- loading ----------------------------------------------------------------

def test_a_missing_artifact_raises_rather_than_returning_a_stub(tmp_path):
    """The caller decides whether to fall back, so the decision is recorded."""
    with pytest.raises(ModelUnavailable, match="train_pass_model.py"):
        load_predictor(tmp_path, checkpoint="DETECTION")


# --- the route --------------------------------------------------------------

def post(app, body):
    return request_json(app, "POST", "/api/v1/pass/predict", body)


def test_the_route_returns_422_with_a_code_on_a_checkpoint_violation():
    status, payload = post(create_app(mode="replay"), {
        "checkpoint": "DETECTION", "gap_at_checkpoint": 0.7,
        "speed_at_activation_kmh": 300.0})
    assert status == 422
    assert payload["detail"]["code"] == "CHECKPOINT_VIOLATION"


def test_the_route_returns_422_on_an_ineligible_state():
    status, payload = post(create_app(mode="replay"), {
        "checkpoint": "DETECTION", "gap_at_checkpoint": 0.7,
        "normal_race_model_eligible": False})
    assert status == 422
    assert payload["detail"]["code"] == "NOT_MODEL_ELIGIBLE"


def test_a_refusal_never_falls_through_to_the_synthetic_model():
    """Returning a synthetic probability for a request the real model just
    refused would be the worst of both."""
    app = create_app(mode="replay")
    status, payload = post(app, {"checkpoint": "DETECTION", "gap_at_checkpoint": 0.7,
                                 "speed_at_activation_kmh": 300.0})
    assert status == 422
    assert "p_pass_by_outcome_horizon" not in payload
    assert not app.state.stubs_used


def test_a_valid_request_is_answered():
    status, payload = post(create_app(mode="replay"),
                           {"checkpoint": "DETECTION", "gap_at_checkpoint": 0.7})
    assert status == 200
    assert 0.0 <= payload["p_pass_by_outcome_horizon"]["value"] <= 1.0


def _without_artifacts(monkeypatch):
    """An app whose CP-14 artifacts are all unreadable."""
    import trackshift.serve.app as app_module

    def refuse(*args, **kwargs):
        raise ModelUnavailable("no artifact in this test")

    monkeypatch.setattr(app_module, "load_predictor", refuse)
    return app_module


def test_without_an_artifact_the_counted_rate_answers_and_is_not_a_stub(monkeypatch):
    """The measured rate is a real answer, so it must not be labelled a placeholder.

    With no trained model the route still knows something true: how often a pass
    was completed from this gap in the season's own telemetry. Calling that a
    stub would understate it exactly as badly as calling the synthetic curve a
    measurement overstates it.
    """
    _without_artifacts(monkeypatch)
    app = create_app(mode="replay")
    status, payload = post(app, {"decision_checkpoint": "DETECTION", "gap_s": 0.7})
    assert status == 200
    if not app.state.pass_rate_tables:
        pytest.skip("no artifacts/pass_fallback tables on this machine")
    assert payload["is_stub"] is False
    assert payload["p_pass_by_outcome_horizon"]["provenance"] == "DERIVED"
    # A counted frequency is only readable with its sample size and interval.
    assert payload["support"]["n"] > 0
    assert payload["interval"]["low"] <= payload["p_pass_by_outcome_horizon"]["value"] <= payload["interval"]["high"]
    assert "pass/predict:DETECTION" not in app.state.stubs_used


def test_the_synthetic_fallback_records_itself_in_stubs_used(monkeypatch):
    """The gate only works if a stub answer is visible afterwards.

    Reached only when there is neither a trained artifact nor a counted rate --
    the last tier, and the only one that is a placeholder.
    """
    _without_artifacts(monkeypatch)
    app = create_app(mode="replay")
    app.state.pass_rate_tables = {}
    status, payload = post(app, {"checkpoint": "DETECTION", "gap_s": 0.7})
    assert status == 200
    assert payload["is_stub"] is True
    assert "pass/predict:DETECTION" in app.state.stubs_used


# --- the request contract ---------------------------------------------------

def test_decision_checkpoint_selects_the_model_for_that_checkpoint():
    """API.md 5.8 names the field `decision_checkpoint`.

    Reading only `checkpoint` scored every ACTIVATION request with the DETECTION
    model and then refused it for carrying activation speed -- a leakage refusal
    raised against a request that leaked nothing.
    """
    status, payload = post(create_app(mode="replay"), {
        "decision_checkpoint": "ACTIVATION", "gap_at_checkpoint": 0.7,
        "speed_at_activation_kmh": 300.0})
    assert status == 200
    assert payload["decision_checkpoint"] == "ACTIVATION"


def test_the_ui_field_names_reach_the_model():
    """`time_gap_s` is the UI's name for `gap_at_checkpoint` (INTEGRATION.md 3).

    Unmapped, the feature vector arrives empty and the model returns its base
    rate for every request -- a number that looks like a prediction and does not
    move when the gap does.
    """
    status, payload = post(create_app(mode="replay"),
                           {"decision_checkpoint": "DETECTION", "time_gap_s": 0.7})
    assert status == 200
    assert "gap_at_checkpoint" in payload["features_supplied"]


def test_features_may_be_nested_under_features_as_api_md_documents():
    status, payload = post(create_app(mode="replay"), {
        "decision_checkpoint": "DETECTION",
        "features": {"gap_at_checkpoint": 0.7, "p_eligible": 0.9}})
    assert status == 200
    assert "gap_at_checkpoint" in payload["features_supplied"]
    assert "p_eligible" in payload["features_supplied"]


#: A populated DETECTION row, as the UI sends one once its field names are mapped.
#: Deliberately complete: a tree given thirteen NaNs of fifteen is not obliged to
#: be monotone in the two it has, and asserting a direction on a near-empty row
#: would be testing noise.
FULL_DETECTION_ROW = {
    "closing_rate_s_per_s": -0.05, "p_eligible": 0.9, "track_temperature": 41.0,
    "attacker_tyre_life_laps": 12, "defender_tyre_life_laps": 16,
    "tyre_life_delta_laps": -4,
    "wind_head_component_mps": -2.1, "wind_cross_component_mps": 3.4,
    "attacker_tyre_compound": "MEDIUM", "defender_tyre_compound": "HARD",
    "tyre_compound_pair": "MEDIUM|HARD", "corner_type": "MEDIUM_RIGHT",
    "wet_track_flag": False,
}


def test_the_probability_moves_with_the_gap():
    """The whole point of sending features.

    A probability that does not move when the gap does is the signature of a
    feature vector that never arrived -- and it is invisible in any single
    response, because the constant it returns is a perfectly plausible number.
    """
    app = create_app(mode="replay")

    def p(gap):
        status, payload = post(app, {"decision_checkpoint": "DETECTION",
                                     "gap_at_checkpoint": gap, **FULL_DETECTION_ROW})
        assert status == 200
        assert payload["features_missing"] == ["sector"], payload["features_missing"]
        return payload["p_pass_by_outcome_horizon"]["value"]

    close, distant = p(0.2), p(2.5)
    assert close > distant, (
        f"a pass is harder from further back, but 0.2 s gave {close} and 2.5 s gave {distant}")


def test_an_unknown_checkpoint_is_refused_rather_than_silently_detection():
    status, payload = post(create_app(mode="replay"),
                           {"decision_checkpoint": "APEX", "gap_at_checkpoint": 0.7})
    assert status == 422
    assert payload["detail"]["code"] == "FEATURE_SCHEMA_MISMATCH"
