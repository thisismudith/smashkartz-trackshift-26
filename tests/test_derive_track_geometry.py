"""Regression coverage for CP-05 geometry derivation inputs."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "features" / "derive_track_geometry.py"


def _geometry_script():
    spec = importlib.util.spec_from_file_location("derive_track_geometry", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_direct_phase_one_raw_layout_supplies_corner_markers(tmp_path, monkeypatch):
    """The direct ``data/raw/<year>`` layout must not drop corner boundaries."""
    module = _geometry_script()
    direct = tmp_path / "raw"
    corners_path = direct / "2026" / "Example Grand Prix" / "Race" / "corners.json"
    corners_path.parent.mkdir(parents=True)
    corners_path.write_text(
        json.dumps({"CornerNumber": [1, 2], "Distance": [123.4, 567.8], "Rotation": 10.0}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "RAW", tmp_path / "raw" / "tracinginsights")
    monkeypatch.setattr(module, "RAW_FALLBACK", direct)

    corners, rotation, source = module.load_corners("Example Grand Prix")

    assert [corner["Distance"] for corner in corners] == [123.4, 567.8]
    assert rotation == 10.0
    assert source.endswith("2026/Example Grand Prix/Race/corners.json")
