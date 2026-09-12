"""Arc-length geometry on a closed track ring.

The ring is parametrised by GEOMETRIC arc length, never by the telemetry `distance`
channel: the two disagree by -0.8..+0.4 % per circuit (measured), and `distance` is an
integrated wheel speed that inherits wheelspin error on lap 1.

Frame: metres, raw telemetry orientation (right-handed, these circuits run clockwise so
the signed area is negative). Lateral offset is signed with + to the LEFT of travel.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.spatial import cKDTree


def arclength(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    step = np.hypot(np.diff(x), np.diff(y))
    return np.concatenate(([0.0], np.cumsum(step)))


def resample_by_arclength(x, y, z, ds: float = 1.0):
    """Uniform ds resampling along the polyline's own arc length."""
    m = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
    x, y, z = x[m], y[m], z[m]
    s = arclength(x, y)
    # drop zero-length steps, which break interpolation
    keep = np.concatenate(([True], np.diff(s) > 1e-9))
    x, y, z, s = x[keep], y[keep], z[keep], s[keep]
    grid = np.arange(0.0, s[-1], ds)
    return (np.interp(grid, s, x), np.interp(grid, s, y),
            np.interp(grid, s, z), grid)


def close_ring(x, y, z, ds: float = 1.0):
    """Append the wrap segment so the ring is continuous, then re-resample.

    The closure gap is purely longitudinal at the wrap (measured: 1.4 m Silverstone,
    0.4 Spa, 7.0 Zandvoort, 9.5 Monaco) so simply joining last->first adds no kink.
    """
    xs = np.concatenate((x, x[:1]))
    ys = np.concatenate((y, y[:1]))
    zs = np.concatenate((z, z[:1]))
    return resample_by_arclength(xs, ys, zs, ds)


def smooth_circular(v: np.ndarray, window_m: float, ds: float = 1.0) -> np.ndarray:
    """Box filter that wraps around the ring. window_m is the full width."""
    w = max(1, int(round(window_m / ds)))
    if w % 2 == 0:
        w += 1
    if w <= 1:
        return v.copy()
    pad = w // 2
    ext = np.concatenate((v[-pad:], v, v[:pad]))
    kern = np.ones(w) / w
    return np.convolve(ext, kern, mode="valid")


class Ring:
    """A closed centreline sampled every `ds` metres, with projection."""

    def __init__(self, x, y, z, ds: float = 1.0):
        self.x, self.y, self.z = x, y, z
        self.n = len(x)
        # MEASURED, never asserted. This used to read `self.length = self.n * ds`, which
        # DECLARES the ring to be one metre per vertex. It is not: build_ring smooths the
        # ring AFTER the arc-length resample, so the vertex count is fixed against the
        # pre-smoothing curve while the geometry actually drawn is the shorter smoothed
        # one. Measured declared/true: 1.0003-1.0024 on eleven circuits, 1.0050 at Monaco
        # and 1.0637 at Hungary -- and Hungary's is not a scale you can divide out, its
        # local index-to-arclength ratio runs 0.804..1.000 with 274.5 m of cumulative
        # drift. Deriving `ds` FROM the measured length instead restores the invariant
        # `n * ds == length` by construction, which is exactly what the frontend assumes
        # when it computes `ds = lengthMetres / n` (manifest.ts).
        self.ds_nominal = ds
        seg = np.hypot(np.diff(x), np.diff(y))
        wrap = math.hypot(float(x[0] - x[-1]), float(y[0] - y[-1]))
        self.length = float(np.nansum(seg) + wrap)
        self.ds = self.length / self.n if self.n else ds
        self.s = np.arange(self.n, dtype=np.float64) * self.ds
        self._tree = cKDTree(np.column_stack((x, y)))
        self._tangent()

    def _tangent(self):
        dx = np.gradient(self.x)
        dy = np.gradient(self.y)
        # wrap-correct the endpoints, which np.gradient gets wrong on a ring
        dx[0] = (self.x[1] - self.x[-1]) / 2.0
        dy[0] = (self.y[1] - self.y[-1]) / 2.0
        dx[-1] = (self.x[0] - self.x[-2]) / 2.0
        dy[-1] = (self.y[0] - self.y[-2]) / 2.0
        mag = np.hypot(dx, dy)
        mag[mag == 0] = 1.0
        self.tx, self.ty = dx / mag, dy / mag
        self.heading = np.arctan2(self.ty, self.tx)
        # left normal
        self.nx, self.ny = -self.ty, self.tx

    def project(self, px, py):
        """Nearest-point projection. Returns (station_m, lateral_m).

        Refines the nearest ring vertex onto its two adjacent segments, so accuracy is
        far better than the ds spacing.
        """
        px = np.asarray(px, dtype=np.float64)
        py = np.asarray(py, dtype=np.float64)
        ok = np.isfinite(px) & np.isfinite(py)
        st = np.full(px.shape, np.nan)
        lat = np.full(px.shape, np.nan)
        if not ok.any():
            return st, lat
        _, idx = self._tree.query(np.column_stack((px[ok], py[ok])))
        qx, qy = px[ok], py[ok]
        best_s = np.full(idx.shape, np.nan)
        best_l = np.full(idx.shape, np.nan)
        best_d = np.full(idx.shape, np.inf)
        for off in (-1, 0):
            i0 = (idx + off) % self.n
            i1 = (i0 + 1) % self.n
            ax, ay = self.x[i0], self.y[i0]
            bx, by = self.x[i1], self.y[i1]
            ex, ey = bx - ax, by - ay
            seg2 = ex * ex + ey * ey
            seg2[seg2 == 0] = 1e-12
            t = ((qx - ax) * ex + (qy - ay) * ey) / seg2
            t = np.clip(t, 0.0, 1.0)
            cx, cy = ax + t * ex, ay + t * ey
            d = np.hypot(qx - cx, qy - cy)
            # signed lateral via the cross product of the segment and the offset
            cross = ex * (qy - ay) - ey * (qx - ax)
            sign = np.where(cross >= 0, 1.0, -1.0)
            s_here = (i0.astype(np.float64) + t) * self.ds
            better = d < best_d
            best_d = np.where(better, d, best_d)
            best_s = np.where(better, s_here, best_s)
            best_l = np.where(better, sign * d, best_l)
        st[ok] = best_s % self.length
        lat[ok] = best_l
        return st, lat

    def project_path(self, px, py, max_step_m: float | None = None):
        """Projection for a TIME-ORDERED trace, with continuity.

        `project` is a stateless nearest-point query, which is wrong on a ring that
        passes close to itself. Suzuka is the only self-crossing circuit in the 2026
        set: 529 vertex pairs lie within 12 m of each other while being more than 150 m
        apart in station, closest approach 0.37 m. A car on one branch of the crossover
        therefore snaps to the other branch whenever it drifts a few centimetres --
        measured, ALB lap 51 samples 291-293 read 2516.3 -> 4881.5 -> 2525.7 m, a 2365 m
        round trip in two samples, which is exactly the branch separation.

        Here each sample keeps the nearest candidate that is REACHABLE from the previous
        accepted station. Continuity is dropped (and re-seeded from the globally nearest
        point) when no candidate is reachable, so a genuine gap in the feed cannot lock
        the projection onto a wrong branch for the rest of the lap.

        Returns (station_m, lateral_m), same shape and units as `project`.
        """
        px = np.asarray(px, dtype=np.float64)
        py = np.asarray(py, dtype=np.float64)
        st = np.full(px.shape, np.nan)
        lat = np.full(px.shape, np.nan)
        ok = np.isfinite(px) & np.isfinite(py)
        if not ok.any() or self.n < 2:
            return st, lat
        if max_step_m is None:
            # 250 m is ~10 samples of slack at 350 km/h with the feed's ~0.25 s spacing,
            # so it never rejects honest motion, while the Suzuka alias is 2365 m.
            max_step_m = min(250.0, 0.25 * self.length)

        qx, qy = px[ok], py[ok]
        k = int(min(8, self.n))
        _, idx = self._tree.query(np.column_stack((qx, qy)), k=k)
        idx = np.atleast_2d(idx)
        if idx.shape[0] != qx.shape[0]:
            idx = idx.T

        # Evaluate every candidate vertex's two adjacent segments, vectorised.
        cand_s, cand_l, cand_d = [], [], []
        for col in range(idx.shape[1]):
            base = idx[:, col]
            for off in (-1, 0):
                i0 = (base + off) % self.n
                i1 = (i0 + 1) % self.n
                ax, ay = self.x[i0], self.y[i0]
                ex, ey = self.x[i1] - ax, self.y[i1] - ay
                seg2 = ex * ex + ey * ey
                seg2 = np.where(seg2 == 0, 1e-12, seg2)
                t = np.clip(((qx - ax) * ex + (qy - ay) * ey) / seg2, 0.0, 1.0)
                cx, cy = ax + t * ex, ay + t * ey
                d = np.hypot(qx - cx, qy - cy)
                cross = ex * (qy - ay) - ey * (qx - ax)
                cand_s.append(((i0.astype(np.float64) + t) * self.ds) % self.length)
                cand_l.append(np.where(cross >= 0, 1.0, -1.0) * d)
                cand_d.append(d)
        cand_s = np.column_stack(cand_s)
        cand_l = np.column_stack(cand_l)
        cand_d = np.column_stack(cand_d)

        half = self.length / 2.0
        out_s = np.empty(qx.shape[0])
        out_l = np.empty(qx.shape[0])
        prev = None
        for i in range(qx.shape[0]):
            d_row = cand_d[i]
            if prev is None:
                j = int(np.argmin(d_row))
            else:
                gap = np.abs((cand_s[i] - prev + half) % self.length - half)
                reach = gap <= max_step_m
                j = int(np.argmin(np.where(reach, d_row, np.inf))) if reach.any()                     else int(np.argmin(d_row))
            out_s[i] = cand_s[i, j]
            out_l[i] = cand_l[i, j]
            prev = out_s[i]

        st[ok] = out_s
        lat[ok] = out_l
        return st, lat

    def point_at(self, station):
        """Ring point (x, y, z) at an arbitrary station, linearly interpolated."""
        s = np.asarray(station, dtype=np.float64) % self.length
        i0 = np.floor(s / self.ds).astype(int) % self.n
        i1 = (i0 + 1) % self.n
        f = (s / self.ds) - np.floor(s / self.ds)
        return (self.x[i0] + f * (self.x[i1] - self.x[i0]),
                self.y[i0] + f * (self.y[i1] - self.y[i0]),
                self.z[i0] + f * (self.z[i1] - self.z[i0]))

    def rotated(self, station0: float) -> "Ring":
        """Re-parametrise so `station0` becomes station 0."""
        shift = int(round(station0 / self.ds)) % self.n
        return Ring(np.roll(self.x, -shift), np.roll(self.y, -shift),
                    np.roll(self.z, -shift), self.ds)

    def curvature(self, smooth_m: float = 15.0) -> np.ndarray:
        h = np.unwrap(self.heading)
        dh = np.gradient(h) / self.ds
        return smooth_circular(dh, smooth_m, self.ds)

    def signed_area(self) -> float:
        return 0.5 * float(np.sum(self.x * np.roll(self.y, -1) - np.roll(self.x, -1) * self.y))
