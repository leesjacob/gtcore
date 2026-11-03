"""Tests for ``scripts/export_figures_medphys.py``: a fake minimal repository
(manifest + rendered panels + committed data) exported into tmp; refusal of a
non-empty folder; ``SUMMARY.md`` and foreign files preserved under
``--overwrite``; edited managed files kept; the inventory and format checks."""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "export_figures_medphys.py")

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402


def _load():
    spec = importlib.util.spec_from_file_location("export_figures_medphys", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod          # dataclasses resolve the module by name
    spec.loader.exec_module(mod)
    return mod


ex = _load()

HEADER = "# made by scripts/fake.py at commit abc1234 from PT-CT1mm\n"


def _png(path, size, dpi=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im = Image.new("RGB", size, (255, 255, 255))
    if dpi:
        im.save(path, dpi=(dpi, dpi))
    else:
        im.save(path)


def _touch(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    figs = r / "output" / "figures"
    data = r / "docs" / "figures" / "data"
    # f2c: a 2-D chart, all three formats, 600 dpi, one CSV source with a header comment
    _png(str(figs / "f2c_model.png"), (120, 80), dpi=600)
    _touch(str(figs / "f2c_model.pdf"), "%PDF")
    _touch(str(figs / "f2c_model.svg"), "<svg/>")
    _touch(str(data / "f2c_src.csv"), HEADER + "a,b\n1,2\n")
    # f4b: a 3-D render, 2400 px, no dpi metadata, NPZ source with a JSON sidecar
    _png(str(figs / "f4b_render.png"), (2400, 1800))
    _touch(str(figs / "f4b_render.pdf"), "%PDF")
    _touch(str(figs / "f4b_render.svg"), "<svg/>")
    _touch(str(data / "f4b_scene.npz"), "npz")
    _touch(str(data / "f4b_scene.json"), "{}")
    # f5b: 300 dpi and no SVG -> two violations
    _png(str(figs / "f5b_dvh.png"), (100, 100), dpi=300)
    _touch(str(figs / "f5b_dvh.pdf"), "%PDF")
    # unrelated committed data -> data/
    _touch(str(data / "table_source.csv"), HEADER + "x\n1\n")
    _touch(str(r / "docs" / "figures" / "tables" / "table4.csv"), "# Table 4\nstage,seconds\nfit,1.0\n")
    _touch(str(r / "docs" / "figures" / "tables" / "table4.md"), "| stage | s |\n")
    _touch(str(r / "docs" / "figures" / "tables" / "table1.csv"), "a\n")
    _touch(str(r / "docs" / "results-summary.md"), "# results\n")
    _touch(str(r / "gtcore" / "planner.py"), "self.pl = pv.Plotter(window_size=(1280, 900), title=t)\n")
    manifest = {
        "script": "scripts/paper_figures.py", "script_commit": "deadbee", "generated": "2026-11-03 10:00",
        "tile_mode": "auto",
        "panels": {
            "f2c": {"figure": "F2", "status": "ok", "script_commit": "deadbee", "rendered": "t",
                    "description": "F2(c): tile model.",
                    "sources": [{"path": "docs/figures/data/f2c_src.csv", "exists": True,
                                 "header_commit": "abc1234", "git_commit": "abc1235"}],
                    "outputs": ["output/figures/f2c_model.png", "output/figures/f2c_model.pdf",
                                "output/figures/f2c_model.svg"], "key_numbers": {}, "note": ""},
            "f4b": {"figure": "F4", "status": "ok", "script_commit": "deadbee",
                    "sources": [{"path": "docs/figures/data/f4b_scene.npz", "exists": True}],
                    "outputs": ["output/figures/f4b_render.png", "output/figures/f4b_render.pdf",
                                "output/figures/f4b_render.svg"],
                    "key_numbers": {"window_px": [2400, 1800]}, "note": ""},
            "f5b": {"figure": "F5", "status": "ok", "script_commit": "deadbee", "sources": [],
                    "outputs": ["output/figures/f5b_dvh.png", "output/figures/f5b_dvh.pdf"], "note": ""},
            "f8b": {"figure": "F8", "status": "skipped", "script_commit": "deadbee",
                    "sources": [{"path": "docs/figures/data/f8b_opt.npz", "exists": False}],
                    "note": "campaign output f8b_opt.npz not committed yet"},
            "s5_dvh3": {"figure": "S5 (optional)", "status": "skipped", "sources": [], "note": "optional chart"},
        },
    }
    _touch(str(r / "docs" / "figures" / "manifest.json"), json.dumps(manifest, indent=1))
    return r


def _run(repo, out, *extra):
    return ex.main(["--repo", str(repo), "--out", str(out), *extra])


def test_export_layout_and_checks(repo, tmp_path, capsys):
    out = tmp_path / "Figs"
    assert _run(repo, out) == 0
    txt = capsys.readouterr().out
    for f in ex.FIXED_FOLDERS + ["tables", "data"]:
        assert (out / f).is_dir(), f
    assert not (out / "figS5").exists()            # optional + not rendered: no folder
    # panels and sources, byte copies (header comment kept), NPZ sidecar JSON
    for name in ("f2c_model.png", "f2c_model.pdf", "f2c_model.svg"):
        assert (out / "fig02" / name).exists()
    assert (out / "fig02" / "data" / "f2c_src.csv").read_text(encoding="utf-8").startswith(HEADER)
    assert (out / "fig04" / "data" / "f4b_scene.json").exists()
    assert (out / "data" / "table_source.csv").exists()
    assert not (out / "data" / "f2c_src.csv").exists()          # tied to a panel -> only in fig02
    # tables, F1 gets Table 4, F7 the command and window size, F9 a README
    assert (out / "tables" / "table4.csv").exists() and (out / "tables" / "table1.csv").exists()
    assert (out / "fig01" / "table4.csv").exists()
    f7 = (out / "fig07" / "README.md").read_text(encoding="utf-8")
    assert 'gt plan "$GT_DATA_ROOT/<PH8>" --hrctv' in f7 and "1280 x 900 px" in f7 and ex.TAG in f7
    assert "Supplied by Jacob" in (out / "fig09" / "README.md").read_text(encoding="utf-8")
    # pending note for the skipped panel
    assert "not committed yet" in (out / "fig08" / "f8b.PENDING.md").read_text(encoding="utf-8")
    # root files
    assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["script_commit"] == "deadbee"
    assert (out / "results-summary.md").read_text(encoding="utf-8") == "# results\n"
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "`python scripts/paper_figures.py --only f2c`" in readme
    assert "abc1234 / abc1235" in readme and "deadbee" in readme
    assert "## Style bar" in readme and "## Regenerate from the tag" in readme
    assert not (out / "SUMMARY.md").exists()
    # checks: f5b dpi + missing SVG; the 3-D render without dpi passes on its pixel size
    assert "f5b: fig05/f5b_dvh.png dpi metadata 300 x 300" in txt
    assert "f5b: f5b_dvh lacks SVG" in txt
    assert "f4b" not in "".join(l for l in txt.splitlines() if "dpi" in l or "px" in l)
    assert "f8b" in txt and "not committed yet" in txt
    assert "s5_dvh3" in txt and "optional" in txt


def test_inventory_counts(repo, tmp_path, capsys):
    out = tmp_path / "Figs"
    assert _run(repo, out) == 0
    lines = {l.split()[0]: l.split()[1:] for l in capsys.readouterr().out.splitlines()
             if l.split() and l.split()[0] in ("fig02", "fig04", "fig07", "tables", "data")}
    # png pdf svg data readme other on-disk
    assert lines["fig02"] == ["1", "1", "1", "1", "1", "0", "5"]
    assert lines["fig04"] == ["1", "1", "1", "2", "1", "0", "6"]
    assert lines["fig07"] == ["0", "0", "0", "0", "1", "0", "1"]
    assert lines["tables"][3] == "2" and lines["tables"][5] == "1"
    assert lines["data"][3] == "1"


def test_refuses_non_empty_folder(repo, tmp_path, capsys):
    out = tmp_path / "Figs"
    out.mkdir()
    (out / "notes.txt").write_text("mine", encoding="utf-8")
    assert _run(repo, out) == 2
    assert "refusing" in capsys.readouterr().err
    assert sorted(os.listdir(out)) == ["notes.txt"]


def test_overwrite_keeps_summary_and_foreign_files(repo, tmp_path, capsys):
    out = tmp_path / "Figs"
    assert _run(repo, out) == 0
    (out / "SUMMARY.md").write_text("coordinator", encoding="utf-8")
    (out / "fig07" / "f7_frame1.png").write_bytes(b"jacob")
    # a source changes in the repository; a managed README is edited by hand
    src = repo / "docs" / "figures" / "data" / "f2c_src.csv"
    src.write_text(HEADER + "a,b\n3,4\n", encoding="utf-8")
    (out / "fig09" / "README.md").write_text("Jacob's notes", encoding="utf-8")
    capsys.readouterr()
    assert _run(repo, out) == 2                       # still refuses without --overwrite
    assert _run(repo, out, "--overwrite") == 0
    txt = capsys.readouterr().out
    assert (out / "SUMMARY.md").read_text(encoding="utf-8") == "coordinator"
    assert (out / "fig07" / "f7_frame1.png").read_bytes() == b"jacob"
    assert (out / "fig02" / "data" / "f2c_src.csv").read_text(encoding="utf-8").endswith("3,4\n")
    assert (out / "fig09" / "README.md").read_text(encoding="utf-8") == "Jacob's notes"
    assert "kept: fig09/README.md (edited since the last export)" in txt
    assert "SUMMARY.md (coordinator's, untouched)" in txt
    # still detected as edited on the next run; --replace-modified replaces it
    assert _run(repo, out, "--overwrite") == 0
    assert (out / "fig09" / "README.md").read_text(encoding="utf-8") == "Jacob's notes"
    assert _run(repo, out, "--overwrite", "--replace-modified") == 0
    assert "Supplied by Jacob" in (out / "fig09" / "README.md").read_text(encoding="utf-8")
    assert (out / "SUMMARY.md").read_text(encoding="utf-8") == "coordinator"


def test_stale_pending_note_reported_not_deleted(repo, tmp_path, capsys):
    out = tmp_path / "Figs"
    assert _run(repo, out) == 0
    # the panel lands: render outputs appear and the manifest marks it ok
    figs = repo / "output" / "figures"
    _png(str(figs / "f8b_opt.png"), (2400, 2400))
    _touch(str(figs / "f8b_opt.pdf"), "%PDF")
    _touch(str(figs / "f8b_opt.svg"), "<svg/>")
    mp = repo / "docs" / "figures" / "manifest.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["panels"]["f8b"].update(status="ok", sources=[], note="",
                              outputs=["output/figures/f8b_opt.%s" % e for e in ("png", "pdf", "svg")])
    mp.write_text(json.dumps(m), encoding="utf-8")
    capsys.readouterr()
    assert _run(repo, out, "--overwrite") == 0
    txt = capsys.readouterr().out
    assert (out / "fig08" / "f8b_opt.png").exists()
    assert (out / "fig08" / "f8b.PENDING.md").exists()
    assert "stale" in txt and "fig08/f8b.PENDING.md" in txt


def test_strict_exit_on_violation(repo, tmp_path):
    assert _run(repo, tmp_path / "Figs", "--strict") == 3


def test_summary_never_managed():
    plan = ex.Plan()
    with pytest.raises(ValueError):
        plan.add(ex.Item("SUMMARY.md", text="x"))


def test_folder_mapping():
    assert ex.folder_for("F4", "f4b") == "fig04"
    assert ex.folder_for("S3 (optional)", "s3_gamma") == "figS3"
    assert ex.folder_for("", "f8b_rind") == "fig08"
    assert ex.figure_key("figS2") == "S2" and ex.figure_key("fig07") == "F7"


@pytest.mark.skipif(not os.path.exists(os.path.join(ROOT, "docs", "figures", "manifest.json")),
                    reason="no committed manifest")
def test_repository_manifest_maps_to_the_fixed_folders(tmp_path):
    with open(os.path.join(ROOT, "docs", "figures", "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    plan = ex.build_plan(ROOT, manifest, str(tmp_path / "no_renders"), extra_data=False)
    for r in plan.panels:
        if r.status == "ok" and "optional" not in r.figure.lower():
            assert r.folder in ex.FIXED_FOLDERS, (r.pid, r.folder)
    # without rendered files every ok panel is reported missing, never silently dropped
    missing = {pid for pid, _ in plan.pending}
    assert {r.pid for r in plan.panels if r.status == "ok"} <= missing
