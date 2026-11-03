# Paper 2 — plan amendments to requirements rev 3 (decision log)

Dated decisions that change the rev 3 work list. Each entry says what was
found, what Jacob decided, and which figures, tables and tasks change. The
rev 3 document itself is not edited; this file is read with it.

## 2026-11-03 — "Case 54" and the TPS case are one patient (Jacob: assume identical)

**Finding.** The CT delivered as "Case 54 post-op CT" is a second
reconstruction (series 3, 178 contiguous 1 mm slices, no Hr40 kernel) of the
same acquisition as the TPS case's thin-cut (series 2, 204 slices, Hr40):
identical acquisition timestamp to 3 ms, identical table position and slice
positions, identical tube settings, same seed voxels, slice correlation 0.985.
The "Case 54" post-op MRI rigidly registers onto that CT with the craniotomy,
the cavity, the ventricles and the dental work coinciding. Confidence: the CT
identity is certain; the MRI/patient identity is very high (a rigid transform
cannot align a different skull, craniotomy site and cavity). Jacob
(2026-11-03): assume they are the same patient and change the plan.

**Consequences (rev 3 → this amendment).**

1. *Datasets (Table 1).* One clinical patient, dataset id **JD**: `JD-1mm`
   (thin-cut, Hr40; the series the RTSTRUCT references), `JD-CT-s3` (the
   second reconstruction of the same acquisition, contiguous 1 mm), `JD RT`
   (RTSTRUCT, RTDOSE; RTPLAN when obtained), `JD-MRI-pre`, `JD-MRI-intra`,
   `JD-MRI-post`. The printed phantom cavity was printed from `JD-MRI-intra`
   (rev 2 wording restored: the phantom is this patient's intra-operative
   cavity; tiles placed by hand in arbitrary positions, "as placed"). The
   identifier "Case 54" is not used anywhere. `JD-2mm` (the 64-slice gapped
   export of series 3) is missing from disk since 2026-11-03; see F10(b).
2. *No held-out real scan.* The paper states one clinical case. Every
   real-data row of Table 3 is flagged calibration-set; the only negative
   control is the tile-less phantom. The rev 3 "held-out run on Case 54" and
   `scripts/heldout_case54.py` are removed from Phase 5. The brief's rule "no
   parameter is re-tuned on JohnDoe" still holds: the clinical case is a
   reference, not a tuning set, and the formalism/gamma/DVH comparisons are
   implementation-equivalence checks, as §5 already says.
3. *F8 becomes two panels.* F8(c) ("Case 54 implant vs optimizer") is the
   single-patient implanted-vs-optimized comparison that rev 2/3 moved to
   Paper 3 for the target-circularity reason (the full rind scores the
   surgeon on a target he was not aiming at). The comparison is still
   computed in Phase 5 (`gt optimize` with N = 8 on the `JD-1mm` cavity,
   both arms scored on `RIND5(gtcore cavity)`) and reported in Supplement S4
   as a labelled single-case illustration, not a claim. Jacob may promote it.
4. *S5 is "the cavity across the loop, one patient".* Panel: the same
   cavity slice on `JD-MRI-pre`, `JD-MRI-intra`, `JD-MRI-post`, `JD-1mm`
   (each rigidly registered to the CT) and the printed phantom CT (`PH0`).
   Numbers: cavity volume at implant time = the printed cavity measured on
   the tile-less phantom CT (`PH0`, gtcore cavity), vs the post-operative
   cavity (`Cav_Post` from the RTSTRUCT and the gtcore cavity on `JD-1mm`).
   No MRI segmentation is added (stop list); the MRIs are shown, not
   segmented. New script `scripts/cavity_across_scans.py` (Phase 5)
   replaces `heldout_case54.py`.
5. *F10(b).* If `JD-2mm` is restored, unchanged. Otherwise two real
   substitutes, both stated as such: (i) `JD-CT-s3` vs `JD-1mm` — seed
   positions across two reconstructions of one acquisition (reconstruction
   repeatability at 1 mm); (ii) `JD-1mm` decimated to 1 mm slabs at 2 mm
   pitch (a simulated gapped export, geometry identical to the lost one,
   same kernel). The previously measured gapped-export numbers (median 0.32
   mm, per-axis SD 0.51/0.40/0.63 mm, `docs/localization-notes.md`) are
   quoted with their commit, labelled "not regenerable from the tag".
6. *Table 3.* Drop the row "Case 54 held-out (seed/tile count vs the
   operative note)". The operative count for JD is 8 full tiles = 32
   sources (already the F4 criterion).
7. *§5 statements.* "Case 54 is the only held-out real scan" → "there is
   no held-out real scan; one clinical case, reference not validation
   cohort". Phantom provenance sentence: cavity = this patient's intra-op
   MRI geometry.

## 2026-11-03 — F9 modality figure reduced (Jacob)

No printed-phantom STL exists; the optical export is a partial open surface;
the phantom MRI is a dry plastic phantom with almost no signal. F9(b) and
`scripts/modality_metrics.py` are dropped; F9(a)/Table 1b are at most a
qualitative strip assembled by Jacob, or the figure is cut and one
Discussion sentence remains.

## 2026-11-03 — figure plan v6 adopted, with four amendments

`figure-plan-v6.md` (Jacob, 2026-11-03) restructures the figures around one
patient followed through the loop. Adopted: dataset ids `PT-preMRI`,
`PT-intraMRI`, `PT-postMRI`, `PT-CT1mm`, `PT-CT2mm`, `PT-RT`, `PH8`, `PH0`
(the Phase 1 notes still say `JD-1mm`/`JD RT`; alias `JD-1mm` = `PT-CT1mm`,
`JD-2mm` = `PT-CT2mm`, `JD RT` = `PT-RT`); F3 = the one-case strip (four
scans + phantom); F4(b) = the patient's 3-D implant (6/8 tiles, 8 leftover
seeds marked) with the count-free curves in the Supplement; F10 → S2; the
seed-to-`Cav_Post` distance (median 4.6 mm, mock-level) stated in Methods
without re-tuning `SEED_PLANE_OFFSET_MM`; `scripts/cavity_change.py` is the
name of the cavity-across-the-loop script (replaces the `cavity_across_scans`
name above). Amendments:

1. **F9**: no STL exists and the optical/MRI exports are unusable (entry
   above); v6's "deviation vs STL" panel cannot be made. F9 stays reduced or
   cut.
2. **PT-CT2mm** is not on disk (lost in the 2026-11-03 folder move). Until it
   is restored, S2's real-data panel uses the two stated substitutes.
3. **No MRI cavity segmentation is added to gtcore** (stop list). The
   cavity-change measurement takes manual cavity contours on the MRIs
   (Jacob, in Slicer; RTSTRUCT or NIfTI) if provided, and otherwise uses the
   printed cavity measured on the tile-less phantom CT as the intra-operative
   volume. Rigid registration (SimpleITK, mutual information) and the
   surface-distance measurement are the script's only computation.
4. **F8(c) on this patient** is accepted for the main text only with: both
   targets reported side by side (full `RIND5` and the physician's `CTV_GT`;
   the mock run shows the gain is confined to the rind the physician
   excluded: 0.67 → 0.81 on the rind, 0.92 → 0.91 on `CTV_GT`); optimizer
   eligibility restricted to the craniotomy-side wall (a wall the surgeon
   could reach); numbers regenerated from the Phase 4 optimizer (λ_tail = 1,
   SA 8/60, full-grid metrics), never the mock run; the caption says
   "one case, illustration, not a cohort claim". The `S4`-only option above is
   superseded unless Jacob reverts it.

## 2026-11-03 — data designation v7 adopted (what is a figure, a table row, an image)

`figure-data-designation.md` (Jacob, v7) supersedes the v6 panel list for what
is *rendered*: main text keeps one chart (F5b DVH with the MC envelope) and
one diagram with numbers (F1); everything else is an image or a 3-D
reconstruction. Removed from figures → tables/captions: the gamma map (pass
rates in Table 3 + F5 caption), the implant-reach slice and the three-target
DVH (F6 caption numbers + Table 3), the synthetic V100-vs-N chart (Table 3 +
S4), the phantom hand-placed-vs-optimized panel (Table 3). Final figures: F1,
F2, F3, F4, F5 (isodoses + DVH), F6 (one slice), F7, F8 (patient implanted vs
optimized, two renders), F9; S2, S4. Code consequences: `paper_figures.py`
emits F2(c), F3(c), F4(a–c), F5(a–b), F6, F8(a–b), S2(a–b), S4; every other
number flows through `paper_tables.py` (Tables 1–4, caption numbers, S3/S4
tables). The computations behind the removed panels (gamma, three-target DVH,
campaign curves) are unchanged — only their presentation moves. Coordinator
notes: (i) the gamma map and the three-target DVH are generated anyway and
are kept as optional Supplement panels (S3, S5-equivalent) for reviewers;
(ii) F9 still lists an Artec crop — no Artec export exists on this machine
(see the F9 entry above); (iii) "structure-volume reproduction vs MIM" needs
the MIM export, still missing.

## 2026-11-03 — F6 implant-reach radius calibrated: r = 10 mm (Phase 2 `targets`)

Rev 3 rule: "DECIDE r for `IMPLANT_REACH` by Dice vs `CTV_GT` on this case,
and say so in Table 2 (calibration set = this case)". Measured on PT-CT1mm
with the 32 gtcore seeds on `RIND5(Cav_Post, clip = Brain)` (CT grid):
Dice vs `CTV_GT` 0.214 / 0.578 / **0.881** / 0.846 / 0.740 at r = 6 / 8 /
**10** / 12 / 14 mm (volumes 2.4 / 8.5 / 16.8 / 25.3 / 31.8 cc; `CTV_GT`
19.0 cc). The rule picks **r = 10 mm**; the v6/v7 sketch's "12 mm, Dice 0.80"
is replaced. Table 2 row: `IMPLANT_REACH` r = 10 mm, calibration set = this
case, criterion = Dice vs `CTV_GT`. (12 mm covers 98.5 % of `CTV_GT` at a
lower Dice; if Jacob prefers coverage over Dice he reverts this entry.)
Convention recorded for Methods: `RIND5` is the set of voxel centres whose
Euclidean distance to the nearest cavity voxel centre is <= 5 mm, outside the
cavity, inside the clip — a centre-to-centre EDT, which makes the rind about
0.2 mm thinner than a surface-based shell (−4 to −5 % volume on a digital
sphere); the same definition is used in every table.

## 2026-11-03 — seed-plane model confirmed in vivo (Phase 3 `seed-wall`); phantom wall reference

Signed seed-to-surface distance (positive = cavity side), `scripts/seed_wall_distance.py`:
clinical 32 gtcore seeds vs `Cav_Post` median +3.65 mm (10–90 % 1.70…5.46; TPS
sources +3.57) — inside the hydrated 2.25–3.75 mm band → the 3 mm seed-plane model
holds in vivo; `SEED_PLANE_OFFSET_MM` = 3.0 is not re-tuned (Table 2 row, Methods
sentence in the notes). The v6/v7 caption number "median 4.6 mm" is superseded by
3.65 mm. Phantom PH8 seeds vs the tile-less twin PH0 rigidly registered into PH8
(registration residual 0.34 mm median): +3.05 mm (2.35…3.77, 26/32 in band) — the
earlier "~1 mm off the mesh" was measured against the PH8 body mask, which includes
the collagen tiles (tile free face), i.e. a reference-surface artifact, not a
placement artifact. **Phase 4 consequence:** the phantom arms (V6, F8/Table 3
phantom rows) conform and optimise on the registered PH0 wall (tile-free printed
surface) and are scored on `RIND5(phantom cavity)` built from the PH0 lumen; the
"as placed" arm is the pipeline's recovered tiles on that same wall.

## 2026-11-03 — gamma results and two wording corrections (Phase 3 `gamma`)

pymedphys 0.41.0 gamma, reference = RTDOSE voxels ≥ 30 Gy, evaluation = gtcore on a
0.5 mm lattice, 32 gtcore seeds at nominal 3.5 U: over 30–200 Gy, 3 %/2 mm global
99.33 % (local 99.37 %), 2 %/1 mm global 96.96 % (local 96.23 %); over ≥ 30 Gy
98.71 / 98.92 / 95.21 / 94.78 %. With the TPS's own 30 contoured seeds + 2 maxima the
numbers are within 0.3 pp, so the residual 3 % at 2 %/1 mm is engine/grid, not
localisation. These supersede the rev-3 mock (99.2 / 88.1 %). Corrections to the
paper text: (1) the voxels > 200 Gy (9 % of the ≥ 30 Gy set) lie a median 3.8 mm
(p95 6.1 mm) from the nearest seed — they are the seed plane of each tile, not
"within ~3 mm of a seed"; caption: "voxels above 200 Gy, where the 1 × 1 × 1.67 mm
TPS grid averages the seed-plane dose, are excluded; the ≥ 30 Gy figure is given
alongside". (2) Above the prescription dose, *local* normalisation is the lenient
one (3 % of a 150 Gy voxel is 4.5 Gy), global-to-rx the strict one; §5's "global-to-rx
is lenient near seeds" is reversed below rx only. Table 3 / F5 caption rows: gtcore
seeds (pipeline result) and TPS contours (engine-equivalence reference); the
RTDOSE-maxima set is a grid-limited position estimate and is not quoted.

## 2026-11-03 — the eight leftover seeds are half tiles (Phase 3 `leftovers`); tile count to confirm

Count-free: 6/8 tiles, 8 leftovers; count-known N = 8: 7/8 with one degraded
completion that is two abutting pairs misread as a crumpled tile. The leftovers are
four 2-seed pairs at the manufactured pitch (9.0–9.6 mm), one pair 3.2–3.5 mm beneath
a fitted tile's seed sheet (stacked), the others at the edges of fitted tiles; two
single-linkage clusters span 18.0 and 25.5 mm (> 14.1 mm, the seed diagonal of an
intact tile), so they cannot be crumpled full tiles; all eight are TPS-contoured,
brighter than the assigned seeds; no merged blobs, no misses (32 = 32 sources). The
model-free reading is **6 full + 4 half tiles = 32 seeds**. This contradicts the
brief's "8 full tiles = 32 sources"; **Jacob to check the operative record (were two
tiles cut?)**. If confirmed, the count-known Table 3 row becomes
`ImplantPrior(n_full=6, n_half=4)` (the fitter already recovers 6 full + {11,21},
{13,16} with halves allowed), F4(b) shows 6 sheets + 4 half-tile pairs, and the
Results sentence names half tiles as the cause. No two-layer model extension is
warranted (§4 item 18 not triggered). Marginal-score curves on real scans end at
the implanted count (no 7th / 9th candidate) rather than crossing λ = 3.5 — caption
point for the Supplement curve.

## 2026-11-03 — formalism: MIM is 2D with seed axes along S–I (Phase 3 `formalism`)

The angular-symmetry test on the RTDOSE reproduces rev 3 (ratio 0.93 at 0–15° →
1.10 at 75–90° with the physical PCA axes). The residual trend is explained by the
seed-axis definition, not the anisotropy dataset (CLRP_V2 identical), the grid
(voxel averaging identical) or S_K (flat multiplier): axes fitted to the RTDOSE's
own 2–6 mm shells are a median 3.6° from the patient S–I axis (p95 9.3°) and 66°
from the physical axes; with all axes along S–I the ratio is flat (RMS log-ratio
0.042 vs 0.180), within 1–2 % from 30–90° and ±5 % within 30° of the axis at
r < 5 mm. Conclusion for the paper: MIM used the 2D line-source formalism and a
default seed orientation (S–I) for every source; no 1D mode. Rev 3's "1D would
give 1.29 → 0.96" holds only in the far field (> 6 mm); under the > 50 %
single-seed selection it is 0.95 → 1.05. Consequences: (1) Table 3 and the F5
caption report both gtcore with physical axes (the pipeline result) and gtcore
with the TPS convention (axes along S–I; the like-for-like equivalence row) —
Phase 5 adds the S–I-axis variant to `validation_gamma.py` and the DVH matrix;
(2) the implied-S_K diagnostic with TPS-convention axes is 3.59 U (nominal 3.5 U
unchanged); (3) the RTPLAN, when it arrives, is expected to show
ControlPointOrientation = (0, 0, ±1) or absent — `scripts/formalism_check.py
--rtplan` compares.
