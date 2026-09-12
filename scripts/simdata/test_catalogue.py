"""The configurator may only offer cars that were on the grid, so the catalogue's
per-event entry list is what these tests pin down. Reads data/2026 directly."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from simdata.catalogue import DATA_ROOT, build_catalogue, discover_events, driver_registry, race_entries

CAT = build_catalogue()
TRACKS = {t["slug"]: t for t in CAT["tracks"]}


def test_every_race_fields_a_plausible_grid():
    # 2026 is an 11-team, 22-car championship; a session missing a car or two in the
    # raw data is normal, a session offering 30 is a registry leak.
    for slug, track in TRACKS.items():
        assert 18 <= len(track["entries"]) <= 22, f"{slug}: {len(track['entries'])} entries"


def test_entries_never_include_a_practice_only_reserve():
    """The registry is the union over every session and is deliberately wider than any
    single grid (measured: 35 entries). No car may reach the configurator that has no
    Race-session presence at the event being configured."""
    assert len(CAT["drivers"]) > 22, "registry should still be the full season union"
    for event in discover_events():
        slug = event.lower().replace(" ", "-")
        race_dir = DATA_ROOT / event / "Race"
        seen = {d.name for d in race_dir.iterdir() if d.is_dir() and len(d.name) == 3}
        import json
        drv_file = race_dir / "drivers.json"
        if drv_file.exists():
            seen |= {r["driver"] for r in json.loads(drv_file.read_text(encoding="utf-8"))["drivers"]
                     if r.get("driver")}
        for entry in TRACKS[slug]["entries"]:
            assert entry["code"] in seen, f"{slug}: {entry['code']} never appeared in the Race"


def test_entries_are_fully_described_and_number_ordered():
    for slug, track in TRACKS.items():
        numbers = []
        for e in track["entries"]:
            for field in ("number", "team", "colour", "firstName", "lastName"):
                assert e[field], f"{slug}/{e['code']}: missing {field}"
            numbers.append(int(e["number"]))
        assert numbers == sorted(numbers), f"{slug}: entries must be in car-number order"
        assert len(set(numbers)) == len(numbers), f"{slug}: duplicate car number"


def test_directory_only_entrant_is_still_named():
    """drivers.json drops entrants in several events (measured: the Chinese Race lists
    18 rows against 22 telemetry directories), so the registry has to fill the gap."""
    entries = race_entries("Chinese Grand Prix", driver_registry(discover_events()))
    by_code = {e["code"]: e for e in entries}
    assert "NOR" in by_code, "an entrant present only as a telemetry directory must survive"
    assert by_code["NOR"]["lastName"] and by_code["NOR"]["number"]
