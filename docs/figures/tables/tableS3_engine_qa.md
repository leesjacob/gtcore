## Table S3 -- dose engine QA: gtcore TG43Engine('tg43u1s2') vs the TG-43U1S2 QA tables and Paper 1's engine

rel = 100 x (gtcore / reference - 1); reference = Paper 1's DoseInterpolator (TG43U1S2 dataset) unless the row names a QA table (Table XII: erratum values via Paper 1's transcription; Table XXIII: 2017 print, erratum status unknown). Synthetic geometry, no patient data (flag n/a).

| section | row | n | max \|rel\| % | median \|rel\| % | p95 \|rel\| % | where max | note | commit |
|---|---|---|---|---|---|---|---|---|
| tables | consensus entries compared | 209 |  |  |  |  | Lambda, L, g_L (16 r + 16 values), F axes (15 + 10), F table (150) | 29295c502 |
| tables | DIFFERENCE F values | 1 |  |  |  | theta_deg=80, r_cm=7 | paper1=0.999 gtcore=0.998 | 29295c502 |
| tableXII | gtcore exact vs Table XII (erratum) | 143 | 0.286 | 0.054 | 0.19 | r=5 cm, theta=80 deg | published values carry 3-4 significant figures | 29295c502 |
| tableXII | Paper 1 engine vs Table XII (erratum) | 143 | 0.286 | 0.054 | 0.193 | r=5 cm, theta=80 deg | published values carry 3-4 significant figures | 29295c502 |
| tableXII | gtcore vs Paper 1 at the 143 Table XII nodes | 143 | 0.1 | 0 | 7.09e-05 | r=7 cm, theta=80 deg | node points: only table-value differences survive | 29295c502 |
| tableXXIII | gtcore exact vs Table XXIII (along-away, 2017 print) | 149 | 0.244 | 0.0664 | 0.164 | along=70 mm, away=70 mm | off-node points: interpolation rules enter; 3-4 significant figures | 29295c502 |
| tableXXIII | Paper 1 engine vs Table XXIII (along-away, 2017 print) | 149 | 1.01 | 0.301 | 0.78 | along=70 mm, away=50 mm | off-node points: interpolation rules enter; 3-4 significant figures | 29295c502 |
| tableXXIII | gtcore vs Paper 1 at the Table XXIII points | 149 | 0.872 | 0.386 | 0.762 | along=65 mm, away=70 mm |  | 29295c502 |
| grid | all points (exact engine) | 13213 | 0.861 | 0.00609 | 0.705 | r=9.423 cm, theta=80 deg | g_L interpolation rule is the only off-node difference | 29295c502 |
| grid | at common table nodes (r in g and F nodes, theta in F nodes) | 105 | 0.1 | 0 | 2.22e-14 | r=7 cm, theta=80 deg | identical arithmetic expected | 29295c502 |
| grid | between g_L nodes | 10860 | 0.861 | 0.0188 | 0.765 | r=9.423 cm, theta=80 deg | Paper 1 linear vs gtcore log-linear (TG-43U1S1 Eq. 3) | 29295c502 |
| grid | near axis, theta < 5 deg | 730 | 0.842 | 0.00362 | 0.705 | r=9.423 cm, theta=3 deg | no near-axis difference for r >= 0.3 cm | 29295c502 |
| grid | theta >= 5 deg | 12483 | 0.861 | 0.00609 | 0.705 | r=9.423 cm, theta=80 deg |  | 29295c502 |
| grid | r in [0.3, 1) cm | 4344 | 0.00362 | 0.000601 | 0.00357 | r=0.3805 cm, theta=26 deg |  | 29295c502 |
| grid | r in [1, 3) cm | 3801 | 0.301 | 0.0188 | 0.288 | r=2.549 cm, theta=86.5 deg |  | 29295c502 |
| grid | r in [3, 6) cm | 2715 | 0.692 | 0.314 | 0.666 | r=5.519 cm, theta=80 deg |  | 29295c502 |
| grid | r in [6, 10) cm | 2353 | 0.861 | 0.347 | 0.842 | r=9.423 cm, theta=80 deg |  | 29295c502 |
| grid | all points (gtcore tabulated kernel, compute_dose_grid default) | 13213 | 0.858 | 0.0101 | 0.703 | r=9.423 cm, theta=80 deg | adds the kernel-table interpolation error | 29295c502 |
| cloud | summed rate per U, exact engine, all points | 1000 | 11.4 | 0.258 | 0.42 | nearest seed 1.11 mm | conversion factors divided out | 29295c502 |
| cloud | summed rate per U, points >= 3 mm from every seed | 992 | 0.512 | 0.259 | 0.42 | nearest seed 42.37 mm |  | 29295c502 |
| cloud | total-decay dose, each engine's own conversion | 1000 | 11.4 | 0.258 | 0.421 | nearest seed 1.11 mm | tau: gtcore 335.4785 h vs Paper 1 335.48 h (-4.37e-04 %) | 29295c502 |
| cloud | total-decay dose, gtcore tabulated kernel vs Paper 1 | 1000 | 11.4 | 0.257 | 0.419 | nearest seed 1.11 mm | compute_dose_grid default path | 29295c502 |
| cloud | dose scale (gtcore exact) | 1000 |  |  |  |  | median 2011.2 cGy, max 181714.2 cGy, min 277.12 cGy; S_K 3.5 U | 29295c502 |
| probe | reference point | 1 | 0 |  |  | theta=90 deg, r=1 cm | gtcore=1.056 paper1=1.056 cGy/h/U; both must equal Lambda = 1.056 | 29295c502 |
| probe | on-axis r < L/2 (inside can) | 1 |  |  |  | theta=0 deg, r=0.15 cm | gtcore=112.736 paper1=0 cGy/h/U; Paper 1: 1/(r^2-L^2/4) < 0, clamped to 0 (port-notes item 1); gtcore: projected to the capsule end face | 29295c502 |
| probe | on-axis r = L/2 (active tip, inside can) | 1 | 90.5 |  |  | theta=0 deg, r=0.2 cm | gtcore=61.6623 paper1=652.188 cGy/h/U; Paper 1: singular denominator clamped at 1e-3 -> spike; gtcore: end-face value | 29295c502 |
| probe | on-axis r = 0.21 cm (inside can) | 1 | 61.3 |  |  | theta=0 deg, r=0.21 cm | gtcore=61.6623 paper1=159.362 cGy/h/U; Paper 1: 1/(r^2-L^2/4) pole; gtcore: end-face plateau (no tissue here) | 29295c502 |
| probe | on-axis r = 0.23 cm (just outside can) | 1 | 0.00269 |  |  | theta=0 deg, r=0.23 cm | gtcore=50.8344 paper1=50.8358 cGy/h/U; both use the analytic on-axis limit; only g_L interpolation differs | 29295c502 |
| probe | on-axis r = 0.3 cm | 1 | 0.00233 |  |  | theta=0 deg, r=0.3 cm | gtcore=14.0877 paper1=14.088 cGy/h/U; grid lower edge | 29295c502 |
| probe | NaN-hole lookup F(0.1 cm, 0 deg) region | 1 |  |  |  | theta=0 deg, r=0.1 cm | gtcore=161.281 paper1=0 cGy/h/U; both borrow F from r = 0.25 cm (identical fill); gtcore evaluates at the capsule surface instead (inside the can) | 29295c502 |
| probe | NaN-hole lookup F(0.1 cm, 20 deg) region | 1 | 2.55 |  |  | theta=20 deg, r=0.1 cm | gtcore=176.139 paper1=171.756 cGy/h/U; same fill rule in both; gtcore projects to the capsule surface | 29295c502 |
| probe | radial floor ring (90 deg, 0.045 cm) | 1 | 11.6 |  |  | theta=90 deg, r=0.045 cm | gtcore=136.179 paper1=154.011 cGy/h/U; Paper 1 clips r at 0.041 cm, gtcore floors at 0.05 cm (0.09 mm ring beside the capsule side) | 29295c502 |
| probe | between g_L nodes 9-10 cm (90 deg, 9.5 cm) | 1 | 0.874 |  |  | theta=90 deg, r=9.5 cm | gtcore=0.00200454 paper1=0.0020222 cGy/h/U; linear (Paper 1) vs log-linear (gtcore, TG-43U1S1 Eq. 3) g_L interpolation | 29295c502 |
| probe | beyond the data (90 deg, 12 cm) | 1 | 59.1 |  |  | theta=90 deg, r=12 cm | gtcore=0.000647224 paper1=0.00158436 cGy/h/U; Paper 1 clips r to 10 cm (whole rate frozen); gtcore: G_L keeps falling, g_L single-exponential tail (TG-43U1S1 Eq. 4) | 29295c502 |
| probe | beyond the data (90 deg, 15 cm) | 1 | 88.2 |  |  | theta=90 deg, r=15 cm | gtcore=0.000186882 paper1=0.00158436 cGy/h/U; as above | 29295c502 |

Generated by `scripts/paper_tables.py` at tree `056334c` from the committed data; the CSV next to this file carries a `locator` column per row.
