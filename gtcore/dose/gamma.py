"""Gamma-index agreement between a reference dose grid and gtcore's TG-43 dose.

This module is a thin wrapper over ``pymedphys.gamma`` -- the gamma index of
Low et al. [1]_ evaluated with the shell-search algorithm of Wendling et
al. [2]_ as implemented in PyMedPhys [3]_.  **No gamma index is computed in
gtcore**; this module only prepares the two grids, calls
``pymedphys.gamma`` and summarises its output.

The comparison it is written for (Paper 2, F5 / Table 3): the TPS dose
(RTDOSE, ``gtcore.io.rt.load_rtdose``, cGy) is the *reference* and
gtcore's TG-43 dose from the recovered seeds is the *evaluation*.

* Reference = the RTDOSE voxel values on their own grid, restricted to the
  bounding box of the voxels at or above the lower cutoff (30 Gy) plus a
  margin.  Every reference voxel becomes the centre of one gamma search.
* Evaluation = ``gtcore.dose.dose_at_points`` (exact 2D line-source rate)
  on a regular ``eval_spacing_mm`` (0.5 mm) lattice covering the same box
  plus the search margin; pymedphys interpolates this lattice trilinearly
  along the search shells.
* Lower cutoff: pymedphys only knows a cutoff expressed as a percent of its
  normalisation dose, so ``lower_cutoff_cgy`` is translated to
  ``lower_percent_dose_cutoff = 100 * lower_cutoff_cgy / global_norm_cgy``
  and the mask pymedphys derives from it is asserted equal to
  ``reference >= lower_cutoff_cgy``.  For local gamma the normalisation
  only sets that cutoff.
* Upper cutoff (``upper_cutoff_cgy``, 200 Gy): a post-hoc selection of
  reference voxels.  The gamma of one reference voxel depends only on the
  evaluation grid, never on other reference voxels, so computing once over
  ``>= lower`` and reporting the ``lower..upper`` window afterwards is exact
  (``tests/test_gamma.py`` checks this against a pre-filtered run).  Both
  windows are reported: ``pass_rate`` (lower..upper, primary) and
  ``pass_rate_ge30`` (``>= lower``, no upper cut).
* Normalisation: ``local=False`` is global-to-rx (dose differences in
  percent of ``global_norm_cgy``, 6000 cGy); ``local=True`` is local gamma
  (percent of the reference voxel's own dose).  Global-to-rx is lenient
  where the reference exceeds rx (near the seeds) and stricter below it.
* Pass rule: ``gamma <= 1`` (pymedphys' own ``calculate_pass_rate`` uses
  ``< 1``; the difference is measure-zero).
* ``max_gamma`` (2.0) bounds the search distance to
  ``max_gamma * dist_mm`` and caps the reported gamma at that value; pass
  rates are unaffected, the histogram's last bin is ``>= max_gamma``.
* Interpolation backend: ``interp_algo="auto"`` uses pymedphys' numba
  interpolator when ``numba`` imports and falls back to
  ``interp_algo="scipy"`` (``scipy.interpolate.RegularGridInterpolator``)
  otherwise; both give the same gamma to ~1e-13 (see the Phase 1 packaging
  notes in docs/paper2-phase-notes.md).

Methods wording (pymedphys call, for the paper)::

    pymedphys.gamma((z, y, x), reference_cgy, (z', y', x'), evaluation_cgy,
                    dose_percent_threshold, distance_mm_threshold,
                    lower_percent_dose_cutoff=100 * 3000 / 6000,
                    global_normalisation=6000, local_gamma=<False|True>,
                    max_gamma=2.0, interp_fraction=10, interp_algo="pymedphys")

References
----------
.. [1] Low DA, Harms WB, Mutic S, Purdy JA. A technique for the quantitative
       evaluation of dose distributions. Med Phys 1998;25:656-661.
       doi:10.1118/1.598248
.. [2] Wendling M, Zijp LJ, McDermott LN, et al. A fast algorithm for gamma
       evaluation in 3D. Med Phys 2007;34:1647-1654. doi:10.1118/1.2721657
.. [3] Biggs S, Jennings M, Swerdloff S, et al. PyMedPhys: A community
       effort to develop an open, Python-based standard library for medical
       physics applications. J Open Source Softw 2022;7(78):4555.
       doi:10.21105/joss.04555  (``pymedphys.gamma``; version recorded in
       ``GammaResult.settings["pymedphys_version"]``)
"""
from __future__ import annotations

import importlib
import math
import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np

from ..volume import Volume
from .engine import TG43Engine, dose_at_points
from .targets import _git_commit

__all__ = [
    "Evaluation",
    "GammaResult",
    "evaluation_lattice",
    "gamma_analysis",
    "gamma_histogram",
    "grid_axes",
    "pass_rate",
    "prepare_evaluation",
    "pymedphys_version",
    "reference_box",
    "resolve_interp_algo",
    "run_pymedphys_gamma",
]

#: Default histogram bin width and upper edge (the last bin is ``>= max``).
HISTOGRAM_BIN_WIDTH = 0.1
HISTOGRAM_MAX = 2.0


# ------------------------------------------------------------------ backend
def pymedphys_version() -> str:
    """Version string of the installed pymedphys (raises ImportError if absent)."""
    pmp = importlib.import_module("pymedphys")
    return str(getattr(pmp, "__version__", "unknown"))


def resolve_interp_algo(interp_algo: str = "auto") -> str:
    """Map ``"auto"`` to pymedphys' interpolation backend.

    ``"pymedphys"`` (numba-jitted trilinear interpolation) when ``numba``
    imports, else ``"scipy"``.  Explicit ``"pymedphys"`` / ``"scipy"`` are
    passed through unchanged.
    """
    algo = str(interp_algo).lower()
    if algo in ("pymedphys", "scipy"):
        return algo
    if algo != "auto":
        raise ValueError("interp_algo must be 'auto', 'pymedphys' or 'scipy', got %r"
                         % (interp_algo,))
    try:
        importlib.import_module("numba")
    except Exception:                                  # ImportError or a broken install
        return "scipy"
    return "pymedphys"


# ----------------------------------------------------------------- geometry
def grid_axes(volume: Volume) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-axis voxel-centre coordinates ``(zs, ys, xs)`` of an axis-aligned Volume.

    ``zs`` runs along the array's k axis, ``ys`` along j and ``xs`` along i,
    so ``(zs, ys, xs)`` is the ``axes`` tuple pymedphys expects for an array
    indexed ``[k, j, i]``.  Axes may be descending (RTDOSE x and y are, after
    the LPS->RAS flip); the affine must be axis-aligned (direction matrix a
    signed identity), otherwise ``ValueError``.
    """
    aff = np.asarray(volume.affine, dtype=float)
    direction = aff[:3, :3] / np.linalg.norm(aff[:3, :3], axis=0)
    if not np.allclose(np.abs(direction), np.eye(3), atol=1e-6):
        raise ValueError("gamma needs an axis-aligned grid; direction matrix is\n%r"
                         % (direction,))
    ni, nj, nk = volume.shape_ijk
    origin = aff[:3, 3]
    xs = origin[0] + aff[0, 0] * np.arange(ni, dtype=float)
    ys = origin[1] + aff[1, 1] * np.arange(nj, dtype=float)
    zs = origin[2] + aff[2, 2] * np.arange(nk, dtype=float)
    return zs, ys, xs


def reference_box(reference: Volume, lower_cutoff_cgy: float = 3000.0,
                  margin_mm: float = 5.0) -> Tuple[Tuple[slice, slice, slice], Volume]:
    """Restrict ``reference`` to the bounding box of ``>= lower_cutoff_cgy`` plus a margin.

    Returns ``(slices_kji, box_volume)``; the box affine is the reference
    affine translated to the box corner, so voxel centres are unchanged.
    """
    arr = np.asarray(reference.array, dtype=float)
    above = arr >= float(lower_cutoff_cgy)
    if not above.any():
        raise ValueError("no reference voxel reaches the lower cutoff (%.0f cGy)"
                         % lower_cutoff_cgy)
    idx = np.argwhere(above)
    lo_kji, hi_kji = idx.min(axis=0), idx.max(axis=0)
    spacing_kji = np.asarray(reference.spacing, dtype=float)[::-1]
    pad = np.ceil(float(margin_mm) / spacing_kji - 1e-9).astype(int)
    lo = np.maximum(lo_kji - pad, 0)
    hi = np.minimum(hi_kji + pad, np.asarray(arr.shape) - 1)
    slices = tuple(slice(int(a), int(b) + 1) for a, b in zip(lo, hi))
    affine = np.asarray(reference.affine, dtype=float).copy()
    affine[:3, 3] = reference.index_to_ras(np.array([lo[2], lo[1], lo[0]], dtype=float))
    meta = dict(reference.meta)
    meta.update({"box_slices_kji": [[int(s.start), int(s.stop)] for s in slices],
                 "box_lower_cutoff_cgy": float(lower_cutoff_cgy),
                 "box_margin_mm": float(margin_mm)})
    return slices, Volume(arr[slices].copy(), affine, meta)


def evaluation_lattice(box: Volume, spacing_mm: float = 0.5,
                       margin_mm: float = 4.5) -> Tuple[Tuple[np.ndarray, np.ndarray, np.ndarray],
                                                        np.ndarray, Tuple[int, int, int]]:
    """Regular ascending lattice covering ``box`` plus ``margin_mm`` on every side.

    The lattice is anchored on the box's minimum-coordinate voxel centre
    (shifted outward by the margin rounded up to whole steps), so reference
    voxel centres coincide with lattice points along any axis whose
    reference spacing is a multiple of ``spacing_mm``.  Returns
    ``((zs, ys, xs), affine, shape_kji)`` with every axis ascending and
    C-contiguous (pymedphys' numba interpolator requires both).
    """
    spacing = float(spacing_mm)
    if spacing <= 0.0:
        raise ValueError("spacing_mm must be positive")
    lo, hi = box.bounds_ras()                                    # voxel-centre extremes
    n_margin = int(math.ceil(float(margin_mm) / spacing - 1e-9))
    start = lo - n_margin * spacing
    counts = np.floor((hi - start) / spacing + 1e-9).astype(int) + 1 + n_margin
    xs = np.ascontiguousarray(start[0] + spacing * np.arange(counts[0], dtype=float))
    ys = np.ascontiguousarray(start[1] + spacing * np.arange(counts[1], dtype=float))
    zs = np.ascontiguousarray(start[2] + spacing * np.arange(counts[2], dtype=float))
    affine = np.eye(4)
    affine[0, 0] = affine[1, 1] = affine[2, 2] = spacing
    affine[:3, 3] = start
    return (zs, ys, xs), affine, (int(counts[2]), int(counts[1]), int(counts[0]))


def _lattice_points(axes_zyx) -> np.ndarray:
    zs, ys, xs = axes_zyx
    zz, yy, xx = np.meshgrid(zs, ys, xs, indexing="ij")
    return np.stack([xx.ravel(), yy.ravel(), zz.ravel()], axis=1)


def _voxel_centres(volume: Volume) -> np.ndarray:
    """RAS centres of every voxel of ``volume`` in ``array.ravel()`` order."""
    nk, nj, ni = volume.array.shape
    kk, jj, ii = np.indices((nk, nj, ni))
    ijk = np.stack([ii.ravel(), jj.ravel(), kk.ravel()], axis=1).astype(float)
    return volume.index_to_ras(ijk)


# --------------------------------------------------------------- evaluation
@dataclass
class Evaluation:
    """gtcore's dose prepared for one seed set (reusable across criteria).

    ``reference_box`` is the reference restricted to the cutoff bounding
    box; ``lattice`` the gtcore dose on the fine lattice (what pymedphys
    interpolates); ``dose_at_box_centres`` the exact gtcore dose at the
    reference voxel centres of the box (for the difference volume).
    """

    box_slices: Tuple[slice, slice, slice]
    reference_box: Volume
    lattice: Volume
    dose_at_box_centres: np.ndarray
    seed_centers: np.ndarray
    seed_axes: np.ndarray
    sk_u: np.ndarray
    runtime_s: float
    settings: Dict[str, Any] = field(default_factory=dict)

    @property
    def difference_volume(self) -> Volume:
        """Evaluation minus reference at the reference voxel centres [cGy]."""
        diff = self.dose_at_box_centres.reshape(self.reference_box.array.shape) \
            - np.asarray(self.reference_box.array, dtype=float)
        meta = {"units": "cGy", "kind": "evaluation_minus_reference",
                "n_seeds": int(self.seed_centers.shape[0])}
        return Volume(diff, self.reference_box.affine.copy(), meta)


def prepare_evaluation(reference: Volume, seed_centers, seed_axes, sk_u=TG43Engine.DEFAULT_SK_U,
                       lower_cutoff_cgy: float = 3000.0, box_margin_mm: float = 5.0,
                       eval_spacing_mm: float = 0.5, search_margin_mm: float = 4.5,
                       engine: Optional[TG43Engine] = None, exact: bool = True,
                       elapsed_hours: Optional[float] = None) -> Evaluation:
    """Build the reference box and gtcore's evaluation lattice for one seed set.

    ``search_margin_mm`` must cover the largest search distance of the
    criteria the evaluation will serve (``max_gamma * dist_mm``, plus one
    lattice step); :func:`gamma_analysis` checks that.
    """
    t0 = time.perf_counter()
    centers = np.asarray(seed_centers, dtype=float).reshape(-1, 3)
    axes = np.asarray(seed_axes, dtype=float).reshape(-1, 3)
    sk = np.broadcast_to(np.asarray(sk_u, dtype=float), (centers.shape[0],)).astype(float)
    eng = engine if engine is not None else TG43Engine()

    slices, box = reference_box(reference, lower_cutoff_cgy, box_margin_mm)
    lat_axes, lat_affine, lat_shape = evaluation_lattice(box, eval_spacing_mm, search_margin_mm)
    lat_dose = dose_at_points(centers, axes, _lattice_points(lat_axes), sk_per_seed_u=sk,
                              engine=eng, exact=exact, elapsed_hours=elapsed_hours)
    lattice = Volume(lat_dose.reshape(lat_shape), lat_affine,
                     {"units": "cGy", "kind": "tg43_evaluation_lattice",
                      "spacing_mm": float(eval_spacing_mm), "n_seeds": int(centers.shape[0])})
    at_centres = dose_at_points(centers, axes, _voxel_centres(box), sk_per_seed_u=sk,
                                engine=eng, exact=exact, elapsed_hours=elapsed_hours)
    runtime = time.perf_counter() - t0
    settings = {
        "n_seeds": int(centers.shape[0]),
        "sk_u": sk.tolist() if np.ptp(sk) > 0 else float(sk[0]),
        "engine_dataset": getattr(getattr(eng, "dataset", None), "name", None),
        "engine_exact": bool(exact),
        "elapsed_hours": elapsed_hours,
        "lower_cutoff_cgy": float(lower_cutoff_cgy),
        "box_margin_mm": float(box_margin_mm),
        "box_shape_kji": [int(s) for s in box.array.shape],
        "box_bounds_ras": np.asarray(box.bounds_ras()).round(3).tolist(),
        "reference_shape_kji": [int(s) for s in reference.array.shape],
        "reference_spacing_mm": np.asarray(reference.spacing).round(4).tolist(),
        "eval_spacing_mm": float(eval_spacing_mm),
        "search_margin_mm": float(search_margin_mm),
        "eval_shape_kji": [int(s) for s in lat_shape],
        "eval_bounds_ras": np.asarray(lattice.bounds_ras()).round(3).tolist(),
        "eval_n_points": int(np.prod(lat_shape)),
        "runtime_eval_s": float(runtime),
    }
    return Evaluation(slices, box, lattice, np.asarray(at_centres, dtype=float),
                      centers, axes, sk, runtime, settings)


# -------------------------------------------------------------------- gamma
def run_pymedphys_gamma(axes_reference, dose_reference, axes_evaluation, dose_evaluation,
                        dose_pct: float, dist_mm: float, lower_cutoff_cgy: float,
                        global_norm_cgy: float, local: bool = False, max_gamma: float = 2.0,
                        interp_algo: str = "auto", interp_fraction: int = 10,
                        ram_available: Optional[int] = None) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Call ``pymedphys.gamma`` with the cutoff translated to its percent convention.

    Returns ``(gamma, settings)``; ``gamma`` has the reference shape, NaN
    where the reference is below ``lower_cutoff_cgy`` (not computed).
    Raises ``AssertionError`` if the mask pymedphys derives from
    ``lower_percent_dose_cutoff`` differs from ``dose_reference >= lower_cutoff_cgy``.
    """
    pmp = importlib.import_module("pymedphys")
    dose_reference = np.asarray(dose_reference, dtype=float)
    dose_evaluation = np.ascontiguousarray(np.asarray(dose_evaluation, dtype=float))
    axes_evaluation = tuple(np.ascontiguousarray(np.asarray(a, dtype=float)) for a in axes_evaluation)
    axes_reference = tuple(np.asarray(a, dtype=float) for a in axes_reference)
    for a in axes_evaluation:
        if a.size < 2 or not np.all(np.diff(a) > 0):
            raise ValueError("evaluation axes must be ascending (pymedphys interpolation)")

    norm = float(global_norm_cgy)
    if norm <= 0.0:
        raise ValueError("global_norm_cgy must be positive")
    lower_pct = 100.0 * float(lower_cutoff_cgy) / norm
    # pymedphys: lower_dose_cutoff = lower_percent_dose_cutoff / 100 * global_normalisation,
    # mask = dose_reference >= lower_dose_cutoff.  Replicate and assert equality.
    pmp_cutoff = lower_pct / 100 * norm
    with np.errstate(invalid="ignore"):
        want = dose_reference >= float(lower_cutoff_cgy)
        got = dose_reference >= pmp_cutoff
    if not np.array_equal(want, got):
        raise AssertionError(
            "pymedphys' percent cutoff (%.6f %% of %.1f cGy = %.9f cGy) does not reproduce "
            "reference >= %.1f cGy (%d voxels differ)"
            % (lower_pct, norm, pmp_cutoff, lower_cutoff_cgy, int((want != got).sum())))

    algo = resolve_interp_algo(interp_algo)
    kwargs = dict(lower_percent_dose_cutoff=lower_pct, interp_fraction=int(interp_fraction),
                  max_gamma=float(max_gamma), local_gamma=bool(local),
                  global_normalisation=norm, skip_once_passed=False, random_subset=None)
    if ram_available is not None:
        kwargs["ram_available"] = int(ram_available)

    def _call(a):
        return pmp.gamma(axes_reference, dose_reference, axes_evaluation, dose_evaluation,
                         float(dose_pct), float(dist_mm), interp_algo=a, **kwargs)

    t0 = time.perf_counter()
    try:
        gamma = _call(algo)
    except ImportError as exc:                      # numba missing/broken after all
        if algo != "pymedphys":
            raise
        warnings.warn("pymedphys' numba interpolation unavailable (%s); falling back to "
                      "interp_algo='scipy'" % exc)
        algo = "scipy"
        gamma = _call(algo)
    runtime = time.perf_counter() - t0
    gamma = np.asarray(gamma, dtype=float)

    computed = ~np.isnan(gamma)
    n_unresolved = int((want & ~computed).sum())
    if n_unresolved:
        warnings.warn("%d reference voxels above the cutoff got no gamma (search exhausted "
                      "inside the evaluation lattice?); they count as failures" % n_unresolved)
    if (computed & ~want).any():
        raise AssertionError("pymedphys computed gamma below the lower cutoff")

    settings = {
        "pymedphys_version": pymedphys_version(),
        "pymedphys_call": (
            "pymedphys.gamma(axes_reference, dose_reference_cgy, axes_evaluation, "
            "dose_evaluation_cgy, dose_percent_threshold=%g, distance_mm_threshold=%g, "
            "lower_percent_dose_cutoff=%g, interp_fraction=%d, max_gamma=%g, "
            "local_gamma=%s, global_normalisation=%g, skip_once_passed=False, "
            "random_subset=None, interp_algo=%r)"
            % (float(dose_pct), float(dist_mm), lower_pct, int(interp_fraction),
               float(max_gamma), bool(local), norm, algo)),
        "dose_pct": float(dose_pct),
        "dist_mm": float(dist_mm),
        "normalisation": "local" if local else "global",
        "local_gamma": bool(local),
        "global_normalisation_cgy": norm,
        "lower_cutoff_cgy": float(lower_cutoff_cgy),
        "lower_percent_dose_cutoff": lower_pct,
        "max_gamma": float(max_gamma),
        "interp_fraction": int(interp_fraction),
        "interp_algo_requested": str(interp_algo),
        "interp_algo_used": algo,
        "pass_rule": "gamma <= 1",
        "n_unresolved": n_unresolved,
        "runtime_gamma_s": float(runtime),
    }
    return gamma, settings


def pass_rate(gamma: np.ndarray, mask: np.ndarray) -> float:
    """Fraction of ``mask`` voxels with ``gamma <= 1`` (NaN counts as a failure)."""
    mask = np.asarray(mask, dtype=bool)
    n = int(mask.sum())
    if n == 0:
        return float("nan")
    with np.errstate(invalid="ignore"):
        return float(np.count_nonzero(np.asarray(gamma)[mask] <= 1.0)) / n


def gamma_histogram(gamma: np.ndarray, mask: np.ndarray, bin_width: float = HISTOGRAM_BIN_WIDTH,
                    max_edge: float = HISTOGRAM_MAX) -> Tuple[np.ndarray, np.ndarray]:
    """Counts of gamma over ``mask`` in bins ``[0, w), ..., [max-w, max), [max, inf)``.

    Returns ``(edges, counts)`` with ``edges[-1] = inf``; NaN values are
    dropped (they are reported separately as unresolved).
    """
    n_bins = int(round(float(max_edge) / float(bin_width)))
    edges = np.concatenate([np.arange(n_bins + 1, dtype=float) * float(bin_width), [np.inf]])
    vals = np.asarray(gamma, dtype=float)[np.asarray(mask, dtype=bool)]
    vals = vals[~np.isnan(vals)]
    counts, _ = np.histogram(vals, bins=edges)
    return edges, counts.astype(int)


@dataclass
class GammaResult:
    """Output of :func:`gamma_analysis` on the reference box.

    Arrays are indexed ``[k, j, i]`` on ``box_affine`` (the reference grid
    restricted to the cutoff bounding box).  ``gamma`` is NaN where the
    reference is below the lower cutoff.  ``pass_rate`` is over
    ``mask_used`` (``lower <= reference <= upper``, the primary window) and
    ``pass_rate_ge30`` over ``mask_computed`` (``reference >= lower``, no
    upper cut; the name reflects the 30 Gy default).
    """

    gamma: np.ndarray
    box_affine: np.ndarray
    box_slices: Tuple[slice, slice, slice]
    reference_box: Volume
    evaluation: Volume
    mask_computed: np.ndarray
    mask_used: np.ndarray
    pass_rate: float
    pass_rate_ge30: float
    n_points: int
    n_points_ge30: int
    histogram: Dict[str, np.ndarray]
    difference_volume: Volume
    runtime_s: float
    settings: Dict[str, Any] = field(default_factory=dict)

    @property
    def pass_rate_lower_only(self) -> float:
        """Alias of ``pass_rate_ge30`` for non-default lower cutoffs."""
        return self.pass_rate_ge30

    @property
    def gamma_volume(self) -> Volume:
        return Volume(self.gamma, self.box_affine.copy(),
                      {"kind": "gamma_index", "units": "1",
                       "nan": "reference below lower cutoff"})

    def pass_rate_in(self, lower_cgy: float, upper_cgy: float = np.inf) -> float:
        """Pass rate over any post-hoc reference window inside the computed set."""
        ref = np.asarray(self.reference_box.array, dtype=float)
        m = self.mask_computed & (ref >= float(lower_cgy)) & (ref <= float(upper_cgy))
        return pass_rate(self.gamma, m)

    def gamma_stats(self, mask: Optional[np.ndarray] = None) -> Dict[str, float]:
        """mean / median / p95 / max of gamma over ``mask`` (default ``mask_used``)."""
        m = self.mask_used if mask is None else np.asarray(mask, dtype=bool)
        v = self.gamma[m]
        v = v[~np.isnan(v)]
        if v.size == 0:
            return {"mean": float("nan"), "median": float("nan"), "p95": float("nan"),
                    "max": float("nan"), "n": 0}
        return {"mean": float(v.mean()), "median": float(np.median(v)),
                "p95": float(np.percentile(v, 95)), "max": float(v.max()), "n": int(v.size)}


def gamma_analysis(reference: Volume, seed_centers=None, seed_axes=None,
                   sk_u=TG43Engine.DEFAULT_SK_U, dose_pct: float = 3.0, dist_mm: float = 2.0,
                   lower_cutoff_cgy: float = 3000.0, upper_cutoff_cgy: float = 20000.0,
                   local: bool = False, global_norm_cgy: float = 6000.0,
                   eval_spacing_mm: float = 0.5, interp_algo: str = "auto", *,
                   evaluation: Optional[Evaluation] = None, max_gamma: float = 2.0,
                   box_margin_mm: float = 5.0, interp_fraction: int = 10,
                   engine: Optional[TG43Engine] = None, exact: bool = True,
                   elapsed_hours: Optional[float] = None,
                   ram_available: Optional[int] = None) -> GammaResult:
    """Gamma index of gtcore's TG-43 dose (evaluation) against ``reference`` (cGy).

    Parameters
    ----------
    reference : Volume
        The reference dose grid in cGy (``gtcore.io.rt.load_rtdose``),
        axis-aligned (RTDOSE axes may be descending).
    seed_centers, seed_axes : (N, 3)
        Seed centres and long axes, RAS mm (the evaluation's sources).
    sk_u : float or (N,)
        Air-kerma strength per seed [U]; nominal 3.5 U.
    dose_pct, dist_mm : float
        Gamma criteria (3 %/2 mm primary, 2 %/1 mm alongside).
    lower_cutoff_cgy, upper_cutoff_cgy : float
        Reference window of the primary pass rate (30-200 Gy); gamma is
        computed for every voxel ``>= lower`` and ``pass_rate_ge30`` reports
        that set without the upper cut.
    local : bool
        Local gamma (``True``) or global-to-rx (``False``, dose difference
        in percent of ``global_norm_cgy``).  The cutoff is expressed against
        ``global_norm_cgy`` in both cases.
    eval_spacing_mm : float
        Lattice step of the evaluation dose (0.5 mm).
    interp_algo : str
        ``"auto"`` (numba when available, else scipy), ``"pymedphys"`` or ``"scipy"``.
    evaluation : Evaluation, optional
        A prepared :func:`prepare_evaluation` for the same reference and
        seeds (reused across criteria); its lattice must cover the search
        distance ``max_gamma * dist_mm``.
    max_gamma : float
        Search bound and cap (2.0); pass rates are unaffected.
    box_margin_mm : float
        Margin around the ``>= lower`` bounding box kept in the outputs.
    """
    t0 = time.perf_counter()
    search_margin = float(max_gamma) * float(dist_mm) + float(eval_spacing_mm)
    if evaluation is None:
        if seed_centers is None or seed_axes is None:
            raise ValueError("seed_centers and seed_axes are required without a prepared evaluation")
        evaluation = prepare_evaluation(reference, seed_centers, seed_axes, sk_u,
                                        lower_cutoff_cgy=lower_cutoff_cgy,
                                        box_margin_mm=box_margin_mm,
                                        eval_spacing_mm=eval_spacing_mm,
                                        search_margin_mm=search_margin, engine=engine,
                                        exact=exact, elapsed_hours=elapsed_hours)
    else:
        ev_lo, ev_hi = evaluation.lattice.bounds_ras()
        bx_lo, bx_hi = evaluation.reference_box.bounds_ras()
        need = float(max_gamma) * float(dist_mm)
        if np.any(bx_lo - ev_lo < need - 1e-6) or np.any(ev_hi - bx_hi < need - 1e-6):
            raise ValueError("prepared evaluation lattice does not cover the search distance "
                             "%.2f mm around the reference box" % need)
        if float(evaluation.settings.get("lower_cutoff_cgy", lower_cutoff_cgy)) != float(lower_cutoff_cgy):
            raise ValueError("prepared evaluation was built for a different lower cutoff")

    box = evaluation.reference_box
    ref = np.asarray(box.array, dtype=float)
    axes_ref = grid_axes(box)
    axes_eval = grid_axes(evaluation.lattice)
    gamma, gsettings = run_pymedphys_gamma(
        axes_ref, ref, axes_eval, evaluation.lattice.array, dose_pct, dist_mm,
        lower_cutoff_cgy, global_norm_cgy, local=local, max_gamma=max_gamma,
        interp_algo=interp_algo, interp_fraction=interp_fraction, ram_available=ram_available)

    mask_computed = ref >= float(lower_cutoff_cgy)
    mask_used = mask_computed & (ref <= float(upper_cutoff_cgy))
    edges, counts_used = gamma_histogram(gamma, mask_used)
    _, counts_all = gamma_histogram(gamma, mask_computed)
    settings = dict(evaluation.settings)
    settings.update(gsettings)
    settings.update({
        "upper_cutoff_cgy": float(upper_cutoff_cgy),
        "upper_cutoff_rule": "post-hoc selection of reference voxels (exact: each voxel's "
                             "gamma depends only on the evaluation grid)",
        "n_points_window": int(mask_used.sum()),
        "n_points_lower_only": int(mask_computed.sum()),
        "n_points_above_upper": int((mask_computed & ~mask_used).sum()),
        "histogram_bin_width": HISTOGRAM_BIN_WIDTH,
        "histogram_max": HISTOGRAM_MAX,
        "git_commit": _git_commit(),
    })
    runtime = time.perf_counter() - t0 + float(evaluation.runtime_s)
    settings["runtime_total_s"] = float(runtime)
    return GammaResult(
        gamma=gamma, box_affine=box.affine.copy(), box_slices=evaluation.box_slices,
        reference_box=box, evaluation=evaluation.lattice,
        mask_computed=mask_computed, mask_used=mask_used,
        pass_rate=pass_rate(gamma, mask_used), pass_rate_ge30=pass_rate(gamma, mask_computed),
        n_points=int(mask_used.sum()), n_points_ge30=int(mask_computed.sum()),
        histogram={"edges": edges, "counts": counts_used, "counts_ge30": counts_all},
        difference_volume=evaluation.difference_volume, runtime_s=float(runtime),
        settings=settings)
