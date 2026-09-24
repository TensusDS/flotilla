"""The ledger service and the moves that open and end rows: claim, reserve, release.

Every move follows one shape: resolve and check the caller's post, then inside the log's lock re-read the rows,
check the transition, the row rule and the evidence, and append one event. Nothing else writes the log.
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path

from flotilla import __version__
from flotilla.core.census import CensusUnavailable, read_census
from flotilla.ledger import gitq
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row, fold, make_event, next_row_id, now_iso
from flotilla.ledger.transitions import next_state
from flotilla.posts import PostError, post_for_session


class Ledger:
    def __init__(self, *, store, root, repo_key: str, profile: dict, posts: dict, state_dir, run=subprocess.run,
                 census=None, clock=None, version: str = __version__, rules: str = ""):
        self.store = store
        self.root = Path(root)
        self.repo_key = repo_key
        self.profile = profile
        self.posts = posts
        self.state_dir = Path(state_dir)
        self.run = run
        self.census = census
        self.clock = clock
        self.version = version
        self.rules = rules

    @property
    def trunk(self) -> str:
        return (self.profile.get("trunk") or {}).get("branch", "main")

    def now(self) -> str:
        return now_iso(self.clock() if self.clock else None)

    def rows(self) -> dict[str, Row]:
        return fold(self.store.read(self.repo_key).records)

    def owner_post(self, row: Row) -> str:
        try:
            post = post_for_session(self.posts, row.owner)
        except PostError:
            return ""
        return post.name if post else ""

    def live_names(self) -> set[str]:
        try:
            sessions = (self.census or read_census)()
        except CensusUnavailable as err:
            raise MoveRefused(f"could not ask which sessions are alive: {err}") from err
        return {session.name for session in sessions if session.name}

    @contextlib.contextmanager
    def session(self):
        with self.store.transaction(self.repo_key) as tx:
            yield LedgerSession(self, tx)


class LedgerSession:
    def __init__(self, ledger: Ledger, tx):
        self.ledger = ledger
        self.tx = tx
        self.rows = fold(tx.read().records)

    def open_row(self, branch: str) -> Row | None:
        return next((row for row in reversed(list(self.rows.values())) if row.branch == branch and row.is_open), None)

    def need_open_row(self, branch: str) -> Row:
        row = self.open_row(branch)
        if row is None:
            raise MoveRefused(f"no open ledger row for `{branch}`; claim it first (`flotilla work claim {branch}`)")
        return row

    def next_state(self, row: Row, move: str) -> str:
        return next_state(row.state, move, self.ledger.profile, self.ledger.owner_post(row))

    def append(self, actor: Actor, row_id: str, move: str, state: str, *, fields: dict | None = None,
               evidence: dict | None = None) -> Row:
        fields = dict(fields or {})
        before = self.rows.get(row_id)
        if before is not None and before.state != state and (before.waiting_on or before.note):
            fields.setdefault("waiting_on", "")   # a wait belongs to the state it was recorded in
            fields.setdefault("note", "")
        event = make_event(row=row_id, move=move, state=state, by=actor.name,
                           post=actor.post.name if actor.post else "", via=actor.via, fields=fields,
                           evidence=evidence or {}, at=self.ledger.now(), plugin=self.ledger.version,
                           caller=actor.caller, rules=self.ledger.rules)
        self.tx.append(event)
        self.rows = fold(self.tx.read().records)
        return self.rows[row_id]


def check_claim(rows: dict[str, Row], branch: str, *, ref: str = "", also: str = "", requires=()) -> list[str]:
    if not branch.strip():
        raise MoveRefused("a claim names a branch")
    for row in rows.values():
        if row.is_open and row.branch == branch:
            raise MoveRefused(f"`{branch}` is already claimed by {row.owner} (row {row.id}, {row.state})")
    if ref and not also:
        for row in rows.values():
            if row.is_open and row.ref == ref:
                raise MoveRefused(f"`{ref}` is already being worked on by {row.owner} in `{row.branch}` "
                                  f"(row {row.id}); pass --also \"<why>\" to work on it too")
    ids = []
    for wanted in requires:
        target = rows.get(wanted) or next((row for row in reversed(list(rows.values()))
                                           if row.branch == wanted and row.is_open), None)
        if target is None:
            raise MoveRefused(f"--requires `{wanted}`: no such row or open branch")
        ids.append(target.id)
    return ids


def claim(ledger: Ledger, actor: Actor, branch: str, *, tree: str = "", ref: str = "", requires=(),
          also: str = "", base: str | None = None) -> Row:
    require_may(actor, "claim", ledger.posts)
    with ledger.session() as s:
        ids = check_claim(s.rows, branch, ref=ref, also=also, requires=requires)
        if base is None:
            base = gitq.fork_point(ledger.root, branch, ledger.trunk, run=ledger.run) or ""
        return append_claim(s, actor, branch, tree=tree, base=base, ref=ref, ids=ids, also=also)


def append_claim(s: LedgerSession, actor: Actor, branch: str, *, tree: str, base: str, ref: str, ids: list,
                 also: str) -> Row:
    fields = {"branch": branch, "owner": actor.name, "tree": tree, "base": base, "ref": ref, "requires": ids}
    return s.append(actor, next_row_id(s.rows), "claim", "claimed", fields=fields,
                    evidence={"also": also} if also else {})


def reserve(ledger: Ledger, actor: Actor, branch: str, *, tree: str = "") -> Row:
    require_may(actor, "reserve", ledger.posts)
    with ledger.session() as s:
        held = [row for row in s.rows.values() if row.is_open and row.state == "reserved" and row.owner == actor.name]
        if held:
            raise MoveRefused(f"{actor.name} already holds a post row ({held[0].id}, `{held[0].branch}`)")
        check_claim(s.rows, branch)
        return s.append(actor, next_row_id(s.rows), "reserve", "reserved",
                        fields={"branch": branch, "owner": actor.name, "tree": tree})


def release(ledger: Ledger, actor: Actor, branch: str, *, why: str) -> Row:
    require_may(actor, "release", ledger.posts)
    if not why.strip():
        raise MoveRefused("a release says why the work will not happen (--why)")
    current = next((row for row in reversed(list(ledger.rows().values())) if row.branch == branch and row.is_open), None)
    if current is not None and current.owner != actor.name and current.owner in ledger.live_names():
        raise MoveRefused(f"{current.owner} is alive; only they release their own work")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "release")
        if row.owner != actor.name and (current is None or row.id != current.id):
            raise MoveRefused(f"`{branch}` changed while this move was checked; run it again")
        return s.append(actor, row.id, "release", state, evidence={"why": why.strip()})
