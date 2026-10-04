"""Posts: which session may make which ledger move (spec, section 7.3).

A post is a Claude Code agent definition with flotilla keys in its frontmatter: `name_pattern` (for example
"review session {n}"), `may` (the ledger moves the post may make) and `writes_one_copy` (the single writer of
one-copy resources), and `plugins` (the plugins that bring MCP servers a seat of the post keeps; every other one is
turned off for it). Project posts live in `.flotilla/posts/*.md`; onboarding copies them from the plugin's
`templates/posts/` and never overwrites a post the project has edited.
"""

from __future__ import annotations

import dataclasses

import re
from dataclasses import dataclass
from pathlib import Path

MOVES = ("reserve", "claim", "hand", "moved", "fix", "assign", "recuse", "take", "accept", "queue", "land",
         "inbatch", "ship", "walked", "broke", "unbroke", "close", "release", "offledger", "wait", "hold",
         "unhold", "adopt", "urgent", "walkable", "vouch", "return")
PERMISSION_MODES = ("acceptEdits", "auto", "bypassPermissions", "default", "manual", "dontAsk", "plan")
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
    model: str = "inherit"
    permission_mode: str = ""
    plugins: tuple = ()   # the MCP-bearing plugins a seat of this post keeps; the rest are turned off (decision 154)
    plugins_written: bool = False   # whether the post says `plugins:` at all (a post older than the key does not)
    project: str = ""   # the fleet's name its seats carry (`worldcore-orchestrator 1`); "" for a profile without one

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
    model = meta.get("model", "inherit")
    if not isinstance(model, str) or not model:
        raise PostError(f"{path}: `model` must be a model name or `inherit`")
    mode = meta.get("permission_mode", "")
    if mode and mode not in PERMISSION_MODES:
        raise PostError(f"{path}: `permission_mode` must be one of {', '.join(PERMISSION_MODES)}")
    kept = meta.get("plugins", [])
    if not isinstance(kept, list):
        raise PostError(f"{path}: `plugins` must be a list of plugin ids like [playwright@claude-plugins-official]")
    return Post(name, pattern, frozenset(may), one_copy, version, path, body, model, mode, tuple(kept),
                "plugins" in meta)


def _folder(root: Path) -> Path:
    folder = Path(root) / POSTS_DIR
    if folder.is_symlink():
        raise PostError(f"{folder} is a symlink; posts are read and written only inside the repository")
    return folder


def load_posts(root: Path, *, project: str = "") -> dict[str, Post]:
    """The project's posts; with a fleet name, every pattern is prefixed with it (`worldcore-` + `orchestrator {n}`),
    so the project's seats count from 1 and another project's sessions match none of its posts."""
    folder = _folder(root)
    if not folder.is_dir():
        return {}
    posts = (load_post(path) for path in sorted(folder.glob("*.md")))
    if project:
        posts = (dataclasses.replace(post, name_pattern=f"{project}-{post.name_pattern}", project=project)
                 for post in posts)
    return {post.name: post for post in posts}


def post_for_session(posts: dict[str, Post], session_name: str) -> Post | None:
    hits = [post for post in posts.values() if post.matches(session_name)]
    if len(hits) > 1:
        raise PostError(f"`{session_name}` matches more than one post: {', '.join(sorted(p.name for p in hits))}")
    return hits[0] if hits else None


def behind(posts: dict, *, template_dir: Path = TEMPLATE_DIR) -> list[tuple[str, int, int]]:
    """(post, its template_version, the shipped one) for each post older than the template flotilla ships under the
    same name. `posts` are the ones the fleet reads - trunk's, not the files on disk. Post files are copied at
    onboarding and never overwritten, so nothing else says when one falls behind (review of 0.7.12); a post with no
    shipped template - the project's own - is not compared."""
    found = []
    for template in sorted(Path(template_dir).glob("*.md")):
        post = posts.get(template.stem)
        if post is None:
            continue
        shipped = load_post(template).template_version
        if post.template_version < shipped:
            found.append((template.stem, post.template_version, shipped))
    return found


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


def post_or_former(posts: dict[str, Post], session_name: str) -> str:
    """The post a session holds - or held under its former name, before the profile named the fleet
    (`main session 19` in a fleet now called `twosuns`). For saying who can take a gone session's move only: a
    former name holds no post today (twosuns field test of 0.6.7)."""
    try:
        found = post_for_session(posts, session_name)
    except PostError:
        return ""
    if found is not None:
        return found.name
    for post in posts.values():
        prefix = f"{post.project}-" if post.project else ""
        if prefix and post.name_pattern.startswith(prefix):
            former = dataclasses.replace(post, name_pattern=post.name_pattern[len(prefix):])
            if former.matches(session_name):
                return post.name
    return ""
