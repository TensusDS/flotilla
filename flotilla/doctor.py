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
            read=None, os_name: str = sys.platform,
            python=tuple(sys.version_info[:3]), timeout: float = 30, setup: bool = True,
            home: Path | None = None, read_rows=None) -> list[Finding]:
    """`timeout` bounds each external call; a hook passes a short one so its findings are printed
    before Claude Code kills it."""
    asked: dict = {}

    def text() -> str:   # one `claude agents --json` answers both the count and the shape
        if "text" not in asked:
            try:
                asked["text"] = census_mod.read_text(run=run, timeout=timeout)
            except census_mod.CensusUnavailable as err:
                asked["text"] = err
        if isinstance(asked["text"], Exception):
            raise asked["text"]
        return asked["text"]
    if read is None:
        read = lambda: census_mod.drop_gone(census_mod.parse_census(text()))  # noqa: E731
    if read_rows is None:
        read_rows = lambda: census_mod.rows_of(text())  # noqa: E731
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
        done = run(["claude", "--version"], capture_output=True, text=True, timeout=timeout, check=False)
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
    except OSError as err:
        findings.append(Finding("fail", "claude", f"`claude` cannot be run: {err}"))
    except subprocess.TimeoutExpired:
        findings.append(Finding("warn", "claude", f"`claude --version` did not answer within {timeout:g}s"))

    try:
        sessions = read()
        findings.append(Finding("ok", "census", f"{len(sessions)} live session(s)"))
    except census_mod.CensusUnavailable as err:
        findings.append(Finding("fail", "census", f"unknown: {err}"))

    if setup:   # a hook skips them: one more call to `claude`, and a drift is no news at every session's start
        findings += _records(env=env, home=home, read_rows=read_rows)

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
        if setup:   # a hook skips them: its firing proves the plugin runs, and its budget is seconds
            findings += _setup(root, run=run, timeout=timeout, home=home)

    return findings


def _records(*, env, home: Path | None, read_rows) -> list[Finding]:
    """Claude Code's own records flotilla reads, against the shape measured on real versions: a drift does not
    break flotilla, it blinds one of its checks without an error, so it is a warning that names what goes blind."""
    from flotilla.core import claude_state
    after = "update flotilla, or report it at https://github.com/TensusDS/flotilla/issues with `claude --version`"
    found = []
    try:
        rows = read_rows()
    except census_mod.CensusUnavailable as err:
        rows = []
        found.append(Finding("info", "census-shape", f"not measured: {err}"))
    else:
        problems = claude_state.census_problems(rows)
        if not rows:
            found.append(Finding("info", "census-shape", "no session is listed to compare with the measured shape"))
        elif problems:
            found.append(Finding("warn", "census-shape", "`claude agents --json` changed shape since Claude Code "
                                                         f"{claude_state.MEASURED_ON}: " + "; ".join(problems), after))
        else:
            found.append(Finding("ok", "census-shape", f"as measured on Claude Code {claude_state.MEASURED_ON} "
                                                       f"({len(rows)} listed)"))
    config = claude_state.config_dir(env, home)
    readable = claude_state.registry_readable(rows, config)
    if readable:
        found.append(Finding("ok", "registry", f"{config / 'sessions'} says how each session was started"))
    elif readable is False:
        found.append(Finding("warn", "registry", f"no live session has its entry in {config / 'sessions'} (or it "
                                                 "no longer says how the session was started): a review a plugin "
                                                 "hook starts in a seat's tree will be named a stranger", after))
    else:
        found.append(Finding("info", "registry", "no session of yours is listed with a pid to look its entry up by"))
    record = claude_state.trust_record(home)
    if record:
        found.append(Finding("ok", "trust-record", "~/.claude.json keeps trust as "
                                                   "`projects[<path>].hasTrustDialogAccepted`"))
    elif record is False:
        found.append(Finding("warn", "trust-record", "~/.claude.json no longer keeps `projects[<path>]."
                                                     "hasTrustDialogAccepted`: whether a directory is "
                                                     "trusted cannot be read, so a spawn there may fail unwarned",
                             after))
    else:
        found.append(Finding("info", "trust-record", "no ~/.claude.json to read"))
    return found


def _setup(root: Path, *, run, timeout: float, home: Path | None) -> list[Finding]:
    """What a background session raised here depends on: the plugin enabled, the directory trusted (F2, F3)."""
    from flotilla.core import claude_state
    from flotilla.fleet import launch
    try:   # spawn launches from the main checkout; a fleet worktree is never trusted itself
        root = launch.main_checkout(root, run=run)
    except launch.LaunchError:
        pass
    found = []
    enabled = claude_state.plugin_enabled(root, run=run, timeout=timeout)
    if enabled:
        found.append(Finding("ok", "plugin", f"flotilla is enabled in {root}"))
    elif enabled is False:
        found.append(Finding("fail", "plugin", f"flotilla is not enabled in {root}: sessions raised here would have "
                                               "no flotilla skills or hooks",
                             f"run `{claude_state.install_hint(root, run=run, timeout=timeout)}` in {root}"))
    else:
        found.append(Finding("warn", "plugin", "could not tell whether flotilla is enabled here "
                                               "(`claude plugin list` did not answer)"))
    trust = claude_state.trusted(root, home=home)
    if trust:
        found.append(Finding("ok", "trust", f"{root} is trusted"))
    elif trust is False:
        found.append(Finding("fail", "trust", f"{root} is not trusted, and `claude --bg` refuses it",
                             "run `claude` here once and accept the trust dialog"))
    else:
        found.append(Finding("warn", "trust", f"could not tell whether {root} is trusted (no entry in "
                                              "~/.claude.json)", "run `claude` here once and accept the trust dialog"))
    return found


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
