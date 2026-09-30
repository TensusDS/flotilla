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
