import io
import json
import re
import sys
from contextlib import redirect_stdout

import pytest

from flotilla import cli
from flotilla.onboard.tomlw import render_toml
from flotilla.posts import TEMPLATE_DIR, install_templates
from ledgerkit import commit, git, merge, repo_with_origin

GREEN = f"{sys.executable} -c \"print('1 passed')\""
CALL = re.compile(r"`flotilla ([a-z-]+)(?: ([a-z-]+))?")


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def subcommands(parser):
    action = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return action.choices


def onboarded(tmp_path, monkeypatch, profile):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text(render_toml(profile), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


PLAIN = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"}}


def test_a_review_cycle_through_the_cli(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    code, out = run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    assert code == 0, out
    tip = commit(tree, "work", "work.txt")
    assert run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")[0] == 0
    assert run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")[0] == 0
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", tip[:8], "--root", str(root),
                        "--as", "review session 1")
    assert code == 0 and "accepted" in out
    code, out = run_cli("work", "show", "feat/x", "--root", str(root))
    history = out.split("history:", 1)[1].split()
    assert [word for word in history if word in ("claim", "hand", "take", "accept")] == ["claim", "hand", "take", "accept"]


def test_a_refusal_exits_2_with_the_reason(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", "HEAD", "--root", str(root),
                        "--as", "main session 1")
    assert code == 2 and "may not `accept`" in out


def test_outside_an_onboarded_project(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    code, out = run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")
    assert code == 2 and "not onboarded" in out


def test_receipt_then_hand(tmp_path, monkeypatch):
    profile = {**PLAIN, "tests": {"tier": [{"name": "unit", "command": GREEN, "required_for": ["handover"]}]}}
    root = onboarded(tmp_path, monkeypatch, profile)
    tree = tmp_path / "app-main-1"
    assert run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")[0] == 0
    commit(tree, "work", "work.txt")
    code, out = run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    assert code == 2 and "receipt run" in out
    code, out = run_cli("receipt", "run", "--purpose", "handover", "--tree", str(tree), "--no-lane")
    assert code == 0, out
    assert run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")[0] == 0


def test_a_broken_post_file_refuses_by_name(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    post = root / ".flotilla" / "posts" / "main.md"
    post.write_text(post.read_text(encoding="utf-8").replace("may: [", "may: [merge, "), encoding="utf-8")
    git(root, "add", ".flotilla")
    commit(root, "a broken post")
    git(root, "push", "-q", "origin", "main")
    code, out = run_cli("work", "claim", "feat/y", "--root", str(root), "--as", "main session 1")
    assert code == 2 and "origin/main:.flotilla/posts/main.md" in out and "merge" in out


def test_every_cli_call_in_the_post_templates_exists():
    top = subcommands(cli.build_parser())
    for path in sorted(TEMPLATE_DIR.glob("*.md")):
        for command, sub in CALL.findall(path.read_text(encoding="utf-8")):
            assert command in top, f"{path.name}: `flotilla {command}`"
            if sub and command in ("work", "tree", "receipt", "onboard", "events"):
                assert sub in subcommands(top[command]), f"{path.name}: `flotilla {command} {sub}`"


def test_the_rules_come_from_trunk_not_from_the_callers_tree(tmp_path, monkeypatch):
    profile = {**PLAIN, "tests": {"tier": [{"name": "unit", "command": GREEN, "required_for": ["handover"]}]}}
    root = onboarded(tmp_path, monkeypatch, profile)
    tree = tmp_path / "app-main-1"
    assert run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")[0] == 0
    commit(tree, "work", "work.txt")
    (tree / ".flotilla" / "project.toml").write_text(render_toml(PLAIN), encoding="utf-8")   # drop the tier, uncommitted
    post = tree / ".flotilla" / "posts" / "main.md"
    post.write_text(post.read_text(encoding="utf-8").replace("may: [", "may: [accept, "), encoding="utf-8")
    code, out = run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    assert code == 2 and "receipt" in out


def test_the_census_switch_is_recorded_in_the_event(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    log = next((tmp_path / "state" / "ledger").glob("*.jsonl")).read_text(encoding="utf-8")
    assert "FLOTILLA_NO_CENSUS" in log


def test_a_damaged_log_is_refused_not_a_traceback(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    log = next((tmp_path / "state" / "ledger").glob("*.jsonl"))
    log.write_text("not json\n" + log.read_text(encoding="utf-8"), encoding="utf-8")
    code, out = run_cli("work", "show", "feat/x", "--root", str(root))
    assert code == 2 and "refused" in out


def add_event(root, name, body):
    folder = root / ".flotilla" / "events"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(body, encoding="utf-8")
    (folder / name).chmod(0o755)


def test_event_scripts_come_from_trunk(tmp_path, monkeypatch):
    from flotilla.ledger.commands import trunk_rules
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    add_event(root, "pre-handed", "#!/bin/sh\nexit 0\n")
    assert "pre-handed" not in trunk_rules(root).events
    git(root, "add", ".flotilla")
    commit(root, "an event")
    git(root, "push", "-q", "origin", "main")
    assert trunk_rules(root).events["pre-handed"] == (b"#!/bin/sh\nexit 0\n", True)


def test_events_schema_prints_the_contract(tmp_path, monkeypatch):
    from flotilla.ledger import events
    code, out = run_cli("events", "schema")
    assert code == 0 and json.loads(out) == events.schema()


def test_events_check_names_a_broken_script_on_trunk(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    add_event(root, "pre-handed", "#!/bin/sh\nexit 3\n")
    git(root, "add", ".flotilla")
    commit(root, "a broken event")
    git(root, "push", "-q", "origin", "main")
    code, out = run_cli("events", "check", "--root", str(root))
    assert code == 1 and "broken" in out and "pre-handed" in out


def test_events_run_replays_a_script_over_a_row(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    add_event(root, "pre-handed", "#!/bin/sh\necho no ticket\nexit 2\n")
    git(root, "add", ".flotilla")
    commit(root, "a rejecting event")
    git(root, "push", "-q", "origin", "main")
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("events", "run", "pre-handed", "--row", "feat/x", "--root", str(root))
    assert code == 2 and "rejected: no ticket" in out


DIRECT_PLAIN = {**PLAIN, "flow": {"mode": "direct"}}


def test_a_direct_push_cycle_through_the_cli(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, DIRECT_PLAIN)
    tree = tmp_path / "app-main-1"
    assert run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")[0] == 0
    tip = commit(tree, "work", "work.txt")
    assert run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")[0] == 0
    assert run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")[0] == 0
    assert run_cli("work", "accept", "feat/x", "--reviewed", tip[:8], "--root", str(root),
                   "--as", "review session 1")[0] == 0
    code, out = run_cli("brief", "--root", str(root))
    assert code == 0 and "`feat/x` (accepted)" in out and "Answer yes to ship 1 row." in out
    assert run_cli("work", "queue", "feat/x", "--root", str(root), "--as", "sender 1")[0] == 0
    merge(root, "feat/x")
    assert run_cli("work", "land", "feat/x", "--root", str(root), "--as", "sender 1")[0] == 0
    code, out = run_cli("work", "ship", "feat/x", "--root", str(root), "--as", "sender 1")
    assert code == 3 and "landed, not pushed" in out
    git(root, "push", "-q", "origin", "main")
    code, out = run_cli("work", "reconcile", "--root", str(root), "--as", "sender 1")
    assert code == 0 and "shipped feat/x" in out
    assert run_cli("work", "close", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("status", "--root", str(root), "--stalled", "4")
    assert code == 0 and "census: unknown" in out and "no open rows" in out and "findings: none" in out
    code, out = run_cli("metrics", "--root", str(root))
    assert code == 0 and "return rate: 0% of handovers came back" in out


def test_status_names_whose_move_it_is(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    assert run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")[0] == 0
    commit(tree, "work", "work.txt")
    assert run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")[0] == 0
    code, out = run_cli("status", "--root", str(root))
    assert "r1 feat/x: handed -> nobody named" in out
    assert "feat/x: nobody_named" in out


def test_a_skipped_event_must_name_a_real_event_and_say_why(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    code, out = run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1",
                        "--skip-event", "pre-nonsense", "--skip-why", "x")
    assert code == 2 and "not an event name" in out
    code, out = run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1",
                        "--skip-event", "pre-claimed")
    assert code == 2 and "--skip-why" in out


def test_broke_prints_the_fix_row_it_filed(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, DIRECT_PLAIN)
    tree = tmp_path / "app-main-1"
    run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    tip = commit(tree, "work", "work.txt")
    run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")
    run_cli("work", "accept", "feat/x", "--reviewed", tip, "--root", str(root), "--as", "review session 1")
    run_cli("work", "queue", "feat/x", "--root", str(root), "--as", "sender 1")
    merge(root, "feat/x")
    run_cli("work", "land", "feat/x", "--root", str(root), "--as", "sender 1")
    git(root, "push", "-q", "origin", "main")
    run_cli("work", "ship", "feat/x", "--root", str(root), "--as", "sender 1")
    code, out = run_cli("work", "broke", "feat/x", "--where", "Settings > Export", "--saw", "nothing happens",
                        "--root", str(root), "--as", "acceptance judge 1")
    assert code == 0 and "fix row r2 `fix/x` filed for main session 1" in out
    assert "flotilla tree switch fix/x" in out and "tree cut" not in out
    code, out = run_cli("status", "--root", str(root))
    assert "r1 feat/x: shipped -> the fix `fix/x` (main session 1)" in out   # not "nobody named" (H26b)


def test_a_session_takes_a_task_in_its_home_tree(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    home = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(home), "origin/main")
    assert run_cli("work", "reserve", "fleet/main-1", "--tree", str(home), "--root", str(root),
                   "--as", "main session 1")[0] == 0
    code, out = run_cli("tree", "switch", "feat/x", "--ref", "LIN-1", "--root", str(home), "--as", "main session 1")
    assert code == 0 and "feat/x: claimed" in out
    assert git(home, "rev-parse", "--abbrev-ref", "HEAD") == "feat/x"


def test_an_empty_branch_is_never_called_finished(tmp_path):
    from flotilla.ledger import commands, core, receipts
    from ledgerkit import actor, git, make_ledger, repo_with_origin
    root = repo_with_origin(tmp_path)
    profile = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "direct"},
               "tests": {"tier": [{"name": "t", "command": "true", "required_for": ["handover"]}]}}
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    git(root, "branch", "fix/empty", "main")
    row = core.claim(ledger, actor(ledger, "main session 1"), "fix/empty")
    receipts.run_receipt(root, state=tmp_path / "state", repo_key=ledger.repo_key, purpose="handover",
                         profile=profile, timeout=60)
    assert commands._finished(ledger, row) is False


def test_a_stacked_branch_with_no_commit_of_its_own_is_not_finished(tmp_path):
    from flotilla.ledger import commands, core, receipts
    from ledgerkit import actor, branch, git, make_ledger, repo_with_origin
    root = repo_with_origin(tmp_path)
    profile = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "direct"},
               "tests": {"tier": [{"name": "t", "command": "true", "required_for": ["handover"]}]}}
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    branch(root, "feat/a", "the part")
    first = core.claim(ledger, actor(ledger, "main session 1"), "feat/a")
    git(root, "branch", "feat/b", "feat/a")             # stacked on feat/a, nothing of its own yet
    second = core.claim(ledger, actor(ledger, "main session 1"), "feat/b")
    git(root, "checkout", "-q", "feat/a")
    receipts.run_receipt(root, state=tmp_path / "state", repo_key=ledger.repo_key, purpose="handover",
                         profile=profile, timeout=60)
    git(root, "checkout", "-q", "main")
    assert commands._finished(ledger, second) is False
    assert commands._finished(ledger, first) is True


def test_a_stacked_branch_stays_unfinished_when_the_part_under_it_moves_on(tmp_path):
    from flotilla.ledger import commands, core, receipts
    from ledgerkit import actor, branch, commit, git, make_ledger, repo_with_origin
    root = repo_with_origin(tmp_path)
    profile = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "direct"},
               "tests": {"tier": [{"name": "t", "command": "true", "required_for": ["handover"]}]}}
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    branch(root, "feat/a", "the part")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/a")
    git(root, "branch", "feat/b", "feat/a")
    second = core.claim(ledger, actor(ledger, "main session 2"), "feat/b")
    git(root, "checkout", "-q", "feat/b")
    receipts.run_receipt(root, state=tmp_path / "state", repo_key=ledger.repo_key, purpose="handover",
                         profile=profile, timeout=60)
    git(root, "checkout", "-q", "feat/a")
    commit(root, "the part's author keeps working", "more.txt")
    git(root, "checkout", "-q", "main")
    assert commands._finished(ledger, second) is False


def test_every_refusal_names_a_way_forward(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", "HEAD", "--root", str(root),
                        "--as", "main session 1")
    last = out.rstrip().splitlines()[-1]
    assert code == 2 and last.startswith("next: ")
    assert "work wait" in last and "tell the orchestrator" in last and "git plumbing" in last


def test_the_way_forward_is_one_fixed_line():
    from flotilla.ledger import commands
    assert commands.STUCK.startswith("next: ")


def test_accept_prints_the_letter_for_the_sender(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    tip = commit(tree, "work", "work.txt")
    run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", tip, "--root", str(root),
                        "--as", "review session 1")
    assert code == 0
    assert "letter for the session holding the sender post - send it with SendMessage" in out


def test_assign_prints_one_letter_for_the_reader(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    commit(tree, "work", "work.txt")
    run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    monkeypatch.setattr("flotilla.ledger.core.Ledger.live_names", lambda self: {"review session 1"})
    code, out = run_cli("work", "assign", "feat/x", "--reader", "review session 1", "--root", str(root),
                        "--as", "orchestrator 1")
    assert code == 0 and out.count("flotilla work take feat/x") == 1
    assert "letter for review session 1" in out


def test_a_project_error_is_not_dressed_as_a_move_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    code, out = run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")
    assert code == 2 and "next: if no move" not in out and "send this text to the orchestrator" in out


def test_a_recorded_move_survives_a_letter_that_cannot_be_computed(tmp_path, monkeypatch):
    from flotilla.ledger import letters
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(letters, "changed", boom)
    code, out = run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")
    assert code == 0 and "the move is recorded; its letters could not be computed: disk on fire" in out


def test_status_prints_a_run_recorded_with_colour_codes_plain(tmp_path, monkeypatch):
    from flotilla.ledger import runs
    from flotilla.ledger.actor import resolve_actor
    from flotilla.ledger.commands import open_ledger
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")
    ledger = open_ledger(root)
    runs.record_run(ledger, resolve_actor(ledger.posts, as_name="main session 1"), "feat/x", verdict="green",
                    summary="\x1b[32m12 passed\x1b[0m in 0.02s", revision="0" * 40, evidence={})
    code, out = run_cli("status", "--root", str(root))
    assert "12 passed in 0.02s" in out and "\x1b" not in out


def test_an_accepted_row_does_not_say_its_reader_is_reading(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    tip = commit(tree, "work", "work.txt")
    run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    code, out = run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")
    assert "(reading)" in out
    run_cli("work", "accept", "feat/x", "--reviewed", tip, "--root", str(root), "--as", "review session 1")
    code, out = run_cli("work", "show", "feat/x", "--root", str(root))
    assert "(reading)" not in out and "reader review session 1" in out


def test_show_says_a_row_has_no_branch_yet(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    code, out = run_cli("work", "claim", "feat/later", "--root", str(root), "--as", "main session 1")
    assert code == 0, out
    code, out = run_cli("work", "show", "feat/later", "--root", str(root))
    assert "base: no branch yet" in out and "base unknown" not in out


def test_a_letter_goes_to_this_projects_sender_only(tmp_path, monkeypatch):
    import dataclasses
    from watchkit import sess
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    tip = commit(tree, "work", "work.txt")
    run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")
    ours = dataclasses.replace(sess("sender 1"), cwd=str(root))
    theirs = dataclasses.replace(sess("sender 2"), cwd=str(tmp_path / "another-repo"))
    monkeypatch.setattr("flotilla.ledger.core.Ledger.live_sessions", lambda self: [ours, theirs])
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", tip, "--root", str(root),
                        "--as", "review session 1")
    assert code == 0 and "letter for sender 1 - send" in out and "sender 2" not in out


def test_claim_takes_after_on_the_command_line(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("work", "claim", "tool/y", "--after", "feat/x", "--root", str(root), "--as", "main session 1")
    assert code == 0, out
    from flotilla.ledger.commands import open_ledger
    rows = open_ledger(root).rows()
    assert next(row for row in rows.values() if row.branch == "tool/y").after == ["r1"]
