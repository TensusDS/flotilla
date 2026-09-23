# flotilla onboarding — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `flotilla onboard` — measure the machine, detect what the repository already declares (tests, CI, releases,
signals for conditional questions), drive the questionnaire one round at a time for the `flotilla-onboard` skill, run
every chosen test tier once, and write `.flotilla/project.toml` (schema 1); plus `onboard check` for drift.

**Architecture:** deterministic work lives in code under `flotilla/onboard/`; the skill only asks questions. The CLI
decides which questions apply next (`onboard next`), validates and stores each answer (`onboard answer`), and builds
and writes the profile (`onboard write`). Every detector reads files or asks a supported surface (`git`, `gh`) and says
"unknown" rather than guessing. Machine facts go to the state directory, project facts to the repository.

**Tech stack:** Python ≥ 3.11 standard library only (`tomllib` to read, an own minimal writer to write), pytest, `git`,
`gh` (optional at runtime).

**Spec:** `docs/specs/2026-09-22-flotilla-design.md` — sections 1.2, 4 (all), 5, 15 (item 2). Foundation interfaces
used here: `flotilla.core.{platform, paths, repo, config}` and `flotilla.cli`.

## Global constraints

- Python **≥ 3.11**, standard library only at runtime.
- **English only** in every file. No `\u`+4-hex escapes are needed in this plan; if one is ever written, it is produced
  by code (`chr(92) + "u..."`), because the text of a tool call decodes such escapes (seen 2026-09-22).
- The questionnaire asks only what is a human's **choice**; everything found in files is shown as detected
  (spec 4.1). Nothing is decided silently.
- AskUserQuestion limits: **at most 4 questions per call, 2 to 4 options per question, header at most 12 characters.**
- An instrument that could not ask says **unknown**, never an empty list read as "none".
- **Installed ≠ active:** nothing in this plan writes to a project unless the human runs `onboard write`.
- Existing `.flotilla/project.toml` is **never overwritten without `--force`**.
- Durable machine state goes under `flotilla.core.paths.state_dir()`; never `${CLAUDE_PLUGIN_DATA}`.
- Test command (repository root): `uv run --with pytest python -m pytest`; floor: `uv run --python 3.11 --with pytest
  python -m pytest`. CI runs both platforms on every push.
- Commits: Conventional Commits, English, one finished thing each, ending with the session's attribution lines.

## Decisions this plan takes against the spec text (each reported to Max)

1. **Measured tier times are machine facts.** Spec 4.4 shows `measured_seconds` inside `project.toml`, but spec 4.5
   rule 3 says machine facts never go into the project: a suite that takes 212 s here takes 90 s on a colleague's
   laptop. Rule 3 wins. Times go to `<state>/measurements/<repo-key>.toml`; `project.toml` keeps the command and its
   purpose. Task 9 edits spec 4.2 and 4.4 to match.
2. **Required CI jobs are read from the last push run on trunk**, not parsed from workflow YAML. The standard library
   has no YAML parser, and a run's job names are exactly what a later check compares against: already
   matrix-expanded, and only jobs a push triggers. When no run exists yet, job ids come from a minimal reader of the
   workflow files and are marked unverified.
3. **The first run does not go through the lane yet** — the lane is sub-project 5. Tiers run directly with a timeout
   that kills the whole process group.
4. **Onboarding records guard and post choices; it installs nothing.** Copying post templates belongs to sub-project 4,
   installing git hooks to sub-project 6. Spec 4.2 steps 4–5 are covered as questions and profile keys only.

## Review focus

1. **npm's placeholder test script** (`echo "Error: no test specified" && exit 1`, written by `npm init`) must not
   be proposed as a tier — it always fails. Test: Task 4 `test_npm_placeholder_is_not_a_tier`.
2. **`gh` missing, unauthenticated, or no push run yet** — required jobs come from the workflow files and say
   "unverified", never an empty list that reads as "no required jobs". Tests: Task 5
   `test_gh_missing_falls_back_to_files_marked_unverified`, `test_no_push_run_yet_falls_back_to_files`.
3. **An existing `project.toml`** — `write` refuses without `--force` and names the file. Tests: Task 8
   `test_existing_profile_is_refused`, Task 11 `test_second_write_without_force_is_refused`.
4. **A tier whose child process keeps the pipe open after a timeout** (`echo started; sleep 30`) — the run ends near
   the timeout, not when the grandchild exits. Test: Task 9 `test_timeout_kills_the_whole_process_group`.
5. **A repository with no remote** — the trunk question is not asked, the flow is local, CI is "none", and
   `shipped` is absent from the profile. Tests: Task 6 `test_repository_without_remote`, Task 7
   `test_no_remote_skips_the_flow_question`, Task 8 `test_local_flow_has_no_pr_section`.

---

## File map

```
flotilla/onboard/__init__.py        package marker                                         (Task 1)
flotilla/onboard/tomlw.py           minimal TOML writer                                    (Task 1)
flotilla/onboard/machine.py         machine profile: measure, write, read                  (Task 2)
flotilla/onboard/detect_repo.py     trunk, remote, releases, commit convention             (Task 3)
flotilla/onboard/files.py           read a text file or "" (shared by detectors)            (Task 4)
flotilla/onboard/detect_tests.py    test tiers the repository declares                     (Task 4)
flotilla/onboard/detect_ci.py       workflows, fingerprint, required jobs, merge methods    (Task 5)
flotilla/onboard/detect_signals.py  signals for conditional questions                      (Task 6)
flotilla/onboard/detect.py          one detection dict for the whole repository            (Task 6)
flotilla/onboard/questions.py       which question comes next; answer validation           (Task 7)
flotilla/onboard/profile.py         detection + answers -> project.toml                    (Task 8)
flotilla/onboard/firstrun.py        run a tier once; measurements in machine state          (Task 9)
flotilla/onboard/check.py           drift between the profile and the repository           (Task 10)
flotilla/onboard/answers.py         answers stored per repository in machine state         (Task 11)
flotilla/onboard/commands.py        `flotilla onboard ...` behaviour                        (Task 11)
flotilla/cli.py                     `onboard` subcommands                                   (Task 11)
skills/flotilla-onboard/SKILL.md    the questionnaire, driven by the CLI                    (Task 11)
tests/test_onboard_*.py             one test module per module above
```

---

### Task 1: Minimal TOML writer

**Files:**
- Create: `flotilla/onboard/__init__.py`, `flotilla/onboard/tomlw.py`
- Test: `tests/test_onboard_tomlw.py`

**Interfaces:**
- Produces: `tomlw.TomlWriteError(TypeError)`; `tomlw.render_toml(data: dict, header: str = "") -> str`.
  Subset: tables, arrays of tables, `str`, `int`, `float`, `bool`, flat arrays of those. `None`, NaN, infinities,
  nested arrays and mixed arrays are refused with the dotted key in the message. Scalars of a table are written
  before its sub-tables.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_tomlw.py`:

```python
import tomllib

import pytest

from flotilla.onboard.tomlw import TomlWriteError, render_toml

PROFILE = {
    "schema": 1,
    "trunk": {"branch": "main"},
    "repos": [{"name": "app", "path": ".", "push_after": []}],
    "tests": {"tier": [
        {"name": "unit", "command": "uv run pytest -q", "required_for": ["handover", "push"]},
        {"name": "e2e", "command": "npm run e2e", "required_for": ["push"]},
    ]},
    "fleet": {"default": {"main": 1, "review": 1}},
    "guards": {"revert": True, "line_edit": False},
    "ratio": 0.5,
}


def test_profile_round_trips():
    assert tomllib.loads(render_toml(PROFILE)) == PROFILE


def test_strings_with_quotes_backslashes_controls_and_accents_round_trip():
    text = 'say "hi" \\ path\\to\nnext\tcol caf' + chr(233) + chr(1) + chr(127)
    assert tomllib.loads(render_toml({"s": text}))["s"] == text


def test_keys_that_are_not_bare_are_quoted():
    data = {"with space": 1, "dotted.key": {"x": 2}}
    assert tomllib.loads(render_toml(data)) == data


def test_booleans_are_written_as_booleans():
    out = render_toml({"flag": True, "n": 1})
    assert "flag = true" in out and "n = 1" in out


def test_header_becomes_comments():
    out = render_toml({"a": 1}, header="Written by flotilla.\nEdit freely.")
    assert out.startswith("# Written by flotilla.\n# Edit freely.\n")
    assert tomllib.loads(out) == {"a": 1}


def test_table_inside_an_array_item_round_trips():
    data = {"tests": {"tier": [{"name": "a", "env": {"CI": "1"}}, {"name": "b"}]}}
    assert tomllib.loads(render_toml(data)) == data


def test_empty_list_is_an_empty_array():
    assert tomllib.loads(render_toml({"x": []})) == {"x": []}


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), [1, {"a": 1}], [[1]], object()])
def test_unrepresentable_values_are_refused_by_name(bad):
    with pytest.raises(TomlWriteError, match="value"):
        render_toml({"value": bad})
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_tomlw.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'flotilla.onboard'`.

- [ ] **Step 3: Write the writer**

`flotilla/onboard/__init__.py`:

```python
"""Onboarding: measure the machine, detect the repository, ask, and write the project profile."""
```

`flotilla/onboard/tomlw.py`:

```python
"""A TOML writer for flotilla's own files.

The standard library reads TOML (`tomllib`) but cannot write it. flotilla writes a small subset:
tables, arrays of tables, strings, integers, floats, booleans and flat arrays of those. This module
emits exactly that subset and refuses anything else by name instead of guessing a representation.
"""

from __future__ import annotations

import math
import re

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class TomlWriteError(TypeError):
    """A value has no representation in the subset flotilla writes."""


def _string(text: str) -> str:
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif code < 0x20 or code == 0x7F:
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _key(key) -> str:
    if not isinstance(key, str) or not key:
        raise TomlWriteError(f"keys must be non-empty strings, got {key!r}")
    return key if _BARE_KEY.match(key) else _string(key)


def _scalar(value, where: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise TomlWriteError(f"{where}: {value!r} is not written")
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if value is None:
        raise TomlWriteError(f"{where}: TOML has no null; leave the key out instead")
    raise TomlWriteError(f"{where}: cannot write a {type(value).__name__}")


def _is_table_array(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _value(value, where: str) -> str:
    if isinstance(value, list):
        if any(isinstance(v, (dict, list)) for v in value):
            raise TomlWriteError(f"{where}: arrays hold scalars only, or tables only")
        return "[" + ", ".join(_scalar(v, where) for v in value) + "]"
    return _scalar(value, where)


def _emit(lines: list[str], path: list[str], table: dict) -> None:
    for key, value in table.items():
        if not isinstance(value, dict) and not _is_table_array(value):
            lines.append(f"{_key(key)} = {_value(value, '.'.join(path + [str(key)]))}")
    for key, value in table.items():
        dotted = ".".join(_key(part) for part in path + [key])
        if isinstance(value, dict):
            lines.extend(["", f"[{dotted}]"])
            _emit(lines, path + [key], value)
        elif _is_table_array(value):
            for item in value:
                lines.extend(["", f"[[{dotted}]]"])
                _emit(lines, path + [key], item)


def render_toml(data: dict, header: str = "") -> str:
    lines = [f"# {line}".rstrip() for line in header.splitlines()]
    _emit(lines, [], data)
    return "\n".join(lines).strip("\n") + "\n"
```

After writing, confirm the escape survived: `grep -c 'u%04x' flotilla/onboard/tomlw.py` prints `1`.

- [ ] **Step 4: Run to see them pass; see the escaping go red**

Run: `uv run --with pytest python -m pytest tests/test_onboard_tomlw.py`
Expected: `13 passed`.

Injection (plausible neighbour): in `_string`, change `elif code < 0x20 or code == 0x7F:` to `elif code < 0x20:`.
Expected: `test_strings_with_quotes_backslashes_controls_and_accents_round_trip` FAILS (DEL is written raw and
`tomllib` rejects it). Undo; confirm with `git diff --stat` that only the test file and the new modules differ.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/__init__.py flotilla/onboard/tomlw.py tests/test_onboard_tomlw.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): minimal TOML writer for flotilla's own files"
```

---

### Task 2: Machine profile

**Files:**
- Create: `flotilla/onboard/machine.py`
- Test: `tests/test_onboard_machine.py`

**Interfaces:**
- Consumes: `platform.probe(os_name, proc_root, which)`, `tomlw.render_toml`.
- Produces: `machine.MIN_PYTHON = (3, 11)`; `machine.memory_bytes(*, proc_root, run) -> int | None`;
  `machine.path_python(*, run) -> dict` (`{"ok": bool, "path": str, "version": str}` or `{"ok": False, "error": str}`);
  `machine.gh_state(*, which, run) -> str` in `missing` / `installed` / `authenticated`;
  `machine.measure_machine(*, os_name, proc_root, which, run, now) -> dict`;
  `machine.write_machine(state: Path, data: dict) -> Path`; `machine.read_machine(state: Path) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_machine.py`:

```python
import datetime as dt
import subprocess

import pytest

from flotilla.onboard import machine


def fake(outputs):
    def run(argv, **kwargs):
        if argv[0] not in outputs:
            raise FileNotFoundError(argv[0])
        rc, out = outputs[argv[0]]
        return subprocess.CompletedProcess(argv, rc, stdout=out, stderr="")
    return run


def test_memory_from_meminfo(tmp_path):
    (tmp_path / "meminfo").write_text("MemTotal:       16384 kB\nMemFree: 1 kB\n")
    assert machine.memory_bytes(proc_root=tmp_path, run=fake({})) == 16384 * 1024


def test_memory_from_sysctl_without_procfs(tmp_path):
    assert machine.memory_bytes(proc_root=tmp_path, run=fake({"sysctl": (0, "34359738368\n")})) == 34359738368


def test_memory_unknown_is_none_not_zero(tmp_path):
    assert machine.memory_bytes(proc_root=tmp_path, run=fake({})) is None


def test_path_python_new_enough():
    got = machine.path_python(run=fake({"python3": (0, "/opt/homebrew/bin/python3\n3.12.4\n")}))
    assert got == {"ok": True, "path": "/opt/homebrew/bin/python3", "version": "3.12.4"}


def test_path_python_system_mac_is_too_old_and_paths_may_hold_spaces():
    got = machine.path_python(run=fake({"python3": (0, "/Applications/Xcode 26.app/usr/bin/python3\n3.9.6\n")}))
    assert got["ok"] is False and got["version"] == "3.9.6"
    assert got["path"] == "/Applications/Xcode 26.app/usr/bin/python3"


def test_path_python_missing_is_an_error_not_ok():
    got = machine.path_python(run=fake({}))
    assert got["ok"] is False and "could not be run" in got["error"]


@pytest.mark.parametrize("have_gh, rc, expected",
                         [(False, 0, "missing"), (True, 1, "installed"), (True, 0, "authenticated")])
def test_gh_state(have_gh, rc, expected):
    which = (lambda name: "/usr/bin/gh") if have_gh else (lambda name: None)
    assert machine.gh_state(which=which, run=fake({"gh": (rc, "")})) == expected


def test_measure_leaves_unknown_memory_out(tmp_path):
    data = machine.measure_machine(
        os_name="darwin", proc_root=tmp_path / "none", which=lambda name: None,
        run=fake({"python3": (0, "/usr/bin/python3\n3.9.6\n")}),
        now=dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc))
    assert "memory_bytes" not in data
    assert data["parent_pid_source"] == "ps" and data["timeout_command"] == "none" and data["gh"] == "missing"
    assert data["measured_at"] == "2026-09-23T00:00:00+00:00"
    assert data["python3"]["ok"] is False


def test_write_then_read(tmp_path):
    data = {"schema": 1, "os": "linux", "python3": {"ok": True, "path": "/usr/bin/python3", "version": "3.12.1"}}
    path = machine.write_machine(tmp_path / "state", data)
    assert path.read_text(encoding="utf-8").startswith("# Measured by")
    assert machine.read_machine(tmp_path / "state") == data


def test_read_missing_is_none(tmp_path):
    assert machine.read_machine(tmp_path) is None


def test_measure_this_machine_has_the_shape():
    data = machine.measure_machine()
    assert data["os"] in ("linux", "darwin")
    assert "version" in data["python3"] or "error" in data["python3"]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_machine.py`
Expected: FAIL — `ImportError: cannot import name 'machine'`.

- [ ] **Step 3: Write the module**

`flotilla/onboard/machine.py`:

```python
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
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run --with pytest python -m pytest tests/test_onboard_machine.py`
Expected: `13 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/machine.py tests/test_onboard_machine.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): machine profile measured into the state directory"
```

---

### Task 3: Repository facts — trunk, remote, releases, commit convention

**Files:**
- Create: `flotilla/onboard/detect_repo.py`
- Test: `tests/test_onboard_detect_repo.py`

**Interfaces:**
- Produces: `detect_repo.has_remote(root: Path, run=subprocess.run) -> bool`;
  `detect_repo.trunk_branch(root, run) -> str` (origin/HEAD → local `main` → local `master` → current branch → `"main"`);
  `detect_repo.release_info(root, run) -> dict` (`{"version_files": [...], "latest_tag": "v1.2.3"?}`);
  `detect_repo.commit_convention(root, run) -> str` in `conventional` / `free` / `unknown`.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_detect_repo.py`:

```python
import json
import subprocess

import pytest

from flotilla.onboard import detect_repo as dr


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def commit(cwd, message):
    git(cwd, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "feat: start")
    return root


def test_no_remote(repo):
    assert dr.has_remote(repo) is False


def test_remote(repo):
    git(repo, "remote", "add", "origin", "git@github.com:o/app.git")
    assert dr.has_remote(repo) is True


def test_trunk_from_origin_head(repo):
    git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
    assert dr.trunk_branch(repo) == "develop"


def test_trunk_prefers_main_over_the_current_feature_branch(repo):
    git(repo, "checkout", "-q", "-b", "feat/x")
    assert dr.trunk_branch(repo) == "main"


def test_trunk_falls_back_to_master(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    git(root, "init", "-q", "-b", "master")
    commit(root, "init")
    git(root, "checkout", "-q", "-b", "feat/y")
    assert dr.trunk_branch(root) == "master"


def test_release_info_reads_the_latest_tag_and_version_files(repo):
    git(repo, "tag", "v1.2.0")
    commit(repo, "fix: later")
    git(repo, "tag", "v1.10.0")
    (repo / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1.10.0"\n', encoding="utf-8")
    (repo / "package.json").write_text(json.dumps({"name": "app"}), encoding="utf-8")
    info = dr.release_info(repo)
    assert info["latest_tag"] == "v1.10.0"
    assert info["version_files"] == ["pyproject.toml"]


def test_release_info_ignores_malformed_files(repo):
    (repo / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    (repo / "package.json").write_text("{not json", encoding="utf-8")
    assert dr.release_info(repo) == {"version_files": []}


def test_commit_convention_conventional(repo):
    for n in range(4):
        commit(repo, f"fix: thing {n}")
    assert dr.commit_convention(repo) == "conventional"


def test_commit_convention_free(repo):
    for n in range(4):
        commit(repo, f"Update thing {n}")
    assert dr.commit_convention(repo) == "free"


def test_commit_convention_unknown_without_commits(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    assert dr.commit_convention(root) == "unknown"
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_detect_repo.py`
Expected: FAIL — `ImportError: cannot import name 'detect_repo'`.

- [ ] **Step 3: Write the module**

`flotilla/onboard/detect_repo.py`:

```python
"""What the repository says about itself: trunk, remote, releases and commit style."""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from pathlib import Path

CONVENTIONAL = re.compile(r"^(feat|fix|docs|chore|ci|test|refactor|build|perf|style|revert)(\([^)]*\))?!?: ")


def _git(root: Path, *args: str, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def has_remote(root: Path, run=subprocess.run) -> bool:
    return _git(root, "remote", "get-url", "origin", run=run).returncode == 0


def trunk_branch(root: Path, run=subprocess.run) -> str:
    head = _git(root, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD", run=run)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip().split("/", 1)[-1]
    for name in ("main", "master"):
        if _git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", run=run).returncode == 0:
            return name
    current = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", run=run)
    if current.returncode == 0 and current.stdout.strip():
        return current.stdout.strip()
    return "main"


def _toml_version(path: Path, table: str) -> bool:
    try:
        return bool(tomllib.loads(path.read_text(encoding="utf-8")).get(table, {}).get("version"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError):
        return False


def _json_version(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError, OSError):
        return False
    return isinstance(data, dict) and bool(data.get("version"))


def release_info(root: Path, run=subprocess.run) -> dict:
    files = []
    if (root / "pyproject.toml").is_file() and _toml_version(root / "pyproject.toml", "project"):
        files.append("pyproject.toml")
    if (root / "package.json").is_file() and _json_version(root / "package.json"):
        files.append("package.json")
    if (root / "Cargo.toml").is_file() and _toml_version(root / "Cargo.toml", "package"):
        files.append("Cargo.toml")
    info: dict = {"version_files": files}
    tags = _git(root, "tag", "--list", "v[0-9]*", "--sort=-v:refname", run=run)
    latest = tags.stdout.split()[0] if tags.returncode == 0 and tags.stdout.split() else None
    if latest:
        info["latest_tag"] = latest
    return info


def commit_convention(root: Path, run=subprocess.run) -> str:
    log = _git(root, "log", "-50", "--format=%s", run=run)
    subjects = [s for s in log.stdout.splitlines() if s.strip()] if log.returncode == 0 else []
    if not subjects:
        return "unknown"
    share = sum(1 for s in subjects if CONVENTIONAL.match(s)) / len(subjects)
    return "conventional" if share >= 0.6 else "free"
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run --with pytest python -m pytest tests/test_onboard_detect_repo.py`
Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/detect_repo.py tests/test_onboard_detect_repo.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): trunk, remote, release and commit-style detection"
```

---

### Task 4: Test tiers the repository declares

**Files:**
- Create: `flotilla/onboard/files.py`, `flotilla/onboard/detect_tests.py`
- Test: `tests/test_onboard_detect_tests.py`

**Interfaces:**
- Produces: `files.read_text(path: Path) -> str` (`""` when unreadable);
  `detect_tests.detect_tiers(root: Path) -> tuple[list[dict], list[str]]` — tiers
  `{"name", "command", "source"}` in the order python, node, rust, go, make; notes explain what was skipped and why.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_detect_tests.py`:

```python
import json

from flotilla.onboard.detect_tests import detect_tiers


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_empty_repository_has_no_tiers(tmp_path):
    assert detect_tiers(tmp_path) == ([], [])


def test_uv_project(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\n")
    write(tmp_path, "uv.lock", "")
    tiers, _ = detect_tiers(tmp_path)
    assert tiers == [{"name": "python", "command": "uv run pytest", "source": "pyproject.toml + uv.lock"}]


def test_poetry_project(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.poetry.group.dev.dependencies]\npytest = '*'\n")
    write(tmp_path, "poetry.lock", "")
    assert detect_tiers(tmp_path)[0][0]["command"] == "poetry run pytest"


def test_plain_pyproject_with_a_tests_directory(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    (tmp_path / "tests").mkdir()
    assert detect_tiers(tmp_path)[0][0]["command"] == "python3 -m pytest"


def test_pyproject_without_pytest_is_noted_not_proposed(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    tiers, notes = detect_tiers(tmp_path)
    assert tiers == [] and "no sign of pytest" in notes[0]


def test_npm_placeholder_is_not_a_tier(tmp_path):
    write(tmp_path, "package.json", json.dumps(
        {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
    tiers, notes = detect_tiers(tmp_path)
    assert tiers == [] and "placeholder" in notes[0]


def test_pnpm_project(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    write(tmp_path, "pnpm-lock.yaml", "")
    assert detect_tiers(tmp_path)[0] == [
        {"name": "node", "command": "pnpm test", "source": "package.json + pnpm-lock.yaml"}]


def test_invalid_package_json_is_noted(tmp_path):
    write(tmp_path, "package.json", "{not json")
    tiers, notes = detect_tiers(tmp_path)
    assert tiers == [] and "not valid JSON" in notes[0]


def test_rust_go_and_make_in_order(tmp_path):
    write(tmp_path, "Cargo.toml", "[package]\nname = 'x'\n")
    write(tmp_path, "go.mod", "module x\n")
    write(tmp_path, "Makefile", "build:\n\ttrue\ntest:\n\ttrue\n")
    assert [t["name"] for t in detect_tiers(tmp_path)[0]] == ["rust", "go", "make"]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_detect_tests.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'flotilla.onboard.detect_tests'`.

- [ ] **Step 3: Write the modules**

`flotilla/onboard/files.py`:

```python
"""Reading repository files for detection: an unreadable file reads as empty, never as an error."""

from __future__ import annotations

from pathlib import Path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
```

`flotilla/onboard/detect_tests.py`:

```python
"""Test commands the repository already declares.

Found here, confirmed by the human in the questionnaire, run once before they are written down
(spec, section 4). A declaration known to be a placeholder is noted and never proposed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from flotilla.onboard.files import read_text

NPM_PLACEHOLDER = "no test specified"
_NODE_LOCKS = (("pnpm-lock.yaml", "pnpm test"), ("yarn.lock", "yarn test"),
               ("bun.lock", "bun run test"), ("bun.lockb", "bun run test"))


def _python_tier(root: Path, notes: list[str]) -> dict | None:
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    uses_pytest = ("pytest" in read_text(pyproject) or (root / "tests").is_dir()
                   or (root / "pytest.ini").is_file())
    if not uses_pytest:
        notes.append("pyproject.toml found, but no sign of pytest; no Python tier proposed")
        return None
    if (root / "uv.lock").is_file():
        return {"name": "python", "command": "uv run pytest", "source": "pyproject.toml + uv.lock"}
    if (root / "poetry.lock").is_file():
        return {"name": "python", "command": "poetry run pytest", "source": "pyproject.toml + poetry.lock"}
    return {"name": "python", "command": "python3 -m pytest", "source": "pyproject.toml"}


def _node_tier(root: Path, notes: list[str]) -> dict | None:
    package = root / "package.json"
    if not package.is_file():
        return None
    try:
        data = json.loads(read_text(package))
    except ValueError:
        notes.append("package.json is not valid JSON; no Node tier proposed")
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    script = scripts.get("test") if isinstance(scripts, dict) else None
    if not isinstance(script, str) or not script.strip():
        return None
    if NPM_PLACEHOLDER in script:
        notes.append("package.json has npm's placeholder test script (it always fails); no Node tier proposed")
        return None
    for lock, command in _NODE_LOCKS:
        if (root / lock).is_file():
            return {"name": "node", "command": command, "source": f"package.json + {lock}"}
    return {"name": "node", "command": "npm test", "source": "package.json"}


def detect_tiers(root: Path) -> tuple[list[dict], list[str]]:
    tiers: list[dict] = []
    notes: list[str] = []
    for tier in (_python_tier(root, notes), _node_tier(root, notes)):
        if tier:
            tiers.append(tier)
    for name, marker, command in (("rust", "Cargo.toml", "cargo test"), ("go", "go.mod", "go test ./...")):
        if (root / marker).is_file():
            tiers.append({"name": name, "command": command, "source": marker})
    makefile = root / "Makefile"
    if makefile.is_file() and re.search(r"(?m)^test\s*:", read_text(makefile)):
        tiers.append({"name": "make", "command": "make test", "source": "Makefile"})
    return tiers, notes
```

- [ ] **Step 4: Run to see them pass; see the placeholder guard go red**

Run: `uv run --with pytest python -m pytest tests/test_onboard_detect_tests.py`
Expected: `9 passed`.

Injection (removal): delete the `if NPM_PLACEHOLDER in script:` block. Expected: `test_npm_placeholder_is_not_a_tier`
FAILS. Undo.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/files.py flotilla/onboard/detect_tests.py tests/test_onboard_detect_tests.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): detect declared test tiers, never npm's placeholder"
```

---

### Task 5: CI — workflows, fingerprint, required jobs, merge methods

**Files:**
- Create: `flotilla/onboard/detect_ci.py`
- Test: `tests/test_onboard_detect_ci.py`

**Interfaces:**
- Produces: `detect_ci.workflow_files(root) -> list[Path]`; `detect_ci.fingerprint(root, files) -> str | None`;
  `detect_ci.job_ids_from_file(text) -> list[str]`; `detect_ci.github_slug(normalized_origin) -> str | None`;
  `detect_ci.jobs_from_last_push_run(slug, trunk, run) -> list[str] | None`;
  `detect_ci.merge_methods(slug, run) -> list[str] | None`;
  `detect_ci.detect_ci(root, normalized_origin, trunk, run) -> dict` — `{"provider": "none"}`, or
  `{"provider": "github", "workflow_files", "fingerprint", "file_job_ids", "jobs", "jobs_source", "merge_methods"?}`
  where `jobs_source` is `"last-push-run"` or starts with `"workflow-files"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_detect_ci.py`:

```python
import json
import subprocess

from flotilla.onboard import detect_ci as ci

WORKFLOW = """name: ci
on:
  push:
    branches: [main]
jobs:
  # the suite
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
  lint:
    runs-on: ubuntu-latest
"""


def write_workflow(root, name="ci.yml", text=WORKFLOW):
    folder = root / ".github" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def gh(run_list="[]", jobs=None, repo=None, fail=False):
    def run(argv, **kwargs):
        if fail:
            raise FileNotFoundError("gh")
        if argv[:3] == ["gh", "run", "list"]:
            return subprocess.CompletedProcess(argv, 0, stdout=run_list, stderr="")
        if argv[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"jobs": jobs or []}), stderr="")
        if argv[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(repo or {}), stderr="")
        raise AssertionError(f"unexpected call {argv}")
    return run


def test_no_workflows_means_no_ci(tmp_path):
    assert ci.detect_ci(tmp_path, "github.com/o/app", "main", run=gh(fail=True)) == {"provider": "none"}


def test_job_ids_from_a_workflow_file():
    assert ci.job_ids_from_file(WORKFLOW) == ["test", "lint"]


def test_fingerprint_is_stable_and_follows_edits(tmp_path):
    write_workflow(tmp_path)
    first = ci.fingerprint(tmp_path, ci.workflow_files(tmp_path))
    assert first == ci.fingerprint(tmp_path, ci.workflow_files(tmp_path)) and first.startswith("sha256:")
    write_workflow(tmp_path, text=WORKFLOW + "  e2e:\n    runs-on: ubuntu-latest\n")
    assert ci.fingerprint(tmp_path, ci.workflow_files(tmp_path)) != first


def test_jobs_come_from_the_last_push_run(tmp_path):
    write_workflow(tmp_path)
    run = gh(run_list='[{"databaseId": 42}]',
             jobs=[{"name": "test (ubuntu-latest, 3.11)"}, {"name": "lint"}],
             repo={"mergeCommitAllowed": False, "squashMergeAllowed": True, "rebaseMergeAllowed": False})
    got = ci.detect_ci(tmp_path, "github.com/o/app", "main", run=run)
    assert got["jobs"] == ["test (ubuntu-latest, 3.11)", "lint"]
    assert got["jobs_source"] == "last-push-run"
    assert got["merge_methods"] == ["squash"]


def test_gh_missing_falls_back_to_files_marked_unverified(tmp_path):
    write_workflow(tmp_path)
    got = ci.detect_ci(tmp_path, "github.com/o/app", "main", run=gh(fail=True))
    assert got["jobs"] == ["test", "lint"]
    assert got["jobs_source"].startswith("workflow-files") and "unverified" in got["jobs_source"]
    assert "merge_methods" not in got


def test_no_push_run_yet_falls_back_to_files(tmp_path):
    write_workflow(tmp_path)
    got = ci.detect_ci(tmp_path, "github.com/o/app", "main", run=gh(run_list="[]"))
    assert got["jobs"] == ["test", "lint"] and "unverified" in got["jobs_source"]


def test_non_github_origin_never_calls_gh(tmp_path):
    write_workflow(tmp_path)
    def forbidden(argv, **kwargs):
        raise AssertionError("gh must not be called for a non-GitHub origin")
    got = ci.detect_ci(tmp_path, "gitlab.com/o/app", "main", run=forbidden)
    assert got["provider"] == "github" and "unverified" in got["jobs_source"]


def test_github_slug():
    assert ci.github_slug("github.com/Owner/app") == "Owner/app"
    assert ci.github_slug("gitlab.com/o/app") is None and ci.github_slug(None) is None


def test_merge_methods_unknown_when_gh_fails():
    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="HTTP 404")
    assert ci.merge_methods("o/app", run=failing) is None
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_detect_ci.py`
Expected: FAIL — `ImportError: cannot import name 'detect_ci'`.

- [ ] **Step 3: Write the module**

`flotilla/onboard/detect_ci.py`:

```python
"""The project's CI, read from what actually ran rather than from what a file promises.

Required jobs come from the latest completed push run on trunk: those names are already
matrix-expanded and include only jobs a push triggers, which is exactly what a later check compares
against. The workflow files are read for their fingerprint and as a fallback when no run exists yet
— marked unverified, because a job id in a file is not the name a run reports.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

_TOP_KEY = re.compile(r"^(\S[^:]*):")
_JOB_KEY = re.compile(r"^  ([A-Za-z0-9_-]+):\s*(#.*)?$")
UNVERIFIED = "workflow-files (unverified until a push run on trunk exists)"


def workflow_files(root: Path) -> list[Path]:
    folder = root / ".github" / "workflows"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml"))


def fingerprint(root: Path, files: list[Path]) -> str | None:
    if not files:
        return None
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def job_ids_from_file(text: str) -> list[str]:
    """Job ids under the top-level `jobs:` key, assuming the usual two-space indentation."""
    ids: list[str] = []
    inside = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = _TOP_KEY.match(line)
        if top:
            inside = top.group(1).strip().strip("'\"") == "jobs"
            continue
        job = _JOB_KEY.match(line) if inside else None
        if job:
            ids.append(job.group(1))
    return ids


def github_slug(normalized_origin: str | None) -> str | None:
    if normalized_origin and normalized_origin.startswith("github.com/"):
        return normalized_origin[len("github.com/"):]
    return None


def _gh(argv: list[str], run) -> subprocess.CompletedProcess | None:
    try:
        return run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def jobs_from_last_push_run(slug: str, trunk: str, run=subprocess.run) -> list[str] | None:
    listed = _gh(["gh", "run", "list", "-R", slug, "--branch", trunk, "--event", "push",
                  "--status", "completed", "--limit", "1", "--json", "databaseId"], run)
    if listed is None or listed.returncode != 0:
        return None
    try:
        runs = json.loads(listed.stdout or "[]")
        if not runs:
            return None
        viewed = _gh(["gh", "run", "view", str(runs[0]["databaseId"]), "-R", slug, "--json", "jobs"], run)
        if viewed is None or viewed.returncode != 0:
            return None
        jobs = json.loads(viewed.stdout).get("jobs", [])
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return None
    names = [job["name"] for job in jobs if isinstance(job, dict) and job.get("name")]
    return names or None


def merge_methods(slug: str, run=subprocess.run) -> list[str] | None:
    done = _gh(["gh", "repo", "view", slug, "--json",
                "mergeCommitAllowed,squashMergeAllowed,rebaseMergeAllowed"], run)
    if done is None or done.returncode != 0:
        return None
    try:
        data = json.loads(done.stdout)
    except ValueError:
        return None
    pairs = (("mergeCommitAllowed", "merge"), ("squashMergeAllowed", "squash"), ("rebaseMergeAllowed", "rebase"))
    return [name for key, name in pairs if data.get(key)]


def detect_ci(root: Path, normalized_origin: str | None, trunk: str, run=subprocess.run) -> dict:
    files = workflow_files(root)
    if not files:
        return {"provider": "none"}
    file_ids: list[str] = []
    for path in files:
        try:
            file_ids.extend(job_ids_from_file(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            continue
    result = {
        "provider": "github",
        "workflow_files": [p.relative_to(root).as_posix() for p in files],
        "fingerprint": fingerprint(root, files),
        "file_job_ids": file_ids,
    }
    slug = github_slug(normalized_origin)
    jobs = jobs_from_last_push_run(slug, trunk, run=run) if slug else None
    if jobs:
        result.update(jobs=jobs, jobs_source="last-push-run")
    else:
        result.update(jobs=file_ids, jobs_source=UNVERIFIED)
    if slug:
        methods = merge_methods(slug, run=run)
        if methods is not None:
            result["merge_methods"] = methods
    return result
```

- [ ] **Step 4: Run to see them pass; see the fallback marker go red**

Run: `uv run --with pytest python -m pytest tests/test_onboard_detect_ci.py`
Expected: `9 passed`.

Injection (plausible neighbour): change `UNVERIFIED` to `"workflow-files"`. Expected:
`test_gh_missing_falls_back_to_files_marked_unverified`, `test_no_push_run_yet_falls_back_to_files` and
`test_non_github_origin_never_calls_gh` FAIL. Undo.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/detect_ci.py tests/test_onboard_detect_ci.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): CI jobs from the last push run, files as an unverified fallback"
```

---

### Task 6: Signals and the whole detection

**Files:**
- Create: `flotilla/onboard/detect_signals.py`, `flotilla/onboard/detect.py`
- Test: `tests/test_onboard_detect.py`

**Interfaces:**
- Consumes: `repo.identify(cwd, run)`, `repo.normalize_origin(url, base)`, Tasks 3–5.
- Produces: `detect_signals.detect_signals(root) -> dict` with keys `multi_repo`, `deployment`, `shared_files`,
  `sequential` (each a list of strings); `detect.detect(root: Path, run=subprocess.run) -> dict` with keys
  `root`, `repo_key`, `origin` (normalized, `""` when absent), `remote`, `trunk`, `tests`, `notes`, `ci`, `release`,
  `commit_convention`, `signals`.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_detect.py`:

```python
import json
import subprocess

from flotilla.onboard.detect import detect
from flotilla.onboard.detect_signals import detect_signals


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_sibling_repositories_from_uv_lock_and_package_json(tmp_path):
    (tmp_path / "uv.lock").write_text(
        'source = { editable = "../core" }\nsource = { path = "../shared" }\nsource = { editable = "." }\n',
        encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"ui": "file:../ui", "x": "^1"}}),
                                           encoding="utf-8")
    assert detect_signals(tmp_path)["multi_repo"] == ["../core", "../shared", "../ui"]


def test_deployment_signals(tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "app.service").write_text("[Unit]\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "next dev"}}), encoding="utf-8")
    assert detect_signals(tmp_path)["deployment"] == ["deploy", "app.service", "package.json scripts.dev"]


def test_shared_and_sequential_signals(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("", encoding="utf-8")
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    signals = detect_signals(tmp_path)
    assert signals["shared_files"] == ["CHANGELOG.md"] and signals["sequential"] == ["alembic/versions"]


def test_empty_directory_has_no_signals(tmp_path):
    assert detect_signals(tmp_path) == {"multi_repo": [], "deployment": [], "shared_files": [], "sequential": []}


def test_whole_detection_on_a_repository(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "remote", "add", "origin", "git@gitlab.com:o/app.git")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (root / "uv.lock").write_text("", encoding="utf-8")
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("on: push\njobs:\n  test:\n    runs-on: x\n", encoding="utf-8")
    got = detect(root)
    assert got["remote"] is True and got["origin"] == "gitlab.com/o/app" and got["trunk"] == "main"
    assert [t["name"] for t in got["tests"]] == ["python"]
    assert got["ci"]["jobs"] == ["test"] and "unverified" in got["ci"]["jobs_source"]
    assert set(got) == {"root", "repo_key", "origin", "remote", "trunk", "tests", "notes", "ci", "release",
                        "commit_convention", "signals"}


def test_repository_without_remote(tmp_path):
    root = tmp_path / "lone"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    got = detect(root)
    assert got["remote"] is False and got["origin"] == "" and got["ci"] == {"provider": "none"}
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_detect.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'flotilla.onboard.detect'`.

- [ ] **Step 3: Write the modules**

`flotilla/onboard/detect_signals.py`:

```python
"""Signals that make a conditional onboarding question worth asking (spec, section 4.3)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from flotilla.onboard.files import read_text

SHARED_CANDIDATES = ("CHANGELOG.md", "TODO.md", "HANDOFF.md")
SEQUENTIAL_CANDIDATES = ("migrations", "alembic/versions", "db/migrate", "docs/adr", "doc/adr")
DEPLOY_CANDIDATES = ("deploy", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")
_SIBLING = re.compile(r'(?:editable|path)\s*=\s*"(\.\./[^"]+)"')


def _package(root: Path) -> dict:
    try:
        data = json.loads(read_text(root / "package.json") or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def sibling_repos(root: Path) -> list[str]:
    found = set(_SIBLING.findall(read_text(root / "uv.lock")))
    package = _package(root)
    for section in ("dependencies", "devDependencies"):
        deps = package.get(section)
        if isinstance(deps, dict):
            found.update(spec[len("file:"):] for spec in deps.values()
                         if isinstance(spec, str) and spec.startswith("file:../"))
    return sorted(found)


def deployment(root: Path) -> list[str]:
    hits = [name for name in DEPLOY_CANDIDATES if (root / name).exists()]
    hits += sorted(path.name for path in root.glob("*.service"))
    scripts = _package(root).get("scripts")
    if isinstance(scripts, dict) and scripts.get("dev"):
        hits.append("package.json scripts.dev")
    return hits


def detect_signals(root: Path) -> dict:
    return {
        "multi_repo": sibling_repos(root),
        "deployment": deployment(root),
        "shared_files": [name for name in SHARED_CANDIDATES if (root / name).is_file()],
        "sequential": [name for name in SEQUENTIAL_CANDIDATES if (root / name).is_dir()],
    }
```

`flotilla/onboard/detect.py`:

```python
"""One detection of the whole repository: what the questionnaire shows as found."""

from __future__ import annotations

import subprocess
from pathlib import Path

from flotilla.core import repo
from flotilla.onboard.detect_ci import detect_ci
from flotilla.onboard.detect_repo import commit_convention, has_remote, release_info, trunk_branch
from flotilla.onboard.detect_signals import detect_signals
from flotilla.onboard.detect_tests import detect_tiers


def detect(root: Path, run=subprocess.run) -> dict:
    ident = repo.identify(root, run=run)
    top = ident.root
    normalized = repo.normalize_origin(ident.origin, base=ident.common_dir.parent) if ident.origin else ""
    trunk = trunk_branch(top, run=run)
    tiers, notes = detect_tiers(top)
    return {
        "root": str(top),
        "repo_key": ident.key,
        "origin": normalized,
        "remote": has_remote(top, run=run),
        "trunk": trunk,
        "tests": tiers,
        "notes": notes,
        "ci": detect_ci(top, normalized or None, trunk, run=run),
        "release": release_info(top, run=run),
        "commit_convention": commit_convention(top, run=run),
        "signals": detect_signals(top),
    }
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run --with pytest python -m pytest tests/test_onboard_detect.py`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/detect_signals.py flotilla/onboard/detect.py tests/test_onboard_detect.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): signals for conditional questions and one whole detection"
```

---

### Task 7: The questionnaire — what comes next, and whether an answer fits

**Files:**
- Create: `flotilla/onboard/questions.py`
- Test: `tests/test_onboard_questions.py`

**Interfaces:**
- Consumes: the detection dict of Task 6.
- Produces: `questions.MAX_PER_ROUND = 4`; `questions.AnswerError(ValueError)`;
  `questions.all_questions(det, answers) -> list[dict]` — every question that applies now, answered or not, in
  spec order; `questions.next_questions(det, answers, limit=4) -> list[dict]` — the unanswered ones;
  `questions.validate_answer(question, values: list[str]) -> str | list[str]`;
  `questions.suggest_composition(answers) -> dict[str, int]`.
  A question is `{"id", "header", "question", "multi_select", "free_text", "options": [{"value", "label",
  "description"}]}`. A single-select answer is a `str`; a multi-select answer is a `list[str]`. With `free_text`, a
  typed value outside the options is accepted as is.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_questions.py`:

```python
import pytest

from flotilla.onboard import questions as qs


def detection(**overrides):
    base = {
        "remote": True, "trunk": "main",
        "tests": [{"name": "python", "command": "uv run pytest", "source": "pyproject.toml"}],
        "ci": {"provider": "github", "jobs": ["test"], "jobs_source": "last-push-run", "merge_methods": ["squash"]},
        "release": {"version_files": []},
        "signals": {"multi_repo": [], "deployment": [], "shared_files": [], "sequential": []},
    }
    base.update(overrides)
    return base


def ids(question_list):
    return [q["id"] for q in question_list]


def answer_all(det):
    answers = {}
    for _ in range(20):
        batch = qs.next_questions(det, answers)
        if not batch:
            return answers
        for q in batch:
            first = q["options"][0]["value"]
            answers[q["id"]] = [first] if q["multi_select"] else first
    raise AssertionError("the questionnaire never ended")


def test_first_round_for_a_repository_with_a_remote():
    assert ids(qs.next_questions(detection(), {})) == ["flow", "review", "permissions", "ci"]


def test_no_remote_skips_the_flow_question():
    first = qs.next_questions(detection(remote=False, ci={"provider": "none"}), {})
    assert "flow" not in ids(first) and "merge_auth" not in ids(first)


def test_merge_auth_follows_a_sender_merged_flow():
    assert "merge_auth" in ids(qs.all_questions(detection(), {"flow": "pr-sender"}))
    assert "merge_auth" not in ids(qs.all_questions(detection(), {"flow": "pr-human"}))


def test_ci_where_follows_a_ci_answer():
    assert "ci_where" not in ids(qs.all_questions(detection(), {}))
    assert "ci_where" in ids(qs.all_questions(detection(), {"ci": "github"}))


def test_github_is_offered_only_when_detected():
    ci_question = next(q for q in qs.all_questions(detection(ci={"provider": "none"}), {}) if q["id"] == "ci")
    assert [o["value"] for o in ci_question["options"]] == ["command", "none"]


def test_conditional_questions_follow_their_signals():
    quiet = ids(qs.all_questions(detection(), {}))
    loud = ids(qs.all_questions(detection(signals={"multi_repo": ["../core"], "deployment": ["deploy"],
                                                   "shared_files": ["TODO.md"], "sequential": ["migrations"]},
                                          release={"version_files": ["pyproject.toml"]}), {}))
    for conditional in ("repos", "release", "deploy", "shared", "sequential"):
        assert conditional not in quiet and conditional in loud


def test_merge_method_only_when_several_are_allowed_and_a_pr_flow():
    many = detection(ci={"provider": "github", "jobs": [], "jobs_source": "x", "merge_methods": ["merge", "squash"]})
    assert "merge_method" in ids(qs.all_questions(many, {"flow": "pr-sender"}))
    assert "merge_method" not in ids(qs.all_questions(many, {"flow": "direct"}))
    assert "merge_method" not in ids(qs.all_questions(detection(), {"flow": "pr-sender"}))


def test_every_question_fits_ask_user_question_limits():
    rich = detection(tests=[{"name": f"t{n}", "command": f"run {n}", "source": "x"} for n in range(6)],
                     ci={"provider": "github", "jobs": [], "jobs_source": "x", "merge_methods": ["merge", "squash", "rebase"]},
                     release={"version_files": ["pyproject.toml"], "latest_tag": "v1.0.0"},
                     signals={"multi_repo": ["../core"], "deployment": ["deploy"],
                              "shared_files": ["TODO.md"], "sequential": ["migrations"]})
    everything = qs.all_questions(rich, {"flow": "pr-sender", "ci": "command"})
    for q in everything:
        assert len(q["header"]) <= 12, q["id"]
        assert 2 <= len(q["options"]) <= 4, q["id"]
    assert len(next(q for q in everything if q["id"] == "tiers")["options"]) == 4


def test_answers_are_validated():
    ci_question = next(q for q in qs.all_questions(detection(), {}) if q["id"] == "ci")
    assert qs.validate_answer(ci_question, ["github"]) == "github"
    with pytest.raises(qs.AnswerError):
        qs.validate_answer(ci_question, ["jenkins"])
    tiers = next(q for q in qs.all_questions(detection(), {}) if q["id"] == "tiers")
    assert qs.validate_answer(tiers, ["python", "make test-slow"]) == ["python", "make test-slow"]
    with pytest.raises(qs.AnswerError):
        qs.validate_answer(tiers, [])


def test_the_questionnaire_ends():
    answers = answer_all(detection())
    assert qs.next_questions(detection(), answers) == []
    assert {"flow", "review", "permissions", "ci", "tiers", "tracker", "guards", "model"} <= set(answers)


def test_composition_suggestion():
    assert qs.suggest_composition({"review": "every"}) == {"main": 1, "review": 1}
    assert qs.suggest_composition({"review": "none"}) == {"main": 1}
    assert qs.suggest_composition({"review": "every", "deploy": "web"}) == {"main": 1, "review": 1, "judge": 1}
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_questions.py`
Expected: FAIL — `ImportError: cannot import name 'questions'`.

- [ ] **Step 3: Write the module**

`flotilla/onboard/questions.py`:

```python
"""The onboarding questionnaire: which questions apply next, and whether an answer fits.

The skill asks; this module decides. `next_questions` returns at most four unanswered questions
that apply given what was detected and answered so far, in the order of spec section 4.3. Every
question fits AskUserQuestion: a header of at most 12 characters and 2 to 4 options. With
`free_text`, a typed value (AskUserQuestion's "Other") is accepted as the answer.
"""

from __future__ import annotations

MAX_PER_ROUND = 4


class AnswerError(ValueError):
    """An answer that does not fit its question."""


def _q(qid: str, header: str, question: str, options, *, multi: bool = False, free_text: bool = False) -> dict:
    return {"id": qid, "header": header, "question": question, "multi_select": multi, "free_text": free_text,
            "options": [{"value": v, "label": label, "description": d} for v, label, d in options]}


def all_questions(det: dict, answers: dict) -> list[dict]:
    remote = bool(det.get("remote"))
    ci = det.get("ci") or {}
    signals = det.get("signals") or {}
    release = det.get("release") or {}
    flow = answers.get("flow", None if remote else "local")
    out: list[dict] = []

    if remote:
        out.append(_q("flow", "Trunk path", "How does work reach trunk?", [
            ("pr-sender", "PR, sender merges (Recommended)",
             "The sender opens a pull request and merges it once the required checks are green."),
            ("pr-human", "PR, a human merges", "The sender opens the pull request; a person reviews and merges it."),
            ("direct", "Direct push",
             "For experienced users or simple projects: CI runs only after the code is already in trunk."),
            ("local", "Local only", "Work is merged locally and never pushed."),
        ]))
    if flow in ("pr-sender", "direct"):
        out.append(_q("merge_auth", "Merge auth", "Who authorizes a merge into trunk?", [
            ("human", "A human, per batch", "The sender shows the batch in one block and waits for a yes."),
            ("sender", "The sender", "The sender merges on its own once the push receipt is green."),
        ]))
    out.append(_q("review", "Review", "How much work goes through an independent reviewer?", [
        ("every", "Every branch", "Nothing is queued for merge until a reviewer accepts it over its exact revision."),
        ("main-only", "Main work only", "Large work is reviewed; small fixes from minor sessions go straight on."),
        ("none", "No review", "Branches go to the sender once handed over."),
    ]))
    out.append(_q("permissions", "Permissions", "How do background sessions get permission for their tools?", [
        ("ask", "They ask me", "Sessions stop and wait for you at every permission prompt."),
        ("rules", "Allow rules exist", "Your settings already allow the commands the fleet needs."),
        ("auto", "Auto mode", "Sessions run in auto mode and the classifier decides."),
    ]))
    ci_options = []
    if ci.get("provider") == "github":
        ci_options.append(("github", "GitHub Actions (detected)", "Required jobs are checked by name after a push."))
    ci_options += [
        ("command", "Own gate command", "A command that answers 0 green, 1 red, 2 pending for a revision."),
        ("none", "No CI", "Only the local push receipt stands behind shipped, and the batch says so."),
    ]
    out.append(_q("ci", "CI", "Which CI stands behind shipped?", ci_options))
    if answers.get("ci") in ("github", "command"):
        out.append(_q("ci_where", "CI runs on", "Where does that CI run?", [
            ("cloud", "Hosted runners", "CI runs elsewhere; it does not compete for this machine."),
            ("this-machine", "This machine", "Self-hosted here: the lane also waits for the CI queue."),
        ]))
    if answers.get("ci") == "command":
        out.append(_q("gate_command", "Gate cmd", "Which command reports CI for a revision? Type it as Other.", [
            ("ask-human", "Ask me each time", "No command; the sender asks you whether CI is green."),
            ("later", "Set it later", "Leave it empty for now; edit project.toml when ready."),
        ], free_text=True))
    detected = [(t["name"], t["name"], f"{t['command']}  (from {t['source']})") for t in det.get("tests") or []]
    tier_options = detected[:MAX_PER_ROUND] if detected else [
        ("none", "No tests", "Nothing runs before a handover or a push."),
        ("later", "Add them later", "Type a command as Other, or edit project.toml later."),
    ]
    if len(tier_options) == 1:
        tier_options.append(("none", "None of these", "Do not run this tier before shipping."))
    out.append(_q("tiers", "Test tiers", "Which test commands must be green before shipping? Type more as Other.",
                  tier_options, multi=True, free_text=True))
    out.append(_q("tracker", "Tasks", "Where are tasks tracked?", [
        ("nowhere", "Nowhere", "Closing a row needs no reference."),
        ("github-issues", "GitHub Issues", "Closing a row may name an issue like #42."),
        ("pattern", "Ticket ids", "Closing a row may name a ticket like ABC-123 (Jira, Linear)."),
        ("own-register", "Own register", "Type your id pattern as Other, a regular expression."),
    ], free_text=True))
    if signals.get("multi_repo"):
        out.append(_q("repos", "Push order", f"Sibling repositories found ({', '.join(signals['multi_repo'])}). "
                      "Which goes first?", [
            ("this-first", "This one first", "This repository is pushed before its siblings."),
            ("siblings-first", "Siblings first", "The siblings are pushed first; this one follows."),
        ]))
    if release.get("latest_tag") or release.get("version_files"):
        out.append(_q("release", "Versions", "Who moves the version and tags a release?", [
            ("sender-semver", "Sender, semver", "The sender bumps the version files and writes an annotated tag."),
            ("none", "Nobody", "flotilla leaves versions alone."),
        ]))
    if signals.get("deployment"):
        out.append(_q("deploy", "Deployment", "A deployment was found. What surface does a person use?", [
            ("web", "Web", "An acceptance judge walks the human path in a browser."),
            ("cli", "Command line", "An acceptance judge walks the human path in a terminal."),
            ("api", "API", "An acceptance judge walks the human path with HTTP calls."),
            ("none", "No judge", "No acceptance judge in the fleet."),
        ]))
    if signals.get("shared_files"):
        out.append(_q("shared", "Shared files", f"Files everyone appends to: {', '.join(signals['shared_files'])}. "
                      "Reserve rewrites?", [
            ("reserve", "Reserve rewrites", "A rewrite needs the file's reservation; appends always pass."),
            ("off", "No reservation", "Anyone may rewrite them."),
        ]))
    if signals.get("sequential"):
        out.append(_q("sequential", "Numbering", f"Numbered files live in {', '.join(signals['sequential'])}. "
                      "Claim numbers?", [
            ("claim", "Claim numbers", "Sessions claim the next number, so two branches never take the same one."),
            ("off", "No claims", "Numbers are chosen by hand."),
        ]))
    methods = ci.get("merge_methods") or []
    if flow in ("pr-sender", "pr-human") and len(methods) > 1:
        out.append(_q("merge_method", "Merge method", "Which merge method does the sender use?",
                      [(m, m.capitalize(), f"GitHub's {m} merge.") for m in methods[:MAX_PER_ROUND]]))
    out.append(_q("guards", "Guards", "Which command guards should be on? Each refuses one dangerous command.", [
        ("revert", "Revert guard", "Refuses a checkout, reset or clean that would destroy uncommitted work."),
        ("line_edit", "Line-number edit", "Refuses in-place edits addressed by line number, which go stale silently."),
        ("push_receipt", "Push receipt", "Refuses a push or merge without a green run over that exact revision."),
    ], multi=True))
    out.append(_q("model", "Models", "Which model runs each post?", [
        ("one", "One for all", "Every session uses the model you launch Claude Code with."),
        ("reviewer-strongest", "Strongest reviewer", "Reviewers run on the most capable model; others on yours."),
    ]))
    return out


def next_questions(det: dict, answers: dict, limit: int = MAX_PER_ROUND) -> list[dict]:
    return [q for q in all_questions(det, answers) if q["id"] not in answers][:limit]


def validate_answer(question: dict, values: list[str]):
    allowed = [o["value"] for o in question["options"]]
    values = [v.strip() for v in values if v and v.strip()]
    if not values:
        raise AnswerError(f"{question['id']}: an answer is required")
    for value in values:
        if value not in allowed and not question["free_text"]:
            raise AnswerError(f"{question['id']}: {value!r} is not one of {', '.join(allowed)}")
    if question["multi_select"]:
        return values
    if len(values) != 1:
        raise AnswerError(f"{question['id']}: one answer expected, got {len(values)}")
    return values[0]


def suggest_composition(answers: dict) -> dict[str, int]:
    composition = {"main": 1}
    if answers.get("review", "every") != "none":
        composition["review"] = 1
    if answers.get("deploy") in ("web", "cli", "api"):
        composition["judge"] = 1
    return composition
```

- [ ] **Step 4: Run to see them pass; see a limit guard go red**

Run: `uv run --with pytest python -m pytest tests/test_onboard_questions.py`
Expected: `11 passed`.

Injection (plausible neighbour): change `detected[:MAX_PER_ROUND]` to `detected`. Expected:
`test_every_question_fits_ask_user_question_limits` FAILS. Undo.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/questions.py tests/test_onboard_questions.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): questionnaire rounds within AskUserQuestion's limits"
```

---

### Task 8: The project profile

**Files:**
- Create: `flotilla/onboard/profile.py`
- Test: `tests/test_onboard_profile.py`

**Interfaces:**
- Consumes: `questions.suggest_composition`, `tomlw.render_toml`, `config.load_project`.
- Produces: `profile.ProfileExists(RuntimeError)`; `profile.build_profile(det, answers) -> dict` (schema 1);
  `profile.write_profile(root: Path, data: dict, *, force: bool = False) -> Path` — writes
  `.flotilla/project.toml`, then reads it back through `config.load_project`.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_profile.py`:

```python
import tomllib

import pytest

from flotilla.core import config
from flotilla.onboard.profile import ProfileExists, build_profile, write_profile


def detection(root, **overrides):
    base = {
        "root": str(root), "remote": True, "trunk": "main",
        "tests": [{"name": "python", "command": "uv run pytest", "source": "pyproject.toml"}],
        "ci": {"provider": "github", "jobs": ["test", "lint"], "jobs_source": "last-push-run",
               "fingerprint": "sha256:abc", "merge_methods": ["squash"]},
        "release": {"version_files": ["pyproject.toml"], "latest_tag": "v0.1.0"},
        "signals": {"multi_repo": [], "deployment": [], "shared_files": ["TODO.md"], "sequential": []},
    }
    base.update(overrides)
    return base


BASE_ANSWERS = {"flow": "pr-sender", "merge_auth": "human", "review": "every", "permissions": "ask",
                "ci": "github", "ci_where": "cloud", "tiers": ["python"], "tracker": "nowhere",
                "guards": ["revert", "push_receipt"], "model": "one"}


def test_profile_loads_through_the_config_door(tmp_path):
    write_profile(tmp_path, build_profile(detection(tmp_path), BASE_ANSWERS))
    assert config.load_project(tmp_path).schema == 1


def test_pr_flow_with_a_single_allowed_method(tmp_path):
    data = build_profile(detection(tmp_path), BASE_ANSWERS)
    assert data["flow"] == {"mode": "pr", "merge_authorized_by": "human"}
    assert data["pr"] == {"opened_by": "sender", "merged_by": "sender", "merge_method": "squash"}


def test_tiers_from_detection_and_typed_commands(tmp_path):
    data = build_profile(detection(tmp_path), {**BASE_ANSWERS, "tiers": ["python", "make e2e"]})
    assert data["tests"]["tier"] == [
        {"name": "python", "command": "uv run pytest", "required_for": ["handover", "push"]},
        {"name": "custom-1", "command": "make e2e", "required_for": ["handover", "push"]},
    ]


def test_github_ci_takes_the_detected_jobs(tmp_path):
    data = build_profile(detection(tmp_path), BASE_ANSWERS)
    assert data["ci"] == {"provider": "github", "runs_on": "cloud", "required_jobs": ["test", "lint"],
                          "jobs_source": "last-push-run", "workflow_fingerprint": "sha256:abc"}


def test_local_flow_has_no_pr_section(tmp_path):
    answers = {k: v for k, v in BASE_ANSWERS.items() if k not in ("flow", "merge_auth")}
    data = build_profile(detection(tmp_path, remote=False, ci={"provider": "none"}), {**answers, "ci": "none"})
    assert data["flow"] == {"mode": "local"} and "pr" not in data and data["ci"] == {"provider": "none"}


def test_guards_and_tracker(tmp_path):
    data = build_profile(detection(tmp_path), {**BASE_ANSWERS, "tracker": "github-issues"})
    assert data["guards"] == {"revert": True, "line_edit": False, "push_receipt": True}
    assert data["evidence"]["close"] == {"field": "ref", "pattern": "^#\\d+$", "required": False}


def test_typed_tracker_pattern_is_kept(tmp_path):
    data = build_profile(detection(tmp_path), {**BASE_ANSWERS, "tracker": "^CURVE-\\d+(\\.\\d+)*$"})
    assert data["evidence"]["close"]["pattern"] == "^CURVE-\\d+(\\.\\d+)*$"


def test_deployment_adds_the_judge_and_a_deploy_section(tmp_path):
    data = build_profile(detection(tmp_path), {**BASE_ANSWERS, "deploy": "web"})
    assert data["fleet"]["default"] == {"main": 1, "review": 1, "judge": 1}
    assert data["deploy"] == {"surface": "web", "revision_command": ""}
    assert data["judge"] == {"required": False}


def test_existing_profile_is_refused(tmp_path):
    write_profile(tmp_path, build_profile(detection(tmp_path), BASE_ANSWERS))
    with pytest.raises(ProfileExists, match="project.toml"):
        write_profile(tmp_path, build_profile(detection(tmp_path), BASE_ANSWERS))


def test_force_overwrites(tmp_path):
    write_profile(tmp_path, build_profile(detection(tmp_path), BASE_ANSWERS))
    write_profile(tmp_path, build_profile(detection(tmp_path), {**BASE_ANSWERS, "review": "none"}), force=True)
    written = tomllib.loads((tmp_path / ".flotilla" / "project.toml").read_text(encoding="utf-8"))
    assert written["review"] == {"depth": "none"}
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_profile.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'flotilla.onboard.profile'`.

- [ ] **Step 3: Write the module**

`flotilla/onboard/profile.py`:

```python
"""From detection and answers to `.flotilla/project.toml` (schema 1).

Only project facts go here. Measured tier times are machine facts and live in the state directory
(plan decision 1). A written file is read back through `config.load_project`, the same door every
other module uses, so a profile this module cannot load is never left behind silently.
"""

from __future__ import annotations

from pathlib import Path

from flotilla.core import config
from flotilla.onboard.questions import suggest_composition
from flotilla.onboard.tomlw import render_toml

HEADER = ("Written by `flotilla onboard write`. Edit by hand freely; `flotilla onboard check` reports drift.")
FLOW_MODES = {"pr-sender": "pr", "pr-human": "pr", "direct": "direct", "local": "local"}
KNOWN_TRACKERS = {"nowhere": None, "github-issues": "^#\\d+$", "pattern": "^[A-Z][A-Z0-9]+-\\d+$",
                  "own-register": ""}
GUARDS = ("revert", "line_edit", "push_receipt")


class ProfileExists(RuntimeError):
    """A project profile is already there; onboarding does not overwrite it without --force."""


def _tiers(det: dict, chosen: list[str]) -> list[dict]:
    detected = {t["name"]: t for t in det.get("tests") or []}
    tiers, custom = [], 0
    for value in chosen:
        if value in ("none", "later"):
            continue
        if value in detected:
            tiers.append({"name": value, "command": detected[value]["command"], "required_for": ["handover", "push"]})
        else:
            custom += 1
            tiers.append({"name": f"custom-{custom}", "command": value, "required_for": ["handover", "push"]})
    return tiers


def _ci(det: dict, answers: dict) -> dict:
    provider = answers.get("ci", "none")
    section: dict = {"provider": provider}
    if provider in ("github", "command"):
        section["runs_on"] = answers.get("ci_where", "cloud")
    if provider == "github":
        found = det.get("ci") or {}
        section["required_jobs"] = list(found.get("jobs") or [])
        section["jobs_source"] = found.get("jobs_source", "unknown")
        if found.get("fingerprint"):
            section["workflow_fingerprint"] = found["fingerprint"]
    if provider == "command":
        command = answers.get("gate_command", "later")
        section["gate_command"] = "" if command in ("ask-human", "later") else command
    return section


def build_profile(det: dict, answers: dict) -> dict:
    root = Path(det["root"])
    flow = answers.get("flow", "local")
    signals = det.get("signals") or {}
    data: dict = {"schema": 1, "trunk": {"branch": det.get("trunk", "main")},
                  "flow": {"mode": FLOW_MODES.get(flow, "local")}}
    if "merge_auth" in answers:
        data["flow"]["merge_authorized_by"] = answers["merge_auth"]
    if flow in ("pr-sender", "pr-human"):
        methods = (det.get("ci") or {}).get("merge_methods") or []
        pr = {"opened_by": "sender", "merged_by": "sender" if flow == "pr-sender" else "human"}
        method = answers.get("merge_method") or (methods[0] if len(methods) == 1 else None)
        if method:
            pr["merge_method"] = method
        data["pr"] = pr
    siblings = signals.get("multi_repo") or []
    data["repos"] = [{"name": root.name, "path": ".",
                      "push_after": list(siblings) if answers.get("repos") == "siblings-first" else []}]
    tiers = _tiers(det, answers.get("tiers") or [])
    if tiers:
        data["tests"] = {"tier": tiers}
    data["ci"] = _ci(det, answers)
    data["review"] = {"depth": answers.get("review", "every")}
    data["permissions"] = {"mode": answers.get("permissions", "ask")}
    if answers.get("release") == "sender-semver":
        data["release"] = {"version_files": list((det.get("release") or {}).get("version_files") or []),
                           "tag": "v{version}", "annotated": True}
    data["fleet"] = {"default": suggest_composition(answers), "model": answers.get("model", "one")}
    chosen_guards = answers.get("guards") or []
    data["guards"] = {guard: guard in chosen_guards for guard in GUARDS}
    tracker = answers.get("tracker", "nowhere")
    pattern = KNOWN_TRACKERS.get(tracker, tracker)
    if pattern is not None:
        close = {"field": "ref", "required": False}
        if pattern:
            close = {"field": "ref", "pattern": pattern, "required": False}
        data["evidence"] = {"close": close}
    if answers.get("deploy") in ("web", "cli", "api"):
        data["deploy"] = {"surface": answers["deploy"], "revision_command": ""}
        data["judge"] = {"required": False}
    if answers.get("shared") == "reserve":
        data["reservation"] = {"files": list(signals.get("shared_files") or [])}
    if answers.get("sequential") == "claim":
        data["numbering"] = {"directories": list(signals.get("sequential") or [])}
    return data


def write_profile(root: Path, data: dict, *, force: bool = False) -> Path:
    path = Path(root) / config.PROJECT_DIR / config.PROJECT_FILE
    if path.exists() and not force:
        raise ProfileExists(f"{path} already exists; run `flotilla onboard check`, or re-onboard with --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_toml(data, header=HEADER), encoding="utf-8")
    config.load_project(Path(root))
    return path
```

Check the dict ordering assert in `test_guards_and_tracker`: `{"field", "pattern", "required"}` compares equal
regardless of key order, so the construction order in `build_profile` does not matter.

- [ ] **Step 4: Run to see them pass; see the overwrite guard go red**

Run: `uv run --with pytest python -m pytest tests/test_onboard_profile.py`
Expected: `10 passed`.

Injection (removal): delete the whole `if path.exists() and not force:` block. Expected:
`test_existing_profile_is_refused` FAILS. Undo.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/profile.py tests/test_onboard_profile.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): build and write project.toml, never over an existing one"
```

---

### Task 9: First run of each tier; measurements in machine state

**Files:**
- Create: `flotilla/onboard/firstrun.py`
- Modify: `docs/specs/2026-09-22-flotilla-design.md` (sections 4.2 step 3 and the 4.4 example; plan decisions 1 and 3)
- Test: `tests/test_onboard_firstrun.py`

**Interfaces:**
- Consumes: `tomlw.render_toml`.
- Produces: `firstrun.TierRun(name, status, seconds, summary, tail)` with `status` in `green` / `red` / `killed` /
  `timed-out`; `firstrun.run_tier(name, command, cwd, *, timeout) -> TierRun`;
  `firstrun.save_measurements(state, repo_key, runs) -> Path` — only green runs are kept;
  `firstrun.load_measurements(state, repo_key) -> dict[str, float]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_firstrun.py`:

```python
import sys
import time

from flotilla.onboard import firstrun

PY = sys.executable


def test_green_run_has_seconds_and_a_summary(tmp_path):
    run = firstrun.run_tier("unit", f"{PY} -c \"print('3 passed in 0.1s')\"", tmp_path, timeout=30)
    assert run.status == "green" and run.seconds is not None and run.summary == "3 passed in 0.1s"


def test_red_run_keeps_the_tail_and_no_seconds(tmp_path):
    run = firstrun.run_tier("unit", f"{PY} -c \"print('boom'); raise SystemExit(1)\"", tmp_path, timeout=30)
    assert run.status == "red" and run.seconds is None and "boom" in run.tail


def test_timeout_kills_the_whole_process_group(tmp_path):
    started = time.monotonic()
    run = firstrun.run_tier("slow", "echo started; sleep 30; echo never", tmp_path, timeout=1)
    assert run.status == "timed-out" and "started" in run.tail
    assert time.monotonic() - started < 10


def test_a_signal_kill_is_killed_not_red(tmp_path):
    run = firstrun.run_tier("oom", "kill -9 $$", tmp_path, timeout=30)
    assert run.status == "killed" and run.seconds is None


def test_the_command_runs_in_the_given_directory(tmp_path):
    (tmp_path / "marker.txt").write_text("here", encoding="utf-8")
    run = firstrun.run_tier("cwd", "cat marker.txt", tmp_path, timeout=30)
    assert run.status == "green" and "here" in run.tail


def test_only_green_runs_are_measured(tmp_path):
    runs = [firstrun.TierRun("unit", "green", 12.3, "5 passed", ""),
            firstrun.TierRun("e2e", "red", None, None, "fail")]
    firstrun.save_measurements(tmp_path, "app-0123456789ab", runs)
    assert firstrun.load_measurements(tmp_path, "app-0123456789ab") == {"unit": 12.3}
    assert firstrun.load_measurements(tmp_path, "other-0123456789ab") == {}
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_firstrun.py`
Expected: FAIL — `ImportError: cannot import name 'firstrun'`.

- [ ] **Step 3: Write the module and correct the spec**

`flotilla/onboard/firstrun.py`:

```python
"""The first run of every chosen test tier (spec, section 4.2 step 3).

A tier that is not green is not recorded as working: no time is saved for it, and the caller shows
the tail. A run is killed as a process group on timeout, because a shell's child can hold the output
pipe open long after the shell itself is gone. Measured times are machine facts and live in the
state directory, never in the project (plan decision 1). The lane will book the machine for these
runs once it exists (plan decision 3).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from flotilla.onboard.tomlw import render_toml

_SUMMARY = re.compile(r"\b\d+ passed\b|^test result: |^ok\s|\bTests?:\s+\d+")
TAIL_LINES = 20


@dataclass(frozen=True)
class TierRun:
    name: str
    status: str
    seconds: float | None
    summary: str | None
    tail: str


def _tail(text: str) -> str:
    return "\n".join(text.rstrip().splitlines()[-TAIL_LINES:])


def _summary(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if _SUMMARY.search(line.strip())]
    return lines[-1] if lines else None


def run_tier(name: str, command: str, cwd: Path, *, timeout: float) -> TierRun:
    started = time.monotonic()
    proc = subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        output, _ = proc.communicate()
        return TierRun(name, "timed-out", None, None, _tail(output or ""))
    seconds = round(time.monotonic() - started, 2)
    output = output or ""
    if proc.returncode < 0:
        status = "killed"
    elif proc.returncode == 0:
        status = "green"
    else:
        status = "red"
    return TierRun(name, status, seconds if status == "green" else None, _summary(output), _tail(output))


def _path(state: Path, repo_key: str) -> Path:
    return state / "measurements" / f"{repo_key}.toml"


def save_measurements(state: Path, repo_key: str, runs: list[TierRun]) -> Path:
    path = _path(state, repo_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"seconds": {run.name: run.seconds for run in runs if run.status == "green"}}
    path.write_text(render_toml(data, header="Tier run times on this machine, green runs only."), encoding="utf-8")
    return path


def load_measurements(state: Path, repo_key: str) -> dict[str, float]:
    try:
        data = tomllib.loads(_path(state, repo_key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return dict(data.get("seconds") or {})
```

Correct the spec. In section 4.2, replace step 3 with:

```markdown
3. **First run:** every chosen test tier runs once (directly, with a timeout that kills its whole process group;
   through the lane once the lane exists). Its time is a **machine** fact and is saved in the state directory
   (`measurements/<repo-key>.toml`), never in `project.toml`. A tier that is not green is **not** recorded as
   working — onboarding shows the tail and asks whether the command or the project is wrong.
```

In the section 4.4 `project.toml` example, delete both `measured_seconds = …` lines.

In section 4.6, replace the first sentence of "Required jobs" with: "**Required jobs** are read from the last
completed push run on trunk (`gh run view --json jobs`): those names are already matrix-expanded and include only
jobs a push triggers. With no such run yet, or without `gh`, job ids are read from the workflow files and marked
unverified." (plan decision 2)

- [ ] **Step 4: Run to see them pass; see the group kill go red**

Run: `uv run --with pytest python -m pytest tests/test_onboard_firstrun.py`
Expected: `6 passed`.

Injection (removal): replace `os.killpg(proc.pid, signal.SIGKILL)` with `proc.kill()`. Expected:
`test_timeout_kills_the_whole_process_group` FAILS on the elapsed-time assert (the grandchild `sleep 30` holds the
pipe). Undo.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/firstrun.py tests/test_onboard_firstrun.py docs/specs/2026-09-22-flotilla-design.md
git -C /home/max/workspace/flotilla commit -m "feat(onboard): first run of each tier; run times are machine facts"
```

---

### Task 10: Drift between the profile and the repository

**Files:**
- Create: `flotilla/onboard/check.py`
- Test: `tests/test_onboard_check.py`

**Interfaces:**
- Produces: `check.check_drift(profile: dict, det: dict, measured: dict[str, float]) -> list[str]` — one plain
  sentence per finding; empty when nothing drifted.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_check.py`:

```python
from flotilla.onboard.check import check_drift

PROFILE = {
    "trunk": {"branch": "main"},
    "tests": {"tier": [{"name": "unit", "command": "uv run pytest", "required_for": ["push"]}]},
    "ci": {"provider": "github", "required_jobs": ["test", "lint"], "workflow_fingerprint": "sha256:a"},
}


def detection(**ci_overrides):
    ci = {"provider": "github", "fingerprint": "sha256:a", "jobs": ["test", "lint"], "jobs_source": "last-push-run"}
    ci.update(ci_overrides)
    return {"trunk": "main", "ci": ci}


def test_nothing_drifted():
    assert check_drift(PROFILE, detection(), {"unit": 4.2}) == []


def test_workflow_files_changed():
    assert any("workflow files changed" in f for f in check_drift(PROFILE, detection(fingerprint="sha256:b"), {"unit": 1}))


def test_a_new_job_ran_that_is_not_required():
    found = check_drift(PROFILE, detection(jobs=["test", "lint", "e2e"]), {"unit": 1})
    assert found == ["CI: job `e2e` ran on the last push but is not required"]


def test_a_required_job_did_not_run():
    found = check_drift(PROFILE, detection(jobs=["test"]), {"unit": 1})
    assert found == ["CI: required job `lint` did not run on the last push"]


def test_trunk_renamed():
    det = {**detection(), "trunk": "trunk"}
    assert "trunk: the profile says `main`, the repository says `trunk`" in check_drift(PROFILE, det, {"unit": 1})


def test_a_tier_never_green_on_this_machine():
    assert check_drift(PROFILE, detection(), {}) == ["tests: tier `unit` has never run green on this machine"]
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_check.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'flotilla.onboard.check'`.

- [ ] **Step 3: Write the module**

`flotilla/onboard/check.py`:

```python
"""Drift between the project profile and the repository as it is now (spec, section 4.5 rule 2).

Job comparisons are made only against a real push run: a fallback list read from workflow files is
unverified by definition, and comparing against it would report drift that nobody can act on.
"""

from __future__ import annotations


def check_drift(profile: dict, det: dict, measured: dict[str, float]) -> list[str]:
    findings: list[str] = []
    ci = profile.get("ci") or {}
    now = det.get("ci") or {}
    if ci.get("provider") == "github":
        if now.get("provider") != "github":
            findings.append("CI: the profile names GitHub Actions, but no workflow files were found")
        else:
            if ci.get("workflow_fingerprint") and now.get("fingerprint") != ci["workflow_fingerprint"]:
                findings.append("CI: workflow files changed since onboarding; review `required_jobs`")
            if now.get("jobs_source") == "last-push-run":
                required = set(ci.get("required_jobs") or [])
                ran = set(now.get("jobs") or [])
                findings += [f"CI: job `{job}` ran on the last push but is not required" for job in sorted(ran - required)]
                findings += [f"CI: required job `{job}` did not run on the last push" for job in sorted(required - ran)]
    trunk = (profile.get("trunk") or {}).get("branch")
    if trunk and det.get("trunk") and det["trunk"] != trunk:
        findings.append(f"trunk: the profile says `{trunk}`, the repository says `{det['trunk']}`")
    for tier in (profile.get("tests") or {}).get("tier") or []:
        if tier.get("name") not in measured:
            findings.append(f"tests: tier `{tier.get('name')}` has never run green on this machine")
    return findings
```

- [ ] **Step 4: Run to see them pass**

Run: `uv run --with pytest python -m pytest tests/test_onboard_check.py`
Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/check.py tests/test_onboard_check.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): drift check against the last push run and this machine"
```

---

### Task 11: The `onboard` commands and the `flotilla-onboard` skill

**Files:**
- Create: `flotilla/onboard/answers.py`, `flotilla/onboard/commands.py`, `skills/flotilla-onboard/SKILL.md`
- Modify: `flotilla/cli.py`
- Test: `tests/test_onboard_cli.py`, `tests/test_onboard_skill.py`

**Interfaces:**
- Consumes: every module of Tasks 1–10; `paths.state_dir()`; `config.load_project`.
- Produces: `answers.load(state, repo_key) -> dict`, `answers.save(state, repo_key, data) -> None`,
  `answers.reset(state, repo_key) -> None`; CLI:
  - `flotilla onboard machine` — measure and write `machine.toml`; exit 1 when the `python3` on PATH is too old.
  - `flotilla onboard detect [--root DIR]` — print the detection as JSON.
  - `flotilla onboard next [--root DIR]` — print `{"questions": [...], "done": bool}`.
  - `flotilla onboard answer ID VALUE... [--root DIR]` — validate against the question as it applies now; exit 2 on
    a bad id or value.
  - `flotilla onboard write [--root DIR] [--force] [--no-run] [--keep-unmeasured] [--timeout S]` — exit 2 when
    questions remain, 2 when the profile exists without `--force`, 4 when a tier is not green (tails printed) unless
    `--keep-unmeasured`; on success the stored answers are cleared.
  - `flotilla onboard check [--root DIR]` — print drift; exit 1 when there is any.
  - `flotilla onboard reset [--root DIR]` — forget stored answers.

- [ ] **Step 1: Write the failing tests**

`tests/test_onboard_cli.py`:

```python
import io
import json
import subprocess
import sys
import tomllib
from contextlib import redirect_stdout

import pytest

from flotilla import cli


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "app"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    return root


def answer_everything(root):
    command = f"{sys.executable} -c \"print('1 passed')\""
    for _ in range(20):
        code, out = run_cli("onboard", "next", "--root", str(root))
        assert code == 0
        page = json.loads(out)
        if page["done"]:
            return
        for q in page["questions"]:
            value = command if q["id"] == "tiers" else q["options"][0]["value"]
            code, out = run_cli("onboard", "answer", q["id"], value, "--root", str(root))
            assert code == 0, out
    raise AssertionError("never done")


def test_full_onboarding_writes_a_loadable_profile(repo, tmp_path):
    answer_everything(repo)
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 0, out
    profile = tomllib.loads((repo / ".flotilla" / "project.toml").read_text(encoding="utf-8"))
    assert profile["schema"] == 1 and profile["tests"]["tier"][0]["name"] == "custom-1"
    assert "measured_seconds" not in json.dumps(profile)
    assert list((tmp_path / "state" / "measurements").glob("*.toml"))
    assert run_cli("onboard", "check", "--root", str(repo))[0] == 0


def test_second_write_without_force_is_refused(repo):
    answer_everything(repo)
    assert run_cli("onboard", "write", "--root", str(repo))[0] == 0
    answer_everything(repo)
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 2 and "already exists" in out


def test_write_before_all_answers_is_refused(repo):
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 2 and "questions remain" in out


def test_a_red_tier_is_not_written(repo):
    for _ in range(20):
        page = json.loads(run_cli("onboard", "next", "--root", str(repo))[1])
        if page["done"]:
            break
        for q in page["questions"]:
            value = "exit 3" if q["id"] == "tiers" else q["options"][0]["value"]
            run_cli("onboard", "answer", q["id"], value, "--root", str(repo))
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 4 and "red" in out
    assert not (repo / ".flotilla" / "project.toml").exists()


def test_outside_a_repository_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    code, out = run_cli("onboard", "next", "--root", str(tmp_path))
    assert code == 2 and "not inside a git repository" in out


def test_bad_answer_is_refused(repo):
    code, out = run_cli("onboard", "answer", "review", "sometimes", "--root", str(repo))
    assert code == 2 and "not one of" in out


def test_machine_writes_machine_toml(repo, tmp_path):
    code, _ = run_cli("onboard", "machine")
    assert (tmp_path / "state" / "machine.toml").is_file()
    assert code in (0, 1)
```

`tests/test_onboard_skill.py`:

```python
import re
from pathlib import Path

from flotilla import cli

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "flotilla-onboard" / "SKILL.md"


def frontmatter(text):
    block = text.split("---", 2)[1]
    return dict(line.split(":", 1) for line in block.strip().splitlines() if ":" in line)


def test_skill_frontmatter():
    meta = frontmatter(SKILL.read_text(encoding="utf-8"))
    assert meta["name"].strip() == "flotilla-onboard"
    assert 0 < len(meta["description"].strip()) <= 1024


def test_every_onboard_subcommand_the_skill_names_exists():
    named = set(re.findall(r"flotilla onboard ([a-z]+)", SKILL.read_text(encoding="utf-8")))
    parser = cli.build_parser()
    onboard = next(a for a in parser._subparsers._group_actions[0].choices["onboard"]._actions
                   if a.dest == "action")
    assert named and named <= set(onboard.choices), named - set(onboard.choices)
```

- [ ] **Step 2: Run to see them fail**

Run: `uv run --with pytest python -m pytest tests/test_onboard_cli.py tests/test_onboard_skill.py`
Expected: FAIL — `argparse` exits on the unknown `onboard` command (`SystemExit: 2`), and the skill file is missing.

- [ ] **Step 3: Write the answers store, the commands and the skill**

`flotilla/onboard/answers.py`:

```python
"""Answers given so far, per repository, in the machine's state directory.

Kept outside the repository: half an onboarding is nobody's project fact, and it must survive the
skill's session ending between two questions.
"""

from __future__ import annotations

import json
from pathlib import Path


def _path(state: Path, repo_key: str) -> Path:
    return state / "onboarding" / f"{repo_key}.json"


def load(state: Path, repo_key: str) -> dict:
    try:
        data = json.loads(_path(state, repo_key).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(state: Path, repo_key: str, data: dict) -> None:
    path = _path(state, repo_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def reset(state: Path, repo_key: str) -> None:
    _path(state, repo_key).unlink(missing_ok=True)
```

In `flotilla/cli.py`, add to `build_parser`, after the `hook` parser:

```python
    onboard = sub.add_parser("onboard", help="measure, detect, ask and write the project profile")
    actions = onboard.add_subparsers(dest="action", required=True)
    actions.add_parser("machine", help="measure this machine into the state directory")
    for name, text in (("detect", "print what the repository declares"),
                       ("next", "print the next questions"),
                       ("check", "report drift between the profile and the repository"),
                       ("reset", "forget the answers given so far")):
        actions.add_parser(name, help=text).add_argument("--root", default=".")
    answer = actions.add_parser("answer", help="record the answer to one question")
    answer.add_argument("question")
    answer.add_argument("values", nargs="+")
    answer.add_argument("--root", default=".")
    write = actions.add_parser("write", help="run the tiers once and write .flotilla/project.toml")
    write.add_argument("--root", default=".")
    write.add_argument("--force", action="store_true", help="replace an existing profile")
    write.add_argument("--no-run", action="store_true", help="do not run the tiers now")
    write.add_argument("--keep-unmeasured", action="store_true", help="write even if a tier is not green")
    write.add_argument("--timeout", type=float, default=1800.0, help="seconds per tier (default 1800)")
```

and the branch in `main`, before `return 2`:

```python
    if args.command == "onboard":
        from flotilla.onboard.commands import run_onboard
        return run_onboard(args)
```

Create `flotilla/onboard/commands.py`:

```python
"""`flotilla onboard …` — the deterministic half of onboarding; the skill asks the questions."""

from __future__ import annotations

import json
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.onboard import answers as store
from flotilla.onboard import machine
from flotilla.onboard.check import check_drift
from flotilla.onboard.detect import detect
from flotilla.onboard.firstrun import load_measurements, run_tier, save_measurements
from flotilla.onboard.profile import ProfileExists, build_profile, write_profile
from flotilla.onboard.questions import AnswerError, all_questions, next_questions, validate_answer


def _machine() -> int:
    data = machine.measure_machine()
    path = machine.write_machine(paths.state_dir(), data)
    print(f"machine profile written: {path}")
    python3 = data["python3"]
    if not python3.get("ok"):
        detail = python3.get("error") or f"python3 on PATH is {python3.get('version')} ({python3.get('path')})"
        print(f"fail  python3: {detail}; hooks need 3.11+ (`brew install python` or `uv python install 3.11`)")
        return 1
    print(f"ok    python3 {python3['version']} at {python3['path']}; gh {data['gh']}")
    return 0


def _write(det: dict, given: dict, args, state: Path) -> int:
    remaining = next_questions(det, given)
    if remaining:
        print(f"questions remain: {', '.join(q['id'] for q in remaining)}; run `flotilla onboard next`")
        return 2
    root = Path(det["root"])
    target = root / config.PROJECT_DIR / config.PROJECT_FILE
    if target.exists() and not args.force:
        print(f"{target} already exists; run `flotilla onboard check`, or re-onboard with --force")
        return 2
    data = build_profile(det, given)
    tiers = (data.get("tests") or {}).get("tier") or []
    if tiers and not args.no_run:
        runs = [run_tier(t["name"], t["command"], root, timeout=args.timeout) for t in tiers]
        for run in runs:
            timing = f"{run.seconds:.1f}s" if run.seconds is not None else "no time recorded"
            print(f"{run.status:<9} {run.name}: {run.summary or ''} ({timing})")
            if run.status != "green":
                print(f"--- last lines of {run.name} ---\n{run.tail}\n---")
        if any(run.status != "green" for run in runs) and not args.keep_unmeasured:
            print("not written: a tier is not green. Fix the command (`flotilla onboard answer tiers …`) "
                  "or the project, or pass --keep-unmeasured")
            return 4
        save_measurements(state, det["repo_key"], runs)
    try:
        path = write_profile(root, data, force=args.force)
    except ProfileExists as err:
        print(err)
        return 2
    store.reset(state, det["repo_key"])
    print(f"project profile written: {path}")
    return 0


def run_onboard(args) -> int:
    if args.action == "machine":
        return _machine()
    state = paths.state_dir()
    try:
        det = detect(Path(args.root))
    except repo.NotARepository as err:
        print(f"{err}; run onboarding from inside the repository, or pass --root")
        return 2
    given = store.load(state, det["repo_key"])
    if args.action == "detect":
        print(json.dumps(det, indent=2, sort_keys=True))
        return 0
    if args.action == "next":
        page = next_questions(det, given)
        print(json.dumps({"questions": page, "done": not page}, indent=2))
        return 0
    if args.action == "answer":
        question = next((q for q in all_questions(det, given) if q["id"] == args.question), None)
        if question is None:
            print(f"no question `{args.question}` applies now; run `flotilla onboard next`")
            return 2
        try:
            given[args.question] = validate_answer(question, args.values)
        except AnswerError as err:
            print(err)
            return 2
        store.save(state, det["repo_key"], given)
        print(f"recorded {args.question}")
        return 0
    if args.action == "write":
        return _write(det, given, args, state)
    if args.action == "check":
        try:
            profile = config.load_project(Path(det["root"])).data
        except config.ConfigError as err:
            print(err)
            return 2
        findings = check_drift(profile, det, load_measurements(state, det["repo_key"]))
        for finding in findings:
            print(finding)
        return 1 if findings else 0
    if args.action == "reset":
        store.reset(state, det["repo_key"])
        print("answers forgotten")
        return 0
    return 2
```

Note on `load_project`: `config.load_project` returns a `Project` whose `data` field is the parsed dict (foundation
Task 5), which is what `check_drift` takes.

`skills/flotilla-onboard/SKILL.md`:

````markdown
---
name: flotilla-onboard
description: Set up flotilla in a repository — measure this machine, show what the repository already declares (test commands, CI, releases), ask the few questions only a person can answer, run each chosen test tier once, and write .flotilla/project.toml. Use when someone asks to onboard, set up, configure or initialize flotilla, to prepare a repository for a fleet of Claude Code sessions, or to re-check an existing flotilla profile for drift.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla onboard *)
---

# Onboard a repository to flotilla

flotilla stays inactive in a repository until `.flotilla/project.toml` exists. This skill produces that file. The
command line decides which questions apply and checks every answer; your job is to ask them, faithfully, and to
report what the commands print.

Run every command from the repository root. The CLI is `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla`.

## 1. Measure the machine

Run `flotilla onboard machine`. If it prints `fail  python3`, stop and tell the person exactly what it printed:
hooks run through the `python3` on PATH, and nothing else works until that is 3.11 or newer.

## 2. Show what was found

Run `flotilla onboard detect`. Summarize it in a few lines: trunk, test commands and where each came from, CI and
whether its jobs came from a real push run or only from workflow files ("unverified"), and any notes. Do not
decide anything from it — the person confirms it in the questions.

## 3. Ask, one round at a time

Loop:

1. Run `flotilla onboard next`. If `done` is true, go to step 4.
2. Ask the returned questions with AskUserQuestion, in one call: use each question's `question`, `header`,
   `options` (label and description) and `multi_select` exactly as given. Do not add, drop or reorder options.
3. Record each answer with `flotilla onboard answer <id> <value>`: use the option's `value`, not its label. For a
   multi-select question pass every chosen value. When the person typed their own text (Other) and the question
   has `free_text`, pass the text itself as the value.
4. If `answer` refuses a value, show the person the refusal and ask that question again.

Answers are stored between sessions; `flotilla onboard reset` forgets them.

## 4. Write the profile

Run `flotilla onboard write`. It runs each chosen test tier once, then writes the file.

- If a tier is not green, it prints the last lines and writes nothing. Show those lines and ask the person whether
  the command is wrong (then record a corrected `tiers` answer and run `write` again) or the project is red right
  now (then run `write --keep-unmeasured`).
- If the profile already exists, it refuses. Suggest `flotilla onboard check` first; re-onboard with `--force`
  only when the person asks to replace their file.

## 5. Confirm

Run `flotilla onboard check` and report its findings, if any. Tell the person that the file is theirs to edit and
commit, and that guards and git hooks they chose are recorded but installed later by flotilla's guard setup.
````

- [ ] **Step 4: Run to see them pass; validate the plugin**

Run: `uv run --with pytest python -m pytest tests/test_onboard_cli.py tests/test_onboard_skill.py`
Expected: `9 passed`.

Run: `claude plugin validate /home/max/workspace/flotilla`
Expected: validation passes and names the skill.

Injection (plausible neighbour): in `SKILL.md` change one `flotilla onboard answer` to `flotilla onboard reply`.
Expected: `test_every_onboard_subcommand_the_skill_names_exists` FAILS naming `reply`. Undo.

- [ ] **Step 5: Run everything, then commit**

Run: `uv run --with pytest python -m pytest` and `uv run --python 3.11 --with pytest python -m pytest`
Expected: all pass on both (`218 passed` = 116 foundation + 102 here, if the per-task counts hold; report the
printed number).
Run: `python3 tools/check_no_cyrillic.py` → exit 0.

```bash
git -C /home/max/workspace/flotilla add flotilla/onboard/answers.py flotilla/onboard/commands.py flotilla/cli.py skills tests/test_onboard_cli.py tests/test_onboard_skill.py
git -C /home/max/workspace/flotilla commit -m "feat(onboard): onboard commands and the flotilla-onboard skill"
```

---

## Done when

- A fresh repository can be onboarded end to end through the CLI (`machine`, `next`/`answer` rounds, `write`,
  `check`), and the written profile loads through `config.load_project`.
- No measured time is written into `project.toml`; the spec says so in 4.2 and 4.4.
- Each injection named in Tasks 1, 4, 5, 7, 8, 9 and 11 was seen red and undone.
- The full suite passes on Python 3.11 and the newest local interpreter; CI is green on Linux and macOS after push.
- Pushing needs Max's yes, as always.
