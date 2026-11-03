"""F8 / Table 3 -- the patient run: the implanted configuration vs the
optimizer's 8 tiles on the same post-operative cavity (Paper 2, Phase 4
campaign, step 4; requirements rev 3 section 3 F8(c), decisions log
2026-11-03 "F8(c) conditions" and "Phase 4 code decisions").

What it does (one clinical case, PT-CT1mm + PT-RT; every number is
calibration-set):

1. ``reconstruct(vol, n_full_tiles="auto")`` -> the 32 localized seeds, the
   gtcore cavity mask and its wall mesh (the mesh both arms are scored on).
2. The scoring structures of Table 3 (``validation_clinical.build_targets``,
   Phase 2 definitions, CT grid): ``RIND5(gtcore cavity, clip cranial
   interior)`` (the implant-independent F8 / Table 3 target),
   ``HRCTV_PHYS (CTV_GT)`` (the physician's hand-trimmed target), and
   ``IMPLANT_REACH(r = 10 mm)`` (the physician's partial-rind convention, F6
   only; ``RIND5(Cav_Post, clip Brain)`` and r = 12 mm alongside for
   completeness).  Every arm is scored with the ONE ``dose_stats`` on the exact
   TG-43 dose at the structure's voxel centres, S_K 3.5 U nominal.
3. The eligible wall for the optimizer: the craniotomy-side rule WITHOUT the
   implant-visibility term (``candidates.craniotomy_faces(mesh, opening,
   implant_center_ras=None)``: facing the opening within the 90 deg cone AND
   line of sight through the opening) -- the decisions log's F8(b) rule; the
   CLI's ``--eligible craniotomy`` adds the implant term, which would bias
   the optimizer toward where the implant already is, so this driver calls
   the library directly.  The fraction of implanted seeds on eligible wall is
   the fairness check.
4. Arms: (i) **as implanted** = the pipeline's 32 localized seeds with their
   physical (PCA) axes -- the dose is a property of the seeds, not of the
   tile fit; the two count-known tile fits (N = 8 full, and 6 full + 4 half
   -- ``scripts/leftover_seeds.py --halves 6,4``, decisions log "the eight
   leftover seeds are half tiles") are reported as the implant's geometry
   (tile count, overlaps, leftovers) and, for the N = 8 fit, the dose of the
   seeds its tiles own (leftovers excluded) as a sensitivity line;
   (ii) **optimized** = ``plan.optimize(mesh, 8, solver="sa")`` on the
   eligible wall, frozen objective (lambda_tail 1, TAIL_Q 0.10), SA 8 x 60,
   one run per ``--sa-seeds`` value (best and mean reported; best = highest
   RIND5 V100 on the gtcore rind, the campaign's ``sa_best`` rule).
5. Outputs: ``<out-csv>/f8_patient_arms.csv`` (arm x structure x metric),
   ``<out-csv>/f8_patient_summary.csv`` (one row per arm), and the render
   data ``<out-data>/f8b_patient_optimized.npz`` + ``.json`` (same array
   conventions as ``f4b_patient_implant.npz``: RAS mm, [k, j, i] arrays,
   shared ``sheet_faces``; plus the eligible-face mask and the opening).

Usage (worktree root, PYTHONPATH=<worktree>, GTCORE_STRICT=1)::

    python scripts/patient_optimize.py "$GT_DATA_ROOT/<PT-CT1mm>" \
        --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT>" --tiles 8 --sa-seeds 0 1 2 --halves 6,4 \
        --out-csv docs/figures/optimize/data --out-data docs/figures/data --reports <scratch>

No parameter is tuned here; the objective, the SA budget, S_K, the seed plane
and the eligibility constants are the frozen gtcore values.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import importlib.util
import json
import os
import subprocess
import sys
import time

import numpy as np
import trimesh

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gtcore  # noqa: E402
import gtcore.plan as plan  # noqa: E402
from gtcore import geometry as _geom  # noqa: E402
from gtcore.dose.engine import TG43Engine  # noqa: E402
from gtcore.dose.metrics import dose_stats  # noqa: E402
from gtcore.dose.targets import target_doses_exact  # noqa: E402
from gtcore.interact import (SEED_WALL_OFFSET_MM, _project_to_wall, _tangent_frame,  # noqa: E402
                             find_overlapping_tiles, tiles_to_seed_arrays)
from gtcore.io import load_volume  # noqa: E402
from gtcore.io.rt import load_rtstruct, rasterize_rois  # noqa: E402
from gtcore.pipeline import reconstruct  # noqa: E402
from gtcore.plan import candidates as cand_mod  # noqa: E402
from gtcore.planner import wall_mesh_for  # noqa: E402
from gtcore.tiles import ImplantPrior, fit_tiles, fit_tiles_prior  # noqa: E402
from gtcore.tiles.auto import to_placed_tiles  # noqa: E402

SK_U = 3.5                  # nominal S_K per seed, never fitted
RX_CGY = 6000.0
REACH_R_MM = 10.0           # F6 calibration (docs/paper2-decisions.md)
OVERLAP_MM = 1.0            # the planner's overlap rule (amber caution, never an error)
SHEET_N = 9                 # grid points per tile side in the render sheets (as f4b)
TILE_HALF_MM = 10.0
CAVITY_FACE_MM = SEED_WALL_OFFSET_MM + 1.0   # f4b convention: tissue face -3, seed sheet 0, cavity face +1
FLAG = "calibration-set"
DATASETS = "PT-CT1mm (post-op CT, 1 mm), PT-RT (RTSTRUCT: Cav_Post, CTV_GT, Brain)"
PRIMARY = ("RIND5(gtcore cavity, clip cranial interior)", "HRCTV_PHYS (CTV_GT)",
           "IMPLANT_REACH(r=10 mm)")
STAT_KEYS = ("D90", "V100", "V150", "V200", "Dmin", "Dmean", "Dmax", "D50", "volume_cc", "n")


# ------------------------------------------------------------------ helpers
def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:                                                 # noqa: BLE001
        return "unknown"


def load_script(name: str):
    """Import a sibling script as a module (its ``main`` is guarded)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def masked_argv(argv, flags=("--rtstruct", "--out-csv", "--out-data", "--reports")):
    out, skip = [], False
    for i, tok in enumerate(argv):
        if skip:
            out.append("<path>")
            skip = False
        elif tok in flags:
            out.append(tok)
            skip = True
        elif i == 0 and not tok.startswith("-"):
            out.append('"$GT_DATA_ROOT/<PT-CT1mm>"')
        else:
            out.append(tok)
    return out


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if not np.isfinite(v):
            return "nan"
        return "%.6g" % v
    if isinstance(v, (np.floating,)):
        return _fmt(float(v))
    if isinstance(v, (np.integer,)):
        return str(int(v))
    return str(v)


def write_csv(path, rows, columns, header_lines):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for ln in header_lines:
            fh.write("# " + ln + "\n")
        w = csv.writer(fh)
        w.writerow(columns)
        for r in rows:
            w.writerow([_fmt(r.get(c)) for c in columns])


def header(command, commit, what, columns, extra=()):
    lines = [
        "F8 / Table 3 patient run -- %s" % what,
        "script: scripts/patient_optimize.py",
        "command: " + command,
        "commit: " + commit,
        "date: " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "datasets: " + DATASETS,
        "flag: %s (one clinical case; reference, not a cohort claim)" % FLAG,
        "frozen: objective lambda_tail %g / TAIL_Q %g; SA %d x %d; S_K %.2f U nominal; rx %.0f cGy; "
        "seed plane %.2f mm; eligibility cone %g deg, footprint tol %g mm, view offset %g mm; "
        "candidate grid h %g mm / %d spins; M_opt %d"
        % (plan.LAMBDA_TAIL, plan.TAIL_Q, plan.SA_N_RESTARTS, plan.SA_N_SWEEPS, SK_U, RX_CGY,
           SEED_WALL_OFFSET_MM, cand_mod.CONE_DEG, cand_mod.FOOTPRINT_TOL_MM, cand_mod.VIEW_OFFSET_MM,
           plan.DEFAULT_H_MM, plan.DEFAULT_N_SPINS_FULL, plan.M_OPT_MAX),
        "scoring: exact TG-43 dose at the structure's CT-grid voxel centres, gtcore.dose.metrics.dose_stats "
        "(one convention); the optimizer report's own RIND5 (1 mm report grid) is carried as report_RIND5_*",
    ]
    lines += list(extra)
    lines.append("columns: " + ", ".join(columns))
    return lines


def cavity_centroid_ras(vol, mask):
    kji = np.argwhere(np.asarray(mask, bool))
    if kji.size == 0:
        return None
    return np.asarray(vol.index_to_ras(kji.mean(axis=0)[::-1]), float).reshape(3)


# --------------------------------------------------------------------- arms
def score(targets, centers, axes, eng):
    out = {}
    for label, t in targets:
        d = np.asarray(target_doses_exact(t, centers, axes, SK_U, engine=eng), float).reshape(-1)
        out[label] = dose_stats(d, RX_CGY, voxel_volume_mm3=t.voxel_volume_mm3)
    return out


def fit_geometry(fit, centers, axes, n_seeds):
    """Tile count / kinds / leftovers / planner overlaps of a tile fit."""
    poses = list(getattr(fit, "all_tiles", None) or fit.tiles)
    assigned = sorted({int(i) for p in poses for i in p.seed_indices if int(i) >= 0})
    left = [i for i in range(n_seeds) if i not in set(assigned)]
    tiles = to_placed_tiles(fit, centers, axes) if poses else []
    ov = find_overlapping_tiles(tiles, threshold_mm=OVERLAP_MM) if len(tiles) > 1 else []
    return {
        "poses": poses, "tiles": tiles, "assigned": assigned, "leftovers": left,
        "n_tiles": len(poses), "n_full": sum(1 for p in poses if p.kind == "full"),
        "n_half": sum(1 for p in poses if p.kind == "half"),
        "n_degraded": sum(1 for p in poses if bool(getattr(p, "degraded", False))),
        "n_overlaps": len(ov), "overlap_pairs": [(int(a), int(b)) for a, b in ov],
    }


def fairness(mesh, elig_mask, seed_centers, near_mm=6.0):
    """Implanted seeds on eligible wall: nearest face eligible; within
    ``near_mm`` of an eligible face."""
    _cp, _d, fid = trimesh.proximity.closest_point(mesh, seed_centers)
    on = np.asarray(elig_mask, bool)[np.asarray(fid, int)]
    sub = mesh.submesh([np.flatnonzero(elig_mask)], append=True) if elig_mask.any() else None
    if sub is not None and len(sub.faces):
        _cp2, d2, _f2 = trimesh.proximity.closest_point(sub, seed_centers)
        near = np.asarray(d2) <= near_mm
    else:
        d2 = np.full(len(seed_centers), np.nan)
        near = np.zeros(len(seed_centers), bool)
    return {"n_seeds": int(len(seed_centers)), "nearest_face_eligible": int(on.sum()),
            "fraction_nearest_face_eligible": float(on.mean()),
            "within_%gmm_of_eligible" % near_mm: int(near.sum()),
            "fraction_within_%gmm_of_eligible" % near_mm: float(near.mean()),
            "seed_to_eligible_wall_mm": [float(v) for v in np.asarray(d2)],
            "seed_nearest_face_eligible": [bool(v) for v in on]}


def tile_sheets(mesh, tiles, n=SHEET_N):
    """n x n render sheets of conformed tiles, draped the way the conformer
    drapes a tile (``interact._project_to_wall`` from the tile's anchor along
    its normal): the tissue face ON the wall, the seed sheet +3 mm and the
    cavity face +4 mm along the local inward normal.  Row-major (r * n + c),
    the faces are ``leftover_seeds.sheet_faces(n)``."""
    u = np.linspace(-TILE_HALF_MM, TILE_HALF_MM, n)
    uv_full = np.array([[a, b] for a in u for b in u], float)
    out = {"wall": [], "seed": [], "cavity": []}
    for t in tiles:
        nrm, t1, t2 = _tangent_frame(t.normal_ras, t.axis_ras)
        uv = uv_full.copy()
        if t.kind == "half":
            uv[:, 0] *= 0.5                       # a 10 x 20 mm strip along t2
        anchor = t.anchor_ras if t.anchor_ras is not None else t.center_ras - SEED_WALL_OFFSET_MM * nrm
        wall, loc_n = _project_to_wall(mesh, anchor, nrm, t1, t2, uv)
        out["wall"].append(wall)
        out["seed"].append(wall + SEED_WALL_OFFSET_MM * loc_n)
        out["cavity"].append(wall + CAVITY_FACE_MM * loc_n)
    return {k: (np.asarray(v, float) if v else np.zeros((0, n * n, 3))) for k, v in out.items()}


def tile_arrays(tiles):
    N = len(tiles)
    seeds = np.full((N, 4, 3), np.nan)
    saxes = np.full((N, 4, 3), np.nan)
    for i, t in enumerate(tiles):
        k = t.seed_centers.shape[0]
        seeds[i, :k] = t.seed_centers
        saxes[i, :k] = t.seed_axes
    return {
        "tile_kind": np.array([t.kind for t in tiles]) if N else np.zeros(0, dtype="<U4"),
        "tile_center_ras": np.asarray([t.center_ras for t in tiles], float).reshape(N, 3),
        "tile_normal_ras": np.asarray([t.normal_ras for t in tiles], float).reshape(N, 3),
        "tile_axis_ras": np.asarray([t.axis_ras for t in tiles], float).reshape(N, 3),
        "tile_anchor_ras": np.asarray([t.anchor_ras if t.anchor_ras is not None else t.center_ras
                                       for t in tiles], float).reshape(N, 3),
        "tile_corners_ras": np.asarray([t.corners_ras for t in tiles], float).reshape(N, 4, 3),
        "tile_seed_ras": seeds, "tile_seed_axes_ras": saxes,
    }


# --------------------------------------------------------------------- main
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("ct_dir", help="PT-CT1mm DICOM series directory")
    p.add_argument("--rtstruct", required=True, help="PT-RT RTSTRUCT directory/file (Cav_Post, CTV_GT, Brain)")
    p.add_argument("--tiles", type=int, default=8, help="N full tiles for the optimizer (default 8)")
    p.add_argument("--sa-seeds", type=int, nargs="+", default=(0, 1, 2), dest="sa_seeds")
    p.add_argument("--halves", default="6,4", help="count-known fit with half tiles, F,H (default 6,4)")
    p.add_argument("--count-known", type=int, default=8, dest="count_known",
                   help="count-known N full fit (pipeline chord path, default 8)")
    p.add_argument("--out-csv", default=os.path.join("docs", "figures", "optimize", "data"), dest="out_csv")
    p.add_argument("--out-data", default=os.path.join("docs", "figures", "data"), dest="out_data")
    p.add_argument("--reports", default=None, help="scratch folder for the optimizer report JSONs")
    p.add_argument("--rx", type=float, default=RX_CGY)
    a = p.parse_args(argv)
    argv_in = sys.argv[1:] if argv is None else list(argv)
    command = "python scripts/patient_optimize.py " + " ".join(masked_argv(argv_in))
    commit = git_commit()
    print("gtcore.__file__ =", gtcore.__file__)
    print("commit", commit)
    if (plan.LAMBDA_TAIL, plan.TAIL_Q, plan.SA_N_RESTARTS, plan.SA_N_SWEEPS) != (1.0, 0.10, 8, 60):
        raise SystemExit("frozen Phase 4 constants changed: %r" % ((plan.LAMBDA_TAIL, plan.TAIL_Q,
                                                                   plan.SA_N_RESTARTS, plan.SA_N_SWEEPS),))
    rx = float(a.rx)
    t_all = time.perf_counter()
    if a.reports:
        os.makedirs(a.reports, exist_ok=True)
    vc = load_script("validation_clinical")
    ls = load_script("leftover_seeds")

    # 1. pipeline ----------------------------------------------------------
    vol = load_volume(a.ct_dir)
    print("volume %s @ %s mm" % (vol.array.shape, tuple(round(float(s), 3) for s in vol.spacing)))
    t0 = time.perf_counter()
    res = reconstruct(vol, verbose=False, n_full_tiles="auto", n_half_tiles=0)
    t_rec = time.perf_counter() - t0
    centers = np.asarray(res.seeds.centers_ras, float)
    axes = np.asarray(res.seeds.axes_ras, float)
    n_seeds = int(centers.shape[0])
    mesh, label = wall_mesh_for(res)
    if label != "cavity wall" or mesh is None:
        raise SystemExit("no gtcore cavity wall on this scan (wall = %s)" % label)
    aff = np.asarray(vol.affine, float)
    cav_center = cavity_centroid_ras(vol, res.cavity_mask)
    vox_ml = float(np.prod(vol.spacing)) / 1000.0
    print("reconstruct %.1f s: %d seeds, gtcore cavity %.1f cc, wall %d faces %.0f mm2"
          % (t_rec, n_seeds, res.cavity_mask.sum() * vox_ml, len(mesh.faces), mesh.area))

    # 2. structures --------------------------------------------------------
    rs = load_rtstruct(a.rtstruct)
    names = [n for n in ("Cav_Post", "CTV_GT", "Brain") if n in rs.roi_names]
    t0 = time.perf_counter()
    masks_rt = rasterize_rois(rs, names, vol)
    targets, _m = vc.build_targets(vol, rs, res, centers, masks=masks_rt)
    t_targets = time.perf_counter() - t0
    tdict = dict(targets)
    for lab in PRIMARY:
        if lab not in tdict:
            raise SystemExit("structure %r missing from build_targets (have %r)" % (lab, list(tdict)))
    print("structures (%.1f s):" % t_targets)
    for lab, t in targets:
        print("  %-46s %7.2f cc  %s" % (lab, t.volume_cc, t.describe()))
    eng = TG43Engine()

    # 3. eligibility ---------------------------------------------------------
    t0 = time.perf_counter()
    opening = cand_mod.craniotomy_opening(res.masks, aff, cavity_mask=res.cavity_mask)
    t_open = time.perf_counter() - t0
    print(opening.describe())
    t0 = time.perf_counter()
    elig = cand_mod.craniotomy_faces(mesh, opening, implant_center_ras=None)
    t_elig = time.perf_counter() - t0
    print("eligible (facing + line of sight, no implant term): %d of %d faces, %.1f %% of the area (%.1f s)"
          % (elig.n_eligible, elig.mask.size, 100 * elig.fraction, t_elig))
    elig3 = cand_mod.craniotomy_faces(mesh, opening, implant_center_ras=centers.mean(axis=0))
    print("  (with the implant-visibility term, the CLI's --eligible craniotomy: %d faces, %.1f %%; not used)"
          % (elig3.n_eligible, 100 * elig3.fraction))
    fair = fairness(mesh, elig.mask, centers)
    fair3 = fairness(mesh, elig3.mask, centers)
    print("fairness: %d/%d implanted seeds have their nearest wall face eligible; %d/%d within 6 mm of "
          "eligible wall" % (fair["nearest_face_eligible"], n_seeds, fair["within_6mm_of_eligible"], n_seeds))
    elig_area = float(np.asarray(mesh.area_faces)[elig.mask].sum())
    rec = plan.recommend_tile_count(mesh, eligible_faces=elig.mask)
    print("manufacturer rule on the eligible area (%.0f mm2): %d tiles" % (elig_area, rec.n_tiles))

    # 4. arms ------------------------------------------------------------------
    arms = []

    def add_arm(name, label_txt, cent, ax, geom=None, sa_seed=None, rep=None, solve_s=None,
                total_s=None, note=""):
        t0 = time.perf_counter()
        st = score(targets, cent, ax, eng)
        row = {"arm": name, "label": label_txt, "sa_seed": sa_seed, "stats": st,
               "n_seeds_scored": int(np.asarray(cent).shape[0]), "geom": geom, "report": rep,
               "solve_s": solve_s, "total_s": total_s, "note": note, "score_s": time.perf_counter() - t0,
               "centers": np.asarray(cent, float), "axes": np.asarray(ax, float)}
        arms.append(row)
        r5 = st[PRIMARY[0]]
        ctv = st[PRIMARY[1]]
        rc = st[PRIMARY[2]]
        print("  %-34s RIND5(gtcore) V100 %.3f D90 %5.0f | CTV_GT V100 %.3f D90 %5.0f | REACH10 V100 %.3f "
              "D90 %5.0f | seeds %d%s" % (name, r5["V100"], r5["D90"], ctv["V100"], ctv["D90"], rc["V100"],
                                          rc["D90"], row["n_seeds_scored"],
                                          (" | %d tiles, %d overlaps" % (geom["n_tiles"], geom["n_overlaps"])
                                           if geom else "")))
        return row

    print("arms:")
    # (i) as implanted: the 32 localized seeds, physical axes
    add_arm("as_implanted", "as implanted: %d localized seeds, physical (PCA) axes" % n_seeds, centers, axes,
            note="the dose of the implant as found; independent of the tile fit")
    # the implant's geometry: count-known N full (pipeline chord path, as reconstruct(n_full_tiles=N))
    t0 = time.perf_counter()
    fit8 = fit_tiles(centers, axes, int(a.count_known), 0, cavity_center_ras=cav_center, complete_degraded=True)
    g8 = fit_geometry(fit8, centers, axes, n_seeds)
    g8["fit_s"] = time.perf_counter() - t0
    sub = np.asarray(g8["assigned"], int)
    add_arm("as_implanted_fit_count%d" % a.count_known,
            "as implanted, count-known N = %d fit (pipeline chord path): the seeds its tiles own"
            % a.count_known, centers[sub], axes[sub], geom=g8,
            note="%d tiles (%d degraded), %d leftover seeds excluded from this dose row: %s"
                 % (g8["n_tiles"], g8["n_degraded"], len(g8["leftovers"]), g8["leftovers"]))
    # 6 full + 4 half (planner prior path with the mesh), leftover_seeds.run_halves_mode
    nf, nh = (int(v) for v in a.halves.split(","))
    t0 = time.perf_counter()
    fit64 = fit_tiles_prior(centers, axes, ImplantPrior(n_full=nf, n_half=nh), cavity_center_ras=cav_center,
                            mesh=mesh, spacing_mm=vol.spacing, complete_degraded=True)
    g64 = fit_geometry(fit64, centers, axes, n_seeds)
    g64["fit_s"] = time.perf_counter() - t0
    sub = np.asarray(g64["assigned"], int)
    add_arm("as_implanted_fit_%dfull_%dhalf" % (nf, nh),
            "as implanted, count-known %d full + %d half fit (planner prior path): the seeds its tiles own"
            % (nf, nh), centers[sub], axes[sub], geom=g64,
            note="%d tiles (%d full, %d half, %d degraded), %d leftover seeds: %s"
                 % (g64["n_tiles"], g64["n_full"], g64["n_half"], g64["n_degraded"], len(g64["leftovers"]),
                    g64["leftovers"]))

    # (ii) optimized: N full tiles on the eligible wall, SA, several seeds
    rind_kw = dict(cavity_mask=res.cavity_mask, cavity_affine=aff, clip_mask=res.masks["cranial_interior"],
                   clip_affine=aff, cavity_source="gtcore:cavity", clip_source="gtcore:cranial_interior")
    opt_rows = []
    for s in a.sa_seeds:
        lines = []
        t0 = time.perf_counter()
        tiles, rep = plan.optimize(mesh, int(a.tiles), 0, rx_cgy=rx, solver="sa", seed=int(s), eligible=elig,
                                   report=True, verbose=False, log=lines.append, **rind_kw)
        tot = time.perf_counter() - t0
        if a.reports:
            rep.to_json(os.path.join(a.reports, "f8_optimize_sa_seed%d_report.json" % s))
            with open(os.path.join(a.reports, "f8_optimize_sa_seed%d_log.txt" % s), "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
        c_o, a_o = tiles_to_seed_arrays(tiles)
        geom = {"poses": None, "tiles": list(tiles), "assigned": list(range(c_o.shape[0])), "leftovers": [],
                "n_tiles": len(tiles), "n_full": sum(1 for t in tiles if t.kind == "full"),
                "n_half": sum(1 for t in tiles if t.kind == "half"), "n_degraded": 0,
                "n_overlaps": len(rep.overlaps), "overlap_pairs": [(int(i), int(j)) for i, j in rep.overlaps]}
        row = add_arm("optimized_sa_seed%d" % s, "optimized: %d full tiles, SA %d x %d, seed %d, eligible = "
                      "craniotomy-side (facing + line of sight)" % (a.tiles, plan.SA_N_RESTARTS,
                                                                      plan.SA_N_SWEEPS, s),
                      c_o, a_o, geom=geom, sa_seed=int(s), rep=rep,
                      solve_s=float(rep.runtime.get("solver", float("nan"))), total_s=tot,
                      note="plan.optimize stages (s): " + ", ".join("%s %.1f" % (k, v) for k, v in rep.runtime.items()))
        row["objective"] = float(rep.solver.objective) if rep.solver is not None else float("nan")
        row["solver_status"] = getattr(rep.solver, "status", None)
        row["n_candidates"] = (rep.candidate_stats or {}).get("n_candidates")
        opt_rows.append(row)
    key0 = PRIMARY[0]
    best = max(opt_rows, key=lambda r: (r["stats"][key0]["V100"], r["stats"][key0]["D90"]))
    best_obj = max(opt_rows, key=lambda r: r["objective"])
    print("best SA seed by RIND5(gtcore) V100: %d (objective-best seed: %d)" % (best["sa_seed"], best_obj["sa_seed"]))

    # 5. tables -----------------------------------------------------------------
    arm_cols = ["arm", "sa_seed", "structure", "cavity_source", "clip_source", "volume_cc", "n_voxels",
                "D90_cGy", "V100", "V150", "V200", "Dmin_cGy", "Dmean_cGy", "Dmax_cGy", "n_seeds_scored",
                "n_tiles", "n_full", "n_half", "n_degraded", "n_leftover", "n_overlaps", "solve_s",
                "optimize_total_s", "report_RIND5_V100", "report_RIND5_D90_cGy", "report_RIND5_volume_cc",
                "flag", "note"]
    arm_rows = []
    for r in arms:
        g = r["geom"] or {}
        rep = r["report"]
        rr5 = (rep.metrics_grid or {}).get("RIND5", {}) if rep is not None else {}
        for lab, t in targets:
            st = r["stats"][lab]
            prov = t.provenance or {}
            arm_rows.append({
                "arm": r["arm"], "sa_seed": r["sa_seed"], "structure": lab,
                "cavity_source": prov.get("cavity_source"), "clip_source": prov.get("clip_source"),
                "volume_cc": st.get("volume_cc", t.volume_cc), "n_voxels": st.get("n", t.n),
                "D90_cGy": st.get("D90"), "V100": st.get("V100"), "V150": st.get("V150"), "V200": st.get("V200"),
                "Dmin_cGy": st.get("Dmin"), "Dmean_cGy": st.get("Dmean"), "Dmax_cGy": st.get("Dmax"),
                "n_seeds_scored": r["n_seeds_scored"], "n_tiles": g.get("n_tiles"), "n_full": g.get("n_full"),
                "n_half": g.get("n_half"), "n_degraded": g.get("n_degraded"),
                "n_leftover": (len(g["leftovers"]) if g else None), "n_overlaps": g.get("n_overlaps"),
                "solve_s": r["solve_s"], "optimize_total_s": r["total_s"],
                "report_RIND5_V100": rr5.get("V100"), "report_RIND5_D90_cGy": rr5.get("D90"),
                "report_RIND5_volume_cc": rr5.get("volume_cc"), "flag": FLAG, "note": r["note"]})
    # mean over the SA seeds, per structure
    for lab, t in targets:
        vals = {k: np.mean([r["stats"][lab][k] for r in opt_rows]) for k in ("D90", "V100", "V150", "V200")}
        sds = {k: np.std([r["stats"][lab][k] for r in opt_rows], ddof=1) if len(opt_rows) > 1 else 0.0
               for k in ("D90", "V100")}
        prov = t.provenance or {}
        arm_rows.append({
            "arm": "optimized_sa_mean", "sa_seed": None, "structure": lab,
            "cavity_source": prov.get("cavity_source"), "clip_source": prov.get("clip_source"),
            "volume_cc": t.volume_cc, "n_voxels": t.n, "D90_cGy": vals["D90"], "V100": vals["V100"],
            "V150": vals["V150"], "V200": vals["V200"], "n_seeds_scored": opt_rows[0]["n_seeds_scored"],
            "n_tiles": a.tiles, "n_full": a.tiles, "n_half": 0, "n_degraded": 0, "n_leftover": 0,
            "n_overlaps": float(np.mean([r["geom"]["n_overlaps"] for r in opt_rows])),
            "solve_s": float(np.mean([r["solve_s"] for r in opt_rows])),
            "optimize_total_s": float(np.mean([r["total_s"] for r in opt_rows])), "flag": FLAG,
            "note": "mean over SA seeds %s; SD V100 %.4f, D90 %.0f cGy" % (list(a.sa_seeds), sds["V100"], sds["D90"])})
    for lab, t in targets:
        src = dict(next(r for r in arm_rows if r["arm"] == best["arm"] and r["structure"] == lab))
        src["arm"] = "optimized_sa_best"
        src["note"] = "best of the SA seeds by RIND5(gtcore cavity) V100 (campaign sa_best rule) = seed %d" % best["sa_seed"]
        arm_rows.append(src)
    hdr_extra = [
        "eligible wall: craniotomy-side rule WITHOUT the implant-visibility term (facing within %g deg of the "
        "view point AND line of sight through the opening): %d of %d faces, %.1f %% of the wall area "
        "(%.0f mm2 of %.0f mm2); with the implant term (CLI --eligible craniotomy, not used): %.1f %%"
        % (cand_mod.CONE_DEG, elig.n_eligible, elig.mask.size, 100 * elig.fraction, elig_area, mesh.area,
           100 * elig3.fraction),
        "opening: %s" % json.dumps(opening.summary(), default=str),
        "fairness: %d/%d implanted seeds have their nearest wall face eligible (%.2f); %d/%d within 6 mm of "
        "eligible wall (%.2f)" % (fair["nearest_face_eligible"], n_seeds, fair["fraction_nearest_face_eligible"],
                                   fair["within_6mm_of_eligible"], n_seeds, fair["fraction_within_6mm_of_eligible"]),
        "as implanted = the pipeline's %d localized seeds (dose independent of the tile fit); the two fits "
        "describe the implant's geometry; the fit rows score only the seeds the fitted tiles own" % n_seeds,
        "optimized = plan.optimize(mesh, %d, solver='sa') on the eligible wall; one row per SA seed; "
        "optimized_sa_mean / optimized_sa_best are derived rows" % a.tiles,
        "overlaps = planner rule (find_overlapping_tiles, %g mm): an amber caution, never an error" % OVERLAP_MM,
    ]
    os.makedirs(a.out_csv, exist_ok=True)
    arms_csv = os.path.join(a.out_csv, "f8_patient_arms.csv")
    write_csv(arms_csv, arm_rows, arm_cols, header(command, commit, "arm x structure x metric", arm_cols, hdr_extra))

    # summary: one row per arm, the three primary structures side by side
    sum_cols = ["arm", "sa_seed", "n_seeds_scored", "n_tiles", "n_full", "n_half", "n_degraded", "n_leftover",
                "n_overlaps", "solve_s", "optimize_total_s", "objective", "solver_status", "n_candidates",
                "RIND5_gtcore_V100", "RIND5_gtcore_D90_cGy", "RIND5_gtcore_V150", "RIND5_gtcore_volume_cc",
                "CTV_GT_V100", "CTV_GT_D90_cGy", "CTV_GT_V150", "CTV_GT_volume_cc",
                "REACH10_V100", "REACH10_D90_cGy", "REACH10_volume_cc",
                "RIND5_CavPost_V100", "RIND5_CavPost_D90_cGy", "report_RIND5_V100", "report_RIND5_D90_cGy",
                "eligible_fraction_area", "seeds_nearest_face_eligible", "seeds_within_6mm_eligible", "flag", "note"]
    lab_cp = vc.ROW_RIND_TPS
    sum_rows = []

    def _sum_row(r, name=None, note=None):
        st = r["stats"]
        g = r["geom"] or {}
        rep = r["report"]
        rr5 = (rep.metrics_grid or {}).get("RIND5", {}) if rep is not None else {}
        return {"arm": name or r["arm"], "sa_seed": r["sa_seed"], "n_seeds_scored": r["n_seeds_scored"],
                "n_tiles": g.get("n_tiles"), "n_full": g.get("n_full"), "n_half": g.get("n_half"),
                "n_degraded": g.get("n_degraded"), "n_leftover": (len(g["leftovers"]) if g else None),
                "n_overlaps": g.get("n_overlaps"), "solve_s": r["solve_s"], "optimize_total_s": r["total_s"],
                "objective": r.get("objective"), "solver_status": r.get("solver_status"),
                "n_candidates": r.get("n_candidates"),
                "RIND5_gtcore_V100": st[PRIMARY[0]]["V100"], "RIND5_gtcore_D90_cGy": st[PRIMARY[0]]["D90"],
                "RIND5_gtcore_V150": st[PRIMARY[0]]["V150"], "RIND5_gtcore_volume_cc": st[PRIMARY[0]]["volume_cc"],
                "CTV_GT_V100": st[PRIMARY[1]]["V100"], "CTV_GT_D90_cGy": st[PRIMARY[1]]["D90"],
                "CTV_GT_V150": st[PRIMARY[1]]["V150"], "CTV_GT_volume_cc": st[PRIMARY[1]]["volume_cc"],
                "REACH10_V100": st[PRIMARY[2]]["V100"], "REACH10_D90_cGy": st[PRIMARY[2]]["D90"],
                "REACH10_volume_cc": st[PRIMARY[2]]["volume_cc"],
                "RIND5_CavPost_V100": st.get(lab_cp, {}).get("V100"), "RIND5_CavPost_D90_cGy": st.get(lab_cp, {}).get("D90"),
                "report_RIND5_V100": rr5.get("V100"), "report_RIND5_D90_cGy": rr5.get("D90"),
                "eligible_fraction_area": elig.fraction,
                "seeds_nearest_face_eligible": fair["nearest_face_eligible"],
                "seeds_within_6mm_eligible": fair["within_6mm_of_eligible"], "flag": FLAG,
                "note": note if note is not None else r["note"]}
    for r in arms:
        sum_rows.append(_sum_row(r))
    mean_row = {k: None for k in sum_cols}
    mean_row.update(arm="optimized_sa_mean", n_seeds_scored=opt_rows[0]["n_seeds_scored"], n_tiles=a.tiles,
                    n_full=a.tiles, n_half=0, n_degraded=0, n_leftover=0,
                    n_overlaps=float(np.mean([r["geom"]["n_overlaps"] for r in opt_rows])),
                    solve_s=float(np.mean([r["solve_s"] for r in opt_rows])),
                    optimize_total_s=float(np.mean([r["total_s"] for r in opt_rows])),
                    objective=float(np.mean([r["objective"] for r in opt_rows])),
                    eligible_fraction_area=elig.fraction, seeds_nearest_face_eligible=fair["nearest_face_eligible"],
                    seeds_within_6mm_eligible=fair["within_6mm_of_eligible"], flag=FLAG,
                    note="mean over SA seeds %s" % list(a.sa_seeds))
    for col, lab, k in (("RIND5_gtcore_V100", PRIMARY[0], "V100"), ("RIND5_gtcore_D90_cGy", PRIMARY[0], "D90"),
                        ("RIND5_gtcore_V150", PRIMARY[0], "V150"), ("CTV_GT_V100", PRIMARY[1], "V100"),
                        ("CTV_GT_D90_cGy", PRIMARY[1], "D90"), ("CTV_GT_V150", PRIMARY[1], "V150"),
                        ("REACH10_V100", PRIMARY[2], "V100"), ("REACH10_D90_cGy", PRIMARY[2], "D90"),
                        ("RIND5_CavPost_V100", lab_cp, "V100"), ("RIND5_CavPost_D90_cGy", lab_cp, "D90")):
        mean_row[col] = float(np.mean([r["stats"][lab][k] for r in opt_rows]))
    for col, lab in (("RIND5_gtcore_volume_cc", PRIMARY[0]), ("CTV_GT_volume_cc", PRIMARY[1]),
                     ("REACH10_volume_cc", PRIMARY[2])):
        mean_row[col] = tdict[lab].volume_cc
    sum_rows.append(mean_row)
    sum_rows.append(_sum_row(best, name="optimized_sa_best",
                             note="best SA seed by RIND5(gtcore cavity) V100 = seed %d" % best["sa_seed"]))
    summary_csv = os.path.join(a.out_csv, "f8_patient_summary.csv")
    write_csv(summary_csv, sum_rows, sum_cols, header(command, commit, "one row per arm, the Table 3 structures side "
                                                       "by side", sum_cols, hdr_extra))

    # 6. render data (F8b) ---------------------------------------------------------
    os.makedirs(a.out_data, exist_ok=True)
    npz_path = os.path.join(a.out_data, "f8b_patient_optimized.npz")
    tiles_b = best["geom"]["tiles"]
    sheets = tile_sheets(mesh, tiles_b, SHEET_N)
    tarr = tile_arrays(tiles_b)
    cav_rt = ls.mesh_from_mask(masks_rt["Cav_Post"], aff) if "Cav_Post" in masks_rt else None
    arrays = {
        "seed_centers_ras": centers, "seed_axes_ras": axes,
        "seed_length_mm": np.array(_geom.SEED_LENGTH_MM), "seed_diameter_mm": np.array(_geom.SEED_DIAMETER_MM),
        "cavity_center_ras": (np.full(3, np.nan) if cav_center is None else np.asarray(cav_center, float)),
        "volume_spacing_mm": np.asarray(vol.spacing, float),
        "volume_shape_kji": np.asarray(vol.array.shape, np.int64),
        "sheet_faces": ls.sheet_faces(SHEET_N), "sheet_grid_n": np.array(SHEET_N),
        "wall_mesh_source": np.array("gtcore:cavity"),
        "wall_mesh_vertices": np.asarray(mesh.vertices, float), "wall_mesh_faces": np.asarray(mesh.faces, np.int32),
        "eligible_face_mask": np.asarray(elig.mask, bool),
        "eligible_face_mask_with_implant_term": np.asarray(elig3.mask, bool),
        "eligible_rule": np.array("craniotomy: facing (cone %g deg) AND line of sight through the opening; "
                                  "no implant-visibility term" % cand_mod.CONE_DEG),
        "eligible_fraction_area": np.array(float(elig.fraction)),
        "opening_centroid_ras": np.asarray(opening.centroid_ras, float),
        "opening_normal_ras": np.asarray(opening.normal_ras, float),
        "opening_points_ras": np.asarray(opening.points_ras, float),
        "opening_area_mm2": np.array(float(opening.area_mm2)),
        "view_point_ras": np.asarray(opening.view_point(), float),
        "opt_sa_seed": np.array(int(best["sa_seed"])),
        "opt_tile_kind": tarr["tile_kind"], "opt_tile_center_ras": tarr["tile_center_ras"],
        "opt_tile_normal_ras": tarr["tile_normal_ras"], "opt_tile_axis_ras": tarr["tile_axis_ras"],
        "opt_tile_anchor_ras": tarr["tile_anchor_ras"], "opt_tile_corners_ras": tarr["tile_corners_ras"],
        "opt_tile_seed_ras": tarr["tile_seed_ras"], "opt_tile_seed_axes_ras": tarr["tile_seed_axes_ras"],
        "opt_seed_centers_ras": best["centers"], "opt_seed_axes_ras": best["axes"],
        "opt_sheet_wall_vertices": sheets["wall"], "opt_sheet_seed_vertices": sheets["seed"],
        "opt_sheet_cavity_vertices": sheets["cavity"],
        "implant_seed_nearest_face_eligible": np.asarray(fair["seed_nearest_face_eligible"], bool),
        "implant_seed_to_eligible_wall_mm": np.asarray(fair["seed_to_eligible_wall_mm"], float),
    }
    if cav_rt is not None:
        arrays["cavity_rt_vertices"] = np.asarray(cav_rt.vertices, float)
        arrays["cavity_rt_faces"] = np.asarray(cav_rt.faces, np.int32)
    # the implant's fits for F8(a), same conventions (sheets draped the conformer's way)
    for name, g in (("count%d" % a.count_known, g8), ("count_%dfull_%dhalf" % (nf, nh), g64)):
        ta = tile_arrays(g["tiles"])
        sh = tile_sheets(mesh, g["tiles"], SHEET_N)
        for k, v in ta.items():
            arrays["%s_%s" % (name, k)] = v
        arrays["%s_sheet_wall_vertices" % name] = sh["wall"]
        arrays["%s_sheet_seed_vertices" % name] = sh["seed"]
        arrays["%s_sheet_cavity_vertices" % name] = sh["cavity"]
        sid = np.full(n_seeds, -1, np.int32)
        for ti, pose in enumerate(g["poses"]):
            for i in pose.seed_indices:
                if int(i) >= 0:
                    sid[int(i)] = ti
        arrays["%s_seed_tile_id" % name] = sid
        arrays["%s_seed_leftover" % name] = sid < 0
        arrays["%s_tile_degraded" % name] = np.asarray([bool(getattr(p, "degraded", False)) for p in g["poses"]], bool)
    arrays["readme"] = np.array("PT-CT1mm optimizer render data (Paper 2 F8(b)): the best SA seed's %d tiles on the "
                                "craniotomy-side eligible wall of the gtcore cavity; see the JSON index for every "
                                "array. F8(a) uses f4b_patient_implant.npz (same mesh, same camera)." % a.tiles)
    np.savez_compressed(npz_path, **arrays)
    docs = {
        "seed_centers_ras": ("mm", "the 32 localized seed centres (pipeline, vault-filtered), RAS"),
        "seed_axes_ras": ("unit", "per-seed PCA long axis (sign arbitrary), RAS"),
        "seed_length_mm": ("mm", "capsule length to draw"), "seed_diameter_mm": ("mm", "capsule diameter"),
        "cavity_center_ras": ("mm", "centroid of the gtcore cavity mask"),
        "volume_spacing_mm": ("mm", "voxel spacing (si, sj, sk)"), "volume_shape_kji": ("voxels", "CT shape [k, j, i]"),
        "sheet_faces": ("index", "triangles shared by every *_sheet_*_vertices array (row-major n x n grid)"),
        "sheet_grid_n": ("-", "grid points per tile side"),
        "wall_mesh_source": ("-", "gtcore:cavity"), "wall_mesh_vertices": ("mm", "the gtcore cavity wall (both arms)"),
        "wall_mesh_faces": ("index", "triangles of wall_mesh_vertices"),
        "eligible_face_mask": ("bool", "per wall face: eligible for the optimizer (facing + line of sight; no implant term)"),
        "eligible_face_mask_with_implant_term": ("bool", "the CLI's --eligible craniotomy mask (adds visible-from-implant); NOT used"),
        "eligible_rule": ("-", "the rule"), "eligible_fraction_area": ("-", "eligible share of the wall area"),
        "opening_centroid_ras": ("mm", "craniotomy opening centroid"), "opening_normal_ras": ("unit", "outward normal"),
        "opening_points_ras": ("mm", "opening voxel centres (skull defect + cavity mouth component)"),
        "opening_area_mm2": ("mm^2", "opening area"), "view_point_ras": ("mm", "the surgeon's view point (centroid + 10 mm out)"),
        "opt_sa_seed": ("-", "the SA seed of the exported configuration (best RIND5 V100)"),
        "opt_tile_kind": ("-", "'full'"), "opt_tile_center_ras": ("mm", "centroid of the conformed seed centres"),
        "opt_tile_normal_ras": ("unit", "wall normal at the anchor, INTO the cavity"),
        "opt_tile_axis_ras": ("unit", "in-plane t1 (seed axes direction)"), "opt_tile_anchor_ras": ("mm", "wall anchor (on the mesh)"),
        "opt_tile_corners_ras": ("mm", "four corners at the seed plane (3 mm off the wall), loop order"),
        "opt_tile_seed_ras": ("mm", "the 4 conformed seed centres per tile"), "opt_tile_seed_axes_ras": ("unit", "their axes"),
        "opt_seed_centers_ras": ("mm", "all optimized seeds, flat (tiles_to_seed_arrays order)"),
        "opt_seed_axes_ras": ("unit", "their axes"),
        "opt_sheet_wall_vertices": ("mm", "n x n tissue face ON the wall (interact._project_to_wall from the anchor)"),
        "opt_sheet_seed_vertices": ("mm", "the same sheet +3 mm along the local inward normal (seed plane)"),
        "opt_sheet_cavity_vertices": ("mm", "the same sheet +4 mm (cavity face)"),
        "implant_seed_nearest_face_eligible": ("bool", "fairness: implanted seed's nearest wall face is eligible"),
        "implant_seed_to_eligible_wall_mm": ("mm", "fairness: distance from each implanted seed to the eligible wall"),
        "cavity_rt_vertices": ("mm", "physician's Cav_Post (RTSTRUCT rasterised, marching cubes)"),
        "cavity_rt_faces": ("index", "triangles of cavity_rt_vertices"),
        "readme": ("-", "summary"),
    }
    index = {"dataset": "PT-CT1mm", "script": "scripts/patient_optimize.py", "command": command, "commit": commit,
             "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "gtcore_file": gtcore.__file__.replace(ROOT, "<worktree>"), "flag": FLAG,
             "conventions": {"coordinates": "RAS mm; arrays indexed [k, j, i]; affine maps (i, j, k, 1) -> RAS",
                             "sheets": "wall = the tile's tissue face draped onto the gtcore cavity wall the way "
                                       "the conformer drapes it (flat offsets from the anchor cast along the anchor "
                                       "normal); seed = +3 mm, cavity = +4 mm along the local inward normal; "
                                       "row-major n x n, faces shared (sheet_faces)",
                             "implant_fits": "count<N>_* = count-known N full fit (pipeline chord path); "
                                             "count_<F>full_<H>half_* = planner prior fit; seed_tile_id -1 = leftover"},
             "eligible": elig.info, "opening": opening.summary(), "fairness": {k: v for k, v in fair.items()
                                                                               if not isinstance(v, list)},
             "fairness_with_implant_term": {k: v for k, v in fair3.items() if not isinstance(v, list)},
             "manufacturer_rule_tiles_on_eligible_area": int(rec.n_tiles), "eligible_area_mm2": elig_area,
             "arms": {r["arm"]: {"label": r["label"], "sa_seed": r["sa_seed"], "n_seeds_scored": r["n_seeds_scored"],
                                 "geometry": ({k: v for k, v in (r["geom"] or {}).items()
                                               if k not in ("poses", "tiles")} if r["geom"] else None),
                                 "objective": r.get("objective"), "solver_status": r.get("solver_status"),
                                 "solve_s": r["solve_s"], "total_s": r["total_s"],
                                 "stats": {lab: {k: r["stats"][lab].get(k) for k in STAT_KEYS if k in r["stats"][lab]}
                                           for lab in r["stats"]},
                                 "note": r["note"]} for r in arms},
             "best_sa_seed": int(best["sa_seed"]), "objective_best_sa_seed": int(best_obj["sa_seed"]),
             "timings_s": {"reconstruct": t_rec, "structures": t_targets, "opening": t_open, "eligibility": t_elig,
                           "fit_count%d" % a.count_known: g8["fit_s"], "fit_%dfull_%dhalf" % (nf, nh): g64["fit_s"],
                           "total": time.perf_counter() - t_all},
             "arrays": {}}
    for k, v in arrays.items():
        base = k
        for pre in ("count%d_" % a.count_known, "count_%dfull_%dhalf_" % (nf, nh)):
            if k.startswith(pre):
                base = "opt_" + k[len(pre):] if ("opt_" + k[len(pre):]) in docs else k[len(pre):]
        units, doc = docs.get(base, docs.get(k, ("-", "[implant fit] " + k)))
        index["arrays"][k] = {"shape": list(v.shape), "dtype": str(v.dtype), "units": units, "doc": doc}
    with open(npz_path[:-4] + ".json", "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))

    # 7. markdown for the notes ---------------------------------------------------
    print("\n### F8 / Table 3 patient arms (commit %s; %s)\n" % (commit, FLAG))
    print("| arm | seeds | tiles | overlaps | RIND5(gtcore) V100 | D90 | CTV_GT V100 | D90 | REACH10 V100 | D90 | "
          "RIND5(Cav_Post) V100 | D90 | solve s |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sum_rows:
        print("| %s | %s | %s | %s | %.3f | %.0f | %.3f | %.0f | %.3f | %.0f | %.3f | %.0f | %s |"
              % (r["arm"], r["n_seeds_scored"], r["n_tiles"], _fmt(r["n_overlaps"]), r["RIND5_gtcore_V100"],
                 r["RIND5_gtcore_D90_cGy"], r["CTV_GT_V100"], r["CTV_GT_D90_cGy"], r["REACH10_V100"],
                 r["REACH10_D90_cGy"], r["RIND5_CavPost_V100"] or float("nan"), r["RIND5_CavPost_D90_cGy"] or float("nan"),
                 _fmt(r["solve_s"])))
    print("\nwrote", arms_csv, summary_csv, npz_path, "(%.0f s total)" % (time.perf_counter() - t_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
