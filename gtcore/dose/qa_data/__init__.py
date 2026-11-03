"""QA fixtures for the TG-43 engine (Paper 2, Phase 3, task ``engine-qa``).

This package holds the reference data the dose engine is pinned to and the
small loaders the QA test (``tests/test_dose_engine_qa.py``) and the
cross-check script (``scripts/engine_crosscheck.py``) share.

Files
-----
``tg43u1s2_cs1rev2.json``
    The TG-43U1S2 consensus parameters for the IsoRay model CS-1 Rev2
    (Proxcelan) 131Cs source -- Lambda, L, g_L(r) and F(r, theta) -- copied
    verbatim from the ``DoseInterpolator`` class of Paper 1's engine
    (``GammaTile.py``, dataset ``"TG43U1S2"``), with a provenance header
    that names the report tables the source comments cite.  The file is
    written by ``scripts/engine_crosscheck.py --write-fixtures`` and is
    *data about Paper 1's engine*, not a hand-edited copy of the report:
    where it differs from gtcore's embedded tables, the difference is a
    finding (see ``docs/paper2-phase-notes.md``), not something to fix here.
``tg43u1s2_table_xii_erratum.csv``
    The report's QA table for this source -- dose rate per unit air-kerma
    strength, D(r, theta)/S_K in cGy h^-1 U^-1, at the F-table nodes --
    as corrected by the 2018 erratum (Med Phys 45(2):971-974,
    doi:10.1002/mp.12728).  Published in polar (r, theta) form, 143 values.
    Transcribed from Paper 1's ``tests/tg43_qa_check.py``; see the header.
``tg43u1s2_along_away.csv``  (OPTIONAL -- not shipped)
    If an along-away QA table for this source is ever supplied, drop it
    here and :func:`load_along_away` picks it up; the layout is documented
    on that function.  TG-43U1S2 publishes the CS-1 Rev2 QA table in polar
    coordinates only, so the file does not exist today and the test that
    reads it is skipped with that reason.

The Paper 1 class itself is never copied into gtcore: :func:`extract_paper1_interpolator`
reads it out of a ``GammaTile.py`` on disk (the file imports ``slicer``/``vtk``/``qt``
and cannot be imported as a module) by taking the class block with ``ast`` and
executing only that block with ``numpy`` in scope.
"""
from __future__ import annotations

import ast
import csv
import json
import math
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

__all__ = [
    "QA_DATA_DIR",
    "CONSENSUS_FIXTURE",
    "TABLE_XII_FIXTURE",
    "ALONG_AWAY_FIXTURE",
    "PAPER1_ENGINE_ENV",
    "load_consensus_fixture",
    "load_table_xii",
    "load_along_away",
    "extract_paper1_interpolator",
    "extract_paper1_table_xii",
    "paper1_engine_path",
]

QA_DATA_DIR = Path(__file__).resolve().parent
CONSENSUS_FIXTURE = QA_DATA_DIR / "tg43u1s2_cs1rev2.json"
TABLE_XII_FIXTURE = QA_DATA_DIR / "tg43u1s2_table_xii_erratum.csv"
ALONG_AWAY_FIXTURE = QA_DATA_DIR / "tg43u1s2_along_away.csv"

#: Environment variable naming Paper 1's ``GammaTile.py`` (or the root of
#: its repository clone).  The file is not part of gtcore and lives at a
#: machine-specific path, so the live cross-check test is skipped unless
#: this is set.
PAPER1_ENGINE_ENV = "GT_PAPER1_ENGINE"


# ------------------------------------------------------------------ fixtures
def _nan_to_none(values):
    out = []
    for row in values:
        if isinstance(row, (list, tuple, np.ndarray)):
            out.append(_nan_to_none(row))
        else:
            v = float(row)
            out.append(None if math.isnan(v) else v)
    return out


def _none_to_nan(values) -> np.ndarray:
    return np.array([[np.nan if v is None else float(v) for v in row]
                     for row in values], dtype=float)


def load_consensus_fixture(path: Optional[os.PathLike] = None) -> dict:
    """Load ``tg43u1s2_cs1rev2.json``.

    Returns the JSON document with the numeric tables converted to numpy
    arrays under the same keys (``g_L["r_cm"]``, ``g_L["values"]``,
    ``F["theta_deg"]``, ``F["r_cm"]``, ``F["values"]`` with ``NaN`` where
    the file has ``null``).  The ``_provenance`` block is left as is.
    """
    p = Path(path) if path is not None else CONSENSUS_FIXTURE
    with open(p, "r", encoding="utf-8") as f:
        doc = json.load(f)
    g = doc["g_L"]
    g["r_cm"] = np.asarray(g["r_cm"], dtype=float)
    g["values"] = np.asarray(g["values"], dtype=float)
    F = doc["F"]
    F["theta_deg"] = np.asarray(F["theta_deg"], dtype=float)
    F["r_cm"] = np.asarray(F["r_cm"], dtype=float)
    F["values"] = _none_to_nan(F["values"])
    return doc


def _read_commented_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Read a CSV whose leading ``#`` lines are a header comment."""
    comments: List[str] = []
    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        body = []
        for line in f:
            if line.startswith("#"):
                comments.append(line.rstrip("\n"))
            elif line.strip():
                body.append(line)
    for rec in csv.DictReader(body):
        rows.append(rec)
    return comments, rows


def load_table_xii(path: Optional[os.PathLike] = None) -> dict:
    """Load the erratum-corrected Table XII (polar QA dose-rate table).

    Returns ``{"r_cm", "theta_deg", "rate", "along_mm", "away_mm",
    "comments"}`` with the first five as 1-D float arrays of equal length
    (one entry per tabulated point, 143 for this source).
    """
    p = Path(path) if path is not None else TABLE_XII_FIXTURE
    comments, rows = _read_commented_csv(p)
    r = np.array([float(x["r_cm"]) for x in rows])
    th = np.array([float(x["theta_deg"]) for x in rows])
    rate = np.array([float(x["dose_rate_cGy_per_h_per_U"]) for x in rows])
    along = np.array([float(x["along_mm"]) for x in rows])
    away = np.array([float(x["away_mm"]) for x in rows])
    return {"r_cm": r, "theta_deg": th, "rate": rate, "along_mm": along,
            "away_mm": away, "comments": comments}


def load_along_away(path: Optional[os.PathLike] = None) -> Optional[dict]:
    """Load an along-away QA dose-rate table if one has been supplied.

    Expected layout of ``tg43u1s2_along_away.csv`` (UTF-8, comma separated):

    * any number of leading comment lines starting with ``#`` (state the
      source document, table number, erratum status and units there);
    * a header row ``along_mm,away_mm,dose_rate_cGy_per_h_per_U``;
    * one row per tabulated point: ``along_mm`` = signed or unsigned axial
      distance from the seed centre along the long axis in mm, ``away_mm``
      = perpendicular distance from the axis in mm, and the dose rate per
      unit air-kerma strength in cGy h^-1 U^-1 (TG-43 "dose rate per unit
      source strength").  Leave a cell empty for untabulated points.

    The polar equivalents are ``r_cm = hypot(along, away) / 10`` and
    ``theta_deg = degrees(atan2(away, |along|))``; both are returned.
    Returns ``None`` when the file does not exist (the QA test then skips).
    """
    p = Path(path) if path is not None else ALONG_AWAY_FIXTURE
    if not p.exists():
        return None
    comments, rows = _read_commented_csv(p)
    along, away, rate = [], [], []
    for x in rows:
        v = x.get("dose_rate_cGy_per_h_per_U", "").strip()
        if not v:
            continue
        along.append(float(x["along_mm"]))
        away.append(float(x["away_mm"]))
        rate.append(float(v))
    along = np.asarray(along, float)
    away = np.asarray(away, float)
    r_cm = np.hypot(along, away) / 10.0
    theta = np.degrees(np.arctan2(away, np.abs(along)))
    return {"along_mm": along, "away_mm": away, "rate": np.asarray(rate, float),
            "r_cm": r_cm, "theta_deg": theta, "comments": comments}


# ------------------------------------------------------- Paper 1's engine
def paper1_engine_path(path: Optional[os.PathLike] = None) -> Optional[Path]:
    """Resolve ``GammaTile.py`` from ``path`` or ``$GT_PAPER1_ENGINE``.

    Either may name the file itself or the root of the GammaTileDosimetry
    clone (then ``GammaTile.py`` inside it).  Returns ``None`` if neither
    is given or the file is missing.
    """
    cand = path if path is not None else os.environ.get(PAPER1_ENGINE_ENV)
    if not cand:
        return None
    p = Path(cand)
    if p.is_dir():
        p = p / "GammaTile.py"
    return p if p.is_file() else None


def _class_source(src: str, name: str) -> Tuple[str, int, int]:
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            seg = ast.get_source_segment(src, node)
            if seg is None:  # pragma: no cover - needs a malformed file
                raise ValueError("could not slice class %s" % name)
            # The block must be self-contained: no imports, only numpy use.
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    raise ValueError("class %s imports modules; refusing to exec"
                                     % name)
            return seg, node.lineno, node.end_lineno
    raise ValueError("class %s not found" % name)


def extract_paper1_interpolator(path: os.PathLike):
    """Return Paper 1's ``DoseInterpolator`` class from a ``GammaTile.py``.

    The module cannot be imported (it imports ``slicer``, ``vtk``, ``qt``,
    ``ctk`` at top level), so the class definition is sliced out with
    ``ast`` and executed alone in a namespace containing only ``numpy``
    as ``np``.  The class needs nothing else.  The returned class carries
    two extra attributes, ``__paper1_path__`` and ``__paper1_lines__``
    (first, last line of the block), for provenance.
    """
    p = Path(path)
    src = p.read_text(encoding="utf-8")
    block, first, last = _class_source(src, "DoseInterpolator")
    ns = {"np": np, "__name__": "paper1_dose_interpolator"}
    exec(compile(block, str(p), "exec"), ns)  # noqa: S102 - user's own file
    cls = ns["DoseInterpolator"]
    cls.__paper1_path__ = str(p)
    cls.__paper1_lines__ = (first, last)
    return cls


def extract_paper1_table_xii(qa_script_path: os.PathLike) -> Tuple[List[float],
                                                                   Dict[int, list]]:
    """Read ``TABLE_XII_RADII_CM`` and ``TABLE_XII`` out of Paper 1's
    ``tests/tg43_qa_check.py`` with ``ast.literal_eval`` (no execution).

    Returns ``(radii_cm, {theta_deg: [rate or None, ...]})``.
    """
    src = Path(qa_script_path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found: Dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("TABLE_XII_RADII_CM", "TABLE_XII"):
            found[node.targets[0].id] = ast.literal_eval(node.value)
    if set(found) != {"TABLE_XII_RADII_CM", "TABLE_XII"}:
        raise ValueError("TABLE_XII_RADII_CM / TABLE_XII not found in %s"
                         % qa_script_path)
    radii = [float(r) for r in found["TABLE_XII_RADII_CM"]]
    table = {int(k): list(v) for k, v in found["TABLE_XII"].items()}
    return radii, table
