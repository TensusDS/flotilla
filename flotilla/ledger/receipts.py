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
from flotilla.onboard.firstrun import run_tier

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


def run_receipt(tree: Path, *, state: Path, repo_key: str, purpose: str, profile: dict, timeout: float,
                run=subprocess.run) -> dict:
    if purpose not in PURPOSES:
        raise ReceiptRefused(f"unknown purpose `{purpose}`; one of {', '.join(PURPOSES)}")
    sha = gitq.resolve(tree, "HEAD", run=run)
    if sha is None:
        raise ReceiptRefused(f"{tree}: git could not resolve HEAD")
    if gitq.is_clean(tree, run=run) is not True:
        raise ReceiptRefused(f"{tree} has uncommitted changes, or git could not say; a receipt describes one "
                             "revision, so commit first")
    tiers = tiers_for(profile, purpose)
    runs = [run_tier(tier["name"], tier["command"], Path(tree), timeout=timeout) for tier in tiers]
    receipt = {"sha": sha, "purpose": purpose, "at": now_iso(), "tiers_fingerprint": tiers_fingerprint(tiers),
               "tiers": {r.name: {"status": r.status, "summary": r.summary or "", "seconds": r.seconds}
                         for r in runs}}
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
    if receipt.get("tiers_fingerprint") != tiers_fingerprint(tiers):
        return False, f"the {purpose} tiers changed since the receipt over {sha[:7]}; run it again"
    red = sorted(name for name, tier in (receipt.get("tiers") or {}).items() if tier.get("status") != "green")
    if red:
        return False, f"the {purpose} receipt over {sha[:7]} is not green: {', '.join(red)}"
    return True, f"{purpose} receipt green over {sha[:7]}"
