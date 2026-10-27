"""What holds a commit, and where a branch has landed (gc design, sections 5.1 and 5.2).

A commit is held when any ref reaches it - in every namespace, the person's own `refs/attic/*` and flotilla's
`refs/flotilla/parked/*` included - other than the refs this pass deletes. Holders go to git on stdin: a field
repository had 900 refs, more than an argument list should carry.

A branch has landed when a trunk contains its tip: origin's trunk as origin answers now (not the local
remote-tracking ref, which a seat can move), the local trunk in local flow, where there is no origin to ask, or a
trunk the project declares (`[gc] trunks`, a long-lived line its branches are cut from) at its local tip. A squash
the ledger shipped exactly has landed too. Landing on a trunk origin does not hold, or by a squash whose tip is on
no ref, parks the branch's tip when the branch goes.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Trunk:
    name: str
    sha: str
    on_origin: bool


@dataclass(frozen=True)
class Landing:
    how: str        # "trunk" or "shipped"
    trunk: str
    park: bool      # the tip is pinned when the branch goes: no copy of it on origin


def _git(ledger, *args, input=None) -> subprocess.CompletedProcess:
    return ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False,
                      input=input)


def _present(ledger, sha: str) -> bool:
    return _git(ledger, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def origin_tip(ledger, name: str) -> tuple[str | None, str]:
    """(origin's tip of branch `name`, present here - fetched if it was not) or (None, why not)."""
    listed = _git(ledger, "ls-remote", "origin", f"refs/heads/{name}")
    sha = listed.stdout.split()[0] if listed.returncode == 0 and listed.stdout.split() else ""
    if not sha:
        return None, f"origin could not be asked for its trunk `{name}`"
    if not _present(ledger, sha):
        _git(ledger, "fetch", "-q", "origin", name)
        if not _present(ledger, sha):
            return None, f"origin's trunk {sha[:7]} is not here and could not be fetched"
    return sha, ""


def _local_tip(ledger, name: str) -> str:
    return _git(ledger, "rev-parse", "--verify", "-q", f"refs/heads/{name}^{{commit}}").stdout.strip()


def _contains(ledger, outer: str, inner: str) -> bool:
    return _git(ledger, "merge-base", "--is-ancestor", inner, outer).returncode == 0


def _origin_on_this_disk(ledger) -> str:
    url = _git(ledger, "remote", "get-url", "origin")
    where = url.stdout.strip() if url.returncode == 0 else ""
    if where.startswith("file://") or where.startswith("/") or where.startswith("."):
        return f"origin is on this machine (`{where}`): it is no copy off this disk"
    return ""


def trunks(ledger) -> tuple[list[Trunk], list[str]]:
    """The profile's trunk first, then the declared ones, with what the report says about them."""
    found, warnings = [], []
    near = _origin_on_this_disk(ledger)
    if near:
        warnings.append(near)
    if ledger.mode == "local":
        sha = _local_tip(ledger, ledger.trunk)
        if sha:
            found.append(Trunk(ledger.trunk, sha, False))
        else:
            warnings.append(f"the trunk `{ledger.trunk}` is not a branch here")
    else:
        sha, why = origin_tip(ledger, ledger.trunk)
        if sha is None:
            warnings.append(why)
        else:
            found.append(Trunk(ledger.trunk, sha, True))
    declared = (ledger.profile.get("gc") or {}).get("trunks") or []
    for name in [str(item) for item in declared if str(item) != ledger.trunk]:
        sha = _local_tip(ledger, name)
        if not sha:
            warnings.append(f"the declared trunk `{name}` is not a branch here")
            continue
        remote, _ = origin_tip(ledger, name)
        on_origin = bool(remote) and _contains(ledger, remote, sha)
        if not on_origin:
            count = _git(ledger, "rev-list", "--count", sha, *([f"^{remote}"] if remote else [])).stdout.strip()
            warnings.append(f"`{name}`: {count or 'some'} commits only on this disk")
        found.append(Trunk(name, sha, on_origin))
    return found, warnings


def shipped_tips(ledger) -> set[str]:
    """Tips the ledger SHIPPED - a ship move is checked against origin; a close alone is not (a local flow closes
    after landing on a local trunk; review of the cleanup, I4)."""
    return {row.tip for row in ledger.rows().values()
            if row.tip and any(step.get("move") == "ship" for step in row.history)}


def landing(ledger, tip: str, trunks: list[Trunk], shipped: set[str]) -> Landing | None:
    for trunk in trunks:
        if _contains(ledger, trunk.sha, tip):
            return Landing("trunk", trunk.name, not trunk.on_origin)
    if tip in shipped:
        return Landing("shipped", "", True)
    return None


def holders(ledger, *, dropping=frozenset()) -> list[str]:
    """Every ref of the repository by its full name, minus `dropping`; [] when git cannot list them."""
    listed = _git(ledger, "for-each-ref", "--format=%(refname)")
    if listed.returncode != 0:
        return []
    return [name for name in listed.stdout.splitlines() if name and name not in dropping]


def _unreachable(ledger, commits: list[str], holders: list[str]) -> set[str] | None:
    feed = "".join(f"{sha}\n" for sha in commits) + "".join(f"^{name}\n" for name in holders)
    done = _git(ledger, "rev-list", "--stdin", input=feed)
    return set(done.stdout.split()) if done.returncode == 0 else None


def held(ledger, commit: str, holders: list[str]) -> bool:
    """Whether a holder reaches `commit`; a git that cannot answer holds nothing, so the tree stays."""
    found = _unreachable(ledger, [commit], holders)
    return found is not None and not found


def unheld(ledger, commits: list[str], holders: list[str]) -> list[str]:
    """The commits no holder reaches, reduced to the tips of their chains: a ref on a tip keeps the chain.
    A git that cannot answer returns every commit, so each is parked rather than lost."""
    commits = list(dict.fromkeys(sha for sha in commits if sha))
    if not commits:
        return []
    found = _unreachable(ledger, commits, holders)
    lost = commits if found is None else [sha for sha in commits if sha in found]
    tips: set[str] = set()
    for start in range(0, len(lost), 200):
        chunk = lost[start:start + 200]
        done = _git(ledger, "merge-base", "--independent", *chunk)
        tips |= set(done.stdout.split()) if done.returncode == 0 else set(chunk)
    if len(lost) > 200:   # tips of different chunks may still contain each other
        done = _git(ledger, "merge-base", "--independent", *sorted(tips))
        tips = set(done.stdout.split()) if done.returncode == 0 else tips
    return [sha for sha in lost if sha in tips]
