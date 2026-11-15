"""The fleet's work on a model other than its default, noticed (fleet model design 5.5, scenario C).

Claude Code's `/model` saves its choice as the default every later session starts on, so a switch made for one task
quietly becomes the model the person's sessions - the orchestrator's among them - run on. flotilla reads two things
and writes neither: the `model` key of Claude Code's user settings, and the model a session reported at its start
(the SessionStart hook's `model`, or the orchestrator's own word, `flotilla fleet model --running <name>`). While a
lead lives and either names a model other than the fleet's default, the orchestrator and the person are told at
most once an hour, with three ways out: make it the default, mute the note, or switch back. A mute ends early when
what differs changes. A seat on a borrowed model (5.3) is not an override: it is counted, not noted. A switch kept
to one session after it started is not seen - flotilla reads no transcript - unless the orchestrator says so.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from flotilla.fleet import model as fleet_model

FILE = "override.json"
EVERY = dt.timedelta(hours=1)
KEPT_SESSIONS = 200   # the newest session models kept; a session's start is what is read, never its history
NOTE, LINE = "note", "line"   # the orchestrator's context, and the person's screen: one clock each


def settings_model(env=os.environ, home: Path | None = None) -> str:
    """The `model` Claude Code's user settings name, or "" - on any error too. Read only, never written."""
    from flotilla.core.claude_state import config_dir
    try:
        data = json.loads((config_dir(env, home) / "settings.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ""
    value = data.get("model") if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) and fleet_model.valid(value.strip()) else ""


def _load(state: Path) -> dict:
    try:
        data = json.loads((Path(state) / FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(state: Path, data: dict) -> None:
    path = Path(state) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _table(data: dict, key: str) -> dict:
    found = data.get(key)
    return found if isinstance(found, dict) else {}


def record_session_model(state: Path, session_id: str, model: str, *, now: dt.datetime) -> None:
    """The model a session runs on, as its start or its own word says; the newest KEPT_SESSIONS are kept."""
    model = (model or "").strip()
    if not session_id or not fleet_model.valid(model):
        return
    data = _load(state)
    sessions = _table(data, "sessions")
    sessions[session_id] = {"model": model, "at": now.isoformat(timespec="seconds")}
    newest = sorted(sessions.items(), key=lambda item: str((item[1] or {}).get("at", "")))[-KEPT_SESSIONS:]
    data["sessions"] = dict(newest)
    _save(state, data)


def session_model(state: Path, session_id: str) -> str:
    found = _table(_load(state), "sessions").get(session_id)
    value = found.get("model") if isinstance(found, dict) else None
    return value if isinstance(value, str) and fleet_model.valid(value) else ""


def differing(default: str, *, lead_live: bool, settings: str, lead: str, lead_name: str = "") -> list[str]:
    """Where the fleet's work runs on a model other than `default`, one phrase each; [] when nothing differs (by
    family) or no lead lives - without one, the person's sessions take Claude Code's own setting, by their rule."""
    if not lead_live or not default:
        return []
    found = []
    if settings and not fleet_model.same(settings, default):
        found.append(f"Claude Code's own setting (`model` in its settings.json) is {settings}")
    if lead and not fleet_model.same(lead, default):
        found.append(f"{lead_name or 'the orchestrator'} runs on {lead}")
    return found


def check(default: str, *, lead_live: bool, settings_model: str, lead_model: str, lead_name: str = "") -> str:
    """The note's text, "" when nothing differs or no lead lives."""
    where = differing(default, lead_live=lead_live, settings=settings_model, lead=lead_model, lead_name=lead_name)
    if not where:
        return ""
    other = next((name for name in (lead_model, settings_model) if name and not fleet_model.same(name, default)), "")
    return (f"the fleet's default model is {default}; {'; '.join(where)} - work of the fleet may run on it. Three "
            f"ways out: make it the default (`! flotilla fleet model {other}`); mute this note (`! flotilla fleet "
            f"model --mute <hours>`); or switch back - type `/model {default}` in the orchestrator's session and "
            "press `s` in the picker to keep it to that session (otherwise Claude Code saves it as its default for "
            "every new session).")


def _when(value) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None


def due(state: Path, repo: str, audience: str, text: str, *, now: dt.datetime) -> bool:
    """Whether the note is said to `audience` now: never while muted for the same thing, at most once an hour.
    Saying it is recorded here: the caller says it when this answers True."""
    if not text:
        return False
    data = _load(state)
    mine = _table(_table(data, "repos"), repo)
    mute = _table(mine, "mute")
    until = _when(mute.get("until"))
    if until is not None and now < until and mute.get("what") == _what(text):
        return False
    said = _when(_table(mine, audience).get("at"))
    if said is not None and now - said < EVERY and _table(mine, audience).get("what") == _what(text):
        return False
    repos = _table(data, "repos")
    mine[audience] = {"at": now.isoformat(timespec="seconds"), "what": _what(text)}
    repos[repo] = mine
    data["repos"] = repos
    _save(state, data)
    return True


def _what(text: str) -> str:
    """What the note is about, as a mute compares it: the text up to its ways out."""
    return text.split(" - work of the fleet", 1)[0]


def mute(state: Path, repo: str, text: str, *, hours: int, now: dt.datetime) -> dt.datetime:
    """The person's move: no note about this same difference for `hours`; a new difference speaks at once."""
    data = _load(state)
    repos = _table(data, "repos")
    mine = _table(repos, repo)
    until = now + dt.timedelta(hours=hours)
    mine["mute"] = {"until": until.isoformat(timespec="seconds"), "what": _what(text)}
    repos[repo] = mine
    data["repos"] = repos
    _save(state, data)
    return until


def current(ledger, sessions, *, env=os.environ, home: Path | None = None) -> str:
    """The note for this project now: its default, its live lead (a session of the orchestrator post) and the model
    that lead reported, and Claude Code's setting. "" when nothing differs, no lead lives, or the default is not
    named (spawn asks for it then)."""
    from flotilla.core import paths
    from flotilla.fleet import launch
    from flotilla.ledger import project
    from flotilla.posts import PostError, post_for_session
    try:
        main = launch.main_checkout(ledger.root, run=ledger.run)
    except launch.LaunchError:
        return ""
    default, _ = fleet_model.default(ledger.profile, state=paths.state_dir(), main=main)

    def leads(name: str) -> bool:
        try:
            post = post_for_session(ledger.posts, name)
        except PostError:
            return False
        return post is not None and post.name == "orchestrator"
    mine = project.members(list(sessions or []), ledger.rows(), project.roots(ledger.root, run=ledger.run))
    lead = next((item for item in mine if item.name and leads(item.name)), None)
    return check(default, lead_live=lead is not None, settings_model=settings_model(env, home),
                 lead_model=session_model(paths.state_dir(), lead.session_id) if lead is not None else "",
                 lead_name=lead.name if lead is not None else "")
