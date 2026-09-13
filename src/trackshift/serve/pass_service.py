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
    "DEFAULT_ARTIFACT_VERSION",
    "FEATURE_ALIASES",
    "PassServiceError",
    "CheckpointViolation",
    "NotModelEligible",
    "ModelUnavailable",
    "PassPredictor",
    "load_predictor",
    "normalise_features",
]

PASS_SERVICE_SCHEMA_VERSION = "cp24_pass_service_v1"

#: Which build under ``artifacts/models/pass/`` answers by default.
#:
#: ``v2`` rather than ``v1``: INTEGRATION.md section 2 says "Prefer ``v2``" and
#: repeats it in the gotchas -- same feature count, later build. Naming it once
#: here is the point; it was previously a default argument repeated in
#: ``load_predictor`` and ``available_checkpoints``, so the readiness report and
#: the serving path could drift apart and each look correct on its own.
DEFAULT_ARTIFACT_VERSION = "v2"

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

#: UI vocabulary -> the model's own locked schema. INTEGRATION.md section 3 states
#: the requirement plainly: "if the frontend currently posts ``gap_s``, it must map
#: to ``gap_at_checkpoint``". Without it a caller using the documented API.md name
#: supplies zero features and the model answers with its base rate for every
#: request -- a number that looks like a prediction and moves for no input.
#:
#: These are RENAMES ONLY. Nothing here converts a unit. A field whose unit
#: differs from the schema's is deliberately left unmapped so it surfaces in
#: ``features_missing``, because a silently rescaled feature is the one failure
#: this mapping exists to prevent.
FEATURE_ALIASES: dict[str, str] = {
    # seconds, both sides
    "gap_s": "gap_at_checkpoint",
    "time_gap_s": "gap_at_checkpoint",
    "gap_at_checkpoint_s": "gap_at_checkpoint",
    # NOTE: `gap_rate_ahead_s_per_s` (API.md 5.5) is deliberately NOT mapped here.
    # It is the derivative of the gap, negative while closing, whereas
    # `closing_rate_s_per_s` is positive while closing (rules/eligibility.py) --
    # the same magnitude with the opposite sign. Aliasing them would hand the
    # model a backwards feature that no response could reveal.
    # degrees C, both sides
    "track_temperature_c": "track_temperature",
    # km/h, both sides -- kph and kmh are the same unit spelled two ways, and
    # mapping them matters at DETECTION where supplying either is a violation.
    "speed_at_activation_kph": "speed_at_activation_kmh",
    "speed_at_braking_kph": "speed_at_braking_kmh",
}

#: Keys that are request envelope, not features. Copied through unchanged so the
#: eligibility gate can still find its flag after flattening.
_ENVELOPE_KEYS = frozenset({"features", "decision_checkpoint", "checkpoint",
                            "feature_schema_id", "event", "attacker", "defender"})


def normalise_features(payload: Mapping[str, Any]) -> dict[str, Any]:
    """One flat feature dict from either documented request shape.

    Two shapes reach this route and both are documented. API.md 5.8 nests the
    feature vector under ``features``; INTEGRATION.md section 3 sends the same
    names at the top level. Accepting only one would make the contract and the
    model owner's own handoff disagree, with no way for a caller to tell which
    document won -- so both are accepted and the nested block wins on a clash,
    being the more specific statement of intent.

    Envelope keys are carried through rather than dropped: ``assert_eligible``
    looks for ``normal_race_model_eligible`` (or a ``state`` carrying it) on the
    same mapping, and losing it here would silently disable the gate.
    """
    flat: dict[str, Any] = {}
    nested = payload.get("features")
    sources: tuple[Mapping[str, Any], ...] = (
        payload, nested if isinstance(nested, Mapping) else {})
    for source in sources:
        for key, value in source.items():
            if key in _ENVELOPE_KEYS:
                continue
            flat[FEATURE_ALIASES.get(key, key)] = value
    return flat


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

        # A row of all-NaN still scores: the tree walks to its all-missing leaf
        # and hands back the fitted base rate -- for DETECTION, 0.231, which sits
        # close enough to the observed 2026 rate to read as a real answer. It is
        # not one. It is the same constant for every gap, every compound and
        # every corner, and no field of the old response distinguished it from a
        # prediction about the opportunity that was asked about. So the model is
        # not asked, and the absence is stated the way every other unavailable
        # quantity here is stated: null with a reason. Provenance stays INFERRED
        # -- a model still owns this field, and API.md 3.2 keeps provenance on a
        # null value; it is the value that is missing, not its source.
        blank = (
            "no model feature was supplied, so there is nothing to condition on. A "
            f"probability from zero of {len(self.features)} features is this model's "
            "unconditional base rate, not a prediction about this opportunity. Send at "
            "least gap_at_checkpoint.")
        probability = (None if not supplied
                       else float(self.model.predict_proba(self._row(payload))[0]))

        quantity: dict[str, Any] = {
            "value": probability,
            "unit": None,
            "provenance": "INFERRED",
            "model": f"M10/{self.family}",
            "artifact_version": self.artifact_version,
        }
        if probability is None:
            quantity["reason"] = blank

        return {
            "p_pass_by_outcome_horizon": quantity,
            "checkpoint": self.checkpoint,
            "calibration": self.manifest.get("calibration", "uncalibrated"),
            "features_supplied": supplied,
            # Named rather than silently imputed: a probability produced from two
            # of seventeen features is a different claim from one produced from
            # all of them, and the caller cannot tell without this.
            "features_missing": missing,
            # The counts, so a client can show "2 of 15" without owning the
            # schema. A caller that reads only the probability still sees the
            # emptiness in the same body.
            "features_supplied_count": len(supplied),
            "features_expected": len(self.features),
            # The manifest grades the ARTIFACT's evidence. With nothing supplied
            # there is no answer for that grade to describe, so it is withheld
            # rather than lent to a null -- a graded null reads as a graded
            # prediction.
            "evidence_grade": ((self.manifest.get("run") or {}).get("evidence_grade")
                               if supplied else None),
            "is_stub": False,
            **({"reason": blank} if probability is None else {}),
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


def load_predictor(root: Path, *, checkpoint: str,
                   version: str = DEFAULT_ARTIFACT_VERSION,
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


def available_checkpoints(root: Path, version: str = DEFAULT_ARTIFACT_VERSION,
                          checkpoints: Sequence[str] = ("DETECTION", "ACTIVATION", "BRAKING"),
                          ) -> dict[str, str | None]:
    """``checkpoint -> artifact path`` (or ``None``), for the bundle manifest."""
    out: dict[str, str | None] = {}
    for checkpoint in checkpoints:
        directory = _artifact_root(Path(root), version, checkpoint, None)
        out[checkpoint] = str(directory) if directory else None
    return out
