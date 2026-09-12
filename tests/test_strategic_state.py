from __future__ import annotations
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trackshift.contracts.strategic_state import validate_strategic_state
from trackshift.value.api import battle_step_to_strategic_state, dependency_stub, discretize_state, reject_stubs_for_final, STUB_RESPONSE, StrategicStateAdapterError

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
