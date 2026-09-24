"""Cut a worktree from trunk and claim it in one step (spec, section 6.1).

The claim is filed by the act a session performs anyway, because a voluntary journal decays. Every check that can
refuse runs before anything is created on disk, so a refusal never leaves an orphan tree or branch behind.
"""

from __future__ import annotations

from pathlib import Path

from flotilla.core import repo
from flotilla.ledger import core, gitq
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row


def _names(ledger: core.Ledger) -> set[str]:
    ident = repo.identify(ledger.root, run=ledger.run)
    tail = (ident.origin or "").rstrip("/").split("/")[-1].split(":")[-1]
    if tail.endswith(".git"):
        tail = tail[:-4]
    return {ident.root.name, tail} - {""}


def cut(ledger: core.Ledger, actor: Actor, branch: str, tree: Path, *, expect: str | None = None, ref: str = "",
        requires=(), also: str = "") -> Row:
    require_may(actor, "claim", ledger.posts)
    tree = Path(tree)
    if tree.exists() or tree.is_symlink():
        raise MoveRefused(f"{tree} already exists; nothing was cut")
    if expect and expect not in _names(ledger):
        raise MoveRefused(f"--expect {expect}: this repository is {', '.join(sorted(_names(ledger)))}; nothing was cut")
    if gitq.branch_tip(ledger.root, branch, run=ledger.run):
        raise MoveRefused(f"branch `{branch}` already exists; nothing was cut")
    core.check_claim(ledger.rows(), branch, ref=ref, also=also, requires=requires)
    if gitq.resolve(ledger.root, f"refs/remotes/origin/{ledger.trunk}", run=ledger.run):
        ledger.run(["git", "-C", str(ledger.root), "fetch", "--quiet", "origin", ledger.trunk],
                   capture_output=True, text=True, check=False)
    base_ref = gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
    done = ledger.run(["git", "-C", str(ledger.root), "worktree", "add", "-q", "-b", branch, str(tree), base_ref],
                      capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise MoveRefused(f"git worktree add failed: {done.stderr.strip()}")
    base = gitq.resolve(ledger.root, base_ref, run=ledger.run) or ""
    return core.claim(ledger, actor, branch, tree=str(tree.resolve()), ref=ref, requires=requires, also=also,
                      base=base)
