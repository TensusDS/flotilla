"""What a push would carry to trunk, and whether a verdict covers every commit in it (spec, sections 6.2-6.3).

A direct-push sender merges accepted work into the local trunk and pushes. Before `land` is recorded, every commit
the push would carry is asked one question: whose verdict covers it? A commit is accounted for when it is a merge,
is reachable from a reviewed row's revision, carries no change, is a release (version lines only), was recorded as
work born in the batch, or carries the same change as reviewed work (a squash or a cherry-pick, recognised by
`git patch-id`). Anything else is unread work riding along. A question git cannot answer counts as not accounted
for: doubt errs towards a refusal, never towards a pass.
"""

from __future__ import annotations

import re

from flotilla.ledger import gitq
from flotilla.ledger.model import Row

REVIEWED = frozenset({"accepted", "queued", "landed", "shipped", "walked", "closed"})
LOCK_COMPANIONS = frozenset({"uv.lock", "package-lock.json", "Cargo.lock", "poetry.lock"})
VERSION_LINE = re.compile(r'^\s*"?version"?\s*[=:]\s*"[0-9][^"]*"\s*,?\s*$')
BARE_VERSION = re.compile(r"^\s*v?[0-9][0-9A-Za-z.+-]*\s*$")
DIFF_NOISE = ("+++", "---", "@@", "diff ", "index ", "new file", "deleted file", "similarity", "rename ")


def _git(ledger, *args: str) -> str | None:
    done = ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False)
    return done.stdout if done.returncode == 0 else None


def revision_of(row: Row) -> str:
    """The revision a row's reader vouched for: the verdict, or the tip where the profile skips review."""
    return row.verdict or row.tip


def subject(ledger, sha: str) -> str:
    return (_git(ledger, "log", "-1", "--format=%s", sha) or "").strip()


def outgoing(ledger, upto: str, *, base: str = "") -> list[str] | None:
    """Commits `upto` carries that origin's trunk lacks (or past `base` without an origin), oldest first."""
    remote = f"refs/remotes/origin/{ledger.trunk}"
    stop = remote if gitq.resolve(ledger.root, remote, run=ledger.run) else base
    if not stop:
        return None
    listed = _git(ledger, "rev-list", "--reverse", upto, f"^{stop}")
    return None if listed is None else listed.split()


def is_merge(ledger, sha: str) -> bool | None:
    listed = _git(ledger, "rev-list", "--parents", "-n", "1", sha)
    return None if listed is None else len(listed.split()) > 2


def is_release(ledger, sha: str) -> bool:
    """Only version files (and their locks) change, and only their version lines."""
    files = set((ledger.profile.get("release") or {}).get("version_files") or [])
    names = _git(ledger, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha)
    if not files or not names:
        return False
    paths = set(names.split())
    if not paths <= files | LOCK_COMPANIONS or not paths & files:
        return False
    for path in paths:
        body = _git(ledger, "show", "--format=", "-U0", sha, "--", path)
        if body is None:
            return False
        bare = path.rsplit("/", 1)[-1] == "VERSION"
        for line in body.splitlines():
            if not line or line.startswith(DIFF_NOISE):
                continue
            if line[0] in "+-" and not (BARE_VERSION if bare else VERSION_LINE).match(line[1:]):
                return False
    return True


def _commit_patch(ledger, sha: str) -> str | None:
    return gitq.patch_fingerprint(ledger.root, f"{sha}^", sha, run=ledger.run)


def carries_change(ledger, sha: str, base: str, tip: str) -> bool:
    """Whether commit `sha` carries exactly the change `base..tip` (a squash or a rebase of it)."""
    if not base or not tip:
        return False
    own = _commit_patch(ledger, sha)
    return own not in (None, "empty") and own == gitq.patch_fingerprint(ledger.root, base, tip, run=ledger.run)


def account(ledger, rows: dict[str, Row], sha: str) -> str | None:
    """Why this commit is accounted for, or None when no verdict covers it."""
    merge = is_merge(ledger, sha)
    if merge is None:
        return None
    if merge:
        return "a merge"
    own = _commit_patch(ledger, sha)
    if own == "empty":
        return "carries no change"
    reviewed = [row for row in rows.values() if row.state in REVIEWED and revision_of(row)]
    for row in reviewed:
        if gitq.is_ancestor(ledger.root, sha, revision_of(row), run=ledger.run) is True:
            return f"read in `{row.branch}` ({row.id})"
    for row in rows.values():
        if row.state == "inbatch" and row.merge and gitq.same_revision(ledger.root, row.merge, sha,
                                                                        run=ledger.run):
            return f"born in the batch, read by {row.reader}"
    if is_release(ledger, sha):
        return "a release"
    if own is None:
        return None
    for row in reviewed:
        if carries_change(ledger, sha, row.base, revision_of(row)):
            return f"a squash of `{row.branch}` ({row.id})"
        listed = _git(ledger, "rev-list", "--no-merges", revision_of(row), f"^{row.base}") if row.base else None
        for mine in (listed or "").split():
            if _commit_patch(ledger, mine) == own:
                return f"a cherry-pick from `{row.branch}` ({row.id})"
    return None


def unaccounted(ledger, rows: dict[str, Row], upto: str, *, base: str = "") -> list[str] | None:
    """The commits `upto` would carry that no verdict covers; None when what it carries cannot be told."""
    commits = outgoing(ledger, upto, base=base)
    if commits is None:
        return None
    return [sha for sha in commits if account(ledger, rows, sha) is None]
