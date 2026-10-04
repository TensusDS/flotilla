"""Receipts: the project's test tiers for one purpose, run once over one exact revision.

A receipt is valid only for the revision it ran over and for the tier commands it ran: a moved HEAD or an edited
tier command voids it. A tree with uncommitted changes gets no receipt, because a receipt describes a revision and
the uncommitted part belongs to none. The lane (a later part) will book the machine around these runs.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from flotilla.ledger import gitq
from flotilla.ledger.model import now_iso
from flotilla.onboard.firstrun import measure_once, measure_peaks, run_tier

PURPOSES = ("handover", "push")


class ReceiptRefused(RuntimeError):
    """No receipt can be issued; the message says why."""


def tiers_for(profile: dict, purpose: str) -> list[dict]:
    tiers = (profile.get("tests") or {}).get("tier") or []
    return [tier for tier in tiers if purpose in (tier.get("required_for") or [])]


def tiers_fingerprint(tiers: list[dict]) -> str:
    payload = json.dumps([[tier.get("name"), tier.get("command")] for tier in tiers])
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _path(state: Path, repo_key: str, sha: str, purpose: str) -> Path:
    return Path(state) / "receipts" / repo_key / f"{sha}-{purpose}.json"


def _tier_key(tier: dict) -> str:
    return tiers_fingerprint([tier])


def _files_path(state: Path, repo_key: str, files: str) -> Path:
    return Path(state) / "receipts" / repo_key / f"files-{files}.json"


def _green_over(state: Path, repo_key: str, files: str | None) -> dict:
    """Tier fingerprint -> the green run of that tier over exactly these files, whichever commit carried them."""
    if not files:
        return {}
    try:
        return json.loads(_files_path(state, repo_key, files).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


#: The lockfiles whose content decides whether a tree's dependencies are current.
LOCKFILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock")


def _locks_of(tree: Path) -> str:
    digest = hashlib.sha256()
    for name in LOCKFILES:
        path = Path(tree) / name
        if path.is_file():
            digest.update(name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _birth(tree: Path) -> str:
    """Which checkout stands at this path: the git directory of a tree is made anew when the tree is cut again, so
    a seat retired and raised at the same path is a different tree (review of 0.6.2, I4)."""
    done = subprocess.run(["git", "-C", str(tree), "rev-parse", "--absolute-git-dir"], capture_output=True,
                          text=True, check=False)
    where = done.stdout.strip()
    # a linked tree's `gitdir` file is written once, when the tree is cut; the directory itself changes with every
    # commit (index.lock), so it would set the tree up again each time. The main checkout keeps its .git for good.
    made = Path(where) / "gitdir"
    try:
        found = made.stat() if made.exists() else Path(where).stat()
        return f"{where}:{found.st_ino}:{found.st_ctime_ns if made.exists() else 0}"
    except OSError:
        return where


def _setup_path(state: Path, repo_key: str, tree: Path) -> Path:
    where = hashlib.sha256(str(Path(tree).resolve()).encode()).hexdigest()[:16]
    return Path(state) / "receipts" / repo_key / f"setup-{where}.json"


def set_up(tree: Path, *, state: Path, repo_key: str, profile: dict, timeout: float, force: bool = False) -> str:
    """Run the profile's tree setup in `tree` when its lockfiles changed since the last one there, or it never ran
    (worldcore field test W24: a sender's fresh tree had no node_modules, and its push receipt read as red tests).
    Returns what was done; a failed setup refuses the receipt, naming it."""
    command = ((profile.get("tests") or {}).get("setup_command") or "").strip()
    if not command:
        return ""
    locks, marker, birth = _locks_of(tree), _setup_path(state, repo_key, tree), _birth(tree)
    try:
        last = json.loads(marker.read_text(encoding="utf-8"))
        if not force and last.get("locks") == locks and last.get("birth") == birth:
            return f"skipped: the tree was set up at {last.get('at', '?')} and its lockfile has not changed"
    except (OSError, ValueError):
        pass
    done = run_tier("setup", command, Path(tree), timeout=timeout)
    if done.status != "green":
        code = f"exit {done.exit}" if done.exit is not None else "stopped for its time"
        raise ReceiptRefused(f"not run: the tree setup `{command}` failed in {tree} ({code}); its last lines:\n"
                             + (done.tail or "(it printed nothing)"))
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"locks": locks, "birth": birth, "command": command, "at": now_iso()}),
                      encoding="utf-8")
    return f"set up the tree: {command}"


def to_run(tree: Path, *, state: Path, repo_key: str, purpose: str, profile: dict,
           run=subprocess.run) -> list[str]:
    """The tiers a receipt here would have to run: those with no green answer over these files yet."""
    sha = gitq.resolve(tree, "HEAD", run=run)
    known = _green_over(state, repo_key, gitq.files_of(tree, sha, run=run) if sha else None)
    return [tier["name"] for tier in tiers_for(profile, purpose) if _tier_key(tier) not in known]


def run_receipt(tree: Path, *, state: Path, repo_key: str, purpose: str, profile: dict, timeout: float,
                run=subprocess.run, setup: bool = False) -> dict:
    if purpose not in PURPOSES:
        raise ReceiptRefused(f"unknown purpose `{purpose}`; one of {', '.join(PURPOSES)}")
    sha = gitq.resolve(tree, "HEAD", run=run)
    if sha is None:
        raise ReceiptRefused(f"{tree}: git could not resolve HEAD")
    if gitq.is_clean(tree, run=run) is not True:
        raise ReceiptRefused(f"{tree} has uncommitted changes, or git could not say; a receipt describes one "
                             "revision, so commit first")
    tiers = tiers_for(profile, purpose)
    prepared = ""
    if to_run(tree, state=state, repo_key=repo_key, purpose=purpose, profile=profile, run=run):
        prepared = set_up(tree, state=state, repo_key=repo_key, profile=profile, timeout=timeout, force=setup)
    # A tier's answer depends on the files it tests, not on the commit that carries them: a merge that changes no
    # file is the files the author's receipt already tested. A green answer is reused; a red one runs again.
    files = gitq.files_of(tree, sha, run=run)
    known = _green_over(state, repo_key, files)
    results, learned, ran_green, ran_peaks = [], {}, {}, {}
    for tier in tiers:
        seen = known.get(_tier_key(tier))
        if seen:
            results.append({"name": tier["name"], "status": "green", "seconds": seen.get("seconds"),
                            "summary": f"reused: green over the same files at {str(seen.get('sha', ''))[:7]} "
                                       f"({seen.get('summary', '')})"})
            continue
        r = run_tier(tier["name"], tier["command"], Path(tree), timeout=timeout)
        result = {"name": r.name, "status": r.status, "summary": r.summary or "", "seconds": r.seconds}
        if r.status != "green":   # why it is not green travels with it (worldcore field test W23)
            result.update({"exit": r.exit, "tail": r.tail})
            if prepared.startswith("skipped"):   # a tree whose dependencies went missing reads as red tests
                result["summary"] = (f"{result['summary']} ({prepared}; `flotilla receipt run --setup` runs it "
                                     "again)").strip()
        else:
            ran_green[r.name] = r.seconds
            ran_peaks[r.name] = r.peak_mb
            learned[_tier_key(tier)] = {"sha": sha, "summary": r.summary or "", "seconds": r.seconds,
                                        "at": now_iso()}
        results.append(result)
    # what ran was these files only if the tree still is them: a tree edited or moved during the run gets no receipt,
    # and its greens are not kept for other commits to reuse (review of 0.6.2, I1)
    if gitq.resolve(tree, "HEAD", run=run) != sha or gitq.is_clean(tree, run=run) is not True:
        raise ReceiptRefused(f"{tree} changed while its tiers ran (a commit, or uncommitted changes); what ran is "
                             "not what is committed, so there is no receipt - run it again on a tree nobody edits")
    receipt = {"sha": sha, "purpose": purpose, "at": now_iso(), "tiers_fingerprint": tiers_fingerprint(tiers),
               "tiers": results}
    if files and learned:
        known_path = _files_path(state, repo_key, files)
        known_path.parent.mkdir(parents=True, exist_ok=True)
        known_path.write_text(json.dumps({**_green_over(state, repo_key, files), **learned}, indent=2,
                                         sort_keys=True), encoding="utf-8")
    measure_once(state, repo_key, ran_green)   # only tiers that ran here: a reused green measured nothing (M5)
    measure_peaks(state, repo_key, ran_peaks)
    path = _path(state, repo_key, sha, purpose)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    return receipt


def check_receipt(*, state: Path, repo_key: str, sha: str, purpose: str, profile: dict) -> tuple[bool, str]:
    tiers = tiers_for(profile, purpose)
    if not tiers:
        return True, f"no {purpose} tiers configured"
    try:
        receipt = json.loads(_path(state, repo_key, sha, purpose).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, (f"no {purpose} receipt over {sha[:7]}; run `flotilla receipt run --purpose {purpose}` "
                       "in the branch's tree")
    except (ValueError, OSError):
        return False, f"the {purpose} receipt over {sha[:7]} cannot be read; run it again"
    if not isinstance(receipt, dict) or receipt.get("sha") != sha or receipt.get("purpose") != purpose:
        return False, (f"the {purpose} receipt file over {sha[:7]} was written for another revision or purpose; "
                       "run it again")
    if receipt.get("tiers_fingerprint") != tiers_fingerprint(tiers):
        return False, f"the {purpose} tiers changed since the receipt over {sha[:7]}; run it again"
    ran = receipt.get("tiers")
    if not isinstance(ran, list) or len(ran) != len(tiers):
        return False, f"the {purpose} receipt over {sha[:7]} does not list every tier; run it again"
    red = sorted({tier.get("name", "?") for tier in ran if tier.get("status") != "green"})
    if red:
        return False, f"the {purpose} receipt over {sha[:7]} is not green: {', '.join(red)}"
    return True, f"{purpose} receipt green over {sha[:7]}"
