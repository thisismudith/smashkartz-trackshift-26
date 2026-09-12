"""Artifact and manifest writing for the M10 benchmark (Tanveer CP-14, section 53).

Every artifact records the git commit, the source datasets, the config, the
schema version, the feature schema, the seed, and ``cpu_inference_verified``.
The last one is not a formality: MODELS.md section 1.1 requires every artifact to
load and run on CPU, and the only way to know is to reload the saved file in a
fresh object and score a row with it. So :func:`write_fit_artifact` does exactly
that before it writes the manifest, and records the round-trip agreement.

Two files carry the model. The pickle is what this codebase reloads; the native
booster dump (``.txt`` for LightGBM, ``.json`` for XGBoost, ``.cbm`` for CatBoost)
is what survives a library upgrade, because a pickle is only readable by the
version that wrote it and these artifacts outlive their wheels.
"""
from __future__ import annotations

import json
import pickle
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "git_commit",
    "package_versions",
    "write_fit_artifact",
    "load_fit_artifact",
]

ARTIFACT_SCHEMA_VERSION = "m10_pass_model_v1"

_TRACKED_PACKAGES = (
    "numpy", "pandas", "pyarrow", "scikit-learn", "lightgbm", "xgboost", "catboost", "scipy",
)


#: Repo root, derived from this file's own location rather than the working
#: directory, so every default resolves the same way whatever the caller's cwd.
PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def git_commit(root: Path | None = None) -> str | None:
    """The HEAD commit, or ``None`` outside a repository.

    ``None`` rather than a raise: an artifact built from an exported tarball is
    still a valid artifact, and it is better for the manifest to say the commit
    is unknown than for the run to fail at the write step.
    """
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root if root is not None else PACKAGE_ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def package_versions() -> dict[str, str | None]:
    """Installed versions of the libraries a fit's reproducibility depends on."""
    from importlib.metadata import PackageNotFoundError, version

    out: dict[str, str | None] = {}
    for name in _TRACKED_PACKAGES:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out


def _dump_native(model: Any, directory: Path) -> str | None:
    """Write the library's own portable model format, if it has one."""
    inner = getattr(model, "_model", None)
    family = getattr(model, "family", None)
    try:
        if family == "lightgbm":
            path = directory / "model.lgb.txt"
            inner.booster_.save_model(str(path))
        elif family == "xgboost":
            path = directory / "model.xgb.json"
            inner.get_booster().save_model(str(path))
        elif family == "catboost":
            path = directory / "model.cbm"
            inner.save_model(str(path))
        else:
            return None
    except Exception:
        # A missing portable dump costs future-proofing, not correctness: the
        # pickle beside it still loads. Failing the run here would throw away a
        # completed fit over a nice-to-have.
        return None
    return path.name


def write_fit_artifact(
    directory: Path,
    *,
    model: Any,
    selection: Any,
    metrics: Mapping[str, Any],
    split_plan: Mapping[str, Any],
    sample_row: Any,
    source_datasets: Sequence[str],
    config_files: Sequence[str] = (),
    extra: Mapping[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Write one fit's model, feature schema, metrics and manifest.

    ``sample_row`` is a one-row frame in the model's own feature schema. It is
    scored twice -- once by the fitted object and once by the object reloaded
    from disk -- and the manifest records whether the two agree. That is what
    turns ``cpu_inference_verified`` from an assertion into a measurement.
    """
    directory.mkdir(parents=True, exist_ok=True)

    model_path = directory / "model.pkl"
    with model_path.open("wb") as handle:
        pickle.dump(model, handle, protocol=pickle.HIGHEST_PROTOCOL)
    native = _dump_native(model, directory)

    cpu_verified = False
    round_trip_delta: float | None = None
    verification_error: str | None = None
    try:
        with model_path.open("rb") as handle:
            reloaded = pickle.load(handle)
        before = float(model.predict_proba(sample_row)[0])
        after = float(reloaded.predict_proba(sample_row)[0])
        round_trip_delta = abs(before - after)
        cpu_verified = round_trip_delta <= 1e-9
    except Exception as exc:
        verification_error = f"{type(exc).__name__}: {exc}"

    (directory / "feature_schema.json").write_text(
        json.dumps(selection.as_schema(), indent=2), encoding="utf-8"
    )
    (directory / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    manifest: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(root),
        "model": model.describe(),
        "decision_checkpoint": selection.checkpoint,
        "feature_schema_version": selection.schema_version,
        "feature_schema": "feature_schema.json",
        "n_features": len(selection.columns),
        "seed": model.seed,
        "split": dict(split_plan),
        "source_datasets": list(source_datasets),
        "config_files": list(config_files),
        "model_files": [model_path.name] + ([native] if native else []),
        # MODELS.md section 1.1 and the A6000 rules: every artifact must load and
        # score on CPU, verified before merge.
        "device_trained_on": "cpu",
        "cpu_inference_verified": cpu_verified,
        "cpu_inference_round_trip_delta": round_trip_delta,
        "cpu_inference_error": verification_error,
        "platform": {
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "package_versions": package_versions(),
    }
    if extra:
        manifest.update(extra)
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_fit_artifact(directory: Path) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Reload ``(model, feature_schema, manifest)`` written by :func:`write_fit_artifact`."""
    directory = Path(directory)
    with (directory / "model.pkl").open("rb") as handle:
        model = pickle.load(handle)
    schema = json.loads((directory / "feature_schema.json").read_text(encoding="utf-8"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    return model, schema, manifest
