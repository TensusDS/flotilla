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


PROC_SELF_CGROUP = Path("/proc/self/cgroup")
CGROUP_ROOT = Path("/sys/fs/cgroup")
V1_UNLIMITED = 2**60   # cgroup v1 writes "no limit" as a page-rounded 2**63 - 1


def _int_file(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return int(text) if text.isdigit() else None


def _stat_field(path: Path, field: str) -> int:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition(" ")
            if name == field and value.strip().isdigit():
                return int(value)
    except (OSError, ValueError):
        pass
    return 0


def _cgroup_limits(proc_self: Path, root: Path) -> list[tuple[int, int, str]]:
    """(limit bytes, working-set bytes, where) for every memory limit on this process's cgroup and its ancestors.
    The namespace root is read too: in a container `/proc/self/cgroup` says `0::/` and the limit sits on the root's
    own memory.max (measured in `docker run -m 512m`, review of 0.7.14); a host's root carries none."""
    try:
        lines = proc_self.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return []
    found = []

    def take(limit, usage, inactive, where):
        if limit is not None and usage is not None and limit < V1_UNLIMITED:
            found.append((limit, max(0, usage - inactive), where))

    for line in lines:
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy, controllers, path = parts
        if hierarchy == "0" and controllers == "":   # cgroup v2: the group and each ancestor, the root included
            leaf = root / path.strip().lstrip("/")
            for here in (leaf, *leaf.parents):
                if here != root and root not in here.parents:
                    break
                where = "/" + str(here.relative_to(root)) if here != root else "/"
                take(_int_file(here / "memory.max"), _int_file(here / "memory.current"),
                     _stat_field(here / "memory.stat", "inactive_file"), where)
                if here == root:
                    break
        elif "memory" in controllers.split(","):      # cgroup v1's memory controller
            base = root / "memory"
            here = base / path.strip().lstrip("/")
            target = here if (here / "memory.limit_in_bytes").exists() else base
            take(_int_file(target / "memory.limit_in_bytes"), _int_file(target / "memory.usage_in_bytes"),
                 _stat_field(target / "memory.stat", "total_inactive_file"), path.strip() or "/")
    return found


def cgroup_room_mb(*, proc_self: Path = PROC_SELF_CGROUP, root: Path = CGROUP_ROOT) -> tuple[int | None, str]:
    """(MB left under the tightest memory limit on this process's cgroup or any ancestor, a note naming it), or
    (None, "") where no limit is set or nothing can be read. Used is the group's working set - its usage less the file
    cache it can drop (`inactive_file`), as container runtimes count it. In a container or a systemd slice with
    MemoryMax, MemAvailable is the host's, and a fleet sized on it is killed (review of 0.7.14)."""
    rooms = [(max(0, limit - used) // 2**20, limit, where) for limit, used, where in _cgroup_limits(proc_self, root)]
    if not rooms:
        return None, ""
    room, limit, where = min(rooms)
    return room, f"the memory limit of {limit // 2**20} MB on cgroup {where} leaves {room} MB"


def cgroup_limit_mb(*, proc_self: Path = PROC_SELF_CGROUP, root: Path = CGROUP_ROOT) -> int | None:
    """The tightest memory limit on this process's cgroup or an ancestor, in MB: the machine's size, as far as this
    process is concerned. None where no limit is set."""
    limits = [limit for limit, _used, _where in _cgroup_limits(proc_self, root)]
    return min(limits) // 2**20 if limits else None


def memory_mb(*, meminfo: Path = Path("/proc/meminfo"), vm_stat_text: str | None = None, run=subprocess.run,
              os_name: str = sys.platform, proc_self: Path = PROC_SELF_CGROUP,
              cgroup_root: Path = CGROUP_ROOT) -> tuple[int | None, str]:
    """(memory available for new work in MB, a note when a cgroup limit set it): the host's figure, or the room under
    a container's limit where that is smaller."""
    host = _host_available_mb(meminfo=meminfo, vm_stat_text=vm_stat_text, run=run, os_name=os_name)
    if not os_name.startswith("linux"):
        return host, ""
    room, note = cgroup_room_mb(proc_self=proc_self, root=cgroup_root)
    if room is not None and (host is None or room < host):
        return room, note
    return host, ""


def available_mb(**kwargs) -> int | None:
    """Memory available for new work, in MB - the host's, or a container's room where smaller."""
    return memory_mb(**kwargs)[0]


def _host_available_mb(*, meminfo: Path = Path("/proc/meminfo"), vm_stat_text: str | None = None, run=subprocess.run,
                       os_name: str = sys.platform) -> int | None:
    """The host's memory available for new work, in MB: Linux `MemAvailable`, macOS `vm_stat`; None elsewhere."""
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
