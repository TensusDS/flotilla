"""`flotilla inbox` and what counts as shown (person inbox design, "What counts as shown"): the person's own run of
the listing counts; a model's Bash call of it, or a background session's, records nothing."""

import datetime as dt
import io
import json
from types import SimpleNamespace

import pytest

from flotilla import hooks
from flotilla.core import caller
from flotilla.core.census import Session
from flotilla.watch import inbox, inbox_command, shown, toolcall
from watchkit import AT, NOW, context, onboarded, row, rows, sess

ORCH = sess("orchestrator 1", state="working")


def asking(id):
    history = [{"at": AT, "move": "wait", "by": "main session 1", "post": "main"}]
    return row(id=id, branch=f"feat/{id}", waiting_on="the person", note=f"question {id}", history=history)


def test_mark_keeps_the_first_time(tmp_path):
    shown.mark(tmp_path, "repo", ["row:r1"], "2026-09-27T10:00:00+00:00")
    shown.mark(tmp_path, "repo", ["row:r1", "row:r2"], "2026-09-27T11:00:00+00:00")
    assert shown.load(tmp_path, "repo")["first"] == {"row:r1": "2026-09-27T10:00:00+00:00",
                                                     "row:r2": "2026-09-27T11:00:00+00:00"}


def test_a_damaged_shown_file_reads_as_never_shown(tmp_path):
    shown.mark(tmp_path, "repo", ["row:r1"], AT)
    path = next((tmp_path / "watch" / "shown").iterdir())
    path.write_text("{torn", encoding="utf-8")
    assert shown.load(tmp_path, "repo")["first"] == {}


def test_listing_numbers_rows_and_says_never(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    entries = inbox.collect(context(tmp_path, me=ORCH, rows_=rows(asking("r1"), asking("r2"))), fleet_items=[])
    lines = inbox_command.listing(entries, {"row:r1": "2026-09-27T11:00:00+00:00"}, NOW)
    text = "\n".join(lines)
    assert "  1. feat/r1" in text and "  2. feat/r2" in text
    assert "first shown 1 h ago" in text and text.count("never shown") == 1
    assert "a question (2)" in text


def test_a_put_off_row_is_listed_under_put_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    held = row(id="r1", state="accepted", held_until="the person", held_by="orchestrator 1", held_why="after Friday")
    entries = inbox.collect(context(tmp_path, me=ORCH, rows_=rows(held)), fleet_items=[])
    assert "put off (1)" in "\n".join(inbox_command.listing(entries, {}, NOW))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A project with two rows waiting on the person, the command run from session `s1`."""
    root = onboarded(tmp_path / "project")
    state = tmp_path / "state"
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(state))
    ctx = context(root, me=ORCH, rows_=rows(asking("r1"), asking("r2")))
    monkeypatch.setattr("flotilla.watch.context.gather", lambda r, sid: ctx)
    me = Session(name="orchestrator 1", session_id="s1", kind="interactive", pid=1, short_id=None, status=None,
                 state=None, cwd="/", started_at_ms=None)
    monkeypatch.setattr(caller, "calling_sessions", lambda: [me])
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    monkeypatch.setattr(inbox_command, "_now", lambda: NOW)
    return SimpleNamespace(root=root, state=state, ctx=ctx)


def run(world, capsys):
    code = inbox_command.run_inbox_command(SimpleNamespace(root=str(world.root)))
    return code, capsys.readouterr().out


def test_the_persons_listing_records_shown(world, capsys):
    code, out = run(world, capsys)
    assert code == 1 and "feat/r1" in out and "feat/r2" in out
    assert set(shown.load(world.ctx.ledger.state_dir, "repo")["first"]) == {"row:r1", "row:r2"}


def test_a_tool_call_of_inbox_records_nothing(world, capsys, monkeypatch):
    payload = {"cwd": str(world.root), "tool_name": "Bash", "session_id": "s1",
               "tool_input": {"command": "flotilla inbox", "description": "d"}, "permission_mode": "auto"}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    code, printed = run(world, capsys)
    assert code == 1 and "feat/r1" in printed   # the model reads the queue; it is not shown to the person
    assert shown.load(world.ctx.ledger.state_dir, "repo")["first"] == {}


def test_a_background_session_records_nothing(world, capsys, monkeypatch):
    monkeypatch.setattr(caller, "person_refusal", lambda what: "`orchestrator 1` is a background session")
    run(world, capsys)
    assert shown.load(world.ctx.ledger.state_dir, "repo")["first"] == {}


def test_an_empty_queue_exits_0(world, capsys, monkeypatch):
    empty = context(world.root, me=ORCH, rows_={})
    monkeypatch.setattr("flotilla.watch.context.gather", lambda r, sid: empty)
    code, out = run(world, capsys)
    assert code == 0 and "nothing waits on you" in out


def test_a_note_is_used_once_and_goes_stale(tmp_path):
    toolcall.note(tmp_path, "s1", 1000.0)
    assert toolcall.by_tool(tmp_path, ["s1"], 1010.0)
    assert not toolcall.by_tool(tmp_path, ["s1"], 1011.0)   # used up: the next run is the person's own
    toolcall.note(tmp_path, "s1", 1000.0)
    assert not toolcall.by_tool(tmp_path, ["s1"], 1000.0 + 61)


def test_the_guard_notes_a_call_naming_inbox_and_decides_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    payload = {"cwd": str(tmp_path), "tool_name": "Bash", "session_id": "s9",
               "tool_input": {"command": "cd x && ./bin/flotilla inbox", "description": "d"}}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    assert out.getvalue() == ""
    assert toolcall.by_tool(tmp_path / "state", ["s9"], dt.datetime.now().timestamp())
