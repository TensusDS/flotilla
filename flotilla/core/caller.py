"""Whether a person stands behind this call (security review F2-F12).

Some commands act for the person: answering another session's permission question, recording the onboarding
answers that become shell commands, running them. A background session must not run them, and a process that only
looks like "outside any session" — one a session detached with `setsid`, whose parent chain reaches pid 1 — must not
pass for a person either. So: a call from inside a background session is refused; a call from an interactive
session is the person's; a call from outside any session counts as a person only with a terminal behind it.

Every session on the machine runs as the same user, so this stops an overeager or misled session, not a determined
program of that user's: a process detached with a pseudo-terminal of its own (`tmux new -d`, `setsid -f script`)
still looks like a person here. That wrapper has to get past the session's own permission mode first; the person's
gate stays Claude Code's permission prompt, which no skill pre-approves for these commands.
"""

from __future__ import annotations

import os

from flotilla.core.census import Session


def has_terminal() -> bool:
    """A controlling terminal: what a person's shell has and a detached process does not."""
    try:
        fd = os.open("/dev/tty", os.O_RDWR)
    except OSError:
        return False
    os.close(fd)
    return True


def sessions_above(sessions: list[Session], *, parent_of, start_pid: int | None = None,
                   max_depth: int = 64) -> list[Session]:
    """Every census session this process runs under, nearest first; [] outside any. A session can start another
    (an interactive `claude` inside a background one), so the nearest one alone does not say who stands behind."""
    by_pid = {item.pid: item for item in sessions if item.pid is not None}
    found, seen = [], set()
    pid = os.getpid() if start_pid is None else start_pid
    for _ in range(max_depth):
        if pid is None or pid <= 1 or pid in seen:
            break
        if pid in by_pid:
            found.append(by_pid[pid])
        seen.add(pid)
        pid = parent_of(pid)
    return found


def calling_sessions() -> list[Session]:
    """The census sessions this process runs under, nearest first. Raises CensusUnavailable."""
    from flotilla.core import platform as plat
    from flotilla.core.census import read_census
    source = plat.probe().parent_pid_source
    return sessions_above(read_census(timeout=10), parent_of=lambda pid: plat.parent_pid(pid, source))


def person_refusal(what: str) -> str:
    """Why this call is not a person's, or "" when it is. `what` completes "only a person ..."."""
    tail = f"only a person {what}, from their own Claude Code session or a terminal"
    try:
        chain = calling_sessions()
    except Exception as err:  # noqa: BLE001 - who calls is unknown: not a gate's to guess
        return f"could not tell who calls ({err}); {tail}"
    if not chain:
        return "" if has_terminal() else (
            f"this process runs outside any session and has no terminal, so it is not a person; {tail}")
    for session in chain:   # every session up the chain, not the nearest: a background one can start another
        if session.kind == "background":
            return f"`{session.name or session.short_id}` is a background session; {tail}"
        if session.kind != "interactive":
            return f"`{session.name or session.short_id}` is not a session a person works in ({session.kind!r}); {tail}"
    return ""
