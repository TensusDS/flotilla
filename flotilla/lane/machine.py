"""The lane's other three questions: a foreign run exists, it is computing, CI on this machine took it (spec, 9).

The booking log answers "does a peer hold the lane". A peer may also run a suite without asking, so the process
table is asked for runs matching the project's run patterns - skipping shells (the run they wrap is its own
process), the caller's own ancestors and the processes of live bookings. A run that exists may not be computing:
two CPU samples tell, and a young process counts as computing even when it paused (a suite waits on I/O), while an
old one that did not compute is named and does not block. When the profile says CI runs on this machine, GitHub's
queue is asked too. Anything that could not be asked blocks, because not asked is not free.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATTERNS = ("pytest", "py.test", "playwright", "vitest", "jest", "cargo test", "go test")
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
            continue   # it ended during the sample
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
    return Answer("ci", None, f"the CI queue command exited {done.returncode}: not asked is not free")


def ci_here(profile: dict, *, run=subprocess.run, root) -> Answer:
    ci = profile.get("ci") or {}
    if ci.get("runs_on") != "this-machine":
        return Answer("ci", False, "CI does not run on this machine")
    command = (ci.get("queue_command") or "").strip()
    if command:
        return _queue(command, root, run)
    if ci.get("provider") != "github":
        return Answer("ci", None, "CI runs on this machine through a gate command, and `[ci] queue_command` is not "
                                  "set, so its queue cannot be asked; waiting cannot change that. Set queue_command "
                                  "(exit 0 idle, 1 busy), or take the lane knowingly: receipts with --no-lane",
                      lasting=True)
    rows = _gh_json(run, root, "run", "list", "--limit", "10", "--json", "status,databaseId")
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
    if "self-hosted" in labels:
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
         problem: str = "") -> Reading:
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
        named = "; ".join(f"pid {proc.pid} `{proc.command[:80]}`" for proc in busy)
        resting = "; ".join(f"pid {proc.pid} `{proc.command[:80]}`" for proc in idle)
        if busy:
            answers.append(Answer("foreign run", True, f"{len(busy)} unbooked run(s) computing: {named}"))
        elif idle:
            answers.append(Answer("foreign run", False, f"unbooked run(s) exist but do not compute, so they do not "
                                                        f"block: {resting}"))
        else:
            answers.append(Answer("foreign run", False, "no unbooked run"))
    if problem:
        answers.append(Answer("ci", None, f"the project profile could not be read, so CI on this machine could not be "
                                          f"asked: {problem}", lasting=True))
    else:
        answers.append(ci_here(profile, run=run, root=root))
    return Reading(answers, busy, idle)
