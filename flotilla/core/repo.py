"""Which repository a directory belongs to, identical from the main checkout and every worktree.

The ledger is keyed by the repository, not by the directory: a session in a linked worktree and a
session in the main checkout must land in the same log, or the fleet's state splits in two.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_URL = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", re.IGNORECASE)
_SCP = re.compile(r"^(?:[^@/]+@)?([^/:]+):(?!//)(.+)$")


class NotARepository(RuntimeError):
    """The directory is not inside a git repository."""


@dataclass(frozen=True)
class RepoIdentity:
    root: Path
    common_dir: Path
    origin: str | None
    key: str


def normalize_origin(url: str, base: Path | None = None) -> str:
    """A stable name for an origin. Local paths are resolved against `base` (the repository),
    never against the process's working directory, which differs between worktrees."""
    url = url.strip()
    if url.lower().startswith("file://"):
        url = url[len("file://"):]
    elif not url.startswith(("/", ".", "~")):
        match = _URL.match(url) or _SCP.match(url)
        if match:
            host, path = match.groups()
            path = path.rstrip("/")
            if path.endswith(".git"):
                path = path[:-4]
            return f"{host.lower()}/{path}"
    local = Path(url).expanduser()
    if not local.is_absolute() and base is not None:
        local = Path(base) / local
    return f"path:{local.resolve()}"


def repo_key(normalized: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")[:60].strip("-")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def _git(cwd: Path, *args: str, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)


def identify(cwd: Path, run=subprocess.run) -> RepoIdentity:
    top = _git(cwd, "rev-parse", "--show-toplevel", run=run)
    if top.returncode != 0:
        raise NotARepository(f"{cwd} is not inside a git repository")
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir", run=run)
    origin_done = _git(cwd, "config", "--get", "remote.origin.url", run=run)
    origin = origin_done.stdout.strip() or None
    common_dir = Path(common.stdout.strip()).resolve()
    normalized = normalize_origin(origin, base=common_dir.parent) if origin else f"local:{common_dir}"
    return RepoIdentity(root=Path(top.stdout.strip()).resolve(), common_dir=common_dir,
                        origin=origin, key=repo_key(normalized))
