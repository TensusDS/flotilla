"""Test commands the repository already declares.

Found here, confirmed by the human in the questionnaire, run once before they are written down
(spec, section 4). A declaration known to be a placeholder is noted and never proposed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from flotilla.onboard.files import read_text

NPM_PLACEHOLDER = "no test specified"
_NODE_LOCKS = (("pnpm-lock.yaml", "pnpm test"), ("yarn.lock", "yarn test"),
               ("bun.lock", "bun run test"), ("bun.lockb", "bun run test"))


def _python_tier(root: Path, notes: list[str]) -> dict | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    uses_pytest = ("pytest" in read_text(pyproject) or (root / "tests").is_dir()
                   or (root / "pytest.ini").is_file())
    if not uses_pytest:
        notes.append("pyproject.toml found, but no sign of pytest; no Python tier proposed")
        return None
    if (root / "uv.lock").is_file():
        return {"name": "python", "command": "uv run pytest", "source": "pyproject.toml + uv.lock"}
    if (root / "poetry.lock").is_file():
        return {"name": "python", "command": "poetry run pytest", "source": "pyproject.toml + poetry.lock"}
    return {"name": "python", "command": "python3 -m pytest", "source": "pyproject.toml"}


def _node_tier(root: Path, notes: list[str]) -> dict | None:
    package = root / "package.json"
    if not package.is_file():
        return None
    try:
        data = json.loads(read_text(package))
    except ValueError:
        notes.append("package.json is not valid JSON; no Node tier proposed")
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    script = scripts.get("test") if isinstance(scripts, dict) else None
    if not isinstance(script, str) or not script.strip():
        return None
    if NPM_PLACEHOLDER in script:
        notes.append("package.json has npm's placeholder test script (it always fails); no Node tier proposed")
        return None
    for lock, command in _NODE_LOCKS:
        if (root / lock).is_file():
            return {"name": "node", "command": command, "source": f"package.json + {lock}"}
    return {"name": "node", "command": "npm test", "source": "package.json"}


def detect_tiers(root: Path) -> tuple[list[dict], list[str]]:
    tiers: list[dict] = []
    notes: list[str] = []
    for tier in (_python_tier(root, notes), _node_tier(root, notes)):
        if tier:
            tiers.append(tier)
    for name, marker, command in (("rust", "Cargo.toml", "cargo test"), ("go", "go.mod", "go test ./...")):
        if (root / marker).is_file():
            tiers.append({"name": name, "command": command, "source": marker})
    makefile = root / "Makefile"
    if makefile.is_file() and re.search(r"(?m)^test\s*:", read_text(makefile)):
        tiers.append({"name": "make", "command": "make test", "source": "Makefile"})
    return tiers, notes
