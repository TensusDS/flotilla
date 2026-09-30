from flotilla.ledger import letters
from watchkit import posts, row, rows

DIRECT = {"flow": {"mode": "direct"}, "review": {"depth": "every"}}
JUDGED = {**DIRECT, "judge": {"required": True}}


def live(*names):
    return lambda: set(names)


def never():
    raise AssertionError("the census was asked although no letter was due")


def test_an_accepted_row_writes_to_the_live_sender(tmp_path):
    before = rows(row(state="handed", reader="review session 1", tip="abc1234def"))
    after = rows(row(state="accepted", reader="review session 1", verdict="abc1234def"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "review session 1",
                            live("sender 1", "main session 1", "review session 1"))
    assert [(item.mover, item.to) for item in found] == [("the sender", ("sender 1",))]
    assert "accepted by its reader" in found[0].text and "flotilla work show feat/x" in found[0].text


def test_no_letter_when_the_move_stays_with_the_callers_post(tmp_path):
    before = rows(row(state="accepted", reader="review session 1"))
    after = rows(row(state="queued", reader="review session 1"))
    assert letters.changed(before, after, DIRECT, posts(tmp_path), "sender 1", never) == []


def test_no_letter_when_nobody_new_holds_the_move(tmp_path):
    handed = row(state="handed", reader="review session 1", tip="abc1234def")
    waited = row(state="handed", reader="review session 1", tip="abc1234def", waiting_on="the person")
    assert letters.changed(rows(handed), rows(waited), DIRECT, posts(tmp_path), "main session 1", never) == []


def test_a_letter_is_addressed_by_post_when_the_census_is_unknown(tmp_path):
    before = rows(row(state="handed", reader="review session 1"))
    after = rows(row(state="accepted", reader="review session 1"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "review session 1", lambda: None)
    assert found[0].to == ("the session holding the sender post",)


def test_nobody_alive_to_move_it_says_so(tmp_path):
    before = rows(row(state="handed", reader="review session 1"))
    after = rows(row(state="accepted", reader="review session 1"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "review session 1", live("main session 1"))
    assert found[0].to == ()
    rendered = "\n".join(letters.render(found[0]))
    assert "no live session" in rendered and "tell the orchestrator" in rendered


def test_a_handed_row_carries_the_readers_letter(tmp_path):
    before = rows(row(state="handed", reader="", tip="abc1234def"))
    after = rows(row(state="handed", reader="review session 1", tip="abc1234def"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "orchestrator 1", live("review session 1"))
    assert found[0].to == ("review session 1",) and "flotilla work take feat/x" in found[0].text


def test_a_return_names_what_must_change(tmp_path):
    before = rows(row(state="handed", reader="review session 1"))
    after = rows(row(state="fixing", reader="review session 1", why="the score never resets"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "review session 1", live("main session 1"))
    assert found[0].to == ("main session 1",) and "the score never resets" in found[0].text


def test_a_move_on_one_row_that_frees_another_writes_for_both(tmp_path):
    part = row(id="r1", branch="feat/logic", state="shipped")
    whole_queued = row(id="r2", branch="feat/ui", state="queued", requires=["r1"])
    whole_shipped = row(id="r2", branch="feat/ui", state="shipped", requires=["r1"])
    found = letters.changed(rows(part, whole_queued), rows(part, whole_shipped), JUDGED, posts(tmp_path),
                            "sender 1", live("acceptance judge 1", "sender 1"))
    assert sorted(item.branch for item in found) == ["feat/logic", "feat/ui"]
    assert {item.to for item in found} == {("acceptance judge 1",)}


def test_the_person_who_merges_a_pr_gets_no_letter(tmp_path):
    human = {"flow": {"mode": "pr"}, "pr": {"merged_by": "human"}, "review": {"depth": "every"}}
    before = rows(row(state="accepted", reader="review session 1"))
    after = rows(row(state="queued", reader="review session 1", pr="12"))
    assert letters.changed(before, after, human, posts(tmp_path), "sender 1", never) == []


def test_render_names_the_recipients_and_how_to_send():
    found = letters.Letter(mover="the sender", to=("sender 1",), branch="feat/x", text="line one\nline two")
    rendered = letters.render(found)
    assert rendered[0].startswith("letter for sender 1") and "SendMessage" in rendered[0]
    assert rendered[1:] == ["  line one", "  line two"]


def test_a_fix_settled_by_another_row_wakes_the_judge(tmp_path):
    broken = row(id="r1", branch="feat/x", state="shipped", broken="Settings", merge="abc")
    waiting_fix = row(id="r2", branch="fix/x", state="claimed", fixes="r1")
    settled_fix = row(id="r2", branch="fix/x", state="released", fixes="r1",
                      history=[{"move": "release", "state": "released", "evidence": {"settled_by": "r3"}}])
    other = row(id="r3", branch="fix/other", state="shipped")
    before = rows(broken, waiting_fix, other)
    after = rows(broken, settled_fix, other)
    found = letters.changed(before, after, JUDGED, posts(tmp_path), "main session 1", live("acceptance judge 1"))
    assert [(item.branch, item.to) for item in found if item.branch == "feat/x"] == [("feat/x",
                                                                                    ("acceptance judge 1",))]
