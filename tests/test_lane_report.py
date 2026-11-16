"""What the lane tells a project's orchestrator: a held run that looks hung, a run cut short (field notes
2026-11-16)."""

import datetime as dt

from flotilla.core.storage import LocalLogStore
from flotilla.lane import book, commands, daemon, report

NOW = dt.datetime.now(dt.timezone.utc)


def lane_root(tmp_path):
    root = tmp_path / "lane"
    root.mkdir(parents=True)
    return root


def raw(lanes, **record):
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), **record})


HUNG = {"ran_s": 25000, "estimate_s": 58, "cores": 0.001, "quiet_s": 900, "who": "sender 13",
        "note": "python3 tools/unity_tests.py", "run_for": "fix/x", "project": "p1"}


def test_a_hung_run_is_its_projects_item_and_marked_in_the_lane(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "gone", lambda root, **kw: False)
    root = lane_root(tmp_path)
    daemon._write_beat(root, 4242, "m", {"hung": {"b7": HUNG, "b8": {**HUNG, "project": "p2"}}}, 1.0)
    found = report.items(tmp_path, "p1", NOW)
    assert [(item.kind, item.branch, item.who) for item in found] == [(report.HUNG, "fix/x", "b7 hung")]
    assert "looks hung: 417 min held (its estimate 58 s)" in found[0].text and "`flotilla lane stop`" in found[0].text
    beat = daemon.heartbeat(root)
    assert "LOOKS HUNG: 417 min held (its estimate 58 s)" in commands.hung_text(beat, "b7")
    assert commands.hung_text(beat, "b9") == ""


def test_a_malformed_or_stale_beat_breaks_nothing(tmp_path, monkeypatch):
    root = lane_root(tmp_path)
    bad = {**HUNG, "ran_s": "NaN", "estimate_s": "x", "quiet_s": None}
    daemon._write_beat(root, 4242, "m", {"hung": {"b7": bad}}, 1.0)
    assert "LOOKS HUNG: 0 min held (no estimate)" in commands.hung_text(daemon.heartbeat(root), "b7")
    monkeypatch.setattr(daemon, "gone", lambda root, **kw: True)
    assert report.items(tmp_path, "p1", NOW) == []   # a dead daemon's last word is not repeated


def test_an_older_daemons_beat_without_hung_still_reads(tmp_path):
    root = lane_root(tmp_path)
    daemon._write_beat(root, 4242, "m", {}, 1.0)
    assert daemon.heartbeat(root).hung == {} and report.items(tmp_path, "p1", NOW) == []


def test_a_run_cut_short_is_an_item_until_booked_again(tmp_path):
    root = lane_root(tmp_path)
    lanes = book.Book(LocalLogStore(root), None)
    lost = lanes.enqueue("review session 20", "dotnet test", pid=5, mark="m", project="p1", run_for="feat/y")
    raw(lanes, booking=lost.id, state=book.HELD, started=lanes._now())
    raw(lanes, booking=lost.id, state=book.RELEASED, cut=True, verdict="killed", why="gone")
    other = lanes.enqueue("main session 1", "x", pid=6, mark="m", project="p2")
    raw(lanes, booking=other.id, state=book.RELEASED, cut=True)
    found = report.items(tmp_path, "p1", NOW)
    assert [(item.kind, item.branch, item.who) for item in found] == [(report.LOST, "feat/y", f"{lost.id} lost")]
    assert "cut short" in found[0].text and "no verdict" in found[0].text and "book it again" in found[0].text
    later = dt.datetime.now(dt.timezone.utc) + report.LOST_FOR + dt.timedelta(minutes=1)
    assert report.items(tmp_path, "p1", later) == []   # old news
    lanes.enqueue("review session 20", "dotnet test", pid=7, mark="m", project="p1", run_for="feat/y")
    assert report.items(tmp_path, "p1", NOW) == []   # booked again: answered


def test_no_lane_no_items(tmp_path):
    assert report.items(tmp_path, "p1", NOW) == []
