import datetime as dt

from flotilla.fleet.pace import pace
from flotilla.ledger.model import Row

NOW = dt.datetime(2026, 10, 4, 18, 0, tzinfo=dt.timezone.utc)


def _row(rid, *moves):
    row = Row(id=rid)
    for at, move in moves:
        row.history.append({"at": f"2026-10-{at}:00+00:00", "move": move, "state": ""})
    return row


def test_three_claim_to_hand_rows_give_the_author_rate():
    rows = [_row(f"r{i}", ("04T10:00", "claim"), ("04T12:00", "hand")) for i in range(3)]
    p = pace(rows, now=NOW)
    assert p.handovers_per_author_hour == 0.5 and "median of 3" in p.note


def test_three_hand_to_verdict_rows_give_review_hours():
    rows = [_row("a", ("04T10:00", "claim"), ("04T12:00", "hand"), ("04T12:30", "accept")),
            _row("b", ("04T10:00", "claim"), ("04T12:00", "hand"), ("04T12:30", "fix")),
            _row("c", ("04T10:00", "claim"), ("04T12:00", "hand"), ("04T12:30", "accept"))]
    p = pace(rows, now=NOW)
    assert p.review_hours == 0.5


def test_a_returned_row_is_measured_on_each_round():
    """hand -> fix -> hand -> accept: two reviews; the second authoring round runs from the fix to the next hand."""
    rows = [_row("a", ("04T08:00", "claim"), ("04T09:00", "hand"), ("04T10:00", "fix"), ("04T11:00", "hand"),
                 ("04T12:00", "accept")),
            _row("b", ("04T08:00", "claim"), ("04T09:00", "hand"), ("04T10:00", "accept"))]
    p = pace(rows, now=NOW)
    assert p.measured and p.handovers_per_author_hour == 1.0 and p.review_hours == 1.0


def test_two_samples_fall_back_and_say_so():
    rows = [_row(f"r{i}", ("04T10:00", "claim"), ("04T12:00", "hand"), ("04T12:30", "accept")) for i in range(2)]
    p = pace(rows, now=NOW)
    assert p.handovers_per_author_hour == 1.0 and p.review_hours == 0.33 and not p.measured
    assert "2" in p.note and "default" in p.note


def test_events_outside_the_window_are_ignored():
    old = [_row(f"o{i}", ("01T10:00", "claim"), ("01T11:00", "hand"), ("01T11:06", "accept")) for i in range(5)]
    old = [_row(r.id, *[]) for r in old]
    for r in old:
        r.history = [{"at": "2026-08-01T10:00:00+00:00", "move": "claim"},
                     {"at": "2026-08-01T11:00:00+00:00", "move": "hand"},
                     {"at": "2026-08-01T11:06:00+00:00", "move": "accept"}]
    recent = [_row(f"r{i}", ("04T10:00", "claim"), ("04T14:00", "hand"), ("04T15:00", "accept")) for i in range(3)]
    p = pace(old + recent, now=NOW)
    assert p.handovers_per_author_hour == 0.25 and p.review_hours == 1.0


def test_an_empty_ledger_and_unreadable_times_fall_back():
    assert not pace([], now=NOW).measured
    bad = [_row(f"r{i}") for i in range(3)]
    for r in bad:
        r.history = [{"at": "garbage", "move": "claim"}, {"at": "", "move": "hand"}]
    assert not pace(bad, now=NOW).measured


def test_rows_may_be_a_dict():
    rows = {f"r{i}": _row(f"r{i}", ("04T10:00", "claim"), ("04T12:00", "hand")) for i in range(3)}
    assert pace(rows, now=NOW).handovers_per_author_hour == 0.5


def test_the_median_not_the_mean():
    """One row left claimed over a weekend must not make the whole fleet look slow."""
    rows = [_row("a", ("04T10:00", "claim"), ("04T11:00", "hand")),
            _row("b", ("04T10:00", "claim"), ("04T11:00", "hand")),
            _row("c", ("04T00:00", "claim"), ("04T10:00", "hand"))]
    assert pace(rows, now=NOW).handovers_per_author_hour == 1.0


def test_a_second_hand_without_a_new_start_is_not_an_authoring_round():
    rows = [_row(f"r{i}", ("04T10:00", "claim"), ("04T11:00", "hand"), ("04T16:00", "hand")) for i in range(3)]
    assert pace(rows, now=NOW).handovers_per_author_hour == 1.0


def test_a_verdict_is_counted_once_per_handover():
    rows = [_row(f"r{i}", ("04T10:00", "claim"), ("04T12:00", "hand"), ("04T12:30", "accept"),
                 ("04T16:30", "fix")) for i in range(3)]
    assert pace(rows, now=NOW).review_hours == 0.5


def test_a_handover_with_an_unreadable_time_is_skipped_not_fatal():
    rows = [_row(f"r{i}", ("04T10:00", "claim")) for i in range(3)]
    for r in rows:
        r.history.append({"at": "not a time", "move": "hand"})
    assert not pace(rows, now=NOW).measured


def test_events_dated_after_now_are_ignored():
    """Review of 0.7.14 (security): a ledger event from the future made a pace nobody kept."""
    rows = [_row(f"r{i}", ("04T10:00", "claim"), ("04T12:00", "hand")) for i in range(3)]
    rows += [_row(f"f{i}", ("09T10:00", "claim"), ("09T10:01", "hand")) for i in range(5)]
    assert pace(rows, now=NOW).handovers_per_author_hour == 0.5
