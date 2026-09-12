"""Discovery and metadata joining for raw TrackShift telemetry."""
from __future__ import annotations
import json, re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from .validation import ValidationResult, validate_telemetry_object

@dataclass(frozen=True)
class RawLap:
    year: str; event: str; session: str; driver: str; lap: int; path: Path

def _unwrap(value: Any, key: str) -> Any: return value.get(key, value) if isinstance(value, dict) else value

def _records(value: Any, key: str) -> list[dict[str, Any]]:
    """Normalise a TracingInsights JSON document to a list of per-row dicts.

    The source is inconsistent: some files are a list of records, some wrap that
    list under a key, and laptimes.json is *columnar* -- a dict of equal-length
    lists keyed by field name. Handling only the first two shapes silently
    produced empty metadata for every lap, because a columnar dict is not a
    list and the old guard simply returned nothing.

    Scalar entries alongside the lists (a session-wide constant) are broadcast
    to every row rather than dropped.
    """
    value = _unwrap(value, key)
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        columns = {name: column for name, column in value.items() if isinstance(column, list)}
        if not columns:
            return []
        scalars = {name: v for name, v in value.items() if not isinstance(v, list)}
        rows = max(len(column) for column in columns.values())
        return [
            {**scalars, **{name: (column[i] if i < len(column) else None) for name, column in columns.items()}}
            for i in range(rows)
        ]
    return []
def _lap_number(path: Path) -> int | None:
    match = re.match(r"(\d+)_tel\.json$", path.name); return int(match.group(1)) if match else None

def discover_laps(raw_root: Path, year: str, event: str, session: str, drivers: Iterable[str] | None = None, laps: Iterable[int] | None = None, max_laps_per_driver: int | None = None) -> list[RawLap]:
    root = raw_root / str(year) / event / session; wanted_drivers = set(drivers) if drivers else None; wanted_laps = set(laps) if laps else None; discovered = []
    if not root.is_dir(): return discovered
    for driver_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if wanted_drivers and driver_dir.name not in wanted_drivers: continue
        selected = [RawLap(str(year), event, session, driver_dir.name, lap, path) for path in sorted(driver_dir.glob("*_tel.json"), key=lambda x: (_lap_number(x) is None, _lap_number(x) or 0)) if (lap := _lap_number(path)) is not None and (not wanted_laps or lap in wanted_laps)]
        discovered.extend(selected[:max_laps_per_driver] if max_laps_per_driver is not None else selected)
    return discovered

def load_raw_lap(raw_lap: RawLap) -> tuple[dict[str, Any] | None, ValidationResult]:
    try:
        with raw_lap.path.open(encoding="utf-8") as handle: payload = json.load(handle)
    except json.JSONDecodeError as exc: return None, ValidationResult("REJECTED", "MALFORMED_JSON", str(exc))
    except OSError as exc: return None, ValidationResult("REJECTED", "MALFORMED_JSON", str(exc))
    return payload, validate_telemetry_object(payload)

# Canonical name -> the raw keys it may appear under, first match wins.
# The short codes (lST, iacc, pin, pout, del, ...) are the names TracingInsights
# actually uses, documented in each season's data_dictionary.json. Four canonical
# fields previously listed only guessed spellings and so never resolved.
METADATA_ALIASES = {
    "lap_time_s": ("time", "lap_time", "lapTime"),
    "session_time_s": ("sesT", "session_time", "sessionTime"),
    "lap_start_session_s": ("lST", "lap_start_session_s", "lapStartSession", "start_time", "startTime"),
    "sector_1_s": ("s1", "sector_1", "sector1"),
    "sector_2_s": ("s2", "sector_2", "sector2"),
    "sector_3_s": ("s3", "sector_3", "sector3"),
    "race_position": ("pos", "position"),
    "tyre_compound": ("compound", "tyre_compound"),
    "tyre_life_laps": ("life", "tyre_life"),
    "tyre_is_new": ("fresh",),
    "stint": ("stint",),
    "team": ("team",),
    "driver_number": ("dNum",),
    "track_status": ("status", "track_status"),
    "is_accurate": ("iacc", "is_accurate", "accurate"),
    "is_personal_best": ("pb",),
    "lap_deleted": ("del",),
    "lap_deleted_reason": ("delR",),
    "speed_trap_kmh": ("vst",),
    "qualifying_segment": ("qs",),
    "pit_in_session_s": ("pin", "pit_in_time", "pitInTime", "pit_in_session_s"),
    "pit_out_session_s": ("pout", "pit_out_time", "pitOutTime", "pit_out_session_s"),
}

def load_lap_metadata(raw_lap: RawLap) -> dict[str, Any]:
    empty = {name: None for name in METADATA_ALIASES}; path = raw_lap.path.parent / "laptimes.json"
    if not path.exists(): return empty
    try:
        with path.open(encoding="utf-8-sig") as handle: entries = _records(json.load(handle), "laptimes")
    except (OSError, json.JSONDecodeError): return empty
    if not entries: return empty
    entry = next((item for item in entries if isinstance(item, dict) and str(item.get("lap", item.get("lap_number", ""))) == str(raw_lap.lap)), None)
    if entry is None: return empty
    return {target: _clean(next((entry[key] for key in aliases if key in entry), None))
            for target, aliases in METADATA_ALIASES.items()}


def _clean(value: Any) -> Any:
    """Map the source's sentinels to a real None.

    TracingInsights writes the literal string "None" for an absent value. Left
    alone it is truthy, so `if pit_out_session_s:` would read a missing pit exit
    as a real one. Missing must be missing (AGENTS.md section 11).
    """
    return None if value in (None, "None", "", "nan", "NaN") else value
