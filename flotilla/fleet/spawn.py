"""Raising sessions (spec, sections 7.2 and 7.5).

A plan turns a composition into seats: names from the journal (above every live, known and issued number), trees
and branches that must not exist yet, one-copy posts held once. Raising a seat cuts its tree from trunk and locks
it, records the post row (written by the spawner in the new session's name, `via: spawn`, the real caller kept),
launches `claude --bg` from the main checkout with the MCP plugins its post does not keep turned off, and asks the
census for the new name. A launch that fails takes back what it made. A session the census does not list yet is
reported as launched, never retried: a second launch would put two processes under one name. Spawning needs the
census, because names are checked against it.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from flotilla.core.census import CensusUnavailable
from flotilla.fleet import compose, launch, names, plugins
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


MEMORY_FLOOR_MB = 2000
HELPER = "helper"
SEAT_COST_MB = 800   # a seat with its MCP servers, as measured in the twosuns run (H49)
MEMINFO = Path("/proc/meminfo")


def read_available_mb(meminfo: Path = MEMINFO) -> int | None:
    """MemAvailable in MB, or None where it cannot be read (no /proc, as on macOS)."""
    try:
        text = meminfo.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            words = line.split()
            return int(words[1]) // 1024 if len(words) > 1 and words[1].isdigit() else None
    return None


def available_mb() -> int | None:
    return read_available_mb()


def memory_settings(ledger) -> tuple[int, int, list[str]]:
    """(floor, cost of one seat, notes): each a whole number of MB from `[fleet]`, or its default with a note."""
    from flotilla.core.config import whole_number
    fleet = ledger.profile.get("fleet") or {}
    floor, floor_note = whole_number(fleet.get("memory_floor_mb", MEMORY_FLOOR_MB), MEMORY_FLOOR_MB,
                                     "fleet.memory_floor_mb")
    cost, cost_note = whole_number(fleet.get("seat_cost_mb", SEAT_COST_MB), SEAT_COST_MB, "fleet.seat_cost_mb")
    return floor, cost, [note for note in (floor_note, cost_note) if note]


def memory_short(ledger, seats: int = 1) -> str:
    """Why `seats` new seats should not be raised into this machine now, or "" (H47, H9: the daemon retires idle
    seats under low memory, and a new seat takes the readers' place). Each seat is counted at its cost with its MCP
    servers (`fleet.seat_cost_mb`), not the free memory once."""
    floor, cost, _ = memory_settings(ledger)
    free = available_mb()
    left = None if free is None else free - seats * cost
    if left is None or not floor or left >= floor:
        return ""
    return (f"{free} MB of memory is available, and {seats} seat(s) at ~{cost} MB each (fleet.seat_cost_mb) "
            f"leave {left} MB, under the {floor} MB floor (fleet.memory_floor_mb): a new seat here makes the daemon "
            "retire idle ones, readers first; retire an idle seat first, raise fewer, or raise them with --anyway")


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
        if post is not None:   # a seat whose turn is done still holds its post (decision 118)
            held[post.name] += 1
    return held


#: Models Claude Code was seen to refuse auto mode for ("auto mode unavailable for this model", Haiku 4.5, measured
#: 2026-10-01). A warning, not a refusal: the list is Claude Code's, and it moves.
NO_AUTO_SEEN = ("haiku",)


def _auto_unavailable(ledger, wanted) -> list[str]:
    lines = []
    for post_name in wanted:
        post = ledger.posts[post_name]
        try:
            mode = launch.permission_mode(ledger.profile, post)
        except launch.LaunchError:
            continue
        model = launch.model_for(ledger.profile, post)
        if mode == "auto" and any(word in model.lower() for word in NO_AUTO_SEEN):
            lines.append(f"post `{post_name}` would run auto mode on `{model}`, for which Claude Code has said "
                         "\"auto mode unavailable for this model\"; pick another model or permission mode")
    return lines


def _named_before(ledger, mine) -> str:
    """Why seats cannot be raised while live seats of this project carry names from before its fleet name: they match
    no post, so the one-copy check would not see a live sender and raise a second one (review of 0.6.0, I1)."""
    import re
    old = []
    for item in mine:
        if not item.name or any(post.matches(item.name) for post in ledger.posts.values()):
            continue
        for post in ledger.posts.values():
            if not post.project:
                continue
            base = post.name_pattern[len(post.project) + 1:]
            regex = "^(?:.+-)?" + re.escape(base).replace(re.escape("{n}"), r"\d+") + "$"
            if re.match(regex, item.name):
                old.append(item.name)
                break
    if not old:
        return ""
    project = next(post.project for post in ledger.posts.values() if post.project)
    return (f"live seats of this project carry names from before its fleet name `{project}`: {', '.join(old)}; "
            "they hold their posts unseen by the new names - stand that fleet down (`flotilla fleet down`) or "
            "retire them first")


def _left_by_older_seats(ledger) -> set[str]:
    """The names whose seat branch (`fleet/<post>-<n>`) is still in the repository: numbering steps over them, so a
    project that took a fleet name and counts from 1 again does not meet the branches of its older seats (W11)."""
    listed = _git(ledger, "for-each-ref", "--format=%(refname:short)", "refs/heads/fleet/").stdout.split()
    found = set()
    for branch in listed:
        slug = branch[len("fleet/"):]
        post_name, _, number = slug.rpartition("-")
        post = ledger.posts.get(post_name)
        if post is not None and number.isdigit():
            found.add(post.name_pattern.replace("{n}", number))
    return found


def tree_left_behind(main, post):
    """Why a name's seat cannot be made here - its tree is already on disk, with no branch to number it by, as an
    older fleet leaves it (twosuns, 2026-10-03) - or "". Numbering steps over such a number; nothing there is
    touched."""
    def occupied(name: str) -> str:
        tree = launch.seat_for(main, post, name).tree
        return (f"{tree} is already there; `flotilla fleet clean` says whether it is an earlier fleet's and can go"
                if tree.exists() or tree.is_symlink() else "")
    return occupied


def plan(ledger, counts: dict, *, census, store, reserve: bool,
         strict: bool = True, anyway: bool = False) -> tuple[list[launch.Seat], list[str]]:
    try:
        wanted = compose.normalise(counts, ledger.posts)
    except compose.CompositionError as err:
        raise SpawnRefused(str(err)) from err
    if not wanted:
        raise SpawnRefused("name a composition, for example -r 1 -M 1, or use --default")
    if HELPER in wanted:
        raise SpawnRefused("a helper is raised by the session it helps, for one piece of its work: "
                           "`flotilla helper raise --for <branch> --task \"<what>\"`")
    short = "" if anyway else memory_short(ledger, sum(wanted.values()))
    if short and strict:
        raise SpawnRefused("; ".join(memory_settings(ledger)[2] + [short]) + "; nothing was raised")
    sessions = _live(census)
    from flotilla.ledger import project
    mine = project.members(sessions, ledger.rows(), project.roots(ledger.root, run=ledger.run))
    stale = _named_before(ledger, mine)
    if stale and strict:
        raise SpawnRefused(stale + "; nothing was raised")
    held = _live_posts(ledger, mine)   # one-copy resources are the project's; the census is the machine's (W10)
    problems = compose.one_copy_problems(wanted, ledger.posts, held)
    if problems:
        alive = ", ".join(item.name for item in mine if item.name)
        raise SpawnRefused("; ".join(problems) + (f" (alive here: {alive})" if alive else ""))
    taken = {item.name for item in sessions if item.name}   # names are machine-wide addresses: all of them
    taken |= {name for row in ledger.rows().values() for name in (row.owner, row.reader) if name}
    taken |= _left_by_older_seats(ledger)
    main = launch.main_checkout(ledger.root, run=ledger.run)
    from flotilla.core import claude_state
    refusals, setup_warnings = claude_state.setup_problems(main, run=ledger.run)   # F2, F3
    if refusals and strict:
        raise SpawnRefused("; ".join(refusals) + "; nothing was raised")
    seats: list[launch.Seat] = []
    skipped: list[str] = []
    for post_name in compose.raise_order(wanted):
        post = ledger.posts[post_name]
        issued = names.next_names(post, wanted[post_name], taken=taken, store=store, reserve=reserve,
                                  now=ledger.now(), occupied=tree_left_behind(main, post), skipped=skipped)
        taken |= set(issued)
        seats += [launch.seat_for(main, post, name) for name in issued]
    clashes = [f"{seat.tree} already exists" for seat in seats if seat.tree.exists() or seat.tree.is_symlink()]
    clashes += [f"branch `{seat.branch}` already exists" for seat in seats
                if gitq.branch_tip(ledger.root, seat.branch, run=ledger.run)]
    if clashes:
        raise SpawnRefused("; ".join(clashes) + "; nothing was raised")
    unwalked = _auto_unavailable(ledger, wanted)
    if (ledger.profile.get("judge") or {}).get("required") and not wanted.get("judge") and not held.get("judge"):
        unwalked.append("the profile requires a judge and the fleet will hold none: shipped rows wait for a walk "
                        "nobody makes; add one (`--post judge=1`)")
    return seats, memory_settings(ledger)[2] + ([short] if short else []) + skipped + \
        compose.warnings(wanted, ledger.posts, held) + unwalked + refusals + setup_warnings


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


def _record_session(ledger, actor: Actor, row_id: str, session_id: str) -> None:
    """Keep the seat's session id on its post row: `claude --bg` ignores --session-id (measured on 2.1.289), so it is
    learned here, and with it the broker knows a seat when the census is down. Only an id no other open row carries
    is written - one session is one seat - and a failure to write it costs nothing but that knowledge."""
    if not session_id:
        return
    try:
        with ledger.session() as s:
            if any(other.is_open and other.session_id == session_id and other.id != row_id
                   for other in s.rows.values()):
                return
            row = s.rows.get(row_id)
            if row is None or not row.is_open:
                return
            s.append(actor, row_id, "launched", s.next_state(row, "launched"), fields={"session_id": session_id})
    except MoveRefused:
        return


def _find(census, name: str, *, wait: float, poll: float, sleep):
    """The session with this name in the census: (session or None, whether the census answered at all, how many
    sessions the census listed under the name)."""
    waited, answered, named = 0.0, False, 0
    while True:
        try:
            listed = [item for item in census() if item.name == name]
            found, named, answered = (listed[0] if listed else None), len(listed), True
        except CensusUnavailable:
            found = None
        if found is not None or waited >= wait:
            return found, answered, named
        sleep(poll)
        waited += poll


#: How long a just-reserved seat may still be launching, unseen by the census: `claude --bg`'s 180 s and the wait
#: for its name, with room. A one-copy post's row younger than this blocks a second copy even when nobody is listed.
LAUNCH_WINDOW = 600


def _census_names(census) -> set[str] | None:
    try:
        return {item.name for item in census()}
    except CensusUnavailable:
        return None   # unknown: every rival counts


def _one_copy_rival(ledger, rows: dict, seat: launch.Seat, live: set[str] | None) -> str:
    """Why this seat would be a second copy of a one-copy post: another open seat row of the post whose session is
    alive (`live`, the census read before the lock; None when it could not be asked), or was reserved so recently
    that it may still be launching. Run under the ledger's lock, where the plan's census check could not see a spawn
    running beside it."""
    if not ledger.posts[seat.post].writes_one_copy:
        return ""
    from flotilla.posts import PostError, post_for_session
    rivals = []
    for row in rows.values():
        if not row.is_open or row.state != "reserved" or not row.owner or row.owner == seat.name:
            continue
        try:
            post = post_for_session(ledger.posts, row.owner)
        except PostError:
            continue
        if post and post.name == seat.post:
            rivals.append(row)
    if not rivals:
        return ""
    now = _moment(ledger.now())
    for row in rivals:
        reserved = _moment((row.history[0].get("at") if row.history else "") or row.updated_at)
        fresh = now is None or reserved is None or (now - reserved).total_seconds() < LAUNCH_WINDOW
        if live is None or row.owner in live or fresh:
            why = "is alive" if live and row.owner in live else ("was reserved moments ago and may still be "
                                                                 "launching" if fresh else "cannot be ruled out")
            return (f"post `{seat.post}` writes one-copy resources, and {row.owner} {why}; {seat.name} would be a "
                    "second copy. Retire the other first, or wait for it to show in `claude agents`")
    return ""


def _moment(value):
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def raise_seat(ledger, seat: launch.Seat, *, caller: str, census, wait: float = 30.0, poll: float = 1.0,
               sleep=time.sleep, base: str = "", fields: dict | None = None, prompt: str = "",
               settings_json: str | None = None) -> Raised:
    """Cut the seat's tree (from `base`, else trunk), reserve its post row (with any extra `fields`), launch it
    (with `prompt` as its first prompt, else the fleet's). `settings_json` None asks the plugin list here, so a
    seat raised by any caller is narrowed (H49); "" is an explicit choice not to narrow."""
    post = ledger.posts[seat.post]
    actor = Actor(seat.name, post, "spawn", caller)
    main = launch.main_checkout(ledger.root, run=ledger.run)
    if settings_json is None:
        settings_json = plugins.narrowing(ledger.run, main).settings_json(post)
    command = launch.argv(seat, post, ledger.profile, main=main, settings_json=settings_json, first_prompt=prompt)
    base = base or gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
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
    live = _census_names(census) if post.writes_one_copy else set()   # asked before the lock: it may take 30 s
    try:
        with ledger.session() as s:
            core.check_claim(s.rows, seat.branch)
            rival = _one_copy_rival(ledger, s.rows, seat, live)   # under the lock: two spawns at once (TODO)
            if rival:
                raise MoveRefused(rival)
            row = s.append(actor, next_row_id(s.rows), "reserve", "reserved",
                           fields={"branch": seat.branch, "owner": seat.name, "tree": str(seat.tree),
                                   **(fields or {})})
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
    found, answered, named = _find(census, seat.name, wait=wait, poll=poll, sleep=sleep)
    if found is not None:
        if named == 1:
            _record_session(ledger, actor, row.id, found.session_id)
        return Raised(seat, found.short_id, f"{problem}, but the session is in the census" if problem else "")
    if problem and answered:
        raise _take_back(ledger, seat, actor, row.id, problem)
    if problem:
        return Raised(seat, None, f"{problem}; the census could not be asked, so the tree and post row are kept: "
                                  "check `claude agents` before launching again")
    return Raised(seat, None, f"launched, not yet seen in the census after {wait:g} s: do not launch it again; "
                              "check `claude agents`")


def spawn(ledger, counts: dict, *, census, store, caller: str, wait: float = 30.0, poll: float = 1.0,
          sleep=time.sleep, anyway: bool = False) -> tuple[list[Raised], list[str]]:
    seats, warnings = plan(ledger, counts, census=census, store=store, reserve=True, anyway=anyway)
    narrow = plugins.narrowing(ledger.run, launch.main_checkout(ledger.root, run=ledger.run))   # once (H49)
    warnings = warnings + narrow.warnings([ledger.posts[seat.post] for seat in seats])
    raised: list[Raised] = []
    for seat in seats:
        try:
            raised.append(raise_seat(ledger, seat, caller=caller, census=census, wait=wait, poll=poll,
                                     sleep=sleep, settings_json=narrow.settings_json(ledger.posts[seat.post])))
        except SpawnRefused as err:
            if not raised:
                raise
            raise SpawnStopped(f"{err}; stopped after raising {len(raised)} of {len(seats)}", raised) from err
    return raised, warnings
