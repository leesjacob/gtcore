"""gtcore.dose.targets: one definition of every scoring structure.

- ``rind5`` is the Euclidean rind in physical mm: on a 0.5 x 0.5 x 2 mm grid
  the band is exactly (R, R+5] along every axis, the volume matches the
  analytic shell up to the voxel-boundary bias of a centre-to-centre distance
  transform (measured -4.8 %, see the test), nothing inside the cavity;
- ``implant_reach`` is a subset of the rind, the whole rind for r -> inf,
  empty for r -> 0, and depends on the seed centres only;
- ``s5`` is ``plan.TargetSet.from_shell``;
- ``dice`` / ``coverage`` on known masks, across grids;
- every provenance dict is JSON-serialisable and carries the contract keys;
- ``build_hrctv`` with a cavity mask IS ``rind5`` on the rind grid and says
  ``cavity_source="gtcore:cavity"``; without one the fallback is flagged
  ``"mesh"``; ``metrics.rind_mask`` is a wrapper on ``rind5``.
"""
from __future__ import annotations

import json

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from gtcore.dose import metrics
from gtcore.dose.hrctv import HRCTV, build_hrctv
from gtcore.dose.targets import (Target, coverage, dice, hrctv_phys, implant_reach,
                                 rind5, s5, same_grid, target_doses_exact,
                                 target_doses_from_grid, target_stats, volume_cc)
from gtcore.volume import Volume

R_MM = 12.0
DEPTH_MM = 5.0
SHELL_CC = 4.0 / 3.0 * np.pi * ((R_MM + DEPTH_MM) ** 3 - R_MM ** 3) / 1000.0


def _grid(spacing, extent_mm):
    """Axis-aligned grid of voxel centres symmetric about the origin:
    ``(affine, xyz [k, j, i, 3], r [k, j, i])``."""
    sp = np.asarray(spacing, dtype=float)
    half = np.ceil(extent_mm / sp[::-1]).astype(int)
    n = 2 * half + 1                                    # (nk, nj, ni), odd: a centre at 0
    lo = -half[::-1] * sp                               # (x, y, z)
    aff = np.diag(list(sp) + [1.0])
    aff[:3, 3] = lo
    k, j, i = np.mgrid[:n[0], :n[1], :n[2]]
    xyz = np.stack([i * sp[0] + lo[0], j * sp[1] + lo[1], k * sp[2] + lo[2]], axis=-1)
    return aff, xyz, np.linalg.norm(xyz, axis=-1)


@pytest.fixture(scope="module")
def aniso():
    """Digital sphere of radius R on a 0.5 x 0.5 x 2 mm grid."""
    aff, xyz, r = _grid((0.5, 0.5, 2.0), R_MM + DEPTH_MM + 4.0)
    return aff, xyz, r, r <= R_MM


@pytest.fixture(scope="module")
def iso1():
    """Digital sphere of radius 15 on a 1 mm grid (matches the icosphere)."""
    aff, xyz, r = _grid((1.0, 1.0, 1.0), 15.0 + DEPTH_MM + 4.0)
    return aff, xyz, r, r <= 15.0


@pytest.fixture(scope="module")
def sphere():
    return trimesh.creation.icosphere(subdivisions=4, radius=15.0)


# ------------------------------------------------------------------ (a) RIND5
def test_rind5_is_the_physical_mm_shell_of_a_digital_sphere(aniso):
    aff, xyz, r, cav = aniso
    t = rind5(cav, aff, depth_mm=DEPTH_MM)
    assert t.name == "RIND5" and t.mask.shape == cav.shape and t.n > 0
    assert np.allclose(t.affine, aff)
    # nothing inside the cavity
    assert not (t.mask & cav).any()
    # the band is exactly the centres in (R, R+5] along each axis (voxel
    # steps would give 2.5 x 2 mm = 5 voxels = 10 mm along z)
    kc, jc, ic = (s // 2 for s in cav.shape)
    x = xyz[kc, jc, :, 0]
    z = xyz[:, jc, ic, 2]
    on_x = x[t.mask[kc, jc, :]]
    on_z = z[t.mask[:, jc, ic]]
    assert np.all((np.abs(on_x) > R_MM) & (np.abs(on_x) <= R_MM + DEPTH_MM))
    assert np.all((np.abs(on_z) > R_MM) & (np.abs(on_z) <= R_MM + DEPTH_MM))
    assert on_x.size == np.count_nonzero((np.abs(x) > R_MM) & (np.abs(x) <= R_MM + DEPTH_MM))
    assert on_z.size == np.count_nonzero((np.abs(z) > R_MM) & (np.abs(z) <= R_MM + DEPTH_MM))
    # every rind voxel centre is between the surface and the surface + depth
    # (+ a voxel diagonal: the nearest cavity centre is not on the surface)
    diag = 0.5 * np.linalg.norm([0.5, 0.5, 2.0])
    rr = r[t.mask]
    assert rr.min() > R_MM and rr.max() <= R_MM + DEPTH_MM + diag
    # volume: the analytic shell up to the centre-to-centre bias of the
    # distance transform (the digital sphere's boundary centres lie inside
    # the analytic surface, so the rind is systematically ~0.2 mm thin:
    # measured -4.8 % here, -5.2 % at 1 mm isotropic, -2.2 % at 0.5 mm
    # isotropic; the task's 3 % figure is reached only on <= 0.5 mm grids)
    assert abs(t.volume_cc - SHELL_CC) / SHELL_CC < 0.06
    assert t.volume_cc < SHELL_CC                       # the bias is one-sided
    # agreement with the exactly voxelised continuous rind
    ref = Target("RIND5", (r > R_MM) & (r <= R_MM + DEPTH_MM), aff)
    assert dice(ref, t) > 0.96                          # measured 0.969
    assert volume_cc(t) == pytest.approx(t.n * 0.5 * 0.5 * 2.0 / 1000.0)


def test_rind5_clip_mask_removes_voxels(aniso):
    aff, xyz, r, cav = aniso
    full = rind5(cav, aff, depth_mm=DEPTH_MM)
    half = xyz[..., 0] > 0.0
    clipped = rind5(cav, aff, clip_mask=half, clip_affine=aff, depth_mm=DEPTH_MM,
                    clip_source="gtcore:brain")
    assert clipped.n < full.n
    assert np.array_equal(clipped.mask, full.mask & half)
    assert clipped.provenance["clip_source"] == "gtcore:brain"
    # a clip mask on the cavity grid needs no affine; an unlabelled one is said so
    same = rind5(cav, aff, clip_mask=half, depth_mm=DEPTH_MM)
    assert np.array_equal(same.mask, clipped.mask)
    assert same.provenance["clip_source"] == "unlabelled"
    assert full.provenance["clip_source"] is None


def test_rind5_on_another_grid_resamples_first(aniso):
    aff, xyz, r, cav = aniso
    # a 1 mm isotropic output grid, offset by 0.3 mm, covering the same box
    g_aff = np.eye(4)
    g_aff[:3, 3] = xyz[0, 0, 0] + 0.3
    g_shape = (43, 43, 43)
    t = rind5(cav, aff, depth_mm=DEPTH_MM, grid_affine=g_aff, grid_shape=g_shape)
    assert t.mask.shape == g_shape and np.allclose(t.affine, g_aff)
    assert t.provenance["grid"]["shape"] == list(g_shape)
    assert t.provenance["grid"]["spacing_mm"] == [1.0, 1.0, 1.0]
    # == resample the cavity nearest-neighbour, then the distance transform
    grid = Volume(np.zeros(g_shape, dtype=np.uint8), g_aff)
    cav_g = metrics.resample_mask_to(grid, cav, aff)
    ref = rind5(cav_g, g_aff, depth_mm=DEPTH_MM)
    assert np.array_equal(t.mask, ref.mask)
    assert abs(t.volume_cc - SHELL_CC) / SHELL_CC < 0.08
    assert not same_grid(aff, cav.shape, g_aff, g_shape)
    assert same_grid(aff, cav.shape, aff + 1e-9, cav.shape)


def test_rind5_rejects_bad_inputs_and_handles_empty(aniso):
    aff, xyz, r, cav = aniso
    with pytest.raises(ValueError):
        rind5(cav, aff, depth_mm=0.0)
    with pytest.raises(ValueError):
        rind5(cav, aff, grid_affine=aff)                 # shape missing
    with pytest.raises(ValueError):
        rind5(cav[0], aff)                               # 2-D
    with pytest.raises(ValueError):
        rind5(cav, None)
    empty = rind5(np.zeros_like(cav), aff)
    assert empty.n == 0 and empty.volume_cc == 0.0
    assert empty.voxel_centers_ras().shape == (0, 3)
    with pytest.raises(ValueError):
        Target("RIND5", cav, None)
    with pytest.raises(ValueError):
        Target("S5", None, None)


# --------------------------------------------------------- (b) IMPLANT_REACH
def test_implant_reach_is_the_rind_within_r_of_the_seed_centres(aniso):
    aff, xyz, r, cav = aniso
    rind = rind5(cav, aff, depth_mm=DEPTH_MM, cavity_source="RTSTRUCT:Cav_Post")
    # three "seeds" just outside the wall on the +x side, off voxel centres
    seeds = np.array([[R_MM + 2.3, 0.1, 0.2], [R_MM + 2.1, 3.1, -0.4],
                      [R_MM + 2.2, -2.9, 0.6]])
    far = implant_reach(rind, seeds, 1e6)
    none = implant_reach(rind, seeds, 0.0)
    near = implant_reach(rind, seeds, 6.0)
    assert np.array_equal(far.mask, rind.mask)
    assert none.n == 0
    assert 0 < near.n < rind.n
    assert not (near.mask & ~rind.mask).any()            # subset of the rind
    # the definition, brute force: every rind voxel within r of the nearest
    # seed centre is in, every other rind voxel is out
    c = rind.voxel_centers_ras()
    d = np.min(np.linalg.norm(c[:, None, :] - seeds[None, :, :], axis=2), axis=1)
    expect = np.zeros_like(rind.mask)
    expect[rind.mask] = d <= 6.0
    assert np.array_equal(near.mask, expect)
    # seeds, not footprints: the reach of a seed set is the union of the
    # single-seed reaches (distance to the nearest centre, nothing else)
    union = np.zeros_like(rind.mask)
    for s in seeds:
        union |= implant_reach(rind, s[None, :], 6.0).mask
    assert np.array_equal(near.mask, union)
    # a seed on the far side lights up the far side only
    opposite = implant_reach(rind, np.array([[-(R_MM + 2.3), 0.1, 0.2]]), 6.0)
    assert opposite.n > 0 and not (opposite.mask & near.mask).any()
    assert np.all(opposite.voxel_centers_ras()[:, 0] < 0.0)
    # provenance: inherits the rind's, adds r_mm and n_seeds
    p = near.provenance
    assert p["r_mm"] == 6.0 and p["n_seeds"] == 3
    assert p["cavity_source"] == "RTSTRUCT:Cav_Post" and p["depth_mm"] == DEPTH_MM
    assert p["builder"] == "implant_reach" and p["rind_builder"] == "rind5"
    assert near.name == "IMPLANT_REACH"
    with pytest.raises(ValueError):
        implant_reach(rind, seeds, -1.0)
    assert implant_reach(rind, np.zeros((0, 3)), 6.0).n == 0


# -------------------------------------------------------------------- (c) S5
def test_s5_matches_plan_targetset_from_shell(sphere):
    from gtcore.plan import TargetSet
    t = s5(sphere, 5.0)
    ts = TargetSet.from_shell(sphere, 5.0)
    assert t.name == "S5" and t.mask is None and t.affine is None
    assert np.array_equal(t.points, ts.points)
    assert np.array_equal(t.weights, ts.weights)
    assert t.n == len(ts) == len(sphere.vertices)
    # area weights: the +5 mm shell of a 15 mm sphere
    assert t.total_weight == pytest.approx(4.0 * np.pi * 20.0 ** 2, rel=0.02)
    assert np.isnan(t.volume_cc) and t.voxel_volume_mm3 is None
    assert np.array_equal(t.voxel_centers_ras(), ts.points)
    assert t.provenance["cavity_source"] == "mesh" and t.provenance["grid"] is None
    assert t.provenance["depth_mm"] == 5.0
    with pytest.raises(ValueError):
        t.as_volume()
    with pytest.raises(ValueError):
        implant_reach(t, np.zeros((1, 3)), 5.0)
    assert "S5" in t.describe() and "points" in t.describe()


# ------------------------------------------------------ (d) dice / coverage
def test_dice_and_coverage_on_known_masks():
    n = 20
    aff1 = np.eye(4)                                    # 1 mm, centres at integers
    k, j, i = np.mgrid[:n, :n, :n]
    a = Target("HRCTV_PHYS", (i >= 0) & (i < 10), aff1)
    b = Target("RIND5", (i >= 5) & (i < 15), aff1)
    assert dice(a, b) == pytest.approx(0.5)
    assert coverage(a, b) == pytest.approx(0.5)          # half of b lies in a
    assert coverage(b, a) == pytest.approx(0.5)
    assert dice(a, a) == 1.0 and coverage(a, a) == 1.0
    # b on a 0.5 mm grid (centres at multiples of 0.5): resampled onto a's grid
    aff2 = np.diag([0.5, 0.5, 0.5, 1.0])
    k2, j2, i2 = np.mgrid[:2 * n, :2 * n, :2 * n]
    x2 = i2 * 0.5
    b2 = Target("RIND5", (x2 >= 5.0) & (x2 < 15.0), aff2)
    assert dice(a, b2) == pytest.approx(0.5)
    assert coverage(a, b2) == pytest.approx(0.5)
    # the other way round `a` is resampled onto the 0.5 mm grid, where its
    # voxel edges fall on half-integer ties of the nearest-neighbour lookup:
    # the result is grid-dependent (0.45 here), which is why `dice(a, b)`
    # always resamples onto the FIRST argument's grid and the tables say so
    assert 0.4 < dice(b2, a) < 0.5
    # empties
    e = Target("RIND5", np.zeros((n, n, n), bool), aff1)
    assert np.isnan(dice(e, e)) and np.isnan(coverage(a, e))
    assert dice(a, e) == 0.0 and coverage(e, a) == 0.0
    assert volume_cc(a) == pytest.approx(10 * n * n / 1000.0)


# ------------------------------------------------------------ (e) provenance
def test_provenance_is_json_serialisable_with_the_contract_keys(aniso, sphere):
    aff, xyz, r, cav = aniso
    half = xyz[..., 0] > 0.0
    rind = rind5(cav, aff, clip_mask=half, clip_affine=aff, depth_mm=DEPTH_MM,
                 cavity_source="gtcore:cavity", clip_source="gtcore:cranial_interior")
    reach = implant_reach(rind, np.array([[R_MM + 2.0, 0.0, 0.0]]), 12.0)
    phys = hrctv_phys(cav, aff, roi_name="CTV_GT")
    shell = s5(sphere, 5.0)
    for t in (rind, reach, phys, shell):
        text = json.dumps(t.provenance)              # must not raise
        back = json.loads(text)
        for key in ("cavity_source", "clip_source", "grid", "depth_mm", "git_commit",
                    "builder", "name"):
            assert key in back, (t.name, key)
        assert back["git_commit"] is None or isinstance(back["git_commit"], str)
        assert back["name"] == t.name
    g = rind.provenance["grid"]
    assert g["spacing_mm"] == [0.5, 0.5, 2.0]
    assert g["shape"] == list(cav.shape)
    assert np.allclose(g["origin_ras"], aff[:3, 3])
    assert rind.provenance["cavity_source"] == "gtcore:cavity"
    assert rind.provenance["clip_source"] == "gtcore:cranial_interior"
    assert rind.provenance["depth_mm"] == 5.0 and rind.provenance["builder"] == "rind5"
    assert reach.provenance["r_mm"] == 12.0 and reach.provenance["n_seeds"] == 1
    assert phys.provenance["roi_name"] == "CTV_GT" and phys.name == "HRCTV_PHYS"
    assert phys.provenance["cavity_source"] is None and phys.provenance["depth_mm"] is None
    assert shell.provenance["grid"] is None
    # the provenance travels with the exported Volume
    v = rind.as_volume()
    assert v.meta["cavity_source"] == "gtcore:cavity" and v.array.dtype == np.uint8
    assert np.array_equal(v.array.astype(bool), rind.mask)
    assert "RIND5" in rind.describe() and "clip=gtcore:cranial_interior" in rind.describe()


# ------------------------------------------------------- (f) build_hrctv path
def test_build_hrctv_with_a_cavity_mask_is_rind5_on_the_rind_grid(sphere, iso1):
    aff, xyz, r, cav = iso1
    keep = xyz[..., 0] > -3.0
    h = build_hrctv(sphere, depth_mm=5.0, spacing_mm=1.0, inside_mask=cav,
                    inside_affine=aff, keep_mask=keep, keep_affine=aff,
                    keep_source="gtcore:brain", with_mesh=True)
    assert isinstance(h, HRCTV) and isinstance(h.target, Target)
    ref = rind5(cav, aff, clip_mask=keep, clip_affine=aff, depth_mm=5.0,
                grid_affine=h.affine, grid_shape=h.mask.shape)
    assert np.array_equal(h.mask, ref.mask)
    assert h.target.mask is h.mask and np.allclose(h.target.affine, h.affine)
    p = h.target.provenance
    assert p["cavity_source"] == "gtcore:cavity" and p["clip_source"] == "gtcore:brain"
    assert p["builder"] == "rind5" and p["depth_mm"] == 5.0
    assert p["grid"]["shape"] == list(h.mask.shape) and p["grid"]["spacing_mm"] == [1.0] * 3
    assert "fallback" not in h.source and "fallback" not in h.describe()
    assert h.provenance == p
    # the volume is the one RIND5 gives, outside the cavity, clipped
    pts = h.voxel_centers_ras()
    rr = np.linalg.norm(pts, axis=1)
    assert rr.min() > 15.0 and np.all(pts[:, 0] > -3.0)
    expect = 4.0 / 3.0 * np.pi * (20.0 ** 3 - 15.0 ** 3) / 1000.0
    assert 0.5 * expect < h.volume_cc < expect
    # display mesh: the far surface of the RIND5 mask
    assert h.mesh is not None and len(h.mesh.faces)
    rv = np.linalg.norm(np.asarray(h.mesh.vertices), axis=1)
    assert np.percentile(rv, 5) > 17.5 and np.percentile(rv, 95) < 21.5
    # with_mesh=False skips the shell but not the target
    h2 = build_hrctv(sphere, inside_mask=cav, inside_affine=aff, with_mesh=False)
    assert h2.mesh is None and np.array_equal(
        h2.mask, rind5(cav, aff, grid_affine=h2.affine, grid_shape=h2.mask.shape).mask)
    assert h2.target.provenance["clip_source"] is None


def test_build_hrctv_without_a_cavity_mask_is_the_flagged_mesh_fallback(sphere, iso1):
    aff, xyz, r, cav = iso1
    h = build_hrctv(sphere, depth_mm=5.0, spacing_mm=1.0, with_mesh=False)
    p = h.target.provenance
    assert p["cavity_source"] == "mesh" and p["builder"] == "build_hrctv"
    assert p["clip_source"] is None and p["side"] == 1
    assert "mesh-offset fallback" in h.source and "mesh-offset fallback" in h.describe()
    assert h.target.name == "RIND5" and h.target.mask is h.mask
    # an unlabelled keep mask is recorded as such; a labelled one keeps its label
    keep = xyz[..., 0] > 0.0
    hk = build_hrctv(sphere, keep_mask=keep, keep_affine=aff, with_mesh=False)
    assert hk.target.provenance["clip_source"] == "unlabelled"
    hk2 = build_hrctv(sphere, keep_mask=keep, keep_affine=aff, with_mesh=False,
                      keep_source="gtcore:body")
    assert hk2.target.provenance["clip_source"] == "gtcore:body"
    # the fallback still needs its masks' affines; an empty cavity is refused
    with pytest.raises(ValueError):
        build_hrctv(sphere, inside_mask=cav)
    with pytest.raises(ValueError):
        build_hrctv(sphere, inside_mask=np.zeros_like(cav), inside_affine=aff)
    # an HRCTV built by hand gets a default target carrying its grid
    plain = HRCTV(mask=h.mask, affine=h.affine, depth_mm=5.0, spacing_mm=1.0)
    assert plain.target.provenance["grid"]["shape"] == list(h.mask.shape)
    assert plain.target.provenance["cavity_source"] is None


# ------------------------------------------------- (g) metrics.rind_mask wrapper
def test_rind_mask_wrapper_equals_rind5():
    n = 41
    aff_s = np.eye(4)
    aff_s[:3, 3] = -20.0
    k, j, i = np.mgrid[:n, :n, :n]
    ball = np.sqrt((i - 20.0) ** 2 + (j - 20.0) ** 2 + (k - 20.0) ** 2) <= 8.0
    aff_d = np.diag([2.0, 2.0, 3.0, 1.0])
    aff_d[:3, 3] = [-19.5, -19.5, -19.5]
    dose = Volume(np.ones((14, 21, 21)), aff_d)
    half = np.zeros(dose.array.shape, bool)
    half[:, :, :10] = True
    old = metrics.rind_mask(dose, ball, aff_s, depth_mm=4.0, exclude_mask=half)
    new = rind5(ball, aff_s, clip_mask=~half, clip_affine=aff_d, depth_mm=4.0,
                grid_affine=aff_d, grid_shape=dose.array.shape).mask
    assert old.any() and np.array_equal(old, new)
    old0 = metrics.rind_mask(dose, ball, aff_s, depth_mm=4.0)
    new0 = rind5(ball, aff_s, depth_mm=4.0, grid_affine=aff_d,
                 grid_shape=dose.array.shape).mask
    assert np.array_equal(old0, new0) and old.sum() < old0.sum()
    # same grid, no resampling: identical to the distance transform by hand
    same = metrics.rind_mask(Volume(np.zeros((n, n, n)), aff_s), ball, aff_s, 4.0)
    from scipy import ndimage
    d = ndimage.distance_transform_edt(~ball)
    assert np.array_equal(same, (d <= 4.0) & ~ball)


# ------------------------------------------------------------ doses + stats
def test_target_doses_and_stats(aniso, sphere):
    from gtcore.dose import compute_dose_grid, dose_at_points
    aff, xyz, r, cav = aniso
    t = rind5(cav, aff, depth_mm=DEPTH_MM)
    c = np.array([[0.0, 0.0, 0.0]])
    a = np.array([[1.0, 0.0, 0.0]])
    exact = target_doses_exact(t, c, a)
    assert exact.shape == (t.n,)
    assert np.allclose(exact, dose_at_points(c, a, t.voxel_centers_ras()), rtol=1e-12)
    # the sample order is the mask's C order (np.nonzero)
    kk, jj, ii = np.nonzero(t.mask)
    ctr = t.as_volume().index_to_ras(np.stack([ii, jj, kk], 1).astype(float))
    assert np.allclose(ctr, t.voxel_centers_ras())
    grid = compute_dose_grid(c, a, [[-25.0] * 3, [25.0] * 3], spacing_mm=1.0)
    sampled = target_doses_from_grid(t, grid)
    assert sampled.shape == exact.shape
    assert np.median(np.abs(sampled - exact) / exact) < 0.02
    s = target_stats(t, exact, 6000.0)
    for key in ("D90", "D50", "Dmin", "Dmax", "Dmean", "V100", "V150", "V200",
                "volume_cc", "n", "rx_cgy"):
        assert key in s, key
    assert s["volume_cc"] == pytest.approx(t.volume_cc)
    assert s["n"] == t.n and s["target"] == "RIND5"
    assert s["provenance"]["cavity_source"] == "gtcore:cavity"
    assert s["Dmin"] <= s["D90"] <= s["D50"] <= s["Dmax"]
    with pytest.raises(ValueError):
        target_stats(t, exact[:-1], 6000.0)
    # weighted (S5) path
    sh = s5(sphere, 5.0)
    d5 = target_doses_exact(sh, c, a)
    assert d5.shape == (sh.n,)
    w = target_stats(sh, d5, 6000.0)
    assert 0.0 <= w["V100"] <= 1.0 and w["D90"] <= w["D50"]
    assert np.isnan(w["volume_cc"]) and w["target"] == "S5"
    # empty target: NaN statistics, zero volume
    e = rind5(np.zeros_like(cav), aff)
    se = target_stats(e, target_doses_exact(e, c, a), 6000.0)
    assert np.isnan(se["D90"]) and se["volume_cc"] == 0.0
