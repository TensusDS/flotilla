"""The person's own moves, wrapped (the person, 2026-11-03): code handed to an interpreter as text that runs a move
only the person makes is refused; code that names flotilla but whose move cannot be read asks the person. The person
runs their moves themselves with `!` or in a terminal; the orchestrator hands them the line and never runs it.

Measured on Claude Code 2.1.317 before this was built: a PreToolUse `ask` shows the person a confirmation in auto
mode and the command waits for it; a `!` command fires no hook at all, so it is never asked."""

import io
import json

import pytest

from flotilla import hooks
from guardkit import onboarded


def ask(root, command, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    payload = {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command, "description": "d"},
               "permission_mode": "auto", "session_id": "sid"}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


REFUSED = [
    "bash -c 'flotilla work approve feat/x'",
    'sh -c "flotilla work approve feat/x"',
    "bash -lc 'cd ~/p && flotilla work approve feat/x'",
    "zsh -c 'flotilla rig enable --provider vast'",
    "bash -c 'flotilla attention digest'",
    "python3 -c \"import subprocess; subprocess.run(['flotilla', 'work', 'approve', 'feat/x'])\"",
    "python3 -c \"import os; os.system('flotilla rig open --hours 2')\"",
    "perl -e 'system(\"flotilla work approve feat/x\")'",
    "bash -c '\"$F\" work approve feat/x'",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['flotilla', 'work', 'approve', 'feat/x'])\nEOF",
]


@pytest.mark.parametrize("command", REFUSED)
def test_a_persons_move_wrapped_in_code_is_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer
    assert "person" in answer["permissionDecisionReason"] and "!" in answer["permissionDecisionReason"]


ASKED = [
    "bash -c 'flotilla work show feat/x'",
    "python3 -c \"import subprocess; subprocess.run(['flotilla', 'status'])\"",
    "sh -c 'flotilla work $MOVE feat/x'",
]


@pytest.mark.parametrize("command", ASKED)
def test_flotilla_wrapped_in_code_asks_the_person(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "ask", answer
    assert "person decides" in answer["permissionDecisionReason"]


PASSES = [
    "bash -c 'ls -la'",
    "python3 -c 'print(1)'",
    "flotilla work show feat/x | grep approve",
    "git commit -m 'approve the flotilla change'",
    "python3 - <<'EOF'\nprint('the flotilla docs')\nEOF",
]


@pytest.mark.parametrize("command", PASSES)
def test_other_code_is_left_alone(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") not in ("deny", "ask"), answer
