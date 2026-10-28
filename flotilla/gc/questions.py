"""The collector's questions, through onboarding's question door (gc design, sections 3 and 9).

One question per piece of work, across all its trees (a builder's tree and a reviewer's seat on the same row are
asked about once), never one per tree or per file. The answers offered are the person's: archive and remove, junk,
keep, or their own words. Archive is left out where the content is not plain files to move (a nested repository, a
populated submodule, files git is told not to look at). The collector proposes only for what the archive would take
(tier 2, large), and "accept all as proposed" records that proposal per item; for tier 3 it shows the evidence and
proposes nothing.

An answer is bound to the fingerprint of what the person was shown: a question's id carries it
(`work:<key>@<fingerprint>`, `gc:all@<the proposals' fingerprint>`), an answer to an id that no longer matches is
refused, and when anything in the trees moves later the recorded answer no longer applies and the question comes back
(security review of 089743b). A "keep" with the same fingerprint is not asked again. Everything in a question is made
visible: names of trees, files and branches reach the person's terminal.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from flotilla.core.text import visible
from flotilla.gc import trees
from flotilla.gc.sweep import piece, size_text
from flotilla.onboard.questions import MAX_PER_ROUND, AnswerError, _q, validate_answer

ALL = "gc:all"
OPTIONS = {
    "archive": ("archive", "Archive and remove", "The files go to the archive and the tree goes; `flotilla gc restore` "
                                                 "brings them back while the archive keeps them."),
    "junk": ("junk", "Junk", "Removed for good now, the commits parked; never asked about again."),
    "keep": ("keep", "Keep", "The tree stays; asked again only when something in it changes."),
}
NOT_FILES = ("hidden", "submodule", "nested")
MEDIA = (".exr", ".hdr", ".blend", ".psd")


@dataclass
class Item:
    key: str
    trees: list[str] = field(default_factory=list)
    verdicts: list = field(default_factory=list)
    proposed: str = ""
    offered: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def _media(path: str) -> bool:
    kind = mimetypes.guess_type(path)[0] or ""
    return kind.split("/")[0] in ("video", "audio", "image") or path.lower().endswith(MEDIA)


def _mtime(path: Path) -> int:
    try:
        return path.lstat().st_mtime_ns
    except OSError:
        return -1


def _archive_text(verdict) -> str:
    tree = Path(verdict.tree)
    newest = sorted(verdict.archive, key=lambda item: _mtime(tree / item[0]), reverse=True)
    media = sum(1 for path, _, _ in verdict.archive if _media(path))
    size = sum(size for _, size, _ in verdict.archive)
    kinds = f", {media} of them media" if media else ""
    return (f"{len(verdict.archive)} ignored file(s) with no copy elsewhere, {size_text(size)}{kinds}, newest "
            + ", ".join(visible(path) for path, _, _ in newest[:3]))


def items(verdicts: dict, *, rows: dict, small: set) -> list[Item]:
    """The trees only the person can settle, grouped by piece of work, in judging order."""
    found: dict[str, Item] = {}
    for tree, verdict in verdicts.items():
        if verdict is None or not trees.asks(verdict) or tree in small:
            continue
        key = piece(rows, Path(tree), verdict.branch, verdict.head)
        item = found.setdefault(key, Item(key))
        item.trees.append(str(tree))
        item.verdicts.append(verdict)
        lines = [doubt.text for doubt in verdict.doubts]
        if verdict.archive:
            lines.append(_archive_text(verdict))
        item.evidence.append(f"{Path(tree).name}: " + "; ".join(lines))
    for item in found.values():
        kinds = {doubt.kind for verdict in item.verdicts for doubt in verdict.doubts}
        item.proposed = "archive" if not kinds else ""
        item.offered = [name for name in OPTIONS if name != "archive" or not kinds & set(NOT_FILES)]
    return list(found.values())


def fingerprint(item: Item) -> str:
    """What the person was shown: each tree's HEAD and every path in question with its size and mtime."""
    shown = []
    for verdict in item.verdicts:
        tree = Path(verdict.tree)
        named = sorted({path for doubt in verdict.doubts if doubt.kind != "sole" for path in doubt.paths}
                       | {path for path, _, _ in verdict.archive})
        files = []
        for path in named:
            try:   # ctime and inode too: a rewrite can keep size and mtime (final review of gc stage 2, C1)
                stat = (tree / path).lstat()
                files.append([path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino])
            except OSError:
                files.append([path, "missing"])
        shown.append([str(tree), verdict.head, [doubt.kind for doubt in verdict.doubts], files])
    return hashlib.sha256(json.dumps(sorted(shown)).encode()).hexdigest()


def _answer(data: dict, key: str) -> dict:
    return data["answers"].get(key) or {}


def _pending(found: list[Item], data: dict) -> list[Item]:
    return [item for item in found if _answer(data, item.key).get("seen") != fingerprint(item)]


def _all_seen(proposed: list[Item]) -> str:
    return hashlib.sha256(json.dumps(sorted([item.key, fingerprint(item)] for item in proposed)).encode()).hexdigest()


def _short(digest: str) -> str:
    return digest[:12]


def _question(item: Item) -> dict:
    trees_named = ", ".join(Path(tree).name for tree in item.trees)
    text = (f"{item.key[len('work:'):]} ({trees_named}): " + " | ".join(item.evidence)
            + (" - proposed: archive and remove." if item.proposed else ""))
    return _q(f"{item.key}@{_short(fingerprint(item))}", "Leftovers", visible(text),
              [OPTIONS[name] for name in item.offered], free_text=True)


def all_questions(found: list[Item], data: dict) -> list[dict]:
    """Every question that applies now: "accept all" first when two or more proposals wait, then one per item."""
    pending = _pending(found, data)
    proposed = [item for item in pending if item.proposed]
    out = []
    if len(proposed) >= 2 and _answer(data, ALL).get("seen") != _all_seen(proposed):
        out.append(_q(f"{ALL}@{_short(_all_seen(proposed))}", "All",
                      f"Accept all {len(proposed)} proposals as shown below (archive and remove)?", [
            ("yes", "Accept all as proposed", "Archive and remove every item below that proposes it."),
            ("no", "One by one", "Answer each item on its own.")]))
    return out + [_question(item) for item in pending]


def next_round(found: list[Item], data: dict) -> list[dict]:
    return all_questions(found, data)[:MAX_PER_ROUND]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(found: list[Item], data: dict, qid: str, values: list[str]) -> str:
    current = all_questions(found, data)
    question = next((asked for asked in current if asked["id"] == qid), None)
    if question is None:
        key = qid.rsplit("@", 1)[0]
        if any(asked["id"].rsplit("@", 1)[0] == key for asked in current):
            raise AnswerError(f"`{visible(key)}` changed since it was shown; nothing recorded - run `flotilla gc "
                              "next` and ask again")
        raise AnswerError(f"no question `{visible(qid)}` applies now; run `flotilla gc next`")
    key = qid.rsplit("@", 1)[0]
    allowed = {option["value"] for option in question["options"]}
    if len(values) > 1 and question["free_text"] and not set(values) <= allowed:
        values = [" ".join(value.strip() for value in values if value and value.strip())]
    value = validate_answer(question, values)
    if key == ALL:
        proposed = [item for item in _pending(found, data) if item.proposed]
        data["answers"][ALL] = {"value": value, "seen": _all_seen(proposed), "at": _now()}
        if value == "yes":
            for item in proposed:
                data["answers"][item.key] = {"value": item.proposed, "seen": fingerprint(item), "at": _now()}
            return f"recorded {ALL}: {len(proposed)} item(s) to archive and remove"
        return f"recorded {ALL}: {value}"
    item = next(item for item in found if item.key == key)
    if value not in allowed:
        data["answers"][key] = {"value": "words", "words": value, "seen": fingerprint(item), "at": _now()}
        return f"recorded {visible(key)}: in your own words - a session carries them out as ordinary work"
    data["answers"][key] = {"value": value, "seen": fingerprint(item), "at": _now()}
    return f"recorded {visible(key)}: {value}"


def waiting(found: list[Item], data: dict) -> list[str]:
    """Items answered in the person's own words: work for a session, then the trees are judged again."""
    out = []
    for item in found:
        answer = _answer(data, item.key)
        if answer.get("value") == "words" and answer.get("seen") == fingerprint(item):
            out.append(f"{item.key}: carry out the person's words as ordinary work, then the tree is judged again: "
                       f"\"{visible(answer.get('words', ''))}\"")
    return out
