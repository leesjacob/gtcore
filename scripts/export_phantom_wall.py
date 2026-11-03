"""Export the PH8 phantom wall that F4(c) shows as context, so that
``scripts/paper_figures.py`` never reads DICOM or registers volumes.

    PYTHONPATH=. python scripts/export_phantom_wall.py "$GT_DATA_ROOT/<PH8>" "$GT_DATA_ROOT/<PH0>" \
        [--seeds docs/figures/data/f4c_phantom_implant.npz] [--out docs/figures/data/f4c_phantom_wall.npz]

The wall is ``gtcore.segment.cavity.phantom_wall_and_cavity`` (Phase 4): the
tile-less twin PH0 rigidly registered into PH8, its printed inner surface
bordering the phantom cavity (normals into the plastic), in PH8 RAS mm.  The
seeds are the committed pipeline detections of ``f4c_phantom_implant.npz``
(nothing is re-detected).  Written: the full wall (``wall_vertices`` /
``wall_faces``), the same wall cropped to the implant region (faces whose
centroid lies within ``--crop-mm`` of the seed bounding box,
``wall_crop_vertices`` / ``wall_crop_faces``), the registration residual, the
cavity volume and a ``header`` (script, sanitised command, commit, datasets).
Commit the NPZ with ``git add -f``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gtcore  # noqa: E402
from gtcore.io import load_dicom_series  # noqa: E402
from gtcore.segment.cavity import phantom_wall_and_cavity  # noqa: E402


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def _crop(vertices: np.ndarray, faces: np.ndarray, lo: np.ndarray, hi: np.ndarray):
    cent = vertices[faces].mean(axis=1)
    keep = np.all((cent >= lo) & (cent <= hi), axis=1)
    f = faces[keep]
    used = np.unique(f)
    remap = -np.ones(len(vertices), np.int64)
    remap[used] = np.arange(len(used))
    return vertices[used], remap[f].astype(np.int32)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ph8_dir")
    ap.add_argument("ph0_dir")
    ap.add_argument("--seeds", default=os.path.join(ROOT, "docs", "figures", "data", "f4c_phantom_implant.npz"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "figures", "data", "f4c_phantom_wall.npz"))
    ap.add_argument("--crop-mm", type=float, default=12.0)
    a = ap.parse_args(argv)

    print("gtcore:", gtcore.__file__)
    z = np.load(a.seeds, allow_pickle=True)
    seeds = np.asarray(z["seed_centers_ras"], float)
    seeds_commit = "unknown"
    idx = a.seeds[:-4] + ".json"
    if os.path.exists(idx):
        with open(idx, encoding="utf-8") as fh:
            seeds_commit = str(json.load(fh).get("commit", "unknown"))
    vol8 = load_dicom_series(a.ph8_dir)
    vol0 = load_dicom_series(a.ph0_dir)
    wall, cav_mask, cav_aff, info = phantom_wall_and_cavity(vol8, vol0, seed_centers_ras=seeds)
    V = np.asarray(wall.vertices, float)
    F = np.asarray(wall.faces, np.int64)
    lo, hi = seeds.min(axis=0) - a.crop_mm, seeds.max(axis=0) + a.crop_mm
    Vc, Fc = _crop(V, F, lo, hi)
    vox = float(abs(np.linalg.det(np.asarray(cav_aff)[:3, :3])))
    cav_ml = float(np.count_nonzero(cav_mask)) * vox / 1000.0
    print("wall: %d vertices / %d faces; cropped %d / %d; cavity %.1f mL" % (len(V), len(F), len(Vc), len(Fc), cav_ml))
    resid = info.get("residual_mm")
    cmd = 'python scripts/export_phantom_wall.py "$GT_DATA_ROOT/<PH8>" "$GT_DATA_ROOT/<PH0>" --crop-mm %g' % a.crop_mm
    header = "\n".join([
        "# script: scripts/export_phantom_wall.py",
        "# command: " + cmd,
        "# commit: " + git_commit(),
        "# datasets: PH8 (printed phantom with tiles), PH0 (tile-less twin, rigidly registered into PH8); seeds from %s (commit %s)"
        % (os.path.relpath(a.seeds, ROOT).replace(os.sep, "/"), seeds_commit),
        "# wall: gtcore.segment.cavity.phantom_wall_and_cavity (PH8 RAS mm, normals into the plastic); crop = faces within %g mm of the seed bounding box" % a.crop_mm,
        "# generated: " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "# panel: F4(c) context surface",
    ])
    out = {"wall_vertices": V, "wall_faces": F.astype(np.int32), "wall_crop_vertices": Vc, "wall_crop_faces": Fc,
           "crop_lo_ras": lo, "crop_hi_ras": hi, "cavity_volume_ml": np.array(cav_ml),
           "registration_residual": np.array(json.dumps(resid, default=str)),
           "header": np.array(header)}
    np.savez_compressed(a.out, **out)
    print("wrote", a.out, "(%.0f kB)" % (os.path.getsize(a.out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
