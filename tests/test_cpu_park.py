"""Tests for CPU topology detection and parking logic in cpu_park.py."""
import pytest

import cpu_park
from cpu_park import CPUTopology, TopologyKind


@pytest.fixture(autouse=True)
def _reset_topo_cache():
    cpu_park._topo_cache = None
    yield
    cpu_park._topo_cache = None


def sysfs_x3d(files, n=32, vcache_ccd=range(0, 8), plain_ccd=range(8, 16)):
    """Populate a fake 7950X3D-style layout: 16 cores / 32 threads, 2 CCDs.

    vcache_ccd/plain_ccd are physical core ranges; SMT sibling of core c is c+16.
    """
    files["/sys/devices/system/cpu/present"] = f"0-{n-1}"
    files["/sys/devices/system/cpu/online"] = f"0-{n-1}"
    for core in vcache_ccd:
        for cpu in (core, core + 16):
            files[f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/size"] = "98304K"
    for core in plain_ccd:
        for cpu in (core, core + 16):
            files[f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/size"] = "32768K"
    for core in list(vcache_ccd) + list(plain_ccd):
        for cpu in (core, core + 16):
            files[f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id"] = str(core)


class TestParseCpulistFile:
    def test_range(self, fake_sysfs):
        fake_sysfs["/sys/x"] = "0-3,8\n"
        assert cpu_park._parse_cpulist_file("/sys/x") == {0, 1, 2, 3, 8}

    def test_empty_file(self, fake_sysfs):
        fake_sysfs["/sys/x"] = "\n"
        assert cpu_park._parse_cpulist_file("/sys/x") == set()

    def test_missing_file(self, fake_sysfs):
        assert cpu_park._parse_cpulist_file("/sys/missing") == set()

    def test_garbage_returns_empty(self, fake_sysfs):
        fake_sysfs["/sys/x"] = "garbage"
        assert cpu_park._parse_cpulist_file("/sys/x") == set()


class TestFmt:
    def test_ranges(self):
        assert cpu_park._fmt({0, 1, 2, 3, 8, 10, 11}) == "0-3,8,10-11"

    def test_empty(self):
        assert cpu_park._fmt(set()) == ""


class TestAmdX3dDetection:
    def test_detects_vcache_ccd_as_preferred(self, fake_sysfs):
        sysfs_x3d(fake_sysfs)
        topo = cpu_park._detect_amd_x3d()
        assert topo.kind == TopologyKind.AMD_X3D
        assert topo.preferred == set(range(0, 8)) | set(range(16, 24))
        assert topo.non_preferred == set(range(8, 16)) | set(range(24, 32))

    def test_uniform_l3_not_x3d(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-7"
        for cpu in range(8):
            fake_sysfs[f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/size"] = "32768K"
        topo = cpu_park._detect_amd_x3d()
        assert not topo.has_asymmetry

    def test_no_l3_info(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-7"
        topo = cpu_park._detect_amd_x3d()
        assert not topo.has_asymmetry

    def test_l3_size_units(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-1"
        fake_sysfs["/sys/devices/system/cpu/cpu0/cache/index3/size"] = "96M"
        fake_sysfs["/sys/devices/system/cpu/cpu1/cache/index3/size"] = "32768K"
        topo = cpu_park._detect_amd_x3d()
        assert topo.preferred == {0}
        assert topo.non_preferred == {1}

    def test_parked_ccd_inferred_from_offline(self, fake_sysfs):
        # Gaming Mode active: CCD1 parked, its sysfs L3 entries unreadable.
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-31"
        fake_sysfs["/sys/devices/system/cpu/offline"] = "8-15,24-31"
        for cpu in list(range(0, 8)) + list(range(16, 24)):
            fake_sysfs[f"/sys/devices/system/cpu/cpu{cpu}/cache/index3/size"] = "98304K"
        topo = cpu_park._detect_amd_x3d()
        assert topo.kind == TopologyKind.AMD_X3D
        assert topo.preferred == set(range(0, 8)) | set(range(16, 24))
        assert topo.non_preferred == set(range(8, 16)) | set(range(24, 32))


class TestIntelHybridDetection:
    def test_detects_p_and_e_cores(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-7"
        for cpu in range(4):
            fake_sysfs[f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq"] = "5800000"
        for cpu in range(4, 8):
            fake_sysfs[f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq"] = "4300000"
        topo = cpu_park._detect_intel_hybrid()
        assert topo.kind == TopologyKind.INTEL_HYBRID
        assert topo.preferred == {0, 1, 2, 3}
        assert topo.non_preferred == {4, 5, 6, 7}

    def test_slight_freq_variance_grouped_as_p_cores(self, fake_sysfs):
        # within 80% of max counts as preferred
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-2"
        fake_sysfs["/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"] = "5800000"
        fake_sysfs["/sys/devices/system/cpu/cpu1/cpufreq/cpuinfo_max_freq"] = "5600000"
        fake_sysfs["/sys/devices/system/cpu/cpu2/cpufreq/cpuinfo_max_freq"] = "4300000"
        topo = cpu_park._detect_intel_hybrid()
        assert topo.preferred == {0, 1}
        assert topo.non_preferred == {2}

    def test_uniform_freq_not_hybrid(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-3"
        for cpu in range(4):
            fake_sysfs[f"/sys/devices/system/cpu/cpu{cpu}/cpufreq/cpuinfo_max_freq"] = "4000000"
        assert not cpu_park._detect_intel_hybrid().has_asymmetry


class TestDetectTopology:
    def test_x3d_wins(self, fake_sysfs):
        sysfs_x3d(fake_sysfs)
        topo = cpu_park.detect_topology()
        assert topo.kind == TopologyKind.AMD_X3D

    def test_uniform_fallback(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-7"
        topo = cpu_park.detect_topology()
        assert topo.kind == TopologyKind.UNIFORM
        assert topo.preferred == set(range(8))
        assert not topo.has_asymmetry

    def test_cache_survives_parked_state(self, fake_sysfs):
        # First detection: full X3D visible. Then sysfs degrades to uniform
        # (as when cores are parked) — cached asymmetric result is returned.
        sysfs_x3d(fake_sysfs)
        first = cpu_park.detect_topology()
        assert first.has_asymmetry

        fake_sysfs.clear()
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-31"
        second = cpu_park.detect_topology()
        assert second is first

    def test_no_stale_cache_without_prior_detection(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-3"
        assert cpu_park.detect_topology().kind == TopologyKind.UNIFORM


class TestSmtSiblings:
    def test_siblings_detected(self, fake_sysfs):
        # cores 0,1 each with 2 threads: (0,2) and (1,3)
        for cpu, core in [(0, 0), (1, 1), (2, 0), (3, 1)]:
            fake_sysfs[f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id"] = str(core)
        assert cpu_park.get_smt_siblings_of({0, 1, 2, 3}) == {2, 3}

    def test_no_smt(self, fake_sysfs):
        for cpu in range(4):
            fake_sysfs[f"/sys/devices/system/cpu/cpu{cpu}/topology/core_id"] = str(cpu)
        assert cpu_park.get_smt_siblings_of({0, 1, 2, 3}) == set()

    def test_unreadable_core_ids_ignored(self, fake_sysfs):
        assert cpu_park.get_smt_siblings_of({0, 1}) == set()


class TestParkUnpark:
    @pytest.fixture
    def helper(self, monkeypatch):
        """Mock the privileged helper; records calls, configurable result."""
        calls = []

        class H:
            ok = True
            msg = ""

        def run_helper(*args):
            calls.append(args)
            return H.ok, H.msg

        monkeypatch.setattr(cpu_park, "_run_helper", run_helper)
        monkeypatch.setattr(cpu_park, "is_helper_installed", lambda: True)
        H.calls = calls
        return H

    def test_park_offlines_each_cpu(self, helper):
        assert cpu_park.park_cpus({8, 9}) is True
        assert helper.calls == [("cpu-online", "8", "0"), ("cpu-online", "9", "0")]

    def test_park_skips_cpu0(self, helper):
        logs = []
        assert cpu_park.park_cpus({0, 1}, log_cb=logs.append) is True
        assert helper.calls == [("cpu-online", "1", "0")]
        assert any("CPU 0" in m for m in logs)

    def test_park_empty_set_is_success(self, helper):
        assert cpu_park.park_cpus(set()) is True
        assert helper.calls == []

    def test_park_failure_returns_false_but_continues(self, helper):
        helper.ok = False
        helper.msg = "denied"
        assert cpu_park.park_cpus({1, 2}) is False
        assert len(helper.calls) == 2   # still attempted every CPU

    def test_unpark_all_noop_when_nothing_offline(self, helper, monkeypatch):
        monkeypatch.setattr(cpu_park, "get_offline_cpus", lambda: set())
        assert cpu_park.unpark_all() is True
        assert helper.calls == []

    def test_unpark_all_runs_helper(self, helper, monkeypatch):
        monkeypatch.setattr(cpu_park, "get_offline_cpus", lambda: {8, 9})
        assert cpu_park.unpark_all() is True
        assert helper.calls == [("cpu-unpark-all",)]

    def test_unpark_all_failure(self, helper, monkeypatch):
        monkeypatch.setattr(cpu_park, "get_offline_cpus", lambda: {8})
        helper.ok = False
        assert cpu_park.unpark_all() is False

    def test_run_helper_requires_installation(self, monkeypatch):
        monkeypatch.setattr(cpu_park, "is_helper_installed", lambda: False)
        ok, msg = cpu_park._run_helper("cpu-online", "1", "0")
        assert ok is False
        assert "not installed" in msg.lower()
