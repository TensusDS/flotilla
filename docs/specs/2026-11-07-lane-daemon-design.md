# One scheduler per machine: the lane daemon, soft memory ceilings and freezing as the second line (design)

Date: 2026-11-07.

It amends `2026-11-06-lane-shared-machine-design.md` (below: "the 0.30 spec"). Section 4 of that spec says "the lane
has no daemon"; this design reverses it. Everything of the 0.30 spec not named here stays: the priority formula
(2.5), immortality (2.6), the class rules (2.7a), the run cap (2.9), fair share (2.10), the swap budget (4.3), a run's
own clock while frozen (4.5), the freeze mechanics of a wrapper (4.1 steps 2-3, 4.2), the anti-goals (3).

## 1. What went wrong (field test, 2026-11-07)

Two demo fleets on one machine (8 cores, 15.6 GB, swap 12 GB), plugin 0.30.1 plus the review fixes (218d41b), one
batch each of three heavy tasks (2.5-3.5 GB, 4-6 cores, 240 s) and two light ones (200 MB, 20 s). The mains raised
helper seats on their own, so five heavy receipts met at once. Record: `/home/user/var/demo-2026-11-07/
observations.md`.

- Every heavy run was admitted on a light estimate: the load tier's command text is the same for light and heavy
  tasks, so its history said 92-216 MB. Two went in on the **light** path and were marked heavy only after they grew.
  Admission obeyed rule 2.2 each time; the estimate was wrong, as the 0.30 spec expects it to be ("a wrong guess here
  costs one freeze, not a forbidden start").
- **The freeze never came.** The lane printed "heavy runs: 8.6 of 7.7 GB"; the machine had 0.5 GB free, swap 10.4
  of 12.3 GB used, load 46. The journal holds 22 bookings and 0 freeze orders. A freeze is ordered only by a waiter,
  for its own start (`policy.decide(runs, machine, mine)`); with nobody waiting nothing looks at the machine as a
  whole. The one light waiter that came found "no candidate to freeze": two runs in their first minute, two ranked
  higher. With immortality at 3 minutes (the test's setting) a run was freezable between minute 1 and minute 3 only.
- Under that load `claude agents --json` did not answer within 3 s, so both orchestrators' `flotilla watch` exited 2:
  the fleets went blind exactly when the machine was tight.

The person's reading (2026-11-07): sessions should only put their runs in a queue; one place, strict code, sees the
whole queue, ranks it and acts. Not a session: a decision taken in seconds by a model, under a load that blinds it,
is the wrong tool.

## 2. The person's rulings (2026-11-07)

1. **One daemon per machine**, shared by every fleet and every lone session on it. The first that needs it raises
   it; it lives while the lane has bookings and leaves after 10 minutes with none.
2. **Any lane client raises it when it is absent** - an orchestrator at its start and on each pass, and also a
   `lane run`, a receipt, a `lane take` of a session with no fleet.
3. **When it cannot be raised**, the client falls back (section 6): runs already going go on, frozen ones thaw by
   their own guards, and a new run starts only into an empty lane.
4. **Code and daemon stay matched through the existing update mechanism** (`2026-11-04-plugin-updates-design.md`):
   the daemon runs from the newest copy installed on the machine and is replaced when a newer one appears.
5. **The first-minute grace does not protect a run from the overload rule** (it still applies to a freeze ordered
   for a waiter, 0.30 spec 2.7). The run that just ballooned is usually the newcomer.
6. **Heaviness does not choose the victim; priority does** (the person asked how the victim is chosen; answer
   below, 4.4).
7. **When the swap cannot take a frozen run's memory, it is frozen anyway**, without reclaim: it stops growing, its
   memory stays in RAM.
8. **When everything over the line is immortal or pinned**, nothing is squeezed or frozen, nothing new starts, and
   `flotilla lane` says "the machine is over by N MB and nothing can be frozen".
9. **Memory is held by a soft ceiling per run, watched per group by the daemon; freezing is the second line.**
   Measured before the ruling (section 3).

Never: `memory.max` (the kernel kills at it: a kill and retry, which the person refused for long runs); a model in
any decision of the lane.

## 3. Why a soft ceiling, and why it needs a watch (bench, 2026-11-07)

One run of 3 GB for 60 s of work, in its own user systemd scope, three ways. "Hot" touches its whole block in a loop
(as the demo's load tier does); "cold" holds 3 GB and works on 200 MB of it (what a test suite holding data it no
longer reads looks like - an assumption, not yet measured on a real suite). Rate is work per second of active time
against the same profile with no ceiling. Raw: `/home/user/var/demo-2026-11-07/bench/results.jsonl`.

| profile | condition | rate | rate in the 15 s after a thaw | lowest MemAvailable | swap out / in |
|---|---|---|---|---|---|
| hot | none | 100 % | - | 10.1 GB | 0 / 0 |
| hot | `memory.high` 1 GB | **0.2 %** | - | 11.8 GB | 5.1 / 3.1 GB |
| hot | freeze 30 s, reclaim 2 GB | 52 % | 0.7 % | 10.1 GB | 2.1 / 2.1 GB |
| cold | none | 100 % | - | 10.1 GB | 0 / 0 |
| cold | `memory.high` 1 GB | **92 %** | - | **12.1 GB** | 2.2 / 0.2 GB |
| cold | freeze 30 s, reclaim 2 GB | 96 % | 89 % | 10.1 GB | 2.0 / 0.2 GB |

- On a cold run a ceiling is nearly free: it never stops, and the machine gets 2 GB back.
- On a hot run a ceiling is the worst choice: it stands still and churns 8 GB of swap a minute. A freeze stands
  still quietly and pays 15-20 s of faults after the thaw.
- The kernel tells the two apart per group - but not by pressure, as this design first assumed. Re-run with the
  group read every second (2026-11-07), past the fill, under a 1 GB ceiling:

  | profile | pressure "some avg10" p10 / p50 / p90 | its own pages read back from swap | `memory.events` high a second |
  |---|---|---|---|
  | hot | 9.5-13.1 / 10.8-14.5 / 18.6-19.2 % | 43-49 MB/s | 90-109 |
  | cold | 0.2 / 1.9 / 16.9-17.4 % | 0 | 0 |

  A cold run pushing its pages out reads as much pressure as a hot one churning, so no pressure mark sits between
  them. What a hot run does and a cold one never does is read its own pages back: `memory.stat`
  `workingset_refault_anon`. *(Builder's change after the bench; the person has not ruled on it yet.)*
- A real suite (this plugin's own lane tests, 737 of them, peak 227 MB) under a ceiling of half its peak: 95.2 s
  against 87.7 s without (8.5 % slower), pressure 0.1 % (p50), 50 MB read back in all. The cold assumption holds for
  it; a suite that holds gigabytes is not measured yet.

Hence the shape: a ceiling for every run, the daemon reads each group, and a run held at its ceiling that reads its
own pages back, with no room to give it, is frozen instead.

## 4. The daemon

### 4.1 What it is

A process started from the plugin copy: `flotilla lane daemon run`, detached, its own process group, no terminal.
One per machine, held by an exclusive lock in the lane's state directory (`lane/daemon.lock`); a second start finds
the lock taken and exits 0 saying who holds it. It writes a heartbeat (`lane/daemon.json`: pid, start mark, plugin
copy path, release, protocol version, last tick) every tick.

It is the **only writer of decisions**. Clients write requests (ask, release, refuse) to the journal as today;
the daemon writes admits, ceilings it set, freeze orders, thaws. Each decision is a journal record, so `lane`,
the analyses and the replay tests read one history.

### 4.2 The tick

Every 2 s, and at once when the journal is written (the wake the wrappers already use), the daemon:

1. reads the journal, the machine (`MemAvailable`, swap, the floor), and each held run's group: `memory.current`,
   `memory.pressure`, liveness of its pid and mark;
2. calls one pure function, `policy.tick(runs, machine, now) -> Actions` - starts, ceilings, freeze orders, thaws,
   and a line of why for each run that does not move;
3. writes the actions: admits and orders to the journal in one transaction; ceilings straight to each scope's
   `memory.high` (a file the user owns - no systemd call); then the heartbeat.

`tick` is pure and replayable: the 2026-11-07 minutes 01:54-01:58 are a replay test (section 8).

### 4.3 What `tick` decides, in order

**The floor and the gates of a shared daemon** *(builder's call)*. The floor is per project today
(`[lane] memory_floor_mb`); the daemon uses **the largest floor among the projects with a live booking** - the most
cautious one, since one machine is being protected. The per-project gates (CI on this machine, disk, foreign runs by
the project's patterns, unknown readings) stay with the **client**: it asks only when they pass, and re-checks them
while it waits, as today.

**Free room**, used everywhere below: `room = MemAvailable - floor - Σ(ceiling - used) of capped runs - reservations`
- what is left once every ceiling's unused headroom is counted as promised.

**a. Starts.** Waiters in priority order, by the 0.30 rules unchanged: free memory now (2.2, with "free" = room), the
class rules (2.7a), heavy cores (2.4), the run cap (2.9), fair share (2.10), and the freeze for a higher waiter (2.7,
its grace included). A started run gets a ceiling at once (b).

**b. Ceilings** (runs in a scope only; section 4.6 for the rest) *(builder's call, from the bench)*:
- **at the start**: `memory.high = max(estimate, 256 MB) + slack`, slack = max(256 MB, 10 % of it). The estimate is
  the one admission used (the 0.30 reservation);
- **growth**: a run is **at its ceiling** when its group's `memory.events` "high" counter rose since the last tick
  (the kernel throttled it - an instant signal, unlike the 10 s pressure average). A run at its ceiling gets more
  **whenever there is room**, hot or cold: up to doubling its ceiling per tick, in priority order, until room is
  gone. There is no reason to throttle while the machine has room - this is what lets a mispredicted run grow
  (256 MB to 4 GB in four ticks) and still be held the moment the room ends;
- the ceiling never moves otherwise: it is not reset to `used + slack` each tick, so a run cannot creep;
- an **immortal** or **pinned** run (`lane take`) has no ceiling (`max`): a ceiling below what it would use is a
  hidden freeze, and nothing freezes it.

**c. Pressed without room** *(builder's call)*. A run at its ceiling, with no room to give, reading its own pages
back from swap at `REFAULT_MB_S` or more for `PRESSED_S` (it is hot: it churns swap and does no work - the bench's
worst case; section 3), counts
as a **waiter for memory** with its own P. Among it and the controllable runs of lower P, the **lowest** is frozen
(4.4's order): the pressed run itself when it is the lowest, else a lower run that gives it room. This holds whether
or not the machine is over its floor: a ceiling keeps `MemAvailable` up by pushing pages out, so a churning run may
never let the machine count as overloaded.

**d. Overload.** When `MemAvailable` has stayed below the floor for `OVER_S` seconds:
1. take the **victim** (4.4);
2. **squeeze** it: `memory.high = max(256 MB, used - deficit)` - its cold pages go to swap, it keeps running. The
   squeeze is clamped to the swap budget (0.30 spec 4.3: swap at most 80 % used after it); when the swap can take
   nothing, skip to 3 at once;
3. if it is pressed under the squeeze (c), or the swap could not take the squeeze, **freeze** it (order to its
   wrapper, 0.30 spec 4.1-4.2), with reclaim within the swap budget, or without reclaim when the swap cannot take it
   (ruling 7);
4. one victim at a time; the next only when the first one's memory shows on the machine or it has been frozen.

The swap budget limits **every** move that pushes pages out - ceilings lowered, squeezes, reclaims - not freezes
only: otherwise a squeeze would put its run at the top of systemd-oomd's list, a kill by a side door.

**e. Thaws and lifts** *(builder's call)*. A frozen run thaws, in priority order, when room covers **its growth
headroom** - its slack, and for a run frozen while pressed, its ceiling's doubling - plus, for a run frozen with its
pages reclaimed, the pages it will fault back; and it has stood frozen at least 30 s. It comes back under its
ceiling, so it cannot simply regrow over the line. A run frozen by overload or pressure **twice** thaws only when
room covers its recorded peak. Squeezed runs get their ceiling back to `used + slack` before any new run starts.
An overload or pressure freeze is bounded by the freeze ceiling (10 min, `lane_freeze_ceiling_minutes`); the 120 s
let-go of 0.30.1 belongs to a waiter's order and does not apply (it has no waiter).

**e. Overload with nothing to take** (ruling 8): no start, no squeeze, the "over by N MB, nothing can be frozen"
line.

### 4.4 Who is the victim

Candidates: held runs in a scope or stoppable by signal, not immortal, not pinned, not let go (a run that was let go
is never frozen again), not under a refused order. Order: **lowest priority P first** (the formula of 2.5). The
newest run is almost always lowest - its run-time term is near 0 - so the victim is almost always the run that just
grew; on 2026-11-07 at 01:56 it would have been km-to-miles (P 0.18, 3.5 GB), and its 3.5 GB alone covered the
deficit of about 1 GB.

The person's ruling (2026-11-07): within 0.05 of P, the run that grew most over the last minute goes first.
Without it, two young runs of near-equal P are told apart by booking order, which says nothing about which one hurt
the machine.

### 4.5 Waiters

A waiter no longer decides. It writes its ask and waits for the daemon's admit (or the fallback, section 6),
printing the daemon's why as it does today. Its wrapper, once admitted, works as in 0.30: it puts the command in a
scope, watches its own booking for freeze orders, guards the ceiling of a freeze (10 min, `lane_freeze_ceiling_minutes`)
and thaws by itself when the daemon or the watch is gone.

### 4.6 What the daemon cannot hold

- **No scope** (signal backend; macOS; a scope refused): no ceiling. Overload goes straight to freezing (a stop by
  signal), as in 0.30.
- **A booking of an older flotilla** that decides for itself (0.30 and before) *(builder's call)*: bookings made
  under the daemon carry a new rule number (`book.RULE` 5); one with a lower number is an older client's. The daemon
  keeps deciding for rule-5 bookings and never admits an older one - it counts its memory, sets a ceiling only when
  the run is in a scope, and its own freeze orders to it lapse unanswered (0.30 spec 4.1). An older waiter still
  admits itself by its own `decide` and may write freeze orders; a rule-5 wrapper refuses every order that is not
  the daemon's (4.8), so an older client can never freeze a run the daemon manages - it waits instead. Mixed
  machines last only until the update reaches every install (ruling 4). `flotilla lane` names such bookings.
- **A foreign run** (not booked): counted in `MemAvailable`, never touched, as today.

### 4.7 Lifecycle and versions

- **Raised by** `flotilla lane daemon ensure`: start it when the lock is free, or when it is stuck - no heartbeat
  for 10 s, or no finished tick for 60 s, or a last tick dated more than 60 s ahead of the clock *(builder's call,
  after the final review)*: signal its process group TERM,
  then KILL after 5 s, then start. The heartbeat is written by a thread of its own every 2 s, so one slow tick
  (a `systemctl` under load answers within 10 s) is not read as a dead daemon; the thread stops beating once ticks
  have stopped for 60 s, so a loop wedged on the journal's lock is not read as a live one. A signal goes only to a
  process proven to be the daemon: it holds the lock by its own descriptors (and by the kernel's lock list where
  that names a holder), leads its own process group, and runs `<copy>/bin/flotilla lane daemon run`. The daemon is
  started through a launcher that forks it into a session of its own and exits, so it is nobody's child.
  Orchestrators run `ensure` at start and on each pass (post text); every lane client runs it before asking
  (ruling 2).
- **Leaves** after 10 minutes with no booking, held or waiting, and lifts every ceiling it set on the way out (sets
  `max`), so no run outlives its scheduler under a cap.
- **Versions** (ruling 4): the daemon records its release and protocol version. A client from a newer copy that finds
  an older daemon replaces it (TERM, wait for the lock, start its own); a client from an older copy that finds a
  newer daemon speaking a protocol it knows works with it; one that does not know the protocol says "the lane
  daemon speaks protocol N, this copy M: restart this session" and takes the fallback. An update on disk under a
  running daemon is noticed by the daemon itself at its next tick (the copy path's release changed): it hands over
  the same way. `flotilla doctor` reports the daemon's release beside the installs.
- **Ceilings outlive a dead daemon** (they are files of the scope): a client that finds the daemon dead and cannot
  raise it lifts the ceilings it can see before taking the fallback, and a wrapper lifts its own scope's ceiling
  once the daemon is gone - no heartbeat for 10 s, or one dated more than 10 s ahead of the clock, so a
  handover, whose last beat is a second old, is not an absence; the same test thaws a run the daemon froze. A
  heartbeat whose times are no numbers (NaN, infinity) is no heartbeat. A daemon that exits on SIGTERM - replaced, or the machine going down -
  does not lift: its successor adopts each group's ceiling as it stands *(builder's call)*.
- **What a daemon keeps, and what a restart forgets** *(builder's call, after the final review)*: the ceilings it
  writes are its own decisions, kept in memory - never a value a journal line names, since any session writes the
  journal. A restarted daemon adopts each scope's `memory.high` as it finds it, and holds a scoped run with none at
  its use plus the slack. It does not know which runs were squeezed: adopting that flag from the journal would let
  one line hold back every start, so a squeezed run recovers through the growth rule instead.

### 4.8 Which orders a wrapper obeys *(builder's call, security)*

Any session can write the journal, so today a wrapper obeys an order only when it names a live waiter that the rules
allow (`watch._order_holds`). An overload or pressure order has no waiter, and rulings 5 and 6 lift two of those
rules, so the check changes:
- the order must carry the **daemon's pid and start mark**, and they must match `daemon.json` and the process that
  holds `daemon.lock` now - and, since a pid and a mark are public, a **seal**: HMAC-SHA256 over everything the
  order says, with a key the daemon makes when it takes the lock (`daemon.key`, this user's only, never a link). A
  line written by anyone else is refused, as a forged waiter is today (security review of the first build). The
  seal covers the reclaim too, and an order given before the run's last start or thaw is refused. Its bound, said
  plainly: the key is this user's file, so the seal stops a writer of the journal alone - an older flotilla, a client
  gone wrong - and not a session that reads the user's files;
- **the bound against a session that reads the key** (the person's ruling, 2026-11-07: an honest bound). Such a
  session could freeze a run by signal without the lane at all. What the lane guarantees against it rests only on
  what the wrapper reads for itself, never on the journal: it is never frozen when immortal by its own count of run
  time since it was admitted (frozen time left out), or pinned (a pinned run has no watch at all), or let go in this
  process; an overload order holds only while `MemAvailable`, read by the wrapper, is below the floor plus 512 MB
  (a floor a booking names counts up to a quarter of the machine); a pressure order names the pressed run, and holds
  only while that run's group, looked at for a second by the wrapper itself, is held at its ceiling and reads its
  pages back at `REFAULT_MB_S` or more - or is held at its ceiling while the swap, read by the wrapper, is within
  the 2 GB reserve of its budget, where the daemon freezes at once; and an order given before this wrapper's own
  last thaw is a replay. So a forger can freeze a mortal run - whatever the order's cause - only while the machine's
  own readings agree, each freeze for at most the freeze ceiling (10 min), and again after a thaw while they still
  agree; never an immortal or pinned run, and nothing is ever killed. The number of freezes is not capped: the daemon
  itself freezes a run more than once (4.3 e). **Who** is the right victim the wrapper does not
  judge: a second copy of the daemon's choice, fed from the journal, drifted from the daemon's (it refused true
  orders) and could be fed (two reviews, 2026-11-07);
- a waiter's order (2.7) is written by the daemon too, so it passes the same check; the waiter it names is checked
  as today.

### 4.9 What `flotilla lane` prints

The daemon line first: running (pid, release, last tick N s ago) / not running and why. Then per held run, beside
P and memory: its ceiling and its pressure (`ceiling 1.2 GB, pressure 3 %`), squeezed or pressed when so, and for
the machine "over by N MB for M s" when overload holds.

## 5. The orchestrator post

Its start and every pass run `flotilla lane daemon ensure` and say so in one line only when it did something
(started, replaced, could not raise and why). It decides nothing about runs.

## 6. Fallback (no daemon can be raised)

The client says why (`lane daemon not running: <reason>`), lifts ceilings it can see, and:
- a run already going goes on; a frozen one thaws by its wrapper's guards (dead daemon = gone watcher, 0.30.1);
- a new run starts only into an **empty** lane (Allow one, 0.30 spec 2.2), so nothing is admitted blind beside a
  run nobody watches;
- every ask retries `ensure` while it waits.

## 7. Settings

Builder's numbers, each awaits the field test; none is a person's setting until the test says it should be:
`REFAULT_MB_S` 10 MB/s (pages read back that make a held run pressed; the bench: hot 43-49, cold 0, a real suite
about 0.5), `PRESSED` 5 % (pressure a wrapper needs to see in the pressed run's group; hot 9.5-19, cold 0.2-1.9 once
filled), `PRESSED_S` 10 s, `OVER_S` 10 s, slack max(256 MB, 10 %), a swap reserve of 2 GB below which a held run is
frozen at once (the replay of section 8 took the swap to 88 % without it, 81.5 % with it), tick 2 s, idle exit
10 min, heartbeat stale 10 s (and as far ahead of the clock, which steps back now and then), no tick for 60 s - or
a tick dated more than 60 s ahead - is stuck.
`lane_freeze` = off now means: ceilings still, no freezing (overload ends at squeeze).

## 8. How it is tested

- `policy.tick` by unit tests (each rule of 4.3-4.4, every ruling of section 2) and by **replay of the 2026-11-07
  minutes 01:54-01:58** *(builder's call)*: the record has bookings, starts and machine samples every ~16 s but no
  per-run group readings, so the replay builds each run's memory curve from its load spec (megabytes, start, hot) -
  constructed from known inputs, not invented - and asserts the outcome, not the second: every run's ceiling held
  below the machine's floor line, at most the lowest run squeezed or frozen, no immortal touched, `MemAvailable`
  never below the floor for more than `OVER_S` + two ticks. The next field run's sampler records each run's
  `memory.current`, `memory.events` and `memory.pressure`, so later replays are fed by readings.
- The daemon by process tests: one instance under two starts; a stuck heartbeat replaced; a newer copy replacing an
  older; an unknown protocol taking the fallback; idle exit lifting ceilings; a dead daemon's ceilings lifted by a
  client.
- On Linux CI with user systemd: one real scope, a ceiling written and read back, pressure read from it. Skipped
  where there is no user manager, saying so.
- The bench of section 3 re-run with pressure read every second, to set `PRESSED`; and once on a real suite (the
  plugin's own) under a ceiling of half its peak, to check the "cold" assumption.
- Field test: batch 1 again, the load tier taught a `touch = "hot" | "cold"` key so both branches of 4.3 are met.

## 9. Non-goals

- predicting weight from a command (unchanged);
- killing and retrying a run (unchanged; `memory.max` is never set);
- a session or a model taking any lane decision;
- the fleet findings of the same test that are not the lane's (census time-out, `lane stop` of a waiting booking,
  the reviewer's detached tip, the "12 cores" wording, a guard on the reviewer's own temp directory, a seat stuck on
  a start-up dialog): they are listed in the test record and taken after this.

## 10. Open, for the person

The person approved this design and confirmed 4.4's tie-break by growth (2026-11-07). The person's rulings on the
builder's changes since (2026-11-07):

- pressed is decided by pages read back from swap (`REFAULT_MB_S`), not by pressure (sections 3, 4.3 c, 7): accepted;
- the replay of section 8 holds the swap under 85 %, not the 80 % budget - what held runs push between a look and
  their freeze is theirs (81.5 % on the field record); the daemon's own moves keep to 80 %: accepted;
- `FLOTILLA_LANE_DAEMON=off`, which the test suite needs, stays documented as the person's switch to the careful
  lane (no daemon: a new run only into an empty lane): accepted;
- against a session that reads the daemon's key, an honest bound rather than a second copy of the daemon's choice
  in the wrapper (4.8): chosen.

Builder's changes that await the person:

- after the reviews: the heartbeat thread and the 60 s bound on ticks; a wrapper lifting its own ceiling; a
  restarted daemon adopting each group's ceiling and forgetting which runs were squeezed; a daemon on SIGTERM
  leaving its ceilings to its successor (section 4.7); a booking's floor believed up to a quarter of the machine
  (4.3); a wrapper counting its own run time for immortality (4.8);
- the daemon is gone for a wrapper only when its heartbeat is missing, not a number, older than 10 s, or dated
  more than 10 s ahead of the clock (a wall clock stepping back is the honest case of a beat ahead; `ensure` alone
  reads the beat one way and the tick both ways, so for one beat after such a step the two may disagree) -
  not when its lock is briefly free in a handover (4.7). Under load a replacement can still take more than 10 s, and
  wrappers then lift and thaw; the successor holds the runs again at their use plus the slack;
- **open: a cap on freezes.** Each freeze lasts at most the freeze ceiling (10 min), but their number is not capped:
  the daemon may freeze a run again after a thaw, and so may a session that reads the key, for as long as the
  machine's readings agree - frozen time does not count towards immortality, so such a run can be held frozen most
  of the time (each freeze ending before the ceiling, `lane_freeze_ceiling_minutes`, 10 by default: one that
  reaches it lets the run go for good in that process). A cap on the time a booking may stand frozen in all (say 10 min) would bound both; it would also stop
  the daemon from freezing that run again once spent. The person's to decide.
