"""The ledger service and the moves that open and end rows: claim, reserve, release.

Every move follows one shape: resolve and check the caller's post, then inside the log's lock re-read the rows,
check the transition, the row rule and the evidence, and append one event. Nothing else writes the log.
A move that changes a row's state runs the project's `pre-<state>` event script inside the lock before its event
is written, and `post-<state>` after the lock is released.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from flotilla import __version__
from flotilla.core.census import CensusUnavailable, read_census
from flotilla.core.storage import LocalLogStore
from flotilla.ledger import events, gitq
from flotilla.ledger.actor import NO_CENSUS, Actor, require_may
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row, fold, make_event, next_row_id, now_iso
from flotilla.ledger.transitions import next_state
from flotilla.posts import PostError, post_for_session


class Ledger:
    def __init__(self, *, store, root, repo_key: str, profile: dict, posts: dict, state_dir, run=subprocess.run,
                 census=None, clock=None, version: str = __version__, rules: str = "",
                 events: dict | None = None, skip_events: dict | None = None):
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
        self.events = events or {}
        self.skip_events = skip_events or {}
        self.notices: list[str] = []

    @property
    def trunk(self) -> str:
        return (self.profile.get("trunk") or {}).get("branch", "main")

    @property
    def mode(self) -> str:
        return (self.profile.get("flow") or {}).get("mode", "local")

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
        if self.census is None and os.environ.get(NO_CENSUS):
            raise MoveRefused(f"{NO_CENSUS} is set, so the census is not asked and liveness is unknown")
        try:
            sessions = (self.census or read_census)()
        except CensusUnavailable as err:
            raise MoveRefused(f"could not ask which sessions are alive: {err}") from err
        return {session.name for session in sessions if session.name}

    @contextlib.contextmanager
    def session(self):
        with self.store.transaction(self.repo_key) as tx:
            s = LedgerSession(self, tx)
            yield s
        for name, data in s.post:
            self.fire_post(name, data)

    def _log_failure(self, name: str, data: dict, outcome) -> None:
        LocalLogStore(self.state_dir / "events").append(self.repo_key, {
            "at": self.now(), "event": name, "branch": data["row"].get("branch", ""), "status": outcome.status,
            "text": outcome.text})

    def fire_pre(self, name: str, data: dict) -> dict:
        """Run `pre-<state>`; return evidence to record, or refuse the move."""
        script = self.events.get(name)
        if script is None:
            return {}
        if name in self.skip_events:
            return {"skipped_events": {name: self.skip_events[name]}}
        outcome = events.run_event(name, script, data, cwd=self.root, run=self.run)
        if outcome.status == events.OK:
            return {}
        self._log_failure(name, data, outcome)
        where, branch = f"{events.EVENTS_DIR}/{name}", data["row"].get("branch", "")
        if outcome.status == events.REJECTED:
            raise MoveRefused(f"`{where}` rejected the move: {outcome.text or '(no output)'}")
        raise MoveRefused(f"`{where}` failed ({outcome.text}); nothing was recorded. Reproduce: `flotilla events "
                          f"run {name} --row {branch}`. On a person's decision: --skip-event {name} --skip-why "
                          "\"<reason>\"")

    def fire_post(self, name: str, data: dict) -> None:
        script = self.events.get(name)
        if script is None or name in self.skip_events:
            return
        outcome = events.run_event(name, script, data, cwd=self.root, run=self.run)
        if outcome.status != events.OK:
            self._log_failure(name, data, outcome)
            self.notices.append(f"`{events.EVENTS_DIR}/{name}` failed after the move was recorded "
                                f"({outcome.text or 'no output'}); the move stands")


class LedgerSession:
    def __init__(self, ledger: Ledger, tx):
        self.ledger = ledger
        self.tx = tx
        self.rows = fold(tx.read().records)
        self.post: list[tuple[str, dict]] = []

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
        evidence = dict(evidence or {})
        before = self.rows.get(row_id)
        if before is not None and before.state != state and (before.waiting_on or before.note):
            fields.setdefault("waiting_on", "")   # a wait belongs to the state it was recorded in
            fields.setdefault("note", "")
        changes = before is None or before.state != state
        data = None
        if changes:
            data = events.payload(event=f"pre-{state}", move=move, state=state, repo=self.ledger.repo_key,
                                  trunk=self.ledger.trunk, by=actor.name,
                                  post=actor.post.name if actor.post else "",
                                  row=events.row_view(before, row_id, fields), evidence=evidence)
            evidence.update(self.ledger.fire_pre(f"pre-{state}", data))
        event = make_event(row=row_id, move=move, state=state, by=actor.name,
                           post=actor.post.name if actor.post else "", via=actor.via, fields=fields,
                           evidence=evidence, at=self.ledger.now(), plugin=self.ledger.version,
                           caller=actor.caller, rules=self.ledger.rules)
        self.tx.append(event)
        self.rows = fold(self.tx.read().records)
        if changes:
            self.post.append((f"post-{state}", {**data, "event": f"post-{state}", "evidence": evidence}))
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
