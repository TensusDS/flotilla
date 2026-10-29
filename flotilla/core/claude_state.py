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
import re
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


def running_root() -> Path:
    """The plugin copy this process runs: the directory that holds `bin/` and `flotilla/`."""
    return Path(__file__).resolve().parents[2]


def _same(recorded, path: Path) -> bool:
    try:
        return bool(recorded) and Path(str(recorded)).resolve() == path
    except (OSError, ValueError, RuntimeError):   # RuntimeError: a symlink loop, on Python 3.11 and 3.12
        return False


def _version(text) -> tuple[int, int, int] | None:
    """A dotted version to order by, padded to three parts; None for anything else (a commit sha, a word)."""
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", str(text or "").strip())
    return tuple(int(part or 0) for part in match.groups()) if match else None


def _shown(item: dict) -> str:
    return str(item.get("version") or "unknown version")


def copy_in_use(cwd: Path, *, running: Path | None = None, env=os.environ,
                home: Path | None = None) -> tuple[str, str, str] | None:
    """(status, detail, fix) for the copy of flotilla running here against the one the project installed, or None
    when Claude Code's record of installs (`<config>/plugins/installed_plugins.json`) cannot be read or names none.
    `cwd` is where the session started: a project's install applies there, not in a directory below it (measured
    with `claude plugin list`, ai-os til plugin-scope-project-enabledplugins, 2026-07-08). Which copy loads when the
    user and a project both install flotilla is not documented; measured, the user's did (void-lanes-online on 0.23.1
    ran 0.21.0, 2026-10-29; the khrushchevka fleet on 0.13.0 ran 0.10.2, 2026-10-12). Only the copy that runs can say
    so: an older one, from before 0.23.2, says nothing."""
    try:
        data = json.loads((config_dir(env, home) / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    plugins = data.get("plugins") if isinstance(data, dict) else None
    records = [(str(name), item) for name, items in (plugins if isinstance(plugins, dict) else {}).items()
               if str(name).startswith("flotilla@") and isinstance(items, list)
               for item in items if isinstance(item, dict)]
    if not records:
        return None
    running = Path(running or running_root()).resolve()
    here = Path(cwd).resolve()

    def installed_in(directory: Path):
        found = [(name, item) for name, item in records if item.get("scope") in ("local", "project")
                 and _same(item.get("projectPath"), directory)]
        return min(found, key=lambda pair: pair[1].get("scope") != "local") if found else None
    mine = installed_in(here)
    above = None if mine else next(((directory, found) for directory in here.parents
                                    if (found := installed_in(directory))), None)
    user = next(((name, item) for name, item in records if item.get("scope") == "user"
                 and _same(item.get("installPath"), running)), None)
    recorded = any(_same(item.get("installPath"), running) for _, item in records)
    runs = f"{_shown(user[1])} from the user scope" if user else f"the copy in {running}"
    if mine is None and above is not None:
        directory, (name, item) = above
        if _same(item.get("installPath"), running):
            return "ok", f"{_shown(item)} ({item.get('scope')} scope of {directory})", ""
        return ("warn", f"started in {here}, below {directory}, which installs {_shown(item)}: a project's "
                        f"install applies where the session starts, so the copy running here is {runs}",
                f"start the session in {directory}")
    if mine is None:
        return ("ok", f"{_shown(user[1])} (user scope; this project installs none of its own)", "") if user \
            else ("ok", f"running from {running}; this project installs none of its own", "")
    name, item = mine
    if _same(item.get("installPath"), running):
        return "ok", f"{_shown(item)} ({item.get('scope')} scope, this project's own)", ""
    own = f"this project's own {_shown(item)} ({item.get('scope')} scope)"
    other = next(((other_name, found) for other_name, found in records if found is not item
                  and found.get("scope") in ("local", "project") and _same(found.get("projectPath"), here)
                  and _same(found.get("installPath"), running)), None)
    if other is not None:   # this project's other install (third review of 6e5b1b5..6c70530)
        return ("warn", f"the copy running here is this project's {other[1].get('scope')} install {_shown(other[1])}, "
                        f"not its {item.get('scope')} install {_shown(item)}",
                f"keep one of this project's two installs: `claude plugin uninstall {other[0]} --scope "
                f"{other[1].get('scope')}` or `claude plugin uninstall {name} --scope {item.get('scope')}`, run in "
                f"{here}; then start the session again")
    if user is None:
        where = ("neither the user's install nor this project's" if recorded
                 else "outside Claude Code's install records (a --plugin-dir or a checkout)")
        return ("warn", f"the copy running here comes from {running}, {where}, not {own}",
                "start the session again; if it still runs that copy, look for a --plugin-dir in how the session "
                "is started")
    theirs, ours = _version(user[1].get("version")), _version(item.get("version"))
    if theirs is None or ours is None or theirs == ours:   # nothing to bring up: two installs (second review, I1)
        return ("warn", f"the copy running here is {user[0]} {_shown(user[1])} from the user scope, not "
                        f"{own} as {name}: another install, which Claude Code loaded",
                f"keep one of the two: remove the user install (`claude plugin uninstall {user[0]} --scope user`, "
                f"which changes what every project relying on it runs) or this project's (`claude plugin uninstall "
                f"{name} --scope {item.get('scope')}`, run in {here}); then start the session again")
    if theirs > ours:
        return ("warn", f"the copy running here is {_shown(user[1])} from the user scope, newer than {own}: "
                        "Claude Code loaded the user's",
                f"bring the project's install up: `claude plugin update {name} --scope {item.get('scope')}`, run in "
                f"{here} (it takes the marketplace's newest); the user install stays as it is")
    return ("warn", f"the copy running here is {_shown(user[1])} from the user scope, older than {own}: "
                    "Claude Code loaded the user's",
            f"update the user install (`claude plugin update {user[0]} --scope user`, which takes the marketplace's "
            f"newest, not necessarily {_shown(item)}) or remove it (`claude plugin uninstall {user[0]} --scope "
            "user`) - either changes what every project relying on it runs - then start the session again")


REPO = "TensusDS/flotilla"


def _declared(root: Path, market: str) -> dict | None:
    """The source a project's settings give the marketplace (`settings.local.json` over `settings.json`), or None."""
    found = None
    for name in ("settings.json", "settings.local.json"):
        try:
            data = json.loads((Path(root) / ".claude" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        known = data.get("extraKnownMarketplaces") if isinstance(data, dict) else None
        entry = known.get(market) if isinstance(known, dict) else None
        if isinstance(entry, dict) and isinstance(entry.get("source"), dict):
            found = entry["source"]
    return found


def marketplace_pin(root: Path, *, version: str, market: str = "flotilla", env=os.environ,
                    home: Path | None = None) -> tuple[str, str, str] | None:
    """(status, detail, fix) for the ref the project pins flotilla's marketplace to against the one this machine
    follows, or None when the project declares no such marketplace. Measured 2026-10-29 with an isolated
    CLAUDE_CONFIG_DIR: `claude plugin marketplace add <repo>#<tag> --scope project` writes the ref into the project's
    settings and installs that tag's version, and `claude plugin update` stays on it; but one marketplace per name
    serves the whole machine (`<config>/plugins/known_marketplaces.json`), and the last `marketplace add` repoints it
    for every project, while each project's settings keep their own ref."""
    declared = _declared(root, market)
    if declared is None:
        return None
    repo = str(declared.get("repo") or REPO)
    wanted = str(declared.get("ref") or "")
    if not wanted:
        return ("info", f"the {market} marketplace follows the default branch of {repo}: every update takes what is "
                        "there", f"pin it to a release: `claude plugin marketplace add {repo}#v{version} --scope "
                                 f"project`, then `claude plugin update flotilla@{market} --scope project` - the "
                                 "machine's one marketplace of that name then follows the pin for every project")
    try:
        known = json.loads((config_dir(env, home) / "plugins" / "known_marketplaces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known = {}
    entry = known.get(market) if isinstance(known, dict) else None
    source = entry.get("source") if isinstance(entry, dict) and isinstance(entry.get("source"), dict) else {}
    follows = str(source.get("ref") or "")
    if follows == wanted:
        return "ok", f"the {market} marketplace is pinned to {wanted}", ""
    return ("warn", f"this project pins the {market} marketplace to {wanted}, but this machine's follows "
                    f"{follows or 'the default branch'}: the last `claude plugin marketplace add` on a machine decides "
                    "for every project, so an update here takes that",
            f"add it again from here: `claude plugin marketplace add {repo}#{wanted} --scope project` - which repoints "
            "it for every project on this machine")


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
              "the watch cannot tell a seat that stopped (blocked or done) from one at work")
    interactive = [row.get("status") for row in rows if row.get("kind") == "interactive"]
    statuses = interactive + [row["status"] for row in background if row.get("status") is not None]
    if odd(statuses, STATUSES):
        drift("status", f"is missing or new ({', '.join(odd(statuses, STATUSES))})",
              "a session waiting on a prompt cannot be told from one that is idle")
    running = [row for row in rows if row.get("kind") == "interactive" or row.get("status") is not None]
    checked = (   # field, its type, rows that must carry it somewhere (a retired seat has no pid), what goes blind
        ("cwd", str, rows, "a session cannot be placed in its project or its seat's tree"),
        ("name", str, rows, "sessions cannot be matched to their posts"),
        ("pid", int, running, "the session running a command cannot be found, so ledger moves are refused"),
        ("id", str, background, "a seat cannot be stopped or attached to by its id"))
    for field, kind_of, where, stops in checked:
        if not where:
            continue
        carried = [row[field] for row in where if field in row]
        if not carried:
            drift(field, "is missing from every session", stops)
        elif not any(isinstance(value, kind_of) and not isinstance(value, bool) for value in carried):
            drift(field, f"is no longer a {kind_of.__name__}", stops)
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
    """Whether a live session's registry entry carries how it was started. Measured only for interactive sessions,
    so its absence is drift only when one of them is listed with a pid; None when none is."""
    live = [row for row in rows if isinstance(row, dict) and isinstance(row.get("pid"), int) and row.get("sessionId")]
    if any(isinstance((session_entry(row["pid"], row["sessionId"], config) or {}).get("entrypoint"), str)
           for row in live):
        return True
    return False if any(row.get("kind") == "interactive" for row in live) else None


def trust_record(home: Path | None = None) -> bool | None:
    """Whether `~/.claude.json` still keeps trust as `projects[<path>].hasTrustDialogAccepted`; None when there is
    no file to read. A file with projects of which none carries the flag has moved it."""
    try:
        data = json.loads(((home or Path.home()) / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    projects = data.get("projects") if isinstance(data, dict) else None
    if not isinstance(projects, dict):
        return False
    entries = [entry for entry in projects.values() if isinstance(entry, dict)]
    return not entries or any(isinstance(entry.get("hasTrustDialogAccepted"), bool) for entry in entries)
