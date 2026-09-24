"""Who is making a move, and whether their post allows it.

The caller is the live session the census lists as an ancestor of this process, or the name given with `--as` (a
person in a terminal, or a tool acting for a named session); the event records which. When neither answers, the
move is refused: nothing is recorded under a guessed name.
"""

from __future__ import annotations

from dataclasses import dataclass

from flotilla.core import platform as plat
from flotilla.core.census import CensusUnavailable, read_census
from flotilla.core.identity import find_calling_session
from flotilla.ledger.errors import ActorUnknown, MoveRefused
from flotilla.posts import Post, PostError, post_for_session


@dataclass(frozen=True)
class Actor:
    name: str
    post: Post | None
    via: str


def _post(posts: dict, name: str) -> Post | None:
    try:
        return post_for_session(posts, name)
    except PostError as err:
        raise MoveRefused(str(err)) from err


def resolve_actor(posts: dict, *, as_name: str | None = None, census=None, parent_of=None,
                  start_pid: int | None = None) -> Actor:
    if as_name:
        return Actor(as_name, _post(posts, as_name), "as")
    try:
        sessions = (census or read_census)()
    except CensusUnavailable as err:
        raise ActorUnknown(f"could not identify the calling session: {err}; pass --as <session name>") from err
    if parent_of is None:
        source = plat.probe().parent_pid_source
        parent_of = lambda pid: plat.parent_pid(pid, source)  # noqa: E731
    found = find_calling_session(sessions, parent_of=parent_of, start_pid=start_pid)
    if found is None or not found.name:
        raise ActorUnknown("this process is not inside a Claude Code session the census lists; "
                           "pass --as <session name>")
    return Actor(found.name, _post(posts, found.name), "census")


def require_may(actor: Actor, move: str, posts: dict) -> None:
    if not posts:
        raise MoveRefused("this project has no posts in .flotilla/posts/; run `flotilla onboard write`, or add them")
    if actor.post is None:
        raise MoveRefused(f"`{actor.name}` matches no post's name pattern; posts: {', '.join(sorted(posts))}")
    if move not in actor.post.may:
        allowed = sorted(post.name for post in posts.values() if move in post.may)
        who = f"posts that may: {', '.join(allowed)}" if allowed else "no post may"
        raise MoveRefused(f"post `{actor.post.name}` may not `{move}`; {who}")
