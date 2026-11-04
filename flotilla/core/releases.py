"""The newest flotilla release and the commit the `stable` channel names, asked of GitHub and kept for a day.

`git ls-remote` with no repository of its own (no hooks, no config of a checkout) and no prompt; five seconds at
most, because a session's start waits for it. A failed answer is not kept, so the next start asks again. Only
`vX.Y.Z` tags count: a pre-release or any other tag is not a release (spec 2026-11-04-plugin-updates, part B).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

URL = "https://github.com/TensusDS/flotilla"
CACHE = "releases.json"
TAG = re.compile(r"refs/tags/v(\d+)\.(\d+)\.(\d+)")
STABLE = "refs/heads/stable"


@dataclass(frozen=True)
class Releases:
    newest: str | None
    stable: str | None
    asked_at: str
    error: str = ""


def ask(run=subprocess.run, *, timeout: float = 5.0, now: dt.datetime | None = None) -> Releases:
    asked = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "SSH_ASKPASS": ""}
    try:
        done = run(["git", "ls-remote", "--refs", URL, "refs/tags/v*", STABLE], capture_output=True, text=True,
                   check=False, timeout=timeout, env=env, cwd=os.path.expanduser("~"))
    except (OSError, subprocess.TimeoutExpired) as err:
        return Releases(None, None, asked, f"GitHub could not be asked: {err}")
    if done.returncode != 0:
        return Releases(None, None, asked, f"`git ls-remote` exited {done.returncode}: "
                                            f"{(done.stderr or '').strip()[-200:]}")
    versions, stable = [], None
    for line in (done.stdout or "").splitlines():
        sha, _, ref = line.partition("\t")
        match = TAG.fullmatch(ref.strip())
        if match:
            versions.append(tuple(int(part) for part in match.groups()))
        elif ref.strip() == STABLE:
            stable = sha.strip()
    newest = ".".join(map(str, max(versions))) if versions else None
    return Releases(newest, stable, asked, "" if newest else "no vX.Y.Z tag in the answer")


def cached(state: Path, run=subprocess.run, *, now: dt.datetime | None = None,
           max_age: dt.timedelta = dt.timedelta(days=1), fresh: bool = False) -> Releases:
    now = now or dt.datetime.now(dt.timezone.utc)
    path = Path(state) / CACHE
    if not fresh:
        try:
            kept = Releases(**json.loads(path.read_text(encoding="utf-8")))
            if kept.newest and now - dt.datetime.fromisoformat(kept.asked_at) < max_age:
                return kept
        except (OSError, ValueError, TypeError):
            pass
    found = ask(run, now=now)
    if found.newest:
        try:
            Path(state).mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(found)), encoding="utf-8")
        except OSError:
            pass
    return found
