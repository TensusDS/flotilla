"""One walk over the repository: the items organize asks about, the lines it only lists, and notes (spec, sections 3
and 9).

Before the profile is on origin's trunk the ledger takes no moves, so the walk holds what must be settled before
publishing - tool caches, the local trunk's own commits, onboarding's branch beside its profile commit, the
organizing tree's edits. After, it holds the rest: pull requests, chains, native stacks, branches, and edits in other
trees. Nothing is judged from stale refs: it fetches first. What GitHub could not answer is a note, never an empty
answer.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from flotilla.ledger import gitq
from flotilla.ledger.core import SAFE_BRANCH
from flotilla.organize import chains as chaining
from flotilla.organize import github, local, whose
from flotilla.organize.items import Item, shown

ASK = ""   # `slug` default: ask git for origin's GitHub name


@dataclass
class Walk:
    phase: str
    items: list[Item] = field(default_factory=list)
    listed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    trunk: str = "main"


def _git(root: Path, *args: str, run) -> str | None:
    done = run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return done.stdout if done.returncode == 0 else None


def _subject(root: Path, sha: str, run) -> str:
    return shown((_git(root, "log", "-1", "--format=%s", sha, run=run) or "").strip())


def _count(root: Path, base: str, tip: str, run) -> int:
    said = (_git(root, "rev-list", "--count", f"{base}..{tip}", run=run) or "0").strip()
    return int(said) if said.isdigit() else 0


def phase_of(root: Path, trunk: str, run) -> str:
    published = run(["git", "-C", str(root), "cat-file", "-e", f"refs/remotes/origin/{trunk}:.flotilla/project.toml"],
                    capture_output=True, text=True, check=False)
    return "after" if published.returncode == 0 else "before"


def checked_out(root: Path, run) -> dict[str, str]:
    """{branch: tree path} for every worktree that has a branch checked out."""
    found, tree = {}, ""
    for line in (_git(root, "worktree", "list", "--porcelain", run=run) or "").splitlines():
        if line.startswith("worktree "):
            tree = line[len("worktree "):]
        elif line.startswith("branch refs/heads/"):
            found[line[len("branch refs/heads/"):]] = tree
    return found


def _wanted(item: Item, only: tuple) -> bool:
    if not only:
        return True
    names = {str(name).lstrip("#") for name in only}
    facts = item.facts
    numbers = {str(pull["number"]) for pull in facts.get("pulls", [])}
    branches = {pull["branch"] for pull in facts.get("pulls", [])} | {facts.get("branch", "")}
    return bool(names & (numbers | branches))


def walk(root: Path, *, rows: dict, profile: dict, ignored=frozenset(), run=subprocess.run, proc_root=None,
         only=(), slug: str | None = ASK) -> Walk:
    root = Path(root)
    trunk = (profile.get("trunk") or {}).get("branch", "main")
    fetched = run(["git", "-C", str(root), "fetch", "--prune", "--quiet", "origin"], capture_output=True, text=True,
                  check=False)
    out = Walk(phase=phase_of(root, trunk, run), trunk=trunk)
    if fetched.returncode != 0:
        out.notes.append("git could not fetch origin, so what it shows is read as origin was last fetched here")
    if slug == ASK:
        slug = github.slug_of(root, run=run)
    login = ""
    if slug:
        try:
            login = github.login(run)
        except github.GitHubUnknown as err:
            out.notes.append(f"GitHub could not say who you are ({shown(str(err))}); no pull request is offered")
    person = whose.person_of(root, login, run=run)
    origin = gitq.resolve(root, f"refs/remotes/origin/{trunk}", run=run) or ""
    _before_items(out, root, trunk, origin, run)
    if out.phase == "after":
        pull_branches = _pull_items(out, root, trunk, origin, slug, person, rows, profile, only, run)
        _branch_items(out, root, trunk, person, rows, only, slug, pull_branches, run)
    elif slug:
        out.notes.append("pull requests, chains and branches are asked once the profile is on origin's trunk")
    _edits_items(out, root, person, run)
    if only:
        out.items = [item for item in out.items if _wanted(item, only) or item.kind in ("edits", "cache", "trunk")]
    skipped = [item.key for item in out.items if item.key in ignored]
    if skipped:
        out.items = [item for item in out.items if item.key not in ignored]
        out.notes.append(f"ignored earlier, not asked again: {', '.join(skipped)}")
    return out


def _before_items(out: Walk, root: Path, trunk: str, origin: str, run) -> None:
    if out.phase == "before":
        for cache in local.caches(root, run=run):
            out.items.append(Item(key=f"cache:{cache}", kind="cache", phase="before",
                                  title=f"{cache} - a tool's cache, untracked: add it to .git/info/exclude?",
                                  lines=[], facts={"dir": cache}))
        found = local.onboard_branch(root, trunk, run=run)
        if found and found["extra"]:
            lines = [f"{sha[:7]} {_subject(root, sha, run)}" for sha in found["extra"]]
            if not found["direct"]:
                out.notes.append(f"`{found['branch']}` holds the profile, but not directly on origin's {trunk}; "
                                 "publish cannot fast-forward to it - settle it yourself")
            else:
                out.items.append(Item(key=f"onboard:{found['branch']}", kind="onboard", phase="before",
                                      title=f"{found['branch']} also carries {len(found['extra'])} commit(s) beside the "
                                            "profile: land them with the profile, or move them to a branch of their own?",
                                      lines=lines, facts={**found, "origin": origin}))
    commits = local.trunk_commits(root, trunk, run=run)
    if commits:
        head = gitq.resolve(root, f"refs/heads/{trunk}", run=run) or ""
        out.items.append(Item(key=f"trunk:{trunk}", kind="trunk", phase=out.phase,
                              title=f"your local {trunk} has {len(commits)} commit(s) origin does not",
                              lines=[f"{sha[:7]} {_subject(root, sha, run)}" for sha in commits],
                              facts={"trunk": trunk, "head": head, "origin": origin, "commits": commits}))


def _known(rows: dict) -> tuple[set[str], set[int]]:
    branches = {row.branch for row in rows.values() if row.is_open and row.branch}
    numbers = {int(row.pr) for row in rows.values() if str(row.pr).isdigit() and row.is_open}
    for row in rows.values():
        numbers.update(int(number) for number in row.closes or [])
    return branches, numbers


def _pull_facts(pull) -> dict:
    return {"number": pull.number, "branch": pull.branch, "head": pull.head, "base": pull.base}


def _pull_items(out: Walk, root: Path, trunk: str, origin: str, slug, person, rows: dict, profile: dict, only,
                run) -> set[str]:
    """Items for the person's pull requests; returns every open pull request's branch, so no branch item repeats
    one."""
    if not slug:
        return set()
    try:
        pulls, truncated = github.pulls(slug, run)
        native = github.stacks(slug, run)
    except github.GitHubUnknown as err:
        out.notes.append(f"GitHub could not list pull requests or stacks ({shown(str(err))}); look at them yourself, "
                         "or run /flotilla:organize again")
        return set()
    every = {pull.branch for pull in pulls}
    if truncated:
        out.notes.append(f"the list of open pull requests stopped at {github.LIMIT}; the rest is not shown")
    branches, numbers = _known(rows)
    pulls = [pull for pull in pulls if pull.number not in numbers and pull.branch not in branches]
    named = {str(name).lstrip("#") for name in only}
    keep, others = [], []
    for pull in pulls:
        if pull.fork:
            others.append(f"#{pull.number} from a fork ({shown(pull.title)})")
        elif not SAFE_BRANCH.fullmatch(pull.branch):
            others.append(f"#{pull.number} on a branch a shell would read ({shown(pull.branch)}); not offered")
        elif str(pull.number) in named or pull.branch in named or _pull_is_mine(root, pull, person, origin, run):
            keep.append(pull)
        else:
            others.append(f"#{pull.number} `{shown(pull.branch)}` ({shown(pull.title)})")
    method = (profile.get("pr") or {}).get("merge_method") or ""
    if not method:
        out.notes.append("the profile names no merge method, so a chain is offered what a squash merge allows")
    by_number = {pull.number: pull for pull in keep}
    stacked = set()
    for item in native:
        members = [by_number[number] for number in item.members if number in by_number]
        if len(members) != len(item.members):
            continue   # a stack that holds work not offered here is not taken apart
        stacked.update(item.members)
        offered = chaining.offered(members, method=method, forked=False, off_trunk=False) if len(members) > 1 \
            else ["whole"]
        out.items.append(Item(key=f"stack:{item.number}", kind="stack", phase="after",
                              title=f"a native GitHub stack of {len(members)} pull request(s) - taking it in unstacks "
                                    "it on GitHub (reviewers there no longer see it as a stack)",
                              lines=_chain_lines(root, members, trunk, origin, run),
                              facts={"stack": item.number, "pulls": [_pull_facts(pull) for pull in members],
                                     "method": method, "origin": origin},
                              offered=offered))
    rest = [pull for pull in keep if pull.number not in stacked]

    def trunk_contains(branch: str) -> bool:
        tip = gitq.resolve(root, f"refs/remotes/origin/{branch}", run=run)
        return bool(tip) and gitq.is_ancestor(root, tip, origin, run=run) is True

    found, singles, notes = chaining.chains(rest, trunk, trunk_contains=trunk_contains)
    out.notes += notes
    for chain in found:
        forked = chaining.is_forked(chain, rest)
        off = chaining.is_off_trunk(chain, trunk, trunk_contains=trunk_contains)
        offered = chaining.offered(chain, method=method, forked=forked, off_trunk=off)
        why = (" - forked: two pull requests stand on one" if forked else
               " - its bottom stands on a branch trunk does not hold" if off else "")
        out.items.append(Item(key="chain:" + "-".join(str(pull.number) for pull in chain), kind="chain",
                              phase="after", title=f"a chain of {len(chain)} pull requests (bottom first){why}",
                              lines=_chain_lines(root, chain, trunk, origin, run),
                              facts={"pulls": [_pull_facts(pull) for pull in chain], "method": method,
                                     "origin": origin,
                                     "bottom_in_trunk": chaining.bottom_base_in_trunk(
                                         chain, trunk, trunk_contains=trunk_contains)},
                              offered=offered))
    for pull in singles:
        if pull.base != trunk and not trunk_contains(pull.base):
            others.append(f"#{pull.number} targets `{shown(pull.base)}`, a branch trunk does not hold")
            continue
        out.items.append(Item(key=f"pr:{pull.number}", kind="pr", phase="after",
                              title=f"pull request #{pull.number} `{shown(pull.branch)}` ({shown(pull.title)})",
                              lines=_chain_lines(root, [pull], trunk, origin, run),
                              facts={"pulls": [_pull_facts(pull)], "method": method, "origin": origin}))
    if others:
        out.listed.append("not touched (not yours, or another AI's - name it to take it in): " + "; ".join(others))
    return every


def _pull_is_mine(root: Path, pull, person, origin: str, run) -> bool:
    if not whose.pull_is_mine(pull, person):
        return False
    base = gitq.resolve(root, f"refs/remotes/origin/{pull.base}", run=run) or origin
    return not whose.other_ai_signs(root, base, pull.head, run=run)


def _chain_lines(root: Path, chain: list, trunk: str, origin: str, run) -> list[str]:
    lines = []
    for index, pull in enumerate(chain):
        below = chain[index - 1].head if index else (gitq.resolve(root, f"refs/remotes/origin/{pull.base}", run=run)
                                                      or origin)
        own = _count(root, below, pull.head, run) if below else 0
        clean = gitq.merges_cleanly(root, pull.head, origin, run=run) if origin else None
        conflict = "  conflicts with trunk" if clean is False else ""
        lines.append(f"#{pull.number} {shown(pull.branch)} -> {shown(pull.base)}  {own} commit(s) of its own"
                     f"{conflict}")
    return lines


def _branch_items(out: Walk, root: Path, trunk: str, person, rows: dict, only, slug, pull_branches: set[str],
                  run) -> None:
    merged: dict[str, set[str]] = {}
    if slug:
        try:
            merged = github.merged_branches(slug, run)
        except github.GitHubUnknown:
            out.notes.append("GitHub could not list merged pull requests; squash-merged branches may read as work")
    skip = pull_branches | {row.branch for row in rows.values() if row.is_open}
    trees = checked_out(root, run)
    found = local.branches(root, trunk, skip=skip, checked_out=set(trees),
                           merged_pr_heads=merged, run=run)
    origin = f"refs/remotes/origin/{trunk}"
    named = {str(name) for name in only}
    others = []
    for name in found["work"]:
        tip = gitq.resolve(root, f"refs/heads/{name}", run=run) or gitq.resolve(root, f"refs/remotes/origin/{name}",
                                                                                 run=run) or ""
        if not SAFE_BRANCH.fullmatch(name):
            others.append(f"`{shown(name)}` (a name a shell would read)")
            continue
        base = (_git(root, "merge-base", origin, tip, run=run) or "").strip()
        mine, why = whose.range_is_mine(root, base, tip, person, run=run) if base else (False, "no common history")
        if not mine and name not in named:
            others.append(f"`{shown(name)}` ({why})")
            continue
        local_branch = gitq.resolve(root, f"refs/heads/{name}", run=run) is not None
        out.items.append(Item(key=f"branch:{name}", kind="branch", phase="after",
                              title=f"branch `{name}` - {_count(root, base, tip, run)} commit(s) not on {trunk}, no pull "
                                    "request",
                              lines=[], facts={"branch": name, "tip": tip, "local": local_branch, "base": base}))
    if others:
        out.listed.append("not touched (not yours, or another AI's - name it to take it in): " + "; ".join(others))
    unsafe = [name for name in found["no_own"] + found["merged"] if not SAFE_BRANCH.fullmatch(name)]
    for label, names in (("no commits of their own", found["no_own"]), ("merged", found["merged"])):
        plain = [name for name in names if SAFE_BRANCH.fullmatch(name)]
        if plain:
            out.listed.append(f"{label}, may be deleted: " + ", ".join(plain))
    if unsafe:
        out.listed.append("on trunk, but named so a shell would read the name - a name a shell would read, look at "
                          "them yourself: " + ", ".join(f"`{shown(name)}`" for name in unsafe))


def _edits_items(out: Walk, root: Path, person, run) -> None:
    paths = local.dirty(root, run=run)
    if not paths:
        return
    signs = [path for path in paths if whose.is_path_sign(path)]
    if signs:
        out.listed.append(f"not touched: this tree's edits carry another AI's files ({', '.join(signs[:3])})")
        return
    digest = (_git(root, "status", "--porcelain=v1", "--untracked-files=all", run=run) or "")
    out.items.append(Item(key=f"edits:{root}", kind="edits", phase=out.phase,
                          title=f"uncommitted edits in this tree ({len(paths)} path(s))", lines=paths[:20],
                          facts={"tree": str(root), "paths": paths, "status": digest}))
