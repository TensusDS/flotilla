# Field Fixes, Part 2: Who Talks to the Person, and Who Wakes the Next Mover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the two stalls the snake fleet hit outside the ledger: a background session that asks the person a
question nobody can see (and hangs), and a move that passes to an idle session nobody wakes.

**Architecture:** Five small changes. (1) Every ledger refusal printed by the command line ends with one fixed
line naming the way forward. (2) A new module `flotilla/ledger/letters.py` compares whose move every row was before
and after a command, and the command line prints a letter for each session a row passed to, which the session that
made the move sends (SendMessage). (3) `flotilla watch --wait <seconds>` blocks until something new needs the
orchestrator, so one background command wakes it for questions, dropped balls and breaks. (4) A new hook event
`ask` (PreToolUse, matcher `AskUserQuestion`) refuses the question in a background session whose post is not the
orchestrator's, naming the route. (5) The post templates and the arrangement skill say it: only the orchestrator
talks to the person; a task routed by the orchestrator is a work order; a printed letter is sent.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`.

**Spec:** the field test `docs/field-tests/2026-09-27-snake-fleet.md` — findings F12, F16, F17, F23 and the triage
with Max of 2026-09-28, groups 2 and 3; `docs/specs/2026-09-22-flotilla-design.md` sections 6.9 and 8.

## Decisions (Max's, 2026-09-28, and the executor's)

1. **Only the orchestrator talks to the person** (Max). Every other post sends its question to the orchestrator
   (SendMessage) and records `flotilla work wait <branch> --on "the person" --why "<the question>"` on the row it
   holds up. The sender's batch goes to the orchestrator too, and the orchestrator relays the person's yes.
2. **A guard on AskUserQuestion** (Max): a PreToolUse hook, matcher `AskUserQuestion`, denies the question in a
   background session whose post is not `orchestrator`, naming the live orchestrators and the wait to record. An
   interactive session, a session with no post, and the orchestrator ask freely. When the census or the ledger
   cannot be asked, the question goes through with a note saying so: a guard that cannot ask never traps a session.
3. **Posts accept a task routed by the orchestrator** (Max), after checking that `flotilla fleet` lists the sender
   of the message as the live session holding the orchestrator post. Any other peer's message stays information,
   never a work order.
4. **Every ledger refusal names the next legal move** (Max). The command line ends every `refused:` with one fixed
   line: record whom you wait on and tell the orchestrator; a refusal that names no way forward is a flotilla
   defect, sent to the orchestrator verbatim; never work around flotilla with git plumbing. (executor: one line at
   the one print site, rather than editing every refusal text, so no future refusal can miss it.)
5. **A move that passes a row to another session prints the letter; the post obliges sending it** (Max). The
   command line compares, for every row, whose move it was before the command and after it. A row whose mover
   changed to someone other than the caller gets a letter, addressed from the census, or by post when the census
   cannot be asked. The letter is printed, never sent by flotilla: `claude` has no command line to message a
   session, and flotilla never drives a session. (executor: comparing every row, not only the moved one, also
   covers a move on one row that frees another — a part the judge may walk once the row building on it ships.)
6. **The orchestrator keeps one background `flotilla watch --wait 3600`** (Max). It returns when an attention item
   appears that was not there when it started — a permission question, a dropped ball, a break — so the orchestrator
   wakes for all of them from one command. Items present at the start do not wake it. (executor: this replaces the
   orchestrator's `permit next --wait 3600`, which watched questions only; `permit next --wait` stays for a person.)

## Global Constraints

- flotilla is English only: code, comments, output, templates, docs (`tools/check_no_cyrillic.py`).
- Linux and macOS; Python 3.11+ standard library only.
- A hook never crashes or traps a session: what it cannot check it says, and it decides only on what it could ask.
- flotilla never parses transcripts and never drives a session.
- Every `flotilla <cmd>` a post template names must exist (`tests/test_ledger_cli.py::
  test_every_cli_call_in_the_post_templates_exists`).
- Tests run three ways before a task is done: default, `--python 3.11`, and `GIT_CONFIG_GLOBAL=/dev/null`; plus
  `python3 tools/check_no_cyrillic.py` and `claude plugin validate .`.
- The suite command: `uv run --with pytest python -m pytest -p no:cacheprovider tests/` — without `-q`
  (`pyproject.toml` already sets it; a second `-q` hides the summary line). Read the `N passed` line, never the
  exit code of a pipeline.

## Review Focus

1. **The census cannot be asked in the ask hook** → the question goes through with a note, never a blind deny
   (Task 4, `test_ask_goes_through_with_a_note_when_the_census_is_down`).
2. **An interactive session that holds a post asks the person** → allowed: a person is in front of it (Task 4,
   `test_an_interactive_session_asks_freely`).
3. **The caller's own next move** (the sender queues, then lands) → no letter to itself or to other sessions of
   its post (Task 2, `test_no_letter_when_the_move_stays_with_the_callers_post`).
4. **The census is off or down when a letter is due** → the move still succeeds and the letter is addressed by
   post (Task 2, `test_a_letter_is_addressed_by_post_when_the_census_is_unknown`).
5. **`watch --wait` started while attention already exists, or the census fails mid-wait** → it does not return at
   once for what was already there, and a failed census is exit 2, never "nothing new" (Task 3,
   `test_what_was_there_at_the_start_does_not_wake_it`, `test_a_census_lost_mid_wait_exits_2`).

---

### Task 1: Every ledger refusal names a way forward

**Files:**
- Modify: `flotilla/ledger/commands.py` (the `refused:` print in `run_ledger_command`, around line 391)
- Test: `tests/test_ledger_cli.py`

**Interfaces:**
- Produces: `commands.STUCK: str` — the fixed line printed after every `refused:`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_ledger_cli.py`)

```python
def test_every_refusal_names_a_way_forward(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", "HEAD", "--root", str(root),
                        "--as", "main session 1")
    last = out.rstrip().splitlines()[-1]
    assert code == 2 and last.startswith("next: ")
    assert "work wait" in last and "tell the orchestrator" in last and "git plumbing" in last


def test_the_way_forward_is_one_fixed_line():
    from flotilla.ledger import commands
    assert commands.STUCK.startswith("next: ")
```

- [ ] **Step 2: Run it to see it fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_cli.py -k "way_forward"`
Expected: FAIL — `AttributeError: module 'flotilla.ledger.commands' has no attribute 'STUCK'` and the last line is
`refused: ...`.

- [ ] **Step 3: Implement**

In `flotilla/ledger/commands.py`, next to `MOVES`:

```python
#: Printed after every refusal, so no refusal leaves a session without a way forward (field test F17).
STUCK = ("next: if no move named above is yours to make, record whom you wait on (`flotilla work wait <branch> "
         "--on \"<whom>\" --why \"<why>\"`) and tell the orchestrator. A refusal that names no way forward is a "
         "flotilla defect: send it to the orchestrator verbatim, and never work around flotilla with git plumbing.")
```

and in `run_ledger_command`, the refusal branch becomes:

```python
        print(f"refused: {err}")
        if ledger is not None:
            _notices(ledger)
        print(STUCK)
        return 2
```

- [ ] **Step 4: Run the tests and the full suite**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_cli.py` → all pass.
Run the full suite (Global Constraints) → `N passed`, no failures. If a test asserted that `refused:` is the last
line, change it to look for the refusal line anywhere in the output.

- [ ] **Step 5: Commit**

```bash
git add flotilla/ledger/commands.py tests/test_ledger_cli.py
git commit -m "feat(ledger): every refusal ends with the way forward

A refusal that named only a state left the sender improvising git plumbing and asking the person (F17). The
command line now ends every refusal with one line: record the wait and tell the orchestrator; a refusal with no
way forward is a flotilla defect."
```

---

### Task 2: A move that passes a row to another session prints its letter

**Files:**
- Create: `flotilla/ledger/letters.py`
- Modify: `flotilla/ledger/commands.py` (`MOVES["assign"]`, `run_ledger_command`: read the rows before the move,
  print letters after it; the reconcile branch too)
- Modify: `skills/flotilla/SKILL.md` (a printed letter is sent)
- Test: create `tests/test_ledger_letters.py`; modify `tests/test_ledger_cli.py`, `tests/test_skills.py`

**Interfaces:**
- Consumes: `views.who_moves(row, profile, rows)`, `views.POST_OF_MOVER`, `reading.letter(row)`,
  `flotilla.posts.post_for_session(posts, name)`.
- Produces:
  - `letters.Letter` — frozen dataclass `(mover: str, to: tuple[str, ...], branch: str, text: str)`;
  - `letters.changed(before: dict[str, Row], after: dict[str, Row], profile: dict, posts: dict, caller: str,
    live: Callable[[], set[str] | None]) -> list[Letter]` — `live` is called at most once, and only when a letter
    is due; it returns `None` when the census cannot be asked;
  - `letters.render(letter: Letter) -> list[str]`.

- [ ] **Step 1: Write the failing tests** (`tests/test_ledger_letters.py`)

```python
from flotilla.ledger import letters
from watchkit import posts, row, rows

DIRECT = {"flow": {"mode": "direct"}, "review": {"depth": "every"}}
JUDGED = {**DIRECT, "judge": {"required": True}}


def live(*names):
    return lambda: set(names)


def never():
    raise AssertionError("the census was asked although no letter was due")


def test_an_accepted_row_writes_to_the_live_sender(tmp_path):
    before = rows(row(state="handed", reader="review session 1", tip="abc1234def"))
    after = rows(row(state="accepted", reader="review session 1", verdict="abc1234def"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "review session 1",
                            live("sender 1", "main session 1", "review session 1"))
    assert [(item.mover, item.to) for item in found] == [("the sender", ("sender 1",))]
    assert "accepted by its reader" in found[0].text and "flotilla work show feat/x" in found[0].text


def test_no_letter_when_the_move_stays_with_the_callers_post(tmp_path):
    before = rows(row(state="accepted", reader="review session 1"))
    after = rows(row(state="queued", reader="review session 1"))
    assert letters.changed(before, after, DIRECT, posts(tmp_path), "sender 1", never) == []


def test_no_letter_when_nobody_new_holds_the_move(tmp_path):
    handed = row(state="handed", reader="review session 1", tip="abc1234def")
    waited = row(state="handed", reader="review session 1", tip="abc1234def", waiting_on="the person")
    assert letters.changed(rows(handed), rows(waited), DIRECT, posts(tmp_path), "main session 1", never) == []


def test_a_letter_is_addressed_by_post_when_the_census_is_unknown(tmp_path):
    before = rows(row(state="handed", reader="review session 1"))
    after = rows(row(state="accepted", reader="review session 1"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "review session 1", lambda: None)
    assert found[0].to == ("the session holding the sender post",)


def test_nobody_alive_to_move_it_says_so(tmp_path):
    before = rows(row(state="handed", reader="review session 1"))
    after = rows(row(state="accepted", reader="review session 1"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "review session 1", live("main session 1"))
    assert found[0].to == ()
    rendered = "\n".join(letters.render(found[0]))
    assert "no live session" in rendered and "tell the orchestrator" in rendered


def test_a_handed_row_carries_the_readers_letter(tmp_path):
    before = rows(row(state="handed", reader="", tip="abc1234def"))
    after = rows(row(state="handed", reader="review session 1", tip="abc1234def"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "orchestrator 1", live("review session 1"))
    assert found[0].to == ("review session 1",) and "flotilla work take feat/x" in found[0].text


def test_a_return_names_what_must_change(tmp_path):
    before = rows(row(state="handed", reader="review session 1"))
    after = rows(row(state="fixing", reader="review session 1", why="the score never resets"))
    found = letters.changed(before, after, DIRECT, posts(tmp_path), "review session 1", live("main session 1"))
    assert found[0].to == ("main session 1",) and "the score never resets" in found[0].text


def test_a_move_on_one_row_that_frees_another_writes_for_both(tmp_path):
    part = row(id="r1", branch="feat/logic", state="shipped")
    whole_queued = row(id="r2", branch="feat/ui", state="queued", requires=["r1"])
    whole_shipped = row(id="r2", branch="feat/ui", state="shipped", requires=["r1"])
    found = letters.changed(rows(part, whole_queued), rows(part, whole_shipped), JUDGED, posts(tmp_path),
                            "sender 1", live("acceptance judge 1", "sender 1"))
    assert sorted(item.branch for item in found) == ["feat/logic", "feat/ui"]
    assert {item.to for item in found} == {("acceptance judge 1",)}


def test_the_person_who_merges_a_pr_gets_no_letter(tmp_path):
    human = {"flow": {"mode": "pr"}, "pr": {"merged_by": "human"}, "review": {"depth": "every"}}
    before = rows(row(state="accepted", reader="review session 1"))
    after = rows(row(state="queued", reader="review session 1", pr="12"))
    assert letters.changed(before, after, human, posts(tmp_path), "sender 1", never) == []


def test_render_names_the_recipients_and_how_to_send():
    found = letters.Letter(mover="the sender", to=("sender 1",), branch="feat/x", text="line one\nline two")
    rendered = letters.render(found)
    assert rendered[0].startswith("letter for sender 1") and "SendMessage" in rendered[0]
    assert rendered[1:] == ["  line one", "  line two"]
```

Before writing the tests, check `tests/watchkit.py::row` accepts the fields used (`reader`, `tip`, `verdict`,
`waiting_on`, `why`, `requires`, `pr`): they are `Row` fields; if one is named differently in
`flotilla/ledger/model.py`, use the model's name in the test.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_letters.py`
Expected: FAIL — `ImportError: cannot import name 'letters'`.

- [ ] **Step 3: Implement `flotilla/ledger/letters.py`**

```python
"""The letters a command owes the sessions it passed a move to (field test F23).

An idle background session is woken only by a message, and `claude` has no command line to send one, so flotilla
never sends it: the command line prints the letter and the session that made the move sends it (SendMessage). The
comparison covers every row, because a move on one row can pass another one on (a part the judge may walk once the
row building on it ships). A letter is addressed from the census, or by post when the census cannot be asked:
liveness unknown never swallows a letter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flotilla.ledger import views
from flotilla.ledger.model import Row
from flotilla.posts import PostError, post_for_session

WHAT = {"reserved": "reserved for you", "claimed": "yours to build", "fixing": "returned to you for fixes",
        "accepted": "accepted by its reader", "queued": "queued", "landed": "landed", "shipped": "shipped",
        "walked": "walked on the live build"}


@dataclass(frozen=True)
class Letter:
    mover: str
    to: tuple[str, ...]
    branch: str
    text: str


def _post_of(posts: dict, name: str) -> str:
    try:
        found = post_for_session(posts, name)
    except PostError:
        return ""
    return found.name if found is not None else ""


def _body(row: Row) -> str:
    if row.state == "handed":
        from flotilla.ledger.reading import letter
        return letter(row)
    said = f"`{row.branch}` (row {row.id}) is {WHAT.get(row.state, row.state)}; the next move is yours."
    if row.state == "fixing" and row.why:
        said += f" What must change: {row.why}"
    return said + f" `flotilla work show {row.branch}` has the rest."


def changed(before: dict[str, Row], after: dict[str, Row], profile: dict, posts: dict, caller: str,
            live: Callable[[], set[str] | None]) -> list[Letter]:
    """A letter for every row whose mover changed to a session other than the caller's."""
    mine = _post_of(posts, caller)
    due = []
    for row_id, row in after.items():
        mover = views.who_moves(row, profile, after)
        if not mover or mover.startswith("the person"):
            continue
        old = before.get(row_id)
        if old is not None and views.who_moves(old, profile, before) == mover:
            continue
        if mover == caller or (mine and views.POST_OF_MOVER.get(mover) == mine):
            continue
        due.append((mover, row))
    if not due:
        return []
    names = live()
    found = []
    for mover, row in due:
        post = views.POST_OF_MOVER.get(mover)
        if post is None:
            to = (mover,) if names is None or mover in names else ()
        elif names is None:
            to = (f"the session holding the {post} post",)
        else:
            to = tuple(sorted(name for name in names if _post_of(posts, name) == post))
        found.append(Letter(mover=mover, to=to, branch=row.branch, text=_body(row)))
    return found


def render(letter: Letter) -> list[str]:
    if not letter.to:
        return [f"note: `{letter.branch}` is now {letter.mover}'s move and no live session can make it; "
                "tell the orchestrator"]
    return [f"letter for {', '.join(letter.to)} - send it with SendMessage; an idle background session is woken "
            "only by a message:", *(f"  {line}" for line in letter.text.splitlines())]
```

- [ ] **Step 4: Run the letter tests**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_letters.py` → all pass.

- [ ] **Step 5: Write the failing command-line tests** (append to `tests/test_ledger_cli.py`)

```python
def test_accept_prints_the_letter_for_the_sender(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    tip = commit(tree, "work", "work.txt")
    run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", tip, "--root", str(root),
                        "--as", "review session 1")
    assert code == 0
    assert "letter for the session holding the sender post - send it with SendMessage" in out


def test_assign_prints_one_letter_for_the_reader(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    commit(tree, "work", "work.txt")
    run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    monkeypatch.setattr("flotilla.ledger.core.Ledger.live_names", lambda self: {"review session 1"})
    code, out = run_cli("work", "assign", "feat/x", "--reader", "review session 1", "--root", str(root),
                        "--as", "orchestrator 1")
    assert code == 0 and out.count("flotilla work take feat/x") == 1
    assert "letter for review session 1" in out
```

`PLAIN` is PR mode: the accepted row's next move is the sender's. `FLOTILLA_NO_CENSUS=1` (set by `onboarded`) makes
`live_names` raise `MoveRefused`, which the command line reads as "census unknown". If `assign` needs the census
earlier than the patch point, patch before the call as written.

- [ ] **Step 6: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_cli.py -k "letter"`
Expected: FAIL — no `letter for` line; `assign` prints its letter text once without the header.

- [ ] **Step 7: Wire it into the command line**

In `flotilla/ledger/commands.py`:

1. `from flotilla.ledger import letters` beside the other ledger imports.
2. `MOVES["assign"]` returns the row only; its letter now comes from `letters`:

```python
    "assign": lambda l, a, x: reading.assign(l, a, x.branch, reader=x.reader)[0],
```

3. A helper:

```python
def _letters(ledger: core.Ledger, caller, before: dict) -> None:
    def live():
        try:
            return ledger.live_names()
        except MoveRefused:
            return None
    for letter in letters.changed(before, ledger.rows(), ledger.profile, ledger.posts, caller.name, live):
        print("\n".join(letters.render(letter)))
```

4. In `run_ledger_command`, read the rows once the caller is resolved and before any move, and print letters after
   every successful move, the reconcile branch included:

```python
        caller = resolve_actor(ledger.posts, as_name=args.as_name)
        before = ledger.rows()
        if args.command == "work" and args.move == "reconcile":
            lines = delivery.reconcile(ledger, caller)
            print("\n".join(lines) if lines else "nothing is queued or landed")
            _letters(ledger, caller, before)
            return 0
```

   In the tuple branch (`broke`), after `print(text)`: `_letters(ledger, caller, before)` before `_notices`. In the
   plain branch, after the `stacked_on` notes: `_letters(ledger, caller, before)` before `_notices`.

- [ ] **Step 8: The arrangement skill says the letter is sent**

In `skills/flotilla/SKILL.md`, section "State lives in the ledger, not in letters", after the first paragraph add:

```markdown
A move that passes work to another session prints `letter for <session> - send it with SendMessage`. The move is
not finished until you sent that letter: an idle background session is woken only by a message, and nothing else
tells it the move is now its own. A printed `note: ... no live session can make it` goes to the orchestrator.
```

Replace the orchestrator line of that section's list with:

```markdown
- orchestrator: `flotilla work assign <branch> --reader "<session>"`, then send the letter it prints;
```

Append to `tests/test_skills.py`:

```python
def test_the_arrangement_says_a_printed_letter_is_sent():
    text = (ROOT / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "letter for <session> - send it with SendMessage" in text and "not finished until you sent" in text
```

(use the module-level `ROOT` of `tests/test_skills.py`; if it is named differently there, use that name.)

- [ ] **Step 9: Run the tests and the full suite**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_ledger_letters.py tests/test_ledger_cli.py tests/test_skills.py`
→ all pass. Then the full suite → `N passed`. A test that asserted `assign`'s output without the `letter for`
header is updated to the new header, not deleted.

- [ ] **Step 10: Commit**

```bash
git add flotilla/ledger/letters.py flotilla/ledger/commands.py skills/flotilla/SKILL.md tests/test_ledger_letters.py tests/test_ledger_cli.py tests/test_skills.py
git commit -m "feat(ledger): a move that passes work on prints the letter to send

Nothing woke an idle background session when a move became its own (F23). The command line now compares whose
move every row was before and after a command and prints a letter for each session a row passed to; the session
that made the move sends it. Addressed by post when the census cannot be asked."
```

---

### Task 3: `flotilla watch --wait`, and the orchestrator keeps it running

**Files:**
- Modify: `flotilla/watch/commands.py`, `flotilla/cli.py` (the `watch` parser)
- Modify: `templates/posts/orchestrator.md` (the permission bullet; `template_version: 4`)
- Test: `tests/test_watch_cli.py`, `tests/test_skills.py`

**Interfaces:**
- Consumes: `Context.fleet()`, `watch.render.lines(items, now, limit=...)`, `whose.Item(kind, branch, text, since)`.
- Produces: `run_watch_command(args, *, gather=None, now=None, sleep=time.sleep, clock=time.monotonic) -> int`;
  `args.wait: float` (0 = off), `args.interval: float` (default 20).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_watch_cli.py`)

```python
def waiting(seconds=60.0, interval=20.0):
    return argparse.Namespace(once=False, wait=seconds, interval=interval, root=None)


def run_wait(tmp_path, capsys, contexts, seconds=60.0):
    onboarded(tmp_path)
    given = iter(contexts)
    moments = iter(range(0, 10_000, 20))
    ns = waiting(seconds)
    ns.root = str(tmp_path)
    code = run_watch_command(ns, gather=lambda root, sid: next(given), now=NOW, sleep=lambda s: None,
                             clock=lambda: float(next(moments)))
    return code, capsys.readouterr().out


QUIET = [sess("main session 1", state="working")]
DROPPED = [sess("review session 1"), sess("main session 1", state="working")]


def test_wait_returns_when_something_new_needs_attention(tmp_path, capsys):
    calm = context(tmp_path, me=None, sessions=QUIET)
    news = context(tmp_path, me=None, sessions=DROPPED, rows_=HANDED)
    code, out = run_wait(tmp_path, capsys, [calm, calm, news])
    assert code == 1 and "attention (new):" in out and "review session 1 holds the move" in out


def test_what_was_there_at_the_start_does_not_wake_it(tmp_path, capsys):
    same = context(tmp_path, me=None, sessions=DROPPED, rows_=HANDED)
    code, out = run_wait(tmp_path, capsys, [same] * 10)
    assert code == 0 and "nothing new in 60 s" in out


def test_a_census_lost_mid_wait_exits_2(tmp_path, capsys):
    calm = context(tmp_path, me=None, sessions=QUIET)
    lost = context(tmp_path, me=None, census_error="`claude` is not on PATH")
    code, out = run_wait(tmp_path, capsys, [calm, lost])
    assert code == 2 and "could not be asked" in out and "nothing new" not in out


def test_the_cli_parses_watch_wait():
    parsed = cli.build_parser().parse_args(["watch", "--wait", "3600"])
    assert parsed.wait == 3600 and parsed.interval == 20 and not parsed.once
```

Replace the existing `test_without_once_it_refuses` assertion so it also names the new flag:

```python
def test_without_once_it_refuses(tmp_path, capsys):
    code, out = run(tmp_path, context(tmp_path, me=None), capsys, once=False)
    assert code == 2 and "--once" in out and "--wait" in out
```

and give the module's `args()` helper the new fields: `argparse.Namespace(once=once, wait=0.0, interval=20.0,
root=str(tmp_path))`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_watch_cli.py`
Expected: FAIL — `run_watch_command() got an unexpected keyword argument 'sleep'`, and the parser rejects `--wait`.

- [ ] **Step 3: Implement**

`flotilla/cli.py`, the `watch` parser:

```python
    watch.add_argument("--wait", type=float, default=0.0,
                       help="block up to this many seconds until something new needs attention (exit 1), or "
                            "nothing new (exit 0)")
    watch.add_argument("--interval", type=float, default=20.0, help=argparse.SUPPRESS)
```

`flotilla/watch/commands.py` — update the module docstring's first line to name `--wait`, add `import time`, and:

```python
def run_watch_command(args, *, gather=None, now: dt.datetime | None = None, sleep=time.sleep,
                      clock=time.monotonic) -> int:
    wait = getattr(args, "wait", 0.0) or 0.0
    if not args.once and not wait:
        print("refused: flotilla has no schedule of its own; run `flotilla watch --once` from cron, launchd or a "
              "loop, or keep `flotilla watch --wait <seconds>` running in the background")
        return 2
    ...  # the root check and the first gather stay as they are, up to `if problems: ... return 2`
    if wait:
        return _wait(root, gather, ctx, wait, getattr(args, "interval", 20.0) or 20.0, now, sleep, clock)
    ...  # the --once output stays as it is


def _key(item) -> tuple:
    return (item.kind, item.branch, item.text)


def _problems(ctx) -> list[str]:
    problems = []
    if ctx.sessions is None:
        problems.append(f"census: could not be asked ({ctx.census_error})")
    if ctx.ledger is None:
        problems.append(f"ledger: could not be read ({ctx.ledger_error})")
    return problems


def _wait(root, gather, ctx, seconds, interval, now, sleep, clock) -> int:
    """Block until an attention item appears that was not there at the start. What was there does not wake it,
    so a standing item cannot turn the wait into a loop."""
    from flotilla.watch import render
    seen = {_key(item) for item in ctx.fleet()}
    deadline = clock() + seconds
    while True:
        left = deadline - clock()   # one reading per turn: the tests drive the clock one step per call
        if left <= 0:
            break
        sleep(min(interval, left))
        ctx = gather(root, "")
        problems = _problems(ctx)
        if problems:
            print("\n".join(problems))
            return 2
        new = [item for item in ctx.fleet() if _key(item) not in seen]
        if new:
            print(f"census: {len(ctx.live)} live session(s)")
            print("attention (new):")
            print("\n".join(render.lines(new, now or dt.datetime.now(dt.timezone.utc), limit=10_000)))
            return 1
    print(f"attention: nothing new in {seconds:g} s")
    return 0
```

Use `_problems(ctx)` in the `--once` path too, in place of the inline list, so both paths say the same.

- [ ] **Step 4: The orchestrator keeps one wait running**

In `templates/posts/orchestrator.md` set `template_version: 4` and replace the bullet that begins "Background
sessions cannot show a permission prompt" with:

```markdown
- Keep one `flotilla watch --wait 3600` running in the background, and start it again each time it returns. It
  returns when something new needs you: a background session's permission question (run /flotilla:permit: ask the
  person, one question at a time, oldest first, and record the answer; a question nobody answers in nine minutes is
  refused on its own), a dropped ball (message the session it names, with the letter the last move printed), a
  break, orphaned work. Exit 0 means an hour passed with nothing new.
```

In `tests/test_skills.py` replace `test_the_orchestrator_post_keeps_the_question_watch` with:

```python
def test_the_orchestrator_post_keeps_one_watch_running():
    from flotilla.posts import TEMPLATE_DIR
    text = (TEMPLATE_DIR / "orchestrator.md").read_text(encoding="utf-8")
    assert "flotilla watch --wait 3600" in text and "/flotilla:permit" in text
```

- [ ] **Step 5: Run the tests and the full suite**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_watch_cli.py tests/test_skills.py tests/test_ledger_cli.py`
→ all pass (`test_every_cli_call_in_the_post_templates_exists` checks that `flotilla watch` exists). Then the full
suite → `N passed`.

- [ ] **Step 6: Commit**

```bash
git add flotilla/watch/commands.py flotilla/cli.py templates/posts/orchestrator.md tests/test_watch_cli.py tests/test_skills.py
git commit -m "feat(watch): watch --wait wakes the orchestrator when something new needs it

An idle orchestrator heard of a dropped ball only when a person prompted it (F23). One background
\`flotilla watch --wait 3600\` now returns on a new question, dropped ball, break or orphaned row; what was there
at the start does not wake it."
```

---

### Task 4: Only the orchestrator asks the person — the AskUserQuestion guard

**Files:**
- Create: `flotilla/watch/ask.py`
- Modify: `flotilla/hooks.py` (`EVENTS`, the handler map, the stderr group of a failed hook), `hooks/hooks.json`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `Context` (`sessions`, `census_error`, `ledger`, `ledger_error`, `me`, `live`, `post(name)`,
  `post_of(name)`), `census.Session.kind` (`"background"` for a background session).
- Produces: `ask.ask_verdict(ctx, cli: str = "flotilla") -> tuple[str, str]` — `("deny", reason)`,
  `("note", context)`, or `("", "")`; hook event `ask`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_hooks.py`)

```python
def asked(tmp_path, ctx):
    said = call("ask", tmp_path, ctx, payload={"tool_name": "AskUserQuestion"})
    return json.loads(said)["hookSpecificOutput"] if said else {}


def test_a_background_producer_is_refused_and_told_the_route(tmp_path):
    me = sess("minor session 1")
    body = asked(tmp_path, context(tmp_path, me=me, sessions=[me, sess("orchestrator 1")]))
    reason = body["permissionDecisionReason"]
    assert body["permissionDecision"] == "deny"
    assert "orchestrator 1" in reason and "SendMessage" in reason and '--on "the person"' in reason


def test_with_no_orchestrator_alive_the_question_is_still_refused_and_said_how(tmp_path):
    me = sess("sender 1")
    body = asked(tmp_path, context(tmp_path, me=me, sessions=[me]))
    assert body["permissionDecision"] == "deny" and "No orchestrator is alive" in body["permissionDecisionReason"]


def test_the_orchestrator_asks_freely(tmp_path):
    me = sess("orchestrator 1")
    assert asked(tmp_path, context(tmp_path, me=me)) == {}


def test_an_interactive_session_asks_freely(tmp_path):
    me = sess("minor session 1", kind="interactive")
    assert asked(tmp_path, context(tmp_path, me=me)) == {}


def test_a_session_with_no_post_asks_freely(tmp_path):
    me = sess("somebody else")
    assert asked(tmp_path, context(tmp_path, me=me)) == {}


def test_ask_goes_through_with_a_note_when_the_census_is_down(tmp_path):
    body = asked(tmp_path, context(tmp_path, me=None, census_error="`claude` is not on PATH"))
    assert "permissionDecision" not in body and "could not ask the census" in body["additionalContext"]


def test_ask_goes_through_with_a_note_when_the_ledger_is_unreadable(tmp_path):
    me = sess("minor session 1")
    body = asked(tmp_path, context(tmp_path, me=me, ledger_error="trunk carries no .flotilla"))
    assert "permissionDecision" not in body and "could not read the ledger" in body["additionalContext"]


def test_the_ask_hook_is_declared_with_a_budget():
    declared = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    group = next(group for group in declared if group.get("matcher") == "AskUserQuestion")
    assert group["hooks"][0]["command"].endswith("hook ask") and group["hooks"][0]["timeout"] >= 3 * 3 + 2
```

and extend the mapping in `test_every_hook_event_is_declared` with `"ask": "PreToolUse"`.

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_hooks.py`
Expected: FAIL — `KeyError: 'ask'` from the handler map, and the declaration test finds no `AskUserQuestion` group.

- [ ] **Step 3: Implement `flotilla/watch/ask.py`**

```python
"""The AskUserQuestion guard: only the orchestrator talks to the person (field test F12, F16, F17).

A background session nobody is attached to that asks the person waits for ever: there is no timeout, and the
permission broker does not cover a question. So a background session holding any post but the orchestrator's is
refused, with the route to follow: the question goes to the orchestrator, and the row it holds up records the wait.
An interactive session has a person in front of it, a session with no post is not the fleet's to govern, and the
orchestrator is the post that talks to the person: all three ask freely. What the guard could not ask lets the
question through, and says so.
"""

from __future__ import annotations

PERSON_FACING = ("orchestrator",)


def ask_verdict(ctx, cli: str = "flotilla") -> tuple[str, str]:
    if ctx.sessions is None:
        return "note", (f"flotilla could not ask the census ({ctx.census_error}); if this is a background session "
                        "nobody is attached to, this question waits for ever: send it to the orchestrator instead")
    if ctx.me is None or ctx.me.kind != "background":
        return "", ""
    if ctx.ledger is None:
        return "note", (f"flotilla could not read the ledger ({ctx.ledger_error}), so your post is unknown; if you "
                        "hold a post other than the orchestrator's, send this question to the orchestrator instead")
    post = ctx.post(ctx.me.name)
    if post is None or post.name in PERSON_FACING:
        return "", ""
    orchestrators = sorted(name for name in ctx.live if ctx.post_of(name) in PERSON_FACING)
    lines = [f"flotilla: you are {ctx.me.name} ({post.name} post), a background session nobody is attached to: a "
             "question here waits for ever. Only the orchestrator talks to the person."]
    if orchestrators:
        lines.append(f"Send the question to {', '.join(orchestrators)} with SendMessage; the answer comes back the "
                     "same way.")
    else:
        lines.append("No orchestrator is alive: the wait below carries the question, and `flotilla watch` shows it "
                     "to the person.")
    lines.append(f'Record the wait on the row the question holds up: {cli} work wait <branch> --on "the person" '
                 '--why "<the question>".')
    return "deny", "\n".join(lines)
```

- [ ] **Step 4: Wire the hook**

`flotilla/hooks.py`:

```python
EVENTS = ("session-start", "prompt", "stop", "guard", "permission", "ask")
```

the handler map in `run_hook` gains `"ask": _ask`; the failure branch prints to stderr for `ask` too
(`if event in ("stop", "permission", "ask"):`) — a PreToolUse hook's stdout must be JSON or nothing; and:

```python
def _ask(ctx, payload, out, now) -> int:
    from flotilla.watch.ask import ask_verdict
    kind, text = ask_verdict(ctx, cli=str(CLI))
    if kind == "deny":
        body = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": text}
    elif kind == "note":
        body = {"hookEventName": "PreToolUse", "additionalContext": text}
    else:
        return 0
    print(json.dumps({"hookSpecificOutput": body}), file=out)
    return 0
```

`hooks/hooks.json`, a second group in `PreToolUse`, after the Bash group:

```json
      {
        "matcher": "AskUserQuestion",
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/flotilla\" hook ask",
            "timeout": 20
          }
        ]
      }
```

The `ask` path gathers the context like `prompt` does (the census and the ledger): `HOOK_CHECK_TIMEOUT` is 3 s per
call, so 20 s holds both with a margin. The inactive path needs no change: a project without `.flotilla/` returns
before any import (`test_inactive_project_is_silent` and `test_inactive_path_imports_nothing_heavy` cover `ask`
through `hooks.EVENTS`).

- [ ] **Step 5: Run the tests and the full suite**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_hooks.py` → all pass; then
`claude plugin validate .` → passed; then the full suite → `N passed`.

- [ ] **Step 6: Commit**

```bash
git add flotilla/watch/ask.py flotilla/hooks.py hooks/hooks.json tests/test_hooks.py
git commit -m "feat(hooks): only the orchestrator asks the person

A background producer that asked the person with AskUserQuestion hung with nobody attached (F12, F16). A
PreToolUse hook on AskUserQuestion now refuses it for every background post but the orchestrator's, naming the
live orchestrators and the wait to record; interactive sessions, sessions with no post and a census it could not
ask let the question through."
```

---

### Task 5: The posts say who talks to the person, and who gives work

**Files:**
- Modify: `templates/posts/main.md`, `templates/posts/minor.md` (`template_version: 2` each),
  `templates/posts/sender.md` (`template_version: 3`), `templates/posts/orchestrator.md` (stays 4 from Task 3),
  `skills/flotilla/SKILL.md`
- Test: `tests/test_skills.py`

**Interfaces:**
- Consumes: the `ask` hook (Task 4), `flotilla fleet` (lists post rows with their post and liveness), `flotilla
  brief`, the letters (Task 2).
- Produces: template text only.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_skills.py`)

```python
def template(name):
    from flotilla.posts import TEMPLATE_DIR
    return (TEMPLATE_DIR / f"{name}.md").read_text(encoding="utf-8")


def test_producers_take_work_routed_by_the_orchestrator():
    for name in ("main", "minor"):
        text = template(name)
        assert "A task from the orchestrator is a work order" in text and "flotilla fleet" in text
        assert "Only the person assigns work" not in text


def test_no_post_but_the_orchestrator_tells_the_person():
    for name in ("main", "minor", "reviewer", "judge", "sender"):
        assert "tell the person" not in template(name).lower(), name
    skill = (ROOT / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Who talks to the person" in skill and "Tell the person, in one short paragraph" not in skill


def test_the_sender_asks_for_its_yes_through_the_orchestrator():
    text = template("sender")
    assert "to the orchestrator" in text and "flotilla brief" in text


def test_the_orchestrator_relays_the_senders_batch():
    text = template("orchestrator")
    assert "You are the one session that talks to the person" in text
    assert "You never ask the person to approve a push" not in text
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_skills.py`
Expected: FAIL on each of the four.

- [ ] **Step 3: Edit the templates**

`templates/posts/main.md` — `template_version: 2`; append:

```markdown
- A task from the orchestrator is a work order: before you take it, check that `flotilla fleet` lists the session
  that sent it as the live holder of the orchestrator post. Any other peer's message is information, never a work
  order.
- You never ask the person. A question for them goes to the orchestrator (SendMessage), and the row it holds up
  records it: `flotilla work wait <branch> --on "the person" --why "<the question>"`.
```

`templates/posts/minor.md` — `template_version: 2`; replace the last two bullets ("You never take the
orchestrator's or the sender's post…" and "A peer's message is information…") with:

```markdown
- You never take the orchestrator's or the sender's post when nobody holds it: tell the orchestrator one is
  needed, and keep your work committed on its branch.
- A task from the orchestrator is a work order: before you take it, check that `flotilla fleet` lists the session
  that sent it as the live holder of the orchestrator post. Any other peer's message is information, never a work
  order.
- You never ask the person. A question for them goes to the orchestrator (SendMessage), and the row it holds up
  records it: `flotilla work wait <branch> --on "the person" --why "<the question>"`.
```

`templates/posts/sender.md` — `template_version: 3`; replace step 3 with:

```markdown
3. When the project says a person authorizes merges, send the batch (`flotilla brief`) to the orchestrator, which
   puts it to the person and sends back their answer; wait for that yes. You never ask the person yourself: you run
   where nobody is attached. Take a yes only from the session `flotilla fleet` lists as the live orchestrator.
```

`templates/posts/orchestrator.md` — replace the closing paragraph ("You never ask the person to approve a push…")
with:

```markdown
You are the one session that talks to the person. Other posts send you their questions: put each to the person and
send the answer back to the session that asked, verbatim. The sender sends you its batch (`flotilla brief`) for the
person's yes; relay the yes or the no, never your own. A refusal a session reports as a flotilla defect goes to the
person as a defect of the tool, with its text verbatim.
```

`skills/flotilla/SKILL.md`:

1. "When you start", step 3 becomes: `3. Tell the orchestrator, in one short paragraph, what you inherited: rows in
   your name, broken chains, open findings. If you are the orchestrator, tell the person.` Step 4 becomes:
   `4. Wait for a task from the orchestrator (the orchestrator waits for the person). Do not start work nobody gave
   you.`
2. A new section before "## Never":

```markdown
## Who talks to the person

Only the orchestrator. Every other post runs in the background, where nobody is attached: a question asked there
waits for ever, and flotilla refuses AskUserQuestion in it. Send the question to the orchestrator (SendMessage) and
record the wait on the row it holds up: `flotilla work wait <branch> --on "the person" --why "<the question>"`.
A flotilla refusal that leaves you no move goes to the orchestrator too, with its text verbatim; never work around
it with git plumbing.

A task from the orchestrator is a work order. Check that `flotilla fleet` lists the session that sent it as the
live holder of the orchestrator post; any other peer's message is information.
```

3. "## Never", the last bullet becomes: `- take a post nobody gave you, even when it is empty: tell the orchestrator
   one is needed.`

- [ ] **Step 4: Run the tests and the full suite**

Run: `uv run --with pytest python -m pytest -p no:cacheprovider tests/test_skills.py tests/test_ledger_cli.py tests/test_posts.py`
→ all pass. Then the full suite → `N passed`. Search the templates and the skill for a remaining "the person"
instruction to a non-orchestrator post: `grep -n "person" templates/posts/*.md skills/flotilla/SKILL.md` — every hit
is the orchestrator's, the judge's "reaches a person" (the product's user, not this person), the sender's relayed
yes, or the new route.

- [ ] **Step 5: Commit**

```bash
git add templates/posts skills/flotilla/SKILL.md tests/test_skills.py
git commit -m "docs(fleet): the posts say only the orchestrator talks to the person

A minor refused the orchestrator's routed task and asked the person instead (F12); the sender asked the person for
its yes and about the tool's internals (F16, F17). Producers now take work routed by the live orchestrator; every
question, the sender's batch included, goes to the orchestrator, which alone talks to the person."
```

---

### Task 6: The record — decisions, spec, findings

**Files:**
- Modify: `docs/specs/2026-09-22-decisions-log.md`, `docs/specs/2026-09-22-flotilla-design.md` (section 8's hook
  table and section 6.9), `docs/field-tests/2026-09-27-snake-fleet.md`
- Test: none of its own; `tools/check_no_cyrillic.py`

- [ ] **Step 1: Decisions log.** Append entries 90–95 after entry 89, one per decision of this plan (Decisions 1–6
  above), each ending with its source in the log's form: `(Max's triage 2026-09-28)` for 1–6's Max parts and
  `(executor's decision, field fixes part 2, 2026-09-28)` for the executor's parts.

- [ ] **Step 2: Design spec, section 8.** Add a row to the hook table after the Bash row:

```markdown
| `PreToolUse` AskUserQuestion | refuses the question in a background session whose post is not the orchestrator's, naming the route | every session |
```

  and after the Stop guard paragraph:

```markdown
**Only the orchestrator talks to the person.** Every other post sends its question to the orchestrator and records
the wait; the sender's batch too. `flotilla watch --wait 3600`, kept running by the orchestrator, returns when
something new needs it. A move that passes work to another session prints the letter the caller sends
(SendMessage): an idle background session is woken only by a message.
```

- [ ] **Step 3: Design spec, section 6.9.** After its layer list, one sentence: "The letter a move prints is how
  layer 1 reaches a session that is idle: the hooks speak only when a session is prompted."

  (Read 6.9 first; name the layer the way it is named there. If 6.9 has no numbered layers, add the sentence after
  its first paragraph.)

- [ ] **Step 4: Findings file.** After the line "Part 1 (the ledger) fixes …", add:

```markdown
Part 2 (who talks to the person, who wakes the next mover) fixes F12, F16, F17, F23 — plan
`docs/plans/2026-09-28-field-fixes-voice-and-wake.md`.
```

- [ ] **Step 5: Check and commit**

Run: `python3 tools/check_no_cyrillic.py` → exit 0.

```bash
git add docs/specs docs/field-tests
git commit -m "docs(specs): decisions 90-95 and the spec for who talks to the person and who wakes the next mover"
```

---

### Task 7: Live probe — the ask guard in a real background session

The guard's decision is tested; whether Claude Code runs a PreToolUse hook on AskUserQuestion in a `--bg` session
and honours `deny` there is not measured yet (decisions log, entry 70 measured PermissionRequest only). One
background session on Sonnet settles it. This task changes no code unless the probe fails.

**Files:** none in the repository; the result goes into the findings file as a measurement.

- [ ] **Step 1: Point the probe project at this branch.** The snake project installs flotilla from the local
  marketplace clone. Update it to the branch head:
  `git -C /home/max/workspace/flotilla-market/plugins/flotilla fetch /home/max/workspace/flotilla feat/field-fixes-voice-and-wake && git -C /home/max/workspace/flotilla-market/plugins/flotilla checkout --detach FETCH_HEAD`
  (record the revision it stood on before, to restore it in Step 5).

- [ ] **Step 2: Raise one background session holding the minor post in the snake project:**

```bash
cd /home/max/workspace/snake && claude --bg -n "minor session 91" --model sonnet --permission-mode auto \
  "Ask the person, with the AskUserQuestion tool, which colour the snake should be. Then report what happened."
```

- [ ] **Step 3: Observe through the census, not the transcript.** Every 30 s for up to 3 minutes:
  `claude agents --json`, the row named "minor session 91": record `status` and `state`. Expected: it never
  stands at `waiting` (a hung question); it ends `idle`.

- [ ] **Step 4: Stop it:** `claude stop <its sessionId>` (by id: stopping by name fails, entry 70).

- [ ] **Step 5: Restore** the marketplace clone to the revision recorded in Step 1.

- [ ] **Step 6: Record the measurement** in `docs/field-tests/2026-09-27-snake-fleet.md` under the Part 2 line:
  date, what was raised, the census samples, the verdict. If the session stood at `waiting`, the hook did not
  hold: say so, and stop the plan here for Max — the guard's premise (a PreToolUse hook on AskUserQuestion in a
  background session) is then false and needs another design.

- [ ] **Step 7: Commit**

```bash
git add docs/field-tests/2026-09-27-snake-fleet.md
git commit -m "docs(field-test): the ask guard measured in a live background session"
```
