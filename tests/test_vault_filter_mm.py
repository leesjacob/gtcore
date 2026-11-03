"""Vault filter in physical millimetres (gtcore.pipeline).

A seed candidate is kept when its Euclidean distance to the segmented
cranial interior is at most ``VAULT_MARGIN_MM``, measured in mm on the
volume's own spacing, so the margin means the same thing in-plane and
through-slice.  The rule it replaces, ``binary_dilation(cranial_interior,
iterations=2)``, was two VOXELS (1 mm in-plane / 2 mm through-slice on the
clinical grid) and dropped a contoured seed sitting 2.1 mm outside the
interior under the craniotomy (docs/clinical-audit-2026-10-08.md on the
notes/clinical-audit branch).
"""
from __future__ import annotations

import contextlib
from unittest import mock

import numpy as np
import pytest
from scipy import ndimage

import gtcore.pipeline as pl
from gtcore.phantom import make_head_phantom
from gtcore.pipeline import (
    VAULT_MARGIN_MM,
    in_cranial_vault,
    reconstruct,
    seed_vault_distance_mm,
)
from gtcore.volume import Volume

SPACING = (0.5, 0.5, 2.0)       # (si, sj, sk): 0.5 mm in-plane, 2 mm slices
AXIS_NAME = {0: "i-in-plane", 1: "j-in-plane", 2: "k-through-slice"}


def _grid():
    """80 x 80 x 40 anisotropic volume holding a 20 x 20 x 40 mm interior box."""
    affine = np.diag([SPACING[0], SPACING[1], SPACING[2], 1.0])
    affine[:3, 3] = [-20.0, -20.0, -40.0]
    vol = Volume(np.zeros((40, 80, 80), np.float32), affine)
    interior = np.zeros(vol.array.shape, dtype=bool)
    interior[10:30, 20:60, 20:60] = True              # [k, j, i]
    return vol, interior


def _face_seed(vol, interior, axis, sign, offset_mm):
    """RAS point ``offset_mm`` beyond the interior's outermost voxel centre,
    through the middle of one face, along index axis ``axis`` (0=i, 1=j, 2=k).
    The affine is diagonal, so RAS axis ``axis`` is index axis ``axis``."""
    ijk = np.argwhere(interior)[:, ::-1]
    lo, hi = ijk.min(axis=0), ijk.max(axis=0)
    c = (lo + hi) / 2.0
    c[axis] = hi[axis] if sign > 0 else lo[axis]
    p = vol.index_to_ras(c)
    p[axis] += sign * offset_mm
    return p


def test_margin_is_a_named_physical_constant():
    assert VAULT_MARGIN_MM == 4.0


@pytest.mark.parametrize("sign", [+1, -1], ids=["plus", "minus"])
@pytest.mark.parametrize("axis", [0, 1, 2], ids=lambda a: AXIS_NAME[a])
def test_kept_at_3p5_mm_dropped_at_4p5_mm_on_every_axis(axis, sign):
    """3.5 mm outside is kept and 4.5 mm outside is dropped whether the
    offset is in-plane (7 and 9 voxels) or through-slice (1.75 and 2.25
    voxels -- sub-voxel, where a nearest-voxel lookup would read 4.0 for
    both)."""
    vol, interior = _grid()
    pts = np.vstack([_face_seed(vol, interior, axis, sign, 3.5),
                     _face_seed(vol, interior, axis, sign, 4.5)])
    d = seed_vault_distance_mm(interior, vol, pts)
    assert d == pytest.approx([3.5, 4.5], abs=1e-6)
    assert in_cranial_vault(interior, vol, pts).tolist() == [True, False]


def test_inside_the_interior_reads_zero_and_is_kept():
    vol, interior = _grid()
    centre = vol.index_to_ras(np.argwhere(interior)[:, ::-1].mean(axis=0))
    assert seed_vault_distance_mm(interior, vol, centre) == pytest.approx([0.0])
    assert in_cranial_vault(interior, vol, centre).tolist() == [True]


def test_two_voxel_rule_was_anisotropic_and_the_mm_rule_is_not():
    """The defect being fixed: two voxels is 1 mm in-plane but 4 mm
    through-slice on this grid, so the old rule dropped a seed 2 mm outside
    in-plane while keeping one 4 mm outside through-slice."""
    vol, interior = _grid()
    old = ndimage.binary_dilation(interior, iterations=2)

    def old_keep(p):
        kji = np.round(vol.ras_to_index(p)[::-1]).astype(int)
        return bool(old[tuple(kji)])

    in_plane_2mm = _face_seed(vol, interior, 0, +1, 2.0)
    through_4mm = _face_seed(vol, interior, 2, +1, 4.0)
    assert not old_keep(in_plane_2mm)
    assert old_keep(through_4mm)
    pts = np.vstack([in_plane_2mm, through_4mm])
    assert seed_vault_distance_mm(interior, vol, pts) == pytest.approx([2.0, 4.0])
    assert in_cranial_vault(interior, vol, pts).tolist() == [True, True]


def test_cropped_transform_matches_the_full_grid_transform():
    """The helper crops the EDT to each call's candidate box.  For tight
    clusters anywhere in the grid every distance up to the margin, and every
    keep decision, must equal the full-grid transform sampled the same way;
    beyond the margin the cropped value may only be larger."""
    vol, interior = _grid()
    sp = np.asarray(vol.spacing)[::-1]
    full = ndimage.distance_transform_edt(~interior, sampling=sp)
    rng = np.random.default_rng(0)
    n_near = n_far = 0
    for _ in range(60):
        centre = rng.uniform([2, 2, 1], [77, 77, 38])            # (i, j, k)
        ijk = centre + rng.uniform(-3.0, 3.0, size=(3, 3)) / np.asarray(vol.spacing)
        ijk = np.clip(ijk, 0, np.asarray(vol.shape_ijk) - 1)
        pts = vol.index_to_ras(ijk)
        ref = ndimage.map_coordinates(full, ijk[:, ::-1].T, order=1, mode="nearest")
        got = seed_vault_distance_mm(interior, vol, pts)
        near = ref <= VAULT_MARGIN_MM
        assert got[near] == pytest.approx(ref[near], abs=1e-9)
        assert np.all(got >= ref - 1e-9)
        assert np.array_equal(got <= VAULT_MARGIN_MM, near)
        assert np.array_equal(in_cranial_vault(interior, vol, pts), near)
        n_near += int(near.sum())
        n_far += int((~near).sum())
    assert n_near > 20 and n_far > 20


def test_far_from_every_interior_voxel_is_dropped():
    vol, interior = _grid()
    corner = vol.index_to_ras(np.array([2.0, 2.0, 1.0]))          # > 9 mm away
    d = seed_vault_distance_mm(interior, vol, corner)
    assert d[0] > VAULT_MARGIN_MM                                  # upper bound, may be inf
    assert in_cranial_vault(interior, vol, corner).tolist() == [False]


def test_no_candidates_gives_an_empty_mask():
    vol, interior = _grid()
    assert in_cranial_vault(interior, vol, np.zeros((0, 3))).shape == (0,)


# ------------------------------------------------------------ reconstruct()
@pytest.fixture(scope="module")
def phantom():
    return make_head_phantom(spacing=1.0, n_tiles=3)[0]


def test_reconstruct_applies_the_filter_and_records_the_margin(phantom):
    vol = Volume(phantom.array, phantom.affine, dict(phantom.meta))
    res = reconstruct(vol, verbose=False)
    vf = vol.meta["vault_filter"]
    assert vf["applied"] is True
    assert vf["margin_mm"] == VAULT_MARGIN_MM
    assert vf["interior_ml"] >= 300.0
    assert vf["n_dropped"] == 0
    assert len(res.seeds) == 12


def test_reconstruct_fails_open_without_a_credible_vault(phantom):
    """Empty head masks (a skull-less printed phantom): the filter is skipped
    with a warning, nothing is dropped, and the meta still names the margin."""
    vol = Volume(phantom.array, phantom.affine, dict(phantom.meta))

    def head(v, metal_mask=None):
        z = np.zeros(v.array.shape, dtype=bool)
        return dict(body=z, skull=z, cranial_interior=z, brain=z)

    with contextlib.ExitStack() as st:
        st.enter_context(mock.patch.object(pl, "segment_head", head))
        st.enter_context(mock.patch.object(
            pl, "segment_cavity", lambda v, *a, **k: np.zeros(v.array.shape, bool)))
        with pytest.warns(UserWarning, match="vault filter skipped"):
            res = reconstruct(vol, verbose=False)
    vf = vol.meta["vault_filter"]
    assert vf["applied"] is False
    assert vf["n_dropped"] == 0
    assert vf["margin_mm"] == VAULT_MARGIN_MM
    assert len(res.seeds) == 12
