"""MonitorThread: background QThread that scans processes and enforces rules."""
from __future__ import annotations

import os
import time
import logging

import psutil
from PyQt6.QtCore import QThread, pyqtSignal

from rules import RuleEngine
from probalance import ProBalance
import utils

log = logging.getLogger(__name__)


def _resolve_name(comm: str, cmdline: list[str]) -> str:
    """Return the best human-readable process name.

    Wine/Proton processes have comm='Main' (or other generic names) but
    cmdline[0] is the Windows path, e.g.:
      Z:\\...\\PathOfExileSteam.exe
    We detect that and use the Windows basename instead.
    Also handles comm truncated at 15 chars by trying cmdline[0] basename.
    """
    if cmdline:
        arg0 = cmdline[0]
        # Windows path: contains backslash and ends with .exe (case-insensitive)
        if "\\" in arg0 and arg0.lower().endswith(".exe"):
            basename = arg0.replace("\\", "/").rstrip("/").split("/")[-1]
            if basename:
                return basename
        # comm is capped at 15 chars by the kernel; if it looks truncated,
        # try to get the real name from the argv[0] basename
        if len(comm) == 15:
            basename = os.path.basename(arg0)
            if basename and len(basename) > 15:
                return basename
    return comm


def _safe_proc_info(proc: psutil.Process) -> dict | None:
    """Collect process info safely. Returns None if process is gone/denied."""
    try:
        with proc.oneshot():
            pid = proc.pid
            comm = proc.name()
            try:
                cmdline = proc.cmdline()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                cmdline = []
            name = _resolve_name(comm, cmdline)
            cpu = proc.cpu_percent()
            mem = proc.memory_info().rss
            try:
                nice = proc.nice()
            except (psutil.AccessDenied, AttributeError):
                nice = 0
            try:
                affinity = proc.cpu_affinity()
                affinity_str = utils._cpuset_to_cpulist(set(affinity))
            except (psutil.AccessDenied, AttributeError):
                affinity_str = ""
            try:
                ionice = proc.ionice()
                ionice_str = f"{ionice.ioclass}/{ionice.value}"
            except (psutil.AccessDenied, AttributeError):
                ionice_str = ""
            return {
                "pid": pid,
                "name": name,
                "cpu_percent": cpu,
                "mem_rss": mem,
                "nice": nice,
                "affinity": affinity_str,
                "ionice": ionice_str,
            }
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return None


class MonitorThread(QThread):
    """
    Background thread that:
    - Every 0.5s: enforces rules on all matching processes
    - Every 1.0s: runs ProBalance tick
    - Every 2.0s: emits process_snapshot_ready with a copy of the snapshot
    - On new PID: applies matching rule, or default affinity if no rule matched
    """

    process_snapshot_ready = pyqtSignal(list)    # emitted with list of proc dicts
    cpu_snapshot_ready     = pyqtSignal(list)    # emitted with list of per-CPU % floats
    log_message = pyqtSignal(str)                # log lines for UI

    def __init__(self, rule_engine: RuleEngine, probalance: ProBalance, config: dict):
        super().__init__()
        self._rule_engine = rule_engine
        self._probalance = probalance
        self._config = config
        self._stop = False
        self._known_pids: set[int] = set()

        # Track original affinity before we change it, for "Reset All" function.
        # pid → frozenset of CPU numbers that were online when we first touched the process.
        self._original_affinities: dict[int, frozenset] = {}

        # Gaming Mode nice -1 tracking: pid → original nice value before we elevated it
        self._gaming_mode: bool = False
        self._gaming_mode_elevate_nice: bool = False
        self._gaming_niced: dict[int, int] = {}  # pid → original nice

        # Wire log callbacks
        rule_engine.set_log_callback(self._emit_log)
        probalance.set_log_callback(self._emit_log)

    def _emit_log(self, msg: str):
        self.log_message.emit(msg)

    def _default_affinity(self) -> str | None:
        return self._config.get("cpu", {}).get("default_affinity") or None

    def update_config(self, config: dict):
        self._config = config
        self._probalance.update_config(config.get("probalance", {}))

    def reapply_all_defaults(self):
        """Force re-apply default affinity to all currently known PIDs.
        Called when the user changes the default affinity setting."""
        default = self._default_affinity()
        if not default:
            return
        for pid in list(self._known_pids):
            try:
                comm = open(f"/proc/{pid}/comm").read().strip()
                try:
                    cmdline_raw = open(f"/proc/{pid}/cmdline").read().split("\x00")
                except OSError:
                    cmdline_raw = []
                name = _resolve_name(comm, cmdline_raw)
                actions = self._rule_engine.apply_to_process(pid, name)
                if not actions:
                    if utils.set_affinity(pid, default):
                        self._emit_log(f"[Default] affinity={default} → {name}({pid})")
            except OSError:
                pass

    def set_gaming_mode(self, active: bool, elevate_nice: bool):
        """Called from GUI thread when Gaming Mode is toggled.
        If deactivating, restores nice values for all processes we elevated."""
        self._gaming_mode = active
        self._gaming_mode_elevate_nice = elevate_nice
        if not active and self._gaming_niced:
            self._restore_gaming_nices()

    def _restore_gaming_nices(self):
        """Restore nice values to original for all game processes we elevated."""
        import cpu_park
        count = 0
        for pid, orig_nice in list(self._gaming_niced.items()):
            try:
                if cpu_park.set_process_nice_via_helper(pid, orig_nice):
                    count += 1
            except Exception:
                pass
        self._gaming_niced.clear()
        self._emit_log(f"[Gaming Mode] Restored nice for {count} processes.")

    def stop(self):
        self._stop = True

    def reset_all_affinities(self):
        """Restore every process we touched back to its original affinity.
        Called by the GUI 'Reset All Changes' button.
        Processes that have since exited are silently skipped."""
        online = utils.get_cpu_count()
        all_cpus = set(range(online))
        count = 0
        for pid, orig in list(self._original_affinities.items()):
            try:
                # Restore to captured original; fall back to all CPUs
                mask = orig if orig else all_cpus
                os.sched_setaffinity(pid, mask)
                # Restore all threads too
                for tid in utils._get_tids(pid):
                    try:
                        os.sched_setaffinity(tid, mask)
                    except OSError:
                        pass
                count += 1
            except (ProcessLookupError, PermissionError, OSError):
                pass
        self._original_affinities.clear()
        self._emit_log(f"[Reset] Restored affinity on {count} processes to original state.")

    def _capture_original(self, pid: int):
        """Store the current affinity of a process before we change it."""
        if pid in self._original_affinities:
            return
        try:
            self._original_affinities[pid] = frozenset(os.sched_getaffinity(pid))
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _apply_new_pid(self, info: dict):
        """Apply rules or default affinity to a newly seen process."""
        pid = info["pid"]
        name = info["name"]
        self._capture_original(pid)
        actions = self._rule_engine.apply_to_process(pid, name)
        if actions:
            # Rule matched — if gaming mode + elevate_nice, apply nice -1
            if self._gaming_mode and self._gaming_mode_elevate_nice and pid not in self._gaming_niced:
                import cpu_park
                orig_nice = info.get("nice", 0)
                if cpu_park.set_process_nice_via_helper(pid, -1):
                    self._gaming_niced[pid] = orig_nice
                    self._emit_log(f"[Gaming Mode] nice -1 → {name}({pid})")
        else:
            default = self._default_affinity()
            if default:
                if utils.set_affinity(pid, default):
                    self._emit_log(f"[Default] affinity={default} → {name}({pid})")

    def run(self):
        tick_interval = 0.1
        last_enforce = 0.0
        last_probalance = 0.0
        last_snapshot = 0.0
        last_pb_tick = time.monotonic()

        enforce_interval = self._config.get("monitor", {}).get("rule_enforce_interval_ms", 500) / 1000.0
        snapshot_interval = self._config.get("monitor", {}).get("display_refresh_interval_ms", 2000) / 1000.0

        snapshot: list[dict] = []

        while not self._stop:
            now = time.monotonic()

            # Collect current snapshot
            try:
                procs = list(psutil.process_iter())
            except Exception:
                procs = []

            new_snapshot = []
            current_pids = set()
            for proc in procs:
                info = _safe_proc_info(proc)
                if info is not None:
                    new_snapshot.append(info)
                    current_pids.add(info["pid"])

            # Detect new PIDs: apply matching rule OR default affinity
            new_pids = current_pids - self._known_pids
            if new_pids:
                for info in new_snapshot:
                    if info["pid"] in new_pids:
                        self._apply_new_pid(info)
            self._known_pids = current_pids
            snapshot = new_snapshot

            elapsed_enforce = now - last_enforce
            elapsed_pb = now - last_probalance
            elapsed_snap = now - last_snapshot

            # Rule enforcement every 0.5s (rules only — not default, too expensive)
            if elapsed_enforce >= enforce_interval:
                for info in snapshot:
                    self._rule_engine.apply_to_process(info["pid"], info["name"])
                last_enforce = now

            # ProBalance every 1.0s
            if elapsed_pb >= 1.0:
                pb_tick = now - last_pb_tick
                last_pb_tick = now
                self._probalance.tick(snapshot, pb_tick)
                last_probalance = now

            # Snapshot emit every 2.0s
            if elapsed_snap >= snapshot_interval:
                self.process_snapshot_ready.emit(list(snapshot))
                try:
                    cpu_percents = psutil.cpu_percent(percpu=True)
                    self.cpu_snapshot_ready.emit(list(cpu_percents))
                except Exception:
                    pass
                last_snapshot = now

            time.sleep(tick_interval)
