"""Smoke tests for ``scripts/paper_figures.py``: ``--list`` and three cheap
panels (F5(b) DVH, F4(a) seed slice, S2(a) spacing chart) rendered from the
committed data into tmp; one 3-D render when off-screen rendering works."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "paper_figures.py")
DATA = os.path.join(ROOT, "docs", "figures", "data")


def _run(args, tmp_path):
    env = dict(os.environ, PYTHONPATH=ROOT, MPLBACKEND="Agg")
    return subprocess.run([sys.executable, SCRIPT] + args, cwd=ROOT, env=env, capture_output=True,
                          text=True, timeout=600)


def test_list_prints_every_panel(tmp_path):
    r = _run(["--list"], tmp_path)
    assert r.returncode == 0, r.stderr
    for pid in ("f2c", "f3a", "f3c", "f4a", "f4b", "f4c", "f5a", "f5b", "f6", "f8a", "f8b", "s2a", "s2b", "s4"):
        assert any(line.split()[:1] == [pid] for line in r.stdout.splitlines()), pid
    # sources are listed with their header commits
    assert "clinical_dvh_hrctv.csv @ " in r.stdout


@pytest.mark.skipif(not os.path.exists(os.path.join(DATA, "clinical_slice.npz")), reason="committed panel data missing")
def test_cheap_panels_render(tmp_path):
    out = tmp_path / "fig"
    man = tmp_path / "manifest.json"
    r = _run(["--only", "f5b,f4a,s2a", "--out", str(out), "--manifest", str(man)], tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    for stem in ("f5b_dvh", "f4a_seed_slice", "s2a_recall_error_vs_spacing"):
        for ext, min_bytes in (("png", 20_000), ("pdf", 5_000), ("svg", 5_000)):
            p = out / ("%s.%s" % (stem, ext))
            assert p.exists(), p
            assert p.stat().st_size > min_bytes, (p, p.stat().st_size)
        svg = (out / (stem + ".svg")).read_text(encoding="utf-8")
        assert "<text" in svg            # svg.fonttype none: text stays editable
    from PIL import Image
    with Image.open(out / "f5b_dvh.png") as im:
        w, h = im.size
    assert w >= 2000 and abs(w / 600 - 3.7) < 0.05     # 600 dpi at the stated panel width
    m = json.loads(man.read_text(encoding="utf-8"))
    f5b = m["panels"]["f5b"]
    assert f5b["status"] == "ok"
    assert abs(f5b["key_numbers"]["D90_cGy"]["tps"] - 6364.65) < 1.0
    assert abs(f5b["key_numbers"]["D90_cGy"]["gtcore32"] - 6167.48) < 1.0
    assert all(s["header_commit"] for s in f5b["sources"])
    assert m["script_commit"]


def _offscreen_ok() -> bool:
    try:
        import pyvista as pv
        pv.OFF_SCREEN = True
        pl = pv.Plotter(off_screen=True, window_size=[64, 64])
        pl.add_mesh(pv.Sphere())
        img = pl.screenshot(None, return_img=True)
        pl.close()
        return img is not None and img.shape[0] == 64
    except Exception:
        return False


@pytest.mark.skipif(not _offscreen_ok(), reason="off-screen rendering unavailable")
def test_render_scene_small(tmp_path):
    spec = importlib.util.spec_from_file_location("paper_figures", SCRIPT)
    pf = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    spec.loader.exec_module(pf)
    import numpy as np
    d = pf._npz(os.path.join(DATA, "f4c_phantom_implant.npz"))
    tiles, leftover, info = pf._implant_tiles(d, "auto")
    assert info["n_tiles"] == 8 and info["n_leftover"] == 0 and info["degraded_tiles"] == [7]
    img = pf.render_scene(pf.S.CAMERA["phantom"], None, tiles, pf._seed_groups(d, leftover), window=(200, 200))
    assert img.shape[:2] == (200, 200)
    assert (img < 250).any() and np.std(img) > 5     # something was drawn


def test_half_tile_from_pair_geometry():
    spec = importlib.util.spec_from_file_location("paper_figures", SCRIPT)
    pf = importlib.util.module_from_spec(spec)
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    spec.loader.exec_module(pf)
    import numpy as np
    c0, c1 = np.array([0.0, 0.0, 0.0]), np.array([9.0, 0.0, 0.0])
    tissue, cavity = pf.half_tile_sheets(c0, c1, np.array([4.5, 0.0, 20.0]), 5)
    # 20 mm along the pair, 10 mm across, 3 mm toward the wall, 1 mm toward the cavity
    assert np.isclose(np.ptp(tissue[:, 0]), 20.0) and np.isclose(np.ptp(tissue[:, 1]), 10.0)
    assert np.allclose(tissue[:, 2], -3.0) and np.allclose(cavity[:, 2], 1.0)
