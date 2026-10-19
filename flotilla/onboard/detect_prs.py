"""Open pull requests made outside the ledger, said around the profile's publishing (a new project's onboarding,
2026-10-19). Once the profile is on trunk the push guard asks every `gh pr merge` from a session for a green receipt
over the head, and where a person authorizes merges for an approved ledger row such a pull request does not have;
`offledger` records work that already landed, not work still waiting to. Warned, never refused: they are the
person's to settle. Asked only by `write` and `publish`, not on every onboarding command."""

from __future__ import annotations

import json
import subprocess

from flotilla.core.text import visible
from flotilla.ledger.core import SAFE_BRANCH
from flotilla.onboard.detect_ci import github_slug

LIMIT = 100
SHOWN = 5
TITLE = 60
TIMEOUT = 15


def open_pull_requests(normalized_origin: str | None, run=subprocess.run) -> dict:
    """{"source": "none"} off GitHub; {"source": "unknown", "error"} where gh could not answer - which is not
    "none open"; {"source": "github", "open": [{number, branch, title, fork}]} otherwise."""
    slug = github_slug(normalized_origin)
    if not slug:
        return {"source": "none"}
    argv = ["gh", "pr", "list", "-R", slug, "--state", "open", "--limit", str(LIMIT),
            "--json", "number,headRefName,title,isCrossRepository"]
    try:
        done = run(argv, capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as err:
        return {"source": "unknown", "error": str(err)}
    if done.returncode != 0:
        return {"source": "unknown", "error": (done.stderr or done.stdout or "").strip()[:300]}
    try:
        listed = json.loads(done.stdout or "[]")
        found = [{"number": int(item["number"]), "branch": str(item["headRefName"]), "title": str(item["title"]),
                  "fork": bool(item.get("isCrossRepository"))} for item in listed]
    except (ValueError, KeyError, TypeError) as err:
        return {"source": "unknown", "error": f"gh's answer could not be read: {err}"}
    return {"source": "github", "open": found}


def _shown(text: str) -> str:
    """Author-written text, made one safe line and short; a backquote would break the code span it sits in."""
    text = visible(text).replace("`", "'")
    return text if len(text) <= TITLE else text[:TITLE - 1] + "…"


def applies(profile: dict) -> bool:
    """Whether a pull request made by hand changes anything here: the push guard is on, the flow pushes, and a
    session - not the person - merges pull requests."""
    if not (profile.get("guards") or {}).get("push_receipt"):
        return False
    if (profile.get("flow") or {}).get("mode", "local") == "local":
        return False
    return (profile.get("pr") or {}).get("merged_by") != "human"


def outside_lines(pulls: dict, profile: dict, *, known: set[str], published: bool = False) -> list[str]:
    """What to say about open pull requests no open ledger row knows - before the yes, or (`published`) once the
    profile is on trunk - or nothing where no session could merge them anyway or nothing would refuse it."""
    flow = profile.get("flow") or {}
    if not applies(profile):
        return []
    if pulls.get("source") == "unknown":
        return [f"open pull requests could not be listed ({_shown(pulls.get('error') or 'no answer')}); look at them "
                "yourself - once the profile is on trunk, a session merges one only with a green push receipt"]
    outside = [pull for pull in pulls.get("open") or [] if pull.get("fork") or pull["branch"] not in known]
    if not outside:
        return []
    count = len(outside)
    many = "at least " if len(pulls.get("open") or []) >= LIMIT else ""
    named = "; ".join(f"#{pull['number']} `{_shown(pull['branch'])}`{' from a fork' if pull.get('fork') else ''}"
                      f" ({_shown(pull['title'])})" for pull in outside[:SHOWN])
    more = f"; and {count - SHOWN} more" if count > SHOWN else ""
    trunk = (profile.get("trunk") or {}).get("branch", "main")
    # a fork's branch is named in the fork, often `main`: fetched from origin it is trunk, not the pull request
    takeable = next((pull for pull in outside if not pull.get("fork") and pull["branch"] != trunk
                     and SAFE_BRANCH.fullmatch(pull["branch"])), None)
    if takeable:
        takes = f"(`git fetch origin {takeable['branch']}`, then `flotilla work claim {takeable['branch']}`)"
    else:
        takes = "(no command is offered: these come from forks, or their branch names are not plain)"
    approval = (" and your approval of a ledger row, which these do not have"
                if flow.get("merge_authorized_by") == "human" else "")
    when = "now that the profile is on trunk" if published else "once the profile is on trunk"
    first = "" if published else "merge them before publishing, or "
    return [f"{many}{count} open pull request{'s' if count != 1 else ''} made outside the ledger (titles as their "
            f"authors wrote them): {named}{more}",
            f"{when}, a session's `gh pr merge` needs a green push receipt over the head{approval}: {first}a builder "
            f"takes one in {takes}, or you merge it yourself with `!`"]
