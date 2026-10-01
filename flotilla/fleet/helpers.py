"""Helper sessions: a fleet session raises one for a piece of its own work (field test twosuns, H8).

A helper is a seat of the `helper` post: its tree is cut from the tip of the branch it helps, its seat row names the
parent row (`helper_of`), and it leaves by `done`, which records its tip and a summary and releases the seat. It
never delivers: the parent merges the helper's branch and hands the whole over. The helper stays in the ledger from
the first minute to the last, so nothing it did is lost when it stops.
"""

from __future__ import annotations

import time

from flotilla.fleet import launch, names, spawn
from flotilla.ledger import gitq
from flotilla.ledger.actor import Actor
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row

HELPERS_PER_SEAT = 2
WORKING = ("claimed", "fixing")


def _live(census) -> set[str]:
    try:
        return {item.name for item in census() if item.name}
    except Exception as err:  # noqa: BLE001 - the census's own errors carry the reason
        raise MoveRefused(f"a helper is raised only when the census can say who is alive: {err}") from err


def prompt_for(helper: str, parent: Row, owner: str, task: str, tree) -> str:
    return (f"You are {helper}, a helper raised by {owner} for branch `{parent.branch}` (row {parent.id}). Your "
            f"task: {task}\n\nWork only in your own tree, {tree}: it was cut from the tip of `{parent.branch}`. "
            "Commit your work there. Ask the session that raised you if something is unclear. When the task is done, "
            "or cannot be done, run `flotilla helper done --summary \"<what you did, or why not>\"` and stop; the "
            "flotilla:flotilla skill explains the rest.")


def raise_helper(ledger, actor: Actor, branch: str, *, task: str, census, store, caller: str, wait: float = 30.0,
                 poll: float = 1.0, sleep=time.sleep, anyway: bool = False) -> spawn.Raised:
    task = task.strip()
    if not task:
        raise MoveRefused("say what the helper is to do (--task)")
    post = ledger.posts.get(spawn.HELPER)
    if post is None:
        raise MoveRefused("this project has no `helper` post: run `flotilla onboard` to install the post templates")
    rows = ledger.rows()
    parent = next((row for row in reversed(list(rows.values())) if row.branch == branch and row.is_open), None)
    if parent is None:
        raise MoveRefused(f"no open row for `{branch}`")
    if parent.owner != actor.name:
        raise MoveRefused(f"`{branch}` belongs to {parent.owner}; a helper is raised by the session whose work it is")
    if parent.state not in WORKING:
        raise MoveRefused(f"`{branch}` is {parent.state}; a helper helps work in progress (claimed or fixing)")
    live = _live(census)
    limit = int((ledger.profile.get("fleet") or {}).get("helpers_per_seat", HELPERS_PER_SEAT))
    helping = [row for row in rows.values() if row.helper_of == parent.id and row.is_open and row.owner in live]
    if len(helping) >= limit:
        raise MoveRefused(f"`{branch}` already has {len(helping)} live helper(s) "
                          f"({', '.join(row.owner for row in helping)}); the limit is fleet.helpers_per_seat = {limit}")
    short = "" if anyway else spawn.memory_short(ledger)
    if short:
        raise MoveRefused(short)
    tip = gitq.branch_tip(ledger.root, branch, run=ledger.run)
    if tip is None:
        raise MoveRefused(f"git could not resolve the tip of `{branch}`; a helper starts from a tip someone can name")
    taken = live | {name for row in rows.values() for name in (row.owner, row.reader) if name}
    name = names.next_names(post, 1, taken=taken, store=store, reserve=True, now=ledger.now())[0]
    seat = launch.seat_for(launch.main_checkout(ledger.root, run=ledger.run), post, name)
    try:
        return spawn.raise_seat(ledger, seat, caller=caller, census=census, wait=wait, poll=poll, sleep=sleep,
                                base=tip, fields={"helper_of": parent.id},
                                prompt=prompt_for(name, parent, actor.name, task, seat.tree))
    except spawn.SpawnRefused as err:
        raise MoveRefused(str(err)) from err


def done(ledger, actor: Actor, *, summary: str) -> tuple[Row, str]:
    """Record what the helper did and release its seat; the letter goes to the session that raised it."""
    summary = summary.strip()
    if not summary:
        raise MoveRefused("say what you did, or why it could not be done (--summary)")
    with ledger.session() as s:
        seat = next((row for row in s.rows.values()
                     if row.owner == actor.name and row.is_open and row.state == "reserved" and row.helper_of), None)
        if seat is None:
            raise MoveRefused(f"{actor.name} is not a helper with an open seat; `helper done` ends a helper's work")
        if seat.tree:
            listed = ledger.run(["git", "-C", seat.tree, "status", "--porcelain"], capture_output=True, text=True,
                                check=False)
            dirty = len([line for line in listed.stdout.splitlines() if line.strip()]) if listed.returncode == 0 \
                else 0
            if dirty:
                raise MoveRefused(f"{dirty} uncommitted in {seat.tree}: commit the work you keep (or remove what you "
                                  "do not), so the session you help gets all of it")
        parent = s.rows.get(seat.helper_of)
        tip = gitq.branch_tip(ledger.root, seat.branch, run=ledger.run) or ""
        state = s.next_state(seat, "release")
        released = s.append(actor, seat.id, "release", state,
                            evidence={"why": f"helped: {summary}", "helped": seat.helper_of, "tip": tip,
                                      "summary": summary})
    owner = parent.owner if parent is not None else "the session that raised you"
    letter = (f"letter for {owner} - send it with SendMessage; an idle background session is woken only by a "
              f"message:\n  {actor.name} finished helping `{parent.branch if parent else seat.helper_of}`: {summary} "
              f"(tip {tip[:7] or 'unknown'}).\n  Read it, merge {seat.branch} into your branch, then "
              f"`flotilla retire \"{actor.name}\"`.")
    return released, letter
