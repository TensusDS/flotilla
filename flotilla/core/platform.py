"""What this machine can do, measured rather than assumed from the OS name.

Other modules branch on these capabilities (`parent_pid_source == "procfs"`), never on
`os_name == "darwin"`, so a Linux container without /proc takes the same path as a Mac.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SUPPORTED = ("linux", "darwin")


class UnsupportedPlatform(RuntimeError):
    """flotilla runs on Linux and macOS only, and says so instead of half-working."""


@dataclass(frozen=True)
class Capabilities:
    os_name: str
    parent_pid_source: str
    timeout_command: str | None
    has_flock: bool
    python_version: tuple[int, int, int]
    cpu_count: int | None


def require_supported(os_name: str = sys.platform) -> None:
    if os_name.startswith("win") or os_name == "cygwin":
        raise UnsupportedPlatform(
            f"flotilla supports Linux and macOS; this is {os_name}. On Windows, run it inside WSL.")
    if os_name not in SUPPORTED:
        raise UnsupportedPlatform(f"flotilla supports Linux and macOS; this is {os_name}.")


def probe(*, os_name: str = sys.platform, proc_root: Path = Path("/proc"),
          which=shutil.which) -> Capabilities:
    require_supported(os_name)
    parent_source = "procfs" if (proc_root / "self" / "stat").is_file() else "ps"
    if which("timeout"):
        timeout_command = "timeout"
    elif which("gtimeout"):
        timeout_command = "gtimeout"
    else:
        timeout_command = None
    try:
        import fcntl  # noqa: F401
        has_flock = True
    except ImportError:
        has_flock = False
    return Capabilities(
        os_name=os_name,
        parent_pid_source=parent_source,
        timeout_command=timeout_command,
        has_flock=has_flock,
        python_version=tuple(sys.version_info[:3]),
        cpu_count=os.cpu_count(),
    )


def parent_pid(pid: int, source: str, *, proc_root: Path = Path("/proc"),
               run=subprocess.run) -> int | None:
    """The parent of `pid`, or None when the process is gone."""
    if source == "procfs":
        try:
            text = (proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return None
        # The command name sits in parentheses and may itself contain spaces and parentheses,
        # so the fields are read after the LAST closing parenthesis: state, then ppid.
        fields = text[text.rindex(")") + 2:].split()
        return int(fields[1])
    if source == "ps":
        done = run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, check=False)
        value = done.stdout.strip()
        if done.returncode != 0 or not value:
            return None
        return int(value)
    raise ValueError(f"unknown parent pid source: {source!r}")
