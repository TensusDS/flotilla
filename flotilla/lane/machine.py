"""The lane's other four questions: a foreign run exists, it is computing, memory is under the floor, CI on this
machine took it (spec, 9).

The booking log answers "does a peer hold the lane". A peer may also run a suite without asking, so the process
table is asked for runs matching the project's run patterns - skipping shells (the run they wrap is its own
process), the caller's own ancestors and the processes of live bookings. A run that exists may not be computing:
two CPU samples tell, and a young process counts as computing even when it paused (a suite waits on I/O), while an
old one that did not compute is named and does not block. Memory is asked because a run started under pressure is
killed, booked or not; where it cannot be asked it does not block. When the profile says CI runs on this machine,
GitHub's queue is asked too. Anything else that could not be asked blocks, because not asked is not free.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from flotilla.core.text import visible

#: A headless browser is a run (a capture or an audit takes gigabytes); a full `chrome` is not, because a browser
#: tool server keeps one alive for hours.
DEFAULT_PATTERNS = ("pytest", "py.test", "playwright", "vitest", "jest", "cargo test", "go test",
                    "chrome-headless-shell", "headless_shell")
MEMORY_FLOOR_MB = 1500
MEMINFO = Path("/proc/meminfo")
INTERPRETER = re.compile(r"^(python[0-9.]*|node|nodejs|ruby|perl|bun|deno)$")
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "fish", "ksh"})
YOUNG = 1800.0
SAMPLE = 2.0
IN_PROGRESS = ("in_progress", "queued", "requested", "waiting", "pending")


@dataclass(frozen=True)
class Answer:
    question: str
    blocks: bool | None
    text: str
    lasting: bool = False   # an unknown that waiting cannot change: refuse at once


@dataclass(frozen=True)
class Reading:
    answers: list
    computing: list
    idle: list


def patterns_for(profile: dict) -> list[str]:
    own = (profile.get("lane") or {}).get("run_patterns")
    return list(own) if own else list(DEFAULT_PATTERNS)


def program_of(command: str) -> list[str]:
    """The names a process runs under: its program (an interpreter's script or `-m` module), and "program first-arg"."""
    words = command.split()
    if not words:
        return []
    program, rest = Path(words[0]).name, words[1:]
    if INTERPRETER.match(program):
        if "-m" in rest and rest.index("-m") + 1 < len(rest):
            at = rest.index("-m") + 1
            program, rest = rest[at], rest[at + 1:]
        else:
            script = next((word for word in rest if not word.startswith("-")), None)
            if script is None:
                return [program]
            at = rest.index(script)
            program, rest = Path(script).name, rest[at + 1:]
    return [program, f"{program} {rest[0]}"] if rest else [program]


def read_meminfo(path: Path = MEMINFO, field: str = "MemAvailable") -> int | None:
    """One field of meminfo in kB, or None when the file is missing, unreadable or does not carry it (macOS has
    none)."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        name, _, value = line.partition(":")
        if name.strip() == field:
            words = value.split()
            return int(words[0]) if words and words[0].isdigit() else None
    return None


def _meminfo() -> int | None:
    """MemAvailable in kB, or the room under a container's memory limit where smaller (review of 0.7.14)."""
    from flotilla.core import resources
    host = read_meminfo(MEMINFO)
    room, _ = resources.cgroup_room_mb()
    return room * 1024 if room is not None and (host is None or room * 1024 < host) else host


def _memtotal() -> int | None:
    return read_meminfo(MEMINFO, "MemTotal")


def memory_floor(profile: dict, total_kb: int | None = None) -> tuple[int, str]:
    """The profile's floor; without one, 1500 MB, or a quarter of the machine where that is less, so a small
    machine's lane is not closed by its ordinary state. A floor that is not a whole number gives that default, and
    the note says so."""
    from flotilla.core.config import whole_number
    default = min(MEMORY_FLOOR_MB, total_kb // 1024 // 4) if total_kb else MEMORY_FLOOR_MB
    lane = profile.get("lane") or {}
    if "memory_floor_mb" not in lane:
        return default, ""
    return whole_number(lane["memory_floor_mb"], default, "[lane] memory_floor_mb")


def memory_floor_mb(profile: dict, total_kb: int | None = None) -> int:
    return memory_floor(profile, total_kb)[0]


def memory(profile: dict, meminfo, memtotal=None) -> Answer:
    """Low memory holds the lane: a run started under pressure is killed, booked or not (field test H41). Memory
    that cannot be asked opens it, because an unknown that waiting cannot change would close the lane for ever."""
    floor, note = memory_floor(profile, (memtotal or _memtotal)())
    said = f"; {note}" if note else ""
    if floor == 0:
        return Answer("memory", False, "memory not asked: `[lane] memory_floor_mb = 0`")
    available = meminfo()
    if available is None:
        return Answer("memory", False, f"memory not asked on this platform (no MemAvailable in /proc/meminfo){said}")
    megabytes = available // 1024
    if available < floor * 1024:
        return Answer("memory", True, f"{megabytes} MB available, under the floor of {floor} MB "
                                      f"(`[lane] memory_floor_mb`); a run started now may be killed{said}")
    return Answer("memory", False, f"{megabytes} MB available (floor {floor} MB){said}")


#: A word naming an MCP server package: `@playwright/mcp@latest`, `mcp-server-playwright`, `chrome-devtools-mcp`.
#: Never a flag - a Claude session's `--strict-mcp-config` is not a server.
MCP_SERVER = re.compile(r"(?:^|/)(?:@[\w.-]+/mcp|mcp-server-[\w.-]+|[\w.-]+-mcp)(?:@[\w.-]+)?$")


def _is_mcp_server(command: str) -> bool:
    return any(MCP_SERVER.search(word) for word in command.split() if not word.startswith("-"))


def foreign_runs(table, patterns, exclude: set[int]) -> list | None:
    """Unbooked runs, known by their program (never by a word in their arguments), counted once per process tree."""
    listed = table.list()
    if listed is None:
        return None
    regexes = [re.compile(pattern) for pattern in patterns]
    matched = {}
    for proc in listed:
        words = proc.command.split()
        if proc.pid in exclude or not words or Path(words[0]).name in SHELLS:
            continue
        if any(regex.fullmatch(name) for regex in regexes for name in program_of(proc.command)):
            matched[proc.pid] = proc
    parents = {proc.pid: proc.ppid for proc in listed}
    commands = {proc.pid: proc.command for proc in listed}

    def under_a_tool(pid: int) -> bool:
        """A browser an MCP server opened lives as long as the session that uses it: a tool, not a run with an end
        to wait for (twosuns, 2026-10-05)."""
        seen = set()
        while pid and pid not in seen:
            if _is_mcp_server(commands.get(pid, "")):
                return True
            seen.add(pid)
            pid = parents.get(pid)
        return False
    matched = {pid: proc for pid, proc in matched.items() if not under_a_tool(pid)}

    def under_a_match(pid: int) -> bool:
        seen, pid = set(), parents.get(pid)
        while pid and pid not in seen:
            if pid in matched:
                return True
            seen.add(pid)
            pid = parents.get(pid)
        return False
    return [proc for proc in matched.values() if not under_a_match(proc.pid)]


def split_computing(table, runs, *, sleep, sample: float = SAMPLE, young: float = YOUNG) -> tuple[list, list]:
    if not runs:
        return [], []
    first = {proc.pid: table.cpu_seconds(proc.pid) for proc in runs}
    sleep(sample)
    busy, idle = [], []
    for proc in runs:
        before, after = first[proc.pid], table.cpu_seconds(proc.pid)
        if after is None and before is not None:
            exists = getattr(table, "exists", None)
            if exists is not None and exists(proc.pid) is False:
                continue   # it ended during the sample
            busy.append(proc)   # the sample failed (`ps` did not answer), the run did not: unknown is computing
            continue
        if before is None or after is None or after > before:
            busy.append(proc)   # growing, or unreadable: unknown counts as computing
            continue
        age = table.age_seconds(proc.pid)
        (busy if age is None or age < young else idle).append(proc)
    return busy, idle


def _gh_json(run, root, *args):
    try:
        done = run(["gh", *args], cwd=str(root), capture_output=True, text=True, check=False, timeout=25)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout or "null")
    except ValueError:
        return None


def _queue(command: str, root, run) -> Answer:
    """The project's own answer to "is CI using this machine now": exit 0 no, 1 yes, anything else unknown."""
    try:   # through the shell, as the gate and revision commands are run
        done = run(command, shell=True, cwd=str(root), capture_output=True, text=True, check=False, timeout=25)
    except (OSError, subprocess.SubprocessError) as err:
        return Answer("ci", None, f"the CI queue command could not be run ({err}): not asked is not free")
    first = (done.stdout or "").strip().splitlines()[:1]
    detail = f": {first[0]}" if first else ""
    if done.returncode == 0:
        return Answer("ci", False, f"the CI queue command says CI is not using this machine{detail}")
    if done.returncode == 1:
        return Answer("ci", True, f"the CI queue command says CI is using this machine{detail}")
    if done.returncode == 127:   # the shell found no such command: waiting cannot change that
        return Answer("ci", None, f"the CI queue command `{command}` was not found (exit 127); fix `[ci] "
                                  "queue_command`", lasting=True)
    return Answer("ci", None, f"the CI queue command exited {done.returncode}: not asked is not free")


def ci_here(profile: dict, *, run=subprocess.run, root) -> Answer:
    ci = profile.get("ci") or {}
    if ci.get("runs_on") != "this-machine":
        return Answer("ci", False, "CI does not run on this machine")
    raw = ci.get("queue_command")
    if raw is not None and not isinstance(raw, str):
        return Answer("ci", None, f"`[ci] queue_command` is not a command line ({type(raw).__name__}); waiting cannot "
                                  "change that. Set it to the command that answers (exit 0 idle, 1 busy)",
                      lasting=True)
    command = (raw or "").strip()
    if command:
        return _queue(command, root, run)
    if ci.get("provider") != "github":
        return Answer("ci", None, "CI runs on this machine through a gate command, and `[ci] queue_command` is not "
                                  "set, so its queue cannot be asked; waiting cannot change that. Set queue_command "
                                  "(exit 0 idle, 1 busy), or take the lane knowingly: receipts with --no-lane",
                      lasting=True)
    # one call over the hundred newest runs: ten hid an older run still going (TODO, lane), and a call per status
    # cost five a poll at one poll every 15 s, against GitHub's hourly quota (review of 0.7.11)
    rows = _gh_json(run, root, "run", "list", "--limit", "100", "--json", "status,databaseId")
    if not isinstance(rows, list):
        return Answer("ci", None, "GitHub's CI queue could not be asked: not asked is not free")
    running = [row for row in rows if row.get("status") in IN_PROGRESS]
    if not running:
        return Answer("ci", False, "no CI run in progress")
    labels, unknown = set(), False
    for row in running:
        jobs = _gh_json(run, root, "api", f"repos/{{owner}}/{{repo}}/actions/runs/{row.get('databaseId')}/jobs")
        found = [job.get("labels") or [] for job in (jobs or {}).get("jobs") or []] if isinstance(jobs, dict) else []
        if not any(found):
            unknown = True
        for item in found:
            labels.update(item)
        if "self-hosted" in labels:   # one run on this machine answers; asking the rest only spends the quota
            return Answer("ci", True, f"a CI run is using this machine (labels: {', '.join(sorted(labels))})")
    if unknown:
        return Answer("ci", None, "a CI run is in progress and its runner is not named yet: not asked is not free")
    return Answer("ci", False, f"CI runs elsewhere (labels: {', '.join(sorted(labels))})")


def _descendants(listed, roots: set[int]) -> set[int]:
    children: dict[int, list[int]] = {}
    for proc in listed:
        children.setdefault(proc.ppid, []).append(proc.pid)
    found, stack = set(), list(roots)
    while stack:
        pid = stack.pop()
        for child in children.get(pid, []):
            if child not in found:
                found.add(child)
                stack.append(child)
    return found


def read(lanes, table, profile: dict, *, own_pid: int, root, sleep=time.sleep, run=subprocess.run,
         problem: str = "", meminfo=None, memtotal=None) -> Reading:
    exclude = set(table.ancestors(own_pid))
    booked = {item.pid for item in [*lanes.holders(), *lanes.waiters()] if item.pid and lanes.live(item)}
    listed = table.list() or []
    exclude |= booked | _descendants(listed, booked)   # booked runs, waiting or holding, are nobody's foreign run
    runs = foreign_runs(table, patterns_for(profile), exclude)
    answers = []
    if runs is None:
        answers.append(Answer("foreign run", None, "the process table could not be read: not asked is not free"))
        busy, idle = [], []
    else:
        busy, idle = split_computing(table, runs, sleep=sleep)
        # a command line is whatever its author typed: shown as data, so it cannot forge a line sessions read (F14)
        named = "; ".join(f"pid {proc.pid} `{visible(proc.command[:80])}`" for proc in busy)
        resting = "; ".join(f"pid {proc.pid} `{visible(proc.command[:80])}`" for proc in idle)
        if busy:
            answers.append(Answer("foreign run", True, f"{len(busy)} unbooked run(s) computing: {named}"))
        elif idle:
            answers.append(Answer("foreign run", False, f"unbooked run(s) exist but do not compute, so they do not "
                                                        f"block: {resting}"))
        else:
            answers.append(Answer("foreign run", False, "no unbooked run"))
    answers.append(memory(profile, meminfo or _meminfo, memtotal or _memtotal))
    if problem:
        answers.append(Answer("ci", None, f"the project profile could not be read, so CI on this machine could not be "
                                          f"asked: {problem}", lasting=True))
    else:
        answers.append(ci_here(profile, run=run, root=root))
    return Reading(answers, busy, idle)
