"""A run's cgroup: found only for a lane scope that carries its booking, read, and given a ceiling (lane daemon design,
4.2 and 4.3 b)."""

import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from flotilla.lane import freeze, groups

MANAGER = "/user.slice/user-1000.slice/user@1000.service"
OWN = f"{MANAGER}/app.slice/some-terminal.scope"
UNIT = "flotilla-lane-b7-4242-1.scope"


def tree(tmp_path, *, unit=UNIT, where=f"{MANAGER}/app.slice", booking="b7", high=True):
    root = tmp_path / "cgroup"
    group = root / where.lstrip("/") / unit
    group.mkdir(parents=True)
    if high:
        (group / "memory.high").write_text("max\n")
    (group / "cgroup.procs").write_text("5001\n")
    proc = tmp_path / "proc"
    (proc / "5001").mkdir(parents=True)
    (proc / "5001" / "environ").write_bytes(f"PATH=/bin\0FLOTILLA_LANE_BOOKING={booking}\0".encode())
    shown = f"{where}/{unit}"
    return root, proc, group, shown


def systemctl(answer, code=0):
    def run(argv, **kw):
        return SimpleNamespace(returncode=code, stdout=answer + "\n", stderr="")
    return run


def test_cgroup_of_answers_a_lane_scope_that_carries_the_booking(tmp_path):
    root, proc, group, shown = tree(tmp_path)
    found = groups.cgroup_of(UNIT, "b7", run=systemctl(shown), root=root, proc_root=proc, own=OWN, uid=1000)
    assert found == group


@pytest.mark.parametrize("case", ["pattern", "systemctl", "outside", "other unit", "no high", "no carrier",
                                  "other booking"])
def test_cgroup_of_refuses(tmp_path, case):   # review focus 3
    unit, answer, code = UNIT, None, 0
    if case == "outside":
        root, proc, group, shown = tree(tmp_path, where="/system.slice")
    elif case == "no high":
        root, proc, group, shown = tree(tmp_path, high=False)
    elif case == "other booking":
        root, proc, group, shown = tree(tmp_path, booking="b8")
    else:
        root, proc, group, shown = tree(tmp_path)
    if case == "pattern":
        unit = "../../../etc.scope"
    if case == "systemctl":
        code = 1
    if case == "other unit":
        answer = f"{MANAGER}/app.slice/flotilla-lane-b9-1-1.scope"
        (root / answer.lstrip("/")).mkdir(parents=True)
        (root / answer.lstrip("/") / "memory.high").write_text("max\n")
        (root / answer.lstrip("/") / "cgroup.procs").write_text("5001\n")
    if case == "no carrier":
        (group / "cgroup.procs").write_text("")
    found = groups.cgroup_of(unit, "b7", run=systemctl(answer or shown, code), root=root, proc_root=proc, own=OWN,
                             uid=1000)
    assert found is None


def test_read_parses_the_four_files(tmp_path):
    group = tmp_path / "g"
    group.mkdir()
    (group / "memory.current").write_text("1073741824\n")
    (group / "memory.high").write_text("max\n")
    (group / "memory.events").write_text("low 0\nhigh 7\nmax 0\noom 0\noom_kill 0\n")
    (group / "memory.pressure").write_text("some avg10=42.50 avg60=10.00 avg300=1.00 total=123\n"
                                           "full avg10=40.00 avg60=9.00 avg300=1.00 total=100\n")
    assert groups.read(group) == groups.Reading(1024, None, 7, 42.5)
    (group / "memory.high").write_text("314572800\n")
    (group / "memory.pressure").unlink()
    assert groups.read(group) == groups.Reading(1024, 300, 7, None)
    assert groups.read(tmp_path / "gone") is None


def test_set_high_writes_megabytes_or_max(tmp_path):
    group = tmp_path / "g"
    group.mkdir()
    (group / "memory.high").write_text("max\n")
    assert groups.set_high(group, 512) and (group / "memory.high").read_text() == "512M"
    assert groups.set_high(group, None) and (group / "memory.high").read_text() == "max"
    assert groups.set_high(tmp_path / "gone", 512) is False


def _user_manager() -> bool:
    if not shutil.which("systemd-run") or not shutil.which("systemctl"):
        return False
    return freeze.backend()[0] == freeze.SCOPE


@pytest.mark.skipif(not _user_manager(), reason="no user systemd manager with a memory controller here")
def test_a_real_scope_takes_a_ceiling():
    unit = f"flotilla-lane-test-{os.getpid()}-{int(time.time())}.scope"
    env = {**os.environ, "FLOTILLA_LANE_BOOKING": "btest"}
    child = subprocess.Popen(["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={unit}", "--",
                              "sleep", "30"], env=env)
    try:
        path = None
        for _ in range(50):
            path = groups.cgroup_of(unit, "btest")
            if path is not None:
                break
            time.sleep(0.1)
        assert path is not None, "the scope was made but not found"
        assert groups.set_high(path, 300)
        found = groups.read(path)
        assert found.high_mb == 300 and isinstance(found.pressure, float)
    finally:
        subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True, check=False)
        child.kill()
        child.wait()
