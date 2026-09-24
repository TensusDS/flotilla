"""The machine profile: what this computer can do, measured once and kept in the state directory.

Machine facts never go into the project (spec, section 4.5): two people on one repository share
`.flotilla/project.toml` and have different machines. Unknown values are left out, never written
as zero.
"""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from flotilla.core import platform as plat
from flotilla.onboard.tomlw import render_toml

MIN_PYTHON = (3, 11)
MACHINE_FILE = "machine.toml"
_PYTHON_PROBE = "import sys; print(sys.executable); print('%d.%d.%d' % tuple(sys.version_info[:3]))"


def memory_bytes(*, proc_root: Path = Path("/proc"), run=subprocess.run) -> int | None:
    try:
        for line in (proc_root / "meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        done = run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    value = done.stdout.strip()
    return int(value) if done.returncode == 0 and value.isdigit() else None


def path_python(*, run=subprocess.run) -> dict:
    """The python3 PATH resolves to: the interpreter Claude Code hooks run through the shebang."""
    try:
        done = run(["python3", "-c", _PYTHON_PROBE], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"ok": False, "error": f"python3 could not be run: {err}"}
    lines = done.stdout.strip().splitlines()
    try:
        path, version = lines
        parts = tuple(int(part) for part in version.split("."))
    except ValueError:
        return {"ok": False, "error": f"python3 answered unexpectedly: {done.stdout.strip()[:120]!r}"}
    if done.returncode != 0:
        return {"ok": False, "error": f"python3 exited {done.returncode}"}
    return {"ok": parts[:2] >= MIN_PYTHON, "path": path, "version": version}


def gh_state(*, which=shutil.which, run=subprocess.run) -> str:
    if not which("gh"):
        return "missing"
    try:
        done = run(["gh", "auth", "status"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "installed"
    return "authenticated" if done.returncode == 0 else "installed"


def measure_machine(*, os_name: str = sys.platform, proc_root: Path = Path("/proc"),
                    which=shutil.which, run=subprocess.run, now: dt.datetime | None = None) -> dict:
    caps = plat.probe(os_name=os_name, proc_root=proc_root, which=which)
    moment = now or dt.datetime.now(dt.timezone.utc)
    data = {
        "schema": 1,
        "measured_at": moment.isoformat(timespec="seconds"),
        "os": caps.os_name,
        "parent_pid_source": caps.parent_pid_source,
        "timeout_command": caps.timeout_command or "none",
        "has_flock": caps.has_flock,
        "gh": gh_state(which=which, run=run),
        "python3": path_python(run=run),
    }
    if caps.cpu_count:
        data["cpu_count"] = caps.cpu_count
    memory = memory_bytes(proc_root=proc_root, run=run)
    if memory is not None:
        data["memory_bytes"] = memory
    return data


def write_machine(state: Path, data: dict) -> Path:
    state.mkdir(parents=True, exist_ok=True)
    path = state / MACHINE_FILE
    header = "Measured by `flotilla onboard machine`. Facts about this computer only; re-run to refresh."
    path.write_text(render_toml(data, header=header), encoding="utf-8")
    return path


def read_machine(state: Path) -> dict | None:
    try:
        return tomllib.loads((state / MACHINE_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
