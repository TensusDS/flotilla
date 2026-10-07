#!/usr/bin/env python3
"""flotilla's rig reaper launcher - copied into the state directory and run by cron (rig design, section 5).

Cron must keep firing when every session is dead and the plugin was updated or removed, and the plugin's cache path
is versioned and deleted later. So cron runs this file, which lives beside the journal it guards, and it imports
nothing from flotilla. Each pass:

- it runs `rig reap` of the flotilla it trusts, newest first: the versions Claude Code lists under the marketplace
  recorded in `entry` (never a plugin of the same name from another marketplace), and the recorded root itself. A
  flotilla that ran leaves a fresh `last-reap`; one that did not (a missing module, too old a Python) is skipped for
  the next;
- with none that ran on two passes in a row - one miss may be installed_plugins.json caught mid-rewrite - it is the
  last resort: for every adapter copied beside it (`providers/<name>.py`, the files flotilla runs every day) whose key
  is in place, it destroys every instance labelled with this machine's key. When every service was listed and none
  shows such an instance, it removes its own crontab line: nothing is left to guard.
"""

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import socket
import stat
import subprocess
import sys
from pathlib import Path

MARK = "# flotilla rig reaper"
MISSES = 2
LOG_LIMIT = 1_000_000
_KEY = re.compile(r"^[0-9a-f]{12}$")
_ADAPTER = re.compile(r"^[a-z][a-z0-9_]{0,30}\.py$")


def tag(state):
    return f"{MARK} {hashlib.sha256(str(Path(state).resolve()).encode()).hexdigest()[:8]}"


def _version(text):
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return None


def _usable(root):
    return (root / "bin" / "flotilla").is_file() and (root / "flotilla" / "rig" / "reaper.py").is_file()


def candidates(plugins, entry):
    try:
        recorded = json.loads(Path(entry).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recorded = {}
    found = []
    market = recorded.get("marketplace") if isinstance(recorded, dict) else ""
    if market:
        try:
            data = json.loads(Path(plugins).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        items = ((data.get("plugins") or {}).get(market) if isinstance(data, dict) else None) or []
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("installPath"):
                root, version = Path(str(item["installPath"])), _version(item.get("version"))
                if version and _usable(root):
                    found.append((version, root))
    if isinstance(recorded, dict) and recorded.get("root"):
        root, version = Path(str(recorded["root"])), _version(recorded.get("version"))
        if version and _usable(root) and all(path != root for _, path in found):
            found.append((version, root))
    return sorted(found)


def read_key(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise PermissionError(f"{path} is not a private regular file of this user")
    return Path(path).read_text(encoding="utf-8").strip()


def _load(path):
    spec = importlib.util.spec_from_file_location(f"flotilla_rig_adapter_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(argv):
    try:
        return subprocess.run(argv, timeout=900, check=False).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def _host_id():
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            found = open(path, encoding="utf-8").read().strip()
        except OSError:
            continue
        if found:
            return found
    return socket.gethostname()


HOST = _host_id   # the same answer as flotilla's settings.HOST; a test holds them equal


def _machine_key(folder):
    try:
        recorded = (folder / "machine-key.host").read_text(encoding="utf-8").strip()
    except OSError:
        recorded = ""
    if recorded and recorded != HOST():
        print(f"rig launcher: the machine key here was made on another machine ({recorded}); nothing is destroyed")
        return ""
    for name in ("machine-key", "machine-key.bak"):
        try:
            text = (folder / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if _KEY.match(text):
            return text
    return ""


def last_resort(folder, env):
    """(exit code, whether every service was listed and none showed this machine's label)."""
    machine = _machine_key(folder)
    if not machine:
        print("rig launcher: no flotilla and no machine key - nothing can be told apart; check the rental services' "
              "consoles by hand")
        return 1, False
    keys = Path(env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")) / "flotilla" / "rig"
    prefix = f"flotilla:{machine}:m"
    clear = True
    for path in sorted(p for p in (folder / "providers").glob("*.py") if _ADAPTER.match(p.name)):
        key_file = keys / f"{path.stem}.key"
        if not key_file.exists():
            continue
        try:
            adapter, key = _load(path), read_key(key_file)
            for row in adapter.instances(key):
                if str(row.get("label") or "").startswith(prefix):
                    clear = False
                    adapter.destroy(key, row["instance"])
                    print(f"rig launcher: no flotilla; destroyed {path.stem} instance {row['instance']} "
                          f"({row['label']})")
        except Exception as err:   # noqa: BLE001 - the last resort reports, it never fails silently
            clear = False
            print(f"rig launcher: no flotilla, and the last resort failed on {path.stem}: {type(err).__name__}")
    return 1, clear


def drop_line(state, run):
    mine = tag(state)
    done = run(["crontab", "-l"], capture_output=True, text=True, errors="surrogateescape", timeout=30, check=False)
    if done.returncode != 0:
        return False
    lines = done.stdout.splitlines()
    kept = [line for line in lines if not line.endswith(mine)]
    if len(kept) == len(lines):
        return False
    run(["crontab", "-"], input="".join(f"{line}\n" for line in kept), capture_output=True, text=True,
        errors="surrogateescape", timeout=30, check=False)
    return True


def _mark(path):
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def main(env=os.environ, *, folder=None, run_crontab=subprocess.run):
    here = Path(folder) if folder else Path(__file__).resolve().parent
    try:
        if (here / "reap.log").stat().st_size > LOG_LIMIT:
            (here / "reap.log").write_text("", encoding="utf-8")
    except OSError:
        pass
    claude = Path(env.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"))
    for _, root in reversed(candidates(claude / "plugins" / "installed_plugins.json", here / "entry")):
        before = _mark(here / "last-reap")
        code = _run([sys.executable, str(root / "bin" / "flotilla"), "rig", "reap"])
        if _mark(here / "last-reap") != before:
            (here / "misses").unlink(missing_ok=True)
            return code
        print(f"rig launcher: {root} did not reap (exit {code}); trying the next")
    try:
        misses = int((here / "misses").read_text(encoding="utf-8").strip() or 0) + 1
    except (OSError, ValueError):
        misses = 1
    (here / "misses").write_text(str(misses), encoding="utf-8")
    if misses < MISSES:
        print(f"rig launcher: no flotilla reaped ({misses} pass); the last resort acts on the next")
        return 1
    with open(here / "reap.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        code, clear = last_resort(here, env)
    if clear and drop_line(here.parent, run_crontab):
        print("rig launcher: no flotilla and nothing labelled left; the crontab line is removed")
    return code


if __name__ == "__main__":
    sys.exit(main())
