"""`flotilla work …`, `flotilla tree cut` and `flotilla receipt …`."""

from __future__ import annotations

from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core.storage import LocalLogStore
from flotilla.ledger import core, gitq, handover, reading, receipts
from flotilla.ledger import tree as tree_mod
from flotilla.ledger.actor import resolve_actor
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row
from flotilla.posts import PostError, load_posts


def _project(root: Path) -> config.Project:
    found = config.find_project(root)
    if found is None:
        raise config.ConfigError(f"not onboarded: no .flotilla/project.toml at or above {Path(root).resolve()}; "
                                 "run /flotilla:onboard")
    return config.load_project(found)


def open_ledger(root: Path) -> core.Ledger:
    project = _project(root)
    ident = repo.identify(Path(root))
    state = paths.state_dir()
    return core.Ledger(store=LocalLogStore(state / "ledger"), root=ident.root, repo_key=ident.key,
                       profile=project.data, posts=load_posts(project.root), state_dir=state)


def summary(row: Row) -> str:
    reader = f", reader {row.reader}{' (reading)' if row.taken else ''}" if row.reader else ""
    return f"{row.id} {row.branch}: {row.state} (owner {row.owner}{reader})"


def _show(ledger: core.Ledger, branch: str) -> int:
    matches = [row for row in ledger.rows().values() if row.branch == branch]
    if not matches:
        print(f"no ledger row for `{branch}`")
        return 2
    row = matches[-1]
    print(summary(row))
    print(f"  base {row.base or 'unknown'} | tip {row.tip or '-'} | verdict {row.verdict or '-'} | ref {row.ref or '-'}")
    if row.waiting_on:
        print(f"  waiting on {row.waiting_on}: {row.note}")
    print("history:")
    for entry in row.history:
        print(f"  {entry['move']:<8} {entry['state']:<9} {entry['by']}  {entry['at']}")
    return 0


MOVES = {
    "claim": lambda l, a, x: core.claim(l, a, x.branch, tree=x.tree, ref=x.ref, requires=x.requires, also=x.also),
    "reserve": lambda l, a, x: core.reserve(l, a, x.branch, tree=x.tree),
    "release": lambda l, a, x: core.release(l, a, x.branch, why=x.why),
    "hand": lambda l, a, x: handover.hand(l, a, x.branch, tip=x.tip),
    "moved": lambda l, a, x: handover.moved(l, a, x.branch, tip=x.tip, agreed_by=x.agreed_by),
    "wait": lambda l, a, x: handover.wait(l, a, x.branch, on=x.on, why=x.why, clear=x.clear),
    "take": lambda l, a, x: reading.take(l, a, x.branch),
    "recuse": lambda l, a, x: reading.recuse(l, a, x.branch),
    "fix": lambda l, a, x: reading.fix(l, a, x.branch, why=x.why),
    "assign": lambda l, a, x: reading.assign(l, a, x.branch, reader=x.reader),
    "accept": lambda l, a, x: reading.accept(l, a, x.branch, reviewed=x.reviewed),
}


def _receipt(args) -> int:
    tree = Path(args.tree)
    project = _project(tree)
    ident = repo.identify(tree)
    state = paths.state_dir()
    if args.action == "run":
        result = receipts.run_receipt(ident.root, state=state, repo_key=ident.key, purpose=args.purpose,
                                      profile=project.data, timeout=args.timeout)
        for name, tier in result["tiers"].items():
            print(f"{tier['status']:<9} {name}: {tier['summary']}")
        if not result["tiers"]:
            print(f"no {args.purpose} tiers configured; nothing to run")
        print(f"{args.purpose} receipt over {result['sha'][:7]}")
        return 0 if all(tier["status"] == "green" for tier in result["tiers"].values()) else 1
    sha = gitq.resolve(ident.root, args.rev)
    if sha is None:
        print(f"git could not resolve `{args.rev}`")
        return 2
    for purpose in receipts.PURPOSES:
        ok, why = receipts.check_receipt(state=state, repo_key=ident.key, sha=sha, purpose=purpose,
                                         profile=project.data)
        print(f"{'ok' if ok else 'no':<3} {why}")
    return 0


def run_ledger_command(args) -> int:
    try:
        if args.command == "receipt":
            return _receipt(args)
        ledger = open_ledger(Path(args.root))
        if args.command == "work" and args.move == "show":
            return _show(ledger, args.branch)
        caller = resolve_actor(ledger.posts, as_name=args.as_name)
        if args.command == "tree":
            row = tree_mod.cut(ledger, caller, args.branch, Path(args.tree), expect=args.expect, ref=args.ref,
                               requires=args.requires, also=args.also)
        else:
            result = MOVES[args.move](ledger, caller, args)
            if isinstance(result, tuple):
                row, text = result
                print(summary(row))
                print(text)
                return 0
            row = result
    except (MoveRefused, PostError, receipts.ReceiptRefused, config.ConfigError, repo.NotARepository) as err:
        print(f"refused: {err}")
        return 2
    print(summary(row))
    for other in (row.history[-1].get("evidence") or {}).get("stacked_on") or []:
        print(f"note: stacked on `{other}`, which is not accepted yet")
    return 0
