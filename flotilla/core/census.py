"""Live Claude Code sessions, asked of the supported interface `claude agents --json`.

Failure to ask raises CensusUnavailable and never returns an empty list: "nobody is alive" and
"could not ask" are different answers, and a caller that confuses them will adopt the work of a
session that is merely unreachable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, replace


class CensusUnavailable(RuntimeError):
    """The census could not be taken; the message says why."""


@dataclass(frozen=True)
class Session:
    name: str
    session_id: str
    kind: str
    pid: int | None
    short_id: str | None
    status: str | None
    state: str | None
    cwd: str
    started_at_ms: int | None
    held_on: str | None = None   # set only by held_at_start: the start-up question it is held on, "" unread


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_or_none(value) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_census(text: str) -> list[Session]:
    try:
        rows = json.loads(text)
    except ValueError as err:
        raise CensusUnavailable(f"`claude agents --json` did not return JSON: {err}") from err
    if not isinstance(rows, list):
        raise CensusUnavailable("`claude agents --json` did not return a list")
    sessions = []
    for row in rows:
        if not isinstance(row, dict) or not _str_or_none(row.get("sessionId")):
            raise CensusUnavailable(f"`claude agents --json` returned a row without sessionId: {row!r:.200}")
        sessions.append(Session(
            name=row.get("name") if isinstance(row.get("name"), str) else "",
            session_id=row["sessionId"],
            kind=row.get("kind") if isinstance(row.get("kind"), str) else "",
            pid=_int_or_none(row.get("pid")),
            short_id=_str_or_none(row.get("id")),
            status=_str_or_none(row.get("status")),
            state=_str_or_none(row.get("state")),
            cwd=row.get("cwd") if isinstance(row.get("cwd"), str) else "",
            started_at_ms=_int_or_none(row.get("startedAt")),
        ))
    return sessions


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # someone else's process: it exists
    return True


def drop_gone(sessions: list[Session], pid_alive=_pid_alive) -> list[Session]:
    """A background session the daemon retired can stay listed for minutes with no process behind it; it is gone.
    Gone: no status, and no pid or a pid that names no running process. Interactive sessions are kept as listed (H48)."""
    def gone(session: Session) -> bool:
        if session.kind != "background":
            return False
        if session.status is not None:
            return False   # a session that reports a status is running; a pid this process cannot see proves nothing
        return session.pid is None or not pid_alive(session.pid)
    return [session for session in sessions if not gone(session)]


def read_census(run=subprocess.run, claude: str = "claude", timeout: float = 30,
                pid_alive=_pid_alive) -> list[Session]:
    return drop_gone(parse_census(read_text(run, claude, timeout)), pid_alive)


#: A screen that asks the person to choose: Claude Code's start-up dialogs end with it (measured, 2.1.319).
DIALOG_MARK = "Enter to confirm"
#: A seat listed with no process for this long has not started, whatever its screen shows.
HELD_AFTER_S = 60
_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[78]")


def held_at_start(names, *, run=subprocess.run, claude: str = "claude", timeout: float = 30,
                  now_ms: int | None = None) -> list[Session]:
    """The sessions named in `names` that Claude Code lists with no process and no status while their screen
    still answers `claude logs`: held at start by a question nobody answers in the background. The row alone
    reads the same as a job long gone, which `drop_gone` drops; a gone job has no screen (field test 2026-11-07,
    S3: a project's `.mcp.json` asked every seat "New MCP server found in this project"). A screen that asks
    nothing is held only once the seat is HELD_AFTER_S old: before that it may still be starting."""
    if not names:
        return []
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    held = []
    for session in parse_census(read_text(run, claude, timeout)):
        if session.kind != "background" or session.name not in names or session.pid is not None \
                or session.status is not None or not session.short_id:
            continue
        try:
            shown = run([claude, "logs", session.short_id], capture_output=True, text=True, timeout=timeout,
                        check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if shown.returncode != 0:
            continue
        lines = [" ".join(line.split()) for line in _ESCAPE.sub(" ", shown.stdout or "").splitlines()]
        if any(DIALOG_MARK in line for line in lines):
            question = next((line for line in lines if any(ch.isalpha() for ch in line)), "")
            held.append(replace(session, held_on=question[:160]))
        elif session.started_at_ms is not None and now_ms - session.started_at_ms >= HELD_AFTER_S * 1000:
            held.append(replace(session, held_on=""))
    return held


def rows_of(text: str, claude: str = "claude") -> list:
    """The census rows as Claude Code wrote them, for checking their shape (`flotilla doctor`)."""
    try:
        rows = json.loads(text)
    except ValueError as err:
        raise CensusUnavailable(f"`{claude} agents --json` did not return JSON: {err}") from err
    if not isinstance(rows, list):
        raise CensusUnavailable(f"`{claude} agents --json` did not return a list")
    return rows


def read_text(run=subprocess.run, claude: str = "claude", timeout: float = 30) -> str:
    try:
        done = run([claude, "agents", "--json"], capture_output=True, text=True,
                   timeout=timeout, check=False)
    except FileNotFoundError as err:
        raise CensusUnavailable(f"`{claude}` is not on PATH") from err
    except OSError as err:
        raise CensusUnavailable(f"`{claude}` cannot be run: {err}") from err
    except subprocess.TimeoutExpired as err:
        raise CensusUnavailable(f"`{claude} agents --json` did not answer within {timeout:g}s") from err
    if done.returncode != 0:
        raise CensusUnavailable(
            f"`{claude} agents --json` exited {done.returncode}: {done.stderr.strip()[:200]}")
    return done.stdout
