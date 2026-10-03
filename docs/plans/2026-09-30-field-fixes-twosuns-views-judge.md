# Field Fixes, Part 6 (Track B): What the Twosuns Fleet Found in the Views and the Judge — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the findings of the third field test that live in what flotilla *shows* and in the judge's moves:
false or unhelpful lines in `status` and `watch`, a `broke` that cannot be taken back, a `broke` refused on a part,
a lane summary that reads a wrapper's error as the verdict, and three things the judge and main posts do not say.

**Architecture:** Seven independent tasks, each in its own files. Nothing here touches delivery (`batch.py`,
`delivery.py`, `outside.py`, `handover.moved`) — that is Track A, run in parallel by another session; do not edit
those files. The judge gets one new move, `unbroke`. `findings` stops reporting two false signals. `watch` names
stale waits and a session waiting on the person that no row records. The lane's one-line summary skips wrapper
noise. The judge and main post templates say what the field test showed they need to.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`.

**Spec:** the field test `docs/field-tests/2026-09-29-twosuns-fleet.md` — findings H4, H5, H16, H17, H25b, H26a,
H26b, H27, H30, H32a, H32b, H38, H39, H42; `docs/specs/2026-09-22-flotilla-design.md` sections 6.2, 6.7, 6.9, 7.3.
Read each finding in the field-test file before its task: the finding is the spec's evidence, with times and
quotes.

## Decisions (the planner's, 2026-09-30; the person approved fixing the findings and splitting the work)

1. **`broke` on a part is legal** (H38). The part gate ("walk a path, not a part") exists so a part is not
   *accepted* as the whole; a defect seen on a part is a defect whatever ships later. `walked` keeps the gate.
2. **A fix branch drops the broken branch's type prefix** (H26a): `feat/stage-2-two-suns` → `fix/stage-2-two-suns`,
   not `fix/feat/stage-2-two-suns`. A branch with no `/` gets `fix/<branch>` as before.
3. **The judge may take a `broke` back** with `flotilla work unbroke <branch> --why "<why>"` (H27): legal on a
   shipped, broken row with no open fix row; it clears `broken`, keeps the history, and the row is the judge's to
   walk again. An open fix row must be released first — the move says so.
4. **`whose move` names what a broken row waits on** (H26b): "the fix `fix/x` (main session 6)" when a fix row is
   open, "nobody: it broke and no fix row is open" when none is.
5. **`unread_in_trunk` is not raised for a branch that only points at trunk** (H5): a tip on trunk's first-parent
   line is a fresh branch moved to trunk, not work that reached trunk unread. **`finished_not_handed` needs a
   commit of the row's own** (H4): a tip reachable from trunk or from another open row's branch carries nothing to
   hand. **`after_close` is not raised when another open row's branch contains the moved tip** (H32a): the work
   went on in that row.
6. **`watch` says when a wait's reason has moved on** (H30) — a wait whose note names a row (`r32`) or a branch
   that has moved since the wait was recorded gets "(since then: r32 shipped)". **`watch` raises a live fleet
   session whose census status is `waiting` and whose question is not in the broker queue** (H42) as a person item:
   "<name> waits on the person (census: waiting); answer it in its session". **The empty-seat line prints no age**
   (H25b): the ledger knows when the seat was reserved, not when its session left.
7. **The lane summary skips a wrapper's error line** (H32b): when no line counts tests, the summary is the last
   line that is not a shell's own error (`bash: …`, `sh: …`, `zsh: …`, `kill: …`).
8. **The judge post says three more things** (H16, H27, H39): ask for a way to start near what you must see
   (a position, a time, a weather by URL) rather than walking a different path; start your own preview on a port of
   your own (`--port <free> --strictPort`) and read the page's own revision stamp before you walk; what the test
   browser cannot perceive (sound, frame rate) is recorded for the person through the orchestrator, as a file the
   person can open, never walked as if heard. **The main post says**: a row that wires in a part another row builds
   claims with `--requires <that branch>` (H17).

Record these as decisions 121–128 in `docs/specs/2026-09-22-flotilla-design-decisions` — the file is
`docs/specs/2026-09-22-decisions-log.md`, in its existing numbered style, one entry per decision above, ending
"(planner's decision, field fixes part 6 track B, 2026-09-30)". **Numbers 121–129 are this track's; Track A uses
130 and up** — do not take a number outside your range.

## Global Constraints

- flotilla is English only (`python3 tools/check_no_cyrillic.py <files>`); Linux and macOS; Python 3.11+ standard
  library only.
- **Do not edit** `flotilla/ledger/batch.py`, `flotilla/ledger/delivery.py`, `flotilla/ledger/outside.py`,
  `flotilla/ledger/handover.py` (except the `has_own_commits` signature change in Task 4, which is additive),
  `flotilla/ledger/transitions.py` beyond adding the one `unbroke` edge, or `templates/posts/sender.md`,
  `templates/posts/reviewer.md`, `templates/posts/orchestrator.md`. Track A owns them.
- Tests run three ways before a task is done: default, `--python 3.11`, `GIT_CONFIG_GLOBAL=/dev/null`; plus
  `claude plugin validate .` and `claude plugin validate .claude-plugin/plugin.json`.
- The suite command: `uv run --with pytest python -m pytest -p no:cacheprovider tests/` — without `-q` (pyproject
  sets it); read the `N passed` line.
- A post template whose text changes gets its `template_version` raised by one.
- Commit messages: Conventional Commits, English, one finished thing per commit.
- Work in your own worktree of `/home/user/workspace/flotilla` on branch `fix/part6-views-judge`, cut from `main`.
  Never switch or write the main checkout.

## Review Focus

1. **`unbroke` on a row whose fix row is still open** → refused, naming the fix row and "release it first"; the row
   stays broken (Task 2, `test_unbroke_refuses_while_a_fix_row_is_open`).
2. **A fix branch name that is already taken** (`fix/x` exists as a row or a branch) → `_free_branch` numbers it
   (`fix/x-2`), the prefix rule does not bypass that (Task 1, `test_a_fix_branch_name_is_numbered_when_taken`).
3. **A branch that points at trunk but was fast-forwarded with the author's own commit** (not a merge) → that is
   `direct_commit`'s business, `unread_in_trunk` stays quiet; the direct commit is still reported (Task 4,
   `test_a_fresh_branch_at_trunk_is_not_unread_work`).
4. **A wait note that mentions a row id inside another word** (`r3` inside `r33`, `error`) → only whole tokens
   `r\d+` count (Task 5, `test_a_stale_wait_matches_whole_row_ids_only`).
5. **A lane run whose only lines are a wrapper's errors** → the last line is kept (never "(no output)" when there
   was output) (Task 6, `test_a_summary_of_only_wrapper_errors_keeps_the_last_line`).

---

### Task 1: `broke` on a part, and a fix branch without the nested prefix (H38, H26a)

**Files:** Modify `flotilla/ledger/judging.py` (`broke`); Test `tests/test_ledger_judging.py`; update existing tests
that name `fix/feat/x` (grep `fix/feat/` under `tests/`) to the new name `fix/x`.

**Interfaces:** Produces `judging.fix_branch_name(rows, branch) -> str`.

- [ ] **Step 1: Write the failing tests** (append; `part_and_whole`, `world`, `JUDGE`, `JUDGED` are the file's own)

```python
def test_a_part_may_be_broken_while_the_whole_is_not_shipped(tmp_path):
    root, ledger, part = part_and_whole(tmp_path)
    broken, fix = judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="the storm wall", saw="a seam")
    assert broken.broken == "the storm wall" and fix.fixes == broken.id


def test_a_fix_branch_drops_the_type_prefix(tmp_path):
    root, ledger, row = world(tmp_path)
    _, fix = judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="start", saw="nothing")
    assert fix.branch == "fix/x"


def test_a_fix_branch_name_is_numbered_when_taken(tmp_path):
    from ledgerkit import branch
    root, ledger, row = world(tmp_path)
    branch(root, "fix/x", "someone else's fix")
    rows = ledger.rows()
    assert judging.fix_branch_name(rows, "feat/x") == "fix/x"          # the row table does not know the branch
    with pytest.raises(MoveRefused, match="already exists"):
        judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="start", saw="nothing")
```

And change the existing `test_a_part_is_not_walked_while_the_row_building_on_it_is_not_shipped`: keep the
`walked` refusal, delete the `broke` refusal block (it now passes — decision 1).

- [ ] **Step 2: Run them** — `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_judging.py`
  Expected: the three new tests FAIL (the part refusal; `fix/feat/x`; `fix_branch_name` missing).

- [ ] **Step 3: Implement** in `judging.py`:

```python
def fix_branch_name(rows: dict[str, Row], branch: str) -> str:
    """`fix/` and the broken branch's name without its type prefix (feat/x -> fix/x), numbered when taken."""
    bare = branch.split("/", 1)[1] if "/" in branch else branch
    return _free_branch(rows, f"fix/{bare}")
```

In `broke`: remove the `_whole_or_walkable(ledger, s.rows, row)` line, and replace
`_free_branch(s.rows, f"fix/{branch}")` with `fix_branch_name(s.rows, branch)`. Update the docstring of
`_whole_or_walkable` to say it guards `walked` only. Update `templates/posts/judge.md`'s "Walk a path, not a part"
bullet: "`walked` refuses a part until then … A defect seen on a part is recorded with `broke` at once." Raise its
`template_version`.

- [ ] **Step 4: Run the file, then the suite.** Expected: all green; fix any test that expected `fix/feat/...`.
- [ ] **Step 5: Commit** — `fix(judge): broke a part at once, and name its fix branch without the type prefix`

### Task 2: `unbroke` — the judge takes a false `broke` back (H27)

**Files:** Modify `flotilla/ledger/judging.py` (new `unbroke`), `flotilla/ledger/transitions.py` (one edge),
`flotilla/posts.py` (`MOVES`), `flotilla/cli.py` (sub-parser), `flotilla/ledger/commands.py` (dispatch),
`templates/posts/judge.md` (`may` and a bullet); Test `tests/test_ledger_judging.py`, `tests/test_posts.py` if it
lists moves.

**Interfaces:** Produces `judging.unbroke(ledger, actor, branch, *, why: str) -> Row`.

- [ ] **Step 1: Write the failing tests**

```python
def broken_world(tmp_path):
    root, ledger, row = world(tmp_path, JUDGED)
    judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="start", saw="stale preview")
    return root, ledger


def test_the_judge_takes_a_false_broke_back_once_the_fix_row_is_released(tmp_path):
    from flotilla.ledger import core
    root, ledger = broken_world(tmp_path)
    core.release(ledger, actor(ledger, OWNER), "fix/x", why="false broke: a stale preview")
    row = judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why="walked a foreign preview on port 4173")
    assert (row.state, row.broken) == ("shipped", "")
    assert row.history[-1]["evidence"]["why"].startswith("walked a foreign")
    walked = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="the eclipse")
    assert walked.state == "walked"


def test_unbroke_refuses_while_a_fix_row_is_open(tmp_path):
    root, ledger = broken_world(tmp_path)
    with pytest.raises(MoveRefused, match="fix/x.*release it first"):
        judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why="false")


def test_unbroke_needs_a_reason_and_a_broken_row(tmp_path):
    root, ledger, row = world(tmp_path, JUDGED)
    with pytest.raises(MoveRefused, match="is not broken"):
        judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why="x")
    with pytest.raises(MoveRefused, match="--why"):
        judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why=" ")


def test_only_a_post_that_may_unbroke_makes_the_move(tmp_path):
    root, ledger = broken_world(tmp_path)
    with pytest.raises(MoveRefused):
        judging.unbroke(ledger, actor(ledger, OWNER), "feat/x", why="x")
```

Check how `core.release` is called in this file's other tests and match its signature; if release of a fix row
needs `--settled-by` or `--why`, use `why=`.

- [ ] **Step 2: Run** — Expected: FAIL, `judging.unbroke` does not exist.
- [ ] **Step 3: Implement.** `transitions.BASE["shipped"]` gains `"unbroke": "shipped"`. `posts.MOVES` gains
  `"unbroke"`. In `judging.py`:

```python
def unbroke(ledger: Ledger, actor: Actor, branch: str, *, why: str) -> Row:
    """Take a `broke` back: the judge found the break was its own mistake (a stale build, the wrong page)."""
    require_may(actor, "unbroke", ledger.posts)
    if not why.strip():
        raise MoveRefused("say why the break was not one (--why)")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        if not row.broken:
            raise MoveRefused(f"`{branch}` is not broken")
        state = s.next_state(row, "unbroke")
        open_fixes = [other.branch for other in fixes_of(s.rows, row) if other.is_open]
        if open_fixes:
            raise MoveRefused(f"`{branch}` has an open fix row ({', '.join(open_fixes)}); release it first")
        return s.append(actor, row.id, "unbroke", state, fields={"broken": ""}, evidence={"why": why.strip()})
```

`cli.py`: `unbroke = move_parser("unbroke", "take a broke back: it was the judge's mistake (judge)")` with
`--why` required. `commands.py` dispatch: `"unbroke": lambda l, a, x: judging.unbroke(l, a, x.branch, why=x.why)`.
`templates/posts/judge.md`: `may: [reserve, walked, broke, unbroke, wait]` and a bullet "A break you find was your
own mistake — a stale or foreign build, the wrong page — is taken back with `flotilla work unbroke <branch> --why
"<what was wrong>"` once its fix row is released; it is not left standing." Raise `template_version` (once per task
is fine even if Task 1 raised it).

- [ ] **Step 4: Run the file, `tests/test_posts.py`, `tests/test_skills.py`, then the suite.** Expected: green. If
  a test enumerates `MOVES` or the judge's `may`, extend it.
- [ ] **Step 5: Commit** — `feat(judge): unbroke takes a false break back`

### Task 3: `whose move` names what a broken row waits on (H26b)

**Files:** Modify `flotilla/ledger/views.py` (new `waits_on`), `flotilla/ledger/commands.py` (the `whose move`
loop, ~line 330); Test `tests/test_ledger_views.py`, `tests/test_ledger_report.py` or wherever `status` output is
tested (grep `whose move` under `tests/`).

**Interfaces:** Produces `views.waits_on(row, rows, profile) -> str` — "" when `who_moves` names a mover.

- [ ] **Step 1: Write the failing tests** (in `tests/test_ledger_views.py`, building a broken row the way
  `tests/test_ledger_judging.py` does — import its helpers or repeat `world`/`broke`):

```python
def test_a_broken_row_names_its_open_fix(tmp_path):
    root, ledger = broken_world(tmp_path)          # as in Task 2
    rows = ledger.rows()
    broken = next(r for r in rows.values() if r.branch == "feat/x")
    assert views.waits_on(broken, rows, JUDGED) == "the fix `fix/x` (main session 1)"


def test_a_broken_row_with_no_fix_says_so(tmp_path):
    from flotilla.ledger import core
    root, ledger = broken_world(tmp_path)
    core.release(ledger, actor(ledger, OWNER), "fix/x", why="will not happen")
    rows = ledger.rows()
    broken = next(r for r in rows.values() if r.branch == "feat/x")
    assert views.waits_on(broken, rows, JUDGED) == "nobody: it broke and no fix row is open"
```

- [ ] **Step 2: Run** — FAIL, no `waits_on`.
- [ ] **Step 3: Implement** in `views.py` next to `who_moves`:

```python
def waits_on(row: Row, rows: dict, profile: dict) -> str:
    """Why nobody is named for this row's move, in words a person can act on; "" when someone is named."""
    if who_moves(row, profile, rows):
        return ""
    if row.state == "shipped" and row.broken:
        fixes = [other for other in rows.values() if other.fixes == row.id and other.is_open]
        if fixes:
            return ", ".join(f"the fix `{other.branch}` ({other.owner or 'no owner'})" for other in fixes)
        return "nobody: it broke and no fix row is open"
    return ""
```

In `commands.py`, replace `mover = views.who_moves(row, ledger.profile, rows) or "nobody named"` with
`mover = views.who_moves(row, ledger.profile, rows) or views.waits_on(row, rows, ledger.profile) or "nobody named"`
(the part-gate suffix that follows keeps working: it only appends).

- [ ] **Step 4: Run views + status tests, then the suite.**
- [ ] **Step 5: Commit** — `fix(status): a broken row names the fix it waits on`

### Task 4: three false findings (H5, H4, H32a)

**Files:** Modify `flotilla/ledger/findings.py`, `flotilla/ledger/gitq.py` (one helper),
`flotilla/ledger/handover.py` (`has_own_commits` gains an optional keyword), `flotilla/ledger/commands.py`
(`_finished` passes it); Test `tests/test_ledger_findings.py`, `tests/test_ledger_handover.py` or the views test
that covers `finished_not_handed` (grep it).

**Interfaces:** Produces `gitq.on_first_parent(root, sha, ref, *, run, depth=500) -> bool | None` and
`handover.has_own_commits(ledger, tip, *, beside=()) -> bool | None`.

- [ ] **Step 1: Write the failing tests** (use the file's own world builders; the shapes to build):

```python
def test_a_fresh_branch_at_trunk_is_not_unread_work(tmp_path):
    # a claimed row whose branch was moved to trunk's tip (a merge commit on trunk's first-parent line)
    # -> no `unread_in_trunk` for it; a real commit pushed straight to trunk is still `direct_commit`.
    ...


def test_a_stacked_branch_with_no_commit_of_its_own_is_not_finished(tmp_path):
    # row A claimed on feat/a with one commit and a green handover receipt; row B claimed on feat/b cut at
    # feat/a's tip, no commit of its own -> `_finished(ledger, B)` is False, `_finished(ledger, A)` is True.
    ...


def test_after_close_is_quiet_when_another_open_row_carries_the_moved_tip(tmp_path):
    # row A released at tip t1; feat/a moves to t2; row B (open, on feat/b) has t2 as an ancestor of its tip
    # -> no `after_close` for feat/a. Without row B, the finding fires as before.
    ...
```

Write each body fully in the file's existing style (`branch`, `commit`, `git`, `merge`, `drive`, receipts as
`tests/test_ledger_findings.py` and `tests/test_ledger_views.py` already build them). Each test must be seen
failing before the fix.

- [ ] **Step 2: Run** — the three FAIL.
- [ ] **Step 3: Implement.**
  - `gitq.on_first_parent`: `git rev-list --first-parent --max-count=<depth> <ref>`; `sha in lines`; None when git
    fails.
  - `findings.findings`, the `unread_in_trunk` condition: add `and gitq.on_first_parent(ledger.root, local, trunk,
    run=ledger.run) is not True`.
  - `handover.has_own_commits(ledger, tip, *, beside=())`: when `beside` is given, the answer is whether
    `git rev-list <tip> ^<trunk_head> ^<b> ...` for each branch tip in `beside` lists anything; without `beside`,
    exactly as now (the hand gate keeps calling it without).
  - `commands._finished`: pass `beside=[t for t in (gitq.branch_tip(ledger.root, other.branch, run=ledger.run)
    for other in ledger.rows().values() if other.is_open and other.branch != row.branch) if t]`.
  - `findings`, `after_close`: skip when `current` is an ancestor of any open row's branch tip on another branch.
- [ ] **Step 4: Run findings/handover/views tests, then the suite.**
- [ ] **Step 5: Commit** — `fix(findings): a branch at trunk, a stacked branch and work carried on elsewhere are not findings`

### Task 5: `watch` names stale waits and a session waiting on the person; seats print no age (H30, H42, H25b)

**Files:** Modify `flotilla/ledger/views.py` (new `moved_since`), `flotilla/watch/fleet.py`,
`flotilla/watch/whose.py`, `flotilla/watch/render.py`; Test `tests/test_ledger_views.py` and the watch tests
(grep `def test` in `tests/` for `fleet(` / `mine(` / `lines(` callers).

**Interfaces:** Produces `views.moved_since(rows, row) -> list[str]` — e.g. `["r32 shipped", "feat/b handed"]`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_wait_naming_a_row_that_moved_on_is_marked(tmp_path):
    # row A waits (note "r2 is blocked on inbatch permission"); row r2 then ships.
    # views.moved_since(rows, A) == ["r2 shipped"]; the PERSON / WAITING item text ends "(since then: r2 shipped)".
    ...


def test_a_stale_wait_matches_whole_row_ids_only(tmp_path):
    # note "r33 error" must not match r3; "the error" must not match anything.
    ...


def test_a_fleet_session_waiting_on_the_person_is_raised(tmp_path):
    # census: a live session named "orchestrator 1", kind background, state "blocked", status "waiting";
    # not in `asking` -> one PERSON item "orchestrator 1 waits on the person (census: waiting); answer it in its
    # session". In `asking` -> no such item (the QUESTION item covers it).
    ...


def test_the_empty_seat_line_prints_no_age():
    # render.lines on a SEATS item whose since is "" prints no "(... min)" / "(age unknown)" suffix.
    ...
```

Write the bodies with the existing fakes: the watch tests already build census sessions (look for how
`not_working`/`_census_word` are tested) and rows.

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.**
  - `views.moved_since(rows, row)`: tokens `re.findall(r"\br(\d+)\b", row.note)` → row ids `r<n>`; also any open or
    ended row whose `branch` appears as a whole word in the note. For each such other row with
    `other.updated_at > row.updated_at` return `f"{other.id} {other.state}"` (dedupe, keep order).
  - `watch/fleet.py`: the PERSON item text gains `f" (since then: {', '.join(moved)})"` when `moved_since` is
    non-empty. New: for each live session with `session.status == "waiting"` whose name is not in `asking` and
    whose post is a fleet post (`post_of(name)` is truthy), append
    `Item(PERSON, "", f"{name} waits on the person (census: waiting); answer it in its session", "", who=name)`.
    The SEATS item's `since` becomes `""`.
  - `watch/whose.py`: the WAITING item text gains the same "(since then: …)" suffix.
  - `watch/render.py`: when `item.since` is `""`, print no age suffix.
- [ ] **Step 4: Run views and watch tests, then the suite.**
- [ ] **Step 5: Commit** — `fix(watch): stale waits say what moved, a session waiting on the person is raised`

### Task 6: the lane summary skips a wrapper's error (H32b)

**Files:** Modify `flotilla/lane/run.py` (`summarize`); Test `tests/test_lane_run.py`.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_wrapper_error_after_the_output_is_not_the_summary():
    lines = ['{"tag":"storm","programs":16}', "bash: line 1: kill: (3500188) - No such process"]
    assert run.summarize(lines) == '{"tag":"storm","programs":16}'


def test_a_summary_of_only_wrapper_errors_keeps_the_last_line():
    assert run.summarize(["sh: 1: foo: not found"]) == "sh: 1: foo: not found"


def test_a_counting_line_still_wins():
    assert run.summarize(["212 passed in 3.1s", "bash: kill: no such process"]) == "212 passed in 3.1s"
```

(Match the module's import name in that test file.)

- [ ] **Step 2: Run** — the first FAILS.
- [ ] **Step 3: Implement**: `WRAPPER = re.compile(r"^(?:ba|z|)sh: |^kill: |^/bin/(?:ba)?sh: ")`; after the
  `SUMMARY` search, return the last line that does not match `WRAPPER`, else the last line, else "(no output)".
- [ ] **Step 4: Run the lane tests, then the suite.**
- [ ] **Step 5: Commit** — `fix(lane): a run's summary is its output, not its wrapper's error`

### Task 7: what the judge and main posts say (H16, H27, H39, H17)

**Files:** Modify `templates/posts/judge.md`, `templates/posts/main.md`; Test `tests/test_posts.py` /
`tests/test_skills.py` (whatever pins template text or versions — grep `template_version`).

- [ ] **Step 1: Write the failing tests** — one per sentence the posts must now carry, asserting the text is
  present (match the style of existing post-text tests, e.g. a `"--requires" in body` check):
  - judge: "start near", "--strictPort", "revision", "cannot perceive" (or the exact phrases you write below);
  - main: "--requires" in a sentence about wiring in a part.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Write the text.** Judge, three bullets:
  - "When what you must see is too far or too slow to reach in the test browser (a place kilometres away, a time
    of day, a weather), ask the orchestrator for a way to start near it — a position, a time, a weather by URL.
    Walking a different path is not walking this one."
  - "Start your own preview on a free port (`--port <n> --strictPort`), never the default one another tree may
    hold, and before walking read the page's own revision stamp: a walk over a build you did not name is a
    verdict about someone else's build."
  - "What the test browser cannot perceive — sound, real frame rate — is not walked as if perceived. Ask the
    orchestrator for it to reach the person as something they can open (a recording, a build), and record the
    wait on the orchestrator."
  Main, one bullet: "A row that wires in a part another row builds — the core it calls, the module it plugs in —
  is claimed with `--requires <that branch>`, so the part is not walked before it is wired."
  Raise both `template_version`s.
- [ ] **Step 4: Run posts/skills tests, then the suite, then both `claude plugin validate` commands.**
- [ ] **Step 5: Commit** — `docs(posts): the judge starts near, stamps its build, hands the person what it cannot hear`

### Task 8: the decisions log

- [ ] Add decisions 121–128 (one per decision above) to `docs/specs/2026-09-22-decisions-log.md` in its numbered
  style. Commit — `docs: decisions 121-128 from field fixes part 6 track B`.

## Finish

Run the suite three ways and both validations; run `python3 tools/check_no_cyrillic.py` over every changed file.
Then report to the coordinator (the session that dispatched you): the branch, its tip, the commit list, the three
suite results with their `N passed` lines, and any `Ruling:` you made. Do not merge, push, or edit `main`.
