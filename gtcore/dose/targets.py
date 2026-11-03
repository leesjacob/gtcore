"""Target structures: one definition of every volume a dose is scored on.

Paper 2 scores dose on four kinds of target, and every number that leaves
the code must say which one it used.  This module builds all four and
attaches the provenance (cavity source, clip mask, grid, depth, commit) to
the object, so the output tables can carry it without the caller
remembering to.

``HRCTV_PHYS``
    The physician's planning HR-CTV as contoured in the TPS (an RTSTRUCT ROI,
    expected ``CTV_GT``), wrapped unchanged by :func:`hrctv_phys`.  The
    reference structure for every dose comparison with the TPS.
``RIND5(cavity, clip)``
    The implant-independent HR-CTV: the Euclidean 5 mm rind of a cavity mask
    (distance transform in **physical mm**, so anisotropic grids are handled),
    minus the cavity, intersected with a clip mask (TPS ``Brain`` when
    present, gtcore's cranial interior or brain otherwise).  Built by
    :func:`rind5` from any cavity (``Cav_Post`` or gtcore's automatic
    cavity; the provenance says which).  Planner target, optimizer target,
    F8, Table 3.
``IMPLANT_REACH(r)``
    ``RIND5`` restricted to the voxels within ``r`` mm of a *seed centre*
    (seeds, not tile footprints).  :func:`implant_reach`.  Reproduces the
    physician's partial-rind convention for F6 only; **never a planning
    target** -- a target defined by the placement cannot score the placement.
``S5``
    The cavity wall mesh offset 5 mm along its vertex normals, with area
    weights (:func:`s5`, the optimizer's internal surrogate).  Reported only
    as "S5 coverage", never as D90 of the HR-CTV.

Conventions: RAS mm everywhere; masks are bool ``[k, j, i]`` with a 4x4
affine mapping voxel ``(i, j, k, 1)`` to RAS, as :class:`gtcore.volume.Volume`.
Dose statistics on a target are ``metrics.dose_stats(doses, rx,
weights=target.weights, voxel_volume_mm3=target.voxel_volume_mm3)``
(:func:`target_stats` does exactly that).
"""
from __future__ import annotations

import functools
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from ..volume import Volume
from .engine import TG43Engine, dose_at_points
from .metrics import resample_mask_to

__all__ = ["RIND_DEPTH_MM", "Target", "rind5", "hrctv_phys", "implant_reach",
           "s5", "dice", "coverage", "volume_cc", "target_doses_exact",
           "target_doses_from_grid", "target_stats", "same_grid"]

RIND_DEPTH_MM = 5.0     # clinical margin: 60 Gy to the surface 5 mm beyond the wall

_UNLABELLED = "unlabelled"


# ------------------------------------------------------------------ helpers
@functools.lru_cache(maxsize=1)
def _git_commit() -> Optional[str]:
    """Short commit hash of the checkout containing this file (None if no git)."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5.0)
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:
        pass
    return None


def _spacing(affine) -> np.ndarray:
    """Per-axis voxel spacing ``(si, sj, sk)`` of a 4x4 affine."""
    return np.linalg.norm(np.asarray(affine, dtype=float)[:3, :3], axis=0)


def _grid_provenance(affine, shape) -> Dict[str, Any]:
    """``{"spacing_mm": [si, sj, sk], "shape": [nk, nj, ni], "origin_ras": [x, y, z]}``
    -- spacing per ``(i, j, k)`` axis as ``Volume.spacing``, shape in array
    order ``[k, j, i]`` as ``Volume.array.shape``."""
    aff = np.asarray(affine, dtype=float)
    return {"spacing_mm": [float(v) for v in _spacing(aff)],
            "shape": [int(s) for s in shape],
            "origin_ras": [float(v) for v in aff[:3, 3]]}


def same_grid(affine_a, shape_a, affine_b, shape_b, atol: float = 1e-6) -> bool:
    """True when two voxel grids coincide (same shape, affines equal to ``atol``)."""
    if tuple(int(s) for s in shape_a) != tuple(int(s) for s in shape_b):
        return False
    return bool(np.allclose(np.asarray(affine_a, float), np.asarray(affine_b, float),
                            atol=atol, rtol=0.0))


def _on_grid(mask, affine, grid_affine, grid_shape) -> np.ndarray:
    """``mask`` (bool ``[k, j, i]`` on ``affine``) on the output grid: unchanged
    when the grids coincide, else nearest-neighbour resampled
    (:func:`gtcore.dose.metrics.resample_mask_to`)."""
    m = np.asarray(mask, dtype=bool)
    if m.ndim != 3:
        raise ValueError("mask must be 3-D [k, j, i], got shape %s" % (m.shape,))
    if affine is None:
        raise ValueError("a mask needs its affine")
    if same_grid(affine, m.shape, grid_affine, grid_shape):
        return m
    grid = Volume(np.zeros(tuple(grid_shape), dtype=np.uint8), np.asarray(grid_affine, float))
    return resample_mask_to(grid, m, np.asarray(affine, float))


def _rind_of(cavity: np.ndarray, affine, depth_mm: float) -> np.ndarray:
    """Voxels within ``depth_mm`` (Euclidean, physical mm) of ``cavity`` and
    outside it, on the cavity's own grid."""
    if not cavity.any():
        return np.zeros_like(cavity, dtype=bool)
    # array order is [k, j, i]; the affine's spacing is (si, sj, sk)
    dist = ndimage.distance_transform_edt(~cavity, sampling=_spacing(affine)[::-1])
    return (dist <= float(depth_mm)) & ~cavity


def _stats(doses, rx_cgy: float, weights=None, voxel_volume_mm3=None) -> Dict[str, float]:
    """Dose statistics seam.

    Phase 2 unifies every statistic under ``metrics.dose_stats(doses, rx_cgy,
    weights=None, voxel_volume_mm3=None)``; that function is used as soon as
    it exists.  Until then the pre-Phase-2 functions stand in: the planner's
    ``hrctv_stats`` for equal-volume voxels and the optimizer's
    ``weighted_stats`` for area-weighted shells (same D90/V100 conventions as
    today's reports; empty input gives NaN / 0 as those functions define it).
    """
    try:
        from .metrics import dose_stats           # Phase 2, agent dose-stats
    except ImportError:                           # pragma: no cover - interim
        dose_stats = None
    if dose_stats is not None:
        return dose_stats(doses, rx_cgy, weights=weights,
                          voxel_volume_mm3=voxel_volume_mm3)
    if weights is None:                           # pragma: no cover - interim
        from .hrctv import hrctv_stats
        return hrctv_stats(doses, rx_cgy,
                           1.0 if voxel_volume_mm3 is None else float(voxel_volume_mm3))
    from ..plan.report import weighted_stats      # pragma: no cover - interim
    out = weighted_stats(doses, weights, rx_cgy)
    out.setdefault("rx_cgy", float(rx_cgy))
    out.setdefault("volume_cc", float("nan"))
    return out


# ------------------------------------------------------------------- Target
@dataclass(eq=False)
class Target:
    """One scoring structure with its provenance.

    Either a voxel structure (``mask`` bool ``[k, j, i]`` on ``affine``;
    ``HRCTV_PHYS``, ``RIND5``, ``IMPLANT_REACH``) or a weighted point set
    (``points`` (N, 3) RAS mm with area ``weights``; ``S5``).  For a voxel
    structure ``points`` caches the voxel centres once computed.

    ``provenance`` is JSON-serialisable and written into every output table:
    ``cavity_source`` (``"RTSTRUCT:Cav_Post"`` | ``"gtcore:cavity"`` |
    ``"mesh"`` | None), ``clip_source`` (``"RTSTRUCT:Brain"`` |
    ``"gtcore:cranial_interior"`` | ``"gtcore:brain"`` | None), ``grid``
    (``spacing_mm``, ``shape``, ``origin_ras``; None for S5), ``depth_mm``,
    ``r_mm`` (IMPLANT_REACH), ``roi_name`` (HRCTV_PHYS), ``n_seeds``
    (IMPLANT_REACH), ``builder`` and ``git_commit``.
    """

    name: str                                   # HRCTV_PHYS | RIND5 | IMPLANT_REACH | S5
    mask: Optional[np.ndarray]                  # bool [k, j, i] on `affine`; None for S5
    affine: Optional[np.ndarray]                # (4, 4) voxel (i, j, k, 1) -> RAS mm
    points: Optional[np.ndarray] = None         # (N, 3) RAS mm (S5; cached voxel centres)
    weights: Optional[np.ndarray] = None        # (N,) area weights (S5), else None
    provenance: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.mask is not None:
            self.mask = np.asarray(self.mask, dtype=bool)
            if self.mask.ndim != 3:
                raise ValueError("Target.mask must be 3-D [k, j, i], got %s"
                                 % (self.mask.shape,))
            if self.affine is None:
                raise ValueError("a voxel Target needs an affine")
            self.affine = np.asarray(self.affine, dtype=float)
            if self.affine.shape != (4, 4):
                raise ValueError("Target.affine must be 4x4, got %s" % (self.affine.shape,))
            if self.points is not None:
                self.points = np.asarray(self.points, dtype=float).reshape(-1, 3)
            if self.weights is not None:
                raise ValueError("a voxel Target has no weights (every voxel counts "
                                 "its volume)")
        else:
            if self.points is None:
                raise ValueError("a Target needs a mask or points")
            self.points = np.asarray(self.points, dtype=float).reshape(-1, 3)
            if self.weights is not None:
                self.weights = np.asarray(self.weights, dtype=float).reshape(-1)
                if self.weights.shape[0] != self.points.shape[0]:
                    raise ValueError("weights must have one entry per point")
                if (self.weights < 0.0).any():
                    raise ValueError("weights must be non-negative")
        self.provenance = dict(self.provenance)
        self.provenance.setdefault("name", str(self.name))

    # ------------------------------------------------------------ geometry
    @property
    def is_voxel(self) -> bool:
        return self.mask is not None

    @property
    def n(self) -> int:
        """Number of voxels (voxel target) or sample points (S5)."""
        if self.mask is not None:
            return int(self.mask.sum())
        return int(self.points.shape[0])

    @property
    def shape(self):
        return None if self.mask is None else self.mask.shape

    @property
    def spacing_mm(self) -> Optional[np.ndarray]:
        """``(si, sj, sk)`` of the grid; None for a point target."""
        return None if self.affine is None else _spacing(self.affine)

    @property
    def voxel_volume_mm3(self) -> Optional[float]:
        """Volume of one voxel [mm^3]; None for a point target (S5)."""
        if self.affine is None:
            return None
        return float(abs(np.linalg.det(self.affine[:3, :3])))

    @property
    def volume_cc(self) -> float:
        """Structure volume [cc]; NaN for a point target."""
        vv = self.voxel_volume_mm3
        if vv is None:
            return float("nan")
        return self.n * vv / 1000.0

    @property
    def total_weight(self) -> float:
        if self.weights is None:
            return float(self.n)
        return float(self.weights.sum())

    def voxel_centers_ras(self) -> np.ndarray:
        """(N, 3) RAS mm sample points in the order the dose vectors use: the
        rind voxel centres in C (``k, j, i``) order, cached in ``points``;
        the shell points for S5."""
        if self.mask is None:
            return self.points
        if self.points is None or self.points.shape[0] != self.n:
            kk, jj, ii = np.nonzero(self.mask)
            if kk.size:
                ijk = np.stack([ii, jj, kk], axis=1).astype(float)
                self.points = self.as_volume().index_to_ras(ijk)
            else:
                self.points = np.zeros((0, 3))
        return self.points

    def as_volume(self) -> Volume:
        """The mask as a ``Volume`` (uint8) for resampling / export."""
        if self.mask is None:
            raise ValueError("%s is a point target; it has no voxel volume" % self.name)
        meta = {"kind": "target_mask", "target": self.name}
        meta.update({k: v for k, v in self.provenance.items() if k != "grid"})
        return Volume(self.mask.astype(np.uint8), self.affine, meta)

    def describe(self) -> str:
        p = self.provenance
        bits = [self.name]
        if p.get("cavity_source"):
            bits.append("cavity=%s" % p["cavity_source"])
        if p.get("roi_name"):
            bits.append("roi=%s" % p["roi_name"])
        if p.get("clip_source"):
            bits.append("clip=%s" % p["clip_source"])
        if p.get("depth_mm") is not None:
            bits.append("depth=%g mm" % p["depth_mm"])
        if p.get("r_mm") is not None:
            bits.append("r=%g mm (%d seeds)" % (p["r_mm"], p.get("n_seeds", 0)))
        if self.mask is not None:
            sp = self.spacing_mm
            bits.append("%.2f cc (%d voxels @ %gx%gx%g mm)"
                        % (self.volume_cc, self.n, sp[0], sp[1], sp[2]))
        else:
            bits.append("%d points, %.0f mm^2" % (self.n, self.total_weight))
        return ", ".join(bits)

    def __repr__(self) -> str:                      # pragma: no cover - cosmetic
        return "Target(%s)" % self.describe()


# ----------------------------------------------------------------- builders
def rind5(cavity_mask, cavity_affine, clip_mask=None, clip_affine=None,
          depth_mm: float = RIND_DEPTH_MM, grid_affine=None, grid_shape=None,
          cavity_source: str = "gtcore:cavity",
          clip_source: Optional[str] = None) -> Target:
    """``RIND5``: the Euclidean ``depth_mm`` rind of a cavity, minus the cavity,
    intersected with ``clip_mask`` -- **the** HR-CTV for planner, optimizer, F8
    and Table 3.

    ``dist = distance_transform_edt(~cavity, sampling=spacing[::-1])`` in
    physical mm; ``rind = (dist <= depth_mm) & ~cavity & clip``.  The output
    grid is the cavity's own grid unless ``grid_affine`` / ``grid_shape`` are
    given, in which case both masks are first resampled nearest-neighbour to
    that grid (:func:`gtcore.dose.metrics.resample_mask_to`) and the distance
    transform runs there.

    ``cavity_source`` / ``clip_source`` are provenance labels
    (``"RTSTRUCT:Cav_Post"``, ``"gtcore:cavity"``; ``"RTSTRUCT:Brain"``,
    ``"gtcore:cranial_interior"``, ``"gtcore:brain"``).  A clip mask given
    without a label is recorded as ``"unlabelled"``; no clip mask is ``None``.
    """
    depth = float(depth_mm)
    if not depth > 0.0:
        raise ValueError("depth_mm must be positive")
    if (grid_affine is None) != (grid_shape is None):
        raise ValueError("give both grid_affine and grid_shape, or neither")
    cavity = np.asarray(cavity_mask, dtype=bool)
    if cavity.ndim != 3:
        raise ValueError("cavity_mask must be 3-D [k, j, i], got %s" % (cavity.shape,))
    if cavity_affine is None:
        raise ValueError("cavity_affine is required")
    cav_aff = np.asarray(cavity_affine, dtype=float)
    if cav_aff.shape != (4, 4):
        raise ValueError("cavity_affine must be 4x4")
    if grid_affine is None:
        grid_affine, grid_shape = cav_aff, cavity.shape
    grid_affine = np.asarray(grid_affine, dtype=float)
    grid_shape = tuple(int(s) for s in grid_shape)
    if grid_affine.shape != (4, 4) or len(grid_shape) != 3:
        raise ValueError("grid_affine must be 4x4 and grid_shape 3-D")

    cav_g = _on_grid(cavity, cav_aff, grid_affine, grid_shape)
    rind = _rind_of(cav_g, grid_affine, depth)

    clip_label = None
    if clip_mask is not None:
        clip_label = clip_source if clip_source is not None else _UNLABELLED
        if clip_affine is None:
            clip = np.asarray(clip_mask, dtype=bool)
            if clip.shape != cavity.shape:
                raise ValueError("clip_mask without clip_affine must share the "
                                 "cavity grid (shape %s != %s)"
                                 % (clip.shape, cavity.shape))
            clip_affine = cav_aff
        rind &= _on_grid(clip_mask, np.asarray(clip_affine, float), grid_affine, grid_shape)

    prov = {
        "builder": "rind5",
        "cavity_source": str(cavity_source) if cavity_source is not None else None,
        "clip_source": clip_label,
        "grid": _grid_provenance(grid_affine, grid_shape),
        "depth_mm": depth,
        "git_commit": _git_commit(),
    }
    return Target(name="RIND5", mask=rind, affine=grid_affine, provenance=prov)


def hrctv_phys(mask, affine, roi_name: str = "CTV_GT",
               source: str = "RTSTRUCT") -> Target:
    """``HRCTV_PHYS``: the physician's planning HR-CTV as contoured (an
    RTSTRUCT ROI rasterised onto a grid), wrapped unchanged.  The reference
    structure for every TPS dose comparison (F5)."""
    m = np.asarray(mask, dtype=bool)
    aff = np.asarray(affine, dtype=float)
    prov = {
        "builder": "hrctv_phys",
        "cavity_source": None,
        "clip_source": None,
        "grid": _grid_provenance(aff, m.shape),
        "depth_mm": None,
        "roi_name": str(roi_name),
        "roi_source": "%s:%s" % (source, roi_name),
        "git_commit": _git_commit(),
    }
    return Target(name="HRCTV_PHYS", mask=m, affine=aff, provenance=prov)


def implant_reach(rind: Target, seed_centers_ras, r_mm: float) -> Target:
    """``IMPLANT_REACH(r)``: ``rind.mask`` restricted to the voxels whose centre
    lies within ``r_mm`` of the nearest **seed centre** (exact Euclidean
    distance to the given centres; seeds, not tile footprints -- with 6 of 8
    tiles fitted, footprints would exclude the wall under the unfitted tiles).

    F6 only.  This reproduces the physician's partial-rind contouring
    convention, with ``r`` calibrated on the clinical case against ``CTV_GT``
    and stated as such.  **It is never a planning or optimizer target**: a
    target defined by the seed placement cannot score that placement.
    """
    if rind.mask is None:
        raise ValueError("implant_reach needs a voxel rind (RIND5), not a point target")
    r = float(r_mm)
    if r < 0.0:
        raise ValueError("r_mm must be >= 0")
    seeds = np.asarray(seed_centers_ras, dtype=float).reshape(-1, 3)
    mask = np.zeros_like(rind.mask, dtype=bool)
    if seeds.shape[0] and rind.mask.any():
        centers = rind.voxel_centers_ras()
        d, _ = cKDTree(seeds).query(centers)
        # np.nonzero order == boolean-index order (C order), so this lines up
        mask[rind.mask] = d <= r
    prov = dict(rind.provenance)
    prov.update({
        "builder": "implant_reach",
        "rind_builder": rind.provenance.get("builder"),
        "r_mm": r,
        "n_seeds": int(seeds.shape[0]),
        "git_commit": _git_commit(),
    })
    prov["name"] = "IMPLANT_REACH"
    return Target(name="IMPLANT_REACH", mask=mask, affine=rind.affine, provenance=prov)


def s5(mesh, offset_mm: float = RIND_DEPTH_MM) -> Target:
    """``S5``: the wall mesh pushed ``offset_mm`` outward along its vertex
    normals, each vertex weighted by its area on the offset shell
    (:meth:`gtcore.plan.TargetSet.from_shell`).  The optimizer's internal
    surrogate; reported only as "S5 coverage", never as a D90 of the HR-CTV."""
    from ..plan import TargetSet          # lazy: gtcore.plan is a heavier import
    ts = TargetSet.from_shell(mesh, float(offset_mm))
    prov = {
        "builder": "s5",
        "cavity_source": "mesh",
        "clip_source": None,
        "grid": None,
        "depth_mm": float(offset_mm),
        "n_points": int(len(ts)),
        "n_faces": int(np.asarray(mesh.faces).shape[0]),
        "git_commit": _git_commit(),
    }
    return Target(name="S5", mask=None, affine=None, points=ts.points,
                  weights=ts.weights, provenance=prov)


# ------------------------------------------------------------- comparisons
def _mask_pair(a: Target, b: Target):
    """``(a.mask, b.mask on a's grid)``; raises for point targets."""
    if a.mask is None or b.mask is None:
        raise ValueError("mask comparisons need voxel targets (not S5)")
    bm = _on_grid(b.mask, b.affine, a.affine, a.mask.shape)
    return a.mask, bm


def dice(a: Target, b: Target) -> float:
    """Dice coefficient ``2|a & b| / (|a| + |b|)``; ``b`` is resampled
    nearest-neighbour onto ``a``'s grid when the grids differ.  NaN when
    both are empty."""
    am, bm = _mask_pair(a, b)
    denom = int(am.sum()) + int(bm.sum())
    if denom == 0:
        return float("nan")
    return 2.0 * int((am & bm).sum()) / denom


def coverage(a: Target, b: Target) -> float:
    """Fraction of ``b`` inside ``a`` (``|a & b| / |b|``, on ``a``'s grid);
    NaN when ``b`` is empty there."""
    am, bm = _mask_pair(a, b)
    nb = int(bm.sum())
    if nb == 0:
        return float("nan")
    return int((am & bm).sum()) / nb


def volume_cc(t: Target) -> float:
    """Structure volume [cc] (NaN for a point target)."""
    return t.volume_cc


# ------------------------------------------------------------------- doses
def target_doses_exact(target: Target, seed_centers, seed_axes,
                       sk_per_seed_u: float = TG43Engine.DEFAULT_SK_U, engine=None,
                       elapsed_hours=None, interference=None) -> np.ndarray:
    """Exact TG-43 dose [cGy] at the target's sample points (voxel centres, or
    the S5 shell points), ``(N,)`` in the order of ``voxel_centers_ras()``."""
    pts = target.voxel_centers_ras()
    if pts.shape[0] == 0:
        return np.zeros(0)
    return np.asarray(dose_at_points(seed_centers, seed_axes, pts, sk_per_seed_u,
                                     engine=engine, elapsed_hours=elapsed_hours,
                                     interference=interference), dtype=float).reshape(-1)


def target_doses_from_grid(target: Target, dose_volume: Volume) -> np.ndarray:
    """Trilinear sample of a dose grid [cGy] at the target's sample points,
    ``(N,)`` in the order of ``voxel_centers_ras()``; outside the grid reads
    0 cGy.  Cheaper than the exact engine, less accurate near the seeds."""
    pts = target.voxel_centers_ras()
    if pts.shape[0] == 0:
        return np.zeros(0)
    return np.asarray(dose_volume.sample_ras(pts, order=1, fill=0.0),
                      dtype=float).reshape(-1)


def target_stats(target: Target, doses, rx_cgy: float) -> Dict[str, Any]:
    """Dose statistics of ``doses`` (from :func:`target_doses_exact` or
    :func:`target_doses_from_grid`) on ``target``: ``dose_stats(doses, rx,
    weights=target.weights, voxel_volume_mm3=target.voxel_volume_mm3)`` plus
    ``target`` (name) and ``provenance``."""
    d = np.asarray(doses, dtype=float).reshape(-1)
    if d.shape[0] != target.n:
        raise ValueError("doses has %d entries, target %s has %d sample points"
                         % (d.shape[0], target.name, target.n))
    out: Dict[str, Any] = dict(_stats(d, float(rx_cgy), weights=target.weights,
                                      voxel_volume_mm3=target.voxel_volume_mm3))
    out["target"] = target.name
    out["provenance"] = dict(target.provenance)
    return out
