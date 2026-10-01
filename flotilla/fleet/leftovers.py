"""Background processes a retired seat left in its tree (field test H50).

A session that starts a dev server or a browser detached (`nohup ... &`, `setsid`) leaves it running after the
session stops: the process is re-parented to pid 1 and holds memory until someone finds it. In the twosuns run 27
`vite` servers of finished sessions were alive at once. Retire looks for them by two facts procfs gives: the
process's working directory is inside the seat's tree, and its parent is pid 1 — an orphan, not something a live
shell or session still owns. Each such orphan is stopped with everything under it, unless its subtree holds a
process with a terminal (a person's `tmux` server started in the tree) or a live census session, or the process
running this command. An orphan of another user, or one a systemd service runs (its cgroup is a `.service` unit: a
deployment's server, a CI runner), is never a leftover, whatever its directory. A process is signalled only while it
is still the one found: its start time is read again just before the signal, so a reused pid is left alone.

A session also works outside its tree: a background session has a job directory of its own under the Claude Code
configuration directory, `jobs/<its id>/`, and a session that copied trunk there to run a dev server leaves the
server's working directory in it (seen in twosuns: a `vite` in `jobs/cd30b6e8/tmp/trunk`). That directory is looked
at the same way, by the session's id; where it does not exist nothing is assumed about it.

Linux only: where there is no procfs the leftovers are not looked for, and the caller says so.
"""

from __future__ import annotations

import os
import re
import signal
from dataclasses import dataclass
from pathlib import Path

PROC_ROOT = Path("/proc")
CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


@dataclass(frozen=True)
class Leftover:
    pid: int
    command: str
    start: str   # field 22 of stat: what tells this process from a later one with its pid


@dataclass(frozen=True)
class _Proc:
    ppid: int
    tty: int
    cwd: str
    command: str
    start: str
    uid: int
    service: bool


def job_dir(short_id) -> tuple[Path | None, str]:
    """The background session's job directory, or None and why it is not looked at: no plain id, no such directory,
    or a link (a link could point anywhere, and the processes under its target are not this session's)."""
    if not short_id or not re.fullmatch(r"[0-9A-Za-z]{4,64}", str(short_id)):
        return None, f"`{short_id}` is not a plain session id"
    jobs = CONFIG_DIR / "jobs"
    found = jobs / str(short_id)
    if found.is_symlink():
        return None, f"{found} is a link"
    if not found.is_dir():
        return None, f"{found} does not exist"
    return found, ""


def _start(proc_root: Path, pid: int) -> str:
    try:
        stat = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        return stat[stat.rindex(")") + 2:].split()[19]
    except (OSError, ValueError, IndexError):
        return ""


def _uid(entry: Path) -> int:
    for line in (entry / "status").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Uid:"):
            return int(line.split()[1])
    raise ValueError("no Uid line")


def _service(entry: Path) -> bool:
    try:
        text = (entry / "cgroup").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True   # cannot tell where it runs: treated as not ours to stop
    return any(line.rsplit("/", 1)[-1].endswith(".service") for line in text.splitlines())


def _table(proc_root: Path) -> dict[int, _Proc]:
    table = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            fields = stat[stat.rindex(")") + 2:].split()
            cwd = os.readlink(entry / "cwd")
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
            table[int(entry.name)] = _Proc(int(fields[1]), int(fields[4]), cwd, command, fields[19], _uid(entry),
                                           _service(entry))
        except (OSError, ValueError, IndexError):
            continue   # gone since the listing, or not ours to read
    return table


def _inside(path: str, tree: Path) -> bool:
    where = Path(path)
    return where == tree or tree in where.parents


def _subtree(root: int, children: dict[int, list[int]]) -> list[int]:
    found, todo = [], [root]
    while todo:
        pid = todo.pop(0)
        found.append(pid)
        todo += sorted(children.get(pid, []))
    return found


def in_tree(tree, *, keep: set[int], proc_root: Path | None = None, me: int | None = None) -> list[Leftover] | None:
    """The orphans working in `tree`, each followed by what runs under it; None where procfs is absent."""
    proc_root = PROC_ROOT if proc_root is None else Path(proc_root)
    if not proc_root.is_dir():
        return None
    table = _table(proc_root)
    tree = Path(os.path.realpath(tree))
    children: dict[int, list[int]] = {}
    for pid, proc in table.items():
        children.setdefault(proc.ppid, []).append(pid)
    protected = set(keep)
    pid = os.getpid() if me is None else me
    while pid in table and pid not in protected:   # this command and every process above it
        protected.add(pid)
        pid = table[pid].ppid
    uid = os.getuid()
    found = []
    for root in sorted(table):
        orphan = table[root]
        if orphan.ppid != 1 or not _inside(orphan.cwd, tree) or orphan.service:
            continue
        subtree = [pid for pid in _subtree(root, children) if pid in table]
        if protected & set(subtree) or any(table[pid].tty or table[pid].uid != uid for pid in subtree):
            continue
        found += [Leftover(pid, table[pid].command, table[pid].start) for pid in subtree]
    return found


def stop(found: list[Leftover], *, kill=None, proc_root: Path | None = None) -> int:
    """SIGTERM each that is still the process found; how many were sent."""
    kill = os.kill if kill is None else kill
    proc_root = PROC_ROOT if proc_root is None else Path(proc_root)
    reached = 0
    for item in found:
        if not item.start or _start(proc_root, item.pid) != item.start:
            continue   # gone, or its pid now names another process
        try:
            kill(item.pid, signal.SIGTERM)
            reached += 1
        except (ProcessLookupError, PermissionError):
            continue
    return reached
