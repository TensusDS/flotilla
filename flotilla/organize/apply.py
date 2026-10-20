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
    for key in _item_keys(data):
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
    if value == "take" and item.kind in ("pr", "chain", "branch"):
        return _take(root, item, data, walked=walked, run=run, ledger=_ledger(root, ledger))
    raise Skip(f"`{value}` for a {item.kind} is not carried out by this version yet")


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


def _take(root: Path, item: Item, data: dict, *, walked, run, ledger) -> list[str]:
    from flotilla.ledger import takein
    trunk = walked.trunk
    if item.kind == "branch":
        base = item.facts.get("base") or _fork_point(root, trunk, item.facts["tip"], run)
        row = takein.take_in(ledger, item.facts["branch"], base=base)
        return [f"took `{item.facts['branch']}` in as {row.id}; the orchestrator gives it to a seat"]
    pulls = item.facts["pulls"]
    for pull in pulls:
        _local_branch(root, pull["branch"], pull["head"], run)
    shape = "separate" if item.kind == "pr" else questions.shape_of(item, data)
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
