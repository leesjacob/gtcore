"""Craniotomy-side eligibility (``gtcore.plan.candidates``, Paper 2 Phase 4, F8(b)).

Synthetic geometry only:

- a toy head (ball interior, skull shell with a conical hole, body) gives
  the opening from the skull defect; with a cavity touching the vault under
  the hole the cavity mouth is preferred; a sealed skull without a cavity
  fails loudly; a sealed skull (bone flap back) WITH a cavity touching the
  vault still yields the opening -- the clinical situation;
- a spherical cavity under the hole: the floor and walls facing the hole
  are eligible, the roof is not;
- a two-lobe cavity (a lateral pocket out of the line of sight): the far
  wall of the hidden lobe is not eligible, the floor under the hole is;
- ``hits_before`` equals the chunked trimesh reference cast;
- the ``eligible_faces`` rules, the packed mask, and the ``optimize`` option
  (with the fakes of ``tests/test_plan_api.py``): the report records the
  rule, the fraction and the mask; conflicting inputs are rejected.
"""
from __future__ import annotations

import json

import numpy as np
import pytest
import trimesh

import gtcore.plan as plan
import plan_fixtures as pf
from gtcore.plan import api
from gtcore.plan import candidates as C
from gtcore.segment import mask_to_mesh
from gtcore.volume import apply_affine
from test_plan_api import _install_fakes


def _grid(n=81, spacing=1.0, origin=-40.0):
    aff = np.diag([spacing] * 3 + [1.0])
    aff[:3, 3] = origin
    k, j, i = np.mgrid[:n, :n, :n]
    ijk = np.stack([i, j, k], axis=-1).reshape(-1, 3).astype(float)
    return aff, apply_affine(aff, ijk).reshape(n, n, n, 3)


HOLE_FULL_DEG = 40.0      # conical skull defect about +z (radius ~10 mm at r = 30)


def _toy_head(hole=True):
    aff, xyz = _grid()
    r = np.linalg.norm(xyz, axis=-1)
    interior = r <= 30.0
    with np.errstate(invalid="ignore", divide="ignore"):
        cosang = xyz[..., 2] / np.maximum(r, 1e-9)
    cap = cosang >= np.cos(np.deg2rad(HOLE_FULL_DEG / 2.0))
    skull = (r > 30.0) & (r <= 34.0)
    if hole:
        skull &= ~cap
    body = r <= 38.0
    cavity = (np.linalg.norm(xyz - np.array([0.0, 0.0, 21.0]), axis=-1) <= 10.0) & interior
    return {"cranial_interior": interior, "skull": skull, "body": body}, aff, cavity


@pytest.fixture(scope="module")
def head():
    return _toy_head(hole=True)


def _angle_deg(a, b):
    a = np.asarray(a, float) / np.linalg.norm(a)
    b = np.asarray(b, float) / np.linalg.norm(b)
    return float(np.degrees(np.arccos(np.clip(a @ b, -1.0, 1.0))))


# ------------------------------------------------------------- the opening
def test_opening_from_the_skull_defect(head):
    masks, aff, _cav = head
    op = C.craniotomy_opening(masks, aff)
    assert isinstance(op, C.Opening)
    assert op.info["source"] == "defect" and op.info["n_mouth_voxels"] == 0
    assert abs(op.centroid_ras[0]) < 2.0 and abs(op.centroid_ras[1]) < 2.0
    assert 26.0 <= op.centroid_ras[2] <= 31.0
    assert _angle_deg(op.normal_ras, [0, 0, 1]) < 8.0
    # a 40 deg cone on a 30 mm sphere: 2 pi R^2 (1 - cos 20) = 341 mm^2, minus the
    # 3 mm margin along the rim
    assert 120.0 <= op.area_mm2 <= 450.0, op.area_mm2
    assert op.points_ras.shape[1] == 3 and len(op.points_ras) == op.info["chosen"]["n_voxels"]
    assert op.info["margin_mm"] == C.CRANIOTOMY_MARGIN_MM
    assert np.allclose(op.view_point(10.0), op.centroid_ras + 10.0 * op.normal_ras)
    json.dumps(op.summary())
    assert "craniotomy opening" in op.describe()


def test_opening_prefers_the_cavity_mouth(head):
    masks, aff, cav = head
    op_d = C.craniotomy_opening(masks, aff)
    op = C.craniotomy_opening(masks, aff, cavity_mask=cav)
    assert op.info["n_mouth_voxels"] > 0 and op.info["chosen"]["n_mouth"] > 0
    assert op.info["chosen_by"] == "most cavity-mouth voxels"
    assert np.linalg.norm(op.centroid_ras - op_d.centroid_ras) < 6.0
    assert _angle_deg(op.normal_ras, [0, 0, 1]) < 10.0


def test_opening_from_the_mouth_when_the_flap_is_back():
    """Bone flap replaced (no skull defect) but the cavity touches the vault:
    the opening is the cavity mouth -- the PT-CT1mm situation."""
    masks, aff, cav = _toy_head(hole=False)
    with pytest.raises(ValueError, match="no opening"):
        C.craniotomy_opening(masks, aff)
    op = C.craniotomy_opening(masks, aff, cavity_mask=cav)
    assert op.info["source"] == "mouth"
    assert op.info["n_defect_voxels"] == 0
    assert 24.0 <= op.centroid_ras[2] <= 31.0 and abs(op.centroid_ras[0]) < 2.0
    assert _angle_deg(op.normal_ras, [0, 0, 1]) < 10.0


def test_opening_validates_inputs(head):
    masks, aff, cav = head
    with pytest.raises(ValueError):
        C.craniotomy_opening({"skull": masks["skull"]}, aff)
    with pytest.raises(ValueError):
        C.craniotomy_opening(masks, aff, cavity_mask=cav[:-1])
    with pytest.raises(ValueError, match="no opening"):
        C.craniotomy_opening(masks, aff, min_area_mm2=1e6)


# --------------------------------------------------------- the face rule
@pytest.fixture(scope="module")
def sphere_cavity():
    m = trimesh.creation.icosphere(subdivisions=4, radius=15.0)
    m.apply_translation([0.0, 0.0, 10.0])          # top at z = 25, under the hole at ~30
    return m


def test_craniotomy_faces_floor_eligible_roof_not(head, sphere_cavity):
    masks, aff, _cav = head
    op = C.craniotomy_opening(masks, aff)
    el = C.craniotomy_faces(sphere_cavity, op, implant_center_ras=[0.0, 0.0, 10.0])
    assert isinstance(el, C.EligibleFaces) and el.rule == "craniotomy"
    z = sphere_cavity.triangles_center[:, 2]
    # from the view point 29 mm above the centre the silhouette sits at
    # cos(theta) = R / d, z = 10 + 15 * 15 / 29 = 17.8: flank faces below it face
    # the view point, the roof above it faces away
    floor, roof = z < 10.0 - 3.0, z > 19.5
    assert el.mask[floor].mean() > 0.95, el.mask[floor].mean()
    assert not el.mask[roof].any()
    assert 0.4 < el.fraction < 0.8 and 0.4 < el.fraction_faces < 0.8
    i = el.info
    assert i["n_eligible"] == el.n_eligible == int(el.mask.sum())
    assert i["n_facing"] < i["n_faces"] and i["n_visible_from_implant"] == i["n_faces"]
    assert i["n_sight_clear"] + i["n_sight_one_hit_in_footprint"] == i["n_line_of_sight"]
    assert i["opening"]["area_mm2"] == op.area_mm2
    json.dumps(el.summary())
    # a narrower cone keeps fewer faces, and only ever a subset
    el60 = C.craniotomy_faces(sphere_cavity, op, implant_center_ras=[0.0, 0.0, 10.0], cone_deg=60.0)
    assert el60.n_eligible < el.n_eligible and not (el60.mask & ~el.mask).any()
    with pytest.raises(ValueError):
        C.craniotomy_faces(sphere_cavity, op, cone_deg=0.0)
    with pytest.raises(ValueError):
        C.craniotomy_faces(sphere_cavity, "not an opening")


def test_craniotomy_faces_hidden_lobe_is_not_eligible():
    """Ball A under the hole plus a lateral ball B joined through a neck: the
    far wall of B is behind tissue as seen from the opening."""
    aff, xyz = _grid()
    a = np.linalg.norm(xyz - np.array([0.0, 0.0, 8.0]), axis=-1) <= 12.0
    b = np.linalg.norm(xyz - np.array([19.0, 0.0, 3.0]), axis=-1) <= 9.0
    assert (a & b).sum() > 0, "the lobes must join through a neck"
    mesh = mask_to_mesh(a | b, aff)
    assert mesh.bounds[1, 0] > 26.0, "lobe B must be part of the mesh"
    ang = np.linspace(0.0, 2.0 * np.pi, 36, endpoint=False)
    pts = np.array([[r * np.cos(t), r * np.sin(t), 30.0] for r in np.linspace(0, 8, 6) for t in ang])
    op = C.Opening(centroid_ras=[0, 0, 30.0], normal_ras=[0, 0, 1], points_ras=pts,
                   area_mm2=np.pi * 64.0)
    el = C.craniotomy_faces(mesh, op, implant_center_ras=[4.0, 0.0, 6.0])
    c = mesh.triangles_center
    far_b = c[:, 0] > 25.0
    floor_a = (c[:, 2] < -1.0) & (np.abs(c[:, 0]) < 6.0) & (np.abs(c[:, 1]) < 6.0)
    assert far_b.sum() > 20 and floor_a.sum() > 20
    assert not el.mask[far_b].any()
    assert el.mask[floor_a].mean() > 0.9
    assert el.info["n_sight_one_hit_outside"] + el.info["n_sight_blocked"] > 0


def test_hits_before_matches_the_trimesh_reference():
    m = trimesh.creation.icosphere(subdivisions=4, radius=20.0)
    cents = m.triangles_center
    for origin in ([0.0, 0.0, 35.0], [3.0, -2.0, 1.0], [40.0, 10.0, -5.0]):
        n_a, f_a = C.hits_before(m, origin, cents)
        n_t, f_t = C.hits_before_trimesh(m, origin, cents, chunk=777)
        assert np.array_equal(n_a, n_t)
        assert np.allclose(np.nan_to_num(f_a), np.nan_to_num(f_t), atol=1e-9)
        assert np.array_equal(np.isnan(f_a[:, 0]), n_a == 0)
    n_out, _f = C.hits_before(m, [0.0, 0.0, 35.0], cents)
    assert set(np.unique(n_out)) == {0, 1}            # the near cap is reached directly
    n_in, _f = C.hits_before(m, [0.0, 0.0, 0.0], cents)
    assert not n_in.any()                             # from the centre every face is first
    assert np.array_equal(C.visible_faces(m, [0.0, 0.0, 35.0]),
                          C.visible_faces_trimesh(m, [0.0, 0.0, 35.0]))


# ------------------------------------------------------------- the rules
def test_eligible_faces_rules_and_packing():
    m = trimesh.creation.icosphere(subdivisions=3, radius=20.0)
    n = len(m.faces)
    e_all = C.eligible_faces(m, "all")
    assert e_all.rule == "all" and e_all.mask.all() and e_all.fraction == 1.0
    assert C.eligible_faces(m, None).mask.all()
    e_vis = C.eligible_faces(m, "visible")
    assert e_vis.rule == "visible" and e_vis.mask.all()
    e_vis2 = C.eligible_faces(m, "visible", implant_center_ras=[40.0, 0.0, 0.0])
    assert 0 < e_vis2.n_eligible < n and 0.0 < e_vis2.fraction < 1.0
    idx = np.arange(0, n, 2)
    e_idx = C.eligible_faces(m, idx)
    assert e_idx.rule == "explicit" and e_idx.n_eligible == idx.size
    assert C.eligible_faces(m, e_idx) is e_idx
    with pytest.raises(ValueError, match="unknown rule"):
        C.eligible_faces(m, "sideways")
    with pytest.raises(ValueError, match="needs an Opening"):
        C.eligible_faces(m, "craniotomy")
    packed = C.pack_face_mask(e_idx.mask)
    assert len(packed) == n and set(packed) <= {"0", "1"}
    assert np.array_equal(C.unpack_face_mask(packed), e_idx.mask)
    assert "eligible faces (explicit)" in e_idx.describe()


# ----------------------------------------------------------- the api option
@pytest.fixture()
def toy():
    return pf.toy_instance(n_candidates=36, n_targets=150)


@pytest.fixture()
def fakes(monkeypatch, toy):
    calls = {"build_candidates": 0, "build_influence": 0, "build_conflicts": 0,
             "final_report": 0, "recommend": 0}
    api.clear_cache()
    _install_fakes(monkeypatch, toy, calls)
    yield calls
    api.clear_cache()


def test_optimize_records_the_eligibility(fakes, toy):
    mesh = toy["mesh"]
    n = len(mesh.faces)
    _t, rep = plan.optimize(mesh, 2, report=False, eligible="all")
    p = rep.parameters
    assert p["eligible_rule"] == "all" and p["eligible_fraction"] == 1.0
    assert p["eligible"]["n_faces"] == n and len(p["eligible_mask"]) == n
    assert p["eligible_faces"] == n
    json.dumps(rep.to_dict())
    top = pf.flat_wall_top_faces(mesh)
    _t, rep = plan.optimize(mesh, 2, report=False, eligible=top)
    assert rep.parameters["eligible_rule"] == "explicit"
    assert rep.parameters["eligible_faces"] == int(top.sum())
    assert np.array_equal(C.unpack_face_mask(rep.parameters["eligible_mask"]), top)
    _t, rep = plan.optimize(mesh, 2, report=False, eligible_faces=top)
    assert rep.parameters["eligible_rule"] == "explicit"       # legacy keyword, recorded too
    _t, rep = plan.optimize(mesh, 2, report=False, eligible="visible",
                            implant_center_ras=mesh.centroid)
    assert rep.parameters["eligible_rule"] == "visible"
    pre = C.eligible_faces(mesh, top)
    _t, rep = plan.optimize(mesh, 2, report=False, eligible=pre, eligible_faces=top)
    assert rep.parameters["eligible"] == pre.summary()
    _t, rep = plan.optimize(mesh, 2, report=False)
    assert rep.parameters["eligible_rule"] is None and rep.parameters["eligible_mask"] is None
    with pytest.raises(ValueError, match="disagree"):
        plan.optimize(mesh, 2, report=False, eligible="all", eligible_faces=top)
    with pytest.raises(ValueError, match="needs an Opening"):
        plan.optimize(mesh, 2, report=False, eligible="craniotomy")
