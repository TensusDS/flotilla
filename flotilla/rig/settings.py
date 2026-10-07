"""The person's switch and ceilings, the provider's key file, and this machine's key in every label (rig design,
sections 4-6).

Off unless the person ran `flotilla rig enable` (which writes exactly `rig = "on"`): a value nobody meant - `yes`,
`1`, a torn file - is off. A malformed ceiling falls back to its default, never above it: doubt lowers a ceiling.

The machine key names which field machine made an instance, so two machines on one provider account never reap each
other's. It is written once with a backup; a damaged one is recovered from the backup and never silently replaced,
because a new key would make every earlier instance an orphan no pass could recognise. It is never recovered from the
journal's labels: any session can write the journal, and a forged label would adopt another machine's key (second
review of 2026-10-06).
"""

from __future__ import annotations

import os
import re
import secrets as _secrets
import socket
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from flotilla.onboard.machine import read_machine

LABEL_PREFIX = "flotilla:"
OFF_LINE = ("rig is off on this machine: rented machines are used only after the person runs "
            "`flotilla rig enable --provider <service>` (README, section \"Rented machines (rig)\")")
DEFAULTS = {"rig_max_machines": 1, "rig_max_hourly": 0.60, "rig_max_hours": 8.0}
_KEY = re.compile(r"^[0-9a-f]{12}$")
_LABEL = re.compile(r"^flotilla:([0-9a-f]{12}):(m[0-9]+)$")


class KeyRefused(RuntimeError):
    """The provider key or the machine key cannot be used as it stands."""


@dataclass(frozen=True)
class RigSettings:
    on: bool
    provider: str
    max_machines: int = 1
    max_hourly: float = 0.60
    max_hours: float = 8.0


def _number(data: dict, key: str, *, whole: bool):
    value = data.get(key, DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value <= 0:
        return DEFAULTS[key]
    if whole:
        return int(value) if float(value).is_integer() else DEFAULTS[key]
    return float(value)


def settings(state: Path) -> RigSettings:
    try:
        data = read_machine(state) or {}
    except (OSError, ValueError):
        data = {}
    provider = data.get("rig_provider")
    return RigSettings(on=data.get("rig") == "on", provider=provider if isinstance(provider, str) else "",
                       max_machines=_number(data, "rig_max_machines", whole=True),
                       max_hourly=_number(data, "rig_max_hourly", whole=False),
                       max_hours=_number(data, "rig_max_hours", whole=False))


def key_path(provider: str, env: Mapping[str, str] = os.environ) -> Path:
    """Where the person puts a rental service's key: one file per service, named by it."""
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "flotilla" / "rig" / f"{provider}.key"


def read_key(path: Path) -> str:
    """The provider key, from a regular file this user owns and only this user may read."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise KeyRefused(f"no provider key at {path}; the person puts a scoped key there (chmod 600)") from None
    if not stat.S_ISREG(info.st_mode):
        raise KeyRefused(f"{path} is not a regular file (a symlink or something else); put the key itself there")
    if info.st_uid != os.getuid():
        raise KeyRefused(f"{path} belongs to another user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise KeyRefused(f"{path} is readable by others (mode {stat.S_IMODE(info.st_mode):o}); `chmod 600 {path}`")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise KeyRefused(f"{path} is empty")
    return text


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""


def _write(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(value + "\n")
    os.chmod(path, 0o600)


def _host_id() -> str:
    """Which computer this is: the OS's machine id, else the host name. A state directory copied to another computer
    carries its machine key along, and two computers labelling with one key reap each other's instances as orphans
    (final review of 0.8.0, I-2)."""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            found = open(path, encoding="utf-8").read().strip()
        except OSError:
            continue
        if found:
            return found
    return socket.gethostname()


#: Which computer this is; tests replace it.
HOST = _host_id


def _same_host(folder: Path) -> None:
    here, recorded = HOST(), _read(folder / "machine-key.host")
    if not recorded:
        if (folder / "machine-key").exists() or (folder / "machine-key.bak").exists():
            _write(folder / "machine-key.host", here)   # a key made before hosts were recorded: it is this one's
        return
    if recorded != here:
        raise KeyRefused(f"the machine key in {folder} was made on another machine ({recorded}); a copied state "
                         "directory would reap that machine's instances as orphans, so nothing is destroyed here - "
                         "the person removes machine-key, machine-key.bak and machine-key.host to give this "
                         "machine its own")


def machine_key(state: Path) -> str:
    folder = state / "rig"
    main, backup = folder / "machine-key", folder / "machine-key.bak"
    if folder.exists():
        _same_host(folder)
    found, spare = _read(main), _read(backup)
    if _KEY.match(found):
        if spare != found:
            _write(backup, found)
        return found
    if _KEY.match(spare):
        _write(main, spare)
        return spare
    if main.exists() or backup.exists():
        raise KeyRefused(f"the machine key in {main} is damaged and its backup too; instances made under it would no "
                         "longer be recognised, so it is not replaced - the person restores it, or removes both "
                         "files knowingly")
    folder.mkdir(parents=True, exist_ok=True)
    made = _secrets.token_hex(6)
    _write(main, made)
    _write(backup, made)
    _write(folder / "machine-key.host", HOST())
    return made


def label(key: str, machine_id: str) -> str:
    return f"{LABEL_PREFIX}{key}:{machine_id}"


def key_of_label(text: str) -> tuple[str, str] | None:
    found = _LABEL.match(text or "")
    return (found.group(1), found.group(2)) if found else None
