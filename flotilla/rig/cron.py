"""The reaper's launcher and its line in the person's crontab (rig design, section 5, layer 1).

The line runs `<state>/rig/reaper.py`, never a plugin path: the plugin cache is versioned and deleted later, and a
reap run from an old version must not pin cron to it (review of 2026-10-06). The launcher trusts only the marketplace
recorded beside it, and an older flotilla never rewrites what a newer one installed. The line carries a tag with a
hash of the state directory, so a rig under another state directory never removes this one's line. Every other line
of the crontab is kept as it was, and a crontab that could not be read is never written. Every edit of the line is
made under the reaper's lock (`<state>/rig/reap.lock`), held by its caller.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from flotilla.onboard.machine import read_machine
from flotilla.rig import providers

MARK = "# flotilla rig reaper"
CARRIED = ("PATH", "FLOTILLA_STATE_DIR", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "CLAUDE_CONFIG_DIR")
LAUNCHER = Path(__file__).resolve().parent / "launcher.py"
MIN_PYTHON = (3, 11)
_TEMPORARY = ("/.cache/", "/tmp/", "/.venv/", "/venv/")


class CronError(RuntimeError):
    """crontab could not be read or written, or the line could not be made safely."""


def _version(text) -> tuple:
    try:
        return tuple(int(part) for part in str(text).split("."))
    except ValueError:
        return ()


def marketplace_of(plugin_root: Path, env=os.environ) -> str:
    base = Path(env.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude"))
    try:
        data = json.loads((base / "plugins" / "installed_plugins.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    target = os.path.realpath(plugin_root)
    for name, items in ((data.get("plugins") or {}) if isinstance(data, dict) else {}).items():
        if str(name).startswith("flotilla@") and isinstance(items, list) and any(
                isinstance(item, dict) and os.path.realpath(str(item.get("installPath") or "")) == target
                for item in items):
            return str(name)
    return ""


def install_launcher(state: Path, plugin_root: Path, *, version: str, marketplace: str) -> Path:
    folder = state / "rig"
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "reaper.py"
    try:
        recorded = json.loads((folder / "entry").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        recorded = {}
    if isinstance(recorded, dict) and _version(recorded.get("version")) > _version(version) and script.exists():
        return script   # a newer flotilla installed these; an older one never rewrites them
    text = LAUNCHER.read_text(encoding="utf-8")
    if not script.exists() or script.read_text(encoding="utf-8") != text:
        script.write_text(text, encoding="utf-8")
    os.chmod(script, 0o700)
    copies = folder / "providers"
    copies.mkdir(exist_ok=True)
    for source in providers.files():   # the last resort runs the same adapters flotilla runs every day
        target = copies / source.name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            target.write_bytes(source.read_bytes())
    (folder / "entry").write_text(json.dumps({"root": str(plugin_root), "version": version,
                                              "marketplace": marketplace}), encoding="utf-8")
    return script


def python_version(path: str) -> tuple | None:
    try:
        done = subprocess.run([path, "-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"],
                              capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _version(done.stdout.strip()) or None if done.returncode == 0 else None


def interpreter(state: Path, *, which=shutil.which, exists=os.path.exists, version_of=python_version) -> str:
    try:
        measured = ((read_machine(state) or {}).get("python3") or {}).get("path") or ""
    except (OSError, ValueError, AttributeError):
        measured = ""
    for choice in (measured, "/usr/bin/python3", which("python3") or ""):
        if not choice or not exists(choice) or any(part in choice for part in _TEMPORARY):
            continue
        found = version_of(choice)
        if found and tuple(found[:2]) >= MIN_PYTHON:
            return choice
    raise CronError("no lasting python3 of 3.11 or newer for the reaper's crontab line "
                    "(measured, /usr/bin/python3 or on PATH)")


def tag(state: Path) -> str:
    digest = hashlib.sha256(str(Path(state).resolve()).encode()).hexdigest()[:8]
    return f"{MARK} {digest}"


def reaper_line(*, launcher: Path, python: str, state: Path, env=os.environ) -> str:
    env = {**env, "FLOTILLA_STATE_DIR": str(state)}
    carried = " ".join(f"{name}={shlex.quote(env[name])}" for name in CARRIED if env.get(name))
    log = state / "rig" / "reap.log"
    line = (f"*/5 * * * * env {carried} {shlex.quote(python)} {shlex.quote(str(launcher))} "
            f">> {shlex.quote(str(log))} 2>&1 {tag(state)}")
    if "%" in line:
        raise CronError("a path or variable in the reaper's line holds `%`, which cron reads as a newline")
    return line


def _words(line: str) -> list[str]:
    try:
        return shlex.split(line.split(MARK)[0])
    except ValueError:
        return []


def launcher_of(line: str) -> str:
    words = _words(line)
    return words[words.index(">>") - 1] if ">>" in words and words.index(">>") > 0 else ""


def python_of(line: str) -> str:
    words = _words(line)
    return words[words.index(">>") - 2] if ">>" in words and words.index(">>") > 1 else ""


def _read(run) -> list[str]:
    try:
        done = run(["crontab", "-l"], capture_output=True, text=True, errors="surrogateescape", timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise CronError(f"crontab could not be run: {err}") from None
    if done.returncode == 0:
        lines = done.stdout.split("\n")   # \n only: a CR or a form feed in the person's line is theirs to keep
        return lines[:-1] if lines and lines[-1] == "" else lines
    said = (done.stderr or done.stdout or "").strip()
    if "no crontab" in said.lower():
        return []
    raise CronError(f"crontab -l failed: {said[:200]}")


def _write(lines: list[str], run) -> None:
    text = "".join(f"{line}\n" for line in lines)
    try:
        done = run(["crontab", "-"], input=text, capture_output=True, text=True, errors="surrogateescape", timeout=30,
                   check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise CronError(f"crontab could not be run: {err}") from None
    if done.returncode != 0:
        raise CronError(f"crontab - failed: {(done.stderr or done.stdout or '').strip()[:200]}")


def installed(state: Path, *, run) -> str:
    mine = tag(state)
    return next((line for line in _read(run) if line.endswith(mine)), "")


def ensure(line: str, state: Path, *, run) -> str:
    mine = tag(state)
    lines = _read(run)
    ours = [item for item in lines if item.endswith(mine)]
    if ours == [line]:
        return "unchanged"
    _write([item for item in lines if not item.endswith(mine)] + [line], run)
    return "replaced" if ours else "installed"


def remove(state: Path, *, run) -> bool:
    mine = tag(state)
    lines = _read(run)
    kept = [item for item in lines if not item.endswith(mine)]
    if len(kept) == len(lines):
        return False
    _write(kept, run)
    return True
