"""Secrets are never committed by organize, under any answer (spec, section 7).

Files are scanned by content - betterleaks, or gitleaks, when one is installed; otherwise a short set of
high-signal token patterns - and always by name. A hit is named and its file left in place, out of every branch.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

TOOLS = ("betterleaks", "gitleaks")
NAMES = re.compile(r"(^|/)(\.env(\..*)?|id_[a-z0-9]+|[^/]*\.(pem|key|p12|pfx)|[^/]*credentials[^/]*)$", re.I)
PATTERNS = (
    ("an AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("a GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}")),
    ("a Slack token", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("a private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("an Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("an OpenAI key", re.compile(r"sk-[A-Za-z0-9]{32,}")),
    ("a Google API key", re.compile(r"AIza[0-9A-Za-z_-]{35}")),
    ("a Stripe live key", re.compile(r"[rs]k_live_[0-9A-Za-z]{24,}")),
)
TIMEOUT = 60
CHUNK = 1_000_000
OVERLAP = 256   # longer than any real token of these kinds, so one across two chunks is still seen


@dataclass(frozen=True)
class Hit:
    path: str
    why: str


def _tool(which) -> str | None:
    return next((found for found in (which(name) for name in TOOLS) if found), None)


def _patterns(file: Path) -> str:
    """What the file holds that looks like a secret, read whole in overlapping chunks (security review of
    1f67d17: a cap let a secret past it through); a file that cannot be read is a hit, never clean."""
    try:
        with file.open("rb") as handle:
            tail = ""
            while True:
                chunk = handle.read(CHUNK)
                if not chunk:
                    return ""
                text = tail + chunk.decode("utf-8", "replace")
                found = next((what for what, pattern in PATTERNS if pattern.search(text)), "")
                if found:
                    return found
                tail = text[-OVERLAP:]
    except OSError:
        return "something it could not read (the file cannot be opened)"


def scan(tree: Path, paths: list[str], run=subprocess.run, which=None) -> list[Hit]:
    tree = Path(tree)
    tool = _tool(which or shutil.which)
    hits = []
    for path in paths:
        if NAMES.search(path):
            hits.append(Hit(path, "its name is a secret's"))
            continue
        file = tree / path
        if not file.is_file():
            continue
        if tool:
            try:
                done = run([tool, "dir", str(file), "--report-format", "json", "--report-path", "-", "--no-banner"],
                           capture_output=True, text=True, check=False, timeout=TIMEOUT)
            except (OSError, subprocess.TimeoutExpired):
                done = None
            if done is not None and done.returncode == 1:
                hits.append(Hit(path, f"{Path(tool).name} found a secret in it"))
                continue
            if done is not None and done.returncode == 0:
                continue
        what = _patterns(file)   # no tool, or one that could not answer
        if what:
            hits.append(Hit(path, f"it holds what looks like {what}"))
    return hits
