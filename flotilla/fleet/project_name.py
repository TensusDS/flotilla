"""The fleet's name on this machine: the word a project's seat names start with (`worldcore-orchestrator 1`).

Session names are machine-wide addresses - messages are delivered by name - so two projects must not share the word.
The profile names the fleet (`[fleet] name`, written at onboarding from the project's directory); a machine-wide
journal records which project holds which word, and a second project that wants a taken one gets `-2`, `-3`.
Readable on purpose: a hash would be unique and unreadable (the person's request, 2026-10-01). A project is known by
its repository key, so two checkouts of one origin are one project and share the word and the numbers.
"""

from __future__ import annotations

import re

KEY = "projects"


def clean(raw: str) -> str:
    """The name as one address word: letters, digits, `.`, `_` and `-`; anything else becomes `-`."""
    word = re.sub(r"[^A-Za-z0-9._-]+", "-", str(raw).strip()).strip("-.")
    return word or "project"


def resolve(store, repo_key: str, wanted: str) -> tuple[str, str]:
    """(the word this project's seats are named with, a note when it is not the one the profile wants)."""
    wanted = clean(wanted)
    records = store.read(KEY).records
    for record in records:
        if record.get("repo") == repo_key and record.get("wanted") == wanted:
            return record["name"], _note(wanted, record["name"])
    with store.transaction(KEY) as tx:
        records = tx.read().records
        for record in records:   # another caller may have claimed it since the read above
            if record.get("repo") == repo_key and record.get("wanted") == wanted:
                return record["name"], _note(wanted, record["name"])
        taken = {record.get("name") for record in records if record.get("repo") != repo_key}
        name, number = wanted, 1
        while name in taken:
            number += 1
            name = f"{wanted}-{number}"
        tx.append({"repo": repo_key, "wanted": wanted, "name": name})
    return name, _note(wanted, name)


def _note(wanted: str, name: str) -> str:
    if name == wanted:
        return ""
    return (f"the fleet name `{wanted}` is taken on this machine by another project, so this fleet's seats are "
            f"named `{name}-...`; set `[fleet] name` in .flotilla/project.toml to choose another")
