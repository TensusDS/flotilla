"""What a push would carry to trunk, and whether a verdict covers every commit in it (spec, sections 6.2-6.3).

A direct-push sender merges accepted work into the local trunk and pushes. Before `land` is recorded, every commit
the push would carry is asked one question: whose verdict covers it? A commit is accounted for when it lies in a
reviewed row's range (after its base, up to the revision read), is a merge that adds nothing of its own (its tree
is the one git would make from its parents), carries no change, is a release (version lines only), was recorded as
work born in the batch, or carries the same change as reviewed work (a squash or a cherry-pick, recognised by
`git patch-id`). Anything else is unread work riding along. A question git cannot answer counts as not accounted
for: doubt errs towards a refusal, never towards a pass. What the reviewed rows vouch for is gathered once per
question (`Accounting`), so a batch costs rows plus commits in git calls, not rows times commits.
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


def outgoing(ledger, upto: str, *, base: str = "", since: str = "") -> list[str] | None:
    """Commits `upto` carries that origin's trunk lacks (or past `base` without an origin), oldest first; with
    `since`, the commits past that revision instead — for a push that already reached origin."""
    remote = f"refs/remotes/origin/{ledger.trunk}"
    stop = since or (remote if gitq.resolve(ledger.root, remote, run=ledger.run) else base)
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
    if not files:
        return False
    names = _git(ledger, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha)
    if not names:
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


def clean_merge(ledger, sha: str) -> bool | None:
    """Whether a two-parent merge holds exactly the tree git makes from its parents: nothing of its own added."""
    parents = (_git(ledger, "rev-list", "--parents", "-n", "1", sha) or "").split()[1:]
    if len(parents) != 2:
        return None
    done = ledger.run(["git", "-C", str(ledger.root), "merge-tree", "--write-tree", *parents], capture_output=True,
                      text=True, check=False)
    if done.returncode == 1:
        return False   # the parents conflict: whatever the merge holds was resolved by hand
    words = done.stdout.split()
    own = (_git(ledger, "rev-parse", f"{sha}^{{tree}}") or "").strip()
    if done.returncode != 0 or not words or not own:
        return None
    return words[0] == own


def _patch_ids(ledger, *log_args: str) -> list[str]:
    log = ledger.run(["git", "-C", str(ledger.root), "log", "-p", "--no-merges", "--no-color", *log_args],
                     capture_output=True, text=True, check=False)
    if log.returncode != 0 or not log.stdout:
        return []
    done = ledger.run(["git", "-C", str(ledger.root), "patch-id", "--stable"], input=log.stdout,
                      capture_output=True, text=True, check=False)
    return [line.split()[0] for line in done.stdout.splitlines() if line.split()] if done.returncode == 0 else []


class Accounting:
    """What the reviewed rows and the batch rows vouch for, read from git once per question."""

    def __init__(self, ledger, rows: dict[str, Row]):
        self.ledger = ledger
        self.read: dict[str, Row] = {}
        self.squashes: dict[str, Row] = {}
        self.picks: dict[str, Row] = {}
        self.born: dict[str, Row] = {}
        self.vouched: dict[str, Row] = {}
        for row in rows.values():
            for sha in row.vouched or []:
                self.vouched.setdefault(sha, row)
            if row.state == "inbatch" and row.merge:
                full = gitq.resolve(ledger.root, row.merge, run=ledger.run)
                if full:
                    self.born[full] = row
            revision = revision_of(row)
            if row.state not in REVIEWED or not revision:
                continue
            span = [revision, f"^{row.base}"] if row.base else [revision]
            for sha in (_git(ledger, "rev-list", *span) or "").split():
                self.read.setdefault(sha, row)
            if row.base:
                squash = gitq.patch_fingerprint(ledger.root, row.base, revision, run=ledger.run)
                if squash not in (None, "empty"):
                    self.squashes.setdefault(squash, row)
                for patch in _patch_ids(ledger, f"{row.base}..{revision}"):
                    self.picks.setdefault(patch, row)

    def account(self, sha: str) -> str | None:
        """Why this commit is accounted for, or None when no verdict covers it."""
        ledger = self.ledger
        full = gitq.resolve(ledger.root, sha, run=ledger.run)
        merge = is_merge(ledger, sha) if full else None
        if merge is None:
            return None
        born = self.born.get(full)
        if born is not None:
            return f"born in the batch, read by {born.reader}"
        vouched = self.vouched.get(full)
        if vouched is not None:
            return f"vouched for in `{vouched.branch}` ({vouched.id})"
        if merge:
            row = self.read.get(full)
            if row is not None:   # read in the range, a conflict resolved by hand included (H20)
                return f"read in `{row.branch}` ({row.id})"
            return "a merge that adds nothing of its own" if clean_merge(ledger, full) is True else None
        own = _commit_patch(ledger, full)
        if own == "empty":
            return "carries no change"
        row = self.read.get(full)
        if row is not None:
            return f"read in `{row.branch}` ({row.id})"
        if is_release(ledger, full):
            return "a release"
        if own is None:
            return None
        row = self.squashes.get(own)
        if row is not None:
            return f"a squash of `{row.branch}` ({row.id})"
        row = self.picks.get(own)
        if row is not None:
            return f"a cherry-pick from `{row.branch}` ({row.id})"
        return None


def account(ledger, rows: dict[str, Row], sha: str) -> str | None:
    """Why this commit is accounted for, or None when no verdict covers it."""
    return Accounting(ledger, rows).account(sha)


def unaccounted(ledger, rows: dict[str, Row], upto: str, *, base: str = "", since: str = "") -> list[str] | None:
    """The commits `upto` would carry that no verdict covers; None when what it carries cannot be told."""
    commits = outgoing(ledger, upto, base=base, since=since)
    if commits is None:
        return None
    accounting = Accounting(ledger, rows)
    return [sha for sha in commits if accounting.account(sha) is None]
