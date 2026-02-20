"""ProBalance state machine: throttle CPU hogs, restore when calm."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


class ProBalance:
    """Tracks per-process CPU usage and applies/reverts nice throttling."""

    def __init__(self, config: dict, log_callback=None):
        self._cfg = config
        self._log_callback = log_callback
        self._states: dict[int, _ProcState] = {}  # pid → state

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

    def tick(self, snapshot: list[dict], tick_seconds: float):
        """
        Called every ProBalance update interval.
        snapshot: list of dicts with keys: pid, name, cpu_percent, nice
        tick_seconds: elapsed time since last tick
        """
        if not self._cfg.get("enabled", True):
            return

        threshold = self._cfg.get("cpu_threshold_percent", 85.0)
        consec_threshold = self._cfg.get("consecutive_seconds", 3)
        adjustment = self._cfg.get("nice_adjustment", 10)
        nice_floor = self._cfg.get("nice_floor", 15)
        restore_threshold = self._cfg.get("restore_threshold_percent", 40.0)
        restore_hysteresis = self._cfg.get("restore_hysteresis_seconds", 5)

        alive_pids = {p["pid"] for p in snapshot}

        # Clean up dead processes
        dead = [pid for pid in self._states if pid not in alive_pids]
        for pid in dead:
            del self._states[pid]

        for proc in snapshot:
            pid = proc["pid"]
            name = proc["name"]
            cpu = proc.get("cpu_percent", 0.0)
            current_nice = proc.get("nice", 0)

            if self._is_exempt(name):
                continue

            if pid not in self._states:
                self._states[pid] = _ProcState(original_nice=current_nice)

            state = self._states[pid]

            if state.state == "NORMAL":
                if cpu > threshold:
                    state.consecutive_high += tick_seconds
                    if state.consecutive_high >= consec_threshold:
                        # Throttle
                        new_nice = min(current_nice + adjustment, nice_floor)
                        state.original_nice = current_nice
                        if utils.set_nice(pid, new_nice):
                            state.state = "THROTTLED"
                            state.throttle_nice = new_nice
                            state.consecutive_high = 0.0
                            state.consecutive_low = 0.0
                            self._log(
                                f"[ProBalance] THROTTLE {name}({pid}) "
                                f"cpu={cpu:.1f}% nice {current_nice}→{new_nice}"
                            )
                else:
                    state.consecutive_high = max(0.0, state.consecutive_high - tick_seconds)

            elif state.state == "THROTTLED":
                if cpu < restore_threshold:
                    state.consecutive_low += tick_seconds
                    if state.consecutive_low >= restore_hysteresis:
                        # Restore
                        orig = state.original_nice if state.original_nice is not None else 0
                        if utils.set_nice(pid, orig):
                            self._log(
                                f"[ProBalance] RESTORE {name}({pid}) "
                                f"cpu={cpu:.1f}% nice {current_nice}→{orig}"
                            )
                        state.state = "NORMAL"
                        state.consecutive_high = 0.0
                        state.consecutive_low = 0.0
                        state.original_nice = orig
                        state.throttle_nice = None
                else:
                    state.consecutive_low = 0.0

    def get_throttled_pids(self) -> set[int]:
        return {pid for pid, s in self._states.items() if s.state == "THROTTLED"}
