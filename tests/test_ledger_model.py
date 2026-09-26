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
