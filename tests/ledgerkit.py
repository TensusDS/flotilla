"""Shared fixtures for ledger tests: a repository with an origin, and a ledger over it."""

import subprocess
from pathlib import Path

PROFILE = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"}}
DEFAULT_LIVE = ("main session 1", "minor session 1", "review session 1", "review session 2", "sender 1",
                "orchestrator 1", "acceptance judge 1")


def git(cwd, *args) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit(cwd, message, name=None, text=None) -> str:
    if name:
        (Path(cwd) / name).write_text(text if text is not None else message + "\n", encoding="utf-8")
        git(cwd, "add", name)
    git(cwd, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


def repo_with_origin(tmp_path) -> Path:
    seed = Path(tmp_path) / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    commit(seed, "init", "README.md", "hello\n")
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(Path(tmp_path) / "origin.git"))
    root = Path(tmp_path) / "app"
    git(tmp_path, "clone", "-q", str(Path(tmp_path) / "origin.git"), str(root))
    return root


def branch(root, name, *messages) -> str:
    git(root, "checkout", "-q", "-b", name)
    tip = git(root, "rev-parse", "HEAD")
    for index, message in enumerate(messages):
        tip = commit(root, message, f"{name.replace('/', '_')}_{index}.txt")
    git(root, "checkout", "-q", "main")
    return tip


def make_ledger(root, state, profile=None, live=DEFAULT_LIVE, posts=None, census=None):
    from flotilla.core.census import Session
    from flotilla.core.repo import identify
    from flotilla.core.storage import LocalLogStore
    from flotilla.ledger.core import Ledger
    from flotilla.posts import TEMPLATE_DIR, load_post

    if posts is None:
        posts = {p.name: p for p in (load_post(path) for path in sorted(TEMPLATE_DIR.glob("*.md")))}
    sessions = [Session(name=name, session_id=name, kind="background", pid=None, short_id=None, status="idle",
                        state=None, cwd="", started_at_ms=None) for name in live]
    ident = identify(Path(root))
    return Ledger(store=LocalLogStore(Path(state) / "ledger"), root=ident.root, repo_key=ident.key,
                  profile=profile or PROFILE, posts=posts, state_dir=Path(state),
                  census=census or (lambda: sessions))


def actor(ledger, name):
    from flotilla.ledger.actor import resolve_actor
    return resolve_actor(ledger.posts, as_name=name, census=lambda: [])
