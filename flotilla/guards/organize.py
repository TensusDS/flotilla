"""The organize guard: only `flotilla organize apply` changes a pull request organize or the ledger tracks (spec
2026-10-19-organize-design, section 4).

In auto mode a command left out of a skill's `allowed-tools` goes to the classifier, not to the person, so the
person's recorded answer is the authority and flotilla's own `apply` carries it out. This guard refuses the same
moves typed by a session: `gh pr close`, `gh pr edit --base`, a non-GET `gh api` call on a stack endpoint, and the
`gh stack` commands that push, merge or unstack. A `gh pr` door is refused when its pull request is one a ledger row
names (its own, or one it closes once it ships) or an open organize answer names, and when it names none - the
current branch's pull request cannot be told from the text. Stack calls are refused outright: which pull requests a
stack holds cannot be told from the text either. Where the ledger cannot be read, a door is refused.
"""

from __future__ import annotations

import re
from pathlib import Path

from flotilla.guards import Finding

GUARD = "organize"
STACK_DOORS = ("push", "submit", "link", "merge", "unstack", "delete", "sync")
VALUES = {
    "close": ("-c", "--comment", "-R", "--repo"),
    "edit": ("-t", "--title", "-b", "--body", "-F", "--body-file", "-B", "--base", "--add-assignee",
             "--remove-assignee", "--add-label", "--remove-label", "--add-project", "--remove-project",
             "--add-reviewer", "--remove-reviewer", "-m", "--milestone", "-R", "--repo"),
}
API_VALUES = ("-X", "--method", "-f", "--raw-field", "-F", "--field", "-H", "--header", "--input", "-q", "--jq",
              "-t", "--template", "--hostname", "--cache", "-p", "--preview")
PULL = re.compile(r"/pull/(\d+)")
REFUSAL = ("`{command}` changes a pull request organize or the ledger tracks; only `flotilla organize apply` acts on "
           "it, from the person's recorded answer")


def tracked(root: Path) -> tuple[dict, set[int]]:
    """The ledger's rows and the pull request numbers open organize answers name."""
    from flotilla.core import paths, repo
    from flotilla.ledger.commands import open_ledger
    from flotilla.organize import store
    rows = open_ledger(Path(root)).rows()
    numbers = store.numbers(store.load(paths.state_dir(), repo.identify(Path(root)).key))
    return rows, numbers


def _target(words: list[str], values: tuple) -> str | None:
    i = 0
    while i < len(words):
        word = words[i]
        if word in values:
            i += 2
            continue
        if not word.startswith("-"):
            return word
        i += 1
    return None


def door(segment) -> str:
    """"" when the segment is no door this guard judges; else its kind: `pr`, `api` or `stack`."""
    words = list(segment.words)
    if segment.program != "gh" or len(words) < 2:
        return ""
    if words[1] == "pr" and len(words) >= 3 and words[2] in ("close", "edit"):
        if words[2] == "edit" and not any(word in ("-B", "--base") or word.startswith("--base=") for word in words):
            return ""
        return "pr"
    if words[1] == "api":
        method = next((words[i + 1] for i, word in enumerate(words[:-1]) if word in ("-X", "--method")), None)
        method = method or next((word.split("=", 1)[1] for word in words if word.startswith("--method=")), None)
        method = method or next((word[2:] for word in words if word.startswith("-X") and len(word) > 2), None)
        if method is None and any(word in ("-f", "--raw-field", "-F", "--field", "--input") for word in words):
            method = "POST"   # gh sends fields with POST unless told otherwise
        path = _target(words[2:], API_VALUES) or ""
        return "api" if "/stacks" in path and (method or "GET").upper() != "GET" else ""
    if words[1] == "stack" and len(words) >= 3 and words[2] in STACK_DOORS:
        return "stack"
    return ""


def check(segment, *, rows: dict, numbers: set[int]) -> Finding | None:
    kind = door(segment)
    if not kind:
        return None
    refused = Finding(GUARD, True, REFUSAL.format(command=" ".join(segment.words)[:120]))
    if kind in ("api", "stack"):
        return refused
    words = list(segment.words)
    target = _target(words[3:], VALUES[words[2]])
    if target is None:
        return refused   # the current branch's pull request: which one cannot be told from the text
    named = PULL.search(target)
    number = named.group(1) if named else target.lstrip("#")
    known = set(numbers)
    for row in rows.values():
        if str(row.pr).isdigit():
            known.add(int(row.pr))
        known.update(int(n) for n in row.closes or [])
    if number.isdigit():
        return refused if int(number) in known else None
    branches = {row.branch for row in rows.values() if row.pr}
    return refused if target in branches else None
