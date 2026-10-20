"""Carrying out what the person answered (spec, sections 4-7).

Only what an answer names is done, and only while what the person was shown still holds: each answered item is
found again in a fresh walk and its fingerprint compared; an item that moved or went is forgotten and asked again,
with a line saying so. Everything outside the ledger is done here and nowhere else - the skill never builds these
commands - and a drop, the one answer that loses work, is printed for the person to type after `!`, never run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from flotilla.ledger import gitq
from flotilla.ledger.errors import MoveRefused
from flotilla.organize import questions, store
from flotilla.organize.items import Item, seen, shown

FOLLOW_UPS = (":shape", ":proposal", ":plan", ":steps")
#: The order answers are carried out in: the local trunk is settled before onboarding's branch moves trunk, so no
#: move of trunk ever passes the person's own commits on it (final review of 67a497f..7a44d0c, finding 1).
ORDER = ("cache", "trunk", "onboard", "edits")


class Skip(RuntimeError):
    """This item is not carried out now; the message says why and what to do."""


def _git(root: Path, *args: str, run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def _item_keys(data: dict) -> list[str]:
    keys = set()
    for qid in data["answers"]:
        if qid.startswith("tree:"):
            continue
        keys.add(next((qid[:-len(suffix)] for suffix in FOLLOW_UPS if qid.endswith(suffix)), qid))
    return sorted(keys)


def _settled(walk, data: dict, item: Item) -> bool:
    open_ = [question["id"] for question in questions.all_questions(walk, data)]
    if any(qid == item.key or qid.startswith(f"{item.key}:") for qid in open_):
        return False
    return not any(line.startswith(f"{item.key}:") for line in questions.waiting(walk, data))


def apply(root, data: dict, *, keys=(), walked, profile: dict, run=subprocess.run, ledger=None,
          proc_root=None) -> tuple[list[str], bool]:
    root = Path(root)
    lines, complete = [], True
    current = {item.key: item for item in walked.items}
    order = {kind: index for index, kind in enumerate(ORDER)}
    keys_now = sorted(_item_keys(data), key=lambda key: (order.get(current[key].kind, len(ORDER))
                                                          if key in current else len(ORDER), key))
    for key in keys_now:
        if keys and key not in keys:
            continue
        answer = data["answers"].get(key) or {}
        item = current.get(key)
        if item is None:
            if key in data["ignored"]:
                store.forget(data, key)
                continue
            lines.append(f"{key}: no longer there as you were asked about it; it is asked again if it still needs "
                         "you")
            store.forget(data, key)
            complete = False
            continue
        if answer.get("seen") != seen(item):
            lines.append(f"`{key}` changed since you were asked; it is asked again")
            store.forget(data, key)
            complete = False
            continue
        if not _settled(walked, data, item):
            lines.append(f"{key}: waits for the rest of its answers (`flotilla organize next`)")
            complete = False
            continue
        try:
            said = _carry_out(root, item, data, walked=walked, profile=profile, run=run, ledger=ledger,
                              proc_root=proc_root)
        except Skip as err:
            lines.append(f"{key}: {err}")
            complete = False
            continue
        except MoveRefused as err:
            lines.append(f"{key}: refused - {err}")
            complete = False
            continue
        lines += said
        store.forget(data, key)
    return lines, complete


def _ledger(root: Path, ledger):
    if ledger is not None:
        return ledger
    from flotilla.ledger.commands import open_ledger
    return open_ledger(root)


def _carry_out(root: Path, item: Item, data: dict, *, walked, profile: dict, run, ledger, proc_root) -> list[str]:
    value = (data["answers"].get(item.key) or {}).get("value")
    if value == "ignore":
        return []
    if value == "words" and item.kind != "edits":
        return _steps(root, item, data, walked=walked, run=run, ledger=ledger)
    if value == "take" and item.kind in ("pr", "chain", "branch", "stack"):
        return _take(root, item, questions.shape_of(item, data), walked=walked, run=run, ledger=ledger)
    if item.kind == "cache":
        return [exclude(root, item.facts["dir"], run=run)] if value == "exclude" else []
    if item.kind == "trunk":
        return _trunk(root, item, value, walked=walked, run=run, ledger=ledger)
    if item.kind == "edits":
        return _edits(root, item, value, data, walked=walked, run=run, ledger=ledger)
    if item.kind == "onboard":
        return _onboard(root, item, value, data, walked=walked, run=run)
    raise Skip(f"`{value}` for a {item.kind} is not carried out by this version yet")


def exclude(root: Path, directory: str, run=subprocess.run) -> str:
    """Add a tool's cache to `.git/info/exclude` once: the repository's own, never committed."""
    where = _git(root, "rev-parse", "--git-path", "info/exclude", run=run).stdout.strip()
    path = Path(where) if Path(where).is_absolute() else Path(root) / where
    line = "/" + directory.strip("/") + "/"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if line in text.splitlines():
        return f"{directory} is excluded already"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + line + "\n", encoding="utf-8")
    return f"{directory} added to .git/info/exclude"


def _slug(root: Path, run) -> str:
    from flotilla.organize import github
    slug = github.slug_of(root, run=run)
    if not slug:
        raise Skip("this repository's origin is not on GitHub")
    return slug


def _unstack(root: Path, item: Item, run) -> str:
    """Unstack a native stack and read it back: every open member must have left it, or nothing else is done."""
    from flotilla.organize import github
    slug = _slug(root, run)
    number = item.facts["stack"]
    try:
        github.unstack(slug, number, run)
        left = github.stacks(slug, run)
    except github.GitHubUnknown as err:
        raise Skip(f"GitHub did not unstack stack {number} ({shown(str(err))}); nothing else was done") from err
    members = {pull["number"] for pull in item.facts["pulls"]}
    still = sorted(members & {n for stack in left for n in stack.members})
    if still:
        raise Skip(f"#{', #'.join(str(n) for n in still)} still stacked on GitHub (queued, or set to auto-merge?); "
                   "nothing else was done - settle it on GitHub, then run organize again")
    return f"stack {number} unstacked on GitHub; its pull requests are a plain chain now"


def _steps(root: Path, item: Item, data: dict, *, walked, run, ledger) -> list[str]:
    """The steps the person confirmed for their own words, each held to this item: own words about #42 never
    close #99."""
    from flotilla.organize import github
    steps = questions.confirmed(item, data, "steps")
    if steps is None:
        raise Skip("its steps were not confirmed as they stand; it is asked again")
    numbers = {pull["number"] for pull in item.facts.get("pulls", [])}
    branches = {pull["branch"] for pull in item.facts.get("pulls", [])} | {item.facts.get("branch", "")}
    for step in steps:   # checked whole before the first one runs
        if step["do"] == "close" and step["pr"] not in numbers:
            raise Skip(f"step `close #{step['pr']}` names a pull request outside this item; nothing was done")
        if step["do"] == "unstack" and step["stack"] != item.facts.get("stack"):
            raise Skip(f"step `unstack {step['stack']}` names a stack outside this item; nothing was done")
        if step["do"] == "delete-branch" and step["branch"] not in branches - {""}:
            raise Skip(f"step `delete {step['branch']}` names a branch outside this item; nothing was done")
        if step["do"] == "take" and item.kind not in ("pr", "chain", "branch", "stack"):
            raise Skip(f"a {item.kind} is not taken in that way; nothing was done")
    out = []
    for step in steps:
        if step["do"] == "ignore":
            data["ignored"][item.key] = seen(item)
            out.append(f"{item.key}: ignored")
        elif step["do"] == "take":
            out += _take(root, item, step["shape"], walked=walked, run=run, ledger=ledger)
        elif step["do"] == "close":
            try:
                github.close(_slug(root, run), step["pr"], "Closed on the person's word (flotilla organize)", run)
            except github.GitHubUnknown as err:
                raise Skip(f"GitHub did not close #{step['pr']} ({shown(str(err))})") from err
            out.append(f"closed #{step['pr']}")
        elif step["do"] == "unstack":
            out.append(_unstack(root, item, run))
        elif step["do"] == "delete-branch":
            out.append(_drop_branch(root, step["branch"], run))
    return out


def _kept(root: Path, sha: str, run) -> str:
    """The dropped ref holding `sha`: made now, or found from an earlier run - and proved to hold it before any
    command that loses work is printed (final review, finding 11: a lock or a full disk read as "kept already")."""
    from flotilla.organize import keep
    ref = f"refs/flotilla/dropped/{sha}"
    try:
        keep.create_ref(root, "dropped", sha, run=run)
    except RuntimeError as err:
        if gitq.resolve(root, ref, run=run) != sha:
            raise Skip(f"the commits could not be kept under {ref} ({err}); nothing is dropped") from err
    return ref


def _drop_branch(root: Path, branch: str, run) -> str:
    from flotilla.organize import keep
    tip = gitq.resolve(root, f"refs/heads/{branch}", run=run)
    if tip is None:
        raise Skip(f"there is no local `{branch}` to delete")
    ref = _kept(root, tip, run)
    return (f"`{branch}` is kept under {ref}; deleting it loses work, so it is yours to type: "
            f"! git branch -D {branch}")


def _trunk_tree(root: Path, trunk: str, run) -> str:
    from flotilla.organize.walk import checked_out
    return checked_out(root, run).get(trunk, "")


def _trunk(root: Path, item: Item, value: str, *, walked, run, ledger) -> list[str]:
    from flotilla.organize import keep
    trunk, head = item.facts["trunk"], item.facts["head"]
    if value == "leave":
        return [f"your local {trunk} is left as is: publish refuses while it is ahead with these; push them "
                "yourself after `!` when you mean to"]
    where = _trunk_tree(root, trunk, run)
    here = Path(root).resolve()
    command = (f"git reset --keep origin/{trunk}" if where and Path(where).resolve() == here
               else f"git branch -f {trunk} origin/{trunk}")
    if value == "drop":
        ref = _kept(root, head, run)
        return [f"the {len(item.facts['commits'])} commit(s) are kept under {ref}; dropping them is yours to type: "
                f"! {command}"]
    if value != "move":
        raise Skip(f"`{value}` is not an answer for the local trunk")
    if where and Path(where).resolve() != here:
        raise Skip(f"{trunk} is checked out in {shown(where, 120)}; it is not moved from here - switch that tree "
                   "first, or answer in your own words")
    branch = f"work/local-{head[:7]}"
    made = _git(root, "branch", branch, head, run=run)
    if made.returncode != 0:
        raise Skip(f"git could not create `{branch}`: {made.stderr.strip()[:200]}")
    if where:
        if gitq.resolve(root, "HEAD", run=run) != head:
            raise Skip(f"`{branch}` holds the commits, but {trunk} moved since you were asked; it is not moved")
        moved = _git(root, "reset", "--keep", f"origin/{trunk}", run=run)
    else:   # compare-and-swap: a commit made since the walk is never left on no branch
        moved = _git(root, "update-ref", f"refs/heads/{trunk}", item.facts["origin"], head, run=run)
    if moved.returncode != 0:
        raise Skip(f"`{branch}` holds the commits, but {trunk} did not move: {moved.stderr.strip()[:200]}")
    out = [f"`{branch}` holds the {len(item.facts['commits'])} commit(s); {trunk} is back at origin's"]
    if walked.phase == "after":
        base = _fork_point(root, trunk, head, run)
        from flotilla.ledger import takein
        row = takein.take_in(_ledger(root, ledger), branch, base=base)
        out.append(f"took `{branch}` in as {row.id}")
    else:
        out.append(f"`{branch}` is asked about once the profile is on origin's trunk")
    return out


def _fork_point(root: Path, trunk: str, tip: str, run) -> str:
    done = _git(root, "merge-base", f"refs/remotes/origin/{trunk}", tip, run=run)
    if done.returncode != 0 or not done.stdout.strip():
        raise Skip(f"{tip[:7]} shares no history with origin's {trunk}")
    return done.stdout.strip()


def _local_branch(root: Path, branch: str, head: str, run) -> None:
    """The branch exists locally at the pull request's head: created there when only origin has it; left alone, and
    the item skipped, when it stands somewhere else."""
    here = gitq.resolve(root, f"refs/heads/{branch}", run=run)
    if here is None:
        made = _git(root, "branch", branch, head, run=run)
        if made.returncode != 0:
            raise Skip(f"git could not create `{branch}` at {head[:7]}: {made.stderr.strip()[:200]}")
    elif here != head:
        raise Skip(f"your local `{branch}` is at {here[:7]}, the pull request's head is {head[:7]}; bring them "
                   "together (push or pull it), then run apply again")


def _take(root: Path, item: Item, shape: str, *, walked, run, ledger) -> list[str]:
    from flotilla.ledger import takein
    ledger = _ledger(root, ledger)
    trunk = walked.trunk
    if item.kind == "branch":
        base = item.facts.get("base") or _fork_point(root, trunk, item.facts["tip"], run)
        row = takein.take_in(ledger, item.facts["branch"], base=base)
        return [f"took `{item.facts['branch']}` in as {row.id}; the orchestrator gives it to a seat"]
    pulls = item.facts["pulls"]
    for pull in pulls:
        _local_branch(root, pull["branch"], pull["head"], run)
    if item.kind == "pr":
        shape = "separate"
    if shape not in ("separate", "parts", "whole"):
        raise Skip(f"no shape is settled for it (`{shape or 'none'}`)")
    said = [_unstack(root, item, run)] if item.kind == "stack" else []
    return said + _take_pulls(root, item, shape, trunk=trunk, run=run, ledger=ledger)


def _take_pulls(root: Path, item: Item, shape: str, *, trunk: str, run, ledger) -> list[str]:
    from flotilla.ledger import takein
    pulls = item.facts["pulls"]
    bottom_off = pulls[0]["base"] != trunk   # a lower pull request merged without deleting its branch
    if shape == "whole":
        top = pulls[-1]
        lower = [pull["number"] for pull in pulls[:-1]]
        # with merge commits GitHub marks the bottom one merged itself once its head reaches trunk
        closes = lower if item.facts.get("method") != "merge" or bottom_off else lower[1:]
        row = takein.take_in(ledger, top["branch"], base=_fork_point(root, trunk, top["head"], run),
                             pr=top["number"], retarget=trunk, closes=closes)
        said = f"; once it lands, #{', #'.join(str(n) for n in closes)} close" if closes else ""
        return [f"took #{top['number']} in as {row.id}, one work whole over {len(pulls)} pull request(s){said}"]
    if shape not in ("separate", "parts"):
        raise Skip(f"no shape is settled for it (`{shape or 'none'}`)")
    fork = _fork_point(root, trunk, pulls[0]["head"], run)
    out, below = [], None
    for index, pull in enumerate(pulls):
        base = fork if index == 0 else pulls[index - 1]["head"]
        links = [below] if below else []
        row = takein.take_in(ledger, pull["branch"], base=base, pr=pull["number"],
                             after=links if shape == "separate" else (), requires=links if shape == "parts" else (),
                             retarget=trunk if index or bottom_off else "")
        below = row.id
        out.append(f"took #{pull['number']} `{shown(pull['branch'])}` in as {row.id}"
                   + (f" ({'after' if shape == 'separate' else 'a part above'} {links[0]})" if links else ""))
    return out


def _edits(root: Path, item: Item, value: str, data: dict, *, walked, run, ledger) -> list[str]:
    # "discretion", "words" and typed words all come here: each is carried out by a plan the person confirmed
    """Keep every edit first, keep secrets out, then carry out the plan the person confirmed, exactly."""
    from flotilla.core import paths
    from flotilla.organize import keep, secrets, split
    tree = Path(item.facts["tree"])
    if value == "leave":
        return [f"the edits in {shown(str(tree), 120)} are left as they are; the fleet does not see them"]
    plan = questions.confirmed(item, data, "plan")
    if plan is None:
        raise Skip("its plan was not confirmed as it stands; it is asked again")
    errors = split.validate_plan(plan, item.facts["paths"])
    if errors:
        raise Skip("the plan does not fit the tree's edits (" + "; ".join(errors) + "); nothing was done")
    try:
        kept = keep.create_ref(root, "kept", keep.keep_all(tree, run=run), run=run)
    except RuntimeError as err:
        raise Skip(f"the edits could not be kept first ({err}); nothing was done") from err
    out = [f"every edit in {shown(str(tree), 120)} is kept under {kept} first"]
    hits = {hit.path: hit for hit in secrets.scan(tree, [p for entry in plan for p in entry["paths"]], run=run)}
    for path, hit in sorted(hits.items()):
        out.append(f"`{shown(path)}` stays where it is, out of every branch: {hit.why} - a secret is never committed")
    plan = [{"branch": entry["branch"], "paths": [p for p in entry["paths"] if p not in hits]} for entry in plan]
    plan = [entry for entry in plan if entry["paths"]]
    if not plan:
        return out
    start = _git(tree, "rev-parse", "HEAD", run=run).stdout.strip()
    try:
        said, made = split.carry_out(root, tree, plan, start=start, state=paths.state_dir(), run=run)
    except split.Stopped as err:
        raise Skip(f"{err}; every edit is still in the tree, and kept under {kept}") from err
    out += said
    if walked.phase != "after":
        return out + [f"{', '.join(made)} are asked about once the profile is on origin's trunk"]
    from flotilla.ledger import takein
    for branch in made:
        row = takein.take_in(_ledger(root, ledger), branch, base=_fork_point(root, walked.trunk, branch, run))
        out.append(f"took `{branch}` in as {row.id}")
    return out


def _slug_branch(subject: str) -> str:
    import re
    from flotilla.ledger.core import SAFE_BRANCH
    slug = re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")[:40].strip("-") or "work"
    name = f"work/{slug}"
    return name if SAFE_BRANCH.fullmatch(name) else "work/beside-the-profile"


def _onboard(root: Path, item: Item, value: str, data: dict, *, walked, run) -> list[str]:
    """Onboarding's branch beside its profile commit (spec, section 6): trunk is fast-forwarded to the profile - or,
    on `land`, to the branch's tip - and the organizing tree switched to it, so `onboard publish` carries a profile
    already committed. Trunk is never rewritten: only fast-forwarded, and refused where another tree holds it."""
    from flotilla.organize import keep
    facts, trunk = item.facts, walked.trunk
    branch, profile, tip = facts["branch"], facts["profile"], facts["tip"]
    if value not in ("land", "own-branch"):
        raise Skip(f"`{value}` is not an answer for onboarding's branch")
    where = _trunk_tree(root, trunk, run)
    if where:
        raise Skip(f"{trunk} is checked out in {shown(where, 120)}; trunk is moved only where no tree holds it")
    if _git(root, "symbolic-ref", "-q", "--short", "HEAD", run=run).stdout.strip() != branch:
        raise Skip(f"this tree is not on `{branch}`; run organize from the tree onboarding wrote the profile in")
    if _git(root, "status", "--porcelain", "--untracked-files=no", run=run).stdout.strip():
        raise Skip("this tree has uncommitted changes to tracked files; settle them first, so switching to "
                   f"{trunk} loses nothing")
    target = tip if value == "land" else profile
    if gitq.is_ancestor(root, facts["origin"], target, run=run) is not True:
        raise Skip(f"{target[:7]} does not stand on origin's {trunk}; trunk is only fast-forwarded")
    before = gitq.resolve(root, f"refs/heads/{trunk}", run=run)
    if before and gitq.is_ancestor(root, before, target, run=run) is not True:
        raise Skip(f"your local {trunk} has commits {target[:7]} does not carry ({before[:7]}); trunk is only "
                   f"fast-forwarded, so settle the local trunk first (`trunk:{trunk}`), then this is asked again")
    name = ""
    if value == "own-branch":
        subject = _git(root, "log", "-1", "--format=%s", facts["extra"][0], run=run).stdout.strip()
        name = _slug_branch(subject)
        if gitq.resolve(root, f"refs/heads/{name}", run=run):
            raise Skip(f"`{name}` exists already; answer in your own words where these commits go")
    moved = _git(root, "update-ref", f"refs/heads/{trunk}", target, before or keep.ZERO, run=run)
    if moved.returncode != 0:   # compare-and-swap: trunk moved since it was checked, so nothing is moved
        raise Skip(f"git did not move {trunk}: {moved.stderr.strip()[:200]}")
    switched = _git(root, "switch", "-q", trunk, run=run)
    if switched.returncode != 0:
        raise Skip(f"{trunk} is at {target[:7]}, but this tree did not switch to it: {switched.stderr.strip()[:200]}")
    out = []
    if value == "own-branch":
        renamed = _git(root, "branch", "-m", branch, name, run=run)
        if renamed.returncode != 0:
            raise Skip(f"git did not rename `{branch}`: {renamed.stderr.strip()[:200]}")
        data["publish_named"] = []
        out.append(f"`{name}` holds the {len(facts['extra'])} commit(s) beside the profile; it is asked about once "
                   "the profile is on origin's trunk")
    else:
        data["publish_named"] = list(facts["extra"])
        out.append(f"{trunk} carries the profile and the {len(facts['extra'])} commit(s) beside it")
    out.append(f"{trunk} is at {target[:7]} and this tree is on it; next: `flotilla onboard publish`")
    return out
