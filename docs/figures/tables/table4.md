## Table 4 -- runtime per stage per dataset

Hardware: AMD Ryzen 5 7600X 6-Core Processor, 12 logical cores, 31 GB RAM, Windows 11; Python 3.12.10, numpy 2.5.2. One run per stage with the machine otherwise idle (scripts/table4_runtimes.py, Phase 4 campaign); wall-clock seconds (perf_counter).

| dataset | block | stage | seconds | note | source | commit |
|---|---|---|---|---|---|---|
| PH8 | a | io: load_volume (DICOM series) | 1.4 | 157x512x512 voxels @ 0.59x0.59x1.00 mm | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | reconstruct: seed detection | 0.3 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | reconstruct: metal inpainting | 0.2 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | reconstruct: head segmentation | 13.4 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | reconstruct: cavity segmentation | 1.5 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | reconstruct: surface meshes | 0.4 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | reconstruct: tile fitting | 1.6 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | group: seeds | 0.4 | = seed detection + metal inpainting | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | group: segment | 15.3 | = head segmentation + cavity segmentation + surface meshes | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | group: tiles | 1.6 | = tile fitting | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | a | reconstruct total (count-free tile fit) | 17.6 | n_full_tiles='auto'; 8 tiles | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | io: load_volume (DICOM series) | 1.4 | 204x512x512 voxels @ 0.52x0.52x1.00 mm | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | reconstruct: seed detection | 0.4 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | reconstruct: metal inpainting | 1.2 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | reconstruct: head segmentation | 5.0 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | reconstruct: cavity segmentation | 2.6 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | reconstruct: surface meshes | 4.0 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | reconstruct: tile fitting | 24.3 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | group: seeds | 1.5 | = seed detection + metal inpainting | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | group: segment | 11.7 | = head segmentation + cavity segmentation + surface meshes | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | group: tiles | 24.3 | = tile fitting | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | a | reconstruct total (count-free tile fit) | 38.1 | n_full_tiles='auto'; 6 tiles | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | io: load_volume (DICOM series) | 2.0 | 248x512x512 voxels @ 0.68x0.68x1.00 mm | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | reconstruct: seed detection | 0.1 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | reconstruct: head segmentation | 14.7 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | reconstruct: cavity segmentation | 2.5 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | reconstruct: surface meshes | 0.3 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | reconstruct: tile fitting | 0.0 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | group: seeds | 0.1 | = seed detection | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | group: segment | 17.5 | = head segmentation + cavity segmentation + surface meshes | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | group: tiles | 0.0 | = tile fitting | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH0 | a | reconstruct total (count-free tile fit) | 17.6 | n_full_tiles='auto'; 0 tiles | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | c | planner U: compute_dose_grid 2 mm (tabulated kernel) | 0.7 | seeds +- 40 mm pad; wall = phantom shell (no cavity segmented) | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | c | planner U: isodose_surfaces (30, 60 Gy) | 0.0 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | c | planner V: build_hrctv (RIND5 5 mm, 1 mm grid) | 1.0 | mesh-offset fallback (shell); clip gtcore:body; built once per session | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | c | planner V: HR-CTV exact-engine scoring (dose_stats) | 1.4 | D90 374 cGy, V100 0.133, 225.7 cc | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | c | planner recompute latency per edit (U + rind scoring) | 2.1 | dose grid + isodoses + rind scoring (the rind is built once) | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | c | planner U: compute_dose_grid 2 mm (tabulated kernel) | 0.6 | seeds +- 40 mm pad; wall = cavity wall | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | c | planner U: isodose_surfaces (30, 60 Gy) | 0.0 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | c | planner V: build_hrctv (RIND5 5 mm, 1 mm grid) | 0.4 | RIND5(cavity); clip gtcore:cranial_interior; built once per session | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | c | planner V: HR-CTV exact-engine scoring (dose_stats) | 0.2 | D90 2673 cGy, V100 0.395, 52.7 cc | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | c | planner recompute latency per edit (U + rind scoring) | 0.8 | dose grid + isodoses + rind scoring (the rind is built once) | docs/figures/data/table4_runtimes.csv | 4748ccb |
| synthetic s1_x1.00 | b | optimizer: target (+5 mm shell of the eligible wall) | 0.0 | synthetic cavity 24.4 mL, 4178 mm2, all faces eligible | docs/figures/data/table4_runtimes.csv | 4748ccb |
| synthetic s1_x1.00 | b | optimizer: build_candidates (h 2.5 mm, 6 spins) | 23.9 | synthetic cavity 24.4 mL, 4178 mm2, all faces eligible | docs/figures/data/table4_runtimes.csv | 4748ccb |
| synthetic s1_x1.00 | b | optimizer: build_influence (M_opt 4000) | 2.2 | synthetic cavity 24.4 mL, 4178 mm2, all faces eligible | docs/figures/data/table4_runtimes.csv | 4748ccb |
| synthetic s1_x1.00 | b | optimizer: build_conflicts | 212.0 | synthetic cavity 24.4 mL, 4178 mm2, all faces eligible | docs/figures/data/table4_runtimes.csv | 4748ccb |
| synthetic s1_x1.00 | b | optimizer: solve_sa 8 x 60, N = 8 | 30.2 | synthetic cavity 24.4 mL, 4178 mm2, all faces eligible | docs/figures/data/table4_runtimes.csv | 4748ccb |
| synthetic s1_x1.00 | b | optimizer: final_report (1 mm grid, RIND5) | 15.6 | synthetic cavity 24.4 mL, 4178 mm2, all faces eligible | docs/figures/data/table4_runtimes.csv | 4748ccb |
| synthetic s1_x1.00 | b | optimizer: plan.optimize total (SA, N = 8) | 283.9 | synthetic cavity 24.4 mL, 4178 mm2, all faces eligible; eligible 100.0 % of the wall area; RIND5 V100 1.000 D90 7878 cGy; 0 overlaps | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | phantom-ref: phantom_wall_and_cavity (register PH0, lumen, wall) | 10.2 | Dice 0.979; cavity 55.3 cc; wall 8443 mm2 | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | eligibility: visible from the implant centre | 2.3 | 27594 of 42627 faces, 64.8 % of the area | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | optimizer: target (+5 mm shell of the eligible wall) | 0.0 | registered PH0 wall 8443 mm2, eligible = visible from the implant | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | optimizer: build_candidates (h 2.5 mm, 6 spins) | 24.6 | registered PH0 wall 8443 mm2, eligible = visible from the implant | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | optimizer: build_influence (M_opt 4000) | 0.4 | registered PH0 wall 8443 mm2, eligible = visible from the implant | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | optimizer: build_conflicts | 4.3 | registered PH0 wall 8443 mm2, eligible = visible from the implant | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | optimizer: solve_sa 8 x 60, N = 8 | 55.7 | registered PH0 wall 8443 mm2, eligible = visible from the implant | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | optimizer: final_report (1 mm grid, RIND5) | 25.6 | registered PH0 wall 8443 mm2, eligible = visible from the implant | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PH8 | b | optimizer: plan.optimize total (SA, N = 8) | 110.6 | registered PH0 wall 8443 mm2, eligible = visible from the implant; eligible 64.8 % of the wall area; RIND5 V100 0.572 D90 3480 cGy; 0 overlaps | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | eligibility: craniotomy_opening | 1.9 | opening 28.7 cm2 | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | eligibility: craniotomy_faces (facing + line of sight, no implant term) | 5.8 | 6508 of 24736 faces, 26.5 % of the area | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | optimizer: target (+5 mm shell of the eligible wall) | 0.0 | gtcore cavity wall, 13261 mm2, craniotomy-side eligible wall | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | optimizer: build_candidates (h 2.5 mm, 6 spins) | 24.9 | gtcore cavity wall, 13261 mm2, craniotomy-side eligible wall | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | optimizer: build_influence (M_opt 4000) | 0.5 | gtcore cavity wall, 13261 mm2, craniotomy-side eligible wall | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | optimizer: build_conflicts | 8.8 | gtcore cavity wall, 13261 mm2, craniotomy-side eligible wall | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | optimizer: solve_sa 8 x 60, N = 8 | 55.6 | gtcore cavity wall, 13261 mm2, craniotomy-side eligible wall | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | optimizer: final_report (1 mm grid, RIND5) | 23.0 | gtcore cavity wall, 13261 mm2, craniotomy-side eligible wall | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | optimizer: plan.optimize total (SA, N = 8) | 112.9 | gtcore cavity wall, 13261 mm2, craniotomy-side eligible wall; eligible 26.5 % of the wall area; RIND5 V100 0.474 D90 3329 cGy; 0 overlaps | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | all faces: build_candidates (h 2.5 mm, 6 spins) | 54.2 | rejected {'ineligible': 0, 'hanging': 4721, 'detached': 1180, 'conform_error': 0} | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | all faces: build_conflicts | 38.7 | 124726 pairs | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | all faces: candidates + conflicts (rev-3 item 17 threshold ~300 s) | 92.8 | within 5 min | docs/figures/data/table4_runtimes.csv | 4748ccb |
| PT-CT1mm | b | all faces: build_influence (M_opt 4000) | 0.9 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |
| all | - | script wall time | 704.4 |  | docs/figures/data/table4_runtimes.csv | 4748ccb |

Generated by `scripts/paper_tables.py` at tree `d7134a9` from the committed data; the CSV next to this file carries a `locator` column per row.
