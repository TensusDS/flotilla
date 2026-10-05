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


def test_a_green_run_records_its_peak_memory(tmp_path):
    hold = f"{sys.executable} -c \"x = bytearray(80_000_000); import time; time.sleep(1.5); print('1 passed')\""
    run = firstrun.run_tier("unit", hold, tmp_path, timeout=30)
    assert run.status == "green" and run.peak_mb is not None and run.peak_mb >= 60


def test_peaks_keep_the_larger_and_survive_the_seconds_writer(tmp_path):
    firstrun.measure_peaks(tmp_path, "k", {"unit": 900, "e2e": None})
    firstrun.measure_peaks(tmp_path, "k", {"unit": 400})
    firstrun.measure_once(tmp_path, "k", {"unit": 12.5})
    assert firstrun.load_peaks(tmp_path, "k") == {"unit": 900}
    assert firstrun.load_measurements(tmp_path, "k") == {"unit": 12.5}
    firstrun.measure_peaks(tmp_path, "k", {"unit": 1200})
    assert firstrun.load_peaks(tmp_path, "k") == {"unit": 1200} and firstrun.load_measurements(tmp_path, "k")


def test_a_sampler_thread_that_cannot_start_does_not_cost_the_tier(tmp_path, monkeypatch):
    import threading
    real = threading.Thread.start

    def refuse(self):
        if "GroupPeak" in repr(getattr(self, "_target", "")):
            raise RuntimeError("can't start new thread")
        return real(self)
    monkeypatch.setattr(threading.Thread, "start", refuse)
    run = firstrun.run_tier("unit", "true", tmp_path, timeout=30)
    assert run.status == "green"


def _bad(tmp_path, text):
    path = firstrun._path(tmp_path, "k")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_a_torn_file_is_replaced_by_the_next_green_run(tmp_path):
    """Review of 0.7.14: an undecodable file was skipped forever by the receipt writers, and crashed onboarding,
    which used to overwrite it. Measurements are this machine's cache: any writer starts again from a bad file."""
    _bad(tmp_path, "[seconds]\nunit = ")
    firstrun.measure_once(tmp_path, "k", {"unit": 3.0})
    assert firstrun.load_measurements(tmp_path, "k") == {"unit": 3.0}
    _bad(tmp_path, "[seconds]\nunit = ")
    firstrun.measure_peaks(tmp_path, "k", {"unit": 700})
    assert firstrun.load_peaks(tmp_path, "k") == {"unit": 700}
    _bad(tmp_path, "[seconds]\nunit = ")
    firstrun.save_measurements(tmp_path, "k", [firstrun.TierRun("unit", "green", 4.0, "", "")])
    assert firstrun.load_measurements(tmp_path, "k") == {"unit": 4.0}


def test_values_of_the_wrong_type_are_dropped_not_fatal(tmp_path):
    """A hand-edited `peak_mb = 5` or `unit = "big"` raised TypeError out of the receipt after its tiers ran."""
    _bad(tmp_path, 'peak_mb = 5\n[seconds]\nunit = "slow"\nlint = 2.0\n')
    assert firstrun.load_measurements(tmp_path, "k") == {"lint": 2.0}
    assert firstrun.load_peaks(tmp_path, "k") == {}
    firstrun.measure_peaks(tmp_path, "k", {"unit": 300})
    assert firstrun.load_peaks(tmp_path, "k") == {"unit": 300}
    _bad(tmp_path, '[peak_mb]\nunit = "big"\ne2e = true\n')
    firstrun.measure_peaks(tmp_path, "k", {"unit": 300})
    assert firstrun.load_peaks(tmp_path, "k") == {"unit": 300}


def test_an_unreadable_file_is_named_not_mistaken_for_no_measurement(tmp_path):
    _bad(tmp_path, "[seconds]\nunit = ")
    assert "measurements" in firstrun.measurements_problem(tmp_path, "k")
    assert firstrun.measurements_problem(tmp_path, "absent") == ""


def test_onboarding_keeps_the_larger_peak(tmp_path):
    firstrun.measure_peaks(tmp_path, "k", {"unit": 900})
    firstrun.save_measurements(tmp_path, "k", [firstrun.TierRun("unit", "green", 4.0, "", "", 0, 300)])
    assert firstrun.load_peaks(tmp_path, "k") == {"unit": 900}


def test_a_torn_file_is_repaired_even_when_nothing_new_was_measured(tmp_path):
    _bad(tmp_path, "[seconds]\nunit = ")
    firstrun.measure_peaks(tmp_path, "k", {"unit": None})
    assert firstrun.measurements_problem(tmp_path, "k") == ""
