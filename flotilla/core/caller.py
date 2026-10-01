"""Whether a person stands behind this call (security review F2-F12).

Some commands act for the person: answering another session's permission question, recording the onboarding
answers that become shell commands, running them. A background session must not run them, and a process that only
looks like "outside any session" — one a session detached with `setsid`, whose parent chain reaches pid 1 — must not
pass for a person either. So: a call from inside a background session is refused; a call from an interactive
session is the person's; a call from outside any session counts as a person only with a terminal behind it.

Every session on the machine runs as the same user, so this stops an overeager or misled session, not a determined
program of that user's; the person's own gate stays Claude Code's permission prompt, which no skill pre-approves for
these commands.
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


def calling_session() -> Session | None:
    """The census session this process runs under; None outside any. Raises CensusUnavailable."""
    from flotilla.core import platform as plat
    from flotilla.core.census import read_census
    from flotilla.core.identity import find_calling_session
    source = plat.probe().parent_pid_source
    return find_calling_session(read_census(timeout=10), parent_of=lambda pid: plat.parent_pid(pid, source))


def person_refusal(what: str) -> str:
    """Why this call is not a person's, or "" when it is. `what` completes "only a person ..."."""
    try:
        session = calling_session()
    except Exception as err:  # noqa: BLE001 - who calls is unknown: not a gate's to guess
        return (f"could not tell who calls ({err}); only a person {what}, from their own Claude Code session or a "
                f"terminal")
    if session is None:
        return "" if has_terminal() else (
            f"this process runs outside any session and has no terminal, so it is not a person; only a person "
            f"{what}, from their own Claude Code session or a terminal")
    if session.kind == "background":
        return (f"`{session.name or session.short_id}` is a background session; only a person {what}, from their own "
                f"Claude Code session or a terminal")
    return ""
