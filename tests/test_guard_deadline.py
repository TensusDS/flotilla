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


def test_a_reading_that_dies_without_an_answer_is_a_failure_to_read(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "_guard", lambda *args: os._exit(3))
    assert answer(tmp_path, "git push origin main")["permissionDecision"] == "deny"


def test_the_programs_a_reading_started_end_with_it(tmp_path, monkeypatch):
    import subprocess
    marker = tmp_path / "pid"

    def guard(*args):
        child = subprocess.Popen(["sleep", "30"])
        marker.write_text(str(child.pid))
        time.sleep(30)
    monkeypatch.setattr(hooks, "GUARD_DEADLINE", 1.0)
    monkeypatch.setattr(hooks, "_guard", guard)
    answer(tmp_path, "git push origin main")
    pid = int(marker.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail(f"the git a reading started outlived it: {pid}")


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
