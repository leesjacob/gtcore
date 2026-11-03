"""Clinical dose metrics on top of the TG-43 engine.

GammaTile is prescribed as a dose at depth: 60 Gy to a point 5 mm from the
tile (cavity-wall) surface. The quantities a planner needs to report are
therefore (a) the dose reaching a *rind* of tissue around the cavity, as a
DVH with D90 / V100 / V150 / V200, and (b) the dose *on the wall at depth*,
as an area-weighted coverage fraction. Both are pure numpy over the
``Volume`` / ``trimesh`` objects the rest of the pipeline already produces.

Conventions: dose in cGy, distances in mm, ``rx_cgy`` the prescription.

One definition of the statistics (Paper 2, requirements §2 "Dose statistics")
----------------------------------------------------------------------------
Every D / V number gtcore reports -- planner panel (``dvh.dvh_stats``,
``hrctv.hrctv_stats``), optimizer (``plan.objective.metrics_from_dose``,
``plan.report.weighted_stats``) and validation (``dose_metrics`` on a
``Volume`` + mask) -- is produced by :func:`dose_stats` here.  The rules:

* **Dxx** (dose to at least xx % of the structure) is the *weighted inverted
  CDF*, Hyndman & Fan type 1: sort the doses ascending with their weights,
  form the cumulative weight fraction ``F``, and take the smallest ``d_i``
  with ``F(d_i) >= 1 - xx / 100``.  No interpolation -- Dxx is always a dose
  that occurs in the structure -- and at least xx % of the weight receives
  ``>= Dxx``.  ``D100 = Dmin``.  With unit weights this is numpy's
  ``method="inverted_cdf"``; it is *not* numpy's default linear percentile.
* **Vyyy** (fraction of the structure at or above yyy % of the prescription)
  is ``sum(w[d >= yyy/100 * rx]) / sum(w)`` -- a ``>=`` rule.
* **Empty** structures (no samples, or zero total weight) report ``nan`` for
  every dose and fraction, ``n = 0``, ``total_weight = 0`` and
  ``volume_cc = 0``; callers test ``np.isnan`` rather than ``== 0``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from ..volume import Volume
from .engine import TG43Engine, dose_at_points

__all__ = ["DVH", "dvh", "dose_stats", "weighted_quantile", "dose_metrics",
           "resample_mask_to", "rind_mask", "wall_dose", "surface_coverage",
           "DOSE_STAT_KEYS"]

DOSE_STAT_KEYS = ("D90", "D50", "D100", "Dmin", "Dmax", "Dmean",
                  "V100", "V150", "V200", "n", "total_weight", "volume_cc",
                  "rx_cgy")
# The fixed key set of ``dose_stats``; the first nine are nan on an empty
# structure.

_QUANTILE_SLACK = 4.0 * np.finfo(float).eps
# Relative slack on the cumulative-weight threshold: cumulative unit weights
# are exact integers, so the slack never changes an integer case; for
# non-integer (area) weights it absorbs the rounding of the running sum so
# that ``q * W`` landing exactly on a cumulative weight picks that sample.


# ---------------------------------------------------------------- quantile
def _quantiles_sorted(values_sorted: np.ndarray, cum_weights: np.ndarray,
                      q) -> np.ndarray:
    """Type-1 (inverted CDF) quantiles at fractions ``q`` of the total weight.

    ``values_sorted`` ascending, ``cum_weights`` their cumulative weights
    (all positive, so strictly increasing); returns, for each ``q``, the
    smallest value whose cumulative weight reaches ``q * total``.
    """
    total = float(cum_weights[-1])
    thresh = np.asarray(q, dtype=float) * total - _QUANTILE_SLACK * total
    idx = np.searchsorted(cum_weights, thresh, side="left")
    idx = np.minimum(idx, values_sorted.size - 1)
    return values_sorted[idx]


def weighted_quantile(values, weights, q: float) -> float:
    """Weighted inverted-CDF quantile of ``values`` at ``q`` in [0, 1].

    Smallest sorted value whose cumulative weight reaches ``q * sum(weights)``
    (Hyndman-Fan type 1); equals ``numpy.percentile(values, 100 * q,
    method="inverted_cdf")`` for unit weights, ``q = 0`` is the minimum and
    ``q = 1`` the maximum.  Zero-weight entries are ignored; raises
    ``ValueError`` on empty input, all-zero weights, negative weights or
    ``q`` outside [0, 1].  This is the one quantile every Dxx in gtcore
    uses (``plan.objective.weighted_quantile`` is this function).
    """
    v = np.asarray(values, dtype=float).reshape(-1)
    w = np.asarray(weights, dtype=float).reshape(-1)
    if v.shape != w.shape:
        raise ValueError("values and weights must have the same length")
    q = float(q)
    if not (0.0 <= q <= 1.0):
        raise ValueError("q must lie in [0, 1]")
    if (w < 0).any():
        raise ValueError("weights must be non-negative")
    keep = w > 0
    v, w = v[keep], w[keep]
    if v.size == 0:
        raise ValueError("weighted_quantile of an empty / zero-weight set")
    order = np.argsort(v, kind="stable")
    return float(_quantiles_sorted(v[order], np.cumsum(w[order]), q))


# -------------------------------------------------------------- dose_stats
def _empty_stats(rx: float) -> Dict[str, float]:
    nan = float("nan")
    out = {k: nan for k in DOSE_STAT_KEYS}
    out.update({"n": 0, "total_weight": 0.0, "volume_cc": 0.0, "rx_cgy": rx})
    return out


def dose_stats(doses, rx_cgy: float, weights=None,
               voxel_volume_mm3: Optional[float] = None) -> Dict[str, float]:
    """The one set of dose statistics for a structure (see the module notes).

    Parameters
    ----------
    doses : 1-D array-like, cGy
        One sample per voxel / surface point.
    rx_cgy : float
        Prescription dose; the V-levels are 100 / 150 / 200 % of it.
    weights : 1-D array-like, optional
        Non-negative weight per sample (area weights for a shell, voxel
        volumes for a mixed-grid structure).  ``None`` = every sample counts
        equally.  Samples with zero weight are not part of the structure.
    voxel_volume_mm3 : float, optional
        Volume represented by one unit of weight: with ``weights=None`` the
        voxel volume (``volume_cc = n * voxel_volume_mm3 / 1000``); with
        weights that are volumes in mm^3 pass ``1.0``.  ``None`` ->
        ``volume_cc`` is ``nan`` (a shell has no volume).

    Returns
    -------
    dict with exactly the keys ``DOSE_STAT_KEYS``: ``D90, D50, D100, Dmin,
    Dmax, Dmean`` [cGy], ``V100, V150, V200`` [fraction of the weight],
    ``n`` (samples with positive weight, int), ``total_weight``,
    ``volume_cc``, ``rx_cgy``.  Dxx is the weighted inverted-CDF (type 1)
    quantile, Vyyy the ``>=`` fraction, ``Dmean`` weighted.  An empty
    structure (no samples or zero total weight) gives ``nan`` for every
    dose / fraction, ``n = 0``, ``total_weight = 0``, ``volume_cc = 0``.

    Cost: one ``argsort`` (O(n log n)) plus a few O(n) passes, numpy only.
    """
    d = np.asarray(doses, dtype=float).reshape(-1)
    rx = float(rx_cgy)
    if weights is None:
        if d.size == 0:
            return _empty_stats(rx)
        w = np.ones(d.size, dtype=float)
    else:
        w = np.asarray(weights, dtype=float).reshape(-1)
        if w.shape != d.shape:
            raise ValueError("weights must have one entry per dose (%d != %d)"
                             % (w.size, d.size))
        if (w < 0).any():
            raise ValueError("weights must be non-negative")
        keep = w > 0
        if not keep.all():
            d, w = d[keep], w[keep]
        if d.size == 0:
            return _empty_stats(rx)
    total = float(w.sum())
    if not (total > 0.0):
        return _empty_stats(rx)

    order = np.argsort(d, kind="stable")
    d_sorted = d[order]
    cw = np.cumsum(w[order])
    d90, d50 = _quantiles_sorted(d_sorted, cw, (0.10, 0.50))
    dmin = float(d_sorted[0])
    out = {
        "D90": float(d90),
        "D50": float(d50),
        "D100": dmin,
        "Dmin": dmin,
        "Dmax": float(d_sorted[-1]),
        "Dmean": float(w @ d) / total,
        "V100": float(w @ (d >= rx)) / total,
        "V150": float(w @ (d >= 1.5 * rx)) / total,
        "V200": float(w @ (d >= 2.0 * rx)) / total,
        "n": int(d.size),
        "total_weight": total,
        "volume_cc": (total * float(voxel_volume_mm3) / 1000.0
                      if voxel_volume_mm3 is not None else float("nan")),
        "rx_cgy": rx,
    }
    return out


# ------------------------------------------------------------------------ DVH
@dataclass
class DVH:
    """Cumulative dose-volume histogram of one structure.

    Stores the sorted voxel doses rather than a binned curve, so ``D`` and
    ``V`` are exact order statistics and the binned ``curve`` is derived
    on demand at whatever resolution the caller wants.  ``D`` and ``V``
    follow the :func:`dose_stats` rules (type-1 quantile, ``>=``).
    """

    doses_sorted: np.ndarray        # ascending, cGy, one entry per voxel
    voxel_volume_mm3: float

    @property
    def n_voxels(self) -> int:
        return int(self.doses_sorted.size)

    @property
    def volume_cc(self) -> float:
        return self.n_voxels * self.voxel_volume_mm3 / 1000.0

    def D(self, percent):
        """Dose [cGy] received by at least ``percent`` % of the volume.

        Weighted inverted-CDF (type 1) quantile with unit weights: the
        smallest voxel dose ``d`` such that at least ``percent`` % of the
        voxels receive ``>= d`` -- the same rule as :func:`dose_stats`.
        D90 is the classic coverage statistic; D100 is the minimum dose.
        """
        if self.n_voxels == 0:
            return float("nan")
        p = np.asarray(percent, dtype=float)
        if np.any(p < 0.0) or np.any(p > 100.0):
            raise ValueError("percent must be within [0, 100]")
        cw = np.arange(1, self.n_voxels + 1, dtype=float)
        out = _quantiles_sorted(self.doses_sorted, cw, 1.0 - p / 100.0)
        return float(out) if out.ndim == 0 else out

    def V(self, dose_cgy, relative=True):
        """Volume receiving at least ``dose_cgy`` (``>=``): fraction (or cc)."""
        d = np.asarray(dose_cgy, dtype=float)
        n_ge = self.n_voxels - np.searchsorted(self.doses_sorted, d,
                                               side="left")
        if relative:
            out = n_ge / self.n_voxels if self.n_voxels else np.full_like(
                d, np.nan, dtype=float)
        else:
            out = n_ge * self.voxel_volume_mm3 / 1000.0
        return float(out) if np.ndim(out) == 0 else out

    def stats(self, rx_cgy: float) -> Dict[str, float]:
        """:func:`dose_stats` of this structure (equal-volume voxels)."""
        return dose_stats(self.doses_sorted, rx_cgy,
                          voxel_volume_mm3=self.voxel_volume_mm3)

    def curve(self, n_bins=256, max_dose_cgy=None):
        """Binned cumulative curve: ``(dose_axis, fraction >= dose)``."""
        top = float(max_dose_cgy) if max_dose_cgy is not None else (
            float(self.doses_sorted[-1]) if self.n_voxels else 1.0)
        axis = np.linspace(0.0, top, int(n_bins))
        return axis, self.V(axis)


def dvh(dose_volume: Volume, mask=None) -> DVH:
    """DVH of ``dose_volume`` inside ``mask`` (bool ``[k, j, i]`` on the
    dose grid; ``None`` = the whole grid)."""
    arr = np.asarray(dose_volume.array, dtype=float)
    if mask is None:
        vals = arr.ravel()
    else:
        m = np.asarray(mask, dtype=bool)
        if m.shape != arr.shape:
            raise ValueError("mask shape %s != dose grid shape %s"
                             % (m.shape, arr.shape))
        vals = arr[m]
    return DVH(np.sort(vals), float(np.prod(dose_volume.spacing)))


def dose_metrics(dose_volume: Volume, mask, rx_cgy: float) -> Dict[str, float]:
    """Standard permanent-implant statistics for one voxel structure: the
    validation-path wrapper of :func:`dose_stats`.

    Returns the full ``DOSE_STAT_KEYS`` set (D90/D100/D50/Dmin/Dmean/Dmax
    [cGy], V100/V150/V200 as fractions of the structure at >= 100/150/200 %
    of ``rx_cgy``, ``volume_cc`` from the voxel count x voxel volume, ``n``);
    an empty mask gives nan doses and ``volume_cc = 0``.
    """
    return dvh(dose_volume, mask).stats(rx_cgy)


# ------------------------------------------------------------- structures
RESAMPLE_CHUNK_VOXELS = 4_000_000   # ~100 MB of float64 points per chunk


def resample_mask_to(dose_volume: Volume, mask, affine,
                     chunk_voxels: int = RESAMPLE_CHUNK_VOXELS):
    """Nearest-neighbour resample of a bool mask (``[k, j, i]`` with its own
    ``affine``) onto the dose grid. Returns bool ``[k, j, i]`` on the grid.

    Every output voxel centre is mapped to RAS and looked up (order 0) in the
    source mask; the work is done in slabs of at most ``chunk_voxels`` so a
    full clinical CT grid (204 x 512 x 512) stays within a few hundred MB.
    """
    src = Volume(np.asarray(mask, dtype=np.uint8), np.asarray(affine, float))
    nk, nj, ni = dose_volume.array.shape
    out = np.zeros((nk, nj, ni), dtype=bool)
    if nk == 0 or nj == 0 or ni == 0:
        return out
    per_slab = max(1, int(chunk_voxels) // max(1, nj * ni))
    jj, ii = np.meshgrid(np.arange(nj), np.arange(ni), indexing="ij")
    jj = jj.ravel().astype(float)
    ii = ii.ravel().astype(float)
    for k0 in range(0, nk, per_slab):
        k1 = min(nk, k0 + per_slab)
        n_slab = k1 - k0
        kk = np.repeat(np.arange(k0, k1, dtype=float), nj * ni)
        ijk = np.stack([np.tile(ii, n_slab), np.tile(jj, n_slab), kk], axis=1)
        pts = dose_volume.index_to_ras(ijk)
        vals = src.sample_ras(pts, order=0, fill=0.0)
        out[k0:k1] = (np.asarray(vals) > 0.5).reshape(n_slab, nj, ni)
    return out


def rind_mask(dose_volume: Volume, structure_mask, structure_affine,
              depth_mm=5.0, exclude_mask=None):
    """Shell of tissue within ``depth_mm`` outside a structure, on the dose
    grid -- the GammaTile target volume for a resection cavity.

    Thin wrapper (kept for its callers) around :func:`gtcore.dose.targets.rind5`,
    the one ``RIND5`` definition: ``rind5(structure_mask, structure_affine,
    clip_mask=~exclude_mask, grid_affine=dose_volume.affine,
    grid_shape=dose_volume.array.shape, depth_mm=depth_mm).mask``.  New code
    should call ``rind5`` directly and keep the ``Target`` (provenance).

    Parameters
    ----------
    dose_volume : Volume
        Defines the output grid.
    structure_mask, structure_affine
        The cavity (or any structure) mask in its own grid.
    depth_mm : float
        Rind thickness measured from the structure surface (Euclidean
        distance in physical mm, so anisotropic grids are handled).
    exclude_mask : bool array on the dose grid, optional
        Voxels to drop from the rind, e.g. skull or air outside the head.
    """
    if depth_mm <= 0.0:
        raise ValueError("depth_mm must be positive")
    from .targets import rind5                   # lazy: targets imports this module
    clip = None if exclude_mask is None else ~np.asarray(exclude_mask, dtype=bool)
    target = rind5(structure_mask, structure_affine,
                   clip_mask=clip,
                   clip_affine=dose_volume.affine if clip is not None else None,
                   depth_mm=float(depth_mm),
                   grid_affine=dose_volume.affine,
                   grid_shape=dose_volume.array.shape,
                   clip_source="exclude_mask" if clip is not None else None)
    return target.mask


# ------------------------------------------------------------- wall dose
def wall_dose(mesh, depth_mm, seed_centers=None, seed_axes=None,
              sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
              elapsed_hours=None, dose_volume: Optional[Volume] = None):
    """Dose [cGy] at ``depth_mm`` along each vertex normal of ``mesh``.

    The cavity mesh from :func:`gtcore.segment.mask_to_mesh` carries normals
    pointing out of the cavity into the surrounding brain, so the offset
    points sit ``depth_mm`` deep in the wall -- the prescription point of a
    GammaTile implant. Dose comes from the exact engine over the given
    seeds, or by trilinear sampling of ``dose_volume`` if one is supplied.
    Returns a ``(n_vertices,)`` array.
    """
    if len(mesh.vertices) == 0:
        return np.zeros(0)
    pts = np.asarray(mesh.vertices, float) \
        + float(depth_mm) * np.asarray(mesh.vertex_normals, float)
    if dose_volume is not None:
        return np.asarray(dose_volume.sample_ras(pts), dtype=float)
    if seed_centers is None or seed_axes is None:
        raise ValueError("give seed_centers/seed_axes or a dose_volume")
    return dose_at_points(seed_centers, seed_axes, pts, sk_per_seed_u,
                          engine=engine, elapsed_hours=elapsed_hours)


def surface_coverage(mesh, vertex_dose, rx_cgy):
    """Area fraction of ``mesh`` whose (face-mean) dose is >= ``rx_cgy``."""
    faces = np.asarray(mesh.faces)
    if faces.shape[0] == 0:
        return float("nan")
    vd = np.asarray(vertex_dose, dtype=float)
    if vd.shape != (len(mesh.vertices),):
        raise ValueError("vertex_dose must have one value per vertex")
    face_dose = vd[faces].mean(axis=1)
    area = np.asarray(mesh.area_faces, dtype=float)
    total = area.sum()
    if total <= 0.0:
        return float("nan")
    return float(area[face_dose >= float(rx_cgy)].sum() / total)
