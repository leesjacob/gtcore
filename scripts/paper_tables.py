"""Paper 2 tables (Tables 1-4, S3, S4) and caption numbers from the committed
data, with a traceability check (Phase 5, task ``paper-tables``).

Reads ONLY committed data: ``docs/figures/data/*.csv`` (Phase 1-4 real-data
runs), ``docs/figures/optimize/data/*.csv`` + ``*.json`` (the optimizer
campaign), ``docs/figures/manifest.json`` when present, the notes files in
``docs/`` (for numbers that have no CSV: they are cited with the notes
section's commit), the code constants in ``gtcore/`` (Table 2) and one small
hand-maintained input, ``docs/figures/tables/table2_parameters.csv``.  Nothing
is recomputed from images.

Writes ``docs/figures/tables/table{1,2,3,4}.{csv,md}``, ``tableS3_engine_qa``,
``tableS4_optimizer``, ``caption_numbers.{csv,md}`` and
``docs/results-summary.md``.  Every data row carries a ``locator`` (CSV only)
that names where each number comes from::

    csv:<file>?<where (url-encoded)>#<column>@<fmt>   one CSV cell, formatted
    md:<file>#<token>|<token>                          literal tokens in a notes file / CSV header
    code:<file>#<token>|<token>                        literal tokens in a gtcore source file
    pending:<file>                                     the campaign output the row waits for
    fixed:<file>                                       hand-maintained input row

``--check`` rebuilds every table in memory from the committed data, compares
it with the written files (data rows only) and re-evaluates every locator:
the formatted CSV value must appear in the row, every token must be in the
cited file, every cited commit must exist in the repository (when git is
available), and a ``pending`` row's file must still be absent.  It prints a
traceability report and exits non-zero on any problem.

Usage (worktree root)::

    python scripts/paper_tables.py                 # build into docs/figures/tables + docs/results-summary.md
    python scripts/paper_tables.py --check         # traceability report (exit 1 on a problem)
    python scripts/paper_tables.py --out <dir> --summary <file.md> [--check]

Formatting: cGy as integers, Gy to one decimal, fractions as % (one decimal),
mm to two decimals, Dice to three decimals, seconds to one decimal.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import io
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA = "docs/figures/data"
OPT = "docs/figures/optimize/data"
TABLES = "docs/figures/tables"
NOTES = "docs/paper2-phase-notes.md"
DECISIONS = "docs/paper2-decisions.md"
LOCNOTES = "docs/localization-notes.md"
AUTOGEN = "docs/autogen-notes.md"
OPTNOTES = "docs/optimize-notes.md"
MANIFEST = "docs/figures/manifest.json"
TABLE2_INPUT = TABLES + "/table2_parameters.csv"

CLIN = DATA + "/clinical_table3_rows.csv"
GAMMA = DATA + "/gamma_pass_rates.csv"
GAMMA_CAPS = DATA + "/gamma_capsule_diagnostics.csv"
GAMMA_SETS = DATA + "/gamma_seed_sets.csv"
UNC = DATA + "/uncertainty_d90_v100.csv"
SEEDWALL = DATA + "/seed_wall_summary.csv"
ENGINE = DATA + "/engine_crosscheck.csv"
MODES_PT = DATA + "/f4_leftover_modes_PT-CT1mm.csv"
MODES_PH8 = DATA + "/f4_leftover_modes_PH8.csv"
PAIRS_PT = DATA + "/f4_leftover_pairs_PT-CT1mm.csv"
MARGINAL = DATA + "/marginal_score_real.csv"
FORM_HYP = DATA + "/formalism_hypotheses.csv"
FORM_ANG = DATA + "/formalism_angular_ratio.csv"
FORM_SUM = DATA + "/formalism_variants_summary.csv"
FORM_AXES = DATA + "/formalism_axis_comparison.csv"
TABLE4_RUNTIMES = DATA + "/table4_runtimes.csv"          # campaign (scripts/table4_runtimes.py)
F8_SUMMARY = OPT + "/f8_patient_summary.csv"             # campaign (scripts/patient_optimize.py)
F8_ARMS = OPT + "/f8_patient_arms.csv"
CAPACITY = OPT + "/capacity.csv"
V2_SUMMARY = OPT + "/v2_summary.csv"
V2_PRIMARY = OPT + "/v2_primary.csv"
V2_PAIRED = OPT + "/v2_paired.csv"
V3_GAP = OPT + "/v3_gap.csv"
V4_DISC = OPT + "/v4_discretization.csv"
V5_SENS = OPT + "/v5_sensitivity.csv"
V5_RANK = OPT + "/v5_rank_stability.csv"
V6_PHANTOM = OPT + "/v6_phantom_collapsed.csv"
V6_PHANTOM_RAW = OPT + "/v6_phantom.csv"
V6_INFO = OPT + "/v6_info.json"
V6_SWEEP = OPT + "/v6_sweep.csv"
V7_RUNTIME = OPT + "/v7_runtime.csv"
V7_HARDWARE = OPT + "/v7_hardware.json"
V8_FAIL = OPT + "/v8_failure_modes.csv"

HARDWARE_LINE = ("AMD Ryzen 5 7600X, 6 cores / 12 threads, 31 GB RAM, Windows 11 Home; "
                 "Python 3.12.10, numpy 2.5 (shared machine: Phase 1-4 timings are upper bounds)")

FLAG_CAL = "calibration-set"
FLAG_SYN = "synthetic"
FLAG_NA = "n/a"

T3_COLUMNS = ["stage", "dataset", "metric", "value", "truth/reference", "source csv",
              "command", "commit", "flag"]
LOC = "locator"

CMD_CLIN = ('python scripts/validation_clinical.py "$GT_DATA_ROOT/<PT-CT1mm dir>" '
            '"$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>" '
            '--rx 6000 --out docs/figures/data --axes si')


# ============================================================ repository access
class Repo:
    """Read-only view of the repository: cached text / CSV reads, header
    commits, git lookups.  ``root`` defaults to the tree this script lives in."""

    def __init__(self, root: str = ROOT):
        self.root = os.path.abspath(root)
        self._text: Dict[str, str] = {}
        self._csv: Dict[str, Tuple[List[str], List[Dict[str, str]]]] = {}
        self._git_ok: Optional[bool] = None
        self._git_cache: Dict[str, bool] = {}

    def path(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def exists(self, rel: str) -> bool:
        return os.path.isfile(self.path(rel))

    def text(self, rel: str) -> str:
        if rel not in self._text:
            with open(self.path(rel), encoding="utf-8", errors="replace") as fh:
                self._text[rel] = fh.read()
        return self._text[rel]

    def csv(self, rel: str) -> Tuple[List[str], List[Dict[str, str]]]:
        """(header comment lines without the leading '# ', data rows)."""
        if rel not in self._csv:
            header, body = [], []
            with open(self.path(rel), encoding="utf-8", newline="") as fh:
                for line in fh:
                    if line.startswith("#"):
                        header.append(line[1:].strip())
                    else:
                        body.append(line)
            rows = list(csv.DictReader(io.StringIO("".join(body)))) if body else []
            self._csv[rel] = (header, rows)
        return self._csv[rel]

    def csv_commit(self, rel: str) -> Optional[str]:
        """The ``# commit: <hash>`` of a CSV header (first token after 'commit:')."""
        header, _ = self.csv(rel)
        for line in header:
            m = re.search(r"commit:\s*([0-9a-fA-F]{7,40})", line)
            if m:
                return m.group(1)
        return None

    def csv_command(self, rel: str) -> str:
        header, _ = self.csv(rel)
        for line in header:
            m = re.match(r"command:\s*(.*)", line)
            if m:
                return m.group(1).strip()
        return ""

    def json(self, rel: str) -> Any:
        return json.loads(self.text(rel))

    def head(self) -> str:
        try:
            return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=self.root,
                                           stderr=subprocess.DEVNULL).decode().strip()
        except Exception:                                   # noqa: BLE001
            return "unknown"

    def git_available(self) -> bool:
        if self._git_ok is None:
            try:
                subprocess.check_output(["git", "rev-parse", "--git-dir"], cwd=self.root,
                                        stderr=subprocess.DEVNULL)
                self._git_ok = True
            except Exception:                               # noqa: BLE001
                self._git_ok = False
        return self._git_ok

    def git_has(self, commit: str) -> Optional[bool]:
        """True / False when git can answer, None when git is unavailable."""
        if not self.git_available():
            return None
        if commit not in self._git_cache:
            try:
                subprocess.check_output(["git", "cat-file", "-e", commit + "^{commit}"],
                                        cwd=self.root, stderr=subprocess.DEVNULL)
                self._git_cache[commit] = True
            except Exception:                               # noqa: BLE001
                self._git_cache[commit] = False
        return self._git_cache[commit]


# ================================================================= formatting
def _f(v: Any) -> float:
    return float(str(v).strip())


FMT = {
    "cgy": lambda v: "%d" % int(round(_f(v))),               # cGy as integers
    "gy1": lambda v: "%.1f" % (_f(v) / 100.0),               # cGy -> Gy, one decimal
    "pct1": lambda v: "%.1f" % (100.0 * _f(v)),              # fraction -> %
    "pct2": lambda v: "%.2f" % (100.0 * _f(v)),
    "pct1p": lambda v: "%.1f" % _f(v),                       # already a percent
    "pct2p": lambda v: "%.2f" % _f(v),
    "mm1": lambda v: "%.1f" % _f(v),
    "mm2": lambda v: "%.2f" % _f(v),
    "cc1": lambda v: "%.1f" % _f(v),
    "cc2": lambda v: "%.2f" % _f(v),
    "f1": lambda v: "%.1f" % _f(v),
    "f2": lambda v: "%.2f" % _f(v),
    "f3": lambda v: "%.3f" % _f(v),
    "g3": lambda v: "%.3g" % _f(v),
    "int": lambda v: "%d" % int(round(_f(v))),
    "s1": lambda v: "%.1f" % _f(v),
    "pp1": lambda v: "%.1f" % _f(v),
    "str": lambda v: str(v).strip(),
}


def fmt(code: str, v: Any) -> str:
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return "n/a"
    if isinstance(v, str) and v.strip().lower() == "nan":
        return "n/a"
    if isinstance(v, float) and math.isnan(v):
        return "n/a"
    return FMT[code](v)


# =================================================================== lookups
@dataclass
class Lookup:
    """One CSV cell: the unique row of ``path`` matching ``where``, column
    ``column``, formatted with ``fmt``."""
    path: str
    where: Dict[str, str]
    column: str
    fmt: str = "str"

    def encode(self) -> str:
        q = urlencode(sorted(self.where.items())) if self.where else ""
        return "csv:%s%s#%s@%s" % (self.path, ("?" + q) if q else "", self.column, self.fmt)

    @staticmethod
    def decode(s: str) -> "Lookup":
        assert s.startswith("csv:"), s
        body = s[4:]
        path_q, _, col_fmt = body.rpartition("#")
        column, _, code = col_fmt.rpartition("@")
        path, _, q = path_q.partition("?")
        where = dict(parse_qsl(q, keep_blank_values=True)) if q else {}
        return Lookup(path, where, column, code or "str")

    def raw(self, repo: Repo) -> str:
        _, rows = repo.csv(self.path)
        hits = [r for r in rows if all(str(r.get(k, "")).strip() == v for k, v in self.where.items())]
        if len(hits) != 1:
            raise LookupError("%s: %d rows match %s" % (self.path, len(hits), self.where))
        if self.column not in hits[0]:
            raise LookupError("%s: no column %r" % (self.path, self.column))
        return hits[0][self.column]

    def value(self, repo: Repo) -> str:
        return fmt(self.fmt, self.raw(repo))


def L(path: str, where: Dict[str, str], column: str, code: str = "str") -> Lookup:
    return Lookup(path, dict(where), column, code)


def clin(row: str, source: str, metric: str, code: str, structure: Optional[str] = None) -> Lookup:
    where = {"row": row, "dose_source": source, "metric": metric}
    if structure is not None:
        where["structure"] = structure
    return L(CLIN, where, "value", code)


def gam(seed_source: str, axes: str, pct: str, mm: str, norm: str, window: str) -> Lookup:
    return L(GAMMA, {"seed_source": seed_source, "axes": axes, "dose_pct": pct, "dist_mm": mm,
                     "normalisation": norm, "window": window}, "pass_rate_pct", "pct1p")


def unc(band: str, seed_set: str, stat: str, column: str, code: str) -> Lookup:
    return L(UNC, {"band": band, "seed_set": seed_set, "statistic": stat}, column, code)


def wall(dataset: str, seed_set: str, reference: str, method: str, column: str, code: str = "mm2") -> Lookup:
    return L(SEEDWALL, {"dataset": dataset, "seed_set": seed_set, "reference": reference,
                        "method": method}, column, code)


def md_locator(path: str, *tokens: str) -> str:
    return "md:%s#%s" % (path, "|".join(tokens))


def code_locator(path: str, *tokens: str) -> str:
    return "code:%s#%s" % (path, "|".join(tokens))


def split_locator(loc: str) -> List[str]:
    return [p.strip() for p in loc.split(";;") if p.strip()]


# =================================================================== rows
@dataclass
class Row:
    cells: Dict[str, str]
    locator: str = ""

    def text(self) -> str:
        return " ".join(str(v) for v in self.cells.values())


@dataclass
class Table:
    name: str                     # file stem, e.g. "table3"
    title: str
    columns: List[str]
    rows: List[Row] = field(default_factory=list)
    preamble: List[str] = field(default_factory=list)   # markdown lines before the table

    def add(self, row: Row) -> None:
        missing = [c for c in self.columns if c not in row.cells]
        if missing:
            raise ValueError("%s: row lacks %s" % (self.name, missing))
        self.rows.append(row)


class Builder:
    """Builds every table from one ``Repo``; collects which files it read."""

    def __init__(self, repo: Repo):
        self.repo = repo
        self.sources_read: List[str] = []

    # ----------------------------------------------------------- helpers
    def _note(self, rel: str) -> None:
        if rel not in self.sources_read:
            self.sources_read.append(rel)

    def tokens_present(self, rel: str, tokens: Sequence[str]) -> List[str]:
        """Tokens of ``tokens`` missing from file ``rel`` (empty = all present)."""
        if not self.repo.exists(rel):
            return list(tokens)
        txt = self.repo.text(rel)
        return [t for t in tokens if t not in txt]

    def t3_csv(self, stage: str, dataset: str, metric: str, template: str,
               lookups: Sequence[Lookup], truth: str, flag: str, command: Optional[str] = None) -> Row:
        vals = [lk.value(self.repo) for lk in lookups]
        paths = []
        for lk in lookups:
            if lk.path not in paths:
                paths.append(lk.path)
        commits = []
        for p in paths:
            c = self.repo.csv_commit(p) or "?"
            if c not in commits:
                commits.append(c)
            self._note(p)
        cmd = command if command is not None else self.repo.csv_command(paths[0])
        cells = {"stage": stage, "dataset": dataset, "metric": metric,
                 "value": template.format(*vals), "truth/reference": truth,
                 "source csv": ", ".join(paths), "command": cmd,
                 "commit": ", ".join(commits), "flag": flag}
        return Row(cells, " ;; ".join(lk.encode() for lk in lookups))

    def t3_md(self, stage: str, dataset: str, metric: str, value: str, truth: str, flag: str,
              notes: str, commit: str, tokens: Sequence[str], command: str = "",
              extra_locators: Sequence[str] = ()) -> Row:
        missing = self.tokens_present(notes, tokens)
        if missing:
            raise LookupError("%s: tokens not found %s" % (notes, missing))
        self._note(notes)
        locs = [md_locator(notes, *tokens)] + list(extra_locators)
        cells = {"stage": stage, "dataset": dataset, "metric": metric, "value": value,
                 "truth/reference": truth, "source csv": notes + " (notes section)",
                 "command": command, "commit": commit, "flag": flag}
        return Row(cells, " ;; ".join(locs))

    def t3_pending(self, stage: str, dataset: str, metric: str, reason: str, expected: str,
                   truth: str, flag: str, command: str = "") -> Row:
        cells = {"stage": stage, "dataset": dataset, "metric": metric,
                 "value": "pending " + reason, "truth/reference": truth,
                 "source csv": expected + " (expected)", "command": command,
                 "commit": "--", "flag": flag}
        return Row(cells, "pending:" + expected)

    def landed(self, rel: str, need_cols: Sequence[str] = ()) -> bool:
        """A campaign file 'landed' when it exists with a ``# commit:`` header
        (the rev-1 files of 2026-10-07 have no header) and the named columns."""
        if not self.repo.exists(rel):
            return False
        if self.repo.csv_commit(rel) is None:
            return False
        _, rows = self.repo.csv(rel)
        if need_cols and not rows:
            return False
        return all(any(c in r for r in rows[:1]) for c in need_cols)

    def col(self, rows: List[Dict[str, str]], *candidates: str) -> Optional[str]:
        """First column name present among ``candidates`` (regex allowed)."""
        if not rows:
            return None
        keys = list(rows[0].keys())
        for c in candidates:
            if c in keys:
                return c
        for c in candidates:
            for k in keys:
                if re.fullmatch(c, k):
                    return k
        return None

    # ================================================================ Table 1
    def table1(self) -> Table:
        t = Table("table1", "Table 1 -- datasets, acquisition and provenance",
                  ["id", "what", "acquisition", "role in the paper", "provenance / status", "source", "commit"])
        t.preamble = [
            "One clinical patient (dataset ids PT-*), their printed phantom twin (PH8, PH0) and synthetic phantoms. "
            "There is no held-out real scan: every real-data row of Table 3 is calibration-set; the only negative "
            "control is the tile-less phantom. Dataset ids follow figure plan v6 (`PT-CT1mm` = `JD-1mm`, `PT-RT` = `JD RT`).",
        ]
        r = self.repo
        cav = clin("RTSTRUCT", "-", "volume_cc", "cc1", "RTSTRUCT:Cav_Post").value(r)
        ctv = clin("RTSTRUCT", "-", "volume_cc", "cc1", "RTSTRUCT:CTV_GT").value(r)
        brain = clin("RTSTRUCT", "-", "volume_cc", "cc1", "RTSTRUCT:Brain").value(r)
        self._note(CLIN)

        def row(id_, what, acq, role, prov, source, commit, locator):
            t.add(Row({"id": id_, "what": what, "acquisition": acq, "role in the paper": role,
                       "provenance / status": prov, "source": source, "commit": commit}, locator))

        row("PT-preMRI / PT-intraMRI / PT-postMRI",
            "pre-, intra- and post-operative MRI of the one patient (shown, never segmented: no MRI segmentation in gtcore)",
            "three MRI series of the same patient (geometry read from the headers; Jacob to confirm in the Methods)",
            "F3(a) strip (rigidly registered onto PT-CT1mm by scripts/cavity_change.py); the phantom cavity was printed from PT-intraMRI",
            "same patient as the TPS case (decided 2026-11-03: identical acquisition timestamp, table position and slice positions of the CT twin; the MRI registers onto that CT)",
            DECISIONS, "4748ccb",
            md_locator(DECISIONS, "`PT-preMRI`", "`PT-intraMRI`", "`PT-postMRI`", "printed from `JD-MRI-intra`"))
        row("PT-CT1mm (= JD-1mm)",
            "post-operative thin-cut CT, the series the RTSTRUCT references (the clinical case)",
            "204 slices, 0.52 x 0.52 x 1.0 mm, contiguous, Hr40 kernel (series 2)",
            "seed localisation, tile recovery, cavity, every dose comparison (F4-F6, F8a, Table 3)",
            "one clinical case; reference, not validation cohort; every row calibration-set",
            NOTES + "; " + DECISIONS, "bcb7a22",
            md_locator(NOTES, "204 slices", "0.52 × 0.52 × 1.0 mm") + " ;; " + md_locator(DECISIONS, "series 2, 204 slices, Hr40"))
        row("PT-CT-s3 (= JD-CT-s3)",
            "second reconstruction of the same acquisition (series 3, no Hr40 kernel)",
            "178 contiguous 1 mm slices",
            "S2 substitute (ii) for the lost gapped export: seed positions across two reconstructions of one acquisition",
            "identity with PT-CT1mm certain (timestamp to 3 ms, table position, slice positions, seed voxels; slice correlation 0.985)",
            DECISIONS, "4748ccb",
            md_locator(DECISIONS, "178 contiguous 1 mm slices", "slice correlation 0.985"))
        row("PT-CT2mm (= JD-2mm)",
            "gapped export of series 3 (1 mm slabs at 2 mm pitch)",
            "64 slices",
            "S2 real-data panel (2 mm vs 1 mm thin-cut)",
            "MISSING from disk since the 2026-11-03 folder move; the earlier numbers (median 0.32 mm, per-axis SD 0.51 / 0.40 / 0.63 mm) are quoted with their commit and labelled not regenerable from the tag",
            DECISIONS + "; " + LOCNOTES, "5073e01",
            md_locator(DECISIONS, "the 64-slice gapped", "missing from disk since 2026-11-03") + " ;; " + md_locator(LOCNOTES, "0.506 / 0.398 / 0.631", "5073e01"))
        row("PT-RT (= JD RT)",
            "MIM 7.2.5 exports: RTSTRUCT (Cav_Post %s cc, CTV_GT %s cc = HRCTV_PHYS, CTV_Post, Brain %s cc, Seeds 30 contours) and RTDOSE (1 x 1 x 1.667 mm, Gy, total dose to decay; the plan has 32 sources)" % (cav, ctv, brain),
            "RTDOSE 250 x 266 x 266 at 1 x 1 x 1.667 mm, PLAN, no DVHSequence; RTSTRUCT contours at 1 mm pitch",
            "reference for F5 / Table 3 (RTDOSE), target definitions (F6), seed match (F4a)",
            "RTPLAN and the MIM DVH export are NOT on this machine (open items); the RTDOSE 'Pre-Op' label is wrong, it is the implant plan dose",
            CLIN + "; " + NOTES, "378b819",
            clin("RTSTRUCT", "-", "volume_cc", "cc1", "RTSTRUCT:Cav_Post").encode() + " ;; " +
            clin("RTSTRUCT", "-", "volume_cc", "cc1", "RTSTRUCT:CTV_GT").encode() + " ;; " +
            clin("RTSTRUCT", "-", "volume_cc", "cc1", "RTSTRUCT:Brain").encode() + " ;; " +
            md_locator(NOTES, "RTSTRUCT from MIM 7.2.5", "250 × 266 × 266 at 1 × 1 × 1.667 mm"))
        row("PH8",
            "CT of the 3-D-printed phantom with 8 full tiles = 32 seeds placed BY HAND in arbitrary positions (never 'as implanted')",
            "157 slices, 0.59 x 0.59 x 1.0 mm, Philips, O-MAR",
            "F3(c), F4(c), seed-to-wall check, phantom optimizer arms (as placed vs optimized), Table 3 phantom rows",
            "cavity printed from PT-intraMRI (this patient's intra-operative cavity); no STL exists; phantom cavity defined by gtcore's rule on the registered twin: 55.3 cc",
            NOTES + "; " + DECISIONS, "4cd9891",
            md_locator(NOTES, "157 slices, 0.59") + " ;; " + md_locator(DECISIONS, "tiles placed by hand in arbitrary positions", "55.3 cc"))
        row("PH0",
            "CT of the tile-less printed twin",
            "248 slices, 0.68 x 0.68 x 1.0 mm",
            "the only negative control (0 seed candidates, implant absent); the tile-free wall reference for PH8 (rigid registration PH0 -> PH8, Dice 0.979, residual 0.34 mm median)",
            "same print as PH8; the tile-less wall replaces the PH8 body surface (which includes the collagen tiles) as the phantom wall",
            NOTES, "448a6b3",
            md_locator(NOTES, "248 slices", "0.979", "448a6b3"))
        row("Synthetic head phantom",
            "gtcore.phantom.make_head_phantom: cavity, brain, skull, tiles with known seed truth",
            "0.7 / 0.8 mm isotropic, slab-averaged to 1.4 / 2.1 / 2.8 mm for the spacing sweep; 18 cavities (6 seeds x 3 scales) for the optimizer campaign",
            "seed error vs truth, partition / pose, cavity Dice, optimizer benchmark (Table 3 synthetic rows, S4)",
            "ground truth by construction",
            LOCNOTES + "; " + AUTOGEN, "fe4a1a8",
            md_locator(LOCNOTES, "make_head_phantom(spacing=0.7, n_tiles=3)", "fe4a1a8"))
        row("Not on this machine",
            "RTPLAN, MIM DVH / plan export, TG-43U1S2 erratum text, printed STL, Artec / TRIOS meshes, phantom MRI",
            "--",
            "RTPLAN: per-seed S_K and orientations (expected along S-I); MIM export: structure-volume / D90 check (verify_against_tps); STL: F9(b) dropped",
            "open items; the code paths exist and are tested on synthetic inputs, the real-data step is a documented command",
            DECISIONS, "4748ccb",
            md_locator(DECISIONS, "No printed-phantom STL exists"))
        row("IRB / consent",
            "retrospective single-case use of one patient's imaging and plan; the phantom carries no PHI",
            "--",
            "Methods statement",
            "PLACEHOLDER -- Jacob supplies the IRB / consent wording",
            "--", "--", "fixed:" + DECISIONS)
        return t

    # ================================================================ Table 2
    def table2(self) -> Table:
        t = Table("table2", "Table 2 -- parameters, justification and calibration set",
                  ["parameter", "value", "unit", "role", "justification", "calibration set", "check", "source", "commit"])
        t.preamble = [
            "Nothing was re-tuned on the clinical case (PT-CT1mm + PT-RT) or on the phantom; model changes were validated "
            "synthetic + phantom first. `calibration set` names the data a value was chosen on; `check` is what the value "
            "produces on the data it was NOT tuned on. Hand-maintained input: `docs/figures/tables/table2_parameters.csv`.",
        ]
        if not self.repo.exists(TABLE2_INPUT):
            raise FileNotFoundError(TABLE2_INPUT)
        _, rows = self.repo.csv(TABLE2_INPUT)
        self._note(TABLE2_INPUT)
        for r in rows:
            cells = {"parameter": r["parameter"], "value": r["value"], "unit": r["unit"], "role": r["role"],
                     "justification": r["justification"], "calibration set": r["calibration_set"],
                     "check": r["check"], "source": r["source"], "commit": r["commit"]}
            loc = r.get("locator", "").strip() or ("fixed:" + TABLE2_INPUT)
            # every locator part of a hand-maintained row is verified at build time too
            for part in split_locator(loc):
                self._verify_part(part, " ".join(cells.values()), cells["commit"])
            t.add(Row(cells, loc))
        return t

    # ================================================================ Table 3
    def table3(self) -> Table:
        t = Table("table3", "Table 3 -- accuracy per stage and dataset (every row flagged)", T3_COLUMNS)
        t.preamble = [
            "flag: `calibration-set` = measured on the one clinical case or its printed twin (reference, not validation cohort; "
            "no parameter was tuned on it); `synthetic` = ground truth by construction; `n/a` = published tables / negative "
            "control / convention. rx 6000 cGy, S_K 3.5 U nominal for every seed; dose statistics = `dose_stats` (type-1 "
            "quantile, >= rule) on the CT grid (0.52 x 0.52 x 1.0 mm). `(S-I axes)` = every seed axis along the patient "
            "superior-inferior direction, the TPS convention found by the formalism check (equivalence row); unsuffixed "
            "gtcore rows use the physical (PCA) seed axes (the pipeline result). Every dose row vs the RTDOSE still carries "
            "`TPS volume check pending` (MIM export not on this machine).",
        ]
        R = self.repo
        add = t.add

        # ---------------------------------------------------------- localization (synthetic)
        add(self.t3_md("seed localisation", "synthetic head phantom (0.7 mm, 3 tiles, rng 0-4)",
                       "3-D seed error vs truth, analytic capsules (binary capsules)",
                       "mean 0.10 +/- 0.08 mm, P95 0.25, max 0.44 mm; recall 1.00 (binary phantom: 0.20 +/- 0.16 mm, P95 0.50, max 0.67)",
                       "generator truth (Hungarian match < 2 mm)", FLAG_SYN, LOCNOTES, "fe4a1a8",
                       ["0.10 ± 0.08", "0.25", "0.44", "0.20 ± 0.16", "fe4a1a8"],
                       "python scripts/validation_spacing.py --refine none --realizations 5 [--seed-render analytic]"))
        add(self.t3_md("seed localisation", "synthetic head phantom, slab-averaged to 1.4 / 2.1 / 2.8 mm",
                       "recall and 3-D error vs slice spacing (binary capsules; adaptive detection)",
                       "recall 1.00 / 1.00 / 0.97 / 0.98 at 0.7 / 1.4 / 2.1 / 2.8 mm; mean error 0.20 / 0.29 / 0.50 / 0.70 mm; z RMS 0.09 / 0.18 / 0.39 / 0.62 mm",
                       "generator truth", FLAG_SYN, LOCNOTES, "fe4a1a8",
                       ["| 1.4 | 1.00 / 0.88 | 60 | 0.29 ± 0.20", "| 2.1 | 0.97 / 0.17 | 58 | 0.50 ± 0.26", "| 2.8 | 0.98 / 0.00 | 59 | 0.70 ± 0.36", "fe4a1a8"],
                       "python scripts/validation_spacing.py --refine none --realizations 5"))
        add(self.t3_md("tile partition / pose", "synthetic sweep (rng 0-5 x 1-5 tiles x 0.8 / 1.2 mm), no count input",
                       "tile count and exact partition; tile centre / normal error",
                       "0.8 mm: count 30 / 30, partition 30 / 30, centre 0.10 / 0.22 mm (mean / max), normal 1.7 deg; 1.2 mm: 24 / 30 (all six misses are seed-detection misses, 11 of 12 seeds), centre 0.13 / 0.27 mm, normal 3.2 deg",
                       "generator truth", FLAG_SYN, AUTOGEN, "da71f76",
                       ["30 / 30", "24 / 30", "0.10 / 0.22 mm", "0.13 / 0.27 mm", "1.7°", "3.2°"],
                       "python scripts/validation_autogen.py"))
        add(self.t3_md("tile partition / pose", "synthetic head phantom (counted fit, 3 tiles), slab-averaged",
                       "partition success and tile pose vs slice spacing (binary capsules)",
                       "partition 5/5 / 4/5 / 2/5 / 2/5 at 0.7 / 1.4 / 2.1 / 2.8 mm; tile centre error 0.11 / 0.15 / 0.19 / 0.35 mm; normal error about 7 deg (generator floor: the truth normal is the radial direction)",
                       "generator truth", FLAG_SYN, LOCNOTES, "fe4a1a8",
                       ["| 5/5 | 0.11 | 7.0 |", "| 4/5 | 0.15 | 7.0 |", "| 2/5 | 0.19 | 7.6 |", "| 2/5 | 0.35 | 6.5 |"],
                       "python scripts/validation_spacing.py --refine none --realizations 5"))
        # ---------------------------------------------------------- localization (real, no truth)
        add(self.t3_md("seed localisation", "PT-CT2mm vs PT-CT1mm (same acquisition; 25 matched seeds, no transform)",
                       "2 mm gapped export vs 1 mm thin-cut: per-axis SD and 3-D difference",
                       "SD i / j / k 0.51 / 0.40 / 0.63 mm; 3-D median 0.32 mm, mean 0.63, P95 1.80, max 2.41 mm (the thin-cut is a measurement, not truth; PT-CT2mm not on disk: not regenerable from the tag)",
                       "PT-CT1mm seeds (reference measurement)", FLAG_CAL, LOCNOTES, "5073e01",
                       ["0.506 / 0.398 / 0.631", "| 0.63 | 0.32 | 1.80 | 2.41 |", "5073e01"],
                       "python scripts/localization_proxies_diag.py --part thincut"))
        add(self.t3_md("seed localisation", "PH8 split halves (even / odd 1 mm slices as two 2 mm scans, 28 common seeds)",
                       "split-half disagreement / sqrt(2)",
                       "3-D 1.01 mm (i / j / k 0.756 / 0.352 / 0.576 mm); vs the full 1 mm result: even 0.74 +/- 0.59, odd 0.49 +/- 0.48 mm",
                       "no truth (internal consistency)", FLAG_CAL, LOCNOTES, "5073e01",
                       ["0.756 / 0.352 / 0.576", "**1.01 mm**", "even 0.74 ± 0.59", "odd 0.49 ± 0.48"],
                       "python scripts/validation_realdata_proxies.py --refine none --no-cache"))
        # ---------------------------------------------------------- implant recovery
        add(self.t3_csv("implant recovery", "PH8", "seeds and tiles recovered (count-free)",
                        "{0} tiles ({1} supported, {2} degraded = the crumpled tile), {3}/32 seeds assigned, {4} leftover",
                        [L(MODES_PH8, {"mode": "auto"}, "n_tiles", "int"), L(MODES_PH8, {"mode": "auto"}, "n_supported", "int"),
                         L(MODES_PH8, {"mode": "auto"}, "n_degraded", "int"), L(MODES_PH8, {"mode": "auto"}, "n_assigned", "int"),
                         L(MODES_PH8, {"mode": "auto"}, "n_leftover", "int")],
                        "8 tiles / 32 seeds placed by hand (count known); 32 raw blobs = 32 seeds", FLAG_CAL))
        add(self.t3_csv("implant recovery", "PT-CT1mm + PT-RT", "seeds vs the TPS: contoured seeds and plan sources",
                        "{0} contoured seeds matched one-to-one (2 mm gate): median {1} mm, p95 {2}, max {3} mm; {4} plan sources (RTDOSE maxima) matched (3 mm gate): median {5} mm; {6} detections, 0 false detections",
                        [clin("seed match", "-", "n_matched_of_contoured", "str", "Seeds (contoured) vs gtcore"),
                         clin("seed match", "-", "match_median_mm", "mm2", "Seeds (contoured) vs gtcore"),
                         L(GAMMA_SETS, {"seed_set": "gtcore"}, "vs_tps_contours_p95_mm", "mm2"),
                         clin("seed match", "-", "match_max_mm", "mm2", "Seeds (contoured) vs gtcore"),
                         clin("seed match", "-", "n_matched_of_sources", "str", "plan sources (RTDOSE maxima) vs gtcore"),
                         clin("seed match", "-", "source_median_mm", "mm2", "plan sources (RTDOSE maxima) vs gtcore"),
                         clin("seed match", "-", "n_detected", "str", "gtcore detections")],
                        "RTSTRUCT Seeds (30 contours) and the 32 RTDOSE maxima > 200 Gy (grid-quantised, 1 x 1 x 1.667 mm)", FLAG_CAL))
        add(self.t3_csv("implant recovery", "PT-CT1mm", "tiles, count-free (the pipeline result; F4b primary)",
                        "{0}/8 tiles, {1}/32 seeds assigned, {2} leftover seeds = four 2-seed pairs at the manufactured pitch ({3}-{4} mm), all TPS-contoured intact capsules",
                        [L(MODES_PT, {"mode": "auto"}, "n_tiles", "int"), L(MODES_PT, {"mode": "auto"}, "n_assigned", "int"),
                         L(MODES_PT, {"mode": "auto"}, "n_leftover", "int"),
                         L(PAIRS_PT, {"seeds": "11;21"}, "dist_mm", "mm1"), L(PAIRS_PT, {"seeds": "2;5"}, "dist_mm", "mm1")],
                        "operative count 8 full tiles (brief); the geometry says 6 full + 4 half", FLAG_CAL))
        add(self.t3_csv("implant recovery", "PT-CT1mm", "tiles, count-known N = 8 full (planner prior)",
                        "{0}/8 tiles ({1} degraded completion = two abutting half-tile pairs read as a crumpled tile), {2}/32 assigned, {3} leftover",
                        [L(MODES_PT, {"mode": "prior8"}, "n_tiles", "int"), L(MODES_PT, {"mode": "prior8"}, "n_degraded", "int"),
                         L(MODES_PT, {"mode": "prior8"}, "n_assigned", "int"), L(MODES_PT, {"mode": "prior8"}, "n_leftover", "int")],
                        "N = 8 full tiles as an input", FLAG_CAL))
        add(self.t3_csv("implant recovery", "PT-CT1mm", "tiles, count-known 6 full + 4 half (planner prior) -- PENDING OPERATIVE RECORD",
                        "{0} tiles (6 full + 4 half), {1} degraded, {2}/32 assigned, {3} leftover; the half tiles are cut along the seed axes (template orientation limitation noted)",
                        [L(MODES_PT, {"mode": "count_6full_4half"}, "n_tiles", "int"), L(MODES_PT, {"mode": "count_6full_4half"}, "n_degraded", "int"),
                         L(MODES_PT, {"mode": "count_6full_4half"}, "n_assigned", "int"), L(MODES_PT, {"mode": "count_6full_4half"}, "n_leftover", "int")],
                        "ImplantPrior(n_full=6, n_half=4) as an input; which count-known row the paper quotes waits for the operative record", FLAG_CAL))
        add(self.t3_csv("implant recovery", "PH8, PT-CT1mm", "marginal bent-tile score vs tile count (count-free search)",
                        "PH8 selects n = {0} (curve ends at 8: no 9th candidate); PT-CT1mm selects n = {1} (no 7th candidate); in neither scan does the marginal fall below lambda = {2} -- the plateau is a termination, not a lambda crossing",
                        [L(MARGINAL, {"dataset": "PH8", "n": "8"}, "n_selected", "int"),
                         L(MARGINAL, {"dataset": "PT-CT1mm", "n": "6"}, "n_selected", "int"),
                         L(MARGINAL, {"dataset": "PT-CT1mm", "n": "6"}, "lambda_full", "f1")],
                        "implanted counts 8 (PH8) and 8 full = 32 seeds (PT-CT1mm, brief)", FLAG_CAL))
        add(self.t3_md("implant recovery", "PH0 (tile-less twin)", "negative control",
                       "0 raw blobs, 0 seed candidates, implant absent (vault filter fails open: no skull)",
                       "no seeds present (the only negative control; there is no patient negative control)", FLAG_NA, NOTES, "fad8701",
                       ["0 raw blobs, 0 seed candidates", "fad8701"],
                       "python scripts/validation_realdata_proxies.py --scans negatives,printed8"))
        # ---------------------------------------------------------- cavity
        add(self.t3_md("cavity segmentation", "synthetic head phantom (0.7 mm, truth cavity 26.3 cc)",
                       "cavity Dice vs truth at HULL_REACH_MM = 14 (mm-correct rule)",
                       "Dice 0.896, volume 26.9 cc (was 0.839 / 22.8 cc with the voxel-step rule; 12 / 16 / 20 mm: 0.873 / 0.920 / 0.903, not re-tuned)",
                       "generator truth cavity", FLAG_SYN, NOTES, "0efe5eb",
                       ["0.896", "26.9 cc", "26.3 cc", "0efe5eb"],
                       "python scripts/cavity_reach_sweep.py --reaches 12,14,16,20"))
        add(self.t3_csv("cavity segmentation", "PT-CT1mm + PT-RT", "gtcore cavity vs the physician's Cav_Post",
                        "gtcore cavity {0} cc vs Cav_Post {1} cc: Dice {2} (covers 72 % of Cav_Post; the uncovered 28 % is clot / debris above 26 HU; 0.663 at 0efe5eb before the Phase 2 rasteriser)",
                        [clin("gtcore cavity", "-", "volume_cc", "cc1", "gtcore:cavity"),
                         clin("RTSTRUCT", "-", "volume_cc", "cc1", "RTSTRUCT:Cav_Post"),
                         clin("gtcore cavity", "-", "dice", "f3", "gtcore:cavity vs RTSTRUCT:Cav_Post")],
                        "RTSTRUCT Cav_Post (physician's post-operative cavity, drawn through tiles, fluid and debris)", FLAG_CAL))
        add(self.t3_md("cavity segmentation", "PH8 + PH0", "phantom cavity (cavity at print time)",
                       "no implant-independent lumen exists (mouth as wide as the craniotomy: 36-118 mL over 6-28 mm closing, no plateau); gtcore's cavity rule on the registered twin: 55.3 cc, 32/32 seeds inside; RIND5(phantom cavity, clip PH0 shell) 47.72 cc",
                       "no STL; the printed geometry is this patient's intra-operative cavity (PT-intraMRI)", FLAG_CAL, NOTES, "4cd9891",
                       ["55.3 cc", "32/32 seeds inside", "47.72 cc", "4cd9891"],
                       'python -m gtcore.cli optimize "$GT_DATA_ROOT/<PH8>" --phantom-ref "$GT_DATA_ROOT/<PH0>" ...'))
        add(self.t3_csv("target definition", "PT-CT1mm + PT-RT", "structure volumes and Dice vs CTV_GT (HRCTV_PHYS)",
                        "CTV_GT {0} cc; RIND5(Cav_Post, clip Brain) {1} cc, Dice {2} (CTV_GT covers 53 % of the full rind); RIND5(gtcore cavity, clip cranial interior) {3} cc, Dice {4}; IMPLANT_REACH(r = 10 mm) {5} cc, Dice {6}; IMPLANT_REACH(r = 12 mm) {7} cc, Dice {8}",
                        [clin("HRCTV_PHYS (CTV_GT)", "-", "volume_cc", "cc1"),
                         clin("RIND5(Cav_Post, clip Brain)", "-", "volume_cc", "cc2"), clin("RIND5(Cav_Post, clip Brain)", "-", "dice_vs_CTV_GT", "f3"),
                         clin("RIND5(gtcore cavity, clip cranial interior)", "-", "volume_cc", "cc2"), clin("RIND5(gtcore cavity, clip cranial interior)", "-", "dice_vs_CTV_GT", "f3"),
                         clin("IMPLANT_REACH(r=10 mm)", "-", "volume_cc", "cc2"), clin("IMPLANT_REACH(r=10 mm)", "-", "dice_vs_CTV_GT", "f3"),
                         clin("IMPLANT_REACH(r=12 mm)", "-", "volume_cc", "cc2"), clin("IMPLANT_REACH(r=12 mm)", "-", "dice_vs_CTV_GT", "f3")],
                        "RTSTRUCT CTV_GT (hand-trimmed partial rind); r = 10 mm calibrated on this case (Dice)", FLAG_CAL))
        # ---------------------------------------------------------- dose on HRCTV_PHYS
        H = "HRCTV_PHYS (CTV_GT)"
        for src, label in [("RTDOSE", "RTDOSE (TPS)"),
                           ("gtcore@32 TPS sources", "gtcore @ 32 TPS sources, physical axes"),
                           ("gtcore@32 TPS sources (S-I axes)", "gtcore @ 32 TPS sources (S-I axes = TPS convention)"),
                           ("gtcore@32 gtcore seeds", "gtcore @ 32 gtcore seeds, physical axes (pipeline result)"),
                           ("gtcore@32 gtcore seeds (S-I axes)", "gtcore @ 32 gtcore seeds (S-I axes = TPS convention)"),
                           ("gtcore@30 contoured seeds", "gtcore @ 30 contoured seeds (the two uncontoured sources dropped)")]:
            add(self.t3_csv("dose vs TPS", "PT-CT1mm + PT-RT", "HRCTV_PHYS (CTV_GT) D90 / V100 / V150: " + label,
                            "D90 {0} cGy ({1} Gy) / V100 {2} % / V150 {3} %",
                            [clin(H, src, "D90", "cgy"), clin(H, src, "D90", "gy1"), clin(H, src, "V100", "pct1"), clin(H, src, "V150", "pct1")],
                            "RTDOSE sampled trilinearly at the CT voxel centres; TPS volume check pending", FLAG_CAL))
        add(self.t3_csv("target definition", "PT-CT1mm + PT-RT", "RTDOSE D90 / V100 by structure (same dose, different rind; F6 caption)",
                        "CTV_GT {0} cGy / {1} %; RIND5(Cav_Post, clip Brain) {2} cGy / {3} %; RIND5(gtcore cavity) {4} cGy / {5} %; IMPLANT_REACH(10) {6} cGy / {7} %; IMPLANT_REACH(12) {8} cGy / {9} %",
                        [clin(H, "RTDOSE", "D90", "cgy"), clin(H, "RTDOSE", "V100", "pct1"),
                         clin("RIND5(Cav_Post, clip Brain)", "RTDOSE", "D90", "cgy"), clin("RIND5(Cav_Post, clip Brain)", "RTDOSE", "V100", "pct1"),
                         clin("RIND5(gtcore cavity, clip cranial interior)", "RTDOSE", "D90", "cgy"), clin("RIND5(gtcore cavity, clip cranial interior)", "RTDOSE", "V100", "pct1"),
                         clin("IMPLANT_REACH(r=10 mm)", "RTDOSE", "D90", "cgy"), clin("IMPLANT_REACH(r=10 mm)", "RTDOSE", "V100", "pct1"),
                         clin("IMPLANT_REACH(r=12 mm)", "RTDOSE", "D90", "cgy"), clin("IMPLANT_REACH(r=12 mm)", "RTDOSE", "V100", "pct1")],
                        "the clinical RTDOSE on each target (structure choice moves D90 by a factor 2.3, the dose source by <= 3 %)", FLAG_CAL))
        add(self.t3_csv("target definition", "PT-CT1mm", "gtcore @ 32 gtcore seeds D90 / V100 by structure (F8a titles: as implanted)",
                        "CTV_GT {0} cGy / {1} %; RIND5(Cav_Post, clip Brain) {2} cGy / {3} %; RIND5(gtcore cavity, clip cranial interior) {4} cGy / {5} %; IMPLANT_REACH(10) {6} cGy / {7} %",
                        [clin(H, "gtcore@32 gtcore seeds", "D90", "cgy"), clin(H, "gtcore@32 gtcore seeds", "V100", "pct1"),
                         clin("RIND5(Cav_Post, clip Brain)", "gtcore@32 gtcore seeds", "D90", "cgy"), clin("RIND5(Cav_Post, clip Brain)", "gtcore@32 gtcore seeds", "V100", "pct1"),
                         clin("RIND5(gtcore cavity, clip cranial interior)", "gtcore@32 gtcore seeds", "D90", "cgy"), clin("RIND5(gtcore cavity, clip cranial interior)", "gtcore@32 gtcore seeds", "V100", "pct1"),
                         clin("IMPLANT_REACH(r=10 mm)", "gtcore@32 gtcore seeds", "D90", "cgy"), clin("IMPLANT_REACH(r=10 mm)", "gtcore@32 gtcore seeds", "V100", "pct1")],
                        "the implant as recovered (32 seeds, physical axes, S_K 3.5 U)", FLAG_CAL))
        # ---------------------------------------------------------- gamma
        for src, label, flag_note in [("gtcore", "gtcore seeds (pipeline result)", ""),
                                      ("tps_contours", "TPS contour positions + 2 maxima (engine-equivalence reference)", "")]:
            for axes, alabel in [("physical", "physical axes"), ("si", "S-I axes (TPS convention)")]:
                add(self.t3_csv("dose vs TPS (gamma)", "PT-CT1mm + PT-RT",
                                "gamma pass rate vs RTDOSE, %s, %s: 3 %%/2 mm global (local); 2 %%/1 mm global (local); 30-200 Gy, >= 30 Gy in brackets" % (label, alabel),
                                "3 %/2 mm: {0} % ({1}) global, {2} % ({3}) local; 2 %/1 mm: {4} % ({5}) global, {6} % ({7}) local",
                                [gam(src, axes, "3", "2", "global", "30-200Gy"), gam(src, axes, "3", "2", "global", "ge30Gy"),
                                 gam(src, axes, "3", "2", "local", "30-200Gy"), gam(src, axes, "3", "2", "local", "ge30Gy"),
                                 gam(src, axes, "2", "1", "global", "30-200Gy"), gam(src, axes, "2", "1", "global", "ge30Gy"),
                                 gam(src, axes, "2", "1", "local", "30-200Gy"), gam(src, axes, "2", "1", "local", "ge30Gy")],
                                "RTDOSE voxels >= 30 Gy (89 639; 81 527 in 30-200 Gy); pymedphys 0.41.0", FLAG_CAL))
        add(self.t3_csv("dose vs TPS (gamma)", "PT-CT1mm + PT-RT", "reference voxels above 200 Gy (excluded from the primary window)",
                        "{0} % of the >= 30 Gy voxels; distance to the nearest gtcore seed median {1} mm, p95 {2} mm, max {3} mm (the tiles' seed planes, not 'within ~3 mm of a seed'); RTDOSE {4} Gy at its hottest voxel vs gtcore {5} Gy there",
                        [L(GAMMA_CAPS, {}, "fraction_above_200", "pct2"), L(GAMMA_CAPS, {}, "dist_gtcore_median_mm", "mm2"),
                         L(GAMMA_CAPS, {}, "dist_gtcore_p95_mm", "mm2"), L(GAMMA_CAPS, {}, "dist_gtcore_max_mm", "mm2"),
                         L(GAMMA_CAPS, {}, "rtdose_max_gy", "int"), L(GAMMA_CAPS, {}, "gtcore_at_rtdose_argmax_gy", "int")],
                        "RTDOSE grid 1 x 1 x 1.667 mm (volume-averaged near the seeds)", FLAG_CAL))
        # ---------------------------------------------------------- MC uncertainty
        for band, blabel in [("residual", "primary band (resampled measured residuals)"),
                             ("degraded_export", "degraded-export band (per-axis SD 0.51 / 0.40 / 0.63 mm)")]:
            add(self.t3_csv("dose uncertainty (MC, 1000 draws)", "PT-CT1mm + PT-RT",
                            "HRCTV_PHYS, gtcore @ 32 gtcore seeds: %s" % blabel,
                            "D90 nominal {0} cGy, mean {1} +/- {2} cGy, 95 % [{3}, {4}] cGy; V100 nominal {5} %, 95 % [{6}, {7}] %; V150 95 % [{8}, {9}] %",
                            [unc(band, "gtcore32", "D90", "nominal", "cgy"), unc(band, "gtcore32", "D90", "mean", "cgy"), unc(band, "gtcore32", "D90", "sd", "cgy"),
                             unc(band, "gtcore32", "D90", "p2.5", "cgy"), unc(band, "gtcore32", "D90", "p97.5", "cgy"),
                             unc(band, "gtcore32", "V100", "nominal", "pct1"), unc(band, "gtcore32", "V100", "p2.5", "pct1"), unc(band, "gtcore32", "V100", "p97.5", "pct1"),
                             unc(band, "gtcore32", "V150", "p2.5", "pct1"), unc(band, "gtcore32", "V150", "p97.5", "pct1")],
                            "seed-position error only (axes and S_K fixed); both bands conservative; RTDOSE 6365 cGy lies outside both", FLAG_CAL))
        add(self.t3_csv("dose uncertainty (MC, 1000 draws)", "PT-CT1mm + PT-RT",
                        "HRCTV_PHYS, 30-seed sets, primary band: TPS-contoured positions vs the 30 matched gtcore seeds",
                        "contoured30 D90 {0} cGy [{1}, {2}], V100 {3} %; gtcore30 D90 {4} cGy [{5}, {6}], V100 {7} % -- the 377 cGy drop from 32 to 30 seeds is the two uncontoured sources, not localisation",
                        [unc("residual", "contoured30", "D90", "nominal", "cgy"), unc("residual", "contoured30", "D90", "p2.5", "cgy"), unc("residual", "contoured30", "D90", "p97.5", "cgy"),
                         unc("residual", "contoured30", "V100", "nominal", "pct1"),
                         unc("residual", "gtcore30", "D90", "nominal", "cgy"), unc("residual", "gtcore30", "D90", "p2.5", "cgy"), unc("residual", "gtcore30", "D90", "p97.5", "cgy"),
                         unc("residual", "gtcore30", "V100", "nominal", "pct1")],
                        "same target and engine; 30 vs 32 sources", FLAG_CAL))
        # ---------------------------------------------------------- formalism, S_K, delivered fraction
        add(self.t3_csv("formalism (TPS)", "PT-RT RTDOSE vs gtcore @ 32 sources", "angular-symmetry test: RTDOSE / gtcore-2D vs angle to the seed axis (3-15 mm single-seed voxels)",
                        "physical (PCA) axes: {0} (0-15 deg) -> {1} (75-90 deg), RMS log {2}; all axes along S-I after a sub-voxel position fit: {3} -> {4}, RMS log {5} (free per-seed axes {6}); 1D point source rejected: RMS {7}. MIM = 2D line source with every seed axis along S-I (physical axes 64.5 deg from S-I in median; common fitted axis 2.2 deg from S-I)",
                        [L(FORM_ANG, {"variant": "baseline", "bin_lo_deg": "0"}, "mean", "f2"), L(FORM_ANG, {"variant": "baseline", "bin_lo_deg": "75"}, "mean", "f2"),
                         L(FORM_HYP, {"hypothesis": "H1_2D_pca_axes"}, "rms_log_median", "f3"),
                         L(FORM_ANG, {"variant": "H3_2D_axes_z_refined", "bin_lo_deg": "0"}, "mean", "f2"), L(FORM_ANG, {"variant": "H3_2D_axes_z_refined", "bin_lo_deg": "75"}, "mean", "f2"),
                         L(FORM_HYP, {"hypothesis": "H3_2D_axes_z"}, "rms_log_median", "f3"), L(FORM_HYP, {"hypothesis": "H4_2D_fitted_axes"}, "rms_log_median", "f3"),
                         L(FORM_HYP, {"hypothesis": "H2_1D"}, "rms_log_median", "f3")],
                        "the RTDOSE itself (which formalism / axis convention MIM used); RTPLAN pending", FLAG_CAL))
        self._note(FORM_AXES)
        add(self.t3_csv("formalism (TPS)", "PT-CT1mm + PT-RT", "implied S_K (diagnostic, never used) and delivered fraction",
                        "TG-43 (3.5 U) / RTDOSE at CTV_GT voxels > 5 mm from any source: median {0} -> implied S_K {1} U (physical axes); {2} -> {3} U (S-I axes); the RTDOSE is the total dose to decay (no delivered-fraction correction)",
                        [clin("diagnostic", "RTDOSE / gtcore@32 TPS sources", "ratio_median_far_from_sources", "f3"),
                         clin("diagnostic", "gtcore@32 TPS sources / RTDOSE", "implied_SK_not_used", "f2"),
                         clin("diagnostic", "RTDOSE / gtcore@32 TPS sources (S-I axes)", "ratio_median_far_from_sources", "f3"),
                         clin("diagnostic", "gtcore@32 TPS sources (S-I axes) / RTDOSE", "implied_SK_not_used", "f2")],
                        "nominal 3.5 U per seed stays (RTPLAN per-seed S_K pending)", FLAG_CAL))
        # ---------------------------------------------------------- seed-to-wall
        add(self.t3_csv("seed-plane model (3.0 mm)", "PT-CT1mm + PT-RT", "signed seed-to-Cav_Post distance (positive = cavity side; EDT estimator)",
                        "32 gtcore seeds: median +{0} mm (10-90 % {1}-{2}), {3}/32 in the hydrated band 2.25-3.75 mm, {4} seed outside the contour; 32 TPS sources: median +{5} mm ({6}-{7}); mesh cross-check +{8} / +{9} mm",
                        [wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "edt", "median"), wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "edt", "p10"), wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "edt", "p90"),
                         wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "edt", "n_in_band", "int"), wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "edt", "n_negative", "int"),
                         wall("PT-CT1mm+PT-RT", "tps_sources", "Cav_Post", "edt", "median"), wall("PT-CT1mm+PT-RT", "tps_sources", "Cav_Post", "edt", "p10"), wall("PT-CT1mm+PT-RT", "tps_sources", "Cav_Post", "edt", "p90"),
                         wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "mesh", "median"), wall("PT-CT1mm+PT-RT", "tps_sources", "Cav_Post", "mesh", "median")],
                        "SEED_PLANE_OFFSET_MM 3.0 (hydrated 2.25-3.75), not re-tuned; the v6 mock's 4.6 mm is superseded", FLAG_CAL))
        add(self.t3_csv("seed-plane model (3.0 mm)", "PH8 + PH0", "signed seed-to-wall distance on the phantom",
                        "vs the registered tile-less twin's printed wall: median +{0} mm (10-90 % {1}-{2}), {3}/32 in band, 0 negative; vs the PH8 body surface (tiles included): {4} mm -- a reference-surface artifact (the tile's free face), not a placement error; registration residual 0.34 mm median",
                        [wall("PH8+PH0", "gtcore_seeds", "PH0_wall", "edt", "median"), wall("PH8+PH0", "gtcore_seeds", "PH0_wall", "edt", "p10"), wall("PH8+PH0", "gtcore_seeds", "PH0_wall", "edt", "p90"),
                         wall("PH8+PH0", "gtcore_seeds", "PH0_wall", "edt", "n_in_band", "int"), wall("PH8", "gtcore_seeds", "PH8_body", "edt", "median")],
                        "PH0 body mask carried into PH8 by rigid registration (Dice 0.979)", FLAG_CAL))
        # ---------------------------------------------------------- engine QA
        add(self.t3_csv("dose engine QA", "TG-43U1S2 QA tables (CS-1 Rev2), Paper 1 engine", "TG43Engine('tg43u1s2') vs the published QA tables and Paper 1's engine",
                        "Table XII (erratum, 143 points): max {0} %, median {1} %; Table XXIII (along-away, 2017 print, 149 points): max {2} %, median {3} %; vs Paper 1 at the table nodes {4} % (one transcription slip, F(7 cm, 80 deg) 0.999 vs 0.998), between g_L nodes <= {5} % (linear vs log-linear g_L), 32-seed cloud >= 3 mm from the seeds <= {6} % (median {7} %)",
                        [L(ENGINE, {"section": "tableXII", "row": "gtcore exact vs Table XII (erratum)"}, "max_abs_rel_pct", "f2"),
                         L(ENGINE, {"section": "tableXII", "row": "gtcore exact vs Table XII (erratum)"}, "median_abs_rel_pct", "f3"),
                         L(ENGINE, {"section": "tableXXIII", "row": "gtcore exact vs Table XXIII (along-away, 2017 print)"}, "max_abs_rel_pct", "f2"),
                         L(ENGINE, {"section": "tableXXIII", "row": "gtcore exact vs Table XXIII (along-away, 2017 print)"}, "median_abs_rel_pct", "f3"),
                         L(ENGINE, {"section": "tableXII", "row": "gtcore vs Paper 1 at the 143 Table XII nodes"}, "max_abs_rel_pct", "f3"),
                         L(ENGINE, {"section": "grid", "row": "between g_L nodes"}, "max_abs_rel_pct", "f2"),
                         L(ENGINE, {"section": "cloud", "row": "summed rate per U, points >= 3 mm from every seed"}, "max_abs_rel_pct", "f2"),
                         L(ENGINE, {"section": "cloud", "row": "summed rate per U, points >= 3 mm from every seed"}, "median_abs_rel_pct", "f2")],
                        "published consensus / QA tables (Rivard et al. 2017, erratum 2018 via Paper 1's transcription); synthetic geometry only", FLAG_NA))
        # ---------------------------------------------------------- cross-grid volume, TPS check
        add(self.t3_md("structure rasterisation", "PT-RT RTSTRUCT on the CT grid vs the RTDOSE grid", "cross-grid structure-volume reproduction (supersampled rasteriser)",
                       "CTV_GT 19.01 cc (CT grid) vs 19.39 cc (1.667 mm dose grid): +2.0 %; Cav_Post +0.9 %; CTV_Post +1.9 %; Brain +0.0 %; CTV_GT D90 6365 vs 6360 cGy across grids (the old voxel-centre rasteriser lost 40-65 % of every structure on the dose grid)",
                       "the same contours on two grids (within the 2 % gate); the MIM volumes remain the external check", FLAG_CAL, NOTES, "d40bb32",
                       ["19.39 cc", "+2.0 %", "+0.9 %", "+1.9 %", "6360 cGy", "d40bb32"],
                       "python -c \"from gtcore.io.rt import load_rtstruct, load_rtdose, rasterize_roi ...\" (Phase 2 rt-io notes)"))
        add(self.t3_csv("structure rasterisation", "PT-RT vs MIM export", "structure volume / D90 reproduction vs the TPS (verify_against_tps, 2 % gate)",
                        "pending MIM export (tps_check = '{0}' on every dose row above; the MIM DVH / plan export is not on this machine and the RTDOSE has no DVHSequence)",
                        [L(CLIN, {"row": H, "dose_source": "RTDOSE", "metric": "D90"}, "tps_check", "str")],
                        "MIM structure volumes and D90", FLAG_CAL))
        # ---------------------------------------------------------- packing capacity, eligibility
        add(self.t3_md("placement optimisation", "PT-CT1mm (F8b eligible wall)", "craniotomy-side eligibility and packing capacity at grid (h 2.5 mm, 6 spins; not exact)",
                       "facing + line-of-sight wall (no implant term) 26.5 % of the cavity area (11.8 % with the implant-visibility term); capacity 13 tiles (10 with the implant term) vs the manufacturer rule 4 tiles on the eligible area (34 on the whole wall); only 9/32 implanted seeds lie on the facing + line-of-sight wall, so the patient arms use that wall UNION the wall within 10 mm of an implanted seed (demonstrated reach; fraction pending campaign)",
                       "geometry only (no dose); opening = skull defect U cavity mouth, 28.7 cm2", FLAG_CAL, NOTES, "4cd9891",
                       ["0.265", "0.118", "10 tiles", "28.7 cm²", "4cd9891"],
                       'python -m gtcore.cli optimize "$GT_DATA_ROOT/<PT-CT1mm>" --tiles 8 --solver sa --eligible craniotomy',
                       [md_locator(DECISIONS, "26.5 %", "13 tiles at h 2.5 mm", "9/32", "demonstrated reach")]))
        add(self._capacity_row())
        for r in self._optimizer_rows():
            add(r)
        return t

    # ------------------------------------------------------- optimizer rows (campaign)
    def _capacity_row(self) -> Row:
        if self.landed(CAPACITY, ["capacity_at_grid"]):
            _, rows = self.repo.csv(CAPACITY)
            parts, locs = [], []
            for r in rows:
                if r.get("status", "ok") != "ok":
                    continue
                lab = r.get("dataset", "") + (" " + r.get("cavity", "") if r.get("dataset") == "synthetic" else "")
                key = {"dataset": r["dataset"], "cavity": r["cavity"]}
                parts.append("%s: rule %s / capacity %s / area bound %s / placed %s" % (
                    lab, fmt("int", r["rule_tiles"]), fmt("int", r["capacity_at_grid"]), fmt("int", r["upper_bound_area"]), fmt("int", r["implanted"])))
                for c in ("rule_tiles", "capacity_at_grid", "upper_bound_area", "implanted"):
                    locs.append(L(CAPACITY, key, c, "int").encode())
            self._note(CAPACITY)
            cells = {"stage": "placement optimisation", "dataset": "synthetic cavities, PH8", "metric": "manufacturer rule vs capacity at grid vs placed (%s)" % rows[0].get("label", ""),
                     "value": "; ".join(parts), "truth/reference": "greedy maximal independent set on the planner-rule conflict graph (lower bound, grid-dependent)",
                     "source csv": CAPACITY, "command": self.repo.csv_command(CAPACITY), "commit": self.repo.csv_commit(CAPACITY) or "?", "flag": FLAG_SYN + " / " + FLAG_CAL}
            return Row(cells, " ;; ".join(locs))
        return self.t3_pending("placement optimisation", "synthetic cavities, PH8", "manufacturer rule vs capacity at grid vs placed",
                               "campaign (Phase 4 validation_optimize.py --capacity)", CAPACITY,
                               "greedy maximal independent set on the conflict graph (lower bound)", FLAG_SYN + " / " + FLAG_CAL,
                               "python scripts/validation_optimize.py --capacity ...")

    def _optimizer_rows(self) -> List[Row]:
        rows: List[Row] = []
        R = self.repo
        # ---- synthetic benchmark: primary endpoint SA - uniform at N*
        if self.landed(V2_PRIMARY, ["comparison"]):
            _, prim = R.csv(V2_PRIMARY)
            hits = [r for r in prim if r.get("comparison", "").strip()]
            if hits:
                h = hits[-1]
                key = {"comparison": h["comparison"]}
                rows.append(self.t3_csv("placement optimisation", "18 synthetic cavities", "synthetic benchmark, primary endpoint: SA - uniform at N* (RIND5 V100, full grid)",
                                        "{0} pp [95 % CI {1}, {2}], n = {3}, Wilcoxon p = {4}",
                                        [L(V2_PRIMARY, key, "mean_pp", "pp1"), L(V2_PRIMARY, key, "ci_lo_pp", "pp1"), L(V2_PRIMARY, key, "ci_hi_pp", "pp1"),
                                         L(V2_PRIMARY, key, "n", "int"), L(V2_PRIMARY, key, "wilcoxon_p", "g3")],
                                        "uniform heuristic at the smallest N where it reaches D90 >= rx", FLAG_SYN))
        else:
            rows.append(self.t3_pending("placement optimisation", "18 synthetic cavities", "synthetic benchmark: SA - uniform at N* (pp, 95 % CI), SA - greedy per N, gap to the same-candidate V100 bound",
                                        "campaign (Phase 4 V2 / V3; the rev-1 run of 2026-10-07 at cca2120 is superseded)", V2_PRIMARY,
                                        "uniform / greedy heuristics; exact V100 bound on the same candidate set", FLAG_SYN,
                                        "python scripts/validation_optimize.py --section v2,v3,v4,v5,v7,v8 --sa-seeds 3 --same-candidates --capacity"))
        if self.landed(V2_PAIRED, ["comparison"]):
            _, paired = R.csv(V2_PAIRED)
            ep = self.col(paired, "endpoint")
            parts, locs = [], []
            for r in paired:
                if r.get("comparison", "").strip() != "sa - greedy":
                    continue
                if ep and "RIND5" not in r.get(ep, "") and "S5" in r.get(ep, ""):
                    continue
                key = {"N": r["N"], "comparison": r["comparison"]}
                if ep:
                    key[ep] = r[ep]
                parts.append("N = %s: %s pp [%s, %s] (n = %s)" % (r["N"], fmt("pp1", r["mean_pp"]), fmt("pp1", r["ci_lo_pp"]), fmt("pp1", r["ci_hi_pp"]), fmt("int", r["n"])))
                locs += [L(V2_PAIRED, key, "mean_pp", "pp1").encode(), L(V2_PAIRED, key, "ci_lo_pp", "pp1").encode(), L(V2_PAIRED, key, "ci_hi_pp", "pp1").encode()]
            if parts:
                self._note(V2_PAIRED)
                rows.append(Row({"stage": "placement optimisation", "dataset": "18 synthetic cavities", "metric": "SA - greedy per N (RIND5 V100, paired, 95 % CI)",
                                 "value": "; ".join(parts), "truth/reference": "greedy heuristic on the same candidate set",
                                 "source csv": V2_PAIRED, "command": R.csv_command(V2_PAIRED), "commit": R.csv_commit(V2_PAIRED) or "?", "flag": FLAG_SYN},
                                " ;; ".join(locs)))
        if self.landed(V3_GAP, ["gap"]):
            _, gap = R.csv(V3_GAP)
            ok = [r for r in gap if r.get("status") == "ok" and r.get("gap", "").strip() not in ("", "nan")]
            if ok:
                vals = sorted(float(r["gap"]) for r in ok)
                med = vals[len(vals) // 2]
                worst = min(vals)
                self._note(V3_GAP)
                rows.append(Row({"stage": "placement optimisation", "dataset": "synthetic cavities (same candidate set)", "metric": "SA vs the exact V100 bound: gap on the influence coverage (negative = below the bound)",
                                 "value": "median %.1f %%, worst %.1f %% over %d solved (cavity, N) instances" % (100 * med, 100 * worst, len(ok)),
                                 "truth/reference": "solve_milp coverage-only bound on the identical candidate set (tail term dropped)",
                                 "source csv": V3_GAP, "command": R.csv_command(V3_GAP), "commit": R.csv_commit(V3_GAP) or "?", "flag": FLAG_SYN},
                                "agg:" + V3_GAP + "#gap"))
        # ---- phantom arms
        v6 = V6_PHANTOM if self.landed(V6_PHANTOM, ["RIND5_V100"]) else (V6_PHANTOM_RAW if self.landed(V6_PHANTOM_RAW, ["RIND5_V100"]) else None)
        if v6:
            _, arms = R.csv(v6)
            parts, locs = [], []
            for r in arms:
                if r.get("status") != "ok" or r.get("RIND5_V100", "").strip() in ("", "nan"):
                    continue
                key = {"arm": r["arm"], "N": r["N"]}
                if "sa_seed" in r and r.get("sa_seed", "").strip():
                    key["sa_seed"] = r["sa_seed"]
                parts.append("%s: V100 %s %% / D90 %s cGy, %s overlaps" % (r["arm"], fmt("pct1", r["RIND5_V100"]), fmt("cgy", r["RIND5_D90"]), fmt("str", r.get("n_overlaps", ""))))
                locs += [L(v6, key, "RIND5_V100", "pct1").encode(), L(v6, key, "RIND5_D90", "cgy").encode()]
            minn = ""
            if R.exists(V6_INFO):
                info = R.json(V6_INFO)
                mn = info.get("min_n") if isinstance(info, dict) else None
                if isinstance(mn, dict):
                    minn = "; minimum N (greedy sweep): D90 >= rx at N = %s, V100 >= 0.90 at N = %s" % (mn.get("D90>=rx"), mn.get("V100>=0.90"))
                    locs.append(md_locator(V6_INFO, '"D90>=rx"'))
                    self._note(V6_INFO)
            if parts:
                self._note(v6)
                rows.append(Row({"stage": "placement optimisation", "dataset": "PH8 (wall = registered PH0; target RIND5(phantom cavity))", "metric": "phantom: as placed vs optimized, RIND5 V100 / D90 per arm (plastic, not tissue; NOT optimizer-vs-surgeon)",
                                 "value": "; ".join(parts) + minn, "truth/reference": "hand-placed tiles (arbitrary positions) vs the optimizer's 8 tiles on the same wall",
                                 "source csv": v6, "command": R.csv_command(v6), "commit": R.csv_commit(v6) or "?", "flag": FLAG_CAL},
                                " ;; ".join(locs)))
        else:
            rows.append(self.t3_pending("placement optimisation", "PH8 (wall = registered PH0)", "phantom: as placed vs optimized (RIND5 V100 / D90 per arm; minimum N; manufacturer rule)",
                                        "campaign (Phase 4 V6 on the registered tile-less wall; the rev-1 as_implanted 0.654 -> SA 0.849 on the +5 mm shell is superseded)", V6_PHANTOM,
                                        "hand-placed tiles vs the optimizer on the same wall", FLAG_CAL,
                                        'python scripts/validation_optimize.py --section v6 --phantom "$GT_DATA_ROOT/<PH8>" --ph0 "$GT_DATA_ROOT/<PH0>" --sa-seeds 3 --same-candidates'))
        # ---- patient: three arms (decisions 2026-11-03 a445cf7): as implanted; optimized for RIND5; optimized for CTV_GT
        if self.landed(F8_SUMMARY, ["arm"]):
            _, arms = R.csv(F8_SUMMARY)
            parts, locs = [], []
            for r in arms:
                key = {"arm": r["arm"]}
                if r.get("sa_seed", "").strip():
                    key["sa_seed"] = r["sa_seed"]
                bits = []
                for lab, cv, cd in (("RIND5(gtcore)", "RIND5_gtcore_V100", "RIND5_gtcore_D90_cGy"), ("CTV_GT", "CTV_GT_V100", "CTV_GT_D90_cGy"),
                                    ("IMPLANT_REACH(10)", "REACH10_V100", "REACH10_D90_cGy")):
                    if cv in r and cd in r:
                        bits.append("%s %s %% / %s cGy" % (lab, fmt("pct1", r[cv]), fmt("cgy", r[cd])))
                        locs += [L(F8_SUMMARY, key, cv, "pct1").encode(), L(F8_SUMMARY, key, cd, "cgy").encode()]
                parts.append("%s%s: %s; overlaps %s" % (r["arm"], (" (SA seed %s)" % r["sa_seed"]) if r.get("sa_seed", "").strip() else "",
                                                        ", ".join(bits), fmt("str", r.get("n_overlaps", ""))))
            if parts:
                self._note(F8_SUMMARY)
                rows.append(Row({"stage": "placement optimisation", "dataset": "PT-CT1mm (gtcore cavity; eligible = craniotomy U 10 mm demonstrated reach)",
                                 "metric": "patient: as implanted / optimized for RIND5 / optimized for CTV_GT, each scored on all three targets (V100 / D90; one case, illustration, not a cohort claim)",
                                 "value": "; ".join(parts), "truth/reference": "the implanted 32 seeds (physical axes) vs plan.optimize(mesh, 8, solver='sa') for the stated target; one dose_stats",
                                 "source csv": F8_SUMMARY, "command": R.csv_command(F8_SUMMARY), "commit": R.csv_commit(F8_SUMMARY) or "?", "flag": FLAG_CAL},
                                " ;; ".join(locs)))
        else:
            rows.append(self.t3_pending("placement optimisation", "PT-CT1mm (gtcore cavity; eligible = craniotomy U 10 mm demonstrated reach)",
                                        "patient: three arms -- as implanted / optimized for RIND5 / optimized for the physician's target (CTV_GT) -- each scored on RIND5(gtcore cavity), CTV_GT and IMPLANT_REACH(10); overlaps; solve time (F8 titles)",
                                        "campaign (scripts/patient_optimize.py; the 4748ccb trial run in docs/paper2-decisions.md and the mock 0.67 -> 0.81 / 0.92 -> 0.91 are not quoted)", F8_SUMMARY,
                                        "the implanted 32 seeds vs the optimizer on the same cavity wall; the optimizer optimizes the target it is given", FLAG_CAL,
                                        'python scripts/patient_optimize.py "$GT_DATA_ROOT/<PT-CT1mm>" --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT>" --tiles 8 ... --out-csv docs/figures/optimize/data --out-data docs/figures/data'))
        return rows

    # ================================================================ Table 4
    def table4(self) -> Table:
        t = Table("table4", "Table 4 -- runtime per stage per dataset",
                  ["dataset", "block", "stage", "seconds", "note", "source", "commit"])
        R = self.repo
        if self.landed(TABLE4_RUNTIMES, ["stage", "seconds"]):
            header, rows = R.csv(TABLE4_RUNTIMES)
            hw = rows[0].get("hardware", "") if rows else ""
            t.preamble = ["Hardware: %s. One run per stage with the machine otherwise idle (scripts/table4_runtimes.py, Phase 4 campaign); wall-clock seconds (perf_counter)." % (hw or HARDWARE_LINE)]
            self._note(TABLE4_RUNTIMES)
            for r in rows:
                key = {"dataset": r["dataset"], "block": r["block"], "stage": r["stage"]}
                t.add(Row({"dataset": r["dataset"], "block": r["block"], "stage": r["stage"], "seconds": fmt("s1", r["seconds"]),
                           "note": r.get("note", ""), "source": TABLE4_RUNTIMES, "commit": r.get("commit", "") or (R.csv_commit(TABLE4_RUNTIMES) or "?")},
                          L(TABLE4_RUNTIMES, key, "seconds", "s1").encode()))
            return t
        # interim: the Phase 1-3 timings recorded in the notes / CSV headers (concurrent load: upper bounds)
        t.preamble = ["Hardware: %s." % HARDWARE_LINE,
                      "INTERIM: the dedicated single-run measurement (`scripts/table4_runtimes.py` -> `docs/figures/data/table4_runtimes.csv`, Phase 4 campaign) "
                      "has not landed; the rows below are the Phase 1-3 timings recorded with other agents running (upper bounds). They are replaced "
                      "wholesale when the campaign file arrives."]

        def md(dataset, block, stage, seconds, note, notes, commit, tokens):
            missing = self.tokens_present(notes, tokens)
            if missing:
                raise LookupError("%s: tokens not found %s" % (notes, missing))
            self._note(notes)
            t.add(Row({"dataset": dataset, "block": block, "stage": stage, "seconds": seconds, "note": note,
                       "source": notes, "commit": commit}, md_locator(notes, *tokens)))

        t.add(Row({"dataset": "all", "block": "--", "stage": "dedicated per-stage measurement (reconstruct stages, optimizer stages, planner recompute latency)",
                   "seconds": "pending campaign", "note": "scripts/table4_runtimes.py", "source": TABLE4_RUNTIMES + " (expected)", "commit": "--"},
                  "pending:" + TABLE4_RUNTIMES))
        md("PT-CT1mm", "reconstruct", "seed detection / metal inpainting / head segmentation / cavity segmentation / surface meshes / tile fitting (count-free)",
           "0.55 / 1.75 / 7.92 / 3.89 / 5.93 / 28.3", "stage timings of reconstruct(auto), 49.6 s total (31 seeds at this pre-vault-fix commit)", NOTES, "bcb7a22",
           ["seed detection 0.55 s", "metal inpainting 1.75 s", "head segmentation 7.92 s", "cavity segmentation 3.89 s", "surface meshes 5.93 s", "tile fitting 28.3 s", "49.6 s"])
        md("PH8", "reconstruct", "seed detection / metal inpainting / head segmentation / cavity segmentation / surface meshes / tile fitting (count-free)",
           "0.38 / 0.23 / 27.86 / 2.54 / 0.61 / 2.42", "stage timings of reconstruct(auto), 34.6 s total", NOTES, "bcb7a22",
           ["seed detection 0.38 s", "metal inpainting 0.23 s", "head segmentation 27.86 s", "cavity segmentation 2.54 s", "tile fitting 2.42 s", "34.6 s"])
        md("PT-CT1mm", "reconstruct", "reconstruct (32 seeds, cavity, meshes) in the Phase 3 follow-up runs", "17.5", "wall, suite running concurrently", NOTES, "378b819", ["reconstruct 17.5 s", "378b819"])
        md("PT-CT1mm + PT-RT", "targets", "rind5 on the full CT grid (204 x 512 x 512)", "5.6", "EDT in physical mm + clip", NOTES, "32234a4", ["`rind5` on the full CT grid: 5.6 s", "32234a4"])
        md("PT-CT1mm + PT-RT", "validation", "structure x dose matrix (6 gtcore columns, exact engine at ~560 k voxel centres)", "22.7", "validation_clinical.py", NOTES, "378b819", ["matrix 22.7 s", "378b819"])
        g = L(GAMMA, {"seed_source": "gtcore", "axes": "physical", "dose_pct": "3", "dist_mm": "2", "normalisation": "global", "window": "30-200Gy"}, "runtime_eval_s", "s1")
        g2 = L(GAMMA, {"seed_source": "gtcore", "axes": "physical", "dose_pct": "3", "dist_mm": "2", "normalisation": "global", "window": "30-200Gy"}, "runtime_gamma_s", "s1")
        g3 = L(GAMMA, {"seed_source": "gtcore", "axes": "physical", "dose_pct": "2", "dist_mm": "1", "normalisation": "global", "window": "30-200Gy"}, "runtime_gamma_s", "s1")
        self._note(GAMMA)
        t.add(Row({"dataset": "PT-CT1mm + PT-RT", "block": "validation", "stage": "gamma: gtcore evaluation on the 0.5 mm lattice (5.1 M points) / pymedphys 3 %/2 mm / 2 %/1 mm (numba)",
                   "seconds": "%s / %s / %s" % (g.value(R), g2.value(R), g3.value(R)), "note": "per seed set; scipy backend about 3.7x slower, identical pass rates",
                   "source": GAMMA, "commit": R.csv_commit(GAMMA) or "?"}, " ;; ".join(x.encode() for x in (g, g2, g3))))
        md("PT-CT1mm + PT-RT", "validation", "MC uncertainty: 1000 draws on the 70 438-voxel HRCTV_PHYS (4 threads)", "88.0", "per band (88-106 s); about 0.1 s per draw", UNC, "c557cf4", ["runtime 88.0 s (4 threads)", "c557cf4"])
        t.add(Row({"dataset": "PT-CT1mm", "block": "tiles", "stage": "count-known fit with 6 full + 4 half tiles in the prior (fit_tiles_prior)",
                   "seconds": L(MODES_PT, {"mode": "count_6full_4half"}, "seconds", "s1").value(R), "note": "planner path on the gtcore cavity mesh (24 736 faces)",
                   "source": MODES_PT, "commit": R.csv_commit(MODES_PT) or "?"}, L(MODES_PT, {"mode": "count_6full_4half"}, "seconds", "s1").encode()))
        self._note(MODES_PT)
        md("PT-CT1mm", "optimizer", "craniotomy-side eligibility rule on the 24 736-face cavity mesh", "12.1", "hits_before + visible test; opening 2-3 s", NOTES, "4cd9891", ["12.1", "4cd9891"])
        md("PH8 + PH0", "validation", "seed_wall_distance.py (clinical + phantom, registration included)", "104", "whole script", NOTES, "448a6b3", ["104 s", "448a6b3"])
        md("PT-RT", "validation", "formalism_check.py (reconstruct + every variant and fit)", "157", "whole script", NOTES, "aaddf65", ["157 s wall", "aaddf65"])
        return t

    # ================================================================ Table S3
    def tableS3(self) -> Table:
        t = Table("tableS3_engine_qa", "Table S3 -- dose engine QA: gtcore TG43Engine('tg43u1s2') vs the TG-43U1S2 QA tables and Paper 1's engine",
                  ["section", "row", "n", "max |rel| %", "median |rel| %", "p95 |rel| %", "where max", "note", "commit"])
        t.preamble = ["rel = 100 x (gtcore / reference - 1); reference = Paper 1's DoseInterpolator (TG43U1S2 dataset) unless the row names a QA table "
                      "(Table XII: erratum values via Paper 1's transcription; Table XXIII: 2017 print, erratum status unknown). Synthetic geometry, no patient data (flag n/a)."]
        _, rows = self.repo.csv(ENGINE)
        c = self.repo.csv_commit(ENGINE) or "?"
        self._note(ENGINE)
        for r in rows:
            key = {"section": r["section"], "row": r["row"]}
            cells = {"section": r["section"], "row": r["row"], "n": fmt("int", r["n"]) if r["n"].strip() else "",
                     "max |rel| %": fmt("g3", r["max_abs_rel_pct"]) if r["max_abs_rel_pct"].strip() else "",
                     "median |rel| %": fmt("g3", r["median_abs_rel_pct"]) if r["median_abs_rel_pct"].strip() else "",
                     "p95 |rel| %": fmt("g3", r["p95_abs_rel_pct"]) if r["p95_abs_rel_pct"].strip() else "",
                     "where max": r["where_max"], "note": r["note"], "commit": c}
            locs = [L(ENGINE, key, col, "g3").encode() for col in ("max_abs_rel_pct", "median_abs_rel_pct", "p95_abs_rel_pct") if r[col].strip()]
            t.add(Row(cells, " ;; ".join(locs) if locs else "fixed:" + ENGINE))
        return t

    # ================================================================ Table S4
    def tableS4(self) -> Table:
        t = Table("tableS4_optimizer", "Table S4 -- optimizer campaign (synthetic benchmark, bound, discretisation, sensitivity, phantom, runtime, failure modes)",
                  ["block", "item", "value", "source csv", "commit", "flag"])
        t.preamble = ["Phase 4 campaign (`scripts/validation_optimize.py`, frozen objective lambda_tail 1 / TAIL_Q 0.10, SA 8 x 60, h 2.5 mm / 6 spins / M_opt 4000, full-grid RIND5 "
                      "scoring, same-candidate V100 bound). Blocks whose Phase 4 file has not landed are marked pending; the rev-1 files of 2026-10-07 (commit cca2120, "
                      "shell-surrogate scoring, SA 3 x 40) are superseded and not quoted."]
        R = self.repo

        def pending(block, item, expected):
            t.add(Row({"block": block, "item": item, "value": "pending campaign", "source csv": expected + " (expected)", "commit": "--", "flag": FLAG_SYN}, "pending:" + expected))

        def add(block, item, value, src, locs, flag=FLAG_SYN):
            self._note(src)
            t.add(Row({"block": block, "item": item, "value": value, "source csv": src, "commit": R.csv_commit(src) or "?", "flag": flag}, " ;; ".join(locs)))

        # V2 summary per N and arm
        if self.landed(V2_SUMMARY, []):
            _, rows = R.csv(V2_SUMMARY)
            vm = self.col(rows, "RIND5_V100_mean", r"RIND5_V100.*mean")
            vs = self.col(rows, "RIND5_V100_sd", r"RIND5_V100.*sd")
            dm = self.col(rows, "RIND5_D90_mean", r"RIND5_D90.*mean")
            nc = self.col(rows, "n_cav")
            if vm and dm:
                for r in rows:
                    key = {"N": r["N"], "arm": r["arm"]}
                    val = "RIND5 V100 %s %%" % fmt("pct1", r[vm]) + (" +/- %s" % fmt("pct1", r[vs]) if vs and r[vs].strip() not in ("", "nan") else "") + \
                          ", D90 %s cGy" % fmt("cgy", r[dm]) + (" (n = %s cavities)" % r[nc] if nc else "")
                    locs = [L(V2_SUMMARY, key, vm, "pct1").encode(), L(V2_SUMMARY, key, dm, "cgy").encode()]
                    add("V2 coverage vs N", "N = %s, %s" % (r["N"], r["arm"]), val, V2_SUMMARY, locs)
            else:
                add("V2 coverage vs N", "layout", "campaign file present but without RIND5_V100 / RIND5_D90 mean columns -- update paper_tables.tableS4", V2_SUMMARY, ["fixed:" + V2_SUMMARY])
        else:
            pending("V2 coverage vs N", "RIND5 V100 / D90 mean +/- SD per N and arm (uniform, greedy, SA, V100 bound, truth)", V2_SUMMARY)
        if self.landed(V2_PAIRED, ["comparison"]):
            _, rows = R.csv(V2_PAIRED)
            ep = self.col(rows, "endpoint")
            for r in rows:
                if ep and "S5" in r.get(ep, "") and "RIND5" not in r.get(ep, ""):
                    continue
                key = {"N": r["N"], "comparison": r["comparison"]}
                if ep:
                    key[ep] = r[ep]
                add("V2 paired comparisons", "N = %s, %s%s" % (r["N"], r["comparison"], (" (%s)" % r[ep]) if ep else ""),
                    "%s pp [%s, %s], n = %s, Wilcoxon p %s" % (fmt("pp1", r["mean_pp"]), fmt("pp1", r["ci_lo_pp"]), fmt("pp1", r["ci_hi_pp"]), fmt("int", r["n"]), fmt("g3", r["wilcoxon_p"])),
                    V2_PAIRED, [L(V2_PAIRED, key, "mean_pp", "pp1").encode(), L(V2_PAIRED, key, "ci_lo_pp", "pp1").encode(), L(V2_PAIRED, key, "ci_hi_pp", "pp1").encode()])
        else:
            pending("V2 paired comparisons", "SA - uniform, SA - greedy, V100 bound - SA, SA_best - greedy per N (pp, 95 % CI, Wilcoxon)", V2_PAIRED)
        if self.landed(V3_GAP, ["gap"]):
            _, rows = R.csv(V3_GAP)
            for r in rows:
                key = {"cavity": r["cavity"], "N": r["N"]}
                if r.get("sa_seed", "").strip():
                    key["sa_seed"] = r["sa_seed"]
                if r.get("status") != "ok":
                    add("V3 gap to the bound", "%s, N = %s" % (r["cavity"], r["N"]), "%s: %s" % (r.get("status"), r.get("reason", "")[:120]), V3_GAP, ["fixed:" + V3_GAP])
                    continue
                g = fmt("pct1", r["gap"])
                add("V3 gap to the bound", "%s, N = %s" % (r["cavity"], r["N"]), "gap %s %% on the influence coverage (SA %s vs bound %s); same candidates: %s" % (
                    g, fmt("f3", r.get("V100_sa_influence", r.get("INFL_V100_sa", "nan"))), fmt("f3", r.get("milp_bound", r.get("bound", "nan"))), r.get("same_candidates", "?")),
                    V3_GAP, [L(V3_GAP, key, "gap", "pct1").encode()])
        else:
            pending("V3 gap to the bound", "SA vs the exact V100 bound on the identical candidate set, per (cavity, N)", V3_GAP)
        if self.landed(V4_DISC, ["h_mm"]):
            _, rows = R.csv(V4_DISC)
            vc = self.col(rows, "sa_RIND5_V100", "sa_V100")
            for r in rows:
                key = {"cavity": r["cavity"], "h_mm": r["h_mm"], "n_spins": r["n_spins"]}
                add("V4 discretisation", "%s: h %s mm, %s spins, N = %s" % (r["cavity"], r["h_mm"], r["n_spins"], r["N"]),
                    "candidates %s; SA RIND5 V100 %s %%; status %s" % (r.get("n_candidates", "?"), fmt("pct1", r[vc]) if vc and r[vc].strip() not in ("", "nan") else "n/a", r.get("status")),
                    V4_DISC, [L(V4_DISC, key, vc, "pct1").encode()] if vc and r[vc].strip() not in ("", "nan") else ["fixed:" + V4_DISC])
        else:
            pending("V4 discretisation", "SA V100 vs anchor spacing / spins (h 4 / 2.5 mm, 3 / 6 / 12 spins)", V4_DISC)
        if self.landed(V5_RANK, ["kendall_tau"]):
            _, rows = R.csv(V5_RANK)
            taus = [float(r["kendall_tau"]) for r in rows if r["kendall_tau"].strip() not in ("", "nan")]
            add("V5 sensitivity", "rank stability of the arms under seed-plane 2.25 / 3.75 mm, S_K +/- 5 %, interference on, M_opt 4000 (Kendall tau)",
                "min %.2f, median %.2f over %d (cavity, perturbation) pairs" % (min(taus), sorted(taus)[len(taus) // 2], len(taus)), V5_RANK, ["agg:" + V5_RANK + "#kendall_tau"])
        else:
            pending("V5 sensitivity", "arm ranking under seed-plane, S_K, interference and M_opt perturbations (Kendall tau); V100 spread per arm", V5_RANK)
        v6 = V6_PHANTOM if self.landed(V6_PHANTOM, ["RIND5_V100"]) else (V6_PHANTOM_RAW if self.landed(V6_PHANTOM_RAW, ["RIND5_V100"]) else None)
        if v6:
            _, rows = R.csv(v6)
            for r in rows:
                key = {"arm": r["arm"], "N": r["N"]}
                if "sa_seed" in r and r.get("sa_seed", "").strip():
                    key["sa_seed"] = r["sa_seed"]
                if r.get("status") != "ok" or r.get("RIND5_V100", "").strip() in ("", "nan"):
                    add("V6 phantom (as placed vs optimized)", "%s, N = %s" % (r["arm"], r["N"]), "%s: %s" % (r.get("status"), r.get("reason", "")[:120]), v6, ["fixed:" + v6], FLAG_CAL)
                    continue
                add("V6 phantom (as placed vs optimized)", "%s, N = %s" % (r["arm"], r["N"]),
                    "RIND5 V100 %s %%, D90 %s cGy, V150 %s %%; overlaps %s" % (fmt("pct1", r["RIND5_V100"]), fmt("cgy", r["RIND5_D90"]), fmt("pct1", r["RIND5_V150"]), r.get("n_overlaps", "")),
                    v6, [L(v6, key, "RIND5_V100", "pct1").encode(), L(v6, key, "RIND5_D90", "cgy").encode(), L(v6, key, "RIND5_V150", "pct1").encode()], FLAG_CAL)
            if self.landed(V6_SWEEP, ["N"]):
                _, sw = R.csv(V6_SWEEP)
                hit = [r for r in sw if r.get("D90>=rx", "").strip() == "True"]
                add("V6 phantom (as placed vs optimized)", "minimum N (greedy sweep, S5 surrogate): first N with D90 >= rx",
                    ("N = %s" % hit[0]["N"]) if hit else "not reached within the sweep (N <= %s)" % sw[-1]["N"], V6_SWEEP,
                    [L(V6_SWEEP, {"N": hit[0]["N"]}, "D90>=rx", "str").encode()] if hit else ["fixed:" + V6_SWEEP], FLAG_CAL)
        else:
            pending("V6 phantom (as placed vs optimized)", "as_placed, as_placed_conformed, uniform, greedy, SA, V100 bound on RIND5(phantom cavity); minimum N", V6_PHANTOM)
        if self.landed(V7_RUNTIME, ["t_candidates"]):
            _, rows = R.csv(V7_RUNTIME)
            for r in rows:
                key = {"cavity": r["cavity"], "N": r["N"]}
                parts = []
                locs = []
                for c in ("t_candidates", "t_conflicts", "t_influence", "t_greedy", "t_sa", "t_milp", "t_final_report_full_50mm", "t_final_report_15mm"):
                    if c in r and r[c].strip() not in ("", "nan"):
                        parts.append("%s %s s" % (c[2:], fmt("s1", r[c])))
                        locs.append(L(V7_RUNTIME, key, c, "s1").encode())
                add("V7 runtime", "%s (%s mL, C = %s), N = %s" % (r["cavity"], r.get("volume_ml", "?"), r.get("n_candidates", "?"), r["N"]), "; ".join(parts), V7_RUNTIME, locs or ["fixed:" + V7_RUNTIME])
        else:
            pending("V7 runtime", "candidates / conflicts / influence / solver / final_report per cavity scale", V7_RUNTIME)
        if self.landed(V8_FAIL, ["verdict"]):
            _, rows = R.csv(V8_FAIL)
            n_pass = sum(1 for r in rows if r["verdict"].startswith("PASS"))
            add("V8 failure modes", "degenerate / empty meshes, N beyond any packing, tile wider than the wall, eligibility smaller than a tile",
                "%d of %d cases behave as specified (explicit ValueError / RuntimeError or a valid plan)" % (n_pass, len(rows)), V8_FAIL, ["agg:" + V8_FAIL + "#verdict"])
        else:
            pending("V8 failure modes", "explicit errors on degenerate inputs", V8_FAIL)
        if self.landed(CAPACITY, ["capacity_at_grid"]):
            _, rows = R.csv(CAPACITY)
            for r in rows:
                key = {"dataset": r["dataset"], "cavity": r["cavity"]}
                add("capacity", "%s %s" % (r["dataset"], r["cavity"][:60]),
                    "manufacturer rule %s / capacity at grid %s / area bound %s / placed %s (%s)" % (r["rule_tiles"], r["capacity_at_grid"], r["upper_bound_area"], r["implanted"], r["label"]),
                    CAPACITY, [L(CAPACITY, key, "capacity_at_grid", "int").encode()], FLAG_SYN if r["dataset"] == "synthetic" else FLAG_CAL)
        else:
            pending("capacity", "manufacturer rule vs capacity at grid vs placed (synthetic cavities, PH8)", CAPACITY)
        if self.landed(F8_SUMMARY, ["arm"]):
            _, rows = R.csv(F8_SUMMARY)
            for r in rows:
                key = {"arm": r["arm"]}
                if r.get("sa_seed", "").strip():
                    key["sa_seed"] = r["sa_seed"]
                add("patient (F8)", "%s%s" % (r["arm"], (" seed %s" % r["sa_seed"]) if r.get("sa_seed", "").strip() else ""),
                    "RIND5(gtcore) V100 %s %% / D90 %s cGy; CTV_GT V100 %s %% / D90 %s cGy; REACH10 V100 %s %% / D90 %s cGy; tiles %s (%s full + %s half), overlaps %s" % (
                        fmt("pct1", r["RIND5_gtcore_V100"]), fmt("cgy", r["RIND5_gtcore_D90_cGy"]), fmt("pct1", r["CTV_GT_V100"]), fmt("cgy", r["CTV_GT_D90_cGy"]),
                        fmt("pct1", r["REACH10_V100"]), fmt("cgy", r["REACH10_D90_cGy"]), r.get("n_tiles", ""), r.get("n_full", ""), r.get("n_half", ""), r.get("n_overlaps", "")),
                    F8_SUMMARY, [L(F8_SUMMARY, key, "RIND5_gtcore_V100", "pct1").encode(), L(F8_SUMMARY, key, "CTV_GT_D90_cGy", "cgy").encode()], FLAG_CAL)
        else:
            pending("patient (F8)", "as implanted vs optimized arms on RIND5(gtcore cavity) / CTV_GT / IMPLANT_REACH(10)", F8_SUMMARY)
        return t

    # ================================================================ captions
    def captions(self) -> Table:
        t = Table("caption_numbers", "Caption numbers for F4, F5, F6 and F8", ["figure", "panel", "item", "value", "source", "commit", "flag"])
        t.preamble = ["Every number a caption of F4, F5, F6 or F8 states, with its source; the panels themselves carry no annotations (Inkscape layer)."]
        R = self.repo

        def cap(figure, panel, item, template, lookups, flag=FLAG_CAL):
            vals = [lk.value(R) for lk in lookups]
            paths = []
            for lk in lookups:
                if lk.path not in paths:
                    paths.append(lk.path)
                self._note(lk.path)
            commits = ", ".join(dict.fromkeys(R.csv_commit(p) or "?" for p in paths))
            t.add(Row({"figure": figure, "panel": panel, "item": item, "value": template.format(*vals), "source": ", ".join(paths), "commit": commits, "flag": flag},
                      " ;; ".join(lk.encode() for lk in lookups)))

        def capmd(figure, panel, item, value, notes, commit, tokens, flag=FLAG_CAL):
            missing = self.tokens_present(notes, tokens)
            if missing:
                raise LookupError("%s: tokens not found %s" % (notes, missing))
            self._note(notes)
            t.add(Row({"figure": figure, "panel": panel, "item": item, "value": value, "source": notes, "commit": commit, "flag": flag}, md_locator(notes, *tokens)))

        H = "HRCTV_PHYS (CTV_GT)"
        cap("F4", "a", "contoured seeds matched (2 mm gate) and match distance",
            "{0} contoured seeds matched one-to-one; median {1} mm, p95 {2} mm, max {3} mm",
            [clin("seed match", "-", "n_matched_of_contoured", "str", "Seeds (contoured) vs gtcore"), clin("seed match", "-", "match_median_mm", "mm2", "Seeds (contoured) vs gtcore"),
             L(GAMMA_SETS, {"seed_set": "gtcore"}, "vs_tps_contours_p95_mm", "mm2"), clin("seed match", "-", "match_max_mm", "mm2", "Seeds (contoured) vs gtcore")])
        cap("F4", "a", "plan sources (RTDOSE maxima) matched and the two uncontoured sources",
            "{0} plan sources matched within 3 mm (median {1} mm); {2} detections, 0 false detections; the 2 uncontoured sources are detections 10 and 17 (0.90 / 0.83 mm from an RTDOSE maximum)",
            [clin("seed match", "-", "n_matched_of_sources", "str", "plan sources (RTDOSE maxima) vs gtcore"), clin("seed match", "-", "source_median_mm", "mm2", "plan sources (RTDOSE maxima) vs gtcore"),
             clin("seed match", "-", "n_detected", "str", "gtcore detections")])
        cap("F4", "b", "count-free tile recovery and the leftover seeds",
            "{0} of 8 tiles fitted, {1} seeds unassigned = four 2-seed pairs at the manufactured pitch ({2}-{3} mm); 6 full + 4 half tiles pending the operative record",
            [L(MODES_PT, {"mode": "auto"}, "n_tiles", "int"), L(MODES_PT, {"mode": "auto"}, "n_leftover", "int"),
             L(PAIRS_PT, {"seeds": "11;21"}, "dist_mm", "mm1"), L(PAIRS_PT, {"seeds": "2;5"}, "dist_mm", "mm1")])
        capmd("F4", "b", "bent-tile RMS of the six fitted tiles", "0.18-0.67 mm", NOTES, "afb96ea", ["0.18–0.67 mm", "afb96ea"])
        cap("F4", "c", "phantom recovery", "{0} of 32 seeds assigned to {1} of 8 tiles (count-free); {2} degraded = the crumpled tile",
            [L(MODES_PH8, {"mode": "auto"}, "n_assigned", "int"), L(MODES_PH8, {"mode": "auto"}, "n_tiles", "int"), L(MODES_PH8, {"mode": "auto"}, "n_degraded", "int")])
        capmd("F4", "c", "crumpled tile fit", "bent RMS 0.46 mm, fold 288 deg (bending energy 0.066)", NOTES, "afb96ea", ["0.46 mm", "fold 288°", "0.066"])
        cap("F5", "a", "isodose overlay: structure and dose sources", "CTV_GT {0} cc; TPS 30 / 60 Gy vs gtcore @ 32 gtcore seeds (physical axes), S_K 3.5 U",
            [clin(H, "-", "volume_cc", "cc1")])
        cap("F5", "b", "DVH on CTV_GT: D90 / V100 of each curve",
            "TPS {0} cGy / {1} %; gtcore @ 32 TPS sources {2} / {3}; gtcore @ 32 gtcore seeds {4} / {5}; gtcore @ 30 contoured seeds {6} / {7}",
            [clin(H, "RTDOSE", "D90", "cgy"), clin(H, "RTDOSE", "V100", "pct1"), clin(H, "gtcore@32 TPS sources", "D90", "cgy"), clin(H, "gtcore@32 TPS sources", "V100", "pct1"),
             clin(H, "gtcore@32 gtcore seeds", "D90", "cgy"), clin(H, "gtcore@32 gtcore seeds", "V100", "pct1"), clin(H, "gtcore@30 contoured seeds", "D90", "cgy"), clin(H, "gtcore@30 contoured seeds", "V100", "pct1")])
        cap("F5", "b", "TPS axis convention (S-I axes) equivalence row",
            "with every seed axis along S-I: gtcore @ 32 TPS sources {0} cGy / {1} %, @ 32 gtcore seeds {2} cGy / {3} % (RTDOSE {4} cGy)",
            [clin(H, "gtcore@32 TPS sources (S-I axes)", "D90", "cgy"), clin(H, "gtcore@32 TPS sources (S-I axes)", "V100", "pct1"),
             clin(H, "gtcore@32 gtcore seeds (S-I axes)", "D90", "cgy"), clin(H, "gtcore@32 gtcore seeds (S-I axes)", "V100", "pct1"), clin(H, "RTDOSE", "D90", "cgy")])
        cap("F5", "b", "MC envelope (primary band, gtcore @ 32 seeds, 1000 draws)",
            "D90 95 % [{0}, {1}] cGy, V100 95 % [{2}, {3}] %; degraded-export band D90 [{4}, {5}] cGy",
            [unc("residual", "gtcore32", "D90", "p2.5", "cgy"), unc("residual", "gtcore32", "D90", "p97.5", "cgy"), unc("residual", "gtcore32", "V100", "p2.5", "pct1"), unc("residual", "gtcore32", "V100", "p97.5", "pct1"),
             unc("degraded_export", "gtcore32", "D90", "p2.5", "cgy"), unc("degraded_export", "gtcore32", "D90", "p97.5", "cgy")])
        cap("F5", "caption", "gamma pass rates (gtcore seeds; 30-200 Gy, >= 30 Gy in brackets)",
            "physical axes: 3 %/2 mm {0} % ({1}) global, {2} % ({3}) local; 2 %/1 mm {4} % ({5}) global, {6} % ({7}) local. S-I axes (TPS convention): 3 %/2 mm {8} % ({9}), 2 %/1 mm {10} % ({11}) global",
            [gam("gtcore", "physical", "3", "2", "global", "30-200Gy"), gam("gtcore", "physical", "3", "2", "global", "ge30Gy"), gam("gtcore", "physical", "3", "2", "local", "30-200Gy"), gam("gtcore", "physical", "3", "2", "local", "ge30Gy"),
             gam("gtcore", "physical", "2", "1", "global", "30-200Gy"), gam("gtcore", "physical", "2", "1", "global", "ge30Gy"), gam("gtcore", "physical", "2", "1", "local", "30-200Gy"), gam("gtcore", "physical", "2", "1", "local", "ge30Gy"),
             gam("gtcore", "si", "3", "2", "global", "30-200Gy"), gam("gtcore", "si", "3", "2", "global", "ge30Gy"), gam("gtcore", "si", "2", "1", "global", "30-200Gy"), gam("gtcore", "si", "2", "1", "global", "ge30Gy")])
        cap("F5", "caption", "voxels above 200 Gy (excluded from the primary window)",
            "{0} % of the voxels >= 30 Gy; the tiles' seed planes: median {1} mm, 95 % within {2} mm of the nearest seed, where the 1 x 1 x 1.67 mm TPS grid averages the seed-plane dose",
            [L(GAMMA_CAPS, {}, "fraction_above_200", "pct2"), L(GAMMA_CAPS, {}, "dist_gtcore_median_mm", "mm2"), L(GAMMA_CAPS, {}, "dist_gtcore_p95_mm", "mm2")])
        capmd("F5", "caption", "TPS seed-axis convention", "the TPS computed every source with its long axis along S-I (common fitted axis 2.2 deg from S-I; physical PCA axes 64.5 deg from it in median)",
              FORM_AXES, "aaddf65", ["common fitted axis 2.2 deg from z", "PCA vs z median 64.5 deg"])
        cap("F6", "slice", "structures on the slice: volumes and overlap",
            "Cav_Post {0} cc; CTV_GT (HRCTV_PHYS) {1} cc = 53 % of the full rind; RIND5(Cav_Post, clip Brain) {2} cc, Dice vs CTV_GT {3}; IMPLANT_REACH(r = 10 mm) {4} cc, Dice {5}",
            [clin("RTSTRUCT", "-", "volume_cc", "cc1", "RTSTRUCT:Cav_Post"), clin(H, "-", "volume_cc", "cc1"), clin("RIND5(Cav_Post, clip Brain)", "-", "volume_cc", "cc1"),
             clin("RIND5(Cav_Post, clip Brain)", "-", "dice_vs_CTV_GT", "f3"), clin("IMPLANT_REACH(r=10 mm)", "-", "volume_cc", "cc1"), clin("IMPLANT_REACH(r=10 mm)", "-", "dice_vs_CTV_GT", "f3")])
        cap("F6", "caption", "the clinical RTDOSE's D90 on each target (same dose, different rind)",
            "CTV_GT {0} Gy; RIND5(Cav_Post, clip Brain) {1} Gy; RIND5(gtcore cavity, clip cranial interior; {2} cc) {3} Gy; IMPLANT_REACH(10) {4} Gy; IMPLANT_REACH(12) {5} Gy",
            [clin(H, "RTDOSE", "D90", "gy1"), clin("RIND5(Cav_Post, clip Brain)", "RTDOSE", "D90", "gy1"), clin("RIND5(gtcore cavity, clip cranial interior)", "-", "volume_cc", "cc1"),
             clin("RIND5(gtcore cavity, clip cranial interior)", "RTDOSE", "D90", "gy1"), clin("IMPLANT_REACH(r=10 mm)", "RTDOSE", "D90", "gy1"), clin("IMPLANT_REACH(r=12 mm)", "RTDOSE", "D90", "gy1")])
        cap("F6", "caption", "seed-to-Cav_Post distance (Methods sentence)", "32 gtcore seeds: median {0} mm (10-90 % {1}-{2} mm), positive toward the lumen; TPS sources {3} mm",
            [wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "edt", "median"), wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "edt", "p10"), wall("PT-CT1mm", "gtcore_seeds", "Cav_Post", "edt", "p90"),
             wall("PT-CT1mm+PT-RT", "tps_sources", "Cav_Post", "edt", "median")])
        cap("F8", "a", "as implanted: gtcore @ 32 gtcore seeds on the full rind and on CTV_GT (title numbers)",
            "RIND5(gtcore cavity, clip cranial interior; {0} cc): V100 {1} % / D90 {2} cGy; CTV_GT: V100 {3} % / D90 {4} cGy (RTDOSE on the same rind: V100 {5} % / D90 {6} cGy)",
            [clin("RIND5(gtcore cavity, clip cranial interior)", "-", "volume_cc", "cc1"), clin("RIND5(gtcore cavity, clip cranial interior)", "gtcore@32 gtcore seeds", "V100", "pct1"),
             clin("RIND5(gtcore cavity, clip cranial interior)", "gtcore@32 gtcore seeds", "D90", "cgy"), clin(H, "gtcore@32 gtcore seeds", "V100", "pct1"), clin(H, "gtcore@32 gtcore seeds", "D90", "cgy"),
             clin("RIND5(gtcore cavity, clip cranial interior)", "RTDOSE", "V100", "pct1"), clin("RIND5(gtcore cavity, clip cranial interior)", "RTDOSE", "D90", "cgy")])
        capmd("F8", "b", "eligible wall for the optimizer", "craniotomy wall (facing + line of sight, no implant term; 26.5 % of the cavity area, holds 9/32 implanted seeds) UNION the wall within 10 mm of an implanted seed (demonstrated reach); packing capacity 13 tiles at h 2.5 mm / 6 spins on the craniotomy wall",
              DECISIONS, "a445cf7", ["26.5 %", "13 tiles at h 2.5 mm", "9/32", "demonstrated reach"])
        if self.landed(F8_SUMMARY, ["arm"]):
            _, arms = R.csv(F8_SUMMARY)
            for r in [r for r in arms if not r["arm"].startswith("as_impl")]:
                key = {"arm": r["arm"]}
                if r.get("sa_seed", "").strip():
                    key["sa_seed"] = r["sa_seed"]
                cap("F8", "b", "optimized 8 tiles (title numbers; arm %s)" % r["arm"],
                    "RIND5(gtcore cavity): V100 {0} % / D90 {1} cGy; CTV_GT: V100 {2} % / D90 {3} cGy; overlaps {4}",
                    [L(F8_SUMMARY, key, "RIND5_gtcore_V100", "pct1"), L(F8_SUMMARY, key, "RIND5_gtcore_D90_cGy", "cgy"), L(F8_SUMMARY, key, "CTV_GT_V100", "pct1"),
                     L(F8_SUMMARY, key, "CTV_GT_D90_cGy", "cgy"), L(F8_SUMMARY, key, "n_overlaps", "str")])
        else:
            t.add(Row({"figure": "F8", "panel": "b", "item": "optimized arms (for RIND5; for CTV_GT): title numbers V100 / D90 on the full rind, CTV_GT and IMPLANT_REACH(10); overlaps; solve time",
                       "value": "pending campaign (scripts/patient_optimize.py -> f8_patient_summary.csv, f8b_patient_optimized.npz)", "source": F8_SUMMARY + " (expected)",
                       "commit": "--", "flag": FLAG_CAL}, "pending:" + F8_SUMMARY))
        return t

    # ------------------------------------------------------------- verify one part
    def _verify_part(self, part: str, row_text: str, commit_cell: str) -> Optional[str]:
        """Return a problem description or None."""
        R = self.repo
        if part.startswith("csv:"):
            lk = Lookup.decode(part)
            if not R.exists(lk.path):
                return "missing file %s" % lk.path
            try:
                v = lk.value(R)
            except LookupError as e:
                return str(e)
            if v not in row_text:
                return "%s -> %r not in row" % (part, v)
            c = R.csv_commit(lk.path)
            if c and commit_cell and commit_cell not in ("--", "?") and c not in commit_cell and not any(c.startswith(x) or x.startswith(c) for x in re.split(r"[,;\s]+", commit_cell) if x):
                return "%s: header commit %s not in row commit %r" % (lk.path, c, commit_cell)
            return None
        if part.startswith("md:") or part.startswith("code:"):
            kind, _, body = part.partition(":")
            path, _, toks = body.partition("#")
            if not R.exists(path):
                return "missing file %s" % path
            missing = self.tokens_present(path, [t for t in toks.split("|") if t])
            if missing:
                return "%s: tokens not found %s" % (path, missing)
            return None
        if part.startswith("agg:"):
            path = part[4:].partition("#")[0]
            return None if R.exists(path) else "missing file %s" % path
        if part.startswith("pending:"):
            path = part[8:]
            if R.exists(path) and R.csv_commit(path) is not None:
                return "%s has landed but the row is still pending: rebuild the tables" % path
            return None
        if part.startswith("fixed:"):
            path = part[6:]
            return None if R.exists(path) else "missing file %s" % path
        return "unknown locator %r" % part


# ================================================================ writers
def md_escape(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def table_md(t: Table) -> str:
    out = ["## %s" % t.title, ""]
    for line in t.preamble:
        out += [line, ""]
    out.append("| " + " | ".join(md_escape(c) for c in t.columns) + " |")
    out.append("|" + "|".join("---" for _ in t.columns) + "|")
    for r in t.rows:
        out.append("| " + " | ".join(md_escape(r.cells[c]) for c in t.columns) + " |")
    out.append("")
    return "\n".join(out)


def write_table(t: Table, out_dir: str, head: str, data_commits: Sequence[str]) -> Tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, t.name + ".csv")
    md_path = os.path.join(out_dir, t.name + ".md")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        fh.write("# %s\n" % t.title)
        fh.write("# generated by scripts/paper_tables.py at tree %s on %s from the committed data (%s)\n" % (
            head, _dt.date.today().isoformat(), ", ".join(data_commits)))
        fh.write("# locator: csv:<file>?<where>#<column>@<fmt> | md:<file>#<tokens> | code:<file>#<tokens> | pending:<file> | fixed:<file> | agg:<file>#<column>; parts separated by ';;'\n")
        w = csv.DictWriter(fh, fieldnames=t.columns + [LOC])
        w.writeheader()
        for r in t.rows:
            d = {c: r.cells[c] for c in t.columns}
            d[LOC] = r.locator
            w.writerow(d)
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(table_md(t))
        fh.write("\nGenerated by `scripts/paper_tables.py` at tree `%s` from the committed data; the CSV next to this file carries a `locator` column per row.\n" % head)
    return csv_path, md_path


# ================================================================ summary
CHANGES_VS_REV3 = """\
## Changes versus the rev-3 / v6 / v7 mock numbers

Every line: mock -> measured (commit of the measurement). The mock numbers are never quoted in the paper.

- Seed-to-`Cav_Post` distance: median 4.6 mm (10-90 % 2.7-5.9, mock) -> **3.65 mm** (1.70-5.46; TPS sources 3.57 mm); `SEED_PLANE_OFFSET_MM` 3.0 not re-tuned (448a6b3).
- `IMPLANT_REACH` radius: 12 mm / Dice 0.80 (sketch) -> **r = 10 mm / Dice 0.881** (12 mm: 0.846, covers 98.5 % of CTV_GT) (32234a4, 378b819).
- Gamma vs the RTDOSE (own implementation, mock 99.2 / 88.1 % at 3 %/2 mm / 2 %/1 mm global, 30-200 Gy) -> pymedphys 0.41.0 **99.3 / 97.0 %** global (98.7 / 95.2 % over >= 30 Gy; local 99.4 / 96.2 %); with the TPS axis convention (S-I axes) 99.5 / 98.8 % (ab85b1c, 378b819).
- "Voxels > 200 Gy lie within ~3 mm of a seed" -> they are the tiles' seed planes: median 3.82 mm, p95 6.05 mm from the nearest seed; 9.05 % of the >= 30 Gy voxels (ab85b1c).
- "Global-to-rx is lenient near seeds" -> reversed above the prescription dose: local normalisation is the lenient one above rx, global-to-rx the strict one; below rx the opposite (ab85b1c).
- Tile count on the clinical case: "8 full tiles = 32 sources" -> the geometry says **6 full + 4 half tiles** (four 2-seed pairs at 9.0-9.6 mm pitch; clusters span 18.0 / 25.5 mm > 14.1 mm); count-free 6/8 with 8 leftovers; count-known N = 8: 7/8 with one degraded completion; `ImplantPrior(6, 4)` explains 32/32 -- which count-known row the paper quotes is pending the operative record (afb96ea, 378b819).
- Held-out scan: "Case 54 held-out run" -> **none**: Case 54 is the same patient (CT identity certain); one clinical case, every real-data row calibration-set; `scripts/heldout_case54.py` dropped (decisions 2026-11-03).
- Seed match vs the TPS contours: 29/30, residual "0.23 mm median, 0.9 mm max" -> **30/30**, median 0.15 mm, p95 0.73, max 0.89 mm; detections 31 -> **32** = the plan's 32 sources (vault filter in physical mm, fad8701; fe0dc81).
- CTV_GT volume on the dose grid: 21.6 cc (+14 %) -> 19.39 cc (+2.0 %) with the supersampled rasteriser; the MIM volume check itself is still pending (d40bb32).
- Implied S_K (diagnostic only): 3.76 U with 30 contoured seeds -> 3.54 U with the plan's 32 sources (physical axes), 3.53 U with S-I axes; nominal 3.5 U kept (fe0dc81, 378b819).
- CTV_GT D90, gtcore at the 32 gtcore seeds: 6051 (31 seeds) / 6168 cGy -> **6167 cGy** (physical axes), 6288 cGy with the TPS axis convention; RTDOSE 6365 cGy (378b819).
- F6 D90 triplet under the RTDOSE: 63.7 / 45.9 / 28.6 Gy (rev 3) and 63.0 / 58.4 / 46.2 Gy (v7 designation) -> **63.6 Gy (CTV_GT) / 46.5 Gy (RIND5(Cav_Post, clip Brain)) / 27.2 Gy (RIND5(gtcore cavity)) / 68.8 Gy (IMPLANT_REACH(10); 58.6 Gy at r = 12)**; CTV_GT covers 53 % of the full rind (not 56 %) (378b819).
- Synthetic cavity Dice at `HULL_REACH_MM` 14: 0.839 -> **0.896** with the mm-correct geodesic growth (not re-tuned); clinical Dice 0.668 -> 0.665 (unchanged within noise) (0efe5eb, 378b819).
- Formalism: "a 1D plan would give 1.29 -> 0.96" -> far field only; under the test's selection 0.95 -> 1.05. **MIM is 2D with every seed axis along S-I** (common fitted axis 2.2 deg from S-I; physical axes 64.5 deg from it); the residual +-6 % angular trend of rev 3 is that axis convention, not the dataset, grid or S_K (aaddf65).
- Phantom "localized seeds 0.84 mm off the wall mesh" -> a reference-surface artifact (the PH8 body mask includes the tiles: -0.68 mm to the tile face); +3.05 mm to the registered tile-less wall, 26/32 in the hydrated band (448a6b3).
- Phantom cavity "lumen = filled minus body" -> impossible for this print (no plateau); gtcore's cavity rule on the registered twin: 55.3 cc, RIND5 47.7 cc (4cd9891).
- F8 arms: the mock run's 0.67 -> 0.81 (rind) / 0.92 -> 0.91 (CTV_GT) is never quoted. Two arms -> **three** (decisions a445cf7): as implanted; optimized for `RIND5`; optimized for the physician's target (`HRCTV_PHYS` = `CTV_GT`), each scored on all three targets. Eligibility facing + line of sight (26.5 % of the wall, only 9/32 implanted seeds on it) -> that wall **UNION the wall within 10 mm of an implanted seed** ("demonstrated reach"). The trial run at 4748ccb (optimizing the full rind costs -17 pp on `CTV_GT`: 0.917 -> 0.750) is recorded in the decisions log, not quoted; the paper numbers come from the campaign's patient run -- pending.
- Optimizer campaign: the rev-1 numbers (2026-10-07 at cca2120: SA - uniform +22.8 pp [19.3, 26.4] on the +5 mm shell; phantom as_implanted V100 0.654 -> SA 0.849; min-N 10 vs rule 12) are superseded by the Phase 4 campaign (full-grid RIND5 scoring, SA 8 x 60, same-candidate V100 bound, registered PH0 wall) -- pending.
- F2(c): the kappa range shown is stated (`KAPPA_RANGE` (-0.08, 0.5) per mm; wall-conformed render at 1/17 per mm). F9: no STL, reduced or cut. S2: PT-CT2mm lost; substitutes PT-CT-s3 vs PT-CT1mm and a decimated thin-cut.
"""

OPEN_ITEMS = """\
## Open items (what no committed number can replace)

1. **MIM DVH / plan export** -- `verify_against_tps` (structure volumes and D90 within 2 %) has not run on real data; every dose row of Table 3 carries `TPS volume check pending`. Command: `python scripts/validation_clinical.py ... --mim-dvh "$GT_DATA_ROOT/<MIM DVH export file>"`.
2. **RTPLAN** -- per-seed S_K, reference time and `ControlPointOrientation` (expected (0, 0, +-1) or absent for every source); `scripts/formalism_check.py ... --rtplan "$GT_DATA_ROOT/<PT-RT RTPLAN>"` adds the `b6_axes_rtplan` variant; a `tps_rtplan` seed set for the gamma script.
3. **PT-CT2mm** -- missing since the 2026-11-03 folder move; S2's real-data panel uses the two stated substitutes; the 0.51 / 0.40 / 0.63 mm SDs of the degraded-export band are quoted, not regenerable.
4. **Operative record** -- were two tiles cut? Decides between the N = 8 count-known row (7/8, one degraded completion) and the 6 full + 4 half row (32/32), and whether F4(b) / F8(a) draw 6 sheets + 8 red seeds or 6 sheets + 4 half-tile pairs (drawn from the pair geometry: the `RigidTile("half")` template is cut across the seed rows, the observed halves along them).
5. **Phase 4 campaign outputs** -- `docs/figures/optimize/data/{v2_*,v3_gap,v4_discretization,v5_*,v6_phantom*,v7_runtime,v8_failure_modes,capacity,f8_patient_summary,f8_patient_arms}.csv`, `docs/figures/data/f8b_patient_optimized.npz` and `docs/figures/data/table4_runtimes.csv`: the optimizer rows of Table 3, Table S4, Table 4 and the F8(b) caption are `pending campaign` until they land (`paper_tables.py` picks them up by file name).
6. **F7 recordings** -- three planner frames by hand from `gt plan "$GT_DATA_ROOT/<PH8>" --hrctv` on the tagged build (fixed window size, same crop); not rendered by the figure script.
7. **Photos** -- F2(a) tile with scale, F3(b) phantom with the eight tiles, F3(a) MRI crops (cavity outlines), F9 strip (CT / 3T MRI / Artec / TRIOS of the tile-less phantom) or F9 cut to a Discussion sentence.
8. **IRB / consent statement** (Table 1 placeholder) and the **Zenodo DOI** of the tagged release (`v1.0-medphys`).
9. **TG-43U1S2 erratum text** (Med Phys 45:971) -- Table XII rests on Paper 1's transcription (validated indirectly); Table XXIII is the 2017 print. Paper 1's F(7 cm, 80 deg) slip (0.999 -> 0.998) to be fixed in that repository.
10. **Cavity across the loop** (Table 1 / F3 caption: cavity volume on each scan, intra-op -> post-op wall displacement) -- `scripts/cavity_change.py` (Phase 5 task `cavity-change`); manual MRI cavity contours from Jacob if wanted (no MRI segmentation in gtcore).
"""


def write_summary(path: str, tables: Dict[str, Table], head: str, data_commits: Sequence[str], sources: Sequence[str]) -> None:
    out = ["# Paper 2 -- results summary (every paper number with its command and commit)", "",
           "Generated by `scripts/paper_tables.py` at tree `%s` (%s) from the committed data only. Data commits read: %s. "
           "Flags: `calibration-set` (one clinical case / its printed twin; reference, not validation cohort; nothing tuned on it), `synthetic` "
           "(ground truth by construction), `n/a` (published tables, negative control, convention), `pending` (campaign output, operative record "
           "or MIM export not yet available). Tables 1-4, S3, S4 and the caption numbers live in `docs/figures/tables/` (CSV with a `locator` "
           "column per row + Markdown); `python scripts/paper_tables.py --check` re-derives every number from its source." % (
               head, _dt.date.today().isoformat(), ", ".join(data_commits)), ""]
    order = ["table1", "table2", "table3", "table4", "caption_numbers", "tableS3_engine_qa", "tableS4_optimizer"]
    for name in order:
        t = tables[name]
        out += ["## %s" % t.title, ""]
        for line in t.preamble:
            out += [line, ""]
        if name == "table3":
            stages = list(dict.fromkeys(r.cells["stage"] for r in t.rows))
            ordered = [r for st in stages for r in t.rows if r.cells["stage"] == st]
            stage = None
            for r in ordered:
                if r.cells["stage"] != stage:
                    if stage is not None:
                        out.append("")
                    stage = r.cells["stage"]
                    out += ["### %s" % stage, ""]
                flag = r.cells["flag"] + (" / pending" if r.cells["value"].startswith("pending") else "")
                out.append("- **%s** (%s): %s  \n  flag `%s`; source `%s`; commit `%s`%s" % (
                    r.cells["metric"], r.cells["dataset"], r.cells["value"], flag, r.cells["source csv"], r.cells["commit"],
                    ("; command `%s`" % r.cells["command"]) if r.cells["command"] else ""))
            out.append("")
        elif name == "table2":
            for r in t.rows:
                out.append("- **%s** = %s %s -- %s. Justification: %s. Calibration set: %s. Check: %s. Source: %s (%s)." % (
                    r.cells["parameter"], r.cells["value"], r.cells["unit"] if r.cells["unit"] != "--" else "", r.cells["role"], r.cells["justification"],
                    r.cells["calibration set"], r.cells["check"], r.cells["source"], r.cells["commit"]))
            out.append("")
        elif name == "table1":
            for r in t.rows:
                out.append("- **%s**: %s; %s; %s; %s (source %s, %s)." % (
                    r.cells["id"], r.cells["what"], r.cells["acquisition"], r.cells["role in the paper"], r.cells["provenance / status"], r.cells["source"], r.cells["commit"]))
            out.append("")
        elif name == "table4":
            for r in t.rows:
                out.append("- %s / %s / %s: **%s s** -- %s (%s, %s)" % (r.cells["dataset"], r.cells["block"], r.cells["stage"], r.cells["seconds"], r.cells["note"], r.cells["source"], r.cells["commit"]))
            out.append("")
        elif name == "caption_numbers":
            fig = None
            for r in t.rows:
                if r.cells["figure"] != fig:
                    if fig is not None:
                        out.append("")
                    fig = r.cells["figure"]
                    out += ["### %s" % fig, ""]
                out.append("- (%s) %s: %s  \n  flag `%s`; source `%s`; commit `%s`" % (r.cells["panel"], r.cells["item"], r.cells["value"], r.cells["flag"], r.cells["source"], r.cells["commit"]))
            out.append("")
        elif name == "tableS3_engine_qa":
            for r in t.rows:
                if r.cells["max |rel| %"]:
                    out.append("- %s / %s (n = %s): max %s %%, median %s %%, p95 %s %% (%s)" % (r.cells["section"], r.cells["row"], r.cells["n"], r.cells["max |rel| %"], r.cells["median |rel| %"], r.cells["p95 |rel| %"], r.cells["where max"]))
                else:
                    out.append("- %s / %s: %s" % (r.cells["section"], r.cells["row"], r.cells["note"]))
            out.append("commit %s" % (t.rows[0].cells["commit"] if t.rows else "?"))
            out.append("")
        else:
            for r in t.rows:
                out.append("- %s -- %s: %s (`%s`, %s, flag `%s`)" % (r.cells["block"], r.cells["item"], r.cells["value"], r.cells["source csv"], r.cells["commit"], r.cells["flag"]))
            out.append("")
    out += [CHANGES_VS_REV3, OPEN_ITEMS, "## Sources read", ""]
    out += ["- `%s`" % s for s in sources]
    out.append("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


# ================================================================ build / check
def build_all(repo: Repo) -> Tuple[Dict[str, Table], Builder]:
    b = Builder(repo)
    tables = {"table1": b.table1(), "table2": b.table2(), "table3": b.table3(), "table4": b.table4(),
              "tableS3_engine_qa": b.tableS3(), "tableS4_optimizer": b.tableS4(), "caption_numbers": b.captions()}
    return tables, b


def data_commits_of(repo: Repo, sources: Sequence[str]) -> List[str]:
    out: List[str] = []
    for s in sources:
        if s.endswith(".csv") and repo.exists(s):
            c = repo.csv_commit(s)
            if c and c not in out:
                out.append(c)
    return out


def build(repo: Repo, out_dir: str, summary_path: Optional[str]) -> Dict[str, Table]:
    tables, b = build_all(repo)
    head = repo.head()
    commits = data_commits_of(repo, b.sources_read)
    for t in tables.values():
        write_table(t, out_dir, head, commits)
    if summary_path:
        os.makedirs(os.path.dirname(os.path.abspath(summary_path)), exist_ok=True)
        write_summary(summary_path, tables, head, commits, b.sources_read)
    return tables


def read_table_csv(path: str) -> Tuple[List[str], List[Dict[str, str]]]:
    with open(path, encoding="utf-8", newline="") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    rows = list(csv.DictReader(io.StringIO("".join(lines))))
    cols = list(rows[0].keys()) if rows else []
    return cols, rows


def check(repo: Repo, out_dir: str, verbose: bool = True) -> int:
    """Traceability report; returns the number of problems."""
    tables, b = build_all(repo)
    problems: List[str] = []
    report: List[str] = []
    n_rows = n_parts = n_pending = 0
    commits_seen: Dict[str, str] = {}
    for name, t in tables.items():
        path = os.path.join(out_dir, name + ".csv")
        md_path = os.path.join(out_dir, name + ".md")
        if not os.path.isfile(path):
            problems.append("%s: missing %s (run the build first)" % (name, path))
            continue
        cols, rows = read_table_csv(path)
        if cols != t.columns + [LOC]:
            problems.append("%s: columns differ from the builder: %s" % (name, cols))
        if len(rows) != len(t.rows):
            problems.append("%s: %d written rows vs %d rebuilt rows" % (name, len(rows), len(t.rows)))
        for i, (w, r) in enumerate(zip(rows, t.rows)):
            for c in t.columns:
                if w.get(c, "") != r.cells[c]:
                    problems.append("%s row %d col %r: written %r != rebuilt %r" % (name, i, c, w.get(c, ""), r.cells[c]))
                    break
            if w.get(LOC, "") != r.locator:
                problems.append("%s row %d: locator differs" % (name, i))
        # markdown renders: header / separator / every row with the same column count
        if os.path.isfile(md_path):
            with open(md_path, encoding="utf-8") as fh:
                md_lines = [ln.rstrip("\n") for ln in fh if ln.startswith("|")]
            ncol = len(t.columns)
            bad = [ln for ln in md_lines if len(re.split(r"(?<!\\)\|", ln)) - 2 != ncol]
            if bad:
                problems.append("%s.md: %d table lines do not have %d columns" % (name, len(bad), ncol))
            if len(md_lines) != len(t.rows) + 2:
                problems.append("%s.md: %d table lines for %d rows" % (name, len(md_lines), len(t.rows)))
        else:
            problems.append("%s: missing %s" % (name, md_path))
        # locators
        t_pend = t_parts = 0
        for i, w in enumerate(rows):
            n_rows += 1
            row_text = " ".join(w.get(c, "") for c in t.columns)
            commit_cell = w.get("commit", "")
            for part in split_locator(w.get(LOC, "")):
                n_parts += 1
                t_parts += 1
                if part.startswith("pending:"):
                    n_pending += 1
                    t_pend += 1
                p = b._verify_part(part, row_text, commit_cell)
                if p:
                    problems.append("%s row %d (%s): %s" % (name, i, (w.get("metric") or w.get("parameter") or w.get("item") or w.get("id") or w.get("stage") or "")[:60], p))
            for tok in re.findall(r"\b[0-9a-f]{7,40}\b", commit_cell):
                commits_seen.setdefault(tok, name)
        report.append("%-22s %3d rows, %4d locator parts, %2d pending" % (name, len(rows), t_parts, t_pend))
    # commits exist in the repository
    git_note = ""
    if repo.git_available():
        for c, where in sorted(commits_seen.items()):
            if repo.git_has(c) is False:
                problems.append("commit %s (cited in %s) does not exist in this repository" % (c, where))
        git_note = "%d distinct commits cited, all present in git" % len(commits_seen) if not any("does not exist" in p for p in problems) else "some cited commits are missing"
    else:
        git_note = "%d distinct commits cited (git unavailable: existence not checked)" % len(commits_seen)
    if verbose:
        print("paper_tables --check  (tree %s, tables in %s)" % (repo.head(), out_dir))
        for line in report:
            print("  " + line)
        print("  %d rows, %d locator parts checked, %d pending (campaign / record / export); %s" % (n_rows, n_parts, n_pending, git_note))
        if problems:
            print("  PROBLEMS (%d):" % len(problems))
            for p in problems:
                print("   - " + p)
        else:
            print("  every value traces to a committed CSV row or a notes section + commit; no problems")
    return len(problems)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--root", default=ROOT, help="repository root (default: the tree this script lives in)")
    p.add_argument("--out", default=None, help="output folder (default <root>/docs/figures/tables)")
    p.add_argument("--summary", default=None, help="results summary path (default <root>/docs/results-summary.md; 'none' to skip)")
    p.add_argument("--check", action="store_true", help="verify the written tables against the committed data (build first unless --no-build)")
    p.add_argument("--no-build", action="store_true", help="with --check: do not rebuild, only verify the existing files")
    a = p.parse_args(argv)
    repo = Repo(a.root)
    out_dir = a.out or os.path.join(repo.root, *TABLES.split("/"))
    summary = None if (a.summary or "").lower() == "none" else (a.summary or os.path.join(repo.root, "docs", "results-summary.md"))
    if not (a.check and a.no_build):
        tables = build(repo, out_dir, summary)
        print("wrote %d tables to %s%s" % (len(tables), out_dir, (" and %s" % summary) if summary else ""))
        for name, t in tables.items():
            n_pend = sum(1 for r in t.rows if r.cells.get("value", r.cells.get("seconds", "")).startswith("pending"))
            print("  %-22s %3d rows%s" % (name, len(t.rows), (" (%d pending)" % n_pend) if n_pend else ""))
    if a.check:
        return 1 if check(repo, out_dir) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
