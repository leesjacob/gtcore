"""Tests for ``gtcore.plan.final_report`` (section 3 G, branch plan/validation).

Paper 2 Phase 2 D (2026-11-03): ``metrics_grid["RIND5"]`` is the volumetric
HR-CTV (``targets.rind5`` of the cavity mask on the final dose grid, scored
by ``dose_stats`` on the exact-kernel dose at its voxel centres, with the
``Target``'s provenance); the shell rows moved to ``metrics_grid["S5"]`` and
are labelled "S5 coverage" (a surrogate); without a cavity mask RIND5 is
absent and ``notes`` says so.
"""
from __future__ import annotations

import csv
import json

import numpy as np
import pytest
import trimesh

import plan_fixtures as pf
from gtcore.dose.dvh import shell_report
from gtcore.dose.engine import dose_at_points
from gtcore.dose.hrctv import build_hrctv, hrctv_stats
from gtcore.dose.metrics import DOSE_STAT_KEYS, dose_stats, weighted_quantile
from gtcore.dose.targets import (rind5, same_grid, target_doses_exact,
                                 target_doses_from_grid)
from gtcore.interact import conform_tile, snap_to_wall
from gtcore.plan import DEFAULT_RX_CGY, OptimizeReport, TargetSet, final_report
from gtcore.plan.report import (NO_CAVITY_NOTE, grid_bounds, rind5_doses_on_grid,
                                weighted_stats)

GRID_MM = 2.0
RX = 3000.0   # two tiles 24 mm apart do not reach 6000 cGy at +5 mm; halve rx so V100 > 0
MARGIN_MM = 12.0

SPHERE_R_MM = 20.0
SPHERE_RX = DEFAULT_RX_CGY


@pytest.fixture(scope="module")
def flat_two_tiles():
    mesh = pf.flat_wall_mesh(size_mm=60.0, step_mm=2.0, depth_mm=30.0)
    tiles = []
    for x in (-12.0, 12.0):
        surf, n_in = snap_to_wall(mesh, np.array([x, 0.0, 0.0]))
        tiles.append(conform_tile(mesh, surf, n_in, np.array([1.0, 0.0, 0.0])))
    return mesh, tiles


@pytest.fixture(scope="module")
def report(flat_two_tiles):
    mesh, tiles = flat_two_tiles
    return final_report(mesh, tiles, rx_cgy=RX, grid_mm=GRID_MM,
                        margin_mm=MARGIN_MM, parameters={"seed": 7, "h_mm": 2.5})


def _seed_arrays(tiles):
    return (np.vstack([t.seed_centers for t in tiles]),
            np.vstack([t.seed_axes for t in tiles]))


@pytest.fixture(scope="module")
def sphere_cavity():
    """A spherical cavity: icosphere wall (R = 20 mm, vertices at +-20 on the
    axes), the cavity mask on a 1 mm grid whose lattice sits 0.3 mm off the
    integer positions, a half-space clip (x > -8, the "brain") and four
    conformed tiles at the poles."""
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=SPHERE_R_MM)
    lo = np.array([-30.3, -30.3, -30.3])
    n = 61
    aff = np.eye(4)
    aff[:3, 3] = lo
    k, j, i = np.mgrid[:n, :n, :n]
    xyz = np.stack([i + lo[0], j + lo[1], k + lo[2]], axis=-1).astype(float)
    cav = np.linalg.norm(xyz, axis=-1) <= SPHERE_R_MM
    clip = xyz[..., 0] > -8.0
    tiles = []
    for d, up in (((0, 0, 1), (1, 0, 0)), ((1, 0, 0), (0, 1, 0)),
                  ((0, 1, 0), (1, 0, 0)), ((0, 0, -1), (1, 0, 0))):
        surf, n_in = snap_to_wall(mesh, SPHERE_R_MM * np.asarray(d, dtype=float))
        tiles.append(conform_tile(mesh, surf, n_in, np.asarray(up, dtype=float)))
    return mesh, tiles, cav, aff, clip


def _sphere_report(sphere_cavity, margin_mm, **kw):
    mesh, tiles, cav, aff, clip = sphere_cavity
    return final_report(mesh, tiles, rx_cgy=SPHERE_RX, grid_mm=1.0, margin_mm=margin_mm,
                        cavity_mask=cav, cavity_affine=aff, clip_mask=clip, clip_affine=aff,
                        clip_source="gtcore:brain", parameters={"shadowing": False}, **kw)


@pytest.fixture(scope="module")
def sphere_report(sphere_cavity):
    """Integer margin: the 1 mm dose grid (mesh bounds - 12 mm) shares its
    lattice with ``build_hrctv``'s rind grid (mesh bounds - 7 mm), exactly as
    in ``gt optimize`` (margin 50 mm)."""
    return _sphere_report(sphere_cavity, 12.0)


# ------------------------------------------------------------------ helpers
def test_weighted_quantile_matches_numpy_for_unit_weights():
    """The report uses the one type-1 (inverted CDF) quantile of
    ``gtcore.dose.metrics``; the former midpoint-interpolating fallback is
    gone (Paper 2 Phase 2, one ``dose_stats``)."""
    rng = np.random.default_rng(0)
    v = rng.normal(size=2001)
    for q in (0.1, 0.5, 0.9):
        assert weighted_quantile(v, np.ones_like(v), q) == \
            np.percentile(v, 100 * q, method="inverted_cdf")


def test_weighted_quantile_respects_weights():
    v = np.array([1.0, 2.0, 3.0])
    w = np.array([1.0, 1.0, 100.0])
    assert weighted_quantile(v, w, 0.5) == 3.0
    with pytest.raises(ValueError):
        weighted_quantile([], [], 0.5)
    st = weighted_stats([], [], 6000.0)
    assert np.isnan(st["D90"]) and np.isnan(st["V100"]) and st["n"] == 0


def test_weighted_stats_fractions():
    d = np.array([5000.0, 7000.0, 9500.0, 13000.0])
    w = np.array([1.0, 1.0, 1.0, 1.0])
    st = weighted_stats(d, w, 6000.0)
    assert st["V100"] == pytest.approx(0.75)
    assert st["V150"] == pytest.approx(0.5)
    assert st["V200"] == pytest.approx(0.25)
    assert st["Dmean"] == pytest.approx(d.mean())
    assert st["total_weight"] == 4.0
    w2 = np.array([10.0, 1.0, 1.0, 1.0])
    assert weighted_stats(d, w2, 6000.0)["V100"] == pytest.approx(3.0 / 13.0)


def test_grid_bounds_clips_margin():
    b = np.array([[0.0, 0.0, 0.0], [40.0, 40.0, 40.0]])
    bounds, used = grid_bounds(b, 50.0, 1.0, max_voxels=1_000_000)
    assert used < 50.0
    n = np.prod(np.floor((bounds[1] - bounds[0]) + 1e-9).astype(int) + 1)
    assert n <= 1_000_000
    bounds2, used2 = grid_bounds(b, 10.0, 1.0)
    assert used2 == 10.0 and np.allclose(bounds2, [[-10] * 3, [50] * 3])


# ------------------------------------------------------------------- report
def test_report_fields_populated(report, flat_two_tiles):
    mesh, tiles = flat_two_tiles
    assert isinstance(report, OptimizeReport)
    assert len(report.tiles) == 2
    assert report.overlaps == []
    assert report.gtcore_version
    assert report.seed == 7
    assert report.wall_clock_s > 0
    # Phase 2 D layout: shells under "S5", no top-level float keys, no "rind"
    assert set(report.metrics_grid) == {"S5", "target"}
    assert set(report.metrics_grid["S5"]) == {0.0, 5.0, 10.0}
    assert not any(isinstance(k, float) for k in report.metrics_grid)
    for off in (0.0, 5.0, 10.0):
        st = report.metrics_grid["S5"][off]
        for key in ("V100", "D90", "V150", "V200"):
            assert key in st
        assert 0.0 <= st["V100"] <= 1.0
        assert st["curve_x"].shape == st["curve_y"].shape
    assert report.metrics_grid["S5"][5.0]["V100"] > 0.0        # seeds deliver dose
    assert report.metrics_grid["target"]["name"] == "shell+5mm"
    assert report.parameters["grid_mm"] == GRID_MM
    assert report.parameters["grid_margin_used_mm"] == MARGIN_MM
    assert report.parameters["n_seeds"] == 8
    assert report.parameters["h_mm"] == 2.5
    assert report.parameters["rind_depth_mm"] == 5.0
    assert len(report.parameters["grid_shape"]) == 3
    assert "dose_grid" in report.runtime and "total" in report.runtime
    assert report.metrics_grid_interference is None
    assert isinstance(report.shadowing, list)
    # the final grid travels with the report (not serialised)
    assert report.dose_volume is not None
    assert list(report.dose_volume.array.shape) == report.parameters["grid_shape"]
    # no cavity mask: RIND5 absent, and the report says so explicitly
    assert "RIND5" not in report.metrics_grid and "rind" not in report.metrics_grid
    assert NO_CAVITY_NOTE in report.notes
    s = report.summary()
    assert "S5 coverage +5 mm" in s and "shell 5.0" not in s
    assert "HR-CTV RIND5 (" not in s
    assert "RIND5 (volumetric HR-CTV) not computed: no cavity mask" in s


def test_report_json_and_csv_write(report, tmp_path):
    jp = tmp_path / "r.json"
    cp = tmp_path / "r.csv"
    report.to_json(jp)
    report.to_csv(cp)
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert data["n_tiles"] == 2
    assert "dose_volume" not in data
    assert "5.0" in data["metrics_grid"]["S5"] and "5.0" not in data["metrics_grid"]
    assert 0.0 <= data["metrics_grid"]["S5"]["5.0"]["V100"] <= 1.0
    assert data["parameters"]["grid_mm"] == GRID_MM
    with open(cp, newline="", encoding="utf-8") as fh:
        keys = {row["key"] for row in csv.DictReader(fh)}
    assert "metrics_grid.S5.5.0.V100" in keys
    assert "metrics_grid.target.V100" in keys
    assert not any(k.startswith("metrics_grid.5.0") for k in keys)


def test_full_target_grid_vs_point_consistency(report, flat_two_tiles):
    """Grid-sampled weighted V100 on the full target agrees with the direct
    exact ``dose_at_points`` evaluation on the same points within 1 pp."""
    mesh, tiles = flat_two_tiles
    target = TargetSet.from_shell(mesh, 5.0)
    centers, axes = _seed_arrays(tiles)
    direct = dose_at_points(centers, axes, target.points, exact=True)
    ref = weighted_stats(direct, target.weights, RX)
    got = report.metrics_grid["target"]
    assert got["n"] == len(target)
    assert abs(got["V100"] - ref["V100"]) <= 0.01
    assert abs(got["D90"] - ref["D90"]) / RX < 0.03


def test_report_sk_scaling_and_interference(flat_two_tiles):
    mesh, tiles = flat_two_tiles
    base = final_report(mesh, tiles, grid_mm=GRID_MM, margin_mm=MARGIN_MM,
                        parameters={"shadowing": False})
    up = final_report(mesh, tiles, grid_mm=GRID_MM, margin_mm=MARGIN_MM,
                      parameters={"sk_per_seed_u": base.parameters["sk_per_seed_u"] * 1.05,
                                  "shadowing": False})
    assert up.metrics_grid["S5"][5.0]["D90"] == pytest.approx(
        1.05 * base.metrics_grid["S5"][5.0]["D90"], rel=1e-6)
    assert up.metrics_grid["S5"][5.0]["V100"] >= base.metrics_grid["S5"][5.0]["V100"]

    interf = final_report(mesh, tiles, grid_mm=GRID_MM, margin_mm=MARGIN_MM,
                          interference=True, parameters={"shadowing": False})
    assert interf.metrics_grid_interference is not None
    assert 5.0 in interf.metrics_grid_interference["S5"]
    assert "RIND5" not in interf.metrics_grid_interference      # no cavity mask
    # capsules only attenuate: never more dose with the model on
    assert (interf.metrics_grid_interference["S5"][5.0]["Dmean"]
            <= interf.metrics_grid["S5"][5.0]["Dmean"] + 1e-9)
    note = [n for n in interf.notes if n.startswith("interference")]
    assert note and "S5 coverage +5 mm" in note[0] and "RIND5" not in note[0]


def test_report_rind5_with_a_coarse_cavity_mask(flat_two_tiles):
    """The pre-Phase-2 ``"rind"`` case: a box cavity on a 2 mm lattice is now
    ``metrics_grid["RIND5"]`` built on the (2 mm) dose grid."""
    mesh, tiles = flat_two_tiles
    sp = 2.0
    lo = np.array([-30.0, -30.0, -30.0])
    n = 31
    affine = np.eye(4)
    affine[0, 0] = affine[1, 1] = affine[2, 2] = sp
    affine[:3, 3] = lo
    zz = lo[2] + sp * np.arange(n)
    mask = np.zeros((n, n, n), dtype=bool)
    mask[zz < 0.0, :, :] = True
    rep = final_report(mesh, tiles, grid_mm=GRID_MM, margin_mm=MARGIN_MM,
                       cavity_mask=mask, cavity_affine=affine,
                       parameters={"shadowing": False})
    assert "RIND5" in rep.metrics_grid and "rind" not in rep.metrics_grid
    r = rep.metrics_grid["RIND5"]
    assert r["volume_cc"] > 0 and 0.0 <= r["V100"] <= 1.0
    assert r["provenance"]["grid"]["spacing_mm"] == [GRID_MM] * 3
    assert r["provenance"]["grid"]["shape"] == rep.parameters["grid_shape"]
    assert r["provenance"]["clip_source"] is None
    assert "rind5" in rep.runtime
    assert not any("RIND5" in n and "not computed" in n for n in rep.notes)


def test_report_empty_tiles_and_bad_mesh(flat_two_tiles):
    mesh, _ = flat_two_tiles
    rep = final_report(mesh, [], grid_mm=4.0, margin_mm=5.0)
    assert rep.metrics_grid["S5"][5.0]["V100"] == 0.0
    assert any("no tiles" in n for n in rep.notes)
    with pytest.raises(ValueError, match="mesh is empty"):
        final_report(trimesh.Trimesh(), [], grid_mm=2.0)


# ------------------------------------------------------- RIND5 (Phase 2 D)
def test_rind5_is_the_volumetric_hrctv_on_the_dose_grid(sphere_cavity, sphere_report):
    """``metrics_grid["RIND5"]`` = ``dose_stats`` on ``rind5(cavity, clip)``
    built on the final grid, exact-kernel dose at the voxel centres, with
    provenance.  The doses are read from the grid: bit-identical to
    ``target_doses_exact`` at the same points; ``target_doses_from_grid``
    (trilinear, float32 cast in ``Volume.sample_ras``) agrees to ~1e-7."""
    mesh, tiles, cav, aff, clip = sphere_cavity
    rep = sphere_report
    assert set(rep.metrics_grid) == {"RIND5", "S5", "target"}
    r = rep.metrics_grid["RIND5"]
    assert set(r) - {"target", "provenance"} == set(DOSE_STAT_KEYS)
    assert r["target"] == "RIND5"
    p = r["provenance"]
    assert p["cavity_source"] == "gtcore:cavity" and p["clip_source"] == "gtcore:brain"
    assert p["builder"] == "rind5" and p["depth_mm"] == 5.0
    dv = rep.dose_volume
    assert p["grid"]["shape"] == list(dv.array.shape)
    assert np.allclose(p["grid"]["origin_ras"], dv.affine[:3, 3])
    assert p["grid"]["spacing_mm"] == [1.0] * 3
    assert r["n"] > 0 and r["volume_cc"] == pytest.approx(r["n"] / 1000.0)
    assert 0.0 < r["V100"] < 1.0 and np.isfinite(r["D90"])
    full_shell_cc = 4.0 / 3.0 * np.pi * ((SPHERE_R_MM + 5.0) ** 3 - SPHERE_R_MM ** 3) / 1000.0
    assert 0.5 * full_shell_cc < r["volume_cc"] < full_shell_cc      # clipped, outside

    # (a) the same target rebuilt on the dose grid, doses read from the grid
    t = rind5(cav, aff, clip_mask=clip, clip_affine=aff,
              grid_affine=dv.affine, grid_shape=dv.array.shape)
    assert t.n == r["n"]
    d_grid = rind5_doses_on_grid(t, dv)
    ref = dose_stats(d_grid, SPHERE_RX, voxel_volume_mm3=t.voxel_volume_mm3)
    for key in DOSE_STAT_KEYS:
        assert r[key] == pytest.approx(ref[key], abs=1e-9), key

    # (b) bit-identical to the exact engine at the rind voxel centres
    centers, axes = _seed_arrays(tiles)
    d_exact = target_doses_exact(t, centers, axes, rep.parameters["sk_per_seed_u"])
    assert np.array_equal(d_exact, d_grid)
    ref_exact = dose_stats(d_exact, SPHERE_RX, voxel_volume_mm3=1.0)
    for key in ("D90", "V100", "V150", "volume_cc"):
        assert r[key] == pytest.approx(ref_exact[key], abs=1e-9), key

    # (c) trilinear sampling at the same centres returns the grid values to
    #     float32 precision (Volume.sample_ras casts the array)
    d_tri = target_doses_from_grid(t, dv)
    assert np.allclose(d_tri, d_grid, rtol=1e-6, atol=0.0)
    ref_tri = dose_stats(d_tri, SPHERE_RX, voxel_volume_mm3=1.0)
    assert r["D90"] == pytest.approx(ref_tri["D90"], rel=1e-6)
    assert r["V100"] == pytest.approx(ref_tri["V100"], abs=1e-6)
    assert r["V150"] == pytest.approx(ref_tri["V150"], abs=1e-6)


def test_rind5_equals_the_planner_hrctv_on_its_own_grid(sphere_cavity, sphere_report):
    """``build_hrctv`` (the planner's V key) builds RIND5 on its rind grid
    (mesh bounds - 7 mm, 1 mm); the report's grid is mesh bounds - 12 mm: a
    different grid on the same lattice, so the planner's and the report's
    volumetric HR-CTV numbers agree to rounding (far inside 2 %)."""
    mesh, tiles, cav, aff, clip = sphere_cavity
    rep = sphere_report
    r = rep.metrics_grid["RIND5"]
    h = build_hrctv(mesh, depth_mm=5.0, spacing_mm=1.0, inside_mask=cav, inside_affine=aff,
                    keep_mask=clip, keep_affine=aff, keep_source="gtcore:brain",
                    with_mesh=False)
    dv = rep.dose_volume
    assert not same_grid(h.affine, h.mask.shape, dv.affine, dv.array.shape)
    assert np.allclose((h.affine[:3, 3] - dv.affine[:3, 3]) % 1.0, 0.0)   # one lattice
    centers, axes = _seed_arrays(tiles)
    st = hrctv_stats(h.doses_exact(centers, axes, rep.parameters["sk_per_seed_u"]),
                     SPHERE_RX, h.voxel_volume_mm3)
    for key in ("D90", "V100", "V150", "V200", "Dmean", "volume_cc", "n"):
        assert r[key] == pytest.approx(st[key], rel=1e-9, abs=1e-9), key
    assert h.target.provenance["cavity_source"] == r["provenance"]["cavity_source"]
    assert h.target.provenance["clip_source"] == r["provenance"]["clip_source"]


def test_rind5_on_a_shifted_lattice_stays_within_tolerance(sphere_cavity):
    """A non-integer margin puts the dose grid 0.3 mm per axis off
    ``build_hrctv``'s lattice; the RIND5 numbers then move by the dose
    gradient over that 0.5 mm diagonal shift at the rind boundaries
    (measured on this toy: D90 -2.7 %, V100 +2.4 pp, V150 +0.35 pp, volume
    unchanged).  Pinned loosely to catch a gross error (units, side of the
    wall, grid); the sensitivity is recorded in docs/paper2-phase-notes.md."""
    mesh, tiles, cav, aff, clip = sphere_cavity
    rep = _sphere_report(sphere_cavity, 12.3)
    r = rep.metrics_grid["RIND5"]
    h = build_hrctv(mesh, depth_mm=5.0, spacing_mm=1.0, inside_mask=cav, inside_affine=aff,
                    keep_mask=clip, keep_affine=aff, with_mesh=False)
    dv = rep.dose_volume
    assert not np.allclose((h.affine[:3, 3] - dv.affine[:3, 3]) % 1.0, 0.0)
    centers, axes = _seed_arrays(tiles)
    st = hrctv_stats(h.doses_exact(centers, axes, rep.parameters["sk_per_seed_u"]),
                     SPHERE_RX, h.voxel_volume_mm3)
    assert r["D90"] == pytest.approx(st["D90"], rel=0.05)
    assert abs(r["V100"] - st["V100"]) <= 0.05
    assert abs(r["V150"] - st["V150"]) <= 0.02
    assert r["volume_cc"] == pytest.approx(st["volume_cc"], rel=0.02)


def test_s5_entries_are_the_shell_numbers(sphere_cavity, sphere_report):
    """``metrics_grid["S5"][offset]`` carries exactly the old shell rows
    (``dvh.shell_report`` of the wall / +5 / +10 mm vertices + curve)."""
    mesh, tiles, cav, aff, clip = sphere_cavity
    rep = sphere_report
    s5 = rep.metrics_grid["S5"]
    assert set(s5) == {0.0, 5.0, 10.0}
    ref = shell_report(rep.dose_volume, mesh, SPHERE_RX, offsets_mm=(0.0, 5.0, 10.0))
    for off, entry in ref.items():
        for key in DOSE_STAT_KEYS:
            a, b = s5[off][key], entry["stats"][key]
            assert (np.isnan(a) and np.isnan(b)) or a == b, (off, key)
        assert np.array_equal(s5[off]["curve_y"], entry["curve_y"])
    # the shell is a surrogate: its numbers are not the HR-CTV's
    assert s5[5.0]["V100"] != rep.metrics_grid["RIND5"]["V100"]
    assert np.isnan(s5[5.0]["volume_cc"]) and rep.metrics_grid["RIND5"]["volume_cc"] > 0


def test_summary_labels_hrctv_and_s5_coverage(sphere_report):
    rep = sphere_report
    s = rep.summary()
    rows = s.split("\nnote:")[0]
    assert ("HR-CTV RIND5 (volumetric, 1x1x1 mm grid; cavity gtcore:cavity, "
            "clip gtcore:brain, depth 5.0 mm)") in rows
    for name in ("wall", "+5 mm", "+10 mm"):
        assert "S5 coverage %s (surrogate shell vertices)" % name in rows
    assert "target shell+5mm (surrogate, area-weighted)" in rows
    assert "shell 5.0" not in s
    r = rep.metrics_grid["RIND5"]
    assert "D90 %.0f cGy  V100 %.3f  V150 %.3f" % (r["D90"], r["V100"], r["V150"]) in rows
    assert "%.1f cc (%d voxels)" % (r["volume_cc"], r["n"]) in rows
    assert rows.index("HR-CTV RIND5") < rows.index("S5 coverage")   # HR-CTV row first


def test_summary_prints_legacy_float_keys_as_s5_coverage():
    rep = OptimizeReport(metrics_grid={5.0: {"V100": 0.7, "D90": 5800.0, "V150": 0.2,
                                             "V200": 0.05}})
    assert "S5 coverage +5 mm (surrogate shell vertices): V100 0.700" in rep.summary()
    assert "HR-CTV" not in rep.summary()


def test_rind5_json_csv_and_provenance_round_trip(sphere_report, tmp_path):
    rep = sphere_report
    jp = tmp_path / "r.json"
    cp = tmp_path / "r.csv"
    rep.to_json(jp)
    rep.to_csv(cp)
    data = json.loads(jp.read_text(encoding="utf-8"))
    assert "dose_volume" not in data
    r = data["metrics_grid"]["RIND5"]
    assert r["provenance"]["cavity_source"] == "gtcore:cavity"
    assert r["provenance"]["clip_source"] == "gtcore:brain"
    assert r["provenance"]["grid"]["spacing_mm"] == [1.0] * 3
    assert r["D90"] == rep.metrics_grid["RIND5"]["D90"]
    assert data["metrics_grid"]["S5"]["5.0"]["V100"] == rep.metrics_grid["S5"][5.0]["V100"]
    assert "5.0" not in data["metrics_grid"] and "rind" not in data["metrics_grid"]
    with open(cp, newline="", encoding="utf-8") as fh:
        rows = {row["key"]: row["value"] for row in csv.DictReader(fh)}
    for key in ("metrics_grid.RIND5.D90", "metrics_grid.RIND5.V100",
                "metrics_grid.RIND5.volume_cc", "metrics_grid.RIND5.provenance.cavity_source",
                "metrics_grid.S5.5.0.V100", "metrics_grid.S5.0.0.D90",
                "metrics_grid.target.V100"):
        assert key in rows, key
    assert rows["metrics_grid.RIND5.provenance.cavity_source"] == "gtcore:cavity"
    assert not any(k.startswith("metrics_grid.5.0") or k.startswith("metrics_grid.rind")
                   for k in rows)


def test_rind5_with_interference_mirrors_the_entry(sphere_cavity):
    rep = _sphere_report(sphere_cavity, 6.0, interference=True)
    mi = rep.metrics_grid_interference
    assert mi is not None and set(mi) == {"RIND5", "S5", "target"}
    assert mi["RIND5"]["n"] == rep.metrics_grid["RIND5"]["n"]
    assert mi["RIND5"]["Dmean"] <= rep.metrics_grid["RIND5"]["Dmean"] + 1e-9
    assert mi["RIND5"]["provenance"] == rep.metrics_grid["RIND5"]["provenance"]
    note = [n for n in rep.notes if n.startswith("interference")]
    assert note and "S5 coverage +5 mm" in note[0] and "RIND5 V100" in note[0]


def test_rind5_absent_when_inputs_incomplete_or_empty(sphere_cavity):
    """Never silently absent: each way of not getting a RIND5 leaves a note."""
    mesh, tiles, cav, aff, clip = sphere_cavity
    kw = dict(rx_cgy=SPHERE_RX, grid_mm=2.0, margin_mm=6.0, parameters={"shadowing": False})
    rep = final_report(mesh, tiles, cavity_mask=cav, **kw)             # no affine
    assert "RIND5" not in rep.metrics_grid
    assert any("both cavity_mask and cavity_affine" in n for n in rep.notes)
    far = np.eye(4)
    far[:3, 3] = [200.0, 200.0, 200.0]                                   # off the grid
    rep = final_report(mesh, tiles, cavity_mask=cav, cavity_affine=far, **kw)
    assert "RIND5" not in rep.metrics_grid
    assert any("RIND5" in n and "empty on the dose grid" in n for n in rep.notes)
    rep = final_report(mesh, tiles, cavity_mask=cav, cavity_affine=aff,  # clipped away
                       clip_mask=np.zeros_like(cav), clip_affine=aff, **kw)
    assert "RIND5" not in rep.metrics_grid
    assert any("RIND5" in n and "empty on the dose grid" in n for n in rep.notes)


def test_rind5_failure_is_a_note_or_raises_under_strict(sphere_cavity, monkeypatch):
    mesh, tiles, cav, aff, clip = sphere_cavity
    bad = cav[0]                      # 2-D: rind5 rejects it
    kw = dict(rx_cgy=SPHERE_RX, grid_mm=2.0, margin_mm=6.0, parameters={"shadowing": False})
    monkeypatch.setenv("GTCORE_STRICT", "1")
    with pytest.raises(ValueError, match="3-D"):
        final_report(mesh, tiles, cavity_mask=bad, cavity_affine=aff, **kw)
    monkeypatch.setenv("GTCORE_STRICT", "0")
    rep = final_report(mesh, tiles, cavity_mask=bad, cavity_affine=aff, **kw)
    assert "RIND5" not in rep.metrics_grid
    assert any(n.startswith("RIND5 (volumetric HR-CTV) failed: ValueError") for n in rep.notes)


def test_rind5_doses_on_grid_requires_the_dose_grid(sphere_cavity, sphere_report):
    mesh, tiles, cav, aff, clip = sphere_cavity
    t = rind5(cav, aff)               # on the cavity grid, not the dose grid
    with pytest.raises(ValueError, match="not on the dose grid"):
        rind5_doses_on_grid(t, sphere_report.dose_volume)
