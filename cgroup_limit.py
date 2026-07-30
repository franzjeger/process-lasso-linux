"""cgroup v2 CPU limiter: hard-cap runaway processes via cpu.max.

Unlike nice (which only lowers scheduling *priority* and does nothing when
CPUs are otherwise idle), a cgroup cpu.max quota actually caps how much CPU
time a process can consume — a spinning hog stays pinned at the limit.

Works without root by using the user's systemd-delegated cgroup subtree
(user@UID.service): we create a `process-lasso-throttle` group inside it
with one child group per throttled PID, so each process gets its own quota.
On restore the process is moved back to the cgroup it came from.

If the subtree isn't delegated or the cpu controller isn't available the
limiter reports unavailable and callers fall back to nice-based throttling.
"""
from __future__ import annotations

import os
import logging

log = logging.getLogger(__name__)

PERIOD_US = 100_000  # standard 100ms cpu.max period


class CgroupLimiter:
    def __init__(
        self,
        cgroup_root: str = "/sys/fs/cgroup",
        proc_root: str = "/proc",
        group_name: str = "process-lasso-throttle",
    ):
        self._cgroup_root = cgroup_root.rstrip("/")
        self._proc_root = proc_root.rstrip("/")
        self._group_name = group_name
        self._group: str | None = None      # throttle group dir, set by _setup
        self._setup_attempted = False
        self._origin: dict[int, str] = {}   # pid → cgroup dir it came from

    # ── Discovery / setup ───────────────────────────────────────────────

    def _pid_cgroup_dir(self, pid: int | str) -> str | None:
        """Absolute cgroup dir of a process (v2 unified hierarchy only)."""
        try:
            for line in open(f"{self._proc_root}/{pid}/cgroup"):
                if line.startswith("0::"):
                    return self._cgroup_root + line.strip()[3:]
        except OSError:
            pass
        return None

    def _delegated_base(self) -> str | None:
        """Nearest ancestor of our own cgroup that systemd delegates to the
        user: the user@UID.service subtree. Fall back to our own cgroup's
        parent (covers running as root or non-systemd setups)."""
        own = self._pid_cgroup_dir("self")
        if not own:
            return None
        parts = own.split("/")
        for i, part in enumerate(parts):
            if part.startswith("user@") and part.endswith(".service"):
                return "/".join(parts[: i + 1])
        return os.path.dirname(own)

    def _setup(self) -> str | None:
        base = self._delegated_base()
        if not base:
            log.info("cgroup limiter unavailable: cannot resolve own cgroup")
            return None
        group = os.path.join(base, self._group_name)
        try:
            controllers = open(os.path.join(base, "cgroup.controllers")).read().split()
            if "cpu" not in controllers:
                log.info("cgroup limiter unavailable: cpu controller not delegated")
                return None
            # cpu must be enabled for children of base (so the throttle group
            # gets it) and of the throttle group (so per-PID children get it).
            enabled = open(os.path.join(base, "cgroup.subtree_control")).read().split()
            if "cpu" not in enabled:
                with open(os.path.join(base, "cgroup.subtree_control"), "w") as f:
                    f.write("+cpu")
            os.makedirs(group, exist_ok=True)
            with open(os.path.join(group, "cgroup.subtree_control"), "w") as f:
                f.write("+cpu")
            return group
        except OSError as e:
            log.info("cgroup limiter unavailable: %s", e)
            return None

    def available(self) -> bool:
        if not self._setup_attempted:
            self._setup_attempted = True
            self._group = self._setup()
        return self._group is not None

    # ── Limit / unlimit ─────────────────────────────────────────────────

    def limit(self, pid: int, percent: float) -> bool:
        """Cap pid at percent of one CPU (100 = one full core, 200 = two).
        Returns True on success; the process keeps running, just capped."""
        if not self.available() or percent <= 0:
            return False
        origin = self._pid_cgroup_dir(pid)
        if origin is None:
            return False
        sub = os.path.join(self._group, str(pid))
        try:
            os.makedirs(sub, exist_ok=True)
            quota = max(1000, int(PERIOD_US * percent / 100))
            with open(os.path.join(sub, "cpu.max"), "w") as f:
                f.write(f"{quota} {PERIOD_US}")
            with open(os.path.join(sub, "cgroup.procs"), "w") as f:
                f.write(str(pid))
            self._origin[pid] = origin
            return True
        except OSError as e:
            log.warning("cgroup limit pid=%d failed: %s", pid, e)
            self._remove_subgroup(pid)
            return False

    def unlimit(self, pid: int) -> bool:
        """Move pid back to its original cgroup and drop its quota group."""
        origin = self._origin.pop(pid, None)
        if origin is None:
            return False
        ok = True
        try:
            with open(os.path.join(origin, "cgroup.procs"), "w") as f:
                f.write(str(pid))
        except OSError as e:
            log.warning("cgroup unlimit pid=%d failed: %s", pid, e)
            ok = False
        self._remove_subgroup(pid)
        return ok

    def cleanup(self, pid: int) -> None:
        """Forget a process that has exited (no move needed, just tidy up)."""
        self._origin.pop(pid, None)
        self._remove_subgroup(pid)

    def limited_pids(self) -> set[int]:
        return set(self._origin)

    def _remove_subgroup(self, pid: int) -> None:
        if self._group is None:
            return
        try:
            os.rmdir(os.path.join(self._group, str(pid)))
        except OSError:
            pass
