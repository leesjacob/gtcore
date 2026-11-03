"""``scripts/cavity_change.py`` and the CT-MR path of
``gtcore.preprocess.register`` on synthetic data only (Paper 2 Phase 5,
task ``cavity-change``).

- CT-MR registration round trip: a synthetic head CT and an "MR" of it
  (different contrast: bone dark, brain bright) under a known rigid
  transform, on a different grid -> probes recovered to < 0.5 mm;
- the multi-start registration of a printed block into an MR-like scan in
  which it sits rotated by 90 degrees (the phantom frame is arbitrary);
- slice resampling geometry: a linear intensity field sampled through a
  known transform is reproduced exactly at the fixed slice's voxel centres;
  the square crop and its extent;
- helpers: cube rotations, Otsu, two-way surface distance, signed distance,
  composition of transforms, the manual-contour loader;
- ``run()`` end to end on the synthetic head with a manual contour on one
  "MRI": every output file with its documented columns / arrays, the same
  crop for all panels, no machine path in the headers.
"""
from __future__ import annotations

import csv
import importlib.util
import os

import numpy as np
import pytest

sitk = pytest.importorskip("SimpleITK")
trimesh = pytest.importorskip("trimesh")

from gtcore.preprocess import register as R  # noqa: E402
from gtcore.volume import Volume, apply_affine  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLIP = np.array([-1.0, -1.0, 1.0])


@pytest.fixture(scope="module")
def cc():
    path = os.path.join(ROOT, "scripts", "cavity_change.py")
    spec = importlib.util.spec_from_file_location("cavity_change", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------- synthetic head
N, SP = 64, 1.5
AFF = np.diag([-SP, -SP, SP, 1.0])               # RAS affine with flipped x / y (like a CT)
AFF[:3, 3] = (47.25, 47.25, -47.25)
CAV_C = np.array([14.0, 8.0, 6.0])               # off-centre resection cavity (RAS)
CAV_R = 9.0


def _xyz(aff=AFF, shape=(N, N, N)):
    k, j, i = np.mgrid[:shape[0], :shape[1], :shape[2]]
    ijk = np.stack([i, j, k], axis=-1).reshape(-1, 3).astype(float)
    return apply_affine(aff, ijk).reshape(shape + (3,))


def _head_labels(xyz):
    """0 air, 1 scalp, 2 skull, 3 brain, 4 ventricle, 5 cavity, 6 nose."""
    x, y, z = xyz[..., 0], xyz[..., 1], xyz[..., 2]
    e = (x / 36.0) ** 2 + (y / 42.0) ** 2 + (z / 33.0) ** 2
    lab = np.zeros(xyz.shape[:-1], np.uint8)
    lab[e <= 1.0] = 1
    lab[e <= 0.88] = 2
    lab[e <= 0.70] = 3
    vent = ((x + 6.0) / 4.0) ** 2 + ((y + 3.0) / 11.0) ** 2 + ((z - 2.0) / 6.0) ** 2 <= 1.0
    lab[vent & (lab == 3)] = 4
    cav = np.linalg.norm(xyz - CAV_C, axis=-1) <= CAV_R
    lab[cav & (lab >= 3)] = 5
    nose = ((x / 6.0) ** 2 + ((y - 42.0) / 7.0) ** 2 + ((z + 8.0) / 8.0) ** 2) <= 1.0
    lab[nose & (lab == 0)] = 6
    return lab


CT_HU = {0: -1000.0, 1: 40.0, 2: 1100.0, 3: 35.0, 4: 5.0, 5: 10.0, 6: 40.0}
MR_SI = {0: 0.0, 1: 420.0, 2: 30.0, 3: 300.0, 4: 70.0, 5: 60.0, 6: 420.0}


def _image(lab, table, noise, seed):
    out = np.zeros(lab.shape, np.float32)
    for k, v in table.items():
        out[lab == k] = v
    rng = np.random.default_rng(seed)
    return out + rng.normal(0.0, noise, size=lab.shape).astype(np.float32)


def _truth(angles_deg=(5.0, -4.0, 7.0), t=(4.0, -3.0, 5.0)):
    tx = sitk.Euler3DTransform()
    tx.SetCenter((0.0, 0.0, 0.0))
    tx.SetRotation(*np.radians(angles_deg))
    tx.SetTranslation(t)
    return tx


MR_AFF = np.diag([-1.75, -1.75, 2.0, 1.0])       # a different grid for the MR
MR_AFF[:3, 3] = (56.0, 56.0, -48.0)
MR_SHAPE = (49, 64, 64)


def _mr_of_head(truth):
    """The head seen by an MR whose physical frame is the CT frame moved by
    ``truth`` (fixed CT point p sits at truth(p) in the MR frame): label the
    MR voxel centres by pulling them back through the inverse."""
    xyz = _xyz(MR_AFF, MR_SHAPE).reshape(-1, 3)
    back = R.transform_points_ras(truth, xyz, inverse=True)
    lab = _head_labels(back.reshape(MR_SHAPE + (3,)))
    return Volume(_image(lab, MR_SI, 12.0, 3), MR_AFF, {"modality": "MR"}), lab == 5


@pytest.fixture(scope="module")
def head():
    lab = _head_labels(_xyz())
    ct = Volume(_image(lab, CT_HU, 10.0, 1), AFF, {})
    truth = _truth()
    mr, mr_cav = _mr_of_head(truth)
    return {"ct": ct, "lab": lab, "truth": truth, "mr": mr, "mr_cav": mr_cav}


# ------------------------------------------------------------ registration
def test_ct_mr_registration_round_trip(cc, head):
    ct, mr, truth = head["ct"], head["mr"], head["truth"]
    body = cc.body_mask_ct(ct)
    hm, thr = cc.head_mask_mr(mr)
    assert 0.0 < thr < 420.0 and hm.sum() > 0.5 * (head["lab"] > 0).sum() * (SP ** 3) / (1.75 * 1.75 * 2.0)
    tx, info = R.register_rigid(ct, body, mr, hm, moving_range=(0.0, float(np.percentile(mr.array, 99.5))))
    assert info["moving_range"][0] == 0.0 and info["fixed_range"] == [-1000.0, 500.0]
    assert np.isfinite(info["metric_initial"]) and info["metric_final"] < info["metric_initial"]
    probes = np.array([[0.0, 0.0, 0.0], [20.0, -15.0, 10.0], [-18.0, 12.0, -16.0], CAV_C])
    want = R.transform_points_ras(truth, probes)
    err = np.linalg.norm(R.transform_points_ras(tx, probes) - want, axis=1)
    assert err.max() < 0.5, err
    # composition: CT -> MR -> CT is the identity
    back = R.compose_points_ras(probes, [(tx, False), (tx, True)])
    assert np.allclose(back, probes, atol=1e-9)
    # the bone/brain check reads as "aligned" (< 1) and the record is complete
    tx2, rec, hm2 = cc.register_mr_to_ct(ct, body, mr, "post")
    assert rec["scan"] == "PT-postMRI" and rec["bone_over_brain"] < 0.5
    assert rec["dice_head_masks_common_fov"] > 0.9
    assert np.abs(R.transform_points_ras(tx2, probes) - want).max() < 0.5


def test_register_rigid_initial_rotation_validated(head):
    ct = head["ct"]
    m = ct.array > -300
    with pytest.raises(ValueError, match="proper rotation"):
        R.register_rigid(ct, m, ct, m, initial_rotation=np.diag([1.0, 1.0, -1.0]))


def test_cube_rotations(cc):
    rots = cc.cube_rotations()
    assert len(rots) == 24 and np.allclose(rots[0], np.eye(3))
    assert all(abs(np.linalg.det(r) - 1.0) < 1e-12 and np.allclose(r @ r.T, np.eye(3)) for r in rots)
    assert len({tuple(r.ravel()) for r in rots}) == 24


def _print(xyz):
    """An asymmetric printed block: a slab with a corner notch, an
    off-centre spherical cavity and a channel from it to the +z face (the
    printed mouth).  Returns ``(plastic, lumen)``."""
    x, y, z = xyz[..., 0], xyz[..., 1], xyz[..., 2]
    slab = (np.abs(x) <= 24.0) & (np.abs(y) <= 18.0) & (np.abs(z) <= 14.0)
    notch = (x > 12.0) & (y > 8.0)
    cav = np.linalg.norm(xyz - np.array([5.0, -3.0, 0.0]), axis=-1) <= 9.0
    chan = (np.hypot(x - 5.0, y + 3.0) <= 4.0) & (z > 0.0)
    lumen = (cav | chan) & slab & ~notch
    return slab & ~notch & ~lumen, cav & slab


def test_phantom_multistart_recovers_a_rotated_print(cc):
    """The printed block (CT, own frame) inside an MR-like scan in which it is
    turned by 90 degrees about z (plus a small tilt) and shifted: the
    24-orientation multi-start recovers the pose, and the identity start is
    not the answer."""
    n = 64
    aff0 = np.eye(4)
    aff0[:3, 3] = (-31.5, -31.5, -31.5)
    plastic, lumen = _print(_xyz(aff0, (n, n, n)))
    ph0 = Volume(np.where(plastic, 100.0, -1000.0).astype(np.float32)
                 + np.random.default_rng(0).normal(0, 10, plastic.shape).astype(np.float32), aff0, {})
    truth = sitk.Euler3DTransform()               # PH0 point p sits at truth(p) in the MR frame (LPS)
    truth.SetCenter((0.0, 0.0, 0.0))
    truth.SetRotation(np.radians(4.0), np.radians(-3.0), np.radians(90.0))
    truth.SetTranslation((6.0, -4.0, 3.0))
    mr_aff = np.eye(4)
    mr_aff[:3, 3] = (-40.0, -40.0, -40.0)
    mshape = (81, 81, 81)
    back = R.transform_points_ras(truth, _xyz(mr_aff, mshape).reshape(-1, 3), inverse=True).reshape(mshape + (3,))
    p_mr, _l = _print(back)
    # MR-like: plastic dark, the surrounding "tissue" and the lumen fluid bright
    in_box = np.linalg.norm(back, axis=-1) <= 38.0
    mr_arr = np.where(p_mr, 20.0, np.where(in_box, 250.0, 0.0)).astype(np.float32)
    mr_arr += np.random.default_rng(1).normal(0, 8, mshape).astype(np.float32)
    mr = Volume(mr_arr, mr_aff, {})
    c_lumen = apply_affine(aff0, np.argwhere(lumen).mean(axis=0)[::-1])
    target = R.transform_points_ras(truth, c_lumen)[0]
    tx, rec, refined = cc.register_phantom_to_mr(ph0, plastic, lumen, mr, mr_arr > 100.0, target, n_refine=3)
    assert rec["n_starts"] == 24 and rec["n_refined"] == 3
    probes = np.array([[0.0, 0.0, 0.0], [15.0, -10.0, 8.0], [-12.0, 14.0, -9.0]])
    err = np.linalg.norm(R.transform_points_ras(tx, probes) - R.transform_points_ras(truth, probes), axis=1)
    assert err.max() < 1.0, (err, rec)
    assert rec["best_start"] != 0          # the identity start is not the answer here


# ------------------------------------------------------------ the slice
def test_slice_resampling_geometry(cc, head):
    ct = head["ct"]
    truth = head["truth"]
    # a moving volume carrying a linear field of its own RAS coordinates
    xyz_m = _xyz(MR_AFF, MR_SHAPE)
    f = lambda p: 2.0 * p[..., 0] - 3.0 * p[..., 1] + 0.5 * p[..., 2] + 7.0   # noqa: E731
    mov = Volume(f(xyz_m).astype(np.float32), MR_AFF, {})
    k = 30
    x2, y2, pts = cc.slice_points(ct, k)
    assert x2.shape == (N, N) and np.allclose(pts[:, 2], apply_affine(AFF, [0, 0, k])[2])
    assert np.allclose(x2[0, :3], [47.25, 45.75, 44.25]) and np.allclose(y2[:3, 0], [47.25, 45.75, 44.25])
    img = cc.sample_through(mov, truth, pts).reshape(x2.shape)
    carried = R.transform_points_ras(truth, pts).reshape(N, N, 3)
    inside = np.all((carried >= xyz_m.reshape(-1, 3).min(0) + 0.1) & (carried <= xyz_m.reshape(-1, 3).max(0) - 0.1), axis=-1)
    assert inside.mean() > 0.8
    assert np.allclose(img[inside], f(carried)[inside], atol=2e-3)
    # identity transform samples in place
    same = Volume(f(_xyz()).astype(np.float32), AFF, {})
    assert np.allclose(cc.sample_through(same, None, pts).reshape(x2.shape), same.array[k], atol=2e-3)
    # square crop centred on a point, clipped, extent in voxel centres
    crop, ext = cc.square_crop(ct, k, (14.0, 8.0), 30.0)
    i0, i1, j0, j1 = crop
    assert i1 - i0 == j1 - j0 == 20
    cx = 0.5 * (x2[0, i0] + x2[0, i1 - 1])
    cy = 0.5 * (y2[j0, 0] + y2[j1 - 1, 0])
    assert abs(cx - 14.0) <= SP and abs(cy - 8.0) <= SP
    assert np.allclose(ext, (x2[0, i1 - 1], x2[0, i0], y2[j1 - 1, 0], y2[j0, 0]))
    crop_c, _ = cc.square_crop(ct, k, (40.0, 40.0), 60.0)
    assert crop_c[0] == 0 and crop_c[2] == 0
    kk, zz, c = cc.implant_slice_index(ct, np.array([[0.0, 0.0, 5.0], [0.0, 0.0, 7.0]]))
    assert abs(zz - 6.0) <= SP / 2 and kk == int(np.rint(ct.ras_to_index([0, 0, 6.0])[2]))


# ------------------------------------------------------------ helpers
def _ball_mask(r, center=(0.0, 0.0, 0.0), n=60, sp=1.0):
    aff = np.diag([sp, sp, sp, 1.0])
    aff[:3, 3] = -0.5 * (n - 1) * sp
    xyz = _xyz(aff, (n, n, n))
    return np.linalg.norm(xyz - np.asarray(center), axis=-1) <= r, aff


def test_surface_distances(cc):
    a, aff = _ball_mask(10.0)
    b, _ = _ball_mask(13.0)
    ma, mb = cc.mesh_of(a, aff), cc.mesh_of(b, aff)
    d = cc.surface_distance_two_way(ma, mb)
    for key in ("a_to_b_median_mm", "b_to_a_median_mm", "symmetric_median_mm"):
        assert abs(d[key] - 3.0) < 0.35, (key, d[key])
    assert d["symmetric_p95_mm"] < 3.8
    # signed: the larger sphere's surface lies outside the smaller cavity (+), and vice versa
    s_out = cc.signed_distance_to_mask(a, aff, np.asarray(mb.vertices))
    s_in = cc.signed_distance_to_mask(b, aff, np.asarray(ma.vertices))
    assert abs(np.median(s_out) - 3.0) < 0.5 and abs(np.median(s_in) + 3.0) < 0.5


def test_otsu_and_pose_difference(cc):
    rng = np.random.default_rng(0)
    v = np.concatenate([rng.normal(10, 2, 5000), rng.normal(100, 5, 5000)])
    t = cc.otsu_threshold(v)               # anywhere in the gap between the classes
    assert (v[:5000] > t).mean() < 0.05 and (v[5000:] <= t).mean() < 0.01
    a, b = _truth((0, 0, 0), (0, 0, 0)), _truth((0, 0, 10.0), (3.0, 4.0, 0.0))
    t, ang = cc.pose_difference(a, b)
    assert abs(t - 5.0) < 1e-9 and abs(ang - 10.0) < 1e-9


def test_manual_contour_loader_label_image(cc, head, tmp_path):
    from gtcore.io import save_volume
    mr = head["mr"]
    lab = Volume(head["mr_cav"].astype(np.uint8), mr.affine, {})
    p = str(tmp_path / "intra_cavity.nrrd")
    save_volume(lab, p)
    m = cc.load_manual_contour(p, "Cavity", mr)
    assert m.shape == mr.array.shape and (m == head["mr_cav"]).mean() > 0.999
    with pytest.raises(SystemExit):
        cc.parse_contours(["bogus=file"])
    assert cc.parse_contours(["post=a.nii", "pre=b.nrrd"]) == {"post": "a.nii", "pre": "b.nrrd"}


# ------------------------------------------------------------ end to end
def test_run_smoke(cc, head, tmp_path):
    ct, mr = head["ct"], head["mr"]
    lab = head["lab"]
    cav = lab == 5
    body = cc.body_mask_ct(ct)
    seeds = CAV_C + 6.0 * np.array([[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1.0]])
    pre, _pre_cav = _mr_of_head(_truth((-3.0, 2.0, -4.0), (-2.0, 3.0, 1.0)))
    out = tmp_path / "data"
    prev = tmp_path / "prev"
    cran = cc.cranium_mask(ct, {"skull": lab == 2, "cranial_interior": (lab >= 3) & (lab <= 5)})
    assert cran.sum() > ((lab >= 2) & (lab <= 5)).sum()
    res = cc.run(ct, body, seeds, cav, cav, {"pre": pre, "post": mr}, ct_cranium=cran, slice_k=None, crop_mm=60.0,
                 contours={"post": head["mr_cav"]}, out_dir=str(out), previews_dir=str(prev),
                 command="python scripts/cavity_change.py --ct \"$GT_DATA_ROOT/<PT-CT1mm>\"",
                 datasets=("PT-CT1mm", "PT-RT", "PT-preMRI", "PT-postMRI"))
    p = res["paths"]
    for key in ("registration_csv", "volumes_csv", "displacement_csv", "npz", "json"):
        assert os.path.exists(p[key])
        if key.endswith("csv"):
            text = open(p[key], encoding="utf-8").read()
            assert "C:\\" not in text and "Users" not in text and "# commit:" in text
            assert "calibration-set" in text
    for name in ("f3a_ct.png", "f3a_mri_pre.png", "f3a_mri_post.png"):
        assert os.path.exists(prev / name)
    # registrations: both recovered (bone dark in the MR)
    regs = list(csv.DictReader(line for line in open(p["registration_csv"], encoding="utf-8") if not line.startswith("#")))
    assert [r["scan"] for r in regs] == ["PT-preMRI", "PT-postMRI"]
    assert all(float(r["bone_over_brain"]) < 0.5 for r in regs)
    for r in regs:                 # two stages; stage 2 on the cranium, stable under a second sampling seed
        assert r["metric_region"].startswith("cranium") and float(r["stability_max_cranium_mm"]) < SP   # one voxel
        assert r["stage1_metric_final"] != "" and r["stage1_stop"] != ""
    # volumes: Cav_Post, gtcore cavity, the manual contour (own grid) -- no phantom rows without the phantom
    vols = {r["row"]: r for r in csv.DictReader(line for line in open(p["volumes_csv"], encoding="utf-8")
                                                 if not line.startswith("#"))}
    assert set(vols) == {"cav_post", "gtcore_cavity_ct", "manual_contour_post"}
    v_true = 4.0 / 3.0 * np.pi * CAV_R ** 3 / 1000.0
    assert abs(float(vols["cav_post"]["volume_cc"]) - v_true) / v_true < 0.15
    assert abs(float(vols["manual_contour_post"]["volume_cc"]) - v_true) / v_true < 0.15
    # displacement: the manual contour carried back lands on Cav_Post; no phantom -> stated
    disp = {r["row"]: r for r in csv.DictReader(line for line in open(p["displacement_csv"], encoding="utf-8")
                                                 if not line.startswith("#"))}
    man = disp["manual_contour_post_vs_cav_post"]
    assert float(man["symmetric_median_mm"]) < 1.0 and abs(float(man["volume_ratio_post_over_moving"]) - 1.0) < 0.15
    assert disp["printed_wall_vs_cav_post"]["credible"].startswith("NO")
    # the strip: same slice, same crop, one image per scan, windows
    z = np.load(p["npz"], allow_pickle=True)
    k = int(z["slice_k"])
    assert k == cc.implant_slice_index(ct, seeds)[0]
    for key in ("ct_hu", "mri_pre", "mri_post", "mask_cav_post", "mask_contour_post", "x_ras", "y_ras"):
        assert z[key].shape == (N, N), key
    i0, i1, j0, j1 = z["crop_ij"]
    assert i1 - i0 == j1 - j0 == 40
    assert tuple(z["wl_ct"]) == cc.WL_CT and z["wl_mri_pre"].shape == (2,)
    assert "mask_contour_pre" not in z.files              # MRIs are not segmented
    # the registered MRI slice shows the cavity where the CT does (dark MR, CT cavity mask)
    cav2 = z["mask_cav_post"]
    brain2 = lab[k] == 3
    assert np.median(z["mri_post"][cav2]) < 0.5 * np.median(z["mri_post"][brain2])
    # the manual contour carried into the CT slice overlaps Cav_Post
    mc = z["mask_contour_post"]
    assert 2.0 * (mc & cav2).sum() / (mc.sum() + cav2.sum()) > 0.85
    idx = __import__("json").load(open(p["json"], encoding="utf-8"))
    assert idx["flag"] == "calibration-set" and "mri_post" in idx["arrays"]
