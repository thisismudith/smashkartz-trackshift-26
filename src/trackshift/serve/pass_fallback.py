"""A season-scoped empirical pass rate, for use only while M10 has no artifact.

WHAT THIS IS. The observed frequency with which an attacker that was `gap_s`
behind at the 2026 Overtake **Detection Line** was ahead of that same defender by
the **exit of the next activation zone** -- counted over every 2026 Race and
Sprint the raw feed carries, bucketed by gap, with a Wilson score interval on
each bucket. It is a *contingency table*, not a model: no fitting, no features
beyond the gap, no calibration, no ensemble.

WHAT THIS IS NOT. It is not M10 and must never be presented as M10.
`trackshift.pass_model` (CHECKPOINTS_TANVEER.md CP-14 to CP-17) is the real
pass model: per-checkpoint feature matrices, five model families, isotonic or
Platt calibration selected on ECE, and a five-member ensemble carrying a genuine
spread. When that pipeline writes an artifact, :func:`serve.app` loads it and
this module stops being consulted -- there is no merge, no blend, and nothing
here to unwind. This exists so the route answers with something REAL and
measured in the meantime, instead of a flat 0.5 placeholder.

HOW THE TWO DIFFER, PLAINLY, so the badge on screen can say it:

  - one feature (gap at detection) versus M10's full per-checkpoint matrix
  - 2026 only, versus M10's 2022-2026 with era handling (CP-17)
  - a bucket's own observed frequency, versus a calibrated probability (CP-15)
  - a Wilson interval on the count, versus an ensemble spread (CP-16)

A Wilson interval answers "how well is THIS BUCKET's frequency pinned down by
its own sample", which is a narrower question than M10's uncertainty. It is
honest about counting error and silent about model error, and the response says
so in its own `reason`.

THE CHECKPOINT GEOMETRY IS READ, NEVER GUESSED. Detection and activation line
positions come from `config/rules/2026/<event>.yaml`, which carries its own
`value_source` per number (`DERIVED_TELEMETRY` for the detection line measured
at Safety Car Line 1, `PROXY_HISTORICAL_DRS` for activation lines inferred from
2022-2025 DRS). An event missing either is skipped rather than defaulted: there
is no such thing as a sensible stand-in for a line's position.
"""
from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

__all__ = [
    "ARTIFACT_PATH",
    "GAP_BUCKETS_S",
    "SCHEMA_VERSION",
    "Opportunity",
    "RateTable",
    "build_rate_table",
    "load_rate_table",
    "predict_from_table",
]

SCHEMA_VERSION = "pass_fallback_rate_v1"

#: Where the build script writes, and the service reads. Relative to the repo root.
ARTIFACT_PATH = Path("artifacts") / "pass_fallback" / "2026_rate_table.json"

#: Gap-at-detection buckets, seconds. The first edge is the 1.0 s arming
#: threshold split in two, because that is the region the decision actually
#: lives in; beyond ~3 s the observed rate is already at the noise floor.
GAP_BUCKETS_S: tuple[tuple[float, float], ...] = (
    (0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 5.0),
)

#: A sample is accepted as "at" a line only within this distance of it. The raw
#: feed samples every 10-15 m, so 60 m admits the nearest sample on either side
#: with room for a sparse patch, and rejects a lap whose trace simply stops.
LINE_TOLERANCE_M = 60.0

#: Above this the pair is not in any meaningful contest and the row is dropped
#: rather than counted as a failed pass -- which would bias every bucket down.
MAX_TRACKED_GAP_S = 5.0

#: Below this the speed channel cannot convert a distance gap into a time gap.
MIN_SPEED_KMH = 5.0


@dataclass(frozen=True)
class Opportunity:
    """One attacker/defender approach to one activation zone, and its outcome."""

    event: str
    session: str
    lap: int
    attacker: str
    defender: str
    gap_s: float
    gap_m: float
    speed_kmh: float
    armed: bool
    passed: bool


@dataclass(frozen=True)
class Bucket:
    low_s: float
    high_s: float
    n: int
    passes: int
    rate: float
    ci_low: float
    ci_high: float


@dataclass(frozen=True)
class RateTable:
    schema_version: str
    season: str
    label_definition: str
    detection_gap_threshold_s: float
    events: list[str]
    n_opportunities: int
    buckets: list[Bucket]

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["buckets"] = [asdict(b) if not isinstance(b, dict) else b for b in self.buckets]
        return data

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "RateTable":
        return cls(
            schema_version=str(data["schema_version"]),
            season=str(data["season"]),
            label_definition=str(data["label_definition"]),
            detection_gap_threshold_s=float(data["detection_gap_threshold_s"]),
            events=list(data.get("events", [])),
            n_opportunities=int(data.get("n_opportunities", 0)),
            buckets=[Bucket(**b) for b in data.get("buckets", [])],
        )


# --------------------------------------------------------------- Wilson

def _wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval.

    Imported from :mod:`trackshift.pass_model.calibration` when that module is
    importable, so there is exactly one implementation in the tree; the inline
    copy exists only so this module still answers when scikit-learn (which
    pass_model pulls in) is not installed in the serving environment.
    """
    try:  # pragma: no cover - exercised by whichever branch the env allows
        from trackshift.pass_model.calibration import wilson_interval

        return wilson_interval(successes, n, z)
    except Exception:
        if n <= 0:
            return (0.0, 1.0)
        p = successes / n
        denom = 1.0 + z * z / n
        centre = (p + z * z / (2 * n)) / denom
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        return (max(0.0, centre - half), min(1.0, centre + half))


# ------------------------------------------------------------ raw access

def _load_json(path: Path) -> Any | None:
    try:
        with path.open(encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def _driver_numbers(session_dir: Path) -> dict[str, str]:
    """Driver number -> three-letter code, from each driver's laptimes.json.

    The telemetry's `DriverAhead` channel carries the NUMBER, not the code, so
    without this every opportunity resolves to an unknown defender.
    """
    mapping: dict[str, str] = {}
    for driver_dir in sorted(p for p in session_dir.iterdir() if p.is_dir()):
        payload = _load_json(driver_dir / "laptimes.json")
        if payload is None:
            continue
        rows = payload.get("laptimes", payload) if isinstance(payload, dict) else payload
        number: Any = None
        if isinstance(rows, dict):
            value = rows.get("dNum")
            number = value[0] if isinstance(value, list) and value else value
        elif isinstance(rows, list) and rows and isinstance(rows[0], dict):
            number = rows[0].get("dNum")
        if number not in (None, "None", ""):
            mapping[str(number)] = driver_dir.name
    return mapping


def _session_laps(session_dir: Path) -> dict[tuple[str, int], dict[str, Any]]:
    laps: dict[tuple[str, int], dict[str, Any]] = {}
    for driver_dir in sorted(p for p in session_dir.iterdir() if p.is_dir()):
        for tel_path in driver_dir.glob("*_tel.json"):
            stem = tel_path.name.split("_")[0]
            if not stem.isdigit():
                continue
            payload = _load_json(tel_path)
            if not isinstance(payload, dict):
                continue
            tel = payload.get("tel")
            if isinstance(tel, dict):
                laps[(driver_dir.name, int(stem))] = tel
    return laps


def _index_at(tel: Mapping[str, Any], target_m: float) -> int | None:
    """Index of the sample nearest `target_m` along the lap, within tolerance."""
    distances = tel.get("distance")
    if not isinstance(distances, list):
        return None
    best: int | None = None
    best_delta: float | None = None
    for index, value in enumerate(distances):
        if not isinstance(value, (int, float)):
            continue
        delta = abs(float(value) - target_m)
        if best_delta is None or delta < best_delta:
            best, best_delta = index, delta
    if best is None or best_delta is None or best_delta > LINE_TOLERANCE_M:
        return None
    return best


def _channel_at(tel: Mapping[str, Any], name: str, index: int) -> Any:
    values = tel.get(name)
    if not isinstance(values, list) or index >= len(values):
        return None
    return values[index]


# ----------------------------------------------------------- geometry

def _event_geometry(config_dir: Path, slug: str) -> tuple[float, float, float] | None:
    """(detection_m, zone_start_m, zone_end_m) for the zone the detection arms.

    Returns None when this event's config has no detection line or no activation
    zone -- both are required to define an opportunity and its outcome, and
    neither has a defensible stand-in.
    """
    try:
        import yaml  # noqa: PLC0415 - optional at import time, required here
    except ImportError:
        return None
    path = config_dir / f"{slug}.yaml"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return None
    overtake = config.get("overtake") or {}

    def _value(node: Any) -> float | None:
        if isinstance(node, Mapping):
            node = node.get("value")
        return float(node) if isinstance(node, (int, float)) else None

    detection = _value(overtake.get("detection_line_m"))
    if detection is None:
        return None
    zones: list[tuple[float, float]] = []
    for zone in overtake.get("zones") or []:
        start = _value(zone.get("activation_line_m"))
        end = _value(zone.get("zone_end_m"))
        if start is not None and end is not None:
            zones.append((start, end))
    if not zones:
        return None
    # The zone the car reaches FIRST after arming: the next start strictly ahead
    # of the detection line, else (wrapping past the line) the earliest zone.
    ahead = sorted(z for z in zones if z[0] > detection)
    start, end = ahead[0] if ahead else sorted(zones)[0]
    return detection, start, end


def _slug(event: str) -> str:
    return event.lower().replace(" ", "_")


# -------------------------------------------------------------- build

def _opportunities_for_session(
    session_dir: Path, event: str, session: str, geometry: tuple[float, float, float],
) -> list[Opportunity]:
    detection_m, zone_start_m, zone_end_m = geometry
    numbers = _driver_numbers(session_dir)
    laps = _session_laps(session_dir)
    # Same lap when the zone lies ahead of the detection line, next lap when the
    # car has to cross the start/finish line to reach it.
    lap_offset = 0 if zone_start_m > detection_m else 1
    found: list[Opportunity] = []

    for (attacker, lap), tel in laps.items():
        index = _index_at(tel, detection_m)
        if index is None:
            continue
        ahead = _channel_at(tel, "DriverAhead", index)
        gap_m = _channel_at(tel, "DistanceToDriverAhead", index)
        speed = _channel_at(tel, "speed", index)
        if ahead in (None, "None", "") or not isinstance(gap_m, (int, float)):
            continue
        if not isinstance(speed, (int, float)) or speed <= MIN_SPEED_KMH:
            continue
        defender = numbers.get(str(ahead))
        if defender is None or defender == attacker:
            continue
        gap_s = float(gap_m) / (float(speed) / 3.6)
        if not (0.0 <= gap_s <= MAX_TRACKED_GAP_S):
            continue
        # Outcome read from the DEFENDER's own trace: at the zone exit, is the
        # attacker the car ahead of it? That is unambiguous -- reading the
        # attacker's own DriverAhead instead would also change when a THIRD car
        # comes between them, which is not this pair's outcome.
        defender_tel = laps.get((defender, lap + lap_offset))
        if defender_tel is None:
            continue
        exit_index = _index_at(defender_tel, zone_end_m)
        if exit_index is None:
            continue
        defender_ahead = _channel_at(defender_tel, "DriverAhead", exit_index)
        if defender_ahead in (None, "None", ""):
            continue
        found.append(Opportunity(
            event=event, session=session, lap=lap,
            attacker=attacker, defender=defender,
            gap_s=round(gap_s, 4), gap_m=round(float(gap_m), 2),
            speed_kmh=round(float(speed), 2),
            armed=gap_s <= 1.0,
            passed=numbers.get(str(defender_ahead)) == attacker,
        ))
    return found


def collect_opportunities(
    raw_root: Path, config_dir: Path, season: str = "2026",
    sessions: Sequence[str] = ("Race", "Sprint"),
    events: Iterable[str] | None = None,
) -> tuple[list[Opportunity], list[str], dict[str, str]]:
    """Every opportunity the season's raw feed supports, plus why events were skipped."""
    season_dir = raw_root / season
    if not season_dir.is_dir():
        return [], [], {season: "no raw directory for this season"}
    names = sorted(events) if events is not None else sorted(
        p.name for p in season_dir.iterdir() if p.is_dir()
    )
    found: list[Opportunity] = []
    used: list[str] = []
    skipped: dict[str, str] = {}
    for event in names:
        geometry = _event_geometry(config_dir, _slug(event))
        if geometry is None:
            skipped[event] = "config carries no detection line, or no activation zone"
            continue
        before = len(found)
        for session in sessions:
            session_dir = season_dir / event / session
            if session_dir.is_dir():
                found += _opportunities_for_session(session_dir, event, session, geometry)
        if len(found) > before:
            used.append(event)
        else:
            skipped[event] = "no usable opportunity rows in this event's raw feed"
    return found, used, skipped


def build_rate_table(
    opportunities: Sequence[Opportunity], *, season: str = "2026",
    events: Sequence[str] = (), detection_gap_threshold_s: float = 1.0,
) -> RateTable:
    buckets: list[Bucket] = []
    for low, high in GAP_BUCKETS_S:
        selected = [o for o in opportunities if low <= o.gap_s < high]
        n = len(selected)
        passes = sum(1 for o in selected if o.passed)
        ci_low, ci_high = _wilson(passes, n)
        buckets.append(Bucket(
            low_s=low, high_s=high, n=n, passes=passes,
            rate=(passes / n) if n else 0.0,
            ci_low=round(ci_low, 6), ci_high=round(ci_high, 6),
        ))
    return RateTable(
        schema_version=SCHEMA_VERSION, season=season,
        label_definition="zone_exit_v1",
        detection_gap_threshold_s=detection_gap_threshold_s,
        events=list(events), n_opportunities=len(opportunities), buckets=buckets,
    )


# --------------------------------------------------------------- serve

def load_rate_table(path: Path | None = None) -> RateTable | None:
    """The built table, or None when it has not been built for this checkout."""
    target = path or (_repo_root() / ARTIFACT_PATH)
    payload = _load_json(target)
    if not isinstance(payload, dict):
        return None
    try:
        table = RateTable.from_json(payload)
    except (KeyError, TypeError, ValueError):
        return None
    return table if table.schema_version == SCHEMA_VERSION else None


#: The regulation era each built season belongs to. 2022-2025 ran DRS; 2026 runs
#: the Overtake system. Measured across these five seasons the difference is not
#: subtle -- at a sub-0.5 s gap the four DRS seasons sit at 11.8-16.5 % and 2026
#: at 21.6 %, and at 0.5-1.0 s the gap is roughly fourfold -- which is precisely
#: why they are never pooled into one rate.
SEASON_ERA: Mapping[str, str] = {
    "2022": "DRS", "2023": "DRS", "2024": "DRS", "2025": "DRS", "2026": "OVERTAKE",
}


def load_all_tables(directory: Path | None = None) -> dict[str, RateTable]:
    """Every built season table, keyed by season, newest-first when iterated."""
    root = directory or (_repo_root() / ARTIFACT_PATH.parent)
    if not root.is_dir():
        return {}
    tables: dict[str, RateTable] = {}
    for path in sorted(root.glob("*_rate_table.json"), reverse=True):
        table = load_rate_table(path)
        if table is not None:
            tables[table.season] = table
    return tables


def era_comparison(tables: Mapping[str, RateTable], gap_s: float | None) -> dict[str, Any] | None:
    """The same gap's observed rate in every built season, grouped by era.

    This is the evidence for keeping eras apart rather than pooling them, and it
    is worth returning with a prediction: a single number invites the reader to
    assume it generalises across regulations, and these counts show it does not.
    """
    if gap_s is None or not tables:
        return None
    seasons: list[dict[str, Any]] = []
    for season in sorted(tables, reverse=True):
        hit = predict_from_table(tables[season], gap_s)
        if hit is None:
            continue
        seasons.append({
            "season": season,
            "era": SEASON_ERA.get(season, "UNKNOWN"),
            "rate": hit["p_pass_by_outcome_horizon"],
            "interval_low": hit["interval_low"],
            "interval_high": hit["interval_high"],
            "n": hit["n"],
        })
    if not seasons:
        return None
    by_era: dict[str, dict[str, int]] = {}
    for row in seasons:
        bucket = by_era.setdefault(row["era"], {"passes": 0, "n": 0})
        # Recovering counts from rate*n keeps this honest about its own rounding
        # rather than re-reading the tables a second time.
        bucket["passes"] += round(row["rate"] * row["n"])
        bucket["n"] += row["n"]
    pooled = {}
    for era, counts in by_era.items():
        low, high = _wilson(counts["passes"], counts["n"])
        pooled[era] = {
            "rate": round(counts["passes"] / counts["n"], 6) if counts["n"] else None,
            "interval_low": round(low, 6), "interval_high": round(high, 6),
            "n": counts["n"],
        }
    return {"seasons": seasons, "by_era": pooled}


def _repo_root() -> Path:
    return Path(os.environ.get("TRACKSHIFT_ROOT") or Path(__file__).resolve().parents[3])


def predict_from_table(table: RateTable, gap_s: float | None) -> dict[str, Any] | None:
    """The bucket containing `gap_s`, as a p_pass with its counting interval.

    None when the gap is absent or outside the tracked range, or when the bucket
    holding it is empty -- an empty bucket has no observed frequency, and the
    nearest non-empty one is a different question's answer.
    """
    if gap_s is None or not isinstance(gap_s, (int, float)):
        return None
    gap = float(gap_s)
    for bucket in table.buckets:
        if bucket.low_s <= gap < bucket.high_s:
            if bucket.n == 0:
                return None
            return {
                "p_pass_by_outcome_horizon": round(bucket.rate, 6),
                "interval_low": bucket.ci_low,
                "interval_high": bucket.ci_high,
                "n": bucket.n,
                "passes": bucket.passes,
                "gap_bucket_s": [bucket.low_s, bucket.high_s],
            }
    return None
