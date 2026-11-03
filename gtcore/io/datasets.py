"""Dataset ids -> folders, with no machine path and no patient folder name in code.

Every script that reads a real scan names it by a **dataset id** (``PH8``,
``PT-CT1mm``, ...) and resolves the folder at run time:

1. ``GT_DATA_ROOT`` (environment) is the folder that holds the scans.  It is
   mandatory; nothing is guessed from the machine.
2. ``GT_DATASETS_JSON`` (environment, optional) points to a user-local JSON
   file mapping dataset ids to folder names (relative to ``GT_DATA_ROOT``) or
   to absolute paths.  When it is unset, ``<GT_DATA_ROOT>/datasets.json`` is
   read if it exists.  The file is never committed: it carries the real
   folder names.  Format: ``docs/datasets.example.json`` (keys starting with
   ``_`` are comments and ignored).
3. Without a mapping for an id, the folder literally named ``<dataset id>``
   under ``GT_DATA_ROOT`` is used.

CLI flags of the scripts (``--phantom``, ``--data-root``, positional paths)
still override everything here.  A missing configuration raises
:class:`DatasetNotConfigured` with the exact remedy; it is never silently
replaced by a default path.

Dataset ids (figure plan v6 / data designation v7; the Phase 1-3 notes use the
``JD-*`` aliases for the same folders):

======================  ===============================================================
``PH8``                 CT of the 3-D-printed phantom, 8 tiles / 32 seeds placed by hand
``PH0``                 CT of the tile-less printed twin (the only negative control)
``PT-CT1mm``            patient post-implant CT, contiguous 1 mm thin-cut; the series
                        the RTSTRUCT references (alias ``JD-1mm``)
``PT-CT2mm``            the same acquisition, gapped export: 1 mm slabs at 2 mm pitch
                        (alias ``JD-2mm``; not on disk since 2026-11-03)
``PT-CT-s3``            second reconstruction of the same acquisition, contiguous 1 mm
                        (alias ``JD-CT-s3``)
``PT-RTSTRUCT``         TPS structure set (folder holding the RTSTRUCT file)
``PT-RTDOSE``           TPS plan dose (folder holding the RTDOSE file)
``PT-RTPLAN``           TPS plan (RTPLAN; not on hand)
``PT-preMRI``           pre-operative MRI (alias ``JD-MRI-pre``)
``PT-intraMRI``         intra-operative MRI, the phantom cavity's source (``JD-MRI-intra``)
``PT-postMRI``          post-operative MRI (alias ``JD-MRI-post``)
======================  ===============================================================

The legacy keys of the localisation scripts (``printed8``, ``phantom8``,
``printed_blank``, ``tilefree``, ``postop``, ``thincut``) are
accepted as aliases so their cached runs keep their meaning.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = [
    "ALIASES",
    "DATASET_IDS",
    "DatasetNotConfigured",
    "canonical_id",
    "data_root",
    "dataset_path",
    "load_dataset_map",
]

ENV_ROOT = "GT_DATA_ROOT"
ENV_JSON = "GT_DATASETS_JSON"
DEFAULT_JSON_NAME = "datasets.json"

DATASET_IDS = {
    "PH8": "CT of the 3-D-printed phantom with 8 tiles / 32 seeds placed by hand",
    "PH0": "CT of the tile-less printed twin (negative control)",
    "PT-CT1mm": "patient post-implant CT, contiguous 1 mm thin-cut (RTSTRUCT reference series)",
    "PT-CT2mm": "same acquisition, gapped export (1 mm slabs at 2 mm pitch)",
    "PT-CT-s3": "second reconstruction of the same acquisition, contiguous 1 mm",
    "PT-RTSTRUCT": "TPS structure set (RTSTRUCT)",
    "PT-RTDOSE": "TPS plan dose (RTDOSE)",
    "PT-RTPLAN": "TPS plan (RTPLAN)",
    "PT-preMRI": "pre-operative MRI",
    "PT-intraMRI": "intra-operative MRI (the printed phantom's cavity geometry)",
    "PT-postMRI": "post-operative MRI",
}

ALIASES = {
    # Phase 1-3 notes
    "JD-1mm": "PT-CT1mm",
    "JD-2mm": "PT-CT2mm",
    "JD-CT-s3": "PT-CT-s3",
    "JD-MRI-pre": "PT-preMRI",
    "JD-MRI-intra": "PT-intraMRI",
    "JD-MRI-post": "PT-postMRI",
    # legacy keys of scripts/validation_realdata_proxies.py and friends
    "printed8": "PH8",
    "phantom8": "PH8",
    "printed_blank": "PH0",
    "tilefree": "PH0",
    "postop": "PT-CT2mm",
    "thincut": "PT-CT1mm",
}


class DatasetNotConfigured(RuntimeError):
    """``GT_DATA_ROOT`` unset, or a dataset id that resolves to no folder."""


def canonical_id(dataset_id):
    """``'JD-1mm'`` -> ``'PT-CT1mm'``; unknown ids pass through unchanged."""
    key = str(dataset_id).strip()
    return ALIASES.get(key, key)


def data_root(root=None):
    """The scan root as a :class:`Path`; ``root`` (a CLI flag) wins over the env."""
    value = root if root not in (None, "") else os.environ.get(ENV_ROOT, "")
    if not str(value).strip():
        raise DatasetNotConfigured(
            "%s is not set. Point it at the folder that holds the scans "
            "(bash: %s=/path/to/scans python ...; PowerShell: $env:%s='...'), "
            "and map dataset ids to folder names in a JSON file named by %s "
            "(or <%s>/%s); format: docs/datasets.example.json."
            % (ENV_ROOT, ENV_ROOT, ENV_ROOT, ENV_JSON, ENV_ROOT, DEFAULT_JSON_NAME))
    path = Path(str(value)).expanduser()
    if not path.is_dir():
        raise DatasetNotConfigured(
            "%s=%r is not a directory." % (ENV_ROOT, str(path)))
    return path


def load_dataset_map(datasets_json=None, root=None):
    """The id -> folder mapping (empty when no JSON file is configured).

    ``datasets_json`` (a CLI flag) wins over ``GT_DATASETS_JSON``; when both
    are unset, ``<root>/datasets.json`` is used if it exists.  Values may be
    folder names relative to the root or absolute paths; keys starting with
    ``_`` are comments.  Aliases are accepted as keys and canonicalised.
    """
    candidate = datasets_json if datasets_json not in (None, "") else os.environ.get(ENV_JSON, "")
    source = None
    if str(candidate).strip():
        source = Path(str(candidate)).expanduser()
        if not source.is_file():
            raise DatasetNotConfigured(
                "%s=%r does not exist (format: docs/datasets.example.json)."
                % (ENV_JSON, str(source)))
    elif root is not None:
        default = Path(root) / DEFAULT_JSON_NAME
        if default.is_file():
            source = default
    if source is None:
        return {}
    with open(source, "r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh)
        except ValueError as exc:
            raise DatasetNotConfigured("%s is not valid JSON: %s" % (source, exc)) from exc
    if not isinstance(raw, dict):
        raise DatasetNotConfigured(
            "%s must hold one JSON object {dataset id: folder}." % source)
    out = {}
    for key, value in raw.items():
        if str(key).startswith("_"):
            continue
        if not isinstance(value, str) or not value.strip():
            raise DatasetNotConfigured(
                "%s: entry %r must be a non-empty folder name or path." % (source, key))
        out[canonical_id(key)] = value
    return out


def dataset_path(dataset_id, root=None, datasets_json=None, must_exist=True):
    """Folder (or file) of ``dataset_id`` as a :class:`Path`.

    Parameters
    ----------
    dataset_id : str
        A key of :data:`DATASET_IDS` or an alias (:data:`ALIASES`).
    root, datasets_json : str, optional
        CLI overrides for ``GT_DATA_ROOT`` and ``GT_DATASETS_JSON``.
    must_exist : bool
        Raise :class:`DatasetNotConfigured` when the resolved path is absent
        (default).  ``False`` returns the path anyway (callers that want to
        print "not found at ..." and continue).
    """
    cid = canonical_id(dataset_id)
    base = data_root(root)
    mapping = load_dataset_map(datasets_json, base)
    if cid in mapping:
        folder = Path(mapping[cid]).expanduser()
        path = folder if folder.is_absolute() else base / folder
        how = "mapped by the datasets JSON"
    else:
        path = base / cid
        how = "no JSON entry; the folder literally named %r under %s" % (cid, ENV_ROOT)
    if must_exist and not path.exists():
        hint = ("Add %r to the JSON named by %s (or <%s>/%s) with the real folder name; "
                "format: docs/datasets.example.json." % (cid, ENV_JSON, ENV_ROOT, DEFAULT_JSON_NAME))
        raise DatasetNotConfigured(
            "dataset %r (%s) not found: %s does not exist (%s). %s"
            % (dataset_id, DATASET_IDS.get(cid, "unknown id"), str(path), how, hint))
    return path


def _main(argv=None):
    """``python -m gtcore.io.datasets [ID ...]``: print how each id resolves."""
    import argparse

    ap = argparse.ArgumentParser(description="Resolve dataset ids to folders.")
    ap.add_argument("ids", nargs="*", help="dataset ids (default: every known id)")
    ap.add_argument("--data-root", default=None, help="override %s" % ENV_ROOT)
    ap.add_argument("--datasets-json", default=None, help="override %s" % ENV_JSON)
    args = ap.parse_args(argv)
    ids = args.ids or list(DATASET_IDS)
    rc = 0
    for did in ids:
        try:
            p = dataset_path(did, root=args.data_root, datasets_json=args.datasets_json)
            print("%-12s -> %s" % (did, p))
        except DatasetNotConfigured as exc:
            print("%-12s -> MISSING: %s" % (did, exc))
            rc = 1
    return rc


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
