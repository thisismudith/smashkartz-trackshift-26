"""Lap- and segment-level context for M07 opportunities (CP-13 feature groups).

CP-13 specifies tyre, weather and geometry features on every opportunity row.
The builder previously emitted only the gap/eligibility core, which left CP-14
fitting on three features and CP-23 with no feature group to ablate. The data
was never missing -- it sits in the C1 segments and the CP-06 weather overlay,
keyed on exactly the same ``(year, event, session, driver, lap)`` M07 already
has.

Two joins, because the quantities behave differently:

**Lap-level** (tyre compound, tyre age, team). Constant within a lap except
across a pit stop, so one value per ``(session, driver, lap)`` is the honest
resolution. Taken at the *first* segment of the lap rather than averaged: a
compound is categorical, and a tyre age that changed mid-lap changed because the
car pitted, in which case the entry value is the one the opportunity was run on.

**Segment-level** (corner type, sector). These vary along the lap, so they are
resolved at the checkpoint's own distance rather than per lap. Resolving them
per lap would attach a straight's geometry to a corner's opportunity.

Both sides of the pair are looked up, because CP-13's features are per-pair and
a defender on fresh softs is a different proposition from one on old hards. The
defender is addressed by driver code, which the caller resolves from the car
number through the same map the C8 battle join uses.

Nothing here is invented. A driver-lap with no overlay row yields ``None`` for
that feature, never a default: a fabricated compound would be a silent claim
about a car's strategy.
"""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from typing import Any

__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "CONTEXT_FIELDS",
    "CONTEXT_NUMERIC_FIELDS",
    "CONTEXT_BOOL_FIELDS",
    "GEOMETRY_FIELDS",
    "coerce_context_dtypes",
    "LAP_CONTEXT_COLUMNS",
    "WEATHER_COLUMNS",
    "LapContext",
    "SegmentGeometry",
    "build_lap_context",
    "build_segment_geometry",
    "pair_context",
]

CONTEXT_SCHEMA_VERSION = "m07_opportunity_context_v1"

#: Per-driver, per-lap columns taken from C1 segments.
LAP_CONTEXT_COLUMNS = ("tyre_compound", "tyre_life_laps", "team")

#: CP-06 weather columns carried onto the opportunity. ``track_temperature_c``
#: is the overlay's name; it is emitted as the registered ``track_temperature``.
WEATHER_COLUMNS = {
    "wind_head_component_mps": "wind_head_component_mps",
    "wind_cross_component_mps": "wind_cross_component_mps",
    "wet_track_flag": "wet_track_flag",
    "track_temperature_c": "track_temperature",
}


#: Every context column an opportunity row carries, always, in this order.
#:
#: Emitted even when the source overlay is absent for an event, because a
#: partition that simply lacks the column and one that has no value make
#: different claims, and only the second is true. Stable columns also keep the
#: M07 table loadable as one frame: the loader refuses partitions that disagree
#: on their schema, which is the right guard and would otherwise fire whenever
#: one circuit's weather join came back empty.
CONTEXT_FIELDS: tuple[str, ...] = (
    "attacker_tyre_compound", "defender_tyre_compound",
    "attacker_tyre_life_laps", "defender_tyre_life_laps",
    "tyre_life_delta_laps", "tyre_compound_pair",
    "attacker_team", "defender_team",
    "wind_head_component_mps", "wind_cross_component_mps",
    "track_temperature", "wet_track_flag",
)

#: Geometry resolved at the checkpoint distance rather than per lap.
GEOMETRY_FIELDS: tuple[str, ...] = ("corner_type", "sector")

#: Context fields that must land as floats, and those that must land as bools.
#:
#: Stable *names* are not enough: an event whose weather overlay is missing
#: writes an all-None column, pandas types it ``object``, and concatenating that
#: with another event's ``float64`` yields ``object`` for the whole table. The
#: feature selector reads dtype as the strong signal for categorical-vs-numeric,
#: so one weatherless circuit would silently turn track temperature and both
#: wind components into categoricals across every event.
CONTEXT_NUMERIC_FIELDS: tuple[str, ...] = (
    "attacker_tyre_life_laps", "defender_tyre_life_laps", "tyre_life_delta_laps",
    "wind_head_component_mps", "wind_cross_component_mps", "track_temperature",
)

CONTEXT_BOOL_FIELDS: tuple[str, ...] = ("wet_track_flag",)


def coerce_context_dtypes(frame):
    """Force the declared dtype on every context column present in ``frame``."""
    import pandas as pd

    for name in CONTEXT_NUMERIC_FIELDS:
        if name in frame.columns:
            frame[name] = pd.to_numeric(frame[name], errors="coerce").astype("float64")
    for name in CONTEXT_BOOL_FIELDS:
        if name in frame.columns:
            # A genuinely unknown wet flag stays unknown; "boolean" is the
            # nullable dtype, so an absent overlay does not read as "dry".
            frame[name] = frame[name].astype("boolean")
    return frame


class LapContext(dict):
    """``(session, driver, lap) -> {column: value}``."""


class SegmentGeometry:
    """Distance-indexed geometry lookup for one ``(session, driver, lap)``.

    Segments tile the lap, so a checkpoint distance falls in exactly one. The
    lookup is a binary search over segment starts rather than a scan, because it
    runs once per opportunity per checkpoint.
    """

    def __init__(self, by_key: Mapping[tuple, list[tuple[float, float, dict[str, Any]]]]):
        self._by_key = {k: sorted(v) for k, v in by_key.items()}
        self._starts = {k: [row[0] for row in v] for k, v in self._by_key.items()}

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_key.values())

    def at(self, session: Any, driver: Any, lap: Any, distance_m: Any) -> dict[str, Any]:
        """Geometry of the segment containing ``distance_m``; empty when unknown."""
        key = (str(session), str(driver), _int_or_none(lap))
        bucket = self._by_key.get(key)
        if not bucket:
            return {name: None for name in GEOMETRY_FIELDS}
        try:
            position = float(distance_m)
        except (TypeError, ValueError):
            return {name: None for name in GEOMETRY_FIELDS}
        if position != position:
            return {name: None for name in GEOMETRY_FIELDS}
        empty = {name: None for name in GEOMETRY_FIELDS}
        index = bisect_right(self._starts[key], position) - 1
        if index < 0:
            return empty
        start, end, payload = bucket[index]
        # The last segment of a lap can end a little short of the finish line
        # after resampling, so a distance past the final end is still that
        # segment's; a distance before the first start is not any segment's.
        if position > end and index != len(bucket) - 1:
            return empty
        return {**empty, **payload}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean(value: Any) -> Any:
    """NaN and pandas NA become None, so a gap reads as absent, not as a number."""
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    try:
        import pandas as pd

        if value is pd.NA or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
            return None
    except (ImportError, TypeError, ValueError):
        pass
    return value


def build_lap_context(segments, weather=None) -> LapContext:
    """One context row per ``(session, driver, lap)`` from C1 and CP-06.

    ``segments`` and ``weather`` are frames already filtered to one event and
    year. Weather is averaged over the lap for its numeric components and taken
    as "any" for the wet flag: it is sampled roughly every 40 s, so within-lap
    variation is small, while a lap that saw rain at all is a wet lap.
    """
    import pandas as pd

    context = LapContext()
    if segments is None or len(segments) == 0:
        return context

    keys = ["session", "driver", "lap"]
    present = [c for c in LAP_CONTEXT_COLUMNS if c in segments.columns]
    if present:
        # First segment of the lap: see the module docstring on why not a mean.
        ordered = segments.sort_values(
            [c for c in (*keys, "start_distance_m") if c in segments.columns],
            kind="stable")
        for key, row in ordered.groupby(keys, sort=False).first().iterrows():
            session, driver, lap = key
            context[(str(session), str(driver), _int_or_none(lap))] = {
                name: _clean(row.get(name)) for name in present
            }

    if weather is None or len(weather) == 0:
        return context

    available = {src: dst for src, dst in WEATHER_COLUMNS.items() if src in weather.columns}
    if not available:
        return context
    numeric = [s for s in available if s != "wet_track_flag"]
    grouped = weather.groupby(keys, sort=False)
    means = grouped[numeric].mean(numeric_only=True) if numeric else None
    wet = grouped["wet_track_flag"].any() if "wet_track_flag" in available else None

    for key in (means.index if means is not None else (wet.index if wet is not None else [])):
        session, driver, lap = key
        entry = context.setdefault((str(session), str(driver), _int_or_none(lap)), {})
        if means is not None:
            # Iterate what the aggregation actually produced, not what was
            # asked for: ``numeric_only=True`` silently drops a column that is
            # entirely null in this partition, and indexing it by name after
            # that raises rather than reporting the gap.
            for source in means.columns:
                entry[available[source]] = _clean(means.loc[key, source])
        if wet is not None:
            entry["wet_track_flag"] = bool(wet.loc[key])
    return context


def build_segment_geometry(segments, columns=("corner_type", "sector")) -> SegmentGeometry:
    """Distance-indexed geometry per ``(session, driver, lap)``."""
    by_key: dict[tuple, list[tuple[float, float, dict[str, Any]]]] = {}
    if segments is None or len(segments) == 0:
        return SegmentGeometry(by_key)
    present = [c for c in columns if c in segments.columns]
    needed = {"session", "driver", "lap", "start_distance_m", "end_distance_m"}
    if not present or not needed.issubset(set(segments.columns)):
        return SegmentGeometry(by_key)

    for row in segments.itertuples():
        start, end = _clean(row.start_distance_m), _clean(row.end_distance_m)
        if start is None or end is None:
            continue
        key = (str(row.session), str(row.driver), _int_or_none(row.lap))
        by_key.setdefault(key, []).append(
            (float(start), float(end), {name: _clean(getattr(row, name, None))
                                        for name in present}))
    return SegmentGeometry(by_key)


def pair_context(lap_context: LapContext, *, session: Any, lap: Any,
                 attacker: Any, defender_code: Any) -> dict[str, Any]:
    """Attacker and defender lap context, flattened onto CP-13's names.

    Shared quantities (weather) are emitted once from the attacker's row; the
    two cars are metres apart and cannot be in different weather. Per-car
    quantities are prefixed. A car with no context row contributes nothing
    rather than a default.
    """
    lap_number = _int_or_none(lap)
    attacker_row = lap_context.get((str(session), str(attacker), lap_number), {})
    defender_row = (lap_context.get((str(session), str(defender_code), lap_number), {})
                    if defender_code else {})

    out: dict[str, Any] = {name: None for name in CONTEXT_FIELDS}
    for source, target in (("tyre_compound", "tyre_compound"),
                           ("tyre_life_laps", "tyre_life_laps"),
                           ("team", "team")):
        if source in attacker_row:
            out[f"attacker_{target}"] = attacker_row[source]
        if source in defender_row:
            out[f"defender_{target}"] = defender_row[source]

    # Weather is a property of the track at that moment, not of a car.
    for name in ("wind_head_component_mps", "wind_cross_component_mps",
                 "track_temperature", "wet_track_flag"):
        if name in attacker_row:
            out[name] = attacker_row[name]

    attacker_life = out.get("attacker_tyre_life_laps")  # noqa: E501
    defender_life = out.get("defender_tyre_life_laps")
    if attacker_life is not None and defender_life is not None:
        out["tyre_life_delta_laps"] = float(attacker_life) - float(defender_life)
    attacker_compound = out.get("attacker_tyre_compound")
    defender_compound = out.get("defender_tyre_compound")
    if attacker_compound is not None and defender_compound is not None:
        out["tyre_compound_pair"] = f"{attacker_compound}|{defender_compound}"
    return out
