"""RADAR_DATA_DIR override for runtime config + calibration assets."""

import yaml

from modules.radar import config
from modules.radar.config import CornerRefConfig


def test_paths_fall_back_to_module_without_env(monkeypatch) -> None:
    monkeypatch.delenv(config.DATA_DIR_ENV, raising=False)
    assert config.data_dir() is None
    # Unset → the in-module assets are used (backward compatible).
    assert config.default_config_path() == config._MODULE_DIR / config.DEFAULT_CONFIG_NAME
    assert config.corner_ref_path() == config._MODULE_DIR / config.CORNER_REF_NAME


def test_corner_ref_writes_to_data_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path))
    ref = CornerRefConfig(cross_px=(100.0, 200.0), outside_lower=0.1)

    saved = config.save_corner_ref(ref)

    # Written to the data dir, not next to the code.
    assert saved == tmp_path / config.CORNER_REF_NAME
    assert saved.is_file()
    # And read back from the same place.
    assert config.corner_ref_path() == saved
    loaded = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert loaded["cross_px"] == [100.0, 200.0]


def test_config_reads_data_dir_copy_when_present(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(config.DATA_DIR_ENV, str(tmp_path))
    # No copy in the data dir yet → falls back to the in-module config.
    assert config.default_config_path() == config._MODULE_DIR / config.DEFAULT_CONFIG_NAME
    # Provision a data-dir copy → it wins.
    (tmp_path / config.DEFAULT_CONFIG_NAME).write_text("version: 1\n", encoding="utf-8")
    assert config.default_config_path() == tmp_path / config.DEFAULT_CONFIG_NAME
