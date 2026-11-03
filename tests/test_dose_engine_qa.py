"""Engine QA (Paper 2, Phase 3, task ``engine-qa``; Supplement S3 / Table 3).

Pins ``TG43Engine("tg43u1s2")`` to

* the TG-43U1S2 consensus tables as embedded in Paper 1's engine
  (``gtcore/dose/qa_data/tg43u1s2_cs1rev2.json``): Lambda, L, g_L(r) and
  F(r, theta) reproduced exactly at the table nodes, interpolation between
  nodes monotone / bounded;
* the reference-point identity D(1 cm, 90 deg) = Lambda * S_K;
* the report's QA dose-rate tables for the IsoRay CS-1 Rev2: Table XII
  (polar, erratum-corrected values) and Table XXIII (along-away, as
  printed);
* Paper 1's engine itself, when its ``GammaTile.py`` is available
  (``$GT_PAPER1_ENGINE``; otherwise that one test is skipped).

Every tolerance below is taken from ``scripts/engine_crosscheck.py``
(numbers in ``docs/paper2-phase-notes.md``, "Phase 3 -- engine QA"):
gtcore vs Table XII max 0.286 % (3-4 significant figures published);
vs Table XXIII max 0.244 %; vs Paper 1 at common nodes 0 (bit-identical
arithmetic) except the one documented table difference (0.100 %), and
<= 0.861 % off the nodes, all of it the g_L interpolation rule (Paper 1
linear, gtcore log-linear per TG-43U1S1 Eq. 3), largest between the 9 and
10 cm nodes.
"""
from __future__ import annotations

import numpy as np
import pytest

from gtcore.dose import TG43Engine, dose_at_points
from gtcore.dose.engine import TG43U1S2_CONSENSUS
from gtcore.dose.qa_data import (ALONG_AWAY_FIXTURE, PAPER1_ENGINE_ENV,
                                 extract_paper1_interpolator,
                                 load_along_away, load_consensus_fixture,
                                 load_table_xii, paper1_engine_path)

#: Table entries where Paper 1's embedded data and gtcore's differ --
#: (theta_deg, r_cm): (paper1, gtcore).  The printed report (Table AXIII,
#: IROC-hosted PDF, checked 2026-11-03) reads 0.998, i.e. gtcore's value;
#: Paper 1's 0.999 is a transcription slip.  Kept explicit so that a change
#: on either side is a conscious decision, not a silent drift.
KNOWN_FIXTURE_DIFFERENCES = {(80.0, 7.0): (0.999, 0.998)}

LAMBDA = 1.056
TOL_TABLE_XII_PCT = 0.5       # measured max 0.286 % at (5 cm, 80 deg)
TOL_TABLE_XXIII_PCT = 0.5     # measured max 0.244 % at (70 mm, 70 mm)
TOL_OFF_NODE_PCT = 1.0        # measured max 0.861 % (9.42 cm, 80 deg)


@pytest.fixture(scope="module")
def eng():
    return TG43Engine("tg43u1s2")


@pytest.fixture(scope="module")
def fx():
    return load_consensus_fixture()


def _nodes(fx):
    """(theta, r, value) for every tabulated (non-null) F entry."""
    F = fx["F"]
    out = []
    for i, t in enumerate(F["theta_deg"]):
        for j, r in enumerate(F["r_cm"]):
            v = F["values"][i, j]
            if not np.isnan(v):
                out.append((float(t), float(r), float(v)))
    return out


# ------------------------------------------------------- fixture provenance
def test_fixture_carries_its_provenance(fx):
    p = fx["_provenance"]
    assert "10.1002/mp.12430" in p["paper"]
    assert "DoseInterpolator" in p["copied_from"] and "TG43U1S2" in p["copied_from"]
    assert "10.1002/mp.12728" in p["erratum_applied"]
    assert set(p["tables_as_stated_in_source_comments"]) >= {
        "dose_rate_constant", "active_length", "g_L", "F"}
    assert fx["phi_an"] is None and "AXIV" in fx["phi_an_note"]
    assert fx["source_model"].startswith("IsoRay")


# --------------------------------------------------- tables vs the fixture
def test_lambda_and_active_length_match_fixture(eng, fx):
    ds = eng.dataset
    assert ds is TG43U1S2_CONSENSUS
    assert ds.dose_rate_constant == fx["dose_rate_constant_cGy_per_h_per_U"] == LAMBDA
    assert ds.active_length_cm == fx["active_length_cm"] == 0.40
    assert eng.LAMBDA == LAMBDA and eng.L == 0.40
    # Half-life behind tau (Table XXIV); Paper 1 stores the mean life.
    assert eng.T_HALF_HOURS == pytest.approx(fx["half_life_days"] * 24.0)
    assert eng.TAU_HOURS == pytest.approx(fx["paper1_mean_life_h"], rel=1e-5)


def test_radial_dose_table_matches_fixture_exactly(eng, fx):
    ds = eng.dataset
    assert np.array_equal(ds.g_radii, fx["g_L"]["r_cm"])
    assert np.array_equal(ds.g_values, fx["g_L"]["values"])
    assert ds.g_radii.size == 16 and ds.g_radii[0] == 0.1 and ds.g_radii[-1] == 10.0


def test_anisotropy_table_matches_fixture_except_documented_entries(eng, fx):
    ds = eng.dataset
    F = fx["F"]
    assert np.array_equal(ds.f_thetas, F["theta_deg"])
    assert np.array_equal(ds.f_radii, F["r_cm"])
    a, b = F["values"], ds.f_table
    assert a.shape == b.shape == (15, 10)
    same = (a == b) | (np.isnan(a) & np.isnan(b))
    found = {(float(F["theta_deg"][i]), float(F["r_cm"][j])): (float(a[i, j]), float(b[i, j]))
             for i, j in np.argwhere(~same)}
    assert found == KNOWN_FIXTURE_DIFFERENCES, (
        "F(r, theta) differences between Paper 1's data and gtcore changed: %r"
        % (found,))
    # Hole pattern identical: r = 0.1 cm untabulated for theta <= 20 deg.
    holes = {tuple(h) for h in F["untabulated_entries_theta_r"]}
    assert holes == {(t, 0.1) for t in (0.0, 2.0, 5.0, 7.0, 10.0, 15.0, 20.0)}
    assert {(float(F["theta_deg"][i]), float(F["r_cm"][j]))
            for i, j in np.argwhere(np.isnan(b))} == holes


# ------------------------------------------------- engine reproduces nodes
def test_radial_dose_reproduces_every_node_exactly(eng, fx):
    r, v = fx["g_L"]["r_cm"], fx["g_L"]["values"]
    got = eng._radial_dose(r)
    assert np.allclose(got, v, rtol=0.0, atol=1e-12), got - v
    # Scalar path and the g(1 cm) = 1 normalisation.
    assert float(eng._radial_dose(1.0)) == pytest.approx(1.0, abs=1e-12)


def test_anisotropy_reproduces_every_node_exactly(eng, fx):
    for theta, r, v in _nodes(fx):
        got = float(eng._anisotropy(r, theta))
        expect = KNOWN_FIXTURE_DIFFERENCES.get((theta, r), (v, v))[1]
        assert got == pytest.approx(expect, abs=1e-12), (theta, r, got, expect)
    assert np.all(eng._anisotropy(fx["F"]["r_cm"], 90.0) == 1.0)


def test_untabulated_anisotropy_entries_are_filled_from_the_nearest_radius_and_sit_inside_the_capsule(eng, fx):
    """Port-notes item 4 (NaN holes), re-checked on the consensus table:
    the fill is the value at the first tabulated radius (0.25 cm) of the
    same theta, and every hole lies inside the titanium can, so no tissue
    point ever reads a borrowed value (interior points are projected to
    the capsule surface before F is looked up)."""
    F = fx["F"]
    ir = {float(r): j for j, r in enumerate(F["r_cm"])}
    it = {float(t): i for i, t in enumerate(F["theta_deg"])}
    for theta, r in F["untabulated_entries_theta_r"]:
        filled = eng._F_FILLED[it[theta], ir[r]]
        assert filled == F["values"][it[theta], ir[0.25]]
        y = r * np.sin(np.radians(theta))          # perpendicular, cm
        z = r * np.cos(np.radians(theta))          # axial, cm
        assert y < eng._RHO_SURFACE and z <= eng._CAP_HALF_CM


# ------------------------------------------- interpolation between nodes
def test_radial_dose_interpolation_is_monotone_and_bounded_between_nodes(eng, fx):
    r, v = fx["g_L"]["r_cm"], fx["g_L"]["values"]
    for r0, r1, v0, v1 in zip(r[:-1], r[1:], v[:-1], v[1:]):
        rr = np.linspace(r0, r1, 27)[1:-1]
        g = eng._radial_dose(rr)
        lo, hi = min(v0, v1) - 1e-12, max(v0, v1) + 1e-12
        assert np.all((g >= lo) & (g <= hi)), (r0, r1)
        d = np.diff(np.concatenate([[v0], g, [v1]]))
        if v1 > v0:
            assert np.all(d > 0), (r0, r1)
        elif v1 < v0:
            assert np.all(d < 0), (r0, r1)
        else:
            assert np.allclose(d, 0.0, atol=1e-12)
    # Below the first node: nearest neighbour; the table's domain edge.
    assert float(eng._radial_dose(0.05)) == pytest.approx(v[0], abs=1e-12)


def test_anisotropy_interpolation_is_bounded_by_the_enclosing_nodes(eng, fx):
    rng = np.random.default_rng(7)
    F = fx["F"]
    radii, thetas, table = F["r_cm"], F["theta_deg"], eng._F_FILLED
    r = np.exp(rng.uniform(np.log(0.1), np.log(10.0), 4000))
    t = rng.uniform(0.0, 90.0, 4000)
    val = eng._anisotropy(r, t)
    ir = np.clip(np.searchsorted(radii, r, side="right") - 1, 0, radii.size - 2)
    it = np.clip(np.searchsorted(thetas, t, side="right") - 1, 0, thetas.size - 2)
    corners = np.stack([table[it, ir], table[it, ir + 1],
                        table[it + 1, ir], table[it + 1, ir + 1]])
    assert np.all(val >= corners.min(axis=0) - 1e-12)
    assert np.all(val <= corners.max(axis=0) + 1e-12)
    assert np.all((val > 0.5) & (val <= 1.2))


# ------------------------------------------------------- reference point
@pytest.mark.parametrize("sk_u", [1.0, 3.5])
def test_reference_point_dose_rate_is_lambda_times_sk(eng, sk_u):
    """D(r0 = 1 cm, theta0 = 90 deg) = Lambda * S_K [cGy/h], exactly: the
    geometry ratio is 1 by construction and g_L(1) = F(1, 90) = 1 at the
    consensus table nodes."""
    assert eng.dose_rate(90.0, 1.0) == pytest.approx(LAMBDA, rel=1e-12)
    assert eng.dose_rate(90.0, 1.0) * sk_u == pytest.approx(LAMBDA * sk_u, rel=1e-12)
    # Through the public point-evaluation path: one seed along +z at the
    # origin, field point 10 mm along +x (theta = 90 deg); total-to-decay
    # dose / tau is the initial dose rate.
    total = dose_at_points([[0.0, 0.0, 0.0]], [[0.0, 0.0, 1.0]],
                           [10.0, 0.0, 0.0], sk_per_seed_u=sk_u, engine=eng)
    assert total / eng.TAU_HOURS == pytest.approx(LAMBDA * sk_u, rel=1e-12)
    assert eng.total_dose_at_point(90.0, 1.0, sk_u) == pytest.approx(
        LAMBDA * sk_u * eng.TAU_HOURS, rel=1e-12)


# --------------------------------------------------------- QA tables
def test_table_xii_erratum_dose_rates_reproduced(eng, fx):
    """Published dose rate per unit S_K at the 143 (r, theta) nodes, 3-4
    significant figures: the largest residual, 0.286 % at (5 cm, 80 deg),
    is the rounding of 0.0222 (half a unit in the last place = 0.23 %)."""
    tab = load_table_xii()
    assert tab["rate"].size == 143
    assert set(tab["r_cm"].tolist()) <= set(fx["F"]["r_cm"].tolist())
    assert set(tab["theta_deg"].tolist()) <= set(fx["F"]["theta_deg"].tolist())
    # along/away are stored to 0.1 um; compare at that resolution (cm).
    assert np.allclose(np.hypot(tab["along_mm"], tab["away_mm"]) / 10.0, tab["r_cm"],
                       rtol=0.0, atol=1e-4)
    rate = eng.dose_rate(tab["theta_deg"], tab["r_cm"])
    rel = 100.0 * (rate / tab["rate"] - 1.0)
    assert np.abs(rel).max() < TOL_TABLE_XII_PCT, (
        "worst %+.3f %% at r=%g cm theta=%g deg"
        % (rel[np.argmax(np.abs(rel))], tab["r_cm"][np.argmax(np.abs(rel))],
           tab["theta_deg"][np.argmax(np.abs(rel))]))
    assert np.median(np.abs(rel)) < 0.1
    # The reference node of the table is Lambda itself.
    i = np.flatnonzero((tab["r_cm"] == 1.0) & (tab["theta_deg"] == 90.0))[0]
    assert tab["rate"][i] == LAMBDA


def test_along_away_qa_table_reproduced(eng):
    """Table XXIII (along-away) as printed in the 2017 report.  Skipped --
    with the expected CSV layout in the reason -- if the fixture is absent."""
    aa = load_along_away()
    if aa is None:
        pytest.skip("gtcore/dose/qa_data/tg43u1s2_along_away.csv absent; expected "
                    "layout: '#' comment lines, then along_mm,away_mm,"
                    "dose_rate_cGy_per_h_per_U (one row per point; empty cell "
                    "for untabulated)")
    assert aa["rate"].size == 149
    rate = eng.dose_rate(aa["theta_deg"], aa["r_cm"])
    rel = 100.0 * (rate / aa["rate"] - 1.0)
    k = int(np.argmax(np.abs(rel)))
    assert np.abs(rel).max() < TOL_TABLE_XXIII_PCT, (
        "worst %+.3f %% at along=%g mm away=%g mm"
        % (rel[k], aa["along_mm"][k], aa["away_mm"][k]))
    assert np.median(np.abs(rel)) < 0.1
    # Cross-fixture consistency: the transverse row of Table XXIII (along =
    # 0) must equal Table XII's 90 deg column at the common radii.
    tab = load_table_xii()
    xii90 = {float(r): v for r, t, v in zip(tab["r_cm"], tab["theta_deg"], tab["rate"])
             if t == 90.0}
    for a, b, v in zip(aa["along_mm"], aa["away_mm"], aa["rate"]):
        if a == 0.0 and (b / 10.0) in xii90:
            assert v == xii90[b / 10.0], (b, v, xii90[b / 10.0])
    assert ALONG_AWAY_FIXTURE.exists()


# ------------------------------------------- cross-check vs Paper 1's engine
def test_paper1_radial_interpolation_rule_differs_by_less_than_one_percent(eng, fx):
    """Paper 1 interpolates g_L linearly in r (numpy.interp); gtcore
    log-linearly (TG-43U1S1 Eq. 3).  Both pass through the nodes; between
    them the difference peaks in the widest, steepest interval (9-10 cm) at
    0.87 % -- the whole of the off-node disagreement measured by
    scripts/engine_crosscheck.py.  Evaluated on the fixture so it runs
    without Paper 1's source."""
    r_nodes, v_nodes = fx["g_L"]["r_cm"], fx["g_L"]["values"]
    r = np.unique(np.concatenate([np.geomspace(0.3, 10.0, 2000), r_nodes[r_nodes >= 0.3]]))
    paper1 = np.interp(r, r_nodes, v_nodes)
    gt = eng._radial_dose(r)
    rel = 100.0 * (gt / paper1 - 1.0)
    on_node = np.isin(r, r_nodes)
    assert np.allclose(rel[on_node], 0.0, atol=1e-10)
    assert np.abs(rel).max() < TOL_OFF_NODE_PCT
    k = int(np.argmax(np.abs(rel)))
    assert 9.0 < r[k] < 10.0 and abs(rel[k]) == pytest.approx(0.874, abs=0.02)
    assert np.all(rel <= 1e-10)          # log-linear never exceeds linear here


@pytest.mark.skipif(paper1_engine_path() is None,
                    reason="set %s to Paper 1's GammaTile.py (or its clone root) "
                           "to run the live cross-check" % PAPER1_ENGINE_ENV)
def test_crosscheck_against_paper1_engine(eng, fx):
    cls = extract_paper1_interpolator(paper1_engine_path())
    p1 = cls(dataset="TG43U1S2")
    conv = float(cls.DOSE_CONVERSION_FACTOR)

    def p1_rate(theta, r):
        th, rr = np.broadcast_arrays(np.atleast_1d(np.asarray(theta, float)),
                                     np.atleast_1d(np.asarray(r, float)))
        return p1.dose_at_points(th.ravel(), rr.ravel()).reshape(th.shape) / conv

    # Reference point and the conversion factor (tau * 3.5 U).
    assert float(p1_rate(90.0, 1.0)[0]) == pytest.approx(LAMBDA, rel=1e-12)
    assert conv == pytest.approx(eng.TAU_HOURS * 3.5, rel=1e-5)

    # Common table nodes: bit-identical arithmetic except the documented
    # F(7 cm, 80 deg) entry (0.999 vs 0.998 -> 0.1 %).
    common_r = [0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0]
    for theta in fx["F"]["theta_deg"]:
        for r in common_r:
            a = float(eng.dose_rate(theta, r))
            b = float(p1_rate(theta, r)[0])
            if (float(theta), r) in KNOWN_FIXTURE_DIFFERENCES:
                assert a / b == pytest.approx(0.998 / 0.999, rel=1e-9)
            else:
                assert a == pytest.approx(b, rel=1e-9), (theta, r)

    # Off the nodes: within TOL_OFF_NODE_PCT everywhere on r = 0.3-10 cm.
    TH, R = np.meshgrid(np.array([0.0, 1.0, 3.0, 10.0, 33.0, 60.0, 89.0, 90.0]),
                        np.array([0.3, 0.45, 1.3, 2.5, 4.4, 7.5, 9.5]), indexing="ij")
    rel = 100.0 * (eng.dose_rate(TH, R) / p1_rate(TH, R) - 1.0)
    assert np.abs(rel).max() < TOL_OFF_NODE_PCT, rel

    # Random 32-seed cloud, points >= 3 mm from every seed: < 1 %.
    rng = np.random.default_rng(0)
    centers = rng.uniform(-20.0, 20.0, (32, 3))
    axes = rng.normal(size=(32, 3))
    axes /= np.linalg.norm(axes, axis=1)[:, None]
    pts = rng.uniform(-40.0, 40.0, (300, 3))
    far = np.linalg.norm(pts[:, None, :] - centers[None], axis=2).min(axis=1) >= 3.0
    pts = pts[far]
    gt = dose_at_points(centers, axes, pts, sk_per_seed_u=3.5, engine=eng) \
        / (eng.TAU_HOURS * 3.5)
    acc = np.zeros(pts.shape[0])
    for c, a in zip(centers, axes):
        d = pts - c
        rr = np.linalg.norm(d, axis=1)
        z = d @ a
        y = np.sqrt(np.maximum(rr ** 2 - z ** 2, 0.0))
        acc += p1.dose_at_points(np.degrees(np.arctan2(y, np.abs(z))), rr / 10.0)
    rel = 100.0 * (gt / (acc / conv) - 1.0)
    assert np.abs(rel).max() < TOL_OFF_NODE_PCT

    # Known Paper 1 behaviours, still present in its source (port-notes
    # items 1 and 5): zero on the long axis inside r < L/2, and the whole
    # rate frozen beyond 10 cm.  gtcore is positive / keeps falling.
    assert float(p1_rate(0.0, 0.15)[0]) == 0.0 and eng.dose_rate(0.0, 0.15) > 0.0
    assert float(p1_rate(90.0, 12.0)[0]) == float(p1_rate(90.0, 10.0)[0])
    assert eng.dose_rate(90.0, 12.0) < 0.75 * eng.dose_rate(90.0, 10.0)
