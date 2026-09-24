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

_TOP_KEY = re.compile(r"^(\S[^:]*):(.*)$")
_INDENTED_KEY = re.compile(r"^(\s+)[\"']?([A-Za-z0-9_-]+)[\"']?:\s*(#.*)?$")
UNVERIFIED = "workflow-files (unverified until a push run on trunk exists)"
NO_IDS = "unknown: no job ids could be read from the workflow files"
PUSH_EVENTS = ("push", "pull_request", "pull_request_target")


def workflow_files(root: Path) -> list[Path]:
    """Workflow files that really live inside the repository: a symlink out of it is not read."""
    folder = root / ".github" / "workflows"
    if not folder.is_dir():
        return []
    inside = root.resolve()
    return sorted(p for p in folder.iterdir()
                  if p.suffix in (".yml", ".yaml") and p.is_file() and p.resolve().is_relative_to(inside))


def fingerprint(root: Path, files: list[Path]) -> str | None:
    if not files:
        return None
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        try:
            digest.update(path.read_bytes() + b"\0")
        except OSError:
            digest.update(b"unreadable\0")
    return "sha256:" + digest.hexdigest()


def _top_level(text: str) -> dict[str, list[str]]:
    """Top-level keys with their inline value (first element) and the indented lines under them."""
    blocks: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        top = _TOP_KEY.match(line)
        if top:
            current = top.group(1).strip().strip("'\"")
            blocks[current] = [top.group(2).strip()]
        elif current is not None:
            blocks[current].append(line)
    return blocks


def job_ids_from_file(text: str) -> list[str]:
    """Job ids under `jobs:`, at whatever indentation the first job uses; [] when none can be read."""
    lines = _top_level(text).get("jobs", [""])[1:]
    ids: list[str] = []
    level = None
    for line in lines:
        key = _INDENTED_KEY.match(line)
        indent = len(line) - len(line.lstrip())
        if level is None and key:
            level = indent
        if key and indent == level:
            ids.append(key.group(2))
    return ids


def triggered_by_push(text: str) -> bool | None:
    """Whether a push or pull request runs this workflow; None when `on:` cannot be read."""
    blocks = _top_level(text)
    on = blocks.get("on") or blocks.get("true")  # YAML 1.1 reads a bare `on` key as true
    if on is None:
        return None
    body = " ".join([on[0]] + on[1:])
    return any(re.search(rf"(?<![A-Za-z_]){event}(?![A-Za-z_])", body) for event in PUSH_EVENTS)


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
    left_out: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if triggered_by_push(text) is False:
            left_out.append(path.relative_to(root).as_posix())
            continue
        file_ids.extend(job_ids_from_file(text))
    result = {
        "provider": "github",
        "workflow_files": [p.relative_to(root).as_posix() for p in files],
        "fingerprint": fingerprint(root, files),
        "file_job_ids": file_ids,
        "left_out": left_out,
    }
    slug = github_slug(normalized_origin)
    jobs = jobs_from_last_push_run(slug, trunk, run=run) if slug else None
    if jobs:
        result.update(jobs=jobs, jobs_source="last-push-run")
    elif file_ids:
        result.update(jobs=file_ids, jobs_source=UNVERIFIED)
    else:
        result["jobs_source"] = NO_IDS
    if slug:
        methods = merge_methods(slug, run=run)
        if methods is not None:
            result["merge_methods"] = methods
    return result
