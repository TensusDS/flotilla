"""What a hook knows when it starts: which session it serves, that session's post, the census and the ledger.

The session is found by the `session_id` Claude Code passes to every hook, matched to the census `sessionId`
(measured 2026-09-27: they are the same id), and failing that by walking up the process parents to a pid the census
lists. Whatever could not be asked is kept as a reason, never as an empty answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Seconds per external call inside a hook; the timeouts in hooks/hooks.json leave room for it.
HOOK_CHECK_TIMEOUT = 3


@dataclass
class Context:
    root: Path
    session_id: str
    sessions: list | None = None
    census_error: str = ""
    me: object | None = None
    ledger: object | None = None
    ledger_error: str = ""
    rows: dict = field(default_factory=dict)
    project: list | None = None   # the sessions of this project; None: not narrowed, every session counts

    @property
    def live(self) -> set[str] | None:
        return None if self.sessions is None else {session.name for session in self.sessions if session.name}

    @property
    def project_live(self) -> set[str] | None:
        chosen = self.project if self.project is not None else self.sessions
        return None if chosen is None else {session.name for session in chosen if session.name}

    @property
    def profile(self) -> dict:
        return self.ledger.profile if self.ledger is not None else {}

    @property
    def posts(self) -> dict:
        return self.ledger.posts if self.ledger is not None else {}

    def post(self, name: str):
        from flotilla.posts import PostError, post_for_session
        try:
            return post_for_session(self.posts, name)
        except PostError:
            return None

    def post_of(self, name: str) -> str:
        found = self.post(name)
        return found.name if found is not None else ""

    @property
    def is_orchestrator(self) -> bool:
        return self.me is not None and self.post_of(self.me.name) == "orchestrator"

    def mine(self) -> list:
        from flotilla.watch import whose
        if self.me is None or self.ledger is None:
            return []
        post = self.post(self.me.name)
        return whose.mine(self.rows, self.profile, self.me.name, post=post.name if post else "",
                          may=post.may if post else frozenset(), owner_post=self.post_of, live=self.live)

    def fleet(self, now: float | None = None) -> list:
        from flotilla.broker import queue
        from flotilla.watch import fleet
        if self.sessions is None or self.ledger is None:
            return []
        questions = queue.live(self.ledger.state_dir, self.ledger.repo_key, now=now)
        breaks = fleet.open_breaks(self.ledger.state_dir, self.ledger.repo_key, self.rows, self.profile,
                                   self.post_of)
        return fleet.question_items(questions) + fleet.fleet(
            self.rows, self.profile, self.project if self.project is not None else self.sessions,
            post_of=self.post_of, breaks=breaks,
            asking={asked.session for asked in questions})


def this_session(sessions, session_id: str, *, parent_of=None, start_pid: int | None = None):
    if session_id:
        for session in sessions:
            if session.session_id == session_id:
                return session
    from flotilla.core import platform as plat
    from flotilla.core.identity import find_calling_session
    if parent_of is None:
        source = plat.probe().parent_pid_source
        parent_of = lambda pid: plat.parent_pid(pid, source)  # noqa: E731
    return find_calling_session(sessions, parent_of=parent_of, start_pid=start_pid)


def gather(root, session_id: str, *, census=None, open_ledger=None, parent_of=None,
           start_pid: int | None = None) -> Context:
    from flotilla.core.census import CensusUnavailable, read_census
    ctx = Context(root=Path(root), session_id=session_id or "")
    try:
        ctx.sessions = (census or (lambda: read_census(timeout=HOOK_CHECK_TIMEOUT)))()
    except CensusUnavailable as err:
        ctx.census_error = str(err)
    if ctx.sessions is not None:
        ctx.me = this_session(ctx.sessions, ctx.session_id, parent_of=parent_of, start_pid=start_pid)
    if open_ledger is None:
        from flotilla.ledger.commands import open_ledger
    try:
        ctx.ledger = open_ledger(Path(root))
        ctx.rows = ctx.ledger.rows()
    except Exception as err:  # noqa: BLE001 - a hook says what it could not read and goes on
        ctx.ledger, ctx.rows, ctx.ledger_error = None, {}, str(err) or type(err).__name__
    if ctx.sessions is not None and ctx.ledger is not None:
        from flotilla.ledger import project
        ctx.project = project.members(ctx.sessions, ctx.rows, project.roots(ctx.ledger.root))
    return ctx
