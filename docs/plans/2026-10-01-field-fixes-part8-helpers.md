# Field Fixes, Part 8 (Track A): Helper Sessions — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fleet session may raise a short-lived helper session for a piece of its own work: the helper works in
its own tree on a branch cut from the parent's work, follows the fleet's rules, is kept in the ledger, and finishes
by recording what it did and leaving — never vanishing without a trace (twosuns H8; the person's decision: a helper
works in its own tree, so edits never race).

**Architecture:** A helper is a seat of a new post, `helper` (`helper {n}`), raised by `flotilla helper raise --for
<branch> --task "<what>"` from a session that owns that open row. Its seat row carries a new field `helper_of`
(the parent row's id); its tree is cut from the parent branch's tip, on the seat branch `fleet/helper-<n>`. It
commits there and ends with `flotilla helper done --summary "<what it did>"`, a release of its seat row whose
evidence names the parent row and the helper's tip. The parent merges `fleet/helper-<n>` into its own branch and
hands over as usual: the reviewer reads the parent's whole range, the helper's commits included. `watch` tells the
orchestrator about a finished helper still running (retire it) and about a helper whose parent is gone. In-session
subagents stay the tool for reading and research (H40); a helper session is for work that edits.

**Spec:** `docs/field-tests/2026-09-29-twosuns-fleet.md` — H8, H40; the person's design choices of 2026-09-30.

## Decisions (the planner's, 2026-10-01; the person approved the design)

1. Who may raise: a session that owns the open row `--for` names, in state `claimed` or `fixing`.
2. At most `fleet.helpers_per_seat` (default 2) live helpers per parent row; the memory floor of `spawn` applies.
3. The helper's tree is cut from the parent branch's current tip; its branch is its seat branch `fleet/helper-<n>`.
4. `helper done` records `{"helped": <parent row>, "tip": <sha>, "summary": ...}` and releases the seat row; the
   parent gets a letter ("merge fleet/helper-<n>, then retire helper <n>").
5. The helper post may `reserve`, `release`, `wait`, never a delivery move: it never hands, queues or lands.
6. `watch` (orchestrator items): a live helper whose seat row is released → "helper <n> finished helping `<branch>`:
   retire it"; a live helper whose parent row's owner is not alive → "helper <n>'s parent is gone".

Decisions 159–164 in the decisions log (Track B holds 153–158).

## Tasks (native; each test-first, suite after each, one commit each)

1. The `helper` post template and the `helper_of` row field (model, schema line, posts load it).
2. `flotilla/fleet/helpers.py` — `raise_helper` (checks 1–3, reuses `spawn.raise_seat` with a base, an extra field
   and a first prompt that carries the task) and `done`.
3. CLI `flotilla helper raise|done`, the letter to the parent, the skill and the main/minor posts say when to use it.
4. `watch` items for finished and orphaned helpers.
5. Decisions 159–164; whole-branch review; merge with Track B; release 0.3.0 with its tag.
