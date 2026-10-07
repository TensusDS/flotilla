"""What a run sends to the machine and what it may bring back (rig design, section 7).

A run is tied to a committed revision; the files git does not track that it needs travel with `--put`, which never
carries a secret, a symlink or anything outside the tree. What comes back with `--get` is a stranger's archive: it
is checked whole before anything in the tree changes, lands in a temporary directory, and never reaches a place the
field machine runs code from.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

#: Names `--put` never carries, matched against every path component, case-insensitively.
SECRET_NAMES = (".env*", "*.pem", "*.key", "*.p12", "*.pfx", "id_*", "credentials*", ".npmrc", ".netrc", ".pypirc",
                ".git", ".git-credentials", ".ssh", ".aws", ".kube", ".docker")
#: Path components `--get` never writes, in the paths asked for and in every member of what comes back: git's,
#: Claude Code's and flotilla's own files, and the places other tools run code from - dependencies, environments,
#: direnv, git hooks and attributes, CI workflows, editor tasks, pre-commit.
CODE_NAMES = (".git", ".gitattributes", ".gitmodules", ".claude", ".flotilla", "node_modules", ".venv", "venv",
              ".envrc", ".direnv", ".husky", ".github", ".gitlab-ci.yml", ".vscode", ".idea",
              ".pre-commit-config.yaml")
CAP = 500 * 2 ** 20


class Refused(ValueError):
    """A path or an archive the run will not use; the message names it."""


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _top(path: Path) -> Path:
    found = _git(path, "rev-parse", "--show-toplevel")
    if found.returncode != 0:
        raise Refused(f"{path} is not a git tree")
    return Path(found.stdout.strip()).resolve()


def same_repository(root, cwd) -> Path:
    """`--root` names the caller's own tree: a peer's worktree shares the repository's key and must not pass."""
    top = _top(Path(root))
    if top != _top(Path(cwd)):
        raise Refused(f"--root {root} is not this tree; a run works only in the tree it is called from")
    return top


def relative(root, text: str) -> str:
    if not text or text.startswith("/"):
        raise Refused(f"not a path inside the tree: {text!r}")
    parts = []
    for part in PurePosixPath(text).parts:
        if part == "..":
            if not parts:
                raise Refused(f"{text!r} leaves the tree")
            parts.pop()
        elif part != ".":
            parts.append(part)
    if not parts:
        raise Refused(f"{text!r} is the tree itself")
    return "/".join(parts)


def unsent(root) -> list[str]:
    found = _git(Path(root), "status", "--porcelain=v1", "-z", "--untracked-files=no")
    names = []
    for entry in found.stdout.split("\0"):
        if len(entry) > 3:
            names.append(entry[3:])
    return names


def gate(root, puts) -> str:
    """The committed revision a run is tied to; a tracked file changed since and not put is refused, named."""
    head = _git(Path(root), "rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        raise Refused("the tree has no commit: a run is tied to a revision - commit first")
    missing = [name for name in unsent(root) if name not in set(puts)]
    if missing:
        raise Refused(f"uncommitted changes to {', '.join(missing)}: commit them, or pass each with --put")
    return head.stdout.strip()


def _names_match(path: str, patterns) -> str:
    for part in PurePosixPath(path).parts:
        for pattern in patterns:
            if fnmatch.fnmatch(part.lower(), pattern.lower()):
                return part
    return ""


def _forbidden_default() -> tuple:
    from flotilla.core import paths
    home = Path.home()
    return (paths.state_dir(), home / ".ssh", home / ".config" / "flotilla", home / ".claude")


def put_tar(root, puts, *, forbidden_roots=None) -> bytes:
    root = Path(root)
    forbidden = [os.path.realpath(p) for p in (_forbidden_default() if forbidden_roots is None else forbidden_roots)]
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as archive:
        for text in puts:
            name = relative(root, text)
            for path in [root / name, *_walk(root / name)]:
                rel = path.relative_to(root).as_posix()
                hit = _names_match(rel, SECRET_NAMES)
                if hit:
                    raise Refused(f"--put {rel}: {hit} is never sent")
                info = os.lstat(path)
                if stat.S_ISLNK(info.st_mode):
                    raise Refused(f"--put {rel} is a symlink, which is never followed")
                real = os.path.realpath(path)
                if not real.startswith(os.path.realpath(root) + os.sep):
                    raise Refused(f"--put {rel} is outside the tree")
                if any(real == f or real.startswith(f + os.sep) for f in forbidden):
                    raise Refused(f"--put {rel} is under a directory that holds keys or flotilla's state")
                if stat.S_ISDIR(info.st_mode):
                    archive.addfile(_info(rel, info, tarfile.DIRTYPE))
                elif stat.S_ISREG(info.st_mode):
                    if info.st_nlink > 1:
                        raise Refused(f"--put {rel} has other hard links, which may name a file outside the tree")
                    with open(path, "rb") as handle:
                        archive.addfile(_info(rel, info, tarfile.REGTYPE), handle)
                else:
                    raise Refused(f"--put {rel} is not a file or a directory")
    return out.getvalue()


def _walk(path: Path):
    if path.is_symlink() or not path.is_dir():
        return
    for child in sorted(path.iterdir()):
        yield child
        yield from _walk(child)


def _info(name: str, info, kind) -> tarfile.TarInfo:
    item = tarfile.TarInfo(name)
    item.type = kind
    item.mode = 0o755 if kind == tarfile.DIRTYPE else (0o755 if info.st_mode & 0o111 else 0o644)
    item.size = info.st_size if kind == tarfile.REGTYPE else 0
    item.mtime = int(info.st_mtime)
    return item


def _symlinked_parent(root: Path, name: str) -> str:
    here = root
    for part in PurePosixPath(name).parts:
        here = here / part
        if here.is_symlink():
            return here.relative_to(root).as_posix()
        if not here.exists():
            return ""
    return ""


def check_gets(root, gets) -> list[str]:
    root = Path(root)
    checked = []
    for text in gets:
        name = relative(root, text)
        hit = _names_match(name, CODE_NAMES)
        if hit:
            raise Refused(f"--get {name}: {hit} holds code or state this machine runs; it is never brought back")
        low = name.lower()   # a case-insensitive file system makes src/app.py the tracked src/App.py
        tracked = [t for t in _git(root, "ls-files", "-z").stdout.split("\0") if t]
        if any(t.lower() == low or t.lower().startswith(low + "/") for t in tracked):
            raise Refused(f"--get {name}: git tracks it; an artifact is never a tracked file")
        link = _symlinked_parent(root, name)
        if link:
            raise Refused(f"--get {name}: {link} is a symlink here")
        checked.append(name)
    return checked


def _inside(name: str, wanted) -> bool:
    parts = PurePosixPath(name).parts
    return any(parts[:len(PurePosixPath(w).parts)] == PurePosixPath(w).parts for w in wanted)


def unpack(root, archive: Path, wanted, *, cap: int = CAP) -> list[str]:
    """Check every member, extract into a temporary directory in the tree, then replace each wanted path that
    arrived. Any refusal is raised before the tree changes."""
    root, archive = Path(root), Path(archive)
    if archive.stat().st_size == 0:
        return []
    wanted = [relative(root, w) for w in wanted]
    wanted = [w for w in dict.fromkeys(wanted)   # a path inside another wanted one arrives with it
              if not any(o != w and w.startswith(o + "/") for o in wanted)]
    try:
        handle = tarfile.open(archive, mode="r:*")
    except tarfile.TarError as err:
        raise Refused(f"what came back is not an archive: {err}") from None
    with handle:
        checked, total = [], 0
        for member in handle.getmembers():
            if member.name in (".", "./"):
                continue
            if member.name.startswith("/"):
                raise Refused(f"the archive holds an absolute path: {member.name!r}")
            name = relative(root, member.name)
            if not (member.isreg() or member.isdir()):
                raise Refused(f"the archive holds {name!r}, which is not a file or a directory")
            if not _inside(name, wanted):
                raise Refused(f"the archive holds {name!r}, outside {', '.join(wanted)}")
            hit = _names_match(name, CODE_NAMES)
            if hit:
                raise Refused(f"the archive holds {name!r}: {hit} holds code this machine runs")
            total += member.size
            if total > cap:
                raise Refused(f"the archive is over {cap // 2 ** 20} MB")
            member.name = name
            member.mode = 0o755 if member.isdir() else (member.mode & 0o755)
            checked.append(member)
        for name in wanted:
            link = _symlinked_parent(root, name)
            if link:
                raise Refused(f"--get {name}: {link} became a symlink while the run computed")
        temp = root / f".flotilla-get-{secrets.token_hex(4)}"
        temp.mkdir()
        try:
            if hasattr(tarfile, "data_filter"):
                handle.extractall(temp, members=checked, filter="data")
            else:
                handle.extractall(temp, members=checked)
            placed = []
            for name in wanted:
                arrived = temp / name
                if not arrived.exists():
                    continue
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                old = root / f".flotilla-old-{secrets.token_hex(4)}"
                if target.exists():
                    os.rename(target, old)
                os.rename(arrived, target)
                if old.exists():
                    shutil.rmtree(old) if old.is_dir() else old.unlink()
                placed.append(name)
            return placed
        finally:
            shutil.rmtree(temp, ignore_errors=True)


def project_slug(root, key: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(root).name).strip("-")[:40] or "project"
    return f"{base}-{hashlib.sha1(key.encode()).hexdigest()[:8]}"
