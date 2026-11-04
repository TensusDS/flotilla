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



# --- the security reviews of bebab1b: option spellings, code on stdin, and what is not a person's move ---

REFUSED_TOO = [
    "bash -o pipefail -c 'flotilla work approve feat/x'",
    "python3 -Ic \"import subprocess; subprocess.run(['flotilla', 'work', 'approve', 'x'])\"",
    "python -X dev -c \"import subprocess; subprocess.run(['flotilla', 'work', 'approve', 'x'])\"",
    "python3 -W ignore -c \"import subprocess; subprocess.run(['flotilla', 'work', 'approve', 'x'])\"",
    "node --eval \"require('child_process').execSync('flotilla work approve x')\"",
    "node -p \"require('child_process').execSync('flotilla work approve x')\"",
    "perl -E 'system(\"flotilla work approve x\")'",
    "echo 'flotilla work approve feat/x' | sh",
    "printf '%s\\n' 'flotilla work approve feat/x' | bash",
    "bash <<< 'flotilla work approve feat/x'",
    "python3 <<< \"import subprocess; subprocess.run(['flotilla','work','approve','x'])\"",
    "cat <<'EOF' | python3\nimport subprocess\nsubprocess.run(['flotilla', 'work', 'approve', 'x'])\nEOF",
    "bash -c '\"$PLUGIN/bin/flotilla\" work approve feat/x'",
]


@pytest.mark.parametrize("command", REFUSED_TOO)
def test_every_way_code_reaches_an_interpreter_is_read(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


ASKED_TOO = [
    "bash -c 'cd ~/p && ./tool work approve feat/x'",   # a person's move, its program unreadable
]


@pytest.mark.parametrize("command", ASKED_TOO)
def test_a_persons_move_whose_program_cannot_be_read_asks(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "ask", answer


LEFT_ALONE = [
    "bash -c 'gh pr review --approve $PR'",
    "bash -c 'for f in *.md; do grep -l approve \"$f\"; done'",
    "bash -c 'grep -rn approve flotilla/guards'",
    "python3 -c \"print('approve costs $5')\"",
    "bash -c 'cd /home/max/workspace/flotilla-field && uv run pytest -q'",
    "sh -c 'ls ~/.config/flotilla'",
    "python3 -c \"print(open('.flotilla/project.toml').read())\"",
    "python3 - <<'EOF'\nimport re\nprint(re.findall('approve', open('flotilla/guards/person.py').read()))\nEOF",
]


@pytest.mark.parametrize("command", LEFT_ALONE)
def test_ordinary_code_is_neither_refused_nor_asked(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") not in ("deny", "ask"), answer


def test_an_ask_keeps_the_other_guards_notes(tmp_path, monkeypatch):
    from flotilla.guards import Finding, run
    out = __import__("io").StringIO()
    monkeypatch.setattr(run, "evaluate", lambda *a, **k: [Finding("person", False, "ASKED", ask=True),
                                                          Finding("revert", False, "A NOTE")])
    run.guard_hook("x", tmp_path, None, out)
    body = json.loads(out.getvalue())["hookSpecificOutput"]
    assert body["permissionDecision"] == "ask" and "ASKED" in body["permissionDecisionReason"]
    assert "A NOTE" in body["permissionDecisionReason"]


REFUSED_THIRD = [
    "bash -s arg <<'EOF'\nflotilla work approve feat/x\nEOF",
    "python3 /dev/stdin <<'EOF'\nimport subprocess\nsubprocess.run(['flotilla', 'work', 'approve', 'x'])\nEOF",
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['flotilla'] + ['work'] + ['approve', 'x'])\nEOF",
    "bash -c '~/plug/flotilla work approve feat/x'",
    "python3 -c \"import subprocess; c = ['flotilla']; c += ['rig']; c += ['open']; subprocess.run(c)\"",
]


@pytest.mark.parametrize("command", REFUSED_THIRD)
def test_a_persons_move_spelled_loosely_or_fed_on_stdin_is_refused(tmp_path, monkeypatch, command):
    """Third security review of the wrapped check: arguments beside a heredoc, a move built in pieces, a path to the
    CLI that is not .../bin/flotilla."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


REFUSED_FOURTH = [
    "flotilla rig run --get out -- make shots; bash -c 'flotilla work approve feat/x'",
    "flotilla rig run -- make shots && python3 -c \"import subprocess; subprocess.run(['flotilla', 'work', 'approve', 'x'])\"",
    "python3 -c \"from flotilla.cli import main; main(['work', 'approve', 'x'])\"",
    "python3 -c \"import flotilla.cli as c; c.main(['work'] + ['approve', 'x'])\"",
    "python3 -c 'print(1)' | cat <<'EOF' | bash\nflotilla work approve feat/x\nEOF",
]


@pytest.mark.parametrize("command", REFUSED_FOURTH)
def test_a_rig_run_cut_never_hides_what_follows_and_an_import_counts(tmp_path, monkeypatch, command):
    """Fourth security review of the wrapped check: the cut after a `rig run`'s `--` dropped the rest of the line,
    the package imported was not flotilla named, and a stdin body went unjudged once the line had a verdict."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


REFUSED_FOURTH_EXACT = [
    "flotilla rig run -- make x; cat <<'EOF' | python3\nimport subprocess\nsubprocess.run(['flotilla', 'work', 'approve', 'x'])\nEOF",
    "python3 -c \"from flotilla.ledger import steering; steering.approve('x')\"",
    "python3 -c \"import flotilla.rig.commands as r; r.run_rig_command(['open'])\"",
]


@pytest.mark.parametrize("command", REFUSED_FOURTH_EXACT)
def test_a_body_past_a_rig_run_and_the_package_imported_are_judged(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] in ("deny", "ask"), answer


def test_a_pipe_into_an_interpreter_with_an_argument_is_read(tmp_path, monkeypatch):
    """Fifth security review: the stdin check was told of a pipe by the segments joined by newlines, which have no
    pipe left, so `... | python3 /dev/stdin` went unjudged."""
    command = "printf \"import subprocess; subprocess.run(['flotilla', 'work', 'approve', 'x'])\" | python3 /dev/stdin"
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


# --- 0.28.1: the narrowing of when the guard asks was withdrawn after three rounds of bypasses (the final review of
# 0.28.1: a wrapper's own interpreter-named word chose the wrong option table; fish -C, python -m timeit, node --import,
# perl -M carry code the table did not know). Anything after an interpreter that names flotilla asks, as at 0.28.0;
# what was asked about at 0.28.0 must still be asked about.

ASKED_AS_AT_0_28_0 = [
    "uv run --python python3.12 node -e 'require(\"child_process\").execSync(\"flotilla work land b\")'",
    "docker run --rm node bash -c 'flotilla work land b'",
    "sudo -u node bash -c 'flotilla work land b'",
    "perl x.pl python3 -c 'flotilla work land b'",
    "fish -C 'flotilla work land b'",
    "python3 -m timeit -n1 \"import os; os.system('flotilla work land b')\"",
    "node --import 'data:text/javascript,flotilla work land b'",
    "perl '-Mstrict;`flotilla work land b`' /dev/null",
    "python3 -m flotilla work show feat/x",
    "flotilla lane run -- python -m pytest -k flotilla tests/",
]


@pytest.mark.parametrize("command", ASKED_AS_AT_0_28_0)
def test_code_or_arguments_naming_flotilla_after_an_interpreter_ask_as_at_0_28_0(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] in ("ask", "deny"), answer


SHARE_IS_A_WORD = [
    "bash -c \"flotilla rig run --why 'share gpu' -- pytest\"",
    "bash -c \"grep -rn rig flotilla | grep share\"",
    "python3 -c \"print('flotilla rig run --why share-it')\"",
]


@pytest.mark.parametrize("command", SHARE_IS_A_WORD)
def test_the_word_share_near_rig_in_wrapped_code_is_not_a_refusal(tmp_path, monkeypatch, command):
    """`share` is a common word: only `rig share` spelled as the CLI spells it is the person's move (final review of
    0.28.1, M1); built from pieces it stays a gap of decision 245, as it was before share was guarded."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") != "deny", answer


STILL_REFUSED = [
    "python3 -m flotilla work approve feat/x",
    "python3 /opt/p/bin/flotilla rig open --hours 1",
    "python3 tools/x.py flotilla work approve feat/x",
    "python3 -X dev -c \"import subprocess; subprocess.run(['flotilla', 'work', 'approve', 'x'])\"",
]


@pytest.mark.parametrize("command", STILL_REFUSED)
def test_a_persons_move_in_a_scripts_arguments_is_still_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


STILL_ASKED = [
    "python3 tools/x.py work approve feat/x",          # a move through a program the guard cannot read
    "bash -lc 'flotilla work show feat/x'",            # code handed as text that runs flotilla
    "node -e \"require('child_process').execSync('flotilla status')\"",
]


@pytest.mark.parametrize("command", STILL_ASKED)
def test_code_handed_as_text_or_an_unreadable_move_still_asks(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "ask", answer


# the commit security review of c4d060a: what the script-or-code reading must not be fooled by - fail closed
CODE_HOWEVER_SPELLED = [
    "bash --rcfile /tmp/rc -c 'flotilla work show feat/x'",
    "bash --init-file /tmp/rc -c 'flotilla work show feat/x'",
    "node --eval=\"require('child_process').execSync('flotilla status')\"",
    "node --require ./x.js -e \"require('child_process').execSync('flotilla status')\"",
    "fish --command 'flotilla work show feat/x'",
    "python3 <(echo \"import os; os.system('flotilla status')\")",
    "python3 /dev/fd/3 3<<< \"import os; os.system('flotilla work approve x')\"",   # stdin: moves refused
]


@pytest.mark.parametrize("command", CODE_HOWEVER_SPELLED)
def test_code_handed_as_text_however_spelled_still_asks(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] in ("ask", "deny"), answer



SHARE_BUILT_IN_CODE = [
    "python3 - <<'EOF'\nimport subprocess\ncmd = ['flotilla', 'rig']\ncmd.append('share')\nsubprocess.run(cmd + ['x'])\nEOF",
    "python3 -c \"import subprocess; c=['flotilla','rig']; c.append('unshare'); subprocess.run(c+['x'])\"",
]


@pytest.mark.parametrize("command", SHARE_BUILT_IN_CODE)
def test_share_built_into_an_argv_in_code_is_refused_on_stdin_too(tmp_path, monkeypatch, command):
    """The commit security review of bd6b7dd: stdin-fed code is never asked about, only refused, so `share` must stay
    a move word there - as a quoted token, which prose does not use."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer



SHARE_AMONG_WORDS_ON_STDIN = [
    "python3 - <<'EOF'\nimport subprocess\nsubprocess.run('flotilla rig ' + 'now share'.split()[1] + ' x', shell=True)\nEOF",
    "cat <<'EOF' | python3\nimport os\nos.system('flotilla rig ' + 'it unshare'.split()[1] + ' x')\nEOF",
]


@pytest.mark.parametrize("command", SHARE_AMONG_WORDS_ON_STDIN)
def test_share_among_other_words_in_stdin_code_naming_flotilla_asks(tmp_path, monkeypatch, command):
    """The commit security review of c7c5eba: a quoted token alone missed `share` inside a longer string. Refusing any
    such string would refuse prose; stdin code that names flotilla and has `share` after `rig` asks the person."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] in ("ask", "deny"), answer



SHARE_BEFORE_RIG = [
    "python3 - <<'EOF'\nimport subprocess\nm = 'share'\nsubprocess.run(['flotilla', 'rig', m, 'x'])\nEOF",
    "cat <<'EOF' | python3\nimport os\nm = 'it unshare'.split()[1]\nos.system('flotilla rig ' + m + ' x')\nEOF",
]


@pytest.mark.parametrize("command", SHARE_BEFORE_RIG)
def test_share_before_rig_in_code_naming_flotilla_asks_too(tmp_path, monkeypatch, command):
    """The commit security review of 0dccc07: the ask read `rig` before `share`, so a word set first and used after
    passed. Order no longer matters; a move assembled from pieces of words stays decision 245's gap."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] in ("ask", "deny"), answer
