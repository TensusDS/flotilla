"""What this machine has free right now: memory, disk, and how big a tree is - the machine signals of fleet sizing
(design, section 2.1). Every reader answers None when it could not read, so the sizing names the signal as unknown
instead of resting on a guess."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

_PAGE = re.compile(r"page size of (\d+) bytes")
_PAGES = re.compile(r"^Pages (free|inactive|speculative):\s+(\d+)\.?\s*$", re.MULTILINE)


def parse_vm_stat(text: str) -> int | None:
    """Free memory in MB from macOS `vm_stat`: free + inactive + speculative pages (what the system hands out without
    swapping), times the page size its own first line names - 16 KB on Apple silicon, 4 KB on Intel."""
    page = _PAGE.search(text)
    counts = {name: int(value) for name, value in _PAGES.findall(text)}
    if not page or "free" not in counts:
        return None
    pages = counts["free"] + counts.get("inactive", 0) + counts.get("speculative", 0)
    return pages * int(page.group(1)) // 2**20


def available_mb(*, meminfo: Path = Path("/proc/meminfo"), vm_stat_text: str | None = None, run=subprocess.run,
                 os_name: str = sys.platform) -> int | None:
    """Memory available for new work, in MB: Linux `MemAvailable`, macOS `vm_stat`; None elsewhere or unreadable."""
    if os_name.startswith("linux"):
        try:
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
        except (OSError, ValueError, IndexError):
            return None
        return None
    if os_name == "darwin":
        if vm_stat_text is None:
            try:
                done = run(["vm_stat"], capture_output=True, text=True, check=False, timeout=5)
            except (OSError, subprocess.SubprocessError):
                return None
            if done.returncode != 0:
                return None
            vm_stat_text = done.stdout
        return parse_vm_stat(vm_stat_text)
    return None


def free_disk_mb(path) -> int | None:
    """Free disk in MB on the filesystem holding `path`; None when the path cannot be asked."""
    try:
        return shutil.disk_usage(path).free // 2**20
    except OSError:
        return None


def tree_mb(path, *, run=subprocess.run) -> int | None:
    """A tree's size on disk in MB (rounded up) by `du -sk`; None when du fails or takes over 20 seconds."""
    try:
        done = run(["du", "-sk", "--", str(path)], capture_output=True, text=True, check=False, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        kb = int(done.stdout.split()[0])
    except (ValueError, IndexError):
        return None
    return -(-kb // 1024)
