"""``gtcore.io.datasets``: dataset ids resolve through ``GT_DATA_ROOT`` and an
optional user-local JSON map; nothing is guessed from the machine and every
failure names the remedy."""
from __future__ import annotations

import json
import os

import pytest

from gtcore.io import datasets as ds
from gtcore.io.datasets import DatasetNotConfigured, canonical_id, dataset_path


@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv(ds.ENV_ROOT, raising=False)
    monkeypatch.delenv(ds.ENV_JSON, raising=False)


def test_unset_root_raises_with_the_remedy(clean_env):
    with pytest.raises(DatasetNotConfigured) as e:
        dataset_path("PH8")
    msg = str(e.value)
    assert ds.ENV_ROOT in msg and ds.ENV_JSON in msg and "datasets.example.json" in msg


def test_root_that_is_not_a_directory_raises(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv(ds.ENV_ROOT, str(tmp_path / "nope"))
    with pytest.raises(DatasetNotConfigured, match="not a directory"):
        dataset_path("PH8")


def test_literal_id_folder_without_json(clean_env, tmp_path, monkeypatch):
    (tmp_path / "PH8").mkdir()
    monkeypatch.setenv(ds.ENV_ROOT, str(tmp_path))
    assert dataset_path("PH8") == tmp_path / "PH8"
    # aliases resolve to the canonical folder
    (tmp_path / "PT-CT1mm").mkdir()
    assert dataset_path("JD-1mm") == tmp_path / "PT-CT1mm"
    assert dataset_path("doe") == tmp_path / "PT-CT1mm"


def test_missing_folder_names_what_was_tried(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv(ds.ENV_ROOT, str(tmp_path))
    with pytest.raises(DatasetNotConfigured) as e:
        dataset_path("PH0")
    msg = str(e.value)
    assert "PH0" in msg and str(tmp_path / "PH0") in msg and ds.ENV_JSON in msg
    # must_exist=False returns the candidate path instead
    assert dataset_path("PH0", must_exist=False) == tmp_path / "PH0"


def test_json_map_relative_absolute_alias_keys_and_comments(clean_env, tmp_path, monkeypatch):
    root = tmp_path / "scans"
    root.mkdir()
    (root / "phantom folder (8 tiles)").mkdir()
    elsewhere = tmp_path / "elsewhere" / "thin cut"
    elsewhere.mkdir(parents=True)
    cfg = tmp_path / "my_datasets.json"
    cfg.write_text(json.dumps({
        "_comment": "ignored",
        "PH8": "phantom folder (8 tiles)",
        "JD-1mm": str(elsewhere),          # alias key, absolute value
    }), encoding="utf-8")
    monkeypatch.setenv(ds.ENV_ROOT, str(root))
    monkeypatch.setenv(ds.ENV_JSON, str(cfg))
    assert dataset_path("PH8") == root / "phantom folder (8 tiles)"
    assert dataset_path("PT-CT1mm") == elsewhere
    assert dataset_path("printed8") == root / "phantom folder (8 tiles)"
    m = ds.load_dataset_map()
    assert "_comment" not in m and set(m) == {"PH8", "PT-CT1mm"}


def test_default_json_under_root_is_picked_up(clean_env, tmp_path, monkeypatch):
    (tmp_path / "ph0 scan").mkdir()
    (tmp_path / ds.DEFAULT_JSON_NAME).write_text(json.dumps({"PH0": "ph0 scan"}),
                                                 encoding="utf-8")
    monkeypatch.setenv(ds.ENV_ROOT, str(tmp_path))
    assert dataset_path("PH0") == tmp_path / "ph0 scan"


def test_cli_overrides_win_over_the_environment(clean_env, tmp_path, monkeypatch):
    env_root = tmp_path / "env"
    cli_root = tmp_path / "cli"
    (env_root / "PH8").mkdir(parents=True)
    (cli_root / "other").mkdir(parents=True)
    cfg = tmp_path / "cli.json"
    cfg.write_text(json.dumps({"PH8": "other"}), encoding="utf-8")
    monkeypatch.setenv(ds.ENV_ROOT, str(env_root))
    assert dataset_path("PH8") == env_root / "PH8"
    assert dataset_path("PH8", root=str(cli_root), datasets_json=str(cfg)) == cli_root / "other"


def test_bad_json_file_raises_clearly(clean_env, tmp_path, monkeypatch):
    monkeypatch.setenv(ds.ENV_ROOT, str(tmp_path))
    monkeypatch.setenv(ds.ENV_JSON, str(tmp_path / "missing.json"))
    with pytest.raises(DatasetNotConfigured, match="does not exist"):
        dataset_path("PH8")
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    monkeypatch.setenv(ds.ENV_JSON, str(bad))
    with pytest.raises(DatasetNotConfigured, match="one JSON object"):
        dataset_path("PH8")
    bad.write_text(json.dumps({"PH8": ""}), encoding="utf-8")
    with pytest.raises(DatasetNotConfigured, match="non-empty"):
        dataset_path("PH8")


def test_canonical_ids_cover_the_documented_set():
    assert canonical_id("JD-2mm") == "PT-CT2mm"
    assert canonical_id("unknown-id") == "unknown-id"
    for alias, target in ds.ALIASES.items():
        assert target in ds.DATASET_IDS, alias


def test_example_json_has_placeholders_only_and_every_id():
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "..", "docs", "datasets.example.json"), encoding="utf-8") as fh:
        ex = json.load(fh)
    keys = {k for k in ex if not k.startswith("_")}
    assert keys == set(ds.DATASET_IDS)
    for k in keys:
        assert ex[k].startswith("<"), k          # placeholders, never a real folder name


def test_module_cli_reports_missing_ids(clean_env, tmp_path, monkeypatch, capsys):
    (tmp_path / "PH8").mkdir()
    monkeypatch.setenv(ds.ENV_ROOT, str(tmp_path))
    assert ds._main(["PH8"]) == 0
    assert ds._main(["PH8", "PH0"]) == 1
    out = capsys.readouterr().out
    assert "PH8" in out and "MISSING" in out
