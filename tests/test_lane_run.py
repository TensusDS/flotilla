import io
import sys

from flotilla.lane import run


def execute(code):
    out = io.StringIO()
    return run.execute([sys.executable, "-c", code], out=out), out.getvalue()


def test_a_green_run_keeps_its_summary_line():
    result, out = execute("print('collected 3 items'); print('===== 3 passed in 0.10s =====')")
    assert (result.exit, result.verdict, result.summary) == (0, "green", "3 passed in 0.10s")
    assert "collected 3 items" in out


def test_a_red_run_keeps_its_own_exit_code():
    result, _ = execute("import sys; print('1 failed, 2 passed in 0.2s'); sys.exit(1)")
    assert (result.exit, result.verdict, result.summary) == (1, "red", "1 failed, 2 passed in 0.2s")


def test_a_killed_run_has_no_verdict():
    result, _ = execute("import os, signal; print('5 passed'); os.kill(os.getpid(), signal.SIGKILL)")
    assert (result.exit, result.verdict, result.signal) == (137, "killed", 9)
    assert result.summary.startswith("killed by signal 9 - no verdict")


def test_a_shell_reported_kill_is_a_kill():
    result, _ = execute("import sys; sys.exit(137)")
    assert (result.verdict, result.signal) == ("killed", 9)


def test_a_command_that_cannot_start_is_red():
    result = run.execute(["/no/such/runner"], out=io.StringIO())
    assert (result.exit, result.verdict) == (127, "red") and "could not start" in result.summary


def test_without_a_summary_line_the_last_line_stands():
    result, _ = execute("print('building'); print('done')")
    assert result.summary == "done"


def test_a_run_interrupted_on_our_side_stops_its_command():
    import subprocess
    import pytest
    started = []

    def popen(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        started.append(process)
        return process

    class Broken(io.StringIO):
        def write(self, text):
            raise BrokenPipeError("the reader went away")
    with pytest.raises(BrokenPipeError):
        run.execute([sys.executable, "-c", "import time\nwhile True:\n    print('tick', flush=True); time.sleep(0.05)"],
                    popen=popen, out=Broken())
    assert started[0].poll() is not None


def test_a_coloured_summary_is_recorded_plain():
    from flotilla.lane.run import summarize
    assert summarize(["noise", "\x1b[32m12 passed\x1b[0m in 0.02s"]) == "12 passed in 0.02s"


def test_a_wrapper_error_after_the_output_is_not_the_summary():
    lines = ['{"tag":"storm","programs":16}', "bash: line 1: kill: (3500188) - No such process"]
    assert run.summarize(lines) == '{"tag":"storm","programs":16}'
    assert run.summarize(["done", "kill: (12) - No such process", "/bin/sh: 1: x: not found"]) == "done"


def test_a_summary_of_only_wrapper_errors_keeps_the_last_line():
    assert run.summarize(["sh: 1: foo: not found"]) == "sh: 1: foo: not found"
    assert run.summarize(["zsh: x", "bash: y"]) == "bash: y"


def test_a_counting_line_still_wins():
    assert run.summarize(["212 passed in 3.1s", "bash: kill: no such process"]) == "212 passed in 3.1s"


def test_a_run_past_its_ceiling_is_stopped_with_everything_it_started(tmp_path):
    """Worldcore field test W21: a vitest worker looped for 28 minutes and held the machine's one lane - another
    project's push receipt waited behind it. A run has a ceiling; past it, the command and every process it started
    are stopped, and the run is recorded as stopped, not as a verdict."""
    import os
    import time
    child = tmp_path / "child.pid"
    script = (f"import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', 'import time; "
              f"time.sleep(60)']); open(r'{child}', 'w').write(str(p.pid)); time.sleep(60)")
    started = time.monotonic()
    result = run.execute([sys.executable, "-c", script], out=io.StringIO(), ceiling=1.5)
    assert time.monotonic() - started < 20
    assert result.verdict == "killed" and "ceiling" in result.summary and "1.5" in result.summary
    pid = int(child.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the grandchild outlived the ceiling")


def test_a_run_under_its_ceiling_is_untouched():
    result = run.execute([sys.executable, "-c", "print('3 passed')"], out=io.StringIO(), ceiling=30)
    assert result.verdict == "green" and result.summary == "3 passed"


def test_a_run_carries_its_usage():
    result, _ = execute("import time; b = bytearray(50_000_000); time.sleep(1.2); print('1 passed')")
    assert result.usage is not None and result.usage.seconds >= 1.0
    assert result.usage.peak_mb is None or result.usage.peak_mb >= 40


def test_a_run_that_could_not_start_has_no_usage():
    result = run.execute(["/no/such/program"], out=io.StringIO())
    assert result.usage is None


def test_a_run_stopped_at_the_ceiling_says_so_by_a_flag_not_by_its_text():
    stopped = run.execute([sys.executable, "-c", "import time; time.sleep(5)"], out=io.StringIO(), ceiling=0.5)
    assert stopped.at_ceiling
    said, _ = execute("print('stopped at the ceiling of 10 s'); raise SystemExit(1)")
    assert not said.at_ceiling
