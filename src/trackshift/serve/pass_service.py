"""Serve the CP-14 pass model behind ``POST /pass/predict`` (CP-24).

The route already existed as a synthetic logistic curve, honestly labelled
``calibration: synthetic-development``. This binds it to the real artifact when
one is on disk, and -- just as importantly -- reports which of the two answered,
so the replay bundle can refuse to ship a demo built on a stub.

Three things this enforces that a thin wrapper around ``predict_proba`` would
not:

**Checkpoint scope.** A ``DETECTION`` request carrying ``speed_at_activation_kmh``
is refused with ``CHECKPOINT_VIOLATION``. The value is not knowable at the
Detection Line, so a caller supplying it is either confused or leaking, and
answering would return a confident probability for a decision that cannot be
made. This is the serving half of CP-13's leakage guarantee.

**Provenance on every number.** The probability comes back as a ``Quantity``
with its provenance, the artifact version and the calibration method that
produced it. A bare float reaching the UI is a number with no way to tell
whether a model or a placeholder produced it.

**Model eligibility.** A request whose state is not ``normal_race_model_eligible``
is refused with ``NOT_MODEL_ELIGIBLE`` rather than scored. The model was trained
on green-flag, normal-race rows only (CP-13); under a Safety Car its output is
an extrapolation nobody asked for.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "PASS_SERVICE_SCHEMA_VERSION",
    "PassServiceError",
    "CheckpointViolation",
    "NotModelEligible",
    "ModelUnavailable",
    "PassPredictor",
    "load_predictor",
]

PASS_SERVICE_SCHEMA_VERSION = "cp24_pass_service_v1"

#: Features that are not knowable at each checkpoint. Mirrors the registry's
#: ``decision_checkpoint`` scoping; kept as an explicit list here because the
#: serving path must refuse a *request*, not merely drop a column.
FORBIDDEN_AT: dict[str, frozenset[str]] = {
    "DETECTION": frozenset({
        "gap_at_activation_s", "speed_at_activation_kmh",
        "distance_activation_to_brake", "speed_at_braking_kmh",
    }),
    "ACTIVATION": frozenset({"speed_at_braking_kmh"}),
    "BRAKING": frozenset(),
}


class PassServiceError(RuntimeError):
    """Base for every refusal this service makes."""

    code = "PASS_SERVICE_ERROR"

    def as_error(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": str(self)}}


class CheckpointViolation(PassServiceError):
    """The request carries a feature its checkpoint cannot know."""

    code = "CHECKPOINT_VIOLATION"


class NotModelEligible(PassServiceError):
    """The state is outside the normal-race rows the model was trained on."""

    code = "NOT_MODEL_ELIGIBLE"


class ModelUnavailable(PassServiceError):
    """No CP-14 artifact on disk for this checkpoint."""

    code = "MODEL_UNAVAILABLE"


@dataclass
class PassPredictor:
    """One checkpoint's fitted model, plus what it needs to answer honestly."""

    checkpoint: str
    family: str
    model: Any
    numeric: tuple[str, ...]
    categorical: tuple[str, ...]
    manifest: Mapping[str, Any] = field(default_factory=dict)
    #: Where the artifact came from, for the response and the bundle manifest.
    artifact_version: str = "unknown"

    @property
    def features(self) -> tuple[str, ...]:
        return tuple(self.numeric) + tuple(self.categorical)

    def assert_checkpoint_scope(self, payload: Mapping[str, Any]) -> None:
        """Refuse a request carrying a feature this checkpoint cannot know."""
        forbidden = FORBIDDEN_AT.get(self.checkpoint, frozenset())
        present = sorted(name for name in forbidden
                         if payload.get(name) is not None)
        if present:
            raise CheckpointViolation(
                f"{present} cannot be known at {self.checkpoint}. Supplying them would "
                "return a confident probability for a decision that cannot be made "
                "there; this is the serving half of CP-13's leakage guarantee.")

    def assert_eligible(self, payload: Mapping[str, Any]) -> None:
        """Refuse a state the model was never trained on.

        Absent is treated as eligible: most callers will not send the flag, and
        refusing everything unflagged would make the route unusable. An explicit
        ``false`` is refused, which is the case that matters.
        """
        flag = payload.get("normal_race_model_eligible")
        if flag is None:
            state = payload.get("state") or {}
            flag = state.get("normal_race_model_eligible") if isinstance(state, Mapping) else None
        if flag is not None and not bool(flag):
            raise NotModelEligible(
                "normal_race_model_eligible is false. The pass model is fitted on "
                "green-flag normal-race rows only (CP-13), so under a Safety Car, VSC "
                "or pit sequence its output would be an extrapolation, not a prediction.")

    def _row(self, payload: Mapping[str, Any]):
        """One-row frame in the model's own schema, missing features left missing."""
        import pandas as pd

        values: dict[str, Any] = {}
        for name in self.numeric:
            raw = payload.get(name)
            values[name] = None if raw is None else float(raw)
        for name in self.categorical:
            raw = payload.get(name)
            values[name] = None if raw is None else str(raw)
        frame = pd.DataFrame([values], columns=list(self.features))
        for name in self.numeric:
            frame[name] = pd.to_numeric(frame[name], errors="coerce")
        return frame

    def predict(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """``P(pass)`` for one opportunity, with provenance and its inputs named."""
        self.assert_checkpoint_scope(payload)
        self.assert_eligible(payload)

        supplied = [n for n in self.features if payload.get(n) is not None]
        missing = [n for n in self.features if payload.get(n) is None]
        probability = float(self.model.predict_proba(self._row(payload))[0])

        return {
            "p_pass_by_outcome_horizon": {
                "value": probability,
                "unit": None,
                "provenance": "INFERRED",
                "model": f"M10/{self.family}",
                "artifact_version": self.artifact_version,
            },
            "checkpoint": self.checkpoint,
            "calibration": self.manifest.get("calibration", "uncalibrated"),
            "features_supplied": supplied,
            # Named rather than silently imputed: a probability produced from two
            # of seventeen features is a different claim from one produced from
            # all of them, and the caller cannot tell without this.
            "features_missing": missing,
            "evidence_grade": (self.manifest.get("run") or {}).get("evidence_grade"),
            "is_stub": False,
        }


def _artifact_root(root: Path, version: str, checkpoint: str, family: str | None) -> Path | None:
    base = Path(root) / version / checkpoint.lower()
    if not base.is_dir():
        return None
    if family:
        candidate = base / family
        return candidate if (candidate / "model.pkl").exists() else None
    # No family named: take whichever the benchmark ranked first, which
    # write_fit_artifact records as selection_rank 1.
    best: tuple[int, Path] | None = None
    for directory in sorted(base.iterdir()):
        metrics_path = directory / "metrics.json"
        if not (directory / "model.pkl").exists() or not metrics_path.exists():
            continue
        try:
            rank = int(json.loads(metrics_path.read_text(encoding="utf-8"))
                       .get("selection_rank", 99))
        except (OSError, ValueError, TypeError):
            rank = 99
        if best is None or rank < best[0]:
            best = (rank, directory)
    return best[1] if best else None


def load_predictor(root: Path, *, checkpoint: str, version: str = "v1",
                   family: str | None = None) -> PassPredictor:
    """Load one checkpoint's CP-14 artifact, or raise :class:`ModelUnavailable`.

    Raising rather than returning a stub is deliberate: the caller decides
    whether to fall back, and the decision is then recorded in ``stubs_used``
    instead of being made invisibly here.
    """
    from ..pass_model.artifacts import load_fit_artifact

    directory = _artifact_root(Path(root), version, checkpoint, family)
    if directory is None:
        raise ModelUnavailable(
            f"no CP-14 artifact for {checkpoint} under {Path(root) / version}. Train "
            "one with scripts/train/train_pass_model.py, or accept the synthetic "
            "development model and let the bundle record it in stubs_used.")

    model, schema, manifest = load_fit_artifact(directory)
    return PassPredictor(
        checkpoint=checkpoint,
        family=directory.name,
        model=model,
        numeric=tuple(schema.get("numeric") or ()),
        categorical=tuple(schema.get("categorical") or ()),
        manifest=manifest,
        artifact_version=f"{version}/{checkpoint.lower()}/{directory.name}",
    )


def available_checkpoints(root: Path, version: str = "v1",
                          checkpoints: Sequence[str] = ("DETECTION", "ACTIVATION", "BRAKING"),
                          ) -> dict[str, str | None]:
    """``checkpoint -> artifact path`` (or ``None``), for the bundle manifest."""
    out: dict[str, str | None] = {}
    for checkpoint in checkpoints:
        directory = _artifact_root(Path(root), version, checkpoint, None)
        out[checkpoint] = str(directory) if directory else None
    return out
