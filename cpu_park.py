"""CPU core parking: offline non-preferred CPUs (mirrors gamemoderun).

Works on:
  AMD X3D (Ryzen 7950X3D etc.)  — detects preferred CCD by L3 cache size
  Intel hybrid (12th gen+)      — detects P-cores vs E-cores by max freq
  Uniform / other CPUs          — gracefully reports no asymmetry found

Why park instead of sched_setaffinity:
  Parking writes 0 to /sys/devices/system/cpu/cpuN/online. The kernel
  physically removes those CPUs from the scheduler. Every process
  (including the game) sees fewer CPUs from birth, so thread-pool sizing
  is correct and there is no scheduling contention or frametime jitter.
  sched_setaffinity forces existing threads to compete on fewer CPUs —
  that's the source of frametime spikes.

Requires root for sysfs writes. A tiny sudo-whitelisted helper is
installed to /usr/local/bin/process-lasso-sysfs with a NOPASSWD sudoers
rule, so no password prompt at runtime.
"""
from __future__ import annotations

import os
import subprocess
import logging
from dataclasses import dataclass, field
from enum import Enum, auto

log = logging.getLogger(__name__)

# Module-level topology cache: preserved across detect_topology() calls within
# a session.  Once we have a good asymmetric result, we keep it even if a later
# call occurs after Gaming Mode has parked one CCD (making sysfs unreadable).
_topo_cache: "CPUTopology | None" = None

HELPER      = "/usr/local/bin/process-lasso-sysfs"
SUDOERS_FILE = "/etc/sudoers.d/process-lasso"

HELPER_CONTENT = """\
#!/bin/bash
# Process Lasso privileged sysfs helper — managed by process-lasso.
set -euo pipefail
case "$1" in
    cpu-online)
        [[ "$2" =~ ^[0-9]+$ ]] || exit 1
        [[ "$3" =~ ^[01]$   ]] || exit 1
        echo "$3" > "/sys/devices/system/cpu/cpu$2/online"
        ;;
    cpu-unpark-all)
        offline=$(cat /sys/devices/system/cpu/offline 2>/dev/null || true)
        [ -z "$offline" ] && exit 0
        # Expand cpulist (e.g. "8-15,24-31") to individual numbers
        for part in $(echo "$offline" | tr ',' ' '); do
            if [[ "$part" == *-* ]]; then
                lo=${part%-*}; hi=${part#*-}
                for ((c=lo; c<=hi; c++)); do
                    echo 1 > "/sys/devices/system/cpu/cpu${c}/online" 2>/dev/null || true
                done
            else
                echo 1 > "/sys/devices/system/cpu/cpu${part}/online" 2>/dev/null || true
            fi
        done
        ;;
    renice-pid)
        # renice-pid <nice_value> <pid>   (nice_value may be negative)
        [[ "$2" =~ ^-?[0-9]+$ ]] || exit 1
        [[ "$3" =~ ^[0-9]+$ ]]   || exit 1
        renice -n "$2" -p "$3"
        ;;
    *)
        echo "Unknown command: $1" >&2; exit 1 ;;
esac
"""


class TopologyKind(Enum):
    AMD_X3D   = auto()   # asymmetric L3 (V-Cache die vs plain die)
    INTEL_HYBRID = auto()  # P-cores vs E-cores (max-freq asymmetry)
    UNIFORM   = auto()   # all CPUs equal — no preference detectable


@dataclass
class CPUTopology:
    kind: TopologyKind = TopologyKind.UNIFORM
    preferred: set[int]     = field(default_factory=set)  # game CPUs
    non_preferred: set[int] = field(default_factory=set)  # background CPUs
    description: str        = ""

    @property
    def has_asymmetry(self) -> bool:
        return bool(self.non_preferred)


# ── SMT sibling detection ───────────────────────────────────────────────────

def get_smt_siblings_of(cpus: set[int]) -> set[int]:
    """Return the SMT sibling threads within a set of CPUs.

    Reads /sys/.../topology/core_id to group logical CPUs by physical core.
    For each physical core that has 2+ logical CPUs in the set, all but the
    lowest-numbered are considered SMT siblings.
    Returns an empty set when SMT is disabled or there are no siblings.
    """
    core_to_logical: dict[int, list[int]] = {}
    for cpu in sorted(cpus):
        path = f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id"
        try:
            core_id = int(open(path).read().strip())
            core_to_logical.setdefault(core_id, []).append(cpu)
        except (OSError, ValueError):
            pass
    siblings: set[int] = set()
    for logical_cpus in core_to_logical.values():
        if len(logical_cpus) >= 2:
            primary = min(logical_cpus)
            siblings.update(c for c in logical_cpus if c != primary)
    return siblings


# ── Detection ───────────────────────────────────────────────────────────────

def detect_topology() -> CPUTopology:
    """Auto-detect CPU topology.  Tries AMD X3D first, then Intel hybrid.

    Caches the last asymmetric result so topology is preserved even when Gaming
    Mode has parked one CCD, making sysfs L3/freq files unreadable for those CPUs.
    """
    global _topo_cache
    topo = _detect_amd_x3d()
    if topo.has_asymmetry:
        _topo_cache = topo
        return topo
    topo = _detect_intel_hybrid()
    if topo.has_asymmetry:
        _topo_cache = topo
        return topo
    # If live detection returned UNIFORM but we have a cached asymmetric result
    # from earlier in this session (before Gaming Mode parked cores), return it.
    if _topo_cache is not None and _topo_cache.has_asymmetry:
        return _topo_cache
    # True uniform: all CPUs equally capable
    all_cpus = _parse_cpulist_file("/sys/devices/system/cpu/present") or set(range(os.cpu_count() or 1))
    return CPUTopology(
        kind=TopologyKind.UNIFORM,
        preferred=all_cpus,
        description="Uniform topology (no asymmetry detected). All CPUs equal.",
    )


def _detect_amd_x3d() -> CPUTopology:
    """Detect AMD X3D: preferred CCD has larger L3 (3D V-Cache).
    e.g. Ryzen 9 7950X3D: CCD0=96MB (preferred), CCD1=32MB.

    When Gaming Mode is already active (CPUs parked), the offline CPUs' sysfs
    L3 entries are unreadable.  In that case all readable CPUs show the same L3
    (the online CCD).  We detect this by checking whether there are offline CPUs
    — if so, the offline set IS the non-preferred CCD and we return accordingly.
    """
    present = _parse_cpulist_file("/sys/devices/system/cpu/present") or set(range(os.cpu_count() or 1))
    offline = _parse_cpulist_file("/sys/devices/system/cpu/offline")
    l3: dict[int, int] = {}
    for cpu in sorted(present):
        path = f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/size"
        try:
            raw = open(path).read().strip()
            if raw.endswith("K"):
                l3[cpu] = int(raw[:-1])
            elif raw.endswith("M"):
                l3[cpu] = int(raw[:-1]) * 1024
            else:
                l3[cpu] = int(raw)
        except (OSError, ValueError):
            pass  # offline CPU — sysfs entry gone

    if not l3:
        return CPUTopology()

    sizes = set(l3.values())
    if len(sizes) <= 1:
        # All readable (online) CPUs have the same L3.
        # If there are offline CPUs, the other CCD is parked by Gaming Mode —
        # infer: online CPUs = preferred, offline CPUs = non-preferred.
        if offline and l3:
            online_kb = next(iter(sizes))
            online_set = set(l3.keys())
            return CPUTopology(
                kind=TopologyKind.AMD_X3D,
                preferred=online_set,
                non_preferred=offline,
                description=(
                    f"AMD X3D detected (other CCD currently parked). "
                    f"Preferred (V-Cache, {online_kb//1024}MB L3): CPUs {_fmt(online_set)}. "
                    f"Non-preferred (parked): CPUs {_fmt(offline)}."
                ),
            )
        return CPUTopology()   # genuine uniform L3 — not X3D

    max_kb = max(sizes)
    min_kb = min(sizes)
    preferred     = {cpu for cpu, s in l3.items() if s == max_kb}
    non_preferred = {cpu for cpu, s in l3.items() if s == min_kb}

    return CPUTopology(
        kind=TopologyKind.AMD_X3D,
        preferred=preferred,
        non_preferred=non_preferred,
        description=(
            f"AMD X3D detected. "
            f"Preferred (V-Cache, {max_kb//1024}MB L3): CPUs {_fmt(preferred)}. "
            f"Non-preferred ({min_kb//1024}MB L3): CPUs {_fmt(non_preferred)}."
        ),
    )


def _detect_intel_hybrid() -> CPUTopology:
    """Detect Intel hybrid: P-cores run at higher max freq than E-cores.
    e.g. Core i9-13900K: P-cores ~5800 MHz, E-cores ~4300 MHz."""
    present = _parse_cpulist_file("/sys/devices/system/cpu/present") or set(range(os.cpu_count() or 1))
    max_freq: dict[int, int] = {}
    for cpu in sorted(present):
        path = f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq"
        try:
            max_freq[cpu] = int(open(path).read().strip())
        except (OSError, ValueError):
            pass

    if not max_freq:
        return CPUTopology()

    freqs = set(max_freq.values())
    if len(freqs) <= 1:
        return CPUTopology()   # uniform max freq — not hybrid

    # Treat highest-freq CPUs as P-cores (preferred)
    max_f = max(freqs)
    # Use >80% of max as threshold to group P-cores (handles slight freq variance)
    threshold = max_f * 0.80
    preferred    = {cpu for cpu, f in max_freq.items() if f >= threshold}
    non_preferred = {cpu for cpu, f in max_freq.items() if f < threshold}

    max_ghz = max_f / 1_000_000
    min_ghz = min(freqs) / 1_000_000

    return CPUTopology(
        kind=TopologyKind.INTEL_HYBRID,
        preferred=preferred,
        non_preferred=non_preferred,
        description=(
            f"Intel Hybrid detected. "
            f"P-cores ({max_ghz:.1f} GHz max): CPUs {_fmt(preferred)}. "
            f"E-cores ({min_ghz:.1f} GHz max): CPUs {_fmt(non_preferred)}."
        ),
    )


def _fmt(cpus: set[int]) -> str:
    """Format a set of CPUs as compact cpulist string."""
    if not cpus:
        return ""
    sorted_cpus = sorted(cpus)
    ranges, start, end = [], sorted_cpus[0], sorted_cpus[0]
    for c in sorted_cpus[1:]:
        if c == end + 1:
            end = c
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = end = c
    ranges.append(f"{start}-{end}" if start != end else str(start))
    return ",".join(ranges)


# ── Online / offline state ──────────────────────────────────────────────────

def get_online_cpus() -> set[int]:
    return _parse_cpulist_file("/sys/devices/system/cpu/online")


def get_offline_cpus() -> set[int]:
    return _parse_cpulist_file("/sys/devices/system/cpu/offline")


def _parse_cpulist_file(path: str) -> set[int]:
    try:
        raw = open(path).read().strip()
        if not raw:
            return set()
        result = set()
        for part in raw.split(","):
            part = part.strip()
            if "-" in part:
                lo, hi = part.split("-", 1)
                result.update(range(int(lo), int(hi) + 1))
            else:
                result.add(int(part))
        return result
    except (OSError, ValueError):
        return set()


# ── Helper installation ─────────────────────────────────────────────────────

def is_helper_installed() -> bool:
    return os.path.isfile(HELPER) and os.access(HELPER, os.X_OK)


def is_sudoers_installed() -> bool:
    """/etc/sudoers.d/ is 750 root:root — non-root can't check file existence.
    Instead, run `sudo -n helper --check-only`:
      returncode 0 or 1 → sudo let us through (NOPASSWD rule active)
      returncode >1     → sudo demanded a password or helper not found
    """
    if not is_helper_installed():
        return False
    try:
        result = subprocess.run(
            ["sudo", "-n", HELPER, "--check-only"],
            capture_output=True, timeout=3,
        )
        return result.returncode in (0, 1)
    except Exception:
        return False


def is_helper_current() -> bool:
    """True if helper is installed AND has the renice-pid command (up to date)."""
    if not is_helper_installed():
        return False
    try:
        return "renice-pid" in open(HELPER).read()
    except OSError:
        return False


def install_helper_as_root(username: str = "", password: str = "") -> tuple[bool, str]:
    """Write helper + sudoers rule via su root (pty). Returns (ok, msg)."""
    import pty, time, select

    if not username:
        username = os.environ.get("USER") or ""
    if not username:
        return False, "Could not determine current username."
    if not password:
        return False, "No root password provided."
    sudoers_line = f"{username} ALL=(root) NOPASSWD: {HELPER}"

    def run_root(cmd: str) -> tuple[int, str]:
        master, slave = pty.openpty()
        pid = os.fork()
        if pid == 0:
            os.setsid()
            os.dup2(slave, 0); os.dup2(slave, 1); os.dup2(slave, 2)
            os.close(master); os.close(slave)
            os.execv("/bin/su", ["su", "root", "-c", cmd])
        else:
            os.close(slave)
            time.sleep(0.4)
            os.write(master, (password + "\n").encode())
            time.sleep(1.5)
            buf = b""
            while True:
                r, _, _ = select.select([master], [], [], 0.3)
                if not r:
                    break
                try:
                    buf += os.read(master, 4096)
                except OSError:
                    break
            _, status = os.waitpid(pid, 0)
            os.close(master)
            return os.waitstatus_to_exitcode(status), buf.decode(errors="replace")

    # Write helper to tmp
    tmp = "/tmp/pl-sysfs.tmp"
    with open(tmp, "w") as f:
        f.write(HELPER_CONTENT)
    os.chmod(tmp, 0o755)

    cmd = (
        f"cp {tmp} {HELPER} && "
        f"chmod 755 {HELPER} && "
        f"chown root:root {HELPER} && "
        f"printf '%s\\n' '{sudoers_line}' > {SUDOERS_FILE} && "
        f"chmod 440 {SUDOERS_FILE} && "
        f"echo INSTALL_OK"
    )
    rc, out = run_root(cmd)
    if "INSTALL_OK" in out:
        return True, "Helper and sudoers rule installed."
    return False, f"Install failed (rc={rc}): {out.strip()[-300:]}"


# ── Park / Unpark ───────────────────────────────────────────────────────────

def _run_helper(*args: str) -> tuple[bool, str]:
    if not is_helper_installed():
        return False, "Helper not installed. Run install first."
    try:
        result = subprocess.run(
            ["sudo", HELPER] + list(args),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or result.stdout).strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return False, str(e)


def park_cpus(cpus: set[int], log_cb=None) -> bool:
    """Take CPUs offline. Returns True if all succeeded."""
    if not cpus:
        return True
    ok = True
    for cpu in sorted(cpus):
        # CPU 0 cannot be taken offline (bootstrap processor)
        if cpu == 0:
            if log_cb:
                log_cb(f"[Park] Skipping CPU 0 (bootstrap processor, cannot offline)")
            continue
        success, msg = _run_helper("cpu-online", str(cpu), "0")
        if success:
            if log_cb:
                log_cb(f"[Park] CPU {cpu} → offline")
        else:
            log.warning("park cpu%d failed: %s", cpu, msg)
            if log_cb:
                log_cb(f"[Park] CPU {cpu} FAILED: {msg}")
            ok = False
    return ok


def set_process_nice_via_helper(pid: int, nice_val: int) -> bool:
    """Set process nice value via privileged helper (required for negative nice).
    Returns True on success."""
    ok, msg = _run_helper("renice-pid", str(nice_val), str(pid))
    if not ok:
        log.warning("renice-pid pid=%d nice=%d failed: %s", pid, nice_val, msg)
    return ok


def unpark_all(log_cb=None) -> bool:
    """Bring all offline CPUs back online."""
    offline = get_offline_cpus()
    if not offline:
        if log_cb:
            log_cb("[Park] No offline CPUs to restore.")
        return True
    success, msg = _run_helper("cpu-unpark-all")
    if success:
        if log_cb:
            log_cb(f"[Park] CPUs {sorted(offline)} restored online.")
        return True
    log.warning("unpark-all failed: %s", msg)
    if log_cb:
        log_cb(f"[Park] Unpark all FAILED: {msg}")
    return False
