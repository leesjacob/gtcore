# Paper 2 — phase notes

Numbers produced by the Paper 2 work, each with the command and the commit
it was run at (rev-3 requirements doc §6).  Dataset ids as in the agent
brief: PH8 = printed phantom with 8 hand-placed tiles, PH0 = tile-less
printed phantom, JD-1mm = JohnDoe contiguous 1 mm post-op CT, JD RT = its
RTSTRUCT / RTDOSE.  `GT_DATA_ROOT` is the data root; on 2026-11-03 the three
JohnDoe folders moved one level down into `$GT_DATA_ROOT/JohnDoe/`.

Hardware for every timing below: AMD Ryzen 5 7600X (6 cores / 12 threads),
31 GB RAM, Windows 11 Home, Python 3.12.10, numpy 2.5.2, scipy 1.18.1.
Other agents were running concurrently, so the wall-clock numbers are
upper bounds.

---

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
