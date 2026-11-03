"""Resection-cavity segmentation, optionally guided by the detected seed cloud.

The seed-cloud prior
--------------------
GammaTiles are collagen tiles carrying Cs-131 seeds that the surgeon lays
against the *wall of the resection cavity* -- that is what the device is for.
So once ``gtcore.seeds.detect`` has found the seeds, their centres are not
merely an output, they are a strong spatial prior on where the cavity is: the
cavity is the low-density pocket that the seed cloud surrounds.

That prior does real work.  A post-op brain contains several dark pockets --
ventricles, sulcal CSF, pneumocephalus far from the surgical bed, a
contralateral cyst -- and intensity alone cannot tell you which one the
surgeon operated in.  With no seeds supplied we fall back to the largest
candidate component, which is the usual heuristic and usually right but not
reliably so.

The seed *sheet* prior (four or more seeds)
-------------------------------------------
Picking the dark component that touches the seed cloud is not enough on a
real post-operative scan.  The cavity contents there are a speckled mixture
of fluid, clot, debris and air, and the low-density class runs on without a
break into peri-cavity oedema and from there into the lateral ventricle, so
"the component the seeds touch" came back as cavity + oedema + both
ventricles (193 cc for a ~25 cc cavity on the first clinical case).  Two
facts about the device fix that:

1. The seeds lie *inside* the cavity, 3 mm in from the tissue face of the
   tile (``gtcore.tiles.model``), so the convex hull of the seed cloud is a
   solid core of cavity, and nothing more than ``sheet_tol_mm`` (3 mm) beyond
   the sheet of seeds -- on the far side of the tiles -- can be cavity.
2. Where the wall is not tiled, the cavity may still extend beyond the hull,
   but only through low-density voxels geodesically connected to it, and not
   far: ``hull_reach_mm``.

So the cavity is grown from the seed hull through low-density voxels, with a
geodesic reach, clipped at the seed sheet, then lightly closed and filled.
Against the clinical structure set of the first case this gives 41 cc (Dice
0.65 to the physician's 35 cc cavity) instead of 193 cc, and 0.85 Dice on the
synthetic phantom (docs/clinical-validation-notes.md); the old component rule
stays as the fallback for fewer than four non-coplanar seeds.

The printed phantom (Paper 2 Phase 4)
-------------------------------------
:func:`phantom_wall_and_cavity` is a separate path for the 3-D-printed
phantom, whose body mask includes the collagen tiles: the wall and the
cavity come from the tile-less twin (``PH0``) rigidly registered into the
implanted scan (``PH8``), the cavity being the seed-sheet rule above applied
to the tile-less twin around the carried implant (the printed mouth is as wide
as the craniotomy, so no closing isolates a lumen).  The clinical rule above is
untouched.
"""
from __future__ import annotations

import time

import numpy as np
from scipy import ndimage

from ._morph import close_mm, dilate_mm, geodesic_grow_mm, largest_cc

AIR_HU = -250.0          # pneumocephalus / trapped air
FLUID_HI_HU = 26.0       # CSF and serosanguinous fluid sit around 0-20 HU
BRAIN_ENVELOPE_MM = 10.0  # closing radius used to build the "inside brain" hull
CAVITY_CLOSE_MM = 5.0     # tidies the final mask across thin septa / clot flecks
# seed-sheet prior (see module docstring)
HULL_REACH_MM = 14.0      # geodesic growth beyond the seed hull through low density
# (sweep in docs/clinical-validation-notes.md: phantom Dice rises with reach,
#  the clinical cavity leaks into oedema beyond ~12 mm; 14 mm balances both.
#  That sweep ran with voxel-step growth; the reach has been a physical-mm
#  geodesic distance since docs/paper2-phase-notes.md "Phase 1", where the
#  phantom and clinical numbers at 14 mm are re-measured, not re-tuned.)
SHEET_TOL_MM = 3.0        # cavity ends this far beyond the seed sheet (tile tissue face)
SHEET_CLOSE_MM = 3.0      # gentler closing: 5 mm bridged speckle into oedema (+40 cc)
_SHEET_MIN_SEEDS = 4


def segment_cavity(vol, interior_mask, brain_mask, seed_centers_ras=None,
                   seed_radius_mm=14.0, hull_reach_mm=HULL_REACH_MM,
                   sheet_tol_mm=SHEET_TOL_MM):
    """Segment the resection cavity inside an already-segmented brain.

    Parameters
    ----------
    vol : gtcore.volume.Volume
        The CT, HU in ``array`` indexed ``[k, j, i]``.
    interior_mask : ndarray of bool
        ``cranial_interior`` from :func:`gtcore.segment.head.segment_head`.
    brain_mask : ndarray of bool
        ``brain`` from the same call.
    seed_centers_ras : array_like, shape (N, 3), optional
        Seed centres in RAS mm from ``detect_seed_candidates``.  See module
        docstring.
    seed_radius_mm : float
        Fallback component rule only: how far from the seed cloud a cavity
        component may be and still be accepted.  14 mm is roughly a tile
        diagonal plus the wall thickness a tile is pressed into.
    hull_reach_mm : float
        Seed-sheet rule: geodesic distance the cavity may grow beyond the
        convex hull of the seeds through low-density voxels (untiled wall).
    sheet_tol_mm : float
        Seed-sheet rule: how far beyond the sheet of seeds (away from the
        implant centre) a voxel may lie and still be cavity -- the 3 mm from
        the seed plane to the tile's tissue face.

    Returns
    -------
    ndarray of bool, shaped like ``vol.array``.
    """
    arr = np.asarray(vol.array, dtype=np.float32)
    spacing = vol.spacing
    interior = np.asarray(interior_mask, dtype=bool)
    brain = np.asarray(brain_mask, dtype=bool)

    # ------------------------------------------------ intensity candidates
    air = (arr < AIR_HU) & interior
    fluid = (arr < FLUID_HI_HU) & interior & ~brain & ~air

    # Restrict to holes *within* the brain envelope.  Closing the brain mask
    # by 10 mm produces a solid hull whose surface follows the cortex; this
    # both excludes the subarachnoid CSF rim between brain and skull (which is
    # fluid-density and touches everything) and re-admits the cavity itself,
    # which was punched out of the brain mask by the intensity threshold.
    envelope = close_mm(brain, spacing, BRAIN_ENVELOPE_MM)
    envelope = ndimage.binary_fill_holes(envelope)

    # ------------------------------------------------- seed-sheet rule
    if seed_centers_ras is not None:
        centers = np.atleast_2d(np.asarray(seed_centers_ras, dtype=float))
        if centers.shape[0] >= _SHEET_MIN_SEEDS:
            # no brain-envelope mask here: a cavity under the craniotomy
            # lies largely outside the closed brain hull, and the sheet clip
            # plus the reach already bound where the growth can go
            out = _cavity_from_seed_sheet(
                vol, arr, air | fluid, interior, centers,
                float(hull_reach_mm), float(sheet_tol_mm))
            if out is not None:
                return out

    cand = ndimage.binary_opening(air | fluid, iterations=1) & envelope

    if not cand.any():
        return np.zeros(arr.shape, dtype=bool)

    # --------------------------------------------------- seed-guided select
    selected = None
    if seed_centers_ras is not None:
        centers = np.atleast_2d(np.asarray(seed_centers_ras, dtype=float))
        if centers.size:
            seed_vol = np.zeros(arr.shape, dtype=bool)
            ijk = np.rint(vol.ras_to_index(centers)).astype(int)
            nk, nj, ni = arr.shape
            # ras_to_index gives (i, j, k); the array is [k, j, i] -- flip.
            kji = ijk[:, ::-1]
            ok = np.all((kji >= 0) & (kji < np.array([nk, nj, ni])), axis=1)
            kji = kji[ok]
            if len(kji):
                seed_vol[kji[:, 0], kji[:, 1], kji[:, 2]] = True
                cloud = ndimage.distance_transform_edt(
                    ~seed_vol, sampling=(spacing[2], spacing[1], spacing[0])
                ) <= float(seed_radius_mm)

                labels, n = ndimage.label(cand)
                if n:
                    hit = np.unique(labels[cloud & cand])
                    hit = hit[hit > 0]
                    if hit.size:
                        selected = np.isin(labels, hit)

    if selected is None:
        selected = largest_cc(cand)

    # --------------------------------------------------------------- tidy
    out = close_mm(selected, spacing, CAVITY_CLOSE_MM) & interior
    out = ndimage.binary_fill_holes(out)
    return out


def _cavity_from_seed_sheet(vol, arr, low, interior, centers, reach_mm,
                            tol_mm):
    """Cavity = low-density voxels grown geodesically from the convex hull
    of the seeds, clipped at the seed sheet, closed and filled.  Returns
    None when the seeds are degenerate (coplanar / collinear) so the caller
    falls back to the component rule.

    ``reach_mm`` and ``tol_mm`` are physical millimetres on every axis of
    the (usually anisotropic) CT grid -- see :func:`geodesic_grow_mm` and
    :func:`dilate_mm` in ``gtcore.segment._morph``."""
    from scipy.spatial import Delaunay, QhullError, cKDTree

    try:
        tri = Delaunay(centers)
    except (QhullError, ValueError):
        return None

    spacing = vol.spacing
    nk, nj, ni = arr.shape
    # work on the bounding box of what can possibly be cavity
    pad = reach_mm + tol_mm + 2.0 * float(np.max(spacing))
    lo = np.floor(vol.ras_to_index(centers.min(axis=0) - pad)).astype(int)
    hi = np.ceil(vol.ras_to_index(centers.max(axis=0) + pad)).astype(int)
    lo, hi = np.minimum(lo, hi), np.maximum(lo, hi)
    lo = np.clip(lo, 0, [ni - 1, nj - 1, nk - 1])
    hi = np.clip(hi, 0, [ni - 1, nj - 1, nk - 1])
    sl = (slice(lo[2], hi[2] + 1), slice(lo[1], hi[1] + 1), slice(lo[0], hi[0] + 1))
    kk, jj, ii = np.indices(tuple(s.stop - s.start for s in sl))
    ijk = np.stack([ii.ravel() + lo[0], jj.ravel() + lo[1], kk.ravel() + lo[2]],
                   axis=1).astype(float)
    ras = vol.index_to_ras(ijk)
    shape_box = tuple(s.stop - s.start for s in sl)

    hull = (tri.find_simplex(ras) >= 0).reshape(shape_box)
    if not hull.any():
        return None

    # seed sheet: for each voxel the nearest seed and the local normal of
    # the sheet there; beyond tol_mm along it is tile / tissue.  The normal
    # is the plane through the seed's nearest neighbours (a tile's four seeds
    # are coplanar; neighbouring tiles bend gently), oriented away from the
    # implant centre.  Using the sheet normal rather than the direction from
    # the centre matters for one-sided implants: at the rim of a flat tile
    # the radial direction runs along the sheet and would clip the cavity.
    outward = _sheet_normals(centers, vol, low)
    _d, idx = cKDTree(centers).query(ras)
    proj = np.einsum("ij,ij->i", ras - centers[idx], outward[idx])
    inside_sheet = (proj <= tol_mm).reshape(shape_box)

    low_box = np.asarray(low, dtype=bool)[sl]
    interior_box = np.asarray(interior, dtype=bool)[sl]
    # the growth starts from the hull padded by the sheet tolerance: seeds
    # sit at the wall (1-3 mm either side of it), so a one-sided implant's
    # hull is a thin slab that need not itself contain cavity voxels.
    # Pad and reach are physical millimetres on every axis: the pad is an
    # exact Euclidean ball, the reach a geodesic (Eikonal) distance through
    # the growth mask (gtcore.segment._morph).  The voxel-step dilations
    # they replace made a 14 mm reach 27 mm through-slice, 14 mm on an
    # in-plane axis and 9.6 mm on an in-plane diagonal on the 0.52 x 0.52 x
    # 1.0 mm clinical grid (tests/test_cavity_reach_mm.py).
    padded = dilate_mm(hull, spacing, tol_mm) & low_box
    start = (hull | padded) & inside_sheet
    grow_mask = (low_box | hull) & inside_sheet
    grown = geodesic_grow_mm(start, grow_mask, spacing, reach_mm)

    tidy = close_mm(grown, spacing, SHEET_CLOSE_MM) & interior_box & inside_sheet
    tidy = ndimage.binary_fill_holes(tidy)

    out = np.zeros(arr.shape, dtype=bool)
    out[sl] = tidy
    return out


_SHEET_K = 6            # neighbours used for the local sheet plane
_SHEET_NEIGHBOUR_MM = 16.0   # a tile diagonal plus a little: same or adjacent tile
_SHEET_PROBE_MM = (3.0, 5.0, 7.0)   # where the cavity / tissue test samples


def _sheet_normals(centers, vol, low):
    """Unit normal of the local seed sheet at every seed, pointing OUT of
    the cavity (towards the tile and the tissue behind it).

    Direction: the plane through the seed and up to ``_SHEET_K`` neighbours
    within ``_SHEET_NEIGHBOUR_MM`` (a tile's four seeds are coplanar and
    neighbouring tiles bend gently); a seed with fewer than two usable
    neighbours takes the radial direction from the implant centre.

    Sign: the side with more low-density voxels at 3-7 mm along the normal
    is the cavity, so the normal points the other way.  The implant centre
    cannot decide this for a one-sided implant (three tiles on one wall put
    the centre in the sheet itself), and the intensity evidence can; the
    radial direction only breaks ties.
    """
    from scipy.spatial import cKDTree

    centers = np.asarray(centers, dtype=float)
    n = centers.shape[0]
    centre = centers.mean(axis=0)
    radial = centers - centre
    rn = np.linalg.norm(radial, axis=1)
    rn[rn < 1e-9] = 1.0
    radial /= rn[:, None]
    tree = cKDTree(centers)
    k = min(_SHEET_K + 1, n)
    dist, idx = tree.query(centers, k=k)
    if n == 1:
        dist, idx = np.atleast_2d(dist), np.atleast_2d(idx)
    low = np.asarray(low, dtype=bool)
    nk, nj, ni = low.shape

    def low_fraction(points):
        ijk = np.rint(vol.ras_to_index(points)).astype(int)
        ok = np.all((ijk >= 0) & (ijk < np.array([ni, nj, nk])), axis=1)
        if not ok.any():
            return 0.0
        ijk = ijk[ok]
        return float(low[ijk[:, 2], ijk[:, 1], ijk[:, 0]].mean())

    out = np.empty_like(centers)
    probes = np.asarray(_SHEET_PROBE_MM, dtype=float)
    for i in range(n):
        near = idx[i][(dist[i] <= _SHEET_NEIGHBOUR_MM)]
        if near.size < 3:
            nrm = radial[i]
        else:
            q = centers[near] - centers[near].mean(axis=0)
            _w, v = np.linalg.eigh(q.T @ q)
            nrm = v[:, 0]
        plus = low_fraction(centers[i] + probes[:, None] * nrm)
        minus = low_fraction(centers[i] - probes[:, None] * nrm)
        if plus > minus:          # cavity lies along +nrm: point the other way
            nrm = -nrm
        elif plus == minus and np.dot(nrm, radial[i]) < 0.0:
            nrm = -nrm
        out[i] = nrm
    return out


# ----------------------------------------------------------- printed phantom
# Paper 2 Phase 4 (docs/paper2-decisions.md 2026-11-03 "seed-plane model ...
# phantom wall reference"): the PH8 body mask includes the hydrated collagen
# tiles, so the phantom wall for conforming / optimising is the tile-less twin
# PH0 rigidly registered into the PH8 frame, and the phantom cavity is the air
# lumen the printed shell encloses, in the PH8 grid.  Nothing here touches the
# clinical rule above.
PHANTOM_WALL_REACH_MM = 1.5
# A plastic-surface face is cavity wall when its centroid lies within this
# distance of a cavity voxel centre: the half-voxel of the mid-surface plus
# the gap the seed-sheet clip leaves where a seed sits beyond 3 mm.
PHANTOM_LID_PROBE_MM = 1.0
# The wall's normals are oriented per face by probing the cavity mask this
# far along the normal (into the cavity = wrong way).
PHANTOM_PLASTIC_HU = 100.0
PHANTOM_AIR_HU = -1000.0
PHANTOM_MIN_WALL_FRACTION = 0.2
# printed_lumen: at least this share of the cavity's surface voxels must touch
# plastic, else the seeds were not inside the printed cavity (the open mouth's
# cap is the only part of a real cavity's surface in air).
# HU the printed body / air masks are rendered at when the cavity rule reads
# a synthetic volume built from the body mask alone (:func:`printed_lumen`).


def printed_body_mask(arr, threshold_hu=None):
    """gtcore's body rule (``segment_head``) on a printed phantom: largest
    6-connected component above the air/body threshold, holes filled."""
    from .head import AIR_BODY_HU
    thr = AIR_BODY_HU if threshold_hu is None else float(threshold_hu)
    body = largest_cc(np.asarray(arr, np.float32) > thr)
    return ndimage.binary_fill_holes(body)


def printed_lumen(body, affine, seed_centers_ras, hull_reach_mm=HULL_REACH_MM,
                  sheet_tol_mm=SHEET_TOL_MM):
    """The printed cavity as gtcore's cavity: ``(lumen, info)``.

    The printed cavity's mouth is as wide as the craniotomy it reproduces
    (the phantom is a partial head section whose cavity opens into the
    printed defect), so no hole fill or closing isolates an
    implant-independent lumen: a closing that bridges the mouth also fills
    the cavity and the section's exterior concavities.  The cavity is
    therefore defined exactly as the clinical one -- :func:`segment_cavity`'s
    seed-sheet rule on a volume rendered from the body mask (plastic
    ``PHANTOM_PLASTIC_HU``, air ``PHANTOM_AIR_HU``): the air grown
    geodesically from the convex hull of the implant's seeds
    (``seed_centers_ras``, RAS, at least four non-coplanar) within
    ``hull_reach_mm``, clipped ``sheet_tol_mm`` beyond the seed sheet,
    closed and filled, with ``interior`` = the air and ``brain`` = the
    plastic.  One definition of "the cavity" for the patient and the
    phantom; the mouth is capped where the reach from the implant ends.

    ``info``: the rule and its parameters, ``volume_ml``, ``n_seeds``,
    ``seeds_inside``.  Raises ``ValueError`` with fewer than four seeds or
    when the rule returns nothing.
    """
    from ..volume import Volume
    body = np.asarray(body, dtype=bool)
    affine = np.asarray(affine, dtype=float)
    if body.ndim != 3 or not body.any():
        raise ValueError("printed_lumen: body mask is empty or not 3-D")
    seeds = np.atleast_2d(np.asarray(seed_centers_ras, dtype=float)) if seed_centers_ras is not None \
        else np.zeros((0, 3))
    if seeds.shape[0] < _SHEET_MIN_SEEDS:
        raise ValueError("printed_lumen: the printed cavity is defined around the implant -- "
                         "give at least %d seed centres (got %d)" % (_SHEET_MIN_SEEDS, seeds.shape[0]))
    arr = np.where(body, np.float32(PHANTOM_PLASTIC_HU), np.float32(PHANTOM_AIR_HU))
    vol = Volume(arr, affine, {})
    air = ~body
    lumen = segment_cavity(vol, air, body, seed_centers_ras=seeds,
                           hull_reach_mm=float(hull_reach_mm), sheet_tol_mm=float(sheet_tol_mm))
    lumen = np.asarray(lumen, dtype=bool) & air
    if not lumen.any():
        raise ValueError("printed_lumen: the seed-sheet cavity rule found no air around the implant "
                         "(seeds outside the printed cavity, or coplanar)")
    # the cavity must be bounded by the printed wall: a good share of its
    # surface voxels touch plastic (the rest is the cap across the mouth)
    surface = lumen & ~ndimage.binary_erosion(lumen)
    contact = ndimage.binary_dilation(lumen) & body
    wall_fraction = float(contact.sum()) / max(1.0, float(surface.sum()))
    if wall_fraction < PHANTOM_MIN_WALL_FRACTION:
        raise ValueError("printed_lumen: the air grown around the implant is not bounded by the "
                         "printed wall (%.0f %% of its surface touches plastic, < %.0f %%): the "
                         "seeds are not inside the printed cavity"
                         % (100.0 * wall_fraction, 100.0 * PHANTOM_MIN_WALL_FRACTION))
    spacing = np.linalg.norm(affine[:3, :3], axis=0)
    vox_ml = float(np.prod(spacing)) / 1000.0
    inv = np.linalg.inv(affine)
    kji = np.rint(seeds @ inv[:3, :3].T + inv[:3, 3])[:, ::-1].astype(int)
    ok = np.all((kji >= 0) & (kji < np.asarray(body.shape)), axis=1)
    inside = int(lumen[kji[ok, 0], kji[ok, 1], kji[ok, 2]].sum())
    info = {"rule": "gtcore segment_cavity seed-sheet rule on the tile-less twin",
            "hull_reach_mm": float(hull_reach_mm), "sheet_tol_mm": float(sheet_tol_mm),
            "sheet_close_mm": float(SHEET_CLOSE_MM), "volume_ml": float(lumen.sum()) * vox_ml,
            "n_seeds": int(seeds.shape[0]), "seeds_inside": inside,
            "wall_contact_fraction": wall_fraction,
            "voxel_ml": vox_ml, "spacing_mm": [float(s) for s in spacing]}
    return lumen, info


def _lumen_wall_mesh(lumen, body, affine, smooth_iterations=10, step_size=1,
                     reach_mm=PHANTOM_WALL_REACH_MM, probe_mm=PHANTOM_LID_PROBE_MM,
                     margin_mm=10.0):
    """The printed wall bounding ``lumen``: the plastic surface
    (:func:`mask_to_mesh` of ``body`` on the lumen's bounding box plus
    ``margin_mm``) restricted to the faces whose centroid lies within
    ``reach_mm`` of a cavity voxel centre, as the largest connected patch,
    with every face wound so its normal points out of the cavity into the
    plastic (the clinical cavity mesh's convention; decided per face by
    probing the cavity mask ``probe_mm`` along the normal, since the cropped
    surface is open and trimesh cannot orient it globally).  Returns
    ``(wall_mesh, info)`` with the face counts and the area.  The cap across
    the mouth (the reach surface of the cavity rule, in air) has no plastic
    behind it and never enters the wall."""
    import trimesh
    from ..volume import Volume
    from .surface import mask_to_mesh
    lumen = np.asarray(lumen, dtype=bool)
    body = np.asarray(body, dtype=bool)
    affine = np.asarray(affine, dtype=float)
    spacing = np.linalg.norm(affine[:3, :3], axis=0)
    sampling = (spacing[2], spacing[1], spacing[0])
    kji = np.argwhere(lumen)
    pad = np.ceil(float(margin_mm) / spacing[::-1]).astype(int)
    lo = np.maximum(kji.min(axis=0) - pad, 0)
    hi = np.minimum(kji.max(axis=0) + pad, np.asarray(lumen.shape) - 1)
    box = tuple(slice(int(a), int(b) + 1) for a, b in zip(lo, hi))
    aff_box = affine.copy()
    aff_box[:3, 3] = affine[:3, :3] @ np.array([lo[2], lo[1], lo[0]], float) + affine[:3, 3]
    mesh = mask_to_mesh(body[box], aff_box, smooth_iterations=smooth_iterations,
                        largest_only=False, step_size=step_size)
    if not len(mesh.faces):
        raise ValueError("phantom wall: no plastic surface around the cavity")
    cents = np.asarray(mesh.triangles_center, dtype=float)
    # distance from each face centroid to the nearest cavity voxel centre
    dist = ndimage.distance_transform_edt(~lumen[box], sampling=sampling).astype(np.float32)
    near = Volume(dist, aff_box).sample_ras(cents, fill=1e6) <= float(reach_mm)
    n_all = int(len(mesh.faces))
    if not near.any():
        raise ValueError("phantom wall: no plastic face borders the cavity")
    keep = np.flatnonzero(near)
    # largest connected patch of the kept faces, by face adjacency; never
    # trimesh.split(), whose repair step fills holes
    sub = mesh.submesh([keep], append=True, repair=False)
    comps = trimesh.graph.connected_components(sub.face_adjacency, min_len=1,
                                               nodes=np.arange(len(sub.faces)))
    if len(comps) > 1:
        largest = max(comps, key=len)
        sub = sub.submesh([np.asarray(largest, dtype=int)], append=True, repair=False)
    # orient every face out of the cavity: a normal whose probe lands in the
    # cavity points the wrong way
    faces = np.asarray(sub.faces, dtype=int).copy()
    normals = np.asarray(sub.face_normals, dtype=float)
    probe = np.asarray(sub.triangles_center, dtype=float) + float(probe_mm) * normals
    wrong = Volume(lumen.astype(np.float32), affine).sample_ras(probe) >= 0.5
    faces[wrong] = faces[wrong][:, ::-1]
    wall = trimesh.Trimesh(vertices=np.asarray(sub.vertices, dtype=float), faces=faces,
                           process=False)
    info = {"n_faces_body_surface": n_all, "n_faces_bordering_cavity": int(near.sum()),
            "n_faces_wall": int(len(wall.faces)),
            "n_faces_dropped_as_fragments": int(near.sum() - len(wall.faces)),
            "n_faces_reoriented": int(wrong.sum()),
            "area_wall_mm2": float(wall.area), "reach_mm": float(reach_mm),
            "probe_mm": float(probe_mm), "margin_mm": float(margin_mm),
            "smooth_iterations": int(smooth_iterations), "step_size": int(step_size)}
    return wall, info


def phantom_wall_and_cavity(vol_ph8, vol_ph0, masks_ph8=None, seed_centers_ras=None,
                            threshold_hu=None, hull_reach_mm=HULL_REACH_MM,
                            sheet_tol_mm=SHEET_TOL_MM, smooth_iterations=10, step_size=1,
                            registration_kw=None):
    """Phantom wall and cavity from the registered tile-less twin.

    ``vol_ph8`` is the scanned implanted phantom (``PH8``, tiles in place),
    ``vol_ph0`` the tile-less print of the same object (``PH0``).  Steps:
    the body (plastic) masks of both (``masks_ph8["body"]`` when given,
    else :func:`printed_body_mask`); the rigid registration PH0 -> PH8
    (:func:`gtcore.preprocess.register.register_rigid`, fixed = PH8); PH0's
    printed cavity as the air lumen its shell encloses
    (:func:`printed_lumen`: gtcore's seed-sheet cavity rule around the
    implant carried into PH0 -- the printed mouth is as wide as the
    craniotomy, so there is no implant-independent lumen);
    the printed wall as the plastic surface bordering that cavity
    (:func:`mask_to_mesh` of the PH0 body, faces within 1.5 mm of a cavity
    voxel, normals into the plastic); everything carried into the PH8
    frame.

    Returns ``(wall_mesh, cavity_mask, cavity_affine, registration_info)``:
    the inner wall in PH8 RAS mm (normals out of the lumen, i.e. into the
    plastic, the convention of the clinical cavity mesh), the lumen as a
    bool mask on the PH8 grid with the PH8 affine (so
    ``targets.rind5(cavity_mask, cavity_affine, clip_mask=shell)`` is the
    phantom HR-CTV), and a dict with the registration record, the lumen
    record (``lumen``: rule, reach, volume in mL, seeds inside),
    the wall record (``wall``: face counts, area), the registration
    residual of the carried wall against the PH8 body surface
    (``residual_mm``) and ``shell_mask``: PH0's plastic carried into the
    PH8 grid, the clip for the rind.  ``seed_centers_ras`` (PH8 frame)
    picks the lumen component holding the implant and sets the residual's
    "far from the tiles" subset, and defines the cavity (required);
    ``hull_reach_mm`` / ``sheet_tol_mm`` are the cavity rule's constants
    (not re-tuned); ``registration_kw`` reaches :func:`register_rigid`.
    """
    from ..preprocess.register import (register_rigid, resample_mask_into_fixed,
                                       transform_mesh_ras, transform_points_ras)
    from ..volume import Volume
    t0 = time.time()
    body8 = None
    if masks_ph8 is not None and masks_ph8.get("body") is not None:
        body8 = np.asarray(masks_ph8["body"], dtype=bool)
    if body8 is None or not body8.any():
        body8 = printed_body_mask(vol_ph8.array, threshold_hu)
    body0 = printed_body_mask(vol_ph0.array, threshold_hu)
    if not body0.any():
        raise ValueError("phantom_wall_and_cavity: the tile-less scan has no body above the threshold")
    tx, reg = register_rigid(vol_ph8, body8, vol_ph0, body0, **(registration_kw or {}))
    seeds0 = None
    if seed_centers_ras is not None and len(np.atleast_2d(seed_centers_ras)):
        seeds0 = transform_points_ras(tx, np.atleast_2d(seed_centers_ras))   # PH8 -> PH0
    if seeds0 is None:
        raise ValueError("phantom_wall_and_cavity: seed_centers_ras (the implant in the "
                         "implanted scan's frame) is required -- the printed cavity is defined "
                         "around the implant (see printed_lumen)")
    lumen0, lumen_info = printed_lumen(body0, vol_ph0.affine, seeds0,
                                       hull_reach_mm=hull_reach_mm, sheet_tol_mm=sheet_tol_mm)
    wall0, wall_info = _lumen_wall_mesh(lumen0, body0, vol_ph0.affine,
                                        smooth_iterations=smooth_iterations, step_size=step_size)
    wall8 = transform_mesh_ras(tx, wall0, inverse=True)                        # PH0 -> PH8
    shape8 = np.asarray(vol_ph8.array).shape
    lumen8 = resample_mask_into_fixed(lumen0, vol_ph0.affine, tx, vol_ph8.affine, shape8)
    shell8 = resample_mask_into_fixed(body0, vol_ph0.affine, tx, vol_ph8.affine, shape8)
    vox8_ml = float(np.prod(vol_ph8.spacing)) / 1000.0
    residual = _wall_residual(wall8, body8, vol_ph8, seed_centers_ras)
    info = {
        "registration": reg, "transform_parameters": [float(v) for v in tx.GetParameters()],
        "transform_center_lps": [float(v) for v in tx.GetCenter()],
        "lumen": lumen_info, "wall": wall_info, "residual_mm": residual,
        "lumen_volume_ml_ph0": lumen_info["volume_ml"],
        "lumen_volume_ml_ph8_grid": float(lumen8.sum()) * vox8_ml,
        "shell_volume_ml_ph8_grid": float(shell8.sum()) * vox8_ml,
        "body_ml": {"ph8": float(body8.sum()) * vox8_ml,
                    "ph0": float(body0.sum()) * float(np.prod(vol_ph0.spacing)) / 1000.0},
        "shell_mask": shell8, "transform": tx, "wall_mesh_ph0": wall0, "lumen_mask_ph0": lumen0,
        "seconds": float(time.time() - t0),
    }
    return wall8, lumen8, np.asarray(vol_ph8.affine, dtype=float).copy(), info


def _wall_residual(wall8, body8, vol_ph8, seed_centers_ras, max_points=20000, far_mm=15.0):
    """|signed distance| of the carried wall's vertices to the PH8 body
    surface (EDT inside - outside on a crop, trilinear), all points and the
    points farther than ``far_mm`` from any seed (no tiles there)."""
    from ..volume import Volume
    verts = np.asarray(wall8.vertices, dtype=float)
    if len(verts) > max_points:
        verts = verts[np.random.default_rng(0).choice(len(verts), max_points, replace=False)]
    body8 = np.asarray(body8, dtype=bool)
    spacing = vol_ph8.spacing
    kji = np.argwhere(body8)
    pad = np.ceil(10.0 / np.asarray(spacing[::-1])).astype(int)
    lo = np.maximum(kji.min(axis=0) - pad, 0)
    hi = np.minimum(kji.max(axis=0) + pad, np.asarray(body8.shape) - 1)
    box = tuple(slice(int(a), int(b) + 1) for a, b in zip(lo, hi))
    sub = body8[box]
    sampling = (spacing[2], spacing[1], spacing[0])
    sdf = (ndimage.distance_transform_edt(sub, sampling=sampling)
           - ndimage.distance_transform_edt(~sub, sampling=sampling)).astype(np.float32)
    aff = np.asarray(vol_ph8.affine, dtype=float).copy()
    aff[:3, 3] = aff[:3, :3] @ np.array([lo[2], lo[1], lo[0]], float) + aff[:3, 3]
    d = np.abs(Volume(sdf, aff).sample_ras(verts))

    def _stats(r):
        if r.size == 0:
            return {"n": 0}
        return {"n": int(r.size), "median": float(np.median(r)), "p90": float(np.percentile(r, 90)),
                "p95": float(np.percentile(r, 95)), "max": float(r.max())}
    out = {"all": _stats(d)}
    if seed_centers_ras is not None and len(np.atleast_2d(seed_centers_ras)):
        from scipy.spatial import cKDTree
        dseed, _ = cKDTree(np.atleast_2d(np.asarray(seed_centers_ras, float))).query(verts)
        out["far_from_seeds"] = _stats(d[dseed > far_mm])
        out["far_mm"] = float(far_mm)
    return out
