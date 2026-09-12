from __future__ import annotations
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trackshift.contracts.strategic_state import validate_strategic_state
from trackshift.value import state as strategic_state_module
from trackshift.value.api import battle_step_to_strategic_state, c3_candidate_actions, c3_excluded_actions, dependency_stub, discretize_state, reject_stubs_for_final, STUB_RESPONSE, StrategicStateAdapterError

def step(): return {"year":2026,"event":"test_gp","session":"Race","lap":1,"segment_id":2,"distance_m":10.,"normal_race_model_eligible":True,"time_gap_s":.8,"distance_gap_m":30.,"relative_speed_mps":2.,"gap_rate_s_per_s":-.1,"attacker_tyre_compound":"MEDIUM"}
def test_adapter_validates_and_json_round_trips():
 s=battle_step_to_strategic_state(step()); assert validate_strategic_state(json.loads(json.dumps(s))) == s
def test_future_and_unavailable_are_honest():
 with pytest.raises(StrategicStateAdapterError): battle_step_to_strategic_state(step() | {"future_gap_s": 0})
 s=battle_step_to_strategic_state(step()); assert s["energy"]["ers_soc_est_mj"]["reason"]
def test_discretization_is_deterministic_and_nulls_are_not_binned():
 s=battle_step_to_strategic_state(step()); a=discretize_state(s); assert a == discretize_state(s); assert a["schema_version"] == "strategic_state_discretization_v1"; assert a["bins"]["energy_state_mj"] is None
def test_stubs_are_marked_and_rejected_from_final():
 stub=dependency_stub("C4"); assert stub["marker"] == STUB_RESPONSE and stub["provenance"] == "STUB" and stub["reason"]
 with pytest.raises(StrategicStateAdapterError): reject_stubs_for_final(stub)
def test_cpu_only_import():
 assert "torch" not in sys.modules


def _public_c3_response():
 return {
  "legal_actions": [
   {"deploy_level": 0.0, "lift_amount": 0.0, "provenance": "RULE"},
   {"deploy_level": 0.5, "lift_amount": 0.0, "provenance": "RULE"},
  ],
  "excluded_actions": [{
   "deploy_level": 1.0, "lift_amount": 0.0, "rule": "energy.deploy_limit_per_lap_mj",
   "source": "official rule source", "reason": "remaining deploy budget is insufficient", "provenance": "RULE",
  }],
  "provenance": "RULE",
 }


def test_adapter_calls_only_public_c3_and_forwards_state_and_rules(monkeypatch):
 seen = {}
 event_rules = {"event": "unverified-but-not-invented"}
 def fake_legal_actions(current_state, supplied_rules):
  seen["state"] = current_state
  seen["event_rules"] = supplied_rules
  return _public_c3_response()
 monkeypatch.setattr(strategic_state_module.rules_api, "legal_actions", fake_legal_actions)

 result = battle_step_to_strategic_state(step() | {"speed_kmh": 287.5}, event_rules=event_rules)

 assert seen["event_rules"] is event_rules
 assert seen["state"]["ref"] == result["ref"]
 assert seen["state"]["speed_kmh"] == 287.5
 assert result["legal_actions"] == _public_c3_response()


def test_c3_exclusions_are_audit_only_and_never_candidates(monkeypatch):
 response = _public_c3_response()
 monkeypatch.setattr(strategic_state_module.rules_api, "legal_actions", lambda *_: response)

 action_set = battle_step_to_strategic_state(step(), event_rules={})["legal_actions"]
 candidates = c3_candidate_actions(action_set)
 excluded = c3_excluded_actions(action_set)

 assert candidates == response["legal_actions"]
 assert excluded == response["excluded_actions"]
 assert excluded[0] not in candidates
 assert {action["deploy_level"] for action in candidates} == {0.0, 0.5}
 assert excluded[0]["reason"] and excluded[0]["source"]


def test_c3_action_set_is_json_safe_and_keeps_rule_provenance(monkeypatch):
 monkeypatch.setattr(strategic_state_module.rules_api, "legal_actions", lambda *_: _public_c3_response())

 action_set = json.loads(json.dumps(battle_step_to_strategic_state(step(), event_rules={})))["legal_actions"]

 assert action_set["provenance"] == "RULE"
 for action in c3_candidate_actions(action_set):
  assert action["provenance"] == "RULE"
 for excluded in c3_excluded_actions(action_set):
  assert excluded["provenance"] == "RULE"
  assert excluded["reason"] and excluded["source"]


@pytest.mark.parametrize("c3_result", [
 {"available": False, "provenance": "RULE", "reason": "event configuration unavailable"},
 {"error": "C3 configuration cannot be evaluated", "provenance": "RULE"},
 {"legal_actions": [], "excluded_actions": [], "provenance": "RULE", "reason": "no legal actions configured"},
])
def test_c3_unavailability_is_fail_closed_and_not_a_stub(monkeypatch, c3_result):
 monkeypatch.setattr(strategic_state_module.rules_api, "legal_actions", lambda *_: c3_result)

 action_set = battle_step_to_strategic_state(step(), event_rules={})["legal_actions"]

 assert c3_candidate_actions(action_set) == []
 assert action_set["available"] is False
 assert action_set["provenance"] == "RULE"
 assert action_set["reason"]
 assert action_set.get("marker") != STUB_RESPONSE


def test_c3_error_is_fail_closed_json_safe_and_preserves_provenance(monkeypatch):
 def unavailable_c3(*_):
  raise RuntimeError("rule service unavailable")
 monkeypatch.setattr(strategic_state_module.rules_api, "legal_actions", unavailable_c3)

 result = battle_step_to_strategic_state(step(), event_rules={})
 action_set = json.loads(json.dumps(result))["legal_actions"]

 assert action_set["provenance"] == "RULE"
 assert "rule service unavailable" in action_set["reason"]
 assert c3_candidate_actions(action_set) == []
 assert c3_excluded_actions(action_set) == []
