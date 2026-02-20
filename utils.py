"""CPU/priority helpers using direct syscalls where possible."""
from __future__ import annotations

import os
import subprocess
import logging

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
    """Set nice priority via renice.
    Negative values require root — failure logged silently.
    Returns True on success."""
    try:
        result = subprocess.run(
            ["renice", "-n", str(nice), "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            log.debug("renice pid=%d nice=%d: OK", pid, nice)
            return True
        log.warning("renice pid=%d nice=%d failed: %s", pid, nice, result.stderr.strip())
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        log.warning("renice error pid=%d: %s", pid, e)
        return False


def set_ionice(pid: int, ionice_class: int, ionice_level: int | None = None) -> bool:
    """Set I/O priority via ionice.
    class: 1=realtime, 2=best-effort, 3=idle
    level: 0-7 (only for classes 1 and 2)
    Returns True on success."""
    try:
        cmd = ["ionice", "-c", str(ionice_class)]
        if ionice_level is not None and ionice_class in (1, 2):
            cmd += ["-n", str(ionice_level)]
        cmd += ["-p", str(pid)]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            log.debug("ionice pid=%d class=%d level=%s: OK", pid, ionice_class, ionice_level)
            return True
        log.warning("ionice pid=%d class=%d failed: %s", pid, ionice_class, result.stderr.strip())
        return False
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        log.warning("ionice error pid=%d: %s", pid, e)
        return False


def get_cpu_count() -> int:
    """Return number of logical CPUs."""
    return os.cpu_count() or 1


def validate_cpulist(cpulist: str) -> bool:
    """Check that cpulist string is valid (e.g. '0-3,5,7')."""
    if not cpulist or not cpulist.strip():
        return False
    cpu_count = get_cpu_count()
    try:
        cpus = cpulist_to_set(cpulist)
    except ValueError:
        return False
    if not cpus:
        return False
    return all(0 <= c < cpu_count for c in cpus)
