"""One detection of the whole repository: what the questionnaire shows as found."""

from __future__ import annotations

import subprocess
from pathlib import Path

from flotilla.core import repo
from flotilla.onboard.detect_ci import detect_ci
from flotilla.onboard.detect_repo import commit_convention, has_remote, release_info, trunk_branch
from flotilla.onboard.detect_signals import detect_signals
from flotilla.onboard.detect_tests import detect_tiers


def detect(root: Path, run=subprocess.run) -> dict:
    ident = repo.identify(root, run=run)
    top = ident.root
    normalized = repo.normalize_origin(ident.origin, base=ident.common_dir.parent) if ident.origin else ""
    trunk = trunk_branch(top, run=run)
    tiers, notes = detect_tiers(top)
    return {
        "root": str(top),
        "repo_key": ident.key,
        "origin": normalized,
        "remote": has_remote(top, run=run),
        "trunk": trunk,
        "tests": tiers,
        "notes": notes,
        "ci": detect_ci(top, normalized or None, trunk, run=run),
        "release": release_info(top, run=run),
        "commit_convention": commit_convention(top, run=run),
        "signals": detect_signals(top),
    }
