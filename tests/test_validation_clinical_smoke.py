"""Smoke test for ``scripts/validation_clinical.py`` on synthetic data only.

A synthetic head phantom (``gtcore.phantom.make_head_phantom``) stands in for
the CT; an RTSTRUCT (``Cav_Post`` = the truth cavity outlined per slice,
``CTV_GT`` = a hand-trimmed half of its 5 mm rind, ``Brain`` = a sphere,
``Seeds`` = small circles around every truth seed but one, so one source is
"uncontoured" as in the clinical case) and an RTDOSE (gtcore's own TG-43
total-decay dose at the truth seeds on a 2 mm grid) are written with pydicom
exactly as ``tests/test_io_rt.py`` does.  No real data is read.
"""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pytest
from scipy import ndimage

from gtcore.dose.engine import dose_at_points
from gtcore.io import rt, save_volume
from gtcore.phantom import make_head_phantom
from gtcore.pipeline import reconstruct

pytest.importorskip("pydicom")
from test_io_rt import circle_lps, write_rtdose, write_rtstruct  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RX = 6000.0
SPACING = 1.0
LPS = np.array([-1.0, -1.0, 1.0])


@pytest.fixture(scope="module")
def vc():
    path = os.path.join(ROOT, "scripts", "validation_clinical.py")
    spec = importlib.util.spec_from_file_location("validation_clinical", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _slice_polygons_lps(mask, vol):
    """Closed planar contours (LPS) of a [k, j, i] mask, one or more per slice."""
    from skimage import measure
    out = []
    for k in range(mask.shape[0]):
        sl = mask[k]
        if not sl.any():
            continue
        padded = np.pad(sl.astype(float), 1)
        for c in measure.find_contours(padded, 0.5):
            j, i = c[:, 0] - 1.0, c[:, 1] - 1.0
            if len(j) < 4:
                continue
            ijk = np.column_stack([i, j, np.full(len(i), float(k))])
            ras = vol.index_to_ras(ijk)
            out.append(("CLOSED_PLANAR", ras * LPS))
    return out


def _stadium_lps(p0, p1, z, r, n=12):
    """Closed planar outline of the segment ``p0 -> p1`` (xy, LPS) widened by
    ``r``: two semicircles joined by the parallel sides."""
    p0 = np.asarray(p0, float)
    p1 = np.asarray(p1, float)
    d = p1 - p0
    th = np.arctan2(d[1], d[0]) if np.linalg.norm(d) > 1e-9 else 0.0
    t1 = th - np.pi / 2 + np.linspace(0.0, np.pi, n)        # around p1
    t0 = th + np.pi / 2 + np.linspace(0.0, np.pi, n)        # around p0
    pts = np.vstack([np.column_stack([p1[0] + r * np.cos(t1), p1[1] + r * np.sin(t1)]),
                     np.column_stack([p0[0] + r * np.cos(t0), p0[1] + r * np.sin(t0)])])
    return np.column_stack([pts, np.full(len(pts), float(z))])


@pytest.fixture(scope="module")
def synthetic_case(tmp_path_factory):
    d = tmp_path_factory.mktemp("clinical_synth")
    vol, truth = make_head_phantom(spacing=SPACING, n_tiles=2, rng_seed=2)
    centers = np.array([s.center_ras for s in truth.seeds])
    axes = np.array([s.axis_ras for s in truth.seeds])
    cavity = truth.masks["cavity"]
    dist = ndimage.distance_transform_edt(~cavity, sampling=vol.spacing[::-1])
    rind = (dist <= 5.0) & ~cavity
    # "hand-trimmed" partial rind: the half on the seeds' side of the cavity centre
    kk, jj, ii = np.indices(cavity.shape)
    pts = vol.index_to_ras(np.stack([ii.ravel(), jj.ravel(), kk.ravel()], axis=1).astype(float))
    side = centers.mean(axis=0) - truth.cavity_center_ras
    side /= np.linalg.norm(side)
    half = ((pts - truth.cavity_center_ras) @ side > 0.0).reshape(cavity.shape)
    ctv = rind & half
    # RTSTRUCT: Cav_Post, CTV_GT, Brain (sphere), Seeds (all but the last truth seed)
    brain = []
    for z in np.arange(-70.0, 70.0, 1.0):
        r2 = 70.0 ** 2 - z ** 2
        if r2 > 0:
            brain.append(("CLOSED_PLANAR", circle_lps(0.0, 0.0, z, np.sqrt(r2), 96)))
    # seed contours lie on the CT slice planes, as a contouring tool draws them: on every
    # slice the capsule crosses, the outline of the capsule segment inside that 1 mm slab
    # projected onto the plane and widened by 1 mm (the bright blob's footprint)
    z_slices = vol.index_to_ras(np.column_stack([np.zeros(vol.array.shape[0]), np.zeros(vol.array.shape[0]),
                                                 np.arange(vol.array.shape[0], dtype=float)]))[:, 2]
    seeds = []
    for c, a in zip(centers[:-1], axes[:-1]):
        for zk in z_slices[np.abs(z_slices - c[2]) <= 2.25 * abs(a[2]) + 0.5]:
            if abs(a[2]) > 1e-6:
                s0 = np.clip((zk - 0.5 - c[2]) / a[2], -2.25, 2.25)
                s1 = np.clip((zk + 0.5 - c[2]) / a[2], -2.25, 2.25)
            else:
                s0, s1 = -2.25, 2.25
            p0 = (c + s0 * a) * LPS
            p1 = (c + s1 * a) * LPS
            seeds.append(("CLOSED_PLANAR", _stadium_lps(p0[:2], p1[:2], zk, 1.0)))
    rois = [("Cav_Post", _slice_polygons_lps(cavity, vol)),
            ("CTV_GT", _slice_polygons_lps(ctv, vol)),
            ("Brain", brain),
            ("Seeds", seeds)]
    rs_path = write_rtstruct(d / "rtstruct.dcm", rois)
    # RTDOSE: gtcore's TG-43 total-decay dose at the truth seeds on a 2 mm grid (GY, scaling
    # 0.02), evaluated on the grid the LPS header describes (identity IOP: RAS x and y run
    # backwards along i and j), exactly as tests/test_io_rt.py builds its dose field.
    lo = centers.min(axis=0) - 35.0
    hi = centers.max(axis=0) + 35.0
    n = np.ceil((hi - lo) / 2.0).astype(int) + 1
    ipp_lps = np.array([-hi[0], -hi[1], lo[2]])
    affine_lps = np.diag([2.0, 2.0, 2.0, 1.0])
    affine_lps[:3, 3] = ipp_lps
    affine_ras = np.diag([-1.0, -1.0, 1.0, 1.0]) @ affine_lps
    shape = (int(n[2]), int(n[1]), int(n[0]))
    kk, jj, ii = np.indices(shape)
    ijk1 = np.stack([ii.ravel(), jj.ravel(), kk.ravel(), np.ones(ii.size)], axis=1).astype(float)
    pts_ras = (ijk1 @ affine_ras.T)[:, :3]
    dose = dose_at_points(centers, axes, pts_ras, 3.5).reshape(shape)
    rtdose_path = write_rtdose(d / "rtdose.dcm", dose, ipp_lps, (2.0, 2.0), 2.0,
                               units="GY", scaling=0.02)
    ct_path = save_volume(vol, str(d / "ct.nrrd"))
    return {"dir": str(d), "vol": vol, "truth": truth, "centers": centers, "axes": axes,
            "rtstruct": rs_path, "rtdose": rtdose_path, "ct": ct_path, "n_seeds": len(centers)}


@pytest.fixture(scope="module")
def result(vc, synthetic_case, tmp_path_factory):
    out = tmp_path_factory.mktemp("clinical_out")
    case = synthetic_case
    vol = case["vol"]
    res = reconstruct(vol, verbose=False)
    rs = rt.load_rtstruct(case["rtstruct"])
    dv = rt.load_rtdose(case["rtdose"])
    r = vc.run_analysis(vol, rs, dv, res, RX, str(out), "python scripts/validation_clinical.py <synthetic>",
                        "test", datasets=("synthetic-phantom", "synthetic-RT"), verbose=True)
    r["out"] = str(out)
    r["res"] = res
    return r


def _read(vc, path):
    cols, rows = vc.read_csv_rows(path)
    return cols, rows


def test_inputs_are_what_the_clinical_case_has(synthetic_case):
    rs = rt.load_rtstruct(synthetic_case["rtstruct"])
    dv = rt.load_rtdose(synthetic_case["rtdose"])
    assert set(rs.roi_names) == {"Cav_Post", "CTV_GT", "Brain", "Seeds"}
    assert dv.meta["units"] == "cGy" and "dvh_sequence" not in dv.meta
    assert dv.array.max() > 20000.0


def test_seed_match_and_sources(result, synthetic_case):
    n = synthetic_case["n_seeds"]
    s = result["match_summary"]
    assert s["n_det"] == n                          # reconstruct finds every truth seed
    assert s["n_tps"] == n - 1 and s["n_matched"] == n - 1
    assert s["match_max_mm"] < 2.0
    assert s["n_sources"] == n and s["n_sources_matched"] == n      # RTDOSE maxima = plan sources
    src = result["sources"]
    assert src["centers"].shape == (n, 3) and np.all(np.isfinite(src["axes"]))
    assert (src["origin"] == "contour").sum() == n - 1 and (src["origin"] == "rtdose-max").sum() == 1
    assert np.allclose(np.linalg.norm(src["axes"], axis=1), 1.0, atol=1e-6)
    # the RTDOSE maxima sit within one 2 mm voxel of the truth sources
    d = np.linalg.norm(src["centers"][:, None] - synthetic_case["centers"][None], axis=2).min(axis=1)
    assert d.max() < 2.0


def test_residual_csv_columns_and_rows(result, vc, synthetic_case):
    cols, rows = _read(vc, result["paths"]["residuals_csv"])
    assert cols == ["seed_id", "dx_mm", "dy_mm", "dz_mm", "dist_mm"]
    assert len(rows) == synthetic_case["n_seeds"] - 1
    for r in rows:
        v = np.array([float(r[c]) for c in cols[1:]])
        assert np.isclose(np.linalg.norm(v[:3]), v[3], atol=1e-4)
        assert v[3] < 2.0
    with open(result["paths"]["residuals_csv"], encoding="utf-8") as f:
        head = f.read(600)
    assert "# script: scripts/validation_clinical.py" in head and "# commit:" in head
    assert "synthetic-phantom" in head and "calibration-set" in head


def test_table3_rows_columns_and_sanity(result, vc):
    cols, rows = _read(vc, result["paths"]["table3_csv"])
    assert cols[:8] == ["row", "structure", "dose_source", "metric", "value", "unit", "grid", "flag"]
    assert all(r["flag"] == "calibration-set" for r in rows)
    labels = {r["row"] for r in rows}
    assert {vc.ROW_HRCTV, vc.ROW_RIND_TPS, vc.ROW_RIND_GT,
            vc.ROW_REACH % 10, vc.ROW_REACH % 12} <= labels
    sources = {r["dose_source"] for r in rows if r["metric"] == "D90"}
    assert {vc.COL_RTDOSE, vc.COL_TPS32, vc.COL_GT32, vc.COL_TPS30} <= sources
    # no MIM reference -> every dose row pending
    dose_rows = [r for r in rows if r["metric"] in ("D90", "V100", "V150") and r["row"] in labels - {"diagnostic"}
                 and r["dose_source"] != "-"]
    assert dose_rows and all(r["tps_check"] == "TPS volume check pending" for r in dose_rows)
    assert result["tps_status"] == "pending"

    def val(row, src, metric):
        return float([r for r in rows if r["row"] == row and r["dose_source"] == src
                      and r["metric"] == metric][0]["value"])
    # the RTDOSE is gtcore's own dose at the truth seeds: the 32-source column reproduces it
    d90_rt = val(vc.ROW_HRCTV, vc.COL_RTDOSE, "D90")
    d90_src = val(vc.ROW_HRCTV, vc.COL_TPS32, "D90")
    d90_gt = val(vc.ROW_HRCTV, vc.COL_GT32, "D90")
    assert d90_rt > 0 and abs(d90_src / d90_rt - 1.0) < 0.15        # 2 mm RTDOSE grid + maxima positions
    assert abs(d90_gt / d90_rt - 1.0) < 0.15
    # the contoured-seed column misses one source -> no higher than the 32-source column
    assert val(vc.ROW_HRCTV, vc.COL_TPS30, "D90") <= d90_src * 1.05
    vol_ctv = float([r for r in rows if r["row"] == vc.ROW_HRCTV and r["metric"] == "volume_cc"][0]["value"])
    assert vol_ctv > 1.0
    reach10 = float([r for r in rows if r["row"] == vc.ROW_REACH % 10 and r["metric"] == "volume_cc"][0]["value"])
    rind = float([r for r in rows if r["row"] == vc.ROW_RIND_TPS and r["metric"] == "volume_cc"][0]["value"])
    assert 0.0 < reach10 <= rind


def test_delivered_fraction_and_implied_sk(result):
    ratio = result["ratio"]
    assert ratio["n"] > 50
    assert 0.9 < ratio["ratio_median"] < 1.1              # RTDOSE is the total-decay dose
    assert "total dose to decay" in ratio["conclusion"]
    assert abs(ratio["implied_sk_u"] - 3.5) < 0.4         # diagnostic only


def test_panel_data_files(result, vc):
    paths = result["paths"]
    for key in ("slice_npz", "slice_contours_csv", "slice_seeds_csv", "dvh_csv",
                "seed_match_csv", "source_match_csv"):
        assert os.path.isfile(paths[key]), key
    z = np.load(paths["slice_npz"])
    nj, ni = result["res"].volume.array.shape[1:] if hasattr(result["res"], "volume") else z["ct_hu"].shape
    for key in ("ct_hu", "rtdose_cgy", "gtcore32src_cgy", "gtcore32gt_cgy", "gtcore30_cgy",
                "mask_cav_post", "mask_ctv_gt", "mask_rind5_cavpost", "x_ras", "y_ras"):
        assert z[key].shape == (nj, ni), key
    assert int(z["slice_k"]) == result["slice"]["slice_k"]
    assert "validation_clinical" in str(z["header"])
    cols, rows = _read(vc, paths["slice_contours_csv"])
    assert cols == vc.CONTOUR_COLUMNS
    layers = {r["layer"] for r in rows}
    assert {"rtdose_iso", "gtcore32src_iso", "cav_post_outline", "ctv_gt_outline",
            "rind5_cavpost_outline"} <= layers
    levels = {float(r["level_cgy"]) for r in rows if r["layer"] == "rtdose_iso"}
    assert levels == {3000.0, 6000.0}
    cols, rows = _read(vc, paths["slice_seeds_csv"])
    assert cols == vc.SLICE_SEED_COLUMNS
    assert {r["set"] for r in rows} == {"gtcore_seed", "tps_contour_centroid", "plan_source"}
    cols, rows = _read(vc, paths["dvh_csv"])
    assert cols == vc.DVH_COLUMNS and len(rows) == 301
    rt_curve = np.array([float(r["rtdose"]) for r in rows])
    assert rt_curve[0] == 1.0 and np.all(np.diff(rt_curve) <= 1e-12) and rt_curve[-1] <= 0.3
    cols, rows = _read(vc, paths["source_match_csv"])
    assert cols == vc.SOURCE_MATCH_COLUMNS


def test_main_end_to_end(vc, synthetic_case, tmp_path, capsys):
    out = tmp_path / "out"
    rc = vc.main([synthetic_case["ct"], synthetic_case["rtstruct"], synthetic_case["rtdose"],
                  "--rx", "6000", "--out", str(out), "--datasets", "synthetic-phantom,synthetic-RT"])
    assert rc == 0
    text = capsys.readouterr().out
    assert "gtcore.__file__ =" in text and "git commit:" in text
    assert "structure x dose matrix" in text
    assert "diagnostic, not used" in text
    assert "TPS volume check pending" in text and "--mim-dvh" in text
    assert "hooks:" in text
    assert (out / "clinical_table3_rows.csv").is_file()
    with open(out / "clinical_table3_rows.csv", encoding="utf-8") as f:
        head = f.read(400)
    assert "C:\\" not in head and "/Users/" not in head          # no machine paths in headers
    assert "<synthetic-phantom dir>" in head


def test_helpers(vc, tmp_path):
    # polylines from a disc: one closed polyline at the 0.5 level
    x = np.arange(20.0)
    X, Y = np.meshgrid(x, x)
    disc = ((X - 10) ** 2 + (Y - 10) ** 2 <= 25).astype(float)
    polys = vc.mask_outline_polylines(X, Y, disc)
    assert len(polys) == 1 and polys[0].shape[1] == 2 and polys[0].shape[0] > 8
    assert np.allclose(polys[0][0], polys[0][-1])
    assert vc.mask_outline_polylines(X, Y, np.zeros_like(disc)) == []
    # DVH curve: >= rule on the grid
    c = vc.dvh_curve([10.0, 20.0, 30.0, 40.0], [0.0, 20.0, 25.0, 40.0, 50.0])
    assert np.allclose(c, [1.0, 0.75, 0.5, 0.25, 0.0])
    # one-to-one match
    a = np.array([[0, 0, 0], [10, 0, 0]], float)
    b = np.array([[10.5, 0, 0], [0.2, 0, 0], [50, 0, 0]], float)
    ia, ib, d, ok = vc.match_one_to_one(a, b, 1.0)
    assert set(zip(ia.tolist(), ib.tolist())) == {(0, 1), (1, 0)} and ok.all()
    # CSV round trip with quoting
    rows = [{"a": 'x,"y"', "b": 1.5, "c": float("nan"), "d": True}]
    p = str(tmp_path / "vc_smoke_roundtrip.csv")
    vc.write_csv(p, rows, ["a", "b", "c", "d"], ["# h"])
    cols, back = vc.read_csv_rows(p)
    assert cols == ["a", "b", "c", "d"] and back[0]["a"] == 'x,"y"' and back[0]["c"] == "nan"
