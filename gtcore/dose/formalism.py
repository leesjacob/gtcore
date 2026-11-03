"""Formalism check: is a TPS dose grid a TG-43U1 **2D line-source** calculation?

The clinical reference dose (an RTDOSE exported by the treatment-planning
system) is a TG-43 calculation too, so comparing it with gtcore measures
implementation equivalence, not physical accuracy.  Before any of the dose
numbers in the paper are quoted, three things about that reference have to be
pinned down from the RTDOSE itself: the *formalism* (1D point-source or 2D
line-source), the *anisotropy dataset* and the *seed axes* the TPS used.  This
module packages the test that does it -- the **angular-symmetry test** -- and
the diagnostics that chase the residual.

Angular-symmetry test (``angular_symmetry_test``)
-------------------------------------------------
For every voxel of the reference grid within ``r_max_mm`` of a source, the
per-source gtcore dose contributions are evaluated (``per_seed_doses``).  A
voxel is kept when its *dominant* source (the largest contributor) lies
``r_min_mm``..``r_max_mm`` away and contributes more than ``min_fraction`` of
the gtcore total, so the voxel's dose is essentially one seed's dose and its
angle ``theta`` to that seed's long axis is well defined.  The ratio
``reference / gtcore`` is then binned by ``theta`` (0-15, ..., 75-90 deg;
``ANGLE_BINS_DEG``).

* A 2D reference against a 2D gtcore gives a flat ratio (1.00 in every bin on
  synthetic data; ``tests/test_formalism.py``).
* A **1D** (point-source, ``phi_an(r)``) reference against gtcore-2D gives a
  falling ratio -- roughly 1.3 near the axis (where the 2D dose is depressed
  by ``F(r, theta) ~ 0.7-0.85`` and the 1D dose is not) to ``phi_an ~ 0.96``
  at the transverse plane.  ``point_source_1d_dose`` computes exactly that
  prediction so the measured curve can be read against it; it is a diagnostic
  inside this check only, never an engine mode.

Residual diagnostics (``scripts/formalism_check.py`` drives them)
-----------------------------------------------------------------
``fit_axis_to_reference`` recovers each source's long axis from the reference
dose's own local anisotropy (the 2-6 mm shell), so gtcore's PCA axes can be
compared with what the TPS evidently used; ``compare_axes`` gives the per-seed
angle between two axis sets (sign-blind, since F is symmetric), for PCA vs
RTDOSE-fitted axes and PCA vs RTPLAN ``ControlPointOrientation`` when the plan
file exists; ``perturb_axes`` simulates a known axis error; ``supersample`` in
``angular_symmetry_test`` evaluates gtcore averaged over each reference voxel
instead of at its centre.  Everything here is numpy/scipy only and works on
any :class:`~gtcore.volume.Volume` in cGy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
from scipy import ndimage
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree

from ..volume import Volume
from .engine import TG43Engine, _prepare_seeds

__all__ = [
    "ANGLE_BINS_DEG",
    "AngularRatioResult",
    "SourceFit",
    "angular_symmetry_test",
    "binned_ratio",
    "compare_axes",
    "fit_axis_to_reference",
    "fit_common_axis",
    "fit_source_to_reference",
    "fit_sources_to_reference",
    "match_sources_to_seeds",
    "per_seed_doses",
    "perturb_axes",
    "phi_an",
    "point_source_1d_dose",
    "prediction_rows",
    "reference_points_near_seeds",
    "rtdose_source_positions",
    "seed_frame",
]

#: Default angular bins [deg] for the ratio table: six 15-degree bins.
ANGLE_BINS_DEG: Tuple[float, ...] = (0.0, 15.0, 30.0, 45.0, 60.0, 75.0, 90.0)


# ------------------------------------------------------------------ geometry
def seed_frame(points_ras, center, axis):
    """Polar coordinates of points about one seed.

    Returns ``(r_mm, theta_deg, z_mm, y_mm)``: distance, angle to the long
    axis folded into [0, 90] deg (axis sign is irrelevant, as it is for
    ``F(r, theta)``), signed axial position and perpendicular distance.
    """
    pts = np.atleast_2d(np.asarray(points_ras, dtype=float))
    a = np.asarray(axis, dtype=float)
    a = a / np.linalg.norm(a)
    d = pts - np.asarray(center, dtype=float)
    r2 = np.einsum("ij,ij->i", d, d)
    z = d @ a
    y = np.sqrt(np.maximum(r2 - z * z, 0.0))
    theta = np.degrees(np.arctan2(y, np.abs(z)))
    return np.sqrt(r2), theta, z, y


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    if np.any(n == 0.0):
        raise ValueError("zero-length axis")
    return v / n


def _any_perpendicular(a):
    """A unit vector perpendicular to unit vector ``a``."""
    h = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    p = np.cross(a, h)
    return p / np.linalg.norm(p)


def _rotate_about(v, k, angle_rad):
    """Rodrigues rotation of ``v`` about unit axis ``k``."""
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    return v * c + np.cross(k, v) * s + k * np.dot(k, v) * (1.0 - c)


# ---------------------------------------------------------------- per-seed dose
def per_seed_doses(points_ras, seed_centers, seed_axes,
                   sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                   exact=True, elapsed_hours=None):
    """Per-seed total-to-decay dose [cGy] at RAS points: ``(N_seeds, M)``.

    Same formalism, data, folding and flooring as
    :func:`gtcore.dose.engine.dose_at_points` (whose result is the column
    sum of this matrix); kept apart so a voxel's dominant seed and its share
    of the total are available.  Memory is ``N x M`` doubles, so callers
    pre-select points (:func:`reference_points_near_seeds`).
    """
    centers, axes, sk = _prepare_seeds(seed_centers, seed_axes, sk_per_seed_u)
    pts = np.atleast_2d(np.asarray(points_ras, dtype=float)).reshape(-1, 3)
    eng = engine if engine is not None else TG43Engine()
    scale = eng.TAU_HOURS * float(eng.delivered_fraction(elapsed_hours))
    rate_fn = eng._rate_folded if exact else eng._rate_tabulated_folded
    out = np.zeros((centers.shape[0], pts.shape[0]), dtype=np.float64)
    for s in range(centers.shape[0]):
        if sk[s] == 0.0:
            continue
        r_mm, theta, _z, _y = seed_frame(pts, centers[s], axes[s])
        r_cm = np.maximum(r_mm / 10.0, eng.R_FLOOR_CM)
        out[s] = rate_fn(theta, r_cm) * (sk[s] * scale)
    return out


# ------------------------------------------------------------- 1D formalism
def phi_an(engine, r_cm, n_theta=1801):
    """1D anisotropy factor ``phi_an(r)`` of the engine's dataset.

    TG-43U1 Eq. (12): ``phi_an(r) = int_0^pi D(r, theta) sin(theta) dtheta /
    (2 D(r, pi/2))``, evaluated by trapezoidal quadrature of the engine's own
    2D rate (``G_L * g_L * F``) on ``n_theta`` nodes over 0..90 deg (the
    integrand is symmetric about 90 deg).  This is the quantity the consensus
    tables derive from the same Monte-Carlo ``F(r, theta)``; the printed
    consensus row can be substituted through ``point_source_1d_dose``'s
    ``phi_an_table`` argument when it is to hand.
    """
    eng = engine if engine is not None else TG43Engine()
    r = np.atleast_1d(np.asarray(r_cm, dtype=float))
    th = np.linspace(0.0, 90.0, int(n_theta))
    rate = eng._rate_folded(th[None, :], np.maximum(r[:, None], eng.R_FLOOR_CM))
    ref = eng._rate_folded(np.full_like(r, 90.0), np.maximum(r, eng.R_FLOOR_CM))
    num = np.trapezoid(rate * np.sin(np.radians(th))[None, :],
                       np.radians(th), axis=1)
    out = num / ref
    return out if np.ndim(r_cm) else float(out[0])


def point_source_1d_dose(points_ras, seed_centers,
                         sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                         elapsed_hours=None, phi_an_table=None, per_seed=False):
    """Total-to-decay dose [cGy] from the TG-43U1 **1D** formalism.

    ``D(r) = Lambda * [G_L(r, theta0) / G_L(r0, theta0)] * g_L(r) *
    phi_an(r)`` (the line-source-based 1D form TG-43U1 recommends, Eq. 11),
    with ``phi_an`` from :func:`phi_an` or from ``phi_an_table =
    (radii_cm, values)`` (linear interpolation, nearest beyond the ends).
    Seed axes play no part.  Diagnostic only: this is what a TPS running the
    1D formalism would have computed, used to read the angular-symmetry test.
    """
    centers = np.atleast_2d(np.asarray(seed_centers, dtype=float)).reshape(-1, 3)
    pts = np.atleast_2d(np.asarray(points_ras, dtype=float)).reshape(-1, 3)
    eng = engine if engine is not None else TG43Engine()
    sk = np.broadcast_to(np.asarray(sk_per_seed_u, dtype=float),
                         (centers.shape[0],)).astype(float)
    scale = eng.TAU_HOURS * float(eng.delivered_fraction(elapsed_hours))
    # phi_an on a log-spaced radial lattice, interpolated: it is smooth.
    if phi_an_table is None:
        pa_r, pa_v = _phi_an_lattice(eng)
    else:
        pa_r = np.asarray(phi_an_table[0], dtype=float)
        pa_v = np.asarray(phi_an_table[1], dtype=float)
    out = np.zeros((centers.shape[0], pts.shape[0]), dtype=np.float64)
    for s in range(centers.shape[0]):
        d = pts - centers[s]
        r_cm = np.maximum(np.sqrt(np.einsum("ij,ij->i", d, d)) / 10.0,
                          eng.R_FLOOR_CM)
        rate_t = eng._rate_folded(np.full_like(r_cm, 90.0), r_cm)
        out[s] = rate_t * np.interp(r_cm, pa_r, pa_v) * (sk[s] * scale)
    return out if per_seed else out.sum(axis=0)


# ------------------------------------------------------------- the ratio test
def reference_points_near_seeds(reference: Volume, seed_centers, r_max_mm):
    """Flat voxel indices and RAS centres of reference voxels within
    ``r_max_mm`` of any seed (a KD-tree ball query on the voxel centres of
    the seeds' bounding box, so whole-head grids stay cheap)."""
    centers = np.atleast_2d(np.asarray(seed_centers, dtype=float))
    nk, nj, ni = reference.array.shape
    lo = reference.ras_to_index(centers.min(axis=0) - r_max_mm - 1.0)
    hi = reference.ras_to_index(centers.max(axis=0) + r_max_mm + 1.0)
    # index-space box (axis order i, j, k), clipped to the grid
    b0 = np.floor(np.minimum(lo, hi)).astype(int)
    b1 = np.ceil(np.maximum(lo, hi)).astype(int)
    i0, j0, k0 = np.maximum(b0, 0)
    i1, j1, k1 = np.minimum(b1, np.array([ni, nj, nk]) - 1)
    if np.any(np.array([i1, j1, k1]) < np.array([i0, j0, k0])):
        return np.zeros(0, dtype=np.intp), np.zeros((0, 3))
    kk, jj, ii = np.meshgrid(np.arange(k0, k1 + 1), np.arange(j0, j1 + 1),
                             np.arange(i0, i1 + 1), indexing="ij")
    ijk = np.stack([ii.ravel(), jj.ravel(), kk.ravel()], axis=1).astype(float)
    pts = reference.index_to_ras(ijk)
    d, _ = cKDTree(centers).query(pts, distance_upper_bound=r_max_mm + 1e-9)
    keep = np.isfinite(d)
    flat = np.ravel_multi_index((kk.ravel()[keep], jj.ravel()[keep],
                                 ii.ravel()[keep]), reference.array.shape)
    return flat, pts[keep]


def _voxel_offsets(affine, supersample):
    """RAS offsets of the sub-sample centres inside one voxel."""
    n = np.broadcast_to(np.asarray(supersample, dtype=int), (3,))
    if np.any(n < 1):
        raise ValueError("supersample must be >= 1 per axis")
    fr = [(np.arange(ni) + 0.5) / ni - 0.5 for ni in n]
    fi, fj, fk = np.meshgrid(*fr, indexing="ij")
    f = np.stack([fi.ravel(), fj.ravel(), fk.ravel()], axis=1)
    return f @ np.asarray(affine, dtype=float)[:3, :3].T


def binned_ratio(ref, calc, theta_deg, bins_deg=ANGLE_BINS_DEG):
    """Mean / SD / median / n of ``ref / calc`` per ``theta`` bin.

    Bins are ``[lo, hi)`` except the last, which is closed at 90.  Returns a
    list of dicts ``bin_lo, bin_hi, mean, sd, median, n``; empty bins have
    NaN statistics and ``n = 0``.
    """
    ref = np.asarray(ref, dtype=float)
    calc = np.asarray(calc, dtype=float)
    th = np.asarray(theta_deg, dtype=float)
    ok = calc > 0.0
    ratio = np.full(ref.shape, np.nan)
    ratio[ok] = ref[ok] / calc[ok]
    edges = np.asarray(bins_deg, dtype=float)
    rows = []
    for b in range(edges.size - 1):
        lo, hi = edges[b], edges[b + 1]
        sel = (th >= lo) & ((th < hi) if b < edges.size - 2 else (th <= hi)) & ok
        v = ratio[sel]
        rows.append({
            "bin_lo": float(lo), "bin_hi": float(hi), "n": int(v.size),
            "mean": float(v.mean()) if v.size else float("nan"),
            "sd": float(v.std(ddof=1)) if v.size > 1 else float("nan"),
            "median": float(np.median(v)) if v.size else float("nan"),
        })
    return rows


@dataclass
class AngularRatioResult:
    """Output of :func:`angular_symmetry_test`."""

    label: str
    bins: List[Dict[str, float]]          # from binned_ratio
    n_selected: int
    n_candidates: int                      # voxels within r_max of any seed
    overall_mean: float
    overall_median: float
    slope_per_90deg: float                 # least-squares slope of ratio vs theta, over 90 deg
    end_to_end: float                      # mean(last bin) - mean(first bin)
    rms_log: float = float("nan")          # RMS of log(ratio) about its mean: scatter of the fit
    r_median_mm: float = float("nan")      # median distance to the dominant seed (selected voxels)
    r_max_selected_mm: float = float("nan")
    params: Dict[str, object] = field(default_factory=dict)
    # per-voxel detail (selected voxels only)
    flat_index: np.ndarray = field(default=None, repr=False)
    theta_deg: np.ndarray = field(default=None, repr=False)
    r_mm: np.ndarray = field(default=None, repr=False)
    dominant_seed: np.ndarray = field(default=None, repr=False)
    reference: np.ndarray = field(default=None, repr=False)
    calculated: np.ndarray = field(default=None, repr=False)
    points_ras: np.ndarray = field(default=None, repr=False)
    affine: np.ndarray = field(default=None, repr=False)

    @property
    def ratio(self) -> np.ndarray:
        return self.reference / self.calculated

    def table(self, digits=3) -> str:
        """Plain-text table: bin, mean +- sd, n."""
        lines = ["%-10s %8s %8s %7s" % ("theta", "mean", "sd", "n")]
        for b in self.bins:
            lines.append("%3.0f-%-3.0f    %8.*f %8.*f %7d"
                         % (b["bin_lo"], b["bin_hi"], digits, b["mean"],
                            digits, b["sd"], b["n"]))
        lines.append("overall mean %.*f, median %.*f, n %d; slope %+.*f per 90 deg; "
                     "RMS(log ratio) %.*f; r median %.1f, max %.1f mm"
                     % (digits, self.overall_mean, digits, self.overall_median,
                        self.n_selected, digits, self.slope_per_90deg, digits,
                        self.rms_log, self.r_median_mm, self.r_max_selected_mm))
        return "\n".join(lines)


def angular_symmetry_test(reference: Volume, seed_centers, seed_axes,
                          sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                          r_min_mm=3.0, r_max_mm=15.0, min_fraction=0.5,
                          bins_deg=ANGLE_BINS_DEG, supersample=None,
                          calc="2D", exact=True, min_reference_cgy=0.0,
                          label=None, keep_detail=True,
                          _points=None) -> AngularRatioResult:
    """Ratio ``reference / gtcore`` binned by angle to the dominant seed's axis.

    Parameters
    ----------
    reference : Volume
        The dose grid under test, in cGy (e.g. ``gtcore.io.rt.load_rtdose``).
    seed_centers, seed_axes : (N, 3)
        Source positions and long axes (RAS mm) the gtcore calculation uses;
        the axes also define ``theta``.  Axis sign is irrelevant.
    sk_per_seed_u : float or (N,)
        Air-kerma strength per seed [U].
    engine : TG43Engine, optional
        Dataset variant (``TG43Engine("clrp_v2")`` for the Carleton tables).
    r_min_mm, r_max_mm : float
        Distance window to the dominant seed (3-15 mm by default: beyond the
        capsule/grid-averaging zone, inside the single-seed zone).
    min_fraction : float
        A voxel is kept when its dominant seed contributes more than this
        fraction of the gtcore total (0.5 = "> 50 % single-seed").
    bins_deg : sequence
        Angular bin edges.
    supersample : int or (3,) ints, optional
        Evaluate gtcore as the mean over this many sub-samples per voxel
        axis (i, j, k) instead of at the voxel centre -- the grid-averaging
        variant.  ``None``/1 = centre.
    calc : "2D" | "1D"
        gtcore formalism for the denominator.  ``"1D"`` uses
        :func:`point_source_1d_dose` (diagnostic; the axes still define
        ``theta`` and the dominant seed is taken from the 2D contributions).
    exact : bool
        Exact analytic rate (default) or the engine's tabulated kernel.
    min_reference_cgy : float
        Drop voxels whose reference dose is at or below this (e.g. the grid's
        quantisation step).
    label : str
        Variant name carried into the result.
    keep_detail : bool
        Keep the per-voxel arrays on the result.

    Returns
    -------
    AngularRatioResult
    """
    centers, axes, sk = _prepare_seeds(seed_centers, seed_axes, sk_per_seed_u)
    eng = engine if engine is not None else TG43Engine()
    if calc not in ("2D", "1D"):
        raise ValueError("calc must be '2D' or '1D'")
    if not (0.0 <= r_min_mm < r_max_mm):
        raise ValueError("need 0 <= r_min_mm < r_max_mm")

    if _points is None:
        flat, pts = reference_points_near_seeds(reference, centers, r_max_mm)
    else:
        flat, pts = _points
    n_cand = int(flat.size)
    if n_cand == 0:
        raise ValueError("no reference voxels within r_max_mm of the seeds")
    ref_vals = np.asarray(reference.array, dtype=float).ravel()[flat]

    # per-seed 2D contributions, at the centre or averaged over the voxel
    offsets = (np.zeros((1, 3)) if supersample is None
               else _voxel_offsets(reference.affine, supersample))
    per_seed = np.zeros((centers.shape[0], pts.shape[0]))
    for off in offsets:
        per_seed += per_seed_doses(pts + off, centers, axes, sk, engine=eng,
                                   exact=exact)
    per_seed /= offsets.shape[0]
    total2d = per_seed.sum(axis=0)
    dom = per_seed.argmax(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = per_seed[dom, np.arange(pts.shape[0])] / total2d

    # distance and angle to the dominant seed (at the voxel centre)
    d = pts - centers[dom]
    r_mm = np.sqrt(np.einsum("ij,ij->i", d, d))
    z = np.einsum("ij,ij->i", d, axes[dom])
    y = np.sqrt(np.maximum(r_mm * r_mm - z * z, 0.0))
    theta = np.degrees(np.arctan2(y, np.abs(z)))

    keep = ((r_mm >= r_min_mm) & (r_mm <= r_max_mm) & (frac > min_fraction)
            & (total2d > 0.0) & (ref_vals > min_reference_cgy))

    if calc == "1D":
        calc_vals = np.zeros(pts.shape[0])
        for off in offsets:
            calc_vals += point_source_1d_dose(pts + off, centers, sk, engine=eng)
        calc_vals /= offsets.shape[0]
    else:
        calc_vals = total2d

    ref_k, calc_k, th_k = ref_vals[keep], calc_vals[keep], theta[keep]
    rows = binned_ratio(ref_k, calc_k, th_k, bins_deg)
    ratio = ref_k / calc_k if ref_k.size else np.zeros(0)
    if ratio.size >= 2 and np.ptp(th_k) > 0:
        slope = float(np.polyfit(th_k, ratio, 1)[0] * 90.0)
    else:
        slope = float("nan")
    filled = [b for b in rows if b["n"] > 0]
    e2e = (filled[-1]["mean"] - filled[0]["mean"]) if len(filled) >= 2 else float("nan")
    res = AngularRatioResult(
        label=label or ("gtcore-%s %s%s" % (calc, eng.dataset.name,
                                             "" if supersample is None else
                                             " voxel-averaged")),
        bins=rows, n_selected=int(keep.sum()), n_candidates=n_cand,
        overall_mean=float(ratio.mean()) if ratio.size else float("nan"),
        overall_median=float(np.median(ratio)) if ratio.size else float("nan"),
        slope_per_90deg=slope, end_to_end=float(e2e),
        rms_log=(float(np.sqrt(np.mean((np.log(ratio) - np.log(ratio).mean()) ** 2)))
                 if ratio.size else float("nan")),
        r_median_mm=float(np.median(r_mm[keep])) if keep.any() else float("nan"),
        r_max_selected_mm=float(r_mm[keep].max()) if keep.any() else float("nan"),
        params={"dataset": eng.dataset.name, "calc": calc, "exact": bool(exact),
                "r_min_mm": float(r_min_mm), "r_max_mm": float(r_max_mm),
                "min_fraction": float(min_fraction),
                "supersample": (None if supersample is None
                                else [int(v) for v in np.broadcast_to(supersample, (3,))]),
                "sk_per_seed_u": (float(sk[0]) if np.allclose(sk, sk[0])
                                  else sk.tolist()),
                "n_seeds": int(centers.shape[0]),
                "min_reference_cgy": float(min_reference_cgy)},
    )
    if keep_detail:
        res.flat_index = flat[keep]
        res.theta_deg = th_k
        res.r_mm = r_mm[keep]
        res.dominant_seed = dom[keep]
        res.reference = ref_k
        res.calculated = calc_k
        res.points_ras = pts[keep]
        res.affine = np.asarray(reference.affine, dtype=float).copy()
    return res


# --------------------------------------------------------------- axis tools
def compare_axes(axes_a, axes_b) -> Dict[str, object]:
    """Per-seed angle [deg] between two axis sets, sign-blind.

    ``axes_a``, ``axes_b``: ``(N, 3)`` in the same seed order (rows with a
    non-finite entry on either side are skipped).  The angle is folded into
    [0, 90] because a seed axis has no direction (``F`` is symmetric).
    Returns ``angles_deg`` (N, NaN where skipped), ``median``, ``mean``,
    ``max``, ``p95``, ``n``.
    """
    a = np.atleast_2d(np.asarray(axes_a, dtype=float))
    b = np.atleast_2d(np.asarray(axes_b, dtype=float))
    if a.shape != b.shape or a.shape[1] != 3:
        raise ValueError("axis sets must both be (N, 3)")
    ok = np.isfinite(a).all(axis=1) & np.isfinite(b).all(axis=1)
    ang = np.full(a.shape[0], np.nan)
    if ok.any():
        ua, ub = _unit(a[ok]), _unit(b[ok])
        c = np.abs(np.einsum("ij,ij->i", ua, ub))
        ang[ok] = np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))
    v = ang[ok]
    return {
        "angles_deg": ang,
        "n": int(ok.sum()),
        "median": float(np.median(v)) if v.size else float("nan"),
        "mean": float(v.mean()) if v.size else float("nan"),
        "max": float(v.max()) if v.size else float("nan"),
        "p95": float(np.percentile(v, 95)) if v.size else float("nan"),
    }


def perturb_axes(axes, angle_deg, rng=None):
    """Rotate every axis by exactly ``angle_deg`` about a random direction
    perpendicular to it (a controlled axis error).  ``rng``: numpy Generator
    or seed."""
    rng = np.random.default_rng(rng)
    a = _unit(np.atleast_2d(np.asarray(axes, dtype=float)))
    out = np.empty_like(a)
    for i, ai in enumerate(a):
        p = _any_perpendicular(ai)
        q = np.cross(ai, p)
        phi = rng.uniform(0.0, 2.0 * math.pi)
        k = math.cos(phi) * p + math.sin(phi) * q        # random perpendicular
        out[i] = _rotate_about(ai, k, math.radians(angle_deg))
    return out


def _fibonacci_hemisphere(n):
    """``n`` roughly uniform unit vectors on the z >= 0 hemisphere."""
    i = np.arange(n) + 0.5
    z = i / n                                  # (0, 1]
    phi = i * math.pi * (3.0 - math.sqrt(5.0))
    rho = np.sqrt(np.maximum(1.0 - z * z, 0.0))
    return np.stack([rho * np.cos(phi), rho * np.sin(phi), z], axis=1)


def _tilt_grid(axis, half_deg, n=7):
    """Unit vectors tilted from ``axis`` on an n x n grid of +-half_deg."""
    p1 = _any_perpendicular(axis)
    p2 = np.cross(axis, p1)
    g = np.linspace(-half_deg, half_deg, n)
    ta, tb = np.meshgrid(g, g, indexing="ij")
    out = []
    for a_, b_ in zip(ta.ravel(), tb.ravel()):
        v = _rotate_about(axis, p1, math.radians(a_))
        v = _rotate_about(v, p2, math.radians(b_))
        out.append(v / np.linalg.norm(v))
    return np.asarray(out)


_PHI_AN_CACHE: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}


def _phi_an_lattice(eng):
    """phi_an(r) of ``eng`` on a log-spaced lattice, cached per engine."""
    key = id(eng)
    if key not in _PHI_AN_CACHE:
        lat = np.exp(np.linspace(math.log(eng.R_FLOOR_CM), math.log(10.0), 400))
        _PHI_AN_CACHE[key] = (lat, phi_an(eng, lat))
    return _PHI_AN_CACHE[key]


@dataclass
class SourceFit:
    """One source's position and/or long axis recovered from the reference."""

    seed: int
    center_initial: np.ndarray
    center_fitted: np.ndarray
    shift_mm: float                # |center_fitted - center_initial|
    axis_initial: np.ndarray
    axis_fitted: np.ndarray
    angle_deg: float               # between initial and fitted axis (sign-blind)
    rms_initial: float             # RMS of log(ref / model) about its mean, initial geometry
    rms_fitted: float
    scale_fitted: float            # exp(mean log ratio) at the fitted geometry
    n_points: int
    r_window_mm: Tuple[float, float]
    calc: str = "2D"
    fit_position: bool = False
    fit_axis: bool = True


def fit_source_to_reference(reference: Volume, seed_centers, seed_axes, seed,
                            sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                            r_min_mm=2.0, r_max_mm=6.0, min_fraction=0.5,
                            fit_position=False, fit_axis=True, calc="2D",
                            max_shift_mm=1.5, n_directions=800,
                            refine_deg=(6.0, 1.5, 0.4), n_rounds=3,
                            free_scale=True, _points=None) -> SourceFit:
    """Recover one source's long axis and/or position from the reference dose.

    Reference voxels whose centre lies ``r_min_mm``..``r_max_mm`` from the
    source's *initial* position and where that source dominates the gtcore
    total (``min_fraction``, with the given geometry) are the data; the other
    sources' contributions are held fixed at their given geometry.  The cost
    is the RMS of ``log(reference) - log(model)`` about its mean when
    ``free_scale`` (a flat strength / scale mismatch cannot steer the fit),
    or about zero otherwise.

    ``fit_axis``: the axis is scanned over ``n_directions`` hemisphere
    directions, then refined on shrinking local tilt grids (``refine_deg``).
    ``fit_position``: the position is optimised (Nelder-Mead on the three
    offsets, kept within ``max_shift_mm`` of the start -- grid maxima are
    quantised to half a voxel, so the true source lies within that).  With
    both, axis and position alternate for ``n_rounds``.  ``calc="1D"`` fits
    a point-source position only (an axis is meaningless there).
    """
    from scipy.optimize import minimize

    centers, axes, sk = _prepare_seeds(seed_centers, seed_axes, sk_per_seed_u)
    eng = engine if engine is not None else TG43Engine()
    if calc not in ("2D", "1D"):
        raise ValueError("calc must be '2D' or '1D'")
    if calc == "1D":
        fit_axis = False
    s = int(seed)
    if _points is None:
        flat, pts = reference_points_near_seeds(reference, centers[[s]], r_max_mm)
    else:
        flat, pts = _points
    ref = np.asarray(reference.array, dtype=float).ravel()[flat]
    per_seed = per_seed_doses(pts, centers, axes, sk, engine=eng)
    total = per_seed.sum(axis=0)
    r_mm, _t, _z, _y = seed_frame(pts, centers[s], axes[s])
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = per_seed[s] / total
    keep = (r_mm >= r_min_mm) & (r_mm <= r_max_mm) & (frac > min_fraction) \
        & (ref > 0.0) & (total > 0.0)
    if keep.sum() < 8:
        raise ValueError("seed %d: only %d reference voxels in the %g-%g mm shell"
                         % (s, int(keep.sum()), r_min_mm, r_max_mm))
    p = pts[keep]
    log_ref = np.log(ref[keep])
    background = total[keep] - per_seed[s][keep]
    scale = sk[s] * eng.TAU_HOURS
    c0, a0 = centers[s].copy(), axes[s].copy()
    pa_r, pa_v = _phi_an_lattice(eng)

    def own_dose(center, dirs):
        """(M, K) own-seed dose for K candidate axes at one centre."""
        d = p - center
        r_pt = np.sqrt(np.einsum("ij,ij->i", d, d))
        r_cm = np.maximum(r_pt / 10.0, eng.R_FLOOR_CM)
        if calc == "1D":
            rate_t = eng._rate_folded(np.full_like(r_cm, 90.0), r_cm)
            return (rate_t * np.interp(r_cm, pa_r, pa_v) * scale)[:, None]
        dirs = np.atleast_2d(dirs)
        z = d @ dirs.T
        y = np.sqrt(np.maximum(r_pt[:, None] ** 2 - z * z, 0.0))
        th = np.degrees(np.arctan2(y, np.abs(z)))
        return eng._rate_folded(th, r_cm[:, None]) * scale

    def cost_of(center, dirs):
        """(RMS of the log residual, mean log residual) per candidate axis;
        with ``free_scale`` the RMS is taken about the mean (the mean itself
        is the fitted log scale), otherwise about zero."""
        model = np.log(own_dose(center, dirs) + background[:, None])
        resid = log_ref[:, None] - model
        mean = resid.mean(axis=0)
        if free_scale:
            resid = resid - mean[None, :]
        return np.sqrt(np.mean(resid * resid, axis=0)), mean

    def fit_axis_at(center, start, best_cost):
        best = start.copy()
        dirs = _fibonacci_hemisphere(int(n_directions))
        c, _m = cost_of(center, dirs)
        k = int(np.argmin(c))
        if c[k] < best_cost:
            best, best_cost = dirs[k], float(c[k])
        for half in refine_deg:
            cand = _tilt_grid(best, half)
            c, _m = cost_of(center, cand)
            k = int(np.argmin(c))
            if c[k] < best_cost:
                best, best_cost = cand[k], float(c[k])
        return best, best_cost

    def fit_position_at(center, axis, best_cost):
        def f(x):
            if np.linalg.norm(x) > max_shift_mm:
                return 10.0 + float(np.linalg.norm(x))
            return float(cost_of(c0 + x, axis)[0][0])
        x0 = center - c0
        res = minimize(f, x0, method="Nelder-Mead",
                       options={"xatol": 0.01, "fatol": 1e-6, "maxiter": 400,
                                "initial_simplex": np.vstack([x0, x0 + np.eye(3) * 0.3])})
        if res.fun < best_cost:
            return c0 + res.x, float(res.fun)
        return center, best_cost

    cost0 = float(cost_of(c0, a0)[0][0])
    center, axis, cost = c0.copy(), a0.copy(), cost0
    rounds = max(1, int(n_rounds)) if (fit_position and fit_axis) else 1
    for _ in range(rounds):
        if fit_axis:
            axis, cost = fit_axis_at(center, axis, cost)
        if fit_position:
            center, cost = fit_position_at(center, axis, cost)
    c1, m1 = cost_of(center, axis)
    ang = compare_axes(a0[None, :], axis[None, :])["median"] if calc == "2D" else float("nan")
    return SourceFit(seed=s, center_initial=c0, center_fitted=center.copy(),
                     shift_mm=float(np.linalg.norm(center - c0)),
                     axis_initial=a0, axis_fitted=axis.copy(), angle_deg=float(ang),
                     rms_initial=cost0, rms_fitted=float(c1[0]),
                     scale_fitted=float(np.exp(m1[0])), n_points=int(keep.sum()),
                     r_window_mm=(float(r_min_mm), float(r_max_mm)), calc=calc,
                     fit_position=bool(fit_position), fit_axis=bool(fit_axis))


def fit_axis_to_reference(reference: Volume, seed_centers, seed_axes, seed,
                          sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                          **kw) -> SourceFit:
    """:func:`fit_source_to_reference` with the position held fixed (axis only)."""
    kw.setdefault("fit_position", False)
    kw["fit_axis"] = True
    return fit_source_to_reference(reference, seed_centers, seed_axes, seed,
                                   sk_per_seed_u, engine, **kw)


def fit_sources_to_reference(reference: Volume, seed_centers, seed_axes,
                             sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                             **kw) -> List[SourceFit]:
    """:func:`fit_source_to_reference` for every seed (one pass; each fit
    holds the other seeds at their *given* geometry)."""
    centers = np.atleast_2d(np.asarray(seed_centers, dtype=float))
    return [fit_source_to_reference(reference, seed_centers, seed_axes, s,
                                    sk_per_seed_u, engine, **kw)
            for s in range(centers.shape[0])]


def fit_common_axis(reference: Volume, seed_centers, seed_axes,
                    sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                    r_min_mm=2.0, r_max_mm=6.0, min_fraction=0.5,
                    n_directions=800, refine_deg=(6.0, 1.5, 0.4)) -> Dict[str, object]:
    """One long axis shared by every source, fitted to the reference.

    Tests the hypothesis that the TPS gave all seeds the same (default)
    orientation.  The data are the union of the per-seed shells (selected
    with the *given* axes); the model sets every axis to the candidate
    direction; the cost is the RMS of ``log(ref / model)`` about one common
    mean.  Returns ``axis``, ``rms``, ``rms_given`` (the given axes),
    ``rms_z`` (all axes along RAS z), ``angle_to_z_deg`` and ``n_points``.
    """
    centers, axes, sk = _prepare_seeds(seed_centers, seed_axes, sk_per_seed_u)
    eng = engine if engine is not None else TG43Engine()
    flat, pts = reference_points_near_seeds(reference, centers, r_max_mm)
    ref = np.asarray(reference.array, dtype=float).ravel()[flat]
    per_seed = per_seed_doses(pts, centers, axes, sk, engine=eng)
    total = per_seed.sum(axis=0)
    dom = per_seed.argmax(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        frac = per_seed[dom, np.arange(pts.shape[0])] / total
    r_mm = np.linalg.norm(pts - centers[dom], axis=1)
    keep = (r_mm >= r_min_mm) & (r_mm <= r_max_mm) & (frac > min_fraction) \
        & (ref > 0.0) & (total > 0.0)
    if keep.sum() < 8:
        raise ValueError("too few reference voxels in the shells")
    p, log_ref = pts[keep], np.log(ref[keep])

    def rms_about_mean(resid):
        resid = resid - resid.mean()
        return float(np.sqrt(np.mean(resid * resid)))

    def cost_of(u):
        ax = np.tile(np.asarray(u, dtype=float), (centers.shape[0], 1))
        model = np.log(per_seed_doses(p, centers, ax, sk, engine=eng).sum(axis=0))
        return rms_about_mean(log_ref - model)

    dirs = _fibonacci_hemisphere(int(n_directions))
    costs = np.array([cost_of(u) for u in dirs])
    k = int(np.argmin(costs))
    best, best_cost = dirs[k], float(costs[k])
    for half in refine_deg:
        cand = _tilt_grid(best, half)
        c = np.array([cost_of(u) for u in cand])
        k = int(np.argmin(c))
        if c[k] < best_cost:
            best, best_cost = cand[k], float(c[k])
    zax = np.array([0.0, 0.0, 1.0])
    return {"axis": best, "rms": best_cost,
            "rms_given": rms_about_mean(log_ref - np.log(total[keep])),
            "rms_z": cost_of(zax),
            "angle_to_z_deg": float(np.degrees(np.arccos(min(1.0, abs(float(best @ zax)))))),
            "n_points": int(keep.sum())}


def prediction_rows(result: "AngularRatioResult", seed_centers, seed_axes=None,
                    sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                    calc="1D", supersample=None, bins_deg=ANGLE_BINS_DEG):
    """What the binned ratio *would* be had the reference been computed with
    another model: ``D_alt / D_calc`` on the voxels and ``theta`` of
    ``result`` (which must carry its detail).  ``calc`` / ``seed_axes`` /
    ``engine`` describe the alternative (``"1D"``; or ``"2D"`` with other
    axes or another dataset); ``supersample`` averages it over the voxel.
    """
    if result.points_ras is None:
        raise ValueError("result has no per-voxel detail (keep_detail=False)")
    centers = np.atleast_2d(np.asarray(seed_centers, dtype=float))
    eng = engine if engine is not None else TG43Engine()
    pts = result.points_ras
    offsets = (np.zeros((1, 3)) if supersample is None
               else _voxel_offsets(result.affine, supersample))
    alt = np.zeros(pts.shape[0])
    for off in offsets:
        if calc == "1D":
            alt += point_source_1d_dose(pts + off, centers, sk_per_seed_u, engine=eng)
        else:
            if seed_axes is None:
                raise ValueError("2D prediction needs seed_axes")
            alt += per_seed_doses(pts + off, centers, seed_axes, sk_per_seed_u,
                                  engine=eng).sum(axis=0)
    alt /= offsets.shape[0]
    return binned_ratio(alt, result.calculated, result.theta_deg, bins_deg)


# ------------------------------------------------------- sources on the grid
def rtdose_source_positions(reference: Volume, min_cgy=20000.0, merge_mm=3.0):
    """Source positions read off a TPS dose grid: its local maxima above
    ``min_cgy`` (3 x 3 x 3 neighbourhood), merged greedily (highest first)
    when closer than ``merge_mm``.  Positions are voxel centres, i.e.
    quantised to half the grid spacing.  Returns ``(N, 3)`` RAS mm sorted by
    descending peak dose."""
    arr = np.asarray(reference.array, dtype=float)
    peaks = (arr == ndimage.maximum_filter(arr, size=3)) & (arr > float(min_cgy))
    kji = np.argwhere(peaks)
    if kji.size == 0:
        return np.zeros((0, 3))
    kji = kji[np.argsort(-arr[tuple(kji.T)])]
    ras = reference.index_to_ras(kji[:, ::-1].astype(float))
    out: List[np.ndarray] = []
    for p in ras:
        if not out or np.min(np.linalg.norm(np.asarray(out) - p, axis=1)) > merge_mm:
            out.append(p)
    return np.asarray(out)


def match_sources_to_seeds(sources_ras, seed_centers, gate_mm=3.0):
    """One-to-one (Hungarian) match of plan sources to detected seeds.

    Returns ``(index, distance_mm)``: for every source the matched seed
    index (-1 when none within ``gate_mm``) and the match distance (NaN).
    """
    src = np.atleast_2d(np.asarray(sources_ras, dtype=float))
    det = np.atleast_2d(np.asarray(seed_centers, dtype=float))
    idx = np.full(src.shape[0], -1, dtype=int)
    dist = np.full(src.shape[0], np.nan)
    if src.size == 0 or det.size == 0:
        return idx, dist
    D = np.linalg.norm(src[:, None, :] - det[None, :, :], axis=2)
    ri, ci = linear_sum_assignment(D)
    for r_, c_ in zip(ri, ci):
        if D[r_, c_] <= gate_mm:
            idx[r_], dist[r_] = c_, D[r_, c_]
    return idx, dist
