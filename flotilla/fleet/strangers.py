"""Sessions in a seat's tree that are not the fleet's (H7).

A plugin hook can start a headless `claude` in a seat's tree (in the twosuns run, `security-guidance` did, under the
name `<tree>-<n>`), and a person can open one there by hand. Such a session works where a seat works but holds no
post: it is named for what it is and counted nowhere a seat is counted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

def label(tree: str, started: str = "") -> str:
    if started:
        return (f"a headless session a plugin hook started ({started}) in {tree}: it reviews the change it was "
                "started for and holds no post; not a fleet session")
    return f"not a fleet session (started in {tree})"


def started_by(session) -> str:
    """How a session was started, when Claude Code's registry says it was not by a person at a terminal: the
    entrypoint (`sdk-cli`, `sdk-py`, `sdk-ts`), or "" for the command line, an IDE, or when nothing can be read
    (worldcore field test W6). Asked of the registry entry Claude Code keeps for each running session,
    `<config>/sessions/<pid>.json` (measured on 2.1.288: `cli` at a terminal, `sdk-cli` for `claude -p`) - never of
    the session's transcript, which holds the conversation (Software Directory Policy: no reading chat history)."""
    if not session.pid or not session.session_id:
        return ""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    try:
        entry = json.loads((home / "sessions" / f"{int(session.pid)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    if not isinstance(entry, dict) or entry.get("sessionId") != session.session_id:
        return ""   # a reused pid names another session
    started = entry.get("entrypoint")
    return started[:20] if isinstance(started, str) and started.startswith("sdk") else ""


def seat_trees(rows: dict) -> list[Path]:
    """The trees of the open post rows: where the fleet's seats live."""
    return [Path(row.tree).resolve() for row in rows.values()
            if row.is_open and row.state == "reserved" and row.tree]


def in_seat_trees(sessions, rows: dict, post_of) -> list[tuple[object, str]]:
    """(session, its seat tree) for every named session whose working directory lies in a seat tree and whose name
    is no post's."""
    trees = seat_trees(rows)
    found = []
    for session in sessions:
        if not session.name or not session.cwd or post_of(session.name):
            continue
        where = Path(session.cwd).resolve()
        tree = next((tree for tree in trees if where == tree or tree in where.parents), None)
        if tree is not None:
            found.append((session, str(tree)))
    return found
