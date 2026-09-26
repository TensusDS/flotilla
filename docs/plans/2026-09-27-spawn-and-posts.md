# Spawn and Posts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise a fleet of named background Claude Code sessions with one command — each with a post, a home
worktree and its post text — retire them without losing their work, and give every session the arrangement it
follows (`flotilla spawn`, `flotilla retire`, `flotilla fleet`, `/flotilla:spawn`, `/flotilla:retire`, and the
model-facing `flotilla` skill).

**Architecture:** A new package `flotilla/fleet/`: composition (how many of which post), names (an issued-numbers
journal in the state directory, locked), launch (the pure `claude --bg` argv), spawn (cut and lock the home tree,
record the post row, launch, find the session in the census), retire (stop, unlock, release the post row, name what
is left). Everything that talks to `claude` or the census is injected, so tests run with a fake launcher and real git.

**Tech Stack:** Python 3.11+ stdlib, git, the `claude` CLI (`--bg`, `agents --json`, `stop`), pytest via `uv`.

**Spec:** `docs/specs/2026-09-22-flotilla-design.md` sections 3.4, 7 (7.1-7.5), 11, 13; decisions log entry 21.
Prior art read for this plan (Gas Town, multiclaude): names are prevented from colliding at spawn (multiclaude does
not), a post may carry its own permission mode (Gas Town's lead role cannot edit), and a retired session's left-over
work is named before it is stopped.

## Decisions (executor's, to be confirmed by Max when he reads the plan)

1. **Permission modes from the questionnaire:** "they ask me" → `manual`; "allow rules exist" → `dontAsk` (a call
   the rules do not allow is refused, visibly, instead of stalling on a prompt nobody sees); "auto mode" → `auto`.
   A post may override with an optional `permission_mode` key (any value `claude --permission-mode` accepts).
2. **"Strongest reviewer"** (`fleet.model = "reviewer-strongest"`) gives every post that may `accept` the `opus`
   alias; otherwise a post's `model` key is passed when it is not `inherit`.
3. **Names are machine-wide**: the census is machine-wide, so the issued-numbers journal is one per machine
   (`<state>/fleet/names.jsonl`). A new name is above every number ever issued for that post, above every live
   session's and every ledger name's number, and never equal to a taken name.
4. **The spawner records the post row, not the new session.** The reserve row is written with the new session's
   name as `by`, `via: spawn`, and the real caller in `caller` — part A's rule that a session never acts under
   another's name stays whole, because the spawner is not a session acting, it is the one who creates it.
5. **The session is found in the census, not in `claude --bg`'s printed text.** Printed text is not a supported
   surface; `claude agents --json` is. A session launched but not listed within the wait is reported as "launched,
   not yet seen — do not launch it again", never as a failure (spec 7.2: "address not read ≠ did not start").
6. **Retire stops, never deletes.** It stops the session, waits for the census to agree, unlocks the tree, releases
   the post row, and prints the tree's uncommitted file count and every open row the session still owns (orphaned,
   for `adopt`). Removing the tree is a person's step, printed as a command.
7. **One-copy posts stay single:** a spawn that would leave two live sessions holding a post with
   `writes_one_copy: true` (the sender) is refused naming the one alive.
8. **The census cannot tell "waiting for a task" from "waiting on a permission prompt"** (both read
   `state: blocked`, measured 2026-09-27 on 2.1.283 over eight background sessions). `flotilla fleet` prints the
   state as the census gives it; telling them apart belongs to the watchers part.

## Out of scope here

- Detecting a session stalled on a permission prompt, nudging idle holders of a move (watchers and guards part).
- Path-scoped deny rules for the judge (spec 17, open question 4).
- Reviving with `claude respawn`: native; `flotilla fleet` shows whether the name and tree are kept afterwards.

## Global Constraints

- Python floor 3.11; stdlib only (`dependencies = []`).
- English only everywhere; `python3 tools/check_no_cyrillic.py` must pass.
- Durable state only under `flotilla.core.paths.state_dir()`.
- An instrument that could not ask says unknown, never "no" and never "green".
- `spawn`, `retire` are person-only commands (`disable-model-invocation: true`); the `flotilla` skill is
  model-only (`user-invocable: false`).
- Tests never start a real Claude Code session: `claude` is always a fake in tests.
- Full suite on the default interpreter and on 3.11; `claude plugin validate .` passes.
- Test git commits and merges pass an identity (`ledgerkit.IDENTITY`): CI runners have no global git config.

## Review Focus

1. **Two spawns at the same moment** get different names — the journal is locked. Test: Task 2
   `test_two_callers_never_get_the_same_name`.
2. **A launch that fails after the tree was cut** leaves no tree, no branch and an open post row released. Test:
   Task 4 `test_a_failed_launch_takes_back_the_tree_the_branch_and_the_row`.
3. **A launched session the census does not list yet** is reported as launched, not failed, and keeps its tree.
   Test: Task 4 `test_a_session_not_yet_in_the_census_is_reported_not_retried`.
4. **Retiring a session with uncommitted work** keeps the tree and names the files and the orphaned rows. Test:
   Task 5 `test_retire_names_what_the_session_leaves_behind`.
5. **The census unreachable** — spawn refuses (it cannot check names), retire refuses (it cannot tell whether the
   session runs), `--dry-run` plans with a warning. Tests: Task 4 `test_spawn_refuses_without_the_census`, Task 5
   `test_retire_refuses_without_the_census`, Task 6 `test_dry_run_without_the_census_warns`.

---

## File map

| file | responsibility |
|---|---|
| `flotilla/posts.py` (modify) | `Post.model`, `Post.permission_mode` |
| `flotilla/fleet/__init__.py` (create) | package marker |
| `flotilla/fleet/compose.py` (create) | counts per post, raise order, one-copy check, warnings |
| `flotilla/fleet/names.py` (create) | the issued-numbers journal |
| `flotilla/fleet/launch.py` (create) | seat (name, tree, branch), permission mode, model, system prompt, argv |
| `flotilla/fleet/spawn.py` (create) | plan and raise seats |
| `flotilla/fleet/retire.py` (create) | the fleet view and retire |
| `flotilla/fleet/commands.py` (create), `flotilla/cli.py` (modify) | `flotilla spawn`, `retire`, `fleet` |
| `skills/spawn`, `skills/retire`, `skills/flotilla` (create) | the three skills |
| `tests/fleetkit.py` (create) | the fake `claude` and census |

---

### Task 1: Post model and permission mode; the composition

**Files:**
- Modify: `flotilla/posts.py`
- Create: `flotilla/fleet/__init__.py`, `flotilla/fleet/compose.py`
- Test: `tests/test_posts.py`, `tests/test_fleet_compose.py`

**Interfaces:**
- Produces: `posts.PERMISSION_MODES`; `Post.model: str = "inherit"`, `Post.permission_mode: str = ""`;
  `compose.ORDER`, `compose.FLAGS`, `compose.ALIASES`, `compose.CompositionError`;
  `compose.normalise(counts: dict, posts: dict) -> dict[str, int]`;
  `compose.raise_order(counts: dict[str, int]) -> list[str]` (post names, grouped, in raise order);
  `compose.one_copy_problems(counts, posts, live_posts: dict[str, int]) -> list[str]`;
  `compose.warnings(counts, posts, live_posts) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_posts.py`:

```python
def test_a_post_reads_its_model_and_permission_mode(tmp_path):
    from flotilla.posts import load_post
    path = tmp_path / "lead.md"
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\nmodel: opus\n"
                    "permission_mode: plan\n---\nbody\n", encoding="utf-8")
    post = load_post(path)
    assert (post.model, post.permission_mode) == ("opus", "plan")


def test_a_post_refuses_an_unknown_permission_mode(tmp_path):
    import pytest
    from flotilla.posts import PostError, load_post
    path = tmp_path / "lead.md"
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\npermission_mode: yolo\n---\n",
                    encoding="utf-8")
    with pytest.raises(PostError, match="permission_mode"):
        load_post(path)


def test_template_posts_inherit_the_model_and_the_permission_mode():
    from flotilla.posts import TEMPLATE_DIR, load_post
    for path in TEMPLATE_DIR.glob("*.md"):
        post = load_post(path)
        assert (post.model, post.permission_mode) == ("inherit", ""), path.name
```

Create `tests/test_fleet_compose.py`:

```python
import pytest

from flotilla.fleet import compose
from flotilla.posts import TEMPLATE_DIR, load_post

POSTS = {p.name: p for p in (load_post(path) for path in TEMPLATE_DIR.glob("*.md"))}


def test_counts_name_posts_and_accept_the_profile_alias():
    assert compose.normalise({"review": 2, "main": 1, "minor": 0}, POSTS) == {"reviewer": 2, "main": 1}


def test_a_count_for_a_post_the_project_lacks_is_refused():
    with pytest.raises(compose.CompositionError, match="no post `tester`"):
        compose.normalise({"tester": 1}, POSTS)
    with pytest.raises(compose.CompositionError, match="whole number"):
        compose.normalise({"main": -1}, POSTS)


def test_acceptors_are_raised_before_producers_and_custom_posts_last():
    counts = {"main": 1, "reviewer": 2, "sender": 1, "zeta": 1, "alpha": 1}
    assert compose.raise_order(counts) == ["sender", "reviewer", "main", "alpha", "zeta"]


def test_a_second_sender_is_refused():
    assert compose.one_copy_problems({"sender": 1}, POSTS, {"sender": 1})
    assert compose.one_copy_problems({"sender": 2}, POSTS, {})
    assert not compose.one_copy_problems({"sender": 1}, POSTS, {})


def test_a_fleet_above_three_without_orchestrator_or_sender_is_warned():
    found = compose.warnings({"main": 2, "reviewer": 2}, POSTS, {})
    assert any("orchestrator" in line for line in found) and any("sender" in line for line in found)
    assert compose.warnings({"main": 1, "reviewer": 1}, POSTS, {}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_posts.py tests/test_fleet_compose.py -q`
Expected: FAIL — `Post` has no `model`; `ModuleNotFoundError: flotilla.fleet`.

- [ ] **Step 3: Implement**

In `flotilla/posts.py`, after `MOVES` add:

```python
PERMISSION_MODES = ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")
```

add two fields at the end of the `Post` dataclass (after `body: str`):

```python
    model: str = "inherit"
    permission_mode: str = ""
```

and in `load_post`, replace the final `return Post(...)` with:

```python
    model = meta.get("model", "inherit")
    if not isinstance(model, str) or not model:
        raise PostError(f"{path}: `model` must be a model name or `inherit`")
    mode = meta.get("permission_mode", "")
    if mode and mode not in PERMISSION_MODES:
        raise PostError(f"{path}: `permission_mode` must be one of {', '.join(PERMISSION_MODES)}")
    return Post(name, pattern, frozenset(may), one_copy, version, path, body, model, mode)
```

Create `flotilla/fleet/__init__.py`:

```python
"""Raising, listing and retiring the fleet's sessions (spec, section 7)."""
```

Create `flotilla/fleet/compose.py`:

```python
"""A fleet composition: how many sessions of which post (spec, sections 7.2-7.3).

Counts come from flags (one per template post) or from the profile's `fleet.default`, and are checked against the
project's posts: a count for a post the project does not have is refused by name. Sessions are raised acceptors
first - orchestrator, sender, reviewer, judge - then producers, so a producer's first handover finds its reader
alive; custom posts come last, by name. A post that writes one-copy resources (the sender) is never held by two
live sessions.
"""

from __future__ import annotations

ORDER = ("orchestrator", "sender", "reviewer", "judge", "main", "minor")
FLAGS = {"orchestrator": "-o", "sender": "-s", "reviewer": "-r", "judge": "-j", "main": "-M", "minor": "-m"}
ALIASES = {"review": "reviewer"}
SOLO_ABOVE = 3


class CompositionError(ValueError):
    """A composition that cannot be raised; the message says why."""


def normalise(counts: dict, posts: dict) -> dict[str, int]:
    found: dict[str, int] = {}
    for key, value in counts.items():
        name = ALIASES.get(key, key)
        if name not in posts:
            raise CompositionError(f"no post `{key}` in .flotilla/posts/; posts: {', '.join(sorted(posts))}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CompositionError(f"`{key}` needs a whole number of sessions, not {value!r}")
        if value:
            found[name] = found.get(name, 0) + value
    return found


def raise_order(counts: dict[str, int]) -> list[str]:
    return [name for name in ORDER if name in counts] + sorted(name for name in counts if name not in ORDER)


def one_copy_problems(counts: dict[str, int], posts: dict, live_posts: dict[str, int]) -> list[str]:
    problems = []
    for name, count in counts.items():
        total = count + live_posts.get(name, 0)
        if posts[name].writes_one_copy and total > 1:
            problems.append(f"post `{name}` writes one-copy resources and is held by one session at most; this "
                            f"would make {total} ({live_posts.get(name, 0)} alive)")
    return problems


def warnings(counts: dict[str, int], posts: dict, live_posts: dict[str, int]) -> list[str]:
    total = sum(counts.values()) + sum(live_posts.values())
    if total <= SOLO_ABOVE:
        return []
    return [f"the fleet will hold {total} sessions and no {role}; above {SOLO_ABOVE} the spec suggests one"
            for role in ("orchestrator", "sender")
            if role in posts and counts.get(role, 0) + live_posts.get(role, 0) == 0]
```

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `uv run --with pytest python -m pytest tests/test_posts.py tests/test_fleet_compose.py -q`
Expected: PASS.
Run: `uv run --with pytest python -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/posts.py flotilla/fleet tests/test_posts.py tests/test_fleet_compose.py
git -C /home/max/workspace/flotilla commit -m "feat(fleet): posts carry a model and a permission mode; the composition"
```

---

### Task 2: The issued-numbers journal

**Files:**
- Create: `flotilla/fleet/names.py`
- Test: `tests/test_fleet_names.py`

**Interfaces:**
- Consumes: `LocalLogStore`, `Post.name_pattern`.
- Produces: `names.KEY = "names"`; `names.number_of(post, name) -> int | None`;
  `names.next_names(post, count, *, taken: set[str], store, reserve: bool, now: str = "") -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fleet_names.py`:

```python
import multiprocessing

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import names
from flotilla.posts import TEMPLATE_DIR, load_post

REVIEWER = load_post(TEMPLATE_DIR / "reviewer.md")
MAIN = load_post(TEMPLATE_DIR / "main.md")


def test_a_name_carries_its_number():
    assert names.number_of(REVIEWER, "review session 12") == 12
    assert names.number_of(REVIEWER, "main session 12") is None


def test_names_are_issued_in_order_and_never_twice(tmp_path):
    store = LocalLogStore(tmp_path)
    assert names.next_names(REVIEWER, 2, taken=set(), store=store, reserve=True) == ["review session 1",
                                                                                     "review session 2"]
    assert names.next_names(REVIEWER, 1, taken=set(), store=store, reserve=True) == ["review session 3"]


def test_a_new_name_is_above_every_live_or_known_number(tmp_path):
    store = LocalLogStore(tmp_path)
    found = names.next_names(REVIEWER, 1, taken={"review session 43", "main session 90"}, store=store, reserve=True)
    assert found == ["review session 44"]


def test_a_dry_run_issues_nothing(tmp_path):
    store = LocalLogStore(tmp_path)
    assert names.next_names(MAIN, 1, taken=set(), store=store, reserve=False) == ["main session 1"]
    assert names.next_names(MAIN, 1, taken=set(), store=store, reserve=True) == ["main session 1"]


def _issue(path, queue):
    queue.put(names.next_names(REVIEWER, 1, taken=set(), store=LocalLogStore(path), reserve=True)[0])


def test_two_callers_never_get_the_same_name(tmp_path):
    queue = multiprocessing.Queue()
    workers = [multiprocessing.Process(target=_issue, args=(tmp_path, queue)) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
    issued = [queue.get(timeout=5) for _ in workers]
    assert len(set(issued)) == 6
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_fleet_names.py -q`
Expected: FAIL — `ImportError: cannot import name 'names'`.

- [ ] **Step 3: Implement**

Create `flotilla/fleet/names.py`:

```python
"""Names for new sessions: from the issued-numbers journal, never "highest live + 1" (spec, section 7.2).

Live sessions are a snapshot of a minute; by morning they are gone, and a counter taken from them would reissue
names that yesterday's records already carry, so two different sessions' work would read as one. The journal is
append-only, one per machine (the census is machine-wide), and locked: two spawns in one second get different
numbers. A new number is above every number issued for the post and above every taken name's number; a name is
never equal to a taken one.
"""

from __future__ import annotations

import re

KEY = "names"


def number_of(post, name: str) -> int | None:
    regex = "^" + re.escape(post.name_pattern).replace(re.escape("{n}"), r"(\d+)") + "$"
    found = re.match(regex, name)
    return int(found.group(1)) if found else None


def next_names(post, count: int, *, taken: set[str], store, reserve: bool, now: str = "") -> list[str]:
    with store.transaction(KEY) as tx:
        issued = [record.get("n", 0) for record in tx.read().records if record.get("post") == post.name]
        known = [number_of(post, name) or 0 for name in taken]
        number = max([0, *issued, *known])
        found: list[str] = []
        while len(found) < count:
            number += 1
            name = post.name_pattern.replace("{n}", str(number))
            if name in taken:
                continue
            found.append(name)
            if reserve:
                tx.append({"post": post.name, "n": number, "name": name, "at": now})
    return found
```

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `uv run --with pytest python -m pytest tests/test_fleet_names.py -q`
Expected: PASS (5 tests).
Run: `uv run --with pytest python -m pytest`
Expected: all pass.

- [ ] **Step 5: Injection — the journal is what keeps names apart**

Copy `names.py` aside; change `if reserve:` to `if False:` (nothing is recorded); run the names tests. Expected:
`test_names_are_issued_in_order_and_never_twice` and `test_two_callers_never_get_the_same_name` FAIL. Restore, `cmp`,
record.

- [ ] **Step 6: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/fleet/names.py tests/test_fleet_names.py
git -C /home/max/workspace/flotilla commit -m "feat(fleet): names from a locked issued-numbers journal"
```

---

### Task 3: The seat and the launch command

**Files:**
- Create: `flotilla/fleet/launch.py`
- Test: `tests/test_fleet_launch.py`

**Interfaces:**
- Consumes: `names.number_of`, `Post`.
- Produces: `launch.PERMISSION`, `launch.STRONGEST = "opus"`, `launch.FIRST_PROMPT`, `launch.CLI` (absolute path
  of `scripts/flotilla`), `launch.LaunchError`; `launch.Seat(post, name, number, tree: Path, branch)`;
  `launch.main_checkout(root: Path, run=subprocess.run) -> Path`; `launch.seat_for(main: Path, post, name) -> Seat`;
  `launch.permission_mode(profile, post) -> str`; `launch.model_for(profile, post) -> str`;
  `launch.system_prompt(seat, post, *, main: Path) -> str`;
  `launch.argv(seat, post, profile, *, main: Path) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fleet_launch.py`:

```python
from pathlib import Path

import pytest

from flotilla.fleet import launch
from flotilla.posts import TEMPLATE_DIR, load_post
from ledgerkit import git, repo_with_origin

POSTS = {p.name: p for p in (load_post(path) for path in TEMPLATE_DIR.glob("*.md"))}
MAIN = Path("/work/app")


def test_a_seat_is_a_sibling_tree_on_a_fleet_branch():
    seat = launch.seat_for(MAIN, POSTS["reviewer"], "review session 3")
    assert (seat.tree, seat.branch, seat.number) == (Path("/work/app-reviewer-3"), "fleet/reviewer-3", 3)


def test_the_main_checkout_is_found_from_any_linked_tree(tmp_path):
    root = repo_with_origin(tmp_path)
    git(root, "worktree", "add", "-q", "-b", "side", str(tmp_path / "side"))
    assert launch.main_checkout(tmp_path / "side") == root.resolve()


def test_the_questionnaire_answer_sets_the_permission_mode():
    post = POSTS["main"]
    assert launch.permission_mode({"permissions": {"mode": "ask"}}, post) == "manual"
    assert launch.permission_mode({"permissions": {"mode": "rules"}}, post) == "dontAsk"
    assert launch.permission_mode({"permissions": {"mode": "auto"}}, post) == "auto"
    with pytest.raises(launch.LaunchError, match="ask, rules or auto"):
        launch.permission_mode({"permissions": {"mode": "sometimes"}}, post)


def test_a_posts_own_permission_mode_wins():
    import dataclasses
    lead = dataclasses.replace(POSTS["orchestrator"], permission_mode="plan")
    assert launch.permission_mode({"permissions": {"mode": "auto"}}, lead) == "plan"


def test_the_strongest_reviewer_answer_reaches_every_post_that_may_accept():
    profile = {"fleet": {"model": "reviewer-strongest"}}
    assert launch.model_for(profile, POSTS["reviewer"]) == "opus"
    assert launch.model_for(profile, POSTS["main"]) == ""
    assert launch.model_for({"fleet": {"model": "one"}}, POSTS["reviewer"]) == ""


def test_the_argv_names_the_session_its_tree_and_its_post():
    seat = launch.seat_for(MAIN, POSTS["reviewer"], "review session 3")
    argv = launch.argv(seat, POSTS["reviewer"], {"permissions": {"mode": "auto"}}, main=MAIN)
    assert argv[:8] == ["claude", "--bg", "-n", "review session 3", "--add-dir", "/work/app-reviewer-3",
                        "--permission-mode", "auto"]
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert '"review session 3"' in prompt and "`reviewer`" in prompt and str(launch.CLI) in prompt
    assert POSTS["reviewer"].body.strip()[:40] in prompt
    assert argv[-1] == launch.FIRST_PROMPT
    assert launch.CLI.is_file()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_fleet_launch.py -q`
Expected: FAIL — `ImportError: cannot import name 'launch'`.

- [ ] **Step 3: Implement**

Create `flotilla/fleet/launch.py`:

```python
"""Where a new session sits and how it is launched (spec, sections 7.1-7.2).

A seat is a sibling worktree of the main checkout, `<checkout>-<post>-<n>`, on branch `fleet/<post>-<n>`: the
session's home. The session is launched from the main checkout, which belongs to nobody (Claude Code's own
background guard refuses edits there and accepts them in any linked worktree), with its tree added by `--add-dir`.
Its name is given at birth (`-n`), its post text and the absolute path of the flotilla command line ride in the
system prompt, and its first prompt sends it to the `flotilla` skill. It is never asked to infer who it is.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from flotilla.fleet.names import number_of

PERMISSION = {"ask": "manual", "rules": "dontAsk", "auto": "auto"}
STRONGEST = "opus"
CLI = Path(__file__).resolve().parents[2] / "scripts" / "flotilla"
FIRST_PROMPT = ("Use the flotilla:flotilla skill: take the census, announce yourself to the live peers, report what "
                "you inherited, then wait for a task.")


class LaunchError(RuntimeError):
    """A session cannot be launched as asked; the message says why."""


@dataclass(frozen=True)
class Seat:
    post: str
    name: str
    number: int
    tree: Path
    branch: str


def main_checkout(root: Path, run=subprocess.run) -> Path:
    done = run(["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
               capture_output=True, text=True, check=False)
    if done.returncode != 0 or not done.stdout.strip():
        raise LaunchError(f"{root}: git could not name the main checkout")
    return Path(done.stdout.strip()).parent.resolve()


def seat_for(main: Path, post, name: str) -> Seat:
    number = number_of(post, name)
    if number is None:
        raise LaunchError(f"`{name}` does not match post `{post.name}`'s pattern `{post.name_pattern}`")
    slug = f"{post.name}-{number}"
    return Seat(post.name, name, number, main.parent / f"{main.name}-{slug}", f"fleet/{slug}")


def permission_mode(profile: dict, post) -> str:
    if post.permission_mode:
        return post.permission_mode
    answer = (profile.get("permissions") or {}).get("mode", "ask")
    if answer not in PERMISSION:
        raise LaunchError(f"permissions.mode is `{answer}`; it must be ask, rules or auto")
    return PERMISSION[answer]


def model_for(profile: dict, post) -> str:
    if (profile.get("fleet") or {}).get("model") == "reviewer-strongest" and "accept" in post.may:
        return STRONGEST
    return "" if post.model == "inherit" else post.model


def system_prompt(seat: Seat, post, *, main: Path) -> str:
    return (f"You are the session named \"{seat.name}\", holding the post `{post.name}` in a flotilla fleet on "
            f"this machine. Your home worktree is {seat.tree} (branch {seat.branch}). The main checkout {main} "
            "belongs to nobody: read it, never edit it; work happens in linked worktrees. "
            f"The flotilla command line is {CLI}; every ledger move goes through it. Your name and your post are "
            "given here: never infer them from the work.\n\n" + post.body.strip())


def argv(seat: Seat, post, profile: dict, *, main: Path) -> list[str]:
    command = ["claude", "--bg", "-n", seat.name, "--add-dir", str(seat.tree), "--permission-mode",
               permission_mode(profile, post)]
    model = model_for(profile, post)
    if model:
        command += ["--model", model]
    return command + ["--append-system-prompt", system_prompt(seat, post, main=main), FIRST_PROMPT]
```

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `uv run --with pytest python -m pytest tests/test_fleet_launch.py -q`
Expected: PASS (6 tests).
Run: `uv run --with pytest python -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/fleet/launch.py tests/test_fleet_launch.py
git -C /home/max/workspace/flotilla commit -m "feat(fleet): a seat per session and the claude --bg command that launches it"
```

---

### Task 4: Spawn — plan the seats, cut and lock the trees, record the post rows, launch, find in the census

**Files:**
- Create: `flotilla/fleet/spawn.py`, `tests/fleetkit.py`
- Test: `tests/test_fleet_spawn.py`

**Interfaces:**
- Consumes: `compose.*`, `names.next_names`, `launch.*`, `core.check_claim`, `model.next_row_id`,
  `actor.Actor`, `gitq.trunk_ref`, `posts.post_for_session`.
- Produces: `spawn.SpawnRefused(MoveRefused)`; `spawn.Raised(seat, short_id: str | None, note: str)`;
  `spawn.plan(ledger, counts, *, census, store, reserve: bool) -> tuple[list[Seat], list[str]]`;
  `spawn.raise_seat(ledger, seat, *, caller, census, wait=30.0, poll=1.0, sleep=time.sleep) -> Raised`;
  `spawn.spawn(ledger, counts, *, census, store, caller, wait=30.0, poll=1.0, sleep=time.sleep)
  -> tuple[list[Raised], list[str]]`;
  `fleetkit.FakeClaude` (a `subprocess.run` stand-in for `claude …`; real git passes through) with
  `.sessions: list[Session]`, `.launched: list[list[str]]`, `.fail_launch`, `.appear`, and `.census()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/fleetkit.py`:

```python
"""A fake `claude` for fleet tests: launches and stops are recorded, the census is a list, git runs for real."""

import subprocess

from flotilla.core.census import CensusUnavailable, Session


def session(name, short_id, state="blocked"):
    return Session(name=name, session_id=f"sid-{short_id}", kind="background", pid=None, short_id=short_id,
                   status=None, state=state, cwd="", started_at_ms=None)


class FakeClaude:
    def __init__(self, sessions=(), *, appear=True, fail_launch=False, reachable=True):
        self.sessions = list(sessions)
        self.launched: list[list[str]] = []
        self.stopped: list[str] = []
        self.appear = appear
        self.fail_launch = fail_launch
        self.reachable = reachable
        self.stops = True

    def census(self):
        if not self.reachable:
            raise CensusUnavailable("`claude agents --json` did not answer within 30s")
        return list(self.sessions)

    def __call__(self, cmd, **kwargs):
        if not (isinstance(cmd, list) and cmd and cmd[0] == "claude"):
            return subprocess.run(cmd, **kwargs)
        if cmd[1] == "--bg":
            self.launched.append(list(cmd))
            if self.fail_launch:
                return subprocess.CompletedProcess(cmd, 1, "", "error: not logged in")
            if self.appear:
                name = cmd[cmd.index("-n") + 1]
                self.sessions.append(session(name, f"{len(self.launched):06x}"))
            return subprocess.CompletedProcess(cmd, 0, "backgrounded", "")
        if cmd[1] in ("stop", "kill"):
            self.stopped.append(cmd[2])
            if self.stops:
                self.sessions = [s for s in self.sessions if s.short_id != cmd[2]]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", f"fake claude: {cmd[1:]}")
```

Create `tests/test_fleet_spawn.py`:

```python
import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import spawn
from fleetkit import FakeClaude, session
from ledgerkit import PROFILE, git, make_ledger, repo_with_origin

CALLER = "spawn by main-control 1"


def world(tmp_path, fake, profile=None):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=profile or {**PROFILE, "permissions": {"mode": "auto"}},
                         run=fake, census=fake.census)
    return root, ledger, LocalLogStore(tmp_path / "state" / "fleet")


def run(ledger, store, fake, counts, **more):
    return spawn.spawn(ledger, counts, census=fake.census, store=store, caller=CALLER, wait=0.2, poll=0.05,
                       sleep=lambda seconds: None, **more)


def test_spawn_raises_acceptors_first_each_in_its_own_tree(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    raised, warnings = run(ledger, store, fake, {"main": 1, "review": 1})
    assert [item.seat.name for item in raised] == ["review session 1", "main session 1"]
    assert all(item.short_id for item in raised) and warnings == []
    for item in raised:
        assert item.seat.tree.is_dir()
        assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert [cmd[3] for cmd in fake.launched] == ["review session 1", "main session 1"]


def test_each_seat_gets_a_post_row_recorded_by_the_spawner(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    row = next(iter(ledger.rows().values()))
    assert (row.state, row.owner, row.branch, row.tree) == ("reserved", "main session 1", "fleet/main-1",
                                                           str(raised[0].seat.tree))
    event = ledger.store.read(ledger.repo_key).records[-1]
    assert (event["by"], event["via"], event["caller"]) == ("main session 1", "spawn", CALLER)


def test_the_tree_is_locked_while_the_session_lives(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    listing = git(root, "worktree", "list", "--porcelain")
    assert f"worktree {raised[0].seat.tree}" in listing and "locked flotilla: main session 1" in listing


def test_names_skip_live_sessions_and_ledger_names(tmp_path):
    fake = FakeClaude([session("main session 4", "aaaaaa")])
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    assert raised[0].seat.name == "main session 5"


def test_a_second_sender_is_refused_naming_the_live_one(tmp_path):
    fake = FakeClaude([session("sender 1", "aaaaaa")])
    root, ledger, store = world(tmp_path, fake)
    with pytest.raises(spawn.SpawnRefused, match="one-copy"):
        run(ledger, store, fake, {"sender": 1})
    assert fake.launched == []


def test_a_failed_launch_takes_back_the_tree_the_branch_and_the_row(tmp_path):
    fake = FakeClaude(fail_launch=True)
    root, ledger, store = world(tmp_path, fake)
    with pytest.raises(spawn.SpawnRefused, match="not logged in"):
        run(ledger, store, fake, {"main": 1})
    assert not (tmp_path / "app-main-1").exists()
    assert git(root, "branch", "--list", "fleet/main-1") == ""
    assert [row.state for row in ledger.rows().values()] == ["released"]


def test_a_session_not_yet_in_the_census_is_reported_not_retried(tmp_path):
    fake = FakeClaude(appear=False)
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    assert raised[0].short_id is None and "do not launch it again" in raised[0].note
    assert raised[0].seat.tree.is_dir() and len(fake.launched) == 1
    assert [row.state for row in ledger.rows().values()] == ["reserved"]


def test_spawn_refuses_without_the_census(tmp_path):
    fake = FakeClaude(reachable=False)
    root, ledger, store = world(tmp_path, fake)
    with pytest.raises(spawn.SpawnRefused, match="census"):
        run(ledger, store, fake, {"main": 1})
    assert fake.launched == []


def test_a_dry_run_plans_names_and_changes_nothing(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    seats, _ = spawn.plan(ledger, {"main": 2}, census=fake.census, store=store, reserve=False)
    assert [seat.name for seat in seats] == ["main session 1", "main session 2"]
    assert fake.launched == [] and ledger.rows() == {} and not seats[0].tree.exists()
    seats, _ = spawn.plan(ledger, {"main": 1}, census=fake.census, store=store, reserve=False)
    assert seats[0].name == "main session 1"


def test_a_seat_whose_tree_already_exists_is_refused_before_anything_starts(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    (tmp_path / "app-main-1").mkdir()
    with pytest.raises(spawn.SpawnRefused, match="app-main-1 already exists"):
        run(ledger, store, fake, {"main": 1})
    assert fake.launched == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_fleet_spawn.py -q`
Expected: FAIL — `ImportError: cannot import name 'spawn'`.

- [ ] **Step 3: Implement**

Create `flotilla/fleet/spawn.py`:

```python
"""Raising sessions (spec, sections 7.2 and 7.5).

A plan turns a composition into seats: names from the journal (above every live, known and issued number), trees
and branches that must not exist yet, one-copy posts held once. Raising a seat cuts its tree from trunk and locks
it, records the post row (written by the spawner in the new session's name, `via: spawn`, the real caller kept),
launches `claude --bg` from the main checkout, and asks the census for the new name. A launch that fails takes back
what it made. A session the census does not list yet is reported as launched, never retried: a second launch would
put two processes under one name. Spawning needs the census, because names are checked against it.
"""

from __future__ import annotations

import subprocess
import time
from collections import Counter
from dataclasses import dataclass

from flotilla.core.census import CensusUnavailable
from flotilla.fleet import compose, launch, names
from flotilla.ledger import core, gitq
from flotilla.ledger.actor import Actor
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import next_row_id
from flotilla.posts import PostError, post_for_session


class SpawnRefused(MoveRefused):
    """The fleet cannot be raised as asked; the message says why."""


@dataclass(frozen=True)
class Raised:
    seat: launch.Seat
    short_id: str | None
    note: str = ""


def _live(census) -> list:
    try:
        return census()
    except CensusUnavailable as err:
        raise SpawnRefused(f"spawning needs the census to check names, and it could not be asked: {err}") from err


def _live_posts(ledger, sessions) -> Counter:
    held: Counter = Counter()
    for item in sessions:
        try:
            post = post_for_session(ledger.posts, item.name) if item.name else None
        except PostError:
            post = None
        if post is not None and item.state != "done":
            held[post.name] += 1
    return held


def plan(ledger, counts: dict, *, census, store, reserve: bool) -> tuple[list[launch.Seat], list[str]]:
    try:
        wanted = compose.normalise(counts, ledger.posts)
    except compose.CompositionError as err:
        raise SpawnRefused(str(err)) from err
    if not wanted:
        raise SpawnRefused("name a composition, for example -r 1 -M 1, or use --default")
    sessions = _live(census)
    held = _live_posts(ledger, sessions)
    problems = compose.one_copy_problems(wanted, ledger.posts, held)
    if problems:
        alive = ", ".join(item.name for item in sessions if item.name)
        raise SpawnRefused("; ".join(problems) + (f" (alive: {alive})" if alive else ""))
    taken = {item.name for item in sessions if item.name}
    taken |= {name for row in ledger.rows().values() for name in (row.owner, row.reader) if name}
    main = launch.main_checkout(ledger.root, run=ledger.run)
    seats: list[launch.Seat] = []
    for post_name in compose.raise_order(wanted):
        post = ledger.posts[post_name]
        issued = names.next_names(post, wanted[post_name], taken=taken, store=store, reserve=reserve,
                                  now=ledger.now())
        taken |= set(issued)
        seats += [launch.seat_for(main, post, name) for name in issued]
    clashes = [f"{seat.tree} already exists" for seat in seats if seat.tree.exists() or seat.tree.is_symlink()]
    clashes += [f"branch `{seat.branch}` already exists" for seat in seats
                if gitq.branch_tip(ledger.root, seat.branch, run=ledger.run)]
    if clashes:
        raise SpawnRefused("; ".join(clashes) + "; nothing was raised")
    return seats, compose.warnings(wanted, ledger.posts, held)


def _git(ledger, *args: str) -> subprocess.CompletedProcess:
    return ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False)


def _take_back(ledger, seat: launch.Seat, actor: Actor, row_id: str | None, why: str) -> None:
    if row_id is not None:
        with ledger.session() as s:
            s.append(actor, row_id, "release", "released", evidence={"why": why})
    _git(ledger, "worktree", "unlock", str(seat.tree))
    if seat.tree.exists():
        _git(ledger, "worktree", "remove", "--force", str(seat.tree))
    _git(ledger, "worktree", "prune")
    _git(ledger, "branch", "-D", seat.branch)


def raise_seat(ledger, seat: launch.Seat, *, caller: str, census, wait: float = 30.0, poll: float = 1.0,
               sleep=time.sleep) -> Raised:
    post = ledger.posts[seat.post]
    actor = Actor(seat.name, post, "spawn", caller)
    main = launch.main_checkout(ledger.root, run=ledger.run)
    command = launch.argv(seat, post, ledger.profile, main=main)
    base = gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
    done = _git(ledger, "worktree", "add", "-q", "-b", seat.branch, str(seat.tree), base)
    if done.returncode != 0:
        _take_back(ledger, seat, actor, None, "")
        raise SpawnRefused(f"git worktree add failed for {seat.name}: {done.stderr.strip()}")
    _git(ledger, "worktree", "lock", "--reason", f"flotilla: {seat.name}", str(seat.tree))
    with ledger.session() as s:
        core.check_claim(s.rows, seat.branch)
        row = s.append(actor, next_row_id(s.rows), "reserve", "reserved",
                       fields={"branch": seat.branch, "owner": seat.name, "tree": str(seat.tree)})
    try:
        started = ledger.run(command, cwd=str(main), capture_output=True, text=True, check=False, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as err:
        _take_back(ledger, seat, actor, row.id, f"launch failed: {err}")
        raise SpawnRefused(f"`claude --bg` could not be run for {seat.name}: {err}") from err
    if started.returncode != 0:
        why = ((started.stderr or "") + (started.stdout or "")).strip()[-300:]
        _take_back(ledger, seat, actor, row.id, f"launch failed: {why}")
        raise SpawnRefused(f"`claude --bg` failed for {seat.name}: {why}")
    waited = 0.0
    while True:
        try:
            found = next((item for item in census() if item.name == seat.name), None)
        except CensusUnavailable:
            found = None
        if found is not None:
            return Raised(seat, found.short_id)
        if waited >= wait:
            return Raised(seat, None, f"launched, not yet seen in the census after {wait:g} s: do not launch it "
                                      "again; check `claude agents`")
        sleep(poll)
        waited += poll


def spawn(ledger, counts: dict, *, census, store, caller: str, wait: float = 30.0, poll: float = 1.0,
          sleep=time.sleep) -> tuple[list[Raised], list[str]]:
    seats, warnings = plan(ledger, counts, census=census, store=store, reserve=True)
    raised = []
    for seat in seats:
        raised.append(raise_seat(ledger, seat, caller=caller, census=census, wait=wait, poll=poll, sleep=sleep))
    return raised, warnings
```

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `uv run --with pytest python -m pytest tests/test_fleet_spawn.py -q`
Expected: PASS (10 tests).
Run: `uv run --with pytest python -m pytest`
Expected: all pass.

- [ ] **Step 5: Injections — two shapes**

Copy `spawn.py` aside.
(a) Removal: in `raise_seat` replace `_take_back(ledger, seat, actor, row.id, f"launch failed: {why}")` with `pass`.
Expected: `test_a_failed_launch_takes_back_the_tree_the_branch_and_the_row` FAILS alone.
(b) Neighbour: in `plan` change `taken = {item.name for item in sessions if item.name}` to `taken = set()` (the
census is asked but not used for names). Expected: `test_names_skip_live_sessions_and_ledger_names` FAILS.
Restore, `cmp`, record both.

- [ ] **Step 6: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/fleet/spawn.py tests/fleetkit.py tests/test_fleet_spawn.py
git -C /home/max/workspace/flotilla commit -m "feat(fleet): spawn cuts and locks the home tree, records the post row, launches, finds the session"
```

---

### Task 5: The fleet view and retire

**Files:**
- Create: `flotilla/fleet/retire.py`
- Test: `tests/test_fleet_retire.py`

**Interfaces:**
- Consumes: `spawn.SpawnRefused`, `fleetkit.FakeClaude`, `Actor`, `post_for_session`.
- Produces: `retire.RetireRefused(MoveRefused)`; `retire.post_rows(ledger) -> list[Row]`;
  `retire.fleet_view(ledger, sessions: list | None) -> list[dict]` (keys `name`, `post`, `tree`, `tree_exists`,
  `locked`, `live`, `state`, `short_id`, `work`); `retire.retire(ledger, name, *, caller, census, wait=60.0,
  poll=1.0, sleep=time.sleep) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fleet_retire.py`:

```python
import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import retire, spawn
from flotilla.ledger import core
from fleetkit import FakeClaude
from ledgerkit import PROFILE, actor, commit, git, make_ledger, repo_with_origin

CALLER = "spawn by main-control 1"


def raised_world(tmp_path, fake):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "permissions": {"mode": "auto"}},
                         run=fake, census=fake.census)
    store = LocalLogStore(tmp_path / "state" / "fleet")
    raised, _ = spawn.spawn(ledger, {"main": 1}, census=fake.census, store=store, caller=CALLER, wait=0.1,
                            poll=0.05, sleep=lambda seconds: None)
    return root, ledger, raised[0]


def do_retire(ledger, fake, name="main session 1"):
    return retire.retire(ledger, name, caller="retire by main-control 1", census=fake.census, wait=0.2, poll=0.05,
                         sleep=lambda seconds: None)


def test_the_fleet_view_lists_each_post_row_with_its_session(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    view = retire.fleet_view(ledger, fake.census())
    assert [(item["name"], item["post"], item["live"], item["locked"], item["tree_exists"]) for item in view] == [
        ("main session 1", "main", True, True, True)]
    assert retire.fleet_view(ledger, None)[0]["live"] is None


def test_retire_stops_the_session_unlocks_the_tree_and_releases_the_post_row(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    lines = do_retire(ledger, fake)
    assert fake.stopped == [seat.short_id]
    assert "locked" not in git(root, "worktree", "list", "--porcelain")
    assert [row.state for row in ledger.rows().values()] == ["released"]
    assert seat.seat.tree.is_dir() and any(str(seat.seat.tree) in line for line in lines)


def test_retire_names_what_the_session_leaves_behind(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    (seat.seat.tree / "draft.txt").write_text("unsaved\n", encoding="utf-8")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    lines = "\n".join(do_retire(ledger, fake))
    assert "1 uncommitted file" in lines
    assert "orphaned: `feat/x` (claimed)" in lines and "flotilla work adopt feat/x" in lines
    assert ledger.rows()["r2"].state == "claimed"


def test_retire_refuses_without_the_census(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    fake.reachable = False
    with pytest.raises(retire.RetireRefused, match="census"):
        do_retire(ledger, fake)
    assert fake.stopped == [] and ledger.rows()["r1"].state == "reserved"


def test_retire_refuses_when_the_session_does_not_stop(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    fake.stops = False
    with pytest.raises(retire.RetireRefused, match="still running"):
        do_retire(ledger, fake)
    assert ledger.rows()["r1"].state == "reserved"


def test_retire_of_a_name_without_a_post_row_is_refused(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    with pytest.raises(retire.RetireRefused, match="no post row for `review session 9`"):
        do_retire(ledger, fake, "review session 9")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_fleet_retire.py -q`
Expected: FAIL — `ImportError: cannot import name 'retire'`.

- [ ] **Step 3: Implement**

Create `flotilla/fleet/retire.py`:

```python
"""The fleet's post rows, and retiring a session (spec, section 7.5).

The view lists every open post row (`reserved`) with what the census and git say about it now: alive, its census
state, whether its tree is still there and still locked. Retire stops the session and waits for the census to
agree, unlocks the tree, and releases the post row. It deletes nothing: before it reports, it counts the tree's
uncommitted files and lists every open row the session still owns, which are now orphaned and wait for `adopt`.
A retire that cannot tell whether the session runs does nothing.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from flotilla.core.census import CensusUnavailable
from flotilla.ledger.actor import Actor
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row
from flotilla.posts import PostError, post_for_session


class RetireRefused(MoveRefused):
    """A session cannot be retired as asked; the message says why."""


def post_rows(ledger) -> list[Row]:
    return [row for row in ledger.rows().values() if row.is_open and row.state == "reserved"]


def _post_name(ledger, name: str) -> str:
    try:
        post = post_for_session(ledger.posts, name)
    except PostError:
        return ""
    return post.name if post else ""


def _locked(ledger) -> set[str]:
    done = ledger.run(["git", "-C", str(ledger.root), "worktree", "list", "--porcelain"], capture_output=True,
                      text=True, check=False)
    locked, current = set(), ""
    for line in done.stdout.splitlines() if done.returncode == 0 else []:
        if line.startswith("worktree "):
            current = line[len("worktree "):]
        elif line.startswith("locked"):
            locked.add(str(Path(current).resolve()))
    return locked


def _running(sessions, name: str):
    return next((item for item in sessions if item.name == name and item.state != "done"), None)


def fleet_view(ledger, sessions: list | None) -> list[dict]:
    locked = _locked(ledger)
    rows = ledger.rows()
    view = []
    for row in post_rows(ledger):
        found = _running(sessions, row.owner) if sessions is not None else None
        view.append({
            "name": row.owner, "post": _post_name(ledger, row.owner), "tree": row.tree,
            "tree_exists": bool(row.tree) and Path(row.tree).is_dir(),
            "locked": bool(row.tree) and str(Path(row.tree).resolve()) in locked,
            "live": None if sessions is None else found is not None,
            "state": found.state if found else "", "short_id": found.short_id if found else "",
            "work": [other for other in rows.values()
                     if other.is_open and other.owner == row.owner and other.state != "reserved"],
        })
    return view


def _dirty(tree: str) -> int | None:
    if not tree or not Path(tree).is_dir():
        return None
    done = subprocess.run(["git", "-C", tree, "status", "--porcelain"], capture_output=True, text=True,
                          check=False)
    return len(done.stdout.splitlines()) if done.returncode == 0 else None


def retire(ledger, name: str, *, caller: str, census, wait: float = 60.0, poll: float = 1.0,
           sleep=time.sleep) -> list[str]:
    row = next((item for item in post_rows(ledger) if item.owner == name), None)
    if row is None:
        raise RetireRefused(f"no post row for `{name}`; `flotilla fleet` lists the fleet")
    try:
        found = _running(census(), name)
    except CensusUnavailable as err:
        raise RetireRefused(f"cannot tell whether `{name}` runs: the census could not be asked ({err})") from err
    if found is not None:
        if not found.short_id:
            raise RetireRefused(f"the census lists `{name}` without an id; stop it by hand with `claude agents`")
        stopped = ledger.run(["claude", "stop", found.short_id], capture_output=True, text=True, check=False)
        if stopped.returncode != 0:
            raise RetireRefused(f"`claude stop {found.short_id}` failed: {(stopped.stderr or '').strip()}")
        waited = 0.0
        while True:
            try:
                still = _running(census(), name)
            except CensusUnavailable:
                still = found
            if still is None:
                break
            if waited >= wait:
                raise RetireRefused(f"`{name}` is still running {wait:g} s after `claude stop`; nothing was released")
            sleep(poll)
            waited += poll
    ledger.run(["git", "-C", str(ledger.root), "worktree", "unlock", row.tree], capture_output=True, text=True,
               check=False)
    try:
        post = post_for_session(ledger.posts, name)
    except PostError:
        post = None
    with ledger.session() as s:
        s.append(Actor(name, post, "retire", caller), row.id, "release", "released", evidence={"why": "retired"})
    lines = [f"retired {name}" + (f" (stopped {found.short_id})" if found else " (it was not running)")]
    dirty = _dirty(row.tree)
    if dirty is None:
        lines.append(f"its tree {row.tree or '(none)'} is gone")
    else:
        uncommitted = f", {dirty} uncommitted file{'s' if dirty != 1 else ''}" if dirty else ""
        lines.append(f"its tree is kept at {row.tree}{uncommitted}; remove it with `git worktree remove "
                     f"{row.tree}` once nothing in it is needed")
    for other in ledger.rows().values():
        if other.is_open and other.owner == name and other.state != "reserved":
            lines.append(f"orphaned: `{other.branch}` ({other.state}); hand it on with `flotilla work adopt "
                         f"{other.branch} --to \"<session>\"`")
    return lines
```

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `uv run --with pytest python -m pytest tests/test_fleet_retire.py -q`
Expected: PASS (6 tests).
Run: `uv run --with pytest python -m pytest`
Expected: all pass.

- [ ] **Step 5: Injection — retire never releases a session it could not stop**

Copy `retire.py` aside; replace the line `raise RetireRefused(f"`{name}` is still running …")` with `break`. Expected:
`test_retire_refuses_when_the_session_does_not_stop` FAILS alone. Restore, `cmp`, record.

- [ ] **Step 6: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/fleet/retire.py tests/test_fleet_retire.py
git -C /home/max/workspace/flotilla commit -m "feat(fleet): the fleet view, and retire that stops, releases and names what is left"
```

---

### Task 6: `flotilla spawn`, `flotilla retire`, `flotilla fleet`

**Files:**
- Create: `flotilla/fleet/commands.py`
- Modify: `flotilla/cli.py`
- Test: `tests/test_fleet_cli.py`

**Interfaces:**
- Consumes: `ledger.commands.open_ledger`, `spawn.*`, `retire.*`, `compose.FLAGS`, `actor.NO_CENSUS`,
  `core.identity.find_calling_session`, `core.paths.state_dir`.
- Produces: `commands.run_fleet_command(args) -> int` (0 done, 2 refused); `commands.counts_from(args, profile)`;
  `commands.caller_line(census) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_fleet_cli.py`:

```python
import io
from contextlib import redirect_stdout

from flotilla import cli
from flotilla.onboard.tomlw import render_toml
from flotilla.posts import install_templates
from ledgerkit import commit, git, repo_with_origin

PROFILE = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"},
           "permissions": {"mode": "auto"}, "fleet": {"default": {"main": 1, "review": 1}, "model": "one"}}


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def onboarded(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text(render_toml(PROFILE), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


def test_dry_run_without_the_census_warns(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    assert code == 0
    assert "census: unknown" in out and "names may collide" in out
    assert "review session 1" in out and "main session 1" in out
    assert "--permission-mode auto" in out and "fleet/reviewer-1" in out
    assert not (tmp_path / "app-reviewer-1").exists()


def test_spawn_needs_a_composition_and_refuses_both_kinds_at_once(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--dry-run", "--root", str(root))
    assert code == 2 and "name a composition" in out
    code, out = run_cli("spawn", "--default", "-r", "1", "--dry-run", "--root", str(root))
    assert code == 2 and "either --default or counts" in out


def test_spawn_without_the_census_refuses_to_launch(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "-M", "1", "--root", str(root))
    assert code == 2 and "census" in out


def test_the_fleet_is_listed_even_when_the_census_is_unknown(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "--root", str(root))
    assert code == 0 and "no post rows" in out


def test_retire_of_an_unknown_name_is_refused(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("retire", "main session 7", "--root", str(root))
    assert code == 2 and "no post row for `main session 7`" in out


def test_a_custom_count_is_given_by_post_name(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--post", "minor=2", "--dry-run", "--root", str(root))
    assert code == 0 and "minor session 1" in out and "minor session 2" in out
    code, out = run_cli("spawn", "--post", "minor", "--dry-run", "--root", str(root))
    assert code == 2 and "NAME=N" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_fleet_cli.py -q`
Expected: FAIL — `invalid choice: 'spawn'`.

- [ ] **Step 3: Implement**

Create `flotilla/fleet/commands.py`:

```python
"""`flotilla spawn`, `flotilla retire` and `flotilla fleet`."""

from __future__ import annotations

import os
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core import platform as plat
from flotilla.core.census import CensusUnavailable, read_census
from flotilla.core.identity import find_calling_session
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.fleet import compose, launch, retire, spawn
from flotilla.ledger.actor import NO_CENSUS
from flotilla.ledger.commands import open_ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.posts import PostError


def census():
    if os.environ.get(NO_CENSUS):
        raise CensusUnavailable(f"{NO_CENSUS} is set")
    return read_census()


def caller_line(sessions) -> str:
    source = plat.probe().parent_pid_source
    found = find_calling_session(sessions, parent_of=lambda pid: plat.parent_pid(pid, source))
    return f"by {found.name}" if found is not None and found.name else "outside any listed session"


def counts_from(args, profile: dict) -> dict:
    flags = {post: getattr(args, f"count_{post}") for post in compose.FLAGS if getattr(args, f"count_{post}")}
    for item in args.post:
        name, sep, value = item.partition("=")
        if not sep or not value.strip().isdigit():
            raise MoveRefused(f"--post {item}: write NAME=N, for example --post minor=2")
        flags[name.strip()] = flags.get(name.strip(), 0) + int(value)
    if args.default and flags:
        raise MoveRefused("either --default or counts, not both")
    if args.default:
        return dict((profile.get("fleet") or {}).get("default") or {})
    return flags


def _spawn(ledger, args) -> int:
    counts = counts_from(args, ledger.profile)
    store = LocalLogStore(paths.state_dir() / "fleet")
    if args.dry_run:
        try:
            census()
            probe = census
        except CensusUnavailable as err:
            print(f"census: unknown ({err}); names may collide with live sessions")
            probe = lambda: []  # noqa: E731
        seats, warnings = spawn.plan(ledger, counts, census=probe, store=store, reserve=False)
        for line in warnings:
            print(f"warning: {line}")
        main = launch.main_checkout(ledger.root, run=ledger.run)
        for seat in seats:
            post = ledger.posts[seat.post]
            model = launch.model_for(ledger.profile, post)
            print(f"{seat.name}  ({seat.post})  tree {seat.tree}  branch {seat.branch}")
            print(f"    claude --bg -n \"{seat.name}\" --add-dir {seat.tree} --permission-mode "
                  f"{launch.permission_mode(ledger.profile, post)}" + (f" --model {model}" if model else "")
                  + f"  (from {main})")
        print(f"dry run: {len(seats)} session(s) planned; nothing was changed")
        return 0
    try:
        sessions = census()
    except CensusUnavailable as err:
        raise spawn.SpawnRefused(f"spawning needs the census to check names, and it could not be asked: "
                                 f"{err}") from err
    raised, warnings = spawn.spawn(ledger, counts, census=census, store=store,
                                   caller=f"spawn {caller_line(sessions)}")
    for line in warnings:
        print(f"warning: {line}")
    for item in raised:
        address = f"claude attach {item.short_id}" if item.short_id else item.note
        print(f"{item.seat.name}  {address}  {item.seat.tree}")
    print("a closed terminal tab does not stop a session; `flotilla fleet` lists the fleet")
    return 0


def _fleet(ledger, args) -> int:
    try:
        sessions = census()
    except CensusUnavailable as err:
        sessions = None
        print(f"census: unknown ({err}); liveness is not asserted")
    view = retire.fleet_view(ledger, sessions)
    if not view:
        print("no post rows: nobody was spawned, or everyone was retired")
    for item in view:
        live = {True: f"alive ({item['state'] or 'no state'}), claude attach {item['short_id']}",
                False: "not running", None: "liveness unknown"}[item["live"]]
        tree = item["tree"] if item["tree_exists"] else f"{item['tree']} (missing)"
        lock = "locked" if item["locked"] else "not locked"
        work = ", ".join(f"{row.branch} ({row.state})" for row in item["work"]) or "no open work"
        print(f"{item['name']}  ({item['post'] or 'no post'})  {live}\n    tree {tree}, {lock}; {work}")
    return 0


def _retire(ledger, args) -> int:
    try:
        sessions = census()
        caller = f"retire {caller_line(sessions)}"
    except CensusUnavailable:
        caller = "retire, census unknown"
    for line in retire.retire(ledger, args.name, caller=caller, census=census):
        print(line)
    return 0


def run_fleet_command(args) -> int:
    try:
        ledger = open_ledger(Path(args.root))
        return {"spawn": _spawn, "fleet": _fleet, "retire": _retire}[args.command](ledger, args)
    except (MoveRefused, PostError, CensusUnavailable, launch.LaunchError, config.ConfigError,
            repo.NotARepository, StorageCorrupt) as err:
        print(f"refused: {err}")
        return 2
```

In `flotilla/cli.py`, before `return parser`, add:

```python
    from flotilla.fleet.compose import FLAGS
    spawn_ = sub.add_parser("spawn", help="raise background sessions, each with a post and a home worktree")
    for post, flag in FLAGS.items():
        spawn_.add_argument(flag, dest=f"count_{post}", type=int, default=0, metavar="N", help=f"{post} sessions")
    spawn_.add_argument("--post", action="append", default=[], metavar="NAME=N", help="sessions of any post")
    spawn_.add_argument("--default", action="store_true", help="the profile's fleet.default composition")
    spawn_.add_argument("--dry-run", action="store_true", help="show names, trees and commands; change nothing")
    spawn_.add_argument("--root", default=".")
    retire_ = sub.add_parser("retire", help="stop a session and release its post; its work stays")
    retire_.add_argument("name")
    retire_.add_argument("--root", default=".")
    sub.add_parser("fleet", help="the fleet's sessions, their trees and their work").add_argument("--root", default=".")
```

and in `main`, before `return 2`, add:

```python
    if args.command in ("spawn", "retire", "fleet"):
        from flotilla.fleet.commands import run_fleet_command
        return run_fleet_command(args)
```

- [ ] **Step 4: Run the tests to verify they pass, then the full suite**

Run: `uv run --with pytest python -m pytest tests/test_fleet_cli.py -q`
Expected: PASS (6 tests).
Run: `uv run --with pytest python -m pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/fleet/commands.py flotilla/cli.py tests/test_fleet_cli.py
git -C /home/max/workspace/flotilla commit -m "feat(cli): flotilla spawn, retire and fleet"
```

---

### Task 7: The skills — `/flotilla:spawn`, `/flotilla:retire`, and the `flotilla` arrangement; documents

**Files:**
- Create: `skills/spawn/SKILL.md`, `skills/retire/SKILL.md`, `skills/flotilla/SKILL.md`
- Modify: `tests/test_skills.py`, `README.md`, `docs/specs/2026-09-22-flotilla-design.md`,
  `docs/specs/2026-09-22-decisions-log.md`

**Interfaces:**
- Consumes: CLI `flotilla spawn|retire|fleet|status|work|tree|receipt|brief`.
- Produces: three skills; `tests/test_skills.py` constant `MODEL_ONLY = {"flotilla"}`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_skills.py` change `PERSON_ONLY = {"doctor", "check", "status", "brief"}` to
`PERSON_ONLY = {"doctor", "check", "status", "brief", "spawn", "retire"}`, add below `MODEL_INVOCABLE`:

```python
MODEL_ONLY = {"flotilla"}
```

and append:

```python
def test_the_arrangement_skill_is_for_the_model_only():
    for path in SKILLS:
        meta = frontmatter(path)
        if path.parent.name in MODEL_ONLY:
            assert meta.get("user-invocable", "true").lower() == "false", path.parent.name
            assert meta.get("disable-model-invocation", "false").lower() != "true", path.parent.name
    assert {p.parent.name for p in SKILLS} >= MODEL_ONLY


def test_the_first_prompt_names_the_arrangement_skill():
    from flotilla.fleet.launch import FIRST_PROMPT
    assert "flotilla:flotilla" in FIRST_PROMPT
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --with pytest python -m pytest tests/test_skills.py -q`
Expected: FAIL — no `spawn`, `retire` or `flotilla` skill.

- [ ] **Step 3: Write the skills**

Create `skills/spawn/SKILL.md`:

```markdown
---
name: spawn
description: Raise flotilla sessions - background Claude Code sessions, each with a post (orchestrator, sender, reviewer, judge, main, minor), a name given at birth and its own home worktree - from a composition such as "-r 1 -M 2" or the profile's default.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn*)
---

1. Show the plan first: run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn $ARGUMENTS --dry-run` from the repository
   root (with no arguments, use `--default`). Show every line it prints: names, trees, branches, the permission
   mode and the warnings.
2. Ask the person whether to raise exactly that fleet. Raising sessions spends money and occupies worktrees; do
   not proceed on your own judgement.
3. On a yes, run the same command without `--dry-run` and show the table it prints: each name with its
   `claude attach <id>` and its tree.
4. A session reported "launched, not yet seen in the census" is alive or starting: never run spawn again for it,
   because two processes would share one name. Tell the person to check `claude agents`.
5. When the permission mode is `manual`, say that each session stops and waits at every permission prompt until
   someone attaches and answers.
```

Create `skills/retire/SKILL.md`:

```markdown
---
name: retire
description: Retire a flotilla session - stop it, release its post, keep its worktree and name the work it leaves orphaned.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla retire*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet*)
---

1. Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet` and show the line for the session named in $ARGUMENTS: its
   state, its tree and its open work.
2. If it holds open work or its tree has uncommitted changes, say so and ask the person to confirm the retire.
3. Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla retire "$ARGUMENTS"` and show every line it prints. The tree is kept;
   orphaned rows wait for `flotilla work adopt`. Never remove the tree yourself.
```

Create `skills/flotilla/SKILL.md`:

```markdown
---
name: flotilla
description: The arrangement every session in a flotilla fleet follows - who you are, how you start, how work moves through the ledger, whose move it is, and what never to do. Use when your system prompt says you hold a flotilla post, when you start as a flotilla session, or before any flotilla ledger move.
user-invocable: false
---

# Working in a flotilla fleet

Your system prompt names you, your post, your home worktree and the absolute path of the flotilla command line.
Use that path for every command below. Never infer your name or your post from the work.

## When you start

1. Take the census: `flotilla status`. Read deviations and findings first; they are moves that cannot happen now,
   or work git sees and the ledger does not.
2. Announce yourself to the live peers: list them (ListAgents) and send each one line - your name, your post,
   your home worktree.
3. Tell the person, in one short paragraph, what you inherited: rows in your name, broken chains, open findings.
4. Wait for a task. Do not start work nobody gave you.

## State lives in the ledger, not in letters

A letter is a notification of a move, never its carrier. Before you say "done", "handed", "accepted" or
"shipped", the move must be in the ledger, made by you through the command line:

- main and minor: claim by cutting a tree (`flotilla tree cut <branch> --tree <path> --ref <task>`), hand over
  committed and green (`flotilla receipt run --purpose handover`, then `flotilla work hand <branch>`), close what
  shipped (`flotilla work close <branch>`);
- reviewer: `flotilla work take <branch>`, then `flotilla work accept <branch> --reviewed <sha>` or
  `flotilla work fix <branch> --why "<what must change>"`;
- sender: `flotilla brief` for the person's one yes, then `flotilla work queue`, `land`, and `flotilla work
  reconcile` after every push;
- orchestrator: `flotilla work assign <branch> --reader "<session>"` and send the letter it prints;
- judge: `flotilla work walked` or `flotilla work broke` over the deployed build.

Your post's text in the system prompt says which of these are yours; a move your post may not make is refused.

## Whose move it is

`flotilla status` names, for every open row, whose move it is. If it is yours, make it or record why you wait:
`flotilla work wait <branch> --on "<whom>" --why "<why>"`. Falling silent while holding a move is the failure the
fleet is built against.

## Never

- edit the main checkout; work happens in linked worktrees;
- act under another session's name (`--as`); the ledger records who really called;
- read, return or accept your own work;
- type "shipped": the ledger asks the PR or origin;
- take a post nobody gave you, even when it is empty: tell the person one is needed.
```

- [ ] **Step 4: Run the skill tests, the plugin validator and the language gate**

Run: `uv run --with pytest python -m pytest tests/test_skills.py -q`
Expected: PASS.
Run: `claude plugin validate .` → `✔ Validation passed`; `python3 tools/check_no_cyrillic.py` → exit 0.

- [ ] **Step 5: Update the documents**

`README.md` — replace the status paragraph with:

```
**Status: pre-alpha.** The foundation, onboarding, the work ledger (`/flotilla:status`, `/flotilla:brief`) and
the fleet (`/flotilla:spawn`, `/flotilla:retire`) are in place; the lane and the guards are being built. Design:
`docs/specs/2026-09-22-flotilla-design.md`.
```

`docs/specs/2026-09-22-decisions-log.md` — insert before `## Open questions (for the foundation spec)` one numbered
entry per decision of this plan (numbers continue after the last one in the file), each the decision's text from
this plan's "Decisions" section, marked "(executor's decision, spawn and posts plan, 2026-09-27)".

`docs/specs/2026-09-22-flotilla-design.md`:
- section 7.2, after the `--dry-run` bullet, add: "- the post row is recorded by the spawner in the new session's
  name (`via: spawn`, the real caller kept); the session is found in `claude agents --json`, not in printed text;
  questionnaire answers map to `--permission-mode`: ask → `manual`, rules → `dontAsk`, auto → `auto`; a post may
  set its own `permission_mode`."
- section 7.5, the retire row: append "; the tree is kept, and retire prints its uncommitted file count".
- section 17, question 3: append "Measured 2026-09-27 (2.1.283): the census gives `state: blocked` both for a
  session waiting on a task and for one waiting on a prompt; telling them apart is the watchers' job."
- section 17, question 7: strike through and append "- **read 2026-09-27**: collisions are prevented at spawn
  (multiclaude does not), a post may carry its own permission mode (Gas Town), and retire names what it leaves."

- [ ] **Step 6: Full verification and commit**

Run: `uv run --with pytest python -m pytest` and `uv run --python 3.11 --with pytest python -m pytest` → both all
pass, same count. Run: `GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest python -m pytest` → all pass (the CI
runners have no global git identity).

```bash
git -C /home/max/workspace/flotilla add skills tests/test_skills.py README.md docs/specs
git -C /home/max/workspace/flotilla commit -m "feat(skills): /flotilla:spawn, /flotilla:retire and the flotilla arrangement; spec records the fleet"
```
