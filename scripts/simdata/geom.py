"""Arc-length geometry on a closed track ring.

The ring is parametrised by GEOMETRIC arc length, never by the telemetry `distance`
channel: the two disagree by -0.8..+0.4 % per circuit (measured), and `distance` is an
integrated wheel speed that inherits wheelspin error on lap 1.

Frame: metres, raw telemetry orientation (right-handed, these circuits run clockwise so
the signed area is negative). Lateral offset is signed with + to the LEFT of travel.
"""
from __future__ import annotations

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
        self.ds = ds
        self.x, self.y, self.z = x, y, z
        self.n = len(x)
        self.length = self.n * ds
        self.s = np.arange(self.n, dtype=np.float64) * ds
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
