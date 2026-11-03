"""gtcore.dose.gamma (wrapper over pymedphys.gamma) on a synthetic 3-seed cloud.

The reference is computed with gtcore itself on a 1 x 1 x 1.667 mm grid whose
x axis runs *descending* with the array index, exactly like an RTDOSE after
the LPS->RAS flip; the evaluation is gtcore on the 0.5 mm lattice the wrapper
builds.  Everything here runs in a few seconds (about 3.6k reference voxels
above 30 Gy).
"""
from __future__ import annotations

import sys

import numpy as np
import pytest
from scipy.spatial import cKDTree

pytest.importorskip("pymedphys")

from gtcore.dose import dose_at_points  # noqa: E402
from gtcore.dose import gamma as gm  # noqa: E402
from gtcore.volume import Volume  # noqa: E402

CENTERS = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [5.0, 8.0, 0.0]])
AXES = np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]])
SK_U = 3.5
SHIFTED_SEED = 1
SHIFT_MM = 1.5
LOWER, UPPER, RX = 3000.0, 20000.0, 6000.0


def _voxel_centres(shape_kji, affine):
    kk, jj, ii = np.indices(shape_kji)
    ijk = np.stack([ii.ravel(), jj.ravel(), kk.ravel()], axis=1).astype(float)
    return ijk @ affine[:3, :3].T + affine[:3, 3]


@pytest.fixture(scope="module")
def reference():
    """gtcore dose on a 1 x 1 x 1.667 mm grid, x descending (RTDOSE-like)."""
    spacing = (1.0, 1.0, 1.667)
    nx, ny, nz = 44, 40, 24
    affine = np.diag([-spacing[0], spacing[1], spacing[2], 1.0])
    affine[:3, 3] = [26.0, -16.0, -19.0]
    dose = dose_at_points(CENTERS, AXES, _voxel_centres((nz, ny, nx), affine),
                          sk_per_seed_u=SK_U).reshape(nz, ny, nx)
    return Volume(dose, affine, {"units": "cGy"})


@pytest.fixture(scope="module")
def shifted_centers():
    c = CENTERS.copy()
    c[SHIFTED_SEED, 0] += SHIFT_MM
    return c


@pytest.fixture(scope="module")
def identical(reference):
    return gm.gamma_analysis(reference, CENTERS, AXES, SK_U, 3.0, 2.0)


@pytest.fixture(scope="module")
def shifted_global(reference, shifted_centers):
    return gm.gamma_analysis(reference, shifted_centers, AXES, SK_U, 3.0, 2.0)


@pytest.fixture(scope="module")
def shifted_local(reference, shifted_centers):
    return gm.gamma_analysis(reference, shifted_centers, AXES, SK_U, 3.0, 2.0, local=True)


@pytest.fixture(scope="module")
def shifted_strict(reference, shifted_centers):
    return gm.gamma_analysis(reference, shifted_centers, AXES, SK_U, 2.0, 1.0)


def _box_centres(result):
    return _voxel_centres(result.gamma.shape, result.box_affine)


# ---------------------------------------------------------------- identical
def test_identical_dose_passes_everywhere_in_the_window(identical, reference):
    assert identical.pass_rate == 1.0
    assert identical.settings["n_unresolved"] == 0
    assert identical.settings["interp_algo_used"] in ("pymedphys", "scipy")
    assert isinstance(identical.settings["pymedphys_version"], str)
    # the 30 Gy cutoff is passed to pymedphys as 50 % of the 6000 cGy normalisation
    assert identical.settings["lower_percent_dose_cutoff"] == pytest.approx(50.0)
    assert identical.settings["global_normalisation_cgy"] == RX
    assert "pymedphys.gamma(" in identical.settings["pymedphys_call"]
    # the box is a sub-grid of the reference: same voxel centres
    ref_box = reference.array[identical.box_slices]
    np.testing.assert_array_equal(ref_box, identical.reference_box.array)
    sl = identical.box_slices
    corner = reference.index_to_ras(np.array([sl[2].start, sl[1].start, sl[0].start], float))
    np.testing.assert_allclose(identical.box_affine[:3, 3], corner)


def test_difference_volume_is_zero_for_identical_seeds(identical, shifted_global):
    assert identical.difference_volume.array.shape == identical.gamma.shape
    assert np.abs(identical.difference_volume.array).max() < 1e-6
    assert np.abs(shifted_global.difference_volume.array[shifted_global.mask_used]).max() > 100.0


def test_window_and_capsule_counts(identical):
    ref = identical.reference_box.array
    np.testing.assert_array_equal(identical.mask_computed, ref >= LOWER)
    np.testing.assert_array_equal(identical.mask_used, (ref >= LOWER) & (ref <= UPPER))
    # gamma exists exactly where the reference is above the lower cutoff
    np.testing.assert_array_equal(~np.isnan(identical.gamma), identical.mask_computed)
    n_capsule = int((ref > UPPER).sum())
    assert n_capsule > 0, "the synthetic cloud must contain > 200 Gy voxels"
    assert identical.n_points_ge30 - identical.n_points == n_capsule
    assert identical.n_points == int(identical.mask_used.sum())
    assert identical.settings["n_points_above_upper"] == n_capsule
    # the capsule voxels are within a few mm of a seed (grid-averaging region)
    hot = _box_centres(identical)[(ref > UPPER).ravel()]
    d, _ = cKDTree(CENTERS).query(hot)
    assert d.max() < 4.0
    # post-hoc windows reproduce the two headline numbers
    assert identical.pass_rate_in(LOWER, UPPER) == identical.pass_rate
    assert identical.pass_rate_in(LOWER) == identical.pass_rate_ge30
    assert identical.pass_rate_lower_only == identical.pass_rate_ge30


def test_histogram_bins_cover_zero_to_two_plus(identical):
    edges, counts = identical.histogram["edges"], identical.histogram["counts"]
    assert len(edges) == 22 and edges[0] == 0.0 and edges[-2] == pytest.approx(2.0)
    assert np.isinf(edges[-1])
    assert counts.sum() == identical.n_points
    assert identical.histogram["counts_ge30"].sum() == identical.n_points_ge30
    # identical dose: everything in the lowest bins
    assert counts[:6].sum() == identical.n_points


# ------------------------------------------------------------------ shifted
def test_shift_of_one_seed_fails_near_that_seed(identical, shifted_global, shifted_strict):
    assert shifted_global.pass_rate < 1.0
    assert shifted_global.pass_rate < identical.pass_rate
    fail = shifted_global.mask_used & (shifted_global.gamma > 1.0)
    assert fail.sum() > 0
    pts = _box_centres(shifted_global)[fail.ravel()]
    _, nearest = cKDTree(CENTERS).query(pts)
    assert np.all(nearest == SHIFTED_SEED), "3 %/2 mm failures must all sit at the shifted seed"
    # 2 %/1 mm is the stricter pair: more failures, still concentrated at the shifted seed
    assert shifted_strict.pass_rate < shifted_global.pass_rate
    assert shifted_strict.pass_rate < 0.97
    fail2 = shifted_strict.mask_used & (shifted_strict.gamma > 1.0)
    _, nearest2 = cKDTree(CENTERS).query(_box_centres(shifted_strict)[fail2.ravel()])
    assert np.mean(nearest2 == SHIFTED_SEED) > 0.5
    # the shifted seed's neighbourhood carries the dose difference
    diff = np.abs(shifted_global.difference_volume.array)
    _, nearest_all = cKDTree(CENTERS).query(_box_centres(shifted_global))
    near = (nearest_all == SHIFTED_SEED).reshape(diff.shape) & shifted_global.mask_used
    far = (nearest_all != SHIFTED_SEED).reshape(diff.shape) & shifted_global.mask_used
    assert np.median(diff[near]) > np.median(diff[far])


def test_local_and_global_differ_in_the_expected_direction(shifted_global, shifted_local):
    """Local gamma normalises the dose difference to the reference voxel's own
    dose, global-to-rx to 6000 cGy.  Where the reference exceeds rx (near the
    seeds) the local tolerance is the larger one, so local gamma <= global gamma
    voxel by voxel; below rx the ordering reverses.  (The requirements text
    "global-to-rx is lenient near seeds" has it backwards; see the notes.)"""
    ref = shifted_global.reference_box.array
    both = shifted_global.mask_computed & shifted_local.mask_computed
    hi = both & (ref >= RX)
    lo = both & (ref < RX)
    assert hi.sum() > 100 and lo.sum() > 100
    assert np.all(shifted_local.gamma[hi] <= shifted_global.gamma[hi] + 1e-9)
    assert np.all(shifted_global.gamma[lo] <= shifted_local.gamma[lo] + 1e-9)
    assert shifted_local.gamma[hi].mean() < shifted_global.gamma[hi].mean()
    assert shifted_global.gamma[lo].mean() < shifted_local.gamma[lo].mean()
    assert shifted_local.settings["normalisation"] == "local"
    assert shifted_global.settings["normalisation"] == "global"
    assert shifted_local.settings["local_gamma"] is True


def test_scipy_backend_gives_the_same_gamma(reference, shifted_centers, shifted_global):
    r = gm.gamma_analysis(reference, shifted_centers, AXES, SK_U, 3.0, 2.0, interp_algo="scipy")
    assert r.settings["interp_algo_used"] == "scipy"
    assert abs(r.pass_rate - shifted_global.pass_rate) <= 0.005
    assert abs(r.pass_rate_ge30 - shifted_global.pass_rate_ge30) <= 0.005
    m = shifted_global.mask_computed
    assert np.nanmax(np.abs(r.gamma[m] - shifted_global.gamma[m])) < 1e-6


def test_upper_cutoff_post_hoc_filtering_is_exact(shifted_global):
    """Gamma per reference voxel depends only on the evaluation grid, so
    selecting the 30-200 Gy voxels after the fact must equal a run in which
    the > 200 Gy voxels were removed from the reference beforehand."""
    ref = shifted_global.reference_box.array.copy()
    ref[ref > UPPER] = np.nan                    # pre-filtered reference: never computed
    g, settings = gm.run_pymedphys_gamma(
        gm.grid_axes(shifted_global.reference_box), ref,
        gm.grid_axes(shifted_global.evaluation), shifted_global.evaluation.array,
        3.0, 2.0, LOWER, RX, local=False, max_gamma=shifted_global.settings["max_gamma"],
        interp_algo=shifted_global.settings["interp_algo_used"])
    used = shifted_global.mask_used
    np.testing.assert_array_equal(g[used], shifted_global.gamma[used])
    assert np.isnan(g[shifted_global.mask_computed & ~used]).all()
    assert gm.pass_rate(g, used) == shifted_global.pass_rate
    assert settings["lower_percent_dose_cutoff"] == pytest.approx(50.0)


# -------------------------------------------------------------- plumbing
def test_prepared_evaluation_is_reusable_and_checked(reference, shifted_centers, shifted_global):
    ev = gm.prepare_evaluation(reference, shifted_centers, AXES, SK_U, lower_cutoff_cgy=LOWER,
                               eval_spacing_mm=0.5, search_margin_mm=4.5)
    r = gm.gamma_analysis(reference, dose_pct=3.0, dist_mm=2.0, evaluation=ev)
    assert r.pass_rate == shifted_global.pass_rate
    np.testing.assert_array_equal(r.gamma, shifted_global.gamma)
    # too small a lattice for a 2 mm / max_gamma 2 search -> refused
    small = gm.prepare_evaluation(reference, shifted_centers, AXES, SK_U, lower_cutoff_cgy=LOWER,
                                  eval_spacing_mm=0.5, search_margin_mm=1.0)
    with pytest.raises(ValueError):
        gm.gamma_analysis(reference, dose_pct=3.0, dist_mm=2.0, evaluation=small)
    # the 0.5 mm lattice contains the 1 mm reference voxel centres in x and y
    zs, ys, xs = gm.grid_axes(ev.lattice)
    rz, ry, rx_ = gm.grid_axes(ev.reference_box)
    assert np.all(np.min(np.abs(rx_[:, None] - xs[None, :]), axis=1) < 1e-9)
    assert np.all(np.min(np.abs(ry[:, None] - ys[None, :]), axis=1) < 1e-9)
    assert np.all(np.diff(xs) > 0) and np.all(np.diff(ys) > 0) and np.all(np.diff(zs) > 0)
    assert np.all(np.diff(rx_) < 0), "the synthetic reference keeps its descending x axis"


def test_reference_box_margin_and_errors(reference):
    slices, box = gm.reference_box(reference, LOWER, margin_mm=5.0)
    above = np.argwhere(reference.array >= LOWER)
    lo, hi = above.min(axis=0), above.max(axis=0)
    for ax, s in enumerate(slices):
        pad = int(np.ceil(5.0 / reference.spacing[::-1][ax] - 1e-9))
        assert s.start == max(0, lo[ax] - pad)
        assert s.stop == min(reference.array.shape[ax], hi[ax] + pad + 1)
    assert box.array.shape == tuple(s.stop - s.start for s in slices)
    with pytest.raises(ValueError):
        gm.reference_box(reference, 1e9)
    oblique = Volume(reference.array, np.array([[0.9, 0.1, 0, 0], [-0.1, 0.9, 0, 0],
                                                 [0, 0, 1.667, 0], [0, 0, 0, 1]]))
    with pytest.raises(ValueError):
        gm.grid_axes(oblique)


def test_interp_algo_resolution(monkeypatch):
    assert gm.resolve_interp_algo("scipy") == "scipy"
    assert gm.resolve_interp_algo("pymedphys") == "pymedphys"
    assert gm.resolve_interp_algo("auto") in ("pymedphys", "scipy")
    with pytest.raises(ValueError):
        gm.resolve_interp_algo("cubic")
    # without numba, "auto" falls back to scipy
    monkeypatch.setitem(sys.modules, "numba", None)
    assert gm.resolve_interp_algo("auto") == "scipy"


def test_pass_rate_and_histogram_helpers():
    g = np.array([[0.2, 1.0, 1.5, np.nan], [2.0, 0.0, 0.99, 3.0]])
    m = np.ones_like(g, dtype=bool)
    assert gm.pass_rate(g, m) == pytest.approx(4 / 8)       # NaN counts as a failure
    assert np.isnan(gm.pass_rate(g, np.zeros_like(m)))
    edges, counts = gm.gamma_histogram(g, m)
    assert counts.sum() == 7 and counts[-1] == 2              # 2.0 and 3.0 in the ">= 2" bin
    assert counts[0] == 1 and counts[2] == 1 and counts[9] == 1 and counts[10] == 1
