"""scripts/paper_tables.py: Tables 1-4, S3, S4 and the caption numbers are
built from the committed data into a temporary folder and every value passes
the ``--check`` traceability report; the check catches a tampered value; the
campaign rows switch from "pending" to measured when the campaign files land.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("paper_tables", os.path.join(ROOT, "scripts", "paper_tables.py"))
pt = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pt
_spec.loader.exec_module(pt)

NAMES = ["table1", "table2", "table3", "table4", "tableS3_engine_qa", "tableS4_optimizer", "caption_numbers"]


def _rows(path):
    with open(path, encoding="utf-8", newline="") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    return list(csv.DictReader(io.StringIO("".join(lines))))


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("tables")
    summary = out / "results-summary.md"
    repo = pt.Repo(ROOT)
    tables = pt.build(repo, str(out), str(summary))
    return repo, out, summary, tables


def test_every_table_written_as_csv_and_md(built):
    _, out, summary, tables = built
    assert set(tables) == set(NAMES)
    for n in NAMES:
        assert (out / (n + ".csv")).is_file() and (out / (n + ".md")).is_file()
        rows = _rows(out / (n + ".csv"))
        assert rows and all(r[pt.LOC] for r in rows), n
    assert summary.is_file()


def test_check_passes_on_the_committed_data(built, capsys):
    repo, out, _, _ = built
    assert pt.check(repo, str(out)) == 0
    text = capsys.readouterr().out
    assert "no problems" in text and "locator parts checked" in text


def test_main_build_and_check_cli(tmp_path):
    rc = pt.main(["--out", str(tmp_path), "--summary", str(tmp_path / "rs.md"), "--check"])
    assert rc == 0
    assert pt.main(["--out", str(tmp_path), "--summary", "none", "--check", "--no-build"]) == 0


def test_table3_columns_flags_and_key_numbers(built):
    _, out, _, _ = built
    rows = _rows(out / "table3.csv")
    assert list(rows[0].keys()) == pt.T3_COLUMNS + [pt.LOC]
    flags = {r["flag"] for r in rows}
    assert flags <= {"calibration-set", "synthetic", "n/a", "synthetic / calibration-set"}
    text = " ".join(r["value"] for r in rows)
    for needle in ("30/30", "32/32", "6/8 tiles", "7/8 tiles", "10 tiles (6 full + 4 half)",
                   "6365 cGy", "6167 cGy", "6288 cGy", "6323 cGy", "99.3 %", "97.0 %", "98.8 %",
                   "+3.65 mm", "+3.05 mm", "Dice 0.896", "Dice 0.665", "Dice 0.881", "55.3 cc",
                   "9.05 %", "[6133, 6212]", "0.29 %", "+2.0 %", "pending MIM export"):
        assert needle in text, needle
    # every real-data row is calibration-set; no held-out flag anywhere
    assert not any("held-out" in r["flag"] for r in rows)
    real = [r for r in rows if r["dataset"].startswith(("PT-", "PH8"))]
    assert real and all(r["flag"].startswith("calibration-set") or "calibration-set" in r["flag"] for r in real)
    # the half-tile count-known row is marked pending the operative record
    assert any("PENDING OPERATIVE RECORD" in r["metric"] for r in rows)
    # gamma rows exist for both axis conventions
    assert sum("gamma pass rate" in r["metric"] for r in rows) == 4
    assert any("S-I axes" in r["metric"] and "gamma" in r["metric"] for r in rows)


def test_campaign_rows_are_pending_until_the_files_land(built):
    _, out, _, _ = built
    rows = _rows(out / "table3.csv")
    pend = [r for r in rows if r[pt.LOC].startswith("pending:")]
    expected = {pt.CAPACITY, pt.V2_PRIMARY, pt.V6_PHANTOM, pt.F8_SUMMARY}
    assert {r[pt.LOC][len("pending:"):] for r in pend} == {p for p in expected if not pt.Repo(ROOT).exists(p)
                                                           or pt.Repo(ROOT).csv_commit(p) is None}
    patient = [r for r in rows if r["metric"].startswith("patient")]
    assert len(patient) == 1 and "three arms" in patient[0]["metric"]


def test_formatting_conventions():
    assert pt.fmt("cgy", "6364.65") == "6365"
    assert pt.fmt("gy1", "6364.65") == "63.6"
    assert pt.fmt("pct1", "0.937363") == "93.7"
    assert pt.fmt("mm2", "3.648") == "3.65"
    assert pt.fmt("f3", "0.881429") == "0.881"
    assert pt.fmt("cgy", "nan") == "n/a" and pt.fmt("cgy", "") == "n/a"


def test_lookup_roundtrip_and_uniqueness():
    lk = pt.clin("IMPLANT_REACH(r=10 mm)", "-", "dice_vs_CTV_GT", "f3")
    back = pt.Lookup.decode(lk.encode())
    assert back == lk
    assert back.value(pt.Repo(ROOT)) == "0.881"
    with pytest.raises(LookupError):
        pt.L(pt.CLIN, {"metric": "D90"}, "value", "cgy").value(pt.Repo(ROOT))   # many rows match


def test_markdown_tables_render(built):
    _, out, _, tables = built
    for n, t in tables.items():
        with open(out / (n + ".md"), encoding="utf-8") as fh:
            lines = [ln.rstrip("\n") for ln in fh if ln.startswith("|")]
        assert len(lines) == len(t.rows) + 2
        import re
        for ln in lines:
            assert len(re.split(r"(?<!\\)\|", ln)) - 2 == len(t.columns), (n, ln[:80])


def test_check_catches_a_tampered_value(built, tmp_path):
    repo, out, _, _ = built
    for n in NAMES:
        shutil.copy(out / (n + ".csv"), tmp_path / (n + ".csv"))
        shutil.copy(out / (n + ".md"), tmp_path / (n + ".md"))
    p = tmp_path / "table3.csv"
    txt = p.read_text(encoding="utf-8")
    assert "6167 cGy" in txt
    p.write_text(txt.replace("6167 cGy", "6170 cGy", 1), encoding="utf-8")
    assert pt.check(repo, str(tmp_path), verbose=False) > 0


def test_locator_parts_detect_missing_tokens_and_landed_pending(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "n.md").write_text("value 3.65 mm at abc1234\n", encoding="utf-8")
    (tmp_path / "x.csv").write_text("# commit: abc1234\na,b\n1,2\n", encoding="utf-8")
    b = pt.Builder(pt.Repo(str(tmp_path)))
    assert b._verify_part("md:docs/n.md#3.65 mm|abc1234", "", "") is None
    assert "tokens not found" in b._verify_part("md:docs/n.md#4.6 mm", "", "")
    assert "has landed" in b._verify_part("pending:x.csv", "", "")
    assert b._verify_part("pending:y.csv", "", "") is None
    assert b._verify_part("csv:x.csv?a=1#b@int", "value 2 here", "abc1234") is None
    assert "not in row" in b._verify_part("csv:x.csv?a=1#b@int", "value 3", "abc1234")


@pytest.fixture(scope="module")
def landed_root(tmp_path_factory):
    """A copy of the committed docs + gtcore sources with fake campaign outputs."""
    root = tmp_path_factory.mktemp("landed")
    shutil.copytree(os.path.join(ROOT, "docs"), root / "docs", ignore=shutil.ignore_patterns("*.npz", "*.png"))
    shutil.copytree(os.path.join(ROOT, "gtcore"), root / "gtcore", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    hdr = "# script: scripts/patient_optimize.py\n# command: python scripts/patient_optimize.py <test>\n# commit: 1234abc\n"
    cols = ["arm", "sa_seed", "RIND5_gtcore_V100", "RIND5_gtcore_D90_cGy", "CTV_GT_V100", "CTV_GT_D90_cGy",
            "REACH10_V100", "REACH10_D90_cGy", "n_overlaps", "n_tiles", "n_full", "n_half"]
    data = [["as_implanted", "", "0.387", "2653", "0.917", "6167", "0.960", "6700", "0", "6", "6", "0"],
            ["optimized_rind5_sa_best", "1", "0.500", "3400", "0.760", "4800", "0.780", "4950", "0", "8", "8", "0"],
            ["optimized_ctv_gt_sa_best", "2", "0.420", "2900", "0.950", "6500", "0.970", "6900", "0", "8", "8", "0"]]
    with open(root / "docs" / "figures" / "optimize" / "data" / "f8_patient_summary.csv", "w", newline="", encoding="utf-8") as fh:
        fh.write(hdr)
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(data)
    with open(root / "docs" / "figures" / "data" / "table4_runtimes.csv", "w", newline="", encoding="utf-8") as fh:
        fh.write("# script: scripts/table4_runtimes.py\n# commit: 1234abc\n")
        w = csv.writer(fh)
        w.writerow(["dataset", "block", "stage", "seconds", "n_faces", "n_candidates", "n_voxels", "n_seeds", "note", "commit", "hardware"])
        w.writerow(["PT-CT1mm", "a", "reconstruct total (count-free tile fit)", "38.13", "", "", "", "32", "", "1234abc", "AMD Ryzen 5 7600X"])
        w.writerow(["PH8", "a", "reconstruct total (count-free tile fit)", "17.65", "", "", "", "32", "", "1234abc", "AMD Ryzen 5 7600X"])
    return root


def test_campaign_files_landing_replace_the_pending_rows(landed_root, tmp_path, capsys):
    repo = pt.Repo(str(landed_root))
    tables = pt.build(repo, str(tmp_path), None)
    t3 = tables["table3"]
    patient = [r for r in t3.rows if r.cells["metric"].startswith("patient")]
    assert len(patient) == 1 and not patient[0].locator.startswith("pending:")
    v = patient[0].cells["value"]
    for needle in ("as_implanted", "optimized_rind5_sa_best", "optimized_ctv_gt_sa_best", "CTV_GT 76.0 % / 4800 cGy", "RIND5(gtcore) 38.7 % / 2653 cGy"):
        assert needle in v, needle
    t4 = tables["table4"]
    assert [r.cells["seconds"] for r in t4.rows] == ["38.1", "17.6"]
    assert "AMD Ryzen 5 7600X" in t4.preamble[0]
    caps = [r for r in tables["caption_numbers"].rows if r.cells["figure"] == "F8" and r.cells["panel"] == "b"]
    assert sum(not r.locator.startswith("pending:") and "optimized" in r.cells["item"] for r in caps) == 2
    assert pt.check(repo, str(tmp_path)) == 0
