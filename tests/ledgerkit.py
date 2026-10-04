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


def make_ledger(root, state, profile=None, live=DEFAULT_LIVE, posts=None, census=None, run=None, events=None,
                skip_events=None):
    import subprocess as _subprocess

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
                  census=census or (lambda: sessions), run=run or _subprocess.run, events=events,
                  skip_events=skip_events)


def drive(root, ledger, name="feat/x", *, to="accepted", owner="main session 1", reader="review session 1",
          requires=()):
    """Cut `name` with one commit and move its row through claim, hand, take and accept, stopping at `to`."""
    from flotilla.ledger import core, handover, reading

    branch(root, name, "work")
    row = core.claim(ledger, actor(ledger, owner), name, requires=requires)
    if to == "claimed":
        return row
    row = handover.hand(ledger, actor(ledger, owner), name)
    if to == "handed":
        return row
    reading.take(ledger, actor(ledger, reader), name)
    return reading.accept(ledger, actor(ledger, reader), name, reviewed=row.tip)


def actor(ledger, name):
    from flotilla.ledger.actor import resolve_actor
    return resolve_actor(ledger.posts, as_name=name, census=lambda: [])


IDENTITY = ("-c", "user.email=t@example.invalid", "-c", "user.name=t")


def merge(root, name, *, squash=False) -> str:
    """Merge branch `name` into the checked-out trunk of `root` (a real merge, or a squash) and return the new HEAD."""
    if squash:
        git(root, "merge", "-q", "--squash", name)
        git(root, *IDENTITY, "commit", "-q", "-m", f"squash {name}")
    else:
        git(root, *IDENTITY, "merge", "-q", "--no-ff", "-m", f"merge {name}", name)
    return git(root, "rev-parse", "HEAD")


def fake_gh(handler, calls=None):
    """A subprocess.run stand-in: `gh ...` goes to handler(args) -> (code, stdout); everything else runs for real."""
    import json as _json

    def run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd and cmd[0] == "gh":
            if calls is not None:
                calls.append(list(cmd[1:]))
            answer = handler(list(cmd[1:]))
            code, out = answer[0], answer[1]
            err = answer[2] if len(answer) > 2 else ("" if code == 0 else "gh: request failed")
            text = out if isinstance(out, str) else _json.dumps(out)
            return subprocess.CompletedProcess(cmd, code, text, err)
        return subprocess.run(cmd, **kwargs)
    return run


def shipped_direct(root, ledger, name="feat/x"):
    """Drive `name` to shipped in a direct-push project without CI: accept, queue, merge, land, push, ship."""
    from flotilla.ledger import delivery

    drive(root, ledger, name)
    sender = actor(ledger, "sender 1")
    delivery.queue(ledger, sender, name)
    merge(root, name)
    delivery.land(ledger, sender, name)
    git(root, "push", "-q", "origin", ledger.trunk)
    return delivery.ship(ledger, sender, name)
