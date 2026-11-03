"""Small binary-morphology helpers shared by the segmentation modules.

Everything here works on boolean arrays indexed ``[k, j, i]`` (see
``gtcore.volume``) and takes physical spacing in ``(si, sj, sk)`` millimetres,
so callers can express structuring-element sizes in millimetres instead of
voxels.  Head CT is routinely anisotropic (0.4 mm in-plane, 1--3 mm slice), so
a voxel-radius ball would be a very different physical shape depending on the
scan; the helpers below fix that by resampling to an isotropic working grid.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage


def largest_cc(mask):
    """Return the largest 6-connected component of ``mask`` as a bool array.

    An empty (or all-False) mask returns an all-False array of the same shape,
    which keeps downstream boolean algebra total -- no special-casing at the
    call sites.
    """
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return np.zeros(mask.shape, dtype=bool)
    labels, n = ndimage.label(mask)
    if n <= 1:
        return labels > 0
    counts = np.bincount(labels.ravel())
    counts[0] = 0  # background
    return labels == int(counts.argmax())


def match_shape(mask, shape):
    """Crop and/or zero-pad a bool array so it has exactly ``shape``.

    Used to undo the rounding that ``ndimage.zoom`` introduces when a mask is
    taken down to a coarse working grid and back again.
    """
    mask = np.asarray(mask, dtype=bool)
    shape = tuple(int(s) for s in shape)
    if mask.ndim != len(shape):
        raise ValueError("match_shape: rank mismatch %s vs %s" % (mask.shape, shape))

    # crop first
    slicer = tuple(slice(0, min(m, s)) for m, s in zip(mask.shape, shape))
    cropped = mask[slicer]
    if cropped.shape == shape:
        return np.ascontiguousarray(cropped)

    out = np.zeros(shape, dtype=bool)
    dst = tuple(slice(0, c) for c in cropped.shape)
    out[dst] = cropped
    return out


def close_mm(mask, spacing, radius_mm, work_mm=2.0):
    """Morphological closing with a *physical* radius, in millimetres.

    The mask is resampled (nearest neighbour) onto an isotropic ``work_mm``
    grid, closed there with a Euclidean ball of ``radius_mm``, then resampled
    back.  Doing the work coarsely matters: sealing a 14 mm craniotomy on a
    512x512x300 CT with a true voxel ball is minutes of work, and at 2 mm
    isotropic it is a fraction of a second, with an error bounded by the
    working voxel size -- far below the anatomical tolerance we need.

    The closing is implemented with distance transforms rather than
    ``binary_closing``: dilation is ``edt(~m) <= r`` and the following erosion
    is ``edt(dilated) > r - work_mm/2``, which gives an exact Euclidean ball
    instead of an iterated 3x3x3 approximation.

    That half-voxel on the erosion is a deliberate sampling correction, and it
    is the difference between sealing a craniotomy and not.  Both transforms
    measure centre-to-centre distances on the working lattice, so the sampled
    background sits up to half a voxel further out than the continuous
    background it stands for, and the erosion consequently eats half a voxel
    too much.  The bridge a closing lays over a hole in a *thin* shell is only
    a few millimetres thick to begin with, so an uncorrected 14 mm closing at
    a 2 mm working resolution loses the bridge entirely and the "sealed" vault
    still leaks.  Measured on a 30 mm shell with a conical defect, the
    correction moves the largest sealable defect from under 8 degrees of
    half-angle to about 16.

    ``spacing`` is ``(si, sj, sk)`` as returned by ``Volume.spacing``; the
    array axes are ``[k, j, i]``, hence the reversed zoom factors.

    The result is OR-ed with the input, because closing is an extensive
    operator by definition and resampling must not be allowed to erode away
    thin structures that were there to begin with.
    """
    mask = np.asarray(mask, dtype=bool)
    if not mask.any() or radius_mm <= 0:
        return mask.copy()

    spacing = np.asarray(spacing, dtype=float)
    full_shape = mask.shape
    # array is [k, j, i]; spacing is (si, sj, sk)
    factors = np.array([spacing[2], spacing[1], spacing[0]], dtype=float) / float(work_mm)

    if np.allclose(factors, 1.0):
        small = mask
    else:
        small = ndimage.zoom(mask.astype(np.uint8), factors, order=0, grid_mode=False)
        small = small.astype(bool)
    if not small.any():
        return mask.copy()

    pad = int(math.ceil(float(radius_mm) / float(work_mm))) + 2
    padded = np.pad(small, pad, mode="constant", constant_values=False)

    r = float(radius_mm)
    r_erode = max(r - float(work_mm) / 2.0, 0.0)  # sampling correction, see docstring
    dilated = ndimage.distance_transform_edt(~padded, sampling=work_mm) <= r
    closed = ndimage.distance_transform_edt(dilated, sampling=work_mm) > r_erode

    closed = closed[tuple(slice(pad, s - pad) for s in closed.shape)]

    if closed.shape != full_shape:
        inv = 1.0 / factors
        big = ndimage.zoom(closed.astype(np.uint8), inv, order=0, grid_mode=False)
        big = match_shape(big.astype(bool), full_shape)
    else:
        big = closed

    return big | mask


def dilate_mm(mask, spacing, radius_mm):
    """Dilate ``mask`` by an exact Euclidean ball of ``radius_mm``.

    Full resolution, spacing-aware: a voxel is added when its centre lies
    within ``radius_mm`` of a mask voxel centre, measured in physical
    millimetres on every axis (``distance_transform_edt`` with ``sampling``).
    Unlike :func:`close_mm` there is no coarse working grid, so use it on
    boxes, not on a whole clinical CT.  ``spacing`` is ``(si, sj, sk)``; the
    array is ``[k, j, i]``.
    """
    mask = np.asarray(mask, dtype=bool)
    if not mask.any() or radius_mm <= 0 or mask.all():
        return mask.copy()
    spacing = np.asarray(spacing, dtype=float)
    sampling = (spacing[2], spacing[1], spacing[0])
    return ndimage.distance_transform_edt(~mask, sampling=sampling) <= float(radius_mm)


def geodesic_grow_mm(start, mask, spacing, reach_mm):
    """Grow ``start`` through ``mask`` to a geodesic distance of ``reach_mm``.

    Returns the bool region of ``mask | start`` voxels whose geodesic distance
    from ``start`` -- the length in physical millimetres of the shortest path
    that stays inside the mask and moves only between face-adjacent voxels --
    is at most ``reach_mm``.  ``start`` is always included.

    Why not ``binary_dilation(iterations=round(reach / min(spacing)))``: that
    counts voxel steps with a 6-connected element, so on a 0.52 x 0.52 x 1.0
    mm head CT a nominal 14 mm reach runs 27 mm through-slice, 14 mm along an
    in-plane axis and 9.6 mm on an in-plane diagonal.  Here the distance is
    the solution of the Eikonal equation ``|grad T| = 1`` (fast marching,
    Sethian 1996) on the voxel grid with the voxel spacing on every axis, so
    a point in an all-true mask grows to a sphere of radius ``reach_mm``
    (tests/test_cavity_reach_mm.py: 14 mm on the axes, the in-plane diagonal
    and the volume-equivalent radius, within one voxel), and around an
    obstacle the distance is the detour, not the straight line.

    Scheme: Godunov upwind update, second order along each axis where two
    upwind neighbours are known (first order otherwise, and as a fallback
    whenever the second-order root is not causal), solved for all active
    voxels at once and iterated to a fixed point (the fast iterative method
    of Jeong and Whitaker 2008, vectorised in numpy).  The first-order
    stencil from a point source is 20 % long at the first diagonal voxel, so
    the 26-neighbour shell of ``start`` that is face-reachable through the
    mask is seeded with its exact Euclidean distance.  Only face neighbours
    ever exchange values, so the growth has the same 6-connected topology as
    the voxel dilation it replaces: it never crosses a one-voxel wall or a
    diagonal crack.  Voxels beyond ``reach_mm`` stop propagating, which keeps
    the work proportional to the grown region (a clinical 14 mm reach on a
    3 M voxel box runs in about a second).

    ``spacing`` is ``(si, sj, sk)``; arrays are ``[k, j, i]``.
    """
    region, _dist = _geodesic_distance_mm(start, mask, spacing, reach_mm)
    return region


def _geodesic_distance_mm(start, mask, spacing, reach_mm, max_iter=None):
    """Worker for :func:`geodesic_grow_mm`.  Returns ``(region, dist)`` where
    ``dist`` is the geodesic distance in mm (``inf`` beyond ``reach_mm`` or
    where unreachable; 0 on ``start``)."""
    import warnings

    start = np.asarray(start, dtype=bool)
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != start.shape:
        raise ValueError("geodesic_grow_mm: mask %s and start %s differ in shape"
                         % (mask.shape, start.shape))
    if start.ndim != 3:
        raise ValueError("geodesic_grow_mm: expected a 3-D [k, j, i] array")
    spacing = np.asarray(spacing, dtype=float).reshape(3)
    if not np.all(spacing > 0):
        raise ValueError("geodesic_grow_mm: spacing must be positive, got %s" % (spacing,))
    reach_mm = float(reach_mm)
    dist = np.full(start.shape, np.inf)
    dist[start] = 0.0
    if not start.any() or reach_mm <= 0:
        return start.copy(), dist

    dom = mask | start
    hs = np.array([spacing[2], spacing[1], spacing[0]], dtype=float)   # per array axis
    nk, nj, ni = start.shape
    # two-voxel halo: the second-order stencil looks two voxels upwind and
    # the halo (inf, outside the domain) saves every bounds check
    P = (nk + 4, nj + 4, ni + 4)
    inner = (slice(2, -2), slice(2, -2), slice(2, -2))
    T = np.full(P, np.inf)
    D = np.zeros(P, dtype=bool)
    D[inner] = dom
    Tv = T[inner]
    Tv[start] = 0.0

    # exact Euclidean seed of the 26-neighbour shell, restricted to voxels a
    # face-connected path of at most three steps through the domain can
    # reach (edge neighbours need two, corner neighbours three), so the seed
    # never crosses a wall the growth itself could not cross
    cross = ndimage.generate_binary_structure(3, 1)
    reach3 = start
    for _ in range(3):
        reach3 = ndimage.binary_dilation(reach3, structure=cross) & dom
    ring = (ndimage.binary_dilation(start, structure=np.ones((3, 3, 3), bool))
            & reach3 & ~start)
    if ring.any():
        edt = ndimage.distance_transform_edt(~start, sampling=hs)
        Tv[ring] = edt[ring]

    Tf = T.ravel()
    Df = D.ravel()
    sk, sj, si = P[1] * P[2], P[2], 1
    strides = (sk, sj, si)
    offs = np.array([sk, -sk, sj, -sj, si, -si])

    known = np.flatnonzero(np.isfinite(Tf) & (Tf <= reach_mm))
    active = np.unique((known[:, None] + offs[None, :]).ravel())
    active = active[Df[active] & (Tf[active] > 0.0)]
    if max_iter is None:
        max_iter = 20 * int(np.ceil(reach_mm / hs.min())) + 100
    n_iter = 0
    while active.size:
        n_iter += 1
        if n_iter > max_iter:
            warnings.warn("geodesic_grow_mm: no fixed point after %d sweeps; "
                          "returning the region grown so far" % max_iter)
            break
        n = active.size
        A1 = np.empty((n, 3))
        A = np.empty((n, 3))
        H = np.empty((n, 3))
        for ax, s in enumerate(strides):
            tm, tp = Tf[active - s], Tf[active + s]
            fwd = tp < tm
            t1 = np.where(fwd, tp, tm)
            A1[:, ax] = t1
            A[:, ax] = t1
            H[:, ax] = hs[ax]
            t2 = np.where(fwd, Tf[active + 2 * s], Tf[active - 2 * s])
            ok2 = np.isfinite(t1) & np.isfinite(t2) & (t2 <= t1)
            if ok2.any():
                A[ok2, ax] = (4.0 * t1[ok2] - t2[ok2]) / 3.0
                H[ok2, ax] = 2.0 * hs[ax] / 3.0
        Tn = _godunov_solve(A, H)
        bad = Tn < A1.min(axis=1)          # non-causal second-order root
        if bad.any():
            Tn[bad] = _godunov_solve(A1[bad], np.broadcast_to(hs, (int(bad.sum()), 3)))
        imp = Tn < Tf[active] * (1.0 - 1e-12)
        changed = active[imp]
        Tf[changed] = Tn[imp]
        changed = changed[Tf[changed] <= reach_mm]
        if not changed.size:
            break
        nxt = np.unique((changed[:, None] + offs[None, :]).ravel())
        active = nxt[Df[nxt] & (Tf[nxt] > 0.0)]

    dist = T[inner].copy()
    dist[dist > reach_mm] = np.inf
    region = dist <= reach_mm
    return region, dist


def _godunov_solve(a, h):
    """Vectorised Godunov update: the root ``T`` of
    ``sum_i ((T - a_i) / h_i)^2 = 1`` over the axes with ``a_i < T``.

    ``a`` (N, 3) holds the upwind arrival values per axis (``inf`` when
    unknown) and ``h`` (N, 3) the matching step lengths.  The axes are taken
    in increasing ``a``; a term joins only when the solution without it
    exceeds its ``a`` (the standard causality test), which also guarantees a
    real root.
    """
    a = np.asarray(a, dtype=float)
    h = np.asarray(h, dtype=float)
    order = np.argsort(a, axis=1)
    a = np.take_along_axis(a, order, axis=1)
    h = np.take_along_axis(h, order, axis=1)
    a1, a2, a3 = a[:, 0], a[:, 1], a[:, 2]
    h1, h2, h3 = h[:, 0], h[:, 1], h[:, 2]
    T = a1 + h1
    use2 = T > a2
    if use2.any():
        w1 = 1.0 / h1[use2] ** 2
        w2 = 1.0 / h2[use2] ** 2
        b1, b2 = a1[use2], a2[use2]
        W = w1 + w2
        B = w1 * b1 + w2 * b2
        disc = W - w1 * w2 * (b1 - b2) ** 2
        ok = disc >= 0.0
        T2 = np.where(ok, (B + np.sqrt(np.maximum(disc, 0.0))) / W, T[use2])
        T[use2] = T2
        use3 = np.zeros_like(use2)
        use3[use2] = ok & (T2 > a3[use2])
        if use3.any():
            w1 = 1.0 / h1[use3] ** 2
            w2 = 1.0 / h2[use3] ** 2
            w3 = 1.0 / h3[use3] ** 2
            b1, b2, b3 = a1[use3], a2[use3], a3[use3]
            W = w1 + w2 + w3
            B = w1 * b1 + w2 * b2 + w3 * b3
            C = w1 * b1 ** 2 + w2 * b2 ** 2 + w3 * b3 ** 2 - 1.0
            disc = B ** 2 - W * C
            ok = disc >= 0.0
            T[use3] = np.where(ok, (B + np.sqrt(np.maximum(disc, 0.0))) / W, T[use3])
    return T
