"""Style bar for the Paper 2 figure panels (``scripts/paper_figures.py``).

One place for everything that must be identical across panels: the
matplotlib rcParams (typography, spines, ticks, editable text in PDF/SVG),
the Okabe-Ito palette with the paper's fixed meaning per colour, line
widths and marker sizes, the panel sizes taken from the v7 mockups, the CT
window/level constants, the shared crop of the clinical slice (F4a, F5a,
F6 share one crop), the stored cameras of the 3-D renders (one per cavity:
F4b/F8a/F8b share the patient camera, F4c the phantom camera), and the
small helpers (scale bar, halo, three-format save).

Nothing here reads data.  Changing a constant here changes every panel
that uses it, which is the point.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

__all__ = [
    "OI", "COL", "TILE_COLOURS", "LW", "MS", "FONT", "SIZE", "DPI",
    "CT_WINDOW", "CLINICAL_CROP", "PHANTOM_CROP", "CAMERA", "RENDER",
    "DVH_AXES", "apply_style", "halo", "add_scale_bar", "save_panel",
    "image_axes", "chart_axes", "legend", "legend_below_image", "set_window",
]

# ---------------------------------------------------------------- palette
# Okabe-Ito (colour-blind safe).  The paper gives every colour ONE meaning.
OI: Dict[str, str] = {
    "black": "#000000", "orange": "#E69F00", "sky": "#56B4E9",
    "green": "#009E73", "yellow": "#F0E442", "blue": "#0072B2",
    "vermilion": "#D55E00", "purple": "#CC79A7", "grey": "#BBBBBB",
    "darkgrey": "#3C3C3C", "midgrey": "#999999",
}
COL: Dict[str, str] = {
    "tps": OI["black"],               # TPS / RTDOSE / TPS contours
    "gtcore": OI["vermilion"],        # gtcore (32 seeds): curves, isodoses, seed marks
    "gtcore30": OI["blue"],           # gtcore at the 30 contoured seeds (dashed)
    "ctv_gt": OI["green"],            # physician's CTV_GT
    "cavity": OI["grey"],             # cavity fill (60 % alpha)
    "rind": OI["black"],              # full RIND5 outline (dashed)
    "reach": OI["orange"],            # IMPLANT_REACH (F6 caption only now)
    "band": OI["vermilion"],          # MC band (20 % alpha)
    "leftover": OI["vermilion"],      # leftover seeds in the 3-D renders
    "seed": OI["darkgrey"],           # seed capsules in the 3-D renders
    "mesh": "#C8C8C8",                # cavity mesh in the 3-D renders
    "mesh_eligible": "#A8A8A8",       # craniotomy-side eligible wall (F8b)
    "adaptive": OI["blue"],           # S2a: shipped (adaptive) detection
    "fixed": OI["midgrey"],           # S2a: fixed-threshold detection
    "sheet": OI["orange"],            # F2c: the collagen sheet
}
# tile sheets: one colour per tile, FIXED order, never cycled; the same list
# for the implanted and the optimized configuration.  Vermilion is the
# attention colour (leftover seeds; the crumpled phantom tile): it is not in
# the list and goes only to a degraded tile; grey is reserved for the cavity.
TILE_COLOURS: List[str] = [OI["orange"], OI["sky"], OI["green"], OI["yellow"], OI["blue"],
                           OI["purple"], "#882255"]   # 7th: Tol "wine"; no grey (grey is the cavity); vermilion = degraded tile
ALPHA = {"cavity_fill": 0.60, "ctv_fill": 0.45, "band": 0.20, "tile": 0.75,
         "mesh": 0.30, "legend_box": 0.85}

# ---------------------------------------------------------------- lines, markers, fonts
LW = {"data": 1.0, "ref": 0.8, "overlay": 0.9, "spine": 0.6, "tick": 0.6,
      "grid": 0.3, "halo_extra": 0.9, "rx": 0.6}
MS = {"chart": 3.5, "tps_circle": 8.0, "gtcore_x": 4.5, "uncontoured": 6.0,
      "phantom_circle": 7.0}
FONT = {"family": "Arial", "fallback": ["Liberation Sans", "DejaVu Sans"],
        "axis": 8, "tick": 8, "legend": 7, "title": 9, "scalebar": 7}
DPI = 600

# ---------------------------------------------------------------- panel sizes (inches) from the v7 mockups
SIZE: Dict[str, Tuple[float, float]] = {
    "f2c": (4.2, 1.7),        # three sheets in a row (half of a double column)
    "f3c": (2.6, 2.6),        # square phantom slice
    "f4a": (2.2, 2.2),        # square clinical slice, one of three across 6.9 in
    "f5a": (2.8, 2.8),        # square clinical slice, left half of F5
    "f5b": (3.7, 2.8),        # DVH, right half of F5
    "f6": (3.35, 3.35),       # single column square
    "s2a": (3.35, 3.2),       # two stacked small multiples
    "s2b": (3.35, 2.4),
    "s4": (3.35, 2.5),
    "s3_gamma": (2.8, 2.8),
    "s5_dvh3": (3.7, 2.8),
}
# 3-D renders: square window, >= 2400 px on the long side (off-screen, SSAA)
RENDER = {"window_px": (2400, 2400), "panel_in": 3.3, "mesh_culling": "front",
          "mesh_opacity": ALPHA["mesh"], "tile_opacity": ALPHA["tile"], "tile_opacity_highlight": 0.92, "tile_edge_lw": 3.0,
          "silhouette_lw": 2.0, "silhouette_colour": "#7A7A7A",
          "mesh_smooth_iter": 40, "mesh_smooth_pass_band": 0.05, "phantom_mesh_opacity": 0.25}

# ---------------------------------------------------------------- CT display
# window / level in HU; vmin = L - W/2, vmax = L + W/2
CT_WINDOW = {"clinical": (400.0, 40.0), "phantom": (1000.0, 0.0)}
# the one crop shared by F4a, F5a, F6 (square, RAS mm, centred on the cavity:
# Cav_Post bbox on slice k = 117 is x 7..55, y 188..229; the 30 Gy isodose and
# the full rind reach y ~ 243 / x ~ -2; half-width 42 mm leaves the corners
# free for the legend and the scale bar).
CLINICAL_CROP = {"center_xy": (31.0, 209.5), "half_mm": 42.0}
# phantom slice crop (F3c): centred on the PH8 seed centroid (x, y); set by
# the export helper from the seeds and stored in the NPZ, this is the fallback
PHANTOM_CROP = {"half_mm": 36.0}
SCALE_BAR_MM = 10.0

# ---------------------------------------------------------------- cameras (RAS mm)
# One stored camera per cavity: every render of the same cavity uses the same
# position / focal point / view-up / parallel scale (parallel projection).
# Stored cameras, derived once with paper_figures.fit_camera (screen-space
# bounding box of the tile sheets + seeds, centred; parallel scale = half-size
# x margin), so the renders are cropped to the IMPLANT, not to the mesh.
# patient (F4b, F8a, F8b): the camera looks into the cavity from the left
# (direction (-0.995, 0, 0.10) from the focal point, margin 1.12) at the
# implant on the right wall; the cavity mesh is display-smoothed, its front
# faces culled, so the far wall is behind the tiles and nothing is in front.
# phantom (F4c): posterior-superior view of the tiles on the printed wall
# (registered PH0 wall, cropped to the implant region; no culling).
CAMERA: Dict[str, Dict[str, Sequence[float]]] = {
    "patient": {"focal_point": (26.5, 210.7, -513.2), "position": (-122.8, 210.7, -498.3),
                "view_up": (0.0, 0.0, 1.0), "parallel_scale": 27.7, "mesh_culling": "front"},
    "phantom": {"focal_point": (-16.6, -192.7, -619.7), "position": (-46.8, -296.6, -515.8),
                "view_up": (0.0, 0.0, 1.0), "parallel_scale": 28.3, "mesh_culling": None},
}

# ---------------------------------------------------------------- DVH axes
DVH_AXES = {"xlim": (0.0, 150.0), "ylim": (0.0, 100.0), "rx_gy": 60.0,
            "xticks": [0, 30, 60, 90, 120, 150], "yticks": [0, 20, 40, 60, 80, 100]}


# ---------------------------------------------------------------- rcParams
def apply_style() -> None:
    """Medical Physics style bar: Arial 8 pt, left/bottom spines 0.6 pt,
    outward 3 pt ticks, no grid (the DVH adds its own faint one), editable
    text in PDF (type 42) and SVG (fonttype none)."""
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": [FONT["family"]] + FONT["fallback"],
        "font.size": FONT["axis"], "axes.labelsize": FONT["axis"],
        "axes.titlesize": FONT["title"], "axes.titleweight": "normal",
        "xtick.labelsize": FONT["tick"], "ytick.labelsize": FONT["tick"],
        "legend.fontsize": FONT["legend"], "legend.frameon": False,
        "legend.handlelength": 1.8, "legend.handletextpad": 0.6,
        "legend.borderaxespad": 0.3, "legend.labelspacing": 0.35,
        "lines.linewidth": LW["data"], "lines.markersize": MS["chart"],
        "axes.linewidth": LW["spine"],
        "axes.spines.top": False, "axes.spines.right": False,
        "xtick.direction": "out", "ytick.direction": "out",
        "xtick.major.width": LW["tick"], "ytick.major.width": LW["tick"],
        "xtick.major.size": 3.0, "ytick.major.size": 3.0,
        "xtick.minor.visible": False, "ytick.minor.visible": False,
        "axes.grid": False, "axes.axisbelow": True,
        "axes.unicode_minus": True,
        "mathtext.fontset": "custom", "mathtext.rm": FONT["family"],
        "mathtext.it": FONT["family"] + ":italic", "mathtext.bf": FONT["family"],
        "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
        "savefig.dpi": DPI, "figure.dpi": 100,
        "figure.facecolor": "white", "savefig.facecolor": "white",
        "axes.facecolor": "white",
    })


# ---------------------------------------------------------------- helpers
def halo(lw: float, colour: str = "black", alpha: float = 0.55) -> list:
    """Path effects: a thin dark halo under an overlay line on a grey image."""
    return [pe.Stroke(linewidth=lw + LW["halo_extra"], foreground=colour, alpha=alpha),
            pe.Normal()]


def set_window(kind: str) -> Tuple[float, float]:
    """(vmin, vmax) in HU for a named CT window."""
    w, l = CT_WINDOW[kind]
    return l - w / 2.0, l + w / 2.0


LEGEND_ROW_IN = 0.165      # height of one legend row below a slice image (7 pt text)
LEGEND_PAD_IN = 0.07


def image_axes(size: Tuple[float, float], legend_rows: int = 0):
    """A figure whose image axes fills its width (CT slice panels); with
    ``legend_rows`` > 0 a white band below the image holds the legend, so no
    legend ever overlays the image."""
    w, h = size
    band = (LEGEND_ROW_IN * legend_rows + LEGEND_PAD_IN) if legend_rows else 0.0
    fig = plt.figure(figsize=(w, h + band))
    ax = fig.add_axes([0.0, band / (h + band), 1.0, h / (h + band)])
    ax.set_axis_off()
    ax.set_aspect("equal")
    fig._gt_legend_band = band
    return fig, ax


def legend_below_image(fig, handles, ncol: int = 2):
    """One shared layout for the slice panels: legend centred in the band
    below the image, frameless, at most ``ncol`` entries per row."""
    labels = [h.get_label() for h in handles]
    return fig.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.0), ncol=min(ncol, len(handles)),
                      frameon=False, fontsize=FONT["legend"], handlelength=1.6, handletextpad=0.5,
                      columnspacing=1.2, borderaxespad=0.15, borderpad=0.15, labelspacing=0.25)


def chart_axes(size: Tuple[float, float], nrows: int = 1, sharex: bool = False):
    """Chart panel(s) with fixed outer margins (no bbox_inches='tight', so
    the panel keeps its stated size).  Returns (fig, axes list)."""
    fig, axes = plt.subplots(nrows, 1, figsize=size, sharex=sharex, squeeze=False)
    fig.subplots_adjust(left=0.16, right=0.95, bottom=0.17 if nrows == 1 else 0.11,
                        top=0.97, hspace=0.18)
    return fig, [a for a in axes[:, 0]]


def add_scale_bar(ax, extent_xy: Tuple[float, float, float, float], length_mm: float = SCALE_BAR_MM,
                  invert_x: bool = False, pad_frac: float = 0.06, label: bool = True):
    """10 mm bar, bottom-right, white with a thin dark outline; ``extent_xy``
    = (x_left, x_right, y_bottom, y_top) of the visible crop in data units.
    ``invert_x`` for a radiological display (x decreasing to the right)."""
    x_l, x_r, y_b, y_t = extent_xy
    w = abs(x_r - x_l)
    h = abs(y_t - y_b)
    pad_x, pad_y = pad_frac * w, pad_frac * h
    # bottom-right corner in data coordinates
    x_end = x_r + pad_x if invert_x else x_r - pad_x
    x_start = x_end + length_mm if invert_x else x_end - length_mm
    y0 = min(y_b, y_t) + pad_y
    bar = Line2D([x_start, x_end], [y0, y0], color="white", linewidth=2.2,
                 solid_capstyle="butt", zorder=20,
                 path_effects=[pe.Stroke(linewidth=3.4, foreground="black", alpha=0.8), pe.Normal()])
    ax.add_line(bar)
    if label:
        ax.text(0.5 * (x_start + x_end), y0 + 0.018 * h, "%g mm" % length_mm,
                color="white", fontsize=FONT["scalebar"], ha="center", va="bottom", zorder=21,
                path_effects=[pe.Stroke(linewidth=1.6, foreground="black", alpha=0.8), pe.Normal()])
    return bar


def legend(ax, handles=None, labels=None, loc: str = "lower left", on_image: bool = False, **kw):
    """Legend without a frame (charts) or with a translucent white box when
    it sits on a CT slice (so it stays legible on grey)."""
    args = dict(loc=loc, fontsize=FONT["legend"], frameon=on_image, borderpad=0.5)
    if on_image:
        args.update(framealpha=0.92, facecolor="white", edgecolor="none", handlelength=1.4,
                    borderaxespad=0.15, borderpad=0.4,
                    fancybox=False)
    args.update(kw)
    if handles is not None:
        return ax.legend(handles, labels, **args)
    return ax.legend(**args)


def save_panel(fig, out_dir: str, stem: str, dpi: int = DPI, tight: bool = False) -> List[str]:
    """PNG (dpi) + PDF + SVG with editable text.  Returns the three paths."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    kw = {"bbox_inches": "tight", "pad_inches": 0.02} if tight else {}
    for ext in ("png", "pdf", "svg"):
        p = os.path.join(out_dir, "%s.%s" % (stem, ext))
        fig.savefig(p, dpi=dpi if ext == "png" else None, **kw)
        paths.append(p)
    plt.close(fig)
    return paths


def save_raster_panel(img, out_dir: str, stem: str, panel_in: float, dpi: int = DPI) -> List[str]:
    """A rendered RGB(A) array (3-D render) as PNG at the render resolution,
    plus a PDF and an SVG that embed the same raster at ``panel_in`` inches
    (no text in these panels, so nothing is lost)."""
    import numpy as np
    from PIL import Image

    os.makedirs(out_dir, exist_ok=True)
    img = np.asarray(img)
    h, w = img.shape[:2]
    png = os.path.join(out_dir, stem + ".png")
    Image.fromarray(img).save(png, dpi=(dpi, dpi))
    fig = plt.figure(figsize=(panel_in, panel_in * h / w))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.imshow(img, interpolation="none")
    paths = [png]
    for ext in ("pdf", "svg"):
        p = os.path.join(out_dir, "%s.%s" % (stem, ext))
        fig.savefig(p, dpi=dpi)
        paths.append(p)
    plt.close(fig)
    return paths


__all__.append("save_raster_panel")
__all__.append("ALPHA")
__all__.append("SCALE_BAR_MM")
