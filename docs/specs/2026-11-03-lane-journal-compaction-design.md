# The lane journal is compacted (0.26.1) - design

Status: approved by the person in chat on 2026-11-03 (snapshot + archive + a new journal, threshold 2 MB). Source:
TODO "The lane journal is never compacted" (gamma and alpha, 2026-11-01..02).

## Why

The lane journal (`<state>/lane/lane.jsonl`) only grows: on 2026-11-03 it held 25 624 lines, 8.35 MB, 8690 bookings
over 37 days. Since 0.24.2 a waiter parses only the lines appended since its last poll, but every read still reads and
hashes the whole file, and a new waiter, `flotilla lane` and every seat on an older flotilla fold all of it.

## What a compaction keeps

A booking's state is the fold of its events, so the journal cannot be cut by line count: a booking taken by hand stays
held until it is released, however old its first line is, and cutting that line would make the lane think the machine
free. A compaction keeps by meaning:

- **every live booking** (held or waiting), all its events as written;
- **every booking held, waited or ended in the last day** (`book.SERVED_WINDOW`), all its events: the fair share reads
  when each project was served;
- **every ended booking that an estimate reads**, rewritten as one record holding only what estimates read (state,
  time, project, ladder, ran, seconds, peak_mb, cores, busy, verdict, cut). Its ladder and tiers keep only the steps in
  which it is among the last `estimate.KEEP` samples within the 30-day window, and a step below `estimate.ENOUGH`
  samples whose newest is older than 7 days is dropped: no estimate reads it, and a command run once a week ago is
  estimated, if it comes again, by the coarser steps of its ladder.

Everything else - and every original line of a rewritten booking - goes to the archive.

Measured on a copy of the journal of 2026-11-03: **2856 records, 1.47 MB**, from 25 624 and 8.35 MB; every estimate
usable that day has the same samples before and after. (Keeping whole bookings by meaning kept 5.52 MB: 4484 of the
5747 steps are one-off exact commands with one sample.)

## How

- **When:** a writer already holds the lane's lock. After its append, if the journal is over **2 MB** and over 1.5
  times what the last compaction left (recorded beside the journal), it compacts, in the same lock. No schedule. The
  growth condition keeps a journal whose kept core nears the threshold from compacting on every write.
- **The new journal is written beside the old and replaces it by rename.** Readers already detect a replaced file (a
  new inode) and read it from the start; a reader mid-read keeps the old file whole.
- **Only ordinary journal records**, never a new kind: seats on older flotillas read the same file and fold it as
  before. A rewritten booking folds to the same state, project, figures and end as its original lines.
- **The archive** is `<state>/lane/archive/<YYYY-MM>.jsonl.gz`, appended (gzip members) before the rename, so a crash
  between the two leaves history doubled, never lost. Nothing reads it; it is there for the field analyses the
  journal fed (median waits, run lengths).
- A compaction that fails leaves the journal as it was and says so on stderr; the append it followed stands.

## Non-goals

- Waiters still poll every 15 s; waiting on the lock file is the other half of the TODO item, a later step.
- No command to read the archive.

## Compatibility

A plugin version change, not a schema change: older seats read the compacted journal; an older seat never compacts.
