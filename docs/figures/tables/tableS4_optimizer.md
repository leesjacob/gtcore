## Table S4 -- optimizer campaign (synthetic benchmark, bound, discretisation, sensitivity, phantom, runtime, failure modes)

Phase 4 campaign (`scripts/validation_optimize.py`, frozen objective lambda_tail 1 / TAIL_Q 0.10, SA 8 x 60, h 2.5 mm / 6 spins / M_opt 4000, full-grid RIND5 scoring, same-candidate V100 bound). Blocks whose Phase 4 file has not landed are marked pending; the rev-1 files of 2026-10-07 (commit cca2120, shell-surrogate scoring, SA 3 x 40) are superseded and not quoted.

| block | item | value | source csv | commit | flag |
|---|---|---|---|---|---|
| V2 coverage vs N | N = 4, uniform | RIND5 V100 30.6 % +/- 20.4, D90 2545 cGy (n = 15 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 4, greedy | RIND5 V100 37.0 % +/- 20.7, D90 2681 cGy (n = 18 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 4, sa | RIND5 V100 35.6 % +/- 22.9, D90 3018 cGy (n = 18 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 4, sa_best | RIND5 V100 36.2 % +/- 22.8, D90 3014 cGy (n = 18 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 4, v100_bound | RIND5 V100 37.7 % +/- 19.9, D90 2193 cGy (n = 18 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 6, uniform | RIND5 V100 52.1 % +/- 25.3, D90 4221 cGy (n = 11 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 6, greedy | RIND5 V100 55.7 % +/- 21.8, D90 3739 cGy (n = 13 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 6, sa | RIND5 V100 58.1 % +/- 24.9, D90 4248 cGy (n = 13 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 6, sa_best | RIND5 V100 59.5 % +/- 24.5, D90 4256 cGy (n = 13 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 6, v100_bound | RIND5 V100 57.0 % +/- 24.2, D90 4008 cGy (n = 13 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 8, uniform | RIND5 V100 57.7 % +/- 2.8, D90 4151 cGy (n = 4 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 8, greedy | RIND5 V100 78.8 % +/- 20.7, D90 5495 cGy (n = 12 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 8, sa | RIND5 V100 81.2 % +/- 20.0, D90 6131 cGy (n = 12 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 8, sa_best | RIND5 V100 81.5 % +/- 19.7, D90 6161 cGy (n = 12 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 8, v100_bound | RIND5 V100 73.9 % +/- 17.7, D90 5187 cGy (n = 9 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 8, truth | RIND5 V100 75.6 % +/- 16.6, D90 4664 cGy (n = 18 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 8, truth_raw | RIND5 V100 74.8 % +/- 16.5, D90 4563 cGy (n = 18 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 10, greedy | RIND5 V100 88.0 % +/- 7.5, D90 5722 cGy (n = 6 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 10, sa | RIND5 V100 98.1 % +/- 1.3, D90 6707 cGy (n = 6 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 10, sa_best | RIND5 V100 98.5 % +/- 1.2, D90 6727 cGy (n = 6 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 12, greedy | RIND5 V100 99.5 % +/- 0.5, D90 7737 cGy (n = 5 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 12, sa | RIND5 V100 100.0 % +/- 0.0, D90 7909 cGy (n = 5 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 coverage vs N | N = 12, sa_best | RIND5 V100 100.0 % +/- 0.0, D90 7899 cGy (n = 5 cavities) | docs/figures/optimize/data/v2_summary.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 4, sa - greedy (RIND5_V100) | -1.3 pp [-2.9, 0.3], n = 18, Wilcoxon p 0.119 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 4, sa - uniform (RIND5_V100) | -0.2 pp [-1.5, 1.1], n = 15, Wilcoxon p 0.804 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 4, greedy - uniform (RIND5_V100) | 1.6 pp [-0.3, 3.6], n = 15, Wilcoxon p 0.073 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 4, v100_bound - sa (RIND5_V100) | 2.0 pp [-0.2, 4.2], n = 18, Wilcoxon p 0.0987 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 4, sa_best - uniform (RIND5_V100) | 0.4 pp [-0.9, 1.7], n = 15, Wilcoxon p 0.489 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 4, sa_best - greedy (RIND5_V100) | -0.8 pp [-2.3, 0.8], n = 18, Wilcoxon p 0.347 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 6, sa - greedy (RIND5_V100) | 2.5 pp [-0.7, 5.6], n = 13, Wilcoxon p 0.129 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 6, sa - uniform (RIND5_V100) | 1.3 pp [-1.3, 4.0], n = 11, Wilcoxon p 0.147 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 6, greedy - uniform (RIND5_V100) | -1.0 pp [-6.2, 4.3], n = 11, Wilcoxon p 1 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 6, v100_bound - sa (RIND5_V100) | -1.1 pp [-2.9, 0.6], n = 13, Wilcoxon p 0.176 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 6, sa_best - uniform (RIND5_V100) | 2.8 pp [0.1, 5.5], n = 11, Wilcoxon p 0.042 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 6, sa_best - greedy (RIND5_V100) | 3.9 pp [0.8, 6.9], n = 13, Wilcoxon p 0.021 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 8, sa - greedy (RIND5_V100) | 2.4 pp [0.7, 4.2], n = 12, Wilcoxon p 0.00244 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 8, sa - uniform (RIND5_V100) | 1.9 pp [-2.7, 6.4], n = 4, Wilcoxon p 0.375 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 8, greedy - uniform (RIND5_V100) | -0.6 pp [-5.8, 4.6], n = 4, Wilcoxon p 1 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 8, v100_bound - sa (RIND5_V100) | -1.1 pp [-3.5, 1.3], n = 9, Wilcoxon p 0.203 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 8, sa_best - uniform (RIND5_V100) | 2.5 pp [-2.0, 7.0], n = 4, Wilcoxon p 0.25 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 8, sa_best - greedy (RIND5_V100) | 2.7 pp [0.9, 4.6], n = 12, Wilcoxon p 0.000977 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 10, sa - greedy (RIND5_V100) | 10.2 pp [3.4, 16.9], n = 6, Wilcoxon p 0.0312 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 10, sa_best - greedy (RIND5_V100) | 10.5 pp [3.5, 17.5], n = 6, Wilcoxon p 0.0312 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 12, sa - greedy (RIND5_V100) | 0.4 pp [-0.1, 1.0], n = 5, Wilcoxon p 0.125 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V2 paired comparisons | N = 12, sa_best - greedy (RIND5_V100) | 0.5 pp [-0.1, 1.0], n = 5, Wilcoxon p 0.125 | docs/figures/optimize/data/v2_paired.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 4 | gap -75.7 % on the influence coverage (SA 0.154 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 4 | gap -76.2 % on the influence coverage (SA 0.151 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 4 | gap -75.7 % on the influence coverage (SA 0.154 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 6 | gap -45.9 % on the influence coverage (SA 0.526 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 6 | gap -45.9 % on the influence coverage (SA 0.526 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 6 | gap -49.4 % on the influence coverage (SA 0.492 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s1_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 4 | gap -87.4 % on the influence coverage (SA 0.075 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 4 | gap -74.6 % on the influence coverage (SA 0.151 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 4 | gap -74.6 % on the influence coverage (SA 0.151 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 6 | gap -50.0 % on the influence coverage (SA 0.460 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 6 | gap -56.6 % on the influence coverage (SA 0.399 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 6 | gap -54.3 % on the influence coverage (SA 0.420 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s2_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 4 | gap -87.7 % on the influence coverage (SA 0.074 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 4 | gap -74.0 % on the influence coverage (SA 0.156 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 4 | gap -72.0 % on the influence coverage (SA 0.168 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 6 | gap -52.9 % on the influence coverage (SA 0.438 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 6 | gap -53.2 % on the influence coverage (SA 0.435 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 6 | gap -54.1 % on the influence coverage (SA 0.427 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s3_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 4 | gap -77.1 % on the influence coverage (SA 0.134 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 4 | gap -72.3 % on the influence coverage (SA 0.162 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 4 | gap -75.7 % on the influence coverage (SA 0.142 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 6 | gap -59.6 % on the influence coverage (SA 0.365 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 6 | gap -52.9 % on the influence coverage (SA 0.425 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 6 | gap -59.4 % on the influence coverage (SA 0.367 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 8 | gap -0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 8 | gap -0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s4_x1.00, N = 8 | gap -0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 4 | gap -74.6 % on the influence coverage (SA 0.156 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 4 | gap -73.5 % on the influence coverage (SA 0.163 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 4 | gap -74.4 % on the influence coverage (SA 0.157 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 6 | gap -52.1 % on the influence coverage (SA 0.450 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 6 | gap -49.3 % on the influence coverage (SA 0.476 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 6 | gap -48.8 % on the influence coverage (SA 0.481 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s5_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 4 | gap -76.1 % on the influence coverage (SA 0.146 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 4 | gap -74.3 % on the influence coverage (SA 0.157 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 4 | gap -74.3 % on the influence coverage (SA 0.157 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 6 | gap -50.6 % on the influence coverage (SA 0.464 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 6 | gap -52.4 % on the influence coverage (SA 0.447 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 6 | gap -53.0 % on the influence coverage (SA 0.441 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V3 gap to the bound | s6_x1.00, N = 8 | gap 0.0 % on the influence coverage (SA 1.000 vs bound n/a); same candidates: True | docs/figures/optimize/data/v3_gap.csv | d2d455e | synthetic |
| V4 discretisation | s1_x1.00: h 4 mm, 3 spins, N = 8 | candidates 433; SA RIND5 V100 n/a %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s1_x1.00: h 4 mm, 6 spins, N = 8 | candidates 869; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s1_x1.00: h 4 mm, 12 spins, N = 8 | candidates 1737; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s1_x1.00: h 3 mm, 3 spins, N = 8 | candidates 773; SA RIND5 V100 99.9 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s1_x1.00: h 3 mm, 6 spins, N = 8 | candidates 1544; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s1_x1.00: h 2.5 mm, 3 spins, N = 8 | candidates 1154; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s2_x1.00: h 4 mm, 3 spins, N = 8 | candidates 481; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s2_x1.00: h 4 mm, 6 spins, N = 8 | candidates 965; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s2_x1.00: h 4 mm, 12 spins, N = 8 | candidates 1925; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s2_x1.00: h 3 mm, 3 spins, N = 8 | candidates 823; SA RIND5 V100 99.9 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s2_x1.00: h 3 mm, 6 spins, N = 8 | candidates 1646; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V4 discretisation | s2_x1.00: h 2.5 mm, 3 spins, N = 8 | candidates 1243; SA RIND5 V100 100.0 %; status ok | docs/figures/optimize/data/v4_discretization.csv | d2d455e | synthetic |
| V5 sensitivity | rank stability of the arms under seed-plane 2.25 / 3.75 mm, S_K +/- 5 %, interference on, M_opt 4000 (Kendall tau) | min 1.00, median 1.00 over 18 (cavity, perturbation) pairs | docs/figures/optimize/data/v5_rank_stability.csv | d2d455e | synthetic |
| V6 phantom (as placed vs optimized) | as_placed, N = 8 | RIND5 V100 44.3 %, D90 2187 cGy, V150 28.5 %; overlaps 3 | docs/figures/optimize/data/v6_phantom_collapsed.csv | d2d455e | calibration-set |
| V6 phantom (as placed vs optimized) | as_placed_conformed, N = 8 | RIND5 V100 45.2 %, D90 2161 cGy, V150 30.7 %; overlaps 8 | docs/figures/optimize/data/v6_phantom_collapsed.csv | d2d455e | calibration-set |
| V6 phantom (as placed vs optimized) | uniform, N = 8 | RIND5 V100 48.9 %, D90 3408 cGy, V150 22.4 %; overlaps 0 | docs/figures/optimize/data/v6_phantom_collapsed.csv | d2d455e | calibration-set |
| V6 phantom (as placed vs optimized) | greedy, N = 8 | RIND5 V100 48.3 %, D90 3290 cGy, V150 22.9 %; overlaps 0 | docs/figures/optimize/data/v6_phantom_collapsed.csv | d2d455e | calibration-set |
| V6 phantom (as placed vs optimized) | sa, N = 8 | RIND5 V100 54.6 %, D90 3529 cGy, V150 22.1 %; overlaps 0 | docs/figures/optimize/data/v6_phantom_collapsed.csv | d2d455e | calibration-set |
| V6 phantom (as placed vs optimized) | sa_best, N = 8 | RIND5 V100 54.6 %, D90 3529 cGy, V150 22.1 %; overlaps 0 | docs/figures/optimize/data/v6_phantom_collapsed.csv | d2d455e | calibration-set |
| V6 phantom (as placed vs optimized) | v100_bound, N = 8 | RIND5 V100 59.2 %, D90 1866 cGy, V150 29.1 %; overlaps 0 | docs/figures/optimize/data/v6_phantom_collapsed.csv | d2d455e | calibration-set |
| V6 phantom (as placed vs optimized) | continuous, N = 8 | RIND5 V100 48.7 %, D90 3135 cGy, V150 23.6 %; overlaps 0 | docs/figures/optimize/data/v6_phantom_collapsed.csv | d2d455e | calibration-set |
| V6 phantom (as placed vs optimized) | minimum N (greedy sweep, S5 surrogate): first N with D90 >= rx | not reached within the sweep (N <= 10) | docs/figures/optimize/data/v6_sweep.csv | d2d455e | calibration-set |
| V7 runtime | s1_x0.80 (12.399 mL, C = 131), N = 8 | candidates 1.8 s; conflicts 0.8 s; influence 0.2 s; final_report_full_50mm 14.3 s; final_report_15mm 1.6 s | docs/figures/optimize/data/v7_runtime.csv | d2d455e | synthetic |
| V7 runtime | s1_x1.00 (24.382 mL, C = 869), N = 8 | candidates 11.0 s; conflicts 43.7 s; influence 1.5 s; greedy 5.0 s; sa 32.0 s; final_report_full_50mm 27.1 s; final_report_15mm 3.0 s | docs/figures/optimize/data/v7_runtime.csv | d2d455e | synthetic |
| V7 runtime | s1_x1.25 (47.835 mL, C = 1380), N = 8 | candidates 39.7 s; conflicts 59.0 s; influence 1.2 s; greedy 5.3 s; sa 85.1 s; final_report_full_50mm 19.0 s; final_report_15mm 2.6 s | docs/figures/optimize/data/v7_runtime.csv | d2d455e | synthetic |
| V8 failure modes | degenerate / empty meshes, N beyond any packing, tile wider than the wall, eligibility smaller than a tile | 12 of 12 cases behave as specified (explicit ValueError / RuntimeError or a valid plan) | docs/figures/optimize/data/v8_failure_modes.csv | d2d455e | synthetic |
| capacity | synthetic s1_x0.80 | manufacturer rule 7 / capacity at grid 4 / area bound 6 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s2_x0.80 | manufacturer rule 8 / capacity at grid 4 / area bound 7 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s3_x0.80 | manufacturer rule 8 / capacity at grid 4 / area bound 7 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s4_x0.80 | manufacturer rule 8 / capacity at grid 4 / area bound 7 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s5_x0.80 | manufacturer rule 8 / capacity at grid 4 / area bound 7 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s6_x0.80 | manufacturer rule 8 / capacity at grid 4 / area bound 7 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s1_x1.00 | manufacturer rule 11 / capacity at grid 7 / area bound 10 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s2_x1.00 | manufacturer rule 12 / capacity at grid 7 / area bound 11 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s3_x1.00 | manufacturer rule 12 / capacity at grid 7 / area bound 11 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s4_x1.00 | manufacturer rule 12 / capacity at grid 9 / area bound 11 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s5_x1.00 | manufacturer rule 12 / capacity at grid 7 / area bound 11 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s6_x1.00 | manufacturer rule 11 / capacity at grid 7 / area bound 10 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s1_x1.25 | manufacturer rule 17 / capacity at grid 10 / area bound 16 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s2_x1.25 | manufacturer rule 18 / capacity at grid 10 / area bound 17 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s3_x1.25 | manufacturer rule 18 / capacity at grid 10 / area bound 17 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s4_x1.25 | manufacturer rule 19 / capacity at grid 10 / area bound 18 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s5_x1.25 | manufacturer rule 18 / capacity at grid 10 / area bound 17 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | synthetic s6_x1.25 | manufacturer rule 18 / capacity at grid 10 / area bound 17 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | synthetic |
| capacity | PH8 PH8 (phantom wall (tile-less twin PH0, registered into PH8), | manufacturer rule 14 / capacity at grid 10 / area bound 13 / placed 8 (capacity at grid (h = 4 mm, 6 spins), not exact) | docs/figures/optimize/data/capacity.csv | d2d455e | calibration-set |
| patient (F8) | as_implanted | RIND5(gtcore) V100 38.7 % / D90 2653 cGy; CTV_GT V100 91.7 % / D90 6167 cGy; REACH10 V100 96.0 % / D90 6700 cGy; tiles  ( full +  half), overlaps | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | as_implanted_fit_count8 | RIND5(gtcore) V100 33.6 % / D90 2431 cGy; CTV_GT V100 83.0 % / D90 5391 cGy; REACH10 V100 88.5 % / D90 5845 cGy; tiles 7 (7 full + 0 half), overlaps 2 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | as_implanted_fit_6full_4half | RIND5(gtcore) V100 38.7 % / D90 2653 cGy; CTV_GT V100 91.7 % / D90 6167 cGy; REACH10 V100 96.0 % / D90 6700 cGy; tiles 10 (6 full + 4 half), overlaps 3 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_rind5_sa_seed0 seed 0 | RIND5(gtcore) V100 50.3 % / D90 3141 cGy; CTV_GT V100 85.5 % / D90 5706 cGy; REACH10 V100 88.5 % / D90 5898 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_rind5_sa_seed1 seed 1 | RIND5(gtcore) V100 50.3 % / D90 3141 cGy; CTV_GT V100 85.5 % / D90 5706 cGy; REACH10 V100 88.5 % / D90 5898 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_rind5_sa_seed2 seed 2 | RIND5(gtcore) V100 50.3 % / D90 3141 cGy; CTV_GT V100 85.5 % / D90 5706 cGy; REACH10 V100 88.5 % / D90 5898 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_ctvgt_sa_seed0 seed 0 | RIND5(gtcore) V100 45.4 % / D90 3147 cGy; CTV_GT V100 97.6 % / D90 6739 cGy; REACH10 V100 98.0 % / D90 6867 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_ctvgt_sa_seed1 seed 1 | RIND5(gtcore) V100 41.6 % / D90 3165 cGy; CTV_GT V100 98.2 % / D90 6783 cGy; REACH10 V100 99.2 % / D90 7073 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_ctvgt_sa_seed2 seed 2 | RIND5(gtcore) V100 41.6 % / D90 3165 cGy; CTV_GT V100 98.2 % / D90 6783 cGy; REACH10 V100 99.2 % / D90 7073 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_rind5_sa_mean | RIND5(gtcore) V100 50.3 % / D90 3141 cGy; CTV_GT V100 85.5 % / D90 5706 cGy; REACH10 V100 88.5 % / D90 5898 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_rind5_sa_best seed 0 | RIND5(gtcore) V100 50.3 % / D90 3141 cGy; CTV_GT V100 85.5 % / D90 5706 cGy; REACH10 V100 88.5 % / D90 5898 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_ctvgt_sa_mean | RIND5(gtcore) V100 42.9 % / D90 3159 cGy; CTV_GT V100 98.0 % / D90 6769 cGy; REACH10 V100 98.8 % / D90 7004 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |
| patient (F8) | optimized_ctvgt_sa_best seed 1 | RIND5(gtcore) V100 41.6 % / D90 3165 cGy; CTV_GT V100 98.2 % / D90 6783 cGy; REACH10 V100 99.2 % / D90 7073 cGy; tiles 8 (8 full + 0 half), overlaps 0 | docs/figures/optimize/data/f8_patient_summary.csv | 085fe60 | calibration-set |

Generated by `scripts/paper_tables.py` at tree `056334c` from the committed data; the CSV next to this file carries a `locator` column per row.
