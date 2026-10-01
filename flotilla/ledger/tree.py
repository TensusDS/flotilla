"""Cut a worktree from trunk and claim it in one step (spec, section 6.1).

The claim is filed by the act a session performs anyway, because a voluntary journal decays. The check, the
`worktree add` and the claim run inside one hold of the ledger's lock, and a failure after git has created anything
removes what it created, so a refusal never leaves an orphan tree or branch behind. A fix row filed by
`broke` is picked up by its owner's cut instead of refused.

`switch` is the fleet's form of the same claim. A spawned session may edit only its home tree (the one directory
it was launched with), so it takes each task there: the home tree moves to a new branch from trunk and the claim is
filed in the same step. Switching back to one's own open branch (to answer a returned verdict) files nothing new.
The home tree must be clean, because a switch would carry uncommitted work onto another branch.
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
        requires=(), after=(), also: str = "") -> Row:
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
        filed = s.open_row(branch)
        if filed is not None and filed.fixes and not filed.tree and filed.owner == actor.name:
            if ref or requires or after or also:
                raise MoveRefused(f"`{branch}` was filed by `broke` for {filed.fixes}; cut it without --ref, "
                                  "--requires, --after or --also")
            ids, later = [], []
        else:
            filed = None
            ids = core.check_claim(s.rows, branch, ref=ref, also=also, requires=requires)
            later = core.link_ids(s.rows, after, "--after")
        if tree.exists() or tree.is_symlink():   # checked again under the lock: another session may have just made it
            raise MoveRefused(f"{tree} already exists; nothing was cut")
        done = ledger.run(["git", "-C", str(ledger.root), "worktree", "add", "-q", "-b", branch, str(tree),
                           base_ref], capture_output=True, text=True, check=False)
        if done.returncode != 0:   # no tree of ours stands there: never remove one, it may be another session's (F17)
            # the branch did not exist under this lock a moment ago, so a branch by its name now is the failed add's
            ledger.run(["git", "-C", str(ledger.root), "branch", "-D", branch], capture_output=True, text=True,
                       check=False)
            raise MoveRefused(f"git worktree add failed: {done.stderr.strip()}; nothing was cut")
        try:
            base = gitq.resolve(ledger.root, base_ref, run=ledger.run) or ""
            if filed is not None:
                return s.append(actor, filed.id, "claim", "claimed",
                                fields={"tree": str(tree.resolve()), "base": base}, evidence={"picked_up": filed.fixes})
            return core.append_claim(s, actor, branch, tree=str(tree.resolve()), base=base, ref=ref, ids=ids,
                                     also=also, after=later)
        except BaseException:
            _undo(ledger, branch, tree)
            raise


def _undo(ledger: core.Ledger, branch: str, tree: Path) -> None:
    git = ["git", "-C", str(ledger.root)]
    if tree.exists():
        ledger.run([*git, "worktree", "remove", "--force", str(tree)], capture_output=True, text=True, check=False)
    ledger.run([*git, "worktree", "prune"], capture_output=True, text=True, check=False)
    ledger.run([*git, "branch", "-D", branch], capture_output=True, text=True, check=False)


def switch(ledger: core.Ledger, actor: Actor, branch: str, *, ref: str = "", requires=(), after=(),
           also: str = "") -> Row:
    require_may(actor, "claim", ledger.posts)
    home_row = next((row for row in ledger.rows().values()
                     if row.is_open and row.state == "reserved" and row.owner == actor.name and row.tree), None)
    if home_row is None:
        raise MoveRefused(f"{actor.name} has no home tree (no post row); `flotilla tree cut` claims in a new tree")
    home = Path(home_row.tree)
    clean = gitq.is_clean(home, run=ledger.run)
    if clean is not True:
        raise MoveRefused(f"{home} has uncommitted work, or git could not say; commit it on its branch first")
    if gitq.resolve(ledger.root, f"refs/remotes/origin/{ledger.trunk}", run=ledger.run):
        ledger.run(["git", "-C", str(ledger.root), "fetch", "--quiet", "origin", ledger.trunk],
                   capture_output=True, text=True, check=False)
    base_ref = gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
    git = ["git", "-C", str(home)]
    with ledger.session() as s:
        mine = s.open_row(branch)
        if mine is not None and mine.owner == actor.name:
            missing = gitq.branch_tip(ledger.root, branch, run=ledger.run) is None
            if mine.state != "claimed":   # switching trees never moves work back to a claim
                if missing:
                    raise MoveRefused(f"`{branch}` is {mine.state} and its branch is gone here; tree switch cuts a "
                                      "branch only for a claimed row")
                done = ledger.run([*git, "switch", "-q", branch], capture_output=True, text=True, check=False)
                if done.returncode != 0:
                    raise MoveRefused(f"git switch {branch} failed in {home}: {done.stderr.strip()}")
                return mine
            if missing:
                # a row filed before its branch (a judge's fix row): cut the branch from trunk here (G6)
                base = gitq.resolve(ledger.root, base_ref, run=ledger.run) or ""
                done = ledger.run([*git, "switch", "-q", "-c", branch, base_ref], capture_output=True, text=True,
                                  check=False)
                if done.returncode != 0:
                    raise MoveRefused(f"git switch -c {branch} failed in {home}: {done.stderr.strip()}")
                return s.append(actor, mine.id, "claim", "claimed", fields={"tree": str(home.resolve()),
                                                                           "base": base})
            done = ledger.run([*git, "switch", "-q", branch], capture_output=True, text=True, check=False)
            if done.returncode != 0:
                raise MoveRefused(f"git switch {branch} failed in {home}: {done.stderr.strip()}")
            if mine.tree != str(home.resolve()):
                return s.append(actor, mine.id, "claim", "claimed", fields={"tree": str(home.resolve())})
            return mine
        ids = core.check_claim(s.rows, branch, ref=ref, also=also, requires=requires)
        later = core.link_ids(s.rows, after, "--after")
        if gitq.branch_tip(ledger.root, branch, run=ledger.run):
            raise MoveRefused(f"branch `{branch}` already exists and holds no open row of yours; nothing was switched")
        base = gitq.resolve(ledger.root, base_ref, run=ledger.run) or ""
        done = ledger.run([*git, "switch", "-q", "-c", branch, base_ref], capture_output=True, text=True,
                          check=False)
        if done.returncode != 0:
            made = gitq.branch_tip(ledger.root, branch, run=ledger.run)
            if made and made == base:
                ledger.run([*git, "branch", "-D", branch], capture_output=True, text=True, check=False)
            raise MoveRefused(f"git switch -c {branch} failed in {home}: {done.stderr.strip()}")
        try:
            return core.append_claim(s, actor, branch, tree=str(home.resolve()), base=base, ref=ref, ids=ids,
                                     also=also, after=later)
        except BaseException:
            ledger.run([*git, "switch", "-q", home_row.branch], capture_output=True, text=True, check=False)
            ledger.run([*git, "branch", "-D", branch], capture_output=True, text=True, check=False)
            raise
