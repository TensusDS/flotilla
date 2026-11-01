"""The daily pass and its occasions, gc design 4 and 8: ship, release and helper done mark the collector's pass due;
the orchestrator's `flotilla fleet clean --daily` runs it when due - dead seats, the sweep, a low disk's shrinking, the
outputs letter, the archive's question - and the watch names it."""

import os
import time
from collections import namedtuple
from pathlib import Path

import pytest

from flotilla.fleet import cleanup
from flotilla.gc import due, sweep
from ledgerkit import PROFILE, actor, git, make_ledger, merge, repo_with_origin
from test_gc_trees import session
from test_ledger_cli import DIRECT_PLAIN, commit, onboarded, run_cli

HOUR = 3600
Usage = namedtuple("Usage", "total used free")


def plenty(path):
    return Usage(2**40, 0, 2**39)


def low(path):
    return Usage(2**40, 0, 2**33)   # 8 GB of 1 TB: under 20 GB, over the 2 GB floor


def test_a_fresh_project_is_due(tmp_path):
    assert due.due(tmp_path, "repo", now=time.time()) == ["a day since the last pass"]


def test_a_pass_clears_the_marks_and_starts_the_day(tmp_path):
    now = time.time()
    due.mark(tmp_path, "repo", "shipped feat/x")
    due.mark(tmp_path, "repo", "shipped feat/x")
    assert due.due(tmp_path, "repo", now=now)[0] == "shipped feat/x"
    due.passed(tmp_path, "repo", now=now)
    assert due.due(tmp_path, "repo", now=now + HOUR) == []
    assert due.due(tmp_path, "repo", now=now + 25 * HOUR) == ["a day since the last pass"]


def shipped_project(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, DIRECT_PLAIN)
    tree = tmp_path / "app-main-1"
    assert run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")[0] == 0
    tip = commit(tree, "work", "work.txt")
    assert run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")[0] == 0
    assert run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")[0] == 0
    assert run_cli("work", "accept", "feat/x", "--reviewed", tip[:8], "--root", str(root),
                   "--as", "review session 1")[0] == 0
    assert run_cli("work", "queue", "feat/x", "--root", str(root), "--as", "sender 1")[0] == 0
    merge(root, "feat/x")
    assert run_cli("work", "land", "feat/x", "--root", str(root), "--as", "sender 1")[0] == 0
    git(root, "push", "-q", "origin", "main")
    return root


def ledger_of(root):
    from flotilla.ledger.commands import open_ledger
    return open_ledger(root)


def test_ship_marks_the_pass_due(tmp_path, monkeypatch):
    root = shipped_project(tmp_path, monkeypatch)
    code, out = run_cli("work", "ship", "feat/x", "--root", str(root), "--as", "sender 1")
    assert code == 0 and "note: the collector's next pass is due" in out, out
    ledger = ledger_of(root)
    assert "shipped feat/x" in due.due(ledger.state_dir, ledger.repo_key, now=time.time())


def test_reconcile_marks_the_pass_due(tmp_path, monkeypatch):
    root = shipped_project(tmp_path, monkeypatch)
    code, out = run_cli("work", "reconcile", "--root", str(root), "--as", "sender 1")
    assert code == 0 and "shipped feat/x" in out and "note: the collector's next pass is due" in out, out


def test_release_marks_the_pass_due(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, DIRECT_PLAIN)
    tree = tmp_path / "app-main-1"
    assert run_cli("tree", "cut", "feat/y", "--tree", str(tree), "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("work", "release", "feat/y", "--why", "not needed", "--root", str(root),
                        "--as", "main session 1")
    assert code == 0 and "note: the collector's next pass is due" in out, out
    ledger = ledger_of(root)
    assert "released feat/y" in due.due(ledger.state_dir, ledger.repo_key, now=time.time())


def test_a_mark_that_cannot_be_written_does_not_fail_the_ship(tmp_path, monkeypatch):
    root = shipped_project(tmp_path, monkeypatch)

    def broken(*args, **kwargs):
        raise OSError(30, "Read-only file system")
    monkeypatch.setattr(due, "mark", broken)
    code, out = run_cli("work", "ship", "feat/x", "--root", str(root), "--as", "sender 1")
    assert code == 0 and "note: the collector's next pass could not be marked due" in out, out


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=PROFILE, live=())
    proc = tmp_path / "proc"
    proc.mkdir()
    return root, ledger, proc


def daily(ledger, proc, *, usage=plenty, sessions=()):
    return cleanup.sweep(ledger, sessions=list(sessions), act=True, daily=True, census=lambda: list(sessions),
                         caller="test", usage=usage, proc_root=proc)


def test_the_daily_pass_does_nothing_when_not_due(tmp_path, monkeypatch):
    root, ledger, proc = world(tmp_path)
    due.passed(ledger.state_dir, ledger.repo_key, now=time.time())

    def refused(*args, **kwargs):
        raise AssertionError("the pass ran")
    monkeypatch.setattr(sweep, "run_pass", refused)
    lines = daily(ledger, proc)
    assert len(lines) == 1 and "nothing to do" in lines[0]


def test_the_daily_pass_retires_dead_seats_and_removes_their_trees(tmp_path):
    root, ledger, proc = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    with ledger.session() as s:
        s.append(actor(ledger, "main session 1"), "r1", "reserve", "reserved",
                 fields={"branch": "fleet/main-1", "owner": "main session 1", "tree": str(tree)})
    due.mark(ledger.state_dir, ledger.repo_key, "shipped feat/x")
    first = daily(ledger, proc)
    assert tree.exists() and any("seen gone once" in line for line in first), first   # one look is no death
    from flotilla.gc import store
    data = store.load(ledger.state_dir, ledger.repo_key)
    data["dead_since"] = {key: when - 3600 for key, when in data["dead_since"].items()}   # an hour later
    store.save(ledger.state_dir, ledger.repo_key, data)
    due.mark(ledger.state_dir, ledger.repo_key, "shipped feat/y")
    lines = daily(ledger, proc)
    assert not tree.exists() and "dead seats: 1 retired" in lines, lines
    assert due.due(ledger.state_dir, ledger.repo_key, now=time.time()) == []


def test_the_daily_pass_shrinks_only_when_low(tmp_path):
    root, ledger, proc = world(tmp_path)
    commit(root, "ignore", ".gitignore", "node_modules\n")
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    (tree / "node_modules").mkdir()
    (tree / "node_modules" / "x.js").write_text("x" * 4096, encoding="utf-8")
    (tree / "notes.txt").write_text("mine\n", encoding="utf-8")   # untracked work: the sweep keeps the tree
    gitdir = Path(git(tree, "rev-parse", "--absolute-git-dir"))
    then = time.time() - 30 * HOUR
    for name in ("index", "HEAD", "logs/HEAD"):
        if (gitdir / name).exists():
            os.utime(gitdir / name, (then, then))
    daily(ledger, proc, usage=plenty)
    assert (tree / "node_modules" / "x.js").exists()
    due.mark(ledger.state_dir, ledger.repo_key, "again")
    lines = daily(ledger, proc, usage=low)
    assert not (tree / "node_modules").exists() and (tree / "notes.txt").exists(), lines
    assert any(line.startswith("shrank 1 idle tree") for line in lines)


def test_watch_names_a_due_pass_and_a_low_disk(tmp_path):
    from flotilla.watch import fleet
    state = tmp_path / "state"
    found = fleet.collector_items(state, "repo", tmp_path, {}, now=time.time(), usage=low)
    assert sorted(item.kind for item in found) == [fleet.COLLECTOR, fleet.DISK]
    assert "fleet clean --daily" in next(item.text for item in found if item.kind == fleet.COLLECTOR)
    due.passed(state, "repo", now=time.time())
    assert fleet.collector_items(state, "repo", tmp_path, {}, now=time.time(), usage=plenty) == []


def test_the_orchestrators_watch_carries_the_collector_items(tmp_path):
    from watchkit import context, sess
    orchestrator = sess("orchestrator 1", state="working")
    ctx = context(tmp_path, me=orchestrator, sessions=[orchestrator], collector_due=True, disk_usage=low)
    kinds = [item.kind for item in ctx.fleet()]
    assert "collector" in kinds and "disk" in kinds
    quiet = context(tmp_path / "other", me=orchestrator, sessions=[orchestrator])
    assert not {"collector", "disk"} & {item.kind for item in quiet.fleet()}
