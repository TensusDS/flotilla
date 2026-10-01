"""The fleet's post rows, and retiring a session (spec, section 7.5).

The view lists every open post row (`reserved`) with what the census and git say about it now: alive, its census
state, whether its tree is still there and still locked; after them, any session in a seat's tree that holds no
post, named as not a fleet session (H7). Retire stops the session and waits for the census to
agree, unlocks the tree, and releases the post row. It deletes nothing: before it reports, it counts the tree's
uncommitted files and lists every open row the session still owns, which are now orphaned and wait for `adopt`.
A retire that cannot tell whether the session runs does nothing. Once the session is stopped, the processes it left
running detached in its tree (a dev server, a browser) are stopped too (H50).
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from flotilla.core.census import CensusUnavailable
from flotilla.fleet import leftovers, strangers
from flotilla.ledger import core
from flotilla.ledger.actor import Actor
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row
from flotilla.posts import PostError, post_for_session


class RetireRefused(MoveRefused):
    """A session cannot be retired as asked; the message says why."""


def post_rows(ledger) -> list[Row]:
    return [row for row in ledger.rows().values() if row.is_open and row.state == "reserved"]


def finished_helper_seat(ledger, name: str) -> Row | None:
    """A helper that ran `helper done` released its seat but still runs until retired: its latest seat row."""
    seats = [row for row in ledger.rows().values() if row.owner == name and row.helper_of]
    return seats[-1] if seats and not seats[-1].is_open else None


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
    row = next((item for item in post_rows(ledger) if item.owner == name), None) or \
        finished_helper_seat(ledger, name)
    if row is None:
        raise RetireRefused(f"no post row for `{name}`; `flotilla fleet` lists the fleet")
    try:
        listed = [item for item in census() if item.name == name]
    except CensusUnavailable as err:
        raise RetireRefused(f"cannot tell whether `{name}` runs: the census could not be asked ({err})") from err
    if len(listed) > 1:   # which one is the seat cannot be told; stopping either could stop the wrong one
        ids = ", ".join(item.short_id or "(no id)" for item in listed)
        raise RetireRefused(f"the census lists {len(listed)} sessions named `{name}` ({ids}); stop the right one by "
                            f"hand with `claude stop <id>`, then retire again")
    found = listed[0] if listed else None
    cut_off = leftovers.now_ticks()   # before the stop: what starts after it is the stop's own work (session-end hooks)
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
    left = _stop_leftovers(ledger, row.tree, census, short_id=found.short_id if found else "",
                           was_running=found is not None, started_before=cut_off)
    ledger.run(["git", "-C", str(ledger.root), "worktree", "unlock", row.tree], capture_output=True, text=True,
               check=False)
    try:
        post = post_for_session(ledger.posts, name)
    except PostError:
        post = None
    freed = core.free_seat_tree(ledger, name, row.tree) if row.tree else ""
    if row.is_open:   # a finished helper released its seat with `helper done`; retiring it only stops it
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
    lines += left
    for other in ledger.rows().values():
        if other.is_open and other.owner == name and other.state != "reserved":
            lines.append(f"orphaned: `{other.branch}` ({other.state}); hand it on with `flotilla work adopt "
                         f"{other.branch} --to \"<session>\"`")
    return lines


def _stop_leftovers(ledger, tree: str, census, *, short_id: str = "", was_running: bool = True,
                    started_before: int | None = None) -> list[str]:
    """Stop what the session left running detached (H50): in its tree, and in its job directory when retire stopped
    it and so knows its id. Live sessions' processes are spared. The tree only when it is a seat's own worktree:
    never the main checkout, a relative path or a directory above the trees."""
    places, lines = [], []
    if tree and not (Path(tree).is_absolute() and not Path(tree).is_dir()):
        refused = core.seat_tree_refusal(ledger, tree)
        if refused:
            lines.append(f"background processes were not looked for: {refused}")
        else:
            places.append(tree)
    if was_running:
        job, why = leftovers.job_dir(short_id)
        if job is not None:
            places.append(str(job))
        else:
            lines.append(f"its job directory was not looked at: {why}")
    else:
        lines.append("its job directory was not looked at: the session had already gone, so its id is unknown")
    if not places:
        return lines
    if started_before is None:
        return lines + [f"background processes in {', '.join(places)} were not looked for: /proc could not be read "
                        f"on this machine"]
    try:
        keep = {item.pid for item in census() if item.pid}
    except CensusUnavailable:
        return lines + [f"background processes in {', '.join(places)} were not looked for: the census could not "
                        f"be asked"]
    for place in places:
        grace = 0 if was_running else int(leftovers.GRACE_SECONDS * leftovers.CLK_TCK)
        scanned = leftovers.scan(place, keep=keep, started_before=started_before, grace_ticks=grace)
        if scanned is None:
            return lines + [f"background processes in {', '.join(places)} were not looked for: /proc could not be "
                            f"read on this machine"]
        found, spared = scanned
        if spared:
            lines.append(f"left {len(spared)} process{'es' if len(spared) != 1 else ''} in {place} that started as "
                         f"the session stopped: " + ", ".join(item.command[:60] for item in spared[:5]))
        if not found:
            continue
        count = leftovers.stop(found)
        names = ", ".join(item.command[:60] for item in found[:5]) + (", ..." if len(found) > 5 else "")
        missed = f" ({len(found) - count} had already gone)" if count != len(found) else ""
        lines.append(f"sent SIGTERM to {count} background process{'es' if count != 1 else ''} left in {place}: "
                     f"{names}{missed}")
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
    try:
        running = {item.name for item in census() if item.name}
    except CensusUnavailable:
        running = set()
    seats = post_rows(ledger)
    seats += [seat for seat in (finished_helper_seat(ledger, name) for name in sorted(running)) if seat is not None]
    for row in seats:
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
