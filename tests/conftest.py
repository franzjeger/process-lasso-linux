"""Shared fixtures: repo path setup, PyQt6 stub, fake sysfs filesystem."""
from __future__ import annotations

import builtins
import io
import sys
import types
from pathlib import Path

import pytest

# Make the repo root importable regardless of where pytest is invoked from.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# monitor.py imports PyQt6 at module level, but nothing we test needs a real
# Qt event loop. Stub the small surface it uses so the module imports headless.
if "PyQt6" not in sys.modules:
    qtcore = types.ModuleType("PyQt6.QtCore")

    class _QThread:
        def __init__(self, *args, **kwargs):
            pass

    def _pyqt_signal(*args, **kwargs):
        return object()

    qtcore.QThread = _QThread
    qtcore.pyqtSignal = _pyqt_signal
    pyqt6 = types.ModuleType("PyQt6")
    pyqt6.QtCore = qtcore
    sys.modules["PyQt6"] = pyqt6
    sys.modules["PyQt6.QtCore"] = qtcore


@pytest.fixture
def fake_sysfs(monkeypatch):
    """Redirect open() of /sys/... paths to an in-memory dict.

    Tests populate the returned dict with path -> file content. Reads of
    /sys paths not in the dict raise FileNotFoundError (like a missing
    sysfs entry for an offline CPU). Non-/sys paths use the real open().
    """
    files: dict[str, str] = {}
    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        p = str(path)
        if p in files:
            return io.StringIO(files[p])
        if p.startswith("/sys/"):
            raise FileNotFoundError(p)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    return files
