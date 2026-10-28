"""Twins by content, rule 2 of the gc design (section 6): a file can come back when a byte-identical copy survives the
pass - in the main checkout, in a store the project declares (`[gc] stores`), in the archive, in a tree the pass
does not judge, or as a blob a surviving ref reaches (`rev-list --objects` over the pass's holders: an unreachable
blob in the object store is pruned by git's own gc).

Hashes are sha256 of the bytes, cached by (path, size, mtime) so a daily pass does not reread gigabytes. A symlink
is never followed: it is hashed as its target's text, never walked into, and never a twin.
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


def sha256(path: Path, cache: dict) -> str:
    """The sha256 of the file's bytes, or of `link:<target>` for a symlink; reused while size and mtime hold."""
    path = Path(path)
    stat = path.lstat()
    key = str(path)
    known = cache.get(key)
    if known and known[0] == stat.st_size and known[1] == stat.st_mtime_ns:
        return known[2]
    digest = hashlib.sha256()
    if path.is_symlink():
        digest.update(f"link:{os.readlink(path)}".encode())
    else:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(CHUNK), b""):
                digest.update(chunk)
    cache[key] = [stat.st_size, stat.st_mtime_ns, digest.hexdigest()]
    return cache[key][2]


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
    """Every regular file under `top`, never following a link and never entering `.git`."""
    for here, dirs, files in os.walk(top, followlinks=False):
        dirs[:] = [name for name in dirs if name != ".git" and not os.path.islink(os.path.join(here, name))]
        for name in files:
            path = Path(here) / name
            if not path.is_symlink():
                yield path


class Twins:
    """Finds a surviving byte-identical copy of a file. Sources are walked once, lazily, indexed by size."""

    def __init__(self, ledger, *, holders: list[str], sources: list[tuple[Path, str]], cache: dict):
        self.ledger, self.holders, self.cache = ledger, list(holders), cache
        self.sources = [(Path(where), kind) for where, kind in sources]
        self._by_size: list[tuple[str, dict[int, list[Path]]]] | None = None
        self._reachable: set[str] | None = None
        self._format: str | None = None

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
        size, digest = path.lstat().st_size, sha256(path, self.cache)
        for where, kind in self.sources:
            if kind == "archive" and (where / digest[:2] / digest).exists():
                return Twin(str(where / digest[:2] / digest), "archive")
        for kind, sizes in self._index():
            for other in sizes.get(size, ()):
                try:
                    if other.resolve() == path.resolve():
                        continue
                    if sha256(other, self.cache) == digest:
                        return Twin(str(other), kind)
                except OSError:
                    continue
        blob = self._blob_id(path)
        if blob and blob in self._reached():
            return Twin(f"reachable blob {blob[:8]}", "blob")
        return None
