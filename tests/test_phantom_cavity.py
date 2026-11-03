"""Phantom wall and cavity from the registered tile-less twin
(``gtcore.segment.cavity.phantom_wall_and_cavity``, ``gtcore.preprocess.register``;
Paper 2 Phase 4).

Synthetic data only: a printed block (plastic ball, spherical cavity, a
cylindrical mouth to the outside -- the printed craniotomy channel) at 1 mm.

- ``printed_lumen`` (gtcore's seed-sheet cavity rule around the implant)
  recovers the cavity ball to a few per cent of the voxel truth whether or
  not the mouth is open, needs the implant, and fails loudly on seeds
  outside the shell;
- the wall mesh is the plastic surface bordering that cavity (no face in
  the air of the mouth or on the block's exterior), normals into the plastic;
- the registration round trip (mesh and mask) on a copy of the block under
  a known rigid transform;
- ``phantom_wall_and_cavity`` end to end: the implanted copy carries
  collagen-like tiles on the wall (so its body mask hides the wall, the
  real PH8 situation); seeds placed 3 mm off the true wall read 3 mm off
  the returned wall, the cavity volume is the lumen's, and ``rind5`` of it
  with the shell as clip is plastic around the lumen.
"""
from __future__ import annotations

import numpy as np
import pytest

sitk = pytest.importorskip("SimpleITK")
trimesh = pytest.importorskip("trimesh")

from gtcore.dose import targets  # noqa: E402
from gtcore.preprocess import register as R  # noqa: E402
from gtcore.segment import cavity as cav  # noqa: E402
from gtcore.volume import Volume, apply_affine  # noqa: E402

N = 81
AFF = np.diag([1.0, 1.0, 1.0, 1.0])
AFF[:3, 3] = (-40.0, -40.0, -40.0)
R_IN, R_OUT, MOUTH_R = 15.0, 24.0, 5.0


def _xyz():
    k, j, i = np.mgrid[:N, :N, :N]
    ijk = np.stack([i, j, k], axis=-1).reshape(-1, 3).astype(float)
    return apply_affine(AFF, ijk).reshape(N, N, N, 3)


def _block(mouth=True):
    """``(plastic, lumen_true)``: ball R_OUT of plastic with a spherical
    cavity R_IN and (optionally) a channel of radius MOUTH_R to the outside
    along +z."""
    xyz = _xyz()
    r = np.linalg.norm(xyz, axis=-1)
    # a ball with two flat cuts: a plain ball leaves the registration free to
    # spin about the channel axis
    outer = (r <= R_OUT) & (xyz[..., 0] <= 17.0) & (xyz[..., 1] >= -19.0)
    ball = r <= R_IN
    rho = np.linalg.norm(xyz[..., :2], axis=-1)
    channel = (rho <= MOUTH_R) & (xyz[..., 2] > 0.0) if mouth else np.zeros_like(ball)
    lumen = (ball | channel) & outer
    return outer & ~lumen, lumen


def _volume(plastic, seed=0, tiles=None, tile_hu=-100.0):
    rng = np.random.default_rng(seed)
    hu = np.where(plastic, 100.0, -1000.0)
    if tiles is not None:
        hu = np.where(tiles & ~plastic, tile_hu, hu)
    hu = hu + rng.normal(0.0, 15.0, size=plastic.shape)
    return Volume(hu.astype(np.float32), AFF, {})


def _dice(a, b):
    a, b = np.asarray(a, bool), np.asarray(b, bool)
    return 2.0 * float((a & b).sum()) / max(1.0, float(a.sum() + b.sum()))


# -------------------------------------------------------------- the lumen
def _implant(n=24, r=R_IN - 3.0, top_cone_deg=35.0, seed=5):
    """Seeds 3 mm off the cavity wall in ``n`` directions (none within the
    channel's cone about +z)."""
    rng = np.random.default_rng(seed)
    out = []
    while len(out) < n:
        d = rng.normal(size=3)
        d /= np.linalg.norm(d)
        if d[2] > np.cos(np.radians(top_cone_deg)):
            continue
        out.append(r * d)
    return np.asarray(out)


def test_printed_lumen_is_the_cavity_around_the_implant():
    plastic, truth = _block(mouth=True)
    ball = np.linalg.norm(_xyz(), axis=-1) <= R_IN
    seeds = _implant()
    lumen, info = cav.printed_lumen(plastic, AFF, seeds)
    assert info["hull_reach_mm"] == cav.HULL_REACH_MM and info["sheet_tol_mm"] == cav.SHEET_TOL_MM
    assert not (lumen & plastic).any()
    assert _dice(lumen, ball) > 0.94, _dice(lumen, ball)
    v_ball = ball.sum() / 1000.0
    assert 0.9 * v_ball < info["volume_ml"] < 1.1 * truth.sum() / 1000.0
    assert info["seeds_inside"] == info["n_seeds"] == len(seeds)
    # the closed shell (no mouth) gives the same cavity
    plastic_c, truth_c = _block(mouth=False)
    lumen_c, info_c = cav.printed_lumen(plastic_c, AFF, seeds)
    assert _dice(lumen_c, truth_c) > 0.94
    assert abs(info_c["volume_ml"] - info["volume_ml"]) < 0.1 * info["volume_ml"]


def test_printed_lumen_needs_the_implant_and_fails_loudly():
    plastic, _truth = _block(mouth=True)
    with pytest.raises(ValueError, match="at least 4 seed"):
        cav.printed_lumen(plastic, AFF, np.zeros((3, 3)))
    with pytest.raises(ValueError, match="at least 4 seed"):
        cav.printed_lumen(plastic, AFF, None)
    # seeds in the air far outside the shell: nothing grows from inside plastic
    far = np.array([[30.0, 30.0, 30.0], [31.0, 29.0, 30.0], [30.0, 31.0, 29.0], [29.5, 30.5, 31.0]])
    with pytest.raises(ValueError, match="not bounded by the printed wall"):
        cav.printed_lumen(plastic, AFF, far)
    with pytest.raises(ValueError):
        cav.printed_lumen(np.zeros((4, 4, 4), bool), AFF, _implant())


def test_wall_mesh_is_the_plastic_bordering_the_cavity():
    plastic, _truth = _block(mouth=True)
    lumen, _info = cav.printed_lumen(plastic, AFF, _implant())
    wall, info = cav._lumen_wall_mesh(lumen, plastic, AFF)
    assert info["n_faces_wall"] > 0 and info["n_faces_wall"] <= info["n_faces_bordering_cavity"]
    assert info["n_faces_bordering_cavity"] < info["n_faces_body_surface"]
    assert info["reach_mm"] == cav.PHANTOM_WALL_REACH_MM
    c = wall.triangles_center
    n = np.asarray(wall.face_normals)
    # every wall face sits on the plastic surface with its normal into the
    # plastic: plastic 1 mm along the normal, cavity 1 mm the other way
    plastic_v = Volume(plastic.astype(np.float32), AFF)
    lumen_v = Volume(lumen.astype(np.float32), AFF)
    assert (plastic_v.sample_ras(c + cav.PHANTOM_LID_PROBE_MM * n) >= 0.5).mean() > 0.95
    assert (lumen_v.sample_ras(c - cav.PHANTOM_LID_PROBE_MM * n) >= 0.5).mean() > 0.9
    # no face in the open air of the channel or on the block's exterior
    rho = np.linalg.norm(c[:, :2], axis=1)
    assert not ((rho < MOUTH_R - 1.5) & (c[:, 2] > np.sqrt(R_IN ** 2 - MOUTH_R ** 2) + 1.0)).any()
    # (the channel's rim at the block surface is wall; nothing else out there)
    assert (np.linalg.norm(c, axis=1) > R_OUT - 2.0).mean() < 0.03
    # about the ball's surface (plus whatever channel wall the cavity reaches)
    analytic = 4.0 * np.pi * R_IN ** 2
    assert 0.85 * analytic < wall.area < 1.15 * (analytic + 2.0 * np.pi * MOUTH_R * 9.4), wall.area
    # seeds 3 mm off the true wall read 3 mm off this one
    seeds = _implant()
    _c, dist, _t = trimesh.proximity.ProximityQuery(wall).on_surface(seeds)
    assert abs(np.median(dist) - 3.0) < 0.3 and np.abs(dist - 3.0).max() < 1.0


# ---------------------------------------------------------- registration
def _known_transform():
    truth = sitk.Euler3DTransform()
    truth.SetCenter((0.0, 0.0, 0.0))
    truth.SetRotation(np.radians(4.0), np.radians(-3.0), np.radians(6.0))
    truth.SetTranslation((3.0, -2.5, 4.0))
    return truth


def _transformed_copy(vol, truth, fill=-1000.0):
    img = R.to_sitk(vol.array, vol.affine)
    out = sitk.Resample(img, img, truth.GetInverse(), sitk.sitkLinear, fill, sitk.sitkFloat32)
    return Volume(sitk.GetArrayFromImage(out), vol.affine, {})


def test_registration_round_trip_mesh_and_mask():
    plastic, _truth = _block(mouth=True)
    fixed = _volume(plastic)
    truth = _known_transform()
    moving = _transformed_copy(fixed, truth)
    f_mask = cav.printed_body_mask(fixed.array)
    m_mask = cav.printed_body_mask(moving.array)
    tx, info = R.register_rigid(fixed, f_mask, moving, m_mask, margin_mm=6.0)
    assert info["dice_masks"] > 0.97
    probes = np.array([[0.0, 0.0, 0.0], [20.0, -15.0, 10.0], [-18.0, 12.0, -16.0]])
    flip = np.array([-1.0, -1.0, 1.0])
    want = np.array([truth.TransformPoint(tuple(p)) for p in probes * flip]) * flip
    assert np.abs(R.transform_points_ras(tx, probes) - want).max() < 0.3
    mesh = trimesh.creation.icosphere(subdivisions=2, radius=10.0)
    carried = R.transform_mesh_ras(tx, mesh)
    assert np.allclose(carried.vertices, R.transform_points_ras(tx, mesh.vertices))
    assert np.array_equal(carried.faces, mesh.faces)
    back = R.transform_mesh_ras(tx, carried, inverse=True)
    assert np.allclose(back.vertices, mesh.vertices, atol=1e-9)
    # a moving-grid mask carried into the fixed grid lands on the fixed mask
    into_fixed = R.resample_mask_into_fixed(m_mask, moving.affine, tx, fixed.affine, fixed.array.shape)
    assert into_fixed.shape == fixed.array.shape and _dice(into_fixed, f_mask) > 0.95
    sub, A, (lo, hi) = R.crop_mask(f_mask, fixed.affine, 3.0)
    assert sub.shape == tuple(hi - lo + 1) and np.allclose(R.spacing_of(A), 1.0)
    assert np.allclose(apply_affine(A, [0, 0, 0]), apply_affine(fixed.affine, lo[::-1]))


# ---------------------------------------------------------- end to end
def test_phantom_wall_and_cavity_end_to_end():
    plastic, truth_lumen = _block(mouth=True)
    ph0 = _volume(plastic, seed=1)
    truth = _known_transform()
    # the implanted copy: PH0 under a known transform, plus collagen-like tiles
    # (3 mm layer on the lower wall, inside the lumen) that the body mask absorbs
    ph8_bare = _transformed_copy(ph0, truth)
    flip = np.array([-1.0, -1.0, 1.0])
    c8 = np.asarray(truth.TransformPoint((0.0, 0.0, 0.0))) * flip          # lumen centre, RAS
    xyz = _xyz()
    r8 = np.linalg.norm(xyz - c8, axis=-1)
    tiles = (r8 >= R_IN - 3.0) & (r8 <= R_IN + 0.5) & (xyz[..., 2] - c8[2] < -2.0)
    hu = np.asarray(ph8_bare.array, np.float32).copy()
    air = hu < -600.0
    hu[tiles & air] = -100.0
    ph8 = Volume(hu, AFF, {})
    body8 = cav.printed_body_mask(ph8.array)
    assert (body8 & tiles & air).sum() > 100, "the tiles must be part of the PH8 body mask"
    # seeds 3 mm off the TRUE wall all around the cavity (PH8 frame); the
    # lower ones sit under the tiles
    seeds8 = c8 + _implant(n=30)
    d = (seeds8 - c8) / (R_IN - 3.0)

    wall, cavity, aff, info = cav.phantom_wall_and_cavity(
        ph8, ph0, masks_ph8={"body": body8}, seed_centers_ras=seeds8)
    # the PH8 body carries the tiles the PH0 body lacks, so the mask Dice is
    # below the tile-free value; the seed-to-wall distance below is the real check
    assert info["registration"]["dice_masks"] > 0.9
    assert np.allclose(aff, AFF) and cavity.shape == ph8.array.shape
    # the wall is PH0's printed surface in the PH8 frame: seeds sit 3 mm off it,
    # although the PH8 body surface (the tile face) is at 0 mm from them
    _c, dist, _t = trimesh.proximity.ProximityQuery(wall).on_surface(seeds8)
    assert abs(np.median(dist) - 3.0) < 0.4, np.median(dist)
    assert np.abs(dist - 3.0).max() < 1.5
    lower = d[:, 2] < -0.3
    _c, dist_body, _t = trimesh.proximity.ProximityQuery(
        trimesh.Trimesh(*_surface(body8))).on_surface(seeds8[lower])
    assert np.median(dist_body) < 1.5
    # the cavity is the ball carried into the PH8 grid
    v_ball = (np.linalg.norm(_xyz(), axis=-1) <= R_IN).sum() / 1000.0
    v_true = truth_lumen.sum() / 1000.0
    assert 0.9 * v_ball < info["lumen_volume_ml_ph8_grid"] < 1.1 * v_true
    assert 0.9 * v_ball < info["lumen"]["volume_ml"] < 1.1 * v_true
    inside = Volume(cavity.astype(np.float32), aff).sample_ras(seeds8, order=0)
    assert inside.sum() == len(seeds8)
    assert info["lumen"]["seeds_inside"] == len(seeds8)
    with pytest.raises(ValueError, match="seed_centers_ras"):
        cav.phantom_wall_and_cavity(ph8, ph0, masks_ph8={"body": body8})
    # the shell is PH0's plastic in the PH8 grid; RIND5(lumen, clip shell) is plastic
    shell = info["shell_mask"]
    assert shell.shape == cavity.shape and not (shell & cavity).any()
    assert abs(shell.sum() / 1000.0 - plastic.sum() / 1000.0) / (plastic.sum() / 1000.0) < 0.1
    rind = targets.rind5(cavity, aff, clip_mask=shell, clip_affine=aff,
                         cavity_source="PH0:lumen(registered)", clip_source="PH0:shell(registered)")
    assert rind.mask.any() and not (rind.mask & ~shell).any()
    assert rind.provenance["cavity_source"] == "PH0:lumen(registered)"
    # records
    assert len(wall.faces) == info["wall"]["n_faces_wall"] > 0
    assert info["residual_mm"]["far_from_seeds"]["median"] < 1.0
    assert info["residual_mm"]["all"]["n"] > 0 and "transform_parameters" in info
    # the wall normals point out of the lumen (into the plastic), like the cavity mesh
    probe = wall.triangles_center + 1.5 * np.asarray(wall.face_normals)
    assert Volume(shell.astype(np.float32), aff).sample_ras(probe).mean() > 0.8


def _surface(mask):
    from gtcore.segment import mask_to_mesh
    m = mask_to_mesh(mask, AFF, smooth_iterations=0)
    return np.asarray(m.vertices), np.asarray(m.faces)
