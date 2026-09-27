from flotilla.watch import fleet
from watchkit import PR, row, rows, sess

POSTS = {"sender 1": "sender", "main session 1": "main", "review session 1": "reviewer",
         "orchestrator 1": "orchestrator"}


def post_of(name):
    return POSTS.get(name, "")


def test_an_idle_holder_is_a_dropped_ball():
    found = fleet.fleet(rows(row(state="handed", reader="review session 1")), PR,
                        [sess("review session 1"), sess("main session 1", state="working")], post_of=post_of)
    assert [(item.kind, item.branch) for item in found] == [("dropped", "feat/x")]
    assert "waiting on a permission prompt" in found[0].text


def test_a_working_holder_is_not_dropped():
    found = fleet.fleet(rows(row(state="handed", reader="review session 1")), PR,
                        [sess("review session 1", state="working"), sess("main session 1")], post_of=post_of)
    assert found == []


def test_a_recorded_wait_is_not_dropped():
    waiting = row(state="handed", reader="review session 1", waiting_on="the person", note="asked")
    found = fleet.fleet(rows(waiting), PR, [sess("review session 1"), sess("main session 1")], post_of=post_of)
    assert found == []


def test_an_interactive_session_is_read_by_its_status():
    assert fleet.not_working(sess("x", kind="interactive", status="idle"))
    assert not fleet.not_working(sess("x", kind="interactive", status="busy"))


def test_a_post_named_move_with_nobody_in_the_post_is_named():
    found = fleet.fleet(rows(row(state="accepted")), PR, [sess("main session 1")], post_of=post_of)
    assert [(item.kind, item.branch) for item in found] == [("nobody", "feat/x")]
    assert "`sender`" in found[0].text


def test_an_idle_sender_holding_a_queued_row_is_dropped():
    found = fleet.fleet(rows(row(state="queued")), PR, [sess("sender 1"), sess("main session 1")], post_of=post_of)
    assert [item.kind for item in found] == ["dropped"]


def test_a_gone_mover_is_a_deviation_not_a_dropped_ball():
    found = fleet.fleet(rows(row(state="fixing")), PR, [sess("sender 1")], post_of=post_of)
    assert [item.kind for item in found] == ["deviation"] and "mover_gone" in found[0].text


def test_a_break_stays_open_until_the_row_moves(tmp_path):
    fleet.record_break(tmp_path, "repo", "main session 1", ["feat/x"], at="2026-09-27T11:00:00+00:00")
    standing = row(state="fixing", updated_at="2026-09-27T10:00:00+00:00")
    assert [item.kind for item in fleet.open_breaks(tmp_path, "repo", rows(standing), PR, post_of)] == ["break"]
    moved = row(state="handed", reader="review session 1", updated_at="2026-09-27T11:30:00+00:00")
    assert fleet.open_breaks(tmp_path, "repo", rows(moved), PR, post_of) == []


def test_a_break_closes_when_a_wait_is_recorded(tmp_path):
    fleet.record_break(tmp_path, "repo", "main session 1", ["feat/x"], at="2026-09-27T11:00:00+00:00")
    waiting = row(state="fixing", waiting_on="the person", note="asked")
    assert fleet.open_breaks(tmp_path, "repo", rows(waiting), PR, post_of) == []


def test_nothing_recorded_is_no_break(tmp_path):
    assert fleet.open_breaks(tmp_path, "repo", {}, PR, post_of) == []


def test_an_idle_post_holder_is_not_a_dropped_ball():
    post_row = rows(row(branch="post/sender", owner="sender 1", state="reserved"))
    assert fleet.fleet(post_row, PR, [sess("sender 1")], post_of=post_of) == []
