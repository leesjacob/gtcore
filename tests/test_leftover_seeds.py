"""Leftover-seed diagnostic (scripts/leftover_seeds.py) on the synthetic phantom.

Known truth: every detected seed of the clean phantom belongs to a tile, so
all three fit modes assign all seeds and report zero leftovers.  With four
scattered decoys appended, every mode leaves exactly those four over and the
diagnostic reports the gate each tier fails on them.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import numpy as np
import pytest

from gtcore.phantom import make_head_phantom
from gtcore.seeds import detect_seed_candidates
from gtcore.segment import mask_to_mesh
from gtcore.tiles.auto import LAMBDA_FULL
from gtcore.tiles.fit import SIDE_MAX_MM, SIDE_MIN_MM

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "leftover_seeds", os.path.join(_ROOT, "scripts", "leftover_seeds.py"))
ls = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ls          # dataclasses resolve annotations via sys.modules
_spec.loader.exec_module(ls)

N_TILES = 3


@pytest.fixture(scope="module")
def phantom():
    vol, truth = make_head_phantom(spacing=0.8, n_tiles=N_TILES, rng_seed=2)
    cands = detect_seed_candidates(vol)
    assert len(cands) == 4 * N_TILES
    return dict(vol=vol, truth=truth, seeds=cands,
                centers=np.array(cands.centers_ras, copy=True),
                axes=np.array(cands.axes_ras, copy=True))


@pytest.fixture(scope="module")
def clean_modes(phantom):
    return ls.run_modes(phantom["centers"], phantom["axes"], N_TILES,
                        cavity_center_ras=phantom["truth"].cavity_center_ras,
                        spacing_mm=phantom["vol"].spacing)


def _decoys(centers, truth):
    """Four far-apart decoys (> 18 mm from everything, random axes)."""
    rng = np.random.default_rng(0)
    cav = truth.cavity_center_ras
    extra = cav[None, :] + np.array([[70.0, 0.0, 0.0], [0.0, 75.0, 20.0],
                                     [-60.0, -40.0, 30.0], [20.0, -70.0, -45.0]])
    axes = rng.normal(size=(4, 3))
    axes /= np.linalg.norm(axes, axis=1, keepdims=True)
    D = np.linalg.norm(extra[:, None, :] - centers[None, :, :], axis=2)
    assert D.min() > 18.0
    return extra, axes


@pytest.fixture(scope="module")
def decoy_case(phantom):
    extra, eaxes = _decoys(phantom["centers"], phantom["truth"])
    centers = np.vstack([phantom["centers"], extra])
    axes = np.vstack([phantom["axes"], eaxes])
    modes = ls.run_modes(centers, axes, N_TILES,
                         cavity_center_ras=phantom["truth"].cavity_center_ras,
                         spacing_mm=phantom["vol"].spacing)
    return dict(centers=centers, axes=axes, modes=modes,
                decoys=list(range(len(phantom["centers"]), len(centers))))


# ------------------------------------------------------------- clean phantom
def test_clean_phantom_all_modes_assign_every_seed(phantom, clean_modes):
    n = len(phantom["centers"])
    assert set(clean_modes) == {"auto", "count%d" % N_TILES, "prior%d" % N_TILES}
    for m in clean_modes.values():
        assert m.n_tiles == N_TILES, m.summary()
        assert m.leftovers == []
        assert m.assigned == list(range(n))
        assert m.n_degraded == 0
        rows = ls.tile_rows(m, phantom["centers"], phantom["axes"])
        assert len(rows) == N_TILES
        for r in rows:
            assert r["n_seeds"] == 4
            assert np.isfinite(r["bent_rms_mm"]) and r["bent_rms_mm"] < 1.0
            assert np.isfinite(r["chord_residual_mm"])


def test_clean_phantom_partition_matches_truth(phantom, clean_modes):
    truth = phantom["truth"]
    tc = np.array([s.center_ras for s in truth.seeds])
    D = np.linalg.norm(phantom["centers"][:, None] - tc[None], axis=2)
    d2t = D.argmin(axis=1)
    assert (D.min(axis=1) < 2.0).all()
    want = {frozenset(t.seed_ids) for t in truth.tiles}
    for m in clean_modes.values():
        got = {frozenset(int(d2t[i]) for i in p.seed_indices) for p in m.tiles}
        assert got == want, m.name


def test_clean_phantom_diagnostic_is_empty(phantom, clean_modes):
    m = clean_modes["auto"]
    d, a = ls.pairwise_tables(phantom["centers"], phantom["axes"], m.leftovers)
    assert d.shape == (0, 0) and a.shape == (0, 0)
    assert ls.seed_rows(phantom["centers"], phantom["axes"], m.leftovers, m) == []
    q = ls.quad_gate_rows(phantom["centers"], phantom["axes"], m.leftovers)
    assert q == []
    assert ls.best_quad(q) is None
    f = ls.cluster_finding(phantom["centers"], m.leftovers, q)
    assert f["n_clusters"] == 0 and f["n_disjoint_passing"] == 0
    assert f["verdict"] == "no leftover seeds"


# ------------------------------------------------------- scattered decoys
def test_decoys_are_the_leftovers_in_every_mode(decoy_case):
    for m in decoy_case["modes"].values():
        assert m.n_tiles == N_TILES, m.summary()
        assert m.leftovers == decoy_case["decoys"], m.summary()
        assert m.n_degraded == 0
    auto = decoy_case["modes"]["auto"]
    # far from every tile: clutter, not in-implant unassigned
    assert auto.clutter == decoy_case["decoys"]
    assert auto.unassigned_in_implant == []


def test_decoy_gates_are_reported(decoy_case):
    c, a = decoy_case["centers"], decoy_case["axes"]
    m = decoy_case["modes"]["auto"]
    left = m.leftovers
    d, ang = ls.pairwise_tables(c, a, left)
    assert d.shape == (4, 4) and np.allclose(np.diag(d), 0.0)
    assert d[np.triu_indices(4, 1)].min() > 18.0
    rows = ls.seed_rows(c, a, left, m)
    assert [r["seed"] for r in rows] == left
    for r in rows:
        assert r["nn_any_mm"] > 18.0 and r["nn_leftover_mm"] > 18.0
        assert r["clutter"] and not r["in_implant"]
        assert r["nearest_tile"] in {p.tile_id for p in m.tiles}
        assert not r["inside_footprint"]
        assert r["layer_class"] == "far" and r["dist_sheet_mm"] > 18.0
    lay = ls.layer_rows(c, a, left, m)
    assert len(lay) == 4 * N_TILES
    assert all(r["layer_class"] == "far" for r in lay)
    q = ls.quad_gate_rows(c, a, left)
    assert len(q) == 1                      # C(4, 4)
    row = q[0]
    assert sorted(row["seeds"]) == left
    assert not row["standard_pass"] and not row["standard_pass_ref"]
    assert not row["loose_pass"] and not row["cover_pass"] and not row["completion_pass"]
    for gate in ("std_link", "loose_chord", "cover_chord", "completion_chord"):
        assert gate in row["gates_failed"]
    assert np.isfinite(row["def_score"])   # the bent-tile fit is always reported
    bq = ls.best_quad(q)
    assert bq["seeds"] == row["seeds"]
    assert "std_link" in bq["rejected_by"]["standard"]
    assert bq["below_penalty"] == (bq["def_score"] < LAMBDA_FULL)
    pairs = ls.pair_gate_rows(c, a, left)
    assert len(pairs) == 6 and not any(r["pair_pass"] for r in pairs)
    assert all("pair_chord_window" in r["gates_failed"] for r in pairs)
    trips = ls.triplet_rows(c, a, left)
    assert len(trips) == 4 and not any(r["triplet_pass"] for r in trips)
    f = ls.cluster_finding(c, left, q)
    assert f["n_clusters"] == 4 and f["n_disjoint_passing"] == 0
    assert f["verdict"].startswith("no leftover quad passes")


def test_layer_classes_sheet_and_stacked(phantom, clean_modes):
    """A tile's own seed is 'sheet' in its tile; a point one tile thickness
    (4 mm) above the tile's seed sheet, toward the cavity, is 'stacked'."""
    m = clean_modes["auto"]
    c, a = phantom["centers"], phantom["axes"]
    p = m.tiles[0]
    fit = m.deforms[p.tile_id]
    own = ls.layer_rows(c, a, [p.seed_indices[0]], m)
    mine = [r for r in own if r["tile"] == p.tile_id][0]
    assert mine["inside_footprint"] and abs(mine["h_mm"]) < 1.5
    assert mine["layer_class"] == "sheet" and mine["dist_sheet_mm"] < 1.5
    n_cav = ls._model_normal_sign(p, fit) * np.asarray(fit.pose.normal)
    stacked_pt = np.asarray(fit.pose.t) + 4.0 * n_cav
    c2 = np.vstack([c, stacked_pt[None, :]])
    a2 = np.vstack([a, a[p.seed_indices[0]][None, :]])
    rows = ls.layer_rows(c2, a2, [len(c)], m)
    r0 = [r for r in rows if r["tile"] == p.tile_id][0]
    assert r0["inside_footprint"]
    assert 3.0 < r0["h_mm"] < 5.0, r0
    assert r0["layer_class"] == "stacked"
    # the stacked point is also what seed_rows reports as nearest
    srow = ls.seed_rows(c2, a2, [len(c)], m)[0]
    assert srow["nearest_tile"] == p.tile_id and srow["layer_class"] == "stacked"


def test_true_tile_quad_passes_the_standard_gates(phantom, clean_modes):
    """Sanity: the same gate table on a real tile's four seeds passes."""
    m = clean_modes["auto"]
    idx = list(m.tiles[0].seed_indices)
    q = ls.quad_gate_rows(phantom["centers"], phantom["axes"], idx)
    assert len(q) == 1
    assert q[0]["standard_pass"] and q[0]["standard_pass_ref"]
    assert q[0]["gates_failed"] == []
    assert q[0]["def_score"] > LAMBDA_FULL


# ---------------------------------------------------------- render export
def test_sheet_faces_cover_the_grid():
    n = 5
    f = ls.sheet_faces(n)
    assert f.shape == (2 * (n - 1) ** 2, 3)
    assert f.min() == 0 and f.max() == n * n - 1
    # every interior vertex is used
    assert set(np.unique(f)) == set(range(n * n))


def test_export_documents_every_array(tmp_path, phantom, decoy_case):
    truth = phantom["truth"]
    mesh = mask_to_mesh(truth.masks["cavity"], phantom["vol"].affine)
    c, a = decoy_case["centers"], decoy_case["axes"]
    modes = decoy_case["modes"]
    npz_path = str(tmp_path / "f4_implant_SYN.npz")
    ls.export_implant(npz_path, "SYN", c, a, modes, primary="auto", wall_mesh=mesh,
                      wall_source="gtcore:cavity", cavity_center_ras=truth.cavity_center_ras,
                      spacing_mm=phantom["vol"].spacing, shape_kji=phantom["vol"].array.shape,
                      command="test", n_full=N_TILES, n=7)
    data = np.load(npz_path)
    with open(str(tmp_path / "f4_implant_SYN.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    assert set(index["arrays"]) == set(data.files)
    for key in data.files:
        assert list(data[key].shape) == index["arrays"][key]["shape"], key
        if key != "readme":
            assert index["arrays"][key]["doc"], "undocumented array %s" % key
    assert data["sheet_faces"].max() == 7 * 7 - 1
    assert data["wall_mesh_vertices"].shape[0] == len(mesh.vertices)
    n = len(c)
    for name, m in modes.items():
        tid = data["%s_seed_tile_id" % name]
        assert tid.shape == (n,)
        assert sorted(np.flatnonzero(tid < 0).tolist()) == m.leftovers
        assert data["%s_seed_leftover" % name].sum() == len(m.leftovers)
        sheets = data["%s_sheet_seed_vertices" % name]
        assert sheets.shape == (m.n_tiles, 49, 3) and np.isfinite(sheets).all()
        # the seed sheet passes through the fitted seeds (within the fit rms + bend)
        for t, p in enumerate(m.tiles):
            seeds_t = c[list(p.seed_indices)]
            d = np.linalg.norm(sheets[t][:, None] - seeds_t[None], axis=2).min(axis=0)
            assert d.max() < 2.0, (name, t, d)
        # tissue face is 3 mm from the seed sheet
        tissue = data["%s_sheet_tissue_vertices" % name]
        gap = np.linalg.norm(tissue - sheets, axis=2)
        assert np.allclose(gap, 3.0, atol=0.05)
    assert index["modes"]["auto"]["n_leftover"] == 4
    assert index["primary_mode"] == "auto"


# ------------------------------------------------- --halves N_FULL,N_HALF
def _truth_partition(phantom):
    """Detection index -> truth seed id, and the truth tiles as seed-id sets."""
    truth = phantom["truth"]
    tc = np.array([s.center_ras for s in truth.seeds])
    D = np.linalg.norm(phantom["centers"][:, None] - tc[None], axis=2)
    assert (D.min(axis=1) < 2.0).all()
    d2t = D.argmin(axis=1)
    return d2t, {frozenset(t.seed_ids) for t in truth.tiles}


@pytest.fixture(scope="module")
def halves_clean(phantom):
    """``--halves 2,2`` on the 3-tile phantom: two tiles as full, the third
    read as two half tiles (the prior is an input, not a fitted count)."""
    return ls.run_halves_mode(phantom["centers"], phantom["axes"], N_TILES - 1, 2,
                              cavity_center_ras=phantom["truth"].cavity_center_ras,
                              spacing_mm=phantom["vol"].spacing)


def test_parse_halves_and_mode_name():
    assert ls.parse_halves("6,4") == (6, 4)
    assert ls.parse_halves(" 2 , 2 ") == (2, 2)
    assert ls.parse_halves("8,0") == (8, 0)
    for bad in ("6", "6,4,1", "a,b", "-1,2", ""):
        with pytest.raises(ValueError):
            ls.parse_halves(bad)
    assert ls.halves_mode_name(6, 4) == "count_6full_4half"


def test_halves_mode_reads_one_tile_as_two_halves(phantom, halves_clean):
    m = halves_clean
    n = len(phantom["centers"])
    assert m.name == "count_2full_2half"
    assert "2 full + 2 half" in m.label
    assert sorted(p.kind for p in m.tiles) == ["full", "full", "half", "half"]
    assert m.leftovers == [] and m.assigned == list(range(n))
    assert m.n_degraded == 0
    d2t, want = _truth_partition(phantom)
    fulls = {frozenset(int(d2t[i]) for i in p.seed_indices) for p in m.tiles if p.kind == "full"}
    assert fulls < want                                   # two of the three truth tiles, intact
    half_union = frozenset(int(d2t[i]) for p in m.tiles if p.kind == "half" for i in p.seed_indices)
    assert half_union in want and half_union not in fulls   # the halves are the third tile, cut in two
    for p in m.tiles:
        if p.kind == "half":
            assert len(p.seed_indices) == 2
            d = np.linalg.norm(phantom["centers"][p.seed_indices[0]] - phantom["centers"][p.seed_indices[1]])
            assert SIDE_MIN_MM <= d <= SIDE_MAX_MM         # the half-tile pair window (one bent pitch)
    # every tile, half tiles included, has a bent-tile fit (rigid fallback for 2 seeds)
    rows = ls.tile_rows(m, phantom["centers"], phantom["axes"])
    assert len(rows) == 4
    assert sorted(r["n_seeds"] for r in rows) == [2, 2, 4, 4]
    assert all(np.isfinite(r["bent_rms_mm"]) and r["bent_rms_mm"] < 1.5 for r in rows)
    assert all(np.isfinite(r["chord_residual_mm"]) for r in rows)
    # the chord-path cross-check with the same counts assigns the same seeds to the same kinds
    cc = ls.halves_chord_crosscheck(phantom["centers"], phantom["axes"], N_TILES - 1, 2,
                                    cavity_center_ras=phantom["truth"].cavity_center_ras)
    assert cc.name == "chord_2full_2half" and cc.leftovers == []
    assert sorted(p.kind for p in cc.tiles) == ["full", "full", "half", "half"]
    cc_fulls = {frozenset(int(d2t[i]) for i in p.seed_indices) for p in cc.tiles if p.kind == "full"}
    assert cc_fulls == fulls
    cc_half = frozenset(int(d2t[i]) for p in cc.tiles if p.kind == "half" for i in p.seed_indices)
    assert cc_half == half_union


def test_halves_mode_leaves_decoys_over(decoy_case, phantom):
    m = ls.run_halves_mode(decoy_case["centers"], decoy_case["axes"], N_TILES - 1, 2,
                           cavity_center_ras=phantom["truth"].cavity_center_ras,
                           spacing_mm=phantom["vol"].spacing)
    assert sorted(p.kind for p in m.tiles) == ["full", "full", "half", "half"]
    assert m.leftovers == decoy_case["decoys"], m.summary()
    assert m.n_degraded == 0
    # the decoys (> 18 mm apart) can never pass the half-tile pair window
    assert not any(set(p.seed_indices) & set(decoy_case["decoys"]) for p in m.tiles)


def test_export_with_the_halves_mode_as_an_additional_mode(tmp_path, phantom, clean_modes, halves_clean):
    truth = phantom["truth"]
    mesh = mask_to_mesh(truth.masks["cavity"], phantom["vol"].affine)
    c, a = phantom["centers"], phantom["axes"]
    modes = dict(clean_modes)
    modes[halves_clean.name] = halves_clean
    npz_path = str(tmp_path / "f4_implant_SYN.npz")
    ls.export_implant(npz_path, "SYN", c, a, modes, primary="auto", wall_mesh=mesh,
                      wall_source="gtcore:cavity", cavity_center_ras=truth.cavity_center_ras,
                      spacing_mm=phantom["vol"].spacing, shape_kji=phantom["vol"].array.shape,
                      command="test --halves 2,2", n_full=N_TILES, n=7, halves=(2, 2))
    data = np.load(npz_path)
    with open(str(tmp_path / "f4_implant_SYN.json"), encoding="utf-8") as fh:
        index = json.load(fh)
    # the other modes are still there, the new one is added
    assert set(index["modes"]) == {"auto", "count%d" % N_TILES, "prior%d" % N_TILES, "count_2full_2half"}
    assert index["halves_requested"] == {"n_full": 2, "n_half": 2, "mode": "count_2full_2half"}
    assert "count_<F>full_<H>half" in index["conventions"]["modes"]
    assert set(index["arrays"]) == set(data.files)
    for key in data.files:
        assert list(data[key].shape) == index["arrays"][key]["shape"], key
        if key != "readme":
            assert index["arrays"][key]["doc"], "undocumented array %s" % key
    assert index["arrays"]["count_2full_2half_tile_kind"]["doc"].startswith("[mode count_2full_2half]")
    kinds = data["count_2full_2half_tile_kind"]
    assert sorted(kinds.tolist()) == ["full", "full", "half", "half"]
    idx = data["count_2full_2half_tile_seed_indices"]
    assert idx.shape == (4, 4)
    for t, kind in enumerate(kinds):
        assert int((idx[t] >= 0).sum()) == (4 if kind == "full" else 2)
    sheets = data["count_2full_2half_sheet_seed_vertices"]
    assert sheets.shape == (4, 49, 3) and np.isfinite(sheets).all()
    for t, p in enumerate(halves_clean.tiles):
        seeds_t = c[list(p.seed_indices)]
        d = np.linalg.norm(sheets[t][:, None] - seeds_t[None], axis=2).min(axis=0)
        assert d.max() < 2.0, (t, p.kind, d)
        # a half-tile sheet spans half the width of a full one
        ext = np.ptp(sheets[t], axis=0)
        assert (ext.max() < 25.0) and (ext.max() > 12.0)
    tissue = data["count_2full_2half_sheet_tissue_vertices"]
    assert np.allclose(np.linalg.norm(tissue - sheets, axis=2), 3.0, atol=0.05)
    assert data["count_2full_2half_seed_leftover"].sum() == 0
    assert (data["count_2full_2half_seed_tile_id"] >= 0).all()
    assert index["modes"]["count_2full_2half"]["n_leftover"] == 0
    assert index["modes"]["count_2full_2half"]["n_tiles"] == 4
    assert index["modes"]["auto"]["n_tiles"] == N_TILES         # untouched


def test_patch_png_is_written(tmp_path, phantom, clean_modes):
    m = clean_modes["auto"]
    c = phantom["centers"]
    out = str(tmp_path / "patches" / "SYN_seed00.png")
    is_left = np.zeros(len(c), dtype=bool)
    ls.write_patch(phantom["vol"], c[0], 0, out, other_centers=c, other_leftover=is_left,
                   dataset="SYN", dpi=72)
    assert os.path.isfile(out) and os.path.getsize(out) > 2000
    rows = ls.seed_rows(c, phantom["axes"], [0], m, seeds=phantom["seeds"], vol=phantom["vol"])
    assert rows[0]["peak_hu_1mm"] > 1500.0
    assert rows[0]["inside_footprint"]          # a tile seed sits in its own tile
    assert abs(rows[0]["tile_h_mm"]) < 1.5
    assert rows[0]["layer_class"] == "sheet"
    owner = [p.tile_id for p in m.tiles if 0 in p.seed_indices][0]
    assert rows[0]["nearest_tile"] == owner
