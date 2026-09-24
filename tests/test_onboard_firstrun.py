import sys
import time
from pathlib import Path

from flotilla.onboard import firstrun

PY = sys.executable


def test_green_run_has_seconds_and_a_summary(tmp_path):
    run = firstrun.run_tier("unit", f"{PY} -c \"print('3 passed in 0.1s')\"", tmp_path, timeout=30)
    assert run.status == "green" and run.seconds is not None and run.summary == "3 passed in 0.1s"


def test_red_run_keeps_the_tail_and_no_seconds(tmp_path):
    run = firstrun.run_tier("unit", f"{PY} -c \"print('boom'); raise SystemExit(1)\"", tmp_path, timeout=30)
    assert run.status == "red" and run.seconds is None and "boom" in run.tail


def test_timeout_kills_the_whole_process_group(tmp_path):
    started = time.monotonic()
    run = firstrun.run_tier("slow", "echo started; sleep 30; echo never", tmp_path, timeout=1)
    assert run.status == "timed-out" and "started" in run.tail
    assert time.monotonic() - started < 10


def test_a_signal_kill_is_killed_not_red(tmp_path):
    run = firstrun.run_tier("oom", "kill -9 $$", tmp_path, timeout=30)
    assert run.status == "killed" and run.seconds is None


def test_the_command_runs_in_the_given_directory(tmp_path):
    (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
    run = firstrun.run_tier("cwd", "cat marker.txt", tmp_path, timeout=30)
    assert run.status == "green" and "here" in run.tail


def test_only_green_runs_are_measured(tmp_path):
    runs = [firstrun.TierRun("unit", "green", 12.3, "5 passed", ""),
            firstrun.TierRun("e2e", "red", None, None, "fail")]
    firstrun.save_measurements(tmp_path, "app-0123456789ab", runs)
    assert firstrun.load_measurements(tmp_path, "app-0123456789ab") == {"unit": 12.3}
    assert firstrun.load_measurements(tmp_path, "other-0123456789ab") == {}


def test_non_utf8_output_does_not_crash(tmp_path):
    run = firstrun.run_tier("bytes", "printf 'ok \\377\\n'", tmp_path, timeout=30)
    assert run.status == "green" and "ok" in run.tail


def test_a_descendant_that_leaves_the_group_does_not_hold_the_run(tmp_path):
    started = time.monotonic()
    detach = f"{PY} -c \"import os, time; os.setsid(); time.sleep(8)\""
    run = firstrun.run_tier("escape", f"echo started; {detach}", tmp_path, timeout=1)
    assert run.status == "timed-out" and time.monotonic() - started < 6


def test_a_tier_reading_stdin_gets_end_of_file(tmp_path):
    # Under pytest stdin is already closed, which would hide the defect: run the tier from a child
    # whose stdin is a pipe that stays open, as it is under a Claude Code session.
    import subprocess
    root = str(Path(__file__).resolve().parent.parent)
    code = ("import sys, time; sys.path.insert(0, %r); from flotilla.onboard import firstrun; "
            "t = time.monotonic(); r = firstrun.run_tier('stdin', 'cat', %r, timeout=20); "
            "print(r.status, round(time.monotonic() - t))" % (root, str(tmp_path)))
    import os
    read_end, write_end = os.pipe()          # the write end stays open in this process: no end of file
    try:
        child = subprocess.Popen([PY, "-c", code], stdin=read_end, stdout=subprocess.PIPE, text=True)
        os.close(read_end)
        out, _ = child.communicate(timeout=60)
    finally:
        os.close(write_end)
    status, seconds = out.split()
    assert status == "green" and int(seconds) < 10
