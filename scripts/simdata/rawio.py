"""Raw TracingInsights JSON access.

Conventions established by measurement (see plan section 3):
  * x / y / z are DECIMETRES -> divide by 10 for metres.
  * Missing values are the STRING "None", never null.
  * Channel arrays within one lap file can disagree in length (one malformed file in
    3327 across three races), so every lap is truncated to the shortest channel.
  * Session time = laptimes.lST[lap] + tel.time[i].
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

DM_TO_M = 0.1
MIN_SAMPLES = 20  # laps shorter than this are stubs left behind by a retirement

TEL_FLOAT = ("time", "rpm", "speed", "throttle", "distance", "rel_distance",
             "acc_x", "acc_y", "acc_z", "x", "y", "z", "DistanceToDriverAhead")
TEL_INT = ("gear", "brake", "drs")


def read_json(path: Path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _to_float(seq) -> np.ndarray:
    """Column of floats with the string "None" mapped to NaN."""
    out = np.empty(len(seq), dtype=np.float64)
    for i, v in enumerate(seq):
        out[i] = np.nan if isinstance(v, str) else v
    return out


def _to_int(seq, missing=-1) -> np.ndarray:
    out = np.empty(len(seq), dtype=np.int16)
    for i, v in enumerate(seq):
        out[i] = missing if isinstance(v, str) else int(v)
    return out


class Lap:
    """One {lap}_tel.json, unit-corrected and length-consistent."""

    __slots__ = ("driver", "lap", "n", "t", "speed", "dist", "x", "y", "z",
                 "gear", "brake", "throttle", "rpm", "ahead", "gap_ahead")

    def __init__(self, driver: str, lap: int, tel: dict):
        n = min(len(v) for k, v in tel.items() if isinstance(v, list))
        self.driver, self.lap, self.n = driver, lap, n
        self.t = _to_float(tel["time"][:n])
        self.speed = _to_float(tel["speed"][:n])
        self.dist = _to_float(tel["distance"][:n])
        self.x = _to_float(tel["x"][:n]) * DM_TO_M
        self.y = _to_float(tel["y"][:n]) * DM_TO_M
        self.z = _to_float(tel["z"][:n]) * DM_TO_M
        self.gear = _to_int(tel["gear"][:n])
        self.brake = _to_int(tel["brake"][:n], 0)
        self.throttle = _to_float(tel["throttle"][:n])
        self.rpm = _to_float(tel["rpm"][:n])
        self.ahead = [None if v == "None" else v for v in tel["DriverAhead"][:n]]
        self.gap_ahead = _to_float(tel["DistanceToDriverAhead"][:n])

    @property
    def has_xy(self) -> bool:
        return bool(np.isfinite(self.x).any())

    def xy_fraction(self) -> float:
        return float(np.isfinite(self.x).mean()) if self.n else 0.0

    def path_length(self) -> float:
        m = np.isfinite(self.x) & np.isfinite(self.y)
        if m.sum() < 2:
            return 0.0
        return float(np.hypot(np.diff(self.x[m]), np.diff(self.y[m])).sum())

    def distance_span(self) -> float:
        d = self.dist[np.isfinite(self.dist)]
        return float(d[-1] - d[0]) if d.size >= 2 else 0.0

    def geometry_valid(self) -> bool:
        """Gate from the plan: >=95 % positions present and a path length that agrees
        with the integrated distance to within a factor of two either way."""
        if self.n < MIN_SAMPLES or self.xy_fraction() < 0.95:
            return False
        span = self.distance_span()
        if span <= 0:
            return False
        ratio = self.path_length() / span
        return 0.5 <= ratio <= 1.5


def load_lap(session_dir: Path, driver: str, lap: int) -> Lap | None:
    p = session_dir / driver / f"{lap}_tel.json"
    if not p.exists():
        return None
    try:
        tel = read_json(p)["tel"]
    except (KeyError, json.JSONDecodeError):
        return None
    try:
        return Lap(driver, lap, tel)
    except (KeyError, ValueError):
        return None


class LapTable:
    """session_laptimes.json as columns, plus the clean-lap filter."""

    def __init__(self, session_dir: Path):
        self.raw = read_json(session_dir / "session_laptimes.json")
        self.n = len(self.raw["lap"])

    def col(self, key):
        return self.raw.get(key, ["None"] * self.n)

    def rows(self):
        keys = list(self.raw.keys())
        for i in range(self.n):
            yield {k: self.raw[k][i] for k in keys}

    def clean_mask(self):
        """The exact 8-clause filter from the plan, plus the two integrity clauses."""
        st = self.col("status"); ia = self.col("iacc"); pin = self.col("pin")
        pout = self.col("pout"); lap = self.col("lap"); dele = self.col("del")
        ff = self.col("ff1G"); tm = self.col("time"); life = self.col("life")
        comp = self.col("compound")
        out = []
        for i in range(self.n):
            out.append(
                st[i] == "1" and ia[i] is True
                and pin[i] == "None" and pout[i] == "None"
                and lap[i] > 1 and dele[i] is not True and ff[i] is not True
                and not isinstance(tm[i], str)
                and not isinstance(life[i], str)
                and comp[i] in ("SOFT", "MEDIUM", "HARD")
            )
        return out
