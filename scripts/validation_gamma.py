"""Gamma agreement of gtcore's TG-43 dose with the TPS RTDOSE on the clinical case.

Paper 2, F5 caption / Table 3 rows "gamma pass rate" and the optional
Supplement gamma-map panel.  Wrapper: ``gtcore.dose.gamma`` over
``pymedphys.gamma`` (cited there).  Reference = the RTDOSE (cGy) on its own
grid inside the bounding box of the voxels >= 30 Gy (+ 5 mm); evaluation =
gtcore ``dose_at_points`` (32 sources, nominal S_K, exact 2D line-source
rate) on a 0.5 mm lattice.  Four criteria combinations (3 %/2 mm and
2 %/1 mm, local and global-to-rx) x two reference windows (30-200 Gy
primary, >= 30 Gy alongside) x three seed sets:

``gtcore``        the 32 seeds ``gtcore.pipeline.reconstruct`` finds on the CT
                  (engine equivalence + localisation error);
``tps_maxima``    the 32 local maxima of the RTDOSE (> 200 Gy, merged at 3 mm),
                  at voxel centres of the 1 x 1 x 1.667 mm dose grid -- the
                  requirements' "TPS sources" (engine equivalence only, but
                  quantised to the dose grid; see the match table printed);
``tps_contours``  the 30 ``Seeds`` contour centroids of the RTSTRUCT plus the
                  two uncontoured sources taken from the maxima (the TPS's own
                  sub-voxel seed positions where it has them).
Seed axes for the TPS sets are the gtcore axes of the one-to-one matched
gtcore seed (Hungarian, gate 3 mm; the nearest gtcore seed otherwise).

Seed-axis convention (``--axes``, Phase 3 ``formalism`` result: the TPS
computed every source with its long axis along the patient S-I direction):

``physical``      the PCA long axis of each seed (default; the pipeline result,
                  the dose the implant delivers under TG-43);
``si``            every seed axis set to RAS (0, 0, 1) -- the TPS convention,
                  i.e. the like-for-like implementation-equivalence check (the
                  sign is irrelevant for the symmetric anisotropy function).
``--axes physical,si`` runs both and writes one table with an ``axes`` column;
the ``physical`` rows and files are exactly those of a ``physical``-only run,
the ``si`` per-run files carry the suffix ``_siaxes``.

usage::

    python scripts/validation_gamma.py "$GT_DATA_ROOT/<PT-CT1mm>" \\
        "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>" \\
        [--sk 3.5] [--eval-spacing 0.5] [--criteria 3/2,2/1] \\
        [--seed-sources gtcore,tps_maxima,tps_contours] [--axes physical,si] \\
        [--out docs/figures/data] [--full-npz output/gamma] [--interp-algo auto]

Writes to ``--out`` (default ``docs/figures/data``, header comments name the
script, the command, the commit and the dataset ids):

* ``gamma_pass_rates.csv``      one row per axes convention x seed set x criteria x normalisation x window;
* ``gamma_pass_by_band.csv``    pass rate per reference dose band (post-hoc windows);
* ``gamma_histogram_<set>_<pct>pct_<mm>mm_<norm>[_siaxes].csv``  counts in 0.1 bins, 0-2+;
* ``gamma_map_slice_<set>_<pct>pct_<mm>mm_<norm>[_siaxes].npz`` one axial slice (gamma,
  reference, gtcore dose, masks, mm axes) for the Supplement panel, 3 %/2 mm only;
* ``gamma_capsule_diagnostics.csv``  the > 200 Gy voxels: count, fraction, distance
  to the nearest seed, RTDOSE vs gtcore at the hottest voxel;
* ``gamma_seed_sets.csv``       the seed-set match table (positions vs gtcore / contours).
With ``--full-npz`` the full 3-D gamma / reference / difference boxes go to
that (git-ignored) folder.  Prints a Markdown table for the notes.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import subprocess
import sys
import time

import numpy as np
from scipy import ndimage
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gtcore  # noqa: E402
from gtcore.dose import dose_at_points  # noqa: E402
from gtcore.dose import gamma as gm  # noqa: E402
from gtcore.dose.formalism import match_sources_to_seeds, rtdose_source_positions  # noqa: E402
from gtcore.io import load_volume  # noqa: E402
from gtcore.io.rt import check_references, load_rtdose, load_rtstruct, rasterize_roi  # noqa: E402
from gtcore.pipeline import reconstruct  # noqa: E402

DATASETS = "PT-CT1mm (CT + gtcore seeds), PT-RT (RTSTRUCT Seeds contours, RTDOSE)"
COMMAND = ('python scripts/validation_gamma.py "$GT_DATA_ROOT/<PT-CT1mm>" '
           '"$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>"')
LOWER_CGY, UPPER_CGY, RX_CGY = 3000.0, 20000.0, 6000.0
BANDS_GY = [(30, 60), (60, 100), (100, 200), (200, 400), (400, np.inf), (30, 200), (30, np.inf)]
# seed-axis conventions (module docstring); "si" = the TPS convention found by
# the Phase 3 formalism check: every source axis along the patient S-I
# direction, RAS (0, 0, +-1)
AXES_CONVENTIONS = ("physical", "si")
SI_AXIS_RAS = np.array([0.0, 0.0, 1.0])
AXES_NOTE = ("axes: physical = PCA long axis of each (matched) seed, the pipeline result; "
             "si = every seed axis along RAS z (patient S-I), the TPS convention of the "
             "formalism check -- the like-for-like equivalence row")


# ----------------------------------------------------------------- helpers
def git_state():
    try:
        h = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                               capture_output=True, text=True).stdout.strip()
        return h or "unknown", bool(dirty)
    except Exception:
        return "unknown", False


def header_lines(commit, dirty, extra, command=COMMAND):
    lines = ["# script: scripts/validation_gamma.py",
             "# command: %s" % command,
             "# commit: %s%s" % (commit, " (dirty)" if dirty else ""),
             "# datasets: %s" % DATASETS,
             "# flag: calibration-set (one clinical case, reference not validation cohort)",
             "# date: %s" % _dt.date.today().isoformat()]
    lines += ["# %s" % e for e in extra]
    return lines


def write_csv(path, header, columns, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for line in header:
            f.write(line + "\n")
        f.write(",".join(columns) + "\n")
        for r in rows:
            f.write(",".join(_fmt(r[c]) for c in columns) + "\n")


def _fmt(v):
    if isinstance(v, float):
        if np.isnan(v):
            return "nan"
        return "%.6g" % v if abs(v) < 1e-3 or abs(v) >= 1e6 else "%.4f" % v if v != int(v) else "%d" % int(v)
    if isinstance(v, (list, tuple, np.ndarray)):
        return '"' + "x".join(str(int(x)) for x in v) + '"'
    return str(v)


def hungarian(a, b, gate_mm=3.0):
    """One-to-one match of rows of ``a`` to rows of ``b``; returns (ia, ib, d) within the gate."""
    D = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2)
    r, c = linear_sum_assignment(D)
    ok = D[r, c] <= gate_mm
    return r[ok], c[ok], D[r, c][ok]


def rtdose_maxima(dv, min_cgy=20000.0, merge_mm=3.0):
    """Local maxima of the RTDOSE above ``min_cgy``, greedily merged within
    ``merge_mm`` (``gtcore.dose.formalism.rtdose_source_positions``; the same
    rule the Phase 3 run used inline)."""
    return np.asarray(rtdose_source_positions(dv, min_cgy=min_cgy, merge_mm=merge_mm), float).reshape(-1, 3)


def convention_axes(axes, convention):
    """Seed axes under a convention: ``physical`` returns the given (PCA)
    axes unchanged; ``si`` returns ``SI_AXIS_RAS`` for every seed."""
    axes = np.asarray(axes, float).reshape(-1, 3)
    if convention == "physical":
        return axes
    if convention == "si":
        return np.tile(SI_AXIS_RAS, (axes.shape[0], 1))
    raise ValueError("unknown seed-axis convention %r (choose from %s)" % (convention, AXES_CONVENTIONS))


def set_tag(name, convention="physical"):
    """Seed-set label for file names: the physical convention keeps the Phase 3
    names, ``si`` appends ``_siaxes``."""
    return name if convention == "physical" else "%s_%saxes" % (name, convention)


def file_tag(name, pct, mm, norm, convention="physical"):
    """File stem of one (seed set, criteria, normalisation, axes) run."""
    tag = "%s_%gpct_%gmm_%s" % (name, pct, mm, norm)
    return tag if convention == "physical" else tag + "_%saxes" % convention


def match_stats(a, b, gate_mm=3.0):
    ia, ib, d = hungarian(a, b, gate_mm)
    if len(d) == 0:
        return dict(matched=0, median_mm=np.nan, p95_mm=np.nan, max_mm=np.nan, rms_x=np.nan, rms_y=np.nan, rms_z=np.nan)
    rms = np.sqrt(((a[ia] - b[ib]) ** 2).mean(axis=0))
    return dict(matched=int(len(d)), median_mm=float(np.median(d)), p95_mm=float(np.percentile(d, 95)),
                max_mm=float(d.max()), rms_x=float(rms[0]), rms_y=float(rms[1]), rms_z=float(rms[2]))


def axes_from_gtcore(centers, gt_centers, gt_axes, gate_mm=3.0):
    """Seed axes for TPS source positions: the one-to-one matched gtcore
    seed's axis (``gtcore.dose.formalism.match_sources_to_seeds``, Hungarian
    within the gate), the nearest gtcore seed's axis otherwise."""
    idx, _d = match_sources_to_seeds(centers, gt_centers, gate_mm=gate_mm)
    axes = np.empty_like(centers)
    _, nn = cKDTree(gt_centers).query(centers)
    axes[:] = gt_axes[nn]
    ok = idx >= 0
    axes[ok] = gt_axes[idx[ok]]
    return axes, int(ok.sum())


def seed_centroids(rs, vol, roi="Seeds"):
    m = rasterize_roi(rs, roi, vol.affine, vol.array.shape)
    lab, n = ndimage.label(m)
    if n == 0:
        return np.zeros((0, 3))
    cs = np.array(ndimage.center_of_mass(m, lab, range(1, n + 1)))[:, ::-1]
    return vol.index_to_ras(cs)


def slice_npz(path, result, source, pct, mm, norm, k=None, axes="physical"):
    """One axial slice through the box (the k with most 30-200 Gy voxels unless given)."""
    used = result.mask_used
    if k is None:
        k = int(np.argmax(used.reshape(used.shape[0], -1).sum(axis=1)))
    zs, ys, xs = gm.grid_axes(result.reference_box)
    ev = result.evaluation
    diff = result.difference_volume.array
    np.savez_compressed(
        path, gamma=result.gamma[k], reference_cgy=result.reference_box.array[k],
        gtcore_cgy=(result.reference_box.array[k] + diff[k]),
        mask_window=used[k], mask_above_upper=(result.mask_computed & ~used)[k],
        mask_computed=result.mask_computed[k], k=k, z_mm=zs[k], x_mm=xs, y_mm=ys,
        box_affine=result.box_affine, eval_affine=ev.affine, seed_source=source,
        dose_pct=pct, dist_mm=mm, normalisation=norm, axes=axes,
        settings=json.dumps(result.settings, default=str))
    return k


def run_gamma_table(dv, seed_sets, conventions, criteria, norms, out, full_npz, commit, dirty,
                    command=COMMAND, sk=3.5, eval_spacing=0.5, interp_algo="auto", max_gamma=2.0,
                    search_margin=None, verbose=True):
    """Every axes convention x seed set x criteria x normalisation gamma run.

    ``seed_sets`` maps a set name to ``(centers, physical_axes, n_matched)``;
    the axes actually used are ``convention_axes(physical_axes, convention)``.
    Writes the per-run histogram CSVs, the 3 %/2 mm slice NPZs and (when
    ``full_npz`` is a folder) the full boxes; returns ``(rows, band_rows,
    md_lines)`` for ``gamma_pass_rates.csv`` / ``gamma_pass_by_band.csv``.
    The ``physical`` rows and files are those of a ``physical``-only run.
    """
    if search_margin is None:
        search_margin = max_gamma * max(mm for _, mm in criteria) + eval_spacing
    rows, band_rows, md = [], [], []
    for convention in conventions:
        for name, (c, a_phys, _n) in seed_sets.items():
            a = convention_axes(a_phys, convention)
            ev = gm.prepare_evaluation(dv, c, a, sk, lower_cutoff_cgy=LOWER_CGY, box_margin_mm=5.0,
                                       eval_spacing_mm=eval_spacing, search_margin_mm=search_margin)
            if verbose:
                print("[%s | %s axes] %d seeds | box %s | lattice %s (%d points) | evaluation %.1f s"
                      % (name, convention, len(c), ev.settings["box_shape_kji"], ev.settings["eval_shape_kji"],
                         ev.settings["eval_n_points"], ev.runtime_s))
            if full_npz:
                np.savez_compressed(os.path.join(full_npz, "gamma_box_%s_reference.npz" % set_tag(name, convention)),
                                    reference_cgy=ev.reference_box.array, gtcore_cgy=ev.dose_at_box_centres.reshape(
                                        ev.reference_box.array.shape),
                                    box_affine=ev.reference_box.affine, seed_centers=c, seed_axes=a,
                                    axes=convention)
            for pct, mm in criteria:
                for norm in norms:
                    r = gm.gamma_analysis(dv, dose_pct=pct, dist_mm=mm, lower_cutoff_cgy=LOWER_CGY,
                                          upper_cutoff_cgy=UPPER_CGY, local=(norm == "local"),
                                          global_norm_cgy=RX_CGY, eval_spacing_mm=eval_spacing,
                                          interp_algo=interp_algo, evaluation=ev, max_gamma=max_gamma)
                    tag = file_tag(name, pct, mm, norm, convention)
                    st_used, st_all = r.gamma_stats(r.mask_used), r.gamma_stats(r.mask_computed)
                    common = dict(seed_source=name, axes=convention, n_seeds=len(c), sk_u=sk, dose_pct=pct,
                                  dist_mm=mm, normalisation=norm, global_norm_cgy=RX_CGY,
                                  interp_algo=r.settings["interp_algo_used"], eval_spacing_mm=eval_spacing,
                                  max_gamma=max_gamma, n_unresolved=r.settings["n_unresolved"],
                                  runtime_gamma_s=r.settings["runtime_gamma_s"], runtime_eval_s=ev.runtime_s,
                                  box_shape_kji=r.settings["box_shape_kji"], eval_shape_kji=r.settings["eval_shape_kji"],
                                  flag="calibration-set")
                    rows.append(dict(common, window="30-200Gy", lower_cgy=LOWER_CGY, upper_cgy=UPPER_CGY,
                                     n_points=r.n_points, pass_rate_pct=100 * r.pass_rate,
                                     n_fail=int(round((1 - r.pass_rate) * r.n_points)),
                                     gamma_mean=st_used["mean"], gamma_median=st_used["median"], gamma_p95=st_used["p95"]))
                    rows.append(dict(common, window="ge30Gy", lower_cgy=LOWER_CGY, upper_cgy=np.inf,
                                     n_points=r.n_points_ge30, pass_rate_pct=100 * r.pass_rate_ge30,
                                     n_fail=int(round((1 - r.pass_rate_ge30) * r.n_points_ge30)),
                                     gamma_mean=st_all["mean"], gamma_median=st_all["median"], gamma_p95=st_all["p95"]))
                    for lo, hi in BANDS_GY:
                        pr = r.pass_rate_in(lo * 100.0, hi * 100.0)
                        ref = r.reference_box.array
                        n_b = int((r.mask_computed & (ref >= lo * 100.0) & (ref <= hi * 100.0)).sum())
                        band_rows.append(dict(seed_source=name, axes=convention, dose_pct=pct, dist_mm=mm,
                                              normalisation=norm,
                                              band_gy="%g-%s" % (lo, "inf" if np.isinf(hi) else "%g" % hi),
                                              lower_cgy=lo * 100.0, upper_cgy=hi * 100.0, n_points=n_b,
                                              pass_rate_pct=100 * pr, flag="calibration-set"))
                    edges, counts, counts_all = r.histogram["edges"], r.histogram["counts"], r.histogram["counts_ge30"]
                    write_csv(os.path.join(out, "gamma_histogram_%s.csv" % tag),
                              header_lines(commit, dirty, ["seed_source=%s axes=%s dose_pct=%g dist_mm=%g normalisation=%s; "
                                                           "last bin is gamma >= %g (capped)"
                                                           % (name, convention, pct, mm, norm, max_gamma)], command),
                              ["bin_lo", "bin_hi", "count_30_200Gy", "count_ge30Gy"],
                              [dict(bin_lo=float(edges[i]), bin_hi=float(edges[i + 1]) if np.isfinite(edges[i + 1]) else "inf",
                                    count_30_200Gy=int(counts[i]), count_ge30Gy=int(counts_all[i])) for i in range(len(counts))])
                    if (pct, mm) == criteria[0]:
                        k = slice_npz(os.path.join(out, "gamma_map_slice_%s.npz" % tag), r, name, pct, mm, norm,
                                      axes=convention)
                    else:
                        k = None
                    if full_npz:
                        np.savez_compressed(os.path.join(full_npz, "gamma_box_%s.npz" % tag),
                                            gamma=r.gamma, mask_used=r.mask_used, mask_computed=r.mask_computed,
                                            box_affine=r.box_affine, settings=json.dumps(r.settings, default=str))
                    if verbose:
                        print("  %g %%/%g mm %-6s  30-200 Gy: %.2f %% (n=%d)   >= 30 Gy: %.2f %% (n=%d)   "
                              "gamma mean %.3f  | %.1f s%s"
                              % (pct, mm, norm, 100 * r.pass_rate, r.n_points, 100 * r.pass_rate_ge30, r.n_points_ge30,
                                 st_used["mean"], r.settings["runtime_gamma_s"], "" if k is None else "  (slice k=%d saved)" % k))
                    md.append("| %s | %s | %g %%/%g mm | %s | %.2f | %.2f | %d / %d | %.0f |" % (
                        name, convention, pct, mm, norm, 100 * r.pass_rate, 100 * r.pass_rate_ge30, r.n_points,
                        r.n_points_ge30, r.settings["runtime_gamma_s"]))
    return rows, band_rows, md


PASS_RATE_COLUMNS = ["seed_source", "axes", "n_seeds", "sk_u", "dose_pct", "dist_mm", "normalisation",
                     "global_norm_cgy", "window", "lower_cgy", "upper_cgy", "n_points", "pass_rate_pct", "n_fail",
                     "gamma_mean", "gamma_median", "gamma_p95", "n_unresolved", "runtime_gamma_s", "runtime_eval_s",
                     "interp_algo", "eval_spacing_mm", "max_gamma", "box_shape_kji", "eval_shape_kji", "flag"]
BAND_COLUMNS = ["seed_source", "axes", "dose_pct", "dist_mm", "normalisation", "band_gy", "lower_cgy", "upper_cgy",
                "n_points", "pass_rate_pct", "flag"]


def write_pass_rate_tables(out, rows, band_rows, commit, dirty, command=COMMAND, algo="auto"):
    """``gamma_pass_rates.csv`` and ``gamma_pass_by_band.csv`` from
    :func:`run_gamma_table`'s rows."""
    p1 = os.path.join(out, "gamma_pass_rates.csv")
    write_csv(p1, header_lines(commit, dirty, ["pymedphys %s, interp_algo %s; pass = gamma <= 1; gamma capped at max_gamma; "
                                               "reference = RTDOSE (cGy) on its grid, evaluation = gtcore dose_at_points on the "
                                               "eval_spacing lattice; upper window is a post-hoc voxel selection"
                                               % (gm.pymedphys_version(), algo), AXES_NOTE], command),
              PASS_RATE_COLUMNS, rows)
    p2 = os.path.join(out, "gamma_pass_by_band.csv")
    write_csv(p2, header_lines(commit, dirty, ["pass rate per reference dose band (post-hoc selection of computed voxels)",
                                               AXES_NOTE], command),
              BAND_COLUMNS, band_rows)
    return p1, p2


# -------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ct")
    ap.add_argument("rtstruct")
    ap.add_argument("rtdose")
    ap.add_argument("--sk", type=float, default=3.5, help="nominal S_K per seed [U]")
    ap.add_argument("--eval-spacing", type=float, default=0.5, help="evaluation lattice step [mm]")
    ap.add_argument("--criteria", default="3/2,2/1", help="pct/mm pairs, comma separated")
    ap.add_argument("--seed-sources", default="gtcore,tps_maxima,tps_contours")
    ap.add_argument("--normalisations", default="global,local")
    ap.add_argument("--axes", default="physical",
                    help="seed-axis convention(s), comma separated: 'physical' = the PCA axis of each "
                         "(matched) seed, the pipeline result (default); 'si' = every axis along the "
                         "patient S-I direction, RAS (0, 0, 1) -- the TPS convention found by the "
                         "formalism check, the like-for-like equivalence row. 'physical,si' writes "
                         "one table with both (column 'axes').")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "figures", "data"))
    ap.add_argument("--full-npz", default=os.path.join(ROOT, "output", "gamma"),
                    help="folder for the full 3-D boxes ('' to skip)")
    ap.add_argument("--interp-algo", default="auto", choices=("auto", "pymedphys", "scipy"))
    ap.add_argument("--max-gamma", type=float, default=2.0)
    args = ap.parse_args(argv)

    commit, dirty = git_state()
    algo = gm.resolve_interp_algo(args.interp_algo)
    conventions = [c.strip() for c in args.axes.split(",") if c.strip()]
    for c_ in conventions:
        if c_ not in AXES_CONVENTIONS:
            raise SystemExit("unknown --axes convention %r (choose from %s)" % (c_, ",".join(AXES_CONVENTIONS)))
    command = COMMAND if conventions == ["physical"] else COMMAND + " --axes %s" % ",".join(conventions)
    print("gtcore     :", gtcore.__file__)
    print("commit     :", commit, "(dirty)" if dirty else "(clean)")
    print("pymedphys  :", gm.pymedphys_version(), "| interp_algo:", algo,
          "| python", platform.python_version(), "| numpy", np.__version__)
    print("machine    :", platform.processor() or platform.machine(), "|", os.cpu_count(), "logical CPUs")
    print("axes       :", ", ".join(conventions),
          "(physical = PCA seed axes; si = every axis along RAS z, the TPS convention)")
    os.makedirs(args.out, exist_ok=True)
    if args.full_npz:
        os.makedirs(args.full_npz, exist_ok=True)

    criteria = [tuple(float(x) for x in c.split("/")) for c in args.criteria.split(",")]
    norms = [n.strip() for n in args.normalisations.split(",")]
    sources = [s.strip() for s in args.seed_sources.split(",")]
    search_margin = args.max_gamma * max(mm for _, mm in criteria) + args.eval_spacing

    # ------------------------------------------------------------- inputs
    t0 = time.perf_counter()
    vol = load_volume(args.ct)
    rs = load_rtstruct(args.rtstruct)
    dv = load_rtdose(args.rtdose)
    print("CT %s @ %s mm | RTDOSE %s @ %s mm, %s, max %.0f Gy | load %.1f s"
          % (vol.array.shape, np.round(vol.spacing, 3), dv.array.shape, np.round(dv.spacing, 3),
             dv.meta.get("dose_summation_type"), dv.array.max() / 100, time.perf_counter() - t0))
    for w in check_references(rs, ct=vol, rtdose=dv):
        print("  reference warning:", w)

    t0 = time.perf_counter()
    res = reconstruct(vol, verbose=False)
    gt_c = np.asarray(res.seeds.centers_ras, float)
    gt_a = np.asarray(res.seeds.axes_ras, float)
    print("reconstruct: %d gtcore seeds in %.1f s" % (len(gt_c), time.perf_counter() - t0))

    maxima = rtdose_maxima(dv)
    contours = seed_centroids(rs, vol, "Seeds")
    print("RTDOSE maxima > 200 Gy (merged 3 mm): %d | Seeds contours: %d" % (len(maxima), len(contours)))
    # tps_contours = contour centroids + the maxima that have no contour within 3 mm
    ia, ib, _ = hungarian(maxima, contours, 3.0)
    extra = np.setdiff1d(np.arange(len(maxima)), ia)
    tps_contours = np.vstack([contours, maxima[extra]]) if len(extra) else contours.copy()
    print("tps_contours = %d contour centroids + %d uncontoured maxima" % (len(contours), len(extra)))

    seed_sets = {}
    for name in sources:
        if name == "gtcore":
            seed_sets[name] = (gt_c, gt_a, len(gt_c))
        elif name == "tps_maxima":
            a, nm = axes_from_gtcore(maxima, gt_c, gt_a)
            seed_sets[name] = (maxima, a, nm)
        elif name == "tps_contours":
            a, nm = axes_from_gtcore(tps_contours, gt_c, gt_a)
            seed_sets[name] = (tps_contours, a, nm)
        else:
            raise SystemExit("unknown seed source %r" % name)

    # maxima of gtcore's own dose on the RTDOSE grid: how far a 1 x 1 x 1.667 mm
    # maximum sits from the seed that produced it (explains the tps_maxima offsets)
    slices, box = gm.reference_box(dv, LOWER_CGY, 5.0)
    nk, nj, ni = box.array.shape
    kk, jj, ii = np.indices((nk, nj, ni))
    box_pts = box.index_to_ras(np.stack([ii.ravel(), jj.ravel(), kk.ravel()], 1).astype(float))
    own = dose_at_points(gt_c, gt_a, box_pts, sk_per_seed_u=args.sk).reshape(nk, nj, ni)
    own_vol = type(box)(own, box.affine)
    own_max = rtdose_maxima(own_vol)
    seed_rows = []
    for name, (c, _a, nm) in seed_sets.items():
        row = dict(seed_set=name, n=len(c), axes_from_matched_gtcore=nm)
        for other, oc in (("gtcore", gt_c), ("tps_contours", contours), ("tps_maxima", maxima)):
            s = match_stats(c, oc)
            row.update({"vs_%s_%s" % (other, k): v for k, v in s.items()})
        seed_rows.append(row)
    s_own = match_stats(own_max, gt_c)
    seed_rows.append(dict(seed_set="maxima_of_gtcore_dose_on_rtdose_grid", n=len(own_max), axes_from_matched_gtcore=0,
                          **{"vs_gtcore_%s" % k: v for k, v in s_own.items()}))
    cols = ["seed_set", "n", "axes_from_matched_gtcore"] + sorted(
        {k for r in seed_rows for k in r if k.startswith("vs_")})
    for r in seed_rows:
        for c_ in cols:
            r.setdefault(c_, np.nan)
    write_csv(os.path.join(args.out, "gamma_seed_sets.csv"),
              header_lines(commit, dirty, ["match: one-to-one (Hungarian), gate 3 mm; distances in mm"], command),
              cols, seed_rows)
    print("seed sets (median / max mm vs gtcore seeds): " + "; ".join(
        "%s %.2f / %.2f" % (r["seed_set"], r["vs_gtcore_median_mm"], r["vs_gtcore_max_mm"]) for r in seed_rows))

    # -------------------------------------------------- capsule diagnostics
    arr = np.asarray(dv.array, float)
    ge30 = arr >= LOWER_CGY
    hot = arr > UPPER_CGY
    hot_pts = dv.index_to_ras(np.argwhere(hot)[:, ::-1].astype(float))
    kmax = np.unravel_index(int(np.argmax(arr)), arr.shape)
    pmax = dv.index_to_ras(np.array(kmax[::-1], float))
    cap = dict(n_ref_ge30=int(ge30.sum()), n_ref_above_200=int(hot.sum()),
               fraction_above_200=float(hot.sum() / ge30.sum()),
               n_ref_30_200=int((ge30 & ~hot).sum()),
               rtdose_max_gy=float(arr.max() / 100),
               gtcore_at_rtdose_argmax_gy=float(dose_at_points(gt_c, gt_a, pmax, sk_per_seed_u=args.sk) / 100),
               gtcore_max_on_box_centres_gy=float(own.max() / 100))
    for name, (c, _a, _n) in seed_sets.items():
        d, _ = cKDTree(c).query(hot_pts)
        cap.update({"dist_%s_median_mm" % name: float(np.median(d)),
                    "dist_%s_p95_mm" % name: float(np.percentile(d, 95)),
                    "dist_%s_max_mm" % name: float(d.max()),
                    "dist_%s_within3mm" % name: float((d <= 3.0).mean()),
                    "dist_%s_within4mm" % name: float((d <= 4.0).mean()),
                    "dist_%s_within6mm" % name: float((d <= 6.0).mean())})
    write_csv(os.path.join(args.out, "gamma_capsule_diagnostics.csv"),
              header_lines(commit, dirty, ["> 200 Gy reference voxels: count, fraction of the >= 30 Gy set, "
                                           "distance to the nearest seed of each set; RTDOSE vs gtcore at the hottest voxel "
                                           "(gtcore with the physical seed axes)"], command),
              list(cap.keys()), [cap])
    print("capsule: %d of %d >= 30 Gy voxels (%.1f %%) exceed 200 Gy; distance to nearest gtcore seed "
          "median %.2f, p95 %.2f, max %.2f mm (%.0f %% within 3 mm, %.0f %% within 4 mm); RTDOSE max %.0f Gy "
          "vs gtcore %.0f Gy at that voxel centre"
          % (cap["n_ref_above_200"], cap["n_ref_ge30"], 100 * cap["fraction_above_200"],
             cap.get("dist_gtcore_median_mm", np.nan), cap.get("dist_gtcore_p95_mm", np.nan),
             cap.get("dist_gtcore_max_mm", np.nan), 100 * cap.get("dist_gtcore_within3mm", np.nan),
             100 * cap.get("dist_gtcore_within4mm", np.nan), cap["rtdose_max_gy"], cap["gtcore_at_rtdose_argmax_gy"]))

    # ------------------------------------------------------------- gamma
    rows, band_rows, md = run_gamma_table(
        dv, seed_sets, conventions, criteria, norms, args.out, args.full_npz, commit, dirty, command=command,
        sk=args.sk, eval_spacing=args.eval_spacing, interp_algo=args.interp_algo, max_gamma=args.max_gamma,
        search_margin=search_margin)
    write_pass_rate_tables(args.out, rows, band_rows, commit, dirty, command=command, algo=algo)

    print("\n| seed set | axes | criteria | norm | pass 30-200 Gy (%) | pass >= 30 Gy (%) | n (30-200 / >= 30) | gamma s |")
    print("|---|---|---|---|---|---|---|---|")
    for line in md:
        print(line)
    print("\nwrote", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
