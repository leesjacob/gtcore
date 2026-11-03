"""RT DICOM readers: RTSTRUCT contours, RTDOSE grids, RTPLAN brachy sources.

Everything leaves this module in gtcore conventions: RAS millimetres (DICOM
is LPS; ``x`` and ``y`` are negated here, exactly as :mod:`gtcore.io.dicom`
does for images), arrays indexed ``[k, j, i]``, 4x4 affines mapping voxel
index ``(i, j, k, 1)`` to RAS, dose in cGy.

Rasterisation rule (Methods wording)
------------------------------------
A contoured structure is converted to a voxel mask on an arbitrary output
grid as follows.

* *In-plane.*  Every output voxel is divided into ``supersample x
  supersample`` sub-pixels (default 4 x 4 = 16).  The closed planar contours
  of one plane are filled jointly with the even-odd (parity) rule, so a
  contour drawn inside another is a hole and disjoint contours are a union
  (this is the XOR of the individual fills).  A sub-pixel counts as covered
  when its centre lies inside; the voxel is included when the covered
  fraction is ``>= min_fraction`` (default 0.5).  With one sub-pixel per voxel
  this reduces to the classic "voxel centre inside the polygon" rule.
* *Through-plane (end-slice rule).*  The structure's own contour pitch ``p``
  is the median spacing of its contour planes.  A contour plane at ``z_c``
  claims every output voxel whose centre ``z`` satisfies
  ``z_c - p/2 <= z < z_c + p/2`` (half-open, so a centre exactly half a pitch
  from two planes belongs to the plane with the larger grid index, and a slab
  contoured on ``k`` planes is exactly ``k * p`` thick).  Voxels more than half
  a pitch beyond the first or last plane are outside; nothing is interpolated
  across missing planes.  When the output z-spacing is coarser than ``p`` each
  output slice therefore takes the contour plane nearest its centre; when it
  is finer, each plane is copied into every output slice inside its slab.

The rule is the same on the CT grid and on the TPS dose grid, which is what
makes structure volumes comparable across grids (the requirement that a
structure's volume reproduce the TPS's within 2 % before any D90 is quoted).

Dose statistics for :func:`verify_against_tps` come from one private helper
(:func:`_dose_stats`) so that switching to the unified
``gtcore.dose.metrics.dose_stats`` is a one-line change.
"""
from __future__ import annotations

import csv
import io
import os
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np

from ..volume import Volume, apply_affine

__all__ = [
    "Contour",
    "RTStruct",
    "load_rtstruct",
    "rasterize_roi",
    "rasterize_rois",
    "load_rtdose",
    "Source",
    "RTPlan",
    "load_rtplan",
    "reference_from_dvh_sequence",
    "parse_mim_dvh_export",
    "verify_against_tps",
    "check_references",
]

# LPS <-> RAS: negate x and y.  Same matrix as gtcore.io.dicom (self-inverse).
_LPS_TO_RAS = np.diag([-1.0, -1.0, 1.0, 1.0])

_RTSTRUCT_SOP = "1.2.840.10008.5.1.4.1.1.481.3"
_RTDOSE_SOP = "1.2.840.10008.5.1.4.1.1.481.2"
_RTPLAN_SOP = "1.2.840.10008.5.1.4.1.1.481.5"

_PLANE_ROUND_MM = 3           # contour planes are grouped at 1e-3 mm
_DOSE_SUMMATION_OK = ("PLAN", "MULTI_PLAN", "BRACHY")


# ----------------------------------------------------------------- helpers
def _dcmread(path, **kw):
    import pydicom
    return pydicom.dcmread(str(path), force=True, **kw)


def _find_dicom_file(path_or_dir, modality):
    """Return the one file in ``path_or_dir`` whose Modality is ``modality``.

    A file path is returned unchanged (its modality is checked by the
    caller); a directory is scanned (headers only) and must contain exactly
    one file of that modality.
    """
    p = str(path_or_dir)
    if os.path.isfile(p):
        return p
    if not os.path.isdir(p):
        raise FileNotFoundError("no such file or directory: %s" % p)
    hits = []
    for name in sorted(os.listdir(p)):
        f = os.path.join(p, name)
        if not os.path.isfile(f):
            continue
        try:
            ds = _dcmread(f, stop_before_pixels=True,
                          specific_tags=["Modality", "SOPClassUID"])
        except Exception:
            continue
        if str(getattr(ds, "Modality", "")).upper() == modality:
            hits.append(f)
    if len(hits) != 1:
        raise FileNotFoundError(
            "expected exactly one %s file in %s, found %d" % (modality, p, len(hits)))
    return hits[0]


def _str(ds, keyword, default=""):
    v = getattr(ds, keyword, None)
    if v is None:
        return default
    try:
        return str(v)
    except Exception:  # pragma: no cover
        return default


def _float(ds, keyword, default=None):
    v = getattr(ds, keyword, None)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_dicom_datetime(date_str, time_str):
    """DICOM DA ('YYYYMMDD') + TM ('HHMMSS.FFFFFF', shorter forms allowed)
    -> ``datetime`` or None."""
    if not date_str:
        return None
    d = str(date_str).strip()
    t = str(time_str or "").strip().replace(":", "")
    if len(d) != 8 or not d.isdigit():
        return None
    frac = 0
    if "." in t:
        t, f = t.split(".", 1)
        f = (f + "000000")[:6]
        frac = int(f) if f.isdigit() else 0
    t = (t + "000000")[:6]
    if not t.isdigit():
        t = "000000"
    try:
        return datetime(int(d[:4]), int(d[4:6]), int(d[6:8]),
                        int(t[:2]), int(t[2:4]), int(t[4:6]), frac)
    except ValueError:
        return None


def _lps_points_to_ras(pts):
    pts = np.asarray(pts, dtype=float).reshape(-1, 3).copy()
    pts[:, :2] *= -1.0
    return pts


def _lps_vector_to_ras(v):
    v = np.asarray(v, dtype=float).reshape(3).copy()
    v[:2] *= -1.0
    return v


# ----------------------------------------------------------------- RTSTRUCT
@dataclass
class Contour:
    """One ContourSequence item in RAS mm."""

    points_ras: np.ndarray            # (N, 3)
    geometric_type: str               # CLOSED_PLANAR | POINT | OPEN_PLANAR | ...

    @property
    def n_points(self) -> int:
        return int(self.points_ras.shape[0])

    @property
    def z_ras(self) -> float:
        """Mean RAS z of the points (the plane position for planar contours)."""
        return float(np.mean(self.points_ras[:, 2]))


@dataclass
class RTStruct:
    """A parsed RTSTRUCT: ROI names/numbers and their contours in RAS mm."""

    roi_names: List[str]
    roi_numbers: Dict[str, int]
    contours_by_number: Dict[int, List[Contour]]
    frame_of_reference_uid: Optional[str] = None
    referenced_series_uid: Optional[str] = None
    referenced_sop_instance_uids: List[str] = field(default_factory=list)
    interpreted_types: Dict[int, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)
    dataset: Any = field(default=None, repr=False)

    # ------------------------------------------------------------ lookup
    def roi_number(self, name) -> int:
        """ROI number of ``name`` (an int is returned unchanged)."""
        if isinstance(name, (int, np.integer)):
            n = int(name)
            if n not in self.contours_by_number:
                raise KeyError("no ROI number %d in this RTSTRUCT" % n)
            return n
        try:
            return self.roi_numbers[str(name)]
        except KeyError:
            raise KeyError("no ROI named %r (available: %s)"
                           % (name, ", ".join(self.roi_names))) from None

    def roi_name(self, number) -> str:
        for n, num in self.roi_numbers.items():
            if num == int(number):
                return n
        raise KeyError("no ROI number %r" % (number,))

    def contours(self, name, geometric_type="CLOSED_PLANAR") -> List[np.ndarray]:
        """Contours of ``name`` as ``(N, 3)`` RAS mm arrays.

        ``geometric_type`` filters on ``ContourGeometricType`` (default: only
        closed planar contours, which is what the rasteriser consumes); pass
        ``None`` for every contour including POINT and OPEN_PLANAR.
        """
        items = self.contours_by_number[self.roi_number(name)]
        if geometric_type is not None:
            gt = str(geometric_type).upper()
            items = [c for c in items if c.geometric_type == gt]
        return [c.points_ras for c in items]

    def contour_items(self, name) -> List[Contour]:
        return list(self.contours_by_number[self.roi_number(name)])

    def n_contours(self, name) -> int:
        return len(self.contours_by_number[self.roi_number(name)])

    def contour_types(self, name) -> List[str]:
        return sorted(set(c.geometric_type for c in self.contour_items(name)))

    def contour_planes_z(self, name) -> np.ndarray:
        """Sorted unique RAS z of the closed planar contour planes (mm)."""
        zs = [c.z_ras for c in self.contour_items(name)
              if c.geometric_type == "CLOSED_PLANAR"]
        if not zs:
            return np.zeros(0)
        return np.unique(np.round(np.asarray(zs, dtype=float), _PLANE_ROUND_MM))

    def contour_z_pitch_mm(self, name) -> float:
        """Median spacing of the structure's contour planes (mm); NaN when
        the structure has fewer than two planes."""
        z = self.contour_planes_z(name)
        if z.size < 2:
            return float("nan")
        return float(np.median(np.diff(z)))

    def describe(self) -> str:
        lines = ["%-18s %5s %6s %7s  %s" % ("ROI", "n", "planes", "pitch", "types")]
        for n in self.roi_names:
            items = self.contour_items(n)
            z = self.contour_planes_z(n)
            pitch = self.contour_z_pitch_mm(n)
            lines.append("%-18s %5d %6d %7s  %s" % (
                n, len(items), z.size,
                ("%.3f" % pitch) if np.isfinite(pitch) else "-",
                ",".join(self.contour_types(n)) or "(empty)"))
        return "\n".join(lines)


def load_rtstruct(path_or_dir) -> RTStruct:
    """Read an RTSTRUCT file (or the one RTSTRUCT in a directory)."""
    f = _find_dicom_file(path_or_dir, "RTSTRUCT")
    ds = _dcmread(f)
    modality = _str(ds, "Modality").upper()
    if modality and modality != "RTSTRUCT":
        raise ValueError("%s is modality %r, not RTSTRUCT" % (f, modality))

    names, numbers = [], {}
    for roi in getattr(ds, "StructureSetROISequence", []):
        num = int(roi.ROINumber)
        name = _str(roi, "ROIName", "ROI_%d" % num)
        if name in numbers:
            warnings.warn("duplicate ROI name %r in RTSTRUCT; keeping the first "
                          "(ROI %d), ROI %d is reachable by number only"
                          % (name, numbers[name], num))
            names.append(name)  # keep the listing honest
            continue
        names.append(name)
        numbers[name] = num

    contours: Dict[int, List[Contour]] = {num: [] for num in numbers.values()}
    for rc in getattr(ds, "ROIContourSequence", []):
        num = int(rc.ReferencedROINumber)
        bucket = contours.setdefault(num, [])
        for cs in getattr(rc, "ContourSequence", []):
            data = getattr(cs, "ContourData", None)
            if data is None or len(data) < 3:
                continue
            pts = _lps_points_to_ras(np.asarray(data, dtype=float))
            bucket.append(Contour(pts, _str(cs, "ContourGeometricType", "CLOSED_PLANAR").upper()))
    # ROIs listed in ROIContourSequence but not in StructureSetROISequence
    for num in contours:
        if num not in numbers.values():
            name = "ROI_%d" % num
            names.append(name)
            numbers[name] = num

    interp = {}
    for ob in getattr(ds, "RTROIObservationsSequence", []):
        try:
            interp[int(ob.ReferencedROINumber)] = _str(ob, "RTROIInterpretedType")
        except (AttributeError, TypeError, ValueError):
            continue

    for_uid, series_uid, sops = None, None, []
    for fr in getattr(ds, "ReferencedFrameOfReferenceSequence", []):
        if for_uid is None:
            for_uid = _str(fr, "FrameOfReferenceUID", None)
        for st in getattr(fr, "RTReferencedStudySequence", []):
            for se in getattr(st, "RTReferencedSeriesSequence", []):
                if series_uid is None:
                    series_uid = _str(se, "SeriesInstanceUID", None)
                for ci in getattr(se, "ContourImageSequence", []):
                    s = _str(ci, "ReferencedSOPInstanceUID", None)
                    if s:
                        sops.append(s)
    if for_uid is None:
        # fall back to the per-ROI reference
        for roi in getattr(ds, "StructureSetROISequence", []):
            for_uid = _str(roi, "ReferencedFrameOfReferenceUID", None)
            if for_uid:
                break

    meta = {
        "source": f,
        "manufacturer": _str(ds, "Manufacturer"),
        "software": _str(ds, "SoftwareVersions"),
        "structure_set_label": _str(ds, "StructureSetLabel"),
        "sop_instance_uid": _str(ds, "SOPInstanceUID", None),
    }
    return RTStruct(names, numbers, contours, for_uid, series_uid, sops, interp, meta, ds)


# ------------------------------------------------------------- rasteriser
def _coverage_fraction(polys_ij, nj, ni, supersample):
    """Even-odd scan-line fill of ``polys_ij`` (list of ``(N, 2)`` arrays of
    continuous voxel-index coordinates ``(i, j)``, voxel centres at integers)
    on an ``nj x ni`` grid with ``supersample`` sub-pixels per axis.  Returns
    the ``(nj, ni)`` float fraction of covered sub-pixels per voxel.

    Equivalent to XOR-ing ``skimage.draw.polygon`` fills of the individual
    polygons on the supersampled grid, but O(perimeter + area) instead of
    O(bounding box x vertices), which matters for 1000-vertex brain contours.
    """
    s = int(supersample)
    R, C = nj * s, ni * s
    x0s, y0s, x1s, y1s = [], [], [], []
    for poly in polys_ij:
        p = np.asarray(poly, dtype=float).reshape(-1, 2)
        if p.shape[0] < 3:
            continue
        # sub-pixel m has centre at index (m + 0.5) / s - 0.5  ->  m = (x + 0.5) s - 0.5
        q = (p + 0.5) * s - 0.5
        nxt = np.roll(q, -1, axis=0)
        x0s.append(q[:, 0]); y0s.append(q[:, 1])
        x1s.append(nxt[:, 0]); y1s.append(nxt[:, 1])
    frac = np.zeros((nj, ni), dtype=float)
    if not x0s:
        return frac
    x0 = np.concatenate(x0s); y0 = np.concatenate(y0s)
    x1 = np.concatenate(x1s); y1 = np.concatenate(y1s)
    keep = y0 != y1
    x0, y0, x1, y1 = x0[keep], y0[keep], x1[keep], y1[keep]
    if x0.size == 0:
        return frac
    ymin = np.minimum(y0, y1); ymax = np.maximum(y0, y1)
    r_start = np.maximum(np.ceil(ymin).astype(np.int64), 0)
    r_end = np.minimum(np.ceil(ymax).astype(np.int64), R)   # exclusive
    n = np.maximum(r_end - r_start, 0)
    total = int(n.sum())
    if total == 0:
        return frac
    edge = np.repeat(np.arange(x0.size), n)
    offs = np.arange(total) - np.repeat(np.cumsum(n) - n, n)
    r = r_start[edge] + offs
    slope = (x1 - x0) / (y1 - y0)
    x = x0[edge] + (r - y0[edge]) * slope[edge]
    order = np.lexsort((x, r))
    r, x = r[order], x[order]
    row_start = np.searchsorted(r, r, side="left")
    pos = np.arange(r.size) - row_start
    left = (pos % 2) == 0
    right = ~left
    if left.sum() != right.sum():  # pragma: no cover - cannot happen for closed polygons
        m = min(left.sum(), right.sum())
        xl, xr, rows = x[left][:m], x[right][:m], r[left][:m]
    else:
        xl, xr, rows = x[left], x[right], r[left]
    c0 = np.floor(xl).astype(np.int64) + 1          # strictly inside: xl < c < xr
    c1 = np.ceil(xr).astype(np.int64) - 1
    c0 = np.maximum(c0, 0)
    c1 = np.minimum(c1, C - 1)
    ok = c1 >= c0
    if not ok.any():
        return frac
    acc = np.zeros((R, C + 1), dtype=np.int32)
    np.add.at(acc, (rows[ok], c0[ok]), 1)
    np.add.at(acc, (rows[ok], c1[ok] + 1), -1)
    inside = np.cumsum(acc, axis=1)[:, :C] > 0
    return inside.reshape(nj, s, ni, s).mean(axis=(1, 3))


def rasterize_roi(rtstruct: RTStruct, name, affine, shape, supersample=4,
                  min_fraction=0.5, slice_rule="nearest", pitch_mm=None):
    """Voxel mask (bool ``[k, j, i]``) of one ROI on the grid ``(affine, shape)``.

    See the module docstring for the rule.  ``supersample`` sub-pixels per
    voxel axis in-plane; a voxel is in when its covered fraction is
    ``>= min_fraction``.  ``slice_rule`` must be ``"nearest"`` (the only rule;
    the keyword exists so a different convention can be named explicitly
    later).  ``pitch_mm`` overrides the structure's own contour pitch, which
    is needed when a structure has a single contour plane (then the output
    grid's k-spacing is used and a warning issued).

    POINT / OPEN_PLANAR contours and degenerate polygons (< 3 points) are
    skipped with a warning; an ROI without closed planar contours gives an
    all-False mask and a warning, never an exception.
    """
    if str(slice_rule) != "nearest":
        raise ValueError("slice_rule must be 'nearest', got %r" % (slice_rule,))
    supersample = int(supersample)
    if supersample < 1:
        raise ValueError("supersample must be >= 1")
    if not (0.0 < float(min_fraction) <= 1.0):
        raise ValueError("min_fraction must be in (0, 1]")
    affine = np.asarray(affine, dtype=float)
    nk, nj, ni = (int(v) for v in shape)
    mask = np.zeros((nk, nj, ni), dtype=bool)

    items = rtstruct.contour_items(name)
    closed = [c for c in items if c.geometric_type == "CLOSED_PLANAR"]
    skipped = [c.geometric_type for c in items if c.geometric_type != "CLOSED_PLANAR"]
    if skipped:
        warnings.warn("ROI %r: skipping %d non-planar contour(s) of type %s"
                      % (name, len(skipped), sorted(set(skipped))))
    closed = [c for c in closed if c.n_points >= 3] or []
    if len(closed) < len([c for c in items if c.geometric_type == "CLOSED_PLANAR"]):
        warnings.warn("ROI %r: skipping degenerate closed contour(s) with < 3 points" % (name,))
    if not closed:
        warnings.warn("ROI %r has no closed planar contours; mask is empty" % (name,))
        return mask

    inv = np.linalg.inv(affine)
    spacing_k = float(np.linalg.norm(affine[:3, 2]))

    # Group contours into planes by their RAS z (structure-intrinsic), then
    # locate each plane on the output k axis.
    z_key = np.round([c.z_ras for c in closed], _PLANE_ROUND_MM)
    planes: Dict[float, List[Contour]] = {}
    for zc, c in zip(z_key, closed):
        planes.setdefault(float(zc), []).append(c)
    plane_z = np.array(sorted(planes))

    if pitch_mm is None:
        if plane_z.size >= 2:
            pitch = float(np.median(np.diff(plane_z)))
        else:
            pitch = spacing_k
            warnings.warn("ROI %r has a single contour plane; using the output "
                          "k-spacing %.3f mm as its pitch" % (name, pitch))
    else:
        pitch = float(pitch_mm)
    if not (pitch > 0.0):
        raise ValueError("contour pitch must be positive, got %r" % pitch)
    half_k = 0.5 * pitch / spacing_k

    # plane centre in index-k units and polygons in continuous (i, j)
    plane_k = np.empty(plane_z.size)
    plane_polys: List[List[np.ndarray]] = []
    warned_oblique = False
    for p_idx, zc in enumerate(plane_z):
        ks, polys = [], []
        for c in planes[float(zc)]:
            ijk = apply_affine(inv, c.points_ras)
            if not warned_oblique and np.ptp(ijk[:, 2]) > 0.01:
                warnings.warn("ROI %r: contour points are not coplanar with the "
                              "output grid's slices (k spread %.3f); using their "
                              "mean k" % (name, float(np.ptp(ijk[:, 2]))))
                warned_oblique = True
            ks.append(float(np.mean(ijk[:, 2])))
            polys.append(ijk[:, :2])
        plane_k[p_idx] = float(np.mean(ks))
        plane_polys.append(polys)
    order = np.argsort(plane_k)
    plane_k = plane_k[order]
    plane_polys = [plane_polys[i] for i in order]

    # Half-open claim [k_c - half, k_c + half): the plane claiming output
    # slice k is the first plane whose upper bound exceeds k (with a tiny
    # tolerance so exact ties resolve deterministically).
    eps = 1e-6 * max(half_k, 1e-9)
    upper = plane_k + half_k - eps
    ks = np.arange(nk, dtype=float)
    idx = np.searchsorted(upper, ks, side="right")
    valid = idx < plane_k.size
    claimed = np.zeros(nk, dtype=bool)
    claimed[valid] = ks[valid] >= plane_k[idx[valid]] - half_k - eps

    cache: Dict[int, np.ndarray] = {}
    for k in np.flatnonzero(claimed):
        p_idx = int(idx[k])
        if p_idx not in cache:
            frac = _coverage_fraction(plane_polys[p_idx], nj, ni, supersample)
            cache[p_idx] = frac >= float(min_fraction) - 1e-12
        mask[k] = cache[p_idx]
    return mask


def rasterize_rois(rtstruct: RTStruct, names: Iterable[str], volume: Volume,
                   **kwargs) -> Dict[str, np.ndarray]:
    """``{name: mask}`` for several ROIs on ``volume``'s grid (see
    :func:`rasterize_roi` for the keyword arguments)."""
    return {n: rasterize_roi(rtstruct, n, volume.affine, volume.array.shape, **kwargs)
            for n in names}


# ------------------------------------------------------------------- RTDOSE
def _parse_dvh_sequence(ds) -> List[Dict[str, Any]]:
    """Raw per-ROI DVH items of an RTDOSE ``DVHSequence`` (empty if absent)."""
    out = []
    for item in getattr(ds, "DVHSequence", []):
        roi_numbers = [int(r.ReferencedROINumber)
                       for r in getattr(item, "DVHReferencedROISequence", [])
                       if hasattr(r, "ReferencedROINumber")]
        data = np.asarray(getattr(item, "DVHData", []), dtype=float)
        out.append({
            "roi_numbers": roi_numbers,
            "dvh_type": _str(item, "DVHType", "CUMULATIVE").upper(),
            "dose_units": _str(item, "DoseUnits", "GY").upper(),
            "dose_scaling": _float(item, "DVHDoseScaling", 1.0),
            "volume_units": _str(item, "DVHVolumeUnits", "CM3").upper(),
            "data": data,
            "min_dose": _float(item, "DVHMinimumDose"),
            "max_dose": _float(item, "DVHMaximumDose"),
            "mean_dose": _float(item, "DVHMeanDose"),
        })
    return out


def load_rtdose(path_or_dir, allow_any=False) -> Volume:
    """Read an RTDOSE (file or the one RTDOSE in a directory) as a
    :class:`Volume` in **cGy** with an RAS affine.

    ``DoseUnits`` GY is converted (x100), CGY kept, anything else raises.
    ``DoseGridScaling`` is applied.  ``GridFrameOffsetVector`` gives the frame
    positions along the slice normal and must be uniform (raises otherwise).
    ``DoseSummationType`` must be PLAN, MULTI_PLAN or BRACHY unless
    ``allow_any=True``.  ``meta`` carries ``dose_units_original``,
    ``dose_type``, ``dose_summation_type``, ``frame_of_reference_uid``,
    ``referenced_plan_uid``, ``manufacturer``, ``software`` and, when the
    file has one, the parsed ``dvh_sequence``.
    """
    f = _find_dicom_file(path_or_dir, "RTDOSE")
    ds = _dcmread(f)
    modality = _str(ds, "Modality").upper()
    if modality and modality != "RTDOSE":
        raise ValueError("%s is modality %r, not RTDOSE" % (f, modality))

    units = _str(ds, "DoseUnits").upper()
    if units == "GY":
        factor = 100.0
    elif units == "CGY":
        factor = 1.0
    else:
        raise ValueError("unsupported DoseUnits %r (need GY or CGY)" % units)

    summation = _str(ds, "DoseSummationType").upper()
    if summation not in _DOSE_SUMMATION_OK and not allow_any:
        raise ValueError(
            "DoseSummationType %r is not a whole-plan dose (%s); pass allow_any=True "
            "to load it anyway" % (summation, "/".join(_DOSE_SUMMATION_OK)))

    scaling = _float(ds, "DoseGridScaling", 1.0)
    arr = np.asarray(ds.pixel_array)
    if arr.ndim == 2:
        arr = arr[None]
    if arr.ndim != 3:
        raise ValueError("RTDOSE pixel data must be 2-D or 3-D, got shape %s" % (arr.shape,))
    dose = arr.astype(np.float64) * scaling * factor

    ipp = np.asarray(getattr(ds, "ImagePositionPatient", [0.0, 0.0, 0.0]), dtype=float)
    iop = np.asarray(getattr(ds, "ImageOrientationPatient",
                             [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]), dtype=float)
    ps = [float(v) for v in getattr(ds, "PixelSpacing", [1.0, 1.0])]
    row_dir, col_dir = iop[:3], iop[3:]
    normal = np.cross(row_dir, col_dir)
    normal /= np.linalg.norm(normal)

    gfo = getattr(ds, "GridFrameOffsetVector", None)
    nk = dose.shape[0]
    if gfo is not None and len(gfo) >= 1:
        gfo = np.asarray(gfo, dtype=float)
        if len(gfo) != nk:
            raise ValueError("GridFrameOffsetVector has %d entries for %d frames"
                             % (len(gfo), nk))
        if nk >= 2:
            dzs = np.diff(gfo)
            dz = float(dzs[0])
            if dz == 0.0 or np.any(np.abs(dzs - dz) > 1e-3 * max(abs(dz), 1e-9) + 1e-6):
                raise ValueError("non-uniform GridFrameOffsetVector (frame spacing "
                                 "ranges %.4f..%.4f mm)" % (dzs.min(), dzs.max()))
        else:
            dz = _float(ds, "SliceThickness", 1.0) or 1.0
        # DICOM C.8.8.3.2: offsets relative to IPP along the normal, unless the
        # first value equals the IPP z (then absolute z positions, legacy form)
        first = float(gfo[0])
        if first != 0.0 and abs(first - ipp[2]) < 1e-3 and np.allclose(normal, [0, 0, 1], atol=1e-6):
            origin = ipp.copy()
        else:
            origin = ipp + normal * first
    else:
        if nk > 1:
            raise ValueError("multi-frame RTDOSE without GridFrameOffsetVector")
        dz = _float(ds, "SliceThickness", 1.0) or 1.0
        origin = ipp.copy()
        warnings.warn("single-frame RTDOSE without GridFrameOffsetVector; k-spacing "
                      "taken as %.3f mm" % dz)

    affine_lps = np.eye(4)
    affine_lps[:3, 0] = row_dir * ps[1]     # i = column index steps along the row direction
    affine_lps[:3, 1] = col_dir * ps[0]     # j = row index steps down the column direction
    affine_lps[:3, 2] = normal * dz
    affine_lps[:3, 3] = origin
    affine = _LPS_TO_RAS @ affine_lps

    ref_plan = None
    for rp in getattr(ds, "ReferencedRTPlanSequence", []):
        ref_plan = _str(rp, "ReferencedSOPInstanceUID", None)
        break
    ref_ss = None
    for rs in getattr(ds, "ReferencedStructureSetSequence", []):
        ref_ss = _str(rs, "ReferencedSOPInstanceUID", None)
        break

    meta = {
        "kind": "rtdose",
        "units": "cGy",
        "source": f,
        "dose_units_original": units,
        "dose_type": _str(ds, "DoseType").upper(),
        "dose_summation_type": summation,
        "dose_grid_scaling": scaling,
        "frame_of_reference_uid": _str(ds, "FrameOfReferenceUID", None),
        "referenced_plan_uid": ref_plan,
        "referenced_structure_set_uid": ref_ss,
        "manufacturer": _str(ds, "Manufacturer"),
        "software": _str(ds, "SoftwareVersions"),
        "tissue_heterogeneity_correction": _str(ds, "TissueHeterogeneityCorrection"),
        "sop_instance_uid": _str(ds, "SOPInstanceUID", None),
    }
    dvh = _parse_dvh_sequence(ds)
    if dvh:
        meta["dvh_sequence"] = dvh
    return Volume(dose, affine, meta)


# ------------------------------------------------------------------- RTPLAN
@dataclass
class Source:
    """One brachytherapy source (seed) of an RTPLAN, in RAS mm."""

    position_ras: np.ndarray                    # (3,)
    orientation_ras: Optional[np.ndarray]       # (3,) unit vector or None
    air_kerma_strength_u: Optional[float]       # U = uGy m^2 / h (ReferenceAirKermaRate)
    reference_datetime: Optional[datetime]      # SourceStrengthReferenceDate/Time
    isotope: str = ""
    model_id: str = ""
    manufacturer: str = ""
    source_number: Optional[int] = None
    channel_number: Optional[int] = None
    source_type: str = ""                       # POINT | LINE | CYLINDER | SPHERE
    half_life_days: Optional[float] = None
    active_length_mm: Optional[float] = None
    active_diameter_mm: Optional[float] = None
    n_control_points: int = 0


@dataclass
class RTPlan:
    """Parsed brachy RTPLAN: the sources plus plan-level descriptors."""

    sources: List[Source]
    plan_level: Dict[str, Any] = field(default_factory=dict)
    frame_of_reference_uid: Optional[str] = None
    referenced_structure_set_uid: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    dataset: Any = field(default=None, repr=False)

    @property
    def n_sources(self) -> int:
        return len(self.sources)

    @property
    def positions_ras(self) -> np.ndarray:
        if not self.sources:
            return np.zeros((0, 3))
        return np.stack([s.position_ras for s in self.sources])

    @property
    def orientations_ras(self) -> Optional[np.ndarray]:
        if not self.sources or any(s.orientation_ras is None for s in self.sources):
            return None
        return np.stack([s.orientation_ras for s in self.sources])

    @property
    def strengths_u(self) -> np.ndarray:
        return np.array([np.nan if s.air_kerma_strength_u is None else s.air_kerma_strength_u
                         for s in self.sources], dtype=float)


def _source_catalog(ds) -> Dict[int, Dict[str, Any]]:
    cat = {}
    for s in getattr(ds, "SourceSequence", []):
        num = int(getattr(s, "SourceNumber", len(cat) + 1))
        rakr = _float(s, "ReferenceAirKermaRate")
        if rakr is None:
            # Some systems write SourceStrength with SourceStrengthUnits instead
            units = _str(s, "SourceStrengthUnits").upper()
            val = _float(s, "SourceStrength")
            if val is not None and units in ("AIR_KERMA_RATE", "U", "UGY_M2_H", ""):
                rakr = val
        cat[num] = {
            "air_kerma_strength_u": rakr,
            "reference_datetime": _parse_dicom_datetime(
                _str(s, "SourceStrengthReferenceDate"), _str(s, "SourceStrengthReferenceTime")),
            "isotope": _str(s, "SourceIsotopeName"),
            "model_id": _str(s, "SourceModelID"),
            "manufacturer": _str(s, "SourceManufacturer"),
            "source_type": _str(s, "SourceType").upper(),
            "half_life_days": _float(s, "SourceIsotopeHalfLife"),
            "active_length_mm": _float(s, "ActiveSourceLength"),
            "active_diameter_mm": _float(s, "ActiveSourceDiameter"),
            "description": _str(s, "SourceDescription"),
        }
    return cat


def load_rtplan(path_or_dir) -> RTPlan:
    """Read a brachytherapy RTPLAN (file or the one RTPLAN in a directory).

    Sources are read from ``ApplicationSetupSequence -> ChannelSequence ->
    BrachyControlPointSequence``: one channel = one seed for a permanent
    implant (``SourceMovementType`` FIXED).  The seed position is the mean of
    the channel's distinct ``ControlPoint3DPosition`` values (a single point,
    or the midpoint when both ends of a line source are given); the
    orientation is ``ControlPointOrientation`` when present, else the
    end-to-end direction when two distinct positions exist, else None.
    Per-seed air-kerma strength, reference time, isotope and model come from
    the referenced ``SourceSequence`` item.  All coordinates are converted
    LPS -> RAS.

    ``plan_level`` holds the treatment technique/type, prescription (from
    ``DoseReferenceSequence`` TARGET items, cGy), plan label/date, approval
    status, manufacturer/software and the count of sources/channels.  The
    DICOM RT Plan IOD has no public attribute for the TG-43 formalism (1D/2D)
    or the anisotropy dataset; ``plan_level["private_creators"]`` lists the
    private-block creator strings found so a vendor-specific attribute can be
    located by hand, and ``formalism`` stays None.
    """
    f = _find_dicom_file(path_or_dir, "RTPLAN")
    ds = _dcmread(f)
    modality = _str(ds, "Modality").upper()
    if modality and modality != "RTPLAN":
        raise ValueError("%s is modality %r, not RTPLAN" % (f, modality))

    catalog = _source_catalog(ds)
    sources: List[Source] = []
    n_channels = 0
    for setup in getattr(ds, "ApplicationSetupSequence", []):
        for ch in getattr(setup, "ChannelSequence", []):
            n_channels += 1
            ref = getattr(ch, "ReferencedSourceNumber", None)
            info = catalog.get(int(ref)) if ref is not None else None
            if info is None and len(catalog) == 1:
                info = next(iter(catalog.values()))
                ref = next(iter(catalog))
            info = info or {}
            cps = list(getattr(ch, "BrachyControlPointSequence", []))
            positions, orientations = [], []
            for cp in cps:
                p = getattr(cp, "ControlPoint3DPosition", None)
                if p is not None and len(p) == 3:
                    positions.append(_lps_points_to_ras(np.asarray(p, dtype=float))[0])
                o = getattr(cp, "ControlPointOrientation", None)
                if o is not None and len(o) == 3:
                    orientations.append(_lps_vector_to_ras(np.asarray(o, dtype=float)))
            if not positions:
                warnings.warn("RTPLAN channel %s has no ControlPoint3DPosition; skipped"
                              % _str(ch, "ChannelNumber", "?"))
                continue
            pos = np.asarray(positions)
            uniq = np.unique(np.round(pos, 4), axis=0)
            position = uniq.mean(axis=0)
            orientation = None
            if orientations:
                o = np.asarray(orientations).mean(axis=0)
                n = np.linalg.norm(o)
                if n > 0:
                    orientation = o / n
            elif uniq.shape[0] >= 2:
                d = uniq[-1] - uniq[0]
                n = np.linalg.norm(d)
                if n > 0:
                    orientation = d / n
            sources.append(Source(
                position_ras=position,
                orientation_ras=orientation,
                air_kerma_strength_u=info.get("air_kerma_strength_u"),
                reference_datetime=info.get("reference_datetime"),
                isotope=info.get("isotope", ""),
                model_id=info.get("model_id", ""),
                manufacturer=info.get("manufacturer", ""),
                source_number=int(ref) if ref is not None else None,
                channel_number=int(ch.ChannelNumber) if hasattr(ch, "ChannelNumber") else None,
                source_type=info.get("source_type", ""),
                half_life_days=info.get("half_life_days"),
                active_length_mm=info.get("active_length_mm"),
                active_diameter_mm=info.get("active_diameter_mm"),
                n_control_points=len(cps),
            ))

    prescriptions = []
    for dr in getattr(ds, "DoseReferenceSequence", []):
        tpd = _float(dr, "TargetPrescriptionDose")
        prescriptions.append({
            "type": _str(dr, "DoseReferenceType").upper(),
            "structure_type": _str(dr, "DoseReferenceStructureType").upper(),
            "description": _str(dr, "DoseReferenceDescription"),
            "target_prescription_cgy": None if tpd is None else 100.0 * tpd,   # Gy -> cGy
            "referenced_roi_number": _float(dr, "ReferencedROINumber"),
        })
    rx = [p["target_prescription_cgy"] for p in prescriptions
          if p["type"] == "TARGET" and p["target_prescription_cgy"] is not None]

    private_creators = []
    try:
        for elem in ds:
            if elem.tag.is_private and elem.tag.element < 0x100 and elem.VR == "LO":
                private_creators.append(str(elem.value))
    except Exception:  # pragma: no cover
        pass

    isotopes = sorted(set(s.isotope for s in sources if s.isotope))
    models = sorted(set(s.model_id for s in sources if s.model_id))
    plan_level = {
        "plan_label": _str(ds, "RTPlanLabel"),
        "plan_name": _str(ds, "RTPlanName"),
        "plan_datetime": _parse_dicom_datetime(_str(ds, "RTPlanDate"), _str(ds, "RTPlanTime")),
        "plan_geometry": _str(ds, "RTPlanGeometry").upper(),
        "approval_status": _str(ds, "ApprovalStatus").upper(),
        "brachy_treatment_technique": _str(ds, "BrachyTreatmentTechnique").upper(),
        "brachy_treatment_type": _str(ds, "BrachyTreatmentType").upper(),
        "application_setup_types": sorted(set(
            _str(a, "ApplicationSetupType").upper()
            for a in getattr(ds, "ApplicationSetupSequence", []))),
        "total_reference_air_kerma": [
            _float(a, "TotalReferenceAirKerma")
            for a in getattr(ds, "ApplicationSetupSequence", [])],
        "n_channels": n_channels,
        "n_sources": len(sources),
        "n_source_models": len(catalog),
        "isotopes": isotopes,
        "source_models": models,
        "prescriptions": prescriptions,
        "prescription_cgy": rx[0] if rx else None,
        "formalism": None,                      # not a public DICOM attribute
        "dose_calculation_algorithm": None,     # not a public DICOM attribute
        "private_creators": private_creators,
        "manufacturer": _str(ds, "Manufacturer"),
        "software": _str(ds, "SoftwareVersions"),
    }
    ref_ss = None
    for rs in getattr(ds, "ReferencedStructureSetSequence", []):
        ref_ss = _str(rs, "ReferencedSOPInstanceUID", None)
        break
    meta = {"source": f, "sop_instance_uid": _str(ds, "SOPInstanceUID", None)}
    return RTPlan(sources, plan_level, _str(ds, "FrameOfReferenceUID", None), ref_ss, meta, ds)


# ------------------------------------------------------- TPS verification
def _dose_stats(doses, rx_cgy, voxel_volume_mm3):
    """The one place this module computes D90 / V100 / volume.

    Currently ``gtcore.dose.hrctv.hrctv_stats`` (D90 = Hyndman-Fan type-1
    inverted-CDF quantile, V100 = fraction of voxels with dose >= rx; both
    match the Phase 2 ``dose_stats`` contract).  When
    ``gtcore.dose.metrics.dose_stats`` lands, replace the body with
    ``return dose_stats(doses, rx_cgy, voxel_volume_mm3=voxel_volume_mm3)``.
    """
    from ..dose.hrctv import hrctv_stats
    return hrctv_stats(np.asarray(doses, dtype=float).ravel(), float(rx_cgy),
                       float(voxel_volume_mm3))


def _norm_name(s):
    return re.sub(r"\s+", " ", str(s)).strip().lower()


def _d_from_cumulative(dose_edges, volumes, fraction):
    """Dose at which the cumulative volume falls to ``fraction`` of the total
    (linear interpolation along the binned curve; ``fraction=0.9`` -> D90)."""
    v = np.asarray(volumes, dtype=float)
    d = np.asarray(dose_edges, dtype=float)
    if v.size == 0 or v[0] <= 0:
        return float("nan")
    target = fraction * v[0]
    # v is non-increasing; find first index where v <= target
    idx = int(np.argmax(v <= target)) if np.any(v <= target) else v.size - 1
    if idx == 0:
        return float(d[0])
    v0, v1, d0, d1 = v[idx - 1], v[idx], d[idx - 1], d[idx]
    if v0 == v1:
        return float(d1)
    return float(d0 + (target - v0) * (d1 - d0) / (v1 - v0))


def reference_from_dvh_sequence(dvh_items, rtstruct: Optional[RTStruct] = None,
                                percent=90.0) -> Dict[str, Dict[str, float]]:
    """Convert an RTDOSE ``DVHSequence`` (a pydicom Dataset holding one, a
    Volume from :func:`load_rtdose`, or its parsed ``meta["dvh_sequence"]``)
    into the reference dict :func:`verify_against_tps` consumes:
    ``{roi_name: {"volume_cc", "D90_cgy", "Dmin_cgy", "Dmax_cgy", "Dmean_cgy"}}``.

    ``DVHData`` is read as (bin width, volume) pairs; for a CUMULATIVE DVH the
    n-th volume is the volume receiving at least the cumulative dose at the
    start of the n-th bin (so the first value is the total volume), the
    dicompyler convention; a DIFFERENTIAL DVH is accumulated first.  D90 is
    the linear interpolation of that curve at 90 % of the total.  Doses are
    scaled by ``DVHDoseScaling`` and converted to cGy; volumes in PERCENT give
    ``volume_cc = nan``.  ROI numbers are mapped to names through ``rtstruct``
    (else the key is ``"ROI_<n>"``).
    """
    if isinstance(dvh_items, Volume):
        dvh_items = dvh_items.meta.get("dvh_sequence", [])
    elif not isinstance(dvh_items, (list, tuple)):
        dvh_items = _parse_dvh_sequence(dvh_items)
    out: Dict[str, Dict[str, float]] = {}
    for item in dvh_items:
        data = np.asarray(item["data"], dtype=float)
        if data.size < 2:
            continue
        widths = data[0::2] * float(item.get("dose_scaling") or 1.0)
        vols = data[1::2]
        n = min(widths.size, vols.size)
        widths, vols = widths[:n], vols[:n]
        dose_factor = 100.0 if item.get("dose_units", "GY") == "GY" else 1.0
        edges = np.concatenate([[0.0], np.cumsum(widths)])[:n] * dose_factor
        if item.get("dvh_type", "CUMULATIVE") == "DIFFERENTIAL":
            cum = np.cumsum(vols[::-1])[::-1]   # volume receiving >= edge
        else:
            cum = vols
        total = float(cum[0]) if cum.size else float("nan")
        vol_cc = total if item.get("volume_units", "CM3") == "CM3" else float("nan")
        rec = {
            "volume_cc": vol_cc,
            "D%d_cgy" % int(round(percent)): _d_from_cumulative(edges, cum, percent / 100.0),
            "Dmin_cgy": None if item.get("min_dose") is None else item["min_dose"] * dose_factor,
            "Dmax_cgy": None if item.get("max_dose") is None else item["max_dose"] * dose_factor,
            "Dmean_cgy": None if item.get("mean_dose") is None else item["mean_dose"] * dose_factor,
            "volume_units": item.get("volume_units", "CM3"),
        }
        for num in item.get("roi_numbers", []) or [None]:
            name = "ROI_%s" % num
            if rtstruct is not None and num is not None:
                try:
                    name = rtstruct.roi_name(num)
                except KeyError:
                    pass
            out[name] = dict(rec)
    return out


_UNIT_RE = re.compile(r"[\[\(]\s*([a-zA-Z%³3]+)\s*[\]\)]")


def _unit_in_header(text):
    m = _UNIT_RE.search(str(text))
    return m.group(1).lower() if m else ""


def _to_float(s):
    try:
        return float(str(s).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_mim_dvh_export(path, roi_col=None, volume_col=None, d90_col=None,
                         dose_units=None, volume_units=None, delimiter=None,
                         encoding="utf-8-sig", layout="auto") -> Dict[str, Dict[str, float]]:
    """Tolerant reader for a TPS (MIM) DVH / plan-statistics export.

    The real export format is unknown until the file arrives, so this reader
    accepts three layouts (``layout="auto"`` picks the first that fits):

    ``"wide"`` -- one row per structure, columns for the structure name, the
    volume and D90, e.g.::

        Structure,Volume (cc),D90 (Gy),V100 (%)
        CTV_GT,19.02,63.65,94.1

    ``"long"`` -- one row per (structure, statistic, value)::

        Structure\\tMetric\\tValue
        CTV_GT\\tVolume [cc]\\t19.02
        CTV_GT\\tD90 [Gy]\\t63.65

    ``"curves"`` -- a cumulative DVH table: a dose column followed by one
    volume column per structure (header = structure name, optional unit)::

        Dose (Gy),CTV_GT (cc),Cav_Post (cc)
        0,19.02,35.1
        10,19.02,35.1
        ...

    Any number of preamble lines (patient/plan header) before the column
    header are skipped; the header row is the first row whose cells match
    the structure-name column (``roi_col``, default pattern
    ``structure|roi|contour|name``), the volume column (``volume_col``,
    default pattern ``vol``) and the D90 column (``d90_col``, default pattern
    ``d\\s*90``).  Units are read from the header text in brackets
    (``(Gy)``, ``[cGy]``, ``(cc)``, ``(cm3)``, ``(ml)``, ``(%)``) and can be
    forced with ``dose_units`` (``"GY"``/``"CGY"``) and ``volume_units``
    (``"CC"``/``"PERCENT"``).  Defaults when no unit is written: dose in Gy,
    volume in cc (a percent volume gives ``volume_cc = nan``).  The delimiter
    is sniffed (comma, tab, semicolon) unless given.  Returns
    ``{structure: {"volume_cc": ..., "D90_cgy": ...}}``.
    """
    with open(str(path), "r", encoding=encoding, errors="replace") as fh:
        text = fh.read()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty DVH export: %s" % path)
    if delimiter is None:
        counts = {d: sum(ln.count(d) for ln in lines) for d in (",", "\t", ";")}
        delimiter = max(counts, key=counts.get)
        if counts[delimiter] == 0:
            raise ValueError("could not detect a delimiter (comma/tab/semicolon) in %s" % path)
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    rows = [[c.strip() for c in r] for r in rows if any(c.strip() for c in r)]

    roi_pat = re.compile(roi_col if roi_col else r"structure|roi|contour|name", re.I)
    vol_pat = re.compile(volume_col if volume_col else r"vol", re.I)
    d90_pat = re.compile(d90_col if d90_col else r"d\s*90", re.I)

    def match_col(pat, header, explicit):
        for i, cell in enumerate(header):
            if explicit and cell.strip().lower() == explicit.strip().lower():
                return i
        if explicit:
            for i, cell in enumerate(header):
                if re.search(explicit, cell, re.I):
                    return i
            return None
        for i, cell in enumerate(header):
            if pat.search(cell):
                return i
        return None

    def dose_to_cgy(value, header_text):
        if value is None:
            return None
        u = (dose_units or _unit_in_header(header_text) or "gy").lower()
        if u in ("cgy",):
            return value
        if u in ("gy", "%", "percent"):
            return value * 100.0 if u == "gy" else value
        return value * 100.0

    def volume_to_cc(value, header_text):
        if value is None:
            return None
        u = (volume_units or _unit_in_header(header_text) or "cc").lower()
        if u in ("cc", "cm3", "cm³", "ml"):
            return value
        if u in ("%", "percent"):
            return float("nan")
        return value

    out: Dict[str, Dict[str, float]] = {}

    def try_wide():
        for h_idx, header in enumerate(rows):
            ri = match_col(roi_pat, header, roi_col)
            vi = match_col(vol_pat, header, volume_col)
            di = match_col(d90_pat, header, d90_col)
            if ri is None or vi is None or di is None or len({ri, vi, di}) < 3:
                continue
            res = {}
            for r in rows[h_idx + 1:]:
                if len(r) <= max(ri, vi, di) or not r[ri]:
                    continue
                v, d = _to_float(r[vi]), _to_float(r[di])
                if v is None and d is None:
                    continue
                res[r[ri]] = {"volume_cc": volume_to_cc(v, header[vi]),
                              "D90_cgy": dose_to_cgy(d, header[di])}
            if res:
                return res
        return None

    def try_long():
        for h_idx, header in enumerate(rows):
            ri = match_col(roi_pat, header, roi_col)
            mi = next((i for i, c in enumerate(header) if re.search(r"metric|statistic|parameter|quantity", c, re.I)), None)
            vi = next((i for i, c in enumerate(header) if re.fullmatch(r"value|result", c.strip(), re.I)), None)
            if ri is None or mi is None or vi is None:
                continue
            res: Dict[str, Dict[str, float]] = {}
            for r in rows[h_idx + 1:]:
                if len(r) <= max(ri, mi, vi) or not r[ri]:
                    continue
                val = _to_float(r[vi])
                metric = r[mi]
                rec = res.setdefault(r[ri], {"volume_cc": None, "D90_cgy": None})
                if vol_pat.search(metric):
                    rec["volume_cc"] = volume_to_cc(val, metric)
                elif d90_pat.search(metric):
                    rec["D90_cgy"] = dose_to_cgy(val, metric)
            if res:
                return res
        return None

    def try_curves():
        for h_idx, header in enumerate(rows):
            if len(header) < 2 or not re.search(r"dose", header[0], re.I):
                continue
            body = [r for r in rows[h_idx + 1:] if len(r) >= len(header)]
            num = [[_to_float(c) for c in r[:len(header)]] for r in body]
            num = [r for r in num if r[0] is not None]
            if len(num) < 2:
                continue
            arr = np.array([[np.nan if v is None else v for v in r] for r in num], dtype=float)
            order = np.argsort(arr[:, 0])
            arr = arr[order]
            dose = np.array([dose_to_cgy(float(x), header[0]) for x in arr[:, 0]])
            res = {}
            for ci in range(1, len(header)):
                col = arr[:, ci]
                good = np.isfinite(col)
                if good.sum() < 2:
                    continue
                name = re.sub(r"\s*[\[\(].*?[\]\)]\s*$", "", header[ci]).strip()
                vols = col[good]
                res[name] = {"volume_cc": volume_to_cc(float(vols[0]), header[ci]),
                             "D90_cgy": _d_from_cumulative(dose[good], vols, 0.9)}
            if res:
                return res
        return None

    attempts = {"wide": try_wide, "long": try_long, "curves": try_curves}
    if layout != "auto":
        if layout not in attempts:
            raise ValueError("layout must be auto/wide/long/curves")
        out = attempts[layout]() or {}
    else:
        for fn in attempts.values():
            out = fn() or {}
            if out:
                break
    if not out:
        raise ValueError(
            "could not find structure / volume / D90 columns in %s; pass roi_col, "
            "volume_col, d90_col or layout explicitly" % path)
    return out


def verify_against_tps(rtdose: Volume, rtstruct: RTStruct, reference=None,
                       rois=("Cav_Post", "CTV_GT", "CTV_Post", "Brain"),
                       rx_cgy=6000.0, tolerance=0.02, supersample=4,
                       min_fraction=0.5) -> List[Dict[str, Any]]:
    """Rasterise each ROI on the **RTDOSE grid** (the TPS's own grid), take
    the RTDOSE voxel values inside it, and compare volume and D90 with the
    TPS's own numbers.

    ``reference`` is a dict ``{roi: {"volume_cc", "D90_cgy"}}`` (from
    :func:`reference_from_dvh_sequence` or :func:`parse_mim_dvh_export`), a
    path to an export file, a Volume/Dataset carrying a ``DVHSequence``, or
    ``None`` to use ``rtdose.meta["dvh_sequence"]`` (raises when absent).
    ROI names match case- and whitespace-insensitively.  ``rois=None`` means
    every ROI present in the reference.

    One row per ROI: ``roi, vol_mim, vol_gtcore, vol_rel_err, D90_mim,
    D90_gtcore, D90_rel_err, V100_gtcore, n_voxels, pass``; ``pass`` is True
    when both relative errors are finite and ``<= tolerance`` (2 %).
    """
    if reference is None:
        if not rtdose.meta.get("dvh_sequence"):
            raise ValueError("RTDOSE has no DVHSequence; pass a MIM DVH/plan export "
                             "(parse_mim_dvh_export) as reference")
        reference = reference_from_dvh_sequence(rtdose, rtstruct)
    elif isinstance(reference, (str, os.PathLike)):
        reference = parse_mim_dvh_export(reference)
    elif isinstance(reference, Volume) or not isinstance(reference, dict):
        reference = reference_from_dvh_sequence(reference, rtstruct)
    ref_norm = {_norm_name(k): (k, v) for k, v in reference.items()}

    if rois is None:
        rois = [k for k, _ in ref_norm.values()]
    vvol = float(np.prod(rtdose.spacing))
    arr = np.asarray(rtdose.array, dtype=float)
    rows = []
    for roi in rois:
        key = _norm_name(roi)
        ref = ref_norm.get(key, (roi, {}))[1]
        vol_ref = ref.get("volume_cc")
        d90_ref = ref.get("D90_cgy")
        try:
            mask = rasterize_roi(rtstruct, roi, rtdose.affine, arr.shape,
                                 supersample=supersample, min_fraction=min_fraction)
        except KeyError:
            warnings.warn("verify_against_tps: ROI %r not in RTSTRUCT" % roi)
            mask = None
        if mask is None:
            st = {"volume_cc": float("nan"), "D90": float("nan"), "V100": float("nan"), "n": 0.0}
        else:
            st = _dose_stats(arr[mask], rx_cgy, vvol)

        def rel(a, b):
            if a is None or b is None or not np.isfinite(a) or not np.isfinite(b) or a == 0:
                return float("nan")
            return float(abs(b - a) / abs(a))

        v_err = rel(vol_ref, st["volume_cc"])
        d_err = rel(d90_ref, st["D90"])
        rows.append({
            "roi": roi,
            "vol_mim": float("nan") if vol_ref is None else float(vol_ref),
            "vol_gtcore": float(st["volume_cc"]),
            "vol_rel_err": v_err,
            "D90_mim": float("nan") if d90_ref is None else float(d90_ref),
            "D90_gtcore": float(st["D90"]),
            "D90_rel_err": d_err,
            "V100_gtcore": float(st["V100"]),
            "n_voxels": int(st.get("n", 0) or 0),
            "pass": bool(np.isfinite(v_err) and np.isfinite(d_err)
                         and v_err <= tolerance and d_err <= tolerance),
        })
    return rows


# -------------------------------------------------------- reference checks
def _ct_identifiers(ct):
    """(frame_of_reference_uid, series_instance_uid) of a CT given as a
    Volume (from ``load_dicom_series``), a directory, a file or a Dataset."""
    if ct is None:
        return None, None
    if isinstance(ct, Volume):
        m = ct.meta
        return m.get("frame_of_reference_uid"), m.get("series_instance_uid") or m.get("series_id")
    if isinstance(ct, (str, os.PathLike)):
        p = str(ct)
        if os.path.isdir(p):
            files = [os.path.join(p, n) for n in sorted(os.listdir(p))]
            files = [f for f in files if os.path.isfile(f)]
            if not files:
                return None, None
            p = files[0]
        ds = _dcmread(p, stop_before_pixels=True)
    else:
        ds = ct
    return _str(ds, "FrameOfReferenceUID", None), _str(ds, "SeriesInstanceUID", None)


def check_references(rtstruct: Optional[RTStruct] = None, ct=None, rtdose=None,
                     rtplan: Optional[RTPlan] = None) -> List[str]:
    """Consistency of the frame-of-reference / referenced-series links between
    an RTSTRUCT, the CT it was drawn on, an RTDOSE and an RTPLAN.

    Returns a list of warning strings (empty = everything that could be
    checked agrees).  The strings name the mismatching pair but never contain
    a UID, so they can be printed into notes; nothing is written to disk.
    """
    warns: List[str] = []
    ct_for, ct_series = _ct_identifiers(ct)
    rs_for = rtstruct.frame_of_reference_uid if rtstruct is not None else None
    rs_series = rtstruct.referenced_series_uid if rtstruct is not None else None
    rd_for = rtdose.meta.get("frame_of_reference_uid") if isinstance(rtdose, Volume) else (
        _str(rtdose, "FrameOfReferenceUID", None) if rtdose is not None else None)
    rp_for = rtplan.frame_of_reference_uid if rtplan is not None else None

    if rtstruct is not None and ct is not None:
        if rs_for and ct_for:
            if rs_for != ct_for:
                warns.append("RTSTRUCT frame of reference differs from the CT's")
        else:
            warns.append("cannot compare RTSTRUCT and CT frames of reference (one is missing)")
        if rs_series and ct_series:
            if rs_series != ct_series:
                warns.append("RTSTRUCT references a different CT series than the one loaded")
        else:
            warns.append("cannot compare the RTSTRUCT referenced series with the CT (one is missing)")
    if rtstruct is not None and rtdose is not None:
        if rs_for and rd_for:
            if rs_for != rd_for:
                warns.append("RTDOSE frame of reference differs from the RTSTRUCT's")
        else:
            warns.append("cannot compare RTDOSE and RTSTRUCT frames of reference (one is missing)")
        rd_ss = rtdose.meta.get("referenced_structure_set_uid") if isinstance(rtdose, Volume) else None
        rs_sop = rtstruct.meta.get("sop_instance_uid")
        if rd_ss and rs_sop and rd_ss != rs_sop:
            warns.append("RTDOSE references a different structure set than the RTSTRUCT loaded")
    if ct is not None and rtdose is not None and rtstruct is None:
        if ct_for and rd_for and ct_for != rd_for:
            warns.append("RTDOSE frame of reference differs from the CT's")
    if rtplan is not None:
        if rp_for and rs_for and rp_for != rs_for:
            warns.append("RTPLAN frame of reference differs from the RTSTRUCT's")
        if rp_for and rd_for and rp_for != rd_for:
            warns.append("RTPLAN frame of reference differs from the RTDOSE's")
        if rp_for and ct_for and rp_for != ct_for:
            warns.append("RTPLAN frame of reference differs from the CT's")
        rp_ss = rtplan.referenced_structure_set_uid
        rs_sop = rtstruct.meta.get("sop_instance_uid") if rtstruct is not None else None
        if rp_ss and rs_sop and rp_ss != rs_sop:
            warns.append("RTPLAN references a different structure set than the RTSTRUCT loaded")
        if isinstance(rtdose, Volume):
            rd_plan = rtdose.meta.get("referenced_plan_uid")
            rp_sop = rtplan.meta.get("sop_instance_uid")
            if rd_plan and rp_sop and rd_plan != rp_sop:
                warns.append("RTDOSE references a different plan than the RTPLAN loaded")
    return warns
