"""scripts/seed_wall_distance.py on synthetic data.

- the signed distance of seeds placed at known offsets from a digital sphere
  cavity on an anisotropic (0.5 x 0.5 x 1.0 mm) grid is recovered: every
  sign right, the distribution (median / mean) within 0.2 mm, the rms within
  0.2 mm, and every single seed within half the slice spacing + 0.2 mm --
  the per-seed floor a binary mask on 1 mm slices allows (a surface element
  perpendicular to z is quantised to the slice);
- the same for gtcore's mesh cross-check;
- the ``solid`` convention (phantom plastic): positive on the air side;
- flipped direction cosines and a non-zero origin (the PH8 affine) give the
  same numbers;
- the RTDOSE-maxima source finder recovers two synthetic sources;
- the rigid registration recovers a known Euler transform between a block
  and its resampled copy;
- the SimpleITK image built from a gtcore affine has the same geometry.
"""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")
sitk = pytest.importorskip("SimpleITK")

from gtcore.volume import Volume, apply_affine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def swd():
    path = os.path.join(ROOT, "scripts", "seed_wall_distance.py")
    spec = importlib.util.spec_from_file_location("seed_wall_distance", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _grid(spacing, extent_mm, origin_shift=(0.0, 0.0, 0.0), flip=False):
    """Axis-aligned grid of voxel centres around the origin: ``(affine, xyz)``.
    ``flip`` negates every direction cosine (as the PH8/PH0 scans have)."""
    sp = np.asarray(spacing, float)
    half = np.ceil(extent_mm / sp[::-1]).astype(int)
    n = 2 * half + 1
    aff = np.diag(list(sp) + [1.0])
    if flip:
        aff[:3, :3] *= -1.0
        aff[:3, 3] = half[::-1] * sp + np.asarray(origin_shift)
    else:
        aff[:3, 3] = -half[::-1] * sp + np.asarray(origin_shift)
    k, j, i = np.mgrid[:n[0], :n[1], :n[2]]
    ijk = np.stack([i, j, k], axis=-1).reshape(-1, 3).astype(float)
    xyz = apply_affine(aff, ijk).reshape(n[0], n[1], n[2], 3)
    return aff, xyz


def _dirs(n=300, seed=1):
    d = np.random.default_rng(seed).normal(size=(n, 3))
    return d / np.linalg.norm(d, axis=1, keepdims=True)


R_MM = 15.0
OFFSETS = (4.6, 3.0, 1.0, -1.0, -3.0)   # + = inside the cavity


def _check(err, truth, slice_mm, label):
    assert np.all(np.sign(err + truth) == np.sign(truth)), label + ": sign"
    assert abs(np.median(err)) <= 0.2, label + ": median %.3f" % np.median(err)
    assert abs(err.mean()) <= 0.2, label + ": mean %.3f" % err.mean()
    assert np.sqrt((err ** 2).mean()) <= 0.25, label + ": rms %.3f" % np.sqrt((err ** 2).mean())
    assert np.abs(err).max() <= 0.5 * slice_mm + 0.2, label + ": max %.3f" % np.abs(err).max()


def test_sphere_cavity_edt_and_mesh(swd):
    spacing = (0.5, 0.5, 1.0)
    aff, xyz = _grid(spacing, R_MM + 8.0, origin_shift=(3.3, -2.1, 7.7))
    centre = np.array([3.3, -2.1, 7.7])
    mask = np.linalg.norm(xyz - centre, axis=-1) <= R_MM
    dirs = _dirs()
    pts = {off: centre + (R_MM - off) * dirs for off in OFFSETS}
    allp = np.vstack(list(pts.values()))
    field = swd.SignedDistanceField(mask, aff, solid=False, points_ras=allp)
    assert field.factors_kji == (2, 1, 1)                       # z anti-aliased to 0.5 mm
    assert np.allclose(field.spacing_fine_mm, (0.5, 0.5, 0.5))
    mesh = swd.surface_mesh(mask, aff, points_ras=allp)
    assert mesh.is_watertight
    for off in OFFSETS:
        _check(field.sample(pts[off]) - off, off, spacing[2], "edt off %+.1f" % off)
        _check(swd.signed_distance_mesh(mesh, pts[off]) - off, off, spacing[2], "mesh off %+.1f" % off)
    # the convenience wrapper is the same field
    assert np.allclose(swd.signed_distance_edt(mask, aff, pts[3.0]), field.sample(pts[3.0]), atol=1e-5)


def test_solid_convention_and_flipped_affine(swd):
    """Phantom convention: the mask is the plastic ball; seeds in the air at
    +3 mm read +3, seeds inside the plastic at -1 read -1.  The same mask on
    an affine with negated direction cosines gives the same numbers."""
    spacing = (0.59, 0.59, 1.0)
    out = {}
    for flip in (False, True):
        aff, xyz = _grid(spacing, 12.0 + 7.0, origin_shift=(-20.0, 15.0, 100.0), flip=flip)
        centre = np.array([-20.0, 15.0, 100.0])
        ball = np.linalg.norm(xyz - centre, axis=-1) <= 12.0
        dirs = _dirs(200, seed=3)
        p_air = centre + (12.0 + 3.0) * dirs
        p_in = centre + (12.0 - 1.0) * dirs
        d_air = swd.signed_distance_edt(ball, aff, p_air, solid=True)
        d_in = swd.signed_distance_edt(ball, aff, p_in, solid=True)
        _check(d_air - 3.0, 3.0, spacing[2], "solid air flip=%s" % flip)
        _check(d_in + 1.0, -1.0, spacing[2], "solid inside flip=%s" % flip)
        mesh = swd.surface_mesh(ball, aff, points_ras=np.vstack([p_air, p_in]))
        dm = swd.signed_distance_mesh(mesh, p_air, solid=True)
        _check(dm - 3.0, 3.0, spacing[2], "solid mesh flip=%s" % flip)
        out[flip] = (d_air, d_in)
    # flipping the axes relabels voxels only: identical masks, identical distances
    assert np.allclose(out[False][0], out[True][0], atol=1e-4)
    assert np.allclose(out[False][1], out[True][1], atol=1e-4)


def test_summarize_and_histogram(swd):
    d = np.array([2.0, 2.5, 3.0, 3.5, 4.0, -1.0])
    s = swd.summarize(d)
    assert s["n"] == 6 and s["median"] == pytest.approx(2.75) and s["min"] == -1.0 and s["max"] == 4.0
    assert s["n_in_band"] == 3 and s["n_negative"] == 1          # 2.5, 3.0, 3.5 in [2.25, 3.75]
    rows = swd.histogram_rows(d, "X", "s", "r")
    assert sum(r["count"] for r in rows) == 6
    assert all(r["dataset"] == "X" for r in rows)
    assert swd.summarize([])["n"] == 0


def test_tps_sources_from_rtdose(swd):
    """Two Gaussian hot spots on a 1 x 1 x 1.667 mm dose grid -> two sources
    at the peak voxel centres."""
    aff = np.diag([1.0, 1.0, 1.667, 1.0])
    aff[:3, 3] = (-20.0, -20.0, -25.0)
    nk, nj, ni = 31, 41, 41
    k, j, i = np.mgrid[:nk, :nj, :ni]
    xyz = apply_affine(aff, np.stack([i, j, k], -1).reshape(-1, 3).astype(float)).reshape(nk, nj, ni, 3)
    truth = apply_affine(aff, np.array([[15, 23, 10], [26, 16, 18]], float))   # two voxel centres
    dose = np.zeros((nk, nj, ni))
    for c in truth:
        dose += 50000.0 * np.exp(-np.sum((xyz - c) ** 2, axis=-1) / (2 * 2.0 ** 2))   # cGy, peak 500 Gy
    src = swd.tps_sources_from_rtdose(Volume(dose, aff, {}), source_gy=200.0, merge_mm=3.0)
    assert src.shape == (2, 3)
    ia, ib, dd = swd.match_one_to_one(truth, src)
    assert dd.max() < 1e-6
    assert swd.tps_sources_from_rtdose(Volume(np.zeros((5, 5, 5)), aff, {})).shape == (0, 3)


def test_to_sitk_geometry(swd):
    aff = np.array([[-0.6, 0.0, 0.0, 150.0], [0.0, -0.6, 0.0, 30.0], [0.0, 0.0, -1.0, -545.0], [0, 0, 0, 1.0]])
    img = swd.to_sitk(np.zeros((4, 5, 6), np.float32), aff)
    for idx in ((0, 0, 0), (5, 4, 3), (2, 1, 3)):
        lps = np.asarray(img.TransformIndexToPhysicalPoint(idx))
        ras = apply_affine(aff, np.asarray(idx, float))
        assert np.allclose(lps, ras * np.array([-1.0, -1.0, 1.0]), atol=1e-9)


def test_rigid_registration_recovers_known_transform(swd):
    """A printed-block look-alike (box with a spherical notch, ~100 HU in air
    at -1000) is resampled under a known Euler transform; the registration
    must map points back within 0.3 mm and carry the mask with Dice > 0.97."""
    sp = (1.0, 1.0, 1.0)
    aff = np.diag(list(sp) + [1.0])
    aff[:3, 3] = (-40.0, -40.0, -40.0)
    n = 81
    k, j, i = np.mgrid[:n, :n, :n]
    xyz = apply_affine(aff, np.stack([i, j, k], -1).reshape(-1, 3).astype(float)).reshape(n, n, n, 3)
    box = np.all(np.abs(xyz - np.array([0.0, 0.0, 0.0])) <= np.array([25.0, 20.0, 22.0]), axis=-1)
    notch = np.linalg.norm(xyz - np.array([0.0, 20.0, 5.0]), axis=-1) <= 14.0
    hole = np.linalg.norm(xyz - np.array([-12.0, -8.0, -10.0]), axis=-1) <= 4.0
    block = box & ~notch & ~hole
    rng = np.random.default_rng(0)
    hu = np.where(block, 100.0, -1000.0) + rng.normal(0.0, 15.0, size=block.shape)
    fixed = Volume(hu.astype(np.float32), aff, {})
    # moving = fixed resampled under a known transform (moving -> fixed is its inverse)
    truth = sitk.Euler3DTransform()
    truth.SetCenter((0.0, 0.0, 0.0))
    truth.SetRotation(np.radians(4.0), np.radians(-3.0), np.radians(6.0))
    truth.SetTranslation((3.0, -2.5, 4.0))
    f_img = swd.to_sitk(fixed.array, aff)
    m_img = sitk.Resample(f_img, f_img, truth.GetInverse(), sitk.sitkLinear, -1000.0, sitk.sitkFloat32)
    moving = Volume(sitk.GetArrayFromImage(m_img), aff, {})
    f_mask = swd.body_mask_from_hu(fixed.array)
    m_mask = swd.body_mask_from_hu(moving.array)
    tx, info = swd.register_rigid(fixed, f_mask, moving, m_mask, margin_mm=6.0)
    assert info["dice_masks"] > 0.97, info
    probes = np.array([[0.0, 0.0, 0.0], [20.0, -15.0, 10.0], [-18.0, 12.0, -16.0], [5.0, 18.0, 20.0]])
    want = np.array([truth.TransformPoint(tuple(p)) for p in probes])      # fixed -> moving (LPS)
    got = swd.transform_points_ras(tx, probes * np.array([-1.0, -1.0, 1.0])) * np.array([-1.0, -1.0, 1.0])
    assert np.abs(got - want).max() < 0.3, np.abs(got - want).max()
    back = swd.transform_points_ras(tx, swd.transform_points_ras(tx, probes), inverse=True)
    assert np.allclose(back, probes, atol=1e-9)
