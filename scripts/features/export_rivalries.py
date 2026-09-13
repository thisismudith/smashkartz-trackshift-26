#!/usr/bin/env python3
"""Export the MEASURED pass-conversion rate per circuit to sim/rivalries.<hash>.json.

The /insights page already ranks a FITTED overtaking-difficulty index. That index is a
model output; this artifact is the observation it should be read against -- of the
opportunities that actually arose in 2026, what fraction actually ended with the
attacker ahead. Putting the two side by side is the whole point, so the measurement has
to arrive in the same shape the page already consumes.

SHAPE. Every entry under ``passConversionRate`` is exactly the ``Leaf`` the frontend
already has (frontend/src/sim/engine/params.ts): {value, ci95, n, provenance, note}.
That is deliberate -- it drops into the insights page's existing fromLeaves() helper and
RankedEffects component with no frontend change at all. Keys are FULL DISPLAY NAMES
("British Grand Prix"), matching params.<hash>.json, so the two rankings line up row for
row.

THE INTERVAL IS COMPUTED HERE. A Wilson score interval on 61 Monaco opportunities is the
difference between "1.6%" read as fact and "1.6%, somewhere in 0.3-8.7%" read as the
near-nothing sample it is. Statistical logic lives outside UI code (AGENTS.md section
32), so the browser receives the interval rather than the formula.

UNLABELLED IS NOT FALSE. ``passed_by_outcome_horizon`` is null when either car has no
position at the zone exit -- a retirement, or a missing sample. Scoring those as failed
passes would bias every rate downward, worst in exactly the races where the best battles
ended early. They are excluded from n, from the numerator and from the rate, and the
count of what was excluded travels with every number so the exclusion is visible rather
than merely correct.

HEAD TO HEAD. The same measurement, cut by WHO rather than by WHERE: ``headToHead.pairs``
keys a Leaf per directed attacker-defender pair, ``attackerTotals`` and ``defenderTotals``
key one per driver. Every value is the identical Leaf, which is why the whole section
drops into the page's existing fromLeaves() helper with no component change -- the same
trick passConversionRate already plays. The counting rules are not re-implemented for
this cut: each key's rows are handed to the SAME summarise_event() and the SAME leaf(),
so "unlabelled is not false" and "one opportunity is one row" hold identically here.

THE DEFENDER ARRIVES AS A CAR NUMBER. M07 knows its attacker by code ("ANT") and its
defender only by number ("63"), because ``driver_ahead_number`` is what the telemetry
reports. Resolving one to the other is battle_join.build_number_to_code()'s job and is
imported from there rather than rewritten: the map is per (year, event, session) because
car numbers are reused between seasons, and a second copy of that rule would be a second
thing to get wrong. A number that does not resolve raises, because a dropped row is a
rivalry that silently loses opportunities to nowhere.

Usage:
    python scripts/features/export_rivalries.py            # write the artifact
    python scripts/features/export_rivalries.py --dry-run  # print the table only
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from trackshift.features.battle_join import build_number_to_code  # noqa: E402
from trackshift.rules.config import load_event_rules  # noqa: E402

# write_json is imported, never reimplemented: the content-hashed filename is how every
# other artifact in frontend/public/sim is named and cache-busted, and a second hashing
# implementation would drift from it. (When build_sim_data.py is itself __main__ this
# imports a second copy of that module -- harmless, since write_json is pure and OUT_DIR
# is derived from __file__.)
from build_sim_data import OUT_DIR, write_json  # noqa: E402

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
RUN_MANIFEST = OPPORTUNITIES / "run_manifest.json"

#: The 20 m lake, read for one thing only: the car-number-to-driver-code map. It is the
#: only table that carries both, which is why the join cannot be made from M07 alone.
LAKE = ROOT / "data" / "processed" / "telemetry_20m"

SCHEMA_VERSION = 1

#: Each opportunity is written once per decision checkpoint. The outcome label is the
#: same on all three, so anchoring on the first makes the row count equal the OPPORTUNITY
#: count instead of three times it.
ANCHOR_CHECKPOINT = "DETECTION"

#: The z that makes the interval a 95% one. Named because the artifact states it.
Z_95 = 1.96

#: Versioned upstream in trackshift.features.opportunities and restated here so a reader
#: of the artifact alone knows which definition of "converted" produced these numbers.
LABEL_DEFINITION = "zone_exit_v1"

NOTE = ("An opportunity is one attacker-defender pair reaching a DRS detection line on "
        "one lap; it converted when that attacker was ahead of that same defender at the "
        "exit of the activation zone (label zone_exit_v1).")

HEAD_TO_HEAD_NOTE = (
    "A directed pair is one attacker behind one defender at a DRS detection line -- "
    "\"ANT → RUS\" counts only the opportunities ANT had against RUS, converted when "
    "ANT was ahead of RUS at the activation zone exit, so \"RUS → ANT\" is a "
    "different rivalry with its own rate.")

COLUMNS = ["year", "event", "session", "decision_checkpoint",
           "passed_by_outcome_horizon"]

#: The head-to-head cut needs the two cars as well. Same rows, same anchor, same label
#: column -- only the grouping key differs, which is why the counting is not duplicated.
PAIR_COLUMNS = COLUMNS + ["attacker", "defender"]

#: What the lake is read for. ``driver`` is the code, ``driver_number`` the number M07
#: knows the defender by; the other three are the key the map is scoped to.
LAKE_COLUMNS = ["year", "event", "session", "driver", "driver_number"]

#: Only sessions that produce opportunities. A practice partition would add numbers for
#: cars that never raced and cannot contradict anything, but reading it is pure cost.
RACING_SESSIONS = ("Race", "Sprint")

#: U+2192 RIGHTWARDS ARROW with a space either side. Spelled once, because the key is the
#: contract: the frontend splits on exactly this separator.
PAIR_ARROW = " → "

#: The arrow does not survive a Windows console's cp1252 encoder. The ARTIFACT always
#: carries U+2192 (write_json encodes it as →); only the human-readable table is
#: transliterated, and only on its way to stdout.
CONSOLE_ARROW = " -> "


class RivalriesError(RuntimeError):
    """The measurement cannot be made honestly from the data on disk."""


def wilson_interval(successes: int, n: int, z: float = Z_95) -> tuple[float, float]:
    """95% Wilson score interval for a proportion, clamped to [0, 1].

    Wilson rather than the textbook normal interval because the rates here are small and
    one of the samples is not: Monaco is 1 pass in 61, where the normal interval runs
    negative (-0.016 to 0.049) and would render as a circuit with a negative conversion
    rate. Wilson stays inside [0, 1] at those counts by construction and still agrees
    with the normal interval where the sample is large, so one formula covers the range.

    The clamp is belt-and-braces against float error at the extremes, not a correction.
    """
    if n <= 0:
        raise RivalriesError("a proportion needs at least one labelled observation")
    if not 0 <= successes <= n:
        raise RivalriesError(f"{successes} successes out of {n} is not a proportion")
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half_width = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, centre - half_width), min(1.0, centre + half_width)


def event_display(slug: str, season: str) -> str:
    """The circuit's full display name, from the rule config and nowhere else.

    ``slug.replace("_", " ").title()`` happens to be right for the eight 2026 circuits
    that currently build, and is wrong the moment one carries an accent or a lowercase
    particle. More to the point, these keys have to match params.<hash>.json exactly or
    the insights page ranks two disjoint sets of rows against each other -- and params is
    keyed from this same rule config. A missing display name is therefore an error, not
    an invitation to guess.
    """
    try:
        rules = load_event_rules(slug, season)
    except Exception as exc:                     # noqa: BLE001 - re-raised with context
        raise RivalriesError(
            f"no {season} rule config for {slug!r}, so its display name cannot be "
            f"resolved: {type(exc).__name__}: {exc}") from exc
    display = rules.get("event_display")
    if not display:
        raise RivalriesError(
            f"{slug!r} has no event_display in its {season} rule config; the artifact "
            f"key must match params.<hash>.json and will not be guessed from the slug")
    return str(display)


def summarise_event(frame: pd.DataFrame) -> dict[str, int]:
    """Reduce one circuit's table to opportunities, its labelled subset, and its passes.

    Anything in the outcome column that is neither True, False nor null is refused rather
    than coerced: a stray string is truthy, and would silently inflate the pass count.
    """
    for column in ("decision_checkpoint", "passed_by_outcome_horizon"):
        if column not in frame.columns:
            raise RivalriesError(f"the opportunities table has no {column!r} column")

    anchored = frame[frame["decision_checkpoint"] == ANCHOR_CHECKPOINT]
    outcome = anchored["passed_by_outcome_horizon"]
    labelled = outcome[outcome.notna()]

    unexpected = {v for v in labelled.unique().tolist() if v not in (True, False)}
    if unexpected:
        raise RivalriesError(
            f"passed_by_outcome_horizon holds {sorted(map(repr, unexpected))}, which is "
            f"neither a label nor a missing label")

    return {
        "opportunities": int(len(anchored)),
        "labelled": int(len(labelled)),
        "unlabelled": int(len(anchored) - len(labelled)),
        "passes": int(sum(1 for value in labelled if bool(value))),
    }


def leaf(counts: dict[str, int]) -> dict[str, Any]:
    """One circuit's conversion rate as the frontend's Leaf, interval included.

    Rounded to five decimals: the sixth is noise at these sample sizes, and an unrounded
    float would let the artifact's content hash -- and therefore its filename -- change
    on a rerun that measured exactly the same thing. The note carries the raw counts so a
    reader can redo the division and see how much was excluded doing it.
    """
    n, passes = counts["labelled"], counts["passes"]
    low, high = wilson_interval(passes, n)
    return {
        "value": round(passes / n, 5),
        "ci95": [round(low, 5), round(high, 5)],
        "n": n,
        "provenance": "DERIVED",
        "note": (f"{passes} of {n} labelled opportunities converted; "
                 f"{counts['unlabelled']} unlabelled excluded"),
    }


def _source_label() -> str:
    """Name the dataset AND the schema version that produced it, from its own manifest.

    A schema bump that changes what an opportunity IS would otherwise be invisible in the
    artifact, and the insights page would go on ranking numbers whose definition moved.
    """
    version = "m07_overtake_opportunities_v1"
    if RUN_MANIFEST.exists():
        try:
            manifest = json.loads(RUN_MANIFEST.read_text(encoding="utf-8"))
            version = str(manifest.get("schema_version") or version)
        except (OSError, AttributeError, json.JSONDecodeError):
            pass                      # the manifest is provenance, not a hard dependency
    return f"data/processed/overtake_opportunities ({version})"


def measure(root: Path = OPPORTUNITIES) -> tuple[dict[str, dict[str, int]], str, list[str]]:
    """Count every event partition. Returns (counts by slug, season, sessions).

    The partition directory is the authoritative circuit key; a frame whose own ``event``
    column disagrees with it would attribute one circuit's passes to another, so the two
    are checked against each other rather than one being trusted.
    """
    files = sorted(root.glob("event=*/opportunities.parquet"))
    if not files:
        raise RivalriesError(f"no event partitions under {root}")

    counts: dict[str, dict[str, int]] = {}
    seasons: set[str] = set()
    sessions: set[str] = set()
    for path in files:
        slug = path.parent.name.split("=", 1)[-1]
        frame = pd.read_parquet(path, columns=COLUMNS)
        mismatched = set(frame["event"].unique().tolist()) - {slug}
        if mismatched:
            raise RivalriesError(f"{path} is partitioned as {slug!r} but holds "
                                 f"{sorted(mismatched)}")
        seasons.update(str(year) for year in frame["year"].unique().tolist())
        sessions.update(str(s) for s in frame["session"].unique().tolist())
        counts[slug] = summarise_event(frame)

    # One artifact, one season: display names are resolved against a season's rule
    # config, and a mixed set would key two seasons of circuits into one ranking.
    if len(seasons) != 1:
        raise RivalriesError(f"expected exactly one season, found {sorted(seasons)}")
    return counts, seasons.pop(), sorted(sessions)


def pair_key(attacker: str, defender_code: str) -> str:
    """The one spelling of a directed rivalry key, used for building and for parsing."""
    return f"{attacker}{PAIR_ARROW}{defender_code}"


def load_number_to_code(lake: Path = LAKE) -> dict[tuple[str, str, str, str], str]:
    """``(year, event, session, car number) -> driver code``, built by battle_join.

    The map itself is NOT built here -- build_number_to_code owns the rules that make it
    correct (numbers canonicalised across the "44"/"44.0"/NaN spellings the lake writers
    produce; a number claimed by two codes in one session refused rather than resolved to
    whichever row came last). This function only decides which partitions feed it.

    The lake's ``event`` is the display name ("British Grand Prix"); M07's is the slug
    ("british_grand_prix"). Nothing is renamed to bridge that -- the opportunity side is
    put through event_display(), the same rule config params.<hash>.json is keyed from, so
    the two names meet at the one place that already defines them.
    """
    files = [path for path in sorted(lake.glob("year=*/event=*/session=*/telemetry_20m.parquet"))
             if path.parent.name.split("=", 1)[-1] in RACING_SESSIONS]
    if not files:
        raise RivalriesError(
            f"no {' or '.join(RACING_SESSIONS)} partitions under {lake}; the defender is "
            f"a car number in M07 and only the lake says whose number it is, so the "
            f"head-to-head cut cannot be measured without it")
    frame = pd.concat([pd.read_parquet(path, columns=LAKE_COLUMNS).drop_duplicates()
                       for path in files], ignore_index=True).drop_duplicates()
    return build_number_to_code(frame.to_dict("records"))


def resolve_defender_codes(frame: pd.DataFrame, display: str,
                           number_to_code: dict[tuple[str, str, str, str], str],
                           source: Any = "<frame>") -> list[str]:
    """Every row's defender as a driver code. An unresolved number raises.

    Dropping the row instead would be the quiet failure: the season total would still
    read 4,970 while some rivalry silently lost opportunities to nowhere, and nothing on
    the page would say so. On the 2026 data every one of the 4,970 opportunities resolves,
    so a miss here means the join is keyed wrong -- most likely the event name -- and that
    is worth stopping for.
    """
    for column in ("year", "session", "defender"):
        if column not in frame.columns:
            raise RivalriesError(f"the opportunities table has no {column!r} column")

    years = frame["year"].astype(str).tolist()
    sessions = frame["session"].astype(str).tolist()
    # Whitespace only: the number is not reformatted here, because build_number_to_code
    # already canonicalised the lake side and a second normaliser would be the second
    # implementation this module exists to avoid.
    numbers = frame["defender"].astype(str).str.strip().tolist()

    wanted = {(year, display, session, number)
              for year, session, number in zip(years, sessions, numbers)}
    resolved = {key: number_to_code.get(key) for key in wanted}
    missing = sorted(key for key, code in resolved.items() if code is None)
    if missing:
        raise RivalriesError(
            f"{source}: {len(missing)} (year, event, session, car number) combinations "
            f"have no driver code in {LAKE.name}, e.g. {missing[:5]}. Every 2026 "
            f"opportunity resolves, so this is a wrong join key -- check that "
            f"{display!r} is spelled as the lake spells its event -- not a data gap, and "
            f"the rows are not dropped to make it go away")
    return [resolved[(year, display, session, number)]
            for year, session, number in zip(years, sessions, numbers)]


def _summarise_groups(frame: pd.DataFrame, by: str) -> dict[str, dict[str, int]]:
    """One counts dict per distinct value of ``by``, each from summarise_event.

    Deliberately the same summariser the per-circuit path uses: the anchor on DETECTION,
    the exclusion of unlabelled outcomes and the refusal of a non-boolean label are then
    literally the same code here, so the two cuts of the season cannot drift apart.
    """
    return {str(key): summarise_event(group)
            for key, group in frame.groupby(by, sort=True)}


def measure_pairs(root: Path, season: str, lake: Path = LAKE,
                  number_to_code: dict[tuple[str, str, str, str], str] | None = None,
                  ) -> dict[str, dict[str, dict[str, int]]]:
    """Count the same opportunities by rivalry instead of by circuit.

    Returns {"pairs" | "attackers" | "defenders": {key: counts}}. The two totals are
    aggregated from the ROWS, not averaged from the pair rates: a driver with one
    98-opportunity rivalry and one 3-opportunity rivalry converts at his row rate, and a
    mean of the two pair rates would let the 3 count as much as the 98.
    """
    files = sorted(root.glob("event=*/opportunities.parquet"))
    if not files:
        raise RivalriesError(f"no event partitions under {root}")
    if number_to_code is None:
        number_to_code = load_number_to_code(lake)

    frames = []
    for path in files:
        slug = path.parent.name.split("=", 1)[-1]
        frame = pd.read_parquet(path, columns=PAIR_COLUMNS)
        codes = resolve_defender_codes(frame, event_display(slug, season),
                                       number_to_code, path)
        frames.append(frame.assign(defender_code=codes))

    frame = pd.concat(frames, ignore_index=True)
    frame["pair"] = [pair_key(str(a), str(d))
                     for a, d in zip(frame["attacker"], frame["defender_code"])]
    return {
        "pairs": _summarise_groups(frame, "pair"),
        "attackers": _summarise_groups(frame, "attacker"),
        "defenders": _summarise_groups(frame, "defender_code"),
    }


def _leaves(counts: dict[str, dict[str, int]], kind: str) -> dict[str, dict[str, Any]]:
    """Leaves for every key that has something to divide, sorted, the rest announced.

    A rivalry whose every opportunity is unlabelled is LEFT OUT rather than emitted as
    zero, exactly as a circuit with no labelled outcome is: there is no rate to report and
    a Leaf has nowhere to say why. Sorted so the same measurement hashes to the same
    filename.
    """
    empty = sorted(key for key, c in counts.items() if c["labelled"] == 0)
    if empty:
        shown = ", ".join(key.replace(PAIR_ARROW, CONSOLE_ARROW) for key in empty[:6])
        print(f"  ({len(empty)} {kind} with no labelled opportunity omitted rather than "
              f"reported as zero: {shown}{', ...' if len(empty) > 6 else ''})")
    return {key: leaf(counts[key]) for key in sorted(counts) if counts[key]["labelled"]}


def build_head_to_head(measured: dict[str, dict[str, dict[str, int]]],
                       ) -> dict[str, dict[str, Any]]:
    """The three new artifact sections, each keyed by rivalry or by driver.

    ``counts`` restates each pair's denominator beside the Leaf that divided by it, so a
    reader can redo the arithmetic without parsing the note. ``opportunities`` there is
    the LABELLED denominator -- the same number as the Leaf's ``n`` -- and ``unlabelled``
    is what was set aside to get it; the raw row count of the rivalry is their sum.
    """
    pairs = _leaves(measured["pairs"], "rivalries")
    return {
        "headToHead": {
            "note": HEAD_TO_HEAD_NOTE,
            "pairs": pairs,
            # Keyed identically to pairs, so the two never disagree about what exists.
            "counts": {key: {"opportunities": measured["pairs"][key]["labelled"],
                             "passes": measured["pairs"][key]["passes"],
                             "unlabelled": measured["pairs"][key]["unlabelled"]}
                       for key in pairs},
        },
        "attackerTotals": _leaves(measured["attackers"], "attackers"),
        "defenderTotals": _leaves(measured["defenders"], "defenders"),
    }


def build_artifact(counts: dict[str, dict[str, int]], season: str,
                   sessions: list[str],
                   measured_pairs: dict[str, dict[str, dict[str, int]]] | None = None,
                   ) -> dict[str, Any]:
    """Assemble the artifact from measured counts.

    A circuit with opportunities but none of them labelled is LEFT OUT of
    passConversionRate rather than emitted as zero -- there is no rate to report, and a
    Leaf has nowhere to say why. Its opportunities still count in the totals, and the
    omission is announced on stdout, so it reads as missing rather than as absent.
    """
    rates: dict[str, dict[str, Any]] = {}
    for slug, per_event in counts.items():
        display = event_display(slug, season)
        if per_event["labelled"] == 0:
            print(f"  ({display}: {per_event['opportunities']} opportunities, none "
                  f"labelled -- omitted rather than reported as zero)")
            continue
        rates[display] = leaf(per_event)

    totals: dict[str, Any] = {
        key: sum(c[key] for c in counts.values())
        for key in ("opportunities", "labelled", "unlabelled", "passes")
    }
    totals["events"] = len(counts)
    totals["sessions"] = sessions

    artifact = {
        "schemaVersion": SCHEMA_VERSION,
        "season": season,
        "labelDefinition": LABEL_DEFINITION,
        "provenance": "DERIVED",
        "source": _source_label(),
        "note": NOTE,
        "totals": totals,
        # Sorted so the same measurement always hashes to the same filename.
        "passConversionRate": {key: rates[key] for key in sorted(rates)},
    }
    # Additive: schemaVersion still means what it meant, passConversionRate and totals are
    # untouched, and a consumer that has never heard of headToHead reads the artifact
    # exactly as before. Omitted entirely when no pair measurement was supplied -- an
    # empty section would claim there are no rivalries, which is a different statement
    # from not having looked.
    if measured_pairs is not None:
        artifact.update(build_head_to_head(measured_pairs))
    return artifact


def build_rivalries(root: Path = OPPORTUNITIES, lake: Path = LAKE) -> dict[str, Any]:
    """Measure and assemble in one call -- what build_sim_data.py imports."""
    counts, season, sessions = measure(root)
    return build_artifact(counts, season, sessions,
                          measure_pairs(root, season, lake))


def print_table(counts: dict[str, dict[str, int]], artifact: dict[str, Any]) -> None:
    """The numbers the artifact carries, laid out for a human to check by hand."""
    print("  circuit                   n   passes   rate    ci95 (Wilson, z=1.96)")
    for slug in sorted(counts):
        per_event = counts[slug]
        n, passes = per_event["labelled"], per_event["passes"]
        if n == 0:
            print(f"  {slug:<24}{0:>4}{0:>7}      --   (no labelled opportunity)")
            continue
        low, high = wilson_interval(passes, n)
        print(f"  {slug:<24}{n:>4}{passes:>7}    {passes / n:.3f}   "
              f"[{low:.3f}, {high:.3f}]")
    totals = artifact["totals"]
    print(f"\n  Totals: {totals['opportunities']} opportunities, {totals['labelled']} "
          f"labelled, {totals['unlabelled']} unlabelled, {totals['passes']} passes.")
    print(f"  {totals['events']} events, sessions {', '.join(totals['sessions'])}, "
          f"season {artifact['season']}, keyed as "
          f"{', '.join(sorted(artifact['passConversionRate']))}.")


def print_head_to_head(artifact: dict[str, Any],
                       measured: dict[str, dict[str, dict[str, int]]],
                       top: int = 6) -> None:
    """The busiest rivalries and the same season counted a second way.

    The two totals lines are the check worth having by eye: attackerTotals and
    defenderTotals partition the SAME labelled rows two different ways, so both must sum
    to the season's labelled count and its pass count. If they do not, a row was dropped
    or double-counted in the join.
    """
    head = artifact.get("headToHead")
    if not head:
        return
    counts = head["counts"]
    print(f"\n  {len(head['pairs'])} directed rivalries "
          f"({sum(1 for c in counts.values() if c['opportunities'] >= 10)} with 10+ "
          f"labelled opportunities). Busiest:")
    print("  rivalry            n   passes   rate    ci95 (Wilson, z=1.96)")
    busiest = sorted(counts, key=lambda k: (-counts[k]["opportunities"], k))[:top]
    for key in busiest:
        n, passes = counts[key]["opportunities"], counts[key]["passes"]
        low, high = head["pairs"][key]["ci95"]
        print(f"  {key.replace(PAIR_ARROW, CONSOLE_ARROW):<17}{n:>4}{passes:>7}    "
              f"{passes / n:.3f}   [{low:.3f}, {high:.3f}]")
    for label, section in (("attackers", "attackerTotals"), ("defenders", "defenderTotals")):
        per_driver = measured[label].values()
        print(f"  {len(artifact[section])} {label}, "
              f"{sum(c['labelled'] for c in per_driver)} labelled rows, "
              f"{sum(c['passes'] for c in per_driver)} passes "
              f"(both cuts re-count the same season).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the per-circuit table and write nothing")
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR,
                    help=f"where the artifact is written (default {OUT_DIR})")
    args = ap.parse_args()

    counts, season, sessions = measure()
    measured = measure_pairs(OPPORTUNITIES, season)
    artifact = build_artifact(counts, season, sessions, measured)
    print_table(counts, artifact)
    print_head_to_head(artifact, measured)
    if args.dry_run:
        print("\n  (dry run: nothing written)")
        return
    args.out_dir.mkdir(parents=True, exist_ok=True)
    name = write_json(artifact, args.out_dir, "rivalries")
    print(f"\n  wrote {name}")


if __name__ == "__main__":
    main()
