"""Cavity growth distances are physical millimetres, not voxel steps.

``gtcore.segment.cavity`` grows the cavity from the seed hull with a reach
(``HULL_REACH_MM``) and pads the hull by the sheet tolerance
(``SHEET_TOL_MM``).  Both used to be ``binary_dilation(iterations=round(mm /
min(spacing)))`` -- voxel steps with a 6-connected element -- so on the
0.52 x 0.52 x 1.0 mm clinical grid a 14 mm reach ran 27 mm through-slice,
14 mm along an in-plane axis and 9.6 mm on an in-plane diagonal.  These
tests pin the replacement (``geodesic_grow_mm`` / ``dilate_mm`` in
``gtcore.segment._morph``, imported by the cavity module): a point grows to
a 14 mm sphere on an anisotropic grid, the pad is a Euclidean ball, growth
stays geodesic and 6-connected, and the isotropic synthetic phantom does
not regress.
"""
from __future__ import annotations

import numpy as np
import pytest

from gtcore.segment import cavity as cavity_mod
from gtcore.segment._morph import _geodesic_distance_mm

# the cavity module must use these, not a local voxel-step rule
geodesic_grow_mm = cavity_mod.geodesic_grow_mm
dilate_mm = cavity_mod.dilate_mm

REACH_MM = 14.0
GRIDS = [(0.5, 0.5, 2.0), (0.52, 0.52, 1.0)]     # (si, sj, sk): 2 mm slabs; the clinical CT


def _point_box(spacing, half_mm):
    """All-true box with one centre voxel set; returns (start, centre, h_kji)."""
    h = np.array([spacing[2], spacing[1], spacing[0]], dtype=float)   # array axes [k, j, i]
    shape = tuple(2 * int(np.ceil(half_mm / hh)) + 1 for hh in h)
    c = tuple(s // 2 for s in shape)
    start = np.zeros(shape, dtype=bool)
    start[c] = True
    return start, c, h


def _extents_mm(region, c, h):
    """Max extent from the centre along +k, +j, +i and along the in-plane
    (j, i) diagonal, in mm."""
    kk, jj, ii = np.nonzero(region)
    ext = ((kk.max() - c[0]) * h[0], (jj.max() - c[1]) * h[1], (ii.max() - c[2]) * h[2])
    n = min(region.shape[1] - c[1], region.shape[2] - c[2])
    t = max(t for t in range(n) if region[c[0], c[1] + t, c[2] + t])
    return ext, t * float(np.hypot(h[1], h[2]))


def _r_equivalent_mm(region, spacing):
    vol = region.sum() * float(np.prod(spacing))
    return (3.0 * vol / (4.0 * np.pi)) ** (1.0 / 3.0)


# ------------------------------------------------------------- the sphere
@pytest.mark.parametrize("spacing", GRIDS)
def test_point_grows_to_a_14mm_sphere_on_an_anisotropic_grid(spacing):
    start, c, h = _point_box(spacing, REACH_MM + 3.0)
    region = geodesic_grow_mm(start, np.ones_like(start), spacing, REACH_MM)

    (ek, ej, ei), diag = _extents_mm(region, c, h)
    r_eq = _r_equivalent_mm(region, spacing)
    msg = "extents k %.2f j %.2f i %.2f, in-plane diagonal %.2f, r_eq %.2f" % (ek, ej, ei, diag, r_eq)
    for e in (ek, ej, ei, diag, r_eq):
        assert abs(e - REACH_MM) <= 1.0, msg
    # the old rule's signature: through-slice >> in-plane
    assert abs(ek - ej) <= max(h) and abs(ek - ei) <= max(h), msg
    # symmetric about the centre on every axis
    kk, jj, ii = np.nonzero(region)
    assert kk.max() - c[0] == c[0] - kk.min()
    assert jj.max() - c[1] == c[1] - jj.min()
    assert ii.max() - c[2] == c[2] - ii.min()


@pytest.mark.parametrize("spacing", GRIDS + [(0.7, 0.7, 0.7)])
def test_geodesic_distance_tracks_euclidean_in_free_space(spacing):
    """In an all-true mask the geodesic distance IS the Euclidean distance;
    the discrete solver must track it everywhere -- not only along the axes
    -- within half the largest voxel dimension (a first-order upwind scheme
    is 10-15 % long on the oblique directions, a voxel-step dilation 40 %
    short).  Measured at the fix: 0.16 mm worst on the clinical grid, 0.19
    mm at 0.7 mm isotropic, 0.44 mm on 2 mm slabs (point source, one slab
    known, no second-order stencil yet)."""
    start, c, h = _point_box(spacing, REACH_MM + 3.0)
    _region, dist = _geodesic_distance_mm(start, np.ones_like(start), spacing, REACH_MM)
    kk, jj, ii = np.indices(start.shape)
    euclid = np.sqrt(((kk - c[0]) * h[0]) ** 2 + ((jj - c[1]) * h[1]) ** 2 + ((ii - c[2]) * h[2]) ** 2)
    sel = np.isfinite(dist) & (euclid > 0.0)
    err = dist[sel] - euclid[sel]
    half_voxel = 0.5 * max(spacing)
    assert np.abs(err).max() <= half_voxel, "worst error %.3f mm" % np.abs(err).max()
    assert abs(err.mean()) <= 0.5 * half_voxel, "mean error %.3f mm" % err.mean()
    far = euclid[sel] > 5.0
    rel = np.abs(err[far]) / euclid[sel][far]
    assert rel.max() < 0.10, "worst relative error beyond 5 mm %.3f" % rel.max()


def test_old_voxel_step_rule_was_anisotropic():
    """Documents the defect this module fixes, on the clinical grid."""
    from scipy import ndimage
    spacing = (0.52, 0.52, 1.0)
    start, c, h = _point_box(spacing, 30.0)
    iters = int(round(REACH_MM / min(spacing)))                     # 27 voxel steps
    old = ndimage.binary_dilation(start, iterations=iters)
    (ek, ej, ei), diag = _extents_mm(old, c, h)
    assert ek > 26.0 and abs(ej - 14.0) < 0.6 and diag < 10.5     # 27 / 14 / 9.6 mm


# ------------------------------------------------------------- the pad
@pytest.mark.parametrize("spacing", GRIDS)
def test_hull_pad_is_a_euclidean_ball_in_mm(spacing):
    tol = cavity_mod.SHEET_TOL_MM                                   # 3 mm
    start, c, h = _point_box(spacing, tol + 2.0)
    pad = dilate_mm(start, spacing, tol)
    kk, jj, ii = np.indices(start.shape)
    euclid = np.sqrt(((kk - c[0]) * h[0]) ** 2 + ((jj - c[1]) * h[1]) ** 2 + ((ii - c[2]) * h[2]) ** 2)
    # exactly the voxel centres within tol: no voxel-step overshoot through-slice
    assert np.array_equal(pad, euclid <= tol)
    (ek, ej, ei), _diag = _extents_mm(pad, c, h)
    assert ek <= tol and ej <= tol and ei <= tol
    assert ek > tol - h[0] and ej > tol - h[1] and ei > tol - h[2]
    # the old rule on the clinical grid padded round(3 / 0.52) = 6 slices = 6 mm
    old_vox = int(round(tol / min(spacing)))
    assert old_vox * h[0] > tol + 1.0 or spacing[2] <= min(spacing)


def test_pad_and_grow_degenerate_inputs():
    z = np.zeros((4, 5, 6), dtype=bool)
    assert not dilate_mm(z, (1.0, 1.0, 1.0), 3.0).any()
    assert not geodesic_grow_mm(z, np.ones_like(z), (1.0, 1.0, 1.0), 3.0).any()
    s = z.copy()
    s[2, 2, 3] = True
    assert np.array_equal(geodesic_grow_mm(s, np.ones_like(s), (1.0, 1.0, 1.0), 0.0), s)
    assert np.array_equal(dilate_mm(s, (1.0, 1.0, 1.0), 0.0), s)
    # start voxels are always part of the region even when outside the mask
    assert geodesic_grow_mm(s, z, (1.0, 1.0, 1.0), 5.0)[2, 2, 3]
    assert geodesic_grow_mm(s, z, (1.0, 1.0, 1.0), 5.0).sum() == 1
    with pytest.raises(ValueError):
        geodesic_grow_mm(s, np.ones((4, 5, 7), bool), (1.0, 1.0, 1.0), 5.0)


# ------------------------------------------------------------- geodesic + topology
def test_growth_is_geodesic_and_six_connected():
    """A wall with a far gap: the voxel 10 mm straight across it is not
    reached at 14 mm (the detour is ~21 mm) but is at 30 mm; a one-voxel
    wall and a diagonal crack are never crossed."""
    spacing = (0.5, 0.5, 0.5)
    shape = (21, 41, 41)
    mask = np.ones(shape, dtype=bool)
    mask[:, :, 20] = False          # wall at i = 20 ...
    mask[:, 38:, 20] = True         # ... open at j >= 38
    start = np.zeros(shape, dtype=bool)
    start[10, 20, 10] = True        # 5 mm this side of the wall
    behind = (10, 20, 30)           # 5 mm beyond it: 10 mm straight line
    assert not geodesic_grow_mm(start, mask, spacing, REACH_MM)[behind]
    region, dist = _geodesic_distance_mm(start, mask, spacing, 30.0)
    assert region[behind]
    detour = 2.0 * np.hypot(18 * 0.5, 10 * 0.5)      # via (j=38, i=20) and back
    assert abs(dist[behind] - detour) < 0.05 * detour, "%.2f vs %.2f" % (dist[behind], detour)
    # the wall itself is never entered
    assert not (region & ~mask & ~start).any()

    # diagonal crack: two wall voxels touching only along an edge
    shape = (5, 9, 9)
    mask = np.ones(shape, dtype=bool)
    mask[:, 4, :4] = False
    mask[:, 3, 4:] = False
    start = np.zeros(shape, dtype=bool)
    start[2, 1, 4] = True
    grown = geodesic_grow_mm(start, mask, (1.0, 1.0, 1.0), 50.0)
    assert not grown[2, 7, 4], "growth leaked through a diagonal crack (26-connected)"


# ------------------------------------------------------------- phantom non-regression
@pytest.fixture(scope="module")
def phantom_case():
    from gtcore.phantom import make_head_phantom
    from gtcore.preprocess import inpaint_metal
    from gtcore.seeds import detect_seed_candidates
    from gtcore.segment import segment_head
    vol, truth = make_head_phantom()            # 0.7 mm isotropic, 3 tiles, rng 0
    cands = detect_seed_candidates(vol)
    clean = inpaint_metal(vol, cands.mask)
    masks = segment_head(clean, metal_mask=cands.mask)
    return vol, truth, cands, clean, masks


def _dice(a, b):
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    return 2.0 * np.logical_and(a, b).sum() / max(a.sum() + b.sum(), 1)


def test_isotropic_synthetic_phantom_does_not_regress(phantom_case):
    """The 0.7 mm isotropic synthetic phantom through the demo / integration
    path at the default reach.  Voxel-step growth at aff83c0 gave Dice 0.839
    (22.8 cc of the 26.3 cc truth); the physical-mm geodesic must not fall
    below that, and the reach must still bound the volume (no leak)."""
    vol, truth, cands, clean, masks = phantom_case
    cav = cavity_mod.segment_cavity(clean, masks["cranial_interior"], masks["brain"],
                                    cands.centers_ras)
    d = _dice(cav, truth.masks["cavity"])
    assert d >= 0.83, "cavity dice %.3f" % d
    vmm3 = float(np.prod(vol.spacing))
    truth_cc = truth.masks["cavity"].sum() * vmm3 / 1000.0
    cav_cc = cav.sum() * vmm3 / 1000.0
    assert 0.7 * truth_cc < cav_cc < 1.3 * truth_cc, "cavity %.1f cc vs truth %.1f cc" % (cav_cc, truth_cc)


def test_reach_is_monotone_on_the_phantom(phantom_case):
    """More reach, more cavity -- and 14 mm must not already saturate."""
    vol, truth, cands, clean, masks = phantom_case
    vols = []
    for reach in (10.0, 14.0, 18.0):
        cav = cavity_mod.segment_cavity(clean, masks["cranial_interior"], masks["brain"],
                                        cands.centers_ras, hull_reach_mm=reach)
        vols.append(int(cav.sum()))
    assert vols[0] < vols[1] < vols[2], vols
