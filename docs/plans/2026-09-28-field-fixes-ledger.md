# Field Fixes, Part 1: the Ledger — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix what stalled the snake fleet inside the ledger itself: the sender's land path in direct-push mode,
the mover who could not record its own wait, a hand-over or a "finished" alarm over a branch with no commits of its
own, a fix row that another row made unnecessary, and a judge who walks a part of the work as if it were the whole
path.

**Architecture:** Small changes to existing ledger modules: `delivery.land` also accepts a merge already on
`origin/<trunk>`; `batch.unaccounted` learns to count from the row's base; `handover.wait` admits the row's mover;
`handover.hand` and `commands._finished` refuse a tip that is already on trunk; `core.release` gains `settled_by`;
`views` gains `pending_dependents`, `who_moves` defers the judge while rows that build on this one are not shipped,
and a new annotation `walkable` (the orchestrator's) lets a part be walked on its own. Post templates for the sender,
judge and orchestrator say the new sequences.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`.

**Spec:** the field test `docs/field-tests/2026-09-27-snake-fleet.md` (findings F14, F15, F18, F21, F24, F26 and the
triage with the person of 2026-09-28: `land` accepts origin; the judge waits for rows that require the one it walks, the
orchestrator may override); `docs/specs/2026-09-22-flotilla-design.md` sections 6.2–6.4, 7.4.

## Decisions (the person's, 2026-09-28, and the executor's)

1. **`land` in direct-push mode accepts a merge reachable from `origin/<trunk>`** (the person). The local trunk, checked
   out in the main checkout that belongs to nobody, is not needed and is never moved. The sender merges in its own
   tree on a branch from `origin/<trunk>`, takes the push receipt, pushes `HEAD:<trunk>`, then records `land` and
   `ship`. Pushing before `land` is therefore the sequence, not a fault (F15).
2. **The unread-work check still runs for a merge already on origin**, counted from the row's base: a push that
   carried work nobody read is refused at `land` with the `inbatch` fix, even though it is already on origin — the
   ledger must not record it as read.
3. **`wait` is allowed to the row's mover** as well as its owner and reader (F24): the session `who_moves` names, or
   a session of the post a post-named mover ("the sender", "the judge") stands for.
4. **A tip already on trunk has no commits of its own**: `hand` refuses it, naming `release --settled-by`, and the
   "finished_not_handed" deviation never fires for it (F18).
5. **`release --settled-by <branch|row>`** closes a row whose purpose another delivered row fulfilled (F26); the
   other row must be delivered; the reason defaults to "settled by <branch>".
6. **The judge waits for the rows that build on a row** (the person): while an open row that `requires` this one is not
   delivered, `who_moves` names nobody for the judge's move, and `walked` / `broke` refuse, naming those rows.
   **`flotilla work walkable <branch> --why`**, an annotation the orchestrator may make, lets that row be walked on
   its own.

## Global Constraints

- Python floor 3.11; stdlib only. English only; `python3 tools/check_no_cyrillic.py` passes.
- Every refusal names the next legal move (triage item 2, applied here to the refusals this plan touches).
- Tests are hermetic: repositories in `tmp_path` with a local bare origin.
- Full suite on the default interpreter, on 3.11, and with `GIT_CONFIG_GLOBAL=/dev/null`; `claude plugin validate .`.

## Review Focus

1. **A merge on neither the local trunk nor origin** is refused with the sequence to follow. Test: Task 1
   `test_a_merge_on_neither_trunk_is_refused_with_the_sequence`.
2. **Unread work pushed along with the merge** is still refused at `land`. Test: Task 1
   `test_unread_work_already_on_origin_is_refused_at_land`.
3. **A session that is neither owner, reader nor mover** still cannot record a wait. Test: Task 2
   `test_a_session_that_does_not_move_the_row_cannot_record_its_wait`.
4. **`settled-by` over an undelivered row** is refused. Test: Task 4 `test_settled_by_an_undelivered_row_is_refused`.
5. **A row nothing requires** is walked as before (no deferral by accident). Test: Task 5
   `test_a_row_nothing_requires_is_walked_as_before`.

---

## File map

| file | change |
|---|---|
| `flotilla/ledger/delivery.py` | `land` accepts a merge on origin in direct mode; refusals name the sequence |
| `flotilla/ledger/batch.py` | `outgoing` / `unaccounted` accept `since` (count from a base instead of origin) |
| `flotilla/ledger/handover.py` | `wait` admits the mover; `hand` refuses a tip already on trunk |
| `flotilla/ledger/commands.py` | `_finished` false for a tip on trunk; `release --settled-by`; `walkable` move; status shows a deferred judge |
| `flotilla/ledger/core.py` | `release(..., settled_by="")` |
| `flotilla/ledger/views.py` | `pending_dependents`; `who_moves(row, profile, rows=None)` defers the judge |
| `flotilla/ledger/judging.py` | `walked` / `broke` refuse while dependents are undelivered and the row is not walkable |
| `flotilla/ledger/steering.py` | `walkable` annotation |
| `flotilla/ledger/model.py`, `transitions.py` | field `walkable`; annotation `walkable` |
| `flotilla/cli.py` | `release --settled-by`, `--why` optional with it; `work walkable` |
| `flotilla/watch/whose.py`, `flotilla/watch/fleet.py` | pass `rows` to `who_moves` |
| `templates/posts/sender.md`, `judge.md`, `orchestrator.md` | the new sequences |
| `docs/events/schema.json` | regenerated (a new row field) |
| tests: `test_ledger_delivery.py`, `test_ledger_handover.py`, `test_ledger_core.py`, `test_ledger_judging.py`, `test_ledger_views.py`, `test_posts.py` | |

---

### Task 1: `land` accepts a merge already on origin

**Files:**
- Modify: `flotilla/ledger/delivery.py` (`land`), `flotilla/ledger/batch.py` (`outgoing`, `unaccounted`)
- Test: `tests/test_ledger_delivery.py`

**Interfaces:**
- Produces: `batch.outgoing(ledger, upto, *, base="", since="")`, `batch.unaccounted(ledger, rows, upto, *,
  base="", since="")` — `since` (a revision) replaces origin's trunk as the point counted from.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger_delivery.py`:

```python
def pushed_from_a_side_tree(root, name="feat/x", *, extra=None):
    """The sender's direct-push sequence without touching the local trunk: merge on a branch from origin's trunk,
    push HEAD:main, come back. Returns the pushed commit."""
    git(root, "fetch", "-q", "origin")
    git(root, "checkout", "-q", "-b", f"integrate-{name.replace('/', '-')}", "origin/main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "-m", f"merge {name}", name)
    if extra:
        commit(root, extra, "extra.txt")
    pushed = git(root, "rev-parse", "HEAD")
    git(root, "push", "-q", "origin", "HEAD:main")
    git(root, "checkout", "-q", "main")
    return pushed


def test_land_accepts_a_merge_already_pushed_to_origin(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root)
    assert git(root, "rev-parse", "main") != pushed   # the local trunk never moved
    row = delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)
    assert (row.state, row.merge) == ("landed", pushed)
    assert delivery.ship(ledger, actor(ledger, SENDER), "feat/x").state == "shipped"


def test_land_without_merge_takes_origins_trunk_when_the_local_one_lacks_the_work(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root)
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x").merge == pushed


def test_a_merge_on_neither_trunk_is_refused_with_the_sequence(direct):
    root, ledger = direct
    queued(root, ledger)
    with pytest.raises(MoveRefused, match="push HEAD:main"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge="feat/x")


def test_unread_work_already_on_origin_is_refused_at_land(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root, extra="a quick fix nobody read")
    with pytest.raises(MoveRefused, match="nobody read: .* a quick fix nobody read"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)
```

Add `IDENTITY` to the file's `from ledgerkit import (...)` list.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_delivery.py -k "origin or neither"`
Expected: FAIL — "is not on the local `main`" on the first three; the fourth may fail the same way.

- [ ] **Step 3: Implement**

In `flotilla/ledger/batch.py`, give `outgoing` and `unaccounted` a `since` parameter:

```python
def outgoing(ledger, upto: str, *, base: str = "", since: str = "") -> list[str] | None:
    """Commits `upto` carries that origin's trunk lacks (or past `base` without an origin), oldest first; with
    `since`, the commits past that revision instead — for a push that already reached origin."""
    remote = f"refs/remotes/origin/{ledger.trunk}"
    stop = since or (remote if gitq.resolve(ledger.root, remote, run=ledger.run) else base)
    if not stop:
        return None
    listed = _git(ledger, "rev-list", "--reverse", upto, f"^{stop}")
    return None if listed is None else listed.split()
```

```python
def unaccounted(ledger, rows: dict[str, Row], upto: str, *, base: str = "", since: str = "") -> list[str] | None:
    """The commits `upto` would carry that no verdict covers; None when what it carries cannot be told."""
    commits = outgoing(ledger, upto, base=base, since=since)
    if commits is None:
        return None
    accounting = Accounting(ledger, rows)
    return [sha for sha in commits if accounting.account(sha) is None]
```

In `flotilla/ledger/delivery.py`, replace `land` with:

```python
SEQUENCE = ("merge `{branch}` in your own tree on a branch from `origin/{trunk}`, run `flotilla receipt run "
            "--purpose push` there, push HEAD:{trunk}, then `flotilla work land {branch} --merge <that commit>`")


def land(ledger: Ledger, actor: Actor, branch: str, *, merge: str | None = None) -> Row:
    """Record the commit that carries the revision read: on the local trunk, or, in direct-push mode, already on
    origin's trunk (the local trunk is checked out in the main checkout, which belongs to nobody)."""
    require_may(actor, "land", ledger.posts)
    trunk_head = gitq.resolve(ledger.root, f"refs/heads/{ledger.trunk}", run=ledger.run)
    origin_head = None
    if ledger.mode == "direct":
        _fetch(ledger)
        origin_head = gitq.resolve(ledger.root, f"refs/remotes/origin/{ledger.trunk}", run=ledger.run)
    if trunk_head is None and origin_head is None:
        raise MoveRefused(f"git could not resolve the local trunk `{ledger.trunk}` or origin's")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "land")
        read = batch.revision_of(row)
        if not read:
            raise MoveRefused(f"`{branch}` carries no recorded revision; nothing lands that nobody read")
        if merge:
            commit = gitq.resolve(ledger.root, merge, run=ledger.run)
            if commit is None:
                raise MoveRefused(f"git could not resolve --merge {merge}")
        elif trunk_head and gitq.is_ancestor(ledger.root, read, trunk_head, run=ledger.run) is True:
            commit = trunk_head
        else:
            commit = origin_head or trunk_head
        on_local = bool(trunk_head) and gitq.is_ancestor(ledger.root, commit, trunk_head, run=ledger.run) is True
        on_origin = (not on_local and bool(origin_head)
                     and gitq.is_ancestor(ledger.root, commit, origin_head, run=ledger.run) is True)
        if not on_local and not on_origin:
            where = f"the local `{ledger.trunk}`" + (f" or origin's" if ledger.mode == "direct" else "")
            raise MoveRefused(f"{commit[:7]} is not on {where}; "
                              + (SEQUENCE.format(branch=branch, trunk=ledger.trunk) if ledger.mode == "direct"
                                 else f"merge `{branch}` into `{ledger.trunk}` first"))
        contains = gitq.is_ancestor(ledger.root, read, commit, run=ledger.run) is True
        if not contains and not _squash_on_trunk(ledger, row, read, commit):
            raise MoveRefused(f"{commit[:7]} does not contain the revision read ({read[:7]}); "
                              + (SEQUENCE.format(branch=branch, trunk=ledger.trunk) if ledger.mode == "direct"
                                 else f"merge `{branch}` into `{ledger.trunk}` first"))
        if on_local:
            loose = batch.unaccounted(ledger, s.rows, trunk_head, base=row.base)   # what the push will carry
        else:
            loose = batch.unaccounted(ledger, s.rows, commit, since=row.base) if row.base else None
        if loose is None:
            raise MoveRefused("could not tell what the push carries: no origin, and no base recorded on the row")
        if loose:
            named = "; ".join(f"{sha[:7]} {batch.subject(ledger, sha)}" for sha in loose[:5])
            more = f" and {len(loose) - 5} more" if len(loose) > 5 else ""
            raise MoveRefused(f"the batch carries work nobody read: {named}{more}. Hand it over for review, or "
                              "record work born in the batch with `flotilla work inbatch`")
        where = "trunk" if on_local else f"origin/{ledger.trunk}"
        return s.append(actor, row.id, "land", state, fields={"merge": commit},
                        evidence={"trunk": trunk_head if on_local else origin_head, "on": where})
```

Update the module docstring's `land` paragraph to say: "`land` (direct push and local only) records the commit that
carries the revision read — on the local trunk, or in direct-push mode already on origin's trunk, because the local
trunk is checked out in the main checkout, which belongs to nobody — and refuses when the push carries a commit no
verdict covers."

Existing test `test_a_merge_that_is_not_on_trunk_is_refused` matches "not on the local `main`": keep that phrase at
the start of the refusal (it is), so the test stays green.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_delivery.py tests/test_ledger_batch.py tests/test_ledger_judging.py`
Expected: all passed.

- [ ] **Step 5: Injection — a merge only on origin needs the unread check too**

Change `loose = batch.unaccounted(ledger, s.rows, commit, since=row.base) if row.base else None` to `loose = []`;
expected red: `test_unread_work_already_on_origin_is_refused_at_land`. Roll back by file copy, check with `cmp`,
under `PYTHONDONTWRITEBYTECODE=1`.

- [ ] **Step 6: Commit**

```bash
git add flotilla/ledger/delivery.py flotilla/ledger/batch.py tests/test_ledger_delivery.py
git commit -m "fix(ledger): land accepts a merge already on origin in direct-push mode (F14, F15)"
```

---

### Task 2: The mover records its wait

**Files:**
- Modify: `flotilla/ledger/handover.py` (`wait`)
- Test: `tests/test_ledger_handover.py`

**Interfaces:**
- Consumes: `views.who_moves`, `views.SENDER`, `views.JUDGE`.
- Produces: `handover.moves_it(row, profile, actor) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger_handover.py` (reuse the file's imports; add `delivery` to the
`from flotilla.ledger import` list if absent):

```python
def test_the_sender_records_a_wait_on_the_row_it_must_move(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "direct"}})
    drive(root, ledger)
    delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x")
    row = handover.wait(ledger, actor(ledger, "sender 1"), "feat/x", on="the person", why="asked about the batch")
    assert row.waiting_on == "the person"


def test_a_session_that_does_not_move_the_row_cannot_record_its_wait(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "direct"}})
    drive(root, ledger)
    with pytest.raises(MoveRefused, match="records a wait"):
        handover.wait(ledger, actor(ledger, "review session 2"), "feat/x", on="x", why="y")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_handover.py -k wait`
Expected: FAIL on the sender test — "only the owner … or the reader".

- [ ] **Step 3: Implement**

In `flotilla/ledger/handover.py` add `from flotilla.ledger import views` (with the other ledger imports) and:

```python
POST_OF_MOVER = {views.SENDER: "sender", views.JUDGE: "judge"}


def moves_it(row: Row, profile: dict, actor: Actor) -> bool:
    """Whether `actor` is the row's mover: named, or a session of the post a post-named mover stands for."""
    mover = views.who_moves(row, profile)
    post = actor.post.name if actor.post is not None else ""
    return bool(mover) and (mover == actor.name or POST_OF_MOVER.get(mover) == post)
```

and in `wait` replace the owner/reader check with:

```python
        if actor.name not in (row.owner, row.reader) and not moves_it(row, ledger.profile, actor):
            raise MoveRefused(f"only the owner ({row.owner}), the reader, or the session whose move it is records a "
                              f"wait on `{branch}`")
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_handover.py tests/test_ledger_steering.py`
Expected: all passed.

- [ ] **Step 5: Injection** — make `moves_it` return `False` always → red
`test_the_sender_records_a_wait_on_the_row_it_must_move`. Roll back, `cmp`.

- [ ] **Step 6: Commit**

```bash
git add flotilla/ledger/handover.py tests/test_ledger_handover.py
git commit -m "fix(ledger): the session whose move it is records its own wait (F24)"
```

---

### Task 3: A tip already on trunk has nothing to hand over

**Files:**
- Modify: `flotilla/ledger/handover.py` (`hand`), `flotilla/ledger/commands.py` (`_finished`)
- Test: `tests/test_ledger_handover.py`, `tests/test_ledger_cli.py`

**Interfaces:**
- Produces: `handover.has_own_commits(ledger, tip) -> bool | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger_handover.py`:

```python
def test_a_branch_with_no_commits_of_its_own_is_not_handed_over(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    git(root, "branch", "fix/empty", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/empty")
    with pytest.raises(MoveRefused, match="no commits of its own.*--settled-by"):
        handover.hand(ledger, actor(ledger, "main session 1"), "fix/empty")
```

Append to `tests/test_ledger_cli.py`:

```python
def test_an_empty_branch_is_never_called_finished(tmp_path):
    from flotilla.ledger import commands, core, receipts
    from ledgerkit import actor, git, make_ledger, repo_with_origin
    root = repo_with_origin(tmp_path)
    profile = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "direct"},
               "tests": {"tier": [{"name": "t", "command": "true", "required_for": ["handover"]}]}}
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    git(root, "branch", "fix/empty", "main")
    row = core.claim(ledger, actor(ledger, "main session 1"), "fix/empty")
    receipts.run_receipt(root, state=tmp_path / "state", repo_key=ledger.repo_key, purpose="handover",
                         profile=profile, timeout=60)
    assert commands._finished(ledger, row) is False
```

Import `core` and `git` in `tests/test_ledger_handover.py` if absent.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_handover.py tests/test_ledger_cli.py -k "own or finished"`
Expected: FAIL on both.

- [ ] **Step 3: Implement**

In `flotilla/ledger/handover.py`:

```python
def has_own_commits(ledger: Ledger, tip: str) -> bool | None:
    """Whether `tip` carries anything trunk does not: a tip already on trunk has nothing to hand over."""
    trunk_head = gitq.resolve(ledger.root, gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run), run=ledger.run)
    if trunk_head is None:
        return None
    on_trunk = gitq.is_ancestor(ledger.root, tip, trunk_head, run=ledger.run)
    return None if on_trunk is None else not on_trunk
```

In `hand`, after `current = _current_tip(ledger, branch, tip)`:

```python
    if has_own_commits(ledger, current) is False:
        raise MoveRefused(f"`{branch}` has no commits of its own: its tip {current[:7]} is already on trunk. If "
                          "another row delivered what it was for, close it with `flotilla work release "
                          f"{branch} --settled-by <that branch>`")
```

In `flotilla/ledger/commands.py` `_finished`, after resolving `tip`:

```python
    if handover.has_own_commits(ledger, tip) is not True:
        return False
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_handover.py tests/test_ledger_cli.py tests/test_ledger_views.py`
Expected: all passed.

- [ ] **Step 5: Injection** — in `_finished`, delete the two new lines → red
`test_an_empty_branch_is_never_called_finished`. Roll back, `cmp`.

- [ ] **Step 6: Commit**

```bash
git add flotilla/ledger/handover.py flotilla/ledger/commands.py tests/test_ledger_handover.py tests/test_ledger_cli.py
git commit -m "fix(ledger): a branch with no commits of its own is neither handed over nor called finished (F18)"
```

---

### Task 4: `release --settled-by`

**Files:**
- Modify: `flotilla/ledger/core.py` (`release`), `flotilla/ledger/commands.py` (`MOVES["release"]`),
  `flotilla/cli.py` (the `release` parser)
- Test: `tests/test_ledger_core.py`

**Interfaces:**
- Produces: `core.release(ledger, actor, branch, *, why="", settled_by="") -> Row`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger_core.py` (reuse its imports; add `shipped_direct`, `git`, `PROFILE` from `ledgerkit`
as needed):

```python
DIRECT_FLOW = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "direct"}}


def test_a_row_another_row_fulfilled_is_settled_by_it(tmp_path):
    from ledgerkit import git, repo_with_origin, shipped_direct
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, **DIRECT_FLOW})
    done = shipped_direct(root, ledger, "feat/x")
    git(root, "branch", "fix/y", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/y")
    row = core.release(ledger, actor(ledger, "main session 1"), "fix/y", settled_by="feat/x")
    assert row.state == "released"
    evidence = row.history[-1]["evidence"]
    assert evidence["settled_by"] == done.id and "settled by feat/x" in evidence["why"]


def test_settled_by_an_undelivered_row_is_refused(tmp_path):
    from ledgerkit import drive, git, repo_with_origin
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, **DIRECT_FLOW})
    drive(root, ledger, "feat/x", to="claimed")
    git(root, "branch", "fix/y", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/y")
    with pytest.raises(MoveRefused, match="not delivered"):
        core.release(ledger, actor(ledger, "main session 1"), "fix/y", settled_by="feat/x")


def test_a_release_names_a_reason_or_the_row_that_settled_it(tmp_path):
    from ledgerkit import git, repo_with_origin
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    git(root, "branch", "fix/y", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/y")
    with pytest.raises(MoveRefused, match="--why.*--settled-by"):
        core.release(ledger, actor(ledger, "main session 1"), "fix/y")
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_core.py -k "settled or reason"`
Expected: FAIL — `release() got an unexpected keyword argument 'settled_by'` / missing `why`.

- [ ] **Step 3: Implement**

In `flotilla/ledger/core.py` replace `release` with (add `delivered` to the `flotilla.ledger.model` import):

```python
def release(ledger: Ledger, actor: Actor, branch: str, *, why: str = "", settled_by: str = "") -> Row:
    """Say the work will not happen — or, with `settled_by`, that another delivered row fulfilled it."""
    require_may(actor, "release", ledger.posts)
    if not why.strip() and not settled_by.strip():
        raise MoveRefused("a release says why the work will not happen (--why), or names the delivered row that "
                          "fulfilled it (--settled-by <branch>)")
    current = next((row for row in reversed(list(ledger.rows().values())) if row.branch == branch and row.is_open), None)
    if current is not None and current.owner != actor.name and current.owner in ledger.live_names():
        raise MoveRefused(f"{current.owner} is alive; only they release their own work")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "release")
        if row.owner != actor.name and (current is None or row.id != current.id):
            raise MoveRefused(f"`{branch}` changed while this move was checked; run it again")
        evidence = {}
        if settled_by.strip():
            wanted = settled_by.strip()
            other = s.rows.get(wanted) or next((r for r in reversed(list(s.rows.values())) if r.branch == wanted),
                                                None)
            if other is None:
                raise MoveRefused(f"no ledger row `{wanted}` to settle `{branch}` by")
            if not delivered(other, ledger.profile):
                raise MoveRefused(f"`{other.branch or other.id}` is {other.state}, not delivered; a row is settled "
                                  "by delivered work only — wait for it, or release with --why")
            evidence["settled_by"] = other.id
            why = why.strip() or f"settled by {other.branch or other.id}"
        evidence["why"] = why.strip()
        return s.append(actor, row.id, "release", state, evidence=evidence)
```

In `flotilla/ledger/commands.py`, `MOVES["release"]` becomes
`lambda l, a, x: core.release(l, a, x.branch, why=x.why, settled_by=x.settled_by)`.

In `flotilla/cli.py`, replace `move_parser("release", "say the work will not happen").add_argument("--why",
required=True)` with:

```python
    release = move_parser("release", "say the work will not happen, or name the delivered row that fulfilled it")
    release.add_argument("--why", default="")
    release.add_argument("--settled-by", dest="settled_by", default="", metavar="BRANCH")
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_core.py tests/test_ledger_cli.py`
Expected: all passed.

- [ ] **Step 5: Injection** — delete the `if not delivered(...)` block → red
`test_settled_by_an_undelivered_row_is_refused`. Roll back, `cmp`.

- [ ] **Step 6: Commit**

```bash
git add flotilla/ledger/core.py flotilla/ledger/commands.py flotilla/cli.py tests/test_ledger_core.py
git commit -m "feat(ledger): release --settled-by closes a row another delivered row fulfilled (F26)"
```

---

### Task 5: The judge waits for the rows that build on a row

**Files:**
- Modify: `flotilla/ledger/views.py` (`pending_dependents`, `who_moves`), `flotilla/ledger/judging.py` (`walked`,
  `broke`), `flotilla/ledger/steering.py` (`walkable`), `flotilla/ledger/model.py` (`ROW_FIELDS` gains `walkable`),
  `flotilla/ledger/transitions.py` (`ANNOTATIONS` gains `walkable`), `flotilla/ledger/commands.py` (`MOVES`,
  status suffix), `flotilla/cli.py` (`work walkable`), `flotilla/watch/whose.py`, `flotilla/watch/fleet.py`,
  `templates/posts/orchestrator.md` (`may` gains `walkable`, `template_version: 3`), `docs/events/schema.json`
- Test: `tests/test_ledger_judging.py`, `tests/test_ledger_views.py`

**Interfaces:**
- Produces: `views.pending_dependents(rows, row, profile) -> list[Row]`; `views.who_moves(row, profile, rows=None)`;
  `steering.walkable(ledger, actor, branch, *, why) -> Row`; `whose.holds_move(row, profile, name, post="",
  rows=None)`; `fleet.movers(row, profile, live, post_of, rows=None)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ledger_judging.py`:

```python
JUDGED = {**DIRECT, "judge": {"required": True}}


def part_and_whole(tmp_path):
    from flotilla.ledger import core
    from ledgerkit import branch
    root, ledger, part = world(tmp_path, JUDGED)
    branch(root, "feat/whole", "builds on the part")
    core.claim(ledger, actor(ledger, OWNER), "feat/whole", requires=[part.id])
    return root, ledger, part


def test_a_part_is_not_walked_while_the_row_building_on_it_is_not_shipped(tmp_path):
    root, ledger, part = part_and_whole(tmp_path)
    with pytest.raises(MoveRefused, match="feat/whole.*walkable"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="w")
    with pytest.raises(MoveRefused, match="feat/whole"):
        judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="start", saw="no entry point")


def test_the_judge_holds_no_move_on_a_part_yet(tmp_path):
    from flotilla.ledger import views
    root, ledger, part = part_and_whole(tmp_path)
    rows = ledger.rows()
    shipped = next(row for row in rows.values() if row.branch == "feat/x")
    assert views.who_moves(shipped, JUDGED, rows) == ""
    assert views.who_moves(shipped, JUDGED) == views.JUDGE   # without rows, as before


def test_the_orchestrator_marks_a_part_walkable_on_its_own(tmp_path):
    from flotilla.ledger import steering
    root, ledger, part = part_and_whole(tmp_path)
    steering.walkable(ledger, actor(ledger, "orchestrator 1"), "feat/x", why="the logic has its own CLI")
    walked = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="w")
    assert walked.state == "walked"


def test_a_lone_shipped_row_is_the_judges_to_walk(tmp_path):
    from flotilla.ledger import views
    root, ledger, row = world(tmp_path, JUDGED)
    rows = ledger.rows()
    assert views.who_moves(rows[row.id], JUDGED, rows) == views.JUDGE


def test_a_row_nothing_requires_is_walked_as_before(tmp_path):
    root, ledger, row = world(tmp_path, JUDGED)
    assert judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="w").state == "walked"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_judging.py -k "part or walkable or nothing"`
Expected: FAIL — walks are recorded; `who_moves` takes no `rows`; no `steering.walkable`.

- [ ] **Step 3: Implement**

`flotilla/ledger/model.py`: add `"walkable"` to `ROW_FIELDS` and `walkable: str = ""` to `Row` (after
`last_run`).

`flotilla/ledger/transitions.py`: `ANNOTATIONS = ("wait", "hold", "unhold", "adopt", "urgent", "run", "walkable")`.

`flotilla/ledger/views.py`:

```python
def pending_dependents(rows: dict[str, Row], row: Row, profile: dict) -> list[Row]:
    """Open rows that require this one and are not delivered: the path this row is part of is not whole yet."""
    return [other for other in rows.values()
            if other.is_open and row.id in (other.requires or []) and not delivered(other, profile)]
```

and in `who_moves`, give it `rows: dict | None = None` and change the `shipped` branch to:

```python
    if row.state == "shipped":
        if (profile.get("judge") or {}).get("required"):
            if rows is not None and not row.walkable and pending_dependents(rows, row, profile):
                return ""   # a part: the judge walks the path once the rows building on it ship
            return JUDGE
        return row.owner
```

In `deviations`, call `who_moves(row, profile, rows)`.

`flotilla/ledger/judging.py`: add a helper and call it first inside the session of both `walked` and `broke` (right
after `row = s.need_open_row(branch)`):

```python
def _whole_or_walkable(ledger: Ledger, rows: dict[str, Row], row: Row) -> None:
    waiting = [] if row.walkable else views.pending_dependents(rows, row, ledger.profile)
    if waiting:
        names = ", ".join(f"`{other.branch or other.id}` ({other.state})" for other in waiting)
        raise MoveRefused(f"`{row.branch}` is a part: {names} build on it and are not shipped yet. Walk the path "
                          "once they ship; the orchestrator may mark this row walkable on its own "
                          f"(`flotilla work walkable {row.branch} --why \"<why>\"`)")
```

(`from flotilla.ledger import core, gitq, views` at the top.)

`flotilla/ledger/steering.py`:

```python
def walkable(ledger: Ledger, actor: Actor, branch: str, *, why: str) -> Row:
    """The orchestrator's word that a row which others build on reaches a person on its own."""
    require_may(actor, "walkable", ledger.posts)
    if not why.strip():
        raise MoveRefused("say why this part reaches a person on its own (--why)")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "walkable")
        return s.append(actor, row.id, "walkable", state, fields={"walkable": why.strip()})
```

`flotilla/ledger/commands.py`: `MOVES["walkable"] = lambda l, a, x: steering.walkable(l, a, x.branch, why=x.why)`;
in `_status`'s whose-move loop, compute `mover = views.who_moves(row, ledger.profile, rows) or "nobody named"` and,
when the row is `shipped`, the judge is required and `views.pending_dependents(rows, row, ledger.profile)` is not
empty, append `f" (the judge walks it after {', '.join(o.branch for o in pending)} ship)"`.

`flotilla/cli.py`: `move_parser("walkable", "mark a part walkable on its own (orchestrator)").add_argument("--why",
required=True)`.

`flotilla/watch/whose.py`: `holds_move(row, profile, name, post="", rows=None)` calls `views.who_moves(row, profile,
rows)`; `mine` passes `rows`. `flotilla/watch/fleet.py`: `movers(row, profile, live, post_of, rows=None)` calls
`views.who_moves(row, profile, rows)`; `fleet` passes `rows` to `movers` and uses `views.who_moves(row, profile,
rows)` for `mover`; `open_breaks` passes `rows` to `holds_move`.

`templates/posts/orchestrator.md`: add `walkable` to `may`, set `template_version: 3`, and add the bullet:
"- A row that other rows build on is a part: the judge walks it once they ship. When a part reaches a person on its
  own, say so: `flotilla work walkable <branch> --why \"<why>\"`."

Regenerate the event contract: `python3 scripts/flotilla events schema > docs/events/schema.json` (with
`FLOTILLA_STATE_DIR` pointing anywhere writable).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_judging.py tests/test_ledger_views.py tests/test_ledger_events.py tests/test_watch_whose.py tests/test_watch_fleet.py tests/test_posts.py tests/test_skills.py`
Expected: all passed.

- [ ] **Step 5: Injections**

1. Delete the `_whole_or_walkable` call in `walked` → red
   `test_a_part_is_not_walked_while_the_row_building_on_it_is_not_shipped`.
2. In `_whole_or_walkable`, drop `[] if row.walkable else` → red
   `test_the_orchestrator_marks_a_part_walkable_on_its_own`.
3. In `who_moves`, drop `and pending_dependents(rows, row, profile)` → red
   `test_a_lone_shipped_row_is_the_judges_to_walk`.

Each rolled back by file copy and checked with `cmp`.

- [ ] **Step 6: Commit**

```bash
git add flotilla/ledger flotilla/watch flotilla/cli.py templates/posts/orchestrator.md docs/events/schema.json \
        tests/test_ledger_judging.py
git commit -m "feat(ledger): the judge walks a path, not a part; the orchestrator may mark a part walkable (F21)"
```

---

### Task 6: The sender's and the judge's sequences, the documents, the whole suite

**Files:**
- Modify: `templates/posts/sender.md` (step 6, `template_version: 2`), `templates/posts/judge.md` (a bullet,
  `template_version: 2`), `docs/specs/2026-09-22-decisions-log.md`, `docs/field-tests/2026-09-27-snake-fleet.md`
  (a status line), `tests/test_skills.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_skills.py`:

```python
def test_the_sender_post_carries_the_direct_push_sequence():
    from flotilla.posts import TEMPLATE_DIR
    text = (TEMPLATE_DIR / "sender.md").read_text(encoding="utf-8")
    assert "push HEAD:<trunk>" in text and "land <branch> --merge" in text and "--settled-by" in text
```

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_skills.py`
Expected: FAIL on the new test.

- [ ] **Step 2: Write the templates and documents**

`templates/posts/sender.md`: set `template_version: 2`; replace step 6 with:

```markdown
6. The moves, in order: `flotilla work queue <branch>` (with `--pr <number>` in a PR project). In a direct-push
   project never touch the main checkout: in your own tree, on a branch from `origin/<trunk>`, merge the branch,
   run `flotilla receipt run --purpose push`, push `HEAD:<trunk>`, then `flotilla work land <branch> --merge
   <that commit>` and `flotilla work ship <branch>`. After every push, and whenever CI settles,
   `flotilla work reconcile` asks the PR or origin about every queued or landed row and records what shipped.
   A fix row whose purpose another delivered row fulfilled closes with `flotilla work release <branch>
   --settled-by <that branch>`, never with `offledger`.
```

`templates/posts/judge.md`: set `template_version: 2`; add the bullet:

```markdown
- Walk a path, not a part. A row that other rows build on is walked once they ship: `walked` and `broke` refuse
  it until then, unless the orchestrator marked it walkable on its own. A refusal of that kind is not a break.
```

`docs/specs/2026-09-22-decisions-log.md`: append decisions 81–86, one per numbered item of this plan's Decisions
section, each ending `(field test 2026-09-27; the person's triage 2026-09-28)` for 1 and 6 and `(executor's decision, field
fixes part 1, 2026-09-28)` for the rest, before `## Open questions`, no second number.

`docs/field-tests/2026-09-27-snake-fleet.md`: append a line under the triage: "Part 1 (the ledger) fixes F14, F15,
F18, F21, F24, F26 — plan `docs/plans/2026-09-28-field-fixes-ledger.md`."

- [ ] **Step 3: Run the whole suite, three ways, and the checks**

```bash
uv run --with pytest python -m pytest -p no:cacheprovider tests/ 2>&1 | tail -3
uv run --python 3.11 --with pytest python -m pytest -p no:cacheprovider tests/ 2>&1 | tail -3
GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest python -m pytest -p no:cacheprovider tests/ 2>&1 | tail -3
python3 tools/check_no_cyrillic.py
claude plugin validate .
```

Expected: the same `N passed` line three times, no Cyrillic, the plugin valid. If `tests/test_posts.py` pins a
template version that changed, update it.

- [ ] **Step 4: Commit**

```bash
git add templates/posts/sender.md templates/posts/judge.md tests/test_skills.py \
        docs/specs/2026-09-22-decisions-log.md docs/field-tests/2026-09-27-snake-fleet.md
git commit -m "docs(fleet): the sender's direct-push sequence and the judge's path rule in the posts"
```
