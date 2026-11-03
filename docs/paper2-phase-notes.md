# Paper 2 — phase notes

Dated sections per task (one per agent branch); the coordinator merges them.
Every number that may appear in the paper carries the exact command and the
commit it was run at. Paths are relative to the repository or use
`$GT_DATA_ROOT` / `<scratchpad>`; no machine paths.

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
| (next commit) | this notes section |

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
