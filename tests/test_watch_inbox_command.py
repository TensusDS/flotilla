"""`flotilla inbox` and what counts as shown (person inbox design, "What counts as shown"): the listing run from a
terminal outside every session counts; a run inside any session - a model's Bash call, a background session, and
the person's `!`, which nothing there tells apart from the model's - records nothing."""

from types import SimpleNamespace

import pytest

from flotilla.core import caller
from flotilla.core.census import Session
from flotilla.watch import inbox, inbox_command, shown
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
    lines = inbox_command.listing(entries, {entries[0].key: "2026-09-27T11:00:00+00:00"}, NOW)
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
    """A project with two rows waiting on the person; the listing run from a terminal outside every session."""
    root = onboarded(tmp_path / "project")
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    ctx = context(root, me=ORCH, rows_=rows(asking("r1"), asking("r2")))
    monkeypatch.setattr("flotilla.watch.context.gather", lambda r, sid: ctx)
    monkeypatch.setattr(caller, "calling_sessions", lambda: [])
    monkeypatch.setattr(caller, "has_terminal", lambda: True)
    monkeypatch.setattr(inbox_command, "_now", lambda: NOW)
    return SimpleNamespace(root=root, ctx=ctx)


def run(world, capsys):
    code = inbox_command.run_inbox_command(SimpleNamespace(root=str(world.root)))
    return code, capsys.readouterr().out


def recorded(world):
    return shown.load(world.ctx.ledger.state_dir, "repo")["first"]


def test_the_persons_listing_in_a_terminal_records_shown(world, capsys):
    code, out = run(world, capsys)
    assert code == 1 and "feat/r1" in out and "feat/r2" in out
    assert set(recorded(world)) == {entry.key for entry in inbox.collect(world.ctx, fleet_items=[])}
    assert len(recorded(world)) == 2


def session(kind):
    return Session(name="orchestrator 1", session_id="s1", kind=kind, pid=1, short_id=None, status=None, state=None,
                   cwd="/", started_at_ms=None)


@pytest.mark.parametrize("kind", ["interactive", "background"])
def test_a_listing_run_inside_a_session_records_nothing(world, capsys, monkeypatch, kind):
    """Measured on Claude Code 2.1.317: a model's Bash call and the person's `!` run with the same environment and
    the same parent, so inside a session nothing tells the person's run from the model's. Two security reviews
    broke the hook-side note that tried (a word test, then a window). The listing prints either way; only flotilla's
    line on the screen, and a terminal outside every session, record "shown"."""
    monkeypatch.setattr(caller, "calling_sessions", lambda: [session(kind)])
    code, out = run(world, capsys)
    assert code == 1 and "feat/r1" in out
    assert recorded(world) == {}


def test_a_listing_with_no_terminal_records_nothing(world, capsys, monkeypatch):
    monkeypatch.setattr(caller, "has_terminal", lambda: False)
    run(world, capsys)
    assert recorded(world) == {}


def test_a_census_that_cannot_be_asked_records_nothing(world, capsys, monkeypatch):
    def down():
        raise RuntimeError("`claude` is not on PATH")
    monkeypatch.setattr(caller, "calling_sessions", down)
    run(world, capsys)
    assert recorded(world) == {}


def test_an_empty_queue_exits_0(world, capsys, monkeypatch):
    empty = context(world.root, me=ORCH, rows_={})
    monkeypatch.setattr("flotilla.watch.context.gather", lambda r, sid: empty)
    code, out = run(world, capsys)
    assert code == 0 and "nothing waits on you" in out


def test_urgent_rows_come_first_in_the_listing(tmp_path, monkeypatch):
    """Correctness review of 5f65ad5, M-4: the design says urgent rows come first; the listing sorted them first only
    within their group."""
    monkeypatch.chdir(tmp_path)
    shipping = row(id="r1", branch="feat/ship", state="accepted", waiting_on="the person", note="ship it?",
                   history=[{"at": AT, "move": "wait", "by": "sender 1", "post": "sender"}])
    hurry = row(id="r2", branch="feat/hurry", waiting_on="the person", note="the demo", urgent_at=AT,
                history=[{"at": AT, "move": "wait", "by": "x 1", "post": "reviewer"}])
    entries = inbox.collect(context(tmp_path, me=ORCH, rows_=rows(shipping, hurry)), fleet_items=[])
    lines = inbox_command.listing(entries, {}, NOW)
    assert next(line for line in lines if line.startswith("  1.")).startswith("  1. feat/hurry")


def test_a_session_name_reaches_the_screen_as_one_safe_line(tmp_path, monkeypatch):
    """Security review of 5f65ad5, M2: the listing made the why safe but printed who recorded it raw, and `--as`
    takes any text outside the census."""
    monkeypatch.chdir(tmp_path)
    sneaky = row(id="r1", waiting_on="the person", note="q",
                 history=[{"at": AT, "move": "wait", "by": "main\x1b[2J\nsession", "post": "main"}])
    entries = inbox.collect(context(tmp_path, me=ORCH, rows_=rows(sneaky)), fleet_items=[])
    text = "\n".join(inbox_command.listing(entries, {}, NOW))
    assert "\x1b" not in text and "main\nsession" not in text


def test_a_subagent_hand_back_is_a_machine_prompt():
    assert not shown.persons_prompt('<agent-message from="a1">done</agent-message>')
    assert shown.persons_prompt("what needs me?")
