"""Dead seats, gc design 4 e, g and i. A seat - a post row (`reserved`) or a finished helper's seat - whose session the
census no longer lists, and whose tree still exists. What holds the tree decides what happens, and no case is silent
(the person's call, 2026-10-31):

- nothing works in it: retired exactly as `flotilla fleet retire` does for a session already gone;
- only zombies: the same. A zombie has exited - it holds no memory, no files and no directory, and no signal reaches
  it (measured on Linux: its `cwd` is unreadable with ENOENT, SIGTERM leaves it `Z`, it has no `VmRSS`) - so it is
  never seen as working in a tree;
- processes work in it: not retired; one question per seat, stop or keep, with each process's evidence and an
  assessment of whether it is still needed. Only a recorded `stop` stops anything, and only the processes shown;
- processes cannot be read (no /proc, as on macOS): asked as retire or keep.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from flotilla.core.text import visible
from flotilla.fleet import leftovers, retire
from flotilla.gc import trees
from flotilla.gc.questions import Item
from flotilla.lane.procs import CLK_TCK

BUSY_SHARE = 0.01   # on the first look: a process that used over 1 % of a CPU over its life may still be working
BUSY_SECONDS = 1.0  # since the last look: more than a second of CPU


@dataclass(frozen=True)
class Holder:
    pid: int
    start: str
    command: str
    age_s: int
    cpu_s: float
    cpu_since_s: float | None
    since_h: int | None
    rss_mb: int | None
    state: str
    orphan: bool
    tty: bool
    ours: bool
    service: bool
    parent: int


@dataclass(frozen=True)
class Seat:
    row: object
    holders: list[Holder] | None
    zombie_children: tuple[int, ...] = ()


def _rss_mb(entry: Path) -> int | None:
    try:
        for line in (entry / "status").read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def holders(tree, *, proc_root=None, data: dict | None = None, now: float | None = None) -> list[Holder] | None:
    """Every process whose cwd is in `tree`; None where procfs cannot be read. A zombie has no cwd and is never one.
    `data["holders"]` keeps each process's CPU at a look at least a day old, so the next look can say what it used
    since."""
    proc_root = leftovers.PROC_ROOT if proc_root is None else Path(proc_root)
    working = leftovers.working_in(tree, proc_root=proc_root)
    if working is None:
        return None
    now = time.time() if now is None else now
    ticks = leftovers.now_ticks(proc_root)
    seen = {} if data is None else data.setdefault("holders", {})
    me = os.getuid()
    found = []
    for pid, command in working:
        entry = proc_root / str(pid)
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            fields = stat[stat.rindex(")") + 2:].split()
            uid = leftovers._uid(entry)
        except (OSError, ValueError, IndexError):
            continue   # gone since the look
        state, ppid, tty, start = fields[0], int(fields[1]), int(fields[4]), fields[19]
        cpu = (int(fields[11]) + int(fields[12])) / CLK_TCK
        age = max(0, (ticks - int(start)) // CLK_TCK) if ticks is not None and start.isdigit() else 0
        key = f"{pid}:{start}"
        before = seen.get(key)
        since = since_h = None
        if isinstance(before, dict) and isinstance(before.get("cpu"), (int, float)) \
                and isinstance(before.get("at"), (int, float)):
            since, since_h = max(0.0, cpu - before["cpu"]), int((now - before["at"]) // 3600)
        if not isinstance(before, dict) or now - before.get("at", now) >= 86400:
            seen[key] = {"cpu": cpu, "at": now}
        found.append(Holder(pid, start, command, int(age), cpu, since, since_h, _rss_mb(entry), state, ppid == 1,
                            tty != 0, uid == me, leftovers._service(entry), ppid))
    return found


def _zombie_children(proc_root, parents: set[int]) -> tuple[int, ...]:
    proc_root = leftovers.PROC_ROOT if proc_root is None else Path(proc_root)
    found = []
    try:
        entries = [entry for entry in proc_root.iterdir() if entry.name.isdigit()]
    except OSError:
        return ()
    for entry in entries:
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            fields = stat[stat.rindex(")") + 2:].split()
        except (OSError, ValueError, IndexError):
            continue
        if fields[0] == "Z" and int(fields[1]) in parents:
            found.append(int(entry.name))
    return tuple(sorted(found))


def dead_seats(ledger, sessions, *, proc_root=None, data: dict | None = None, now: float | None = None) -> list[Seat]:
    """Seats whose session the census does not list and whose tree is a directory no live session holds, by owner."""
    live = {item.name for item in sessions if item.name}
    rows = retire.post_rows(ledger)
    helpers_seen = sorted({row.owner for row in ledger.rows().values() if row.helper_of and row.owner})
    rows += [seat for seat in (retire.finished_helper_seat(ledger, name) for name in helpers_seen) if seat]
    found = []
    for row in sorted(rows, key=lambda item: item.owner):
        if row.owner in live or not row.tree or not Path(row.tree).is_dir():
            continue
        here = Path(row.tree).resolve()
        if trees._alive(ledger, here, sessions, {row.id}, None):   # adopted work, or a live session in it
            continue
        held = holders(here, proc_root=proc_root, data=data, now=now)
        zombies = _zombie_children(proc_root, {item.pid for item in held}) if held else ()
        found.append(Seat(row, held, zombies))
    return found


def assess(found: list[Holder]) -> str:
    """Whether the processes in a dead seat's tree look still needed: the first that applies."""
    foreign = [item for item in found if not item.ours or item.service]
    if foreign:
        who = foreign[0]
        return (f"not ours to stop: process {who.pid} is "
                + ("a system service" if who.service else "another user's"))
    if any(item.tty for item in found):
        line = "someone may be using it: it has a terminal"
    elif any(item.cpu_since_s is not None and item.cpu_since_s > BUSY_SECONDS for item in found):
        busiest = max(found, key=lambda item: item.cpu_since_s or 0)
        line = f"may still be working: it used {busiest.cpu_since_s:.0f} s of CPU over the last {busiest.since_h} h"
    elif any(item.cpu_since_s is None and item.age_s and item.cpu_s / item.age_s > BUSY_SHARE for item in found):
        line = "may still be working: it has used over 1 % of a CPU over its life"
    else:
        waits = "nothing waits for it" if all(item.orphan for item in found) else "its parent still runs"
        line = f"likely not needed: its session is gone, {waits}, and it has been idle"
    if any(item.state == "D" for item in found):
        line += "; in an uninterruptible wait, a stop may not reach it"
    return line


def _age(seconds: int) -> str:
    return f"{seconds // 3600} h" if seconds >= 3600 else f"{seconds // 60} min"


def _evidence(item: Holder) -> str:
    since = "first look" if item.cpu_since_s is None else f"{item.cpu_since_s:.1f} s over the last {item.since_h} h"
    traits = [word for word, on in (("orphan", item.orphan), ("terminal", item.tty), ("service", item.service),
                                    ("another user's", not item.ours)) if on]
    memory = f"{item.rss_mb} MB" if item.rss_mb is not None else "memory unknown"
    return (f"{item.pid} {visible(item.command[:80])}: up {_age(item.age_s)}, {item.cpu_s:.1f} s CPU ({since}), "
            f"{memory}, state {item.state}" + (f", {', '.join(traits)}" if traits else ""))


def seat_items(seats: list[Seat]) -> list[Item]:
    """One question per seat something holds or whose processes cannot be read."""
    found = []
    for seat in seats:
        owner, tree = seat.row.owner, str(seat.row.tree)
        if seat.holders is None:
            found.append(Item(f"seat:{owner}", trees=[tree], proposed="", offered=["retire", "keep"],
                              evidence=[f"{visible(owner)}'s session is gone; this machine does not show processes, "
                                        f"so whether something still works in {visible(tree)} cannot be told - "
                                        "check with `ps`"],
                              shown=[owner, "processes unreadable"]))
            continue
        if not seat.holders:
            continue
        line = assess(seat.holders)
        mine = not line.startswith("not ours")
        evidence = [f"{visible(owner)}'s session is gone and {len(seat.holders)} process(es) work in its tree: "
                    + line] + [_evidence(item) for item in seat.holders]
        if seat.zombie_children:
            evidence.append(f"zombie(s) {', '.join(map(str, seat.zombie_children))}: exited, holding nothing; gone "
                            "when their parent above is stopped")
        found.append(Item(f"seat:{owner}", trees=[tree], proposed="stop" if line.startswith("likely") else "keep",
                          offered=["stop", "keep"] if mine else ["keep"], evidence=evidence,
                          shown=[owner, sorted([item.pid, item.start] for item in seat.holders)]))
    return found


def retire_dead(ledger, seats: list[Seat], *, census, caller: str) -> list[str]:
    """Retire every seat nothing holds; the others wait on the person's answer (`seat_items`)."""
    free = [seat for seat in seats if seat.holders == []]
    lines, done = [], 0
    for seat in free:
        try:
            lines += retire.retire(ledger, seat.row.owner, caller=caller, census=census)
            done += 1
        except retire.RetireRefused as err:
            lines.append(f"kept {seat.row.owner}: {err}")
    return ([f"dead seats: {done} retired"] if done else []) + lines
