from flotilla.watch import whose
from watchkit import PR, kinds, row, rows

READER_MAY = frozenset({"reserve", "take", "accept", "fix", "recuse", "wait"})


def test_a_reader_holds_the_move_on_a_handed_row():
    found = whose.mine(rows(row(state="handed", reader="review session 1", tip="abc1234def")), PR,
                       "review session 1", post="reviewer", may=READER_MAY)
    assert kinds(found) == [("ball", "feat/x")]
    assert "abc1234" in found[0].text
    assert found[0].text.split("moves:")[1].strip() == "accept, fix, recuse, take, wait"


def test_the_author_does_not_hold_a_handed_row():
    assert whose.mine(rows(row(state="handed", reader="review session 1")), PR, "main session 1") == []


def test_a_post_named_move_belongs_to_that_post():
    accepted = rows(row(state="accepted"))
    assert kinds(whose.mine(accepted, PR, "sender 1", post="sender")) == [("ball", "feat/x")]
    assert whose.mine(accepted, PR, "main session 1", post="main") == []


def test_a_recorded_wait_is_not_a_ball():
    found = whose.mine(rows(row(state="fixing", waiting_on="the person", note="asked about scope")), PR,
                       "main session 1")
    assert kinds(found) == [("waiting", "feat/x")] and "the person" in found[0].text


def test_a_held_row_is_not_a_ball():
    found = whose.mine(rows(row(held_by="orchestrator 1", held_until="feat/y", held_why="stacked")), PR,
                       "main session 1")
    assert [item.kind for item in found] == ["waiting"]


def test_claimed_work_is_held_by_its_owner():
    assert kinds(whose.mine(rows(row()), PR, "main session 1")) == [("working", "feat/x")]


def test_handed_work_with_no_reader_is_named_to_its_author():
    assert kinds(whose.mine(rows(row(state="handed")), PR, "main session 1")) == [("unread", "feat/x")]


def test_a_lifted_hold_is_named_to_whoever_placed_it():
    held = row(held_by="orchestrator 1", held_until="feat/y", held_why="waits for y")
    done = row(id="r2", branch="feat/y", state="accepted")
    found = whose.mine(rows(held, done), PR, "orchestrator 1", post="orchestrator",
                       live={"orchestrator 1", "main session 1"})
    assert ("hold", "feat/x") in kinds(found)


def test_finished_rows_are_nobodys_move():
    assert whose.mine(rows(row(state="closed")), PR, "main session 1") == []


def test_the_moves_offered_are_legal_and_allowed_to_the_post():
    fixing = row(state="fixing")
    assert whose.next_moves(fixing, PR, frozenset({"hand", "wait", "close"})) == ["hand", "wait"]
    assert "run" not in whose.next_moves(fixing, PR)


def test_without_a_post_no_move_is_promised():
    found = whose.mine(rows(row(state="fixing")), PR, "main session 1", may=frozenset())
    assert found[0].text.endswith("your moves: none your post may make")
