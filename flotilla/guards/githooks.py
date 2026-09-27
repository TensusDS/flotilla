"""The git hooks behind the command guards: `pre-commit` (file reservation) and `pre-push` (the second barrier).

A hook reaches flotilla through a stable link, `<state>/bin/flotilla`, which every session start and every
install point at the plugin that is running: the plugin's own path moves with each update, a hook file does not.
Install never replaces a hook flotilla did not write and never writes into `core.hooksPath` (a hook manager's
directory, often committed); it prints the line to add there instead. Hooks live in the common git directory, so
one install covers every worktree of the clone.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

NAMES = ("pre-commit", "pre-push")
MARK = "# flotilla-hook:"


def link_path(state_dir) -> Path:
    return Path(state_dir) / "bin" / "flotilla"


def refresh_link(state_dir, cli) -> bool:
    link = link_path(state_dir)
    try:
        if link.is_symlink() and os.readlink(link) == str(cli):
            return True
        link.parent.mkdir(parents=True, exist_ok=True)
        staged = link.with_name(".flotilla.new")
        staged.unlink(missing_ok=True)
        staged.symlink_to(cli)
        os.replace(staged, link)
        return True
    except OSError:
        return False


def script(name: str) -> str:
    missing = ('  if [ -n "${FLOTILLA_GATE_OVERRIDE:-}" ]; then\n'
               '    echo "flotilla pre-push: override accepted, not recorded (flotilla is missing)" >&2\n'
               "    exit 0\n  fi\n  exit 1\n") if name == "pre-push" else "  exit 0\n"
    return (f"#!/bin/sh\n{MARK} {name}\n"
            "# Installed by `flotilla guard install`. Remove this file to uninstall.\n"
            'state="${FLOTILLA_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/flotilla}"\n'
            'cli="$state/bin/flotilla"\n'
            'if [ ! -x "$cli" ]; then\n'
            f'  echo "flotilla {name}: $cli is missing; start a Claude Code session with flotilla to restore it" >&2\n'
            f"{missing}fi\n"
            + (f'exec "$cli" guard githook {name} "$@"\n' if name == "pre-push" else
               # A refusal is exit 1. Any other failure (no python3 >= 3.11 under git, a crash) is a check that
               # could not run, and a commit can be amended: it passes, and says so.
               f'"$cli" guard githook {name} "$@"\n'
               'code=$?\n'
               '[ "$code" -eq 1 ] && exit 1\n'
               f'[ "$code" -ne 0 ] && echo "flotilla {name}: could not check (exit $code); '
               'the commit goes through" >&2\n'
               "exit 0\n"))


def _git(root, *args, run):
    done = run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False, timeout=10)
    return done.stdout.strip() if done.returncode == 0 else None


def _folder(root, run) -> tuple[Path | None, str]:
    configured = _git(root, "config", "--get", "core.hooksPath", run=run)
    if configured:
        return None, configured
    common = _git(root, "rev-parse", "--git-common-dir", run=run)
    if common is None:
        return None, ""
    path = Path(common)
    return (path if path.is_absolute() else Path(root) / path) / "hooks", ""


def _line(name, link) -> str:
    return f'"{link}" guard githook {name} "$@" || exit 1'


def status(root, run=subprocess.run) -> list[tuple[str, str]]:
    folder, configured = _folder(root, run)
    found = []
    for name in NAMES:
        if folder is None:
            found.append((name, f"core.hooksPath is {configured or 'unknown'}; not checked"))
            continue
        target = folder / name
        if not target.exists():
            found.append((name, "not installed"))
        elif MARK in target.read_text(encoding="utf-8", errors="replace"):
            found.append((name, "installed"))
        else:
            found.append((name, "another tool's hook"))
    return found


def wanted(profile: dict) -> list[str]:
    names = []
    if (profile.get("reservation") or {}).get("files"):
        names.append("pre-commit")
    if (profile.get("guards") or {}).get("push_receipt"):
        names.append("pre-push")
    return names


def install(root, names, *, state_dir, cli, run=subprocess.run) -> tuple[bool, list[str]]:
    link = link_path(state_dir)
    refresh_link(state_dir, cli)
    folder, configured = _folder(root, run)
    ok, said = True, []
    for name in names:
        if folder is None:
            ok = False
            said.append(f"{name}: core.hooksPath is set ({configured}); flotilla does not write there. Add this line "
                        f"to its {name} hook: {_line(name, link)}")
            continue
        target = folder / name
        if target.exists() and MARK not in target.read_text(encoding="utf-8", errors="replace"):
            ok = False
            said.append(f"{name}: {target} is another tool's hook; flotilla does not replace it. Add this line to "
                        f"it: {_line(name, link)}")
            continue
        folder.mkdir(parents=True, exist_ok=True)
        target.write_text(script(name), encoding="utf-8")
        target.chmod(0o755)
        said.append(f"{name}: installed at {target}")
    return ok, said
