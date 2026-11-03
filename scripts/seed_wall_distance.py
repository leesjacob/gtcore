"""Signed seed-to-surface distance: does the 3 mm seed-plane model hold?

Phase 3 task ``seed-wall`` (requirements rev 3, §3 F8 "FIX the 3 mm offset
contradiction").  The tile model places the seed plane
``gtcore.geometry.SEED_PLANE_OFFSET_MM`` = 3.0 mm from the tile's tissue face
(hydrated tiles 2.25-3.75 mm), so a seed should sit about 3 mm off the cavity
wall, inside the lumen.  This script measures where the seeds actually sit:

* clinical case (``PT-CT1mm`` + ``PT-RT``): the gtcore seeds
  (``gtcore.pipeline.reconstruct``) and the TPS source positions (RTDOSE local
  maxima, the rule of ``scripts/validation_clinical.py``) against (a) the
  physician's ``Cav_Post`` contour rasterised on the CT grid
  (``gtcore.io.rt.rasterize_roi``) and (b) gtcore's own cavity mask;
* printed phantom (``PH8`` + ``PH0``): the 32 PH8 seeds against (i) the PH8
  ``body`` mask (gtcore's, which includes the collagen tiles glued to the
  wall) and (ii) the tile-less twin ``PH0``'s printed wall after a rigid
  registration PH0 -> PH8 (SimpleITK, Mattes mutual information).

Sign convention, identical for every reference: **positive = the seed centre
lies on the cavity side of the surface** (inside the contoured / segmented
cavity; in the lumen, away from the printed plastic), **negative = inside the
tissue / plastic / segmented solid**.  The model predicts +3.0 mm.

Primary estimator: the signed Euclidean distance transform of the mask
(``scipy.ndimage.distance_transform_edt`` in physical mm, inside minus
outside), computed on the image grid after linear through-plane upsampling of
the binary mask to <= ``FINE_MM`` (anti-aliasing of the slice quantisation:
the 0.5-level of the linearly interpolated indicator is the same mid-point
surface marching cubes draws), sampled trilinearly at the seed centres.
Cross-check: gtcore's own surface mesh of the same mask
(``gtcore.segment.surface.mask_to_mesh``, Taubin-smoothed marching cubes) and
``trimesh.proximity.signed_distance``.  Both are unit-tested on a digital
sphere (``tests/test_seed_wall_distance.py``).

Usage (paths are CLI arguments; never commit them)::

    python scripts/seed_wall_distance.py \
        --ct "$GT_DATA_ROOT/<PT-CT1mm>" --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT>" \
        --rtdose "$GT_DATA_ROOT/<PT-RT RTDOSE>" \
        --ph8 "$GT_DATA_ROOT/<PH8>" --ph0 "$GT_DATA_ROOT/<PH0>" \
        [--out docs/figures/data]

Writes ``seed_wall_distance.csv`` (one row per seed x reference surface),
``seed_wall_summary.csv`` and ``seed_wall_histogram.csv`` with header comments
(script, command with dataset ids, commit, parameters).  ``SEED_PLANE_OFFSET_MM``
is measured against, never re-tuned, here.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import os
import subprocess
import sys
import time
from typing import Dict, List, Sequence, Tuple

import numpy as np
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gtcore                                                       # noqa: E402
from gtcore import geometry as _geom                                 # noqa: E402
from gtcore.segment._morph import largest_cc                         # noqa: E402
from gtcore.segment.head import AIR_BODY_HU                          # noqa: E402
from gtcore.segment.surface import mask_to_mesh                      # noqa: E402
from gtcore.volume import Volume, apply_affine                       # noqa: E402

FINE_MM = 0.5             # anti-aliasing target spacing for the signed EDT
CROP_MARGIN_MM = 15.0     # the field is exact for |distance| < this margin
MODEL_MM = float(_geom.SEED_PLANE_OFFSET_MM)                 # 3.0
BAND_MM = tuple(float(x) for x in _geom.SEED_PLANE_OFFSET_RANGE_MM)  # (2.25, 3.75)
HIST_EDGES_MM = np.round(np.arange(-6.0, 12.0 + 1e-9, 0.5), 2)
RTDOSE_SOURCE_GY = 200.0  # validation_clinical.py rule: maxima above this ...
RTDOSE_MERGE_MM = 3.0     # ... merged within this radius
RAS_TO_LPS = np.diag([-1.0, -1.0, 1.0, 1.0])
_FLIP = np.array([-1.0, -1.0, 1.0])


# ----------------------------------------------------------------- geometry
def _spacing(affine) -> np.ndarray:
    """Per-axis voxel spacing (si, sj, sk) in mm of a (4, 4) affine."""
    return np.linalg.norm(np.asarray(affine, float)[:3, :3], axis=0)


def crop_mask(mask, affine, margin_mm: float, points_ras=None):
    """Crop ``mask`` to its bounding box (union with ``points_ras``) plus
    ``margin_mm``; returns ``(sub_mask, sub_affine, (lo_kji, hi_kji))``.  The
    affine is adjusted so voxel (0, 0, 0) of the crop keeps its RAS position
    (works for any direction cosines, flipped axes included)."""
    mask = np.asarray(mask, bool)
    affine = np.asarray(affine, float)
    if not mask.any():
        raise ValueError("crop_mask: empty mask")
    kji = np.argwhere(mask)
    lo, hi = kji.min(axis=0).astype(float), kji.max(axis=0).astype(float)
    if points_ras is not None and len(points_ras):
        ijk = apply_affine(np.linalg.inv(affine), np.atleast_2d(points_ras))
        lo = np.minimum(lo, np.floor(ijk[:, ::-1]).min(axis=0))
        hi = np.maximum(hi, np.ceil(ijk[:, ::-1]).max(axis=0))
    pad = np.ceil(margin_mm / _spacing(affine)[::-1])
    lo = np.maximum(lo - pad, 0).astype(int)
    hi = np.minimum(hi + pad, np.asarray(mask.shape) - 1).astype(int)
    sub = mask[lo[0]:hi[0] + 1, lo[1]:hi[1] + 1, lo[2]:hi[2] + 1]
    A = affine.copy()
    A[:3, 3] = apply_affine(affine, np.array([lo[2], lo[1], lo[0]], float))
    return sub, A, (lo, hi)


def upsample_mask(mask, affine, fine_mm: float = FINE_MM):
    """Linearly upsample a binary mask by integer factors so every spacing is
    <= about ``fine_mm`` (edge-aligned, ``grid_mode=True``), threshold the
    interpolated indicator at 0.5.  Returns ``(fine_mask, fine_affine, factors_kji)``."""
    mask = np.asarray(mask, bool)
    affine = np.asarray(affine, float)
    sp_ijk = _spacing(affine)
    f_ijk = np.maximum(1, np.round(sp_ijk / float(fine_mm))).astype(int)
    f_kji = f_ijk[::-1]
    if np.all(f_kji == 1):
        return mask.copy(), affine.copy(), f_kji
    fine = ndimage.zoom(mask.astype(np.float32), zoom=tuple(float(f) for f in f_kji),
                        order=1, grid_mode=True, mode="nearest")
    A = affine.copy()
    A[:3, :3] = affine[:3, :3] / f_ijk[None, :]
    # fine index m <-> coarse index (m + 0.5) / f - 0.5  => fine voxel 0 sits at coarse (0.5 / f - 0.5)
    A[:3, 3] = apply_affine(affine, 0.5 / f_ijk - 0.5)
    return fine >= 0.5, A, f_kji


class SignedDistanceField:
    """Signed Euclidean distance to the surface of a voxel mask.

    ``solid=False``: the mask is the cavity (hollow); positive inside it.
    ``solid=True``: the mask is the solid (plastic / body); positive outside
    it, i.e. still "on the cavity side".  The field is computed on a crop
    around the mask (and the query points) with ``crop_margin_mm`` of
    context, after through-plane anti-aliasing to ``fine_mm``; ``sample``
    interpolates it trilinearly.
    """

    def __init__(self, mask, affine, solid: bool = False, fine_mm: float = FINE_MM,
                 crop_margin_mm: float = CROP_MARGIN_MM, points_ras=None):
        t0 = time.time()
        sub, A, (lo, hi) = crop_mask(mask, affine, crop_margin_mm, points_ras)
        fine, Af, factors = upsample_mask(sub, A, fine_mm)
        sp_kji = _spacing(Af)[::-1]
        d_in = ndimage.distance_transform_edt(fine, sampling=sp_kji)
        d_out = ndimage.distance_transform_edt(~fine, sampling=sp_kji)
        field = (d_in - d_out).astype(np.float32)
        self.field = -field if solid else field
        self.affine = Af
        self.inv_affine = np.linalg.inv(Af)
        self.solid = bool(solid)
        self.factors_kji = tuple(int(f) for f in factors)
        self.spacing_fine_mm = tuple(float(s) for s in _spacing(Af))
        self.crop_kji = (tuple(int(x) for x in lo), tuple(int(x) for x in hi))
        self.shape_fine = tuple(int(n) for n in self.field.shape)
        self.seconds = time.time() - t0

    def sample(self, points_ras) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(points_ras, float))
        ijk = apply_affine(self.inv_affine, pts)
        return ndimage.map_coordinates(self.field, ijk[:, ::-1].T, order=1, mode="nearest")

    def inside_crop(self, points_ras) -> np.ndarray:
        pts = np.atleast_2d(np.asarray(points_ras, float))
        ijk = apply_affine(self.inv_affine, pts)
        hi = np.asarray(self.shape_fine)[::-1] - 1
        return np.all((ijk >= 0) & (ijk <= hi), axis=1)


def signed_distance_edt(mask, affine, points_ras, solid: bool = False, **kw) -> np.ndarray:
    """Convenience: build the field and sample it once."""
    return SignedDistanceField(mask, affine, solid=solid, points_ras=points_ras, **kw).sample(points_ras)


def surface_mesh(mask, affine, smooth_iterations: int = 10, crop_margin_mm: float = CROP_MARGIN_MM,
                 points_ras=None):
    """gtcore's surface of a mask (``mask_to_mesh``: marching cubes at 0.5,
    Taubin smoothing, largest component), built on a crop for speed."""
    sub, A, _ = crop_mask(mask, affine, crop_margin_mm, points_ras)
    return mask_to_mesh(sub, A, smooth_iterations=smooth_iterations, largest_only=True, step_size=1)


def signed_distance_mesh(mesh, points_ras, solid: bool = False) -> np.ndarray:
    """``trimesh.proximity.signed_distance`` (positive inside the mesh), with
    the sign flipped for a solid so positive is again the cavity side."""
    import trimesh
    pts = np.atleast_2d(np.asarray(points_ras, float))
    d = np.asarray(trimesh.proximity.signed_distance(mesh, pts), float)
    return -d if solid else d


# --------------------------------------------------------------- seed sets
def tps_sources_from_rtdose(dose: Volume, source_gy: float = RTDOSE_SOURCE_GY,
                            merge_mm: float = RTDOSE_MERGE_MM) -> np.ndarray:
    """Plan source positions as the RTDOSE local maxima above ``source_gy``
    (3 x 3 x 3 neighbourhood), merged within ``merge_mm`` -- the rule of
    ``scripts/validation_clinical.py::print_seed_match_detail``.  Positions are
    dose-grid voxel centres (1 x 1 x 1.667 mm on PT-RT)."""
    arr = np.asarray(dose.array, float)
    peaks = (arr == ndimage.maximum_filter(arr, size=3)) & (arr > source_gy * 100.0)
    pk = np.argwhere(peaks)
    if not len(pk):
        return np.zeros((0, 3))
    pk = pk[np.argsort(-arr[tuple(pk.T)])]
    src: List[np.ndarray] = []
    for p in dose.index_to_ras(pk[:, ::-1].astype(float)):
        if not src or np.min(np.linalg.norm(np.asarray(src) - p, axis=1)) > merge_mm:
            src.append(p)
    return np.asarray(src)


def match_one_to_one(a, b) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hungarian match of point sets ``a`` and ``b``: ``(ia, ib, dist)``."""
    from scipy.optimize import linear_sum_assignment
    a = np.atleast_2d(a)
    b = np.atleast_2d(b)
    D = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    ia, ib = linear_sum_assignment(D)
    return ia, ib, D[ia, ib]


# ------------------------------------------------------------ phantom twin
def body_mask_from_hu(arr, threshold_hu: float = AIR_BODY_HU) -> np.ndarray:
    """gtcore's body rule (``segment_head``): largest component above the
    air/body threshold, holes filled."""
    body = largest_cc(np.asarray(arr, np.float32) > float(threshold_hu))
    return ndimage.binary_fill_holes(body)


def to_sitk(arr_kji, affine):
    """A SimpleITK image (LPS) with exactly the geometry of a gtcore affine."""
    import SimpleITK as sitk
    img = sitk.GetImageFromArray(np.ascontiguousarray(arr_kji))
    A = RAS_TO_LPS @ np.asarray(affine, float)
    sp = np.linalg.norm(A[:3, :3], axis=0)
    D = A[:3, :3] / sp[None, :]
    img.SetSpacing(tuple(float(s) for s in sp))
    img.SetOrigin(tuple(float(o) for o in A[:3, 3]))
    img.SetDirection(tuple(float(x) for x in D.ravel()))
    return img


def euler_as_matrix(tx) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(R, c, t)`` of a SimpleITK Euler3DTransform: ``y = R (x - c) + c + t`` (LPS)."""
    R = np.asarray(tx.GetMatrix(), float).reshape(3, 3)
    return R, np.asarray(tx.GetCenter(), float), np.asarray(tx.GetTranslation(), float)


def transform_points_ras(tx, points_ras, inverse: bool = False) -> np.ndarray:
    """Apply a SimpleITK Euler3D transform (fixed -> moving, LPS) to RAS
    points; ``inverse=True`` maps moving -> fixed."""
    R, c, t = euler_as_matrix(tx)
    p = np.atleast_2d(np.asarray(points_ras, float)) * _FLIP
    if inverse:
        q = (p - c - t) @ R + c          # R orthonormal: R^-1 = R^T
    else:
        q = (p - c) @ R.T + c + t
    return q * _FLIP


def _centroid_ras(mask, affine) -> np.ndarray:
    kji = np.argwhere(mask).mean(axis=0)
    return apply_affine(affine, kji[::-1])


def _crop_volume(vol: Volume, mask, margin_mm: float):
    sub_mask, A, (lo, hi) = crop_mask(mask, vol.affine, margin_mm)
    sub = np.asarray(vol.array)[lo[0]:hi[0] + 1, lo[1]:hi[1] + 1, lo[2]:hi[2] + 1]
    return Volume(sub, A, dict(vol.meta)), sub_mask


def register_rigid(fixed: Volume, fixed_mask, moving: Volume, moving_mask,
                   margin_mm: float = 10.0, hu_range=(-1000.0, 500.0), bins: int = 50,
                   sampling: float = 0.25, seed: int = 1234, verbose: bool = False):
    """Rigid (Euler3D) registration moving -> fixed with SimpleITK: Mattes
    mutual information on HU clamped to ``hu_range``, random sampling
    (seeded), three resolution levels, regular-step gradient descent, centred
    on the fixed mask's centroid and initialised by the mask centroids.
    Returns ``(transform, info)``; the transform maps fixed physical points to
    moving physical points (SimpleITK's resampling convention).  ``info``
    carries the final metric, the Euler angles / translation, the Dice of the
    resampled moving mask against the fixed one and the stop condition."""
    import SimpleITK as sitk
    t0 = time.time()
    f_vol, f_mask = _crop_volume(fixed, fixed_mask, margin_mm)
    m_vol, m_mask = _crop_volume(moving, moving_mask, margin_mm)
    lo, hi = float(hu_range[0]), float(hu_range[1])
    fi = to_sitk(np.clip(np.asarray(f_vol.array, np.float32), lo, hi), f_vol.affine)
    mi = to_sitk(np.clip(np.asarray(m_vol.array, np.float32), lo, hi), m_vol.affine)
    cf = _centroid_ras(f_mask, f_vol.affine) * _FLIP
    cm = _centroid_ras(m_mask, m_vol.affine) * _FLIP
    tx = sitk.Euler3DTransform()
    tx.SetCenter(tuple(float(x) for x in cf))
    tx.SetTranslation(tuple(float(x) for x in (cm - cf)))
    reg = sitk.ImageRegistrationMethod()
    reg.SetMetricAsMattesMutualInformation(numberOfHistogramBins=int(bins))
    reg.SetMetricSamplingStrategy(reg.RANDOM)
    reg.SetMetricSamplingPercentage(float(sampling), int(seed))
    reg.SetInterpolator(sitk.sitkLinear)
    reg.SetOptimizerAsRegularStepGradientDescent(learningRate=1.0, minStep=1e-4,
                                                 numberOfIterations=300, relaxationFactor=0.5,
                                                 gradientMagnitudeTolerance=1e-6)
    reg.SetOptimizerScalesFromPhysicalShift()
    reg.SetShrinkFactorsPerLevel(shrinkFactors=[4, 2, 1])
    reg.SetSmoothingSigmasPerLevel(smoothingSigmas=[2.0, 1.0, 0.0])
    reg.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()
    reg.SetInitialTransform(tx, inPlace=True)
    if verbose:
        reg.AddCommand(sitk.sitkIterationEvent,
                       lambda: print("    it %3d  metric %.5f" % (reg.GetOptimizerIteration(), reg.GetMetricValue())))
    reg.Execute(fi, mi)
    # Dice of the moving mask carried into the fixed grid
    mm = to_sitk(np.asarray(m_mask, np.uint8), m_vol.affine)
    carried = sitk.Resample(mm, fi, tx, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
    carried = sitk.GetArrayFromImage(carried).astype(bool)
    dice = 2.0 * float((carried & f_mask).sum()) / max(1.0, float(carried.sum() + f_mask.sum()))
    ang = np.degrees(np.asarray(tx.GetParameters()[:3], float))
    tr = np.asarray(tx.GetParameters()[3:6], float)
    info = {
        "metric_final": float(reg.GetMetricValue()),
        "stop_condition": str(reg.GetOptimizerStopConditionDescription()),
        "iterations": int(reg.GetOptimizerIteration()),
        "euler_angles_deg": [float(a) for a in ang],
        "translation_lps_mm": [float(v) for v in tr],
        "initial_translation_lps_mm": [float(v) for v in (cm - cf)],
        "dice_masks": float(dice),
        "fixed_crop_shape": [int(n) for n in f_vol.array.shape],
        "moving_crop_shape": [int(n) for n in m_vol.array.shape],
        "seconds": float(time.time() - t0),
    }
    return tx, info


def surface_points(mesh, max_points: int = 60000, seed: int = 0) -> np.ndarray:
    """The mesh's vertices (RAS), subsampled to at most ``max_points``."""
    v = np.asarray(mesh.vertices, float)
    if len(v) > max_points:
        v = v[np.random.default_rng(seed).choice(len(v), max_points, replace=False)]
    return v


# ------------------------------------------------------------------ stats
def summarize(d, band=BAND_MM) -> Dict[str, float]:
    d = np.asarray(d, float)
    d = d[np.isfinite(d)]
    if not len(d):
        return {"n": 0}
    lo, hi = band
    in_band = (d >= lo) & (d <= hi)
    return {
        "n": int(len(d)), "median": float(np.median(d)),
        "p10": float(np.percentile(d, 10)), "p90": float(np.percentile(d, 90)),
        "min": float(d.min()), "max": float(d.max()), "mean": float(d.mean()),
        "sd": float(d.std(ddof=1)) if len(d) > 1 else float("nan"),
        "n_in_band": int(in_band.sum()), "frac_in_band": float(in_band.mean()),
        "n_negative": int((d < 0).sum()),
    }


def histogram_rows(d, dataset, seed_set, reference, edges=HIST_EDGES_MM) -> List[dict]:
    d = np.asarray(d, float)
    counts, _ = np.histogram(d, bins=edges)
    rows = []
    for c, lo, hi in zip(counts, edges[:-1], edges[1:]):
        rows.append({"dataset": dataset, "seed_set": seed_set, "reference": reference,
                     "bin_lo_mm": float(lo), "bin_hi_mm": float(hi), "count": int(c)})
    n_lo, n_hi = int((d < edges[0]).sum()), int((d >= edges[-1]).sum())
    if n_lo or n_hi:
        rows.append({"dataset": dataset, "seed_set": seed_set, "reference": reference,
                     "bin_lo_mm": float("-inf"), "bin_hi_mm": float(edges[0]), "count": n_lo})
        rows.append({"dataset": dataset, "seed_set": seed_set, "reference": reference,
                     "bin_lo_mm": float(edges[-1]), "bin_hi_mm": float("inf"), "count": n_hi})
    return rows


def _fmt_stats(s: Dict[str, float]) -> str:
    if not s.get("n"):
        return "n = 0"
    return ("n %d  median %+.2f  10-90 %% %+.2f..%+.2f  min %+.2f  max %+.2f  mean %+.2f  sd %.2f  "
            "in band [%.2f, %.2f] %d/%d  negative %d"
            % (s["n"], s["median"], s["p10"], s["p90"], s["min"], s["max"], s["mean"], s["sd"],
               BAND_MM[0], BAND_MM[1], s["n_in_band"], s["n"], s["n_negative"]))


def _round_stats(s: Dict[str, float]) -> Dict[str, float]:
    return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()}


# ------------------------------------------------------------------ runs
class Collector:
    """Accumulates per-seed rows, summary rows and histogram rows."""

    def __init__(self):
        self.rows: List[dict] = []
        self.summary: List[dict] = []
        self.hist: List[dict] = []
        self.notes: List[str] = []

    def add(self, dataset, seed_set, reference, points_ras, d_edt, d_mesh=None, extra=None, ids=None):
        pts = np.atleast_2d(np.asarray(points_ras, float))
        d_edt = np.asarray(d_edt, float)
        d_mesh = np.full(len(pts), np.nan) if d_mesh is None else np.asarray(d_mesh, float)
        ids = list(range(len(pts))) if ids is None else list(ids)
        for i in range(len(pts)):
            row = {"dataset": dataset, "seed_set": seed_set, "seed_id": int(ids[i]),
                   "x_ras_mm": round(float(pts[i, 0]), 3), "y_ras_mm": round(float(pts[i, 1]), 3),
                   "z_ras_mm": round(float(pts[i, 2]), 3), "reference": reference,
                   "signed_distance_mm": round(float(d_edt[i]), 3),
                   "signed_distance_mesh_mm": round(float(d_mesh[i]), 3),
                   "in_band": int(BAND_MM[0] <= d_edt[i] <= BAND_MM[1])}
            if extra:
                for k, v in extra.items():
                    row[k] = v[i] if np.ndim(v) else v
            self.rows.append(row)
        s = summarize(d_edt)
        s_m = summarize(d_mesh) if np.isfinite(d_mesh).any() else {}
        self.summary.append(dict({"dataset": dataset, "seed_set": seed_set, "reference": reference,
                                  "method": "edt"}, **_round_stats(s)))
        if s_m:
            self.summary.append(dict({"dataset": dataset, "seed_set": seed_set, "reference": reference,
                                      "method": "mesh"}, **_round_stats(s_m)))
        self.hist.extend(histogram_rows(d_edt, dataset, seed_set, reference))
        print("  %-16s %-13s vs %-14s EDT : %s" % (dataset, seed_set, reference, _fmt_stats(s)))
        if s_m:
            print("  %-16s %-13s    %-14s mesh: %s" % ("", "", "", _fmt_stats(s_m)))
        return s


def run_clinical(ct_dir, rtstruct_path, rtdose_path, col: Collector, verbose=False):
    from gtcore.io import load_volume
    from gtcore.io.rt import load_rtdose, load_rtstruct, rasterize_roi
    from gtcore.pipeline import reconstruct

    print("== clinical: PT-CT1mm + PT-RT")
    t0 = time.time()
    vol = load_volume(ct_dir)
    res = reconstruct(vol, verbose=verbose)
    seeds = np.asarray(res.seeds.centers_ras, float)
    vmm3 = float(np.prod(vol.spacing))
    print("  CT %s at %s mm; reconstruct %.0f s: %d seeds, gtcore cavity %.1f cc"
          % (vol.array.shape, np.round(vol.spacing, 3), time.time() - t0, len(seeds),
             res.cavity_mask.sum() * vmm3 / 1000.0))
    rs = load_rtstruct(rtstruct_path)
    dv = load_rtdose(rtdose_path)
    cav = rasterize_roi(rs, "Cav_Post", vol.affine, vol.array.shape)
    print("  Cav_Post on the CT grid: %.2f cc" % (cav.sum() * vmm3 / 1000.0))
    src = tps_sources_from_rtdose(dv)
    ia, ib, dd = match_one_to_one(src, seeds)
    print("  TPS sources: %d RTDOSE maxima > %.0f Gy merged at %.0f mm; one-to-one vs gtcore seeds: "
          "median %.2f, p95 %.2f, max %.2f mm" % (len(src), RTDOSE_SOURCE_GY, RTDOSE_MERGE_MM,
                                                  np.median(dd), np.percentile(dd, 95), dd.max()))
    col.notes.append("clinical: %d gtcore seeds, %d TPS sources (RTDOSE maxima); one-to-one match median "
                     "%.2f / max %.2f mm" % (len(seeds), len(src), np.median(dd), dd.max()))
    # TPS ids follow the matched gtcore seed where the match is within 3 mm
    tps_ids = np.arange(len(src)) + 100
    tps_match = np.full(len(src), -1, int)
    tps_mdist = np.full(len(src), np.nan)
    for a, b, d in zip(ia, ib, dd):
        tps_match[a] = b
        tps_mdist[a] = d
        if d <= 3.0:
            tps_ids[a] = b
    all_pts = np.vstack([seeds, src])
    for ref_name, mask in (("Cav_Post", cav), ("gtcore_cavity", np.asarray(res.cavity_mask, bool))):
        sdf = SignedDistanceField(mask, vol.affine, solid=False, points_ras=all_pts)
        mesh = surface_mesh(mask, vol.affine, points_ras=all_pts)
        print("  [%s] EDT crop %s -> fine %s at %s mm (%.1f s); mesh %d faces"
              % (ref_name, tuple(int(h - l + 1) for l, h in zip(*sdf.crop_kji)), sdf.shape_fine,
                 np.round(sdf.spacing_fine_mm, 3), sdf.seconds, len(mesh.faces)))
        col.add("PT-CT1mm", "gtcore_seeds", ref_name, seeds, sdf.sample(seeds),
                signed_distance_mesh(mesh, seeds))
        col.add("PT-CT1mm+PT-RT", "tps_sources", ref_name, src, sdf.sample(src),
                signed_distance_mesh(mesh, src), ids=tps_ids,
                extra={"matched_gtcore_seed": tps_match, "match_distance_mm": np.round(tps_mdist, 3)})
    return {"n_seeds": int(len(seeds)), "n_sources": int(len(src))}


def run_phantom(ph8_dir, ph0_dir, col: Collector, verbose=False):
    from gtcore.io import load_volume
    from gtcore.pipeline import reconstruct

    print("== phantom: PH8 seeds vs (i) PH8 body (tiles included) and (ii) PH0 printed wall (registered)")
    t0 = time.time()
    v8 = load_volume(ph8_dir)
    res8 = reconstruct(v8, verbose=verbose)
    seeds = np.asarray(res8.seeds.centers_ras, float)
    body8 = np.asarray(res8.masks["body"], bool)
    vmm3 = float(np.prod(v8.spacing))
    print("  PH8 %s at %s mm; reconstruct %.0f s: %d seeds; body %.0f mL"
          % (v8.array.shape, np.round(v8.spacing, 3), time.time() - t0, len(seeds),
             body8.sum() * vmm3 / 1000.0))
    ijk = np.rint(apply_affine(np.linalg.inv(v8.affine), seeds)).astype(int)
    n_in_body = int(body8[ijk[:, 2], ijk[:, 1], ijk[:, 0]].sum())
    print("  seed centres inside the PH8 body mask (i.e. inside a segmented tile): %d / %d"
          % (n_in_body, len(seeds)))
    col.notes.append("PH8: %d seeds; %d centres inside the body mask" % (len(seeds), n_in_body))
    # (i) PH8 body
    sdf8 = SignedDistanceField(body8, v8.affine, solid=True, points_ras=seeds)
    mesh8 = surface_mesh(body8, v8.affine, points_ras=seeds)
    print("  [PH8_body] EDT fine %s at %s mm (%.1f s); mesh %d faces"
          % (sdf8.shape_fine, np.round(sdf8.spacing_fine_mm, 3), sdf8.seconds, len(mesh8.faces)))
    col.add("PH8", "gtcore_seeds", "PH8_body", seeds, sdf8.sample(seeds),
            signed_distance_mesh(mesh8, seeds, solid=True))
    # (ii) PH0 registered
    t1 = time.time()
    v0 = load_volume(ph0_dir)
    arr0 = np.asarray(v0.array, np.float32)
    body0 = body_mask_from_hu(arr0)
    print("  PH0 %s at %s mm; body (largest component > %.0f HU, filled) %.0f mL (load %.1f s)"
          % (v0.array.shape, np.round(v0.spacing, 3), AIR_BODY_HU,
             body0.sum() * float(np.prod(v0.spacing)) / 1000.0, time.time() - t1))
    core0 = ndimage.binary_erosion(body0, iterations=3)
    core8 = ndimage.binary_erosion(body8, iterations=3)
    hu0 = float(np.median(arr0[core0])) if core0.any() else float("nan")
    hu8 = float(np.median(np.asarray(v8.array, np.float32)[core8])) if core8.any() else float("nan")
    print("  plastic HU (median of the 3-voxel-eroded body): PH8 %.0f, PH0 %.0f" % (hu8, hu0))
    tx, info = register_rigid(v8, body8, v0, body0, verbose=verbose)
    print("  rigid PH0 -> PH8 (SimpleITK Mattes MI): %d iterations, metric %.4f, Dice(body masks) %.4f, "
          "angles %s deg, translation %s mm (LPS); %.1f s; %s"
          % (info["iterations"], info["metric_final"], info["dice_masks"],
             np.round(info["euler_angles_deg"], 3), np.round(info["translation_lps_mm"], 2),
             info["seconds"], info["stop_condition"]))
    seeds0 = transform_points_ras(tx, seeds)       # PH8 seeds in PH0 space
    sdf0 = SignedDistanceField(body0, v0.affine, solid=True, points_ras=seeds0)
    mesh0 = surface_mesh(body0, v0.affine, points_ras=seeds0)
    print("  [PH0_wall] EDT fine %s at %s mm (%.1f s); mesh %d faces"
          % (sdf0.shape_fine, np.round(sdf0.spacing_fine_mm, 3), sdf0.seconds, len(mesh0.faces)))
    # registration residual: PH0 wall (the Taubin-smoothed mesh vertices, sub-voxel) carried into PH8
    # vs the PH8 body surface (its anti-aliased signed EDT)
    sp0_in8 = transform_points_ras(tx, surface_points(mesh0), inverse=True)
    ok = sdf8.inside_crop(sp0_in8)
    resid = np.abs(sdf8.sample(sp0_in8[ok]))
    dseed = np.min(np.linalg.norm(sp0_in8[ok][:, None, :] - seeds[None, :, :], axis=2), axis=1)
    far = dseed > 15.0

    def _res(r):
        return {"n": int(len(r)), "median": float(np.median(r)), "p90": float(np.percentile(r, 90)),
                "p95": float(np.percentile(r, 95)), "max": float(r.max())}

    info["residual_all_mm"] = _res(resid)
    info["residual_far_from_seeds_mm"] = _res(resid[far])
    ra, rf = info["residual_all_mm"], info["residual_far_from_seeds_mm"]
    print("  registration residual (PH0 wall points -> PH8 body surface, |distance|): all %d points median %.2f, "
          "p90 %.2f, p95 %.2f mm; > 15 mm from any seed (%d points, no tiles) median %.2f, p90 %.2f, p95 %.2f mm"
          % (ra["n"], ra["median"], ra["p90"], ra["p95"], rf["n"], rf["median"], rf["p90"], rf["p95"]))
    col.notes.append("PH0->PH8 rigid: Dice %.4f, residual median %.2f mm (all) / %.2f mm (>15 mm from seeds), "
                     "angles %s deg, translation %s mm (LPS)"
                     % (info["dice_masks"], ra["median"], rf["median"],
                        np.round(info["euler_angles_deg"], 2).tolist(),
                        np.round(info["translation_lps_mm"], 2).tolist()))
    col.add("PH8+PH0", "gtcore_seeds", "PH0_wall", seeds, sdf0.sample(seeds0),
            signed_distance_mesh(mesh0, seeds0, solid=True))
    # sensitivity: the wall at the air/plastic mid-point threshold instead of gtcore's -300 HU
    thr_mid = 0.5 * (hu0 + (-1000.0))
    body0_mid = body_mask_from_hu(arr0, thr_mid)
    s_mid = summarize(SignedDistanceField(body0_mid, v0.affine, solid=True, points_ras=seeds0).sample(seeds0))
    col.summary.append(dict({"dataset": "PH8+PH0", "seed_set": "gtcore_seeds",
                             "reference": "PH0_wall_mid_threshold(%.0f HU)" % thr_mid, "method": "edt"},
                            **_round_stats(s_mid)))
    print("  sensitivity, PH0 wall at the air/plastic mid-point threshold (%.0f HU): %s"
          % (thr_mid, _fmt_stats(s_mid)))
    info["plastic_hu_median"] = {"PH8": hu8, "PH0": hu0}
    info["mid_threshold_hu"] = float(thr_mid)
    return info


# ---------------------------------------------------------------- output
def git_commit() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5.0)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def write_csv(path, header_lines: Sequence[str], rows: List[dict]):
    fields: List[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(path, "w", newline="") as f:
        for line in header_lines:
            f.write("# " + line + "\n")
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def recorded_command(args) -> str:
    """The command with every path replaced by its dataset id (never a machine path)."""
    parts = ["python scripts/seed_wall_distance.py"]
    if args.ct:
        parts += ['--ct "$GT_DATA_ROOT/<PT-CT1mm>"', '--rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT>"',
                  '--rtdose "$GT_DATA_ROOT/<PT-RT RTDOSE>"']
    if args.ph8:
        parts += ['--ph8 "$GT_DATA_ROOT/<PH8>"', '--ph0 "$GT_DATA_ROOT/<PH0>"']
    return " ".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ct", help="PT-CT1mm DICOM series directory")
    ap.add_argument("--rtstruct", help="PT-RT RTSTRUCT file or directory")
    ap.add_argument("--rtdose", help="PT-RT RTDOSE file or directory")
    ap.add_argument("--ph8", help="PH8 DICOM series directory (printed phantom, 8 tiles)")
    ap.add_argument("--ph0", help="PH0 DICOM series directory (tile-less twin)")
    ap.add_argument("--out", default=os.path.join("docs", "figures", "data"),
                    help="output directory for the CSVs")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if bool(args.ct) != bool(args.rtstruct and args.rtdose):
        ap.error("--ct needs --rtstruct and --rtdose")
    if bool(args.ph8) != bool(args.ph0):
        ap.error("--ph8 needs --ph0")
    if not (args.ct or args.ph8):
        ap.error("nothing to do: give --ct/--rtstruct/--rtdose and/or --ph8/--ph0")

    print("gtcore.__file__ =", gtcore.__file__)
    commit = git_commit()
    t0 = time.time()
    col = Collector()
    datasets = []
    reg_info = None
    if args.ct:
        run_clinical(args.ct, args.rtstruct, args.rtdose, col, verbose=args.verbose)
        datasets += ["PT-CT1mm", "PT-RT"]
    if args.ph8:
        reg_info = run_phantom(args.ph8, args.ph0, col, verbose=args.verbose)
        datasets += ["PH8", "PH0"]

    os.makedirs(args.out, exist_ok=True)
    header = [
        "script: scripts/seed_wall_distance.py",
        "command: " + recorded_command(args),
        "commit: " + commit,
        "date: " + _dt.date.today().isoformat(),
        "datasets: " + ", ".join(datasets),
        "flag: calibration-set (one clinical case; the phantom cavity is the same patient's intra-operative cavity)",
        "sign: positive = seed centre on the cavity side of the surface (inside the contoured/segmented cavity; "
        "in the lumen, away from the printed plastic); negative = inside tissue / plastic / the segmented solid",
        "model: SEED_PLANE_OFFSET_MM = %.2f mm, hydrated band [%.2f, %.2f] mm (gtcore.geometry; measured "
        "against, not re-tuned)" % (MODEL_MM, BAND_MM[0], BAND_MM[1]),
        "method edt: signed Euclidean distance transform (inside - outside, physical mm) of the mask on the image "
        "grid linearly upsampled through-plane to <= %.2f mm and thresholded at 0.5, trilinear at the seed "
        "centres; crop margin %.0f mm" % (FINE_MM, CROP_MARGIN_MM),
        "method mesh: gtcore.segment.surface.mask_to_mesh (marching cubes 0.5, Taubin 10, largest component) + "
        "trimesh.proximity.signed_distance",
        "references: Cav_Post = RTSTRUCT ROI rasterised on the CT grid (gtcore.io.rt.rasterize_roi); "
        "gtcore_cavity = gtcore.pipeline.reconstruct cavity mask (seed-informed: clipped SHEET_TOL_MM = 3 mm "
        "beyond the seed sheet, so this reference is circular and reported for completeness only); PH8_body = "
        "gtcore body mask of PH8 (> %.0f HU, filled; includes the collagen tiles); PH0_wall = body mask of the "
        "tile-less twin PH0, PH8 seeds carried into PH0 by the rigid registration" % AIR_BODY_HU,
        "seed sets: gtcore_seeds = reconstruct().seeds.centers_ras; tps_sources = RTDOSE local maxima > %.0f Gy "
        "merged within %.0f mm (validation_clinical.py rule; dose-grid voxel centres, 1 x 1 x 1.667 mm); "
        "tps seed_id = matched gtcore seed id when the one-to-one match is <= 3 mm, else 100 + index"
        % (RTDOSE_SOURCE_GY, RTDOSE_MERGE_MM),
        "percentiles: numpy linear interpolation; sd: sample (ddof 1)",
    ]
    if reg_info is not None:
        header.append("registration PH0 -> PH8: SimpleITK Euler3D, Mattes MI 50 bins, random sampling 25 %% "
                      "(seed 1234), levels 4/2/1 with 2/1/0 mm smoothing, regular-step gradient descent; final "
                      "metric %.4f, Dice of the body masks %.4f, Euler angles %s deg, translation %s mm (LPS), "
                      "%d iterations, %s"
                      % (reg_info["metric_final"], reg_info["dice_masks"],
                         np.round(reg_info["euler_angles_deg"], 3).tolist(),
                         np.round(reg_info["translation_lps_mm"], 3).tolist(), reg_info["iterations"],
                         reg_info["stop_condition"]))
        ra, rf = reg_info["residual_all_mm"], reg_info["residual_far_from_seeds_mm"]
        header.append("registration residual (|distance| of PH0 wall points carried into PH8 to the PH8 body "
                      "surface): all %d points median %.3f / p90 %.3f / p95 %.3f mm; > 15 mm from any seed "
                      "%d points median %.3f / p90 %.3f / p95 %.3f mm"
                      % (ra["n"], ra["median"], ra["p90"], ra["p95"],
                         rf["n"], rf["median"], rf["p90"], rf["p95"]))
        header.append("plastic HU (median, eroded body): PH8 %.0f, PH0 %.0f; sensitivity threshold %.0f HU"
                      % (reg_info["plastic_hu_median"]["PH8"], reg_info["plastic_hu_median"]["PH0"],
                         reg_info["mid_threshold_hu"]))
    for n in col.notes:
        header.append("note: " + n)
    header.append("runtime: %.0f s total (other processes may have run concurrently)" % (time.time() - t0))
    write_csv(os.path.join(args.out, "seed_wall_distance.csv"), header, col.rows)
    write_csv(os.path.join(args.out, "seed_wall_summary.csv"), header, col.summary)
    write_csv(os.path.join(args.out, "seed_wall_histogram.csv"), header, col.hist)
    print("wrote %s/seed_wall_{distance,summary,histogram}.csv (%d seed rows) in %.0f s"
          % (args.out, len(col.rows), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
