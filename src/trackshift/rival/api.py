"""Public C10 rival-belief boundary (CP-07).

The API returns an ``INFERRED`` distribution only when a versioned CPU model
is supplied.  Calling it without a loaded artifact fails explicitly instead
of fabricating a tactical state.
"""

from .model import (
    M08_SCHEMA_VERSION,
    MODEL_SCHEMA_VERSION,
    MODEL_VERSION,
    ManifestMismatchError,
    ModelUnavailableError,
    RivalModel,
    benchmark,
    benchmark_candidates,
    build_battle_sequences,
    extract_observation,
    fit_centroid,
    fit_model,
    load_m08_sequences,
    load_model,
    rival_state as _rival_state,
)
from .synthetic import BENCHMARK_SEED, REGRESSION_SEED, STATES, SYNTHETIC_PROVENANCE, SyntheticConfig, generate
from .era import (c10_prediction_evidence, evaluate_era_strategies, materialise_historical_m08,
                  observation_predictive_nll, posterior_stability)
from typing import Any, Iterable, Mapping, TypedDict


class StateDistribution(TypedDict, total=False):
    """C10 JSON response shape returned by :func:`rival_state`."""

    api_version: str
    p: dict[str, float]
    merged: list[list[str]]
    merged_states: list[list[str]]
    provenance: str
    model_version: str
    split_version: str
    causal_cutoff: int | None
    uncertainty: dict[str, Any]


def rival_state(battle_segments: Iterable[Mapping[str, Any]], model: RivalModel | Mapping[str, Any] | None = None) -> StateDistribution:
    """Return the causal, normalized C10 distribution."""
    return _rival_state(battle_segments, model)

__all__ = [
    "MODEL_VERSION", "MODEL_SCHEMA_VERSION", "M08_SCHEMA_VERSION", "STATES",
    "SYNTHETIC_PROVENANCE", "REGRESSION_SEED", "BENCHMARK_SEED", "SyntheticConfig", "generate",
    "RivalModel", "ManifestMismatchError", "ModelUnavailableError", "extract_observation",
    "build_battle_sequences", "load_m08_sequences", "fit_model", "load_model", "fit_centroid",
    "StateDistribution", "rival_state", "benchmark", "benchmark_candidates", "c10_prediction_evidence",
    "observation_predictive_nll", "posterior_stability", "evaluate_era_strategies", "materialise_historical_m08",
]
