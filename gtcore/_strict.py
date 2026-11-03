"""Strict mode: stop fallback handlers from hiding failures.

A few places in gtcore attempt an optional refinement -- the
surface-constrained tile fit, the bent-tile fit on a completed tile, the
HR-CTV display shell -- and fall back to "not available" when it fails, so
that one bad tile cannot take the whole reconstruction down.  Those
fallbacks used to be silent ``except Exception: pass``-style handlers.
Every such site now goes through :func:`swallow_or_raise`:

* by default the exception is logged **with its traceback** through the
  :mod:`logging` module (logger named after the calling module, level
  WARNING, so it reaches stderr even when nothing configured logging) and
  the caller takes its fallback branch -- the behaviour the planner and
  the pipeline rely on;
* with the environment variable ``GTCORE_STRICT`` set to ``1``, ``true``
  or ``yes`` (case-insensitive) the exception is re-raised instead, so
  the failure surfaces where it happened.

The test suite sets the flag (``tests/conftest.py``); validation runs
should too (``GTCORE_STRICT=1 python scripts/...``).
"""
from __future__ import annotations

import logging
import os

STRICT_ENV = "GTCORE_STRICT"
_TRUTHY = frozenset({"1", "true", "yes"})


def strict_mode() -> bool:
    """True when ``GTCORE_STRICT`` is ``1``, ``true`` or ``yes`` (any case)."""
    return os.environ.get(STRICT_ENV, "").strip().lower() in _TRUTHY


def swallow_or_raise(logger: logging.Logger, msg: str, exc: BaseException) -> None:
    """Log ``exc`` with its traceback under ``msg``; re-raise it in strict mode.

    Call it from inside the ``except`` block *after* assigning the fallback
    value, so the caller's state is consistent whether or not the exception
    propagates.  ``msg`` should say what was being attempted and for which
    object (tile id, structure name) -- the traceback says where.
    """
    logger.warning("%s: %s: %s", msg, type(exc).__name__, exc, exc_info=exc)
    if strict_mode():
        raise exc
