import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trackshift.resample import resample_lap
def make_rows(distance=(0, 40)):
    tel = {"distance":list(distance), "time":[0,4], "speed":[100,140], "throttle":[20,60], "gear":[3,5], "brake":[0,1], "drs":[0,1], "DriverAhead":[None,"44"]}
    return resample_lap(tel, {"year":"2026", "event":"Test", "session":"Sprint", "driver":"HAM", "lap":1}, {}, "test.json", 2, 20)
def test_continuous_fields_interpolate_at_20m():
    row = make_rows()[1]
    assert row["distance_m"] == 20 and row["speed_kmh"] == 120 and row["throttle_pct"] == 40
def test_discrete_fields_hold_preceding_sample_and_null_ahead():
    row = make_rows()[1]
    assert row["gear"] == 3 and row["brake_on"] == 0 and row["driver_ahead_number"] is None
def test_no_extrapolation_after_final_sample(): assert [row["distance_m"] for row in make_rows((0,35))] == [0.0,20.0]
