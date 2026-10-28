"""`flotilla gc` (gc design, sections 7 and 9): the questions as JSON, answers and apply only from a person, restore
and the archive's list for anyone."""

import argparse
import json
from pathlib import Path

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


def test_restore_is_the_persons(tmp_path, capsys, monkeypatch):
    """Security review of de4ae37: an archive record is a file any session can write, so restoring from it is the
    person's move, like answering and applying."""
    _, ledger, tree = world(tmp_path)
    qid = next_round(ledger, capsys)["questions"][0]["id"]
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    run(ledger, "answer", question=qid, values=["archive"])
    run(ledger, "apply", keys=[])
    monkeypatch.setattr(caller, "person_refusal", lambda what: f"only a person {what}")
    assert run(ledger, "restore", key=tree.name) == 2
    assert not tree.exists()
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
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


# Second review of the branch, m-5: an entry is one per piece of work per day, and a pass's "told after" and the
# person's answer can both land in it; each tree keeps its own approval, which the listing shows.

def test_each_tree_in_an_entry_keeps_its_own_approval(tmp_path, capsys):
    from flotilla.gc import archive
    root, ledger, tree = world(tmp_path)
    other = tmp_path / "app-main-2"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-2", str(other))
    (other / "x.bin").write_bytes(b"small")
    archive.put(ledger, entry="2026-10-28-work", tree=other, files=["x.bin"], tier="2",
                approved_by="told after: small", branch="fleet/main-2", head="")
    archive.put(ledger, entry="2026-10-28-work", tree=tree, files=["notes.txt"], tier="3",
                approved_by="answer work:x 2026-10-28T10:00:00+00:00", branch="fleet/main-1", head="")
    records = {Path(item["tree"]).name: (item["tier"], item["approved_by"])
               for item in archive.entries(ledger)[0].trees}
    assert records == {"app-main-2": ("2", "told after: small"),
                       "app-main-1": ("3", "answer work:x 2026-10-28T10:00:00+00:00")}
    capsys.readouterr()
    assert run(ledger, "archive") == 0
    shown = capsys.readouterr().out
    assert "app-main-1  tier 3  approved by answer work:x 2026-10-28T10:00:00+00:00" in shown, shown
    assert "app-main-2  tier 2  approved by told after: small" in shown, shown


# Second review of the branch, m-8: `next` said done only when no own-words answer waited, and the skill applies only
# after done, so one own-words answer held back every archive and junk answer while they went stale.

def test_an_own_words_answer_does_not_hold_back_the_rest(tmp_path, capsys, monkeypatch):
    root, ledger, tree = world(tmp_path)
    other = tmp_path / "app-main-2"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-2", str(other))
    (other / "notes.txt").write_text("theirs\n", encoding="utf-8")
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    ids = {question["id"].rsplit("@", 1)[0]: question["id"] for question in next_round(ledger, capsys)["questions"]}
    assert run(ledger, "answer", question=ids["work:fleet/main-1"], values=["commit these to the branch first"]) == 0
    assert run(ledger, "answer", question=ids["work:fleet/main-2"], values=["junk"]) == 0
    shown = next_round(ledger, capsys)
    assert shown["done"] is True and shown["words"] == 1, shown
    assert any("commit these to the branch first" in note for note in shown["notes"]), shown
    run(ledger, "apply", keys=[])
    assert not other.exists() and tree.exists()


# Second review of the branch, m-9: a junk answer carried out stayed recorded, and the next apply said it "no longer
# waits on you" and exited 2.

def test_a_carried_out_junk_answer_is_not_reported_again(tmp_path, capsys, monkeypatch):
    _, ledger, tree = world(tmp_path)
    qid = next_round(ledger, capsys)["questions"][0]["id"]
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    assert run(ledger, "answer", question=qid, values=["junk"]) == 0
    assert run(ledger, "apply", keys=[]) == 0 and not tree.exists()
    capsys.readouterr()
    assert run(ledger, "apply", keys=[]) == 0
    assert "no longer waits" not in capsys.readouterr().out
