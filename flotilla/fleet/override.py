"""The fleet's work on a model other than its default, noticed (fleet model design 5.5, scenario C).

Claude Code's `/model` saves its choice as the default every later session starts on, so a switch made for one task
quietly becomes the model the person's sessions - the orchestrator's among them - run on. flotilla reads and never
writes: the `model` key of Claude Code's user settings, and the model the orchestrator started on (the SessionStart
hook's `model`) or says it runs on (`flotilla fleet model --running <name>`) - both kept, either may differ. While a
session of the orchestrator post lives and one of them is not what it should be (the fleet's default; the
orchestrator post's own model, when it names one), the orchestrator is told at most once an hour, and the person's
line carries it - shown, like the rest of that line, on a prompt a person typed. The ways out fit the cause: make it
the default, mute the note, or switch back. A mute lasts its hours, or until the models that differ change; once
nothing differs, it is gone. A seat on a borrowed model (5.3) is not an override: it is counted, not noted.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

from flotilla.fleet import model as fleet_model

FILE, LOCK = "override.json", "override.lock"
EVERY = dt.timedelta(hours=1)
KEPT_SESSIONS = 1000   # the newest session models kept; resume, /clear and compaction write a session's again
#: Words Claude Code's settings may hold that name no model of their own (an alias that moves with Claude Code).
NOT_A_MODEL = ("default",)


@dataclass(frozen=True)
class Notice:
    text: str = ""   # what is said; "" when nothing differs or no lead lives
    what: str = ""   # the models that differ, as a mute and the hourly clock compare them


def _model_word(value) -> str:
    value = value.strip() if isinstance(value, str) else ""
    return value if fleet_model.valid(value) and value.lower() not in NOT_A_MODEL else ""


def settings_model(env=os.environ, home: Path | None = None) -> str:
    """The `model` Claude Code's user settings name, or "" - on any error too. Read only, never written."""
    from flotilla.core.claude_state import config_dir
    try:
        data = json.loads((config_dir(env, home) / "settings.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ""
    return _model_word(data.get("model")) if isinstance(data, dict) else ""


def _load(state: Path) -> dict:
    try:
        data = json.loads((Path(state) / FILE).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


@contextlib.contextmanager
def _edit(state: Path):
    """The file, read and written back under a lock: hooks of many sessions write it at once."""
    import fcntl
    Path(state).mkdir(parents=True, exist_ok=True)
    with open(Path(state) / LOCK, "a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = _load(state)
        yield data
        path = Path(state) / FILE
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def _table(data: dict, key: str) -> dict:
    found = data.get(key)
    if not isinstance(found, dict):
        found = {}
        data[key] = found
    return found


def record_session_model(state: Path, session_id: str, model, *, now: dt.datetime, said: bool = False) -> None:
    """The model a session started on, or - `said` - the one it says it runs on; both are kept."""
    model = _model_word(model)
    if not session_id or not model:
        return
    with _edit(state) as data:
        sessions = _table(data, "sessions")
        entry = sessions.get(session_id) if isinstance(sessions.get(session_id), dict) else {}
        entry["said" if said else "start"] = model
        entry["at"] = now.isoformat(timespec="seconds")
        sessions[session_id] = entry
        if len(sessions) > KEPT_SESSIONS:
            newest = sorted(sessions.items(), key=lambda item: str((item[1] or {}).get("at", "")))[-KEPT_SESSIONS:]
            data["sessions"] = dict(newest)


def session_models(state: Path, session_id: str) -> list[str]:
    """The models a session started on and said it runs on, each once."""
    found = (_load(state).get("sessions") or {}).get(session_id)
    if not isinstance(found, dict):
        return []
    return list(dict.fromkeys(word for word in (_model_word(found.get("start")), _model_word(found.get("said")))
                              if word))


def _visible(text: str) -> str:
    from flotilla.core.text import visible
    return visible(text)


def check(default: str, *, lead_live: bool, settings_model: str = "", lead_models=(), lead_name: str = "",
          lead_expected: str = "") -> Notice:
    """What to say, when anything differs and a lead lives. `lead_expected` is the model the orchestrator post runs
    on (its own, else the default); `lead_models` what its session started on or said."""
    if not lead_live or not default:
        return Notice()
    expected = lead_expected or default
    if isinstance(lead_models, str):
        lead_models = [lead_models] if lead_models else []
    settings_off = settings_model if settings_model and not fleet_model.same(settings_model, default) else ""
    lead_off = next((name for name in lead_models if name and not fleet_model.same(name, expected)), "")
    if not (settings_off or lead_off):
        return Notice()
    who = _visible(lead_name) or "the orchestrator"
    where, back = [], []
    if settings_off:
        where.append(f"Claude Code's own setting (`model` in its settings.json) is {settings_off}, so every new "
                     "session starts on it")
        back.append(f"type `/model {_visible(default)}` in any session - Claude Code saves it as its default again")
    if lead_off:
        where.append(f"{who} runs on {lead_off}")
        back.append(f"type `/model {_visible(expected)}` in {who}'s session (press `s` in the picker to keep it to "
                    "that session)")
    other = settings_off or lead_off
    what = ",".join(sorted({fleet_model.family(name) for name in (settings_off, lead_off) if name}))
    text = (f"the fleet's default model is {_visible(default)}; {'; '.join(where)}. Ways out: make it the default "
            f"(`! flotilla fleet model {other}`); mute this note (`! flotilla fleet model --mute <hours>`); or switch "
            f"back - {'; and '.join(back)}.")
    return Notice(text, what)


def _when(value) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None


def observe(state: Path, repo: str, what: str, *, now: dt.datetime, note: bool) -> tuple[bool, bool]:
    """(muted, the orchestrator's note due now) for the models that differ. Nothing differing ends a mute and the
    clock; another set of models differing ends a mute at once. The note is recorded as said when due."""
    with _edit(state) as data:
        mine = _table(_table(data, "repos"), repo)
        if not what:
            mine.pop("mute", None)
            mine.pop("note", None)
            return False, False
        mute = mine.get("mute") if isinstance(mine.get("mute"), dict) else {}
        until = _when(mute.get("until"))
        muted = until is not None and now < until and mute.get("what") == what
        if mute and not muted:
            mine.pop("mute", None)
        if not note or muted:
            return muted, False
        said = mine.get("note") if isinstance(mine.get("note"), dict) else {}
        last = _when(said.get("at"))
        if last is not None and now - last < EVERY and said.get("what") == what:
            return muted, False
        mine["note"] = {"at": now.isoformat(timespec="seconds"), "what": what}
        return muted, True


def mute(state: Path, repo: str, what: str, *, hours: int, now: dt.datetime) -> dt.datetime:
    """The person's move: nothing said about these same models for `hours`."""
    until = now + dt.timedelta(hours=hours)
    with _edit(state) as data:
        _table(_table(data, "repos"), repo)["mute"] = {"until": until.isoformat(timespec="seconds"), "what": what}
    return until


def current(ledger, sessions, *, rows: dict | None = None, members: list | None = None, env=os.environ,
            home: Path | None = None) -> Notice:
    """The notice for this project now: its default, its live leads (sessions of the orchestrator post), the models
    they started on or said, and Claude Code's setting. Empty when nothing differs, no lead lives, or the default
    is not named (spawn asks for it then). `rows` and `members` spare a hook what it already read."""
    from flotilla.core import paths
    from flotilla.fleet import launch
    from flotilla.ledger import project
    from flotilla.posts import PostError, post_for_session
    post = ledger.posts.get("orchestrator")
    if post is None:
        return Notice()
    try:
        main = launch.main_checkout(ledger.root, run=ledger.run)
        resolved = fleet_model.resolved(ledger.profile, state=paths.state_dir(), main=main)
    except launch.LaunchError:   # ModelUnnamed among them
        return Notice()
    default = str(resolved["fleet"]["model"])

    def leads(name: str) -> bool:
        try:
            found = post_for_session(ledger.posts, name)
        except PostError:
            return False
        return found is not None and found.name == "orchestrator"
    if members is None:
        members = project.members(list(sessions or []), rows if rows is not None else ledger.rows(),
                                  project.roots(ledger.root, run=ledger.run))
    state = paths.state_dir()
    setting = settings_model(env, home)
    found = [check(default, lead_live=True, settings_model=setting, lead_models=session_models(state, item.session_id),
                   lead_name=item.name, lead_expected=launch.model_for(resolved, post))
             for item in members if item.name and leads(item.name)]
    return next((notice for notice in found if notice.what), Notice())
