"""The model a fleet runs on (fleet model design, section 5).

The fleet's default is named by the project's profile, `[fleet] model` - never inherited from what Claude Code last
saved: Claude Code's `/model` writes its choice as the default of every later session, and a fleet once ran for hours
on a model switched for one task, until its limit stopped every seat. The profile is read from trunk; a name the
person gave on this machine and not yet landed there is kept in a local note, which answers until trunk names a
model of its own.

Names are compared by family: an alias (`opus`), a full id (`claude-opus-5-5`) and a variant (`opus[1m]`) are one
model; two full ids are compared exactly.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from flotilla.fleet.launch import LaunchError

#: Words an older profile used for "no model named": inherit what Claude Code runs (`one`, or nothing), or that and
#: the strongest model for reviewers.
LEGACY = ("one", "reviewer-strongest", "")
#: The model the strongest-reviewer choice gave reviewers before it was named (`launch.STRONGEST` until 0.32).
STRONGEST_REVIEWERS = "opus"
NOTE = "fleet-model.json"
FROM_PROFILE, FROM_NOTE = "profile", "local note (not on trunk yet)"
ANSWER = "! flotilla fleet model <name>"


class ModelUnnamed(LaunchError):
    """The fleet's default model is not named; the message says how to name it."""


def family(name: str) -> str:
    """`claude-opus-5-5`, `opus[1m]`, `opusplan` -> `opus`; a name of no known shape is itself."""
    base = re.sub(r"\[[^\]]*\]$", "", name.strip().lower())
    found = re.fullmatch(r"claude-([a-z]+)(?:-.*)?", base)
    if found:
        return found.group(1)
    if base.endswith("plan") and base[:-4]:
        return base[:-4]
    return base


def same(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    if a.startswith("claude-") and b.startswith("claude-"):
        return re.sub(r"\[[^\]]*\]$", "", a) == re.sub(r"\[[^\]]*\]$", "", b)
    return family(a) == family(b)


def _named(profile: dict) -> str:
    value = (profile.get("fleet") or {}).get("model")
    return value.strip() if isinstance(value, str) and value.strip() not in LEGACY else ""


def _notes(state: Path) -> dict:
    try:
        data = json.loads((Path(state) / NOTE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def note_for(state: Path, main: Path) -> dict | None:
    found = _notes(state).get(str(main))
    return found if isinstance(found, dict) and isinstance(found.get("model"), str) and found["model"] else None


def _write(state: Path, data: dict) -> None:
    path = Path(state) / NOTE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_note(state: Path, main: Path, name: str, *, replaced: str, now) -> None:
    """The person named `name` on this machine; trunk's profile said `replaced` then."""
    data = _notes(state)
    data[str(main)] = {"model": name, "replaced": replaced, "at": now.isoformat(timespec="seconds")}
    _write(state, data)


def drop_note(state: Path, main: Path) -> None:
    data = _notes(state)
    if data.pop(str(main), None) is not None:
        _write(state, data)


def default(profile: dict, *, state: Path, main: Path) -> tuple[str, str]:
    """The fleet's default model and where it was read: trunk's profile, or the person's note on this machine while
    trunk still says what the note replaced. ("", "") when neither names one."""
    trunk = _named(profile)
    note = note_for(state, main)
    if note is not None:
        said = (profile.get("fleet") or {}).get("model")
        said = said if isinstance(said, str) else ""
        if trunk and said != note.get("replaced"):   # trunk has a model of its own since: it wins
            drop_note(state, main)
            return trunk, FROM_PROFILE
        return note["model"], FROM_NOTE
    return (trunk, FROM_PROFILE) if trunk else ("", "")


def reviewers(profile: dict) -> str:
    """The model posts that may accept run on, when the profile names one for them; "" when they take the default."""
    fleet = profile.get("fleet") or {}
    named = fleet.get("reviewers")
    if isinstance(named, str) and named.strip():
        return named.strip()
    return STRONGEST_REVIEWERS if fleet.get("model") == "reviewer-strongest" else ""


def with_model(text: str, name: str) -> str:
    """The profile's text with `[fleet] model = "<name>"`, every other line kept as written. A strongest-reviewer
    profile gets `reviewers = "opus"` beside it, so its reviewers stay where they were."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "[fleet]"), None)
    entry = f'model = "{name}"'
    if start is None:
        return text.rstrip("\n") + f"\n\n[fleet]\n{entry}\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("[")), len(lines))
    at = next((i for i in range(start + 1, end) if re.match(r"\s*model\s*=", lines[i])), None)
    strongest = at is not None and re.match(r'\s*model\s*=\s*"reviewer-strongest"', lines[at])
    has_reviewers = any(re.match(r"\s*reviewers\s*=", lines[i]) for i in range(start + 1, end))
    extra = [f'reviewers = "{STRONGEST_REVIEWERS}"'] if strongest and not has_reviewers else []
    if at is None:
        lines[start + 1:start + 1] = [entry]
    else:
        lines[at:at + 1] = [entry, *extra]
    return "\n".join(lines) + "\n"


def recorded(row) -> str:
    """The model a seat's post row says it was raised on: its `reserve` event's evidence (fleet model design 5.4);
    "" for a seat raised before 0.32."""
    for event in row.history:
        if event.get("move") == "reserve":
            value = (event.get("evidence") or {}).get("model")
            return value if isinstance(value, str) else ""
    return ""


def unnamed_message() -> str:
    return ("the fleet's model is not named: flotilla no longer lets seats inherit the model Claude Code last saved "
            "(a /model switch for one task would carry every seat with it). The person names it: "
            f"`{ANSWER}`, such as `! flotilla fleet model opus`")


def resolved(profile: dict, *, state: Path, main: Path) -> dict:
    """A copy of the profile whose `[fleet] model` is the default and `[fleet] reviewers` the reviewers' model."""
    name, _source = default(profile, state=state, main=main)
    if not name:
        raise ModelUnnamed(unnamed_message())
    out = copy.deepcopy(profile)
    fleet = out.setdefault("fleet", {})
    who = reviewers(profile)
    fleet["model"] = name
    if who:
        fleet["reviewers"] = who
    return out
