"""What Claude Code keeps about itself that flotilla reads, and whether it still has the shape flotilla expects.

Two facts a background session depends on (field test F2, F3):

Whether the plugin is enabled in a directory is asked of `claude plugin list --json` run there: it reports
`enabled` for the directory it runs in (measured 2026-09-29). Whether a directory is trusted is read from
`~/.claude.json`, `projects[<path>].hasTrustDialogAccepted`: `claude --bg` refuses an untrusted directory, and
trust is not inherited from a parent (decisions log, entry 70).

Three records whose shape `flotilla doctor` checks, because only the first is a published interface and a change in
any of them makes a check go blind without one error: the census rows of `claude agents --json`, the session registry
`<config>/sessions/<pid>.json`, and the trust record in `~/.claude.json`. The shapes were measured on Claude Code
2.1.280 and 2.1.289 (`tests/fixtures/agents-json/`). Nothing here writes Claude Code's files.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

MEASURED_ON = "2.1.289"
KINDS = frozenset({"background", "interactive"})
STATES = frozenset({"working", "blocked", "done", "failed", "stopped"})   # a background session's
STATUSES = frozenset({"busy", "idle", "waiting"})

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


def census_problems(rows: list) -> list[str]:
    """Each way the census rows differ from the measured shape, said as what stops working; [] when they match.
    A field missing from every row, or a value outside its measured set, is a drift; a row of one kind lacking an
    optional field (a retired background session has no pid or status) is not."""
    rows = [row for row in rows if isinstance(row, dict)]
    if not rows:
        return []
    found = []

    def odd(values, allowed):
        return sorted({str(value) for value in values} - allowed)

    def drift(field, what, stops):
        found.append(f"`{field}` {what}: {stops}")

    kinds = [row.get("kind") for row in rows]
    if odd(kinds, KINDS):
        drift("kind", f"is missing or new ({', '.join(odd(kinds, KINDS))})", "your own sessions cannot be told from "
              "background seats, so the watch misreads who is stuck and who is gone")
    background = [row for row in rows if row.get("kind") == "background"]
    states = [row.get("state") for row in background]
    if background and odd(states, STATES):
        drift("state", f"of a background session is missing or new ({', '.join(odd(states, STATES))})",
              "the watch cannot tell a stopped seat from a working one")
    interactive = [row.get("status") for row in rows if row.get("kind") == "interactive"]
    statuses = interactive + [row["status"] for row in background if row.get("status") is not None]
    if odd(statuses, STATUSES):
        drift("status", f"is missing or new ({', '.join(odd(statuses, STATUSES))})",
              "a session waiting on a prompt cannot be told from one that is idle")
    for field, stops in (("cwd", "a session cannot be placed in its project or its seat's tree"),
                         ("name", "sessions cannot be matched to their posts"),
                         ("pid", "the session running a command cannot be found, so ledger moves are refused")):
        if not any(field in row for row in rows):
            drift(field, "is missing from every session", stops)
    return found


def config_dir(env=os.environ, home: Path | None = None) -> Path:
    return Path(env.get("CLAUDE_CONFIG_DIR") or (home or Path.home()) / ".claude")


def session_entry(pid, session_id: str, config: Path | None = None) -> dict | None:
    """The registry entry Claude Code keeps for a running session, `<config>/sessions/<pid>.json`, when it names this
    session; None when it is missing, unreadable, or names another session (a reused pid). Never the transcript."""
    try:
        entry = json.loads(((config or config_dir()) / "sessions" / f"{int(pid)}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return entry if isinstance(entry, dict) and entry.get("sessionId") == session_id else None


def registry_readable(rows: list, config: Path | None = None) -> bool | None:
    """Whether a live session's registry entry carries how it was started; None when no session carries a pid."""
    live = [row for row in rows if isinstance(row, dict) and isinstance(row.get("pid"), int) and row.get("sessionId")]
    if not live:
        return None
    return any(isinstance((session_entry(row["pid"], row["sessionId"], config) or {}).get("entrypoint"), str)
               for row in live)


def trust_record(home: Path | None = None) -> bool | None:
    """Whether `~/.claude.json` still keeps trust under `projects`; None when there is no file to read."""
    try:
        data = json.loads(((home or Path.home()) / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return isinstance(data, dict) and isinstance(data.get("projects"), dict)
