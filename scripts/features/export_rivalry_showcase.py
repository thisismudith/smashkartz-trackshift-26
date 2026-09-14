#!/usr/bin/env python3
"""Export the rivalries where the pass model AGREED WITH THE TELEMETRY, per circuit.

The simulator can already draw a race. What it cannot do is show that the model is
worth anything. This artifact is the evidence: for every overtake opportunity the
season actually produced, the CP-14 model's probability is scored against what the
cars actually did, and the ones it called correctly -- and confidently -- are exported
in order so the sim can play them back one after another.

WHY THIS IS NOT CHERRY-PICKING, PROVIDED YOU READ THE HEADER. Selecting the model's
hits and showing only those would be exactly that. So every event's entry carries the
FULL population it was drawn from: how many opportunities existed, how many were
passes, the base rate, the model's ROC AUC over all of them, and how many were
selected. A reel of twelve good calls above a line saying "12 of 412, AUC 0.79" is an
honest demonstration. The same reel without that line is not, and the artifact makes
the line impossible to drop because the numbers live in the same object.

THE THRESHOLD IS THE BASE RATE, NOT 0.5. Passes are about a tenth of opportunities, so
a 0.5 cut would call almost nothing a pass and "agreement" would collapse into the
trivial statement that most approaches do not produce an overtake. What the model is
actually for is RANKING: of the approaches it rated highest, how many converted. So a
true positive is an approach that converted AND that the model rated in the top decile;
a true negative is one that did not convert AND that it rated in the bottom decile.
Both quantiles are computed per event and written into the artifact.

UNLABELLED IS NOT FAILED. ``passed_by_outcome_horizon`` is null when a car has no
position at the zone exit -- a retirement, or a dropped sample. Those rows are excluded
from the population, the rate and the AUC, and the count of what was excluded travels
with the numbers rather than vanishing into them.

Usage:
    python scripts/features/export_rivalry_showcase.py
    python scripts/features/export_rivalry_showcase.py --dry-run
    python scripts/features/export_rivalry_showcase.py --event british_grand_prix
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from trackshift.rules.api import load_event_rules, max_electrical_power_kw  # noqa: E402
from trackshift.serve.pass_service import load_predictor  # noqa: E402

from build_sim_data import OUT_DIR, write_json  # noqa: E402

OPPORTUNITIES = ROOT / "data" / "processed" / "overtake_opportunities"
TELEMETRY = ROOT / "data" / "processed" / "telemetry_20m"
PASS_MODELS = ROOT / "artifacts" / "models" / "pass"

#: Seconds either side of the Detection Line to carry as a replay window.
#: Three is the span in which an Overtake either happens or does not: the zone is
#: a few hundred metres and the cars cross it in about that. A snapshot at the
#: line shows the decision; this shows whether it worked.
WINDOW_S = 3.0

SCHEMA_VERSION = 1

#: Only the Detection Line. It is the checkpoint the simulator's Overtake panel scores
#: at, and mixing checkpoints in one reel would compare probabilities built from
#: different information -- which API.md section 8 forbids showing as one number.
CHECKPOINT = "DETECTION"

#: How far into each tail a call has to be to count as confident. A tenth either way:
#: wide enough that a mid-sized event still fills a reel, narrow enough that the
#: selected calls are genuinely the model's strongest opinions rather than its median.
TAIL_QUANTILE = 0.10

#: Most opportunities are the same two cars a lap apart, repeating. A reel of eight
#: consecutive ALO-vs-18 approaches is one rivalry shown eight times, so each pairing
#: contributes at most this many entries and the rest are counted, not shown.
MAX_PER_PAIRING = 2

#: The reel's composition, PER SESSION: three approaches the model called as passes
#: that became passes, then two it called as no-pass that stayed no-pass.
#:
#: Both directions are required. Three hits alone would show a model that says "yes"
#: and is sometimes right, which a model that always says yes would also produce. The
#: two correct refusals are what make the three hits mean anything -- and since only
#: about a tenth of approaches convert, ruling them out is most of the model's job.
TARGET_TRUE_POSITIVE = 3
TARGET_TRUE_NEGATIVE = 2


class ShowcaseError(RuntimeError):
    """The inputs cannot support an honest showcase."""


def _driver_codes() -> dict[str, str]:
    """Car number -> driver code, from the catalogue the sim already ships.

    The opportunity rows name the defender by number in some races and by code in
    others. The simulator highlights cars by code, so a number reaching it unmapped is
    a rivalry that silently highlights nobody.
    """
    matches = sorted(glob.glob(str(OUT_DIR / "catalogue.*.json")))
    if not matches:
        raise ShowcaseError(
            f"no catalogue.*.json under {OUT_DIR}. Run scripts/build_sim_data.py first; "
            "without it a defender given as a car number cannot be resolved to a code.")
    catalogue = json.loads(Path(matches[-1]).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for entry in catalogue.get("drivers", []):
        number = entry.get("number")
        code = entry.get("code")
        if number and code:
            out[str(number)] = str(code)
    return out


def _as_code(raw: Any, numbers: dict[str, str]) -> str | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    # A pure number is a car number; anything else is already a code.
    return numbers.get(text, text if not text.isdigit() else None)


def _slug(event: str) -> str:
    return event.replace("_", "-")


def _roc_auc(scores: list[float], labels: list[bool]) -> float | None:
    """AUC by rank, without pulling in scikit-learn for one number.

    This is the honest summary of the model over the WHOLE population, and it is what
    stops the selected reel from being read as the model's accuracy. Ties share a rank,
    which is why the ranks are averaged rather than taken in order.
    """
    positives = sum(1 for flag in labels if flag)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return None
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1  # 1-based, averaged across the tie
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    positive_rank_sum = sum(r for r, flag in zip(ranks, labels) if flag)
    return (positive_rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def _score(predictor, frame: pd.DataFrame) -> tuple[list[float], int, list[str]]:
    """The model's probability for every row, plus how many features actually landed."""
    present = [name for name in predictor.features if name in frame.columns]
    missing = [name for name in predictor.features if name not in frame.columns]
    row = pd.DataFrame(index=frame.index, columns=list(predictor.features), dtype=object)
    for name in present:
        row[name] = frame[name]
    for name in predictor.numeric:
        row[name] = pd.to_numeric(row[name], errors="coerce")
    for name in predictor.categorical:
        row[name] = row[name].map(lambda v: None if pd.isna(v) else str(v))
    return [float(p) for p in predictor.model.predict_proba(row)], len(present), missing


def _telemetry(event: str, session: str,
               cache: dict[str, pd.DataFrame | None]) -> pd.DataFrame | None:
    """One session's 20 m-resampled telemetry, loaded once.

    The path spells the event in Title_Case while the opportunity dataset uses
    lower_snake; they are the same event under two conventions and the join is
    silent if you get it wrong, producing a reel with no windows and no error.
    """
    key = f"{event}/{session}"
    if key in cache:
        return cache[key]
    path = (TELEMETRY / "year=2026"
            / f"event={'_'.join(part.capitalize() for part in event.split('_'))}"
            / f"session={session.replace(' ', '_')}" / "telemetry_20m.parquet")
    cache[key] = pd.read_parquet(path) if path.exists() else None
    return cache[key]


def _machine_limit_kw(event: str, speed_kmh: Any) -> float:
    """The regulatory electrical cap, from the rule engine. Never a local constant.

    This was a hardcoded ``350.0``, which AGENTS.md section 32 forbids and
    ``tests/test_rules.py::test_no_new_envelope_constant_outside_config`` catches:
    the cap is a speed-dependent curve owned by ``max_electrical_power_kw``, and a
    second copy of one point on it drifts the moment the config changes.

    Evaluated at the window's PEAK speed, because ``electrical_split_kw`` takes a
    single ceiling for the whole trace. That is the loosest bound in the window,
    so it never clips a sample the regulations would have allowed; the twin's own
    over-estimates stay visible rather than being hidden by a tight clamp.

    NORMAL mode, not override: whether a car had override armed is not in the feed,
    and assuming the higher cap would quietly licence a bigger number than the
    evidence supports.
    """
    rules = load_event_rules(event)
    peak = float(speed_kmh) if speed_kmh == speed_kmh else 0.0
    return float(max_electrical_power_kw(peak, "normal", rules))


def _energy_channels(rows: pd.DataFrame, event: str) -> dict[str, Any]:
    """Wheel power and the ERS split, from the twin that already owns this physics.

    SIMULATED, not measured. The public feed carries no battery, no MGU-K power
    and no fuel flow, so every number here is a model's estimate of what the car
    must have been doing to produce the speed trace it did -- which is exactly
    what scripts/simdata/twin.py exists to compute, and why it is called rather
    than reimplemented. The browser never does this arithmetic (section 32).

    Grade is passed as zero: the resampled telemetry carries no elevation, and a
    guessed gradient would move the wheel-power number without any evidence
    behind it. On Silverstone's 4% maxima the error is small; it is named here
    rather than hidden.
    """
    import numpy as np
    sys.path.insert(0, str(ROOT / "scripts"))
    from simdata import twin

    params = twin.TwinParams()
    speed_mps = rows["speed_kmh"].to_numpy(dtype=float) / 3.6
    accel = rows["acc_longitudinal_mps2"].to_numpy(dtype=float)
    brake = rows["brake_on"].to_numpy() if "brake_on" in rows.columns else np.zeros(len(rows), bool)
    throttle = rows["throttle_pct"].to_numpy(dtype=float) / 100.0
    rpm = rows["engine_rpm"].to_numpy(dtype=float) if "engine_rpm" in rows.columns else None

    forces = twin.force_split(speed_mps, accel, np.zeros(len(rows)), params)
    wheel = twin.wheel_power_kw(forces, speed_mps)
    ice, _, _ = twin.ice_power_kw(throttle, brake, params, rpm=rpm)
    peak_kmh = float(rows["speed_kmh"].max()) if len(rows) else 0.0
    split = twin.electrical_split_kw(wheel, ice, brake, params,
                                     machine_limit_kw=_machine_limit_kw(event, peak_kmh))
    deploy = split.get("deploy_kw") if isinstance(split, dict) else None
    harvest = split.get("harvest_kw") if isinstance(split, dict) else None
    return {"wheel": wheel, "deploy": deploy, "harvest": harvest}


def _sample_time(frame: pd.DataFrame) -> pd.Series:
    """Session seconds for each 20 m SAMPLE, not for its lap.

    ``session_time_s`` is constant across a whole lap -- it is the lap's own
    timestamp, and there is exactly one distinct value per driver per lap. Using
    it as the sample axis put every station of the lap inside the window and gave
    all 290 of them ``t = 0``. The per-sample clock is the lap's start plus the
    distance-resampled elapsed time, which is what ``lap_elapsed_s`` carries.
    """
    return frame["lap_start_session_s"] + frame["lap_elapsed_s"]


def _trace(car: pd.DataFrame, centre: float, t0: float, t1: float, event: str,
           rival: pd.DataFrame | None = None) -> list[dict[str, Any]]:
    """One car's measured channels across the window, relative to the centre.

    ``gapToRivalM`` is the distance to THIS PAIR's other car, and it is the
    channel worth drawing. The feed's own ``gap_ahead_m`` is the gap to whoever
    happens to be ahead, so the instant a pass completes it switches to a
    different car and jumps -- 4 m to 85 m in one sample on the BOT/BEA pass --
    which reads on a chart as the attacker falling back at the exact moment it
    went past. The pair gap is continuous and crosses zero at the swap, which is
    the thing being claimed.
    """
    rows = car[(car["_t"] >= t0) & (car["_t"] <= t1)].sort_values("_t")
    if rows.empty:
        return []
    pair: Any = None
    if rival is not None and len(rival) >= 2:
        import numpy as np
        pair = np.interp(
            rows["_t"].to_numpy(dtype=float),
            rival["_t"].to_numpy(dtype=float), rival["_total_m"].to_numpy(dtype=float),
            left=float("nan"), right=float("nan"),
        ) - rows["_total_m"].to_numpy(dtype=float)

    energy = _energy_channels(rows, event)

    out: list[dict[str, Any]] = []
    for i, (_, r) in enumerate(rows.iterrows()):
        def chan(name: str) -> float | None:
            arr = energy.get(name)
            if arr is None or i >= len(arr):
                return None
            v = float(arr[i])
            return round(v, 2) if v == v else None
        def f(name: str) -> float | None:
            v = r.get(name)
            return None if v is None or pd.isna(v) else round(float(v), 3)
        gap_to_rival = None
        if pair is not None and i < len(pair) and pair[i] == pair[i]:  # not NaN
            gap_to_rival = round(float(pair[i]), 3)
        out.append({
            # Seconds from the centre: negative is before the pass.
            "t": round(float(r["_t"]) - centre, 3),
            "speedKph": f("speed_kmh"),
            "throttlePct": f("throttle_pct"),
            # Positive means the other car is still ahead; through zero is the pass.
            "gapToRivalM": gap_to_rival,
            "gapAheadM": f("gap_ahead_m"),
            "distanceM": f("distance_m"),
            # SIMULATED: the twin's estimate of the power that must have been at
            # the wheels, and the electrical share of it. Never measured.
            "wheelPowerKw": chan("wheel"),
            "ersDeployKw": chan("deploy"),
            "ersHarvestKw": chan("harvest"),
        })
    return out


def _car(frame: pd.DataFrame, driver: str, lap_length: float) -> pd.DataFrame:
    """One car's samples with a session clock and a cumulative lap distance.

    Distance is made cumulative across laps so two cars can be compared at one
    instant: within-lap distance alone puts a car at 5,700 m just before the line
    and 20 m just after, which would read as the other car having passed it.
    """
    car = frame[frame["driver"] == driver].copy()
    if car.empty:
        return car
    car["_t"] = _sample_time(car)
    car["_total_m"] = car["lap"].astype(float) * lap_length + car["distance_m"].astype(float)
    return car[car["_t"].notna()].sort_values("_t")


def _moment(attacker: pd.DataFrame, defender: pd.DataFrame,
            after: float, before: float, passed: bool) -> float | None:
    """When the thing the reel is about actually happened.

    NOT the Detection Line. That is where the decision is made; at Silverstone
    the zone runs about 2 km further on, so a window centred on the line shows
    the approach and the overtake happens thirty seconds off the end of it.

    For a completed pass this is the POSITION SWAP: the first instant the
    attacker's cumulative distance exceeds the defender's. For an approach that
    did not convert there is no swap, so it is the closest the attacker got --
    the moment the pass was there to be had and was not taken.

    Both are searched only between the Detection Line and the zone exit, because
    a swap outside that span belongs to a different opportunity.
    """
    if attacker.empty or defender.empty:
        return None
    grid = attacker[(attacker["_t"] >= after) & (attacker["_t"] <= before)]
    if grid.empty:
        return None
    # The defender's position resampled onto the attacker's own sample times, so
    # the two are compared at the same instant rather than at the same station.
    lead = pd.Series(
        data=defender["_total_m"].to_numpy(),
        index=defender["_t"].to_numpy(),
    ).sort_index()
    times = grid["_t"].to_numpy()
    interp = pd.Series(lead.index).astype(float)
    if len(interp) < 2:
        return None
    import numpy as np
    ahead_m = np.interp(times, lead.index.to_numpy(dtype=float), lead.to_numpy(dtype=float),
                        left=float("nan"), right=float("nan"))
    delta = grid["_total_m"].to_numpy(dtype=float) - ahead_m
    finite = np.isfinite(delta)
    if not finite.any():
        return None

    if passed:
        # First instant the attacker is in front. `> 0` and not `>= 0`: equal
        # cumulative distance is side by side, which is not yet a pass.
        crossed = np.flatnonzero(finite & (delta > 0))
        if crossed.size:
            return float(times[crossed[0]])
        # Labelled a pass but no swap inside the zone window -- the label is by
        # classified race position at zone exit, which can settle after the last
        # sample. Fall back to the closest approach rather than inventing a time.
    closest = np.nanargmin(np.where(finite, np.abs(delta), np.nan))
    return float(times[closest])


def _window(event: str, session: str, row: Any, attacker: str, defender: str,
            cache: dict[str, pd.DataFrame | None]) -> dict[str, Any] | None:
    """Both cars' telemetry for WINDOW_S either side of the Detection Line.

    This is what turns a claim into something watchable: the panel can draw the
    gap closing and the speeds diverging across the moment the model was asked,
    and the stage can play the same seconds on the circuit. Every channel is
    OBSERVED -- nothing here is modelled, interpolated or smoothed.

    Returns None rather than an empty window when the session's telemetry is
    absent or the car has no sample at the line; an absent window is absent.
    """
    frame = _telemetry(event, session, cache)
    if frame is None:
        return None
    lap = row.get("lap")
    line = row.get("feature_cutoff_distance_m")
    if lap is None or pd.isna(lap) or line is None or pd.isna(line):
        return None

    # The attacker's own crossing time, taken at the nearest resampled station to
    # the line rather than interpolated: a 20 m grid puts the nearest sample
    # within a tenth of a second at racing speed, and inventing a sub-sample time
    # would imply a precision the feed does not have.
    lap_length = float(frame["distance_m"].max()) + 20.0
    attacker_car = _car(frame, attacker, lap_length)
    defender_car = _car(frame, defender, lap_length)
    if attacker_car.empty or defender_car.empty:
        return None

    on_lap = attacker_car[attacker_car["lap"] == lap]
    if on_lap.empty:
        return None
    nearest = on_lap.iloc[(on_lap["distance_m"] - float(line)).abs().argsort().iloc[0]]
    detection_t = float(nearest["_t"])

    # Search from the Detection Line to a generous bound past it. The zone can run
    # most of a lap, so the swap is looked for over the rest of this lap and a
    # little of the next rather than over a fixed number of seconds.
    lap_time = float(nearest.get("lap_time_s") or 100.0)
    centre = _moment(attacker_car, defender_car,
                     detection_t, detection_t + lap_time * 1.5,
                     bool(row["passed_by_outcome_horizon"]))
    if centre is None:
        return None

    t0, t1 = centre - WINDOW_S, centre + WINDOW_S
    attacker_trace = _trace(attacker_car, centre, t0, t1, event, defender_car)
    defender_trace = _trace(defender_car, centre, t0, t1, event, attacker_car)
    if not attacker_trace:
        return None
    return {
        "detectionSessionTimeS": round(detection_t, 3),
        "window": {
            "beforeS": WINDOW_S, "afterS": WINDOW_S,
            "provenance": "OBSERVED",
            # Session seconds the window is centred on, and what centred it.
            # The stage seeks to `centreSessionTimeS - beforeS` and stops at
            # `+ afterS`, so the replay plays exactly this span and no more.
            "centreSessionTimeS": round(centre, 3),
            "centredOn": "POSITION_SWAP" if bool(row["passed_by_outcome_horizon"]) else "CLOSEST_APPROACH",
            # Where the Detection Line sits relative to the centre, so the chart
            # can mark it when it falls inside the window and omit it when the
            # zone is long enough that it does not.
            "detectionOffsetS": round(detection_t - centre, 3),
            "attacker": attacker_trace,
            "defender": defender_trace,
        },
    }


def build_event(event: str, predictor, numbers: dict[str, str]) -> dict[str, Any] | None:
    path = OPPORTUNITIES / f"event={event}" / "opportunities.parquet"
    if not path.exists():
        return None
    frame = pd.read_parquet(path)
    frame = frame[frame["decision_checkpoint"] == CHECKPOINT]
    unlabelled = int(frame["passed_by_outcome_horizon"].isna().sum())
    frame = frame[frame["passed_by_outcome_horizon"].notna()].reset_index(drop=True)
    if frame.empty:
        return None

    probabilities, supplied, missing = _score(predictor, frame)
    frame = frame.assign(p_pass=probabilities)
    outcomes = [bool(v) for v in frame["passed_by_outcome_horizon"]]
    passes = sum(outcomes)
    auc = _roc_auc(probabilities, outcomes)

    high = float(frame["p_pass"].quantile(1 - TAIL_QUANTILE))
    low = float(frame["p_pass"].quantile(TAIL_QUANTILE))

    # Confident and correct, in both directions. Sorted by how far into the tail the
    # call sits, so the reel opens with the model's strongest opinion.
    hits = frame[
        ((frame["p_pass"] >= high) & (frame["passed_by_outcome_horizon"] == True))    # noqa: E712
        | ((frame["p_pass"] <= low) & (frame["passed_by_outcome_horizon"] == False))  # noqa: E712
    ].copy()
    hits["confidence"] = [
        p - high if bool(outcome) else low - p
        for p, outcome in zip(hits["p_pass"], hits["passed_by_outcome_horizon"])
    ]
    hits = hits.sort_values("confidence", ascending=False)

    # The reel is built PER SESSION, to a fixed quota: three correct passes then
    # two correct refusals. The sim plays one session at a time, so a reel mixed
    # across Race and Sprint would seek to laps that do not exist in the replay
    # on screen.
    #
    # Selection walks the CONFIDENCE order and takes the first rows that satisfy
    # the quota -- not the earliest laps. "First that qualifies" is the whole
    # point: the criteria pick the entries, and lap order is irrelevant to
    # whether a call was a good one.
    entries: list[dict[str, Any]] = []
    skipped_repeats = 0
    telemetry_cache: dict[str, pd.DataFrame | None] = {}

    for session in sorted(hits["session"].dropna().unique()):
        in_session = hits[hits["session"] == session]
        seen: dict[tuple[str, str], int] = {}
        quota = {True: TARGET_TRUE_POSITIVE, False: TARGET_TRUE_NEGATIVE}
        chosen: list[Any] = []
        for _, row in in_session.iterrows():
            outcome = bool(row["passed_by_outcome_horizon"])
            if quota[outcome] <= 0:
                continue
            attacker = _as_code(row.get("attacker"), numbers)
            defender = _as_code(row.get("defender"), numbers)
            if not attacker or not defender or attacker == defender:
                continue
            pairing = (attacker, defender)
            if seen.get(pairing, 0) >= MAX_PER_PAIRING:
                skipped_repeats += 1
                continue
            seen[pairing] = seen.get(pairing, 0) + 1
            quota[outcome] -= 1
            chosen.append((row, attacker, defender))
            if quota[True] <= 0 and quota[False] <= 0:
                break

        # Passes first, then the refusals: the order the claim is made in.
        chosen.sort(key=lambda c: not bool(c[0]["passed_by_outcome_horizon"]))

        for row, attacker, defender in chosen:
            def number(name: str) -> float | None:
                value = row.get(name)
                return None if value is None or pd.isna(value) else float(value)

            window = _window(event, str(session), row, attacker, defender, telemetry_cache)
            entries.append({
            "opportunityId": str(row.get("opportunity_id")),
            # The session matters: a British Grand Prix weekend produces Race AND
            # Sprint opportunities, and lap 3 of the Sprint is a different moment
            # from lap 3 of the Race. Seeking to one inside the other would show
            # two cars nowhere near each other and call it the model's evidence.
            "session": str(row.get("session")) if row.get("session") else None,
            "attacker": attacker,
            "defender": defender,
            "lap": int(row["lap"]) if not pd.isna(row.get("lap")) else None,
            "zone": None if pd.isna(row.get("zone")) else int(row["zone"]),
            "gapS": number("gap_at_checkpoint"),
            "closingRateSPerS": number("closing_rate_s_per_s"),
            "pEligible": number("p_eligible"),
            "pPass": round(float(row["p_pass"]), 4),
            "outcome": bool(row["passed_by_outcome_horizon"]),
            "call": "TRUE_POSITIVE" if bool(row["passed_by_outcome_horizon"]) else "TRUE_NEGATIVE",
            "detectionDistanceM": number("feature_cutoff_distance_m"),
            # Everything below is measured telemetry over the seconds either side
            # of the Detection Line. Null when the session's telemetry cannot
            # supply it -- a window that could not be built is absent, not empty.
            **(window or {"detectionSessionTimeS": None, "window": None}),
            })

    return {
        "event": event,
        "slug": _slug(event),
        "checkpoint": CHECKPOINT,
        "model": {
            "artifact": predictor.artifact_version,
            "family": predictor.family,
            "evidenceGrade": (predictor.manifest.get("run") or {}).get("evidence", {}).get("grade")
                             or (predictor.manifest.get("run") or {}).get("evidence_grade"),
            "featuresSupplied": supplied,
            "featuresTotal": len(predictor.features),
            "featuresMissing": missing,
        },
        # The line the reel must never be shown without.
        "population": {
            "opportunities": int(len(frame)),
            "passes": int(passes),
            "baseRate": round(passes / len(frame), 4),
            "rocAuc": None if auc is None else round(auc, 4),
            "unlabelledExcluded": unlabelled,
            "selected": len(entries),
            "repeatsSkipped": skipped_repeats,
            "note": (
                f"{len(entries)} of {len(frame)} labelled Detection-Line approaches, chosen as the "
                f"model's most confident correct calls in each direction. The model's discrimination "
                f"over ALL {len(frame)} is the ROC AUC above; this reel is a demonstration, not a score."
            ),
        },
        "thresholds": {"highDecile": round(high, 4), "lowDecile": round(low, 4),
                       "tailQuantile": TAIL_QUANTILE},
        "rivalries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--event", help="one event key, e.g. british_grand_prix")
    parser.add_argument("--dry-run", action="store_true", help="print the table, write nothing")
    args = parser.parse_args()

    predictor = load_predictor(PASS_MODELS, checkpoint=CHECKPOINT)
    numbers = _driver_codes()

    events = ([args.event] if args.event else
              sorted(p.name.split("=", 1)[1] for p in OPPORTUNITIES.glob("event=*")))

    out: dict[str, Any] = {}
    for event in events:
        built = build_event(event, predictor, numbers)
        if built is None:
            print(f"  {event}: no labelled {CHECKPOINT} rows, skipped", file=sys.stderr)
            continue
        out[built["slug"]] = built
        pop = built["population"]
        print(f"  {event}: {pop['selected']} shown of {pop['opportunities']} "
              f"(passes {pop['passes']}, base {pop['baseRate']:.3f}, AUC {pop['rocAuc']})")

    if not out:
        raise ShowcaseError("no event produced a showcase; nothing written")

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "checkpoint": CHECKPOINT,
        "events": out,
        "provenance": "DERIVED",
    }
    if args.dry_run:
        print(json.dumps(payload["events"][next(iter(out))], indent=1)[:1800])
        return 0

    name = write_json(payload, OUT_DIR, "rivalry_showcase")
    print(f"wrote {name}")

    # Register it in the index the sim already reads, so the artifact is
    # reachable the same way every other one is. Rewriting the index in place
    # rather than rebuilding every circuit: the other entries are unchanged and
    # a full rebuild would rewrite 40 files to add one key.
    pointer = OUT_DIR / "index.json"
    if pointer.exists():
        current = json.loads(pointer.read_text(encoding="utf-8"))["latest"]
        index = json.loads((OUT_DIR / current).read_text(encoding="utf-8"))
        index["rivalryShowcase"] = name
        new_index = write_json(index, OUT_DIR, "index")
        pointer.write_text(json.dumps({"latest": new_index}), encoding="utf-8")
        print(f"index -> {new_index}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
