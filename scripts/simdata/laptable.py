"""Tidy lap table across every Race and Sprint session, for fitting the New Race engine
parameters. Streams one session_laptimes.json at a time; never opens telemetry.
"""
from __future__ import annotations

import json

import numpy as np

from simdata.paths import data_root

NUMERIC_NONE_COLS = ("time", "life", "s1", "s2", "s3", "vi1", "vi2", "vfl", "vst",
                     "wAT", "wTT", "wH", "wP", "wWS", "wWD", "s1T", "s2T", "s3T")


def _num_or_nan(v):
    return np.nan if isinstance(v, str) else v


def load_session(event: str, session: str) -> "dict[str, np.ndarray] | None":
    p = data_root() / event / session / "session_laptimes.json"
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    n = len(raw.get("lap", []))
    if n == 0:
        return None
    out = {"event": np.full(n, event, dtype=object),
           "session": np.full(n, session, dtype=object)}
    for col in NUMERIC_NONE_COLS:
        if col in raw:
            out[col] = np.array([_num_or_nan(v) for v in raw[col]], dtype=np.float64)
    for col in ("drv", "team", "compound", "status", "pin", "pout", "dNum", "lSD"):
        if col in raw:
            out[col] = np.array(raw[col], dtype=object)
    for col in ("lap", "stint"):
        if col in raw:
            out[col] = np.array([_num_or_nan(v) for v in raw[col]], dtype=np.float64)
    for col in ("del", "ff1G", "iacc", "pb", "fresh", "wR"):
        if col in raw:
            out[col] = np.array([v is True for v in raw[col]], dtype=bool)
    out["pos"] = np.array([_num_or_nan(v) for v in raw.get("pos", [None] * n)], dtype=np.float64)
    out["sesT"] = np.array([_num_or_nan(v) for v in raw.get("sesT", [None] * n)], dtype=np.float64)
    out["lST"] = np.array([_num_or_nan(v) for v in raw.get("lST", [None] * n)], dtype=np.float64)
    return out


def discover_events() -> list[str]:
    out = []
    for d in sorted(data_root().iterdir()):
        if d.is_dir() and (d / "Race").exists():
            out.append(d.name)
    return out


def build_tidy_table(sessions=("Race", "Sprint")) -> dict:
    """Concatenate every event/session into one column-of-arrays table."""
    parts = []
    for event in discover_events():
        for session in sessions:
            s = load_session(event, session)
            if s is not None:
                parts.append(s)
    if not parts:
        raise RuntimeError("no sessions found")

    keys = set.intersection(*(set(p.keys()) for p in parts))
    out = {}
    for k in keys:
        arrs = [p[k] for p in parts]
        if arrs[0].dtype == object:
            out[k] = np.concatenate(arrs)
        else:
            out[k] = np.concatenate(arrs)
    out["n"] = sum(len(p["lap"]) for p in parts)
    return out


def clean_mask(t: dict) -> np.ndarray:
    """The exact clean-lap filter: green flag for the whole lap, timing-accurate,
    not an in/out lap, not lap 1, not deleted, not FastF1-generated, has a lap time,
    has tyre life, and a dry-compound tyre."""
    status_ok = t["status"] == "1"
    return (
        status_ok
        & t["iacc"]
        & (t["pin"] == "None")
        & (t["pout"] == "None")
        & (t["lap"] > 1)
        & ~t["del"]
        & ~t["ff1G"]
        & np.isfinite(t["time"])
        & np.isfinite(t["life"])
        & np.isin(t["compound"], ("SOFT", "MEDIUM", "HARD"))
    )
