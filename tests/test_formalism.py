"""Tests for gtcore.dose.formalism (Phase 3, task ``formalism``).

Synthetic references only: a small implant (two tiles' worth of seeds, random
axes) is rendered on a 1 x 1 x 1.667 mm grid like the clinical RTDOSE's,
either with gtcore's own 2D engine (the ratio must be flat at 1.00) or with
the 1D point-source formalism (the ratio must show the 1D-vs-2D signature).
``compare_axes`` / ``perturb_axes`` are checked on known rotations and the
RTDOSE axis fit on a reference with known axes.
"""
from __future__ import annotations

import numpy as np
import pytest

from gtcore.dose import TG43Engine, dose_at_points
from gtcore.dose.formalism import (ANGLE_BINS_DEG, angular_symmetry_test,
                                   binned_ratio, compare_axes, fit_common_axis,
                                   fit_axis_to_reference,
                                   fit_source_to_reference,
                                   match_sources_to_seeds, per_seed_doses,
                                   perturb_axes, phi_an, point_source_1d_dose,
                                   prediction_rows,
                                   reference_points_near_seeds,
                                   rtdose_source_positions, seed_frame)
from gtcore.volume import Volume

SPACING = np.array([1.0, 1.0, 1.0 + 2.0 / 3.0])      # the clinical RTDOSE grid


def _implant(rng, n=8):
    """Seed centres on a loose 10 mm lattice with random long axes."""
    g = np.array([[i, j, 0.0] for i in range(2) for j in range(n // 2)], float)
    centers = (g - g.mean(axis=0)) * 10.0 + rng.normal(0.0, 0.5, g.shape)
    axes = rng.normal(size=(len(centers), 3))
    axes /= np.linalg.norm(axes, axis=1, keepdims=True)
    return centers, axes


def _grid(centers, margin_mm=20.0):
    lo = centers.min(axis=0) - margin_mm
    hi = centers.max(axis=0) + margin_mm
    n = np.ceil((hi - lo) / SPACING).astype(int) + 1
    affine = np.diag([*SPACING, 1.0])
    affine[:3, 3] = lo
    kk, jj, ii = np.meshgrid(*[np.arange(v) for v in n[::-1]], indexing="ij")
    ijk = np.stack([ii.ravel(), jj.ravel(), kk.ravel()], axis=1).astype(float)
    pts = ijk @ affine[:3, :3].T + affine[:3, 3]
    return affine, tuple(int(v) for v in n[::-1]), pts


def _render(centers, axes, formalism="2D", engine=None, sk=3.5):
    affine, shape, pts = _grid(centers)
    if formalism == "2D":
        d = dose_at_points(centers, axes, pts, sk_per_seed_u=sk, engine=engine)
    else:
        d = point_source_1d_dose(pts, centers, sk, engine=engine)
    return Volume(d.reshape(shape), affine, {"units": "cGy"})


@pytest.fixture(scope="module")
def eng():
    return TG43Engine()


@pytest.fixture(scope="module")
def implant():
    rng = np.random.default_rng(7)
    return _implant(rng)


@pytest.fixture(scope="module")
def ref_2d(implant, eng):
    c, a = implant
    return _render(c, a, "2D", eng)


@pytest.fixture(scope="module")
def ref_1d(implant, eng):
    c, a = implant
    return _render(c, a, "1D", eng)


# ------------------------------------------------------------- building blocks
def test_seed_frame_folds_theta_and_ignores_axis_sign():
    c = np.zeros(3)
    pts = np.array([[0, 0, 5.0], [0, 0, -5.0], [5.0, 0, 0], [3.0, 0, 4.0]])
    r, th, z, y = seed_frame(pts, c, [0, 0, 1.0])
    assert np.allclose(r, 5.0)
    assert np.allclose(th, [0.0, 0.0, 90.0, np.degrees(np.arctan2(3, 4))])
    r2, th2, _z, _y = seed_frame(pts, c, [0, 0, -1.0])
    assert np.allclose(th, th2)


def test_per_seed_doses_sum_to_dose_at_points(implant, eng):
    c, a = implant
    rng = np.random.default_rng(1)
    pts = rng.uniform(-30, 30, (500, 3))
    m = per_seed_doses(pts, c, a, 3.5, engine=eng)
    assert m.shape == (len(c), 500)
    assert np.allclose(m.sum(axis=0), dose_at_points(c, a, pts, 3.5, engine=eng),
                       rtol=1e-12, atol=0.0)


def test_phi_an_is_the_solid_angle_average_and_near_consensus(eng):
    """phi_an(1 cm) for CS-1 Rev2 is ~0.96-0.97 in the consensus data; the
    numerical integral of the engine's own 2D rate must land there and be
    below 1 (the seed is dimmer along its axis) at every tissue radius."""
    r = np.array([0.5, 0.7, 1.0, 1.5, 2.0, 3.0, 5.0])
    pa = phi_an(eng, r)
    assert np.all(pa < 1.01) and np.all(pa > 0.93)
    assert 0.955 < phi_an(eng, 1.0) < 0.975
    # the 1D dose at r equals the solid-angle average of the 2D dose at r
    rr = 0.8
    th = np.linspace(0.0, 180.0, 3601)
    pts = rr * 10.0 * np.stack([np.sin(np.radians(th)), np.zeros_like(th),
                                np.cos(np.radians(th))], axis=1)
    d2 = per_seed_doses(pts, [[0, 0, 0]], [[0, 0, 1]], 3.5, engine=eng)[0]
    avg = np.trapezoid(d2 * np.sin(np.radians(th)), np.radians(th)) / 2.0
    d1 = point_source_1d_dose([[0.0, 0.0, rr * 10.0]], [[0, 0, 0]], 3.5, engine=eng)
    assert float(d1[0]) == pytest.approx(avg, rel=2e-3)


def test_point_source_1d_is_isotropic(eng):
    c = np.array([[0.0, 0.0, 0.0]])
    pts = 7.0 * np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1], [0.6, 0, 0.8]])
    d = point_source_1d_dose(pts, c, 3.5, engine=eng)
    assert np.allclose(d, d[0], rtol=1e-12)


def test_binned_ratio_bins_and_empties():
    th = np.array([0.0, 10.0, 20.0, 89.0, 90.0])
    ref = np.array([2.0, 2.0, 3.0, 1.0, 1.0])
    calc = np.ones(5)
    rows = binned_ratio(ref, calc, th, ANGLE_BINS_DEG)
    assert [r["n"] for r in rows] == [2, 1, 0, 0, 0, 2]
    assert rows[0]["mean"] == 2.0 and rows[1]["mean"] == 3.0
    assert np.isnan(rows[2]["mean"]) and rows[-1]["mean"] == 1.0


def test_reference_points_near_seeds_is_a_ball_union(implant, ref_2d):
    c, _a = implant
    flat, pts = reference_points_near_seeds(ref_2d, c, 6.0)
    d = np.min(np.linalg.norm(pts[:, None, :] - c[None, :, :], axis=2), axis=1)
    assert np.all(d <= 6.0 + 1e-9)
    # every grid voxel within 6 mm is present
    affine, shape, allp = _grid(c)
    dd = np.min(np.linalg.norm(allp[:, None, :] - c[None, :, :], axis=2), axis=1)
    assert flat.size == int((dd <= 6.0).sum())


# ------------------------------------------------------- the symmetry test
def test_2d_reference_gives_a_flat_ratio_of_one(implant, ref_2d, eng):
    c, a = implant
    res = angular_symmetry_test(ref_2d, c, a, 3.5, engine=eng)
    assert res.n_selected > 500
    for b in res.bins:
        assert b["n"] > 20, b
        assert abs(b["mean"] - 1.0) < 0.01, b
        assert b["sd"] < 0.01, b
    assert abs(res.slope_per_90deg) < 0.01
    assert abs(res.end_to_end) < 0.01
    # the selection obeys its own rules
    assert res.r_mm.min() >= 3.0 and res.r_mm.max() <= 15.0
    assert res.theta_deg.min() >= 0.0 and res.theta_deg.max() <= 90.0


def test_2d_reference_flat_with_tabulated_kernel_and_flipped_axes(implant, ref_2d, eng):
    c, a = implant
    res = angular_symmetry_test(ref_2d, c, -a, 3.5, engine=eng, exact=False)
    for b in res.bins:
        assert abs(b["mean"] - 1.0) < 0.01, b


def test_1d_reference_shows_the_1d_vs_2d_signature(implant, ref_1d, eng):
    """A 1D plan read against gtcore-2D is NOT flat, and its binned ratio is
    exactly the model prediction D_1D / D_2D on the same voxels.  The sign of
    the near-axis difference depends on r: inside ~6 mm the line-source G_L
    raises the on-axis 2D dose above the point-source value, beyond it
    F(r, theta) < 1 dominates (the classic 1D-vs-2D signature, rev 3's
    1.29 -> 0.96), so the 3-15 mm band mixes both and the far-field band
    shows the classic shape.  Read against a 1D gtcore it is flat."""
    c, a = implant
    res = angular_symmetry_test(ref_1d, c, a, 3.5, engine=eng)
    pred = prediction_rows(res, c, calc="1D", engine=eng)
    for b, pb in zip(res.bins, pred):
        assert b["n"] > 20
        assert b["mean"] == pytest.approx(pb["mean"], abs=0.01), (b, pb)
    assert max(abs(b["mean"] - 1.0) for b in res.bins) > 0.03
    assert res.bins[0]["sd"] > 0.02            # r-dependence inside the near-axis bin
    far = angular_symmetry_test(ref_1d, c, a, 3.5, engine=eng, r_min_mm=6.0, r_max_mm=15.0)
    means = [b["mean"] for b in far.bins]
    assert all(b["n"] >= 20 for b in far.bins)
    assert means[0] > 1.08 and means[-1] < 0.995
    assert all(m1 >= m2 - 0.005 for m1, m2 in zip(means[:-1], means[1:]))
    # and read against a 1D gtcore it is flat again
    flat = angular_symmetry_test(ref_1d, c, a, 3.5, engine=eng, calc="1D")
    for b in flat.bins:
        assert abs(b["mean"] - 1.0) < 0.01, b


def test_1d_signature_far_field_single_seed(eng):
    """One isolated seed, 6-15 mm: the classic signature -- a 1D reference
    read with gtcore-2D is ~1.2-1.3 near the axis and ~phi_an ~ 0.96 at the
    transverse plane, falling monotonically (rev 3: 1.29 -> 0.96)."""
    c = np.array([[0.3, -0.2, 0.4]])
    a = np.array([[0.0, 0.6, 0.8]])
    ref = _render(c, a, "1D", eng)
    res = angular_symmetry_test(ref, c, a, 3.5, engine=eng, r_min_mm=6.0, r_max_mm=15.0)
    means = [b["mean"] for b in res.bins]
    assert all(b["n"] > 30 for b in res.bins)
    assert 1.15 < means[0] < 1.35
    assert 0.94 < means[-1] < 0.99
    assert all(m1 >= m2 - 0.005 for m1, m2 in zip(means[:-1], means[1:]))
    assert res.slope_per_90deg < -0.2
    # and a 2D reference over the same far-field window is flat
    res2 = angular_symmetry_test(_render(c, a, "2D", eng), c, a, 3.5, engine=eng,
                                 r_min_mm=6.0, r_max_mm=15.0)
    for b in res2.bins:
        assert abs(b["mean"] - 1.0) < 0.01 and b["sd"] < 0.01, b


def test_prediction_rows_2d_other_axes_reproduce_a_measured_ratio(implant, eng):
    """Reference rendered with every axis along z, read with the true axes:
    the measured bins equal prediction_rows(..., seed_axes=z) to < 1 %."""
    c, a = implant
    zax = np.tile([0.0, 0.0, 1.0], (len(c), 1))
    ref = _render(c, zax, "2D", eng)
    res = angular_symmetry_test(ref, c, a, 3.5, engine=eng)
    pred = prediction_rows(res, c, zax, calc="2D", engine=eng)
    for b, pb in zip(res.bins, pred):
        assert b["mean"] == pytest.approx(pb["mean"], abs=0.01), (b, pb)
    assert res.rms_log > 0.03          # the wrong axes do show as scatter
    with pytest.raises(ValueError):
        prediction_rows(res, c, None, calc="2D", engine=eng)


def test_flat_sk_scale_cannot_make_a_trend(implant, ref_2d, eng):
    c, a = implant
    r1 = angular_symmetry_test(ref_2d, c, a, 3.5, engine=eng)
    r2 = angular_symmetry_test(ref_2d, c, a, 3.5 * 1.07, engine=eng)
    assert r2.n_selected == r1.n_selected
    for b1, b2 in zip(r1.bins, r2.bins):
        assert b2["mean"] == pytest.approx(b1["mean"] / 1.07, rel=1e-9)
    assert r2.end_to_end == pytest.approx(r1.end_to_end / 1.07, abs=1e-9)


def test_axis_error_is_visible_and_predicted(implant, ref_2d, eng):
    """Reading a 2D reference with wrong axes: the per-voxel scatter grows with
    the axis error (0 with the true axes), the bins leave 1.00, and the
    measured bins equal prediction_rows(..., true axes) -- so a measured
    angular trend can be tested against any candidate axis set.  A 10-20 deg
    error moves the bin means by only a few per cent at 3-6 mm: the
    clinical 17 % trend needs a far larger axis difference."""
    c, a = implant
    exact = angular_symmetry_test(ref_2d, c, a, 3.5, engine=eng)
    assert exact.rms_log < 1e-6
    prev = 0.0
    for deg in (10.0, 20.0):
        bad = perturb_axes(a, deg, rng=3)
        res = angular_symmetry_test(ref_2d, c, bad, 3.5, engine=eng)
        assert res.rms_log > prev + 0.005
        prev = res.rms_log
        assert max(abs(b["mean"] - 1.0) for b in res.bins) > 0.01
        assert max(abs(b["mean"] - 1.0) for b in res.bins) < 0.06
        pred = prediction_rows(res, c, a, calc="2D", engine=eng)
        for b, pb in zip(res.bins, pred):
            assert b["mean"] == pytest.approx(pb["mean"], abs=1e-6), (b, pb)


def test_voxel_averaging_option_runs_and_differs_little_on_a_2d_reference(implant, ref_2d, eng):
    c, a = implant
    res = angular_symmetry_test(ref_2d, c, a, 3.5, engine=eng, supersample=(2, 2, 3))
    assert res.params["supersample"] == [2, 2, 3]
    # a centre-sampled reference vs a voxel-averaged calc: averaging a convex
    # 1/r^2 field raises the calc, so the ratio dips slightly below 1
    for b in res.bins:
        assert 0.9 < b["mean"] < 1.0, b


# ---------------------------------------------------------------- axis tools
def test_compare_axes_known_rotations():
    rng = np.random.default_rng(11)
    a = rng.normal(size=(12, 3))
    a /= np.linalg.norm(a, axis=1, keepdims=True)
    for deg in (0.0, 7.5, 25.0, 60.0):
        b = perturb_axes(a, deg, rng=rng)
        out = compare_axes(a, b)
        assert out["n"] == 12
        assert np.allclose(out["angles_deg"], deg, atol=1e-9)
        assert out["median"] == pytest.approx(deg, abs=1e-9)
        assert out["max"] == pytest.approx(deg, abs=1e-9)
    # sign is irrelevant and angles fold into [0, 90]
    assert np.allclose(compare_axes(a, -a)["angles_deg"], 0.0, atol=1e-9)
    b = perturb_axes(a, 120.0, rng=1)
    assert np.allclose(compare_axes(a, b)["angles_deg"], 60.0, atol=1e-9)
    # mixed rotation: median / max are the right order statistics
    b = a.copy()
    b[:6] = perturb_axes(a[:6], 10.0, rng=2)
    b[6:] = perturb_axes(a[6:], 30.0, rng=3)
    out = compare_axes(a, b)
    assert out["max"] == pytest.approx(30.0, abs=1e-9)
    assert 10.0 - 1e-9 <= out["median"] <= 30.0 + 1e-9
    # NaN rows are skipped, not counted
    b[0] = np.nan
    out = compare_axes(a, b)
    assert out["n"] == 11 and np.isnan(out["angles_deg"][0])


def test_compare_axes_rejects_shape_mismatch():
    with pytest.raises(ValueError):
        compare_axes(np.eye(3), np.eye(3)[:2])


def test_perturb_axes_keeps_unit_length_and_angle():
    a = np.array([[0, 0, 1.0], [1, 0, 0.0], [0.6, 0.8, 0.0]])
    b = perturb_axes(a, 15.0, rng=0)
    assert np.allclose(np.linalg.norm(b, axis=1), 1.0)
    assert np.allclose(np.degrees(np.arccos(np.abs(np.einsum("ij,ij->i", a, b)))), 15.0)


def test_fit_axis_to_reference_recovers_known_axes(implant, ref_2d, eng):
    """Start 25 deg off; the RTDOSE-shell fit must come back to the true axis
    within a few degrees for every seed and improve the residual."""
    c, a = implant
    start = perturb_axes(a, 25.0, rng=5)
    recovered = []
    for s in range(len(c)):
        f = fit_axis_to_reference(ref_2d, c, start, s, 3.5, engine=eng)
        assert f.rms_fitted <= f.rms_initial
        assert f.n_points >= 50
        recovered.append(f.axis_fitted)
    out = compare_axes(a, np.asarray(recovered))
    assert out["max"] < 4.0, out
    assert out["median"] < 2.0, out


def test_fit_axis_scale_is_free(implant, eng):
    """A flat 7 % strength mismatch must not move the fitted axis."""
    c, a = implant
    ref = _render(c, a, "2D", eng, sk=3.5 * 1.07)
    f = fit_axis_to_reference(ref, c, a, 0, 3.5, engine=eng)
    assert f.angle_deg < 1.0
    assert f.scale_fitted == pytest.approx(1.07, rel=0.01)


def test_fit_source_position_recovers_a_half_voxel_offset(implant, ref_2d, eng):
    """Start from positions quantised to the grid (as RTDOSE maxima are) and
    recover the true centres to < 0.15 mm with the axes known."""
    c, a = implant
    affine = ref_2d.affine
    ijk = np.rint((c - affine[:3, 3]) @ np.linalg.inv(affine[:3, :3]).T)
    quantised = ijk @ affine[:3, :3].T + affine[:3, 3]
    assert np.linalg.norm(quantised - c, axis=1).max() > 0.3
    err = []
    for s in range(len(c)):
        fit = fit_source_to_reference(ref_2d, quantised, a, s, 3.5, engine=eng,
                                      fit_position=True, fit_axis=False)
        assert fit.rms_fitted < fit.rms_initial
        err.append(np.linalg.norm(fit.center_fitted - c[s]))
    assert max(err) < 0.15, err


def test_fit_source_joint_recovers_axis_and_position(implant, ref_2d, eng):
    c, a = implant
    rng = np.random.default_rng(9)
    start_c = c + rng.uniform(-0.5, 0.5, c.shape)
    start_a = perturb_axes(a, 20.0, rng=rng)
    fits = [fit_source_to_reference(ref_2d, start_c, start_a, s, 3.5, engine=eng,
                                    fit_position=True, fit_axis=True)
            for s in range(len(c))]
    ax = np.array([f.axis_fitted for f in fits])
    cen = np.array([f.center_fitted for f in fits])
    assert compare_axes(a, ax)["max"] < 5.0
    assert np.linalg.norm(cen - c, axis=1).max() < 0.25


def test_fit_source_1d_fits_position_only(implant, ref_1d, eng):
    c, a = implant
    fit = fit_source_to_reference(ref_1d, c + 0.3, a, 2, 3.5, engine=eng,
                                  fit_position=True, calc="1D")
    assert fit.calc == "1D" and fit.fit_axis is False
    assert np.isnan(fit.angle_deg)
    assert np.linalg.norm(fit.center_fitted - c[2]) < 0.15
    with pytest.raises(ValueError):
        fit_source_to_reference(ref_1d, c, a, 0, 3.5, engine=eng, calc="3D")


def test_fit_common_axis_finds_the_shared_direction(implant, eng):
    """Reference with every seed along one direction; the common-axis scan
    recovers it within 3 deg, beats the (wrong) PCA axes and matches the
    z-hypothesis RMS when that direction is z."""
    c, a = implant
    u = np.array([0.0, 0.0, 1.0])
    ref = _render(c, np.tile(u, (len(c), 1)), "2D", eng)
    out = fit_common_axis(ref, c, a, 3.5, engine=eng)
    assert out["angle_to_z_deg"] < 3.0
    assert out["rms"] < out["rms_given"]
    assert out["rms"] <= out["rms_z"] + 1e-3        # the scan stops within refine_deg[-1] of z
    v = np.array([0.6, 0.0, 0.8])
    ref2 = _render(c, np.tile(v, (len(c), 1)), "2D", eng)
    out2 = fit_common_axis(ref2, c, a, 3.5, engine=eng)
    assert compare_axes(out2["axis"][None], v[None])["median"] < 3.0
    assert out2["rms"] < out2["rms_z"]


# -------------------------------------------------------- sources on the grid
def test_rtdose_source_positions_and_matching(implant, ref_2d):
    c, _a = implant
    src = rtdose_source_positions(ref_2d, min_cgy=20000.0, merge_mm=3.0)
    assert src.shape == c.shape
    idx, dist = match_sources_to_seeds(src, c, gate_mm=3.0)
    assert np.all(idx >= 0) and len(set(idx.tolist())) == len(c)
    # maxima are voxel centres near the seed: the brightest voxel of a line
    # source need not be the one containing it (transverse neighbours can
    # beat an on-axis centre), so allow one voxel diagonal
    assert np.nanmax(dist) <= np.linalg.norm(SPACING) + 1e-9
    assert np.nanmedian(dist) <= 0.75 * np.linalg.norm(SPACING)
    idx2, _d = match_sources_to_seeds(src, c + 10.0, gate_mm=3.0)
    assert np.all(idx2 == -1)


def test_angular_symmetry_test_validates_inputs(implant, ref_2d, eng):
    c, a = implant
    with pytest.raises(ValueError):
        angular_symmetry_test(ref_2d, c, a, 3.5, engine=eng, calc="3D")
    with pytest.raises(ValueError):
        angular_symmetry_test(ref_2d, c, a, 3.5, engine=eng, r_min_mm=9.0, r_max_mm=3.0)
    with pytest.raises(ValueError):
        angular_symmetry_test(ref_2d, c + 500.0, a, 3.5, engine=eng)
