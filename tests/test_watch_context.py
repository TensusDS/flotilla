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
