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


def ask(root, command, monkeypatch, tmp_path, cwd=None, mode="auto", session="sid-sender"):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("FLOTILLA_GATE_OVERRIDE", raising=False)
    payload = {"cwd": str(cwd or root), "tool_name": "Bash", "tool_input": {"command": command, "description": "d"},
               "permission_mode": mode, "session_id": session}
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
                                     "/opt/flotilla/bin/flotilla lane run --for x -- uv run pytest",
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


CLI_PATH = str(ROOT / "bin" / "flotilla")


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


CLI = str(hooks.CLI)


def _allowed(answer) -> bool:
    return bool(answer) and answer.get("permissionDecision") == "allow"


#: The person's opt-in: without it nothing flotilla checks is let past auto mode's classifier (directory readiness).
OPTED = '\n[permissions]\nskip_classifier_for_checked = true\n'


@pytest.mark.parametrize("command", [f"{CLI} work land feat/x", f"{CLI} status", f"{CLI} work reconcile"])
def test_flotillas_own_commands_pass_the_classifier(tmp_path, monkeypatch, command):
    """Twosuns field test of 0.6.7, W11: in auto mode Claude Code's classifier refused `work land` - a ledger record -
    as "Merge Without Review". The guard hook allows exactly what the broker's own-command check passes (decisions
    199, 200); a hook's allow passes the classifier (measured on Claude Code 2.1.288)."""
    assert _allowed(ask(onboarded(tmp_path, extra=OPTED), command, monkeypatch, tmp_path))


@pytest.mark.parametrize("command", [f"{CLI} work land feat/x", f"{CLI} status"])
def test_without_the_persons_opt_in_nothing_passes_the_classifier(tmp_path, monkeypatch, command):
    """Directory readiness: the Software Directory Policy says software must not "evade or enable users to circumvent
    Claude's safety guardrails". Letting checked commands past auto mode's classifier is the person's choice, made
    in the profile on trunk (`[permissions] skip_classifier_for_checked`), and off unless they make it."""
    assert not _allowed(ask(onboarded(tmp_path), command, monkeypatch, tmp_path))


@pytest.mark.parametrize("command", [
    f"{CLI} work land feat/x --skip-event pre-landed --skip-why y",   # an event gate is the person's to skip
    f"{CLI} lane run --for x -- sh -c 'git push --force origin HEAD:main'",   # review of 0.6.10, C1
    f"{CLI} events check --tree .",   # review of 0.6.10, C2: runs scripts the seat wrote
    f"{CLI} status; touch x",
    f"{CLI} work approve feat/x",
    f"{CLI} work hand x --as 'sender 1'",
    f"{CLI} receipt run --purpose push",
])
def test_what_steps_around_a_check_is_left_to_the_classifier(tmp_path, monkeypatch, command):
    assert not _allowed(ask(onboarded(tmp_path, extra=OPTED), command, monkeypatch, tmp_path))


def _sender_tree(tmp_path, monkeypatch, opted=True):
    """A direct-flow project whose main checkout is the sender's home tree, as the ledger records it."""
    from flotilla.ledger import core
    from flotilla.ledger.commands import open_ledger
    from flotilla.posts import install_templates
    from guardkit import IDENTITY, git
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = onboarded(tmp_path, extra=OPTED if opted else "", push=False)
    text = (root / ".flotilla" / "project.toml").read_text(encoding="utf-8")
    (root / ".flotilla" / "project.toml").write_text(text.replace('mode = "direct"',
                                                                  'mode = "direct"\nmerge_authorized_by = "sender"'),
                                                     encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    git(root, *IDENTITY, "commit", "-q", "-m", "posts")
    git(root, "push", "-q", "origin", "main")
    from ledgerkit import actor
    ledger = open_ledger(root)
    core.reserve(ledger, actor(ledger, "sender 1"), "fleet/sender-1", tree=str(root))
    names = {"sid-sender": "sender 1", "sid-sender-2": "sender 2", "sid-main": "main session 1"}
    monkeypatch.setattr("flotilla.guards.run._caller_name", lambda session_id: names.get(session_id, ""))
    return root


def test_the_senders_exact_push_of_accounted_work_passes_the_classifier(tmp_path, monkeypatch):
    """W9: the classifier refused the sender's push of reviewed work as "Merge Without Review". The guard allows it
    only when everything flotilla checks holds: the post may land, the tree is its home, the flow is direct, the
    receipt is green over the pushed revision, and every commit it carries is accounted for by the ledger."""
    from guardkit import IDENTITY, git, receipt
    root = _sender_tree(tmp_path, monkeypatch)
    git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "carries no change")   # accounted: nothing in it
    receipt(root, tmp_path / "state")
    assert _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))
    assert not _allowed(ask(root, "git push origin HEAD:main", monkeypatch, tmp_path))   # the tree is named, always
    assert not _allowed(ask(root, f"git -C {root} push --force origin HEAD:main", monkeypatch, tmp_path))


def test_a_push_of_unreviewed_work_is_left_to_the_classifier(tmp_path, monkeypatch):
    """Review of 0.6.10, I2: in a sender-merges flow the push guard asks for green tests, not for review; the
    classifier's "Merge Without Review" was the wall against unreviewed commits, and an allow must not pass it."""
    from guardkit import IDENTITY, git, receipt
    root = _sender_tree(tmp_path, monkeypatch)
    (root / "own.txt").write_text("the sender's own change\n", encoding="utf-8")
    git(root, "add", "own.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "unreviewed")
    receipt(root, tmp_path / "state")
    answer = ask(root, "git push origin HEAD:main", monkeypatch, tmp_path)
    assert not _allowed(answer)


def test_a_push_from_a_tree_whose_owner_may_not_land_is_left_to_the_classifier(tmp_path, monkeypatch):
    from flotilla.ledger import core
    from flotilla.ledger.commands import open_ledger
    from guardkit import IDENTITY, git, receipt
    from ledgerkit import actor
    root = _sender_tree(tmp_path, monkeypatch)
    git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "carries no change")
    receipt(root, tmp_path / "state")
    other = tmp_path / "elsewhere"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(other), "main")
    core.reserve(open_ledger(root), actor(open_ledger(root), "main session 1"), "fleet/main-1", tree=str(other))
    assert not _allowed(ask(root, f"git -C {other} push origin HEAD:main", monkeypatch, tmp_path,
                            session="sid-main"))   # its owner calls, and that owner's post may not land


def test_a_push_inside_a_compound_command_is_left_to_the_classifier(tmp_path, monkeypatch):
    from guardkit import IDENTITY, git, receipt
    root = _sender_tree(tmp_path, monkeypatch)
    git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "carries no change")
    receipt(root, tmp_path / "state")
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main; touch x", monkeypatch, tmp_path))
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main && touch x", monkeypatch, tmp_path))


def test_a_push_let_through_by_a_recorded_override_is_not_allowed_past_the_classifier(tmp_path, monkeypatch):
    """An override lets a push without a receipt through flotilla's guard, recorded; it is the person's knowing
    step, not a check that passed, so it gives no allow."""
    from guardkit import IDENTITY, git
    root = _sender_tree(tmp_path, monkeypatch)
    git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "carries no change")   # no receipt run
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    payload = {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": "git push origin HEAD:main"},
               "permission_mode": "auto"}
    monkeypatch.setenv("FLOTILLA_GATE_OVERRIDE", "the person said so")
    out = io.StringIO()
    hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out)
    answer = json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None
    assert answer is not None and "override" in json.dumps(answer) and not _allowed(answer)


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "dontAsk", "plan", ""])
def test_the_allow_is_for_auto_mode_only(tmp_path, monkeypatch, mode):
    """The allow answers auto mode's classifier. In `ask` mode the person sees every command they promised to see,
    and the person's own session is asked as before; the hook input names the mode (measured on 2.1.288)."""
    from guardkit import IDENTITY, git, receipt
    root = _sender_tree(tmp_path, monkeypatch)
    git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "carries no change")
    receipt(root, tmp_path / "state")
    assert not _allowed(ask(root, "git push origin HEAD:main", monkeypatch, tmp_path, mode=mode))
    assert not _allowed(ask(root, f"{CLI} status", monkeypatch, tmp_path, mode=mode))



@pytest.mark.parametrize("move", ["approv[e]", "a*", "?pprove", "appro?e", "'approve'"])
def test_the_person_guard_reads_a_glob_as_whatever_it_may_expand_to(tmp_path, monkeypatch, move):
    """Review of 0.6.10, C2: `approv[e]` was no move the guard knew, and bash expands it to `approve` given a file
    of that name in the cwd - the person's one move, made by a tool call."""
    answer = ask(onboarded(tmp_path), f"{CLI} work {move} feat/x", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "person" in answer["permissionDecisionReason"]



def _accounted_sender(tmp_path, monkeypatch):
    from guardkit import IDENTITY, git, receipt
    root = _sender_tree(tmp_path, monkeypatch)
    git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "carries no change")
    receipt(root, tmp_path / "state")
    assert _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))   # the baseline
    return root


def test_a_cd_or_a_relative_tree_hides_which_tree_is_pushed(tmp_path, monkeypatch):
    """Review of 0.6.10, C1: `cd <tree> &&` moved the push to another tree while the checks read the payload's."""
    root = _accounted_sender(tmp_path, monkeypatch)
    for command in (f"cd {root} && git push origin HEAD:main", f"cd {root}; git -C {root} push origin HEAD:main",
                    "git -C . push origin HEAD:main", f"command git -C {root} push origin HEAD:main",
                    f"(git -C {root} push origin HEAD:main)", f"git  -C {root} push origin HEAD:main"):
        assert not _allowed(ask(root, command, monkeypatch, tmp_path)), command


def test_a_remote_tracking_ref_moved_by_the_seat_hides_nothing(tmp_path, monkeypatch):
    """Review of 0.6.10, I1: the accounting base was the local refs/remotes/origin/<trunk>, which the seat can move
    over its own unreviewed commit; the base is asked of origin."""
    from guardkit import IDENTITY, git, receipt
    root = _accounted_sender(tmp_path, monkeypatch)
    (root / "own.txt").write_text("unreviewed\n", encoding="utf-8")
    git(root, "add", "own.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "unreviewed")
    receipt(root, tmp_path / "state")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


@pytest.mark.parametrize("config", [
    ("remote.origin.pushurl", "/tmp/elsewhere.git"),          # review of 0.6.10, I2: history goes elsewhere
    ("url./tmp/elsewhere.git.pushInsteadOf", "ORIGIN"),
    ("core.hooksPath", "/tmp/hooks"),                          # code that runs on the push, past the classifier
])
def test_a_push_whose_destination_or_hooks_were_changed_is_left_to_the_classifier(tmp_path, monkeypatch, config):
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    key, value = config
    if value == "ORIGIN":
        value = git(root, "remote", "get-url", "origin")
    git(root, "config", key, value)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_the_allowed_push_runs_no_repository_hook_and_pushes_the_checked_commit(tmp_path, monkeypatch):
    """Third review of 0.6.10, I2, I3: a reference-transaction hook ran on the push, and the commit pushed was
    whatever HEAD was when it ran. The allow now rewrites the command: no repository hook, the checked commit."""
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    answer = ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path)
    head = git(root, "rev-parse", "HEAD")
    rewritten = answer["updatedInput"]["command"]
    assert "core.hooksPath=" in rewritten and f"{head}:refs/heads/main" in rewritten and "HEAD:" not in rewritten
    assert answer["updatedInput"].get("description") == "d"


def test_only_the_owner_of_the_senders_tree_is_allowed_its_push(tmp_path, monkeypatch):
    """Third review of 0.6.10, M1: any seat could push the sender's tree."""
    root = _accounted_sender(tmp_path, monkeypatch)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path, session="sid-main"))
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path, session="unknown"))


@pytest.mark.parametrize("key, value", [
    ("remote.origin.receivepack", "touch /tmp/x; git-receive-pack"),   # third review, I2 (c)
    ("remote.origin.uploadpack", "touch /tmp/x; git-upload-pack"),
    ("core.sshCommand", "touch /tmp/x; ssh"), ("credential.helper", "!touch /tmp/x"),
    ("include.path", "/tmp/elsewhere.cfg"), ("push.recurseSubmodules", "on-demand"),
])
def test_repository_config_that_runs_code_or_redirects_the_push_is_left_to_the_classifier(tmp_path, monkeypatch,
                                                                                         key, value):
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    git(root, "config", key, value)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_a_second_push_url_is_left_to_the_classifier(tmp_path, monkeypatch):
    """Third review of 0.6.10, I2 (b): `get-url --push` printed only the first of two push URLs."""
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    url = git(root, "remote", "get-url", "origin")
    git(root, "config", "--add", "remote.origin.pushurl", url)
    git(root, "config", "--add", "remote.origin.pushurl", "/tmp/elsewhere.git")
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_a_checkout_of_another_repository_is_not_the_senders_tree(tmp_path, monkeypatch):
    """Review of 0.6.10, I2: a clone elsewhere, with its own history and config, pushing to the same origin."""
    from flotilla.ledger import core
    from flotilla.ledger.commands import open_ledger
    from guardkit import IDENTITY, git, receipt
    from ledgerkit import actor
    root = _accounted_sender(tmp_path, monkeypatch)
    other = tmp_path / "other-clone"
    git(tmp_path, "clone", "-q", git(root, "remote", "get-url", "origin"), str(other))
    ledger = open_ledger(root)
    core.reserve(ledger, actor(ledger, "sender 2"), "fleet/sender-2", tree=str(other))
    session = "sid-sender-2"   # its owner calls: only the other repository stands between it and an allow
    git(other, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "carries no change")
    receipt(other, tmp_path / "state")
    assert not _allowed(ask(root, f"git -C {other} push origin HEAD:main", monkeypatch, tmp_path, session=session))


@pytest.mark.parametrize("command", [
    f"{CLI} work {{approve,feat}} x",                           # brace expansion: no file needed
    f"{CLI} work $'approve' feat/x", f'{CLI} work $"approve" feat/x',   # ANSI-C and locale quoting
    f"{CLI} work appro\\\nve feat/x",                            # a line continuation joins the word again
    f"{CLI} work @(approve) feat/x",                             # extglob
    f"env -C /tmp -u work -u show {CLI} work approve feat/x",    # a decoy `work` before flotilla's
    f"{CLI} lane run --note work --for show -- {CLI} work approve feat/x",   # a decoy flotilla before the real one
])
def test_the_person_guard_reads_every_spelling_bash_turns_into_approve(tmp_path, monkeypatch, command):
    """Third review of 0.6.10, I1: each of these reached `approve` in bash and passed the guard."""
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "person" in answer["permissionDecisionReason"], \
        (command, answer)


def test_the_person_guard_still_lets_ordinary_moves_through(tmp_path, monkeypatch):
    root = onboarded(tmp_path)
    for command in (f"{CLI} work hand feat/x", f"{CLI} work show feat/approve-button", f"{CLI} status"):
        answer = ask(root, command, monkeypatch, tmp_path, mode="default")
        assert not answer or answer.get("permissionDecision") != "deny", (command, answer)


def test_an_own_command_on_another_repository_is_left_to_the_classifier(tmp_path, monkeypatch):
    """Third review of 0.6.10, C1: `--root` named any repository the seat made - its profile's commands and event
    scripts then ran under the allow. Only a checkout of this project's own repository is passed; `lane` not at all
    (bare `lane` may run the profile's queue command)."""
    from guardkit import IDENTITY, git
    root = onboarded(tmp_path, extra=OPTED)
    other = tmp_path / "made-by-a-seat"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    git(other, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "x")
    assert not _allowed(ask(root, f"{CLI} work show feat/x --root {other}", monkeypatch, tmp_path))
    assert not _allowed(ask(root, f"{CLI} status --root={other}", monkeypatch, tmp_path))
    assert _allowed(ask(root, f"{CLI} work show feat/x --root {root}", monkeypatch, tmp_path))
    assert not _allowed(ask(root, f"{CLI} lane", monkeypatch, tmp_path))
    outside = tmp_path / "outside"
    outside.mkdir()
    assert not _allowed(ask(root, f"{CLI} status", monkeypatch, tmp_path, cwd=outside))


def test_a_second_origin_url_is_left_to_the_classifier(tmp_path, monkeypatch):
    """Final review of 0.6.10, C1: two local url values passed the key check; `git push origin` pushed to both, and
    `ls-remote origin` asked the first - a decoy that could report any base."""
    from guardkit import IDENTITY, git, receipt
    root = _accounted_sender(tmp_path, monkeypatch)
    (root / "own.txt").write_text("unreviewed\n", encoding="utf-8")
    git(root, "add", "own.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "unreviewed")
    receipt(root, tmp_path / "state")
    decoy = tmp_path / "decoy.git"
    git(tmp_path, "init", "-q", "--bare", str(decoy))
    git(root, "push", "-q", str(decoy), "HEAD:refs/heads/main")   # the decoy says origin's trunk is HEAD already
    url = git(root, "remote", "get-url", "origin")
    git(root, "config", "--unset-all", "remote.origin.url")
    git(root, "config", "--add", "remote.origin.url", str(decoy))
    git(root, "config", "--add", "remote.origin.url", url)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_the_allowed_push_goes_to_the_one_origin_url_named_explicitly(tmp_path, monkeypatch):
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    url = git(root, "remote", "get-url", "origin")
    rewritten = ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path)["updatedInput"]["command"]
    assert f" push {url} " in rewritten and " push origin " not in rewritten


def test_a_request_to_leave_the_sandbox_gets_no_allow(tmp_path, monkeypatch):
    """Final review of 0.6.10, M1: the allow skipped the prompt dangerouslyDisableSandbox would cause."""
    root = onboarded(tmp_path, extra=OPTED)
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    payload = {"cwd": str(root), "tool_name": "Bash", "permission_mode": "auto", "session_id": "s",
               "tool_input": {"command": f"{CLI} status", "dangerouslyDisableSandbox": True}}
    out = io.StringIO()
    hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out)
    assert not _allowed(json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None)


def test_config_a_global_include_reads_from_inside_the_repository_is_not_trusted(tmp_path, monkeypatch):
    """Final review of 0.6.10, M2: a global includeIf pointing into the repository reported its entries as global."""
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    inside = root / ".git" / "extra.cfg"
    inside.write_text("[remote \"origin\"]\n\tpushurl = /tmp/elsewhere.git\n", encoding="utf-8")
    global_cfg = tmp_path / "global.cfg"
    global_cfg.write_text(f'[includeIf "gitdir:{root}/"]\n\tpath = {inside}\n', encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_a_replaced_history_is_left_to_the_classifier(tmp_path, monkeypatch):
    """The ledger's accounting reads history through refs/replace (git honours them by default), while a push sends
    the real objects: a replace ref could dress an unreviewed commit as an accounted one. Any replace ref, graft or
    shallow history and the classifier decides."""
    from guardkit import IDENTITY, git, receipt
    root = _accounted_sender(tmp_path, monkeypatch)
    accounted = git(root, "rev-parse", "HEAD")
    (root / "own.txt").write_text("unreviewed\n", encoding="utf-8")
    git(root, "add", "own.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "unreviewed")
    receipt(root, tmp_path / "state")
    git(root, "replace", "-f", git(root, "rev-parse", "HEAD"), accounted)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


@pytest.mark.parametrize("url, plain", [
    ("git@github.com:o/r.git", True), ("https://github.com/o/r.git", True), ("ssh://git@host.example/o/r", True),
    ("/srv/git/r.git", True),
    ("evil.example:x@github.com:o/r", False),          # git connects to evil.example; the ledger key said github
    ("https://user:pass@github.com/o/r", False), ("ext::sh -c x", False), ("file://./r", False),
    ("git@github.com:o/r with space", False), ("-uhost:o/r", False),
])
def test_only_plain_origin_urls_are_pushed_past_the_classifier(url, plain):
    """Review of the sender's push allow, I3: the ledger's key and git's connection target parsed one URL apart."""
    from flotilla.guards import run as guard_run
    assert guard_run._plain_url(url) is plain


def _origin_moves(tmp_path, root, old, new, message):
    """origin's trunk gets a commit replacing `old` with `new` in the profile, made in a clone of its own: what the
    real trunk says, which the session's tree has not been told."""
    from guardkit import IDENTITY, git
    clone = tmp_path / "another-clone"
    git(tmp_path, "clone", "-q", git(root, "config", "--get", "remote.origin.url"), str(clone))
    path = clone / ".flotilla" / "project.toml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(old, new) if old else text + new, encoding="utf-8")
    git(clone, *IDENTITY, "commit", "-q", "-am", message)
    git(clone, "push", "-q", "origin", "main")
    return git(clone, "rev-parse", "HEAD")


def test_rules_on_a_repointed_trunk_ref_grant_no_allow(tmp_path, monkeypatch):
    """Scan of 0.6.10, F1: the profile and posts that grant the allow were read from the local `origin/<trunk>`, a
    ref any session can repoint. Origin's trunk moved off the direct flow; the session points its ref back at the
    commit that still says direct, and the rules that grant must be origin's, not the ref's."""
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    forged = git(root, "rev-parse", "refs/remotes/origin/main")
    _origin_moves(tmp_path, root, 'mode = "direct"', 'mode = "pr"', "the person moves trunk to pull requests")
    git(root, "fetch", "-q", "origin")
    git(root, "update-ref", "refs/remotes/origin/main", forged)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_a_trunk_ref_that_disagrees_with_origin_grants_no_allow(tmp_path, monkeypatch):
    """Scan of 0.6.10, F1: the local `origin/<trunk>` is what the guards and the accounting read; where it is not
    origin's trunk, what they judged is not what the push lands on, whatever the profile says."""
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    stale = git(root, "rev-parse", "refs/remotes/origin/main")
    _origin_moves(tmp_path, root, "", "# a comment the person added\n", "a comment on trunk")
    git(root, "fetch", "-q", "origin")
    git(root, "update-ref", "refs/remotes/origin/main", stale)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_rules_are_read_at_the_revision_origin_named(tmp_path, monkeypatch):
    """Scan of 0.6.10, F1: a ref checked against origin and then read again may have moved in between (another
    session repoints it in that window). The rules that grant are read at the revision origin named, never at the
    ref."""
    from flotilla.ledger import gitq
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    forged = git(root, "rev-parse", "refs/remotes/origin/main")
    _origin_moves(tmp_path, root, 'mode = "direct"', 'mode = "pr"', "the person moves trunk to pull requests")
    git(root, "fetch", "-q", "origin")   # the ref is origin's at the check ...
    monkeypatch.setattr(gitq, "trunk_ref", lambda *a, **k: forged)   # ... and is read again after it moved
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_the_accounting_counts_by_the_rules_origin_named(tmp_path, monkeypatch):
    """Scan of 0.6.10, F1: the ledger that accounts for the pushed commits reads the profile too (whether a person
    approves merges); it is built from the rules read at origin's trunk, not from a ref read again afterwards."""
    from flotilla.ledger import core, gitq, handover, reading
    from flotilla.ledger.commands import open_ledger
    from guardkit import IDENTITY, git, receipt
    from ledgerkit import actor
    root = _accounted_sender(tmp_path, monkeypatch)
    git(root, "switch", "-q", "-c", "feat/x")
    (root / "x.txt").write_text("reviewed work\n", encoding="utf-8")
    git(root, "add", "x.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "reviewed work")
    git(root, "switch", "-q", "main")
    ledger = open_ledger(root)
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    row = handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=row.tip)
    git(root, "merge", "-q", "--ff-only", "feat/x")
    receipt(root, tmp_path / "state")
    assert _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))   # accepted, so accounted
    forged = git(root, "rev-parse", "refs/remotes/origin/main")
    _origin_moves(tmp_path, root, 'merge_authorized_by = "sender"', 'merge_authorized_by = "human"',
                  "the person approves every merge now")
    git(root, "fetch", "-q", "origin")
    monkeypatch.setattr(gitq, "trunk_ref", lambda *a, **k: forged)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_moves_made_under_a_forged_trunk_ref_account_for_nothing(tmp_path, monkeypatch):
    """Final review of the scan fixes, C1: the allow read its rules at origin's trunk, but the ledger moves it counts
    had been made under whatever the local ref said then. A session points the ref at a commit saying review depth
    `none`, queues unreviewed work through flotilla's own (auto-allowed) moves, puts the ref back and pushes: every
    move a counted row rests on must have been made under the rules origin's trunk carries."""
    from flotilla.ledger import core, delivery
    from flotilla.ledger.commands import open_ledger
    from guardkit import IDENTITY, git, receipt
    from ledgerkit import actor
    root = _accounted_sender(tmp_path, monkeypatch)
    real = git(root, "rev-parse", "refs/remotes/origin/main")
    git(root, "switch", "-q", "-c", "forge", real)
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text(encoding="utf-8") + '\n[review]\ndepth = "none"\n', encoding="utf-8")
    git(root, *IDENTITY, "commit", "-q", "-am", "review nothing")
    forged = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    git(root, "branch", "-q", "-D", "forge")
    git(root, "update-ref", "refs/remotes/origin/main", forged)
    git(root, "switch", "-q", "-c", "feat/evil", "main")
    (root / "evil.txt").write_text("nobody read this\n", encoding="utf-8")
    git(root, "add", "evil.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "unreviewed")
    git(root, "switch", "-q", "main")
    ledger = open_ledger(root)
    core.claim(ledger, actor(ledger, "main session 1"), "feat/evil")
    delivery.queue(ledger, actor(ledger, "sender 1"), "feat/evil")   # no review asked: the forged depth says none
    git(root, "update-ref", "refs/remotes/origin/main", real)
    git(root, "merge", "-q", "--ff-only", "feat/evil")
    receipt(root, tmp_path / "state")
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_the_receipt_the_allow_names_is_asked_under_origins_rules(tmp_path, monkeypatch):
    """Final review of the scan fixes, I1: the allow says the receipt is green, but it took that from the push guard,
    which read its tiers from the local ref; a ref repointed while the guard ran made "no tier applies to push" pass.
    The allow asks for the receipt again, under the rules read at origin's trunk."""
    from flotilla.ledger import gitq
    from guardkit import IDENTITY, git
    root = _accounted_sender(tmp_path, monkeypatch)
    real = git(root, "rev-parse", "refs/remotes/origin/main")
    git(root, "switch", "-q", "-c", "forge", real)
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text(encoding="utf-8").replace('required_for = ["push"]', 'required_for = []'),
                       encoding="utf-8")
    git(root, *IDENTITY, "commit", "-q", "-am", "no tier for push")
    forged = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "no receipt over this one")
    monkeypatch.setattr(gitq, "trunk_ref", lambda *a, **k: forged)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_the_senders_push_waits_for_the_persons_opt_in(tmp_path, monkeypatch):
    from guardkit import IDENTITY, git, receipt
    root = _sender_tree(tmp_path, monkeypatch, opted=False)
    git(root, *IDENTITY, "commit", "-q", "--allow-empty", "-m", "carries no change")
    receipt(root, tmp_path / "state")
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def test_the_opt_in_is_read_where_origin_names_trunk(tmp_path, monkeypatch):
    """The opt-in grants, so for the push it is read at the revision origin names (scan of 0.6.10, F1): a ref
    repointed at a commit that opts in grants nothing where origin's trunk does not."""
    from flotilla.ledger import gitq
    from guardkit import git
    root = _accounted_sender(tmp_path, monkeypatch)
    forged = git(root, "rev-parse", "refs/remotes/origin/main")
    _origin_moves(tmp_path, root, "skip_classifier_for_checked = true", "skip_classifier_for_checked = false",
                  "the person takes the opt-in back")
    git(root, "fetch", "-q", "origin")
    monkeypatch.setattr(gitq, "trunk_ref", lambda *a, **k: forged)
    assert not _allowed(ask(root, f"git -C {root} push origin HEAD:main", monkeypatch, tmp_path))


def _opt_in_locally(root):
    from guardkit import IDENTITY, git
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text(encoding="utf-8") + OPTED, encoding="utf-8")
    git(root, *IDENTITY, "commit", "-q", "-am", "opt in, here only")


def test_an_opt_in_the_session_wrote_in_its_tree_grants_nothing(tmp_path, monkeypatch):
    """Review of the directory readiness work, I1: the opt-in for flotilla's own commands was read through the local
    trunk ref - or, where that ref carried no profile, the tree's own. A seat edits its tree's profile, or repoints
    the ref, and its moves skip the classifier without the person choosing it. The opt-in is read where origin
    names trunk, for every allow."""
    from guardkit import git
    root = onboarded(tmp_path)   # origin's trunk: no opt-in
    _opt_in_locally(root)
    assert not _allowed(ask(root, f"{CLI} status", monkeypatch, tmp_path))   # committed, not on origin
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert not _allowed(ask(root, f"{CLI} status", monkeypatch, tmp_path))   # the ref repointed at it
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text(encoding="utf-8").replace('branch = "main"', 'branch = "nope"'),
                       encoding="utf-8")
    assert not _allowed(ask(root, f"{CLI} status", monkeypatch, tmp_path))   # trunk renamed: the tree's profile


def test_a_branch_on_origin_that_calls_itself_trunk_grants_nothing(tmp_path, monkeypatch):
    """A feature branch can reach origin without any check; it names itself trunk in its own profile and opts in.
    Trunk is origin's default branch, asked of origin."""
    from guardkit import git
    root = onboarded(tmp_path)
    git(root, "switch", "-q", "-c", "evil")
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text(encoding="utf-8").replace('branch = "main"', 'branch = "evil"') + OPTED,
                       encoding="utf-8")
    from guardkit import IDENTITY
    git(root, *IDENTITY, "commit", "-q", "-am", "evil is trunk now")
    git(root, "push", "-q", "origin", "evil")
    assert not _allowed(ask(root, f"{CLI} status", monkeypatch, tmp_path))
    git(root, "remote", "set-head", "origin", "evil")   # the local idea of origin's default branch, repointed
    assert not _allowed(ask(root, f"{CLI} status", monkeypatch, tmp_path))


def test_a_session_is_told_once_that_the_opt_in_is_off(tmp_path, monkeypatch):
    """Review of the directory readiness work, M6: on updating, auto mode's classifier refuses the sender's push
    and ledger moves again (W9, W11), and nothing said why. Where only the missing opt-in keeps flotilla from
    allowing a command, the session is told so - once - and that turning it on is the person's choice."""
    root = onboarded(tmp_path)
    first = ask(root, f"{CLI} work land feat/x", monkeypatch, tmp_path, session="sid-a")
    assert first and "skip_classifier_for_checked" in first.get("additionalContext", "")
    assert "person" in first["additionalContext"] and not _allowed(first)
    assert ask(root, f"{CLI} status", monkeypatch, tmp_path, session="sid-a") is None   # said once
    assert ask(root, "ls -la", monkeypatch, tmp_path, session="sid-b") is None   # not one of flotilla's own
    assert ask(root, f"{CLI} status", monkeypatch, tmp_path, session="sid-c", mode="default") is None   # auto only


def _human_with_unapproved(tmp_path):
    """Origin's trunk: a person approves merges, the receipt guard on. The tree: one commit nobody approved."""
    from guardkit import IDENTITY, git, receipt
    root = onboarded(tmp_path)
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text().replace('mode = "direct"', 'mode = "direct"\nmerge_authorized_by = "human"'),
                       encoding="utf-8")
    git(root, *IDENTITY, "commit", "-q", "-am", "a person authorizes merges")
    git(root, "push", "-q", "origin", "main")
    (root / "w.txt").write_text("work nobody approved\n", encoding="utf-8")
    git(root, "add", "w.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "unapproved work")
    receipt(root, tmp_path / "state")
    return root


def _pre_push(root, tmp_path, url):
    from flotilla.guards.commands import run_githook
    from guardkit import git
    head = git(root, "rev-parse", "HEAD")
    stdin = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
    import contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_githook("pre-push", root, stdin, env={"FLOTILLA_STATE_DIR": str(tmp_path / "state")},
                           hook_args=("origin", url))
    return code, err.getvalue()


def test_a_replaced_trunk_commit_does_not_change_the_rules_a_push_is_judged_by(tmp_path, monkeypatch):
    """Review of the scan fixes of 0.7.0, C1: the rules were read from the local object store, which honours
    `git replace` - a commit with every guard off stood in for origin's trunk. Both barriers read with replace refs
    and grafts switched off."""
    from guardkit import IDENTITY, git
    root = _human_with_unapproved(tmp_path)
    real = git(root, "rev-parse", "refs/remotes/origin/main")
    git(root, "switch", "-q", "-c", "forge", real)
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text().replace("push_receipt = true", "push_receipt = false")
                       .replace('merge_authorized_by = "human"', 'merge_authorized_by = "sender"'), encoding="utf-8")
    git(root, *IDENTITY, "commit", "-q", "-am", "every guard off")
    forged = git(root, "rev-parse", "HEAD")
    git(root, "switch", "-q", "main")
    git(root, "replace", real, forged)
    answer = ask(root, "git push origin main", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "not approved" in answer["permissionDecisionReason"]
    code, text = _pre_push(root, tmp_path, git(root, "config", "--get", "remote.origin.url"))
    assert code == 1 and "not approved" in text


def test_pre_push_asks_the_destination_git_names_not_the_remote_by_name(tmp_path, monkeypatch):
    """Review of the scan fixes of 0.7.0, C3, C4: origin's fetch URL pointed at a repository the session made,
    whose trunk turned every guard off, while `pushurl` (or a rename, or an insteadOf) sent the push to the real
    one. git hands pre-push the real destination after every rewrite (measured on git 2.53); that is what is asked,
    from outside the repository, so no repository config redirects the question."""
    from guardkit import IDENTITY, git
    root = _human_with_unapproved(tmp_path)
    real_url = git(root, "config", "--get", "remote.origin.url")
    fake = tmp_path / "fake.git"
    git(tmp_path, "clone", "-q", "--bare", real_url, str(fake))
    work = tmp_path / "fake-work"
    git(tmp_path, "clone", "-q", str(fake), str(work))
    profile = work / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text().replace("push_receipt = true", "push_receipt = false")
                       .replace('merge_authorized_by = "human"', 'merge_authorized_by = "sender"'), encoding="utf-8")
    git(work, *IDENTITY, "commit", "-q", "-am", "every guard off")
    git(work, "push", "-q", "origin", "main")
    git(root, "config", "remote.origin.url", str(fake))
    git(root, "config", "remote.origin.pushurl", real_url)
    git(root, "fetch", "-q", "origin")
    code, text = _pre_push(root, tmp_path, real_url)   # what git passes: the push URL
    assert code == 1 and "not approved" in text


def test_a_broken_tree_profile_and_an_override_do_not_open_a_push(tmp_path, monkeypatch):
    """Review of the scan fixes of 0.7.0, C2: a tree profile that could not be read made the push guard's verdict
    an overridable failure, and pre-push let any failure through with the override, unrecorded. The push is judged
    by origin's rules either way, and the person's approval has no override."""
    root = _human_with_unapproved(tmp_path)
    (root / ".flotilla" / "project.toml").write_text("[[[", encoding="utf-8")
    answer = ask(root, 'FLOTILLA_GATE_OVERRIDE="x" git push origin main', monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "not approved" in answer["permissionDecisionReason"]
