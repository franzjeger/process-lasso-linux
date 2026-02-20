"""Load/save config from ~/.config/process-lasso/config.json."""
from __future__ import annotations

import json
import os
import copy
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "process-lasso"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "version": 1,
    "rules": [],
    "cpu": {
        # Applied to every process not matched by a specific rule.
        # e.g. "8-15,24-31" pushes all background processes to CCD1 while
        # rules for steam/games keep them on CCD0 (3D V-Cache die).
        # null = disabled.
        "default_affinity": None,
    },
    "probalance": {
        "enabled": True,
        "cpu_threshold_percent": 85.0,
        "consecutive_seconds": 3,
        "nice_adjustment": 10,
        "nice_floor": 15,
        "restore_threshold_percent": 40.0,
        "restore_hysteresis_seconds": 5,
        "exempt_patterns": ["kwin", "plasmashell", "systemd", "kthreadd", "Xorg", "xwayland"],
    },
    "monitor": {
        "display_refresh_interval_ms": 2000,
        "rule_enforce_interval_ms": 500,
    },
    "ui": {
        "start_minimized": False,
        "sort_column": "cpu_percent",
        "sort_order": "desc",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base recursively, returning new dict."""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def load() -> dict:
    """Load config, filling missing keys with defaults."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
            return _deep_merge(DEFAULT_CONFIG, data)
        except (json.JSONDecodeError, OSError):
            pass
    return copy.deepcopy(DEFAULT_CONFIG)


def save(config: dict) -> None:
    """Atomically save config to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    tmp.replace(CONFIG_FILE)
