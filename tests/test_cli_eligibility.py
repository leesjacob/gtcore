"""``gt optimize --eligible / --eligible-cone / --phantom-ref`` (Paper 2 Phase 4).

Option parsing and forwarding on the synthetic head phantom, with
``gtcore.plan.optimize`` / ``recommend_tile_count`` faked (tiles conformed on
the real wall so the seed export works) and the pipeline result cached per
module.  ``--eligible craniotomy`` runs the REAL opening finder and face rule
on the phantom's pipeline masks (the synthetic head has a 16 deg craniotomy
cone); ``--phantom-ref`` fakes the registration helper and checks what the
CLI hands to the optimizer.
"""
from __future__ import annotations

import dataclasses
import json
import os

import numpy as np
import pytest

pytest.importorskip("pyvista")           # gtcore.planner (wall_mesh_for) imports it

import gtcore.plan as plan  # noqa: E402
from gtcore.interact import conform_tile, find_overlapping_tiles, snap_to_wall  # noqa: E402
from gtcore.phantom import make_head_phantom  # noqa: E402
from gtcore.pipeline import reconstruct  # noqa: E402
from gtcore.plan import OptimizeReport, SolverResult, TileCountRecommendation  # noqa: E402
from gtcore.plan import candidates as C  # noqa: E402


@pytest.fixture(scope="module")
def result():
    vol, _truth = make_head_phantom(spacing=1.0)
    res = reconstruct(vol, verbose=False)
    if "cavity" not in res.meshes or not len(res.meshes["cavity"].vertices):
        pytest.skip("pipeline found no cavity on this phantom")
    return res


def _tiles_on(mesh, n):
    verts = np.asarray(mesh.vertices, dtype=float)
    out = []
    for i in range(0, len(verts), max(1, len(verts) // 40)):
        surf, n_in = snap_to_wall(mesh, verts[i])
        tile = conform_tile(mesh, surf, n_in, np.array([1.0, 0.0, 0.0]))
        if find_overlapping_tiles(out + [tile], threshold_mm=1.0):
            continue
        out.append(tile)
        if len(out) == n:
            break
    return out


def _install_fakes(monkeypatch, calls):
    def optimize(mesh, n_full, n_half=0, **kw):
        calls.append(("optimize", n_full, n_half, dict(kw)))
        tiles = _tiles_on(mesh, n_full)
        res = SolverResult(selection=np.arange(len(tiles)), objective=0.9,
                           metrics={"V100": 0.9, "D90": 6100.0}, solver=kw.get("solver"))
        return tiles, OptimizeReport(tiles=tiles, solver=res, runtime={"solver": 0.01})

    def recommend_tile_count(mesh, contraction_pct=0.0, untreated_pct=0.0, eligible_faces=None):
        calls.append(("recommend", eligible_faces is None))
        return TileCountRecommendation(
            n_tiles=3, area_mm2=1200.0, treatable_area_mm2=1200.0, contraction_pct=0.0,
            untreated_pct=0.0, ellipsoid_area_mm2=1300.0, n_tiles_ellipsoid=4,
            diameters_mm=[30.0, 28.0, 26.0])

    monkeypatch.setattr(plan, "optimize", optimize)
    monkeypatch.setattr(plan, "recommend_tile_count", recommend_tile_count)


def _last_optimize_kw(calls):
    return [c for c in calls if c[0] == "optimize"][-1][3]


def _shell_result(result):
    meshes = {k: v for k, v in result.meshes.items() if k != "cavity"}
    meshes["body"] = result.meshes["cavity"]
    return dataclasses.replace(result, meshes=meshes)


# ---------------------------------------------------------------- parsing
def test_cli_eligible_options_parse():
    from gtcore.cli import main
    with pytest.raises(SystemExit):
        main(["optimize", "--eligible", "sideways"])
    with pytest.raises(SystemExit):
        main(["optimize", "--eligible-cone", "wide"])
    with pytest.raises(SystemExit) as exc:
        main(["optimize", "--help"])
    assert exc.value.code == 0


# ----------------------------------------------------------- craniotomy
def test_cli_eligible_craniotomy_on_the_synthetic_head(result, monkeypatch, tmp_path, capsys):
    import gtcore.pipeline as pipeline_mod
    from gtcore.cli import main

    calls = []
    _install_fakes(monkeypatch, calls)
    monkeypatch.setattr(pipeline_mod, "reconstruct", lambda vol, **kw: result)
    out = tmp_path / "cran"
    rc = main(["optimize", "--spacing", "1.0", "--out", str(out), "--tiles", "1", "--no-report",
               "--eligible", "craniotomy", "--eligible-cone", "75"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "craniotomy opening:" in text and "eligible faces (craniotomy)" in text
    assert "facing the opening (cone 75 deg)" in text
    kw = _last_optimize_kw(calls)
    elig = kw["eligible"]
    assert isinstance(elig, C.EligibleFaces) and elig.rule == "craniotomy"
    assert elig.info["cone_deg"] == 75.0 and 0.0 < elig.fraction < 1.0
    assert kw["eligible_faces"] is not None and np.array_equal(kw["eligible_faces"], elig.mask)
    assert elig.info["opening"]["n_voxels"] > 0
    mask_file = out / "eligible_faces.npy"
    assert mask_file.exists() and np.array_equal(np.load(mask_file), elig.mask)
    # the recommendation ran on the eligible wall
    assert [c for c in calls if c[0] == "recommend"][-1][1] is False
    # the real rule agrees with the direct call on the same inputs
    op = C.craniotomy_opening(result.masks, result.volume.affine, cavity_mask=result.cavity_mask)
    direct = C.craniotomy_faces(result.meshes["cavity"], op,
                                implant_center_ras=result.seeds.centers_ras.mean(axis=0),
                                cone_deg=75.0)
    assert np.array_equal(direct.mask, elig.mask)


def test_cli_eligible_visible_and_all(result, monkeypatch, tmp_path, capsys):
    import gtcore.pipeline as pipeline_mod
    from gtcore.cli import main

    calls = []
    _install_fakes(monkeypatch, calls)
    monkeypatch.setattr(pipeline_mod, "reconstruct", lambda vol, **kw: result)
    rc = main(["optimize", "--spacing", "1.0", "--out", str(tmp_path / "vis"), "--tiles", "1",
               "--no-report", "--eligible", "visible"])
    assert rc == 0
    kw = _last_optimize_kw(calls)
    assert kw["eligible"].rule == "visible" and kw["eligible_faces"] is not None
    assert "eligible faces (visible from the implant)" in capsys.readouterr().out
    # 'all' on a phantom shell: no mask (the historical call), the rule recorded
    monkeypatch.setattr(pipeline_mod, "reconstruct", lambda vol, **kw: _shell_result(result))
    rc = main(["optimize", "--spacing", "1.0", "--out", str(tmp_path / "all"), "--tiles", "1",
               "--no-report", "--eligible", "all"])
    assert rc == 0
    kw = _last_optimize_kw(calls)
    assert kw["eligible_faces"] is None and kw["eligible"].rule == "all"
    # default (auto) on the shell stays 'visible'
    rc = main(["optimize", "--spacing", "1.0", "--out", str(tmp_path / "auto"), "--tiles", "1",
               "--no-report"])
    assert rc == 0
    kw = _last_optimize_kw(calls)
    assert kw["eligible"].rule == "visible" and kw["eligible_faces"] is not None


def test_cli_craniotomy_fails_loudly_without_an_opening(result, monkeypatch, tmp_path, capsys):
    import gtcore.pipeline as pipeline_mod
    from gtcore.cli import main

    calls = []
    _install_fakes(monkeypatch, calls)
    monkeypatch.setattr(pipeline_mod, "reconstruct", lambda vol, **kw: result)

    def no_opening(*a, **k):
        raise ValueError("craniotomy_opening: no opening found -- test")

    monkeypatch.setattr(C, "craniotomy_opening", no_opening)
    rc = main(["optimize", "--spacing", "1.0", "--out", str(tmp_path / "x"), "--tiles", "1",
               "--no-report", "--eligible", "craniotomy"])
    assert rc == 1
    assert "optimize failed: craniotomy_opening: no opening found" in capsys.readouterr().out
    assert not any(c[0] == "optimize" for c in calls)


# ------------------------------------------------------------ phantom ref
def test_cli_phantom_ref_uses_the_registered_twin(result, monkeypatch, tmp_path, capsys):
    import gtcore.io as io_mod
    import gtcore.pipeline as pipeline_mod
    import gtcore.segment.cavity as cav_mod
    from gtcore.cli import main

    calls = []
    _install_fakes(monkeypatch, calls)
    monkeypatch.setattr(pipeline_mod, "reconstruct", lambda vol, **kw: result)
    loaded = []
    monkeypatch.setattr(io_mod, "load_volume", lambda path: loaded.append(path) or result.volume)
    shell = np.asarray(result.masks["cranial_interior"], bool) & ~np.asarray(result.cavity_mask, bool)
    seen = {}

    def fake_phantom(vol_ph8, vol_ph0, masks_ph8=None, seed_centers_ras=None, **kw):
        seen.update(masks=masks_ph8, seeds=seed_centers_ras)
        info = {"registration": {"dice_masks": 0.98, "iterations": 12, "seconds": 1.0},
                "lumen": {"volume_ml": 30.0, "rule": "fake rule", "hull_reach_mm": 14.0,
                          "seeds_inside": 32, "n_seeds": 32},
                "wall": {"n_faces_wall": len(result.meshes["cavity"].faces),
                         "area_wall_mm2": 5000.0},
                "residual_mm": {"all": {"median": 0.3, "p95": 0.9},
                                "far_from_seeds": {"median": 0.25}},
                "lumen_volume_ml_ph8_grid": 29.5, "shell_mask": shell}
        return result.meshes["cavity"], result.cavity_mask, result.volume.affine, info

    monkeypatch.setattr(cav_mod, "phantom_wall_and_cavity", fake_phantom)
    out = tmp_path / "ph"
    rc = main(["optimize", "--spacing", "1.0", "--out", str(out), "--tiles", "1", "--no-report",
               "--phantom-ref", "twin-dir"])
    assert rc == 0
    text = capsys.readouterr().out
    assert loaded == ["twin-dir"]
    assert seen["masks"] is result.masks and np.allclose(seen["seeds"], result.seeds.centers_ras)
    assert "wall: phantom wall (tile-less twin, registered)" in text
    assert "HR-CTV: RIND5 of the phantom cavity" in text and "cavity 30.0 cc" in text
    kw = _last_optimize_kw(calls)
    assert kw["cavity_mask"] is result.cavity_mask
    assert kw["clip_mask"] is shell and np.allclose(kw["clip_affine"], result.volume.affine)
    assert kw["cavity_source"] == "PH0:lumen(registered)"
    assert kw["clip_source"] == "PH0:shell(registered)"
    # auto eligibility on the phantom wall = visible from the implant
    assert kw["eligible"].rule == "visible" and kw["eligible_faces"] is not None
    rep = json.loads((out / "report.json").read_text())
    assert rep["n_tiles"] == 1
    assert (out / "eligible_faces.npy").exists()
