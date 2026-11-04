# Lane: one heavy run, light runs beside it (0.28.0) - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.

**Goal:** Light runs (measured ≤ 60 s, ≤ 2 cores, known peak) are admitted on the cores and memory that heavy runs
leave, without counting against `lane_capacity`. On top of that:
- an expired wait keeps its place;
- a waiter is handed the lane within a second of a release;
- `flotilla lane` shows light and heavy, the budget, and each heavy waiter's expected start.

**Spec:** `docs/specs/2026-11-04-lane-light-runs-design.md`.

## Global Constraints

- `RULE = 3`.
- Light admission only for a booking under rule ≥ 3, and only while no live waiter waits under rule < 3.
- Constants:
  - `LIGHT_SECONDS = 60.0`, `LIGHT_CORES = 2.0`, in `estimate.py`;
  - `FOREIGN_CORES = 4.0`, `SENIORITY = 10 min`, in `book.py`;
  - `lane_cpu_factor` comes from machine.toml: default 1.5, bounds 1-4, anything else gives 1.5.
- A booking with no measured estimate is heavy. Prior sources are never light.
- **A holder's kind is read from its recorded `path`:** it is light when the path starts with `light`. It is never
  re-estimated.
- `grant(..., room=None)` keeps today's behaviour exactly. Every existing test passes unchanged.
- Full suite: `uv run --with pytest python -m pytest -p no:cacheprovider tests/ -q -rf`, in the background, with dots
  counted.

## Review Focus

1. A stream of light runs never holds a heavy run back. Light holders are not counted against capacity, and a heavy
   waiter is never "behind" a light one.
2. **A light waiter that does not fit** is never ahead of a heavy one; among light runs order is strict arrival, so
   a later light run waits behind an earlier one that does not fit (pinned by a test).
3. **An older flotilla's waiter is live:** no light admission.
4. **Memory:** the left-over is read inside the lock, through the callable. When it is `None` (unreadable), no light
   admission.
5. **Seniority:** only the same `who` and the same exact signature, only within 10 min, and only once.

---

### Task 1: What is light

**`estimate.py`:**
- `def for_booking(item, hist, *, cpus=None) -> Estimate`: a receipt (`will_run`) goes through `receipt_estimate`,
  anything else through `estimate`.
- `def light(found: Estimate) -> bool`: true when all hold:
  - `seconds` is not None and ≤ 60;
  - `cores` is not None and ≤ 2;
  - `peak_mb` is not None;
  - `"prior" not in source`.

`commands.describe_estimate` uses `for_booking`.

**Tests** (`tests/test_lane_estimate.py`):
- `test_light_needs_a_measured_short_small_run` is parametrized over:
  - measured 4 s / 1 core;
  - 61 s;
  - 3 cores;
  - no peak;
  - the project prior;
  - the fixed prior;
  - a receipt with one unmeasured tier.

**Commit:** `feat(lane): a run is light only by its own measurements`.

### Task 2: Light admission in the grant

**`book.py`:**
- `@dataclass(frozen=True) class Room: cpus: int; factor: float; memory_left: Callable[[], int | None]; foreign: int = 0`
- `def light_verdict(found, mine, room, hist, live) -> str`: `""` when `mine` may start now as a light run, else why
  not. The reasons are:
  - `"not light: <source>"`;
  - `"a waiter of an older flotilla is live"`;
  - `"behind light jN"`;
  - `"needs X cores, Y left"`;
  - `"needs X MB, Y left"`;
  - `"memory cannot be read"`.
- `grant(..., room: Room | None = None)`. With a room, a booking whose estimate is light takes only the light path:
  it holds with `path = "light: X of Y cores, Z MB left"`, or returns None. A heavy booking takes today's path, with
  two differences:
  - holders counted against `slots` are the live holders whose path does not start with `light`;
  - in `_path`, waiters ahead are only the earlier live heavy waiters, and the fair choice skips light waiters.
- `RULE = 3`.

**Tests** (`tests/test_lane_book.py`, with a helper that releases measured runs to build history):
- `test_a_light_run_starts_beside_a_heavy_holder_at_capacity_one`
- `test_a_heavy_run_never_waits_for_light_holders`
- `test_a_light_run_waits_when_cores_or_memory_do_not_fit`
- `test_light_runs_go_by_arrival_among_themselves`
- `test_a_light_waiter_is_not_ahead_of_a_heavy_one`
- `test_no_light_admission_while_an_older_flotillas_waiter_is_live`
- `test_unreadable_memory_admits_no_light_run`
- `test_without_a_room_the_grant_is_todays`
- `test_a_foreign_computing_run_takes_four_cores_of_the_budget`

**Commit:** `feat(lane): light runs take what the heavy ones leave`.

### Task 3: The room reaches the grant

**`acquire.acquire` and `acquire.held`:**
- They gain `room: Callable[[Reading], Room] | None = None`.
- The room is passed to `grant`, built from that poll's reading, so `foreign = len(reading.computing)`.

**`commands`:**
- `def cpu_factor() -> float`: from machine.toml, with the bounds above.
- `def room_for(profile) -> Callable[[Reading], Room]`:
  - `cpus` is `os.cpu_count()`;
  - `memory_left` is `resources.available_mb() - machine.memory_floor_mb(profile)`, or `None` when the available
    memory is `None`.
- `booked` and `_take` pass it.

**Tests:**
- `tests/test_lane_acquire.py`: `test_the_room_built_from_the_reading_reaches_the_grant`.
- `tests/test_lane_cli.py`: `test_cpu_factor_reads_bounds`.

**Commit:** `feat(lane): every booking's grant sees the machine's room`.

### Task 4: An expired wait keeps its place

**`book.py`:**
- `Booking.place: int | None`, folded only from an int ≥ 1.
- `_order(item)` returns `item.place or number(item.id)`.
- `enqueue` sets `place` from the latest EXPIRED booking that meets all of:
  - the same `who`;
  - `ladder[:1]` equal to the new booking's;
  - `ended` within `SENIORITY`;
  - its place not already taken by a later booking.

**Tests:**
- `test_an_expired_booking_made_again_keeps_its_place`
- `test_seniority_needs_the_same_session_signature_and_ten_minutes`
- `test_a_place_is_inherited_once`

**Commit:** `feat(lane): a booking made again after its wait ran out keeps its place`.

### Task 5: Handoff without the 15-second poll

**`Book.stamp() -> tuple | None`:** the journal file's `(size, mtime_ns)`, or None when there is no file.

**`acquire.acquire`:**
- Between polls, it sleeps in steps of 1 s (by the injected `sleep` and `clock`).
- It breaks out early when `lanes.stamp()` changed.
- The poll is `poll + random.uniform(0, 1)`. The random source is injectable as `jitter`.

**Tests:**
- `test_a_waiter_wakes_within_a_second_of_a_journal_change`
- `test_without_a_change_the_machine_is_read_once_per_poll`

**Commit:** `feat(lane): a waiter is handed the lane within a second of a release`.

### Task 6: What the person sees

**`commands._status`:**
- Prints a budget line: `budget: X of Y cores (factor F), Z MB left after holders' peaks (floor N MB)`.
- Each holder is marked `light` or `heavy`.
- Each waiter shows its kind and its reason:
  - a light waiter gets `light_verdict`;
  - a heavy waiter gets `in_the_way`, plus `Nth of the heavy runs; by estimates it starts around HH:MM or sooner`.
- `def heavy_starts(found, hist, slots, now, live, ceiling) -> dict[str, dt.datetime | None]` uses the serial lanes
  model (`slots` lanes). A heavy holder's remaining time is its estimate less its elapsed time, at least 60 s. Unknown
  durations count at `ceiling`.

**Tests** (`tests/test_lane_cli.py`):
- `test_status_marks_light_and_heavy_and_prints_the_budget`
- `test_heavy_waiters_get_an_expected_start`

**Commit:** `feat(lane): flotilla lane shows light and heavy, the budget, and when heavy runs start`.

### Task 7: Documents and version 0.28.0

- README lane section: light runs, the budget, seniority, the faster handoff, `lane_cpu_factor`.
- CHANGELOG.
- TODO:
  - move the polling, seniority and mode 1 items, and item 13's cause, to Done;
  - keep bump, outside-lane counting, and slots.
- Version in three places, and the README pin.

**Commit:** `docs(lane): light runs beside one heavy run; 0.28.0`.
