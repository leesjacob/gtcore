"""Monte-Carlo propagation of seed-position error to the DVH, D90 and V100.

Paper 2 compares gtcore's dose on the physician's HR-CTV (``HRCTV_PHYS``,
:mod:`gtcore.dose.targets`) with the treatment-planning system.  The seed
positions that feed the engine are measurements, so every D90 / V100 /
DVH curve needs an uncertainty.  This module propagates a *seed-position*
error model through the exact TG-43 engine to the dose statistics of a
:class:`~gtcore.dose.targets.Target`:

1. the nominal dose is evaluated once at the target's sample points
   (:func:`~gtcore.dose.targets.target_doses_exact`);
2. ``n_draws`` perturbation sets are drawn from an :class:`ErrorModel`
   (one 3-vector per seed per draw, RAS mm) with a seeded
   ``numpy.random.default_rng``;
3. for every draw the perturbed seeds are re-evaluated with the same exact
   engine at the same points, and :func:`~gtcore.dose.metrics.dose_stats`
   gives that draw's D90, V100 and V150 while :func:`dvh_curve` gives its
   cumulative DVH on a fixed dose ladder;
4. the result reports the per-draw arrays, their mean and 2.5 / 97.5
   percentiles, and the pointwise DVH envelope (mean, 2.5 %, 97.5 % of the
   volume fraction at every dose level).

Seed *axes* are held fixed: the measured residuals are position residuals
(contour centroid vs detection) and the dose a few millimetres from a 4.5 mm
seed is far more sensitive to where the seed is than to a few degrees of
tilt; an axis model would have to be invented, which is exactly what this
module avoids.  The air-kerma strength is the nominal value for every seed
and is not perturbed (it is a stated input, never fitted).

Error models
------------
``ResidualResample(residuals)`` -- the **primary band**: each seed's
perturbation is one of the measured thin-cut-vs-TPS residual vectors
(gtcore detection minus the TPS ``Seeds`` contour centroid, RAS mm),
resampled with replacement, independently per seed and per draw.  No
distributional assumption; the measured anisotropy and tails are kept.
``AxisGaussian(sd_mm)`` -- the **degraded-export band**: independent
zero-mean Gaussians per RAS axis with the per-axis SDs measured between the
2 mm gapped export and the 1 mm thin-cut of the same acquisition
(:data:`DEGRADED_EXPORT_SD_MM`).
``Covariance(cov_ras)`` -- per-seed multivariate normal from the refinement
covariances (``SeedCandidates.cov_ras``).  The refinement is default-off and
its covariance failed its real-data gate, so this is a *secondary check*
only, never the band.
``NoError()`` -- zero perturbation; reproduces the nominal statistics bit
for bit (the regression anchor).

Both measured bands are **conservative**: the residuals they are built from
are differences between two measurements (gtcore vs the TPS contours, or
two reconstructions of one acquisition), so they include the TPS contouring
error and the reference's own localisation error, not gtcore's error alone.

Implementation
--------------
For one draw, seed ``s`` displaced by ``delta_s`` contributes at point ``p``
what the undisplaced seed contributes at ``p - delta_s``; each seed is
therefore evaluated with :func:`gtcore.dose.engine._sum_seed_rates` (the
code path behind ``dose_at_points`` / ``target_doses_exact``) on the shifted
point cloud, and the contributions are summed in seed order with the same
floating-point sequence as the engine's own loop -- so a zero perturbation
gives the nominal dose exactly.  Draws are independent; ``n_workers > 1``
spreads them over threads (numpy releases the GIL inside the kernel) and
does not change a single bit of the result.  Cost: one exact engine
evaluation per draw (about 0.1 s for 19 k points x 32 seeds on the
reference machine, single thread), so 1000 draws on ``HRCTV_PHYS`` take
about two minutes serially.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from .engine import TG43Engine, _prepare_seeds, _sum_seed_rates
from .metrics import dose_stats
from .targets import Target, target_doses_exact

__all__ = ["DEGRADED_EXPORT_SD_MM", "DEFAULT_LEVELS_CGY", "ErrorModel", "NoError",
           "AxisGaussian", "ResidualResample", "Covariance", "MCResult",
           "propagate", "dvh_curve", "load_residuals"]

#: Per-axis SD [mm] of the seed-position difference between the 2 mm gapped
#: export (``PT-CT2mm``) and the contiguous 1 mm thin-cut (``PT-CT1mm``) of
#: the same acquisition, 25 matched seeds, no refinement on either side
#: (``docs/localization-notes.md``, "Stage 3 with-truth track", row (a):
#: SD i / j / k = 0.506 / 0.398 / 0.631 mm; the export's voxel axes are the
#: CT's in-plane (x, y) and through-slice (z) RAS axes).  ``PT-CT2mm`` is no
#: longer on disk, so these are quoted, not regenerated
#: (``docs/paper2-decisions.md``).
DEGRADED_EXPORT_SD_MM = (0.51, 0.40, 0.63)

#: Default dose ladder for the DVH envelope: 0 .. 150 Gy in 0.5 Gy steps.
DEFAULT_LEVELS_CGY = np.arange(0.0, 15001.0, 50.0)

_PCT = (2.5, 97.5)


# ------------------------------------------------------------------ DVH curve
def dvh_curve(doses, levels_cgy, weights=None) -> np.ndarray:
    """Cumulative DVH: fraction of the structure's weight receiving ``>=``
    each level in ``levels_cgy`` (the :func:`dose_stats` ``V`` rule, so the
    value at ``rx`` equals ``V100``).  ``weights=None`` counts every sample
    once; zero-weight samples are not part of the structure.  Empty input
    returns NaN at every level."""
    d = np.asarray(doses, dtype=float).reshape(-1)
    lv = np.asarray(levels_cgy, dtype=float).reshape(-1)
    if weights is None:
        w = None
    else:
        w = np.asarray(weights, dtype=float).reshape(-1)
        if w.shape != d.shape:
            raise ValueError("weights must have one entry per dose")
        keep = w > 0
        d, w = d[keep], w[keep]
    if d.size == 0:
        return np.full(lv.shape, np.nan)
    order = np.argsort(d, kind="stable")
    ds = d[order]
    cw = np.arange(1.0, d.size + 1.0) if w is None else np.cumsum(w[order])
    total = float(cw[-1])
    if not total > 0.0:
        return np.full(lv.shape, np.nan)
    idx = np.searchsorted(ds, lv, side="left")          # samples with d < level
    below = np.where(idx > 0, cw[np.maximum(idx - 1, 0)], 0.0)
    # (total - below) / total, not 1 - below / total: with unit weights both
    # terms are exact integers, so the value equals dose_stats' V (count / n)
    # bit for bit; with area weights it agrees to rounding.
    return (total - below) / total


# --------------------------------------------------------------- error models
class ErrorModel:
    """Base class: ``draw(rng, n_seeds, n_draws)`` returns perturbation
    vectors ``(n_draws, n_seeds, 3)`` in RAS mm; ``describe()`` is the
    Methods-level one-liner written into every output table."""

    name = "error_model"

    def draw(self, rng: np.random.Generator, n_seeds: int, n_draws: int) -> np.ndarray:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name

    def __repr__(self) -> str:                      # pragma: no cover - cosmetic
        return "%s(%s)" % (type(self).__name__, self.describe())


class NoError(ErrorModel):
    """Zero perturbation for every seed: the nominal dose, exactly."""

    name = "none"

    def draw(self, rng, n_seeds, n_draws):
        return np.zeros((int(n_draws), int(n_seeds), 3))

    def describe(self):
        return "no seed-position error (nominal)"


class AxisGaussian(ErrorModel):
    """Independent zero-mean Gaussian per RAS axis, SD ``sd_mm = (sx, sy, sz)``
    for every seed.  With :data:`DEGRADED_EXPORT_SD_MM` this is the
    "degraded export" band."""

    name = "axis_gaussian"

    def __init__(self, sd_mm=DEGRADED_EXPORT_SD_MM, label: Optional[str] = None):
        sd = np.asarray(sd_mm, dtype=float).reshape(-1)
        if sd.shape != (3,):
            raise ValueError("sd_mm must be three per-axis standard deviations")
        if not np.all(np.isfinite(sd)) or np.any(sd < 0.0):
            raise ValueError("sd_mm must be finite and non-negative")
        self.sd_mm = sd
        self.label = label

    def draw(self, rng, n_seeds, n_draws):
        return rng.normal(0.0, 1.0, size=(int(n_draws), int(n_seeds), 3)) * self.sd_mm

    def describe(self):
        s = ("independent per-axis Gaussian, SD (x, y, z) = (%.2f, %.2f, %.2f) mm"
             % tuple(self.sd_mm))
        return s if not self.label else "%s [%s]" % (s, self.label)


class ResidualResample(ErrorModel):
    """Resample measured residual vectors with replacement, independently per
    seed and per draw.  ``residuals`` is ``(M, 3)`` RAS mm (gtcore detection
    minus TPS contour centroid; see :func:`load_residuals`).  The empirical
    distribution is used as is -- its mean offset, anisotropy and tails are
    all kept (``center=True`` subtracts the mean vector first)."""

    name = "residual_resample"

    def __init__(self, residuals, center: bool = False, label: Optional[str] = None):
        r = np.asarray(residuals, dtype=float)
        if r.ndim == 1 and r.size == 3:
            r = r.reshape(1, 3)
        if r.ndim != 2 or r.shape[1] != 3 or r.shape[0] == 0:
            raise ValueError("residuals must be (M, 3) with M >= 1, got %s" % (r.shape,))
        if not np.all(np.isfinite(r)):
            raise ValueError("residuals must be finite")
        self.center = bool(center)
        self.residuals = r - r.mean(axis=0) if self.center else r.copy()
        self.label = label

    @classmethod
    def from_csv(cls, path, **kw) -> "ResidualResample":
        return cls(load_residuals(path), **kw)

    @property
    def n(self) -> int:
        return int(self.residuals.shape[0])

    def draw(self, rng, n_seeds, n_draws):
        idx = rng.integers(0, self.n, size=(int(n_draws), int(n_seeds)))
        return self.residuals[idx]

    def describe(self):
        mag = np.linalg.norm(self.residuals, axis=1)
        s = ("resampled with replacement from %d measured residual vectors "
             "(|r| median %.2f, p95 %.2f, max %.2f mm; per-axis SD %.2f / %.2f / %.2f mm%s)"
             % (self.n, np.median(mag), np.percentile(mag, 95), mag.max(),
                *self.residuals.std(axis=0, ddof=1 if self.n > 1 else 0),
                "; mean removed" if self.center else ""))
        return s if not self.label else "%s [%s]" % (s, self.label)


class Covariance(ErrorModel):
    """Per-seed multivariate normal with the given ``(N, 3, 3)`` covariance
    matrices [mm^2] (``SeedCandidates.cov_ras``).  ``N`` must equal the
    number of seeds propagated.  Secondary check only: the refinement that
    produces these covariances is default-off and failed its real-data gate
    (``docs/localization-notes.md``), so this is never the published band."""

    name = "covariance"

    def __init__(self, cov_ras, label: Optional[str] = None):
        c = np.asarray(cov_ras, dtype=float)
        if c.ndim == 2 and c.shape == (3, 3):
            c = c[None]
        if c.ndim != 3 or c.shape[1:] != (3, 3):
            raise ValueError("cov_ras must be (N, 3, 3), got %s" % (c.shape,))
        if not np.all(np.isfinite(c)):
            raise ValueError("cov_ras must be finite")
        if not np.allclose(c, np.swapaxes(c, 1, 2), rtol=0.0, atol=1e-9):
            raise ValueError("cov_ras matrices must be symmetric")
        w, v = np.linalg.eigh(c)
        if np.any(w < -1e-9 * max(1.0, float(np.abs(w).max()))):
            raise ValueError("cov_ras matrices must be positive semi-definite")
        # A = V sqrt(max(w, 0)): delta = z @ A^T has covariance A A^T = C
        self.cov_ras = c
        self._A = v * np.sqrt(np.maximum(w, 0.0))[:, None, :]
        self.label = label

    @property
    def n(self) -> int:
        return int(self.cov_ras.shape[0])

    def draw(self, rng, n_seeds, n_draws):
        if int(n_seeds) != self.n:
            raise ValueError("Covariance model has %d seeds, propagate() got %d"
                             % (self.n, int(n_seeds)))
        z = rng.normal(0.0, 1.0, size=(int(n_draws), self.n, 3))
        return np.einsum("dsj,sij->dsi", z, self._A)

    def describe(self):
        rms = np.sqrt(np.trace(self.cov_ras, axis1=1, axis2=2))
        s = ("per-seed multivariate normal from %d refinement covariances "
             "(3-D RMS radius median %.2f, max %.2f mm)" % (self.n, np.median(rms), rms.max()))
        return s if not self.label else "%s [%s]" % (s, self.label)


def load_residuals(path) -> np.ndarray:
    """Read a residuals CSV (``seed_id,dx_mm,dy_mm,dz_mm[,dist_mm]``, ``#``
    comment lines allowed) and return the ``(M, 3)`` vectors in mm."""
    rows: List[List[float]] = []
    header: Optional[List[str]] = None
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = [p.strip() for p in s.split(",")]
            if header is None:
                header = [p.lower() for p in parts]
                continue
            rows.append([float(p) if p != "" else float("nan") for p in parts])
    if header is None or not rows:
        raise ValueError("no residual rows in %s" % (path,))
    try:
        cols = [header.index(k) for k in ("dx_mm", "dy_mm", "dz_mm")]
    except ValueError:
        raise ValueError("residuals CSV needs columns dx_mm, dy_mm, dz_mm; got %s" % header)
    arr = np.asarray(rows, dtype=float)[:, cols]
    if not np.all(np.isfinite(arr)):
        raise ValueError("residuals CSV contains non-finite dx/dy/dz values")
    return arr


# ------------------------------------------------------------------- result
@dataclass
class MCResult:
    """Output of :func:`propagate`.

    Per-draw arrays ``d90`` / ``v100`` / ``v150`` (``n_draws``), their
    summaries via :meth:`summary` (``nominal, mean, sd, p2.5, p97.5, min,
    max``), the DVH envelope on ``levels_cgy`` (``dvh_nominal``,
    ``dvh_mean``, ``dvh_p2_5``, ``dvh_p97_5``: fraction of the structure
    receiving ``>=`` each level, percentiles taken pointwise across draws),
    the nominal :func:`dose_stats`, the model description, the drawn
    perturbation statistics and the runtime.
    """

    model: str
    model_name: str
    target: str
    provenance: Dict[str, Any]
    n_draws: int
    seed: int
    n_seeds: int
    n_points: int
    sk_u: float
    rx_cgy: float
    runtime_s: float
    nominal: Dict[str, float]
    d90: np.ndarray
    v100: np.ndarray
    v150: np.ndarray
    levels_cgy: np.ndarray
    dvh_nominal: np.ndarray
    dvh_mean: np.ndarray
    dvh_p2_5: np.ndarray
    dvh_p97_5: np.ndarray
    perturbation_mm: Dict[str, Any] = field(default_factory=dict)
    engine: str = "exact"
    axes_fixed: bool = True

    _ARRAYS = {"D90": "d90", "V100": "v100", "V150": "v150"}

    def summary(self, key: str) -> Dict[str, float]:
        """``{"nominal", "mean", "sd", "p2.5", "p97.5", "min", "max"}`` for
        ``key`` in ``D90`` / ``V100`` / ``V150``."""
        x = np.asarray(getattr(self, self._ARRAYS[key]), dtype=float)
        lo, hi = np.percentile(x, _PCT)
        return {"nominal": float(self.nominal[key]), "mean": float(x.mean()),
                "sd": float(x.std(ddof=1)) if x.size > 1 else 0.0,
                "p2.5": float(lo), "p97.5": float(hi),
                "min": float(x.min()), "max": float(x.max())}

    # convenience accessors (the task's "mean / 2.5 / 97.5 percentiles")
    @property
    def d90_mean(self) -> float:
        return float(np.mean(self.d90))

    @property
    def d90_p2_5(self) -> float:
        return float(np.percentile(self.d90, 2.5))

    @property
    def d90_p97_5(self) -> float:
        return float(np.percentile(self.d90, 97.5))

    @property
    def v100_mean(self) -> float:
        return float(np.mean(self.v100))

    @property
    def v100_p2_5(self) -> float:
        return float(np.percentile(self.v100, 2.5))

    @property
    def v100_p97_5(self) -> float:
        return float(np.percentile(self.v100, 97.5))

    def stat_rows(self, band: str, **extra) -> List[Dict[str, Any]]:
        """One row per statistic for the ``uncertainty_d90_v100.csv`` table:
        ``band, statistic, nominal, mean, sd, p2.5, p97.5, n_draws, seed``
        plus any ``extra`` columns (e.g. ``seed_set``)."""
        rows = []
        for key in ("D90", "V100", "V150"):
            s = self.summary(key)
            row: Dict[str, Any] = {"band": band}
            row.update(extra)
            row.update({"statistic": key, "nominal": s["nominal"], "mean": s["mean"],
                        "sd": s["sd"], "p2.5": s["p2.5"], "p97.5": s["p97.5"],
                        "n_draws": self.n_draws, "seed": self.seed})
            rows.append(row)
        return rows

    def dvh_rows(self, band: str, **extra) -> List[Dict[str, Any]]:
        """One row per dose level for the ``f5b_dvh_band.csv`` table:
        ``band, level_cgy, nominal, mean, p2.5, p97.5`` plus ``extra``."""
        rows = []
        for i, lv in enumerate(self.levels_cgy):
            row: Dict[str, Any] = {"band": band}
            row.update(extra)
            row.update({"level_cgy": float(lv), "nominal": float(self.dvh_nominal[i]),
                        "mean": float(self.dvh_mean[i]), "p2.5": float(self.dvh_p2_5[i]),
                        "p97.5": float(self.dvh_p97_5[i])})
            rows.append(row)
        return rows

    def describe(self) -> str:
        d, v = self.summary("D90"), self.summary("V100")
        return ("%s: %d seeds, %d points, %d draws (seed %d), %s; D90 %.0f cGy nominal, "
                "mean %.0f, 95%% [%.0f, %.0f]; V100 %.3f nominal, mean %.3f, "
                "95%% [%.3f, %.3f]; %.1f s"
                % (self.target, self.n_seeds, self.n_points, self.n_draws, self.seed,
                   self.model, d["nominal"], d["mean"], d["p2.5"], d["p97.5"],
                   v["nominal"], v["mean"], v["p2.5"], v["p97.5"], self.runtime_s))


# ---------------------------------------------------------------- propagate
def _perturbation_summary(delta: np.ndarray) -> Dict[str, Any]:
    mag = np.linalg.norm(delta, axis=2).reshape(-1)
    flat = delta.reshape(-1, 3)
    return {"median_mm": float(np.median(mag)), "p95_mm": float(np.percentile(mag, 95)),
            "max_mm": float(mag.max()), "rms_mm": float(np.sqrt(np.mean(mag ** 2))),
            "mean_vector_mm": [float(x) for x in flat.mean(axis=0)],
            "sd_mm": [float(x) for x in (flat.std(axis=0, ddof=1) if flat.shape[0] > 1
                                          else np.zeros(3))]}


def propagate(target: Target, seed_centers, seed_axes, model: ErrorModel,
              n_draws: int = 1000, seed: int = 0,
              sk_u=TG43Engine.DEFAULT_SK_U, rx_cgy: float = 6000.0,
              levels_cgy=DEFAULT_LEVELS_CGY, engine: Optional[TG43Engine] = None,
              n_workers: int = 1, exact: bool = True) -> MCResult:
    """Propagate a seed-position :class:`ErrorModel` to the D90 / V100 / V150
    and DVH of ``target``.

    Parameters
    ----------
    target : Target
        Voxel structure (``HRCTV_PHYS``, ``RIND5``, ...) or weighted point
        set (``S5``); doses are evaluated at ``target.voxel_centers_ras()``
        and weighted with ``target.weights`` as :func:`target_stats` does.
    seed_centers, seed_axes : (N, 3)
        Nominal seed centres and long axes, RAS mm.  Axes are held fixed.
    model : ErrorModel
        Where the per-seed perturbation vectors come from.
    n_draws : int
        Monte-Carlo draws (1000 for the paper).
    seed : int
        ``numpy.random.default_rng(seed)``; the same seed reproduces every
        array bit for bit, whatever ``n_workers``.
    sk_u : float or (N,)
        Air-kerma strength per seed [U], nominal, never fitted.
    rx_cgy : float
        Prescription for V100 / V150 [cGy].
    levels_cgy : 1-D array
        Dose ladder of the DVH envelope.
    engine : TG43Engine, optional
        Reuse an engine; ``exact=True`` uses the analytic rate (as
        ``target_doses_exact``), ``False`` the tabulated kernel.
    n_workers : int
        Threads over draws (results identical for any value).
    """
    n_draws = int(n_draws)
    if n_draws < 1:
        raise ValueError("n_draws must be >= 1")
    if not isinstance(model, ErrorModel):
        raise TypeError("model must be an ErrorModel instance")
    centers, axes, sk = _prepare_seeds(seed_centers, seed_axes, sk_u)
    n_seeds = int(centers.shape[0])
    if n_seeds == 0:
        raise ValueError("no seeds to propagate")
    pts = np.asarray(target.voxel_centers_ras(), dtype=float).reshape(-1, 3)
    if pts.shape[0] == 0:
        raise ValueError("target %s has no sample points" % target.name)
    weights = None if target.weights is None else np.asarray(target.weights, dtype=float)
    vvol = target.voxel_volume_mm3
    levels = np.asarray(levels_cgy, dtype=float).reshape(-1)
    if levels.size == 0:
        raise ValueError("levels_cgy must not be empty")
    rx = float(rx_cgy)
    eng = engine if engine is not None else TG43Engine()
    scale = eng.TAU_HOURS * float(eng.delivered_fraction(None))

    t0 = time.perf_counter()
    # Nominal dose through the public exact path.  It is handed the RAW
    # centres / axes / S_K so that its one normalisation inside
    # ``_prepare_seeds`` is the same one the draws see above; a zero
    # perturbation is then bit-identical to this value.
    d0 = target_doses_exact(target, seed_centers, seed_axes, sk_u, engine=eng) if exact else \
        _dose_shifted(pts, centers, axes, sk, np.zeros((n_seeds, 3)), eng, exact, scale)
    nominal = dose_stats(d0, rx, weights=weights, voxel_volume_mm3=vvol)
    dvh0 = dvh_curve(d0, levels, weights)

    rng = np.random.default_rng(int(seed))
    delta = np.asarray(model.draw(rng, n_seeds, n_draws), dtype=float)
    if delta.shape != (n_draws, n_seeds, 3):
        raise ValueError("model.draw returned %s, expected %s"
                         % (delta.shape, (n_draws, n_seeds, 3)))
    if not np.all(np.isfinite(delta)):
        raise ValueError("model.draw returned non-finite perturbations")

    def one(b: int):
        d = _dose_shifted(pts, centers, axes, sk, delta[b], eng, exact, scale)
        st = dose_stats(d, rx, weights=weights, voxel_volume_mm3=vvol)
        return st["D90"], st["V100"], st["V150"], dvh_curve(d, levels, weights)

    d90 = np.empty(n_draws)
    v100 = np.empty(n_draws)
    v150 = np.empty(n_draws)
    curves = np.empty((n_draws, levels.size))
    nw = max(1, int(n_workers))
    if nw == 1:
        results = map(one, range(n_draws))
    else:
        pool = ThreadPoolExecutor(max_workers=nw)
        results = pool.map(one, range(n_draws))
    for b, (a, c, e, cv) in enumerate(results):
        d90[b], v100[b], v150[b], curves[b] = a, c, e, cv
    if nw != 1:
        pool.shutdown(wait=True)
    lo, hi = np.percentile(curves, _PCT, axis=0)
    runtime = time.perf_counter() - t0

    return MCResult(
        model=model.describe(), model_name=model.name, target=target.name,
        provenance=dict(target.provenance), n_draws=n_draws, seed=int(seed),
        n_seeds=n_seeds, n_points=int(pts.shape[0]), sk_u=float(np.mean(sk)),
        rx_cgy=rx, runtime_s=float(runtime), nominal=dict(nominal),
        d90=d90, v100=v100, v150=v150, levels_cgy=levels, dvh_nominal=dvh0,
        dvh_mean=curves.mean(axis=0), dvh_p2_5=lo, dvh_p97_5=hi,
        perturbation_mm=_perturbation_summary(delta),
        engine="exact" if exact else "table", axes_fixed=True)


def _dose_shifted(pts, centers, axes, sk, delta, eng, exact, scale) -> np.ndarray:
    """Dose [cGy] at ``pts`` from seeds displaced by ``delta`` ``(N, 3)``:
    seed ``s`` at ``c_s + delta_s`` seen from ``p`` is seed ``s`` at ``c_s``
    seen from ``p - delta_s``.  Summed in seed order, so ``delta = 0`` is
    bit-identical to ``dose_at_points``."""
    acc = np.zeros(pts.shape[0], dtype=np.float64)
    for s in range(centers.shape[0]):
        if sk[s] == 0.0:
            continue
        acc += _sum_seed_rates(pts - delta[s], centers[s:s + 1], axes[s:s + 1],
                               sk[s:s + 1], eng, exact)
    return acc * scale
