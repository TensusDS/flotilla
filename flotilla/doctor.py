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
            python=tuple(sys.version_info[:3]), timeout: float = 30) -> list[Finding]:
    """`timeout` bounds each external call; a hook passes a short one so its findings are printed
    before Claude Code kills it."""
    if read is None:
        read = lambda: census_mod.read_census(run=run, timeout=timeout)  # noqa: E731
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
