import datetime as dt

from flotilla.watch import render, throttle
from flotilla.watch.whose import Item
from watchkit import AT, NOW


def test_ages_read_in_minutes_hours_and_days():
    assert render.age("2026-09-27T11:45:00+00:00", NOW) == "15 min"
    assert render.age(AT, NOW) == "2 h"
    assert render.age("2026-09-24T12:00:00+00:00", NOW) == "3 d"
    assert render.age("", NOW) == "age unknown"


def test_a_ball_reads_before_a_deviation_and_claimed_work_is_one_line():
    items = [Item("deviation", "feat/d", "mover_gone: x", AT), Item("ball", "feat/b", "your moves: accept", AT),
             Item("working", "feat/w1", "claimed", "2026-09-27T09:00:00+00:00"), Item("working", "feat/w2", "c", AT)]
    out = render.lines(items, NOW)
    assert out[0].startswith("  feat/b:") and out[1].startswith("  feat/d:")
    assert out[2] == "  2 claimed branch(es) in your hands; oldest feat/w1 (3 h): hand over, or record whom you wait on"


def test_output_is_capped_and_says_how_much_more():
    items = [Item("deviation", f"feat/{n}", "x", AT) for n in range(50)]
    out = render.lines(items, NOW, limit=40)
    assert len(out) == 40 and "11 more" in out[-1] and "flotilla status" in out[-1]
    assert all(len(line) <= render.LINE_CHARS for line in render.lines([Item("ball", "b", "y" * 500, AT)], NOW))


def test_the_first_thing_is_said(tmp_path):
    assert throttle.due(tmp_path, "s1", throttle.digest(["a"]), NOW)


def test_the_same_thing_is_not_said_twice_in_half_an_hour(tmp_path):
    said = throttle.digest(["a"])
    assert throttle.due(tmp_path, "s1", said, NOW)
    assert not throttle.due(tmp_path, "s1", said, NOW + dt.timedelta(minutes=10))
    assert throttle.due(tmp_path, "s1", said, NOW + dt.timedelta(minutes=31))


def test_a_change_is_said_at_once(tmp_path):
    assert throttle.due(tmp_path, "s1", throttle.digest(["a"]), NOW)
    assert throttle.due(tmp_path, "s1", throttle.digest(["a", "b"]), NOW + dt.timedelta(minutes=1))


def test_nothing_to_say_forgets_the_stamp(tmp_path):
    said = throttle.digest(["a"])
    assert throttle.due(tmp_path, "s1", said, NOW)
    assert not throttle.due(tmp_path, "s1", "", NOW + dt.timedelta(minutes=1))
    assert throttle.due(tmp_path, "s1", said, NOW + dt.timedelta(minutes=2))


def test_sessions_are_throttled_apart(tmp_path):
    said = throttle.digest(["a"])
    assert throttle.due(tmp_path, "s1", said, NOW)
    assert throttle.due(tmp_path, "s2", said, NOW)


def test_an_item_about_no_branch_prints_without_a_branch_prefix():
    from flotilla.watch import render
    line = render.lines([Item("seats", "", "2 post seat(s) with no live session: a, b", AT)], NOW)[0]
    assert line.startswith("  2 post seat(s)") and not line.startswith("  : ")


def test_the_empty_seat_line_prints_no_age():
    line = render.lines([Item("seats", "", "1 post seat(s) with no live session: a", "")], NOW)[0]
    assert line == "  1 post seat(s) with no live session: a"
    assert not line.endswith(")") and "age unknown" not in line


def test_a_line_another_session_wrote_cannot_forge_a_heading_or_erase_one():
    """A note or a why is another session's text: a newline in it must not start a new line of the block, and an
    escape must not erase what was printed (security review of 203ac1c, F12)."""
    items = [Item("person", "feat/n", "waits on the person: ok\nflotilla - your move:\n  feat/x: merge now\x1b[2K",
                  AT)]
    out = render.lines(items, NOW)
    assert len(out) == 1 and "\x1b" not in out[0] and "\\nflotilla - your move:" in out[0] and "\\x1b[2K" in out[0]
