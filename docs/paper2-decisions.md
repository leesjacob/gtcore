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
