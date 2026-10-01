"""Sessions in a seat's tree that are not the fleet's (H7).

A plugin hook can start a headless `claude` in a seat's tree (in the twosuns run, `security-guidance` did, under the
name `<tree>-<n>`), and a person can open one there by hand. Such a session works where a seat works but holds no
post: it is named for what it is and counted nowhere a seat is counted.
"""

from __future__ import annotations

from pathlib import Path


def label(tree: str) -> str:
    return f"not a fleet session (started in {tree})"


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
