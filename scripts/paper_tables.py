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
V2_COLLAPSED = OPT + "/v2_rows_collapsed.csv"
V3_RED = OPT + "/v3_gap_reduced.csv"                     # frozen objective (lambda_tail 1), reduced grid, exact bound
V3_RED_L0 = OPT + "/v3_gap_reduced_l0.csv"               # SA on pure V100 (lambda_tail 0): search quality
V3_INST = OPT + "/v3_gap_per_instance.csv"               # campaign grid (bound mostly HiGHS time-limited)
V3_INST_RED = OPT + "/v3_gap_per_instance_reduced.csv"
V3_INST_RED_L0 = OPT + "/v3_gap_per_instance_reduced_l0.csv"
V8_PACK_SUM = OPT + "/v8_packing_failures_summary.csv"
V8_PACK = OPT + "/v8_packing_failures.csv"
F8B_JSON = "docs/figures/data/f8b_patient_optimized.json"

# F8 / Table 3 patient arms reported in the main tables: as implanted (with its two
# tile-fit readings) and, per optimized family, the seed mean and the best seed only;
# the per-seed rows go to Table S4.
F8_MAIN_ARMS = (
    ("as_implanted", "as implanted"),
    ("as_implanted_fit_count8", "N = 8 count fit"),
    ("as_implanted_fit_6full_4half", "6 full + 4 half fit (PENDING OPERATIVE RECORD)"),
    ("optimized_rind5_sa_mean", "optimized for RIND5, mean of the SA seeds"),
    ("optimized_rind5_sa_best", "optimized for RIND5, best seed"),
    ("optimized_ctvgt_sa_mean", "optimized for CTV_GT, mean of the SA seeds"),
    ("optimized_ctvgt_sa_best", "optimized for CTV_GT, best seed"),
)
F8_TARGETS = (("RIND5(gtcore)", "RIND5_gtcore_V100", "RIND5_gtcore_D90_cGy"),
              ("CTV_GT", "CTV_GT_V100", "CTV_GT_D90_cGy"),
              ("IMPLANT_REACH(10)", "REACH10_V100", "REACH10_D90_cGy"))

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
        # post-processed tables (scripts/packing_failures.py): "this table generated at <hash>"
        for line in header:
            m = re.search(r"generated at\s+([0-9a-fA-F]{7,40})\b", line)
            if m:
                return m.group(1)
        return None

    def csv_input_commit(self, rel: str) -> Optional[str]:
        """``# campaign commit (inputs): <hash>`` of a post-processed table, if any."""
        header, _ = self.csv(rel)
        for line in header:
            m = re.search(r"commit \(inputs\):\s*([0-9a-fA-F]{7,40})", line)
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
    "pp2": lambda v: "%.2f" % _f(v),
    "pp2s": lambda v: "%+.2f" % _f(v),                       # signed difference, two decimals
    "p3": lambda v: "%.3f" % _f(v),                          # p value, three decimals
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
            "204 slices, 0.52 x 0.52 x 1.0 mm, contiguous (series 2)",
            "seed localisation, tile recovery, cavity, every dose comparison (F4-F6, F8a, Table 3)",
            "one clinical case; reference, not validation cohort; every row calibration-set",
            NOTES + "; " + DECISIONS, "bcb7a22",
            md_locator(NOTES, "204 slices", "0.52 × 0.52 × 1.0 mm") + " ;; " + md_locator(DECISIONS, "series 2, 204 slices"))
        row("PT-CT-s3 (= JD-CT-s3)",
            "second reconstruction of the same acquisition (series 3, a different kernel)",
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
        add(self._eligibility_row())
        add(self._capacity_row())
        for r in self._optimizer_rows():
            add(r)
        return t

    # ------------------------------------------------------- optimizer rows (campaign)
    def _mixed_row(self, stage: str, dataset: str, metric: str, value: str, truth: str,
                   sources: Sequence[str], locs: Sequence[str], flag: str,
                   command: Optional[str] = None, extra_commits: Sequence[str] = ()) -> Row:
        """A Table 3 row whose numbers come from several files (CSV cells,
        aggregates, notes / JSON tokens).  The commit cell lists every CSV
        header commit plus ``extra_commits`` (notes sections)."""
        R = self.repo
        commits: List[str] = []
        for s in sources:
            self._note(s)
            c = R.csv_commit(s) if s.endswith(".csv") and R.exists(s) else None
            if c and c not in commits:
                commits.append(c)
        for c in extra_commits:
            if c not in commits:
                commits.append(c)
        if command is None:
            command = next((R.csv_command(s) for s in sources if s.endswith(".csv") and R.exists(s) and R.csv_command(s)), "")
        cells = {"stage": stage, "dataset": dataset, "metric": metric, "value": value, "truth/reference": truth,
                 "source csv": ", ".join(sources), "command": command, "commit": ", ".join(commits) or "?", "flag": flag}
        return Row(cells, " ;; ".join(locs))

    def _need_tokens(self, rel: str, tokens: Sequence[str]) -> str:
        missing = self.tokens_present(rel, tokens)
        if missing:
            raise LookupError("%s: tokens not found %s" % (rel, missing))
        self._note(rel)
        return md_locator(rel, *tokens)

    def _eligibility_parts(self) -> Optional[Tuple[str, List[str], List[str], List[str]]]:
        """The measured F8(b) eligible wall (campaign patient run): value text,
        locators, sources, extra commits -- or None when the run has not landed."""
        R = self.repo
        need = ["eligible_fraction_area", "eligible_fraction_craniotomy_only", "seeds_nearest_face_eligible",
                "seeds_nearest_face_eligible_craniotomy_only", "n_seeds_scored"]
        if not (self.landed(F8_SUMMARY, need) and R.exists(F8B_JSON) and R.exists(F8_ARMS)):
            return None
        _, rows = R.csv(F8_SUMMARY)
        arm = "as_implanted" if any(r["arm"] == "as_implanted" for r in rows) else rows[0]["arm"]
        key = {"arm": arm}
        lk = [L(F8_SUMMARY, key, "eligible_fraction_craniotomy_only", "pct1"),
              L(F8_SUMMARY, key, "seeds_nearest_face_eligible_craniotomy_only", "int"),
              L(F8_SUMMARY, key, "n_seeds_scored", "int"),
              L(F8_SUMMARY, key, "eligible_fraction_area", "pct1"),
              L(F8_SUMMARY, key, "seeds_nearest_face_eligible", "int")]
        v = [x.value(R) for x in lk]
        info = R.json(F8B_JSON)
        rule = info.get("manufacturer_rule_tiles_on_eligible_area")
        area = info.get("eligible_area_mm2")
        hdr, _ = R.csv(F8_ARMS)
        m = re.search(r"\((\d+) mm2 of (\d+) mm2\)", " ".join(hdr))
        if rule is None or area is None or m is None:
            return None
        cand = [r for r in rows if r["arm"].startswith("optimized") and r.get("n_candidates", "").strip()]
        cand_txt, cand_loc = "", []
        if cand:
            ck = {"arm": cand[0]["arm"]}
            cand_txt = ", which gives C = %s candidates" % fmt("int", cand[0]["n_candidates"])
            cand_loc = [L(F8_SUMMARY, ck, "n_candidates", "int").encode()]
        value = ("eligible = the craniotomy wall (facing + line of sight through the opening, no implant term: %s %% of the wall area; "
                 "it holds the nearest wall face of only %s of the %s implanted seeds) UNION the wall within 10 mm of an implanted seed "
                 "(demonstrated reach) = %s %% of the wall area (%s of %s mm2); %s/%s implanted seeds have their nearest wall face eligible. "
                 "Manufacturer rule (eligible area / 4 cm2, rounded up): %d tiles on that eligible area (%s mm2); 34 on the whole %s mm2 wall. "
                 "Packing capacity at grid (h 2.5 mm, 6 spins; greedy maximal independent set, not exact): 13 tiles on the craniotomy wall alone "
                 "(not re-measured on the union%s). Not used: the CLI's three-term rule (+ implant visibility) = 11.8 %% of the area, rule 4 tiles, "
                 "capacity 10" % (v[0], v[1], v[2], v[3], m.group(1), m.group(2), v[4], v[2], int(rule), m.group(1), m.group(2), cand_txt))
        locs = [x.encode() for x in lk] + cand_loc + [
            self._need_tokens(F8B_JSON, ['"manufacturer_rule_tiles_on_eligible_area": %d' % int(rule), '"eligible_area_mm2": %s' % m.group(1)]),
            self._need_tokens(F8_ARMS, ["(%s mm2 of %s mm2)" % (m.group(1), m.group(2)), "11.8 %"]),
            self._need_tokens(NOTES, ["on the eligible area says 4 tiles (34 on the whole wall)", "tiles without the implant term (453 candidates",
                                      "**10 tiles** (290 candidates", "4cd9891"])]
        return value, locs, [F8_SUMMARY, F8B_JSON, F8_ARMS, NOTES], ["4cd9891"]

    def _eligibility_row(self) -> Row:
        parts = self._eligibility_parts()
        if parts is not None:
            value, locs, sources, extra = parts
            return self._mixed_row("placement optimisation", "PT-CT1mm (F8b eligible wall, gtcore cavity mesh)",
                                   "eligible wall for the patient arms (measured union fraction, fairness), manufacturer rule and packing capacity (area basis stated)",
                                   value, "geometry only (no dose); opening = skull defect U cavity mouth, 28.7 cm2", sources, locs, FLAG_CAL,
                                   extra_commits=extra)
        return self.t3_md("placement optimisation", "PT-CT1mm (F8b eligible wall)", "craniotomy-side eligibility and packing capacity at grid (h 2.5 mm, 6 spins; not exact)",
                          "facing + line-of-sight wall (no implant term) 26.5 % of the cavity area (11.8 % with the implant-visibility term); capacity 13 tiles (10 with the implant term); "
                          "the patient arms use that wall UNION the wall within 10 mm of an implanted seed (demonstrated reach; union fraction pending the patient run)",
                          "geometry only (no dose); opening = skull defect U cavity mouth, 28.7 cm2", FLAG_CAL, NOTES, "4cd9891",
                          ["0.265", "0.118", "10 tiles", "28.7 cm²", "4cd9891"],
                          'python -m gtcore.cli optimize "$GT_DATA_ROOT/<PT-CT1mm>" --tiles 8 --solver sa --eligible craniotomy',
                          [md_locator(DECISIONS, "26.5 %", "13 tiles at h 2.5 mm", "9/32", "demonstrated reach")])

    @staticmethod
    def _span(vals: Sequence[int]) -> str:
        lo, hi = min(vals), max(vals)
        return "%d" % lo if lo == hi else "%d-%d" % (lo, hi)

    def _capacity_row(self) -> Row:
        if self.landed(CAPACITY, ["capacity_at_grid"]):
            _, rows = self.repo.csv(CAPACITY)
            ok = [r for r in rows if r.get("status", "ok") == "ok"]
            groups: Dict[str, List[Dict[str, str]]] = {}
            for r in ok:
                g = ("synthetic " + r["cavity"].split("_")[-1]) if r.get("dataset") == "synthetic" else r.get("dataset", "?")
                groups.setdefault(g, []).append(r)
            parts, locs = [], []
            for g, rs in groups.items():
                caps = [int(round(float(r["capacity_at_grid"]))) for r in rs]
                common = max(set(caps), key=caps.count)
                odd = ["%s: %d" % (r["cavity"], c) for r, c in zip(rs, caps) if c != common]
                cap_txt = "%d%s" % (common, (" (" + ", ".join(odd) + ")") if odd else "")
                lab = g if len(rs) == 1 else "%s (%d cavities)" % (g, len(rs))
                if g == "PH8":
                    lab = "PH8 (registered PH0 wall, eligible %s mm2)" % fmt("int", rs[0]["eligible_area_mm2"])
                parts.append("%s: rule %s / capacity %s / area bound %s / placed %s" % (
                    lab, self._span([int(round(float(r["rule_tiles"]))) for r in rs]), cap_txt,
                    self._span([int(round(float(r["upper_bound_area"]))) for r in rs]), self._span([int(round(float(r["implanted"]))) for r in rs])))
                for r in rs:
                    key = {"dataset": r["dataset"], "cavity": r["cavity"]}
                    for c in ("rule_tiles", "capacity_at_grid", "upper_bound_area", "implanted"):
                        locs.append(L(CAPACITY, key, c, "int").encode())
            self._note(CAPACITY)
            cells = {"stage": "placement optimisation", "dataset": "18 synthetic cavities, PH8",
                     "metric": "manufacturer rule (eligible area / 4 cm2) vs packing capacity at grid vs placed (%s; per cavity in Table S4)" % ok[0].get("label", ""),
                     "value": "; ".join(parts) + "; the rule over-counts on every cavity (no packing loss); placed 8 = the generator's truth tiles (overlapping, off grid) / the hand-placed phantom tiles",
                     "truth/reference": "greedy maximal independent set on the planner-rule conflict graph (a lower bound: greedy / SA pack 8 on the x1.00 cavities where it reports 7)",
                     "source csv": CAPACITY, "command": self.repo.csv_command(CAPACITY), "commit": self.repo.csv_commit(CAPACITY) or "?", "flag": FLAG_SYN + " / " + FLAG_CAL}
            return Row(cells, " ;; ".join(locs))
        return self.t3_pending("placement optimisation", "synthetic cavities, PH8", "manufacturer rule vs capacity at grid vs placed",
                               "campaign (Phase 4 validation_optimize.py --capacity)", CAPACITY,
                               "greedy maximal independent set on the conflict graph (lower bound)", FLAG_SYN + " / " + FLAG_CAL,
                               "python scripts/validation_optimize.py --capacity ...")

    # ---- aggregates over the campaign CSVs (each quoted with an agg: locator)
    def _nstar(self) -> Optional[Dict[str, Dict[str, List[Optional[int]]]]]:
        """{scale: {arm: [N* per cavity]}}: smallest N with RIND5 D90 >= rx (None = never)."""
        if not self.landed(V2_COLLAPSED, ["RIND5_D90"]):
            return None
        _, rows = self.repo.csv(V2_COLLAPSED)
        cavs = sorted({r["cavity"] for r in rows})
        best: Dict[Tuple[str, str], int] = {}
        for r in rows:
            if r.get("status") != "ok" or r.get("arm") not in ("sa", "greedy", "uniform"):
                continue
            try:
                d90 = float(r["RIND5_D90"])
            except ValueError:
                continue
            if d90 >= 6000.0:
                k = (r["arm"], r["cavity"])
                best[k] = min(int(r["N"]), best.get(k, 10 ** 6))
        out: Dict[str, Dict[str, List[Optional[int]]]] = {}
        for c in cavs:
            sc = c.split("_")[-1]
            for arm in ("sa", "greedy", "uniform"):
                out.setdefault(sc, {}).setdefault(arm, []).append(best.get((arm, c)))
        return out

    @staticmethod
    def _count_text(vals: Sequence[Optional[int]]) -> str:
        cnt: Dict[Optional[int], int] = {}
        for v in vals:
            cnt[v] = cnt.get(v, 0) + 1
        keys = sorted([k for k in cnt if k is not None]) + ([None] if None in cnt else [])
        return ", ".join("%s on %d" % ("N %d" % k if k is not None else "never", cnt[k]) for k in keys) + " (of %d)" % len(vals)

    def _v3_summary(self, per_row: str, per_inst: str) -> Optional[Dict[str, Any]]:
        """Per-N mean gap, rows at the optimum, best seed at the optimum, RIND5
        differences, grid, from one V3 row set (reduced grid)."""
        R = self.repo
        if not (self.landed(per_row, ["gap"]) and self.landed(per_inst, ["gap_mean"])):
            return None
        _, rows = R.csv(per_row)
        _, inst = R.csv(per_inst)
        ok = [r for r in rows if r.get("status") == "ok" and r.get("gap", "").strip() not in ("", "nan")]
        if not ok or not inst:
            return None
        Ns = sorted({int(r["N"]) for r in ok})
        s: Dict[str, Any] = {"N": Ns, "grid": (ok[0].get("h_mm"), ok[0].get("n_spins"), ok[0].get("m_opt")),
                             "methods": sorted({r.get("bound_method", "") for r in ok}),
                             "statuses": sorted({r.get("bound_status", "") for r in ok}),
                             "n_inst": len(inst), "infeasible": sorted({(r["cavity"], r["N"]) for r in rows if r.get("status") != "ok"}),
                             "best_opt": sum(1 for r in inst if abs(float(r["gap_best"])) < 1e-9), "per_N": {}}
        s["worst"] = min(float(r["gap"]) for r in ok)
        for n in Ns:
            rn = [r for r in ok if int(r["N"]) == n]
            iN = [r for r in inst if int(r["N"]) == n]
            s["per_N"][n] = {
                "gap": sum(float(r["gap_mean"]) for r in iN) / len(iN) if iN else float("nan"),
                "at_opt": sum(1 for r in rn if abs(float(r["gap"])) < 1e-9), "rows": len(rn),
                "dD90": sum(float(r["RIND5_D90_sa"]) - float(r["RIND5_D90_bound"]) for r in rn) / len(rn),
                "dV100": 100.0 * sum(float(r["RIND5_V100_sa"]) - float(r["RIND5_V100_bound"]) for r in rn) / len(rn)}
        return s

    def _f8_rows(self) -> List[Dict[str, str]]:
        _, rows = self.repo.csv(F8_SUMMARY)
        return rows

    def _f8_main(self) -> List[Tuple[str, Dict[str, str]]]:
        """(label, row) for the arms the main tables report (seed mean and best
        seed per optimized family; the as-implanted arm and its two fits); all
        arms when none of the expected names is present."""
        rows = self._f8_rows()
        by = {r["arm"]: r for r in rows}
        out = [(lab, by[a]) for a, lab in F8_MAIN_ARMS if a in by]
        return out if out else [(r["arm"], r) for r in rows]

    def _f8_arm_text(self, label: str, r: Dict[str, str], all_rows: Sequence[Dict[str, str]]) -> Tuple[str, List[str]]:
        key = {"arm": r["arm"]}
        if r.get("sa_seed", "").strip():
            key["sa_seed"] = r["sa_seed"]
        bits, locs = [], []
        for lab, cv, cd in F8_TARGETS:
            if cv in r and cd in r:
                bits.append("%s %s %% / %s cGy" % (lab, fmt("pct1", r[cv]), fmt("cgy", r[cd])))
                locs += [L(F8_SUMMARY, key, cv, "pct1").encode(), L(F8_SUMMARY, key, cd, "cgy").encode()]
        arm = r["arm"]
        extra = []
        if arm.endswith("_sa_best") and r.get("sa_seed", "").strip():
            label += " (seed %s)" % r["sa_seed"]
        elif arm.endswith("_sa_mean"):
            fam = arm[: -len("_sa_mean")]
            seeds = [x.get("sa_seed", "") for x in all_rows if x["arm"].startswith(fam + "_sa_seed")]
            if seeds:
                label += " (seeds %s)" % ", ".join(seeds)
        if arm.startswith("as_implanted_fit") and r.get("n_tiles", "").strip():
            extra.append("%s tiles (%s full + %s half, %s degraded), %s seeds scored" % (
                r["n_tiles"], r.get("n_full", "?"), r.get("n_half", "?"), r.get("n_degraded", "?") or "0", r.get("n_seeds_scored", "?")))
        elif arm == "as_implanted" and r.get("n_seeds_scored", "").strip():
            extra.append("%s seeds" % r["n_seeds_scored"])
        elif r.get("n_tiles", "").strip():
            extra.append("%s tiles" % r["n_tiles"])
        if r.get("n_overlaps", "").strip():
            extra.append("overlaps %s" % r["n_overlaps"])
            locs.append(L(F8_SUMMARY, key, "n_overlaps", "str").encode())
        if r.get("solve_s", "").strip() not in ("", "nan"):
            extra.append("SA %s s" % fmt("s1", r["solve_s"]))
            locs.append(L(F8_SUMMARY, key, "solve_s", "s1").encode())
        return "%s: %s%s" % (label, ", ".join(bits), ("; " + "; ".join(extra)) if extra else ""), locs

    def _optimizer_rows(self) -> List[Row]:
        rows: List[Row] = []
        R = self.repo
        st = "placement optimisation"
        syn = "18 synthetic cavities (seeds 1-6 x scales 0.8 / 1.0 / 1.25)"
        # ---- synthetic benchmark: the pre-declared primary endpoint, as measured
        if self.landed(V2_PRIMARY, ["comparison"]):
            _, prim = R.csv(V2_PRIMARY)
            by = {r["comparison"].strip(): r for r in prim if r.get("comparison", "").strip()}
            p = by.get("sa - uniform at N*")
            b = by.get("sa_best - uniform at N*")
            if p is not None:
                kp = {"comparison": p["comparison"]}
                lks = [L(V2_PRIMARY, kp, "mean_pp", "pp2s"), L(V2_PRIMARY, kp, "ci_lo_pp", "pp2"), L(V2_PRIMARY, kp, "ci_hi_pp", "pp2"),
                       L(V2_PRIMARY, kp, "n", "int"), L(V2_PRIMARY, kp, "wilcoxon_p", "p3")]
                v = [x.value(R) for x in lks]
                sig = float(p["wilcoxon_p"]) < 0.05
                txt = "SA (mean of 3 seeds) - uniform at N*: %s pp [95 %% CI %s, %s], n = %s, Wilcoxon p = %s -- %s (pre-declared primary endpoint, reported as measured)" % (
                    v[0], v[1], v[2], v[3], v[4], "significant" if sig else "NOT significant")
                if b is not None:
                    kb = {"comparison": b["comparison"]}
                    lb = [L(V2_PRIMARY, kb, "mean_pp", "pp2s"), L(V2_PRIMARY, kb, "ci_lo_pp", "pp2"), L(V2_PRIMARY, kb, "ci_hi_pp", "pp2"), L(V2_PRIMARY, kb, "wilcoxon_p", "p3")]
                    vb = [x.value(R) for x in lb]
                    txt += "; best of the 3 SA seeds - uniform: %s pp [%s, %s], p = %s (secondary)" % tuple(vb)
                    lks += lb
                txt += "; N* = the largest N at which both arms packed (uniform never reaches RIND5 D90 >= rx: %s of %s flagged)" % (p.get("n_flagged", "?"), p.get("n", "?"))
                rows.append(self._mixed_row(st, syn, "synthetic benchmark, primary endpoint: SA - uniform on RIND5 V100 at N* (full-grid scoring)", txt,
                                            "uniform heuristic on the same candidate set", [V2_PRIMARY], [x.encode() for x in lks], FLAG_SYN))
        else:
            rows.append(self.t3_pending(st, syn, "synthetic benchmark: SA - uniform at N* (pp, 95 % CI), SA - greedy per N, gap to the same-candidate V100 bound",
                                        "campaign (Phase 4 V2 / V3; the rev-1 run of 2026-10-07 at cca2120 is superseded)", V2_PRIMARY,
                                        "uniform / greedy heuristics; exact V100 bound on the same candidate set", FLAG_SYN,
                                        "python scripts/validation_optimize.py --section v2,v3,v4,v5,v7,v8 --sa-seeds 3 --same-candidates --capacity"))
        # ---- SA - greedy at N 8 and N 10 (secondary)
        if self.landed(V2_PAIRED, ["comparison"]):
            _, paired = R.csv(V2_PAIRED)
            parts, locs = [], []
            for r in paired:
                if r.get("comparison", "").strip() != "sa - greedy" or r.get("endpoint", "RIND5_V100") != "RIND5_V100" or r["N"] not in ("8", "10"):
                    continue
                key = {"N": r["N"], "comparison": r["comparison"], "endpoint": r.get("endpoint", "RIND5_V100")}
                lk = [L(V2_PAIRED, key, "mean_pp", "pp2s"), L(V2_PAIRED, key, "ci_lo_pp", "pp2"), L(V2_PAIRED, key, "ci_hi_pp", "pp2"),
                      L(V2_PAIRED, key, "n", "int"), L(V2_PAIRED, key, "wilcoxon_p", "p3")]
                v = [x.value(R) for x in lk]
                parts.append("N = %s: %s pp [%s, %s], n = %s, p = %s" % (r["N"], v[0], v[1], v[2], v[3], v[4]))
                locs += [x.encode() for x in lk]
            if parts:
                rows.append(self._mixed_row(st, syn, "SA (mean of 3 seeds) - greedy on RIND5 V100 at N = 8 and N = 10 (paired over the cavities where both packed; 95 % CI, Wilcoxon; every N in Table S4)",
                                            "; ".join(parts), "greedy heuristic on the same candidate set", [V2_PAIRED], locs, FLAG_SYN))
        # ---- N*: smallest N with RIND5 D90 >= rx
        ns = self._nstar()
        if ns:
            parts = []
            for sc in sorted(ns, key=lambda s: float(s.lstrip("x"))):
                parts.append("%s: SA %s; greedy %s" % (sc, self._count_text(ns[sc]["sa"]), self._count_text(ns[sc]["greedy"])))
            uni = [v for sc in ns for v in ns[sc]["uniform"] if v is not None]
            rows.append(self._mixed_row(st, syn, "N*: smallest N with RIND5 D90 >= rx (6000 cGy) per cavity scale (N in 4-12)",
                                        "; ".join(parts) + "; uniform: %s" % ("never at any N where it packs" if not uni else self._count_text(uni)) +
                                        " (never = packing-limited: no N-packing on the candidate grid at the N needed)",
                                        "the generator's 8 truth tiles (overlapping, off grid) reach rx on the x0.80 cavities only", [V2_COLLAPSED],
                                        ["agg:" + V2_COLLAPSED + "#RIND5_D90"], FLAG_SYN))
        # ---- packing failures (V8): grid-capacity limits, not search failures
        if self.landed(V8_PACK_SUM, ["sa_failed_packing_exists"]):
            _, pk = R.csv(V8_PACK_SUM)
            parts, locs = [], []
            for r in pk:
                key = {"N": r["N"]}
                lk = [L(V8_PACK_SUM, key, c, "int") for c in ("sa_all_seeds_packed", "sa_failed_packing_exists", "sa_failed_no_packing_proved", "sa_failed_existence_unknown")]
                v = [x.value(R) for x in lk]
                parts.append("N %s: %s / %s / %s / %s" % (r["N"], v[0], v[1], v[2], v[3]))
                locs += [x.encode() for x in lk]
            none_known = all(int(r["sa_failed_packing_exists"]) == 0 for r in pk)
            n_cav = pk[0].get("n_cavities", "?")
            where = ""
            srcs = [V8_PACK_SUM]
            if self.landed(V8_PACK, ["sa_failure_class"]):
                _, per = R.csv(V8_PACK)
                fails = [r for r in per if int(r["N"]) <= 8 and int(r.get("n_sa_failed", "0") or 0) > 0]
                scales = sorted({r["cavity"].split("_")[-1] for r in fails})
                if fails:
                    where = " (%s: %s)" % ("all on the %s cavities" % scales[0] if len(scales) == 1 else "on the %s cavities" % " / ".join(scales),
                                           ", ".join(sorted({r["cavity"] for r in fails})))
                    locs.append("agg:" + V8_PACK + "#sa_failure_class")
                    srcs.append(V8_PACK)
            txt = ("per N, of %s cavities (SA packed with every seed / SA failed although a grid packing is known to exist / SA failed, the same-candidate bound "
                   "proved no N-packing on the grid / SA failed, existence unknown -- no bound run above N 8): %s. %s" % (
                       n_cav, "; ".join(parts),
                       "SA never failed where a packing is known to exist: every failure at N <= 8 is a proved grid-capacity limit%s; "
                       "the SA start is greedy (frozen solver), so this is the candidate grid's capacity, not the search" % where
                       if none_known else "some SA failures occur where a packing is known to exist (see Table S4)"))
            rows.append(self._mixed_row(st, syn, "packing failures (V8): SA failures to place N tiles, classified by whether an N-packing exists on the candidate grid",
                                        txt, "the same-candidate V100 bound (proves infeasibility) and packing_capacity; truth never counts (overlapping, off grid)",
                                        srcs, locs, FLAG_SYN,
                                        extra_commits=[c for c in [R.csv_input_commit(V8_PACK_SUM)] if c]))
        # ---- search quality: lambda_tail 0 SA vs the exact optimum (like-for-like)
        s0 = self._v3_summary(V3_RED_L0, V3_INST_RED_L0)
        if s0:
            per = "; ".join("N %d %.1f %% (rows at the optimum %d/%d)" % (n, 100 * d["gap"], d["at_opt"], d["rows"]) for n, d in s0["per_N"].items())
            rows.append(self._mixed_row(
                st, "synthetic cavities s1-s6 x1.00 (same candidate set; reduced grid)",
                "search quality: SA with lambda_tail 0 (pure V100, the bound's own objective) vs the exact V100 optimum, gap on the solver's V100",
                "reduced grid h %s mm / %s spins / M_opt %s, bound %s (%s): mean gap %s; best of 3 seeds at the optimum on %d/%d instances; worst single row %.1f %%; "
                "%d (cavity, N) instance(s) proved infeasible at N 8 on this grid" % (
                    s0["grid"][0], s0["grid"][1], s0["grid"][2], "/".join(s0["methods"]), "/".join(s0["statuses"]), per, s0["best_opt"], s0["n_inst"],
                    100 * s0["worst"], len(s0["infeasible"])),
                "solve_milp coverage-only bound, exact enumeration on the identical candidate ids", [V3_INST_RED_L0, V3_RED_L0],
                ["agg:" + V3_INST_RED_L0 + "#gap_mean", "agg:" + V3_RED_L0 + "#gap"], FLAG_SYN))
        # ---- objective trade-off: the frozen objective (lambda_tail 1) on the same instances
        s1 = self._v3_summary(V3_RED, V3_INST_RED)
        if s1:
            Ns = list(s1["per_N"])
            rows.append(self._mixed_row(
                st, "synthetic cavities s1-s6 x1.00 (same candidate set; reduced grid)",
                "objective trade-off: SA with the frozen objective (lambda_tail 1) vs the exact V100 optimum on the same instances",
                "mean gap on the solver's V100 %s %% at N %s, while the full-grid RIND5 D90 SA - bound is %s cGy and RIND5 V100 SA - bound %s pp: "
                "the tail term trades V100 for D90 by design (not a search failure; the lambda_tail 0 row is the search-quality measure)" % (
                    " / ".join("%.1f" % (100 * s1["per_N"][n]["gap"]) for n in Ns), " / ".join(str(n) for n in Ns),
                    " / ".join("%+.0f" % s1["per_N"][n]["dD90"] for n in Ns), " / ".join("%+.1f" % s1["per_N"][n]["dV100"] for n in Ns)),
                "solve_milp coverage-only bound (tail term dropped), exact enumeration", [V3_INST_RED, V3_RED],
                ["agg:" + V3_INST_RED + "#gap_mean", "agg:" + V3_RED + "#RIND5_D90_sa"], FLAG_SYN))
        # ---- phantom arms
        v6 = V6_PHANTOM if self.landed(V6_PHANTOM, ["RIND5_V100"]) else (V6_PHANTOM_RAW if self.landed(V6_PHANTOM_RAW, ["RIND5_V100"]) else None)
        if v6:
            labels = {"as_placed": "as placed (the pipeline's 8 recovered tiles; hand-placed, arbitrary positions)", "as_placed_conformed": "as placed, conformed to the wall",
                      "uniform": "uniform", "greedy": "greedy", "sa": "SA (mean of 3 seeds)", "sa_best": "SA best seed", "v100_bound": "V100 bound",
                      "continuous": "continuous"}
            _, arms = R.csv(v6)
            parts, locs = [], []
            for r in arms:
                if r.get("status") not in ("ok", "", None) or r.get("RIND5_V100", "").strip() in ("", "nan"):
                    continue
                key = {"arm": r["arm"], "N": r["N"]}
                lab = labels.get(r["arm"], r["arm"])
                if r["arm"] == "v100_bound" and r.get("solver_status", "").strip():
                    lab += " (%s)" % r["solver_status"]
                parts.append("%s: V100 %s %% / D90 %s cGy, %s overlaps" % (lab, fmt("pct1", r["RIND5_V100"]), fmt("cgy", r["RIND5_D90"]), fmt("str", r.get("n_overlaps", ""))))
                locs += [L(v6, key, "RIND5_V100", "pct1").encode(), L(v6, key, "RIND5_D90", "cgy").encode()]
            srcs = [v6]
            extra = ""
            if self.landed(V6_SWEEP, ["N"]):
                _, sw = R.csv(V6_SWEEP)
                hit = [r for r in sw if r.get("D90>=rx", "").strip() == "True"]
                n_max = max(int(r["N"]) for r in sw)
                extra += "; minimum N (greedy sweep, S5 surrogate): %s" % (("D90 >= rx at N = %s" % hit[0]["N"]) if hit else "D90 >= rx not reached for N <= %d" % n_max)
                locs.append((L(V6_SWEEP, {"N": hit[0]["N"]}, "D90>=rx", "str").encode()) if hit else "agg:" + V6_SWEEP + "#D90>=rx")
                srcs.append(V6_SWEEP)
            if R.exists(V6_INFO):
                info = R.json(V6_INFO)
                if isinstance(info, dict) and info.get("recommended_tiles") is not None and info.get("capacity_at_grid") is not None:
                    extra += "; manufacturer rule %s vs capacity at grid %s (area bound %s) vs placed %s" % (
                        info["recommended_tiles"], info["capacity_at_grid"], info.get("capacity_upper_bound_area", "?"), info.get("n_tiles_fitted", "?"))
                    locs.append(self._need_tokens(V6_INFO, ['"recommended_tiles": %s' % json.dumps(info["recommended_tiles"]),
                                                            '"capacity_at_grid": %s' % json.dumps(info["capacity_at_grid"])]))
                    srcs.append(V6_INFO)
            if parts:
                rows.append(self._mixed_row(st, "PH8 (wall = registered PH0; target RIND5(phantom cavity))",
                                            "phantom: as placed vs optimized, RIND5 V100 / D90 per arm (plastic, not tissue; NOT optimizer-vs-surgeon)",
                                            "; ".join(parts) + extra, "hand-placed tiles (arbitrary positions) vs the optimizer's 8 tiles on the same registered PH0 wall",
                                            srcs, locs, FLAG_CAL, command=R.csv_command(v6)))
        else:
            rows.append(self.t3_pending(st, "PH8 (wall = registered PH0)", "phantom: as placed vs optimized (RIND5 V100 / D90 per arm; minimum N; manufacturer rule)",
                                        "campaign (Phase 4 V6 on the registered tile-less wall; the rev-1 as_implanted 0.654 -> SA 0.849 on the +5 mm shell is superseded)", V6_PHANTOM,
                                        "hand-placed tiles vs the optimizer on the same wall", FLAG_CAL,
                                        'python scripts/validation_optimize.py --section v6 --phantom "$GT_DATA_ROOT/<PH8>" --ph0 "$GT_DATA_ROOT/<PH0>" --sa-seeds 3 --same-candidates'))
        # ---- patient: three arms (decisions a445cf7); seed mean and best seed per optimized family
        pds = "PT-CT1mm (gtcore cavity; eligible = craniotomy U 10 mm demonstrated reach)"
        if self.landed(F8_SUMMARY, ["arm"]):
            all_rows = self._f8_rows()
            parts, locs = [], []
            for lab, r in self._f8_main():
                txt, lk = self._f8_arm_text(lab, r, all_rows)
                parts.append(txt)
                locs += lk
            if parts:
                self._note(F8_SUMMARY)
                rows.append(Row({"stage": st, "dataset": pds,
                                 "metric": "patient: three arms -- as implanted (with its two tile-fit readings) / optimized for RIND5 / optimized for CTV_GT (mean of the SA seeds and the best seed) -- "
                                           "each scored on RIND5(gtcore cavity), CTV_GT and IMPLANT_REACH(10) (V100 / D90; overlaps; SA solve time; one case, illustration, not a cohort claim; per-seed rows in Table S4)",
                                 "value": "; ".join(parts),
                                 "truth/reference": "the implanted 32 seeds (physical axes) vs plan.optimize(mesh, 8, solver='sa') for the stated target on the same eligible wall; one dose_stats on the CT grid",
                                 "source csv": F8_SUMMARY, "command": R.csv_command(F8_SUMMARY), "commit": R.csv_commit(F8_SUMMARY) or "?", "flag": FLAG_CAL},
                                " ;; ".join(locs)))
        else:
            rows.append(self.t3_pending(st, pds,
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
        t.preamble = ["Phase 4 campaign (`scripts/validation_optimize.py`, frozen objective lambda_tail 1 / TAIL_Q 0.10, SA 8 restarts x 60 sweeps, 3 SA seeds, full-grid RIND5 "
                      "scoring on the 1 mm report grid, same-candidate V100 bound). Candidate grid h 4 mm / 6 spins / M_opt 1000 for V2, V4-V6 and capacity (V4 sweeps it; "
                      "V7 M_opt 4000); V3 is quoted on the reduced grid (h 6 mm / 2 spins / M_opt 300) where the bound is exact, with two SA row sets: lambda_tail 0 "
                      "(SA on pure V100 = the bound's objective: search quality) and lambda_tail 1 (the frozen objective: the objective trade-off); the campaign-grid V3 rows "
                      "(HiGHS time-limited, loose LP reference) are summarised, not quoted. Patient run: h 2.5 mm / 6 spins / M_opt 4000. Blocks whose Phase 4 file has not "
                      "landed are marked pending; the rev-1 files of 2026-10-07 (commit cca2120, shell-surrogate scoring, SA 3 x 40) are superseded and not quoted."]
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
                    "%s pp [%s, %s], n = %s, Wilcoxon p %s" % (fmt("pp2s", r["mean_pp"]), fmt("pp2", r["ci_lo_pp"]), fmt("pp2", r["ci_hi_pp"]), fmt("int", r["n"]), fmt("p3", r["wilcoxon_p"])),
                    V2_PAIRED, [L(V2_PAIRED, key, "mean_pp", "pp2s").encode(), L(V2_PAIRED, key, "ci_lo_pp", "pp2").encode(), L(V2_PAIRED, key, "ci_hi_pp", "pp2").encode(),
                                L(V2_PAIRED, key, "wilcoxon_p", "p3").encode()])
        else:
            pending("V2 paired comparisons", "SA - uniform, SA - greedy, V100 bound - SA, SA_best - greedy per N (pp, 95 % CI, Wilcoxon)", V2_PAIRED)
        if self.landed(V2_PRIMARY, ["comparison"]):
            _, rows = R.csv(V2_PRIMARY)
            for r in rows:
                if not r.get("comparison", "").strip():
                    continue
                key = {"comparison": r["comparison"]}
                add("V2 primary endpoint (pre-declared)", "%s (%s)" % (r["comparison"], r.get("endpoint", "RIND5_V100")),
                    "%s pp [%s, %s], n = %s, Wilcoxon p %s; flagged (uniform never reaches rx) %s, fallback N %s" % (
                        fmt("pp2s", r["mean_pp"]), fmt("pp2", r["ci_lo_pp"]), fmt("pp2", r["ci_hi_pp"]), fmt("int", r["n"]), fmt("p3", r["wilcoxon_p"]),
                        r.get("n_flagged", "?"), r.get("n_fallback_N", "?")),
                    V2_PRIMARY, [L(V2_PRIMARY, key, c, f).encode() for c, f in (("mean_pp", "pp2s"), ("ci_lo_pp", "pp2"), ("ci_hi_pp", "pp2"), ("n", "int"), ("wilcoxon_p", "p3"))])
        ns = self._nstar()
        if ns:
            _, crow = R.csv(V2_COLLAPSED)
            cavs = sorted({r["cavity"] for r in crow})
            for c in sorted(cavs, key=lambda c: (float(c.split("_x")[-1]), c)):
                sc = c.split("_")[-1]
                idx = [x for x in cavs if x.split("_")[-1] == sc].index(c)
                vals = {arm: ns[sc][arm][idx] for arm in ("sa", "greedy", "uniform")}
                add("V2 N*", c, "smallest N with RIND5 D90 >= rx: SA %s, greedy %s, uniform %s" % tuple(
                    ("N %d" % vals[a]) if vals[a] is not None else "never" for a in ("sa", "greedy", "uniform")),
                    V2_COLLAPSED, ["agg:" + V2_COLLAPSED + "#RIND5_D90"])
        v3_sets = (("V3 search quality (lambda_tail 0, reduced grid, exact bound)", V3_RED_L0, V3_INST_RED_L0),
                   ("V3 objective trade-off (lambda_tail 1, reduced grid, exact bound)", V3_RED, V3_INST_RED))
        any_v3 = False
        for block, per_row, per_inst in v3_sets:
            sm = self._v3_summary(per_row, per_inst)
            if not sm:
                continue
            any_v3 = True
            for n, d in sm["per_N"].items():
                add(block, "N = %d, mean over the instances" % n,
                    "gap %.1f %% (rows at the optimum %d/%d); full-grid RIND5 SA - bound: V100 %+.2f pp, D90 %+.0f cGy" % (100 * d["gap"], d["at_opt"], d["rows"], d["dV100"], d["dD90"]),
                    per_inst, ["agg:" + per_inst + "#gap_mean", "agg:" + per_row + "#gap"])
            _, inst = R.csv(per_inst)
            for r in inst:
                key = {"cavity": r["cavity"], "N": r["N"]}
                add(block, "%s, N = %s (C = %s)" % (r["cavity"], r["N"], r.get("n_candidates", "?")),
                    "gap mean %s %% / best %s %% / worst %s %% over %s SA seeds; RIND5 V100 gap mean %s %%; bound %s (%s)" % (
                        fmt("pct1", r["gap_mean"]), fmt("pct1", r["gap_best"]), fmt("pct1", r["gap_worst"]), r.get("n_seeds", "?"),
                        fmt("pct1", r["gap_RIND5_mean"]), r.get("bound_status", "?"), r.get("bound_method", "?")),
                    per_inst, [L(per_inst, key, c, "pct1").encode() for c in ("gap_mean", "gap_best", "gap_worst", "gap_RIND5_mean")])
            for cav, n in sm["infeasible"]:
                add(block, "%s, N = %s" % (cav, n), "no N-packing on this candidate grid (bound proved infeasible; SA failed with every seed)", per_row, ["fixed:" + per_row])
        if self.landed(V3_INST, ["gap_mean"]):
            _, inst = R.csv(V3_INST)
            tl = sum(1 for r in inst if r.get("bound_status") == "time_limit")
            mg = sum(float(r["gap_mean"]) for r in inst) / len(inst)
            add("V3 campaign grid (not quoted)", "h 4 mm / 6 spins / M_opt 1000, bound HiGHS 300 s",
                "bound time-limited on %d of %d instances, so the reference is the loose LP bound: mean gap %.1f %% -- uninformative; the reduced-grid rows above are the measure" % (tl, len(inst), 100 * mg),
                V3_INST, ["agg:" + V3_INST + "#gap_mean"])
            any_v3 = True
        if not any_v3:
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
            if self.landed(V5_SENS, ["RIND5_V100"]):
                _, sens = R.csv(V5_SENS)
                vals: Dict[Tuple[str, str], List[Tuple[str, float]]] = {}
                for r in sens:
                    if r.get("status") == "ok" and r.get("RIND5_V100", "").strip() not in ("", "nan"):
                        vals.setdefault((r["arm"], r["cavity"]), []).append((r["perturbation"], float(r["RIND5_V100"])))
                for arm in sorted({a for a, _ in vals}, key=lambda a: {"sa": 0, "greedy": 1}.get(a, 2)):
                    sp = [100 * (max(v for _, v in xs) - min(v for _, v in xs)) for (a, _c), xs in sorted(vals.items()) if a == arm]
                    mu = sum(sp) / len(sp)
                    sd = (sum((x - mu) ** 2 for x in sp) / (len(sp) - 1)) ** 0.5 if len(sp) > 1 else 0.0
                    noms = [v for (a, _c), xs in sorted(vals.items()) if a == arm for p_, v in xs if p_ == "nominal"]
                    note = ""
                    if arm == "sa":
                        n_ceil = sum(1 for v in noms if v >= 0.9995)
                        if n_ceil:
                            note = ("; CEILING CAVEAT: SA's nominal RIND5 V100 rounds to 1.000 on %d of %d cavities, so its small spread is partly a ceiling effect"
                                    % (n_ceil, len(noms)))
                    add("V5 sensitivity", "%s: spread of RIND5 V100 over the perturbations (max - min incl. nominal, per cavity)" % arm,
                        "%.2f +/- %.2f pp over %d cavities (nominal %s)%s" % (mu, sd, len(sp), " / ".join("%.3f" % v for v in noms), note),
                        V5_SENS, ["agg:" + V5_SENS + "#RIND5_V100"])
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
                    ("N = %s" % hit[0]["N"]) if hit else "not reached within the sweep (N <= %d)" % max(int(r["N"]) for r in sw), V6_SWEEP,
                    [L(V6_SWEEP, {"N": hit[0]["N"]}, "D90>=rx", "str").encode()] if hit else ["agg:" + V6_SWEEP + "#D90>=rx"], FLAG_CAL)
            if R.exists(V6_INFO):
                info = R.json(V6_INFO)
                if isinstance(info, dict) and info.get("recommended_tiles") is not None:
                    self._note(V6_INFO)
                    t.add(Row({"block": "V6 phantom (as placed vs optimized)", "item": "wall, eligibility, manufacturer rule vs capacity",
                               "value": "wall = %s (%s faces, %.0f mm2); eligible %.0f mm2 (%s faces); manufacturer rule %s vs capacity at grid %s (area bound %s) vs placed %s; "
                                        "localized seeds %.2f mm off the wall (mean); conformer substitution %+.2f pp RIND5 V100" % (
                                            info.get("wall", "?"), info.get("n_faces", "?"), float(info.get("mesh_area_mm2", float("nan"))), float(info.get("eligible_area_mm2", float("nan"))),
                                            info.get("eligible_faces", "?"), info["recommended_tiles"], info.get("capacity_at_grid", "?"), info.get("capacity_upper_bound_area", "?"),
                                            info.get("n_tiles_fitted", "?"), float(info.get("mean_seed_wall_mm", float("nan"))), float(info.get("conformer_delta_V100_pp", float("nan")))),
                               "source csv": V6_INFO, "commit": R.csv_commit(v6) or "?", "flag": FLAG_CAL},
                              self._need_tokens(V6_INFO, ['"recommended_tiles": %s' % json.dumps(info["recommended_tiles"]),
                                                          '"capacity_at_grid": %s' % json.dumps(info.get("capacity_at_grid"))])))
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
        if self.landed(V8_PACK_SUM, ["sa_failed_packing_exists"]):
            _, pk = R.csv(V8_PACK_SUM)
            for r in pk:
                key = {"N": r["N"]}
                cols = ("sa_all_seeds_packed", "sa_failed_packing_exists", "sa_failed_no_packing_proved", "sa_failed_existence_unknown", "greedy_packed")
                add("V8 packing failures", "N = %s (%s cavities)" % (r["N"], r.get("n_cavities", "?")),
                    "SA packed with every seed %s; SA failed although a grid packing is known %s; failed, no N-packing proved by the bound %s; failed, existence unknown %s; "
                    "greedy packed %s; bound packed / run %s / %s" % tuple([r[c] for c in cols] + [r.get("bound_packed", "?"), r.get("bound_run", "?")]),
                    V8_PACK_SUM, [L(V8_PACK_SUM, key, c, "int").encode() for c in cols])
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
            rows = self._f8_rows()
            for r in rows:
                txt, locs = self._f8_arm_text(r["arm"], r, rows)
                add("patient (F8)", "%s%s" % (r["arm"], (" seed %s" % r["sa_seed"]) if r.get("sa_seed", "").strip() else ""),
                    txt.split(": ", 1)[1] if ": " in txt else txt, F8_SUMMARY, locs, FLAG_CAL)
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
        if self.landed(F8_SUMMARY, ["arm"]):
            all_rows = self._f8_rows()
            self._note(F8_SUMMARY)
            c8 = R.csv_commit(F8_SUMMARY) or "?"
            for lab, r in self._f8_main():
                txt, locs = self._f8_arm_text(lab, r, all_rows)
                panel = "a" if r["arm"].startswith("as_impl") else "b"
                item = ("as implanted, scored by the patient run on the three targets (title numbers)" if panel == "a"
                        else "optimized 8 tiles (title numbers)") + " -- " + txt.split(": ", 1)[0]
                t.add(Row({"figure": "F8", "panel": panel, "item": item, "value": txt.split(": ", 1)[1] if ": " in txt else txt,
                           "source": F8_SUMMARY, "commit": c8, "flag": FLAG_CAL}, " ;; ".join(locs)))
        else:
            t.add(Row({"figure": "F8", "panel": "b", "item": "optimized arms (for RIND5; for CTV_GT): title numbers V100 / D90 on the full rind, CTV_GT and IMPLANT_REACH(10); overlaps; solve time",
                       "value": "pending campaign (scripts/patient_optimize.py -> f8_patient_summary.csv, f8b_patient_optimized.npz)", "source": F8_SUMMARY + " (expected)",
                       "commit": "--", "flag": FLAG_CAL}, "pending:" + F8_SUMMARY))
        el = self._eligibility_parts()
        if el is not None:
            value, locs, sources, extra = el
            commits = list(dict.fromkeys([c for c in (R.csv_commit(s) for s in sources if s.endswith(".csv")) if c] + list(extra)))
            t.add(Row({"figure": "F8", "panel": "b", "item": "eligible wall for the optimizer (measured on the patient run), manufacturer rule and capacity with their area basis",
                       "value": value, "source": ", ".join(sources), "commit": ", ".join(commits), "flag": FLAG_CAL}, " ;; ".join(locs)))
        else:
            capmd("F8", "b", "eligible wall for the optimizer", "craniotomy wall (facing + line of sight, no implant term; 26.5 % of the cavity area, holds 9/32 implanted seeds) UNION the wall within 10 mm of an implanted seed (demonstrated reach); packing capacity 13 tiles at h 2.5 mm / 6 spins on the craniotomy wall",
                  DECISIONS, "a445cf7", ["26.5 %", "13 tiles at h 2.5 mm", "9/32", "demonstrated reach"])
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
- Held-out scan: the planned held-out run on the separately delivered MRI/CT folder -> **none**: that folder is the same patient (CT identity certain); one clinical case, every real-data row calibration-set; `scripts/heldout_case54.py` dropped (decisions 2026-11-03).
- Seed match vs the TPS contours: 29/30, residual "0.23 mm median, 0.9 mm max" -> **30/30**, median 0.15 mm, p95 0.73, max 0.89 mm; detections 31 -> **32** = the plan's 32 sources (vault filter in physical mm, fad8701; fe0dc81).
- CTV_GT volume on the dose grid: 21.6 cc (+14 %) -> 19.39 cc (+2.0 %) with the supersampled rasteriser; the MIM volume check itself is still pending (d40bb32).
- Implied S_K (diagnostic only): 3.76 U with 30 contoured seeds -> 3.54 U with the plan's 32 sources (physical axes), 3.53 U with S-I axes; nominal 3.5 U kept (fe0dc81, 378b819).
- CTV_GT D90, gtcore at the 32 gtcore seeds: 6051 (31 seeds) / 6168 cGy -> **6167 cGy** (physical axes), 6288 cGy with the TPS axis convention; RTDOSE 6365 cGy (378b819).
- F6 D90 triplet under the RTDOSE: 63.7 / 45.9 / 28.6 Gy (rev 3) and 63.0 / 58.4 / 46.2 Gy (v7 designation) -> **63.6 Gy (CTV_GT) / 46.5 Gy (RIND5(Cav_Post, clip Brain)) / 27.2 Gy (RIND5(gtcore cavity)) / 68.8 Gy (IMPLANT_REACH(10); 58.6 Gy at r = 12)**; CTV_GT covers 53 % of the full rind (not 56 %) (378b819).
- Synthetic cavity Dice at `HULL_REACH_MM` 14: 0.839 -> **0.896** with the mm-correct geodesic growth (not re-tuned); clinical Dice 0.668 -> 0.665 (unchanged within noise) (0efe5eb, 378b819).
- Formalism: "a 1D plan would give 1.29 -> 0.96" -> far field only; under the test's selection 0.95 -> 1.05. **MIM is 2D with every seed axis along S-I** (common fitted axis 2.2 deg from S-I; physical axes 64.5 deg from it); the residual +-6 % angular trend of rev 3 is that axis convention, not the dataset, grid or S_K (aaddf65).
- Phantom "localized seeds 0.84 mm off the wall mesh" -> a reference-surface artifact (the PH8 body mask includes the tiles: -0.68 mm to the tile face); +3.05 mm to the registered tile-less wall, 26/32 in the hydrated band (448a6b3).
- Phantom cavity "lumen = filled minus body" -> impossible for this print (no plateau); gtcore's cavity rule on the registered twin: 55.3 cc, RIND5 47.7 cc (4cd9891).
- F8 arms: the mock run's 0.67 -> 0.81 (rind) / 0.92 -> 0.91 (CTV_GT) is never quoted. Two arms -> **three** (decisions a445cf7), measured at 085fe60 (3 SA seeds, 8 tiles, 0 overlaps each): as implanted `RIND5(gtcore)` V100 38.7 % / D90 2653 cGy, `CTV_GT` 91.7 % / 6167 cGy, `IMPLANT_REACH(10)` 96.0 % / 6700 cGy; **optimized for `RIND5`** (all 3 seeds identical) 50.3 % / 3141 | 85.5 % / 5706 | 88.5 % / 5898; **optimized for `CTV_GT`** seed mean 42.9 % / 3159 | 98.0 % / 6769 | 98.8 % / 7004, best seed 41.6 % / 3165 | 98.2 % / 6783 | 99.2 % / 7073. Given the physician's target the optimizer improves on the implant on all three targets; given the full rind it trades `CTV_GT` (-6.2 pp) for rind coverage. The main tables report the seed mean and the best seed per family; per-seed rows are in Table S4. The as-implanted 6 full + 4 half fit owns all 32 seeds (row = as implanted; pending the operative record); the N = 8 count fit scores the 28 seeds it owns (sensitivity line).
- F8(b) eligibility: facing + line of sight only (26.5 % of the wall, 9/32 implanted seeds' nearest face on it; the 4748ccb trial run on that wall cost -17 pp on `CTV_GT`, not quoted) -> that wall **UNION the wall within 10 mm of an implanted seed** = **67.3 %** of the wall (8919 of 13 261 mm2), 32/32 implanted seeds on eligible wall. Manufacturer rule: the "4 tiles on the eligible area (34 on the whole wall)" of 4cd9891 was the three-term CLI wall (11.8 %, 1566 mm2) -> **23 tiles on the union eligible area** (34 on the whole wall); packing capacity at grid 13 tiles was measured on the craniotomy wall alone (h 2.5 mm, 6 spins) and is not re-measured on the union (085fe60, 4cd9891).
- Optimizer headline: rev-1 SA - uniform **+22.8 pp** V100 [19.3, 26.4] on the +5 mm shell surrogate (cca2120) -> pre-declared primary endpoint on the volumetric `RIND5` with full-grid scoring: SA (seed mean) - uniform at N* **+1.19 pp [-0.71, 3.09], n 15, Wilcoxon p 0.055 -- not significant**; best-of-3 +2.14 pp [0.23, 4.05], p 0.010 (secondary). The clear advantages are over greedy (N 8 +2.45 pp, p 0.002; N 10 +10.16 pp, p 0.031) and in reaching RIND5 D90 >= rx with fewer tiles (N* SA 10 vs greedy 12 on four of six x1.25 cavities) (d2d455e).
- Packing failures: "SA fails to place N tiles although a truth configuration exists" (first V2 reading) -> **grid-capacity limits**: SA never failed where a packing is known to exist on the candidate grid; every SA failure at N <= 8 is on a cavity where the same-candidate bound proved no N-packing (N 6: 5/18, N 8: 6/18; x0.80 cavities); N 10 / 12 partly unknown (no bound run above N 8). The truth configurations are continuous, overlapping placements, not grid packings (packing_failures.py at 085fe60 on d2d455e).
- Search quality vs the bound: the campaign-grid gap (HiGHS time-limited on 15/18 instances, loose LP reference, mean -42.8 %) is not quoted -> reduced grid, exact enumeration: with lambda_tail 0 (SA on pure V100, like-for-like) the mean gap is **-1.0 / -0.5 / -1.8 %** at N 4 / 6 / 8 and the best of 3 seeds is at the optimum on **14/15** instances (085fe60); with the frozen lambda_tail 1 the gap is -52.7 / -18.6 / -3.1 % while RIND5 D90 is +1122 / +1603 / -181 cGy above the bound's selection -- an objective trade-off, not search (d2d455e). (Rev 1, pure V100, 3 x 40: mean -1.98 %.)
- Phantom arms: rev-1 PH8 body mesh (which includes the collagen tiles) and +5 mm shell scoring (as_implanted V100 0.654 -> SA 0.849; min-N 10 vs rule 12) -> **the registered tile-less PH0 wall** scored on `RIND5(phantom cavity)` 47.7 cc: as placed V100 44.3 % / D90 2187 cGy (3 overlaps), conformed 45.2 % / 2161, uniform 48.9 % / 3408, greedy 48.3 % / 3290, SA 54.6 % / 3529 (0 overlaps), V100 bound (time-limited) 59.2 % / 1866; greedy min-N sweep: D90 >= rx not reached for N <= 10; manufacturer rule 14 vs capacity at grid 10 vs placed 8 (d2d455e).
- Capacity rows (new): manufacturer rule (area / 4 cm2) vs packing capacity at grid (h 4 mm, 6 spins; greedy maximal independent set, a lower bound) vs placed: x0.80 rule 7-8 / capacity 4 / placed 8 (truth, overlapping), x1.00 11-12 / 7 (s4: 9) / 8, x1.25 17-19 / 10 / 8, PH8 14 / 10 / 8 -- the rule over-counts everywhere (no packing loss) (d2d455e).
- V5 sensitivity: SA's RIND5 V100 spread 0.09 +/- 0.06 pp (greedy 3.41 +/- 3.15, truth 4.63 +/- 0.10; Kendall tau 1.00 in all 18 cells) carries a **ceiling caveat**: SA's nominal V100 is 1.000 on all three cavities (d2d455e).
- F2(c): the kappa range shown is stated (`KAPPA_RANGE` (-0.08, 0.5) per mm; wall-conformed render at 1/17 per mm). F9: no STL, reduced or cut. S2: PT-CT2mm lost; substitutes PT-CT-s3 vs PT-CT1mm and a decimated thin-cut.
"""

OPEN_ITEMS = """\
## Open items (what no committed number can replace)

1. **MIM DVH / plan export** -- `verify_against_tps` (structure volumes and D90 within 2 %) has not run on real data; every dose row of Table 3 carries `TPS volume check pending`. Command: `python scripts/validation_clinical.py ... --mim-dvh "$GT_DATA_ROOT/<MIM DVH export file>"`.
2. **RTPLAN** -- per-seed S_K, reference time and `ControlPointOrientation` (expected (0, 0, +-1) or absent for every source); `scripts/formalism_check.py ... --rtplan "$GT_DATA_ROOT/<PT-RT RTPLAN>"` adds the `b6_axes_rtplan` variant; a `tps_rtplan` seed set for the gamma script.
3. **PT-CT2mm** -- missing since the 2026-11-03 folder move; S2's real-data panel uses the two stated substitutes; the 0.51 / 0.40 / 0.63 mm SDs of the degraded-export band are quoted, not regenerable.
4. **Operative record** -- were two tiles cut? Decides between the N = 8 count-known row (7/8, one degraded completion) and the 6 full + 4 half row (32/32), and whether F4(b) / F8(a) draw 6 sheets + 8 red seeds or 6 sheets + 4 half-tile pairs (drawn from the pair geometry: the `RigidTile("half")` template is cut across the seed rows, the observed halves along them).
5. **Optimizer follow-ups (campaign merged at 056334c; no table row is pending on it)** -- (a) which F8 arm pair the main-text panel shows (suggested: as implanted vs optimized for `CTV_GT`; the `RIND5` arm in S4 / Table 3) is Jacob's choice; (b) packing existence at N 10 / 12 on the x1.00 cavities (and one x1.25 at N 12) is unknown -- no bound run above N 8; (c) packing capacity on the union eligible wall of the patient (67.3 %) was not measured (13 tiles is the craniotomy wall alone); (d) a feasibility-first SA start is the obvious fix for the grid-capacity failures and is future work, not done.
6. **F7 recordings** -- three planner frames by hand from `gt plan "$GT_DATA_ROOT/<PH8>" --hrctv` on the tagged build (fixed window size, same crop); not rendered by the figure script.
7. **Photos** -- F2(a) tile with scale, F3(b) phantom with the eight tiles, F3(a) MRI crops (cavity outlines), F9 strip (CT / 3T MRI / Artec / TRIOS of the tile-less phantom) or F9 cut to a Discussion sentence.
8. **IRB / consent statement** (Table 1 placeholder) and the **Zenodo DOI** of the tagged release (`v1.0-medphys`).
9. **TG-43U1S2 erratum text** (Med Phys 45:971) -- Table XII rests on Paper 1's transcription (validated indirectly); Table XXIII is the 2017 print. Paper 1's F(7 cm, 80 deg) slip (0.999 -> 0.998) to be fixed in that repository.
10. **Cavity across the loop** -- volumes done (`scripts/cavity_change.py`, `docs/figures/data/f3_cavity_volumes.csv`: printed cavity 55.3 cc, `Cav_Post` 35.1 cc, gtcore 41.3 cc); the intra-op -> post-op wall displacement was attempted and is not credible, not quoted; manual MRI cavity contours from Jacob would add MRI volumes (no MRI segmentation in gtcore).
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
