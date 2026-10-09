# Rig 2c (0.11.0) - packing runs by estimates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Runs waiting in a rig session start by estimates: a run starts when its estimate fits what the running
runs leave on the machine; among the runs that fit, the longest goes first and short ones fill the rest; a run that
has waited 10 minutes is senior and no later run may take room it needs; a run bigger than the whole machine runs
alone once the machine is empty. `flotilla rig` and the waiting `rig run` say why a run waits.

**Architecture:**
- `flotilla/rig/remote.py` - READINGS reports the machine's real size inside its container (the cgroup's memory
  limit and CPU quota, v1 and v2), memory free net of reclaimable page cache, and no GPU when NVML answers 0; the
  run's sampler reports `gpu_mb=-1` on a machine without `nvidia-smi`.
- `flotilla/rig/packing.py` (new, pure) - `history` and `estimate` (what a run will take), `choose` (which waiting
  run starts on a machine, and why each other one waits), the margins (moved here from `run.py`).
- `flotilla/rig/journal.py` - `start_run` asks `choose` inside its transaction; `would_start` asks the same question
  without starting anything, so a run reads the machine only when it would be the one to go; a waiting run's reason
  is recorded when it changes.
- `flotilla/rig/run.py` - every waiting `rig run` tries every ready machine of its session (only the oldest raises a
  machine); the machine's shape is read before the first try; the reason a run waits is printed when it changes.
- `flotilla/rig/surface.py` - `flotilla rig` shows each waiting run's reason, estimate and seniority.

**Tech Stack:** Python 3.11+ stdlib only; bash, coreutils and awk on the machine; pytest.

**Spec:** `docs/specs/2026-10-06-rig-design.md` - section 7 ("Several runs on one machine", the 0.11.0 bullet),
section 11 stage 2c, section 12 "Live check 2". The five details below were approved by the person on 2026-10-09
("yes, all good, go") in answer to the design posted on 2026-10-08:
1. estimates come from runs measured on rig machines, by the lane's signature ladder: duration is the median of
   green runs; cores, memory and GPU memory are maxima; GPU memory measured as shared is not used; a command never
   measured takes a prior of 4 cores, 2 GB memory, 2 GB GPU memory;
2. a run fits when the estimates of the running runs plus its own do not exceed the machine's resources minus
   margins; today's rule on live readings stays as a second barrier, and so does the floor of two runs;
3. among the runs that fit, the longest by estimate goes first, short ones fill the rest;
4. a run waiting over 10 minutes is senior: until it fits, no run that came after it may take room it needs;
5. a run whose estimate does not fit even the empty machine runs alone when the machine is free.

## Global Constraints

- Python stdlib only; `requires-python >= 3.11`; English only (`tools/check_no_cyrillic.py`); lines at most 120
  characters.
- Tests: `uv run --with pytest python -m pytest tests` (the project's config sets `-q`; no second `-q`), and on the
  person's machine every suite run goes through `flotilla lane run --root <tree> -- ...` (peer sessions share it).
  No real network, ssh or crontab in tests; remote scripts run under the ssh stand-in, Linux-only tests `skipif`.
- Margins: `MARGIN_MB = 2048` memory, `GPU_MARGIN_MB = 1024` GPU memory - defined in `packing.py`, imported by
  `run.py` (the data layer never imports the CLI layer).
- The prior: 4.0 cores, 2048 MB memory, 2048 MB GPU memory, no duration. `SENIOR = 10 minutes` from `since`.
- FLOOR = 2, RUN_SETTLE = 60 s, DEFAULT_CAP = 4 keep their values.
- The journal is folded by every session: a missing or malformed field folds as missing, never crashes.
- Version 0.11.0 in `pyproject.toml`, `flotilla/__init__.py`, `.claude-plugin/plugin.json`.

## Rulings (planning and the two plan reviews of 2026-10-09)

- **The floor, read as: up to two runs share a machine whatever the CORE estimates say; memory and GPU estimates
  bind from the first run.** CPU over-commit only slows runs; memory and GPU over-commit kill them. This keeps
  0.10.x's "two always share" on small machines (a 4-vCPU CI host, a 4-core rental) and adds what 0.10.x lacked:
  two runs whose memory cannot fit are not started together. Past the floor, cores bind too, and the live readings,
  the settle minute and the CPU cap stay exactly as in 0.10.x. Cost if wrong: if the person meant "estimates bind
  only past the floor", two memory-heavy runs wait where 0.10.x would have started them both - and OOM'd.
- **Estimated cores are clamped to the machine's CPUs** - a run measured at 40 cores on a 64-core box needs at most
  24 on a 24-core one; cores alone never make a run "bigger than the machine".
- **The `project:` step never answers** (as in the lane): a command never measured takes the prior, not another
  command's numbers.
- **One sample is enough** (the lane needs three): rig runs are few and paid. A sample of 0 cores or 0 MB is no
  sample for that resource (a command shorter than the sampler's 2 s period); a run that ended with 137 (killed,
  usually by the OOM killer) is no memory sample - it never reached its need.
- **Duration:** median of green runs, a run stopped at the ceiling counting as at least its time (as the lane); a
  step with only red runs takes its duration from a coarser step (as the lane), else none.
- **An unknown duration sorts as the longest**, so a never-measured run is measured early.
- **GPU memory measured as shared** is kept in the journal (`gpu_shared_mb`) and may RAISE an estimate, never lower
  it: a shared figure overstates (another run's growth) or understates (another run freed memory), and only the
  overstatement is safe to act on. Detail 1's "not used" holds for lowering. Without this, packing would never learn
  a GPU figure, since packed runs overlap.
- **A resource with no usable sample takes the prior's value** for that resource, and the display says "GPU: prior".
  This is a guess, not a safe bound: a 12 GB render never measured alone stays at 2 GB until it is.
- **Reservation is per resource; a senior bigger than the empty machine reserves the whole machine** (no later run
  starts there, so the machine empties and the senior runs alone). Otherwise a later run may start only if, in
  every resource where it needs anything, what it leaves still holds the senior's need. A senior's reservation ends
  with its process (its `--wait`); no backfill by expected end times in 0.11.0 (TODO).
- **A senior's reservation holds on every machine of the session.** Cost: with two machines, later runs wait on both.
- **Machine shape unknown** (no readings recorded): 0.10.x's rule - the session's oldest waiting run, the floor and
  the readings.
- **Head-of-line:** when the chosen run is refused by the second barrier, nothing else starts on that machine that
  poll. Same as 0.10.x.
- **Arrival order is the run number** (ids are issued in order), not the `since` text.
- **Run time is the journal's `seconds`** (the slot's wall time, tree and setup included), used only for ordering.
- **`ended`** of a finished run is the time of its first `done` event, taken in the fold, so records from before
  0.11.0 teach too and a later `swept` event does not move it.

## Review Focus

- **The chosen run is not the oldest.** Expected: it starts at its next poll; the oldest does not block it. Pinned in
  Task 4 (`test_the_longer_run_that_came_second_starts_first`, a two-process `rig run` test).
- **A container smaller than its host.** Expected: the shape recorded is the cgroup's limit and quota, not
  `MemTotal`/`nproc`. Pinned in Task 1 (`test_readings_report_the_containers_limits`).
- **A senior bigger than the machine while runs needing none of the overflowing resource keep arriving.** Expected:
  they wait, the machine empties, the senior starts. Pinned in Task 3
  (`test_a_senior_too_big_for_the_machine_reserves_all_of_it`).
- **Hand-written journal lines** (`gpu_mb: "x"`, negative `seconds`, a slug with `/`). Expected: folded as missing.
  Pinned in Task 2 (`test_hand_written_measurements_fold_as_missing`).
- **Ten waiting runs.** Expected: only the run `choose` would start reads the machine; the others make no ssh call.
  Pinned in Task 4 (`test_only_the_run_that_would_start_reads_the_machine`).

---

### Task 1: readings and measurements fit for packing

**Files:**
- Modify: `flotilla/rig/remote.py` (READINGS; the sampler and status line in the RUN template)
- Modify: `flotilla/rig/run.py` (`Run.roomy` notes `ram_mb` from `mem_limit_kb`; `read_status` reads `gpu_mb=-1`
  as None and keeps a shared figure as `gpu_shared_mb`)
- Modify: `flotilla/rig/journal.py` (`Run.gpu_shared_mb`; `WHOLE` bound for it)
- Test: `tests/test_rig_remote.py`, `tests/test_rig_run.py`

**Interfaces:**
- Produces: READINGS prints `mem_kb mem_total_kb mem_limit_kb cpus gpu_free_mb gpu_total_mb disk_free_mb`, where
  `mem_limit_kb = min(MemTotal, cgroup limit)`, `cpus = min(nproc, ceil(quota/period))` for cgroup v2 `cpu.max` and
  v1 `cpu.cfs_quota_us/cpu.cfs_period_us`, `mem_kb = min(MemAvailable, limit - (current - inactive_file))`, and
  `gpu_free_mb = gpu_total_mb = -1` when `nvidia-smi` is absent or reports a total of 0. READINGS takes an optional
  second argument, a root prefixed to `/proc` and `/sys` paths (tests point it at a fake tree; the machine passes
  nothing).
  The status line's `gpu_mb` is `-1` when the machine has no `nvidia-smi`.
  `j.Run.gpu_shared_mb: int | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_rig_remote.py` (Linux-only like its neighbours; `ssh` is the file's helper that runs a script under
the stand-in):

```python
def fake_root(tmp_path, *, meminfo_total=256 * 1024 * 1024, available=200 * 1024 * 1024, v2=True,
              limit=8 * 1024 ** 3, current=6 * 1024 ** 3, inactive=3 * 1024 ** 3, quota="400000 100000"):
    root = tmp_path / "root"
    (root / "proc").mkdir(parents=True)
    (root / "proc/meminfo").write_text(f"MemTotal: {meminfo_total} kB\nMemAvailable: {available} kB\n")
    cg = root / "sys/fs/cgroup"
    if v2:
        cg.mkdir(parents=True)
        (cg / "memory.max").write_text(f"{limit}\n")
        (cg / "memory.current").write_text(f"{current}\n")
        (cg / "memory.stat").write_text(f"anon 1\ninactive_file {inactive}\n")
        (cg / "cpu.max").write_text(f"{quota}\n")
    else:
        (cg / "memory").mkdir(parents=True)
        (cg / "cpu").mkdir(parents=True)
        (cg / "memory/memory.limit_in_bytes").write_text(f"{limit}\n")
        (cg / "memory/memory.usage_in_bytes").write_text(f"{current}\n")
        (cg / "memory/memory.stat").write_text(f"total_inactive_file {inactive}\n")
        q, p = quota.split()
        (cg / "cpu/cpu.cfs_quota_us").write_text(f"{q}\n")
        (cg / "cpu/cpu.cfs_period_us").write_text(f"{p}\n")
    return root


def readings(tmp_path, root, box):
    line = ssh(tmp_path, remote.READINGS, box / "work", root).stdout.decode()
    return {k: int(v) for k, v in (item.split("=") for item in line.split())}


@pytest.mark.parametrize("v2", [True, False])
def test_readings_report_the_containers_limits(tmp_path, box, v2):
    got = readings(tmp_path, fake_root(tmp_path, v2=v2), box)
    assert got["mem_limit_kb"] == 8 * 1024 * 1024          # the cgroup's 8 GB, not the host's 256 GB
    assert got["cpus"] <= 4                                # the quota's 4 cores, whatever nproc says
    assert got["mem_kb"] == (8 - (6 - 3)) * 1024 * 1024     # page cache that can be reclaimed is free


def test_readings_without_a_cgroup_limit_report_the_machine(tmp_path, box):
    got = readings(tmp_path, fake_root(tmp_path, limit=2 ** 63 - 1, quota="max 100000"), box)
    assert got["mem_limit_kb"] == 256 * 1024 * 1024


def test_a_gpu_that_reports_no_memory_is_no_gpu(tmp_path, box, monkeypatch):
    fake_nvidia_smi(tmp_path, monkeypatch, total=0, free=0)   # helper below: a PATH dir with an nvidia-smi
    got = readings(tmp_path, fake_root(tmp_path), box)
    assert got["gpu_total_mb"] == -1 and got["gpu_free_mb"] == -1
```

`fake_nvidia_smi(tmp_path, monkeypatch, total, free)`: writes `tmp_path/bin/nvidia-smi` printing `total` for
`--query-gpu=memory.total`, `free` for `memory.free`, `0` for `memory.used`, chmod 755, and prepends `tmp_path/bin`
to `PATH` for the stand-in. If the stand-in starts the remote with a clean `PATH`, the executor uses the variable
the stand-in already forwards for test binaries (see `tests/rigssh.py`) and ledgers it.

`tests/test_rig_run.py`:

```python
def test_a_machine_without_nvidia_smi_measures_no_gpu(world, box, tree, tmp_path):
    opened(world)
    raised(world, tree)
    code, out = rig_run(world, tree, "--", "true")
    run = journal(world).runs()["j1"]
    assert code == 0 and run.gpu_mb is None


def test_a_shared_gpu_figure_is_kept_apart():
    task = run_module.Run.__new__(run_module.Run)
    task.measured = {}
    task.read_status("exit=0 seconds=10 cpu_s=20 peak_kb=4096 gpu_mb=3000 shared=1", stopped_here=False)
    assert task.measured["gpu_mb"] is None and task.measured["gpu_shared_mb"] == 3000
```

(`run_module` is `from flotilla.rig import run as run_module`; if the file imports it under another name, use that.)

- [ ] **Step 2: Run them to verify they fail**

Run: `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_remote.py tests/test_rig_run.py -k "limits or without_a_cgroup or no_gpu or without_nvidia or shared_gpu"`
Expected: FAIL - no `mem_limit_kb` key; `gpu_mb` 0 not None; no `gpu_shared_mb`.

- [ ] **Step 3: Implement**

READINGS, replacing lines `mem=` .. `gtotal=` (paths through `$r`):

```bash
r=${2:-}
mem=$(awk '/^MemAvailable:/ {print $2}' "$r/proc/meminfo")
total=$(awk '/^MemTotal:/ {print $2}' "$r/proc/meminfo")
limit=$total
cpus=$(nproc)
cg=$r/sys/fs/cgroup
take_limit() {  # $1 limit in bytes, $2 usage in bytes, $3 reclaimable page cache in bytes
  [ "$1" -lt 1000000000000000 ] || return 0
  [ $(( $1 / 1024 )) -lt "$limit" ] && limit=$(( $1 / 1024 ))
  local room=$(( ($1 - ($2 - $3)) / 1024 ))
  [ "$room" -lt "$mem" ] && mem=$room
  return 0
}
take_quota() {  # $1 quota, $2 period (microseconds); -1 or "max" is no quota
  case $1 in max|-1|'') return 0 ;; esac
  local capped=$(( ($1 + $2 - 1) / $2 ))
  [ "$capped" -lt "$cpus" ] && cpus=$capped
  return 0
}
if [ -r "$cg/memory.max" ]; then
  max=$(cat "$cg/memory.max")
  if [ "$max" != max ]; then
    take_limit "$max" "$(cat "$cg/memory.current" 2>/dev/null || echo 0)" \
      "$(awk '$1 == "inactive_file" {print $2}' "$cg/memory.stat" 2>/dev/null || echo 0)"
  fi
elif [ -r "$cg/memory/memory.limit_in_bytes" ]; then
  take_limit "$(cat "$cg/memory/memory.limit_in_bytes")" \
    "$(cat "$cg/memory/memory.usage_in_bytes" 2>/dev/null || echo 0)" \
    "$(awk '$1 == "total_inactive_file" {print $2}' "$cg/memory/memory.stat" 2>/dev/null || echo 0)"
fi
if [ -r "$cg/cpu.max" ]; then
  read -r quota period < "$cg/cpu.max"; take_quota "$quota" "$period"
elif [ -r "$cg/cpu/cpu.cfs_quota_us" ]; then
  take_quota "$(cat "$cg/cpu/cpu.cfs_quota_us")" "$(cat "$cg/cpu/cpu.cfs_period_us")"
fi
gfree=-1 gtotal=-1
if command -v nvidia-smi > /dev/null 2>&1; then
  gfree=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | awk '{s += $1} END {print s + 0}')
  gtotal=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | awk '{s += $1} END {print s + 0}')
  [ "$gtotal" -gt 0 ] || { gfree=-1; gtotal=-1; }    # NVML that answers 0 is a GPU we cannot count on
fi
```

and the echo gains `mem_limit_kb=$limit`. Empty awk results: guard each with `${x:-0}` where the value feeds
arithmetic.

The sampler (`_RUN` template): start with `gpu=-1` when `command -v nvidia-smi` fails, else `gpu=0`; keep the
`-1` through the status line.

`run.py`:
- `roomy` notes `ram_mb=readings.get("mem_limit_kb", readings.get("mem_total_kb", 0)) // 1024`;
- `read_status`: `gpu = int(fields.get("gpu_mb", "0"))`; `self.measured["gpu_mb"] = None if shared or gpu < 0 else
  gpu`; `self.measured["gpu_shared_mb"] = gpu if shared and gpu >= 0 else None`.

`journal.py`: `gpu_shared_mb: int | None = None` on `Run`; `"gpu_shared_mb": 10 ** 7` in `WHOLE`.

- [ ] **Step 4: Run them to verify they pass, then the remote and run files**

Run: `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_remote.py tests/test_rig_run.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add flotilla/rig/remote.py flotilla/rig/run.py flotilla/rig/journal.py tests/test_rig_remote.py tests/test_rig_run.py
git commit -m "feat(rig): readings report the container's size, and measurements say when there is no GPU"
```

---

### Task 2: estimates from measured runs

**Files:**
- Create: `flotilla/rig/packing.py`
- Modify: `flotilla/rig/journal.py` (`Run.ended`, set in `_fold_runs` from the first `done` event's `at`)
- Modify: `flotilla/rig/run.py` (import `MARGIN_MB`, `GPU_MARGIN_MB` from `packing`; delete its own)
- Test: `tests/test_rig_packing.py`

**Interfaces:**
- Produces:
  - `packing.MARGIN_MB = 2048`, `packing.GPU_MARGIN_MB = 1024`
  - `packing.Need` - frozen dataclass `(seconds: float | None, cores: float, ram_mb: int, gpu_mb: int, source: str,
    gpu_prior: bool)`
  - `packing.PRIOR = Need(None, 4.0, 2048, 2048, "prior", True)`
  - `packing.history(runs: dict[str, j.Run], *, now) -> dict[str, list[Sample]]`
  - `packing.estimate(ladder, hist) -> Need`
  - `j.Run.ended: str`

- [ ] **Step 1: Write the failing tests** (`tests/test_rig_packing.py`)

```python
import datetime as dt
import json

from flotilla.core.storage import LocalLogStore
from flotilla.rig import journal as j, packing

NOW = dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.timezone.utc)


def done(i, ladder, *, verdict="green", exit=0, seconds=60.0, cores=2.0, peak_mb=900, gpu_mb=500,
         gpu_shared_mb=None, days=0):
    return j.Run(id=f"j{i}", state=j.DONE, verdict=verdict, exit=exit, ladder=tuple(ladder), seconds=seconds,
                 cores=cores, peak_mb=peak_mb, gpu_mb=gpu_mb, gpu_shared_mb=gpu_shared_mb,
                 ended=(NOW - dt.timedelta(days=days)).isoformat())


def est(ladder, *runs):
    return packing.estimate(ladder, packing.history({r.id: r for r in runs}, now=NOW))


def test_a_never_measured_command_takes_the_prior():
    assert packing.estimate(["exact:a"], {}) == packing.PRIOR


def test_the_most_exact_step_with_a_sample_answers():
    need = est(["exact:a", "prog:x"], done(1, ["exact:a", "prog:x"], seconds=100, cores=6.0),
               done(2, ["exact:b", "prog:x"], seconds=10, cores=1.0))
    assert (need.seconds, need.cores) == (100.0, 6.0) and "exact" in need.source


def test_the_project_step_never_answers():
    assert est(["exact:new", "project:P"], done(1, ["exact:true", "project:P"], cores=0.1)) == packing.PRIOR


def test_duration_is_the_median_of_green_runs_and_resources_the_maxima():
    need = est(["exact:a"], done(1, ["exact:a"], seconds=10, peak_mb=100), done(2, ["exact:a"], seconds=30),
               done(3, ["exact:a"], seconds=20, peak_mb=700), done(4, ["exact:a"], verdict="red", exit=1, seconds=1))
    assert need.seconds == 20.0 and need.ram_mb == 900


def test_a_step_with_only_red_runs_takes_its_duration_from_a_coarser_step():
    need = est(["exact:a", "prog:x"], done(1, ["exact:a", "prog:x"], verdict="red", exit=1, seconds=5),
               done(2, ["exact:b", "prog:x"], seconds=40))
    assert need.seconds == 40.0


def test_a_zero_is_no_sample_and_a_killed_run_is_no_memory_sample():
    need = est(["exact:a"], done(1, ["exact:a"], cores=0.0, peak_mb=0),
               done(2, ["exact:a"], verdict="red", exit=137, peak_mb=5000))
    assert need.cores == packing.PRIOR.cores and need.ram_mb == packing.PRIOR.ram_mb


def test_a_shared_gpu_figure_raises_but_never_lowers():
    assert est(["exact:a"], done(1, ["exact:a"], gpu_mb=1000), done(2, ["exact:a"], gpu_mb=None,
                                                                   gpu_shared_mb=9000)).gpu_mb == 9000
    assert est(["exact:a"], done(1, ["exact:a"], gpu_mb=1000), done(2, ["exact:a"], gpu_mb=None,
                                                                   gpu_shared_mb=100)).gpu_mb == 1000


def test_a_gpu_never_measured_alone_is_the_prior_and_says_so():
    need = est(["exact:a"], done(1, ["exact:a"], gpu_mb=None))
    assert need.gpu_mb == packing.PRIOR.gpu_mb and need.gpu_prior


def test_old_and_unmeasured_runs_teach_nothing():
    assert est(["exact:a"], done(1, ["exact:a"], days=31), done(2, ["exact:a"], verdict="stopped"),
               done(3, ["exact:a"], verdict="refused")) == packing.PRIOR


def test_hand_written_measurements_fold_as_missing(tmp_path):
    store = LocalLogStore(tmp_path / "rig")
    r = j.Rig(store, clock=lambda: NOW)
    at = NOW.isoformat()
    lines = [{"kind": "run", "id": "j1", "state": "waiting", "at": at, "ladder": ["exact:a"]},
             {"kind": "run", "id": "j1", "state": "done", "at": at, "verdict": "green", "seconds": -5,
              "gpu_mb": "x", "peak_mb": 900, "cores": 2.0, "slug": "../../etc"}]
    path = next((tmp_path / "rig").rglob("*")) if (tmp_path / "rig").exists() else None
    with store.transaction(j.KEY) as tx:      # the store's own writer, so the line is a line the store reads
        for line in lines:
            tx.append(line)
    run = r.runs()["j1"]
    assert run.seconds is None and run.gpu_mb is None and run.slug == "" and run.ended == at
    assert packing.history(r.runs(), now=NOW) == {}   # no seconds: no sample
```

Note for the executor: if `LocalLogStore`'s transaction appends with another method name, use it (read
`flotilla/core/storage.py`) and drop the unused `path` line; ledger it.

- [ ] **Step 2: Run them to verify they fail**

Run: `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_packing.py`
Expected: FAIL - `ModuleNotFoundError: No module named 'flotilla.rig.packing'`.

- [ ] **Step 3: Implement**

`journal.py`: `ended: str = ""` on `Run`; in `_fold_runs`, after `item.state = state`:
`if state == DONE and not item.ended: item.ended = event["at"]` (and `ended` is skipped when copying fields from
events, like `since`, so a written `ended` cannot override the fold).

`packing.py`:

```python
"""Packing runs on a rig machine by estimates (rig design, section 7, 0.11.0).

What a run will take comes from the runs measured on rig machines before it, by the lane's signature ladder: the
most exact step with a measured run answers (never the project-wide step); duration is the median of green runs (a
run stopped at the ceiling counts as at least its time), and a step with only red runs takes its duration a step
down; cores, memory and GPU memory are maxima. A GPU figure measured beside another run may raise an estimate but
never lower it. A command never measured takes a prior. One sample is enough: rig runs are few and paid.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass

from flotilla.rig import journal as j

MARGIN_MB = 2048
GPU_MARGIN_MB = 1024
KEEP, DAYS = 10, 30
MEASURED = ("green", "red", "ceiling")   # a run that ended for another reason measured its end, not the command
KILLED = 137
SKEW = dt.timedelta(minutes=5)


@dataclass(frozen=True)
class Sample:
    seconds: float
    cores: float | None
    ram_mb: int | None
    gpu_mb: int | None
    gpu_shared_mb: int | None
    verdict: str


@dataclass(frozen=True)
class Need:
    seconds: float | None
    cores: float
    ram_mb: int
    gpu_mb: int
    source: str
    gpu_prior: bool


PRIOR = Need(None, 4.0, 2048, 2048, "prior", True)


def _positive(value, kind):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return kind(value)


def history(runs: dict, *, now: dt.datetime) -> dict[str, list[Sample]]:
    since, latest = now - dt.timedelta(days=DAYS), now + SKEW
    dated = []
    for run in runs.values():
        if run.state != j.DONE or run.verdict not in MEASURED:
            continue
        try:
            ended = dt.datetime.fromisoformat(run.ended)
        except (TypeError, ValueError):
            continue
        seconds = _positive(run.seconds, float)
        if ended.tzinfo is None or not since <= ended <= latest or seconds is None:
            continue
        killed = run.exit == KILLED
        dated.append((ended, run, Sample(seconds, _positive(run.cores, float),
                                         None if killed else _positive(run.peak_mb, int),
                                         _positive(run.gpu_mb, int), _positive(run.gpu_shared_mb, int),
                                         run.verdict)))
    found: dict[str, list[Sample]] = {}
    for _, run, sample in sorted(dated, key=lambda item: item[0]):
        for step in run.ladder:
            if isinstance(step, str) and not step.startswith("project:"):
                found.setdefault(step, []).append(sample)
    return {step: samples[-KEEP:] for step, samples in found.items()}


def _duration(samples):
    usable = [s.seconds for s in samples if s.verdict in ("green", "ceiling")]
    return float(statistics.median(usable)) if usable else None


def _max(values):
    usable = [v for v in values if v is not None]
    return max(usable) if usable else None


def estimate(ladder, hist: dict[str, list[Sample]]) -> Need:
    steps = [(step, hist[step]) for step in ladder
             if isinstance(step, str) and not step.startswith("project:") and hist.get(step)]
    if not steps:
        return PRIOR
    step, samples = steps[0]
    seconds = _duration(samples)
    for _, more in steps[1:]:
        if seconds is not None:
            break
        seconds = _duration(more)
    alone, shared = _max(s.gpu_mb for s in samples), _max(s.gpu_shared_mb for s in samples)
    gpu = alone if alone is not None else None
    if shared is not None and (gpu is None or shared > gpu):
        gpu = shared if gpu is not None or shared > PRIOR.gpu_mb else None
    cores, ram = _max(s.cores for s in samples), _max(s.ram_mb for s in samples)
    return Need(seconds, cores if cores is not None else PRIOR.cores, ram if ram is not None else PRIOR.ram_mb,
                gpu if gpu is not None else PRIOR.gpu_mb, f"{len(samples)} run(s), {step.split(':', 1)[0]} match",
                gpu is None)
```

(The GPU lines read: a figure measured alone is the base; a shared figure above it raises it; with no figure alone,
a shared one raises the prior but never lowers it.)

- [ ] **Step 4: Run them to verify they pass, with the journal and run files**

Run: `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_packing.py tests/test_rig_runs_journal.py tests/test_rig_run.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add flotilla/rig/packing.py flotilla/rig/journal.py flotilla/rig/run.py tests/test_rig_packing.py
git commit -m "feat(rig): estimates of what a run takes, from runs measured on rig machines"
```

---

### Task 3: which waiting run starts, and why the others wait

**Files:**
- Modify: `flotilla/rig/packing.py`
- Test: `tests/test_rig_packing.py`

**Interfaces:**
- Consumes: `Need`, `PRIOR`, the margins (Task 2); `j.Run` (`id`, `since`), `j.Machine` (`cpus`, `ram_mb`,
  `gpu_total_mb`).
- Produces:
  - `packing.SENIOR = dt.timedelta(minutes=10)`
  - `packing.shape_known(machine) -> bool` - `cpus` and `ram_mb` both positive.
  - `packing.Pick` - frozen dataclass `(run_id: str | None, why: dict[str, str])`: the run that starts, and for
    every other waiting run a short reason in plain words (`"needs 6 cores, 3 left"`, `"held for senior j4"`,
    `"j7 goes first (longer)"`).
  - `packing.choose(waiting: list[j.Run], running: list[j.Run], machine: j.Machine, needs: dict[str, Need],
    now) -> Pick`. `waiting`: the live waiting runs of the machine's session; `running`: the runs on the machine;
    `needs`: an estimate per id in both lists.

- [ ] **Step 1: Write the failing tests**

```python
MACHINE = j.Machine(id="m1", cpus=24, ram_mb=64000, gpu_total_mb=16000)   # room: 24 cores, 61952 MB, 14976 MB


def waiting(i, minutes=0):
    return j.Run(id=f"j{i}", state=j.WAITING, since=(NOW - dt.timedelta(minutes=minutes)).isoformat())


def on(i):
    return j.Run(id=f"j{i}", state=j.RUNNING)


def need(seconds, cores=2.0, ram=1000, gpu=0):
    return packing.Need(seconds, cores, ram, gpu, "test", False)


def pick(waits, running, needs, machine=MACHINE):
    return packing.choose(waits, running, machine, needs, NOW).run_id


def test_the_longest_run_that_fits_goes_first():
    needs = {"j1": need(60), "j2": need(480, cores=6, gpu=3000), "j3": need(60)}
    assert pick([waiting(1), waiting(2), waiting(3)], [], needs) == "j2"


def test_short_runs_fill_what_a_long_one_leaves():
    needs = {"j9": need(480, cores=20), "j9b": need(480, cores=1), "j1": need(900, cores=6), "j2": need(60)}
    running = [on(9), j.Run(id="j10", state=j.RUNNING)]
    needs["j10"] = needs.pop("j9b")
    assert pick([waiting(1), waiting(2)], running, needs) == "j2"


def test_an_unmeasured_run_counts_as_the_longest():
    assert pick([waiting(1), waiting(2)], [], {"j1": need(900), "j2": packing.PRIOR}) == "j2"


def test_within_the_floor_cores_do_not_bind_but_memory_does():
    small = j.Machine(id="m1", cpus=4, ram_mb=8000, gpu_total_mb=None)        # room: 4 cores, 5952 MB
    assert pick([waiting(1)], [on(9)], {"j9": packing.PRIOR, "j1": packing.PRIOR}, small) == "j1"
    assert pick([waiting(1)], [on(9)], {"j9": need(60, ram=4000), "j1": need(60, ram=2000)}, small) is None


def test_past_the_floor_cores_bind():
    running = [on(8), on(9)]
    needs = {"j8": need(60, cores=10), "j9": need(60, cores=10), "j1": need(60, cores=6)}
    assert pick([waiting(1)], running, needs) is None


def test_measured_cores_are_clamped_to_the_machine():
    assert pick([waiting(1)], [on(9)], {"j9": need(60, cores=1), "j1": need(60, cores=40)}) == "j1"


def test_a_senior_that_fits_goes_before_a_longer_later_run():
    assert pick([waiting(1, minutes=11), waiting(2)], [], {"j1": need(60), "j2": need(900)}) == "j1"


def test_a_senior_holds_the_room_it_needs():
    running = [on(8), on(9)]
    needs = {"j8": need(600, cores=10), "j9": need(600, cores=10), "j1": need(900, cores=8), "j2": need(30)}
    got = packing.choose([waiting(1, minutes=11), waiting(2)], running, MACHINE, needs, NOW)
    assert got.run_id is None and "senior j1" in got.why["j2"]


def test_a_later_run_may_take_room_the_senior_does_not_need():
    running = [on(9)]
    needs = {"j9": need(600, cores=4, gpu=14000), "j1": need(900, cores=4, gpu=4000), "j2": need(30, gpu=0)}
    assert pick([waiting(1, minutes=11), waiting(2)], running, needs) == "j2"


def test_a_run_bigger_than_the_machine_starts_alone_on_an_empty_machine():
    assert pick([waiting(1)], [], {"j1": need(60, ram=90000)}) == "j1"
    assert pick([waiting(1)], [on(9)], {"j9": need(60, cores=1), "j1": need(60, ram=90000)}) is None


def test_a_senior_too_big_for_the_machine_reserves_all_of_it():
    needs = {"j9": need(60, cores=1), "j1": need(60, gpu=20000), "j2": need(10, cores=1, ram=100, gpu=0)}
    assert pick([waiting(1, minutes=11), waiting(2)], [on(9)], needs) is None


def test_ties_go_by_run_number():
    assert pick([waiting(2), waiting(1)], [], {"j1": need(60), "j2": need(60)}) == "j1"


def test_a_machine_without_gpu_does_not_count_gpu_memory():
    cpu_only = j.Machine(id="m1", cpus=8, ram_mb=32000, gpu_total_mb=None)
    assert pick([waiting(1)], [on(9)], {"j9": need(60, gpu=20000), "j1": need(60, gpu=2048)}, cpu_only) == "j1"


def test_every_waiting_run_that_does_not_start_says_why():
    got = packing.choose([waiting(1), waiting(2)], [], MACHINE, {"j1": need(900), "j2": need(60)}, NOW)
    assert got.run_id == "j1" and set(got.why) == {"j2"} and got.why["j2"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_packing.py`
Expected: FAIL - `AttributeError: module 'flotilla.rig.packing' has no attribute 'choose'`.

- [ ] **Step 3: Implement**

```python
SENIOR = dt.timedelta(minutes=10)


@dataclass(frozen=True)
class Pick:
    run_id: str | None
    why: dict


def shape_known(machine) -> bool:
    return bool(machine.cpus) and bool(machine.ram_mb)


def _number(run) -> int:
    return int(run.id[1:])


def _age(run, now):
    try:
        return now - dt.datetime.fromisoformat(run.since)
    except (TypeError, ValueError):
        return dt.timedelta(0)


def choose(waiting, running, machine, needs, now) -> Pick:
    cores_room = float(machine.cpus)
    room = {"cores": cores_room, "MB of memory": machine.ram_mb - MARGIN_MB}
    if machine.gpu_total_mb is not None and machine.gpu_total_mb > 0:
        room["MB of GPU memory"] = machine.gpu_total_mb - GPU_MARGIN_MB

    def amounts(item):
        n = needs[item.id]
        found = {"cores": min(n.cores, cores_room), "MB of memory": n.ram_mb}
        if "MB of GPU memory" in room:
            found["MB of GPU memory"] = n.gpu_mb
        return found

    used = {key: sum(amounts(r)[key] for r in running) for key in room}
    binding = [key for key in room if key != "cores" or len(running) >= 2]   # the floor: cores bind past two runs

    def short(item, extra=None):
        """The first resource `item` lacks, with `extra` (a senior's share) held back, or ""."""
        mine = amounts(item)
        for key in binding:
            held = amounts(extra)[key] if extra is not None and mine[key] > 0 else 0
            left = room[key] - used[key] - held
            if mine[key] > left:
                return f"needs {mine[key]:g} {key}, {max(left, 0):g} left"
        return ""

    def bigger_than_machine(item):
        return any(amounts(item)[key] > room[key] for key in room if key != "cores")

    def fits(item):
        return not running or not short(item)

    order = sorted(waiting, key=_number)
    why: dict[str, str] = {}
    seniors = [r for r in order if _age(r, now) >= SENIOR]
    senior = seniors[0] if seniors else None
    if senior is not None and fits(senior):
        return Pick(senior.id, {r.id: f"senior {senior.id} goes first" for r in order if r is not senior})
    candidates = []
    for item in order:
        if item is senior:
            why[item.id] = f"senior; {short(item) or 'waits for the machine to empty'}"
            continue
        if senior is not None and bigger_than_machine(senior):
            why[item.id] = f"held for senior {senior.id}, which needs the whole machine"
        elif senior is not None and short(item, senior):
            why[item.id] = f"held for senior {senior.id}: {short(item, senior)}"
        elif not fits(item):
            why[item.id] = short(item)
        else:
            candidates.append(item)
    if not candidates:
        return Pick(None, why)
    chosen = max(candidates, key=lambda r: (math.inf if needs[r.id].seconds is None else needs[r.id].seconds,
                                            -_number(r)))
    for item in candidates:
        if item is not chosen:
            why[item.id] = f"{chosen.id} goes first (longer)"
    return Pick(chosen.id, why)
```

Check by hand before running: `test_short_runs_fill_what_a_long_one_leaves` has two runs on the machine (past the
floor), cores 20 + 1 used, j1 needs 6 > 3 left, j2 needs 2 <= 3 - j2 starts. `test_a_senior_too_big...`: j1 needs
20000 MB GPU > 14976 room, so `bigger_than_machine(j1)`, the machine is not empty, j1 does not fit, j2 is held.

- [ ] **Step 4: Run them to verify they pass**

Run: `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_packing.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add flotilla/rig/packing.py tests/test_rig_packing.py
git commit -m "feat(rig): choose which waiting run starts - longest first, short ones fill, seniors hold room"
```

---

### Task 4: the journal starts runs by `choose`; every waiting run tries

**Files:**
- Modify: `flotilla/rig/journal.py` (`start_run`, new `would_start`, `Run.waits`)
- Modify: `flotilla/rig/run.py` (`Run.take_room`, `Run.shape`)
- Test: `tests/test_rig_runs_journal.py`, `tests/test_rig_run.py`

**Interfaces:**
- Consumes: `packing.choose`, `estimate`, `history`, `shape_known` (Tasks 2-3).
- Produces:
  - `Rig.would_start(run_id, machine_id, *, alive) -> str` - `""` when this run is the one `choose` (or, shape
    unknown, the session's line) would start on that machine now, else the reason it waits. Reads, never writes.
  - `Rig.start_run(...)` - same signature and result as today; when it refuses because of packing, it records the
    run's `waits` reason if that changed (one event per change, never per poll).
  - `j.Run.waits: str` (TEXT).

- [ ] **Step 1: Write the failing tests**

`tests/test_rig_runs_journal.py` - new helpers at the end of the helper block:

```python
def shaped_machine(r, session=None, cpus=24, ram_mb=64000, gpu_total_mb=16000):
    s, m = ready_machine(r, session)
    r.note(m.id, cpus=cpus, ram_mb=ram_mb, gpu_total_mb=gpu_total_mb)
    return s, m


def measure(r, s, m, ladder, **measured):
    run = r.queue_run(s.id, who="minor 8", project="P", revision="a" * 40, program="node", ladder=ladder, pid=7,
                      mark="m")
    r.start_run(run.id, m.id, alive=ALIVE, roomy=True)
    return r.finish_run(run.id, "green", exit=0, **measured)


def queue_ladder(r, s, ladder, pid=1):
    return r.queue_run(s.id, who="minor 8", project="P", revision="a" * 40, program="node", ladder=ladder, pid=pid,
                       mark="m")
```

Tests:

```python
def test_the_longest_measured_run_starts_before_an_earlier_short_one(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r)
    measure(r, s, m, ("exact:long",), seconds=480.0, cores=6.0, peak_mb=900)
    measure(r, s, m, ("exact:short",), seconds=30.0, cores=1.0, peak_mb=200)
    short, long = queue_ladder(r, s, ("exact:short",), pid=1), queue_ladder(r, s, ("exact:long",), pid=2)
    assert r.start_run(short.id, m.id, alive=ALIVE, roomy=True) is None
    assert "goes first" in r.runs()[short.id].waits
    assert r.start_run(long.id, m.id, alive=ALIVE, roomy=True) is not None
    assert r.start_run(short.id, m.id, alive=ALIVE, roomy=True) is not None


def test_would_start_answers_without_writing(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r)
    one, two = queue_ladder(r, s, ("exact:a",), pid=1), queue_ladder(r, s, ("exact:a",), pid=2)
    before = len(r.store.read(j.KEY).records)
    assert r.would_start(one.id, m.id, alive=ALIVE) == "" and r.would_start(two.id, m.id, alive=ALIVE)
    assert len(r.store.read(j.KEY).records) == before


def test_a_waits_reason_is_written_once_per_change(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r)
    one, two = queue_ladder(r, s, ("exact:a",), pid=1), queue_ladder(r, s, ("exact:a",), pid=2)
    r.start_run(two.id, m.id, alive=ALIVE, roomy=True)
    before = len(r.store.read(j.KEY).records)
    r.start_run(two.id, m.id, alive=ALIVE, roomy=True)
    assert len(r.store.read(j.KEY).records) == before


def test_memory_estimates_bind_within_the_floor(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r, cpus=4, ram_mb=8000, gpu_total_mb=None)
    measure(r, s, m, ("exact:big",), seconds=60.0, cores=1.0, peak_mb=4000)
    first, second = queue_ladder(r, s, ("exact:big",), pid=1), queue_ladder(r, s, ("exact:big",), pid=2)
    assert r.start_run(first.id, m.id, alive=ALIVE, roomy=False) is not None
    assert r.start_run(second.id, m.id, alive=ALIVE, roomy=False) is None


def test_two_unmeasured_runs_share_a_small_machine(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r, cpus=4, ram_mb=16000, gpu_total_mb=None)
    first, second = queue_ladder(r, s, ("exact:a",), pid=1), queue_ladder(r, s, ("exact:b",), pid=2)
    assert r.start_run(first.id, m.id, alive=ALIVE, roomy=False) is not None
    assert r.start_run(second.id, m.id, alive=ALIVE, roomy=False) is not None


def test_a_dead_waiting_run_is_never_chosen_and_reserves_nothing(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = shaped_machine(r)
    dead = queue_ladder(r, s, ("exact:a",), pid=404)
    clock["at"] = T0 + dt.timedelta(minutes=20)              # dead and senior
    live = queue_ladder(r, s, ("exact:b",), pid=2)
    assert r.start_run(live.id, m.id, alive=ALIVE, roomy=True) is not None


def test_readings_still_refuse_past_the_floor(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = shaped_machine(r)
    for pid in (1, 2):
        run = queue_ladder(r, s, ("exact:tiny",), pid=pid)
        assert r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
        r.command_started(run.id)
    clock["at"] = T0 + dt.timedelta(seconds=120)
    third = queue_ladder(r, s, ("exact:tiny",), pid=3)
    assert r.start_run(third.id, m.id, alive=ALIVE, roomy=False) is None
    assert r.start_run(third.id, m.id, alive=ALIVE, roomy=True) is not None
```

The existing journal tests use `ready_machine`, whose shape is unknown: they keep pinning the 0.10.x fallback
(arrival order), unchanged.

`tests/test_rig_run.py` - the path the plan reviews found broken, through real `rig run` processes:

```python
def test_the_longer_run_that_came_second_starts_first(world, box, tree, tmp_path):
    opened(world)
    raised(world, tree)
    assert rig_run(world, tree, "--", "sleep", "3")[0] == 0           # measured: the long one
    assert rig_run(world, tree, "--", "true")[0] == 0                 # measured: the short one
    holder = background_rig_run(world, tree, box, tmp_path, "--", "sleep", "8")   # fills the floor ...
    other = background_rig_run(world, tree, box, tmp_path, "--", "sleep", "8")    # ... with two runs
    wait_for(lambda: running(world) == 2)
    short = background_rig_run(world, tree, box, tmp_path, "--", "true")
    wait_for(lambda: waiting_ids(world) == ["j5"])
    long = background_rig_run(world, tree, box, tmp_path, "--", "sleep", "3")
    for child in (holder, other, short, long):
        child.wait(120)
    runs = journal(world).runs()
    assert runs["j6"].command_at < runs["j5"].command_at


def test_only_the_run_that_would_start_reads_the_machine(world, box, tree, tmp_path, monkeypatch):
    ...  # see note
```

`waiting_ids(world)`: the ids of runs in `waiting`, sorted. The executor sets the readings in the stand-in so that
past the floor exactly one more run has room (the stand-in's `RIG_TEST_NO_ROOM` and its readings are in
`tests/rigrun_child.py`); if the floor-filling arrangement cannot be made deterministic with the stand-in, the
executor replaces it with a journal-level test driving `take_room` with a fake `_readings` and ledgers it.
`test_only_the_run_that_would_start_reads_the_machine`: three waiting runs past the floor, `run.READ` set to a
function that records which run asked; one poll of each `take_room`; assert only the chosen run's id is recorded.

- [ ] **Step 2: Run them to verify they fail**

Run: `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_runs_journal.py tests/test_rig_run.py -k "longest or would_start or once_per_change or within_the_floor or small_machine or dead_waiting or past_the_floor or came_second or would_start_reads"`
Expected: FAIL - `would_start` missing; the longest-first test starts the short run; `came_second` runs j5 first
(or both runs time out, the bug both reviews found). The floor and dead-run tests may pass already: they pin
behaviour that must survive.

- [ ] **Step 3: Implement**

`journal.py`:

```python
    def _verdict(self, runs, machine, run, alive) -> str:
        """"" when `run` is the one to start on `machine` now (before the second barrier), else why it waits."""
        from flotilla.rig import packing
        live = [item for item in runs.values() if item.state == WAITING and item.session == run.session
                and alive(item.pid, item.mark)]
        if machine.session != run.session or run.id not in {item.id for item in live}:
            return "not waiting in this machine's session"
        if not packing.shape_known(machine):
            head = min(live, key=lambda item: _number(item.id))
            return "" if head.id == run.id else f"{head.id} came first"
        there = [item for item in runs.values() if item.state == RUNNING and item.machine == machine.id]
        hist = packing.history(runs, now=self.now())
        needs = {item.id: packing.estimate(item.ladder, hist) for item in live + there}
        picked = packing.choose(live, there, machine, needs, self.now())
        return "" if picked.run_id == run.id else picked.why.get(run.id) or f"{picked.run_id} goes first"

    def would_start(self, run_id, machine_id, *, alive) -> str:
        records = self.store.read(KEY).records
        runs, machine = _fold_runs(records), _fold(records)[1].get(machine_id)
        run = runs.get(run_id)
        if run is None or machine is None or run.state != WAITING or machine.state not in (READY, BUSY):
            return "no such waiting run or ready machine"
        return self._verdict(runs, machine, run, alive)
```

and in `start_run` replace the head check with:

```python
            reason = self._verdict(runs, machine, run, alive)
            if reason:
                if reason != run.waits:
                    self._append(tx, "run", run_id, WAITING, waits=reason)
                return None
```

Careful: the fold sets `since` on every `waiting` event - change `_fold_runs` so `since` is set only on the FIRST
`waiting` event of a run (`if state == WAITING and not item.since`), or the `waits` events would reset seniority.
Add `waits: str = ""` to `Run` and `"waits"` to `TEXT`. The FLOOR / settle / cap / `roomy` block below stays as is.

`run.py` `take_room`:

```python
    def take_room(self, session, deadline) -> bool:
        said = ""
        while True:
            machines = [m for m in self.rig.machines().values() if m.session == session.id]
            for machine in machines:
                if machine.state not in (j.READY, j.BUSY) or not machine.address:
                    continue
                machine = self.shape(machine)
                reason = self.rig.would_start(self.run.id, machine.id, alive=self.alive)
                if reason:
                    if reason != said:
                        _say(f"rig run {self.run.id} waits: {reason}")
                        said = reason
                    continue
                roomy = len(self.rig.on(machine.id)) < j.FLOOR or self.roomy(machine)
                if self.rig.start_run(self.run.id, machine.id, alive=self.alive, roomy=roomy):
                    self.machine = self.rig.machines()[machine.id]
                    return True
            head = self.rig.head(session.id, self.alive)
            if head is not None and head.id == self.run.id and not any(m.state in (j.READY, j.BUSY)
                                                                         for m in machines):
                code = commands.raise_machine(...)   # unchanged arguments and handling of 2 / 0
                ...
            if time.monotonic() >= deadline:
                ...                                  # unchanged
            SLEEP(POLL)

    def shape(self, machine):
        """The machine's size, read once: packing needs it before the first try, not only past the floor."""
        if packing.shape_known(machine):
            return machine
        self.roomy(machine)
        return self.rig.machines()[machine.id]
```

(`_say` is the module's printer to stderr; if the waiting reason should go to stdout too, it does not - only the
last line does.) `roomy()` past the floor is now asked only by the run `would_start` named: ten waiting runs make
one ssh call per poll, not ten.

- [ ] **Step 4: Run the rig tests**

Run: `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_*.py`
Expected: PASS. `test_two_runs_share_a_machine` (test_rig_run.py) keeps passing on a 4-vCPU CI host: within the
floor cores do not bind, and two prior runs need 4 GB of memory.

- [ ] **Step 5: Commit**

```bash
git add flotilla/rig/journal.py flotilla/rig/run.py tests/test_rig_runs_journal.py tests/test_rig_run.py
git commit -m "feat(rig): a waiting run starts by its estimate; every waiting run tries, only the chosen one reads the machine"
```

---

### Task 5: `flotilla rig` shows why a run waits

**Files:**
- Modify: `flotilla/rig/surface.py` (`run_lines`)
- Test: `tests/test_rig_surface.py`

- [ ] **Step 1: Write the failing test** (use the file's `rig(state, at)` and `machine_ready(r, s)`; queue runs
  with `r.queue_run(...)`, measure one with `start_run` + `finish_run`; move the clock by building `rig(state,
  at=T0 + 12 min)` for the read)

```python
def test_a_waiting_run_shows_why_its_estimate_and_seniority(tmp_path):
    state = state_on(tmp_path)
    r = rig(state)
    s = r.open_session("p", "x", hours=2, budget=1.0)
    m = machine_ready(r, s)
    measured = r.queue_run(s.id, who="minor 8", project="P", revision="a" * 40, program="node",
                           ladder=("exact:scene",), pid=7, mark="m")
    r.start_run(measured.id, m.id, alive=lambda p, k: True, roomy=True)
    r.finish_run(measured.id, "green", exit=0, seconds=480.0, cores=6.0, peak_mb=3000, gpu_mb=3000)
    r.queue_run(s.id, who="minor 8", project="P", revision="a" * 40, program="node", ladder=("exact:scene",),
                pid=8, mark="m")
    later = rig(state, at=T0 + dt.timedelta(minutes=12))
    lines = surface.run_lines(later, T0 + dt.timedelta(minutes=12))
    line = next(l for l in lines if "waits" in l)
    assert "~8 min" in line and "6 cores" in line and "senior" in line and "GPU: prior" not in line
```

- [ ] **Step 2: Run it to verify it fails** - `flotilla lane run --root . -- uv run --with pytest python -m pytest tests/test_rig_surface.py -k why`
  Expected: FAIL (the line reads `waits` only).

- [ ] **Step 3: Implement** - before the loop: `runs = rig.runs(); hist = packing.history(runs, now=now)`; the
  waiting branch:

```python
        elif run.state == j.WAITING:
            need = packing.estimate(run.ladder, hist)
            since = _when(run.since)
            took = f"~{max(1, round(need.seconds / 60))} min, " if need.seconds is not None else ""
            gpu = "GPU: prior" if need.gpu_prior else f"{need.gpu_mb} MB GPU"
            senior = "senior, " if since and now - since >= packing.SENIOR else ""
            reason = f": {run.waits}" if run.waits else ""
            lines.append(f"  run {run.id} ({_who(run.who)}) waits{reason} ({senior}{took}{need.cores:g} cores, "
                         f"{need.ram_mb} MB, {gpu}; {need.source})")
```

`run.waits` is journal text: it is already cleaned by `visible` in the fold; it never reaches a line the person
pastes (`run_lines` is display only - the executor checks that no paste line includes it).

- [ ] **Step 4: Run it to verify it passes** - `... tests/test_rig_surface.py` - PASS.

- [ ] **Step 5: Commit** - `git commit -m "feat(rig): flotilla rig shows why a run waits, its estimate and seniority"`

---

### Task 6: docs, spec, TODO and version 0.11.0

**Files:** `docs/specs/2026-10-06-rig-design.md` (section 7 as below; section 11: stage 2c done), `README.md` (rig
section, one paragraph), `USER_MANUAL.md` (the `flotilla rig` waiting line and the `rig run ... waits:` line),
`CHANGELOG.md` (0.11.0: what packing does, the floor reading, readings now the container's size, history starts
counting `ended` from old records too), `TODO.md`, `pyproject.toml`, `flotilla/__init__.py`,
`.claude-plugin/plugin.json`.

- [ ] **Step 1: Section 7's paragraph, as built:**

> A run waits for **room**, and room is two barriers. **Estimates:** what a run will take (duration, cores, memory,
> GPU memory) comes from the runs measured on rig machines before it, by the lane's signature ladder (never the
> project-wide step) - the median duration of green runs, the maxima of the rest; a GPU figure measured beside
> another run may raise an estimate but never lower it; a command never measured takes 4 cores, 2 GB memory and
> 2 GB GPU memory. A run fits when the estimates of the runs on the machine plus its own stay within the machine's
> memory minus 2 GB and GPU memory minus 1 GB, and - past two runs - its CPUs; the machine's size is its
> container's (cgroup limit and quota). Of the runs that fit, the longest goes first and short ones fill the rest; a
> run waiting 10 minutes is senior, and until it fits no later run takes room it needs - all of the machine, when
> the senior is bigger than it; a run bigger than the machine starts alone on an empty one. **Live readings:**
> beyond two runs, the machine's free memory (reclaimable page cache counted free) and GPU memory above their
> margins, every run on it in its command for a minute, and fewer runs than its CPUs. A waiting run says why.

- [ ] **Step 2: TODO.md** gains, under the rig: EASY backfill (a later run may use a senior's room if it ends before
  the senior can start); memory as PSS (`smaps_rollup`) and `/dev/shm`; setup-phase measurements kept with the
  command's; a per-run disk estimate; OOM kills read from `memory.events`; peak cores per sampling window instead
  of the mean; the journal grows without bound.

- [ ] **Step 3: Bump the version to 0.11.0 and run the checks**

Run: `python3 tools/check_no_cyrillic.py && flotilla lane run --root . -- uv run --with pytest python -m pytest tests`
Expected: no Cyrillic; the full suite passes.

- [ ] **Step 4: Commit**

```bash
git add -A docs README.md USER_MANUAL.md CHANGELOG.md TODO.md pyproject.toml flotilla/__init__.py .claude-plugin/plugin.json
git commit -m "docs(rig): packing by estimates documented; version 0.11.0"
```
