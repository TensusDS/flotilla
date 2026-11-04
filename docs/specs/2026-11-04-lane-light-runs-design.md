# One heavy run and light runs beside it: the lane's short-run path (design)

Date: 2026-11-04.

The person's ruling (2026-11-03): option B of the lane fork, "one heavy, and the small ones on what is left". It
stands instead of stages 2-3 of `2026-10-05-lane-resource-admission-design.md`, budget admission and overtaking for
every run. Those stages stay as the later option if the field needs more.

Sources:
- twosuns items 13 and 16;
- void-lanes-online's lane report;
- TODO "The lane".

## 1. What goes wrong today

The lane admits by a count. `lane_capacity` runs hold it at a time, and every other run waits in arrival order.
That fails both ways.

**At capacity 1, short runs wait behind long ones.**
- Five void-lanes-online runs of 21-38 s waited about 2.5 h behind 11-minute receipts (item 16, 11-02).
- Runs under a minute waited a median 30.6 min for a median 6.8 s of work.
- A seat that runs one test file to check a fix waits as long as a full receipt does.

**At capacity 2, heavy runs choke each other.**
- Two receipts' software-rendered smoke tiers at once put a load of 17-26 on 8 cores.
- Five handovers went red from load, not code (item 13, 11-01).
- The only remedy was capacity 1, which brings the first failure back.

## 2. The rule

Every booking is **light** or **heavy**.

- **Light:** its own measured history says it is short and small. That means, from the lane's estimate (stage 1, by
  the signature ladder) answered by the command's own step - `exact` or `norm`, never `prog` (every run of the same
  program) and never a prior (the final review: a full suite with a new flag was light by single-file runs of its
  program):
  - duration ≤ 60 s (`LIGHT_SECONDS`);
  - cores ≤ 2 (`LIGHT_CORES`);
  - a known peak memory.
- **Light receipt:** every tier that will run is measured, and the sums meet the same bounds.
- **Heavy:** everything else, including a run never measured, a booking taken by hand, and a run with only a project
  prior. A run is light only by evidence, never by hope.

### Heavy runs

Heavy runs keep today's rule exactly:
- **Capacity:** `lane_capacity` heavy holders at a time. Foreign computing runs (unbooked) take heavy places as they
  do today.
- **Order:** arrival order among heavy waiters.
- **Fair share:** `lane_share = "fair"` chooses among heavy waiters.
- **Light holders do not count against `lane_capacity`.** A heavy run never waits for a light one: a light run ends
  within about a minute, and holding a heavy run back for it would let a stream of light runs starve it.

### Light runs

Light runs take what the heavy runs leave. A light waiter is admitted now when all four hold:
1. **No light ahead:** no earlier live light waiter is still waiting. Light runs go in arrival order among
   themselves.
2. **Cores:** its estimated cores fit the cores left. The total is `lane_cpu_factor` (default 1.5) times the
   machine's cores. From it come off:
   - every live holder's estimated cores (heavy, light, and a heavy run with no measured cores counted as all the
     machine's cores);
   - each foreign computing run, counted as 4 cores (the fixed prior).
3. **Memory:** its estimated peak fits the memory left, read in the journal's lock. "Left" means the available memory
   less the floor, less the estimated peak of every live holder. This counts a holder's peak in full, although part
   of it is already in use. That is conservative on purpose: memory refuses by killing.
4. **No older waiter:** no live waiter of the lane waits under an older rule. A flotilla before this one knows nothing
   of light runs, sees a light holder as a taken place, and could wait for ever behind a stream of them. While such a
   waiter is live, every run - light ones too - is admitted by count and arrival, as today, and light holders count
   as places (the final review found that refusing light runs outright instead left a light waiter and an older
   waiter each waiting for the other, and the lane empty).

There is no count of light runs. The cores and the memory bound them.

### An example

1. An 11-minute receipt holds the lane: heavy, 7 cores, 1.7 GB.
2. A seat runs one test file: measured at 4 s, 1 core, 300 MB. 7 + 1 = 8 of 12 cores, memory to spare, so it starts
   at once and ends 4 s later.
3. A second receipt arrives. It is heavy and waits for the first, as today.
4. A third test file arrives while the second receipt waits. It is light, so it does not wait for the receipt; it
   only needs room beside the running one.

## 3. Seniority survives an expired wait

A booking whose `--wait` ran out keeps its place when it is made again, under three conditions:
- the same session (`who`) makes it;
- for the same exact signature;
- within 10 minutes of the expiry.

Its place is the expired booking's place, carried in a new `place` field. Without this, a heavy run re-queues at the
tail each time and can expire for ever (void-lanes-online: six bookings expired at 1800 s).

A flotilla before this one ignores `place` and sees the new booking at the tail. That is safe: it only makes the
older waiter more patient.

## 4. Handoff without the 15-second poll

**Today:** a waiter reads the whole machine every 15 s. That is the process table, CPU samples and CI.

**From now on:**
- A waiter checks the journal's size and modification time once a second.
- It reads the machine only when the journal changed or 15 s passed, with up to 1 s of random jitter.
- A release then reaches the next waiter in 1-2 s instead of a median of 9 s.

## 5. What the person sees

`flotilla lane`:
- Each holder and waiter is marked `light` or `heavy`, with its estimate.
- A budget line: cores used of the total, memory left after the reservations, and the floor.
- **A light waiter** says what it lacks, e.g. `needs 2 cores, 1.5 left`, or `waits behind light j812`.
- **A heavy waiter** says where it stands and when it starts by estimates, e.g.
  `2nd of the heavy runs; by estimates it starts around 14:32 or sooner`.
  - The time is worked out like the rig's line: heavy holders' remaining estimates, then the heavy waiters ahead.
  - A run never measured counts at its ceiling.

## 6. Mixed versions

The rule number becomes 3. Light admission needs:
- the booking to wait under rule 3;
- no live waiter under an older rule (2.4).

A fleet still on 0.21 (the user-scope install twosuns runs) keeps the lane as it is today for as long as one of its
seats waits.

## 7. Out of this release

- **Counting a seat's run outside the lane as its booking.** The person's call of 2026-11-01. It needs process
  attribution and its own design.
- **`lane bump`** for the orchestrator. Deferred: the short-run path removes the case it was asked for.
- **Mode 2 `slots`.**
- **A tier's declared cost, and the "starved" verdict** (item 13). Capacity 1 for heavy runs removes the cause.

## 8. Calls I made

1. Light means ≤ 60 s, ≤ 2 cores, a known peak, and a measured match only.
2. A heavy run never waits for light ones.
3. The memory reservation counts holders' peaks in full.
4. A foreign computing run counts as 4 cores.
5. Lights are off while a waiter of an older flotilla is live.
6. There is no machine.toml switch. The path is on for every machine running this version, because the person chose
   it as the lane's behaviour, not as a mode.
