"""Paper 2 Phase 4 (task ``opt-constants-bound``): ``gtcore.plan.candidates.packing_capacity``
and the frozen optimizer constants.

``packing_capacity`` is the Table 3 number "capacity at grid (h, spins), not
exact": a greedy maximal independent set on the conflict graph (lower bound
on the exact packing number of the candidate set) next to the eligible-area /
tile-area quotient (upper bound when tiles cannot leave the eligible region).
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest
import scipy.sparse as sp

import gtcore.plan as plan
import plan_fixtures as pf
from gtcore.plan import candidates as C
from gtcore.plan.conflicts import tiles_conflict


# ----------------------------------------------------------- constants
def test_phase4_constants_frozen():
    """Requirements rev 3 section 3 F8 / section 6 Phase 4: the objective is
    frozen at lambda_tail = 1, q = 0.10 and the SA budget is 8 x 60."""
    assert plan.LAMBDA_TAIL == 1.0
    assert plan.TAIL_Q == 0.10
    assert plan.SA_N_RESTARTS == 8
    assert plan.SA_N_SWEEPS == 60
    # the solver defaults are the constants (both the wrapper and the module)
    from gtcore.plan import solvers
    for fn in (plan.solve_sa, solvers.solve_sa):
        sig = inspect.signature(fn)
        assert sig.parameters["n_sweeps"].default == 60
        assert sig.parameters["n_restarts"].default == 8
    assert plan.Objective.__dataclass_fields__["lambda_tail"].default == 1.0
    assert plan.Objective.__dataclass_fields__["tail_q"].default == 0.10
    # the constants' comments cite the evidence (lambda sweep) and the argument
    src = inspect.getsource(plan)
    assert "Objective: lower-tail term" in src
    assert "D90 >= rx <=> V100 >= 0.90" in src
    assert "18.7 %" in src


# ----------------------------------------------------- independent set
def test_greedy_independent_set_small_graphs():
    P = sp.lil_matrix((5, 5), dtype=bool)                  # path 0-1-2-3-4
    for i in range(4):
        P[i, i + 1] = True
        P[i + 1, i] = True
    assert C.greedy_independent_set(P.tocsr()).tolist() == [0, 2, 4]
    S = sp.lil_matrix((5, 5), dtype=bool)                  # star, centre 0
    for i in range(1, 5):
        S[0, i] = True
        S[i, 0] = True
    assert C.greedy_independent_set(S.tocsr()).tolist() == [1, 2, 3, 4]
    # eligibility restricts the vertices; empty graph -> empty set
    assert C.greedy_independent_set(S.tocsr(), eligible=[True, True, False, False, False]).tolist() == [1]
    assert C.greedy_independent_set(sp.csr_matrix((0, 0), dtype=bool)).size == 0
    with pytest.raises(ValueError, match="square"):
        C.greedy_independent_set(np.zeros((2, 3), dtype=bool))
    # deterministic
    a = C.greedy_independent_set(P.tocsr(), rng_seed=3, n_random_orders=4)
    b = C.greedy_independent_set(P.tocsr(), rng_seed=3, n_random_orders=4)
    assert np.array_equal(a, b)


def test_packing_capacity_toy_grid_exact_optimum():
    """7 x 7 anchors at 10 mm pitch, full tiles, the planner rule: tiles at
    anchor Chebyshev distance <= 20 mm conflict, 30 mm apart do not, so the
    exact packing number is 3 x 3 = 9; the greedy set reaches it."""
    toy = pf.toy_instance(n_candidates=49, n_targets=40, rng_seed=0)
    top = pf.flat_wall_top_faces(toy["mesh"])
    r = C.packing_capacity(toy["mesh"], 10.0, 1, eligible=top,
                           candidates=toy["candidates"], conflicts=toy["conflicts"])
    assert r["capacity"] == 9
    assert sorted(r["selection"]) == [0, 3, 6, 21, 24, 27, 42, 45, 48]
    assert toy["conflicts"].is_feasible(r["selection"])
    # toy mesh: 2 * (30 + 20) = 100 mm plate -> 10 000 mm^2 / 400 mm^2
    assert r["eligible_area_mm2"] == pytest.approx(100.0 ** 2, rel=1e-9)
    assert r["upper_bound_area"] == 25
    assert r["capacity"] <= r["upper_bound_area"]
    assert r["n_candidates"] == 49 and r["n_anchors"] == 49
    assert r["n_pairs"] == toy["conflicts"].count_pairs()
    assert (r["h_mm"], r["n_spins"], r["kind"]) == (10.0, 1, "full")
    assert r["exact"] is False
    assert r["label"] == "capacity at grid (h = 10 mm, 1 spins), not exact"
    assert "greedy maximal independent set" in r["method"]
    assert r["tile_area_mm2"] == plan.TILE_AREA_CM2 * 100.0


def test_packing_capacity_flat_plate_known_area():
    """A 100 mm plate with the inner 60 x 60 mm square eligible: eligible area
    3600 mm^2 exactly -> area bound 9 (= floor(3600 / 400)); every greedy tile
    is conflict-free under the robust rule, anchored inside the square and
    flat on the plate (no tile wraps over the box edge), and the capacity sits
    in [4, 9] (a 2 x 2 at >= 21 mm spacing always fits in the 60 mm anchor range)."""
    mesh = pf.flat_wall_mesh(size_mm=100.0, step_mm=2.0)
    cen = np.asarray(mesh.triangles_center, dtype=float)
    inner = pf.flat_wall_top_faces(mesh) & (np.abs(cen[:, 0]) < 30.0) & (np.abs(cen[:, 1]) < 30.0)
    r = C.packing_capacity(mesh, 5.0, 1, eligible=inner)
    assert r["eligible_area_mm2"] == pytest.approx(60.0 * 60.0, rel=1e-9)
    assert r["upper_bound_area"] == 9
    assert 4 <= r["capacity"] <= 9
    assert len(r["selection"]) == r["capacity"]
    cand = C.build_candidates(mesh, h_mm=5.0, n_spins=1, eligible_faces=inner)
    assert r["n_candidates"] == len(cand)
    tiles = cand.tiles_of(r["selection"])
    assert tiles_conflict(tiles, robust=True) == []
    for t in tiles:
        assert np.all(np.abs(t.anchor_ras[:2]) <= 30.0 + 1e-6)
        assert np.all(np.abs(t.corners_ras[:, :2]) <= 40.0 + 1e-6)        # on the plate
        assert np.allclose(t.corners_ras[:, 2], t.corners_ras[0, 2], atol=1e-6)   # flat
    assert r["h_mm"] == 5.0 and r["n_spins"] == 1
    # face-index form of ``eligible`` gives the same answer
    r2 = C.packing_capacity(mesh, 5.0, 1, eligible=np.flatnonzero(inner))
    assert r2["capacity"] == r["capacity"] and r2["selection"] == r["selection"]


def test_packing_capacity_wall_too_small_and_errors():
    narrow = pf.flat_wall_mesh(size_mm=12.0, step_mm=2.0)
    r = C.packing_capacity(narrow, 5.0, 1, eligible=pf.flat_wall_top_faces(narrow))
    assert r["capacity"] == 0 and r["selection"] == []
    assert r["upper_bound_area"] == 0
    assert r["note"].startswith("no candidate survives at this grid")
    assert r["label"] == "capacity at grid (h = 5 mm, 1 spins), not exact"
    mesh = pf.flat_wall_mesh(size_mm=60.0, step_mm=2.0)
    with pytest.raises(ValueError, match="eligible region is empty"):
        C.packing_capacity(mesh, 5.0, 1, eligible=np.zeros(len(mesh.faces), dtype=bool))
    with pytest.raises(ValueError, match="kind"):
        C.packing_capacity(mesh, 5.0, 1, kind="third")
    import trimesh
    with pytest.raises(ValueError):
        C.packing_capacity(trimesh.Trimesh(), 5.0, 1)
