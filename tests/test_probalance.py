"""Tests for the ProBalance throttle/restore state machine."""
import pytest

import probalance as probalance_mod
from probalance import ProBalance


CFG = {
    "enabled": True,
    "cpu_threshold_percent": 85.0,
    "consecutive_seconds": 3,
    "nice_adjustment": 10,
    "nice_floor": 15,
    "restore_threshold_percent": 40.0,
    "restore_hysteresis_seconds": 5,
    "exempt_patterns": ["kwin", "Xorg"],
}


def proc(pid=100, name="hog", cpu=95.0, nice=0, create_time=1000.0):
    return {"pid": pid, "name": name, "cpu_percent": cpu, "nice": nice,
            "create_time": create_time}


class FakeLimiter:
    def __init__(self, is_available=True, limit_ok=True):
        self.is_available = is_available
        self.limit_ok = limit_ok
        self.limits = []       # (pid, percent)
        self.unlimited = []    # pid
        self.cleaned = []      # pid

    def available(self):
        return self.is_available

    def limit(self, pid, percent):
        if self.limit_ok:
            self.limits.append((pid, percent))
        return self.limit_ok

    def unlimit(self, pid):
        self.unlimited.append(pid)
        return True

    def cleanup(self, pid):
        self.cleaned.append(pid)


@pytest.fixture
def nice_calls(monkeypatch):
    """Mock utils.set_nice; returns the call list. Set nice_calls.ok = False to fail."""
    calls = []

    class Holder:
        ok = True

    def set_nice(pid, nice):
        calls.append((pid, nice))
        return Holder.ok

    monkeypatch.setattr(probalance_mod.utils, "set_nice", set_nice)
    calls_obj = calls
    calls_obj_holder = Holder
    # attach the toggle to the list object for convenient access in tests
    Holder.calls = calls
    return Holder


def make_pb(cfg=None):
    return ProBalance(dict(CFG if cfg is None else cfg))


class TestThrottle:
    def test_throttles_after_consecutive_seconds(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(cpu=95)], 1.0)
        pb.tick([proc(cpu=95)], 1.0)
        assert nice_calls.calls == []          # only 2s above threshold
        pb.tick([proc(cpu=95)], 1.0)
        assert nice_calls.calls == [(100, 10)]  # 0 + adjustment 10
        assert pb.get_throttled_pids() == {100}

    def test_single_long_tick_throttles(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(cpu=95)], 3.0)
        assert pb.get_throttled_pids() == {100}

    def test_no_throttle_below_threshold(self, nice_calls):
        pb = make_pb()
        for _ in range(10):
            pb.tick([proc(cpu=50)], 1.0)
        assert nice_calls.calls == []
        assert pb.get_throttled_pids() == set()

    def test_cpu_exactly_at_threshold_does_not_count(self, nice_calls):
        pb = make_pb()
        for _ in range(10):
            pb.tick([proc(cpu=85.0)], 1.0)   # threshold is strict '>'
        assert pb.get_throttled_pids() == set()

    def test_high_counter_decays_on_dip(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(cpu=95)], 1.0)
        pb.tick([proc(cpu=95)], 1.0)
        pb.tick([proc(cpu=10)], 1.0)   # dip: counter decays 2 → 1
        pb.tick([proc(cpu=95)], 1.0)   # back up: 1 → 2, still below 3
        assert pb.get_throttled_pids() == set()
        pb.tick([proc(cpu=95)], 1.0)   # 2 → 3: throttle
        assert pb.get_throttled_pids() == {100}

    def test_nice_floor_clamps_adjustment(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(cpu=95, nice=12)], 3.0)
        assert nice_calls.calls == [(100, 15)]   # 12+10=22 clamped to floor 15

    def test_failed_set_nice_keeps_state_normal(self, nice_calls):
        nice_calls.ok = False
        pb = make_pb()
        pb.tick([proc(cpu=95)], 3.0)
        assert pb.get_throttled_pids() == set()
        # counter is not reset on failure, so it retries on the very next tick
        pb.tick([proc(cpu=95)], 1.0)
        assert len(nice_calls.calls) == 2

    def test_disabled_config_does_nothing(self, nice_calls):
        cfg = dict(CFG, enabled=False)
        pb = make_pb(cfg)
        pb.tick([proc(cpu=100)], 100.0)
        assert nice_calls.calls == []

    def test_exempt_patterns_case_insensitive_substring(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(name="KWin_x11", cpu=100), proc(pid=101, name="xorg-server", cpu=100)], 10.0)
        assert nice_calls.calls == []


class TestRestore:
    def _throttled_pb(self, nice_calls, original_nice=0):
        pb = make_pb()
        pb.tick([proc(cpu=95, nice=original_nice)], 3.0)
        assert pb.get_throttled_pids() == {100}
        nice_calls.calls.clear()
        return pb

    def test_restores_after_hysteresis(self, nice_calls):
        pb = self._throttled_pb(nice_calls)
        for _ in range(4):
            pb.tick([proc(cpu=10, nice=10)], 1.0)
        assert pb.get_throttled_pids() == {100}   # 4s < 5s hysteresis
        pb.tick([proc(cpu=10, nice=10)], 1.0)
        assert pb.get_throttled_pids() == set()
        assert nice_calls.calls == [(100, 0)]     # restored to original nice

    def test_restore_uses_original_nice(self, nice_calls):
        pb = self._throttled_pb(nice_calls, original_nice=5)
        pb.tick([proc(cpu=10, nice=15)], 5.0)
        assert nice_calls.calls == [(100, 5)]

    def test_spike_resets_low_counter(self, nice_calls):
        pb = self._throttled_pb(nice_calls)
        pb.tick([proc(cpu=10, nice=10)], 4.0)
        pb.tick([proc(cpu=60, nice=10)], 1.0)     # above restore threshold: reset
        pb.tick([proc(cpu=10, nice=10)], 4.0)
        assert pb.get_throttled_pids() == {100}   # counter restarted, 4s < 5s
        pb.tick([proc(cpu=10, nice=10)], 1.0)
        assert pb.get_throttled_pids() == set()

    def test_cpu_at_restore_threshold_does_not_count(self, nice_calls):
        pb = self._throttled_pb(nice_calls)
        pb.tick([proc(cpu=40.0, nice=10)], 100.0)   # strict '<'
        assert pb.get_throttled_pids() == {100}

    def test_state_goes_normal_even_if_restore_nice_fails(self, nice_calls):
        pb = self._throttled_pb(nice_calls)
        nice_calls.ok = False
        pb.tick([proc(cpu=10, nice=10)], 5.0)
        assert pb.get_throttled_pids() == set()

    def test_can_rethrottle_after_restore(self, nice_calls):
        pb = self._throttled_pb(nice_calls)
        pb.tick([proc(cpu=10, nice=10)], 5.0)      # restore
        nice_calls.calls.clear()
        pb.tick([proc(cpu=95, nice=0)], 3.0)       # hog again
        assert pb.get_throttled_pids() == {100}
        assert nice_calls.calls == [(100, 10)]


class TestLifecycle:
    def test_dead_pids_are_cleaned_up(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(pid=100, cpu=95)], 3.0)
        assert pb.get_throttled_pids() == {100}
        pb.tick([proc(pid=200, cpu=5)], 1.0)       # pid 100 gone
        assert pb.get_throttled_pids() == set()

    def test_multiple_processes_tracked_independently(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(pid=100, cpu=95), proc(pid=200, cpu=5)], 3.0)
        assert pb.get_throttled_pids() == {100}

    def test_missing_cpu_key_defaults_to_zero(self, nice_calls):
        pb = make_pb()
        pb.tick([{"pid": 100, "name": "x"}], 10.0)
        assert nice_calls.calls == []

    def test_update_config_takes_effect(self, nice_calls):
        pb = make_pb()
        pb.update_config(dict(CFG, cpu_threshold_percent=50.0))
        pb.tick([proc(cpu=60)], 3.0)
        assert pb.get_throttled_pids() == {100}

    def test_log_callback_invoked_on_throttle(self, nice_calls):
        logged = []
        pb = ProBalance(dict(CFG), log_callback=logged.append)
        pb.tick([proc(cpu=95)], 3.0)
        assert len(logged) == 1
        assert "THROTTLE" in logged[0]

    def test_recycled_pid_does_not_inherit_state(self, nice_calls):
        # Same PID, new create_time → brand-new process: no instant throttle,
        # and the new process's own nice becomes the recorded original.
        pb = make_pb()
        pb.tick([proc(cpu=95, create_time=1000.0)], 2.0)   # almost throttled
        pb.tick([proc(cpu=95, create_time=2000.0)], 1.0)   # recycled PID
        assert pb.get_throttled_pids() == set()            # counter restarted

    def test_recycled_pid_of_throttled_process(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(cpu=95, nice=5, create_time=1000.0)], 3.0)
        assert pb.get_throttled_pids() == {100}
        # PID recycled: old state dropped, new proc tracked from scratch
        pb.tick([proc(cpu=10, nice=0, create_time=2000.0)], 10.0)
        assert pb.get_throttled_pids() == set()
        # No restore was issued for the recycled pid (only the throttle call)
        assert nice_calls.calls == [(100, 15)]


class TestCgroupMode:
    def _cfg(self):
        return dict(CFG, throttle_mode="cgroup", cgroup_limit_percent=50)

    def test_cgroup_throttle_and_restore(self, nice_calls):
        limiter = FakeLimiter()
        pb = ProBalance(self._cfg(), limiter=limiter)
        pb.tick([proc(cpu=95)], 3.0)
        assert limiter.limits == [(100, 50)]
        assert pb.get_throttled_pids() == {100}
        assert nice_calls.calls == []          # nice untouched in cgroup mode

        pb.tick([proc(cpu=10)], 5.0)
        assert limiter.unlimited == [100]
        assert pb.get_throttled_pids() == set()
        assert nice_calls.calls == []

    def test_falls_back_to_nice_when_unavailable(self, nice_calls):
        limiter = FakeLimiter(is_available=False)
        pb = ProBalance(self._cfg(), limiter=limiter)
        pb.tick([proc(cpu=95)], 3.0)
        assert limiter.limits == []
        assert nice_calls.calls == [(100, 10)]
        assert pb.get_throttled_pids() == {100}
        # Restore uses the method the throttle was applied with
        pb.tick([proc(cpu=10, nice=10)], 5.0)
        assert nice_calls.calls == [(100, 10), (100, 0)]
        assert limiter.unlimited == []

    def test_failed_limit_keeps_state_normal(self, nice_calls):
        limiter = FakeLimiter(limit_ok=False)
        pb = ProBalance(self._cfg(), limiter=limiter)
        pb.tick([proc(cpu=95)], 3.0)
        assert pb.get_throttled_pids() == set()

    def test_dead_cgroup_throttled_pid_cleaned_up(self, nice_calls):
        limiter = FakeLimiter()
        pb = ProBalance(self._cfg(), limiter=limiter)
        pb.tick([proc(cpu=95)], 3.0)
        pb.tick([proc(pid=200, cpu=5)], 1.0)   # pid 100 gone
        assert limiter.cleaned == [100]


class TestShutdown:
    def test_shutdown_reverts_nice_throttles(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(cpu=95, nice=3)], 3.0)
        nice_calls.calls.clear()
        pb.shutdown()
        assert nice_calls.calls == [(100, 3)]
        assert pb.get_throttled_pids() == set()

    def test_shutdown_reverts_cgroup_throttles(self, nice_calls):
        limiter = FakeLimiter()
        pb = ProBalance(dict(CFG, throttle_mode="cgroup"), limiter=limiter)
        pb.tick([proc(cpu=95)], 3.0)
        pb.shutdown()
        assert limiter.unlimited == [100]

    def test_shutdown_ignores_normal_processes(self, nice_calls):
        pb = make_pb()
        pb.tick([proc(cpu=10)], 3.0)
        nice_calls.calls.clear()
        pb.shutdown()
        assert nice_calls.calls == []
