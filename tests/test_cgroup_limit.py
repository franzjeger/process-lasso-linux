"""Tests for the cgroup v2 CPU limiter using a fake cgroup/proc tree."""
import os

import pytest

from cgroup_limit import CgroupLimiter, PERIOD_US


@pytest.fixture
def fake_tree(tmp_path):
    """Build a systemd-style delegated cgroup layout plus a fake /proc.

    Returns (limiter, base_dir, app_scope_dir, proc_dir).
    """
    cg = tmp_path / "cgroup"
    base = cg / "user.slice" / "user-1000.slice" / "user@1000.service"
    scope = base / "app.slice" / "app-lasso.scope"
    scope.mkdir(parents=True)
    (base / "cgroup.controllers").write_text("cpuset cpu io memory pids")
    (base / "cgroup.subtree_control").write_text("memory pids")

    proc = tmp_path / "proc"
    rel = "/" + str(scope.relative_to(cg))
    (proc / "self").mkdir(parents=True)
    (proc / "self" / "cgroup").write_text(f"0::{rel}\n")

    limiter = CgroupLimiter(cgroup_root=str(cg), proc_root=str(proc))
    return limiter, base, scope, proc


def add_proc(proc_dir, pid, cgroup_dir, cgroup_root):
    rel = "/" + str(cgroup_dir.relative_to(cgroup_root))
    d = proc_dir / str(pid)
    d.mkdir()
    (d / "cgroup").write_text(f"0::{rel}\n")


class TestSetup:
    def test_available_creates_throttle_group(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        assert limiter.available() is True
        group = base / "process-lasso-throttle"
        assert group.is_dir()
        # cpu controller enabled for base children and throttle-group children
        assert "+cpu" in (base / "cgroup.subtree_control").read_text()
        assert "+cpu" in (group / "cgroup.subtree_control").read_text()

    def test_unavailable_without_cpu_controller(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        (base / "cgroup.controllers").write_text("memory pids")
        assert limiter.available() is False

    def test_unavailable_without_proc_cgroup(self, tmp_path):
        limiter = CgroupLimiter(cgroup_root=str(tmp_path), proc_root=str(tmp_path / "noproc"))
        assert limiter.available() is False

    def test_setup_only_attempted_once(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        assert limiter.available() is True
        # Breaking the tree afterwards doesn't flip availability (cached)
        (base / "cgroup.controllers").write_text("memory")
        assert limiter.available() is True

    def test_fallback_base_without_systemd_user_service(self, tmp_path):
        # No user@UID.service ancestor: falls back to own cgroup's parent
        cg = tmp_path / "cgroup"
        parent = cg / "somewhere"
        own = parent / "me"
        own.mkdir(parents=True)
        (parent / "cgroup.controllers").write_text("cpu")
        (parent / "cgroup.subtree_control").write_text("")
        proc = tmp_path / "proc"
        (proc / "self").mkdir(parents=True)
        (proc / "self" / "cgroup").write_text("0::/somewhere/me\n")
        limiter = CgroupLimiter(cgroup_root=str(cg), proc_root=str(proc))
        assert limiter.available() is True
        assert (parent / "process-lasso-throttle").is_dir()


class TestLimit:
    def test_limit_moves_pid_and_sets_quota(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        add_proc(proc, 42, scope, base.parent.parent.parent)
        assert limiter.limit(42, 50) is True

        sub = base / "process-lasso-throttle" / "42"
        assert (sub / "cpu.max").read_text() == f"{PERIOD_US // 2} {PERIOD_US}"
        assert (sub / "cgroup.procs").read_text() == "42"
        assert limiter.limited_pids() == {42}

    def test_limit_200_percent(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        add_proc(proc, 42, scope, base.parent.parent.parent)
        assert limiter.limit(42, 200) is True
        sub = base / "process-lasso-throttle" / "42"
        assert (sub / "cpu.max").read_text() == f"{PERIOD_US * 2} {PERIOD_US}"

    def test_limit_dead_pid_fails(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        assert limiter.limit(99, 50) is False   # no /proc/99/cgroup
        assert limiter.limited_pids() == set()

    def test_limit_zero_percent_rejected(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        add_proc(proc, 42, scope, base.parent.parent.parent)
        assert limiter.limit(42, 0) is False

    def test_unlimit_moves_pid_back(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        add_proc(proc, 42, scope, base.parent.parent.parent)
        limiter.limit(42, 50)
        assert limiter.unlimit(42) is True
        # Process moved back to the cgroup it came from
        assert (scope / "cgroup.procs").read_text() == "42"
        assert limiter.limited_pids() == set()

    def test_unlimit_unknown_pid(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        assert limiter.unlimit(42) is False

    def test_cleanup_forgets_without_moving(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        add_proc(proc, 42, scope, base.parent.parent.parent)
        limiter.limit(42, 50)
        limiter.cleanup(42)
        assert limiter.limited_pids() == set()
        assert not (scope / "cgroup.procs").exists()   # no move happened

    def test_limit_unavailable_returns_false(self, fake_tree):
        limiter, base, scope, proc = fake_tree
        (base / "cgroup.controllers").write_text("memory")
        assert limiter.limit(42, 50) is False
