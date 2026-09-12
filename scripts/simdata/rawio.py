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

# ---------------------------------------------------------------------------
# "Position unknown" sentinels.
#
# The feed does not write null when the positioning system loses a car. It writes a
# CONSTANT COORDINATE, while speed, gear, throttle and distance keep running normally
# (median speed at the sentinel is 79.6 km/h -- the pit limit), which is why every
# heuristic keyed on "the car looks stopped" fails to see it. Measured: it covers the
# whole starting grid and every pit-lane visit at the Chinese rounds, where it puts all
# 18 cars on ONE station, and it is not China-only -- Canadian P1 uses (345, 86) m at
# 6.21 %, Miami Qualifying and Belgian P3 use the ORIGIN. At least three sentinel
# families exist, so the coordinate is DISCOVERED, never hardcoded.
#
# Detection is deliberately coordinate-free and ring-free: a sample is "stuck" when the
# car did not move while its own speed channel says it must have. A distance-from-centre
# test cannot work -- it flags Silverstone's correctly traced pit lane, which genuinely
# sits 31-104 m off the centreline.
STUCK_MOVE_M = 0.25      # the car moved less than this...
STUCK_DEMAND_M = 1.0     # ...while its speed demanded more than this
STUCK_MIN_RUN = 3        # consecutive stuck steps before it counts (kills float noise)
SENTINEL_BIN_M = 1.0     # cluster stuck coordinates at this resolution
SENTINEL_MIN_HITS = 100  # a real sentinel is used heavily...
SENTINEL_MIN_DRIVERS = 3 # ...by several cars; one car parked is not a sentinel
SENTINEL_MIN_SHARE = 0.30  # ...and it DOMINATES: it is the one marker meaning "unknown"
SENTINEL_RADIUS_M = 2.0  # flag everything this close to an accepted sentinel

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


def stuck_mask(t, speed_kph, x, y,
               move_m: float = STUCK_MOVE_M,
               demand_m: float = STUCK_DEMAND_M,
               min_run: int = STUCK_MIN_RUN) -> np.ndarray:
    """Stage 1: samples where the position did not advance though the speed says it must.

    Pure: arrays in, boolean array out. Coordinate-free, so it finds a sentinel at any
    location including the origin, and it does not need a ring or a track model.

    The float-noise detail that defeats naive detectors: the provider writes -8325.0,
    -8325.000000000002, -8325.000000000004 for what is one point, so exact-equality
    freeze detection sees 0.704 % where the truth is ~2 %. A 0.25 m movement threshold
    collapses that smear; the run-length requirement then removes single-sample noise.
    """
    t = np.asarray(t, dtype=np.float64)
    v = np.asarray(speed_kph, dtype=np.float64)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = t.shape[0]
    out = np.zeros(n, dtype=bool)
    if n < 2:
        return out

    dt = np.diff(t)
    moved = np.hypot(np.diff(x), np.diff(y))
    demanded = np.abs(v[:-1]) / 3.6 * dt
    step = np.isfinite(moved) & np.isfinite(demanded)         & (moved < move_m) & (demanded > demand_m)

    # keep only runs of >= min_run consecutive stuck steps
    if min_run > 1 and step.any():
        keep = np.zeros_like(step)
        i = 0
        while i < step.size:
            if step[i]:
                j = i
                while j < step.size and step[j]:
                    j += 1
                if j - i >= min_run:
                    keep[i:j] = True
                i = j
            else:
                i += 1
        step = keep

    # a stuck STEP taints both samples it joins
    out[:-1] |= step
    out[1:] |= step
    return out


class SentinelIndex:
    """Stage 2: the session-wide sentinel coordinates, discovered from stage 1.

    Kept separate from Lap because a sentinel is a property of a SESSION, not a lap:
    one car sitting still is a parked car, the same point reused by many cars across
    many laps is the feed's "unknown" marker. Accepting a cluster needs the whole
    session, which is why this is built once and then applied per lap.
    """

    __slots__ = ("points",)

    def __init__(self, points: np.ndarray):
        self.points = np.asarray(points, dtype=np.float64).reshape(-1, 2)

    def __bool__(self) -> bool:
        return self.points.shape[0] > 0

    def __len__(self) -> int:
        return int(self.points.shape[0])

    @classmethod
    def from_laps(cls, laps) -> "SentinelIndex":
        """Discover sentinels from an iterable of Lap objects in one session."""
        hits: dict[tuple[int, int], list] = {}
        for lap in laps:
            if lap is None or lap.n < 2:
                continue
            m = lap.stuck
            if not m.any():
                continue
            xs, ys = lap.x[m], lap.y[m]
            good = np.isfinite(xs) & np.isfinite(ys)
            for bx, by, drv in zip(np.round(xs[good] / SENTINEL_BIN_M).astype(int),
                                   np.round(ys[good] / SENTINEL_BIN_M).astype(int),
                                   [lap.driver] * int(good.sum())):
                cell = hits.setdefault((int(bx), int(by)), [0, set()])
                cell[0] += 1
                cell[1].add(drv)
        total = sum(count for count, _ in hits.values())
        if not total:
            return cls(np.empty((0, 2)))
        # The SHARE test is what separates a sentinel from a STALE HOLD, and without it
        # this whole detector is dangerous. Hungary's race feed is a stale-anchor
        # sample-and-hold: it repeats the car's LAST KNOWN position, which is a different
        # fault and lives wherever the car happened to be. Measured, the difference is
        # unambiguous -- China's sentinel is 80 % (Race) and 84 % (Qualifying) of every
        # stuck sample in its session, while Hungary's busiest cluster is 14 % and the
        # damage is spread over 24 of them. Without this test Hungary would have 62.8 %
        # of its positions withdrawn by a detector aimed at something else entirely.
        pts = [(bx * SENTINEL_BIN_M, by * SENTINEL_BIN_M)
               for (bx, by), (count, drivers) in hits.items()
               if count >= SENTINEL_MIN_HITS
               and len(drivers) >= SENTINEL_MIN_DRIVERS
               and count / total >= SENTINEL_MIN_SHARE]
        return cls(np.array(pts, dtype=np.float64) if pts else np.empty((0, 2)))

    def mask(self, x, y, radius_m: float = SENTINEL_RADIUS_M) -> np.ndarray:
        """Samples sitting on (or beside) a discovered sentinel."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        out = np.zeros(x.shape, dtype=bool)
        for px, py in self.points:
            out |= np.hypot(x - px, y - py) <= radius_m
        return out


class Lap:
    """One {lap}_tel.json, unit-corrected and length-consistent."""

    __slots__ = ("driver", "lap", "n", "t", "speed", "dist", "x", "y", "z",
                 "gear", "brake", "throttle", "rpm", "ahead", "gap_ahead",
                 "_stuck", "position_dropped")

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
        self._stuck = None
        # How many samples had their position withdrawn as unusable. Nonzero means this
        # lap's position is partly ABSENT, never that it was replaced by a guess.
        self.position_dropped = 0

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


def _lap_stuck(self) -> np.ndarray:
    """Stage-1 stuck mask for this lap, computed once."""
    if self._stuck is None:
        self._stuck = stuck_mask(self.t, self.speed, self.x, self.y)
    return self._stuck


def _lap_drop_positions(self, mask) -> int:
    """Withdraw the position of the masked samples: x/y/z become NaN.

    NaN is the honest representation -- every consumer already treats a non-finite
    position as "no position here" and skips it, whereas leaving the sentinel in place
    made the pipeline draw a car at a coordinate the feed never measured. The count is
    kept so a session can report how much of its position channel is missing instead of
    silently shipping a shorter truth.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != self.x.shape or not mask.any():
        return 0
    fresh = int(np.count_nonzero(mask & np.isfinite(self.x)))
    self.x[mask] = np.nan
    self.y[mask] = np.nan
    self.z[mask] = np.nan
    self.position_dropped += fresh
    self._stuck = None
    return fresh


Lap.stuck = property(_lap_stuck)
Lap.drop_positions = _lap_drop_positions


def _lap_position_quality(self) -> dict:
    """Measurements a caller needs to decide whether this lap's POSITIONS are usable.

    Deliberately returns numbers, not a verdict: the right threshold differs between
    "may this lap define the circuit's shape" and "may this lap be replayed", and a
    single boolean hid that distinction. Every field is measured, none is a guess.

      xyFraction   fraction of samples carrying a finite position
      pathOverSpan geometric path length / integrated-distance span. Jitter integrates,
                   so a stale-anchor hold inflates this (Hungary Race median 1.36, every
                   clean session 0.997-1.010). NOTE this is a WEAK discriminator on its
                   own -- 15.8 % of Hungary's corrupt laps land inside [0.97, 1.03] by
                   chance, because a zigzag around a held anchor adds little length.
      uniqueFrac   fraction of DISTINCT (x, y) values. This is the strong one: Hungary
                   Race reads 0.536 against 1.000 at every other session.
      repeatBackFrac  fraction of samples exactly equal to the sample two before, the
                   signature of alternating hold/advance (Hungary 0.282, others <0.001).
      endGapM      distance from the first sample to the last. build_ring welds this
                   shut, so a lap with a large gap silently adds a phantom straight --
                   measured up to 1147.6 m at Hungary.
      medianStepM  median sample-to-sample movement (6-8 m when healthy).
    """
    x, y = self.x, self.y
    finite = np.isfinite(x) & np.isfinite(y)
    nf = int(finite.sum())
    out = {
        "xyFraction": float(finite.mean()) if self.n else 0.0,
        "pathOverSpan": None, "uniqueFrac": None, "repeatBackFrac": None,
        "endGapM": None, "medianStepM": None,
    }
    if nf < 2:
        return out
    fx, fy = x[finite], y[finite]
    span = self.distance_span()
    path = float(np.hypot(np.diff(fx), np.diff(fy)).sum())
    if span > 0:
        out["pathOverSpan"] = path / span
    pairs = np.column_stack((np.round(fx, 3), np.round(fy, 3)))
    out["uniqueFrac"] = float(len(np.unique(pairs, axis=0)) / nf)
    if nf > 2:
        back = (pairs[2:] == pairs[:-2]).all(axis=1)
        out["repeatBackFrac"] = float(back.mean())
    out["endGapM"] = float(np.hypot(fx[0] - fx[-1], fy[0] - fy[-1]))
    out["medianStepM"] = float(np.median(np.hypot(np.diff(fx), np.diff(fy))))
    return out


Lap.position_quality = _lap_position_quality


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
