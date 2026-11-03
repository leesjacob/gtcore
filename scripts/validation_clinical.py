"""Clinical validation: gtcore vs the treatment-planning system on one case
(Paper 2, Phase 3 ``clinical-rebuild``).  The single source of the clinical
numbers in Table 3 and of the F4(a), F5(a), F5(b) and F6 panel data.

Inputs (DICOM): the post-implant CT series (``PT-CT1mm``), the RTSTRUCT
exported from the TPS (``PT-RT``: ``Cav_Post`` resection cavity, ``CTV_GT``
the physician's hand-trimmed HR-CTV, ``Brain``, ``Seeds`` -- 30 contoured
of the plan's 32 sources) and the RTDOSE grid of the clinical plan.

What it does, in order (everything on Phase 2's one definition of each thing:
``gtcore.io.rt`` readers and rasteriser, ``gtcore.dose.targets`` structures,
``gtcore.dose.metrics.dose_stats`` statistics, ``gtcore.pipeline.reconstruct``
seeds):

1. runs the gtcore pipeline on the CT (32 seeds after Phase 1);
2. reads the TPS ``Seeds`` contours (30 centroids + PCA axes) and the plan's
   32 source positions (RTDOSE local maxima > 200 Gy merged at 3 mm; axes =
   the matched contour's PCA axis, else the matched gtcore detection's axis);
3. seed match tables: contoured 30 vs gtcore (one-to-one, 2 mm gate) with
   per-seed residual vectors (gtcore - TPS, RAS mm) and the 32 plan sources
   vs gtcore (3 mm gate);
4. builds the structures on the CT grid: ``HRCTV_PHYS`` (= ``CTV_GT``),
   ``RIND5(Cav_Post, clip Brain)``, ``RIND5(gtcore cavity, clip cranial
   interior)``, ``IMPLANT_REACH(r = 10 mm)`` (F6 calibration) and r = 12 mm
   as a labelled second line;
5. the structure x dose matrix: RTDOSE (trilinear at voxel centres), gtcore
   @ 32 TPS sources, gtcore @ 32 gtcore seeds, gtcore @ 30 contoured seeds;
   D90 / V100 / V150 / volume from ``dose_stats``; S_K nominal 3.5 U
   everywhere (never fitted);
6. the ``delivered_fraction`` check (RTDOSE magnitude vs the TG-43
   total-decay dose at the 32 sources) and the implied-S_K line
   (diagnostic, not used);
7. ``verify_against_tps`` against the RTDOSE ``DVHSequence`` when present or
   a MIM DVH export (``--mim-dvh``); otherwise prints the command to run and
   marks every dose row "TPS volume check pending";
8. writes the Table 3 rows and the panel data CSV/NPZ under ``--out``.

Seed-axis convention (``--axes``; Phase 3 ``formalism``: the TPS computed
every source with its long axis along the patient S-I direction).
``physical`` (default) is the current behaviour: the PCA long axis of each
seed / matched contour, the pipeline result.  ``si`` ADDS the TPS-convention
columns ``gtcore@32 TPS sources (S-I axes)`` and ``gtcore@32 gtcore seeds
(S-I axes)`` -- every seed axis set to RAS (0, 0, 1); the sign is irrelevant
for the symmetric anisotropy function -- to the structure x dose matrix and
to ``clinical_table3_rows.csv``, plus the implied-S_K diagnostic line with
S-I axes.  The physical columns, the diagnostics and the panel data are
unchanged by ``si`` (they are always written: the F4(a)/F5/F6 panels show
the pipeline result; the S-I column is the like-for-like equivalence row).

Usage::

    python scripts/validation_clinical.py "<PT-CT1mm dir>" "<RTSTRUCT dir>" "<RTDOSE dir>"
        [--rx 6000] [--out docs/figures/data] [--mim-dvh <csv>] [--axes physical|si]

Hooks only (not implemented here): the optimizer arm (Phase 4/5); gamma
(``gtcore.dose.gamma``) and formalism (``gtcore.dose.formalism``) are
imported when merged and reported "not merged" otherwise.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys
import time
import warnings

import numpy as np
from scipy import ndimage
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import gtcore                                                    # noqa: E402
from gtcore.dose.engine import TG43Engine, dose_at_points        # noqa: E402
from gtcore.dose.formalism import rtdose_source_positions        # noqa: E402
from gtcore.dose.metrics import dose_stats                       # noqa: E402
from gtcore.dose.targets import (Target, dice, hrctv_phys, implant_reach,  # noqa: E402
                                 rind5, target_doses_exact,
                                 target_doses_from_grid)
from gtcore.io import load_volume                                # noqa: E402
from gtcore.io.rt import (check_references, load_rtdose, load_rtstruct,  # noqa: E402
                          parse_mim_dvh_export, rasterize_rois,
                          reference_from_dvh_sequence, verify_against_tps)
from gtcore.pipeline import reconstruct                          # noqa: E402
from gtcore.volume import Volume                                 # noqa: E402

# ------------------------------------------------------------------ constants
SK_NOMINAL_U = 3.5              # nominal S_K per seed [U]; never fitted
RX_DEFAULT_CGY = 6000.0
REACH_R_PRIMARY_MM = 10.0       # F6 calibration (docs/paper2-decisions.md, Dice vs CTV_GT)
REACH_R_SECOND_MM = 12.0        # labelled second line (98.5 % coverage of CTV_GT)
SEED_GATE_MM = 2.0              # contoured seed <-> detection match gate
SOURCE_GATE_MM = 3.0            # plan source <-> detection match gate
SOURCE_GY = 200.0               # RTDOSE local maxima above this are sources
SOURCE_MERGE_MM = 3.0
RATIO_MIN_DIST_MM = 5.0         # implied-S_K / delivered-fraction voxels: > 5 mm from a source
RATIO_MIN_DOSE_CGY = 100.0
RATIO_SUBSAMPLE = 5
DVH_MAX_CGY = 15000.0           # F5(b): 0-150 Gy
DVH_STEP_CGY = 50.0             # 0.5 Gy steps
ISO_LEVELS_CGY = (3000.0, 6000.0)
SLICE_SEED_WINDOW_MM = 3.0      # seeds "on/near" the slice
FLAG = "calibration-set"
DEFAULT_DATASETS = ("PT-CT1mm", "PT-RT")
VERIFY_ROIS = ("Cav_Post", "CTV_GT", "CTV_Post", "Brain")

TABLE3_COLUMNS = ["row", "structure", "dose_source", "metric", "value", "unit",
                  "grid", "flag", "tps_check"]
RESIDUAL_COLUMNS = ["seed_id", "dx_mm", "dy_mm", "dz_mm", "dist_mm"]
SEED_MATCH_COLUMNS = ["seed_id", "tps_x", "tps_y", "tps_z", "gt_index", "gt_x", "gt_y", "gt_z",
                      "dx_mm", "dy_mm", "dz_mm", "dist_mm", "matched"]
SOURCE_MATCH_COLUMNS = ["source_id", "origin", "x", "y", "z", "ax", "ay", "az",
                        "contour_seed_id", "contour_dist_mm", "gt_index", "gt_dist_mm", "matched"]
CONTOUR_COLUMNS = ["layer", "level_cgy", "polyline_id", "vertex", "x_ras", "y_ras", "z_ras"]
SLICE_SEED_COLUMNS = ["set", "seed_id", "x_ras", "y_ras", "z_ras", "dz_mm", "ax", "ay", "az",
                      "contoured", "near_slice"]
DVH_COLUMNS = ["dose_cgy", "rtdose", "gtcore_32_tps_sources", "gtcore_32_gtcore_seeds",
               "gtcore_30_contoured_seeds"]

ROW_HRCTV = "HRCTV_PHYS (CTV_GT)"
ROW_RIND_TPS = "RIND5(Cav_Post, clip Brain)"
ROW_RIND_GT = "RIND5(gtcore cavity, clip cranial interior)"
ROW_REACH = "IMPLANT_REACH(r=%g mm)"
COL_RTDOSE = "RTDOSE"
COL_TPS32 = "gtcore@32 TPS sources"
COL_GT32 = "gtcore@32 gtcore seeds"
COL_TPS30 = "gtcore@30 contoured seeds"
# TPS seed-axis convention (formalism check): every axis along RAS z (S-I)
COL_TPS32_SI = "gtcore@32 TPS sources (S-I axes)"
COL_GT32_SI = "gtcore@32 gtcore seeds (S-I axes)"
AXES_CONVENTIONS = ("physical", "si")
SI_AXIS_RAS = np.array([0.0, 0.0, 1.0])


def parse_axes(text):
    """``--axes`` value -> tuple of conventions (``physical`` is always
    computed; ``si`` adds the S-I columns)."""
    conv = tuple(c.strip() for c in str(text).split(",") if c.strip())
    for c in conv:
        if c not in AXES_CONVENTIONS:
            raise ValueError("unknown --axes convention %r (choose from %s)" % (c, ", ".join(AXES_CONVENTIONS)))
    return conv or ("physical",)


def si_axes(n):
    """``(n, 3)`` seed axes all along the patient S-I direction, RAS (0, 0, 1)."""
    return np.tile(SI_AXIS_RAS, (int(n), 1))


# -------------------------------------------------------------------- helpers
def git_commit(short=True):
    """Commit of the tree this file lives in (``+dirty`` when uncommitted
    changes exist); ``"unknown"`` outside a checkout."""
    try:
        h = subprocess.run(["git", "rev-parse", "--short" if short else "HEAD", "HEAD"],
                           cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        if h.returncode != 0:
            return "unknown"
        d = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                           cwd=REPO_ROOT, capture_output=True, text=True, timeout=10)
        dirty = d.returncode == 0 and d.stdout.strip() != ""
        return h.stdout.strip() + ("+dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def sanitized_command(args, datasets):
    """The command for the CSV headers with dataset ids in place of the
    machine paths (never a patient folder name or a machine path)."""
    ids = list(datasets) if datasets else ["<CT>", "<RT>"]
    ct_id = ids[0]
    rt_id = ids[1] if len(ids) > 1 else ids[0]
    parts = ["python scripts/validation_clinical.py",
             '"$GT_DATA_ROOT/<%s dir>"' % ct_id,
             '"$GT_DATA_ROOT/<%s RTSTRUCT dir>"' % rt_id,
             '"$GT_DATA_ROOT/<%s RTDOSE dir>"' % rt_id,
             "--rx %g" % args.rx]
    if args.out:
        out = args.out if not os.path.isabs(args.out) else "<out dir>"
        parts.append("--out %s" % out)
    if args.mim_dvh:
        parts.append("--mim-dvh <MIM DVH export>")
    axes = getattr(args, "axes", "physical")
    if axes and axes != "physical":
        parts.append("--axes %s" % axes)
    return " ".join(parts)


def header_lines(command, commit, datasets, columns=None, extra=()):
    lines = ["# script: scripts/validation_clinical.py",
             "# command: %s" % command,
             "# commit: %s" % commit,
             "# datasets: %s" % ", ".join(datasets),
             "# generated: %s" % _dt.datetime.now().isoformat(timespec="seconds"),
             "# flag: %s (one clinical case; no held-out scan)" % FLAG]
    lines += ["# %s" % e for e in extra]
    if columns:
        lines.append("# columns: %s" % ",".join(columns))
    return lines


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, (bool, np.bool_)):
        return "True" if v else "False"
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        if not np.isfinite(v):
            return "nan"
        return "%.6g" % float(v)
    s = str(v)
    if any(c in s for c in ",\"\n"):
        s = '"' + s.replace('"', '""') + '"'
    return s


def write_csv(path, rows, columns, header):
    """CSV with ``#`` header-comment lines (readers: ``comment="#"``)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for line in header:
            f.write(line + "\n")
        f.write(",".join(columns) + "\n")
        for r in rows:
            f.write(",".join(_fmt(r.get(c)) for c in columns) + "\n")
    return path


def read_csv_rows(path):
    """Read one of this script's CSVs back (skips ``#`` lines); values as str."""
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        cols = None
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            vals = _split_csv_line(line.rstrip("\n"))
            if cols is None:
                cols = vals
                continue
            rows.append(dict(zip(cols, vals)))
    return cols or [], rows


def _split_csv_line(line):
    out, cur, q = [], "", False
    i = 0
    while i < len(line):
        c = line[i]
        if q:
            if c == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    cur += '"'
                    i += 1
                else:
                    q = False
            else:
                cur += c
        else:
            if c == '"':
                q = True
            elif c == ",":
                out.append(cur)
                cur = ""
            else:
                cur += c
        i += 1
    out.append(cur)
    return out


def dice_masks(a, b):
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    return 2.0 * (a & b).sum() / max(1, a.sum() + b.sum())


def grid_label(vol, dataset_id):
    sp = vol.spacing
    return "%s %.2fx%.2fx%.2f mm" % (dataset_id, sp[0], sp[1], sp[2])


# ------------------------------------------------------------ TPS seed inputs
def tps_seed_contours(rs, vol, seeds_mask=None):
    """Centroids (RAS mm) and PCA long axes of the contoured ``Seeds`` on the
    CT grid (26-connected components of the rasterised ROI: a tilted seed's
    per-slice contours may touch only diagonally; seeds are >= 8 mm apart so
    nothing merges).  Returns ``(centers (n, 3), axes (n, 3), labels, n)``."""
    if seeds_mask is None:
        seeds_mask = rasterize_rois(rs, ("Seeds",), vol)["Seeds"]
    lab, n = ndimage.label(seeds_mask, structure=np.ones((3, 3, 3), bool))
    if n == 0:
        return np.zeros((0, 3)), np.zeros((0, 3)), lab, 0
    cs = np.array(ndimage.center_of_mass(seeds_mask, lab, range(1, n + 1)), float)[:, ::-1]
    centers = vol.index_to_ras(cs)
    axes = []
    for i in range(1, n + 1):
        q = vol.index_to_ras(np.argwhere(lab == i)[:, ::-1].astype(float))
        q = q - q.mean(axis=0)
        if q.shape[0] < 2:
            axes.append(np.array([0.0, 0.0, 1.0]))
            continue
        _w, v = np.linalg.eigh(q.T @ q)
        axes.append(v[:, -1])
    return centers, np.asarray(axes, float), lab, n


def plan_sources_from_rtdose(dv, source_gy=SOURCE_GY, merge_mm=SOURCE_MERGE_MM):
    """The plan's source positions as the RTDOSE local maxima above
    ``source_gy`` (merged within ``merge_mm``), RAS mm, sorted by dose
    (``gtcore.dose.formalism.rtdose_source_positions``: the same 3 x 3 x 3
    maximum filter and greedy merge the Phase 3 run used inline)."""
    src = rtdose_source_positions(dv, min_cgy=source_gy * 100.0, merge_mm=merge_mm)
    return np.asarray(src, float).reshape(-1, 3)


def match_one_to_one(a, b, gate_mm):
    """Hungarian match of rows of ``a`` to rows of ``b``; returns
    ``(ia, ib, dist, ok)`` for the assigned pairs (``ok`` = within the gate)."""
    a = np.asarray(a, float).reshape(-1, 3)
    b = np.asarray(b, float).reshape(-1, 3)
    if a.shape[0] == 0 or b.shape[0] == 0:
        z = np.zeros(0, int)
        return z, z, np.zeros(0), np.zeros(0, bool)
    D = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    ia, ib = linear_sum_assignment(D)
    d = D[ia, ib]
    return ia, ib, d, d <= gate_mm


def build_tps_sources(plan_src, cs_ras, cs_axes, gt_centers, gt_axes,
                      gate_mm=SOURCE_GATE_MM):
    """The 32 TPS sources: positions = the RTDOSE maxima (``plan_src``);
    axis = the PCA axis of the contoured seed matched one-to-one within the
    gate, else the axis of the matched gtcore detection, else the mean axis
    of the three nearest sources that have one.  Returns a dict with
    ``centers``, ``axes``, ``origin`` (per source: ``"contour"`` when a
    contoured seed matched, ``"rtdose-max"`` otherwise), ``contour_id``
    (index into ``cs_ras`` or -1), ``contour_dist``, ``hybrid_centers``
    (the contour centroid where matched, the maximum otherwise -- a
    diagnostic variant) and the per-source match to the gtcore detections."""
    plan_src = np.asarray(plan_src, float).reshape(-1, 3)
    n = plan_src.shape[0]
    axes = np.full((n, 3), np.nan)
    origin = np.array(["rtdose-max"] * n, dtype=object)
    contour_id = np.full(n, -1, int)
    contour_dist = np.full(n, np.nan)
    hybrid = plan_src.copy()
    ic, isrc, d, ok = match_one_to_one(cs_ras, plan_src, gate_mm)
    for a, b, dd, o in zip(ic, isrc, d, ok):
        contour_dist[b] = dd
        if o:
            contour_id[b] = a
            axes[b] = cs_axes[a]
            origin[b] = "contour"
            hybrid[b] = cs_ras[a]
    gt_index = np.full(n, -1, int)
    gt_dist = np.full(n, np.nan)
    ig, isrc2, d2, ok2 = match_one_to_one(np.asarray(gt_centers, float), plan_src, gate_mm)
    for a, b, dd, o in zip(ig, isrc2, d2, ok2):
        gt_dist[b] = dd
        if o:
            gt_index[b] = a
            if not np.all(np.isfinite(axes[b])):
                axes[b] = np.asarray(gt_axes, float)[a]
    # fallback: mean axis of the three nearest sources that have one
    have = np.all(np.isfinite(axes), axis=1)
    for b in np.flatnonzero(~have):
        if have.any():
            order = np.argsort(np.linalg.norm(plan_src[have] - plan_src[b], axis=1))[:3]
            v = axes[have][order]
            ref = v[0]
            v = np.where((v @ ref)[:, None] < 0, -v, v)
            m = v.mean(axis=0)
            axes[b] = m / max(np.linalg.norm(m), 1e-12)
        else:
            axes[b] = np.array([0.0, 0.0, 1.0])
    return {"centers": plan_src, "axes": axes, "origin": origin, "contour_id": contour_id,
            "contour_dist": contour_dist, "hybrid_centers": hybrid,
            "gt_index": gt_index, "gt_dist": gt_dist}


# -------------------------------------------------------- seed match printing
def seed_match_tables(vol, res, cs_ras, dv, plan_src=None, gate_mm=SEED_GATE_MM,
                      source_gate_mm=SOURCE_GATE_MM):
    """Phase 1 printing (vault-filter acceptance) plus the data behind it: a
    one-to-one (Hungarian) match of the detections to the contoured
    ``Seeds`` with per-seed residual vectors (gtcore - TPS, RAS mm), the
    plan's source count from the RTDOSE maxima and their match to the
    detections, and where every detection sits relative to gtcore's cranial
    interior.  Returns ``(residual_rows, source_rows, summary)``."""
    ours = np.asarray(res.seeds.centers_ras, float)
    cs_ras = np.asarray(cs_ras, float).reshape(-1, 3)
    n_tps, n_det = len(cs_ras), len(ours)
    print("seeds: TPS contoured %d, gtcore detected %d" % (n_tps, n_det))
    ri, ci, dd, ok = match_one_to_one(cs_ras, ours, gate_mm)
    md = dd[ok] if ok.any() else np.array([np.nan])
    print("  one-to-one match (gate %.0f mm): %d/%d contoured seeds matched; "
          "distance median %.2f, p95 %.2f, max %.2f mm"
          % (gate_mm, int(ok.sum()), n_tps, np.median(md), np.percentile(md, 95), md.max()))
    residual_rows, match_rows = [], []
    matched_t = {}
    for t, e, d, o in zip(ri, ci, dd, ok):
        matched_t[int(t)] = (int(e), float(d), bool(o))
    for t in range(n_tps):
        e, d, o = matched_t.get(t, (-1, float("nan"), False))
        row = {"seed_id": t, "tps_x": cs_ras[t, 0], "tps_y": cs_ras[t, 1], "tps_z": cs_ras[t, 2],
               "gt_index": e, "matched": o}
        if o:
            delta = ours[e] - cs_ras[t]
            row.update({"gt_x": ours[e, 0], "gt_y": ours[e, 1], "gt_z": ours[e, 2],
                        "dx_mm": delta[0], "dy_mm": delta[1], "dz_mm": delta[2], "dist_mm": d})
            residual_rows.append({"seed_id": t, "dx_mm": delta[0], "dy_mm": delta[1],
                                  "dz_mm": delta[2], "dist_mm": d})
        else:
            row.update({"gt_x": np.nan, "gt_y": np.nan, "gt_z": np.nan,
                        "dx_mm": np.nan, "dy_mm": np.nan, "dz_mm": np.nan, "dist_mm": np.nan})
            nearest = np.linalg.norm(ours - cs_ras[t], axis=1).min() if n_det else np.nan
            print("    contoured seed %d unmatched: nearest detection %.2f mm" % (t, nearest))
        match_rows.append(row)
    if residual_rows:
        R = np.array([[r["dx_mm"], r["dy_mm"], r["dz_mm"]] for r in residual_rows])
        print("  residuals gtcore - TPS (RAS mm): mean %+.2f %+.2f %+.2f, SD %.2f %.2f %.2f, "
              "|r| median %.2f max %.2f"
              % (R[:, 0].mean(), R[:, 1].mean(), R[:, 2].mean(), R[:, 0].std(ddof=1) if len(R) > 1 else 0.0,
                 R[:, 1].std(ddof=1) if len(R) > 1 else 0.0, R[:, 2].std(ddof=1) if len(R) > 1 else 0.0,
                 np.median(np.linalg.norm(R, axis=1)), np.linalg.norm(R, axis=1).max()))

    # every detection's distance to gtcore's cranial interior (0 = inside)
    interior = np.asarray(res.masks["cranial_interior"], bool)
    d_out = d_tps = np.zeros(0)
    if interior.any():
        d_int = ndimage.distance_transform_edt(~interior, sampling=vol.spacing[::-1])

        def outside_mm(p):
            if len(p) == 0:
                return np.zeros(0)
            return ndimage.map_coordinates(
                d_int, np.atleast_2d(vol.ras_to_index(p))[:, ::-1].T, order=1, mode="nearest")

        d_out, d_tps = outside_mm(ours), outside_mm(cs_ras)
        print("  vault filter meta: %r" % (vol.meta.get("vault_filter"),))
        print("  detections outside the cranial interior: %d of %d (max %.1f mm); "
              "contoured seeds outside it: %d of %d (max %.1f mm)"
              % (int((d_out > 0).sum()), n_det, d_out.max() if n_det else 0.0,
                 int((d_tps > 0).sum()), n_tps, d_tps.max() if n_tps else 0.0))
    else:
        print("  cranial interior empty (phantom?): extracranial check skipped")

    # plan sources = RTDOSE local maxima above SOURCE_GY, merged within 3 mm
    if plan_src is None:
        plan_src = plan_sources_from_rtdose(dv)
    rs_, cs_, ds, oks = match_one_to_one(plan_src, ours, source_gate_mm)
    ms = ds[oks] if oks.any() else np.array([np.nan])
    print("  plan sources: %d RTDOSE local maxima > %.0f Gy (merged at %.0f mm); detections "
          "matched one-to-one within %.0f mm: %d/%d, distance median %.2f, max %.2f mm; "
          "sources without a detection: %d; detections without a source: %d"
          % (len(plan_src), SOURCE_GY, SOURCE_MERGE_MM, source_gate_mm, int(oks.sum()),
             len(plan_src), np.median(ms), ms.max(), len(plan_src) - int(oks.sum()),
             n_det - int(oks.sum())))
    matched_e = set(ci[ok].tolist())
    for e in sorted(set(range(n_det)) - matched_e):
        d_c = np.linalg.norm(cs_ras - ours[e], axis=1).min() if n_tps else np.nan
        d_s = np.linalg.norm(plan_src - ours[e], axis=1).min() if len(plan_src) else np.nan
        print("    detection %d has no contoured seed (nearest %.2f mm); nearest plan "
              "source %.2f mm; %.1f mm outside the cranial interior"
              % (e, d_c, d_s, d_out[e] if d_out.size else 0.0))
    summary = {"n_tps": n_tps, "n_det": n_det, "n_matched": int(ok.sum()),
               "match_median_mm": float(np.median(md)), "match_p95_mm": float(np.percentile(md, 95)),
               "match_max_mm": float(md.max()), "n_sources": int(len(plan_src)),
               "n_sources_matched": int(oks.sum()), "source_median_mm": float(np.median(ms)),
               "source_max_mm": float(ms.max()),
               "n_det_outside_interior": int((d_out > 0).sum()) if d_out.size else 0}
    return residual_rows, match_rows, summary


def source_rows(src, gt_centers):
    rows = []
    for b in range(src["centers"].shape[0]):
        rows.append({"source_id": b, "origin": src["origin"][b],
                     "x": src["centers"][b, 0], "y": src["centers"][b, 1], "z": src["centers"][b, 2],
                     "ax": src["axes"][b, 0], "ay": src["axes"][b, 1], "az": src["axes"][b, 2],
                     "contour_seed_id": int(src["contour_id"][b]),
                     "contour_dist_mm": src["contour_dist"][b],
                     "gt_index": int(src["gt_index"][b]), "gt_dist_mm": src["gt_dist"][b],
                     "matched": bool(src["gt_index"][b] >= 0)})
    return rows


# ------------------------------------------------------------------ targets
def build_targets(vol, rs, res, gt_centers, masks=None):
    """The Table 3 structures on the CT grid, in row order:
    ``[(row_label, Target), ...]`` plus the raw RTSTRUCT masks."""
    if masks is None:
        names = [n for n in ("Cav_Post", "CTV_GT", "Brain") if n in rs.roi_names]
        masks = rasterize_rois(rs, names, vol)
    aff = vol.affine
    out = []
    out.append((ROW_HRCTV, hrctv_phys(masks["CTV_GT"], aff, roi_name="CTV_GT")))
    if "Brain" in masks:
        clip, clip_src = masks["Brain"], "RTSTRUCT:Brain"
    else:
        clip, clip_src = res.masks["cranial_interior"], "gtcore:cranial_interior"
    rind_tps = rind5(masks["Cav_Post"], aff, clip_mask=clip, clip_affine=aff,
                     cavity_source="RTSTRUCT:Cav_Post", clip_source=clip_src)
    out.append((ROW_RIND_TPS if clip_src == "RTSTRUCT:Brain"
                else "RIND5(Cav_Post, clip cranial interior)", rind_tps))
    rind_gt = rind5(res.cavity_mask, aff, clip_mask=res.masks["cranial_interior"], clip_affine=aff,
                    cavity_source="gtcore:cavity", clip_source="gtcore:cranial_interior")
    out.append((ROW_RIND_GT, rind_gt))
    for r in (REACH_R_PRIMARY_MM, REACH_R_SECOND_MM):
        out.append((ROW_REACH % r, implant_reach(rind_tps, gt_centers, r)))
    return out, masks


def print_structure_table(targets, ctv):
    print("structures on the CT grid (volume, Dice vs CTV_GT):")
    for label, t in targets:
        print("  %-44s %7.2f cc  Dice vs CTV_GT %.3f   [%s]"
              % (label, t.volume_cc, dice(ctv, t), t.describe()))


# --------------------------------------------------------- structure x dose
def dose_columns(dv, src, gt_centers, gt_axes, cs_ras, cs_axes, engine=None, sk=SK_NOMINAL_U,
                 axes=("physical",)):
    """``{column: (callable(Target) -> doses cGy, description)}``.  The four
    physical-axis columns always; with ``"si"`` in ``axes`` the two
    TPS-convention columns (every seed axis along RAS z) follow them."""
    eng = engine if engine is not None else TG43Engine()
    cols = {
        COL_RTDOSE: (lambda t: target_doses_from_grid(t, dv),
                     "clinical RTDOSE, trilinear at the CT voxel centres"),
        COL_TPS32: (lambda t: target_doses_exact(t, src["centers"], src["axes"], sk, engine=eng),
                    "TG-43 exact at %d plan sources (RTDOSE maxima; contour/detection axes), "
                    "S_K %.2f U nominal" % (src["centers"].shape[0], sk)),
        COL_GT32: (lambda t: target_doses_exact(t, gt_centers, gt_axes, sk, engine=eng),
                   "TG-43 exact at %d gtcore seeds, S_K %.2f U nominal" % (len(gt_centers), sk)),
        COL_TPS30: (lambda t: target_doses_exact(t, cs_ras, cs_axes, sk, engine=eng),
                    "TG-43 exact at %d contoured TPS seeds (contour centroids, PCA axes), "
                    "S_K %.2f U nominal" % (len(cs_ras), sk)),
    }
    if "si" in axes:
        src_si = si_axes(src["centers"].shape[0])
        gt_si = si_axes(len(gt_centers))
        cols[COL_TPS32_SI] = (lambda t: target_doses_exact(t, src["centers"], src_si, sk, engine=eng),
                              "TG-43 exact at %d plan sources (RTDOSE maxima), every axis along RAS z "
                              "(S-I, the TPS convention), S_K %.2f U nominal" % (src["centers"].shape[0], sk))
        cols[COL_GT32_SI] = (lambda t: target_doses_exact(t, gt_centers, gt_si, sk, engine=eng),
                             "TG-43 exact at %d gtcore seeds, every axis along RAS z (S-I, the TPS "
                             "convention), S_K %.2f U nominal" % (len(gt_centers), sk))
    return cols


def structure_dose_matrix(targets, columns, rx_cgy, ctv=None, verbose=True):
    """Every (structure, dose source) cell through ``dose_stats``.  Returns
    ``(cells, doses)`` with ``cells[(row, col)] = stats dict`` and
    ``doses[(row, col)]`` the dose vector (order of ``voxel_centers_ras()``)."""
    cells, doses = {}, {}
    for label, t in targets:
        for col, (fn, _desc) in columns.items():
            t0 = time.perf_counter()
            d = np.asarray(fn(t), float).reshape(-1)
            st = dose_stats(d, rx_cgy, voxel_volume_mm3=t.voxel_volume_mm3)
            st["elapsed_s"] = time.perf_counter() - t0
            if ctv is not None:
                st["dice_vs_ctv_gt"] = dice(ctv, t)
            cells[(label, col)] = st
            doses[(label, col)] = d
    if verbose:
        print_matrix(targets, columns, cells, rx_cgy)
    return cells, doses


def print_matrix(targets, columns, cells, rx_cgy):
    cols = list(columns)
    print("structure x dose matrix (rx %.0f cGy; cell = D90 cGy / V100 %% / V150 %% ; volume cc "
          "in the row label; S_K %.2f U nominal everywhere):" % (rx_cgy, SK_NOMINAL_U))
    w = max(30, max(len(c) for c in cols) + 2)
    print("  %-46s" % "" + "".join(("%-" + str(w) + "s") % c for c in cols))
    for label, t in targets:
        cell = []
        for c in cols:
            s = cells[(label, c)]
            cell.append(("%6.0f / %5.1f / %5.1f" % (s["D90"], 100 * s["V100"], 100 * s["V150"])).ljust(w))
        print("  %-46s" % ("%s  %.1f cc" % (label, t.volume_cc)) + "".join(cell))
    print("  dose sources:")
    for c, (_fn, desc) in columns.items():
        print("    %-28s %s" % (c, desc))


def table3_rows(targets, columns, cells, grid, tps_check, extra_rows=()):
    rows = []
    for label, t in targets:
        prov = t.describe()
        rows.append({"row": label, "structure": prov, "dose_source": "-", "metric": "volume_cc",
                     "value": t.volume_cc, "unit": "cc", "grid": grid, "flag": FLAG, "tps_check": "n/a"})
        rows.append({"row": label, "structure": prov, "dose_source": "-", "metric": "n_voxels",
                     "value": t.n, "unit": "-", "grid": grid, "flag": FLAG, "tps_check": "n/a"})
        for col in columns:
            s = cells[(label, col)]
            if "dice_vs_ctv_gt" in s and col == list(columns)[0]:
                rows.append({"row": label, "structure": prov, "dose_source": "-",
                             "metric": "dice_vs_CTV_GT", "value": s["dice_vs_ctv_gt"], "unit": "-",
                             "grid": grid, "flag": FLAG, "tps_check": "n/a"})
            for metric, unit in (("D90", "cGy"), ("D50", "cGy"), ("Dmin", "cGy"), ("Dmean", "cGy"),
                                 ("V100", "fraction"), ("V150", "fraction"), ("V200", "fraction")):
                rows.append({"row": label, "structure": prov, "dose_source": col, "metric": metric,
                             "value": s[metric], "unit": unit, "grid": grid, "flag": FLAG,
                             "tps_check": tps_check})
    rows.extend(extra_rows)
    return rows


# ------------------------------------------ implied S_K and delivered fraction
def dose_ratio_check(dv, hrctv, src, rx_cgy, engine=None, sk=SK_NOMINAL_U,
                     min_dist_mm=RATIO_MIN_DIST_MM, min_dose_cgy=RATIO_MIN_DOSE_CGY,
                     subsample=RATIO_SUBSAMPLE, label="32 TPS sources"):
    """RTDOSE / TG-43(total decay, nominal S_K, 32 TPS sources) at HRCTV_PHYS
    voxels farther than ``min_dist_mm`` from any source.  Returns a dict
    with the ratio statistics, the implied S_K (diagnostic, not used) and
    the ``delivered_fraction`` conclusion; prints both lines.  ``label``
    names the source set in the printed line (e.g. ``"32 TPS sources, S-I
    axes"`` for the TPS-convention variant)."""
    eng = engine if engine is not None else TG43Engine()
    pts = hrctv.voxel_centers_ras()[::max(1, int(subsample))]
    if pts.shape[0] == 0 or src["centers"].shape[0] == 0:
        print("  implied S_K / delivered fraction: no voxels or sources (skipped)")
        return {"n": 0, "ratio_median": float("nan"), "implied_sk_u": float("nan"),
                "conclusion": "skipped"}
    dcl = np.asarray(dv.sample_ras(pts, order=1, fill=0.0), float)
    dtg = np.asarray(dose_at_points(src["centers"], src["axes"], pts, sk, engine=eng), float)
    ds, _ = cKDTree(src["centers"]).query(pts)
    ok = (ds > min_dist_mm) & (dcl > min_dose_cgy) & (dtg > 0)
    n = int(ok.sum())
    if n == 0:
        print("  implied S_K / delivered fraction: no eligible voxels (skipped)")
        return {"n": 0, "ratio_median": float("nan"), "implied_sk_u": float("nan"),
                "conclusion": "skipped"}
    r_tg = dtg[ok] / dcl[ok]                 # TG-43 / RTDOSE (the Phase 1 line)
    r_cl = dcl[ok] / dtg[ok]                 # RTDOSE / TG-43 total decay
    med_tg = float(np.median(r_tg))
    med_cl = float(np.median(r_cl))
    implied_sk = sk / med_tg
    print("  TG-43 (%.2f U, %s) / RTDOSE at HRCTV_PHYS voxels > %.0f mm from a source: "
          "median %.3f, p5 %.3f, p95 %.3f (n=%d) -> implied S_K %.2f U  [diagnostic, not used; "
          "every table row uses S_K %.2f U nominal]"
          % (sk, label, min_dist_mm, med_tg, np.percentile(r_tg, 5), np.percentile(r_tg, 95), n,
             implied_sk, sk))
    # delivered-fraction check: a dose-to-time export would read fraction * (S_K_true / S_K_nominal)
    # with fraction = 1 - exp(-t / tau) < 1.
    tau = float(eng.TAU_HOURS)
    f30 = float(eng.delivered_fraction(30.0 * 24.0))
    if med_cl < 1.0:
        t_equiv_h = -tau * np.log(1.0 - med_cl)
        t_txt = "equivalent elapsed time at nominal S_K %.1f d" % (t_equiv_h / 24.0)
    else:
        t_equiv_h = float("inf")
        t_txt = "no finite elapsed time reproduces it at nominal S_K (ratio >= 1)"
    if med_cl >= 0.97:
        conclusion = ("RTDOSE is total dose to decay (ratio %.3f ~ 1; a 30-day dose-to-time export "
                      "would read <= %.3f at nominal S_K); gtcore compares its total-decay dose "
                      "without a delivered_fraction correction" % (med_cl, f30))
    else:
        conclusion = ("RTDOSE / TG-43 total decay = %.3f < 0.97: the RTDOSE may be a dose-to-time "
                      "export (%s) or the plan S_K is below nominal -- check the RTPLAN / plan "
                      "report before quoting F5" % (med_cl, t_txt))
    print("  delivered_fraction check: RTDOSE / TG-43 total decay (%.2f U) median %.3f "
          "(p5 %.3f, p95 %.3f); tau %.1f h; %s" % (sk, med_cl, np.percentile(r_cl, 5),
                                                     np.percentile(r_cl, 95), tau, t_txt))
    print("  conclusion: %s" % conclusion)
    return {"n": n, "ratio_median": med_cl, "ratio_p5": float(np.percentile(r_cl, 5)),
            "ratio_p95": float(np.percentile(r_cl, 95)), "tg43_over_rtdose_median": med_tg,
            "implied_sk_u": float(implied_sk), "tau_hours": tau, "fraction_30d": f30,
            "t_equiv_hours": float(t_equiv_h), "conclusion": conclusion}


# ------------------------------------------------------------ TPS volume check
def tps_volume_check(dv, rs, rx_cgy, mim_dvh=None, rois=VERIFY_ROIS):
    """Wire ``verify_against_tps``: the RTDOSE ``DVHSequence`` when present,
    else ``--mim-dvh``, else print the command and return ``"pending"``.
    Returns ``(status, rows)`` with status ``"pass" | "fail" | "pending"``."""
    rois = tuple(r for r in rois if r in rs.roi_names)
    ref, source = None, None
    if dv.meta.get("dvh_sequence"):
        ref = reference_from_dvh_sequence(dv, rs)
        source = "RTDOSE DVHSequence"
    elif mim_dvh:
        ref = parse_mim_dvh_export(mim_dvh)
        source = "MIM DVH export (--mim-dvh)"
    print("TPS volume / D90 check (verify_against_tps, rasterised on the RTDOSE grid, 2 %% gate):")
    if ref is None:
        print("  reference missing: the RTDOSE has no DVHSequence and no --mim-dvh file was given.")
        print("  Every Table 3 dose row is marked 'TPS volume check pending'. When the MIM DVH /")
        print("  plan export arrives, run from the repo root:")
        print('    python scripts/validation_clinical.py "$GT_DATA_ROOT/<PT-CT1mm dir>" '
              '"$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>" '
              '--rx %g --mim-dvh "$GT_DATA_ROOT/<MIM DVH export file>"' % rx_cgy)
        print("  (or directly: gtcore.io.rt.verify_against_tps(load_rtdose(...), load_rtstruct(...), "
              "parse_mim_dvh_export(<file>), rois=%s, rx_cgy=%g); add roi_col=/volume_col=/"
              "d90_col=/dose_units= to parse_mim_dvh_export if auto-detection fails)"
              % (rois, rx_cgy))
        return "pending", []
    rows = verify_against_tps(dv, rs, ref, rois=rois, rx_cgy=rx_cgy)
    print("  reference: %s" % source)
    all_pass = True
    for r in rows:
        all_pass &= bool(r["pass"])
        print("  %-9s vol %6.2f vs %6.2f cc (%5.1f%%)  D90 %6.0f vs %6.0f cGy (%5.1f%%)  %s"
              % (r["roi"], r["vol_mim"], r["vol_gtcore"], 100 * r["vol_rel_err"],
                 r["D90_mim"], r["D90_gtcore"], 100 * r["D90_rel_err"],
                 "PASS" if r["pass"] else "FAIL"))
    status = "pass" if (rows and all_pass) else "fail"
    print("  overall: %s" % status.upper())
    return status, rows


# ------------------------------------------------------------------ panel data
def implant_slice_index(vol, gt_centers):
    """Axial slice (k) through the implant centroid and its RAS z."""
    c = np.asarray(gt_centers, float).reshape(-1, 3).mean(axis=0)
    k = int(np.clip(np.rint(vol.ras_to_index(c)[2]), 0, vol.array.shape[0] - 1))
    z = float(vol.index_to_ras(np.array([0.0, 0.0, float(k)]))[2])
    return k, z, c


def slice_coordinates(vol, k):
    """``(x_ras, y_ras, z_ras)`` 2-D arrays ``(nj, ni)`` of the voxel centres
    of slice ``k`` and the flat ``(nj*ni, 3)`` point list."""
    nk, nj, ni = vol.array.shape
    jj, ii = np.meshgrid(np.arange(nj), np.arange(ni), indexing="ij")
    ijk = np.stack([ii.ravel(), jj.ravel(), np.full(ii.size, float(k))], axis=1).astype(float)
    pts = vol.index_to_ras(ijk)
    return (pts[:, 0].reshape(nj, ni), pts[:, 1].reshape(nj, ni),
            pts[:, 2].reshape(nj, ni), pts)


def polylines_from_contour(x2d, y2d, z2d, levels):
    """matplotlib ``contour`` of ``z2d`` on the physical grid ``(x2d, y2d)``;
    returns ``[(level, [(N, 2) polyline, ...]), ...]``."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:                               # pragma: no cover - env without matplotlib
        from skimage import measure
        out = []
        for lev in levels:
            segs = []
            for c in measure.find_contours(np.asarray(z2d, float), lev):
                j, i = c[:, 0], c[:, 1]
                x = ndimage.map_coordinates(x2d, [j, i], order=1, mode="nearest")
                y = ndimage.map_coordinates(y2d, [j, i], order=1, mode="nearest")
                segs.append(np.column_stack([x, y]))
            out.append((lev, segs))
        return out
    z = np.asarray(z2d, float)
    out = []
    if not np.isfinite(z).any() or z.max() <= min(levels):
        return [(lev, []) for lev in levels]
    fig, ax = plt.subplots()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            cs = ax.contour(x2d, y2d, z, levels=list(levels))
        for lev, path in zip(cs.levels, cs.get_paths()):
            segs = []
            verts, codes = path.vertices, path.codes
            if verts.shape[0] == 0:
                out.append((float(lev), segs))
                continue
            if codes is None:
                segs.append(np.asarray(verts, float))
            else:
                starts = np.flatnonzero(codes == 1)       # MOVETO
                bounds = list(starts) + [len(codes)]
                for a, b in zip(bounds[:-1], bounds[1:]):
                    v = verts[a:b][codes[a:b] != 79]      # drop CLOSEPOLY placeholders
                    if v.shape[0] >= 2:
                        if np.any(codes[a:b] == 79):
                            v = np.vstack([v, v[:1]])
                        segs.append(np.asarray(v, float))
            out.append((float(lev), segs))
    finally:
        plt.close(fig)
    return out


def mask_outline_polylines(x2d, y2d, mask2d):
    """Outline(s) of a boolean slice mask as polylines at the 0.5 level."""
    if not np.asarray(mask2d).any():
        return []
    res = polylines_from_contour(x2d, y2d, np.asarray(mask2d, float), (0.5,))
    return res[0][1] if res else []


def contour_rows(layer, level, polylines, z):
    rows = []
    for pid, poly in enumerate(polylines):
        for vi, (x, y) in enumerate(np.asarray(poly, float)):
            rows.append({"layer": layer, "level_cgy": level, "polyline_id": pid, "vertex": vi,
                         "x_ras": x, "y_ras": y, "z_ras": z})
    return rows


def dvh_curve(doses, grid_cgy):
    """Cumulative fraction of samples with dose >= each grid value."""
    d = np.sort(np.asarray(doses, float).reshape(-1))
    if d.size == 0:
        return np.full(len(grid_cgy), np.nan)
    idx = np.searchsorted(d, np.asarray(grid_cgy, float), side="left")
    return 1.0 - idx / d.size


def export_panel_data(out_dir, vol, rs, dv, res, masks, targets, cells, doses, src, cs_ras, cs_axes,
                      header, dataset_ct, engine=None, sk=SK_NOMINAL_U, rx_cgy=RX_DEFAULT_CGY,
                      verbose=True):
    """F4(a) / F5(a) / F6 slice data and the F5(b) DVH curves.  Returns the
    paths written."""
    eng = engine if engine is not None else TG43Engine()
    gt_centers = np.asarray(res.seeds.centers_ras, float)
    gt_axes = np.asarray(res.seeds.axes_ras, float)
    k, z, centroid = implant_slice_index(vol, gt_centers)
    x2d, y2d, z2d, pts = slice_coordinates(vol, k)
    nj, ni = x2d.shape
    paths = {}

    # --- doses on the slice
    rt_slice = np.asarray(dv.sample_ras(pts, order=1, fill=0.0), float).reshape(nj, ni)
    seed_sets = {
        "gtcore32src": (src["centers"], src["axes"]),
        "gtcore32gt": (gt_centers, gt_axes),
        "gtcore30": (cs_ras, cs_axes),
    }
    dose_slices = {"rtdose": rt_slice}
    for name, (c, a) in seed_sets.items():
        if len(c):
            dose_slices[name] = np.asarray(dose_at_points(c, a, pts, sk, engine=eng), float).reshape(nj, ni)
        else:
            dose_slices[name] = np.zeros((nj, ni))

    # --- masks on the slice
    tdict = dict(targets)
    slice_masks = {
        "cav_post": np.asarray(masks["Cav_Post"][k], bool),
        "ctv_gt": np.asarray(masks["CTV_GT"][k], bool),
    }
    rind_label = [lbl for lbl in tdict if lbl.startswith("RIND5(Cav_Post")][0]
    slice_masks["rind5_cavpost"] = np.asarray(tdict[rind_label].mask[k], bool)
    slice_masks["rind5_gtcore"] = np.asarray(tdict[ROW_RIND_GT].mask[k], bool)
    reach_label = ROW_REACH % REACH_R_PRIMARY_MM
    slice_masks["implant_reach_r%g" % REACH_R_PRIMARY_MM] = np.asarray(tdict[reach_label].mask[k], bool)
    slice_masks["gtcore_cavity"] = np.asarray(res.cavity_mask[k], bool)

    # --- NPZ
    npz = os.path.join(out_dir, "clinical_slice.npz")
    np.savez_compressed(
        npz,
        header=np.array("\n".join(header)),
        slice_k=np.array(k), slice_z_ras=np.array(z), implant_centroid_ras=centroid,
        affine=np.asarray(vol.affine, float), spacing_mm=np.asarray(vol.spacing, float),
        ct_hu=np.asarray(vol.array[k], np.float32),
        x_ras=np.asarray(x2d, np.float32), y_ras=np.asarray(y2d, np.float32),
        rtdose_cgy=np.asarray(dose_slices["rtdose"], np.float32),
        gtcore32src_cgy=np.asarray(dose_slices["gtcore32src"], np.float32),
        gtcore32gt_cgy=np.asarray(dose_slices["gtcore32gt"], np.float32),
        gtcore30_cgy=np.asarray(dose_slices["gtcore30"], np.float32),
        **{"mask_%s" % n: m for n, m in slice_masks.items()},
        iso_levels_cgy=np.asarray(ISO_LEVELS_CGY, float),
        window_level_note=np.array("display W 400 / L 40 HU (soft tissue); crop centred on the cavity"),
    )
    paths["slice_npz"] = npz

    # --- polylines CSV: isodoses, mask outlines, TPS seed contours near the slice
    rows = []
    for name, arr in dose_slices.items():
        for lev, polys in polylines_from_contour(x2d, y2d, arr, ISO_LEVELS_CGY):
            rows += contour_rows("%s_iso" % name, lev, polys, z)
    for name, m in slice_masks.items():
        rows += contour_rows("%s_outline" % name, np.nan, mask_outline_polylines(x2d, y2d, m), z)
    if "Seeds" in rs.roi_names:
        pid = 0
        for c in rs.contours("Seeds"):
            zc = float(np.mean(c[:, 2]))
            if abs(zc - z) <= SLICE_SEED_WINDOW_MM:
                poly = np.vstack([c[:, :2], c[:1, :2]])
                rows += [dict(r, polyline_id=pid, z_ras=zc)
                         for r in contour_rows("tps_seed_contour", np.nan, [poly], zc)]
                pid += 1
    csv_c = os.path.join(out_dir, "clinical_slice_contours.csv")
    write_csv(csv_c, rows, CONTOUR_COLUMNS,
              header + ["# layers: <dose>_iso at level_cgy (rtdose, gtcore32src, gtcore32gt, gtcore30), "
                        "<mask>_outline (0.5 level), tps_seed_contour (Seeds planes within %g mm of the slice); "
                        "slice k=%d z=%.3f mm" % (SLICE_SEED_WINDOW_MM, k, z)])
    paths["slice_contours_csv"] = csv_c

    # --- seeds / sources with their distance to the slice
    srows = []
    contoured_gt = set(int(i) for i in src["gt_index"][src["origin"] == "contour"] if i >= 0)
    for i, (c, a) in enumerate(zip(gt_centers, gt_axes)):
        srows.append({"set": "gtcore_seed", "seed_id": i, "x_ras": c[0], "y_ras": c[1], "z_ras": c[2],
                      "dz_mm": c[2] - z, "ax": a[0], "ay": a[1], "az": a[2],
                      "contoured": i in contoured_gt, "near_slice": abs(c[2] - z) <= SLICE_SEED_WINDOW_MM})
    for i, (c, a) in enumerate(zip(cs_ras, cs_axes)):
        srows.append({"set": "tps_contour_centroid", "seed_id": i, "x_ras": c[0], "y_ras": c[1],
                      "z_ras": c[2], "dz_mm": c[2] - z, "ax": a[0], "ay": a[1], "az": a[2],
                      "contoured": True, "near_slice": abs(c[2] - z) <= SLICE_SEED_WINDOW_MM})
    for i, (c, a) in enumerate(zip(src["centers"], src["axes"])):
        srows.append({"set": "plan_source", "seed_id": i, "x_ras": c[0], "y_ras": c[1], "z_ras": c[2],
                      "dz_mm": c[2] - z, "ax": a[0], "ay": a[1], "az": a[2],
                      "contoured": src["origin"][i] == "contour",
                      "near_slice": abs(c[2] - z) <= SLICE_SEED_WINDOW_MM})
    csv_s = os.path.join(out_dir, "clinical_slice_seeds.csv")
    write_csv(csv_s, srows, SLICE_SEED_COLUMNS,
              header + ["# sets: gtcore_seed (reconstruct), tps_contour_centroid (Seeds ROI), plan_source "
                        "(RTDOSE maxima); near_slice = |dz| <= %g mm; slice k=%d z=%.3f mm"
                        % (SLICE_SEED_WINDOW_MM, k, z)])
    paths["slice_seeds_csv"] = csv_s

    # --- F5(b) DVH curves on HRCTV_PHYS
    grid = np.arange(0.0, DVH_MAX_CGY + 0.5 * DVH_STEP_CGY, DVH_STEP_CGY)
    curves = {
        "rtdose": dvh_curve(doses[(ROW_HRCTV, COL_RTDOSE)], grid),
        "gtcore_32_tps_sources": dvh_curve(doses[(ROW_HRCTV, COL_TPS32)], grid),
        "gtcore_32_gtcore_seeds": dvh_curve(doses[(ROW_HRCTV, COL_GT32)], grid),
        "gtcore_30_contoured_seeds": dvh_curve(doses[(ROW_HRCTV, COL_TPS30)], grid),
    }
    drows = [{"dose_cgy": g, **{kk: v[i] for kk, v in curves.items()}} for i, g in enumerate(grid)]
    d90s = "; ".join("%s D90 %.0f cGy" % (c, cells[(ROW_HRCTV, c)]["D90"])
                     for c in (COL_RTDOSE, COL_TPS32, COL_GT32, COL_TPS30))
    csv_d = os.path.join(out_dir, "clinical_dvh_hrctv.csv")
    write_csv(csv_d, drows, DVH_COLUMNS,
              header + ["# cumulative DVH on HRCTV_PHYS (CTV_GT, CT grid): fraction of voxels with dose >= dose_cgy; "
                        "%g-%g cGy in %g cGy steps" % (grid[0], grid[-1], DVH_STEP_CGY),
                        "# " + d90s,
                        "# MC band hook: the 95 % band columns are NOT in this file; paper_figures.py joins "
                        "docs/figures/data/f5c_*.csv (agent `uncertainty`, gtcore.dose.uncertainty) on dose_cgy"])
    paths["dvh_csv"] = csv_d

    if verbose:
        print("panel data: slice k=%d (z=%.2f mm RAS) through the implant centroid %s; %d polylines; "
              "DVH %d points" % (k, z, np.round(centroid, 1).tolist(),
                                 len({(r["layer"], r["polyline_id"]) for r in rows}), len(grid)))
        for p in paths.values():
            print("  wrote %s" % p)
    return paths, {"slice_k": k, "slice_z": z, "centroid": centroid}


# ---------------------------------------------------------------------- hooks
def optional_hooks(verbose=True):
    """Gamma and formalism are concurrent agents' modules: import when
    merged, report "not merged" otherwise.  The optimizer arm is Phase 4/5."""
    status = {}
    for name in ("gamma", "formalism"):
        try:
            mod = __import__("gtcore.dose.%s" % name, fromlist=[name])
            status[name] = "available (%s)" % getattr(mod, "__file__", "?")
        except ImportError:
            status[name] = "not merged"
    status["optimizer_arm"] = "Phase 4/5 (not run here)"
    if verbose:
        print("hooks: gamma: %s; formalism: %s; optimizer arm: %s"
              % (status["gamma"], status["formalism"], status["optimizer_arm"]))
    return status


# ---------------------------------------------------------------------- driver
def run_analysis(vol, rs, dv, res, rx_cgy, out_dir, command, commit, datasets=DEFAULT_DATASETS,
                 mim_dvh=None, verbose=True, axes=("physical",)):
    """Everything after loading and reconstruction; returns a dict of results
    and the paths written.  Used by ``main`` and by the smoke test.
    ``axes``: ``("physical",)`` is the Phase 3 run; with ``"si"`` present the
    TPS-convention columns (every seed axis along RAS z) and the S-I
    implied-S_K line are added -- nothing else changes."""
    os.makedirs(out_dir, exist_ok=True)
    axes = tuple(axes)
    with_si = "si" in axes
    eng = TG43Engine()
    header = header_lines(command, commit, datasets)
    dataset_ct = datasets[0] if datasets else "CT"
    grid = grid_label(vol, dataset_ct)
    vmm3 = float(np.prod(vol.spacing))
    gt_centers = np.asarray(res.seeds.centers_ras, float)
    gt_axes = np.asarray(res.seeds.axes_ras, float)

    # references
    warns = check_references(rs, ct=vol, rtdose=dv)
    print("check_references: %s" % ("no warnings" if not warns else "; ".join(warns)))
    print("RTDOSE: %s, %s units -> cGy, summation %s, grid %s @ %.2fx%.2fx%.3f mm, max %.0f cGy, "
          "DVHSequence %s" % (dv.meta.get("dose_type"), dv.meta.get("dose_units_original"),
                               dv.meta.get("dose_summation_type"), dv.array.shape,
                               dv.spacing[0], dv.spacing[1], dv.spacing[2], float(dv.array.max()),
                               "present" if dv.meta.get("dvh_sequence") else "absent"))

    # structures
    names = [n for n in ("Cav_Post", "CTV_GT", "Brain", "Seeds") if n in rs.roi_names]
    t0 = time.perf_counter()
    masks = rasterize_rois(rs, names, vol)
    print("TPS structures on the CT grid (%.1f s):" % (time.perf_counter() - t0))
    for n in names:
        print("  %-9s %8.2f cc" % (n, masks[n].sum() * vmm3 / 1000.0))
    cav = res.cavity_mask
    print("gtcore cavity %.1f cc: Dice vs Cav_Post %.3f, covers %.0f%% of it, %.0f%% of ours inside it"
          % (cav.sum() * vmm3 / 1000.0, dice_masks(cav, masks["Cav_Post"]),
             100.0 * (cav & masks["Cav_Post"]).sum() / max(1, masks["Cav_Post"].sum()),
             100.0 * (cav & masks["Cav_Post"]).sum() / max(1, cav.sum())))

    # seeds
    cs_ras, cs_axes, _lab, n_cs = tps_seed_contours(rs, vol, masks.get("Seeds"))
    plan_src = plan_sources_from_rtdose(dv)
    residual_rows, match_rows, match_summary = seed_match_tables(vol, res, cs_ras, dv, plan_src)
    src = build_tps_sources(plan_src, cs_ras, cs_axes, gt_centers, gt_axes)
    n_contour = int((src["origin"] == "contour").sum())
    print("  32-source model: %d plan sources; %d take a contoured seed's PCA axis (maxima-to-centroid "
          "distance median %.2f, max %.2f mm), %d uncontoured take the matched detection's axis"
          % (len(plan_src), n_contour,
             np.nanmedian(src["contour_dist"][src["origin"] == "contour"]) if n_contour else np.nan,
             np.nanmax(src["contour_dist"][src["origin"] == "contour"]) if n_contour else np.nan,
             len(plan_src) - n_contour))

    targets, masks = build_targets(vol, rs, res, gt_centers, masks)
    ctv = targets[0][1]
    print_structure_table(targets, ctv)

    # matrix
    columns = dose_columns(dv, src, gt_centers, gt_axes, cs_ras, cs_axes, engine=eng, axes=axes)
    t0 = time.perf_counter()
    cells, doses = structure_dose_matrix(targets, columns, rx_cgy, ctv=ctv, verbose=verbose)
    print("  (matrix %.1f s)" % (time.perf_counter() - t0))

    # diagnostics: implied S_K, delivered fraction, hybrid-position variant
    print("diagnostics:")
    ratio = dose_ratio_check(dv, ctv, src, rx_cgy, engine=eng)
    d_h = target_doses_exact(ctv, src["hybrid_centers"], src["axes"], SK_NOMINAL_U, engine=eng)
    st_h = dose_stats(d_h, rx_cgy, voxel_volume_mm3=ctv.voxel_volume_mm3)
    print("  variant (diagnostic, not a table column): 32 TPS sources with the contour centroid in "
          "place of the RTDOSE maximum where matched -> HRCTV_PHYS D90 %.0f cGy / V100 %.1f %% "
          "(maxima positions: %.0f / %.1f)" % (st_h["D90"], 100 * st_h["V100"],
                                               cells[(ROW_HRCTV, COL_TPS32)]["D90"],
                                               100 * cells[(ROW_HRCTV, COL_TPS32)]["V100"]))
    ratio_si = None
    if with_si:
        # the TPS convention: the same 32 sources, every axis along RAS z
        src_si = dict(src, axes=si_axes(src["centers"].shape[0]))
        ratio_si = dose_ratio_check(dv, ctv, src_si, rx_cgy, engine=eng, label="32 TPS sources, S-I axes")
        print("  S-I axes vs physical axes on %s: %s D90 %.0f vs %.0f cGy, V100 %.1f vs %.1f %%; "
              "%s D90 %.0f vs %.0f cGy, V100 %.1f vs %.1f %%"
              % (ROW_HRCTV, COL_TPS32_SI, cells[(ROW_HRCTV, COL_TPS32_SI)]["D90"],
                 cells[(ROW_HRCTV, COL_TPS32)]["D90"], 100 * cells[(ROW_HRCTV, COL_TPS32_SI)]["V100"],
                 100 * cells[(ROW_HRCTV, COL_TPS32)]["V100"], COL_GT32_SI,
                 cells[(ROW_HRCTV, COL_GT32_SI)]["D90"], cells[(ROW_HRCTV, COL_GT32)]["D90"],
                 100 * cells[(ROW_HRCTV, COL_GT32_SI)]["V100"], 100 * cells[(ROW_HRCTV, COL_GT32)]["V100"]))

    # TPS check
    tps_status, tps_rows = tps_volume_check(dv, rs, rx_cgy, mim_dvh=mim_dvh)
    tps_check = {"pass": "pass", "fail": "FAIL", "pending": "TPS volume check pending"}[tps_status]

    hooks = optional_hooks(verbose=verbose)

    # --- files
    paths = {}
    extra = [
        {"row": "diagnostic", "structure": ctv.describe(), "dose_source": "RTDOSE / %s" % COL_TPS32,
         "metric": "ratio_median_far_from_sources", "value": ratio["ratio_median"], "unit": "-",
         "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "diagnostic", "structure": ctv.describe(), "dose_source": "%s / RTDOSE" % COL_TPS32,
         "metric": "implied_SK_not_used", "value": ratio["implied_sk_u"], "unit": "U",
         "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "diagnostic", "structure": "-", "dose_source": "-", "metric": "delivered_fraction_conclusion",
         "value": ratio["conclusion"], "unit": "-", "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "diagnostic", "structure": ctv.describe(), "dose_source": COL_TPS32 + " (hybrid positions)",
         "metric": "D90", "value": st_h["D90"], "unit": "cGy", "grid": grid, "flag": FLAG,
         "tps_check": tps_check},
        {"row": "seed match", "structure": "Seeds (contoured) vs gtcore", "dose_source": "-",
         "metric": "n_matched_of_contoured", "value": "%d/%d" % (match_summary["n_matched"], match_summary["n_tps"]),
         "unit": "-", "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "seed match", "structure": "Seeds (contoured) vs gtcore", "dose_source": "-",
         "metric": "match_median_mm", "value": match_summary["match_median_mm"], "unit": "mm",
         "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "seed match", "structure": "Seeds (contoured) vs gtcore", "dose_source": "-",
         "metric": "match_max_mm", "value": match_summary["match_max_mm"], "unit": "mm",
         "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "seed match", "structure": "plan sources (RTDOSE maxima) vs gtcore", "dose_source": "-",
         "metric": "n_matched_of_sources", "value": "%d/%d" % (match_summary["n_sources_matched"],
                                                                match_summary["n_sources"]),
         "unit": "-", "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "seed match", "structure": "plan sources (RTDOSE maxima) vs gtcore", "dose_source": "-",
         "metric": "source_median_mm", "value": match_summary["source_median_mm"], "unit": "mm",
         "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "seed match", "structure": "gtcore detections", "dose_source": "-",
         "metric": "n_detected", "value": match_summary["n_det"], "unit": "-", "grid": grid,
         "flag": FLAG, "tps_check": "n/a"},
        {"row": "gtcore cavity", "structure": "gtcore:cavity", "dose_source": "-", "metric": "volume_cc",
         "value": cav.sum() * vmm3 / 1000.0, "unit": "cc", "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        {"row": "gtcore cavity", "structure": "gtcore:cavity vs RTSTRUCT:Cav_Post", "dose_source": "-",
         "metric": "dice", "value": dice_masks(cav, masks["Cav_Post"]), "unit": "-", "grid": grid,
         "flag": FLAG, "tps_check": "n/a"},
    ]
    if ratio_si is not None:
        extra[2:2] = [
            {"row": "diagnostic", "structure": ctv.describe(), "dose_source": "RTDOSE / %s" % COL_TPS32_SI,
             "metric": "ratio_median_far_from_sources", "value": ratio_si["ratio_median"], "unit": "-",
             "grid": grid, "flag": FLAG, "tps_check": "n/a"},
            {"row": "diagnostic", "structure": ctv.describe(), "dose_source": "%s / RTDOSE" % COL_TPS32_SI,
             "metric": "implied_SK_not_used", "value": ratio_si["implied_sk_u"], "unit": "U",
             "grid": grid, "flag": FLAG, "tps_check": "n/a"},
        ]
    for n in names:
        extra.append({"row": "RTSTRUCT", "structure": "RTSTRUCT:%s" % n, "dose_source": "-",
                      "metric": "volume_cc", "value": masks[n].sum() * vmm3 / 1000.0, "unit": "cc",
                      "grid": grid, "flag": FLAG, "tps_check": "n/a"})
    for r in tps_rows:
        for m in ("vol_mim", "vol_gtcore", "vol_rel_err", "D90_mim", "D90_gtcore", "D90_rel_err"):
            extra.append({"row": "verify_against_tps", "structure": "RTSTRUCT:%s" % r["roi"],
                          "dose_source": "RTDOSE grid", "metric": m, "value": r[m],
                          "unit": "cc" if m.startswith("vol") and "err" not in m else
                          ("cGy" if m.startswith("D90") and "err" not in m else "-"),
                          "grid": "RTDOSE grid", "flag": FLAG, "tps_check": "pass" if r["pass"] else "FAIL"})
    for h, v in hooks.items():
        extra.append({"row": "hook", "structure": "-", "dose_source": "-", "metric": h, "value": v,
                      "unit": "-", "grid": "-", "flag": FLAG, "tps_check": "n/a"})
    rows3 = table3_rows(targets, columns, cells, grid, tps_check, extra_rows=extra)
    p = os.path.join(out_dir, "clinical_table3_rows.csv")
    t3_extra = ["# S_K %.2f U nominal in every gtcore cell; implied S_K is a diagnostic line only" % SK_NOMINAL_U,
                "# tps_check: verify_against_tps status (pass / FAIL / 'TPS volume check pending' when no MIM reference)"]
    if with_si:
        t3_extra.append("# axes: dose_source columns without a suffix use the physical (PCA) seed axes; "
                        "'(S-I axes)' = every seed axis along RAS z, the TPS convention (formalism check)")
    write_csv(p, rows3, TABLE3_COLUMNS, header + t3_extra)
    paths["table3_csv"] = p

    p = os.path.join(out_dir, "clinical_seed_residuals.csv")
    write_csv(p, residual_rows, RESIDUAL_COLUMNS,
              header + ["# per contoured seed matched one-to-one (gate %g mm): gtcore - TPS contour centroid, RAS mm"
                        % SEED_GATE_MM])
    paths["residuals_csv"] = p
    p = os.path.join(out_dir, "clinical_seed_match.csv")
    write_csv(p, match_rows, SEED_MATCH_COLUMNS,
              header + ["# every contoured seed with its one-to-one gtcore match (gate %g mm); RAS mm" % SEED_GATE_MM])
    paths["seed_match_csv"] = p
    p = os.path.join(out_dir, "clinical_source_match.csv")
    write_csv(p, source_rows(src, gt_centers), SOURCE_MATCH_COLUMNS,
              header + ["# the plan's sources = RTDOSE local maxima > %g Gy merged at %g mm; origin = contour when a "
                        "contoured seed matched within %g mm (its PCA axis is used), else rtdose-max (matched "
                        "detection's axis); gt_index = one-to-one gtcore detection within %g mm"
                        % (SOURCE_GY, SOURCE_MERGE_MM, SOURCE_GATE_MM, SOURCE_GATE_MM)])
    paths["source_match_csv"] = p

    panel_paths, slice_info = export_panel_data(out_dir, vol, rs, dv, res, masks, targets, cells, doses,
                                                src, cs_ras, cs_axes, header, dataset_ct, engine=eng,
                                                rx_cgy=rx_cgy, verbose=verbose)
    paths.update(panel_paths)
    if verbose:
        for key in ("table3_csv", "residuals_csv", "seed_match_csv", "source_match_csv"):
            print("  wrote %s" % paths[key])
    return {"targets": targets, "cells": cells, "doses": doses, "sources": src, "cs_ras": cs_ras,
            "cs_axes": cs_axes, "residual_rows": residual_rows, "match_summary": match_summary,
            "ratio": ratio, "ratio_si": ratio_si, "axes": axes, "tps_status": tps_status,
            "tps_rows": tps_rows, "hooks": hooks, "paths": paths, "slice": slice_info, "masks": masks}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ct", help="CT series directory (PT-CT1mm) or a single image file")
    ap.add_argument("rtstruct", help="RTSTRUCT file or directory (PT-RT)")
    ap.add_argument("rtdose", help="RTDOSE file or directory (PT-RT)")
    ap.add_argument("--rx", type=float, default=RX_DEFAULT_CGY, help="prescription [cGy]")
    ap.add_argument("--out", default=os.path.join("docs", "figures", "data"),
                    help="output directory for the CSV/NPZ panel data")
    ap.add_argument("--mim-dvh", default=None, help="MIM DVH / plan export for verify_against_tps")
    ap.add_argument("--datasets", default=",".join(DEFAULT_DATASETS),
                    help="dataset ids for the CSV headers (comma separated)")
    ap.add_argument("--axes", default="physical",
                    help="seed-axis convention: 'physical' (default; PCA axes, the pipeline result) or "
                         "'si' -- adds the TPS-convention columns (every seed axis along RAS z, the "
                         "patient S-I direction; formalism check) and the S-I implied-S_K diagnostic "
                         "line next to the physical ones, which are always written")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    datasets = tuple(s.strip() for s in args.datasets.split(",") if s.strip())
    commit = git_commit()
    command = sanitized_command(args, datasets)
    print("gtcore.__file__ = %s" % gtcore.__file__)
    print("git commit: %s" % commit)
    print("command: %s" % command)
    print("S_K %.2f U nominal for every seed (never fitted); rx %.0f cGy" % (SK_NOMINAL_U, args.rx))
    axes = parse_axes(args.axes)
    print("seed axes: %s%s" % (", ".join(axes),
                               " (si = every seed axis along RAS z, the TPS convention; physical "
                               "columns are always written)" if "si" in axes else " (PCA axes)"))

    t0 = time.perf_counter()
    vol = load_volume(args.ct)
    print("CT: %s voxels @ %.2fx%.2fx%.2f mm (%.1f s)" % (vol.array.shape, vol.spacing[0], vol.spacing[1],
                                                        vol.spacing[2], time.perf_counter() - t0))
    t0 = time.perf_counter()
    res = reconstruct(vol, verbose=False)
    print("reconstruct: %d seeds, cavity %.1f cc (%.1f s)"
          % (len(res.seeds), res.cavity_mask.sum() * float(np.prod(vol.spacing)) / 1000.0,
             time.perf_counter() - t0))
    rs = load_rtstruct(args.rtstruct)
    dv = load_rtdose(args.rtdose)
    out_dir = args.out if os.path.isabs(args.out) else os.path.join(REPO_ROOT, args.out)
    run_analysis(vol, rs, dv, res, args.rx, out_dir, command, commit, datasets=datasets,
                 mim_dvh=args.mim_dvh, axes=axes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
