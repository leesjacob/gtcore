"""Timing driver for the Paper 2 ``dose_stats`` task (before / after).

usage (from any directory; the tree under test is the argument, and
``gtcore.__file__`` is printed to prove which tree was measured)::

    PYTHONPATH=<tree> python scripts/bench_dose_stats.py <tree> <label> [quick]

Micro-benchmarks of ``plan.objective`` (``metrics_from_dose``, ``metrics``,
``hard``, ``evaluate``, ``gains_all``) on ``tests/plan_fixtures.toy_instance``
(C = 100, M = 4000), ``solve_greedy`` / ``solve_sa`` at N = 8 (best of 3),
then on a synthetic cavity (``make_head_phantom`` seed 1, 1 mm) either the
full ``plan.optimize(solver="sa")`` + ``final_report`` (default; ~5 min, the
conflict stage dominates) or, with ``quick``, ``final_report`` on a few
farthest-point tiles.  Timings are wall clock and load-dependent: compare
interleaved runs of the two trees and take the minimum per quantity
(docs/paper2-phase-notes.md, "Phase 2 -- one dose_stats").
"""
import os
import sys
import time

ROOT = os.path.abspath(sys.argv[1])
LABEL = sys.argv[2] if len(sys.argv) > 2 else "run"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import numpy as np  # noqa: E402

import gtcore  # noqa: E402
print("label      :", LABEL)
print("gtcore     :", gtcore.__file__)
assert os.path.abspath(gtcore.__file__).lower().startswith(ROOT.lower()), "wrong tree!"
import subprocess  # noqa: E402
print("commit     :", subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                     capture_output=True, text=True).stdout.strip(),
      "dirty" if subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip() else "clean")

import plan_fixtures as pf  # noqa: E402
from gtcore import plan  # noqa: E402
from gtcore.plan import make_objective, solve_sa, solve_greedy  # noqa: E402
from gtcore.plan import objective as ob  # noqa: E402

N_C, N_T, N_TILES = 100, 4000, 8
t0 = time.perf_counter()
inst = pf.toy_instance(n_candidates=N_C, n_targets=N_T, rng_seed=0)
print("toy_instance(%d, %d): %.1f s" % (N_C, N_T, time.perf_counter() - t0))
obj = make_objective(inst["influence"], inst["conflicts"])
cand = inst["candidates"]
rng = np.random.default_rng(0)
sels = [np.sort(rng.choice(N_C, N_TILES, replace=False)) for _ in range(200)]


def bench(fn, reps=7):
    best = float("inf")
    for _ in range(reps):
        t = time.perf_counter()
        for s in sels:
            fn(s)
        best = min(best, time.perf_counter() - t)
    return 1e6 * best / len(sels)


w = inst["target"].weights
d0 = inst["influence"].dose_of(sels[0])
print("metrics_from_dose   : %8.1f us/call (M=%d)" % (bench(lambda s: ob.metrics_from_dose(d0, w, obj.rx_cgy)), N_T))
print("objective.metrics   : %8.1f us/call" % bench(lambda s: ob.metrics(obj, s)))
print("objective.hard      : %8.1f us/call" % bench(lambda s: ob.hard(obj, s)))
print("objective.evaluate  : %8.1f us/call" % bench(lambda s: ob.evaluate(obj, s)))
print("objective.gains_all : %8.1f us/call" % bench(lambda s: ob.gains_all(obj, s), reps=3))

# greedy + SA (the optimizer proper)
best_g = float("inf")
for _ in range(3):
    t = time.perf_counter()
    g = solve_greedy(obj, N_TILES)
    best_g = min(best_g, time.perf_counter() - t)
print("solve_greedy        : %8.3f s (best of 3)  V100 %.4f D90 %.1f" % (best_g, g.metrics["V100"], g.metrics["D90"]))
best_sa = float("inf")
for _ in range(3):
    t = time.perf_counter()
    r = solve_sa(obj, N_TILES, seed=0, n_sweeps=plan.SA_N_SWEEPS, n_restarts=plan.SA_N_RESTARTS,
                 candidates=cand)
    best_sa = min(best_sa, time.perf_counter() - t)
print("solve_sa %dx%d        : %8.3f s (best of 3)  objective %.5f V100 %.4f D90 %.1f n_evals %d"
      % (plan.SA_N_RESTARTS, plan.SA_N_SWEEPS, best_sa, r.objective, r.metrics["V100"],
         r.metrics["D90"], r.extra.get("n_evals", -1)))

# the report path on a synthetic cavity (final_report: shells + target + rind)
from gtcore.phantom.generate import make_head_phantom  # noqa: E402
from gtcore.segment.surface import mask_to_mesh  # noqa: E402
vol, truth = make_head_phantom(spacing=1.0, n_tiles=3, rng_seed=1)
mask = truth.masks["cavity"]
mesh = mask_to_mesh(mask, vol.affine)
QUICK = len(sys.argv) > 3 and sys.argv[3] == "quick"
if not QUICK:
    t = time.perf_counter()
    tiles, rep = plan.optimize(mesh, n_full=N_TILES, solver="sa", seed=0, report=True)
    t_opt = time.perf_counter() - t
    print("plan.optimize(sa, 8): %8.1f s total; report runtime %s" % (
        t_opt, {k: round(v, 3) for k, v in rep.runtime.items()}))
else:
    # quick mode: 8 wall-conformed tiles by farthest-point sampling (no
    # candidate / conflict build, which dominates plan.optimize and has
    # nothing to do with the statistics)
    from gtcore.interact import conform_tile, find_overlapping_tiles, snap_to_wall
    verts = np.asarray(mesh.vertices, dtype=float)
    order = [0]
    dd = np.linalg.norm(verts - verts[0], axis=1)
    for _ in range(1, 40):
        order.append(int(np.argmax(dd)))
        dd = np.minimum(dd, np.linalg.norm(verts - verts[order[-1]], axis=1))
    tiles = []
    for i in order:
        surf, n_in = snap_to_wall(mesh, verts[i])
        tile = conform_tile(mesh, surf, n_in, np.array([1.0, 0.0, 0.0]), kind="full")
        if not find_overlapping_tiles(tiles + [tile], threshold_mm=1.0):
            tiles.append(tile)
        if len(tiles) == N_TILES:
            break
    print("quick mode: %d spread tiles (no plan.optimize)" % len(tiles))
t = time.perf_counter()
rep2 = plan.final_report(mesh, tiles, cavity_mask=mask, cavity_affine=vol.affine,
                         grid_mm=1.0, parameters={"shadowing": False})
print("final_report(1 mm, rind): %6.1f s; runtime %s" % (
    time.perf_counter() - t, {k: round(v, 3) for k, v in rep2.runtime.items()}))
s5 = rep2.metrics_grid[5.0]
print("report +5mm shell   : V100 %.4f D90 %.1f ; target V100 %.4f D90 %.1f ; rind V100 %.4f D90 %.1f"
      % (s5["V100"], s5["D90"], rep2.metrics_grid["target"]["V100"], rep2.metrics_grid["target"]["D90"],
         rep2.metrics_grid["rind"]["V100"], rep2.metrics_grid["rind"]["D90"]))
