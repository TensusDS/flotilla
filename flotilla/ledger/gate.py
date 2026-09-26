"""Which gate a shipped commit stands on (spec, section 4.6).

GitHub: every required job, by name, over the commit (or over the earliest later push on trunk that contains it,
because CI runs once per push). A gate command: `<command> <sha>` exits 0 green, 1 red, 2 pending. No CI: a green
push receipt over the commit, or over a later commit that contains it. No CI and no push tiers: nothing verified
this commit, and the gate says so in those words. A question that could not be asked is unknown, never red.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass

from flotilla.ledger import gitq, receipts

GREEN, RED, PENDING, UNKNOWN, NONE = "green", "red", "pending", "unknown", "none"


@dataclass(frozen=True)
class Gate:
    status: str
    text: str


def _gh_json(ledger, *args: str):
    done = ledger.run(["gh", *args], cwd=str(ledger.root), capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout or "null")
    except ValueError:
        return None


def _distance(ledger, older: str, newer: str) -> int | None:
    done = ledger.run(["git", "-C", str(ledger.root), "rev-list", "--count", f"{older}..{newer}"],
                      capture_output=True, text=True, check=False)
    return int(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip().isdigit() else None


def _runs_for(ledger, sha: str) -> list | None:
    listed = _gh_json(ledger, "run", "list", "--commit", sha, "--json", "databaseId,event", "--limit", "20")
    if listed is None:
        return None
    direct = [run["databaseId"] for run in listed if run.get("event") == "push"]
    if direct:
        return direct
    listed = _gh_json(ledger, "run", "list", "--branch", ledger.trunk, "--event", "push", "--json",
                      "databaseId,headSha", "--limit", "30")
    if listed is None:
        return None
    best = None
    for run in listed:
        head = run.get("headSha") or ""
        if not head or gitq.is_ancestor(ledger.root, sha, head, run=ledger.run) is not True:
            continue
        distance = _distance(ledger, sha, head)
        if distance is not None and (best is None or distance < best[0]):
            best = (distance, head)
    return [] if best is None else [run["databaseId"] for run in listed if run.get("headSha") == best[1]]


def _github(ledger, sha: str) -> Gate:
    required = list((ledger.profile.get("ci") or {}).get("required_jobs") or [])
    if not required:
        return Gate(UNKNOWN, "the profile names no required CI jobs; run /flotilla:check")
    runs = _runs_for(ledger, sha)
    if runs is None:
        return Gate(UNKNOWN, "could not ask GitHub for the CI runs")
    if not runs:
        return Gate(PENDING, f"no CI run over {sha[:7]} yet")
    jobs: dict[str, tuple] = {}
    for run_id in runs:
        view = _gh_json(ledger, "run", "view", str(run_id), "--json", "jobs")
        if not isinstance(view, dict):
            return Gate(UNKNOWN, f"could not read CI run {run_id}")
        for job in view.get("jobs") or []:
            jobs.setdefault(job.get("name"), (run_id, job))
    red, waiting = [], []
    for name in required:
        found = jobs.get(name)
        if found is None or found[1].get("status") != "completed":
            waiting.append(name)
        elif found[1].get("conclusion") != "success":
            red.append(f"{name} ({found[1].get('conclusion')})")
    if red:
        return Gate(RED, f"CI over {sha[:7]} is red: {', '.join(red)}")
    if waiting:
        return Gate(PENDING, f"CI over {sha[:7]} has not finished: {', '.join(waiting)}")
    ids = sorted({str(jobs[name][0]) for name in required})
    return Gate(GREEN, f"github run {', '.join(ids)} over {sha[:7]}")


def _command(ledger, sha: str) -> Gate:
    command = (ledger.profile.get("ci") or {}).get("gate_command") or ""
    if not command:
        return Gate(UNKNOWN, "the profile has no gate command; the sender asks the human")
    try:
        done = ledger.run(f"{command} {shlex.quote(sha)}", shell=True, cwd=str(ledger.root), capture_output=True,
                          text=True, check=False, timeout=600)
    except subprocess.TimeoutExpired:
        return Gate(UNKNOWN, "the gate command did not answer within 600 s")
    tail = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()[-1:] or [""]
    return {0: Gate(GREEN, f"gate command green over {sha[:7]}"),
            1: Gate(RED, f"the gate command says red over {sha[:7]}: {tail[0]}"),
            2: Gate(PENDING, f"the gate command says pending over {sha[:7]}")}.get(
        done.returncode, Gate(UNKNOWN, f"the gate command exited {done.returncode}: {tail[0]}"))


def _local(ledger, candidates: list[str]) -> Gate:
    if not receipts.tiers_for(ledger.profile, "push"):
        return Gate(NONE, "none: no CI and no push tiers — nothing verified this commit")
    for sha in candidates:
        ok, _ = receipts.check_receipt(state=ledger.state_dir, repo_key=ledger.repo_key, sha=sha, purpose="push",
                                       profile=ledger.profile)
        if ok:
            return Gate(GREEN, f"local receipt over {sha[:7]}")
    return Gate(PENDING, f"no green push receipt over {candidates[0][:7]}; run `flotilla receipt run --purpose "
                         "push` where it was built")


def gate_for(ledger, sha: str, *, candidates=()) -> Gate:
    provider = (ledger.profile.get("ci") or {}).get("provider", "none")
    if provider == "github":
        return _github(ledger, sha)
    if provider == "command":
        return _command(ledger, sha)
    return _local(ledger, [sha, *candidates])
