"""Fit the lap-time model (M1/M2 from the plan) from the tidy lap table.

Every OLS design matrix is built and solved by plain numpy least squares -- no new
dependency. Standard errors are heteroskedasticity-robust (White/HC1), which is a
simpler (and slightly more conservative in most of these designs) substitute for the
cluster-robust SEs used in the original audit; it is documented as such rather than
silently presented as identical.
"""
from __future__ import annotations

import numpy as np


def _ols_hc1(X: np.ndarray, y: np.ndarray):
    """OLS via lstsq, with HC1 (White, small-sample corrected) robust SEs."""
    n, k = X.shape
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    XtX_inv = np.linalg.pinv(X.T @ X)
    meat = (X * (resid ** 2)[:, None]).T @ X
    scale = n / max(1, n - k)
    cov = scale * XtX_inv @ meat @ XtX_inv
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    yhat = X @ beta
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return beta, se, resid, r2


def robust_trim(X, y, groups, lo_mad=-3.0, hi_mad=2.0, max_iter=1):
    """Two-pass asymmetric trim: traffic/incidents only inflate lap time, so the
    upper bound is tighter than the lower one."""
    keep = np.ones(len(y), dtype=bool)
    for _ in range(max_iter + 1):
        beta, se, resid, r2 = _ols_hc1(X[keep], y[keep])
        med = np.median(resid)
        mad = 1.4826 * np.median(np.abs(resid - med))
        if mad == 0:
            break
        full_resid = y - X @ beta
        new_keep = (full_resid - med > lo_mad * mad) & (full_resid - med < hi_mad * mad)
        if new_keep.sum() == keep.sum():
            keep = new_keep
            break
        keep = new_keep
    beta, se, resid, r2 = _ols_hc1(X[keep], y[keep])
    return beta, se, resid, r2, keep


def leaf(value, se=None, n=None, provenance="DERIVED", source=None, note=None):
    d = {"value": round(float(value), 6) if value is not None else None,
         "provenance": provenance}
    if se is not None:
        d["se"] = round(float(se), 6)
        d["ci95"] = [round(float(value - 1.96 * se), 6), round(float(value + 1.96 * se), 6)]
    if n is not None:
        d["n"] = int(n)
    if source:
        d["source"] = source
    if note:
        d["note"] = note
    return d


def fit_track_model(t: dict, mask: np.ndarray, track_mask: np.ndarray):
    """Fit M1 for one track's Race session: time ~ fuel*(lap-1) + compound intercepts
    + compound*(life-1) degradation + driver dummies. Returns fitted numbers plus the
    residuals (for the noise and dirty-air passes) aligned to the FULL boolean row set
    used, so callers can reattach gap/driver context."""
    rows = mask & track_mask
    idx = np.where(rows)[0]
    if idx.size < 30:
        return None

    lap = t["lap"][idx]
    life = t["life"][idx]
    comp = t["compound"][idx]
    drv = t["drv"][idx]
    y = t["time"][idx]

    compounds = sorted(set(comp))
    ref_compound = max(compounds, key=lambda c: (comp == c).sum())
    other_compounds = [c for c in compounds if c != ref_compound]
    drivers = sorted(set(drv))
    ref_driver = drivers[0]
    other_drivers = drivers[1:]

    cols = [np.ones(idx.size), (lap - 1)]
    names = ["intercept", "fuel"]
    for c in other_compounds:
        m = (comp == c).astype(np.float64)
        cols.append(m); names.append(f"k_{c}")
    for c in compounds:
        m = (comp == c).astype(np.float64)
        cols.append(m * (life - 1)); names.append(f"g_{c}")
    for d in other_drivers:
        cols.append((drv == d).astype(np.float64)); names.append(f"drv_{d}")
    X = np.column_stack(cols)

    beta, se, resid, r2, keep = robust_trim(X, y, drv)
    n_kept = int(keep.sum())
    result = {names[i]: {"beta": float(beta[i]), "se": float(se[i])} for i in range(len(names))}
    # full-sample residuals from the TRIMMED beta (not a second, differently-trimmed
    # fit): the noise and dirty-air models need the incidents the trim removed, so
    # they must see every row, evaluated against the same fitted line.
    full_resid = y - X @ beta
    return {
        "refCompound": ref_compound, "refDriver": ref_driver,
        "compounds": compounds, "n": n_kept, "r2": r2,
        "params": result,
        "residual_idx": idx[keep], "residual": resid,
        "residual_idx_full": idx, "residual_full": full_resid,
        "lap_full": lap, "drv_full": drv,
    }


def session_pace_trend(track_fits: dict) -> dict:
    """Empirical-Bayes shrinkage of the per-track fuel coefficient toward the pooled
    mean, using DerSimonian-Laird between-track variance."""
    tracks, betas, ses = [], [], []
    for track, fit in track_fits.items():
        if fit is None or "fuel" not in fit["params"]:
            continue
        tracks.append(track)
        betas.append(fit["params"]["fuel"]["beta"])
        ses.append(fit["params"]["fuel"]["se"])
    betas = np.array(betas); ses = np.array(ses)
    w = 1.0 / np.clip(ses ** 2, 1e-9, None)
    pooled = float(np.sum(w * betas) / np.sum(w))
    pooled_se = float(np.sqrt(1.0 / np.sum(w)))
    q = float(np.sum(w * (betas - pooled) ** 2))
    df = max(1, len(tracks) - 1)
    c = np.sum(w) - np.sum(w ** 2) / np.sum(w)
    tau2 = max(0.0, (q - df) / c) if c > 0 else 0.0

    per_track = {}
    for tr, b, s in zip(tracks, betas, ses):
        wshrink = tau2 / (tau2 + s ** 2) if (tau2 + s ** 2) > 0 else 0.0
        shrunk = wshrink * b + (1 - wshrink) * pooled
        per_track[tr] = leaf(shrunk, s, provenance="DERIVED",
                             note=f"empirical-Bayes shrunk (raw fit {b:.4f}, weight {wshrink:.2f})")
    return {
        "pooled": leaf(pooled, pooled_se, provenance="DERIVED",
                       note="fuel burn + track evolution; the two are collinear in lap "
                            "number and cannot be separated from lap data alone"),
        "perTrack": per_track,
        "betweenTrackTau": round(float(np.sqrt(tau2)), 6),
    }


def track_degradation_index(track_fits: dict) -> dict:
    """Mean degradation slope across compounds per track: measured to be a TRACK
    property, not a compound property (compound deviations are within noise)."""
    out = {}
    for track, fit in track_fits.items():
        if fit is None:
            continue
        slopes = [v["beta"] for k, v in fit["params"].items() if k.startswith("g_")]
        if slopes:
            out[track] = leaf(np.mean(slopes), np.std(slopes) if len(slopes) > 1 else None,
                              n=len(slopes),
                              note="mean of per-compound degradation slopes at this track")
    return out


def driver_team_offsets(t: dict, mask: np.ndarray, group_col: str):
    """M2: pooled global fit with (event|session) fixed effects, a per-key fuel term
    and compound structure, plus sum-to-zero driver/team dummies. Returns offsets in
    seconds, recentred on the lap-weighted mean.
    """
    idx = np.where(mask)[0]
    key = np.array([f"{e}|{s}" for e, s in zip(t["event"][idx], t["session"][idx])])
    keys = sorted(set(key))
    lap = t["lap"][idx]; life = t["life"][idx]; comp = t["compound"][idx]
    grp = t[group_col][idx]; y = t["time"][idx]

    cols, names = [], []
    for k in keys:
        m = (key == k).astype(np.float64)
        cols.append(m); names.append(f"base_{k}")
        cols.append(m * (lap - 1)); names.append(f"fuel_{k}")
    compounds = sorted(set(comp))
    for k in keys:
        for c in compounds:
            m = ((key == k) & (comp == c)).astype(np.float64)
            cols.append(m * (life - 1)); names.append(f"deg_{k}_{c}")

    groups = sorted(set(grp))
    ref = groups[0]
    for g in groups:
        if g == ref:
            continue
        cols.append((grp == g).astype(np.float64) - (grp == ref).astype(np.float64))
        names.append(f"grp_{g}")
    X = np.column_stack(cols)

    beta, se, resid, r2, keep = robust_trim(X, y, grp)
    offsets = {}
    grp_betas = []
    for i, name in enumerate(names):
        if name.startswith("grp_"):
            g = name[len("grp_"):]
            offsets[g] = (beta[i], se[i])
            grp_betas.append(beta[i])
    ref_offset = -sum(b for b, _ in offsets.values())  # sum-to-zero
    offsets[ref] = (ref_offset, float(np.mean([s for _, s in offsets.values()])) if offsets else 0.0)

    n_by_group = {g: int(((grp == g) & keep).sum()) for g in groups}
    result = {g: leaf(b, s, n=n_by_group.get(g), provenance="DERIVED")
              for g, (b, s) in offsets.items()}
    return result, r2, int(keep.sum())


def noise_model(resid: np.ndarray, drv: np.ndarray = None):
    med = float(np.median(resid))
    mad = float(1.4826 * np.median(np.abs(resid - med)))
    sd = float(np.std(resid))
    incident = resid > (med + 1.0)  # a loose excess-time definition; see note
    p_incident = float(incident.mean())
    mean_excess = float((resid[incident] - med - 1.0).mean()) if incident.any() else 0.0
    out = {
        "coreSigma": leaf(mad, provenance="DERIVED",
                          note="robust (MAD-based) sigma of the normal core; the raw sd "
                               f"is {sd:.3f}, inflated by a long right tail (skew present)"),
        "incidentProbability": leaf(p_incident, provenance="DERIVED",
                                    note="fraction of clean laps with residual > median+1s"),
        "incidentMeanExcessSeconds": leaf(mean_excess, provenance="DERIVED"),
    }
    if drv is not None:
        per_driver = {}
        for d in sorted(set(drv)):
            r = resid[drv == d]
            if r.size >= 20:
                m = float(np.median(r))
                per_driver[d] = leaf(1.4826 * float(np.median(np.abs(r - m))),
                                     n=r.size, provenance="DERIVED")
        out["perDriverSigma"] = per_driver
    return out
