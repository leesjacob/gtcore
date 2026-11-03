"""gtcore.dose.uncertainty: Monte-Carlo seed-position error -> D90 / V100 / DVH.

Properties pinned here:

- a zero-error model (``NoError``, ``AxisGaussian((0, 0, 0))``,
  ``ResidualResample`` of one zero vector, ``Covariance`` of zero matrices)
  reproduces the nominal D90 / V100 / DVH *bit for bit*, and the nominal is
  ``dose_stats(target_doses_exact(...))``;
- ``AxisGaussian`` with a tiny SD gives a band whose width goes to zero, and
  the width grows with the SD;
- an isotropic 1 mm Gaussian on a synthetic 3-seed target gives a finite
  spread whose 95 % interval contains the nominal D90, V100 and DVH curve;
- the same seed reproduces every array, a different seed does not, and the
  thread count changes nothing;
- ``ResidualResample`` with one non-zero vector is a rigid shift of the
  implant; the CSV loader round-trips;
- ``Covariance`` draws have the requested covariance and reject bad input;
- weighted (S5-like) point targets and the table / describe helpers work.
"""
from __future__ import annotations

import numpy as np
import pytest

from gtcore.dose import dose_stats
from gtcore.dose.engine import TG43Engine
from gtcore.dose.targets import Target, hrctv_phys, target_doses_exact
from gtcore.dose.uncertainty import (DEFAULT_LEVELS_CGY, DEGRADED_EXPORT_SD_MM,
                                     AxisGaussian, Covariance, ErrorModel, MCResult,
                                     NoError, ResidualResample, dvh_curve,
                                     load_residuals, propagate)

ENGINE = TG43Engine()
LEVELS = np.arange(0.0, 20001.0, 250.0)        # coarse ladder keeps the tests quick


# ----------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def synthetic():
    """Three seeds 10 mm apart along x in the plane z = 0 (axes along x) and a
    1 mm-grid target box 3-8 mm above them: ~2400 voxels, D50 of a few
    thousand cGy at 3.5 U."""
    centers = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [20.0, 0.0, 0.0]])
    axes = np.tile([1.0, 0.0, 0.0], (3, 1))
    aff = np.eye(4)
    aff[:3, 3] = (-15.0, -8.0, 1.0)                 # origin of voxel (0, 0, 0)
    shape = (10, 17, 36)                              # (nk, nj, ni): z 1..10, y -8..8, x -15..20
    k, j, i = np.mgrid[:shape[0], :shape[1], :shape[2]]
    x, y, z = i + aff[0, 3], j + aff[1, 3], k + aff[2, 3]
    mask = (z >= 3) & (z <= 8) & (x >= -10) & (x <= 20) & (np.abs(y) <= 6)
    target = hrctv_phys(mask, aff, roi_name="SYN")
    d0 = target_doses_exact(target, centers, axes, 3.5, engine=ENGINE)
    rx = float(dose_stats(d0, 6000.0)["D50"])         # V100 ~ 0.5: both tails informative
    return target, centers, axes, rx


def _run(synthetic, model, n_draws=60, seed=0, **kw):
    target, centers, axes, rx = synthetic
    return propagate(target, centers, axes, model, n_draws=n_draws, seed=seed,
                     rx_cgy=rx, levels_cgy=LEVELS, engine=ENGINE, **kw)


# ----------------------------------------------------------------- dvh_curve
def test_dvh_curve_is_the_ge_rule():
    d = np.array([1.0, 2.0, 3.0, 4.0])
    np.testing.assert_allclose(dvh_curve(d, [0.0, 1.0, 2.5, 4.0, 5.0]),
                               [1.0, 1.0, 0.5, 0.25, 0.0])
    # weights: zero-weight samples are not part of the structure
    w = np.array([1.0, 0.0, 1.0, 2.0])
    np.testing.assert_allclose(dvh_curve(d, [0.0, 2.0, 3.0, 4.0, 4.1], w),
                               [1.0, 0.75, 0.75, 0.5, 0.0])
    assert np.isnan(dvh_curve([], [0.0, 1.0])).all()
    with pytest.raises(ValueError):
        dvh_curve(d, [1.0], weights=[1.0, 2.0])


def test_dvh_curve_at_rx_equals_v100(synthetic):
    target, centers, axes, rx = synthetic
    d0 = target_doses_exact(target, centers, axes, 3.5, engine=ENGINE)
    st = dose_stats(d0, rx)
    cv = dvh_curve(d0, [rx, 1.5 * rx, 2.0 * rx])
    assert cv[0] == st["V100"] and cv[1] == st["V150"] and cv[2] == st["V200"]


# ------------------------------------------------------- zero error = nominal
@pytest.mark.parametrize("model", [NoError(), AxisGaussian((0.0, 0.0, 0.0)),
                                   ResidualResample([[0.0, 0.0, 0.0]]),
                                   Covariance(np.zeros((3, 3, 3)))])
def test_zero_error_models_reproduce_the_nominal_exactly(synthetic, model):
    res = _run(synthetic, model, n_draws=5)
    target, centers, axes, rx = synthetic
    d0 = target_doses_exact(target, centers, axes, 3.5, engine=ENGINE)
    st = dose_stats(d0, rx, voxel_volume_mm3=target.voxel_volume_mm3)
    assert res.nominal["D90"] == st["D90"] and res.nominal["V100"] == st["V100"]
    assert np.all(res.d90 == st["D90"])
    assert np.all(res.v100 == st["V100"])
    assert np.all(res.v150 == st["V150"])
    assert np.array_equal(res.dvh_nominal, dvh_curve(d0, LEVELS))
    assert np.array_equal(res.dvh_p2_5, res.dvh_nominal)
    assert np.array_equal(res.dvh_p97_5, res.dvh_nominal)
    # the mean of five identical rows is the row up to one rounding of the sum
    np.testing.assert_allclose(res.dvh_mean, res.dvh_nominal, rtol=0.0, atol=1e-15)
    s = res.summary("D90")
    assert s["sd"] == 0.0 and s["p2.5"] == s["p97.5"] == s["nominal"]
    assert res.perturbation_mm["max_mm"] == 0.0


def test_zero_error_with_unnormalised_axes_still_matches_target_doses_exact(synthetic):
    """Both paths normalise the axes exactly once from the raw input."""
    target, centers, axes, rx = synthetic
    raw_axes = axes * np.array([[0.3], [7.0], [1.0e-3]])
    res = propagate(target, centers, raw_axes, NoError(), n_draws=2, rx_cgy=rx,
                    levels_cgy=LEVELS, engine=ENGINE)
    d0 = target_doses_exact(target, centers, raw_axes, 3.5, engine=ENGINE)
    assert res.d90[0] == dose_stats(d0, rx)["D90"]


# --------------------------------------------------------- band width vs SD
def test_axis_gaussian_band_width_vanishes_with_tiny_sd_and_grows_with_sd(synthetic):
    widths, dvh_widths = [], []
    for sd in (1.0e-6, 0.1, 1.0):
        res = _run(synthetic, AxisGaussian((sd, sd, sd)), n_draws=60, seed=1)
        s = res.summary("D90")
        widths.append(s["p97.5"] - s["p2.5"])
        dvh_widths.append(float(np.max(res.dvh_p97_5 - res.dvh_p2_5)))
        np.testing.assert_allclose(res.perturbation_mm["sd_mm"], sd, rtol=0.25)
    assert widths[0] <= 0.5                     # cGy: a 1 nm shift is no shift
    assert dvh_widths[0] <= 1.0e-3
    assert widths[0] < widths[1] < widths[2]
    assert dvh_widths[0] < dvh_widths[1] < dvh_widths[2]
    assert widths[2] > 50.0                     # 1 mm on a 10 mm pitch moves D90 by > 0.5 Gy


def test_isotropic_1mm_gaussian_band_contains_the_nominal(synthetic):
    res = _run(synthetic, AxisGaussian((1.0, 1.0, 1.0)), n_draws=200, seed=3)
    assert res.d90.shape == res.v100.shape == res.v150.shape == (200,)
    assert np.all(np.isfinite(res.d90)) and np.all((res.v100 >= 0) & (res.v100 <= 1))
    for key in ("D90", "V100", "V150"):
        s = res.summary(key)
        assert s["p2.5"] <= s["nominal"] <= s["p97.5"], (key, s)
        assert s["sd"] > 0.0
    assert np.all(res.dvh_p2_5 <= res.dvh_nominal + 1e-12)
    assert np.all(res.dvh_p97_5 >= res.dvh_nominal - 1e-12)
    assert np.all(res.dvh_p2_5 <= res.dvh_mean + 1e-12)
    assert np.all(res.dvh_mean <= res.dvh_p97_5 + 1e-12)
    # a cumulative DVH is monotone non-increasing in dose, draw by draw and in the envelope
    assert np.all(np.diff(res.dvh_mean) <= 1e-12)
    assert res.dvh_mean[0] == 1.0 and res.dvh_nominal[0] == 1.0
    # the D90 spread is a few percent of the dose, not zero and not everything
    assert 0.002 < res.summary("D90")["sd"] / res.nominal["D90"] < 0.5
    assert res.d90_p2_5 <= res.d90_mean <= res.d90_p97_5
    assert res.v100_p2_5 <= res.v100_mean <= res.v100_p97_5


# ------------------------------------------------------------ reproducibility
def test_same_seed_reproduces_every_array_and_threads_change_nothing(synthetic):
    model = AxisGaussian(DEGRADED_EXPORT_SD_MM)
    a = _run(synthetic, model, n_draws=40, seed=7)
    b = _run(synthetic, model, n_draws=40, seed=7)
    c = _run(synthetic, model, n_draws=40, seed=7, n_workers=3)
    for r in (b, c):
        assert np.array_equal(a.d90, r.d90) and np.array_equal(a.v100, r.v100)
        assert np.array_equal(a.v150, r.v150)
        assert np.array_equal(a.dvh_mean, r.dvh_mean)
        assert np.array_equal(a.dvh_p2_5, r.dvh_p2_5) and np.array_equal(a.dvh_p97_5, r.dvh_p97_5)
    d = _run(synthetic, model, n_draws=40, seed=8)
    assert not np.array_equal(a.d90, d.d90)


# ------------------------------------------------------- residual resampling
def test_residual_resample_single_vector_is_a_rigid_shift(synthetic):
    target, centers, axes, rx = synthetic
    v = np.array([0.5, -0.3, 0.8])
    res = _run(synthetic, ResidualResample(v), n_draws=4)
    shifted = dose_stats(target_doses_exact(target, centers + v, axes, 3.5, engine=ENGINE), rx)
    np.testing.assert_allclose(res.d90, shifted["D90"], rtol=1e-9)
    np.testing.assert_allclose(res.v100, shifted["V100"], atol=1e-12)
    np.testing.assert_allclose(res.perturbation_mm["median_mm"], np.linalg.norm(v))
    assert res.summary("D90")["sd"] == 0.0


def test_residual_resample_draws_only_measured_vectors_and_describes_them():
    r = np.array([[0.1, 0.0, 0.0], [0.0, -0.2, 0.0], [0.0, 0.0, 0.9]])
    m = ResidualResample(r)
    rng = np.random.default_rng(0)
    d = m.draw(rng, n_seeds=5, n_draws=200)
    assert d.shape == (200, 5, 3)
    flat = d.reshape(-1, 3)
    assert all(any(np.array_equal(row, rr) for rr in r) for row in flat)
    assert len({tuple(row) for row in flat}) == 3      # every vector gets used
    assert "3 measured residual vectors" in m.describe() and "max 0.90 mm" in m.describe()
    mc = ResidualResample(r, center=True)
    np.testing.assert_allclose(mc.residuals.mean(axis=0), 0.0, atol=1e-15)
    with pytest.raises(ValueError):
        ResidualResample(np.zeros((0, 3)))
    with pytest.raises(ValueError):
        ResidualResample(np.ones((4, 2)))
    with pytest.raises(ValueError):
        ResidualResample([[np.nan, 0.0, 0.0]])


def test_load_residuals_round_trip(tmp_path):
    p = tmp_path / "res.csv"
    p.write_text("# script: x\n# command: y\nseed_id,dx_mm,dy_mm,dz_mm,dist_mm\n"
                 "0,0.10,-0.20,0.30,0.374\n1,-0.05,0.00,0.90,0.901\n", encoding="utf-8")
    r = load_residuals(p)
    np.testing.assert_allclose(r, [[0.10, -0.20, 0.30], [-0.05, 0.00, 0.90]])
    m = ResidualResample.from_csv(p)
    assert m.n == 2
    bad = tmp_path / "bad.csv"
    bad.write_text("seed_id,a,b,c\n0,1,2,3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_residuals(bad)
    empty = tmp_path / "empty.csv"
    empty.write_text("# nothing but comments\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_residuals(empty)


# ----------------------------------------------------------------- covariance
def test_covariance_model_draws_have_the_requested_covariance_and_reject_bad_input():
    cov = np.array([np.diag([0.04, 0.09, 0.25]),
                    np.array([[0.1, 0.05, 0.0], [0.05, 0.1, 0.0], [0.0, 0.0, 0.3]])])
    m = Covariance(cov)
    d = m.draw(np.random.default_rng(1), n_seeds=2, n_draws=20000)
    for s in range(2):
        emp = np.cov(d[:, s, :].T)
        np.testing.assert_allclose(emp, cov[s], atol=0.02)
    assert "2 refinement covariances" in m.describe()
    with pytest.raises(ValueError):
        m.draw(np.random.default_rng(0), n_seeds=3, n_draws=2)       # seed-count mismatch
    with pytest.raises(ValueError):
        Covariance(np.ones((2, 2, 2)))
    with pytest.raises(ValueError):
        Covariance(np.array([[[1.0, 0.5, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]]))  # asymmetric
    with pytest.raises(ValueError):
        Covariance(np.array([np.diag([1.0, -1.0, 1.0])]))             # not PSD
    one = Covariance(np.diag([0.01, 0.01, 0.01]))                      # (3, 3) promotes to (1, 3, 3)
    assert one.n == 1


def test_covariance_matches_axis_gaussian_statistically(synthetic):
    """Diagonal covariances with the degraded-export SDs are the same model."""
    sd = np.asarray(DEGRADED_EXPORT_SD_MM)
    cov = np.tile(np.diag(sd ** 2), (3, 1, 1))
    a = _run(synthetic, AxisGaussian(sd), n_draws=150, seed=11)
    b = _run(synthetic, Covariance(cov), n_draws=150, seed=11)
    sa, sb = a.summary("D90"), b.summary("D90")
    # different RNG streams, same distribution: means within half an SD
    # (the standard error is sd / sqrt(150) ~ 0.08 sd), SDs within a factor 1.6
    assert abs(sa["mean"] - sb["mean"]) < 0.5 * max(sa["sd"], sb["sd"])
    assert 0.6 < sa["sd"] / sb["sd"] < 1.6


# ------------------------------------------------------- weighted point target
def test_weighted_point_target_uses_the_weights():
    pts = np.array([[0.0, 0.0, 5.0], [5.0, 0.0, 5.0], [10.0, 0.0, 5.0], [0.0, 0.0, 12.0]])
    w = np.array([1.0, 2.0, 1.0, 4.0])
    t = Target(name="S5", mask=None, affine=None, points=pts, weights=w,
               provenance={"builder": "test"})
    centers = np.array([[5.0, 0.0, 0.0]])
    axes = np.array([[1.0, 0.0, 0.0]])
    d0 = target_doses_exact(t, centers, axes, 3.5, engine=ENGINE)
    rx = float(np.median(d0))
    res = propagate(t, centers, axes, NoError(), n_draws=3, rx_cgy=rx, levels_cgy=LEVELS,
                    engine=ENGINE)
    st = dose_stats(d0, rx, weights=w)
    assert res.nominal["D90"] == st["D90"] and res.nominal["V100"] == st["V100"]
    assert np.isnan(res.nominal["volume_cc"])
    assert res.dvh_nominal[0] == 1.0
    assert np.array_equal(res.dvh_nominal, dvh_curve(d0, LEVELS, w))
    res2 = propagate(t, centers, axes, AxisGaussian((0.5, 0.5, 0.5)), n_draws=20,
                     rx_cgy=rx, levels_cgy=LEVELS, engine=ENGINE)
    assert np.all(np.isfinite(res2.d90)) and res2.n_points == 4


# --------------------------------------------------------------- bad inputs
def test_bad_inputs_raise(synthetic):
    target, centers, axes, rx = synthetic
    with pytest.raises(ValueError):
        propagate(target, centers, axes, NoError(), n_draws=0, engine=ENGINE)
    with pytest.raises(TypeError):
        propagate(target, centers, axes, "not a model", n_draws=2, engine=ENGINE)
    with pytest.raises(ValueError):
        propagate(target, np.zeros((0, 3)), np.zeros((0, 3)), NoError(), n_draws=2, engine=ENGINE)
    empty = hrctv_phys(np.zeros_like(target.mask), target.affine, roi_name="EMPTY")
    with pytest.raises(ValueError):
        propagate(empty, centers, axes, NoError(), n_draws=2, engine=ENGINE)
    with pytest.raises(ValueError):
        propagate(target, centers, axes, NoError(), n_draws=2, levels_cgy=[], engine=ENGINE)

    class Wrong(ErrorModel):
        def draw(self, rng, n_seeds, n_draws):
            return np.zeros((n_draws, n_seeds + 1, 3))

    class NonFinite(ErrorModel):
        def draw(self, rng, n_seeds, n_draws):
            return np.full((n_draws, n_seeds, 3), np.nan)

    with pytest.raises(ValueError):
        propagate(target, centers, axes, Wrong(), n_draws=2, engine=ENGINE)
    with pytest.raises(ValueError):
        propagate(target, centers, axes, NonFinite(), n_draws=2, engine=ENGINE)
    with pytest.raises(ValueError):
        AxisGaussian((0.1, 0.2))
    with pytest.raises(ValueError):
        AxisGaussian((0.1, -0.2, 0.3))


# ------------------------------------------------------------ tables / text
def test_rows_describe_and_defaults(synthetic):
    res = _run(synthetic, AxisGaussian(DEGRADED_EXPORT_SD_MM, label="degraded export"),
               n_draws=10, seed=2)
    assert isinstance(res, MCResult)
    rows = res.stat_rows("degraded_export", seed_set="syn3")
    assert [r["statistic"] for r in rows] == ["D90", "V100", "V150"]
    assert set(rows[0]) == {"band", "seed_set", "statistic", "nominal", "mean", "sd",
                            "p2.5", "p97.5", "n_draws", "seed"}
    assert rows[0]["n_draws"] == 10 and rows[0]["seed"] == 2 and rows[0]["band"] == "degraded_export"
    dvh = res.dvh_rows("degraded_export", seed_set="syn3")
    assert len(dvh) == LEVELS.size and dvh[0]["level_cgy"] == 0.0 and dvh[0]["nominal"] == 1.0
    assert set(dvh[0]) == {"band", "seed_set", "level_cgy", "nominal", "mean", "p2.5", "p97.5"}
    txt = res.describe()
    assert "degraded export" in txt and "SYN" not in txt and "HRCTV_PHYS" in txt
    assert "0.51, 0.40, 0.63" in res.model and res.model_name == "axis_gaussian"
    assert res.axes_fixed and res.engine == "exact" and res.runtime_s > 0.0
    assert res.provenance["roi_name"] == "SYN" and res.n_seeds == 3
    assert DEFAULT_LEVELS_CGY[0] == 0.0 and DEFAULT_LEVELS_CGY[-1] == 15000.0
    assert DEFAULT_LEVELS_CGY.size == 301 and np.all(np.diff(DEFAULT_LEVELS_CGY) == 50.0)
    assert DEGRADED_EXPORT_SD_MM == (0.51, 0.40, 0.63)


def test_tabulated_kernel_path_is_close_to_exact(synthetic):
    a = _run(synthetic, NoError(), n_draws=2)
    b = _run(synthetic, NoError(), n_draws=2, exact=False)
    assert b.engine == "table"
    np.testing.assert_allclose(b.nominal["D90"], a.nominal["D90"], rtol=1e-3)
    assert b.d90[0] == b.nominal["D90"]
