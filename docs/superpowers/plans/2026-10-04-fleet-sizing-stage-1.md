# Fleet sizing, stage 1 (machine and work) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `flotilla fleet size` and `flotilla spawn --recommended` recommend a fleet from the machine (memory, test-run
cost, disk) and the work (ledger rows in flight, named tasks, a GitHub tracker, TODO files), naming the cap that set
each number.

**Architecture:** Pure functions over recorded inputs (meminfo text, `vm_stat` text, TODO text, `gh` JSON, ledger rows)
compute signals; one pure function, `sizing.recommend`, turns signals into counts, the binding cap and the lines to
print; a thin gatherer reads the real machine and repository and calls them. The lane's tier runner gains a peak-memory
sampler, recorded beside the measured seconds.

**Tech Stack:** Python 3.11+ standard library, `git`, optional `gh`; pytest for tests (`uv run --with pytest python
-m pytest tests/ -o addopts="" -q -p no:cacheprovider`).

**Spec:** `docs/specs/2026-10-04-fleet-sizing-design.md` (sections 1, 2.1, 2.3, 3, 4, 5 stage 1, 6, 7).

## Global Constraints

- Standard library only; Linux and macOS; any project language.
- A signal that could not be read is **unknown**: left out of `min` and named in the output, never a silent default.
- Nothing is raised without the person's yes; `fleet size` is read-only.
- `authors = max(1, min(...))` except when memory cannot hold even one more seat: then the recommendation is "raise
  nothing now" with the reason.
- `[fleet.sizing] max_seats`, when set, is never exceeded.
- Code (K_eff, hotspots) and money are stages 2 and 3: in this stage they print as "not measured in this version".
- Commits: Conventional Commits, English; every guard gets an injection that turns its test red.

## Review Focus

1. A fresh machine and project - no receipts, no ledger history, no seat tree: the recommendation stands on defaults
   that are each named as such, and nothing crashes or divides by zero. (Task 5 test `test_a_fresh_project...`)
2. macOS memory: `vm_stat` pages times the page size it prints, not 4096 assumed. (Task 2 test
   `test_vm_stat_uses_its_own_page_size`)
3. A TODO file with done items, nested bullets and a fenced code block holding `- [ ]`: only open top items outside
   code count. (Task 3 test `test_todo_items_skip_done_nested_and_code`)
4. A tracker configured while `gh` is missing or unauthenticated: the tracker is unknown and named, the rest stands.
   (Task 3 test `test_a_tracker_gh_cannot_ask_is_unknown`)
5. Free memory below the floor plus one seat: "raise nothing now", not "1 author". (Task 5 test
   `test_no_room_for_a_seat_says_raise_nothing`)

---

### Task 1: Peak memory of a tier run

**Files:**
- Create: `flotilla/lane/peak.py`
- Modify: `flotilla/onboard/firstrun.py` (`TierRun`, `run_tier`, add `measure_peaks`, `load_peaks`)
- Modify: `flotilla/ledger/receipts.py` (record peaks beside seconds)
- Test: `tests/test_lane_peak.py`, `tests/test_onboard_firstrun.py`

**Interfaces:**
- Produces: `peak.GroupPeak(pgid: int, *, sample: float = 1.0, read=None)` - a context manager running a daemon
  thread that sums the resident memory of every process in group `pgid` each `sample` seconds; `.peak_mb -> int | None`.
  `peak.group_rss_kb(pgid, *, proc_root=Path("/proc"), run=subprocess.run) -> int | None` (Linux: `/proc/<pid>/stat`
  field 5 is the group, `/proc/<pid>/statm` field 2 x page size; else `ps -o rss= -g <pgid>`).
- Produces: `TierRun.peak_mb: int | None = None`; `firstrun.measure_peaks(state, repo_key, peaks: dict[str, int])`
  keeps the larger of the stored and the new value per tier in the measurements file's `[peak_mb]` table;
  `firstrun.load_peaks(state, repo_key) -> dict[str, int]`.

- [ ] **Step 1: failing tests** - `group_rss_kb` over a fake `/proc` with two processes in group 7 and one in group 8
  returns the sum of the two; `GroupPeak` with an injected `read` returning 100, 300, 200 kB keeps 300 kB (0 MB rounded
  up to 1); `run_tier` over `python -c "x = bytearray(60_000_000); import time; time.sleep(1.5)"` reports
  `peak_mb >= 50`; `measure_peaks` keeps the larger value on a second, smaller run and ignores None.
- [ ] **Step 2:** run them: FAIL (no module `flotilla.lane.peak`).
- [ ] **Step 3:** implement `peak.py`; in `run_tier`, wrap `proc.communicate` in `with GroupPeak(proc.pid) as p:` and
  return `peak_mb=p.peak_mb` for a green run; in `run_receipt`, collect `ran_peaks[r.name] = r.peak_mb` for green tiers
  that ran and call `measure_peaks(state, repo_key, ran_peaks)` beside `measure_once`.
- [ ] **Step 4:** tests PASS; the full `tests/test_ledger_receipts.py` and `tests/test_onboard_firstrun.py` PASS.
- [ ] **Step 5:** commit `feat(lane): record a tier run's peak memory beside its time`.

### Task 2: Machine resources now

**Files:**
- Create: `flotilla/core/resources.py`
- Test: `tests/test_core_resources.py`

**Interfaces:**
- Produces: `available_mb(*, meminfo=Path("/proc/meminfo"), vm_stat_text: str | None = None, run=subprocess.run,
  os_name=sys.platform) -> int | None` (Linux `MemAvailable`; macOS free + inactive + speculative pages from `vm_stat`,
  times the page size from its first line); `parse_vm_stat(text) -> int | None` (MB); `free_disk_mb(path) -> int | None`
  (`shutil.disk_usage`); `tree_mb(path, *, run=subprocess.run) -> int | None` (`du -sk`, timeout 20 s).

- [ ] **Step 1: failing tests** - `parse_vm_stat` on a recorded macOS sample with `page size of 16384 bytes` returns
  (free + inactive + speculative) x 16384 / 2**20 (`test_vm_stat_uses_its_own_page_size`); garbage returns None;
  `available_mb` on a fake meminfo returns MemAvailable // 1024; `free_disk_mb(tmp_path)` > 0; `tree_mb` with a `run`
  answering `"1024\t/x"` returns 1; with `du` failing returns None.
- [ ] **Step 2:** FAIL (no module).
- [ ] **Step 3:** implement; `fleet/spawn.read_available_mb` and `lane/machine.read_meminfo` callers stay as they are
  (this module is the sizing's reader; unifying them is out of scope).
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(core): read free memory on macOS too, free disk and a tree's size`.

### Task 3: The backlog from four sources

**Files:**
- Create: `flotilla/fleet/backlog.py`
- Test: `tests/test_fleet_backlog.py`

**Interfaces:**
- Produces: `Source(name: str, main: int | None, minor: int | None, note: str)` (None = unknown);
  `Backlog(sources: list[Source])` with `.main`, `.minor` (sums over known sources), `.unknown` (names);
  `todo_items(text: str, *, minor_prefix="[minor]") -> tuple[int, int]`; `from_files(root, globs) -> Source`;
  `from_tasks(count: int | None, file: Path | None) -> Source`; `from_github(root, *, labels, minor_labels, run) -> Source`;
  `from_ledger(rows, profile) -> Source` (open rows in `claimed` or `fixing`: work in flight, which holds authors);
  `gather(root, profile, rows, *, tasks, tasks_file, run) -> Backlog`.

- [ ] **Step 1: failing tests** -
  `todo_items` counts `- [ ] a`, `* [ ] b`, not `- [x] c`, not `~~- [ ] d~~`, not items under a `## Done` heading, not
  nested `  - [ ] e` (a sub-item of an item), not `- [ ]` inside a fenced block; `[minor]` prefix counts as minor
  (`test_todo_items_skip_done_nested_and_code`); a file with no checkboxes counts top-level bullets under headings;
  `from_files` with a glob matching nothing is a known zero, a glob escaping the root (`../x`) is refused as unknown;
  `from_github` with `run` raising FileNotFoundError or exiting 4 is unknown with the reason
  (`test_a_tracker_gh_cannot_ask_is_unknown`); with JSON of 3 issues, one labelled `size:small`, gives main 2 minor 1;
  `from_tasks(5, None)` is main 5; a tasks file with 3 non-blank lines, one `[minor]`, is main 2 minor 1;
  `from_ledger` counts claimed and fixing rows only.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** implement; `gh issue list --state open --limit 500 --json number,labels`, timeout 25 s.
- [ ] **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(fleet): count the backlog from the ledger, named tasks, GitHub issues and TODO files`.

### Task 4: The fleet's own pace

**Files:**
- Create: `flotilla/fleet/pace.py`
- Test: `tests/test_fleet_pace.py`

**Interfaces:**
- Produces: `Pace(handovers_per_author_hour: float, review_hours: float, measured: bool, note: str)`;
  `pace(rows, *, now, window_days=30) -> Pace` - median claim->hand hours over rows handed in the window gives the
  author rate (1 / median); median hand->first `accept` or `fix` hours gives review hours; under 3 samples each
  falls back to 1.0 per hour and 0.33 h, said in `note`.

- [ ] **Step 1: failing tests** - rows with history claim at 10:00, hand at 12:00 (three such rows) give 0.5 per hour;
  hand at 12:00, accept at 12:30 (three) give 0.5 h; two samples fall back with the note; events outside the window
  are ignored.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(fleet): measure how fast this fleet hands over and reviews`.

### Task 5: The rule

**Files:**
- Create: `flotilla/fleet/sizing.py`
- Test: `tests/test_fleet_sizing.py`

**Interfaces:**
- Consumes: `Backlog`, `Pace` (tasks 3, 4).
- Produces: `Machine(free_mb, floor_mb, seat_mb, live_seats, run_mb, run_seconds, lane_capacity, free_disk_mb,
  tree_mb)` (each `int | float | None`); `Recommendation(counts: dict[str, int], authors: int, caps: dict[str, int |
  None], binding: str, lines: list[str], raise_nothing: str)`; `recommend(machine, backlog, pace, profile) ->
  Recommendation`.
- Caps (spec section 3): `backlog = main + minor`; `memory = floor((free - floor - run_mb) / seat) - others`;
  `runs = (runs_that_fit x 3600 / run_seconds) / handovers_per_author_hour` with `runs_that_fit =
  min(lane_capacity, floor((free - live_seats x seat) / run_mb))`; `disk = floor(0.5 x free_disk / tree) - others`;
  `max_seats - others` when set. `others` = orchestrator 1 + sender 1 + reviewers + judge. `code` and `money` caps
  print "not measured in this version".
- Reviewers `max(1, ceil(authors x handovers_per_author_hour x review_hours))`; main/minor by the backlog mix, at
  least 1 main when any main item exists; judge 1 when `[judge] required` or a `[deploy]` table exists.

- [ ] **Step 1: failing tests** - a table of cases: each cap binding in turn (`binding` names it and the first line
  says `limited by <cap>`); an unknown signal is absent from `caps` and named in `lines`; `max_seats` caps the total;
  `test_a_fresh_project_stands_on_named_defaults` (all measurements None, backlog 0 known) gives 1 author, 1
  reviewer and lines naming every default; `test_no_room_for_a_seat_says_raise_nothing` (free below floor + seat)
  gives `raise_nothing` with the numbers; reviewers grow with authors and pace; judge rule.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS.
- [ ] **Step 5:** commit `feat(fleet): recommend a fleet as the smallest cap, and name it`.

### Task 6: `fleet size`, `spawn --recommended`, the skill and the README

**Files:**
- Modify: `flotilla/fleet/sizing.py` (add `gather(ledger, *, tasks, tasks_file, census, run) -> Recommendation`)
- Modify: `flotilla/cli.py` (`fleet size [--tasks N] [--tasks-file P] [--json]`; `spawn --recommended`, `--tasks`,
  `--tasks-file`), `flotilla/fleet/commands.py`
- Modify: `skills/spawn/SKILL.md` (no arguments: show `fleet size`, ask "raise this fleet?", counts editable),
  `README.md` (a "How big a fleet" section; `[fleet.sizing]` keys), `docs/specs/2026-09-22-decisions-log.md`
- Test: `tests/test_fleet_cli.py`, `tests/test_skills.py`

**Interfaces:**
- Consumes: everything above; the census (live seats), `load_measurements`/`load_peaks` (task 1), `available_mb`,
  `free_disk_mb`, `tree_mb` (task 2), the profile's `[fleet.sizing]` (`tracker`, `labels`, `minor_labels`,
  `backlog_files`, `max_seats`).
- `run_tier`'s measured tiers: the run cost is the push tiers' total seconds and the largest peak.

- [ ] **Step 1: failing tests** - `fleet size` in an onboarded fake world prints `recommended:` and a `limited by`
  line, exits 0, and never calls `claude --bg`; `--json` gives `counts`, `caps`, `binding`; `spawn --recommended
  --dry-run` plans exactly the recommended counts beside the posts already held (like `--fill`); `--tasks 4` moves
  the backlog cap; the spawn skill names `fleet size` and asks before raising; `CALL` test: every CLI call a skill
  names exists.
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS, then the full suite through the lane.
- [ ] **Step 5:** commit `feat(fleet): fleet size and spawn --recommended` and the docs commit.
