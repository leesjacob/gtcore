"""Table 4 -- per-stage runtimes of gtcore on the paper's datasets (Paper 2,
Phase 4 campaign, step 1).

One run per stage with the machine otherwise idle; hardware stated in every
row.  Three blocks:

(a) ``gtcore.pipeline.reconstruct`` stage timings (``result.timings``) on
    PH8, PT-CT1mm and the tile-less negative control PH0, with the count-free
    tile fit (``n_full_tiles="auto"``, the ``gt plan`` / F4 path; the plain
    ``reconstruct(vol)`` fits no tiles), plus the grouped rows io / seeds /
    segment / tiles the figure F1(b) annotates;
(b) optimizer stages (target, candidates, influence, conflicts, SA solver
    8 x 60 at N = 8, final_report) through ``gtcore.plan.optimize`` at the
    production defaults (h 2.5 mm, 6 spins, M_opt 4000, lambda_tail 1,
    S_K 3.5 U) on one synthetic cavity, on the PH8 phantom with the
    registered tile-less twin as the wall (``gt optimize --phantom-ref``)
    and on the PT-CT1mm gtcore cavity mesh at clinical resolution with the
    craniotomy-side eligibility (facing + line of sight, no implant term:
    the F8(b) rule of docs/paper2-decisions.md), then -- the rev-3 "Should"
    item 17 decision number -- candidates + conflicts on the WHOLE clinical
    mesh (eligibility ``all``, the planner's default) at the same grid;
(c) planner recompute latency (``gt plan`` key U + V): the 2 mm dose grid
    over the seeds, the isodose surfaces, the HR-CTV rind
    (``RIND5(cavity, clip)`` at 1 mm) build and its exact-engine scoring,
    on PH8 and PT-CT1mm.

Usage (worktree root, PYTHONPATH=<worktree>, GTCORE_STRICT=1)::

    python scripts/table4_runtimes.py --ph8 "$GT_DATA_ROOT/<PH8>" --ph0 "$GT_DATA_ROOT/<PH0>" \
        --patient "$GT_DATA_ROOT/<PT-CT1mm>" --out docs/figures/data/table4_runtimes.csv \
        --reports <scratch dir>

Every timing is a single wall-clock measurement (perf_counter); the CSV
header states the commit and the hardware.  No parameter is tuned here.
"""
from __future__ import annotations

import argparse
import csv
import ctypes
import datetime as _dt
import os
import platform
import re
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gtcore  # noqa: E402
import gtcore.plan as plan  # noqa: E402
from gtcore.io import load_volume  # noqa: E402
from gtcore.pipeline import reconstruct  # noqa: E402
from gtcore.plan import candidates as cand_mod  # noqa: E402

RX_CGY = 6000.0
N_TILES = 8
COLUMNS = ["dataset", "block", "stage", "seconds", "n_faces", "n_candidates", "n_voxels", "n_seeds",
           "note", "commit", "hardware"]


# ------------------------------------------------------------------ helpers
def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:                                                 # noqa: BLE001
        return "unknown"


def cpu_name() -> str:
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                           r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except Exception:                                                 # noqa: BLE001
        return platform.processor()


def ram_gb() -> float:
    try:
        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        ms = MS()
        ms.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
        return ms.ullTotalPhys / 1024.0 ** 3
    except Exception:                                                 # noqa: BLE001
        return float("nan")


def hardware_line() -> str:
    return ("%s, %d logical cores, %.0f GB RAM, %s %s; Python %s, numpy %s"
            % (cpu_name(), os.cpu_count() or 0, ram_gb(), platform.system(), platform.release(),
               platform.python_version(), np.__version__))


class Rows:
    def __init__(self, commit: str, hw: str):
        self.rows = []
        self.commit, self.hw = commit, hw

    def add(self, dataset, block, stage, seconds, n_faces=None, n_candidates=None, n_voxels=None,
            n_seeds=None, note=""):
        row = {"dataset": dataset, "block": block, "stage": stage, "seconds": round(float(seconds), 3),
               "n_faces": n_faces, "n_candidates": n_candidates, "n_voxels": n_voxels, "n_seeds": n_seeds,
               "note": note, "commit": self.commit, "hardware": self.hw}
        self.rows.append(row)
        print("  [%s] %-9s %-60s %9.2f s  %s" % (block, dataset, stage, seconds, note), flush=True)
        return row


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


# -------------------------------------------------------------------- (a)
GROUPS = {
    "seeds": ("seed detection", "metal inpainting", "seed refinement"),
    "segment": ("head segmentation", "cavity segmentation", "surface meshes"),
    "tiles": ("tile fitting", "tile fusion", "inferred-seed check"),
}


def block_a(rows: Rows, dataset: str, path: str):
    print("(a) reconstruct on", dataset, flush=True)
    vol, t_io = timed(lambda: load_volume(path))
    nvox = int(np.asarray(vol.array).size)
    rows.add(dataset, "a", "io: load_volume (DICOM series)", t_io, n_voxels=nvox,
             note="%s voxels @ %s mm" % ("x".join(str(s) for s in vol.array.shape),
                                         "x".join("%.2f" % s for s in vol.spacing)))
    res, t_rec = timed(lambda: reconstruct(vol, verbose=False, n_full_tiles="auto", n_half_tiles=0))
    n_seeds = int(len(res.seeds)) if res.seeds is not None else 0
    for name, s in res.timings.items():
        rows.add(dataset, "a", "reconstruct: " + name, s, n_voxels=nvox, n_seeds=n_seeds)
    for g, names in GROUPS.items():
        s = sum(res.timings.get(n, 0.0) for n in names)
        present = [n for n in names if n in res.timings]
        rows.add(dataset, "a", "group: " + g, s, n_voxels=nvox, n_seeds=n_seeds,
                 note="= " + " + ".join(present) if present else "no stage ran")
    n_tiles = len(res.tiles.tiles) if res.tiles is not None else 0
    rows.add(dataset, "a", "reconstruct total (count-free tile fit)", t_rec, n_voxels=nvox, n_seeds=n_seeds,
             note="n_full_tiles='auto'; %d tiles" % n_tiles)
    return vol, res


# -------------------------------------------------------------------- (c)
def block_c(rows: Rows, dataset: str, vol, res):
    """The planner's recompute (key U: dose grid + isodoses; key V: HR-CTV)."""
    from gtcore.dose import compute_dose_grid, isodose_surfaces
    from gtcore.dose.hrctv import build_hrctv, hrctv_stats
    from gtcore.planner import (DOSE_PAD_MM, DOSE_SPACING_MM, HRCTV_SPACING_MM, WALL_DEPTH_MM,
                                wall_mesh_for)
    print("(c) planner recompute on", dataset, flush=True)
    mesh, label = wall_mesh_for(res)
    if mesh is None or res.seeds is None or not len(res.seeds):
        rows.add(dataset, "c", "planner: recompute", float("nan"), note="no wall or no seeds -- not applicable")
        return
    centers = np.asarray(res.seeds.centers_ras, float)
    axes = np.asarray(res.seeds.axes_ras, float)
    n_seeds = int(centers.shape[0])
    nf = int(len(mesh.faces))
    bounds = np.vstack([centers.min(axis=0) - DOSE_PAD_MM, centers.max(axis=0) + DOSE_PAD_MM])
    dv, t_grid = timed(lambda: compute_dose_grid(centers, axes, bounds, spacing_mm=DOSE_SPACING_MM))
    rows.add(dataset, "c", "planner U: compute_dose_grid %g mm (tabulated kernel)" % DOSE_SPACING_MM, t_grid,
             n_faces=nf, n_voxels=int(dv.array.size), n_seeds=n_seeds,
             note="seeds +- %g mm pad; wall = %s" % (DOSE_PAD_MM, label))
    _iso, t_iso = timed(lambda: isodose_surfaces(dv, (0.5 * RX_CGY, RX_CGY)))
    rows.add(dataset, "c", "planner U: isodose_surfaces (30, 60 Gy)", t_iso, n_faces=nf,
             n_voxels=int(dv.array.size), n_seeds=n_seeds)
    on_cavity = label == "cavity wall"
    cav = getattr(res, "cavity_mask", None)
    inside = cav if (on_cavity and cav is not None and np.any(cav)) else None
    aff = res.volume.affine
    keep = keep_source = None
    for key in (("cranial_interior", "brain") if on_cavity else ("body",)):
        m = (res.masks or {}).get(key)
        if m is not None and np.any(m):
            keep, keep_source = m, "gtcore:%s" % key
            break
    h, t_build = timed(lambda: build_hrctv(mesh, depth_mm=WALL_DEPTH_MM, spacing_mm=HRCTV_SPACING_MM,
                                           side=+1 if on_cavity else -1,
                                           inside_mask=inside,
                                           inside_affine=aff if inside is not None else None,
                                           keep_mask=keep, keep_affine=aff if keep is not None else None,
                                           source=label, keep_source=keep_source))
    rows.add(dataset, "c", "planner V: build_hrctv (RIND5 %g mm, %g mm grid)" % (WALL_DEPTH_MM, HRCTV_SPACING_MM),
             t_build, n_faces=nf, n_voxels=int(h.n_voxels), n_seeds=n_seeds,
             note="%s; clip %s; built once per session" % (("RIND5(cavity)" if inside is not None
                                                            else "mesh-offset fallback (shell)"), keep_source))
    doses, t_score = timed(lambda: h.doses_exact(centers, axes))
    st = hrctv_stats(doses, RX_CGY, h.voxel_volume_mm3)
    rows.add(dataset, "c", "planner V: HR-CTV exact-engine scoring (dose_stats)", t_score, n_faces=nf,
             n_voxels=int(h.n_voxels), n_seeds=n_seeds,
             note="D90 %.0f cGy, V100 %.3f, %.1f cc" % (st["D90"], st["V100"], st["volume_cc"]))
    rows.add(dataset, "c", "planner recompute latency per edit (U + rind scoring)", t_grid + t_iso + t_score,
             n_faces=nf, n_seeds=n_seeds, note="dose grid + isodoses + rind scoring (the rind is built once)")


# -------------------------------------------------------------------- (b)
def _optimize_rows(rows: Rows, dataset: str, mesh, elig, rind_kw, note: str, reports_dir, tag: str):
    lines = []
    nf = int(len(mesh.faces))
    t0 = time.perf_counter()
    tiles, rep = plan.optimize(mesh, N_TILES, 0, rx_cgy=RX_CGY, solver="sa", seed=0, eligible=elig,
                               report=True, verbose=False, log=lines.append, **rind_kw)
    wall = time.perf_counter() - t0
    n_cand = None
    for ln in lines:
        m = re.match(r"candidates: (\d+)", ln)
        if m:
            n_cand = int(m.group(1))
    for key, label in (("target", "optimizer: target (+5 mm shell of the eligible wall)"),
                       ("candidates", "optimizer: build_candidates (h 2.5 mm, 6 spins)"),
                       ("influence", "optimizer: build_influence (M_opt 4000)"),
                       ("conflicts", "optimizer: build_conflicts"),
                       ("solver", "optimizer: solve_sa 8 x 60, N = 8"),
                       ("report", "optimizer: final_report (1 mm grid, RIND5)")):
        if key in rep.runtime:
            rows.add(dataset, "b", label, rep.runtime[key], n_faces=nf, n_candidates=n_cand, note=note)
    r5 = (rep.metrics_grid or {}).get("RIND5", {})
    rows.add(dataset, "b", "optimizer: plan.optimize total (SA, N = 8)", wall, n_faces=nf, n_candidates=n_cand,
             note="%s; eligible %.1f %% of the wall area; RIND5 V100 %.3f D90 %.0f cGy; %d overlaps"
                  % (note, 100.0 * (elig.fraction if elig is not None else 1.0),
                     r5.get("V100", float("nan")), r5.get("D90", float("nan")), len(rep.overlaps)))
    if reports_dir:
        rep.to_json(os.path.join(reports_dir, "table4_%s_report.json" % tag))
        with open(os.path.join(reports_dir, "table4_%s_log.txt" % tag), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    return rep


def block_b_synthetic(rows: Rows, reports_dir):
    import gtcore.phantom.generate as gen
    from gtcore.segment.surface import mask_to_mesh
    print("(b) optimizer on one synthetic cavity (make_head_phantom seed 1, scale 1.0)", flush=True)
    vol, truth = gen.make_head_phantom(spacing=1.0, n_tiles=8, rng_seed=1)
    mesh = mask_to_mesh(truth.masks["cavity"], vol.affine)
    elig = cand_mod.eligible_faces(mesh, "all")
    rind_kw = dict(cavity_mask=truth.masks["cavity"], cavity_affine=vol.affine,
                   clip_mask=truth.masks.get("brain"), clip_affine=vol.affine,
                   cavity_source="synthetic:cavity", clip_source="synthetic:brain")
    vol_ml = float(truth.masks["cavity"].sum()) / 1000.0
    _optimize_rows(rows, "synthetic s1_x1.00", mesh, elig, rind_kw,
                   "synthetic cavity %.1f mL, %.0f mm2, all faces eligible" % (vol_ml, mesh.area),
                   reports_dir, "synthetic")


def block_b_phantom(rows: Rows, vol8, res8, vol0, reports_dir):
    from gtcore.segment.cavity import phantom_wall_and_cavity
    print("(b) optimizer on PH8 with the registered tile-less twin (gt optimize --phantom-ref)", flush=True)
    centers = np.asarray(res8.seeds.centers_ras, float)
    (wall, cav, aff, info), t = timed(lambda: phantom_wall_and_cavity(
        vol8, vol0, masks_ph8=res8.masks, seed_centers_ras=centers))
    rows.add("PH8", "b", "phantom-ref: phantom_wall_and_cavity (register PH0, lumen, wall)", t,
             n_faces=int(len(wall.faces)), n_voxels=int(cav.size), n_seeds=int(len(centers)),
             note="Dice %.3f; cavity %.1f cc; wall %.0f mm2" % (info["registration"]["dice_masks"],
                                                               info["lumen_volume_ml_ph8_grid"],
                                                               info["wall"]["area_wall_mm2"]))
    elig, t = timed(lambda: cand_mod.eligible_faces(wall, "visible",
                                                    implant_center_ras=centers.mean(axis=0)))
    rows.add("PH8", "b", "eligibility: visible from the implant centre", t, n_faces=int(len(wall.faces)),
             note="%d of %d faces, %.1f %% of the area" % (elig.n_eligible, elig.mask.size, 100 * elig.fraction))
    rind_kw = dict(cavity_mask=cav, cavity_affine=aff, clip_mask=info["shell_mask"], clip_affine=aff,
                   cavity_source="PH0:lumen(registered)", clip_source="PH0:shell(registered)")
    _optimize_rows(rows, "PH8", wall, elig, rind_kw,
                   "registered PH0 wall %.0f mm2, eligible = visible from the implant" % wall.area,
                   reports_dir, "PH8_phantom_ref")


def block_b_patient(rows: Rows, vol, res, reports_dir):
    from gtcore.plan import candidates as cand_mod
    from gtcore.planner import wall_mesh_for
    print("(b) optimizer on PT-CT1mm, craniotomy-side eligibility (facing + line of sight)", flush=True)
    mesh, label = wall_mesh_for(res)
    nf = int(len(mesh.faces))
    opening, t = timed(lambda: cand_mod.craniotomy_opening(res.masks, vol.affine, cavity_mask=res.cavity_mask))
    rows.add("PT-CT1mm", "b", "eligibility: craniotomy_opening", t, n_faces=nf,
             note="opening %.1f cm2" % (opening.area_mm2 / 100.0))
    elig, t = timed(lambda: cand_mod.craniotomy_faces(mesh, opening, implant_center_ras=None))
    rows.add("PT-CT1mm", "b", "eligibility: craniotomy_faces (facing + line of sight, no implant term)", t,
             n_faces=nf, note="%d of %d faces, %.1f %% of the area" % (elig.n_eligible, nf, 100 * elig.fraction))
    rind_kw = dict(cavity_mask=res.cavity_mask, cavity_affine=vol.affine,
                   clip_mask=res.masks["cranial_interior"], clip_affine=vol.affine,
                   cavity_source="gtcore:cavity", clip_source="gtcore:cranial_interior")
    _optimize_rows(rows, "PT-CT1mm", mesh, elig, rind_kw,
                   "gtcore %s, %.0f mm2, craniotomy-side eligible wall" % (label, mesh.area),
                   reports_dir, "PT-CT1mm_craniotomy")
    return mesh


def block_b_patient_all_faces(rows: Rows, mesh, reports_dir):
    """Rev-3 'Should' item 17: candidates + conflicts on the whole clinical
    mesh at the production grid (the planner's default eligibility)."""
    print("(b) decision number: candidates + conflicts on ALL faces of the clinical mesh", flush=True)
    nf = int(len(mesh.faces))
    cand, t_c = timed(lambda: plan.build_candidates(mesh, h_mm=plan.DEFAULT_H_MM, n_spins=None,
                                                    kinds=("full",)))
    rows.add("PT-CT1mm", "b", "all faces: build_candidates (h 2.5 mm, 6 spins)", t_c, n_faces=nf,
             n_candidates=int(len(cand)), note="rejected %r" % (dict(cand.n_rejected),))
    conf, t_k = timed(lambda: plan.build_conflicts(cand))
    rows.add("PT-CT1mm", "b", "all faces: build_conflicts", t_k, n_faces=nf, n_candidates=int(len(cand)),
             note="%d pairs" % conf.count_pairs())
    rows.add("PT-CT1mm", "b", "all faces: candidates + conflicts (rev-3 item 17 threshold ~300 s)", t_c + t_k,
             n_faces=nf, n_candidates=int(len(cand)),
             note="%s 5 min" % ("EXCEEDS" if (t_c + t_k) > 300.0 else "within"))
    from gtcore.plan.api import default_target
    target = default_target(mesh, None)
    _infl, t_i = timed(lambda: plan.build_influence(cand, target, rx_cgy=RX_CGY))
    rows.add("PT-CT1mm", "b", "all faces: build_influence (M_opt 4000)", t_i, n_faces=nf,
             n_candidates=int(len(cand)))


# ------------------------------------------------------------------- main
def masked_argv(argv):
    out, skip = [], False
    for tok in argv:
        if skip:
            out.append("<path>")
            skip = False
        elif tok in ("--ph8", "--ph0", "--patient", "--out", "--reports"):
            out.append(tok)
            skip = True
        else:
            out.append(tok)
    return out


def write_csv(path: str, rows: Rows, argv, hw: str, commit: str, datasets: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    header = [
        "# Table 4 -- per-stage runtimes, one wall-clock measurement each, machine otherwise idle",
        "# script: scripts/table4_runtimes.py",
        "# command: python scripts/table4_runtimes.py " + " ".join(masked_argv(argv)),
        "# commit: " + commit,
        "# date: " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "# datasets: " + datasets,
        "# hardware: " + hw,
        "# gtcore: " + gtcore.__file__.replace(ROOT, "<worktree>"),
        "# blocks: a = reconstruct stages (count-free tile fit) and grouped rows; b = optimizer stages via "
        "plan.optimize (production defaults h 2.5 mm / 6 spins / M_opt 4000 / lambda_tail 1 / SA 8 x 60 / "
        "N = 8 / S_K 3.5 U) and the all-faces candidates + conflicts decision number; c = planner recompute",
        "# columns: dataset, block, stage, seconds (wall), n_faces (wall mesh), n_candidates, n_voxels "
        "(CT / dose grid / rind), n_seeds, note, commit, hardware",
        "# flag: PH8 / PT-CT1mm rows are calibration-set (one patient; the phantom is the same patient's "
        "intra-operative cavity); synthetic n/a",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for ln in header:
            fh.write(ln + "\n")
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows.rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in COLUMNS})


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--ph8", required=True, help="PH8 DICOM directory (printed phantom, 8 tiles)")
    p.add_argument("--ph0", required=True, help="PH0 DICOM directory (tile-less twin)")
    p.add_argument("--patient", required=True, help="PT-CT1mm DICOM directory")
    p.add_argument("--out", default=os.path.join("docs", "figures", "data", "table4_runtimes.csv"))
    p.add_argument("--reports", default=None, help="folder for the optimizer report JSONs (scratch)")
    p.add_argument("--skip-all-faces", action="store_true",
                   help="skip the all-faces candidates + conflicts decision measurement")
    a = p.parse_args(argv)
    argv = sys.argv[1:] if argv is None else list(argv)
    print("gtcore.__file__ =", gtcore.__file__)
    commit = git_commit()
    hw = hardware_line()
    print("commit", commit, "|", hw, flush=True)
    if a.reports:
        os.makedirs(a.reports, exist_ok=True)
    rows = Rows(commit, hw)
    t_all = time.perf_counter()

    vol8, res8 = block_a(rows, "PH8", a.ph8)
    volp, resp = block_a(rows, "PT-CT1mm", a.patient)
    vol0, _res0 = block_a(rows, "PH0", a.ph0)
    block_c(rows, "PH8", vol8, res8)
    block_c(rows, "PT-CT1mm", volp, resp)
    block_b_synthetic(rows, a.reports)
    block_b_phantom(rows, vol8, res8, vol0, a.reports)
    mesh_p = block_b_patient(rows, volp, resp, a.reports)
    if not a.skip_all_faces:
        block_b_patient_all_faces(rows, mesh_p, a.reports)
    rows.add("all", "-", "script wall time", time.perf_counter() - t_all)
    write_csv(a.out, rows, argv, hw, commit,
              "PH8, PT-CT1mm, PH0 (dataset ids of docs/paper2-decisions.md); "
              "synthetic = make_head_phantom(seed 1, scale 1.0)")
    print("wrote", a.out, "(%d rows)" % len(rows.rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
