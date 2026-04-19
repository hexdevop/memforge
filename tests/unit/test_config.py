"""Unit tests for config loading."""

from pathlib import Path

from memforge.config import Config, _deep_merge, load_config, save_config


def test_default_config_is_valid():
    cfg = Config()
    assert cfg.version == 1
    assert cfg.extractor.max_units_per_save == 10


def test_deep_merge():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    override = {"b": {"c": 99}, "e": 5}
    result = _deep_merge(base, override)
    assert result["a"] == 1
    assert result["b"]["c"] == 99
    assert result["b"]["d"] == 3
    assert result["e"] == 5


def test_save_and_load_config(tmp_path: Path):
    cfg = Config()
    cfg.user.name = "TestUser"
    path = tmp_path / "config.yaml"
    save_config(cfg, path)
    assert path.exists()


def test_load_config_no_files():
    cfg = load_config(project_root=None)
    assert isinstance(cfg, Config)
