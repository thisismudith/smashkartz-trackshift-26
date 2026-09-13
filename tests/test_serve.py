from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trackshift.serve.app import create_app  # noqa: E402
from trackshift.serve.fixture import BATTLE_ID, EVENT  # noqa: E402
from trackshift.serve.replay import build_replay_bundle, request_json  # noqa: E402
from trackshift.rules.config import FinalModeError  # noqa: E402


@pytest.fixture()
def client():
    return create_app()


def test_service_exposes_shared_routes_with_synthetic_provenance(client):
    for path in ("/api/v1/meta", "/api/v1/validation", f"/api/v1/track/{EVENT}", f"/api/v1/rules/{EVENT}", "/api/v1/simulate/policies"):
        status, response = request_json(client, "GET", path)
        assert status == 200, (path, response)
    assert request_json(client, "GET", "/api/v1/meta")[1]["release_ready"] is False
    assert request_json(client, "GET", "/api/v1/validation")[1]["rival"]["provenance"] == "SIMULATED"


def test_model_routes_share_c3_and_are_deterministic(client):
    assert request_json(client, "POST", "/api/v1/rules/legal_actions", {})[0] == 200
    first = request_json(client, "POST", "/api/v1/plan", {"include_baselines": True})[1]
    second = request_json(client, "POST", "/api/v1/plan", {"include_baselines": True})[1]
    assert first == second
    assert first["rule_violations"] == 0
    assert request_json(client, "POST", "/api/v1/simulate", {"n_episodes": 2, "seed": 17})[1]["summary"]["rule_violations"] == 0


def test_api_boundary_rejects_2026_drs_input(client):
    status, response = request_json(client, "POST", "/api/v1/plan", {"state": {"historical_drs_open": 0}})
    assert status == 422
    assert response["detail"]["code"] == "FEATURE_SCHEMA_MISMATCH"


def test_final_service_and_replay_fail_closed():
    with pytest.raises(FinalModeError):
        create_app(final_mode=True)


def test_replay_calls_shared_routes_and_records_no_stubs(tmp_path: Path):
    manifest = build_replay_bundle(tmp_path)
    assert manifest["stubs_used"] == []
    assert manifest["final_mode_permitted"] is False
    assert manifest["rule_violations"] == 0
    assert (tmp_path / "bundle_manifest.json").exists()
    json.loads((tmp_path / "timeline.json").read_text())
