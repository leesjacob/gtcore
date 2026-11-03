"""Suite-wide configuration.

The whole suite runs in strict mode: every ``swallow_or_raise`` fallback in
gtcore re-raises instead of logging and continuing, so a test cannot pass
on a silently degraded result.  ``setdefault`` keeps an explicit
``GTCORE_STRICT=0`` in the environment effective for anyone who wants to
see the fallback behaviour.  Set at import so it precedes every gtcore
import during collection.
"""
import os

os.environ.setdefault("GTCORE_STRICT", "1")
