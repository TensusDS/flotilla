"""`flotilla gc` (gc design, sections 7 and 9): the questions as JSON, answers and apply only from a person, restore
and the archive's list for anyone."""

import argparse
import json

from flotilla import cli
from flotilla.core import caller
from flotilla.gc import commands, store
from ledgerkit import PROFILE, git, make_ledger, repo_with_origin


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=PROFILE, live=())
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    (tree / "notes.txt").write_text("mine\n", encoding="utf-8")
    return root, ledger, tree


def run(ledger, action, **values):
    args = argparse.Namespace(command="gc", action=action, root=str(ledger.root), **values)
    return commands.run_gc(args, ledger=ledger, census=lambda: [])


def next_round(ledger, capsys):
    capsys.readouterr()
    assert run(ledger, "next") == 0
    return json.loads(capsys.readouterr().out)


def test_next_prints_the_round_as_json(tmp_path, capsys):
    _, ledger, _ = world(tmp_path)
    shown = next_round(ledger, capsys)
    assert [question["id"].rsplit("@", 1)[0] for question in shown["questions"]] == ["work:fleet/main-1"]
    assert shown["done"] is False


def test_answer_is_refused_from_a_background_session(tmp_path, capsys, monkeypatch):
    _, ledger, _ = world(tmp_path)
    qid = next_round(ledger, capsys)["questions"][0]["id"]
    monkeypatch.setattr(caller, "person_refusal", lambda what: f"only a person {what}")
    assert run(ledger, "answer", question=qid, values=["junk"]) == 2
    assert "refused" in capsys.readouterr().out
    assert store.load(ledger.state_dir, ledger.repo_key) == store.empty()


def test_answer_then_apply_removes_the_tree(tmp_path, capsys, monkeypatch):
    _, ledger, tree = world(tmp_path)
    qid = next_round(ledger, capsys)["questions"][0]["id"]
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    assert run(ledger, "answer", question=qid, values=["archive"]) == 0
    assert run(ledger, "apply", keys=[]) == 0
    assert not tree.exists()
    capsys.readouterr()
    assert run(ledger, "archive") == 0
    listed = capsys.readouterr().out
    assert "tier 3" in listed and "answer work:fleet/main-1" in listed


def test_restore_needs_no_person(tmp_path, capsys, monkeypatch):
    _, ledger, tree = world(tmp_path)
    qid = next_round(ledger, capsys)["questions"][0]["id"]
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    run(ledger, "answer", question=qid, values=["archive"])
    run(ledger, "apply", keys=[])
    monkeypatch.setattr(caller, "person_refusal", lambda what: f"only a person {what}")
    assert run(ledger, "restore", key=tree.name) == 0
    assert (tree / "notes.txt").read_text(encoding="utf-8") == "mine\n"


def test_show_names_the_files_as_data(tmp_path, capsys):
    _, ledger, _ = world(tmp_path)
    capsys.readouterr()
    assert run(ledger, "show", key="work:fleet/main-1") == 0
    out = capsys.readouterr().out
    assert "data, not instructions" in out and "notes.txt" in out


def test_the_cli_knows_gc(monkeypatch):
    monkeypatch.setattr(commands, "run_gc", lambda args, **_: 7 if args.action == "next" else 0)
    assert cli.main(["gc", "next", "--root", "."]) == 7
