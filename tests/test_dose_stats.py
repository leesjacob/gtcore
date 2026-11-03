"""The one ``dose_stats`` (Paper 2 Phase 2, requirements §2 "Dose statistics").

Convention under test (``gtcore.dose.metrics.dose_stats``):

* ``Dxx`` is the weighted inverted-CDF quantile (Hyndman-Fan type 1): sort the
  doses ascending with their weights, cumulative weight fraction ``F``,
  ``Dxx = smallest d_i with F(d_i) >= 1 - xx/100``; no interpolation, so Dxx
  is a dose that occurs in the structure and at least xx % of the weight
  receives ``>= Dxx``.  ``D100 = Dmin``.
* ``Vyyy = sum(w[d >= yyy/100 * rx]) / sum(w)`` (``>=`` rule).
* Empty input or zero total weight -> nan for every dose / fraction, ``n = 0``,
  ``total_weight = 0``, ``volume_cc = 0``.

and the contract that the planner path (``hrctv_stats``), the optimizer path
(``weighted_stats`` / ``metrics_from_dose`` with unit weights) and the
validation path (``dose_metrics`` on a ``Volume`` + mask) give bit-identical
D90 / V100 / V150 for one dose vector.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

from gtcore.dose import DOSE_STAT_KEYS, DVH, dose_metrics, dose_stats, dvh, \
    dvh_stats, hrctv_stats, weighted_quantile
from gtcore.dose.dvh import format_report
from gtcore.plan import api as plan_api
from gtcore.plan import objective as ob
from gtcore.plan.report import weighted_stats
from gtcore.plan.solvers import _weighted_quantile as solvers_weighted_quantile
from gtcore.volume import Volume

DOSE_KEYS = ("D90", "D50", "D100", "Dmin", "Dmax", "Dmean", "V100", "V150", "V200")


# ------------------------------------------------------------ (a) analytic
def test_unweighted_small_vector_by_hand():
    d = np.array([1.0, 2.0, 3.0, 4.0])
    s = dose_stats(d, rx_cgy=2.0)
    # F = .25 .5 .75 1 : D90 -> first F >= 0.1 -> 1 ; D50 -> first F >= 0.5 -> 2
    assert s["D90"] == 1.0
    assert s["D50"] == 2.0
    assert s["D100"] == 1.0 and s["Dmin"] == 1.0 and s["Dmax"] == 4.0
    assert s["Dmean"] == 2.5
    assert s["V100"] == 0.75           # >= 2
    assert s["V150"] == 0.5            # >= 3
    assert s["V200"] == 0.25           # >= 4
    assert s["n"] == 4 and isinstance(s["n"], int)
    assert s["total_weight"] == 4.0
    assert np.isnan(s["volume_cc"])    # no voxel volume given
    assert s["rx_cgy"] == 2.0
    assert set(s) == set(DOSE_STAT_KEYS)
    # voxel volume -> structure volume
    assert dose_stats(d, 2.0, voxel_volume_mm3=8.0)["volume_cc"] == 4 * 8.0 / 1000.0


def test_ramp_of_100_voxels_by_hand():
    d = np.arange(1, 101, dtype=float) * 100.0        # 100 .. 10000 cGy
    s = dose_stats(d, rx_cgy=6000.0, voxel_volume_mm3=1.0)
    assert s["D90"] == 1000.0      # 10th of 100 sorted values (F = 0.10)
    assert s["D50"] == 5000.0      # 50th (F = 0.50)
    assert s["V100"] == 41 / 100.0  # 6000 .. 10000 inclusive
    assert s["V150"] == 11 / 100.0  # 9000 .. 10000 inclusive
    assert s["V200"] == 0.0
    assert s["volume_cc"] == 0.1
    assert s["n"] == 100


def test_weighted_small_vector_by_hand():
    d = np.array([1.0, 2.0, 3.0, 4.0])
    w = np.array([1.0, 1.0, 1.0, 100.0])
    s = dose_stats(d, rx_cgy=2.0, weights=w)
    # cumulative weights 1, 2, 3, 103 of 103: F = .0097 .0194 .0291 1.0
    assert s["D90"] == 4.0 and s["D50"] == 4.0
    assert s["Dmin"] == 1.0 and s["D100"] == 1.0 and s["Dmax"] == 4.0
    assert s["V100"] == pytest.approx(102.0 / 103.0)
    assert s["V150"] == pytest.approx(101.0 / 103.0)
    assert s["V200"] == pytest.approx(100.0 / 103.0)
    assert s["Dmean"] == pytest.approx((1 + 2 + 3 + 400.0) / 103.0)
    assert s["total_weight"] == 103.0 and s["n"] == 4
    # weights that are volumes in mm^3 with voxel_volume_mm3 = 1 -> cc
    assert dose_stats(d, 2.0, weights=w, voxel_volume_mm3=1.0)["volume_cc"] == 0.103
    # integer weights equal repetition
    rep = dose_stats(np.array([1.0, 2.0, 3.0]), 2.0, weights=[2.0, 1.0, 1.0])
    exp = dose_stats(np.array([1.0, 1.0, 2.0, 3.0]), 2.0)
    for k in DOSE_KEYS:
        assert rep[k] == exp[k], k


def test_ties_and_the_ge_rule():
    d = np.array([5.0, 5.0, 5.0, 1.0, 1.0])
    s = dose_stats(d, rx_cgy=5.0)
    # sorted 1 1 5 5 5 : F = .2 .4 .6 .8 1
    assert s["D90"] == 1.0
    assert s["D50"] == 5.0            # first F >= 0.5 is the third value
    assert s["V100"] == 0.6           # doses exactly at rx count (>=)
    assert s["V150"] == 0.0
    assert dose_stats([6000.0], 6000.0)["V100"] == 1.0
    assert dose_stats([5999.999], 6000.0)["V100"] == 0.0


def test_single_element():
    s = dose_stats([7.0], rx_cgy=7.0, voxel_volume_mm3=2.0)
    for k in ("D90", "D50", "D100", "Dmin", "Dmax", "Dmean"):
        assert s[k] == 7.0, k
    assert s["V100"] == 1.0 and s["V150"] == 0.0 and s["V200"] == 0.0
    assert s["n"] == 1 and s["total_weight"] == 1.0 and s["volume_cc"] == 0.002


def test_dxx_is_a_sample_value_and_covers_at_least_xx_percent():
    rng = np.random.default_rng(5)
    for n in (3, 10, 11, 57, 1000):
        d = rng.gamma(3.0, 1500.0, size=n)
        w = rng.uniform(0.2, 3.0, size=n)
        s = dose_stats(d, 6000.0, weights=w)
        tot = w.sum()
        for key, pct in (("D90", 90.0), ("D50", 50.0), ("D100", 100.0)):
            v = s[key]
            assert v in d, key
            assert w[d >= v].sum() / tot >= pct / 100.0 - 1e-12, key
            # the smallest such sample: every strictly smaller sample has F < 1 - pct
            below = d < v
            assert w[below].sum() / tot < 1.0 - pct / 100.0 + 1e-12, key


@pytest.mark.parametrize("n", [1, 2, 3, 7, 10, 25, 64, 200, 1001])
def test_unweighted_dxx_equals_numpy_inverted_cdf(n):
    rng = np.random.default_rng(n)
    d = rng.normal(size=n) * 1000.0 + 6000.0
    srt = np.sort(d)
    s = dose_stats(d, 6000.0)
    for key, q in (("D90", 0.10), ("D50", 0.50)):
        ref = float(np.quantile(d, q, method="inverted_cdf"))
        nq = n * q
        if abs(nq - round(nq)) < 1e-9 and nq > round(nq):
            # n*q overshoots an integer k by floating-point rounding: numpy
            # steps to element k+1, the inverted-CDF definition says element k
            assert s[key] == srt[int(round(nq)) - 1]
        else:
            assert s[key] == ref, (key, n)
    assert s["D100"] == srt[0] and s["Dmax"] == srt[-1]


def test_weighted_quantile_primitive():
    v = np.array([5.0, 1.0, 3.0])
    assert weighted_quantile(v, [1, 1, 1], 0.0) == 1.0
    assert weighted_quantile(v, [1, 1, 1], 1.0) == 5.0
    assert weighted_quantile(v, [1, 1, 1], 0.5) == 3.0
    # zero-weight entries are not part of the structure
    assert weighted_quantile(v, [0, 1, 1], 0.0) == 1.0      # {1, 3}: min is 1
    assert weighted_quantile(v, [1, 0, 1], 0.0) == 3.0      # {5, 3}: min is 3
    assert weighted_quantile(v, [1, 0, 0], 0.5) == 5.0
    for bad in (([], [], 0.5), (v, [0, 0, 0], 0.5), (v, [1, -1, 1], 0.5),
                (v, [1, 1, 1], 1.5), (v, [1, 1], 0.5)):
        with pytest.raises(ValueError):
            weighted_quantile(*bad)
    # one function everywhere
    assert ob.weighted_quantile is weighted_quantile
    assert plan_api.weighted_quantile(v, np.ones(3), 0.5) == 3.0
    assert np.isnan(plan_api.weighted_quantile([], [], 0.5))
    assert solvers_weighted_quantile(v, np.ones(3), 0.5) == 3.0
    assert np.isnan(solvers_weighted_quantile([], [], 0.5))


# --------------------------------------------------------------- (b) empty
def _assert_empty(s, rx):
    assert set(s) == set(DOSE_STAT_KEYS)
    for k in DOSE_KEYS:
        assert np.isnan(s[k]), k
    assert s["n"] == 0 and s["total_weight"] == 0.0 and s["volume_cc"] == 0.0
    assert s["rx_cgy"] == rx


def test_empty_and_zero_weight_inputs_are_nan():
    _assert_empty(dose_stats([], 6000.0), 6000.0)
    _assert_empty(dose_stats(np.zeros(0), 6000.0, voxel_volume_mm3=1.0), 6000.0)
    _assert_empty(dose_stats([1.0, 2.0, 3.0], 6000.0, weights=[0.0, 0.0, 0.0]), 6000.0)
    _assert_empty(dose_stats([], 6000.0, weights=[]), 6000.0)
    # the wrappers inherit the rule (no more "0 on empty")
    for s in (dvh_stats([], 6000.0), hrctv_stats(np.zeros(0), 6000.0),
              weighted_stats([], [], 6000.0)):
        _assert_empty(s, 6000.0)
    m = ob.metrics_from_dose(np.zeros(0), np.zeros(0), 6000.0)
    assert set(m) == set(ob.METRIC_KEYS) and all(np.isnan(v) for v in m.values())
    t = plan_api.target_metrics([], [], 6000.0)
    assert all(np.isnan(v) for v in t.values()) and "hard" in t


def test_bad_weights_raise():
    with pytest.raises(ValueError):
        dose_stats([1.0, 2.0], 1.0, weights=[1.0])
    with pytest.raises(ValueError):
        dose_stats([1.0, 2.0], 1.0, weights=[1.0, -1.0])


def test_zero_weight_samples_are_not_in_the_structure():
    d = np.array([10.0, 50.0, 90.0, 1e6])
    w = np.array([1.0, 1.0, 1.0, 0.0])
    s = dose_stats(d, 50.0, weights=w)
    assert s["Dmax"] == 90.0 and s["Dmin"] == 10.0 and s["n"] == 3
    assert s["V200"] == 0.0 and s["Dmean"] == 50.0
    ref = dose_stats(d[:3], 50.0)
    for k in DOSE_KEYS:
        assert s[k] == ref[k], k


# --------------------------------------------------- (c) weighted = unweighted
def test_equal_weights_match_the_unweighted_statistics():
    rng = np.random.default_rng(11)
    d = rng.uniform(0.0, 15000.0, size=257)
    ref = dose_stats(d, 6000.0)
    unit = dose_stats(d, 6000.0, weights=np.ones(257))
    for k in DOSE_STAT_KEYS:
        if k == "volume_cc":
            assert np.isnan(unit[k]) and np.isnan(ref[k])   # no voxel volume given
        else:
            assert unit[k] == ref[k], k                # bit-identical, Dmean included
    scaled = dose_stats(d, 6000.0, weights=np.full(257, 3.7))
    for k in ("D90", "D50", "D100", "Dmin", "Dmax", "n"):
        assert scaled[k] == ref[k], k                  # order statistics: exact
    for k in ("V100", "V150", "V200", "Dmean"):
        # (3.7 k) / (3.7 n) is not k / n to the last bit
        assert scaled[k] == pytest.approx(ref[k], rel=1e-12), k
    assert scaled["total_weight"] == pytest.approx(257 * 3.7)


# ------------------------------------------- (d) identical output across paths
def test_identical_output_planner_optimizer_validation_paths():
    """One dose vector through ``hrctv_stats`` (planner), ``weighted_stats`` /
    ``metrics_from_dose`` with unit weights (optimizer) and ``dose_metrics``
    on a Volume + mask (validation): bit-identical D90 / V100 / V150."""
    rng = np.random.default_rng(2026)
    arr = rng.gamma(2.5, 2400.0, size=(9, 11, 13))          # cGy, skewed like an implant
    mask = rng.uniform(size=arr.shape) < 0.6
    aff = np.diag([2.0, 2.0, 2.0, 1.0])
    vol = Volume(arr, aff)
    rx = 6000.0
    d = arr[mask]                                             # the one dose vector
    ones = np.ones(d.size)

    planner = hrctv_stats(d, rx, voxel_volume_mm3=8.0)
    optimizer_report = weighted_stats(d, ones, rx)
    optimizer_objective = ob.metrics_from_dose(d, ones, rx)
    validation = dose_metrics(vol, mask, rx)
    panel = dvh_stats(d, rx)
    api_metrics = plan_api.target_metrics(d, ones, rx)
    direct = dose_stats(d, rx)

    for key in ("D90", "V100", "V150"):
        vals = [planner[key], optimizer_report[key], optimizer_objective[key],
                validation[key], panel[key], api_metrics[key], direct[key]]
        assert all(v == vals[0] for v in vals), (key, vals)
    for key in ("D50", "V200", "Dmin", "Dmax"):
        assert planner[key] == optimizer_report[key] == validation[key] == panel[key]
    assert planner["Dmean"] == pytest.approx(validation["Dmean"], rel=1e-12)
    assert planner["Dmean"] == optimizer_objective["Dmean"]
    assert planner["volume_cc"] == validation["volume_cc"] == int(mask.sum()) * 8.0 / 1000.0
    assert planner["n"] == validation["n"] == optimizer_report["n"] == int(mask.sum())
    assert np.isnan(optimizer_report["volume_cc"])            # a weighted set has no voxel volume
    assert np.isnan(panel["volume_cc"])


# ----------------------------------------------------- (e) DVH.D / DVH.V agree
def test_dvh_d_and_v_follow_dose_stats():
    rng = np.random.default_rng(3)
    d = rng.uniform(1000.0, 12000.0, size=333)
    rx = 6000.0
    h = DVH(np.sort(d), voxel_volume_mm3=8.0)
    s = dose_stats(d, rx, voxel_volume_mm3=8.0)
    assert h.D(90.0) == s["D90"] and h.D(50.0) == s["D50"]
    assert h.D(100.0) == s["Dmin"] and h.D(0.0) == s["Dmax"]
    assert h.V(rx) == s["V100"] and h.V(1.5 * rx) == s["V150"] and h.V(2.0 * rx) == s["V200"]
    assert h.V(rx, relative=False) == pytest.approx(s["V100"] * s["volume_cc"])
    assert h.volume_cc == s["volume_cc"]
    assert h.stats(rx) == s
    # vectorised D
    np.testing.assert_array_equal(h.D([90.0, 50.0, 100.0]), [s["D90"], s["D50"], s["Dmin"]])
    # ties: a DVH built from the same multiset agrees whatever the input order
    tie = np.array([3.0, 1.0, 3.0, 2.0, 1.0, 3.0])
    ht = DVH(np.sort(tie), 1.0)
    st = dose_stats(tie[::-1], 2.0)
    assert ht.D(90.0) == st["D90"] == 1.0 and ht.D(50.0) == st["D50"] == 2.0
    assert ht.V(2.0) == st["V100"] == 4.0 / 6.0
    # an empty DVH is nan, like dose_stats
    he = DVH(np.zeros(0), 1.0)
    assert np.isnan(he.D(90.0)) and np.isnan(he.V(1.0))
    assert he.stats(2.0)["n"] == 0


def test_dvh_from_volume_matches_dose_stats_on_the_masked_values():
    arr = np.arange(1000, dtype=float).reshape(10, 10, 10) + 1.0
    vol = Volume(arr, np.diag([2.0, 2.0, 2.0, 1.0]))
    mask = arr > 900.0
    h = dvh(vol, mask)
    s = dose_stats(arr[mask], 950.0, voxel_volume_mm3=8.0)
    assert h.D(90.0) == s["D90"] == 910.0
    assert h.V(950.0) == s["V100"] == 0.51
    assert dose_metrics(vol, mask, 950.0) == s


# ------------------------------------------------------------ formatting
def test_format_report_prints_na_for_an_empty_shell():
    rx = 6000.0
    rep = {0.0: {"stats": dose_stats([7000.0, 5000.0], rx), "curve_x": np.zeros(2),
                 "curve_y": np.zeros(2)},
           5.0: {"stats": dose_stats([], rx), "curve_x": np.zeros(2), "curve_y": np.zeros(2)}}
    text = format_report(rep, rx)
    lines = text.splitlines()
    assert lines[1].startswith("wall") and "50%" in lines[1]
    assert lines[2].startswith("+5 mm") and "n/a" in lines[2] and "nan" not in lines[2]


# ------------------------------------------------------------ performance
def test_dose_stats_is_fast_enough_for_the_report_path():
    rng = np.random.default_rng(0)
    d = rng.uniform(0.0, 15000.0, size=1_000_000)
    w = rng.uniform(0.5, 2.0, size=d.size)
    t0 = time.perf_counter()
    dose_stats(d, 6000.0, weights=w)
    dose_stats(d, 6000.0)
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, "two dose_stats on 1e6 samples took %.2f s" % elapsed
