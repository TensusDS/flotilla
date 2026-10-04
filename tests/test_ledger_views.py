import datetime as dt

from flotilla.ledger import views
from flotilla.ledger.model import Row

PR = {"flow": {"mode": "pr"}, "review": {"depth": "every"}}
JUDGED = {**PR, "judge": {"required": True}}
NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.timezone.utc)


def row(id="r1", **fields):
    return Row(id=id, **{"branch": "feat/x", "owner": "main session 1", "state": "claimed", **fields})


def rows(*items):
    return {item.id: item for item in items}


def test_whose_move_follows_the_state_not_the_owner():
    assert views.who_moves(row(), PR) == "main session 1"
    assert views.who_moves(row(state="handed", reader="review session 1"), PR) == "review session 1"
    assert views.who_moves(row(state="handed"), PR) == ""
    assert views.who_moves(row(state="accepted"), PR) == views.SENDER
    assert views.who_moves(row(state="shipped"), PR) == "main session 1"
    assert views.who_moves(row(state="shipped"), JUDGED) == views.JUDGE
    assert views.who_moves(row(state="closed"), PR) == ""


def test_the_roster_computes_each_sessions_state():
    table = rows(
        row("r1", branch="a", owner="main session 1", state="claimed"),
        row("r2", branch="b", owner="main session 2", state="handed", reader="review session 1", taken=True),
        row("r3", branch="c", owner="main session 3", state="claimed", requires=["r1"]),
        row("r4", branch="d", owner="minor session 1", state="accepted"),
        row("r5", branch="e", owner="sender 1", state="reserved"),
        row("r6", branch="f", owner="minor session 9", state="claimed"),
    )
    live = {"main session 1", "main session 2", "main session 3", "review session 1", "minor session 1", "sender 1"}
    found = {entry["who"]: entry["state"] for entry in views.roster(table, PR, live)}
    assert found == {"main session 1": "working", "main session 2": "waiting", "main session 3": "blocked",
                     "review session 1": "reading", "minor session 1": "waiting", "sender 1": "idle",
                     "minor session 9": "orphaned"}


def test_without_the_census_nobody_is_called_orphaned():
    found = views.roster(rows(row(owner="gone session 1")), PR, None)
    assert found[0]["state"] == "working"


def test_stalled_is_measured_from_the_last_move():
    table = rows(row("r1", branch="old", updated_at="2026-09-26T06:00:00+00:00"),
                 row("r2", branch="new", updated_at="2026-09-26T11:30:00+00:00"),
                 row("r3", branch="post", state="reserved", updated_at="2026-09-20T00:00:00+00:00"),
                 row("r4", branch="done", state="closed", updated_at="2026-09-20T00:00:00+00:00"))
    assert [item.branch for item in views.stalled(table, 4, NOW)] == ["old"]


def test_a_hold_is_asked_of_the_world():
    waiting_on_branch = row("r1", state="handed", held_by="orchestrator 1", held_until="feat/y")
    assert views.hold_lifted(waiting_on_branch, rows(waiting_on_branch, row("r2", branch="feat/y", state="handed"))) is False
    assert views.hold_lifted(waiting_on_branch, rows(waiting_on_branch, row("r2", branch="feat/y", state="accepted"))) is True
    on_reader = row("r1", state="handed", held_by="orchestrator 1", held_until="review session 1")
    busy = row("r2", branch="feat/y", state="handed", reader="review session 1")
    assert views.hold_lifted(on_reader, rows(on_reader, busy), {"review session 1"}) is False
    assert views.hold_lifted(on_reader, rows(on_reader), {"review session 1"}) is True
    assert views.hold_lifted(on_reader, rows(on_reader), {"someone else"}) is None
    assert views.hold_lifted(row(), rows(row())) is None


def test_deviations_are_moves_that_cannot_happen_now():
    table = rows(
        row("r1", branch="nobody", state="handed"),
        row("r2", branch="held", state="handed", held_by="orchestrator 1", held_until="nobody-left", reader=""),
        row("r3", branch="gone", state="handed", reader="review session 7"),
        row("r4", branch="broken", state="shipped", broken="Settings"),
        row("r5", branch="done", state="claimed"),
    )
    found = {(item["branch"], item["kind"]) for item in views.deviations(
        table, PR, live={"main session 1"}, finished=lambda item: item.branch == "done")}
    assert found == {("nobody", "nobody_named"), ("held", "hold_unknown"), ("gone", "mover_gone"),
                     ("broken", "broken_unfixed"), ("done", "finished_not_handed")}


def test_without_the_census_no_mover_is_called_gone():
    table = rows(row("r1", state="handed", reader="review session 7"))
    assert views.deviations(table, PR, live=None) == []


LOCAL = {"flow": {"mode": "local"}, "review": {"depth": "every"}}


def test_a_hold_on_a_branch_lifts_only_once_that_branch_is_accepted():
    held = row("r1", state="handed", held_by="orchestrator 1", held_until="feat/y")
    still_written = row("r2", branch="feat/y", state="claimed")
    assert views.hold_lifted(held, rows(held, still_written)) is False


def test_without_origin_a_landed_row_is_the_owners_to_close():
    assert views.who_moves(row(state="landed"), LOCAL) == "main session 1"
    assert views.who_moves(row(state="landed"), PR) == views.SENDER


def test_a_post_seat_whose_session_is_gone_is_an_empty_seat_not_a_gone_mover():
    seat = row("r1", branch="fleet/reviewer-1", owner="review session 1", state="reserved")
    found = views.deviations(rows(seat), PR, live=set())
    assert [item["kind"] for item in found] == ["seat_empty"]


def settled(fix_id, by):
    return row(fix_id, branch="fix/x", state="released", fixes="r1",
               history=[{"move": "release", "state": "released", "evidence": {"settled_by": by}}])


def test_a_fix_settled_by_a_delivered_row_leaves_the_broken_row_fixed():
    table = rows(row("r1", branch="feat/x", state="shipped", broken="Settings"), settled("r2", "r3"),
                 row("r3", branch="fix/other", state="shipped"))
    assert "broken_unfixed" not in {item["kind"] for item in views.deviations(table, PR)}
    assert views.fix_delivery(table, table["r2"], PR).id == "r3"


def test_a_fix_settled_by_an_undelivered_row_is_not_a_delivery():
    table = rows(row("r1", branch="feat/x", state="shipped", broken="Settings"), settled("r2", "r3"),
                 row("r3", branch="fix/other", state="handed"))
    assert views.fix_delivery(table, table["r2"], PR) is None
    assert "broken_unfixed" in {item["kind"] for item in views.deviations(table, PR)}
    missing = rows(row("r1", branch="feat/x", state="shipped", broken="Settings"), settled("r2", "r9"))
    assert views.fix_delivery(missing, missing["r2"], PR) is None


def test_a_fix_released_without_settlement_leaves_the_row_unfixed():
    gone = row("r2", branch="fix/x", state="released", fixes="r1",
               history=[{"move": "release", "state": "released", "evidence": {"why": "will not happen"}}])
    table = rows(row("r1", branch="feat/x", state="shipped", broken="Settings"), gone)
    assert "broken_unfixed" in {item["kind"] for item in views.deviations(table, PR)}


def test_a_broken_row_is_nobodys_move_until_its_fix_arrives():
    table = rows(row("r1", branch="feat/x", state="shipped", broken="Settings"),
                 row("r2", branch="fix/x", state="claimed", fixes="r1"))
    assert views.who_moves(table["r1"], JUDGED, table) == ""
    table["r2"] = row("r2", branch="fix/x", state="shipped", fixes="r1")
    assert views.who_moves(table["r1"], JUDGED, table) == views.JUDGE


def broken_table(fix_state="claimed"):
    return rows(row("r1", state="shipped", broken="the storm wall"),
                row("r2", branch="fix/x", state=fix_state, fixes="r1"))


def test_a_broken_row_names_its_open_fix():
    table = broken_table()
    assert views.who_moves(table["r1"], JUDGED, table) == ""
    assert views.waits_on(table["r1"], table, JUDGED) == "the fix `fix/x` (main session 1)"


def test_a_broken_row_with_no_fix_says_so():
    table = broken_table("released")
    assert views.waits_on(table["r1"], table, JUDGED) == "nobody: it broke and no fix row is open"


def test_a_row_with_a_mover_waits_on_nothing():
    table = broken_table()
    assert views.waits_on(table["r2"], table, JUDGED) == ""
    assert views.waits_on(row(state="handed"), rows(row(state="handed")), JUDGED) == ""


LATER = "2026-09-26T12:30:00+00:00"


def test_a_wait_naming_a_row_that_moved_on_is_marked():
    waiting = row("r1", state="handed", waiting_on="the person", note="r2 is blocked on inbatch permission",
                  updated_at=NOW.isoformat())
    table = rows(waiting, row("r2", branch="feat/y", state="shipped", updated_at=LATER))
    assert views.moved_since(table, waiting) == ["r2 shipped"]


def test_a_wait_naming_a_branch_that_moved_on_is_marked():
    waiting = row("r1", state="handed", waiting_on="the person", note="goes after feat/y, then r2.",
                  updated_at=NOW.isoformat())
    table = rows(waiting, row("r2", branch="feat/y", state="handed", updated_at=LATER),
                 row("r3", branch="feat/y-2", state="shipped", updated_at=LATER))
    assert views.moved_since(table, waiting) == ["r2 handed"]


def test_a_wait_whose_named_row_has_not_moved_is_not_marked():
    waiting = row("r1", state="handed", waiting_on="the person", note="after r2", updated_at=LATER)
    table = rows(waiting, row("r2", branch="feat/y", state="shipped", updated_at=NOW.isoformat()))
    assert views.moved_since(table, waiting) == []


def test_a_stale_wait_matches_whole_row_ids_only():
    moved = row("r3", branch="feat/y", state="shipped", updated_at=LATER)
    for note in ("r33 error", "the error", "r3x is next", "br3 is next"):
        waiting = row("r1", state="handed", waiting_on="the person", note=note, updated_at=NOW.isoformat())
        assert views.moved_since(rows(waiting, moved), waiting) == [], note


def test_a_broken_row_whose_fix_arrived_names_no_fix():
    table = broken_table("shipped")
    assert views.who_moves(table["r1"], JUDGED, table) == views.JUDGE
    assert views.waits_on(table["r1"], table, JUDGED) == ""


def test_a_broken_part_whose_fix_arrived_does_not_wait_on_that_fix():
    table = broken_table("shipped")
    table["r3"] = row("r3", branch="feat/whole", state="claimed", requires=["r1"])   # the part gate holds r1
    assert views.who_moves(table["r1"], JUDGED, table) == ""
    assert "fix/x" not in views.waits_on(table["r1"], table, JUDGED)


def _post_of(name):
    for prefix, post in (("main session", "main"), ("review session", "reviewer")):
        if name.startswith(prefix):
            return post
    return None


def test_a_gone_mover_comes_with_who_can_take_the_move():
    """Twosuns field test of 0.6.7 and the person's proposal: a fix row filed for a session of a past fleet was an
    alarm the orchestrator had to work out each time - whose post, who is free, which move. The alarm now names the
    live sessions of that post, least loaded first, and the move that hands the work over."""
    table = rows(row("r1", branch="fix/thunder", owner="main session 19"),
                 row("r2", branch="feat/far", owner="main session 1"),
                 row("r3", branch="feat/y", owner="main session 1", state="handed", reader="review session 9"))
    live = {"main session 1", "main session 2", "review session 1"}
    why = {item["branch"]: item["why"] for item in views.deviations(table, PR, live, post_of=_post_of)}
    assert "flotilla work adopt fix/thunder --to 'main session 2'" in why["fix/thunder"], why
    assert "main session 1 (2 open)" in why["fix/thunder"] and "main session 2 (0 open)" in why["fix/thunder"]
    assert "flotilla work assign feat/y --reader 'review session 1'" in why["feat/y"], why


def test_a_gone_mover_with_no_live_peer_names_raising_one():
    table = rows(row("r1", branch="fix/thunder", owner="main session 19"))
    found = views.deviations(table, PR, {"review session 1"}, post_of=_post_of)
    assert "flotilla spawn --post main=1" in found[0]["why"], found


def test_a_branch_name_with_braces_does_not_break_the_hint():
    """Review of 0.6.9, I3: the hint built its command with str.format, and `fix/{x}` - a legal branch name -
    raised out of `work status` and every orchestrator hook."""
    table = rows(row("r1", branch="fix/{x}-{0}", owner="main session 19"))
    found = views.deviations(table, PR, {"main session 2"}, post_of=_post_of)
    assert "fix/{x}-{0}" in found[0]["why"] and "main session 2" in found[0]["why"]


def test_the_hint_quotes_a_branch_name_a_shell_would_read():
    """Review of 0.6.9, M8: the orchestrator copies the printed line into Bash."""
    table = rows(row("r1", branch="fix/$(touch x)", owner="main session 19"))
    found = views.deviations(table, PR, {"main session 2"}, post_of=_post_of)
    assert "adopt 'fix/$(touch x)' --to 'main session 2'" in found[0]["why"], found[0]["why"]


def _strict(name):
    return {"twosuns-main session 1": "main", "twosuns-review session 1": "reviewer",
            "twosuns-review session 2": "reviewer"}.get(name, "")


def _former(name):
    return _strict(name) or _post_of(name)


def test_the_hint_offers_only_this_projects_sessions(tmp_path):
    """Review of 0.6.9, I4: the former-name mapping was used for every live name, so `main session 3` - another
    project's, or an older fleet's - was offered first, and adopt would refuse it."""
    table = rows(row("r1", branch="fix/a", owner="main session 19"))
    found = views.deviations(table, PR, {"main session 3", "twosuns-main session 1"}, post_of=_strict,
                             former_of=_former)
    why = found[0]["why"]
    assert "twosuns-main session 1" in why and "main session 3" not in why, why


def test_a_gone_readers_successors_are_ordered_by_what_they_read():
    """Review of 0.6.9, M7: readers own only their seat row, so counting owned rows ordered them by name."""
    table = rows(row("r1", branch="feat/a", owner="twosuns-main session 1", state="handed", reader="review session 9"),
                 row("r2", branch="feat/b", owner="twosuns-main session 1", state="handed",
                     reader="twosuns-review session 1"))
    live = {"twosuns-main session 1", "twosuns-review session 1", "twosuns-review session 2"}
    found = [item for item in views.deviations(table, PR, live, post_of=_strict, former_of=_former)
             if item["branch"] == "feat/a"]
    assert "--reader 'twosuns-review session 2'" in found[0]["why"], found[0]["why"]


def test_a_wait_on_a_branch_is_over_only_once_that_branch_has_no_open_row():
    waiting = row("r1", state="accepted", waiting_on="feat/y", note="y ships first")
    assert views.wait_over(waiting, rows(waiting, row("r2", branch="feat/y", state="shipped"))) == ""
    assert views.wait_over(waiting, rows(waiting, row("r2", branch="feat/y", state="closed"))) == "lifted"
    reopened = rows(waiting, row("r2", branch="feat/y", state="closed"), row("r3", branch="feat/y", state="claimed"))
    assert views.wait_over(waiting, reopened) == ""   # the branch came back under a new row: still waited on


def test_a_wait_on_a_session_is_over_when_that_session_is_gone():
    known = row("r2", branch="feat/y", owner="main session 2")
    waiting = row("r1", state="claimed", waiting_on="main session 2", note="asked")
    assert views.wait_over(waiting, rows(waiting, known), {"main session 1", "main session 2"}) == ""
    assert views.wait_over(waiting, rows(waiting, known), {"main session 1"}) == "gone"
    assert views.wait_over(waiting, rows(waiting, known), None) == ""   # no census: nothing is asserted


def test_a_wait_the_ledger_cannot_ask_stays_a_wait():
    for on in ("the person", "the lane", "CI on origin", "main session 9"):   # the last is a name nobody has used
        waiting = row("r1", state="claimed", waiting_on=on, note="x")
        assert views.wait_over(waiting, rows(waiting), {"main session 1"}) == ""
    assert views.wait_over(row(), rows(row())) == ""


def test_an_over_wait_is_a_deviation_that_names_the_way_out():
    table = rows(row("r1", branch="a", state="accepted", waiting_on="feat/y", note="first"),
                 row("r2", branch="feat/y", state="closed"),
                 row("r3", branch="b", state="claimed", waiting_on="main session 2", note="asked"),
                 row("r4", branch="c", owner="main session 2", state="closed"))
    found = {item["branch"]: item for item in views.deviations(table, PR, live={"main session 1"})}
    assert found["a"]["kind"] == "wait_lifted" and "`feat/y`" in found["a"]["why"]
    assert "flotilla work wait a --clear" in found["a"]["why"]
    assert found["b"]["kind"] == "wait_gone" and "main session 2" in found["b"]["why"]


def test_the_clear_wait_line_is_shell_quoted():
    assert views.clear_wait("feat/x") == "flotilla work wait feat/x --clear"
    assert views.clear_wait("feat/$(touch pwned)") == "flotilla work wait 'feat/$(touch pwned)' --clear"
