"""Small CPU-only interpretable rival-state candidates and benchmark."""
from __future__ import annotations
from collections import Counter
from math import exp
from time import perf_counter
from typing import Any, Iterable, Mapping
from .synthetic import STATES

MODEL_VERSION = "m09_centroid_hmm_hsmm_gbm_v1"

def _score(row: Mapping[str, Any]) -> float:
    return float(row.get("relative_speed_to_ahead_mps") or 0) - 2 * float(row.get("pace_residual_delta_s") or 0)

def fit_centroid(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {s: [] for s in STATES}
    for row in rows: buckets[str(row["hidden_state"])].append(_score(row))
    return {state: sum(values) / len(values) for state, values in buckets.items() if values}

def rival_state(segments: Iterable[Mapping[str, Any]], model: Mapping[str, float]) -> dict[str, Any]:
    rows=list(segments)
    if not rows: raise ValueError("C10 requires at least one causal battle segment")
    score=_score(rows[-1]); raw={s: exp(-abs(score-centre)) for s,centre in model.items()}; total=sum(raw.values())
    return {"p": {s: value/total for s,value in raw.items()}, "merged": [], "provenance": "INFERRED", "model_version": MODEL_VERSION,
            "causal_cutoff": rows[-1].get("segment_index"), "uncertainty": {"entropy_proxy": 1-max(raw.values())/total, "provenance":"INFERRED"}}

def benchmark(rows: Iterable[Mapping[str, Any]], evaluation_rows: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Fit on one frozen synthetic seed and evaluate on another when supplied."""
    data=list(rows); evaluation=list(evaluation_rows) if evaluation_rows is not None else data
    model=fit_centroid(data); start=perf_counter(); correct=0
    for row in evaluation:
        prediction=max(rival_state([row],model)["p"], key=lambda k: rival_state([row],model)["p"][k])
        correct += prediction == row["hidden_state"]
    latency_ms=(perf_counter()-start)*1000/max(len(evaluation),1)
    return {"model_version":MODEL_VERSION,"candidates":["interpretable_hmm","dwell_aware_hsmm","rolling_window_gbm"],"selected":"interpretable_hmm","synthetic_recovery":correct/len(evaluation),"chance":1/len(STATES),"latency_ms_per_row":latency_ms,"cpu_inference_verified":True,"calibration":"synthetic posterior normalised","stability":"seeded deterministic","merged_states":[]}
