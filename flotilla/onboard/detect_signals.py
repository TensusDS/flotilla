"""Signals that make a conditional onboarding question worth asking (spec, section 4.3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from flotilla.onboard.files import read_text

SHARED_CANDIDATES = ("CHANGELOG.md", "TODO.md", "HANDOFF.md")
SEQUENTIAL_CANDIDATES = ("migrations", "alembic/versions", "db/migrate", "docs/adr", "doc/adr")
DEPLOY_CANDIDATES = ("deploy", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_SIBLING = re.compile(r'(?:editable|path)\s*=\s*"(\.\./[^"]+)"')


def _package(root: Path) -> dict:
    try:
        data = json.loads(read_text(root / "package.json") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def sibling_repos(root: Path) -> list[str]:
    found = set(_SIBLING.findall(read_text(root / "uv.lock")))
    package = _package(root)
    for section in ("dependencies", "devDependencies"):
        deps = package.get(section)
        if isinstance(deps, dict):
            found.update(spec[len("file:"):] for spec in deps.values()
                         if isinstance(spec, str) and spec.startswith("file:../"))
    return sorted(found)


def deployment(root: Path) -> list[str]:
    hits = [name for name in DEPLOY_CANDIDATES if (root / name).exists()]
    hits += sorted(path.name for path in root.glob("*.service"))
    scripts = _package(root).get("scripts")
    if isinstance(scripts, dict) and scripts.get("dev"):
        hits.append("package.json scripts.dev")
    return hits


def detect_signals(root: Path) -> dict:
    return {
        "multi_repo": sibling_repos(root),
        "deployment": deployment(root),
        "shared_files": [name for name in SHARED_CANDIDATES if (root / name).is_file()],
        "sequential": [name for name in SEQUENTIAL_CANDIDATES if (root / name).is_dir()],
    }
