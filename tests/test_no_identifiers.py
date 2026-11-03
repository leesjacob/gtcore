"""The committed tree carries no machine path, patient folder name, pseudonym
or DICOM header value (``scripts/check_no_identifiers.py``).

The campaign-owned files (``scripts/validation_optimize.py``,
``docs/optimize-notes.md``, ``docs/figures/optimize/``) are allowlisted inside
the script while the Phase 4 campaign runs.  TODO(coordinator): after the
campaign merges, scrub them, delete ``ALLOWLIST`` and this note; the strict
test below then becomes the only one.
"""
from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPT = os.path.join(REPO, "scripts", "check_no_identifiers.py")


@pytest.fixture(scope="module")
def checker():
    spec = importlib.util.spec_from_file_location("check_no_identifiers", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_repository_tree_is_clean():
    r = subprocess.run([sys.executable, SCRIPT, "--root", REPO],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, "forbidden identifiers in the tree:\n" + r.stdout + r.stderr


@pytest.mark.xfail(strict=False, reason="campaign-owned files are allowlisted until the "
                   "Phase 4 campaign merges; TODO(coordinator): remove the allowlist")
def test_repository_tree_is_clean_strict():
    r = subprocess.run([sys.executable, SCRIPT, "--root", REPO, "--strict"],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("bad", [
    r"data = r'C:\Users\someone\Documents\scan'",
    "cached under /Users/someone/scans",
    "synced from OneDrive yesterday",
    "folder DOEJOHNPOSTCT holds the thin-cut",
    "PatientName DOE^JOHN",
    "the JohnDoe case",
    "see Case 54 and Case54",
    "the DOE thin-cut",
    "3D-Printed Phantom-8tiles (223)",
    "CT 3D printed",
    "id ANON99939",
    "Head^SPIRAL.STEALTH.(Adult)",
    "kernel STEALTH 1.0 Hr40",
    "acquired 2023-10-26 09:09:16",
    "uid 1.3.46.670589.11.1234.5.0.1",
    "uid 1.2.826.0.1.99999.1.2.3.4.5",
])
def test_checker_catches_each_kind(checker, tmp_path, bad):
    (tmp_path / "note.md").write_text("fine line\n%s\n" % bad, encoding="utf-8")
    out = io.StringIO()
    n = checker.check(str(tmp_path), use_git=False, out=out)
    assert n >= 1, out.getvalue()
    assert "note.md:2:" in out.getvalue()


def test_checker_accepts_dataset_ids_standard_uids_and_binary(checker, tmp_path):
    good = "\n".join([
        'python scripts/x.py "$GT_DATA_ROOT/<PT-CT1mm>" "$GT_DATA_ROOT/<PT-RTSTRUCT>"',
        "PH8 PH0 PT-CT2mm PT-preMRI; GT_DATASETS_JSON maps the ids",
        "_RTDOSE_SOP = '1.2.840.10008.5.1.4.1.1.481.2'",
        "FOR_UID = '1.2.826.0.1.3680043.8.498.1'   # pydicom public root, synthetic",
        "the patient does not have a second case; 54 seeds; case 5",
        r"venv at %USERPROFILE%\.venvs\gammatile",
    ])
    (tmp_path / "ok.py").write_text(good, encoding="utf-8")
    (tmp_path / "blob.npz").write_bytes(b"\x00\x01C:\\Users\\x")       # binary: skipped
    out = io.StringIO()
    assert checker.check(str(tmp_path), use_git=False, out=out) == 0, out.getvalue()


def test_allowlist_warns_but_does_not_fail_unless_strict(checker, tmp_path):
    sub = tmp_path / "docs"
    sub.mkdir()
    (sub / "campaign.md").write_text("path C:\\Users\\x\\y\n", encoding="utf-8")
    out = io.StringIO()
    n = checker.check(str(tmp_path), use_git=False, allowlist=("docs/campaign.md",), out=out)
    assert n == 0 and "allowlisted" in out.getvalue()
    n_strict = checker.check(str(tmp_path), use_git=False, strict=True,
                             allowlist=("docs/campaign.md",), out=io.StringIO())
    assert n_strict == 1
