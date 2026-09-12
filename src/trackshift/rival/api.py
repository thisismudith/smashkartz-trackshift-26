"""Public Owner A boundary for the future C10 rival-belief contract.

Rishabh CP-05 to CP-08 provide the implementation. No tactical-state model is
stubbed here, because a placeholder prediction would be misleading.
"""

from .model import MODEL_VERSION, benchmark, fit_centroid, rival_state
from .synthetic import BENCHMARK_SEED, REGRESSION_SEED, STATES, SYNTHETIC_PROVENANCE, SyntheticConfig, generate
from .era import evaluate_era_strategies

__all__ = ["MODEL_VERSION", "STATES", "SYNTHETIC_PROVENANCE", "REGRESSION_SEED", "BENCHMARK_SEED", "SyntheticConfig", "generate", "fit_centroid", "rival_state", "benchmark", "evaluate_era_strategies"]
