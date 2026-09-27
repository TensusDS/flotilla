"""Shared fixtures for watcher tests: sessions, rows and posts, without the world."""

import datetime as dt
from pathlib import Path

from flotilla.core.census import Session
from flotilla.ledger.model import Row
from flotilla.posts import install_templates, load_posts

PR = {"flow": {"mode": "pr"}, "review": {"depth": "every"}}
AT = "2026-09-27T10:00:00+00:00"
NOW = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.timezone.utc)


def sess(name, kind="background", state="blocked", status=None, sid=None, pid=None):
    return Session(name=name, session_id=sid or f"id-{name.replace(' ', '-')}", kind=kind, pid=pid, short_id=None,
                   status=status, state=None if kind == "interactive" else state, cwd="/", started_at_ms=None)


def row(id="r1", **fields):
    return Row(id=id, **{"branch": "feat/x", "owner": "main session 1", "state": "claimed", "updated_at": AT,
                         **fields})


def rows(*items):
    return {item.id: item for item in items}


def kinds(items):
    return [(item.kind, item.branch) for item in items]


def posts(tmp_path):
    install_templates(Path(tmp_path))
    return load_posts(Path(tmp_path))


def onboarded(tmp_path):
    folder = Path(tmp_path) / ".flotilla"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    return Path(tmp_path)
