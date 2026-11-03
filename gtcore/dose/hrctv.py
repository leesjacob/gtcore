"""HR-CTV: the 5 mm rind of tissue outside the cavity wall, as a volume.

GammaTile's clinical target is the resection-cavity wall plus a margin of
brain -- the STaRT-trial convention prescribes 60 Gy to the surface 5 mm
beyond the wall, and the high-risk clinical target volume (HR-CTV) is the
tissue between the wall and that surface.  The planner's dose panel scores
offset *surfaces* (``gtcore.dose.dvh``); this module builds the HR-CTV as a
**voxel volume** so that D90 / V100 and friends are true volumetric
statistics, every voxel counting equally.

How the rind is built
---------------------
The HR-CTV is ``RIND5`` (:func:`gtcore.dose.targets.rind5`): the Euclidean
``depth_mm`` rind of the **cavity mask**, computed with a distance transform
in physical mm on an isotropic grid (``spacing_mm``, default 1 mm) covering
the wall mesh's bounds plus the rind depth, minus the cavity, intersected
with ``keep_mask`` (the cranial interior / brain, or the printed phantom's
body).  The planner, the optimizer report and the clinical validation all
score this same definition, and the resulting :class:`HRCTV` carries the
``Target`` with its provenance (``cavity_source="gtcore:cavity"``, clip
source, grid, depth, commit).

The wall ``mesh`` (``planner.wall_mesh_for``: the cavity surface, or the
phantom shell when no cavity was segmented) sets the grid and produces the
display ``mesh`` -- the outer boundary of the rind.  When **no cavity mask**
is available (``inside_mask=None``: phantoms, degraded exports) the rind
falls back to the mesh-offset construction: the surface is rasterised onto
the grid, a distance transform gives every voxel its distance to the wall,
and the vertex normal of the nearest surface sample decides which side of
the wall the voxel is on (``side=+1``: along the normals, out of the cavity;
``side=-1``: against them, for a closed phantom shell with outward normals).
That fallback is flagged: ``provenance["cavity_source"] == "mesh"`` and
``HRCTV.source`` says "mesh-offset fallback".

Dose statistics are evaluated at the voxel centres with the **exact** TG-43
engine (:func:`gtcore.dose.engine.dose_at_points`), not by trilinear sampling
of the 2 mm planner grid, whose interpolation error reaches tens of percent
at the wall where the seeds sit.  A few tens of thousands of points cost well
under a second for a 32-seed implant.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np
from scipy import ndimage

from .._strict import swallow_or_raise
from ..volume import Volume
from .engine import TG43Engine, dose_at_points
from .metrics import DVH, resample_mask_to
from .targets import Target, _git_commit, _grid_provenance, rind5

_log = logging.getLogger(__name__)

__all__ = ["HRCTV_DEPTH_MM", "HRCTV_SPACING_MM", "HRCTV", "build_hrctv",
           "hrctv_stats"]

HRCTV_DEPTH_MM = 5.0      # clinical margin: 60 Gy to the surface 5 mm out
HRCTV_SPACING_MM = 1.0    # rind voxel size (isotropic)
_MARGIN_MM = 2.0          # grid padding beyond mesh bounds + depth
_FALLBACK_NOTE = "mesh-offset fallback: no cavity mask"


@dataclass
class HRCTV:
    """The HR-CTV rind as a voxel mask on its own isotropic grid.

    ``target`` is the :class:`~gtcore.dose.targets.Target` (``RIND5``) the
    mask came from, with its provenance; ``mask`` / ``affine`` are the same
    arrays, kept here for the planner's existing call sites.
    """

    mask: np.ndarray          # bool [k, j, i]
    affine: np.ndarray        # voxel (i, j, k, 1) -> RAS mm
    depth_mm: float
    spacing_mm: float
    source: str = "cavity wall"
    mesh: Optional[object] = None   # outer boundary (wall + rind), trimesh
    target: Optional[Target] = None
    _centers: Optional[np.ndarray] = field(default=None, repr=False)

    def __post_init__(self):
        if self.target is None:
            self.target = Target(
                name="RIND5", mask=self.mask, affine=self.affine,
                provenance={"builder": "HRCTV", "cavity_source": None,
                            "clip_source": None,
                            "grid": _grid_provenance(self.affine, np.shape(self.mask)),
                            "depth_mm": float(self.depth_mm),
                            "git_commit": _git_commit()})

    @property
    def provenance(self) -> Dict:
        return dict(self.target.provenance)

    @property
    def n_voxels(self) -> int:
        return int(self.mask.sum())

    @property
    def voxel_volume_mm3(self) -> float:
        return float(self.spacing_mm) ** 3

    @property
    def volume_cc(self) -> float:
        return self.n_voxels * self.voxel_volume_mm3 / 1000.0

    @property
    def volume(self) -> Volume:
        """The mask as a ``Volume`` (uint8) for resampling / export."""
        return Volume(self.mask.astype(np.uint8), self.affine,
                      {"kind": "hrctv_mask", "depth_mm": self.depth_mm})

    def voxel_centers_ras(self) -> np.ndarray:
        """(N, 3) RAS mm centres of the rind voxels (cached)."""
        if self._centers is None:
            kk, jj, ii = np.nonzero(self.mask)
            ijk = np.stack([ii, jj, kk], axis=1).astype(float)
            self._centers = self.volume.index_to_ras(ijk) if len(ijk) \
                else np.zeros((0, 3))
        return self._centers

    def doses_exact(self, seed_centers, seed_axes,
                    sk_per_seed_u=TG43Engine.DEFAULT_SK_U, engine=None,
                    elapsed_hours=None, interference=None) -> np.ndarray:
        """Exact TG-43 dose [cGy] at every rind voxel centre, (N,)."""
        pts = self.voxel_centers_ras()
        if pts.shape[0] == 0:
            return np.zeros(0)
        return np.asarray(dose_at_points(
            seed_centers, seed_axes, pts, sk_per_seed_u, engine=engine,
            elapsed_hours=elapsed_hours, interference=interference),
            dtype=float)

    def doses_from_grid(self, dose_volume: Volume) -> np.ndarray:
        """Trilinear sample of a dose grid at the voxel centres (cheaper,
        less accurate near the seeds; outside the grid reads 0 cGy)."""
        pts = self.voxel_centers_ras()
        if pts.shape[0] == 0:
            return np.zeros(0)
        return np.asarray(dose_volume.sample_ras(pts, order=1, fill=0.0),
                          dtype=float)

    def dvh(self, doses) -> DVH:
        return DVH(np.sort(np.asarray(doses, dtype=float).reshape(-1)),
                   self.voxel_volume_mm3)

    def describe(self) -> str:
        return "HR-CTV: %g mm rind of the %s, %.1f cc (%d voxels @ %g mm)" % (
            self.depth_mm, self.source, self.volume_cc, self.n_voxels,
            self.spacing_mm)


def hrctv_stats(doses, rx_cgy: float, voxel_volume_mm3: float = 1.0) -> Dict[str, float]:
    """Volumetric D90/D50/D100(min)/Dmean/Dmax [cGy], V100/V150/V200
    [fraction], ``volume_cc`` and ``n`` for a set of equal-volume voxel
    doses.  Empty input yields NaN doses and zero volume."""
    d = np.sort(np.asarray(doses, dtype=float).reshape(-1))
    rx = float(rx_cgy)
    if d.size == 0:
        nan = float("nan")
        return {"D90": nan, "D50": nan, "Dmin": nan, "D100": nan,
                "Dmean": nan, "Dmax": nan, "V100": nan, "V150": nan,
                "V200": nan, "volume_cc": 0.0, "n": 0.0, "rx_cgy": rx}
    h = DVH(d, float(voxel_volume_mm3))
    return {
        "D90": h.D(90.0),
        "D50": h.D(50.0),
        "Dmin": float(d[0]),
        "D100": float(d[0]),
        "Dmean": float(d.mean()),
        "Dmax": float(d[-1]),
        "V100": h.V(rx),
        "V150": h.V(1.5 * rx),
        "V200": h.V(2.0 * rx),
        "volume_cc": h.volume_cc,
        "n": float(d.size),
        "rx_cgy": rx,
    }


# ------------------------------------------------------------------ builder
def _surface_samples(mesh, spacing_mm):
    """Points + outward normals densely covering the mesh surface, at least
    one sample per ``spacing_mm`` cell: vertices, face centroids, and random
    face samples proportional to area."""
    verts = np.asarray(mesh.vertices, dtype=float)
    vnorm = np.asarray(mesh.vertex_normals, dtype=float)
    faces = np.asarray(mesh.faces)
    fnorm = np.asarray(mesh.face_normals, dtype=float)
    pts = [verts]
    nrm = [vnorm]
    if faces.shape[0]:
        pts.append(verts[faces].mean(axis=1))
        nrm.append(fnorm)
        # ~2 samples per spacing^2 of area so no cell on the wall is skipped
        n_extra = int(np.ceil(2.0 * float(mesh.area) / (spacing_mm ** 2)))
        n_extra = max(0, n_extra - verts.shape[0] - faces.shape[0])
        if n_extra > 0:
            s, fid = mesh.sample(n_extra, return_index=True)
            pts.append(np.asarray(s, dtype=float))
            nrm.append(fnorm[np.asarray(fid)])
    return np.vstack(pts), np.vstack(nrm)


def _rind_grid(mesh, depth_mm: float, spacing_mm: float):
    """Isotropic grid covering the mesh bounds plus the rind depth and a
    margin: ``(affine, (nz, ny, nx))``."""
    verts = np.asarray(mesh.vertices, dtype=float)
    lo = verts.min(axis=0) - (depth_mm + _MARGIN_MM)
    hi = verts.max(axis=0) + (depth_mm + _MARGIN_MM)
    n_xyz = np.ceil((hi - lo) / spacing_mm).astype(int) + 1
    nx, ny, nz = (int(v) for v in n_xyz)
    affine = np.eye(4)
    affine[0, 0] = affine[1, 1] = affine[2, 2] = spacing_mm
    affine[:3, 3] = lo
    return affine, (nz, ny, nx)


def _mesh_offset_rind(mesh, affine, shape, depth_mm: float, spacing_mm: float,
                      side: int):
    """Fallback rind from the wall mesh alone: ``(rind, dist)`` with ``dist``
    the (unsigned) distance of every voxel centre to the wall [mm]."""
    sp = float(spacing_mm)
    nz, ny, nx = shape
    grid = Volume(np.zeros((nz, ny, nx), dtype=np.uint8), affine)

    # --- rasterise the wall: surface voxels + mean normal per voxel
    pts, nrm = _surface_samples(mesh, sp)
    ijk = np.rint(grid.ras_to_index(pts)).astype(int)
    ok = np.all((ijk >= 0) & (ijk < np.array([nx, ny, nz])), axis=1)
    ijk, nrm = ijk[ok], nrm[ok]
    flat = np.ravel_multi_index((ijk[:, 2], ijk[:, 1], ijk[:, 0]), (nz, ny, nx))
    surf = np.zeros((nz, ny, nx), dtype=bool)
    surf.ravel()[flat] = True
    n_vox = nz * ny * nx
    # per wall voxel: mean sample position (where the wall really crosses
    # the voxel, sub-voxel) and mean normal
    pts = pts[ok]
    count = np.bincount(flat, minlength=n_vox).astype(float)
    count[count == 0.0] = 1.0
    wall_pt = np.zeros((n_vox, 3), dtype=float)
    np.add.at(wall_pt, flat, pts)
    wall_pt /= count[:, None]
    normal_sum = np.zeros((n_vox, 3), dtype=float)
    np.add.at(normal_sum, flat, nrm)
    norms = np.linalg.norm(normal_sum, axis=1)
    norms[norms < 1e-12] = 1.0
    normal_sum /= norms[:, None]

    # --- nearest wall voxel for every voxel (EDT on the rasterised wall),
    # then the distance to the wall as the distance to that voxel's mean
    # sample point, signed by its normal: sub-voxel, and the wall's own
    # voxel layer is split correctly between the two sides
    _edt, (nk, nj, ni) = ndimage.distance_transform_edt(
        ~surf, sampling=(sp, sp, sp), return_indices=True)
    near_flat = np.ravel_multi_index((nk, nj, ni), (nz, ny, nx)).ravel()
    centers = grid.index_to_ras(np.stack(
        [c.ravel() for c in np.indices((nz, ny, nx))[::-1]], axis=1).astype(float))
    off = centers - wall_pt[near_flat]
    dist = np.linalg.norm(off, axis=1).reshape(nz, ny, nx)
    dot = np.einsum("ij,ij->i", off, normal_sum[near_flat]) * float(side)
    tissue_side = (dot > 0.0).reshape(nz, ny, nx)
    return (dist <= float(depth_mm)) & tissue_side, dist


def _display_shell(rind, dist, affine, depth_mm: float, source: str):
    """Outer boundary of the rind for display: the rind's marching-cubes
    surface with the faces nearer than half the depth to the wall removed,
    so the planner shows where the HR-CTV ends, not a second copy of the
    cavity.  ``dist`` is the distance-to-wall field on the rind grid.
    Failure leaves the rind VOLUME complete; logged, re-raised under
    ``GTCORE_STRICT=1``."""
    try:
        from ..segment.surface import mask_to_mesh
        shell = mask_to_mesh(rind, affine, smooth_iterations=5,
                             largest_only=False)
        if len(shell.faces):
            dist_vol = Volume(np.asarray(dist, dtype=np.float32), affine)
            cent = np.asarray(shell.vertices)[np.asarray(shell.faces)].mean(axis=1)
            far = np.asarray(dist_vol.sample_ras(cent, order=1, fill=0.0)) \
                > 0.5 * float(depth_mm)
            shell.update_faces(far)
            shell.remove_unreferenced_vertices()
        return shell
    except Exception as exc:
        swallow_or_raise(_log, "HR-CTV display-shell extraction failed "
                         "(source %r, depth %g mm)" % (source, depth_mm), exc)
        return None


def build_hrctv(mesh, depth_mm: float = HRCTV_DEPTH_MM,
                spacing_mm: float = HRCTV_SPACING_MM, side: int = +1,
                inside_mask=None, inside_affine=None,
                keep_mask=None, keep_affine=None,
                source: str = "cavity wall", with_mesh: bool = True,
                cavity_source: str = "gtcore:cavity",
                keep_source: Optional[str] = None) -> HRCTV:
    """Build the HR-CTV rind for a wall ``mesh`` (trimesh, RAS mm).

    With ``inside_mask`` (the cavity) the rind is ``RIND5``:
    :func:`gtcore.dose.targets.rind5` of that mask, clipped by ``keep_mask``,
    on the isotropic rind grid the mesh bounds define (``side`` is then
    unused: the tissue side of a cavity mask is its outside).
    Without a cavity mask the mesh-offset fallback is used and flagged
    (``provenance["cavity_source"] == "mesh"``, ``HRCTV.source`` says so).

    Parameters
    ----------
    mesh : trimesh.Trimesh
        The wall; sets the rind grid, produces the display mesh, and in the
        fallback its vertex normals define the tissue side.
    depth_mm, spacing_mm : float
        Rind thickness and the isotropic voxel size of the rind grid.
    side : +1 or -1
        Fallback only.  +1: tissue lies along the normals (cavity wall);
        -1: against them (closed phantom shell with outward normals).
    inside_mask, inside_affine
        The cavity mask in its own grid (``RIND5`` path).
    keep_mask, keep_affine
        Optional tissue region to intersect with (cranial interior / body).
    source : str
        Label for reports.
    with_mesh : bool
        Also extract the outer boundary surface (wall + rind) for display.
    cavity_source, keep_source : str
        Provenance labels for the cavity (default ``"gtcore:cavity"``) and
        the clip mask (``"gtcore:cranial_interior"``, ``"gtcore:brain"``,
        ``"gtcore:body"``; ``None`` records ``"unlabelled"`` when a keep mask
        is given).
    """
    if depth_mm <= 0.0:
        raise ValueError("depth_mm must be positive")
    if spacing_mm <= 0.0:
        raise ValueError("spacing_mm must be positive")
    if side not in (+1, -1):
        raise ValueError("side must be +1 or -1")
    if mesh is None or not len(getattr(mesh, "vertices", ())):
        raise ValueError("an HR-CTV needs a wall mesh with vertices")
    if inside_mask is not None and inside_affine is None:
        raise ValueError("inside_mask needs its inside_affine")
    if keep_mask is not None and keep_affine is None:
        raise ValueError("keep_mask needs its keep_affine")

    sp = float(spacing_mm)
    depth = float(depth_mm)
    affine, shape = _rind_grid(mesh, depth, sp)
    grid = Volume(np.zeros(shape, dtype=np.uint8), affine)

    dist = None
    if inside_mask is not None:
        # --- RIND5 of the cavity mask on the rind grid (the one definition)
        inside = resample_mask_to(grid, inside_mask, inside_affine)
        if not inside.any():
            raise ValueError("inside_mask is empty on the rind grid; pass "
                             "inside_mask=None for the mesh-offset fallback")
        target = rind5(inside_mask, inside_affine,
                       clip_mask=keep_mask, clip_affine=keep_affine,
                       depth_mm=depth, grid_affine=affine, grid_shape=shape,
                       cavity_source=cavity_source, clip_source=keep_source)
        rind = target.mask
        label = source
        if with_mesh and rind.any():
            # distance to the wall for the display-shell trim
            dist = ndimage.distance_transform_edt(~inside, sampling=(sp, sp, sp))
    else:
        # --- fallback: no cavity mask, rind from the mesh offset (flagged)
        rind, dist = _mesh_offset_rind(mesh, affine, shape, depth, sp, side)
        clip_label = None
        if keep_mask is not None:
            rind &= resample_mask_to(grid, keep_mask, keep_affine)
            clip_label = keep_source if keep_source is not None else "unlabelled"
        target = Target(name="RIND5", mask=rind, affine=affine, provenance={
            "builder": "build_hrctv",
            "cavity_source": "mesh",
            "clip_source": clip_label,
            "grid": _grid_provenance(affine, shape),
            "depth_mm": depth,
            "side": int(side),
            "git_commit": _git_commit(),
        })
        label = "%s (%s)" % (source, _FALLBACK_NOTE)

    out_mesh = None
    if with_mesh and rind.any():
        out_mesh = _display_shell(rind, dist, affine, depth, source)

    return HRCTV(mask=rind, affine=affine, depth_mm=depth, spacing_mm=sp,
                 source=label, mesh=out_mesh, target=target)
