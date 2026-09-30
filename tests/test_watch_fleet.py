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
    waiting = row(state="handed", reader="review session 1", waiting_on="main session 1", note="asked")
    found = fleet.fleet(rows(waiting), PR, [sess("review session 1"), sess("main session 1")], post_of=post_of)
    assert found == []


def test_a_wait_on_the_person_is_the_orchestrators_to_carry_not_a_dropped_ball():
    waiting = row(state="handed", reader="review session 1", waiting_on="the person", note="asked")
    found = fleet.fleet(rows(waiting), PR, [sess("review session 1"), sess("main session 1")], post_of=post_of)
    assert [(item.kind, item.text) for item in found] == [(fleet.PERSON, "waits on the person: asked")]


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


def test_a_session_asking_the_person_is_not_a_dropped_ball():
    found = fleet.fleet(rows(row(state="handed", reader="review session 1")), PR,
                        [sess("review session 1", status="waiting"), sess("main session 1", state="working")],
                        post_of=post_of, asking={"review session 1"})
    assert found == []


def test_a_prompt_nobody_answers_is_named_as_such():
    found = fleet.fleet(rows(row(state="handed", reader="review session 1")), PR,
                        [sess("review session 1", status="waiting"), sess("main session 1", state="working")],
                        post_of=post_of)
    assert "waiting on a permission prompt nobody answers" in found[0].text


def test_an_idle_status_reads_as_idle():
    found = fleet.fleet(rows(row(state="handed", reader="review session 1")), PR,
                        [sess("review session 1", status="idle"), sess("main session 1", state="working")],
                        post_of=post_of)
    assert "census: idle" in found[0].text


def test_questions_become_items_named_by_the_asking_session():
    from flotilla.broker import queue
    asked = queue.Question("1", 1790500000.0, 1790500540.0, 1, "main session 1", "s", "Bash",
                           {"command": "touch x"}, [])
    item = fleet.question_items([asked])[0]
    assert (item.kind, item.branch) == ("question", "main session 1") and "Bash touch x" in item.text
    assert "/flotilla:permit" in item.text


def test_empty_seats_are_one_quiet_line():
    seats = rows(row(id="r1", branch="fleet/reviewer-1", owner="review session 1", state="reserved"),
                 row(id="r2", branch="fleet/judge-1", owner="acceptance judge 1", state="reserved"))
    found = fleet.fleet(seats, PR, [sess("main session 1")], post_of=post_of)
    assert [(item.kind, item.branch) for item in found] == [(fleet.SEATS, "")]
    assert "2 post seat(s) with no live session: acceptance judge 1, review session 1" in found[0].text
    assert "flotilla fleet down" in found[0].text


def test_a_wait_on_the_person_says_what_moved_since():
    waiting = row(state="handed", reader="review session 1", waiting_on="the person",
                  note="r2 is blocked on inbatch permission")
    shipped = row(id="r2", branch="feat/y", state="shipped", updated_at="2026-09-27T11:00:00+00:00")
    found = fleet.fleet(rows(waiting, shipped), PR, [sess("review session 1"), sess("main session 1")],
                        post_of=post_of)
    person = [item for item in found if item.kind == fleet.PERSON]
    assert [item.text for item in person] == ["waits on the person: r2 is blocked on inbatch permission "
                                              "(since then: r2 shipped)"]


def test_a_fleet_session_waiting_on_the_person_is_raised():
    orchestrator = sess("orchestrator 1", state="blocked", status="waiting")
    found = fleet.fleet({}, PR, [orchestrator], post_of=post_of)
    assert [(item.kind, item.branch, item.text) for item in found] == [
        (fleet.PERSON, "", "orchestrator 1 waits on the person (census: waiting); answer it in its session")]
    assert found[0].who == "orchestrator 1"
    assert fleet.fleet({}, PR, [orchestrator], post_of=post_of, asking={"orchestrator 1"}) == []
    assert fleet.fleet({}, PR, [sess("someone else", status="waiting")], post_of=post_of) == []
    assert fleet.fleet({}, PR, [sess("orchestrator 1", status="idle")], post_of=post_of) == []


def test_the_empty_seat_item_carries_no_age():
    seats = rows(row(id="r1", branch="fleet/reviewer-1", owner="review session 1", state="reserved"))
    found = fleet.fleet(seats, PR, [sess("main session 1")], post_of=post_of)
    assert [(item.kind, item.since) for item in found] == [(fleet.SEATS, "")]
