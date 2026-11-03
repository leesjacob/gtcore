"""Render every data panel of the Paper 2 figures from the committed data.

    python scripts/paper_figures.py                 # every panel
    python scripts/paper_figures.py --list          # panels, sources, header commits
    python scripts/paper_figures.py --only f5b,f4a  # a comma list; a prefix selects a figure (--only f4)
    python scripts/paper_figures.py --out <dir>     # default output/figures

The authoritative render list (AGENT_COMMON, Jacob 2026-11-03): F2(c) tile
sheets flat / conformed / folded; F3(c) PH8 axial slice; F4(a) seed slice,
F4(b) patient 3-D, F4(c) phantom 3-D; F5(a) isodoses, F5(b) DVH + MC band;
F6 one slice; F8(a) implant 3-D, F8(b) optimized 3-D; S2(a, b); S4; and two
optional Supplement panels (gamma map slice, three-target DVH).

Every panel reads ``docs/figures/data`` / ``docs/figures/optimize/data``
only.  The only computation is the F2(c) deform geometry (``gtcore.tiles.
deform`` on ideal poses) and the off-screen 3-D renders of the committed
NPZ files; nothing re-runs a pipeline.  Each panel is written as PNG
(600 dpi), PDF and SVG (editable text) under ``--out``; panels carry data,
axes and legends only (no panel letters, no annotations -- Inkscape layer).
``docs/figures/manifest.json`` records, per panel, the source files with
their header commits, the script commit, the outputs and the key numbers.

Panels whose inputs the Phase 4 campaign has not committed yet (F8(b), S4)
and the real-data panel S2(b) skip with a message, never a stand-in.  The
style bar lives in ``scripts/_figstyle.py``.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import glob
import json
import os
import subprocess
import sys
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import _figstyle as S  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, Patch  # noqa: E402

DATA_DIR = os.path.join(ROOT, "docs", "figures", "data")
OPT_DATA_DIR = os.path.join(ROOT, "docs", "figures", "optimize", "data")
DEFAULT_OUT = os.path.join(ROOT, "output", "figures")
MANIFEST_PATH = os.path.join(ROOT, "docs", "figures", "manifest.json")

# panel data constants (what each panel shows; changing these changes the paper)
F5_GTCORE_FIELD = "gtcore32gt"           # F5(a): isodoses of gtcore at the 32 gtcore seeds (pipeline result)
F5_DVH_GTCORE32 = "gtcore_32_gtcore_seeds"
F5_DVH_GTCORE30 = "gtcore_30_contoured_seeds"
F5_BAND = ("residual", "gtcore32")       # f5b_dvh_band.csv: the primary band
F4_TILE_MODE_DEFAULT = "auto"            # F4(b)/F8(a): 'auto' = 6 sheets + 8 leftover seeds; 'count_6full_4half'
F2C_CONFIGS = (("flat", 0.0, 0.0), ("conformed", 1.0 / 17.0, 1.0 / 17.0), ("folded", 0.35, 0.0))
F3C_NEAR_MM = 1.5
F6_MIN_OUTLINE_MM = 4.0                  # F6: rind outline islands shorter than this (voxel specks at the clip) not drawn
F4A_NEAR_MM = 3.0
GAMMA_MAP_FILE = "gamma_map_slice_gtcore_3pct_2mm_global.npz"
S4_FILE = os.path.join(OPT_DATA_DIR, "v2_summary.csv")
S2B_FILE = os.path.join(DATA_DIR, "s2b_axis_residuals.csv")
F8B_FILE = os.path.join(DATA_DIR, "f8b_patient_optimized.npz")
F8_SUMMARY_FILE = os.path.join(OPT_DATA_DIR, "f8_patient_summary.csv")


class Skip(Exception):
    """The panel cannot be rendered from the committed data (message says why)."""


# ============================================================ bookkeeping
def git_head() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def git_last_commit(path: str) -> Optional[str]:
    try:
        out = subprocess.check_output(["git", "log", "-1", "--format=%h", "--", path], cwd=ROOT,
                                      stderr=subprocess.DEVNULL).decode().strip()
        return out or None
    except Exception:
        return None


def header_commit(path: str) -> Optional[str]:
    """The ``# commit:`` of a CSV header, the ``commit`` of an NPZ's JSON index
    or ``header`` array, or None."""
    if not os.path.exists(path):
        return None
    if path.endswith(".csv") or path.endswith(".md"):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith("#"):
                    break
                if line.lower().startswith("# commit:"):
                    return line.split(":", 1)[1].strip().split()[0]
        return None
    if path.endswith(".npz"):
        idx = path[:-4] + ".json"
        if os.path.exists(idx):
            try:
                with open(idx, encoding="utf-8") as fh:
                    return str(json.load(fh).get("commit")) or None
            except Exception:
                pass
        try:
            with np.load(path, allow_pickle=True) as z:
                if "header" in z.files:
                    for line in str(z["header"]).splitlines():
                        if line.lower().startswith("# commit:"):
                            return line.split(":", 1)[1].strip().split()[0]
                if "settings" in z.files:
                    return json.loads(str(z["settings"])).get("git_commit")
        except Exception:
            return None
    if path.endswith(".json"):
        try:
            with open(path, encoding="utf-8") as fh:
                return str(json.load(fh).get("commit")) or None
        except Exception:
            return None
    return None


def rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def source_records(paths: Sequence[str]) -> List[Dict[str, Any]]:
    out = []
    for p in paths:
        out.append({"path": rel(p), "exists": os.path.exists(p),
                    "header_commit": header_commit(p), "git_commit": git_last_commit(p)})
    return out


def read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        raise Skip("missing %s" % rel(path))
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(line for line in fh if not line.startswith("#")))


def has_header(path: str) -> bool:
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8") as fh:
        return fh.readline().startswith("#")


def fnum(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def dpath(name: str) -> str:
    return os.path.join(DATA_DIR, name)


# ============================================================ shared loaders (cached)
_CACHE: Dict[str, Any] = {}


def clinical_slice() -> Dict[str, Any]:
    if "slice" not in _CACHE:
        p = dpath("clinical_slice.npz")
        if not os.path.exists(p):
            raise Skip("missing %s" % rel(p))
        with np.load(p, allow_pickle=True) as z:
            _CACHE["slice"] = {k: z[k] for k in z.files}
    return _CACHE["slice"]


def clinical_contours() -> Dict[Tuple[str, str], List[np.ndarray]]:
    """(layer, level) -> list of (N, 2) polylines in RAS x, y."""
    if "contours" not in _CACHE:
        rows = read_csv(dpath("clinical_slice_contours.csv"))
        groups: Dict[Tuple[str, str, str], List[Tuple[int, float, float]]] = {}
        for r in rows:
            key = (r["layer"], r["level_cgy"], r["polyline_id"])
            groups.setdefault(key, []).append((int(r["vertex"]), float(r["x_ras"]), float(r["y_ras"])))
        out: Dict[Tuple[str, str], List[np.ndarray]] = {}
        for (layer, level, _pid), pts in groups.items():
            pts.sort()
            out.setdefault((layer, level), []).append(np.array([(x, y) for _v, x, y in pts]))
        _CACHE["contours"] = out
    return _CACHE["contours"]


def clinical_slice_seeds() -> List[Dict[str, str]]:
    if "slice_seeds" not in _CACHE:
        _CACHE["slice_seeds"] = read_csv(dpath("clinical_slice_seeds.csv"))
    return _CACHE["slice_seeds"]


def table3_value(structure_prefix: str, dose_source: str, metric: str) -> Optional[float]:
    if "table3" not in _CACHE:
        try:
            _CACHE["table3"] = read_csv(dpath("clinical_table3_rows.csv"))
        except Skip:
            _CACHE["table3"] = []
    for r in _CACHE["table3"]:
        if r["row"].startswith(structure_prefix) and r["dose_source"] == dose_source and r["metric"] == metric:
            return fnum(r["value"])
    return None


# ============================================================ CT slice panels
def _extent(x_ras: np.ndarray, y_ras: np.ndarray) -> Tuple[float, float, float, float]:
    """imshow extent (left, right, bottom, top) in RAS mm for origin='upper'
    from the voxel-centre coordinate arrays (pixel edges = centres +- half a
    pixel).  With a DICOM affine of negative x/y steps this puts the
    patient's right on the viewer's left (radiological display)."""
    dx = float(x_ras[0, 1] - x_ras[0, 0])
    dy = float(y_ras[1, 0] - y_ras[0, 0])
    return (float(x_ras[0, 0]) - dx / 2.0, float(x_ras[0, -1]) + dx / 2.0,
            float(y_ras[-1, 0]) + dy / 2.0, float(y_ras[0, 0]) - dy / 2.0)


def ct_panel(size, hu: np.ndarray, x_ras: np.ndarray, y_ras: np.ndarray, window: str,
             center_xy: Tuple[float, float], half_mm: float, legend_rows: int = 0):
    """Grey CT slice, fixed window, square crop, isotropic pixels, no axes,
    radiological orientation, 10 mm scale bar.  ``legend_rows`` > 0 adds a
    white band of that many legend rows BELOW the image (nothing overlays the
    image).  Returns (fig, ax, crop)."""
    fig, ax = S.image_axes(size, legend_rows=legend_rows)
    ext = _extent(x_ras, y_ras)
    vmin, vmax = S.set_window(window)
    ax.imshow(hu, cmap="gray", vmin=vmin, vmax=vmax, extent=ext, origin="upper",
              interpolation="nearest", zorder=0)
    cx, cy = center_xy
    invert = ext[0] > ext[1]
    if invert:
        ax.set_xlim(cx + half_mm, cx - half_mm)
    else:
        ax.set_xlim(cx - half_mm, cx + half_mm)
    ax.set_ylim(cy - half_mm, cy + half_mm)
    crop = (cx + half_mm, cx - half_mm, cy - half_mm, cy + half_mm) if invert else \
           (cx - half_mm, cx + half_mm, cy - half_mm, cy + half_mm)
    S.add_scale_bar(ax, crop, invert_x=invert)
    return fig, ax, crop


def draw_polylines(ax, polys: Sequence[np.ndarray], colour: str, lw: float, ls: str = "-",
                   halo_colour: str = "black", zorder: int = 5, closed: bool = True,
                   min_length_mm: float = 0.0):
    for p in polys:
        if len(p) < 2:
            continue
        if min_length_mm > 0 and np.sum(np.linalg.norm(np.diff(p, axis=0), axis=1)) < min_length_mm:
            continue
        q = np.vstack([p, p[:1]]) if closed and not np.allclose(p[0], p[-1]) else p
        ax.plot(q[:, 0], q[:, 1], color=colour, lw=lw, ls=ls, zorder=zorder,
                solid_capstyle="round", solid_joinstyle="round",
                path_effects=S.halo(lw, halo_colour, 0.6 if halo_colour == "black" else 0.75))


def mask_overlay(ax, mask: np.ndarray, colour: str, alpha: float, extent, zorder: int = 2):
    """Pixel-exact translucent fill of a boolean mask on the CT grid."""
    from matplotlib.colors import to_rgb
    rgba = np.zeros(mask.shape + (4,), dtype=np.float32)
    rgba[..., :3] = to_rgb(colour)
    rgba[..., 3] = np.where(mask, alpha, 0.0)
    ax.imshow(rgba, extent=extent, origin="upper", interpolation="nearest", zorder=zorder)


# ============================================================ panels: F2(c)
def f2c_deform(ctx) -> Dict[str, Any]:
    """F2(c): one tile as the bent-sheet model: flat, wall-conformed
    (kappa = 1/17 mm^-1 on both axes) and folded (kappa1 = 0.35 mm^-1), the
    collagen sheet (tissue face) as a wireframe with the 2 x 2 seeds."""
    from gtcore import geometry as G
    from gtcore.tiles.deform import (DeformParams, deformed_seed_axes, deformed_seed_points,
                                     deformed_surface_grid)
    from gtcore.tiles.model import TilePose6

    n_grid = 11
    fig = plt.figure(figsize=S.SIZE["f2c"])
    pose = TilePose6(R=np.eye(3), t=np.zeros(3), kind="full")
    key_numbers: Dict[str, Any] = {}
    half_len = float(G.SEED_LENGTH_MM) / 2.0
    for idx, (name, k1, k2) in enumerate(F2C_CONFIGS):
        ax = fig.add_axes([idx / 3.0, 0.0, 1.0 / 3.0, 1.0], projection="3d")
        ax.set_proj_type("ortho")
        ax.view_init(elev=28, azim=-60)
        ax.set_axis_off()
        params = DeformParams(kappa1=k1, kappa2=k2, psi=0.0)
        tissue = deformed_surface_grid(pose, params, n=n_grid, offset_mm=-G.SEED_PLANE_OFFSET_MM)
        G3 = tissue.reshape(n_grid, n_grid, 3)
        ax.plot_surface(G3[..., 0], G3[..., 1], G3[..., 2], color=S.COL["sheet"], alpha=0.25,
                        linewidth=0, antialiased=True, shade=False, zorder=1)
        for r in range(n_grid):
            ax.plot(G3[r, :, 0], G3[r, :, 1], G3[r, :, 2], color=S.COL["sheet"], lw=0.5, zorder=2)
            ax.plot(G3[:, r, 0], G3[:, r, 1], G3[:, r, 2], color=S.COL["sheet"], lw=0.5, zorder=2)
        seeds = deformed_seed_points(pose, params)
        axes = deformed_seed_axes(pose, params)
        for c, a in zip(seeds, axes):
            p0, p1 = c - half_len * a, c + half_len * a
            ax.plot([p0[0], p1[0]], [p0[1], p1[1]], [p0[2], p1[2]], color=S.COL["seed"], lw=2.6,
                    solid_capstyle="round", zorder=10)
        ax.set_xlim(-11, 11)
        ax.set_ylim(-11, 11)
        ax.set_zlim(-7, 9)
        ax.set_box_aspect((22, 22, 16), zoom=1.02)
        d = np.linalg.norm(seeds[:, None] - seeds[None], axis=2)
        sides = sorted(d[np.triu_indices(4, 1)])[:4]
        key_numbers[name] = {"kappa1_per_mm": k1, "kappa2_per_mm": k2,
                             "fold_deg": params.fold_deg, "bending_energy": params.bending_energy,
                             "seed_chords_mm": [round(float(x), 2) for x in sides]}
    outs = S.save_panel(fig, ctx["out"], "f2c_tile_model")
    return {"outputs": outs, "key_numbers": key_numbers,
            "note": "gtcore.tiles.deform on an ideal pose; tissue-face wireframe at -3 mm, seeds on the seed sheet; "
                    "kappa values in key_numbers (titles are Inkscape's)"}


# ============================================================ panels: F3(c)
def f3c_phantom_slice(ctx) -> Dict[str, Any]:
    """F3(c): one axial PH8 slice through the seeds (W 1000 / L 0), the
    detected seeds on the slice circled, 10 mm bar."""
    p = dpath("f3c_phantom_slice.npz")
    if not os.path.exists(p):
        raise Skip("missing %s (run scripts/export_phantom_slice.py once)" % rel(p))
    with np.load(p, allow_pickle=True) as z:
        d = {k: z[k] for k in z.files}
    cx, cy = [float(v) for v in d["crop_center_xy"]]
    half = float(d["crop_half_mm"])
    fig, ax, crop = ct_panel(S.SIZE["f3c"], d["ct_hu"], d["x_ras"], d["y_ras"], "phantom", (cx, cy), half)
    near = np.asarray(d["seed_near_slice"], bool)
    for c in d["seed_centers_ras"][near]:
        ax.add_patch(Circle((c[0], c[1]), radius=2.3, fill=False, lw=S.LW["overlay"],
                            edgecolor=S.COL["gtcore"], zorder=6,
                            path_effects=S.halo(S.LW["overlay"])))
    outs = S.save_panel(fig, ctx["out"], "f3c_phantom_slice")
    return {"outputs": outs,
            "key_numbers": {"slice_k": int(d["slice_k"]), "slice_z_ras_mm": float(d["slice_z_ras"]),
                            "seeds_on_slice": int(near.sum()), "n_seeds": int(len(near)),
                            "near_mm": float(d["near_mm"]), "window": "W 1000 / L 0",
                            "crop_center_xy": [cx, cy], "crop_half_mm": half},
            "note": str(d["seed_source"])}


# ============================================================ panels: F4(a), F5(a), F6
def _clinical_ct(size, legend_rows: int = 0):
    d = clinical_slice()
    cx, cy = S.CLINICAL_CROP["center_xy"]
    return ct_panel(size, d["ct_hu"], d["x_ras"], d["y_ras"], "clinical", (cx, cy), S.CLINICAL_CROP["half_mm"],
                    legend_rows=legend_rows)


def f4a_seed_slice(ctx) -> Dict[str, Any]:
    """F4(a): the clinical slice (W 400 / L 40) with the TPS seed contours
    (open circles at the contour centroids), gtcore seeds (x) and any
    uncontoured source (filled diamond) within 3 mm of the slice plane."""
    fig, ax, crop = _clinical_ct(S.SIZE["f4a"], legend_rows=1)
    rows = clinical_slice_seeds()
    tps = [r for r in rows if r["set"] == "tps_contour_centroid" and abs(fnum(r["dz_mm"])) <= F4A_NEAR_MM]
    gt = [r for r in rows if r["set"] == "gtcore_seed" and abs(fnum(r["dz_mm"])) <= F4A_NEAR_MM]
    gt_c = [r for r in gt if r["contoured"] == "True"]
    gt_u = [r for r in gt if r["contoured"] != "True"]
    handles = []
    if tps:
        xs, ys = [fnum(r["x_ras"]) for r in tps], [fnum(r["y_ras"]) for r in tps]
        ax.plot(xs, ys, ls="none", marker="o", mfc="none", mec="white", mew=S.LW["overlay"] + 1.0,
                ms=S.MS["tps_circle"], zorder=6, alpha=0.9)
        h, = ax.plot(xs, ys, ls="none", marker="o", mfc="none", mec=S.COL["tps"], mew=S.LW["overlay"],
                     ms=S.MS["tps_circle"], zorder=7, label="TPS seed contour")
        handles.append(h)
    if gt_c:
        h, = ax.plot([fnum(r["x_ras"]) for r in gt_c], [fnum(r["y_ras"]) for r in gt_c], ls="none", marker="x",
                     mec=S.COL["gtcore"], mew=1.1, ms=S.MS["gtcore_x"], zorder=8,
                     path_effects=S.halo(1.1, "black", 0.6), label="gtcore seed")
        handles.append(h)
    if gt_u:
        h, = ax.plot([fnum(r["x_ras"]) for r in gt_u], [fnum(r["y_ras"]) for r in gt_u], ls="none", marker="D",
                     mfc=S.COL["gtcore"], mec="black", mew=0.5, ms=S.MS["uncontoured"], zorder=9,
                     label="uncontoured source (gtcore)")
        handles.append(h)
    S.legend_below_image(fig, handles)
    outs = S.save_panel(fig, ctx["out"], "f4a_seed_slice")
    uncont_all = [r for r in rows if r["set"] == "gtcore_seed" and r["contoured"] != "True"]
    d = clinical_slice()
    return {"outputs": outs,
            "key_numbers": {"slice_k": int(d["slice_k"]), "slice_z_ras_mm": float(d["slice_z_ras"]),
                            "near_mm": F4A_NEAR_MM, "tps_contours_on_slice": len(tps),
                            "gtcore_seeds_on_slice": len(gt), "uncontoured_on_slice": len(gt_u),
                            "uncontoured_sources_dz_mm": {r["seed_id"]: fnum(r["dz_mm"]) for r in uncont_all},
                            "window": "W 400 / L 40", "crop": S.CLINICAL_CROP},
            "note": ("the two uncontoured sources lie %s mm from this slice and are not drawn"
                     % ", ".join("%+.1f" % fnum(r["dz_mm"]) for r in uncont_all)) if not gt_u else ""}


def f5a_isodoses(ctx) -> Dict[str, Any]:
    """F5(a): the same slice with the TPS 30 / 60 Gy isodoses (black, dashed)
    and gtcore's (vermilion), and the CTV_GT outline."""
    fig, ax, crop = _clinical_ct(S.SIZE["f5a"], legend_rows=2)
    con = clinical_contours()
    for lvl in ("3000", "6000"):
        draw_polylines(ax, con.get(("rtdose_iso", lvl), []), S.COL["tps"], S.LW["ref"], ls=(0, (3.2, 1.8)),
                       halo_colour="white", zorder=6)
        draw_polylines(ax, con.get((F5_GTCORE_FIELD + "_iso", lvl), []), S.COL["gtcore"], S.LW["overlay"],
                       zorder=7)
    draw_polylines(ax, con.get(("ctv_gt_outline", "nan"), []), S.COL["ctv_gt"], S.LW["overlay"], zorder=5)
    handles = [Line2D([], [], color=S.COL["tps"], lw=S.LW["ref"], ls=(0, (3.2, 1.8)), label="TPS 30 / 60 Gy"),
               Line2D([], [], color=S.COL["gtcore"], lw=S.LW["overlay"], label="gtcore 30 / 60 Gy"),
               Line2D([], [], color=S.COL["ctv_gt"], lw=S.LW["overlay"], label="CTV_GT")]
    S.legend_below_image(fig, handles)
    outs = S.save_panel(fig, ctx["out"], "f5a_isodoses")
    d = clinical_slice()
    kn = {"slice_k": int(d["slice_k"]), "levels_cgy": [3000, 6000], "gtcore_field": F5_GTCORE_FIELD,
          "window": "W 400 / L 40", "crop": S.CLINICAL_CROP}
    for src, lab in (("RTDOSE", "tps"), ("gtcore@32 gtcore seeds", "gtcore32")):
        kn["ctv_gt_D90_cGy_" + lab] = table3_value("HRCTV_PHYS", src, "D90")
    return {"outputs": outs, "key_numbers": kn, "note": ""}


def f6_targets(ctx) -> Dict[str, Any]:
    """F6: the same slice with the cavity (Cav_Post) filled grey, the
    physician's CTV_GT filled green and the full 5 mm rind outlined (dashed)."""
    fig, ax, crop = _clinical_ct(S.SIZE["f6"], legend_rows=2)
    d = clinical_slice()
    ext = _extent(d["x_ras"], d["y_ras"])
    mask_overlay(ax, np.asarray(d["mask_ctv_gt"], bool), S.COL["ctv_gt"], S.ALPHA["ctv_fill"], ext, zorder=3)
    con = clinical_contours()
    draw_polylines(ax, con.get(("rind5_cavpost_outline", "nan"), []), S.COL["rind"], S.LW["ref"],
                   ls=(0, (3.2, 1.8)), halo_colour="white", zorder=6, min_length_mm=F6_MIN_OUTLINE_MM)
    draw_polylines(ax, con.get(("cav_post_outline", "nan"), []), "white", S.LW["overlay"], halo_colour="black",
                   zorder=7, min_length_mm=F6_MIN_OUTLINE_MM)
    handles = [Line2D([], [], color="white", lw=S.LW["overlay"], label="cavity (Cav_Post)",
                      path_effects=S.halo(S.LW["overlay"], "black", 0.9)),
               Patch(facecolor=S.COL["ctv_gt"], alpha=S.ALPHA["ctv_fill"], edgecolor="none", label="physician's CTV_GT"),
               Line2D([], [], color=S.COL["rind"], lw=S.LW["ref"], ls=(0, (3.2, 1.8)), label="full 5 mm rind")]
    S.legend_below_image(fig, handles)
    outs = S.save_panel(fig, ctx["out"], "f6_target_definition")
    kn = {"slice_k": int(d["slice_k"]), "window": "W 400 / L 40", "crop": S.CLINICAL_CROP}
    for pref, lab in (("HRCTV_PHYS", "ctv_gt"), ("RIND5(Cav_Post", "rind5_cavpost"), ("IMPLANT_REACH(r=10", "reach10")):
        kn[lab + "_volume_cc"] = table3_value(pref, "-", "volume_cc")
        kn[lab + "_D90_cGy_rtdose"] = table3_value(pref, "RTDOSE", "D90")
        kn[lab + "_D90_cGy_gtcore32"] = table3_value(pref, "gtcore@32 gtcore seeds", "D90")
    kn["cav_post_volume_cc"] = table3_value("RTSTRUCT", "-", "volume_cc")
    return {"outputs": outs, "key_numbers": kn,
            "note": "CTV_GT fill = the CT-grid mask (pixel exact); Cav_Post and the full rind are the committed outlines "
                    "(islands < %g mm, voxel specks at the Brain clip, not drawn)" % F6_MIN_OUTLINE_MM}


# ============================================================ panels: F5(b) DVH
def f5b_dvh(ctx) -> Dict[str, Any]:
    """F5(b): cumulative DVH on CTV_GT -- TPS (black), gtcore at the 32
    gtcore seeds (vermilion), gtcore at the 30 contoured seeds (blue, dashed)
    and the 95 % seed-position MC band (vermilion, 20 %); rx dotted."""
    rows = read_csv(dpath("clinical_dvh_hrctv.csv"))
    band_rows = read_csv(dpath("f5b_dvh_band.csv"))
    unc = read_csv(dpath("uncertainty_d90_v100.csv"))
    x = np.array([fnum(r["dose_cgy"]) for r in rows]) / 100.0
    tps = 100 * np.array([fnum(r["rtdose"]) for r in rows])
    g32 = 100 * np.array([fnum(r[F5_DVH_GTCORE32]) for r in rows])
    g30 = 100 * np.array([fnum(r[F5_DVH_GTCORE30]) for r in rows])
    b = [r for r in band_rows if r["band"] == F5_BAND[0] and r["seed_set"] == F5_BAND[1]]
    if not b:
        raise Skip("band %s/%s not in f5b_dvh_band.csv" % F5_BAND)
    bx = np.array([fnum(r["level_cgy"]) for r in b]) / 100.0
    blo = 100 * np.array([fnum(r["p2.5"]) for r in b])
    bhi = 100 * np.array([fnum(r["p97.5"]) for r in b])

    def stat(band, seed_set, statistic, col="nominal"):
        for r in unc:
            if r["band"] == band and r["seed_set"] == seed_set and r["statistic"] == statistic:
                return fnum(r[col])
        return float("nan")

    d90_tps = stat("rtdose", "tps", "D90")
    d90_32 = stat(F5_BAND[0], F5_BAND[1], "D90")
    d90_30 = stat(F5_BAND[0], "contoured30", "D90")
    d90_lo, d90_hi = stat(*F5_BAND, "D90", "p2.5"), stat(*F5_BAND, "D90", "p97.5")

    fig, (ax,) = S.chart_axes(S.SIZE["f5b"])
    ax.yaxis.grid(True, color="#D9D9D9", lw=S.LW["grid"])
    ax.set_axisbelow(True)
    ax.fill_between(bx, blo, bhi, color=S.COL["band"], alpha=S.ALPHA["band"], lw=0, zorder=1,
                    label="95 % band, seed-position MC")
    ax.axvline(S.DVH_AXES["rx_gy"], color="black", lw=S.LW["rx"], ls=(0, (1, 2)), zorder=2)
    ax.plot(x, tps, color=S.COL["tps"], lw=S.LW["data"], zorder=4, label="TPS (D90 %.1f Gy)" % (d90_tps / 100))
    ax.plot(x, g32, color=S.COL["gtcore"], lw=S.LW["data"], zorder=5,
            label="gtcore, 32 seeds (%.1f Gy)" % (d90_32 / 100))
    ax.plot(x, g30, color=S.COL["gtcore30"], lw=S.LW["ref"], ls=(0, (4, 2)), zorder=3,
            label="gtcore, 30 contoured seeds (%.1f Gy)" % (d90_30 / 100))
    ax.set_xlim(*S.DVH_AXES["xlim"])
    ax.set_ylim(*S.DVH_AXES["ylim"])
    ax.set_xticks(S.DVH_AXES["xticks"])
    ax.set_yticks(S.DVH_AXES["yticks"])
    ax.set_xlabel("dose (Gy)")
    ax.set_ylabel("CTV_GT volume ≥ dose (%)")
    handles, labels = ax.get_legend_handles_labels()
    order = [labels.index(l) for l in labels if l.startswith("TPS")] + \
            [labels.index(l) for l in labels if l.startswith("gtcore, 32")] + \
            [labels.index(l) for l in labels if l.startswith("gtcore, 30")] + \
            [labels.index(l) for l in labels if l.startswith("95")]
    leg = S.legend(ax, [handles[i] for i in order], [labels[i] for i in order], loc="lower left",
                   frameon=True, framealpha=1.0, facecolor="white", edgecolor="none", fancybox=False)
    leg.set_zorder(10)
    outs = S.save_panel(fig, ctx["out"], "f5b_dvh")
    return {"outputs": outs,
            "key_numbers": {"D90_cGy": {"tps": d90_tps, "gtcore32": d90_32, "gtcore30_contoured": d90_30},
                            "D90_band95_cGy_gtcore32": [d90_lo, d90_hi],
                            "V100": {"tps": stat("rtdose", "tps", "V100"), "gtcore32": stat(*F5_BAND, "V100"),
                                     "gtcore30_contoured": stat(F5_BAND[0], "contoured30", "V100")},
                            "V100_band95_gtcore32": [stat(*F5_BAND, "V100", "p2.5"), stat(*F5_BAND, "V100", "p97.5")],
                            "band": "%s / %s (p2.5-p97.5 of 1000 draws)" % F5_BAND,
                            "curves": {"tps": "rtdose", "gtcore32": F5_DVH_GTCORE32, "gtcore30": F5_DVH_GTCORE30}},
            "note": "gtcore@30 = the 30 TPS-contoured seed positions (contoured30)"}


# ============================================================ 3-D renders (PyVista, off-screen)
def _pv():
    try:
        import pyvista as pv
    except Exception as exc:  # pragma: no cover
        raise Skip("pyvista unavailable (%s)" % exc)
    pv.OFF_SCREEN = True
    return pv


def _polydata(pv, vertices: np.ndarray, faces: np.ndarray):
    f = np.asarray(faces, np.int64)
    return pv.PolyData(np.asarray(vertices, float), np.hstack([np.full((len(f), 1), 3, np.int64), f]).ravel())


def _slab(pv, tissue: np.ndarray, cavity: np.ndarray, n: int):
    """A closed 4 mm tile: tissue face, cavity face and the four side strips
    of two n x n sheets (row-major grids)."""
    t = np.asarray(tissue, float).reshape(n, n, 3)
    c = np.asarray(cavity, float).reshape(n, n, 3)
    verts = np.vstack([t.reshape(-1, 3), c.reshape(-1, 3)])
    faces = []

    def quad(a, b, cc, d):
        faces.append([a, b, cc])
        faces.append([a, cc, d])

    N = n * n
    for r in range(n - 1):
        for col in range(n - 1):
            a, b, cc, d = r * n + col, r * n + col + 1, (r + 1) * n + col + 1, (r + 1) * n + col
            quad(a, d, cc, b)                  # tissue face
            quad(N + a, N + b, N + cc, N + d)  # cavity face
    border = [r * n + 0 for r in range(n)] + [(n - 1) * n + col for col in range(1, n)] + \
             [r * n + (n - 1) for r in range(n - 2, -1, -1)] + [0 * n + col for col in range(n - 2, 0, -1)]
    for i in range(len(border)):
        a, b = border[i], border[(i + 1) % len(border)]
        quad(a, b, N + b, N + a)
    return _polydata(pv, verts, np.array(faces))


def _grid_faces(n: int) -> np.ndarray:
    f = []
    for r in range(n - 1):
        for c in range(n - 1):
            a, b, cc, d = r * n + c, r * n + c + 1, (r + 1) * n + c + 1, (r + 1) * n + c
            f += [[a, b, cc], [a, cc, d]]
    return np.array(f, np.int64)


def _sheet_edges(pv, tissue: np.ndarray, n: int):
    t = np.asarray(tissue, float).reshape(n, n, 3)
    loop = np.vstack([t[0, :], t[1:, -1], t[-1, -2::-1], t[-2:0:-1, 0]])
    loop = np.vstack([loop, loop[:1]])
    return pv.lines_from_points(loop)


def _flat_sheet(center: np.ndarray, u: np.ndarray, v: np.ndarray, half_u: float, half_v: float, n: int):
    us = np.linspace(-half_u, half_u, n)
    vs = np.linspace(-half_v, half_v, n)
    uu, vv = np.meshgrid(us, vs)
    return center + uu.ravel()[:, None] * u + vv.ravel()[:, None] * v


def half_tile_sheets(c0: np.ndarray, c1: np.ndarray, cavity_center: np.ndarray, n: int,
                     seed_offset: float = 3.0, cavity_face: float = 1.0):
    """A 20 x 10 mm half tile from the pair geometry (the surgeon's cut
    across the seed rows: the two seeds end to end along the tile's long
    axis), as (tissue, cavity) n x n sheets in RAS."""
    mid = 0.5 * (c0 + c1)
    u = c1 - c0
    u /= np.linalg.norm(u)
    toward = cavity_center - mid
    toward -= u * np.dot(toward, u)
    toward /= np.linalg.norm(toward)
    v = np.cross(toward, u)
    v /= np.linalg.norm(v)
    seed_sheet = _flat_sheet(mid, u, v, 10.0, 5.0, n)
    return seed_sheet - seed_offset * toward, seed_sheet + cavity_face * toward


def tile_colour_list(n_tiles: int, degraded: Sequence[bool]) -> List[str]:
    """Fixed palette order (_figstyle.TILE_COLOURS, never cycled, no grey:
    grey is the cavity); the attention colour vermilion goes to a degraded
    (crumpled) tile."""
    base = list(S.TILE_COLOURS)
    out, k = [], 0
    for i in range(n_tiles):
        if degraded[i]:
            out.append(S.OI["vermilion"])
        else:
            out.append(base[k % len(base)])
            k += 1
    return out


def _darken(colour: str, f: float = 0.55) -> Tuple[float, float, float]:
    from matplotlib.colors import to_rgb
    r, g, b = to_rgb(colour)
    return (r * f, g * f, b * f)


def _drop_small_pieces(pv, pd, min_frac: float = 0.02, min_cells: int = 50):
    """Keep the connected pieces of a surface with >= min_frac of the largest
    piece's faces (drops voxel specks / tiny inner shells that render as
    star-shaped artefacts)."""
    conn = pd.connectivity("all")
    rid = np.asarray(conn.cell_data["RegionId"])
    counts = np.bincount(rid)
    keep = np.flatnonzero(counts >= max(min_cells, min_frac * counts.max()))
    out = conn.extract_cells(np.isin(rid, keep)).extract_surface(algorithm="dataset_surface")
    return out.triangulate().clean()


def smooth_mesh(pv, vertices: np.ndarray, faces: np.ndarray, pitch_mm: float = S.RENDER["remesh_pitch_mm"],
                sigma_mm: Sequence[float] = S.RENDER["remesh_sigma_mm"]):
    """Display surface of a closed cavity mesh: voxelise (trimesh, pitch
    ``pitch_mm``, interior filled), Gaussian-smooth the mask (``sigma_mm`` per
    RAS axis; z larger, the RTSTRUCT planes are 1 mm apart), marching cubes at
    the iso-level that preserves the original enclosed volume (within 0.5 %),
    a light Taubin pass, consistent outward normals.  Only what is drawn
    changes; the committed geometry is untouched.  Falls back to Taubin
    smoothing of the mesh itself if the mesh is not closed."""
    import trimesh
    from scipy.ndimage import binary_fill_holes, gaussian_filter
    from skimage.measure import marching_cubes

    raw = _drop_small_pieces(pv, _polydata(pv, vertices, faces).clean())
    tm = trimesh.Trimesh(np.asarray(raw.points), np.asarray(raw.faces).reshape(-1, 4)[:, 1:], process=True)
    if not tm.is_watertight:
        pd = raw.smooth_taubin(n_iter=S.RENDER["mesh_smooth_iter"], pass_band=S.RENDER["mesh_smooth_pass_band"])
        return pd.compute_normals(consistent_normals=True, auto_orient_normals=True, split_vertices=False)
    target = abs(tm.volume)
    vg = tm.voxelized(pitch_mm)
    pad = int(np.ceil(3.0 * max(sigma_mm) / pitch_mm)) + 2
    mask = np.pad(binary_fill_holes(vg.matrix), pad).astype(np.float32)
    field = gaussian_filter(mask, [sg / pitch_mm for sg in sigma_mm])
    best = None
    for level in np.arange(0.40, 0.86, 0.025):
        v, f, _, _ = marching_cubes(field, float(level), spacing=(pitch_mm,) * 3)
        vol = abs(trimesh.Trimesh(v, f, process=False).volume)
        err = abs(vol / target - 1.0)
        if best is None or err < best[0]:
            best = (err, v, f)
        if vol < target:
            break
    _err, v, f = best
    v = trimesh.transform_points(v / pitch_mm - pad, vg.transform)
    pd = _polydata(pv, v, f).clean().smooth_taubin(n_iter=20, pass_band=0.05)
    return pd.compute_normals(consistent_normals=True, auto_orient_normals=True, split_vertices=False)


def context_surface(pv, vertices: np.ndarray, faces: np.ndarray, center: np.ndarray, radius: float,
                    hole_mm: float = S.RENDER["context_hole_mm"], min_component_frac: float = 0.02):
    """A clean display surface from a printed-wall mesh: small holes filled,
    small disconnected pieces dropped, Taubin-smoothed, then clipped by a
    sphere (centre, radius) on an interpolated distance field so the
    boundary is a smooth curve, not a jagged box edge."""
    pd = _polydata(pv, vertices, faces).clean()
    pd = _drop_small_pieces(pv, pd.fill_holes(hole_mm), min_frac=min_component_frac)
    pd = pd.smooth_taubin(n_iter=S.RENDER["context_smooth_iter"], pass_band=S.RENDER["context_smooth_pass_band"],
                          boundary_smoothing=True)
    # the face-selected wall has saw-tooth borders: trim them along a smooth
    # contour of the distance to the border (interpolated clip), then clip by
    # the sphere the same way, so every visible boundary is a clean curve
    from scipy.spatial import cKDTree
    pts = np.asarray(pd.points)
    edges = pd.extract_feature_edges(boundary_edges=True, feature_edges=False, manifold_edges=False,
                                     non_manifold_edges=False)
    if edges.n_points:
        dist, _ = cKDTree(np.asarray(edges.points)).query(pts)
        pd.point_data["from_border"] = dist
        pd = pd.clip_scalar(scalars="from_border", value=S.RENDER["context_border_trim_mm"], invert=False)
        pd = pd.extract_surface(algorithm="dataset_surface").triangulate().clean()
    d = radius - np.linalg.norm(np.asarray(pd.points) - np.asarray(center)[None, :], axis=1)
    pd.point_data["inside"] = d
    pd = pd.clip_scalar(scalars="inside", value=0.0, invert=False)
    pd = pd.extract_surface(algorithm="dataset_surface").triangulate().clean()
    return pd.compute_normals(consistent_normals=True, split_vertices=False)


def render_scene(camera: Dict[str, Sequence[float]], mesh: Optional[Tuple[np.ndarray, np.ndarray]],
                 tiles: Sequence[Dict[str, Any]], seeds: Sequence[Dict[str, Any]],
                 mesh_opacity: float = S.RENDER["mesh_opacity"], eligible: Optional[np.ndarray] = None,
                 window=S.RENDER["window_px"], smooth: bool = True, culling: Optional[str] = "default") -> np.ndarray:
    """Off-screen render.  ``tiles``: dicts with tissue (n*n, 3), n, colour,
    highlight; ``seeds``: dicts with centers, axes, colour.  The context mesh
    is display-smoothed, drawn with its front faces culled (the camera looks
    at the wall behind) and only its true silhouette outlined; tiles are
    translucent sheets with a darker edge of their own hue, depth-peeled."""
    pv = _pv()
    pl = pv.Plotter(off_screen=True, window_size=list(window), lighting="three lights")
    pl.set_background("white")
    try:
        pl.enable_anti_aliasing("ssaa")
    except Exception:
        pass
    pl.enable_parallel_projection()
    if mesh is not None:
        if hasattr(mesh, "n_cells"):            # an already prepared pyvista surface
            pd = mesh
        else:
            pd = smooth_mesh(pv, *mesh) if smooth else _polydata(pv, *mesh)
        # no edge lines of any kind on the context surface: shading only
        kw = dict(opacity=mesh_opacity, smooth_shading=True, specular=0.2, specular_power=15, ambient=0.25,
                  diffuse=0.75,
                  culling=camera.get("mesh_culling", S.RENDER["mesh_culling"]) if culling == "default" else culling)
        if eligible is not None and len(eligible) == pd.n_cells:
            pd.cell_data["eligible"] = np.asarray(eligible, float)
            pl.add_mesh(pd, scalars="eligible", cmap=[S.COL["mesh"], S.COL["mesh_eligible"]], clim=[0, 1],
                        show_scalar_bar=False, **kw)
        else:
            pl.add_mesh(pd, color=S.COL["mesh"], **kw)
    for t in tiles:
        surf = _polydata(pv, t["tissue"], _grid_faces(t["n"]))
        hl = bool(t.get("highlight"))
        op = t.get("opacity", S.RENDER["tile_opacity_highlight"] if hl else S.RENDER["tile_opacity"])
        pl.add_mesh(surf, color=t["colour"], opacity=op, smooth_shading=True, specular=0.0 if hl else 0.1,
                    ambient=0.15 if hl else 0.3, diffuse=0.6 if hl else 0.75)
        edges = _sheet_edges(pv, t["tissue"], t["n"])
        pl.add_mesh(edges, color=(0.08, 0.08, 0.08) if hl else _darken(t["colour"]),
                    line_width=S.RENDER["tile_edge_lw"] * (1.8 if hl else 1.0), render_lines_as_tubes=True)
    for sd in seeds:
        L, D = float(sd.get("length", 4.5)), float(sd.get("diameter", 0.8))
        for c, a in zip(sd["centers"], sd["axes"]):
            cap = pv.Capsule(center=c, direction=a, radius=D / 2.0, cylinder_length=max(L - D, 0.1), resolution=24)
            pl.add_mesh(cap, color=sd["colour"], smooth_shading=True, specular=0.15, specular_power=20,
                        ambient=0.3, diffuse=0.8)
    pl.camera.position = tuple(camera["position"])
    pl.camera.focal_point = tuple(camera["focal_point"])
    pl.camera.up = tuple(camera["view_up"])
    pl.camera.parallel_scale = float(camera["parallel_scale"])
    pl.camera_set = True
    try:
        pl.enable_depth_peeling(number_of_peels=16, occlusion_ratio=0.0)
    except Exception:
        pass
    img = pl.screenshot(None, return_img=True, transparent_background=False)
    pl.close()
    return np.asarray(img)


def fit_camera(direction: Sequence[float], points: np.ndarray, margin: float = 1.08,
               distance: float = 150.0, view_up=(0.0, 0.0, 1.0)) -> Dict[str, Any]:
    """Parallel camera looking along -``direction`` that frames ``points``
    (screen-space bounding box centred, half-size * margin).  Used once to
    derive the stored cameras in _figstyle.CAMERA (never at render time)."""
    v = -np.asarray(direction, float)
    v /= np.linalg.norm(v)
    up = np.asarray(view_up, float) - v * np.dot(view_up, v)
    up /= np.linalg.norm(up)
    r = np.cross(v, up)
    c0 = points.mean(axis=0)
    x, y = (points - c0) @ r, (points - c0) @ up
    fp = c0 + 0.5 * (x.min() + x.max()) * r + 0.5 * (y.min() + y.max()) * up
    half = 0.5 * max(np.ptp(x), np.ptp(y)) * margin
    return {"focal_point": tuple(np.round(fp, 1)), "position": tuple(np.round(fp - distance * v, 1)),
            "view_up": tuple(view_up), "parallel_scale": round(float(half), 1)}


def _npz(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise Skip("missing %s" % rel(path))
    with np.load(path, allow_pickle=True) as z:
        return {k: z[k] for k in z.files}


def _implant_tiles(d: Dict[str, Any], mode: str) -> Tuple[List[Dict[str, Any]], np.ndarray, Dict[str, Any]]:
    """Tile slabs + per-seed leftover flag of one fit mode of an implant NPZ."""
    n = int(d["sheet_grid_n"])
    pre = mode + "_"
    if pre + "sheet_tissue_vertices" not in d:
        raise Skip("mode %r not in the NPZ (modes: %s)" % (mode, sorted({k.split("_sheet_")[0] for k in d if "_sheet_" in k})))
    kinds = [str(k) for k in d[pre + "tile_kind"]]
    degraded = [bool(x) for x in d[pre + "tile_degraded"]]
    colours = tile_colour_list(len(kinds), degraded)
    tiles = []
    info = {"n_tiles": len(kinds), "n_full": kinds.count("full"), "n_half": kinds.count("half"),
            "degraded_tiles": [i for i, x in enumerate(degraded) if x], "half_tiles_from_pair_geometry": []}
    for i, kind in enumerate(kinds):
        if kind == "half":
            idx = [int(s) for s in d[pre + "tile_seed_indices"][i] if int(s) >= 0]
            c0, c1 = d["seed_centers_ras"][idx[0]], d["seed_centers_ras"][idx[1]]
            tissue, cavity = half_tile_sheets(c0, c1, np.asarray(d["cavity_center_ras"], float), n)
            info["half_tiles_from_pair_geometry"].append(idx)
        else:
            tissue, cavity = d[pre + "sheet_tissue_vertices"][i], d[pre + "sheet_cavity_vertices"][i]
        tiles.append({"tissue": tissue, "cavity": cavity, "n": n, "colour": colours[i], "highlight": degraded[i]})
    leftover = np.asarray(d[pre + "seed_leftover"], bool)
    info["n_leftover"] = int(leftover.sum())
    info["leftover_seeds"] = [int(i) for i in np.flatnonzero(leftover)]
    info["tile_colours"] = colours
    return tiles, leftover, info


def _seed_groups(d: Dict[str, Any], leftover: np.ndarray) -> List[Dict[str, Any]]:
    c, a = np.asarray(d["seed_centers_ras"], float), np.asarray(d["seed_axes_ras"], float)
    L, D = float(d["seed_length_mm"]), float(d["seed_diameter_mm"])
    return [{"centers": c[~leftover], "axes": a[~leftover], "colour": S.COL["seed"], "length": L, "diameter": D},
            {"centers": c[leftover], "axes": a[leftover], "colour": S.COL["leftover"], "length": L, "diameter": D}]


def _render_implant(ctx, stem: str, mesh_key: str, camera_key: str, mode: str) -> Dict[str, Any]:
    d = _npz(dpath("f4b_patient_implant.npz"))
    tiles, leftover, info = _implant_tiles(d, mode)
    mesh = (d[mesh_key + "_vertices"], d[mesh_key + "_faces"])
    img = render_scene(S.CAMERA[camera_key], mesh, tiles, _seed_groups(d, leftover))
    outs = S.save_raster_panel(img, ctx["out"], stem, S.RENDER["panel_in"])
    info.update({"mode": mode, "mesh": mesh_key, "camera": S.CAMERA[camera_key], "window_px": list(S.RENDER["window_px"])})
    return {"outputs": outs, "key_numbers": info, "note": ""}


def f4b_patient_3d(ctx) -> Dict[str, Any]:
    """F4(b): the patient's implant on the physician's cavity mesh (Cav_Post):
    32 seeds as capsules, the 6 fitted tile sheets, the 8 leftover seeds in
    vermilion (mode auto; --tile-mode count_6full_4half draws the halves)."""
    return _render_implant(ctx, "f4b_patient_implant", "cavity_rt", "patient", ctx["tile_mode"])


def f8a_implant_3d(ctx) -> Dict[str, Any]:
    """F8(a): the same implant on the gtcore cavity mesh the optimizer uses,
    same camera as F4(b); V100 / D90 on the full rind and CTV_GT in the manifest."""
    r = _render_implant(ctx, "f8a_patient_implant", "wall_mesh", "patient", ctx["tile_mode"])
    r["key_numbers"].update(_f8_numbers("as_implanted"))
    return r


def _f8_numbers(arm_hint: str) -> Dict[str, Any]:
    """V100 / D90 on RIND5(gtcore) and CTV_GT for the F8 titles: from the
    campaign's f8_patient_summary.csv when present, else (as-implanted only)
    from the Table 3 rows (gtcore @ 32 gtcore seeds)."""
    if os.path.exists(F8_SUMMARY_FILE):
        rows = read_csv(F8_SUMMARY_FILE)
        arms = {}
        for r in rows:
            arms[r.get("arm", "?")] = {k: fnum(v) for k, v in r.items()
                                       if k.endswith("_V100") or k.endswith("_D90_cGy") or k in ("solve_s", "n_overlaps", "n_tiles")}
        return {"f8_arms": arms, "f8_numbers_source": rel(F8_SUMMARY_FILE) + " @ " + str(header_commit(F8_SUMMARY_FILE))}
    if arm_hint == "as_implanted":
        return {"RIND5_gtcore_V100": table3_value("RIND5(gtcore", "gtcore@32 gtcore seeds", "V100"),
                "RIND5_gtcore_D90_cGy": table3_value("RIND5(gtcore", "gtcore@32 gtcore seeds", "D90"),
                "CTV_GT_V100": table3_value("HRCTV_PHYS", "gtcore@32 gtcore seeds", "V100"),
                "CTV_GT_D90_cGy": table3_value("HRCTV_PHYS", "gtcore@32 gtcore seeds", "D90"),
                "f8_numbers_source": "clinical_table3_rows.csv (gtcore @ 32 gtcore seeds); campaign f8_patient_summary.csv pending"}
    return {"f8_numbers_source": "campaign f8_patient_summary.csv pending"}


def f8b_optimized_3d(ctx) -> Dict[str, Any]:
    """F8(b): the optimizer's tiles on the same gtcore cavity mesh, same
    camera; one render per optimized arm in f8b_patient_optimized.npz
    (opt_* = optimized for RIND5; further opt<arm>_* prefixes if present)."""
    if not os.path.exists(F8B_FILE):
        raise Skip("campaign output %s not committed yet (scripts/patient_optimize.py)" % rel(F8B_FILE))
    d = _npz(F8B_FILE)
    n = int(d["sheet_grid_n"])
    prefixes = sorted({k[:-len("sheet_seed_vertices")] for k in d if k.endswith("sheet_seed_vertices") and k.startswith("opt")})
    if not prefixes:
        raise Skip("no opt*_sheet_seed_vertices arrays in %s" % rel(F8B_FILE))
    mesh = (d["wall_mesh_vertices"], d["wall_mesh_faces"])
    elig = np.asarray(d["eligible_face_mask"], bool) if "eligible_face_mask" in d else None
    outs, kn = [], {"arms": {}, "camera": S.CAMERA["patient"], "eligible_fraction_area":
                    float(d["eligible_fraction_area"]) if "eligible_fraction_area" in d else None}
    for pre in prefixes:
        wall = d.get(pre + "sheet_wall_vertices", d.get(pre + "sheet_tissue_vertices"))
        cav = d[pre + "sheet_cavity_vertices"]
        n_t = len(wall)
        colours = tile_colour_list(n_t, [False] * n_t)
        tiles = [{"tissue": wall[i], "cavity": cav[i], "n": n, "colour": colours[i]} for i in range(n_t)]
        seeds = [{"centers": d[pre + "seed_centers_ras"], "axes": d[pre + "seed_axes_ras"], "colour": S.COL["seed"],
                  "length": float(d["seed_length_mm"]), "diameter": float(d["seed_diameter_mm"])}]
        img = render_scene(S.CAMERA["patient"], mesh, tiles, seeds, eligible=elig)
        suffix = "" if pre == "opt_" else "_" + pre.strip("_")
        outs += S.save_raster_panel(img, ctx["out"], "f8b_optimized" + suffix, S.RENDER["panel_in"])
        kn["arms"][pre.rstrip("_")] = {"n_tiles": n_t, "sa_seed": int(d["opt_sa_seed"]) if "opt_sa_seed" in d else None}
    kn.update(_f8_numbers("optimized"))
    return {"outputs": outs, "key_numbers": kn, "note": "eligible wall tinted darker grey"}


def f4c_phantom_3d(ctx) -> Dict[str, Any]:
    """F4(c): PH8 -- the 32 seeds and the 8 fitted sheets (count-free fit),
    the crumpled tile highlighted (vermilion, black edge), on the registered
    tile-less twin's printed wall (translucent grey, cropped to the implant)."""
    d = _npz(dpath("f4c_phantom_implant.npz"))
    tiles, leftover, info = _implant_tiles(d, "auto")
    wall_p = dpath("f4c_phantom_wall.npz")
    mesh, mesh_note = None, "none (f4c_phantom_wall.npz missing: run scripts/export_phantom_wall.py)"
    if os.path.exists(wall_p):
        w = _npz(wall_p)
        sc = np.asarray(d["seed_centers_ras"], float)
        lo, hi = sc.min(axis=0), sc.max(axis=0)
        centre, radius = 0.5 * (lo + hi), 0.5 * float(np.linalg.norm(hi - lo)) + S.RENDER["context_clip_margin_mm"]
        mesh = context_surface(_pv(), w["wall_vertices"], w["wall_faces"], centre, radius)
        mesh_note = ("registered PH0 wall (phantom_wall_and_cavity): holes <= %g mm filled, small pieces dropped, "
                     "Taubin-smoothed, clipped by a sphere r = %.1f mm (seed half-diagonal + %g mm) around the implant"
                     % (S.RENDER["context_hole_mm"], radius, S.RENDER["context_clip_margin_mm"]))
    img = render_scene(S.CAMERA["phantom"], mesh, tiles, _seed_groups(d, leftover),
                       mesh_opacity=S.RENDER["phantom_mesh_opacity"])
    outs = S.save_raster_panel(img, ctx["out"], "f4c_phantom_implant", S.RENDER["panel_in"])
    info.update({"mode": "auto", "mesh": mesh_note,
                 "camera": S.CAMERA["phantom"],
                 "fold_deg": [round(float(x), 1) for x in d["auto_tile_fold_deg"]],
                 "bent_rms_mm": [round(float(x), 2) for x in d["auto_tile_bent_rms_mm"]]})
    return {"outputs": outs, "key_numbers": info, "note": ""}


# ============================================================ Supplement charts
def s2a_spacing(ctx) -> Dict[str, Any]:
    """S2(a): seed recall and 3-D seed error vs slice spacing on the synthetic
    head phantom (5 realizations, mean +- SD), shipped (adaptive) vs fixed
    detection thresholds; two stacked small multiples, one axis each."""
    rows = read_csv(dpath("validation_spacing.csv"))
    dzs = sorted({round(fnum(r["dz"]), 2) for r in rows})
    fig, (ax1, ax2) = S.chart_axes(S.SIZE["s2a"], nrows=2, sharex=True)
    style = {"adaptive": ("adaptive thresholds (shipped)", S.COL["adaptive"], "o", "-"),
             "fixed": ("fixed thresholds", S.COL["fixed"], "s", (0, (4, 2)))}
    kn: Dict[str, Any] = {"dz_mm": dzs}
    for i, (mode, (lab, col, mk, ls)) in enumerate(style.items()):
        rec, err = [], []
        for dz in dzs:
            sub = [r for r in rows if round(fnum(r["dz"]), 2) == dz and r["mode"] == mode]
            rv = np.array([fnum(r["recall"]) for r in sub])
            ev = np.array([fnum(r["err_mean"]) for r in sub])
            rec.append((rv.mean(), rv.std(ddof=1) if rv.size > 1 else 0.0))
            fin = ev[np.isfinite(ev)]          # no matched seed (recall 0) -> no error point
            err.append((float(fin.mean()) if fin.size else float("nan"),
                        float(fin.std(ddof=1)) if fin.size > 1 else 0.0))
        off = (i - 0.5) * 0.04
        xs = np.array(dzs) + off
        ax1.errorbar(xs, [m for m, _ in rec], yerr=[s for _, s in rec], color=col, marker=mk, ls=ls,
                     lw=S.LW["data"], ms=S.MS["chart"], capsize=1.8, elinewidth=0.6, label=lab, zorder=3)
        ax2.errorbar(xs, [m for m, _ in err], yerr=[s for _, s in err], color=col, marker=mk, ls=ls,
                     lw=S.LW["data"], ms=S.MS["chart"], capsize=1.8, elinewidth=0.6, label=lab, zorder=3)
        kn[mode] = {"recall_mean": [round(m, 3) for m, _ in rec], "recall_sd": [round(s, 3) for _, s in rec],
                    "err_mean_mm": [round(m, 3) for m, _ in err], "err_sd_mm": [round(s, 3) for _, s in err]}
    ax1.set_ylim(0, 1.05)
    ax1.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax1.set_ylabel("seed recall")
    ax2.set_ylim(0, None)
    ax2.set_ylabel("3-D seed error (mm)")
    ax2.set_xlabel("slice spacing (mm)")
    ax2.set_xticks(dzs)
    ax2.set_xlim(dzs[0] - 0.3, dzs[-1] + 0.3)
    S.legend(ax1, loc="lower left")
    outs = S.save_panel(fig, ctx["out"], "s2a_recall_error_vs_spacing")
    return {"outputs": outs, "key_numbers": kn, "note": "synthetic head phantom, 0.7 mm base, 3 tiles, rng 0-4; adaptive = shipped"}


def s2b_axis_boxplot(ctx) -> Dict[str, Any]:
    """S2(b): per-axis seed-position difference, 2 mm export vs 1 mm thin-cut
    (real data), as a box plot per axis -- rendered only from a committed
    per-seed table; otherwise a placeholder note, never a stand-in."""
    if not os.path.exists(S2B_FILE):
        note = ("PENDING: S2(b) needs a committed per-seed table %s with columns pair,seed_id,dx_mm,dy_mm,dz_mm "
                "(pair = 'PT-CT2mm vs PT-CT1mm' when the gapped export is restored, else the two substitutes of "
                "docs/paper2-decisions.md: 'PT-CT-s3 vs PT-CT1mm' and 'PT-CT1mm decimated 2 mm vs PT-CT1mm'). "
                "The quoted aggregate (25 seeds, per-axis SD 0.51 / 0.40 / 0.63 mm, docs/localization-notes.md) "
                "is not regenerable from the tag and is not drawn." % rel(S2B_FILE))
        p = os.path.join(ctx["out"], "s2b_axis_boxplot.PENDING.md")
        os.makedirs(ctx["out"], exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# S2(b) placeholder\n\n" + note + "\n")
        return {"outputs": [p], "key_numbers": {}, "note": note, "status": "pending"}
    rows = read_csv(S2B_FILE)
    pairs = sorted({r["pair"] for r in rows})
    fig, (ax,) = S.chart_axes(S.SIZE["s2b"])
    data, labels, pos, cols = [], [], [], []
    palette = [S.OI["blue"], S.OI["orange"], S.OI["green"]]
    for pi, pair in enumerate(pairs):
        for ai, axis in enumerate(("dx_mm", "dy_mm", "dz_mm")):
            v = np.array([fnum(r[axis]) for r in rows if r["pair"] == pair])
            data.append(v[np.isfinite(v)])
            labels.append(axis[1])
            pos.append(ai * (len(pairs) + 1) + pi)
            cols.append(palette[pi % len(palette)])
    bp = ax.boxplot(data, positions=pos, widths=0.7, patch_artist=True, showfliers=True,
                    flierprops={"marker": ".", "ms": 2.5, "mec": "none"}, medianprops={"color": "black", "lw": 0.8},
                    whiskerprops={"lw": 0.6}, capprops={"lw": 0.6}, boxprops={"lw": 0.6})
    for patch, c in zip(bp["boxes"], cols):
        patch.set_facecolor(c)
        patch.set_alpha(0.35)
        patch.set_edgecolor(c)
    ax.axhline(0, color="black", lw=0.5)
    ax.set_xticks([ai * (len(pairs) + 1) + (len(pairs) - 1) / 2 for ai in range(3)])
    ax.set_xticklabels(["x (R–L)", "y (A–P)", "z (S–I)"])
    ax.set_ylabel("seed position difference (mm)")
    S.legend(ax, [Patch(facecolor=palette[i % 3], alpha=0.35, edgecolor=palette[i % 3]) for i in range(len(pairs))],
             pairs, loc="upper left")
    outs = S.save_panel(fig, ctx["out"], "s2b_axis_boxplot")
    kn = {pair: {ax_: {"n": int(len(v)), "sd_mm": round(float(np.std(v, ddof=1)), 3) if len(v) > 1 else None}
                 for ax_, v in zip(("x", "y", "z"), data[i::len(pairs)] if False else [data[ai * len(pairs) + pi] for ai in range(3)])}
          for pi, pair in enumerate(pairs) for i in [pi]}
    return {"outputs": outs, "key_numbers": kn, "note": ""}


S4_ARMS = {  # arm key in v2_summary.csv -> (label, colour, marker, linestyle)
    "sa": ("simulated annealing", S.OI["blue"], "o", "-"),
    "greedy": ("greedy", S.OI["orange"], "s", "-"),
    "uniform": ("uniform heuristic", S.OI["midgrey"], "D", "-"),
    "milp": ("V100 bound (same candidates)", S.OI["black"], "^", (0, (4, 2))),
    "bound": ("V100 bound (same candidates)", S.OI["black"], "^", (0, (4, 2))),
    "greedy+local": ("greedy + local search", S.OI["purple"], "v", "-"),
    "random": ("random feasible", S.OI["sky"], "x", (0, (1, 2))),
    "continuous": ("continuous multi-start", S.OI["green"], "P", "-"),
}


def s4_v100_vs_n(ctx) -> Dict[str, Any]:
    """S4: V100 of RIND5 vs tile count N on the synthetic cavities, mean +- SD
    across cavities per arm (the Phase 4 campaign's v2_summary.csv)."""
    if not has_header(S4_FILE):
        raise Skip("%s is not the Phase 4 campaign output yet (no '# commit' header)" % rel(S4_FILE))
    rows = read_csv(S4_FILE)
    arms = []
    for r in rows:
        if r["arm"] not in arms:
            arms.append(r["arm"])
    fig, (ax,) = S.chart_axes(S.SIZE["s4"])
    kn: Dict[str, Any] = {}
    unknown = [a for a in arms if a not in S4_ARMS]
    for i, arm in enumerate([a for a in arms if a in S4_ARMS]):
        lab, col, mk, ls = S4_ARMS[arm]
        sub = sorted((r for r in rows if r["arm"] == arm), key=lambda r: fnum(r["N"]))
        n = np.array([fnum(r["N"]) for r in sub]) + (i - 1.5) * 0.06
        m = np.array([fnum(r["V100_mean"]) for r in sub])
        sd = np.array([fnum(r["V100_sd"]) for r in sub])
        ax.errorbar(n, m, yerr=sd, color=col, marker=mk, ls=ls, lw=S.LW["data"], ms=S.MS["chart"], capsize=1.8,
                    elinewidth=0.6, label=lab, zorder=3)
        kn[arm] = {"N": [int(fnum(r["N"])) for r in sub], "V100_mean": [round(float(v), 3) for v in m],
                   "V100_sd": [round(float(v), 3) for v in sd]}
    ax.set_xlabel("tiles N")
    ax.set_ylabel("V100 of the 5 mm rind")
    ax.set_ylim(0, 1.0)
    ax.set_xticks(sorted({int(fnum(r["N"])) for r in rows}))
    S.legend(ax, loc="lower right")
    outs = S.save_panel(fig, ctx["out"], "s4_v100_vs_n")
    return {"outputs": outs, "key_numbers": kn,
            "note": ("arms without a style, not drawn: %s" % unknown) if unknown else ""}


def s3_gamma_map(ctx) -> Dict[str, Any]:
    """Optional Supplement: the gamma map (3 %/2 mm, global) on the clinical
    slice -- gamma in viridis (0-2), voxels above 200 Gy grey, outside the
    30 Gy window not shown; CTV_GT outline; colour bar."""
    p = dpath(GAMMA_MAP_FILE)
    d = _npz(p)
    g = np.asarray(d["gamma"], float)
    x, y = np.asarray(d["x_mm"], float), np.asarray(d["y_mm"], float)
    dx, dy = x[1] - x[0], y[1] - y[0]
    ext = (x[0] - dx / 2, x[-1] + dx / 2, y[-1] + dy / 2, y[0] - dy / 2)
    win = np.asarray(d["mask_window"], bool)
    above = np.asarray(d["mask_above_upper"], bool)
    fig, ax = S.image_axes(S.SIZE["s3_gamma"])
    sl = clinical_slice()
    vmin, vmax = S.set_window("clinical")
    ax.imshow(sl["ct_hu"], cmap="gray", vmin=vmin, vmax=vmax, extent=_extent(sl["x_ras"], sl["y_ras"]),
              origin="upper", interpolation="nearest", zorder=0)
    gm = np.ma.masked_where(~win, g)
    im = ax.imshow(gm, cmap="viridis", vmin=0, vmax=2, extent=ext, origin="upper", interpolation="nearest", zorder=2,
                   alpha=0.9)
    mask_overlay(ax, above, "#9A9A9A", 0.9, ext, zorder=3)
    draw_polylines(ax, clinical_contours().get(("ctv_gt_outline", "nan"), []), S.COL["ctv_gt"], S.LW["overlay"], zorder=5)
    cx, cy = S.CLINICAL_CROP["center_xy"]
    half = S.CLINICAL_CROP["half_mm"]
    ax.set_xlim(cx + half, cx - half)
    ax.set_ylim(cy - half, cy + half)
    S.add_scale_bar(ax, (cx + half, cx - half, cy - half, cy + half), invert_x=True)
    cax = fig.add_axes([0.86, 0.62, 0.03, 0.30])
    cb = fig.colorbar(im, cax=cax, ticks=[0, 1, 2])
    cb.ax.tick_params(labelsize=S.FONT["legend"], colors="white", width=0.5, length=2)
    cb.outline.set_linewidth(0.5)
    cb.outline.set_edgecolor("white")
    cb.set_label("γ (3 %/2 mm)", fontsize=S.FONT["legend"], color="white")
    pr = [r for r in read_csv(dpath("gamma_pass_rates.csv"))
          if r["seed_source"] == "gtcore" and r["axes"] == "physical" and r["dose_pct"] == "3" and r["dist_mm"] == "2"
          and r["normalisation"] == "global"]
    kn = {"pass_rate_pct": {r["window"]: fnum(r["pass_rate_pct"]) for r in pr},
          "slice_z_mm": float(d["z_mm"]), "n_window_voxels_on_slice": int(win.sum()), "n_above_200Gy_on_slice": int(above.sum())}
    outs = S.save_panel(fig, ctx["out"], "s3_gamma_map")
    return {"outputs": outs, "key_numbers": kn, "note": "optional Supplement panel"}


def s5_dvh_three_targets(ctx) -> Dict[str, Any]:
    """Optional Supplement: the DVH of the clinical plan on each target
    definition (CTV_GT, full rind, implant reach) -- needs clinical_dvh_<target>.csv
    for every target; only the CTV_GT DVH is committed."""
    files = sorted(glob.glob(dpath("clinical_dvh_*.csv")))
    if len(files) < 2:
        raise Skip("only %s is committed; validation_clinical.py exports no per-target DVH for the rind / reach "
                   "(their D90s are F6 caption numbers)" % ", ".join(rel(f) for f in files))
    fig, (ax,) = S.chart_axes(S.SIZE["s5_dvh3"])
    ax.yaxis.grid(True, color="#D9D9D9", lw=S.LW["grid"])
    cols = [S.COL["ctv_gt"], S.COL["rind"], S.COL["reach"], S.OI["blue"]]
    for i, f in enumerate(files):
        rows = read_csv(f)
        x = np.array([fnum(r["dose_cgy"]) for r in rows]) / 100
        y = 100 * np.array([fnum(r["rtdose"]) for r in rows])
        ax.plot(x, y, color=cols[i % len(cols)], lw=S.LW["data"], label=os.path.basename(f)[13:-4])
    ax.axvline(S.DVH_AXES["rx_gy"], color="black", lw=S.LW["rx"], ls=(0, (1, 2)))
    ax.set_xlim(*S.DVH_AXES["xlim"])
    ax.set_ylim(*S.DVH_AXES["ylim"])
    ax.set_xlabel("dose (Gy)")
    ax.set_ylabel("volume ≥ dose (%)")
    S.legend(ax, loc="lower left")
    outs = S.save_panel(fig, ctx["out"], "s5_dvh_three_targets")
    return {"outputs": outs, "key_numbers": {}, "note": "optional Supplement panel"}


# ============================================================ registry
PANELS: List[Tuple[str, str, Callable, List[str]]] = [
    # (panel id, figure, function, source files)
    ("f2c", "F2", f2c_deform, []),
    ("f3c", "F3", f3c_phantom_slice, [dpath("f3c_phantom_slice.npz"), dpath("f4c_phantom_implant.npz")]),
    ("f4a", "F4", f4a_seed_slice, [dpath("clinical_slice.npz"), dpath("clinical_slice_seeds.csv")]),
    ("f4b", "F4", f4b_patient_3d, [dpath("f4b_patient_implant.npz")]),
    ("f4c", "F4", f4c_phantom_3d, [dpath("f4c_phantom_implant.npz"), dpath("f4c_phantom_wall.npz")]),
    ("f5a", "F5", f5a_isodoses, [dpath("clinical_slice.npz"), dpath("clinical_slice_contours.csv"),
                                 dpath("clinical_table3_rows.csv")]),
    ("f5b", "F5", f5b_dvh, [dpath("clinical_dvh_hrctv.csv"), dpath("f5b_dvh_band.csv"), dpath("uncertainty_d90_v100.csv")]),
    ("f6", "F6", f6_targets, [dpath("clinical_slice.npz"), dpath("clinical_slice_contours.csv"),
                              dpath("clinical_table3_rows.csv")]),
    ("f8a", "F8", f8a_implant_3d, [dpath("f4b_patient_implant.npz"), F8_SUMMARY_FILE, dpath("clinical_table3_rows.csv")]),
    ("f8b", "F8", f8b_optimized_3d, [F8B_FILE, F8_SUMMARY_FILE]),
    ("s2a", "S2", s2a_spacing, [dpath("validation_spacing.csv")]),
    ("s2b", "S2", s2b_axis_boxplot, [S2B_FILE]),
    ("s4", "S4", s4_v100_vs_n, [S4_FILE]),
    ("s3_gamma", "S3 (optional)", s3_gamma_map, [dpath(GAMMA_MAP_FILE), dpath("gamma_pass_rates.csv"),
                                                  dpath("clinical_slice.npz"), dpath("clinical_slice_contours.csv")]),
    ("s5_dvh3", "S5 (optional)", s5_dvh_three_targets, [dpath("clinical_dvh_hrctv.csv")]),
]
THREE_D = {"f4b", "f4c", "f8a", "f8b"}


def select(only: Optional[str]) -> List[Tuple[str, str, Callable, List[str]]]:
    if not only:
        return PANELS
    keys = [k.strip().lower() for k in only.split(",") if k.strip()]
    sel = [p for p in PANELS if any(p[0] == k or p[0].startswith(k) for k in keys)]
    if not sel:
        raise SystemExit("--only %r matches no panel; see --list" % only)
    return sel


def describe(fn: Callable) -> str:
    return " ".join((fn.__doc__ or "").split())


def list_panels() -> None:
    print("%-9s %-13s %s" % ("panel", "figure", "sources (header commit / last git commit)"))
    for pid, fig, fn, srcs in PANELS:
        recs = source_records(srcs)
        src = "; ".join("%s%s" % (r["path"], "" if r["exists"] else " [missing]") +
                        (" @ %s" % r["header_commit"] if r["header_commit"] else "") +
                        (" / git %s" % r["git_commit"] if r["git_commit"] else "") for r in recs) or "(geometry only)"
        desc = describe(fn)
        desc = desc.split(": ", 1)[1] if ": " in desc[:30] else desc
        print("%-9s %-13s %s" % (pid, fig, (desc[:100] + "...") if len(desc) > 100 else desc))
        print("%-9s %-13s   %s" % ("", "", src))


def _json_clean(o):
    """Strict JSON: NaN / inf -> null, numpy scalars / arrays -> Python."""
    if isinstance(o, dict):
        return {str(k): _json_clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_clean(v) for v in o]
    if hasattr(o, "tolist"):
        return _json_clean(o.tolist())
    if isinstance(o, float) and not np.isfinite(o):
        return None
    return o


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", help="comma list of panel ids or prefixes (f5b, f4, s2a, ...)")
    ap.add_argument("--list", action="store_true", help="print every panel with its sources and their commits")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output directory (default output/figures)")
    ap.add_argument("--manifest", default=MANIFEST_PATH, help="manifest path (default docs/figures/manifest.json)")
    ap.add_argument("--dpi", type=int, default=S.DPI)
    ap.add_argument("--tile-mode", default=F4_TILE_MODE_DEFAULT, choices=["auto", "count_6full_4half", "count8", "prior8"],
                    help="F4(b)/F8(a) tile fit to draw (default auto: 6 sheets + 8 leftover seeds)")
    ap.add_argument("--no-3d", action="store_true", help="skip the PyVista renders")
    args = ap.parse_args(argv)

    if args.list:
        list_panels()
        return 0

    S.apply_style()
    S.DPI = args.dpi
    os.makedirs(args.out, exist_ok=True)
    ctx: Dict[str, Any] = {"out": args.out, "tile_mode": args.tile_mode}
    manifest: Dict[str, Any] = {}
    if os.path.exists(args.manifest):
        try:
            with open(args.manifest, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception:
            manifest = {}
    manifest.update({
        "script": "scripts/paper_figures.py", "style": "scripts/_figstyle.py", "script_commit": git_head(),
        "generated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "dpi_png": args.dpi,
        "out_dir": rel(args.out), "tile_mode": args.tile_mode,
        "constants": {"clinical_crop": S.CLINICAL_CROP, "ct_window": S.CT_WINDOW, "cameras": S.CAMERA,
                      "render_window_px": list(S.RENDER["window_px"]), "panel_sizes_in": S.SIZE,
                      "palette": S.COL, "tile_colours": S.TILE_COLOURS, "dvh_axes": S.DVH_AXES,
                      "f5_gtcore_field": F5_GTCORE_FIELD, "f5_band": list(F5_BAND), "f2c_configs": [list(c) for c in F2C_CONFIGS]},
    })
    manifest.setdefault("panels", {})
    failures: List[Tuple[str, str]] = []
    n_ok = 0
    for pid, figure, fn, srcs in select(args.only):
        t0 = time.time()
        entry: Dict[str, Any] = {"figure": figure, "description": describe(fn), "sources": source_records(srcs),
                                 "script_commit": git_head(), "rendered": _dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
        if pid in THREE_D and args.no_3d:
            entry.update({"status": "skipped", "note": "--no-3d"})
            manifest["panels"][pid] = entry
            print("%-9s skipped  (--no-3d)" % pid)
            continue
        try:
            res = fn(ctx)
            entry.update({"status": res.get("status", "ok"), "outputs": [rel(p) for p in res["outputs"]],
                          "key_numbers": res.get("key_numbers", {}), "note": res.get("note", "")})
            n_ok += entry["status"] == "ok"
            print("%-9s %-8s %5.1f s  %s" % (pid, entry["status"], time.time() - t0, entry["outputs"][0]))
            if entry["note"]:
                print("%-9s          note: %s" % ("", entry["note"][:160]))
        except Skip as exc:
            entry.update({"status": "skipped", "note": str(exc)})
            print("%-9s skipped  %s" % (pid, exc))
        except Exception as exc:
            entry.update({"status": "error", "note": repr(exc)})
            failures.append((pid, repr(exc)))
            print("%-9s FAILED   %s" % (pid, exc))
            traceback.print_exc()
        manifest["panels"][pid] = entry
    manifest = _json_clean(manifest)
    os.makedirs(os.path.dirname(args.manifest), exist_ok=True)
    with open(args.manifest, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, allow_nan=False, default=str)
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, allow_nan=False, default=str)
    print("\n%d panel(s) rendered to %s; manifest %s" % (n_ok, rel(args.out), rel(args.manifest)))
    if failures:
        print("FAILED: " + ", ".join("%s (%s)" % f for f in failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
