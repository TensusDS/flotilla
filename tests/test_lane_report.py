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


def test_memory_near_claude_codes_line_names_the_projects_runs_at_risk():
    from types import SimpleNamespace as NS

    from flotilla.lane import book
    order = [NS(id="b1", project="p", state=book.HELD, who="main 3"),
             NS(id="b2", project="p", state=book.WAITING, who="review 2"),
             NS(id="b3", project="other", state=book.WAITING, who="main 9"),
             NS(id="b4", project="p", state=book.RELEASED, who="main 4")]
    found = report.memory_items(order, "p", 1900, 1638, 2150)
    assert len(found) == 1 and found[0].kind == report.MEMORY
    text = found[0].text
    assert "1900 MB free" in text and "2150 MB" in text and "1638 MB" in text
    assert "b1 main 3 (running)" in text and "b2 review 2 (waiting)" in text and "b3" not in text and "b4" not in text
    assert report.memory_items(order, "p", 2150, 1638, 2150) == []     # at the floor: not short
    assert report.memory_items(order, "p", None, 1638, 2150) == []     # memory unread
    assert report.memory_items(order, "p", 1000, None, 2150) == []     # no line drawn (a container, macOS)
    assert report.memory_items(order, "quiet", 1000, 1638, 2150) == []  # nothing booked: nothing to lose


def test_memory_item_names_six_runs_and_counts_the_rest():
    from types import SimpleNamespace as NS

    from flotilla.lane import book
    order = [NS(id=f"b{n}", project="p", state=book.WAITING, who=f"main {n}") for n in range(9)]
    text = report.memory_items(order, "p", 100, 1638, 2150)[0].text
    assert "b5 main 5" in text and "b6" not in text and "and 3 more" in text


def test_the_beat_carries_the_wait_estimates_and_a_malformed_one_reads_as_none(tmp_path):
    root = lane_root(tmp_path)
    daemon._write_beat(root, 4242, "m", {"eta": {"b3": 240}}, 1.0)
    assert daemon.heartbeat(root).eta == {"b3": 240}
    daemon._write_beat(root, 4242, "m", {"eta": [1, 2]}, 1.0)
    assert daemon.heartbeat(root).eta == {}
    daemon._write_beat(root, 4242, "m", {}, 1.0)
    assert daemon.heartbeat(root).eta == {}



def test_the_memory_item_is_read_from_the_journal_with_the_projects_floor(tmp_path, monkeypatch):
    from flotilla.lane import machine
    root = lane_root(tmp_path)
    lanes = book.Book(LocalLogStore(root), None)
    waiting = lanes.enqueue("main 3", "suite", pid=5, mark="m", project="p1")
    gone = lanes.enqueue("main 4", "suite", pid=6, mark="m", project="p1")
    monkeypatch.setattr(report, "_available", lambda: 1800)
    monkeypatch.setattr(machine, "_host_total", lambda: 16 * 1024 * 1024)
    monkeypatch.setattr(machine, "_memtotal", lambda: 16 * 1024 * 1024)
    monkeypatch.setattr(report, "_live", lambda: (lambda item: item.id != gone.id))   # read as `live()`
    found = [item for item in report.items(tmp_path, "p1", NOW) if item.kind == report.MEMORY]
    assert len(found) == 1 and "under the 2150 MB" in found[0].text and waiting.id in found[0].text
    assert gone.id not in found[0].text                                        # its process is gone: nothing to lose
    own = report.items(tmp_path, "p1", NOW, {"lane": {"memory_floor_mb": 1500}})
    assert [item for item in own if item.kind == report.MEMORY] == []          # the project's own floor: not short



def test_flotilla_lane_marks_the_runs_the_daemon_reads_as_hung(tmp_path):
    from flotilla.lane import policy
    root = lane_root(tmp_path)
    daemon._write_beat(root, 4242, "m", {"hung": {"b7": HUNG}}, 1.0)
    runs = [policy.Run("b7", policy.RUNNING, 100, False, 1), policy.Run("b8", policy.RUNNING, 100, False, 1)]
    assert [r.hung for r in commands.mark_hung(runs, daemon.heartbeat(root))] == [True, False]
    assert [r.hung for r in commands.mark_hung(runs, None)] == [False, False]
