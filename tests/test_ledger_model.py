import pytest

from flotilla.ledger.model import LedgerVersionError, fold, make_event, next_row_id


def event(row, move, state, fields=None, by="main session 1"):
    return make_event(row=row, move=move, state=state, by=by, post="main", via="as", fields=fields or {},
                      evidence={}, at="2026-09-24T00:00:00+00:00", plugin="0.1.0")


def test_fold_creates_then_updates_a_row():
    rows = fold([event("r1", "claim", "claimed", {"branch": "feat/x", "owner": "main session 1"}),
                 event("r1", "hand", "handed", {"tip": "abc"})])
    row = rows["r1"]
    assert (row.branch, row.owner, row.state, row.tip) == ("feat/x", "main session 1", "handed", "abc")


def test_history_keeps_every_move():
    rows = fold([event("r1", "claim", "claimed", {"branch": "b", "owner": "o"}),
                 event("r1", "hand", "handed"),
                 event("r1", "fix", "fixing", by="review session 1")])
    assert [h["move"] for h in rows["r1"].history] == ["claim", "hand", "fix"]
    assert rows["r1"].history[2]["by"] == "review session 1"


def test_an_unknown_field_is_refused_at_write():
    with pytest.raises(ValueError, match="colour"):
        event("r1", "claim", "claimed", {"colour": "red"})


def test_an_unknown_state_is_refused_at_write():
    with pytest.raises(ValueError, match="merged"):
        event("r1", "claim", "merged")


def test_a_newer_event_version_is_refused():
    record = event("r1", "claim", "claimed", {"branch": "b", "owner": "o"})
    record["v"] = 99
    with pytest.raises(LedgerVersionError, match="update the plugin"):
        fold([record])


def test_row_ids_are_sequential():
    assert next_row_id({}) == "r1"
    assert next_row_id(fold([event("r1", "claim", "claimed", {"branch": "b", "owner": "o"})])) == "r2"


def test_terminal_rows_are_not_open():
    rows = fold([event("r1", "claim", "claimed", {"branch": "b", "owner": "o"}), event("r1", "release", "released")])
    assert rows["r1"].is_open is False


def test_part_b_fields_fold_onto_the_row():
    from flotilla.ledger.model import fold, make_event
    event = make_event(row="r1", move="claim", state="claimed", by="a", post="main", via="as",
                       fields={"branch": "b", "held_until": "x", "merge": "abc", "fixes": "r0", "broken": "home"},
                       evidence={}, at="2026-09-26T10:00:00+00:00", plugin="0")
    row = fold([event])["r1"]
    assert (row.held_until, row.merge, row.fixes, row.broken) == ("x", "abc", "r0", "home")


def test_delivered_counts_landed_only_without_origin():
    from flotilla.ledger.model import Row, delivered
    assert delivered(Row(id="r1", state="landed"), {"flow": {"mode": "local"}})
    assert not delivered(Row(id="r1", state="landed"), {"flow": {"mode": "direct"}})
    assert delivered(Row(id="r2", state="offledger"), {})
    assert delivered(Row(id="r3", state="inbatch"), {})
    assert not delivered(Row(id="r4", state="released"), {})


def test_blocked_by_names_what_is_not_delivered_yet():
    from flotilla.ledger.model import Row, blocked_by
    rows = {"r1": Row(id="r1", branch="a", state="claimed"), "r2": Row(id="r2", branch="b", state="shipped"),
            "r3": Row(id="r3", branch="c", state="handed", requires=["r1", "r2", "r9"])}
    assert [(r.id, r.state) for r in blocked_by(rows, rows["r3"], {})] == [("r1", "claimed"), ("r9", "")]


def test_a_helper_seat_names_its_parent_row():
    from flotilla.ledger.model import ROW_FIELDS, Row
    assert "helper_of" in ROW_FIELDS and Row(id="r1").helper_of == ""
