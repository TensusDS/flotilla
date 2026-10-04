"""How much work is waiting: the backlog signal of fleet sizing (design, section 2.3).

Four sources, each counted once and each named: the ledger's rows in flight, the tasks the orchestrator named, an
external tracker (GitHub issues) and TODO-like files. Items split into main and minor only where the source says so
(a `[minor]` prefix, a size label); everything else counts as main - a size guessed from wording is a number nobody
can check. A source that could not be read is unknown (None) and says why; it is never a silent zero.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILES = ("TODO.md",)
IN_FLIGHT = frozenset({"claimed", "fixing"})
GH_LIMIT = 500
GH_TIMEOUT = 25

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_BOX = re.compile(r"^\s*[-*+] \[[ xX]\]")
_TOP_BOX = re.compile(r"^[-*+] \[([ xX])\] ?(.*)$")
_TOP_BULLET = re.compile(r"^[-*+] (.*)$")
_DONE = re.compile(r"(done|completed|finished)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Source:
    name: str
    main: int | None
    minor: int | None
    note: str = ""


@dataclass
class Backlog:
    sources: list[Source] = field(default_factory=list)

    @property
    def main(self) -> int:
        return sum(s.main for s in self.sources if s.main is not None)

    @property
    def minor(self) -> int:
        return sum(s.minor for s in self.sources if s.minor is not None)

    @property
    def unknown(self) -> list[str]:
        return [s.name for s in self.sources if s.main is None]


def _struck(text: str) -> bool:
    text = text.strip()
    return text.startswith("~~") and text.endswith("~~") and len(text) > 4


def todo_items(text: str, *, minor_prefix: str = "[minor]") -> tuple[int, int]:
    """(main, minor) open items in a TODO-like text: unchecked top-level checkboxes, or - in a text with no checkbox at
    all - top-level bullets under a heading. Nested items (parts of an item), fenced code, struck-through items and
    sections titled Done/Completed/Finished do not count."""
    lines = text.splitlines()
    fenced, has_boxes = False, False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _BOX.match(line):
            has_boxes = True
            break
    main = minor = 0
    fenced, heading_seen, done_level = False, False, None
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            if done_level is not None and level <= done_level:
                done_level = None
            if done_level is None and _DONE.match(heading.group(2)):
                done_level = level
            heading_seen = True
            continue
        if done_level is not None:
            continue
        if has_boxes:
            item = _TOP_BOX.match(line)
            if not item or item.group(1) != " ":
                continue
            body = item.group(2)
        else:
            item = _TOP_BULLET.match(line)
            if not item or not heading_seen:
                continue
            body = item.group(1)
        if not body.strip() or _struck(body):
            continue
        if body.lstrip().startswith(minor_prefix):
            minor += 1
        else:
            main += 1
    return main, minor


def _escapes(glob: str) -> bool:
    return glob.startswith(("/", "\\")) or bool(re.match(r"^[A-Za-z]:", glob)) or ".." in Path(glob).parts


def from_files(root, globs, *, minor_prefix: str = "[minor]") -> Source:
    """TODO-like files matched by globs from the repository root. A glob leaving the root is refused (unknown), and a
    matched file that resolves outside the root - a link - is skipped: the sizing reads this project, nothing else."""
    root = Path(root)
    name = "TODO files"
    bad = [g for g in globs if _escapes(g)]
    if bad:
        return Source(name, None, None, f"refused: {', '.join(bad)} leaves the repository")
    base = root.resolve()
    seen, main, minor, read, problems = set(), 0, 0, [], []
    for glob in globs:
        for path in sorted(root.glob(glob)):
            try:
                real = path.resolve()
                real.relative_to(base)
            except (OSError, ValueError):
                continue
            if real in seen or not real.is_file():
                continue
            seen.add(real)
            try:
                text = real.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                problems.append(f"{path.relative_to(root)}: {exc.strerror or exc}")
                continue
            m, n = todo_items(text, minor_prefix=minor_prefix)
            main, minor = main + m, minor + n
            read.append(str(path.relative_to(root)))
    if problems and not read:
        return Source(name, None, None, "; ".join(problems))
    note = ", ".join(read) if read else f"no file matched {', '.join(globs)}"
    if problems:
        note += f" (unreadable: {'; '.join(problems)})"
    return Source(name, main, minor, note)


def from_tasks(count: int | None, file: Path | None, *, minor_prefix: str = "[minor]") -> Source | None:
    """The tasks the orchestrator named: a count, or a file with one task per line. None when neither was given."""
    name = "named tasks"
    if file is not None:
        try:
            lines = [line.strip() for line in Path(file).read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError as exc:
            return Source(name, None, None, f"cannot read {file}: {exc.strerror or exc}")
        minor = sum(1 for line in lines if line.startswith(minor_prefix))
        return Source(name, len(lines) - minor, minor, str(file))
    if count is not None:
        return Source(name, max(0, count), 0, "--tasks")
    return None


def from_github(root, *, labels, minor_labels, run=subprocess.run) -> Source:
    """Open GitHub issues through `gh`, filtered to any of `labels` when set; an issue with one of `minor_labels` is
    minor. No gh, gh not logged in, or an answer that is not the expected JSON: unknown, with the reason."""
    name = "GitHub issues"
    cmd = ["gh", "issue", "list", "--state", "open", "--limit", str(GH_LIMIT), "--json", "number,labels"]
    try:
        done = run(cmd, cwd=str(root), capture_output=True, text=True, check=False, timeout=GH_TIMEOUT)
    except FileNotFoundError:
        return Source(name, None, None, "gh is not installed")
    except (OSError, subprocess.SubprocessError) as exc:
        return Source(name, None, None, f"gh could not be asked: {exc}")
    if done.returncode != 0:
        reason = (done.stderr or "").strip().splitlines()
        return Source(name, None, None, f"gh exited {done.returncode}: {reason[-1] if reason else 'no message'}")
    try:
        issues = json.loads(done.stdout)
        tagged = [{label["name"] for label in issue.get("labels") or []} for issue in issues]
    except (ValueError, TypeError, KeyError, AttributeError):
        return Source(name, None, None, "gh answered something that is not the issue list")
    wanted, small = set(labels or ()), set(minor_labels or ())
    if wanted:
        tagged = [names for names in tagged if names & wanted]
    minor = sum(1 for names in tagged if names & small)
    note = f"open issues{' labelled ' + ', '.join(sorted(wanted)) if wanted else ''}"
    if len(issues) >= GH_LIMIT:
        note += f" (the first {GH_LIMIT} only)"
    return Source(name, len(tagged) - minor, minor, note)


def from_ledger(rows, profile) -> Source:
    """Rows in flight - claimed or sent back for fixing: work an author holds now (ruling of Task 3: the ledger
    never holds work nobody took, since a row is born at its claim)."""
    flying = [row for row in (rows.values() if isinstance(rows, dict) else rows) if row.state in IN_FLIGHT]
    return Source("ledger", len(flying), 0, f"{len(flying)} row(s) claimed or fixing")


def gather(root, profile, rows, *, tasks=None, tasks_file=None, run=subprocess.run) -> Backlog:
    """Every source the profile asks for: the ledger always, named tasks when given, the tracker when one is set, the
    TODO files (`TODO.md` unless `[fleet.sizing] backlog_files` says otherwise; an empty list turns them off)."""
    sizing = ((profile or {}).get("fleet") or {}).get("sizing") or {}
    prefix = sizing.get("minor_prefix", "[minor]")
    sources = [from_ledger(rows, profile)]
    named = from_tasks(tasks, Path(tasks_file) if tasks_file else None, minor_prefix=prefix)
    if named is not None:
        sources.append(named)
    tracker = sizing.get("tracker")
    if tracker == "github":
        sources.append(from_github(root, labels=sizing.get("labels") or [],
                                   minor_labels=sizing.get("minor_labels") or ["size:small"], run=run))
    elif tracker:
        sources.append(Source(f"tracker {tracker}", None, None, "only github is supported"))
    globs = sizing.get("backlog_files", list(DEFAULT_FILES))
    if globs:
        sources.append(from_files(root, globs, minor_prefix=prefix))
    return Backlog(sources)
