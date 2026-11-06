# The rig queue: runs that keep apart, a line that moves itself (design)

Date: 2026-11-03. Sources:
- alpha feedback items 3, 4, 6 and 12 (`~/alpha-keep/flotilla-feedback-2026-11-01.md`);
- TODO "The rig" (four items).

The person's ruling (2026-11-03): option B, built in two parts. Part A comes first, and part B follows it.

## 1. What went wrong

During alpha rig sessions s73-s76, seats sent their heavy runs (frame captures, audio, benchmarks) to one rented
machine.

**Runs overlapped.** `rig run` packs runs. Two runs from different seats share a machine at once, and more share it
when the machine has room. For CPU work this saves money. For a run that measures frame timing, fps or captured sound,
a neighbour skews the result (item 6).

**The orchestrator became the queue.** To keep runs apart, seats asked the orchestrator for a slot, and it sent each
one "go" by hand when the previous run ended. Twice it forgot:
- on s76 the paid machine sat idle ~40 min;
- on s74 it sat idle 15 min while the next seat was still running its own checks.

Across s73-s76 the idle gaps came to about an hour (item 12).

**Two smaller losses.**
- A run stopped on purpose brought nothing back. Its `--get` paths are fetched only when a run ends on its own, so two
  finished clips of three were lost, 28 min of machine time (item 3).
- A machine still draining from the closed session counted against the machine ceiling. The new session's first run
  was refused, and the machine sat idle 15-18 min right after the person paid (item 4).

## 2. What exists today

A line already exists, and it is tied to a process:
- A `rig run` that cannot start is a waiting run in the journal, tied to its live process.
- It waits up to `--wait` seconds (default 900), polling every 10 s.
- `Rig._verdict` decides which waiting run of the session starts on a ready machine, using packing (`packing.choose`):
  - memory and GPU bind from the first run;
  - cores bind only past two runs;
  - a run waiting 10 min becomes senior;
  - among the runs that fit, the longest goes first.
- `flotilla rig` lists the running and waiting runs, and why each waiting run waits.

Three things are missing:
- a way to say "this run must be alone";
- a wait long enough to stand in line through a session;
- a way for anyone, the orchestrator included, to see the order and when their turn comes.

## 3. Part A (0.27.0): runs that keep apart, a line that moves itself

### A1. Exclusive runs

- A run is either **exclusive** or **shared**.
- `rig run --exclusive` and `rig run --shared` choose per run.
- The project profile sets the default: `[rig] exclusive = true | false` (default `false`, which is today's behaviour).
  - Since the profile is read from the repository, a project of frame and audio work sets it once.
- The journal's run record carries `exclusive` (bool). A record without the field is shared.
- The rules sit in `Rig._verdict`, ahead of packing, so the `would_start` check without the lock and `start_run` inside
  it give the same answer:
  1. A machine with an exclusive run on it starts nothing else. The others wait: `j11 runs alone on m3 (exclusive)`.
  2. An exclusive run starts only on a machine with nothing on it: `exclusive: waits for m3 to empty (j9 runs on it)`.
  3. While an exclusive run waits, every run that arrived after it waits too, on every machine of the session, until
     the exclusive run has started: `held for exclusive j11, which has not started yet`.
     - Runs that arrived before it are packed as today.
     - Exclusive runs among themselves go by arrival.
     - This is the rule that keeps an exclusive run from starving behind a stream of shared runs.
  4. Otherwise packing decides, exactly as today.
- An exclusive run is never measured beside another, so its GPU figure is always the alone figure.

### A2. Seats queue themselves

- `rig run --wait` defaults to the time left in the open session, not 900 s.
  - A seat calls `rig run` once, in the background, when its run is ready. The run stands in line and starts by itself
    when its turn comes.
  - An explicit `--wait` still lowers it.
- At queue time, and again whenever its place changes, the waiting run prints:
  - its place;
  - the runs ahead of it;
  - by estimates, when it starts and when it ends (section A3);
  - when the session ends.
- When by estimates the run would end after the session ends, it says so before it starts:
  `by estimates j12 ends around 15:10, after session s80 ends at 15:00 - lower its ceiling with --max, or run it in the next session`.
  - It does not refuse: the estimate may be wrong, and the run's own ceiling already bounds it.
- Templates:
  - **main and minor:** queue through `rig run` and never ask the orchestrator for a turn; a run whose result is a
    measurement in real time (fps, frame timing, audio capture, a benchmark) is exclusive - tests, builds and frames
    rendered one by one are not (the person, 2026-11-03).
  - **orchestrator:** the rig's order is flotilla's; never send "go" letters; read the new watch item as flotilla's own
    handover having broken.

### A3. The order on screen

`flotilla rig` lists waiting runs in the session's line order, numbered. Each line shows:
- exclusive or shared;
- its estimate;
- by estimates, when it starts;
- a "does not fit the session" mark when it would end after the session ends.

Running runs show how long they have run against their estimate.

**Expected start (my call).** The model is approximate, and the output says so:
- Runs are served one after another on the session's machines, in line order. Each machine is a lane: the next run
  takes the lane that frees first.
- What a running run has left is its estimate minus the time it has run, at least one minute.
- A run never measured counts at its ceiling. The run record keeps the ceiling it was queued with, as `ceiling`
  seconds, and the output says how many runs were counted that way.
- With no machine up yet, the output adds "plus the minutes to raise a machine" and no number.
- Packing two shared runs at once only makes runs start sooner, so the output reads "around HH:MM or sooner".

### A4. A watch item when the handover breaks

The orchestrator's watch gets one new item, from `rig/surface.items`:
- **When:** an open session has a machine that has been ready and idle for 2 min or more, while live runs of that
  session are waiting.
- **What it says:** `rig: machine m3 idle 4 min while j11, j12 wait - read flotilla rig for why`.
- **Why it can appear at all:** flotilla starts a waiting run within 10 s of the machine freeing. So this item means
  something broke, for example a waiting process that stopped polling, or a machine that cannot be read. It is the
  dropped ball alpha asked to be told about.
- **Key:** `rig:idle:<machine>`, so the item is counted once per machine.

### A5. Two small fixes

**A stopped run brings back what it made.**
- **When:** a `rig run` is signalled (by `rig stop jN` or the seat) after its command started.
- **What happens:** its `--get` paths are fetched before the run's directory is dropped.
  - This is best effort, capped at 120 s (`GET_ON_STOP`).
  - It prints either what came back or why nothing did.
- A run stopped before its command started has nothing to bring back.

**A machine draining from a closed session stops counting against the ceiling once its destroy is sent.**
- The machine ceiling counts every live machine. A draining machine still counts, unless both are true:
  - its session is no longer open;
  - its destroy has been sent at least once (`attempts >= 1`).
- `STUCK` always counts.
- **The price:** until the service drops the old instance from its listing (one reaper pass), two machines may be
  billed, one of them already told to die.
- **Rejected:** handing the draining machine over to the new session. Its destroy has already been sent in the same
  reaper pass that drained it, and a destroy cannot be taken back.

### Out of part A

The following come with part B:
- bookings;
- readiness;
- reordering;
- turn letters;
- carry-over.

The `[rig] node` pin and counting a rig green for receipts keep their own designs (TODO).

## 4. Part B (0.28.0): a line you can stand in before you are ready

Alpha asked for more than A gives:
- a seat books early, while its checks still run, and keeps its place;
- the orchestrator reorders;
- flotilla tells a seat when its turn comes;
- the line survives the session's end.

The mechanism follows. The detailed plan for B is written after A ships, against what A's line shows in the field.

### B1. Bookings

- `rig book --est <min> --why "<what>" [--exclusive|--shared]` puts the calling seat in the open session's line
  without a process: a run record in a new state `booked`.
- A seat holds at most one booking per session.
- With no session open, the booking waits for the next one. The rig request for the orchestrator is recorded as today.
- A `rig run` from a seat that holds a booking takes the booking's place in the line, instead of joining at the tail.
- `rig unbook` leaves the line.

### B2. Readiness

- A booking is not ready until `rig ready`. A `rig run` from that seat counts as ready too.
- The line skips a booking that is not ready, but keeps its place.
- `rig pass` lets the next ready entry go first, once, without losing the place.

### B3. Turn and handover

When a machine frees and the first ready entry in the line is a booking with no live run, flotilla sends that seat a
letter, copied to the orchestrator: `the rig is yours: machine m3, next after you j14, the session ends 15:00, your estimate 20 min`.
- **Delivery:** through the peer-message path flotilla already uses for seats.
- **To verify while planning B:** which path it is, and that a background seat's session reads it.
- **Grace:** a seat told its turn that has not started within 5 min moves back one place. The next one is told, and the
  orchestrator gets a note.
- **Out of turn:** a `rig run` from a seat with no booking, while bookings wait, joins the tail as today. It does not
  jump ahead of booked seats.

### B4. Reordering

The orchestrator alone may reorder:
- `rig queue move jN --before jM` reorders the line;
- `rig queue drop jN --why` drops an entry from it.

Each move is a journal record with who made it and why.

### B5. Session end and carry-over

- At close, bookings that were never served carry over in order to the next session that opens.
- Waiting runs still end with `session ended`, as today, but their bookings stay.
- When the first machine of the next session comes up, its first ready seat is told.
- This also covers item 4's gap from the other side: the line is waiting for a machine before anyone calls.

### B6. Records and the watch

- Each finished run records its estimate beside its actual minutes, so `flotilla rig` can show how far off the
  estimates run.
- The A4 watch item widens to "idle with N booked and ready".

## 5. Calls I made (for the person to check)

1. **Exclusive is opt-in.** The default stays shared, and a profile turns it on for the whole project. Today's CPU
   fleets keep their packing.
2. **An exclusive run holds everything that arrived after it, on every machine of the session.** It does not hold per
   machine. With one machine, the usual case, the two are the same.
3. **A seat's `rig run` waits for the whole session by default.** A long-waiting background call is the queue.
4. **A run that does not fit the session is warned, not refused.**
5. **The ceiling rule:** a draining machine whose destroy was sent no longer blocks a new session's machine. This can
   cost one pass of overlap billing.
