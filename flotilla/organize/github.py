"""Every question and action organize puts to GitHub, through `gh`.

A `gh` that cannot answer raises `GitHubUnknown`: "could not ask" is never read as "nothing there". The stack calls
use the REST endpoints GitHub's native stacks have had since 2026-10-06 - the ones the `gh-stack` extension calls - so
the extension is not needed (measured on a scratch repository, 2026-10-20: the list, and `POST .../unstack` answering
204 and leaving every open member on its old base).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

LIMIT = 1000
TIMEOUT = 30
FIELDS = "number,headRefName,headRefOid,baseRefName,author,isDraft,isCrossRepository,title,body"


class GitHubUnknown(RuntimeError):
    """gh could not answer: not the same as an empty answer."""


@dataclass(frozen=True)
class Pull:
    number: int
    branch: str
    head: str
    base: str
    author: str
    draft: bool
    fork: bool
    title: str
    body: str


@dataclass(frozen=True)
class Stack:
    number: int
    base: str
    members: tuple[int, ...]   # its open pull requests, bottom first


def _gh(argv: list[str], run) -> str:
    try:
        done = run(["gh", *argv], capture_output=True, text=True, check=False, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise GitHubUnknown(f"could not run gh: {err}") from err
    if done.returncode != 0:
        raise GitHubUnknown((done.stderr or done.stdout or "gh failed").strip()[:300])
    return done.stdout or ""


def _json(text: str, what: str):
    try:
        return json.loads(text or "null")
    except ValueError as err:
        raise GitHubUnknown(f"gh's answer about {what} is not JSON") from err


def slug_of(root: Path, run=subprocess.run) -> str | None:
    """`owner/name` of the repository's origin on GitHub, or None off GitHub."""
    from flotilla.core import repo
    from flotilla.onboard.detect_ci import github_slug
    ident = repo.identify(Path(root), run=run)
    if not ident.origin:
        return None
    return github_slug(repo.normalize_origin(ident.origin, base=ident.common_dir.parent))


def pulls(slug: str, run=subprocess.run) -> tuple[list[Pull], bool]:
    """The open pull requests, and whether the list was cut at `LIMIT`."""
    listed = _json(_gh(["pr", "list", "-R", slug, "--state", "open", "--limit", str(LIMIT), "--json", FIELDS], run),
                   "pull requests")
    if not isinstance(listed, list):
        raise GitHubUnknown("gh's list of pull requests is not a list")
    try:
        found = [Pull(number=int(item["number"]), branch=str(item["headRefName"]), head=str(item["headRefOid"]),
                      base=str(item["baseRefName"]), author=str((item.get("author") or {}).get("login") or ""),
                      draft=bool(item.get("isDraft")), fork=bool(item.get("isCrossRepository")),
                      title=str(item.get("title") or ""), body=str(item.get("body") or "")) for item in listed]
    except (KeyError, TypeError, ValueError) as err:
        raise GitHubUnknown(f"gh's list of pull requests could not be read: {err}") from err
    return found, len(found) >= LIMIT


def stacks(slug: str, run=subprocess.run) -> list[Stack]:
    """The open native stacks, each with its open members bottom first."""
    listed = _json(_gh(["api", "--paginate", f"repos/{slug}/stacks"], run), "stacks")
    if not isinstance(listed, list):
        raise GitHubUnknown("gh's list of stacks is not a list")
    found = []
    for item in listed:
        if not isinstance(item, dict) or not item.get("open"):
            continue
        members = tuple(int(member["number"]) for member in item.get("pull_requests") or []
                        if isinstance(member, dict) and member.get("state") == "open")
        if members:
            found.append(Stack(number=int(item["number"]), base=str((item.get("base") or {}).get("ref") or ""),
                               members=members))
    return found


def login(run=subprocess.run) -> str:
    """The GitHub login gh is signed in as: the person's."""
    said = _gh(["api", "user", "--jq", ".login"], run).strip()
    if not said:
        raise GitHubUnknown("gh named no login")
    return said


def pull_now(slug: str, number: int, run=subprocess.run) -> dict:
    """{"state", "head", "base"} of one pull request, as GitHub says now."""
    view = _json(_gh(["pr", "view", str(number), "-R", slug, "--json", "state,headRefOid,baseRefName"], run),
                 f"PR #{number}")
    if not isinstance(view, dict):
        raise GitHubUnknown(f"gh's answer about PR #{number} is not an object")
    return {"state": view.get("state") or "", "head": view.get("headRefOid") or "", "base": view.get("baseRefName") or ""}


def unstack(slug: str, number: int, run=subprocess.run) -> None:
    _gh(["api", "-X", "POST", f"repos/{slug}/stacks/{number}/unstack"], run)


def close(slug: str, number: int, comment: str, run=subprocess.run) -> None:
    _gh(["pr", "close", str(number), "-R", slug, "--comment", comment], run)
