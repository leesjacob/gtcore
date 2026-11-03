"""Smoke tests for the helpers in ``scripts/validation_optimize.py`` on the
stub ``toy_instance`` (no A1-A4 functions needed)."""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pytest

import plan_fixtures as pf
from gtcore.interact import find_overlapping_tiles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def vo():
    path = os.path.join(ROOT, "scripts", "validation_optimize.py")
    spec = importlib.util.spec_from_file_location("validation_optimize", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def toy():
    return pf.toy_instance(n_candidates=25, n_targets=100, rng_seed=0)


# ------------------------------------------------------------ heuristics
def test_uniform_heuristic_is_feasible_and_spread(vo, toy):
    cand, conf = toy["candidates"], toy["conflicts"]
    sel = vo.uniform_heuristic(cand, conf, 4)
    assert sel.shape == (4,) and np.all(np.diff(sel) > 0)
    assert conf.is_feasible(sel)
    assert find_overlapping_tiles(cand.tiles_of(sel), threshold_mm=1.0) == []
    # FPS on a 5x5 grid of 10 mm pitch: the four picks are >= 20 mm apart
    a = cand.anchors[sel]
    d = np.linalg.norm(a[:, None] - a[None], axis=2)
    assert d[~np.eye(4, dtype=bool)].min() >= 20.0 - 1e-6
    # deterministic
    assert np.array_equal(sel, vo.uniform_heuristic(cand, conf, 4))


def test_uniform_heuristic_skips_when_infeasible(vo, toy):
    with pytest.raises(vo.Skip, match="uniform"):
        vo.uniform_heuristic(toy["candidates"], toy["conflicts"], 30)


def test_random_feasible_draws(vo, toy):
    cand, conf = toy["candidates"], toy["conflicts"]
    rng = np.random.default_rng(3)
    sels = [vo.random_feasible(cand, conf, 4, rng) for _ in range(5)]
    for s in sels:
        assert s.shape == (4,) and conf.is_feasible(s)
    assert len({tuple(s) for s in sels}) > 1
    with pytest.raises(vo.Skip):
        vo.random_feasible(cand, conf, 26, rng)


def test_farthest_point_order(vo):
    pts = np.array([[0, 0, 0], [1, 0, 0], [10, 0, 0], [5, 0, 0]], dtype=float)
    order = vo.farthest_point_order(pts)
    assert sorted(order.tolist()) == [0, 1, 2, 3]
    assert set(order[:2].tolist()) == {0, 2}          # the two extremes first
    assert vo.farthest_point_order(np.zeros((0, 3))).size == 0


# ------------------------------------------------------------ statistics
def test_paired_ci_and_wilcoxon(vo):
    a = np.array([0.80, 0.82, 0.85, 0.90, 0.88, 0.91])
    b = a - 0.03
    ci = vo.paired_ci(a, b)
    assert ci["n"] == 6 and ci["mean"] == pytest.approx(0.03)
    assert ci["sd"] == pytest.approx(0.0, abs=1e-12)
    assert ci["ci_lo"] == pytest.approx(0.03) and ci["ci_hi"] == pytest.approx(0.03)
    b2 = b + np.array([0.01, -0.01, 0.02, -0.02, 0.0, 0.01])
    ci2 = vo.paired_ci(a, b2)
    assert ci2["ci_lo"] < ci2["mean"] < ci2["ci_hi"]
    # scipy's t for n=6: half-width = 2.571 * sd / sqrt(6)
    assert ci2["ci_hi"] - ci2["mean"] == pytest.approx(2.5706 * ci2["sd"] / np.sqrt(6), rel=1e-3)
    assert vo.wilcoxon_p(a, a) == 1.0                         # all-zero differences
    assert vo.wilcoxon_p(a, b) < 0.05                          # consistent shift
    assert vo.paired_ci([np.nan], [1.0])["n"] == 0


def test_mean_sd_and_kendall(vo):
    m, sd, n = vo.mean_sd([1.0, 2.0, 3.0, None, float("nan")])
    assert (m, n) == (2.0, 3) and sd == pytest.approx(1.0)
    assert vo.kendall_tau([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert vo.kendall_tau([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert np.isnan(vo.kendall_tau([1.0], [2.0]))


# ---------------------------------------------------------- perturbations
def test_shift_seed_plane_moves_seeds_by_stated_amount(vo, toy):
    tiles = toy["candidates"].tiles_of([0, 7])
    for off in (2.25, 3.75):
        moved = vo.shift_seed_plane(tiles, off)
        for t0, t1 in zip(tiles, moved):
            delta = t1.seed_centers - t0.seed_centers
            assert np.allclose(np.linalg.norm(delta, axis=1), abs(off - 3.0))
            # along the inward normal, sign = sign(off - 3)
            assert np.allclose(delta @ t0.normal_ras, off - 3.0)
            assert np.allclose(t1.corners_ras, t0.corners_ras)     # footprint untouched
            assert np.allclose(t1.center_ras - t0.center_ras, (off - 3.0) * t0.normal_ras)
    same = vo.shift_seed_plane(tiles, 3.0)
    assert np.allclose(same[0].seed_centers, tiles[0].seed_centers)


# --------------------------------------------------------------- misc
def test_truth_tile_builders(vo):
    import gtcore.phantom.generate as gen
    from gtcore.segment.surface import mask_to_mesh
    vol, truth = gen.make_head_phantom(spacing=1.5, n_tiles=3, rng_seed=1)
    mesh = mask_to_mesh(truth.masks["cavity"], vol.affine)
    raw = vo.truth_tiles_raw(truth)
    conf = vo.truth_tiles_conformed(mesh, truth)
    assert len(raw) == len(conf) == 3
    for r, c, t in zip(raw, conf, truth.tiles):
        assert r.seed_centers.shape == (4, 3)
        assert np.allclose(r.seed_centers.mean(axis=0), t.center_ras)
        # the conformed tile lands near the truth tile (within a few mm)
        assert np.linalg.norm(c.center_ras - t.center_ras) < 4.0


def test_target_from_faces_and_tile_rule(vo):
    mesh = pf.flat_wall_mesh(size_mm=40.0, step_mm=2.0)
    top = pf.flat_wall_top_faces(mesh)
    t = vo.target_from_faces(mesh, top, 5.0)
    assert len(t) > 0 and np.all(t.points[:, 2] > 0)
    # the wall itself (offset 0) weighs exactly the top-face area; the +5 mm
    # shell is larger because edge vertices carry averaged (tilted) normals
    t0 = vo.target_from_faces(mesh, top, 0.0)
    assert t0.total_weight == pytest.approx(40.0 * 40.0, rel=1e-6)
    assert 40.0 ** 2 < t.total_weight < 50.0 ** 2
    assert vo.tile_count_rule(4178.0) == 11 and vo.tile_count_rule(400.0) == 1
    with pytest.raises(vo.Skip):
        vo.target_from_faces(mesh, np.zeros(len(mesh.faces), dtype=bool))


def test_visible_faces_local_isolates_inner_wall(vo):
    import trimesh
    outer = trimesh.creation.icosphere(subdivisions=3, radius=30.0)
    inner = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    inner.invert()
    shell = trimesh.util.concatenate([outer, inner])
    vis = vo.visible_faces_local(shell, np.zeros(3))
    n_outer = len(outer.faces)
    assert vis[n_outer:].all() and not vis[:n_outer].any()


def test_md_table_and_csv(vo, tmp_path):
    rows = [{"a": 1, "b": 0.5, "c": float("nan")}, {"a": 2, "b": None, "c": "x"}]
    md = vo.md_table(rows, ["a", "b", "c"], {"b": "%.2f"})
    assert "| 1 | 0.50 |" in md and "—" in md
    p = tmp_path / "t.csv"
    vo.write_csv(str(p), rows)
    assert p.read_text(encoding="utf-8").splitlines()[0] == "a,b,c"


def test_parse_args_quick(vo):
    cfg = vo.parse_args(["--quick", "--section", "v8"])
    assert cfg.sections == ["v8"] and cfg.seeds == (1, 2) and cfg.n_list == (4, 8)
    assert cfg.n_random == 10 and cfg.scales == (1.0,)
    full = vo.parse_args([])
    assert full.sections == ["v2", "v3", "v4", "v5", "v6", "v7", "v8"]
    assert full.seeds == vo.SEEDS and full.n_random == vo.N_RANDOM


def test_proxy_overlaps_conservative(vo, toy):
    cand = toy["candidates"]
    # 10 mm pitch grid: neighbours (0, 1) are 10 mm apart -> certain overlap;
    # (0, 2) are 20 mm apart -> not flagged by the proxy (abutting is legal)
    assert vo.proxy_overlaps(cand.tiles_of([0, 1])) == [(0, 1)]
    assert vo.proxy_overlaps(cand.tiles_of([0, 2])) == []
    # opposite-facing tiles never count
    a, b = cand.tiles_of([0, 1])
    flipped = vo.PlacedTile(kind=b.kind, center_ras=b.center_ras, normal_ras=-b.normal_ras,
                            axis_ras=b.axis_ras, seed_centers=b.seed_centers,
                            seed_axes=b.seed_axes, corners_ras=b.corners_ras,
                            anchor_ras=b.anchor_ras)
    assert vo.proxy_overlaps([a, flipped]) == []


def test_uniform_heuristic_spin_fallback_bookkeeping(vo, toy):
    cand, conf = toy["candidates"], toy["conflicts"]
    sel = vo.uniform_heuristic(cand, conf, 4)
    assert vo.uniform_heuristic.last_fallback == 0      # spin-0 only on the toy grid
    assert conf.is_feasible(sel)


def test_augment_conflicts_adds_only_proxy_pairs(vo, toy):
    cand, conf = toy["candidates"], toy["conflicts"]
    graph, n_added = vo.augment_conflicts(cand, conf)
    old = conf.pairs.toarray()
    new = graph.pairs.toarray()
    assert new.shape == old.shape and np.array_equal(new.T, new)
    assert not new.diagonal().any()
    assert np.all(new[old])                               # planner pairs kept
    added = new & ~old
    assert n_added == int(added.sum() // 2)
    # every added pair satisfies the proxy; on the toy grid (10 mm pitch,
    # abutting tiles already conflict) a diagonal neighbour at 14.1 mm is new
    for i, j in zip(*np.nonzero(np.triu(added))):
        assert vo.proxy_overlaps(cand.tiles_of([int(i), int(j)])) == [(0, 1)]
    assert len(graph.cliques) == len(conf.cliques)


# ======================================================================
# Paper 2 Phase 4 (task opt-constants-bound): end-to-end campaign smoke run
# ======================================================================
# Two synthetic cavities (seeds 1, 2 at radius scale 1.0), N in {4, 6}, arms
# uniform / greedy / SA x 2 seeds / V100 bound on the SAME candidate set /
# truth, the rev-1 reduced grid (h = 6 mm, 2 spins, M = 300) and a short SA
# budget (2 restarts x 4 sweeps).  Measured 55 s wall on the Ryzen 5 7600X
# with other agents running (2026-11-03); the test asserts < 180 s.
SMOKE_ARGS = ["--section", "v2", "--seeds", "1", "2", "--scales", "1.0", "--n-list", "4", "6",
              "--arms", "uniform,greedy,sa,milp,truth", "--sa-seeds", "2", "--same-candidates",
              "--sa-sweeps", "4", "--sa-restarts", "2", "--h-mm", "6", "--n-spins", "2",
              "--m-opt", "300", "--bound-time-limit", "60", "--no-cache"]


@pytest.fixture(scope="module")
def campaign(vo, tmp_path_factory):
    import time
    out = tmp_path_factory.mktemp("campaign")
    t0 = time.perf_counter()
    results = vo.main(SMOKE_ARGS + ["--out", str(out)])
    wall = time.perf_counter() - t0
    return {"out": out, "results": results, "wall": wall}


def _rows(vo, path):
    rows = vo.read_csv(str(path))
    assert rows, "empty CSV %s" % path
    return rows


def test_campaign_smoke_runs_under_three_minutes(campaign):
    assert campaign["wall"] < 180.0, "smoke campaign took %.0f s" % campaign["wall"]
    assert "v2" in campaign["results"] and "rows" in campaign["results"]["v2"]


def test_campaign_rows_carry_full_grid_rind5_and_surrogates(vo, campaign):
    rows = _rows(vo, campaign["out"] / "v2_rows.csv")
    cols = set(rows[0])
    for c in ("RIND5_V100", "RIND5_D90", "RIND5_V150", "RIND5_V200", "RIND5_volume_cc", "RIND5_n",
              "S5_V100", "S5_D90", "S5_V150", "S5_V200", "S0_V100", "S10_V100",
              "TGT_V100", "TGT_D90", "INFL_V100", "INFL_D90", "INFL_objective",
              "sa_seed", "sa_rep", "sa_n_sweeps", "sa_n_restarts", "same_candidates",
              "lambda_tail_dropped", "bound_method", "candidate_ids_digest", "n_overlaps"):
        assert c in cols, c
    # the old unprefixed surrogate columns are gone
    assert not ({"V100", "D90", "V100w"} & cols)
    ok = [r for r in rows if r["status"] == "ok"]
    assert len(ok) >= 2 * 2 * 4 + 2        # 2 cavities x 2 N x (greedy, 2 SA, bound) + truth, minus skips
    for r in ok:
        v = float(r["RIND5_V100"])
        assert 0.0 <= v <= 1.0 and np.isfinite(float(r["RIND5_D90"]))
        assert int(float(r["RIND5_n"])) > 0 and float(r["RIND5_volume_cc"]) > 0
        assert 0.0 <= float(r["S5_V100"]) <= 1.0 and 0.0 <= float(r["TGT_V100"]) <= 1.0
    # header comment lines name the script, the command and the commit; the
    # output directory (a machine path) is masked
    text = (campaign["out"] / "v2_rows.csv").read_text(encoding="utf-8")
    assert text.startswith("# scripts/validation_optimize.py section v2")
    assert "# command: python scripts/validation_optimize.py" in text
    assert "--out <path>" in text and str(campaign["out"]) not in text
    assert "# commit:" in text and "LAMBDA_TAIL = 1" in text
    runs = (campaign["out"] / "runs.csv").read_text(encoding="utf-8")
    assert str(campaign["out"]) not in runs


def test_sanitise_argv(vo):
    argv = ["--section", "v6", "--phantom", r"C:\data\PH8 (223)", "--out", "out/x", "--sa-seeds", "3",
            "--clinical", "/mnt/jd", "D:/loose/path", "--h-mm", "4"]
    assert vo.sanitise_argv(argv) == ["--section", "v6", "--phantom", "<path>", "--out", "<path>",
                                      "--sa-seeds", "3", "--clinical", "<path>", "<path>", "--h-mm", "4"]


def test_campaign_sa_seeds_distinct_and_collapsed(vo, campaign):
    rows = _rows(vo, campaign["out"] / "v2_rows.csv")
    sa = [r for r in rows if r["arm"] == "sa" and r["status"] == "ok"]
    per = {}
    for r in sa:
        per.setdefault((r["cavity"], r["N"]), set()).add(int(r["sa_seed"]))
        assert (int(r["sa_n_sweeps"]), int(r["sa_n_restarts"])) == (4, 2)
    assert per, "no SA rows"
    for key, seeds in per.items():
        assert len(seeds) == 2, (key, seeds)
        cav_seed = int(key[0].split("_")[0][1:])
        assert seeds == set(vo.sa_seeds_for(cav_seed, 2))
    coll = _rows(vo, campaign["out"] / "v2_rows_collapsed.csv")
    sa_c = [r for r in coll if r["arm"] == "sa"]
    assert sa_c and all(int(r["n_seeds"]) == 2 for r in sa_c)
    for r in sa_c:
        lo, hi, m = float(r["RIND5_V100_worst"]), float(r["RIND5_V100_best"]), float(r["RIND5_V100"])
        assert lo - 1e-12 <= m <= hi + 1e-12
        assert float(r["RIND5_V100_seed_sd"]) >= 0.0
    assert any(r["arm"] == "sa_best" for r in coll)
    seeds_csv = _rows(vo, campaign["out"] / "v2_sa_seeds.csv")
    assert {"RIND5_V100_mean", "RIND5_V100_seed_sd", "RIND5_V100_best", "sa_seed_best"} <= set(seeds_csv[0])
    summary = _rows(vo, campaign["out"] / "v2_summary.csv")
    sa_sum = [r for r in summary if r["arm"] == "sa"]
    assert sa_sum and all(r["RIND5_V100_seed_sd_mean"] != "" and int(r["n_seeds"]) == 2 for r in sa_sum)
    assert {"RIND5_V100_mean", "RIND5_V100_sd", "RIND5_D90_mean", "S5_V100_mean"} <= set(summary[0])


def test_campaign_v100_bound_on_identical_candidates(vo, campaign):
    rows = _rows(vo, campaign["out"] / "v2_rows.csv")
    bound = [r for r in rows if r["arm"] == "v100_bound" and r["status"] == "ok"]
    sa = [r for r in rows if r["arm"] == "sa" and r["status"] == "ok"]
    assert bound and not any(r["arm"] == "milp" for r in rows)        # alias resolved, label = V100 bound
    inst = {r["cavity"]: r for r in rows if r["arm"] == "greedy" and r["status"] == "ok"}
    for r in bound:
        assert r["same_candidates"] == "True"
        assert float(r["lambda_tail_dropped"]) == 1.0                # the tail term was dropped
        assert r["bound_method"] in ("enum_bb", "highs")
        assert r["solver_status"] in ("optimal", "time_limit")
        assert len(r["candidate_ids_digest"]) == 12
        # the bound ran on SA's instance: same h / spins / M and candidate count
        assert float(r["bound_h_mm"]) == 6.0 and int(r["bound_n_spins"]) == 2 and int(r["bound_m_opt"]) == 300
    # an optimal bound's influence V100 is >= every SA seed's influence V100 on
    # the same instance (same candidates, same subsample); a time_limit incumbent
    # need not be, but its ``bound`` still is
    for r in bound:
        peers = [s for s in sa if s["cavity"] == r["cavity"] and s["N"] == r["N"]]
        assert peers
        ref = float(r["INFL_V100"]) if r["solver_status"] == "optimal" else float(r["bound"])
        for s_ in peers:
            assert ref >= float(s_["INFL_V100"]) - 1e-9
        assert float(r["bound"]) >= float(r["INFL_V100"]) - 1e-9


def test_campaign_paired_and_primary_on_rind5(vo, campaign):
    paired = _rows(vo, campaign["out"] / "v2_paired.csv")
    comps = {(r["comparison"], r["endpoint"]) for r in paired}
    for c in ("sa - uniform", "sa - greedy", "v100_bound - sa", "sa_best - greedy"):
        assert (c, "RIND5_V100") in comps, c
    assert ("sa - greedy", "S5_V100") in comps                       # surrogate check alongside
    for r in paired:
        if r["comparison"] == "sa - greedy" and r["endpoint"] == "RIND5_V100":
            assert int(r["n"]) == 2 and float(r["ci_lo_pp"]) <= float(r["mean_pp"]) <= float(r["ci_hi_pp"])
    primary = _rows(vo, campaign["out"] / "v2_primary.csv")
    summ = [r for r in primary if r.get("comparison")]
    assert any(r["comparison"] == "sa - uniform at N*" and r["endpoint"] == "RIND5_V100" for r in summ)
    md = campaign["results"]["v2"]["md"]
    assert "RIND5 on the full 1 mm grid" in md and "V100 bound" in md
    assert (campaign["out"] / "v2_coverage_vs_n.png").exists()


# ------------------------------------------------- unit tests of the helpers
def test_sa_seeds_for_and_aliases(vo):
    assert vo.sa_seeds_for(3, 1) == [1003]
    assert vo.sa_seeds_for(3, 3) == [1003, 11003, 21003]
    assert vo.sa_seeds_for(3, 0) == [1003]
    cfg = vo.parse_args(["--arms", "sa,milp", "--sa-seeds", "3", "--same-candidates", "--sa-sweeps", "7",
                         "--sa-restarts", "2", "--h-mm", "5", "--n-spins", "3", "--m-opt", "500",
                         "--bound-time-limit", "12", "--bound-n", "4", "6", "8", "10"])
    assert cfg.arms == ("sa", "v100_bound") and cfg.sa_seeds == 3 and cfg.same_candidates
    assert cfg.sa_kw == {"n_sweeps": 7, "n_restarts": 2}
    assert (cfg.h_mm, cfg.n_spins, cfg.m_opt) == (5.0, 3, 500)
    assert cfg.bound_time_limit_s == 12.0 and cfg.bound_n == (4, 6, 8, 10)
    assert cfg.sections == ["v2", "v3", "v4", "v5", "v6", "v7", "v8"]
    cap = vo.parse_args(["--capacity"])
    assert cap.sections == ["capacity"] and cap.sa_kw == {} and cap.sa_seeds == 1 and not cap.same_candidates
    both = vo.parse_args(["--section", "v2", "--capacity"])
    assert both.sections == ["v2", "capacity"]
    assert vo.parse_args([]).bound_n == vo.V3_N


def test_assert_same_candidates(vo, toy):
    cand = toy["candidates"]
    info = vo.assert_same_candidates(cand, cand)
    assert info["same_candidates"] is True and info["n_candidates"] == len(cand)
    assert info["candidate_ids_digest"] == vo.candidate_ids_digest(cand)
    with pytest.raises(RuntimeError, match="NOT identical"):
        vo.assert_same_candidates(cand, cand.subset(np.arange(len(cand) - 1)))
    other = pf.toy_instance(n_candidates=25, n_targets=100, rng_seed=0, pitch_mm=12.0)["candidates"]
    with pytest.raises(RuntimeError, match="NOT identical"):
        vo.assert_same_candidates(cand, other)
    assert vo.candidate_ids_digest(cand) != vo.candidate_ids_digest(other)


def test_collapse_seeds(vo):
    base = {"cavity": "s1", "N": 4, "status": "ok", "seed": 1}
    rows = [dict(base, arm="greedy", RIND5_V100=0.5, RIND5_D90=4000.0, solve_s=1.0),
            dict(base, arm="sa", sa_seed=1001, sa_rep=0, RIND5_V100=0.6, RIND5_D90=4500.0, solve_s=2.0),
            dict(base, arm="sa", sa_seed=11001, sa_rep=1, RIND5_V100=0.8, RIND5_D90=4700.0, solve_s=4.0)]
    out = vo.collapse_seeds(rows)
    by = {r["arm"]: r for r in out}
    assert set(by) == {"greedy", "sa", "sa_best"}
    assert by["greedy"]["n_seeds"] == 1 and by["greedy"]["RIND5_V100_seed_sd"] == 0.0
    sa = by["sa"]
    assert sa["n_seeds"] == 2 and sa["RIND5_V100"] == pytest.approx(0.7) and sa["solve_s"] == pytest.approx(3.0)
    assert sa["RIND5_V100_best"] == 0.8 and sa["RIND5_V100_worst"] == 0.6
    assert sa["RIND5_V100_seed_sd"] == pytest.approx(np.std([0.6, 0.8], ddof=1))
    assert sa["RIND5_D90"] == pytest.approx(4600.0) and sa["sa_seed_best"] == 11001
    assert sa["N"] == 4 and sa["seed"] == 1                           # identity columns untouched
    assert by["sa_best"]["RIND5_V100"] == 0.8 and by["sa_best"]["sa_seed"] == 11001
    assert by["sa_best"]["n_seeds"] == 2


def test_write_and_read_csv_with_header(vo, tmp_path):
    p = tmp_path / "h.csv"
    vo.write_csv(str(p), [{"a": 1, "b": 0.25}], header=["first line", "second\nline"])
    text = p.read_text(encoding="utf-8").splitlines()
    assert text[0] == "# first line" and text[1] == "# second line" and text[2] == "a,b"
    assert vo.read_csv(str(p)) == [{"a": "1", "b": "0.25"}]
    vo.write_csv(str(tmp_path / "e.csv"), [], header=["only a header"])
    assert (tmp_path / "e.csv").read_text(encoding="utf-8") == "# only a header\n"


def test_solver_columns_and_rind5_kwargs(vo):
    from gtcore.plan import SolverResult
    res = SolverResult(selection=np.array([0, 1]), objective=0.7, metrics={"V100": 0.6, "D90": 5000.0},
                       runtime_s=0.1, solver="milp", bound=1200.0, mip_gap=0.0,
                       extra={"lambda_tail_dropped": 1.0, "method": "enum_bb"})
    cols = vo.solver_columns(res, total_weight=2000.0)
    assert cols["INFL_V100"] == 0.6 and cols["INFL_D90"] == 5000.0 and cols["INFL_objective"] == 0.7
    assert cols["bound"] == pytest.approx(0.6) and cols["lambda_tail_dropped"] == 1.0
    assert cols["bound_method"] == "enum_bb" and cols["solver_status"] == "ok"
    assert vo.solver_columns(None) == {}
    cav = {"cavity_mask": np.ones((2, 2, 2), bool), "affine": np.eye(4), "brain_mask": np.ones((2, 2, 2), bool)}
    kw = vo.rind5_kwargs(cav)
    assert kw["cavity_source"] == "synthetic:cavity" and kw["clip_source"] == "synthetic:brain"
    assert kw["clip_mask"] is cav["brain_mask"] and kw["cavity_affine"] is cav["affine"]
    assert "clip_mask" not in vo.rind5_kwargs({"cavity_mask": cav["cavity_mask"], "affine": np.eye(4)})
