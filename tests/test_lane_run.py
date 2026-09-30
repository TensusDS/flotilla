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
