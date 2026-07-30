"""Tests for cpulist parsing, formatting and validation in utils.py."""
import pytest

import utils


class TestCpulistToSet:
    @pytest.mark.parametrize("cpulist,expected", [
        ("0", {0}),
        ("0,1,2", {0, 1, 2}),
        ("0-3", {0, 1, 2, 3}),
        ("0-3,5", {0, 1, 2, 3, 5}),
        ("0-7,16-23", set(range(0, 8)) | set(range(16, 24))),
        (" 0 - 3 , 5 ", {0, 1, 2, 3, 5}),
        ("3,1,2", {1, 2, 3}),
        ("5-5", {5}),
    ])
    def test_valid(self, cpulist, expected):
        assert utils.cpulist_to_set(cpulist) == expected

    def test_empty_string(self):
        assert utils.cpulist_to_set("") == set()

    def test_trailing_comma_ignored(self):
        assert utils.cpulist_to_set("0,1,") == {0, 1}

    def test_reversed_range_yields_empty(self):
        # range(7, 1) is empty — documents current behavior for "7-0" style input
        assert utils.cpulist_to_set("7-0") == set()

    @pytest.mark.parametrize("bad", ["a", "0-x", "1..3", "0;1"])
    def test_malformed_raises(self, bad):
        with pytest.raises(ValueError):
            utils.cpulist_to_set(bad)


class TestCpusetToCpulist:
    @pytest.mark.parametrize("cpus,expected", [
        (set(), ""),
        ({0}, "0"),
        ({0, 1, 2, 3}, "0-3"),
        ({0, 1, 2, 3, 5}, "0-3,5"),
        ({0, 2, 4}, "0,2,4"),
        ({5, 6, 10, 11, 12}, "5-6,10-12"),
    ])
    def test_format(self, cpus, expected):
        assert utils._cpuset_to_cpulist(cpus) == expected

    @pytest.mark.parametrize("cpulist", ["0", "0-3", "0-3,5", "0-7,16-23", "1,3,5"])
    def test_round_trip(self, cpulist):
        cpus = utils.cpulist_to_set(cpulist)
        assert utils.cpulist_to_set(utils._cpuset_to_cpulist(cpus)) == cpus


class TestValidateCpulist:
    @pytest.fixture(autouse=True)
    def _cpu_count_8(self, monkeypatch):
        monkeypatch.setattr(utils, "get_cpu_count", lambda: 8)

    @pytest.mark.parametrize("cpulist,ok", [
        ("0-7", True),
        ("0", True),
        ("7", True),
        ("0-3,5", True),
        ("8", False),          # out of range
        ("0-8", False),        # partially out of range
        ("", False),
        ("   ", False),
        ("abc", False),
        ("7-0", False),        # empty set after parse
        ("-1", False),         # parses as ValueError ("" and "1" split on "-")
    ])
    def test_validate(self, cpulist, ok):
        assert utils.validate_cpulist(cpulist) is ok


class TestGetCpuCount:
    def test_reads_present_file(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-31\n"
        assert utils.get_cpu_count() == 32

    def test_sparse_present_uses_max_plus_one(self, fake_sysfs):
        # A sparse cpulist: count is highest CPU number + 1, not set size
        fake_sysfs["/sys/devices/system/cpu/present"] = "0-3,8-11"
        assert utils.get_cpu_count() == 12

    def test_fallback_to_os_cpu_count(self, fake_sysfs, monkeypatch):
        import os
        monkeypatch.setattr(os, "cpu_count", lambda: 4)
        assert utils.get_cpu_count() == 4

    def test_fallback_when_os_cpu_count_none(self, fake_sysfs, monkeypatch):
        import os
        monkeypatch.setattr(os, "cpu_count", lambda: None)
        assert utils.get_cpu_count() == 1


class TestGetOnlineCpus:
    def test_reads_online_file(self, fake_sysfs):
        fake_sysfs["/sys/devices/system/cpu/online"] = "0-7,16-23\n"
        assert utils.get_online_cpus() == set(range(8)) | set(range(16, 24))

    def test_fallback_to_all_cpus(self, fake_sysfs, monkeypatch):
        monkeypatch.setattr(utils, "get_cpu_count", lambda: 4)
        assert utils.get_online_cpus() == {0, 1, 2, 3}


class TestSetAffinity:
    def test_bad_cpulist_returns_false(self):
        assert utils.set_affinity(12345, "not-a-list") is False

    def test_applies_to_all_threads(self, monkeypatch):
        calls = []
        monkeypatch.setattr(utils, "_get_tids", lambda pid: [100, 101, 102])
        monkeypatch.setattr(utils.os, "sched_setaffinity",
                            lambda tid, cpus: calls.append((tid, cpus)))
        assert utils.set_affinity(100, "0-3") is True
        assert [c[0] for c in calls] == [100, 101, 102]
        assert all(c[1] == {0, 1, 2, 3} for c in calls)

    def test_partial_failure_still_true(self, monkeypatch):
        monkeypatch.setattr(utils, "_get_tids", lambda pid: [100, 101])

        def setaff(tid, cpus):
            if tid == 100:
                raise ProcessLookupError()

        monkeypatch.setattr(utils.os, "sched_setaffinity", setaff)
        assert utils.set_affinity(100, "0") is True

    def test_all_threads_fail_returns_false(self, monkeypatch):
        monkeypatch.setattr(utils, "_get_tids", lambda pid: [100])

        def setaff(tid, cpus):
            raise PermissionError()

        monkeypatch.setattr(utils.os, "sched_setaffinity", setaff)
        assert utils.set_affinity(100, "0") is False


class TestSetNice:
    def test_success(self, monkeypatch):
        calls = []
        monkeypatch.setattr(utils.os, "setpriority",
                            lambda which, pid, nice: calls.append((which, pid, nice)))
        assert utils.set_nice(42, 10) is True
        assert calls == [(utils.os.PRIO_PROCESS, 42, 10)]

    def test_permission_error_returns_false(self, monkeypatch):
        def setpriority(which, pid, nice):
            raise PermissionError()
        monkeypatch.setattr(utils.os, "setpriority", setpriority)
        assert utils.set_nice(42, -5) is False

    def test_dead_process_returns_false(self, monkeypatch):
        def setpriority(which, pid, nice):
            raise ProcessLookupError()
        monkeypatch.setattr(utils.os, "setpriority", setpriority)
        assert utils.set_nice(42, 5) is False


class TestGetNice:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(utils.os, "getpriority", lambda which, pid: 7)
        assert utils.get_nice(42) == 7

    def test_gone_returns_none(self, monkeypatch):
        def getpriority(which, pid):
            raise ProcessLookupError()
        monkeypatch.setattr(utils.os, "getpriority", getpriority)
        assert utils.get_nice(42) is None


class _FakeIONice:
    """Mimics psutil.Process for ionice get/set."""
    def __init__(self, ioclass=0, value=0, fail=False):
        self.ioclass = ioclass
        self.value = value
        self.fail = fail
        self.calls = []

    def __call__(self, pid):     # constructor stand-in: psutil.Process(pid)
        return self

    def ionice(self, *args):
        if self.fail:
            raise utils.psutil.Error("denied")
        if args:
            self.calls.append(args)
            return None
        import collections
        P = collections.namedtuple("pionice", ["ioclass", "value"])
        return P(self.ioclass, self.value)


class TestSetIonice:
    def test_best_effort_with_level(self, monkeypatch):
        fake = _FakeIONice()
        monkeypatch.setattr(utils.psutil, "Process", fake)
        assert utils.set_ionice(42, 2, 4) is True
        assert fake.calls == [(2, 4)]

    def test_idle_class_omits_level(self, monkeypatch):
        # class 3 (idle) takes no level even when one is given
        fake = _FakeIONice()
        monkeypatch.setattr(utils.psutil, "Process", fake)
        assert utils.set_ionice(42, 3, 5) is True
        assert fake.calls == [(3,)]

    def test_no_level(self, monkeypatch):
        fake = _FakeIONice()
        monkeypatch.setattr(utils.psutil, "Process", fake)
        assert utils.set_ionice(42, 1) is True
        assert fake.calls == [(1,)]

    def test_failure(self, monkeypatch):
        monkeypatch.setattr(utils.psutil, "Process", _FakeIONice(fail=True))
        assert utils.set_ionice(42, 2, 0) is False


class TestGetIonice:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(utils.psutil, "Process", _FakeIONice(ioclass=2, value=4))
        assert utils.get_ionice(42) == (2, 4)

    def test_gone_returns_none(self, monkeypatch):
        monkeypatch.setattr(utils.psutil, "Process", _FakeIONice(fail=True))
        assert utils.get_ionice(42) is None


class TestGetAffinitySet:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(utils.os, "sched_getaffinity", lambda pid: {0, 1})
        assert utils.get_affinity_set(42) == {0, 1}

    def test_gone_returns_none(self, monkeypatch):
        def getaff(pid):
            raise ProcessLookupError()
        monkeypatch.setattr(utils.os, "sched_getaffinity", getaff)
        assert utils.get_affinity_set(42) is None
