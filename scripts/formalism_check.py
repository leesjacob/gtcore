"""Formalism check of the TPS dose grid: 1D or 2D, which axes, which dataset.

Runs the angular-symmetry test of ``gtcore.dose.formalism`` on the clinical
RTDOSE (``PT-RT``) with the 32 plan sources read off the dose grid (its local
maxima) and gtcore's PCA seed axes from the thin-cut CT (``PT-CT1mm``), then
re-runs it as a set of labelled variants that each test one candidate cause
of the residual angular trend:

* ``pred_*``   -- what the ratio *would* be under another model, on the same
  voxels (1D point-source; 2D with every axis along RAS z);
* ``a_*``      -- anisotropy dataset (TG-43U1S2 consensus vs CLRP v2);
* ``b*_*``     -- seed-axis definition (sign flip, +-10/20 deg perturbation,
  axes fitted to the RTDOSE, RTSTRUCT ``Seeds`` PCA, all along z, RTPLAN
  orientations when a plan file is given);
* ``c_*``      -- RTDOSE grid averaging (gtcore averaged over each voxel);
* ``d_*``      -- a flat S_K scale;
* ``e_*``      -- source positions (gtcore CT centres instead of grid maxima);
* ``H*_*``     -- the four hypotheses with sub-voxel source positions fitted
  on the RTDOSE 2-6 mm shells (2D + PCA axes, 2D + z axes, 1D, 2D + per-seed
  fitted axes), ranked by the scatter of log(RTDOSE / model).

Usage::

    python scripts/formalism_check.py "$GT_DATA_ROOT/<PT-CT1mm>" \\
        "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>" \\
        [--rtplan "$GT_DATA_ROOT/<PT-RT RTPLAN>"] [--sk 3.5] [--out docs/figures/data]

Writes ``formalism_angular_ratio.csv`` (variant, bin, mean, sd, median, n),
``formalism_variants_summary.csv`` (one row per variant: n, overall mean,
slope, RMS of log ratio, ...) and ``formalism_axis_comparison.csv`` (per
source: gtcore PCA axis vs RTDOSE-fitted, RTSTRUCT and RTPLAN axes), each
with a header comment naming the script, the command, the commit and the
dataset ids; prints the tables that docs/paper2-phase-notes.md quotes.
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time

import numpy as np
from scipy import ndimage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gtcore                                                       # noqa: E402
from gtcore.dose import TG43Engine                                  # noqa: E402
from gtcore.dose.formalism import (ANGLE_BINS_DEG, angular_symmetry_test,  # noqa: E402
                                   compare_axes, fit_common_axis,
                                   fit_sources_to_reference,
                                   match_sources_to_seeds, perturb_axes,
                                   prediction_rows, rtdose_source_positions)
from gtcore.io import load_volume                                   # noqa: E402
from gtcore.io.rt import (check_references, load_rtdose, load_rtplan,  # noqa: E402
                          load_rtstruct, rasterize_rois)
from gtcore.pipeline import reconstruct                             # noqa: E402

DATASETS = "PT-CT1mm (thin-cut CT), PT-RT (RTSTRUCT + RTDOSE)"
Z_AXIS = np.array([0.0, 0.0, 1.0])


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       cwd=os.path.dirname(os.path.dirname(
                                           os.path.abspath(__file__))),
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:                                   # pragma: no cover
        return "unknown"


def public_command(args):
    """The command line with dataset ids in place of the machine paths."""
    cmd = ("python scripts/formalism_check.py \"$GT_DATA_ROOT/<PT-CT1mm>\" "
           "\"$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>\" \"$GT_DATA_ROOT/<PT-RT RTDOSE dir>\"")
    if args.rtplan:
        cmd += " --rtplan \"$GT_DATA_ROOT/<PT-RT RTPLAN>\""
    if args.sk != 3.5:
        cmd += " --sk %g" % args.sk
    return cmd


def write_csv(path, header_lines, fieldnames, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for line in header_lines:
            fh.write("# %s\n" % line)
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: fmt_cell(r.get(k)) for k in fieldnames})


def fmt_cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return "nan" if np.isnan(v) else "%.6g" % v
    return v


def roi_seed_axes(rtstruct, ct):
    """Centres and PCA axes of the RTSTRUCT ``Seeds`` contours on the CT grid."""
    S = rasterize_rois(rtstruct, ("Seeds",), ct)["Seeds"]
    lab, n = ndimage.label(S)
    if n == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    cs = np.array(ndimage.center_of_mass(S, lab, range(1, n + 1)))[:, ::-1]
    centers = ct.index_to_ras(cs)
    axes = []
    for i in range(1, n + 1):
        q = ct.index_to_ras(np.argwhere(lab == i)[:, ::-1].astype(float))
        q = q - q.mean(axis=0)
        _w, v = np.linalg.eigh(q.T @ q)
        axes.append(v[:, -1])
    return centers, np.asarray(axes)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ct")
    ap.add_argument("rtstruct")
    ap.add_argument("rtdose")
    ap.add_argument("--rtplan", default=None, help="RTPLAN file/dir (optional)")
    ap.add_argument("--sk", type=float, default=3.5, help="nominal S_K per seed [U]")
    ap.add_argument("--out", default=os.path.join("docs", "figures", "data"))
    ap.add_argument("--supersample", type=int, nargs=3, default=(3, 3, 5),
                    metavar=("NI", "NJ", "NK"), help="sub-samples per voxel axis (variant c)")
    ap.add_argument("--r-min", type=float, default=3.0)
    ap.add_argument("--r-max", type=float, default=15.0)
    ap.add_argument("--dominance", type=float, default=0.5)
    ap.add_argument("--no-fit", action="store_true", help="skip the RTDOSE axis/position fits")
    args = ap.parse_args(argv)
    t_start = time.time()
    commit = git_commit()
    print("gtcore.__file__ = %s   commit %s" % (gtcore.__file__, commit))

    # ---------------------------------------------------------------- inputs
    ct = load_volume(args.ct)
    res = reconstruct(ct, verbose=False)
    gt_c = np.asarray(res.seeds.centers_ras, float)
    gt_a = np.asarray(res.seeds.axes_ras, float)
    rs = load_rtstruct(args.rtstruct)
    dv = load_rtdose(args.rtdose)
    plan = load_rtplan(args.rtplan) if args.rtplan else None
    warns = check_references(rs, ct=ct, rtdose=dv, rtplan=plan)
    print("CT %s %s mm; RTDOSE %s %s mm, max %.0f cGy, units %s; gtcore seeds %d; "
          "reference warnings: %s" % (ct.array.shape, np.round(ct.spacing, 3), dv.array.shape,
                                      np.round(dv.spacing, 3), dv.array.max(),
                                      dv.meta.get("dose_units_original"), len(gt_c),
                                      warns or "none"))
    pos = np.unique(dv.array[dv.array > 0])
    quant = float(np.min(np.diff(pos))) if pos.size > 1 else float("nan")
    print("RTDOSE value quantisation: %.1f cGy" % quant)

    src = rtdose_source_positions(dv)
    idx, dist = match_sources_to_seeds(src, gt_c, gate_mm=3.0)
    print("plan sources (RTDOSE maxima > 200 Gy, merged at 3 mm): %d; matched to gtcore seeds "
          "within 3 mm: %d; match distance median %.2f, max %.2f mm"
          % (len(src), int((idx >= 0).sum()), np.nanmedian(dist), np.nanmax(dist)))
    if not np.all(idx >= 0):
        print("WARNING: %d sources without a gtcore seed; they are dropped" % int((idx < 0).sum()))
        src = src[idx >= 0]
        dist = dist[idx >= 0]
        idx = idx[idx >= 0]
    axes = gt_a[idx]                     # gtcore PCA axis per plan source
    gtc = gt_c[idx]                      # gtcore CT centre per plan source
    n_src = len(src)
    zax = np.tile(Z_AXIS, (n_src, 1))

    roi_c, roi_a = roi_seed_axes(rs, ct)
    ridx, _rd = match_sources_to_seeds(src, roi_c, gate_mm=3.0)
    has_roi = ridx >= 0
    roi_axes_per_src = np.full((n_src, 3), np.nan)
    roi_axes_per_src[has_roi] = roi_a[ridx[has_roi]]
    print("RTSTRUCT Seeds contours: %d; matched to sources: %d" % (len(roi_c), int(has_roi.sum())))

    plan_axes_per_src = np.full((n_src, 3), np.nan)
    if plan is not None:
        pidx, pd = match_sources_to_seeds(src, plan.positions_ras, gate_mm=3.0)
        ori = plan.orientations_ras
        print("RTPLAN: %d sources, orientations %s; matched to maxima: %d (median %.2f mm)"
              % (plan.n_sources, "present" if ori is not None else "ABSENT",
                 int((pidx >= 0).sum()), np.nanmedian(pd)))
        if ori is not None:
            ok = pidx >= 0
            plan_axes_per_src[ok] = ori[pidx[ok]]

    cons, clrp = TG43Engine("tg43u1s2"), TG43Engine("clrp_v2")
    sel = dict(r_min_mm=args.r_min, r_max_mm=args.r_max, min_fraction=args.dominance)
    ss = tuple(int(v) for v in args.supersample)

    results = []       # (key, bins_by, AngularRatioResult or (label, bins), description)

    def run(key, bins_by, desc, centers, ax, **kw):
        kw.setdefault("engine", cons)
        kw.setdefault("sk_per_seed_u", args.sk)
        for k, v in sel.items():
            kw.setdefault(k, v)
        r = angular_symmetry_test(dv, centers, ax, label=key, **kw)
        results.append((key, bins_by, r, desc))
        print("\n== %s  [%s]\n   bins by angle to the %s\n%s" % (key, desc, bins_by, r.table()))
        return r

    def pred(key, bins_by, desc, base, centers, ax=None, calc="1D", engine=None, supersample=None):
        rows = prediction_rows(base, centers, ax, args.sk, engine or cons, calc=calc,
                               supersample=supersample)
        results.append((key, bins_by, rows, desc))
        print("\n== %s  [%s]\n   " % (key, desc)
              + "  ".join("%g-%g: %.3f" % (b["bin_lo"], b["bin_hi"], b["mean"]) for b in rows))
        return rows

    # ------------------------------------------------- 1. the rev 3 baseline
    base = run("baseline", "gtcore PCA axis",
               "2D TG-43U1S2, RTDOSE maxima positions, gtcore PCA axes (rev 3 test)",
               src, axes)
    pred("pred_1D", "gtcore PCA axis",
         "prediction: D_1D / D_2D on the baseline voxels = ratio a 1D plan would give", base, src)
    pred("pred_2D_axes_z", "gtcore PCA axis",
         "prediction: D_2D(all axes along z) / D_2D(PCA axes) on the baseline voxels",
         base, src, zax, calc="2D")
    run("ratio_vs_1D", "gtcore PCA axis", "RTDOSE / gtcore-1D (point source, phi_an)", src, axes,
        calc="1D")

    # ------------------------------------------------- 2. candidate causes
    run("a_clrp_v2", "gtcore PCA axis", "(a) anisotropy dataset: CLRP v2 F(r,theta) and g_L fit",
        src, axes, engine=clrp)
    run("b1_axes_flipped", "gtcore PCA axis", "(b) axis sign flipped (identical by symmetry)",
        src, -axes)
    for ang in (10.0, 20.0):
        run("b2_axes_perturbed_%ddeg" % int(ang), "perturbed axis",
            "(b) every PCA axis rotated %g deg about a random perpendicular (rng 0)" % ang,
            src, perturb_axes(axes, ang, rng=0))
    ok30 = has_roi
    if ok30.sum() >= 4:
        run("b4_axes_rtstruct_pca", "RTSTRUCT Seeds PCA axis",
            "(b) axes = PCA of the RTSTRUCT Seeds contours (%d contoured sources)" % int(ok30.sum()),
            src[ok30], roi_axes_per_src[ok30])
        run("baseline_same_sources", "gtcore PCA axis",
            "baseline restricted to the %d contoured sources" % int(ok30.sum()), src[ok30], axes[ok30])
        print("gtcore PCA vs RTSTRUCT Seeds PCA axes: %s" % {
            k: round(v, 1) for k, v in compare_axes(axes[ok30], roi_axes_per_src[ok30]).items()
            if k != "angles_deg"})
    run("b5_axes_all_z", "RAS z axis", "(b) every axis along RAS z (TPS default-orientation hypothesis)",
        src, zax)
    if plan is not None and np.isfinite(plan_axes_per_src).all(axis=1).any():
        okp = np.isfinite(plan_axes_per_src).all(axis=1)
        run("b6_axes_rtplan", "RTPLAN orientation", "(b) axes = RTPLAN ControlPointOrientation",
            src[okp], plan_axes_per_src[okp])
    run("c_voxel_averaged", "gtcore PCA axis",
        "(c) gtcore averaged over each %dx%dx%d-sub-sampled RTDOSE voxel" % ss, src, axes,
        supersample=ss)
    run("d_sk_x1.07", "gtcore PCA axis", "(d) flat S_K x 1.07 (scale only)", src, axes,
        sk_per_seed_u=args.sk * 1.07)
    run("e_positions_gtcore", "gtcore PCA axis", "(e) source positions = gtcore CT centres", gtc, axes)
    run("baseline_dominance30", "gtcore PCA axis", "baseline with dominance > 30 % (robustness)",
        src, axes, min_fraction=0.3)
    run("b5_axes_all_z_dominance30", "RAS z axis", "(b) all axes along z, dominance > 30 %",
        src, zax, min_fraction=0.3)

    # --------------------------------------- 3. RTDOSE axis / position fits
    hyp_rows = []
    c_pca_z = compare_axes(axes, zax)
    c_pca_roi = compare_axes(axes, roi_axes_per_src)
    c_pca_plan = compare_axes(axes, plan_axes_per_src)
    nan_arr = np.full(n_src, np.nan)
    fit_cols = {"pca_vs_rtdose_axisfit_deg": nan_arr, "pca_vs_rtdose_jointfit_deg": nan_arr,
                "jointfit_vs_z_deg": nan_arr, "jointfit_shift_mm": nan_arr,
                "rms_log_pca": nan_arr, "rms_log_jointfit": nan_arr,
                "n_shell_voxels": np.full(n_src, -1)}
    fit_summary = None
    print("gtcore PCA axes vs RAS z: median %.1f deg; vs RTSTRUCT Seeds PCA: median %.1f max %.1f deg "
          "(n %d); vs RTPLAN orientations: %s"
          % (c_pca_z["median"], c_pca_roi["median"], c_pca_roi["max"], c_pca_roi["n"],
             ("median %.1f max %.1f deg (n %d)" % (c_pca_plan["median"], c_pca_plan["max"],
                                                   c_pca_plan["n"]))
             if c_pca_plan["n"] else "n/a (no RTPLAN given)"))
    if not args.no_fit:
        t0 = time.time()
        common = fit_common_axis(dv, src, axes, args.sk, engine=cons)
        print("\ncommon axis fitted to all shells: %s, %.1f deg from RAS z; RMS(log) common %.4f, "
              "z %.4f, PCA axes %.4f; n %d (%.1fs)"
              % (np.round(common["axis"], 3), common["angle_to_z_deg"], common["rms"],
                 common["rms_z"], common["rms_given"], common["n_points"], time.time() - t0))
        fits_axis = fit_sources_to_reference(dv, src, axes, args.sk, engine=cons,
                                             fit_position=False, fit_axis=True)
        fa = np.array([f.axis_fitted for f in fits_axis])
        fits_joint = fit_sources_to_reference(dv, src, axes, args.sk, engine=cons,
                                              fit_position=True, fit_axis=True)
        ja = np.array([f.axis_fitted for f in fits_joint])
        jc = np.array([f.center_fitted for f in fits_joint])
        c_pca_fit = compare_axes(axes, fa)
        c_pca_joint = compare_axes(axes, ja)
        c_joint_z = compare_axes(ja, zax)
        print("axis comparison (deg): PCA vs RTDOSE axis-only fit median %.1f max %.1f; PCA vs joint fit "
              "median %.1f max %.1f; joint fit vs z median %.1f p95 %.1f max %.1f"
              % (c_pca_fit["median"], c_pca_fit["max"], c_pca_joint["median"], c_pca_joint["max"],
                 c_joint_z["median"], c_joint_z["p95"], c_joint_z["max"]))
        print("joint fit shifts: median %.2f, max %.2f mm; RMS(log) median %.3f -> %.3f"
              % (np.median([f.shift_mm for f in fits_joint]), max(f.shift_mm for f in fits_joint),
                 np.median([f.rms_initial for f in fits_joint]),
                 np.median([f.rms_fitted for f in fits_joint])))
        fit_cols = {"pca_vs_rtdose_axisfit_deg": c_pca_fit["angles_deg"],
                    "pca_vs_rtdose_jointfit_deg": c_pca_joint["angles_deg"],
                    "jointfit_vs_z_deg": c_joint_z["angles_deg"],
                    "jointfit_shift_mm": np.array([f.shift_mm for f in fits_joint]),
                    "rms_log_pca": np.array([f.rms_initial for f in fits_joint]),
                    "rms_log_jointfit": np.array([f.rms_fitted for f in fits_joint]),
                    "n_shell_voxels": np.array([f.n_points for f in fits_joint])}
        fit_summary = ("summary: PCA vs joint fit median %.1f max %.1f deg; joint fit vs z median %.1f "
                       "p95 %.1f max %.1f deg; PCA vs z median %.1f deg; common fitted axis %.1f deg "
                       "from z (RMS(log) common %.4f, z %.4f, PCA axes %.4f)"
                       % (c_pca_joint["median"], c_pca_joint["max"], c_joint_z["median"],
                          c_joint_z["p95"], c_joint_z["max"], c_pca_z["median"],
                          common["angle_to_z_deg"], common["rms"], common["rms_z"],
                          common["rms_given"]))
        run("b3_axes_fitted_rtdose", "RTDOSE-fitted axis",
            "(b) axes fitted per seed to the RTDOSE 2-6 mm shell (positions = maxima)", src, fa)

        # hypotheses with sub-voxel positions fitted under each model
        hyps = (("H1_2D_pca_axes", "gtcore PCA axis", "2D, gtcore PCA axes", axes, "2D"),
                ("H3_2D_axes_z", "RAS z axis", "2D, all axes along RAS z", zax, "2D"),
                ("H2_1D", "RAS z axis", "1D point source (bins by angle to z for reference)", zax, "1D"),
                ("H4_2D_fitted_axes", "RTDOSE-fitted axis", "2D, per-seed jointly fitted axes", ja, "2D"))
        for key, by, desc, ax, calc in hyps:
            fits = fit_sources_to_reference(dv, src, ax, args.sk, engine=cons,
                                            fit_position=True, fit_axis=False, calc=calc)
            cen = np.array([f.center_fitted for f in fits])
            rms = np.array([f.rms_fitted for f in fits])
            hyp_rows.append({"hypothesis": key, "description": desc,
                             "rms_log_median": float(np.median(rms)), "rms_log_mean": float(rms.mean()),
                             "shift_median_mm": float(np.median([f.shift_mm for f in fits])),
                             "shift_max_mm": float(max(f.shift_mm for f in fits)),
                             "n_sources": n_src})
            r = run(key + "_refined", by, desc + "; positions fitted on the RTDOSE shells (per seed)",
                    cen, ax, calc=calc)
            run(key + "_refined_avg", by, desc + "; refined positions; gtcore voxel-averaged",
                cen, ax, calc=calc, supersample=ss)
            if key == "H3_2D_axes_z":
                run(key + "_refined_avg_clrp", by, desc + "; refined positions; voxel-averaged; CLRP v2",
                    cen, ax, calc=calc, supersample=ss, engine=clrp)
                pred("pred_1D_in_H3_bins", by, "prediction: D_1D / D_2D(z) on the H3 voxels",
                     r, cen, calc="1D")
                implied = args.sk * r.overall_mean
                print("H3 overall RTDOSE/gtcore %.3f -> implied S_K %.2f U at nominal %.2f U"
                      % (r.overall_mean, implied, args.sk))
        print("\nhypothesis ranking by RMS(log RTDOSE/model) after per-seed position fits:")
        for h in sorted(hyp_rows, key=lambda d: d["rms_log_median"]):
            print("  %-20s median %.3f  mean %.3f  shift median %.2f max %.2f mm   %s"
                  % (h["hypothesis"], h["rms_log_median"], h["rms_log_mean"],
                     h["shift_median_mm"], h["shift_max_mm"], h["description"]))

    # ------------------------------------------------------------- outputs
    header = [
        "scripts/formalism_check.py -- angular-symmetry formalism test of the TPS RTDOSE",
        "command: " + public_command(args),
        "commit: %s   gtcore: %s" % (commit, os.path.basename(os.path.dirname(gtcore.__file__))),
        "datasets: " + DATASETS,
        "selection: RTDOSE voxels %.0f-%.0f mm from their dominant seed, dominant seed > %.0f %% of "
        "the gtcore total; bins = angle to the stated axis; ratio = RTDOSE / gtcore (cGy), S_K %.2f U "
        "nominal; RTDOSE quantisation %.0f cGy" % (args.r_min, args.r_max, 100 * args.dominance,
                                                    args.sk, quant),
        "variants: see formalism_variants_summary.csv (description per variant)",
    ]
    out_rows = []
    sum_rows = []
    for key, by, r, desc in results:
        if isinstance(r, list):
            bins = r
            summary = {"variant": key, "bins_by": by, "description": desc, "kind": "prediction"}
        else:
            bins = r.bins
            summary = {"variant": key, "bins_by": by, "description": desc, "kind": "measured",
                       "n": r.n_selected, "overall_mean": r.overall_mean,
                       "overall_median": r.overall_median, "slope_per_90deg": r.slope_per_90deg,
                       "end_to_end": r.end_to_end, "rms_log": r.rms_log,
                       "r_median_mm": r.r_median_mm, "r_max_mm": r.r_max_selected_mm,
                       "dataset": r.params["dataset"], "calc": r.params["calc"],
                       "supersample": ("" if r.params["supersample"] is None
                                       else "x".join(str(v) for v in r.params["supersample"]))}
        sum_rows.append(summary)
        for b in bins:
            out_rows.append({"variant": key, "bins_by": by, "bin_lo_deg": b["bin_lo"],
                             "bin_hi_deg": b["bin_hi"], "mean": b["mean"], "sd": b["sd"],
                             "median": b["median"], "n": b["n"]})
    os.makedirs(args.out, exist_ok=True)
    p1 = os.path.join(args.out, "formalism_angular_ratio.csv")
    write_csv(p1, header, ["variant", "bins_by", "bin_lo_deg", "bin_hi_deg", "mean", "sd",
                           "median", "n"], out_rows)
    p2 = os.path.join(args.out, "formalism_variants_summary.csv")
    write_csv(p2, header + ["rms_log = RMS of log(RTDOSE/gtcore) about its mean over the selected "
                            "voxels (scatter of the fit); slope_per_90deg = least-squares slope of "
                            "the ratio vs angle"],
              ["variant", "kind", "bins_by", "n", "overall_mean", "overall_median",
               "slope_per_90deg", "end_to_end", "rms_log", "r_median_mm", "r_max_mm", "dataset",
               "calc", "supersample", "description"], sum_rows)
    p3 = os.path.join(args.out, "formalism_axis_comparison.csv")
    hdr3 = header[:4] + [
        "per plan source (RTDOSE maximum): angle [deg, 0-90, sign-blind] between the gtcore PCA seed "
        "axis and (i) the axis fitted to the RTDOSE 2-6 mm shell with the position fixed, (ii) the "
        "jointly fitted axis (position + axis), (iii) RAS z; the jointly fitted axis vs RAS z; PCA vs "
        "the RTSTRUCT Seeds-contour PCA axis (nan = uncontoured source); PCA vs the RTPLAN "
        "ControlPointOrientation (nan = no RTPLAN on this machine); joint-fit position shift [mm]; "
        "RMS(log RTDOSE/model) before / after the joint fit; shell voxel count"]
    hdr3.append("summary: PCA vs z median %.1f deg; PCA vs RTSTRUCT Seeds PCA median %.1f max %.1f deg "
                "(n %d); PCA vs RTPLAN %s"
                % (c_pca_z["median"], c_pca_roi["median"], c_pca_roi["max"], c_pca_roi["n"],
                   ("median %.1f max %.1f deg (n %d)" % (c_pca_plan["median"], c_pca_plan["max"],
                                                         c_pca_plan["n"]))
                   if c_pca_plan["n"] else "n/a (no RTPLAN on this machine)"))
    if fit_summary:
        hdr3.append(fit_summary)
    axis_rows = []
    for s in range(n_src):
        row = {"source": s, "gtcore_seed": int(idx[s]), "match_dist_mm": float(dist[s]),
               "pca_vs_z_deg": float(c_pca_z["angles_deg"][s]),
               "pca_vs_rtstruct_deg": float(c_pca_roi["angles_deg"][s]),
               "pca_vs_rtplan_deg": float(c_pca_plan["angles_deg"][s])}
        for k, v in fit_cols.items():
            row[k] = int(v[s]) if k == "n_shell_voxels" else float(v[s])
        axis_rows.append(row)
    write_csv(p3, hdr3, ["source", "gtcore_seed", "match_dist_mm", "pca_vs_rtdose_axisfit_deg",
                         "pca_vs_rtdose_jointfit_deg", "jointfit_vs_z_deg", "pca_vs_z_deg",
                         "pca_vs_rtstruct_deg", "pca_vs_rtplan_deg", "jointfit_shift_mm",
                         "rms_log_pca", "rms_log_jointfit", "n_shell_voxels"], axis_rows)
    if hyp_rows:
        p4 = os.path.join(args.out, "formalism_hypotheses.csv")
        write_csv(p4, header[:4] + ["per hypothesis: source positions fitted per seed on the RTDOSE "
                                    "2-6 mm shells under that model (max shift 1.5 mm); rms_log = RMS "
                                    "of log(RTDOSE/model) about its mean, median / mean over sources"],
                  ["hypothesis", "description", "rms_log_median", "rms_log_mean", "shift_median_mm",
                   "shift_max_mm", "n_sources"], hyp_rows)
    print("\nwrote %s, %s, %s%s  (%.0f s total)" % (p1, p2, p3,
                                                   (", " + p4) if hyp_rows else "",
                                                   time.time() - t_start))
    return 0


if __name__ == "__main__":
    sys.exit(main())
