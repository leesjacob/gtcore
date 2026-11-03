"""Export the one axial PH8 slice that F3(c) shows, so that
``scripts/paper_figures.py`` never reads DICOM.

    PYTHONPATH=. python scripts/export_phantom_slice.py "$GT_DATA_ROOT/<PH8>" \
        [--seeds docs/figures/data/f4c_phantom_implant.npz] [--out docs/figures/data/f3c_phantom_slice.npz]

What it writes (``f3c_phantom_slice.npz``, RAS mm, arrays indexed [j, i]):

* ``ct_hu``: the full axial slice (float32 HU), ``x_ras`` / ``y_ras``: the
  voxel-centre coordinates of every pixel, ``affine``, ``spacing_mm``,
  ``slice_k``, ``slice_z_ras``;
* ``seed_centers_ras`` / ``seed_axes_ras``: ALL detected seeds of the
  committed render data (``f4c_phantom_implant.npz`` -- the pipeline's 32
  detections; nothing is re-detected here), ``seed_dz_mm`` their distance to
  the slice plane, ``seed_near_slice`` (|dz| <= ``--near-mm``);
* ``crop_center_xy`` / ``crop_half_mm``: the square crop (centred on the x, y
  centroid of all 32 seeds -- the phantom's implant region; the phantom
  cavity is not in the render NPZ);
* ``window_level_note``, ``header`` (script, sanitised command, commit, dataset).

The slice is the one with the most seeds within ``--near-mm`` of its plane
(ties: closest to the seed centroid).  Commit the NPZ with ``git add -f``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gtcore  # noqa: E402
from gtcore.io import load_dicom_series  # noqa: E402


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("ph8_dir", help="PH8 DICOM directory ($GT_DATA_ROOT/<PH8>)")
    ap.add_argument("--seeds", default=os.path.join(ROOT, "docs", "figures", "data", "f4c_phantom_implant.npz"))
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "figures", "data", "f3c_phantom_slice.npz"))
    ap.add_argument("--near-mm", type=float, default=1.5, help="seed is 'on the slice' if |dz| <= this")
    ap.add_argument("--half-mm", type=float, default=36.0, help="half-width of the square crop")
    a = ap.parse_args(argv)

    print("gtcore:", gtcore.__file__)
    vol = load_dicom_series(a.ph8_dir)
    print("PH8 volume: shape_ijk %s, spacing %s" % (vol.shape_ijk, np.round(vol.spacing, 3)))
    z = np.load(a.seeds, allow_pickle=True)
    centers = np.asarray(z["seed_centers_ras"], float)
    axes = np.asarray(z["seed_axes_ras"], float)
    centroid = centers.mean(axis=0)
    seeds_commit = "unknown"
    idx = a.seeds[:-4] + ".json"
    if os.path.exists(idx):
        import json
        with open(idx, encoding="utf-8") as fh:
            seeds_commit = str(json.load(fh).get("commit", "unknown"))

    # candidate slices: every k whose plane is within near-mm of a seed
    nk = vol.shape_ijk[2]
    ks = np.arange(nk)
    z_of_k = np.array([vol.index_to_ras([0, 0, int(k)])[2] for k in ks])
    counts = np.array([(np.abs(centers[:, 2] - zk) <= a.near_mm).sum() for zk in z_of_k])
    best = np.flatnonzero(counts == counts.max())
    k0 = int(best[np.argmin(np.abs(z_of_k[best] - centroid[2]))])
    z0 = float(z_of_k[k0])
    dz = centers[:, 2] - z0
    near = np.abs(dz) <= a.near_mm
    print("slice k = %d (z = %.2f mm): %d of %d seeds within %.1f mm" % (k0, z0, near.sum(), len(centers), a.near_mm))

    arr = vol.array  # [k, j, i]
    sl = np.asarray(arr[k0], dtype=np.float32)
    nj, ni = sl.shape
    jj, ii = np.meshgrid(np.arange(nj), np.arange(ni), indexing="ij")
    pts = np.stack([ii.ravel(), jj.ravel(), np.full(ii.size, k0)], axis=1).astype(float)
    ras = vol.index_to_ras(pts)
    x_ras = ras[:, 0].reshape(nj, ni).astype(np.float32)
    y_ras = ras[:, 1].reshape(nj, ni).astype(np.float32)

    cmd = "python scripts/export_phantom_slice.py \"$GT_DATA_ROOT/<PH8>\" --near-mm %g --half-mm %g" % (a.near_mm, a.half_mm)
    header = "\n".join([
        "# script: scripts/export_phantom_slice.py",
        "# command: " + cmd,
        "# commit: " + git_commit(),
        "# dataset: PH8 (printed phantom, 8 tiles / 32 seeds placed by hand); seeds from %s (commit %s)"
        % (os.path.relpath(a.seeds, ROOT).replace(os.sep, "/"), seeds_commit),
        "# generated: " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "# panel: F3(c) axial PH8 slice through the seeds, seeds circled, 10 mm bar (W 1000 / L 0)",
    ])
    out = {
        "ct_hu": sl, "x_ras": x_ras, "y_ras": y_ras, "affine": np.asarray(vol.affine, float),
        "spacing_mm": np.asarray(vol.spacing, float), "shape_kji": np.asarray(arr.shape, np.int64),
        "slice_k": np.array(k0), "slice_z_ras": np.array(z0),
        "seed_centers_ras": centers, "seed_axes_ras": axes, "seed_dz_mm": dz, "seed_near_slice": near,
        "near_mm": np.array(a.near_mm),
        "crop_center_xy": np.array([centroid[0], centroid[1]]), "crop_half_mm": np.array(a.half_mm),
        "window_level_note": np.array("display W 1000 / L 0 HU; square crop centred on the seed centroid"),
        "seed_source": np.array("docs/figures/data/f4c_phantom_implant.npz (pipeline detections; not re-run)"),
        "header": np.array(header),
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    np.savez_compressed(a.out, **out)
    print("wrote", a.out, "(%.0f kB)" % (os.path.getsize(a.out) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
