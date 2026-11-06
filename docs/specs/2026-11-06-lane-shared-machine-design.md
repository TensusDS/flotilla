# Sharing the machine by value, not by prediction: memory admission, priorities and freezing (design)

Date: 2026-11-06.

The person's rulings, from one brainstorm on 2026-11-06 (each is his, not a builder's call):
- Admitting a run must not depend on predicting its weight from the text of its command.
- The CPU is shared by the kernel. Memory is admitted by the lane.
- Runs are judged by the value they have built up, not by the resources they take.
- A run that has worked 15 minutes is immortal: nothing freezes it.
- A younger run has a priority by a formula with weights. When room is needed, the lowest one is frozen, and only
  for a newcomer whose own priority is higher.
- "Taken off" means **frozen**, never killed. A frozen run comes back with a higher priority.
- 20 % of the memory is kept for light runs.

- `lane_capacity` is retired: every run works by parallel running with priorities.
- On macOS freezing is on and recommended: a stop by signal, back with a raised priority.

It stands instead of the light-run rule of `2026-11-04-lane-light-runs-design.md` (section 2: light = measured
≤ 60 s and ≤ 2 cores) and of the heavy count `lane_capacity` as the lane's main gate. Everything else of the lane
stays: the journal, bookings, receipts, the outright gates (CI on this machine, memory floor, disk), fair share
between projects, ceilings, measurement.

Sources:
- the field test of two demo fleets on one lane (TODO, section "From the field test of two demo fleets on one lane",
  and `/home/max/var/flotest-2026-11-06/observations.md`);
- the research report `/home/max/var/lane-research/reports/Память при параллельных прогонах.md`, with its notes;
- the model of this design, `lane_sim/` in the brainstorm's scratch directory. Every number marked *(model)* below
  comes from it; it is a throwaway spike, not shipped.

## 1. What goes wrong today

The lane decides whether a run may start by **predicting** it: a run is light when its own history, keyed by the
text of its command, says it is short and small. Everything else is heavy and waits for one of `lane_capacity`
places.

Measured against that:
- **Most runs have no history to predict from.** In the lane journal, 40 % of twosuns' runs and 61 % of
  void-lanes-online's ran a command (after normalisation) that never repeated three times. The lane met each of them
  with the fixed prior "4 cores, 2 GB".
- **The field test (2026-11-06).** Two fleets on one lane at capacity 1, with 7 of 8 cores and 9 GB free all along:
  - 13 runs under a minute waited ~76 min in all for ~100 s of work;
  - heavy runs waited up to 13 min each;
  - every heavy run was a 200 s run of about one core, heavy only because it was long.
- **The same command costs different amounts.** `cargo build` on a warm cache and after `cargo clean` share a text
  and differ tenfold. A prediction from the text cannot see that.
- **Prior art.** No system the research looked at shares memory by predicted size. Bazel (2026), Buck2, GNU parallel
  and YARN all admit by the memory that is free now, and undo a wrong admission by suspending the youngest run.

## 2. The rule

### 2.1 CPU: the kernel shares it

The lane does not book cores. Runs share them as the kernel schedules them, with equal weights.
- A single-threaded run never takes more than one core.
- A run with eight workers takes what the others leave and gives it back within milliseconds.

So a heavy run gets more CPU by itself. No queue is needed for that.

### 2.2 Memory: admitted by what is free now

A run starts when its memory fits what is free **now**:
- the machine's available memory;
- less the profile's memory floor (unchanged);
- less a **reservation for runs started in the last N seconds**, whose memory has not shown yet. Bazel's window is
  5 s; the right N for test runs is the builder's to measure (section 9).

Two more rules:
- **Allow one.** The first run on an empty machine always starts, even when its memory may not fit (Bazel's rule).
  Otherwise a run larger than the machine would never run.
- **Memory still unknown when the run asks:** take its peak from its history when there is one (any step of the
  ladder, the `prog` step and the project prior included), else the profile's default for a run. A wrong guess here
  costs one freeze, not a forbidden start.

**The memory the lane can give** (the base of every share below; a builder's question, settled here as the design's
call): the machine's available memory, less the floor, plus what the lane's own holders hold now. It is
recomputed at each decision. In other words, what the lane would have if all its runs ended.
- It moves when a peer session or a foreign run takes memory, so the reserve of 2.4 is never a reserve on paper
  only.
- Because it moves, the heavy mark of 2.3 is sticky (below).

### 2.3 Heavy and light, by memory

A run is **heavy** when its memory reaches `heavy_memory`, a share of the memory the lane can give. A run is light
otherwise.

**Sticky** (the design's call): a run that has been heavy once stays heavy until it ends. A base that moves must not
turn a run light and back, because who may freeze whom (2.7) depends on it.
- **Before the start:** by its history's peak.
- **During the run:** by what it holds now. A run that grows past the mark becomes heavy while it runs.

The share is the builder's to set; the model drew heavy runs at 1.5-6 GB and light ones at 0.1-1.5 GB on 9 GB.

Duration plays no part in it. A 200 s run of one core and 100 MB is light.

### 2.4 Heavy runs: how many at once

- **CPU bound.** A heavy run is admitted while the heavy runs' combined parallelism stays within `lane_cpu_factor`
  (default 1.5) times the machine's cores. Parallelism comes from the run's measured peak cores (section 4.4); a run
  never measured counts as half the machine's cores.
- **Memory bound.** The heavy runs together never take more than **80 %** of the memory the lane can give. The other
  **20 % is kept for light runs**, the person's choice from the model's trade-off. The empty-machine rule of 2.2
  still lets one heavy run past it.

What the model gives for these rules on the server (8 cores, 9 GB for runs, 30 runs of which 5 heavy) *(model)*:

| rule | all 30 done | light runs' wait to start, 95th percentile |
|---|---|---|
| today: one heavy at a time | 201 min | 4.0 min |
| these rules, reserve 0 % | 149 min | 8.6 min |
| these rules, reserve 10 % | 155 min | 6.8 min |
| **these rules, reserve 20 %** | **159-163 min** | **4.9 min** |

The work itself needs 136 min of the machine.

On 16 cores the same rules gave 94-95 min against 196 for today's rule and 117 for a fixed two heavy runs. The bound
by cores scales; a fixed count does not.

### 2.5 Priority

Every run younger than the immortality mark has a priority from 0 to 1:

```
P = 0.2 · type + 0.3 · min(1, minutes since it first asked / 10) + 0.5 · min(1, minutes it has run / 15)
```

- **type**: the run's purpose, from the booking:

  | purpose | type |
  |---|---|
  | push receipt | 1.0 |
  | handover receipt | 0.7 |
  | a reviewer's run | 0.5 |
  | any other run | 0.3 |

  The model cannot choose these values: in it a run's purpose does not depend on its size. They are set by meaning,
  and the weight 0.2 keeps them from outranking work already done.

  Where the purpose comes from (the design's call):
  - a receipt's from its purpose, which `booked()` already receives;
  - "a reviewer's run" from the session's post: `who` is matched against the posts' name patterns, as the roster
    does. No new flag that sessions would have to remember.
  - Anything unmatched is 0.3.
- **Minutes since it first asked**: from the booking's first `waiting` record. They keep counting while the run waits,
  runs or is frozen.
- **Minutes it has run**: run time added up across thaws.

The weights are the person's draft (0.2 / 0.3 / 0.5). The model put them among the best trade-offs once the wait
term saturates at **10 minutes**: at 30 or 60 minutes, waiting light runs gained too slowly. *(model)*

The priority is computed when a decision needs it, inside the journal's lock. `flotilla lane` prints it each time it
is asked.

### 2.6 Immortality

A run that has run **15 minutes** in all is immortal.
- Nothing freezes it.
- Its memory counts as taken until it ends.

The model found 10, 15 and 20 minutes equally good. 15 is the person's number. *(model)*

### 2.7 Freezing

When the highest waiter (by P) does not fit, the lane may freeze running runs to make room. A run is a candidate
when all of these hold:
- it is not immortal;
- it has run at least **1 minute** since it last started or thawed (the grace);
- its P is lower than the waiter's (margin 0; 0.05 is as good in the model);
- **the waiter is light, or the candidate is light.** A heavy waiter never freezes a heavy run.

Candidates are taken lowest P first until the waiter fits. If all of them together do not make room, nothing is
frozen and the waiter waits. Nothing is frozen either when the swap cannot take the candidates' memory (section 4.3).

Why a heavy run never freezes a heavy one *(model, server, 200 queues)*:

| | a heavy may freeze a heavy | it may not |
|---|---|---|
| all 30 done | 162.7 min | 162.8 min |
| heavy runs frozen | 10 | 1 |
| minutes a frozen heavy run stood, 95th percentile | 59 | 2 |

The swap only decided which of two heavy runs waited. It cost nothing to forbid it, and in life a swap of gigabytes
and a run's own timeouts would have paid for it.

### 2.7a Who may start ahead of a waiter that does not fit

This is the design's call. It is Slurm's backfill, kept within each class, and it is what the model did, apart from
the class rule.
- **Light past heavy.** A lower light waiter may start when the higher waiter is heavy and does not fit. Light runs
  live in the reserve and in what is left, and they end in minutes.
- **No heavy past heavy.** A lower heavy waiter may never start ahead of a higher heavy one that does not fit. The
  heavy room that frees next is the higher one's. That keeps a large run from being passed for ever, and it is the
  only guard where freezing is off (4.2, 4.3).
- **Light past light.** A lower light waiter may start ahead of a higher light one that does not fit. A light run is
  at most the light mark in size, so the room it waits for comes back within minutes.

### 2.8 Thawing

A frozen run goes back into the queue as a waiter. It keeps its first-asked time and its run time, so it already
outranks any later run of the same purpose: the higher priority the person asked for comes with no bonus of its own.
A separate bonus is what would let two runs freeze each other in turn.

It thaws when it is the highest waiter that fits, like any start, and then has its grace minute.

In the model, with the reserve at 20 % and no heavy-freezes-heavy:
- 0.5 runs were frozen per queue of 30;
- no run was frozen twice;
- a frozen light run stood at most 6.5 minutes. *(model)*

### 2.9 How many runs at once

At most **10** runs hold the lane at a time (the person's cap), so freezing and sharing never cut the machine into
slivers. The model found 10, 16 and no cap equal at 30 runs.

### 2.10 Fair share between projects

`lane_share = "fair"` stays as the tie-break between waiters whose priorities are within the margin: the project
served least recently goes first.

## 3. Non-goals and anti-goals

**Non-goals:**
- killing and retrying a run (the person: "not ok for long runs");
- predicting a run's weight from its command for admission. History still feeds estimates, the start-time forecast
  and the default memory of 2.2;
- GPUs, more than one machine.

**Anti-goals** (the design fails if any of these happens, even when it "works"):
- an immortal run is frozen;
- a heavy run is frozen to let another heavy run in;
- a light run waits minutes behind heavy runs while the reserve stands free;
- a run that went red after a freeze is reported as plain red (section 4.5);
- two runs freeze each other in turn.

## 4. How it is carried out

The lane has no daemon. Each waiting process decides for itself inside the journal's lock, and each holder is a live
process (`lane run`, a receipt) running its command in its own process group. The design keeps that.

### 4.1 Who freezes whom

1. The waiter that decides a freeze writes, in the same transaction, a **freeze order** to each victim's booking:
   a journal record, state `freezing`.
2. Each holder's wrapper watches its own booking. It already waits on its command; it gains a thread that wakes on
   journal writes, as `_nap` does.
3. Seeing the order, the wrapper freezes its own command (4.2), pauses its ceiling, and writes `frozen`.
4. The waiter starts once the memory shows free, not on the record alone.

**A victim that does not answer** within a bound (a wrapper of an older flotilla, a dead one) is not frozen. The
order lapses; the waiter waits; `flotilla lane` says which booking did not answer.

### 4.2 Freezing a command

- **Linux with a user systemd** (the user manager has the memory controller delegated; checked on this machine):
  - the wrapper starts its command in a transient scope of its own (`systemd-run --user --scope`, with equal
    `CPUWeight`);
  - freezing writes the scope's `cgroup.freeze`, then asks `memory.reclaim` for **what the waiter needs plus a
    margin, not the run's whole memory**;
  - thawing writes `cgroup.freeze` back.
  - Measured on this machine (2026-11-06, kernel 7.0, as the unprivileged user; probe in the brainstorm's scratch,
    `freeze_probe/`), on a 1 GB process in a transient user scope that keeps touching its memory:

    | step | time |
    |---|---|
    | freeze | 0.02 s; nothing printed while frozen |
    | `memory.reclaim` of 1.2 GB | 2.2-2.6 s; 1029 MB resident became 2 MB, 1027 MB in swap. The write ends in `EAGAIN` when it reclaimed less than asked, which is not an error |
    | thaw | at once |
    | its memory back from swap | ~13 s for the first full pass over 1 GB, then 0.05 s a pass as before |

  - So a freeze costs about **15 s per gigabyte**, nearly all of it on the way back: the kernel brings pages in one
    fault at a time. That is why only the needed amount is reclaimed. Freezing a 4 GB run for a 0.5 GB light one
    then costs seconds, not a minute on the thaw.
  - Earlier the research measured the same on 204 MB (4 MB resident, 211 MB in swap after reclaim).
- **macOS** (the person's ruling, 2026-11-06: freezing is recommended there too):
  - freezing is **on by default**, and it is a stop: `SIGSTOP` to the run's process group; thawing is `SIGCONT`;
  - the stopped run comes back with its raised priority exactly as on Linux (2.8);
  - the difference is memory. A stopped process gives nothing back by itself; macOS compresses or pages it out under
    its own pressure, when it chooses. So the waiter is admitted only when the memory **shows** free, never on the
    stop alone. If it never shows free, the waiter waits as it would without freezing, and the stopped run is let
    go again after a bound (left to the builder) rather than held for nothing;
  - `lane_freeze = false` turns it off; `flotilla doctor` recommends keeping it on.
- **Linux without a user systemd** (no scope to put the run in): the same stop by signal as on macOS, with the same
  rule that only memory shown free admits the waiter.
- Where freezing is off, the rest of the design stands: admission by free memory, the priority order, the reserve,
  the caps. `flotilla lane` says that freezing is off and why.

### 4.3 The swap budget

Freezing a run moves its memory to swap. A freeze is decided only when the free swap, less the memory of runs
already frozen, holds the victims' memory with a margin. Otherwise the waiter waits.
- On this machine: 12 GB of swap, about 8.5 GB free when measured.
- A machine with no swap never freezes.

### 4.4 What the lane measures in addition

Each run's peak cores, sampled in the loop that already samples its group's memory each second. Today only the
average cores over the whole run is kept, which hides a minute at eight cores inside a long run at one. It feeds the
parallelism of 2.4.

The deciding waiter reads holders' current memory from the process table, as it reads foreign runs today. Nothing
more is written to the journal each second.

### 4.5 A run's own clock while frozen

A frozen test's own timeouts (pytest-timeout, Playwright, keepalives to a live server) keep counting, and may fail it
after the thaw. The research names this risk; nobody has measured it.

Rules:
- the lane's ceiling does not count frozen time;
- a run that ends red after a freeze says so in its verdict ("red after a freeze of 3 min"), and a receipt records it
  beside the tier, so a seat reruns before it reads the red as the code's;
- a profile may mark a tier `freeze = false` (a browser suite, a tier holding a live server). Such a tier is never a
  victim: it is treated as immortal from its start, and its memory counts as heavy.

### 4.6 Bookings the lane cannot freeze

- **A booking taken by hand** (`lane take`) keeps its meaning: "I am about to run something heavy that the lane
  does not run" (the design's call).
  - It waits and is admitted as a heavy run of the machine's full cores and of the heavy share of memory: it takes
    the heavy room, as it took a place before.
  - Held, it is immortal: there is no command to freeze.
  - Its real use is seen as foreign memory and CPU.
- **Foreign runs** (computing, unbooked): not the lane's. Their memory is simply not free, which admission by free
  memory sees without asking.
- **A run inside a booking**: part of the outer one, frozen with it.

## 5. What `flotilla lane` prints

Per holder and waiter:
- its priority and the three terms;
- heavy or light, and why (memory now, or history's peak);
- immortal from when;
- frozen since when, by whose order.

Per waiter, what it waits for, by name:
- "memory: 1.2 GB needed, 0.4 GB free, reserve 1.8 GB for light runs";
- "heavy runs' cores: 12 of 12";
- "no candidate to freeze: the two holders are immortal";
- "swap cannot take 2.1 GB".

The budget line says what is used and what is left ("4.5 of 12 cores in use"), not two bare numbers (TODO item from
the field test).

## 6. Settings

| key (machine.toml, the person's) | default | meaning |
|---|---|---|
| `lane_cpu_factor` | 1.5 | heavy runs' combined parallelism, times the cores |
| `lane_light_reserve` | 0.2 | the memory heavy runs leave to light ones |
| `lane_immortal_minutes` | 15 | run time after which a run is never frozen |
| `lane_freeze` | on (Linux: freeze and reclaim; macOS and Linux without a user systemd: stop by signal) | off: admission and order only |
| `lane_max_runs` | 10 | runs holding the lane at once |

The weights, the type values, the 10-minute wait saturation, the 1-minute grace and the margin are constants in code,
not settings. Exposing them would invite tuning by hand of numbers whose effect only a model or a field test shows.

**`lane_capacity` is retired** (the person's ruling, 2026-11-06: "let every run work by parallel running with
priorities").
- This design does not read it. Under it, the value 1 on this machine would have meant today's 201 minutes against
  159 *(model)*.
- What it guarded against is covered by 2.4. It was set to 1 after two software-rendered smoke tiers at once drove
  the load to 17-26 on 8 cores and turned five handovers red from load (twosuns, 2026-11-01). Under 2.4 two tiers of
  8 measured cores make 16, past 1.5 × 8 = 12, so the second waits.
- The person's machine.toml keeps the key; flotilla never edits that file. `flotilla lane` and `flotilla doctor` say
  once that the key no longer has an effect.
- An older flotilla still reads it. While an older flotilla's waiter is live, the lane falls back to the count as it
  does today (section 7).

## 7. Compatibility

- **An older flotilla's waiter** (journal rule below this one) is live: the lane falls back to today's count for
  everyone, as it does for the light path (`older_waiting`), and freezes nothing.
  - "Today's count" means `lane_capacity` read as today. It is the one place the retired key is still read.
  - A booking of an older flotilla is never sent a freeze order.
- **The journal gains** a freeze order's fields (by whom, when), a run's run time across thaws, and the freeze state.
  - Checked by the builder check in 0.29's code: `fold` reads every state but `held` and `waiting` as ended, and
    `holders()` keeps only `held`. A new `frozen` state would read as released to an older flotilla, which would
    then admit over a booking whose memory is in swap.
  - So (the design's call) **a freezing or frozen booking stays a `held` record**, with a field `frozen` (`ordered`,
    `frozen`, or absent). An older flotilla sees it held and counts its place; it ignores the field, as `fold` ignores
    fields it does not know.
- **Receipts and evidence:** unchanged, except the "red after a freeze" note of 4.5.

## 8. How it is tested

- **The policy as a pure function**, from bookings and a machine reading to a decision (start, freeze whom, wait for
  what), with tests for each rule of section 2 and each anti-goal of section 3, including:
  - a heavy waiter with only heavy candidates;
  - an immortal run;
  - a waiter with exactly the victim's priority;
  - no swap;
  - a victim that does not answer.
- **The freezing seam on Linux:** a real transient scope frozen and reclaimed, a child process in it, its memory read
  before and after; skipped where the user manager has no memory controller.
- **The model's numbers as a regression check** of the pure policy: the same 30-run queues replayed through the real
  decision function must land within a stated band of the model's results (the builder decides whether the model
  becomes a test fixture).
- **A field test** like 2026-11-06, two fleets on one lane, compared on the same analyses:
  - short runs that waited;
  - heavy waits on an idle machine;
  - freezes and their duration;
  - red verdicts after a freeze.

## 9. Open questions

For the builder (measure, then choose):
- the reservation window N for just-started runs (2.2) and the default memory of a never-measured run;
- `heavy_memory`, the mark between light and heavy (2.3);
- the swap margin (4.3);
- how long a victim has to answer a freeze order (4.1);
- how often a frozen test fails on its own timeouts after the thaw.

## 10. What the model assumed

- Freezing and thawing are instant and free.
- The kernel shares CPU perfectly.
- A run's memory is known from its start.
- Run sizes are drawn by hand (heavy 60-360 core-minutes on 4-8 cores, light 0.2-6 on 1-2).

On a 4-core / 4 GB machine it drew runs of up to 6 GB. There light runs waited 33-39 min under every rule: a run
larger than the memory can only run alone. That is a limit of the model's mix, not a finding about the rule.

The real numbers come from the field test of section 8.
