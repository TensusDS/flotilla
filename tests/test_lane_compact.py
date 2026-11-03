"""Compacting the lane journal (design 2026-11-03-lane-journal-compaction): kept by meaning, never by line count; the
rest archived; only ordinary records, so an older seat folds the result."""

import datetime as dt

from flotilla.lane import book, compact, estimate

NOW = dt.datetime(2026, 11, 3, 12, 0, tzinfo=dt.timezone.utc)
P = "proj"
LADDER = [f"exact:{P}:make check", f"prog:{P}:make", f"project:{P}"]


def at(**ago) -> str:
    return (NOW - dt.timedelta(**ago)).isoformat(timespec="seconds")


def run(bid, ended, *, ladder=LADDER, seconds=60.0, project=P, who="main session 1", ran=None):
    """A booking's three lines as a lane run writes them: queued, granted, released with its figures."""
    start = (dt.datetime.fromisoformat(ended) - dt.timedelta(seconds=seconds + 30)).isoformat(timespec="seconds")
    held = (dt.datetime.fromisoformat(ended) - dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")
    released = {"at": ended, "booking": bid, "state": book.RELEASED, "seconds": seconds, "verdict": "green",
                "cores": 2.0, "busy": 0.5, "peak_mb": 300.0}
    if ran is not None:
        released["ran"] = ran
    return [{"at": start, "booking": bid, "state": book.WAITING, "who": who, "note": "make check",
             "command": "make check " * 20, "ladder": list(ladder), "project": project, "rule": 2},
            {"at": held, "booking": bid, "state": book.HELD},
            released]


def usable(records):
    hist = estimate.history(book.fold(records), now=NOW)
    return {step: [s.seconds for s in samples] for step, samples in hist.items() if len(samples) >= estimate.ENOUGH}


def journal():
    records = []
    for n in range(12):   # one command run twelve times over the last three weeks
        records += run(f"b{n}", at(days=20 - n), seconds=60.0 + n)
    # a one-off older than the project's last samples, and a recent one: each its own command and program
    records += run("once-old", at(days=25), ladder=[f"exact:{P}:perl /tmp/job-1", f"prog:{P}:perl", f"project:{P}"])
    records += run("once-new", at(days=2), ladder=[f"exact:{P}:bash /tmp/job-2", f"prog:{P}:bash", f"project:{P}"])
    records += run("today", at(hours=3), project="other", ladder=["exact:other:npm test", "project:other"])
    return records


def test_a_booking_held_for_a_week_is_kept():
    held = [{"at": at(days=8), "booking": "hand", "state": book.HELD, "who": "the person", "note": "a long run"}]
    kept, _ = compact.plan(journal() + held, NOW)
    assert [r for r in kept if r["booking"] == "hand"] == held
    assert book.fold(kept)["hand"].state == book.HELD


def test_a_waiter_is_kept():
    waiting = [{"at": at(minutes=5), "booking": "w", "state": book.WAITING, "who": "main session 2", "rule": 2}]
    kept, _ = compact.plan(journal() + waiting, NOW)
    assert book.fold(kept)["w"].state == book.WAITING


def test_the_fair_share_reads_the_same_day():
    records = journal()
    kept, _ = compact.plan(records, NOW)
    assert book.last_served(book.fold(kept), NOW) == book.last_served(book.fold(records), NOW)
    assert [r for r in kept if r["booking"] == "today"] == [r for r in records if r["booking"] == "today"]


def test_every_usable_estimate_is_unchanged():
    records = journal()
    kept, _ = compact.plan(records, NOW)
    assert usable(kept) == usable(records) and usable(records)


def test_only_the_last_samples_of_a_step_are_kept():
    kept, archived = compact.plan(journal(), NOW)
    ids = {r["booking"] for r in kept}
    assert {"b0", "b1"}.isdisjoint(ids) and {f"b{n}" for n in range(2, 12)} <= ids


def test_a_one_off_older_than_a_week_is_archived():
    kept, archived = compact.plan(journal(), NOW)
    assert "once-old" not in {r["booking"] for r in kept}
    assert sum(r["booking"] == "once-old" for r in archived) == 3


def test_a_recent_one_off_is_kept():
    kept, _ = compact.plan(journal(), NOW)
    assert "once-new" in book.fold(kept)


def test_kept_and_archived_account_for_every_record():
    records = journal()
    kept, archived = compact.plan(records, NOW)
    verbatim = [r for r in kept if r in records]
    rewritten = {r["booking"] for r in kept if r not in records}
    assert sorted(map(repr, verbatim + archived)) == sorted(map(repr, records))
    assert all(sum(r["booking"] == bid for r in archived) == 3 for bid in rewritten)


def test_the_rewritten_record_folds_to_the_same_figures():
    records = journal()
    kept, _ = compact.plan(records, NOW)
    before, after = book.fold(records)["b11"], book.fold(kept)["b11"]
    assert (after.state, after.ended, after.seconds, after.project, after.verdict, after.cores, after.peak_mb) == \
        (before.state, before.ended, before.seconds, before.project, before.verdict, before.cores, before.peak_mb)
    assert len([r for r in kept if r["booking"] == "b11"]) == 1


def test_a_tier_sample_keeps_its_booking():
    """The measure on a copy of the field journal first dropped bookings kept only for their tiers' samples."""
    tiers = [{"name": "unit", "seconds": 30.0, "status": "green"}]
    records = []
    for n in range(4):
        records += run(f"r{n}", at(days=10 - n), ladder=[f"receipt:{P}:{n}"], ran=tiers)
    kept, _ = compact.plan(records, NOW)
    assert usable(kept) == usable(records) and any(step.startswith("tier") or "unit" in step for step in usable(kept))


# --- compacting on write (Task 3) ---

import gzip  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402

from flotilla.core.storage import LocalLogStore  # noqa: E402


class Procs:
    def alive(self, pid, mark):
        return True


def big_journal(root, bookings=4000):
    """A journal over the threshold: many old one-off runs, nothing an estimate reads, nothing live."""
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "lane.jsonl", "w", encoding="utf-8") as out:
        for n in range(bookings):
            for record in run(f"b{n + 1}", at(days=20, minutes=n), ladder=[f"exact:{P}:job {n}", f"project:{P}"]):
                out.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return root / "lane.jsonl"


def lanes(root):
    return book.Book(LocalLogStore(root), Procs(), clock=lambda: NOW)


def test_a_journal_over_the_threshold_is_compacted_on_write(tmp_path):
    path = big_journal(tmp_path)
    assert path.stat().st_size > compact.THRESHOLD
    item = lanes(tmp_path).enqueue("main session 1", "make check", pid=1, mark="m")
    assert path.stat().st_size < compact.THRESHOLD
    assert book.fold(LocalLogStore(tmp_path).read(book.KEY).records)[item.id].state == book.WAITING


def test_a_new_booking_takes_a_number_past_the_highest(tmp_path):
    """The person, 2026-11-03: no tombstones; a compacted journal holds fewer bookings than its highest number, so a
    number counted from the bookings would be one already taken."""
    big_journal(tmp_path)
    first = lanes(tmp_path).enqueue("main session 1", "one", pid=1, mark="m")
    second = lanes(tmp_path).enqueue("main session 2", "two", pid=2, mark="m")
    assert first.id == "b4001" and second.id == "b4002"


def test_a_journal_under_the_threshold_is_left(tmp_path):
    path = big_journal(tmp_path, bookings=10)
    before = path.read_bytes()
    lanes(tmp_path).enqueue("main session 1", "make check", pid=1, mark="m")
    assert path.read_bytes().startswith(before) and not (tmp_path / "archive").exists()


def test_a_journal_that_stays_large_is_not_compacted_on_every_write(tmp_path, monkeypatch):
    path = big_journal(tmp_path)
    monkeypatch.setattr(compact, "THRESHOLD", 1000)   # the kept core alone is over this threshold
    lanes(tmp_path).enqueue("main session 1", "a", pid=1, mark="m")
    left = json.loads((tmp_path / "lane.compacted").read_text())["bytes"]
    inode = path.stat().st_ino
    lanes(tmp_path).enqueue("main session 2", "b", pid=2, mark="m")
    assert path.stat().st_ino == inode and path.stat().st_size < left * compact.GROWTH


def test_the_archive_is_written_before_the_rename(tmp_path, monkeypatch):
    path = big_journal(tmp_path)
    before = path.read_bytes()

    def refuse(*args):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", refuse)
    item = lanes(tmp_path).enqueue("main session 1", "make check", pid=1, mark="m")
    assert path.read_bytes().startswith(before)   # the journal as it was, the append after it
    assert item.id in book.fold(LocalLogStore(tmp_path).read(book.KEY).records)
    archive = next((tmp_path / "archive").glob("*.jsonl.gz"))
    with gzip.open(archive, "rt", encoding="utf-8") as handle:
        assert sum(1 for _ in handle) >= 4000 * 3


def test_a_cached_reader_reads_the_new_journal(tmp_path):
    big_journal(tmp_path)
    watcher = lanes(tmp_path)
    watcher.bookings()   # a waiter's cache: an offset into the old file
    item = lanes(tmp_path).enqueue("main session 1", "make check", pid=1, mark="m")
    assert watcher.bookings() == book.fold(LocalLogStore(tmp_path).read(book.KEY).records)
    assert item.id in watcher.bookings()


def test_a_compaction_that_fails_leaves_the_journal_and_says_so(tmp_path, monkeypatch, capsys):
    path = big_journal(tmp_path)
    before = path.read_bytes()

    def broken(records, now):
        raise ValueError("a bad record")
    monkeypatch.setattr(compact, "plan", broken)
    lanes(tmp_path).enqueue("main session 1", "make check", pid=1, mark="m")
    assert path.read_bytes().startswith(before)
    assert "compact" in capsys.readouterr().err
