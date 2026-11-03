"""Rigid registration between two CT volumes and the geometry glue around it.

Moved here from ``scripts/seed_wall_distance.py`` (Paper 2 Phase 3, task
``seed-wall``) so that ``gtcore.segment.cavity.phantom_wall_and_cavity`` can
register the tile-less printed phantom ``PH0`` into the ``PH8`` frame with
exactly the transform the seed-to-wall measurement used; the script imports
these names from here and its behaviour is unchanged.

Conventions.  gtcore volumes are ``[k, j, i]`` arrays with an affine mapping
voxel index ``(i, j, k, 1)`` to **RAS** mm; SimpleITK works in **LPS**.
:func:`to_sitk` builds an image with exactly the gtcore geometry (spacing,
origin and direction cosines, flipped axes included), :func:`register_rigid`
runs SimpleITK's Euler3D registration (Mattes mutual information, seeded
random sampling, three resolution levels, regular-step gradient descent) and
returns the transform in SimpleITK's resampling convention -- it maps
**fixed** physical points to **moving** physical points (LPS) --, and
:func:`transform_points_ras` / :func:`transform_mesh_ras` /
:func:`resample_mask_into_fixed` apply it to RAS points, meshes and masks.

Nothing here is tuned: the registration parameters are the ones measured in
``docs/paper2-phase-notes.md`` (Phase 3 "seed-to-wall distance": PH0 -> PH8
Dice 0.979, surface residual 0.34 mm median).
"""
from __future__ import annotations

import time
from typing import Any, Dict, Tuple

import numpy as np

from ..volume import Volume, apply_affine

RAS_TO_LPS = np.diag([-1.0, -1.0, 1.0, 1.0])
_FLIP = np.array([-1.0, -1.0, 1.0])


# ------------------------------------------------------------ grid helpers
def spacing_of(affine) -> np.ndarray:
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
    pad = np.ceil(margin_mm / spacing_of(affine)[::-1])
    lo = np.maximum(lo - pad, 0).astype(int)
    hi = np.minimum(hi + pad, np.asarray(mask.shape) - 1).astype(int)
    sub = mask[lo[0]:hi[0] + 1, lo[1]:hi[1] + 1, lo[2]:hi[2] + 1]
    A = affine.copy()
    A[:3, 3] = apply_affine(affine, np.array([lo[2], lo[1], lo[0]], float))
    return sub, A, (lo, hi)


def crop_volume(vol: Volume, mask, margin_mm: float):
    """``(Volume, mask)`` cropped to the mask's bounding box plus ``margin_mm``."""
    sub_mask, A, (lo, hi) = crop_mask(mask, vol.affine, margin_mm)
    sub = np.asarray(vol.array)[lo[0]:hi[0] + 1, lo[1]:hi[1] + 1, lo[2]:hi[2] + 1]
    return Volume(sub, A, dict(vol.meta)), sub_mask


def mask_centroid_ras(mask, affine) -> np.ndarray:
    """Centroid of the mask's voxel centres, RAS mm."""
    kji = np.argwhere(mask).mean(axis=0)
    return apply_affine(affine, kji[::-1])


# ---------------------------------------------------- SimpleITK geometry
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


def transform_mesh_ras(tx, mesh, inverse: bool = False):
    """A copy of ``mesh`` (RAS) with its vertices carried through the
    transform (:func:`transform_points_ras`); faces unchanged, normals
    recomputed by trimesh.  A rigid map keeps the winding, so a mesh whose
    normals point out of the solid still does."""
    import trimesh
    verts = transform_points_ras(tx, np.asarray(mesh.vertices, float), inverse=inverse)
    return trimesh.Trimesh(vertices=verts, faces=np.asarray(mesh.faces).copy(), process=False)


def resample_mask_into_fixed(mask, affine, tx, fixed_affine, fixed_shape_kji) -> np.ndarray:
    """Carry a bool mask living on the MOVING grid into the FIXED grid with
    the registration transform (nearest neighbour), i.e. what
    ``sitk.Resample(moving, fixed, tx)`` does -- ``tx`` maps fixed physical
    points to moving physical points, SimpleITK's convention.  The result is
    a bool array shaped like the fixed volume ``[k, j, i]``."""
    import SimpleITK as sitk
    moving = to_sitk(np.asarray(mask, np.uint8), affine)
    ref = to_sitk(np.zeros(tuple(int(n) for n in fixed_shape_kji), np.uint8), fixed_affine)
    carried = sitk.Resample(moving, ref, tx, sitk.sitkNearestNeighbor, 0, sitk.sitkUInt8)
    return sitk.GetArrayFromImage(carried).astype(bool)


# ------------------------------------------------------------ registration
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
    f_vol, f_mask = crop_volume(fixed, fixed_mask, margin_mm)
    m_vol, m_mask = crop_volume(moving, moving_mask, margin_mm)
    lo, hi = float(hu_range[0]), float(hu_range[1])
    fi = to_sitk(np.clip(np.asarray(f_vol.array, np.float32), lo, hi), f_vol.affine)
    mi = to_sitk(np.clip(np.asarray(m_vol.array, np.float32), lo, hi), m_vol.affine)
    cf = mask_centroid_ras(f_mask, f_vol.affine) * _FLIP
    cm = mask_centroid_ras(m_mask, m_vol.affine) * _FLIP
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
    info: Dict[str, Any] = {
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


__all__ = [
    "RAS_TO_LPS", "spacing_of", "crop_mask", "crop_volume", "mask_centroid_ras",
    "to_sitk", "euler_as_matrix", "transform_points_ras", "transform_mesh_ras",
    "resample_mask_into_fixed", "register_rigid",
]
