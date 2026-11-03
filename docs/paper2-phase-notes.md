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

## 2026-11-03 — Phase 3 — gamma (pymedphys) (task `gamma`, branch `p2/gamma`)

Owner: agent `gamma`. Branched from `paper2/integration` at `e13a4af`. Commits:
`8495464` (`gtcore/dose/gamma.py`, export in `gtcore/dose/__init__.py`,
`tests/test_gamma.py`), `ab85b1c` (`scripts/validation_gamma.py`), `90b7f47`
(`docs/figures/data/gamma_*.csv` + slice NPZs, `git add -f`), then this notes
section. Every real-data number below is **calibration-set** (one clinical
case, PT-CT1mm + PT-RT; a reference, not a validation cohort). The real run
was made at `ab85b1c` (clean tree); the CSV headers carry that hash.

### Method (Methods-quotable)

Dose agreement with the treatment-planning system was quantified with the
three-dimensional gamma index (Low et al., Med Phys 1998;25:656) as
implemented in PyMedPhys 0.41.0 (`pymedphys.gamma`; Biggs et al., J Open
Source Softw 2022;7:4555; shell-search algorithm of Wendling et al., Med Phys
2007;34:1647). The TPS dose grid (RTDOSE, 1 × 1 × 1.667 mm, read in cGy) was
the *reference*, restricted to the bounding box of its voxels ≥ 30 Gy plus a
5 mm margin (47 × 78 × 77 voxels); the *evaluation* distribution was gtcore's
TG-43U1S2 two-dimensional line-source dose from the 32 recovered seeds at the
nominal 3.5 U per seed, computed analytically on a regular 0.5 mm lattice
covering the same box plus the search margin (172 × 173 × 171 = 5.09 × 10⁶
points), which PyMedPhys interpolates trilinearly along search shells spaced
at one tenth of the distance criterion (`interp_fraction = 10`). Criteria were
3 %/2 mm (primary) and 2 %/1 mm, each with global-to-prescription
normalisation (dose difference in percent of 60 Gy) and with local
normalisation (percent of the reference voxel's own dose); the search was
bounded at γ = 2 (`max_gamma`; reported γ values are capped there, pass rates
are unaffected). The 30 Gy lower cutoff was passed as 50 % of the 60 Gy
normalisation dose and verified to select exactly the 89 639 reference voxels
≥ 30 Gy. Pass rates (γ ≤ 1) are reported over the 30–200 Gy window
(81 527 voxels, primary) and over all voxels ≥ 30 Gy; because the gamma of a
reference voxel depends only on the evaluation distribution, the 200 Gy upper
cutoff is an exact post-hoc selection of reference voxels (checked against a
pre-filtered run in `tests/test_gamma.py`). Exact call, as recorded in
`GammaResult.settings["pymedphys_call"]` (reference axes `(z, y, x)` of the
RTDOSE box, evaluation axes of the 0.5 mm lattice, both in mm, doses in cGy):

```
pymedphys.gamma(axes_reference, dose_reference_cgy, axes_evaluation, dose_evaluation_cgy,
                dose_percent_threshold=3, distance_mm_threshold=2,      # or 2, 1
                lower_percent_dose_cutoff=50, interp_fraction=10, max_gamma=2,
                local_gamma=False,                                        # True for local gamma
                global_normalisation=6000, skip_once_passed=False, random_subset=None,
                interp_algo='pymedphys')                                  # numba backend; 'scipy' fallback
```

Source positions for the evaluation were (i) the 32 seeds gtcore recovers
from the thin-cut CT (`gtcore.pipeline.reconstruct`; engine equivalence plus
localisation error), (ii) the TPS's own seed positions — the 30 `Seeds`
contour centroids of the RTSTRUCT plus the two uncontoured sources taken from
the RTDOSE maxima (`tps_contours`; engine equivalence with the TPS's
positions), and (iii) the 32 local maxima of the RTDOSE (> 200 Gy, merged
within 3 mm, at the centres of the 1 × 1 × 1.667 mm dose voxels;
`tps_maxima`, the requirements' "TPS sources"). Seed axes for (ii) and (iii)
are those of the one-to-one matched gtcore seed (Hungarian assignment, gate
3 mm; all 32 matched).

### What was added

| File | Content |
|---|---|
| `gtcore/dose/gamma.py` | `gamma_analysis(reference, seed_centers, seed_axes, sk_u=3.5, dose_pct=3, dist_mm=2, lower_cutoff_cgy=3000, upper_cutoff_cgy=20000, local=False, global_norm_cgy=6000, eval_spacing_mm=0.5, interp_algo="auto", *, evaluation=None, max_gamma=2.0, box_margin_mm=5.0, interp_fraction=10, engine=None, exact=True, elapsed_hours=None, ram_available=None) -> GammaResult`; `GammaResult` carries `gamma` (box, NaN below the cutoff), `box_affine`, `box_slices`, `reference_box`, `evaluation` (the lattice dose), `mask_computed` (≥ lower), `mask_used` (lower..upper), `pass_rate`, `pass_rate_ge30`, `n_points`, `n_points_ge30`, `histogram` (edges 0–2 in 0.1 steps + a `>= 2` bin, counts for both windows), `difference_volume` (gtcore − RTDOSE at the reference voxel centres, cGy), `runtime_s`, `settings` (every parameter, the pymedphys version and call, the backend used, git commit), plus `pass_rate_in(lower, upper)` and `gamma_stats(mask)`. Helpers: `prepare_evaluation` (reference box + lattice dose, reusable across criteria), `reference_box`, `evaluation_lattice`, `grid_axes`, `run_pymedphys_gamma` (the one place pymedphys is called; asserts the percent cutoff reproduces `reference >= lower_cutoff_cgy`), `resolve_interp_algo` (`"auto"` → `"pymedphys"` when `numba` imports, else `"scipy"`; an `ImportError` from the numba path also falls back), `pass_rate`, `gamma_histogram`. pymedphys is imported lazily, so `import gtcore.dose` does not need it. |
| `gtcore/dose/__init__.py` | exports `gamma_analysis`, `GammaResult`, `prepare_evaluation`, `Evaluation` (one import line + `__all__`; shared file, minimal edit). |
| `tests/test_gamma.py` | 12 tests, 5 s: synthetic 3-seed cloud, reference computed with gtcore on a 1 × 1 × 1.667 mm grid with a *descending* x axis (RTDOSE-like). Identical dose → 100 % over 30–200 Gy (0.9986 over ≥ 30 Gy: the capsule voxels fail even for identical seeds); 1.5 mm shift of one seed → < 100 % with every failing 3 %/2 mm voxel nearest the shifted seed (2 %/1 mm: < 97 %, > 50 % nearest it); window excludes exactly the > 200 Gy voxels (count equality, mask identities, `pass_rate_in` identities); local vs global ordering voxel-by-voxel (see below); `interp_algo="scipy"` same pass rate within 0.5 pp (same γ to 1e-6); post-hoc upper cutoff equals a run with the > 200 Gy voxels NaN'd out of the reference (`np.array_equal`); prepared-evaluation reuse and margin check; lattice contains the reference voxel centres in x/y; box margin arithmetic; non-axis-aligned grid raises; `"auto"` → `"scipy"` when `numba` is absent (monkeypatched); histogram / pass-rate helpers. |
| `scripts/validation_gamma.py` | the real run (below); writes `docs/figures/data/gamma_pass_rates.csv` (24 rows: 3 seed sets × 2 criteria × 2 normalisations × 2 windows), `gamma_pass_by_band.csv` (pass rate per reference dose band), `gamma_histogram_<set>_<pct>pct_<mm>mm_<norm>.csv` (12 files), `gamma_map_slice_<set>_3pct_2mm_<norm>.npz` (6 files, one axial slice — the k with most 30–200 Gy voxels, k = 23 of the box — with γ, RTDOSE, gtcore dose, masks, mm axes, settings; the optional Supplement panel), `gamma_capsule_diagnostics.csv`, `gamma_seed_sets.csv`; full 3-D boxes to `output/gamma/` (git-ignored). Header comments: script, placeholder command, commit, dataset ids, flag, date. |

### Real run — PT-CT1mm + PT-RT, commit `ab85b1c`

```
export GT_DATA_ROOT=...; export PYTHONPATH=<worktree>
python scripts/validation_gamma.py "$GT_DATA_ROOT/<PT-CT1mm>" "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>"
#   prints gtcore.__file__ = <worktree>\gtcore\__init__.py, commit ab85b1c (clean), pymedphys 0.41.0, interp_algo pymedphys
```

Inputs: CT 204 × 512 × 512 at 0.52 × 0.52 × 1.0 mm; RTDOSE 250 × 266 × 266
at 1 × 1 × 1.667 mm, `PLAN`, max 813 Gy; `check_references` clean.
`reconstruct` → 32 seeds; 32 RTDOSE maxima > 200 Gy; 30 `Seeds` contours.
Reference box 47 × 78 × 77 voxels (89 639 ≥ 30 Gy, 81 527 in 30–200 Gy,
8112 > 200 Gy); lattice 172 × 173 × 171 (5 088 276 points, 0.5 mm).

**Pass rates (γ ≤ 1, %), S_K nominal 3.5 U, 32 sources**

| seed set | criteria | normalisation | 30–200 Gy (primary) | ≥ 30 Gy | γ mean (30–200) | γ p95 |
|---|---|---|---|---|---|---|
| gtcore seeds | 3 %/2 mm | global-to-rx | **99.33** | 98.71 | 0.249 | 0.50 |
| gtcore seeds | 3 %/2 mm | local | **99.37** | 98.92 | 0.250 | 0.50 |
| gtcore seeds | 2 %/1 mm | global-to-rx | **96.96** | 95.21 | 0.429 | 0.86 |
| gtcore seeds | 2 %/1 mm | local | **96.23** | 94.78 | 0.437 | 0.88 |
| TPS contours (+2 maxima) | 3 %/2 mm | global-to-rx | 99.33 | 98.71 | 0.248 | 0.50 |
| TPS contours (+2 maxima) | 3 %/2 mm | local | 99.37 | 98.90 | 0.249 | 0.50 |
| TPS contours (+2 maxima) | 2 %/1 mm | global-to-rx | 97.20 | 95.52 | 0.426 | 0.85 |
| TPS contours (+2 maxima) | 2 %/1 mm | local | 96.57 | 95.16 | 0.433 | 0.87 |
| RTDOSE maxima | 3 %/2 mm | global-to-rx | 99.31 | 98.65 | 0.223 | 0.47 |
| RTDOSE maxima | 3 %/2 mm | local | 99.35 | 98.85 | 0.224 | 0.47 |
| RTDOSE maxima | 2 %/1 mm | global-to-rx | 98.13 | 96.21 | 0.375 | 0.78 |
| RTDOSE maxima | 2 %/1 mm | local | 97.69 | 96.02 | 0.382 | 0.80 |

(n = 81 527 / 89 639 in every row; `n_unresolved` = 0 everywhere; γ p95 from
`gamma_pass_rates.csv`.) Reading: at 3 %/2 mm the three source sets are
indistinguishable (99.31–99.37 %); at 2 %/1 mm the gtcore seeds cost
0.2–0.3 pp against the TPS's own contour positions (96.96 vs 97.20 % global,
96.23 vs 96.57 % local) — the localisation error (median 0.15 mm vs the
contours) is a minor term; the remaining ~3 % of failing voxels at 2 %/1 mm
are engine/grid differences present with the TPS's own positions too
(anisotropy dataset, TPS grid sampling — the formalism task's ±6 % angular
trend). The RTDOSE-maxima set scores highest at 2 %/1 mm although its
positions are 0.92 mm (median) from both the gtcore seeds and the contours:
it places gtcore's sources where the TPS dose actually peaks on its grid, so
it reproduces the TPS near-seed field rather than the seeds' physical
positions (see the seed-set table). **Recommendation for Table 3 / the F5
caption:** quote the gtcore-seed rows as the pipeline result and the
TPS-contour rows as the engine-equivalence reference; keep the maxima rows in
the CSV only.

**Pass rate by reference dose band** (gtcore seeds; post-hoc windows,
`gamma_pass_by_band.csv`):

| band (Gy) | n | 3 %/2 mm global | 3 %/2 mm local | 2 %/1 mm global | 2 %/1 mm local |
|---|---|---|---|---|---|
| 30–60 | 48 201 | 100.0 | 100.0 | 97.40 | 95.98 |
| 60–100 | 18 401 | 100.0 | 100.0 | 99.46 | 99.54 |
| 100–200 | 14 925 | 96.31 | 96.56 | 92.44 | 92.96 |
| 200–400 | 7 247 | 91.68 | 93.74 | 76.21 | 78.79 |
| > 400 | 865 | 99.65 | 100.0 | 89.94 | 91.45 |

The failures concentrate in the 100–400 Gy zone between the seeds of a tile
(where superposition of a tile's four seeds and the neighbouring tiles gives a
flat high dose the TPS grid renders differently), not only above 200 Gy;
voxels > 400 Gy pass by distance-to-agreement (the gradient is steep). Below
60 Gy nothing fails at 3 %/2 mm; at 2 %/1 mm 2.6 % (global) / 4.0 % (local)
of the 30–60 Gy voxels fail — the periphery, where the local criterion is the
stricter one (2 % of 30–60 Gy < 2 % of 60 Gy).

**The > 200 Gy voxels and the upper cutoff** (`gamma_capsule_diagnostics.csv`):
8112 of 89 639 reference voxels ≥ 30 Gy (**9.05 %**) exceed 200 Gy. Their
distance to the nearest gtcore seed: **median 3.82 mm, p95 6.05 mm, max
7.81 mm; 26 % within 3 mm, 55 % within 4 mm, 95 % within 6 mm** (the same
within 0.1 mm against the TPS contour positions). The rev-3 wording "within
~3 mm of a seed" is therefore **not confirmed**: the > 200 Gy region is the
whole seed plane of each tile (seeds 10 mm apart, tiles adjacent), and it
reaches 6 mm from the nearest seed. What *is* confirmed is the grid effect it
was meant to express: at the hottest RTDOSE voxel the TPS reports 813 Gy
where gtcore's point dose at that voxel centre is 2191 Gy (gtcore's maximum
over the box's voxel centres 2406 Gy; rev 3 quoted 2399 Gy) — the TPS value
is far below the point dose, consistent with volume-averaged or otherwise
regularised near-seed dose on the 1 × 1 × 1.667 mm grid. Suggested wording:
"voxels above 200 Gy (9 % of the voxels ≥ 30 Gy; the tiles' seed planes,
median 3.8 mm and 95 % within 6 mm of the nearest seed), where the TPS grid
value is far below the point dose, are excluded from the primary window and
reported alongside".

**Seed sets** (`gamma_seed_sets.csv`; Hungarian one-to-one, gate 3 mm):

| set vs set | matched | median | p95 | max | per-axis RMS x/y/z (mm) |
|---|---|---|---|---|---|
| gtcore seeds vs `Seeds` contours | 30/30 | 0.15 | 0.73 | 0.89 | 0.25 / 0.12 / 0.17 |
| RTDOSE maxima vs gtcore seeds | 32/32 | 0.92 | 1.37 | 1.45 | 0.56 / 0.53 / 0.56 |
| RTDOSE maxima vs `Seeds` contours | 30/30 | 0.85 | 1.39 | 1.51 | 0.60 / 0.50 / 0.54 |
| maxima of *gtcore's own* dose on the RTDOSE grid vs gtcore seeds | 32/32 | 0.76 | 1.35 | 1.57 | 0.53 / 0.44 / 0.49 |

The last row is the control: finding the maxima of a *known* dose on the
1 × 1 × 1.667 mm grid already misplaces the sources by 0.76 mm (median) —
a line-source maximum on a coarse grid lands in a transverse neighbour
voxel — so RTDOSE maxima are grid-limited source estimates, not the plan's
positions. A one-axis parabolic apex refinement of 1/D (tried in a scratch
driver, not committed) only brought the median to 0.77 mm vs gtcore seeds,
so it was dropped. The RTPLAN (absent on this machine) remains the only
authoritative source of the TPS positions; when it arrives, add a
`tps_rtplan` seed set to the script (`load_rtplan(...).sources`).

**Comparison with the rev-3 mock numbers** ("own implementation,
global-to-rx, 32 sources": 99.2 % at 3 %/2 mm and 88.1 % at 2 %/1 mm over
30–200 Gy; 97.4 / 83.1 % over ≥ 30 Gy). pymedphys with gtcore seeds, global:
**99.33 / 96.96 % and 98.71 / 95.21 %**. The 3 %/2 mm figure agrees to
0.1 pp; the 2 %/1 mm figure is 9 pp higher and the ≥ 30 Gy figures 1.3 /
12 pp higher. The mock code is not available, so the causes cannot be
measured, but the pattern points at the distance-to-agreement search: a 1 mm
DTA evaluated on (or near) the 1 × 1 × 1.667 mm grid cannot shift by less
than a voxel, so almost no voxel gets DTA credit at 2 %/1 mm and the capsule
voxels (which pass only by DTA) fail wholesale — exactly where the mock loses
most (its ≥ 30 Gy − 30–200 Gy gap is 1.8 / 5.0 pp against 0.6 / 1.8 pp
here). pymedphys interpolates the 0.5 mm lattice along shells at 0.1 mm
steps for the 1 mm criterion (0.2 mm for 2 mm), the resolution Wendling et al.
recommend. A different source set or S_K in the mock cannot be excluded. The
pymedphys numbers supersede the mock.

**Local vs global — direction.** The requirements/task text says
"global-to-rx is lenient near seeds". Measured and provable, it is the
reverse: the local criterion allows 3 % of the *voxel's own* dose, which
above rx exceeds 3 % of 60 Gy, so voxel by voxel γ_local ≤ γ_global wherever
the reference is ≥ 60 Gy and γ_global ≤ γ_local below it
(`tests/test_gamma.py::test_local_and_global_differ_in_the_expected_direction`
pins both inequalities). On the real case the aggregate therefore depends on
where the failures sit: at 3 %/2 mm they are near the seeds and local is the
more lenient (99.37 vs 99.33 %; 98.92 vs 98.71 % over ≥ 30 Gy), at 2 %/1 mm
the periphery contributes and global is the more lenient (96.96 vs 96.23 %).
Suggested §5 wording: "global-to-rx normalisation is the stricter criterion
above the prescription dose and the more lenient one below it; both are
reported".

**Runtime** (AMD Ryzen 5 7600X, 6 cores / 12 threads, 31 GB RAM; other agents
ran concurrently — upper bounds): `reconstruct` on the 204-slice CT 23.3 s;
per seed set, evaluation (5 088 276 lattice points + 282 282 box centres,
32 seeds, exact 2D rate, single-threaded numpy) 40–52 s; `pymedphys.gamma`
on the 89 639 reference voxels with the numba backend 3.2–4.5 s at 3 %/2 mm
and 7.8–10.5 s at 2 %/1 mm (per-row values in `gamma_pass_rates.csv`).
Whole script (three seed sets, twelve gamma runs, all outputs) about 4 min 15 s wall (from the output-file timestamps; the first, dirty-tree run at `8495464` gave the same pass rates with evaluation 50–64 s and gamma 3.8–4.9 / 8.8–12.6 s).
Numba vs scipy backend on the real case (scratch driver, gtcore seeds,
global, run while the full suite was executing): identical pass rates
(99.3254 / 98.7104 % and 96.9568 / 95.2119 %), max |γ_numba − γ_scipy| over
the 89 639 computed voxels 7.9e-14 (3 %/2 mm) and 1.2e-13 (2 %/1 mm);
`pymedphys.gamma` 3.7 s (numba) vs 13.8 s (scipy) at 3 %/2 mm and 10.0 vs
37.6 s at 2 %/1 mm. The scipy fallback is therefore a complete substitute
(≈ 3.7× slower, still well under a minute per criterion), which settles the
Phase 1 packaging question: numba can stay optional — if its `numpy < 2.6`
cap bites, drop it from the `clinical` extra and the wrapper's `"auto"`
picks scipy with no change in results.

### Tests

```
GTCORE_STRICT=1 python -m pytest -q tests/test_gamma.py     # 12 passed in 4.8 s
GTCORE_STRICT=1 python -m pytest -q                          # 953 passed, 14 skipped, 15242 warnings in 639.48s (0:10:39) at 90b7f47 (+12 vs paper2/integration e13a4af; other agents and the scipy driver ran concurrently, wall time is an upper bound)
```

### Notes for the coordinator

- Shared file touched: `gtcore/dose/__init__.py` (one import line, four
  `__all__` entries, one docstring sentence). `pyproject.toml`, `conftest.py`,
  `README.md` untouched (the `clinical` extra from Phase 1 already lists
  `pymedphys>=0.41` and `numba>=0.60`).
- `docs/figures/data/` is git-ignored by the `data/` pattern; the CSVs and
  the six slice NPZs (≈ 90 kB each) were added with `git add -f`. The full
  3-D boxes (`output/gamma/`) are regenerable and not committed.
- Table 3 / F5 caption: recommend the gtcore-seed rows (99.3 % at 3 %/2 mm
  and 97.0 % at 2 %/1 mm global over 30–200 Gy; 98.7 / 95.2 % over ≥ 30 Gy;
  local 99.4 / 96.2 and 98.9 / 94.8 %) with the TPS-contour rows as the
  engine-equivalence reference. The "within ~3 mm" clause and the
  "global-to-rx is lenient near seeds" clause in rev 3 need the corrections
  above.
- `clinical-rebuild` may import `gtcore.dose.gamma.gamma_analysis` /
  `prepare_evaluation` (reuse one `Evaluation` per seed set across criteria;
  it is the 50–60 s step) or read `docs/figures/data/gamma_pass_rates.csv`.
- Nothing installed; no parameter changed; no data missing for this task
  (the RTPLAN would add the authoritative TPS source set).
