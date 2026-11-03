"""Fail when a machine path, a patient folder name, a patient pseudonym or a
DICOM header value is committed anywhere in the tree.

Usage (from the repo root)::

    python scripts/check_no_identifiers.py            # exit 1 on any hit
    python scripts/check_no_identifiers.py --strict   # also fail on the allowlisted files
    python scripts/check_no_identifiers.py --root <dir> [--no-git]

Scans every git-tracked and untracked-but-not-ignored text file (so ``output/``
and the venv are never read) except ``.git``, this script and its test.  Each
hit is printed as ``path:line: <rule>: <match>``.  Files on the ALLOWLIST are
reported as warnings and do not fail the run unless ``--strict`` is given.

Rules (``gtcore.io.datasets`` is the replacement: ``GT_DATA_ROOT`` + dataset ids):

* machine paths: a Windows user profile (``C:\\Users\\...``), a macOS home
  (``/Users/<name>``), cloud-sync and checkout folder names, temp/scratchpad paths;
* the patient pseudonym in any spelling, its ``DOE`` fragment, the superseded
  "Case 54" label and the patient folder names;
* the phantom folder names (dataset ids ``PH8`` / ``PH0`` replace them);
* DICOM header values copied from the clinical export: anonymised patient id,
  study / series descriptions, reconstruction kernel, acquisition date and
  times, vendor UID roots and any instance UID (7+ dotted segments) that is
  not under the DICOM standard root ``1.2.840.10008`` or the public
  ``1.2.826.0.1.3680043`` root used for synthetic test files.

``tests/test_no_identifiers.py`` runs this on the tree.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.abspath(os.path.join(HERE, ".."))

RULES = [
    ("machine path",
     re.compile(r"C:[\\/]+Users\b|/Users/[A-Za-z]|OneDrive|AlgorithmProject|"
                r"AppData[\\/]+Local[\\/]+Temp", re.I)),
    ("patient pseudonym / label",
     re.compile(r"DOE\^|DOEJOHN|John ?Doe|\bDOE\b|\bCase ?54\b", re.I)),
    ("dataset folder name",
     re.compile(r"Phantom-8tiles|CT 3D printed|DOEJOHNPOSTCT", re.I)),
    ("DICOM header value",
     re.compile(r"ANON\d{3,}|SPIRAL\.STEALTH|STEALTH 1\.0|\bHr40\b|\bGESTA\b|"
                r"Pre-Op\.Dose|2023-10-26|20231026|09:09:16|09:11:14|\b090812\b")),
    ("DICOM UID (vendor / instance)",
     re.compile(r"1\.3\.46\.670589|1\.3\.12\.2\.1107|1\.2\.840\.113619|2\.16\.840\.1\.114362|"
                r"(?<![\d.])(?!1\.2\.840\.10008\b)(?!1\.2\.826\.0\.1\.3680043\b)\d+(?:\.\d+){6,}\b")),
]

# Campaign-owned files (Phase 4 optimizer campaign, still running when the
# Phase 5 scrub landed).  TODO(coordinator): scrub these after the campaign
# merges and delete the allowlist; `--strict` shows what is left.
ALLOWLIST = (
    "scripts/validation_optimize.py",
    "docs/optimize-notes.md",
    "docs/figures/optimize/",
)
SELF_EXCLUDE = ("scripts/check_no_identifiers.py", "tests/test_no_identifiers.py")
SKIP_DIRS = {".git", "output", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
BINARY_EXT = {".npz", ".npy", ".png", ".jpg", ".jpeg", ".pdf", ".pkl", ".pyc", ".ply",
              ".stl", ".nrrd", ".nii", ".gz", ".zip", ".dcm", ".svg", ".ico", ".whl"}


def _rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def list_files(root, use_git=True):
    """Tracked + untracked-not-ignored files (git), else a filtered os.walk."""
    if use_git:
        try:
            out = subprocess.check_output(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=root, stderr=subprocess.DEVNULL)
            names = [n.decode("utf-8", "replace") for n in out.split(b"\0") if n]
            files = [os.path.join(root, n) for n in names]
            return sorted(f for f in files if os.path.isfile(f))
        except (OSError, subprocess.CalledProcessError):
            pass
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        files.extend(os.path.join(dirpath, f) for f in filenames)
    return sorted(files)


def is_text(path):
    if os.path.splitext(path)[1].lower() in BINARY_EXT:
        return False
    try:
        with open(path, "rb") as fh:
            head = fh.read(8192)
    except OSError:
        return False
    return b"\0" not in head


def scan_file(path):
    """Yield (line_no, rule, match) for every forbidden pattern in ``path``."""
    with open(path, "rb") as fh:
        text = fh.read().decode("utf-8", "replace")
    for i, line in enumerate(text.splitlines(), 1):
        for rule, rx in RULES:
            for m in rx.finditer(line):
                yield i, rule, m.group(0)


def check(root, use_git=True, strict=False, allowlist=ALLOWLIST, out=sys.stdout):
    root = os.path.abspath(root)
    failures, warnings = 0, 0
    for path in list_files(root, use_git):
        rel = _rel(path, root)
        if rel in SELF_EXCLUDE or rel.split("/")[0] in SKIP_DIRS or not is_text(path):
            continue
        allowed = (not strict) and any(
            rel == a or (a.endswith("/") and rel.startswith(a)) for a in allowlist)
        for line_no, rule, match in scan_file(path):
            if allowed:
                warnings += 1
                out.write("allowlisted (TODO coordinator, after the campaign merge): "
                          "%s:%d: %s: %s\n" % (rel, line_no, rule, match))
            else:
                failures += 1
                out.write("%s:%d: %s: %s\n" % (rel, line_no, rule, match))
    out.write("check_no_identifiers: %d forbidden hit(s), %d allowlisted\n" % (failures, warnings))
    return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=DEFAULT_ROOT, help="tree to scan (default: the repo)")
    ap.add_argument("--no-git", action="store_true",
                    help="walk the directory instead of asking git for the file list")
    ap.add_argument("--strict", action="store_true",
                    help="fail on the allowlisted (campaign-owned) files as well")
    args = ap.parse_args(argv)
    n = check(args.root, use_git=not args.no_git, strict=args.strict)
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
