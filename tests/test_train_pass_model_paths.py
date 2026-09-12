from __future__ import annotations

import importlib
import json
from pathlib import Path

import pandas as pd
import pytest


def test_load_opportunities_accepts_relative_and_absolute_roots(tmp_path, monkeypatch):
    module = importlib.import_module("scripts.train.train_pass_model")
    repo = tmp_path / "repo"
    root = repo / "data" / "processed" / "overtake_opportunities"
    partition = root / "event=example" / "opportunities.parquet"
    partition.parent.mkdir(parents=True)
    partition.touch()

    monkeypatch.chdir(repo)
    monkeypatch.setattr(module, "ROOT", repo.resolve())
    frame = pd.DataFrame({"event": ["example"], "decision_checkpoint": ["DETECTION"], "passed_by_outcome_horizon": [True]})
    monkeypatch.setattr(pd, "read_parquet", lambda path: frame)

    relative_frame, relative_sources = module.load_opportunities(Path("data/processed/overtake_opportunities"))
    absolute_frame, absolute_sources = module.load_opportunities(root.resolve())

    assert len(relative_frame) == len(absolute_frame) == 1
    assert relative_sources == absolute_sources == ["data/processed/overtake_opportunities/event=example/opportunities.parquet"]
    json.dumps({"sources": relative_sources}, allow_nan=False)


def test_load_opportunities_rejects_old_m07_schema(tmp_path, monkeypatch):
    module = importlib.import_module("scripts.train.train_pass_model")
    root = tmp_path / "data" / "processed" / "overtake_opportunities"
    partition = root / "event=example" / "opportunities.parquet"
    partition.parent.mkdir(parents=True)
    partition.touch()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    old = pd.DataFrame({"schema_version": ["m07_overtake_opportunities_v1"]})
    monkeypatch.setattr(pd, "read_parquet", lambda path: old)
    with pytest.raises(SystemExit, match="schema mismatch"):
        module.load_opportunities(root)
