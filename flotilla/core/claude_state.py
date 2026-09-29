"""Two facts Claude Code keeps that a background session depends on (field test F2, F3).

Whether the plugin is enabled in a directory is asked of `claude plugin list --json` run there: it reports
`enabled` for the directory it runs in (measured 2026-09-29). Whether a directory is trusted is read from
`~/.claude.json`, `projects[<path>].hasTrustDialogAccepted`: `claude --bg` refuses an untrusted directory, and
trust is not inherited from a parent (decisions log, entry 70). Nothing here writes Claude Code's files.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

INSTALL = "claude plugin install flotilla@flotilla --scope project"
MARKETPLACE = "claude plugin marketplace add TensusDS/flotilla"


def _flotilla_entries(cwd: Path, run, timeout: float) -> list[dict] | None:
    try:
        done = run(["claude", "plugin", "list", "--json"], cwd=str(cwd), capture_output=True, text=True,
                   check=False, timeout=timeout)
        entries = json.loads(done.stdout or "") if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(entries, list):
        return None
    return [entry for entry in entries if isinstance(entry, dict) and str(entry.get("id", "")).startswith("flotilla@")]


def plugin_enabled(cwd: Path, *, run=subprocess.run, timeout: float = 30) -> bool | None:
    entries = _flotilla_entries(cwd, run, timeout)
    return None if entries is None else any(entry.get("enabled") is True for entry in entries)


def install_hint(cwd: Path, *, run=subprocess.run, timeout: float = 30) -> str:
    """The command that enables flotilla here: from the marketplace it is already listed from, or, when none lists
    it, adding flotilla's own marketplace first."""
    entries = _flotilla_entries(cwd, run, timeout) or []
    if entries:
        return f"claude plugin install {entries[0]['id']} --scope project"
    return f"{MARKETPLACE}, then {INSTALL}"


def trusted(path: Path, *, home: Path | None = None) -> bool | None:
    try:
        data = json.loads(((home or Path.home()) / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    projects = data.get("projects") if isinstance(data, dict) else None
    entry = projects.get(str(Path(path).resolve())) if isinstance(projects, dict) else None
    value = entry.get("hasTrustDialogAccepted") if isinstance(entry, dict) else None
    return value if isinstance(value, bool) else None


def setup_problems(main: Path, *, run=subprocess.run, home: Path | None = None) -> tuple[list[str], list[str]]:
    """(refusals, warnings) for raising sessions in `main`; each names the command that fixes it."""
    refusals, warnings = [], []
    enabled = plugin_enabled(main, run=run)
    if enabled is False:
        refusals.append(f"flotilla is not enabled in {main}, so the sessions spawn raises there would have no "
                        f"flotilla skills or hooks: run `{install_hint(main, run=run)}` there")
    elif enabled is None:
        warnings.append(f"could not tell whether flotilla is enabled in {main} (`claude plugin list` did not "
                        "answer)")
    trust = trusted(main, home=home)
    if trust is False:
        refusals.append(f"{main} is not trusted, and `claude --bg` refuses an untrusted directory: run `claude` "
                        "there once and accept the trust dialog")
    elif trust is None:
        warnings.append(f"could not tell whether {main} is trusted (no entry in ~/.claude.json): if spawn fails, "
                        "run `claude` there once and accept the trust dialog")
    return refusals, warnings
