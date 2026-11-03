# Paper 2 — phase notes

Dated sections, one per task; each number carries the command that produced
it and the commit it was run at. Timings are wall clock on the Paper 2
machine (AMD Ryzen 5 7600X, 6 cores / 12 threads, 31 GB RAM, Windows 11)
with other agents running concurrently: upper bounds.

## 2026-11-02 — Phase 1 — hidden exceptions under `GTCORE_STRICT` (task `strict-exceptions`)

Branch `p2/strict-exceptions`, code commit `bcb7a22` (requirements rev 3
§4 item 12, §6 Phase 1 fourth checkbox).

### What changed

- `gtcore/_strict.py` (new): `strict_mode()` reads `GTCORE_STRICT`
  (truthy: `1`, `true`, `yes`, case-insensitive, whitespace-stripped);
  `swallow_or_raise(logger, msg, exc)` logs the exception **with its
  traceback** through `logging` at WARNING (so it reaches stderr even with
  no logging configured) and re-raises the same exception object when
  strict. `strict_mode` is exported as `gtcore.strict_mode`.
- The three bare `except Exception:` fallbacks now call it, after assigning
  the same fallback value they always did, with a module logger
  (`logging.getLogger(__name__)`):
  1. `gtcore/tiles/auto.py` `_finish` — per-tile surface cross-check
     (`fit_on_surface`); fallback `pose.surface = None`.
  2. `gtcore/tiles/auto.py` `fit_tiles_prior` — bent-tile fit
     (`fit_deformable`) on a counted full tile the exact fit left rigid,
     i.e. a degraded-completion tile; fallback `pose.deform = None`.
  3. `gtcore/dose/hrctv.py` `build_hrctv` — HR-CTV display-shell extraction
     (`mask_to_mesh` + far-face trim); fallback `mesh=None`, rind volume
     untouched.
- `tests/conftest.py` (new, shared file): `os.environ.setdefault("GTCORE_STRICT", "1")`
  at import, so the whole suite runs strict; an explicit `GTCORE_STRICT=0`
  in the environment still wins.
- `tests/test_strict_mode.py` (new, 22 tests): flag parsing; helper with
  flag unset (no raise, one WARNING record, `exc_info` set, traceback text
  in `caplog.text`) and set (same object re-raised, still logged); each of
  the three sites driven into its `except` branch by a monkeypatched inner
  call (`gtcore.tiles.surface.fit_on_surface`, `gtcore.tiles.auto.fit_deformable`,
  `gtcore.segment.surface.mask_to_mesh`), once swallowing and logging, once
  re-raising; plus a guard test that the site-2 branch is actually reached
  (two good tiles + the crumpled phantom tile from `test_tiles_degraded`,
  `ImplantPrior(n_full=3)`, `complete_degraded=True` → one `degraded` tile
  with `deform` set).
- `scripts/strict_pipeline_check.py` (new): the acceptance-3 driver (below).

### Other `except Exception` handlers in `gtcore/` (audit, not converted)

`grep -rn -E "except Exception|except:" gtcore/ --include=*.py` at `aff83c0`:
91 sites, of which 70 are in `gtcore/planner.py` (the PyVista front-end).
Judgement per site; none is the identical "optional refinement falls back
to None" pattern of the three converted sites, so none was converted.

| Site | Judgement |
|---|---|
| `planner.py` (70 sites, e.g. 507, 541, 613, 1001, 1457, 1705, 2056, 2195, 2435) | Legitimate UI stay-alive: remove-actor / widget / key-binding / status-line calls on a live VTK interactor; failures surface in the status line (`"optimize failed: %r"`, `"dose update failed"`, `"isodose extraction failed"`). Not core-algorithm paths. |
| `interact.py:134` | Legitimate fallback: `mesh.centroid` → vertex mean when trimesh cannot compute it. |
| `interact.py:162` | Legitimate fallback: barycentric vertex normals → face normals. |
| `interact.py:257` | Borderline: ray-cast (`intersects_location`) failure silently leaves tiles un-snapped (`pass`). Candidate for the helper if the footprint fit (`rcond`, F7) work touches it; owned by another Phase 1 task. |
| `io/dicom.py:86`, `:242` | Legitimate: both `warnings.warn` with the reason and fall back (header meta empty; SimpleITK reader). |
| `io/dicom.py:136` | Legitimate: slice-geometry probe returns None → caller uses SimpleITK's geometry. |
| `phantom/generate.py:516`, `:541` | Legitimate: optional skimage streak-artifact synthesis (returns `ok`); synthetic data only. |
| `plan/api.py:114` | Legitimate: user-supplied `log` callback must not take the optimizer down. |
| `plan/candidates.py:268` | Legitimate by design (comment: "the conformer treats this as all-miss"); a chunk of rays that fails is counted as misses. |
| `plan/candidates.py:486` | Legitimate: `conform_error` is counted in `n_rejected` and reported. |
| `plan/candidates.py:772` | Legitimate: `mesh.volume` only when watertight; None otherwise. |
| `plan/milp.py:569`, `:892` | Legitimate: solver failure returns `status="error"` with the message / NaN bound (`:892` says nothing — acceptable for a bound). |
| `plan/milp.py:755` | Legitimate (commented): greedy warm start is a convenience. |
| `plan/report.py:97`, `:155` | Legitimate: optional import / `git rev-parse` probe. |
| `plan/report.py:288`, `:316`, `:337` | Legitimate: each appends `"<stage> failed: Type: msg"` to the report `notes`. |
| `plan/solvers.py:247` | Legitimate: feature probe ("is `objective.<name>` implemented") — any non-`NotImplementedError` means yes. |
| `plan/solvers.py:1043`, `:1091` | Legitimate: Nelder–Mead cost of an unplaceable tile = 1e6 / `nm_failed` counted and reported. |
| `seeds/detect.py:298` | Legitimate fallback: k-means split failure keeps the unsplit blob. |
| `tiles/deform.py:539` | Borderline: a `least_squares` failure in one restart just skips that restart silently; the other restarts still run. Diagnosable through `n_evals`; not a hidden-failure site in the sense of §4 item 12. |
| `tiles/surface.py:152` | Legitimate: cost of an unplaceable pose = 1e6 inside the optimiser loop. |

### Commands

```
# from the worktree root; conftest sets GTCORE_STRICT=1 for the suite
python -m pytest -q
GTCORE_STRICT=1 python -m pytest -q tests/test_strict_mode.py

# acceptance 3: the pipeline on PH8 and JD-1mm under strict mode
GTCORE_STRICT=1 python scripts/strict_pipeline_check.py "$GT_DATA_ROOT/<PH8>" PH8 8
GTCORE_STRICT=1 python scripts/strict_pipeline_check.py "$GT_DATA_ROOT/<JD-1mm>" JD-1mm 8
```

Note for anyone running scripts from a worktree: the venv has an editable
install of the **main checkout**, so `python some_script.py` imports that
checkout's `gtcore` unless the script inserts its repo root on `sys.path`
(12 of 22 scripts in `scripts/` do; `validation_realdata_proxies.py` does
not) or `PYTHONPATH` points at the worktree. `python -m pytest` from the
worktree root is unaffected (cwd is on the path). The runs below were made
with the worktree's `gtcore` (the driver prints which one it imported).

### Results — JD-1mm (204 slices, 0.52 × 0.52 × 1.0 mm), commit `bcb7a22`

`GTCORE_STRICT=1 python scripts/strict_pipeline_check.py "$GT_DATA_ROOT/<JD-1mm>" JD-1mm 8`
— nothing raised; script exit 0. Run twice (before and after the data
move, see the layout note below) with identical results; timings are from
the second run.

| Pass | Result | Sites exercised |
|---|---|---|
| 1 `reconstruct(auto)` | OK, 49.6 s; 31 seeds; 6 tiles (n_selected 6); surface fits 6/6, deform 6/6; meshes brain / skull / cavity (24,750 faces) | site 1 on 6 tiles — no failure |
| 2 `reconstruct(n_full_tiles=8)` | OK, 20.9 s; 31 seeds; 7 tiles (`TileFitResult`, chord score; no mesh, no deform) | none (the pipeline's count path is plain `fit_tiles`) |
| 3 `fit_tiles_prior(n_full=8, mesh=cavity)` | OK, 31.7 s; 7 tiles, 1 degraded-completed; deform 7/7; surface 7/7; 3 seeds unassigned, 0 clutter | site 2 on the 1 completed tile — no failure; site 1 on 7 tiles — no failure |
| 4 `build_hrctv(cavity, with_mesh=True)` | OK, 0.3 s; 66.8 cc; display shell present (43,695 faces) | site 3 — no failure |

Stage timings (pass 1): seed detection 0.55 s, metal inpainting 1.75 s,
head segmentation 7.92 s, cavity segmentation 3.89 s, surface meshes 5.93 s,
tile fitting 28.3 s. (31 seeds / 6 tiles is the known pre-Phase-1 state:
the vault filter drops one contoured seed and count-free saturates at 6;
those belong to the other Phase 1 / F4 tasks and are unchanged here.)

### Results — PH8 (157 slices, 0.59 × 0.59 × 1.0 mm), commit `bcb7a22`

`GTCORE_STRICT=1 python scripts/strict_pipeline_check.py "$GT_DATA_ROOT/<PH8>" PH8 8`
— nothing raised; script exit 0. The pipeline warns "vault filter skipped:
cranial interior 0 mL" (no skull) and segments no cavity, so `meshes` holds
only `skull` (32 speck faces) and `body` (72,396 faces); the planner
therefore conforms to `body` ("phantom shell"), and so do passes 3–4.

| Pass | Result | Sites exercised |
|---|---|---|
| 1 `reconstruct(auto)` | OK, 34.6 s; 32 seeds; 8 tiles (n_selected 8); surface fits 0/8 (no cavity mesh is handed to the fit), deform 8/8 | none (site 1 needs a mesh) |
| 2 `reconstruct(n_full_tiles=8)` | OK, 27.0 s; 32 seeds; 8 tiles (`TileFitResult`, chord score) | none |
| 3 `fit_tiles_prior(n_full=8, mesh=body)` | OK, 68.5 s; 8 tiles, 1 degraded-completed (the crumpled tile); deform 8/8; surface 8/8; 0 unassigned, 0 clutter | site 2 on the 1 completed tile — no failure; site 1 on 8 tiles against the 72k-face shell — no failure |
| 4 `build_hrctv(body, with_mesh=True)` | OK, 1.3 s; 262.3 cc (the whole printed shell's rind — a smoke test, not a target); display shell present (142,298 faces) | site 3 — no failure |

Stage timings (pass 1): seed detection 0.38 s, metal inpainting 0.23 s,
head segmentation 27.86 s, cavity segmentation 2.54 s, surface meshes
0.61 s, tile fitting 2.42 s.

### Full suite under `GTCORE_STRICT=1`, commit `bcb7a22`

`python -m pytest -q` (conftest sets the flag):
`837 passed, 14 skipped, 15122 warnings in 604.98s` — exit 0. That is the
829-test baseline at `aff83c0` plus the 22 new tests (851 collected). No
test drives any of the three sites into its fallback, so the strict suite
confirms the handlers were hiding nothing in the test paths either. The
warnings are the pre-existing numpy-2.5 `ar.shape =` deprecation from
scikit-image's marching cubes and the planner-optimize / tiles-suggest
runtime warnings; none is new.

### Conclusion

Under `GTCORE_STRICT=1` neither dataset raises at any of the three sites,
in the pipeline (acceptance 3) or in the planner-path calls that reach the
two sites the pipeline alone does not (`fit_tiles_prior`, `build_hrctv`).
On real data the handlers were not hiding failures at `aff83c0`; they are
now observable (WARNING + traceback) if they ever do, and fatal under the
flag. No handler intent was changed; nothing was loosened.

Data layout note: between the first and the final JD-1mm run (2026-11-03
00:05) the clinical folders were moved one level down into a `JohnDoe/`
folder under `GT_DATA_ROOT` (same folder names); the phantom folders are
unchanged. Commands above take the dataset path as an argument, so only the
path changed; the pipeline results did not.

## Phase 1 — vault filter in physical mm (2026-11-03, branch `p2/vault-filter`)

Base: `paper2/integration` at `aff83c0`. Commits on the branch:
`fbbdef2` (the fix + `tests/test_vault_filter_mm.py`), `241863c`
(`scripts/validation_clinical.py` printing only), `fad8701` (EDT on a box
around each seed). Hardware: Ryzen 5 7600X, 31 GB, Windows 11; other agents
were running concurrently, so every wall-clock figure below is an upper
bound.

### What changed (`gtcore/pipeline.py`)

- `VAULT_MARGIN_MM = 4.0`, a named module constant with the reason in its
  comment: the segmented cranial interior is bounded by the skull, and under
  the craniotomy the skull is missing, so the tiles sit against the dura
  under the bone defect, i.e. just outside the mask (contoured seeds on the
  clinical case lie up to 2.0 mm outside it, see below).
- One helper for both filter sites (the main filter and the coarse-scan
  threshold search): `in_cranial_vault(interior, vol, centers_ras)` =
  `seed_vault_distance_mm(...) <= VAULT_MARGIN_MM`. The distance is the
  Euclidean distance transform of `~cranial_interior` with
  `sampling = vol.spacing[::-1]` (array order `[k, j, i]`; `vol.spacing` is
  `(si, sj, sk)`), sampled **trilinearly** at the seed's continuous voxel
  position. A nearest-voxel lookup would misread half-voxel offsets (4.5 mm
  on a 2 mm axis rounds to the 4 mm voxel and would be kept). The transform
  runs on a box around each seed padded by the margin plus two voxel
  diagonals; a cropped transform can only over-estimate, and only beyond the
  padding, so every value up to the margin equals the full-grid value
  (asserted in the tests against the full-grid transform). Cost at the
  clinical grid size (204 x 512 x 512): 0.03 s for 32 clustered seeds,
  0.007 s / 0.12 s for 40 / 500 candidates scattered over the whole grid;
  the full-grid transform would be 6.7 s and ~1 GB, the old
  `binary_dilation(iterations=2)` measured 0.63 s under the same load.
- Replaced rule: `binary_dilation(cranial_interior, iterations=2)` = two
  voxels = 1.0 mm in-plane / 2.0 mm through-slice on the 0.52 x 0.52 x 1 mm
  clinical grid (test `test_two_voxel_rule_was_anisotropic...` shows the old
  rule dropping a seed 2 mm outside in-plane while keeping one 4 mm outside
  through-slice on a 0.5 x 0.5 x 2 mm grid).
- Fail-open behaviour unchanged (interior < 300 mL -> filter skipped with the
  same warning); `vol.meta["vault_filter"]` keeps `applied`, `interior_ml`,
  `n_dropped` and gains `margin_mm`.

### Unit tests

`GTCORE_STRICT=1 python -m pytest -q tests/test_vault_filter_mm.py` at
`fad8701`: **14 passed** (13 s). On a 0.5 x 0.5 x 2.0 mm grid a seed 3.5 mm
outside the interior is kept and one 4.5 mm outside is dropped on each of
the six axis directions (through-slice these are 1.75 and 2.25 voxels, i.e.
sub-voxel); the cropped transform equals the full-grid transform for every
distance up to the margin and gives identical keep decisions (60 random
tight clusters); `reconstruct` on the synthetic head phantom applies the
filter (interior >= 300 mL, 0 dropped, 12/12 seeds, `margin_mm` recorded)
and still fails open with empty head masks.

Full suite, `GTCORE_STRICT=1 python -m pytest -q` at `fad8701`:
**829 passed, 14 skipped** in 576.81 s (843 collected = the 829 of `aff83c0`
plus these 14; the skips pre-exist).

### Clinical case JD-1mm (the series the RTSTRUCT references)

```
python scripts/validation_clinical.py "$GT_DATA_ROOT/<JD-1mm>" "$GT_DATA_ROOT/<JD RT RTSTRUCT dir>" "$GT_DATA_ROOT/<JD RT RTDOSE dir>"
```

"Before" = the `aff83c0` pipeline. The stock script at `aff83c0` prints only
`seeds: TPS 30, detected 31; detected->nearest TPS seed median 0.16 mm, p95
4.39, max 8.74; TPS seeds with no detection within 2 mm: 1`; the matching
detail below comes from the `241863c` printing additions run against the
unmodified `aff83c0` package (read-only, same inputs). "After" = `fad8701`
(identical output to `241863c`, as the per-seed crop changes no decision).

| | before (`aff83c0`) | after (`fad8701`) |
|---|---|---|
| detections after the vault filter | 31 | **32** |
| contoured `Seeds` matched one-to-one (Hungarian, 2 mm gate) | 29/30; distance median 0.13, p95 0.75, max 0.89 mm; contoured seed 4 unmatched (nearest detection 7.49 mm) | **30/30; median 0.14, p95 0.73, max 0.89 mm** |
| plan sources = RTDOSE local maxima > 200 Gy, merged at 3 mm | 32 | 32 |
| detections matched one-to-one to a plan source (3 mm gate) | 31/32; median 0.90, max 1.45 mm; 1 source without a detection | **32/32; median 0.92, max 1.45 mm; 0 without** |
| detections without a plan source (false detections) | 0 | **0** (no new extracranial detections) |
| detections outside gtcore's cranial interior | 1 of 31 (max 0.2 mm) | 2 of 32 (max 1.9 mm = the recovered seed) |
| contoured seeds outside the cranial interior | 2 of 30 (max 2.0 mm) | same (mask unchanged) |
| `vault_filter` meta | applied, interior 1339.7 mL, 22 dropped | applied, 1339.7 mL, 21 dropped, margin_mm 4.0 |
| the two uncontoured sources | detections 10 and 17: 0.90 / 0.83 mm from an RTDOSE maximum, 7.9 / 8.7 mm from any contour, inside the interior | same |

The 21 blobs the filter still drops are shape-passing supra-threshold blobs
far from the vault (dental / jaw); only the one seed under the flap changed
side. Downstream numbers that move because the recovered seed enters the
seed hull (reported, not re-tuned): gtcore cavity 40.8 cc / Dice 0.668 vs
`Cav_Post` -> 40.7 cc / 0.671; gtcore HR-CTV 49.3 cc both (Dice vs `CTV_GT`
0.284 -> 0.285); RTDOSE on gtcore HR-CTV D90 2746 -> 2751 cGy; gtcore TG-43
at 3.5 U on `CTV_GT` from gtcore's own seeds D90 6051 / V100 91 % ->
**6168 cGy / 92 %** (RTDOSE 6365 / 94 %; TPS's 30 contoured seeds 5790 /
87 %). Unchanged: `CTV_GT` 19.0 cc, `Cav_Post` 35.1 cc, rind(`Cav_Post`)
33.9 cc, every RTDOSE row on TPS structures, the TG-43/RTDOSE ratio 0.931
(30 contoured seeds).

### Negative control PH0 and phantom PH8

Driver (no script change needed): `load_volume(path)`, then
`reconstruct(vol, verbose=False, n_full_tiles=None)` for PH0 and
`n_full_tiles="auto"` for PH8; read `len(res.seeds_raw)`, `len(res.seeds)`,
`vol.meta["vault_filter"]`, `res.implant`, `res.tiles.tiles`.
`scripts/validation_realdata_proxies.py --scans negatives,printed8` reports
the same quantities.

| | PH0 `CT 3D printed` (248 slices, 0.68 x 0.68 x 1.0 mm) | PH8 printed 8-tile phantom (157 slices, 0.59 x 0.59 x 1.0 mm) |
|---|---|---|
| before `aff83c0` | 0 raw blobs, 0 seed candidates; vault_filter applied False, interior 0.0 mL; implant present False ("only 0 candidates") | 32 raw / 32 seeds; vault filter skipped (0 mL, fails open); implant confirmed (7 gate-passing quads); auto fit 8 full tiles |
| after `fad8701` | 0 raw blobs, 0 seed candidates; `{'applied': False, 'interior_ml': 0.0, 'n_dropped': 0, 'margin_mm': 4.0}`; implant present False | 32 raw / 32 seeds; `{'applied': False, 'interior_ml': 0.0, 'n_dropped': 0, 'margin_mm': 4.0}`; implant confirmed; **8 supported full tiles, 0 tentative, 32 of 32 seeds assigned** |
| pipeline wall (upper bound, concurrent load; three runs each) | 32-35 s, of which head segmentation 24-27 s | 29-31 s, of which head segmentation 21-23 s |

### Notes for the coordinator

- Deviations from the brief's sketch, all deliberate: `sampling` is
  `vol.spacing[::-1]` (the brief wrote `vol.spacing`; array order is
  `[k, j, i]`), the field is sampled trilinearly rather than at the nearest
  voxel (acceptance test 2 cannot be met otherwise on a 2 mm axis), and the
  transform is cropped per seed for runtime.
- `scripts/validation_realdata_proxies.py` still labels the JD-1mm series a
  "pre-implant head CT" negative control in its docstring and `negatives`
  arm; it is the implant CT (32 sources). Not touched here (Phase 5 wording
  scrub).

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
- After merging `paper2/integration` (`d722022`, strict-exceptions; the
  conftest now sets `GTCORE_STRICT=1`) into this branch as `09e7fc7`:
  `python -m pytest -q -p no:cacheprovider` 850 passed, 14 skipped in
  517.7 s (864 collected = 842 + the 22 strict-mode tests; 0 failed).
  The only merge conflict was this notes file (both tasks created it);
  resolved by keeping both sections under the strict-exceptions header.

Hardware for the timings: AMD Ryzen 5 7600X, 31 GB RAM, Windows 11, with
other agents running concurrently (timings are upper bounds).

## 2026-11-03 — Phase 1 — cavity reach in physical mm

Task `cavity-reach`, branch `p2/cavity-reach` (from `paper2/integration` at
`aff83c0`).  Code commit **`0efe5eb`** ("cavity: hull pad and reach in
physical mm on anisotropic grids").  All "before" numbers are at `aff83c0`,
all "after" numbers at `0efe5eb`; `HULL_REACH_MM = 14.0` and
`SHEET_TOL_MM = 3.0` were **not** changed.

### The defect

`gtcore/segment/cavity.py::_cavity_from_seed_sheet` grew the cavity with
`ndimage.binary_dilation(start, iterations=round(reach_mm / min(spacing)),
mask=grow_mask)` and padded the seed hull with
`binary_dilation(hull, iterations=round(tol_mm / min(spacing)))`.  Both are
voxel steps of a 6-connected element, i.e. an L1 ball in voxel units.  On
the JD-1mm grid (0.52 × 0.52 × 1.0 mm) the nominal 14 mm reach was 27 voxel
steps = **27 mm through-slice, 14 mm along an in-plane axis, 9.6 mm on an
in-plane diagonal** (and 8.1 mm on the body diagonal); the 3 mm pad was 6
steps = 6 mm through-slice.  Even on the isotropic 0.7 mm synthetic phantom
the "14 mm" region was an octahedron of 14 mm half-axis, 1/π of the volume
of the 14 mm sphere.

### The fix (`0efe5eb`)

Two helpers in `gtcore/segment/_morph.py`, both taking physical mm on every
axis; `cavity.py` calls them in place of the two dilations:

- `dilate_mm(mask, spacing, radius_mm)` — exact Euclidean ball,
  `distance_transform_edt(~mask, sampling=spacing) <= radius_mm`, full
  resolution (the pad).
- `geodesic_grow_mm(start, mask, spacing, reach_mm)` — geodesic distance
  through the growth mask: the Eikonal equation |∇T| = 1 solved on the
  voxel grid with the per-axis spacing (fast marching, Sethian 1996), Godunov
  upwind update, **second order** along each axis where two upwind
  neighbours are known (first order otherwise and as the fallback when the
  second-order root is not causal), iterated to a fixed point over the
  active voxels only (fast iterative method, Jeong & Whitaker 2008,
  vectorised in numpy).  The 26-neighbour shell of the start that a
  three-step face path can reach is seeded with its exact Euclidean
  distance (removes the first-order point-source error).  Only face
  neighbours exchange values, so the growth keeps the 6-connected topology
  of the dilation it replaces (never crosses a one-voxel wall or a diagonal
  crack).  Propagation stops at `reach_mm`, so the work scales with the
  grown region.  Rejected alternatives: 26-neighbour Dijkstra
  (`skimage.graph.MCP_Geometric`) is a chamfer metric, 12 % long on oblique
  directions and 26-connected; first-order fast marching alone is 11–14 %
  long on oblique directions; resampling to 1 mm isotropic and dilating
  still gives an L1 octahedron.

Accuracy of the discrete distance in free space (point source, 14 mm
reach), `tests/test_cavity_reach_mm.py`:

| grid (mm) | extent +k / +j / +i | in-plane diagonal | r from volume | worst \|T − Euclid\| | mean |
|---|---|---|---|---|---|
| 0.5 × 0.5 × 2.0 | 14.00 / 14.00 / 14.00 | 13.44 (next diagonal voxel 14.15) | 13.81 | 0.44 mm | 0.18 mm |
| 0.52 × 0.52 × 1.0 (JD) | 14.00 / 13.52 / 13.52 (27th voxel at 14.04) | 13.97 | 13.99 | 0.16 mm | 0.01 mm |
| 0.7 iso (synthetic) | 14.00 / 14.00 / 14.00 | 13.86 | 14.03 | 0.19 mm | −0.04 mm |

Same point, old rule, JD grid: 27.0 / 14.0 / 14.0 mm, in-plane diagonal
9.6 mm (`test_old_voxel_step_rule_was_anisotropic`).

Tests: `tests/test_cavity_reach_mm.py` (12 tests: 14 ± 1 mm sphere on both
anisotropic grids — axes, in-plane diagonal, volume-equivalent radius;
free-space distance within half the largest voxel; pad is a Euclidean ball
and equals `euclid <= 3 mm` voxel for voxel; geodesic detour around a wall
within 5 %; one-voxel wall and diagonal crack never crossed; degenerate
inputs; synthetic 0.7 mm phantom Dice ≥ 0.83 and reach monotone 10 < 14 <
18 mm).  Existing `tests/test_phantom.py`, `tests/test_integration.py`,
`tests/test_segment_units.py` unchanged and green.  Full suite at the
docs commit (`python -m pytest -q`, no `GTCORE_STRICT` flag exists in the
tree yet): **827 passed, 14 skipped in 8 min 54 s** (841 collected = the
829 of `aff83c0` + these 12).

### Re-measurement at `HULL_REACH_MM = 14` (not re-tuned)

**(a) Phantom.**  The "phantom Dice" column of the reach sweep in
`docs/clinical-validation-notes.md` §4 was the *synthetic* phantom
(`gtcore.phantom.make_head_phantom()` at its defaults: 0.7 mm isotropic,
3 tiles, rng 0, truth cavity 26.3 cc), through the demo / integration path
(`detect_seed_candidates → inpaint_metal → segment_head → segment_cavity`);
`pipeline.reconstruct` with the reach patched in gives identical Dice and
volume.  The printed phantom PH8 has **no cavity mask** before or after
(0.0 cc, 0 voxels at both commits): the printed shell has no brain-window
tissue, the cranial interior is 0 mL and the sheet rule returns an empty
mask — as already recorded in `docs/optimize-notes.md` V6 ("no cavity
segmented").  There is therefore no PH8 Dice to report, and none existed
before.

```
python scripts/cavity_reach_sweep.py --reaches 12,14,16,20          # synthetic
python scripts/cavity_reach_sweep.py --ct "$GT_DATA_ROOT/<PH8>"     # printed phantom
```
(`scripts/cavity_reach_sweep.py` is added in the commit that adds this
section; it packages the scratch measurement used at `aff83c0` / `0efe5eb`
and reproduces the rows below bit for bit — the Dice/volume path is the
same code.)

| reach | Dice before (`aff83c0`) | volume before | Dice after (`0efe5eb`) | volume after |
|---|---|---|---|---|
| 12 mm | 0.812 | 21.0 cc | 0.873 | 25.0 cc |
| **14 mm** (default) | **0.839** | **22.8 cc** | **0.896** | **26.9 cc** |
| 16 mm | 0.888 | 24.9 cc | 0.920 | 28.8 cc |
| 20 mm | 0.911 | 29.3 cc | 0.903 | 31.5 cc |

(The §4 table in `docs/clinical-validation-notes.md` — 0.808 / 0.851 /
0.886 / 0.924 — was measured at `2046a07`; the seed-localization changes
merged since moved the `aff83c0` baseline to the "before" column here.
That table is now historical: voxel-step growth.)

**(b) Clinical JD-1mm vs `Cav_Post`.**

```
python scripts/validation_clinical.py "$GT_DATA_ROOT/JohnDoe/<JD-1mm>" \
    "$GT_DATA_ROOT/JohnDoe/<JD RT RTSTRUCT>" "$GT_DATA_ROOT/JohnDoe/<JD RT RTDOSE>"
```
(the "before" run used the pre-move paths `$GT_DATA_ROOT/<...>`; same
files.)  Cavity-stage runtime from
`python scripts/cavity_reach_sweep.py --ct "$GT_DATA_ROOT/JohnDoe/<JD-1mm>" --rtstruct "$GT_DATA_ROOT/JohnDoe/<JD RT RTSTRUCT>"`
(`reconstruct(verbose=True)` stage timings; Dice identical to the
validation script's).

| | before (`aff83c0`) | after (`0efe5eb`) |
|---|---|---|
| gtcore cavity volume | 40.8 cc (151 071 vox) | 41.4 cc (153 444 vox) |
| Dice vs `Cav_Post` (35.1 cc) | 0.668 | 0.663 |
| covers `Cav_Post` / of ours inside it | 72 % / 62 % | 72 % / 61 % |
| gtcore HR-CTV (5 mm rind of gtcore cavity) | 49.3 cc | 50.9 cc |
| HR-CTV Dice vs `CTV_GT` / `CTV_Post` | 0.284 / 0.324 | 0.284 / 0.324 |
| RTDOSE D90 / V100 on gtcore HR-CTV | 2746 cGy / 38 % | 2725 cGy / 38 % |
| gtcore TG-43 (our 31 seeds, 3.5 U) D90 on gtcore HR-CTV | 2659 cGy | 2601 cGy |
| seeds (unchanged by this task) | 31 detected, 29/30 matched | same |

Everything else in the validation output (TPS structures, `CTV_GT` rows,
rind of `Cav_Post`, implied S_K 3.76 U) is byte-identical before and after.

**(c) Cavity-stage runtime** (`reconstruct` timing of "cavity segmentation",
which includes the hull / sheet-normal work, the growth, closing and fill;
upper bounds, see hardware note):

| dataset | before (`aff83c0`) | after (`0efe5eb`) |
|---|---|---|
| synthetic 0.7 mm phantom (286³) | 1.4–1.5 s | 1.7–2.0 s |
| PH8 (157 × 512 × 512) | 2.16 s | 2.25 s |
| JD-1mm (204 × 512 × 512) | 3.67 s | 3.91 s |

A repeat of the JD-1mm "after" run with
`scripts/cavity_reach_sweep.py --ct ...` (while the test suite was running)
gave 3.56 s, so the before/after difference on the clinical case is within
run-to-run noise.  Isolated growth step on a JD-sized 90 × 180 × 180 box
with a 60 %-random mask (prototype benchmark): old `binary_dilation × 27`
0.05 s → geodesic 0.7 s.  Full pipeline on JD-1mm stays ≈ 20 s of stage
time.

### Reading (reported, not acted on)

- **Synthetic phantom: Dice up at every reach ≤ 16 mm, 0.839 → 0.896 at
  the default.**  Expected direction: at equal nominal reach the Euclidean
  ball has π times the volume of the L1 octahedron, so the oblique reach
  grew from 8–10 mm to 14 mm and the far wall of the one-sided implant is
  now reached; the 14 mm volume now matches the truth (26.9 vs 26.3 cc).
  The sweep is no longer monotone: it peaks at 16 mm (0.920) and falls at
  20 mm (overshoot, 31.5 cc).  **Not re-tuned** — the rule is synthetic +
  phantom first and never on the clinical case; if the coordinator wants a
  re-tune of `HULL_REACH_MM` it is a separate, declared decision for Table 2.
- **Clinical: essentially unchanged** (+0.6 cc, Dice −0.005, coverage of
  `Cav_Post` 72 % both ways).  Cutting the through-slice reach from 27 to
  14 mm removed almost nothing and the extra in-plane oblique reach added
  almost nothing, so at 14 mm the clinical cavity is bounded by the
  low-density mask and the seed-sheet clip, not by the reach.  The 28 % of
  `Cav_Post` never covered is still the clot / debris above 26 HU
  (`docs/clinical-validation-notes.md` §4), not a reach effect.  Mildly
  surprising only in that the 27 mm through-slice growth was not what let
  the cavity into the oedema.
- **F6 numbers to regenerate** from this commit onward: gtcore HR-CTV 50.9
  cc, RTDOSE D90 2725 cGy on it (the 2863 cGy / 49.9 cc in
  `clinical-validation-notes.md` was at `2046a07`, 2746 / 49.3 at
  `aff83c0`).  The `IMPLANT_REACH` row of F6 does not exist yet (Phase 2
  `targets.py`).
- `docs/clinical-validation-notes.md` §4 (reach sweep, HR-CTV 2863 cGy) and
  the `cavity.py` module docstring's "41 cc / Dice 0.65 / 0.85" are now
  historical; left in place (Phase 5 scrub / README drift), with a pointer
  from the `HULL_REACH_MM` comment to this section.

## 2026-11-02 — Phase 1 — packaging, lock file, test tolerance

Branch `p2/packaging` (worktree `p2-packaging`), based on `paper2/integration`
= `aff83c0`. Requirements rev 3 §6 Phase 1 fifth checkbox, §4 item 12 (the
`rtree` / tolerance parts), brief "Rules" (3.11/3.12 venv, lock file, declare
`rtree` and `pymedphys`). Commits:

| Commit | What |
|---|---|
| `3d10eb9` | `pyproject.toml`: `rtree>=1.0` runtime dependency; `requires-python = ">=3.11,<3.13"`; extras `dev = ["pytest>=8"]`, `clinical = ["pymedphys>=0.41", "numba>=0.60"]`, `viz = ["pyvista"]` (unchanged) |
| `a5d3d13` | `tests/test_tiles_fuse.py`: `test_no_covariance_is_bit_identical_to_the_historical_fit` → `test_no_covariance_is_identical_to_the_historical_fit_to_1e6`, `np.testing.assert_allclose(rtol=1e-6, atol=1e-8)` on R, t, (kappa1, kappa2, psi), rms; assignment / wrms / None checks kept; `seed_cov=None` vs default kept exactly equal (same code path); module docstring says "identical ... to 1e-6" |
| `9ebb13d` | `requirements-lock.txt` (new) and `README.md` (Install section + Python-version bullet) |
| `3921ee0`, `7706697` | this notes section (then appended to the shared notes file) |
| `9179bce`, `dba5ff0` | merge of `paper2/integration` (strict-exceptions + vault-filter) into this branch so it merges clean; notes file = integration's two sections + this one |

### 1. pymedphys / numba (what was installed, exact versions)

Shared project venv (Python 3.12.10, numpy 2.5.2, scipy 1.18.1 — both unchanged):

```
python -m pip install pymedphys      # pymedphys 0.41.0 (+ setuptools 84.2.1, tomlkit 0.15.2; typing_extensions already present)
python -m pip install numba          # numba 0.68.1 + llvmlite 0.50.0
python -c "import pymedphys; from pymedphys import gamma; print(pymedphys.__version__)"   # 0.41.0
```

- `pymedphys 0.41.0` declares `requires_python >=3.10,<3.13` (hence the 3.11/3.12
  bound in `pyproject.toml`). Its *base* install pins nothing on numpy/scipy
  (`numpy>=1.26` and `scipy` appear only under its `user`/`docs` extras), so it
  does **not** downgrade the venv. `pip install --dry-run --report` confirmed
  only `pymedphys`, `setuptools`, `tomlkit` would be added.
- `import pymedphys; from pymedphys import gamma` works on the bare install, but
  **calling** `gamma(...)` with the default `interp_algo="pymedphys"` needs
  `numba` (`pymedphys/_interp/interp.py` is `nb.njit`) and raises
  `ModuleNotFoundError: PyMedPhys was unable to import "numba.njit"`.
  Two working paths, both verified on a 20^3 synthetic grid (3 %/2 mm, pass rate
  1.0 over 8000 voxels):
  - `gamma(..., interp_algo="scipy")` — no numba needed (scipy
    `RegularGridInterpolator`);
  - the default path with `numba 0.68.1` installed (numba pins
    `numpy<2.6,>=1.22` and `llvmlite<0.51,>=0.50.0dev0`; compatible with numpy
    2.5.x, nothing downgraded).
  The `clinical` extra therefore lists both `pymedphys>=0.41` and `numba>=0.60`
  so Phase 3's `gtcore/dose/gamma.py` works with pymedphys' default settings.
  **Coordinator decision:** keep numba in the extra (default pymedphys path, but
  numba's `numpy<2.6` cap will bite on the next numpy minor), or drop it and have
  the Phase 3 wrapper pass `interp_algo="scipy"`. Either way the lock file pins
  a working set.
- `pymedphys[user]` was *not* used: it pulls streamlit, pymssql,
  anthropic==0.29, scikit-learn, shapely and more.

### 2. Lock file (`requirements-lock.txt`)

Produced from a *fresh* venv in `<scratchpad>/lockenv` (not the shared venv),
Python 3.12.10 (CPython, Windows AMD64), pip 26.3:

```
python -m venv <scratchpad>/lockenv
<lockenv>/python -m pip install -e ".[dev,clinical]"        # 32 packages; 754 of 829 tests collect (test_planner*.py importorskip pyvista)
<lockenv>/python -m pip install -e ".[dev,clinical,viz]"    # + pyvista 0.49.2, vtk 9.7.1, pooch, scooby, ... -> all 829 collect
<lockenv>/python -m pip freeze --exclude-editable > requirements-lock.txt   # header with Python / pip version prepended
```

The lock therefore covers runtime + `dev` + `clinical` + `viz` (a superset of
the task's `[dev,clinical]`, chosen so the lock reproduces the *complete* test
suite — without pyvista 75 planner tests are silently skipped). Key pins:
numpy 2.5.4, scipy 1.18.1, scikit-image 0.26.0, SimpleITK 2.5.7, pydicom 3.0.2,
trimesh 5.2.0, rtree 1.4.1, matplotlib 3.11.2, pymedphys 0.41.0, numba 0.68.1,
llvmlite 0.50.0, pyvista 0.49.2, vtk 9.7.1, pytest 9.2.0. The editable `gtcore`
line is excluded. (The shared venv differs slightly: numpy 2.5.2, trimesh 5.1.0,
SimpleITK 2.5.6, pyvista 0.48.4, vtk 9.6.2, pytest 9.1.1 — both sets pass.)

### 3. Test runs

Hardware: AMD Ryzen 5 7600X, 6 cores / 12 threads, 31 GB RAM, Windows 11 Home
10.0.26200. Other agents ran test suites and installs concurrently, so both
wall-clock figures are **upper bounds**. `GTCORE_STRICT=1` was set for both
runs but is a no-op at `aff83c0`/`9ebb13d` (the flag comes from the
strict-exceptions branch); the last row, after merging `paper2/integration`,
runs with the flag functional (`tests/conftest.py` sets `GTCORE_STRICT=1`).

| Run | Commit | Interpreter | Command | Result | Runtime |
|---|---|---|---|---|---|
| Baseline, shared venv, worktree | `aff83c0` (test modules collected before this branch's edits) | shared venv, Python 3.12.10, numpy 2.5.2 | `GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider` | `815 passed, 14 skipped, 15122 warnings in 558.68s (0:09:18)` | wall 9 min 21 s |
| Edited file only, shared venv | working tree of `a5d3d13` | shared venv | `GTCORE_STRICT=1 python -m pytest -q tests/test_tiles_fuse.py` | `13 passed in 16.84s` | — |
| **Clean clone, fresh venv from the lock file** | `9ebb13d` | `<scratchpad>/clonenv`, Python 3.12.10, numpy 2.5.4 | see below | **`815 passed, 14 skipped, 161 warnings in 646.89s (0:10:46)`** | wall 10 min 49 s (pytest only; venv + lock install took 64 s with a warm pip cache) |
| Clean clone, same lock-file venv, after merging `paper2/integration` (strict-exceptions + vault-filter, 36 new tests) | `dba5ff0` | `<scratchpad>/clonenv` (lock and pyproject unchanged, no reinstall) | same recipe with `git checkout dba5ff0` | `851 passed, 14 skipped, 164 warnings in 508.82s (0:08:28)` | wall 8 min 30 s |

Clean-clone recipe (the brief's "tests green on a clean clone" check; it also
exercises the README Install paragraph):

```
git clone --no-hardlinks --no-checkout <repo> <scratchpad>/clone
cd <scratchpad>/clone && git checkout p2/packaging
python -m venv <scratchpad>/clonenv
<clonenv>/python -m pip install -r requirements-lock.txt
<clonenv>/python -m pip install --no-deps -e .
GTCORE_STRICT=1 <clonenv>/python -m pytest -q -p no:cacheprovider
```

The 14 skips are identical in both runs and none needs a data folder: 13 are
`tests/test_plan_interface.py` placeholders ("<name> is implemented (see its
own test module)") and 1 is the `tests/test_planner_optimize.py` end-to-end
optimizer run gated on `GT_E2E=1` (~1–2 min). No test reads `GT_DATA_ROOT` or a
real dataset; the suite is synthetic-phantom only.

### 4. `rtree` — mechanism check

`rtree` is imported nowhere in gtcore; it is `trimesh`'s spatial index, listed
only under trimesh's own `easy` extra, and `trimesh.util` imports it lazily for
`trimesh.proximity.closest_point` (used by `gtcore/interact.py:148` and the
HR-CTV / DVH paths). Verified in the lock venv with `pip uninstall rtree`:
`tests/test_interact.py::test_conform_reproduces_phantom_truth` fails with
`ModuleNotFoundError: No module named 'rtree'` (trimesh/util.py:1896); rtree
1.4.1 was reinstalled afterwards. The "169 tests error" count from the
requirements doc was not re-measured (no full-suite run without rtree).

### 5. Shared files touched (for the merge)

- `pyproject.toml` — dependency list, `requires-python`, extras; comments
  explain each line.
- `README.md` — minimal: one new `## Install` section (10 lines) before
  `## Quick start`, and the "Python ≥3.9-compatible core" bullet under
  Conventions now reads "Python 3.11 / 3.12 (`requires-python` in
  `pyproject.toml`)". The "805 tests" strings and other README drift are left
  for Phase 5.
- `tests/test_tiles_fuse.py` — one test + the module docstring only.

## 2026-11-03 — Phase 1 close-out — planner drag test made deterministic (coordinator)

`tests/test_planner_interaction.py::test_ctrl_left_drag_relocates_and_stays_conformed`
failed intermittently on `origin/main` aff83c0 (1 of 4 full runs today;
"seed 1.02 mm from wall after drag") and once on the Phase 1 tree, passing
alone every time. Cause: `_PlannerApp._drag_move` paces re-conforms by
wall-clock time (`drag_min_interval_s`), so under load only some of the nine
synthetic mouse moves applied, and the test's end point (80 px / 25 px from
the anchor) is off the wall, so the tile landed at whichever intermediate
step last passed the pacing gate; with that pose one seed sat 1.9–2.0 mm
from the lumpy marching-cubes wall, at the test's 2.0 mm bound. Repro script
(four repeats per setting): default pacing moved 6.2 mm once and 23.7 mm
three times; pacing off always 23.7 mm; release-only 0.0 mm (end point off
the wall). Fix (test only, no planner change): `drag_min_interval_s = 0`,
end point 60 px / 15 px asserted on the wall. Three consecutive module runs:
32 passed each. Full-suite reference at the Phase 1 merge d063c23:
875 passed, 14 skipped, 1 failed (this test); the four-merge tree 2630550:
876 passed, 14 skipped.

## 2026-11-03 — Phase 2 — targets (HRCTV_PHYS, RIND5, IMPLANT_REACH, S5) (task `targets`)

Branch `p2/targets`, code commit `56651ba` (then `32234a4` = merge of
`paper2/integration` d1de921, test-only). Requirements rev 3 §2 "Targets",
§3 F6, §6 Phase 2 second checkbox; Phase 2 contract section B. Hardware as
above; other agents ran concurrently, so timings are upper bounds.

### Definitions (Methods-quotable)

**HRCTV_PHYS.** The physician's planning high-risk clinical target volume as
contoured in the treatment-planning system (RTSTRUCT ROI `CTV_GT`),
rasterised onto the CT grid and used unchanged
(`gtcore.dose.targets.hrctv_phys`). It is the reference structure for every
dose comparison with the TPS (F5); `CTV_GT` is a hand-trimmed partial rind
(19.0 cc on the 1 mm CT grid), covering 53 % of the full 5 mm rind of
`Cav_Post` on this case.

**RIND5(cavity, clip).** The implant-independent HR-CTV: the set of voxels
whose centre lies within 5 mm (Euclidean distance in physical millimetres,
`scipy.ndimage.distance_transform_edt` with the grid spacing as `sampling`)
of the nearest cavity voxel centre, outside the cavity, intersected with a
clip mask (the TPS `Brain` contour when present, gtcore's cranial interior or
brain otherwise). The cavity may be the TPS `Cav_Post` contour or gtcore's
automatic segmentation; every output table states which, together with the
clip mask, the grid (spacing, shape) and the depth (`Target.provenance`).
When a target is requested on a grid other than the cavity's, both masks are
first resampled nearest-neighbour to that grid and the distance transform
runs there (`gtcore.dose.targets.rind5`). RIND5 is the planner's HR-CTV
(`build_hrctv`), the optimizer's volumetric target (`metrics.rind_mask`,
Phase 2 D), and the structure of F8 and Table 3.

**IMPLANT_REACH(r).** `RIND5` restricted to the voxels whose centre lies
within r mm of the nearest *seed centre* (exact Euclidean distance to the
detected seed positions; seeds, not tile footprints, because with 6 of 8
tiles fitted a footprint rule would exclude the wall under the unfitted
tiles). It reproduces the physician's partial-rind contouring convention for
F6 only, with r calibrated on this case against `CTV_GT` (Dice) and stated
as such. It is never a planning or optimizer target: a target defined by the
seed placement cannot score that placement
(`gtcore.dose.targets.implant_reach`).

**S5.** The cavity-wall mesh pushed 5 mm outward along its vertex normals,
each vertex weighted by its area on the offset shell
(`gtcore.plan.TargetSet.from_shell`, wrapped by `gtcore.dose.targets.s5`).
It is the optimizer's internal surrogate for the HR-CTV and is reported only
as "S5 coverage" (area fraction at or above the prescription), never as a
D90 of the HR-CTV.

**Distance convention (state in Methods).** The RIND5 distance is measured
from voxel centre to the nearest cavity voxel *centre*; the cavity's
boundary voxel centres lie inside the analytic surface, so the rind is
systematically thin by a fraction of a voxel. On a digital sphere (R = 12 mm,
5 mm rind) the volume deficit against the analytic shell is -4.8 % on a
0.5 x 0.5 x 2 mm grid, -4.4 % on the clinical 0.52 x 0.52 x 1 mm grid,
-5.2 % at 1 mm isotropic, -2.2 % at 0.5 mm isotropic; along each grid axis
the band is exactly the centres in (R, R + 5] (so a unit error of the old
voxel-step kind would be caught), and the Dice against the exactly
voxelised continuous rind is 0.969. The same convention applies to every
structure in a comparison, so Dice and D90 comparisons are unaffected; the
absolute rind volume is quoted with this caveat.

### What changed

- `gtcore/dose/targets.py` (new): `Target` dataclass (`name`, `mask`,
  `affine`, `points`, `weights`, `provenance`; properties `n`,
  `voxel_volume_mm3`, `volume_cc`, `voxel_centers_ras()`, `as_volume()`,
  `describe()`); builders `rind5`, `hrctv_phys`, `implant_reach`, `s5`;
  `dice(a, b)` / `coverage(a, b)` (b resampled nearest-neighbour onto a's
  grid when the grids differ; tables name the grid), `volume_cc`;
  `target_doses_exact` (exact engine at the sample points),
  `target_doses_from_grid` (trilinear), `target_stats` -> one private
  `_stats` seam that calls `metrics.dose_stats` when it exists (Phase 2 A)
  and the pre-Phase-2 `hrctv_stats` / `plan.report.weighted_stats`
  otherwise. Provenance keys: `cavity_source`, `clip_source`, `grid`
  (`spacing_mm` per (i, j, k), `shape` in array order (k, j, i),
  `origin_ras`), `depth_mm`, `r_mm` + `n_seeds` (IMPLANT_REACH), `roi_name`
  (HRCTV_PHYS), `builder`, `git_commit`, `name`; all JSON-serialisable.
- `gtcore/dose/hrctv.py`: `build_hrctv` keeps its signature and return type.
  With `inside_mask` (the cavity) the mask **is**
  `rind5(cavity, clip=keep_mask)` on the isotropic rind grid the mesh bounds
  define; the mesh-offset construction (`_mesh_offset_rind`) remains for the
  display shell's far-surface trim and as the fallback when no cavity mask
  exists, flagged by `provenance["cavity_source"] == "mesh"` and
  `HRCTV.source` "... (mesh-offset fallback: no cavity mask)". `HRCTV.target`
  (a `Target`) and `HRCTV.provenance` added; optional `cavity_source` /
  `keep_source` labels. An empty cavity on the rind grid raises instead of
  silently falling back. `hrctv_stats` untouched (byte-identical; owned by
  `dose-stats`).
- `gtcore/dose/metrics.py`: `rind_mask` is a wrapper returning
  `rind5(...).mask` (old signature; `exclude_mask` becomes the clip);
  `resample_mask_to` is chunked (4 M voxels per slab) so a full CT grid stays
  within a few hundred MB -- same per-voxel computation, bit-identical result.
  Stats functions and `DVH` untouched.
- `gtcore/dose/__init__.py` exports the public names; `gtcore/planner.py`
  passes `keep_source` ("gtcore:cranial_interior" / "gtcore:brain" /
  "gtcore:body") so the planner's HR-CTV is a labelled RIND5 (it already
  passed the pipeline's cavity mask). `gtcore/cli.py`, `gtcore/plan/report.py`,
  `scripts/validation_clinical.py`, `scripts/strict_pipeline_check.py`
  unchanged and still working (checked by the tests / the driver below).

### Unit tests (`tests/test_targets.py`, 11 tests)

`GTCORE_STRICT=1 python -m pytest -q tests/test_targets.py tests/test_hrctv.py tests/test_dose_metrics.py tests/test_strict_mode.py`
at `56651ba`: 49 passed. The digital-sphere test on 0.5 x 0.5 x 2 mm asserts
the axis-exact band, nothing inside the cavity, clip removal, output on
another grid (= resample first, then EDT, bit-identical), volume within 6 %
of the analytic shell with the one-sided bias above (**the task's 3 %
figure is not reachable with the contract's centre-to-centre distance
transform on that grid; see "Notes for the coordinator"**); IMPLANT_REACH
is a subset of RIND5, equals RIND5 for r -> inf, is empty for r -> 0, is
brute-force equal to the nearest-seed-centre rule and to the union over
single seeds; S5 == `TargetSet.from_shell`; Dice/coverage on known boxes
across grids; provenance JSON round trip with the contract keys;
`build_hrctv` with a cavity mask equals `rind5` on the rind grid
(`cavity_source "gtcore:cavity"`) and without one is the flagged `"mesh"`
fallback; `rind_mask` wrapper equals `rind5(...).mask`; doses/stats paths
incl. the weighted S5 path and the empty target.

### Real data -- JD-1mm (204 slices, 0.52 x 0.52 x 1.0 mm) with the RTSTRUCT

Driver: a scratch script (not committed) that runs `load_volume` +
`reconstruct(vol, verbose=False)` on `$GT_DATA_ROOT/<JD-1mm>`, rasterises
`Cav_Post`, `CTV_GT` and `Brain` from `$GT_DATA_ROOT/<JD RT RTSTRUCT>` with
the rasteriser of `scripts/validation_clinical.py` (copied verbatim; the
module belongs to `rt-io`), and calls the `targets` API; run with
`PYTHONPATH` = the worktree, `gtcore.__file__` printed and checked, at
`32234a4` (identical numbers at the uncommitted `d063c23` tree). Pipeline:
32 seeds (Phase 1 vault filter), gtcore cavity 41.3 cc, cranial interior
1340 mL, 21 s; `Cav_Post` 35.10 cc, `CTV_GT` 19.02 cc, `Brain` 1384 cc on
the CT grid. `rind5` on the full CT grid: 5.6 s.

| Target (CT grid) | Volume [cc] | Dice vs `CTV_GT` | covers `CTV_GT` | inside `CTV_GT` |
|---|---|---|---|---|
| `HRCTV_PHYS` = `CTV_GT` | 19.02 | 1 | 100 % | 100 % |
| `RIND5(Cav_Post, clip=Brain)` | 35.35 | **0.692** | 99 % | 53 % |
| `RIND5(Cav_Post, clip=gtcore cranial interior)` | 33.88 | 0.666 | 93 % | 52 % |
| `RIND5(Cav_Post, no clip)` | 45.00 | 0.588 | 99 % | 42 % |
| `RIND5(gtcore cavity, clip=gtcore cranial interior)` | 53.82 | **0.284** | 54 % | 19 % |
| `RIND5(gtcore cavity, clip=Brain)` | 60.27 | 0.291 | 61 % | 19 % |

Dice `RIND5(Cav_Post, Brain)` vs `RIND5(gtcore cavity, cranial interior)`:
**0.443**; `RIND5(Cav_Post, Brain)` vs `RIND5(Cav_Post, cranial interior)`:
0.963 (the TPS `Brain` clip keeps the rind under the craniotomy that the
gtcore cranial interior cuts -- hence 99 % vs 93 % coverage of `CTV_GT`).
gtcore cavity vs `Cav_Post`: Dice 0.665. The 33.88 cc / 0.666 row reproduces
the Phase 1 inline computation in `validation_clinical.py` ("rind(Cav_Post)
33.9 cc"), i.e. `rind5` is that definition. The gtcore rind is large because
the gtcore cavity (41.3 cc) leaks beyond `Cav_Post` (known Phase 1 state,
not touched here).

**F6 calibration -- `IMPLANT_REACH(r)` from the 32 gtcore seeds on
`RIND5(Cav_Post, clip=Brain)`** (nothing else tuned; the rev 3 sketch quoted
12 mm / Dice 0.80):

| r [mm] | Volume [cc] | Dice vs `CTV_GT` | covers `CTV_GT` | inside `CTV_GT` |
|---|---|---|---|---|
| 6 | 2.40 | 0.214 | 12 % | 95 % |
| 8 | 8.48 | 0.578 | 42 % | 94 % |
| **10** | 16.83 | **0.881** | 83 % | 94 % |
| 12 | 25.29 | 0.846 | 98.5 % | 74 % |
| 14 | 31.79 | 0.740 | 99 % | 59 % |
| (4 / 16 / 18 / 20, context) | 0.20 / 34.64 / 35.31 / 35.35 | 0.020 / 0.701 / 0.692 / 0.692 | | |

Dice peaks at r = 10 mm (0.881), not 12 mm; 12 mm gives 0.846 and covers
98.5 % of `CTV_GT` (10 mm covers 83 %). The curve is flat-topped between 10
and 12 mm; the choice is Jacob's (Table 2 states "calibrated on this case").
Context, `IMPLANT_REACH(r)` on `RIND5(gtcore cavity, cranial interior)`:
Dice vs `CTV_GT` 0.088 / 0.305 / 0.523 / 0.520 / 0.440 for r = 6 / 8 / 10 /
12 / 14 (volumes 3.4 / 8.7 / 14.5 / 20.8 / 28.0 cc).

**Planner path.** `build_hrctv(cavity mesh, inside_mask=gtcore cavity,
keep_mask=cranial interior, keep_source="gtcore:cranial_interior")`: 0.6 s;
52.7 cc on the 1 mm rind grid (70 x 73 x 76); provenance `builder rind5`,
`cavity_source gtcore:cavity`, `clip_source gtcore:cranial_interior`,
`depth_mm 5`; display shell 34,575 faces. Dice vs `RIND5(gtcore cavity,
cranial interior)` on the CT grid 0.936 (the 1 mm rind grid resampled onto
the 0.52 mm CT grid -- the residual is the two voxelisations); Dice vs
`CTV_GT` 0.288 (CT-grid value 0.284). Phase 1 quoted 49.3 cc for the
mesh-offset HR-CTV with the same masks: RIND5 is 7 % larger because the
mesh-offset band started at the smoothed marching-cubes surface.

**RTDOSE on each target (trilinear, rx 60 Gy) -- preview only, Phase 3
regenerates with `io/rt.py` and `dose_stats`:** `HRCTV_PHYS` D90 6365 cGy /
V100 0.937 (identical to the Phase 1 value); `RIND5(Cav_Post, Brain)`
4646 / 0.678; `RIND5(gtcore, cranial interior)` 2725 / 0.383;
`IMPLANT_REACH(12)` on `RIND5(Cav_Post)` 5859 / 0.880.

**PH8 (157 slices, 0.59 x 0.59 x 1.0 mm).** `reconstruct`: 26 s, 32 seeds,
cavity mask empty (the phantom pipeline segments no cavity), wall =
"phantom shell (no cavity segmented)". `build_hrctv(body shell, side=-1,
keep_mask=body)` runs in 1.2 s on the **fallback path**: 225.7 cc,
`cavity_source "mesh"`, `clip_source "gtcore:body"`, `side -1`, source label
"... (mesh-offset fallback: no cavity mask)", display shell 107,087 faces.
As before, this is the whole printed body minus its 5 mm skin -- the phantom
has no cavity mask to build a RIND5 from (the printed cavity is a hollow in
the body; a cavity segmentation for the phantom is stop-list work).

### Commands

```
# from the p2-targets worktree root
GTCORE_STRICT=1 python -m pytest -q tests/test_targets.py
GTCORE_STRICT=1 python -m pytest -q   # full suite at 32234a4: 888 passed, 14 skipped in 594 s (0:09:54)
# real-data check (scratch driver; PYTHONPATH must point at the worktree)
PYTHONPATH=. GT_DATA_ROOT=... python <scratch>/targets_realdata.py
#   reads $GT_DATA_ROOT/<JD-1mm>, $GT_DATA_ROOT/<JD RT RTSTRUCT>, $GT_DATA_ROOT/<JD RT RTDOSE>,
#   $GT_DATA_ROOT/<PH8>; the API calls it makes are, in order:
#   rind5(Cav_Post, aff, clip_mask=Brain, clip_affine=aff,
#         cavity_source="RTSTRUCT:Cav_Post", clip_source="RTSTRUCT:Brain")
#   rind5(res.cavity_mask, aff, clip_mask=res.masks["cranial_interior"], clip_affine=aff,
#         cavity_source="gtcore:cavity", clip_source="gtcore:cranial_interior")
#   implant_reach(rind, res.seeds.centers_ras, r) for r in 6, 8, 10, 12, 14
#   dice(hrctv_phys(CTV_GT, aff), target); coverage(...)
#   build_hrctv(res.meshes["cavity"], inside_mask=res.cavity_mask, inside_affine=aff,
#               keep_mask=res.masks["cranial_interior"], keep_affine=aff,
#               keep_source="gtcore:cranial_interior")
```

### Full suite

`GTCORE_STRICT=1 python -m pytest -q` from the worktree root at `32234a4`
(code `56651ba` + the integration's deterministic planner-drag test):
**888 passed, 14 skipped** in 594.27 s (0:09:54), other agents running
concurrently. Baseline before this task: 876 passed, 14 skipped (d1de921);
the 12 new tests are `tests/test_targets.py` (11) and the merged
integration test change.

### Notes for the coordinator

- **Acceptance 1(a) conflict.** The task asks for the digital-sphere rind
  volume within 3 % of the analytic shell on a 0.5 x 0.5 x 2 mm grid; the
  contract's definition (centre-to-centre EDT) gives -4.8 % there
  (one-sided, independent of the sphere radius: the deficit is about 0.2 mm
  x rind area). I kept the contract definition (it is also what `rind_mask`
  and the Phase 1 validation numbers used) and asserted 6 % plus the
  axis-exact band; a sub-voxel surface placement would be a model change
  for Jacob to decide, not a Phase 2 fix.
- **F6 r.** Dice vs `CTV_GT` peaks at 10 mm (0.881); 12 mm is 0.846.
  Reported, not chosen.
- `metrics.py` keeps a now-unused `from scipy import ndimage` (the
  `dose-stats` agent is editing the same file; drop it at the merge).
- `targets._stats` picks up `metrics.dose_stats` automatically once section
  A merges (try-import seam); until then it uses `hrctv_stats` /
  `weighted_stats`. No other code in the tree calls it yet.
- Not committed: the scratch driver and its copy of the
  `validation_clinical.py` rasteriser (owned by `rt-io`).

## 2026-11-03 — Phase 2 — RT DICOM readers (io/rt.py) (task `rt-io`, branch `p2/rt-io`)

Owner: agent `rt-io`. Commits: `c2a8952` (module + tests), `7d1c021`
(script import switch), `d40bb32` (merge of `paper2/integration` at `d1de921`), `fe8d942` (synthetic
RTPLAN test date), then this notes section. Branched from `paper2/integration` at `fde4f6e`;
`d1de921` (test-only + docs) merged before the full run.

### What was added

`gtcore/io/rt.py` (exported from `gtcore.io`):

| Function | Role |
|---|---|
| `load_rtstruct(path_or_dir) -> RTStruct` | ROI names/numbers, `contours(name)` as `(N, 3)` RAS mm (LPS→RAS by negating x and y, the same flip as `gtcore.io.dicom`), `contour_z_pitch_mm`, `contour_planes_z`, `frame_of_reference_uid`, `referenced_series_uid`, `describe()` |
| `rasterize_roi(rtstruct, name, affine, shape, supersample=4, min_fraction=0.5, slice_rule="nearest", pitch_mm=None)` | the rasteriser below; `rasterize_rois(rtstruct, names, volume)` on a `Volume`'s grid |
| `load_rtdose(path_or_dir, allow_any=False) -> Volume` | cGy (`DoseUnits` GY ×100, CGY ×1, anything else raises), `DoseGridScaling` applied, `GridFrameOffsetVector` → k axis (raises on non-uniform), `DoseSummationType` ∈ {PLAN, MULTI_PLAN, BRACHY} unless `allow_any`; affine built exactly like `gtcore.io.dicom` (row/column direction cosines, LPS→RAS flip); `meta` carries units / type / summation / FoR / referenced plan / manufacturer / software and a parsed `dvh_sequence` when the file has one |
| `load_rtplan(path_or_dir) -> RTPlan` | brachy sources from `ApplicationSetupSequence → ChannelSequence → BrachyControlPointSequence`: `position_ras`, `orientation_ras` (`ControlPointOrientation`, else the end-to-end direction of two distinct control points, else None), per-seed `air_kerma_strength_u` (`ReferenceAirKermaRate`), `reference_datetime`, isotope, model, half-life, active length; `plan_level` with technique / type, prescription (cGy), label / date, approval, counts. Built from the DICOM standard (no RTPLAN on this machine) and tested on a synthetic RTPLAN written with pydicom. The RT Plan IOD has no public attribute for the TG-43 formalism or the anisotropy dataset: `plan_level["formalism"]` is None and `plan_level["private_creators"]` lists the private blocks present so a vendor attribute can be located by hand |
| `reference_from_dvh_sequence(rtdose_or_items, rtstruct)` | RTDOSE `DVHSequence` → `{roi: {volume_cc, D90_cgy, Dmin/Dmax/Dmean}}` (cumulative curve, dicompyler convention, D90 interpolated linearly on the binned curve) |
| `parse_mim_dvh_export(path, roi_col=None, volume_col=None, d90_col=None, dose_units=None, volume_units=None, delimiter=None, layout="auto")` | tolerant CSV/TSV reader for the MIM export (format unknown until it arrives): layouts `wide` (one row per structure with volume and D90 columns), `long` (structure / metric / value rows) and `curves` (dose column + one cumulative-volume column per structure); preamble lines skipped; units read from `(Gy)` / `[cGy]` / `(cc)` / `(%)` in the header, defaults Gy and cc, all overridable |
| `verify_against_tps(rtdose, rtstruct, reference, rois=(...), rx_cgy=6000, tolerance=0.02)` | rasterises each ROI **on the RTDOSE grid**, takes the RTDOSE voxel values, compares volume and D90 with the reference; rows `roi, vol_mim, vol_gtcore, vol_rel_err, D90_mim, D90_gtcore, D90_rel_err, V100_gtcore, n_voxels, pass` (`pass` = both errors finite and ≤ 2 %) |
| `check_references(rtstruct, ct=None, rtdose=None, rtplan=None) -> List[str]` | frame-of-reference / referenced-series / referenced-plan consistency as warning strings that never contain a UID; nothing is written to disk |

D90 / V100 inside the module come from one private helper `_dose_stats`
(today `gtcore.dose.hrctv.hrctv_stats`, whose D90 is already the
Hyndman–Fan type-1 inverted-CDF quantile and whose V100 is the ≥ rule; a unit
test pins both: D90 of 10, 20, …, 100 is 10, V100 at rx 50 is 0.6). When agent
`dose-stats` lands `metrics.dose_stats`, the switch is the one-line body of
`_dose_stats`.

`gtcore/io/dicom.py::_series_meta` now also records `frame_of_reference_uid`
and `series_instance_uid` (header values kept in memory for
`check_references`; never printed). `scripts/validation_clinical.py` imports
`load_rtstruct / load_rtdose / rasterize_rois` instead of its three local
helpers (`_dcm`, `rasterise`, `rtdose_volume`); no other logic changed.

### Rasterisation rule (Methods-quotable)

Each physician structure is converted to a voxel mask on the evaluation grid
as follows. In-plane, every voxel is divided into 4 × 4 sub-pixels; the closed
planar contours of one plane are filled jointly with the even-odd (parity)
rule, so a contour drawn inside another is a hole and disjoint contours are a
union; a voxel is included when at least 50 % of its sub-pixels lie inside the
filled region (covered fraction ≥ 0.5). Through-plane, the structure's own
contour pitch *p* is the median spacing of its contour planes; a contour plane
at *z*<sub>c</sub> claims every voxel whose centre *z* satisfies
*z*<sub>c</sub> − *p*/2 ≤ *z* < *z*<sub>c</sub> + *p*/2 (a half-open interval,
so a centre exactly half a pitch from two planes is assigned once, and a slab
contoured on *k* planes is exactly *k·p* thick). Voxels more than half a pitch
beyond the first or last contour plane are outside, and nothing is
interpolated across missing planes. When the grid's slice spacing is coarser
than *p*, each output slice therefore takes the contour plane nearest its
centre; when it is finer, each plane is copied into every slice inside its
slab. The same rule is applied on the CT grid and on the TPS dose grid, which
is what makes a structure's volume comparable across grids.

Implementation note: the fill is an even-odd scan-line fill in numpy
(O(perimeter + area)); tests show it agrees with `skimage.draw.polygon` on
random polygons and that the two-polygon fill equals the XOR of the single
fills. The scan-line is needed because `skimage.draw.polygon` is O(bounding
box × vertices) and the 1249-vertex `Brain` contours on a 2048² supersampled
slice would take minutes. Measured on the clinical `Brain` (143 contours):
5.0 s on the CT grid with 4 × 4 supersampling vs 11.1 s for the old
unsupersampled skimage fill.

### Unit tests (`tests/test_io_rt.py`, 28 tests, synthetic DICOM in `tmp_path`)

- (a) sphere R = 10 mm contoured on 20 planes at 1 mm pitch, rasterised on
  0.5 × 0.5 × 1, 1 × 1 × 1.667 and 0.7 mm isotropic grids: volume within 2 %
  of 4/3 π R³ on all three; mask confined to [−R, R) ± half a voxel.
- (b) slab on 5 planes: thickness 5 × pitch (± half a pitch) and nothing
  beyond, for grid z-spacings 0.3, 0.5, 1.0 (two offsets) and 1.667 mm;
  an exact-tie grid (centres half a pitch from two planes) gives exactly 5
  slices (half-open rule).
- (c) square with a square hole on one plane → ring of 300 mm² (exact on a
  grid whose edges fall between sub-pixel centres); a separate test pins the
  tie behaviour when an edge passes through voxel centres (fraction exactly
  0.5 → in at `min_fraction=0.5`, out at 0.51).
- (d) POINT ROI and an ROI without contours → empty mask + warning; unknown
  name → `KeyError`.
- (e) RTDOSE: GY → cGy, CGY unchanged, RELATIVE raises; `DoseGridScaling`
  applied; non-uniform `GridFrameOffsetVector` raises; voxel (3, 5, 2)
  round-trips to the expected RAS position; negative frame direction;
  `DoseSummationType` FRACTION raises unless `allow_any=True`, MULTI_PLAN and
  BRACHY accepted; directory handling.
- (f) RTPLAN with 3 seeds: positions (LPS→RAS), orientations, strengths,
  reference datetime, isotope / model / type, prescription 6000 cGy, technique.
- (g) `verify_against_tps`: reference from the same synthetic data passes
  (also the analytic sphere volume); volume +5 % fails; D90 −5 % fails;
  missing reference row → NaN + fail, no exception; `DVHSequence` reference
  (cumulative and differential forms) parsed; CSV/TSV parser on the three
  documented example layouts plus explicit column names / units.
- (h) `check_references`: mismatched RTDOSE frame of reference → one warning
  without the UID; CT series mismatch; RTPLAN mismatch; consistent case → `[]`.

```
GTCORE_STRICT=1 python -m pytest -q tests/test_io_rt.py      # 28 passed in 1.3 s
GTCORE_STRICT=1 python -m pytest -q                          # 904 passed, 14 skipped, 15241 warnings in 595.87s (0:09:55) at d40bb32
#   (876 of the Phase 1 tree + 28 new; fe8d942 changed only tests/test_io_rt.py, re-run alone: 28 passed;
#   other agents ran concurrently, so the wall time is an upper bound)
```

### Real data — JD RT + JD-1mm (commit `d40bb32`)

Driver: a scratchpad script (not committed) run from the worktree root with
`PYTHONPATH=<worktree>` and `GT_DATA_ROOT`; it printed
`gtcore.__file__ = <worktree>\gtcore\__init__.py`. Equivalent in-tree
reproduction (from the repo root):

```
python -c "
from gtcore.io import load_volume
from gtcore.io.rt import load_rtstruct, load_rtdose, rasterize_roi, check_references
ct = load_volume('$GT_DATA_ROOT/<JD-1mm>')
rs = load_rtstruct('$GT_DATA_ROOT/<JD RT RTSTRUCT dir>')
dv = load_rtdose('$GT_DATA_ROOT/<JD RT RTDOSE dir>')
print(rs.describe()); print(check_references(rs, ct=ct, rtdose=dv))
for g in (ct, dv):
    for roi in ('Cav_Post', 'CTV_GT', 'CTV_Post', 'Brain', 'Seeds'):
        m = rasterize_roi(rs, roi, g.affine, g.array.shape)
        print(roi, m.sum() * g.spacing.prod() / 1000.0, 'cc')
"
```

Inputs as read: CT 204 × 512 × 512 at 0.52 × 0.52 × 1.0 mm; RTDOSE
250 × 266 × 266 at 1 × 1 × 1.667 mm, `DoseUnits` GY, `DoseType` PHYSICAL,
`DoseSummationType` PLAN, `DoseGridScaling` 0.2, maximum 813 Gy, **no
`DVHSequence`**, `ReferencedRTPlanSequence` present. RTSTRUCT from MIM 7.2.5.

**ROI table** (contours / planes / pitch): Lens_L 9/9, Lens_R 9/9,
Retina_L 17/17, Retina_R 18/18, OpticNerve_L 6/6, OpticNerve_R 5/5,
OpticChiasm 4/4, BrainStem 57/57, SpinalCord 35/35, **Brain 143/132**,
Lacrimal_L 7/7, Lacrimal_R 8/8, Body 189/186, L Hippo 17/17, R Hippo 24/24,
OptNrv_L_PRV 0, OptNrv_R_PRV 0, OpticChiasm_PRV 0 (empty), **Seeds 92/31**,
**Cav_Post 45/43**, **CTV_GT 176/47**, Res_Post 14/10, **CTV_Post 154/47**,
Point-2 1 POINT contour. All planar structures have a 1.000 mm pitch; the
multi-polygon planes (CTV_GT 176 polygons on 47 planes, Brain 143 on 132)
are disjoint pieces, handled by the even-odd fill. The POINT ROI and the
three empty PRVs rasterise to empty masks with a warning.

**`check_references(rtstruct, ct=JD-1mm, rtdose)`: no warnings** — the
RTSTRUCT's referenced series is the JD-1mm series and the RTSTRUCT, CT and
RTDOSE share one frame of reference (confirmed).

**Volumes, old rasteriser (the script's per-contour XOR into `rint(k)`,
voxel-centre rule) vs the new rule, on both grids:**

| ROI | CT grid, old | CT grid, new | RTDOSE grid, old | RTDOSE grid, new | cross-grid gap old → new |
|---|---|---|---|---|---|
| Cav_Post | 35.10 cc | 35.10 cc | 15.53 cc | 35.40 cc | −55.8 % → **+0.9 %** |
| CTV_GT | 19.02 cc | 19.01 cc | 11.62 cc | 19.39 cc | −38.9 % → **+2.0 %** |
| CTV_Post | 23.39 cc | 23.39 cc | 13.44 cc | 23.83 cc | −42.5 % → **+1.9 %** |
| Brain | 1383.98 cc | 1383.98 cc | 490.76 cc | 1384.47 cc | −64.5 % → **+0.0 %** |
| Seeds | 0.36 cc | 0.36 cc | 0.40 cc | 0.34 cc | +13.0 % → −4.6 % |

Reading. On the CT grid (1 mm slices = the 1 mm contour pitch) the new rule
reproduces the old volumes to 0.01 cc: the end-slice rule changes nothing in
z and the in-plane supersampling only moves boundary voxels. On the 1.667 mm
RTDOSE grid the old rasteriser is grossly wrong: two 1 mm contour planes
round to the same dose slice and their XOR cancels, so every structure loses
40–65 % of its volume. The requirements doc's "21.6 cc on the dose grid"
(+14 %) could not be reproduced on this tree by either path: nearest-neighbour
resampling of the CT-grid mask onto the dose grid (`metrics.resample_mask_to`)
gives 19.03 cc (+0.1 %), and the direct supersampled rasterisation 19.39 cc
(+2.0 %; Dice 0.972 between the two dose-grid masks). The residual +2 % for
CTV_GT is in-plane: the structure is a thin fragmented rind (176 polygons on
47 planes), so 1 mm voxels at the 0.5-fraction threshold are coarse relative
to its ~5 mm thickness; its z-extent is 46.67 mm (28 slices) on the dose grid
vs 47.00 mm (47 planes) on the CT grid, −0.7 %. Cav_Post, CTV_Post and Brain
agree across grids within 2 %, so the supersampled rule closes the cross-grid
artifact; the MIM volume comparison (`verify_against_tps`) remains the gate.
Seeds (0.8 mm diameter, contour areas 0.13–8.23 mm², median 3.64 mm²) cannot be
represented on a 1 mm × 1.667 mm grid by any rule (−4.6 %; 38 connected
components for 30 seeds on the dose grid, 52 with the old rasteriser) — the
script only ever uses Seeds on the CT grid, where both rasterisers give 30
components with identical voxel counts (min 30 / median 44 / max 56 voxels)
and centroids within 0.06 mm.

**RTDOSE D90 / V100 of CTV_GT on both grids** (rx 6000 cGy; CT grid =
trilinear sample of the RTDOSE at CT voxel centres, RTDOSE grid = the voxel
values; `_dose_stats`):

| CTV_GT | volume | D90 | D50 | V100 | V150 |
|---|---|---|---|---|---|
| CT grid, old mask | 19.02 cc | 6365 cGy | 8865 | 93.7 % | 48.2 % |
| CT grid, new mask | 19.01 cc | 6365 cGy | 8866 | 93.7 % | 48.2 % |
| RTDOSE grid, old mask | 11.62 cc | 6180 cGy | 8700 | 92.1 % | 46.4 % |
| RTDOSE grid, new mask | 19.39 cc | **6360 cGy** | 8880 | 93.6 % | 48.6 % |

With the new rule the two grids agree on D90 to 5 cGy (0.1 %) and on V100 to
0.1 percentage points; the old rasteriser's dose-grid D90 was 185 cGy (2.9 %)
low because of the lost volume. Same pattern for CTV_Post (D90 5073 / 5100
cGy, V100 81.8 / 82.1 %) and Cav_Post (9357 / 9320 cGy, V100 99.1 / 99.0 %).

### `scripts/validation_clinical.py` before / after the import switch

Both runs on JD-1mm + JD RT, each printing `gtcore.__file__` from its own
tree (`p2-integration` at `fde4f6e` for "before", `p2-rt-io` at `7d1c021`
for "after"); command
`python scripts/validation_clinical.py "$GT_DATA_ROOT/<JD-1mm>" "$GT_DATA_ROOT/<JD RT RTSTRUCT dir>" "$GT_DATA_ROOT/<JD RT RTDOSE dir>"`.
The printed tables are identical except for boundary voxels moved by the
in-plane supersampling (on the 1 mm CT grid the end-slice rule coincides with
the 1 mm contour pitch):

| Line | before | after |
|---|---|---|
| TPS structures (Cav_Post / CTV_GT / CTV_Post / Seeds) | 35.1 / 19.0 / 23.4 / 0.4 cc | identical |
| gtcore cavity, HR-CTV, rind rows (Dice, volumes) | — | identical |
| seeds: TPS 30, detected 32; one-to-one match 30/30 | median 0.14 mm, p95 0.73, max 0.89 | median 0.15 mm, p95 0.73, max 0.89 |
| RTDOSE on CTV_GT | D90 6365, D50 8865, Dmin 3921, V100 94 %, V150 48 % | D50 8866, rest identical |
| RTDOSE on CTV_Post / Cav_Post / gtcore HR-CTV / rind(Cav_Post) | — | identical |
| TG-43 / RTDOSE ratio at CTV_GT voxels > 5 mm from a seed | median 0.931, p5 0.777, p95 1.030, n = 13673 → implied S_K 3.76 U | median 0.931, p5 0.773, p95 1.029, n = 13665 → 3.76 U |
| gtcore TG-43 rows (6 lines) | — | D50 / Dmin / D90 differ by ≤ 2 cGy on five lines (e.g. our seeds 3.50 U on CTV_GT: D90 6168 → 6167); every V100 / V150 / volume identical |

The script stays on the new rasteriser (the intended fix); Phase 3 rebuilds it.

### Missing data / next steps

- **The MIM DVH / plan export is not on this machine**, so
  `verify_against_tps` cannot be run on real data yet (and the JD RTDOSE has
  no `DVHSequence`, checked with `"dvh_sequence" in load_rtdose(...).meta`).
  When the export arrives, run from the repo root:

  ```
  python -c "
  from gtcore.io.rt import load_rtstruct, load_rtdose, parse_mim_dvh_export, verify_against_tps
  rs = load_rtstruct('$GT_DATA_ROOT/<JD RT RTSTRUCT dir>')
  dv = load_rtdose('$GT_DATA_ROOT/<JD RT RTDOSE dir>')
  ref = parse_mim_dvh_export('$GT_DATA_ROOT/<MIM DVH export file>')   # add roi_col=, volume_col=, d90_col=, dose_units= if auto-detection fails
  for r in verify_against_tps(dv, rs, ref, rois=('Cav_Post', 'CTV_GT', 'CTV_Post', 'Brain'), rx_cgy=6000.0):
      print('%-9s vol %6.2f vs %6.2f cc (%.1f%%)  D90 %6.0f vs %6.0f cGy (%.1f%%)  %s' % (
          r['roi'], r['vol_mim'], r['vol_gtcore'], 100 * r['vol_rel_err'],
          r['D90_mim'], r['D90_gtcore'], 100 * r['D90_rel_err'], 'PASS' if r['pass'] else 'FAIL'))
  "
  ```

  Every row must pass before an F5 number is quoted (requirements §2 Grid,
  §3 F5). If MIM reports D90 by interpolating its binned DVH, a sub-percent
  D90 difference against the type-1 quantile is expected and inside the 2 %
  gate; the volume comparison is the sharper test of the rasteriser.
- **The RTPLAN is not on this machine**: `load_rtplan` is standard-built and
  synthetic-tested only. On arrival: `plan = load_rtplan('$GT_DATA_ROOT/<JD RT RTPLAN>')`,
  then `plan.n_sources` (expect 32), `plan.strengths_u` (per-seed S_K),
  `plan.sources[0].reference_datetime`, `plan.plan_level["private_creators"]`
  for the formalism hunt, and `check_references(rs, ct=ct, rtdose=dv, rtplan=plan)`.
- `_dose_stats` → `metrics.dose_stats` once agent `dose-stats` merges (one
  line).

## 2026-11-03 — Phase 2 — one `dose_stats` (task `dose-stats`, branch `p2/dose-stats`)

Requirements rev 3 §2 "Dose statistics", §6 Phase 2 first checkbox; Phase 2
contract section A. Branched from `paper2/integration` (d063c23, then
fast-forwarded to d1de921 before the full run). Code commit `00ac0a5`;
notes + benchmark driver in the follow-up commit (hash in the final line
of this section).

### The convention (for the Methods)

All dose–volume statistics in gtcore are computed by one function,
`gtcore.dose.metrics.dose_stats(doses, rx_cgy, weights=None,
voxel_volume_mm3=None)`. Dxx is the weighted inverted-CDF quantile
(Hyndman–Fan type 1): the doses of a structure are sorted ascending together
with their weights (equal weights for voxel structures such as the HR-CTV
rind or an RTSTRUCT mask; vertex-area weights for the +5 mm surface shell),
F is the cumulative weight fraction, and Dxx is the smallest dose d_i with
F(d_i) ≥ 1 − xx/100. Hence at least xx % of the structure receives ≥ Dxx,
and Dxx is always a dose that occurs in the structure — no interpolation
between samples (with equal weights this is numpy's `method="inverted_cdf"`,
not its default linear percentile); D100 = Dmin. Vyyy is the fraction of the
structure's weight receiving ≥ yyy % of the prescription (a ≥ rule, so a
voxel exactly at the prescription dose counts as covered). An empty
structure (no samples, or zero total weight) reports NaN for every dose and
fraction, never 0. The same function serves the planner's dose panel, the
optimizer's reported metrics and the clinical validation, so one dose vector
yields bit-identical D90 / V100 / V150 on every path.

### What changed

- `gtcore/dose/metrics.py` (new core): `weighted_quantile` (moved here from
  `plan.objective`, unchanged algorithm: zero-weight entries dropped, stable
  argsort, `searchsorted(cumsum, q·W − 4ε·W, side="left")`), `dose_stats`
  (one argsort, three O(n) passes; fixed 13-key set `DOSE_STAT_KEYS` =
  `D90 D50 D100 Dmin Dmax Dmean V100 V150 V200 n total_weight volume_cc
  rx_cgy`; `n` is an `int` and counts the samples with positive weight;
  `volume_cc = total_weight × voxel_volume_mm3 / 1000` when a voxel volume
  is given, else NaN — a shell has no volume; empty → NaN doses, `n = 0`,
  `total_weight = 0`, `volume_cc = 0`), `DVH.D` (type-1 on the unit-weight
  cumulative count, replacing `np.quantile(..., "inverted_cdf")`, which
  differs only where `n·q` overshoots an integer in floating point),
  `DVH.V` (≥, unchanged) and a new `DVH.stats(rx)`.
  `rind_mask` / `resample_mask_to` untouched (agent `targets`).
- Thin wrappers (names, signatures and old keys kept; every number from
  `dose_stats`): `dose.dvh.dvh_stats` (was `np.percentile` linear, 0 on
  empty), `dose.hrctv.hrctv_stats` (was `DVH.D/V`, already type-1),
  `dose.metrics.dose_metrics` (idem), `plan.report.weighted_stats` (was
  `objective.weighted_quantile` with a midpoint-linear
  `weighted_quantile_fallback` behind a `getattr`; the fallback is deleted;
  0 on empty → NaN; `Dmin`/`Dmax` now over positive-weight samples only, as
  the quantiles always were), `plan.objective.metrics_from_dose` (returns the
  `METRIC_KEYS` subset `V100 V150 V200 D90 Dmean`, as the solver tests pin
  that exact key set; 0 on empty → NaN), `plan.api.target_metrics` and
  `plan.api.weighted_quantile` (0 on empty → NaN, `hard` included),
  `plan.milp._metrics_local` (own `_weighted_quantile` deleted),
  `plan.solvers._HardEval.metrics_from_dose`, `_ContinuousCore.metrics` and
  `_weighted_quantile` (delegates; NaN on empty as before). Nine
  implementations of D90 became one.
- `dose.dvh.format_report` prints `n/a` for a shell with no samples (the
  planner's HR-CTV row already tested `np.isfinite`). `gtcore.dose` exports
  `dose_stats`, `weighted_quantile`, `DOSE_STAT_KEYS`.
- Not changed: `OptimizeReport.summary()` and `gt optimize` print `nan` with
  `%.3f` for an empty target (honest; no `n/a` wording added there);
  `plan.sweep._row` compares `D90 >= rx`, which is `False` for NaN as
  intended; `tests/review_reference.py::weighted_quantile` (the test
  oracle, 0.0 on empty) and `test_plan_objective.brute_metrics` stay as
  independent references on purpose.

### Hot loop (performance criterion) — which path calls what

`dose_stats` sorts, so it is kept out of every per-move evaluation:
`plan.objective.hard / soft / gain / gains_all / soft_gains_all` use
`_coverage_terms` (O(M) matmuls) and `tail_rows`, unchanged byte for byte;
`plan.solvers._HardEval.hard_from_dose / soft_from_dose / hard_all /
soft_all / coverage` and `_ContinuousCore.hard / soft / v100` likewise.
`dose_stats` is reached only through the *reported* metrics:
`Objective.metrics` → `metrics_from_dose` (once per `solve_*` call in
`_result`, in `evaluate`, per `sweep_n` row, per MILP result),
`_HardEval.metrics_from_dose` (only when `Objective.metrics` is a stub —
never in the merged tree — and in tests), `_ContinuousCore.metrics` (once
per `refine_continuous`), `plan.api.target_metrics` (once per
`suggest_next` for the base `hard`), and in `final_report` the three shell
rows, the weighted `target` row and the `rind` row. The SA with 40 091
evaluations therefore calls it once.

### Timings

Hardware AMD Ryzen 5 7600X / 31 GB / Windows 11; other agents ran test
suites concurrently, so every number is an upper bound and the interleaved
comparison is the one that counts. Driver: `scripts/bench_dose_stats.py`
(committed with these notes); "before" = detached worktree at `d063c23`
(untouched Phase 1 tree, `gtcore.__file__` printed and asserted), "after"
= this branch at `00ac0a5`. Toy instance `tests/plan_fixtures.toy_instance`
(C = 100, M = 4000), N = 8, `SA_N_RESTARTS × SA_N_SWEEPS = 3 × 40` (the
current defaults; Phase 4 raises them).

```
PYTHONPATH=<tree> GTCORE_STRICT=1 python scripts/bench_dose_stats.py <tree> <label>        # full
PYTHONPATH=<tree> GTCORE_STRICT=1 python scripts/bench_dose_stats.py <tree> <label> quick  # A/B
```

Interleaved A/B (before, after, before, after; **minimum of the two runs per
tree**; micro-benchmarks are best-of-7 over 200 random 8-tile selections,
solvers best-of-3):

| quantity | before `d063c23` | after `00ac0a5` | change |
|---|---|---|---|
| `metrics_from_dose` (M = 4000) | 93.8 µs | 93.8 µs | 0.0 % |
| `Objective.metrics` | 224.6 µs | 225.2 µs | +0.3 % |
| `Objective.evaluate` | 658.2 µs | 666.2 µs | +1.2 % |
| `Objective.hard` (code unchanged) | 149.9 µs | 157.0 µs | +4.7 % — load noise band |
| `gains_all` (code unchanged) | 15.84 ms | 16.27 ms | +2.7 % — load noise band |
| `solve_greedy`, N = 8 | 0.369 s | 0.368 s | −0.3 % |
| **`solve_sa` 3 × 40, N = 8** (40 091 evals) | **9.417 s** | **9.435 s** | **+0.2 %** |
| `final_report` 1 mm: shells / target / rind | 0.008 / 0.003 / 0.233 s | 0.008 / 0.003 / 0.250 s | statistics ≪ dose grid (9.6 s) |

The two quantities whose code did not change moved by +4.7 % and +2.7 %
between trees, so that is the noise floor of this machine today; the SA
(the criterion) and the only changed call (`metrics`) moved by ≤ 0.3 %.
Optimizer not measurably slower: **met (< 5 %)**.

Single full runs (one each, noisier): before — `solve_sa` 10.786 s,
`plan.optimize(sa, 8)` on the 1 mm synthetic cavity 304.6 s total
(candidates 31.6, influence 2.6, conflicts 230.2, solver 19.3, report 20.8),
`final_report(1 mm, rind)` 18.9 s; after — 11.829 s, 298.2 s (32.5 / 2.6 /
231.0 / 15.8 / 16.5), 15.3 s. Same selections and objective values on both
trees (`solve_sa` objective 1.89862, V100 0.9820, D90 6610.5 cGy).

Effect on reported numbers: only the planner's unweighted shell rows
(`dvh_stats`: wall / +5 / +10 mm vertex samples) change, by at most one
sample spacing in the lower tail — on the benchmark cavity the +5 mm shell
D90 went 6882.5 → 6882.4 cGy (full run) and 3166.0 → 3165.8 cGy (quick run);
the area-weighted `target` row (6795.3 / 3093.4 cGy), the `rind` row
(7860.3 / 3718.1 cGy), every V-value, the HR-CTV row and all optimizer
metrics were already type-1 / ≥ and are unchanged.

### Test pins that changed (linear percentile → type-1; 0 on empty → NaN)

| test | old | new |
|---|---|---|
| `tests/test_dvh.py::test_dvh_curve_and_stats_closed_form`, doses `[1, 2, 3, 4]` | `D50 == np.percentile(d, 50)` = 2.5; `D90 == np.percentile(d, 10)` = 1.3 | `D50 == 2.0`; `D90 == 1.0` (= `np.quantile(..., method="inverted_cdf")`) |
| same test, `dvh_stats([], 2.0)` | every value `== 0.0` | all eight doses / fractions NaN, `n == 0`, `total_weight == 0.0` |
| `tests/test_plan_report.py::test_weighted_quantile_matches_numpy_for_unit_weights` | `weighted_quantile_fallback` within 0.01 of the linear `np.percentile` | `metrics.weighted_quantile == np.percentile(..., method="inverted_cdf")` exactly |
| `tests/test_plan_report.py::test_weighted_quantile_respects_weights` | fallback `≈ 3.0 (abs 0.05)`; `fallback([], [], .5) == 0.0` | `weighted_quantile == 3.0`; empty raises `ValueError`; `weighted_stats([], [], rx)` → NaN, `n == 0` |
| `tests/test_plan_api.py::test_weighted_quantile_and_target_metrics` | `target_metrics([], [], RX)`: `V100 == 0.0 and hard == 0.0` | both NaN |

Pins that did not need to move (already the type-1 / ≥ convention or
tolerance-based): `test_dose_metrics` (ramp D90 100 → within 1 of 101, same
value before and after since `DVH.D` already used `inverted_cdf`),
`test_hrctv` (abs 100 cGy), `test_plan_objective` (brute-force type-1
reference, exact key set `{V100, V150, V200, D90, Dmean}` kept),
`test_plan_solvers::test_weighted_quantile_matches_numpy_for_unit_weights`
(vs `inverted_cdf`), `test_plan_milp` (D90 coverage property), and
`test_plan_review` (1 % of rx). My own new test file had three wrong
assertions on first run (a copied zero-weight case, a `nan == nan`, and an
exact float equality `190·8/1000 == (190/333)·(333·8/1000)`), all test-side.

### Grep of the tree

- `np.percentile(` on a dose vector: only `scripts/scout_objective.py:264`
  (`obj_d90`, linear percentile along the rows of a dose matrix) and that
  script's own `weighted_quantile` (type-1 without the zero-weight filter).
  It is the section 7.1 objective scout whose results are historical in
  `docs/optimize-notes.md`; left unchanged so the scout reproduces. All
  other `np.percentile` calls in `gtcore/` and `scripts/` are on error
  distributions (p95 of localization errors, interference ratios), not dose
  statistics.
- Callers comparing D90 / V100 with 0 to mean "empty": none in `gtcore/` or
  `scripts/`. `plan.sweep._row` (`D90 >= rx`) is NaN-safe; `planner.py`
  `_dose_panel_lines` already used `np.isfinite`; `plan.__init__._jsonable`
  writes NaN as `null`.

### Tests

New `tests/test_dose_stats.py` (25 tests): analytic weighted / unweighted
cases by hand (ties, single element, the ≥ rule at exactly rx, equal-weight
repetition), Dxx is a sample value covering ≥ xx % and the smallest such,
unweighted Dxx equals numpy `inverted_cdf` for n ∈ {1, 2, 3, 7, 10, 25, 64,
200, 1001}, the quantile primitive (zero weights ignored, errors, one
function behind `plan.objective`, `plan.api`, `plan.solvers`), empty /
zero-weight → NaN through every wrapper, bad weights raise, zero-weight
samples excluded, equal weights = unweighted (unit weights bit-identical
including `Dmean`), the contract's identical-output test (one dose vector
through `hrctv_stats`, `weighted_stats`, `metrics_from_dose`, `dose_metrics`
on a `Volume` + mask, `dvh_stats`, `target_metrics`: bit-identical D90 /
V100 / V150), `DVH.D/V/stats` agree with `dose_stats`, `format_report`
`n/a`, and a loose speed bound (two `dose_stats` on 10⁶ samples < 5 s;
measured ≈ 0.3 s).

Affected files first: `GTCORE_STRICT=1 python -m pytest -q
tests/test_dose_stats.py tests/test_dvh.py tests/test_dose_metrics.py
tests/test_hrctv.py tests/test_plan_objective.py tests/test_plan_report.py
tests/test_plan_api.py tests/test_plan_solvers.py tests/test_plan_milp.py
tests/test_plan_influence.py tests/test_plan_review.py
tests/test_planner_interaction.py` → `221 passed in 160.00s` (after the
test-side fixes; the planner drag test from d1de921 passed).

Full suite (`GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider` from
the worktree, conftest sets the flag): see the line appended below.

Full-suite result at `00ac0a5` + these notes (tree = `paper2/integration`
d1de921 + this branch; `GTCORE_STRICT=1`, other agents running concurrently,
so the wall time is an upper bound):
`901 passed, 14 skipped, 15240 warnings in 476.78s (0:07:56)` — the 14 skips
are the Phase 1 set (13 interface placeholders + the `GT_E2E`-gated run);
876 → 901 = the 25 new `test_dose_stats.py` tests. Integration moved to
66dc856 (targets + rt-io) during this run; not merged here by the
coordinator's instruction (conflicts in `metrics.py` / `hrctv.py` resolved
at merge).


## 2026-11-03 — Phase 3 — formalism: angular symmetry test and residual trend (task `formalism`, branch `p2/formalism`)

Owner: agent `formalism`. Branched from `paper2/integration` at `e13a4af`. Commits:
`aaddf65` (module + script + tests); the four CSVs and this section are the next
commit on the branch (`git log --oneline paper2/integration..p2/formalism`). Every number below was produced by
`python scripts/formalism_check.py "$GT_DATA_ROOT/<PT-CT1mm>" "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>"`
run from the worktree root at commit `aaddf65` (the script prints
`gtcore.__file__` = the worktree's `gtcore`), 157 s wall (reconstruct 20 s, the
rest the fits; other agents ran concurrently, so an upper bound). Calibration set:
this case (`PT-CT1mm`, `PT-RT`); no parameter was tuned on it. CSVs in
`docs/figures/data/` (`git add -f`, the directory name `data/` is git-ignored):
`formalism_angular_ratio.csv` (variant, bin, mean, sd, median, n),
`formalism_variants_summary.csv` (one row per variant: n, overall mean/median, slope,
end-to-end, RMS of log ratio, r median/max, description),
`formalism_axis_comparison.csv` (per source: gtcore PCA axis vs RTDOSE-fitted,
RTSTRUCT and RTPLAN axes) and `formalism_hypotheses.csv`.

### What was added

`gtcore/dose/formalism.py` (numpy/scipy only; not re-exported from `gtcore.dose`
to leave the shared `__init__` alone):

| Function | Role |
|---|---|
| `angular_symmetry_test(reference, centers, axes, sk, engine, r_min_mm=3, r_max_mm=15, min_fraction=0.5, bins_deg, supersample=None, calc="2D"/"1D")` | the packaged test: per-source gtcore contributions at the reference voxel centres (or averaged over `supersample` sub-samples per voxel), dominant source, selection, ratio `reference / gtcore` binned by angle to the dominant source's axis; returns `AngularRatioResult` (bins with mean/sd/median/n, overall mean/median, least-squares slope per 90 deg, end-to-end, RMS of log ratio, r median/max, per-voxel detail) |
| `per_seed_doses` | `(N_seeds, M)` matrix whose column sum is `engine.dose_at_points` (bit-identical, tested) |
| `phi_an(engine, r_cm)` / `point_source_1d_dose` | TG-43U1 1D formalism `Lambda G_L(r, theta0)/G_L(r0, theta0) g_L(r) phi_an(r)` with `phi_an` integrated from the engine's own 2D rate (TG-43U1 Eq. 12, trapezoid on 1801 nodes; the printed consensus row can be passed as `phi_an_table`); diagnostic inside the check only, **no engine mode** |
| `prediction_rows(result, centers, axes_alt, calc_alt, ...)` | what the binned ratio would be under another model on the *same* voxels (`D_alt / D_calc`): the 1D prediction and the "all axes along z" prediction |
| `fit_source_to_reference` / `fit_sources_to_reference` / `fit_axis_to_reference` | per-source axis (hemisphere scan, 800 directions + three local tilt grids) and/or sub-voxel position (Nelder-Mead, max shift 1.5 mm) fitted to the reference's 2-6 mm shell, other sources fixed, cost = RMS of `log(ref/model)` about its mean (a flat scale cannot steer the fit); `calc="1D"` fits a point-source position |
| `fit_common_axis` | one axis for all sources (the TPS default-orientation hypothesis), with the RMS for the given axes and for RAS z alongside |
| `compare_axes(a, b)` | per-seed angle (sign-blind, 0-90 deg), median, mean, p95, max, n; NaN rows skipped |
| `perturb_axes(axes, deg, rng)` | every axis rotated by exactly `deg` about a random perpendicular |
| `rtdose_source_positions(volume, min_cgy=20000, merge_mm=3)` / `match_sources_to_seeds` | the plan sources as RTDOSE local maxima (voxel centres) and their Hungarian match to detected seeds |

`scripts/formalism_check.py <PT-CT1mm> <RTSTRUCT> <RTDOSE> [--rtplan] [--sk] [--out] [--supersample 3 3 5] [--no-fit]`
runs `reconstruct` for the gtcore seeds/axes, reads the 32 sources off the RTDOSE,
matches them to the gtcore seeds (axes) and to the RTSTRUCT `Seeds` contours (PCA
axes of the contours), runs the baseline and every variant below, the fits and the
hypothesis ranking, and writes the four CSVs with header comments (script, command
with dataset ids, commit, datasets, selection rule). With `--rtplan` it adds the
variant `b6_axes_rtplan` (axes = `ControlPointOrientation`) and fills the
`pca_vs_rtplan_deg` column (code path exercised on a synthetic RTPLAN written with
`tests/test_io_rt.write_rtplan`; no RTPLAN is on this machine).

`tests/test_formalism.py` (25 tests, synthetic references on a 1 x 1 x 1.667 mm grid,
8 seeds on a 10 mm lattice with random axes): 2D reference -> 1.00 +- 0.01 in every bin
(mean within 0.01, SD < 0.01, exact and tabulated kernels, flipped axes); 1D reference
-> measured bins equal `prediction_rows(calc="1D")` to 0.01 and are not flat, and
in the 6-15 mm far field of an isolated seed the classic signature (first bin
1.15-1.35, transverse 0.94-0.99, monotone); 1D read against a 1D gtcore flat; flat
S_K scales every bin and cannot make a trend; axis error raises the scatter and the
bins equal the prediction with the true axes; voxel averaging; `compare_axes` on
known rotations (0/7.5/25/60 deg, 120 -> 60 fold, sign flip -> 0, mixed, NaN rows);
`perturb_axes`; position fit recovers grid-quantised centres to < 0.15 mm; joint fit
recovers 20 deg-off axes to < 5 deg and positions to < 0.25 mm; 1D position fit;
common-axis scan recovers a shared direction (z and an oblique one) within 3 deg;
`rtdose_source_positions` + matching; input validation; `phi_an` is the
solid-angle average of the 2D dose (0.2 %) and `phi_an(1 cm)` = 0.965 (0.955-0.975
pinned; the engine gives 0.960-0.964 at 2-10 cm, below 1 at every tissue radius).

### Definitions (Methods-quotable)

*Selection.* Every RTDOSE voxel within 15 mm of a plan source gets the per-source
gtcore doses at its centre; its dominant source is the largest contributor. A voxel
enters the test when its dominant source lies 3-15 mm away and contributes more than
50 % of the gtcore total, so its dose is essentially one seed's and the angle `theta`
to that seed's long axis is meaningful. The ratio RTDOSE / gtcore is binned in six
15-degree bins of `theta`; mean, SD and n per bin, plus the least-squares slope of
the ratio over 0-90 deg and the RMS of `log(ratio)` about its mean (the scatter).
Plan sources = the 32 RTDOSE local maxima above 200 Gy merged within 3 mm (voxel
centres, i.e. quantised to +-0.5 / 0.5 / 0.83 mm); each is matched one-to-one to a
gtcore seed (32/32 within 3 mm, median 0.92, max 1.45 mm) whose PCA long axis is used.

*1D prediction.* `D_1D(r) = Lambda G_L(r, 90)/G_L(1 cm, 90) g_L(r) phi_an(r)`, with
`phi_an(r) = int_0^pi D_2D(r, theta) sin(theta) dtheta / (2 D_2D(r, 90))` from the
consensus `F(r, theta)` and `g_L` (0.965 at 1 cm, 0.96 at 2-10 cm, 1.01 at 5 mm).
The 1D-vs-2D difference is r-dependent: inside ~6 mm the line-source geometry
function raises the on-axis 2D dose above the point-source value
(`G_L(r, 0)/G_L(r, 90)` = 2.0 at 3 mm, 1.25 at 5 mm; `F(r, 0)` ~ 0.83), beyond it
`F(r, theta) < 1` wins (at 10 mm, 10 deg: `D_1D/D_2D` = 1.29). Rev 3's "a 1D plan
would give 1.29 -> 0.96" is the far-field signature (reproduced by the unit test on an
isolated seed at 6-15 mm: 1.12-1.3 -> 0.96). Under the rev 3 selection on this
implant **no selected voxel is more than 5.1 mm from its dominant seed** (seeds 10 mm
apart: beyond ~5 mm a neighbour always takes > 50 %; r median 3.5 mm), so the 1D
prediction in those bins is 0.95 -> 1.05, not 1.29 -> 0.96. The discrimination
between 1D and 2D therefore rests on the scatter and on the axis analysis below,
not on the sign of the first bin.

### Results — PT-RT RTDOSE (250 x 266 x 266, 1 x 1 x 1.667 mm, GY, quantised to 20 cGy) vs gtcore @ 32 sources, S_K 3.5 U nominal, commit `aaddf65`

**A. The rev 3 test reproduced** (`baseline`: 2D TG-43U1S2, maxima positions, gtcore PCA
axes; n = 661 voxels of 65 002 within 15 mm; 26 in the first bin):

| theta to PCA axis | 0-15 | 15-30 | 30-45 | 45-60 | 60-75 | 75-90 | slope / 90 deg | RMS log |
|---|---|---|---|---|---|---|---|---|
| RTDOSE / gtcore-2D | 0.934 +- 0.143 | 1.009 +- 0.222 | 1.012 +- 0.196 | 1.055 +- 0.197 | 1.090 +- 0.166 | 1.104 +- 0.166 | +0.179 | 0.171 |
| n | 26 | 75 | 130 | 156 | 136 | 138 | | |
| `pred_1D` = D_1D / D_2D (if MIM were 1D) | 0.947 | 0.952 | 0.971 | 1.010 | 1.041 | 1.054 | | |
| `pred_2D_axes_z` = D_2D(all axes along z) / D_2D(PCA) | 0.921 | 0.944 | 0.965 | 0.994 | 1.024 | 1.040 | | |
| measured / `pred_2D_axes_z` | 1.014 | 1.069 | 1.049 | 1.061 | 1.064 | 1.062 | | |
| `ratio_vs_1D` = RTDOSE / gtcore-1D | 0.984 +- 0.127 | 1.058 +- 0.219 | 1.041 +- 0.195 | 1.045 +- 0.194 | 1.048 +- 0.161 | 1.047 +- 0.158 | +0.019 | 0.162 |

Rev 3 quoted 0.94 -> 1.07; here 0.93 -> 1.10 (same selection; the difference is the
exact seed/axis set of the earlier scratch run). The within-bin SD of 0.14-0.22 is
dominated by the half-voxel quantisation of the source positions (a 0.5 mm shift at
3.5 mm is a 30 % dose change), so the first bin (n = 26) has a standard error of
0.03. The measured curve divided by the all-axes-along-z prediction is flat at
1.01-1.07: **the rev 3 trend is, to within noise, what a TPS computing every source
with its axis along the patient S-I direction produces when read with the physical
(PCA) axes.**

**B. Candidate causes, each as a labelled variant of the same binned ratio**
(`formalism_angular_ratio.csv`; bins by angle to the axis the variant uses):

| variant | 0-15 | 15-30 | 30-45 | 45-60 | 60-75 | 75-90 | n | slope | RMS log | reading |
|---|---|---|---|---|---|---|---|---|---|---|
| `a_clrp_v2` (CLRP v2 F and g_L) | 0.933 | 1.003 | 1.011 | 1.056 | 1.084 | 1.103 | 645 | +0.179 | 0.171 | identical to the consensus: **not the dataset** (the two datasets differ by < 1.5 % off-axis and only inside the capsule's solid angle) |
| `b1_axes_flipped` | 0.934 | 1.009 | 1.012 | 1.055 | 1.090 | 1.104 | 661 | +0.179 | 0.171 | bit-identical (F is symmetric) |
| `b2_axes_perturbed_10deg` | 1.005 | 0.997 | 1.022 | 1.052 | 1.090 | 1.099 | 658 | +0.148 | 0.171 | a 10-20 deg PCA error moves bins by a few % only |
| `b2_axes_perturbed_20deg` | 0.953 | 1.031 | 1.020 | 1.059 | 1.057 | 1.099 | 653 | +0.133 | 0.167 | (synthetic: 20 deg off changes the bins by <= 3.4 %) |
| `b4_axes_rtstruct_pca` (30 contoured sources, axes = PCA of the `Seeds` contours) | 1.052 | 1.059 | 1.090 | 1.116 | 1.155 | 1.170 | 778 | +0.164 | 0.188 | same trend; gtcore PCA vs contour PCA agree: median 4.8, max 27.8 deg |
| `baseline_same_sources` (30) | 1.011 | 1.075 | 1.076 | 1.126 | 1.159 | 1.175 | 786 | +0.190 | 0.188 | |
| `b5_axes_all_z` (every axis along RAS z; bins by angle to z) | 1.160 | 1.151 | 1.088 | 1.043 | 1.039 | 1.034 | 569 | -0.146 | 0.158 | trend reverses in sign and the scatter drops |
| `c_voxel_averaged` (gtcore averaged over 3 x 3 x 5 sub-samples per RTDOSE voxel) | 0.922 | 1.000 | 1.003 | 1.053 | 1.090 | 1.105 | 680 | +0.196 | 0.167 | **not grid averaging** |
| `d_sk_x1.07` | 0.873 | 0.943 | 0.946 | 0.986 | 1.019 | 1.032 | 661 | +0.168 | 0.171 | every bin / 1.07, slope unchanged: **a flat S_K cannot make a trend** |
| `e_positions_gtcore` (sources at the gtcore CT centres) | 1.060 | 1.006 | 1.033 | 1.051 | 1.094 | 1.122 | 735 | +0.171 | 0.180 | same trend, noisier |
| `baseline_dominance30` (> 30 % instead of > 50 %) | 1.010 | 1.011 | 1.005 | 1.025 | 1.042 | 1.052 | 8062 | +0.074 | 0.106 | trend halves when neighbours dilute the single-seed anisotropy |
| `b5_axes_all_z_dominance30` | 1.074 | 1.054 | 1.030 | 1.022 | 1.015 | 1.014 | 7837 | -0.047 | 0.097 | |
| `b3_axes_fitted_rtdose` (axis per seed fitted to the RTDOSE 2-6 mm shell, positions = maxima) | 1.150 | 1.175 | 1.080 | 1.082 | 1.004 | 1.015 | 566 | -0.200 | 0.153 | fitted axes: median 67 deg from the PCA axes, 5.4 deg from z (see C) |

**C. Which axes does the RTDOSE carry?** (`formalism_axis_comparison.csv`,
`formalism_hypotheses.csv`)

| comparison (deg, sign-blind) | median | mean | p95 | max | n |
|---|---|---|---|---|---|
| gtcore PCA axis vs RTSTRUCT `Seeds` contour PCA axis | 4.8 | 7.3 | 20.9 | 27.8 | 30 |
| gtcore PCA axis vs RAS z | 64.5 | 65.7 | 90.0 | 90.0 | 32 |
| gtcore PCA axis vs axis fitted to the RTDOSE shell (position fixed) | 67.0 | 65.2 | 88.9 | 89.9 | 32 |
| gtcore PCA axis vs jointly fitted axis (position + axis) | 66.4 | 65.6 | 89.5 | 89.9 | 32 |
| **jointly fitted axis vs RAS z** | **3.6** | 4.2 | 9.3 | 10.0 | 32 |
| gtcore PCA axis vs RTPLAN `ControlPointOrientation` | — | — | — | — | no RTPLAN on this machine |

One common axis fitted to all 32 shells: (-0.037, 0.009, 0.999), **2.2 deg from RAS
z**; RMS of log(RTDOSE/model) 0.1995 for the common axis, 0.1996 for exactly z,
0.2652 for the PCA axes (positions = maxima). Joint fits move the sources by 0.77 mm
median, 0.99 mm max — inside the half-voxel quantisation of the maxima (up to
1.09 mm) — and reduce the per-seed RMS from 0.276 to 0.040 (median).

Hypotheses ranked by the scatter after a per-seed sub-voxel position fit *under that
hypothesis* (3 free parameters each; `fit_axis=False`; `formalism_hypotheses.csv`):

| hypothesis | RMS log, median | mean | shift median / max (mm) |
|---|---|---|---|
| H3: 2D, every axis along RAS z | **0.042** | 0.044 | 0.77 / 0.99 |
| H4: 2D, per-seed jointly fitted axes (2 extra parameters per seed) | 0.042 | 0.045 | 0.77 / 0.98 |
| H2: 1D point source | 0.104 | 0.107 | 0.65 / 0.91 |
| H1: 2D, gtcore PCA axes (the physical seed axes) | 0.180 | 0.172 | 0.70 / 1.11 |

The free per-seed axes buy nothing over "all along z" (0.042 vs 0.042), 1D is 2.5 x
worse, the physical axes 4 x worse. With the H3 positions, binned by angle to z:

| theta to z | 0-15 | 15-30 | 30-45 | 45-60 | 60-75 | 75-90 | n | slope | RMS log |
|---|---|---|---|---|---|---|---|---|---|
| `H3_2D_axes_z_refined` RTDOSE / gtcore-2D(z) | 1.033 +- 0.055 | 1.084 +- 0.055 | 1.030 +- 0.035 | 1.013 +- 0.014 | 1.005 +- 0.012 | 1.001 +- 0.012 | 722 | -0.094 | 0.040 |
| `H3_2D_axes_z_refined_avg` (+ voxel averaging 3 x 3 x 5) | 0.949 +- 0.051 | 1.048 +- 0.052 | 1.019 +- 0.032 | 1.010 +- 0.014 | 1.005 +- 0.012 | 1.002 +- 0.012 | 753 | -0.024 | 0.037 |
| `H3_2D_axes_z_refined_avg_clrp` (CLRP v2) | 0.940 | 1.046 | 1.018 | 1.009 | 1.004 | 1.001 | 748 | -0.018 | 0.039 |
| `H2_1D_refined` RTDOSE / gtcore-1D | 1.143 +- 0.098 | 1.184 +- 0.111 | 1.070 +- 0.057 | 1.020 +- 0.028 | 0.984 +- 0.025 | 0.965 +- 0.027 | 707 | -0.283 | 0.087 |
| `pred_1D_in_H3_bins` = D_1D / D_2D(z) | 0.921 | 0.923 | 0.962 | 0.994 | 1.026 | 1.045 | | | |
| `H1_2D_pca_axes_refined` (bins by angle to PCA axis) | 0.970 +- 0.090 | 0.973 +- 0.111 | 0.986 +- 0.101 | 1.040 +- 0.111 | 1.057 +- 0.098 | 1.070 +- 0.080 | 787 | +0.153 | 0.097 |

The RTDOSE is anisotropic about z with line-source magnitude: read against an
isotropic 1D model it rises to 1.14-1.18 within 30 deg of z and falls to 0.965 in the
transverse plane (SD per bin 0.03-0.11), exactly the 2D-vs-1D shape (the D_2D(z)/D_1D
prediction is 1/0.921 = 1.09 -> 1/1.045 = 0.96; the measured excess near z beyond that
is the same +5-8 % near-axis residual seen in the H3 rows). **MIM is 2D.** Scale: H3
overall RTDOSE / gtcore 1.025 (median 1.012) at 3-5 mm -> implied S_K 3.59 U at
nominal 3.5 U, a diagnostic only (`validation_clinical.py` measures 1.074 / 3.76 U at
CTV_GT voxels > 5 mm from a seed with the PCA axes, which this result says is biased
by the axis convention; the nominal 3.5 U stays).

**D. What remains.** With the TPS-convention axes, refined positions and voxel
averaging, the ratio is within 1-2 % (SD 1.2-3 %) from 30 to 90 deg and within 5 %
(0.949 / 1.048, n = 42 / 142, SE 0.008 / 0.004) in the two bins nearest the z axis,
where r is 3-4 mm, `F(r, theta)` has its 5-10 deg dip (0.68-0.72) and `G_L(r, 0)` its
on-axis spike. The sign flips between the centre-sampled and the voxel-averaged
evaluation (1.03 / 1.08 vs 0.95 / 1.05), so this residual is at the level of how the
TPS samples its 1 x 1 x 1.667 mm grid near the axis (centre vs average, its own F
interpolation) and of the 0.04 position-fit residual; it is not explained here and
cannot be without the TPS's calculation settings (RTPLAN private blocks). It is
confined to < 30 deg of z at r < 5 mm, i.e. to a few voxels per seed; it does not
touch the 5 mm prescription surface or the DVH.

### Conclusion

**MIM used the 2D line-source formalism** (1D rejected: RMS 0.104 vs 0.042; the
RTDOSE carries line-source anisotropy about one axis). **The residual +-6 % angular
trend of rev 3 is explained by the seed-axis definition, not by the anisotropy
dataset, the grid or S_K:** the TPS computed every source with its long axis along
the patient S-I direction (RAS z; common fitted axis 2.2 deg from z, per-seed 3.6 deg
median, 10 deg max), while the physical seeds lie in the tile planes (gtcore PCA axes
64.5 deg from z in median, confirmed by the physician's `Seeds` contours to 4.8 deg).
Reading the RTDOSE with the physical axes produces exactly the 0.93 -> 1.10 curve
(prediction 0.92 -> 1.04 x a flat 1.06). Residual after the axis correction: +-5 %
inside 30 deg of z at r < 5 mm, unexplained (TPS near-axis sampling), < 2 %
elsewhere. The RTPLAN, when it arrives, is expected to show
`ControlPointOrientation` = (0, 0, +-1) (or absent, the TPS default) for every
source:

```
python scripts/formalism_check.py "$GT_DATA_ROOT/<PT-CT1mm>" "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>" --rtplan "$GT_DATA_ROOT/<PT-RT RTPLAN>"
```
adds the variant `b6_axes_rtplan` (ratio binned by the RTPLAN orientation; flat if
the orientations are what MIM used) and fills `pca_vs_rtplan_deg` in
`formalism_axis_comparison.csv` (`compare_axes(pca_axes, plan.orientations_ras)`;
with a synthetic RTPLAN carrying z orientations the column reads 64.5 deg median and
`b6` reproduces `b5_axes_all_z`). Also check `plan.plan_level["private_creators"]`
for the vendor block that names the dose-calculation settings.

**Consequence for F5 / gamma (coordinator decision).** gtcore @ 32 sources with the
physical (PCA) axes and the TPS dose differ near every seed by a TPS input convention,
not by dosimetry. Two defensible arms: (i) physical axes (gtcore's claim: the dose the
implant delivers under TG-43), (ii) TPS-convention axes (all along S-I; via
`angular_symmetry_test(..., seed_axes=np.tile([0, 0, 1], (32, 1)))` or by passing
those axes to `dose_at_points`), which is the like-for-like implementation-equivalence
check. The paper should state which is shown and why the TPS convention is a
limitation of the reference, not of gtcore. `formalism.fit_sources_to_reference(...,
fit_position=True, fit_axis=False)` gives sub-voxel source positions (0.77 mm median
shift from the maxima) if the gamma arm wants them; nothing in gtcore was changed.

**Rev 3 text to amend.** "a 1D plan would give 1.29 -> 0.96" holds for the far field
(> 6 mm from an isolated seed); under the test's own selection on this implant the
1D prediction is 0.95 -> 1.05 (Table A). The rev 3 conclusion (2D) stands, on the
scatter and axis evidence above.

### Table 3 row (proposed text)

| Row | Value | Set |
|---|---|---|
| TPS formalism check (PT-RT): RTDOSE / gtcore-2D vs angle to the seed axis, 3-5 mm single-seed voxels | physical (PCA) axes: 0.93 (0-15 deg) -> 1.10 (75-90 deg), RMS 0.17; TPS-convention axes (all along S-I): 1.03 -> 1.00, RMS 0.04; 1D rejected (RMS 0.10). MIM = 2D line source; TPS seed axes along S-I, 64.5 deg (median) from the physical axes | calibration (this case) |

### Commands

```
python scripts/formalism_check.py "$GT_DATA_ROOT/<PT-CT1mm>" "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>"     # 157 s; writes docs/figures/data/formalism_*.csv
GTCORE_STRICT=1 python -m pytest -q tests/test_formalism.py                                                                                # 25 passed
GTCORE_STRICT=1 python -m pytest -q                                                                                                        # full suite: see below
```
