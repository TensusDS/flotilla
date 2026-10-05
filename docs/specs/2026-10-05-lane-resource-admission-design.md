# Lane admission by resources - design

Date: 2026-10-05. Status: draft for the person's review. Research behind it, in the session's scratchpad and summarised
here where it matters: the lane's journal on the field machine (29.09-04.10: 1,341 granted bookings, 44.7 h held,
146 h waited by them), a study of what holds the lane, and a review of the pitfalls of backfilling (EASY backfilling,
Lifka 1995 and Mu'alem & Feitelson 2001; estimate inaccuracy, Tsafrir, Etsion & Feitelson 2007; Slurm's backfill
scheduler; requests vs. limits in Kubernetes; Bazel's local resources).

## 1. What it is for

The lane books the machine for long runs: test tiers before a handover, e2e, builds, screenshot jobs. Today it admits
by a count - live holders under `lane_capacity` (1 unless set), less one slot for each foreign run computing - and
serves strictly in order of arrival. On a mid-range machine (8 cores, 16 GB) that made it the fleet's bottleneck:

- 64% of bookings held the lane under a minute (5 h in all) and waited 84 h for it; 10% of bookings (5 min and over)
  held 56% of the time. A two-second test of one file waited three minutes behind a half-hour screenshot job.
- The machine sat in two regimes: a receipt (`vitest run`, default workers) used 7 of 8 cores, while most other runs
  used 1-3 and left 5-7 idle. Memory never refused a booking in the week (8.6-9.7 GB available).
- 7.9 h in the week the lane was free and could not be given: one unbooked `vitest` or headless browser took the only
  slot.

A stopgap `lane_capacity = 2` is set on the field machine. This design replaces the count with a budget.

**The person's scenario.** A screenshot job holds the lane (3 cores, 1 GB, half an hour). A receipt arrives (7 cores,
1.7 GB, 40 s): 10 of 12 cores, memory to spare - it runs at once. A seat's test of one file arrives (1 core, 0.3 GB,
3 s): it runs at once too. A second receipt arrives and does not fit; it becomes the head of the queue, and the lane
holds a place for it when the first receipt ends. A later one-file test overtakes it only if it fits now and does not
move the head's start. `flotilla lane` says, for each waiter, which resource it waits for and behind whom.

## 2. Measuring every run

**What is recorded.** `lane run` and receipts run under the sampler of `lane/peak.py` (the peak resident memory of
the run's process group and every descendant, sampled about once a second, backed by the largest child the kernel
reaped). Around the run the lane takes `getrusage(RUSAGE_CHILDREN)`. On release the journal's `released` event
carries `seconds`, `peak_mb`, `cores` and `verdict` (green, red, killed, ceiling). A booking swept because its process
is gone is recorded as `cut`, with no measurement, never dropped from the history.

**Cores are demand, not what was received.** CPU seconds over wall seconds measures what a run got: under contention
it falls, the lane admits more, and contention grows. A run's `cores` sample is recorded with the machine's busy share
during the run (from `/proc/stat`); estimates use only samples taken while the machine was not saturated (busy under
85%), and take their maximum, not their mean.

**The signature of a run** is what joins runs of one command into a history. The waiting event carries the full
command (the note stays a note, cut to 80 characters as today) and a ladder of signatures, from exact to coarse:

1. exact: the command with the seat's tree path replaced by a placeholder, matched on the whole path component (so
   `twosuns-main-1` and `twosuns-main-12` never fold into one);
2. normalised: also hashes (hex runs of 8+), numbers in paths and ports, temporary paths and redirections replaced;
3. program: the program and its script or subcommand (`vitest run`, `node shoot.mjs`, `vite build`), flags that set
   parallelism kept (`--maxWorkers=1` stays);
4. project: every run of the project.

A receipt's booking is the tiers it will run, not a tier: before booking, `receipts.to_run` names the tiers a green
reuse does not cover, and the booking's estimate is the sum of their durations and the maximum of their peaks and
cores; each tier keeps its own history under the signature `tier:<name>`.

**The estimate** comes from the most exact step of the ladder with at least three measurements in the last 30 days:
duration = median of green runs (a red or killed run that ended early says nothing about how long a full run takes; a
run stopped at the ceiling counts as "at least" its time), cores = maximum of unsaturated samples, peak = maximum. An
estimate drifts with the code (the twosuns receipt tier went from 10.7 s to 43.8 s in six days), so only the last 10
measurements of a step count. Each estimate names its source: "5 runs of this command", "12 runs of `vitest run`",
"project prior".

**A run with no history** gets the project's prior - the 90th percentile of cores and peak over the project's runs -
not the whole machine: by the journal 26-39% of runs are first seen even after normalising, and giving each the whole
machine would hold 33-58% of the lane's time single-file, worse than the stopgap. A project with no runs at all gets a
fixed prior (4 cores, 2 GB). After 20 s of running, a run's reservation is recomputed from its live sample (its RSS
and its group's CPU), so a wrong prior is corrected while the run goes.

**Hints.** `lane run --cores N --mem MB` may only make a run heavier than its estimate; a lighter claim is ignored
unless measured. `--light` is honoured inside fixed bounds (1.5 cores, 500 MB, 60 s): the run is sampled, and one
that breaks a bound gets its full reservation at once, and that session's light hints are not honoured for 24 hours.
Fleets are run by models, and a hint that costs nothing would be learned as a way past the queue.

**Stage 1 changes no admission.** It records and shows; the next stages decide on what it gathered.

## 3. Admission

**The budget**, read on every decision:

- **CPU: 1.5 x the cores** (`machine.toml` `lane_cpu_factor`, default 1.5), filled by the holders' estimated cores and
  the live cores of foreign unbooked runs. A foreign run's cores are its whole process tree's CPU over the sample (a
  `vitest` with seven workers is seven cores, not one), and the sum is checked against the machine's own idle share:
  when `/proc/stat` says the machine is busier than the budget believes, the busier figure wins. CPU over-subscription
  only slows runs; the factor keeps that bounded.
- **Memory, strictly:** available memory (container-aware, `core/resources.py`) less the floor, less the reservation:
  for each holder, its estimated peak less the memory its tree holds now. Memory refuses by killing, so the memory
  part of the decision is read fresh **inside the journal's lock**: `/proc/meminfo` and the holders' current RSS are
  read under the lock, never from a reading taken before it. (Today the reading comes from before the lock; two
  waiters can admit themselves against the same free memory while a receipt is still growing its 1.7 GB.) The
  costly questions - foreign runs' CPU over 2 s, CI through `gh` - stay outside the lock.
- Outright blocks are unchanged: CI running on this machine, memory under the floor, a question that cannot be
  answered.
- `lane_capacity`, when set, stays a ceiling on the number of runs. Unset, there is no count, only the budget.

**Order: the queue with overtaking (EASY backfilling).**

1. The earliest live waiter is the head. If it fits the budget, it goes.
2. If not, its **shadow**: the moment enough holders will have ended, by their estimates, for it to fit; and the
   **leftover**: what the budget will hold beside it at that moment.
3. A later waiter goes now when it fits now and either ends before the shadow, or fits in the leftover beside the head
   and every waiter that overtook before it (all live holders count, not only the head's).
4. A holder past `max(2 x its estimate, its estimate + 30 s)` has an unknown end (time in the journal has one-second
   resolution, and a third of the runs under 3 s overrun twice their estimate). An unknown end only makes the head's
   shadow unknown; then only runs that fit the leftover may overtake, and none on the strength of ending first.
5. **Starvation guard by the shadow, not the clock:** an overtaking is refused when the head's shadow computed with
   it - and with every holder that overtook the same head - would be later than the shadow computed without them.
   A holder that overruns moves the shadow too, but that is the holder's length, not the overtakers': only the
   difference the overtakers make counts. Waiting a long time behind a long holder is not starving, so a wall-clock
   guard would return the queue to strict order exactly when overtaking matters.

Each waiter decides for itself, under the lock, by this one rule and the journal's state; the `held` event records
the path it took (`head`, `before-shadow`, `beside-head`) and the budget figures it saw, so a wrong admission can be
read back.

**Seniority survives an expired wait.** A booking that expired (its `--wait` ran out) and is made again by the same
session for the same exact signature within 10 minutes keeps its place: its position is the expired booking's.
Otherwise a heavy run re-queues at the tail each time and can expire for ever.

**Fleets on mixed versions.** Seats run the installed plugin, which may be older. Every booking records the version
of the rule it waits under. Budget admission and overtaking apply only while every live booking waits under this
rule; with an older booking live, the lane keeps counting slots, and `lane_capacity` must not be removed before every
project's fleet runs the new version. `flotilla doctor` names a stale `lane_capacity` once nothing older is live.

**Other existing behaviour**: a booking taken by hand (`lane take`) has no process to sample and holds its hint or
its project prior until released; a booking nested in another (`FLOTILLA_LANE_BOOKING`) is part of its parent and
never measured twice; the ceiling of `run.py` caps a run's unknown end - the estimate is never longer than the
ceiling.

## 4. Handoff without the 15-second poll

A waiter checks the journal's modification time once a second (a `stat`); it reads the machine fully only when the
journal changed or 15 seconds passed, with a random jitter of up to 1 s so waiters do not stampede together. The
median handoff in the field was 9 s; it should fall to 1-2 s, about 2 h a week of a free but ungiven lane.

## 5. What the person sees

- `flotilla lane`: the budget (`cores 10 of 12 (2 foreign), memory 6.1 GB free, 1.2 GB reserved, floor 1.5 GB`);
  each holder's estimate, its source and expected end; each waiter's reason: the resource it lacks, how much, and
  who is ahead (`needs 7 cores, 2 free; head of the queue is the receipt of main session 3, its place comes in ~25 s`).
- A refusal after `--wait` names the same.
- `lane run --light`, `--cores`, `--mem` documented in the README's lane section, with the bounds and the penalty.

## 6. Fleet size

The `test runs` cap of `fleet size` (design of 2026-10-04) treated the lane as if only handover tiers held it - 6.5%
of its time in the field - and took the tier's time from onboarding's first measurement (141.8 s against 32-44 s
today; `measure_once` never updates). It now reads the lane's journal: the hours a day the lane was held and waited
for over the last 7 days, and each tier's estimate from section 2. Onboarding's measurement is used only where the
journal has nothing.

## 7. Stages - each a release

1. **Measure**: release events with seconds, peak, cores and busy share; full commands and the signature ladder in
   waiting events; estimates and their sources in `flotilla lane`; receipts' per-tier history. No admission changes.
2. **Budget**: CPU and memory admission with the reservation read under the lock, the project prior and its live
   correction, hints with bounds, version gating, handoff without the poll.
3. **Overtaking**: shadow, leftover, unknown ends, the shadow guard, seniority across expiry, reasons for waiting.
4. **Fleet size** reads the lane.

A week of the field fleet on stage 1 measures what no one has measured yet - browser and mutation jobs' cores and
peaks - before stage 2 decides on them.

## 8. Testing

- The rule is a pure function: (journal state, machine reading taken under the lock, estimates) -> grant or wait,
  with the path and the reason. A table covers: the head fits; the head waits and a short run ends before its shadow;
  a run fits beside the head; two overtakers that each fit but together would not; an unknown holder end; a holder
  overrunning by 2x and by +30 s; a run with no history (prior); a light hint broken; a foreign tree of seven workers;
  memory grown since the reading; mixed rule versions; seniority after expiry.
- A replay of the field journal (29.09-04.10, the commands and their measured times; cores and peaks by class, since
  the journal holds none) checks **invariants at every event**, not only totals: the sum of cores never exceeds the
  budget, reservations never exceed the memory, the head never starts later than it would with no overtaking, no
  waiter's wait exceeds its shadow's bound. Each invariant is seen red under an injected rule that breaks it. A total
  ("less waiting than one or two slots") is reported, not asserted: a rule that admits everyone passes it.
- The memory read under the lock is tested with a reading that changes between the first read and the lock.

## 9. Out of scope, and why

- **Fairness between projects** (next the waiter of the project that held least): one fleet runs on the field machine
  today; it is a small change on top of the queue when a second one does.
- A scheduler process: flotilla has no daemons; a dead daemon would stop the lane.
- Sharding a suite across slots, build caches between seat trees, moving heavy jobs to CI: the field study measured
  each as small or as a decision outside flotilla.
- Test selection by changed files for receipts: a receipt signs a whole tier over a revision.
