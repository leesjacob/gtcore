# Paper 2 — phase notes (numbers, commands, commits)

Each section is one Phase task of `paper2-requirements-to-code-rev3.md` §6,
appended by the agent that did it and merged by the coordinator.  Every
number that may appear in the paper is recorded here with the command that
produced it and the commit it was run at.  Dataset ids (PH8, PH0, JD-1mm, ...)
are the table in the common brief; paths are `$GT_DATA_ROOT/<id>`.

## 2026-11-02 — Phase 1 — footprint fit rcond 1e-3 (`p2/footprint-rcond`)

Task: §6 Phase 1 third checkbox; §3 F7; §4 item 12.  Commit `6198fa1`
(code + test), on top of `paper2/integration` = `aff83c0`.

### The defect (what the basis is)

`gtcore.interact._footprint_surface` reconstructs a placed tile's conformed
collagen sheet for `find_overlapping_tiles` by least-squares fitting a
quadratic height field `z(u, v) = c · [1, u, v, u², uv, v²]` (coordinates
scaled by the 10 mm tile half-size) through the tile's own conformed points:
4 corners at (±1, ±1), 4 seeds at (±0.5, ±0.5), inset anchor at (0, 0).
Every fit point has |u| = |v|, so the u² and v² columns coincide and the
u² − v² direction is unidentifiable:

| wall | σ₆/σ₁ of the 9 × 6 basis | at `rcond=1e-6` | at `rcond=1e-3` |
|---|---|---|---|
| flat (`plan_fixtures.flat_wall_mesh`) | ~1e-16 | dropped | dropped |
| icosphere r = 25 mm, subdivisions 4 (5120 faces) | median 8.0e-5, min 6.5e-6, max 2.1e-4 | **kept** → balloons | dropped |
| icosphere r = 25 mm, subdivisions 5 (20 480 faces) | median 2.4e-5, min 1.0e-6 | **kept** → balloons worse | dropped |
| marching-cubes synthetic cavity (`make_head_phantom`, spacing 1.0 / 0.8) | median 1.4e-2, min 1.4e-3 | kept (fine) | kept (unchanged) |
| PH8 printed phantom `body` mesh (72 396 faces) | median 1.5e-2 – 6e-2, min 6.4e-3 | kept (fine) | kept (unchanged) |

On a smooth sphere the 3 mm inset along converging normals keeps |u| = |v|
exactly; only mesh faceting (~0.02 mm sagitta for a subdivision-4 sphere)
lifts the ratio to ~1e-4, above 1e-6, so the old cutoff kept the direction
and divided faceting noise by 1e-4 into coefficients of ±100s.  The sampled
7 × 7 grid (bilinear span of the corners, where u² ≠ v²) then ballooned.
`rcond=1e-3` drops the direction and lstsq returns the minimum-norm fit
(equal u², v² coefficients).  `rcond=1e-4` is NOT enough (21 % / 13 % / 6 %
of sphere tiles still > 20 mm at r = 25 / 30 / 35); 1e-2 gives the same
result as 1e-3.  Fitting u² + v² as one column was rejected earlier
(`docs/optimize-notes.md`, coordinator check 2026-10-07) because real meshes
resolve anisotropic curvature with the separate columns; 1e-3 keeps that
(ratios on real meshes are ≥ 1.4e-3, median ~1e-2).

### Icosphere measurement (60 anchors × 3 spins = 180 full tiles per sphere)

Command (scratch script, no repo change; the committed test reproduces the
R = 25 / 30 / 35 rows with 40 anchors × 3 spins):
`python <scratch>/measure_ico.py <worktree>` at `aff83c0` with the cutoff
monkey-patched.  Bounding radius = max distance of the 49 samples from
their centroid (the quantity `find_overlapping_tiles` and
`plan.conflicts._footprints` use); nominal flat corner radius 14.14 mm.

| sphere | cutoff | radius median | p90 | max | min | tiles > 20 mm | max \|r\| − (R − 3) |
|---|---|---|---|---|---|---|---|
| r 25, sub 4 | 1e-6 | 33.57 | 136.4 | 1192.5 | 12.63 | 76 % | 1190 mm |
| r 25, sub 4 | **1e-3** | **12.63** | 12.64 | 12.64 | 12.63 | 0 % | 0.08 mm |
| r 30, sub 4 | 1e-6 | 27.42 | 106.5 | 219.7 | 12.85 | 59 % | 218 mm |
| r 30, sub 4 | **1e-3** | **12.85** | 12.86 | 12.86 | 12.85 | 0 % | 0.06 mm |
| r 35, sub 4 | 1e-6 | 16.51 | 53.9 | 125.9 | 13.02 | 40 % | 124 mm |
| r 35, sub 4 | **1e-3** | **13.02** | 13.02 | 13.03 | 13.02 | 0 % | 0.06 mm |
| r 25, sub 5 | 1e-6 | 91.65 | 498.1 | 3780.9 | 12.63 | 93 % | 3779 mm |
| r 25, sub 5 | **1e-3** | **12.63** | 12.64 | 12.64 | 12.63 | 0 % | 0.06 mm |

Analytic check: the conformer insets the sheet 3 mm along normals that
converge toward the centre, so the conformed corners sit at tangential
radius (1 − 3/R) · 14.14 = 12.45 / 12.73 / 12.93 mm (R = 25 / 30 / 35); the
fitted radius is that plus the < 0.3 mm through-plane spread.  Footprint
area (sum of the 72 grid triangles; nominal 400 mm²): 1e-6 median 1816 /
1317 / 847 mm² (max 63 557); 1e-3 328.8 / 337.1 / 343.9 mm², i.e. 1.03–1.06 ×
the planar projection (1 − 3/R)² · 400 = 309.8 / 324.0 / 334.4.  Half tiles
(fit points at |u| ≠ |v|) are unchanged: radius 9.92 mm, area 160.4 mm² on
r = 25 under both cutoffs (nominal 11.18 mm / 200 mm² × (1 − 3/R)).  Flat
wall: 14.1421 mm / 400.000 mm² under both.

Planner-level consequence (`find_overlapping_tiles`, 120 tiles per sphere,
40 anchors × 3 spins; "certain" = same-wall anchors < 18 mm apart, the
optimizer's proxy rule; "impossible" = anchors > 32 mm apart):

| sphere | cutoff | pairs flagged | impossible flagged | certain pairs | certain missed |
|---|---|---|---|---|---|
| r 25 | 1e-6 | 2313 | 907 | 1200 | 381 |
| r 25 | **1e-3** | 2180 | **0** | 1200 | **0** |
| r 30 | 1e-6 | 1344 | 432 | 867 | 249 |
| r 30 | **1e-3** | 1647 | **0** | 867 | **0** |
| r 35 | 1e-6 | 745 | 106 | 678 | 172 |
| r 35 | **1e-3** | 1229 | **0** | 678 | **0** |

### Tolerance chosen for `tests/test_footprint_rcond.py`

Tight: radius within 0.5 mm of (1 − 3/R) · 14.14, area within [1.0, 1.10] ×
(1 − 3/R)² · 400, every sample within 0.5 mm of the R − 3 sphere, for every
tile on r = 25 / 30 / 35.  Loose "nominal" band: radius within ±20 % of
14.14 mm, area within [0.70, 1.10] × 400 mm² (a correct fit is within 12 %
of nominal for any R ≥ 25 mm; the old fit's median is 2.4 × nominal at
r = 25).  Guard: `_footprint_surface(tile, rcond=1e-6)` on the same tiles
must balloon (≥ 25 % of tiles > 20 mm, max > 2 × nominal, median area >
440 mm²) and `find_overlapping_tiles` with the old cutoff must flag
impossible pairs and miss certain ones, while the new cutoff does neither.
`rcond` is exposed as a keyword (default `_FOOTPRINT_RCOND = 1e-3`) solely
for that; a test pins the default.  13 tests, 12.5 s.

### Effect on the optimizer (`gtcore/plan`, acceptance 3)

Command: `python <scratch>/measure_cavity.py <worktree>` (both cutoffs,
`build_candidates(h_mm=5, n_spins=3, kinds=("full","half"))`,
`build_conflicts(robust=False/True)`):

| mesh | candidates | footprints moved > 0.1 mm | planner-rule pairs (1e-6 → 1e-3) | robust pairs (1e-6 → 1e-3) | proxy-added | tiles > 20 mm (both) |
|---|---|---|---|---|---|---|
| `make_head_phantom(spacing=1.0, n_tiles=3, rng_seed=1)` (test fixture), 12 300 faces | 596 | 0 | 62 305 → 62 305 | 63 876 → 63 876 | 1571 | 8 |
| `make_head_phantom(spacing=0.8)` (test_overlap fixture), 20 080 faces | 608 | 0 | 62 787 → 62 787 | 63 969 → 63 969 | 1182 | 6 |

No conflict pair, footprint sample or footprint-area value changes on the
marching-cubes meshes; the `tests/test_plan*` expectations are unchanged.
The 8 / 6 candidates with bounding radius 20–34 mm on those meshes are a
different phenomenon: full-rank fits (σ₆/σ₁ = 2e-3 – 1e-2, residual
0.2–0.7 mm) through corners that sag 5–13 mm asymmetrically on lumpy wall
patches — the quadratic extrapolates steeply over the grid.  `rcond` cannot
touch them (and should not; they are not noise amplification).  The
optimizer's proxy rule (`conflicts.robust=True`) stays in force for those.

### PH8 printed phantom (acceptance 4)

Command: `GT_DATA_ROOT=... python <scratch>/ph8_footprints.py <worktree>
"$GT_DATA_ROOT/<PH8>"` at `6198fa1`; `load_volume` → `reconstruct(vol,
n_full_tiles="auto" | 8, n_half_tiles=0)` → `tiles.auto.to_placed_tiles`,
then the planner's adoption rule (`snap_to_wall(center) + conform_tile`) and
the V6 `as_implanted_conformed` rule; candidates `build_candidates(h_mm=5,
n_spins=3, eligible_faces=visible_faces(mesh, seed centroid))`.  Pipeline:
157 × 512 × 512, 0.592 × 0.592 × 1.0 mm, 32 seeds, 8 tiles in both modes,
28.6 s / 25.2 s (other agents running concurrently; upper bounds).  No
`cavity` mesh on the phantom — the wall is the `body` mesh (72 396 faces),
as in the V6 campaign.

| tile set (PH8) | n | radius median 1e-6 → 1e-3 | min | max | area median | tiles > 20 mm |
|---|---|---|---|---|---|---|
| `to_placed_tiles`, auto (flat sheets from the fit) | 8 | 13.26 → 13.26 | 10.46 | 14.68 | 351 | 0 |
| `to_placed_tiles`, N = 8 | 8 | 14.14 → 14.14 | 14.14 | 14.14 | 410 | 0 |
| planner snap + conform, auto | 8 | 16.79 → 16.79 | 13.23 | 28.16 | 573 | 3 |
| planner snap + conform, N = 8 | 8 | 16.03 → 16.03 | 13.62 | 21.22 | 507 | 1 |
| V6 `as_implanted_conformed`, auto / N = 8 | 8 | 16.79 / 16.03 (unchanged) | | 28.16 / 21.22 | 552 / 507 | 3 / 1 |
| `build_candidates` h 5 mm, 3 spins, visible wall | 82 | 16.41 → 16.41 | 13.27 | 58.99 | 503 | 13 |

σ₆/σ₁ on every PH8 tile ≥ 6.4e-3 (medians 1.5e-2 – 6.2e-2): nothing in the
(1e-6, 1e-3] window, so the before/after medians are identical.  The
rcond change is a no-op on the printed phantom and on the synthetic
cavities; it matters on smooth, symmetric walls (the icosphere, and any
smooth/decimated clinical mesh), where it removes the ballooning entirely.
The 1–3 conformed PH8 tiles and 13 / 82 candidates above 20 mm are the
rough-wall extrapolation case noted above (the `body` mesh includes the
printed cavity's lumpy surface), unchanged and out of this task's scope.

### Tests

- `python -m pytest -q -p no:cacheprovider tests/test_footprint_rcond.py` at
  `6198fa1`: 13 passed in 12.5 s.
- Planner / optimizer files (`python -m pytest -q -p no:cacheprovider
  tests/test_overlap.py tests/test_interact.py tests/test_planner*.py
  tests/test_plan_*.py tests/test_validation_optimize_smoke.py`) at
  `6198fa1`: 306 passed, 14 skipped in 391.9 s (skips are the pre-existing
  optional-solver / data-dependent ones; no expectation changed).
- Full suite `GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider` at
  `6198fa1` (the flag is not yet read by the tree at `aff83c0`; set for the
  record): 828 passed, 14 skipped in 613.3 s (842 collected = the 829
  baseline at `aff83c0` + the 13 new tests; 0 failed, 0 errors).

Hardware for the timings: AMD Ryzen 5 7600X, 31 GB RAM, Windows 11, with
other agents running concurrently (timings are upper bounds).
