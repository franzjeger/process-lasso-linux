"""Tests for config load/save and deep merge in config.py."""
import json

import pytest

import config


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    """Point config at a temp dir; returns the config file path."""
    cfg_dir = tmp_path / "process-lasso"
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", cfg_file)
    return cfg_file


class TestDeepMerge:
    def test_override_wins(self):
        merged = config._deep_merge({"a": 1}, {"a": 2})
        assert merged == {"a": 2}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        override = {"x": {"b": 3}}
        assert config._deep_merge(base, override) == {"x": {"a": 1, "b": 3}}

    def test_missing_keys_filled_from_base(self):
        merged = config._deep_merge({"a": 1, "b": 2}, {"b": 9})
        assert merged == {"a": 1, "b": 9}

    def test_non_dict_override_replaces_dict(self):
        merged = config._deep_merge({"x": {"a": 1}}, {"x": None})
        assert merged == {"x": None}

    def test_inputs_not_mutated(self):
        base = {"x": {"a": [1, 2]}}
        override = {"x": {"b": 3}}
        merged = config._deep_merge(base, override)
        merged["x"]["a"].append(99)
        merged["x"]["b"] = 0
        assert base == {"x": {"a": [1, 2]}}
        assert override == {"x": {"b": 3}}


class TestLoad:
    def test_no_file_returns_defaults(self, tmp_config):
        cfg = config.load()
        assert cfg == config.DEFAULT_CONFIG

    def test_load_returns_copy_of_defaults(self, tmp_config):
        cfg = config.load()
        cfg["probalance"]["enabled"] = "mutated"
        assert config.DEFAULT_CONFIG["probalance"]["enabled"] is True

    def test_corrupt_json_falls_back_to_defaults(self, tmp_config):
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        tmp_config.write_text("{not valid json")
        assert config.load() == config.DEFAULT_CONFIG

    def test_user_values_override_defaults(self, tmp_config):
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        tmp_config.write_text(json.dumps({"probalance": {"enabled": False}}))
        cfg = config.load()
        assert cfg["probalance"]["enabled"] is False
        # sibling keys in the same section still come from defaults
        assert cfg["probalance"]["cpu_threshold_percent"] == 85.0

    def test_old_config_gains_new_default_sections(self, tmp_config):
        # Upgrade path: a config written before "gaming_mode" existed
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        tmp_config.write_text(json.dumps({"version": 1, "rules": [{"name": "r"}]}))
        cfg = config.load()
        assert cfg["gaming_mode"] == {"profiles": {}}
        assert cfg["monitor"]["rule_enforce_interval_ms"] == 500
        assert cfg["rules"] == [{"name": "r"}]

    def test_creates_config_dir(self, tmp_config):
        config.load()
        assert tmp_config.parent.is_dir()


class TestSave:
    def test_round_trip(self, tmp_config):
        cfg = config.load()
        cfg["ui"]["opacity"] = 80
        config.save(cfg)
        assert config.load()["ui"]["opacity"] == 80

    def test_writes_valid_json(self, tmp_config):
        config.save({"a": 1})
        assert json.loads(tmp_config.read_text()) == {"a": 1}

    def test_no_leftover_tmp_file(self, tmp_config):
        config.save({"a": 1})
        assert not tmp_config.with_suffix(".tmp").exists()

    def test_overwrites_existing(self, tmp_config):
        config.save({"a": 1})
        config.save({"b": 2})
        assert json.loads(tmp_config.read_text()) == {"b": 2}
