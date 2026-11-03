"""``GTCORE_STRICT``: fallback handlers log and continue by default, re-raise
when the flag is set.

- :func:`gtcore.strict_mode` reads the environment variable;
- :func:`gtcore._strict.swallow_or_raise` logs the exception with its
  traceback through ``logging`` and re-raises it only in strict mode;
- each of the three converted sites (surface cross-check in
  ``tiles.auto._finish``, bent-tile fit on a counted tile in
  ``tiles.auto.fit_tiles_prior``, display shell in ``dose.hrctv.build_hrctv``)
  is driven into its ``except`` branch by a monkeypatched inner call that
  raises, once with the flag unset (fallback value, one WARNING record with
  the traceback) and once with it set (the forced exception propagates).

``tests/conftest.py`` sets the flag for the whole suite; these tests set or
clear it explicitly so they do not depend on that default.
"""
from __future__ import annotations

import logging

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

import gtcore
from gtcore._strict import STRICT_ENV, strict_mode, swallow_or_raise
from gtcore.tiles import ImplantPrior, fit_tiles, fit_tiles_prior


class Forced(RuntimeError):
    """The failure the tests inject."""


def _raiser(*_args, **_kwargs):
    raise Forced("forced failure for the strict-mode test")


# ------------------------------------------------------------------ helper
@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes", " 1 "])
def test_strict_mode_truthy_values(monkeypatch, value):
    monkeypatch.setenv(STRICT_ENV, value)
    assert strict_mode() is True
    assert gtcore.strict_mode is strict_mode          # exported at package level


@pytest.mark.parametrize("value", [None, "", "0", "false", "no", "off", "2"])
def test_strict_mode_falsy_values(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(STRICT_ENV, raising=False)
    else:
        monkeypatch.setenv(STRICT_ENV, value)
    assert strict_mode() is False


def test_swallow_logs_with_traceback_when_unset(monkeypatch, caplog):
    monkeypatch.delenv(STRICT_ENV, raising=False)
    log = logging.getLogger("gtcore.test_strict_mode")
    try:
        raise Forced("inner boom")
    except Forced as exc:
        with caplog.at_level(logging.WARNING, logger="gtcore"):
            swallow_or_raise(log, "doing the thing for tile 3", exc)   # must not raise
    assert len(caplog.records) == 1
    rec = caplog.records[0]
    assert rec.levelno == logging.WARNING
    assert rec.name == "gtcore.test_strict_mode"
    assert rec.exc_info is not None and rec.exc_info[0] is Forced
    assert "doing the thing for tile 3" in rec.getMessage()
    assert "Forced: inner boom" in rec.getMessage()
    assert "Traceback (most recent call last)" in caplog.text
    assert 'raise Forced("inner boom")' in caplog.text


def test_swallow_reraises_same_exception_when_set(monkeypatch, caplog):
    monkeypatch.setenv(STRICT_ENV, "1")
    log = logging.getLogger("gtcore.test_strict_mode")
    err = Forced("inner boom")
    with caplog.at_level(logging.WARNING, logger="gtcore"):
        with pytest.raises(Forced) as info:
            try:
                raise err
            except Forced as exc:
                swallow_or_raise(log, "doing the thing", exc)
    assert info.value is err                            # the same object, not a wrapper
    assert len(caplog.records) == 1                     # logged before re-raising


# ------------------------------------------------------------- call sites
def _three_tiles():
    """Two well-formed 10 mm tiles + the crumpled phantom tile that fails the
    chord gates (from test_tiles_degraded): the count-constrained fit
    recovers it only through degraded completion, so with the count given
    it is the tile ``fit_tiles_prior`` runs its own bent-tile fit on."""
    t1 = np.array([1.0, 0.0, 0.0])
    t2 = np.array([0.0, 1.0, 0.0])
    centers, axes = [], []
    for c in (np.array([0.0, 0.0, 0.0]), np.array([30.0, 0.0, 0.0])):
        centers.extend(c + a * t1 + b * t2
                       for a, b in ((-5, -5), (-5, 5), (5, -5), (5, 5)))
        axes.extend([t1] * 4)
    crumpled = np.array([[60.0, 0.0, 0.0], [60.8, 5.1, 2.8],
                         [51.6, 0.5, 2.5], [53.2, 4.6, 4.5]])
    centers.extend(list(crumpled))
    a3 = np.array([0.98, 0.2, 0.0]) / np.linalg.norm([0.98, 0.2, 0.0])
    axes.extend([t1, t1, a3, t1])
    return np.array(centers, dtype=float), np.array(axes, dtype=float)


@pytest.fixture(scope="module")
def sphere():
    """Any mesh with faces: the surface fit is monkeypatched where it is
    used, and for the HR-CTV a sphere is the standard wall."""
    return trimesh.creation.icosphere(subdivisions=3, radius=15.0)


# site 1: gtcore/tiles/auto.py _finish -- surface cross-check per tile
def test_site_surface_fit_swallows_and_logs(monkeypatch, caplog, sphere):
    monkeypatch.delenv(STRICT_ENV, raising=False)
    # _finish imports fit_on_surface from .surface at call time
    monkeypatch.setattr("gtcore.tiles.surface.fit_on_surface", _raiser)
    centers, axes = _three_tiles()
    with caplog.at_level(logging.WARNING, logger="gtcore"):
        res = fit_tiles(centers[:8], axes[:8], "auto", mesh=sphere)
    assert res.n_selected == 2
    assert all(p.surface is None for p in res.all_tiles)
    recs = [r for r in caplog.records if r.name == "gtcore.tiles.auto"]
    assert len(recs) == 2                               # one per tile
    assert all("surface fit (fit_on_surface) failed for tile" in r.getMessage()
               for r in recs)
    assert all(r.exc_info is not None and r.exc_info[0] is Forced for r in recs)


def test_site_surface_fit_reraises_when_strict(monkeypatch, sphere):
    monkeypatch.setenv(STRICT_ENV, "1")
    monkeypatch.setattr("gtcore.tiles.surface.fit_on_surface", _raiser)
    centers, axes = _three_tiles()
    with pytest.raises(Forced):
        fit_tiles(centers[:8], axes[:8], "auto", mesh=sphere)


# site 2: gtcore/tiles/auto.py fit_tiles_prior -- bent-tile fit on a counted
# tile the exact fit left rigid (degraded completion)
def _prior_fit(centers, axes):
    return fit_tiles_prior(centers, axes, ImplantPrior(n_full=3),
                           complete_degraded=True, cover=False)


def test_site_counted_deform_branch_is_reached():
    """Guard for the tests below: with the real fit the crumpled tile is
    completed as a rigid pose and ``fit_tiles_prior`` then bends it."""
    centers, axes = _three_tiles()
    res = _prior_fit(centers, axes)
    assert res.n_selected == 3
    degraded = [p for p in res.tiles if p.degraded]
    assert len(degraded) == 1
    assert degraded[0].deform is not None               # the branch ran


def test_site_counted_deform_swallows_and_logs(monkeypatch, caplog):
    monkeypatch.delenv(STRICT_ENV, raising=False)
    # the name fit_tiles_prior calls; fit.py imports its own copy locally,
    # so the count-constrained selection itself is unaffected
    monkeypatch.setattr("gtcore.tiles.auto.fit_deformable", _raiser)
    centers, axes = _three_tiles()
    with caplog.at_level(logging.WARNING, logger="gtcore"):
        res = _prior_fit(centers, axes)
    assert res.n_selected == 3
    degraded = [p for p in res.tiles if p.degraded]
    assert len(degraded) == 1 and degraded[0].deform is None
    recs = [r for r in caplog.records if r.name == "gtcore.tiles.auto"]
    assert len(recs) == 1
    assert "bent-tile fit (fit_deformable) failed for counted tile" in recs[0].getMessage()
    assert recs[0].exc_info is not None and recs[0].exc_info[0] is Forced


def test_site_counted_deform_reraises_when_strict(monkeypatch):
    monkeypatch.setenv(STRICT_ENV, "1")
    monkeypatch.setattr("gtcore.tiles.auto.fit_deformable", _raiser)
    centers, axes = _three_tiles()
    with pytest.raises(Forced):
        _prior_fit(centers, axes)


# site 3: gtcore/dose/hrctv.py build_hrctv -- display shell
def test_site_hrctv_shell_swallows_and_logs(monkeypatch, caplog, sphere):
    from gtcore.dose.hrctv import build_hrctv
    monkeypatch.delenv(STRICT_ENV, raising=False)
    # build_hrctv imports mask_to_mesh from ..segment.surface at call time
    monkeypatch.setattr("gtcore.segment.surface.mask_to_mesh", _raiser)
    with caplog.at_level(logging.WARNING, logger="gtcore"):
        h = build_hrctv(sphere, depth_mm=5.0, spacing_mm=2.0, with_mesh=True)
    assert h.mesh is None
    assert h.mask.any()                                 # the volume itself is intact
    recs = [r for r in caplog.records if r.name == "gtcore.dose.hrctv"]
    assert len(recs) == 1
    assert "HR-CTV display-shell extraction failed" in recs[0].getMessage()
    assert recs[0].exc_info is not None and recs[0].exc_info[0] is Forced


def test_site_hrctv_shell_reraises_when_strict(monkeypatch, sphere):
    from gtcore.dose.hrctv import build_hrctv
    monkeypatch.setenv(STRICT_ENV, "1")
    monkeypatch.setattr("gtcore.segment.surface.mask_to_mesh", _raiser)
    with pytest.raises(Forced):
        build_hrctv(sphere, depth_mm=5.0, spacing_mm=2.0, with_mesh=True)
