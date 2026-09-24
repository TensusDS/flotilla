"""Cut a worktree from trunk and claim it in one step (spec, section 6.1).

The claim is filed by the act a session performs anyway, because a voluntary journal decays. The check, the
`worktree add` and the claim run inside one hold of the ledger's lock, and a failure after git has created anything
removes what it created, so a refusal never leaves an orphan tree or branch behind.
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
    if gitq.resolve(ledger.root, f"refs/remotes/origin/{ledger.trunk}", run=ledger.run):
        ledger.run(["git", "-C", str(ledger.root), "fetch", "--quiet", "origin", ledger.trunk],
                   capture_output=True, text=True, check=False)
    base_ref = gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
    with ledger.session() as s:
        if gitq.branch_tip(ledger.root, branch, run=ledger.run):
            raise MoveRefused(f"branch `{branch}` already exists; nothing was cut")
        ids = core.check_claim(s.rows, branch, ref=ref, also=also, requires=requires)
        done = ledger.run(["git", "-C", str(ledger.root), "worktree", "add", "-q", "-b", branch, str(tree),
                           base_ref], capture_output=True, text=True, check=False)
        try:
            if done.returncode != 0:
                raise MoveRefused(f"git worktree add failed: {done.stderr.strip()}; nothing was cut")
            base = gitq.resolve(ledger.root, base_ref, run=ledger.run) or ""
            return core.append_claim(s, actor, branch, tree=str(tree.resolve()), base=base, ref=ref, ids=ids,
                                     also=also)
        except BaseException:
            _undo(ledger, branch, tree)
            raise


def _undo(ledger: core.Ledger, branch: str, tree: Path) -> None:
    git = ["git", "-C", str(ledger.root)]
    if tree.exists():
        ledger.run([*git, "worktree", "remove", "--force", str(tree)], capture_output=True, text=True, check=False)
    ledger.run([*git, "worktree", "prune"], capture_output=True, text=True, check=False)
    ledger.run([*git, "branch", "-D", branch], capture_output=True, text=True, check=False)
