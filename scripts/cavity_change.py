"""The cavity across the loop, one patient (Paper 2 Phase 5, task ``cavity-change``).

F3(a) strip data and the Table 1 / F3-caption numbers, nothing else:

1. **Registration.**  Rigid (SimpleITK Euler3D, Mattes mutual information,
   three resolution levels; ``gtcore.preprocess.register.register_rigid``,
   CT-MR path) of ``PT-preMRI``, ``PT-intraMRI`` and ``PT-postMRI`` onto
   ``PT-CT1mm``, in two stages: (1) the whole head (metric over the CT body
   crop, started on the mask centroids); (2) from that pose, the metric
   restricted to the cranium (gtcore's skull + cranial interior on the CT,
   grown 5 mm) -- the scalp retracted at surgery, the neck and the jaw do not
   move with the skull, and on ``PT-intraMRI`` stage 1 alone wanders (it
   stops on the iteration cap at poses 8 deg apart) while stage 2 lands on
   one pose from either.  Stage 2 is repeated with a second sampling seed
   (stability).  Each registration is recorded with both stages' metrics,
   the stability, the Dice of the head masks (CT body vs. MR foreground,
   also inside the common field of view) and the MR intensity inside CT bone
   relative to CT brain (same patient + aligned -> clearly below 1).
2. **Strip (F3a).**  The axial ``PT-CT1mm`` slice through the implant centroid
   (the slice of ``clinical_slice.npz``, shared with F4(a) / F5(a) / F6; the
   same rule is applied when that file is absent) is resampled identically
   (trilinear, through the registration, at the CT voxel centres) from each
   registered MRI; one square crop centred on ``Cav_Post`` serves all four
   panels.  The ``PH8`` phantom slice through the phantom's implant centroid
   (same rule, phantom in its own frame) is exported with its own crop centred
   on the phantom cavity.  -> ``f3a_scans_slice.npz`` + ``.json`` index, one
   PNG preview per panel.  The MRIs are shown, not segmented (stop list); the
   only overlay on them is a manual contour when ``--contours`` gives one.
3. **Volumes.**  The cavity at print time = gtcore's cavity rule on the
   registered tile-less twin (``gtcore.segment.cavity.phantom_wall_and_cavity``,
   Phase 4), ``Cav_Post`` from the RTSTRUCT rasterised on the CT grid, gtcore's
   cavity on ``PT-CT1mm`` -> ``f3_cavity_volumes.csv`` with the rule, grid,
   commit and flag per row.
4. **Wall displacement intra-op -> post-op.**  The tile-less print ``PH0`` is
   the intra-operative cavity geometry.  It is rigidly registered to
   ``PT-intraMRI`` (CT-MR MI; the phantom frame is arbitrary, so the
   registration is multi-started over the 24 cube orientations about the
   printed cavity's centroid, initialised on the ``Cav_Post`` centroid carried
   into the MRI; two metric regions: the whole print with a 3 mm rim, and
   only the printed wall region within 10 mm of the lumen) and taken through ``PT-intraMRI -> PT-CT1mm`` into the patient
   frame.  The displacement is the two-way surface distance between the carried
   printed wall and the ``Cav_Post`` surface (median / 95th percentile, both
   directions and symmetric; signed one way: positive = printed wall outside the
   post-operative cavity), and the volume ratio.  It is reported only when the
   registration is **credible** by three criteria fixed before the run:
   (a) consensus -- the best two refined starts agree within 3 mm / 5 deg;
   (b) contrast -- in the MRI the carried lumen's median intensity differs from
   the carried plastic's by more than 25 % (the cavity is not brain-like);
   (c) proximity -- the carried wall's median distance to ``Cav_Post`` is at
   most 10 mm.  Otherwise the CSV says why and the numbers are not quoted.
   -> ``f3_wall_displacement.csv``.

``--contours``: manual cavity contours drawn by the physicist on the MRIs
(no automatic MRI segmentation exists in gtcore).  Per scan either an RTSTRUCT
(DICOM, drawn on that MRI series; ROI ``--contour-roi``, default ``Cavity``)
or a label image (NIfTI / NRRD / MHA, non-zero = cavity; a grid other than the
MRI's is resampled nearest-neighbour onto it).  With a contour the scan gets a
cavity-volume row on its own grid, its outline in the strip, and the wall
displacement to ``Cav_Post`` (two-way surface distance of the carried contour)
instead of the phantom-derived one.

Usage (paths are CLI arguments; never commit them)::

    python scripts/cavity_change.py --ct "$GT_DATA_ROOT/<PT-CT1mm>" \\
        --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT>" \\
        --pre "$GT_DATA_ROOT/<PT-preMRI>" --intra "$GT_DATA_ROOT/<PT-intraMRI>" \\
        --post "$GT_DATA_ROOT/<PT-postMRI>" \\
        --ph8 "$GT_DATA_ROOT/<PH8>" --ph0 "$GT_DATA_ROOT/<PH0>" \\
        [--clinical-slice docs/figures/data/clinical_slice.npz] [--crop-mm 110] \\
        [--contours pre=<file> intra=<file> post=<file>] [--contour-roi Cavity] \\
        [--skip-phantom-to-patient] [--out docs/figures/data] [--previews output/figures/cavity_change]

Nothing is tuned here; every constant is a Phase 3 / Phase 4 registration or
cavity-rule parameter (``docs/paper2-phase-notes.md``).
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import subprocess
import sys
import time
import warnings
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gtcore                                                       # noqa: E402
from gtcore.preprocess.register import (                            # noqa: E402
    _FLIP, compose_points_ras, register_rigid, resample_mask_into_fixed, to_sitk,
    transform_points_ras)
from gtcore.segment._morph import largest_cc                        # noqa: E402
from gtcore.segment.head import AIR_BODY_HU                         # noqa: E402
from gtcore.segment.surface import mask_to_mesh                     # noqa: E402
from gtcore.volume import Volume, apply_affine                      # noqa: E402

SCAN_KEYS = ("pre", "intra", "post")
SCAN_IDS = {"pre": "PT-preMRI", "intra": "PT-intraMRI", "post": "PT-postMRI"}
CROP_MM = 110.0                         # square crop centred on the cavity (v6/v7 F3a mockup)
WL_CT = (400.0, 40.0)                   # soft-tissue window / level, HU (style bar)
WL_PHANTOM = (1000.0, 0.0)              # phantom slice window / level, HU (style bar)
MR_WINDOW_PERCENTILES = (1.0, 99.0)     # MR display window: percentiles inside the head on that slice
NEAR_SLICE_MM = 3.0                     # seeds within this of the slice plane are "on" it
CONSENSUS_MM, CONSENSUS_DEG = 3.0, 5.0  # credibility (a): best two starts agree
CONTRAST_MIN = 0.25                     # credibility (b): |lumen - plastic| / plastic in the MRI
PROXIMITY_MAX_MM = 10.0                 # credibility (c): median wall -> Cav_Post distance
N_REFINE = 4                            # multi-start: coarse starts refined at full schedule
CRANIUM_MARGIN_MM = 5.0                 # MR -> CT stage 2: metric on skull + cranial interior grown by this
STABILITY_SEED = 99                     # second sampling seed of the stage-2 stability check
PHANTOM_FOCUS_VARIANTS = (None, 10.0)   # PH0 -> MRI metric: whole print + rim, or the wall region only (mm)
MAX_SURFACE_POINTS = 20000


# ----------------------------------------------------------------- helpers
def gtcore_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=os.path.dirname(gtcore.__file__),
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def otsu_threshold(values, nbins: int = 256) -> float:
    """Otsu's threshold of a 1-D sample (between-class variance maximum)."""
    v = np.asarray(values, np.float64).ravel()
    h, edges = np.histogram(v, bins=nbins)
    c = 0.5 * (edges[:-1] + edges[1:])
    h = h.astype(np.float64)
    w0 = np.cumsum(h)
    w1 = w0[-1] - w0
    s0 = np.cumsum(h * c)
    m0 = s0 / np.maximum(w0, 1e-12)
    m1 = (s0[-1] - s0) / np.maximum(w1, 1e-12)
    var = w0 * w1 * (m0 - m1) ** 2
    return float(c[int(np.argmax(var))])


def head_mask_mr(vol: Volume, sigma_vox: float = 1.0) -> Tuple[np.ndarray, float]:
    """MR foreground (the head) for the registration crop / centroid / Dice:
    Otsu on the lightly smoothed image, largest component, holes filled.
    A head mask, not an anatomical segmentation."""
    a = ndimage.gaussian_filter(np.asarray(vol.array, np.float32), float(sigma_vox))
    pos = a[a > 0]
    thr = otsu_threshold(pos if pos.size else a)
    m = largest_cc(a > thr)
    return ndimage.binary_fill_holes(m), thr


def body_mask_ct(vol: Volume) -> np.ndarray:
    """gtcore's body rule (``segment_head``): largest component above the
    air threshold, holes filled."""
    return ndimage.binary_fill_holes(largest_cc(np.asarray(vol.array, np.float32) > AIR_BODY_HU))


def cube_rotations() -> List[np.ndarray]:
    """The 24 proper rotations of the cube (signed permutation matrices with
    determinant +1), identity first."""
    out = []
    for perm in ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0)):
        for sx in (1, -1):
            for sy in (1, -1):
                for sz in (1, -1):
                    R = np.zeros((3, 3))
                    for row, (col, s) in enumerate(zip(perm, (sx, sy, sz))):
                        R[row, col] = s
                    if np.linalg.det(R) > 0:
                        out.append(R)
    out.sort(key=lambda R: -np.trace(R))
    return out


def implant_slice_index(vol: Volume, centers_ras) -> Tuple[int, float, np.ndarray]:
    """Axial slice (k) through the implant centroid and its RAS z (the rule
    of ``scripts/validation_clinical.py``)."""
    c = np.asarray(centers_ras, float).reshape(-1, 3).mean(axis=0)
    k = int(np.clip(np.rint(vol.ras_to_index(c)[2]), 0, vol.array.shape[0] - 1))
    z = float(vol.index_to_ras(np.array([0.0, 0.0, float(k)]))[2])
    return k, z, c


def slice_points(vol: Volume, k: int):
    """``(x_ras, y_ras, pts)``: 2-D arrays ``(nj, ni)`` of the voxel-centre
    RAS x / y of slice ``k`` and the flat ``(nj * ni, 3)`` point list."""
    nk, nj, ni = vol.array.shape
    jj, ii = np.meshgrid(np.arange(nj), np.arange(ni), indexing="ij")
    ijk = np.stack([ii.ravel(), jj.ravel(), np.full(ii.size, float(k))], axis=1).astype(float)
    pts = vol.index_to_ras(ijk)
    return pts[:, 0].reshape(nj, ni), pts[:, 1].reshape(nj, ni), pts


def sample_through(moving: Volume, tx, pts_fixed_ras, order: int = 1, fill: float = 0.0) -> np.ndarray:
    """Values of ``moving`` at the FIXED-frame RAS points ``pts_fixed_ras``
    carried through ``tx`` (a :func:`register_rigid` transform, fixed ->
    moving); trilinear by default.  ``tx=None`` samples in place."""
    p = np.atleast_2d(np.asarray(pts_fixed_ras, float))
    if tx is not None:
        p = transform_points_ras(tx, p)
    return np.asarray(moving.sample_ras(p, order=order, fill=fill), np.float32)


def square_crop(vol: Volume, k: int, center_ras_xy, size_mm: float):
    """Index bounds ``(i0, i1, j0, j1)`` (half-open) of a square crop of
    ``size_mm`` on slice ``k``, centred on the RAS (x, y) ``center_ras_xy``,
    clipped to the slice; and the crop's voxel-centre extent
    ``(x_min, x_max, y_min, y_max)`` in RAS mm."""
    nk, nj, ni = vol.array.shape
    c = np.array([float(center_ras_xy[0]), float(center_ras_xy[1]),
                  float(vol.index_to_ras(np.array([0.0, 0.0, float(k)]))[2])])
    cij = vol.ras_to_index(c)
    si, sj = vol.spacing[0], vol.spacing[1]
    hi_, hj_ = int(np.rint(0.5 * size_mm / si)), int(np.rint(0.5 * size_mm / sj))
    ci, cj = int(np.rint(cij[0])), int(np.rint(cij[1]))
    i0, i1 = max(0, ci - hi_), min(ni, ci + hi_)
    j0, j1 = max(0, cj - hj_), min(nj, cj + hj_)
    corners = vol.index_to_ras(np.array([[i0, j0, k], [i1 - 1, j1 - 1, k]], float))
    ext = (float(min(corners[:, 0])), float(max(corners[:, 0])),
           float(min(corners[:, 1])), float(max(corners[:, 1])))
    return (i0, i1, j0, j1), ext


def mask_centroid_xy(mask2d, x_ras, y_ras):
    m = np.asarray(mask2d, bool)
    if not m.any():
        return None
    return float(x_ras[m].mean()), float(y_ras[m].mean())


def window_from_percentiles(img2d, mask2d=None, pct=MR_WINDOW_PERCENTILES) -> Tuple[float, float]:
    """``(window, level)`` from the percentiles of the pixels in ``mask2d``."""
    v = np.asarray(img2d, np.float32)
    if mask2d is not None and np.asarray(mask2d, bool).any():
        v = v[np.asarray(mask2d, bool)]
    v = v[np.isfinite(v)]
    lo, hi = (np.percentile(v, pct) if v.size else (0.0, 1.0))
    return float(hi - lo), float(0.5 * (hi + lo))


def _surface_points(mesh, n_max: int = MAX_SURFACE_POINTS, seed: int = 0) -> np.ndarray:
    v = np.asarray(mesh.vertices, float)
    if len(v) > n_max:
        v = v[np.random.default_rng(seed).choice(len(v), n_max, replace=False)]
    return v


def surface_distance_two_way(mesh_a, mesh_b) -> Dict[str, float]:
    """Unsigned closest-point distances: vertices of ``a`` to surface ``b``
    and of ``b`` to surface ``a`` (``trimesh.proximity``), each as median /
    95th percentile / max, and the symmetric (pooled) median / 95th."""
    import trimesh
    pa, pb = _surface_points(mesh_a), _surface_points(mesh_b)
    _c, d_ab, _t = trimesh.proximity.ProximityQuery(mesh_b).on_surface(pa)
    _c, d_ba, _t = trimesh.proximity.ProximityQuery(mesh_a).on_surface(pb)
    pooled = np.concatenate([d_ab, d_ba])
    return {"a_to_b_median_mm": float(np.median(d_ab)), "a_to_b_p95_mm": float(np.percentile(d_ab, 95)),
            "a_to_b_max_mm": float(d_ab.max()),
            "b_to_a_median_mm": float(np.median(d_ba)), "b_to_a_p95_mm": float(np.percentile(d_ba, 95)),
            "b_to_a_max_mm": float(d_ba.max()),
            "symmetric_median_mm": float(np.median(pooled)), "symmetric_p95_mm": float(np.percentile(pooled, 95)),
            "n_a": int(len(pa)), "n_b": int(len(pb))}


def mesh_of(mask, affine, margin_mm: float = 10.0):
    """gtcore's surface of a mask (``mask_to_mesh``: marching cubes, Taubin
    10, largest component) built on the mask's bounding box plus a margin."""
    from gtcore.preprocess.register import crop_mask
    sub, A, _ = crop_mask(mask, affine, margin_mm)
    return mask_to_mesh(sub, A)


def signed_distance_to_mask(mask, affine, points_ras, margin_mm: float = 15.0) -> np.ndarray:
    """Signed Euclidean distance of RAS points to the surface of a cavity
    mask on its own grid: **positive outside** the mask, negative inside
    (EDT inside / outside in physical mm on a crop, trilinear; the raw grid,
    so the through-plane quantisation is half a slice)."""
    from gtcore.preprocess.register import crop_mask
    sub, A, _ = crop_mask(mask, affine, margin_mm, points_ras)
    sp = np.linalg.norm(A[:3, :3], axis=0)[::-1]
    field = (ndimage.distance_transform_edt(~sub, sampling=sp)
             - ndimage.distance_transform_edt(sub, sampling=sp)).astype(np.float32)
    return np.asarray(Volume(field, A).sample_ras(np.atleast_2d(points_ras), order=1, fill=np.nan), float)


def pose_difference(tx_a, tx_b) -> Tuple[float, float]:
    """``(translation_mm, angle_deg)`` between two Euler3D transforms, as the
    rigid map ``b^-1 a``: translation = how far a point at the centre moves."""
    Ra = np.asarray(tx_a.GetMatrix(), float).reshape(3, 3)
    Rb = np.asarray(tx_b.GetMatrix(), float).reshape(3, 3)
    c = np.asarray(tx_a.GetCenter(), float)
    pa = np.asarray(tx_a.TransformPoint(tuple(c)), float)
    pb = np.asarray(tx_b.TransformPoint(tuple(c)), float)
    Rrel = Rb.T @ Ra
    ang = float(np.degrees(np.arccos(np.clip((np.trace(Rrel) - 1.0) / 2.0, -1.0, 1.0))))
    return float(np.linalg.norm(pa - pb)), ang


# ------------------------------------------------------------- registration
def _stop_text(stop: str) -> str:
    """The optimiser's stop condition without its class-name prefix."""
    head, sep, tail = str(stop).partition(": ")
    return (tail if sep else head).strip()[:90]


def cranium_mask(ct: Volume, masks, margin_mm: float = None) -> np.ndarray:
    """gtcore's skull and cranial interior on the CT (``segment_head``), grown
    by ``margin_mm`` (default ``CRANIUM_MARGIN_MM``): the part of the head
    that moves rigidly between scans (scalp retraction, neck flexion and the
    jaw do not)."""
    margin_mm = CRANIUM_MARGIN_MM if margin_mm is None else float(margin_mm)
    cran = np.asarray(masks["skull"], bool) | np.asarray(masks["cranial_interior"], bool)
    if not cran.any():
        raise ValueError("cranium_mask: empty skull / cranial interior")
    return ndimage.distance_transform_edt(~cran, sampling=np.asarray(ct.spacing[::-1], float)) <= margin_mm


def register_mr_to_ct(ct: Volume, ct_body, mr: Volume, label: str, ct_cranium=None, verbose: bool = False):
    """``PT-<label>MRI -> PT-CT1mm``, fixed CT (HU clamped to the Phase 3
    range), moving MR clamped to its 99.5th percentile, in two stages:
    (1) the whole head (metric over the CT body crop, centroid start);
    (2) from that pose, the metric restricted to the cranium
    (:func:`cranium_mask`), repeated with a second sampling seed as a
    stability check (pose difference and the largest displacement of a
    cranium point between the two).  Stage 2 is skipped when ``ct_cranium``
    is ``None``.  Returns ``(tx, record, head_mask)``; the record carries the
    metrics, the pose, the Dice of the head masks and the bone / brain
    intensity check."""
    import SimpleITK as sitk
    t0 = time.time()
    hm, thr = head_mask_mr(mr)
    p995 = float(np.percentile(mr.array, 99.5))
    tx1, info1 = register_rigid(ct, ct_body, mr, hm, moving_range=(0.0, p995), verbose=verbose)
    tx, info = tx1, info1
    stab_mm = stab_deg = stab_max = np.nan
    info2 = None
    if ct_cranium is not None:
        kw = dict(moving_range=(0.0, p995), fixed_metric_mask=ct_cranium, verbose=verbose,
                  initial_rotation=np.asarray(tx1.GetMatrix(), float).reshape(3, 3),
                  center_lps=np.asarray(tx1.GetCenter(), float),
                  initial_translation_lps=np.asarray(tx1.GetTranslation(), float))
        tx, info2 = register_rigid(ct, ct_body, mr, hm, **kw)
        tx_b, _info_b = register_rigid(ct, ct_body, mr, hm, seed=STABILITY_SEED, **kw)
        stab_mm, stab_deg = pose_difference(tx, tx_b)
        kji = np.argwhere(np.asarray(ct_cranium, bool))
        kji = kji[np.random.default_rng(0).choice(len(kji), min(len(kji), 20000), replace=False)]
        pc = ct.index_to_ras(kji[:, ::-1].astype(float))
        stab_max = float(np.linalg.norm(transform_points_ras(tx, pc) - transform_points_ras(tx_b, pc), axis=1).max())
        info = info2
    # MR carried into the CT grid for the intensity check (linear)
    moved = sitk.Resample(to_sitk(np.asarray(mr.array, np.float32), mr.affine),
                          to_sitk(np.zeros(ct.array.shape, np.float32), ct.affine),
                          tx, sitk.sitkLinear, 0.0, sitk.sitkFloat32)
    B = sitk.GetArrayFromImage(moved)
    A = np.asarray(ct.array, np.float32)
    # head-mask Dice inside the common field of view (the MR and the CT cover
    # different extents of the head and neck, so the crop Dice of
    # register_rigid under-reads); MR domain = where the carried MR exists
    dom = sitk.GetArrayFromImage(sitk.Resample(
        to_sitk(np.ones(mr.array.shape, np.uint8), mr.affine),
        to_sitk(np.zeros(ct.array.shape, np.uint8), ct.affine), tx, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)) > 0
    hm_ct = resample_mask_into_fixed(hm, mr.affine, tx, ct.affine, ct.array.shape)
    a_m, b_m = ct_body & dom, hm_ct & dom
    dice_fov = 2.0 * float((a_m & b_m).sum()) / max(1.0, float(a_m.sum() + b_m.sum()))
    bone = ct_body & (A > 300.0)
    brain = ct_body & (A > 0.0) & (A < 80.0)
    mb, mbr = float(np.median(B[bone])) if bone.any() else np.nan, float(np.median(B[brain])) if brain.any() else np.nan
    rec = {
        "scan": SCAN_IDS.get(label, label), "fixed": "PT-CT1mm",
        "stage1_metric_initial": info1["metric_initial"], "stage1_metric_final": info1["metric_final"],
        "stage1_iterations": info1["iterations"], "stage1_stop": _stop_text(info1["stop_condition"]),
        "stage1_euler_angles_deg": info1["euler_angles_deg"],
        "stage1_to_final_mm_deg": list(pose_difference(tx1, tx)) if info2 is not None else [0.0, 0.0],
        "metric_initial": info["metric_initial"], "metric_final": info["metric_final"],
        "iterations": info["iterations"], "starts": 1,
        "stop": _stop_text(info["stop_condition"]),
        "metric_region": ("cranium + %g mm" % CRANIUM_MARGIN_MM) if info2 is not None else "head (CT body crop)",
        "stability_seed_mm": stab_mm, "stability_seed_deg": stab_deg, "stability_max_cranium_mm": stab_max,
        "euler_angles_deg": info["euler_angles_deg"], "translation_lps_mm": info["translation_lps_mm"],
        "dice_head_masks": info["dice_masks"], "dice_head_masks_common_fov": dice_fov,
        "mr_median_in_ct_bone": mb, "mr_median_in_ct_brain": mbr,
        "bone_over_brain": (mb / mbr) if (np.isfinite(mb) and np.isfinite(mbr) and mbr > 0) else np.nan,
        "mr_shape_kji": [int(n) for n in mr.array.shape], "mr_spacing_mm": [float(s) for s in mr.spacing],
        "mr_otsu": thr, "mr_p995": p995, "seconds": float(time.time() - t0),
    }
    return tx, rec, hm


def register_phantom_to_mr(ph0: Volume, body0, lumen0, mr: Volume, mr_mask, target_ras,
                           n_refine: int = N_REFINE, focus_mm: Optional[float] = None, verbose: bool = False):
    """``PH0 -> PT-intraMRI``: fixed PH0 (metric samples restricted to the
    plastic, its 3 mm rim and the printed cavity), moving MRI; multi-start
    over the 24 cube orientations about the printed cavity's centroid, each
    placed on ``target_ras`` (the ``Cav_Post`` centroid carried into the MRI),
    coarse level only; the best ``n_refine`` are refined with the full
    schedule.  Returns ``(tx_best, record, refined)``."""
    sp_kji = np.asarray(ph0.spacing[::-1], float)
    if focus_mm is None:
        rim = ndimage.distance_transform_edt(~np.asarray(body0, bool), sampling=sp_kji) <= 3.0
        metric_mask = np.asarray(body0, bool) | rim | np.asarray(lumen0, bool)
    else:   # only the printed wall region: plastic and lumen within focus_mm of the lumen
        near = ndimage.distance_transform_edt(~np.asarray(lumen0, bool), sampling=sp_kji) <= float(focus_mm)
        metric_mask = near & (np.asarray(body0, bool) | np.asarray(lumen0, bool))
    c_lumen = apply_affine(ph0.affine, np.argwhere(lumen0).mean(axis=0)[::-1])
    c_lps = c_lumen * _FLIP
    t_lps = np.asarray(target_ras, float) * _FLIP - c_lps
    p995 = float(np.percentile(mr.array, 99.5))
    common = dict(moving_range=(0.0, p995), fixed_metric_mask=metric_mask,
                  center_lps=c_lps, initial_translation_lps=t_lps)
    t0 = time.time()
    coarse = []
    for n, R0 in enumerate(cube_rotations()):
        try:
            tx, info = register_rigid(ph0, body0, mr, mr_mask, initial_rotation=R0,
                                      shrink_factors=(4,), smoothing_sigmas_mm=(2.0,), iterations=80,
                                      **common)
        except Exception as exc:        # a start that leaves the moving image entirely
            if verbose:
                print("    start %2d failed: %s" % (n, exc))
            continue
        coarse.append((info["metric_final"], n, tx, info))
        if verbose:
            print("    start %2d  coarse metric %.4f" % (n, info["metric_final"]))
    coarse.sort(key=lambda r: r[0])
    refined = []
    for m_c, n, tx_c, _i in coarse[:n_refine]:
        R0 = np.asarray(tx_c.GetMatrix(), float).reshape(3, 3)
        tx, info = register_rigid(ph0, body0, mr, mr_mask, initial_rotation=R0,
                                  moving_range=(0.0, p995), fixed_metric_mask=metric_mask,
                                  center_lps=np.asarray(tx_c.GetCenter(), float),
                                  initial_translation_lps=np.asarray(tx_c.GetTranslation(), float))
        refined.append({"start": int(n), "coarse_metric": float(m_c), "metric_final": info["metric_final"],
                        "iterations": info["iterations"], "tx": tx, "info": info})
        if verbose:
            print("    refine start %2d  %.4f -> %.4f" % (n, m_c, info["metric_final"]))
    refined.sort(key=lambda r: r["metric_final"])
    best = refined[0]
    consensus = None
    if len(refined) > 1:
        consensus = pose_difference(best["tx"], refined[1]["tx"])
    rec = {
        "n_starts": len(coarse), "n_refined": len(refined), "best_start": best["start"],
        "coarse_metrics_sorted": [round(float(r[0]), 4) for r in coarse],
        "refined_metrics": [round(float(r["metric_final"]), 4) for r in refined],
        "best_metric_final": float(best["metric_final"]),
        "second_metric_final": float(refined[1]["metric_final"]) if len(refined) > 1 else np.nan,
        "consensus_mm": None if consensus is None else float(consensus[0]),
        "consensus_deg": None if consensus is None else float(consensus[1]),
        "euler_angles_deg": best["info"]["euler_angles_deg"],
        "translation_lps_mm": best["info"]["translation_lps_mm"],
        "dice_masks": best["info"]["dice_masks"], "center_ras": [float(v) for v in c_lumen],
        "seconds": float(time.time() - t0),
    }
    return best["tx"], rec, refined


# ----------------------------------------------------------------- outputs
def _header_lines(command: str, commit: str, datasets: Sequence[str], extra: Sequence[str] = ()) -> List[str]:
    lines = ["# script: scripts/cavity_change.py", "# command: " + command, "# commit: " + commit,
             "# date: " + _dt.date.today().isoformat(), "# datasets: " + ", ".join(datasets),
             "# flag: calibration-set (one clinical case; the printed cavity is the same patient's "
             "intra-operative cavity; no held-out scan)"]
    lines.extend("# " + e for e in extra)
    return lines


def write_csv(path: str, header: List[str], columns: List[str], rows: List[Dict[str, Any]]):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        for h in header:
            f.write(h + "\n")
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: _fmt(r.get(c, "")) for c in columns})


def _fmt(v):
    if isinstance(v, float):
        return "" if not np.isfinite(v) else ("%.4f" % v).rstrip("0").rstrip(".")
    if isinstance(v, (list, tuple, np.ndarray)):
        return "(" + ", ".join(_fmt(float(x)) for x in np.asarray(v).ravel()) + ")"
    return v


def sanitised_command(args) -> str:
    parts = ["python scripts/cavity_change.py"]
    for flag, tag in (("ct", "PT-CT1mm"), ("rtstruct", "PT-RT RTSTRUCT"), ("pre", "PT-preMRI"),
                      ("intra", "PT-intraMRI"), ("post", "PT-postMRI"), ("ph8", "PH8"), ("ph0", "PH0")):
        if getattr(args, flag, None):
            parts.append('--%s "$GT_DATA_ROOT/<%s>"' % (flag, tag))
    if getattr(args, "contours", None):
        parts.append("--contours " + " ".join("%s=<manual contour>" % k for k in sorted(args.contours)))
    if getattr(args, "crop_mm", CROP_MM) != CROP_MM:
        parts.append("--crop-mm %g" % args.crop_mm)
    if getattr(args, "skip_phantom_to_patient", False):
        parts.append("--skip-phantom-to-patient")
    parts.append("--out %s" % getattr(args, "out", "docs/figures/data"))
    return " ".join(parts)


def preview_panel(path: str, img, crop, wl, title: str, outline=None, circles_xy=None, x_ras=None, y_ras=None):
    """One PNG per panel for the author's visual check (not a paper figure)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    i0, i1, j0, j1 = crop
    sub = np.asarray(img, np.float32)[j0:j1, i0:i1]
    w, l = wl
    fig, ax = plt.subplots(figsize=(4, 4))
    ax.imshow(sub, cmap="gray", vmin=l - w / 2.0, vmax=l + w / 2.0, interpolation="nearest")
    if outline is not None and np.asarray(outline, bool)[j0:j1, i0:i1].any():
        ax.contour(np.asarray(outline, np.float32)[j0:j1, i0:i1], levels=[0.5], colors="#D55E00", linewidths=0.9)
    if circles_xy is not None and x_ras is not None and len(circles_xy):
        xs, ys = x_ras[j0:j1, i0:i1], y_ras[j0:j1, i0:i1]
        for x, y in circles_xy:
            jj, ii = np.unravel_index(np.argmin((xs - x) ** 2 + (ys - y) ** 2), xs.shape)
            ax.add_patch(plt.Circle((ii, jj), 6, fill=False, color="#E69F00", lw=0.9))
    ax.set_title(title, fontsize=8)
    ax.axis("off")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ------------------------------------------------------------ the pipeline
def load_manual_contour(path: str, roi: str, scan: Volume) -> np.ndarray:
    """A manual cavity contour as a bool mask on ``scan``'s grid (see the
    module docstring for the accepted inputs)."""
    from gtcore.io import load_volume
    from gtcore.io.rt import load_rtstruct, rasterize_roi
    import SimpleITK as sitk
    p = str(path)
    low = p.lower()
    if os.path.isdir(p) or low.endswith(".dcm"):
        rs = load_rtstruct(p)
        return rasterize_roi(rs, roi, scan.affine, scan.array.shape)
    lab = load_volume(p)
    mask = np.asarray(lab.array) > 0
    if mask.shape == scan.array.shape and np.allclose(lab.affine, scan.affine, atol=1e-3):
        return mask
    return resample_mask_into_fixed(mask, lab.affine, sitk.Euler3DTransform(), scan.affine, scan.array.shape)


def run(ct: Volume, ct_body, ct_seeds_ras, gtcore_cavity, cav_post, mris: Dict[str, Volume], ct_cranium=None,
        ph8: Optional[Volume] = None, ph0: Optional[Volume] = None, ph8_masks=None, ph8_seeds_ras=None,
        slice_k: Optional[int] = None, crop_mm: float = CROP_MM, contours: Optional[Dict[str, np.ndarray]] = None,
        phantom_to_patient: bool = True, phantom_focus: Sequence[Optional[float]] = PHANTOM_FOCUS_VARIANTS, out_dir: str = os.path.join("docs", "figures", "data"),
        previews_dir: Optional[str] = None, command: str = "python scripts/cavity_change.py",
        datasets: Sequence[str] = ("PT-CT1mm", "PT-RT", "PT-preMRI", "PT-intraMRI", "PT-postMRI", "PH8", "PH0"),
        verbose: bool = False) -> Dict[str, Any]:
    """Everything after loading; ``mris`` maps ``pre`` / ``intra`` / ``post``
    to Volumes (any subset), ``contours`` the same keys to manual masks on
    the MRI grids.  Returns the records (registrations, volumes,
    displacement) and the output paths."""
    import SimpleITK as sitk
    commit = gtcore_commit()
    os.makedirs(out_dir, exist_ok=True)
    t_all = time.time()
    vox_ct_cc = float(np.prod(ct.spacing)) / 1000.0
    cav_post = np.asarray(cav_post, bool)
    gtcore_cavity = np.asarray(gtcore_cavity, bool)
    ct_body = np.asarray(ct_body, bool)
    print("gtcore: %s  commit %s" % (gtcore.__file__, commit))

    # 1. registrations MR -> CT
    print("== 1. registrations onto PT-CT1mm")
    tx: Dict[str, Any] = {}
    reg_records: List[Dict[str, Any]] = []
    head_masks: Dict[str, np.ndarray] = {}
    for key in SCAN_KEYS:
        if key not in mris:
            continue
        t, rec, hm = register_mr_to_ct(ct, ct_body, mris[key], key, ct_cranium=ct_cranium, verbose=verbose)
        tx[key], head_masks[key] = t, hm
        reg_records.append(rec)
        print("  %-12s stage 1 %.4f -> %.4f (%d it, %s); stage 2 [%s] %.4f -> %.4f (%d it), moved %.2f mm / %.2f deg; "
              "seed stability %.2f mm / %.2f deg (max %.2f mm); Dice heads %.3f (common FOV %.3f), MR bone/brain %.2f, "
              "angles %s deg, %.0f s" % (rec["scan"], rec["stage1_metric_initial"], rec["stage1_metric_final"],
                                         rec["stage1_iterations"], rec["stage1_stop"][:40], rec["metric_region"],
                                         rec["metric_initial"], rec["metric_final"], rec["iterations"],
                                         rec["stage1_to_final_mm_deg"][0], rec["stage1_to_final_mm_deg"][1],
                                         rec["stability_seed_mm"], rec["stability_seed_deg"], rec["stability_max_cranium_mm"],
                                         rec["dice_head_masks"], rec["dice_head_masks_common_fov"],
                                         rec["bone_over_brain"], np.round(rec["euler_angles_deg"], 2), rec["seconds"]))

    # 2. the strip: slice through the implant centroid, same crop for all four
    print("== 2. strip slice and crop")
    k_rule, z_rule, centroid = implant_slice_index(ct, ct_seeds_ras)
    k = k_rule if slice_k is None else int(slice_k)
    if slice_k is not None and k != k_rule:
        warnings.warn("slice k=%d given, the implant-centroid rule says k=%d" % (k, k_rule))
    z = float(ct.index_to_ras(np.array([0.0, 0.0, float(k)]))[2])
    x_ras, y_ras, pts = slice_points(ct, k)
    cav2d = cav_post[k]
    cxy = mask_centroid_xy(cav2d, x_ras, y_ras)
    if cxy is None:
        cxy = (float(centroid[0]), float(centroid[1]))
        warnings.warn("Cav_Post is empty on slice k=%d; crop centred on the implant centroid" % k)
    crop, crop_ext = square_crop(ct, k, cxy, crop_mm)
    i0, i1, j0, j1 = crop
    arrays: Dict[str, Any] = {
        "slice_k": np.array(k), "slice_z_ras": np.array(z), "implant_centroid_ras": np.asarray(centroid, float),
        "affine": np.asarray(ct.affine, float), "spacing_mm": np.asarray(ct.spacing, float),
        "x_ras": x_ras.astype(np.float32), "y_ras": y_ras.astype(np.float32),
        "ct_hu": np.asarray(ct.array[k], np.float32), "mask_cav_post": cav2d.astype(bool),
        "mask_gtcore_cavity": gtcore_cavity[k].astype(bool),
        "crop_ij": np.array(crop, int), "crop_extent_ras": np.array(crop_ext, float),
        "crop_center_ras_xy": np.array(cxy, float), "crop_mm": np.array(float(crop_mm)),
        "wl_ct": np.array(WL_CT, float), "wl_phantom": np.array(WL_PHANTOM, float),
    }
    index: Dict[str, Any] = {}
    body2d = ct_body[k]
    for key in SCAN_KEYS:
        if key not in tx:
            continue
        img = sample_through(mris[key], tx[key], pts).reshape(x_ras.shape)
        arrays["mri_%s" % key] = img
        wl = window_from_percentiles(img, body2d)
        arrays["wl_mri_%s" % key] = np.array(wl, float)
        if contours and key in contours:
            m2 = resample_mask_into_fixed(contours[key], mris[key].affine, tx[key], ct.affine, ct.array.shape)
            arrays["mask_contour_%s" % key] = m2[k].astype(bool)
    print("  slice k=%d (z=%.2f mm RAS), crop %s px, extent %s mm, centre (%.1f, %.1f)"
          % (k, z, (i1 - i0, j1 - j0), np.round(crop_ext, 1).tolist(), cxy[0], cxy[1]))

    # 3. volumes (+ phantom)
    print("== 3. cavity volumes")
    vol_rows: List[Dict[str, Any]] = []
    vol_rows.append({"row": "cav_post", "dataset": "PT-CT1mm+PT-RT", "timepoint": "post-op (CT)",
                     "structure": "Cav_Post", "rule": "RTSTRUCT ROI rasterised on the CT grid (gtcore.io.rt.rasterize_roi)",
                     "volume_cc": float(cav_post.sum()) * vox_ct_cc, "n_voxels": int(cav_post.sum()),
                     "grid_mm": [float(s) for s in ct.spacing], "source": "PT-RT", "flag": "calibration-set"})
    vol_rows.append({"row": "gtcore_cavity_ct", "dataset": "PT-CT1mm", "timepoint": "post-op (CT)",
                     "structure": "gtcore cavity", "rule": "gtcore segment_cavity seed-sheet rule (reconstruct)",
                     "volume_cc": float(gtcore_cavity.sum()) * vox_ct_cc, "n_voxels": int(gtcore_cavity.sum()),
                     "grid_mm": [float(s) for s in ct.spacing], "source": "gtcore.pipeline.reconstruct",
                     "flag": "calibration-set"})
    phantom: Dict[str, Any] = {}
    if ph8 is not None and ph0 is not None:
        from gtcore.segment.cavity import phantom_wall_and_cavity
        t0 = time.time()
        wall8, lumen8, aff8, pinfo = phantom_wall_and_cavity(ph8, ph0, masks_ph8=ph8_masks,
                                                             seed_centers_ras=ph8_seeds_ras)
        vox8_cc = float(np.prod(ph8.spacing)) / 1000.0
        phantom = {"wall8": wall8, "lumen8": lumen8, "info": pinfo,
                   "lumen_volume_cc_ph0": float(pinfo["lumen"]["volume_ml"]),
                   "lumen_volume_cc_ph8_grid": float(lumen8.sum()) * vox8_cc,
                   "seeds_inside": int(pinfo["lumen"]["seeds_inside"]), "n_seeds": int(pinfo["lumen"]["n_seeds"]),
                   "registration": pinfo["registration"], "seconds": float(time.time() - t0)}
        print("  phantom cavity (gtcore rule on the registered twin): %.1f cc on the PH0 grid, %.1f cc on the PH8 grid, "
              "%d/%d seeds inside; twin registration Dice %.3f, %.0f s"
              % (phantom["lumen_volume_cc_ph0"], phantom["lumen_volume_cc_ph8_grid"], phantom["seeds_inside"],
                 phantom["n_seeds"], pinfo["registration"]["dice_masks"], phantom["seconds"]))
        vol_rows.insert(0, {"row": "printed_cavity", "dataset": "PH0 (registered into PH8)", "timepoint": "intra-op (print time)",
                            "structure": "printed cavity", "rule": pinfo["lumen"]["rule"]
                            + " (hull reach %.0f mm, sheet tol %.0f mm; implant-informed, Phase 4 decision)"
                            % (pinfo["lumen"]["hull_reach_mm"], pinfo["lumen"]["sheet_tol_mm"]),
                            "volume_cc": phantom["lumen_volume_cc_ph0"], "n_voxels": int(pinfo["lumen_mask_ph0"].sum()),
                            "grid_mm": [float(s) for s in ph0.spacing], "source": "gtcore.segment.cavity.phantom_wall_and_cavity",
                            "flag": "calibration-set"})
        vol_rows.insert(1, {"row": "printed_cavity_ph8_grid", "dataset": "PH8 grid", "timepoint": "intra-op (print time)",
                            "structure": "printed cavity (carried)", "rule": "same mask resampled (nearest) into the PH8 grid",
                            "volume_cc": phantom["lumen_volume_cc_ph8_grid"], "n_voxels": int(lumen8.sum()),
                            "grid_mm": [float(s) for s in ph8.spacing], "source": "gtcore.segment.cavity.phantom_wall_and_cavity",
                            "flag": "calibration-set"})
        # the phantom slice through the phantom's implant centroid, own frame, own crop
        k8, z8, c8 = implant_slice_index(ph8, ph8_seeds_ras)
        x8, y8, _p8 = slice_points(ph8, k8)
        c8xy = mask_centroid_xy(lumen8[k8], x8, y8) or (float(c8[0]), float(c8[1]))
        crop8, ext8 = square_crop(ph8, k8, c8xy, crop_mm)
        seeds8 = np.atleast_2d(np.asarray(ph8_seeds_ras, float))
        near8 = seeds8[np.abs(seeds8[:, 2] - z8) <= NEAR_SLICE_MM]
        arrays.update({"ph8_hu": np.asarray(ph8.array[k8], np.float32), "ph8_x_ras": x8.astype(np.float32),
                       "ph8_y_ras": y8.astype(np.float32), "ph8_slice_k": np.array(k8), "ph8_slice_z_ras": np.array(z8),
                       "ph8_affine": np.asarray(ph8.affine, float), "ph8_spacing_mm": np.asarray(ph8.spacing, float),
                       "ph8_crop_ij": np.array(crop8, int), "ph8_crop_extent_ras": np.array(ext8, float),
                       "ph8_mask_printed_cavity": lumen8[k8].astype(bool), "ph8_seeds_near_slice_ras": near8.astype(float),
                       "ph8_implant_centroid_ras": np.asarray(c8, float)})
        phantom.update({"k8": k8, "z8": z8, "crop8": crop8, "ext8": ext8, "n_near": int(len(near8))})
        print("  phantom slice k=%d (z=%.2f mm), %d seeds within %.0f mm, crop %s px"
              % (k8, z8, len(near8), NEAR_SLICE_MM, (crop8[1] - crop8[0], crop8[3] - crop8[2])))
    for key in SCAN_KEYS:
        if contours and key in contours and key in mris:
            m = np.asarray(contours[key], bool)
            vol_rows.append({"row": "manual_contour_%s" % key, "dataset": SCAN_IDS[key], "timepoint": {
                "pre": "pre-op (MRI)", "intra": "intra-op (MRI)", "post": "post-op (MRI)"}[key],
                "structure": "manual cavity contour", "rule": "manual contour on the MRI (--contours), own grid",
                "volume_cc": float(m.sum()) * float(np.prod(mris[key].spacing)) / 1000.0, "n_voxels": int(m.sum()),
                "grid_mm": [float(s) for s in mris[key].spacing], "source": "manual", "flag": "calibration-set"})
    for r in vol_rows:
        r["commit"] = commit
        print("  %-26s %7.2f cc  [%s]" % (r["row"], r["volume_cc"], r["rule"][:60]))

    # 4. wall displacement intra-op -> post-op
    print("== 4. wall displacement intra-op -> post-op")
    disp_rows: List[Dict[str, Any]] = []
    mesh_cav = mesh_of(cav_post, ct.affine)
    displacement: Dict[str, Any] = {}
    if contours:
        for key in SCAN_KEYS:
            if key not in contours or key not in tx:
                continue
            carried = resample_mask_into_fixed(contours[key], mris[key].affine, tx[key], ct.affine, ct.array.shape)
            if not carried.any():
                continue
            mesh_c = mesh_of(carried, ct.affine)
            d = surface_distance_two_way(mesh_c, mesh_cav)
            sd = signed_distance_to_mask(cav_post, ct.affine, _surface_points(mesh_c))
            v_c = float(carried.sum()) * vox_ct_cc
            rec = {"row": "manual_contour_%s_vs_cav_post" % key, "credible": "yes (manual contour)",
                   "reference": "Cav_Post", "moving_surface": "manual contour on %s carried into PT-CT1mm" % SCAN_IDS[key],
                   **d, "signed_median_mm": float(np.nanmedian(sd)), "signed_p5_mm": float(np.nanpercentile(sd, 5)),
                   "signed_p95_mm": float(np.nanpercentile(sd, 95)),
                   "volume_moving_cc": v_c, "volume_cav_post_cc": float(cav_post.sum()) * vox_ct_cc,
                   "volume_ratio_post_over_moving": (float(cav_post.sum()) * vox_ct_cc) / max(v_c, 1e-9)}
            disp_rows.append(rec)
            displacement.setdefault("manual", {})[key] = rec
    if phantom and phantom_to_patient and "intra" in tx:
        pinfo = phantom["info"]
        from gtcore.segment.cavity import printed_body_mask
        body0 = printed_body_mask(ph0.array)                 # PH0 grid (shell_mask is the PH8-grid copy)
        lumen0 = np.asarray(pinfo["lumen_mask_ph0"], bool)
        c_cav = apply_affine(ct.affine, np.argwhere(cav_post).mean(axis=0)[::-1])
        target_mr = transform_points_ras(tx["intra"], c_cav)[0]            # CT -> intra MRI
        for focus in phantom_focus:
            t0 = time.time()
            suffix = "" if focus is None else "_focus%g" % focus
            region = ("print + 3 mm rim + lumen" if focus is None else
                      "plastic and lumen within %g mm of the lumen (wall region)" % focus)
            tx_ph, prec, refined = register_phantom_to_mr(ph0, body0, lumen0, mris["intra"], head_masks["intra"],
                                                          target_mr, focus_mm=focus, verbose=verbose)
            # credibility (b): MRI contrast lumen vs plastic, sampled in the MRI through tx_ph (PH0 -> MRI)
            def _sample_mr(mask0, n=20000):
                kji = np.argwhere(mask0)
                if len(kji) > n:
                    kji = kji[np.random.default_rng(1).choice(len(kji), n, replace=False)]
                p0 = apply_affine(ph0.affine, kji[:, ::-1].astype(float))
                return sample_through(mris["intra"], tx_ph, p0, fill=np.nan)
            m_lumen = float(np.nanmedian(_sample_mr(lumen0)))
            m_plastic = float(np.nanmedian(_sample_mr(body0)))
            contrast = abs(m_lumen - m_plastic) / max(abs(m_plastic), 1e-9)
            # the printed wall and lumen into the patient frame: PH0 -> intra MRI (forward) -> CT (inverse)
            wall0 = pinfo["wall_mesh_ph0"]
            import trimesh
            verts_ct = compose_points_ras(np.asarray(wall0.vertices, float), [(tx_ph, False), (tx["intra"], True)])
            wall_ct = trimesh.Trimesh(vertices=verts_ct, faces=np.asarray(wall0.faces).copy(), process=False)
            d = surface_distance_two_way(wall_ct, mesh_cav)
            sd = signed_distance_to_mask(cav_post, ct.affine, _surface_points(wall_ct))
            # the lumen into the CT grid: CT point -> MRI (tx_intra forward) -> PH0 (tx_ph inverse); as a composite sitk transform
            comp = sitk.CompositeTransform([tx_ph.GetInverse(), tx["intra"]])   # applied last-first: CT->MRI, then MRI->PH0
            lumen_ct = resample_mask_into_fixed(lumen0, ph0.affine, comp, ct.affine, ct.array.shape)
            v_print_ct = float(lumen_ct.sum()) * vox_ct_cc
            ok_a = prec["consensus_mm"] is not None and prec["consensus_mm"] <= CONSENSUS_MM and prec["consensus_deg"] <= CONSENSUS_DEG
            ok_b = contrast >= CONTRAST_MIN
            ok_c = d["a_to_b_median_mm"] <= PROXIMITY_MAX_MM
            credible = bool(ok_a and ok_b and ok_c)
            reasons = []
            if not ok_a:
                reasons.append("no consensus between the best two starts (%.1f mm / %.1f deg)"
                               % (prec["consensus_mm"] if prec["consensus_mm"] is not None else np.nan,
                                  prec["consensus_deg"] if prec["consensus_deg"] is not None else np.nan))
            if not ok_b:
                reasons.append("MRI contrast lumen vs plastic %.2f < %.2f" % (contrast, CONTRAST_MIN))
            if not ok_c:
                reasons.append("carried wall median %.1f mm from Cav_Post > %.0f mm" % (d["a_to_b_median_mm"], PROXIMITY_MAX_MM))
            dice_ct = 2.0 * float((lumen_ct & cav_post).sum()) / max(1.0, float(lumen_ct.sum() + cav_post.sum()))
            rec = {"row": "printed_wall_vs_cav_post" + suffix, "ph0_metric_region": region, "credible": "yes" if credible else "NO: " + "; ".join(reasons),
                   "reference": "Cav_Post", "moving_surface": "PH0 printed wall (registered twin) carried PH0 -> PT-intraMRI -> PT-CT1mm",
                   **d, "signed_median_mm": float(np.nanmedian(sd)), "signed_p5_mm": float(np.nanpercentile(sd, 5)),
                   "signed_p95_mm": float(np.nanpercentile(sd, 95)),
                   "volume_moving_cc": phantom["lumen_volume_cc_ph0"], "volume_cav_post_cc": float(cav_post.sum()) * vox_ct_cc,
                   "volume_ratio_post_over_moving": (float(cav_post.sum()) * vox_ct_cc) / max(phantom["lumen_volume_cc_ph0"], 1e-9),
                   "printed_cavity_in_ct_grid_cc": v_print_ct, "dice_printed_vs_cav_post": dice_ct,
                   "ph0_to_intra_metric_final": prec["best_metric_final"], "ph0_to_intra_second_metric": prec["second_metric_final"],
                   "ph0_to_intra_consensus_mm": prec["consensus_mm"], "ph0_to_intra_consensus_deg": prec["consensus_deg"],
                   "ph0_to_intra_n_starts": prec["n_starts"], "ph0_to_intra_best_start": prec["best_start"],
                   "ph0_to_intra_dice_masks": prec["dice_masks"],
                   "mri_median_lumen": m_lumen, "mri_median_plastic": m_plastic, "mri_contrast": contrast,
                   "criteria": "consensus <= %g mm / %g deg; contrast >= %g; proximity median <= %g mm"
                               % (CONSENSUS_MM, CONSENSUS_DEG, CONTRAST_MIN, PROXIMITY_MAX_MM),
                   "seconds": float(time.time() - t0)}
            disp_rows.append(rec)
            displacement["phantom" + suffix] = {"focus_mm": focus, **rec, "tx_ph0_to_intra": tx_ph, "record": prec, "wall_ct": wall_ct, "lumen_ct": lumen_ct}
            print("  PH0 -> PT-intraMRI [%s]: %d starts, best %.4f (start %d), second %.4f, consensus %s mm / %s deg; "
                  "MRI lumen %.1f vs plastic %.1f (contrast %.2f); wall -> Cav_Post median %.2f p95 %.2f mm, "
                  "Cav_Post -> wall %.2f / %.2f, symmetric %.2f / %.2f; signed median %+.2f; Dice %.3f; %.0f s"
                  % (region, prec["n_starts"], prec["best_metric_final"], prec["best_start"], prec["second_metric_final"],
                     _fmt(prec["consensus_mm"]), _fmt(prec["consensus_deg"]), m_lumen, m_plastic, contrast,
                     d["a_to_b_median_mm"], d["a_to_b_p95_mm"], d["b_to_a_median_mm"], d["b_to_a_p95_mm"],
                     d["symmetric_median_mm"], d["symmetric_p95_mm"], rec["signed_median_mm"], dice_ct, rec["seconds"]))
            print("  credible: %s" % rec["credible"])
            if credible and "ph8_slice_k_matched" not in arrays:
                k_matched = int(np.clip(np.rint(ph8.ras_to_index(
                    compose_points_ras(np.array([[cxy[0], cxy[1], z]]),
                                       [(tx["intra"], False), (tx_ph, True), (pinfo["transform"], True)]))[0][2]),
                    0, ph8.array.shape[0] - 1))
                arrays["ph8_slice_k_matched"] = np.array(k_matched)
                arrays["ph8_hu_matched"] = np.asarray(ph8.array[k_matched], np.float32)
                phantom["k8_matched"] = k_matched
                print("  patient slice centre carried into PH8: k=%d (implant-centroid rule gave k=%d)" % (k_matched, phantom["k8"]))
            if previews_dir and "mri_intra" in arrays:
                preview_panel(os.path.join(previews_dir, "f3_phantom_in_patient%s.png" % suffix), arrays["mri_intra"], crop,
                              tuple(arrays["wl_mri_intra"]), "PH0 lumen via PT-intraMRI [%s] on PT-intraMRI; credible: %s"
                              % ("all" if focus is None else "wall %g mm" % focus, "yes" if credible else "no"),
                              outline=lumen_ct[k])
                preview_panel(os.path.join(previews_dir, "f3_phantom_in_patient%s_ct.png" % suffix), arrays["ct_hu"], crop, WL_CT,
                              "carried PH0 lumen (outline) on PT-CT1mm", outline=lumen_ct[k])
    elif phantom and phantom_to_patient:
        disp_rows.append({"row": "printed_wall_vs_cav_post", "credible": "NO: PT-intraMRI registration not available",
                          "reference": "Cav_Post", "moving_surface": "PH0 printed wall"})
    elif not phantom:
        disp_rows.append({"row": "printed_wall_vs_cav_post", "credible": "NO: phantom scans not given",
                          "reference": "Cav_Post", "moving_surface": "PH0 printed wall"})
    for r in disp_rows:
        r["commit"] = commit

    # ---- write
    reg_csv = os.path.join(out_dir, "f3_registration.csv")
    vol_csv = os.path.join(out_dir, "f3_cavity_volumes.csv")
    disp_csv = os.path.join(out_dir, "f3_wall_displacement.csv")
    npz = os.path.join(out_dir, "f3a_scans_slice.npz")
    js = os.path.join(out_dir, "f3a_scans_slice.json")
    hdr = _header_lines(command, commit, datasets)
    reg_cols = ["scan", "fixed", "stage1_metric_initial", "stage1_metric_final", "stage1_iterations", "stage1_stop",
                "stage1_euler_angles_deg", "stage1_to_final_mm_deg", "metric_region",
                "metric_initial", "metric_final", "iterations", "starts", "stop", "stability_seed_mm",
                "stability_seed_deg", "stability_max_cranium_mm", "euler_angles_deg",
                "translation_lps_mm", "dice_head_masks", "dice_head_masks_common_fov", "mr_median_in_ct_bone", "mr_median_in_ct_brain",
                "bone_over_brain", "mr_shape_kji", "mr_spacing_mm", "seconds", "commit"]
    for r in reg_records:
        r["commit"] = commit
    if phantom:
        preg = phantom["registration"]
        reg_records.append({"scan": "PH0", "fixed": "PH8", "metric_initial": preg.get("metric_initial", np.nan),
                            "metric_final": preg["metric_final"], "iterations": preg["iterations"], "starts": 1,
                            "stop": _stop_text(preg["stop_condition"]), "metric_region": "PH0 / PH8 body crops",
                            "euler_angles_deg": preg["euler_angles_deg"], "translation_lps_mm": preg["translation_lps_mm"],
                            "dice_head_masks": preg["dice_masks"], "mr_median_in_ct_bone": np.nan,
                            "mr_median_in_ct_brain": np.nan, "bone_over_brain": np.nan,
                            "mr_shape_kji": [int(n) for n in ph0.array.shape], "mr_spacing_mm": [float(s) for s in ph0.spacing],
                            "seconds": preg["seconds"], "commit": commit})
        for dkey in [d for d in displacement if d.startswith("phantom")]:
            pr = displacement[dkey]["record"]
            reg_records.append({"scan": "PH0", "fixed": "PT-intraMRI", "metric_initial": np.nan,
                                "metric_region": displacement[dkey]["ph0_metric_region"],
                                "metric_final": pr["best_metric_final"], "iterations": np.nan, "starts": pr["n_starts"],
                                "stop": "multi-start (%d cube orientations, %d refined); second best %.4f"
                                        % (pr["n_starts"], pr["n_refined"], pr["second_metric_final"]),
                                "euler_angles_deg": pr["euler_angles_deg"], "translation_lps_mm": pr["translation_lps_mm"],
                                "dice_head_masks": pr["dice_masks"], "mr_median_in_ct_bone": np.nan,
                                "mr_median_in_ct_brain": np.nan, "bone_over_brain": np.nan,
                                "mr_shape_kji": [int(n) for n in mris["intra"].array.shape],
                                "mr_spacing_mm": [float(s) for s in mris["intra"].spacing],
                                "seconds": pr["seconds"], "commit": commit})
    write_csv(reg_csv, hdr + [
        "# rigid Euler3D, Mattes MI 50 bins, 25 % random samples (seed 1234), levels 4/2/1 with 2/1/0 mm smoothing, "
        "regular-step gradient descent (gtcore.preprocess.register.register_rigid; Phase 3 parameters, not tuned); "
        "CT clamped to [-1000, 500] HU, MR to [0, p99.5]; the transform maps fixed -> moving (SimpleITK convention)",
        "# dice_head_masks: moving foreground (CT body rule / MR Otsu head mask) carried into the fixed crop vs the fixed mask; "
        "dice_head_masks_common_fov: the same restricted to the voxels the carried MR covers (the scans cover different extents of head and neck)",
        "# bone_over_brain: median MR intensity inside CT bone (> 300 HU) over the median inside CT brain (0-80 HU); "
        "same patient and aligned -> clearly < 1",
        "# PH0 -> PT-intraMRI: multi-start over the 24 cube orientations about the printed cavity centroid, placed on the "
        "Cav_Post centroid carried into the MRI; metric samples restricted to the plastic + 3 mm rim + printed cavity"],
        reg_cols, reg_records)
    write_csv(vol_csv, hdr + [
        "# one row per cavity volume quoted in Table 1 / the F3 caption; rule = how the mask was made; grid_mm = the voxel grid it was counted on",
        "# printed cavity = the phantom at print time = this patient's intra-operative cavity (printed from PT-intraMRI); gtcore's "
        "cavity rule on the tile-less twin PH0 registered into PH8 around the carried PH8 seeds (Phase 4 decision: no implant-independent "
        "lumen exists for this print, its mouth is as wide as the craniotomy)",
        "# Cav_Post = the physician's post-operative cavity (RTSTRUCT); gtcore cavity = reconstruct() on PT-CT1mm (Dice 0.665 vs Cav_Post, Phase 3)",
        "# the MRIs are not segmented (stop list); manual_contour_* rows appear only with --contours"],
        ["row", "dataset", "timepoint", "structure", "volume_cc", "n_voxels", "grid_mm", "rule", "source", "flag", "commit"],
        vol_rows)
    write_csv(disp_csv, hdr + [
        "# wall displacement intra-op -> post-op: two-way closest-point surface distance (trimesh.proximity) between the moving "
        "surface (the printed wall = intra-operative geometry, carried PH0 -> PT-intraMRI -> PT-CT1mm; or a manual contour carried "
        "through its MRI's registration) and the Cav_Post surface (mask_to_mesh of the rasterised ROI on the CT grid); a_to_b = moving "
        "-> Cav_Post, b_to_a = Cav_Post -> moving (the printed wall is an open patch with no cap across the mouth, so b_to_a includes "
        "Cav_Post vertices facing the mouth), symmetric = pooled",
        "# signed_*: signed distance of the moving surface's vertices to the Cav_Post surface (raw-grid EDT, trilinear): positive = "
        "outside the post-operative cavity (the post-op cavity lies inside the intra-op wall: collapse), negative = inside",
        "# credible: the PH0 -> PT-intraMRI registration passes the three a-priori criteria (consensus of the best two multi-start "
        "poses within %g mm / %g deg; MRI contrast between the carried lumen and the carried plastic >= %g; median carried-wall -> "
        "Cav_Post distance <= %g mm); when NO, the distances are not paper numbers" % (CONSENSUS_MM, CONSENSUS_DEG, CONTRAST_MIN, PROXIMITY_MAX_MM)],
        ["row", "credible", "reference", "moving_surface", "ph0_metric_region", "a_to_b_median_mm", "a_to_b_p95_mm", "a_to_b_max_mm",
         "b_to_a_median_mm", "b_to_a_p95_mm", "b_to_a_max_mm", "symmetric_median_mm", "symmetric_p95_mm",
         "signed_median_mm", "signed_p5_mm", "signed_p95_mm", "volume_moving_cc", "volume_cav_post_cc",
         "volume_ratio_post_over_moving", "printed_cavity_in_ct_grid_cc", "dice_printed_vs_cav_post",
         "ph0_to_intra_metric_final", "ph0_to_intra_second_metric", "ph0_to_intra_consensus_mm", "ph0_to_intra_consensus_deg",
         "ph0_to_intra_n_starts", "ph0_to_intra_best_start", "ph0_to_intra_dice_masks", "mri_median_lumen", "mri_median_plastic",
         "mri_contrast", "criteria", "n_a", "n_b", "seconds", "commit"],
        disp_rows)
    # NPZ + index
    arrays["registration_table"] = np.array(json.dumps(
        [{k: (v if not isinstance(v, (np.floating, np.integer)) else float(v)) for k, v in r.items()} for r in reg_records],
        default=str))
    arrays["header"] = np.array("\n".join(hdr))
    np.savez_compressed(npz, **arrays)
    index = {"script": "scripts/cavity_change.py", "command": command, "commit": commit,
             "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "datasets": list(datasets),
             "flag": "calibration-set", "panel": "F3(a) four registered scans, one slice, one crop (+ the PH8 phantom slice, own frame)",
             "conventions": {
                 "coordinates": "RAS mm; 2-D arrays are (nj, ni) = the CT slice's [j, i] (row = y, column = x); affine maps (i, j, k, 1) -> RAS",
                 "slice": "slice_k = the axial PT-CT1mm slice through the implant centroid (the clinical_slice.npz slice shared with F4a / F5a / F6)",
                 "mri_*": "the registered MRI sampled trilinearly at the CT voxel centres of that slice (native MR intensity units; 0 outside the MR); "
                          "NOT segmented -- no outline on the MRI panels unless mask_contour_* exists (manual contours)",
                 "crop": "crop_ij = (i0, i1, j0, j1) half-open index bounds of the square crop (crop_mm, centred on the Cav_Post centroid of the "
                         "slice); crop_extent_ras = (x_min, x_max, y_min, y_max) voxel-centre extent; identical for the four patient panels",
                 "windows": "wl_* = (window, level): CT fixed W 400 / L 40 HU, phantom W 1000 / L 0 HU, MRI = 1st-99th percentile inside the head on the slice",
                 "overlay_ct": "mask_cav_post (physician's post-operative cavity, RTSTRUCT) outlined on the CT panel; mask_gtcore_cavity provided, not drawn in F3",
                 "phantom": "ph8_* = the PH8 slice through the phantom's implant centroid in the phantom's own frame (same rule), own crop centred on the "
                            "printed cavity (ph8_mask_printed_cavity = the registered-twin cavity on that slice); ph8_seeds_near_slice_ras = seed centres "
                            "within 3 mm of the slice (circle them); ph8_slice_k_matched / ph8_hu_matched (only when the phantom-to-patient registration "
                            "is credible) = the PH8 slice through the point where the patient slice's crop centre lands in the phantom",
             },
             "arrays": {k: {"shape": list(np.asarray(v).shape), "dtype": str(np.asarray(v).dtype)} for k, v in arrays.items()},
             "registrations": [{k: v for k, v in r.items()} for r in reg_records],
             "volumes": [{k: v for k, v in r.items()} for r in vol_rows],
             "displacement": [{k: v for k, v in r.items()} for r in disp_rows]}
    with open(js, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1, default=str)
    paths = {"registration_csv": reg_csv, "volumes_csv": vol_csv, "displacement_csv": disp_csv, "npz": npz, "json": js}
    # previews
    if previews_dir:
        preview_panel(os.path.join(previews_dir, "f3a_ct.png"), arrays["ct_hu"], crop, WL_CT, "PT-CT1mm k=%d (Cav_Post)" % k,
                      outline=arrays["mask_cav_post"])
        for key in SCAN_KEYS:
            if "mri_%s" % key in arrays:
                preview_panel(os.path.join(previews_dir, "f3a_mri_%s.png" % key), arrays["mri_%s" % key], crop,
                              tuple(arrays["wl_mri_%s" % key]), "%s registered, same slice" % SCAN_IDS[key],
                              outline=arrays.get("mask_contour_%s" % key))
        if phantom:
            preview_panel(os.path.join(previews_dir, "f3_phantom_ph8.png"), arrays["ph8_hu"], phantom["crop8"], WL_PHANTOM,
                          "PH8 k=%d (printed cavity, seeds)" % phantom["k8"], outline=arrays["ph8_mask_printed_cavity"],
                          circles_xy=[(s[0], s[1]) for s in arrays["ph8_seeds_near_slice_ras"]],
                          x_ras=arrays["ph8_x_ras"], y_ras=arrays["ph8_y_ras"])
        paths["previews_dir"] = previews_dir
    print("wrote %s, %s, %s, %s, %s (%.0f s)" % (reg_csv, vol_csv, disp_csv, npz, js, time.time() - t_all))
    return {"registrations": reg_records, "volumes": vol_rows, "displacement": disp_rows, "phantom": phantom,
            "slice": {"k": k, "z": z, "crop": crop, "crop_extent": crop_ext, "center_xy": cxy}, "paths": paths,
            "transforms": tx, "displacement_detail": displacement}


def parse_contours(items: Optional[Sequence[str]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for it in items or ():
        if "=" not in it:
            raise SystemExit("--contours expects pre=<file> intra=<file> post=<file>, got %r" % it)
        k, v = it.split("=", 1)
        if k not in SCAN_KEYS:
            raise SystemExit("--contours key must be one of %s, got %r" % (SCAN_KEYS, k))
        out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ct", required=True, help="PT-CT1mm DICOM series directory")
    ap.add_argument("--rtstruct", required=True, help="PT-RT RTSTRUCT file or directory (Cav_Post)")
    ap.add_argument("--pre", help="PT-preMRI DICOM series directory")
    ap.add_argument("--intra", help="PT-intraMRI DICOM series directory")
    ap.add_argument("--post", help="PT-postMRI DICOM series directory")
    ap.add_argument("--ph8", help="PH8 DICOM series directory (printed phantom, 8 tiles)")
    ap.add_argument("--ph0", help="PH0 DICOM series directory (tile-less twin)")
    ap.add_argument("--clinical-slice", default=os.path.join("docs", "figures", "data", "clinical_slice.npz"),
                    help="clinical_slice.npz whose slice_k is reused (the implant-centroid rule when absent)")
    ap.add_argument("--crop-mm", type=float, default=CROP_MM)
    ap.add_argument("--contours", nargs="*", help="manual cavity contours: pre=<file> intra=<file> post=<file>")
    ap.add_argument("--contour-roi", default="Cavity", help="ROI name in a manual RTSTRUCT contour")
    ap.add_argument("--skip-phantom-to-patient", action="store_true",
                    help="do not attempt the PH0 -> PT-intraMRI registration (step 4)")
    ap.add_argument("--out", default=os.path.join("docs", "figures", "data"))
    ap.add_argument("--previews", default=os.path.join("output", "figures", "cavity_change"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    args.contours = parse_contours(args.contours)

    from gtcore.io import load_volume
    from gtcore.io.rt import load_rtstruct, rasterize_roi
    from gtcore.pipeline import reconstruct
    print("gtcore: %s  commit %s" % (gtcore.__file__, gtcore_commit()))
    t0 = time.time()
    ct = load_volume(args.ct)
    res = reconstruct(ct, verbose=args.verbose)
    seeds = np.asarray(res.seeds.centers_ras, float)
    print("PT-CT1mm %s at %s mm; reconstruct %.0f s: %d seeds, gtcore cavity %.1f cc"
          % (ct.array.shape, np.round(ct.spacing, 3), time.time() - t0, len(seeds),
             res.cavity_mask.sum() * np.prod(ct.spacing) / 1000.0))
    rs = load_rtstruct(args.rtstruct)
    cav = rasterize_roi(rs, "Cav_Post", ct.affine, ct.array.shape)
    slice_k = None
    if args.clinical_slice and os.path.exists(args.clinical_slice):
        slice_k = int(np.load(args.clinical_slice, allow_pickle=True)["slice_k"])
        print("slice k=%d from %s" % (slice_k, args.clinical_slice))
    mris: Dict[str, Volume] = {}
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="expected a CT series")
        for key in SCAN_KEYS:
            p = getattr(args, key)
            if p:
                mris[key] = load_volume(p)
                print("%s %s at %s mm" % (SCAN_IDS[key], mris[key].array.shape, np.round(mris[key].spacing, 3)))
    contours = None
    if args.contours:
        contours = {k: load_manual_contour(v, args.contour_roi, mris[k]) for k, v in args.contours.items() if k in mris}
    ph8 = ph0 = None
    ph8_masks = ph8_seeds = None
    if args.ph8 and args.ph0:
        t1 = time.time()
        ph8 = load_volume(args.ph8)
        ph0 = load_volume(args.ph0)
        res8 = reconstruct(ph8, verbose=args.verbose)
        ph8_masks, ph8_seeds = res8.masks, np.asarray(res8.seeds.centers_ras, float)
        print("PH8 %s at %s mm, PH0 %s at %s mm; reconstruct(PH8) %.0f s: %d seeds"
              % (ph8.array.shape, np.round(ph8.spacing, 3), ph0.array.shape, np.round(ph0.spacing, 3),
                 time.time() - t1, len(ph8_seeds)))
    datasets = ["PT-CT1mm", "PT-RT"] + [SCAN_IDS[k] for k in SCAN_KEYS if k in mris] + (["PH8", "PH0"] if ph8 is not None else [])
    return run(ct, res.masks["body"], seeds, res.cavity_mask, cav, mris, ct_cranium=cranium_mask(ct, res.masks), ph8=ph8, ph0=ph0, ph8_masks=ph8_masks,
               ph8_seeds_ras=ph8_seeds, slice_k=slice_k, crop_mm=args.crop_mm, contours=contours,
               phantom_to_patient=not args.skip_phantom_to_patient, out_dir=args.out, previews_dir=args.previews,
               command=sanitised_command(args), datasets=datasets, verbose=args.verbose)


if __name__ == "__main__":
    main()
