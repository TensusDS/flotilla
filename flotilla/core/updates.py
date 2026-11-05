"""What this machine's flotilla follows against the newest release, for doctor and for a session's start.

Light on purpose: a session's start outside a project imports this and nothing heavy (tests/test_hooks.py). GitHub
is asked only when there is a pin to compare (spec 2026-11-04-plugin-updates, part B).
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
from pathlib import Path

from flotilla.core import claude_state, paths, releases as releases_mod

SAID = "updates-said"   # when the session-start line last said a warning, in the state directory
ASK = releases_mod.cached   # the one door to GitHub; tests replace it (tests/conftest.py)


def findings(root: Path, *, env=os.environ, home: Path | None = None, run=subprocess.run, fresh: bool = False,
             releases=None) -> list[tuple[str, str, str]]:
    from flotilla import __version__
    entry = claude_state.user_entry(env, home)
    declared = claude_state._declared(claude_state._settings(root), "flotilla") or {}
    project_ref = str(declared.get("ref") or "")
    if entry is None and not project_ref:
        return []
    asked = releases() if releases else ASK(paths.state_dir(env), run, fresh=fresh)
    return claude_state.update_findings(entry, project_ref, asked, __version__)


def lines(root: Path, *, env=os.environ, home: Path | None = None, run=subprocess.run, now=None,
          releases=None) -> list[str]:
    """A warning only, at most once a day on this machine, so a fleet of seats does not repeat it."""
    now = now or dt.datetime.now(dt.timezone.utc)
    said = paths.state_dir(env) / SAID
    try:
        if now - dt.datetime.fromisoformat(said.read_text(encoding="utf-8").strip()) < dt.timedelta(days=1):
            return []
    except (OSError, ValueError, TypeError):
        pass
    found = [(detail, fix) for status, detail, fix in
             findings(root, env=env, home=home, run=run, releases=releases) if status == "warn"]
    if found:
        try:
            said.parent.mkdir(parents=True, exist_ok=True)
            said.write_text(now.isoformat(), encoding="utf-8")
        except OSError:
            pass
    return [f"updates: {detail}" + (f" (fix: {fix})" if fix else "") for detail, fix in found]
