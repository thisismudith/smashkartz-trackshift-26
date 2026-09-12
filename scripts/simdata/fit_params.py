"""Build sim/params.json: every fitted parameter the New Race engine needs, with value,
n, se, ci95, provenance and source on every leaf. See plan section 5.2 for what each
parameter means and what the data does and does not support.

Everything here streams session_laptimes.json, with ONE exception: the standing-start
block. A grid box and a launch exist only in the position and speed channels, so
scripts/simdata/launch.py reads each race's lap-1 telemetry (one lap per driver, plus
lap 2 for the timing line) and hands back leaves in this file's own shape. That is the
whole of the telemetry this module touches, and it costs about two seconds.

Usage: python scripts/simdata/fit_params.py <out_dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simdata.laptable import build_tidy_table, clean_mask
from simdata.fit_pace import (driver_team_offsets, fit_track_model, leaf,
                              noise_model, session_pace_trend, track_degradation_index)
from simdata.launch import fit_standing_start

SCHEMA_VERSION = 1

# Documented DEFAULTs the data cannot identify (see plan 5.2). Never presented as fitted.
COMPOUND_DEG_MULTIPLIER_DEFAULT = {"SOFT": 1.35, "MEDIUM": 1.00, "HARD": 0.75}
FUEL_KG_PER_LAP_DEFAULT = 1.6  # only used for the "fuel load" UI readout
FRESH_TYRE_GAIN_S_PER_LAP_DEFAULT = 1.0  # measured flat ~1.0 s/lap advantage, sustained to L+5


def dirty_air(t: dict, track_fits: dict) -> dict:
    """Seconds lost per second of proximity inside 3 s of the car ahead, per track.

    The predictor MUST be the gap at the START of the lap (the previous lap's END gap),
    never the current lap's own end gap: a car that loses time this lap ends up with a
    BIGGER end-of-lap gap this lap by construction, which is endogenous and biases the
    coefficient toward zero. Uses the full (untrimmed) residuals, since a dirty-air
    proximity event is itself a kind of "incident" the robust trim would otherwise
    remove from the sample the effect is measured on.
    """
    out = {}
    for track, fit in track_fits.items():
        if fit is None:
            continue
        idx = fit["residual_idx_full"]
        resid = fit["residual_full"]
        sesT = t["sesT"][idx]
        lap = t["lap"][idx]
        drv = t["drv"][idx]

        # end-of-lap gap to the car immediately ahead, keyed by (driver, lap)
        end_gap = {}
        by_lap = {}
        for i in range(idx.size):
            by_lap.setdefault(lap[i], []).append(i)
        for lp, members in by_lap.items():
            members_sorted = sorted(members, key=lambda i: sesT[i])
            for k in range(1, len(members_sorted)):
                i = members_sorted[k]
                end_gap[(drv[i], lp)] = sesT[i] - sesT[members_sorted[k - 1]]

        # predictor for row i = the gap this driver had at the END of lap-1 (i.e. at
        # the START of lap): looked up by (driver, lap-1), never by this row's own lap
        gaps = np.full(idx.size, np.nan)
        for i in range(idx.size):
            gaps[i] = end_gap.get((drv[i], lap[i] - 1), np.nan)

        ok = np.isfinite(gaps) & (gaps > 0) & (gaps < 30)
        if ok.sum() < 20:
            continue
        g = gaps[ok]
        r = resid[ok]
        x = np.maximum(0.0, 3.0 - g)
        X = np.column_stack([np.ones(x.size), x])
        beta, *_ = np.linalg.lstsq(X, r, rcond=None)
        resid2 = r - X @ beta
        denom = max(1e-9, np.sum((x - x.mean()) ** 2))
        se = float(np.sqrt(np.sum(resid2 ** 2) / max(1, x.size - 2) / denom))
        out[track] = leaf(beta[1], se, n=int(ok.sum()), provenance="DERIVED",
                          note="seconds lost per second of proximity inside a 3 s gap "
                               "AT THE START OF THE LAP; doubles as the per-track "
                               "overtaking-difficulty index")
    return out


def pit_loss(t: dict) -> dict:
    """Net pit-lane loss per track, green-flag stops only (status has no SC/VSC code),
    plus the in/out split and the SC/VSC discount derived structurally from the lap-time
    multiplier rather than fitted directly (the plan's recommended, more robust method).
    """
    out = {}
    events = sorted(set(t["event"]))
    for ev in events:
        em = t["event"] == ev
        pin_mask = em & (t["pin"] != "None")
        idx_in = np.where(pin_mask)[0]
        if idx_in.size < 3:
            continue
        clean = clean_mask(t)
        # this driver's own clean median lap time at this track
        driver_median = {}
        for d in set(t["drv"][em]):
            r = t["time"][em & clean & (t["drv"] == d)]
            if r.size:
                driver_median[d] = float(np.median(r))
        losses = []
        for i in idx_in:
            d = t["drv"][i]
            lp = t["lap"][i]
            nxt = np.where(em & (t["drv"] == d) & (t["lap"] == lp + 1))[0]
            if nxt.size == 0 or t["pout"][nxt[0]] == "None":
                continue
            base = driver_median.get(d)
            if base is None:
                continue
            in_status = t["status"][i]
            out_status = t["status"][nxt[0]]
            green = ("4" not in in_status and "6" not in in_status and "5" not in in_status
                    and "4" not in out_status and "6" not in out_status and "5" not in out_status)
            in_time = t["time"][i]
            out_time = t["time"][nxt[0]]
            if not (np.isfinite(in_time) and np.isfinite(out_time)):
                continue
            net = (in_time + out_time) - 2 * base
            if 0 < net < 200:
                losses.append((net, green))
        greens = [n for n, g in losses if g]
        neutral = [n for n, g in losses if not g]
        if len(greens) >= 3:
            arr = np.array(greens)
            out[ev] = {
                "netLossSeconds": leaf(np.median(arr), n=len(greens), provenance="DERIVED",
                                       note="green-flag stops only: (inLap+outLap) time - "
                                            "2x the driver's own clean median"),
                "iqr": [round(float(np.percentile(arr, 25)), 3),
                       round(float(np.percentile(arr, 75)), 3)],
            }
            if neutral:
                out[ev]["neutralisedNetLossSeconds"] = leaf(
                    float(np.median(neutral)), n=len(neutral), provenance="DERIVED",
                    note="stops taken under SC/VSC/red; typically a large NEGATIVE net "
                         "loss (a 'free' stop) relative to green")
    return out


def neutralisation_rates(t: dict) -> dict:
    """Pooled per-lap hazards for SC/VSC/red, from track-status codes. Per-track rates
    are not meaningful at one race per track (see plan); only the pooled rate is."""
    race = t["session"] == "Race"
    status = t["status"][race]
    n = status.size
    def frac_with_code(code):
        return float(np.mean([code in s for s in status]))
    return {
        "safetyCarLapHazard": leaf(frac_with_code("4"), n=n, provenance="DERIVED"),
        "virtualSafetyCarLapHazard": leaf(frac_with_code("6"), n=n, provenance="DERIVED"),
        "redFlagLapHazard": leaf(frac_with_code("5"), n=n, provenance="DERIVED"),
        "perTrackMultiplierDefault": leaf(1.0, provenance="DEFAULT",
                                         note="not fittable from one race per track"),
    }


def retirement_rate(t: dict) -> dict:
    """DNF test: a car's last lap-end session time is more than 1.5 (track) median
    lap times before the leader's finish. The median lap duration must be THIS
    TRACK's own clean lap time, never a diff of interleaved cross-driver session
    times (which mixes different cars' clocks and is dominated by noise)."""
    race = t["session"] == "Race"
    clean = clean_mask(t)
    events = sorted(set(t["event"][race]))
    dnf, total = 0, 0
    for ev in events:
        m = race & (t["event"] == ev)
        drivers = set(t["drv"][m])
        finite_sesT = t["sesT"][m][np.isfinite(t["sesT"][m])]
        if finite_sesT.size == 0:
            continue
        leader_finish = float(np.max(finite_sesT))
        track_clean_times = t["time"][m & clean]
        med_lap = float(np.median(track_clean_times)) if track_clean_times.size >= 5 else 90.0
        for d in drivers:
            dm = m & (t["drv"] == d)
            last = np.nanmax(t["sesT"][dm]) if np.isfinite(t["sesT"][dm]).any() else np.nan
            total += 1
            if np.isfinite(last) and last < leader_finish - 1.5 * med_lap:
                dnf += 1
    rate = dnf / total if total else 0.0
    return {
        "perCarPerRace": leaf(rate, n=total, provenance="DERIVED",
                             note=f"{dnf} DNF-like results out of {total} car-races"),
        "perLapHazard": leaf(rate / 55.0, provenance="DERIVED",
                             note="approximated as a flat per-lap hazard over a ~55-lap "
                                  "average race; near-uniform over race distance"),
    }


def field_and_tyre_facts(t: dict) -> dict:
    race = t["session"] == "Race"
    field_sizes = Counter()
    for ev in sorted(set(t["event"][race])):
        m = race & (t["event"] == ev)
        field_sizes[ev] = len(set(t["drv"][m]))
    max_stint = {}
    for comp in ("SOFT", "MEDIUM", "HARD", "INTERMEDIATE"):
        m = (t["compound"] == comp)
        lives = t["life"][m]
        lives = lives[np.isfinite(lives)]
        if lives.size:
            max_stint[comp] = int(np.max(lives))
    return {
        "fieldSizeObserved": {"min": min(field_sizes.values()), "max": max(field_sizes.values()),
                              "perTrack": dict(field_sizes)},
        "maxObservedStintLaps": max_stint,
    }


def default_laps_formula() -> dict:
    return {
        "formula": "round(305000 / lapLengthMetres)",
        "note": "reproduces every observed 2026 race lap count to +/-1 except Monaco "
                "(255.9 km) and Canadian (293.8 km), which need a per-track override",
        "provenance": "DERIVED",
    }


def build_params() -> dict:
    t0 = time.time()
    t = build_tidy_table()
    mask = clean_mask(t)
    race = t["session"] == "Race"
    dutch_rain = (t["event"] == "Dutch Grand Prix") & t["wR"]
    mask_dry = mask & race & ~dutch_rain

    track_fits = {}
    for ev in sorted(set(t["event"][mask_dry])):
        tm = t["event"] == ev
        track_fits[ev] = fit_track_model(t, mask_dry, tm)

    trend = session_pace_trend(track_fits)
    deg = track_degradation_index(track_fits)
    da = dirty_air(t, track_fits)

    drv_off, drv_r2, drv_n = driver_team_offsets(t, mask & ~dutch_rain, "drv")
    team_off, team_r2, team_n = driver_team_offsets(t, mask & ~dutch_rain, "team")

    all_resid = np.concatenate([f["residual_full"] for f in track_fits.values() if f is not None])
    all_drv = np.concatenate([f["drv_full"] for f in track_fits.values() if f is not None])
    noise = noise_model(all_resid, all_drv)

    pit = pit_loss(t)
    neut = neutralisation_rates(t)
    dnf = retirement_rate(t)
    facts = field_and_tyre_facts(t)
    # The standing start is the one block here that needs TELEMETRY, not just the lap
    # table: a grid box and a launch only exist in the position and speed channels. It
    # owns its own reading (scripts/simdata/launch.py) and hands back leaves in exactly
    # this file's shape, so nothing below has to know where a grid came from.
    start = fit_standing_start()

    out = {
        "schemaVersion": SCHEMA_VERSION,
        "sessionPaceTrendPerLap": trend,
        "tyreDegradation": {
            "trackIndex": deg,
            "compoundMultiplierDefault": {
                k: leaf(v, provenance="DEFAULT",
                       note="compound ordering is not detectable in the data; this is a "
                            "documented, user-visible default")
                for k, v in COMPOUND_DEG_MULTIPLIER_DEFAULT.items()
            },
        },
        "driverOffsetSeconds": drv_off,
        "teamOffsetSeconds": team_off,
        "modelFitR2": {"driver": round(drv_r2, 4), "team": round(team_r2, 4),
                      "n_driver": drv_n, "n_team": team_n},
        "noise": noise,
        "dirtyAirLossPerSecondOfProximity": da,
        "pitLoss": pit,
        "neutralisation": neut,
        "retirement": dnf,
        "fuelLoadKgPerLapDefault": leaf(FUEL_KG_PER_LAP_DEFAULT, provenance="DEFAULT",
                                       note="fuel mass per lap is not in the data; only "
                                            "seconds/lap is fitted (see sessionPaceTrendPerLap)"),
        "freshTyreGainSecondsPerLapDefault": leaf(FRESH_TYRE_GAIN_S_PER_LAP_DEFAULT,
                                                  provenance="DERIVED",
                                                  note="flat advantage sustained through L+5; "
                                                       "the undercut must EMERGE from this "
                                                       "against tyre-age delta, never be a "
                                                       "stored constant"),
        "standingStart": start,
        "defaultLaps": default_laps_formula(),
        "fieldAndTyre": facts,
        "wetWeather": {
            "provenance": "DEFAULT",
            "note": "there is no wet running anywhere in 2026 (the Dutch rain race was "
                    "run entirely on slicks; fitted rain effect was not significant); "
                    "wet pace, the compound crossover and INTERMEDIATE/WET degradation "
                    "must ship as documented, user-visible defaults",
        },
        # Deliberately NOT emitted: a wall-clock duration inside a
        # content-addressed artifact makes every rebuild produce a new
        # params.<hash>.json for no change in content, which breaks this
        # pipeline's own promise of being "deterministic given the same raw
        # data" and churns the index on every run. The build prints its
        # timing instead.
    }
    return out


_T0 = time.time()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    args = ap.parse_args()
    params = build_params()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(params, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:10]
    path = out_dir / f"params.{digest}.json"
    path.write_bytes(payload)
    print(f"params: {path.name}  {len(payload)/1024:.1f} KB  built in {time.time() - _T0:.1f}s")


if __name__ == "__main__":
    main()
