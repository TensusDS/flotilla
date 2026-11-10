"""What reaches the guards, and what they answer when one of them cannot judge.

Four holes read on 2026-11-10: the hook's text check let most of the gc guard's doors pass before any guard ran; a
person guard that raised let a line that names no push run unchecked; on a failure, `FLOTILLA_GATE_OVERRIDE=` anywhere
in the command's text opened a push; and rules that could not be read skipped the gc and organize guards."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from flotilla import guards, hooks
from flotilla.guards import gc
from flotilla.guards import run as guard_run
from guardkit import onboarded

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "bin" / "flotilla"


def ask(root, command, monkeypatch, tmp_path, mode="auto"):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("FLOTILLA_GATE_OVERRIDE", raising=False)
    payload = {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command, "description": "d"},
               "permission_mode": mode, "session_id": "sid-sender"}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


def fleet(tmp_path, monkeypatch):
    """A project whose fleet made one seat tree and one branch, as the gc guard is told."""
    from flotilla.gc import sweep
    root = onboarded(tmp_path)
    seat = tmp_path / "app-main-1"
    (seat / "src").mkdir(parents=True)
    (tmp_path / "app-gc-archive" / "objects").mkdir(parents=True)
    monkeypatch.setattr(sweep, "made_by_flotilla", lambda ledger: ({seat.resolve()}, {"fleet/main-1"}))
    return root


# --- 1. every door of the gc guard reaches it through the hook ---

GC_DOORS = ["git worktree prune", "git worktree remove --force ../app-main-1", "git worktree remove ../app-main-1",
            "git branch -D fleet/main-1", "git branch --delete --force fleet/main-1",
            "git update-ref -d refs/heads/fleet/main-1", "rm -rf ../app-main-1", "rm -r ../app-gc-archive",
            "rm -R ../app-gc-archive/objects"]


@pytest.mark.parametrize("command", GC_DOORS)
def test_a_gc_door_is_refused_through_the_hook(tmp_path, monkeypatch, command):
    """The text check in `flotilla.hooks` answered before any guard for a line naming none of its words, and `rm`,
    `worktree`, `branch` and `update-ref` were not among them: the guard was right and never asked."""
    answer = ask(fleet(tmp_path, monkeypatch), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer
    assert "flotilla gc apply" in answer["permissionDecisionReason"]


@pytest.mark.parametrize("command", GC_DOORS)
def test_every_gc_door_names_a_word_the_hook_looks_for(command):
    assert any(word in command for word in hooks.GUARD_TRIGGERS), command


def test_a_door_the_guard_judges_is_one_the_hook_lets_reach_it(tmp_path):
    """Whatever the gc guard calls a door, the hook's text check passes on to it."""
    from flotilla.guards import shell
    for command in GC_DOORS:
        assert any(gc.door(segment) for segment in shell.segments(command, tmp_path)), command


# --- 2. a person guard that cannot judge does not let the person's own move run ---

def broken_person_guard(monkeypatch):
    from flotilla.guards import person

    def boom(segment):
        raise RuntimeError("person guard on fire")
    monkeypatch.setattr(person, "check", boom)


@pytest.mark.parametrize("command", ["bin/flotilla work approve W-1", "flotilla rig open", "flotilla attention all"])
def test_a_person_guard_that_fails_asks_the_person_about_a_line_that_may_hold_their_move(tmp_path, monkeypatch,
                                                                                         command):
    broken_person_guard(monkeypatch)
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "ask", answer
    assert "person guard on fire" in answer["permissionDecisionReason"]


def test_a_person_guard_that_fails_leaves_the_other_guards_judging(tmp_path, monkeypatch):
    broken_person_guard(monkeypatch)
    root = onboarded(tmp_path)
    assert ask(root, "git push origin main", monkeypatch, tmp_path)["permissionDecision"] == "deny"
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    answer = ask(root, "git checkout -- f.txt", monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny" and "revert" in answer["permissionDecisionReason"]


def test_a_person_guard_that_fails_says_so_about_any_other_line(tmp_path, monkeypatch):
    broken_person_guard(monkeypatch)
    answer = ask(onboarded(tmp_path), "git switch -c topic", monkeypatch, tmp_path)
    assert "permissionDecision" not in answer and "person guard on fire" in answer["additionalContext"]


@pytest.mark.parametrize("command", ["bin/flotilla work approve W-1", "flotilla rig open"])
def test_guards_that_crash_whole_ask_the_person_about_a_line_that_may_hold_their_move(tmp_path, monkeypatch, command):
    from flotilla.guards import shell

    def boom(*args, **kwargs):
        raise RuntimeError("matcher on fire")
    monkeypatch.setattr(shell, "segments", boom)
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "ask", answer


def test_the_failure_rule_asks_only_where_a_persons_move_may_stand():
    err = RuntimeError("x")
    assert guards.on_failure("flotilla work approve W-1", err, {}).ask
    assert guards.on_failure("python3 -m flotilla rig enable", err, {}).ask
    assert not guards.on_failure("flotilla work claim W-1", err, {}).ask
    assert not guards.on_failure("git checkout -- f.txt", err, {}).ask
    found = guards.on_failure("flotilla work approve W-1 && git push", err, {})
    assert found.refuse   # the refusal stands; nothing is left to ask


def test_a_hook_that_cannot_reach_the_guards_asks_the_person_too(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setitem(sys.modules, "flotilla.guards.run", None)   # `import` of it raises ImportError
    answer = ask(onboarded(tmp_path), "flotilla work approve W-1", monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "ask", answer


def test_the_last_resort_asks_the_person_too(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "flotilla.guards.run", None)

    def unreadable(*args, **kwargs):
        raise RuntimeError("the rule itself is broken")
    monkeypatch.setattr(guards, "on_failure", unreadable)
    root = onboarded(tmp_path)
    answer = ask(root, "flotilla work approve W-1", monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "ask", answer
    assert ask(root, "flotilla work claim W-1", monkeypatch, tmp_path).get("permissionDecision") is None


def test_the_words_of_the_failure_rule_are_the_same_in_its_three_places():
    scope: dict = {}
    exec(compile(ENTRY.read_text(encoding="utf-8").split("def main")[0], str(ENTRY), "exec"), scope)
    assert scope["PERSON_WORDS"] == guards.PERSON_WORDS == hooks.LAST_RESORT_PERSON_WORDS
    assert scope["PUSH_WORDS"] == guards.PUSH_WORDS == hooks.LAST_RESORT_PUSH_WORDS


def guard_through(entry, project, command, env=None):
    import os
    payload = json.dumps({"cwd": str(project), "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(entry), "hook", "guard"], input=payload, capture_output=True,
                          text=True, env={**os.environ, **(env or {})})


def old_interpreter(tmp_path):
    fake = tmp_path / "flotilla"
    fake.write_text(ENTRY.read_text(encoding="utf-8").replace("MINIMUM = (3, 11)", "MINIMUM = (99, 0)"),
                    encoding="utf-8")
    project = tmp_path / "app"
    (project / ".flotilla").mkdir(parents=True)
    (project / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    return fake, project


def decision(done):
    return json.loads(done.stdout)["hookSpecificOutput"].get("permissionDecision", "") if done.stdout.strip() else ""


def test_an_entry_point_that_cannot_judge_asks_the_person_about_their_move(tmp_path):
    fake, project = old_interpreter(tmp_path)
    assert decision(guard_through(fake, project, "flotilla work approve W-1")) == "ask"
    assert decision(guard_through(fake, project, "flotilla work claim W-1")) == ""


# --- 3. on a failure, the command's own text does not waive the refusal ---

OVERRIDES = ['FLOTILLA_GATE_OVERRIDE="x" git push origin main', 'echo "FLOTILLA_GATE_OVERRIDE=" && git push origin main',
             "git push origin main # FLOTILLA_GATE_OVERRIDE=x", 'FLOTILLA_GATE_OVERRIDE="x" gh pr merge 5']


@pytest.mark.parametrize("command", OVERRIDES)
def test_an_override_typed_into_the_command_does_not_open_a_push_the_guards_could_not_judge(tmp_path, monkeypatch,
                                                                                             command):
    """Judged, an override waives a missing receipt alone, recorded, and never the person's approval or rules nobody
    can read. Unjudged, none of that is known - so the text a session can type waives nothing."""
    from flotilla.guards import shell

    def boom(*args, **kwargs):
        raise RuntimeError("matcher on fire")
    monkeypatch.setattr(shell, "segments", boom)
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer


@pytest.mark.parametrize("command", OVERRIDES)
def test_the_failure_rule_reads_the_override_from_the_environment_alone(command):
    err = RuntimeError("x")
    assert guards.on_failure(command, err, {}).refuse
    assert not guards.on_failure(command, err, {"FLOTILLA_GATE_OVERRIDE": "the person said so"}).refuse
    assert guards.on_failure(command, err, {"FLOTILLA_GATE_OVERRIDE": "  "}).refuse


def test_the_last_resort_reads_the_override_from_the_environment_alone(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "flotilla.guards.run", None)

    def unreadable(*args, **kwargs):
        raise RuntimeError("the rule itself is broken")
    monkeypatch.setattr(guards, "on_failure", unreadable)
    answer = ask(onboarded(tmp_path), OVERRIDES[0], monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer


def test_an_entry_point_that_cannot_judge_reads_the_override_from_the_environment_alone(tmp_path):
    fake, project = old_interpreter(tmp_path)
    assert decision(guard_through(fake, project, OVERRIDES[0], {"FLOTILLA_GATE_OVERRIDE": ""})) == "deny"
    assert decision(guard_through(fake, project, "git push origin main", {"FLOTILLA_GATE_OVERRIDE": "hotfix"})) == ""


# --- 4. rules that cannot be read do not switch off the guards that never read them ---

def test_unreadable_rules_leave_the_gc_guard_judging(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)

    def unreadable(*args, **kwargs):
        raise RuntimeError("rules on fire")
    monkeypatch.setattr("flotilla.guards.rules.rules_for", unreadable)
    found = guard_run.evaluate("git worktree prune", root, root)
    assert any(item.guard == gc.GUARD and item.refuse for item in found), found


def test_unreadable_rules_leave_the_organize_guard_judging(tmp_path, monkeypatch):
    from flotilla.guards import organize
    root = onboarded(tmp_path)

    def unreadable(*args, **kwargs):
        raise RuntimeError("rules on fire")
    monkeypatch.setattr("flotilla.guards.rules.rules_for", unreadable)
    monkeypatch.setattr(organize, "tracked", lambda root: ({}, {7}))
    with_rules = [item for item in guard_run.evaluate("gh pr close 7", root, root) if item.guard == organize.GUARD]
    assert with_rules and with_rules[0].refuse, with_rules


def test_unreadable_rules_are_still_said_when_nothing_else_is(tmp_path, monkeypatch):
    root = onboarded(tmp_path)

    def unreadable(*args, **kwargs):
        raise RuntimeError("rules on fire")
    monkeypatch.setattr("flotilla.guards.rules.rules_for", unreadable)
    found = guard_run.evaluate("git checkout -- f.txt", root, root)
    assert [item.guard for item in found] == ["rules"] and "rules on fire" in found[0].text
