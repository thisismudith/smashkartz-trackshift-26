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


def test_the_synthetic_fallback_records_itself_in_stubs_used(monkeypatch):
    """The gate only works if a stub answer is visible afterwards."""
    import trackshift.serve.app as app_module

    def refuse(*args, **kwargs):
        raise ModelUnavailable("no artifact in this test")

    monkeypatch.setattr(app_module, "load_predictor", refuse)
    app = create_app(mode="replay")
    status, payload = post(app, {"checkpoint": "DETECTION", "gap_s": 0.7})
    assert status == 200
    assert payload["is_stub"] is True
    assert "pass/predict:DETECTION" in app.state.stubs_used
