"""Regression test for the planner footprint fit's lstsq cutoff
(``gtcore.interact._footprint_surface``, ``rcond`` 1e-6 -> 1e-3).

Paper 2 Phase 1 (requirements rev 3, section 3 F7 / section 4 item 12; the
defect is "Open decision 14" in ``docs/optimize-notes.md``).

The footprint sampler fits a quadratic height field ``z(u, v)`` through the
tile's own conformed points (4 corners, seeds, inset anchor) in the basis
``[1, u, v, u^2, uv, v^2]``.  At the conformer's placement every fit point has
``|u| == |v|``, so the ``u^2 - v^2`` direction is unidentifiable: on a flat
wall its singular value is ~1e-16 of the largest (dropped by any cutoff), on
a smooth sphere mesh faceting lifts it to ~1e-4 -- above the old ``1e-6``
cutoff, so the direction was KEPT and faceting noise was divided by 1e-4 into
coefficients of hundreds.  The sampled grid (where ``u^2 != v^2``) then
ballooned: on an r = 25 mm icosphere the bounding radius had median ~35 mm,
p90 > 100 mm, against the 14.1 mm nominal corner radius, and the planner's
``find_overlapping_tiles`` flagged pairs whose anchors were > 32 mm apart.

Tolerance, from what the fit gives on a flat wall and on spheres:

* flat wall (``plan_fixtures.flat_wall_mesh``): bounding radius exactly
  ``10 * sqrt(2) = 14.142`` mm, area exactly 400 mm^2, for either cutoff;
* sphere of radius R: the conformer insets the sheet 3 mm along normals that
  converge toward the centre, so the conformed corners sit at tangential
  radius ``(1 - 3/R) * 14.142`` (12.45 / 12.73 / 12.93 mm for R = 25 / 30 /
  35) and the correct fit's bounding radius is that plus the < 0.3 mm
  through-plane spread (measured 12.63 / 12.85 / 13.02 mm, identical for
  every anchor and spin); the sampled area is the curved cap over the
  ``(1 - 3/R)``-scaled square, 3-6 % above its planar projection
  ``(1 - 3/R)^2 * 400`` (measured 329 / 337 / 344 mm^2).

So for any cavity of radius >= 25 mm a correct fit lies within 12 % of the
nominal radius and within [0.77, 1.0] of the nominal area, while the
ballooned fit's median radius is 2.4x nominal at R = 25.  The tests below
assert the tight analytic expectations (0.5 mm on the radius, +10 % on the
area, 0.5 mm from the R - 3 sphere) AND the loose "nominal" band (radius
within +-20 % of 14.142 mm, area within [0.70, 1.10] x 400 mm^2), which the
old cutoff violates on the majority of sphere tiles.  Half tiles have
``|u| != |v|`` at their fit points and are well conditioned under both
cutoffs; they are included to pin the nominal 10 x 20 mm footprint.
"""
from __future__ import annotations

import functools

import numpy as np
import pytest
import trimesh

import plan_fixtures as pf
import gtcore.interact as interact
from gtcore.interact import (
    SEED_WALL_OFFSET_MM,
    TILE_HALF_SIZE_MM,
    _FOOTPRINT_RCOND,
    _OVERLAP_GRID_N,
    _footprint_surface,
    _grid_triangles,
    conform_tile,
    find_overlapping_tiles,
    snap_to_wall,
)

OLD_RCOND = 1e-6
NEW_RCOND = 1e-3
BALLOON_MM = 20.0          # tests/test_plan_conflicts.FOOTPRINT_SANE_MM
NOMINAL_TOL = 0.20         # +-20 % band on the nominal corner radius
AREA_BAND = (0.70, 1.10)   # band on the nominal area
IMPOSSIBLE_PAIR_MM = 32.0  # two 20 mm sheets cannot touch across this chord
CERTAIN_PAIR_MM = 18.0     # ... and certainly overlap inside this one (proxy rule)

SPHERE_RADII_MM = (25.0, 30.0, 35.0)   # resection-cavity scale
N_ANCHORS = 40
N_SPINS = 3


def _nominal(kind):
    """(corner radius mm, area mm^2) of the flat manufactured footprint."""
    cu = TILE_HALF_SIZE_MM if kind == "full" else TILE_HALF_SIZE_MM / 2.0
    cv = TILE_HALF_SIZE_MM
    return float(np.hypot(cu, cv)), float(4.0 * cu * cv)


def _radius(points):
    c = points.mean(axis=0)
    return float(np.linalg.norm(points - c[None, :], axis=1).max())


def _area(points):
    tri = _grid_triangles(_OVERLAP_GRID_N)
    a, b, c = points[tri[:, 0]], points[tri[:, 1]], points[tri[:, 2]]
    return float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())


def _place_on_sphere(mesh, radius, kind, n_anchors=N_ANCHORS, n_spins=N_SPINS,
                     seed=0):
    """Conform tiles the way the planner does (``snap_to_wall`` +
    ``conform_tile``) at deterministic pseudo-random anchors and spins."""
    rng = np.random.default_rng(seed)
    dirs = rng.normal(size=(n_anchors, 3))
    dirs /= np.linalg.norm(dirs, axis=1)[:, None]
    tiles = []
    for d in dirs:
        surf, n_in = snap_to_wall(mesh, d * radius)
        helper = np.array([0.0, 0.0, 1.0]) if abs(n_in[2]) < 0.9 \
            else np.array([1.0, 0.0, 0.0])
        t1 = np.cross(n_in, helper)
        t1 /= np.linalg.norm(t1)
        t2 = np.cross(n_in, t1)
        for k in range(n_spins):
            th = 0.5 * np.pi * k / n_spins
            hint = np.cos(th) * t1 + np.sin(th) * t2
            tiles.append(conform_tile(mesh, surf, n_in, hint, kind=kind))
    return tiles


@pytest.fixture(scope="module", params=SPHERE_RADII_MM,
                ids=["R%d" % int(r) for r in SPHERE_RADII_MM])
def sphere(request):
    radius = float(request.param)
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=radius)
    return radius, mesh, _place_on_sphere(mesh, radius, "full")


@pytest.fixture(scope="module")
def flat_tiles():
    mesh = pf.flat_wall_mesh(size_mm=90.0)
    tiles = []
    for x in (-20.0, 0.0, 20.0):
        for y in (-20.0, 0.0, 20.0):
            surf, n_in = snap_to_wall(mesh, np.array([x, y, 0.0]))
            for th in (0.0, 30.0, 60.0):
                hint = np.array([np.cos(np.radians(th)),
                                 np.sin(np.radians(th)), 0.0])
                tiles.append(conform_tile(mesh, surf, n_in, hint, kind="full"))
    return tiles


# ------------------------------------------------------------- the constant
def test_default_cutoff_is_1e_minus_3():
    """A silent revert of the cutoff must fail here, not in a geometry test."""
    assert _FOOTPRINT_RCOND == NEW_RCOND
    assert _footprint_surface.__defaults__[-1] == NEW_RCOND


# ---------------------------------------------------------------- flat wall
@pytest.mark.parametrize("rcond", [NEW_RCOND, OLD_RCOND])
def test_flat_wall_footprint_is_exact(flat_tiles, rcond):
    """On a flat wall the ``u^2 - v^2`` singular value is ~1e-16 and both
    cutoffs drop it: the footprint is the exact 20 x 20 mm square."""
    nom_r, nom_a = _nominal("full")
    for tile in flat_tiles:
        pts, nrm = _footprint_surface(tile, rcond=rcond)
        assert _radius(pts) == pytest.approx(nom_r, abs=1e-3)
        assert _area(pts) == pytest.approx(nom_a, abs=1e-2)
        assert np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-9)


# ------------------------------------------------------------------- sphere
def test_sphere_footprint_within_nominal_tolerance(sphere):
    """Every tile on every sphere: the fitted footprint is the conformed
    sheet -- bounding radius within 0.5 mm of ``(1 - 3/R) * 14.14`` and
    within +-20 % of the nominal 14.14 mm; area between the planar
    projection ``(1 - 3/R)^2 * 400`` and 1.10x that, and within
    [0.70, 1.10] x 400 mm^2; every sample within 0.5 mm of the R - 3 mm
    sphere the conformed points lie on; normals unit and pointing inward."""
    radius, _mesh, tiles = sphere
    nom_r, nom_a = _nominal("full")
    shrink = 1.0 - SEED_WALL_OFFSET_MM / radius
    exp_r = shrink * nom_r
    exp_a = shrink ** 2 * nom_a
    radii, areas = [], []
    for tile in tiles:
        pts, nrm = _footprint_surface(tile)
        r, a = _radius(pts), _area(pts)
        radii.append(r)
        areas.append(a)
        assert abs(r - exp_r) <= 0.5, (radius, r, exp_r)
        assert exp_a <= a <= 1.10 * exp_a, (radius, a, exp_a)
        off_sphere = np.abs(np.linalg.norm(pts, axis=1)
                            - (radius - SEED_WALL_OFFSET_MM))
        assert float(off_sphere.max()) <= 0.5, (radius, off_sphere.max())
        assert np.allclose(np.linalg.norm(nrm, axis=1), 1.0, atol=1e-9)
        assert np.all((nrm * (-pts)).sum(axis=1) > 0.0)
    radii = np.asarray(radii)
    areas = np.asarray(areas)
    assert np.all(np.abs(radii / nom_r - 1.0) <= NOMINAL_TOL)
    assert np.all((AREA_BAND[0] * nom_a <= areas)
                  & (areas <= AREA_BAND[1] * nom_a))
    # the defect signature from test_plan_conflicts: nothing reaches 20 mm
    assert float(radii.max()) < BALLOON_MM


def test_half_tile_footprint_on_sphere():
    """Half tiles (10 x 20 mm, corner radius 11.18 mm, area 200 mm^2) are
    well conditioned under both cutoffs and shrink by the same (1 - 3/R)."""
    radius = 25.0
    mesh = trimesh.creation.icosphere(subdivisions=4, radius=radius)
    tiles = _place_on_sphere(mesh, radius, "half", n_anchors=20, n_spins=2,
                             seed=1)
    nom_r, nom_a = _nominal("half")
    shrink = 1.0 - SEED_WALL_OFFSET_MM / radius
    for tile in tiles:
        for rcond in (NEW_RCOND, OLD_RCOND):
            pts, _nrm = _footprint_surface(tile, rcond=rcond)
            assert abs(_radius(pts) - shrink * nom_r) <= 0.5
            assert shrink ** 2 * nom_a <= _area(pts) <= 1.10 * shrink ** 2 * nom_a


# --------------------------------------------------- the old value balloons
def test_old_cutoff_reproduces_ballooning(sphere):
    """The guard: with ``rcond=1e-6`` the same tiles balloon (bounding radius
    > 20 mm for a substantial fraction, max > 2x nominal, median far above
    the new fit's), so the test fails if the defect returns."""
    radius, _mesh, tiles = sphere
    nom_r, nom_a = _nominal("full")
    old = np.array([_radius(_footprint_surface(t, rcond=OLD_RCOND)[0])
                    for t in tiles])
    new = np.array([_radius(_footprint_surface(t, rcond=NEW_RCOND)[0])
                    for t in tiles])
    frac_balloon = float((old > BALLOON_MM).mean())
    # measured: 0.76 / 0.59 / 0.40 of the tiles for R = 25 / 30 / 35 mm
    assert frac_balloon >= 0.25, (radius, frac_balloon)
    assert float(old.max()) > 2.0 * nom_r, (radius, old.max())
    assert float(np.median(old)) > float(np.median(new)) + 1.0
    assert float(new.max()) < BALLOON_MM
    # the old cutoff's smallest footprints are the correct ones (same fit
    # whenever the near-null direction happened to be dropped or harmless)
    assert float(old.min()) == pytest.approx(float(new.min()), abs=0.05)
    # area tells the same story
    old_a = np.array([_area(_footprint_surface(t, rcond=OLD_RCOND)[0])
                      for t in tiles])
    assert float(np.median(old_a)) > AREA_BAND[1] * nom_a


def test_old_cutoff_flags_impossible_and_misses_certain_overlaps(sphere,
                                                                  monkeypatch):
    """Planner-level consequence.  Two full tiles on the same wall whose
    anchors are < 18 mm apart certainly overlap (each sheet contains the
    10 mm disc about its anchor; ``gtcore.plan.conflicts.PROXY_ANCHOR_MM``),
    and two 20 mm sheets cannot touch across a > 32 mm chord.  With the new
    cutoff ``find_overlapping_tiles`` flags every certain pair and no
    impossible one; with the old cutoff it both flags impossible pairs
    (907 / 432 / 106 of them for R = 25 / 30 / 35, 120 tiles) and MISSES
    certain ones (381 / 249 / 172 of 1200 / 867 / 678) -- the ballooned
    sheet is sampled somewhere else entirely.  ``find_overlapping_tiles``
    looks the sampler up in the module at call time, so the cutoff is
    swapped by patching it."""
    radius, _mesh, tiles = sphere
    anchors = np.array([t.anchor_ras for t in tiles])
    d = np.linalg.norm(anchors[:, None] - anchors[None], axis=2)
    n = len(tiles)
    certain = {(i, j) for i in range(n) for j in range(i + 1, n)
               if d[i, j] < CERTAIN_PAIR_MM}
    assert len(certain) > 100

    def impossible(pairs):
        return sum(1 for i, j in pairs if d[i, j] > IMPOSSIBLE_PAIR_MM)

    new_pairs = find_overlapping_tiles(tiles)
    assert impossible(new_pairs) == 0
    assert len(certain - set(new_pairs)) == 0

    monkeypatch.setattr(interact, "_footprint_surface",
                        functools.partial(_footprint_surface, rcond=OLD_RCOND))
    old_pairs = interact.find_overlapping_tiles(tiles)
    assert impossible(old_pairs) > 0, radius
    assert len(certain - set(old_pairs)) > 0, radius
