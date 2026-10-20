"""The organize guard: only `flotilla organize apply` changes a pull request organize or the ledger tracks (spec
2026-10-19-organize-design, section 4).

In auto mode a command left out of a skill's `allowed-tools` goes to the classifier, not to the person, so the
person's recorded answer is the authority and flotilla's own `apply` carries it out. This guard refuses the same
moves typed by a session: `gh pr close`, `gh pr edit --base`, a non-GET `gh api` call on a stack endpoint, and the
`gh stack` commands that push, merge or unstack. A `gh pr` door is refused when its pull request is one a ledger row
names (its own, or one it closes once it ships) or an open organize answer names, and when it names none - the
current branch's pull request cannot be told from the text. Stack calls are refused outright: which pull requests a
stack holds cannot be told from the text either, nor a GraphQL mutation's target, nor a word the shell expands. A
write to a pull request through the REST API (`gh api -X PATCH .../pulls/<n>`) is judged like `gh pr edit`. Where the
ledger cannot be read, a door is refused. What no text guard sees - a gh alias, `sh -c '...'`, a script - is the
guards' common ceiling (`flotilla guard --help`); it is not closed here.
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


#: What the shell turns into other words before gh sees them: variables, substitutions, globs, brace expansion
#: (`{a,b}`, `{1..3}` - never gh's own `{owner}`) and extglobs.
EXPANDS = re.compile(r"[$`*?\[]|\{[^{}]*(?:,|\.\.)|[@+!]\(")
#: Makes several words of one whatever it holds; the closing brace is not asked for - the splitter may drop it.
SPLITS = re.compile(r"\{[^{}]*(?:,|\.\.)|[@+!]\(")
PLACEHOLDER = re.compile(r"\{(?:owner|repo|branch)\}")   # gh's own, filled in by gh, not by the shell


def _unreadable(words) -> bool:
    """Whether gh or the shell would read these words otherwise than a word-by-word scan does: any short-flag group
    (`-iX`, `-Rc`, `-XPATCH` - pflag reads letters and attached values a scan cannot), or any brace but gh's own
    placeholders. Refused as a class rather than decoded one spelling at a time (security review of 763a240)."""
    return any((word.startswith("-") and not word.startswith("--") and len(word) > 2) or
               "{" in PLACEHOLDER.sub("", word) for word in words)
ISSUE = re.compile(r"/(?:pulls|issues)/([^/?#]*)")   # what follows is read as a number, or refused


def _api_path(words: list[str]) -> str:
    """The endpoint `gh api` calls, as one spelling: no scheme or host, no leading slash, no query or fragment,
    lowercased - so `/graphql`, `https://api.github.com/repos/...` and `PULLS` are judged as what they reach
    (security review of 4170045)."""
    raw = _target(words, API_VALUES) or ""
    path = re.sub(r"^(?:https?://[^/]+)?/*", "", raw, flags=re.IGNORECASE)
    return re.split(r"[?#]", path, maxsplit=1)[0].rstrip("/").lower()


def _method(words: list[str]) -> str:
    """The HTTP method `gh api` sends: the last one given, as gh reads its flags; POST when fields are given and no
    method is (security review of 8e5b605..d2a55bd: the first one was read)."""
    method = None
    for i, word in enumerate(words):
        if word in ("-X", "--method") and i + 1 < len(words):
            method = words[i + 1]
        elif word.startswith("--method="):
            method = word.split("=", 1)[1]
        elif word.startswith("-X") and len(word) > 2:
            method = word[2:]
    if method is None and any(word in ("-f", "--raw-field", "-F", "--field", "--input") or
                              word.startswith(("--raw-field=", "--field=", "--input=")) for word in words):
        method = "POST"
    return (method or "GET").upper()


def door(segment) -> str:
    """"" when the segment is no door this guard judges; else its kind: `pr` (gh pr close / edit --base),
    `api-pull` (a write to a pull request or issue through the REST API), `api` (a stack call, or a GraphQL mutation)
    or `stack` (gh stack that pushes, merges or unstacks)."""
    words = list(segment.words)
    if segment.program != "gh" or len(words) < 2:
        return ""
    if words[1] == "pr" and len(words) >= 3 and words[2] in ("close", "edit"):
        if _unreadable(words[3:]):
            return "api"   # refused outright: which pull request, or whether --base is given, cannot be told
        if words[2] == "edit" and not any(word in ("-B", "--base") or word.startswith("--base=") for word in words):
            return ""
        return "pr"
    if words[1] == "api":
        judged = words[2:]
        if (_api_path(words[2:]) or "").endswith("graphql"):   # a query's own braces are its text, not the shell's
            fields = ("-f", "-F", "--field", "--raw-field")
            judged = [word for i, word in enumerate(words[2:]) if not (i and words[2:][i - 1] in fields)
                      and not word.startswith(("--field=", "--raw-field="))]
        if _unreadable(judged) or any(SPLITS.search(word) for word in judged):
            return "api"   # what gh would read cannot be told by this scan: refused, not guessed (review of bf2a47a)
        path = _api_path(words[2:])
        if path == "graphql" or path.endswith("/graphql"):   # a query read from a file or stdin cannot be told from the text (push review)
            hidden = any("=@" in word or word == "--input" or word.startswith("--input=") or EXPANDS.search(word)
                         for word in words[2:])
            return "api" if hidden or "mutation" in " ".join(words).lower() else ""
        if _method(words[2:]) == "GET":
            return ""
        raw = _target(words[2:], API_VALUES) or ""
        if EXPANDS.search(raw) or "%" in raw or any(part in (".", "..") for part in path.split("/")) or \
                "//" in path:
            return "api"   # a write whose endpoint the text does not spell plainly is refused, not decoded (6d55101)
        if "/stacks" in f"/{path}":
            return "api"
        return "api-pull" if ISSUE.search(f"/{path}") else ""
    if words[1] == "stack" and len(words) >= 3 and words[2] in STACK_DOORS:
        return "stack"
    return ""


def check(segment, *, rows: dict, numbers: set[int]) -> Finding | None:
    kind = door(segment)
    if not kind:
        return None
    refused = Finding(GUARD, True, REFUSAL.format(command=" ".join(segment.words)[:120]))
    words = list(segment.words)
    if kind in ("api", "stack") or any(EXPANDS.search(word) for word in words):
        return refused   # a stack's members, a mutation's target, a word the shell rewrites: none told by the text
    if kind == "api-pull":
        target = ISSUE.search(f"/{_api_path(words[2:])}").group(1)
        if not target.isdigit():
            return refused   # `pulls/$N`, `pulls/forty-three`: which pull request cannot be told from the text
    else:
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
