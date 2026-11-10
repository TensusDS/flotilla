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


def test_the_gc_guards_door_words_are_among_the_hooks():
    assert set(gc.DOOR_WORDS) <= set(hooks.GUARD_TRIGGERS)


def test_every_gc_door_is_spelled_with_one_of_its_door_words(tmp_path):
    """`DOOR_WORDS` is what `door` reads: a door it judged under another word would be one the hook never passes on."""
    from flotilla.guards import shell
    for command in GC_DOORS:
        [segment] = [segment for segment in shell.segments(command, tmp_path) if gc.door(segment)]
        assert segment.program in gc.DOOR_WORDS or segment.git()[0] in gc.DOOR_WORDS, command


# Review of cb3a55f: the text check read the line as typed, the guards read its words as bash does.
SPLIT_WORDS = [("r\\m -rf ../app-main-1", "flotilla gc apply"), ('git wor""ktree prune', "flotilla gc apply"),
               ("git bran''ch -D fleet/main-1", "flotilla gc apply"),
               ('r\\\nm -rf ../app-main-1', "flotilla gc apply"),
               # third review: bash joins the lines first, so the heredoc's body starts a line later than it looks
               ("cat <<EOF; r\\\nm -rf ../app-main-1\nEOF", "flotilla gc apply"),
               ("cat <<EOF; rm -rf \\\n../app-main-1\nEOF", "flotilla gc apply"),
               ('flot""illa work "ap"prove W-1', "person's own move"), ("g''h pr merge 5", "")]


@pytest.mark.parametrize("command", ["git push \\\norigin main", "git checkout -- \\\n  f.txt",
                                     "sed -i 2d \\\nf.txt"])
def test_a_continued_command_is_refused_and_not_said_twice_in_the_same_words(tmp_path, monkeypatch, command):
    """Third review, finding 3: the joined reading stands beside the typed one, and a guard that says the same of
    both says it once."""
    root = onboarded(tmp_path)
    (root / "f.txt").write_text("work\nmore\n", encoding="utf-8")
    found = guard_run.evaluate(command, root, root)
    assert any(item.refuse for item in found), found
    said = [" ".join(item.text.replace("\\\n", "").split()) for item in found]
    assert len(said) == len(set(said)), said


# Fourth review: a reading that took a segment away let a decoy elsewhere in the line take a refusal with it, and
# joining before the bodies are dropped swallows the delimiter of a quoted body that ends a line in a backslash.
DECOYS = ["sed -i 's/a/b/\n2d' f.txt; echo \\\n; sed -i 's/a/b/2d' f.txt",
          'bash -c "true\nflotilla work approve W-1"; echo \\\n; bash -c "trueflotilla work approve W-1"',
          'bash -c "a=flotilla\nb=work\nc=approve\n\\$a \\$b \\$c W-1"; echo \\\n; '
          'bash -c "a=flotillab=workc=approve\\$a \\$b \\$c W-1"']


@pytest.mark.parametrize("command", DECOYS)
def test_no_reading_takes_a_refusal_another_made(tmp_path, monkeypatch, command):
    root = onboarded(tmp_path)
    (root / "f.txt").write_text("work\nmore\n", encoding="utf-8")
    alone = ask(root, command.split("; echo")[0], monkeypatch, tmp_path)
    assert alone and alone.get("permissionDecision") == "deny", alone
    answer = ask(root, command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer


@pytest.mark.parametrize("command", ["cat <<'EOF'\nkeep\\\nEOF\nr\\\nm -rf ../app-main-1\nEOF",
                                     "cat <<'EOF'\nkeep\\\nEOF\nrm -rf \\\n../app-main-1\ncat <<'EOF'\nx\nEOF"])
def test_a_continued_door_after_a_quoted_body_that_ends_in_a_backslash_is_seen(tmp_path, monkeypatch, command):
    answer = ask(fleet(tmp_path, monkeypatch), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer
    assert "flotilla gc apply" in answer["permissionDecisionReason"]


def test_a_quoted_heredoc_whose_body_ends_a_line_in_a_backslash_hides_nothing_after_it(tmp_path, monkeypatch):
    """Why the joined reading stands beside the typed one and not in its place: joined, the body's last line takes
    the delimiter into itself, and what follows would read as body."""
    command = "cat <<'EOF'\nkeep\\\nEOF\nrm -rf ../app-main-1"
    answer = ask(fleet(tmp_path, monkeypatch), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer


@pytest.mark.parametrize("command", ["$F attention all", "F=bin/flotilla; $F attention digest"])
def test_how_the_person_is_asked_is_not_changed_behind_a_variable_program(tmp_path, monkeypatch, command):
    """Third review, finding 2: `rig` and `work` behind a variable program were refused, `attention` ran."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer


@pytest.mark.parametrize("command,said", SPLIT_WORDS)
def test_a_word_split_by_quotes_or_a_backslash_still_reaches_its_guard(tmp_path, monkeypatch, command, said):
    answer = ask(fleet(tmp_path, monkeypatch), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer
    assert said in answer["permissionDecisionReason"]


@pytest.mark.parametrize("command", ["bash -c 'x rig open'", "python3 -c \"run([p, 'attention', 'all'])\""])
def test_code_handed_to_an_interpreter_that_names_a_persons_word_reaches_the_guards(command):
    assert hooks._names_a_guarded_word(command)


@pytest.mark.parametrize("command", ['$A""rig open', "$F wo''rk $B'attention' all", "$F $A'r'ig open",
                                     "`which f` wo''rk $A'ap'prove W-1"])
def test_in_a_line_bash_expands_a_persons_word_anywhere_is_enough(tmp_path, command):
    """Where a word begins is bash's to say once it expands something: `$A'r'ig` is `rig` when A is empty, and no
    reading of the text finds that as a whole word."""
    assert hooks._names_a_guarded_word(command)
    assert guards.on_failure(command, RuntimeError("x"), {}).ask
    fake, project = old_interpreter(tmp_path)
    assert decision(guard_through(fake, project, command)) == "ask"


def test_the_last_resort_finds_such_a_word_too(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "flotilla.guards.run", None)

    def unreadable(*args, **kwargs):
        raise RuntimeError("the rule itself is broken")
    monkeypatch.setattr(guards, "on_failure", unreadable)
    answer = ask(onboarded(tmp_path), '$A""rig open', monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "ask", answer


@pytest.mark.parametrize("command", ["git fetch origin", "ls -la && make", "cat trigger.txt"])
def test_a_persons_word_inside_another_word_is_no_reason_to_run_the_guards(command):
    assert not hooks._names_a_guarded_word(command)


@pytest.mark.parametrize("command", ["a 'b' \"c\" d\\e", "x\\\ny", "", "plain"])
def test_the_three_readings_of_unquoted_text_agree(command):
    scope: dict = {}
    source = ENTRY.read_text(encoding="utf-8")
    line = next(line for line in source.splitlines() if line.strip().startswith("bare = "))
    exec(line.strip(), {"command": command}, scope)
    assert scope["bare"] == guards.unquoted(command) == hooks._unquoted(command)


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


def test_a_person_guard_that_fails_beside_a_push_leaves_the_refusal_standing(tmp_path, monkeypatch):
    broken_person_guard(monkeypatch)
    answer = ask(onboarded(tmp_path), "flotilla work approve W-1 && git push origin main", monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny" and "receipt" in answer["permissionDecisionReason"]


@pytest.mark.parametrize("breaks", ["wrapped_check", "heredoc_check", "whole_text"])
def test_one_reading_of_the_person_guard_failing_costs_no_other_its_refusal(tmp_path, monkeypatch, breaks):
    """Review of cb3a55f, finding 4: one try around every reading threw away a refusal already made, and a line
    whose program is a variable names no flotilla for the failure's own text check."""
    from flotilla.guards import person

    def boom(*args, **kwargs):
        raise RuntimeError("one reading on fire")
    monkeypatch.setattr(person, breaks, boom)
    answer = ask(onboarded(tmp_path), "$F work approve W-1", monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny" and "variable" in answer["permissionDecisionReason"]


def test_a_refusal_in_one_segment_stands_when_the_guard_fails_on_the_next(tmp_path, monkeypatch):
    from flotilla.guards import person
    real = person.check

    def second_fails(segment):
        if "status" in segment.text:
            raise RuntimeError("second segment on fire")
        return real(segment)
    monkeypatch.setattr(person, "check", second_fails)
    answer = ask(onboarded(tmp_path), "flotilla work approve W-1; git status", monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny" and "person's own move" in answer["permissionDecisionReason"]


@pytest.mark.parametrize("door", ["cd {name}; ", "git -C {name} status; "])
def test_a_directory_name_too_long_to_exist_does_not_fail_the_guards(tmp_path, monkeypatch, door):
    """Review of cb3a55f, finding 1: `cd` to a name over the file system's limit raised out of the matcher, bash
    failed the `cd` and ran the rest - under the failure rule, which reads text."""
    command = door.format(name="a" * 300) + "/x/bin/flot''illa work approve W-1"
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny" and "person's own move" in answer["permissionDecisionReason"]


@pytest.mark.parametrize("command", ["/x/bin/flot''illa work approve W-1", "$F work approve W-1",
                                     "`which f` rig open", 'flo\\tilla "attention" all'])
def test_the_failure_rule_reads_a_persons_move_through_quotes_and_variables(command):
    assert guards.on_failure(command, RuntimeError("x"), {}).ask


@pytest.mark.parametrize("command", ["cd /src/flotilla && git fetch origin", "cat .flotilla/project.toml | wc -l",
                                     "bin/flotilla work show W-1 --trigger x", "echo $HOME/notes"])
def test_the_failure_rule_does_not_ask_about_a_persons_word_inside_another(command):
    found = guards.on_failure(command, RuntimeError("x"), {})
    assert not found.ask and not found.refuse


def test_the_failure_rule_reads_a_push_through_quotes():
    assert guards.on_failure("git pu''sh origin main", RuntimeError("x"), {}).refuse
    assert guards.on_failure('g"h" pr merge 5', RuntimeError("x"), {}).refuse


def test_the_entry_point_and_the_last_resort_read_a_push_through_quotes(tmp_path, monkeypatch):
    """Second review, finding 5: nothing held the other two copies to the unquoted reading."""
    fake, project = old_interpreter(tmp_path)
    assert decision(guard_through(fake, project, "g''h pr merge 5", {"FLOTILLA_GATE_OVERRIDE": ""})) == "deny"
    monkeypatch.setitem(sys.modules, "flotilla.guards.run", None)

    def unreadable(*args, **kwargs):
        raise RuntimeError("the rule itself is broken")
    monkeypatch.setattr(guards, "on_failure", unreadable)
    # `checkout` takes the line past the text check; the push word is the split one
    (tmp_path / "p").mkdir()
    answer = ask(onboarded(tmp_path / "p"), "git checkout x && g''h pr merge 5", monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "deny", answer


ESCAPED = "python3 -c \"run('flotilla\\trig\\topen'.split())\""


@pytest.mark.parametrize("escape", ["\\x20", "\\040", "\\u0020"])
def test_a_whole_escape_is_read_as_a_blank(tmp_path, monkeypatch, escape):
    command = "python3 -c \"run('flotilla" + escape + "rig" + escape + "open'.split())\""
    assert guards.on_failure(command, RuntimeError("x"), {}).ask
    fake, project = old_interpreter(tmp_path)
    assert decision(guard_through(fake, project, command)) == "ask"
    monkeypatch.setitem(sys.modules, "flotilla.guards.run", None)

    def unreadable(*args, **kwargs):
        raise RuntimeError("the rule itself is broken")
    monkeypatch.setattr(guards, "on_failure", unreadable)
    (tmp_path / "p").mkdir()
    answer = ask(onboarded(tmp_path / "p"), command, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "ask", answer


def test_a_backslash_the_code_makes_a_blank_of_does_not_hide_a_persons_word(tmp_path, monkeypatch):
    """Second review, finding 2: with the backslashes taken out the word is glued to its neighbours."""
    assert guards.on_failure(ESCAPED, RuntimeError("x"), {}).ask
    fake, project = old_interpreter(tmp_path)
    assert decision(guard_through(fake, project, ESCAPED)) == "ask"
    monkeypatch.setitem(sys.modules, "flotilla.guards.run", None)

    def unreadable(*args, **kwargs):
        raise RuntimeError("the rule itself is broken")
    monkeypatch.setattr(guards, "on_failure", unreadable)
    (tmp_path / "p").mkdir()
    answer = ask(onboarded(tmp_path / "p"), ESCAPED, monkeypatch, tmp_path)
    assert answer and answer.get("permissionDecision") == "ask", answer


def test_a_symlink_loop_does_not_fail_the_guards(tmp_path, monkeypatch):
    """Second review, finding 1: before Python 3.13 `Path.resolve` raises RuntimeError on a loop, which is no
    OSError; bash fails the `cd` and runs the rest where it stood."""
    import os
    root = fleet(tmp_path, monkeypatch)
    os.symlink("loop", root / "loop")
    for command in ("cd loop; rm -rf ../app-main-1", "git -C loop status; rm -rf ../app-main-1"):
        answer = ask(root, command, monkeypatch, tmp_path)
        assert answer and answer.get("permissionDecision") == "deny", (command, answer)
        assert "guards failed" not in answer["permissionDecisionReason"]


def test_where_a_directory_leads_is_unknown_whatever_resolving_it_raises(tmp_path, monkeypatch):
    from pathlib import Path

    from flotilla.guards import shell

    def loops(self, *args, **kwargs):
        raise RuntimeError("Symlink loop from x")
    monkeypatch.setattr(Path, "resolve", loops)
    assert shell.into(tmp_path, "anywhere") is None


def test_the_wrapped_reading_failing_on_one_segment_costs_no_other_its_refusal(tmp_path, monkeypatch):
    """Second review, finding 3: the wrapped reading was one try over every segment."""
    from flotilla.guards import person
    real = person.wrapped_check

    def second_fails(segment, *args, **kwargs):
        if "status" in segment.text:
            raise RuntimeError("second segment on fire")
        return real(segment, *args, **kwargs)
    monkeypatch.setattr(person, "wrapped_check", second_fails)
    answer = ask(onboarded(tmp_path), "bash -c 'flotilla work approve W-1'; git status", monkeypatch, tmp_path)
    assert answer["permissionDecision"] == "deny", answer


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
    found = guard_run.evaluate("git branch -D fleet/main-1", root, root)
    assert any(item.guard == gc.GUARD and item.refuse for item in found), found
    # judged, not refused wholesale: a branch that is none of the fleet's is the person's
    assert [item.guard for item in guard_run.evaluate("git branch -D mine", root, root)] == ["rules"]


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
