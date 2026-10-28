"""The apply step (gc design, section 9): the collector carries out exactly the person's recorded answer.

The answer is the authority, and only this step acts on it - a session relaying "the person said yes" approves
nothing, and `flotilla gc apply` refuses a background session. Each answered piece of work is judged again under the
ledger's lock with every tree of it locked against sessions; when anything moved since the person was shown it (a
file, HEAD, a session that started in it), the answer is set aside and the question comes back.

- **archive**: the tree's HEAD and its reflog-only commits are parked; tracked edits are saved as a patch; untracked
  files, the person's excluded files and unique ignored files go to the archive; the tree is then checked once more
  (nothing left but those edits and what an install makes again) and removed with `--force` - git's own refusal is
  what the archive answered.
- **junk**: the commits are parked as above and the tree is removed for good; the answer stays recorded, so the
  piece is never asked about again.
- **keep**: nothing; the answer stays until something in the trees changes.
- **words**: nothing here; a session carries the person's words out as ordinary work.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from flotilla.core.text import visible
from flotilla.gc import archive, ignored, questions, refs
from flotilla.gc.park import ParkFailed, park
from flotilla.gc.remove import last_copies
from flotilla.gc.trees import GC_LOCK, judge_tree


def _git(ledger, where, *args):
    return ledger.run(["git", "-C", str(where), *args], capture_output=True, text=True, check=False)


def _patch(ledger, tree: Path) -> bytes | None:
    """The tree's edits against HEAD as a patch `git apply` takes back, whatever the person's diff settings are
    (colour, no prefix, an external diff or a textconv made it unusable: final review of gc stage 2, C2); None when git
    cannot say."""
    done = ledger.run(["git", "-C", str(tree), "-c", "color.ui=never", "-c", "diff.noprefix=false", "diff-index", "-p",
                       "--binary", "--no-ext-diff", "--no-textconv", "--no-color", "--src-prefix=a/", "--dst-prefix=b/",
                       "HEAD"], capture_output=True, check=False)
    return done.stdout if done.returncode == 0 else None


def _reproduces(ledger, tree: Path, saved: Path) -> bool:
    """Whether the saved patch, taken back out of the tree, applies: it describes exactly the edits that are there."""
    done = ledger.run(["git", "-C", str(tree), "apply", "--check", "--reverse", "--binary", str(saved)],
                      capture_output=True, check=False)
    return done.returncode == 0


def _untracked(ledger, tree: Path) -> list[str]:
    listed = _git(ledger, tree, "-c", "status.showUntrackedFiles=all", "ls-files", "-z", "--others",
                  "--exclude-standard")
    return [path for path in listed.stdout.split("\0") if path]


def _left_over(ledger, tree: Path, patch: bytes, twins, saved: Path) -> str:
    """What still stands in the way after the archive, or "": only the saved edits and what comes back may stay."""
    if _untracked(ledger, tree):
        return "untracked files appeared"
    found = ignored.classify(tree, main=ledger.root, profile=ledger.profile, twins=twins, run=ledger.run)
    if found is None or found.unique or found.personal or found.nested:
        return "ignored files with no copy remain"
    if _patch(ledger, tree) != patch:
        return "its edits changed while they were saved"
    if patch and not _reproduces(ledger, tree, saved):
        return "the saved edits would not apply back"
    return ""


def _carry_out(ledger, key: str, answer: dict, verdicts: list, twins) -> list[str]:
    """Archive or junk every tree of one piece of work, already judged again under the locks."""
    value, day = answer["value"], datetime.now(timezone.utc).date().isoformat()
    lines = []
    for verdict in verdicts:
        tree = Path(verdict.tree)
        sole = [doubt.paths[0] for doubt in verdict.doubts if doubt.kind == "sole" and doubt.paths]
        try:
            park(ledger, tree.name, sole + list(verdict.park))
        except ParkFailed as err:
            return lines + [f"{visible(key)}: kept {visible(str(tree))}: {err}"]
        if value == "archive":
            patch = _patch(ledger, tree)
            if patch is None:
                return lines + [f"{visible(key)}: kept {visible(str(tree))}: git could not show its edits"]
            personal = [path for doubt in verdict.doubts if doubt.kind == "personal" for path in doubt.paths]
            stored = [path for path, twin in verdict.twins if twin.kind == "archive"]   # restorable by its name
            files = _untracked(ledger, tree) + personal + [path for path, _, _ in verdict.archive] + stored
            try:   # with the last copies (second review of the branch, I-4)
                entry = archive.put(ledger, entry=archive.entry_id(key, day), tree=tree, files=files,
                                    tier="3" if verdict.doubts else "2", approved_by=f"answer {key} {answer['at']}",
                                    branch=verdict.branch, head=verdict.head, patch=patch,
                                    last_copies=last_copies(str(tree), verdict))
            except archive.ArchiveFailed as err:
                return lines + [f"{visible(key)}: kept {visible(str(tree))}: {err}"]
            record = next(item for item in entry.trees if item["tree"] == str(tree))
            saved = archive.root_of(ledger) / "entries" / entry.id / record["patch"] if record["patch"] else tree
            left = _left_over(ledger, tree, patch, twins, saved)
            if left:
                return lines + [f"{visible(key)}: kept {visible(str(tree))} after archiving its files: {left}; "
                                f"restore with flotilla gc restore {entry.id}"]
        _git(ledger, ledger.root, "worktree", "unlock", str(tree))
        done = _git(ledger, ledger.root, "worktree", "remove", "--force", str(tree))
        if done.returncode != 0:
            return lines + [f"{visible(key)}: git refused to remove {visible(str(tree))}: "
                            f"{(done.stderr or done.stdout).strip()[:160]}"]
        lines.append(f"{visible(key)}: archived and removed {visible(tree.name)}: restore with flotilla gc restore "
                     f"{archive.entry_id(key, day)}" if value == "archive"
                     else f"{visible(key)}: removed {visible(tree.name)} for good, as you said")
    return lines


def apply(ledger, data: dict, *, items: list, keys=(), sessions, proc_root=None, twins=None) -> tuple[list[str], bool]:
    """Carry out the recorded answers (`keys` narrows them to some pieces of work); the lines said and whether every
    answer was carried out."""
    by_key = {item.key: item for item in items}
    lines, complete = [], True
    for key, answer in list(data["answers"].items()):
        if key == questions.ALL or (keys and key not in keys):
            continue
        value = answer.get("value")
        item = by_key.get(key)
        if item is None:
            if value in ("archive", "junk", "words"):
                data["answers"].pop(key)
                lines.append(f"{visible(key)}: no longer waits on you (something holds it now, or it is gone); "
                             "the answer is set aside")
                complete = False
            continue
        if answer.get("seen") != questions.fingerprint(item):   # moved since: asked again, nothing done
            data["answers"].pop(key)
            if value != "keep":
                lines.append(f"{visible(key)}: changed since you answered; asked again")
                complete = False
            continue
        if value == "keep":
            lines.append(f"{visible(key)}: kept, as you said")
            continue
        if value == "words":
            lines += questions.waiting([item], data)
            complete = False
            continue
        lines_here, done = _locked(ledger, key, answer, item, sessions=sessions, proc_root=proc_root, twins=twins)
        lines += lines_here
        complete = complete and done
        if done and value == "archive":
            data["answers"].pop(key)
        elif not done:
            data["answers"].pop(key, None)
    return lines, complete


def _locked(ledger, key, answer, item, *, sessions, proc_root, twins) -> tuple[list[str], bool]:
    """Lock every tree of the piece, judge them again, and carry the answer out only if nothing moved."""
    with ledger.session():
        locked = []
        for tree in item.trees:
            if _git(ledger, ledger.root, "worktree", "lock", "--reason", GC_LOCK, tree).returncode != 0:
                for done in locked:
                    _git(ledger, ledger.root, "worktree", "unlock", done)
                return [f"{visible(key)}: a session locked {visible(Path(tree).name)}; asked again"], False
            locked.append(tree)
        checking = twins.checking() if twins is not None else None
        holders = refs.holders(ledger)
        if checking is not None:
            checking.rehold(holders)
        again = [judge_tree(ledger, tree, holders=holders, sessions=sessions, proc_root=proc_root, twins=checking)
                 for tree in item.trees]
        now = questions.Item(key, list(item.trees), again)
        if any(verdict.reasons for verdict in again) or questions.fingerprint(now) != answer.get("seen"):
            for tree in locked:
                _git(ledger, ledger.root, "worktree", "unlock", tree)
            return [f"{visible(key)}: changed since you answered; asked again"], False
        try:
            lines = _carry_out(ledger, key, answer, again, checking)
        except OSError as err:   # nothing may leave a tree locked by the collector (final review of gc stage 2, I5)
            lines = [f"{visible(key)}: stopped: {err}"]
        finally:
            for tree in locked:
                if Path(tree).exists():
                    _git(ledger, ledger.root, "worktree", "unlock", tree)
        removed = all(not Path(tree).exists() for tree in item.trees)
        return lines, removed
