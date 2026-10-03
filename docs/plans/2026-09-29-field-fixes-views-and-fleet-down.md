# Field Fixes, Part 3: What the Views Say, and Standing the Fleet Down — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the views from crying wolf — about other projects' sessions, a seat still being raised, a census
word that flickered, a reader who already finished, colour codes and a "base unknown" that is not a git failure —
and give a finished fleet a one-step way to stand down.

**Architecture:** One new notion carries most of it: **the sessions of this project** — live sessions named in this
ledger's open rows, or whose working directory is inside this repository's main checkout or one of its worktrees
(`flotilla/ledger/project.py`). The hooks, `watch`, the ask guard and the letters use it instead of every name on
the machine. A post row whose session is gone becomes an empty seat, reported as one quiet line, not a mover alarm;
the orchestrator is raised last. `watch` confirms a dropped ball with a second census sample before it reports
one. A shared `strip_ansi` cleans run summaries. `flotilla fleet down` retires every seat of this ledger but the
caller's own.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`.

**Spec:** the field test `docs/field-tests/2026-09-27-snake-fleet.md` — findings F8, F9, F10, F13, F19, F20, F22,
F27 and the triage with the person of 2026-09-28, groups 5 and 6 ("live sessions of this project are the holders of this
ledger's post rows, not every name matching a pattern"; "`flotilla fleet down`; a gone seat is one quiet line, not
a mover alarm"); `docs/specs/2026-09-22-flotilla-design.md` sections 6.7, 6.9, 7.5 and 8.

## Decisions (the person's, 2026-09-28, and the executor's)

1. **The sessions of this project** (the person: "live sessions of this project are the holders of this ledger's post
   rows"). The executor widens it by two, so it holds before any spawn and for a person's own session: a live
   session belongs to this project when its name is an owner or reader of an open row of this ledger (post rows
   included), **or** its census `cwd` is inside the main checkout or a worktree of this repository. A post-named
   mover ("the sender", "the judge") resolves only among them; the greeting names them and counts the rest.
2. **A gone seat is one quiet line, not a mover alarm** (the person). A `reserved` row whose session is not alive is the
   deviation `seat_empty`, not `mover_gone`. The fleet view gathers every empty seat into one item that names them
   and the way out (`flotilla fleet down`).
3. **The orchestrator is raised last** (executor, F8). Its session-start report then sees every seat already
   raised, instead of reporting the ones about to be; and its first census lists the whole fleet.
4. **A dropped ball is confirmed by a second census sample** before `watch` reports it (executor, F10): a session
   caught once at `waiting` or `blocked` is sampled again a few seconds later, and only a ball dropped in both
   samples is reported. The hooks do not sample twice (their budget is seconds); the prompt hook's throttle already
   repeats nothing that did not change.
5. **`flotilla fleet down` retires every seat of this ledger** (the person), with the same steps as `flotilla retire`: stop,
   unlock, release, keep the tree, name the orphaned work. The caller's own seat is skipped and named, because
   stopping the session that runs the command stops the command. A census that cannot be asked refuses the whole
   command before anything is stopped. `/flotilla:down` is the person's command; it shows the fleet and asks first.
6. **Run summaries carry no terminal escapes** (executor, F19): stripped when recorded, and again when shown, for
   rows recorded before.

## Global Constraints

- flotilla is English only: code, comments, output, templates, docs (`tools/check_no_cyrillic.py`).
- Linux and macOS; Python 3.11+ standard library only.
- A hook never crashes or traps a session: what it cannot check it says.
- flotilla never parses transcripts and never drives a session beyond `claude stop` in retire.
- Every `flotilla <cmd>` a template or skill names must exist (`test_every_cli_call_in_the_post_templates_exists`,
  `test_every_cli_call_a_skill_names_exists`).
- Tests run three ways before a task is done: default, `--python 3.11`, and `GIT_CONFIG_GLOBAL=/dev/null`; plus
  `python3 tools/check_no_cyrillic.py` and `claude plugin validate .`.
- The suite command: `uv run --with pytest python -m pytest -p no:cacheprovider tests/` — without `-q`. Read the
  `N passed` line.

## Review Focus

1. **A person's own session, started in another directory, that owns a row here** → still of this project (Task 3,
   `test_an_owner_elsewhere_on_disk_is_of_this_project`).
2. **Sessions started by hand in the main checkout, before any spawn or row** → of this project by `cwd` (Task 3,
   `test_a_session_in_the_main_checkout_is_of_this_project`).
3. **`fleet down` when the census cannot be asked** → refused before anything is stopped or released (Task 6,
   `test_fleet_down_refuses_whole_when_the_census_is_down`).
4. **`fleet down` run from a session that holds a seat** → every other seat retired, its own named and kept (Task 6,
   `test_fleet_down_keeps_the_callers_own_seat`).
5. **A summary with no escapes, or with an OSC hyperlink** → unchanged, and the link's text kept (Task 1,
   `test_strip_ansi_leaves_plain_text_and_keeps_link_text`).

---

### Task 1: Run summaries without terminal escapes (F19)

**Files:**
- Create: `flotilla/core/text.py`
- Modify: `flotilla/lane/run.py` (`summarize`), `flotilla/onboard/firstrun.py` (`_summary`, `_tail`),
  `flotilla/ledger/commands.py` (the `last run` print in `_status`, around line 327)
- Test: create `tests/test_text.py`; modify `tests/test_lane_run.py`

**Interfaces:**
- Produces: `text.strip_ansi(value: str) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_text.py`:

```python
from flotilla.core.text import strip_ansi

PYTEST = "\x1b[32m\x1b[32m\x1b[1m12 passed\x1b[0m\x1b[32m in 0.02s\x1b[0m\x1b[0m"


def test_strip_ansi_removes_colour_codes():
    assert strip_ansi(PYTEST) == "12 passed in 0.02s"


def test_strip_ansi_leaves_plain_text_and_keeps_link_text():
    assert strip_ansi("12 passed in 0.02s") == "12 passed in 0.02s"
    assert strip_ansi("see \x1b]8;;https://x.invalid\x07the docs\x1b]8;;\x07 now") == "see the docs now"
```

Append to `tests/test_lane_run.py`:

```python
def test_a_coloured_summary_is_recorded_plain():
    from flotilla.lane.run import summarize
    assert summarize(["noise", "\x1b[32m12 passed\x1b[0m in 0.02s"]) == "12 passed in 0.02s"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_text.py tests/test_lane_run.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'flotilla.core.text'`, and the coloured summary keeps its
escapes (the `SUMMARY` search still matches "12 passed" between them).

- [ ] **Step 3: Implement**

`flotilla/core/text.py`:

```python
"""Text that came from a terminal program, made fit to store and to print (field test F19)."""

from __future__ import annotations

import re

#: CSI sequences (colours, cursor moves) and OSC sequences (hyperlinks, titles), BEL- or ST-terminated.
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(value: str) -> str:
    return ANSI.sub("", value)
```

`flotilla/lane/run.py`, `summarize`: strip each line first —
`lines = [strip_ansi(line).strip() for line in lines if strip_ansi(line).strip()]` (import
`from flotilla.core.text import strip_ansi`).

`flotilla/onboard/firstrun.py`: `_summary` reads `strip_ansi(text)` in place of `text`; `_tail` returns
`strip_ansi("\n".join(...))`.

`flotilla/ledger/commands.py`, in `_status`: `ran = f" (last run: {strip_ansi(row.last_run)})" if row.last_run
else ""` — rows recorded before this fix still carry escapes.

- [ ] **Step 4: Run the tests and the full suite** → `N passed`.

- [ ] **Step 5: Commit**

```bash
git add flotilla/core/text.py flotilla/lane/run.py flotilla/onboard/firstrun.py flotilla/ledger/commands.py tests/test_text.py tests/test_lane_run.py
git commit -m "fix(lane): run summaries are stored and shown without terminal escapes

A tier's summary kept pytest's colour codes and status printed them raw (F19)."
```

---

### Task 2: `show` says what it knows (F13, F22)

**Files:**
- Modify: `flotilla/ledger/commands.py` (`summary`, `_show`)
- Test: `tests/test_ledger_cli.py`

**Interfaces:**
- Consumes: `gitq.branch_tip(root, branch, run=...)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_ledger_cli.py`)

```python
def test_an_accepted_row_does_not_say_its_reader_is_reading(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    tip = commit(tree, "work", "work.txt")
    run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    code, out = run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")
    assert "(reading)" in out
    run_cli("work", "accept", "feat/x", "--reviewed", tip, "--root", str(root), "--as", "review session 1")
    code, out = run_cli("work", "show", "feat/x", "--root", str(root))
    assert "(reading)" not in out and "reader review session 1" in out


def test_show_says_a_row_has_no_branch_yet(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    run_cli("work", "claim", "feat/later", "--root", str(root), "--as", "main session 1")
    code, out = run_cli("work", "show", "feat/later", "--root", str(root))
    assert "base: no branch yet" in out and "base unknown" not in out
```

Before running, check how `work claim` on a branch that does not exist records `base` (it is `""`, `core.claim`);
if claim refuses a missing branch in this profile, build the row with `core.claim` directly as
`tests/test_ledger_delivery.py::test_a_row_claimed_before_its_branch_existed_lands_from_origin` does.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_cli.py -k "reading or no_branch"`
Expected: FAIL — `(reading)` after accept; `base unknown`.

- [ ] **Step 3: Implement**

`summary(row)`: `(reading)` only while the row is handed and taken:

```python
    reading = " (reading)" if row.taken and row.state == "handed" else ""
    reader = f", reader {row.reader}{reading}" if row.reader else ""
```

`_show`: the first detail line reads the base through a helper:

```python
def _base(ledger: core.Ledger, row: Row) -> str:
    if row.base:
        return f"base {row.base}"
    if gitq.branch_tip(ledger.root, row.branch, run=ledger.run) is None:
        return "base: no branch yet"
    return "base unknown"
```

and the line becomes `print(f"  {_base(ledger, row)} | tip {row.tip or '-'} | verdict ...")` (keep the rest of
the line as it is).

- [ ] **Step 4: Run the tests and the full suite** → `N passed`. A test that asserted `(reading)` on a non-handed
  row is updated to the new rule.

- [ ] **Step 5: Commit**

```bash
git add flotilla/ledger/commands.py tests/test_ledger_cli.py
git commit -m "fix(ledger): show stops calling an accepted reader reading, and says when a row has no branch yet

\`taken\` outlived the verdict (F13), and a fix row filed before its branch read as a git failure (F22)."
```

---

### Task 3: The sessions of this project (F9, F20)

**Files:**
- Create: `flotilla/ledger/project.py`
- Modify: `flotilla/ledger/core.py` (`Ledger.live_sessions`), `flotilla/watch/context.py` (`Context.project`,
  `gather`), `flotilla/hooks.py` (`_identity`), `flotilla/watch/ask.py` (orchestrators),
  `flotilla/ledger/commands.py` (`_letters`)
- Test: create `tests/test_ledger_project.py`; modify `tests/test_hooks.py`, `tests/test_ledger_letters.py`

**Interfaces:**
- Produces:
  - `project.roots(root: Path, run=subprocess.run) -> list[Path]` — the main checkout and every worktree
    (`git worktree list --porcelain`), resolved; `[root]` when git cannot answer;
  - `project.members(sessions: list, rows: dict, roots: list[Path]) -> list` — the live sessions of this project;
  - `Ledger.live_sessions() -> list` — the census sessions, refusing like `live_names` (`live_names` is rewritten on
    top of it);
  - `Context.project: list | None` — `None` means "not narrowed" (a context built by hand in tests), and every
    property then falls back to all sessions; `Context.project_live -> set[str] | None`.

- [ ] **Step 1: Write the failing tests** (`tests/test_ledger_project.py`)

```python
from pathlib import Path

from flotilla.ledger import project
from watchkit import row, rows, sess


def at(name, cwd, **kw):
    import dataclasses
    return dataclasses.replace(sess(name, **kw), cwd=cwd)


ROOTS = [Path("/work/snake"), Path("/work/snake-main-1")]


def test_a_session_in_a_worktree_of_this_repo_is_of_this_project():
    assert [s.name for s in project.members([at("main session 1", "/work/snake-main-1/src")], {}, ROOTS)] == \
        ["main session 1"]


def test_a_session_in_the_main_checkout_is_of_this_project():
    assert project.members([at("orchestrator 1", "/work/snake")], {}, ROOTS)[0].name == "orchestrator 1"


def test_a_session_of_another_repo_with_a_matching_name_is_not():
    other = at("acceptance judge 1", "/work/other-project")
    assert project.members([other], {}, ROOTS) == []


def test_an_owner_elsewhere_on_disk_is_of_this_project():
    mine = at("main session 7", "/home/user")
    assert project.members([mine], rows(row(owner="main session 7")), ROOTS) == [mine]


def test_a_prefix_of_the_root_is_not_inside_it():
    assert project.members([at("x", "/work/snake-origin")], {}, [Path("/work/snake")]) == []


def test_roots_lists_the_main_checkout_and_its_worktrees(tmp_path):
    from ledgerkit import git, repo_with_origin
    root = repo_with_origin(tmp_path)
    git(root, "worktree", "add", "-q", "-b", "side", str(tmp_path / "side"))
    assert set(project.roots(root)) == {root.resolve(), (tmp_path / "side").resolve()}
```

Append to `tests/test_hooks.py`:

```python
def test_the_greeting_names_this_projects_peers_and_counts_the_rest(tmp_path, healthy):
    import dataclasses
    me = sess("orchestrator 1")
    here = sess("main session 1")
    there = sess("acceptance judge 1")
    ctx = context(tmp_path, me=me, sessions=[me, here, there])
    ctx = dataclasses.replace(ctx, project=[me, here])
    said = call("session-start", tmp_path, ctx)
    assert "1 live peer(s) in this project: main session 1" in said and "1 more elsewhere on this machine" in said


def test_the_ask_guard_names_only_this_projects_orchestrator(tmp_path):
    import dataclasses
    me = sess("minor session 1")
    ctx = context(tmp_path, me=me, sessions=[me, sess("orchestrator 1"), sess("orchestrator 2")])
    ctx = dataclasses.replace(ctx, project=[me, sess("orchestrator 2")])
    reason = asked(tmp_path, ctx)["permissionDecisionReason"]
    assert "orchestrator 2" in reason and "orchestrator 1" not in reason
```

`Context` is a dataclass; if `dataclasses.replace` refuses it (it is not frozen, so it will not), set the field
directly.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_project.py tests/test_hooks.py`
Expected: FAIL — no module `flotilla.ledger.project`; `Context` has no field `project`.

- [ ] **Step 3: Implement `flotilla/ledger/project.py`**

```python
"""Which live sessions belong to this project (field test F9, F20).

The census is machine-wide: a judge of another repository matches this project's judge post by name. A session is
of this project when this ledger names it — an owner or reader of an open row, post rows included — or when its
working directory is inside this repository's main checkout or one of its worktrees. The second holds before any
row exists; the first holds for a person's own session started anywhere.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def roots(root: Path, run=subprocess.run) -> list[Path]:
    done = run(["git", "-C", str(root), "worktree", "list", "--porcelain"], capture_output=True, text=True,
               check=False)
    found = [Path(line[len("worktree "):]).resolve() for line in (done.stdout or "").splitlines()
             if done.returncode == 0 and line.startswith("worktree ")]
    return found or [Path(root).resolve()]


def _inside(cwd: str, roots: list[Path]) -> bool:
    if not cwd:
        return False
    here = Path(cwd).resolve()
    return any(here == top or top in here.parents for top in roots)


def members(sessions: list, rows: dict, roots: list[Path]) -> list:
    named = {name for row in rows.values() if row.is_open for name in (row.owner, row.reader) if name}
    return [session for session in sessions if session.name in named or _inside(session.cwd, roots)]
```

- [ ] **Step 4: Wire it**

`flotilla/ledger/core.py`, next to `live_names`:

```python
    def live_sessions(self) -> list:
        if self.census is None and os.environ.get(NO_CENSUS):
            raise MoveRefused(f"{NO_CENSUS} is set, so the census is not asked and liveness is unknown")
        try:
            return (self.census or read_census)()
        except CensusUnavailable as err:
            raise MoveRefused(f"could not ask which sessions are alive: {err}") from err

    def live_names(self) -> set[str]:
        return {session.name for session in self.live_sessions() if session.name}
```

`flotilla/watch/context.py`:

- `Context` gains `project: list | None = None` and:

```python
    @property
    def project_live(self) -> set[str] | None:
        chosen = self.project if self.project is not None else self.sessions
        return None if chosen is None else {session.name for session in chosen if session.name}
```

- `fleet()` passes `self.project if self.project is not None else self.sessions` to `fleet.fleet` in place of
  `self.sessions`;
- `gather`, after the ledger is read and when both `ctx.sessions` and `ctx.ledger` are there:

```python
    if ctx.sessions is not None and ctx.ledger is not None:
        from flotilla.ledger import project
        ctx.project = project.members(ctx.sessions, ctx.rows, project.roots(ctx.ledger.root))
```

`flotilla/hooks.py`, `_identity`, the peers sentence (the census is still asked once):

```python
        mine = ctx.project_live or set()
        peers = sorted(mine - {ctx.me.name})
        elsewhere = len((ctx.live or set()) - mine - {ctx.me.name})
        shown = ", ".join(peers[:PEERS_SHOWN]) + (f", and {len(peers) - PEERS_SHOWN} more"
                                                  if len(peers) > PEERS_SHOWN else "")
        said.append(f"you are {ctx.me.name} ({role}); {len(peers)} live peer(s) in this project"
                    + (f": {shown}" if peers else "")
                    + (f"; {elsewhere} more elsewhere on this machine" if elsewhere else ""))
```

  Update `test_session_start_names_the_session_its_post_and_peers` to the new wording
  (`"1 live peer(s) in this project: main session 1"`).

`flotilla/watch/ask.py`: `orchestrators = sorted(name for name in ctx.project_live or set() if ...)`.

`flotilla/ledger/commands.py`, `_letters`: the `live` callable returns this project's names:

```python
    def live():
        try:
            sessions = ledger.live_sessions()
        except MoveRefused:
            return None
        from flotilla.ledger import project
        return {s.name for s in project.members(sessions, ledger.rows(), project.roots(ledger.root, ledger.run))
                if s.name}
```

- [ ] **Step 5: Run the tests and the full suite** → `N passed`. Every existing test that builds a `Context` by hand
  keeps `project=None` and so its old behaviour.

- [ ] **Step 6: Commit**

```bash
git add flotilla/ledger/project.py flotilla/ledger/core.py flotilla/watch/context.py flotilla/hooks.py flotilla/watch/ask.py flotilla/ledger/commands.py tests/test_ledger_project.py tests/test_hooks.py
git commit -m "fix(watch): the fleet is this project's sessions, not every name on the machine

The greeting listed other-project sessions to the snake orchestrator (F9), and a post-named move was pinned on another
project's judges (F20). A session is of this project when this ledger names it or it works in this repository's
checkout or worktrees; the hooks, watch, the ask guard and the letters use that."
```

---

### Task 4: A dropped ball is confirmed before it is reported (F10)

**Files:**
- Modify: `flotilla/watch/commands.py`, `flotilla/cli.py` (hidden `--confirm`)
- Test: `tests/test_watch_cli.py`

**Interfaces:**
- Consumes: `fleet.DROPPED`, `_key(item)` (part 2).
- Produces: `args.confirm: float` (default 5.0, hidden); `_confirmed(items, root, gather, confirm, sleep) ->
  list | None` — `None` when the second sample could not be taken.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_watch_cli.py`)

```python
def test_once_does_not_report_a_ball_the_second_sample_clears(tmp_path, capsys):
    onboarded(tmp_path)
    given = iter([dropping(tmp_path, "blocked"), dropping(tmp_path, "working")])
    ns = argparse.Namespace(once=True, wait=0.0, interval=20.0, confirm=5.0, root=str(tmp_path))
    code = run_watch_command(ns, gather=lambda root, sid: next(given), now=NOW, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert code == 0 and "attention: none" in out


def test_once_reports_a_ball_both_samples_agree_on(tmp_path, capsys):
    onboarded(tmp_path)
    given = iter([dropping(tmp_path, "blocked"), dropping(tmp_path, "blocked")])
    ns = argparse.Namespace(once=True, wait=0.0, interval=20.0, confirm=5.0, root=str(tmp_path))
    code = run_watch_command(ns, gather=lambda root, sid: next(given), now=NOW, sleep=lambda s: None)
    assert code == 1 and "review session 1 holds the move" in capsys.readouterr().out


def test_wait_does_not_wake_for_a_ball_the_second_sample_clears(tmp_path, capsys):
    calm = context(tmp_path, me=None, sessions=QUIET)
    code, out = run_wait(tmp_path, capsys, [calm, dropping(tmp_path, "blocked"), dropping(tmp_path, "working"),
                                            calm, calm])
    assert code == 0 and "nothing new" in out
```

`dropping` and `run_wait` are the helpers part 2 added to this file. Give the file's `args()` and `waiting()`
helpers `confirm=5.0` too.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_watch_cli.py`
Expected: FAIL — the first reports the ball; the third wakes.

- [ ] **Step 3: Implement**

`flotilla/cli.py`, the `watch` parser: `watch.add_argument("--confirm", type=float, default=5.0,
help=argparse.SUPPRESS)`.

`flotilla/watch/commands.py`:

```python
def _confirmed(items, root, gather, confirm, sleep):
    """Keep a dropped ball only if a second census sample, `confirm` seconds on, still shows it: a session caught
    once at `waiting` is often busy a moment later (field test F10). None when the second sample failed."""
    from flotilla.watch.fleet import DROPPED
    if not any(item.kind == DROPPED for item in items):
        return items
    sleep(confirm)
    again = gather(root, "")
    if _problems(again):
        return None
    still = {_key(item) for item in again.fleet()}
    return [item for item in items if item.kind != DROPPED or _key(item) in still]
```

In `run_watch_command`'s `--once` path, after `items = ctx.fleet()`:

```python
    items = _confirmed(items, root, gather, getattr(args, "confirm", 5.0), sleep)
    if items is None:
        print("census: could not be asked for the second sample")
        return 2
```

In `_wait`, pass `confirm` in and filter `new` the same way before the `if new:` (a `None` returns 2 with the same
line). Keep `seen` computed from the unconfirmed `items`, so a ball the second sample cleared is still "seen" and
counts as new when it comes back.

- [ ] **Step 4: Run the tests and the full suite** → `N passed`.

- [ ] **Step 5: Commit**

```bash
git add flotilla/watch/commands.py flotilla/cli.py tests/test_watch_cli.py
git commit -m "fix(watch): a dropped ball is confirmed by a second census sample

A session caught once at waiting was busy seconds later (F10); watch now reports or wakes for a dropped ball only
when a second sample agrees."
```

---

### Task 5: An empty seat is one quiet line; the orchestrator is raised last (F8, F27)

**Files:**
- Modify: `flotilla/ledger/views.py` (`deviations`), `flotilla/watch/fleet.py` (`fleet`), `flotilla/watch/render.py`
  (`ORDER`), `flotilla/fleet/compose.py` (`raise_order`)
- Test: `tests/test_ledger_views.py`, `tests/test_watch_fleet.py`, `tests/test_fleet_compose.py`

**Interfaces:**
- Produces: deviation kind `seat_empty`; `fleet.SEATS = "seats"` item kind.

- [ ] **Step 1: Write the failing tests**

`tests/test_ledger_views.py` (append; use that file's own row helpers and profile constant — read its top first):

```python
def test_a_post_seat_whose_session_is_gone_is_an_empty_seat_not_a_gone_mover():
    seat = Row(id="r1", branch="fleet/reviewer-1", owner="review session 1", state="reserved",
               updated_at="2026-09-27T10:00:00+00:00")
    found = views.deviations({"r1": seat}, {}, live=set())
    assert [item["kind"] for item in found] == ["seat_empty"]
```

`tests/test_watch_fleet.py` (append):

```python
def test_empty_seats_are_one_quiet_line():
    seats = rows(row(id="r1", branch="fleet/reviewer-1", owner="review session 1", state="reserved"),
                 row(id="r2", branch="fleet/judge-1", owner="acceptance judge 1", state="reserved"))
    found = fleet.fleet(seats, PR, [sess("main session 1")], post_of=post_of)
    assert [(item.kind, item.branch) for item in found] == [(fleet.SEATS, "")]
    assert "2 post seat(s) with no live session: acceptance judge 1, review session 1" in found[0].text
    assert "flotilla fleet down" in found[0].text
```

`tests/test_fleet_compose.py` (append):

```python
def test_the_orchestrator_is_raised_last():
    order = compose.raise_order({"orchestrator": 1, "reviewer": 2, "minor": 1, "alpha": 1})
    assert order[-1] == "orchestrator" and order[:3] == ["reviewer", "minor", "alpha"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_views.py tests/test_watch_fleet.py tests/test_fleet_compose.py`
Expected: FAIL — `mover_gone`; one deviation item per seat; the orchestrator first.

- [ ] **Step 3: Implement**

`views.deviations`, the last check becomes:

```python
        mover = who_moves(row, profile, rows)
        if live is not None and mover and not mover.startswith("the ") and mover not in live:
            if row.state == "reserved":
                add("seat_empty", mover, f"the post seat is held for {mover}, and that session is not alive")
            else:
                add("mover_gone", mover, f"the move is {mover}'s, and that session is not alive")
```

`flotilla/watch/fleet.py`: `SEATS = "seats"`; in `fleet()`, build the deviation items from every deviation except
`seat_empty`, and add one item for all of those:

```python
    found = views.deviations(rows, profile, live)
    items = [Item(DEVIATION, item["branch"], f"{item['kind']}: {item['why']}", since_of(rows, item["branch"]),
                  who=item["kind"]) for item in found if item["kind"] != "seat_empty"]
    empty = sorted(item["on"] for item in found if item["kind"] == "seat_empty")
    if empty:
        items.append(Item(SEATS, "", f"{len(empty)} post seat(s) with no live session: {', '.join(empty)}; "
                                     "`flotilla fleet` lists them, `flotilla fleet down` stands the fleet down",
                          min((row.updated_at for row in rows.values() if row.owner in empty
                               and row.state == "reserved"), default=""), who=",".join(empty)))
```

(Keep the rest of `fleet()` as it is; the existing loop already skips `reserved` rows.)

`flotilla/watch/render.py`: `ORDER` gains `"seats": 9`, and `lines()` prints an item with an empty branch without
the `: ` prefix: `f"  {item.branch + ': ' if item.branch else ''}{item.text} ({age(...)})"`.

`flotilla/fleet/compose.py`:

```python
def raise_order(counts: dict[str, int]) -> list[str]:
    """The orchestrator last: its first report then sees every seat already raised (field test F8)."""
    first = [name for name in ORDER if name in counts and name != "orchestrator"]
    return first + sorted(name for name in counts if name not in ORDER) + (["orchestrator"]
                                                                          if "orchestrator" in counts else [])
```

- [ ] **Step 4: Run the tests and the full suite** → `N passed`. A test that expected `mover_gone` for a `reserved`
  row, or the orchestrator first, is updated to the new rule.

- [ ] **Step 5: Commit**

```bash
git add flotilla/ledger/views.py flotilla/watch/fleet.py flotilla/watch/render.py flotilla/fleet/compose.py tests/test_ledger_views.py tests/test_watch_fleet.py tests/test_fleet_compose.py
git commit -m "fix(watch): an empty post seat is one quiet line, and the orchestrator is raised last

A seat not raised yet read as a gone mover at the orchestrator's start (F8), and a stopped fleet alarmed for ever
(F27). Empty seats are now one line naming them and fleet down; the orchestrator is raised after every other seat."
```

---

### Task 6: `flotilla fleet down` and `/flotilla:down` (F27)

**Files:**
- Modify: `flotilla/fleet/retire.py` (`down`), `flotilla/fleet/commands.py` (`_fleet` dispatch), `flotilla/cli.py`
  (the `fleet` parser: optional action `down`), `templates/posts/orchestrator.md` (one bullet;
  `template_version: 5`)
- Create: `skills/down/SKILL.md`
- Test: `tests/test_fleet_retire.py`, `tests/test_skills.py`

**Interfaces:**
- Consumes: `retire.retire(ledger, name, *, caller, census, wait, poll, sleep) -> list[str]`, `retire.post_rows`,
  `retire.RetireRefused`.
- Produces: `retire.down(ledger, *, caller: str, me: str, census, wait=60.0, poll=1.0, sleep=time.sleep) ->
  tuple[list[str], int]` — the lines to print and how many seats were refused.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_fleet_retire.py`; read its top first and reuse its
  helpers for a ledger with post rows, a fake census and a fake `claude stop` — the names below stand for those)

```python
def test_fleet_down_retires_every_seat(tmp_path):
    ledger, census = seated(tmp_path, ["review session 1", "sender 1"])
    lines, refused = retire.down(ledger, caller="down by the person", me="", census=census, sleep=lambda s: None)
    assert refused == 0 and retire.post_rows(ledger) == []
    assert any("retired review session 1" in line for line in lines)
    assert any("retired sender 1" in line for line in lines)


def test_fleet_down_keeps_the_callers_own_seat(tmp_path):
    ledger, census = seated(tmp_path, ["orchestrator 1", "sender 1"])
    lines, refused = retire.down(ledger, caller="down by orchestrator 1", me="orchestrator 1", census=census,
                                 sleep=lambda s: None)
    assert [row.owner for row in retire.post_rows(ledger)] == ["orchestrator 1"]
    assert any("kept orchestrator 1" in line and "flotilla retire" in line for line in lines)


def test_fleet_down_refuses_whole_when_the_census_is_down(tmp_path):
    from flotilla.core.census import CensusUnavailable
    ledger, _ = seated(tmp_path, ["review session 1"])
    def down():
        raise CensusUnavailable("`claude` is not on PATH")
    with pytest.raises(retire.RetireRefused, match="nothing was stopped"):
        retire.down(ledger, caller="down", me="", census=down, sleep=lambda s: None)
    assert [row.owner for row in retire.post_rows(ledger)] == ["review session 1"]


def test_fleet_down_goes_on_past_a_seat_it_cannot_retire(tmp_path):
    ledger, census = seated(tmp_path, ["review session 1", "sender 1"], stop_fails={"review session 1"})
    lines, refused = retire.down(ledger, caller="down", me="", census=census, sleep=lambda s: None)
    assert refused == 1 and [row.owner for row in retire.post_rows(ledger)] == ["review session 1"]
    assert any(line.startswith("refused review session 1:") for line in lines)
```

If the file has no helper that seats several sessions, write `seated(tmp_path, names, stop_fails=())` in it: a
ledger from `ledgerkit.make_ledger`, one `reserve` row per name (as `spawn.raise_seat` writes it), a census whose
sessions are those names (removed from it once stopped), and `ledger.run` answering `claude stop <id>` with exit 0,
or 1 for a name in `stop_fails`, and `git worktree unlock` with exit 0.

Append to `tests/test_skills.py`: add `"down"` to `PERSON_ONLY`, and

```python
def test_the_down_command_shows_the_fleet_and_asks_first():
    text = (ROOT / "skills" / "down" / "SKILL.md").read_text(encoding="utf-8")
    assert "flotilla fleet`" in text and "flotilla fleet down" in text and "confirm" in text
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_fleet_retire.py tests/test_skills.py`
Expected: FAIL — `retire` has no `down`; no `skills/down`.

- [ ] **Step 3: Implement `retire.down`**

```python
def down(ledger, *, caller: str, me: str, census, wait: float = 60.0, poll: float = 1.0,
         sleep=time.sleep) -> tuple[list[str], int]:
    """Retire every seat of this ledger but the caller's own (field test F27). The census is asked first: a
    fleet that cannot be counted is not stood down at all."""
    try:
        census()
    except CensusUnavailable as err:
        raise RetireRefused(f"the census could not be asked ({err}); nothing was stopped or released") from err
    lines, refused = [], 0
    for row in post_rows(ledger):
        if row.owner == me:
            lines.append(f"kept {me}: it runs this command; retire it last, from elsewhere: "
                         f"`flotilla retire \"{me}\"`")
            continue
        try:
            lines += retire(ledger, row.owner, caller=caller, census=census, wait=wait, poll=poll, sleep=sleep)
        except RetireRefused as err:
            refused += 1
            lines.append(f"refused {row.owner}: {err}")
    if not lines:
        lines.append("no post rows: nobody was spawned, or everyone was retired")
    return lines, refused
```

- [ ] **Step 4: The command line**

`flotilla/cli.py`, the `fleet` parser:

```python
    fleet_ = sub.add_parser("fleet", help="the fleet's sessions, their trees and their work; `down` retires them")
    fleet_.add_argument("action", nargs="?", choices=["down"], help="retire every seat but your own")
    fleet_.add_argument("--root", default=".")
```

`flotilla/fleet/commands.py`, `_fleet` begins:

```python
    if getattr(args, "action", None) == "down":
        try:
            sessions = census()
        except CensusUnavailable as err:
            raise retire.RetireRefused(f"the census could not be asked ({err}); nothing was stopped or "
                                       "released") from err
        source = plat.probe().parent_pid_source
        found = find_calling_session(sessions, parent_of=lambda pid: plat.parent_pid(pid, source))
        me = found.name if found is not None and found.name else ""
        lines, refused = retire.down(ledger, caller=f"fleet down {caller_line(sessions)}", me=me, census=census)
        print("\n".join(lines))
        return 1 if refused else 0
```

- [ ] **Step 5: The person's command** — `skills/down/SKILL.md`:

```markdown
---
name: down
description: Stand the fleet down - retire every flotilla session of this project but your own, keep their trees, and name the work left orphaned.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet*)
---

1. Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet` and show every line: each seat, its state, its tree and its
   open work.
2. If any seat holds open work or uncommitted changes, say which, and ask the person to confirm standing the fleet
   down anyway. Otherwise ask them to confirm once.
3. Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet down` and show every line it prints. Trees are kept; orphaned
   rows wait for `flotilla work adopt`; a seat it could not retire is named with the reason. Never remove a tree
   yourself.
```

`templates/posts/orchestrator.md`: `template_version: 5`; add the bullet

```markdown
- When the person says the work is finished, they stand the fleet down with /flotilla:down; your own seat is kept,
  and they retire it last.
```

- [ ] **Step 6: Run the tests and the full suite** → `N passed`; `claude plugin validate .` → passed.

- [ ] **Step 7: Commit**

```bash
git add flotilla/fleet/retire.py flotilla/fleet/commands.py flotilla/cli.py skills/down templates/posts/orchestrator.md tests/test_fleet_retire.py tests/test_skills.py
git commit -m "feat(fleet): fleet down retires every seat but the caller's own

A finished fleet had no one-step way to stand down, and its seats alarmed for ever (F27). flotilla fleet down
retires each seat as retire does - stop, unlock, release, keep the tree, name orphaned work - and /flotilla:down
shows the fleet and asks first."
```

---

### Task 7: The record — decisions, spec, findings

**Files:**
- Modify: `docs/specs/2026-09-22-decisions-log.md`, `docs/specs/2026-09-22-flotilla-design.md` (sections 6.7, 7.5,
  8), `docs/field-tests/2026-09-27-snake-fleet.md`

- [ ] **Step 1: Decisions log** — entries 99–104 after entry 98, one per decision of this plan (Decisions 1–6), each
  ending `(the person's triage 2026-09-28)` for the person's parts and `(executor's decision, field fixes part 3, 2026-09-29)`
  for the executor's.

- [ ] **Step 2: Design spec.** Section 6.7 (Views): one paragraph on the sessions of this project and on empty
  seats. Section 7.5 (Life of the fleet): `flotilla fleet down` and `/flotilla:down`, the orchestrator raised last.
  Section 8: one sentence — `watch` confirms a dropped ball with a second census sample; the hooks do not. Read each
  section first and put the text where its subject is.

- [ ] **Step 3: Findings file** — after the Part 2 paragraph:

```markdown
Part 3 (what the views say, standing the fleet down) fixes F8, F9, F10, F13, F19, F20, F22, F27 — plan
`docs/plans/2026-09-29-field-fixes-views-and-fleet-down.md`.
```

- [ ] **Step 4: Check and commit**

```bash
python3 tools/check_no_cyrillic.py
git add docs
git commit -m "docs(specs): decisions 99-104 and the spec for the project's sessions, empty seats and fleet down"
```
