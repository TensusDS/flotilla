import os
import subprocess
from pathlib import Path

import pytest

from flotilla.core import platform as plat


def test_windows_refuses_and_names_wsl():
    with pytest.raises(plat.UnsupportedPlatform, match="WSL"):
        plat.require_supported("win32")


def test_unknown_os_refuses():
    with pytest.raises(plat.UnsupportedPlatform):
        plat.require_supported("sunos5")


@pytest.mark.parametrize("name", ["linux", "darwin"])
def test_linux_and_macos_are_supported(name):
    plat.require_supported(name)


def test_probe_uses_procfs_when_present(tmp_path):
    (tmp_path / "self").mkdir()
    (tmp_path / "self" / "stat").write_text("1 (x) S 0\n")
    caps = plat.probe(os_name="linux", proc_root=tmp_path, which=lambda name: None)
    assert caps.parent_pid_source == "procfs"
    assert caps.timeout_command is None


def test_probe_falls_back_to_ps_without_procfs(tmp_path):
    caps = plat.probe(os_name="darwin", proc_root=tmp_path / "absent",
                      which=lambda name: "/opt/homebrew/bin/gtimeout" if name == "gtimeout" else None)
    assert caps.parent_pid_source == "ps"
    assert caps.timeout_command == "gtimeout"


def test_probe_prefers_timeout_over_gtimeout(tmp_path):
    caps = plat.probe(os_name="linux", proc_root=tmp_path, which=lambda name: "/usr/bin/" + name)
    assert caps.timeout_command == "timeout"


def test_procfs_parent_survives_parentheses_and_spaces_in_comm(tmp_path):
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "stat").write_text("123 (we (ird) name) S 45 1 1 0\n")
    assert plat.parent_pid(123, "procfs", proc_root=tmp_path) == 45


def test_procfs_parent_of_a_gone_process_is_none(tmp_path):
    assert plat.parent_pid(999999, "procfs", proc_root=tmp_path) is None


def test_ps_parent_is_parsed():
    fake = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="  4242\n", stderr="")
    assert plat.parent_pid(7, "ps", run=fake) == 4242


def test_ps_parent_of_a_gone_process_is_none():
    fake = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
    assert plat.parent_pid(7, "ps", run=fake) is None


def test_both_sources_agree_with_the_os_on_this_machine():
    # Runs the real ps on every platform, and the real procfs where it exists: the macOS
    # branch is exercised on Linux CI too.
    assert plat.parent_pid(os.getpid(), "ps") == os.getppid()
    if Path("/proc/self/stat").is_file():
        assert plat.parent_pid(os.getpid(), "procfs") == os.getppid()
