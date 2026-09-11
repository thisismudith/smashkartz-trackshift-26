import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from trackshift.validation import validate_telemetry_object
def fixture(distance=(0, 20, 40)):
    return {"tel":{"time":[0,1,2], "distance":list(distance), "speed":[100,110,120], "throttle":[50,60,70], "brake":[0,1,0], "drs":[0,1,1], "gear":[4,5,6]}}
def test_clean_monotonic_lap_is_accepted(): assert validate_telemetry_object(fixture()).status == "ACCEPTED"
def test_distance_decrease_is_rejected(): assert validate_telemetry_object(fixture((0,30,20))).rejection_code == "NON_MONOTONIC_DISTANCE"
def test_array_length_mismatch_is_visible():
    data = fixture(); data["tel"]["speed"] = [100]
    assert validate_telemetry_object(data).rejection_code == "ARRAY_LENGTH_MISMATCH"
