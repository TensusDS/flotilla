# Lane admission, stage 1 (measure every run) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every run that goes through the lane records how long it took, its peak memory, the cores it used and how
busy the machine was, under a ladder of signatures; `flotilla lane` shows each booking's estimate from that history.
Admission does not change.

**Architecture:** A run's measurement (`lane/measure.py`) wraps the existing peak sampler and `getrusage`, and is
taken by `run.execute` and by `firstrun.run_tier`. Bookings gain fields in the existing journal (`book.py`): the
command and its signature ladder (`lane/signature.py`) when waiting, the measurement when released, `cut` when swept.
Estimates (`lane/estimate.py`) fold the journal's history into per-signature figures; `lane status` prints them.

**Tech Stack:** Python 3.11+ stdlib only; pytest via `uv run --with pytest`.

**Spec:** `docs/specs/2026-10-05-lane-resource-admission-design.md` (sections 2, 5, 7, 8 for this stage).

## Global Constraints

- Stdlib only; Python 3.11, 3.12, 3.13 on Linux and macOS (CI runs all six).
- English only in the repository (`tools/check_no_cyrillic.py`).
- Stage 1 changes no admission: `book.grant`, `acquire.acquire` and their callers decide exactly as before.
- Text another session wrote is shown through `flotilla.core.text.visible` (security finding F2): the new `command`
  and `ladder` fields join `book.TEXT`.
- Old journal lines (no new fields) must fold, show and feed estimates as "no history" without error.
- Full suite: `uv run --with pytest python -m pytest tests/ -o addopts= -q -p no:cacheprovider` (about 4-5 min); run
  long suites only when no other test run computes on the machine.
- Timing tests use fake clocks or recorded calls, never wall time (CI's macOS runners broke a wall-clock test in
  0.7.14).

## Review Focus

1. A journal written by an older plugin (no `command`, `ladder`, `seconds`): folds, `lane status` prints, estimates say
   "no history" - Task 3 `test_old_journal_lines_fold_and_estimate_nothing`.
2. A booking swept because its process died: recorded with `cut`, never feeds an estimate, never vanishes from the
   history - Task 3 `test_a_swept_holder_is_cut_and_carries_no_measurement`, Task 5 `test_cut_runs_feed_no_estimate`.
3. A `lane run` nested inside a receipt's booking: measured once, by the outer booking - Task 4
   `test_a_nested_run_is_not_measured_twice`.
4. Seat trees `twosuns-main-1` and `twosuns-main-12`: exact signatures never collide - Task 2
   `test_a_tree_placeholder_matches_whole_path_components`.
5. A machine with no `/proc/stat` (macOS): busy share is unknown, cores and peak still recorded, estimates still form -
   Task 1 `test_busy_share_is_unknown_without_proc_stat`, Task 5 `test_unknown_busy_counts_as_unsaturated`.

---

### Task 1: Measuring a run

**Files:**
- Create: `flotilla/lane/measure.py`
- Modify: `flotilla/lane/run.py` (`RunResult`, `execute`), `flotilla/onboard/firstrun.py` (`TierRun`, `run_tier`)
- Test: `tests/test_lane_measure.py`, `tests/test_lane_run.py`, `tests/test_onboard_firstrun.py`

**Interfaces:**
- Produces: `measure.CpuTimes` (`busy: int, total: int` jiffies) with `measure.cpu_times(path=Path("/proc/stat")) ->
  CpuTimes | None`; `measure.busy_share(before, after) -> float | None` (0..1); `measure.children_cpu_seconds() ->
  float | None`; `measure.Usage(seconds: float, peak_mb: int | None, cores: float | None, busy: float | None)`;
  `measure.Measurer(pgid, *, clock=time.monotonic, cpu=children_cpu_seconds, stat=cpu_times, peak=None)` - a context
  manager; after exit, `.usage -> Usage`.
- `RunResult` gains `usage: Usage | None = None`; `TierRun` gains `cores: float | None = None, busy: float | None =
  None`.

- [ ] **Step 1: failing tests** (`tests/test_lane_measure.py`):

```python
from pathlib import Path

from flotilla.lane import measure


def write_stat(path: Path, user, nice, system, idle, iowait):
    path.write_text(f"cpu  {user} {nice} {system} {idle} {iowait} 0 0 0 0 0\ncpu0 1 1 1 1 1 0 0 0 0 0\n")
    return path


def test_busy_share_is_busy_jiffies_over_all(tmp_path):
    before = measure.cpu_times(write_stat(tmp_path / "a", 100, 0, 100, 700, 100))
    after = measure.cpu_times(write_stat(tmp_path / "b", 400, 0, 200, 900, 100))
    assert measure.busy_share(before, after) == 0.8          # 400 busy of 500 jiffies


def test_busy_share_is_unknown_without_proc_stat(tmp_path):
    assert measure.cpu_times(tmp_path / "absent") is None
    assert measure.busy_share(None, None) is None


def test_a_measurer_reports_wall_time_cpu_cores_and_peak(tmp_path):
    clock = iter([100.0, 110.0])
    cpu = iter([5.0, 45.0])
    stat = iter([measure.CpuTimes(0, 1000), measure.CpuTimes(500, 2000)])

    class Peak:
        peak_mb = 900
        def __enter__(self): return self
        def __exit__(self, *exc): return None
    m = measure.Measurer(7, clock=lambda: next(clock), cpu=lambda: next(cpu), stat=lambda: next(stat),
                         peak=Peak())
    with m:
        pass
    assert m.usage == measure.Usage(seconds=10.0, peak_mb=900, cores=4.0, busy=0.5)


def test_a_measurer_with_nothing_readable_still_has_its_time():
    clock = iter([1.0, 4.0])

    class Peak:
        peak_mb = None
        def __enter__(self): return self
        def __exit__(self, *exc): return None
    m = measure.Measurer(7, clock=lambda: next(clock), cpu=lambda: None, stat=lambda: None, peak=Peak())
    with m:
        pass
    assert m.usage == measure.Usage(seconds=3.0, peak_mb=None, cores=None, busy=None)
```

Append to `tests/test_lane_run.py`:

```python
def test_a_run_carries_its_usage():
    result, _ = execute("import time; b = bytearray(50_000_000); time.sleep(1.2); print('1 passed')")
    assert result.usage is not None and result.usage.seconds >= 1.0
    assert result.usage.peak_mb is None or result.usage.peak_mb >= 40


def test_a_run_that_could_not_start_has_no_usage():
    result = run.execute(["/no/such/program"], out=io.StringIO())
    assert result.usage is None
```

Append to `tests/test_onboard_firstrun.py`:

```python
def test_a_tier_records_the_cores_it_used(tmp_path):
    busy = f'"{sys.executable}" -c "import time; end = time.process_time() + 0.6\nwhile time.process_time() < end: pass"'
    run = firstrun.run_tier("unit", busy, tmp_path, timeout=30)
    assert run.status == "green" and run.cores is not None and run.cores > 0.3
```

(`sys` is imported at the top of `tests/test_onboard_firstrun.py`; add `import sys` if it is not.)

- [ ] **Step 2: run** `uv run --with pytest python -m pytest tests/test_lane_measure.py tests/test_lane_run.py tests/test_onboard_firstrun.py -o addopts= -q -p no:cacheprovider` - Expected: FAIL (no module `measure`, no `usage`, no `cores`).

- [ ] **Step 3: implement** `flotilla/lane/measure.py`:

```python
"""What a run took: its wall time, the cores it used, its peak memory, and how busy the machine was meanwhile
(lane admission design, section 2). Cores are CPU seconds over wall seconds - what the run received, which under
contention is less than what it would use, so each sample carries the machine's busy share and estimates take only
unsaturated ones. Every figure that could not be read is None: unknown, never zero."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROC_STAT = Path("/proc/stat")


@dataclass(frozen=True)
class CpuTimes:
    busy: int
    total: int


@dataclass(frozen=True)
class Usage:
    seconds: float
    peak_mb: int | None
    cores: float | None
    busy: float | None


def cpu_times(path: Path = PROC_STAT) -> CpuTimes | None:
    """The machine's busy and total jiffies from the first line of /proc/stat (idle and iowait are not busy)."""
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0].split()
        values = [int(value) for value in first[1:]]
    except (OSError, ValueError, IndexError):
        return None
    if not first or first[0] != "cpu" or len(values) < 5:
        return None
    idle = values[3] + values[4]
    total = sum(values[:8])
    return CpuTimes(total - idle, total)


def busy_share(before: CpuTimes | None, after: CpuTimes | None) -> float | None:
    if before is None or after is None or after.total <= before.total:
        return None
    return round((after.busy - before.busy) / (after.total - before.total), 3)


def children_cpu_seconds() -> float | None:
    """User and system CPU seconds of every child this process reaped."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    except (ImportError, OSError, ValueError):
        return None
    return usage.ru_utime + usage.ru_stime


class Measurer:
    """Measures the run of process group `pgid` between enter and exit. The run must be reaped before exit, so its
    CPU time reaches RUSAGE_CHILDREN."""

    def __init__(self, pgid: int, *, clock=time.monotonic, cpu=children_cpu_seconds, stat=cpu_times, peak=None):
        from flotilla.lane.peak import GroupPeak
        self.clock, self.cpu, self.stat = clock, cpu, stat
        self.peak = peak if peak is not None else GroupPeak(pgid)
        self.usage: Usage | None = None

    def __enter__(self) -> "Measurer":
        self._began, self._cpu, self._stat = self.clock(), self.cpu(), self.stat()
        self.peak.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self.peak.__exit__(*exc)
        seconds = max(0.0, self.clock() - self._began)
        cpu_after = self.cpu()
        cores = None
        if self._cpu is not None and cpu_after is not None and seconds > 0:
            cores = round(max(0.0, cpu_after - self._cpu) / seconds, 2)
        self.usage = Usage(round(seconds, 2), self.peak.peak_mb, cores, busy_share(self._stat, self.stat()))
```

In `flotilla/lane/run.py`: add `from flotilla.lane.measure import Measurer, Usage` at the top; add `usage: Usage | None
= None` as the last field of `RunResult`; in `execute`, after the process starts, create `measurer = Measurer(process.pid)`
and enter it right away (`measurer.__enter__()`); after `process.wait()` returns (the `verdict_of(process.wait())` line),
call `measurer.__exit__(None, None, None)` and pass `usage=measurer.usage` to each of the three `RunResult(...)` that
follow the wait. On the `except BaseException` path, call `measurer.__exit__(None, None, None)` before re-raising. The
`could not start` result keeps `usage=None`.

In `flotilla/onboard/firstrun.py`: add `cores: float | None = None` and `busy: float | None = None` as the last fields
of `TierRun`; in `run_tier`, replace the `GroupPeak` sampler with `Measurer(proc.pid)` (it holds a `GroupPeak` and
keeps its "never fails the tier" behaviour) and pass `cores=measurer.usage.cores if status == "green" else None,
busy=measurer.usage.busy if status == "green" else None` with the existing `peak_mb=measurer.usage.peak_mb if status
== "green" else None`. On the timeout and exception paths, call `measurer.__exit__(None, None, None)` after the
process is reaped and before returning or re-raising.

- [ ] **Step 4: run** the same command - Expected: PASS. Then `uv run --with pytest python -m pytest tests/test_lane_run.py tests/test_lane_peak.py tests/test_onboard_firstrun.py tests/test_ledger_receipts.py -o addopts= -q -p no:cacheprovider` - Expected: PASS.

- [ ] **Step 5: injections** (each restored by writing the file back from git and checking `sha256sum`): busy counted
  with idle included; cores divided by CPU instead of seconds; `usage` dropped from the green `RunResult`; `cores` not
  passed in `run_tier`. Each must turn its test red.

- [ ] **Step 6: commit** `feat(lane): measure a run's time, cores, peak and the machine's busy share`

### Task 2: The signature ladder

**Files:**
- Create: `flotilla/lane/signature.py`
- Test: `tests/test_lane_signature.py`

**Interfaces:**
- Produces: `signature.ladder(command: list[str], *, tree: str | None, project: str) -> list[str]` (four steps,
  exact to coarse, duplicates removed, each prefixed `exact:`, `norm:`, `prog:`, `project:`);
  `signature.receipt_ladder(purpose: str, tiers: list[str], *, project: str) -> list[str]`;
  `signature.tier_signature(name: str, *, project: str) -> str` (`tier:<project>:<name>`).

- [ ] **Step 1: failing tests**:

```python
from flotilla.lane import signature as sig

P = "proj"


def test_a_tree_placeholder_matches_whole_path_components():
    one = sig.ladder(["npx", "vitest", "run", "/w/twosuns-main-1/tests/a.test.ts"], tree="/w/twosuns-main-1", project=P)
    twelve = sig.ladder(["npx", "vitest", "run", "/w/twosuns-main-12/tests/a.test.ts"], tree="/w/twosuns-main-1",
                        project=P)
    assert one[0] == "exact:npx vitest run <tree>/tests/a.test.ts"
    assert twelve[0] == "exact:npx vitest run /w/twosuns-main-12/tests/a.test.ts"


def test_two_seats_running_one_command_share_its_exact_signature():
    a = sig.ladder(["npx", "vitest", "run", "tests/a.test.ts"], tree="/w/twosuns-main-3", project=P)
    b = sig.ladder(["npx", "vitest", "run", "tests/a.test.ts"], tree="/w/twosuns-minor-7", project=P)
    assert a == b


def test_normalising_drops_hashes_numbers_temporary_paths_and_redirections():
    cmd = ["node", "/home/u/.claude/jobs/2e692fab/tmp/shoot.mjs", "--port", "5091", "/tmp/out-81723", ">", "log.txt"]
    steps = sig.ladder(cmd, tree=None, project=P)
    assert steps[1] == "norm:node /home/u/.claude/jobs/<hex>/tmp/shoot.mjs --port <n> <tmp>"
    assert steps[2] == "prog:node shoot.mjs"


def test_parallelism_flags_stay_in_the_program_step():
    one = sig.ladder(["npx", "vitest", "run", "--maxWorkers=1", "tests/a.test.ts"], tree=None, project=P)
    full = sig.ladder(["npx", "vitest", "run"], tree=None, project=P)
    assert one[2] == "prog:vitest run --maxWorkers=1" and full[2] == "prog:vitest run"


def test_a_shell_wrapper_is_read_through():
    steps = sig.ladder(["sh", "-c", "cd /w/twosuns-main-9 && npx vite build"], tree="/w/twosuns-main-9", project=P)
    assert steps[0] == "exact:npx vite build" and steps[2] == "prog:vite build"


def test_the_last_step_is_the_project_and_steps_are_not_repeated():
    steps = sig.ladder(["make"], tree=None, project=P)
    assert steps[-1] == "project:proj" and len(steps) == len(set(steps))


def test_receipts_and_tiers_have_their_own_signatures():
    assert sig.receipt_ladder("handover", ["unit", "lint"], project=P) == [
        "receipt:proj:handover:lint+unit", "receipt:proj:handover", "project:proj"]
    assert sig.tier_signature("unit", project=P) == "tier:proj:unit"
```

- [ ] **Step 2: run** `uv run --with pytest python -m pytest tests/test_lane_signature.py -o addopts= -q -p no:cacheprovider` - Expected: FAIL (no module).

- [ ] **Step 3: implement** `flotilla/lane/signature.py`:

```python
"""Which runs are runs of the same command (lane admission design, section 2): a ladder of signatures from exact to
coarse, so an estimate comes from the most exact step with history. Seat trees differ per seat; hashes, ports and
temporary files differ per run; flags that set parallelism change what a run takes and stay."""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

from flotilla.lane.machine import INTERPRETER

SHELLS = {"sh", "bash", "zsh", "dash"}
WRAPPERS = {"npx", "pnpm", "yarn", "bunx", "uvx"}
HEX = re.compile(r"\b[0-9a-f]{8,}\b")
NUMBER = re.compile(r"\d{2,}")
REDIRECT = re.compile(r"^(?:\d?>>?|\d?<|&>|\d>&\d)(.*)$")
PARALLEL = re.compile(r"^--?(?:maxWorkers|max-workers|workers|numprocesses|jobs|j|n|w|parallel)(?:=.*)?$")


def _words(command: list[str]) -> list[str]:
    """The command as words, read through `sh -c "cd X && ..."` wrappers."""
    words = list(command)
    if len(words) >= 3 and PurePosixPath(words[0]).name in SHELLS and words[1] == "-c":
        try:
            words = shlex.split(words[2])
        except ValueError:
            return words
        while len(words) >= 3 and words[0] == "cd" and words[2] == "&&":
            words = words[3:]
    return words


def _place_tree(word: str, tree: str | None) -> str:
    if not tree:
        return word
    root = tree.rstrip("/")
    if word == root:
        return "<tree>"
    if word.startswith(root + "/"):
        return "<tree>" + word[len(root):]
    return word


def _normalise(words: list[str]) -> list[str]:
    out, skip = [], False
    for word in words:
        if skip:
            skip = False
            continue
        redirect = REDIRECT.match(word)
        if redirect:
            skip = not redirect.group(1)   # `>` alone takes the next word as its target
            continue
        if word.startswith("/tmp/") or word.startswith("/var/tmp/"):
            out.append("<tmp>")
            continue
        word = HEX.sub("<hex>", word)
        out.append("<n>" if word.isdigit() else NUMBER.sub("<n>", word) if "/" in word or ":" in word or "=" in word
                   else word)
    return out


def _program(words: list[str]) -> str:
    rest = list(words)
    while rest and PurePosixPath(rest[0]).name in WRAPPERS:
        rest = rest[1:]
    if len(rest) >= 2 and rest[0] in ("uv", "npm", "pnpm", "yarn") and rest[1] in ("run", "exec"):
        rest = rest[2:]
    if not rest:
        return ""
    name = PurePosixPath(rest[0]).name
    tail = rest[1:]
    if INTERPRETER.match(name):
        if "-m" in tail and tail.index("-m") + 1 < len(tail):
            at = tail.index("-m") + 1
            name, tail = tail[at], tail[at + 1:]
        else:
            script = next((w for w in tail if not w.startswith("-")), None)
            if script is not None:
                at = tail.index(script)
                name, tail = PurePosixPath(script).name, tail[at + 1:]
    sub = next((w for w in tail if not w.startswith("-") and "/" not in w and "." not in w), None)
    flags = [w for w in tail if PARALLEL.match(w)]
    return " ".join([name, *([sub] if sub else []), *flags])


def ladder(command: list[str], *, tree: str | None, project: str) -> list[str]:
    words = [_place_tree(word, tree) for word in _words(command)]
    steps = [f"exact:{' '.join(words)}", f"norm:{' '.join(_normalise(words))}", f"prog:{_program(words)}",
             f"project:{project}"]
    return list(dict.fromkeys(step for step in steps if not step.endswith(":")))


def receipt_ladder(purpose: str, tiers: list[str], *, project: str) -> list[str]:
    return [f"receipt:{project}:{purpose}:{'+'.join(sorted(tiers))}", f"receipt:{project}:{purpose}",
            f"project:{project}"]


def tier_signature(name: str, *, project: str) -> str:
    return f"tier:{project}:{name}"
```

- [ ] **Step 4: run** the test command - Expected: PASS. If the `norm:` step of
  `test_normalising_drops_hashes_numbers_temporary_paths_and_redirections` differs only in which numbers become `<n>`,
  fix the implementation, not the expectation: the expectation is the spec's rule (hashes, numbers in paths and ports,
  temporary paths and redirections replaced).

- [ ] **Step 5: injections**: the tree match without the `/` boundary; `REDIRECT` handling removed; `PARALLEL` flags
  dropped from `_program`; the `sh -c` unwrapping removed. Each red.

- [ ] **Step 6: commit** `feat(lane): a ladder of signatures that joins runs of one command`

### Task 3: The journal carries commands and measurements

**Files:**
- Modify: `flotilla/lane/book.py`, `flotilla/lane/acquire.py` (`Grant`, `acquire`, `held`)
- Test: `tests/test_lane_book.py`, `tests/test_lane_acquire.py`

**Interfaces:**
- Consumes: `measure.Usage` (Task 1).
- Produces: `book.RULE = 1`; `Booking` gains `command: str = ""`, `ladder: list = field(default_factory=list)`,
  `will_run: list = field(default_factory=list)`, `project: str = ""`, `rule: int | None = None`, `seconds: float |
  None = None`, `peak_mb: int | None = None`, `cores: float | None = None`, `busy: float | None = None`, `verdict: str
  = ""`, `ran: list = field(default_factory=list)`, `cut: bool = False`; `Book.enqueue(..., command="", ladder=(),
  will_run=(), project="")` writes them with `rule=RULE`; `Book.release(booking_id, why="", *, measured: dict |
  None = None)`; `sweep` writes `cut=True`. `acquire.Grant` gains `measured: dict = field(default_factory=dict)`;
  `acquire.acquire(..., command="", ladder=(), will_run=(), project="")` and `held(...)` pass them to `enqueue`;
  `held` releases with `measured=grant.measured`; a nested grant is never released or measured by the inner caller.

- [ ] **Step 1: failing tests**, appended to `tests/test_lane_book.py`:

```python
def test_a_waiting_booking_carries_its_command_and_ladder(lane):
    lanes, _ = lane
    item = lanes.enqueue("main session 1", "vitest", pid=1, mark="m", command="npx vitest run",
                         ladder=["exact:npx vitest run", "project:p"], will_run=[], project="p")
    assert (item.command, item.ladder, item.project, item.rule) == ("npx vitest run",
                                                                     ["exact:npx vitest run", "project:p"], "p",
                                                                     book.RULE)


def test_a_release_carries_the_measurement(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=1, mark="m")
    lanes.grant(item.id, slots=1)
    done = lanes.release(item.id, measured={"seconds": 41.5, "peak_mb": 1730, "cores": 6.8, "busy": 0.62,
                                            "verdict": "green", "ran": [{"name": "unit", "seconds": 40.0}]})
    assert (done.seconds, done.peak_mb, done.cores, done.busy, done.verdict) == (41.5, 1730, 6.8, 0.62, "green")
    assert done.ran == [{"name": "unit", "seconds": 40.0}] and not done.cut


def test_a_swept_holder_is_cut_and_carries_no_measurement(lane):
    lanes, procs = lane
    item = lanes.enqueue("a", "", pid=5, mark="m")
    lanes.grant(item.id, slots=1)
    procs.dead.add(5)
    [swept] = lanes.sweep()
    assert swept.state == book.RELEASED and swept.cut and swept.seconds is None


def test_text_another_session_wrote_is_made_visible(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=1, mark="m", command="echo \x1b[2Jboom", ladder=["exact:echo \x1b[2J"])
    assert "\x1b" not in item.command and all("\x1b" not in step for step in item.ladder)


def test_old_journal_lines_fold_and_estimate_nothing(tmp_path):
    store = LocalLogStore(tmp_path)
    with store.transaction(book.KEY) as tx:
        tx.append({"at": "2026-09-27T22:45:55+00:00", "booking": "b1", "mark": "x", "note": "handover receipt",
                   "pid": 1, "run_for": "", "state": "waiting", "who": "main session 1"})
        tx.append({"at": "2026-09-27T22:45:56+00:00", "booking": "b1", "state": "held"})
        tx.append({"at": "2026-09-27T22:46:30+00:00", "booking": "b1", "state": "released"})
    item = book.fold(store.read(book.KEY).records)["b1"]
    assert (item.command, item.ladder, item.seconds, item.rule, item.cut) == ("", [], None, None, False)
```

Append to `tests/test_lane_acquire.py` (it already has fakes for lanes and the machine reading; reuse its helpers -
read the top of the file before writing this test and use the names it defines):

```python
def test_held_releases_with_what_the_caller_measured(tmp_path):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import acquire, book

    class Procs:
        def alive(self, pid, mark): return True
        def ancestors(self, pid): return []
        def start_mark(self, pid): return "m"

    class Reading:
        answers, computing = [], []
    lanes = book.Book(LocalLogStore(tmp_path), Procs())
    with acquire.held(lanes, lambda: Reading(), who="a", note="n", capacity=1, wait=0, table=Procs(),
                      command="make", ladder=["exact:make"], project="p") as grant:
        grant.measured.update({"seconds": 2.0, "verdict": "green"})
    item = lanes.bookings()[grant.booking.id]
    assert (item.state, item.command, item.seconds, item.verdict) == (book.RELEASED, "make", 2.0, "green")
```

- [ ] **Step 2: run** `uv run --with pytest python -m pytest tests/test_lane_book.py tests/test_lane_acquire.py -o addopts= -q -p no:cacheprovider` - Expected: FAIL.

- [ ] **Step 3: implement** in `flotilla/lane/book.py`:
  - `from dataclasses import dataclass, field`; `RULE = 1` with the comment `# the admission rule a booking waits
    under; stage 2 admits by budget only while every live booking carries this rule or later`;
  - `FIELDS = ("who", "note", "pid", "mark", "run_for", "why", "command", "ladder", "will_run", "project", "rule",
    "seconds", "peak_mb", "cores", "busy", "verdict", "ran", "cut")`; `TEXT = ("who", "note", "run_for", "why",
    "command", "project")`;
  - the new `Booking` fields listed under Interfaces;
  - in `fold`, a list field of strings (`ladder`, `will_run`) is made visible item by item: `value = [visible(v) if
    isinstance(v, str) else v for v in value]` when `key in ("ladder", "will_run") and isinstance(value, list)`;
  - `enqueue(self, who, note, *, pid=None, mark="", run_for="", command="", ladder=(), will_run=(), project="")`
    writes `command=command, ladder=list(ladder), will_run=list(will_run), project=project, rule=RULE` beside the
    existing fields;
  - `release(self, booking_id, why="", *, measured=None)`: `fields = {k: v for k, v in (measured or {}).items() if k
    in ("seconds", "peak_mb", "cores", "busy", "verdict", "ran")}`; write them with `why` when given;
  - `sweep`: the `_write` for a holder adds `cut=True`; for a waiter it stays as today.

  In `flotilla/lane/acquire.py`:
  - `Grant` gains `measured: dict = field(default_factory=dict)` (import `field`);
  - `acquire(...)` gains keyword parameters `command: str = "", ladder=(), will_run=(), project: str = ""` passed to
    `lanes.enqueue`;
  - `held(...)` gains the same four and passes them to `acquire`; its `finally` calls
    `lanes.release(grant.booking.id, measured=grant.measured)`; the nested path (`inside booking ...`) yields a
    `Grant(outer, None, ..., )` whose `measured` nobody reads - document that a nested run is part of its parent.

- [ ] **Step 4: run** the test command - Expected: PASS; then `uv run --with pytest python -m pytest tests/test_lane_book.py tests/test_lane_acquire.py tests/test_lane_cli.py tests/test_lane_machine.py -o addopts= -q -p no:cacheprovider` - Expected: PASS.

- [ ] **Step 5: injections**: `measured` not passed in `held`'s release; `cut=True` dropped from sweep; `command`
  missing from `TEXT`; the list-visible step removed. Each red.

- [ ] **Step 6: commit** `feat(lane): the journal carries each run's command, ladder and measurement`

### Task 4: `lane run` and receipts record what they measured

**Files:**
- Modify: `flotilla/lane/commands.py` (`booked`, `_run`, `_take`), `flotilla/ledger/commands.py` (`_receipt`),
  `flotilla/ledger/receipts.py` (`run_receipt` result per tier)
- Test: `tests/test_lane_cli.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: `booked(root, *, note, wait, run_for="", as_name=None, command=None, will_run=None, purpose="")` - a
  command gives the ladder `signature.ladder(command, tree=str(root), project=<repo key>)`; `will_run` and `purpose`
  give `signature.receipt_ladder`; `project` is `repo.identify(root).key`, or `""` outside a repository. The ran tiers
  of a receipt carry `peak_mb`, `cores`, `busy` beside `seconds`.

- [ ] **Step 1: failing tests**, appended to `tests/test_lane_cli.py` (it defines `run_cli` and `onboarded`):

```python
def _journal(tmp_path):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    return book.fold(LocalLogStore(tmp_path / "state" / "lane").read(book.KEY).records)


def test_a_lane_run_records_its_command_and_measurement(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--tree", str(root), "--", sys.executable, "-c", "print('1 passed')")
    assert code == 0, out
    [item] = [b for b in _journal(tmp_path).values() if b.command]
    assert sys.executable in item.command and item.ladder[-1].startswith("project:")
    assert item.verdict == "green" and item.seconds is not None and item.rule == 1


def test_a_receipt_records_the_tiers_it_ran(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root))
    items = [b for b in _journal(tmp_path).values() if b.note == "handover receipt"]
    assert items and items[-1].will_run and items[-1].ladder[0].startswith("receipt:")
    assert [tier["name"] for tier in items[-1].ran] == items[-1].will_run
    assert all("seconds" in tier and "peak_mb" in tier for tier in items[-1].ran)


def test_a_nested_run_is_not_measured_twice(tmp_path, monkeypatch):
    """A `lane run` inside a booking is part of it: the outer booking is measured, the inner never books."""
    root = onboarded(tmp_path, monkeypatch)
    inner = f"{sys.executable} -m flotilla lane run --tree {root} -- {sys.executable} -c pass"
    code, out = run_cli("lane", "run", "--tree", str(root), "--", "sh", "-c", inner)
    measured = [b for b in _journal(tmp_path).values() if b.seconds is not None]
    assert code == 0, out
    assert len(measured) == 1
```

Read `tests/test_lane_cli.py`'s `onboarded` and `PROFILE` before running: the receipt test needs a profile with a
handover tier (`PROFILE` already has one if `test_a_receipt_takes_the_lane` passes); if `python -m flotilla` does not
run the CLI in this repository, use the `bin/flotilla` path the existing tests use instead.

- [ ] **Step 2: run** `uv run --with pytest python -m pytest tests/test_lane_cli.py -o addopts= -q -p no:cacheprovider` - Expected: the three new tests FAIL.

- [ ] **Step 3: implement**:
  - `booked`: compute `project` with `repo.identify(Path(root)).key` (`repo.NotARepository` -> `""`); if `command`,
    `ladder = signature.ladder(command, tree=str(top), project=project)` and `command_text = shlex.join(command)`; if
    `will_run is not None`, `ladder = signature.receipt_ladder(purpose, will_run, project=project)` and
    `command_text = f"receipt {purpose}: " + ", ".join(will_run)`; pass `command=command_text, ladder=ladder,
    will_run=list(will_run or []), project=project` to `acq.held`.
  - `_run`: `with booked(..., command=command) as grant:`; after `runner.execute`, if `result.usage` is not None:
    `grant.measured.update({"seconds": result.usage.seconds, "peak_mb": result.usage.peak_mb, "cores":
    result.usage.cores, "busy": result.usage.busy, "verdict": "ceiling" if "ceiling" in result.summary else
    result.verdict})`.
  - `ledger/commands.py` `_receipt`: compute `planned = receipts.to_run(...)` once (it is already computed as
    `nothing`'s source - keep one call); `with booked(tree, note=f"{args.purpose} receipt", wait=args.lane_wait,
    will_run=planned, purpose=args.purpose) as grant:`; after `run_receipt`, `ran = [tier for tier in
    result["tiers"] if not str(tier.get("summary", "")).startswith("reused:")]`; `grant.measured.update({"seconds":
    sum(t.get("seconds") or 0 for t in ran) or None, "peak_mb": max((t.get("peak_mb") or 0 for t in ran), default=0)
    or None, "cores": max((t.get("cores") or 0 for t in ran), default=0) or None, "busy": max((t.get("busy") or 0 for
    t in ran), default=0) or None, "verdict": "green" if all(t["status"] == "green" for t in ran) else "red", "ran":
    [{k: t.get(k) for k in ("name", "status", "seconds", "peak_mb", "cores", "busy")} for t in ran]})`.
  - `receipts.run_receipt`: the result dict of a tier that ran gains `"peak_mb": r.peak_mb, "cores": r.cores,
    "busy": r.busy` (the reused branch stays as it is).
  - `_take` (by hand): unchanged - no command, no measurement.

- [ ] **Step 4: run** `uv run --with pytest python -m pytest tests/test_lane_cli.py tests/test_ledger_receipts.py tests/test_ledger_cli*.py -o addopts= -q -p no:cacheprovider` - Expected: PASS.

- [ ] **Step 5: injections**: `command=command` not passed in `_run`; `ran` not recorded in `_receipt`; the nested
  path made to book (drop the outer check in `acquire.held`). Each red.

- [ ] **Step 6: commit** `feat(lane): lane runs and receipts record what they ran and what it took`

### Task 5: Estimates from history

**Files:**
- Create: `flotilla/lane/estimate.py`
- Test: `tests/test_lane_estimate.py`

**Interfaces:**
- Consumes: `book.Booking` fields (Task 3), `signature.tier_signature` (Task 2).
- Produces: `estimate.Sample(seconds, peak_mb, cores, busy, verdict, at)`; `estimate.history(bookings, *, now,
  days=30) -> dict[str, list[Sample]]` (newest last, every step of each released, measured, not-cut booking; a
  receipt's ran tiers also under `tier_signature`); `estimate.Estimate(seconds: float | None, cores: float | None,
  peak_mb: int | None, source: str)`; `estimate.estimate(ladder, hist, *, project) -> Estimate`;
  `estimate.receipt_estimate(will_run, hist, *, project) -> Estimate`. Constants `KEEP = 10`, `ENOUGH = 3`,
  `SATURATED = 0.85`, `PRIOR = Estimate(None, 4.0, 2048, "fixed prior: 4 cores, 2 GB")`.

- [ ] **Step 1: failing tests**:

```python
import datetime as dt

from flotilla.lane import book, estimate as est

NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc)


def done(i, ladder, *, seconds=10.0, peak=500, cores=2.0, busy=0.5, verdict="green", days_ago=1, cut=False,
         ran=None, project="p"):
    at = (NOW - dt.timedelta(days=days_ago)).isoformat(timespec="seconds")
    return book.Booking(id=f"b{i}", state=book.RELEASED, ended=at, ladder=list(ladder), project=project,
                        seconds=None if cut else seconds, peak_mb=None if cut else peak,
                        cores=None if cut else cores, busy=None if cut else busy, verdict="" if cut else verdict,
                        cut=cut, ran=ran or [])


LADDER = ["exact:npx vitest run", "prog:vitest run", "project:p"]


def bookings(*items):
    return {item.id: item for item in items}


def test_the_most_exact_step_with_three_runs_answers():
    hist = est.history(bookings(*[done(i, LADDER, seconds=s) for i, s in enumerate([30, 40, 50])]), now=NOW)
    e = est.estimate(LADDER, hist, project="p")
    assert (e.seconds, e.cores, e.peak_mb) == (40.0, 2.0, 500) and "3 runs" in e.source and "exact" in e.source


def test_two_runs_fall_through_to_a_coarser_step():
    other = ["exact:npx vitest run b", "prog:vitest run", "project:p"]
    hist = est.history(bookings(done(1, LADDER), done(2, LADDER), done(3, other), done(4, other)), now=NOW)
    assert "prog" in est.estimate(LADDER, hist, project="p").source


def test_duration_is_the_median_of_green_runs_only():
    runs = [done(1, LADDER, seconds=40), done(2, LADDER, seconds=42), done(3, LADDER, seconds=44),
            done(4, LADDER, seconds=2, verdict="red"), done(5, LADDER, seconds=1, verdict="killed")]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").seconds == 42.0


def test_a_run_stopped_at_the_ceiling_counts_as_at_least_its_time():
    runs = [done(1, LADDER, seconds=10), done(2, LADDER, seconds=12), done(3, LADDER, seconds=600, verdict="ceiling")]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").seconds == 12.0
    runs += [done(4, LADDER, seconds=600, verdict="ceiling"), done(5, LADDER, seconds=600, verdict="ceiling")]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").seconds == 600.0


def test_cores_come_from_unsaturated_runs_and_take_the_maximum():
    runs = [done(1, LADDER, cores=6.8, busy=0.6), done(2, LADDER, cores=3.1, busy=0.98), done(3, LADDER, cores=5.0,
                                                                                           busy=0.4)]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").cores == 6.8


def test_unknown_busy_counts_as_unsaturated():
    runs = [done(i, LADDER, cores=2.5, busy=None) for i in range(3)]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p").cores == 2.5


def test_only_the_last_ten_within_thirty_days_count():
    old = [done(i, LADDER, seconds=500, days_ago=40) for i in range(5)]
    new = [done(10 + i, LADDER, seconds=10 + i, days_ago=1) for i in range(12)]
    e = est.estimate(LADDER, est.history(bookings(*old, *new), now=NOW), project="p")
    assert e.seconds == 16.5   # median of 12..21 - the last ten


def test_cut_runs_feed_no_estimate():
    runs = [done(i, LADDER, cut=True) for i in range(5)]
    assert est.estimate(LADDER, est.history(bookings(*runs), now=NOW), project="p") == est.PRIOR


def test_a_new_command_takes_the_projects_ninetieth_percentile():
    runs = [done(i, [f"exact:cmd {i}", "project:p"], cores=float(i), peak=100 * i) for i in range(1, 11)]
    e = est.estimate(["exact:new", "project:p"], est.history(bookings(*runs), now=NOW), project="p")
    assert (e.cores, e.peak_mb, e.seconds) == (9.0, 900, None) and "project prior" in e.source


def test_a_project_with_no_runs_gets_the_fixed_prior():
    assert est.estimate(["exact:new", "project:q"], {}, project="q") == est.PRIOR


def test_a_receipt_sums_the_tiers_it_will_run():
    ran = [{"name": "unit", "status": "green", "seconds": 40.0, "peak_mb": 1700, "cores": 6.9, "busy": 0.5},
           {"name": "lint", "status": "green", "seconds": 5.0, "peak_mb": 300, "cores": 1.0, "busy": 0.5}]
    runs = [done(i, ["receipt:p:handover:lint+unit", "project:p"], ran=ran) for i in range(3)]
    hist = est.history(bookings(*runs), now=NOW)
    both = est.receipt_estimate(["unit", "lint"], hist, project="p")
    assert (both.seconds, both.peak_mb, both.cores) == (45.0, 1700, 6.9)
    assert est.receipt_estimate(["lint"], hist, project="p").seconds == 5.0
```

- [ ] **Step 2: run** `uv run --with pytest python -m pytest tests/test_lane_estimate.py -o addopts= -q -p no:cacheprovider` - Expected: FAIL (no module).

- [ ] **Step 3: implement** `flotilla/lane/estimate.py`:

```python
"""What a run will take, from the runs before it (lane admission design, section 2). The most exact signature with
three measurements in the last 30 days answers; duration is the median of green runs (a red or killed run that ended
early says nothing about a full one; a run stopped at the ceiling counts as at least its time), cores the maximum of
runs on an unsaturated machine, peak the maximum. A command never seen takes its project's 90th percentile; a
project never seen, a fixed prior. Only the last ten measurements of a step count: suites grow."""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from flotilla.lane import book
from flotilla.lane.signature import tier_signature

KEEP, ENOUGH, SATURATED = 10, 3, 0.85


@dataclass(frozen=True)
class Sample:
    seconds: float | None
    peak_mb: int | None
    cores: float | None
    busy: float | None
    verdict: str
    at: str


@dataclass(frozen=True)
class Estimate:
    seconds: float | None
    cores: float | None
    peak_mb: int | None
    source: str


PRIOR = Estimate(None, 4.0, 2048, "fixed prior: 4 cores, 2 GB")


def _when(text: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def history(bookings: dict, *, now: dt.datetime, days: int = 30) -> dict[str, list[Sample]]:
    since = now - dt.timedelta(days=days)
    found: dict[str, list[Sample]] = {}
    done = [item for item in bookings.values() if item.state == book.RELEASED and not item.cut
            and item.seconds is not None and (_when(item.ended) or since) >= since]
    for item in sorted(done, key=lambda item: item.ended):
        sample = Sample(item.seconds, item.peak_mb, item.cores, item.busy, item.verdict, item.ended)
        for step in item.ladder:
            found.setdefault(step, []).append(sample)
        for tier in item.ran or []:
            if isinstance(tier, dict) and tier.get("name") and tier.get("seconds") is not None:
                found.setdefault(tier_signature(tier["name"], project=item.project), []).append(Sample(
                    tier.get("seconds"), tier.get("peak_mb"), tier.get("cores"), tier.get("busy"),
                    "green" if tier.get("status") == "green" else str(tier.get("status") or ""), item.ended))
    return {step: samples[-KEEP:] for step, samples in found.items()}


def _duration(samples: list[Sample]) -> float | None:
    usable = [s.seconds for s in samples if s.seconds is not None and s.verdict in ("green", "ceiling")]
    return float(statistics.median(usable)) if usable else None


def _cores(samples: list[Sample]) -> float | None:
    usable = [s.cores for s in samples if s.cores is not None and (s.busy is None or s.busy < SATURATED)]
    return max(usable) if usable else None


def _peak(samples: list[Sample]) -> int | None:
    usable = [s.peak_mb for s in samples if s.peak_mb is not None]
    return max(usable) if usable else None


def _p90(values: list) -> float | int | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]


def estimate(ladder: list[str], hist: dict[str, list[Sample]], *, project: str) -> Estimate:
    for step in ladder:
        if step.startswith("project:"):
            break
        samples = hist.get(step, [])
        if len(samples) >= ENOUGH:
            kind = step.split(":", 1)[0]
            return Estimate(_duration(samples), _cores(samples), _peak(samples),
                            f"{len(samples)} runs ({kind} match)")
    samples = hist.get(f"project:{project}", [])
    if len(samples) >= ENOUGH:
        return Estimate(None, _p90([s.cores for s in samples]), _p90([s.peak_mb for s in samples]),
                        f"project prior: 90th percentile of {len(samples)} runs")
    return PRIOR


def receipt_estimate(will_run: list[str], hist: dict[str, list[Sample]], *, project: str) -> Estimate:
    parts = [estimate([tier_signature(name, project=project)], hist, project=project) for name in will_run]
    if not parts:
        return Estimate(0.0, None, None, "nothing to run")
    seconds = None if any(p.seconds is None for p in parts) else sum(p.seconds for p in parts)
    cores = max((p.cores for p in parts if p.cores is not None), default=None)
    peak = max((p.peak_mb for p in parts if p.peak_mb is not None), default=None)
    sources = sorted({p.source for p in parts})
    return Estimate(seconds, cores, peak, f"{len(parts)} tier(s): " + "; ".join(sources))
```

Note the tier step for a single tier has no `project:` step after it, so a tier with too little history falls to the
project prior through the loop's end: `estimate([tier_signature(...)], ...)` reaches the project lookup after the
loop.

- [ ] **Step 4: run** the test command - Expected: PASS. `test_only_the_last_ten_within_thirty_days_count`: the ten
  newest are 12..21 (seconds 12..21), median 16.5 - if it differs, check `history` keeps the newest ten in order.

- [ ] **Step 5: injections**: red runs counted in `_duration`; the busy filter removed from `_cores`; `KEEP` slicing
  removed; the 30-day cut removed; `cut` bookings not excluded; `receipt_estimate` taking the max of seconds instead of
  the sum. Each red.

- [ ] **Step 6: commit** `feat(lane): estimate a run from the runs before it`

### Task 6: `flotilla lane` shows the estimates, and the docs

**Files:**
- Modify: `flotilla/lane/commands.py` (`_status`), `README.md` (section "Long runs and the lane"),
  `docs/specs/2026-09-22-decisions-log.md` (decision 232)
- Test: `tests/test_lane_cli.py`

**Interfaces:**
- Consumes: `estimate.history`, `estimate.estimate`, `estimate.receipt_estimate` (Task 5), `Booking` fields (Task 3).
- Produces: `commands.describe_estimate(item, hist) -> str` - `"estimate: 40 s, 6.9 cores, 1.7 GB (5 runs (exact
  match))"`, unknown parts as `?`, and `"estimate: none (an older flotilla booked it)"` for a booking with no ladder.

- [ ] **Step 1: failing tests**, appended to `tests/test_lane_cli.py`:

```python
def test_the_lane_shows_each_bookings_estimate(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    for _ in range(3):
        run_cli("lane", "run", "--tree", str(root), "--", sys.executable, "-c", "print('1 passed')")
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    lanes = book.Book(LocalLogStore(tmp_path / "state" / "lane"), type("P", (), {"alive": lambda *a: True})())
    lanes.enqueue("main session 2", "again", pid=None, mark="", command=f"{sys.executable} -c print('1 passed')",
                  ladder=next(b for b in lanes.bookings().values() if b.command).ladder, project="x")
    code, out = run_cli("lane", "--root", str(root))
    assert "estimate:" in out and "3 runs (exact match)" in out


def test_an_older_booking_says_it_has_no_estimate(tmp_path):
    from flotilla.lane import book, commands
    assert commands.describe_estimate(book.Booking(id="b1"), {}) == "estimate: none (an older flotilla booked it)"
```

- [ ] **Step 2: run** `uv run --with pytest python -m pytest tests/test_lane_cli.py -o addopts= -q -p no:cacheprovider` - Expected: the two new tests FAIL.

- [ ] **Step 3: implement**:
  - `describe_estimate(item, hist)`: no ladder and no `will_run` -> the "older flotilla" text; `will_run` ->
    `estimate.receipt_estimate(item.will_run, hist, project=item.project)`; otherwise `estimate.estimate(item.ladder,
    hist, project=item.project)`; format seconds as `f"{s:.0f} s"` (or `?`), cores `f"{c:g} cores"`, peak as GB with
    one decimal when >= 1024 MB else MB.
  - `_status`: build `hist = estimate.history(lanes.bookings(), now=dt.datetime.now(dt.timezone.utc))` once; append
    `"; " + describe_estimate(item, hist)` to each held and each waiting line.
  - README, section "Long runs and the lane", add one paragraph: every run in the lane now records its time, cores,
    peak memory and the machine's busy share; `flotilla lane` shows each booking's estimate and where it comes from;
    this is the first stage of admission by resources (spec of 2026-10-05), and admission itself is unchanged.
  - Decisions log, after decision 231: decision 232 - "**The lane measures every run before it decides by them**",
    dated 2026-10-05, with: the field numbers (64% of bookings under a minute waited 84 h of 146 h; 26-39% of runs
    first seen), the signature ladder and why (hashes and ports make exact matches rare), the estimate rules
    (median of green, max of unsaturated cores, max peak, last 10 in 30 days, three per step, project p90, fixed
    prior), and that stage 1 changes no admission so a week of the field fleet measures browser jobs first.

- [ ] **Step 4: run** the test command - Expected: PASS. Then the full suite (Global Constraints) - Expected: all
  pass; read its last lines, not its exit code.

- [ ] **Step 5: injection**: `describe_estimate` dropped from `_status`'s waiting line. Red.

- [ ] **Step 6: commit** the code `feat(lane): show each booking's estimate and its source`, then the docs `docs:
  the lane measures every run - README and decision 232`.
