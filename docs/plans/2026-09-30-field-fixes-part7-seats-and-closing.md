# Field Fixes, Part 7 (Track A): Liveness, Seats, Sizing, and Closing the Task — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** flotilla stops counting a retired session as alive, frees the branches a dead seat's tree holds, tells
the orchestrator about a seat with no work and about a queue that has drained, refuses to raise a seat into a
machine without memory, and closes a task by asking the person to check it.

**Architecture:** Five tasks in `flotilla/core/census.py`, `flotilla/ledger/core.py` (`release` only),
`flotilla/watch/fleet.py`, `flotilla/fleet/spawn.py`, `flotilla/fleet/retire.py` + `commands.py` (`fleet down`'s
report), and `templates/posts/orchestrator.md`. Track B (releases, lane, `--after`, part 6's minors) runs in
parallel in another worktree; it also edits `ledger/core.py` (`claim`), so the two meet there in different
functions.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`.

**Spec:** `docs/field-tests/2026-09-29-twosuns-fleet.md` — H9, H12, H36, H45, H46, H47, H48 (and G12);
`docs/specs/2026-09-22-flotilla-design.md` sections 7.4–7.5 (seats, spawn, retire).

## Decisions (the planner's, 2026-09-30)

1. **A background session whose process is gone is not alive** (H48): `read_census` drops a background entry whose
   `pid` is missing or names no running process (`os.kill(pid, 0)`), so every caller — `live_names`, `flotilla
   fleet`, `watch`, spawn's name check — sees the seat as empty. Interactive sessions are kept as listed.
2. **Releasing a seat frees its tree** (H36): `release` of a `reserved` row whose `tree` is a clean worktree runs
   `git -C <tree> switch --detach`, so the branches it had checked out can be switched to elsewhere; a dirty tree
   is left as it is and the release's evidence and printed line name it ("N uncommitted in <tree>: its branch stays
   checked out there").
3. **`watch` tells the orchestrator about a seat with no work** (H45): a live session of a post that may `claim`,
   holding no open row but its seat and reading nothing, whose last move is older than
   `watch.idle_seat_minutes` (default 60), gets an item: "<name> has held no work for <age>: give it a row, or
   retire it".
4. **`watch` tells the orchestrator when the queue has drained** (H12, H46): no open row other than seats — or
   only rows whose move is the person's — gives one item: "the queue is empty: ask the person to check the result,
   then offer `flotilla fleet down` (N sessions stay alive until then)". The orchestrator post says the closing step
   in words.
5. **`spawn` refuses to raise a seat under a memory floor** (H47, H9): when `/proc/meminfo`'s `MemAvailable` is under
   `fleet.memory_floor_mb` (default 2000), spawn refuses, naming the numbers and suggesting retiring idle seats;
   `--anyway` overrides. Where `/proc/meminfo` is absent the check is skipped and the plan says so.
6. **`fleet down` names what stays alive** (H12): after standing the fleet down it lists the census sessions whose
   working directory is inside the project or its worktrees and that are not this fleet's seats — seats of past
   fleets and hand-started sessions — with `claude stop <id>` for each.

Decisions 146–151 in the decisions log (Track B holds 136–145).

## Global Constraints

As Track B's plan: English only; stdlib; three suite runs; both validations; `template_version` + 1 on changed
posts; worktree `/home/max/workspace/flotilla-part7a`, branch `fix/part7-seats-closing`.

## Tasks

1. Census drops gone background sessions (H48) — `tests/test_census.py`.
2. Seat release detaches a clean tree (H36) — `tests/test_ledger_core.py` (or where `release` is tested).
3. `watch`: idle seat (H45) and drained queue (H12/H46) items — `tests/test_watch_*`; orchestrator post text.
4. `spawn` memory floor with `--anyway` (H47) — `tests/test_fleet_spawn.py`, `tests/test_fleet_cli.py`.
5. `fleet down` names the sessions that stay alive (H12) — `tests/test_fleet_retire.py`, `tests/test_fleet_cli.py`.
6. Decisions 146–151; whole-branch review; merge with Track B; version 0.2.0 and tag (the person's ok for the tag).

Each task: failing test first, seen red; implement; suite; one commit.
