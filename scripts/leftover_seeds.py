"""Leftover-seed diagnostic, count-known N = 8 fit and F4(b)/F4(c)/F8(a)
render data for one implant CT (Paper 2, Phase 3, task ``leftovers``).

usage::

    GTCORE_STRICT=1 python scripts/leftover_seeds.py "$GT_DATA_ROOT/<PT-CT1mm>" \
        --dataset PT-CT1mm --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" \
        --n-full 8 --out docs/figures/data
    GTCORE_STRICT=1 python scripts/leftover_seeds.py "$GT_DATA_ROOT/<PH8>" \
        --dataset PH8 --n-full 8 --out docs/figures/data
    GTCORE_STRICT=1 python scripts/leftover_seeds.py "$GT_DATA_ROOT/<PT-CT1mm>" \
        --dataset PT-CT1mm --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" \
        --n-full 8 --halves 6,4 --out docs/figures/data

Run from the repository root with ``PYTHONPATH`` pointing at it (the script
prints which ``gtcore`` it imported).  Three tile fits are run on the SAME
shape-filtered, vault-filtered seed candidates (``gtcore.pipeline.reconstruct``):

``auto``
    the count-free pipeline, ``reconstruct(vol, n_full_tiles="auto")`` --
    model selection with the bent-tile score, cover pass, surface cross-check
    against the segmented cavity (``AutoFitResult``);
``count<N>``
    the count-known pipeline path, ``reconstruct(vol, n_full_tiles=N)`` --
    the exact chord-scored selector with degraded completion
    (``TileFitResult``; no mesh, no bent-tile fit);
``prior<N>``
    the planner's count-known fit, ``fit_tiles_prior(ImplantPrior(N), mesh)``
    on the pass-1 seeds with the wall mesh the planner conforms to (the
    cavity, else the scanned object's shell) -- the call
    ``scripts/strict_pipeline_check.py`` makes;
``count<F>full<H>half`` (only with ``--halves F,H``)
    the count-known fit with half tiles in the prior,
    ``fit_tiles_prior(ImplantPrior(n_full=F, n_half=H), mesh)`` (same path
    as ``prior<N>``): the row the Phase 3 leftover diagnostic asked for
    (the eight leftovers are four 2-seed pairs at the manufactured pitch,
    i.e. 6 full + 4 half tiles = 32 seeds).  The chord-scored
    ``fit_tiles(F, H, complete_degraded=True)`` partition is printed and
    stored in the finding JSON as a cross-check (not a mode).  Which
    count-known row the paper quotes waits for the operative record.

For every mode the per-tile table gives the chord residual, the bent-tile
RMS (``gtcore.tiles.deform.fit_deformable``; computed here for tiles the
chord path left without one), bending energy, fold angle, axis error,
degraded / tentative flags, surface verdict and member seeds, and the
assigned / unassigned / clutter seed indices.  For the seeds no tile of the
primary mode (``auto``) explains -- the *leftovers* -- it writes:

* the pairwise distance matrix and the pairwise axis angles;
* one row per leftover seed: centre, PCA axis, nearest-neighbour distances,
  blob volume / elongation / split count / peak HU from detection, the
  nearest fitted tile and the seed's position in that tile's frame
  (``u, v`` in-plane, ``h`` along the normal toward the cavity -- a second
  tile stacked on a fitted one would put its seeds inside the footprint at
  ``h`` of about +4 mm), unsigned distance to the wall mesh(es);
* every 4-combination of leftovers with each gate of the standard tier
  (``gtcore.tiles.fit``: chord link / side / diagonal windows, quad
  topology, grid residual, planarity, axis coherence, chord score), the
  loose (crumpled-tile) tier and the cover tier (``gtcore.tiles.auto``),
  and the count-constrained completion rule, the bent-tile fit and its
  score (``deformable_score``) -- so the best-scoring leftover quad and the
  gate that rejects it are explicit;
* every leftover pair against the half-tile gates, and the L-shaped
  triplets the cover pass would complete;
* single-linkage clusters of the leftovers at the loose chord ceiling, to
  answer whether they hold two coherent tiles' worth of geometry;
* an image patch per leftover seed (20 mm crop, axial / coronal / sagittal,
  two stated HU windows, PNG at 300 dpi) under ``<out>/leftover_patches/``.

The render export (``f4b_patient_implant.npz`` for ``PT-CT1mm``,
``f4c_phantom_implant.npz`` for ``PH8``, plus a JSON index that documents
every array) carries, for all three modes, the seed-to-tile assignment,
the leftover flags, the tile poses and the fitted tile sheets as deformed
quad surfaces (``gtcore.tiles.deform.deformed_surface_grid``: seed sheet,
tissue face and cavity face, shared triangle topology), the wall mesh the
fits used, the physician's ``Cav_Post`` mesh from the RTSTRUCT when given
(``gtcore.io.rt.rasterize_roi`` + marching cubes) and the seed centres /
axes, so a renderer never re-runs the pipeline.  Nothing is tuned here;
no model is extended.  Every CSV starts with ``#`` header lines naming the
script, the command, the commit and the dataset id.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, List, Optional, Sequence

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gtcore  # noqa: E402
from gtcore import geometry as _geom  # noqa: E402
from gtcore.tiles import (  # noqa: E402
    AutoFitResult,
    ImplantPrior,
    TileFitResult,
    deformable_score,
    deformed_surface_grid,
    fit_deformable,
    fit_rigid,
    fit_tiles,
    fit_tiles_prior,
)
from gtcore.tiles import auto as _auto  # noqa: E402
from gtcore.tiles import fit as _fit  # noqa: E402
from gtcore.tiles.fit import (  # noqa: E402
    AXIS_HARD_DEG,
    DIAG_MAX_MM,
    DIAG_MIN_MM,
    MAX_GRID_RESIDUAL_MM,
    MAX_PLANE_RMS_MM,
    PAIR_AXIS_HARD_DEG,
    SIDE_MAX_MM,
    SIDE_MIN_MM,
)

__all__ = ["ModeFit", "run_modes", "run_halves_mode", "halves_chord_crosscheck",
           "halves_mode_name", "parse_halves", "tile_rows", "leftover_indices",
           "pairwise_tables", "seed_rows", "quad_gate_rows", "pair_gate_rows",
           "triplet_rows", "cluster_finding", "best_quad", "sheet_faces",
           "tile_sheets", "export_implant", "write_patch", "write_csv",
           "mesh_from_mask", "main"]

SHEET_N = 9                       # grid points per tile side in the sheets
PATCH_HALF_MM = 10.0              # 20 mm crop
PATCH_STEP_MM = 0.25
PATCH_WINDOWS = ((400.0, 40.0), (3000.0, 1500.0))   # (W, L): soft tissue, metal
PATCH_MARK_SLAB_MM = 1.5          # other seeds drawn when within this of the plane
PATCH_DPI = 300
# completion rule of gtcore.tiles.fit._complete_degraded_quads (its numbers
# are literals there; mirrored here so the gate table can name them)
COMPLETION_CHORD_MM = (3.5, 18.0)
COMPLETION_MIN_AXIS_DOT = 0.6
COMPLETION_MIN_EXTENT_MM = 1.5


# ----------------------------------------------------------------- helpers
def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10,
                             cwd=os.path.dirname(os.path.dirname(
                                 os.path.abspath(__file__))))
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _axis_angle_deg(a, b) -> float:
    return float(np.degrees(np.arccos(min(1.0, abs(float(np.dot(a, b)))))))


def _fmt(v) -> str:
    if isinstance(v, (bool, np.bool_)):
        return "True" if v else "False"
    if isinstance(v, float) or isinstance(v, np.floating):
        return "nan" if not np.isfinite(v) else "%.4g" % v
    if isinstance(v, (list, tuple, np.ndarray)):
        return ";".join(_fmt(x) for x in v)
    return str(v)


def write_csv(path, rows: List[dict], header_lines: Sequence[str],
              fields: Optional[List[str]] = None):
    """CSV with ``#`` header comment lines; ``rows`` are dicts."""
    if fields is None:
        fields = []
        for r in rows:
            for k in r:
                if k not in fields:
                    fields.append(k)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for line in header_lines:
            fh.write("# %s\n" % line)
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k, "")) for k in fields})
    return path


def header(script_cmd: str, dataset: str, extra: Sequence[str] = ()) -> List[str]:
    lines = ["script: scripts/leftover_seeds.py",
             "command: %s" % script_cmd,
             "commit: %s" % git_commit(),
             "dataset: %s" % dataset,
             "generated: %s" % time.strftime("%Y-%m-%d %H:%M")]
    lines.extend(extra)
    return lines


# ------------------------------------------------------------------- modes
@dataclass
class ModeFit:
    """One tile fit and what it explains."""

    name: str                       # "auto" | "count8" | "prior8"
    label: str
    result: object                  # TileFitResult | AutoFitResult
    n_seeds: int
    seconds: float = float("nan")
    tiles: List[object] = field(default_factory=list)      # TilePose, all
    assigned: List[int] = field(default_factory=list)
    leftovers: List[int] = field(default_factory=list)     # all unassigned
    unassigned_in_implant: List[int] = field(default_factory=list)
    clutter: List[int] = field(default_factory=list)
    deforms: Dict[int, object] = field(default_factory=dict)  # tile_id -> DeformableFit

    @property
    def n_tiles(self) -> int:
        return len(self.tiles)

    @property
    def n_supported(self) -> int:
        return sum(1 for p in self.tiles if getattr(p, "confidence", "supported") == "supported")

    @property
    def n_tentative(self) -> int:
        return self.n_tiles - self.n_supported

    @property
    def n_degraded(self) -> int:
        return sum(1 for p in self.tiles if getattr(p, "degraded", False))

    def summary(self) -> str:
        s = ("%s: %d tiles (%d supported, %d tentative, %d degraded); %d/%d seeds "
             "assigned, %d leftover" % (self.label, self.n_tiles, self.n_supported,
                                        self.n_tentative, self.n_degraded,
                                        len(self.assigned), self.n_seeds,
                                        len(self.leftovers)))
        if self.unassigned_in_implant or self.clutter:
            s += " (%d in-implant unassigned, %d clutter)" % (
                len(self.unassigned_in_implant), len(self.clutter))
        if np.isfinite(self.seconds):
            s += "; %.1f s" % self.seconds
        return s


def _all_tiles(result) -> List[object]:
    return list(getattr(result, "all_tiles", None) or getattr(result, "tiles", []))


def leftover_indices(result, n_seeds: int):
    """``(assigned, leftovers, in_implant_unassigned, clutter)`` of a fit.

    Leftovers are every candidate no tile (supported OR tentative) owns.
    For an ``AutoFitResult`` the in-implant / clutter split is the fit's own
    (``unassigned_indices`` / ``clutter_indices``); a plain ``TileFitResult``
    has no implant region, so both lists are empty and ``leftovers`` equals
    ``rejected_indices``.
    """
    assigned = sorted({int(i) for p in _all_tiles(result) for i in p.seed_indices})
    left = [i for i in range(int(n_seeds)) if i not in set(assigned)]
    if isinstance(result, AutoFitResult):
        near = sorted(int(i) for i in (result.unassigned_indices or []))
        far = sorted(int(i) for i in (result.clutter_indices or []))
    else:
        near, far = [], []
    return assigned, left, near, far


def ensure_deform(pose, centers, axes, cache: Optional[dict] = None):
    """The bent-tile fit of a tile: the one the fit attached, else computed
    now with the public :func:`gtcore.tiles.fit_deformable` (reporting only;
    the selection is not touched)."""
    if cache is not None and pose.tile_id in cache:
        return cache[pose.tile_id]
    fit = getattr(pose, "deform", None)
    if fit is None:
        c, a = pose.seed_points(centers, axes)
        fit = fit_deformable(c, a, kind=pose.kind)
    if cache is not None:
        cache[pose.tile_id] = fit
    return fit


def _mode(name, label, result, centers, axes, seconds=float("nan")) -> ModeFit:
    n = int(np.asarray(centers).shape[0])
    assigned, left, near, far = leftover_indices(result, n)
    m = ModeFit(name=name, label=label, result=result, n_seeds=n, seconds=seconds,
                tiles=_all_tiles(result), assigned=assigned, leftovers=left,
                unassigned_in_implant=near, clutter=far)
    for p in m.tiles:
        ensure_deform(p, centers, axes, m.deforms)
    return m


def run_modes(centers, axes, n_full: int, cavity_center_ras=None, mesh=None,
              spacing_mm=None, auto_result=None, count_result=None,
              n_half: int = 0) -> Dict[str, ModeFit]:
    """The three fits on one seed cloud (see the module docstring).

    ``auto_result`` / ``count_result`` are the pipeline's own
    ``PipelineResult.tiles`` when the caller ran ``reconstruct``; otherwise
    the same calls are made here on the given candidates
    (``fit_tiles(..., "auto", mesh, spacing)`` and
    ``fit_tiles(..., n_full, complete_degraded=True)`` -- what
    ``reconstruct`` does).
    """
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    axes = np.asarray(axes, dtype=float).reshape(-1, 3)
    out = {}
    t0 = time.perf_counter()
    if auto_result is None:
        auto_result = fit_tiles(centers, axes, "auto", int(n_half),
                                cavity_center_ras=cavity_center_ras, mesh=mesh,
                                spacing_mm=spacing_mm)
        dt = time.perf_counter() - t0
    else:
        dt = float("nan")
    out["auto"] = _mode("auto", "count-free (auto)", auto_result, centers, axes, dt)

    t0 = time.perf_counter()
    if count_result is None:
        count_result = fit_tiles(centers, axes, int(n_full), int(n_half),
                                 cavity_center_ras=cavity_center_ras,
                                 complete_degraded=True)
        dt = time.perf_counter() - t0
    else:
        dt = float("nan")
    out["count%d" % n_full] = _mode("count%d" % n_full,
                                    "count-known N=%d (pipeline, chord)" % n_full,
                                    count_result, centers, axes, dt)

    t0 = time.perf_counter()
    prior = fit_tiles_prior(centers, axes, ImplantPrior(n_full=int(n_full),
                                                        n_half=int(n_half)),
                            cavity_center_ras=cavity_center_ras, mesh=mesh,
                            spacing_mm=spacing_mm, complete_degraded=True)
    out["prior%d" % n_full] = _mode("prior%d" % n_full,
                                    "count-known N=%d (planner, fit_tiles_prior)" % n_full,
                                    prior, centers, axes, time.perf_counter() - t0)
    return out


def parse_halves(text) -> tuple:
    """``--halves "F,H"`` -> ``(n_full, n_half)``; both non-negative ints."""
    parts = [p.strip() for p in str(text).split(",")]
    if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
        raise ValueError("--halves expects 'N_FULL,N_HALF' (e.g. 6,4), got %r" % (text,))
    n_full, n_half = int(parts[0]), int(parts[1])
    if n_full < 0 or n_half < 0:
        raise ValueError("--halves counts must be >= 0, got %r" % (text,))
    return n_full, n_half


def halves_mode_name(n_full: int, n_half: int) -> str:
    return "count_%dfull_%dhalf" % (int(n_full), int(n_half))


def run_halves_mode(centers, axes, n_full: int, n_half: int, cavity_center_ras=None,
                    mesh=None, spacing_mm=None) -> ModeFit:
    """The count-known fit with half tiles in the prior (module docstring):
    ``fit_tiles_prior(ImplantPrior(n_full, n_half), mesh, spacing,
    complete_degraded=True)`` -- the same planner path as ``prior<N>`` with
    ``n_half`` half tiles allowed.  Returns one :class:`ModeFit` named
    ``count_<F>full_<H>half``.  Nothing is tuned: the prior is an input."""
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    axes = np.asarray(axes, dtype=float).reshape(-1, 3)
    t0 = time.perf_counter()
    res = fit_tiles_prior(centers, axes, ImplantPrior(n_full=int(n_full), n_half=int(n_half)),
                          cavity_center_ras=cavity_center_ras, mesh=mesh,
                          spacing_mm=spacing_mm, complete_degraded=True)
    return _mode(halves_mode_name(n_full, n_half),
                 "count-known %d full + %d half (planner, fit_tiles_prior)" % (n_full, n_half),
                 res, centers, axes, time.perf_counter() - t0)


def halves_chord_crosscheck(centers, axes, n_full: int, n_half: int,
                            cavity_center_ras=None) -> ModeFit:
    """The chord-scored pipeline selector with the same counts,
    ``fit_tiles(n_full, n_half, complete_degraded=True)`` (what
    ``reconstruct(n_full_tiles=N)`` runs, plus the half tiles) -- printed
    and stored as a cross-check of :func:`run_halves_mode`, not a mode."""
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    axes = np.asarray(axes, dtype=float).reshape(-1, 3)
    t0 = time.perf_counter()
    res = fit_tiles(centers, axes, int(n_full), int(n_half),
                    cavity_center_ras=cavity_center_ras, complete_degraded=True)
    return _mode("chord_%dfull_%dhalf" % (n_full, n_half),
                 "count-known %d full + %d half (pipeline, chord)" % (n_full, n_half),
                 res, centers, axes, time.perf_counter() - t0)


def tile_rows(mode: ModeFit, centers, axes) -> List[dict]:
    """One row per tile of a mode."""
    rows = []
    for p in mode.tiles:
        fit = mode.deforms.get(p.tile_id) or ensure_deform(p, centers, axes)
        surf = getattr(p, "surface", None)
        rows.append(dict(
            mode=mode.name, tile_id=int(p.tile_id), kind=p.kind,
            confidence=getattr(p, "confidence", "supported"),
            degraded=bool(getattr(p, "degraded", False)),
            n_seeds=len(p.seed_indices) + (1 if p.inferred_seed_ras is not None else 0),
            seed_indices=list(int(i) for i in p.seed_indices),
            inferred_seed=p.inferred_seed_ras is not None,
            chord_residual_mm=float(p.residual_mm),
            bent_rms_mm=float(fit.rms_mm),
            bending_energy=float(fit.bending_energy),
            fold_deg=float(fit.params.fold_deg),
            axis_err_deg=float(fit.axis_err_deg),
            kappa1=float(fit.params.kappa1), kappa2=float(fit.params.kappa2),
            surface_verdict=surf.verdict() if surf is not None else "",
            surface_rms_mm=float(surf.rms_mm) if surf is not None else float("nan"),
            detachment_mm=float(surf.detachment_mm) if surf is not None else float("nan"),
            center_x=float(p.center_ras[0]), center_y=float(p.center_ras[1]),
            center_z=float(p.center_ras[2]),
            normal_x=float(p.normal_ras[0]), normal_y=float(p.normal_ras[1]),
            normal_z=float(p.normal_ras[2]),
        ))
    return rows


# -------------------------------------------------------- leftover geometry
def pairwise_tables(centers, axes, idx: Sequence[int]):
    """``(dist_mm, axis_angle_deg)`` matrices over the candidates ``idx``."""
    idx = [int(i) for i in idx]
    c = np.asarray(centers, dtype=float)[idx]
    a = np.asarray(axes, dtype=float)[idx]
    d = np.linalg.norm(c[:, None, :] - c[None, :, :], axis=2)
    ang = np.zeros_like(d)
    for p in range(len(idx)):
        for q in range(len(idx)):
            ang[p, q] = 0.0 if p == q else _axis_angle_deg(a[p], a[q])
    return d, ang


def matrix_rows(idx: Sequence[int], mat: np.ndarray, prefix: str) -> List[dict]:
    rows = []
    for r, i in enumerate(idx):
        row = {"seed": int(i)}
        for cidx, j in enumerate(idx):
            row["%s_%d" % (prefix, int(j))] = float(mat[r, cidx])
        rows.append(row)
    return rows


def _model_normal_sign(pose, fit) -> float:
    """+1 when the bent-tile model normal points toward the cavity (the
    ``fit.py`` pose normal points away from it), -1 otherwise -- the rule
    :func:`gtcore.tiles.auto.to_placed_tiles` applies."""
    n_model = np.asarray(fit.pose.normal, dtype=float)
    return -1.0 if float(n_model @ np.asarray(pose.normal_ras, dtype=float)) > 0.0 else 1.0


def tile_frame_coords(point, pose, fit):
    """``(u, v, h)`` of a point in a tile's bent-tile frame: ``u`` along the
    seed axes, ``v`` across, ``h`` along the normal with +h toward the
    cavity (lumen).  The frame is the rigid pose of the fit, so ``h`` is
    exact only for a flat tile; for the question "is this seed sitting on
    top of that tile" a millimetre of curvature error is immaterial."""
    R = np.asarray(fit.pose.R, dtype=float)
    q = R.T @ (np.asarray(point, dtype=float) - np.asarray(fit.pose.t, dtype=float))
    return float(q[0]), float(q[1]), float(q[2] * _model_normal_sign(pose, fit))


# layer classification of a leftover seed against a fitted tile (mm)
LAYER_COPLANAR_H_MM = 2.0        # |h| below: in the tile's own seed sheet
LAYER_STACK_H_MM = (2.0, 6.0)    # |h| in: one tile thickness away (stacked)
LAYER_MAX_OVERHANG_MM = 12.0     # beyond the footprint by more: unrelated


def layer_rows(centers, axes, idx: Sequence[int], mode: ModeFit,
               n: int = SHEET_N) -> List[dict]:
    """Every (leftover seed, fitted tile) pair: the seed in the tile's
    bent-tile frame (``u, v, h``; +h toward the cavity), its distance to
    the tile's seed sheet, whether it lies inside the footprint, and a
    layer class: ``sheet`` (|h| < 2 mm, in or just beyond the footprint:
    the seed is in that tile's own plane), ``stacked`` (inside the
    footprint, 2-6 mm above or below the sheet: one tile thickness away,
    the signature of two tiles laid on top of each other), ``edge`` (within
    12 mm beyond the footprint, |h| < 6 mm), else ``far``."""
    centers = np.asarray(centers, dtype=float)
    axes = np.asarray(axes, dtype=float)
    rows = []
    for p in mode.tiles:
        fit = mode.deforms.get(p.tile_id) or ensure_deform(p, centers, axes)
        sheet = deformed_surface_grid(fit.pose, fit.params, n=n, offset_mm=0.0, kind=p.kind)
        cu = _geom.TILE_HALF_SIZE_MM if p.kind == "full" else _geom.SEED_PITCH_MM / 2.0
        for i in idx:
            i = int(i)
            u, v, h = tile_frame_coords(centers[i], p, fit)
            du = max(0.0, abs(u) - cu)
            dv = max(0.0, abs(v) - _geom.TILE_HALF_SIZE_MM)
            over = float(np.hypot(du, dv))
            dist = float(np.linalg.norm(sheet - centers[i][None, :], axis=1).min())
            inside = over == 0.0
            if abs(h) < LAYER_COPLANAR_H_MM and over <= LAYER_MAX_OVERHANG_MM:
                cls = "sheet"
            elif inside and LAYER_STACK_H_MM[0] <= abs(h) <= LAYER_STACK_H_MM[1]:
                cls = "stacked"
            elif over <= LAYER_MAX_OVERHANG_MM and abs(h) <= LAYER_STACK_H_MM[1]:
                cls = "edge"
            else:
                cls = "far"
            rows.append(dict(seed=i, tile=int(p.tile_id), tile_seeds=list(int(k) for k in p.seed_indices),
                             u_mm=u, v_mm=v, h_mm=h, dist_sheet_mm=dist, overhang_mm=over,
                             inside_footprint=bool(inside), layer_class=cls))
    rows.sort(key=lambda r: (r["seed"], r["dist_sheet_mm"]))
    return rows


def seed_rows(centers, axes, idx: Sequence[int], mode: ModeFit, seeds=None,
              vol=None, meshes: Optional[Dict[str, object]] = None) -> List[dict]:
    """One row per leftover seed (see the module docstring)."""
    centers = np.asarray(centers, dtype=float)
    axes = np.asarray(axes, dtype=float)
    idx = [int(i) for i in idx]
    if not idx:
        return []
    n = centers.shape[0]
    assigned = set(mode.assigned)
    allD = np.linalg.norm(centers[:, None, :] - centers[None, :, :], axis=2)
    np.fill_diagonal(allD, np.inf)
    info = getattr(seeds, "info", None) or {}
    mesh_dist = {}
    if meshes:
        try:
            import trimesh
            for key, m in meshes.items():
                if m is None or not len(getattr(m, "faces", [])):
                    continue
                _c, dist, _t = trimesh.proximity.closest_point(m, centers[idx])
                mesh_dist[key] = np.asarray(dist, dtype=float)
        except Exception as exc:  # distance is context only
            print("  [warn] mesh distance skipped: %r" % (exc,))
    rows = []
    for r, i in enumerate(idx):
        row = dict(seed=i, x=float(centers[i, 0]), y=float(centers[i, 1]),
                   z=float(centers[i, 2]), axis_x=float(axes[i, 0]),
                   axis_y=float(axes[i, 1]), axis_z=float(axes[i, 2]))
        others = [j for j in range(n) if j != i]
        row["nn_any_mm"] = float(allD[i, others].min()) if others else float("nan")
        row["nn_any_seed"] = int(others[int(np.argmin(allD[i, others]))]) if others else -1
        left = [j for j in idx if j != i]
        row["nn_leftover_mm"] = float(allD[i, left].min()) if left else float("nan")
        row["nn_leftover_seed"] = int(left[int(np.argmin(allD[i, left]))]) if left else -1
        asg = sorted(assigned)
        row["nn_assigned_mm"] = float(allD[i, asg].min()) if asg else float("nan")
        row["nn_assigned_seed"] = int(asg[int(np.argmin(allD[i, asg]))]) if asg else -1
        if row["nn_leftover_seed"] >= 0:
            row["nn_leftover_axis_deg"] = _axis_angle_deg(axes[i], axes[row["nn_leftover_seed"]])
        # nearest fitted tile (by distance to its seed sheet) and the seed in
        # its frame; the full seed x tile table is layer_rows()
        lay = layer_rows(centers, axes, [i], mode)
        if lay:
            b = min(lay, key=lambda r_: r_["dist_sheet_mm"])
            row.update(nearest_tile=b["tile"], dist_sheet_mm=b["dist_sheet_mm"],
                       tile_u_mm=b["u_mm"], tile_v_mm=b["v_mm"], tile_h_mm=b["h_mm"],
                       tile_inplane_overhang_mm=b["overhang_mm"],
                       inside_footprint=b["inside_footprint"],
                       layer_class=b["layer_class"])
        else:
            row.update(nearest_tile=-1, dist_sheet_mm=float("nan"), tile_u_mm=float("nan"),
                       tile_v_mm=float("nan"), tile_h_mm=float("nan"),
                       tile_inplane_overhang_mm=float("nan"), inside_footprint=False,
                       layer_class="")
        row["in_implant"] = i in set(mode.unassigned_in_implant)
        row["clutter"] = i in set(mode.clutter)
        if seeds is not None:
            row["blob_volume_mm3"] = float(seeds.volumes_mm3[i])
            row["elongation"] = float(seeds.elongations[i])
            for key in ("split_k", "n_fragments", "source_blob"):
                val = info.get(key)
                if val is not None and len(val) == n:
                    row[key] = int(np.asarray(val)[i])
        if vol is not None:
            row["peak_hu_1mm"] = peak_hu(vol, centers[i], 1.0)
        for key, dist in mesh_dist.items():
            row["dist_%s_mm" % key] = float(dist[r])
        rows.append(row)
    return rows


def peak_hu(vol, center_ras, half_mm=1.0) -> float:
    ijk = np.asarray(vol.ras_to_index(np.asarray(center_ras, dtype=float)))
    sp = np.asarray(vol.spacing, dtype=float)
    lo = np.floor(ijk - half_mm / sp).astype(int)
    hi = np.ceil(ijk + half_mm / sp).astype(int) + 1
    nk, nj, ni = vol.array.shape
    lo = np.maximum(lo, 0)
    hi = np.minimum(hi, [ni, nj, nk])
    if np.any(hi <= lo):
        return float("nan")
    sub = vol.array[lo[2]:hi[2], lo[1]:hi[1], lo[0]:hi[0]]
    return float(sub.max()) if sub.size else float("nan")


def _quad_chords(c4):
    pair_ids = list(combinations(range(4), 2))
    d6 = np.array([np.linalg.norm(c4[a] - c4[b]) for a, b in pair_ids])
    order = np.argsort(d6)
    sides = d6[order[:4]]
    diags = d6[order[4:]]
    d1, d2 = (pair_ids[int(o)] for o in order[4:])
    topology = len({d1[0], d1[1], d2[0], d2[1]}) == 4
    s = float(sides.mean())
    ideal = np.array([s] * 4 + [s * np.sqrt(2.0)] * 2)
    fitted = np.concatenate([sides, diags])
    grid_res = float(np.sqrt(np.mean((fitted - ideal) ** 2)))
    return d6, sides, diags, topology, grid_res


def quad_gate_rows(centers, axes, idx: Sequence[int], tol: float = 1.0) -> List[dict]:
    """Every 4-combination of ``idx`` against each tier's gates, with the
    bent-tile fit and its score computed for all of them."""
    centers = np.asarray(centers, dtype=float)
    axes = np.asarray(axes, dtype=float)
    idx = sorted(int(i) for i in idx)
    rows = []
    for combo in combinations(idx, 4):
        c4 = centers[list(combo)]
        a4 = axes[list(combo)]
        d6, sides, diags, topology, grid_res = _quad_chords(c4)
        _nrm, plane_rms = _fit._plane_fit(c4)
        angles = sorted(_axis_angle_deg(a4[p], a4[q]) for p, q in combinations(range(4), 2))
        dots = [abs(float(a4[p] @ a4[q])) for p, q in combinations(range(4), 2)]
        sv = np.linalg.svd(c4 - c4.mean(axis=0), compute_uv=False)
        extent = float(sv[1])
        chord_score = (_fit.QUAD_BASE - _fit.W_RESIDUAL * grid_res
                       - _fit.W_PLANE * plane_rms - _fit._axis_penalty(angles))
        sim = fit_rigid(c4, a4, kind="full", allow_scale=True, scale_range=(0.5, 1.1))
        fit = fit_deformable(c4, a4, kind="full")
        score = float(deformable_score(fit))

        # standard tier (gtcore.tiles.fit._quad_geometry, in gate order)
        std = {
            "std_link": bool(d6.min() >= SIDE_MIN_MM and d6.max() <= DIAG_MAX_MM),
            "std_side_window": bool(sides.min() >= SIDE_MIN_MM and sides.max() <= SIDE_MAX_MM),
            "std_diag_window": bool(diags.min() >= DIAG_MIN_MM and diags.max() <= DIAG_MAX_MM),
            "std_topology": bool(topology),
            "std_grid_residual": bool(grid_res <= MAX_GRID_RESIDUAL_MM),
            "std_plane_rms": bool(plane_rms <= MAX_PLANE_RMS_MM),
            "std_axis_hard": bool(angles[2] <= AXIS_HARD_DEG),
            "std_chord_score_pos": bool(chord_score > 0.0),
        }
        # loose (crumpled) tier of auto mode
        lo, hi = _auto.LOOSE_CHORD_MM
        loose = {
            "loose_chord": bool(d6.min() >= lo and d6.max() <= hi),
            "loose_topology": bool(topology),
            "loose_axis_hard": bool(angles[2] <= _auto.LOOSE_AXIS_HARD_DEG),
            "loose_extent": bool(extent >= _auto.LOOSE_MIN_EXTENT_MM),
            "loose_sim_rms": bool(sim.rms_mm <= _auto.LOOSE_SIM_RMS_MAX_MM),
            "loose_def_rms": bool(fit.wrms_mm <= _auto.LOOSE_RMS_MAX_MM),
            "loose_energy": bool(fit.bending_energy <= _auto.LOOSE_E_MAX),
            "loose_axis_err": bool(fit.axis_err_deg <= _auto.LOOSE_AXIS_MAX_DEG),
            "loose_score_pos": bool(score > 0.0),
        }
        cover = {
            "cover_chord": loose["loose_chord"],
            "cover_topology": bool(topology),
            "cover_axis_hard": loose["loose_axis_hard"],
            "cover_extent": loose["loose_extent"],
            "cover_sim_rms": bool(sim.rms_mm <= _auto.COVER_SIM_RMS_MAX_MM * tol),
            "cover_def_rms": bool(fit.wrms_mm <= _auto.COVER_RMS_MAX_MM * tol),
            "cover_energy": bool(fit.bending_energy <= _auto.COVER_E_MAX),
            "cover_axis_err": bool(fit.axis_err_deg <= _auto.COVER_AXIS_MAX_DEG),
            "cover_score_pos": bool(score > 0.0),
        }
        comp = {
            "completion_chord": bool(d6.min() >= COMPLETION_CHORD_MM[0]
                                     and d6.max() <= COMPLETION_CHORD_MM[1]),
            "completion_axis_dot": bool(float(np.mean(dots)) >= COMPLETION_MIN_AXIS_DOT),
            "completion_extent": bool(extent >= COMPLETION_MIN_EXTENT_MM),
        }
        ref = _fit._quad_geometry(tuple(range(4)), c4, a4,
                                  np.linalg.norm(c4[:, None] - c4[None], axis=2))
        failed = [k for d in (std, loose, cover, comp) for k, ok in d.items() if not ok]
        row = dict(seeds=list(combo),
                   chords_mm=[float(x) for x in np.sort(d6)],
                   side_min_mm=float(sides.min()), side_max_mm=float(sides.max()),
                   diag_min_mm=float(diags.min()), diag_max_mm=float(diags.max()),
                   grid_residual_mm=grid_res, plane_rms_mm=float(plane_rms),
                   axis_angle_3rd_deg=float(angles[2]),
                   axis_angle_max_deg=float(angles[-1]),
                   mean_axis_dot=float(np.mean(dots)), extent_sv2_mm=extent,
                   sim_rms_mm=float(sim.rms_mm), sim_scale=float(sim.pose.scale),
                   chord_score=float(chord_score),
                   def_rms_mm=float(fit.rms_mm), bending_energy=float(fit.bending_energy),
                   fold_deg=float(fit.params.fold_deg),
                   def_axis_err_deg=float(fit.axis_err_deg), def_score=score)
        row.update(std)
        row["standard_pass"] = all(std.values())
        row["standard_pass_ref"] = ref is not None
        row.update(loose)
        row["loose_pass"] = all(loose.values())
        row.update(cover)
        row["cover_pass"] = all(cover.values())
        row.update(comp)
        row["completion_pass"] = all(comp.values())
        row["gates_failed"] = failed
        rows.append(row)
    rows.sort(key=lambda r: -r["def_score"])
    return rows


def best_quad(rows: List[dict]) -> Optional[dict]:
    """The best-scoring leftover quad and why each tier rejects it."""
    if not rows:
        return None
    r = max(rows, key=lambda q: q["def_score"])
    why = {
        "standard": [k for k in r["gates_failed"] if k.startswith("std_")],
        "loose": [k for k in r["gates_failed"] if k.startswith("loose_")],
        "cover": [k for k in r["gates_failed"] if k.startswith("cover_")],
        "completion": [k for k in r["gates_failed"] if k.startswith("completion_")],
    }
    return dict(seeds=r["seeds"], def_score=r["def_score"], def_rms_mm=r["def_rms_mm"],
                bending_energy=r["bending_energy"], fold_deg=r["fold_deg"],
                def_axis_err_deg=r["def_axis_err_deg"], chords_mm=r["chords_mm"],
                standard_pass=r["standard_pass"], loose_pass=r["loose_pass"],
                cover_pass=r["cover_pass"], completion_pass=r["completion_pass"],
                rejected_by=why,
                below_penalty=bool(r["def_score"] < _auto.LAMBDA_FULL))


def pair_gate_rows(centers, axes, idx: Sequence[int]) -> List[dict]:
    """Every leftover pair against the half-tile gates of ``fit._enumerate_pairs``."""
    centers = np.asarray(centers, dtype=float)
    axes = np.asarray(axes, dtype=float)
    idx = sorted(int(i) for i in idx)
    rows = []
    for i, j in combinations(idx, 2):
        d = float(np.linalg.norm(centers[i] - centers[j]))
        ang = _axis_angle_deg(axes[i], axes[j])
        sep = _unit(centers[j] - centers[i])
        align = []
        for a in (axes[i], axes[j]):
            to_sep = _axis_angle_deg(a, sep)
            align.append(min(to_sep, 90.0 - to_sep))
        align_pen = _fit.W_PAIR_ALIGN * float(np.deg2rad(
            np.mean([max(0.0, a - _fit.PAIR_ALIGN_FREE_DEG) for a in align])))
        score = (_fit.PAIR_BASE - _fit.W_PAIR_DIST * abs(d - _fit.PAIR_EXPECT_MM)
                 - _fit._axis_penalty([ang]) - align_pen)
        gates = {"pair_chord_window": bool(SIDE_MIN_MM <= d <= SIDE_MAX_MM),
                 "pair_axis_hard": bool(ang <= PAIR_AXIS_HARD_DEG),
                 "pair_score_pos": bool(score > 0.0)}
        rows.append(dict(seeds=[i, j], dist_mm=d, axis_angle_deg=ang,
                         align_dev_deg=float(np.mean(align)), pair_score=float(score),
                         **gates, pair_pass=all(gates.values()),
                         gates_failed=[k for k, ok in gates.items() if not ok]))
    rows.sort(key=lambda r: -r["pair_score"])
    return rows


def triplet_rows(centers, axes, idx: Sequence[int], tol: float = 1.0) -> List[dict]:
    """L-shaped triplets among ``idx`` by the cover pass's arm / hypotenuse /
    corner-angle windows (``gtcore.tiles.auto.TRIPLET_*``), with the
    parallelogram 4th-seed position."""
    centers = np.asarray(centers, dtype=float)
    axes = np.asarray(axes, dtype=float)
    idx = sorted(int(i) for i in idx)
    slack = tol - 1.0
    arm_lo, arm_hi = _auto.TRIPLET_ARM_MM[0] - slack, _auto.TRIPLET_ARM_MM[1] + slack
    hyp_lo, hyp_hi = _auto.TRIPLET_HYPOT_MM[0] - slack, _auto.TRIPLET_HYPOT_MM[1] + slack
    rows = []
    for combo in combinations(idx, 3):
        i, j, k = combo
        chords = [(float(np.linalg.norm(centers[i] - centers[j])), k),
                  (float(np.linalg.norm(centers[i] - centers[k])), j),
                  (float(np.linalg.norm(centers[j] - centers[k])), i)]
        chords.sort(key=lambda c: c[0])
        (d_a, _), (d_b, _), (d_h, corner) = chords
        arms_ok = arm_lo <= d_a <= arm_hi and arm_lo <= d_b <= arm_hi
        hyp_ok = hyp_lo <= d_h <= hyp_hi
        arms = [m for m in combo if m != corner]
        u = centers[arms[0]] - centers[corner]
        v = centers[arms[1]] - centers[corner]
        cosang = float(u @ v) / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-12)
        ang = float(np.degrees(np.arccos(np.clip(cosang, -1.0, 1.0))))
        corner_ok = _auto.TRIPLET_CORNER_DEG[0] <= ang <= _auto.TRIPLET_CORNER_DEG[1]
        angles = sorted(_axis_angle_deg(axes[a], axes[b]) for a, b in combinations(combo, 2))
        axis_ok = angles[0] <= _auto.LOOSE_AXIS_HARD_DEG
        fourth = centers[arms[0]] + centers[arms[1]] - centers[corner]
        gap = float(np.linalg.norm(centers - fourth[None, :], axis=1).min())
        gap_ok = gap >= _auto.TRIPLET_MIN_GAP_MM
        gates = {"triplet_arms": bool(arms_ok), "triplet_hypotenuse": bool(hyp_ok),
                 "triplet_corner_angle": bool(corner_ok), "triplet_axis": bool(axis_ok),
                 "triplet_4th_free": bool(gap_ok)}
        rows.append(dict(seeds=list(combo), corner_seed=int(corner), arm_a_mm=d_a,
                         arm_b_mm=d_b, hypotenuse_mm=d_h, corner_angle_deg=ang,
                         fourth_x=float(fourth[0]), fourth_y=float(fourth[1]),
                         fourth_z=float(fourth[2]), fourth_gap_to_detection_mm=gap,
                         **gates, triplet_pass=all(gates.values()),
                         gates_failed=[k for k, ok in gates.items() if not ok]))
    rows.sort(key=lambda r: (-int(r["triplet_pass"]), r["hypotenuse_mm"]))
    return rows


def cluster_finding(centers, idx: Sequence[int], quad_rows: List[dict],
                    link_mm: Optional[float] = None) -> dict:
    """Do the leftovers hold coherent tiles' worth of geometry?

    Single-linkage clusters at ``link_mm`` (default the loose chord ceiling,
    16.5 mm); per cluster its size and the best bent-tile quad score; the
    verdict counts disjoint leftover quads that pass the loose or the cover
    tier (what a model extension could at most recover).
    """
    centers = np.asarray(centers, dtype=float)
    idx = sorted(int(i) for i in idx)
    if link_mm is None:
        link_mm = float(_auto.LOOSE_CHORD_MM[1])
    clusters: List[List[int]] = []
    if idx:
        from scipy.cluster.hierarchy import fcluster, linkage
        if len(idx) == 1:
            clusters = [[idx[0]]]
        else:
            Z = linkage(centers[idx], method="single")
            lab = fcluster(Z, t=link_mm, criterion="distance")
            for l_ in sorted(set(lab)):
                clusters.append([idx[k] for k in range(len(idx)) if lab[k] == l_])
    passing = [r for r in quad_rows if r["loose_pass"] or r["cover_pass"]]
    # disjoint passing quads, greedy by score
    chosen, used = [], set()
    for r in sorted(passing, key=lambda q: -q["def_score"]):
        if not (used & set(r["seeds"])):
            chosen.append(r)
            used |= set(r["seeds"])
    per = []
    for cl in clusters:
        qs = [r for r in quad_rows if set(r["seeds"]) <= set(cl)]
        best = max(qs, key=lambda q: q["def_score"]) if qs else None
        per.append(dict(seeds=cl, n=len(cl),
                        best_quad_seeds=best["seeds"] if best else [],
                        best_quad_score=best["def_score"] if best else float("nan"),
                        best_quad_def_rms_mm=best["def_rms_mm"] if best else float("nan"),
                        best_quad_fold_deg=best["fold_deg"] if best else float("nan"),
                        best_quad_standard=best["standard_pass"] if best else False,
                        best_quad_loose=best["loose_pass"] if best else False,
                        best_quad_cover=best["cover_pass"] if best else False))
    n_coherent = len(chosen)
    if not idx:
        verdict = "no leftover seeds"
    elif n_coherent >= 2:
        verdict = ("%d disjoint leftover quads pass the loose/cover tiers: two "
                   "coherent tiles' worth of geometry -- report to Jacob" % n_coherent)
    elif n_coherent == 1:
        verdict = "one leftover quad passes the loose/cover tiers; the rest do not form a tile"
    else:
        verdict = "no leftover quad passes any tier: the leftovers form no coherent tile"
    return dict(link_mm=float(link_mm), clusters=per, n_clusters=len(clusters),
                n_quads=len(quad_rows), n_quads_standard=sum(r["standard_pass"] for r in quad_rows),
                n_quads_loose=sum(r["loose_pass"] for r in quad_rows),
                n_quads_cover=sum(r["cover_pass"] for r in quad_rows),
                n_quads_completion=sum(r["completion_pass"] for r in quad_rows),
                n_disjoint_passing=n_coherent,
                disjoint_passing=[r["seeds"] for r in chosen], verdict=verdict)


# -------------------------------------------------------------- patches
def write_patch(vol, center_ras, seed_index: int, out_png: str,
                other_centers=None, other_leftover=None, dataset: str = "",
                half_mm: float = PATCH_HALF_MM, step_mm: float = PATCH_STEP_MM,
                windows=PATCH_WINDOWS, dpi: int = PATCH_DPI) -> str:
    """Axial / coronal / sagittal 20 mm crops through one seed on the RAW
    volume (nearest-voxel sampling on an isotropic RAS grid), one row per
    HU window ``(W, L)``; the seed centre is marked +, other detected seeds
    within ``PATCH_MARK_SLAB_MM`` of the plane as circles (red = leftover).
    Diagnostic image, not a paper panel."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    c = np.asarray(center_ras, dtype=float).reshape(3)
    s = np.arange(-half_mm, half_mm + 1e-9, step_mm)
    A, B = np.meshgrid(s, s)                         # columns = first axis
    planes = [("axial (x-y) z=%.1f" % c[2], (0, 1), 2),
              ("coronal (x-z) y=%.1f" % c[1], (0, 2), 1),
              ("sagittal (y-z) x=%.1f" % c[0], (1, 2), 0)]
    others = None if other_centers is None else np.asarray(other_centers, dtype=float)
    leftover = (np.zeros(0, dtype=bool) if other_leftover is None
                else np.asarray(other_leftover, dtype=bool))
    fig, axs = plt.subplots(len(windows), 3, figsize=(7.5, 2.6 * len(windows)))
    axs = np.atleast_2d(axs)
    for r, (W, L) in enumerate(windows):
        for col, (title, (ia, ib), fixed) in enumerate(planes):
            pts = np.tile(c, (A.size, 1))
            pts[:, ia] = c[ia] + A.ravel()
            pts[:, ib] = c[ib] + B.ravel()
            img = vol.sample_ras(pts, order=0, fill=-1000.0).reshape(A.shape)
            ax = axs[r, col]
            ax.imshow(img, cmap="gray", vmin=L - W / 2.0, vmax=L + W / 2.0,
                      origin="lower", extent=[-half_mm, half_mm, -half_mm, half_mm],
                      interpolation="nearest")
            ax.plot([0.0], [0.0], "+", color="#D55E00", ms=9, mew=1.2)
            if others is not None and len(others):
                dz = np.abs(others[:, fixed] - c[fixed])
                near = dz <= PATCH_MARK_SLAB_MM
                for k in np.flatnonzero(near):
                    if np.allclose(others[k], c):
                        continue
                    da, db = others[k, ia] - c[ia], others[k, ib] - c[ib]
                    if abs(da) > half_mm or abs(db) > half_mm:
                        continue                      # outside the crop
                    colr = "#D55E00" if (k < len(leftover) and leftover[k]) else "white"
                    ax.plot([da], [db], "o", mfc="none", mec=colr, ms=10, mew=1.0)
            ax.set_xlim(-half_mm, half_mm)
            ax.set_ylim(-half_mm, half_mm)
            ax.set_aspect("equal")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_title("%s mm  W %d / L %d" % (title, int(W), int(L)), fontsize=7)
    fig.suptitle("%s seed %d  (%.1f, %.1f, %.1f) RAS mm; 20 mm crop, %.2f mm grid, "
                 "nearest voxel" % (dataset, seed_index, c[0], c[1], c[2], step_mm),
                 fontsize=8)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    fig.savefig(out_png, dpi=dpi)
    plt.close(fig)
    return out_png


# ------------------------------------------------------------ render export
def sheet_faces(n: int = SHEET_N) -> np.ndarray:
    """Triangles of the ``n x n`` row-major sheet grid of
    :func:`gtcore.tiles.deformed_surface_grid` (``2 (n-1)^2`` faces)."""
    faces = []
    for r in range(n - 1):
        for c in range(n - 1):
            v00 = r * n + c
            v01 = v00 + 1
            v10 = v00 + n
            v11 = v10 + 1
            faces.append([v00, v10, v11])
            faces.append([v00, v11, v01])
    return np.asarray(faces, dtype=np.int32)


def tile_sheets(mode: ModeFit, centers, axes, n: int = SHEET_N) -> Dict[str, np.ndarray]:
    """Per-tile arrays of one mode (see :func:`export_implant`)."""
    T = len(mode.tiles)
    out = {
        "tile_id": np.full(T, -1, dtype=np.int32),
        "tile_kind": np.array([p.kind for p in mode.tiles], dtype="U4") if T else np.zeros(0, "U4"),
        "tile_confidence": (np.array([getattr(p, "confidence", "supported") for p in mode.tiles],
                                     dtype="U9") if T else np.zeros(0, "U9")),
        "tile_degraded": np.zeros(T, dtype=bool),
        "tile_seed_indices": np.full((T, 4), -1, dtype=np.int32),
        "tile_inferred_seed_ras": np.full((T, 3), np.nan),
        "tile_center_ras": np.full((T, 3), np.nan),
        "tile_normal_ras": np.full((T, 3), np.nan),
        "tile_axis_ras": np.full((T, 3), np.nan),
        "tile_chord_residual_mm": np.full(T, np.nan),
        "tile_bent_rms_mm": np.full(T, np.nan),
        "tile_bending_energy": np.full(T, np.nan),
        "tile_fold_deg": np.full(T, np.nan),
        "tile_axis_err_deg": np.full(T, np.nan),
        "tile_model_normal_toward_cavity": np.full((T, 3), np.nan),
        "sheet_seed_vertices": np.full((T, n * n, 3), np.nan),
        "sheet_tissue_vertices": np.full((T, n * n, 3), np.nan),
        "sheet_cavity_vertices": np.full((T, n * n, 3), np.nan),
        "sheet_corners_tissue_ras": np.full((T, 4, 3), np.nan),
        "tile_model_seed_ras": np.full((T, 4, 3), np.nan),
        "tile_wall_corners_ras": np.full((T, 4, 3), np.nan),
        "tile_wall_seed_ras": np.full((T, 4, 3), np.nan),
        "tile_wall_verdict": (np.array([""] * T, dtype="U96") if T else np.zeros(0, "U96")),
        "seed_tile_id": np.full(mode.n_seeds, -1, dtype=np.int32),
        "seed_leftover": np.ones(mode.n_seeds, dtype=bool),
    }
    for t, p in enumerate(mode.tiles):
        fit = mode.deforms.get(p.tile_id) or ensure_deform(p, centers, axes)
        sgn = _model_normal_sign(p, fit)
        out["tile_id"][t] = int(p.tile_id)
        out["tile_degraded"][t] = bool(getattr(p, "degraded", False))
        k = min(4, len(p.seed_indices))
        out["tile_seed_indices"][t, :k] = [int(i) for i in p.seed_indices[:k]]
        if p.inferred_seed_ras is not None:
            out["tile_inferred_seed_ras"][t] = p.inferred_seed_ras
        out["tile_center_ras"][t] = p.center_ras
        out["tile_normal_ras"][t] = p.normal_ras
        out["tile_axis_ras"][t] = p.axis_ras
        out["tile_chord_residual_mm"][t] = float(p.residual_mm)
        out["tile_bent_rms_mm"][t] = float(fit.rms_mm)
        out["tile_bending_energy"][t] = float(fit.bending_energy)
        out["tile_fold_deg"][t] = float(fit.params.fold_deg)
        out["tile_axis_err_deg"][t] = float(fit.axis_err_deg)
        out["tile_model_normal_toward_cavity"][t] = sgn * np.asarray(fit.pose.normal)
        out["sheet_seed_vertices"][t] = deformed_surface_grid(fit.pose, fit.params, n=n,
                                                              offset_mm=0.0, kind=p.kind)
        out["sheet_tissue_vertices"][t] = deformed_surface_grid(
            fit.pose, fit.params, n=n, offset_mm=-_geom.SEED_PLANE_OFFSET_MM * sgn, kind=p.kind)
        out["sheet_cavity_vertices"][t] = deformed_surface_grid(
            fit.pose, fit.params, n=n,
            offset_mm=(_geom.TILE_THICKNESS_MM - _geom.SEED_PLANE_OFFSET_MM) * sgn, kind=p.kind)
        out["sheet_corners_tissue_ras"][t] = fit.footprint(offset_mm=-_geom.SEED_PLANE_OFFSET_MM * sgn)
        sp = fit.seed_points()
        out["tile_model_seed_ras"][t, :sp.shape[0]] = sp
        surf = getattr(p, "surface", None)
        if surf is not None:
            out["tile_wall_corners_ras"][t] = surf.placed.corners_ras
            sc = np.asarray(surf.placed.seed_centers)
            out["tile_wall_seed_ras"][t, :sc.shape[0]] = sc
            out["tile_wall_verdict"][t] = surf.verdict()[:96]
        for i in p.seed_indices:
            out["seed_tile_id"][int(i)] = int(p.tile_id)
            out["seed_leftover"][int(i)] = False
    return out


_ARRAY_DOCS = {
    "seed_centers_ras": ("mm", "detected seed centres (shape-filtered, vault-filtered candidates), RAS"),
    "seed_axes_ras": ("unit", "per-seed PCA long axis (sign arbitrary), RAS"),
    "seed_volume_mm3": ("mm^3", "thresholded (bloomed) blob volume from detection"),
    "seed_elongation": ("-", "sqrt(major/minor) PCA eigenvalue ratio of the blob"),
    "seed_length_mm": ("mm", "capsule length to draw (gtcore.geometry.SEED_LENGTH_MM)"),
    "seed_diameter_mm": ("mm", "capsule diameter (gtcore.geometry.SEED_DIAMETER_MM)"),
    "cavity_center_ras": ("mm", "centroid of the gtcore cavity mask (nan when none): tile normals point away from it"),
    "volume_spacing_mm": ("mm", "voxel spacing (si, sj, sk) of the CT"),
    "volume_shape_kji": ("voxels", "CT array shape [k, j, i]"),
    "wall_mesh_vertices": ("mm", "the mesh the fits conformed to (planner rule: gtcore cavity, else the scanned object's shell), RAS"),
    "wall_mesh_faces": ("index", "triangles of wall_mesh_vertices"),
    "wall_mesh_source": ("-", "'gtcore:cavity' | 'gtcore:body' | 'none'"),
    "cavity_rt_vertices": ("mm", "physician's Cav_Post from the RTSTRUCT, rasterised on the CT grid (4x4 supersampled, >= 0.5) and marching-cubed (Taubin-smoothed), RAS"),
    "cavity_rt_faces": ("index", "triangles of cavity_rt_vertices"),
    "sheet_faces": ("index", "triangles shared by every *_sheet_*_vertices array (row-major n x n grid, sheet_grid_n)"),
    "sheet_grid_n": ("-", "grid points per tile side in the sheets"),
    "tile_id": ("-", "tile id within the mode"),
    "tile_kind": ("-", "'full' (4 seeds) or 'half' (2 seeds)"),
    "tile_confidence": ("-", "'supported' (calibrated gates / model selection) or 'tentative' (cover pass)"),
    "tile_degraded": ("bool", "recovered outside the standard gates (crumpled / folded; loose tier or count completion)"),
    "tile_seed_indices": ("index", "member seeds (into seed_centers_ras), -1 padded; a triplet-completed tile has 3"),
    "tile_inferred_seed_ras": ("mm", "the inferred 4th seed of a triplet-completed tile, nan otherwise"),
    "tile_center_ras": ("mm", "mean of the member seeds"),
    "tile_normal_ras": ("unit", "fit.py pose normal: away from the cavity centre when one was given"),
    "tile_axis_ras": ("unit", "in-plane tile axis t1 (along the seed axes)"),
    "tile_chord_residual_mm": ("mm", "RMS chord deviation from the ideal grid (chord score path)"),
    "tile_bent_rms_mm": ("mm", "seed-position RMS of the bent-tile fit (gtcore.tiles.deform.fit_deformable)"),
    "tile_bending_energy": ("1/mm^2", "kappa1^2 + kappa2^2 of the bent-tile fit"),
    "tile_fold_deg": ("deg", "total bend angle across the 20 mm tile in the stiffer direction"),
    "tile_axis_err_deg": ("deg", "mean angle between observed seed axes and the fitted sheet tangent"),
    "tile_model_normal_toward_cavity": ("unit", "bent-tile model normal, sign fixed to point toward the cavity (to_placed_tiles rule)"),
    "sheet_seed_vertices": ("mm", "deformed quad surface through the seeds (bent-tile model, offset 0), row-major n x n, RAS"),
    "sheet_tissue_vertices": ("mm", "the same sheet 3 mm toward the wall (tissue face of the tile)"),
    "sheet_cavity_vertices": ("mm", "the same sheet 1 mm toward the cavity (cavity face of the 4 mm tile)"),
    "sheet_corners_tissue_ras": ("mm", "the 4 collagen corners on the tissue face (loop order)"),
    "tile_model_seed_ras": ("mm", "the bent-tile model's seed positions (fit order), nan padded for half tiles"),
    "tile_wall_corners_ras": ("mm", "tile footprint draped on the wall mesh (surface cross-check), nan when no mesh"),
    "tile_wall_seed_ras": ("mm", "seed centres of the wall-conformed tile, nan when no mesh"),
    "tile_wall_verdict": ("-", "surface cross-check verdict text"),
    "seed_tile_id": ("-", "per seed: owning tile id in this mode, -1 = leftover"),
    "seed_leftover": ("bool", "per seed: no tile of this mode owns it"),
}


def _mesh_arrays(mesh):
    if mesh is None or not len(getattr(mesh, "faces", [])):
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int32)
    return (np.asarray(mesh.vertices, dtype=float),
            np.asarray(mesh.faces, dtype=np.int32))


def export_implant(npz_path: str, dataset: str, centers, axes, modes: Dict[str, ModeFit],
                   primary: str, wall_mesh=None, wall_source: str = "none",
                   cavity_rt_mesh=None, cavity_center_ras=None, seeds=None,
                   spacing_mm=None, shape_kji=None, command: str = "",
                   n_full: Optional[int] = None, n: int = SHEET_N, notes: str = "",
                   halves=None) -> str:
    """Write the render NPZ and its JSON index (``<stem>.json``).  ``halves``
    = ``(n_full, n_half)`` of the ``--halves`` mode when one was run (recorded
    as ``halves_requested`` in the index)."""
    centers = np.asarray(centers, dtype=float).reshape(-1, 3)
    axes = np.asarray(axes, dtype=float).reshape(-1, 3)
    N = centers.shape[0]
    arrays: Dict[str, np.ndarray] = {
        "seed_centers_ras": centers,
        "seed_axes_ras": axes,
        "seed_length_mm": np.array(_geom.SEED_LENGTH_MM),
        "seed_diameter_mm": np.array(_geom.SEED_DIAMETER_MM),
        "cavity_center_ras": (np.full(3, np.nan) if cavity_center_ras is None
                              else np.asarray(cavity_center_ras, dtype=float)),
        "volume_spacing_mm": (np.full(3, np.nan) if spacing_mm is None
                              else np.asarray(spacing_mm, dtype=float)),
        "volume_shape_kji": (np.zeros(3, dtype=np.int64) if shape_kji is None
                             else np.asarray(shape_kji, dtype=np.int64)),
        "sheet_faces": sheet_faces(n),
        "sheet_grid_n": np.array(n),
        "wall_mesh_source": np.array(wall_source),
    }
    if seeds is not None:
        arrays["seed_volume_mm3"] = np.asarray(seeds.volumes_mm3, dtype=float)
        arrays["seed_elongation"] = np.asarray(seeds.elongations, dtype=float)
    v, f = _mesh_arrays(wall_mesh)
    arrays["wall_mesh_vertices"], arrays["wall_mesh_faces"] = v, f
    if cavity_rt_mesh is not None:
        v, f = _mesh_arrays(cavity_rt_mesh)
        arrays["cavity_rt_vertices"], arrays["cavity_rt_faces"] = v, f
    index_modes = {}
    for name, m in modes.items():
        sheets = tile_sheets(m, centers, axes, n=n)
        for key, arr in sheets.items():
            arrays["%s_%s" % (name, key)] = arr
        index_modes[name] = dict(
            label=m.label, n_tiles=m.n_tiles, n_supported=m.n_supported,
            n_tentative=m.n_tentative, n_degraded=m.n_degraded,
            n_assigned=len(m.assigned), n_leftover=len(m.leftovers),
            leftovers=[int(i) for i in m.leftovers],
            unassigned_in_implant=[int(i) for i in m.unassigned_in_implant],
            clutter=[int(i) for i in m.clutter],
            result_type=type(m.result).__name__,
            score_rule=getattr(m.result, "score_rule", ""),
            summary=(m.result.summary() if hasattr(m.result, "summary") else ""),
            seconds=(None if not np.isfinite(m.seconds) else round(float(m.seconds), 1)))
    readme = ("%s implant render data (Paper 2 F4(b)/F4(c)/F8(a)); see the JSON index "
              "next to this file for every array. primary mode: %s. %s" % (dataset, primary, notes))
    arrays["readme"] = np.array(readme)
    os.makedirs(os.path.dirname(os.path.abspath(npz_path)), exist_ok=True)
    np.savez_compressed(npz_path, **arrays)

    doc = {}
    for key, arr in arrays.items():
        base = key
        for name in modes:
            if key.startswith(name + "_"):
                base = key[len(name) + 1:]
                break
        units, text = _ARRAY_DOCS.get(base, ("-", ""))
        if base != key:
            text = "[mode %s] %s" % (key[:len(key) - len(base) - 1], text)
        if key == "readme":
            units, text = "-", "this description"
        doc[key] = dict(shape=list(np.asarray(arr).shape), dtype=str(np.asarray(arr).dtype),
                        units=units, doc=text)
    index = dict(
        dataset=dataset, script="scripts/leftover_seeds.py", command=command,
        commit=git_commit(), generated=time.strftime("%Y-%m-%d %H:%M"),
        gtcore_file=os.path.relpath(gtcore.__file__, os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))),
        n_seeds=int(N), n_full_requested=n_full, primary_mode=primary,
        halves_requested=(None if halves is None else
                          dict(n_full=int(halves[0]), n_half=int(halves[1]),
                               mode=halves_mode_name(halves[0], halves[1]))),
        wall_mesh_source=wall_source, has_cavity_rt=cavity_rt_mesh is not None,
        normals_oriented=cavity_center_ras is not None,
        conventions=dict(
            coordinates="RAS mm; arrays indexed [k, j, i]; affine maps (i, j, k, 1) -> RAS",
            sheets=("deformed_surface_grid(pose, params, n, offset) of the bent-tile fit; "
                    "offset 0 = seed sheet, -3 mm = tissue face (on the wall), +1 mm = "
                    "cavity face; offsets taken along the model normal oriented toward "
                    "the cavity (tile_model_normal_toward_cavity)"),
            faces="sheet_faces is shared: vertex r*n + c of the row-major n x n grid",
            leftover="seed_tile_id == -1 / seed_leftover True: no tile of that mode owns the seed",
            modes=("auto = count-free pipeline (reconstruct n_full_tiles='auto'); count<N> = "
                   "count-known pipeline path (reconstruct n_full_tiles=N, chord score, degraded "
                   "completion); prior<N> = planner path fit_tiles_prior(ImplantPrior(N), mesh); "
                   "count_<F>full_<H>half = planner path fit_tiles_prior(ImplantPrior(n_full=F, "
                   "n_half=H), mesh) with half tiles in the prior (--halves F,H); a half tile has "
                   "tile_kind 'half', two member seeds (-1 padded) and a half-width sheet")),
        modes=index_modes, arrays=doc, notes=notes)
    json_path = os.path.splitext(npz_path)[0] + ".json"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1)
    return npz_path


def mesh_from_mask(mask, affine, pad: int = 2, step_size: int = 1, smooth_iterations: int = 10):
    """Marching cubes of a mask restricted to its bounding box (plus ``pad``
    voxels) -- :func:`gtcore.segment.mask_to_mesh` on the crop, with the
    affine shifted to the crop origin."""
    from gtcore.segment import mask_to_mesh

    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return None
    kji = np.argwhere(mask)
    lo = np.maximum(kji.min(axis=0) - pad, 0)
    hi = np.minimum(kji.max(axis=0) + pad + 1, mask.shape)
    sub = mask[lo[0]:hi[0], lo[1]:hi[1], lo[2]:hi[2]]
    aff = np.asarray(affine, dtype=float).copy()
    aff[:3, 3] = aff[:3, 3] + aff[:3, :3] @ np.asarray(lo[::-1], dtype=float)
    return mask_to_mesh(sub, aff, smooth_iterations=smooth_iterations,
                        largest_only=True, step_size=step_size)


# -------------------------------------------------------------------- main
def _wall_mesh(res):
    meshes = res.meshes or {}
    for key in ("cavity", "body"):
        m = meshes.get(key)
        if m is not None and len(getattr(m, "faces", [])):
            return "gtcore:%s" % key, m
    return "none", None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ct_dir", help="DICOM series directory of the implant CT")
    ap.add_argument("--dataset", default="PT-CT1mm",
                    help="dataset id for file names and headers (PT-CT1mm, PH8, ...)")
    ap.add_argument("--rtstruct", default=None,
                    help="RTSTRUCT directory/file (Cav_Post mesh for the render data)")
    ap.add_argument("--cavity-roi", default="Cav_Post")
    ap.add_argument("--n-full", type=int, default=8)
    ap.add_argument("--halves", default=None, metavar="N_FULL,N_HALF",
                    help="also run the count-known fit with half tiles in the prior, "
                         "fit_tiles_prior(ImplantPrior(n_full=N_FULL, n_half=N_HALF), mesh), as the "
                         "additional mode count_<F>full_<H>half (e.g. 6,4 for the clinical case: the "
                         "eight leftovers are four 2-seed pairs); the chord-path partition with the "
                         "same counts is printed as a cross-check")
    ap.add_argument("--out", default=os.path.join("docs", "figures", "data"))
    ap.add_argument("--no-patches", action="store_true")
    ap.add_argument("--sheet-n", type=int, default=SHEET_N)
    args = ap.parse_args(argv)
    halves = None if args.halves is None else parse_halves(args.halves)

    from gtcore.io import load_volume
    from gtcore.pipeline import reconstruct

    print("gtcore from", os.path.dirname(gtcore.__file__))
    print("gtcore.strict_mode() =", gtcore.strict_mode())
    ds = args.dataset
    cmd = ('python scripts/leftover_seeds.py "$GT_DATA_ROOT/<%s>" --dataset %s%s '
           '--n-full %d%s --out %s' % (ds, ds,
                                       ' --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT>"' if args.rtstruct else "",
                                       args.n_full,
                                       "" if halves is None else " --halves %d,%d" % halves,
                                       args.out.replace(os.sep, "/")))
    out = args.out
    os.makedirs(out, exist_ok=True)
    stem = os.path.join(out, "f4_leftover_%%s_%s.csv" % ds)

    vol = load_volume(args.ct_dir)
    print("[%s] volume shape %s spacing %s" % (ds, vol.array.shape, np.round(vol.spacing, 3)))

    t0 = time.perf_counter()
    res_auto = reconstruct(vol, verbose=False, n_full_tiles="auto")
    t_auto = time.perf_counter() - t0
    seeds = res_auto.seeds
    centers, axes = np.asarray(seeds.centers_ras, float), np.asarray(seeds.axes_ras, float)
    print("[%s] reconstruct(auto): %d seeds (%d raw blobs), vault filter %s, %.1f s; "
          "tile fitting %.1f s" % (ds, len(seeds), len(res_auto.seeds_raw),
                                   vol.meta.get("vault_filter"), t_auto,
                                   res_auto.timings.get("tile fitting", float("nan"))))

    t0 = time.perf_counter()
    res_cnt = reconstruct(vol, verbose=False, n_full_tiles=args.n_full)
    t_cnt = time.perf_counter() - t0
    same = (len(res_cnt.seeds) == len(seeds)
            and np.allclose(res_cnt.seeds.centers_ras, centers))
    print("[%s] reconstruct(n_full_tiles=%d): %d seeds, identical to pass 1: %s; %.1f s"
          % (ds, args.n_full, len(res_cnt.seeds), same, t_cnt))
    if not same:
        print("  [warn] seed sets differ between passes; the count-known tiles are "
              "re-indexed onto the pass-1 seeds by position")

    wall_source, mesh = _wall_mesh(res_auto)
    cav = res_auto.cavity_mask
    cav_c = None
    if cav is not None and np.asarray(cav).any():
        cav_c = vol.index_to_ras(np.argwhere(cav).mean(axis=0)[::-1])
    print("[%s] wall mesh: %s (%d faces); cavity centre %s" % (
        ds, wall_source, 0 if mesh is None else len(mesh.faces),
        None if cav_c is None else np.round(cav_c, 1)))

    modes = run_modes(centers, axes, args.n_full, cavity_center_ras=cav_c, mesh=mesh,
                      spacing_mm=vol.spacing, auto_result=res_auto.tiles,
                      count_result=res_cnt.tiles if same else None)
    modes["auto"].seconds = res_auto.timings.get("tile fitting", float("nan"))
    if same:
        modes["count%d" % args.n_full].seconds = res_cnt.timings.get("tile fitting", float("nan"))
    chord_halves = None
    if halves is not None:
        hm = run_halves_mode(centers, axes, halves[0], halves[1], cavity_center_ras=cav_c,
                             mesh=mesh, spacing_mm=vol.spacing)
        modes[hm.name] = hm
        chord_halves = halves_chord_crosscheck(centers, axes, halves[0], halves[1],
                                               cavity_center_ras=cav_c)
    for m in modes.values():
        print("[%s] %s" % (ds, m.summary()))
        if hasattr(m.result, "summary"):
            print("      %s" % m.result.summary())
        if halves is not None and m.name == halves_mode_name(*halves):
            for p in m.tiles:
                print("      tile %d (%s%s): seeds %s, chord residual %.2f mm"
                      % (p.tile_id, p.kind, ", degraded" if getattr(p, "degraded", False) else "",
                         list(int(i) for i in p.seed_indices), float(p.residual_mm)))
    if chord_halves is not None:
        print("[%s] cross-check, %s: %d full + %d half tiles, %d degraded; leftovers %s"
              % (ds, chord_halves.label, sum(1 for p in chord_halves.tiles if p.kind == "full"),
                 sum(1 for p in chord_halves.tiles if p.kind == "half"), chord_halves.n_degraded,
                 chord_halves.leftovers))
        for p in chord_halves.tiles:
            print("      tile %d (%s%s): seeds %s, chord residual %.2f mm"
                  % (p.tile_id, p.kind, ", degraded" if getattr(p, "degraded", False) else "",
                     list(int(i) for i in p.seed_indices), float(p.residual_mm)))

    tree = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(gtcore.__file__))))
    hdr_extra = ["gtcore imported from tree: %s" % tree,
                 "strict_mode: %s" % gtcore.strict_mode(),
                 "n_seeds: %d; n_full requested: %d" % (len(seeds), args.n_full)]
    if halves is not None:
        hdr_extra.append("halves requested: %d full + %d half tiles (mode %s = fit_tiles_prior("
                         "ImplantPrior(n_full=%d, n_half=%d), mesh); the prior is an input, not a "
                         "fitted count; which count-known row the paper quotes waits for the operative record)"
                         % (halves[0], halves[1], halves_mode_name(*halves), halves[0], halves[1]))
    hdr = header(cmd, ds, hdr_extra)
    # ---- per-tile table, all modes
    trows = []
    for m in modes.values():
        trows.extend(tile_rows(m, centers, axes))
    write_csv(stem % "tiles", trows, hdr + [
        "one row per fitted tile and mode; bent_rms_mm from gtcore.tiles.deform.fit_deformable "
        "(attached by the fit, or computed here for chord-path tiles; rigid fallback for 2-seed half "
        "tiles); seed_indices into the pass-1 seed list; surface_* from the cavity-mesh cross-check "
        "when a mesh existed"])
    mrows = [dict(mode=m.name, label=m.label, result_type=type(m.result).__name__,
                  n_tiles=m.n_tiles, n_supported=m.n_supported, n_tentative=m.n_tentative,
                  n_degraded=m.n_degraded, n_assigned=len(m.assigned),
                  n_leftover=len(m.leftovers), leftovers=m.leftovers,
                  unassigned_in_implant=m.unassigned_in_implant, clutter=m.clutter,
                  summary=m.result.summary() if hasattr(m.result, "summary") else "",
                  seconds=m.seconds) for m in modes.values()]
    write_csv(stem % "modes", mrows, hdr + ["one row per fit mode"])

    # ---- leftovers of the primary (count-free) mode
    prim = modes["auto"]
    left = prim.leftovers
    print("[%s] leftovers (auto): %s" % (ds, left))
    dist, ang = pairwise_tables(centers, axes, left)
    write_csv(stem % "pairwise_mm", matrix_rows(left, dist, "d"), hdr + [
        "pairwise distances (mm) between the leftover seeds of the count-free fit"])
    write_csv(stem % "pairwise_axis_deg", matrix_rows(left, ang, "a"), hdr + [
        "pairwise angles (deg) between the PCA axes of the leftover seeds"])
    meshes = {"wall": mesh}
    cavity_rt = None
    if args.rtstruct:
        from gtcore.io import load_rtstruct, rasterize_roi

        rs = load_rtstruct(args.rtstruct)
        t0 = time.perf_counter()
        mask_rt = rasterize_roi(rs, args.cavity_roi, vol.affine, vol.array.shape)
        cavity_rt = mesh_from_mask(mask_rt, vol.affine, step_size=1)
        print("[%s] %s: %.1f cc on the CT grid -> mesh %d faces (%.1f s)" % (
            ds, args.cavity_roi, mask_rt.sum() * float(np.prod(vol.spacing)) / 1000.0,
            len(cavity_rt.faces), time.perf_counter() - t0))
        meshes["cav_post"] = cavity_rt
    srows = seed_rows(centers, axes, left, prim, seeds=seeds, vol=vol, meshes=meshes)
    write_csv(stem % "seeds", srows, hdr + [
        "one row per leftover seed of the count-free fit; nearest_tile = fitted tile whose seed "
        "sheet is closest (dist_sheet_mm); tile_u/v/h_mm = position in that tile's bent-tile "
        "frame (h > 0 toward the cavity); layer_class: sheet / stacked / edge / far (see "
        "layer_rows); dist_*_mm unsigned distance to the wall mesh / Cav_Post mesh; blob_* from "
        "detection (split_k > 1 = from a k-means split of a merged blob, n_fragments > 1 = "
        "rejoined fragments); peak_hu_1mm = max HU within 1 mm of the centre on the raw volume"])
    lrows = layer_rows(centers, axes, left, prim)
    write_csv(stem % "layers", lrows, hdr + [
        "every (leftover seed, fitted tile) pair of the count-free fit: u/v in-plane and h along "
        "the normal (+ toward the cavity) in the tile's bent-tile frame, distance to the tile's "
        "seed sheet, overhang beyond the 20 mm footprint, layer_class as in layer_rows()"])
    for cls in ("sheet", "stacked", "edge"):
        hits = [(r["seed"], r["tile"], round(r["h_mm"], 1)) for r in lrows if r["layer_class"] == cls]
        if hits:
            print("[%s] layer '%s': (seed, tile, h mm) %s" % (ds, cls, hits))
    tol = _auto.spacing_tolerance(vol.spacing)
    qrows = quad_gate_rows(centers, axes, left, tol=tol)
    write_csv(stem % "quads", qrows, hdr + [
        "every 4-combination of the leftover seeds; std_* = gtcore.tiles.fit quad gates, "
        "loose_* / cover_* = gtcore.tiles.auto tiers (cover scaled by tol %.2f), completion_* = "
        "count-constrained completion rule; def_* = bent-tile fit; gates_failed lists every "
        "failed gate" % tol])
    prows = pair_gate_rows(centers, axes, left)
    write_csv(stem % "pairs", prows, hdr + ["every leftover pair against the half-tile gates"])
    trip = triplet_rows(centers, axes, left, tol=tol)
    write_csv(stem % "triplets", trip, hdr + ["every leftover triplet against the cover pass's L-shape windows"])
    finding = cluster_finding(centers, left, qrows)
    bq = best_quad(qrows)
    fpath = os.path.join(out, "f4_leftover_finding_%s.json" % ds)
    def _mode_summary(m):
        return dict(n_tiles=m.n_tiles, n_degraded=m.n_degraded, n_tentative=m.n_tentative,
                    n_full=sum(1 for p in m.tiles if p.kind == "full"),
                    n_half=sum(1 for p in m.tiles if p.kind == "half"),
                    tiles=[dict(tile_id=int(p.tile_id), kind=p.kind,
                                degraded=bool(getattr(p, "degraded", False)),
                                seeds=[int(i) for i in p.seed_indices],
                                chord_residual_mm=float(p.residual_mm),
                                bent_rms_mm=float(m.deforms[p.tile_id].rms_mm)
                                if p.tile_id in m.deforms else None) for p in m.tiles],
                    assigned=m.assigned, leftovers=m.leftovers, seconds=m.seconds)

    with open(fpath, "w", encoding="utf-8") as fh:
        json.dump(dict(dataset=ds, command=cmd, commit=git_commit(),
                       modes={m.name: dict(n_tiles=m.n_tiles, n_degraded=m.n_degraded,
                                           n_tentative=m.n_tentative, leftovers=m.leftovers)
                              for m in modes.values()},
                       halves=(None if halves is None else dict(
                           n_full=halves[0], n_half=halves[1], mode=halves_mode_name(*halves),
                           prior_path=_mode_summary(modes[halves_mode_name(*halves)]),
                           chord_crosscheck=_mode_summary(chord_halves),
                           note="the prior (F full + H half) is an input; which count-known row the "
                                "paper quotes waits for the operative record")),
                       leftovers=left, best_quad=bq, cluster_finding=finding,
                       n_pairs_pass=sum(r["pair_pass"] for r in prows),
                       n_triplets_pass=sum(r["triplet_pass"] for r in trip)),
                  fh, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print("[%s] finding: %s" % (ds, finding["verdict"]))
    if bq is not None:
        print("[%s] best leftover quad %s: score %.2f (penalty %.1f), def rms %.2f mm, E %.3f, "
              "fold %.0f deg, axis err %.0f deg; rejected by standard %s, loose %s, cover %s, "
              "completion %s" % (ds, bq["seeds"], bq["def_score"], _auto.LAMBDA_FULL,
                                 bq["def_rms_mm"], bq["bending_energy"], bq["fold_deg"],
                                 bq["def_axis_err_deg"], bq["rejected_by"]["standard"],
                                 bq["rejected_by"]["loose"], bq["rejected_by"]["cover"],
                                 bq["rejected_by"]["completion"]))

    # ---- image patches
    if not args.no_patches and left:
        pdir = os.path.join(out, "leftover_patches")
        is_left = np.zeros(len(centers), dtype=bool)
        is_left[left] = True
        for i in left:
            p = write_patch(vol, centers[i], i, os.path.join(pdir, "%s_seed%02d.png" % (ds, i)),
                            other_centers=centers, other_leftover=is_left, dataset=ds)
        print("[%s] %d patches -> %s (windows %s, %d dpi)" % (
            ds, len(left), pdir, ["W %d / L %d" % (w, l) for w, l in PATCH_WINDOWS], PATCH_DPI))

    # ---- render export
    npz_name = {"PT-CT1mm": "f4b_patient_implant.npz",
                "PH8": "f4c_phantom_implant.npz"}.get(ds, "f4_implant_%s.npz" % ds)
    npz = export_implant(os.path.join(out, npz_name), ds, centers, axes, modes, primary="auto",
                         wall_mesh=mesh, wall_source=wall_source, cavity_rt_mesh=cavity_rt,
                         cavity_center_ras=cav_c, seeds=seeds, spacing_mm=vol.spacing,
                         shape_kji=vol.array.shape, command=cmd, n_full=args.n_full,
                         n=args.sheet_n, halves=halves,
                         notes=("count-free auto is the F4(b)/F4(c) primary (figure plan v6/v7); "
                                "count-known modes are exported alongside for Table 3 / S-figures."
                                + ("" if halves is None else
                                   " %s = count-known fit with %d full + %d half tiles in the prior "
                                   "(the leftover diagnostic's reading of the eight leftovers); the "
                                   "paper's count-known row awaits the operative record."
                                   % (halves_mode_name(*halves), halves[0], halves[1]))))
    print("[%s] render data -> %s (+ .json index)" % (ds, npz))
    return 0


if __name__ == "__main__":
    sys.exit(main())
