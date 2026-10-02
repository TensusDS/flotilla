"""Sessions in a seat's tree that are not the fleet's (H7).

A plugin hook can start a headless `claude` in a seat's tree (in the twosuns run, `security-guidance` did, under the
name `<tree>-<n>`), and a person can open one there by hand. Such a session works where a seat works but holds no
post: it is named for what it is and counted nowhere a seat is counted.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

#: How many records of a transcript are read for its entrypoint: it is on the first ones.
HEAD = 20


def label(tree: str, started: str = "") -> str:
    if started:
        return (f"a headless session a plugin hook started ({started}) in {tree}: it reviews the change it was "
                "started for and holds no post; not a fleet session")
    return f"not a fleet session (started in {tree})"


def project_slug(cwd: str) -> str:
    """The folder Claude Code keeps a session's transcript in, under its projects directory: the working directory
    with every character but letters and digits turned into `-` (measured on 2.1.287)."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def started_by(session) -> str:
    """How a session was started, when its transcript says it was not by a person at a terminal: the entrypoint
    (`sdk-py`, `sdk-ts`), or "" for the command line or when nothing can be read (worldcore field test W6)."""
    if not session.cwd or not session.session_id:
        return ""
    home = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    path = home / "projects" / project_slug(session.cwd) / f"{session.session_id}.jsonl"
    try:
        with open(path, encoding="utf-8", errors="replace") as transcript:
            for _, line in zip(range(HEAD), transcript):
                try:
                    entry = json.loads(line).get("entrypoint")
                except (ValueError, AttributeError):
                    continue
                if isinstance(entry, str) and entry:   # the SDK is how a hook starts one; an IDE is a person
                    return entry[:20] if entry.startswith("sdk") else ""
    except OSError:
        return ""
    return ""


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
