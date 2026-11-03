# Branch status (snapshot 2026-11-03, Phase 5 of Paper 2)

Remotes: `origin` (https://github.com/Peelyboneheader/gtcore.git) and `leesjacob`
(https://github.com/leesjacob/gtcore.git). The paper's integration branch is
`paper2/integration`. `main` is still at `aff83c0`; every commit on `main` is
contained in `paper2/integration`, so `main` fast-forwards to it when Phase 5 is tagged
(`v1.0-medphys`, pushed with `main` to `leesjacob`). Each branch has its own git
worktree in a sibling folder (`gt-worktrees/<name>`) next to the main checkout.

## Merged into `paper2/integration` (no commits outside it; worktree and branch can go)

| Group | Branches |
|---|---|
| Paper 2 Phase 1 | `p2/strict-exceptions`, `p2/vault-filter`, `p2/footprint-rcond`, `p2/cavity-reach`, `p2/packaging` |
| Paper 2 Phase 2 | `p2/targets`, `p2/rt-io`, `p2/dose-stats`, `p2/report-rind5` |
| Paper 2 Phase 3 | `p2/clinical-rebuild`, `p2/uncertainty`, `p2/engine-qa`, `p2/seed-wall`, `p2/gamma`, `p2/leftovers`, `p2/formalism`, `p2/clinical-followups` |
| Paper 2 Phase 4 | `p2/opt-constants-bound`, `p2/opt-eligibility-phantom` |
| Optimizer build (Oct 2026) | `plan/candidates`, `plan/influence`, `plan/solvers`, `plan/milp`, `plan/validation`, `plan/ui`, `plan/review` |
| Localization upgrade (Oct 2026) | `loc/harness`, `loc/refine`, `loc/mergesplit`, `loc/scoring`, `loc/hwls`, `loc/proxies`, `loc/integration`, `loc/main-merge`, `feature/localization` |
| Earlier features | `feature/dose-engine`, `feature/hrctv`, `feature/reconstruction`, `feature/tile-autogen`, `feature/tile-inference`, `feature/tile-selection`, `feature/ui-interaction`, `feature/validation`, `fix/planner-key-sync`, `main` |

Removal, after Phase 5 is tagged (from the main checkout; `-d` refuses an unmerged branch):

```
git worktree remove ../gt-worktrees/<name>
git branch -d <branch>
```

Worktrees whose branch is in the table above: `p2-strict-exceptions`, `p2-vault-filter`,
`p2-footprint-rcond`, `p2-cavity-reach`, `p2-packaging`, `p2-targets`, `p2-rt-io`,
`p2-dose-stats`, `p2-report-rind5`, `p2-clinical-rebuild`, `p2-uncertainty`,
`p2-engine-qa`, `p2-seed-wall`, `p2-gamma`, `p2-leftovers`, `p2-formalism`,
`p2-clinical-followups`, `p2-opt-constants-bound`, `p2-opt-eligibility-phantom`,
`plan-candidates`, `plan-influence`, `plan-solvers`, `plan-milp`, `plan-validation`,
`plan-ui`, `plan-review`, `loc-harness`, `loc-refine`, `loc-mergesplit`, `loc-scoring`,
`loc-hwls`, `loc-proxies`, `loc-integration`, `loc-main-merge`, `dose-engine`, `hrctv`,
`reconstruction`, `tile-autogen`, `tile-inference`, `tile-selection`, `ui-interaction`,
`validation`, `planner-keys`, `main-objective` (branch `main`).

## In flight (Phase 5; the coordinator merges them)

| Branch | Worktree | Task |
|---|---|---|
| `p2/campaign` | `p2-campaign` | Phase 4 optimizer campaign (owns `scripts/validation_optimize.py`, `docs/optimize-notes.md`, `docs/figures/optimize/`) |
| `p2/cavity-change` | `p2-cavity-change` | `scripts/cavity_change.py` (cavity across the loop, F3) |
| `p2/paper-figures` | `p2-paper-figures` | `scripts/paper_figures.py`, `scripts/_figstyle.py` |
| `p2/paper-tables` | `p2-paper-tables` | `scripts/paper_tables.py`, `docs/results-summary.md`, `docs/figures/tables/` |
| `p2/scrub-readme` | `p2-scrub-readme` | identifier/path scrub, dataset resolver, README |

## Not merged by design (keep or delete; nothing in the paper depends on them)

| Branch | Worktree | Content |
|---|---|---|
| `plan/scout-objective` | `plan-scout-objective` | scout 7.1; report + script copied (`docs/scout-objective.md`) |
| `plan/scout-milp-bound` | `plan-scout-milp-bound` | scout 7.3; report + script copied (`docs/scout-milp-bound.md`) |
| `plan/scout-continuous` | `plan-scout-continuous` | scout 7.4; report + script copied (`docs/scout-continuous.md`) |
| `plan/continuous-perf` | `plan-continuous-perf` | continuous solver overlap/deadline tweak (2 commits); SA stays the default |
| `notes/clinical-audit` | `notes-clinical-audit` | independent audit of the clinical case (2026-10-08 doc + figures); **its doc still names the patient folder**: scrub it (dataset ids) before it is ever merged |

## To resume

- Test command (from a worktree root, the project venv's python):
  `GTCORE_STRICT=1 python -m pytest -q -p no:cacheprovider` (about 10 min; run it
  alone, the off-screen VTK planner tests hang when several suites share the GPU).
- Data: `GT_DATA_ROOT` + an uncommitted `GT_DATASETS_JSON` map (format:
  `docs/datasets.example.json`; README, "Data").
- Identifier check: `python scripts/check_no_identifiers.py` (`--strict` includes the
  campaign-owned files still allowlisted).
