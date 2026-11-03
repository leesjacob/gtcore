"""Cavity reach sweep: Dice / volume / cavity-stage time vs ``hull_reach_mm``.

Reproduces the "phantom Dice rises with reach" measurement of
docs/clinical-validation-notes.md section 4 (that column was the SYNTHETIC
phantom, ``gtcore.phantom.make_head_phantom()`` at its defaults: 0.7 mm
isotropic, 3 tiles, rng 0; the printed phantom has no cavity mask) and,
with ``--ct``, runs the pipeline once on a real CT at the default reach and
reports the cavity volume, the cavity-stage time and -- when an RTSTRUCT
with ``Cav_Post`` is given -- Dice / coverage against it, exactly as
scripts/validation_clinical.py computes them.

Usage::

    python scripts/cavity_reach_sweep.py                       # synthetic sweep
    python scripts/cavity_reach_sweep.py --reaches 12,14,16,20
    python scripts/cavity_reach_sweep.py --ct "$GT_DATA_ROOT/<PH8>"
    python scripts/cavity_reach_sweep.py --ct "$GT_DATA_ROOT/<PT-CT1mm>" \
        --rtstruct "$GT_DATA_ROOT/<PT-RTSTRUCT>"

Numbers quoted in docs/paper2-phase-notes.md ("Phase 1 -- cavity reach in
physical mm") come from this code path.  Writes nothing.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from functools import partial

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from gtcore.phantom import make_head_phantom                 # noqa: E402
from gtcore.preprocess import inpaint_metal                 # noqa: E402
from gtcore.seeds import detect_seed_candidates             # noqa: E402
from gtcore.segment import segment_cavity, segment_head     # noqa: E402
import gtcore.pipeline as pipeline                          # noqa: E402


def dice(a, b):
    a = np.asarray(a, bool)
    b = np.asarray(b, bool)
    return 2.0 * np.logical_and(a, b).sum() / max(a.sum() + b.sum(), 1)


def _commit():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:                                   # noqa: BLE001 - informational only
        return "unknown"


def synthetic_sweep(reaches, via_reconstruct=False):
    vol, truth = make_head_phantom()
    vmm3 = float(np.prod(vol.spacing))
    truth_cav = truth.masks["cavity"]
    print("synthetic phantom make_head_phantom() defaults: spacing %s, shape %s, truth cavity %.1f cc"
          % (np.round(vol.spacing, 3).tolist(), vol.array.shape, truth_cav.sum() * vmm3 / 1000.0))
    if via_reconstruct:
        orig = pipeline.segment_cavity
        try:
            for r in reaches:
                pipeline.segment_cavity = partial(orig, hull_reach_mm=r)
                v, _t = make_head_phantom()
                res = pipeline.reconstruct(v, verbose=False)
                cav = res.cavity_mask
                print("reconstruct  reach %4.1f mm: Dice %.3f  volume %.1f cc  cavity stage %.2f s"
                      % (r, dice(cav, truth_cav), cav.sum() * vmm3 / 1000.0,
                         res.timings["cavity segmentation"]))
        finally:
            pipeline.segment_cavity = orig
        return
    cands = detect_seed_candidates(vol)
    clean = inpaint_metal(vol, cands.mask)
    masks = segment_head(clean, metal_mask=cands.mask)
    for r in reaches:
        t0 = time.perf_counter()
        cav = segment_cavity(clean, masks["cranial_interior"], masks["brain"],
                             cands.centers_ras, hull_reach_mm=r)
        dt = time.perf_counter() - t0
        print("demo path    reach %4.1f mm: Dice %.3f  volume %.1f cc  cavity stage %.2f s"
              % (r, dice(cav, truth_cav), cav.sum() * vmm3 / 1000.0, dt))


def real_case(ct_dir, rtstruct=None):
    from gtcore.io import load_volume
    vol = load_volume(ct_dir)
    print("CT shape %s spacing %s" % (vol.array.shape, np.round(vol.spacing, 4).tolist()))
    res = pipeline.reconstruct(vol, verbose=True)
    vmm3 = float(np.prod(vol.spacing))
    cav = res.cavity_mask
    print("cavity stage %.2f s; gtcore cavity %.1f cc (%d voxels)"
          % (res.timings["cavity segmentation"], cav.sum() * vmm3 / 1000.0, int(cav.sum())))
    if rtstruct:
        from validation_clinical import _dcm, rasterise
        cp = rasterise(_dcm(rtstruct), "Cav_Post", vol)
        print("Cav_Post %.1f cc: Dice %.3f, covers %.0f%% of it, %.0f%% of ours inside it"
              % (cp.sum() * vmm3 / 1000.0, dice(cav, cp),
                 100.0 * (cav & cp).sum() / max(1, cp.sum()),
                 100.0 * (cav & cp).sum() / max(1, cav.sum())))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--reaches", default="12,14,16,20",
                    help="comma-separated hull_reach_mm values for the synthetic sweep")
    ap.add_argument("--via-reconstruct", action="store_true",
                    help="run the synthetic sweep through pipeline.reconstruct instead of the demo path")
    ap.add_argument("--ct", help="real CT directory: run the pipeline once at the default reach")
    ap.add_argument("--rtstruct", help="RTSTRUCT (file or directory) with Cav_Post, for Dice")
    args = ap.parse_args(argv)
    print("gtcore commit %s" % _commit())
    if args.ct:
        real_case(args.ct, args.rtstruct)
    else:
        reaches = [float(x) for x in args.reaches.split(",") if x.strip()]
        synthetic_sweep(reaches, via_reconstruct=args.via_reconstruct)


if __name__ == "__main__":
    main()
