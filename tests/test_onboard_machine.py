import datetime as dt
import subprocess

import pytest

from flotilla.onboard import machine


def fake(outputs):
    def run(argv, **kwargs):
        if argv[0] not in outputs:
            raise FileNotFoundError(argv[0])
        rc, out = outputs[argv[0]]
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")
    return run


def test_memory_from_meminfo(tmp_path):
    (tmp_path / "meminfo").write_text("MemTotal:       16384 kB\nMemFree: 1 kB\n")
    assert machine.memory_bytes(proc_root=tmp_path, run=fake({})) == 16384 * 1024


def test_memory_from_sysctl_without_procfs(tmp_path):
    assert machine.memory_bytes(proc_root=tmp_path, run=fake({"sysctl": (0, "34359738368\n")})) == 34359738368


def test_memory_unknown_is_none_not_zero(tmp_path):
    assert machine.memory_bytes(proc_root=tmp_path, run=fake({})) is None


def test_path_python_new_enough():
    got = machine.path_python(run=fake({"python3": (0, "/opt/homebrew/bin/python3\n3.12.4\n")}))
    assert got == {"ok": True, "path": "/opt/homebrew/bin/python3", "version": "3.12.4"}


def test_path_python_system_mac_is_too_old_and_paths_may_hold_spaces():
    got = machine.path_python(run=fake({"python3": (0, "/Applications/Xcode 26.app/usr/bin/python3\n3.9.6\n")}))
    assert got["ok"] is False and got["version"] == "3.9.6"
    assert got["path"] == "/Applications/Xcode 26.app/usr/bin/python3"


def test_path_python_missing_is_an_error_not_ok():
    got = machine.path_python(run=fake({}))
    assert got["ok"] is False and "could not be run" in got["error"]


@pytest.mark.parametrize("have_gh, rc, expected",
                         [(False, 0, "missing"), (True, 1, "installed"), (True, 0, "authenticated")])
def test_gh_state(have_gh, rc, expected):
    which = (lambda name: "/usr/bin/gh") if have_gh else (lambda name: None)
    assert machine.gh_state(which=which, run=fake({"gh": (rc, "")})) == expected


def test_measure_leaves_unknown_memory_out(tmp_path):
    data = machine.measure_machine(
        os_name="darwin", proc_root=tmp_path / "none", which=lambda name: None,
        run=fake({"python3": (0, "/usr/bin/python3\n3.9.6\n")}),
        now=dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc))
    assert "memory_bytes" not in data
    assert data["parent_pid_source"] == "ps" and data["timeout_command"] == "none" and data["gh"] == "missing"
    assert data["measured_at"] == "2026-09-23T00:00:00+00:00"
    assert data["python3"]["ok"] is False


def test_write_then_read(tmp_path):
    data = {"schema": 1, "os": "linux", "python3": {"ok": True, "path": "/usr/bin/python3", "version": "3.12.1"}}
    path = machine.write_machine(tmp_path / "state", data)
    assert path.read_text(encoding="utf-8").startswith("# Measured by")
    assert machine.read_machine(tmp_path / "state") == data


def test_read_missing_is_none(tmp_path):
    assert machine.read_machine(tmp_path) is None


def test_measure_this_machine_has_the_shape():
    data = machine.measure_machine()
    assert data["os"] in ("linux", "darwin")
    assert "version" in data["python3"] or "error" in data["python3"]
