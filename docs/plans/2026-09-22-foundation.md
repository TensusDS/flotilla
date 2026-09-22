# flotilla foundation — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the plugin skeleton and the `core` every later part of flotilla stands on — platform capabilities, state
paths, append-only log storage, repository identity, project config and activation, the session census and caller
identity — plus `flotilla doctor`, a `SessionStart` hook that stays silent in projects that never onboarded, and CI.

**Architecture:** a Claude Code plugin whose repository root is the plugin root. One Python entry point
(`scripts/flotilla`) dispatches to a stdlib-only package `flotilla/`. `core/` holds small single-purpose modules;
nothing in `core` imports anything outside the standard library and `core` itself. Every instrument that cannot
answer raises a named error instead of returning an empty or "green" value.

**Tech stack:** Python ≥ 3.11 standard library only (`tomllib`, `fcntl`, `json`, `subprocess`); pytest for tests;
GitHub Actions for CI; `claude plugin validate` for the manifest.

**Spec:** `docs/specs/2026-09-22-flotilla-design.md` — sections 1.2, 2, 3, 11, 12, 13, 15 (item 1), 17.
Decision trail: `docs/specs/2026-09-22-decisions-log.md`.

## Global constraints

- Python **≥ 3.11**, standard library only at runtime. pytest is a test-only dependency.
- Platforms: **Linux and macOS**. Native Windows refuses out loud (WSL counts as Linux).
- Code branches on **capabilities**, never on the OS name (`parent_pid_source == "procfs"`, not `os == "darwin"`).
- **English only** in every file, identifiers included. Tests that need Cyrillic write it as `\u` escapes.
- **Escapes survive only if checked.** Writing this plan, the file-writing tool turned `\u0436`-style escapes in
  the content into the characters themselves (seen 2026-09-22; `\n` and `\d` were kept). After writing any file
  that must hold `\u` escapes (Task 1), run `python3 tools/check_no_cyrillic.py` before the tests.
- An instrument that could not ask says **unknown** (raises a named error) — never "none", never "green".
- **Installed ≠ active:** hooks exit 0 with no output when no `.flotilla/project.toml` is found upward from `cwd`.
- Durable state lives in `${FLOTILLA_STATE_DIR}` or else `${XDG_STATE_HOME:-~/.local/state}/flotilla/`, **never** in
  `${CLAUDE_PLUGIN_DATA}` (deleted on uninstall).
- Census only through `claude agents --json`; never read `~/.claude/sessions/` or transcripts.
- Claude Code floor: **2.1.280** — the lowest version with a recorded census sample in `tests/fixtures/agents-json/`.
- Match a session to a process **by pid**; a session process's `comm` is its version number (`2.1.280`), not `claude`.
- Commits: Conventional Commits, English, one finished thing each, ending with the session's attribution lines.
  Nothing is pushed: the repository has no remote yet.
- Test command (from the repository root): `uv run --with pytest python -m pytest`. The floor interpreter:
  `uv run --python 3.11 --with pytest python -m pytest` (downloads 3.11 once).

## Review focus

The inputs the spec implies but no happy-path test meets, most likely to bite a person first. Each has its test in
the owning task.

1. **A malformed `.flotilla/project.toml`** (a typo in a hand edit) — the `SessionStart` hook must not crash the
   session or go silent; it reports the file and line. Tests: Task 5 (`test_malformed_toml_names_file_and_line`),
   Task 8 (`test_session_start_reports_broken_config`).
2. **`claude` missing from `PATH`, exiting non-zero, or printing non-JSON** — the census raises
   `CensusUnavailable`; `doctor` says the census is unknown, never "0 sessions". Tests: Task 6
   (`test_missing_cli_is_unavailable_not_empty` and neighbours), Task 7 (`test_census_failure_is_fail_not_zero`).
3. **Two processes appending to one log at the same moment** — every record survives whole, none interleaved.
   Test: Task 4 (`test_concurrent_appends_keep_every_line_whole`).
4. **A writer killed mid-line, then a new append** — the torn fragment is set aside, not glued to the next record,
   and history stays readable. Test: Task 4 (`test_append_after_torn_tail_sets_fragment_aside`).
5. **A session working inside a linked worktree** — it finds the same project and the same repository key as the
   main checkout, or the ledger splits in two. Tests: Task 4 (`test_linked_worktree_has_the_main_checkout_key`),
   Task 5 (`test_project_found_from_a_linked_worktree`).

---

## File map

```
flotilla/                         repository root = plugin root
├── .claude-plugin/plugin.json    manifest; defaultEnabled false                          (Task 8)
├── .github/workflows/ci.yml      matrix Ubuntu+macOS x 3.11/3.12/3.13; language; validate (Task 9)
├── hooks/hooks.json              SessionStart -> scripts/flotilla hook session-start      (Task 8)
├── scripts/flotilla              entry point; parses under any Python 3; version gate    (Task 2)
├── tools/check_no_cyrillic.py    the language gate                                        (Task 1)
├── pyproject.toml                project metadata and pytest config                       (Task 1)
├── README.md                     status, running tests, deleting state                    (Task 9)
├── flotilla/__init__.py          __version__                                              (Task 1)
├── flotilla/cli.py               argparse dispatcher, lazy imports                        (Task 2, 7, 8)
├── flotilla/doctor.py            findings about this machine and project                  (Task 7)
├── flotilla/hooks.py             hook entry; silent when inactive                         (Task 8)
├── flotilla/core/__init__.py                                                              (Task 1)
├── flotilla/core/platform.py     capabilities, parent pid                                 (Task 3)
├── flotilla/core/paths.py        state directory                                          (Task 4)
├── flotilla/core/storage.py      append-only JSONL logs under flock                       (Task 4)
├── flotilla/core/repo.py         repository identity: root, common dir, origin, key       (Task 4)
├── flotilla/core/config.py       project discovery, activation, loading                   (Task 5)
├── flotilla/core/census.py       claude agents --json                                     (Task 6)
├── flotilla/core/identity.py     which live session called us                             (Task 6)
└── tests/                        one test module per module above; fixtures/agents-json/
```

---

### Task 1: Repository skeleton and the language gate

**Files:**
- Create: `pyproject.toml`, `flotilla/__init__.py`, `flotilla/core/__init__.py`, `tools/check_no_cyrillic.py`
- Test: `tests/test_language.py`

**Interfaces:**
- Produces: `flotilla.__version__: str`; `tools/check_no_cyrillic.py` with
  `find_cyrillic(root: Path, paths: list[str]) -> list[str]` (entries `"<rel>:<line>: <text>"`) and
  `main() -> int` (0 clean, 1 hits).

- [ ] **Step 1: Set the repository identity and write the metadata**

```bash
git -C /home/max/workspace/flotilla config user.name tensusds
git -C /home/max/workspace/flotilla config user.email tensusds@gmail.com
```

`pyproject.toml`:

```toml
[project]
name = "flotilla"
version = "0.1.0"
description = "Coordinate independent peer Claude Code sessions: posts, an evidence-backed work ledger, a lane and guards."
requires-python = ">=3.11"
dependencies = []

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-q"
```

`flotilla/__init__.py`:

```python
"""flotilla: coordinate independent peer Claude Code sessions."""

__version__ = "0.1.0"
```

`flotilla/core/__init__.py`:

```python
"""Foundation modules. Nothing here imports outside the standard library and this package."""
```

- [ ] **Step 2: Write the failing tests**

`tests/test_language.py`:

```python
"""The repository is English-only (spec, section 12). Cyrillic in tests is written as escapes."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_no_cyrillic as gate  # noqa: E402


def test_clean_text_has_no_hits(tmp_path):
    (tmp_path / "a.md").write_text("plain English\n", encoding="utf-8")
    assert gate.find_cyrillic(tmp_path, ["a.md"]) == []


def test_cyrillic_is_reported_with_file_and_line(tmp_path):
    (tmp_path / "a.md").write_text("first\nsecond \u0436\u0443\u043a\n", encoding="utf-8")
    hits = gate.find_cyrillic(tmp_path, ["a.md"])
    assert len(hits) == 1
    assert hits[0].startswith("a.md:2: ")


def test_binary_and_missing_files_are_skipped(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe\x00\x01")
    assert gate.find_cyrillic(tmp_path, ["b.bin", "missing.txt"]) == []


def test_this_repository_is_clean():
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "check_no_cyrillic.py")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
```

- [ ] **Step 3: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_language.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'check_no_cyrillic'`.

- [ ] **Step 4: Write the gate**

`tools/check_no_cyrillic.py`:

```python
#!/usr/bin/env python3
"""Refuse Cyrillic characters in the files of this repository.

The repository is English-only (design spec, section 12). The check reads the files git knows
about, tracked and untracked-but-not-ignored, so a new file is caught before its first commit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

CYRILLIC = re.compile("[\u0400-\u04ff]")


def find_cyrillic(root: Path, paths: list[str]) -> list[str]:
    """Every line containing Cyrillic, as `<path>:<line>: <text>`. Unreadable files are skipped."""
    hits: list[str] = []
    for rel in paths:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if CYRILLIC.search(line):
                hits.append(f"{rel}:{number}: {line.strip()[:120]}")
    return hits


def repository_files(root: Path) -> list[str]:
    done = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True, check=True)
    return [path for path in done.stdout.split("\0") if path]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    hits = find_cyrillic(root, repository_files(root))
    for hit in hits:
        print(hit)
    if hits:
        print(f"{len(hits)} line(s) with Cyrillic; this repository is English-only.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run to see them pass, then see the gate go red**

Run: `uv run --with pytest python -m pytest tests/test_language.py`
Expected: `4 passed`.

Injection (removal): replace the body of `find_cyrillic`'s inner `if` with `pass`, run again.
Expected: `test_cyrillic_is_reported_with_file_and_line` FAILS. Undo the edit by hand (the file is not committed
yet, so `git checkout` has nothing to restore from) and re-run: `4 passed` again.

- [ ] **Step 6: Commit**

```bash
git -C /home/max/workspace/flotilla add pyproject.toml flotilla tools tests
git -C /home/max/workspace/flotilla commit -m "build: repository skeleton and the English-only gate"
```

---

### Task 2: Entry point and CLI dispatcher

**Files:**
- Create: `scripts/flotilla` (executable), `flotilla/cli.py`
- Test: `tests/test_entry.py`

**Interfaces:**
- Consumes: `flotilla.__version__`.
- Produces: `flotilla.cli.build_parser() -> argparse.ArgumentParser`; `flotilla.cli.main(argv: list[str]) -> int`.
  Subcommand `version`. Later tasks add `doctor` (Task 7) and `hook` (Task 8) to `build_parser` and `main`.
  Exit code 3 from `scripts/flotilla` means "interpreter too old".

- [ ] **Step 1: Write the failing tests**

`tests/test_entry.py`:

```python
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "scripts" / "flotilla"


def run(entry, *args, cwd=None):
    return subprocess.run([sys.executable, str(entry), *args], capture_output=True, text=True, cwd=cwd)


def test_entry_parses_with_an_old_grammar():
    # An old python3 must reach the version message, not a SyntaxError.
    ast.parse(ENTRY.read_text(encoding="utf-8"), feature_version=(3, 7))


def test_entry_is_executable():
    assert os.access(ENTRY, os.X_OK)


def test_version_prints_the_package_version():
    from flotilla import __version__
    done = run(ENTRY, "version")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == __version__


def test_entry_works_from_any_directory_and_through_a_symlink(tmp_path):
    link = tmp_path / "flotilla"
    link.symlink_to(ENTRY)
    done = run(link, "version", cwd=tmp_path)
    assert done.returncode == 0, done.stderr


def test_old_interpreter_gets_a_message_not_a_traceback(tmp_path):
    source = ENTRY.read_text(encoding="utf-8").replace("MINIMUM = (3, 11)", "MINIMUM = (99, 0)")
    assert "MINIMUM = (99, 0)" in source, "the gate constant moved; update this test"
    fake = tmp_path / "flotilla"
    fake.write_text(source, encoding="utf-8")
    done = run(fake, "version")
    assert done.returncode == 3
    assert "flotilla needs Python 99.0 or newer" in done.stderr
    assert "Traceback" not in done.stderr


def test_unknown_subcommand_is_a_usage_error():
    done = run(ENTRY, "no-such-command")
    assert done.returncode == 2
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_entry.py`
Expected: FAIL — `FileNotFoundError` / no such file `scripts/flotilla`.

- [ ] **Step 3: Write the entry point and the dispatcher**

`scripts/flotilla`:

```python
#!/usr/bin/env python3
# flotilla entry point. This file keeps to syntax any Python 3 can parse (no f-strings, no
# walrus, no match), so an interpreter older than the floor reaches the message below instead
# of dying on a SyntaxError that names nothing the user can act on.
import os
import sys

MINIMUM = (3, 11)


def main():
    if sys.version_info < MINIMUM:
        found = "%d.%d.%d" % tuple(sys.version_info[:3])
        sys.stderr.write(
            "flotilla needs Python %d.%d or newer; this python3 is %s (%s).\n"
            "Install one with `brew install python` (macOS) or `uv python install 3.11`, "
            "and make it the python3 on PATH.\n" % (MINIMUM[0], MINIMUM[1], found, sys.executable))
        return 3
    root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    sys.path.insert(0, root)
    from flotilla.cli import main as cli_main
    return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
```

```bash
chmod +x /home/max/workspace/flotilla/scripts/flotilla
```

`flotilla/cli.py`:

```python
"""Command-line dispatcher.

Each subcommand imports its module inside its branch, so a hook that exits early never pays
for modules it does not use.
"""

from __future__ import annotations

import argparse

from flotilla import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flotilla", description="Coordinate independent peer Claude Code sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the flotilla version")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    return 2
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run --with pytest python -m pytest tests/test_entry.py`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add scripts/flotilla flotilla/cli.py tests/test_entry.py
git -C /home/max/workspace/flotilla commit -m "feat(cli): entry point with an interpreter gate and a version command"
```

---

### Task 3: Platform capabilities and the parent pid

**Files:**
- Create: `flotilla/core/platform.py`
- Test: `tests/test_platform.py`

**Interfaces:**
- Produces:
  - `class UnsupportedPlatform(RuntimeError)`
  - `require_supported(os_name: str = sys.platform) -> None` — raises on anything but `linux` / `darwin`.
  - `@dataclass(frozen=True) class Capabilities: os_name: str; parent_pid_source: str; timeout_command: str | None;
    has_flock: bool; python_version: tuple[int, int, int]; cpu_count: int | None`
  - `probe(*, os_name: str = sys.platform, proc_root: Path = Path("/proc"), which=shutil.which) -> Capabilities`
  - `parent_pid(pid: int, source: str, *, proc_root: Path = Path("/proc"), run=subprocess.run) -> int | None`
    (`source` is `"procfs"` or `"ps"`; `None` when the process is gone)

- [ ] **Step 1: Write the failing tests**

`tests/test_platform.py`:

```python
import os
import subprocess
from pathlib import Path

import pytest

from flotilla.core import platform as plat


def test_windows_refuses_and_names_wsl():
    with pytest.raises(plat.UnsupportedPlatform, match="WSL"):
        plat.require_supported("win32")


def test_unknown_os_refuses():
    with pytest.raises(plat.UnsupportedPlatform):
        plat.require_supported("sunos5")


@pytest.mark.parametrize("name", ["linux", "darwin"])
def test_linux_and_macos_are_supported(name):
    plat.require_supported(name)


def test_probe_uses_procfs_when_present(tmp_path):
    (tmp_path / "self").mkdir()
    (tmp_path / "self" / "stat").write_text("1 (x) S 0\n")
    caps = plat.probe(os_name="linux", proc_root=tmp_path, which=lambda name: None)
    assert caps.parent_pid_source == "procfs"
    assert caps.timeout_command is None


def test_probe_falls_back_to_ps_without_procfs(tmp_path):
    caps = plat.probe(os_name="darwin", proc_root=tmp_path / "absent",
                      which=lambda name: "/opt/homebrew/bin/gtimeout" if name == "gtimeout" else None)
    assert caps.parent_pid_source == "ps"
    assert caps.timeout_command == "gtimeout"


def test_probe_prefers_timeout_over_gtimeout(tmp_path):
    caps = plat.probe(os_name="linux", proc_root=tmp_path, which=lambda name: "/usr/bin/" + name)
    assert caps.timeout_command == "timeout"


def test_procfs_parent_survives_parentheses_and_spaces_in_comm(tmp_path):
    (tmp_path / "123").mkdir()
    (tmp_path / "123" / "stat").write_text("123 (we (ird) name) S 45 1 1 0\n")
    assert plat.parent_pid(123, "procfs", proc_root=tmp_path) == 45


def test_procfs_parent_of_a_gone_process_is_none(tmp_path):
    assert plat.parent_pid(999999, "procfs", proc_root=tmp_path) is None


def test_ps_parent_is_parsed():
    fake = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="  4242\n", stderr="")
    assert plat.parent_pid(7, "ps", run=fake) == 4242


def test_ps_parent_of_a_gone_process_is_none():
    fake = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
    assert plat.parent_pid(7, "ps", run=fake) is None


def test_both_sources_agree_with_the_os_on_this_machine():
    # Runs the real ps on every platform, and the real procfs where it exists: the macOS
    # branch is exercised on Linux CI too.
    assert plat.parent_pid(os.getpid(), "ps") == os.getppid()
    if Path("/proc/self/stat").is_file():
        assert plat.parent_pid(os.getpid(), "procfs") == os.getppid()
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_platform.py`
Expected: FAIL — `ImportError: cannot import name 'platform' from 'flotilla.core'`.

- [ ] **Step 3: Write the module**

`flotilla/core/platform.py`:

```python
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
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run --with pytest python -m pytest tests/test_platform.py`
Expected: `12 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/core/platform.py tests/test_platform.py
git -C /home/max/workspace/flotilla commit -m "feat(core): platform capabilities and parent pid by capability"
```

---

### Task 4: State directory, log storage and repository identity

**Files:**
- Create: `flotilla/core/paths.py`, `flotilla/core/storage.py`, `flotilla/core/repo.py`
- Test: `tests/test_paths.py`, `tests/test_storage.py`, `tests/test_repo.py`

**Interfaces:**
- Produces:
  - `paths.state_dir(env: Mapping[str, str] = os.environ) -> Path`
  - `storage.StorageCorrupt(RuntimeError)`; `storage.ReadResult(records: list[dict], torn_tail: bool)`
  - `storage.LogTransaction` with `.read() -> ReadResult` and `.append(record: dict) -> None`
  - `storage.LocalLogStore(root: Path)` with `.transaction(key: str)` (context manager yielding `LogTransaction`,
    holding an exclusive lock), `.read(key) -> ReadResult` (lock-free), `.append(key, record) -> None`
  - `storage.LogStore` — the `Protocol` of the three methods above; later a shared ledger implements it.
  - `repo.normalize_origin(url: str) -> str` (e.g. `"github.com/owner/name"`, or `"path:/abs/dir"` for a local path)
  - `repo.RepoIdentity(root: Path, common_dir: Path, origin: str | None, key: str)`
  - `repo.identify(cwd: Path, run=subprocess.run) -> RepoIdentity`; raises `repo.NotARepository`
  - `repo.repo_key(normalized: str) -> str` (`<slug>-<12 hex>`)

- [ ] **Step 1: Write the failing tests**

`tests/test_paths.py`:

```python
from pathlib import Path

from flotilla.core.paths import state_dir


def test_override_wins():
    assert state_dir({"FLOTILLA_STATE_DIR": "/x/y", "XDG_STATE_HOME": "/s"}) == Path("/x/y")


def test_xdg_state_home():
    assert state_dir({"XDG_STATE_HOME": "/s"}) == Path("/s/flotilla")


def test_default_is_local_state_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state_dir({}) == tmp_path / ".local" / "state" / "flotilla"


def test_never_the_plugin_data_directory():
    # That directory is deleted on uninstall (spec, section 3.1).
    assert state_dir({"CLAUDE_PLUGIN_DATA": "/p", "XDG_STATE_HOME": "/s"}) == Path("/s/flotilla")
```

`tests/test_storage.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

import pytest

from flotilla.core.storage import LocalLogStore, StorageCorrupt

ROOT = Path(__file__).resolve().parent.parent


def test_missing_log_reads_empty(tmp_path):
    result = LocalLogStore(tmp_path).read("app-0123456789ab")
    assert result.records == [] and result.torn_tail is False


def test_append_then_read_in_order(tmp_path):
    store = LocalLogStore(tmp_path)
    store.append("k", {"n": 1})
    store.append("k", {"n": 2, "text": "caf\u00e9"})
    assert [r["n"] for r in store.read("k").records] == [1, 2]
    assert store.read("k").records[1]["text"] == "caf\u00e9"


def test_torn_tail_is_left_out_and_reported(tmp_path):
    (tmp_path / "k.jsonl").write_text('{"n":1}\n{"n":2', encoding="utf-8")
    result = LocalLogStore(tmp_path).read("k")
    assert result.records == [{"n": 1}] and result.torn_tail is True


def test_complete_json_without_newline_is_still_torn(tmp_path):
    # A writer that died between the record and its newline never committed it.
    (tmp_path / "k.jsonl").write_text('{"n":1}\n{"n":2}', encoding="utf-8")
    result = LocalLogStore(tmp_path).read("k")
    assert result.records == [{"n": 1}] and result.torn_tail is True


def test_damaged_middle_line_is_corruption_not_a_skip(tmp_path):
    (tmp_path / "k.jsonl").write_text('{"n":1}\nnot json\n{"n":3}\n', encoding="utf-8")
    with pytest.raises(StorageCorrupt, match=r"k\.jsonl:2"):
        LocalLogStore(tmp_path).read("k")


def test_append_after_torn_tail_sets_fragment_aside(tmp_path):
    (tmp_path / "k.jsonl").write_text('{"n":1}\n{"n":2', encoding="utf-8")
    store = LocalLogStore(tmp_path)
    store.append("k", {"n": 3})
    assert [r["n"] for r in store.read("k").records] == [1, 3]
    assert store.read("k").torn_tail is False
    assert (tmp_path / "k.torn").read_text(encoding="utf-8") == '{"n":2\n'


def test_transaction_reads_and_appends_under_one_lock(tmp_path):
    store = LocalLogStore(tmp_path)
    with store.transaction("k") as tx:
        assert tx.read().records == []
        tx.append({"n": 1})
        assert tx.read().records == [{"n": 1}]


@pytest.mark.parametrize("key", ["", "../escape", "a/b", "UPPER", ".hidden"])
def test_unsafe_keys_are_refused(tmp_path, key):
    with pytest.raises(ValueError):
        LocalLogStore(tmp_path).append(key, {"n": 1})


WRITER = """
import sys
sys.path.insert(0, {root!r})
from flotilla.core.storage import LocalLogStore
store = LocalLogStore({dir!r})
for n in range({count}):
    store.append("k", {{"writer": {writer}, "n": n, "pad": "x" * 2000}})
"""


def test_concurrent_appends_keep_every_line_whole(tmp_path):
    writers, count = 4, 200
    procs = [subprocess.Popen([sys.executable, "-c", WRITER.format(
        root=str(ROOT), dir=str(tmp_path), count=count, writer=w)]) for w in range(writers)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    result = LocalLogStore(tmp_path).read("k")
    assert result.torn_tail is False
    assert len(result.records) == writers * count
    for w in range(writers):
        assert [r["n"] for r in result.records if r["writer"] == w] == list(range(count))
```

`tests/test_repo.py`:

```python
import subprocess
from pathlib import Path

import pytest

from flotilla.core import repo


@pytest.mark.parametrize("url", [
    "git@github.com:Owner/app.git",
    "ssh://git@github.com/Owner/app.git",
    "https://github.com/Owner/app",
    "https://user@GitHub.com/Owner/app.git/",
    "ssh://git@github.com:22/Owner/app.git",
])
def test_origin_forms_of_one_repository_normalize_alike(url):
    assert repo.normalize_origin(url) == "github.com/Owner/app"


def test_local_path_origin(tmp_path):
    assert repo.normalize_origin(str(tmp_path / "origin.git")) == f"path:{(tmp_path / 'origin.git').resolve()}"


def test_key_is_a_safe_slug_with_a_digest():
    key = repo.repo_key("github.com/Owner/app")
    assert key.startswith("github-com-owner-app-")
    assert len(key.rsplit("-", 1)[1]) == 12


def test_distinct_repositories_get_distinct_keys():
    assert repo.repo_key("github.com/a/app") != repo.repo_key("github.com/b/app")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def checkout(tmp_path):
    main = tmp_path / "app"
    main.mkdir()
    git("init", "-q", "-b", "main", cwd=main)
    git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i", cwd=main)
    git("remote", "add", "origin", "git@github.com:Owner/app.git", cwd=main)
    return main


def test_identify_reads_origin_and_root(checkout):
    ident = repo.identify(checkout / ".")
    assert ident.root == checkout.resolve()
    assert ident.origin == "git@github.com:Owner/app.git"
    assert ident.key == repo.repo_key("github.com/Owner/app")


def test_linked_worktree_has_the_main_checkout_key(checkout, tmp_path):
    tree = tmp_path / "app-main-2"
    git("worktree", "add", "-q", "-b", "feat/x", str(tree), cwd=checkout)
    assert repo.identify(tree).key == repo.identify(checkout).key
    assert repo.identify(tree).root == tree.resolve()


def test_repository_without_origin_is_keyed_by_its_common_dir(tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    git("init", "-q", "-b", "main", cwd=lone)
    ident = repo.identify(lone)
    assert ident.origin is None
    assert ident.key == repo.repo_key(f"local:{(lone / '.git').resolve()}")


def test_not_a_repository_is_named(tmp_path):
    with pytest.raises(repo.NotARepository):
        repo.identify(tmp_path)
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_paths.py tests/test_storage.py tests/test_repo.py`
Expected: FAIL — `ModuleNotFoundError` for `flotilla.core.paths`, `.storage`, `.repo`.

- [ ] **Step 3: Write the three modules**

`flotilla/core/paths.py`:

```python
"""Where durable state lives on this machine.

Never `${CLAUDE_PLUGIN_DATA}`: Claude Code deletes that directory when the plugin is uninstalled
(the CLI by default), and the fleet's history would go with it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def state_dir(env: Mapping[str, str] = os.environ) -> Path:
    override = env.get("FLOTILLA_STATE_DIR")
    if override:
        return Path(override)
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "flotilla"
```

`flotilla/core/storage.py`:

```python
"""Append-only move logs, one file per key, written under an exclusive file lock.

This is the only module that touches log files. A log is JSON Lines; a line counts only once its
newline is on disk. The reader distinguishes a torn tail (a writer died mid-record: the fragment
was never committed, so it is left out and reported) from a damaged middle line (history itself is
broken: that is an error, never a silent skip).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class StorageCorrupt(RuntimeError):
    """A line other than the last does not parse: history is damaged, not torn."""


@dataclass(frozen=True)
class ReadResult:
    records: list[dict]
    torn_tail: bool


def _read(path: Path) -> ReadResult:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ReadResult([], False)
    lines = text.split("\n")
    tail = lines.pop()  # "" when the file ends with a newline
    records = []
    for number, line in enumerate(lines, start=1):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as err:
            raise StorageCorrupt(f"{path}:{number}: {err.msg}") from err
    return ReadResult(records, tail != "")


class LogTransaction:
    """Reads and appends while the caller holds the key's lock."""

    def __init__(self, path: Path):
        self._path = path

    def read(self) -> ReadResult:
        return _read(self._path)

    def append(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self._set_torn_tail_aside()
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _set_torn_tail_aside(self) -> None:
        """Move an uncommitted fragment out of the log, so the next record is not glued to it."""
        try:
            data = self._path.read_bytes()
        except FileNotFoundError:
            return
        if not data or data.endswith(b"\n"):
            return
        cut = data.rfind(b"\n") + 1
        with open(self._path.with_suffix(".torn"), "ab") as aside:
            aside.write(data[cut:] + b"\n")
        with open(self._path, "r+b") as handle:
            handle.truncate(cut)


class LogStore(Protocol):
    def transaction(self, key: str) -> contextlib.AbstractContextManager[LogTransaction]: ...
    def read(self, key: str) -> ReadResult: ...
    def append(self, key: str, record: dict) -> None: ...


class LocalLogStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.match(key):
            raise ValueError(f"unsafe log key: {key!r}")
        return self.root / f"{key}.jsonl"

    @contextlib.contextmanager
    def transaction(self, key: str) -> Iterator[LogTransaction]:
        path = self._path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / f"{key}.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield LogTransaction(path)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self, key: str) -> ReadResult:
        """Lock-free: a reader may see a record being written as a torn tail."""
        return _read(self._path(key))

    def append(self, key: str, record: dict) -> None:
        with self.transaction(key) as tx:
            tx.append(record)
```

`flotilla/core/repo.py`:

```python
"""Which repository a directory belongs to, identical from the main checkout and every worktree.

The ledger is keyed by the repository, not by the directory: a session in a linked worktree and a
session in the main checkout must land in the same log, or the fleet's state splits in two.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

_URL = re.compile(r"^[a-z][a-z0-9+.-]*://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", re.IGNORECASE)
_SCP = re.compile(r"^(?:[^@/]+@)?([^/:]+):(?!//)(.+)$")


class NotARepository(RuntimeError):
    """The directory is not inside a git repository."""


@dataclass(frozen=True)
class RepoIdentity:
    root: Path
    common_dir: Path
    origin: str | None
    key: str


def normalize_origin(url: str) -> str:
    url = url.strip()
    match = _URL.match(url) or _SCP.match(url)
    if match and not url.startswith("/"):
        host, path = match.groups()
        path = path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"{host.lower()}/{path}"
    return f"path:{Path(url).expanduser().resolve()}"


def repo_key(normalized: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")[:60].strip("-")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def _git(cwd: Path, *args: str, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)


def identify(cwd: Path, run=subprocess.run) -> RepoIdentity:
    top = _git(cwd, "rev-parse", "--show-toplevel", run=run)
    if top.returncode != 0:
        raise NotARepository(f"{cwd} is not inside a git repository")
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir", run=run)
    origin_done = _git(cwd, "config", "--get", "remote.origin.url", run=run)
    origin = origin_done.stdout.strip() or None
    common_dir = Path(common.stdout.strip()).resolve()
    normalized = normalize_origin(origin) if origin else f"local:{common_dir}"
    return RepoIdentity(root=Path(top.stdout.strip()).resolve(), common_dir=common_dir,
                        origin=origin, key=repo_key(normalized))
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run --with pytest python -m pytest tests/test_paths.py tests/test_storage.py tests/test_repo.py`
Expected: `4 + 13 + 12 passed` (29).

Injection (plausible neighbour): in `LogTransaction.append`, delete the call `self._set_torn_tail_aside()`.
Expected: `test_append_after_torn_tail_sets_fragment_aside` FAILS with `StorageCorrupt`. Undo the edit.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/core/paths.py flotilla/core/storage.py flotilla/core/repo.py tests/test_paths.py tests/test_storage.py tests/test_repo.py
git -C /home/max/workspace/flotilla commit -m "feat(core): state directory, append-only log storage and repository identity"
```

---

### Task 5: Project config and activation

**Files:**
- Create: `flotilla/core/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `config.SUPPORTED_SCHEMA = 1`; `config.ConfigError(RuntimeError)`
  - `config.find_project(start: Path) -> Path | None` — the nearest directory at or above `start` that holds
    `.flotilla/project.toml`; does **not** parse the file (the hook's inactive path must stay cheap).
  - `config.Project(root: Path, path: Path, schema: int, data: dict)`
  - `config.load_project(root: Path) -> Project` — raises `ConfigError` naming the file (and line, for syntax).

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import subprocess
from pathlib import Path

import pytest

from flotilla.core import config


def write_project(root: Path, text: str) -> Path:
    (root / ".flotilla").mkdir(parents=True, exist_ok=True)
    path = root / ".flotilla" / "project.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_inactive_when_nothing_is_onboarded(tmp_path):
    assert config.find_project(tmp_path) is None


def test_found_from_a_nested_directory(tmp_path):
    write_project(tmp_path, "schema = 1\n")
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert config.find_project(nested) == tmp_path.resolve()


def test_directory_without_the_file_is_not_a_project(tmp_path):
    (tmp_path / ".flotilla").mkdir()
    assert config.find_project(tmp_path) is None


def test_load_valid_project(tmp_path):
    write_project(tmp_path, 'schema = 1\n[trunk]\nbranch = "main"\n')
    project = config.load_project(tmp_path)
    assert project.schema == 1 and project.data["trunk"]["branch"] == "main"


def test_malformed_toml_names_file_and_line(tmp_path):
    path = write_project(tmp_path, 'schema = 1\n[trunk\nbranch = "main"\n')
    with pytest.raises(config.ConfigError) as err:
        config.load_project(tmp_path)
    assert str(path) in str(err.value)
    assert "line 2" in str(err.value)


def test_missing_schema_is_named(tmp_path):
    write_project(tmp_path, '[trunk]\nbranch = "main"\n')
    with pytest.raises(config.ConfigError, match="missing `schema`"):
        config.load_project(tmp_path)


def test_newer_schema_asks_for_an_update(tmp_path):
    write_project(tmp_path, "schema = 2\n")
    with pytest.raises(config.ConfigError, match="update the plugin"):
        config.load_project(tmp_path)


@pytest.mark.parametrize("value", ["0", "true", '"1"'])
def test_invalid_schema_values(tmp_path, value):
    write_project(tmp_path, f"schema = {value}\n")
    with pytest.raises(config.ConfigError):
        config.load_project(tmp_path)


def test_project_found_from_a_linked_worktree(tmp_path):
    main = tmp_path / "app"
    main.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=main, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    write_project(main, "schema = 1\n")
    run("add", ".flotilla/project.toml")
    run("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "onboard")
    tree = tmp_path / "app-review-1"
    run("worktree", "add", "-q", "-b", "fleet/review-1", str(tree))
    assert config.find_project(tree) == tree.resolve()
    assert config.load_project(tree).schema == 1
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_config.py`
Expected: FAIL — `ImportError: cannot import name 'config'`.

- [ ] **Step 3: Write the module**

`flotilla/core/config.py`:

```python
"""Project configuration and the activation rule.

A project is active when `.flotilla/project.toml` exists at or above the working directory.
Finding it never parses it: the hook path that decides "not onboarded, stay silent" must cost
one stat per directory level and nothing more.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_SCHEMA = 1
PROJECT_DIR = ".flotilla"
PROJECT_FILE = "project.toml"


class ConfigError(RuntimeError):
    """The project configuration cannot be used; the message names the file."""


@dataclass(frozen=True)
class Project:
    root: Path
    path: Path
    schema: int
    data: dict


def find_project(start: Path) -> Path | None:
    current = Path(start).resolve()
    for directory in (current, *current.parents):
        if (directory / PROJECT_DIR / PROJECT_FILE).is_file():
            return directory
    return None


def load_project(root: Path) -> Project:
    path = Path(root) / PROJECT_DIR / PROJECT_FILE
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as err:
        raise ConfigError(f"{path}: {err}") from err
    except OSError as err:
        raise ConfigError(f"{path}: cannot be read: {err}") from err
    schema = data.get("schema")
    if schema is None:
        raise ConfigError(f"{path}: missing `schema`; expected `schema = {SUPPORTED_SCHEMA}`")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema < 1:
        raise ConfigError(f"{path}: `schema` must be a positive integer, found {schema!r}")
    if schema > SUPPORTED_SCHEMA:
        raise ConfigError(f"{path}: schema {schema} is newer than this flotilla understands "
                          f"({SUPPORTED_SCHEMA}); update the plugin")
    return Project(root=Path(root), path=path, schema=schema, data=data)
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run --with pytest python -m pytest tests/test_config.py`
Expected: `11 passed`. (`tomllib` reports `(at line 2, column 7)` for the malformed case; if a Python version
words it differently, the assert on `"line 2"` is the one to adjust, never removed.)

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/core/config.py tests/test_config.py
git -C /home/max/workspace/flotilla commit -m "feat(core): project discovery, activation and schema-checked loading"
```

---

### Task 6: Census and caller identity

**Files:**
- Create: `flotilla/core/census.py`, `flotilla/core/identity.py`, `tests/fixtures/agents-json/2.1.280.json`
- Test: `tests/test_census.py`, `tests/test_identity.py`

**Interfaces:**
- Consumes: `platform.parent_pid(pid, source)`, `platform.probe()` (Task 3).
- Produces:
  - `census.CensusUnavailable(RuntimeError)`
  - `census.Session(name: str, session_id: str, kind: str, pid: int | None, short_id: str | None,
    status: str | None, state: str | None, cwd: str, started_at_ms: int | None)`
  - `census.parse_census(text: str) -> list[Session]`
  - `census.read_census(run=subprocess.run, claude: str = "claude", timeout: float = 30) -> list[Session]`
  - `identity.find_calling_session(sessions: list[Session], *, parent_of: Callable[[int], int | None],
    start_pid: int | None = None, max_depth: int = 64) -> Session | None`

- [ ] **Step 1: Record the fixture**

The keys and value types are exactly those `claude agents --json` returned on Claude Code 2.1.280 on 2026-09-22
(`cwd`, `id`, `kind`, `name`, `pid`, `sessionId`, `startedAt`, `state`, `status`; `id`, `pid`, `state`, `status`
nullable; `kind` in `background` / `interactive`). Values are synthetic. Every nullable field appears in both
branches.

`tests/fixtures/agents-json/2.1.280.json`:

```json
[
  {"cwd": "/home/u/app", "id": null, "kind": "interactive", "name": "operator", "pid": 1001,
   "sessionId": "0f000000-0000-4000-8000-000000000001", "startedAt": 1785175245929, "state": null, "status": "busy"},
  {"cwd": "/home/u/app", "id": "1a2b3c4d", "kind": "background", "name": "review session 1", "pid": 2002,
   "sessionId": "1a2b3c4d-0000-4000-8000-000000000002", "startedAt": 1785175300000, "state": "working", "status": "busy"},
  {"cwd": "/home/u/app", "id": "5e6f7a8b", "kind": "background", "name": "main session 2", "pid": 3003,
   "sessionId": "5e6f7a8b-0000-4000-8000-000000000003", "startedAt": 1785175400000, "state": "blocked", "status": "idle"},
  {"cwd": "/home/u/app", "id": "9c0d1e2f", "kind": "background", "name": "minor session 4", "pid": null,
   "sessionId": "9c0d1e2f-0000-4000-8000-000000000004", "startedAt": 1785175500000, "state": "done", "status": null}
]
```

- [ ] **Step 2: Write the failing tests**

`tests/test_census.py`:

```python
import subprocess
from pathlib import Path

import pytest

from flotilla.core import census

FIXTURES = Path(__file__).parent / "fixtures" / "agents-json"


def sample(version="2.1.280"):
    return (FIXTURES / f"{version}.json").read_text(encoding="utf-8")


def test_every_recorded_row_parses():
    sessions = census.parse_census(sample())
    assert [s.name for s in sessions] == ["operator", "review session 1", "main session 2", "minor session 4"]


def test_nullable_fields_keep_both_branches():
    by_name = {s.name: s for s in census.parse_census(sample())}
    assert by_name["operator"].short_id is None and by_name["review session 1"].short_id == "1a2b3c4d"
    assert by_name["minor session 4"].pid is None and by_name["main session 2"].pid == 3003
    assert by_name["minor session 4"].status is None and by_name["main session 2"].status == "idle"
    assert by_name["operator"].state is None and by_name["main session 2"].state == "blocked"


def test_unknown_fields_are_ignored():
    text = '[{"sessionId": "s", "name": "n", "kind": "background", "cwd": "/", "future": 1}]'
    assert census.parse_census(text)[0].name == "n"


def test_boolean_pid_is_not_a_pid():
    text = '[{"sessionId": "s", "name": "n", "kind": "background", "cwd": "/", "pid": true}]'
    assert census.parse_census(text)[0].pid is None


@pytest.mark.parametrize("text", ["not json", "{}", "[1]", '[{"name": "no id"}]'])
def test_malformed_output_is_unavailable(text):
    with pytest.raises(census.CensusUnavailable):
        census.parse_census(text)


def fake_run(returncode=0, stdout="[]", stderr="", exc=None):
    def run(argv, **kwargs):
        if exc:
            raise exc
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)
    return run


def test_empty_census_is_an_answer():
    assert census.read_census(run=fake_run(stdout="[]")) == []


def test_missing_cli_is_unavailable_not_empty():
    with pytest.raises(census.CensusUnavailable, match="not on PATH"):
        census.read_census(run=fake_run(exc=FileNotFoundError("claude")))


def test_nonzero_exit_is_unavailable():
    with pytest.raises(census.CensusUnavailable, match="exited 1"):
        census.read_census(run=fake_run(returncode=1, stderr="boom"))


def test_timeout_is_unavailable():
    with pytest.raises(census.CensusUnavailable, match="did not answer"):
        census.read_census(run=fake_run(exc=subprocess.TimeoutExpired("claude", 30)))
```

`tests/test_identity.py`:

```python
import os

from flotilla.core import platform as plat
from flotilla.core.census import Session
from flotilla.core.identity import find_calling_session


def session(name, pid):
    return Session(name=name, session_id=name, kind="background", pid=pid, short_id=None,
                   status="busy", state="working", cwd="/", started_at_ms=None)


def chain(mapping):
    return lambda pid: mapping.get(pid)


def test_first_ancestor_in_the_census_wins():
    sessions = [session("outer", 10), session("inner", 20)]
    found = find_calling_session(sessions, start_pid=40, parent_of=chain({40: 30, 30: 20, 20: 10, 10: 1}))
    assert found.name == "inner"


def test_no_ancestor_in_the_census_is_none_not_an_error():
    found = find_calling_session([session("elsewhere", 99)], start_pid=40, parent_of=chain({40: 30, 30: 1}))
    assert found is None


def test_rows_without_pid_never_match():
    found = find_calling_session([session("gone", None)], start_pid=40, parent_of=chain({40: 1}))
    assert found is None


def test_a_cycle_in_the_process_table_stops():
    found = find_calling_session([], start_pid=40, parent_of=chain({40: 30, 30: 40}))
    assert found is None


def test_real_ancestor_walk_on_this_machine():
    # The pytest process's parent stands in for the Claude Code session process.
    caps = plat.probe()
    parent_of = lambda pid: plat.parent_pid(pid, caps.parent_pid_source)
    found = find_calling_session([session("host", os.getppid())], parent_of=parent_of)
    assert found is not None and found.name == "host"
```

- [ ] **Step 3: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_census.py tests/test_identity.py`
Expected: FAIL — `ImportError` for `flotilla.core.census`.

- [ ] **Step 4: Write the two modules**

`flotilla/core/census.py`:

```python
"""Live Claude Code sessions, asked of the supported interface `claude agents --json`.

Failure to ask raises CensusUnavailable and never returns an empty list: "nobody is alive" and
"could not ask" are different answers, and a caller that confuses them will adopt the work of a
session that is merely unreachable.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


class CensusUnavailable(RuntimeError):
    """The census could not be taken; the message says why."""


@dataclass(frozen=True)
class Session:
    name: str
    session_id: str
    kind: str
    pid: int | None
    short_id: str | None
    status: str | None
    state: str | None
    cwd: str
    started_at_ms: int | None


def _int_or_none(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_or_none(value) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_census(text: str) -> list[Session]:
    try:
        rows = json.loads(text)
    except ValueError as err:
        raise CensusUnavailable(f"`claude agents --json` did not return JSON: {err}") from err
    if not isinstance(rows, list):
        raise CensusUnavailable("`claude agents --json` did not return a list")
    sessions = []
    for row in rows:
        if not isinstance(row, dict) or not _str_or_none(row.get("sessionId")):
            raise CensusUnavailable(f"`claude agents --json` returned a row without sessionId: {row!r:.200}")
        sessions.append(Session(
            name=row.get("name") if isinstance(row.get("name"), str) else "",
            session_id=row["sessionId"],
            kind=row.get("kind") if isinstance(row.get("kind"), str) else "",
            pid=_int_or_none(row.get("pid")),
            short_id=_str_or_none(row.get("id")),
            status=_str_or_none(row.get("status")),
            state=_str_or_none(row.get("state")),
            cwd=row.get("cwd") if isinstance(row.get("cwd"), str) else "",
            started_at_ms=_int_or_none(row.get("startedAt")),
        ))
    return sessions


def read_census(run=subprocess.run, claude: str = "claude", timeout: float = 30) -> list[Session]:
    try:
        done = run([claude, "agents", "--json"], capture_output=True, text=True,
                   timeout=timeout, check=False)
    except FileNotFoundError as err:
        raise CensusUnavailable(f"`{claude}` is not on PATH") from err
    except subprocess.TimeoutExpired as err:
        raise CensusUnavailable(f"`{claude} agents --json` did not answer within {timeout:g}s") from err
    if done.returncode != 0:
        raise CensusUnavailable(
            f"`{claude} agents --json` exited {done.returncode}: {done.stderr.strip()[:200]}")
    return parse_census(done.stdout)
```

`flotilla/core/identity.py`:

```python
"""Which live session is calling flotilla.

Walk up from our own process until a pid the census lists. Match by pid only: a session
process's command name is its version number (`2.1.280`), not `claude` (probe, 2026-09-22).
"""

from __future__ import annotations

import os
from collections.abc import Callable

from flotilla.core.census import Session


def find_calling_session(sessions: list[Session], *, parent_of: Callable[[int], int | None],
                         start_pid: int | None = None, max_depth: int = 64) -> Session | None:
    by_pid = {s.pid: s for s in sessions if s.pid is not None}
    pid: int | None = os.getpid() if start_pid is None else start_pid
    seen: set[int] = set()
    for _ in range(max_depth):
        if pid is None or pid <= 1 or pid in seen:
            return None
        if pid in by_pid:
            return by_pid[pid]
        seen.add(pid)
        pid = parent_of(pid)
    return None
```

- [ ] **Step 5: Run to see them pass, and see the null-branch guard go red**

Run: `uv run --with pytest python -m pytest tests/test_census.py tests/test_identity.py`
Expected: `12 + 5 passed` (17).

Injection (plausible neighbour): in `parse_census`, change `pid=_int_or_none(row.get("pid"))` to
`pid=row.get("pid")`. Expected: `test_boolean_pid_is_not_a_pid` FAILS. Undo the edit.

- [ ] **Step 6: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/core/census.py flotilla/core/identity.py tests/fixtures tests/test_census.py tests/test_identity.py
git -C /home/max/workspace/flotilla commit -m "feat(core): census from claude agents --json and caller identity by pid"
```

---

### Task 7: `flotilla doctor`

**Files:**
- Create: `flotilla/doctor.py`
- Modify: `flotilla/cli.py` (add the `doctor` subcommand)
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `platform.require_supported`, `platform.probe`, `paths.state_dir`, `config.find_project`,
  `config.load_project`, `config.ConfigError`, `census.read_census`, `census.CensusUnavailable`.
- Produces:
  - `doctor.MIN_CLAUDE_CODE = (2, 1, 280)`
  - `doctor.Finding(status: str, check: str, detail: str, fix: str = "")`, `status` in `ok`, `info`, `warn`, `fail`
  - `doctor.parse_version(text: str) -> tuple[int, int, int] | None`
  - `doctor.collect(*, cwd: Path, env=os.environ, run=subprocess.run, which=shutil.which,
    read=census.read_census, os_name: str = sys.platform, python=sys.version_info[:3]) -> list[Finding]`
  - `doctor.render(findings: list[Finding], quiet: bool) -> list[str]`
  - `doctor.run_doctor(*, quiet: bool = False, cwd: Path | None = None, out=sys.stdout) -> int` (1 if any `fail`)

- [ ] **Step 1: Write the failing tests**

`tests/test_doctor.py`:

```python
import io
import subprocess

from flotilla import doctor
from flotilla.core.census import CensusUnavailable


def fake_run(version_line="2.1.280 (Claude Code)", returncode=0, missing=False):
    def run(argv, **kwargs):
        if missing:
            raise FileNotFoundError(argv[0])
        return subprocess.CompletedProcess(argv, returncode, stdout=version_line + "\n", stderr="")
    return run


def which_all(name):
    return "/usr/bin/" + name


def collect(tmp_path, **overrides):
    options = dict(cwd=tmp_path, env={"FLOTILLA_STATE_DIR": str(tmp_path / "state")},
                   run=fake_run(), which=which_all, read=lambda: [], os_name="linux", python=(3, 12, 1))
    options.update(overrides)
    return {f.check: f for f in doctor.collect(**options)}


def test_parse_version():
    assert doctor.parse_version("2.1.280 (Claude Code)") == (2, 1, 280)
    assert doctor.parse_version("unknown") is None


def test_healthy_machine_outside_a_project(tmp_path):
    found = collect(tmp_path)
    assert all(f.status in ("ok", "info") for f in found.values()), found
    assert found["project"].status == "info" and "not onboarded" in found["project"].detail


def test_old_python_fails_with_a_fix(tmp_path):
    found = collect(tmp_path, python=(3, 10, 12))
    assert found["python"].status == "fail" and "3.11" in found["python"].fix


def test_windows_fails(tmp_path):
    assert collect(tmp_path, os_name="win32")["platform"].status == "fail"


def test_claude_below_the_floor_fails(tmp_path):
    found = collect(tmp_path, run=fake_run("2.1.200 (Claude Code)"))
    assert found["claude"].status == "fail" and "2.1.280" in found["claude"].detail


def test_claude_missing_fails(tmp_path):
    assert collect(tmp_path, run=fake_run(missing=True))["claude"].status == "fail"


def test_unparseable_claude_version_is_a_warning_not_ok(tmp_path):
    assert collect(tmp_path, run=fake_run("weird"))["claude"].status == "warn"


def test_census_failure_is_fail_not_zero(tmp_path):
    def broken():
        raise CensusUnavailable("`claude` is not on PATH")
    found = collect(tmp_path, read=broken)
    assert found["census"].status == "fail"
    assert "0" not in found["census"].detail and "not on PATH" in found["census"].detail


def test_census_counts_live_sessions(tmp_path):
    assert "2 live" in collect(tmp_path, read=lambda: [object(), object()])["census"].detail


def test_broken_project_config_fails_with_the_file(tmp_path):
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = \n", encoding="utf-8")
    found = collect(tmp_path)
    assert found["project"].status == "fail" and "project.toml" in found["project"].detail


def test_state_dir_is_reported(tmp_path):
    found = collect(tmp_path)
    assert str(tmp_path / "state") in found["state"].detail


def test_quiet_render_keeps_only_what_needs_attention(tmp_path):
    findings = [doctor.Finding("ok", "a", "fine"), doctor.Finding("warn", "b", "hmm", "do x")]
    assert doctor.render(findings, quiet=True) == ["warn  b: hmm (fix: do x)"]
    assert len(doctor.render(findings, quiet=False)) == 2


def test_run_doctor_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "collect", lambda **kw: [doctor.Finding("fail", "x", "bad")])
    out = io.StringIO()
    assert doctor.run_doctor(cwd=tmp_path, out=out) == 1
    assert "fail  x: bad" in out.getvalue()
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_doctor.py`
Expected: FAIL — `ImportError: cannot import name 'doctor'`.

- [ ] **Step 3: Write the module and wire the subcommand**

`flotilla/doctor.py`:

```python
"""What stands between this machine and a working fleet, one finding per check.

A check that could not ask reports `fail` or `warn` with the reason; it never reports `ok` by
default. Everything external is injected so the checks are testable without the real machine.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from flotilla.core import census as census_mod
from flotilla.core import config, paths
from flotilla.core import platform as plat

MIN_CLAUDE_CODE = (2, 1, 280)
MIN_PYTHON = (3, 11)


@dataclass(frozen=True)
class Finding:
    status: str
    check: str
    detail: str
    fix: str = ""


def parse_version(text: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    return tuple(int(part) for part in match.groups()) if match else None


def _dotted(version) -> str:
    return ".".join(str(part) for part in version)


def collect(*, cwd: Path, env=os.environ, run=subprocess.run, which=shutil.which,
            read=census_mod.read_census, os_name: str = sys.platform,
            python=tuple(sys.version_info[:3])) -> list[Finding]:
    findings: list[Finding] = []

    if tuple(python[:2]) >= MIN_PYTHON:
        findings.append(Finding("ok", "python", _dotted(python)))
    else:
        findings.append(Finding("fail", "python", f"{_dotted(python)} is below {_dotted(MIN_PYTHON)}",
                                "install Python 3.11+ (`brew install python` or `uv python install 3.11`)"))

    try:
        plat.require_supported(os_name)
        findings.append(Finding("ok", "platform", os_name))
    except plat.UnsupportedPlatform as err:
        findings.append(Finding("fail", "platform", str(err)))

    findings.append(Finding("ok", "git", which("git")) if which("git")
                    else Finding("fail", "git", "git is not on PATH", "install git"))

    try:
        done = run(["claude", "--version"], capture_output=True, text=True, timeout=30, check=False)
        version = parse_version(done.stdout)
        if version is None:
            findings.append(Finding("warn", "claude", f"version unknown: {done.stdout.strip()[:80]!r}"))
        elif version < MIN_CLAUDE_CODE:
            findings.append(Finding("fail", "claude",
                                    f"{_dotted(version)} is below the floor {_dotted(MIN_CLAUDE_CODE)}",
                                    "update Claude Code"))
        else:
            findings.append(Finding("ok", "claude", _dotted(version)))
    except FileNotFoundError:
        findings.append(Finding("fail", "claude", "`claude` is not on PATH", "install Claude Code"))
    except subprocess.TimeoutExpired:
        findings.append(Finding("warn", "claude", "`claude --version` did not answer within 30s"))

    try:
        sessions = read()
        findings.append(Finding("ok", "census", f"{len(sessions)} live session(s)"))
    except census_mod.CensusUnavailable as err:
        findings.append(Finding("fail", "census", f"unknown: {err}"))

    state = paths.state_dir(env)
    try:
        state.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=state):
            pass
        findings.append(Finding("ok", "state", f"{state} (delete it to erase the fleet's history)"))
    except OSError as err:
        findings.append(Finding("fail", "state", f"{state} is not writable: {err}"))

    root = config.find_project(cwd)
    if root is None:
        findings.append(Finding("info", "project", f"not onboarded here ({cwd}); flotilla stays inactive"))
    else:
        try:
            project = config.load_project(root)
            findings.append(Finding("ok", "project", f"{project.path} (schema {project.schema})"))
        except config.ConfigError as err:
            findings.append(Finding("fail", "project", str(err), "fix the file, then run `flotilla doctor`"))

    return findings


def render(findings: list[Finding], quiet: bool) -> list[str]:
    lines = []
    for finding in findings:
        if quiet and finding.status in ("ok", "info"):
            continue
        line = f"{finding.status:<5} {finding.check}: {finding.detail}"
        if finding.fix:
            line += f" (fix: {finding.fix})"
        lines.append(line)
    return lines


def run_doctor(*, quiet: bool = False, cwd: Path | None = None, out=sys.stdout) -> int:
    findings = collect(cwd=cwd or Path.cwd())
    for line in render(findings, quiet):
        print(line, file=out)
    return 1 if any(f.status == "fail" for f in findings) else 0
```

In `flotilla/cli.py`, replace `build_parser` and `main` with:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flotilla", description="Coordinate independent peer Claude Code sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the flotilla version")
    doctor = sub.add_parser("doctor", help="check this machine and project")
    doctor.add_argument("--quiet", action="store_true", help="print only what needs attention")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        from flotilla.doctor import run_doctor
        return run_doctor(quiet=args.quiet)
    return 2
```

- [ ] **Step 4: Run to see them pass, then run the real thing**

Run: `uv run --with pytest python -m pytest tests/test_doctor.py`
Expected: `13 passed`.

Run: `/home/max/workspace/flotilla/scripts/flotilla doctor`
Expected on this machine: `ok` for python, platform, git, claude (`2.1.280` or newer), census (`N live session(s)`),
state; `info` for project (the repository itself is not onboarded). Exit code 0.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/doctor.py flotilla/cli.py tests/test_doctor.py
git -C /home/max/workspace/flotilla commit -m "feat(doctor): machine and project findings with a Claude Code floor"
```

---

### Task 8: Plugin manifest and the `SessionStart` hook

**Files:**
- Create: `.claude-plugin/plugin.json`, `hooks/hooks.json`, `flotilla/hooks.py`
- Modify: `flotilla/cli.py` (add the `hook` subcommand)
- Test: `tests/test_hooks.py`, `tests/test_manifest.py`

**Interfaces:**
- Consumes: `config.find_project`, `doctor.collect`, `doctor.render`.
- Produces: `hooks.run_hook(event: str, stdin, out=sys.stdout) -> int` (always 0; never blocks a session start).
  CLI: `flotilla hook session-start` reads the hook's JSON on stdin (field `cwd`).

- [ ] **Step 1: Write the failing tests**

`tests/test_manifest.py`:

```python
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_manifest_installs_disabled_under_the_immutable_slug():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "flotilla"
    assert manifest["defaultEnabled"] is False


def test_manifest_version_matches_the_package():
    from flotilla import __version__
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == __version__


def test_every_hook_command_points_at_the_executable_entry():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = [h["command"] for groups in hooks.values() for group in groups for h in group["hooks"]]
    assert commands, "no hook commands declared"
    for command in commands:
        assert command.startswith('"${CLAUDE_PLUGIN_ROOT}/scripts/flotilla" hook ')
    assert os.access(ROOT / "scripts" / "flotilla", os.X_OK)
```

`tests/test_hooks.py`:

```python
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "scripts" / "flotilla"


def hook(cwd, env_state):
    return subprocess.run([str(ENTRY), "hook", "session-start"], input=json.dumps({"cwd": str(cwd)}),
                          capture_output=True, text=True, env={"PATH": "/usr/bin:/bin",
                                                               "FLOTILLA_STATE_DIR": str(env_state)})


def test_inactive_project_is_silent(tmp_path):
    done = hook(tmp_path, tmp_path / "state")
    assert done.returncode == 0
    assert done.stdout == "" and done.stderr == ""


def test_inactive_path_imports_nothing_heavy(tmp_path):
    probe = (
        "import io, json, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from flotilla.hooks import run_hook\n"
        f"run_hook('session-start', io.StringIO(json.dumps({{'cwd': {str(tmp_path)!r}}})))\n"
        "heavy = [m for m in ('flotilla.doctor', 'flotilla.core.census', 'flotilla.core.storage') if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == ""


def test_garbage_on_stdin_is_treated_as_inactive_elsewhere(tmp_path):
    from flotilla.hooks import run_hook
    out = io.StringIO()
    assert run_hook("session-start", io.StringIO("not json"), out=out) == 0


def test_session_start_reports_broken_config(tmp_path):
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = \n", encoding="utf-8")
    done = hook(tmp_path, tmp_path / "state")
    assert done.returncode == 0
    assert "project.toml" in done.stdout and done.stdout.startswith("flotilla:")


def test_session_start_in_a_healthy_project_names_what_needs_attention_only(tmp_path, monkeypatch):
    from flotilla import doctor, hooks
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "collect", lambda **kw: [doctor.Finding("ok", "python", "3.12")])
    out = io.StringIO()
    assert hooks.run_hook("session-start", io.StringIO(json.dumps({"cwd": str(tmp_path)})), out=out) == 0
    assert out.getvalue() == ""


def test_an_internal_error_is_said_not_raised(tmp_path, monkeypatch):
    from flotilla import doctor, hooks
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    def boom(**kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(doctor, "collect", boom)
    out = io.StringIO()
    assert hooks.run_hook("session-start", io.StringIO(json.dumps({"cwd": str(tmp_path)})), out=out) == 0
    assert "could not check this project" in out.getvalue() and "disk on fire" in out.getvalue()
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_manifest.py tests/test_hooks.py`
Expected: FAIL — missing `.claude-plugin/plugin.json` and `flotilla.hooks`.

- [ ] **Step 3: Write the manifest, the hook declaration and the handler**

`.claude-plugin/plugin.json`:

```json
{
  "name": "flotilla",
  "version": "0.1.0",
  "description": "Coordinate independent peer Claude Code sessions: named posts, per-session worktrees, an evidence-backed work ledger, a machine lane and command guards.",
  "author": { "name": "TensusDS" },
  "keywords": ["multi-session", "worktree", "code-review", "orchestration", "ledger"],
  "defaultEnabled": false
}
```

`hooks/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"${CLAUDE_PLUGIN_ROOT}/scripts/flotilla\" hook session-start",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

`flotilla/hooks.py`:

```python
"""Entry point for Claude Code hooks.

Installed is not active: until the project holds `.flotilla/project.toml`, every hook exits 0 with
no output. That path imports only the standard library and `flotilla.core.config`, because a
project that never onboarded flotilla must not pay for it. A hook never blocks a session start;
what it cannot check it says, in text the session will read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run_hook(event: str, stdin, out=sys.stdout) -> int:
    try:
        payload = json.load(stdin)
    except ValueError:
        payload = {}
    cwd = Path(payload.get("cwd") or ".") if isinstance(payload, dict) else Path(".")

    from flotilla.core.config import find_project
    root = find_project(cwd)
    if root is None:
        return 0

    if event == "session-start":
        try:
            from flotilla import doctor
            lines = doctor.render(doctor.collect(cwd=root), quiet=True)
        except Exception as err:  # noqa: BLE001 - a hook must say what broke, never crash the session
            print(f"flotilla: could not check this project: {err}", file=out)
            return 0
        if lines:
            print("flotilla: " + "; ".join(lines), file=out)
    return 0
```

In `flotilla/cli.py`, add the `hook` parser after `doctor` in `build_parser`:

```python
    hook = sub.add_parser("hook", help="entry point for Claude Code hooks")
    hook.add_argument("event", choices=["session-start"])
```

and the branch in `main`, before `return 2`:

```python
    if args.command == "hook":
        import sys
        from flotilla.hooks import run_hook
        return run_hook(args.event, sys.stdin)
```

- [ ] **Step 4: Run to see them pass, then validate the plugin**

Run: `uv run --with pytest python -m pytest tests/test_manifest.py tests/test_hooks.py`
Expected: `3 + 6 passed` (9).

Run: `claude plugin validate /home/max/workspace/flotilla`
Expected: validation passes. If it reports that `hooks/hooks.json` is not discovered by default, add
`"hooks": "./hooks/hooks.json"` to `plugin.json`, re-run, and keep the manifest test green.

Injection (removal): in `run_hook`, delete the line `if root is None:` and its `return 0`.
Expected: `test_inactive_project_is_silent` FAILS (output appears outside a project). Undo the edit.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add .claude-plugin hooks flotilla/hooks.py flotilla/cli.py tests/test_manifest.py tests/test_hooks.py
git -C /home/max/workspace/flotilla commit -m "feat(plugin): manifest installed disabled and a SessionStart hook silent until onboarded"
```

---

### Task 9: CI and README

**Files:**
- Create: `.github/workflows/ci.yml`, `README.md`
- Test: the full suite locally, on the lowest and highest supported interpreters

**Interfaces:**
- Consumes: `tools/check_no_cyrillic.py`, the pytest suite, `claude plugin validate`.

- [ ] **Step 1: Write the workflow**

`.github/workflows/ci.yml`:

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: python -m pip install pytest
      - run: python -m pytest

  language:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python3 tools/check_no_cyrillic.py

  plugin:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
      - run: npm install -g @anthropic-ai/claude-code
      - run: claude plugin validate .
```

The workflow first runs when the repository gets a GitHub home (not part of this plan). Action major versions and
the npm package name are checked on that first run; a red `plugin` job there is fixed in the workflow, not skipped.

- [ ] **Step 2: Write the README**

`README.md`:

````markdown
# flotilla

Coordinate independent peer Claude Code sessions on one machine: named posts (orchestrator, sender, reviewer,
acceptance judge, main, minor), a worktree per session, a work ledger in which every move costs evidence, a lane
that books the machine for long runs, and guards on dangerous commands.

**Status: pre-alpha.** The foundation is in place; the ledger, spawning, the lane and the guards are being built.
Design: `docs/specs/2026-09-22-flotilla-design.md`.

## Requirements

Linux or macOS, Python 3.11+, git, Claude Code 2.1.280+.

## Check your machine

```bash
scripts/flotilla doctor
```

## Where flotilla keeps state

`${FLOTILLA_STATE_DIR}` if set, else `${XDG_STATE_HOME:-~/.local/state}/flotilla/`. It survives plugin updates and
uninstall on purpose. To erase the fleet's history, delete that directory; `flotilla doctor` prints its path.

## Development

```bash
uv run --with pytest python -m pytest                   # the suite
uv run --python 3.11 --with pytest python -m pytest     # on the lowest supported Python
python3 tools/check_no_cyrillic.py                      # the English-only gate
claude plugin validate .                                # the manifest
```
````

- [ ] **Step 3: Run everything locally**

Run: `uv run --with pytest python -m pytest`
Expected: all tests pass (`101 passed` if the counts per task above hold; the number printed is the one to report).

Run: `uv run --python 3.11 --with pytest python -m pytest`
Expected: the same count passes on 3.11.

Run: `python3 tools/check_no_cyrillic.py && claude plugin validate /home/max/workspace/flotilla`
Expected: exit 0, validation passes.

- [ ] **Step 4: Commit**

```bash
git -C /home/max/workspace/flotilla add .github README.md
git -C /home/max/workspace/flotilla commit -m "ci: test matrix on Linux and macOS, the language gate and plugin validation"
```

---

## Done when

- `scripts/flotilla doctor` on this machine prints `ok` for python, platform, git, claude, census and state.
- The full suite passes on Python 3.11 and on the newest local interpreter; the language gate and
  `claude plugin validate` pass.
- Each injection named in Tasks 1, 4, 6 and 8 was seen red and undone.
- Nothing was pushed; the repository still has no remote.
