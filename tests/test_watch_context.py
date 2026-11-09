from types import SimpleNamespace

from flotilla.core.census import CensusUnavailable
from flotilla.core.config import ConfigError
from flotilla.watch import context
from watchkit import PR, context as make_context, kinds, posts, row, rows, sess


def no_parent(pid):
    return None


def test_the_session_is_found_by_the_hooks_session_id():
    listed = [sess("main session 1", sid="aaa"), sess("review session 1", sid="bbb")]
    assert context.this_session(listed, "bbb", parent_of=no_parent).name == "review session 1"


def test_without_an_id_the_process_parents_are_walked():
    listed = [sess("main session 1", sid="aaa", pid=50)]
    chain = {70: 60, 60: 50}
    assert context.this_session(listed, "", parent_of=chain.get, start_pid=70).name == "main session 1"


def test_a_census_that_could_not_be_asked_is_kept_as_a_reason(tmp_path):
    def down():
        raise CensusUnavailable("`claude` is not on PATH")
    ctx = context.gather(tmp_path, "aaa", census=down, open_ledger=lambda root: SimpleNamespace(rows=lambda: {}))
    assert ctx.sessions is None and ctx.live is None and ctx.me is None and "not on PATH" in ctx.census_error


def test_a_ledger_that_could_not_be_read_is_kept_as_a_reason(tmp_path):
    def broken(root):
        raise ConfigError("trunk carries no .flotilla/project.toml")
    ctx = context.gather(tmp_path, "aaa", census=lambda: [sess("main session 1", sid="aaa")], open_ledger=broken,
                         parent_of=no_parent)
    assert ctx.ledger is None and "trunk carries no" in ctx.ledger_error
    assert ctx.me.name == "main session 1" and ctx.mine() == [] and ctx.fleet() == []


def test_the_sessions_own_moves_use_its_post(tmp_path):
    me = sess("review session 1")
    ctx = make_context(tmp_path, me=me, rows_=rows(row(state="handed", reader="review session 1")))
    assert kinds(ctx.mine()) == [("ball", "feat/x")] and "your moves: accept" in ctx.mine()[0].text
    assert ctx.post_of("review session 1") == "reviewer" and not ctx.is_orchestrator


def test_only_a_session_in_the_orchestrator_post_is_the_orchestrator(tmp_path):
    ctx = make_context(tmp_path, me=sess("orchestrator 1", state="working"))
    assert ctx.is_orchestrator


def test_the_orchestrator_sees_live_questions_first(tmp_path):
    from flotilla.broker import queue
    from watchkit import NOW
    me = sess("orchestrator 1", state="working")
    ctx = make_context(tmp_path, me=me, sessions=[me, sess("main session 1", status="waiting")])
    queue.ask(ctx.ledger.state_dir, "repo", session="main session 1", session_id="s", tool="Bash",
              tool_input={"command": "touch x"}, suggestions=[], wait=540, now=NOW.timestamp())
    from flotilla.watch import render
    lines = render.lines(ctx.fleet(now=NOW.timestamp()), NOW)
    assert lines[0].startswith("  main session 1: asks Bash \"touch x")


def test_gather_narrows_the_fleet_to_this_projects_sessions(tmp_path):
    import dataclasses
    from ledgerkit import repo_with_origin
    root = repo_with_origin(tmp_path)
    here = dataclasses.replace(sess("main session 1", sid="aaa"), cwd=str(root))
    there = dataclasses.replace(sess("acceptance judge 1", sid="bbb"), cwd=str(tmp_path / "elsewhere"))
    ledger = SimpleNamespace(rows=lambda: {}, root=root)
    ctx = context.gather(root, "aaa", census=lambda: [here, there], open_ledger=lambda r: ledger,
                         parent_of=no_parent)
    assert [s.name for s in ctx.project] == ["main session 1"] and ctx.project_live == {"main session 1"}


def test_a_wait_is_asked_of_the_whole_census_not_the_projects_members(tmp_path):
    me = sess("orchestrator 1", state="working")
    elsewhere = sess("main session 2", state="working")   # alive, in another repository
    waiting = row("r1", state="handed", reader="review session 1", waiting_on="main session 2", note="asked")
    theirs = row("r2", branch="feat/y", owner="main session 2", state="closed")
    ctx = make_context(tmp_path, me=me, sessions=[me, sess("review session 1"), elsewhere],
                       rows_=rows(waiting, theirs))
    ctx.project = [me, ctx.sessions[1]]   # what project.members keeps: main session 2 holds nothing open here
    assert not any("wait_gone" in item.text or item.kind == "dropped" for item in ctx.fleet())


def test_a_hook_gathers_with_a_hooks_three_seconds(tmp_path, monkeypatch):   # review of 406a533: commands get 20 s
    from flotilla.core import census
    asked = []
    monkeypatch.setattr(census, "read_census", lambda timeout=None, **kw: asked.append(timeout) or [])
    def closed(root):
        raise ConfigError("not needed here")
    context.gather(tmp_path, "aaa", open_ledger=closed, parent_of=no_parent)
    assert asked == [context.HOOK_CHECK_TIMEOUT] == [3]


def test_a_command_asks_the_seats_the_census_misses_whether_they_are_held_at_start(tmp_path, monkeypatch):
    """Field test 2026-11-07, S3: a seat held on a start-up question is listed with no process and dropped as gone.
    `watch` and `inbox` ask the seats they miss; a hook does not pay for it."""
    import dataclasses
    from flotilla.core import census
    held = dataclasses.replace(sess("main session 2"), short_id="0204d254", held_on="New MCP server found")
    asked = []
    monkeypatch.setattr(census, "held_at_start", lambda names, **kw: asked.append(set(names)) or [held])
    seat = row("r1", branch="fleet/main-2", owner="main session 2", state="reserved")
    gone = row("r2", branch="fleet/main-3", owner="main session 3", state="released")
    ledger = SimpleNamespace(rows=lambda: rows(seat, gone), root=tmp_path)
    me = sess("orchestrator 1", sid="aaa")
    ctx = context.gather(tmp_path, "aaa", census=lambda: [me], open_ledger=lambda r: ledger, parent_of=no_parent,
                         held_seats=True)
    assert [s.name for s in ctx.sessions] == ["orchestrator 1", "main session 2"] and asked == [{"main session 2"}]
    context.gather(tmp_path, "aaa", census=lambda: [me], open_ledger=lambda r: ledger, parent_of=no_parent)
    assert asked == [{"main session 2"}]   # a hook's gather does not ask
