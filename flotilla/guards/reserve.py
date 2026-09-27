"""The file reservation: a rewrite of a shared file is made by one row at a time; appends always pass (spec, 10).

The shared files are the profile's `[reservation] files` (patterns). A staged change that deletes more than three
lines of one is a rewrite; fewer is a line fixed or an append, and never a duplicate, because each writer appends
their own. The first open row to commit a rewrite holds the file; a later rewrite by another row is refused,
naming the holder. A reservation lives while its row is open and not yet delivered: no clock ends it. A merge
carries a rewrite, it does not make one, so a merge neither needs nor takes a reservation for what it brings in
(judged by content: against an incoming side, the staged file deletes no more than an edit does).
`FLOTILLA_RESERVE_OVERRIDE="<why>"` lets a refused commit through, recorded.

Not seen: a commit made with `--no-verify` or in a clone without the hook; a rename (read as a deletion); code
files, which branches share legitimately and which are not on the list.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

from flotilla.core.storage import LocalLogStore
from flotilla.ledger.model import delivered, now_iso

REWRITE_DELETED = 3
OVERRIDE = "FLOTILLA_RESERVE_OVERRIDE"


def _store(state_dir) -> LocalLogStore:
    return LocalLogStore(Path(state_dir) / "reservations")


def _git(root, *args, run):
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False, timeout=10)


def rewrites(root, patterns, run=subprocess.run) -> list[tuple[str, int]]:
    done = _git(root, "diff", "--cached", "--numstat", "--no-renames", run=run)
    found = []
    for line in done.stdout.splitlines():
        added, deleted, path = line.split("\t", 2)
        if deleted == "-" or not any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
            continue
        if int(deleted) > REWRITE_DELETED:
            found.append((path, int(deleted)))
    return found


def live(state_dir, repo_key, rows, profile) -> dict[str, dict]:
    held: dict[str, dict] = {}
    for record in _store(state_dir).read(repo_key).records:
        row = rows.get(record.get("row", ""))
        if row is not None and row.is_open and not delivered(row, profile):
            held[record["path"]] = record
    return held


def _merge_heads(root, run) -> list[str]:
    done = _git(root, "rev-parse", "--git-path", "MERGE_HEAD", run=run)
    path = Path(done.stdout.strip()) if done.returncode == 0 else None
    if path is None:
        return []
    path = path if path.is_absolute() else Path(root) / path
    return [line.strip() for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def _brought_in(root, path, merging, run) -> bool:
    """The merge carries this rewrite: against an incoming side, the staged file deletes no more than an edit does.

    Asked of the content, not of the holder's tip: a holder that moved on after its rewrite reached the incoming
    side is no longer an ancestor of it, yet the rewrite that arrives is still theirs.
    """
    for head in merging:
        done = _git(root, "diff", "--cached", "--numstat", "--no-renames", head, "--", path, run=run)
        if done.returncode != 0:
            continue
        deleted = [line.split("\t", 2)[1] for line in done.stdout.splitlines() if line.count("\t") >= 2]
        if all(value != "-" and int(value) <= REWRITE_DELETED for value in deleted):
            return True
    return False


def check(root, *, env=os.environ, run=subprocess.run, ledger=None) -> tuple[int, str]:
    from flotilla.guards.overrides import record_override
    from flotilla.guards.rules import rules_for
    profile, _ = rules_for(root, run=run)
    patterns = (profile.get("reservation") or {}).get("files") or []
    changes = rewrites(root, patterns, run) if patterns else []
    if not changes:
        return 0, ""
    if ledger is None:
        from flotilla.ledger.commands import open_ledger
        ledger = open_ledger(Path(root))
    rows = ledger.rows()
    branch = _git(root, "symbolic-ref", "-q", "--short", "HEAD", run=run).stdout.strip()
    mine = next((row for row in reversed(list(rows.values())) if row.branch == branch and row.is_open), None)
    merging = _merge_heads(root, run)
    held = live(ledger.state_dir, ledger.repo_key, rows, ledger.profile)
    conflicts, grants = [], []
    for path, deleted in changes:
        record = held.get(path)
        holder = rows.get(record["row"]) if record else None
        if holder is not None and (mine is None or holder.id != mine.id):
            if not (merging and _brought_in(root, path, merging, run)):
                conflicts.append((path, deleted, holder, record))
        elif holder is None and mine is not None and not merging:
            grants.append({"at": now_iso(), "path": path, "branch": branch, "row": mine.id, "by": mine.owner})
    said = []
    if conflicts:
        lines = [f"flotilla reservation: {path} is rewritten ({deleted} lines deleted), and {holder.owner} holds "
                 f"it for {holder.branch} ({holder.state}) since {record['at']}"
                 for path, deleted, holder, record in conflicts]
        reason = env.get(OVERRIDE, "").strip()
        if not reason:
            return 1, "\n".join(lines + ["Ask the holder before doing the same work. If the work differs and both "
                                         f'know: {OVERRIDE}="<why>" git commit ... (recorded).'])
        record_override(ledger.state_dir, ledger.repo_key, "reservation", reason,
                        [path for path, *_ in conflicts])
        said.append(f"flotilla reservation: override recorded ({reason})")
    store = _store(ledger.state_dir)
    for grant in grants:
        store.append(ledger.repo_key, grant)
        said.append(f"flotilla reservation: {grant['path']} is reserved for {branch} ({mine.owner})")
    if mine is None and not merging:
        said.append("flotilla reservation: this branch has no open ledger row, so its rewrite of shared files is "
                    "not protected from a second author")
    return 0, "\n".join(said)
