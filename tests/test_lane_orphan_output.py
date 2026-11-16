"""A run whose command leaves a child holding its output (field notes 2026-11-16: a Unity editor re-parented to init
kept the run's stdout, and the lane held the run for 7 hours past its 30-minute ceiling)."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from flotilla.lane import commands, run as runner

ROOT = Path(__file__).resolve().parents[1]

#: The command forks a grandchild in a session of its own (so the run's process group does not reach it), which
#: keeps the inherited stdout open and sleeps, writing its pid where the test finds it; the command then sleeps
#: too, until the ceiling stops it - or, with `exit`, ends at once.
ORPHAN = ("import subprocess, sys, time\n"
          "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
          "open(sys.argv[1], 'w').write(str(child.pid))\n"
          "print('started', flush=True)\n"
          "time.sleep(0 if sys.argv[2] == 'exit' else 60)\n")

#: `execute` in a process of its own: it makes that process the reaper of the command's orphans, which must not
#: stay true of the test process. Prints the result as JSON.
DRIVER = ("import json, sys\n"
          "from flotilla.lane import run\n"
          "result = run.execute([sys.executable, '-c', sys.argv[1], sys.argv[2], sys.argv[3]],\n"
          "                     out=open('/dev/null', 'w'), ceiling=float(sys.argv[5]),\n"
          "                     reap_orphans=sys.argv[4] == 'reaper')\n"
          "print(json.dumps({'verdict': result.verdict, 'at_ceiling': result.at_ceiling,\n"
          "                  'orphans': result.orphans}))\n")


def drive(tmp_path, *, ending: str, reaper: str = "reaper", ceiling: float = 1.0):
    pid_file = tmp_path / "orphan.pid"
    began = time.monotonic()
    done = subprocess.run([sys.executable, "-c", DRIVER, ORPHAN, str(pid_file), ending, reaper, str(ceiling)],
                          cwd=ROOT, capture_output=True, text=True, timeout=40,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})
    took = time.monotonic() - began
    orphan = int(pid_file.read_text()) if pid_file.exists() else None
    return json.loads(done.stdout.strip().splitlines()[-1]), took, orphan


def alive(pid) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def finish(pid):
    if pid is not None and alive(pid):
        os.kill(pid, 9)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a child subreaper is Linux's")
def test_a_ceiling_ends_the_run_though_an_orphan_holds_its_output(tmp_path):
    result, took, orphan = drive(tmp_path, ending="sleep")
    try:
        assert took < 20, f"the run was held {took:.1f} s after a 1 s ceiling"
        assert result["at_ceiling"] and result["orphans"] == 1
        time.sleep(0.2)
        assert not alive(orphan), "the process that held the output outlived the run"
    finally:
        finish(orphan)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a child subreaper is Linux's")
def test_a_command_that_ends_with_an_orphan_holding_its_output_is_let_go(tmp_path):
    result, took, orphan = drive(tmp_path, ending="exit", ceiling=60)
    try:
        assert took < 20 and result["verdict"] == "green" and result["orphans"] == 1
    finally:
        finish(orphan)


def test_an_orphan_that_cannot_be_found_still_lets_the_run_go(tmp_path):
    """Where this process cannot be the orphans' reaper (macOS), the run is let go and says what it could not end."""
    result, took, orphan = drive(tmp_path, ending="exit", reaper="no-reaper", ceiling=60)
    try:
        assert took < 20 and result["verdict"] == "green" and result["orphans"] is None
    finally:
        finish(orphan)


def test_the_verdict_line_names_what_was_ended_or_left():
    base = dict(exit=0, verdict="green", summary="3 passed", signal=None)
    assert commands.verdict_line(runner.RunResult(**base, orphans=2)).endswith(
        "ended 2 process(es) it left running outside its group")
    assert "may still run" in commands.verdict_line(runner.RunResult(**base, orphans=None))
    assert commands.verdict_line(runner.RunResult(**base)) == "lane run: green - 3 passed"


#: The ceiling test of test_lane_run.py, under the reaper: a grandchild in the command's own group, which dies with
#: it and is re-parented to the lane process first - a zombie there must not keep the group "left" for ever.
GRANDCHILD = ("import subprocess, sys, time\n"
              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              "print('started', flush=True)\n"
              "time.sleep(0 if sys.argv[2] == 'exit' else 60)\n")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a child subreaper is Linux's")
def test_under_the_reaper_a_dead_member_does_not_hold_the_run(tmp_path):
    began = time.monotonic()
    done = subprocess.run([sys.executable, "-c", DRIVER, GRANDCHILD, str(tmp_path / "x"), "sleep", "reaper", "1"],
                          cwd=ROOT, capture_output=True, text=True, timeout=40,
                          env={**os.environ, "PYTHONPATH": str(ROOT)})
    result = json.loads(done.stdout.strip().splitlines()[-1])
    assert time.monotonic() - began < 20 and result["at_ceiling"] and result["orphans"] == 0, done.stderr
