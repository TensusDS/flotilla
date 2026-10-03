"""Which profile the guards obey: the one on trunk, or this tree's before onboarding has reached trunk.

Trunk's rules are the ones that count (an uncommitted edit in a branch must not switch off the guard that judges
it). Until the onboarding commit reaches trunk there are no rules there at all, and the push that brings them would
be judged by nothing, so the tree's own profile is obeyed and the note says so.

This reads trunk's name from the tree and trunk itself through a local ref, both of which a session can change, so
nothing that decides a push uses it: the push guard, the pre-push hook and the person's approval read
`push.origin_rules`, which asks origin (scan of 0.7.0, F1). The guards that stay here keep a session from damaging
its own tree; README, "Known limitations", says what that leaves open.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def rules_for(root, run=subprocess.run) -> tuple[dict, str]:
    from flotilla.core import config
    from flotilla.ledger import gitq
    from flotilla.ledger.commands import trunk_rules
    found = config.find_project(Path(root))
    if found is None:
        raise config.ConfigError(f"not onboarded: no .flotilla/project.toml at or above {Path(root).resolve()}")
    local = config.load_project(found)
    trunk = (local.data.get("trunk") or {}).get("branch", "main")
    ref = gitq.trunk_ref(found, trunk, run=run)
    carried = run(["git", "-C", str(found), "cat-file", "-e", f"{ref}:.flotilla/project.toml"],
                  capture_output=True, check=False)
    if carried.returncode != 0:
        return local.data, f"`{ref}` carries no profile yet, so this tree's .flotilla/project.toml is obeyed"
    return trunk_rules(found).profile, ""
