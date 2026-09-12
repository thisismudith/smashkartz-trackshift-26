"""Public Owner A boundary for the future value and shadow-price contract.

Rishabh CP-09 and CP-10 provide the implementation. This module intentionally
does not fabricate legal actions, transitions, or value estimates.
"""

from .state import (DISCRETIZATION_VERSION, STUB_RESPONSE, StrategicStateAdapterError, battle_step_to_strategic_state, dependency_stub, discretize_state, reject_stubs_for_final)
__all__ = ["DISCRETIZATION_VERSION", "STUB_RESPONSE", "StrategicStateAdapterError", "battle_step_to_strategic_state", "dependency_stub", "discretize_state", "reject_stubs_for_final"]
