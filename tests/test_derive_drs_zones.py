"""Tests for deterministic Tier-C historical DRS zone consolidation."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _module():
    path = ROOT / "scripts" / "data" / "derive_drs_zones.py"
    spec = importlib.util.spec_from_file_location("derive_drs_zones", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overlapping_historical_intervals_consolidate_with_median_grid_values():
    deriver = _module()
    per_year = {
        "2022": {"zones": [{"start_m": 3500.0, "end_m": 3940.0, "peak_lap_share": 0.1}]},
        "2023": {"zones": [{"start_m": 3520.0, "end_m": 3960.0, "peak_lap_share": 0.1}]},
        "2024": {"zones": [{"start_m": 3500.0, "end_m": 3960.0, "peak_lap_share": 0.1}]},
        "2025": {"zones": [{"start_m": 3540.0, "end_m": 3900.0, "peak_lap_share": 0.1}]},
    }

    assert deriver.consolidate_candidates(per_year) == [{
        "start_m": 3520.0,
        "end_m": 3960.0,
        "length_m": 440.0,
        "seen_in_years": ["2022", "2023", "2024", "2025"],
        "support_count": 4,
        "source_intervals": [
            {"year": "2022", "start_m": 3500.0, "end_m": 3940.0, "peak_lap_share": 0.1},
            {"year": "2024", "start_m": 3500.0, "end_m": 3960.0, "peak_lap_share": 0.1},
            {"year": "2023", "start_m": 3520.0, "end_m": 3960.0, "peak_lap_share": 0.1},
            {"year": "2025", "start_m": 3540.0, "end_m": 3900.0, "peak_lap_share": 0.1},
        ],
        "value_source": "PROXY_HISTORICAL_DRS",
    }]


def test_consolidation_requires_cross_year_support():
    deriver = _module()
    assert deriver.consolidate_candidates({
        "2024": {"zones": [{"start_m": 3500.0, "end_m": 3940.0}]},
    }) == []
