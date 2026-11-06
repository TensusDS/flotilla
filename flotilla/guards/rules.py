"""Which profile the guards obey: the one on trunk, or this tree's before onboarding has reached trunk.

Trunk's rules are the ones that count (an uncommitted edit in a branch must not switch off the guard that judges
it). Until the onboarding commit reaches trunk there are no rules there at all, and the push that brings them would
be judged by nothing, so the tree's own profile is obeyed and the note says so.

This reads trunk's name from the tree and trunk itself through a local ref, both of which a session can change, so
the push guard and the pre-push hook do not take their rules from here: they read `push.origin_trunk` and
`push.rules_at`, which ask origin (scan of 0.7.0, F1). The guards that stay here keep a session from damaging its
own tree. No client-side guard locks a repository against a session set on getting around it - README, "Security
model and its limits", says what does.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _trunk_without_the_tree(found: Path, err: Exception, run) -> tuple[dict, str]:
    from flotilla.ledger.commands import trunk_rules
    default = run(["git", "-C", str(found), "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD"],
                  capture_output=True, text=True, check=False).stdout.strip()
    trunk = default[len("origin/"):] if default.startswith("origin/") else ""
    named = f"origin's default branch `{trunk}`" if trunk else "`main`, assumed since origin names no default branch"
    rules = trunk_rules(found, trunk=trunk or "main")
    return rules.profile, (f"this tree's .flotilla/project.toml does not parse ({err}); the guards follow the rules "
                           f"of trunk, {named}, until it is fixed")


def rules_for(root, run=subprocess.run) -> tuple[dict, str]:
    from flotilla.core import config
    from flotilla.ledger import gitq
    from flotilla.ledger.commands import trunk_rules
    found = config.find_project(Path(root))
    if found is None:
        raise config.ConfigError(f"not onboarded: no .flotilla/project.toml at or above {Path(root).resolve()}")
    try:
        local = config.load_project(found)
    except config.ConfigError as err:
        # a profile a session broke in its own tree must not switch off the guards that judge that tree: trunk's
        # rules still stand, read without it (gamma, 2026-11-02)
        return _trunk_without_the_tree(found, err, run)
    trunk = (local.data.get("trunk") or {}).get("branch", "main")
    ref = gitq.trunk_ref(found, trunk, run=run)
    carried = run(["git", "-C", str(found), "cat-file", "-e", f"{ref}:.flotilla/project.toml"],
                  capture_output=True, check=False)
    if carried.returncode != 0:
        return local.data, f"`{ref}` carries no profile yet, so this tree's .flotilla/project.toml is obeyed"
    return trunk_rules(found).profile, ""
