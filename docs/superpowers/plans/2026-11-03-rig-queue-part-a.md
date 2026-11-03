# Rig queue, part A (0.27.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task by task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- Exclusive rig runs.
- A `rig run` that stands in line for the whole session and says its place and expected start.
- The order on `flotilla rig`.
- A watch item when the handover breaks.
- Two small fixes:
  - a stopped run brings back its `--get` paths;
  - a draining machine of a closed session stops blocking the machine ceiling.

**Architecture:**
- The rules for exclusive runs live in `Rig._verdict` (`flotilla/rig/journal.py`), ahead of packing, so the check made
  without the lock and the one inside it agree.
- A new module, `flotilla/rig/line.py`, works out a session's line order and expected start times. Two places use it:
  - `rig run` for its own messages;
  - `surface.run_lines` for the screen.
- Everything else is small edits in the places named below.

**Tech Stack:** Python 3.11 stdlib, pytest.

**Spec:** `docs/specs/2026-11-03-rig-queue-design.md`, sections 3 (A1-A5) and 5.

## Global Constraints

- Default behaviour is unchanged for a project without `[rig] exclusive`: runs are shared and packed as today.
- **A record without the `exclusive` field folds as shared.**
  - `exclusive` is kept only when it is literally `true`.
  - `ceiling` is an int from 1 to 86400. Anything else folds as `None`.
- Constants:
  - `IDLE_ALARM = 2 min`, in `surface.py`;
  - `GET_ON_STOP = 120.0` s, in `run.py`.
- Messages:
  - The output of `flotilla rig` and of `rig run` names a program only through the journal's checked `program`, never
    a command's text.
  - Seat names go through `surface._who`.
- Version 0.27.0 in three places: `pyproject.toml`, `.claude-plugin/plugin.json`, `flotilla/__init__.py`.
  - The README pins `v0.27.0`.
  - The tag goes out together with the push.
- Template bumps:
  - `main.md` and `minor.md`: `template_version` +1 each;
  - `orchestrator.md`: 26 → 27.
- Full suite: `uv run --with pytest python -m pytest -p no:cacheprovider tests/ -q -rf`, in the background, with dots
  counted.

## Review Focus

1. An exclusive run waiting behind a stream of later shared runs must not starve. Rule 3 holds later runs. Test:
   `test_later_shared_runs_wait_while_an_exclusive_run_waits`.
2. **A shared run that arrived before a waiting exclusive run is not held.** Test:
   `test_an_earlier_shared_run_is_not_held_by_a_later_exclusive_one`.
3. **The machine's size is unknown (the branch before packing).** The exclusive rules still apply. Test:
   `test_exclusive_rules_hold_before_the_machine_is_measured`.
4. **The expected start has no machine up, or a never-measured run ahead.** It says so; it does not show a fake number.
   Tests in `test_rig_line.py`.
5. **A run is stopped before its command started.** It brings nothing back and does not hang for 120 s. Test:
   `test_a_run_stopped_before_its_command_fetches_nothing`.

---

### Task 1: Exclusive runs in the journal

**Files:**
- Modify: `flotilla/rig/journal.py`
  - `Run` gains `exclusive: bool = False` and `ceiling: int | None = None`.
  - `_clean` handles both fields.
  - `queue_run(..., exclusive=False, ceiling=None)`.
  - `_verdict` applies the rules.
- Test: `tests/test_rig_runs_journal.py`

**Interfaces:**
- **Produces:**
  - `Rig.queue_run(session_id, *, who, project, revision, program, ladder, pid, mark, exclusive: bool = False, ceiling: int | None = None) -> Run`
  - `Run.exclusive`
  - `Run.ceiling`
  - Waiting-reason texts. They are exact, because the screen and the `rig run` output show them:
    - `"{rid} runs alone on {mid} (exclusive)"`
    - `"exclusive: waits for {mid} to empty ({ids} run on it)"`
    - `"held for exclusive {rid}, which waits for a machine to empty"`

- [ ] **Step 1: Write the failing tests.** Use the helpers `queue`, `shaped_machine` and `ready_machine`, adding an
  `exclusive=` keyword to `queue` and `queue_ladder`.
  - `test_an_exclusive_run_starts_only_on_an_empty_machine`
    - j1 (shared) running on m1.
    - j2 (exclusive) waits.
    - `would_start("j2", m1)` returns `"exclusive: waits for m1 to empty (j1 run on it)"`.
    - After j1 finishes, `start_run("j2")` succeeds.
  - `test_nothing_starts_beside_an_exclusive_run`
    - j1 (exclusive) running.
    - j2 (shared) gets `"j1 runs alone on m1 (exclusive)"`.
  - `test_later_shared_runs_wait_while_an_exclusive_run_waits`
    - j1 (shared) running.
    - j2 (exclusive) waiting.
    - j3 (shared) waiting gets `"held for exclusive j2, which waits for a machine to empty"`, even though m1 has room.
  - `test_an_earlier_shared_run_is_not_held_by_a_later_exclusive_one`
    - Waiting: j1 shared, j2 exclusive.
    - Nothing runs.
    - The verdict for j1 is `""` (it starts).
  - `test_exclusive_runs_go_by_arrival`
    - j1 and j2 exclusive, both waiting, with j2 measured longer.
    - j1 starts first.
  - `test_exclusive_rules_hold_before_the_machine_is_measured`
    - `ready_machine`, so the shape is unknown.
    - j1 shared running, j2 exclusive waiting: j2 waits.
  - `test_exclusive_and_ceiling_fold_strictly`
    - Hand-written records `"exclusive": "yes"` and `"ceiling": -5` fold as `False` and `None`.
    - `true` and `600` are kept.

- [ ] **Step 2: Run them.**
  - Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_rig_runs_journal.py -q -k exclusive`
  - Expected: FAIL (`queue_run() got an unexpected keyword argument 'exclusive'`).

- [ ] **Step 3: Implement.**
  - In `_clean`:
    - `"exclusive"` returns `value is True`;
    - `"ceiling"` returns an int from 1 to 86400, else `None`.
  - `_fold_runs` already copies every dataclass field.
  - In `_verdict`, after the "not waiting in this machine's session" check and before the shape check:
    - Let `there` = runs RUNNING on this machine.
    - Rule 1: if any run in `there` is exclusive, return its reason.
    - Rule 3: let `first_x` = the lowest-numbered live waiting exclusive run of the session. If `first_x` exists and
      `_number(run) > _number(first_x)`, return the "held" text.
    - Rule 2: if `run.exclusive` and `there` is not empty, return the "waits for ... to empty" text.
    - Rule 2b, the arrival order among earlier runs: if `run.exclusive` and an earlier live waiting run exists, return
      `f"{earlier.id} came first"`. With rule 3, this makes exclusive runs strictly ordered by arrival.
  - In the packing branch, pass packing only the live runs numbered below `first_x`, plus `run` itself. Later runs are
    already held, and they must not be chosen.

- [ ] **Step 4: Run** `tests/test_rig_runs_journal.py tests/test_rig_packing.py`.
  - Expected: all pass.

- [ ] **Step 5: Commit.** Message: `feat(rig): exclusive runs - alone on a machine, and later runs wait behind them`.

### Task 2: The profile default, the flags, and a wait as long as the session

**Files:**
- Modify:
  - `flotilla/rig/profile.py`: `RigProfile.exclusive: bool = False`. `[rig] exclusive` must be a bool; anything else
    gives a problem and `False`.
  - `flotilla/cli.py`: in the `rig run` parser, a mutually exclusive group of `--exclusive` and `--shared`, both
    `store_true`; `--wait` default `None`, with help "seconds to wait for room (default: until the session ends)".
  - `flotilla/rig/run.py` `_main`:
    - `exclusive = args.exclusive or (profile.exclusive and not args.shared)`;
    - `ceiling` computed before `queue_run` and passed with `exclusive`;
    - the deadline is `args.wait` if given, else the seconds until `session.until`, at least 1. With no `until`, it is
      900.
- Test: `tests/test_rig_profile.py`, `tests/test_rig_run.py`

**Interfaces:**
- **Consumes:** `Rig.queue_run(..., exclusive, ceiling)` from Task 1.
- **Produces:**
  - `RigProfile.exclusive`;
  - `args.exclusive`, `args.shared`, `args.wait: float | None`.

- [ ] **Step 1: Write the failing tests.**
  - `test_rig_exclusive_reads_a_bool_only`, parametrized:
    - `true` gives True with no problem;
    - `"yes"` gives False and a problem naming `[rig] exclusive`;
    - a missing key gives False.
  - `test_the_profile_makes_runs_exclusive_and_shared_overrides_it`, in `test_rig_run.py` with a profile having
    `exclusive = true`:
    - a plain run records `exclusive=True`;
    - a run with `--shared` records `False`;
    - with the default profile, `--exclusive` records `True`.
  - `test_a_run_records_its_ceiling`:
    - `--max 300` records `ceiling == 300`;
    - with no `--max`, it records the profile's ceiling.
  - `test_the_wait_defaults_to_the_sessions_end`: monkeypatch `Run.take_room` to capture the deadline, with the
    session's `until` 40 min ahead. The deadline lies within 40 min ± 5 s of `time.monotonic()` at the call.

- [ ] **Step 2: Run them.**
  - Expected: FAIL (`unrecognized arguments: --shared`, and `RigProfile` has no `exclusive`).

- [ ] **Step 3: Implement** as described under Files.

- [ ] **Step 4: Run** `tests/test_rig_profile.py tests/test_rig_run.py tests/test_rig_cli.py`.
  - Expected: all pass.

- [ ] **Step 5: Commit.** Message: `feat(rig): a project's runs exclusive by profile; a rig run waits as long as its session`.

### Task 3: The line and its expected start

**Files:**
- Create: `flotilla/rig/line.py`
- Test: `tests/test_rig_line.py`

**Interfaces:**
- **Consumes:**
  - `Rig.runs()`, `Rig.machines()`, `Rig.sessions()`;
  - `packing.history` and `packing.estimate`;
  - `Run.ceiling`, `Run.exclusive`.
- **Produces:**
  - `@dataclass(frozen=True) class Place: run: j.Run; number: int; starts: dt.datetime | None; ends: dt.datetime | None; unmeasured_ahead: int; fits: bool | None`
  - `def places(rig: j.Rig, session_id: str, now: dt.datetime, alive) -> list[Place]`
    - Lists the live waiting runs of the session, in line order, numbered from 1.
    - Line order means:
      - every run that arrived before the first waiting exclusive run, in packing order (`packing.choose` reasons
        aside, sort by run number);
      - then the rest by number.
    - Ordering by run number is the honest common case. The screen says "in line order".
  - `def says(place: Place, session: j.Session) -> str`: the one sentence `rig run` prints and the screen reuses.

**Algorithm (what the spec pins):**
- Each READY or BUSY machine of the session is a lane, free at a time:
  - the latest `remaining` of the runs on it;
  - or `now` when nothing runs on it.
- `remaining` = the estimate's seconds (or the run's `ceiling` when not measured, or `PRIOR` 1800 s when there is
  neither) minus the time since `run.since` of its RUNNING record (use `command_at` when set). At least 60 s.
- Walk the waiting runs in line order:
  - each takes the lane that frees first;
  - `starts` = that lane's free time;
  - the lane becomes free at `starts + duration`;
  - `ends` = that same time.
- **An exclusive run:** take the time when every lane is free, which is the latest free time of all lanes, and occupy
  all of them until it ends.
- **No lane:**
  - `starts` and `ends` are `None`;
  - `fits` is `None`.
- `unmeasured_ahead` counts runs ahead of this one, and running ones, that were counted at their ceiling.
- `fits` = `ends <= session.until`. It is `None` when `until` is unknown.

- [ ] **Step 1: Write the failing tests.**
  - `test_one_machine_serves_the_line_in_order`
    - j1 runs, measured 10 min, started 4 min ago.
    - j2 waits, measured 20 min.
    - j3 waits, measured 5 min.
    - j2 starts at now + 6 min and j3 at now + 26 min.
    - The numbers are 1 and 2.
  - `test_an_unmeasured_run_counts_at_its_ceiling_and_is_counted`
    - j1 runs, never measured, with `ceiling` 900, started 5 min ago.
    - j2 starts at now + 10 min.
    - `unmeasured_ahead == 1`.
  - `test_no_machine_up_gives_no_number`
    - `starts is None`;
    - `says(...)` contains "a machine is raised first".
  - `test_a_run_past_the_session_end_does_not_fit`
    - The session ends in 15 min and j2 ends at +26.
    - `fits is False`.
    - `says` contains `"after session"` and `"--max"`.
  - `test_an_exclusive_run_waits_for_every_lane`
    - Two machines, one free now and one free in 10 min.
    - An exclusive j3 starts at +10.
  - `test_says_names_place_runs_ahead_start_and_session_end`
    - The exact sentence, e.g.:
      `"j12 is 2nd in line, after j11; by estimates it starts around 14:32 or sooner and ends around 14:52; session s80 ends 15:00"`.

- [ ] **Step 2: Run** `tests/test_rig_line.py`.
  - Expected: FAIL (`No module named flotilla.rig.line`).

- [ ] **Step 3: Implement `places` and `says`** as above.
  - Times print as `HH:MM` local, as `surface._hhmm` does. Import it; do not copy it.
  - Ordinals: 1st, 2nd, 3rd, then Nth.

- [ ] **Step 4: Run** `tests/test_rig_line.py`.
  - Expected: all pass.

- [ ] **Step 5: Commit.** Message: `feat(rig): the session's line and when each waiting run starts, by estimates`.

### Task 4: `rig run` says its place, and `flotilla rig` shows the line

**Files:**
- Modify: `flotilla/rig/run.py`
  - `take_room`: after `queue_run`, and whenever the joined text changes, print
    `rig run jN waits: <reason>; <says(place)>`.
  - Print the no-fit warning once, at queue time:
    `rig run jN: by estimates it ends around HH:MM, after session sX ends at HH:MM - lower its ceiling with --max, or run it in the next session`.
- Modify: `flotilla/rig/surface.py` `run_lines`:
  - waiting runs are printed in `line.places` order;
  - each line gains:
    - `exclusive` or `shared`;
    - `#N`;
    - `starts ~HH:MM`, or `starts once a machine is up`;
    - `DOES NOT FIT the session` when `fits is False`;
  - running runs gain `of ~M min` when they have an estimate, and `exclusive` when they are exclusive.
- Test: `tests/test_rig_surface.py`, `tests/test_rig_run.py`

**Interfaces:**
- **Consumes:** `line.places`, `line.says`, `Place` (Task 3).

- [ ] **Step 1: Write the failing tests.**
  - `test_rig_lines_show_the_line_in_order_with_starts`
    - Journal: j1 running, j2 and j3 waiting, one shaped machine.
    - The `lines()` output has j2 before j3, `#1` and `#2`, `starts ~`, and `shared`.
  - `test_a_run_that_does_not_fit_is_marked_on_screen_and_warned_at_queue_time`
    - The session ends in 5 min and a measured 20-min run waits.
    - The screen line has `DOES NOT FIT`.
    - A `rig run` child's log, with `no_room=True` so it waits, holds the warning line once.

- [ ] **Step 2: Run them.** Expected: FAIL.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** `tests/test_rig_surface.py tests/test_rig_run.py`.
  - Expected: all pass.

- [ ] **Step 5: Commit.** Message: `feat(rig): a waiting run says its place and start; flotilla rig shows the line`.

### Task 5: The watch item for an idle machine with waiting runs

**Files:**
- Modify: `flotilla/rig/surface.py` `items()`.
  - For each open session, check each READY machine with `idle_since` at least `IDLE_ALARM` (2 min) ago, against the
    live waiting runs of the session (`commands._alive()`).
  - Text: `rig: machine {mid} idle {N} min while {ids} wait - read flotilla rig for why`
  - Key: `rig:idle:{mid}`.
- Test: `tests/test_rig_surface.py`

- [ ] **Step 1: Write the failing tests.**
  - `test_an_idle_machine_with_waiting_runs_is_a_dropped_ball`
    - READY, idle since 3 min, j1 waiting with an alive pid (monkeypatch `commands._alive` to accept it).
    - The item text and key match.
  - `test_no_item_when_nothing_waits_or_idle_is_short`
    - Idle 1 min: no item.
    - Idle 5 min with no waiting runs: no item.
    - A dead waiter: no item.

- [ ] **Step 2: Run them.** Expected: FAIL.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** `tests/test_rig_surface.py tests/test_fleet*.py -q`.
  - Expected: all pass.

- [ ] **Step 5: Commit.** Message: `feat(rig): the watch says when a machine idles while runs wait for it`.

### Task 6: A stopped run brings back what it made

**Files:**
- Modify: `flotilla/rig/run.py`
  - `Run.gets(gets, root, types, *, cap_seconds=None)`: with a cap, `process.wait` uses it. On timeout the process is
    killed, and the output says `flotilla: bringing back --get was cut at {cap} s after the stop`.
  - In `_main`'s `finally`, before the DROP call:
    - if `task.verdict == "stopped"` and the run's command started (`task.command_started`, a new bool set beside
      `rig.command_started`), and the run is neither lost nor the gets already done, call
      `task.gets(gets, root, profile.get_types, cap_seconds=GET_ON_STOP)`;
    - set `task.got = True` after the normal `gets` in `_phases`.
  - The `profile` and `gets` names must reach the `finally`. Bind them before the `try`.
- Test: `tests/test_rig_run.py`

- [ ] **Step 1: Write the failing tests.**
  - `test_a_stopped_run_brings_back_what_it_made`
    - The command writes `out/a.png`, then sleeps 60.
    - `--get out`.
    - After the file exists on the box, call `rig stop j1`.
    - The tree has `out/a.png`, and the child log says `brought back out/a.png`.
  - `test_a_run_stopped_before_its_command_fetches_nothing`
    - A setup command that sleeps, stopped during setup.
    - No `brought back` line.
    - The child ends within 30 s.

- [ ] **Step 2: Run them.** Expected: the first FAILS (nothing came back); the second passes already.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** `tests/test_rig_run.py`.
  - Expected: all pass.

- [ ] **Step 5: Commit.** Message: `fix(rig): a stopped run brings back its --get paths (twosuns item 3)`.

### Task 7: A machine drained from a closed session stops blocking the ceiling once destroyed

**Files:**
- Modify: `flotilla/rig/journal.py` `add_machine`. The ceiling counts `m.state in LIVE`, except a machine that is
  DRAINING, has `attempts >= 1`, and whose session is not OPEN.
- Test: `tests/test_rig_journal.py`

- [ ] **Step 1: Write the failing tests.**
  - `test_a_machine_draining_from_a_closed_session_after_its_destroy_does_not_block_the_next`
    - Ceiling 1.
    - s1 is closed, and m1 is DRAINING with `attempts=1`.
    - s2 is open: `add_machine(s2, limit=1)` succeeds.
  - `test_a_draining_machine_still_blocks_before_its_destroy_is_sent_or_while_its_session_is_open`
    - `attempts=0` refuses.
    - A draining machine of the same open session refuses.
    - STUCK refuses.

- [ ] **Step 2: Run them.** Expected: the first FAILS.

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run** `tests/test_rig_journal.py tests/test_rig_up.py tests/test_rig_reaper.py`.
  - Expected: all pass.

- [ ] **Step 5: Commit.** Message: `fix(rig): a closed session's machine stops counting against the ceiling once its destroy is sent (twosuns item 4)`.

### Task 8: Templates, documents, version

**Files:**
- Modify:
  - `templates/posts/main.md` and `minor.md`, in the rig paragraph:
    - "Call `rig run` when your run is ready and let it wait in the background: it stands in the session's line and
      starts by itself when its turn comes; never ask the orchestrator for a turn. A run that measures time, frames
      or sound runs with `--exclusive` (or the project's `[rig] exclusive`), so no other run shares the machine with
      it. `flotilla rig` shows the line and when each run starts."
    - Bump `template_version` in both files.
  - `templates/posts/orchestrator.md`, in the rented-machines paragraph:
    - "The rig's order is flotilla's: never send seats a 'go' for the rig. A 'machine idle while runs wait' item means
      flotilla's own handover broke - read `flotilla rig` and tell the person if it does not clear."
    - Bump `template_version` to 27.
  - The skill and the README rig section, wherever they describe `rig run`: `--exclusive`/`--shared`, the wait, and
    the line.
  - `CHANGELOG.md`: a 0.27.0 entry.
  - Version in three places, and the README pin.
  - `TODO.md`: move the stopped-run, draining-machine and overlap items to Done. Narrow the twosuns item 12 entry to
    part B.
- Test: the existing doctor and template tests, and the version check.

- [ ] **Step 1: Make the edits.** Check them with
  `grep -rn "rig run" templates skills README.md | head`.
- [ ] **Step 2: Run** `tests/test_doctor.py tests/test_fleet_plugins.py tests/test_version*.py`.
  - Expected: all pass.
- [ ] **Step 3: Commit.** Message: `docs(rig): posts, skill and README for the rig line; 0.27.0`.

### Final

- Full suite in the background, over HEAD.
- A final review of the whole branch on the most capable model.
- One fix pass.
- Merge, push and tag only after the review and the suite are both green. The tag is under the person's standing ok
  for this release; confirm it as with v0.26.2.
