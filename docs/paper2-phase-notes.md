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
| `3921ee0`, re-based onto the shared notes file in the follow-up commit | this notes section |

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
runs but is a no-op at these commits (the flag is introduced by the
hidden-exceptions task on another branch).

| Run | Commit | Interpreter | Command | Result | Runtime |
|---|---|---|---|---|---|
| Baseline, shared venv, worktree | `aff83c0` (test modules collected before this branch's edits) | shared venv, Python 3.12.10, numpy 2.5.2 | `GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider` | `815 passed, 14 skipped, 15122 warnings in 558.68s (0:09:18)` | wall 9 min 21 s |
| Edited file only, shared venv | working tree of `a5d3d13` | shared venv | `GTCORE_STRICT=1 python -m pytest -q tests/test_tiles_fuse.py` | `13 passed in 16.84s` | — |
| **Clean clone, fresh venv from the lock file** | `9ebb13d` | `<scratchpad>/clonenv`, Python 3.12.10, numpy 2.5.4 | see below | **`815 passed, 14 skipped, 161 warnings in 646.89s (0:10:46)`** | wall 10 min 49 s (pytest only; venv + lock install took 64 s with a warm pip cache) |

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
