"""Shared fixtures for guard tests: repositories, segments, receipts."""

import subprocess
from pathlib import Path

from flotilla.guards import shell

IDENTITY = ("-c", "user.email=t@example.invalid", "-c", "user.name=t")


def git(cwd, *args) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def plain_repo(tmp_path) -> Path:
    root = Path(tmp_path) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "f.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "f.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "base")
    return root


def first(command, cwd):
    return next(s for s in shell.segments(command, cwd) if s.program in ("git", "sed", "gsed", "gh"))


from flotilla.core import repo as repo_mod

TIER = '[[tests.tier]]\nname = "unit"\ncommand = "true"\nrequired_for = ["push"]\n'


def origin_repo(tmp_path) -> Path:
    seed = Path(tmp_path) / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    (seed / "f.txt").write_text("base\n", encoding="utf-8")
    git(seed, "add", "f.txt")
    git(seed, *IDENTITY, "commit", "-q", "-m", "base")
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(Path(tmp_path) / "origin.git"))
    root = Path(tmp_path) / "app"
    git(tmp_path, "clone", "-q", str(Path(tmp_path) / "origin.git"), str(root))
    return root


def onboarded(tmp_path, guards=("revert", "line_edit", "push_receipt"), extra="", push=True, off=()) -> Path:
    root = origin_repo(tmp_path)
    flags = "".join(f"{name} = true\n" for name in guards) + "".join(f"{name} = false\n" for name in off)
    text = (f'schema = 1\n\n[trunk]\nbranch = "main"\n\n[flow]\nmode = "direct"\n\n[guards]\n{flags}\n'
            f"{TIER}{extra}")
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text(text, encoding="utf-8")
    git(root, "add", ".flotilla")
    git(root, *IDENTITY, "commit", "-q", "-m", "onboard")
    if push:
        git(root, "push", "-q", "origin", "main")
    return root


def receipt(root, state):
    from flotilla.guards.rules import rules_for
    from flotilla.ledger import receipts
    profile, _ = rules_for(root)
    return receipts.run_receipt(Path(root), state=Path(state), repo_key=repo_mod.identify(Path(root)).key,
                                purpose="push", profile=profile, timeout=60)
