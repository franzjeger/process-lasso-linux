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
    def _run_result(self, returncode, stderr=""):
        class R:
            pass
        r = R()
        r.returncode = returncode
        r.stderr = stderr
        return r

    def test_success(self, monkeypatch):
        cmds = []

        def run(cmd, **kwargs):
            cmds.append(cmd)
            return self._run_result(0)

        monkeypatch.setattr(utils.subprocess, "run", run)
        assert utils.set_nice(42, 10) is True
        assert cmds[0] == ["renice", "-n", "10", "-p", "42"]

    def test_nonzero_exit_returns_false(self, monkeypatch):
        monkeypatch.setattr(utils.subprocess, "run",
                            lambda *a, **k: self._run_result(1, "permission denied"))
        assert utils.set_nice(42, -5) is False

    def test_missing_binary_returns_false(self, monkeypatch):
        def run(*a, **k):
            raise FileNotFoundError("renice")
        monkeypatch.setattr(utils.subprocess, "run", run)
        assert utils.set_nice(42, 5) is False


class TestSetIonice:
    def _capture(self, monkeypatch, returncode=0):
        cmds = []

        class R:
            pass

        def run(cmd, **kwargs):
            cmds.append(cmd)
            r = R()
            r.returncode = returncode
            r.stderr = ""
            return r

        monkeypatch.setattr(utils.subprocess, "run", run)
        return cmds

    def test_best_effort_with_level(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        assert utils.set_ionice(42, 2, 4) is True
        assert cmds[0] == ["ionice", "-c", "2", "-n", "4", "-p", "42"]

    def test_idle_class_omits_level(self, monkeypatch):
        # class 3 (idle) takes no level even when one is given
        cmds = self._capture(monkeypatch)
        assert utils.set_ionice(42, 3, 5) is True
        assert cmds[0] == ["ionice", "-c", "3", "-p", "42"]

    def test_no_level(self, monkeypatch):
        cmds = self._capture(monkeypatch)
        assert utils.set_ionice(42, 1) is True
        assert cmds[0] == ["ionice", "-c", "1", "-p", "42"]

    def test_failure(self, monkeypatch):
        self._capture(monkeypatch, returncode=1)
        assert utils.set_ionice(42, 2, 0) is False
