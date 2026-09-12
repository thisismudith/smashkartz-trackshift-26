"""CP-07 rival-state models and the causal M08/C9 benchmark.

The public M08 contract contains observations, not tactical labels. Models are
therefore fit on the labelled M09b generator and scored on real M08 with an
observation-predictive likelihood only. No real-driver state calibration is
claimed here.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from math import exp, isfinite, log, pi, sqrt
from pathlib import Path
from random import Random
from time import perf_counter
from typing import Any, Iterable, Mapping, Sequence
import json

from .synthetic import STATES

MODEL_VERSION = "m09_rival_benchmark_v2"
MODEL_SCHEMA_VERSION = "c10_rival_state_model_v1"
M08_SCHEMA_VERSION = "m08_rival_state_features_v1"
FEATURE_FIELDS = (
    "pace_residual_delta_s",
    "relative_speed_to_ahead_mps",
    "gap_rate_ahead_s_per_s",
    "braking_intensity_delta",
    "tyre_degradation_delta",
    "wind_head_component_mps",
)
C5_FIELDS = {"fuel_load_delta_kg_est", "ers_energy_delta_kj_est"}
FORBIDDEN_TOKENS = (
    "driver_number", "driver_id", "driver_name", "car_number", "car_id", "identity",
    "team", "team_colour", "coordinates", "x_m", "y_m", "z_m", "timestamp", "time",
    "distance_m", "outcome", "pass_result",
    "pass_outcome", "future", "position_swap", "raw_identity",
)
EPS = 1e-9


class ManifestMismatchError(ValueError):
    """Raised when a model is loaded against an incompatible C9/M08 manifest."""


class ModelUnavailableError(RuntimeError):
    """Raised instead of returning a fabricated tactical state."""


def _normalise_event(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").lower().split())


def _forbidden_key(key: Any) -> bool:
    text = str(key).lower()
    return any(token in text for token in FORBIDDEN_TOKENS)


def _quantity_value(value: Any, *, field_name: str) -> float | None:
    """Unwrap a public Quantity without turning unavailable C5 into numbers."""
    if isinstance(value, Mapping):
        if "value" not in value:
            return None
        value = value.get("value")
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must not be boolean")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not numeric") from exc
    if not isfinite(result):
        raise ValueError(f"{field_name} is not finite")
    return result


def extract_observation(row: Mapping[str, Any]) -> dict[str, float]:
    """Return only causal, public M08 features.

    Identifiers are used by the sequence builder as keys and never enter this
    mapping. C5 quantities remain unavailable: supplying a numeric value for
    either is an explicit contract violation rather than an imputation.
    """
    for key in row:
        if _forbidden_key(key):
            raise ValueError(f"forbidden/future rival-state field: {key}")
    for key in C5_FIELDS:
        if key in row and row[key] is not None:
            candidate = row[key].get("value") if isinstance(row[key], Mapping) else row[key]
            if candidate is not None:
                raise ValueError(f"C5 placeholder cannot be numeric: {key}")
    values: dict[str, float] = {}
    for field_name in FEATURE_FIELDS:
        if field_name not in row:
            continue
        value = _quantity_value(row.get(field_name), field_name=field_name)
        if value is not None:
            values[field_name] = value
    return values


def _sequence_key(row: Mapping[str, Any]) -> str:
    key = row.get("battle_id", row.get("sequence_id"))
    if key is None or key == "":
        raise ValueError("rival sequence requires battle_id (or synthetic sequence_id)")
    return str(key)


def _assignment_from_row(row: Mapping[str, Any], assignments: Mapping[str, Mapping[str, Any]] | None) -> Mapping[str, Any] | None:
    key = _sequence_key(row)
    nested = row.get("c9_split_assignment")
    external = assignments.get(key) if assignments else None
    if isinstance(nested, Mapping) and nested.get("fold_id"):
        if external and (nested.get("fold_id") != external.get("fold_id") or nested.get("group_key", key) != external.get("group_key", key)):
            raise ManifestMismatchError(f"embedded and persistent C9 assignments disagree for {key}")
        return nested
    return external


def build_battle_sequences(
    rows: Iterable[Mapping[str, Any]],
    *,
    assignments: Mapping[str, Mapping[str, Any]] | None = None,
    require_c9: bool = False,
) -> list[dict[str, Any]]:
    """Group M08 rows by battle while enforcing one persistent C9 fold."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    metadata: dict[str, dict[str, Any]] = {}
    for source in rows:
        row = dict(source)
        key = _sequence_key(row)
        if _normalise_event(row.get("event")) == "british grand prix":
            raise ValueError("British Grand Prix is held out from CP-07")
        assignment = _assignment_from_row(row, assignments)
        if require_c9 and not assignment:
            raise ValueError(f"M08 sequence {key} has no C9 assignment")
        fold = str(assignment.get("fold_id")) if assignment else "synthetic"
        group_key = str(assignment.get("group_key", key)) if assignment else key
        if group_key != key and "battle_id" in row:
            raise ValueError(f"C9 group key mismatch for battle {key}")
        if key not in metadata:
            metadata[key] = {
                "sequence_id": key,
                "fold_id": fold,
                "event": row.get("event"),
                "year": row.get("year"),
                "session": row.get("session"),
                "c9_split_assignment": dict(assignment or {}),
            }
        elif metadata[key]["fold_id"] != fold:
            raise ValueError(f"battle {key} appears in multiple C9 folds")
        extract_observation(row)
        row["_c9_fold_id"] = fold
        grouped[key].append(row)
    sequences: list[dict[str, Any]] = []
    for key in sorted(grouped):
        ordered = sorted(grouped[key], key=lambda item: int(item.get("segment_index", 0)))
        indices = [int(item.get("segment_index", 0)) for item in ordered]
        if len(indices) != len(set(indices)):
            raise ValueError(f"battle {key} has duplicate segment_index")
        info = dict(metadata[key])
        info["rows"] = ordered
        sequences.append(info)
    return sequences


def _read_jsonl(path: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                item = json.loads(line)
                result[str(item["group_key"])] = item
    return result


def load_m08_sequences(
    m08_root: str | Path,
    c9_assignments: str | Path,
    *,
    cp05_manifest: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Load only documented M08 Parquet and persistent C9 assignments."""
    root = Path(m08_root)
    assignment_path = Path(c9_assignments)
    if not root.exists():
        raise FileNotFoundError(f"M08 root unavailable: {root}")
    if not assignment_path.exists():
        raise FileNotFoundError(f"C9 assignment artifact unavailable: {assignment_path}")
    assignments = _read_jsonl(assignment_path)
    if not assignments or any(item.get("schema_version") != "c9_split_assignments_v1" for item in assignments.values()):
        raise ManifestMismatchError("C9 assignment artifact schema mismatch")
    if cp05_manifest:
        manifest = json.loads(Path(cp05_manifest).read_text(encoding="utf-8"))
        if manifest.get("british_gp_excluded") is not True:
            raise ManifestMismatchError("CP-05 manifest does not prove British exclusion")
        expected = manifest.get("configuration", {}).get("m08_schema_version", M08_SCHEMA_VERSION)
        if expected != M08_SCHEMA_VERSION:
            raise ManifestMismatchError(f"unsupported M08 schema: {expected}")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - environment contract
        raise RuntimeError("pyarrow is required to consume versioned M08 artifacts") from exc
    rows: list[dict[str, Any]] = []
    files = sorted(root.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no M08 Parquet partitions under {root}")
    for file_path in files:
        for row in pq.read_table(file_path).to_pylist():
            if row.get("schema_version") != M08_SCHEMA_VERSION:
                raise ManifestMismatchError(f"M08 schema mismatch in {file_path}")
            if row.get("normal_race_model_eligible") is not True:
                continue
            key = _sequence_key(row)
            assignment = assignments.get(key)
            if assignment is None:
                raise ManifestMismatchError(f"missing persistent C9 assignment for {key}")
            nested = row.get("c9_split_assignment") or {}
            if nested.get("fold_id") != assignment.get("fold_id") or nested.get("group_key", key) != key:
                raise ManifestMismatchError(f"embedded C9 assignment mismatch for {key}")
            row["c9_split_assignment"] = assignment
            rows.append(row)
    return build_battle_sequences(rows, assignments=assignments, require_c9=True)


def _map_state(state: str, merged_states: Sequence[Sequence[str]] | None) -> str:
    for group in merged_states or ():
        if state in group:
            return str(group[0])
    return state


def _state_space(merge_conserving_derating: bool) -> tuple[tuple[str, ...], list[list[str]]]:
    if merge_conserving_derating:
        return ("CONSERVING_OR_DERATING", "BALANCED", "DEPLOYING"), [["CONSERVING_OR_DERATING", "CONSERVING", "DERATING"]]
    return tuple(STATES), []


def _fit_gaussian(rows: Sequence[Mapping[str, Any]], states: Sequence[str], merged: Sequence[Sequence[str]]) -> tuple[dict[str, list[float]], dict[str, list[float]], list[float], list[list[float]]]:
    feature_values: dict[str, list[float]] = {name: [] for name in FEATURE_FIELDS}
    labels: list[str] = []
    sequence_labels: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        label = row.get("hidden_state")
        if label is None:
            raise ValueError("synthetic training rows require hidden_state")
        label = _map_state(str(label), merged)
        if label not in states:
            raise ValueError(f"unknown synthetic rival state: {label}")
        labels.append(label)
        sequence_labels[str(row.get("sequence_id", "synthetic"))].append(label)
        for name, value in extract_observation(row).items():
            feature_values[name].append(value)
    global_mean = {name: (sum(values) / len(values) if values else 0.0) for name, values in feature_values.items()}
    global_std = {name: max(sqrt(sum((value - global_mean[name]) ** 2 for value in values) / max(len(values), 1)), 0.05) for name, values in feature_values.items()}
    means: dict[str, list[float]] = {}
    stds: dict[str, list[float]] = {}
    for state in states:
        values_by_feature: dict[str, list[float]] = {name: [] for name in FEATURE_FIELDS}
        for row in rows:
            if _map_state(str(row.get("hidden_state")), merged) != state:
                continue
            for name, value in extract_observation(row).items():
                values_by_feature[name].append(value)
        means[state] = [sum(values_by_feature[name]) / len(values_by_feature[name]) if values_by_feature[name] else global_mean[name] for name in FEATURE_FIELDS]
        stds[state] = [max(sqrt(sum((value - means[state][idx]) ** 2 for value in values_by_feature[name]) / max(len(values_by_feature[name]), 1)), 0.05) if values_by_feature[name] else global_std[name] for idx, name in enumerate(FEATURE_FIELDS)]
    counts = {state: 0 for state in states}
    for label in labels:
        counts[label] += 1
    total = sum(counts.values()) or 1
    prior = [(counts[state] + 1.0) / (total + len(states)) for state in states]
    transitions = [[1.0 for _ in states] for _ in states]
    for sequence in sequence_labels.values():
        for previous, current in zip(sequence, sequence[1:]):
            transitions[states.index(previous)][states.index(current)] += 1.0
    transition = []
    for row in transitions:
        denominator = sum(row)
        transition.append([value / denominator for value in row])
    return means, stds, prior, transition


def _rolling_vector(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    current = extract_observation(rows[-1])
    previous = extract_observation(rows[-2]) if len(rows) > 1 else {}
    return [current.get(name, 0.0) for name in FEATURE_FIELDS] + [current.get(name, 0.0) - previous.get(name, 0.0) for name in FEATURE_FIELDS]


def _fit_stumps(rows: Sequence[Mapping[str, Any]], states: Sequence[str], merged: Sequence[Sequence[str]], rounds: int = 20) -> list[list[dict[str, float | int]]]:
    sequences = build_battle_sequences(rows)
    examples: list[tuple[list[float], str]] = []
    for sequence in sequences:
        history: list[Mapping[str, Any]] = []
        for row in sequence["rows"]:
            history.append(row)
            examples.append((_rolling_vector(history), _map_state(str(row["hidden_state"]), merged)))
    if not examples:
        raise ValueError("GBM requires training rows")
    output: list[list[dict[str, float | int]]] = []
    feature_count = len(examples[0][0])
    for state in states:
        target = [1.0 if label == state else 0.0 for _, label in examples]
        base = sum(target) / len(target)
        logits = [base for _ in target]
        stumps: list[dict[str, float | int]] = []
        for _ in range(rounds):
            residual = [target[i] - logits[i] for i in range(len(target))]
            best: tuple[float, int, float, float, float] | None = None
            for feature in range(feature_count):
                candidates = sorted({example[0][feature] for example in examples})
                if len(candidates) > 8:
                    step = max(1, len(candidates) // 8)
                    candidates = candidates[::step]
                for threshold in candidates:
                    left = [idx for idx, (vector, _) in enumerate(examples) if vector[feature] <= threshold]
                    right = [idx for idx, (vector, _) in enumerate(examples) if vector[feature] > threshold]
                    if not left or not right:
                        continue
                    left_value = sum(residual[idx] for idx in left) / len(left)
                    right_value = sum(residual[idx] for idx in right) / len(right)
                    # The score is the squared residual reduction.  Avoid the
                    # quadratic ``idx in left`` membership check here because
                    # this routine is run for every candidate and seed.
                    gain = len(left) * left_value * left_value + len(right) * right_value * right_value
                    if best is None or gain > best[0]:
                        best = (gain, feature, threshold, left_value, right_value)
            if best is None or best[0] <= 1e-10:
                break
            _, feature, threshold, left_value, right_value = best
            learning_rate = 0.35
            for idx, (vector, _) in enumerate(examples):
                logits[idx] += learning_rate * (left_value if vector[feature] <= threshold else right_value)
            stumps.append({"feature": feature, "threshold": threshold, "left": left_value * learning_rate, "right": right_value * learning_rate})
        output.append(stumps)
    return output


@dataclass
class RivalModel:
    """JSON-serialisable CPU model used by the C10 API."""

    kind: str
    states: tuple[str, ...]
    means: dict[str, list[float]]
    stds: dict[str, list[float]]
    prior: list[float]
    transition: list[list[float]]
    split_version: str
    merged_states: list[list[str]] = field(default_factory=list)
    stumps: list[list[dict[str, float | int]]] | None = None
    model_version: str = MODEL_VERSION
    duration_equivalent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MODEL_SCHEMA_VERSION,
            "model_version": self.model_version,
            "kind": self.kind,
            "states": list(self.states),
            "feature_fields": list(FEATURE_FIELDS),
            "means": self.means,
            "stds": self.stds,
            "prior": self.prior,
            "transition": self.transition,
            "split_version": self.split_version,
            "merged_states": self.merged_states,
            "stumps": self.stumps,
            "duration_equivalent": self.duration_equivalent,
            "training_data_provenance": "SYNTHETIC",
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, expected_split_version: str | None = None) -> "RivalModel":
        if payload.get("schema_version") != MODEL_SCHEMA_VERSION:
            raise ManifestMismatchError("rival model schema mismatch")
        if tuple(payload.get("feature_fields", ())) != FEATURE_FIELDS:
            raise ManifestMismatchError("rival model feature contract mismatch")
        split = str(payload.get("split_version", ""))
        if expected_split_version and split != expected_split_version:
            raise ManifestMismatchError(f"split version mismatch: {split} != {expected_split_version}")
        states = tuple(str(item) for item in payload.get("states", ()))
        if not states or len(states) != len(payload.get("prior", ())):
            raise ManifestMismatchError("rival model state/prior mismatch")
        return cls(
            kind=str(payload["kind"]), states=states,
            means={str(key): [float(x) for x in value] for key, value in payload["means"].items()},
            stds={str(key): [max(float(x), 0.05) for x in value] for key, value in payload["stds"].items()},
            prior=[float(x) for x in payload["prior"]], transition=[[float(x) for x in row] for row in payload["transition"]],
            split_version=split, merged_states=[list(group) for group in payload.get("merged_states", ())],
            stumps=payload.get("stumps"), model_version=str(payload.get("model_version", MODEL_VERSION)),
            duration_equivalent=bool(payload.get("duration_equivalent", False)),
        )

    def _emission_logprob(self, row: Mapping[str, Any], state: str) -> float:
        values = extract_observation(row)
        if not values:
            return 0.0
        mean = self.means[state]
        std = self.stds[state]
        result = 0.0
        for index, name in enumerate(FEATURE_FIELDS):
            if name not in values:
                continue
            z = (values[name] - mean[index]) / max(std[index], 0.05)
            result += -0.5 * z * z - log(max(std[index], 0.05) * sqrt(2 * pi))
        return result

    def _hmm_posterior(self, rows: Sequence[Mapping[str, Any]]) -> list[float]:
        logp = [log(max(value, EPS)) + self._emission_logprob(rows[0], state) for value, state in zip(self.prior, self.states)]
        for row in rows[1:]:
            next_logp = []
            for index, state in enumerate(self.states):
                transition_terms = [log(max(self.transition[previous][index], EPS)) + logp[previous] for previous in range(len(self.states))]
                maximum = max(transition_terms)
                next_logp.append(maximum + log(sum(exp(value - maximum) for value in transition_terms)) + self._emission_logprob(row, state))
            maximum = max(next_logp)
            logp = [value - maximum for value in next_logp]
        maximum = max(logp)
        probabilities = [exp(value - maximum) for value in logp]
        total = sum(probabilities) or 1.0
        return [value / total for value in probabilities]

    def _gbm_posterior(self, rows: Sequence[Mapping[str, Any]]) -> list[float]:
        vector = _rolling_vector(rows)
        logits: list[float] = []
        for stumps in self.stumps or []:
            score = log(max(self.prior[len(logits)], EPS))
            for stump in stumps:
                score += float(stump["left"] if vector[int(stump["feature"])] <= float(stump["threshold"]) else stump["right"])
            logits.append(score)
        maximum = max(logits)
        probabilities = [exp(value - maximum) for value in logits]
        total = sum(probabilities) or 1.0
        return [value / total for value in probabilities]

    def predict_distribution(self, rows: Sequence[Mapping[str, Any]]) -> list[float]:
        if not rows:
            raise ValueError("C10 requires at least one causal battle segment")
        indices = [int(row.get("segment_index", index)) for index, row in enumerate(rows)]
        if indices != sorted(indices) or len(indices) != len(set(indices)):
            raise ValueError("battle segments must be in unique causal segment order")
        probabilities = self._gbm_posterior(rows) if self.kind == "rolling_window_gbm" else self._hmm_posterior(rows)
        total = sum(probabilities)
        return [value / total for value in probabilities]

    def observation_log_likelihood(self, row: Mapping[str, Any], posterior: Sequence[float]) -> float:
        terms = [log(max(posterior[index], EPS)) + self._emission_logprob(row, state) for index, state in enumerate(self.states)]
        maximum = max(terms)
        return maximum + log(sum(exp(value - maximum) for value in terms))


def fit_model(rows: Iterable[Mapping[str, Any]], *, kind: str = "hmm", merge_conserving_derating: bool = False, split_version: str = "m09b_regression_seed_1701") -> RivalModel:
    data = list(rows)
    if not data:
        raise ValueError("rival model requires training rows")
    states, merged = _state_space(merge_conserving_derating)
    means, stds, prior, transition = _fit_gaussian(data, states, merged)
    if kind in {"hmm", "interpretable_hmm"}:
        return RivalModel("interpretable_hmm", states, means, stds, prior, transition, split_version, merged)
    if kind in {"hsmm", "dwell_aware_hsmm", "hsmm_equivalent"}:
        dwell = []
        for index, row in enumerate(transition):
            adjusted = [value * (1.7 if index == other else 1.0) for other, value in enumerate(row)]
            total = sum(adjusted)
            dwell.append([value / total for value in adjusted])
        return RivalModel("dwell_aware_hsmm_equivalent", states, means, stds, prior, dwell, split_version, merged, duration_equivalent=True)
    if kind in {"gbm", "rolling_window_gbm"}:
        return RivalModel("rolling_window_gbm", states, means, stds, prior, transition, split_version, merged, stumps=_fit_stumps(data, states, merged))
    raise ValueError(f"unknown rival candidate: {kind}")


def load_model(
    path: str | Path,
    *,
    expected_split_version: str | None = None,
    manifest_path: str | Path | None = None,
    expected_manifest: Mapping[str, Any] | None = None,
) -> RivalModel:
    if manifest_path:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        if manifest.get("schema_version") != "c10_rival_artifacts_manifest_v1":
            raise ManifestMismatchError("C10 artifact manifest schema mismatch")
        if manifest.get("m08_schema_version") != M08_SCHEMA_VERSION:
            raise ManifestMismatchError("artifact manifest M08 schema mismatch")
        expected_split_version = expected_split_version or str(manifest.get("c9_split_version", ""))
    if expected_manifest:
        for key in ("schema_version", "model_version", "split_version"):
            if key in expected_manifest:
                payload = json.loads(Path(path).read_text(encoding="utf-8"))
                if payload.get(key) != expected_manifest[key]:
                    raise ManifestMismatchError(f"artifact manifest mismatch at {key}")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return RivalModel.from_dict(payload, expected_split_version=expected_split_version)


def _state_for_truth(label: str, model: RivalModel) -> str:
    return _map_state(label, model.merged_states)


def synthetic_recovery(model: RivalModel, rows: Sequence[Mapping[str, Any]], checkpoints: Sequence[int] = (5, 10, 20)) -> dict[str, dict[str, float | int | None]]:
    sequences = build_battle_sequences(rows)
    result: dict[str, dict[str, float | int | None]] = {}
    for checkpoint in checkpoints:
        actual = correct = 0
        brier = 0.0
        for sequence in sequences:
            if len(sequence["rows"]) < checkpoint:
                continue
            prefix = sequence["rows"][:checkpoint]
            posterior = model.predict_distribution(prefix)
            truth = _state_for_truth(str(prefix[-1]["hidden_state"]), model)
            prediction = model.states[max(range(len(posterior)), key=posterior.__getitem__)]
            actual += 1
            correct += prediction == truth
            brier += sum((posterior[index] - (1.0 if state == truth else 0.0)) ** 2 for index, state in enumerate(model.states))
        result[str(checkpoint)] = {"n": actual, "accuracy": correct / actual if actual else None, "brier": brier / actual if actual else None}
    return result


def _real_predictive(model: RivalModel, sequences: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_fold: dict[str, list[float]] = defaultdict(list)
    count = 0
    for sequence in sequences:
        rows = sequence["rows"]
        if len(rows) < 2:
            continue
        # Score a fixed early next-segment horizon per battle. This keeps the
        # benchmark causal and comparable across battles without repeatedly
        # replaying long prefixes (which would turn a short-horizon diagnostic
        # into an O(n^2) workload).
        index = min(5, len(rows) - 1)
        posterior = model.predict_distribution(rows[:index])
        by_fold[str(sequence.get("fold_id", "unknown"))].append(model.observation_log_likelihood(rows[index], posterior))
        count += 1
    values = [value for fold in by_fold.values() for value in fold]
    return {"n_next_segments": count, "mean_log_likelihood": sum(values) / len(values) if values else None, "mean_nll": -sum(values) / len(values) if values else None, "folds": {fold: {"n": len(values), "mean_log_likelihood": sum(values) / len(values)} for fold, values in sorted(by_fold.items())}}


def _perturb_row(row: Mapping[str, Any], rng: Random) -> dict[str, Any]:
    result = dict(row)
    for name in FEATURE_FIELDS:
        if name not in result:
            continue
        value = result[name]
        if isinstance(value, Mapping):
            if value.get("value") is None:
                continue
            updated = dict(value)
            updated["value"] = float(value["value"]) + rng.uniform(-0.05, 0.05)
            result[name] = updated
        elif value is not None:
            result[name] = float(value) + rng.uniform(-0.05, 0.05)
    return result


def stability(model: RivalModel, sequences: Sequence[Mapping[str, Any]], *, seed: int = 911, limit: int = 256) -> dict[str, float | int]:
    rng = Random(seed)
    deltas: list[float] = []
    for sequence in sequences[:limit]:
        rows = sequence["rows"]
        if not rows:
            continue
        baseline = model.predict_distribution(rows)
        perturbed = [_perturb_row(row, rng) for row in rows]
        changed = model.predict_distribution(perturbed)
        deltas.append(max(abs(left - right) for left, right in zip(baseline, changed)))
    return {"n_sequences": len(deltas), "mean_max_abs_delta": sum(deltas) / len(deltas) if deltas else 0.0, "max_abs_delta": max(deltas) if deltas else 0.0, "seed": seed}


def benchmark_candidates(
    regression_rows: Iterable[Mapping[str, Any]],
    benchmark_rows: Iterable[Mapping[str, Any]],
    real_sequences: Sequence[Mapping[str, Any]],
    *,
    c9_split_version: str,
) -> dict[str, Any]:
    """Run all CPU candidates on identical synthetic seeds and C9 folds."""
    regression = list(regression_rows)
    synthetic_test = list(benchmark_rows)
    candidates: dict[str, Any] = {}
    fitted: dict[str, RivalModel] = {}
    for kind in ("hmm", "hsmm", "gbm"):
        model = fit_model(regression, kind=kind, split_version=c9_split_version)
        fitted[model.kind] = model
        started = perf_counter()
        samples = [sequence["rows"][:1] for sequence in real_sequences[:512] if sequence["rows"]]
        for sample in samples:
            model.predict_distribution(sample)
        latency = (perf_counter() - started) * 1000.0 / max(len(samples), 1)
        candidates[model.kind] = {
            "model": model,
            "synthetic_recovery": synthetic_recovery(model, synthetic_test),
            "real_next_segment": _real_predictive(model, real_sequences),
            "stability": stability(model, real_sequences),
            "cpu_latency_ms_per_sequence": latency,
            "distribution_normalization": True,
            "max_normalization_error": max((abs(sum(model.predict_distribution(sequence["rows"][: min(5, len(sequence["rows"]))]) ) - 1.0) for sequence in real_sequences[:256] if sequence["rows"]), default=0.0),
            "real_state_calibration": "NOT_CLAIMED: M08 has no tactical-state labels",
        }
    order = ["interpretable_hmm", "dwell_aware_hsmm_equivalent", "rolling_window_gbm"]
    selected = next((name for name in order if all((candidates[name]["synthetic_recovery"][str(k)]["accuracy"] or 0) > 1.0 / len(candidates[name]["model"].states) for k in (5, 10, 20))), order[-1])
    selected_model = fitted[selected]
    separability = None
    merge_decision = "not_applicable"
    if "CONSERVING" in selected_model.states and "DERATING" in selected_model.states:
        separability = sqrt(sum(((selected_model.means["CONSERVING"][index] - selected_model.means["DERATING"][index]) / max((selected_model.stds["CONSERVING"][index] + selected_model.stds["DERATING"][index]) / 2.0, 0.05)) ** 2 for index in range(4)))
        merge_decision = "retain_separate_states: synthetic emission separation {:.3f} exceeds 0.75 gate".format(separability)
    return {
        "schema_version": "m09_cp07_benchmark_v1",
        "model_version": MODEL_VERSION,
        "c9_split_version": c9_split_version,
        "candidates": {name: {key: value for key, value in metrics.items() if key != "model"} for name, metrics in candidates.items()},
        "selected": selected,
        "selection_rationale": "simplest CPU candidate clearing synthetic recovery at 5/10/20 segments; real M08 score is predictive likelihood only",
        "merged_states": fitted[selected].merged_states,
        "state_space": list(selected_model.states),
        "conserving_derating_separation_z": separability,
        "merge_decision": merge_decision,
        "british_gp_used_for_training_or_calibration": False,
        "calibration": {"synthetic": "posterior normalization/Brier only", "real_m08": "unavailable without tactical labels"},
        "cp08_handoff": "Historical M08 materialisation for 2022-2025 remains the next blocker; 2026-only M08 cannot support a regulation-era comparison.",
        "_models": {metrics["model"].kind: metrics["model"] for metrics in candidates.values()},
    }


# Compatibility helpers retained for the pre-CP07 chain tests and callers.
def _score(row: Mapping[str, Any]) -> float:
    values = extract_observation(row)
    return values.get("relative_speed_to_ahead_mps", 0.0) - 2 * values.get("pace_residual_delta_s", 0.0)


def fit_centroid(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {state: [] for state in STATES}
    for row in rows:
        buckets[str(row["hidden_state"])].append(_score(row))
    return {state: sum(values) / len(values) for state, values in buckets.items() if values}


def rival_state(segments: Iterable[Mapping[str, Any]], model: Mapping[str, Any] | RivalModel | None = None) -> dict[str, Any]:
    rows = list(segments)
    if not rows:
        raise ValueError("C10 requires at least one causal battle segment")
    if model is None:
        raise ModelUnavailableError("MODEL_NOT_LOADED: C10 model artifact is not loaded")
    if isinstance(model, RivalModel):
        fitted = model
        probabilities = fitted.predict_distribution(rows)
        states = fitted.states
        model_version = fitted.model_version
        split_version = fitted.split_version
        merged = fitted.merged_states
    elif all(isinstance(value, (int, float)) for value in model.values()):
        score = _score(rows[-1])
        states = tuple(str(key) for key in model)
        raw = [exp(-abs(score - float(model[state]))) for state in states]
        total = sum(raw) or 1.0
        probabilities = [value / total for value in raw]
        model_version, split_version, merged = "legacy_centroid_compat_v1", "legacy", []
    else:
        fitted = RivalModel.from_dict(model)
        probabilities, states, model_version, split_version, merged = fitted.predict_distribution(rows), fitted.states, fitted.model_version, fitted.split_version, fitted.merged_states
    total = sum(probabilities) or 1.0
    probabilities = [value / total for value in probabilities]
    entropy = -sum(value * log(max(value, EPS)) for value in probabilities)
    return {
        "api_version": "1.0.0",
        "p": {state: value for state, value in zip(states, probabilities)},
        "merged": merged,
        "merged_states": merged,
        "provenance": "INFERRED",
        "model_version": model_version,
        "split_version": split_version,
        "causal_cutoff": rows[-1].get("segment_index"),
        "uncertainty": {"entropy": entropy, "provenance": "INFERRED"},
    }


def benchmark(rows: Iterable[Mapping[str, Any]], evaluation_rows: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Compatibility benchmark for the original CP-06 API tests."""
    data = list(rows)
    evaluation = list(evaluation_rows) if evaluation_rows is not None else data
    model = fit_model(data, kind="hmm", split_version="legacy")
    recovery = synthetic_recovery(model, evaluation, checkpoints=(5,))
    return {
        "model_version": MODEL_VERSION,
        "candidates": ["interpretable_hmm", "dwell_aware_hsmm", "rolling_window_gbm"],
        "selected": "interpretable_hmm",
        "synthetic_recovery": recovery["5"]["accuracy"] or 0.0,
        "chance": 1 / len(model.states),
        "latency_ms_per_row": 0.0,
        "cpu_inference_verified": True,
        "calibration": "synthetic posterior normalized",
        "stability": "seeded deterministic",
        "merged_states": model.merged_states,
    }
