#!/usr/bin/env python
"""Engine QA cross-check (Paper 2, Phase 3, task ``engine-qa``; Supplement S3).

Pins ``gtcore.dose.engine.TG43Engine("tg43u1s2")`` against

1. the TG-43U1S2 consensus tables as embedded in Paper 1's engine
   (``DoseInterpolator`` in ``GammaTile.py``, dataset ``"TG43U1S2"``) --
   entry-by-entry table comparison, every difference listed;
2. the report's QA dose-rate tables for the IsoRay CS-1 Rev2: Table XII
   (polar; erratum-corrected values as transcribed in Paper 1's
   ``tests/tg43_qa_check.py``) and Table XXIII (along-away, as printed in
   the 2017 report), for both engines;
3. Paper 1's engine itself, point-wise: an (r, theta) grid r = 0.3-10 cm,
   theta = 0-90 deg, and a random 32-seed cloud at 1000 points
   (max / median / 95th-percentile relative difference), plus probes of
   the behaviours ``docs/tg43-port-notes.md`` records (near-axis zero,
   source-tip singularity, anisotropy NaN holes, the 10 cm freeze).

Paper 1's module imports slicer/vtk/qt and cannot be imported; its
``DoseInterpolator`` class block is sliced out with ``ast`` and executed
alone (``gtcore.dose.qa_data.extract_paper1_interpolator``).  The class is
never copied into gtcore.

Usage (from the repository root, with PYTHONPATH set to it)::

    python scripts/engine_crosscheck.py --paper1 <GammaTileDosimetry clone or GammaTile.py>
        [--write-fixtures]              # regenerate gtcore/dose/qa_data/*.json|*.csv
        [--table-xxiii-text FILE]       # with --write-fixtures: pypdf text of the
                                        # report (IROC-hosted PDF) -> along-away fixture
        [--out docs/figures/data/engine_crosscheck.csv]
        [--table-xii-out docs/figures/data/engine_qa_table_xii.csv]
        [--table-xxiii-out docs/figures/data/engine_qa_table_xxiii.csv]
        [--points-out FILE.csv]         # optional per-point grid dump
        [--seed 0] [--n-points 1000] [--n-seeds 32]

Outputs carry a ``#`` header naming the script, the command (machine paths
replaced by placeholders), the commit, and ``gtcore.__file__`` relative to
the repository, so the numbers can be traced to the tree they came from.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

# Import-trap rule (AGENT_COMMON): the venv has an editable install of the
# main checkout, so a script run by path must put ITS OWN tree first.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import gtcore  # noqa: E402
from gtcore.dose import TG43Engine, dose_at_points  # noqa: E402
from gtcore.dose.engine import TG43U1S2_CONSENSUS  # noqa: E402
from gtcore.dose.qa_data import (  # noqa: E402
    ALONG_AWAY_FIXTURE, CONSENSUS_FIXTURE, TABLE_XII_FIXTURE,
    extract_paper1_interpolator, extract_paper1_table_xii, load_along_away,
    load_table_xii, paper1_engine_path)

PAPER1_REPO = "github.com/Peelyboneheader/GammaTileDosimetry"
REPORT = ("Rivard MJ et al., Supplement 2 for the 2004 update of the AAPM "
          "Task Group No. 43 Report: Joint recommendations by the AAPM and "
          "GEC-ESTRO. Med Phys 44(9):e297-e338 (2017). doi:10.1002/mp.12430")
ERRATUM = ("Rivard MJ et al., Erratum: Supplement 2 for the 2004 update of "
           "the AAPM Task Group No. 43 Report. Med Phys 45(2):971-974 (2018). "
           "doi:10.1002/mp.12728")
IROC_PDF = "irochouston.mdanderson.org/RPc/BrachySeeds/TG-43-Supplement-2.pdf"
NL = chr(10)


# ------------------------------------------------------------------ helpers
def git(args, cwd):
    try:
        return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                              capture_output=True, text=True).stdout.strip()
    except Exception:  # pragma: no cover - git absent / not a repo
        return ""


def commit_label(cwd):
    h = git(["rev-parse", "--short=9", "HEAD"], cwd)
    if not h:
        return "unknown"
    dirty = git(["status", "--porcelain", "--untracked-files=no"], cwd)
    return h + ("+dirty" if dirty else "")


def sanitized_command(argv):
    """The command line with machine paths replaced by placeholders."""
    placeholders = {"--paper1": "<GammaTileDosimetry clone>",
                    "--table-xxiii-text": "<pypdf text of the IROC-hosted report PDF>",
                    "--points-out": "<per-point grid csv>"}
    out = []
    skip = False
    for a in argv[1:]:
        if skip:
            skip = False
            continue
        if a in placeholders:
            out += [a, placeholders[a]]
            skip = True
        elif any(a.startswith(k + "=") for k in placeholders):
            k = a.split("=", 1)[0]
            out.append(k + "=" + placeholders[k])
        else:
            out.append(a)
    return "python scripts/engine_crosscheck.py " + " ".join(out)


def write_comments(f, lines):
    for line in lines:
        f.write("# " + line + NL)


def rel_pct(a, b):
    """100 * (a / b - 1) with b == 0 -> nan."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b != 0.0, 100.0 * (a / b - 1.0), np.nan)


def stats(rel, where=None):
    """max / median / p95 of |rel| plus the signed extreme and its location."""
    rel = np.asarray(rel, float)
    ok = np.isfinite(rel)
    if where is not None:
        ok &= np.asarray(where, bool)
    v = rel[ok]
    if v.size == 0:
        return dict(n=0, max_abs=np.nan, median_abs=np.nan, p95_abs=np.nan,
                    max_signed=np.nan, idx=None)
    i = int(np.argmax(np.abs(v)))
    return dict(n=int(v.size), max_abs=float(np.abs(v).max()),
                median_abs=float(np.median(np.abs(v))),
                p95_abs=float(np.percentile(np.abs(v), 95)),
                max_signed=float(v[i]), idx=int(np.flatnonzero(ok)[i]))


def fmt(x, nd=4):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return ""
    return ("%." + str(nd) + "g") % x


# ------------------------------------------------------------ Paper 1 side
class Paper1:
    """Thin adapter over the extracted ``DoseInterpolator`` (TG43U1S2)."""

    def __init__(self, cls):
        self.cls = cls
        self.obj = cls(dataset="TG43U1S2")
        self.conv = float(cls.DOSE_CONVERSION_FACTOR)   # cGy per (cGy/h/U)
        self.sk_u = float(cls.SEED_STRENGTH_U)
        self.tau_h = float(cls.CS131_MEAN_LIFE_H)

    def rate(self, theta_deg, r_cm):
        """Dose rate per unit S_K [cGy h^-1 U^-1] (conversion divided out)."""
        th = np.atleast_1d(np.asarray(theta_deg, float))
        r = np.atleast_1d(np.asarray(r_cm, float))
        th, r = np.broadcast_arrays(th, r)
        return self.obj.dose_at_points(th.ravel(), r.ravel()).reshape(th.shape) \
            / self.conv

    def total_dose_cloud(self, centers_mm, axes_mm, points_mm):
        """Paper 1 superposition at points: sum over seeds of dose_at_points
        (its own conversion factor, S_K = 3.5 U baked in), cGy."""
        acc = np.zeros(points_mm.shape[0])
        for c, a in zip(centers_mm, axes_mm):
            d = points_mm - c
            r_mm = np.linalg.norm(d, axis=1)
            z = d @ a
            y = np.sqrt(np.maximum(r_mm ** 2 - z ** 2, 0.0))
            theta = np.degrees(np.arctan2(y, np.abs(z)))
            acc += self.obj.dose_at_points(theta, r_mm / 10.0)
        return acc


def compare_tables(cls, ds):
    """Entry-by-entry comparison of Paper 1's consensus tables with gtcore's
    SeedDataset.  Returns a list of difference records (empty = identical)."""
    diffs = []

    def scalar(name, a, b, loc):
        if float(a) != float(b):
            diffs.append(dict(table=name, location=loc, paper1=float(a),
                              gtcore=float(b)))

    def array(name, a, b, axes):
        a = np.asarray(a, float)
        b = np.asarray(b, float)
        if a.shape != b.shape:
            diffs.append(dict(table=name, location="shape", paper1=str(a.shape),
                              gtcore=str(b.shape)))
            return
        eq = (a == b) | (np.isnan(a) & np.isnan(b))
        for idx in np.argwhere(~eq):
            loc = ", ".join("%s=%g" % (ax, axv[i]) for (ax, axv), i
                            in zip(axes, idx))
            diffs.append(dict(table=name, location=loc,
                              paper1=float(a[tuple(idx)]),
                              gtcore=float(b[tuple(idx)])))

    scalar("Lambda", cls._LAMBDA, ds.dose_rate_constant, "Table AI")
    scalar("L", cls._L, ds.active_length_cm, "Table AI")
    array("g_L radii", cls._CON_GL_RADII, ds.g_radii, [("index", np.arange(99))])
    array("g_L values", cls._CON_GL_VALUES, ds.g_values,
          [("r_cm", np.asarray(cls._CON_GL_RADII, float))])
    array("F thetas", cls._CON_F_THETAS, ds.f_thetas, [("index", np.arange(99))])
    array("F radii", cls._CON_F_RADII, ds.f_radii, [("index", np.arange(99))])
    array("F values", cls._CON_F_TABLE, ds.f_table,
          [("theta_deg", np.asarray(cls._CON_F_THETAS, float)),
           ("r_cm", np.asarray(cls._CON_F_RADII, float))])
    return diffs


# ------------------------------------------------------------ fixture files
def write_fixtures(cls, paper1_path: Path, label: str, command: str):
    """Write tg43u1s2_cs1rev2.json and tg43u1s2_table_xii_erratum.csv."""
    clone = paper1_path.parent
    p1_commit = git(["rev-parse", "HEAD"], clone) or "unknown"
    p1_date = git(["log", "-1", "--format=%cd", "--date=short"], clone) or "unknown"
    first, last = cls.__paper1_lines__

    F = np.asarray(cls._CON_F_TABLE, float)
    holes = [[float(cls._CON_F_THETAS[i]), float(cls._CON_F_RADII[j])]
             for i, j in np.argwhere(np.isnan(F))]
    doc = {
        "_provenance": {
            "title": ("TG-43U1S2 consensus dataset for the IsoRay Medical model "
                      "CS-1 Rev2 (Proxcelan) 131Cs source, as embedded in Paper "
                      "1's dose engine"),
            "copied_from": ("GammaTile.py (GammaView Dosimetry 3D Slicer module, "
                            "%s), class DoseInterpolator lines %d-%d, dataset "
                            "'TG43U1S2'; clone commit %s (%s)"
                            % (PAPER1_REPO, first, last, p1_commit, p1_date)),
            "extraction": ("class block sliced with ast and executed with numpy "
                           "only; values written verbatim as Python floats; "
                           "untabulated entries (numpy NaN) written as null"),
            "paper": REPORT,
            "tables_as_stated_in_source_comments": {
                "dose_rate_constant": "Table AI (CON Lambda)",
                "active_length": "Table AI (CON L)",
                "g_L": "Table AII, line-source column 'IsoRay CS-1 Rev2'",
                "F": ("Table AXIII; the theta = 90 deg row (F = 1 by "
                      "definition) is added by Paper 1 and is not printed in "
                      "the report; r = 0.1 cm is not tabulated for theta <= 20 "
                      "deg"),
                "half_life": ("Table XXIV: T1/2 = 9.689 d; Paper 1 stores the "
                              "mean life tau = 335.48 h"),
            },
            "erratum_applied": (
                "Not stated for the consensus tables in Paper 1's source; the "
                "2018 erratum (%s) corrects the report's QA dose-rate tables, "
                "not Tables AI/AII/AXIII. Paper 1's QA script states that its "
                "Table XII values ARE the erratum-corrected ones (see "
                "tg43u1s2_table_xii_erratum.csv)." % ERRATUM),
            "independent_verification": (
                "The journal pages (Wiley) return HTTP 403. A free PDF of the "
                "2017 report hosted by IROC Houston (%s) was text-extracted "
                "(pypdf) and Tables AI, AII and AXIII were compared entry by "
                "entry with this file and with gtcore's tables "
                "(docs/paper2-phase-notes.md, Phase 3 engine QA): gtcore matches "
                "the report everywhere; this file (Paper 1) differs from the "
                "report at ONE entry, F(r = 7 cm, theta = 80 deg) = 0.999 where "
                "the report prints 0.998. That entry is kept here as Paper 1 "
                "has it -- this file documents Paper 1's engine." % IROC_PDF),
            "generated_by": command,
            "generated_at_commit": label,
        },
        "source_model": "IsoRay Medical / GT Medical Technologies model CS-1 Rev2 (Proxcelan)",
        "radionuclide": "131Cs",
        "dose_rate_constant_cGy_per_h_per_U": float(cls._LAMBDA),
        "active_length_cm": float(cls._L),
        "half_life_days": 9.689,
        "paper1_mean_life_h": float(cls.CS131_MEAN_LIFE_H),
        "paper1_seed_strength_U": float(cls.SEED_STRENGTH_U),
        "paper1_dose_conversion_factor_cGy_per_rate": float(cls.DOSE_CONVERSION_FACTOR),
        "g_L": {
            "r_cm": [float(x) for x in cls._CON_GL_RADII],
            "values": [float(x) for x in cls._CON_GL_VALUES],
            "paper1_interpolation": ("linear in r (numpy.interp); held constant "
                                     "below 0.10 cm and above 10 cm"),
        },
        "F": {
            "theta_deg": [float(x) for x in cls._CON_F_THETAS],
            "r_cm": [float(x) for x in cls._CON_F_RADII],
            "values": [[None if math.isnan(v) else float(v) for v in row]
                       for row in F],
            "untabulated_entries_theta_r": holes,
            "paper1_interpolation": (
                "bilinear in (r, theta) on the adjacent nodes, clamped to the "
                "table domain; untabulated entries filled from the nearest "
                "tabulated radius at the same theta, searching outward first"),
        },
        "phi_an": None,
        "phi_an_note": ("The 1-D anisotropy factor phi_an(r) (TG-43U1S2 Table "
                        "AXIV) is embedded in neither Paper 1's engine nor "
                        "gtcore: both are 2-D line-source only (no 1-D mode by "
                        "decision). Nothing to compare; not transcribed here "
                        "to avoid an unverifiable hand copy."),
    }
    CONSENSUS_FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONSENSUS_FIXTURE, "w", encoding="utf-8", newline=NL) as f:
        json.dump(doc, f, indent=1, allow_nan=False)
        f.write(NL)

    # Table XII (erratum) from Paper 1's QA script, polar as published.
    qa_script = clone / "tests" / "tg43_qa_check.py"
    radii, table = extract_paper1_table_xii(qa_script)
    n = 0
    with open(TABLE_XII_FIXTURE, "w", encoding="utf-8", newline="") as f:
        write_comments(f, (
            "TG-43U1S2 QA table for the IsoRay CS-1 Rev2 131Cs source: dose "
            "rate per unit air-kerma strength D(r,theta)/S_K [cGy h^-1 U^-1]",
            "Published as Table XII of " + REPORT,
            "CORRECTED values per the erratum: " + ERRATUM,
            "Transcribed from Paper 1's QA script tests/tg43_qa_check.py (%s, "
            "clone commit %s), which states these are the erratum-corrected "
            "values; NOT independently re-read from the paywalled erratum. "
            "The 2017 print (IROC-hosted PDF) has 13 mislabelled angle rows, "
            "no 80/90 deg rows and no 10 cm column; this transcription has "
            "the full 15 x 10 layout -- see docs/paper2-phase-notes.md."
            % (PAPER1_REPO, p1_commit[:9]),
            "Polar layout as published: r_cm (10 radii 0.1-10 cm), theta_deg "
            "from the seed long axis (15 angles 0-90 deg); r = 0.1 cm is "
            "untabulated for theta <= 20 deg (7 missing -> 143 rows). "
            "along_mm = 10 r cos(theta) and away_mm = 10 r sin(theta) are "
            "derived for convenience.",
            "generated by: %s (commit %s)" % (command, label),
        ))
        w = csv.writer(f, lineterminator=NL)
        w.writerow(["r_cm", "theta_deg", "along_mm", "away_mm",
                    "dose_rate_cGy_per_h_per_U"])
        for theta in sorted(table):
            for r, v in zip(radii, table[theta]):
                if v is None:
                    continue
                along = 10.0 * r * math.cos(math.radians(theta))
                away = 10.0 * r * math.sin(math.radians(theta))
                w.writerow([("%g" % r), theta, "%.4f" % along, "%.4f" % away,
                            repr(float(v))])
                n += 1
    return n


_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def parse_table_xxiii_text(text_path: Path):
    """Parse the printed Table XXIII (CS-1 Rev2, along-away) out of the pypdf
    text of the report.

    Layout in the print: one row per ALONG distance (0.0-7.0 cm in 0.5 cm
    steps, the row label), one column per AWAY distance (0, 0.5, 1, 1.5, 2,
    3, 4, 5, 6, 7 cm).  The (along = 0, away = 0) cell is the source centre
    and is blank, so the first row carries 9 values.  Which axis is which is
    fixed by the data, not the header: the along = 0 row holds the
    transverse-axis rates (4.10 at 0.5 cm, 1.056 at 1 cm, ...).
    Returns ``(along_cm, away_cm, matrix)`` with ``None`` for the blank cell.
    """
    lines = Path(text_path).read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith("TABLE XXIII.") and "CS-1 Rev2" in ln)
    away = None
    rows = []
    for ln in lines[start + 1:]:
        if ln.startswith("Medical Physics"):
            break
        toks = ln.split()
        if not toks or not all(_NUM.match(t) for t in toks):
            continue
        vals = [float(t) for t in toks]
        if away is None:
            away = vals                     # the column header line
            continue
        rows.append(vals)
    if away is None or len(rows) != 15:
        raise ValueError("Table XXIII not parsed (%d rows)" % len(rows))
    along, matrix = [], []
    for vals in rows:
        along.append(vals[0])
        body = vals[1:]
        if len(body) == len(away) - 1:     # blank source-centre cell
            body = [None] + body
        if len(body) != len(away):
            raise ValueError("Table XXIII row %g has %d values" % (vals[0], len(body)))
        matrix.append(body)
    return along, away, matrix


def write_along_away_fixture(text_path: Path, label: str, command: str):
    along, away, M = parse_table_xxiii_text(text_path)
    n = 0
    with open(ALONG_AWAY_FIXTURE, "w", encoding="utf-8", newline="") as f:
        write_comments(f, (
            "TG-43U1S2 QA table for the IsoRay CS-1 Rev2 131Cs source in "
            "along-away format: dose rate per unit air-kerma strength "
            "D/S_K [cGy h^-1 U^-1]",
            "Published as Table XXIII of " + REPORT,
            "Values AS PRINTED in the 2017 report (text-extracted with pypdf "
            "from the free PDF hosted by IROC Houston, %s). The 2018 erratum "
            "(%s) is paywalled; whether it touched Table XXIII is unknown. "
            "The printed values agree with Eq. (1) on the consensus "
            "parameters to the rounding of their 3-4 significant figures "
            "(see docs/figures/data/engine_qa_table_xxiii.csv)."
            % (IROC_PDF, ERRATUM),
            "Layout: along_mm = axial distance from the seed centre along the "
            "long axis (0-70 mm, 5 mm steps), away_mm = perpendicular distance "
            "from the axis (0, 5, 10, 15, 20, 30, 40, 50, 60, 70 mm); the "
            "(0, 0) cell is the source centre and is left empty. 149 values.",
            "generated by: %s (commit %s)" % (command, label),
        ))
        w = csv.writer(f, lineterminator=NL)
        w.writerow(["along_mm", "away_mm", "dose_rate_cGy_per_h_per_U"])
        for a, row in zip(along, M):
            for b, v in zip(away, row):
                w.writerow(["%g" % (10 * a), "%g" % (10 * b),
                            "" if v is None else repr(float(v))])
                n += v is not None
    return n


# -------------------------------------------------------------- the checks
def grid_check(eng, p1, ds):
    g_nodes = np.asarray(ds.g_radii, float)
    f_r = np.asarray(ds.f_radii, float)
    f_t = np.asarray(ds.f_thetas, float)
    r = np.unique(np.concatenate([np.geomspace(0.3, 10.0, 60),
                                  g_nodes[g_nodes >= 0.3], f_r[f_r >= 0.3]]))
    th = np.unique(np.concatenate([np.arange(0.0, 90.0001, 0.5), f_t]))
    TH, R = np.meshgrid(th, r, indexing="ij")
    gt = eng.dose_rate(TH, R)
    gt_tab = eng.dose_rate_tabulated(TH, R)
    pp = p1.rate(TH, R)
    both_nodes_r = np.isin(R, np.intersect1d(g_nodes, f_r))
    node_mask = both_nodes_r & np.isin(TH, f_t)
    return dict(TH=TH, R=R, gt=gt, gt_tab=gt_tab, p1=pp, rel=rel_pct(gt, pp),
                rel_tab=rel_pct(gt_tab, pp), node_mask=node_mask,
                g_node_mask=np.isin(R, g_nodes))


def seed_cloud_check(eng, p1, n_seeds, n_points, seed):
    rng = np.random.default_rng(seed)
    centers = rng.uniform(-20.0, 20.0, (n_seeds, 3))          # mm, 40 mm cube
    axes = rng.normal(size=(n_seeds, 3))
    axes /= np.linalg.norm(axes, axis=1)[:, None]
    pts = rng.uniform(-40.0, 40.0, (n_points, 3))             # mm, 80 mm cube
    sk = p1.sk_u                                              # 3.5 U, as Paper 1
    gt_exact = dose_at_points(centers, axes, pts, sk_per_seed_u=sk,
                              engine=eng, exact=True)
    gt_table = dose_at_points(centers, axes, pts, sk_per_seed_u=sk,
                              engine=eng, exact=False)
    pp = p1.total_dose_cloud(centers, axes, pts)
    r_min = np.linalg.norm(pts[:, None, :] - centers[None, :, :], axis=2).min(axis=1)
    return dict(centers=centers, axes=axes, pts=pts, gt_exact=gt_exact,
                gt_table=gt_table, p1=pp,
                rate_gt=gt_exact / (eng.TAU_HOURS * sk), rate_p1=pp / p1.conv,
                r_min_mm=r_min, sk=sk)


def qa_table_check(eng, p1, tab):
    gt = eng.dose_rate(tab["theta_deg"], tab["r_cm"])
    pp = p1.rate(tab["theta_deg"], tab["r_cm"])
    return dict(gt=gt, p1=pp, rel_gt=rel_pct(gt, tab["rate"]),
                rel_p1=rel_pct(pp, tab["rate"]))


def probes(eng, p1):
    """Point probes of the behaviours the port notes record."""
    L = eng.L
    rows = []

    def add(name, theta, r, note):
        g = float(eng.dose_rate(theta, r))
        q = float(p1.rate(theta, r)[0])
        rows.append(dict(probe=name, theta_deg=theta, r_cm=r, gtcore=g,
                         paper1=q, rel_pct=(100.0 * (g / q - 1.0) if q else
                                            float("nan")), note=note))

    add("reference point", 90.0, 1.0, "both must equal Lambda = 1.056")
    add("on-axis r < L/2 (inside can)", 0.0, 0.15,
        "Paper 1: 1/(r^2-L^2/4) < 0, clamped to 0 (port-notes item 1); gtcore: "
        "projected to the capsule end face")
    add("on-axis r = L/2 (active tip, inside can)", 0.0, L / 2.0,
        "Paper 1: singular denominator clamped at 1e-3 -> spike; gtcore: end-face value")
    add("on-axis r = 0.21 cm (inside can)", 0.0, 0.21,
        "Paper 1: 1/(r^2-L^2/4) pole; gtcore: end-face plateau (no tissue here)")
    add("on-axis r = 0.23 cm (just outside can)", 0.0, 0.23,
        "both use the analytic on-axis limit; only g_L interpolation differs")
    add("on-axis r = 0.3 cm", 0.0, 0.3, "grid lower edge")
    add("NaN-hole lookup F(0.1 cm, 0 deg) region", 0.0, 0.1,
        "both borrow F from r = 0.25 cm (identical fill); gtcore evaluates at "
        "the capsule surface instead (inside the can)")
    add("NaN-hole lookup F(0.1 cm, 20 deg) region", 20.0, 0.1,
        "same fill rule in both; gtcore projects to the capsule surface")
    add("radial floor ring (90 deg, 0.045 cm)", 90.0, 0.045,
        "Paper 1 clips r at 0.041 cm, gtcore floors at 0.05 cm (0.09 mm ring "
        "beside the capsule side)")
    add("between g_L nodes 9-10 cm (90 deg, 9.5 cm)", 90.0, 9.5,
        "linear (Paper 1) vs log-linear (gtcore, TG-43U1S1 Eq. 3) g_L interpolation")
    add("beyond the data (90 deg, 12 cm)", 90.0, 12.0,
        "Paper 1 clips r to 10 cm (whole rate frozen); gtcore: G_L keeps falling, "
        "g_L single-exponential tail (TG-43U1S1 Eq. 4)")
    add("beyond the data (90 deg, 15 cm)", 90.0, 15.0, "as above")
    return rows


# ------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper1", default=None,
                    help="GammaTile.py or the GammaTileDosimetry clone root "
                         "(default: $GT_PAPER1_ENGINE)")
    ap.add_argument("--write-fixtures", action="store_true",
                    help="regenerate gtcore/dose/qa_data fixtures from Paper 1")
    ap.add_argument("--table-xxiii-text", default=None,
                    help="with --write-fixtures: pypdf text of the report PDF; "
                         "its printed Table XXIII becomes the along-away fixture")
    ap.add_argument("--out", default=str(ROOT / "docs/figures/data/engine_crosscheck.csv"))
    ap.add_argument("--table-xii-out",
                    default=str(ROOT / "docs/figures/data/engine_qa_table_xii.csv"))
    ap.add_argument("--table-xxiii-out",
                    default=str(ROOT / "docs/figures/data/engine_qa_table_xxiii.csv"))
    ap.add_argument("--points-out", default=None,
                    help="optional per-point CSV of the (r, theta) grid")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--n-points", type=int, default=1000)
    ap.add_argument("--n-seeds", type=int, default=32)
    args = ap.parse_args(argv)

    print("gtcore.__file__ =", gtcore.__file__)
    gt_rel = os.path.relpath(gtcore.__file__, ROOT).replace(chr(92), "/")
    label = commit_label(ROOT)
    command = sanitized_command(sys.argv if argv is None else ["x", *argv])

    p1_path = paper1_engine_path(args.paper1)
    if p1_path is None:
        sys.exit("Paper 1's GammaTile.py not found: pass --paper1 or set "
                 "GT_PAPER1_ENGINE")
    cls = extract_paper1_interpolator(p1_path)
    p1 = Paper1(cls)
    eng = TG43Engine("tg43u1s2")
    ds = TG43U1S2_CONSENSUS
    print("Paper 1 class: lines %d-%d of %s" % (*cls.__paper1_lines__, p1_path.name))

    if args.write_fixtures:
        n = write_fixtures(cls, p1_path, label, command)
        print("wrote %s and %s (%d Table XII points)"
              % (CONSENSUS_FIXTURE.name, TABLE_XII_FIXTURE.name, n))
        if args.table_xxiii_text:
            n = write_along_away_fixture(Path(args.table_xxiii_text), label, command)
            print("wrote %s (%d Table XXIII points)" % (ALONG_AWAY_FIXTURE.name, n))

    summary = []   # rows of the S3 table

    def row(section, name, st, note="", extra=None):
        summary.append(dict(section=section, row=name, n=st["n"],
                            max_abs_rel_pct=st["max_abs"],
                            median_abs_rel_pct=st["median_abs"],
                            p95_abs_rel_pct=st["p95_abs"],
                            max_signed_rel_pct=st["max_signed"],
                            where_max=extra or "", note=note))
        print("%-10s %-58s n=%5d  max %9.4g  med %9.4g  p95 %9.4g  %s"
              % (section, name, st["n"], st["max_abs"], st["median_abs"],
                 st["p95_abs"], extra or ""))

    def info(section, name, n, note, where=""):
        summary.append(dict(section=section, row=name, n=n, max_abs_rel_pct=np.nan,
                            median_abs_rel_pct=np.nan, p95_abs_rel_pct=np.nan,
                            max_signed_rel_pct=np.nan, where_max=where, note=note))

    # 1. tables ---------------------------------------------------------
    diffs = compare_tables(cls, ds)
    print(NL + "[1] consensus tables, Paper 1 vs gtcore: %d difference(s)" % len(diffs))
    for d in diffs:
        print("    %s at %s: paper1=%r gtcore=%r" % (d["table"], d["location"],
                                                     d["paper1"], d["gtcore"]))
    n_entries = (2 + ds.g_radii.size * 2 + ds.f_thetas.size + ds.f_radii.size
                 + ds.f_table.size)
    info("tables", "consensus entries compared", int(n_entries),
         "Lambda, L, g_L (16 r + 16 values), F axes (15 + 10), F table (150)")
    for d in diffs:
        info("tables", "DIFFERENCE %s" % d["table"], 1,
             "paper1=%r gtcore=%r" % (d["paper1"], d["gtcore"]), d["location"])

    # 2. QA tables ------------------------------------------------------
    tab = load_table_xii()
    tx = qa_table_check(eng, p1, tab)
    print(NL + "[2] Table XII (erratum, %d points)" % tab["rate"].size)
    for key, name in (("rel_gt", "gtcore exact vs Table XII (erratum)"),
                      ("rel_p1", "Paper 1 engine vs Table XII (erratum)")):
        st = stats(tx[key])
        i = st["idx"]
        row("tableXII", name, st, "published values carry 3-4 significant figures",
            "r=%g cm, theta=%g deg" % (tab["r_cm"][i], tab["theta_deg"][i]))
    st = stats(rel_pct(tx["gt"], tx["p1"]))
    i = st["idx"]
    row("tableXII", "gtcore vs Paper 1 at the 143 Table XII nodes", st,
        "node points: only table-value differences survive",
        "r=%g cm, theta=%g deg" % (tab["r_cm"][i], tab["theta_deg"][i]))

    aa = load_along_away()
    txx = None
    if aa is not None:
        txx = qa_table_check(eng, p1, aa)
        print(NL + "[2b] Table XXIII (along-away, as printed, %d points)" % aa["rate"].size)
        for key, name in (("rel_gt", "gtcore exact vs Table XXIII (along-away, 2017 print)"),
                          ("rel_p1", "Paper 1 engine vs Table XXIII (along-away, 2017 print)")):
            st = stats(txx[key])
            i = st["idx"]
            row("tableXXIII", name, st,
                "off-node points: interpolation rules enter; 3-4 significant figures",
                "along=%g mm, away=%g mm" % (aa["along_mm"][i], aa["away_mm"][i]))
        st = stats(rel_pct(txx["gt"], txx["p1"]))
        i = st["idx"]
        row("tableXXIII", "gtcore vs Paper 1 at the Table XXIII points", st, "",
            "along=%g mm, away=%g mm" % (aa["along_mm"][i], aa["away_mm"][i]))
    else:
        info("tableXXIII", "along-away fixture absent", 0,
             "gtcore/dose/qa_data/tg43u1s2_along_away.csv not present; test skipped")

    # 3. (r, theta) grid ------------------------------------------------
    g = grid_check(eng, p1, ds)
    TH, R, rel = g["TH"], g["R"], g["rel"]
    print(NL + "[3] grid r = 0.3-10 cm (%d radii) x theta = 0-90 deg (%d angles)"
          % (R.shape[1], R.shape[0]))

    def where(st):
        if st["idx"] is None:
            return ""
        k = np.unravel_index(st["idx"], R.shape)
        return "r=%.4g cm, theta=%g deg" % (R[k], TH[k])

    st = stats(rel)
    row("grid", "all points (exact engine)", st,
        "g_L interpolation rule is the only off-node difference", where(st))
    st = stats(rel, g["node_mask"])
    row("grid", "at common table nodes (r in g and F nodes, theta in F nodes)",
        st, "identical arithmetic expected", where(st))
    st = stats(rel, ~g["g_node_mask"])
    row("grid", "between g_L nodes", st,
        "Paper 1 linear vs gtcore log-linear (TG-43U1S1 Eq. 3)", where(st))
    st = stats(rel, TH < 5.0)
    row("grid", "near axis, theta < 5 deg", st,
        "no near-axis difference for r >= 0.3 cm", where(st))
    st = stats(rel, TH >= 5.0)
    row("grid", "theta >= 5 deg", st, "", where(st))
    for lo, hi in ((0.3, 1.0), (1.0, 3.0), (3.0, 6.0), (6.0, 10.0001)):
        st = stats(rel, (R >= lo) & (R < hi))
        row("grid", "r in [%g, %g) cm" % (lo, min(hi, 10.0)), st, "", where(st))
    st = stats(g["rel_tab"])
    row("grid", "all points (gtcore tabulated kernel, compute_dose_grid default)",
        st, "adds the kernel-table interpolation error", where(st))

    # 4. seed cloud -----------------------------------------------------
    sc = seed_cloud_check(eng, p1, args.n_seeds, args.n_points, args.seed)
    print(NL + "[4] %d-seed cloud, %d points (seed %d)" % (args.n_seeds, args.n_points,
                                                         args.seed))
    rel_rate = rel_pct(sc["rate_gt"], sc["rate_p1"])
    rel_total = rel_pct(sc["gt_exact"], sc["p1"])
    rel_tabc = rel_pct(sc["gt_table"], sc["p1"])
    far = sc["r_min_mm"] >= 3.0

    def where_pt(st):
        if st["idx"] is None:
            return ""
        return "nearest seed %.2f mm" % sc["r_min_mm"][st["idx"]]

    st = stats(rel_rate)
    row("cloud", "summed rate per U, exact engine, all points", st,
        "conversion factors divided out", where_pt(st))
    st = stats(rel_rate, far)
    row("cloud", "summed rate per U, points >= 3 mm from every seed", st, "",
        where_pt(st))
    st = stats(rel_total)
    row("cloud", "total-decay dose, each engine's own conversion", st,
        "tau: gtcore %.4f h vs Paper 1 %.2f h (%+.2e %%)"
        % (eng.TAU_HOURS, p1.tau_h, 100 * (eng.TAU_HOURS / p1.tau_h - 1)),
        where_pt(st))
    st = stats(rel_tabc)
    row("cloud", "total-decay dose, gtcore tabulated kernel vs Paper 1", st,
        "compute_dose_grid default path", where_pt(st))
    info("cloud", "dose scale (gtcore exact)", int(sc["gt_exact"].size),
         "median %.1f cGy, max %.1f cGy, min %.2f cGy; S_K %.1f U"
         % (float(np.median(sc["gt_exact"])), sc["gt_exact"].max(),
            sc["gt_exact"].min(), sc["sk"]))

    # 5. probes ---------------------------------------------------------
    pr = probes(eng, p1)
    print(NL + "[5] probes")
    for p in pr:
        print("    %-46s theta=%5.3g r=%6.4g  gtcore=%-10.6g paper1=%-10.6g rel=%s"
              % (p["probe"], p["theta_deg"], p["r_cm"], p["gtcore"], p["paper1"],
                 fmt(p["rel_pct"])))
        summary.append(dict(section="probe", row=p["probe"], n=1,
                            max_abs_rel_pct=(abs(p["rel_pct"])
                                             if math.isfinite(p["rel_pct"]) else np.nan),
                            median_abs_rel_pct=np.nan, p95_abs_rel_pct=np.nan,
                            max_signed_rel_pct=p["rel_pct"],
                            where_max="theta=%g deg, r=%g cm" % (p["theta_deg"], p["r_cm"]),
                            note="gtcore=%.6g paper1=%.6g cGy/h/U; %s"
                            % (p["gtcore"], p["paper1"], p["note"])))

    # ------------------------------------------------------------ outputs
    header = [
        "engine QA cross-check: gtcore TG43Engine('tg43u1s2') vs Paper 1's "
        "DoseInterpolator (TG43U1S2) and the TG-43U1S2 QA tables (Table XII "
        "erratum values; Table XXIII as printed). Supplement S3 table rows.",
        "script: scripts/engine_crosscheck.py",
        "command: " + command,
        "commit: " + label,
        "gtcore: " + gt_rel,
        "paper1: %s class DoseInterpolator lines %d-%d, clone commit %s"
        % (PAPER1_REPO, *cls.__paper1_lines__,
           (git(["rev-parse", "--short=9", "HEAD"], p1_path.parent) or "unknown")),
        "datasets: none (synthetic geometry; no patient data). rel_pct = "
        "100 * (gtcore / reference - 1); reference = Paper 1 unless the row "
        "names a QA table.",
        "grid: r = 0.3-10 cm (60 log-spaced + table nodes), theta = 0-90 deg "
        "(0.5 deg + table nodes); cloud: %d random seeds in a 40 mm cube, %d "
        "uniform points in an 80 mm cube, numpy default_rng(%d), S_K %.1f U."
        % (args.n_seeds, args.n_points, args.seed, sc["sk"]),
    ]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["section", "row", "n", "max_abs_rel_pct", "median_abs_rel_pct",
            "p95_abs_rel_pct", "max_signed_rel_pct", "where_max", "note"]
    with open(out, "w", encoding="utf-8", newline="") as f:
        write_comments(f, header)
        w = csv.writer(f, lineterminator=NL)
        w.writerow(cols)
        for rec in summary:
            w.writerow([rec["section"], rec["row"], rec["n"],
                        fmt(rec["max_abs_rel_pct"], 5), fmt(rec["median_abs_rel_pct"], 5),
                        fmt(rec["p95_abs_rel_pct"], 5), fmt(rec["max_signed_rel_pct"], 5),
                        rec["where_max"], rec["note"]])
    print(NL + "wrote", out)

    tout = Path(args.table_xii_out)
    with open(tout, "w", encoding="utf-8", newline="") as f:
        write_comments(f, header[:6] + [
            "per-point: published = erratum Table XII "
            "(gtcore/dose/qa_data/tg43u1s2_table_xii_erratum.csv); rates in "
            "cGy h^-1 U^-1; *_diff_pct = 100 * (engine / published - 1)"])
        w = csv.writer(f, lineterminator=NL)
        w.writerow(["r_cm", "theta_deg", "along_mm", "away_mm", "published",
                    "gtcore", "paper1", "gtcore_diff_pct", "paper1_diff_pct"])
        for i in range(tab["rate"].size):
            w.writerow(["%g" % tab["r_cm"][i], "%g" % tab["theta_deg"][i],
                        "%.4f" % tab["along_mm"][i], "%.4f" % tab["away_mm"][i],
                        repr(float(tab["rate"][i])), "%.6g" % tx["gt"][i],
                        "%.6g" % tx["p1"][i], "%+.3f" % tx["rel_gt"][i],
                        "%+.3f" % tx["rel_p1"][i]])
    print("wrote", tout)

    if txx is not None:
        xout = Path(args.table_xxiii_out)
        with open(xout, "w", encoding="utf-8", newline="") as f:
            write_comments(f, header[:6] + [
                "per-point: published = Table XXIII as printed in 2017 "
                "(gtcore/dose/qa_data/tg43u1s2_along_away.csv); rates in "
                "cGy h^-1 U^-1; *_diff_pct = 100 * (engine / published - 1)"])
            w = csv.writer(f, lineterminator=NL)
            w.writerow(["along_mm", "away_mm", "r_cm", "theta_deg", "published",
                        "gtcore", "paper1", "gtcore_diff_pct", "paper1_diff_pct"])
            for i in range(aa["rate"].size):
                w.writerow(["%g" % aa["along_mm"][i], "%g" % aa["away_mm"][i],
                            "%.5g" % aa["r_cm"][i], "%.4f" % aa["theta_deg"][i],
                            repr(float(aa["rate"][i])), "%.6g" % txx["gt"][i],
                            "%.6g" % txx["p1"][i], "%+.3f" % txx["rel_gt"][i],
                            "%+.3f" % txx["rel_p1"][i]])
        print("wrote", xout)

    if args.points_out:
        pout = Path(args.points_out)
        pout.parent.mkdir(parents=True, exist_ok=True)
        with open(pout, "w", encoding="utf-8", newline="") as f:
            write_comments(f, header[:6])
            w = csv.writer(f, lineterminator=NL)
            w.writerow(["r_cm", "theta_deg", "gtcore", "gtcore_tabulated", "paper1",
                        "rel_pct", "rel_pct_tabulated", "common_node"])
            for k in np.ndindex(R.shape):
                w.writerow(["%.6g" % R[k], "%g" % TH[k], "%.8g" % g["gt"][k],
                            "%.8g" % g["gt_tab"][k], "%.8g" % g["p1"][k],
                            "%+.5f" % rel[k], "%+.5f" % g["rel_tab"][k],
                            int(g["node_mask"][k])])
        print("wrote", pout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
