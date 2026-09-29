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
import tempfile
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


def plan(ledger, counts: dict, *, census, store, reserve: bool,
         strict: bool = True) -> tuple[list[launch.Seat], list[str]]:
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
    from flotilla.core import claude_state
    refusals, setup_warnings = claude_state.setup_problems(main, run=ledger.run)   # F2, F3
    if refusals and strict:
        raise SpawnRefused("; ".join(refusals) + "; nothing was raised")
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
    unwalked = []
    if (ledger.profile.get("judge") or {}).get("required") and not wanted.get("judge") and not held.get("judge"):
        unwalked.append("the profile requires a judge and the fleet will hold none: shipped rows wait for a walk "
                        "nobody makes; add one (`--post judge=1`)")
    return seats, compose.warnings(wanted, ledger.posts, held) + unwalked + refusals + setup_warnings


def _git(ledger, *args: str) -> subprocess.CompletedProcess:
    return ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False)


class SpawnStopped(SpawnRefused):
    """A spawn stopped partway; `raised` holds the sessions raised before it stopped."""

    def __init__(self, message: str, raised: list):
        super().__init__(message)
        self.raised = raised


def _take_back_git(ledger, seat: launch.Seat) -> None:
    """Undo what this raise made in git: the tree and the branch, both created by it."""
    _git(ledger, "worktree", "unlock", str(seat.tree))
    _git(ledger, "worktree", "remove", "--force", str(seat.tree))
    _git(ledger, "worktree", "prune")
    _git(ledger, "branch", "-D", seat.branch)


def _take_back(ledger, seat: launch.Seat, actor: Actor, row_id: str, cause: str) -> SpawnRefused:
    """Git first, then the post row; the refusal keeps the first cause and names a release that failed."""
    _take_back_git(ledger, seat)
    try:
        with ledger.session() as s:
            s.append(actor, row_id, "release", "released", evidence={"why": cause})
    except MoveRefused as err:
        return SpawnRefused(f"{cause}; the tree and branch were taken back, but post row {row_id} could not be "
                            f"released: {err}")
    return SpawnRefused(cause)


def _find(census, name: str, *, wait: float, poll: float, sleep):
    """The session with this name in the census: (session or None, whether the census answered at all)."""
    waited, answered = 0.0, False
    while True:
        try:
            found = next((item for item in census() if item.name == name), None)
            answered = True
        except CensusUnavailable:
            found = None
        if found is not None or waited >= wait:
            return found, answered
        sleep(poll)
        waited += poll


def raise_seat(ledger, seat: launch.Seat, *, caller: str, census, wait: float = 30.0, poll: float = 1.0,
               sleep=time.sleep) -> Raised:
    post = ledger.posts[seat.post]
    actor = Actor(seat.name, post, "spawn", caller)
    main = launch.main_checkout(ledger.root, run=ledger.run)
    command = launch.argv(seat, post, ledger.profile, main=main)
    base = gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
    if seat.tree.exists() or seat.tree.is_symlink():
        raise SpawnRefused(f"{seat.tree} already exists; {seat.name} was not raised and nothing there was touched")
    if gitq.branch_tip(ledger.root, seat.branch, run=ledger.run):
        raise SpawnRefused(f"branch `{seat.branch}` already exists; {seat.name} was not raised and the branch was "
                           "not touched")
    done = _git(ledger, "worktree", "add", "-q", "-b", seat.branch, str(seat.tree), base)
    if done.returncode != 0:
        made = gitq.branch_tip(ledger.root, seat.branch, run=ledger.run)
        if made and made == gitq.resolve(ledger.root, base, run=ledger.run) and not seat.tree.exists():
            _git(ledger, "branch", "-D", seat.branch)   # git made the branch before failing; it holds no work
        raise SpawnRefused(f"git worktree add failed for {seat.name}: {done.stderr.strip()}")
    _git(ledger, "worktree", "lock", "--reason", f"flotilla: {seat.name}", str(seat.tree))
    try:
        with ledger.session() as s:
            core.check_claim(s.rows, seat.branch)
            row = s.append(actor, next_row_id(s.rows), "reserve", "reserved",
                           fields={"branch": seat.branch, "owner": seat.name, "tree": str(seat.tree)})
    except MoveRefused as err:
        _take_back_git(ledger, seat)
        raise SpawnRefused(f"the post row for {seat.name} was refused: {err}; the tree and branch were taken "
                           "back") from err
    problem = ""
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out:
        try:
            started = ledger.run(command, cwd=str(main), stdin=subprocess.DEVNULL, stdout=out,
                                 stderr=subprocess.STDOUT, text=True, check=False, timeout=180)
            out.seek(0)
            text = (out.read() + (started.stdout or "") + (started.stderr or "")).strip()
            if started.returncode != 0:
                problem = f"`claude --bg` exited {started.returncode} for {seat.name}: {text[-300:]}"
        except subprocess.TimeoutExpired:
            problem = f"`claude --bg` timed out after 180 s for {seat.name}"
        except OSError as err:
            problem = f"`claude --bg` could not be run for {seat.name}: {err}"
    found, answered = _find(census, seat.name, wait=wait, poll=poll, sleep=sleep)
    if found is not None:
        return Raised(seat, found.short_id, f"{problem}, but the session is in the census" if problem else "")
    if problem and answered:
        raise _take_back(ledger, seat, actor, row.id, problem)
    if problem:
        return Raised(seat, None, f"{problem}; the census could not be asked, so the tree and post row are kept: "
                                  "check `claude agents` before launching again")
    return Raised(seat, None, f"launched, not yet seen in the census after {wait:g} s: do not launch it again; "
                              "check `claude agents`")


def spawn(ledger, counts: dict, *, census, store, caller: str, wait: float = 30.0, poll: float = 1.0,
          sleep=time.sleep) -> tuple[list[Raised], list[str]]:
    seats, warnings = plan(ledger, counts, census=census, store=store, reserve=True)
    raised: list[Raised] = []
    for seat in seats:
        try:
            raised.append(raise_seat(ledger, seat, caller=caller, census=census, wait=wait, poll=poll,
                                     sleep=sleep))
        except SpawnRefused as err:
            if not raised:
                raise
            raise SpawnStopped(f"{err}; stopped after raising {len(raised)} of {len(seats)}", raised) from err
    return raised, warnings
