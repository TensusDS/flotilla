# The lane daemon: one scheduler per machine, soft memory ceilings, freezing as the second line (0.31.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (the person chose native execution).
> Steps use checkbox (`- [ ]`) syntax.

**Goal:** one process per machine decides every start, ceiling, freeze and thaw of the lane from the whole picture;
each run is held by a soft memory ceiling the daemon raises while there is room; a run that churns at its ceiling, or
a machine over its floor, has its lowest-priority run squeezed and then frozen - never killed.

**Architecture:** a pure function `flotilla/lane/tick.py:tick(runs, machine) -> Actions` decides; it reuses
`policy.decide` for starts and adds ceilings, pressure, overload and thaws. `flotilla/lane/groups.py` reads and writes
a scope's cgroup files. `flotilla/lane/scheduler.py` is one tick of the daemon: it reads the journal, the machine and
every held run's group, keeps the timers a pure function cannot (how long pressed, how long over, growth over a
minute), calls `tick`, and applies the actions - journal records in one transaction, ceilings to `memory.high`.
`flotilla/lane/daemon.py` is the process around it: the lock, the heartbeat, `ensure`, idle exit, handover to a newer
copy. Clients stop deciding: a waiter asks and waits for the daemon's admit, or falls back to "only into an empty
lane"; a wrapper obeys only orders signed by the live daemon.

**Tech Stack:** Python 3.11 stdlib (`fcntl` for the lock), pytest; cgroup v2 `memory.high`, `memory.current`,
`memory.events`, `memory.pressure`; `systemd-run --user --scope`.

**Spec:** docs/specs/2026-11-07-lane-daemon-design.md (amends docs/specs/2026-11-06-lane-shared-machine-design.md)

## Global Constraints

- English only in the repository (`tools/check_no_cyrillic.py`). Stdlib only; `requires-python = ">=3.11"`.
- The repository is public: no client or project names, no personal paths, in code, tests, fixtures or docs. The
  field record is cited as `/home/user/var/demo-2026-11-07/`.
- Suite: `uv run --with pytest python -m pytest -p no:cacheprovider tests/`. Book it through the lane of the machine
  it runs on; never two full suites at once.
- Never write machine.toml; no new person's setting in this plan (spec 7: every new number is a builder's constant
  until the field test says otherwise).
- Never `memory.max`. Never a model in a decision. Nothing the daemon decides kills a run.
- Builder's constants, each with a comment saying it awaits the field test (spec 7): `PRESSED` (pressure "some
  avg10", percent) - `50.0` until Task 11 measures it; `PRESSED_S 10 s`; `OVER_S 10 s`; slack `max(256 MB, 10 %)`;
  start ceiling `max(estimate, 256 MB) + slack`; squeeze floor `256 MB`; tick `2 s`; idle exit `600 s`; heartbeat
  stale `10 s`; TERM-to-KILL `5 s`; a daemon-caused freeze thaws no sooner than `30 s`; tie-break band `0.05` of P.
- The person's rulings of 2026-11-07 (spec 2), not builder's numbers: one daemon per machine; any lane client raises
  it; when it cannot be raised, a new run starts only into an empty lane; the daemon runs from the newest installed
  copy and is replaced by a newer one; the first-minute grace does not protect from overload or pressure (it still
  does from a waiter's order); priority, not heaviness, picks the victim, and within 0.05 of P the run that grew most
  over the last minute goes first; a run whose memory the swap cannot take is frozen without reclaim; with only
  immortal or pinned runs over the line nothing is squeezed, frozen or started.
- The swap budget (0.30 spec 4.3): every move that pushes pages out - a ceiling lowered, a squeeze, a reclaim -
  leaves swap at most 80 % used (`policy.SWAP_USED_CAP`).
- Journal: `book.RULE` becomes 5. No new state names. New fields are cleaned on fold like every other (`_clean`):
  wrong type or out of range folds as unknown, never a crash. A record of a 0.30 flotilla folds as before.
- `lane_freeze` off: ceilings still, no freezing; overload ends at a squeeze.
- Version 0.31.0 (a new capability: the daemon) in `pyproject.toml`, `.claude-plugin/plugin.json`,
  `flotilla/__init__.py`, README's pinned example. Tag and release only with the person's ok.

## Review Focus

1. **A forged order.** Any session writes the journal; a line naming the daemon's old pid, a pid that is not the
   lock holder, or no signature at all must be refused by the wrapper - including an order a 0.30 waiter writes.
   Test in Task 7.
2. **The daemon dies with ceilings set and runs frozen** (SIGKILL, a crash in a tick): frozen commands thaw by their
   wrappers' guards, the next client lifts the ceilings it can see before it falls back, and the next `ensure`
   starts a new daemon that reconciles from the journal. Tests in Tasks 6 and 7.
3. **A ceiling written to the wrong cgroup:** a unit name in the journal that does not match the lane's pattern, a
   scope that ended, a unit whose processes do not carry the booking, a path outside the user manager - the daemon
   writes nothing there. Test in Task 4.
4. **Memory cannot be read** (no MemAvailable): no overload, no growth, no pressure freeze; starts by the run cap and
   the cores bound alone, as 0.30 does. Test in Task 3.
5. **Two clients raise the daemon in the same second:** one daemon runs, the other start exits 0 naming it. Test in
   Task 6.

---

### Task 1: The journal speaks rule 5

**Files:**
- Modify: `flotilla/lane/book.py` (RULE, `FIELDS`, `Booking`, `_clean`, `enqueue`, `grant`, `frozen`; new
  `scoped`, `hold_back`, `apply`)
- Test: `tests/test_lane_book.py`

**Interfaces:**
- Produces, on `Booking` (all folded by `_clean`):
  `order_cause: str` (`""`, `"waiter"`, `"over"`, `"pressed"`), `order_pid: int | None` (>= 2),
  `order_mark: str` (visible, <= 80), `ceiling_mb: int | None` (figure, peak bounds), `squeezed: bool`,
  `held_back: str` (visible, <= 200: why the waiter's own gates hold it), `by_hand: bool`,
  `floor_mb: int | None` (figure, peak bounds: the asking project's memory floor), `held_freezes: int` (>= 0).
- Produces constants `book.CAUSES = ("waiter", "over", "pressed")`, `book.RULE = 5`.
- Produces `Book.enqueue(..., floor_mb: int | None = None, by_hand: bool = False)`.
- Produces `Book.scoped(booking_id: str, unit: str) -> Booking | None` - writes `unit` on a held booking; None and
  nothing written when it no longer holds or `unit` does not match `UNIT`.
- Produces `Book.hold_back(booking_id: str, why: str) -> None` - writes `held_back` on a waiting booking only when it
  differs from what the journal says.
- Produces `Book.apply(actions, signer: tuple[int, str]) -> list[str]` - one transaction; `actions` is a
  `tick.Actions` (Task 3; until then any object with its attributes). Each action is checked in the lock against the
  booking as it stands, and skipped when it no longer applies: a start only of a live `WAITING` booking with
  `rule >= 5` and no `held_back`; an order only of a `HELD` booking not frozen and not `unanswered`; a thaw only of a
  `frozen` one; a ceiling only of a `HELD` one. Returns the ids it wrote. A start writes `HELD`, `path = f"value: P
  {p:.2f}, {'heavy' if heavy else 'light'}"`, `started`, `heavy`, and for a `by_hand` booking `pid=None, mark=""`.
  An order writes `frozen="ordered"`, `order_by` (the waiter id, or `""`), `order_cause`, `order_pid`, `order_mark`
  (the signer), `ordered_at`, `reclaim_mb`. A thaw writes `frozen=""`, `started`, `frozen_s`. A ceiling writes
  `ceiling_mb` (None lifts it) and `squeezed` (True for an id in `actions.squeezed`, False for one in
  `actions.restored`).
- Produces `Book.grant(..., fallback: bool = False)` - with `fallback`, admit only into an empty lane: no live `HELD`
  booking, and no live waiter ahead by order (spec 6). Path `"fallback: empty lane"`.
- `Book.frozen` increments `held_freezes` when the booking's `order_cause` is `"over"` or `"pressed"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_lane_book.py`:
  - `test_rule_five_fields_fold_clean`: a journal line with `order_cause="nuke"`, `order_pid=True`, `order_pid=1`,
    `ceiling_mb=-5`, `ceiling_mb=1e12`, `held_freezes=-1`, `squeezed="yes"`, `held_back=<3000 chars>` folds to `""`,
    `None`, `None`, `None`, `None`, `0`, `False`, a string of 200; a line with valid values folds to them.
  - `test_a_0_30_record_folds_as_before`: a rule-4 held record with none of the new fields folds with their empty forms
    and `rule == 4`.
  - `test_enqueue_records_the_floor_and_by_hand`: `enqueue(..., floor_mb=2000, by_hand=True)` folds `floor_mb == 2000`,
    `by_hand is True`, `rule == 5`.
  - `test_scoped_writes_the_unit_of_a_held_booking_only`: held → unit written; released → None; `"../../etc"` → None.
  - `test_hold_back_writes_only_a_change`: two equal calls append one line (journal size grows once).
  - `test_apply_starts_orders_thaws_and_ceilings_in_one_transaction` - a fake `Actions` with one start, one order
    (cause `"over"`, order_by `""`), one thaw, one ceiling (squeezed) on four bookings; asserts each booking's fields
    as listed in Interfaces and `order_pid, order_mark == signer`.
  - `test_apply_skips_what_no_longer_applies`: a start of a booking that left, an order of a frozen one, a start of a
    rule-4 waiter, a start of a waiter with `held_back` - nothing written for any, returned list empty.
  - `test_apply_admits_by_hand_as_nobodys`: a `by_hand` waiter admitted → `pid is None`, `mark == ""`.
  - `test_fallback_admits_only_into_an_empty_lane`: with a live held booking → None; with none and an earlier live
    waiter → None for the later one; alone → held, path `"fallback: empty lane"`.
  - `test_frozen_counts_daemon_freezes`: an `"over"` order then `frozen(...)` → `held_freezes == 1`, `freezes == 1`;
    a `"waiter"` order then `frozen(...)` → `held_freezes == 0`.
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_book.py -q`.
  Expected: the new tests FAIL (missing fields/methods); the old ones pass.
- [ ] **Step 3: Implement** the Interfaces above in `flotilla/lane/book.py`. Update the `RULE` comment: rule 5
  (0.31) is decided by the lane daemon; a waiter under it never admits itself while a daemon runs.
- [ ] **Step 4: Run** the same command. Expected: all pass. Then
  `uv run --with pytest python -m pytest -p no:cacheprovider tests/ -q -k lane` - expected: all pass (a test that
  pins `RULE == 4` is updated to 5, with that change named in the commit).
- [ ] **Step 5: Commit** `feat(lane): the journal speaks rule 5 - daemon orders, ceilings, held-back waiters, the empty-lane fallback`.

### Task 2: The policy's new inputs and the victim's order

**Files:**
- Modify: `flotilla/lane/policy.py`
- Test: `tests/test_lane_policy.py`

**Interfaces:**
- Produces on `policy.Run` (all with defaults, so every existing caller stands): `ceiling_mb: int | None = None`,
  `scoped: bool = False`, `at_ceiling: bool = False`, `pressure: float | None = None`, `pressed_s: float = 0.0`,
  `grew_mb: int = 0`, `peak_mb: int = 0`, `squeezed: bool = False`, `cause: str = ""`,
  `frozen_s: float = 0.0`, `held_freezes: int = 0`, `reclaimed_mb: int = 0`, `rule: int = 5`.
- Produces on `policy.Machine`: `available_mb: int | None = None`, `floor_mb: int = 0`, `over_s: float = 0.0`.
- Produces constants `WAITER, OVER, PRESSED_CAUSE = "waiter", "over", "pressed"`, `TIE_P = 0.05`,
  `MIN_CEILING_MB = 256`, `SLACK_SHARE = 0.10`.
- Produces `slack(mb: int) -> int` = `max(256, round(0.10 * mb))`.
- Produces `start_ceiling(estimate_mb: int) -> int` = `base + slack(base)` where `base = max(estimate_mb, 256)`.
- Produces `room(runs: list[Run], machine: Machine) -> int | None` = `available - floor - Σ max(0, ceiling - mem)`
  over running/ordered runs with a ceiling `- reserved`, where reserved is `machine.available_mb - machine.floor_mb
  - machine.free_mb` (what admission already reserves for just-started runs); None when memory is unknown.
- Produces `daemon_reason(v: Run, machine: Machine) -> str` - why `v` may not be squeezed or frozen for overload or
  pressure; "" when it may: not `RUNNING`, pinned, not freezable, immortal, `refused_for` set. No grace (ruling 5),
  no heaviness rule.
- Produces `victim(candidates: list[Run], immortal_s: float) -> Run | None` - the lowest P; among those within
  `TIE_P` of it, the largest `grew_mb`; then the lowest P; then the newest (largest `order`).

- [ ] **Step 1: Write the failing tests:**
  - `test_start_ceiling`: `start_ceiling(100) == 512`, `start_ceiling(0) == 512`, `start_ceiling(4000) == 4400`.
  - `test_room_counts_unused_ceiling_as_promised`: available 10000, floor 2000, free_mb 7500 (500 reserved), one run
    mem 1000 ceiling 3000, one run mem 500 ceiling None → `room == 10000 - 2000 - 2000 - 500 == 5500`; available None
    → None.
  - `test_victim_is_the_lowest_priority`: P 0.30 and P 0.20 → the 0.20.
  - `test_victim_tie_break_by_growth` (the person's ruling): P 0.20 grew 100, P 0.24 grew 3000, P 0.40 grew 9000 →
    the 0.24; with the 0.24 at P 0.26 → the 0.20.
  - `test_daemon_reason_ignores_the_grace` (ruling 5): a run 10 s since start, P low → `""`; the same pinned →
    `"... is pinned"`; immortal → `"... is immortal"`; `refused_for="x"` → non-empty; frozen → non-empty.
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_policy.py -q`.
  Expected: new tests FAIL; old ones pass.
- [ ] **Step 3: Implement** in `flotilla/lane/policy.py`. `decide` is not touched.
- [ ] **Step 4: Run** the same file and `tests/test_lane_policy_model.py`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lane): the policy reads ceilings, pressure and growth, and orders a victim by priority then growth`.

### Task 3: `tick` - one pure decision over the whole lane

**Files:**
- Create: `flotilla/lane/tick.py`
- Test: `tests/test_lane_tick.py`

**Interfaces:**
- Consumes: Task 2's `Run`/`Machine` fields, `slack`, `start_ceiling`, `room`, `daemon_reason`, `victim`;
  `policy.decide`, `policy.immortal`, `policy.priority`, `policy.SWAP_USED_CAP`, `policy.RECLAIM_MARGIN_MB`.
- Produces:

```python
@dataclass(frozen=True)
class Actions:
    starts: tuple[tuple[str, bool, float], ...] = ()      # (id, heavy, P)
    ceilings: tuple[tuple[str, int | None], ...] = ()     # (id, MB); None lifts it to max
    squeezed: tuple[str, ...] = ()                        # ids whose entry in `ceilings` is a squeeze
    restored: tuple[str, ...] = ()                        # squeezed runs given back used + slack
    orders: tuple[tuple[str, int, str, str], ...] = ()    # (id, reclaim MB, cause, the waiter's id or "")
    thaws: tuple[str, ...] = ()
    why: tuple[tuple[str, str], ...] = ()                 # (id, why it does not move)
    over_mb: int = 0                                      # over the floor and nothing can be taken (ruling 8)

def tick(runs: list[policy.Run], machine: policy.Machine) -> Actions
```

  Constants: `PRESSED = 50.0` (percent, "some avg10"; awaits Task 11), `PRESSED_S = 10.0`, `OVER_S = 10.0`,
  `THAW_AFTER_S = 30.0`.
  Ruling recorded here: the spec names `policy.tick(runs, machine, now)`; every timer `tick` needs arrives measured on
  its inputs (`pressed_s`, `over_s`, `frozen_s`, `grew_mb`), so `tick` takes no clock and lives in its own module,
  which keeps `policy.py` the waiter's rules it was.

- [ ] **Step 1: Write the failing tests** in `tests/test_lane_tick.py`, one per rule, each building runs and a
  machine by hand (a helper `run(id, state, mem, **kw)` and `machine(available, floor, **kw)`):
  - `test_a_started_run_gets_its_start_ceiling`: one waiter, mem 100, scoped → `starts == (("w", False, P),)`,
    `("w", 512) in ceilings`. Not scoped → no ceiling entry.
  - `test_starts_follow_decide`: two waiters, room for one → only the higher starts; why of the other names memory.
  - `test_a_waiters_freeze_comes_from_decide`: a waiter that fits only if a lower run past its first minute is
    frozen → `orders == (("low", n, "waiter", "w"),)`.
  - `test_growth_doubles_while_there_is_room`: running mem 512 ceiling 512 `at_ceiling`, room 10000 → ceiling 1024;
    room 300 → ceiling 812; room 0 → unchanged; not at ceiling → unchanged.
  - `test_growth_goes_by_priority`: two at their ceilings, room for one doubling → the higher P grows.
  - `test_immortal_and_pinned_runs_lose_their_ceiling`: a run with ceiling 1000 and `ran_s >= immortal_s` →
    `("r", None) in ceilings`; pinned likewise; one with ceiling None and immortal → no entry.
  - `test_pressed_without_room_freezes_the_lowest`: pressed run P 0.4 (at ceiling, `pressure 80`, `pressed_s 12`,
    room 0) and a runnable lower run P 0.2 → order on the lower with cause `"pressed"`; with no lower run → order on
    the pressed run itself, reclaim 0; `pressed_s 5` → no order.
  - `test_overload_squeezes_the_victim_first`: available 1500 floor 2000 `over_s 12`, victim mem 3500 scoped → its
    ceiling becomes `3000` (used - deficit), it is in `squeezed`, no order; `over_s 5` → nothing.
  - `test_the_squeeze_never_goes_under_256`: victim mem 400, deficit 2000 → ceiling 256.
  - `test_the_squeeze_is_clamped_to_the_swap_budget`: swap total 10000 free 2500 (75 % used), victim mem 3500 deficit
    1000 → push at most 500 → ceiling 3000; swap at 80 % → no squeeze, an order with reclaim 0 (ruling 7).
  - `test_a_squeezed_run_that_is_pressed_is_frozen`: squeezed victim with `pressed_s 12` while over → order cause
    `"over"`, reclaim within the swap budget.
  - `test_an_unscoped_victim_is_frozen_at_once`: not scoped, over → order, reclaim 0.
  - `test_overload_ignores_the_grace` (ruling 5): the only candidate is 5 s old → it is squeezed.
  - `test_overload_with_nothing_to_take` (ruling 8): only pinned and immortal runs, over by 700 → no ceilings
    lowered, no orders, no starts though a waiter would fit by `decide`, `over_mb == 700`.
  - `test_one_victim_at_a_time`: an order outstanding (a run in state `ORDERED`) → no new squeeze or order; a run
    squeezed whose `mem > ceiling + slack(ceiling)` → none either.
  - `test_freezing_off_ends_at_a_squeeze`: `machine.freeze=False`, squeezed victim pressed → no order.
  - `test_squeezed_runs_are_restored_before_any_start`: not over, a squeezed run mem 2000 ceiling 2000, room 5000, a
    waiter that fits → `("s", 2256) in ceilings`, `"s" in restored`, no start this tick.
  - `test_a_daemon_freeze_thaws_after_30_s_with_room_for_its_headroom`: frozen cause `"over"`, ceiling 2000,
    `reclaimed_mb 1000`, `frozen_s 31`, room 1300 → thaw (needs slack 256 + 1000); room 1200 → no thaw;
    `frozen_s 20` → no thaw; over → no thaw.
  - `test_a_pressed_freeze_needs_room_for_a_doubling`: frozen cause `"pressed"`, ceiling 2000 → needs 2000 + reclaimed.
  - `test_twice_frozen_needs_room_for_its_peak`: `held_freezes 2`, `peak_mb 5000`, mem 3000 → needs 2000 at least.
  - `test_a_waiter_freeze_thaws_by_decide`: frozen cause `"waiter"` that `decide(..., may_freeze=False)` would start →
    thaw.
  - `test_memory_unknown_decides_by_bounds_only` (Review Focus 4): `available_mb None` → no ceiling growth, no
    squeeze, no order but those `decide` gives (none, since memory is unknown), starts by the run cap.
  - `test_older_bookings_are_never_started`: a waiter with `rule 4` that would fit → not in `starts`.
  - `test_every_run_that_does_not_move_has_a_why`: every waiting and frozen run id appears in `why` unless started
    or thawed.
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_tick.py -q`.
  Expected: FAIL (`flotilla.lane.tick` not found).
- [ ] **Step 3: Implement `tick` in `flotilla/lane/tick.py`** in this order, which the tests pin: (1) lift ceilings of
  immortal or pinned runs; (2) restore squeezed runs when not over and room covers `used + slack - ceiling`, and if
  any was squeezed and not restored, no start this tick; (3) overload (spec 4.3 d), at most one victim, none while an
  order is out or a squeeze has not shown; (4) pressure (4.3 c) when no victim was taken in (3); (5) starts through
  `decide` per rule-5 waiter in priority order, skipped when over, when `over_mb`, or after (2) held them; the first
  waiter whose `decide` gives orders gives the tick's orders (cause `"waiter"`), and none when (3) or (4) gave one;
  (6) growth by priority, from the room left after the starts' ceilings; (7) thaws (4.3 e) by priority, each taking
  its need from the room. Swap room for any push = `SWAP_USED_CAP * total - (total - free) - pending`.
- [ ] **Step 4: Run** the same file. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lane): tick - one pure decision of starts, ceilings, squeezes, freezes and thaws over the whole lane`.

### Task 4: Reading and writing a run's cgroup

**Files:**
- Create: `flotilla/lane/groups.py`
- Test: `tests/test_lane_groups.py`

**Interfaces:**
- Consumes: `freeze.CGROUP_ROOT`, `freeze._manager`, `freeze._own_cgroup`, `freeze.carries`, `book.UNIT`.
- Produces:

```python
@dataclass(frozen=True)
class Reading:
    current_mb: int
    high_mb: int | None        # None: "max"
    high_events: int           # memory.events "high"
    pressure: float | None     # memory.pressure "some avg10"; None when the file is absent

def cgroup_of(unit: str, booking: str, *, run=subprocess.run, root: Path = freeze.CGROUP_ROOT,
              proc_root: Path = Path("/proc"), own: str | None = None) -> Path | None
def read(path: Path) -> Reading | None
def set_high(path: Path, mb: int | None) -> bool
```

  `cgroup_of` answers a path only when: `unit` fully matches `book.UNIT`; `systemctl --user show -p ControlGroup
  --value -- <unit>` names a path; that path lies under this user's manager (`freeze._manager`) and its last part is
  `unit`; the directory has `memory.high`; and at least one pid in its `cgroup.procs` carries `booking`
  (`freeze.carries(...) is True`). Anything else: None. `read` returns None when the directory is gone. `set_high`
  writes `f"{mb}M"` or `"max"`; False on any OSError.

- [ ] **Step 1: Write the failing tests** with a fake cgroup tree under `tmp_path` and a fake `/proc` (environ files):
  - `test_cgroup_of_answers_a_lane_scope_that_carries_the_booking`.
  - `test_cgroup_of_refuses` (Review Focus 3), parametrized: a unit not matching the pattern; systemctl failing; a
    path outside the user manager; a path whose last part is another unit; no `memory.high`; no process carrying the
    booking; a process carrying another booking.
  - `test_read_parses_the_four_files`: `memory.current` 1073741824, `memory.high` "max", `memory.events` with
    `high 7`, `memory.pressure` with `some avg10=42.50 ...` → `Reading(1024, None, 7, 42.5)`; without
    `memory.pressure` → pressure None; directory gone → None.
  - `test_set_high_writes_megabytes_or_max`.
  - `test_a_real_scope_takes_a_ceiling` (Linux with a user manager only; skipped otherwise with the reason): start
    `systemd-run --user --scope --unit=flotilla-lane-test-<pid>.scope -- sleep 30` with
    `FLOTILLA_LANE_BOOKING=test` in its environment, resolve it with `cgroup_of`, `set_high(path, 300)`, read back
    `high_mb == 300`, `pressure` is a float; stop the scope in a `finally`.
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_groups.py -q`.
  Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `flotilla/lane/groups.py`.
- [ ] **Step 4: Run** the same command. Expected: all pass; the real-scope test passes on this machine (it has a user
  manager) - read the output to see it ran, not skipped.
- [ ] **Step 5: Commit** `feat(lane): read and write a run's cgroup - only a lane scope that carries its booking`.

### Task 5: The scheduler - one tick of the daemon

**Files:**
- Create: `flotilla/lane/scheduler.py`
- Modify: `flotilla/lane/freeze.py` (`Slot.on_scope`), `flotilla/lane/watch.py` (`start` sets it)
- Test: `tests/test_lane_scheduler.py`

**Interfaces:**
- Consumes: `admission.view`, `admission.Value`, `estimate.history`, `Book.apply` / `Book._rescue` /
  `Book.bookings` / `Book.live`, `tick.tick`, `groups.cgroup_of` / `read` / `set_high`.
- Produces:

```python
class History:
    """What a pure tick cannot keep: per run, how long it has been pressed and its memory a minute ago; for the
    machine, how long it has been under its floor; each run's last memory.events "high" count and its peak."""
    def run(self, now: float, booking: str, reading: groups.Reading | None, ceiling: int | None) -> dict
    # -> {"at_ceiling": bool, "pressure": float | None, "pressed_s": float, "grew_mb": int, "peak_mb": int}
    def machine(self, now: float, available: int | None, floor: int) -> float   # seconds under the floor
    def forget(self, live: set[str]) -> None

class Scheduler:
    def __init__(self, lanes, value_of, *, signer: tuple[int, str], groups_io=groups, clock=time.monotonic,
                 default_floor: int)
    def step(self) -> tuple[tick.Actions, dict]
    # -> the actions applied, and the heartbeat's picture: {"why": {id: why}, "groups": {id: {"ceiling_mb",
    #    "current_mb", "pressure", "squeezed", "pressed"}}, "over": {"mb": int, "s": float} | {}}
```

  `step`: `lanes._rescue` inside a transaction (lapsed orders, dead holders' frozen commands) → `found` → floor =
  the largest `floor_mb` among live bookings, `default_floor` when none names one → `admission.view(found, hist,
  value, now=..., live=..., mine="")` → for each live held booking with a `unit`: its cgroup (cached per
  `(booking, unit)`; a changed unit is resolved again), its reading, `History.run` → enrich each `policy.Run` with
  `dataclasses.replace` (ceiling and squeezed from the journal; cause from `order_cause`; `frozen_s` from
  `frozen_at`; `reclaimed_mb` from `reclaim_mb` of a frozen run; `held_freezes`; `rule`; `scoped` = a cgroup was
  found) and drop waiters with `held_back` → the machine with `available_mb`, `floor_mb`, `over_s` and
  `free_mb = policy.room(...)` → `tick.tick` → `lanes.apply(actions, signer)` → for every scoped held run, write
  its `memory.high` when the file differs from the journal's `ceiling_mb` (None = `max`) - so a restarted daemon
  reconciles, and a new scope of a receipt's next tier gets its booking's ceiling.
  `Slot.on_scope: Callable[[str], None] | None` is called by `Slot.contain` with the unit it made; `Watch.start` sets
  it to `lambda unit: lanes.scoped(booking_id, unit)`.

- [ ] **Step 1: Write the failing tests** with a real `Book` on `tmp_path`, fake procs (all alive), a fake
  `groups_io` (dict of booking → Reading, records `set_high` calls), and a fake clock:
  - `test_history_pressed_time_counts_only_while_at_the_ceiling_and_pressed`: 6 steps of 2 s at pressure 80 and
    events rising → `pressed_s == 10`; one step at pressure 10 → 0.
  - `test_history_growth_over_a_minute`: memory 500 at t=0, 2500 at t=60 → `grew_mb == 2000`; at t=30 → measured
    against the oldest reading.
  - `test_history_over_time_resets_above_the_floor`.
  - `test_step_admits_a_waiter_and_sets_its_ceiling` - one rule-5 waiter, empty lane → held, `ceiling_mb == 512`
    in the journal; after `scoped(...)` and a second step → `set_high(path, 512)` called once.
  - `test_step_reconciles_a_ceiling_the_file_lost`: journal 1024, file "max" → rewritten 1024.
  - `test_step_uses_the_largest_floor_among_live_bookings`: floors 1000 and 3000 → the machine's floor 3000.
  - `test_step_skips_a_held_back_waiter`.
  - `test_step_signs_its_orders`: a forced overload → the order's `order_pid, order_mark == signer`.
  - `test_slot_reports_its_scope`: `Slot(SCOPE).contain([...], booking="b1")` with `on_scope` set → called with the
    unit.
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_scheduler.py -q`.
  Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `flotilla/lane/scheduler.py`, `Slot.on_scope`, and its setting in `Watch.start`.
- [ ] **Step 4: Run** the file, then `tests/test_lane_freeze.py tests/test_lane_watch.py`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(lane): the scheduler - one tick reads the lane, the machine and every run's group, decides, and applies`.

### Task 6: The daemon process - lock, heartbeat, ensure, handover, idle exit

**Files:**
- Create: `flotilla/lane/daemon.py`
- Modify: `flotilla/cli.py` (`lane daemon run|ensure`), `flotilla/lane/commands.py` (dispatch)
- Test: `tests/test_lane_daemon.py`

**Interfaces:**
- Consumes: `Scheduler`, `claude_state.installs`, `claude_state.running_root`, `claude_state._version`,
  `ProcessTable.start_mark/alive`, `flotilla.__version__`.
- Produces (files under `paths.state_dir() / "lane"`: `daemon.lock`, `daemon.json`, `daemon.log`):

```python
PROTOCOL = 1
KNOWN = frozenset({1})
TICK_S, IDLE_S, STALE_S, TERM_WAIT_S = 2.0, 600.0, 10.0, 5.0

@dataclass(frozen=True)
class Heartbeat:
    pid: int
    mark: str
    copy: str          # the plugin copy it runs from
    release: str
    protocol: int
    tick: float        # wall clock of its last tick
    why: dict
    groups: dict
    over: dict

def heartbeat(root: Path) -> Heartbeat | None          # None: absent, torn, wrong types
def lock_held(root: Path) -> bool                      # try LOCK_EX | LOCK_NB, release at once
def alive(root: Path, procs, *, now=time.time) -> Heartbeat | None
    # a heartbeat whose pid and mark are live, the lock held, the tick within STALE_S
def signed(root: Path, procs, pid: int | None, mark: str) -> bool
    # an order's signature is the live daemon's: alive(...) and (pid, mark) equal
def newest_copy(*, running: Path | None = None, installs=claude_state.installs) -> tuple[str, Path]
    # the highest release among installs whose installPath holds bin/flotilla, and the running copy
def lift(root: Path, lanes, groups_io=groups) -> int     # every journal ceiling's scope set to "max"; how many
def ensure(root: Path, procs, *, newest=newest_copy, spawn=_spawn, now=time.time, sleep=time.sleep) -> str
def serve(root: Path, *, handover: bool = False, idle_s: float = IDLE_S, ticks: int | None = None,
          clock=time.monotonic, sleep=time.sleep) -> int
```

  `ensure` returns `""` when a matching daemon runs; else one line: `"started the lane daemon (<release>)"`,
  `"replaced a stuck lane daemon (no tick for N s)"`, `"replaced the lane daemon <old> with <new>"`, or a fallback
  reason starting `"lane daemon not running: "` (it could not start; or `"the lane daemon speaks protocol N, this copy
  M: restart this session"`). A stuck daemon: TERM to its process group, KILL after `TERM_WAIT_S`, then start. A
  newer copy than the running daemon's release: TERM it, wait for the lock to free (up to `STALE_S`), start the newer
  copy. A start is `[sys.executable, <copy>/bin/flotilla, "lane", "daemon", "run"]`, `start_new_session=True`,
  stdin `/dev/null`, output appended to `daemon.log`, then wait up to 5 s for a heartbeat. When it reports a
  fallback, `ensure` first lifts the ceilings it can see (`lift`).
  `serve`: take the lock (non-blocking; with `handover`, wait up to `STALE_S`); not taken → print `"the lane daemon
  already runs: pid N (<release>)"`, exit 0. Loop: `Scheduler.step`, write `daemon.json` (atomic replace), sleep
  until `TICK_S` or a journal change (`Book.stamp`); exit when no booking is held or waiting for `IDLE_S`, lifting
  every ceiling on the way out; when `newest_copy()` names a higher release than its own, spawn it with `--handover`,
  release the lock, exit without lifting (the successor reconciles from the journal). An exception in a tick is
  written to `daemon.log` and the loop goes on; three in a row exit, lifting ceilings.
  CLI: `flotilla lane daemon run [--handover] [--idle SECONDS]` (`--idle` hidden from help, for tests), `flotilla lane daemon ensure` (prints its line when not empty; exit 0;
  exit 1 on a fallback reason).

- [ ] **Step 1: Write the failing tests:**
  - `test_heartbeat_round_trip_and_torn_file`.
  - `test_alive_needs_the_lock_the_pid_and_a_fresh_tick`: each missing in turn → None.
  - `test_signed_matches_only_the_live_daemon` (Review Focus 1): the daemon's pid and mark → True; its pid and
    another mark → False; another pid → False; lock not held → False; heartbeat stale → False.
  - `test_newest_copy_picks_the_highest_usable_release`: installs 0.30.1 and 0.31.0 (0.31.0 without `bin/flotilla`)
    and running 0.31.0 elsewhere → the running one.
  - `test_serve_once_writes_a_heartbeat_and_exits_on_idle` (`ticks`, fake clock past `IDLE_S`): exits 0, ceilings
    lifted (`lift` called), lock free.
  - `test_ensure_starts_when_nothing_runs` / `..._replaces_a_stuck_daemon` / `..._replaces_an_older_release` /
    `..._refuses_an_unknown_protocol` / `..._lifts_ceilings_on_fallback` - with a fake `spawn` and fake procs.
  - Process tests (real processes, `FLOTILLA_STATE_DIR` in `tmp_path`, a real `bin/flotilla`):
    `test_two_starts_one_daemon` (Review Focus 5: two `lane daemon run` at once → one holds the lock, the other exits
    0 naming it); `test_a_killed_daemon_is_replaced_by_ensure` (SIGKILL it → `ensure` starts a new pid);
    `test_daemon_exits_idle` (started with the hidden `--idle 2`).
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_daemon.py -q`.
  Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `flotilla/lane/daemon.py`, the CLI parser and dispatch.
- [ ] **Step 4: Run** the file. Expected: all pass, the process tests included (read that they ran).
- [ ] **Step 5: Commit** `feat(lane): the lane daemon - one per machine, raised by any client, replaced by a newer copy, gone when idle`.

### Task 7: The wrapper obeys only the daemon

**Files:**
- Modify: `flotilla/lane/watch.py` (`_order_holds`, the frozen branch)
- Test: `tests/test_lane_watch.py`

**Interfaces:**
- Consumes: `daemon.signed`, `daemon.alive`, `book.Booking.order_cause/order_pid/order_mark`, `admission.view`,
  `policy.victim_reason`, `policy.immortal`.
- Rules (spec 4.8): an order is obeyed only when `daemon.signed(root, lanes.procs, item.order_pid,
  item.order_mark)` and it is fresh (`ANSWER_S`), and then: cause `"waiter"` → today's checks of the waiter it names
  (live, rule >= 5, `victim_reason(..., by_rules_only=True)`, the grace included); cause `"over"` or `"pressed"` →
  this run is not pinned, not immortal (by `admission.view`), and not let go in this process. Anything else is
  refused (`lanes.refuse`). While frozen under a daemon order the watch does not ask `lanes.thaw` itself: it thaws
  when the record says thawed (the existing "thawed from outside" branch), when the freeze ceiling passes (existing),
  when the order's cause is `"waiter"` and the let-go bound passes (existing), or when `daemon.alive(...)` is None
  - then `lanes.thaw(id, value, force=True)` and the command thaws (spec 6: a dead daemon is a gone watcher).

- [ ] **Step 1: Write the failing tests:**
  - `test_an_unsigned_order_is_refused` - no `order_pid` → refused, not frozen.
  - `test_an_order_from_a_dead_daemon_is_refused` - signed by a pid that is no longer the lock holder.
  - `test_a_0_30_waiters_order_is_refused` - a rule-4 waiter's order (`order_by` set, no signature) → refused.
  - `test_an_overload_order_freezes_a_young_run` (ruling 5) - signed, cause `"over"`, run 10 s old → frozen.
  - `test_an_overload_order_never_freezes_an_immortal_or_pinned_run` - each → refused.
  - `test_a_waiter_order_still_honours_the_grace` - signed, cause `"waiter"`, run 10 s old → refused.
  - `test_a_daemon_frozen_run_waits_for_the_daemons_thaw` - frozen, daemon alive, `lanes.thaw` never called by the
    watch; record set thawed → the command thaws.
  - `test_a_frozen_run_thaws_when_the_daemon_dies` (Review Focus 2).
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_watch.py -q`.
  Expected: new tests FAIL; tests of 0.30's self-thaw that no longer hold are rewritten to the daemon's thaw, each
  named in the commit body.
- [ ] **Step 3: Implement** in `flotilla/lane/watch.py`.
- [ ] **Step 4: Run** `tests/test_lane_watch.py tests/test_lane_freeze.py tests/test_lane_freeze_scope.py`.
  Expected: all pass.
- [ ] **Step 5: Commit** `fix(lane): a wrapper obeys only the live daemon's signed order - overload ignores the grace, never the anti-goals`.

### Task 8: Waiters ask and wait; with no daemon, only into an empty lane

**Files:**
- Modify: `flotilla/lane/acquire.py` (`acquire`, `held`), `flotilla/lane/commands.py` (`booked`, `_take`)
- Test: `tests/test_lane_acquire.py`

**Interfaces:**
- Consumes: `daemon.ensure`, `daemon.heartbeat`, `Book.hold_back`, `Book.grant(..., fallback=True)`,
  `Book.enqueue(..., floor_mb=, by_hand=)`.
- Produces `acquire(..., scheduler: Callable[[], str] | None = None, floor_mb: int | None = None)`: with a
  `scheduler` (and a `value`), each poll calls it (`daemon.ensure` bound to the state dir); `""` → daemon mode:
  `lanes.hold_back(mine, why_of_gates)` ("" when the gates pass), the grant is the booking turning `HELD`, and the
  reason printed is the heartbeat's `why[mine]` (or "the lane daemon has not looked at this run yet"); a non-empty
  answer → fallback mode: that line is said once, and `lanes.grant(..., fallback=True)` while the gates pass. A
  waiter of `lane take` is enqueued with `by_hand=True`. `commands.booked` and `_take` pass
  `scheduler=lambda: daemon.ensure(...)` and `floor_mb=machine.memory_floor_mb(profile)`.

- [ ] **Step 1: Write the failing tests** (fake scheduler, fake reading):
  - `test_a_waiter_is_admitted_by_the_daemon_not_by_itself` - scheduler returns ""; nothing granted until a test
    thread writes the booking `HELD` (as the daemon would); then the grant returns it.
  - `test_a_waiter_holds_back_while_its_gates_fail` - a CI answer blocks → `held_back` written; it passes →
    cleared.
  - `test_a_waiter_prints_the_daemons_why`.
  - `test_with_no_daemon_a_run_starts_only_into_an_empty_lane` - scheduler returns `"lane daemon not running: ..."`;
    a live held booking → no grant, the reason said; released → granted, path `"fallback: empty lane"`.
  - `test_take_by_hand_waits_as_a_by_hand_booking`.
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_acquire.py -q`.
  Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/ -q -k lane`. Expected: all
  pass.
- [ ] **Step 5: Commit** `feat(lane): waiters ask the daemon and wait; with none, a run starts only into an empty lane`.

### Task 9: The replay of 2026-11-07 01:54-01:58

**Files:**
- Create: `tests/fixtures/lane/daemon-replay-2026-11-07.json`, `tests/lane_daemon_replay.py`
- Test: `tests/test_lane_daemon_replay.py`

**Interfaces:**
- Consumes: `tick.tick`, `policy.*`, `scheduler.History`.
- The fixture is built once from the field record (`/home/user/var/demo-2026-11-07/`: the lane journal 01:45-02:00,
  the machine samples, each task's load spec), names neutralised (`r1`..`rN`, project `a`/`b`): for each run its
  ask and start second, its load spec (`mb`, `cores`, `seconds`), its estimate at admission, kind, and `hot: true`
  (the demo's load touched its whole block); the machine's total memory, swap, floor, and the memory held outside the
  lane (the samples' used memory before the first run). A short header in the fixture says how it was made.
- The simulator (`tests/lane_daemon_replay.py`) steps 2 s: each started run's demand grows to its `mb` over its
  first 20 s (the load's fill) and holds it for `seconds`; its resident memory is `min(demand, ceiling)`, the rest
  in swap; `at_ceiling` while `demand > ceiling`; pressure 90 for a hot run held below its demand, 5 otherwise; a
  frozen run's resident memory is what it held less its reclaim; `MemAvailable = total - outside - Σ resident`.
  It calls `tick` with runs built as the scheduler would (it uses `History`) and applies the actions to its own state.
- [ ] **Step 1: Write the failing test** `test_the_daemon_holds_the_machine_of_2026_11_07` - asserts the outcome
  (spec 8): no immortal run ever squeezed or frozen; at most one run squeezed or ordered at a time; `MemAvailable`
  never below the floor for longer than `OVER_S + 2 * TICK_S`; swap never above 80 % by the daemon's moves; every run
  finishes within the replay's horizon (none frozen for good). And
  `test_the_replay_is_fast` - the file runs in under 10 s.
- [ ] **Step 2: Run** `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_daemon_replay.py -q`
  before building the fixture's simulator: FAIL (missing). Then once the simulator runs against a `tick` with its
  overload step removed (an injection, reverted by writing the file back from its revision and checking `sha256sum`):
  it must FAIL on the floor assertion - the guard is seen red.
- [ ] **Step 3: Build** the fixture and the simulator.
- [ ] **Step 4: Run** the same command. Expected: PASS; print the replay's lowest `MemAvailable`, the victims in
  order, and each squeeze and freeze, for the commit body.
- [ ] **Step 5: Commit** `test(lane): the 2026-11-07 minutes replayed through tick - the machine stays above its floor`.

### Task 10: What the person and the posts see

**Files:**
- Modify: `flotilla/lane/commands.py` (`_status_by_value`, `value_of`), `flotilla/doctor.py`,
  `templates/posts/orchestrator.md` (`template_version: 28`), `skills/lane/SKILL.md`, `skills/flotilla/SKILL.md`
  (the lane paragraph), `USER_MANUAL.md` (the lane chapter), `CHANGELOG.md`, the version files (Global Constraints)
- Test: `tests/test_lane_cli.py`, `tests/test_doctor.py`, the template tests that pin versions

**Interfaces:**
- `flotilla lane` prints first `lane daemon: running, pid N, <release>, last tick N s ago` or `lane daemon: not
  running - <reason>; a new run starts only into an empty lane`; per held run, beside P and memory, `ceiling 1.2 GB,
  pressure 3 %`, `squeezed` or `pressed` when so (from the heartbeat's `groups`); when the heartbeat's `over` is
  set: `machine: over its floor by N MB for M s` and, with nothing to take, `nothing can be frozen: every run over the
  line is immortal or pinned`. A booking with `rule < 5` is named `(an older flotilla: decides for itself)`.
- doctor: a finding `lane daemon` - ok with its release and pid, or ok "not running; the next lane client starts
  it", or warn when its release is older than the newest install.
- The orchestrator post gains one bullet: run `flotilla lane daemon ensure` at the start and before each
  `flotilla watch`, and say its line to the person only when it printed one; it decides nothing about runs.

- [ ] **Step 1: Write the failing tests:** `test_status_names_the_daemon`, `test_status_prints_ceiling_and_pressure`,
  `test_status_prints_over_and_nothing_to_take`, `test_status_names_an_older_booking`,
  `test_doctor_reports_the_lane_daemon` (three cases).
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement**; write the docs and the CHANGELOG entry (what the person gets: the lane no longer waits
  for a waiter to protect the machine; a run's memory is held by a ceiling that grows while there is room; the
  newest run that outgrows the machine is squeezed, then frozen; nothing is killed); bump the version to 0.31.0.
- [ ] **Step 4: Run** the full suite through the lane: `uv run --with pytest python -m pytest -p no:cacheprovider
  tests/ -q`. Expected: the `N passed` line and no failure; `tools/check_no_cyrillic.py` clean.
- [ ] **Step 5: Commit** `feat(lane): flotilla lane, doctor and the orchestrator post know the daemon; 0.31.0`.

### Task 11: Measure `PRESSED`, and the cold assumption on a real suite

**Files:**
- Modify: `flotilla/lane/tick.py` (`PRESSED` and its comment), `docs/specs/2026-11-07-lane-daemon-design.md`
  (section 3: a row per new measurement)
- The bench lives outside the repository (`/home/user/var/demo-2026-11-07/bench/`); book the machine for it.

- [ ] **Step 1:** Teach the bench's driver to read the scope's `memory.pressure` "some avg10" and `memory.events`
  "high" every second. Re-run hot and cold under `memory.high` 1 GB, 3 GB for 60 s, as in section 3.
  Expected: the hot profile's pressure stays high, the cold profile's low; record both series.
- [ ] **Step 2:** Set `PRESSED` between the two (the midpoint of the hot run's 10th percentile and the cold run's
  90th, rounded to 5), with the numbers in its comment. Run `tests/test_lane_tick.py`; a test that pinned `50.0`
  reads the constant instead.
- [ ] **Step 3:** Run this repository's own suite once without a ceiling in a scope, reading its peak; then once
  with `memory.high` at half that peak. Record wall time, pressure and swap of both. Expected, if the cold assumption
  holds: under 30 % slower and pressure under `PRESSED`. If it does not hold, say so in section 3 and bring it to the
  person before the field test - it changes what a ceiling costs a suite.
- [ ] **Step 4: Commit** `docs(lane): PRESSED measured on the bench, and a real suite under half its peak`.

---

## After the plan (not tasks of it)

- The final review of the whole branch on the most capable model; fix commits get their own review.
- The field test, batch 1 again, then batches 2 and 3: the demo projects' load tier taught `touch = "hot" | "cold"`,
  a sampler recording each run's `memory.current`, `memory.events` and `memory.pressure`, immortality at 3 minutes
  with machine.toml backed up and restored byte for byte.
- Merge after a green review and a green suite; tag and release only with the person's ok.
