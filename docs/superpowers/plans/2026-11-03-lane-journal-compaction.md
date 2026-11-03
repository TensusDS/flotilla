# Lane journal compaction (0.26.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** The lane journal stays small: over 2 MB it is rewritten to what the lane and the estimates read, the rest
archived, without a new record kind.

**Architecture:** A pure planner (`flotilla/lane/compact.py`) splits the journal's records into kept and archived; the
store gains an atomic rewrite under its lock; `Lanes._write` compacts after an append when the journal is over the
threshold and has grown since the last compaction.

**Tech Stack:** Python 3.11+ stdlib (gzip, json, os), pytest.

**Spec:** `docs/specs/2026-11-03-lane-journal-compaction-design.md`

## Global Constraints

- Threshold 2 MB (`2_000_000` bytes); growth factor 1.5 over what the last compaction left; one-off steps stale after
  7 days; keep window = `book.SERVED_WINDOW`; samples per step = `estimate.KEEP`; usable = `estimate.ENOUGH`.
- Only ordinary journal records; a seat on an older flotilla folds the result.
- A failed compaction leaves the journal as it was; the append stands.

## Review Focus

- A booking held by hand for days must stay held after a compaction - Task 2 `test_a_booking_held_for_a_week_is_kept`.
- The fair share's last-served times must not change - Task 2 `test_the_fair_share_reads_the_same_day`.
- A reader with a cached offset must refold after the rename - Task 3 `test_a_cached_reader_reads_the_new_journal`.
- A crash between archive and rename loses nothing - Task 3 `test_the_archive_is_written_before_the_rename`.
- A journal whose kept core nears the threshold must not compact on every write - Task 3
  `test_a_journal_that_stays_large_is_not_compacted_on_every_write`.

---

### Task 1: An atomic rewrite under the store's lock

**Files:** Modify `flotilla/core/storage.py` (`LogTransaction`); Test `tests/test_storage.py` (or the store's test
file).

**Interfaces:** Produces `LogTransaction.replace(records: list[dict]) -> None` - writes the records, one JSON line
each as `append` writes them, to a temporary file beside the log, fsyncs it, and renames it over the log.

- [ ] Failing tests: `test_replace_rewrites_the_log_whole` (read after replace returns exactly the records);
  `test_replace_gives_the_log_a_new_identity` (`read_after` with the old identity reads from the start, `whole`
  True); `test_a_failed_replace_leaves_the_log` (monkeypatch `os.replace` to raise -> the log reads as before, no
  temporary file left).
- [ ] Implement; run the store tests; commit `feat(storage): a log rewritten whole under its lock`.

### Task 2: What a compaction keeps

**Files:** Create `flotilla/lane/compact.py`; Test `tests/test_lane_compact.py`.

**Interfaces:** Produces `plan(records: list[dict], now: datetime) -> tuple[list[dict], list[dict]]` - (kept,
archived): kept are the original records of live bookings and of bookings held, waited or ended within
`SERVED_WINDOW`, plus one rewritten record per other ended booking an estimate reads (spec, "What a compaction
keeps"); archived are all original records not kept as written.

- [ ] Failing tests: `test_a_booking_held_for_a_week_is_kept` (held 8 days ago, never released -> its records kept
  verbatim, `fold(kept)` shows it held); `test_a_waiter_is_kept`; `test_the_fair_share_reads_the_same_day`
  (`book.last_served` over `fold(kept)` equals over `fold(records)`); `test_every_usable_estimate_is_unchanged`
  (history of steps with >= ENOUGH samples equal before and after, on a synthetic journal with 12 runs of one step and
  one-offs); `test_a_one_off_older_than_a_week_is_archived` (its booking not in kept, its records in archived);
  `test_a_recent_one_off_is_kept`; `test_kept_and_archived_account_for_every_record` (every original record is in kept
  or archived); `test_the_rewritten_record_folds_to_the_same_figures` (seconds, ended, project, verdict equal).
- [ ] Implement; run; commit `feat(lane): what a compaction of the journal keeps`.

### Task 3: Compacting on write, with the archive

**Files:** Modify `flotilla/lane/compact.py` (`compact`), `flotilla/lane/book.py` (`Lanes._write`); Test
`tests/test_lane_compact.py`.

**Interfaces:** Produces `compact(tx, root: Path, now: datetime) -> bool` (True when it rewrote); `THRESHOLD`,
`GROWTH`, `STALE` constants; the record of the last compaction at `<root>/lane.compacted` (`{"at", "bytes"}`); the
archive at `<root>/archive/<YYYY-MM>.jsonl.gz`.

- [ ] Failing tests: `test_a_journal_over_the_threshold_is_compacted_on_write` (a `Lanes` over a journal of > 2 MB;
  one `enqueue` -> the file is under 2 MB and holds the new booking); `test_a_journal_under_the_threshold_is_left`;
  `test_a_journal_that_stays_large_is_not_compacted_on_every_write` (after one compaction left N bytes, a write at
  < 1.5 N does not rewrite); `test_the_archive_is_written_before_the_rename` (monkeypatch `os.replace` to raise ->
  journal unchanged, archive holds the records, the append stands); `test_a_cached_reader_reads_the_new_journal`
  (a second `Lanes` that read before -> after compaction its `bookings()` equals a fresh fold).
- [ ] Implement; run `tests/test_lane_*.py`; commit `feat(lane): the journal is compacted when it passes 2 MB`.

### Task 4: Docs and version

**Files:** `CHANGELOG.md` (0.26.1), `pyproject.toml`, `.claude-plugin/plugin.json`, `flotilla/__init__.py`, `README.md`
pins, `TODO.md` (the item's compaction half to Done, the polling half stays open).

- [ ] Edit; full suite; commit `docs: 0.26.1 - the lane journal is compacted`.
