"""Assemble the Medical Physics figure deliverable folder from the repository.

    python scripts/export_figures_medphys.py --out <folder>               # copy what is rendered + committed
    python scripts/export_figures_medphys.py --out <folder> --render      # run scripts/paper_figures.py first
    python scripts/export_figures_medphys.py --out <folder> --overwrite   # refresh an existing export

Reads, from the checkout this script lives in (or ``--repo``):
``docs/figures/manifest.json`` (panel -> figure, outputs, source files with
their commits, status and notes), the rendered panels in ``output/figures/``
(``--figures-dir``), the committed panel data (``docs/figures/data``,
``docs/figures/optimize/data`` -- read only), ``docs/figures/tables/*`` and
``docs/results-summary.md``.  Nothing is computed here except the checks.

Writes under ``--out``::

    fig01 ... fig09, figS2, figS3, figS4   one folder per figure: every panel's PNG + PDF + SVG,
                                           its source CSV / NPZ / JSON in data/ (byte copies, the
                                           header comments kept), a generated README.md; F1, F7,
                                           F9 (no rendered panel) get a README saying what Jacob
                                           supplies (F1 also gets the Table 4 runtime CSV)
    tables/                                docs/figures/tables/* (CSV + Markdown)
    data/                                  committed CSV / JSON in docs/figures/data that no panel
                                           lists (table / caption sources), unless --no-extra-data
    README.md                              panel -> files -> source commits -> script commit ->
                                           command; the style bar; how to regenerate from the tag
    manifest.json                          copy of docs/figures/manifest.json
    results-summary.md                     copy of docs/results-summary.md
    export-ledger.json                     what this script wrote (path, sha256, commit)

``SUMMARY.md`` (written by the coordinator) and any file the script does not
manage are never written, replaced or deleted.  A non-empty ``--out`` is
refused unless ``--overwrite``; even then only managed files are replaced,
and a managed file edited since the last export (its sha256 no longer matches
the ledger) is kept unless ``--replace-modified``.  Nothing is ever deleted.

Checks (reported, exit 0; ``--strict`` exits 3 on a violation): every PNG
carries >= 600 dpi metadata, or, for a 3-D render, is >= 2400 px on the long
side; every rendered panel stem exists as PNG + PDF + SVG.  The final
inventory lists files per folder and every missing / pending panel with the
manifest's reason.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = os.path.dirname(HERE)

FIXED_FOLDERS = ["fig%02d" % i for i in range(1, 10)] + ["figS2", "figS3", "figS4"]
PANEL_FORMATS = ("png", "pdf", "svg")
MIN_DPI = 600
MIN_3D_PX = 2400
THREE_D_PANELS = ("f4b", "f4c", "f8a", "f8b")
PROTECTED = {"summary.md"}            # compared case-insensitively; never written by this script
LEDGER = "export-ledger.json"
TAG = "v1.0-medphys"

FIGURE_TITLES = {
    "F1": "Loop and pipeline (diagrams)", "F2": "Device and model", "F3": "One patient, one phantom",
    "F4": "Recovery (seeds, tiles)", "F5": "Dose vs TPS", "F6": "Target definition",
    "F7": "Interactive replanning (planner strip)", "F8": "Patient as implanted vs optimized",
    "F9": "Modality strip", "S2": "Supplement: localization", "S3": "Supplement: gamma map (optional)",
    "S4": "Supplement: optimizer benchmark (V100 vs N)",
}

# what Jacob supplies per figure (figure-data designation v7 section 3; AGENT_COMMON
# "Authoritative render list"); F1, F7, F9 have no rendered panel at all
HAND_PANELS: Dict[str, List[str]] = {
    "F1": ["(a) loop / workflow diagram and (b) pipeline diagram: Inkscape redraw of the v7 mockup.",
           "(b) carries the stage runtimes as annotations only; their numbers are Table 4: "
           "`table4.csv` / `table4.md` in this folder (rows marked `pending campaign` wait for "
           "`docs/figures/data/table4_runtimes.csv`; copied here as well once it is committed)."],
    "F2": ["(a) tile photograph with a scale.",
           "(b) manufactured-geometry schematic (vector redraw of the mockup)."],
    "F3": ["(b) phantom photograph with the eight tiles."],
    "F7": [],   # filled by f7_instructions() (needs the planner window size)
    "F9": ["CT / 3T MRI / Artec / TRIOS crops of the tile-less phantom (PH0), one strip; or F9 is cut to "
           "a Discussion sentence (no printed STL, decision 2026-11-02: no modality metrics are computed)."],
}


# ============================================================ small helpers
def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(repo: str, *args: str) -> Optional[str]:
    try:
        return subprocess.check_output(["git", *args], cwd=repo, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def repo_state(repo: str) -> Dict[str, Any]:
    head = git(repo, "rev-parse", "--short", "HEAD")
    status = git(repo, "status", "--porcelain", "--untracked-files=no")
    tags = git(repo, "tag", "--points-at", "HEAD")
    # paper_figures.py (--render) rewrites the manifest: that change alone is not "dirty"
    changed = [p for p in (ln.split(None, 1)[-1].strip() for ln in (status or "").splitlines() if ln.strip())
               if p != "docs/figures/manifest.json"]
    return {"head": head or "unknown", "dirty": bool(changed) if status is not None else None,
            "changed": changed, "tags": tags.split() if tags else []}


def compare_manifests(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    """Panels whose render record (script commit, time, status, outputs) differs between two manifests."""
    pa, pb = a.get("panels") or {}, b.get("panels") or {}
    diff = []
    for pid in sorted(set(pa) | set(pb)):
        ea, eb = pa.get(pid) or {}, pb.get(pid) or {}
        keys = [k for k in ("script_commit", "rendered", "status", "outputs") if ea.get(k) != eb.get(k)]
        if keys:
            diff.append("%s (%s)" % (pid, ", ".join(keys)))
    return diff


def folder_for(figure: Optional[str], pid: str) -> Optional[str]:
    """'F4' -> fig04, 'S3 (optional)' -> figS3; falls back to the panel id (f8b_x -> fig08)."""
    for s in (figure or "", pid):
        m = re.match(r"^\s*([FfSs])(\d+)", s)
        if m:
            kind, num = m.group(1).upper(), int(m.group(2))
            return "fig%02d" % num if kind == "F" else "figS%d" % num
    return None


def figure_key(folder: str) -> str:
    """fig04 -> F4, figS2 -> S2."""
    rest = folder[3:]
    return "S%d" % int(rest[1:]) if rest.startswith("S") else "F%d" % int(rest)


def is_optional(figure: Optional[str]) -> bool:
    return "optional" in (figure or "").lower()


def is_three_d(pid: str, entry: Dict[str, Any]) -> bool:
    kn = entry.get("key_numbers") or {}
    return pid.startswith(THREE_D_PANELS) or "window_px" in kn or "camera" in kn


def load_figstyle(repo: str):
    """The style constants module of ``repo`` (falls back to this script's sibling)."""
    for path in (os.path.join(repo, "scripts", "_figstyle.py"), os.path.join(HERE, "_figstyle.py")):
        if os.path.exists(path):
            try:
                os.environ.setdefault("MPLBACKEND", "Agg")
                spec = importlib.util.spec_from_file_location("_figstyle_export", path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return mod
            except Exception:
                continue
    return None


def planner_window(repo: str) -> Optional[Tuple[int, int]]:
    """The planner's fixed window size (``pv.Plotter(window_size=...)`` in gtcore/planner.py)."""
    path = os.path.join(repo, "gtcore", "planner.py")
    try:
        with open(path, encoding="utf-8") as fh:
            m = re.search(r"window_size=\((\d+),\s*(\d+)\)", fh.read())
        return (int(m.group(1)), int(m.group(2))) if m else None
    except OSError:
        return None


def png_info(path: str) -> Tuple[Optional[Tuple[float, float]], Tuple[int, int]]:
    """(dpi or None, (width, height)) of a PNG; PIL when present, else the pHYs / IHDR chunks."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            dpi = im.info.get("dpi")
            return (tuple(float(d) for d in dpi) if dpi else None), im.size
    except ImportError:
        pass
    import struct
    with open(path, "rb") as fh:
        data = fh.read()
    w, h = struct.unpack(">II", data[16:24])
    i = data.find(b"pHYs")
    dpi = None
    if i > 0:
        px, py, unit = struct.unpack(">IIB", data[i + 4:i + 13])
        if unit == 1:
            dpi = (px * 0.0254, py * 0.0254)
    return dpi, (w, h)


# ============================================================ the plan
@dataclass
class Item:
    dest: str                      # path relative to --out (forward slashes)
    src: Optional[str] = None      # absolute source path (copy), or None for generated text
    text: Optional[str] = None     # generated content
    panel: Optional[str] = None
    kind: str = "file"             # panel | source | readme | table | extra | root | pending


@dataclass
class PanelRecord:
    pid: str
    folder: Optional[str]
    figure: str
    status: str
    note: str
    outputs: List[str] = field(default_factory=list)       # dest paths
    sources: List[Dict[str, Any]] = field(default_factory=list)
    missing: List[str] = field(default_factory=list)       # reasons
    three_d: bool = False


@dataclass
class Plan:
    items: List[Item] = field(default_factory=list)
    panels: List[PanelRecord] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)
    pending: List[Tuple[str, str]] = field(default_factory=list)   # (panel, reason)
    folders: List[str] = field(default_factory=list)

    def add(self, item: Item) -> None:
        if os.path.basename(item.dest).lower() in PROTECTED:
            raise ValueError("refusing to manage %s" % item.dest)
        if any(it.dest == item.dest for it in self.items):
            return
        self.items.append(item)


def pending_note(pid: str, status: str, note: str, sources: List[Dict[str, Any]]) -> str:
    src = "\n".join("- `%s`%s" % (s.get("path"), "" if s.get("exists", True) else " (missing)")
                    for s in sources) or "- (none)"
    return ("# %s -- not rendered (%s)\n\n%s\n\nSources listed by the manifest:\n\n%s\n\n"
            "Generated by `scripts/export_figures_medphys.py` from `manifest.json`. The script deletes "
            "nothing: once the panel is rendered, a re-export lists this note as stale.\n"
            % (pid, status, note or "(no note)", src))


def _rel_to_repo(repo: str, path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(repo, *path.replace("\\", "/").split("/"))


def build_plan(repo: str, manifest: Dict[str, Any], figures_dir: str, extra_data: bool = True) -> Plan:
    plan = Plan()
    panels = manifest.get("panels") or {}
    used_sources: set = set()
    folders = list(FIXED_FOLDERS)

    for pid, entry in panels.items():
        figure = entry.get("figure") or ""
        folder = folder_for(figure, pid)
        status = entry.get("status") or "unknown"
        note = entry.get("note") or ""
        rec = PanelRecord(pid=pid, folder=folder, figure=figure, status=status, note=note,
                          three_d=is_three_d(pid, entry))
        plan.panels.append(rec)
        optional_absent = is_optional(figure) and status != "ok"
        if folder and folder not in folders and not optional_absent:
            folders.append(folder)
        if folder is None:
            plan.pending.append((pid, "no figure folder for figure %r" % figure))
            continue
        if optional_absent:
            plan.pending.append((pid, "optional, not rendered (%s): %s" % (status, note or "no note")))
            continue

        outputs = entry.get("outputs") or []
        pending_md = [o for o in outputs if o.endswith(".PENDING.md")]
        real = [o for o in outputs if not o.endswith(".PENDING.md")]
        if status != "ok" or not real:
            plan.pending.append((pid, "%s: %s" % (status, note or "no outputs in the manifest")))
            copied = False
            for o in pending_md:                       # the renderer's own PENDING note
                src = os.path.join(figures_dir, os.path.basename(o))
                if os.path.exists(src):
                    plan.add(Item("%s/%s" % (folder, os.path.basename(o)), src=src, panel=pid, kind="pending"))
                    copied = True
            if not copied:
                plan.add(Item("%s/%s.PENDING.md" % (folder, pid), panel=pid, kind="pending",
                              text=pending_note(pid, status, note, entry.get("sources") or [])))
            for s in entry.get("sources") or []:      # listed, not copied: the panel is not rendered
                rec.sources.append(dict(s, copied=None,
                                        on_disk=os.path.exists(_rel_to_repo(repo, s.get("path") or ""))))
            continue
        else:
            stems: Dict[str, set] = {}
            for o in real:
                base = os.path.basename(o)
                stem, ext = os.path.splitext(base)
                stems.setdefault(stem, set())
                src = os.path.join(figures_dir, base)
                if not os.path.exists(src):
                    rec.missing.append("%s not in the figures directory" % base)
                    continue
                stems[stem].add(ext.lstrip(".").lower())
                dest = "%s/%s" % (folder, base)
                plan.add(Item(dest, src=src, panel=pid, kind="panel"))
                rec.outputs.append(dest)
                if ext.lower() == ".png":
                    _check_png(plan, pid, dest, src, rec.three_d)
            for stem, exts in sorted(stems.items()):
                lack = [f for f in PANEL_FORMATS if f not in exts]
                if lack:
                    plan.violations.append("%s: %s lacks %s" % (pid, stem, "/".join(x.upper() for x in lack)))
            if rec.missing:
                plan.pending.append((pid, "rendered outputs missing: " + "; ".join(rec.missing)))

        # sources of a rendered panel
        for s in entry.get("sources") or []:
            path = s.get("path")
            if not path:
                continue
            src = _rel_to_repo(repo, path)
            rs = dict(s, on_disk=os.path.exists(src))
            if not os.path.exists(src):
                rs["copied"] = None
                rec.sources.append(rs)
                rec.missing.append("source %s not committed" % path)
                continue
            used_sources.add(os.path.normcase(os.path.abspath(src)))
            dest = "%s/data/%s" % (folder, os.path.basename(path))
            plan.add(Item(dest, src=src, panel=pid, kind="source"))
            rs["copied"] = dest
            rec.sources.append(rs)
            root, ext = os.path.splitext(src)
            if ext.lower() == ".npz" and os.path.exists(root + ".json"):     # the NPZ's JSON sidecar
                used_sources.add(os.path.normcase(os.path.abspath(root + ".json")))
                plan.add(Item("%s/data/%s" % (folder, os.path.basename(root + ".json")), src=root + ".json",
                              panel=pid, kind="source"))
        if any(m.startswith("source ") for m in rec.missing):
            plan.pending.append((pid, "; ".join(m for m in rec.missing if m.startswith("source "))))

    plan.folders = sorted(set(folders), key=lambda f: (f.startswith("figS"), f))
    order = {f: i for i, f in enumerate(plan.folders)}
    plan.panels.sort(key=lambda r: order.get(r.folder or "", len(order)))

    # tables
    tdir = os.path.join(repo, "docs", "figures", "tables")
    if os.path.isdir(tdir):
        for name in sorted(os.listdir(tdir)):
            p = os.path.join(tdir, name)
            if os.path.isfile(p):
                plan.add(Item("tables/%s" % name, src=p, kind="table"))
    # F1: Table 4 for the runtime annotations
    for name in ("table4.csv", "table4.md"):
        p = os.path.join(tdir, name)
        if os.path.exists(p):
            plan.add(Item("fig01/%s" % name, src=p, kind="table"))
    t4raw = os.path.join(repo, "docs", "figures", "data", "table4_runtimes.csv")
    if os.path.exists(t4raw):
        used_sources.add(os.path.normcase(os.path.abspath(t4raw)))
        plan.add(Item("fig01/data/table4_runtimes.csv", src=t4raw, kind="source"))

    # extra committed CSV / JSON not tied to one panel
    if extra_data:
        ddir = os.path.join(repo, "docs", "figures", "data")
        if os.path.isdir(ddir):
            for name in sorted(os.listdir(ddir)):
                p = os.path.join(ddir, name)
                if (os.path.isfile(p) and name.lower().endswith((".csv", ".json"))
                        and os.path.normcase(os.path.abspath(p)) not in used_sources):
                    plan.add(Item("data/%s" % name, src=p, kind="extra"))
    return plan


def _check_png(plan: Plan, pid: str, dest: str, src: str, three_d: bool) -> None:
    try:
        dpi, (w, h) = png_info(src)
    except Exception as exc:  # unreadable PNG is a violation, not a crash
        plan.violations.append("%s: %s unreadable (%s)" % (pid, dest, exc))
        return
    dpi_ok = dpi is not None and round(min(dpi)) >= MIN_DPI
    if three_d:
        if max(w, h) < MIN_3D_PX:
            plan.violations.append("%s: %s is %d x %d px (3-D render needs >= %d px on the long side)"
                                   % (pid, dest, w, h, MIN_3D_PX))
    elif not dpi_ok:
        plan.violations.append("%s: %s dpi metadata %s (needs >= %d)" % (
            pid, dest, "missing" if dpi is None else "%.0f x %.0f" % dpi, MIN_DPI))


# ============================================================ generated text
def f7_instructions(repo: str) -> List[str]:
    win = planner_window(repo)
    wtxt = ("%d x %d px" % win) if win else "the planner's default size"
    lines = [
        "Three literal screenshots taken by hand (the figure script does not render F7):",
        "",
        "```",
        "git checkout %s" % TAG,
        'gt plan "$GT_DATA_ROOT/<PH8>" --hrctv        # Windows, repo root: .\\gt plan "%GT_DATA_ROOT%\\<PH8>" --hrctv',
        "```",
        "",
        "- Window: **%s**, the fixed `window_size` of the planner (`gtcore/planner.py`). Do not resize or "
        "maximise the window; same camera and the same crop for all three frames." % wtxt,
        "- Frame 1: the ghost tile previewing a drop and snapping to the wall.",
        "- Frame 2: a tile dragged along the wall; the dose panel flagged STALE.",
        "- Frame 3: after `U` (recompute): isodoses, the overlap caution (amber) and the HR-CTV row "
        "(the `RIND5` row) in the dose panel.",
        "- Save them here as `f7_frame1.png`, `f7_frame2.png`, `f7_frame3.png`; this script never "
        "touches files it did not write.",
    ]
    if win:
        lines.append("- Resolution note: %d px across a 6.9 in double column is about %d dpi; three frames "
                     "side by side are about %d dpi each." % (win[0], round(win[0] / 6.9), round(win[0] / 2.3)))
    return lines


def _src_state(s: Dict[str, Any], fmt: Optional[str] = None) -> str:
    if s.get("copied"):
        return (fmt % os.path.basename(s["copied"])) if fmt else ""
    return "not copied (panel not rendered)" if s.get("on_disk") else "**missing**"


def figure_readme(folder: str, plan: Plan, manifest: Dict[str, Any], repo: str) -> str:
    key = figure_key(folder)
    title = FIGURE_TITLES.get(key, "")
    out = ["# %s%s" % (key, (" -- " + title) if title else ""), "",
           "Generated by `scripts/export_figures_medphys.py`; do not edit (a re-export keeps an edited "
           "copy only when it detects the edit). Panel letters, arrows and annotations belong to the "
           "Inkscape layer.", ""]
    recs = [r for r in plan.panels if r.folder == folder]
    if recs:
        out += ["## Rendered panels", ""]
        for r in recs:
            entry = (manifest.get("panels") or {}).get(r.pid, {})
            out.append("### `%s` (%s)" % (r.pid, r.status))
            out.append("")
            if entry.get("description"):
                out += [entry["description"], ""]
            if r.outputs:
                out.append("- Files: " + ", ".join("`%s`" % os.path.basename(d) for d in r.outputs))
            else:
                out.append("- Not rendered: %s" % (r.note or "no outputs"))
            for s in r.sources:
                out.append("- Source: `%s` -> %s (header commit %s, last git commit %s)" % (
                    s.get("path"), _src_state(s, "`data/%s`"),
                    s.get("header_commit") or "--", s.get("git_commit") or "--"))
            out.append("- Rendered at script commit %s (%s)" % (entry.get("script_commit", "--"), entry.get("rendered", "--")))
            out.append("- Command: `python scripts/paper_figures.py --only %s`" % r.pid)
            if r.note and r.status == "ok":
                out.append("- Note: %s" % r.note)
            out.append("")
    hand = f7_instructions(repo) if key == "F7" else HAND_PANELS.get(key, [])
    if hand:
        out += ["## Supplied by Jacob (not rendered by code)", ""]
        out += hand if key == "F7" else ["- " + h for h in hand]
        out.append("")
    if not recs and not hand:
        out += ["No panel of this figure is in the manifest.", ""]
    return "\n".join(out)


def style_summary(fs) -> List[str]:
    if fs is None:
        return ["(scripts/_figstyle.py not importable; see the repository)"]
    g = lambda name, default=None: getattr(fs, name, default)  # noqa: E731
    font, lw, ms, col = g("FONT", {}), g("LW", {}), g("MS", {}), g("COL", {})
    render, ctw, crop, dvh = g("RENDER", {}), g("CT_WINDOW", {}), g("CLINICAL_CROP", {}), g("DVH_AXES", {})
    alpha, size = g("ALPHA", {}), g("SIZE", {})
    lines = [
        "| Item | Value (from `scripts/_figstyle.py`) |", "|---|---|",
        "| Font | %s (fallback %s); axis %s pt, ticks %s pt, legend %s pt, title %s pt; no bold |" % (
            font.get("family"), ", ".join(font.get("fallback", [])), font.get("axis"), font.get("tick"),
            font.get("legend"), font.get("title")),
        "| Output | PNG %s dpi; PDF (fonttype 42) and SVG (fonttype none): editable text |" % g("DPI"),
        "| Lines | data %s pt, reference %s pt, image overlays %s pt + halo, spines/ticks %s pt (left/bottom only, outward 3 pt), DVH grid %s pt |" % (
            lw.get("data"), lw.get("ref"), lw.get("overlay"), lw.get("spine"), lw.get("grid")),
        "| Markers | %s |" % ", ".join("%s %s" % (k, v) for k, v in ms.items()),
        "| Palette (Okabe-Ito, one meaning per colour) | %s |" % ", ".join("%s `%s`" % (k, v) for k, v in col.items()),
        "| Tile colours (fixed order) | %s |" % ", ".join("`%s`" % c for c in g("TILE_COLOURS", [])),
        "| Alphas | %s |" % ", ".join("%s %s" % (k, v) for k, v in alpha.items()),
        "| CT windows (W / L, HU) | %s |" % ", ".join("%s %s / %s" % (k, v[0], v[1]) for k, v in ctw.items()),
        "| Clinical crop (F4a, F5a, F6 share it) | centre %s mm, half-width %s mm; 10 mm scale bar |" % (
            tuple(crop.get("center_xy", ())), crop.get("half_mm")),
        "| 3-D renders | off-screen PyVista, window %s px, SSAA, parallel projection, one stored camera per cavity (%s) |" % (
            " x ".join(str(v) for v in render.get("window_px", ())), ", ".join(sorted(g("CAMERA", {}).keys()))),
        "| DVH axes | x %s Gy, y %s %%, rx line %s Gy |" % (dvh.get("xlim"), dvh.get("ylim"), dvh.get("rx_gy")),
        "| Panel sizes (in) | %s |" % ", ".join("%s %s x %s" % (k, v[0], v[1]) for k, v in size.items()),
    ]
    return lines


def root_readme(plan: Plan, manifest: Dict[str, Any], state: Dict[str, Any], fs, args_cmd: str,
                rendered_now: bool, manifest_label: str = "docs/figures/manifest.json",
                notes: Optional[List[str]] = None) -> str:
    when = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Figures and tables, Medical Physics (Paper 2)", "",
        "Generated by `scripts/export_figures_medphys.py` at repository commit **%s**%s%s on %s; do not edit "
        "(`SUMMARY.md` is the hand-written summary and is never touched by the script)." % (
            state["head"], " (working tree had uncommitted changes)" if state.get("dirty") else "",
            (" tag " + ", ".join(state["tags"])) if state.get("tags") else "", when),
        "",
        "- Panels rendered by `%s` at script commit **%s** (manifest generated %s, tile mode `%s`)%s." % (
            manifest.get("script", "scripts/paper_figures.py"), manifest.get("script_commit", "--"),
            manifest.get("generated", "--"), manifest.get("tile_mode", "--"),
            "; rendered by this export (`--render`)" if rendered_now else ""),
        "- Export command: `%s`" % args_cmd,
        "- Panels carry data, axes and legends only; panel letters, arrows and annotations are the Inkscape layer.",
        "- `manifest.json` is a copy of `%s`; `results-summary.md` is `docs/results-summary.md` (every paper "
        "number with its source and commit); `export-ledger.json` lists what this script wrote." % manifest_label,
        "",
        "## Panel -> files -> source commits -> script commit -> command", "",
        "| Panel | Figure | Status | Files | Source data (header commit / git commit) | Script commit | Command |",
        "|---|---|---|---|---|---|---|",
    ]
    mp = manifest.get("panels") or {}
    for r in plan.panels:
        e = mp.get(r.pid, {})
        files = "<br>".join("`%s`" % d for d in r.outputs) or ("--" if r.status == "ok" else "not rendered")
        srcs = "<br>".join("`%s` (%s / %s)%s" % (
            s.get("copied") or s.get("path"), s.get("header_commit") or "--", s.get("git_commit") or "--",
            "" if s.get("copied") else " " + _src_state(s)) for s in r.sources) or "--"
        lines.append("| %s | %s | %s | %s | %s | %s | `python scripts/paper_figures.py --only %s` |" % (
            r.pid, r.figure or "--", r.status, files, srcs, e.get("script_commit", "--"), r.pid))
    lines += ["", "## Not rendered by code", ""]
    for key in ("F1", "F7", "F9"):
        lines.append("- **%s** (%s): see `%s/README.md`." % (key, FIGURE_TITLES.get(key, ""), folder_for(key, "")))
    lines.append("- Hand panels inside rendered figures: F2(a) photo, F2(b) schematic, F3(b) phantom photo "
                 "(see each folder's README).")
    if state.get("changed"):
        lines += ["", "Uncommitted changes in the repository at export: " +
                  ", ".join("`%s`" % c for c in state["changed"])]
    if notes:
        lines += ["", "## Notes", ""] + ["- " + n for n in notes]
    if plan.pending:
        lines += ["", "## Missing / pending panels (reason from the manifest)", ""]
        lines += ["- `%s`: %s" % (pid, reason) for pid, reason in plan.pending]
    if plan.violations:
        lines += ["", "## Format violations found at export", ""]
        lines += ["- %s" % v for v in plan.violations]
    lines += ["", "## Style bar", ""] + style_summary(fs)
    lines += [
        "", "## Regenerate from the tag", "",
        "```",
        "git checkout %s" % TAG,
        "python scripts/paper_tables.py                 # docs/figures/tables + docs/results-summary.md",
        "python scripts/paper_tables.py --check         # traceability (exit 1 on a problem)",
        "python scripts/export_figures_medphys.py --out <FiguresMedPhys> --render --overwrite",
        "```", "",
        "`--render` runs `python scripts/paper_figures.py --out output/figures` (it reads only the committed "
        "data in `docs/figures/data` and `docs/figures/optimize/data`, rewrites `docs/figures/manifest.json`) "
        "and then copies. Set `GT_DATA_ROOT` only for the scripts that re-run a pipeline (named in each "
        "source file's header comment); the figure and table scripts need no scan.", "",
    ]
    return "\n".join(lines)


# ============================================================ write
def folder_is_empty(path: str) -> bool:
    return not os.path.exists(path) or not os.listdir(path)


def _read_ledger(out: str) -> Dict[str, str]:
    try:
        with open(os.path.join(out, LEDGER), encoding="utf-8") as fh:
            return {r["path"]: r["sha256"] for r in json.load(fh).get("files", [])}
    except Exception:
        return {}


def write_plan(plan: Plan, out: str, overwrite: bool, replace_modified: bool = False) -> Dict[str, List[str]]:
    """Copy / write every item.  Returns {'written', 'unchanged', 'kept'} dest lists."""
    ledger = _read_ledger(out)
    result: Dict[str, List[str]] = {"written": [], "unchanged": [], "kept": [], "stale": []}
    for f in plan.folders:
        os.makedirs(os.path.join(out, f), exist_ok=True)
    for it in plan.items:
        dest = os.path.join(out, *it.dest.split("/"))
        if os.path.basename(dest).lower() in PROTECTED:       # belt and braces
            result["kept"].append(it.dest + " (protected)")
            continue
        new_bytes = it.text.encode("utf-8") if it.text is not None else None
        if os.path.exists(dest):
            cur = sha256(dest)
            new_hash = hashlib.sha256(new_bytes).hexdigest() if new_bytes is not None else sha256(it.src)
            if cur == new_hash:
                result["unchanged"].append(it.dest)
                continue
            if not overwrite:
                raise FileExistsError(dest)
            if it.dest in ledger and ledger[it.dest] != cur and not replace_modified:
                result["kept"].append(it.dest + " (edited since the last export)")
                continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if new_bytes is not None:
            with open(dest, "wb") as fh:
                fh.write(new_bytes)
        else:
            shutil.copy2(it.src, dest)
        result["written"].append(it.dest)
    current = {it.dest for it in plan.items}
    result["stale"] = sorted(d for d in ledger if d not in current and d != LEDGER
                             and os.path.exists(os.path.join(out, *d.split("/"))))
    return result


def write_ledger(plan: Plan, out: str, state: Dict[str, Any], kept: List[str]) -> None:
    kept_paths = {k.split(" (")[0] for k in kept}
    old = _read_ledger(out)
    files = []
    for it in plan.items:
        p = os.path.join(out, *it.dest.split("/"))
        if it.dest in kept_paths:          # keep the previous record so the edit stays detectable
            if it.dest in old:
                files.append({"path": it.dest, "sha256": old[it.dest], "kind": it.kind, "panel": it.panel})
            continue
        if os.path.exists(p):
            files.append({"path": it.dest, "sha256": sha256(p), "kind": it.kind, "panel": it.panel})
    with open(os.path.join(out, LEDGER), "w", encoding="utf-8") as fh:
        json.dump({"script": "scripts/export_figures_medphys.py", "repo_commit": state["head"],
                   "dirty": state.get("dirty"), "written": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                   "files": files}, fh, indent=1)


# ============================================================ inventory
def inventory(plan: Plan, out: str, result: Dict[str, List[str]]) -> str:
    lines = ["", "Inventory of %s" % out, ""]
    names = sorted({it.dest.split("/")[0] for it in plan.items if "/" in it.dest} | set(plan.folders),
                   key=lambda f: (not f.startswith("fig"), f.startswith("figS"), f))
    lines.append("%-9s %5s %5s %5s %5s %6s %5s  %s" % ("folder", "png", "pdf", "svg", "data", "readme", "other", "on disk"))
    for f in names:
        mine = [it.dest for it in plan.items if it.dest.startswith(f + "/")]
        c = {"png": 0, "pdf": 0, "svg": 0, "data": 0, "readme": 0, "other": 0}
        for d in mine:
            base = os.path.basename(d).lower()
            if base == "readme.md":
                c["readme"] += 1
            elif "/data/" in d or base.endswith((".csv", ".npz", ".json")):
                c["data"] += 1
            elif base.rsplit(".", 1)[-1] in ("png", "pdf", "svg"):
                c[base.rsplit(".", 1)[-1]] += 1
            else:
                c["other"] += 1
        disk = 0
        for _r, _d, fs in os.walk(os.path.join(out, f)):
            disk += len(fs)
        lines.append("%-9s %5d %5d %5d %5d %6d %5d  %d" % (f, c["png"], c["pdf"], c["svg"], c["data"], c["readme"],
                                                           c["other"], disk))
    root_files = [it.dest for it in plan.items if "/" not in it.dest] + [LEDGER]
    lines.append("root      %s%s" % (", ".join(root_files),
                                     ", SUMMARY.md (coordinator's, untouched)" if os.path.exists(os.path.join(out, "SUMMARY.md")) else ""))
    lines.append("")
    lines.append("written %d, unchanged %d, kept %d" % (len(result["written"]), len(result["unchanged"]), len(result["kept"])))
    for k in result["kept"]:
        lines.append("  kept: %s" % k)
    for k in result.get("stale", []):
        lines.append("  stale (written by an earlier export, no longer produced; delete by hand): %s" % k)
    lines.append("")
    if plan.pending:
        lines.append("Missing / pending panels:")
        lines += ["  %-9s %s" % (pid, reason[:300]) for pid, reason in plan.pending]
    else:
        lines.append("Missing / pending panels: none")
    lines.append("")
    if plan.violations:
        lines.append("Format violations (%d):" % len(plan.violations))
        lines += ["  " + v for v in plan.violations]
    else:
        lines.append("Format check: every PNG >= %d dpi (3-D renders >= %d px), every panel PNG + PDF + SVG -- OK"
                     % (MIN_DPI, MIN_3D_PX))
    return "\n".join(lines)


# ============================================================ main
def render(repo: str, figures_dir: str, manifest_path: str) -> int:
    env = dict(os.environ, PYTHONPATH=repo, MPLBACKEND="Agg")
    cmd = [sys.executable, os.path.join(repo, "scripts", "paper_figures.py"), "--out", figures_dir,
           "--manifest", manifest_path]
    print("rendering: python scripts/paper_figures.py --out %s" % os.path.relpath(figures_dir, repo)
          if figures_dir.startswith(repo) else "rendering: python scripts/paper_figures.py --out <figures dir>",
          flush=True)
    return subprocess.call(cmd, cwd=repo, env=env)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, help="the deliverable folder (created if absent)")
    ap.add_argument("--render", action="store_true", help="run scripts/paper_figures.py first")
    ap.add_argument("--overwrite", action="store_true",
                    help="allow a non-empty --out; replace only files this script manages, delete nothing")
    ap.add_argument("--replace-modified", action="store_true",
                    help="with --overwrite: also replace managed files edited since the last export")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="repository root (default: this checkout)")
    ap.add_argument("--manifest", default=None, help="default <repo>/docs/figures/manifest.json")
    ap.add_argument("--figures-dir", default=None, help="rendered panels (default <repo>/output/figures)")
    ap.add_argument("--no-extra-data", action="store_true", help="skip data/ (committed CSV/JSON no panel lists)")
    ap.add_argument("--strict", action="store_true", help="exit 3 when a format violation is found")
    args = ap.parse_args(argv)

    repo = os.path.abspath(args.repo)
    out = os.path.abspath(args.out)
    manifest_path = os.path.abspath(args.manifest or os.path.join(repo, "docs", "figures", "manifest.json"))
    figures_dir = os.path.abspath(args.figures_dir or os.path.join(repo, "output", "figures"))

    if not folder_is_empty(out) and not args.overwrite:
        print("refusing to write into the non-empty folder %s (use --overwrite: it replaces only the files "
              "this script manages and deletes nothing)" % out, file=sys.stderr)
        return 2

    if args.render:
        rc = render(repo, figures_dir, manifest_path)
        if rc != 0:
            print("paper_figures.py exited %d; exporting what the manifest records" % rc, file=sys.stderr)
    if not os.path.exists(manifest_path):
        print("no manifest at %s (run with --render)" % manifest_path, file=sys.stderr)
        return 1
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    # paper_figures.py writes the same manifest next to the panels; if the two differ (a partial
    # re-render that was not committed), the copy beside the panels describes the files exported
    notes: List[str] = []
    side = os.path.join(figures_dir, "manifest.json")
    if not args.manifest and os.path.exists(side) and os.path.abspath(side) != manifest_path:
        with open(side, encoding="utf-8") as fh:
            side_manifest = json.load(fh)
        diff = compare_manifests(manifest, side_manifest)
        if diff:
            notes.append("docs/figures/manifest.json (script commit %s) differs from the manifest beside the "
                         "rendered panels (script commit %s) for %s; the latter describes the exported files "
                         "and is used (commit the re-rendered manifest, or re-run with --render)" % (
                             manifest.get("script_commit", "--"), side_manifest.get("script_commit", "--"),
                             "; ".join(diff)))
            manifest_path, manifest = os.path.abspath(side), side_manifest

    state = repo_state(repo)
    plan = build_plan(repo, manifest, figures_dir, extra_data=not args.no_extra_data)
    fs = load_figstyle(repo)
    cmd = "python scripts/export_figures_medphys.py --out <FiguresMedPhys>" + "".join(
        f for f, on in ((" --render", args.render), (" --overwrite", args.overwrite),
                        (" --no-extra-data", args.no_extra_data)) if on)
    for folder in plan.folders:
        plan.add(Item("%s/README.md" % folder, text=figure_readme(folder, plan, manifest, repo), kind="readme"))
    plan.add(Item("manifest.json", src=manifest_path, kind="root"))
    summ = os.path.join(repo, "docs", "results-summary.md")
    if os.path.exists(summ):
        plan.add(Item("results-summary.md", src=summ, kind="root"))
    else:
        plan.pending.append(("results-summary.md", "docs/results-summary.md not in the repository"))
    label = os.path.relpath(manifest_path, repo).replace(os.sep, "/") if manifest_path.startswith(repo) else "<figures dir>/manifest.json"
    plan.add(Item("README.md", text=root_readme(plan, manifest, state, fs, cmd, args.render, label, notes), kind="root"))

    os.makedirs(out, exist_ok=True)
    try:
        result = write_plan(plan, out, overwrite=args.overwrite, replace_modified=args.replace_modified)
    except FileExistsError as exc:     # only reachable if the folder filled up meanwhile
        print("refusing to replace %s without --overwrite" % exc, file=sys.stderr)
        return 2
    write_ledger(plan, out, state, result["kept"])
    print("repository commit %s%s; panels from script commit %s" % (
        state["head"], " (dirty)" if state.get("dirty") else "", manifest.get("script_commit", "--")))
    for n in notes:
        print("NOTE: " + n)
    print(inventory(plan, out, result))
    return 3 if (args.strict and plan.violations) else 0


if __name__ == "__main__":
    sys.exit(main())
