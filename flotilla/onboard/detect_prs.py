"""Open pull requests made outside the ledger, said before the profile is published (a new project's onboarding,
2026-10-19). Once the profile is on trunk the push guard asks every `gh pr merge` for a green receipt over the head,
and where a person authorizes merges for approved ledger rows such a pull request does not have; `offledger` records
work that already landed, not work still waiting to. Warned, never refused: they are the person's to settle."""

from __future__ import annotations

import json
import subprocess

from flotilla.core.text import visible
from flotilla.onboard.detect_ci import github_slug

LIMIT = 100
SHOWN = 5


def open_pull_requests(normalized_origin: str | None, run=subprocess.run) -> dict:
    """{"source": "none"} off GitHub; {"source": "unknown", "error"} where gh could not answer - which is not
    "none open"; {"source": "github", "open": [{number, branch, title}]} otherwise."""
    slug = github_slug(normalized_origin)
    if not slug:
        return {"source": "none"}
    argv = ["gh", "pr", "list", "-R", slug, "--state", "open", "--limit", str(LIMIT),
            "--json", "number,headRefName,title"]
    try:
        done = run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"source": "unknown", "error": str(err)}
    if done.returncode != 0:
        return {"source": "unknown", "error": (done.stderr or done.stdout or "").strip()[:300]}
    try:
        listed = json.loads(done.stdout or "[]")
        found = [{"number": int(item["number"]), "branch": str(item["headRefName"]), "title": str(item["title"])}
                 for item in listed]
    except (ValueError, KeyError, TypeError) as err:
        return {"source": "unknown", "error": f"gh's answer could not be read: {err}"}
    return {"source": "github", "open": found}


def outside_lines(pulls: dict, profile: dict, *, known: set[str]) -> list[str]:
    """What to say about open pull requests no ledger row knows, or nothing where the push guard is off - then
    nothing refuses their merge."""
    if not (profile.get("guards") or {}).get("push_receipt") or (profile.get("flow") or {}).get("mode") == "local":
        return []
    if pulls.get("source") == "unknown":
        return [f"open pull requests could not be listed ({visible(pulls.get('error') or 'no answer')}); look at them "
                "yourself before publishing - once the profile is on trunk, merging one needs a green push receipt"]
    outside = [pull for pull in pulls.get("open") or [] if pull["branch"] not in known]
    if not outside:
        return []
    count = len(outside)
    many = "at least " if len(pulls.get("open") or []) >= LIMIT else ""
    named = "; ".join(f"#{pull['number']} `{visible(pull['branch'])}` ({visible(pull['title'])})"
                      for pull in outside[:SHOWN])
    more = f"; and {count - SHOWN} more" if count > SHOWN else ""
    approval = (" and your approval of its ledger row, which these do not have"
                if (profile.get("flow") or {}).get("merge_authorized_by") == "human" else "")
    branch = visible(outside[0]["branch"])
    return [f"{many}{count} open pull request{'s' if count != 1 else ''} made outside the ledger: {named}{more}",
            f"once the profile is on trunk, `gh pr merge` from a session needs a green push receipt over the head"
            f"{approval}: merge them before publishing, or a builder takes one in (`git fetch origin {branch}`, then "
            f"`flotilla work claim {branch}`), or you merge it yourself with `!`"]
