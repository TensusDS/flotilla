"""`flotilla work …`, `flotilla tree cut` and `flotilla receipt …`.

The rules a move is checked against (the profile and the posts) are read from trunk, not from the caller's tree:
an uncommitted edit in a branch must not drop a handover tier or widen a post for the branch that made it. The
event records the trunk revision the rules came from.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.ledger import core, gitq, handover, reading, receipts
from flotilla.ledger import tree as tree_mod
from flotilla.ledger.actor import resolve_actor
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import LedgerVersionError, Row
from flotilla.posts import PostError, load_posts


def _project(root: Path) -> config.Project:
    found = config.find_project(root)
    if found is None:
        raise config.ConfigError(f"not onboarded: no .flotilla/project.toml at or above {Path(root).resolve()}; "
                                 "run /flotilla:onboard")
    return config.load_project(found)


def _show_file(root: Path, sha: str, path: str) -> str:
    done = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{path}"], capture_output=True, text=True,
                          check=False)
    if done.returncode != 0:
        raise config.ConfigError(f"git could not read {path} at {sha[:7]}: {done.stderr.strip()}")
    return done.stdout


def trunk_rules(root: Path) -> tuple[dict, dict, str]:
    """The profile and posts as trunk carries them, and the label `<ref>@<sha>` they were read at."""
    local = _project(root)
    trunk = (local.data.get("trunk") or {}).get("branch", "main")
    ref = gitq.trunk_ref(local.root, trunk)
    sha = gitq.resolve(local.root, f"{ref}^{{commit}}")
    if sha is None:
        raise config.ConfigError(f"git could not resolve trunk `{ref}`; the ledger reads its rules from trunk")
    listing = subprocess.run(["git", "-C", str(local.root), "ls-tree", "-r", sha, "--", ".flotilla/"],
                             capture_output=True, text=True, check=False)
    entries = {}
    for line in listing.stdout.splitlines():
        meta, _, path = line.partition("\t")
        entries[path] = meta.split()[0]
    if ".flotilla/project.toml" not in entries:
        raise config.ConfigError(f"`{ref}` carries no .flotilla/project.toml; the ledger reads its rules from "
                                 "trunk, so commit the onboarding and bring it to trunk first")
    with tempfile.TemporaryDirectory(prefix="flotilla-rules-") as tmp:
        for path, mode in entries.items():
            if path != ".flotilla/project.toml" and not (path.startswith(".flotilla/posts/") and path.endswith(".md")):
                continue
            if mode == "120000":
                raise config.ConfigError(f"{path} is a symlink on `{ref}`; rules are read only from real files")
            target = Path(tmp) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_show_file(local.root, sha, path), encoding="utf-8")
        try:
            profile = config.load_project(Path(tmp)).data
            posts = load_posts(Path(tmp))
        except (config.ConfigError, PostError) as err:
            raise type(err)(str(err).replace(f"{tmp}/", f"{ref}:")) from err
    if (profile.get("trunk") or {}).get("branch", "main") != trunk:
        raise config.ConfigError(f"this tree names trunk `{trunk}`, but `{ref}` names another; the rules on trunk "
                                 "are the ones that count")
    return profile, posts, f"{ref}@{sha[:12]}"


def open_ledger(root: Path) -> core.Ledger:
    profile, posts, rules = trunk_rules(Path(root))
    ident = repo.identify(Path(root))
    state = paths.state_dir()
    return core.Ledger(store=LocalLogStore(state / "ledger"), root=ident.root, repo_key=ident.key,
                       profile=profile, posts=posts, state_dir=state, rules=rules)


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
    if row.why:
        print(f"  last return: {row.why}")
    print("history:")
    for entry in row.history:
        called = entry.get("caller") or ""
        also = f" (called by {called})" if called and called != entry["by"] else ""
        print(f"  {entry['move']:<8} {entry['state']:<9} {entry['by']}{also}  {entry['at']}")
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
    profile, _, _ = trunk_rules(tree)
    ident = repo.identify(tree)
    state = paths.state_dir()
    if args.action == "run":
        result = receipts.run_receipt(ident.root, state=state, repo_key=ident.key, purpose=args.purpose,
                                      profile=profile, timeout=args.timeout)
        for tier in result["tiers"]:
            print(f"{tier['status']:<9} {tier['name']}: {tier['summary']}")
        if not result["tiers"]:
            print(f"no {args.purpose} tiers configured; nothing to run")
        print(f"{args.purpose} receipt over {result['sha'][:7]}")
        return 0 if all(tier["status"] == "green" for tier in result["tiers"]) else 1
    sha = gitq.resolve(ident.root, args.rev)
    if sha is None:
        print(f"git could not resolve `{args.rev}`")
        return 2
    for purpose in receipts.PURPOSES:
        ok, why = receipts.check_receipt(state=state, repo_key=ident.key, sha=sha, purpose=purpose,
                                         profile=profile)
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
    except (MoveRefused, PostError, receipts.ReceiptRefused, config.ConfigError, repo.NotARepository,
            StorageCorrupt, LedgerVersionError) as err:
        print(f"refused: {err}")
        return 2
    print(summary(row))
    for other in (row.history[-1].get("evidence") or {}).get("stacked_on") or []:
        print(f"note: stacked on `{other}`, which is not accepted yet")
    return 0
