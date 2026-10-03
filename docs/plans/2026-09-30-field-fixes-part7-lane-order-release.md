# Field Fixes, Part 7 (Track B): Releases, the Lane, Ordering-Only Links, and Part 6's Minors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every flotilla release reachable by `claude plugin update`, make the machine lane see the runs that
starved it and the memory that killed them, let a row wait for another without becoming its part, and close the
minors part 6's reviews deferred.

**Architecture:** Six independent tasks. Nothing here touches liveness, seats, spawn, retire, `fleet down` or the
watch fleet items (`flotilla/fleet/*`, `flotilla/core/census.py`, `flotilla/watch/fleet.py`) — that is Track A,
run in parallel by another session; do not edit those files.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`.

**Spec:** the field test `docs/field-tests/2026-09-29-twosuns-fleet.md` — T2, H19, H22, H41, H44, and part 6's
deferred minors (listed in Task 6); `docs/specs/2026-09-22-flotilla-design.md` sections 6.4 and 9 (the lane).
Read each finding before its task.

## Decisions (the planner's, 2026-09-30; the person approved fixing the findings)

1. **The version moves with every release, and CI says when it did not** (T2). One source of truth:
   `.claude-plugin/plugin.json`; `pyproject.toml` and `flotilla/__init__.py` must carry the same string (a test
   pins it). A new tool `tools/check_version.py` fails when the code (`flotilla/`, `templates/`, `skills/`,
   `hooks/`, `.claude-plugin/`) changed since the tag `v<version>` — "bump the version". CI runs it on pushes to
   `main`; a missing tag is a failure that says "tag v<version> on the release commit". This part's release is
   0.2.0 (parts 1–7 together); the coordinator bumps and tags when it merges.
2. **A long run started outside the lane is warned about, not refused** (H19). The Bash guard gains a `lane` rule:
   a command that runs one of the project's tier commands (`receipts.tiers_for(profile, ...)` commands) or a
   default run pattern, and is not itself `flotilla lane run ...` or `flotilla receipt run ...`, gets a warning:
   "this is a long run; book the machine: `flotilla lane run --for <branch> -- <command>`". A warning, because the
   lane is not a lock and a refusal of an ordinary test run would be worse than the starvation.
3. **The lane sees a headless browser as a run** (H22): `chrome-headless-shell` and `headless_shell` join
   `DEFAULT_PATTERNS`. A full `chrome` does not (the Playwright MCP server keeps one alive for hours).
4. **The lane asks memory** (H41): `read` gains an answer `memory`, busy when `MemAvailable` in `/proc/meminfo` is
   under `lane.memory_floor_mb` (default 1500). Where `/proc/meminfo` does not exist (macOS), the answer is
   `False` with "memory not asked on this platform" — never `None`, which would keep the lane closed for ever.
5. **An ordering-only link** (H44): `claim`, `tree cut` and `tree switch` take `--after <row or branch>...`,
   stored in a new row field `after`. `queue` waits for `after` rows as it waits for `requires` rows
   (`model.blocked_by` reads both); the part gate (`views.pending_dependents`) reads `requires` only, so a
   measurement or tool row claimed `--after` another row never holds that row's walk. The main post says when to
   use which.
6. **Part 6's deferred minors**: a vouch counts only while its row is open or delivered (a released or returned
   row's vouch does not account a commit); `return` clears `vouched`; `return` of a row with a PR says the PR stays
   open and names it; `unbroke`'s refusal says "release or close it"; `waits_on` stops naming a fix that has
   arrived.

Record these as decisions 136–141 in `docs/specs/2026-09-22-decisions-log.md`, style as the entries above them,
ending "(planner's decision, field fixes part 7 track B, 2026-09-30)". **Numbers 136–145 are this track's; Track A
uses 146 and up.**

## Global Constraints

- English only (`python3 tools/check_no_cyrillic.py <files>`); Linux and macOS; Python 3.11+ stdlib only.
- **Do not edit** `flotilla/fleet/*`, `flotilla/core/census.py`, `flotilla/watch/fleet.py`,
  `templates/posts/orchestrator.md`. Track A owns them.
- A new row field needs `docs/events/schema.json` updated by **inserting the one line** in sorted position (do not
  regenerate the whole file: its key order differs from `json.dumps`); `tests/test_ledger_events.py` checks it.
- The suite: `uv run --with pytest python -m pytest -p no:cacheprovider tests/` (no `-q`); before finishing also
  with `--python 3.11` and with `GIT_CONFIG_GLOBAL=/dev/null`; `claude plugin validate .` and
  `claude plugin validate .claude-plugin/plugin.json`.
- A post template whose text changes gets `template_version` + 1 (once per branch is enough).
- Worktree `/home/user/workspace/flotilla-part7b`, branch `fix/part7-lane-order-release` from `main`. Never touch the
  main checkout. Do not bump the version or tag — decision 1's bump is the coordinator's at merge.

## Review Focus

1. **`check_version.py` on a repository with no tags at all** → fails with the "tag v<version>" message, not a
   traceback (Task 1).
2. **A command that merely mentions `npm test` in an echo or a commit message** → no lane warning: the guard reads
   the program, not words in arguments, as `machine.program_of` does (Task 2).
3. **`/proc/meminfo` present but unreadable or missing `MemAvailable`** → memory answer `False` "not asked", the
   lane opens (Task 3).
4. **`--after` naming a row that does not exist** → refused at claim like `--requires` (Task 4).
5. **A vouch on a row that later ships** → still counts (a delivered row's vouch stands) (Task 6).

---

### Task 1: the version is one string, and CI says when a release did not move it (T2)

**Files:** Create `tools/check_version.py`, `tests/test_version.py`; modify `.github/workflows/ci.yml`.

- [ ] **Step 1: failing tests** (`tests/test_version.py`):
  - the three carriers agree: parse `.claude-plugin/plugin.json` `version`, `pyproject.toml` `version = "..."`,
    `flotilla/__init__.py` `__version__`; assert all equal.
  - `check_version.main(root)` over a temp git repo: (a) no tag `v<version>` → exit 1, message contains
    `tag v`; (b) tag on HEAD → 0; (c) tag, then a commit touching `flotilla/x.py` → 1, message contains
    `bump the version`; (d) tag, then a commit touching only `docs/x.md` → 0.
- [ ] **Step 2:** run — FAIL (no module).
- [ ] **Step 3:** implement `tools/check_version.py` (stdlib; `git -C root diff --quiet v<ver> -- <paths>`; exit
  codes as above; `main(root=Path.cwd())`, `if __name__ == "__main__": sys.exit(main())`). Add a CI step to the
  existing job that runs the language check (or a new small job) on `push` to `main` only:
  `python3 tools/check_version.py` with `fetch-depth: 0` so tags exist.
- [ ] **Step 4:** suite. **Step 5:** commit `build: one version string, and CI says when a release did not move it`.

### Task 2: a long run outside the lane is warned about (H19)

**Files:** `flotilla/guards/rules.py` (a new rule name `lane`, on by default), `flotilla/guards/run.py` (the
check), `flotilla/hooks.py` only if its cheap text pre-check needs the new program words; tests
`tests/test_guards_run.py` (match its style).

- [ ] **Step 1: failing tests:** `npm test` / `uv run pytest` / `npx vitest run` → one warning Finding naming
  `flotilla lane run --for`; `flotilla lane run --for x -- npm test` → none; `flotilla receipt run --purpose push`
  → none; `echo "run npm test later"` and `git commit -m "npm test passes"` → none; the rule off in the profile →
  none.
- [ ] **Step 2:** FAIL. **Step 3:** implement: split the command into segments as the other guards do; a segment
  whose program (`machine.program_of`) matches a tier command's program-and-first-arg or a lane pattern, in a
  command that does not start with `flotilla lane run` / `flotilla receipt run`, yields `Finding("lane", False,
  "...")` (a warning — second field False, as the reversible guards use; check `flotilla/guards/__init__.py`).
- [ ] **Step 4:** suite. **Step 5:** commit `feat(guard): a long run outside the lane is warned about`.

### Task 3: the lane sees headless browsers and asks memory (H22, H41)

**Files:** `flotilla/lane/machine.py`; tests `tests/test_lane_machine.py`.

- [ ] **Step 1: failing tests:** a process table with `/…/chrome-headless-shell --headless …` computing → a busy
  foreign run; `chrome --remote-debugging-pipe` → not a run. Memory: a fake meminfo reader returning
  `MemAvailable: 900000 kB` with floor 1500 → `Answer("memory", True, ...)` naming the numbers; 3000000 kB → False;
  reader returning None (no file) → False "not asked on this platform"; profile `lane.memory_floor_mb = 0` →
  never busy.
- [ ] **Step 2:** FAIL. **Step 3:** implement: extend `DEFAULT_PATTERNS`; `read(..., meminfo=_meminfo)` with a
  module-level `_meminfo()` that returns kB or None; append the memory answer after the foreign-run answer.
  Check how `Reading`/answers decide "busy" and that a True memory answer holds the lane like a foreign run.
- [ ] **Step 4:** suite. **Step 5:** commit `feat(lane): a headless browser is a run, and low memory holds the lane`.

### Task 4: `--after` — an ordering-only link (H44)

**Files:** `flotilla/ledger/model.py` (field `after: list`, `ROW_FIELDS`, `blocked_by` reads it),
`flotilla/ledger/core.py` (`check_claim`, `claim`, `append_claim`), `flotilla/ledger/tree.py` (`cut`, `switch`),
`flotilla/cli.py` (`--after` on claim, cut, switch), `docs/events/schema.json` (one line),
`templates/posts/main.md`; tests `tests/test_ledger_core.py`, `tests/test_ledger_judging.py`,
`tests/test_ledger_delivery.py`.

- [ ] **Step 1: failing tests:** a row claimed `--after r1` cannot queue until r1 is delivered (same refusal text
  shape as `requires`); a shipped row that another open row names only `--after` is walkable (no part gate:
  `views.pending_dependents` ignores it, `who_moves` names the judge); an unknown `--after` id is refused at claim.
- [ ] **Step 2:** FAIL. **Step 3:** implement; `check_claim` resolves `after` like `requires` (branch or row id →
  row id) — reuse its code. Main post bullet: "`--requires` says your row builds on that one (it is walked with
  it); `--after` says only that yours goes second — a measurement, a tool, a follow-up."
- [ ] **Step 4:** suite (schema test included). **Step 5:** commit `feat(ledger): --after orders rows without making one a part of the other`.

### Task 5: decisions 136–141

- [ ] Log entries in `docs/specs/2026-09-22-decisions-log.md`; commit `docs: decisions 136-141 from field fixes part 7 track B`.

### Task 6: part 6's minors

**Files:** `flotilla/ledger/batch.py` (`Accounting` vouched), `flotilla/ledger/delivery.py` (`send_back`),
`flotilla/ledger/judging.py` (`unbroke` message), `flotilla/ledger/views.py` (`waits_on`); tests alongside.

- [ ] Tests first, each seen failing: a vouch on a row later `released` does not account the commit; a vouch on a
  row later shipped still does; `send_back` clears `vouched`; `send_back` of a PR-mode queued row returns evidence
  or a letter line naming "PR #<n> stays open"; `unbroke` with a shipped fix row says "release or close it";
  `waits_on` returns "" once the fix row is delivered (the judge moves).
- [ ] Implement each; suite; commit `fix(ledger): part 6's review minors — vouches die with their rows, clearer refusals`.

## Finish

Suite three ways, both validations, the Cyrillic check over changed files. Report to the coordinator: branch, tip,
commits, the three `N passed` lines, every `Ruling:`. Do not merge, push, tag or delete.
