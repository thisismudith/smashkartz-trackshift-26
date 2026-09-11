"""Distance-based telemetry resampling."""
from __future__ import annotations
from bisect import bisect_right
from math import floor
from typing import Any
CONTINUOUS = {"time":"lap_elapsed_s", "rpm":"engine_rpm", "speed":"speed_kmh", "throttle":"throttle_pct", "rel_distance":"lap_fraction", "DistanceToDriverAhead":"gap_ahead_m", "acc_x":"acc_longitudinal_mps2", "acc_y":"acc_lateral_mps2", "acc_z":"acc_vertical_mps2", "x":"x_m", "y":"y_m", "z":"z_m"}
DISCRETE = {"gear":"gear", "brake":"brake_on", "drs":"drs_open", "DriverAhead":"driver_ahead_number"}
def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "None": return None
    try: return float(value)
    except (TypeError, ValueError): return None
def _interpolate(distance: list[float], values: list[Any], point: float) -> float | None:
    pairs = [(x, y) for x, value in zip(distance, values) if (y := _numeric(value)) is not None]
    if not pairs: return None
    xs, ys = zip(*pairs); index = bisect_right(xs, point)
    if index == 0 or point < xs[0] or point > xs[-1]: return None
    if index == len(xs) or xs[index - 1] == point: return ys[index - 1]
    x0, x1, y0, y1 = xs[index - 1], xs[index], ys[index - 1], ys[index]
    return y0 + (y1 - y0) * (point - x0) / (x1 - x0)
def resample_lap(tel: dict[str, Any], identifiers: dict[str, Any], metadata: dict[str, Any], source_file: str, source_rows: int, spacing_m: float) -> list[dict[str, Any]]:
    distance = [float(value) for value in tel["distance"]]; max_distance = distance[-1]
    grid = [float(index * spacing_m) for index in range(floor(max_distance / spacing_m) + 1) if index * spacing_m >= distance[0]]
    rows = []
    for point in grid:
        preceding = max(0, bisect_right(distance, point) - 1)
        row = {**identifiers, "distance_m":point, "source_file":source_file, "source_rows":source_rows, "resample_spacing_m":spacing_m, "validation_status":"ACCEPTED", **metadata}
        for raw, output in CONTINUOUS.items(): row[output] = _interpolate(distance, tel.get(raw, [None] * len(distance)), point)
        for raw, output in DISCRETE.items():
            values = tel.get(raw, [None] * len(distance)); row[output] = values[preceding] if preceding < len(values) and values[preceding] != "None" else None
        rows.append(row)
    return rows
