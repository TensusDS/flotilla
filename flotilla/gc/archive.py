"""The archive (gc design, section 7): where files with no copy anywhere go before their tree is removed.

`<main checkout's name>-gc-archive/` beside the main checkout. Files are moved, not copied: renamed on the same
filesystem; across filesystems copied, verified by sha256, then deleted - a copy that does not verify leaves the
original where it was. Content is stored once, by its sha256 (`objects/<sha[:2]>/<sha>`), so identical files from
many trees take the room of one (rule 3). A symlink is moved as a link and never followed.

`MANIFEST` keeps one line per file in the person's own shape - hash, where it is now, where it came from - and each
entry (`entries/<id>/entry.json`) records when, which tier, who approved it, the trees it came from with their
parked HEADs, and where the last copy of a file removed on a twin now lives. Small things of one piece of work on one
day share one entry: one line in the report, one restore (the person's decision, 2026-10-21).

Restore copies the files back and keeps the objects (another entry may point to them); a path that now holds other
bytes is not overwritten, and a tree that is gone is made again at its parked HEAD first. Nothing is taken from or
written to outside the tree a file belongs to: a path with `..` or a linked directory on the way is refused on both
sides, and restore writes only into a worktree of this repository, creating each file without following a link
(security review of 75eff4b) - the archive's records are files any session could edit. For the same reason a
record's digest, HEAD and patch name must have their own shapes, an object must hash to its name before it is copied
back, and a gone tree is made again only where spawn cuts seat trees, beside the main checkout under its name
(security review of 554be5c). Nothing passes through a `.git` component, and nothing is restored into the main
checkout: a forged record must not plant a hook (security review of de4ae37); restoring is the person's move.
Nothing here deletes from the archive: its lifetime is stage 3's, and only on the person's recorded yes.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from flotilla.core.text import visible
from flotilla.gc.park import _component
from flotilla.gc.twins import sha256


DIGEST = re.compile(r"[0-9a-f]{64}")
OBJECT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
PATCH = re.compile(r"edits-[A-Za-z0-9._-]+\.patch")


class ArchiveFailed(RuntimeError):
    """A file could not be moved and verified; it and every file after it stay where they were. `entry`, `files` and
    `moved` say what went into the archive before it (second review of the branch, m-10)."""

    def __init__(self, message: str, *, entry: str = "", files: int = 0, moved: int = 0):
        super().__init__(message)
        self.entry, self.files, self.moved = entry, files, moved


@dataclass
class Entry:
    id: str
    when: str
    tier: str
    approved_by: str
    trees: list = field(default_factory=list)        # {"tree", "branch", "head", "patch", "tier", "approved_by"}
    files: list = field(default_factory=list)        # {"sha256", "size", "from", "tree"}
    last_copies: list = field(default_factory=list)  # {"from", "where"}


def root_of(ledger) -> Path:
    """Beside the main checkout, whichever tree the ledger stands in (final review of gc stage 2, I8)."""
    from flotilla.gc.twins import main_checkout
    root = main_checkout(ledger)
    return root.parent / f"{root.name}-gc-archive"


def entry_id(piece: str, day: str) -> str:
    """One entry per piece of work per day: `<day>-<piece made safe>`; `work:` is dropped."""
    bare = piece[len("work:"):] if piece.startswith("work:") else piece
    return f"{day}-{_component(bare) or 'unnamed'}"


def _private(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def _object(root: Path, digest: str) -> Path:
    return root / "objects" / digest[:2] / digest


def _same(path: Path, digest: str) -> bool:
    try:
        return sha256(path, {}, fresh=True) == digest
    except OSError:
        return False


def _store(root: Path, source: Path) -> tuple[str, int]:
    """Move `source` into the archive's objects; its sha256 and size. Raises ArchiveFailed, leaving it in place."""
    digest, size = sha256(source, {}, fresh=True), source.lstat().st_size
    target = _object(root, digest)
    _private(target.parent)
    if os.path.lexists(target):
        if not _same(target, digest):
            raise ArchiveFailed(f"the archive's copy of {visible(str(source))} does not match it; nothing moved")
        source.unlink()
        return digest, size
    try:
        os.rename(source, target)
        return digest, size
    except OSError as err:
        if err.errno != errno.EXDEV:
            raise ArchiveFailed(f"{visible(str(source))} could not be moved: {err}") from err
    try:
        if source.is_symlink():
            os.symlink(os.readlink(source), target)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
    except OSError as err:
        if os.path.lexists(target):
            target.unlink()
        raise ArchiveFailed(f"{visible(str(source))} could not be copied: {err}") from err
    if not _same(target, digest):
        target.unlink()
        raise ArchiveFailed(f"the copy of {visible(str(source))} did not verify; the original stays")
    source.unlink()
    return digest, size


def _inside(tree: Path, target: Path) -> bool:
    """Whether `target` lies under `tree` with no `..` and no linked directory between them (its last component may
    be a link: that is the file itself)."""
    try:
        relative = target.relative_to(tree)
    except ValueError:
        return False
    if not relative.parts or ".." in relative.parts:
        return False
    if any(part.rstrip(". ").lower() == ".git" for part in relative.parts):   # a case-insensitive file system too
        return False
    here = tree
    for part in relative.parts[:-1]:
        here = here / part
        if here.is_symlink():
            return False
    return True


def _entry_path(root: Path, key: str) -> Path:
    return root / "entries" / key / "entry.json"


def _load(path: Path) -> Entry | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Entry(**data)
    except (OSError, ValueError, TypeError):
        return None


def _save(root: Path, entry: Entry) -> None:
    path = _entry_path(root, entry.id)
    _private(path.parent)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(asdict(entry), indent=2), encoding="utf-8")
    temporary.replace(path)


def put(ledger, *, entry: str, tree, files: list[str], tier: str, approved_by: str, branch: str, head: str,
        patch: bytes = b"", last_copies=()) -> Entry:
    """Move `files` (relative to `tree`) into the archive under `entry`, with the tree's record. Raises
    ArchiveFailed on the first file that cannot be moved, and on an archive that cannot be made or written (second
    review of the branch, m-1); the files before it are recorded, the rest stay."""
    root, tree, moved = root_of(ledger), Path(tree), []
    try:
        _private(root)
        found = _load(_entry_path(root, entry)) or Entry(entry, datetime.now(timezone.utc).isoformat(
            timespec="seconds"), tier, approved_by)
        record = next((item for item in found.trees if item["tree"] == str(tree)), None)
        if record is None:
            record = {"tree": str(tree), "branch": branch, "head": head, "patch": ""}
            found.trees.append(record)
        record.update(tier=tier, approved_by=approved_by)   # each tree its own (second review of the branch, m-5)
        if patch:
            name = f"edits-{_component(tree.name) or 'tree'}.patch"
            (_private(root / "entries" / entry) / name).write_bytes(patch)
            record["patch"] = name
        found.last_copies += [dict(item) for item in last_copies]
    except OSError as err:
        raise ArchiveFailed(f"the archive {visible(str(root))} could not be made or written: {err}") from err
    failed = None
    try:
        with open(root / "MANIFEST", "a", encoding="utf-8") as manifest:
            for relative in files:   # any other failure becomes ArchiveFailed below: callers keep the tree
                source = tree / relative
                if Path(relative).is_absolute() or not _inside(tree, source):
                    raise ArchiveFailed(f"{visible(str(relative))} is not a file of {visible(str(tree))}; nothing "
                                        "after it moved")
                digest, size = _store(root, source)
                found.files.append({"sha256": digest, "size": size, "from": str(source), "tree": str(tree)})
                moved.append(size)
                manifest.write(f"{digest}  objects/{digest[:2]}/{digest}  <-  {visible(str(source))}\n")
    except ArchiveFailed as err:
        failed = str(err)
    except OSError as err:   # final review of gc stage 2, I5
        failed = f"the archive could not take the files of {visible(str(tree))}: {err}"
    try:
        _save(root, found)
    except OSError as err:
        failed = f"{failed + '; ' if failed else ''}the archive's record of {visible(entry)} could not be written: {err}"
    if failed:
        if moved:
            count = f"{len(moved)} file{'' if len(moved) == 1 else 's'} before it {'is' if len(moved) == 1 else 'are'}"
            failed += f"; {count} in the archive: restore with flotilla gc restore {entry}"
        raise ArchiveFailed(failed, entry=entry if moved else "", files=len(moved), moved=sum(moved))
    return found


def entries(ledger) -> list[Entry]:
    folder = root_of(ledger) / "entries"
    if not folder.is_dir():
        return []
    found = [_load(path) for path in sorted(folder.glob("*/entry.json"))]
    return [entry for entry in found if entry is not None]


def _git(ledger, *args):
    return ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False)


def _listed(ledger) -> list[str]:
    listed = _git(ledger, "worktree", "list", "--porcelain").stdout.splitlines()
    return [str(Path(line[len("worktree "):]).resolve()) for line in listed if line.startswith("worktree ")]


def _worktrees(ledger) -> set[str]:
    return set(_listed(ledger))


def _copy_back(stored: Path, target: Path) -> None:
    """Create `target` from the archive's object; never through a link, never over a file that appeared."""
    if stored.is_symlink():
        os.symlink(os.readlink(stored), target)
        return
    handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(handle, "wb") as written, open(stored, "rb") as source:
        shutil.copyfileobj(source, written)
    shutil.copystat(stored, target)


def restore(ledger, key: str) -> list[str]:
    """Put an entry's files back (`key`: an entry id, or a tree's name or path - its newest entry)."""
    known = entries(ledger)
    entry = next((item for item in known if item.id == key), None)
    wanted = None
    if entry is None:
        holding = [item for item in known
                   if any(Path(record["tree"]).name == key or record["tree"] == key for record in item.trees)]
        if not holding:
            return [f"nothing in the archive under `{visible(key)}`"]
        entry = holding[-1]
        wanted = {record["tree"] for record in entry.trees if Path(record["tree"]).name == key or record["tree"] == key}
    root, lines = root_of(ledger), []
    for record in entry.trees:
        if wanted is not None and record["tree"] not in wanted:
            continue
        tree, fresh = Path(record["tree"]), False
        listed = _listed(ledger)   # git names the main checkout first, whichever tree the ledger stands in
        if tree.resolve() == Path(ledger.root).resolve() or (listed and str(tree.resolve()) == listed[0]):
            lines.append(f"refused: {visible(str(tree))} is the main checkout; nothing is restored into it")
            continue
        if tree.exists() and str(tree.resolve()) not in _worktrees(ledger):
            lines.append(f"refused: {visible(str(tree))} is not a worktree of this repository")
            continue
        main = Path(ledger.root)
        if not tree.exists() and (tree.parent != main.parent or not tree.name.startswith(f"{main.name}-")):
            lines.append(f"refused: {visible(str(tree))} is gone and is not where seat trees are cut "
                         f"({visible(str(main.parent))}/{visible(main.name)}-...)")
            continue
        if not tree.exists() and not OBJECT_ID.fullmatch(str(record.get("head", ""))):
            lines.append(f"refused: the record's HEAD for {visible(str(tree))} is not an object id")
            continue
        if tree.exists():   # a seat's name is reused: an old entry must not fill a new session's tree (I9)
            now = ledger.run(["git", "-C", str(tree), "rev-parse", "--verify", "-q", "HEAD"], capture_output=True,
                             text=True, check=False).stdout.strip()
            if now != record.get("head"):
                lines.append(f"refused: {visible(str(tree))} is at another commit ({now[:7]}) than the archived one "
                             f"({str(record.get('head'))[:7]})")
                continue
        if not tree.exists():
            made = _git(ledger, "worktree", "add", "--detach", str(tree), record["head"])
            if made.returncode != 0:
                lines.append(f"{visible(str(tree))} could not be made again at {record['head'][:7]}: "
                             f"{(made.stderr or '').strip()[:160]}")
                continue
            fresh = True
            lines.append(f"made {visible(str(tree))} again at {record['head'][:7]}")
        back = 0
        for item in entry.files:
            if item["tree"] != record["tree"]:
                continue
            if not DIGEST.fullmatch(str(item.get("sha256", ""))):
                lines.append(f"refused: the record of {visible(str(item.get('from', '')))} names no sha256")
                continue
            target, stored = Path(item["from"]), _object(root, item["sha256"])
            if not _inside(tree, target):
                lines.append(f"refused: {visible(str(target))} is not inside {visible(str(tree))}")
                continue
            if os.path.lexists(target):
                if not _same(target, item["sha256"]):
                    lines.append(f"not overwritten: {visible(str(target))} holds other bytes now")
                continue
            if not _same(stored, item["sha256"]):
                lines.append(f"refused: the archive's copy of {visible(str(target))} does not hash to its name")
                continue
            _private(target.parent)
            if not _inside(tree, target):   # a directory made just now could not be a link; one found could
                lines.append(f"refused: {visible(str(target))} is not inside {visible(str(tree))}")
                continue
            _copy_back(stored, target)
            back += 1
        lines.append(f"restored {back} file(s) into {visible(str(tree))}")
        if record.get("patch") and not fresh:
            lines.append(f"the saved edits were not applied: {visible(str(tree))} exists; apply "
                         f"{visible(str(root / 'entries' / entry.id / str(record['patch'])))} by hand if you want them")
        elif record.get("patch") and fresh and not PATCH.fullmatch(str(record["patch"])):
            lines.append(f"refused: the saved edits' name `{visible(str(record['patch']))}` is not one the archive "
                         "writes")
        elif record.get("patch") and fresh:
            applied = ledger.run(["git", "-C", str(tree), "apply", "--binary",
                                  str(root / "entries" / entry.id / record["patch"])],
                                 capture_output=True, text=True, check=False)
            lines.append("applied the saved edits" if applied.returncode == 0
                         else f"the saved edits did not apply: {(applied.stderr or '').strip()[:160]}")
    return lines
