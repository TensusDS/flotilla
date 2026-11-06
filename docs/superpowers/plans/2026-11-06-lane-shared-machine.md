# The lane shares the machine by value (0.30.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (the person chose native execution).
> Steps use checkbox (`- [ ]`) syntax.

**Goal:** runs are admitted by the memory free now, ordered by a priority built from their purpose, wait and run
time, and a younger, cheaper run is frozen - never killed - to let a higher one in; `lane_capacity` stops being the
gate.

**Architecture:** a pure policy (`flotilla/lane/policy.py`: runs + machine -> decision) decides inside the journal's
lock, as the grant does today. `flotilla/lane/admission.py` turns bookings and a machine reading into the policy's
inputs. `flotilla/lane/freeze.py` freezes the holder's running command (a user systemd scope on Linux, a signal stop
elsewhere), and `flotilla/lane/watch.py` is the holder's thread that obeys freeze orders and asks to thaw. The old
grant stays, whole, as the fallback while an older flotilla waits.

**Tech Stack:** Python 3.11 stdlib, pytest; `systemd-run --user --scope`, cgroup v2 `cgroup.freeze` and
`memory.reclaim`; `SIGSTOP`/`SIGCONT`.

**Spec:** docs/specs/2026-11-06-lane-shared-machine-design.md

## Global Constraints

- English only in the repository (`tools/check_no_cyrillic.py`). Stdlib only; `requires-python = ">=3.11"`.
- Suite: `uv run --with pytest python -m pytest -p no:cacheprovider tests/`. Never two full suites at once.
- Never write machine.toml; new keys are read only, and added to `PERSON_KEYS` (`flotilla/onboard/machine.py:96`) so
  a rewrite keeps them.
- The policy's constants are code, not settings (spec 6): weights `0.2 / 0.3 / 0.5`, wait saturation `600 s`,
  grace `60 s`, margin `0.0`, types `push 1.0 · handover 0.7 · review 0.5 · run 0.3`.
- Settings and defaults (spec 6): `lane_cpu_factor 1.5`, `lane_light_reserve 0.2`, `lane_immortal_minutes 15`,
  `lane_freeze` on, `lane_max_runs 10`. `lane_capacity` is read only by the fallback.
- Journal compatibility (spec 7): a freezing or frozen booking stays a `held` record with field `frozen`
  (`ordered` / `frozen` / absent). No new state names. `RULE` becomes 4.
- Every figure the journal holds is cleaned on fold like today's (`_clean`): wrong type or out of range folds as
  unknown, never a crash.
- Builder's numbers, chosen here, each a constant with a comment saying it awaits the field test (spec 9):
  reservation window `30 s`; default memory of a never-measured run `1024 MB`; heavy mark `0.2` of the memory the
  lane can give; swap margin `256 MB`; reclaim margin `128 MB`; answer bound for a freeze order `20 s`; let-go bound
  for a signal stop `120 s`.
- Version 0.30.0 in `pyproject.toml`, `.claude-plugin/plugin.json`, `flotilla/__init__.py`, README's pinned example.
  Tag and release only with the person's ok.

## Review Focus

1. **The scope cannot be made at run time** (no user bus, user manager restarted after the probe): the command must
   still run, unwrapped and freezable by signal, never red from the wrapper. Test in Task 5.
2. **The waiter that ordered a freeze dies or leaves:** the frozen run must thaw through the queue, not stay frozen.
   Test in Task 6.
3. **The holder is killed while its command is frozen:** the command must not stay frozen in swap for ever; stopping
   a frozen command ends it, and `sweep` thaws a dead holder's unit. Tests in Tasks 5 and 7.
4. **Memory cannot be read** (no MemAvailable, `vm_stat` failed): admit by the run cap and the heavy CPU bound only,
   freeze nothing, say so. Test in Task 6.
5. **Records of an older flotilla in a new journal:** a held booking with no `started`, no `asked`, `rule` 3 - it is
   read with `since` as both, never frozen, its memory taken from the process table. Test in Task 6.

---

### Task 1: The journal carries value, freeze orders and run time

**Files:**
- Modify: `flotilla/lane/book.py` (`RULE`, `FIELDS`, `MEASURED`, `BOUNDS`, `_clean`, `Booking`, `enqueue`)
- Test: `tests/test_lane_book.py`

**Interfaces:**
- Produces: `book.RULE == 4`; `Booking` fields `kind: str = ""`, `asked: str = ""`, `started: str = ""`,
  `ran_s: float | None`, `frozen: str = ""`, `order_by: str = ""`, `ordered_at: str = ""`,
  `reclaim_mb: int | None`, `frozen_at: str = ""`, `held_mb: int | None`, `frozen_s: float | None`,
  `freezes: int = 0`, `heavy: bool = False`, `pinned: bool = False`, `unanswered: bool = False`,
  `freeze_kind: str = ""`, `unit: str = ""`, `group: int | None`, `peak_cores: float | None`;
  `Book.enqueue(..., kind: str = "", pinned: bool = False)` writing `asked` = now.
- `FROZEN_STATES = ("ordered", "frozen")`; `KINDS = ("push", "handover", "review", "run")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_new_booking_is_rule_4_and_records_when_it_first_asked(lane):
    lanes, _ = lane
    item = lanes.enqueue("main session 1", "suite", pid=1, mark="m", kind="push")
    assert item.rule == 4 and item.kind == "push" and book._when(item.asked) is not None

def test_asked_survives_the_held_record_that_resets_since(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=1, mark="m")
    held = lanes.grant(item.id, slots=1)
    assert held.asked == item.asked

def test_freeze_fields_fold_cleaned(lane):
    found = book.fold([{"at": "2026-11-06T10:00:00+00:00", "booking": "b1", "state": "held", "frozen": "nonsense",
                        "ran_s": -5, "reclaim_mb": "x", "freezes": True, "heavy": "yes", "kind": "boss",
                        "order_by": "b2\x1b[2J", "peak_cores": 9999, "group": -1}])["b1"]
    assert (found.frozen, found.ran_s, found.reclaim_mb, found.freezes, found.heavy, found.kind) == \
        ("", None, None, 0, False, "run")
    assert "\x1b" not in found.order_by and found.peak_cores is None and found.group is None

def test_a_frozen_booking_is_still_a_holder_to_an_older_fold(lane):
    found = book.fold([{"at": "2026-11-06T10:00:00+00:00", "booking": "b1", "state": "held", "frozen": "frozen"}])
    assert [item.id for item in book.Book.holders(None, found)] == ["b1"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_book.py -k "rule_4 or asked or freeze_fields or older_fold"`
Expected: FAIL (`enqueue() got an unexpected keyword argument 'kind'`, `rule == 3`).

- [ ] **Step 3: Implement the fields in `flotilla/lane/book.py`**

Cleaning rules: `frozen` in `FROZEN_STATES` else ""; `kind` in `KINDS` else "run"; `freeze_kind` in
`("scope", "signal")` else ""; `asked/started/ordered_at/frozen_at` kept only when `_aware`; `order_by` and `unit`
`visible(...[:80])`; `heavy/pinned/unanswered` `is True`; `freezes` an int >= 0 (not bool) else 0; `group` an int
>= 2 else None; `ran_s`, `frozen_s` in `BOUNDS` as `(0.0, 7 * 86400.0)`; `reclaim_mb`, `held_mb` as `peak_mb`;
`peak_cores` as `cores`; `peak_cores` joins `MEASURED`. Update the `RULE` comment with what rule 4 knows (value,
freezing). `enqueue` writes `asked` always, `kind` and `pinned` when given.

- [ ] **Step 4: Run the lane book tests**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_book.py tests/test_lane_compact.py`
Expected: PASS. If a test pins `rule == 3`, it pinned the constant, not behaviour: change it to `book.RULE`.

- [ ] **Step 5: Commit**

`feat(lane): the journal carries a run's purpose, first ask, run time and freeze orders (rule 4)`

---

### Task 2: Peak cores, a holder's memory now, and history that knows them

**Files:**
- Modify: `flotilla/lane/peak.py`, `flotilla/lane/measure.py`, `flotilla/lane/estimate.py`,
  `flotilla/onboard/firstrun.py` (`TierRun.peak_cores`), `flotilla/ledger/receipts.py` (`_tier_result`, `measured_of`)
- Test: `tests/test_lane_peak.py`, `tests/test_lane_measure.py`, `tests/test_lane_estimate.py`

**Interfaces:**
- Produces: `peak.tree_rss_mb(pid: int, *, proc_root=Path("/proc"), run=subprocess.run) -> int | None` (the pid and
  every descendant, rounded up); `GroupPeak.peak_cores: float | None` (largest CPU seconds over wall seconds between
  two samples at least `SAMPLE` apart); `Usage.peak_cores: float | None`;
  `Measurer(..., paused: Callable[[], float] = lambda: 0.0)` - seconds and cores count wall time less `paused()`
  growth between enter and exit; `estimate.Sample.peak_cores`, `estimate.Estimate.peak_cores: float | None = None`
  (max over the samples, like `_peak`).

- [ ] **Step 1: Write the failing tests**

```python
def test_peak_cores_is_the_busiest_stretch_not_the_average():
    readings = iter([(1000, 0.0), (1000, 8.0), (1000, 9.0), (1000, 10.0)])   # (kB, group CPU s) a second apart
    meter = peak.GroupPeak(1, sample=1.0, read=None, sampler=lambda group: next(readings))
    for _ in range(4):
        meter._take()
    assert meter.peak_cores == 8.0

def test_tree_rss_counts_the_wrapper_and_its_command(tmp_path):
    fake_proc(tmp_path, {10: (1, 10, 100), 11: (10, 11, 300), 12: (1, 12, 999)})   # pid: (ppid, pgrp, pages)
    assert peak.tree_rss_mb(10, proc_root=tmp_path, page_kb=4) == 2   # 400 pages * 4 kB, rounded up

def test_frozen_time_is_not_run_time():
    clock = iter([0.0, 100.0])
    paused = iter([0.0, 60.0])
    m = measure.Measurer(1, clock=lambda: next(clock), cpu=iter([0.0, 40.0]).__next__, stat=lambda: None,
                         peak=NullPeak(), paused=lambda: next(paused))
    with m:
        pass
    assert m.usage.seconds == 40.0 and m.usage.cores == 1.0

def test_history_carries_peak_cores(...):   # a released booking with peak_cores 6.0 and cores 1.2
    assert est.estimate(ladder, hist, project="p").peak_cores == 6.0
```

`fake_proc` and `NullPeak` follow the helpers already in these test files; add them where missing. `sampler`
returns `(rss_kb, cpu_seconds)` for the group; the default reads both from `/proc/<pid>/stat` fields 14-15 (utime,
stime, in clock ticks `os.sysconf("SC_CLK_TCK")`) and `ps -o time=` where there is no `/proc`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_peak.py tests/test_lane_measure.py tests/test_lane_estimate.py`
Expected: FAIL (`unexpected keyword argument 'sampler'`, no `tree_rss_mb`, no `paused`).

- [ ] **Step 3: Implement**

`GroupPeak._loop` calls one `_take()` per sample: read `(kb, cpu)`, keep the peak memory, and for the pair with the
previous reading `(cpu - cpu_prev) / (t - t_prev)` when `t - t_prev >= self.sample`, kept as `peak_cores` (rounded to
2). `read=` stays accepted for callers that only want memory. `tree_rss_mb` reuses `_members` with the pid as root.
Every place that builds a measured dict (`lane run` in `commands._run`, `_tier_result`, `measured_of`) carries
`peak_cores`.

- [ ] **Step 4: Run the lane and receipt tests**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/ -k "lane or receipt or firstrun"`
Expected: PASS.

- [ ] **Step 5: Commit**

`feat(lane): measure a run's busiest stretch of cores, and a holder's memory now`

---

### Task 3: The policy, a pure function

**Files:**
- Create: `flotilla/lane/policy.py`
- Test: `tests/test_lane_policy.py`

**Interfaces:**
- Produces:

```python
WAITING, RUNNING, ORDERED, FROZEN = "waiting", "running", "ordered", "frozen"
TYPES = {"push": 1.0, "handover": 0.7, "review": 0.5, "run": 0.3}
WEIGHTS = (0.2, 0.3, 0.5); WAIT_FULL_S = 600.0; GRACE_S = 60.0; MARGIN = 0.0
HEAVY_SHARE = 0.2; SWAP_MARGIN_MB = 256; RECLAIM_MARGIN_MB = 128

@dataclass(frozen=True)
class Run:
    id: str
    state: str            # WAITING, RUNNING, ORDERED, FROZEN
    mem_mb: int           # running: held now (or reserved, if more); waiting/frozen: what it needs
    heavy: bool
    par: float            # cores it can use, for the heavy CPU bound
    kind: str = "run"
    waited_s: float = 0.0       # since it first asked
    ran_s: float = 0.0          # run time across thaws
    since_start_s: float = 0.0  # since its last start or thaw
    order: int = 0              # its place in line
    fair: tuple = ()            # tie-break key between equal priorities (fair share), smaller first
    pinned: bool = False        # never a victim: by hand, a `freeze = false` tier, an older flotilla's
    freezable: bool = True      # its wrapper can obey an order

@dataclass(frozen=True)
class Machine:
    cpus: int
    give_mb: int | None   # the memory the lane can give (spec 2.2); None: memory cannot be read
    free_mb: int | None   # free now, less the floor and the reservation for just-started runs
    swap_free_mb: int | None
    cpu_factor: float = 1.5
    light_reserve: float = 0.2
    immortal_s: float = 900.0
    max_runs: int = 10
    freeze: bool = True

@dataclass(frozen=True)
class Decision:
    start: bool
    freeze: tuple = ()    # ((victim id, MB to reclaim), ...)
    why: str = ""         # what it waits for, named (spec 5); "" when it starts

def priority(run: Run, immortal_s: float) -> float
def immortal(run: Run, immortal_s: float) -> bool        # pinned, or ran_s >= immortal_s
def is_heavy(mem_mb: int, give_mb: int | None, was: bool = False) -> bool   # sticky; False when give is None
def decide(runs: list[Run], machine: Machine, mine: str, *, may_freeze: bool = True) -> Decision
```

- [ ] **Step 1: Write the failing tests** - one per rule of spec 2 and anti-goal of spec 3. A helper
  `run(id, state, mem, heavy=False, par=1, **kw)` and `SERVER = Machine(cpus=8, give_mb=9000, free_mb=..., swap_free_mb=8000)`.

```python
def test_priority_is_the_formula():
    r = Run("b1", WAITING, 100, False, 1, kind="push", waited_s=300, ran_s=450)
    assert priority(r, 900) == pytest.approx(0.2 * 1.0 + 0.3 * 0.5 + 0.5 * 0.5)

def test_the_first_run_on_an_empty_machine_starts_whatever_its_size():
    assert decide([run("b1", WAITING, 50_000)], machine(free=1000), "b1").start

def test_a_run_that_does_not_fit_free_memory_waits_and_says_so():
    d = decide([run("h", RUNNING, 6000), run("b2", WAITING, 2000)], machine(free=1200), "b2")
    assert not d.start and "memory: 2000 MB needed, 1200 MB free" in d.why

def test_the_second_heavy_waits_past_the_cpu_bound():   # 8 + 8 > 1.5 * 8
    d = decide([run("h", RUNNING, 2000, True, 8), run("b2", WAITING, 2000, True, 8)], machine(free=6000), "b2")
    assert not d.start and "heavy runs' cores: 8 of 12" in d.why

def test_heavies_leave_twenty_percent_to_light_runs():   # 4000 + 3500 > 0.8 * 9000
    d = decide([run("h", RUNNING, 4000, True, 2), run("b2", WAITING, 3500, True, 2)], machine(free=5000), "b2")
    assert not d.start and "reserve 1800 MB for light runs" in d.why

def test_a_light_run_starts_in_the_reserve_beside_heavies():
    runs = [run("h", RUNNING, 7000, True, 4), run("b2", WAITING, 500)]
    assert decide(runs, machine(free=1500), "b2").start

def test_light_passes_a_heavy_that_does_not_fit():
    runs = [run("h", RUNNING, 4000, True, 8), run("b2", WAITING, 4000, True, 8, order=2),
            run("b3", WAITING, 300, order=3)]
    assert decide(runs, machine(free=3000), "b3").start

def test_no_heavy_passes_a_higher_heavy_that_does_not_fit():
    runs = [run("h", RUNNING, 3000, True, 6), run("b2", WAITING, 5000, True, 4, order=2, waited_s=600),
            run("b3", WAITING, 1900, True, 2, order=3)]
    d = decide(runs, machine(free=4000), "b3")
    assert not d.start and "behind heavy b2" in d.why

def test_a_light_waiter_freezes_the_lowest_younger_run():
    runs = [run("old", RUNNING, 1500, ran_s=600, since_start_s=600), run("young", RUNNING, 1500, ran_s=90,
            since_start_s=90), run("b3", WAITING, 1000, kind="push", waited_s=300)]
    d = decide(runs, machine(free=200), "b3")
    assert d.freeze == (("young", 800 + RECLAIM_MARGIN_MB),) and not d.start

def test_an_immortal_run_is_never_frozen():
    runs = [run("old", RUNNING, 3000, ran_s=900, since_start_s=900), run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0), "b2")
    assert d.freeze == () and "no candidate to freeze" in d.why

def test_a_heavy_waiter_never_freezes_a_heavy_run():
    runs = [run("h", RUNNING, 4000, True, 4, ran_s=120, since_start_s=120),
            run("b2", WAITING, 4000, True, 4, kind="push", waited_s=600)]
    assert decide(runs, machine(free=500), "b2").freeze == ()

def test_a_run_in_its_grace_minute_is_not_a_candidate():
    runs = [run("y", RUNNING, 2000, ran_s=30, since_start_s=30), run("b2", WAITING, 1000, kind="push")]
    assert decide(runs, machine(free=0), "b2").freeze == ()

def test_equal_priority_is_no_reason_to_freeze():   # margin 0: strictly lower only
    runs = [run("a", RUNNING, 2000, ran_s=120, since_start_s=120), run("b2", WAITING, 1000, ran_s=120)]
    assert decide(runs, machine(free=0), "b2").freeze == ()

def test_nothing_is_frozen_when_all_candidates_together_do_not_make_room():
    runs = [run("a", RUNNING, 500, ran_s=90, since_start_s=90), run("b2", WAITING, 3000, kind="push")]
    assert decide(runs, machine(free=1000), "b2").freeze == ()

def test_no_swap_freezes_nothing():
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90), run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0, swap=0), "b2")
    assert d.freeze == () and "swap cannot take 1128 MB" in d.why

def test_a_frozen_run_thaws_ahead_of_a_later_run_of_its_purpose():
    runs = [run("f", FROZEN, 1500, ran_s=300, waited_s=900, order=1), run("n", WAITING, 1500, order=5)]
    m = machine(free=1600)
    assert decide(runs, m, "f").start and not decide(runs, m, "n").start

def test_two_runs_do_not_freeze_each_other_in_turn():
    # b froze a; a, now frozen, outranks nothing that froze it: a runs again only when it fits, never by an order
    runs = [run("a", FROZEN, 2000, ran_s=200, waited_s=400), run("b", RUNNING, 2000, kind="push", ran_s=10,
            since_start_s=10, waited_s=300)]
    assert decide(runs, machine(free=100), "a").freeze == ()

def test_an_ordered_victim_is_not_ordered_again():
    runs = [run("v", ORDERED, 2000, ran_s=90, since_start_s=90), run("b3", WAITING, 1000, kind="push")]
    assert decide(runs, machine(free=0), "b3").freeze == ()

def test_ten_runs_at_once_at_most():
    runs = [run(f"r{n}", RUNNING, 10) for n in range(10)] + [run("b", WAITING, 10)]
    assert "runs at once: 10 of 10" in decide(runs, machine(free=8000), "b").why

def test_memory_unknown_admits_by_cap_and_cpu_only_and_freezes_nothing():
    runs = [run("a", RUNNING, 2000, ran_s=90, since_start_s=90), run("b", WAITING, 1000, kind="push")]
    d = decide(runs, Machine(cpus=8, give_mb=None, free_mb=None, swap_free_mb=8000), "b")
    assert d.start and d.freeze == ()

def test_freezing_off_orders_nothing():
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90), run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0, freeze=False), "b2")
    assert d.freeze == () and "freezing is off" in d.why

def test_heavy_is_sticky():
    assert is_heavy(100, 9000, was=True) and is_heavy(1800, 9000) and not is_heavy(1799, 9000)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_policy.py`
Expected: FAIL (`No module named 'flotilla.lane.policy'`).

- [ ] **Step 3: Implement `flotilla/lane/policy.py`**

`decide` replays admission in priority order, as every waiter would, and answers only for `mine`:

```text
queue = waiting + frozen runs, by (-priority, fair, order); pool = running + ordered runs; free = machine.free_mb
heavy_blocked = False; unfit_ahead = []
for r in queue:
    if r.heavy and heavy_blocked: why_of[r] = f"behind heavy {blocker}"; continue
    if fits(r, pool, free):               # allow-one; cap; memory; heavy CPU and 80 % bounds (only beside heavies)
        pool += [r]; free -= r.mem_mb     # those ahead take their room first
        if r.id == mine: return Decision(True)
        continue
    if r.heavy: heavy_blocked, blocker = True, r.id
    if r.id == mine: break
    unfit_ahead.append(r)
if mine did not fit and may_freeze and machine.freeze and memory is known
        and no r in unfit_ahead has victims (victims(r, ...) is empty for each):
    chosen = victims(mine, pool, free)    # lowest P first, until mine fits; () if all together do not make room
    if chosen fits the swap (swap_free - SWAP_MARGIN_MB >= sum of reclaims): return Decision(False, chosen, "...")
return Decision(False, (), why)
```

The class rules of spec 2.7a: light passes heavy, light passes light, no heavy passes a higher heavy. A heavy
may pass a light waiter that does not fit - the spec names no rule for that pair; this plan's call, because a light
run that does not fit has freezing to make room and ends in minutes (ledger it as a ruling when executing).

A victim is a `RUNNING` run, not pinned, freezable, not immortal, `since_start_s >= GRACE_S`, with
`priority + MARGIN < mine's`, and not both heavy. Reclaims: the memory mine still lacks (`mine.mem_mb - free`),
taken from victims in order, each victim's share `min(its mem, what is still lacking) + RECLAIM_MARGIN_MB`. With
memory unknown, `fits` skips the memory and 80 % checks. `why` texts, first failing check, verbatim from spec 5:
`memory: {need} MB needed, {free} MB free, reserve {reserve} MB for light runs`,
`heavy runs' cores: {used:g} of {limit:g}`, `runs at once: {n} of {cap}`, `behind heavy {id}`,
`no candidate to freeze: {reason}` (each pool run's reason, e.g. "b4 is immortal"), `swap cannot take {n} MB`,
`freezing is off`.

- [ ] **Step 4: Run them to verify they pass**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_policy.py`
Expected: PASS.

- [ ] **Step 5: Commit**

`feat(lane): the policy - admission by free memory, priority by value, freezing the younger run`

---

### Task 4: The model's queues replayed through the policy

**Files:**
- Create: `tests/lane_replay.py` (the model's queue generator and a discrete replay that calls `policy.decide`),
  `tests/test_lane_policy_model.py`

**Interfaces:**
- Consumes: `policy.decide`, `policy.Run`, `policy.Machine` (Task 3).
- Produces: `replay(seed: int, spread: float, mix: str = "tight") -> dict` with keys `makespan`, `light_wait_p95`,
  `freezes`, `max_freezes_one`, `heavy_by_heavy`, `immortal_frozen`, `unfinished` (minutes; counts).

The generator is `make_jobs` of the brainstorm's model, copied verbatim (seeds must reproduce the model's queues):
heavy 60-360 core-minutes on 4/6/8 cores, light 0.2-6 on 1/1/1/2, `MIXES["tight"] = ((2.0, 6.0), (0.2, 1.5))`
GB, type `rng.randrange(4)` over `push, handover, review, run`. Machine: 8 cores, 9000 MB, swap 8000 MB. Step
0.25 min; each step every queued run (waiting or frozen) asks `decide` in queue order and its answer is applied at
once (start; freeze the victims, their memory to swap); CPU is water-filled as in the model.

- [ ] **Step 1: Write the failing test**

```python
CASES = [(seed, spread) for seed in range(10) for spread in (0, 60)]

def test_the_policy_lands_in_the_models_band():
    rows = [replay(seed, spread) for seed, spread in CASES]
    # the model on these 20 queues (2026-11-06): makespan mean 182.8 min, light wait p95 mean 5.48 min,
    # 0.45 freezes a queue, never twice the same run
    assert all(r["unfinished"] == 0 for r in rows)
    assert 165 <= statistics.mean(r["makespan"] for r in rows) <= 200
    assert statistics.mean(r["light_wait_p95"] for r in rows) <= 6.6

def test_the_anti_goals_hold_on_every_queue():
    for seed, spread in CASES:
        r = replay(seed, spread)
        assert r["heavy_by_heavy"] == 0 and r["immortal_frozen"] == 0 and r["max_freezes_one"] <= 2
```

- [ ] **Step 2: Run it**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_policy_model.py`
Expected: FAIL first on the missing helper; then, once `tests/lane_replay.py` exists, PASS. A result outside the
band is a finding about Task 3's policy (its class rules differ from the model's free backfill): find which rule
moved it, and ledger a ruling with the number before widening any bound.

- [ ] **Step 3: Commit**

`test(lane): the model's thirty-run queues replayed through the real policy`

---

### Task 5: Freezing a command

**Files:**
- Create: `flotilla/lane/freeze.py`
- Test: `tests/test_lane_freeze.py` (fakes), `tests/test_lane_freeze_scope.py` (the real seam, skipped without a
  delegated memory controller)

**Interfaces:**
- Produces:

```python
SCOPE, SIGNAL, OFF = "scope", "signal", ""
def backend(*, setting: bool = True, platform: str = sys.platform, which=shutil.which, run=subprocess.run,
            proc_self=Path("/proc/self/cgroup"), root=Path("/sys/fs/cgroup")) -> tuple[str, str]
    # (kind, why): scope where systemd-run exists, the user manager's cgroup lists `memory`, `cgroup.freeze` exists
    # and `systemd-run --user --scope --quiet true` succeeds once (cached per process); else signal; OFF when the
    # setting is off. `why` names the reason for `flotilla lane` and doctor.
def swap_free_mb(meminfo=Path("/proc/meminfo"), run=subprocess.run) -> int | None   # SwapFree; macOS `sysctl vm.swapusage`

class Slot:
    """The holder process's one running command - what a freeze order acts on."""
    def __init__(self, kind: str, *, run=subprocess.run, root=Path("/sys/fs/cgroup"), clock=time.monotonic,
                 proc_self=Path("/proc/self/cgroup"), table=None)   # table: pid -> (ppid, pgrp, kB), for signal
    def contain(self, argv: list[str], *, booking: str) -> list[str]   # scope: systemd-run ... -- argv; else argv
    def attach(self, pid: int) -> None ; def detach(self) -> None
    def freeze(self, reclaim_mb: int) -> dict   # {"freeze_kind", "unit", "group", "held_mb"}; idle slot: frozen at once
    def thaw(self) -> None
    def frozen(self) -> bool
    def frozen_seconds(self) -> float           # all frozen time in this process so far
    def wait_thawed(self, sleep=time.sleep) -> None   # a new command never starts while frozen
def thaw_unit(unit: str, *, run=subprocess.run, root=Path("/sys/fs/cgroup")) -> bool   # for sweep
SLOT: Slot   # module global, created lazily by slot()
def slot() -> Slot
```

Scope argv: `["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit=flotilla-lane-{booking}-{os.getpid()}-{n}", "-p", "CPUWeight=100", "--", *argv]`.
Freeze on scope: `cgroup.freeze` = 1, wait for `frozen 1` in `cgroup.events` (at most 5 s), then
`memory.reclaim` = `f"{reclaim_mb}M"` when `reclaim_mb > 0`; an `OSError` with `errno.EAGAIN` is success
(it reclaimed less than asked). Signal: `SIGSTOP` / `SIGCONT` to **every process of the attached pid's tree**
(`peak._members` over the process table), not only its group - a test runner's browser and a nested `lane run`
live in groups of their own. The cgroup path comes from `systemctl --user show -p ControlGroup --value <unit>`.

`contain` never wraps a command already inside a lane scope (`/proc/self/cgroup` names a `flotilla-lane-` unit): a
nested run stays in its outer run's scope and is frozen with it (spec 4.6), instead of escaping into a scope of its
own.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_user_systemd_means_signal(tmp_path):
    assert freeze.backend(which=lambda name: None, platform="linux")[0] == freeze.SIGNAL

def test_macos_is_signal_and_the_setting_turns_it_off():
    assert freeze.backend(platform="darwin")[0] == freeze.SIGNAL
    assert freeze.backend(setting=False, platform="darwin")[0] == freeze.OFF

def test_a_scope_probe_that_fails_falls_back_to_signal(tmp_path):   # review focus 1
    assert freeze.backend(which=lambda n: "/usr/bin/" + n, run=failing_run, proc_self=..., root=...)[0] == freeze.SIGNAL

def test_eagain_from_reclaim_is_not_an_error(tmp_path):
    s = freeze.Slot(freeze.SCOPE, root=fake_cgroup(tmp_path, reclaim_raises=errno.EAGAIN), run=fake_systemctl)
    s.attach(4242)
    assert s.freeze(512)["freeze_kind"] == "scope" and s.frozen()

def test_an_idle_slot_is_frozen_at_once_and_blocks_the_next_command():
    s = freeze.Slot(freeze.SIGNAL)
    s.freeze(0)
    assert s.frozen()
    s.thaw()
    s.wait_thawed()   # returns at once once thawed

def test_a_command_inside_a_lane_scope_is_not_wrapped_again(tmp_path):
    cg = tmp_path / "cgroup"
    cg.write_text("0::/user.slice/user-1000.slice/user@1000.service/app.slice/flotilla-lane-b3-77-1.scope\n")
    s = freeze.Slot(freeze.SCOPE, proc_self=cg)
    assert s.contain(["pytest"], booking="b9") == ["pytest"]

def test_a_signal_stop_reaches_a_child_in_a_group_of_its_own(): ...   # fake table: pid 10 -> 11 (own pgrp); both stopped

def test_frozen_seconds_add_up_across_freezes():
    t = iter([0, 10, 20, 50])
    s = freeze.Slot(freeze.SIGNAL, clock=lambda: next(t))
    s.freeze(0); s.thaw(); s.freeze(0); s.thaw()
    assert s.frozen_seconds() == 40
```

Seam test (`test_lane_freeze_scope.py`, skipped unless `freeze.backend()[0] == SCOPE`): start
`python3 -c` a child holding 256 MB in a scope through `Slot.contain` + `Popen(start_new_session=True)`; assert its
`/proc/<pid>/cgroup` names the unit; `freeze(300)` makes its RSS (via `peak.tree_rss_mb`) fall under 64 MB; `thaw()`;
then stopping it with `run._stop` while frozen ends the group within 6 s (review focus 3); the unit is gone
afterwards (`--collect`). The child script lives in `tests/fixtures/hold_memory.py`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_freeze.py tests/test_lane_freeze_scope.py`
Expected: FAIL (`No module named 'flotilla.lane.freeze'`).

- [ ] **Step 3: Implement `flotilla/lane/freeze.py`**, then make `run._stop` thaw before it signals
  (`slot().thaw()` when `slot().frozen()`), so a frozen group receives its SIGTERM.

- [ ] **Step 4: Run them to verify they pass** (the seam test runs on this machine; read that it did not skip)

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_freeze.py tests/test_lane_freeze_scope.py -rs`
Expected: PASS, and no `SKIPPED` line for the scope test.

- [ ] **Step 5: Commit**

`feat(lane): freeze a run's command - a user scope and reclaim on Linux, a signal stop elsewhere`

---

### Task 6: Bookings to the policy, and the grant by value

**Files:**
- Create: `flotilla/lane/admission.py`
- Modify: `flotilla/lane/book.py` (`Book.grant`, new `Book.frozen`, `Book.thaw`, `Book.sweep`)
- Test: `tests/test_lane_admission.py`, `tests/test_lane_book.py`

**Interfaces:**
- Consumes: `policy.*` (Task 3), `freeze.thaw_unit` (Task 5), Task 1's fields, `estimate.Estimate.peak_cores`.
- Produces:

```python
WINDOW_S = 30.0; DEFAULT_MB = 1024; ANSWER_S = 20.0

@dataclass(frozen=True)
class Value:   # what a grant by value reads, built each poll from settings and the machine
    cpus: int
    cpu_factor: float
    light_reserve: float
    immortal_s: float
    max_runs: int
    freeze: str                                   # freeze.SCOPE / SIGNAL / OFF
    available_mb: Callable[[], int | None]
    floor_mb: int
    swap_free_mb: Callable[[], int | None]
    tree_mb: Callable[[int], int | None]          # a holder's process tree, now
    foreign: int = 0

def by_value(found: dict, live) -> bool           # no live waiter under a rule below 4 (spec 7)
def view(found: dict, hist, value: Value, *, now: dt.datetime, live, mine: str) -> tuple[list[policy.Run], policy.Machine]
```

- `Book.grant(booking_id, *, slots, by_hand=False, share="fifo", skip=(), room=None, value: Value | None = None)`:
  with `value` and `by_value(found, live)`, decides by `policy.decide`; else today's code unchanged (the fallback,
  `lane_capacity` included). A start writes `held` with `path="value: ..."`, `started` = now, `heavy`; a freeze
  writes, for each victim, a `held` record `frozen="ordered"`, `order_by`, `ordered_at`, `reclaim_mb`, and returns
  None. `self.waits_for` = the decision's `why`. An order unanswered past `ANSWER_S` is lapsed in the same
  transaction: `held`, `frozen=""`, `unanswered=True`. A run seen heavy by its memory now gets `heavy=True` written.
- `Book.frozen(booking_id, *, freeze_kind, unit, group, held_mb) -> Booking`: `held`, `frozen="frozen"`,
  `frozen_at`, `ran_s` += now - `started`, `freezes` + 1.
- `Book.thaw(booking_id, value, *, force=False) -> Booking | None`: decides for the frozen run as for a waiter;
  on start (or `force`): `held`, `frozen=""`, `started` = now, `frozen_s` += now - `frozen_at`.
- `Book.sweep`: a dead holder with `frozen` and `unit` gets `freeze.thaw_unit(unit)` before its release; with
  `group`, `os.killpg(group, SIGCONT)` (errors ignored).

`view` maps each live booking: held with `frozen=""` -> RUNNING, `frozen="ordered"` -> ORDERED, `"frozen"` -> FROZEN,
waiting -> WAITING. Memory: running = `max(tree_mb(pid), expected)` while `started` is within `WINDOW_S`, else
`tree_mb(pid)`; waiting = history's peak (`estimate.for_booking(...).peak_mb`, any step) else `DEFAULT_MB`; frozen =
`held_mb`. `give_mb = available - floor + Σ tree_mb(live holders)`; `free_mb = available - floor - Σ reservations`.
`par` = `peak_cores`, else `cores`, else `cpus / 2`. Waited = now - (`asked` or `since`); ran = `ran_s` + (now -
(`started` or `since`)) unless frozen. A booking taken by hand (no pid): heavy, `par = cpus`,
`mem = (1 - light_reserve) * give`, pinned. A holder under a rule below 4: pinned, not freezable. `pinned` field:
pinned and heavy. `fair` = the fair-share key of `fair_choice` when `share == "fair"`, else `()`.

- [ ] **Step 1: Write the failing tests**

```python
def test_an_older_waiter_turns_the_lane_back_to_the_count(lane):   # spec 7
    ...  # a live waiter folded with rule 3: grant(..., value=v) admits by slots exactly as before

def test_a_run_is_admitted_by_free_memory_not_by_capacity(lane):
    ...  # slots=1, one heavy held, a 500 MB waiter with 3000 MB free: held, path starts with "value"

def test_a_freeze_is_ordered_and_the_waiter_waits_for_the_memory(lane):
    ...  # grant returns None; the victim's record has frozen == "ordered", order_by == mine, reclaim_mb > 0

def test_an_unanswered_order_lapses_and_is_never_ordered_again(lane):
    ...  # clock past ANSWER_S: victim frozen == "" and unanswered; the next grant orders nobody

def test_a_frozen_run_whose_waiter_left_thaws_through_the_queue(lane):   # review focus 2
    ...  # waiter leaves; thaw(victim, value) with room: frozen == "", frozen_s > 0

def test_frozen_run_time_does_not_count(lane):
    ...  # started 10:00, frozen 10:05, thawed 10:20, now 10:21: view's ran_s == 360

def test_an_older_holder_is_read_with_since_and_never_frozen(lane):   # review focus 5
    ...  # held, rule 3, no started/asked: Run.pinned, waited/ran from since, mem from tree_mb

def test_memory_unread_freezes_nothing(lane):   # review focus 4
    ...  # available_mb -> None: grant admits within the cap, writes no order, waits_for names it

def test_a_booking_by_hand_is_heavy_with_the_full_cores(lane): ...
def test_sweep_thaws_a_dead_holders_unit(lane, monkeypatch): ...   # review focus 3
```

Each test builds a `Value` with fixed numbers and `tree_mb=lambda pid: {...}[pid]`; the book's `clock` is driven by
the test. The ellipses above are the scenario each test sets up; write the calls and asserts as in
`tests/test_lane_book.py`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_admission.py tests/test_lane_book.py`
Expected: FAIL (`No module named 'flotilla.lane.admission'`, `grant() got an unexpected keyword argument 'value'`).

- [ ] **Step 3: Implement**, then **Step 4: run** the same command plus `tests/test_lane_acquire.py`.
Expected: PASS (the existing grant tests pass no `value`, so they test the fallback, unchanged).

- [ ] **Step 5: Commit**

`feat(lane): grant by value inside the journal's lock, with freeze orders, thawing and the older-flotilla fallback`

---

### Task 7: The holder obeys orders, and its command's clock stops while frozen

**Files:**
- Create: `flotilla/lane/watch.py`
- Modify: `flotilla/lane/acquire.py` (`acquire` passes `value`; `held` starts and stops the watch),
  `flotilla/lane/run.py` (`execute`), `flotilla/onboard/firstrun.py` (`run_tier`)
- Test: `tests/test_lane_watch.py`, `tests/test_lane_run.py`, `tests/test_lane_acquire.py`

**Interfaces:**
- Consumes: `Book.frozen`, `Book.thaw` (Task 6); `freeze.slot()`, `Slot.*` (Task 5); `Measurer(paused=...)` (Task 2).
- Produces:
  - `watch.Watch(lanes, booking_id: str, slot, value_of: Callable[[Reading | None], Value], *, clock=time.monotonic, sleep=time.sleep, let_go_s=120.0)`
    with `start()`, `stop()`, and `step() -> str` (one look; returns `"froze"`, `"thawed"`, `"let go"` or `""`) for
    tests. It calls `value_of(None)`: a thaw reads memory, not the CI queue.
  - `acquire(..., value: Callable[[Reading | None], Value] | None = None)` calls `lanes.grant(..., value=value(reading))`
    beside `room`; `held(..., value=None)` starts a `Watch` after a grant whose `path` starts with `value` and stops
    it in `finally`.
  - `RunResult.frozen_s: float = 0.0`, `RunResult.freezes: int = 0`; `TierRun.frozen_s: float = 0.0`.

`step`: reads its booking; `frozen == "ordered"` and `order_by` is this booking's waiter -> `slot.freeze(reclaim_mb)`
then `lanes.frozen(...)`; `frozen == "frozen"` -> `lanes.thaw(id, value_of())`, and on a booking returned
`slot.thaw()`; a `signal` freeze held past `let_go_s` while the ordering booking still waits -> `thaw(force=True)`
and `slot.thaw()`. The loop wakes on journal stamps like `_nap`, and looks at least every 5 s.

`execute`: `argv = slot().contain(command, booking=os.environ.get(acquire.ENV, ""))` only under a booking;
`slot().attach(process.pid)` after start, `detach()` after wait; the ceiling is a daemon thread checking every 0.5 s
`monotonic() - began - (slot().frozen_seconds() - frozen_at_start) >= ceiling`; `Measurer(paused=...)` with the
same difference. `run_tier`: the same `contain`/`attach`, `slot().wait_thawed()` before it starts, and
`communicate(timeout=1.0)` in a loop whose budget is `timeout` plus the frozen seconds.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_watch_freezes_its_command_on_an_order(lane):
    ...  # write an order on the booking; step() == "froze"; the fake slot froze with that reclaim; record frozen

def test_the_watch_thaws_when_the_policy_lets_it(lane): ...          # step() == "thawed"; slot thawed

def test_a_signal_stop_is_let_go_when_its_waiter_never_starts(lane):  # spec 4.2, macOS
    ...  # freeze_kind "signal", clock past let_go_s, ordering booking still waiting: step() == "let go"

def test_the_ceiling_does_not_count_frozen_time(tmp_path, monkeypatch):
    ...  # a fake slot reporting 10 s frozen; a 1.5 s command with ceiling 1.0 reaches its end, not the ceiling

def test_a_red_run_after_a_freeze_says_so():
    ...  # RunResult with frozen_s 180 and verdict red: commands prints "red after a freeze of 3 min"

def test_a_tier_does_not_start_while_its_holder_is_frozen(): ...
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_watch.py tests/test_lane_run.py tests/test_lane_acquire.py tests/test_onboard_firstrun.py`
Expected: FAIL (`No module named 'flotilla.lane.watch'`).

- [ ] **Step 3: Implement**, **Step 4: run** the same command. Expected: PASS.

- [ ] **Step 5: Commit**

`feat(lane): a holder freezes its own command on an order, thaws by the queue, and its ceiling stops meanwhile`

---

### Task 8: Wiring - purpose, pinned tiers, settings, receipts

**Files:**
- Modify: `flotilla/lane/commands.py` (`booked`, `_take`, settings readers, `value_for`, `_run`'s verdict line),
  `flotilla/ledger/commands.py:346` (`booked(..., purpose=...)` already passes purpose), `flotilla/ledger/receipts.py`
  (tier `frozen_s` into the receipt, printed beside the tier), `flotilla/onboard/machine.py` (`PERSON_KEYS`)
- Test: `tests/test_lane_cli.py`, `tests/test_ledger_receipts.py`

**Interfaces:**
- Produces: `commands.light_reserve() -> float` (0.2; 0-0.5), `commands.immortal_minutes() -> float` (15; 1-240),
  `commands.freeze_on() -> bool` (True unless `lane_freeze` is the boolean false), `commands.max_runs() -> int`
  (10; 1-64), each "the default when unset, out of bounds, the wrong type or unreadable", like `cpu_factor`;
  `commands.value_for(profile: dict) -> Callable[[Reading | None], admission.Value]` (no reading: no foreign runs
  counted);
  `commands.kind_of(purpose: str, who: str, root) -> str` - `push` / `handover` from the purpose, `review` when
  `posts.post_for_session(load_posts(root), who)` is the `reviewer` post, else `run`.
- `booked` passes `kind`, `pinned` (any tier in `will_run` whose profile entry says `freeze = false`), and
  `value=value_for(profile)`; `_take` passes `value` too (by hand: heavy and pinned by `view`).

- [ ] **Step 1: Write the failing tests**

```python
def test_settings_fall_back_to_their_defaults(monkeypatch):
    for bad in ({"lane_light_reserve": 2}, {"lane_light_reserve": True}, {"lane_max_runs": 0}, {}):
        monkeypatch.setattr(commands, "_machine", lambda: bad)
        assert (commands.light_reserve(), commands.max_runs(), commands.immortal_minutes()) == (0.2, 10, 15)

def test_lane_freeze_false_turns_freezing_off(monkeypatch): ...
def test_a_reviewer_session_books_as_review(tmp_path): ...   # kind_of("", "review session 3", root) == "review"
def test_a_push_receipt_books_as_push(...): ...
def test_a_freeze_false_tier_pins_the_receipts_booking(...): ...
def test_a_receipt_tier_red_after_a_freeze_says_so_in_the_receipt(...): ...   # "red after a freeze of 2 min"
def test_new_lane_keys_survive_a_machine_toml_rewrite(...): ...   # also lane_cpu_factor and lane_share: PERSON_KEYS lists neither today
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_cli.py tests/test_ledger_receipts.py tests/test_onboard_machine.py`
Expected: FAIL (no `light_reserve`, no `kind_of`).

- [ ] **Step 3: Implement**, **Step 4: run** the same command. Expected: PASS.

- [ ] **Step 5: Commit**

`feat(lane): runs book with their purpose and the machine's settings; a receipt notes a red after a freeze`

---

### Task 9: What `flotilla lane`, doctor and fleet sizing say

**Files:**
- Modify: `flotilla/lane/commands.py` (`_status`), `flotilla/lane/acquire.py` (`in_the_way` by value),
  `flotilla/doctor.py` (a `_lane` finding), `flotilla/fleet/sizing.py` (`lane_capacity` -> `max_runs`)
- Test: `tests/test_lane_cli.py`, `tests/test_doctor.py`, `tests/test_fleet_sizing.py`

**Interfaces:**
- Consumes: `admission.view`, `policy.priority`, `policy.decide` (why), `freeze.backend` (Tasks 3, 5, 6).

Status lines (spec 5), by example - the test asserts these substrings:
- `machine: 8 cores shared by the kernel; memory the lane can give 9.0 GB, 3.2 GB free now; swap 8.1 GB free;
  freezing: scope (Linux user systemd)` (or `signal (...)` / `off (machine.toml lane_freeze = false)`);
- `heavy runs: 4.5 of 12 cores in use (lane_cpu_factor 1.5), 5.1 of 7.2 GB (light reserve 20 %)`;
- per holder `P 0.62 (type 0.20, wait 0.30, run 0.12); heavy (memory now 3.1 GB); immortal from 10:42` and, frozen,
  `frozen since 10:31 by b14's order`; an unanswered order `b9 did not answer a freeze order`;
- per waiter `waits because: memory: 1200 MB needed, 400 MB free, reserve 1800 MB for light runs`;
- `lane_capacity = 1 in machine.toml no longer has an effect (0.30); runs share the machine by memory and priority`
  once, when the key is set; while an older flotilla waits:
  `a seat of a flotilla before 0.30 waits: lane_capacity counts every run until it goes`.

Doctor: `lane: lane_capacity no longer has an effect` (note) when set; `lane: freezing off` (warn, "recommended on:
a younger run is frozen, never killed, to let a higher one in") when `lane_freeze = false`; `lane: no swap - nothing
can be frozen` (note). Sizing: the test-runs cap reads `max_runs()` and says `the lane runs up to 10 at once, as
memory allows`.

- [ ] **Step 1: Write the failing tests** (substrings above; sizing's figure with `max_runs` 10 and the run's memory)
- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_lane_cli.py tests/test_doctor.py tests/test_fleet_sizing.py`
Expected: FAIL on the new substrings.

- [ ] **Step 3: Implement**, **Step 4: run** the same command. Expected: PASS.
- [ ] **Step 5: Commit**

`feat(lane): flotilla lane prints value, heaviness and freezes; doctor and sizing retire lane_capacity`

---

### Task 10: Docs, version, full suite

**Files:**
- Modify: `README.md` (the lane section: the rule, settings table of spec 6, freezing per platform, `lane_capacity`
  retired), `CHANGELOG.md` (`## 0.30.0 - <date>`), `skills/flotilla/SKILL.md` if it names `lane_capacity`,
  `TODO.md` (the lane items of the field test this closes: heaviness in seconds, new-path commands, narrowed
  variants, the budget line wording), version files of the Global Constraints.

- [ ] **Step 1:** `grep -rn "lane_capacity\|light run" README.md skills/ templates/ docs/*.md` and correct each place
  that describes the old gate as current (leave dated specs and the changelog's history as they are).
- [ ] **Step 2:** bump the version in the four places; `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_version.py` PASS.
- [ ] **Step 3:** full suite once, nobody else's suite running (`pgrep -af "[p]ytest"` empty):
  `uv run --with pytest python -m pytest -p no:cacheprovider tests/ > "$WS/suite.txt" 2>&1; echo "exit $?"`;
  read the tail. Expected: `N passed`, exit 0; also the 3.11 run of README line 874.
- [ ] **Step 4: Commit** `docs: 0.30.0 - the lane shares the machine by value`

After the branch-wide review and its fix pass: merge to main and push under the standing rule; tag `v0.30.0` and the
GitHub release only with the person's ok. Then the field test of spec 8 (two fleets on one lane), compared on the
2026-11-06 analyses.
