"""Final reporting (section 3 G). Owner: A5 Validation, branch ``plan/validation``.

``final_report(mesh, tiles, rx_cgy, target, cavity_mask, cavity_affine,
interference, grid_mm, margin_mm, solver_result, parameters, clip_mask,
clip_affine, cavity_source, clip_source) -> OptimizeReport`` for any
configuration (optimized, as-implanted, manual, random):

1. ``compute_dose_grid``, exact kernel, ``grid_mm`` spacing, bounds = mesh
   bounds +- ``margin_mm`` (the margin is clipped so the grid stays at or
   below ``MAX_GRID_VOXELS``; the bounds actually used are recorded);
   optional second run with the interference model (seed capsules only, as
   the planner does), the difference reported;
2. metrics on three kinds of structure (Paper 2 Phase 2 D, "one definition
   of everything"; requirements rev 3 section 3 F8 "FIX scoring"):

   ``metrics_grid["RIND5"]``
       **The HR-CTV.**  The volumetric ``rind_depth_mm`` (5 mm) rind of the
       cavity mask -- :func:`gtcore.dose.targets.rind5`: Euclidean distance
       in physical mm, minus the cavity, intersected with ``clip_mask`` --
       built directly on the final dose grid and scored with
       :func:`gtcore.dose.metrics.dose_stats` on the exact-kernel dose at
       its voxel centres.  The entry carries the full ``DOSE_STAT_KEYS`` set
       plus ``target`` (``"RIND5"``) and the ``Target``'s ``provenance``
       (cavity source, clip source, grid, depth, commit).  Present only when
       ``cavity_mask`` / ``cavity_affine`` are given; otherwise ``notes``
       says so explicitly (never silently absent).
   ``metrics_grid["S5"][offset_mm]``
       **S5 coverage** (surrogate).  The unweighted ``dvh_stats`` of the
       wall / +5 / +10 mm shell vertices (``dvh.shell_report``) plus
       ``curve_x`` / ``curve_y``.  These are the optimizer's own surrogate
       numbers; they are reported as "S5 coverage" and never as D90 / V100
       of the HR-CTV.
   ``metrics_grid["target"]``
       The area-weighted stats of the optimizer's ``TargetSet`` (default the
       +5 mm shell) sampled trilinearly from the grid -- also a surrogate.

3. conflicts re-verified with ``find_overlapping_tiles``; shadowing listed
   via ``find_shadowing_tiles`` (a failure there becomes a note, never a
   crash);
4. ``OptimizeReport`` with gtcore version, parameters, seed, wall-clock
   time and per-stage runtime; ``to_json`` / ``to_csv`` write it out.  The
   final dose grid is attached as ``report.dose_volume`` (a ``Volume`` in
   cGy; not serialised).

All reported metrics come from the dose grid, never from the influence
matrix.  Nothing here touches solver internals.

How the RIND5 doses are evaluated (decision, 2026-11-03)
--------------------------------------------------------
The RIND5 target is built *on the dose grid itself* (``rind5(...,
grid_affine=dose.affine, grid_shape=dose.array.shape)``), so its voxel
centres are grid points and the exact-kernel dose there is already in the
grid.  The doses are therefore read straight from ``dose.array[target.mask]``
(C order, the order of ``Target.voxel_centers_ras()``;
:func:`rind5_doses_on_grid`).  This is bit-identical to
``targets.target_doses_exact`` at the same points -- ``compute_dose_grid``
and ``dose_at_points`` share ``_sum_seed_rates`` -- and costs nothing
extra, which is why the exact engine is not called a second time.  Note that
``targets.target_doses_from_grid`` (trilinear ``Volume.sample_ras``) returns
the same values only to float32 precision, because ``sample_ras`` casts the
array to float32; ``tests/test_plan_report.py`` pins both relations.

Key rename (2026-11-03, Phase 2 D)
----------------------------------
Before Phase 2 D the shell entries were the top-level float keys
``metrics_grid[0.0 / 5.0 / 10.0]`` and the cavity rind was
``metrics_grid["rind"]`` (``dose_metrics`` on ``rind_mask``).  They are now
``metrics_grid["S5"][offset]`` and ``metrics_grid["RIND5"]``.  There is no
``"rind"`` alias and no top-level float keys: every in-tree consumer
(``scripts/validation_optimize.py``, ``scripts/go_no_go_optimize.py``,
``scripts/bench_dose_stats.py``, the tests) was updated.  JSON keys are
``metrics_grid.S5.5.0.V100`` and ``metrics_grid.RIND5.D90`` in the CSV.

Weighted statistics: ``weighted_stats`` is a thin wrapper of
``gtcore.dose.metrics.dose_stats`` (Paper 2 Phase 2, "one dose_stats"):
weighted inverted-CDF (type 1) quantiles for Dxx, the ``>=`` rule for Vyyy,
nan on an empty target.  The S5 entries come from ``dvh.dvh_stats`` and the
RIND5 entry from ``targets.target_stats``, both wrappers of the same
function.

Optional parameters read from ``parameters``:

``sk_per_seed_u``
    Air-kerma strength per seed [U] for the grid (default: the engine's
    ``DEFAULT_SK_U``).  Lets a sensitivity study scale S_K without touching
    the frozen signature.
``seed``
    Recorded as the report's ``seed`` when no ``solver_result`` carries one.
``shell_offsets_mm``
    Shell offsets for the S5 coverage rows (default ``(0, 5, 10)``).
``rind_depth_mm``
    Depth of the RIND5 rind (default 5 mm; recorded back into ``parameters``).
``shadowing``
    ``False`` to skip the (dose-based) shadowing check.
"""
from __future__ import annotations

import functools
import logging
import os
import subprocess
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import DEFAULT_RX_CGY, OptimizeReport, SolverResult, TargetSet, \
    TARGET_SHELL_OFFSET_MM
from .._strict import swallow_or_raise
from ..dose.metrics import dose_stats
from ..dose.targets import Target, rind5, same_grid, target_stats
from ..interact import PlacedTile, find_overlapping_tiles, tiles_to_seed_arrays

_log = logging.getLogger(__name__)

__all__ = ["final_report", "weighted_stats", "rind5_doses_on_grid",
           "weighted_dvh_curve", "grid_bounds", "MAX_GRID_VOXELS",
           "DEFAULT_SHELL_OFFSETS_MM", "RIND_DEPTH_MM", "NO_CAVITY_NOTE"]

MAX_GRID_VOXELS = 30_000_000
# Upper bound on the dose grid: 30 M float64 voxels is ~240 MB and a few
# minutes of exact-kernel time for a 40-seed implant; the margin is shrunk
# (never the spacing) to stay below it.

DEFAULT_SHELL_OFFSETS_MM = (0.0, 5.0, 10.0)
RIND_DEPTH_MM = 5.0

NO_CAVITY_NOTE = ("RIND5 (volumetric HR-CTV) not computed: no cavity mask was given; "
                  "only the S5 coverage surrogate (shell vertices) and the weighted "
                  "target are reported")


# ------------------------------------------------------------ small helpers
def weighted_stats(doses, weights, rx_cgy: float) -> Dict[str, float]:
    """Weighted statistics of a dose vector on a weighted target: the
    report-path wrapper of :func:`gtcore.dose.metrics.dose_stats`.

    Returns the full ``DOSE_STAT_KEYS`` set -- V100 / V150 / V200 (fractions
    of the total weight at >= the level), D90 / D50 / D100 (weighted
    inverted-CDF quantiles, cGy), Dmin / Dmax / Dmean (over the samples with
    positive weight), ``n``, ``total_weight``, ``volume_cc`` (nan: a shell
    has no volume) and ``rx_cgy``.  Empty input or zero total weight -> nan
    doses and fractions, ``n = 0``, ``total_weight = 0``."""
    return dose_stats(doses, rx_cgy, weights=weights)


def grid_bounds(mesh_bounds, margin_mm: float, grid_mm: float,
                max_voxels: int = MAX_GRID_VOXELS) -> Tuple[np.ndarray, float]:
    """``(bounds (2, 3), margin_used_mm)``: mesh bounds padded by ``margin_mm``,
    the margin reduced (never below 0) until the voxel count fits
    ``max_voxels`` at ``grid_mm`` spacing."""
    b = np.asarray(mesh_bounds, dtype=float).reshape(2, 3)
    margin = float(max(0.0, margin_mm))
    spacing = float(grid_mm)

    def n_vox(m):
        ext = (b[1] - b[0]) + 2.0 * m
        return int(np.prod(np.floor(ext / spacing + 1e-9).astype(int) + 1))

    while margin > 0.0 and n_vox(margin) > int(max_voxels):
        margin = max(0.0, margin - max(1.0, spacing))
    return np.vstack([b[0] - margin, b[1] + margin]), margin


@functools.lru_cache(maxsize=1)
def _git_commit() -> Optional[str]:
    """Short commit hash of the checkout containing this file (None if no git)."""
    try:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5.0)
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:
        pass
    return None


def weighted_dvh_curve(doses, weights, levels_cgy) -> np.ndarray:
    """Weighted cumulative DVH: fraction of total weight at >= each level."""
    d = np.asarray(doses, dtype=float).reshape(-1)
    w = np.asarray(weights, dtype=float).reshape(-1)
    lv = np.asarray(levels_cgy, dtype=float).reshape(-1)
    if d.size == 0 or w.sum() <= 0:
        return np.zeros(lv.shape)
    order = np.argsort(d)
    d, w = d[order], w[order]
    tail = np.cumsum(w[::-1])[::-1]              # weight at >= d[i]
    idx = np.searchsorted(d, lv, side="left")
    out = np.where(idx < d.size, tail[np.minimum(idx, d.size - 1)], 0.0)
    return out / float(w.sum())


def _target_metrics(dose_vol, target: TargetSet, rx: float) -> Dict[str, Any]:
    from ..dose.dvh import DEFAULT_CURVE_FRACTIONS, sample_doses
    d = sample_doses(dose_vol, target.points)
    st = weighted_stats(d, target.weights, rx)
    st["name"] = target.name
    fr = np.asarray(DEFAULT_CURVE_FRACTIONS, dtype=float)
    st["curve_x"] = fr.copy()
    st["curve_y"] = weighted_dvh_curve(d, target.weights, fr * rx)
    return st


def _shell_entries(dose_vol, mesh, rx: float, offsets) -> Dict[float, Dict[str, Any]]:
    """S5 coverage rows: ``{offset_mm: dvh_stats + curve}`` of the shell vertices."""
    from ..dose.dvh import shell_report
    rep = shell_report(dose_vol, mesh, rx, offsets_mm=tuple(float(o) for o in offsets))
    out: Dict[float, Dict[str, Any]] = {}
    for off, entry in rep.items():
        row = dict(entry["stats"])
        row["curve_x"] = np.asarray(entry["curve_x"], dtype=float)
        row["curve_y"] = np.asarray(entry["curve_y"], dtype=float)
        out[float(off)] = row
    return out


# ------------------------------------------------------------------ RIND5
def rind5_doses_on_grid(target: Target, dose_volume) -> np.ndarray:
    """Exact-kernel doses [cGy] at the voxel centres of a voxel ``target``
    that lives on the dose grid: ``dose_volume.array[target.mask]`` in C
    order, the order of :meth:`gtcore.dose.targets.Target.voxel_centers_ras`.

    Bit-identical to ``targets.target_doses_exact`` at the same points when
    the grid was computed with the exact kernel (see the module notes), and
    free.  Raises when the target is not on the dose grid -- use
    ``target_doses_exact`` then."""
    if target.mask is None:
        raise ValueError("rind5_doses_on_grid needs a voxel target (RIND5), not S5")
    if not same_grid(target.affine, target.mask.shape,
                     dose_volume.affine, dose_volume.array.shape):
        raise ValueError("%s is not on the dose grid; evaluate it with "
                         "targets.target_doses_exact instead" % target.name)
    return np.asarray(dose_volume.array, dtype=float)[target.mask]


def _rind5_target(dose_vol, cavity_mask, cavity_affine, clip_mask, clip_affine,
                  depth_mm: float, cavity_source: str,
                  clip_source: Optional[str]) -> Target:
    """``RIND5(cavity, clip)`` on the dose grid (both masks resampled
    nearest-neighbour to it first, as ``rind5`` does)."""
    return rind5(np.asarray(cavity_mask, dtype=bool), np.asarray(cavity_affine, dtype=float),
                 clip_mask=None if clip_mask is None else np.asarray(clip_mask, dtype=bool),
                 clip_affine=(None if clip_affine is None
                              else np.asarray(clip_affine, dtype=float)),
                 depth_mm=float(depth_mm),
                 grid_affine=dose_vol.affine, grid_shape=dose_vol.array.shape,
                 cavity_source=cavity_source, clip_source=clip_source)


def _rind5_entry(target: Target, dose_vol, rx: float) -> Dict[str, Any]:
    """``DOSE_STAT_KEYS`` + ``target`` + ``provenance`` of the RIND5 target on
    the grid's exact-kernel doses (``targets.target_stats`` -> ``dose_stats``
    with the voxel volume, so ``volume_cc`` is the rind volume)."""
    return target_stats(target, rind5_doses_on_grid(target, dose_vol), rx)


# ------------------------------------------------------------------- main
def final_report(mesh, tiles: Sequence[PlacedTile], rx_cgy: float = DEFAULT_RX_CGY,
                 target: Optional[TargetSet] = None, cavity_mask=None,
                 cavity_affine=None, interference: bool = False,
                 grid_mm: float = 1.0, margin_mm: float = 50.0,
                 solver_result: Optional[SolverResult] = None,
                 parameters: Optional[Dict[str, Any]] = None,
                 clip_mask=None, clip_affine=None,
                 cavity_source: str = "gtcore:cavity",
                 clip_source: Optional[str] = None) -> OptimizeReport:
    """Section 3 G report for ``tiles`` on ``mesh`` (see the module docstring).

    ``metrics_grid`` keys: ``"RIND5"`` -> volumetric HR-CTV stats
    (``DOSE_STAT_KEYS`` + ``target`` + ``provenance``) on
    ``rind5(cavity_mask, cavity_affine, clip_mask, clip_affine)`` built on
    the dose grid, only when a cavity mask is given (otherwise ``notes``
    says so); ``"S5"`` -> ``{offset_mm (float): dvh_stats of the shell
    vertices + curve_x / curve_y}`` -- the S5 coverage surrogate;
    ``"target"`` -> weighted stats on the full ``target`` (default the +5 mm
    shell with area weights; surrogate).  ``metrics_grid_interference``
    mirrors it with the capsule interference model on.  ``parameters`` is
    copied and extended with ``grid_bounds_ras``, ``grid_margin_used_mm``,
    ``grid_mm``, ``grid_shape``, ``kernel``, ``sk_per_seed_u``,
    ``rind_depth_mm`` and ``git_commit``.  ``report.dose_volume`` is the
    final grid.

    ``clip_mask`` / ``clip_affine`` (optional) restrict the rind to tissue
    (the cranial interior / brain, or an RTSTRUCT ``Brain``); a clip mask
    without its affine must share the cavity grid.  ``cavity_source`` /
    ``clip_source`` are the provenance labels (``"gtcore:cavity"``,
    ``"RTSTRUCT:Cav_Post"``; ``"gtcore:cranial_interior"``,
    ``"RTSTRUCT:Brain"``).  A RIND5 failure is a note and, under
    ``GTCORE_STRICT``, re-raised.
    """
    from ..dose.engine import TG43Engine, compute_dose_grid

    t_start = time.perf_counter()
    runtime: Dict[str, float] = {}
    notes: List[str] = []
    params: Dict[str, Any] = dict(parameters or {})
    rx = float(rx_cgy)
    tiles = list(tiles)

    if mesh is None or len(getattr(mesh, "faces", ())) == 0:
        raise ValueError("final_report: mesh is empty or None")
    if float(grid_mm) <= 0.0:
        raise ValueError("final_report: grid_mm must be positive")

    sk = params.get("sk_per_seed_u", None)
    if sk is None:
        sk = TG43Engine.DEFAULT_SK_U
    sk = float(sk)
    params["sk_per_seed_u"] = sk
    offsets = tuple(float(o) for o in params.get("shell_offsets_mm", DEFAULT_SHELL_OFFSETS_MM))
    params["shell_offsets_mm"] = list(offsets)
    rind_depth = float(params.get("rind_depth_mm", RIND_DEPTH_MM))
    params["rind_depth_mm"] = rind_depth
    do_shadowing = bool(params.get("shadowing", True))

    if target is None:
        target = TargetSet.from_shell(mesh, TARGET_SHELL_OFFSET_MM)

    centers, axes = tiles_to_seed_arrays(tiles)
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    axes = np.asarray(axes, dtype=float).reshape(-1, 3)
    if centers.shape[0] == 0:
        notes.append("no tiles: dose grid is identically zero")

    # 1. dose grid ----------------------------------------------------------
    bounds, margin_used = grid_bounds(mesh.bounds, margin_mm, grid_mm)
    if margin_used < float(margin_mm) - 1e-9:
        notes.append("grid margin clipped from %g to %g mm to keep the grid "
                     "<= %d voxels" % (margin_mm, margin_used, MAX_GRID_VOXELS))
    params.update({"grid_mm": float(grid_mm), "grid_margin_mm": float(margin_mm),
                   "grid_margin_used_mm": float(margin_used),
                   "grid_bounds_ras": bounds.tolist(), "kernel": "exact",
                   "git_commit": _git_commit(), "rx_cgy": rx,
                   "n_seeds": int(centers.shape[0])})

    eng = TG43Engine()
    t0 = time.perf_counter()
    dose = compute_dose_grid(centers, axes, bounds, spacing_mm=float(grid_mm),
                             sk_per_seed_u=sk, engine=eng, exact=True)
    runtime["dose_grid"] = time.perf_counter() - t0
    params["grid_shape"] = list(int(s) for s in dose.array.shape)

    # 2. metrics --------------------------------------------------------------
    t0 = time.perf_counter()
    metrics: Dict[str, Any] = {"S5": _shell_entries(dose, mesh, rx, offsets)}
    runtime["shells"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    metrics["target"] = _target_metrics(dose, target, rx)
    runtime["target"] = time.perf_counter() - t0

    rind: Optional[Target] = None
    if cavity_mask is not None and cavity_affine is not None:
        t0 = time.perf_counter()
        try:
            rind = _rind5_target(dose, cavity_mask, cavity_affine, clip_mask, clip_affine,
                                 rind_depth, cavity_source, clip_source)
            if rind.n > 0:
                metrics["RIND5"] = _rind5_entry(rind, dose, rx)
            else:
                notes.append("RIND5 (volumetric HR-CTV) not computed: the %g mm rind of "
                             "the cavity mask is empty on the dose grid (cavity outside "
                             "the grid bounds, or removed entirely by the clip mask)"
                             % rind_depth)
                rind = None
        except Exception as exc:
            rind = None
            notes.append("RIND5 (volumetric HR-CTV) failed: %s: %s"
                         % (type(exc).__name__, exc))
            swallow_or_raise(_log, "final_report: volumetric RIND5 on the dose grid", exc)
        runtime["rind5"] = time.perf_counter() - t0
    elif (cavity_mask is None) != (cavity_affine is None):
        notes.append("RIND5 (volumetric HR-CTV) not computed: both cavity_mask and "
                     "cavity_affine are needed; only the S5 coverage surrogate and the "
                     "weighted target are reported")
    else:
        notes.append(NO_CAVITY_NOTE)

    metrics_interf = None
    if interference and centers.shape[0] > 0:
        t0 = time.perf_counter()
        try:
            from ..dose.interference import InterferenceModel
            model = InterferenceModel.from_implant(centers, axes)   # capsules only
            dose_i = compute_dose_grid(centers, axes, bounds, spacing_mm=float(grid_mm),
                                       sk_per_seed_u=sk, engine=eng, exact=True,
                                       interference=model)
            metrics_interf = {"S5": _shell_entries(dose_i, mesh, rx, offsets)}
            metrics_interf["target"] = _target_metrics(dose_i, target, rx)
            if rind is not None:
                metrics_interf["RIND5"] = _rind5_entry(rind, dose_i, rx)
            ref = metrics["S5"].get(TARGET_SHELL_OFFSET_MM, metrics["target"])
            alt = metrics_interf["S5"].get(TARGET_SHELL_OFFSET_MM, metrics_interf["target"])
            msg = ("interference (seed capsules only, carriers off): S5 coverage +%g mm "
                   "V100 %.4f -> %.4f (%+.2f pp), D90 %.0f -> %.0f cGy (%+.2f %%)"
                   % (TARGET_SHELL_OFFSET_MM, ref["V100"], alt["V100"],
                      100.0 * (alt["V100"] - ref["V100"]), ref["D90"], alt["D90"],
                      100.0 * (alt["D90"] - ref["D90"]) / max(ref["D90"], 1e-9)))
            if "RIND5" in metrics_interf:
                r0, r1 = metrics["RIND5"], metrics_interf["RIND5"]
                msg += ("; RIND5 V100 %.4f -> %.4f (%+.2f pp), D90 %.0f -> %.0f cGy"
                        % (r0["V100"], r1["V100"], 100.0 * (r1["V100"] - r0["V100"]),
                           r0["D90"], r1["D90"]))
            notes.append(msg)
        except Exception as exc:
            notes.append("interference run failed: %s: %s" % (type(exc).__name__, exc))
        runtime["interference"] = time.perf_counter() - t0
    elif interference:
        notes.append("interference requested but there are no seeds")

    # 3. conflicts / shadowing ----------------------------------------------
    t0 = time.perf_counter()
    overlaps = [(int(i), int(j)) for i, j in find_overlapping_tiles(tiles)]
    runtime["overlaps"] = time.perf_counter() - t0
    if overlaps:
        notes.append("%d overlapping tile pair(s) (planner caution): %r"
                     % (len(overlaps), overlaps))

    shadowing: List[Any] = []
    if do_shadowing and len(tiles) >= 2:
        t0 = time.perf_counter()
        try:
            from ..dose.interference import find_shadowing_tiles
            shadowing = [(int(i), int(j), float(p))
                         for i, j, p in find_shadowing_tiles(tiles, engine=eng)]
        except Exception as exc:
            notes.append("shadowing check failed: %s: %s" % (type(exc).__name__, exc))
        runtime["shadowing"] = time.perf_counter() - t0

    # 4. assemble -------------------------------------------------------------
    seed = params.get("seed", None)
    if seed is None and solver_result is not None:
        seed = solver_result.seed
    wall = time.perf_counter() - t_start
    runtime["total"] = wall
    report = OptimizeReport(
        tiles=tiles, solver=solver_result, parameters=params,
        candidate_stats=dict(params.get("candidate_stats", {})),
        metrics_influence=dict(solver_result.metrics) if solver_result is not None else {},
        metrics_grid=metrics, metrics_grid_interference=metrics_interf,
        overlaps=overlaps, shadowing=shadowing, runtime=runtime,
        seed=None if seed is None else int(seed), wall_clock_s=wall, notes=notes,
        dose_volume=dose)
    return report
