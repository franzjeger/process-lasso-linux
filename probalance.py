"""ProBalance state machine: throttle CPU hogs, restore when calm."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import utils

log = logging.getLogger(__name__)


@dataclass
class _ProcState:
    state: str = "NORMAL"          # "NORMAL" | "THROTTLED"
    consecutive_high: float = 0.0  # seconds spent above threshold
    consecutive_low: float = 0.0   # seconds spent below restore threshold
    original_nice: Optional[int] = None
    throttle_nice: Optional[int] = None
    method: str = "nice"           # how the active throttle was applied: "nice" | "cgroup"


class ProBalance:
    """Tracks per-process CPU usage and applies/reverts throttling.

    Two throttle backends (config key "throttle_mode"):
      "nice"   — raise the nice value (lowers priority; default)
      "cgroup" — cap CPU time via a cgroup v2 cpu.max quota
                 ("cgroup_limit_percent" of one core); falls back to nice
                 when no delegated cgroup subtree is available.
    """

    def __init__(self, config: dict, log_callback=None, limiter=None):
        self._cfg = config
        self._log_callback = log_callback
        # Keyed by (pid, create_time) so a recycled PID never inherits the
        # state — or the recorded original nice — of a dead process.
        self._states: dict[tuple[int, float], _ProcState] = {}
        self._limiter = limiter  # CgroupLimiter, created lazily when needed

    def update_config(self, config: dict):
        self._cfg = config

    def set_log_callback(self, cb):
        self._log_callback = cb

    def _log(self, msg: str):
        log.info(msg)
        if self._log_callback:
            self._log_callback(msg)

    def _is_exempt(self, name: str) -> bool:
        patterns = self._cfg.get("exempt_patterns", [])
        name_lower = name.lower()
        return any(p.lower() in name_lower for p in patterns)

    def _get_limiter(self):
        if self._limiter is None:
            from cgroup_limit import CgroupLimiter
            self._limiter = CgroupLimiter()
        return self._limiter

    def _throttle(self, pid: int, name: str, cpu: float, current_nice: int,
                  state: _ProcState) -> bool:
        """Apply the configured throttle backend. Returns True on success."""
        if self._cfg.get("throttle_mode", "nice") == "cgroup":
            limiter = self._get_limiter()
            if limiter.available():
                percent = self._cfg.get("cgroup_limit_percent", 100)
                if limiter.limit(pid, percent):
                    state.method = "cgroup"
                    self._log(
                        f"[ProBalance] THROTTLE {name}({pid}) "
                        f"cpu={cpu:.1f}% capped at {percent}% of one core (cgroup)"
                    )
                    return True
                return False
            # No delegated cgroup subtree — fall through to nice

        adjustment = self._cfg.get("nice_adjustment", 10)
        nice_floor = self._cfg.get("nice_floor", 15)
        new_nice = min(current_nice + adjustment, nice_floor)
        if utils.set_nice(pid, new_nice):
            state.method = "nice"
            state.throttle_nice = new_nice
            self._log(
                f"[ProBalance] THROTTLE {name}({pid}) "
                f"cpu={cpu:.1f}% nice {current_nice}→{new_nice}"
            )
            return True
        return False

    def _restore(self, pid: int, name: str, cpu: float, current_nice: int,
                 state: _ProcState):
        if state.method == "cgroup":
            if self._get_limiter().unlimit(pid):
                self._log(f"[ProBalance] RESTORE {name}({pid}) cpu={cpu:.1f}% cgroup cap removed")
        else:
            orig = state.original_nice if state.original_nice is not None else 0
            if utils.set_nice(pid, orig):
                self._log(
                    f"[ProBalance] RESTORE {name}({pid}) "
                    f"cpu={cpu:.1f}% nice {current_nice}→{orig}"
                )
            state.original_nice = orig

    def tick(self, snapshot: list[dict], tick_seconds: float):
        """
        Called every ProBalance update interval.
        snapshot: list of dicts with keys: pid, name, cpu_percent, nice
                  (and create_time when available)
        tick_seconds: elapsed time since last tick
        """
        if not self._cfg.get("enabled", True):
            return

        threshold = self._cfg.get("cpu_threshold_percent", 85.0)
        consec_threshold = self._cfg.get("consecutive_seconds", 3)
        restore_threshold = self._cfg.get("restore_threshold_percent", 40.0)
        restore_hysteresis = self._cfg.get("restore_hysteresis_seconds", 5)

        alive_keys = {(p["pid"], p.get("create_time", 0.0)) for p in snapshot}

        # Clean up dead processes (a recycled PID has a new create_time and
        # therefore a new key — the old entry is dropped here)
        dead = [key for key in self._states if key not in alive_keys]
        for key in dead:
            state = self._states.pop(key)
            if state.state == "THROTTLED" and state.method == "cgroup":
                self._get_limiter().cleanup(key[0])

        for proc in snapshot:
            pid = proc["pid"]
            name = proc["name"]
            cpu = proc.get("cpu_percent", 0.0)
            current_nice = proc.get("nice", 0)
            key = (pid, proc.get("create_time", 0.0))

            if self._is_exempt(name):
                continue

            if key not in self._states:
                self._states[key] = _ProcState(original_nice=current_nice)

            state = self._states[key]

            if state.state == "NORMAL":
                if cpu > threshold:
                    state.consecutive_high += tick_seconds
                    if state.consecutive_high >= consec_threshold:
                        state.original_nice = current_nice
                        if self._throttle(pid, name, cpu, current_nice, state):
                            state.state = "THROTTLED"
                            state.consecutive_high = 0.0
                            state.consecutive_low = 0.0
                else:
                    state.consecutive_high = max(0.0, state.consecutive_high - tick_seconds)

            elif state.state == "THROTTLED":
                if cpu < restore_threshold:
                    state.consecutive_low += tick_seconds
                    if state.consecutive_low >= restore_hysteresis:
                        self._restore(pid, name, cpu, current_nice, state)
                        state.state = "NORMAL"
                        state.consecutive_high = 0.0
                        state.consecutive_low = 0.0
                        state.throttle_nice = None
                else:
                    state.consecutive_low = 0.0

    def get_throttled_pids(self) -> set[int]:
        return {pid for (pid, _ct), s in self._states.items() if s.state == "THROTTLED"}

    def shutdown(self):
        """Revert every active throttle (called on app exit)."""
        for (pid, _ct), state in list(self._states.items()):
            if state.state != "THROTTLED":
                continue
            if state.method == "cgroup":
                self._get_limiter().unlimit(pid)
            else:
                orig = state.original_nice if state.original_nice is not None else 0
                utils.set_nice(pid, orig)
        self._states.clear()
