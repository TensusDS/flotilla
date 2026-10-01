import io
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from flotilla import hooks
from flotilla.guards import run as guard_run
from guardkit import onboarded

ROOT = Path(__file__).resolve().parent.parent


def ask(root, command, monkeypatch, tmp_path, cwd=None):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("FLOTILLA_GATE_OVERRIDE", raising=False)
    payload = {"cwd": str(cwd or root), "tool_name": "Bash", "tool_input": {"command": command}}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


def test_a_push_without_a_receipt_is_denied(tmp_path, monkeypatch):
    answer = ask(onboarded(tmp_path), "git push origin main", monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny" and "receipt" in answer["permissionDecisionReason"]


def test_a_warning_is_context_not_a_decision(tmp_path, monkeypatch):
    answer = ask(onboarded(tmp_path), "cd $D && git checkout -- f.txt", monkeypatch, tmp_path)
    assert "permissionDecision" not in answer and "could not tell which tree" in answer["additionalContext"]


def test_a_guard_that_is_off_says_nothing(tmp_path, monkeypatch):
    root = onboarded(tmp_path, guards=("revert",))
    assert ask(root, "sed -i '3d' f.txt", monkeypatch, tmp_path) is None


def test_every_refusal_is_given_at_once(tmp_path, monkeypatch):
    root = onboarded(tmp_path)
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    answer = ask(root, "sed -i '1d' f.txt; git checkout -- f.txt", monkeypatch, tmp_path)
    reason = answer["permissionDecisionReason"]
    assert "line-number" in reason and "revert" in reason


def test_rules_that_cannot_be_read_refuse_a_push_and_warn_otherwise(tmp_path, monkeypatch):
    root = onboarded(tmp_path, push=False)
    (root / ".flotilla" / "project.toml").write_text("schema = \n", encoding="utf-8")
    assert ask(root, "git push origin main", monkeypatch, tmp_path)["permissionDecision"] == "deny"
    answer = ask(root, "git checkout -- f.txt", monkeypatch, tmp_path)
    assert "permissionDecision" not in answer and "could not be read" in answer["additionalContext"]


def test_a_crash_refuses_a_command_that_may_push(tmp_path, monkeypatch):
    from flotilla.guards import shell

    def boom(*args, **kwargs):
        raise RuntimeError("matcher on fire")
    monkeypatch.setattr(shell, "segments", boom)
    root = onboarded(tmp_path)
    assert ask(root, "git push origin main", monkeypatch, tmp_path)["permissionDecision"] == "deny"
    answer = ask(root, "git checkout -- f.txt", monkeypatch, tmp_path)
    assert "permissionDecision" not in answer and "matcher on fire" in answer["additionalContext"]


def test_a_command_naming_no_guarded_program_imports_nothing(tmp_path):
    root = onboarded(tmp_path)
    probe = (
        "import io, json, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from flotilla.hooks import run_hook\n"
        f"payload = {{'cwd': {str(root)!r}, 'tool_name': 'Bash', 'tool_input': {{'command': 'ls -la && make'}}}}\n"
        "run_hook('guard', io.StringIO(json.dumps(payload)))\n"
        "heavy = [m for m in sys.modules if m.startswith(('flotilla.guards', 'flotilla.ledger', 'flotilla.core'))]\n"
        "print(','.join(heavy))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == ""


def test_a_command_the_guards_read_stays_inside_the_budget(tmp_path, monkeypatch):
    root = onboarded(tmp_path)
    started = time.monotonic()
    ask(root, "git status && sed -n 1p f.txt", monkeypatch, tmp_path)
    assert time.monotonic() - started < 1.0   # spec ~200 ms; measured locally and printed by the executor


NPM_TIER = '\n[[tests.tier]]\nname = "npm"\ncommand = "npm test"\nrequired_for = ["push"]\n'


@pytest.mark.parametrize("command", ["npm test", "uv run pytest", "npx vitest run", "cd sub && npm test -- --ci",
                                     "uv run --with pytest python -m pytest -q"])
def test_a_long_run_outside_the_lane_is_warned_about(tmp_path, monkeypatch, command):
    root = onboarded(tmp_path, extra=NPM_TIER)
    (root / "sub").mkdir()
    answer = ask(root, command, monkeypatch, tmp_path)
    assert answer is not None and "permissionDecision" not in answer
    context = answer["additionalContext"]
    assert context.count("flotilla lane run --for") == 1


@pytest.mark.parametrize("command", ["flotilla lane run --for x -- npm test",
                                     "/opt/flotilla/scripts/flotilla lane run --for x -- uv run pytest",
                                     "flotilla receipt run --purpose push",
                                     'echo "run npm test later"', 'git commit -m "npm test passes"',
                                     "npm install", "uv run python tools/check_version.py"])
def test_a_booked_run_or_a_mention_is_not_warned_about(tmp_path, monkeypatch, command):
    root = onboarded(tmp_path, extra=NPM_TIER)
    answer = ask(root, command, monkeypatch, tmp_path)
    assert answer is None or "flotilla lane run" not in answer.get("additionalContext", "")


@pytest.mark.parametrize("command", ["git commit -m 'npm test passes'",
                                     'git commit -m "fix the tier\n\nnpm test passes now"',
                                     "git commit -m 'first line\npytest -q is green'",
                                     'git commit -m "title" -m "uv run pytest passes"',
                                     "git commit -q -F - <<'EOF'\nfix\n\npytest -q passes\nEOF",
                                     'git commit -m "fix\n\nuv run pytest -q\n\nmore"',
                                     "git commit -m 'fix\nnpm test\nok'",
                                     'git commit -m "fix\n\npytest -q passes;\nnpm test too"',
                                     "pytest --version", "uv run pytest --version", "npx playwright install",
                                     "npx playwright install chromium", "playwright install --with-deps"])
def test_text_in_a_commit_message_and_tool_setup_are_not_warned_about(tmp_path, monkeypatch, command):
    root = onboarded(tmp_path, extra=NPM_TIER)
    answer = ask(root, command, monkeypatch, tmp_path)
    assert answer is None or "flotilla lane run" not in answer.get("additionalContext", ""), answer


def test_a_real_run_after_a_commit_is_still_warned_about(tmp_path, monkeypatch):
    root = onboarded(tmp_path, extra=NPM_TIER)
    answer = ask(root, "git commit -m 'wip' && npx playwright test", monkeypatch, tmp_path)
    assert answer is not None and answer["additionalContext"].count("flotilla lane run --for") == 1


def test_the_lane_guard_is_off_when_the_profile_says_so(tmp_path, monkeypatch):
    root = onboarded(tmp_path, extra=NPM_TIER, off=("lane",))
    assert ask(root, "npm test", monkeypatch, tmp_path) is None


@pytest.mark.parametrize("command, booked", [
    ("npm test", ["npm", "test"]),
    ("CI=1 npm test", ["env", "CI=1", "npm", "test"]),
    ("uv run pytest -q 2>&1 | tail -5", ["sh", "-c", "uv run pytest -q 2>&1 | tail -5"]),
    ("cd sub && npm test", ["sh", "-c", "cd sub && npm test"]),
    ("pytest -q > out.txt", ["sh", "-c", "pytest -q > out.txt"]),
])
def test_the_suggested_lane_run_starts_the_same_run(tmp_path, monkeypatch, command, booked):
    import shlex
    root = onboarded(tmp_path, extra=NPM_TIER)
    (root / "sub").mkdir()
    context = ask(root, command, monkeypatch, tmp_path)["additionalContext"]
    suggested = context.split("book it: `", 1)[1].rsplit("`", 1)[0]
    words = shlex.split(suggested)
    assert words[:4] == ["flotilla", "lane", "run", "--for"]
    assert words[words.index("--") + 1:] == booked


CLI_PATH = str(ROOT / "scripts" / "flotilla")


@pytest.mark.parametrize("command", [
    f"{CLI_PATH} work approve feat/x",
    "flotilla work approve feat/x",
    f"cd /tmp && {CLI_PATH} work approve feat/x --root .",
    f"FLOTILLA_STATE_DIR=/s {CLI_PATH} work approve feat/x --root .",
    # review of 0.5.0, I2: each of these passed the first version - the quoted word escaped the text prefilter, and an
    # interpreter or a wrapper put the CLI out of the first word
    f"{CLI_PATH} work app''rove feat/x",
    f"{CLI_PATH} work appro\\ve feat/x",
    f"python3 {CLI_PATH} work approve feat/x",
    f"timeout 60 {CLI_PATH} work approve feat/x",
    f"setsid bash {CLI_PATH} work approve feat/x",
    "python3 -m flotilla.cli work approve feat/x",
])
def test_claude_running_the_persons_approval_is_refused(tmp_path, monkeypatch, command):
    """Where a session leads the fleet from the person's own interactive session, the person check lets that
    session's model through: it IS an interactive session. A tool call is never the person's own act, and the person
    has their own door that this hook does not see - a command typed with `!` (measured on Claude Code 2.1.287: a
    `!` command fires no PreToolUse hook, a model's Bash call does)."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny"
    assert "the person's own move" in answer["permissionDecisionReason"]
    assert "`!`" in answer["permissionDecisionReason"]


def test_approval_is_refused_to_claude_outside_any_project_too(tmp_path, monkeypatch):
    answer = ask(tmp_path, f"{CLI_PATH} work approve feat/x --root /elsewhere", monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", [
    f"{CLI_PATH} work show feat/x",
    f"{CLI_PATH} brief",
    "git log --grep approve",
    f"{CLI_PATH} work accept feat/x --reviewed abc1234",
    # review of 0.5.0, M2: the move is the word after `work`; `approve` elsewhere is a branch or a reason
    f"{CLI_PATH} work show approve",
    f"{CLI_PATH} work claim approve",
    f"{CLI_PATH} work wait feat/x --on me --why approve",
    "git checkout -b approve",
])
def test_other_moves_and_the_word_alone_pass(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer is None or answer.get("permissionDecision") != "deny"


def test_the_guard_knows_every_move_the_cli_has():
    """The move is read as the first word after `work` that names one; a move the guard does not know would let the
    scan run on to a later `approve`."""
    import argparse
    from flotilla import cli
    from flotilla.guards import person
    parser = cli.build_parser()
    work = next(a for a in parser._subparsers._group_actions[0].choices["work"]._actions
                if isinstance(a, argparse._SubParsersAction))
    assert set(work.choices) == set(person.MOVES)
