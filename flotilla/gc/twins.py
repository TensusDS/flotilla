"""Twins by content, rule 2 of the gc design (section 6): a file can come back when a byte-identical copy survives the
pass - in the main checkout, in a store the project declares (`[gc] stores`), in the archive, in a tree the pass
does not judge, or as a blob a surviving ref reaches (`rev-list --objects` over the pass's holders: an unreachable
blob in the object store is pruned by git's own gc).

Hashes are sha256 of the bytes, cached by (path, size, mtime, ctime, inode) so a daily pass does not reread
gigabytes: a rewrite can keep size and mtime, never ctime (security review of dea3c4e). `verify` hashes both files
afresh, for the check under the removal lock. A symlink is never followed: it is hashed as its target's text, never
walked into, and never a twin. A source is never walked into another repository or worktree (a `.git` below its
top): trees nested in the main checkout are judged, not twins (security review of dea3c4e).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from flotilla.core import paths

CHUNK = 1 << 20


@dataclass(frozen=True)
class Twin:
    where: str
    kind: str   # checkout, store, archive, blob, tree


def sha256(path: Path, cache: dict, *, fresh: bool = False) -> str:
    """The sha256 of the file's bytes, or of `link:<target>` for a symlink; reused while size, mtime, ctime and the
    inode hold, unless `fresh`."""
    path = Path(path)
    stat = path.lstat()
    key = str(path)
    stamp = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino]
    known = cache.get(key)
    if not fresh and isinstance(known, list) and len(known) == 5 and known[:4] == stamp:
        return known[4]
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(f"link:{os.readlink(path)}".encode())
    else:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
    cache[key] = [*stamp, digest.hexdigest()]
    return cache[key][4]


def _cache_path(state: Path, repo_key: str) -> Path:
    return Path(state) / "gc" / f"hashes-{repo_key}.json"


def load_cache(state: Path, repo_key: str) -> dict:
    try:
        data = json.loads(_cache_path(state, repo_key).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cache(state: Path, repo_key: str, cache: dict) -> None:
    path = _cache_path(state, repo_key)
    paths.ensure_private(Path(state))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def sources(ledger, *, untouched: list[Path], archive_root: Path) -> list[tuple[Path, str]]:
    """Where a twin may live: the main checkout, the declared stores, the archive's objects, the untouched trees."""
    found = [(Path(ledger.root), "checkout")]
    for store in (ledger.profile.get("gc") or {}).get("stores") or []:
        if isinstance(store, str) and store:
            found.append((Path(ledger.root).parent / store, "store"))
    objects = Path(archive_root) / "objects"
    if objects.is_dir():
        found.append((objects, "archive"))
    found += [(Path(tree), "tree") for tree in untouched]
    return found


def _walk(top: Path):
    """Every regular file under `top`, never following a link, never entering `.git` and never entering another
    repository or worktree below `top` (a directory holding a `.git` file or directory)."""
    for here, dirs, files in os.walk(top, followlinks=False):
        dirs[:] = [name for name in dirs if name != ".git" and not os.path.islink(os.path.join(here, name))
                   and not os.path.lexists(os.path.join(here, name, ".git"))]
        for name in files:
            path = Path(here) / name
            if not path.is_symlink():
                yield path


class Twins:
    """Finds a surviving byte-identical copy of a file. Sources are walked once, lazily, indexed by size."""

    def __init__(self, ledger, *, holders: list[str], sources: list[tuple[Path, str]], cache: dict,
                 verify: bool = False):
        self.ledger, self.holders, self.cache, self.verify = ledger, list(holders), cache, verify
        self.sources = [(Path(where), kind) for where, kind in sources]
        self._by_size: list[tuple[str, dict[int, list[Path]]]] | None = None
        self._reachable: set[str] | None = None
        self._format: str | None = None

    def rehold(self, holders: list[str]) -> None:
        """Judge blobs against other holders (the pass's next iteration); the walked sources are kept."""
        self.holders, self._reachable = list(holders), None

    def checking(self) -> "Twins":
        """The same sources and cache, hashing afresh: for the check under the removal lock."""
        found = Twins(self.ledger, holders=self.holders, sources=self.sources, cache=self.cache, verify=True)
        found._by_size, found._format = self._by_size, self._format
        return found

    def _index(self) -> list[tuple[str, dict[int, list[Path]]]]:
        if self._by_size is None:
            self._by_size = []
            for where, kind in self.sources:
                if kind == "archive" or not where.is_dir():
                    continue
                sizes: dict[int, list[Path]] = {}
                for path in _walk(where):
                    try:
                        sizes.setdefault(path.lstat().st_size, []).append(path)
                    except OSError:
                        continue
                self._by_size.append((kind, sizes))
        return self._by_size

    def _git(self, *args, input=None):
        return self.ledger.run(["git", "-C", str(self.ledger.root), *args], capture_output=True, text=True,
                               check=False, input=input)

    def _blob_id(self, path: Path) -> str:
        if self._format is None:
            shown = self._git("rev-parse", "--show-object-format")
            self._format = shown.stdout.strip() if shown.returncode == 0 else ""
        if self._format == "sha1":
            digest = hashlib.sha1(f"blob {path.stat().st_size}\0".encode())
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(CHUNK), b""):
                    digest.update(chunk)
            return digest.hexdigest()
        hashed = self._git("hash-object", "--no-filters", "--stdin-paths", input=f"{path}\n")
        return hashed.stdout.strip() if hashed.returncode == 0 else ""

    def _reached(self) -> set[str]:
        if self._reachable is None:
            if not self.holders:
                self._reachable = set()
            else:
                listed = self._git("rev-list", "--objects", "--no-object-names", "--stdin",
                                   input="".join(f"{name}\n" for name in self.holders))
                self._reachable = set(listed.stdout.split()) if listed.returncode == 0 else set()
        return self._reachable

    def find(self, path: Path) -> Twin | None:
        """A surviving byte-identical copy of `path`, or None. A symlink has no twin."""
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            return None
        size, digest = path.lstat().st_size, sha256(path, self.cache, fresh=self.verify)
        for where, kind in self.sources:
            stored = where / digest[:2] / digest
            if kind == "archive" and stored.exists() and (not self.verify or sha256(stored, {}, fresh=True) == digest):
                return Twin(str(stored), "archive")
        for kind, sizes in self._index():
            for other in sizes.get(size, ()):
                try:
                    if other.resolve() == path.resolve():
                        continue
                    if sha256(other, self.cache, fresh=self.verify) == digest:
                        return Twin(str(other), kind)
                except OSError:
                    continue
        blob = self._blob_id(path)
        if blob and blob in self._reached():
            return Twin(f"reachable blob {blob[:8]}", "blob")
        return None
