"""The process table, asked of procfs where the machine has it and of `ps` where it does not (spec, section 9).

The lane needs a process's parent, its command line, a mark that says "this is still the same process" (its start
time, so a reused pid is not mistaken for a live booking) and how much CPU it has used. Linux exposes them in
/proc; macOS answers through `ps` (`-A -o`, portable; `pgrep` is not, because macOS `pgrep -a` means something else).
A fact that could not be read is None or an empty mark, never zero: "could not ask" is not "idle" and not "dead".
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

try:
    CLK_TCK = os.sysconf("SC_CLK_TCK")
except (AttributeError, ValueError, OSError):
    CLK_TCK = 100


FIXED = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}   # `lstart` is printed in the locale and zone of the asker


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    command: str


def parse_clock(text: str) -> float | None:
    """`ps` time and etime, `[[dd-]hh:]mm:ss[.cc]`, in seconds."""
    text = (text or "").strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        head, text = text.split("-", 1)
        if not head.isdigit():
            return None
        days = int(head)
    try:
        parts = [float(part) for part in text.split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return days * 86400 + seconds


class ProcessTable:
    def __init__(self, source: str, *, proc_root: Path = Path("/proc"), run=subprocess.run):
        if source not in ("procfs", "ps"):
            raise ValueError(f"unknown process source {source!r}")
        self.source = source
        self.proc_root = Path(proc_root)
        self.run = run

    @classmethod
    def for_machine(cls) -> "ProcessTable":
        from flotilla.core import platform as plat
        return cls(plat.probe().parent_pid_source)

    def _stat(self, pid) -> list[str] | None:
        try:
            text = (self.proc_root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if ")" not in text:
            return None
        return text[text.rindex(")") + 2:].split()

    def _ps(self, pid, field: str) -> str | None:
        done = self.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True, check=False,
                        env=FIXED)
        value = (done.stdout or "").strip()
        return value if done.returncode == 0 and value else None

    def list(self) -> list[Proc] | None:
        if self.source == "procfs":
            try:
                entries = [entry for entry in self.proc_root.iterdir() if entry.name.isdigit()]
            except OSError:
                return None
            found = []
            for entry in entries:
                fields = self._stat(entry.name)
                try:
                    raw = (entry / "cmdline").read_bytes()
                except OSError:
                    continue
                command = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
                if fields and len(fields) > 1 and command:
                    found.append(Proc(int(entry.name), int(fields[1]), command))
            return found
        done = self.run(["ps", "-A", "-ww", "-o", "pid=", "-o", "ppid=", "-o", "command="], capture_output=True,
                        text=True, check=False, env=FIXED)
        if done.returncode != 0:
            return None
        found = []
        for line in (done.stdout or "").splitlines():
            parts = line.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                found.append(Proc(int(parts[0]), int(parts[1]), parts[2]))
        return found

    def start_mark(self, pid) -> str | None:
        """An opaque mark of when the process started; None when there is no such process."""
        if self.source == "procfs":
            fields = self._stat(pid)
            return fields[19] if fields and len(fields) > 19 else None
        return self._ps(pid, "lstart")

    def alive(self, pid: int | None, mark: str) -> bool:
        """Whether this is still the same process. No pid or no mark means unknown, which reads as alive."""
        if pid is None or not mark:
            return True
        if self.source == "procfs":
            fields = self._stat(pid)
            if fields and fields[0] in ("Z", "X"):   # ended, waiting to be reaped: not a live run
                return False
        elif (self._ps(pid, "stat") or "").startswith("Z"):   # the same where `ps` answers (macOS)
            return False
        return self.start_mark(pid) == mark

    def cpu_seconds(self, pid) -> float | None:
        if self.source == "procfs":
            fields = self._stat(pid)
            try:
                return (int(fields[11]) + int(fields[12])) / CLK_TCK
            except (TypeError, IndexError, ValueError):
                return None
        return parse_clock(self._ps(pid, "time") or "")

    def age_seconds(self, pid) -> float | None:
        if self.source == "procfs":
            fields = self._stat(pid)
            try:
                uptime = float((self.proc_root / "uptime").read_text(encoding="utf-8").split()[0])
                return uptime - int(fields[19]) / CLK_TCK
            except (OSError, TypeError, IndexError, ValueError):
                return None
        return parse_clock(self._ps(pid, "etime") or "")

    def parent(self, pid) -> int | None:
        if self.source == "procfs":
            fields = self._stat(pid)
            return int(fields[1]) if fields and len(fields) > 1 else None
        value = self._ps(pid, "ppid")
        return int(value) if value and value.isdigit() else None

    def ancestors(self, pid: int, limit: int = 64) -> list[int]:
        chain: list[int] = []
        while pid and pid > 1 and len(chain) < limit:
            chain.append(pid)
            parent = self.parent(pid)
            if parent is None or parent == pid:
                break
            pid = parent
        return chain
