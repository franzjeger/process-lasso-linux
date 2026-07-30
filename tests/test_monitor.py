"""Tests for process-name resolution heuristics in monitor.py."""
import pytest

from monitor import _resolve_name


class TestResolveName:
    def test_plain_process_uses_comm(self):
        assert _resolve_name("firefox", ["/usr/bin/firefox"]) == "firefox"

    def test_empty_cmdline_uses_comm(self):
        assert _resolve_name("kthreadd", []) == "kthreadd"

    def test_wine_windows_path_uses_exe_basename(self):
        arg0 = "Z:\\home\\user\\Games\\PathOfExileSteam.exe"
        assert _resolve_name("Main", [arg0]) == "PathOfExileSteam.exe"

    def test_wine_path_case_insensitive_exe(self):
        arg0 = "C:\\Games\\ATTILA.EXE"
        assert _resolve_name("Main", [arg0]) == "ATTILA.EXE"

    def test_unix_path_ending_in_exe_without_backslash_keeps_comm(self):
        # No backslash → not treated as a Windows path
        assert _resolve_name("wine", ["/opt/game/launcher.exe"]) == "wine"

    def test_truncated_comm_recovered_from_argv0(self):
        # kernel truncates comm at 15 chars
        comm = "steamwebhelper-"          # exactly 15 chars
        arg0 = "/usr/lib/steam/steamwebhelper-launcher"
        assert _resolve_name(comm, [arg0]) == "steamwebhelper-launcher"

    def test_15_char_comm_with_short_basename_keeps_comm(self):
        # basename not longer than 15 → assume comm wasn't truncated
        comm = "exactly15chars_"
        assert _resolve_name(comm, ["/usr/bin/short"]) == comm

    def test_short_comm_not_replaced(self):
        assert _resolve_name("bash", ["/usr/bin/some-very-long-binary-name"]) == "bash"

    def test_path_not_ending_in_exe_keeps_comm(self):
        assert _resolve_name("Main", ["Z:\\Games\\game.exe\\subdir"]) == "Main"
