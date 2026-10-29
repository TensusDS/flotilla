"""Is the reaper alive and well (rig design, section 5): a crontab line that never fires looks exactly like one that
works, and a pass that could not ask the service looks like one that did, so every pass leaves a mark saying which,
and the mark is checked wherever the person looks."""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.rig import cron
from flotilla.rig import journal as j
from flotilla.rig import settings as rs

SILENT_AFTER = dt.timedelta(minutes=12)


def stamp(state: Path, now: dt.datetime, *, ok: bool = True, note: str = "") -> None:
    folder = state / "rig"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "last-reap").write_text(json.dumps({"at": now.isoformat(timespec="seconds"), "ok": ok,
                                                  "note": note[:300]}) + "\n", encoding="utf-8")


def _mark(state: Path) -> dict:
    try:
        data = json.loads((state / "rig" / "last-reap").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def last_reap(state: Path) -> dt.datetime | None:
    try:
        moment = dt.datetime.fromisoformat(str(_mark(state).get("at")))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def silent(state: Path, rig, now: dt.datetime) -> tuple[bool, dt.datetime | None]:
    """(whether the reaper is silent while something needs it, its last pass). Silence is counted from the later of
    its last pass and the moment it was needed again - a session opened: with nothing to guard it removes its own
    crontab line, so a pass from the morning says nothing about an evening session (field report of 2026-10-14)."""
    last = last_reap(state)
    # only opening a session arms the reaper again (a machine is asked for inside one): a later request must not
    # restart the clock, and a mark ahead of now - a clock that ran ahead - says nothing
    marks = [item.opened for item in rig.sessions().values() if item.state in (j.OPEN, j.CLOSING)]
    since = [moment for moment in (_moment(text) for text in marks) if moment is not None and moment <= now]
    if last is not None:
        since.append(last)
    return (not since or now - max(since) > SILENT_AFTER), last


def _moment(text: str) -> dt.datetime | None:
    try:
        moment = dt.datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return moment if moment is not None and moment.tzinfo is not None else None


def last_outcome(state: Path) -> tuple[bool, str]:
    mark = _mark(state)
    return mark.get("ok") is not False, str(mark.get("note") or "")


def user_scope(env=os.environ) -> bool:
    """Whether flotilla is installed for the user, so its guards run in every session, not only an onboarded
    project's (second review of 2026-10-06)."""
    base = Path(env.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"))
    try:
        data = json.loads((base / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return any(str(name).startswith("flotilla@") and isinstance(items, list)
               and any(isinstance(item, dict) and item.get("scope") == "user" for item in items)
               for name, items in (plugins or {}).items())


def findings(state: Path, *, now: dt.datetime, run, env=os.environ) -> list[tuple[str, str, str]]:
    on = rs.settings(state).on
    try:
        rig = j.Rig(LocalLogStore(state / "rig"))
        live = [m for m in rig.machines().values() if m.state in j.LIVE]
    except StorageCorrupt as err:
        return [("fail", f"the rig journal is damaged: {err}",
                 "run `flotilla rig reap`: it destroys this machine's labelled instances")]
    if not on and not live:
        return [("info", "off", "")]
    found = [("ok", f"on, {len(live)} machine(s) live", "")] if on else \
        [("warn", f"off, but {len(live)} machine(s) still live", "the reaper drains them")]
    if on and not user_scope(env):
        found.append(("warn", "flotilla is installed per project, so the guards on `rig enable` and the person's "
                      "files run only in those projects' sessions",
                      "install it for the user: `claude plugin install flotilla@flotilla --scope user` - that copy has "
                      "been seen loading in place of a project's own install, so keep both at the same version "
                      "(`flotilla doctor` names a mismatch)"))
    if live:
        quiet, last = silent(state, rig, now)
        if quiet:
            when = last.isoformat(timespec="minutes") if last else "never"
            found.append(("fail", f"the reaper is silent (last pass: {when}) while a machine lives",
                          "is cron running on this machine? run `flotilla rig reap` by hand now"))
        ok, note = last_outcome(state)
        if not ok:
            found.append(("fail", f"the last reaper pass did not finish its work: {note}",
                          "a revoked key or an unreachable service; see `flotilla rig`"))
        try:
            line = cron.installed(state, run=run)
        except cron.CronError as err:
            found.append(("fail", f"the crontab could not be read: {err}", ""))
        else:
            if not line:
                found.append(("fail", "no reaper line in the crontab", "run `flotilla rig reap`; it installs it"))
            elif not os.path.exists(cron.launcher_of(line)) or not os.path.exists(cron.python_of(line)):
                found.append(("fail", "the reaper's crontab line names a launcher or a python that is gone",
                              "run `flotilla rig reap`; it rewrites the line"))
    stuck = [m.id for m in live if m.state == j.STUCK]
    if stuck:
        found.append(("fail", f"STUCK machine(s) {', '.join(stuck)} may still cost money",
                      "check the rental service's console"))
    return found
