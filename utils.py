"""CPU/priority helpers using direct syscalls where possible."""
from __future__ import annotations

import os
import re
import logging

import psutil

log = logging.getLogger(__name__)


def _get_tids(pid: int) -> list[int]:
    """Return all thread IDs for a process (including the main thread).
    Games like attila.exe have 70+ threads each with their own Linux TID.
    Setting affinity on only the main PID leaves all other threads unrestricted."""
    task_dir = f"/proc/{pid}/task"
    try:
        return [int(t) for t in os.listdir(task_dir)]
    except OSError:
        return [pid]


def cpulist_to_set(cpulist: str) -> set[int]:
    """Parse '0-7,16-23' → {0,1,2,3,4,5,6,7,16,17,18,19,20,21,22,23}."""
    result = set()
    for part in cpulist.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            result.update(range(int(lo.strip()), int(hi.strip()) + 1))
        else:
            result.add(int(part))
    return result


def set_affinity(pid: int, cpulist: str) -> bool:
    """Apply CPU affinity to a process AND all its threads via sched_setaffinity(2).

    Uses os.sched_setaffinity directly (no subprocess) so it's fast enough to
    apply to hundreds of processes for the default-affinity feature.

    Returns True if at least one thread was set successfully."""
    try:
        cpuset = cpulist_to_set(cpulist)
    except ValueError as e:
        log.warning("set_affinity: bad cpulist %r: %s", cpulist, e)
        return False

    tids = _get_tids(pid)
    any_ok = False
    for tid in tids:
        try:
            os.sched_setaffinity(tid, cpuset)
            any_ok = True
        except (PermissionError, ProcessLookupError, OSError) as e:
            log.debug("sched_setaffinity tid=%d: %s", tid, e)

    if any_ok:
        log.debug("affinity pid=%d cpulist=%s: applied to %d threads", pid, cpulist, len(tids))
    return any_ok


def get_affinity_str(pid: int) -> str:
    """Read current affinity of main thread, return as cpulist string."""
    try:
        cpuset = os.sched_getaffinity(pid)
        return _cpuset_to_cpulist(cpuset)
    except (PermissionError, ProcessLookupError, OSError):
        return ""


def _cpuset_to_cpulist(cpus: set[int]) -> str:
    """Convert {0,1,2,3,5} → '0-3,5'."""
    if not cpus:
        return ""
    sorted_cpus = sorted(cpus)
    ranges = []
    start = sorted_cpus[0]
    end = sorted_cpus[0]
    for c in sorted_cpus[1:]:
        if c == end + 1:
            end = c
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = end = c
    ranges.append(f"{start}-{end}" if start != end else str(start))
    return ",".join(ranges)


def set_nice(pid: int, nice: int) -> bool:
    """Set nice priority via setpriority(2) — no subprocess, fast enough to
    call from the enforcement loop.
    Lowering nice (raising priority) requires CAP_SYS_NICE/root.
    Returns True on success."""
    try:
        os.setpriority(os.PRIO_PROCESS, pid, nice)
        log.debug("setpriority pid=%d nice=%d: OK", pid, nice)
        return True
    except (PermissionError, ProcessLookupError, OSError) as e:
        log.warning("setpriority pid=%d nice=%d failed: %s", pid, nice, e)
        return False


def get_nice(pid: int) -> int | None:
    """Return current nice value, or None if the process is gone/denied."""
    try:
        return os.getpriority(os.PRIO_PROCESS, pid)
    except OSError:
        return None


def set_ionice(pid: int, ionice_class: int, ionice_level: int | None = None) -> bool:
    """Set I/O priority via the ioprio_set syscall (through psutil).
    class: 1=realtime, 2=best-effort, 3=idle
    level: 0-7 (only for classes 1 and 2)
    Returns True on success."""
    try:
        if ionice_class in (1, 2) and ionice_level is not None:
            psutil.Process(pid).ionice(ionice_class, ionice_level)
        else:
            psutil.Process(pid).ionice(ionice_class)
        log.debug("ionice pid=%d class=%d level=%s: OK", pid, ionice_class, ionice_level)
        return True
    except (psutil.Error, OSError, ValueError) as e:
        log.warning("ionice pid=%d class=%d failed: %s", pid, ionice_class, e)
        return False


def get_ionice(pid: int) -> tuple[int, int] | None:
    """Return current (class, level), or None if the process is gone/denied."""
    try:
        pri = psutil.Process(pid).ionice()
        return int(pri.ioclass), int(pri.value)
    except (psutil.Error, OSError):
        return None


def get_affinity_set(pid: int) -> set[int] | None:
    """Return the main thread's current CPU set, or None if gone/denied."""
    try:
        return set(os.sched_getaffinity(pid))
    except OSError:
        return None


def _norm_token(s: str) -> str:
    """Lowercase and strip non-alphanumerics: 'Path of Exile' → 'pathofexile'."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def proc_name_matches(game_name: str, pid: int, proc_root: str = "/proc") -> bool:
    """Return True if the process at *pid* looks like it matches *game_name*.

    Normalises both sides (lowercase, alphanumerics only) so that
    'Path of Exile' matches comm 'PathOfExile' (or its 15-char truncated
    variant). Falls back to checking cmdline for Proton/Wine wrappers that
    forward the game exe path."""
    name_n = _norm_token(game_name)
    if not name_n:
        return False
    try:
        comm = open(f"{proc_root}/{pid}/comm").read().strip()
    except OSError:
        return False
    comm_n = _norm_token(comm)
    if comm_n and (name_n in comm_n or comm_n in name_n):
        return True
    try:
        cmdline = open(f"{proc_root}/{pid}/cmdline").read().replace("\x00", " ")
        if name_n in _norm_token(cmdline):
            return True
    except OSError:
        pass
    return False


def get_online_cpus() -> set[int]:
    """Return the set of currently online (non-parked) CPU numbers."""
    try:
        text = open("/sys/devices/system/cpu/online").read().strip()
        return cpulist_to_set(text)
    except (OSError, ValueError):
        return set(range(get_cpu_count()))


def get_cpu_count() -> int:
    """Return total number of logical CPUs, including any currently offline/parked ones.

    Uses /sys/devices/system/cpu/present instead of os.cpu_count() because
    os.cpu_count() only returns ONLINE CPUs — when Gaming Mode parks cores it
    returns 16 instead of 32, which breaks the affinity dialog and validation."""
    try:
        text = open("/sys/devices/system/cpu/present").read().strip()
        cpus = cpulist_to_set(text)
        if cpus:
            return max(cpus) + 1
    except (OSError, ValueError):
        pass
    return os.cpu_count() or 1


def validate_cpulist(cpulist: str) -> bool:
    """Check that cpulist string is valid (e.g. '0-3,5,7')."""
    if not cpulist or not cpulist.strip():
        return False
    max_cpu = get_cpu_count() - 1
    try:
        cpus = cpulist_to_set(cpulist)
    except ValueError:
        return False
    if not cpus:
        return False
    return all(0 <= c <= max_cpu for c in cpus)
