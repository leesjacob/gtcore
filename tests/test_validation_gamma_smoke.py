"""Smoke test for ``scripts/validation_gamma.py`` on synthetic data only:
the seed-axis conventions (``--axes physical,si``).

Reuses the synthetic case of ``tests/test_validation_clinical_smoke.py``
(head phantom + RTSTRUCT + an RTDOSE that is gtcore's own TG-43 dose at the
truth seeds with their physical axes on a 2 mm grid).  No ``reconstruct``:
the seed set is the truth seeds, so the physical-axis gamma is the identical
dose case and the S-I rows differ from it only through the axis convention.
"""
from __future__ import annotations

import importlib.util
import os

import numpy as np
import pytest

from gtcore.io import rt

pytest.importorskip("pydicom")
pytest.importorskip("pymedphys")
from test_validation_clinical_smoke import synthetic_case  # noqa: E402,F401  (module-scoped fixture)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_MM = 1.0           # coarse lattice: mechanics, not accuracy


@pytest.fixture(scope="module")
def vg():
    path = os.path.join(ROOT, "scripts", "validation_gamma.py")
    spec = importlib.util.spec_from_file_location("validation_gamma", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows_of(rows, convention, window="30-200Gy"):
    return [r for r in rows if r["axes"] == convention and r["window"] == window]


@pytest.fixture(scope="module")
def both(vg, synthetic_case, tmp_path_factory):
    out = tmp_path_factory.mktemp("gamma_both")
    dv = rt.load_rtdose(synthetic_case["rtdose"])
    c, a = synthetic_case["centers"], synthetic_case["axes"]
    seed_sets = {"gtcore": (c, a, len(c))}
    rows, band_rows, md = vg.run_gamma_table(
        dv, seed_sets, ["physical", "si"], [(3.0, 2.0)], ["global"], str(out), "", "test", False,
        command="test --axes physical,si", sk=3.5, eval_spacing=EVAL_MM, interp_algo="auto",
        max_gamma=2.0, verbose=False)
    p1, p2 = vg.write_pass_rate_tables(str(out), rows, band_rows, "test", False,
                                       command="test --axes physical,si", algo="auto")
    return dict(rows=rows, band_rows=band_rows, md=md, out=str(out), csv=p1, band_csv=p2, dv=dv,
                seed_sets=seed_sets)


def test_convention_axes_and_tags(vg):
    a = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.6, 0.0, 0.8]])
    assert vg.convention_axes(a, "physical") is not None and np.array_equal(vg.convention_axes(a, "physical"), a)
    z = vg.convention_axes(a, "si")
    assert z.shape == (3, 3) and np.array_equal(z, np.tile([0.0, 0.0, 1.0], (3, 1)))
    with pytest.raises(ValueError):
        vg.convention_axes(a, "lateral")
    assert vg.file_tag("gtcore", 3, 2, "global") == "gtcore_3pct_2mm_global"
    assert vg.file_tag("gtcore", 3, 2, "global", "si") == "gtcore_3pct_2mm_global_siaxes"
    assert vg.set_tag("tps_maxima") == "tps_maxima" and vg.set_tag("tps_maxima", "si") == "tps_maxima_siaxes"
    assert vg.AXES_CONVENTIONS == ("physical", "si")
    assert vg.PASS_RATE_COLUMNS[:2] == ["seed_source", "axes"]


def test_both_conventions_in_one_table(both, vg):
    rows = both["rows"]
    assert len(rows) == 4                                   # 2 conventions x 1 set x 1 criterion x 1 norm x 2 windows
    assert {r["axes"] for r in rows} == {"physical", "si"}
    assert all(r["seed_source"] == "gtcore" and r["n_seeds"] == 8 for r in rows)
    phys = _rows_of(rows, "physical")[0]
    si = _rows_of(rows, "si")[0]
    # the RTDOSE is gtcore's own dose at these seeds with their physical axes
    assert phys["pass_rate_pct"] >= 99.5
    assert phys["n_points"] == si["n_points"] > 100       # same reference voxels in both conventions
    # S-I axes are not the truth axes: the dose near the seeds changes
    assert np.isfinite(si["pass_rate_pct"]) and si["pass_rate_pct"] <= phys["pass_rate_pct"]
    assert si["gamma_mean"] > phys["gamma_mean"]
    # band rows carry the convention too
    assert {r["axes"] for r in both["band_rows"]} == {"physical", "si"}
    assert len(both["band_rows"]) == 2 * len(vg.BANDS_GY)
    # the markdown lines name the convention
    assert any("| gtcore | si |" in m for m in both["md"]) and any("| gtcore | physical |" in m for m in both["md"])


def test_files_physical_names_kept_si_suffixed(both):
    out = both["out"]
    assert os.path.isfile(os.path.join(out, "gamma_histogram_gtcore_3pct_2mm_global.csv"))
    assert os.path.isfile(os.path.join(out, "gamma_histogram_gtcore_3pct_2mm_global_siaxes.csv"))
    assert os.path.isfile(os.path.join(out, "gamma_map_slice_gtcore_3pct_2mm_global.npz"))
    z = np.load(os.path.join(out, "gamma_map_slice_gtcore_3pct_2mm_global_siaxes.npz"))
    assert str(z["axes"]) == "si" and str(z["seed_source"]) == "gtcore"
    z0 = np.load(os.path.join(out, "gamma_map_slice_gtcore_3pct_2mm_global.npz"))
    assert str(z0["axes"]) == "physical"
    with open(both["csv"], encoding="utf-8") as f:
        text = f.read()
    head = [ln for ln in text.splitlines() if ln.startswith("#")]
    cols = [ln for ln in text.splitlines() if not ln.startswith("#")][0].split(",")
    assert cols[:2] == ["seed_source", "axes"]
    assert any("# command: test --axes physical,si" == ln for ln in head)
    assert any("axes: physical = PCA" in ln for ln in head)
    data = [ln for ln in text.splitlines() if not ln.startswith("#")][1:]
    assert len(data) == 4 and sum(",si," in ln for ln in data) == 2
    with open(both["band_csv"], encoding="utf-8") as f:
        bcols = [ln for ln in f.read().splitlines() if not ln.startswith("#")][0].split(",")
    assert bcols[:2] == ["seed_source", "axes"]


def test_physical_only_run_is_the_physical_rows(both, vg, tmp_path):
    rows, _b, _m = vg.run_gamma_table(
        both["dv"], both["seed_sets"], ["physical"], [(3.0, 2.0)], ["global"], str(tmp_path), "", "test", False,
        command="test", sk=3.5, eval_spacing=EVAL_MM, interp_algo="auto", max_gamma=2.0, verbose=False)
    assert len(rows) == 2 and all(r["axes"] == "physical" for r in rows)
    for r0, r1 in zip(rows, _rows_of(both["rows"], "physical", "30-200Gy") + _rows_of(both["rows"], "physical", "ge30Gy")):
        for key in ("pass_rate_pct", "n_points", "n_fail", "gamma_mean", "gamma_median", "gamma_p95", "window"):
            assert r0[key] == r1[key], key
    assert not os.path.exists(os.path.join(str(tmp_path), "gamma_histogram_gtcore_3pct_2mm_global_siaxes.csv"))


def test_main_rejects_unknown_convention(vg, synthetic_case):
    with pytest.raises(SystemExit):
        vg.main([synthetic_case["ct"], synthetic_case["rtstruct"], synthetic_case["rtdose"],
                 "--axes", "lateral", "--out", os.path.join(synthetic_case["dir"], "never"), "--full-npz", ""])
