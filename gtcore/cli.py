"""The ``gt`` command — one simple entry point for the whole project.

    gt view <dicom-folder-or-file>   load a CT, run the pipeline, open 3D view
    gt view                          same, on the synthetic phantom
    gt plan <dicom-folder-or-file>   pipeline + interactive tile planner
    gt plan                          same, on the synthetic phantom
    gt optimize <scan> --tiles N     pipeline + opt-in placement optimizer -> JSON/CSV
    gt optimize <PH8> --phantom-ref <PH0>   same on the printed phantom: wall + cavity
                                     from the registered tile-less twin
    gt demo                          full phantom demo (writes output/ files)
    gt test                          run the test suite

Installed as a console script (see pyproject.toml); the repo root also has a
``gt.bat`` so it works without activating the venv.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


# planner / `gt plan --optimizer` default solver (SA after the V2 campaign)
DEFAULT_OPTIMIZER = "sa"

def _int_or_auto(text):
    if str(text).lower() == "auto":
        return "auto"
    return int(text)


def _load(path, spacing):
    if path:
        from .io import load_volume

        print("loading", path)
        vol = load_volume(path)
        title = os.path.basename(str(path).rstrip("\\/"))[:60]
        return vol, title
    from .phantom import make_head_phantom

    vol, _truth = make_head_phantom(spacing=spacing)
    return vol, "synthetic phantom (%.1f mm)" % spacing


def cmd_view(args):
    from .pipeline import reconstruct
    from .viz import show_scene

    vol, title = _load(args.path, args.spacing)
    print("volume %s @ %s mm" % (vol.array.shape, tuple(round(s, 2) for s in vol.spacing)))
    result = reconstruct(vol, n_full_tiles=args.tiles, n_half_tiles=args.half)
    out = show_scene(result, title="IntraOp GammaTile — " + title,
                     screenshot=args.snapshot)
    if out:
        print("snapshot written to", out)
    return 0


def cmd_plan(args):
    from .pipeline import reconstruct
    from .planner import run_planner
    from .tiles import ImplantPrior

    vol, title = _load(args.path, args.spacing)
    print("volume %s @ %s mm" % (vol.array.shape, tuple(round(s, 2) for s in vol.spacing)))
    prior = ImplantPrior(n_full=args.tiles, n_half=args.half, n_seeds=args.seeds)
    print("implant prior:", prior.describe())
    result = reconstruct(vol, n_seeds_expected=prior.n_seeds)
    # additive: the historical call is untouched unless --optimizer is set
    # to something other than the planner's own default (DEFAULT_OPTIMIZER)
    extra = {}
    if getattr(args, "optimizer", DEFAULT_OPTIMIZER) != DEFAULT_OPTIMIZER:
        extra["solver"] = args.optimizer
    if getattr(args, "hrctv", False):
        extra["hrctv"] = True
    run_planner(result, rx_cgy=args.rx, suggest=bool(args.suggest), prior=prior, **extra)
    return 0


def _implant_center(result, mesh):
    """Detected-seed mean when seeds exist, else the mesh centroid (RAS)."""
    import numpy as np

    seeds = getattr(result, "seeds", None)
    if seeds is not None and len(seeds):
        return np.asarray(seeds.centers_ras, dtype=float).mean(axis=0)
    return np.asarray(mesh.vertices, dtype=float).mean(axis=0)


def _rind5_inputs(result, wall_label):
    """Cavity and clip masks for the report's volumetric HR-CTV (``RIND5``),
    chosen exactly as the planner's HR-CTV does (``planner._ensure_hrctv``):
    the pipeline's cavity mask when the wall IS the cavity, clipped by the
    cranial interior (else the brain).  On a phantom shell there is no
    cavity mask, so every entry is None and the report says RIND5 is absent.
    """
    import numpy as np

    out = dict(cavity_mask=None, cavity_affine=None, clip_mask=None, clip_affine=None,
               clip_source=None)
    if wall_label != "cavity wall":
        return out
    cav = getattr(result, "cavity_mask", None)
    if cav is None or not np.any(cav):
        return out
    aff = result.volume.affine
    out.update(cavity_mask=cav, cavity_affine=aff)
    masks = getattr(result, "masks", {}) or {}
    for key in ("cranial_interior", "brain"):
        m = masks.get(key)
        if m is not None and np.any(m):
            out.update(clip_mask=m, clip_affine=aff, clip_source="gtcore:%s" % key)
            break
    return out


def _eligibility(args, result, vol, mesh, label):
    """The optimizer's eligible wall for ``gt optimize --eligible``:
    ``auto`` keeps the historical choice (every face of a cavity wall; on a
    phantom shell the faces seen from the implant), ``all`` / ``visible`` /
    ``craniotomy`` are :func:`gtcore.plan.candidates.eligible_faces` rules
    (``craniotomy`` derives the skull opening from the pipeline masks and the
    cavity, Paper 2 F8(b)).  Returns ``(mask_or_None, EligibleFaces)``; the
    mask is None for ``all`` so the historical call is unchanged."""
    import numpy as np

    from .plan import candidates as _cand

    mode = str(getattr(args, "eligible", "auto") or "auto").lower()
    if mode == "auto":
        mode = "all" if label == "cavity wall" else "visible"
    centre = _implant_center(result, mesh)
    opening = None
    if mode == "craniotomy":
        cav = getattr(result, "cavity_mask", None)
        opening = _cand.craniotomy_opening(result.masks, vol.affine, cavity_mask=cav)
        print(opening.describe())
    elig = _cand.eligible_faces(mesh, mode, implant_center_ras=centre, opening=opening,
                                **({"cone_deg": float(args.eligible_cone)}
                                   if mode == "craniotomy" else {}))
    if mode == "visible":
        print("eligible faces (visible from the implant): %d of %d"
              % (elig.n_eligible, elig.mask.size))
    else:
        print(elig.describe())
    if mode == "craniotomy":
        i = elig.info
        print("  facing the opening (cone %.0f deg): %d; line of sight through the opening: "
              "%d (clear %d, one crossing in the footprint %d, outside %d, blocked %d); "
              "visible from the implant: %d"
              % (i["cone_deg"], i["n_facing"], i["n_line_of_sight"], i["n_sight_clear"],
                 i["n_sight_one_hit_in_footprint"], i["n_sight_one_hit_outside"],
                 i["n_sight_blocked"], i["n_visible_from_implant"]))
    mask = None if mode == "all" else np.asarray(elig.mask, dtype=bool)
    return mask, elig


def _phantom_reference(ref_path, vol, result):
    """``gt optimize --phantom-ref``: wall, label and RIND5 inputs from the
    tile-less twin registered into this scan
    (:func:`gtcore.segment.cavity.phantom_wall_and_cavity`)."""
    import numpy as np

    from .io import load_volume
    from .segment.cavity import phantom_wall_and_cavity

    print("loading the tile-less twin", ref_path)
    ref = load_volume(ref_path)
    seeds = getattr(result, "seeds", None)
    centers = (np.asarray(seeds.centers_ras, dtype=float)
               if seeds is not None and len(seeds) else None)
    wall, cav_mask, cav_aff, info = phantom_wall_and_cavity(
        vol, ref, masks_ph8=getattr(result, "masks", None), seed_centers_ras=centers)
    reg = info["registration"]
    res = info["residual_mm"]
    print("phantom twin registered (rigid, Dice %.3f, %d it, %.1f s): cavity %.1f cc (%s, "
          "reach %g mm, %d/%d seeds inside); wall %d faces, %.0f mm^2; residual to this "
          "scan's body surface median %.2f mm (p95 %.2f; far from the seeds %.2f)"
          % (reg["dice_masks"], reg["iterations"], reg["seconds"], info["lumen"]["volume_ml"],
             info["lumen"]["rule"], info["lumen"]["hull_reach_mm"],
             info["lumen"]["seeds_inside"], info["lumen"]["n_seeds"],
             info["wall"]["n_faces_wall"], info["wall"]["area_wall_mm2"],
             res["all"].get("median", float("nan")), res["all"].get("p95", float("nan")),
             res.get("far_from_seeds", {}).get("median", float("nan"))))
    rind_kw = dict(cavity_mask=cav_mask, cavity_affine=cav_aff, clip_mask=info["shell_mask"],
                   clip_affine=cav_aff, cavity_source="PH0:lumen(registered)",
                   clip_source="PH0:shell(registered)")
    return wall, "phantom wall (tile-less twin, registered)", rind_kw, info


def cmd_optimize(args):
    """``gt optimize``: pipeline -> wall -> tile-count recommendation ->
    gtcore.plan.optimize (or sweep_n with --min-n) -> JSON + CSV + seed CSV."""
    import json
    import time

    import numpy as np

    import gtcore.plan as plan
    from .interact import export_plan_csv
    from .pipeline import reconstruct
    from .planner import wall_mesh_for

    vol, title = _load(args.path, args.spacing)
    print("volume %s @ %s mm" % (vol.array.shape, tuple(round(s, 2) for s in vol.spacing)))
    result = reconstruct(vol)
    phantom_info = None
    if getattr(args, "phantom_ref", None):
        # printed phantom: the wall and the cavity come from the tile-less twin
        # registered into this scan (Phase 4; the body mask here includes the tiles)
        mesh, label, rind_kw, phantom_info = _phantom_reference(args.phantom_ref, vol, result)
    else:
        mesh, label = wall_mesh_for(result)
        # the report's HR-CTV is the volumetric RIND5 of the pipeline's cavity mask
        # on the final 1 mm grid; the S5 shell numbers are a surrogate (Phase 2 D)
        rind_kw = _rind5_inputs(result, label)
    if mesh is None or not len(getattr(mesh, "vertices", ())):
        print("no cavity or body surface in this scan -- nothing to optimize on")
        return 2
    print("wall: %s (%d faces)" % (label, len(mesh.faces)))
    if phantom_info is not None:
        print("HR-CTV: RIND5 of the phantom cavity (PH0 lumen registered into this scan, "
              "%.1f cc; clip %s), scored on the final 1 mm dose grid; shell rows are S5 "
              "coverage (surrogate)" % (phantom_info["lumen_volume_ml_ph8_grid"],
                                        rind_kw["clip_source"]))
    elif rind_kw["cavity_mask"] is None:
        print("HR-CTV: no cavity mask on this wall (%s) -- the report carries S5 "
              "coverage (surrogate) only, no RIND5" % label)
    else:
        print("HR-CTV: RIND5 of the gtcore cavity (clip %s), scored on the final "
              "1 mm dose grid; shell rows are S5 coverage (surrogate)"
              % (rind_kw["clip_source"] or "none"))

    try:
        eligible, elig = _eligibility(args, result, vol, mesh, label)
        rec = plan.recommend_tile_count(mesh, eligible_faces=eligible)
        print(rec.describe())
        n_tiles = args.tiles
        cap = None
        if n_tiles is None or args.min_n:
            # the rule has no packing loss: also measure what fits at this grid
            # (with --min-n the sweep runs up to this capacity, not --tiles)
            from .plan.api import packing_capacity
            cap, cap_info = packing_capacity(
                mesh, rx_cgy=args.rx, kind="full", h_mm=args.h, n_spins=args.spins,
                eligible_faces=eligible)
            print("recommended %d (%g cm^2 rule) / fits at this grid (h %g mm, %s spins): "
                  "%s%d  [capacity greedy %.1f s, %d candidates]"
                  % (rec.n_tiles, plan.TILE_AREA_CM2, args.h,
                     args.spins if args.spins is not None else "default",
                     "at least " if cap_info.get("at_least") else "", cap,
                     cap_info.get("seconds", 0.0), cap_info.get("n_candidates", 0)))
        if n_tiles is None:
            n_tiles = min(int(rec.n_tiles), int(cap)) if cap > 0 else int(rec.n_tiles)
            which = ("the packing capacity" if cap < rec.n_tiles
                     else "the recommended count")
            print("--tiles not given: using %s, %d" % (which, n_tiles))
        if n_tiles < 1:
            print("tile count must be >= 1 (got %d)" % n_tiles)
            return 1

        out_dir = args.out or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output",
            time.strftime("optimize_%Y%m%d_%H%M%S"))
        os.makedirs(out_dir, exist_ok=True)

        kw = dict(rx_cgy=args.rx, solver=args.solver, seed=args.seed, h_mm=args.h,
                  n_spins=args.spins, eligible_faces=eligible,
                  time_budget_s=float(args.budget), eligible=elig)
        if args.lambda_tail is not None:
            kw["lambda_tail"] = float(args.lambda_tail)
        n_full = n_tiles
        if args.min_n:
            from .plan.api import default_target
            target = default_target(mesh, eligible)   # +5 mm shell of the eligible wall
            # sweep_n forwards **kw to the discrete solvers, which take no budget
            # and no eligibility record (the mask is in eligible_faces)
            kw_sweep = {k: v for k, v in kw.items() if k not in ("time_budget_s", "eligible")}
            # P2 asks for the smallest N that meets the criterion, so sweep up
            # to what fits at this grid (the sweep stops by itself at the
            # first N that cannot be packed), not just up to --tiles
            n_sweep = max(int(n_tiles), int(cap)) if cap else int(n_tiles)
            sweep = plan.sweep_n(mesh, target, n_sweep, **kw_sweep)
            print("coverage vs N (%s):" % args.solver)
            for row in sweep.rows:
                print("  N %2d  V100 %.3f  D90 %.0f cGy  V150 %.3f  V200 %.3f  %.1f s"
                      % (row.get("N", 0), row.get("V100", float("nan")),
                         row.get("D90", float("nan")), row.get("V150", float("nan")),
                         row.get("V200", float("nan")), row.get("runtime_s", 0.0)))
            print("minimum N:", ", ".join("%s -> %s" % (k, v) for k, v in sweep.min_n.items()))
            with open(os.path.join(out_dir, "sweep.json"), "w", encoding="utf-8") as fh:
                json.dump(plan._jsonable({"rows": sweep.rows, "min_n": sweep.min_n}),
                          fh, indent=2)
            chosen = sweep.min_n.get("D90>=rx")
            if chosen is None:
                n_placed = max([int(r.get("N", 0)) for r in sweep.rows] or [n_tiles])
                print("no N <= %d reaches D90 >= rx (largest N that packed: %d); "
                      "placing %d tiles" % (n_sweep, n_placed, n_tiles))
                chosen = n_tiles
            n_full = int(chosen)
        tiles, rep = plan.optimize(mesh, n_full, args.half, refine=bool(args.refine),
                                   report=not args.no_report, verbose=True, **kw,
                                   **rind_kw)
    except NotImplementedError as exc:
        print("optimizer not available yet:", exc)
        return 2
    except (ValueError, RuntimeError) as exc:
        print("optimize failed:", exc)
        return 1

    print(rep.summary())
    rep.to_json(os.path.join(out_dir, "report.json"))
    rep.to_csv(os.path.join(out_dir, "report.csv"))
    if elig is not None:
        # the face mask for the renderer (F8b), next to the report
        np.save(os.path.join(out_dir, "eligible_faces.npy"), np.asarray(elig.mask, dtype=bool))
    seeds_csv = os.path.join(out_dir, "plan_seeds.csv")
    n_seeds = export_plan_csv(seeds_csv, tiles, result.seeds.centers_ras,
                              result.seeds.axes_ras, rx_cgy=args.rx)
    print("%d tiles, %d seeds written to %s" % (len(tiles), n_seeds, out_dir))
    return 0


def cmd_demo(args):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "scripts", "run_phantom_demo.py")
    cmd = [sys.executable, script, "--spacing", str(args.spacing),
           "--tiles", str(args.tiles)]
    if args.streaks:
        cmd.append("--streaks")
    return subprocess.call(cmd)


def cmd_test(args):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return subprocess.call([sys.executable, "-m", "pytest",
                            os.path.join(root, "tests"), "-q"])


def main(argv=None):
    p = argparse.ArgumentParser(prog="gt", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    v = sub.add_parser("view", help="run the pipeline on a CT and open the 3D viewer")
    v.add_argument("path", nargs="?", default=None,
                   help="DICOM folder or .nrrd/.nii/.mha file (default: phantom)")
    v.add_argument("--spacing", type=float, default=0.7,
                   help="phantom voxel size in mm (phantom mode only)")
    v.add_argument("--snapshot", default=None,
                   help="render off-screen to this PNG instead of opening a window")
    v.add_argument("--tiles", type=_int_or_auto, default=None,
                   help="implanted FULL tile count, or 'auto' to infer the "
                        "configuration from the seeds alone: runs tile "
                        "fitting and colours/labels seeds per recovered tile")
    v.add_argument("--half", type=int, default=0,
                   help="implanted HALF (2x1) tile count (with --tiles); with "
                        "--tiles auto any non-zero value allows half tiles")
    v.set_defaults(fn=cmd_view)

    pln = sub.add_parser("plan", help="run the pipeline and open the interactive tile planner")
    pln.add_argument("path", nargs="?", default=None,
                     help="DICOM folder or .nrrd/.nii/.mha file (default: phantom)")
    pln.add_argument("--spacing", type=float, default=0.7,
                     help="phantom voxel size in mm (phantom mode only)")
    pln.add_argument("--rx", type=float, default=6000.0,
                     help="prescription dose in cGy for the isodose levels")
    pln.add_argument("--suggest", action="store_true",
                     help="start with the tile configuration inferred from "
                          "the detected seeds (same as pressing 'T')")
    pln.add_argument("--tiles", type=int, default=None,
                     help="implanted FULL tile count when the OR team knows "
                          "it (trusted input for 'T'); omit if unknown")
    pln.add_argument("--half", type=int, default=0,
                     help="implanted HALF (2x1) tile count (default 0: with "
                          "no OR confirmation, assume no half tiles)")
    pln.add_argument("--seeds", type=int, default=None,
                     help="implanted seed count (4 per full, 2 per half): "
                          "detection is checked against it and, on coarse "
                          "scans, the HU threshold is lowered stepwise "
                          "until that many seeds are found near the implant")
    pln.add_argument("--optimizer", choices=("greedy", "sa", "continuous"),
                     default=DEFAULT_OPTIMIZER,
                     help="placement optimizer solver for the planner's 'O' key "
                          "(Shift+O cycles it in the window)")
    pln.add_argument("--hrctv", action="store_true",
                     help="start with the HR-CTV (5 mm tissue rind outside the "
                          "wall) shown and scored as a volume (the 'V' key)")
    pln.set_defaults(fn=cmd_plan)

    o = sub.add_parser("optimize", help="run the pipeline and the opt-in placement "
                                        "optimizer (gtcore.plan); writes JSON + CSV")
    o.add_argument("path", nargs="?", default=None,
                   help="DICOM folder or .nrrd/.nii/.mha file (default: phantom)")
    o.add_argument("--tiles", type=int, default=None,
                   help="FULL tiles to place (default: the manufacturer-rule "
                        "recommendation from the wall area, printed first)")
    o.add_argument("--half", type=int, default=0, help="HALF (2x1) tiles to place")
    o.add_argument("--min-n", action="store_true", dest="min_n",
                   help="sweep N = 1..--tiles and place the smallest N with D90 >= rx")
    o.add_argument("--solver", choices=("greedy", "local", "sa", "milp", "continuous"),
                   default="sa")
    o.add_argument("--budget", type=float, default=60.0,
                   help="wall-time budget in s for --solver continuous (default 60)")
    o.add_argument("--seed", type=int, default=0, help="RNG seed (stochastic solvers)")
    o.add_argument("--rx", type=float, default=6000.0, help="prescription dose in cGy")
    o.add_argument("--lambda-tail", type=float, default=None, dest="lambda_tail",
                   help="weight of the lower-tail (D90-directed) coverage term in the "
                        "objective (default gtcore.plan.LAMBDA_TAIL; 0 = pure V100)")
    o.add_argument("--h", type=float, default=2.5, help="candidate anchor spacing in mm")
    o.add_argument("--spins", type=int, default=None,
                   help="spins per anchor (default 6 full / 12 half)")
    o.add_argument("--out", default=None,
                   help="output folder (default output/optimize_<timestamp>/)")
    o.add_argument("--refine", action="store_true",
                   help="continuous (u, v, theta) polish after the discrete solve")
    o.add_argument("--no-report", action="store_true", dest="no_report",
                   help="skip the dose-grid final report (influence metrics only)")
    o.add_argument("--eligible", choices=("auto", "all", "visible", "craniotomy"),
                   default="auto",
                   help="eligible wall: 'all' faces, 'visible' from the implant centre, "
                        "'craniotomy' = the wall the surgeon could reach through the skull "
                        "opening (from the pipeline masks and the cavity; Paper 2 F8b); "
                        "'auto' (default) = all on a cavity wall, visible on a phantom shell")
    o.add_argument("--eligible-cone", type=float, default=90.0, dest="eligible_cone",
                   help="--eligible craniotomy: a face's inward normal must point toward the "
                        "opening within this angle in degrees (default 90)")
    o.add_argument("--phantom-ref", default=None, dest="phantom_ref",
                   help="printed phantom: DICOM folder of the tile-less twin (PH0); its "
                        "printed wall and cavity, rigidly registered into this scan, replace "
                        "the body shell and give the RIND5 HR-CTV")
    o.add_argument("--spacing", type=float, default=0.7,
                   help="phantom voxel size in mm (phantom mode only)")
    o.set_defaults(fn=cmd_optimize)

    d = sub.add_parser("demo", help="full phantom demo, writes output/ files")
    d.add_argument("--spacing", type=float, default=0.7)
    d.add_argument("--tiles", type=int, default=3)
    d.add_argument("--streaks", action="store_true")
    d.set_defaults(fn=cmd_demo)

    t = sub.add_parser("test", help="run the test suite")
    t.set_defaults(fn=cmd_test)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
