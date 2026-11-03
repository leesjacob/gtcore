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

## 2026-11-03 — Phase 3 — MC uncertainty (seed-position error → D90/V100) (task `uncertainty`, branch `p2/uncertainty`)

Requirements rev 3 §3 F5 "ADD `gtcore/dose/uncertainty.py`", §6 Phase 3 last
checkbox; Phase 3 plan row `uncertainty`; figure data designation v7 (F5(b)
DVH with the MC envelope is the paper's one chart; Table 3 "MC D90 ±").
Branched from `paper2/integration` at `e13a4af`. Code commit `c557cf4`
(module, tests, script); the CSVs below were generated at `c557cf4` and
committed at `1104fcb`; this section and a docstring runtime correction
follow in the next commit. Hardware as in
AGENT_COMMON; the full suite and other agents ran concurrently, so every
wall time is an upper bound. **Calibration set: yes** — the one clinical
case (PT-CT1mm + PT-RT), reference not validation cohort; there is no
held-out scan.

### Method (Methods-quotable)

The uncertainty of every gtcore dose statistic on the physician's HR-CTV
was obtained by Monte-Carlo propagation of the seed-position error. The
nominal dose was evaluated with the TG-43U1 2D line-source engine at the
centres of the 70 438 CT voxels (0.52 × 0.52 × 1.0 mm) of the `CTV_GT`
contour (`HRCTV_PHYS`, 19.0 cc), with the nominal air-kerma strength of
3.5 U for every seed and fixed seed axes. In each of 1000 draws every seed
centre was displaced by an independent 3-vector drawn from an error model,
the dose at the same voxel centres was recomputed with the same engine, and
D90, V100 and V150 (`dose_stats`: type-1 quantile, ≥ rule) and the
cumulative DVH on a 0.5 Gy ladder (0–150 Gy) were recorded. Two error
models were used. The **primary band** resamples, with replacement and
independently per seed, the 30 measured residual vectors between gtcore's
seed centres on the thin-cut CT and the centroids of the TPS `Seeds`
contours (one-to-one Hungarian match within 2 mm; |r| median 0.15 mm, 95th
percentile 0.73 mm, maximum 0.89 mm; per-axis SD 0.25 / 0.11 / 0.16 mm;
mean vector −0.03 / −0.06 / +0.04 mm, kept). The **degraded-export band**
draws independent zero-mean Gaussians per RAS axis with SD 0.51 / 0.40 /
0.63 mm, the per-axis seed-position SD measured between the 2 mm gapped
export and the 1 mm thin-cut of the same acquisition (25 seeds,
`docs/localization-notes.md`; the export is no longer on disk, so the SDs
are quoted, not regenerated). Both bands are **conservative**: each is built
from the difference between two measurements, so it contains the TPS
contouring error (primary) or the thin-cut's own localisation error
(degraded export) in addition to gtcore's. The envelope reported is the
mean and the pointwise 2.5th and 97.5th percentiles across draws, of D90,
V100, V150 and of the volume fraction at every DVH level. Seed axes and S_K
were not perturbed (the residuals are position residuals; S_K is a stated
input). The random generator was seeded (`numpy.random.default_rng(0)`);
the thread count does not change a bit of the result. The refinement
covariances (`SeedCandidates.cov_ras`, default-off) are available as a
secondary check but were not present on this run and are never the band.

### Implementation

- `gtcore/dose/uncertainty.py` (new): `ErrorModel` base; `NoError`,
  `AxisGaussian(sd_mm)`, `ResidualResample(residuals, center=False)`
  (+ `from_csv`), `Covariance(cov_ras)`; `DEGRADED_EXPORT_SD_MM =
  (0.51, 0.40, 0.63)`; `dvh_curve(doses, levels, weights=None)` (≥ rule;
  equals `dose_stats` V bit for bit with unit weights); `propagate(target,
  seed_centers, seed_axes, model, n_draws=1000, seed=0, sk_u=3.5,
  rx_cgy=6000, levels_cgy=np.arange(0, 15001, 50), engine=None,
  n_workers=1, exact=True) -> MCResult`; `MCResult` carries the per-draw
  `d90` / `v100` / `v150`, `summary(key)` (nominal, mean, sd, p2.5, p97.5,
  min, max), the DVH envelope (`dvh_nominal`, `dvh_mean`, `dvh_p2_5`,
  `dvh_p97_5`), `nominal` (`dose_stats` dict), `model` text, `perturbation_mm`
  (what was actually drawn), `runtime_s`, and `stat_rows` / `dvh_rows` for
  the CSVs. Per draw, seed *s* displaced by δ_s is evaluated as the
  undisplaced seed seen from the points shifted by −δ_s
  (`engine._sum_seed_rates` per seed, the `dose_at_points` code path, summed
  in seed order), so a zero perturbation reproduces `target_doses_exact`
  bit for bit; draws are spread over threads when `n_workers > 1`.
- `gtcore/dose/__init__.py`: exports the above (one import block +
  `__all__` entries; shared file, minimal edit).
- `scripts/validation_error_propagation.py`: the driver below.
- `tests/test_uncertainty.py` (19 tests): zero-error models (`NoError`,
  `AxisGaussian(0)`, `ResidualResample([0,0,0])`, `Covariance(0)`) reproduce
  the nominal D90 / V100 / V150 / DVH exactly, also with unnormalised axes;
  `dvh_curve` is the ≥ rule and equals V100 / V150 / V200 at rx; tiny-SD
  Gaussian → band width ≤ 0.5 cGy and ≤ 1e-3 volume fraction, width grows
  monotonically with SD; isotropic 1 mm Gaussian on a synthetic 3-seed
  target → finite spread whose 95 % interval contains the nominal D90, V100,
  V150 and the whole nominal DVH curve, envelope monotone; same seed →
  identical arrays with 1 or 3 threads, different seed → different;
  `ResidualResample` of one vector = rigid shift of the implant (rtol 1e-9),
  draws only measured vectors, CSV round trip; `Covariance` draws have the
  requested covariance (20 000 draws, atol 0.02 mm²), diagonal covariance ≈
  `AxisGaussian`, bad input raises; weighted S5-like point target; bad
  inputs raise; CSV row helpers; tabulated kernel within 1e-3 of exact.

### Real data — PT-CT1mm + PT-RT at `c557cf4`

```
# from the worktree root; gtcore.__file__ printed = <worktree>\gtcore\__init__.py
PYTHONPATH=. GTCORE_STRICT=1 python scripts/validation_error_propagation.py "$GT_DATA_ROOT/<PT-CT1mm>" \
    "$GT_DATA_ROOT/<PT-RT RTSTRUCT>" "$GT_DATA_ROOT/<PT-RT RTDOSE>" \
    --n-draws 1000 --seed 0 --workers 4 --seed-sets gtcore32,contoured30,gtcore30
# -> docs/figures/data/f5b_dvh_band.csv, uncertainty_d90_v100.csv, clinical_seed_residuals.csv (git add -f)
```

Pipeline: `reconstruct` 32 seeds in 17 s (Phase 1 vault filter),
`seeds.cov_ras` absent (default `refine_seeds=None`) → covariance check
skipped. Target `HRCTV_PHYS` = `RTSTRUCT:CTV_GT` on the CT grid, 19.01 cc,
70 438 voxels. Seed match: **30/30** contoured seeds matched one-to-one at
2 mm; residual (detection − contour centroid, RAS) |r| median 0.15, p95 0.73,
max 0.89 mm; mean (−0.025, −0.057, +0.037) mm; per-axis SD (0.25, 0.11,
0.16) mm — the rev 3 text's "0.23 mm median, 0.9 mm max" predates the Phase 1
vault filter and the Phase 2 rasteriser; the Phase 2 `rt-io` notes already
give 0.15 / 0.73 / 0.89. RTDOSE on the same target (trilinear at the CT voxel
centres): D90 6365 cGy, V100 0.937, V150 0.482 (as in Phase 2).

Seed sets: `gtcore32` = the 32 gtcore detections (PCA axes); `contoured30`
= the 30 TPS `Seeds` contour centroids (PCA axes; the plan has 32 sources,
the ROI omits two); `gtcore30` = the 30 detections matched to them (added
because it costs 3 minutes and separates the two-missing-seeds effect from
the localisation effect: 5800 vs 5790 cGy nominal).

| seed set | band | D90 nominal [cGy] | D90 mean ± SD | D90 95 % [p2.5, p97.5] | V100 nominal | V100 mean | V100 95 % | V150 nominal | V150 95 % |
|---|---|---|---|---|---|---|---|---|---|
| `gtcore32` | **residual (primary)** | **6167** | 6175 ± 19 | **[6133, 6212]** | **0.917** | 0.918 | **[0.914, 0.921]** | 0.468 | [0.467, 0.473] |
| `gtcore32` | degraded export | 6167 | 6158 ± 49 | [6068, 6255] | 0.917 | 0.916 | [0.907, 0.925] | 0.468 | [0.458, 0.480] |
| `contoured30` | residual (primary) | 5790 | 5797 ± 16 | [5761, 5828] | 0.873 | 0.874 | [0.870, 0.879] | 0.377 | [0.376, 0.383] |
| `contoured30` | degraded export | 5790 | 5777 ± 43 | [5691, 5860] | 0.873 | 0.872 | [0.861, 0.882] | 0.377 | [0.369, 0.390] |
| `gtcore30` | residual (primary) | 5800 | 5803 ± 17 | [5767, 5835] | 0.875 | 0.876 | [0.872, 0.880] | 0.381 | [0.379, 0.386] |
| `gtcore30` | degraded export | 5800 | 5784 ± 44 | [5696, 5872] | 0.875 | 0.874 | [0.863, 0.884] | 0.381 | [0.372, 0.393] |
| TPS (RTDOSE) | — | 6365 | — | — | 0.937 | — | — | 0.482 | — |

Perturbations actually drawn (1000 × 32): primary |δ| median 0.15, p95 0.88,
max 0.89 mm, per-axis SD 0.25 / 0.11 / 0.16 mm; degraded export median
0.79, p95 1.49, max 3.14 mm, SD 0.51 / 0.40 / 0.63 mm.

DVH envelope on `gtcore32` (volume fraction ≥ level; nominal / [p2.5,
p97.5]): 45 Gy 0.994 / primary [0.993, 0.994], degraded [0.991, 0.996];
60 Gy 0.917 / [0.914, 0.921], [0.907, 0.925]; 75 Gy 0.706 / [0.703, 0.712],
[0.693, 0.716]; 90 Gy 0.468 / [0.467, 0.473], [0.458, 0.480]; 120 Gy
0.189 / [0.186, 0.194], [0.180, 0.201]. Below 30 Gy every curve is 1.
(`f5b_dvh_band.csv` also carries the RTDOSE curve on the same ladder as
`band = rtdose, seed_set = tps`, nominal only, for the figure's reference
line; the `clinical-rebuild` CSVs remain the source for the TPS / gtcore
curves themselves.)

**Reading.** On `HRCTV_PHYS` the measured localisation residuals move D90 by
±40 cGy (95 %, −0.6 / +0.7 % of the nominal) and V100 by ±0.4 percentage
points; the degraded-export model, three to four times wider in position,
gives ±95 cGy (±1.5 %) and ±0.9 points. Both bands are conservative (above).
The gap between the RTDOSE and gtcore at the 32 detected seeds (6365 vs
6167 cGy, +3.2 %; V100 0.937 vs 0.917) lies outside both envelopes, so
seed-position error does not account for it; it belongs to the
S_K / formalism / anisotropy comparison (`formalism`, `clinical-rebuild`:
implied S_K 3.76 U at this tree). The band means sit within 0.4 SD of the
nominal (primary +8 cGy, degraded −10 to −16 cGy): random displacement of a
wall-hugging implant is slightly asymmetric in dose, nothing more. The two
30-seed sets agree to 10 cGy nominally and have the same band widths, so the
377 cGy drop from 32 to 30 seeds is the two uncontoured sources, not
localisation.

Runtime (4 threads, concurrent load): 88–106 s per 1000-draw band on the
70 438-voxel target (≈ 0.1 s per draw), 623 s for the whole script (two
engine warm-ups, three seed sets × two bands, one `reconstruct`). Serial
cost measured on a synthetic 19 k-point × 32-seed target: 0.10 s per draw
exact, 0.11 s tabulated; threads 2 / 4 / 6 → ×2.0 / ×3.1 / ×3.7, results
bit-identical. The target grid was **not** subsampled.

### Tests

```
GTCORE_STRICT=1 python -m pytest -q tests/test_uncertainty.py        # 19 passed in 2.4 s
GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider              # full suite, see the line below
```

Full suite at `c557cf4` (tree = `paper2/integration` e13a4af + this branch; conftest sets
`GTCORE_STRICT=1`; run concurrently with the 1000-draw real-data script and other agents, so
the wall time is an upper bound): **960 passed, 14 skipped**, 15242 warnings in 856.84 s
(0:14:16). Baseline at e13a4af: 941 passed, 14 skipped; the 19 new tests are
`tests/test_uncertainty.py`. The 14 skips are the Phase 1 set (interface placeholders + the
`GT_E2E`-gated run).

### Notes for the coordinator

- `docs/figures/data/clinical_seed_residuals.csv` (30 rows, `seed_id, dx_mm,
  dy_mm, dz_mm, dist_mm`) is **owned by `clinical-rebuild`**; it did not
  exist, so this script wrote it with the matching code of
  `validation_clinical.print_seed_match_detail` (Hungarian, 2 mm gate) and a
  header saying it is a stand-in. If `clinical-rebuild` commits its own
  version, keep theirs and re-run this script (it reads the file when
  present; `--rebuild-residuals` overwrites).
- Shared file touched: `gtcore/dose/__init__.py` (one import block, nine
  `__all__` names). `gamma` / `formalism` will edit the same file.
- The covariance check (`Covariance(res.seeds.cov_ras)`) runs automatically
  when `reconstruct(..., refine_seeds=...)` supplies `cov_ras`; the default
  pipeline does not, so no covariance row exists. It is never the band.
- `gtcore30` is a third seed set beyond the task's two; drop its rows if
  F5(b) / Table 3 only want `gtcore32` and `contoured30`.
- Which "gtcore@30" the F5(b) legend means (TPS-contoured positions, or the
  30 matched detections) is a wording decision: both are in the CSV
  (`contoured30`, `gtcore30`), 10 cGy apart.

## 2026-11-03 — Phase 3 — clinical validation rebuilt on Phase 2 (task `clinical-rebuild`, branch `p2/clinical-rebuild`)

Requirements rev 3 §3 F5 / F6, §6 Phase 3 first checkbox; PHASE3_PLAN row
`clinical-rebuild`. Branched from `paper2/integration` at `e13a4af`. Code
commit **`fe0dc81`** (`scripts/validation_clinical.py` rewritten,
`tests/test_validation_clinical_smoke.py` new); the panel data under
`docs/figures/data/clinical_*` and this section are the follow-up commit
(`git log -1 -- docs/figures/data/clinical_table3_rows.csv`). Every real-data
number below is **calibration-set** (one clinical case, no held-out scan;
`docs/paper2-decisions.md`). S_K is 3.5 U nominal in every gtcore cell and
is never fitted; rx 6000 cGy. Hardware as in the Phase 1 notes; other
agents ran concurrently, so wall times are upper bounds.

### What the script is now

`scripts/validation_clinical.py` is the single source of the clinical rows
of Table 3 and of the F4(a), F5(a), F5(b), F6 panel data. It consumes only
Phase 2 interfaces: `gtcore.io.rt` (`load_rtstruct`, `load_rtdose`,
`rasterize_rois`, `check_references`, `verify_against_tps`,
`reference_from_dvh_sequence`, `parse_mim_dvh_export`),
`gtcore.dose.targets` (`hrctv_phys`, `rind5`, `implant_reach`, `dice`,
`target_doses_exact`, `target_doses_from_grid`),
`gtcore.dose.metrics.dose_stats` and `gtcore.pipeline.reconstruct`. Nothing
is tuned; the old inline rasteriser, the `build_hrctv` call and the
`hrctv_stats` calls are gone.

```
python scripts/validation_clinical.py "$GT_DATA_ROOT/<PT-CT1mm dir>" "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>" --rx 6000 --out docs/figures/data
#   [--mim-dvh <MIM DVH export>]  [--datasets PT-CT1mm,PT-RT]
# run from the worktree root with PYTHONPATH=<worktree>; the first output line is gtcore.__file__, the second the commit
```

Definitions used (Methods-quotable; all from the Phase 2 notes):

- **Dose columns.** *RTDOSE*: the clinical dose grid (1 x 1 x 1.667 mm, GY to
  cGy, `DoseSummationType` PLAN, no `DVHSequence`) sampled trilinearly at the
  CT voxel centres of each structure. *gtcore @ 32 TPS sources*: the exact
  TG-43U1S2 engine at the plan's 32 source positions = the 32 RTDOSE local
  maxima > 200 Gy merged at 3 mm; each source takes the PCA long axis of the
  contoured `Seeds` component matched to it one-to-one within 3 mm (30
  sources); the two uncontoured sources take the axis of the gtcore detection
  matched within 3 mm. *gtcore @ 32 gtcore seeds*: the engine at
  `reconstruct`'s 32 detections with their own axes. *gtcore @ 30 contoured
  seeds*: the engine at the 30 `Seeds` contour centroids (26-connected
  components of the rasterised ROI on the CT grid) with their PCA axes. Every
  cell is `dose_stats(doses, 6000, voxel_volume_mm3=target.voxel_volume_mm3)`
  (type-1 quantile, >= rule).
- **Structures (CT grid, 0.52 x 0.52 x 1.0 mm).** `HRCTV_PHYS` = the RTSTRUCT
  `CTV_GT` rasterised with the Phase 2 rule; `RIND5(Cav_Post, clip Brain)`;
  `RIND5(gtcore cavity, clip gtcore cranial interior)`;
  `IMPLANT_REACH(r)` = `RIND5(Cav_Post, Brain)` within r mm of a gtcore seed
  centre, r = 10 mm (the F6 calibration, `docs/paper2-decisions.md`) and
  r = 12 mm as a labelled second line.

### Real run — PT-CT1mm + PT-RT at `fe0dc81` (wall 129 s, suite running concurrently)

Reconstruct: 32 seeds, gtcore cavity 41.3 cc; RTSTRUCT volumes on the CT
grid `Cav_Post` 35.10 cc, `CTV_GT` 19.01 cc, `Brain` 1383.98 cc, `Seeds`
0.36 cc; gtcore cavity vs `Cav_Post` Dice 0.665 (covers 72 % of it, 62 % of
ours inside it). `check_references`: no warnings (RTSTRUCT, CT and RTDOSE
share one frame of reference; the RTSTRUCT references the PT-CT1mm series).

**Seed match table** (`clinical_seed_match.csv`, `clinical_seed_residuals.csv`,
`clinical_source_match.csv`):

| | value |
|---|---|
| contoured `Seeds` components / gtcore detections | 30 / 32 |
| one-to-one match, 2 mm gate | **30/30**; distance median 0.15, p95 0.73, max 0.89 mm |
| residual vectors gtcore - TPS (RAS mm, n = 30) | mean -0.03 / -0.06 / +0.04; SD 0.25 / 0.11 / 0.16; RMS 0.25 / 0.13 / 0.17; norm median 0.15, p95 0.73, max 0.89 |
| plan sources = RTDOSE maxima > 200 Gy merged at 3 mm | **32**; detections matched one-to-one within 3 mm **32/32**, median 0.92, max 1.45 mm; 0 sources without a detection, 0 detections without a source |
| the two uncontoured sources | detections 10 and 17: 0.90 / 0.83 mm from an RTDOSE maximum, 7.90 / 8.74 mm from any contour, inside the cranial interior |
| 32-source model | 30 sources take a contoured seed's axis (maximum-to-centroid distance median 0.85, max 1.51 mm: the 1 x 1 x 1.667 mm dose-grid quantisation of the maxima), 2 take the matched detection's axis |
| detections outside gtcore's cranial interior | 2 of 32 (max 1.9 mm); contoured seeds outside it 2 of 30 (max 2.0 mm); vault filter applied, 1339.7 mL, 21 dropped, margin 4.0 mm |

Identical to the Phase 1 vault-filter acceptance (30/30, 32/32, same
distances). `clinical_seed_residuals.csv` (columns
`seed_id,dx_mm,dy_mm,dz_mm,dist_mm`) is the input of the `uncertainty`
agent's primary band.

**Structures** (volume, Dice vs `CTV_GT`): `HRCTV_PHYS` 19.01 cc;
`RIND5(Cav_Post, Brain)` 35.35 cc / 0.692; `RIND5(gtcore, cranial interior)`
53.82 cc / 0.284; `IMPLANT_REACH(10)` 16.83 cc / **0.881**; `IMPLANT_REACH(12)`
25.29 cc / 0.846 -- the Phase 2 `targets` numbers reproduced exactly.

**Structure x dose matrix** (cell = D90 cGy / V100 % / V150 %; rx 6000 cGy;
S_K 3.5 U nominal; every dose cell flagged *TPS volume check pending*, see
below; `clinical_table3_rows.csv` carries D90 / D50 / Dmin / Dmean / V100 /
V150 / V200 / volume / n / Dice per cell):

| structure (CT grid) | volume | RTDOSE | gtcore @ 32 TPS sources | gtcore @ 32 gtcore seeds | gtcore @ 30 contoured seeds |
|---|---|---|---|---|---|
| `HRCTV_PHYS` (`CTV_GT`) | 19.0 cc | **6365 / 93.7 / 48.2** | 6224 / 92.3 / 47.0 | **6167 / 91.7 / 46.8** | 5790 / 87.3 / 37.7 |
| `RIND5(Cav_Post, clip Brain)` | 35.4 cc | **4646 / 67.8 / 28.1** | 4564 / 67.7 / 27.4 | 4538 / 67.8 / 27.4 | 4350 / 61.1 / 22.0 |
| `RIND5(gtcore cavity, clip cranial interior)` | 53.8 cc | **2725 / 38.3 / 20.0** | 2671 / 38.5 / 19.6 | 2653 / 38.7 / 19.8 | 2528 / 35.4 / 17.7 |
| `IMPLANT_REACH(r = 10 mm)` | 16.8 cc | 6881 / 97.4 / 56.7 | 6776 / 96.5 / 55.2 | 6700 / 96.0 / 54.8 | 6173 / 92.0 / 44.5 |
| `IMPLANT_REACH(r = 12 mm)` (second line) | 25.3 cc | 5859 / 88.0 / 39.3 | 5801 / 87.2 / 38.3 | 5765 / 86.9 / 38.2 | 5404 / 80.2 / 30.8 |

Reading. The sanity anchors hold to the cGy: `CTV_GT` 19.0 cc, RTDOSE D90
6365 / V100 93.7 %, gtcore @ 32 gtcore seeds 6167 cGy (Phase 1: 6168; the
1 cGy is the Phase 2 supersampled boundary voxels), `RIND5(Cav_Post, Brain)`
35.4 cc / 4646, `RIND5(gtcore)` 53.8 cc / 2725, `IMPLANT_REACH(10)` Dice
0.88. Column to column on `CTV_GT`: gtcore at the TPS's own 32 sources is
-2.2 % in D90 against the RTDOSE (6224 vs 6365) and -1.4 pp in V100; the 32
gtcore seeds are -3.1 % / -2.0 pp; dropping the two uncontoured sources
(30-seed column) costs a further -6 % in D90, which is the whole of the
Phase 1 "5790 vs 6365" gap. The same ordering holds on every structure, so
the structure choice moves D90 by a factor 2.3 (6365 to 2725 cGy under the
same RTDOSE) while the dose source moves it by <= 3 % at 32 sources. Matrix
wall 12-22 s (exact engine at ~560 k voxel centres x 3 gtcore columns).

**Diagnostics (printed; in the CSV as `row = diagnostic`; never used in a
table cell):**

- *Implied S_K*: TG-43 (3.50 U, 32 TPS sources) / RTDOSE at `HRCTV_PHYS`
  voxels > 5 mm from any source and > 100 cGy (every 5th voxel, n = 13 476):
  median **0.989**, p5 0.923, p95 1.048 -> implied S_K **3.54 U**
  (diagnostic, not used). The Phase 1 line (0.931 -> 3.76 U) was computed
  with the 30 contoured seeds only; with the plan's 32 sources the far-field
  ratio is within 1.1 % of unity at nominal S_K, i.e. the earlier 7 % was the
  two missing sources, not the strength. The +-5 % spread (p5-p95) is the
  residual angular / positional structure the `formalism` agent is testing.
- *`delivered_fraction` check*: RTDOSE / TG-43 total-decay dose at the same
  voxels median **1.011** (p5 0.954, p95 1.083), tau = 335.5 h. A dose-to-time
  export of t days would read `1 - exp(-t/tau)` times the strength ratio
  (0.883 at 30 d), and no finite time reproduces a ratio >= 1 at nominal S_K.
  **Conclusion: the RTDOSE is the total dose to decay**; gtcore compares its
  total-decay dose with no `delivered_fraction` correction (decision rule in
  the script: ratio >= 0.97 -> total decay, else flag for the RTPLAN).
- *Position variant*: the 32-source column with the contour centroid in place
  of the RTDOSE maximum where a contour matched gives `HRCTV_PHYS` D90
  6172 cGy / V100 91.8 % (vs 6224 / 92.3 with the raw maxima); the maxima
  positions are kept as the column (the task's specification), the variant
  is one printed line.

**TPS volume / D90 check (`verify_against_tps`).** The RTDOSE has no
`DVHSequence` and the MIM DVH / plan export is not on this machine, so the
check is **pending**: every Table 3 dose row carries
`tps_check = "TPS volume check pending"`. The script prints the command to
run when the export arrives:

```
python scripts/validation_clinical.py "$GT_DATA_ROOT/<PT-CT1mm dir>" "$GT_DATA_ROOT/<PT-RT RTSTRUCT dir>" "$GT_DATA_ROOT/<PT-RT RTDOSE dir>" --rx 6000 --mim-dvh "$GT_DATA_ROOT/<MIM DVH export file>"
```

(`parse_mim_dvh_export` auto-detects the layout; `roi_col=` / `volume_col=` /
`d90_col=` / `dose_units=` are the overrides.) The rows then land in
`clinical_table3_rows.csv` as `row = verify_against_tps` and `tps_check`
becomes `pass` / `FAIL` on every dose row.

**Hooks.** `gtcore.dose.gamma` and `gtcore.dose.formalism`: *not merged* at
`fe0dc81` (imported inside `try/except ImportError`; the status is printed
and written as `row = hook`). Optimizer arm: Phase 4/5, not run here.

### Panel data (`docs/figures/data/`, `git add -f`; every file has a header comment with script, sanitised command, commit, dataset ids, flag)

| file | content | panels |
|---|---|---|
| `clinical_table3_rows.csv` | one row per statistic: `row,structure,dose_source,metric,value,unit,grid,flag,tps_check` (`flag` = `calibration-set`; `structure` = `Target.describe()` provenance; `tps_check` is a ninth column added to the specified eight) -- 183 lines incl. the matrix, Dice, RTSTRUCT volumes, gtcore cavity, seed-match summary, diagnostics, hooks | Table 3 (`paper_tables.py`) |
| `clinical_seed_residuals.csv` | `seed_id,dx_mm,dy_mm,dz_mm,dist_mm`, 30 rows, gtcore - TPS RAS mm | `uncertainty` input |
| `clinical_seed_match.csv`, `clinical_source_match.csv` | the 30 contoured seeds with their match; the 32 plan sources with origin (`contour` / `rtdose-max`), axis, contour and detection match | F4(a) supplement |
| `clinical_slice.npz` | the one axial slice through the implant centroid (k = 117 on the CT grid; `slice_z_ras`, `affine`, `spacing_mm`, `x_ras` / `y_ras` voxel-centre arrays): `ct_hu` (HU), `rtdose_cgy`, `gtcore32src_cgy`, `gtcore32gt_cgy`, `gtcore30_cgy`, masks `cav_post`, `ctv_gt`, `rind5_cavpost`, `rind5_gtcore`, `implant_reach_r10`, `gtcore_cavity`; `window_level_note` (W 400 / L 40); `header` | F4(a), F5(a), F6 share this slice and crop |
| `clinical_slice_contours.csv` | `layer,level_cgy,polyline_id,vertex,x_ras,y_ras,z_ras`: 30 / 60 Gy iso-contours of the four dose fields (matplotlib `contour` on the slice), the 0.5-level outlines of every mask above, and the TPS `Seeds` contour polylines within 3 mm of the slice | F5(a) isodoses, F6 fills / outlines, F4(a) seed contours |
| `clinical_slice_seeds.csv` | `set,seed_id,x_ras,y_ras,z_ras,dz_mm,ax,ay,az,contoured,near_slice` for the 32 gtcore seeds, 30 contour centroids and 32 plan sources (6 of each within 3 mm of the slice) | F4(a) markers |
| `clinical_dvh_hrctv.csv` | `dose_cgy,rtdose,gtcore_32_tps_sources,gtcore_32_gtcore_seeds,gtcore_30_contoured_seeds`, 0-15 000 cGy in 50 cGy steps (301 rows), cumulative fraction >= dose on `HRCTV_PHYS`; the header states the D90s. **MC band hook:** the band is not in this file -- `paper_figures.py` joins the `uncertainty` agent's `docs/figures/data/f5c_*.csv` on `dose_cgy` | F5(b) |

### Tests

`tests/test_validation_clinical_smoke.py` (8 tests, synthetic only): a
`make_head_phantom(spacing=1, n_tiles=2)` CT, an RTSTRUCT written with
pydicom as `tests/test_io_rt.py` does (`Cav_Post` = the truth cavity traced
per slice, `CTV_GT` = half of its 5 mm rind, `Brain` sphere, `Seeds` = the
capsule footprint per slice for 7 of the 8 truth seeds, so one source is
uncontoured as in the clinical case) and an RTDOSE = gtcore's own TG-43
dose at the truth seeds on a 2 mm grid. Checks: `reconstruct` finds 8/8, 7/7
contoured matched, 8 RTDOSE maxima = 8 sources matched, 7 contour-axis + 1
detection-axis sources; residual CSV columns and rows; Table 3 CSV columns,
labels, `calibration-set`, `tps_check` pending, the 32-source column
reproduces the RTDOSE D90 within 15 % on the 2 mm grid; ratio 0.9-1.1 and
the "total dose to decay" conclusion, implied S_K within 0.4 U of 3.5;
every panel file with its documented columns / arrays (DVH monotone, 301
rows); `main()` end to end on the synthetic files with no machine path in
the headers; the polyline / DVH / matching / CSV helpers.

```
GTCORE_STRICT=1 python -m pytest -q tests/test_validation_clinical_smoke.py   # 8 passed in 54 s
GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider                        # full suite: see the line below
```

Full suite at `fe0dc81` (`GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider` from the
worktree root; the real-data run and other agents ran concurrently, so the wall time
is an upper bound): **949 passed, 14 skipped**, 15248 warnings in 874.59 s (0:14:34).
The 14 skips are the Phase 1 set; 941 -> 949 = the 8 new smoke tests.

## 2026-11-03 — Phase 3 — engine QA vs TG-43U1S2 and Paper 1 cross-check (task `engine-qa`, branch `p2/engine-qa`)

Pins `gtcore.dose.engine.TG43Engine("tg43u1s2")` to the TG-43U1S2 consensus
dataset for the IsoRay model CS-1 Rev2 131Cs source (Rivard et al., Med Phys
44(9):e297, 2017, doi 10.1002/mp.12430; erratum Med Phys 45(2):971, 2018, doi
10.1002/mp.12728) and cross-checks it against Paper 1's engine. Supplement S3
material and one Table 3 row. No patient data; everything here is synthetic
geometry and published tables. Worktree `gt-worktrees/p2-engine-qa`, from
`paper2/integration` e13a4af.

### Delivered

| Item | Where | Commit |
|---|---|---|
| Fixture package: consensus tables as embedded in Paper 1 (`tg43u1s2_cs1rev2.json`, provenance header), erratum Table XII polar QA table (`tg43u1s2_table_xii_erratum.csv`, 143 points), along-away Table XXIII as printed (`tg43u1s2_along_away.csv`, 149 points); loaders; `extract_paper1_interpolator()` (ast slice of the class, exec with numpy only -- Paper 1's code is never imported or copied) | `gtcore/dose/qa_data/` | `29295c5` |
| Cross-check script (writes the fixtures, compares tables entry by entry, both QA tables with both engines, (r, theta) grid, 32-seed cloud, port-notes probes) | `scripts/engine_crosscheck.py` | `29295c5` |
| `[tool.setuptools.package-data]` stanza so the fixtures ship with the package (shared file, 4 lines) | `pyproject.toml` | `29295c5` |
| 15 tests | `tests/test_dose_engine_qa.py` | `7bcc1ba` |
| S3 rows + per-point QA tables (`engine_crosscheck.csv`, `engine_qa_table_xii.csv`, `engine_qa_table_xxiii.csv`, `#` headers with script / command / commit / gtcore path) | `docs/figures/data/` | `9bc7d20` |

### Sources, and what could and could not be verified

- **Paper 1's engine**: `GammaTile.py` of the GammaTileDosimetry clone (commit
  `ac47ad6b`, 2026-11-02), class `DoseInterpolator` lines 26-264, dataset
  `"TG43U1S2"`. The class needs only numpy, so it is sliced out with `ast` and
  executed alone; the file itself imports slicer/vtk/qt and cannot be imported.
  Its `tests/tg43_qa_check.py` embeds the erratum-corrected Table XII (143 values)
  and states so; those values are read with `ast.literal_eval`.
- **The report**: both Wiley DOI pages return HTTP 403. IROC Houston hosts a
  free PDF of the 2017 report (`irochouston.mdanderson.org/RPc/BrachySeeds/
  TG-43-Supplement-2.pdf`, 43 pages); its text was extracted with `pypdf`
  6.21.4 (installed into the venv for this step only; not a gtcore
  dependency) and Tables AI, AII, AXIII (consensus), XII and XXIII (QA) were
  parsed and compared programmatically (`verify_report_tables.py`, scratch; the
  Table XXIII parser is in the script).
- **The 2018 erratum**: not retrievable (paywalled; no free copy on IROC, AAPM
  or PMC). Its content is therefore known only through Paper 1's transcription
  of Table XII, which is validated indirectly below.

### Consensus tables: Paper 1 vs gtcore vs the printed report (209 entries)

| Quantity | Paper 1 (`DoseInterpolator`) | gtcore (`TG43U1S2_CONSENSUS`) | Report (IROC PDF) | Result |
|---|---|---|---|---|
| Lambda | 1.056 | 1.056 | Table AI: 1.056 +/- 0.013 | identical |
| L | 0.40 cm | 0.40 cm | Table AI: 0.40 | identical |
| g_L(r), 16 radii 0.1-10 cm | Table AII | same values | Table AII, CS-1 Rev2 column | identical, all 16 |
| F(r, theta) axes | 15 angles x 10 radii | same | Table AXIII (14 printed rows + 90 deg = 1 by definition) | identical |
| F(r, theta) values, 143 tabulated + 7 holes | | | | **one difference** (below); hole pattern identical (r = 0.1 cm, theta <= 20 deg) |
| **F(theta = 80 deg, r = 7 cm)** | **0.999** | **0.998** | **0.998** (80 deg row: 1.000 0.998 0.998 0.997 0.998 0.999 0.999 0.999 0.998 0.998) | gtcore matches the report; Paper 1 has a transcription slip. Effect: +0.100 % on Paper 1's rate at that node only (Paper 1's own QA residual there is +0.200 % vs +0.100 % for gtcore) |
| phi_an(r) (Table AXIV) | not embedded | not embedded | -- | nothing to compare (both engines are 2-D only; no 1-D mode by decision) |
| Dose conversion | 335.48 h x 3.5 U | tau = 232.536 h / ln 2 = 335.4785 h, S_K a parameter | Table XXIV T1/2 = 9.689 d | -4.4e-4 % |
| CLRP v2 tables (bonus) | `_CLRP_*` | `CLRP_V2` and `tg43.py` v1 | -- | identical (7 fit coefficients, 32 x 12 F, axes) |

The fixture JSON keeps Paper 1's 0.999 (it documents Paper 1's engine) and the
test `test_anisotropy_table_matches_fixture_except_documented_entries` pins
exactly this one difference, so a change on either side has to be deliberate.
Suggested fix in Paper 1's repository: `_CON_F_TABLE[13][8]` 0.999 -> 0.998
(not done here; that repository is outside this task).

### The published QA tables

**Table XII (polar, erratum).** The 2017 print, read from the IROC PDF, is
defective in exactly the way an erratum would fix: its 13 angle rows are
labelled 0, 5, 10, ..., 90 but hold the rows for 0, 2, 5, 7, 10, 15, 20, 25,
30, 40, 50, 60, 70 deg; the 80 and 90 deg rows and the 10 cm column are
absent; and the 0.7 cm column differs by 0.1-0.5 % in 8 of 13 rows (print ->
erratum: 1.935 -> 1.933, 1.826 -> 1.824, 1.692 -> 1.689, 1.650 -> 1.648,
1.747 -> 1.745, 1.864 -> 1.861, 1.957 -> 1.955, 2.11 -> 2.10). Every other
printed value equals Paper 1's erratum transcription. At 0.7 cm gtcore gives
2.0404 / 1.9322 / 1.8228 / 1.6895 / 1.6480 / 1.7449 / 1.8615 for 0-20 deg,
i.e. the erratum values to their 3-4 significant figures. This also explains
the `docs/tg43-port-notes.md` remark that the printed table was "inconsistent
with its own consensus parameters (0.75 cm column 13 % low, 0.1 cm column 7 %
high)": the column is 0.7 cm, not 0.75, and the 0.1 cm values sit in
mislabelled rows. Paper 1's transcription is therefore validated by structure
(the full 15 x 10 layout) and by the engine (all 143 values within rounding),
even though the erratum text itself was not read.

**Table XXIII (along-away, as printed).** Rows are along (0-7 cm, 0.5 cm
steps), columns away (0, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 7 cm); the (0, 0) cell
is blank; 149 values. Which axis is which is fixed by the data (the along = 0
row holds the transverse rates 4.10, 1.056, ... = Table XII's 90 deg column;
asserted in the test). Whether the erratum touched this table is unknown; the
printed values agree with Eq. (1) on the consensus parameters to rounding
level under gtcore's interpolation rules (below), so it is usable as the
along-away fixture and is flagged "2017 print, erratum status unknown" in its
header. The brief's expected layout (along mm, away mm, dose rate cGy h^-1
U^-1) is what `load_along_away()` documents and reads.

| QA table | Engine | n | max abs | median abs | p95 abs | worst point |
|---|---|---|---|---|---|---|
| XII (erratum) | gtcore exact | 143 | 0.286 % | 0.054 % | 0.190 % | r = 5 cm, 80 deg (published 0.0222: half a unit in the last place is 0.23 %) |
| XII (erratum) | Paper 1 | 143 | 0.286 % | 0.054 % | 0.193 % | same point |
| XII nodes | gtcore vs Paper 1 | 143 | 0.100 % | 0 | 7e-5 % | (7 cm, 80 deg) = the table difference; all other nodes bit-identical |
| XXIII (print) | gtcore exact | 149 | 0.244 % | 0.066 % | 0.164 % | along 70 mm, away 70 mm |
| XXIII (print) | Paper 1 | 149 | 1.01 % | 0.301 % | 0.780 % | along 70 mm, away 50 mm (linear g_L) |
| XXIII points | gtcore vs Paper 1 | 149 | 0.872 % | 0.387 % | 0.762 % | along 65 mm, away 70 mm |

Per-point files: `docs/figures/data/engine_qa_table_xii.csv`,
`engine_qa_table_xxiii.csv`.

### Cross-check gtcore vs Paper 1's engine (rel = gtcore / Paper 1 - 1)

Grid r = 0.3-10 cm (60 log-spaced radii + table nodes = 73), theta = 0-90 deg
(0.5 deg + nodes = 181), 13 213 points; cloud: 32 random seeds in a 40 mm
cube, 1000 uniform points in an 80 mm cube, `default_rng(0)`, S_K 3.5 U.

| Row (S3) | n | max abs | median abs | p95 abs | where / note |
|---|---|---|---|---|---|
| grid, all points, exact engine | 13 213 | 0.861 % | 0.0061 % | 0.705 % | r = 9.42 cm, 80 deg |
| grid, common table nodes (r in g and F nodes, theta in F nodes) | 105 | 0.100 % | 0 | 2e-14 % | only the F(7, 80) entry; arithmetic otherwise bit-identical |
| grid, between g_L nodes | 10 860 | 0.861 % | 0.019 % | 0.765 % | Paper 1 linear vs gtcore log-linear g_L (TG-43U1S1 Eq. 3); peaks in the 9-10 cm interval (0.874 % at 9.5 cm, 90 deg) |
| grid, near axis theta < 5 deg | 730 | 0.842 % | 0.0036 % | 0.705 % | no near-axis-specific difference for r >= 0.3 cm |
| grid, theta >= 5 deg | 12 483 | 0.861 % | 0.0061 % | 0.705 % | |
| grid, r in [0.3, 1) cm | 4 344 | 0.0036 % | 0.0006 % | 0.0036 % | |
| grid, r in [1, 3) cm | 3 801 | 0.301 % | 0.019 % | 0.288 % | |
| grid, r in [3, 6) cm | 2 715 | 0.692 % | 0.314 % | 0.667 % | |
| grid, r in [6, 10] cm | 2 353 | 0.861 % | 0.347 % | 0.842 % | |
| grid, gtcore tabulated kernel (compute_dose_grid default) | 13 213 | 0.858 % | 0.010 % | 0.703 % | kernel-table error adds ~0.004 % median |
| cloud, summed rate per U, all 1000 points | 1 000 | 11.4 % | 0.258 % | 0.420 % | the 11 % point is 1.11 mm from a seed centre (capsule projection vs Paper 1's raw geometry) |
| cloud, points >= 3 mm from every seed | 992 | 0.512 % | 0.259 % | 0.420 % | |
| cloud, total-decay dose, each engine's own conversion | 1 000 | 11.4 % | 0.258 % | 0.421 % | tau difference -4.4e-4 % |
| cloud, gtcore tabulated kernel vs Paper 1 | 1 000 | 11.4 % | 0.257 % | 0.419 % | |

Reading: away from the seeds the two engines agree at interpolation level
(< 1 % everywhere on the grid, 0.51 % max / 0.26 % median in the cloud), and
the whole of the off-node difference is one documented rule -- Paper 1
interpolates the tabulated g_L linearly in r, gtcore log-linearly as
TG-43U1S1 Sec. III.C prescribes. At the table nodes the arithmetic is
bit-identical. The expectation in PHASE3_PLAN ("agreement to
interpolation-level < 1 %") is met; the dose scale in the cloud was median
15 cGy, max 1 229 cGy, min 2.6 cGy.

### Known port-notes behaviours: still present in Paper 1's source?

| Probe (theta, r) | gtcore | Paper 1 | Status |
|---|---|---|---|
| reference point (90 deg, 1 cm) | 1.056 | 1.056 | both = Lambda |
| on axis inside the can (0 deg, 0.15 cm) | 112.7 | **0** | item 1 still present in Paper 1 (1/(r^2 - L^2/4) < 0, clamped to 0); gtcore projects to the capsule end face |
| active tip (0 deg, 0.20 cm) | 61.66 | 652.2 | Paper 1's clamped-denominator spike; inside the titanium (no tissue) |
| (0 deg, 0.21 cm) | 61.66 | 159.4 | same pole, inside the can |
| just outside the can (0 deg, 0.23 cm) | 50.834 | 50.836 | -0.003 % (g_L rule only) |
| NaN-hole region (0 deg, 0.1 cm) | 161.3 | 0 | both borrow F from 0.25 cm (identical fill); Paper 1 reads 0 for the geometry reason above |
| NaN-hole region (20 deg, 0.1 cm) | 176.1 | 171.8 | +2.6 %: gtcore evaluates at the capsule surface; inside the can |
| radial floor ring (90 deg, 0.045 cm) | 136.2 | 154.0 | -11.6 %: Paper 1 clips r at 0.041 cm, gtcore floors at 0.05 cm (0.09 mm ring beside the capsule side) |
| between g_L nodes (90 deg, 9.5 cm) | 0.0020045 | 0.0020222 | -0.87 % (the interpolation rule) |
| beyond the data (90 deg, 12 cm) | 6.47e-4 | 1.584e-3 | -59 %: Paper 1 clips r to 10 cm, freezing the whole rate (item 5); gtcore follows TG-43U1S1 Eq. 4 and 1/r^2 |
| beyond the data (90 deg, 15 cm) | 1.87e-4 | 1.584e-3 | -88 %, as above |

All rates in cGy h^-1 U^-1. Items 1, 4 and 5 of `docs/tg43-port-notes.md`
are confirmed still present in Paper 1's engine and absent from gtcore; none
of them affects a point in tissue except the far-field freeze, which
over-estimates Paper 1's dose beyond 10 cm (about 1.9 cGy per seed there,
i.e. < 60 cGy for 32 seeds -- not a clinical concern, recorded for S3).

### Tests (`tests/test_dose_engine_qa.py`, 15)

Fixture provenance; Lambda, L, T1/2 and tau vs fixture; g_L table exact; F
table exact except the pinned (80 deg, 7 cm) entry, hole pattern; engine
reproduces every g_L and F node exactly (atol 1e-12); holes filled from
0.25 cm and all inside the capsule; g_L interpolation monotone and bounded in
every interval, nearest-neighbour below 0.1 cm; F bilinear bounded by its
four corners (4000 random points); D(1 cm, 90 deg) = Lambda * S_K for S_K =
1 and 3.5 U through `dose_rate`, `dose_at_points` / tau and
`total_dose_at_point`; Table XII reproduced within 0.5 % at all 143 points
(median < 0.1 %), reference node = Lambda; Table XXIII within 0.5 % at all
149 points (median < 0.1 %), along = 0 row equals Table XII's 90 deg column,
skipped with the expected CSV layout in the reason if the file is absent;
Paper 1's linear-g_L rule vs gtcore on the fixture: 0 at the nodes, < 1 %
everywhere, 0.874 +/- 0.02 % peak between 9 and 10 cm; live cross-check
against the extracted Paper 1 class (nodes rel 1e-9 except the pinned
entry; off-node and 32-seed cloud < 1 %; Paper 1's near-axis zero and 10 cm
freeze still present) -- runs only when `GT_PAPER1_ENGINE` names Paper 1's
`GammaTile.py` or its clone root, otherwise skipped with that reason.
Tolerances: 0.5 % for the QA tables (3-4 significant figures: half a unit in
the last place of 0.0222 is 0.23 %; measured 0.29 % / 0.24 %) and 1.0 % off
the nodes (measured 0.86 %, deterministic).

### Commands

```
# from the worktree root, PYTHONPATH set to it (import-trap rule)
python scripts/engine_crosscheck.py --paper1 <GammaTileDosimetry clone> --write-fixtures --table-xxiii-text <pypdf text of the IROC-hosted report PDF>
# (the text file: pypdf PdfReader(...).pages[i].extract_text() of TG-43-Supplement-2.pdf, one '===== PAGE n =====' marker per page)
python -m pytest -q tests/test_dose_engine_qa.py                                               # 14 passed, 1 skipped
GT_PAPER1_ENGINE=<GammaTileDosimetry clone> python -m pytest -q tests/test_dose_engine_qa.py   # 15 passed
GTCORE_STRICT=1 GT_PAPER1_ENGINE=<clone> python -m pytest -q -p no:cacheprovider               # full suite, line below
```

Full suite at `5371d7f` (tree = `paper2/integration` e13a4af + this branch; `GTCORE_STRICT=1`, `GT_PAPER1_ENGINE` set so the live cross-check ran; other agents running concurrently, so the wall time is an upper bound):
`956 passed, 14 skipped, 15242 warnings in 696.80s (0:11:36)`. The skips are the Phase 1 set (13 interface placeholders + the `GT_E2E`-gated run); the new file adds 15 passed, 0 skipped.

Script run at `29295c5` (headers say so): `gtcore.__file__` =
`<worktree>/gtcore/__init__.py`, Paper 1 clone `ac47ad6be`.

### Pending / for the coordinator

- The erratum text (Med Phys 45:971) is still not in hand; Table XII rests on
  Paper 1's transcription, validated indirectly as above. Table XXIII is the
  2017 print; if Jacob obtains the erratum, re-run `--write-fixtures` with any
  corrected along-away values and the test picks them up.
- Paper 1's repository has the F(7 cm, 80 deg) slip (0.999 -> 0.998); fix it
  there when convenient. Both papers then rest on one verified dataset.
- Table 3 row suggestion: "Engine QA vs TG-43U1S2: all 143 Table XII points
  within 0.29 % (median 0.05 %); all 149 along-away points within 0.25 %; vs
  Paper 1's engine bit-identical at the table nodes, <= 0.86 % between g_L
  nodes (interpolation rule), <= 0.51 % on a 32-seed cloud >= 3 mm from the
  seeds." Calibration/held-out flag: not applicable (published tables).
- Shared file touched: `pyproject.toml` (package-data stanza only).
- Installed into the venv: `pypdf` 6.21.4 (PDF text extraction for the
  verification; not used by gtcore, not declared).

## 2026-11-03 — Phase 2 — volumetric RIND5 in `final_report`; shell = S5 coverage (task `report-rind5`, branch `p2/report-rind5`)

Requirements rev 3 §3 F8 "FIX scoring", §6 Phase 2 fourth checkbox; Phase 2
contract section D. Branched from `paper2/integration` at `e13a4af` (A
`dose_stats`, B `targets` and C `rt-io` merged). Code commit `d5d62dc`; these
notes in the follow-up commit. Hardware as above; other agents and the full
suite ran concurrently, so every timing is an upper bound.

### Definition (Methods-quotable)

The optimizer report (`gtcore.plan.final_report`, behind `plan.optimize` and
`gt optimize`) scores the **volumetric HR-CTV** `RIND5(cavity, clip)` on the
final 1 mm dose grid: `gtcore.dose.targets.rind5` is built directly on that
grid (cavity and clip masks resampled nearest-neighbour onto it, Euclidean
distance transform in physical mm, rind = within 5 mm of the cavity, outside
it, inside the clip), and `gtcore.dose.metrics.dose_stats` is evaluated on
the exact-kernel dose at the rind's voxel centres (every voxel counts its
volume). The result -- D90, D50, D100/Dmin, Dmax, Dmean, V100, V150, V200,
n, volume -- is written with the target's provenance (cavity source, clip
source, grid spacing / shape / origin, depth, commit). The +5 mm surface
shell the optimizer works on (vertices of the mesh offset along its normals;
unweighted `dvh_stats` per offset, area-weighted stats on the optimizer's
`TargetSet`) is reported as **"S5 coverage"**, a surrogate, and is never
labelled D90 / V100 of the HR-CTV. When no cavity mask is available (the
printed-phantom shell, or a caller that did not pass one) the RIND5 entry is
absent and the report's `notes` say so explicitly.

### What changed

- `gtcore/plan/report.py::final_report` (signature extended additively:
  `clip_mask=None, clip_affine=None, cavity_source="gtcore:cavity",
  clip_source=None`): `metrics_grid["RIND5"]` =
  `targets.target_stats(rind5(...), doses)` -> `DOSE_STAT_KEYS` + `target`
  + `provenance`; `metrics_grid["S5"][offset]` = the former top-level
  `metrics_grid[offset]` shell rows (`dvh_stats` + `curve_x/curve_y`),
  unchanged numbers; `metrics_grid["target"]` unchanged (labelled surrogate
  in the printout). `metrics_grid_interference` mirrors the three entries;
  its note names "S5 coverage +5 mm" and, when present, the RIND5 delta.
  `parameters["rind_depth_mm"]` is recorded; `runtime["rind5"]` times the
  RIND5 stage. **No `"rind"` alias, no top-level float keys** -- every
  in-tree consumer was updated (below). An empty rind on the grid (cavity
  outside the bounds, or clipped away), a mask without its affine, or no
  mask at all each leave an explicit note; a RIND5 exception is a note and
  is re-raised under `GTCORE_STRICT` (`_strict.swallow_or_raise`, the Phase
  1 pattern -- the old handler swallowed it silently).
- **Decision -- how the RIND5 doses are evaluated.** The target is built on
  the dose grid, so its voxel centres are grid points and the exact-kernel
  dose there is already in the grid; `final_report` reads
  `dose.array[target.mask]` (C order = `Target.voxel_centers_ras()` order;
  `plan.report.rind5_doses_on_grid`). Measured: this is **bit-identical** to
  `targets.target_doses_exact` at the same points (`np.array_equal` True,
  max relative difference 0.0 -- `compute_dose_grid` and `dose_at_points`
  share `_sum_seed_rates`), so the engine is not called a second time
  (0.6 s for the RIND5 stage on a 2.6 M-voxel grid, all of it EDT +
  resampling). `targets.target_doses_from_grid` (trilinear
  `Volume.sample_ras`) returns the same values only to **float32** precision
  (max relative difference 5.9e-8; D90 2356.253086 vs 2356.253174 cGy on
  the sphere toy) because `sample_ras` casts the array to float32 before
  `map_coordinates`. The acceptance criterion's "1e-9 vs
  `target_doses_from_grid`" is therefore pinned as 1e-9 against the grid
  values and against `target_doses_exact`, and as 1e-6 relative against
  `target_doses_from_grid`, with the reason in the test and the module
  docstring. `gtcore/volume.py` was not changed (not in this task); Phase 3
  should remember the cast when it samples the RTDOSE grid trilinearly.
- `gtcore/plan/__init__.py`: `OptimizeReport.summary()` prints, after the
  solver line, the **HR-CTV row first** -- `HR-CTV RIND5 (volumetric, 1x1x1
  mm grid; cavity gtcore:cavity, clip gtcore:cranial_interior, depth 5.0
  mm): D90 ... V100 ... V150 ... V200 ... cc (n voxels)` -- then one
  `S5 coverage <wall|+5 mm|+10 mm> (surrogate shell vertices): ...` row per
  offset, then `target <name> (surrogate, area-weighted): ...`, then the
  notes. Hand-built reports with the old float keys still print (as S5
  coverage). New field `OptimizeReport.dose_volume` (the final grid; left
  out of `to_dict` / JSON / CSV). JSON keys `metrics_grid.S5.5.0.V100`,
  `metrics_grid.RIND5.D90`, `metrics_grid.RIND5.provenance.cavity_source`
  in the CSV. `plan.final_report` wrapper forwards the new keywords.
- `gtcore/plan/api.py::optimize`: additive keywords `cavity_mask,
  cavity_affine, clip_mask, clip_affine, cavity_source, clip_source`
  forwarded to `final_report`; with `report=False` and a cavity mask the
  notes say RIND5 was not scored. Solver / objective untouched.
- `gtcore/cli.py` (`gt optimize`): `_rind5_inputs(result, label)` picks the
  pipeline's `cavity_mask` when the wall is the cavity and the clip in the
  planner's order (`cranial_interior`, then `brain`; `planner._ensure_hrctv`
  does the same), prints `HR-CTV: RIND5 of the gtcore cavity (clip
  gtcore:cranial_interior), scored on the final 1 mm dose grid; shell rows
  are S5 coverage (surrogate)` and passes the masks to `plan.optimize`. On
  a phantom shell (PH8, PH0: no segmented cavity) it prints `HR-CTV: no
  cavity mask on this wall (phantom shell (no cavity segmented)) -- the
  report carries S5 coverage (surrogate) only, no RIND5` and the report's
  notes repeat it. No planner (UI) change: the planner's `O` key still
  calls `plan.optimize` without a cavity mask, so that report carries the
  explicit note; wiring it is a one-line change in `planner.py` left to the
  coordinator (stop list: no planner UI work).
- Consumers of the old keys: `scripts/validation_optimize.py::evaluate_tiles`
  (`src["S5"][offset]`; the rind columns are now `RIND5_V100`, `RIND5_D90`,
  `RIND5_V150`; the unprefixed `V100`/`D90` campaign columns stay the shell
  surrogate until Phase 4 re-runs the campaign -- said in its docstring),
  `scripts/go_no_go_optimize.py` (grid-check line labelled "S5 coverage +5
  mm"), `scripts/bench_dose_stats.py` (Phase 2 A driver). Two consumers the
  first pass missed (found through the full run's extra skip, fixed in the
  follow-up commit named below): `gtcore/planner.py::_before_after_text`
  (the status line after the `O` key looked for a float key at the top of
  `metrics_grid` and would have fallen back silently to the influence
  estimate; it now reads `metrics_grid["S5"][5.0]`, legacy float keys still
  accepted -- key lookup only, wording "+5 mm shell" unchanged: planner UI
  text is on the stop list) and `tests/test_plan_review.py::_shell_entry`
  (the A7 probe (e) skipped itself when it found no `5.0` key; it reads the
  S5 row now and the probe runs again). Checked and not a consumer:
  `scripts/plan_candidates_runtime.py` (no `metrics_grid` / `rind` / shell
  access). `docs/optimize-notes.md` Phase 0 item 9 carries
  one dated pointer to this section; its tables keep the old keys as
  history. `docs/review-report.md` (historical A7 review) still mentions
  `metrics_grid[5.0]`; left as is.

### Synthetic example -- `gt optimize` on the synthetic phantom, N = 6

Command (phantom mode, `make_head_phantom(spacing=0.7)`, pipeline cavity;
run from the worktree root with `PYTHONPATH` set to it, `gtcore.__file__`
printed first = this tree), commit `d5d62dc`:

```
GTCORE_STRICT=1 python -m gtcore.cli optimize --tiles 6 --solver greedy --h 4 --spins 3 --seed 0 --out <dir>
```

Wall `cavity wall` (28 604 faces); clip `gtcore:cranial_interior`; dose grid
1 mm, margin 50 mm, 134 x 139 x 141 voxels; 6 full tiles = 24 seeds, S_K
3.5 U, rx 6000 cGy; no overlaps. The same configuration through
`plan.optimize(mesh, 6, solver="greedy", seed=0, h_mm=4, n_spins=3,
**_rind5_inputs(result, label))` (scratch driver, same commit) gives the
identical rows; the last row is the planner's `V`-key HR-CTV on the same
cavity, clip and seeds (`build_hrctv(..., inside_mask=cavity,
keep_mask=cranial_interior)` + `hrctv_stats(h.doses_exact(seeds))`):

| row (as printed by `gt optimize`) | n | D90 [cGy] | V100 | V150 | V200 | volume |
|---|---|---|---|---|---|---|
| HR-CTV RIND5 (volumetric, 1 mm grid; cavity gtcore:cavity, clip gtcore:cranial_interior) | 29 778 vox | 4056 | 0.642 | 0.283 | 0.104 | 29.8 cc |
| S5 coverage wall (surrogate shell vertices) | 14 296 | 5391 | 0.829 | 0.638 | 0.444 | -- |
| S5 coverage +5 mm (surrogate shell vertices) | 14 296 | 3622 | 0.513 | 0.047 | 0.005 | -- |
| S5 coverage +10 mm (surrogate shell vertices) | 14 296 | 2439 | 0.018 | 0.003 | 0.001 | -- |
| target shell+5mm (surrogate, area-weighted; the objective's target) | 14 296 | 3472 | 0.482 | 0.077 | 0.019 | -- |
| planner `build_hrctv` + `hrctv_stats`, same cavity / clip / seeds (its own grid, mesh bounds - 7 mm) | 29 778 vox | 4055.9 | 0.6421 | 0.2831 | 0.104 | 29.78 cc |

Reading. (i) The report's RIND5 and the planner's HR-CTV are the **same
number** (D90 4055.9 cGy, V100 0.6421, V150 0.2831, 29 778 voxels): both
grids are `mesh.bounds[0]` minus an integer margin at 1 mm, so they share
one lattice (offset mod 1 mm = 0) and the rind voxel sets coincide. (ii) The
S5 +5 mm surrogate reads lower than the volumetric rind here (V100 0.513 vs
0.642, D90 3622 vs 4056 cGy): the rind contains the 0-5 mm depths where
the dose is higher than on the 5 mm surface -- the reason shell numbers
must not be labelled HR-CTV D90 / V100. (iii) Format example only
(synthetic phantom, greedy, h 4 mm / 3 spins, one seed): not a paper
number; Phase 4 re-runs the campaign with RIND5 scoring.

Runtimes (upper bounds, suite running concurrently): reconstruct 16.8 s;
`plan.optimize` 21.8 s = candidates 6.7 + influence 0.3 + conflicts 1.5 +
greedy 0.6 + report 12.7 (dose grid 11.8, **RIND5 0.6**, shadowing 0.3,
shells + target 0.02). JSON / CSV written by the command carry
`metrics_grid.RIND5.*` (incl. `provenance.*`) and `metrics_grid.S5.<off>.*`
(checked in the CSV).

### Sphere toy (the test fixture) -- grid-origin sensitivity of RIND5 at 1 mm

`tests/test_plan_report.py::sphere_cavity`: icosphere R = 20 mm (vertices at
+-20 on the axes), cavity mask on a 1 mm grid whose lattice sits 0.3 mm off
the integers, clip x > -8 mm ("gtcore:brain"), four conformed tiles at the
poles, rx 6000 cGy, 1 mm dose grid. Measured at `d5d62dc` (scratch driver;
`rind5` stage 0.06 s, whole report 0.9 s):

| | D90 [cGy] | V100 | V150 | volume |
|---|---|---|---|---|
| report RIND5, margin 12.3 mm (grid on the cavity's lattice) | 2356.3 | 0.3260 | 0.0792 | 20.613 cc (20 613 vox) |
| same, `target_doses_exact` at the rind centres | identical (array_equal) | | | |
| same, `target_doses_from_grid` (trilinear, float32) | 2356.3 (rel 3.7e-8) | 0.3260 | 0.0792 | |
| `build_hrctv` + `hrctv_stats` (integer lattice: shifted 0.3 mm per axis, 0.52 mm diagonal) | 2421.5 (-2.7 % vs report) | 0.3024 (+2.4 pp) | 0.0756 (+0.35 pp) | 20.613 cc |
| report RIND5, margin 12 mm (integer lattice, = `build_hrctv`'s) vs `build_hrctv` | agree to 1e-9 | | | |

Reading: on a half-voxel lattice shift the 1 mm RIND5 statistics move by a
few % (D90) / a few pp (V100) on this toy, where the 60 Gy isodose sits at
the rind's outer boundary and 4 tiles leave steep gradients. In gtcore the
report and the planner derive their grid origins from the same mesh bounds
minus integer margins, so they coincide (the phantom table above); the
sensitivity is a property of volumetric statistics at 1 mm, to be stated in
the Methods (grid named with every RIND5 number -- the provenance does
this), not a defect. **Acceptance wording check:** "equal `build_hrctv` +
`hrctv_stats` within 2 % on different grids" is met exactly (1e-9) on the
realistic different-grid case (different origin / shape, same lattice),
and is **not** met for a half-voxel lattice shift (-2.7 % D90, +2.4 pp
V100); the test pins the latter loosely (5 % / 0.05) and records the
measured values. Coordinator decision whether a tighter statement is
wanted (e.g. requiring integer margins).

### Tests

`tests/test_plan_report.py` (rewritten, 23 tests): the existing fields /
JSON / CSV / S_K-scaling / interference / empty-tiles tests on the new keys
(`metrics_grid["S5"]`, no float keys, no `"rind"`, `NO_CAVITY_NOTE`
present, summary says "S5 coverage" and has no HR-CTV row without a cavity
mask); the pre-Phase-2 box-cavity case as `RIND5` on the 2 mm grid; the
sphere fixture: `RIND5` keys == `DOSE_STAT_KEYS` + `target` + `provenance`,
provenance (`cavity_source`, `clip_source`, builder, depth, grid == dose
grid), stats == `dose_stats` on the grid values (1e-9), ==
`target_doses_exact` (array_equal, 1e-9), == `target_doses_from_grid`
(float32: 1e-6), == `build_hrctv` + `hrctv_stats` on its own grid (1e-9,
same lattice; the "different grids" case), the shifted-lattice bound (5 % /
0.05), `S5` == `dvh.shell_report` (bit-identical incl. curves), summary
labels and row order, legacy float keys print as S5 coverage, JSON / CSV
round trip with provenance, interference mirror, three explicit-absence
notes (no affine, cavity off the grid, clipped away), strict re-raise vs
note, `rind5_doses_on_grid` grid guard. `tests/test_plan_api.py`: +1
(`optimize` forwards the masks / labels to `final_report`; `report=False`
note). `tests/test_planner_optimize.py`: +1 (`gt optimize` forwards the
pipeline cavity mask + cranial-interior clip; phantom shell -> None and the
"no RIND5" line).

Commands at `d5d62dc` (worktree root, `GTCORE_STRICT=1`):
`python -m pytest -q tests/test_plan_report.py tests/test_plan_api.py
tests/test_plan_interface.py -p no:cacheprovider` -> `64 passed, 13 skipped
in 12.46s` (the skips are the interface placeholders);
`python -m pytest -q tests/test_planner_optimize.py -k cli` -> `6 passed, 20
deselected in 11.97s`.

Full suite (`GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider`, tree
= `paper2/integration` e13a4af + `d5d62dc`, other agents and the phantom
driver running concurrently): see the line appended below.

Full-suite result at `d5d62dc` (`GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider`
from the worktree root; tree = `paper2/integration` e13a4af + this commit; the phantom
driver and other agents ran concurrently, so the wall time is an upper bound):
`953 passed, 15 skipped, 15242 warnings in 670.55s (0:11:10)`, no failures. 901 -> 953
= the tests merged with B / C plus the net new tests here (test_plan_report 7 -> 23 incl.
the rewritten ones, +1 test_plan_api, +1 test_planner_optimize). **The 15th skip was
caused by this branch:** `tests/test_plan_review.py` probe (e) skips itself when
`metrics_grid` has no `5.0` key (`-rs` on the two files holding the baseline 14 showed
exactly the 13 interface placeholders + the `GT_E2E` gate). Fixed in the follow-up commit
(`_shell_entry` and `planner._before_after_text` read `metrics_grid["S5"]`; +1 direct test
of the status line): `tests/test_plan_review.py` -> `16 passed` (0 skipped),
`tests/test_planner_optimize.py -k "before_after or cli"` -> `7 passed`. The full suite was
re-run at that commit; its line is appended below.

Full-suite result at `6d49ee9` (the final code commit: d5d62dc + the two consumer fixes;
`GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider` from the worktree root; eight agents
shared the machine during this run, so the wall time is an upper bound):
`955 passed, 14 skipped, 15242 warnings in 676.65s (0:11:16)`, no failures; the skips are the baseline 14 (13 interface placeholders + the
`GT_E2E`-gated run), the review probe (e) runs again.

## 2026-11-03 — Phase 3 — seed-to-wall distance (3 mm model) (task `seed-wall`, branch `p2/seed-wall`)

Requirements rev 3 §3 F8 "FIX the 3 mm offset contradiction", §6 Phase 3 fifth
checkbox; Phase 3 plan row `seed-wall`. Code commit `448a6b3`
(`scripts/seed_wall_distance.py` + `tests/test_seed_wall_distance.py`); the
CSVs and this section are in the follow-up commit. Branched from
`paper2/integration` at `e13a4af`. **Every real-data number below is
calibration-set** (one clinical case; the printed cavity is the same
patient's intra-operative cavity). Hardware as in the Phase 1 sections; other
agents ran concurrently, so timings are upper bounds.

**Question.** The tile model puts the seed plane `SEED_PLANE_OFFSET_MM` =
3.0 mm from the tile's tissue face (hydrated tiles 2.25–3.75 mm,
`gtcore.geometry`), so a seed should sit about 3 mm off the cavity wall,
inside the lumen. The rev 3 contradiction: on the printed phantom the
localized seeds sat 0.84 mm off the wall mesh while the conformer puts them
at 3 mm (`docs/optimize-notes.md` V6), and the v6 figure plan quoted a
mock-level clinical median of 4.6 mm (10–90 % 2.7–5.9 mm) to `Cav_Post`.

### Answer in one paragraph

On the clinical case the 32 gtcore seeds sit a median **+3.65 mm** inside
the physician's `Cav_Post` surface (10–90 % 1.70–5.46 mm; the 32 TPS source
positions give +3.57 mm, the mesh cross-check +3.56 / +3.51 mm), i.e. the
distribution centres inside the 2.25–3.75 mm hydrated band, 0.65 mm above
the nominal 3.0 mm, and **not** at the mock's 4.6 mm. On the phantom the
same 32 seeds sit a median **+3.05 mm** off the tile-less twin's printed
wall (10–90 % 2.35–3.77 mm, 26/32 in band, sd 0.66 mm) but **−0.68 mm**
relative to the PH8 `body` surface: gtcore's body mask includes the hydrated
collagen tiles, so the "wall mesh" the conformer used on the phantom is the
tiles' free face, 1 mm on the lumen side of the seed plane. The 0.84 mm
figure was a reference-surface artifact, not a placement error, and the
3 mm model holds on the phantom and in vivo. **`SEED_PLANE_OFFSET_MM` is
not re-tuned** (requirements §4 / brief: no parameter is tuned on the
clinical case; the measurement is a check of a device constant).

### What was built

`scripts/seed_wall_distance.py` (paths are CLI arguments; `--out` defaults
to `docs/figures/data`):

| Function | Role |
|---|---|
| `SignedDistanceField(mask, affine, solid, fine_mm=0.5, crop_margin_mm=15, points_ras)` | the primary estimator (below); `.sample(points_ras)` trilinear |
| `surface_mesh(mask, affine)` / `signed_distance_mesh(mesh, points, solid)` | cross-check: `gtcore.segment.surface.mask_to_mesh` (marching cubes 0.5, Taubin 10, largest component, step 1 on a crop) + `trimesh.proximity.signed_distance` |
| `tps_sources_from_rtdose(dose)` | RTDOSE local maxima > 200 Gy (3 × 3 × 3) merged within 3 mm — the rule of `validation_clinical.py::print_seed_match_detail` |
| `body_mask_from_hu(arr, -300)` | gtcore's body rule (`segment_head`: largest component above `AIR_BODY_HU`, holes filled) applied to PH0 |
| `register_rigid(fixed, fixed_mask, moving, moving_mask)` | SimpleITK Euler3D, Mattes MI 50 bins, 25 % random sampling (seed 1234), levels 4/2/1 with 2/1/0 mm smoothing, regular-step gradient descent, centred on the fixed mask centroid and initialised by the two centroids; returns the transform and Dice of the carried mask |
| `to_sitk`, `transform_points_ras`, `euler_as_matrix` | exact gtcore-affine ↔ SimpleITK (LPS) geometry; vectorised point transform and its inverse |
| `summarize`, `histogram_rows`, `Collector`, `write_csv` | statistics (numpy linear percentiles, sample sd, band count), 0.5 mm histogram, CSVs with `#` header comments |

`tests/test_seed_wall_distance.py` (6 tests, 5–6 s): digital sphere on the
clinical 0.5 × 0.5 × 1 mm grid with the centre off-grid, seeds at
+4.6 / +3 / +1 / −1 / −3 mm in 300 directions — every sign right, |median|
and |mean| error ≤ 0.2 mm, rms ≤ 0.25 mm, every seed within half the slice
spacing + 0.2 mm, for the EDT and for the mesh; the `solid` (phantom)
convention on a 0.59 × 0.59 × 1 mm grid; identical numbers on an affine with
negated direction cosines and a far-away origin (the PH8/PH0 affines);
`summarize`/`histogram_rows`; the RTDOSE-maxima finder on two synthetic
Gaussian sources (exact); `to_sitk` geometry against the affine; the rigid
registration recovering a known 4°/−3°/6°, (3, −2.5, 4) mm transform of a
synthetic printed-block look-alike (probe error < 0.3 mm, Dice > 0.97).

### Sign convention and estimator (Methods-quotable)

**Sign.** For every reference surface the signed distance is positive when
the seed centre lies on the *cavity side* of the surface (inside the
contoured or segmented cavity; in the lumen, away from the printed plastic)
and negative inside tissue, plastic or the segmented solid. The model
therefore predicts +3.0 mm on every reference. (The task brief gave
"positive = outside the cavity" for the clinical EDT and "positive = into
the cavity / away from the plastic" for the phantom and asked for one
convention; the cavity-side-positive one is used throughout, so the band is
+2.25…+3.75 mm everywhere.)

**Estimator.** The reference is a binary voxel mask on the image grid: the
physician's `Cav_Post` contour rasterised on the CT grid
(`gtcore.io.rt.rasterize_roi`, Phase 2 rule), gtcore's cavity mask, the PH8
body mask, or the PH0 body mask. The mask is cropped to its bounding box
(plus the query points) with 15 mm of context, linearly upsampled by integer
factors so no spacing exceeds 0.5 mm (through-plane ×2 on every scan here)
and thresholded at 0.5 — the 0.5-level of the linearly interpolated
indicator is the mid-point surface marching cubes draws, which removes the
half-slice bias of a distance transform run on the raw 1 mm-slice mask. The
Euclidean distance transform is computed inside and outside in physical
millimetres (`scipy.ndimage.distance_transform_edt` with the spacing as
`sampling`); their difference is the signed distance, sampled trilinearly at
each seed centre. The cross-check builds gtcore's own surface of the same
mask (`mask_to_mesh`: marching cubes at 0.5, Taubin smoothing, largest
component) and takes `trimesh.proximity.signed_distance`. On a digital
sphere (R = 15 mm, centre off-grid, 600 directions) the committed code gives:

| grid (mm) | offset | EDT median / mean / rms / max abs error (mm) | mesh median / mean / rms / max (mm) |
|---|---|---|---|
| 0.52 × 0.52 × 1.0 (PT-CT1mm) | +4.6 | −0.034 / −0.036 / 0.085 / 0.235 | −0.084 / −0.098 / 0.128 / 0.490 |
| | +3.0 | −0.006 / −0.004 / 0.083 / 0.236 | −0.068 / −0.079 / 0.114 / 0.490 |
| | +1.0 | +0.097 / +0.094 / 0.133 / 0.355 | −0.032 / −0.039 / 0.097 / 0.489 |
| | −1.0 | −0.116 / −0.117 / 0.166 / 0.631 | +0.016 / +0.022 / 0.097 / 0.441 |
| | −3.0 | −0.023 / −0.038 / 0.122 / 0.523 | +0.042 / +0.052 / 0.105 / 0.335 |
| 0.59 × 0.59 × 1.0 (PH8) | +3.0 | +0.010 / +0.014 / 0.091 / 0.356 | −0.066 / −0.077 / 0.116 / 0.483 |
| | +1.0 | +0.121 / +0.118 / 0.154 / 0.407 | −0.028 / −0.036 / 0.102 / 0.480 |
| 0.68 × 0.68 × 1.0 (PH0) | +3.0 | +0.015 / +0.016 / 0.096 / 0.285 | −0.075 / −0.088 / 0.123 / 0.470 |
| | −1.0 | −0.166 / −0.180 / 0.215 / 0.600 | −0.000 / −0.004 / 0.094 / 0.442 |

Reading: the distribution-level accuracy (median, mean) is within 0.2 mm at
every offset and grid, and within 0.05 mm around +3 mm where the clinical
and phantom seeds sit; the per-seed worst case is bounded by half the slice
spacing (a surface element perpendicular to z is quantised to the slice —
irreducible from a binary mask), so the **0.2 mm acceptance criterion is met
for the distribution and for the sign of every seed, not for every single
seed** (the test asserts ≤ 0.5 × slice + 0.2 mm per seed). The EDT
over-reads |distance| by ~0.1 mm within 1 mm of the surface (the trilinear
blend across the zero crossing); the mesh under-reads by ~0.07–0.1 mm at
3–4.6 mm (Taubin). The two methods bracket the truth; both are reported.

### Results — clinical, PT-CT1mm + PT-RT (commit `448a6b3`)

Inputs: CT 204 × 512 × 512 at 0.52 × 0.52 × 1.0 mm; `reconstruct` 28–30 s,
32 seeds, gtcore cavity 41.3 cc; `Cav_Post` 35.10 cc on the CT grid; RTDOSE
maxima: 32 sources, one-to-one match to the gtcore seeds median 0.92 mm, p95
1.37 mm, max 1.45 mm (dose-grid voxel centres, 1 × 1 × 1.667 mm, so ≈ 0.5–0.8
mm of that is grid quantisation). EDT crops 73 × 148 × 162 → 146 × 148 × 162
fine voxels (0.8 s); meshes 69 k (`Cav_Post`) / 104 k (gtcore cavity) faces.

| seed set | reference | method | median | 10–90 % | min | max | mean ± sd | in band 2.25–3.75 | negative |
|---|---|---|---|---|---|---|---|---|---|
| gtcore seeds (32) | `Cav_Post` | EDT | **+3.65** | +1.70 … +5.46 | −2.07 | +7.11 | +3.71 ± 1.65 | 13/32 | 1 |
| gtcore seeds | `Cav_Post` | mesh | +3.56 | +1.57 … +5.45 | −1.82 | +7.00 | +3.63 ± 1.63 | 15/32 | 1 |
| TPS sources (32) | `Cav_Post` | EDT | **+3.57** | +1.68 … +5.20 | −1.75 | +6.68 | +3.68 ± 1.66 | 14/32 | 1 |
| TPS sources | `Cav_Post` | mesh | +3.51 | +1.55 … +5.15 | −1.50 | +6.59 | +3.61 ± 1.64 | 14/32 | 1 |
| gtcore seeds | gtcore cavity | EDT | +2.95 | +0.88 … +3.12 | −1.79 | +3.23 | +2.37 ± 1.20 | 23/32 | 3 |
| gtcore seeds | gtcore cavity | mesh | +2.87 | +0.78 … +3.01 | −1.69 | +3.15 | +2.26 ± 1.17 | 22/32 | 3 |
| TPS sources | gtcore cavity | EDT | +2.87 | +1.00 … +3.57 | −2.05 | +3.71 | +2.41 ± 1.33 | 21/32 | 2 |
| TPS sources | gtcore cavity | mesh | +2.83 | +0.78 … +3.46 | −1.99 | +3.62 | +2.32 ± 1.30 | 20/32 | 2 |

Per-seed values (gtcore seeds vs `Cav_Post`, EDT, sorted, mm): −2.1, 1.5,
1.6, 1.6, 2.6, 2.9, 2.9, 3.1, 3.2, 3.3, 3.4, 3.4, 3.4, 3.5, 3.5, 3.6, 3.7,
3.8, 3.8, 3.9, 4.2, 4.2, 4.6, 4.8, 4.9, 4.9, 4.9, 5.1, 5.5, 5.8, 6.1, 7.1;
1 mm bins: [−3, −2) 1, [1, 2) 3, [2, 3) 3, [3, 4) 13, [4, 5) 7, [5, 6) 3,
[6, 7) 1, [7, 8) 1. TPS sources: 1 below −1, 3 in [1, 2), 4 in [2, 3), 10 in
[3, 4), 8 in [4, 5), 4 in [5, 6), 2 in [6, 7). Paired TPS − gtcore on the
same reference: median −0.06 mm, sd 0.50 mm, max |Δ| 1.29 mm (the seed
localisation disagreement, Phase 1: median 0.15 mm to the contoured seeds,
0.92 mm to the dose-grid maxima).

Reading. (i) The centre of the distribution is 3.5–3.65 mm by four
estimates (two seed sets × two surface methods), inside the hydrated band
and 0.5–0.65 mm above nominal; the v6 mock's 4.6 mm is not reproduced and
is superseded. (ii) 31 of 32 seeds are inside the contoured cavity; one
seed (−2.1 mm) lies in tissue beyond the contour. (iii) The spread (sd
1.65 mm; 10–90 % 1.7–5.5 mm) is 2.5× the phantom's (0.66 mm) — the
physician's contour of a drained post-operative cavity is drawn through
tiles, fluid and debris at 1 mm pitch and does not follow the tile tissue
face everywhere, and tiles that overlap or lift off the wall (6 of 8 fitted,
8 leftover seeds, F4b / task `leftovers`) put seeds 5–7 mm into the lumen (5
seeds > 5 mm). Which seeds those are can be read per `seed_id` from
`seed_wall_distance.csv` against the `leftovers` tables; no tile-level
analysis is made here. (iv) The gtcore-cavity rows are **circular** and are
not evidence: `segment_cavity` clips the cavity `SHEET_TOL_MM` = 3 mm beyond
the seed sheet (its docstring cites the same 3 mm model), so most seeds read
≈ 3.1 mm by construction; the low tail (0.9–1.8 mm, 3 negative) is where the
gtcore cavity leaks past the seeds (Phase 1/2 known state, Dice 0.665 vs
`Cav_Post`). Reported for completeness, labelled as such in the CSV header.

### Results — phantom, PH8 + PH0 (commit `448a6b3`)

Inputs: PH8 157 × 512 × 512 at 0.59 × 0.59 × 1.0 mm, `reconstruct` 29–36 s,
32 seeds, body mask 474 mL; PH0 248 × 512 × 512 at 0.68 × 0.68 × 1.0 mm,
body (largest component > −300 HU, filled) 468 mL, k 135–241 — the same
print (PH0's second object, 268 mL at ~280 HU, is a different piece and is
excluded by the largest-component rule). Plastic HU (median of the 3-voxel
eroded body): PH8 104, PH0 94. **23 of 32 seed centres lie inside the PH8
body mask**, i.e. inside a segmented tile.

Rigid registration PH0 → PH8 (fixed PH8, moving PH0, both cropped to the
body + 10 mm): 23 iterations, final MI −1.417, Dice of the carried body
masks **0.979**, Euler angles (−0.01, −3.54, −0.06)°, translation (−12.9,
−2.67, −142.0) mm LPS (the two scans differ by a 142 mm table offset and a
3.5° tilt about y), 4.5–6 s. Registration residual = |distance| of the PH0
wall (Taubin-mesh vertices, 60 000 sampled) carried into PH8 to the PH8 body
surface: median **0.34 mm**, p90 0.77, p95 0.98 mm over all points; median
0.33 mm, p90 0.70, p95 0.79 mm over the 47 765 points more than 15 mm from
any seed (no tiles). For scale, 1 − Dice = 0.021 on a 470 mL block of
54 000 mm² surface is a 0.18 mm mean surface discrepancy; the residual is at
the two scans' voxel-quantisation floor (0.59 / 0.68 mm pixels).

| seed set | reference | method | median | 10–90 % | min | max | mean ± sd | in band | negative |
|---|---|---|---|---|---|---|---|---|---|
| PH8 seeds (32) | (i) PH8 `body` (tiles included) | EDT | **−0.68** | −1.74 … +0.57 | −2.32 | +1.87 | −0.60 ± 0.96 | 0/32 | 24 |
| PH8 seeds | (i) PH8 `body` | mesh | −0.51 | −1.69 … +0.28 | −2.13 | +1.99 | −0.56 ± 0.89 | 0/32 | 24 |
| PH8 seeds | (ii) PH0 printed wall (registered) | EDT | **+3.05** | +2.35 … +3.77 | +1.36 | +4.36 | +3.03 ± 0.66 | 26/32 | 0 |
| PH8 seeds | (ii) PH0 printed wall | mesh | +2.89 | +2.19 … +3.77 | +1.25 | +4.34 | +2.95 ± 0.68 | 24/32 | 0 |
| PH8 seeds | (ii) at the air/plastic mid-point threshold (−453 HU) | EDT | +2.86 | +2.22 … +3.57 | +0.93 | +4.19 | +2.83 ± 0.67 | 25/32 | 0 |

Per-seed values vs the PH0 wall (EDT, sorted, mm): 1.4, 1.8, 2.3, 2.3, 2.4,
2.4, 2.5, 2.6, 2.7, 2.7, 2.7, 2.7, 2.9, 2.9, 3.0, 3.0, 3.1, 3.1, 3.2, 3.2,
3.3, 3.3, 3.3, 3.5, 3.5, 3.7, 3.7, 3.7, 3.8, 3.8, 4.1, 4.4 (1 mm bins: [1, 2)
2, [2, 3) 13, [3, 4) 15, [4, 5) 2). Paired (ii) − (i) per seed — the
segmented tile's thickness on the lumen side of the plastic: median 3.46 mm,
mean 3.63 ± 1.13 mm, range 1.2–6.1 mm (nominal tile thickness 4.0 mm; the
body mask at −300 HU cuts into the partially hydrated collagen, which sits
at −250…+100 HU).

Reading. Against the clean printed wall the seeds sit at the model's 3 mm
(median 3.05 mm, 81 % in the band; the mid-point-threshold surface, 0.2 mm
further into the plastic's partial-volume edge, gives 2.86 mm — the
threshold choice is a ±0.1 mm effect). Against gtcore's PH8 body surface
the same seeds read −0.68 mm: the tile is part of the body mask, so that
surface is the tile's free face, nominally 1 mm on the lumen side of the
seed plane (4 mm tile − 3 mm offset). This is the 0.84 mm "localized seeds
off the mesh" of `docs/optimize-notes.md` (V6, 2026-10-07): the number was
right, the reference surface was the tile face, not the wall. A conformer
that snaps a tile to that mesh and puts its seeds 3 mm from it places them
≈ 3.7 mm from the plastic — the 3.94 mm "conformer substitution shift"
reported there is this 1 mm reference offset plus the pose change, not a
3 mm placement error. Nothing about the phantom placement contradicts the
model.

### Decision (rev 3 F8) and draft text

**Decision.** The clinical distribution centres inside the 2.25–3.75 mm
band (median 3.65 / 3.57 mm), so per rev 3 the model holds in vivo and
`SEED_PLANE_OFFSET_MM` = 3.0 mm and every stage citing it (`tiles.deform`,
`tiles.surface`, `tiles.auto`, `interact.SEED_WALL_OFFSET_MM`,
`phantom.generate.SEED_INSET_MM`, `segment.cavity.SHEET_TOL_MM`) stay as
they are; the optimizer figures (F8) may proceed. The phantom "offset" is
not a placement artifact to describe but a reference-surface artifact to
state (below). No constant was changed in this task.

**Methods sentence (used; tile model paragraph).** "Seeds are modelled
3.0 mm from the tile's tissue face (manufacturer; 2.25–3.75 mm when
hydrated [Ferreira et al.]). We did not fit this value: on the clinical
case the 32 localized seeds lie a median 3.6 mm (10th–90th percentile
1.7–5.5 mm) inside the physician's post-operative cavity contour, the 32
TPS source positions 3.6 mm (1.7–5.2 mm), and on the printed phantom the
32 seeds lie 3.0 mm (2.4–3.8 mm) off the printed wall of the tile-less twin
after rigid registration (surface residual 0.3 mm median), consistent with
the device geometry; distances are signed Euclidean distances to the
rasterised surface, positive towards the lumen."

**Methods sentence (phantom reference surface; Methods, phantom paragraph
or F8/V6 caption).** "On the phantom the pipeline's wall mesh is the surface
of the body mask, which includes the hydrated collagen tiles; seeds
therefore read 0.7 mm below that surface (the tile's free face), while they
sit 3.0 mm off the plastic measured on the tile-less twin. Phantom distances
quoted to 'the wall' use the twin's printed surface."

**Alternative Methods sentence (drafted as requested; NOT used — the data
do not support it).** "The seeds lie a median 4.6 mm inside the contoured
cavity rather than 3 mm because the seed plane is 3 mm from the tile face
while the drained post-operative cavity contour sits a further ~1.5 mm
outside the tile–tissue interface; the 3 mm seed-plane offset is a device
constant and was not re-tuned to the contour." — The measured centre is
3.6 mm, not 4.6 mm; the residual 0.6 mm above nominal is within the
hydrated band and within the contouring pitch, so no such sentence is
needed. If a reviewer asks about the 0.6 mm: it is the sum of hydration
(up to +0.75 mm by the band), the 1 mm contour pitch and tiles that do not
lie flat (the +5…+7 mm tail), and it is in the direction expected for a
contour drawn around the tile surface rather than through it.

**Table 2 row (parameters).** `SEED_PLANE_OFFSET_MM` | 3.0 mm (hydrated
2.25–3.75 mm) | source: device geometry (manufacturer; Ferreira et al.) |
calibration set: **none** (not fitted) | check: median seed-to-`Cav_Post`
distance 3.65 mm (gtcore seeds) / 3.57 mm (TPS sources) on PT-CT1mm +
PT-RT; 3.05 mm seed-to-printed-wall on PH8 vs PH0 — both inside the band;
`scripts/seed_wall_distance.py`, `docs/figures/data/seed_wall_summary.csv`.

**Results/caption sentence (if the spread is shown, e.g. F4b caption or
Supplement).** "Signed seed-to-cavity-surface distance, positive towards
the lumen: clinical 3.6 mm median (1.7–5.5 mm, n = 32; 13 of 32 inside the
2.25–3.75 mm hydrated band, one seed 2.1 mm outside the contour, five seeds
more than 5 mm inside it where tiles overlap or lift off the wall); phantom
3.0 mm (2.4–3.8 mm; 26 of 32 in band)."

### Outputs

`docs/figures/data/seed_wall_distance.csv` (192 rows: 32 seeds × {gtcore
seeds, TPS sources} × {`Cav_Post`, gtcore cavity} + 32 × {PH8 body, PH0
wall}; columns dataset, seed_set, seed_id, x/y/z RAS, reference,
signed_distance_mm (EDT), signed_distance_mesh_mm, in_band, and for TPS
sources matched_gtcore_seed + match_distance_mm; TPS `seed_id` = the matched
gtcore seed id), `seed_wall_summary.csv` (13 rows), `seed_wall_histogram.csv`
(0.5 mm bins, −6…12 mm). Header comments: script, command with dataset ids,
commit `448a6b3`, date, flag, sign convention, both methods, references,
registration parameters and residual, plastic HU, runtime (104 s total).
Committed with `git add -f` (`docs/figures/data/` is git-ignored).

### Commands

```
# from the p2-seed-wall worktree root, GTCORE_STRICT=1, PYTHONPATH=<worktree>; gtcore.__file__ printed = <worktree>\gtcore\__init__.py
python scripts/seed_wall_distance.py --ct "$GT_DATA_ROOT/<PT-CT1mm>" --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT>" \
    --rtdose "$GT_DATA_ROOT/<PT-RT RTDOSE>" --ph8 "$GT_DATA_ROOT/<PH8>" --ph0 "$GT_DATA_ROOT/<PH0>"      # 104 s at 448a6b3
GTCORE_STRICT=1 python -m pytest -q tests/test_seed_wall_distance.py      # 6 passed in 5-6 s
GTCORE_STRICT=1 python -m pytest -q                                       # full suite, see below
```

### Tests

`tests/test_seed_wall_distance.py`: 6 passed (5.2–6.4 s) at `448a6b3`.
Full suite at `448a6b3`: `GTCORE_STRICT=1 python -m pytest -q` from the worktree root: **947 passed, 14 skipped**, 15245 warnings in 675.26 s (0:11:15), run concurrently with the real-data run and other agents (wall time is an upper bound). Baseline before this task (e13a4af): 941 passed, 14 skipped; the 6 new tests are this file.

### Notes for the coordinator / open points

- **Phase 4 V6 (phantom arm) consequence, not acted on.** The phantom wall
  mesh (`meshes["body"]`, `wall_mesh_for`) is the tile-including body
  surface. The `as_placed` arm scores the localized seeds where they are
  (fine); the conformer arm and every optimizer arm put seeds 3 mm from the
  *tile face*, ≈ 3.7 mm from the plastic, and the "conformer substitution"
  line (+1.75 pp V100) partly measures this reference offset. Options for
  Phase 4: conform/optimize on the registered PH0 wall (the clean printed
  surface; this script's `register_rigid` + `transform_points_ras` give the
  transform), or on a PH8 body mask with the tiles removed (not available
  without a tile segmentation — stop-list adjacent). Coordinator's call;
  nothing in `gtcore` was changed here.
- The v6 figure-plan number "median 4.6 mm (10–90 % 2.7–5.9)" is
  superseded by 3.65 mm (1.70–5.46); `docs/paper2-decisions.md`'s v6 entry
  ("seed-to-`Cav_Post` distance (median 4.6 mm, mock-level) stated in
  Methods") should be read with this section.
- Sign-convention wording in the task brief (clinical "positive = outside
  the cavity") was not followed; one convention (cavity side positive) is
  used for every reference, as the brief's "identical to the clinical case"
  requires. The CSV header states it.
- Per-seed accuracy: the brief's "within 0.2 mm" holds for the
  distribution (median/mean, every grid, every offset) and the sign of
  every seed; a single seed is good to half the slice spacing (binary mask
  on 1 mm slices). The test pins exactly that.
- Nothing installed. No shared files touched (`pyproject.toml`,
  `tests/conftest.py`, `README.md` untouched). Files: `scripts/seed_wall_distance.py`,
  `tests/test_seed_wall_distance.py`, `docs/figures/data/seed_wall_{distance,summary,histogram}.csv`,
  this section.

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

## 2026-11-03 — Phase 3 — leftover-seed diagnostic, count-known N = 8, marginal-score curves on real scans (task `leftovers`, branch `p2/leftovers`)

Base `paper2/integration` e13a4af. Commits: `788df9d` (`scripts/leftover_seeds.py`,
`scripts/validation_autogen.py` real-scan curves, `tests/test_leftover_seeds.py`),
`e977540` (autogen `--no-synthetic` summary guard; `marginal_score_real.csv` was
regenerated at this commit), `afb96ea` (leftover × tile layer table, nearest tile
by sheet distance, patches clamped to the crop; every `f4_*` CSV, both NPZ files
and the patches were regenerated at this commit), then the data commit and this
section. Requirements rev 3 §3 F4 (diagnostic, count-known row, real-scan
curves), §4 items 13 and 18, §6 Phase 3 sixth checkbox. Everything ran with
`GTCORE_STRICT=1` and `PYTHONPATH` = the worktree (the driver prints the tree it
imported: `p2-leftovers`). Hardware: Ryzen 5 7600X, 31 GB; three or four jobs
ran concurrently, so every wall time below is an upper bound. **No parameter
was changed and no model was extended.**

### Commands

```
GTCORE_STRICT=1 python scripts/leftover_seeds.py "$GT_DATA_ROOT/<PT-CT1mm>" --dataset PT-CT1mm \
    --rtstruct "$GT_DATA_ROOT/<PT-RT RTSTRUCT>" --n-full 8 --out docs/figures/data
GTCORE_STRICT=1 python scripts/leftover_seeds.py "$GT_DATA_ROOT/<PH8>" --dataset PH8 --n-full 8 --out docs/figures/data
GTCORE_STRICT=1 python scripts/validation_autogen.py --no-synthetic \
    --phantom8 "$GT_DATA_ROOT/<PH8>" --ct1mm "$GT_DATA_ROOT/<PT-CT1mm>"      # -> docs/figures/data/marginal_score_real.csv
python -m pytest -q tests/test_leftover_seeds.py
```

Outputs (all `git add -f`, every CSV with a `#` header naming script, command,
commit, dataset): `docs/figures/data/f4_leftover_{modes,tiles,seeds,layers,
pairwise_mm,pairwise_axis_deg,quads,pairs,triplets}_<dataset>.csv`,
`f4_leftover_finding_<dataset>.json`, `leftover_patches/PT-CT1mm_seed##.png`
(8 files, 300 dpi), `f4b_patient_implant.npz` + `.json`, `f4c_phantom_implant.npz`
+ `.json`, `marginal_score_real.csv`.

### What the script does

Three fits on the SAME 32 shape-filtered, vault-filtered seeds of
`reconstruct` (seed order identical between the two pipeline passes, asserted):
`auto` = `reconstruct(vol, n_full_tiles="auto")` (count-free, bent-tile score,
cover pass, surface cross-check on the gtcore cavity mesh); `count8` =
`reconstruct(vol, n_full_tiles=8)` (the pipeline's count path: exact chord-scored
selector + degraded completion, no mesh); `prior8` =
`fit_tiles_prior(ImplantPrior(8), mesh=cavity, spacing)` (the planner's
count-known fit, as `scripts/strict_pipeline_check.py` pass 3). For each tile
the chord residual, the bent-tile RMS (`fit_deformable`, attached by the fit or
computed here for chord-path tiles), bending energy, fold angle, axis error,
degraded / tentative flags and the surface verdict; for the leftovers of the
primary (`auto`) mode the pairwise distance and axis-angle matrices, a per-seed
row (PCA axis, nearest-neighbour distances, detection blob volume / elongation /
split count / fragment count, peak HU within 1 mm on the raw volume, unsigned
distance to the wall mesh and to `Cav_Post`, nearest fitted tile and the seed's
`(u, v, h)` in that tile's bent-tile frame with a layer class), every
4-combination against each gate of the standard tier (`tiles/fit.py`), the loose
and cover tiers (`tiles/auto.py`) and the count-completion rule, with the
bent-tile fit and `deformable_score` computed for all of them, every pair against
the half-tile gates, every triplet against the cover pass's L-shape windows,
single-linkage clusters at the loose chord ceiling (16.5 mm), and a 20 mm
axial / coronal / sagittal patch per leftover seed (nearest voxel on a 0.25 mm RAS
grid, soft tissue W 400 / L 40 and metal W 3000 / L 1500, other seeds within
1.5 mm of the plane circled, red = leftover).

### PT-CT1mm — the three modes (commit `afb96ea`; 204 × 512 × 512, 0.52 × 0.52 × 1.0 mm; 32 seeds from 57 raw blobs, vault filter applied, 21 dropped)

| Mode | Tiles | Degraded | Tentative | Seeds assigned | Leftovers | Summary | Wall (upper bound) |
|---|---|---|---|---|---|---|---|
| `auto` (count-free) | 6 | 0 | 0 | 24 / 32 | 8: {0, 2, 5, 8, 11, 13, 16, 21}, all inside the implant region, 0 clutter | "evidence supports n = 6 (no further tile candidate); 8 seeds unassigned" | tile fitting 41.0 s (pipeline 62.9 s) |
| `count8` (pipeline, chord) | 7 | 1 ({0, 8, 13, 16}, completion) | 0 | 28 / 32 | 4: {2, 5, 11, 21} | — | fit 0.01 s (pipeline 26.8 s) |
| `prior8` (planner, deformable, mesh = gtcore cavity 24,736 faces) | 7 | 1 (same group) | 0 | 28 / 32 | 4: same | "asked for 8 full tile(s): 7 supported (geometry supports only 7); 4 seeds unassigned" | 46.5 s |

This makes the Phase 1 numbers (7 tiles, 1 degraded completion, 3 unassigned
at 31 seeds) definitive at 32 seeds: **count-free 6/8 with 8 leftovers;
count-known 7/8 (one degraded completion) with 4 leftovers.** The six
supported tiles are the same seed groups in every mode:

| Tile (auto id) | Seeds | Chord residual (auto / count8) mm | Bent-tile RMS mm | Bending energy mm⁻² | Fold ° | Axis err ° | Surface cross-check (free vs wall-conformed fit, detachment) |
|---|---|---|---|---|---|---|---|
| 0 | 14, 15, 18, 19 | 0.18 / 0.46 | 0.18 | 0.0003 | 18 | 8.1 | inconsistent 4.36 mm, detachment 0.54 mm |
| 1 | 6, 7, 9, 12 | 0.21 / 0.27 | 0.21 | 0.0006 | 27 | 2.6 | inconsistent 5.76, 0.10 |
| 2 | 17, 22, 24, 30 | 0.29 / 0.19 | 0.29 | 0.0029 | 53 | 7.5 | inconsistent 3.40, 0.81 |
| 3 | 1, 3, 4, 10 | 0.47 / 0.70 | 0.47 | 0.0009 | 25 | 4.2 | inconsistent 6.14, 0.69 |
| 4 | 20, 25, 28, 31 | 0.51 / 0.60 | 0.51 | 0.0026 | 52 | 10.3 | inconsistent 3.70, 0.17 |
| 5 | 23, 26, 27, 29 | 0.67 / 1.15 | 0.67 | 0.0104 | 116 | 6.6 | inconsistent 5.83, 0.10 |
| count8 / prior8 tile 6 (degraded completion) | 0, 8, 13, 16 | 3.13 | 1.36 | 0.203 | 515 (κ at its bound) | 13.6 | inconsistent 4.28, 0.95 |

(Every patient tile reads "inconsistent": the seeds sit at the 3 mm wall offset
— detachment 0.1–0.8 mm — but the Nelder–Mead wall-conformed tile lands 3.4–6.1 mm
from the free fit. Pre-existing behaviour, not touched; see the coordinator
notes below.)

### PT-CT1mm — the 8 leftover seeds (count-free mode)

All eight are **TPS-contoured seeds**: Hungarian match of the 32 detections to the
30 `Seeds` contour centroids (2 mm gate) gives 30/30, the leftovers at 0.03–0.59 mm
from their contour; the two uncontoured plan sources are seeds 10 and 17, both
inside fitted tiles (3 and 2). None is a merged blob or a fragment
(`split_k` = 1, `n_fragments` = 1 for all 32); none is a detection miss (32
detections = 32 plan sources, Phase 1). They are, if anything, the brighter
capsules: peak HU 2805–3070 (median 3049; assigned seeds 2115–3071, median
2918), blob volume 4.6–8.1 mm³ (assigned 2.4–7.6), elongation 2.7–3.5 (assigned
2.2–5.0). The patches show one capsule-shaped blob per seed; seeds 8 and 16
are two distinct capsules 4.7 mm apart (patch `PT-CT1mm_seed16.png`, sagittal).

| Seed | RAS (mm) | PCA axis | NN any (seed) | NN leftover (seed, axis angle) | NN assigned (seed) | Nearest tile: sheet dist, (u, v, h) mm, class | to `Cav_Post` / gtcore wall (mm, unsigned) | peak HU | blob mm³ |
|---|---|---|---|---|---|---|---|---|---|
| 0 | (29.9, 224.8, −508.0) | (−0.20, −0.55, 0.81) | 8.9 (16) | 8.9 (16, 33°) | 9.3 (4) | tile 3: 4.6, (−12.9, −7.1, −2.7), edge (2.9 mm beyond) | 1.3 / 3.1 | 3067 | 8.1 |
| 2 | (47.8, 199.3, −510.8) | (−0.52, 0.47, −0.71) | 9.6 (5) | 9.6 (5, 5°) | 10.2 (22) | tile 2: 5.0, (12.9, 9.9, +1.1), sheet (2.9 beyond); tile 0: 5.0, h −2.0 | 3.2 / 0.2 | 3066 | 7.6 |
| 5 | (42.8, 204.3, −517.4) | (−0.53, 0.54, −0.66) | 7.6 (21) | 7.6 (21, 36°) | 7.7 (18) | tile 0: 2.8, (12.4, 4.4, +0.4), sheet (2.4 beyond) | 4.8 / 1.2 | 3067 | 7.0 |
| 8 | (30.9, 219.7, −500.2) | (−0.40, 0.55, −0.74) | 4.7 (16) | 4.7 (16, 37°) | 11.5 (20) | tile 4: 6.2, (−8.2, −16.1, +2.4), edge (6.1 beyond) | 3.5 / 2.9 | 3070 | 6.7 |
| 11 | (39.5, 218.1, −525.8) | (−0.25, 0.85, −0.46) | 5.9 (3) | 9.0 (21, 10°) | 5.9 (3) | **tile 3: 3.1, (7.5, −1.4, −3.5), inside the footprint: stacked** | 2.9 / 0.05 | 3032 | 6.5 |
| 13 | (34.0, 209.2, −500.2) | (−0.26, 0.82, −0.51) | 7.9 (30) | 9.3 (16, 18°) | 7.9 (30) | tile 5: 3.5, (−12.8, −6.6, +3.9), edge (2.8 beyond) | 4.8 / 1.7 | 2913 | 6.2 |
| 16 | (33.3, 217.8, −503.7) | (−0.01, −0.91, 0.42) | 4.7 (8) | 4.7 (8, 37°) | 8.7 (20) | tile 4: 3.4, (−10.2, −11.9, +2.3), edge (1.9 beyond) | 3.0 / 5.4 | 2805 | 5.1 |
| 21 | (40.9, 210.2, −521.8) | (−0.08, 0.89, −0.44) | 7.6 (5) | 7.6 (5, 36°) | 8.2 (3) | **tile 3: 1.9, (9.8, −10.1, −3.2), at the footprint corner (0.08 mm beyond), beneath the sheet** | 5.0 / 2.2 | 2890 | 4.6 |

`h` is along the tile's bent-tile normal, positive toward the cavity; a tile laid
on top of a fitted tile would put its seeds inside the footprint at |h| ≈ 4 mm.

Pairwise distances (mm) between the leftovers:

| | 0 | 2 | 5 | 8 | 11 | 13 | 16 | 21 |
|---|---|---|---|---|---|---|---|---|
| 0 | — | 31.3 | 26.0 | **9.5** | 21.2 | 18.0 | 8.9 | 22.9 |
| 2 | | — | **9.6** | 28.6 | 25.4 | 20.1 | 24.6 | 16.9 |
| 5 | | | — | 26.0 | 16.6 | 19.9 | 21.5 | 7.6 |
| 8 | | | | — | 27.1 | 10.9 | 4.7 | 25.6 |
| 11 | | | | | — | 27.7 | 22.9 | **9.0** |
| 13 | | | | | | — | **9.3** | 22.7 |
| 16 | | | | | | | — | 21.1 |

Pairwise PCA-axis angles (°): 2–5 5, 11–13 4, 16–21 6, 2–8 8, 5–8 9, 11–21 10,
13–21 12, 11–16 16, 13–16 18, 8–13 21, 5–13 24, 8–11 25, 2–13 27, 5–11 27,
2–11 31, 8–21 32, 0–16 33, 0–21 33, 0–8 35, 0–13 36, 5–21 36, 0–11 38, 2–21 39,
5–16 41, 0–2 43, 0–5 44, 2–16 44 (full matrix in `f4_leftover_pairwise_axis_deg_PT-CT1mm.csv`).

**Gates.** Of the 70 leftover quads, 0 pass the standard tier, 0 the loose
(crumpled-tile) tier, 0 the cover tier, 1 the count-completion rule
({0, 8, 13, 16}); 0 of 56 triplets pass the L-shape windows; 7 of 28 pairs pass
the half-tile gates: 11–21 (9.0 mm, axes 10°, score 3.92), 13–16 (9.3, 18°,
3.86), 2–5 (9.6, 5°, 3.80), 0–8 (9.5, 35°, 3.38), 5–21 (7.6, 36°, 3.31), 0–16
(8.9, 33°, 3.06), 8–13 (10.9, 21°, 2.82). The best-scoring quad by the bent-tile
rule is {5, 13, 16, 21}: score −0.02 (penalty λ = 3.5; a quad needs > 0 to be a
candidate at all), bent RMS 4.01 mm, E 0.010, fold 92°, axis error 5°; chords
7.6 / 9.3 / 19.9 / 21.1 / 21.5 / 22.7 mm — rejected by the standard tier on
`std_link` (chords outside [6.3, 16] mm), `std_side_window` (sides outside
[6.3, 11.8]), `std_diag_window` (diagonals outside [9.8, 16]),
`std_grid_residual` (5.0 > 2.0 mm) and `std_chord_score_pos`; by the loose
tier on `loose_chord` (outside [3.5, 16.5]), `loose_sim_rms` (5.0 > 2.0),
`loose_def_rms` (4.0 > 0.8) and `loose_score_pos`; by the cover tier on the
same four; by completion on `completion_chord` (> 18 mm). The only quad any
rule admits, {0, 8, 13, 16} (chords 4.7 / 8.9 / 9.3 / 9.5 / 10.9 / 18.0 mm), has
score −0.21, bent RMS 1.36 mm, E 0.203 and fails `loose_def_rms` (1.36 > 0.8),
`loose_energy` (0.203 > 0.10), `cover_def_rms` (1.36 > 1.2) and `cover_energy`
(0.203 > 0.20); the count-known modes accept it only through the
count-constrained completion (`degraded=True`).

**Clusters.** Single linkage at 16.5 mm splits the leftovers into A = {0, 8, 13,
16} (max separation 18.0 mm, plane RMS 0.64 mm) and B = {2, 5, 11, 21} (max
separation 25.5 mm, plane RMS 0.10 mm: four seeds almost on one line, 2–5–21–11
at 9.6 / 7.6 / 9.0 mm), 19.9 mm apart from each other. **Neither cluster can be
one intact tile, however crumpled:** the collagen bends but does not stretch,
and the farthest two seeds of a 20 mm tile are its grid diagonal, 14.1 mm
(every chord window in the code tops out at 16–16.5 mm); 18.0 and 25.5 mm are
beyond that. What the leftovers do contain is **four 2-seed pairs at the
manufactured pitch** — 11–21 (9.0 mm), 13–16 (9.3), 0–8 (9.5), 2–5 (9.6), axes
within 5–35°, each a half-tile geometry — i.e. two tiles' worth of seeds as
four half tiles, with pair {11, 21} lying 3.2–3.5 mm beneath the seed sheet of
tile 3 inside its footprint (stacked under it), pair {2, 5} in the plane of
tiles 0 / 2 just beyond their edges, and pairs {0, 8} and {13, 16} side by side
(seeds 8 and 16 4.7 mm apart) in the gap between tiles 3, 4 and 5 at the same
layer as those tiles. Three model-free checks from the committed NPZ (no
pipeline re-run; `seed_centers_ras` / `seed_axes_ras` / `cavity_center_ras` /
`volume_spacing_mm`, existing fitter, nothing tuned):

| Call (existing code) | Result |
|---|---|
| `fit_tiles(c, a, n_full=7, n_half=2, cavity_center, complete_degraded=True)` | 6 full (the same six) + halves {11, 21} (0.40 mm), {13, 16} (0.71 mm); rejected {0, 2, 5, 8}; the degraded completion no longer fires (its pool is split) |
| `fit_tiles_prior(c, a, ImplantPrior(n_full=7, n_half=2), cavity_center, spacing)` | same: 6 supported full + 2 half, 4 unassigned |
| `fit_tiles(c, a, "auto", n_half=1 [= allow half tiles], cavity_center, spacing)` | 6 full + 3 half ({11, 21}, {13, 16}, {2, 5}); unassigned {0, 8}: that pair scores 3.38 < λ = 3.5 because its seed axes differ by 35°; penalised curve marginals n = 7..10: 3.92, 3.86, 3.79, 3.38; the per-count search hit its node cap with halves allowed (`capped`) |

**Draft Results sentence (F4):** *On the clinical thin-cut CT the count-free
search recovered 6 of the 8 implanted tiles (bent-tile RMS 0.18–0.67 mm) and
left 8 seeds unassigned; all 8 are TPS-contoured, intact capsules (peak
2805–3070 HU; no merged blob, no detection miss — 32 detections for 32 plan
sources) that form four 2-seed pairs at the manufactured pitch (9.0–9.6 mm) whose
cross-pair separations (16.6–25.5 mm) exceed the 14.1 mm an intact 20 mm tile can
span, i.e. two tiles were cut into halves at surgery (one half lies 3.2–3.5 mm
beneath a fitted tile); the count-free search excludes half tiles by design
without the operative record, and the count-known N = 8 fit reads two abutting
halves as a crumpled seventh tile.* (Cause named: **half tiles**, not stacked
full tiles, merged blobs or detection misses.)

**Finding for Jacob (requirements §4 item 18 / the stop list):** the leftovers do
NOT hold two coherent full tiles' worth of geometry, so a two-layer (stacked
tile) model extension is not warranted; the geometry says 6 full + 4 half
tiles (32 seeds). The operative record should be checked: were two tiles cut?
If so, (i) the F4(b) render shows 6 fitted tiles + 4 leftover pairs (the NPZ
carries the pairs through `auto_seed_leftover`; the pairings above are in
`f4_leftover_pairs_PT-CT1mm.csv`); (ii) the count-known Table 3 row is "8 full
requested: 7 recovered, of which 1 degraded completion that the diagnostic
shows to be two abutting halves; 4 seeds unassigned", and a
`fit_tiles_prior(ImplantPrior(n_full=6, n_half=4))` row would be the honest
count-known line (not run here: it is a new count input, Jacob's call); (iii)
the Methods statement "half tiles are not selected without the operative
record" is the reason count-free stops at 6, which is the result and the
limitation rev 3 §3 F4 anticipated. The dose comparison (F5) is unaffected:
it uses the 32 seeds, not the tiles.

### PH8 — the three modes (commit `afb96ea`; 157 × 512 × 512, 0.59 × 0.59 × 1.0 mm; 32 seeds = 32 raw blobs, vault filter skipped — no skull)

| Mode | Tiles | Degraded | Seeds assigned | Leftovers | Wall (upper bound) |
|---|---|---|---|---|---|
| `auto` | 8 (8 supported) | 1 ({12, 19, 21, 27}: the crumpled tile, bent RMS 0.46 mm, E 0.066, fold 288°) | 32 / 32 | 0 | tile fitting 2.2 s (pipeline 28.9 s) |
| `count8` (chord) | 8 | 1 (same) | 32 / 32 | 0 | fit 0.01 s (pipeline 30.5 s) |
| `prior8` (mesh = body shell, 72,396 faces) | 8 | 1 (same) | 32 / 32 | 0 | 80.8 s |

Bent-tile RMS of the seven standard tiles 0.26–0.87 mm (auto / prior8). The
chord-scored `count8` partition differs from the two deformable-scored modes in
two tiles: it groups {25, 26, 29, 30} and {8, 16, 24, 31} (chord residuals
1.18 / 1.37 mm; bent RMS 1.11 / 1.47 mm) where `auto` / `prior8` group
{26, 29, 30, 31} and {8, 16, 24, 25} (bent RMS 0.87 / 0.78 mm) — seeds 25 and 31
swap. Both are 8/8; the deformable rule prefers the auto partition by 0.3–0.7 mm
of RMS on each tile. Reported, not changed (`reconstruct(n_full_tiles=N)` is the
chord path by design; the planner path agrees with auto). The `prior8` surface
cross-check against the body shell reads "detached" for 7 of 8 tiles (mean
|seed-to-mesh distance − 3 mm| of 1.9–2.8 mm) — the shell mesh is the printed
object's surface, the seed-wall task's territory; pre-existing.

Full suite at `788df9d` (`GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider`
from the worktree, four jobs concurrent): **`950 passed, 14 skipped, 15243
warnings in 865.73s (0:14:25)`**, exit 0 (the 14 skips are the Phase 1 set).
The later commits (`e977540`, `afb96ea`) touch only `scripts/` and
`tests/test_leftover_seeds.py` (10 passed at `afb96ea`, 5.3 s); the final-tree
suite line is appended below when it finishes.

### Marginal score vs n on the real scans (`marginal_score_real.csv`, commit `e977540`)

Count-free fit, bent-tile scores, λ_full = 3.5; `marginal` = score(n) − score(n−1):

| n | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|
| PH8 (32 seeds, 8 tiles placed) | 7.49 | 7.38 | 7.21 | 7.01 | 6.87 | 6.75 | 5.70 | 5.67 | — (no candidate) |
| PT-CT1mm (32 seeds, 8 tiles implanted) | 7.63 | 7.59 | 7.43 | 7.07 | 6.98 | 6.66 | — (no candidate) | — | — |

Selected n: PH8 8 (penalised score 26.07), PT-CT1mm 6 (22.35). **In neither
scan does the marginal ever fall below λ: the curve ends because no further
disjoint full-tile candidate exists in any tier** (PH8 after its 8th tile,
PT-CT1mm after its 6th — the leftovers form no quad, see above). For the
Supplement caption: the plateau is a termination, not a λ crossing; the λ line
is still drawn (column `lambda_full`) because it is what would have stopped a
weaker candidate. Synthetic curves are unchanged (`validation_autogen.py`
without `--no-synthetic`); `PT-CT2mm` is skipped with a message while the folder
is missing.

### Render data for F4(b) / F4(c) / F8(a) (`f4b_patient_implant.npz`, `f4c_phantom_implant.npz`, each with a JSON index documenting every array: shape, dtype, units, meaning)

Per file: `seed_centers_ras` (32, 3), `seed_axes_ras` (32, 3), seed length /
diameter, blob volume and elongation, `cavity_center_ras`, grid spacing and
shape, `wall_mesh_vertices/faces` (the mesh the fits conformed to:
PT-CT1mm = gtcore cavity, 12,274 vertices / 24,736 faces; PH8 = body shell,
72,396 faces), for PT-CT1mm `cavity_rt_vertices/faces` (= `Cav_Post`,
35.1 cc on the CT grid via `gtcore.io.rt.rasterize_roi`, marching cubes at
step 1 on the mask's bounding box, Taubin-smoothed: 34,652 vertices / 69,324
faces), `sheet_faces` (shared 9 × 9 grid topology, 128 triangles) and, per mode
(`auto_`, `count8_`, `prior8_`): `seed_tile_id` (−1 = leftover),
`seed_leftover`, tile id / kind / confidence / degraded / member seeds /
inferred seed / centre / normal / axis / chord residual / bent RMS / bending
energy / fold / axis error, `tile_model_normal_toward_cavity`, the three
deformed quad sheets `sheet_seed_vertices` (the seed sheet), `sheet_tissue_vertices`
(−3 mm, on the wall) and `sheet_cavity_vertices` (+1 mm) as (T, 81, 3), the
tissue-face corners, the model seed positions, and (when a mesh existed) the
wall-draped footprint corners, conformed seed centres and verdict. The
renderer needs nothing else and never re-runs the pipeline. Primary mode for
F4(b)/F4(c)/F8(a): `auto` (6 tiles + 8 leftover seeds in red; PH8 8 tiles with
`auto_tile_degraded` marking the crumpled one). **Recommendation for the
renderer:** draw the bent-tile sheets (`*_sheet_*`), not the wall-draped corners,
until the free-vs-surface disagreement above is understood.

### Tests

`tests/test_leftover_seeds.py` (10 tests, 5.3 s): on the synthetic head phantom
(0.8 mm, 3 tiles, rng 2; 12 seeds detected) every mode assigns all 12 seeds to the
true partition with zero leftovers and empty diagnostic tables; with four
scattered decoys appended (> 18 mm from everything, random axes) every mode
leaves exactly those four, classed as clutter / `far`, the single leftover quad
fails `std_link`, `loose_chord`, `cover_chord` and `completion_chord` (its
bent-tile score is still reported), all 6 pairs fail the chord window, all 4
triplets fail, the cluster verdict is "no leftover quad passes any tier"; a true
tile's quad passes every standard gate with score > λ; the layer classes
(`sheet` for a tile's own seed, `stacked` for a point 4 mm above a fitted sheet);
the NPZ export documents every array in its JSON with matching shapes, the seed
sheets pass within 2 mm of the fitted seeds and the tissue sheet is 3.0 mm from
the seed sheet; the patch PNG is written. Full suite: see the line appended
below.

### Notes for the coordinator

- Shared files: none. `scripts/validation_autogen.py` lost its two machine-path
  defaults (`DEFAULT_PHANTOM8` / `DEFAULT_POSTOP`; folders are now given on the
  command line), its real-case labels became dataset ids (`PH8`, `PT-CT1mm`,
  `PT-CT2mm`) and it gained `--ct1mm`, `--no-synthetic`, `--marginal-csv`.
- `scripts/leftover_seeds.py` is importable (the tests load it by path); its
  functions take seed arrays and a `ModeFit`, so Phase 5 can call them on the NPZ.
- Pre-existing behaviour reported, not changed: (i) every PT-CT1mm tile's
  surface cross-check reads "inconsistent" (free vs wall-conformed fit 3.4–6.1 mm
  apart, seeds attached at 0.1–0.8 mm); (ii) PH8 `count8` (chord) vs `auto` /
  `prior8` (deformable) differ in two tiles (25 ↔ 31); (iii) with half tiles
  allowed the count-free per-count search hits its node cap on PT-CT1mm.
- `docs/clinical-validation-notes.md` §2 still says "the 8 leftover seeds form no
  square quads (crumpled or stacked tiles the bent-tile model does not explain)";
  the sentence above supersedes it (Phase 5 wording scrub).

Full suite on the final tree (`b20ae00`, same command, one job): **`951 passed,
14 skipped, 15243 warnings in 593.86s (0:09:53)`**, exit 0 — 950 of `788df9d` plus
the layer-class test added at `afb96ea`.

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
GTCORE_STRICT=1 python -m pytest -q    # 966 passed, 14 skipped, 15242 warnings in 501.61s (0:08:21) at 538e3f6 (980 collected = the Phase 2 tree + 25 new; other agents ran concurrently, wall time is an upper bound)
```

### Notes for the coordinator

- No shared file was touched (`pyproject.toml`, `tests/conftest.py`, `README.md`, `gtcore/dose/__init__.py` unchanged); `gtcore.dose.formalism` is imported by its full path. `scripts/validation_clinical.py` was not edited (owned by `clinical-rebuild`); it can import `angular_symmetry_test`, `rtdose_source_positions`, `match_sources_to_seeds` and `fit_sources_to_reference` from `gtcore.dose.formalism`.
- Decision needed: which seed-axis convention the F5 / gamma comparison against the RTDOSE uses (physical PCA axes vs the TPS convention, all along S-I) -- see the Conclusion above. Either way the paper has to state that the TPS computed with S-I-aligned seeds.
- Rev 3's "a 1D plan would give 1.29 -> 0.96" needs the amendment stated above (far field only).
- Missing data: the RTPLAN (orientations, per-seed S_K, private calc settings); the documented command above runs the comparison when it arrives. Nothing was installed.
- `docs/figures/data/` did not exist before this branch; `data/` is git-ignored, so the four CSVs were added with `git add -f`.
