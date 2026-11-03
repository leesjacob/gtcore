"""Validation campaign: automatic tile creation from the seed cloud alone.

Runs ``fit_tiles(..., "auto")`` -- no implant count -- on

1. the synthetic phantom: rng seeds x tile counts x voxel spacings
   (truth known: n chosen vs truth, exact partition, pose errors),
2. the printed 8-tile phantom ``PH8`` (count known = 8, one crumpled tile),
3. the clinical thin-cut ``PT-CT1mm`` (8 full tiles = 32 sources implanted;
   count-free saturates below that -- the F4 leftover question) and, when
   present, the gapped export ``PT-CT2mm``,

and writes ``output/validation_autogen.csv`` (one row per case),
``output/validation_autogen.png`` (score-saturation curves) and a summary on
stdout.  Real-scan folders are optional: pass ``--phantom8 <dir>``,
``--ct1mm <dir>``, ``--postop <dir>`` (no machine paths are built in).

The real scans' marginal-score curves (``ScorePoint.marginal`` of the
count-free fit for every feasible tile count ``n``, with the ``LAMBDA_FULL``
penalty line) go to ``--marginal-csv`` (default
``docs/figures/data/marginal_score_real.csv``) for the F4 Supplement curve.

Run from the repo root::

    python scripts/validation_autogen.py [--quick] [--no-synthetic]
        --phantom8 "$GT_DATA_ROOT/<PH8>" --ct1mm "$GT_DATA_ROOT/<PT-CT1mm>"
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scipy.spatial.distance import cdist  # noqa: E402

from gtcore.phantom import make_head_phantom  # noqa: E402
from gtcore.pipeline import filter_seed_shaped, seed_detection_params  # noqa: E402
from gtcore.seeds import detect_seed_candidates  # noqa: E402
from gtcore.tiles import fit_tiles  # noqa: E402
from gtcore.tiles.fit import _orient_normal, _plane_fit  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_MARGINAL_CSV = os.path.join("docs", "figures", "data", "marginal_score_real.csv")


def _git_commit():
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=_ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _angle(a, b):
    return float(np.degrees(np.arccos(min(1.0, abs(float(a @ b))))))


def synthetic_case(rng_seed, n_tiles, spacing):
    vol, truth = make_head_phantom(spacing=spacing, n_tiles=n_tiles,
                                   rng_seed=rng_seed)
    p = seed_detection_params(vol.spacing)
    cands = filter_seed_shaped(
        detect_seed_candidates(vol, hu_threshold=p["hu_threshold"],
                               min_mm3=p["min_mm3"], max_mm3=p["max_mm3"]),
        min_mm3=p["min_mm3"], max_mm3=p["max_mm3"],
        min_elong=p["min_elong"], max_elong=p["max_elong"])
    t0 = time.perf_counter()
    fit = fit_tiles(cands.centers_ras, cands.axes_ras, "auto",
                    cavity_center_ras=truth.cavity_center_ras)
    dt = time.perf_counter() - t0
    tc = np.array([s.center_ras for s in truth.seeds])
    D = cdist(cands.centers_ras, tc) if len(cands) else np.zeros((0, len(tc)))
    d2t = [int(D[i].argmin()) if D[i].min() < 2.0 else None
           for i in range(len(cands))]
    got = {frozenset(d2t[i] for i in p_.seed_indices) for p_ in fit.tiles}
    want = {frozenset(t.seed_ids) for t in truth.tiles}
    truth_by = {frozenset(t.seed_ids): t for t in truth.tiles}
    cerr, nerr = [], []
    for pose in fit.tiles:
        key = frozenset(d2t[i] for i in pose.seed_indices)
        t = truth_by.get(key)
        if t is None:
            continue
        cerr.append(float(np.linalg.norm(pose.center_ras - t.center_ras)))
        pts = tc[t.seed_ids]
        ref, _ = _plane_fit(pts)
        ref = _orient_normal(ref, pts.mean(axis=0), truth.cavity_center_ras)
        nerr.append(_angle(pose.normal_ras, ref))
    return dict(case="synthetic", rng=rng_seed, spacing=spacing,
                n_truth=n_tiles, n_seeds=len(cands), n_auto=fit.n_selected,
                exact=(got == want), n_degraded=sum(p_.degraded for p_ in fit.tiles),
                center_err_mean=float(np.mean(cerr)) if cerr else float("nan"),
                center_err_max=float(np.max(cerr)) if cerr else float("nan"),
                normal_err_mean=float(np.mean(nerr)) if nerr else float("nan"),
                seconds=dt,
                curve=[(p_.n, p_.score, p_.marginal) for p_ in fit.score_curve
                       if p_.feasible])


def real_case(name, path, n_truth):
    from gtcore.io import load_volume
    from gtcore.pipeline import reconstruct

    if not path or not os.path.isdir(path):
        print("  [skip] %s: folder not found (%s)" % (name, path))
        return None
    vol = load_volume(path)
    t0 = time.perf_counter()
    res = reconstruct(vol, verbose=False, n_full_tiles="auto")
    dt = time.perf_counter() - t0
    fit = res.tiles
    row = dict(case=name, rng=-1, spacing=float(np.max(vol.spacing)),
               n_truth=n_truth, n_seeds=len(res.seeds), n_auto=fit.n_selected,
               exact=(fit.n_selected == n_truth) if n_truth >= 0 else None,
               n_degraded=sum(p.degraded for p in fit.tiles),
               center_err_mean=float("nan"), center_err_max=float("nan"),
               normal_err_mean=float("nan"), seconds=dt,
               curve=[(p.n, p.score, p.marginal) for p in fit.score_curve
                      if p.feasible],
               score_points=list(fit.score_curve),
               n_tentative=len(fit.tentative_tiles or []),
               n_unassigned=len(fit.unassigned_indices or []),
               n_clutter=len(fit.clutter_indices or []),
               lambda_full=float(fit.lambda_full))
    print("  %s: %d seeds -> n=%d (%s); tiles %s" % (
        name, len(res.seeds), fit.n_selected, fit.summary(),
        [(p.seed_indices, "crumpled" if p.degraded else
          "%.2f mm" % p.residual_mm) for p in fit.tiles]))
    for p in fit.tiles:
        if p.deform is not None:
            print("    T%d rms %.2f mm  fold %.0f deg  E %.3f%s" % (
                p.tile_id, p.deform.rms_mm, p.deform.params.fold_deg,
                p.deform.bending_energy,
                ("  " + p.surface.verdict()) if p.surface is not None else ""))
    return row


def write_marginal_csv(path, real_rows, args):
    """Marginal score of the n-th tile vs n for each real scan (count-free
    fit), with the penalty line, as the F4 Supplement curve's data."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    cmd = ("python scripts/validation_autogen.py%s%s%s%s --marginal-csv %s" % (
        " --quick" if args.quick else "",
        " --no-synthetic" if args.no_synthetic else "",
        ' --phantom8 "$GT_DATA_ROOT/<PH8>"' if args.phantom8 else "",
        ' --ct1mm "$GT_DATA_ROOT/<PT-CT1mm>"' if args.ct1mm else "",
        args.marginal_csv.replace(os.sep, "/")))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write("# script: scripts/validation_autogen.py\n")
        fh.write("# command: %s\n" % cmd)
        fh.write("# commit: %s\n" % _git_commit())
        fh.write("# datasets: %s\n" % ", ".join(r["case"] for r in real_rows))
        fh.write("# generated: %s\n" % time.strftime("%Y-%m-%d %H:%M"))
        fh.write("# count-free fit (reconstruct n_full_tiles='auto'): per feasible tile "
                 "count n, score = best total unpenalised bent-tile score with exactly n "
                 "tiles, marginal = score(n) - score(n-1), penalized = score - lambda_full "
                 "* n_full - lambda_half * n_half; the fit selects n_selected = argmax "
                 "penalized; lambda_full is the penalty line "
                 "(gtcore.tiles.auto.LAMBDA_FULL)\n")
        fh.write("# n_seeds = seeds after the vault filter; n_true_tiles = implanted full "
                 "tiles (8 for PH8 and PT-CT1mm); spacing_mm = max voxel dimension\n")
        w = csv.writer(fh)
        w.writerow(["dataset", "n", "n_full", "n_half", "score", "marginal",
                    "penalized", "feasible", "lambda_full", "n_selected",
                    "n_true_tiles", "n_seeds", "n_tentative", "n_unassigned",
                    "spacing_mm"])
        for r in real_rows:
            for p in r["score_points"]:
                w.writerow([r["case"], p.n, p.n_full, p.n_half,
                            "%.4f" % p.score if p.feasible else "nan",
                            "%.4f" % p.marginal if p.feasible else "nan",
                            "%.4f" % p.penalized if p.feasible else "nan",
                            bool(p.feasible), "%.2f" % r["lambda_full"],
                            r["n_auto"], r["n_truth"], r["n_seeds"],
                            r["n_tentative"], r["n_unassigned"],
                            "%.3f" % r["spacing"]])
    print("marginal-score curves: %s" % os.path.relpath(path, _ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="smaller synthetic sweep")
    ap.add_argument("--phantom8", default=None, help="PH8 series directory")
    ap.add_argument("--ct1mm", default=None, help="PT-CT1mm series directory")
    ap.add_argument("--postop", default=None,
                    help="PT-CT2mm (gapped export) series directory, if it exists")
    ap.add_argument("--no-real", action="store_true")
    ap.add_argument("--no-synthetic", action="store_true",
                    help="skip the synthetic sweep (real scans only)")
    ap.add_argument("--marginal-csv", default=DEFAULT_MARGINAL_CSV,
                    help="where the real scans' marginal-score curves go "
                         "(relative to the repo root; '' disables)")
    args = ap.parse_args()

    out_dir = os.path.join(os.path.dirname(__file__), "..", "output")
    os.makedirs(out_dir, exist_ok=True)
    rows = []

    rngs = [0, 1, 2] if args.quick else list(range(6))
    tiles = [1, 3, 5] if args.quick else [1, 2, 3, 4, 5]
    spacings = [0.8] if args.quick else [0.8, 1.2]
    if args.no_synthetic:
        rngs, tiles = [], []
    print("synthetic sweep: %d cases" % (len(rngs) * len(tiles) * len(spacings)))
    for sp in spacings:
        for nt in tiles:
            for rng in rngs:
                r = synthetic_case(rng, nt, sp)
                rows.append(r)
                print("  sp %.1f n %d rng %d: auto n=%d exact=%s degraded=%d "
                      "centre %.2f/%.2f mm normal %.1f deg  %.2fs" % (
                          sp, nt, rng, r["n_auto"], r["exact"], r["n_degraded"],
                          r["center_err_mean"], r["center_err_max"],
                          r["normal_err_mean"], r["seconds"]))
    real_rows = []
    if not args.no_real:
        print("real scans:")
        # dataset ids (figure plan v6): PH8 = printed 8-tile phantom (8 full
        # tiles, hand-placed), PT-CT1mm = clinical thin-cut (8 full tiles =
        # 32 sources implanted), PT-CT2mm = its gapped export (if restored)
        for name, path, n in (("PH8", args.phantom8, 8),
                              ("PT-CT1mm", args.ct1mm, 8),
                              ("PT-CT2mm", args.postop, 8)):
            if not path:
                print("  [skip] %s: no folder given" % name)
                continue
            r = real_case(name, path, n)
            if r is not None:
                rows.append(r)
                real_rows.append(r)
    if not rows:
        print("nothing to report")
        return

    # ---------------------------------------------------------------- csv
    skip = {"curve", "score_points"}
    fields = [k for k in rows[0].keys() if k not in skip] + ["curve"]
    with open(os.path.join(out_dir, "validation_autogen.csv"), "w",
              newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            rr = dict(r)
            rr["curve"] = ";".join("%d:%.2f:%.2f" % c for c in r["curve"])
            w.writerow(rr)

    # -------------------------------------------- marginal score vs n (real)
    if real_rows and args.marginal_csv:
        write_marginal_csv(os.path.join(_ROOT, args.marginal_csv), real_rows, args)

    # ------------------------------------------------------------- summary
    syn = [r for r in rows if r["case"] == "synthetic"]
    n_ok = sum(1 for r in syn if r["n_auto"] == r["n_truth"])
    n_exact = sum(1 for r in syn if r["exact"])
    print("\nsynthetic: count correct %d/%d, exact partition %d/%d, "
          "centre err mean %.2f mm (max %.2f), normal err mean %.1f deg, "
          "%.2f s/case" % (
              n_ok, len(syn), n_exact, len(syn),
              np.nanmean([r["center_err_mean"] for r in syn]),
              np.nanmax([r["center_err_max"] for r in syn]),
              np.nanmean([r["normal_err_mean"] for r in syn]),
              np.mean([r["seconds"] for r in syn])))
    for r in rows:
        if r["case"] != "synthetic":
            print("%s: n_auto=%d (truth %s), %d crumpled, %.1f s" % (
                r["case"], r["n_auto"], r["n_truth"] if r["n_truth"] >= 0
                else "unknown", r["n_degraded"], r["seconds"]))

    # -------------------------------------------------------------- figure
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from gtcore.tiles.auto import LAMBDA_FULL

        fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
        ax = axes[0]
        for r in syn:
            if not rngs or r["spacing"] != spacings[0] or r["rng"] != rngs[0]:
                continue
            ns = [c[0] for c in r["curve"]]
            marg = [c[2] for c in r["curve"]]
            ax.plot(ns[1:], marg[1:], "o-", label="truth n=%d" % r["n_truth"])
        ax.axhline(LAMBDA_FULL, color="k", ls="--", label="penalty lambda")
        ax.set_xlabel("tile count n")
        ax.set_ylabel("marginal score of the n-th tile")
        ax.set_title("synthetic phantom (%.1f mm): marginal gain saturates at "
                     "the true n" % spacings[0])
        ax.legend(fontsize=8)
        ax = axes[1]
        for r in rows:
            if r["case"] == "synthetic":
                continue
            ns = [c[0] for c in r["curve"]]
            marg = [c[2] for c in r["curve"]]
            lab = "%s (auto n=%d%s)" % (
                r["case"], r["n_auto"],
                "" if r["n_truth"] < 0 else ", truth %d" % r["n_truth"])
            ax.plot(ns[1:], marg[1:], "s-", label=lab)
        ax.axhline(LAMBDA_FULL, color="k", ls="--", label="penalty lambda")
        ax.set_xlabel("tile count n")
        ax.set_ylabel("marginal score of the n-th tile")
        ax.set_title("real scans (count-free fit)")
        ax.legend(fontsize=8)
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "validation_autogen.png"), dpi=150)
        print("figure: output/validation_autogen.png")
    except Exception as exc:  # matplotlib missing / headless trouble
        print("figure skipped:", exc)


if __name__ == "__main__":
    main()
