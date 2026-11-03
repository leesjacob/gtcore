"""F5(b) / Table 3: seed-position uncertainty propagated to the HR-CTV dose.

Runs :func:`gtcore.dose.uncertainty.propagate` on ``HRCTV_PHYS`` (the TPS
``CTV_GT`` contour rasterised onto the CT grid) for the clinical case:

* seed sets -- ``gtcore32``: the 32 seeds gtcore detects on the thin-cut CT
  (``reconstruct``), PCA axes; ``contoured30``: the 30 seeds the TPS
  ``Seeds`` ROI contours (connected-component centroids on the CT grid,
  PCA axes) -- the plan has 32 sources, the ROI omits two;
* error bands -- ``residual`` (primary): per-seed perturbations resampled
  from the measured gtcore-vs-contour residual vectors
  (``docs/figures/data/clinical_seed_residuals.csv``; written here from the
  one-to-one match if the file does not exist yet); ``degraded_export``: the
  per-axis Gaussian with SD 0.51 / 0.40 / 0.63 mm measured between the 2 mm
  gapped export and the 1 mm thin-cut; ``covariance_check`` only when the
  pipeline result carries ``seeds.cov_ras`` (default-off refinement);
* nominal S_K for every seed (never fitted), rx 60 Gy, 1000 draws, seeded.

Both measured bands are conservative: their residuals include the TPS
contouring error (and, for the degraded export, the thin-cut's own error).

Usage::

    python scripts/validation_error_propagation.py "<PT-CT1mm dir>" "<PT-RT RTSTRUCT dir>" "<PT-RT RTDOSE dir>"
        [--n-draws 1000] [--seed 0] [--sk 3.5] [--rx 6000] [--workers 4]
        [--seed-sets gtcore32,contoured30] [--residuals docs/figures/data/clinical_seed_residuals.csv]
        [--out docs/figures/data]

Writes (header comments: script, command with dataset ids, commit, dataset ids):
``<out>/f5b_dvh_band.csv`` (band, seed_set, level_cgy, nominal, mean, p2.5,
p97.5; plus the RTDOSE curve as band ``rtdose`` / seed_set ``tps``, nominal
only), ``<out>/uncertainty_d90_v100.csv`` (band, seed_set, statistic,
nominal, mean, sd, p2.5, p97.5, n_draws, seed) and, when absent,
``<out>/clinical_seed_residuals.csv`` (seed_id, dx_mm, dy_mm, dz_mm, dist_mm:
gtcore detection minus contour centroid, RAS mm).  Run from the repo root
with ``PYTHONPATH`` set to it; the script prints ``gtcore.__file__``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys
import time

import numpy as np
from scipy import ndimage
from scipy.optimize import linear_sum_assignment

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gtcore                                                           # noqa: E402
from gtcore.dose.engine import TG43Engine                               # noqa: E402
from gtcore.dose.targets import (hrctv_phys, target_doses_exact,        # noqa: E402
                                 target_doses_from_grid, target_stats)
from gtcore.dose.uncertainty import (DEFAULT_LEVELS_CGY,                # noqa: E402
                                     DEGRADED_EXPORT_SD_MM, AxisGaussian,
                                     Covariance, ResidualResample, dvh_curve,
                                     load_residuals, propagate)
from gtcore.io import load_volume                                       # noqa: E402
from gtcore.io.rt import load_rtdose, load_rtstruct, rasterize_rois     # noqa: E402
from gtcore.pipeline import reconstruct                                 # noqa: E402

SCRIPT = "scripts/validation_error_propagation.py"
DATASETS = "PT-CT1mm (thin-cut CT the RTSTRUCT references), PT-RT (RTSTRUCT CTV_GT + Seeds; RTDOSE)"
MATCH_GATE_MM = 2.0          # one-to-one match gate, as validation_clinical.print_seed_match_detail


def _git_commit() -> str:
    root = os.path.dirname(os.path.abspath(gtcore.__file__))
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                             capture_output=True, text=True, timeout=5.0)
        if out.returncode == 0 and out.stdout.strip():
            dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                                   cwd=root, capture_output=True, text=True, timeout=5.0)
            return out.stdout.strip() + ("+dirty" if dirty.stdout.strip() else "")
    except Exception:
        pass
    return "unknown"


def _public_command(args) -> str:
    """The command with dataset ids in place of the machine paths (never a
    patient folder name in a committed file)."""
    cmd = ('python %s "$GT_DATA_ROOT/<PT-CT1mm>" "$GT_DATA_ROOT/<PT-RT RTSTRUCT>" '
           '"$GT_DATA_ROOT/<PT-RT RTDOSE>" --n-draws %d --seed %d --sk %g --rx %g '
           '--seed-sets %s' % (SCRIPT, args.n_draws, args.seed, args.sk, args.rx,
                               ",".join(args.seed_sets)))
    if args.roi != "CTV_GT":
        cmd += " --roi %s" % args.roi
    return cmd


def _write_csv(path, header_lines, fieldnames, rows):
    import csv
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for line in header_lines:
            fh.write("# %s\n" % line)
        w = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: _fmt(r.get(k, "")) for k in fieldnames})


def _fmt(v):
    """Floats to 6 decimals, trailing zeros dropped (1/70438 voxels needs 5)."""
    if isinstance(v, float):
        if v == 0.0:
            return "0"
        if abs(v) < 1e-4 or abs(v) >= 1e7:
            return "%.6g" % v
        return ("%.6f" % v).rstrip("0").rstrip(".")
    return v


def contoured_seeds(seeds_mask, vol):
    """Centroid and PCA long axis (RAS) of every connected component of the
    rasterised ``Seeds`` ROI -- the TPS-contoured seed set (the code
    ``validation_clinical.py`` uses)."""
    lab, n = ndimage.label(seeds_mask)
    cs = np.array(ndimage.center_of_mass(seeds_mask, lab, range(1, n + 1)))[:, ::-1]
    cs_ras = vol.index_to_ras(cs)
    axes = []
    for i in range(1, n + 1):
        q = vol.index_to_ras(np.argwhere(lab == i)[:, ::-1].astype(float))
        q = q - q.mean(axis=0)
        _w, v = np.linalg.eigh(q.T @ q)
        axes.append(v[:, -1])
    return cs_ras, np.array(axes)


def match_residuals(ours, cs_ras, gate_mm=MATCH_GATE_MM):
    """One-to-one (Hungarian) match of the gtcore detections to the contoured
    seeds within ``gate_mm``; returns ``(rows, idx_ours)`` with rows
    ``(contour_id, detection_id, dx, dy, dz, dist)`` where the residual is
    detection minus contour centroid, RAS mm."""
    D = np.linalg.norm(cs_ras[:, None, :] - ours[None, :, :], axis=2)
    ri, ci = linear_sum_assignment(D)
    ok = D[ri, ci] <= gate_mm
    rows, keep = [], []
    for t, e in zip(ri[ok], ci[ok]):
        d = ours[e] - cs_ras[t]
        rows.append((int(t), int(e), float(d[0]), float(d[1]), float(d[2]), float(D[t, e])))
        keep.append(int(e))
    return rows, np.asarray(keep, dtype=int)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ct", help="PT-CT1mm DICOM directory")
    ap.add_argument("rtstruct", help="PT-RT RTSTRUCT file or directory")
    ap.add_argument("rtdose", help="PT-RT RTDOSE file or directory")
    ap.add_argument("--n-draws", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--sk", type=float, default=TG43Engine.DEFAULT_SK_U, help="nominal S_K per seed [U]")
    ap.add_argument("--rx", type=float, default=6000.0, help="prescription [cGy]")
    ap.add_argument("--roi", default="CTV_GT", help="RTSTRUCT ROI taken as HRCTV_PHYS")
    ap.add_argument("--workers", type=int, default=4, help="threads over draws (results identical)")
    ap.add_argument("--seed-sets", default="gtcore32,contoured30",
                    help="comma list of gtcore32, contoured30, gtcore30 (the detections matched to the contours)")
    ap.add_argument("--residuals", default=os.path.join("docs", "figures", "data", "clinical_seed_residuals.csv"))
    ap.add_argument("--rebuild-residuals", action="store_true", help="overwrite the residuals CSV from this run")
    ap.add_argument("--out", default=os.path.join("docs", "figures", "data"))
    args = ap.parse_args(argv)
    args.seed_sets = [s.strip() for s in args.seed_sets.split(",") if s.strip()]
    for s in args.seed_sets:
        if s not in ("gtcore32", "contoured30", "gtcore30"):
            ap.error("unknown seed set %r" % s)

    commit = _git_commit()
    print("gtcore.__file__ = %s  (commit %s)" % (gtcore.__file__, commit))
    t_all = time.perf_counter()

    vol = load_volume(args.ct)
    t0 = time.perf_counter()
    res = reconstruct(vol, verbose=False)
    print("reconstruct: %d seeds in %.1f s; seeds.cov_ras %s"
          % (len(res.seeds), time.perf_counter() - t0,
             "present" if getattr(res.seeds, "cov_ras", None) is not None else "absent"))
    rs = load_rtstruct(args.rtstruct)
    dv = load_rtdose(args.rtdose)
    S = rasterize_rois(rs, (args.roi, "Seeds"), vol)
    target = hrctv_phys(S[args.roi], vol.affine, roi_name=args.roi)
    print("target: %s" % target.describe())

    ours, ours_axes = np.asarray(res.seeds.centers_ras, float), np.asarray(res.seeds.axes_ras, float)
    cs_ras, cs_axes = contoured_seeds(S["Seeds"], vol)
    rows, idx30 = match_residuals(ours, cs_ras)
    dist = np.array([r[5] for r in rows])
    print("seed match (gate %.0f mm): %d/%d contoured seeds matched one-to-one; residual |r| median %.2f, "
          "p95 %.2f, max %.2f mm; mean vector (%.3f, %.3f, %.3f) mm; per-axis SD (%.2f, %.2f, %.2f) mm"
          % (MATCH_GATE_MM, len(rows), len(cs_ras), np.median(dist), np.percentile(dist, 95), dist.max(),
             *np.mean([r[2:5] for r in rows], axis=0), *np.std([r[2:5] for r in rows], axis=0, ddof=1)))

    # residuals CSV: the clinical-rebuild agent owns this file; written here only if absent
    res_path = args.residuals
    if args.rebuild_residuals or not os.path.exists(res_path):
        _write_csv(res_path, [
            "script: %s (stand-in until scripts/validation_clinical.py writes this file)" % SCRIPT,
            "command: %s" % _public_command(args),
            "commit: %s" % commit, "datasets: %s" % DATASETS,
            "residual = gtcore detection (thin-cut CT, PCA centroid) minus TPS Seeds contour centroid, RAS mm; "
            "one-to-one Hungarian match within %.0f mm" % MATCH_GATE_MM,
            "generated: %s" % _dt.date.today().isoformat(),
        ], ["seed_id", "dx_mm", "dy_mm", "dz_mm", "dist_mm"],
            [{"seed_id": r[0], "dx_mm": r[2], "dy_mm": r[3], "dz_mm": r[4], "dist_mm": r[5]} for r in rows])
        print("wrote %s (%d residual vectors)" % (res_path, len(rows)))
        residuals = np.array([r[2:5] for r in rows])
    else:
        residuals = load_residuals(res_path)
        print("read %s (%d residual vectors)" % (res_path, residuals.shape[0]))

    seed_sets = {}
    if "gtcore32" in args.seed_sets:
        seed_sets["gtcore32"] = (ours, ours_axes, "the %d gtcore detections on PT-CT1mm, PCA axes" % len(ours))
    if "contoured30" in args.seed_sets:
        seed_sets["contoured30"] = (cs_ras, cs_axes, "the %d TPS-contoured seeds (Seeds ROI centroids), PCA axes"
                                    % len(cs_ras))
    if "gtcore30" in args.seed_sets:
        seed_sets["gtcore30"] = (ours[idx30], ours_axes[idx30],
                                 "the %d gtcore detections matched one-to-one to the contoured seeds" % len(idx30))

    bands = [("residual", ResidualResample(residuals, label="primary band")),
             ("degraded_export", AxisGaussian(DEGRADED_EXPORT_SD_MM, label="degraded export"))]
    cov = getattr(res.seeds, "cov_ras", None)
    if cov is not None:
        bands.append(("covariance_check", Covariance(cov, label="secondary check, refinement covariances")))
        print("covariance check enabled (seeds.cov_ras present)")
    else:
        print("covariance check skipped: seeds.cov_ras is None (refinement default-off)")

    eng = TG43Engine()
    levels = DEFAULT_LEVELS_CGY
    stat_rows, dvh_rows, notes = [], [], []

    # the TPS dose on the same target, for context and for the figure's reference curve
    d_tps = target_doses_from_grid(target, dv)
    st_tps = target_stats(target, d_tps, args.rx)
    print("RTDOSE on %s: D90 %.0f cGy, V100 %.3f, V150 %.3f (trilinear at the CT voxel centres)"
          % (target.name, st_tps["D90"], st_tps["V100"], st_tps["V150"]))
    cv_tps = dvh_curve(d_tps, levels)
    for i, lv in enumerate(levels):
        dvh_rows.append({"band": "rtdose", "seed_set": "tps", "level_cgy": float(lv),
                         "nominal": float(cv_tps[i]), "mean": float(cv_tps[i]),
                         "p2.5": float(cv_tps[i]), "p97.5": float(cv_tps[i])})
    stat_rows.extend({"band": "rtdose", "seed_set": "tps", "statistic": k, "nominal": st_tps[k],
                      "mean": st_tps[k], "sd": 0.0, "p2.5": st_tps[k], "p97.5": st_tps[k],
                      "n_draws": 0, "seed": args.seed} for k in ("D90", "V100", "V150"))

    for set_name, (c, a, set_desc) in seed_sets.items():
        d_nom = target_doses_exact(target, c, a, args.sk, engine=eng)
        st = target_stats(target, d_nom, args.rx)
        print("\n[%s] %s: nominal D90 %.0f cGy, V100 %.3f, V150 %.3f"
              % (set_name, set_desc, st["D90"], st["V100"], st["V150"]))
        for band_name, model in bands:
            if isinstance(model, Covariance) and model.n != c.shape[0]:
                print("  %s: skipped for %s (covariances for %d seeds, set has %d)"
                      % (band_name, set_name, model.n, c.shape[0]))
                continue
            r = propagate(target, c, a, model, n_draws=args.n_draws, seed=args.seed, sk_u=args.sk,
                          rx_cgy=args.rx, levels_cgy=levels, engine=eng, n_workers=args.workers)
            print("  %-16s %s" % (band_name, r.describe()))
            print("  %-16s perturbation |delta| median %.2f, p95 %.2f, max %.2f mm; sd (%.2f, %.2f, %.2f)"
                  % ("", r.perturbation_mm["median_mm"], r.perturbation_mm["p95_mm"],
                     r.perturbation_mm["max_mm"], *r.perturbation_mm["sd_mm"]))
            stat_rows.extend(r.stat_rows(band_name, seed_set=set_name))
            dvh_rows.extend(r.dvh_rows(band_name, seed_set=set_name))
            notes.append("%s/%s: %s; runtime %.1f s (%d threads)" % (set_name, band_name, model.describe(),
                                                                    r.runtime_s, args.workers))

    header = [
        "script: %s" % SCRIPT,
        "command: %s" % _public_command(args),
        "commit: %s" % commit,
        "datasets: %s" % DATASETS,
        "target: HRCTV_PHYS = RTSTRUCT:%s rasterised on the CT grid (%.2f cc, %d voxels, %s mm); rx %g cGy; "
        "S_K %g U nominal for every seed; exact TG-43 engine at the voxel centres; axes fixed"
        % (args.roi, target.volume_cc, target.n,
           "x".join("%.2f" % v for v in target.spacing_mm), args.rx, args.sk),
        "seed sets: " + "; ".join("%s = %s" % (k, v[2]) for k, v in seed_sets.items())
        + "; tps = the RTDOSE itself (trilinear at the CT voxel centres, no MC)",
        "bands (both conservative: the residuals include the TPS contouring error): "
        + "; ".join(notes),
        "percentiles: p2.5 / p97.5 across the %d draws, pointwise per level for the DVH" % args.n_draws,
        "calibration-set: yes (the one clinical case; there is no held-out scan)",
        "generated: %s" % _dt.date.today().isoformat(),
    ]
    p_dvh = os.path.join(args.out, "f5b_dvh_band.csv")
    p_stat = os.path.join(args.out, "uncertainty_d90_v100.csv")
    _write_csv(p_dvh, header + ["columns: band, seed_set, level_cgy, nominal (volume fraction >= level), mean, p2.5, p97.5"],
               ["band", "seed_set", "level_cgy", "nominal", "mean", "p2.5", "p97.5"], dvh_rows)
    _write_csv(p_stat, header + ["columns: band, seed_set, statistic (D90 cGy; V100 / V150 fraction), nominal, mean, sd, p2.5, p97.5, n_draws, seed"],
               ["band", "seed_set", "statistic", "nominal", "mean", "sd", "p2.5", "p97.5", "n_draws", "seed"],
               stat_rows)
    print("\nwrote %s (%d rows) and %s (%d rows); total %.0f s"
          % (p_dvh, len(dvh_rows), p_stat, len(stat_rows), time.perf_counter() - t_all))
    return 0


if __name__ == "__main__":
    sys.exit(main())
