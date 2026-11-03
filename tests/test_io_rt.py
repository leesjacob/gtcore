"""gtcore.io.rt on synthetic RT DICOM written with pydicom into tmp_path.

Covers the rasterisation rule (volume on several grids, end-slice rule,
holes, POINT/empty ROIs), the RTDOSE loader (units, scaling, frame offsets,
affine, DoseSummationType), the RTPLAN reader, verify_against_tps with a
reference generated from the same synthetic data (pass) and perturbed
(fail), the DVH export parser and check_references.
"""
from __future__ import annotations

import os
import warnings
from datetime import datetime

import numpy as np
import pytest

from gtcore.io import rt
from gtcore.volume import Volume

pydicom = pytest.importorskip("pydicom")
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset  # noqa: E402
from pydicom.sequence import Sequence  # noqa: E402
from pydicom.uid import ExplicitVRLittleEndian, generate_uid  # noqa: E402

FOR_UID = "1.2.826.0.1.3680043.8.498.1"
SERIES_UID = "1.2.826.0.1.3680043.8.498.2"


# ------------------------------------------------------------------ writers
def _file_dataset(path, sop_class, modality):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = sop_class
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Modality = modality
    ds.PatientName = "SYNTH^RT"
    ds.PatientID = "synthetic"
    ds.Manufacturer = "gtcore-tests"
    return ds


def write_rtstruct(path, rois, for_uid=FOR_UID, series_uid=SERIES_UID):
    """``rois``: list of (name, contours) with contours a list of
    (geometric_type, (N, 3) LPS points)."""
    ds = _file_dataset(path, rt._RTSTRUCT_SOP, "RTSTRUCT")
    ds.StructureSetLabel = "synthetic"
    fr = Dataset()
    fr.FrameOfReferenceUID = for_uid
    st = Dataset()
    st.ReferencedSOPClassUID = "1.2.840.10008.3.1.2.3.1"
    st.ReferencedSOPInstanceUID = generate_uid()
    se = Dataset()
    se.SeriesInstanceUID = series_uid
    se.ContourImageSequence = Sequence([])
    st.RTReferencedSeriesSequence = Sequence([se])
    fr.RTReferencedStudySequence = Sequence([st])
    ds.ReferencedFrameOfReferenceSequence = Sequence([fr])
    ssr, rcs, obs = [], [], []
    for num, (name, contours) in enumerate(rois, start=1):
        r = Dataset()
        r.ROINumber = num
        r.ROIName = name
        r.ReferencedFrameOfReferenceUID = for_uid
        r.ROIGenerationAlgorithm = "MANUAL"
        ssr.append(r)
        rc = Dataset()
        rc.ReferencedROINumber = num
        rc.ROIDisplayColor = [255, 0, 0]
        items = []
        for gtype, pts in contours:
            c = Dataset()
            c.ContourGeometricType = gtype
            p = np.asarray(pts, dtype=float).reshape(-1, 3)
            c.NumberOfContourPoints = p.shape[0]
            c.ContourData = [float("%.5f" % v) for v in p.ravel()]
            items.append(c)
        if items:
            rc.ContourSequence = Sequence(items)
        rcs.append(rc)
        o = Dataset()
        o.ObservationNumber = num
        o.ReferencedROINumber = num
        o.RTROIInterpretedType = ""
        o.ROIInterpreter = ""
        obs.append(o)
    ds.StructureSetROISequence = Sequence(ssr)
    ds.ROIContourSequence = Sequence(rcs)
    ds.RTROIObservationsSequence = Sequence(obs)
    ds.save_as(str(path), enforce_file_format=True)
    return str(path)


def write_rtdose(path, dose_cgy, ipp_lps, pixel_spacing, dz, units="GY",
                 scaling=0.02, summation="PLAN", for_uid=FOR_UID,
                 gfov=None, dvh_items=None, iop=(1, 0, 0, 0, 1, 0)):
    """``dose_cgy`` [k, j, i]; stored as uint32 with ``scaling`` in ``units``."""
    ds = _file_dataset(path, rt._RTDOSE_SOP, "RTDOSE")
    ds.FrameOfReferenceUID = for_uid
    ds.DoseUnits = units
    ds.DoseType = "PHYSICAL"
    ds.DoseSummationType = summation
    ds.DoseGridScaling = scaling
    factor = 100.0 if units == "GY" else 1.0
    stored = np.rint(np.asarray(dose_cgy, dtype=float) / factor / scaling).astype(np.uint32)
    nk, nj, ni = stored.shape
    ds.NumberOfFrames = nk
    ds.Rows = nj
    ds.Columns = ni
    ds.PixelSpacing = [float(pixel_spacing[0]), float(pixel_spacing[1])]  # row, col
    ds.ImagePositionPatient = [float(v) for v in ipp_lps]
    ds.ImageOrientationPatient = [float(v) for v in iop]
    ds.GridFrameOffsetVector = [float(v) for v in (gfov if gfov is not None else dz * np.arange(nk))]
    ds.SliceThickness = float(dz)
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 32
    ds.BitsStored = 32
    ds.HighBit = 31
    ds.PixelRepresentation = 0
    ds.FrameIncrementPointer = pydicom.tag.Tag(0x3004, 0x000C)
    ds.PixelData = stored.tobytes()
    rp = Dataset()
    rp.ReferencedSOPClassUID = rt._RTPLAN_SOP
    rp.ReferencedSOPInstanceUID = "1.2.826.0.1.3680043.8.498.77"
    ds.ReferencedRTPlanSequence = Sequence([rp])
    if dvh_items:
        seq = []
        for roi_number, data, vol_units, dose_units, dtype in dvh_items:
            d = Dataset()
            d.DVHType = dtype
            d.DoseUnits = dose_units
            d.DoseType = "PHYSICAL"
            d.DVHDoseScaling = 1.0
            d.DVHVolumeUnits = vol_units
            d.DVHNumberOfBins = len(data) // 2
            d.DVHData = [float(v) for v in data]
            ref = Dataset()
            ref.ReferencedROINumber = roi_number
            ref.DVHROIContributionType = "INCLUDED"
            d.DVHReferencedROISequence = Sequence([ref])
            seq.append(d)
        ds.DVHSequence = Sequence(seq)
    ds.save_as(str(path), enforce_file_format=True)
    return str(path)


def write_rtplan(path, seeds, for_uid=FOR_UID, ref_date="20231026", ref_time="090812.000000"):
    """``seeds``: list of dicts with ``pos`` (LPS), ``orient`` (LPS) and ``sk`` (U)."""
    ds = _file_dataset(path, rt._RTPLAN_SOP, "RTPLAN")
    ds.FrameOfReferenceUID = for_uid
    ds.RTPlanLabel = "synth implant"
    ds.RTPlanDate = ref_date
    ds.RTPlanTime = ref_time
    ds.RTPlanGeometry = "PATIENT"
    ds.BrachyTreatmentTechnique = "PERMANENT"
    ds.BrachyTreatmentType = "LDR"
    ds.ApprovalStatus = "APPROVED"
    rss = Dataset()
    rss.ReferencedSOPClassUID = rt._RTSTRUCT_SOP
    rss.ReferencedSOPInstanceUID = "1.2.826.0.1.3680043.8.498.55"
    ds.ReferencedStructureSetSequence = Sequence([rss])
    dr = Dataset()
    dr.DoseReferenceNumber = 1
    dr.DoseReferenceStructureType = "VOLUME"
    dr.DoseReferenceType = "TARGET"
    dr.DoseReferenceDescription = "60 Gy at 5 mm"
    dr.TargetPrescriptionDose = 60.0
    ds.DoseReferenceSequence = Sequence([dr])
    src_items, channels = [], []
    for n, s in enumerate(seeds, start=1):
        src = Dataset()
        src.SourceNumber = n
        src.SourceType = "LINE"
        src.SourceManufacturer = "Synthetic Isotopes"
        src.SourceIsotopeName = "Cs-131"
        src.SourceIsotopeHalfLife = 9.689
        src.SourceModelID = "CS-1 Rev2"
        src.ActiveSourceLength = 4.0
        src.ActiveSourceDiameter = 0.6
        src.ReferenceAirKermaRate = float(s["sk"])
        src.SourceStrengthReferenceDate = ref_date
        src.SourceStrengthReferenceTime = ref_time
        src_items.append(src)
        ch = Dataset()
        ch.ChannelNumber = n
        ch.ChannelLength = 0.0
        ch.ChannelTotalTime = 0.0
        ch.SourceMovementType = "FIXED"
        ch.ReferencedSourceNumber = n
        ch.NumberOfControlPoints = 2
        cps = []
        for idx in range(2):
            cp = Dataset()
            cp.ControlPointIndex = idx
            cp.ControlPointRelativePosition = 0.0
            cp.ControlPoint3DPosition = [float(v) for v in s["pos"]]
            cp.CumulativeTimeWeight = float(idx)
            if s.get("orient") is not None:
                cp.ControlPointOrientation = [float(v) for v in s["orient"]]
            cps.append(cp)
        ch.BrachyControlPointSequence = Sequence(cps)
        channels.append(ch)
    ds.SourceSequence = Sequence(src_items)
    setup = Dataset()
    setup.ApplicationSetupType = "BRAIN"
    setup.ApplicationSetupNumber = 1
    setup.TotalReferenceAirKerma = float(sum(s["sk"] for s in seeds))
    setup.ChannelSequence = Sequence(channels)
    ds.ApplicationSetupSequence = Sequence([setup])
    ds.save_as(str(path), enforce_file_format=True)
    return str(path)


# ---------------------------------------------------------------- geometry
def circle_lps(cx, cy, z, r, n=128):
    t = np.linspace(0.0, 2.0 * np.pi, n, endpoint=False)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t), np.full(n, z)])


def square_lps(cx, cy, z, half):
    return np.array([[cx - half, cy - half, z], [cx + half, cy - half, z],
                     [cx + half, cy + half, z], [cx - half, cy + half, z]])


def sphere_contours(center_lps, radius, pitch=1.0, n=128):
    """Closed planar circles on planes at ``pitch`` spacing, planes centred
    on the sphere (so the end-slice slabs tile exactly [-R, R))."""
    cx, cy, cz = center_lps
    m = int(round(2 * radius / pitch))
    zs = cz + (np.arange(m) - (m - 1) / 2.0) * pitch
    out = []
    for z in zs:
        r2 = radius ** 2 - (z - cz) ** 2
        if r2 <= 0:
            continue
        out.append(("CLOSED_PLANAR", circle_lps(cx, cy, z, np.sqrt(r2), n)))
    return out


def axial_affine_ras(origin_ras, spacing):
    a = np.eye(4)
    a[0, 0], a[1, 1], a[2, 2] = spacing
    a[:3, 3] = origin_ras
    return a


def grid_around(center_ras, spacing, extent_mm):
    """Axis-aligned RAS grid covering center +- extent on every axis, with
    the grid centred so voxel centres do not fall on tie positions."""
    spacing = np.asarray(spacing, dtype=float)
    n = np.ceil(2 * extent_mm / spacing).astype(int) + 1
    origin = np.asarray(center_ras, dtype=float) - (n - 1) / 2.0 * spacing + 0.123 * spacing
    return axial_affine_ras(origin, spacing), (int(n[2]), int(n[1]), int(n[0]))


def mask_volume_cc(mask, affine):
    return mask.sum() * float(np.prod(np.linalg.norm(affine[:3, :3], axis=0))) / 1000.0


# ------------------------------------------------------------------ fixtures
R_SPHERE = 10.0
SPHERE_CENTER_LPS = np.array([12.0, -30.0, 40.0])
SPHERE_CENTER_RAS = SPHERE_CENTER_LPS * np.array([-1.0, -1.0, 1.0])


@pytest.fixture(scope="module")
def rtstruct_file(tmp_path_factory):
    d = tmp_path_factory.mktemp("rt")
    rois = [
        ("Sphere", sphere_contours(SPHERE_CENTER_LPS, R_SPHERE, pitch=1.0)),
        ("Slab", [("CLOSED_PLANAR", square_lps(0.0, 0.0, z, 10.0)) for z in range(5)]),
        ("Ring", [("CLOSED_PLANAR", square_lps(0.0, 0.0, 0.0, 10.0)),
                  ("CLOSED_PLANAR", square_lps(0.0, 0.0, 0.0, 5.0))]),
        ("Point-2", [("POINT", np.array([[1.0, 2.0, 3.0]]))]),
        ("Empty_PRV", []),
    ]
    return write_rtstruct(d / "rtstruct.dcm", rois)


@pytest.fixture(scope="module")
def rtstruct(rtstruct_file):
    return rt.load_rtstruct(rtstruct_file)


# ------------------------------------------------------------- (a) volumes
def test_load_rtstruct_basics(rtstruct):
    assert rtstruct.roi_names == ["Sphere", "Slab", "Ring", "Point-2", "Empty_PRV"]
    assert rtstruct.roi_number("Slab") == 2
    assert rtstruct.frame_of_reference_uid == FOR_UID
    assert rtstruct.referenced_series_uid == SERIES_UID
    assert rtstruct.n_contours("Sphere") == 20
    assert rtstruct.contour_z_pitch_mm("Sphere") == pytest.approx(1.0)
    assert np.isnan(rtstruct.contour_z_pitch_mm("Ring"))
    assert rtstruct.contour_types("Point-2") == ["POINT"]
    assert rtstruct.contours("Point-2") == []                       # closed planar only
    assert len(rtstruct.contours("Point-2", geometric_type=None)) == 1
    # LPS -> RAS: x and y negated, z kept
    c = rtstruct.contours("Sphere")[0]
    np.testing.assert_allclose(c[:, 2], SPHERE_CENTER_RAS[2] - 9.5, atol=1e-6)
    assert np.allclose(c[:, :2].mean(axis=0), SPHERE_CENTER_RAS[:2], atol=1e-6)
    assert "Sphere" in rtstruct.describe()


@pytest.mark.parametrize("spacing", [(0.5, 0.5, 1.0), (1.0, 1.0, 5.0 / 3.0), (0.7, 0.7, 0.7)])
def test_sphere_volume_within_2_percent(rtstruct, spacing):
    affine, shape = grid_around(SPHERE_CENTER_RAS, spacing, R_SPHERE + 3.0)
    mask = rt.rasterize_roi(rtstruct, "Sphere", affine, shape)
    analytic = 4.0 / 3.0 * np.pi * R_SPHERE ** 3 / 1000.0
    got = mask_volume_cc(mask, affine)
    assert abs(got - analytic) / analytic < 0.02, (spacing, got, analytic)
    # the mask is confined to the sphere's z extent [-R, R) +- half a voxel
    kk = np.flatnonzero(mask.any(axis=(1, 2)))
    z = affine[2, 2] * kk + affine[2, 3]
    assert z.min() >= SPHERE_CENTER_RAS[2] - R_SPHERE - 0.5 * spacing[2] - 1e-9
    assert z.max() <= SPHERE_CENTER_RAS[2] + R_SPHERE + 0.5 * spacing[2] + 1e-9


def test_supersample_one_is_centre_rule_and_matches_skimage(rtstruct):
    skdraw = pytest.importorskip("skimage.draw")
    affine, shape = grid_around(SPHERE_CENTER_RAS, (0.8, 0.8, 1.0), R_SPHERE + 2.0)
    mask = rt.rasterize_roi(rtstruct, "Sphere", affine, shape, supersample=1)
    inv = np.linalg.inv(affine)
    nk, nj, ni = shape
    # reproduce one claimed slice with skimage's centre-in-polygon fill
    k = nk // 2
    zc = affine[2, 2] * k + affine[2, 3]
    planes = rtstruct.contour_planes_z("Sphere")
    pz = planes[np.argmin(np.abs(planes - zc))]
    poly = [c for c in rtstruct.contours("Sphere") if abs(c[0, 2] - pz) < 1e-6][0]
    ij = (np.c_[poly, np.ones(len(poly))] @ inv.T)[:, :2]
    rr, cc = skdraw.polygon(ij[:, 1], ij[:, 0], (nj, ni))
    ref = np.zeros((nj, ni), bool)
    ref[rr, cc] = True
    assert (mask[k] ^ ref).sum() <= 2       # boundary-sitting centres only


def test_evenodd_fill_matches_skimage_on_random_polygons():
    skdraw = pytest.importorskip("skimage.draw")
    rng = np.random.default_rng(7)
    for _ in range(5):
        n = int(rng.integers(5, 40))
        ang = np.sort(rng.uniform(0, 2 * np.pi, n))
        rad = rng.uniform(5, 20, n)
        poly = np.column_stack([25 + rad * np.cos(ang), 22 + rad * np.sin(ang)]) + rng.uniform(-0.3, 0.3, (n, 2))
        frac = rt._coverage_fraction([poly], 50, 55, 1)
        rr, cc = skdraw.polygon(poly[:, 1], poly[:, 0], (50, 55))
        ref = np.zeros((50, 55), bool)
        ref[rr, cc] = True
        assert ((frac > 0.5) ^ ref).sum() <= 2
    # XOR of two polygons == even-odd fill of both (edges off the sub-pixel lattice)
    a = np.array([[2.3, 2.3], [30.3, 2.3], [30.3, 30.3], [2.3, 30.3]])
    b = np.array([[10.3, 10.3], [20.3, 10.3], [20.3, 20.3], [10.3, 20.3]])
    fa = rt._coverage_fraction([a], 40, 40, 1) > 0.5
    fb = rt._coverage_fraction([b], 40, 40, 1) > 0.5
    fab = rt._coverage_fraction([a, b], 40, 40, 1) > 0.5
    assert np.array_equal(fab, fa ^ fb)


# ------------------------------------------------------ (b) end-slice rule
@pytest.mark.parametrize("dz,offset", [(0.5, 0.0), (1.0, 0.0), (1.0, 0.5), (0.3, 0.1), (5.0 / 3.0, 0.2)])
def test_end_slice_rule_slab_thickness(rtstruct, dz, offset):
    k_planes, pitch = 5, 1.0
    nk = int(np.ceil(16.0 / dz))
    affine = axial_affine_ras((-14.125, -14.125, -5.0 + offset), (0.5, 0.5, dz))
    mask = rt.rasterize_roi(rtstruct, "Slab", affine, (nk, 56, 56))
    kk = np.flatnonzero(mask.any(axis=(1, 2)))
    assert kk.size > 0
    thickness = kk.size * dz
    assert abs(thickness - k_planes * pitch) <= 0.5 * pitch + 1e-9, (dz, offset, thickness)
    z = affine[2, 2] * kk + affine[2, 3]
    # nothing beyond half a pitch of the first/last plane (planes at z = 0..4)
    assert z.min() >= 0.0 - 0.5 * pitch - 1e-9
    assert z.max() < 4.0 + 0.5 * pitch + 1e-9
    # claimed slices are contiguous
    assert np.array_equal(kk, np.arange(kk[0], kk[-1] + 1))
    # in-plane the 20 x 20 mm square is 400 mm2 per slice (exact: edges fall
    # between sub-pixel centres on this grid)
    per_slice = mask[kk[0]].sum() * 0.25
    assert per_slice == pytest.approx(400.0)


def test_end_slice_exact_tie_is_half_open(rtstruct):
    """Voxel centres exactly half a pitch from two planes: a slab on k planes
    is exactly k pitches thick, never k + 1."""
    affine = axial_affine_ras((-14.0, -14.0, -5.5), (0.5, 0.5, 1.0))   # centres at -5.5, ..., 0.5 tie
    mask = rt.rasterize_roi(rtstruct, "Slab", affine, (20, 56, 56))
    kk = np.flatnonzero(mask.any(axis=(1, 2)))
    assert kk.size == 5
    z = affine[2, 2] * kk + affine[2, 3]
    np.testing.assert_allclose(z, [-0.5, 0.5, 1.5, 2.5, 3.5])


def test_single_plane_uses_grid_spacing_with_warning(rtstruct):
    affine = axial_affine_ras((-14.0, -14.0, -3.0), (0.5, 0.5, 1.0))
    with pytest.warns(UserWarning, match="single contour plane"):
        mask = rt.rasterize_roi(rtstruct, "Ring", affine, (8, 56, 56))
    kk = np.flatnonzero(mask.any(axis=(1, 2)))
    assert kk.size == 1 and abs(affine[2, 2] * kk[0] + affine[2, 3]) < 1e-9
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        mask2 = rt.rasterize_roi(rtstruct, "Ring", affine, (8, 56, 56), pitch_mm=1.0)
    assert np.array_equal(mask, mask2)


# ------------------------------------------------------------- (c) holes
def test_hole_rasterises_as_ring(rtstruct):
    affine = axial_affine_ras((-14.125, -14.125, 0.0), (0.5, 0.5, 1.0))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mask = rt.rasterize_roi(rtstruct, "Ring", affine, (1, 56, 56), pitch_mm=1.0)
    area = mask[0].sum() * 0.25
    assert area == pytest.approx(300.0)             # 20^2 - 10^2, exact on this grid
    inv = np.linalg.inv(affine)

    def at(x, y):
        i, j = (inv @ np.array([x, y, 0.0, 1.0]))[:2]
        return mask[0, int(round(j)), int(round(i))]

    assert not at(0.0, 0.0)          # centre is the hole
    assert at(7.5, 0.0) and at(0.0, -7.5)
    assert not at(12.0, 0.0)


def test_edge_through_voxel_centre_is_half_covered(rtstruct):
    """A polygon edge through a voxel centre covers exactly half its
    sub-pixels, so with min_fraction 0.5 the voxel is in (>=) and with 0.51
    it is out: the tie rule is deterministic."""
    affine = axial_affine_ras((-14.0, -14.0, 0.0), (0.5, 0.5, 1.0))   # edges at +-10 on voxel centres
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        m_in = rt.rasterize_roi(rtstruct, "Ring", affine, (1, 56, 56), pitch_mm=1.0)
        m_out = rt.rasterize_roi(rtstruct, "Ring", affine, (1, 56, 56), pitch_mm=1.0, min_fraction=0.51)
    # >= 0.5: outer 41^2 - 4 corners (1/4 covered), hole 19^2 (edge voxels are half ring)
    assert m_in[0].sum() * 0.25 == pytest.approx(329.0)
    # >= 0.51: outer 39^2, hole 21^2 - 4 (hole corners are 3/4 ring, so in)
    assert m_out[0].sum() * 0.25 == pytest.approx(271.0)


def test_min_fraction_and_supersample_validation(rtstruct):
    affine = axial_affine_ras((-14.0, -14.0, -1.0), (0.5, 0.5, 1.0))
    with pytest.raises(ValueError):
        rt.rasterize_roi(rtstruct, "Slab", affine, (8, 56, 56), supersample=0)
    with pytest.raises(ValueError):
        rt.rasterize_roi(rtstruct, "Slab", affine, (8, 56, 56), min_fraction=0.0)
    with pytest.raises(ValueError):
        rt.rasterize_roi(rtstruct, "Slab", affine, (8, 56, 56), slice_rule="round")
    # a boundary-straddling grid: a lower min_fraction can only add voxels
    affine2 = axial_affine_ras((-14.3, -14.3, -1.0), (0.7, 0.7, 1.0))
    strict = rt.rasterize_roi(rtstruct, "Slab", affine2, (8, 40, 40), min_fraction=0.9)
    loose = rt.rasterize_roi(rtstruct, "Slab", affine2, (8, 40, 40), min_fraction=0.1)
    assert loose.sum() > strict.sum() and not (strict & ~loose).any()


# --------------------------------------------------- (d) POINT / empty ROIs
def test_point_and_empty_rois_warn_not_raise(rtstruct):
    affine = axial_affine_ras((-14.0, -14.0, -1.0), (0.5, 0.5, 1.0))
    with pytest.warns(UserWarning, match="POINT"):
        m = rt.rasterize_roi(rtstruct, "Point-2", affine, (8, 56, 56))
    assert m.shape == (8, 56, 56) and not m.any()
    with pytest.warns(UserWarning, match="no closed planar contours"):
        m = rt.rasterize_roi(rtstruct, "Empty_PRV", affine, (8, 56, 56))
    assert not m.any()
    with pytest.raises(KeyError):
        rt.rasterize_roi(rtstruct, "Nope", affine, (8, 56, 56))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        vol = Volume(np.zeros((8, 56, 56), np.float32), affine)
        out = rt.rasterize_rois(rtstruct, ["Slab", "Point-2"], vol)
    assert set(out) == {"Slab", "Point-2"} and out["Slab"].any() and not out["Point-2"].any()


# ------------------------------------------------------------- (e) RTDOSE
def _dose_field(shape, affine, center_ras, d0=12000.0, r0=12.0):
    nk, nj, ni = shape
    kk, jj, ii = np.meshgrid(np.arange(nk), np.arange(nj), np.arange(ni), indexing="ij")
    ijk = np.stack([ii.ravel(), jj.ravel(), kk.ravel(), np.ones(ii.size)], axis=1).astype(float)
    ras = (ijk @ affine.T)[:, :3]
    r = np.linalg.norm(ras - center_ras, axis=1)
    return (d0 * np.exp(-r / r0)).reshape(shape)


def test_rtdose_units_scaling_affine(tmp_path):
    ipp = np.array([-40.0, -55.0, 20.0])
    ps, dz = (1.0, 1.0), 5.0 / 3.0
    affine_lps = np.eye(4)
    affine_lps[0, 0], affine_lps[1, 1], affine_lps[2, 2] = ps[1], ps[0], dz
    affine_lps[:3, 3] = ipp
    affine_ras = np.diag([-1.0, -1.0, 1.0, 1.0]) @ affine_lps
    shape = (6, 7, 8)
    dose = np.round(_dose_field(shape, affine_ras, np.array([30.0, 50.0, 25.0]))) // 2 * 2  # multiples of 2 cGy
    f = write_rtdose(tmp_path / "gy.dcm", dose, ipp, ps, dz, units="GY", scaling=0.02)
    vol = rt.load_rtdose(f)
    assert vol.array.shape == shape
    np.testing.assert_allclose(vol.array, dose, atol=1e-6)            # GY -> cGy, scaling applied
    assert vol.meta["units"] == "cGy" and vol.meta["dose_units_original"] == "GY"
    assert vol.meta["dose_summation_type"] == "PLAN"
    assert vol.meta["frame_of_reference_uid"] == FOR_UID
    assert vol.meta["referenced_plan_uid"] == "1.2.826.0.1.3680043.8.498.77"
    assert vol.meta["dose_grid_scaling"] == pytest.approx(0.02)
    # affine: voxel (i, j, k) = (3, 5, 2) -> LPS (ipp + (3*ps1, 5*ps0, 2*dz)) -> RAS
    expect_lps = ipp + np.array([3 * ps[1], 5 * ps[0], 2 * dz])
    expect_ras = expect_lps * np.array([-1.0, -1.0, 1.0])
    np.testing.assert_allclose(vol.index_to_ras([3, 5, 2]), expect_ras, atol=1e-9)
    np.testing.assert_allclose(vol.spacing, [1.0, 1.0, dz], atol=1e-9)
    # CGY unchanged
    f2 = write_rtdose(tmp_path / "cgy.dcm", dose, ipp, ps, dz, units="CGY", scaling=0.5)
    np.testing.assert_allclose(rt.load_rtdose(f2).array, dose, atol=1e-6)


def test_rtdose_rejects_bad_units_offsets_and_summation(tmp_path):
    dose = np.full((3, 4, 5), 100.0)
    f = write_rtdose(tmp_path / "rel.dcm", dose, [0, 0, 0], (1, 1), 1.0, units="RELATIVE")
    with pytest.raises(ValueError, match="DoseUnits"):
        rt.load_rtdose(f)
    f = write_rtdose(tmp_path / "nonuni.dcm", dose, [0, 0, 0], (1, 1), 1.0, gfov=[0.0, 1.0, 3.0])
    with pytest.raises(ValueError, match="non-uniform"):
        rt.load_rtdose(f)
    f = write_rtdose(tmp_path / "frac.dcm", dose, [0, 0, 0], (1, 1), 1.0, summation="FRACTION")
    with pytest.raises(ValueError, match="DoseSummationType"):
        rt.load_rtdose(f)
    vol = rt.load_rtdose(f, allow_any=True)
    assert vol.meta["dose_summation_type"] == "FRACTION"
    for ok in ("MULTI_PLAN", "BRACHY"):
        f = write_rtdose(tmp_path / ("%s.dcm" % ok), dose, [0, 0, 0], (1, 1), 1.0, summation=ok)
        assert rt.load_rtdose(f).meta["dose_summation_type"] == ok
    # directory with one RTDOSE works; directory without raises
    d = tmp_path / "onedose"
    d.mkdir()
    write_rtdose(d / "x.dcm", dose, [0, 0, 0], (1, 1), 1.0)
    assert rt.load_rtdose(d).array.shape == (3, 4, 5)
    with pytest.raises(FileNotFoundError):
        rt.load_rtstruct(d)


def test_rtdose_negative_frame_direction(tmp_path):
    dose = np.zeros((4, 3, 3))
    dose[0] = 1.0
    f = write_rtdose(tmp_path / "neg.dcm", dose, [0, 0, 10.0], (1, 1), -2.0, units="CGY", scaling=1.0,
                     gfov=[0.0, -2.0, -4.0, -6.0])
    vol = rt.load_rtdose(f)
    assert vol.index_to_ras([0, 0, 3])[2] == pytest.approx(4.0)
    assert vol.index_to_ras([0, 0, 0])[2] == pytest.approx(10.0)


# ------------------------------------------------------------- (f) RTPLAN
def test_rtplan_roundtrip(tmp_path):
    seeds = [
        {"pos": (10.0, -20.0, 30.0), "orient": (1.0, 0.0, 0.0), "sk": 3.52},
        {"pos": (12.5, -18.0, 31.0), "orient": (0.0, 0.6, 0.8), "sk": 3.48},
        {"pos": (-5.0, 7.0, 29.0), "orient": None, "sk": 3.50},
    ]
    f = write_rtplan(tmp_path / "plan.dcm", seeds)
    plan = rt.load_rtplan(f)
    assert plan.n_sources == 3
    pos = np.array([s["pos"] for s in seeds]) * np.array([-1.0, -1.0, 1.0])
    np.testing.assert_allclose(plan.positions_ras, pos, atol=1e-6)
    np.testing.assert_allclose(plan.sources[0].orientation_ras, [-1.0, 0.0, 0.0], atol=1e-6)
    np.testing.assert_allclose(plan.sources[1].orientation_ras, [0.0, -0.6, 0.8], atol=1e-6)
    assert plan.sources[2].orientation_ras is None       # identical control points, no orientation tag
    assert plan.orientations_ras is None
    np.testing.assert_allclose(plan.strengths_u, [3.52, 3.48, 3.50])
    s = plan.sources[0]
    assert s.reference_datetime == datetime(2023, 10, 26, 9, 8, 12)
    assert s.isotope == "Cs-131" and s.model_id == "CS-1 Rev2" and s.source_type == "LINE"
    assert s.half_life_days == pytest.approx(9.689)
    assert s.active_length_mm == pytest.approx(4.0)
    assert s.n_control_points == 2 and s.channel_number == 1 and s.source_number == 1
    pl = plan.plan_level
    assert pl["brachy_treatment_technique"] == "PERMANENT"
    assert pl["brachy_treatment_type"] == "LDR"
    assert pl["prescription_cgy"] == pytest.approx(6000.0)
    assert pl["n_channels"] == 3 and pl["isotopes"] == ["Cs-131"]
    assert pl["formalism"] is None
    assert plan.frame_of_reference_uid == FOR_UID
    assert plan.referenced_structure_set_uid == "1.2.826.0.1.3680043.8.498.55"


def test_parse_dicom_datetime_forms():
    assert rt._parse_dicom_datetime("20231026", "0908") == datetime(2023, 10, 26, 9, 8)
    assert rt._parse_dicom_datetime("20231026", "090812.5") == datetime(2023, 10, 26, 9, 8, 12, 500000)
    assert rt._parse_dicom_datetime("20231026", "") == datetime(2023, 10, 26)
    assert rt._parse_dicom_datetime("", "090812") is None


# ------------------------------------------------- (g) verify_against_tps
@pytest.fixture(scope="module")
def dose_pair(tmp_path_factory, rtstruct_file):
    d = tmp_path_factory.mktemp("dose")
    rs = rt.load_rtstruct(rtstruct_file)
    # dose grid 1 x 1 x 1.667 mm around the sphere, identity-IOP LPS header
    spacing = np.array([1.0, 1.0, 5.0 / 3.0])
    n = np.ceil(2 * (R_SPHERE + 4.0) / spacing).astype(int) + 1
    origin_lps = SPHERE_CENTER_LPS - (n - 1) / 2.0 * spacing + 0.123 * spacing
    affine_lps = axial_affine_ras(origin_lps, spacing)
    affine_ras = np.diag([-1.0, -1.0, 1.0, 1.0]) @ affine_lps
    shape = (int(n[2]), int(n[1]), int(n[0]))
    dose = _dose_field(shape, affine_ras, SPHERE_CENTER_RAS)
    dose = np.round(dose / 2.0) * 2.0
    f = write_rtdose(d / "dose.dcm", dose, origin_lps, (1.0, 1.0), spacing[2], units="GY", scaling=0.02)
    return rs, rt.load_rtdose(f)


def test_verify_against_tps_pass_and_fail(dose_pair):
    rs, dv = dose_pair
    mask = rt.rasterize_roi(rs, "Sphere", dv.affine, dv.array.shape)
    st = rt._dose_stats(dv.array[mask], 6000.0, float(np.prod(dv.spacing)))
    ref = {"Sphere": {"volume_cc": st["volume_cc"], "D90_cgy": st["D90"]}}
    rows = rt.verify_against_tps(dv, rs, ref, rois=("Sphere",), rx_cgy=6000.0)
    assert len(rows) == 1 and rows[0]["pass"]
    assert rows[0]["vol_rel_err"] == 0.0 and rows[0]["D90_rel_err"] == 0.0
    assert set(rows[0]) >= {"roi", "vol_mim", "vol_gtcore", "vol_rel_err", "D90_mim",
                            "D90_gtcore", "D90_rel_err", "pass"}
    # analytic sphere volume as the reference also passes (within 2 %)
    ref2 = {"sphere": {"volume_cc": 4.0 / 3.0 * np.pi * R_SPHERE ** 3 / 1000.0, "D90_cgy": st["D90"]}}
    assert rt.verify_against_tps(dv, rs, ref2, rois=("Sphere",))[0]["pass"]
    # perturbed volume +5 % fails on volume only
    bad = {"Sphere": {"volume_cc": 1.05 * st["volume_cc"], "D90_cgy": st["D90"]}}
    row = rt.verify_against_tps(dv, rs, bad, rois=("Sphere",))[0]
    assert not row["pass"] and row["vol_rel_err"] == pytest.approx(0.05 / 1.05) and row["D90_rel_err"] == 0.0
    # perturbed D90 fails too
    bad = {"Sphere": {"volume_cc": st["volume_cc"], "D90_cgy": 0.95 * st["D90"]}}
    assert not rt.verify_against_tps(dv, rs, bad, rois=("Sphere",))[0]["pass"]
    # missing reference entry -> nan errors, fail, no exception
    row = rt.verify_against_tps(dv, rs, ref, rois=("Slab",))[0]
    assert not row["pass"] and np.isnan(row["vol_rel_err"])
    with pytest.raises(ValueError, match="DVHSequence"):
        rt.verify_against_tps(dv, rs, None, rois=("Sphere",))


def test_dose_stats_convention(dose_pair):
    """D90 is the type-1 inverted-CDF quantile and V100 the >= rule."""
    d = np.array([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
    st = rt._dose_stats(d, 50.0, 1.0)
    assert st["D90"] == 10.0           # smallest d with F(d) >= 0.10 (no interpolation)
    assert st["D50"] == 50.0
    assert st["D100"] == 10.0
    assert st["V100"] == 0.6           # doses >= 50: six of ten
    assert st["V150"] == 0.3           # doses >= 75: three of ten
    assert st["volume_cc"] == pytest.approx(0.01)


def test_reference_from_dvh_sequence(tmp_path, rtstruct):
    # cumulative DVH: 1 Gy bins, 19.0 cc total, falls linearly to 0 between 60 and 70 Gy
    widths = np.ones(80)
    edges = np.arange(80)
    vols = np.clip(19.0 * (70.0 - edges) / 10.0, 0.0, 19.0)
    data = np.column_stack([widths, vols]).ravel()
    f = write_rtdose(tmp_path / "dvh.dcm", np.full((2, 2, 2), 100.0), [0, 0, 0], (1, 1), 1.0,
                     dvh_items=[(1, data, "CM3", "GY", "CUMULATIVE")])
    vol = rt.load_rtdose(f)
    assert "dvh_sequence" in vol.meta
    ref = rt.reference_from_dvh_sequence(vol, rtstruct)
    assert "Sphere" in ref
    assert ref["Sphere"]["volume_cc"] == pytest.approx(19.0)
    assert ref["Sphere"]["D90_cgy"] == pytest.approx(6100.0)     # 90 % of 19 cc at 61 Gy
    # differential form of the same curve gives the same D90
    diff = np.concatenate([-np.diff(vols), [vols[-1]]])
    data_d = np.column_stack([widths, diff]).ravel()
    ref_d = rt.reference_from_dvh_sequence(
        [{"roi_numbers": [1], "dvh_type": "DIFFERENTIAL", "dose_units": "GY", "dose_scaling": 1.0,
          "volume_units": "CM3", "data": data_d}], rtstruct)
    assert ref_d["Sphere"]["D90_cgy"] == pytest.approx(6100.0, rel=1e-6)
    assert ref_d["Sphere"]["volume_cc"] == pytest.approx(19.0)
    # verify_against_tps accepts the Volume carrying the DVHSequence directly
    rows = rt.verify_against_tps(vol, rtstruct, vol, rois=("Sphere",))
    assert rows[0]["vol_mim"] == pytest.approx(19.0)


WIDE_EXAMPLE = """Patient,ANON
Plan,implant
Structure,Volume (cc),D90 (Gy),V100 (%)
CTV_GT,19.02,63.65,94.1
Cav_Post,35.10,71.2,99.0
"""

LONG_EXAMPLE = "Structure\tMetric\tValue\nCTV_GT\tVolume [cc]\t19.02\nCTV_GT\tD90 [cGy]\t6365\nCav_Post\tVolume [cc]\t35.1\nCav_Post\tD90 [cGy]\t7120\n"

CURVES_EXAMPLE = """Dose (Gy);CTV_GT (cc);Cav_Post (cc)
0;19.0;35.0
50;19.0;35.0
60;19.0;30.0
70;0.0;10.0
80;0.0;0.0
"""


def test_parse_mim_dvh_export_layouts(tmp_path):
    p = tmp_path / "wide.csv"
    p.write_text(WIDE_EXAMPLE, encoding="utf-8")
    ref = rt.parse_mim_dvh_export(p)
    assert ref["CTV_GT"]["volume_cc"] == pytest.approx(19.02)
    assert ref["CTV_GT"]["D90_cgy"] == pytest.approx(6365.0)
    assert ref["Cav_Post"]["D90_cgy"] == pytest.approx(7120.0)
    p = tmp_path / "long.tsv"
    p.write_text(LONG_EXAMPLE, encoding="utf-8")
    ref = rt.parse_mim_dvh_export(p)
    assert ref["CTV_GT"]["volume_cc"] == pytest.approx(19.02)
    assert ref["CTV_GT"]["D90_cgy"] == pytest.approx(6365.0)
    p = tmp_path / "curves.csv"
    p.write_text(CURVES_EXAMPLE, encoding="utf-8")
    ref = rt.parse_mim_dvh_export(p)
    assert ref["CTV_GT"]["volume_cc"] == pytest.approx(19.0)
    assert ref["CTV_GT"]["D90_cgy"] == pytest.approx(6100.0)    # 17.1 cc at 61 Gy
    assert ref["Cav_Post"]["volume_cc"] == pytest.approx(35.0)
    # explicit column names and forced units
    p = tmp_path / "custom.csv"
    p.write_text("Name,Vol,Dose90\nCTV_GT,19.0,6365\n", encoding="utf-8")
    ref = rt.parse_mim_dvh_export(p, roi_col="Name", volume_col="Vol", d90_col="Dose90", dose_units="CGY")
    assert ref["CTV_GT"]["D90_cgy"] == pytest.approx(6365.0)
    p = tmp_path / "junk.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        rt.parse_mim_dvh_export(p)


# ------------------------------------------------------ (h) check_references
def test_check_references(tmp_path, rtstruct, dose_pair):
    _, dv = dose_pair
    assert rt.check_references(rtstruct, rtdose=dv) == []
    other = write_rtdose(tmp_path / "other.dcm", np.full((2, 2, 2), 1.0), [0, 0, 0], (1, 1), 1.0,
                         for_uid="1.2.826.0.1.3680043.8.498.999")
    warns = rt.check_references(rtstruct, rtdose=rt.load_rtdose(other))
    assert len(warns) == 1 and "frame of reference" in warns[0]
    assert "1.2.826" not in warns[0]                      # never leaks UIDs
    # CT given as a Volume with the loader's meta keys
    ct = Volume(np.zeros((2, 2, 2), np.float32), np.eye(4),
                {"frame_of_reference_uid": FOR_UID, "series_instance_uid": SERIES_UID})
    assert rt.check_references(rtstruct, ct=ct, rtdose=dv) == []
    ct.meta["series_instance_uid"] = "1.2.3"
    warns = rt.check_references(rtstruct, ct=ct)
    assert any("different CT series" in w for w in warns)
    # RTPLAN with a foreign frame of reference
    plan = rt.load_rtplan(write_rtplan(tmp_path / "plan.dcm", [{"pos": (0, 0, 0), "orient": None, "sk": 3.5}],
                                       for_uid="1.2.826.0.1.3680043.8.498.998"))
    warns = rt.check_references(rtstruct, rtdose=dv, rtplan=plan)
    assert any("RTPLAN frame of reference differs from the RTSTRUCT" in w for w in warns)


def test_find_dicom_file_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        rt.load_rtstruct(tmp_path / "missing.dcm")
    d = tmp_path / "empty"
    d.mkdir()
    (d / "note.txt").write_text("not dicom", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        rt.load_rtdose(d)
