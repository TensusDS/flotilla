"""The fleet's post rows, and retiring a session (spec, section 7.5).

The view lists every open post row (`reserved`) with what the census and git say about it now: alive, its census
state, whether its tree is still there and still locked; after them, any session in a seat's tree that holds no
post, named as not a fleet session (H7). Retire stops the session and waits for the census to
agree, unlocks the tree, and releases the post row. It deletes nothing: before it reports, it counts the tree's
uncommitted files and lists every open row the session still owns, which are now orphaned and wait for `adopt`.
A retire that cannot tell whether the session runs does nothing.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from flotilla.core.census import CensusUnavailable
from flotilla.fleet import strangers
from flotilla.ledger import core
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
    """Listed in the census at all: a background session whose turn is done is still a process (G12)."""
    return next((item for item in sessions if item.name == name), None)


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
            "state": found.state if found else "", "short_id": (found.short_id or "") if found else "",
            "dirty": _dirty(row.tree),
            "work": [other for other in rows.values()
                     if other.is_open and other.owner == row.owner and other.state != "reserved"],
            "stranger": "",
        })
    for session, tree in strangers.in_seat_trees(sessions or [], rows, lambda name: _post_name(ledger, name)):
        view.append({"name": session.name, "post": "", "stranger": tree, "short_id": session.short_id or ""})
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
    freed = core.free_seat_tree(ledger, name, row.tree) if row.tree else ""
    with ledger.session() as s:
        s.append(Actor(name, post, "retire", caller), row.id, "release", "released",
                 evidence={"why": "retired", **({"tree": freed} if freed else {})})
    lines = [f"retired {name}" + (f" (stopped {found.short_id})" if found else " (it was not running)")]
    dirty = _dirty(row.tree)
    if not row.tree or not Path(row.tree).is_dir():
        lines.append(f"its tree {row.tree or '(none)'} is gone")
    elif dirty is None:
        lines.append(f"its tree {row.tree} exists; its state is unknown (git could not read it), so it is kept")
    else:
        uncommitted = f", {dirty} uncommitted file{'s' if dirty != 1 else ''}" if dirty else ""
        lines.append(f"its tree is kept at {row.tree}{uncommitted}; remove it with `git worktree remove "
                     f"{row.tree}` once nothing in it is needed")
    for other in ledger.rows().values():
        if other.is_open and other.owner == name and other.state != "reserved":
            lines.append(f"orphaned: `{other.branch}` ({other.state}); hand it on with `flotilla work adopt "
                         f"{other.branch} --to \"<session>\"`")
    return lines


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
        except (MoveRefused, OSError) as err:   # an event script, a failed `claude`: the other seats still go
            refused += 1
            lines.append(f"refused {row.owner}: {err}")
    if not lines:
        lines.append("no post rows: nobody was spawned, or everyone was retired")
    lines += still_alive(ledger, census, me)
    return lines, refused


def _trees(ledger) -> list[Path]:
    """The project's main checkout and every worktree of it: where a session of this project runs."""
    done = ledger.run(["git", "-C", str(ledger.root), "worktree", "list", "--porcelain"], capture_output=True,
                      text=True, check=False)
    found = [Path(line.split(" ", 1)[1]) for line in done.stdout.splitlines() if line.startswith("worktree ")] \
        if done.returncode == 0 else []
    return found or [Path(ledger.root)]


def still_alive(ledger, census, me: str) -> list[str]:
    """What stays alive once the fleet is down: sessions working in this project that are not its seats — seats of
    past fleets, sessions started by hand (H12). Each with the command that stops it."""
    try:
        sessions = census()
    except CensusUnavailable:
        return ["still alive: unknown, the census could not be asked after standing down"]
    trees = [tree.resolve() for tree in _trees(ledger)]
    lines = []
    for item in sessions:
        if not item.name or item.name == me or not item.cwd:
            continue
        where = Path(item.cwd).resolve()
        if not any(where == tree or tree in where.parents for tree in trees):
            continue
        stop = f"`claude stop {item.short_id}`" if item.short_id else "an interactive session: close it there"
        lines.append(f"still alive: {item.name} in {item.cwd}; {stop}")
    return lines
