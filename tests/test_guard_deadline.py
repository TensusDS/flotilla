"""The guard hook answers within its own deadline: Claude Code discards the output of a PreToolUse hook that reaches
its timeout and lets the call go on through the normal permission flow (code.claude.com/docs/en/hooks, Timeouts),
so a reading that runs long must be cut short by the hook itself, with the answer a line read by nobody gets."""

import io
import json
import os
import time
from pathlib import Path

import pytest

from flotilla import hooks
from flotilla.guards import run

ROOT = Path(__file__).resolve().parent.parent


def answer(tmp_path, command):
    payload = {"cwd": str(tmp_path), "session_id": "sid", "permission_mode": "auto",
               "tool_input": {"command": command, "description": "d"}}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


@pytest.fixture
def slow(monkeypatch):
    monkeypatch.setattr(hooks, "GUARD_DEADLINE", 0.5)
    monkeypatch.setattr(hooks, "_guard", lambda *args: time.sleep(30))


@pytest.mark.parametrize("command, decision", [
    ("git push origin main", "deny"),                 # may push: refused unread, as on any failure to read
    ("flotilla work approve 7", "ask"),               # the person's move, unread: theirs to decide
    ("git worktree remove ../x --force", "ask"),      # another guard's move, unread: never let through silently
])
def test_a_reading_past_the_deadline_is_answered_unread(tmp_path, slow, command, decision):
    started = time.monotonic()
    found = answer(tmp_path, command)
    assert time.monotonic() - started < 5
    assert found["permissionDecision"] == decision, found
    assert "did not finish reading" in found["permissionDecisionReason"]


def test_a_reading_inside_the_deadline_answers_as_before(tmp_path, monkeypatch):
    def guard(command, cwd, payload, out, now):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "read"}}),
              file=out)
        return 0
    monkeypatch.setattr(hooks, "_guard", guard)
    assert answer(tmp_path, "git checkout main")["additionalContext"] == "read"
    monkeypatch.setattr(hooks, "_guard", lambda *args: 0)
    assert answer(tmp_path, "git checkout main") is None   # no answer is an answer: the line runs


@pytest.mark.parametrize("command, decision", [
    ("git push origin main", "deny"),
    ("git worktree remove ../x --force", "ask"),   # as a reading past the deadline: never "runs unchecked"
])
def test_a_reading_that_dies_without_an_answer_is_answered_unread(tmp_path, monkeypatch, command, decision):
    monkeypatch.setattr(hooks, "_guard", lambda *args: os._exit(3))
    found = answer(tmp_path, command)
    assert found["permissionDecision"] == decision, found


def test_a_reading_killed_halfway_through_its_answer_gives_no_half_answer(tmp_path, monkeypatch):
    """Review of 6d195ca, M1: a mark written before the answer took a cut answer for a whole one - invalid JSON,
    which Claude Code ignores, so a refused push ran."""
    import signal

    def guard(command, cwd, payload, out, now):
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": "refused: " + "x" * 5000}}), file=out)
        real = os.write

        def half(fd, data):   # this process only: the forked reading
            real(fd, bytes(data)[:len(data) // 2])
            os.kill(os.getpid(), signal.SIGKILL)
        os.write = half
        return 0
    monkeypatch.setattr(hooks, "_guard", guard)
    found = answer(tmp_path, "git push origin main")   # json.loads inside: a cut answer fails here
    assert found["permissionDecision"] == "deny" and "ended before its answer" in found["permissionDecisionReason"]


def test_a_reading_that_cannot_be_reaped_does_not_hold_the_answer(tmp_path, monkeypatch):
    """Review of 6d195ca, M2: a reading stuck where no signal reaches (a hung network mount) is left to init."""
    real = os.waitpid

    def waitpid(pid, flags):
        if flags & os.WNOHANG:
            return 0, 0
        time.sleep(60)
        return real(pid, flags)
    monkeypatch.setattr(hooks, "GUARD_DEADLINE", 0.5)
    monkeypatch.setattr(hooks, "_guard", lambda *args: time.sleep(30))
    monkeypatch.setattr(hooks.os, "waitpid", waitpid)
    started = time.monotonic()
    assert answer(tmp_path, "git push origin main")["permissionDecision"] == "deny"
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("command, decision", [("git push origin main", "deny"), ("git checkout main", "ask")])
def test_without_a_fork_the_line_is_answered_unread(tmp_path, monkeypatch, command, decision):
    """Review of 6d195ca, M6, and the security review of ab4af81: a reading in the hook itself has no deadline, so a
    slow line at the process limit would pass at Claude Code's timeout. Unread is bounded and never silent."""
    def no_fork():
        raise OSError(11, "Resource temporarily unavailable")
    monkeypatch.setattr(hooks.os, "fork", no_fork)
    monkeypatch.setattr(hooks, "_guard", lambda *args: pytest.fail("read without a deadline"))
    found = answer(tmp_path, command)
    assert found["permissionDecision"] == decision and "could not start" in found["permissionDecisionReason"]


def test_a_hook_ended_from_outside_ends_its_reading(tmp_path):
    """Review of 6d195ca, M3: the reading has its own group, so a hook Claude Code ended left it running, holding
    the hook's output open."""
    import signal
    import subprocess
    import sys
    marker = tmp_path / "pid"
    script = tmp_path / "hook.py"
    script.write_text(
        "import io, json, os, sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from flotilla import hooks\n"
        "def guard(*args):\n"
        f"    open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "    time.sleep(60)\n"
        "hooks._guard = guard\n"
        "payload = {'cwd': '/', 'tool_input': {'command': 'git push origin main'}}\n"
        "hooks.run_hook('guard', io.StringIO(json.dumps(payload)))\n")
    hook = subprocess.Popen([sys.executable, "-I", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    for _ in range(100):
        if marker.exists() and marker.read_text():
            break
        time.sleep(0.1)
    hook.send_signal(signal.SIGTERM)
    hook.communicate(timeout=10)   # the hook's streams close: no reading holds them
    assert gone(int(marker.read_text()))


def test_the_programs_a_reading_started_end_with_it(tmp_path, monkeypatch):
    import subprocess
    marker = tmp_path / "pid"

    def guard(*args):
        child = subprocess.Popen(["sleep", "30"])
        marker.write_text(str(child.pid))
        time.sleep(30)
    monkeypatch.setattr(hooks, "GUARD_DEADLINE", 3.0)
    monkeypatch.setattr(hooks, "_guard", guard)
    answer(tmp_path, "git push origin main")
    assert gone(int(marker.read_text()))


def gone(pid: int, within: float = 5.0) -> bool:
    """Ended in time: no such process, or a zombie its new parent has not reaped yet (pytest as PID 1 in a container
    reaps nothing - review of 6d195ca). /proc where there is one; else `ps`, whose exit says whether it found one."""
    import subprocess
    end = time.monotonic() + within
    while time.monotonic() < end:
        stat = Path(f"/proc/{pid}/stat")
        if Path("/proc/self/stat").exists():
            try:
                state = stat.read_text().rsplit(")", 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                return True
        else:
            shown = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
            if shown.returncode != 0:
                return True
            state = shown.stdout.strip()
        if state.startswith("Z"):
            return True
        time.sleep(0.1)
    return False


def test_the_reading_has_a_session_and_streams_of_its_own(tmp_path, monkeypatch):
    """Review of ab4af81: no terminal for a git or ssh to prompt on, and none of the hook's streams held."""
    marker = tmp_path / "seen"

    def guard(*args):
        null = os.stat(os.devnull)
        quiet = all((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (null.st_dev, null.st_ino) for fd in (0, 1, 2))
        marker.write_text(json.dumps({"leader": os.getsid(0) == os.getpid(), "quiet": quiet}))
        return 0
    monkeypatch.setattr(hooks, "_guard", guard)
    answer(tmp_path, "git checkout main")
    assert json.loads(marker.read_text()) == {"leader": True, "quiet": True}


def test_a_reading_orphaned_by_a_hard_kill_ends_by_itself(tmp_path):
    """Review of ab4af81: a hook killed outright (SIGKILL) runs no handler; the reading's own alarm ends it, in the
    middle of a regular expression too."""
    import signal
    import subprocess
    import sys
    marker = tmp_path / "pid"
    script = tmp_path / "hook.py"
    script.write_text(
        "import io, json, os, sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from flotilla import hooks\n"
        "hooks.GUARD_DEADLINE = 1\n"
        "def guard(*args):\n"
        f"    open({str(marker)!r}, 'w').write(str(os.getpid()))\n"
        "    while True: pass\n"
        "hooks._guard = guard\n"
        "payload = {'cwd': '/', 'tool_input': {'command': 'git push origin main'}}\n"
        "hooks.run_hook('guard', io.StringIO(json.dumps(payload)))\n")
    hook = subprocess.Popen([sys.executable, "-I", str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            if marker.exists() and marker.read_text():
                break
            time.sleep(0.05)
        hook.send_signal(signal.SIGKILL)
        hook.communicate(timeout=10)
        pid = int(marker.read_text())
        ended = gone(pid, within=15)
        if not ended:   # a failing run leaves no busy loop behind; only a reading known alive is killed
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        assert ended
    finally:
        if hook.poll() is None:
            hook.kill()
            hook.wait()


def test_a_reading_that_cannot_get_a_pipe_is_answered_unread(tmp_path, monkeypatch):
    """Review of ab4af81: the pipe failing (too many open files) is a reading that could not start."""
    def no_pipe():
        raise OSError(24, "Too many open files")
    monkeypatch.setattr(hooks.os, "pipe", no_pipe)
    found = answer(tmp_path, "git worktree remove ../x --force")
    assert found["permissionDecision"] == "ask" and "could not start" in found["permissionDecisionReason"]


def test_the_deadline_fits_inside_the_hooks_timeout():
    declared = json.loads((ROOT / "hooks" / "hooks.json").read_text())
    guard = next(item for item in declared["hooks"]["PreToolUse"] if item["matcher"] == "Bash")["hooks"][0]
    assert hooks.GUARD_DEADLINE + 5 <= guard["timeout"]   # room for Python's start and the answer


def test_a_line_too_long_says_what_it_holds_and_where_the_long_part_goes():
    """Review of e05669f, I1: `gh` inside `through` refused a long message, and the refusal said it may push."""
    long = "git commit -m 'it goes through' # " + "x" * run.MAX_READ
    found = run.too_long(long)
    assert found.refuse
    assert "`push` or `gh`" in found.text and "a file the command names" in found.text
    assert "may push is refused" not in found.text


def test_a_reading_past_the_deadline_is_not_told_to_shorten_the_line():
    """Review of 6d195ca, M8: a short push held by a slow origin is not a long line."""
    found = run.unread("git push origin main", "the guards did not finish reading this command in 20 seconds")
    assert found.refuse and "a file the command names" not in found.text and found.guard == "unread"
