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
