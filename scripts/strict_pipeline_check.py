"""Run the reconstruction pipeline on one CT series under ``GTCORE_STRICT=1``
and report whether any formerly-hidden fallback now raises.

usage::

    GTCORE_STRICT=1 python scripts/strict_pipeline_check.py "$GT_DATA_ROOT/<dataset>" <label> [n_full]

Four passes, each reported on its own line; the exit code is 0 when nothing
raised and 2..5 for a raise in pass 1..4 (the traceback is printed):

1. ``reconstruct(vol, n_full_tiles="auto")`` -- the count-free pipeline.  It
   hands the tile fit ``meshes["cavity"]`` when a cavity was segmented, so
   the per-tile surface cross-check (``tiles/auto.py`` ``_finish``) runs.
2. ``reconstruct(vol, n_full_tiles=<n_full>)`` -- the count-known pipeline
   path (plain ``fit_tiles`` with the chord score; no mesh, no bent-tile fit).
3. ``fit_tiles_prior(seeds of pass 1, ImplantPrior(n_full), mesh)`` -- the
   planner's count-known fit called directly: the bent-tile fit on a tile
   the exact fit left rigid (``tiles/auto.py`` ``fit_tiles_prior``) and the
   surface cross-check with the mesh.
4. ``build_hrctv(mesh, with_mesh=True)`` -- the planner's HR-CTV with its
   display shell (``dose/hrctv.py``).

The mesh is the one the planner conforms tiles to (``planner.wall_mesh_for``):
the cavity, else the scanned object's shell (``body``).  Timings are wall
clock on a shared machine: upper bounds.
"""
import logging
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402

import gtcore  # noqa: E402
from gtcore.dose.hrctv import build_hrctv  # noqa: E402
from gtcore.io import load_volume  # noqa: E402
from gtcore.pipeline import reconstruct  # noqa: E402
from gtcore.tiles import ImplantPrior, fit_tiles_prior  # noqa: E402


def summarize(label, tag, res, wall):
    tiles = res.tiles
    allt = list(getattr(tiles, "all_tiles", getattr(tiles, "tiles", [])))
    surf = [getattr(p, "surface", None) for p in allt]
    deform = [getattr(p, "deform", None) for p in getattr(tiles, "tiles", [])]
    print("[%s] %s: OK in %.1f s (%s); seeds %d; tiles %d (n_selected %s, "
          "tentative %d); surface fits %d/%d present; deform %d/%d present" % (
              label, tag, wall, type(tiles).__name__, len(res.seeds.centers_ras),
              len(getattr(tiles, "tiles", [])), getattr(tiles, "n_selected", None),
              len(getattr(tiles, "tentative_tiles", []) or []),
              sum(s is not None for s in surf), len(surf),
              sum(d is not None for d in deform), len(deform)))
    print("[%s] %s timings: %s" % (
        label, tag, {k: round(v, 2) for k, v in res.timings.items()}))
    print("[%s] %s meshes: %s" % (
        label, tag, {k: len(m.faces) for k, m in (res.meshes or {}).items()
                     if hasattr(m, "faces")}))


def wall_mesh(res):
    """``planner.wall_mesh_for``: the cavity, else the object shell."""
    meshes = res.meshes or {}
    for key in ("cavity", "body"):
        m = meshes.get(key)
        if m is not None and len(getattr(m, "faces", [])):
            return key, m
    return None, None


def main(argv):
    path, label = argv[1], argv[2]
    n_full = int(argv[3]) if len(argv) > 3 else 8
    logging.basicConfig(level=logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    print("gtcore from", os.path.dirname(gtcore.__file__))
    print("gtcore.strict_mode() =", gtcore.strict_mode())
    if not gtcore.strict_mode():
        print("set GTCORE_STRICT=1 (otherwise fallbacks only log)")
        return 1

    vol = load_volume(path)
    print("[%s] volume shape %s spacing %s" % (label, vol.array.shape, vol.spacing))

    rc = 0
    res_auto = None
    t0 = time.time()
    try:
        res_auto = reconstruct(vol, verbose=False, n_full_tiles="auto")
        summarize(label, "pass1 auto", res_auto, time.time() - t0)
    except Exception:
        print("[%s] pass1 auto: RAISED after %.1f s" % (label, time.time() - t0))
        traceback.print_exc()
        rc = 2

    t0 = time.time()
    try:
        res_n = reconstruct(vol, verbose=False, n_full_tiles=n_full)
        summarize(label, "pass2 count=%d" % n_full, res_n, time.time() - t0)
    except Exception:
        print("[%s] pass2 count=%d: RAISED after %.1f s" % (label, n_full, time.time() - t0))
        traceback.print_exc()
        rc = rc or 3

    if res_auto is None:
        print("[%s] passes 3-4 need the pass-1 result; skipped" % label)
        print("[%s] exit %d" % (label, rc))
        return rc

    key, mesh = wall_mesh(res_auto)
    seeds = res_auto.seeds
    cav = res_auto.cavity_mask
    cav_c = None
    if cav is not None and np.asarray(cav).any():
        cav_c = vol.index_to_ras(np.argwhere(cav).mean(axis=0)[::-1])
    t0 = time.time()
    try:
        pri = fit_tiles_prior(seeds.centers_ras, seeds.axes_ras,
                              ImplantPrior(n_full=n_full),
                              cavity_center_ras=cav_c, mesh=mesh,
                              spacing_mm=vol.spacing, complete_degraded=True)
        allt = list(pri.all_tiles)
        print("[%s] pass3 fit_tiles_prior(n_full=%d, mesh=%s): OK in %.1f s; tiles %d "
              "(n_selected %s, tentative %d, degraded %d); deform %d/%d; "
              "surface %d/%d; unassigned %d, clutter %d" % (
                  label, n_full, key, time.time() - t0, len(pri.tiles),
                  pri.n_selected, len(pri.tentative_tiles or []),
                  sum(1 for p in pri.tiles if getattr(p, "degraded", False)),
                  sum(p.deform is not None for p in pri.tiles), len(pri.tiles),
                  sum(p.surface is not None for p in allt), len(allt),
                  len(pri.unassigned_indices or []), len(pri.clutter_indices or [])))
    except Exception:
        print("[%s] pass3 fit_tiles_prior: RAISED after %.1f s" % (label, time.time() - t0))
        traceback.print_exc()
        rc = rc or 4

    if mesh is None:
        print("[%s] pass4 build_hrctv: no cavity/body mesh in the pipeline result; "
              "skipped" % label)
    else:
        t0 = time.time()
        try:
            h = build_hrctv(mesh, with_mesh=True)
            print("[%s] pass4 build_hrctv(%s): OK in %.1f s; %.1f cc; display mesh %s" % (
                label, key, time.time() - t0, h.volume_cc,
                "present (%d faces)" % len(h.mesh.faces) if h.mesh is not None
                else "ABSENT"))
        except Exception:
            print("[%s] pass4 build_hrctv: RAISED after %.1f s" % (label, time.time() - t0))
            traceback.print_exc()
            rc = rc or 5
    print("[%s] exit %d" % (label, rc))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
