"""Posts: which session may make which ledger move (spec, section 7.3).

A post is a Claude Code agent definition with flotilla keys in its frontmatter: `name_pattern` (for example
"review session {n}"), `may` (the ledger moves the post may make) and `writes_one_copy` (the single writer of
one-copy resources). Project posts live in `.flotilla/posts/*.md`; onboarding copies them from the plugin's
`templates/posts/` and never overwrites a post the project has edited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MOVES = ("reserve", "claim", "hand", "moved", "fix", "assign", "recuse", "take", "accept", "queue", "land",
         "inbatch", "ship", "walked", "broke", "close", "release", "offledger", "wait", "hold", "unhold", "adopt")
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "posts"
POSTS_DIR = Path(".flotilla") / "posts"


class PostError(ValueError):
    """A post file that cannot be used; the message names the file."""


@dataclass(frozen=True)
class Post:
    name: str
    name_pattern: str
    may: frozenset
    writes_one_copy: bool
    template_version: int
    path: Path
    body: str

    def matches(self, session_name: str) -> bool:
        regex = "^" + re.escape(self.name_pattern).replace(re.escape("{n}"), r"\d+") + "$"
        return re.match(regex, session_name) is not None


def _value(raw: str):
    if raw.startswith("[") and raw.endswith("]"):
        return [item.strip().strip("'\"") for item in raw[1:-1].split(",") if item.strip()]
    if raw in ("true", "false"):
        return raw == "true"
    if raw.isdigit():
        return int(raw)
    return raw.strip("'\"")


def parse_frontmatter(text: str, source: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise PostError(f"{source}: no frontmatter")
    end = text.find("\n---\n", 4)
    if end == -1:
        raise PostError(f"{source}: the frontmatter is not closed")
    meta: dict = {}
    for number, line in enumerate(text[4:end].splitlines(), start=2):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise PostError(f"{source}:{number}: expected `key: value`")
        meta[key.strip()] = _value(value.strip())
    return meta, text[end + 5:]


def load_post(path: Path) -> Post:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        raise PostError(f"{path}: cannot be read: {err}") from err
    meta, body = parse_frontmatter(text, str(path))
    name = meta.get("name")
    if name != path.stem:
        raise PostError(f"{path}: `name: {name}` differs from the file name `{path.stem}`")
    pattern = meta.get("name_pattern")
    if not isinstance(pattern, str) or pattern.count("{n}") != 1:
        raise PostError(f"{path}: `name_pattern` must contain `{{n}}` exactly once")
    may = meta.get("may", [])
    if not isinstance(may, list):
        raise PostError(f"{path}: `may` must be a list like [claim, hand]")
    unknown = sorted(set(may) - set(MOVES))
    if unknown:
        raise PostError(f"{path}: `may` names unknown moves: {', '.join(unknown)}")
    one_copy = meta.get("writes_one_copy", False)
    if not isinstance(one_copy, bool):
        raise PostError(f"{path}: `writes_one_copy` must be true or false")
    version = meta.get("template_version", 0)
    if isinstance(version, bool) or not isinstance(version, int):
        raise PostError(f"{path}: `template_version` must be a whole number")
    return Post(name, pattern, frozenset(may), one_copy, version, path, body)


def _folder(root: Path) -> Path:
    folder = Path(root) / POSTS_DIR
    if folder.is_symlink():
        raise PostError(f"{folder} is a symlink; posts are read and written only inside the repository")
    return folder


def load_posts(root: Path) -> dict[str, Post]:
    folder = _folder(root)
    if not folder.is_dir():
        return {}
    return {post.name: post for post in (load_post(path) for path in sorted(folder.glob("*.md")))}


def post_for_session(posts: dict[str, Post], session_name: str) -> Post | None:
    hits = [post for post in posts.values() if post.matches(session_name)]
    if len(hits) > 1:
        raise PostError(f"`{session_name}` matches more than one post: {', '.join(sorted(p.name for p in hits))}")
    return hits[0] if hits else None


def install_templates(root: Path, *, template_dir: Path = TEMPLATE_DIR) -> list[Path]:
    folder = _folder(root)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for template in sorted(Path(template_dir).glob("*.md")):
        target = folder / template.name
        if target.exists() or target.is_symlink():
            continue
        target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(target)
    return written
