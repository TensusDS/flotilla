"""Shared fixtures for organize tests: a project with a published profile, and a fake GitHub that remembers what it
was asked and changes its own state the way GitHub does (retarget, close, unstack)."""

import json
from pathlib import Path

from ledgerkit import PROFILE, commit, fake_gh, git, repo_with_origin


def pull(number, branch, base, head, *, author="me", draft=False, fork=False, title="", body=""):
    """One pull request as `gh pr list --json` returns it."""
    return {"number": number, "headRefName": branch, "headRefOid": head, "baseRefName": base,
            "author": {"login": author}, "isDraft": draft, "isCrossRepository": fork,
            "title": title or f"work on {branch}", "body": body}


def stack(number, *members, base="main", merged=()):
    """One native stack as `GET /repos/{owner}/{repo}/stacks` returns it (organize's probe, 2026-10-20)."""
    return {"number": number, "open": True, "base": {"ref": base},
            "pull_requests": [{"number": n, "state": "closed" if n in merged else "open",
                               "merged_at": "2026-10-20T00:00:00Z" if n in merged else None,
                               "head": {"ref": f"b{n}", "sha": "0" * 40}} for n in members]}


class Hub:
    """A fake GitHub. `stuck` holds pull requests an unstack leaves stacked (queued, or with auto-merge on)."""

    def __init__(self, pulls=(), stacks=(), login="me", stuck=(), fail=(), merged=()):
        self.pulls = {item["number"]: dict(item) for item in pulls}
        self.merged = [{"headRefName": name} for name in merged]
        self.stacks = [dict(item) for item in stacks]
        self.login = login
        self.stuck = set(stuck)
        self.fail = set(fail)          # first words of calls that fail: {"pr list"}, {"api user"}
        self.calls = []
        self.run = fake_gh(self.handle, self.calls)

    def handle(self, args):
        said = " ".join(args[:2])
        if said in self.fail or (args and args[0] in self.fail):
            return 1, "", "gh: could not reach api.github.com"
        if args[:2] == ["pr", "list"]:
            if "merged" in args:
                return 0, self.merged
            return 0, [item for item in self.pulls.values() if item.get("state", "OPEN") == "OPEN"]
        if args[:2] == ["pr", "view"]:
            item = self.pulls[int(args[2])]
            return 0, {"state": item.get("state", "OPEN"), "headRefOid": item["headRefOid"],
                       "baseRefName": item["baseRefName"], "headRefName": item["headRefName"]}
        if args[:2] == ["pr", "edit"] and "--base" in args:
            self.pulls[int(args[2])]["baseRefName"] = args[args.index("--base") + 1]
            return 0, ""
        if args[:2] == ["pr", "close"]:
            self.pulls[int(args[2])]["state"] = "CLOSED"
            return 0, ""
        if args[:1] == ["api"]:
            return self.api(args[1:])
        return 1, "", f"gh: unexpected call {args}"

    def api(self, args):
        words = [word for word in args if not word.startswith("-")]
        path = next((word for word in words if word.startswith(("repos/", "user"))), "")
        if path == "user":
            return 0, self.login
        if path.endswith("/unstack") and ("POST" in args):
            number = int(path.split("/")[-2])
            for item in self.stacks:
                if item["number"] == number:
                    item["pull_requests"] = [member for member in item["pull_requests"]
                                             if member["number"] in self.stuck or member["state"] != "open"]
                    item["open"] = any(member["state"] == "open" for member in item["pull_requests"])
            return 0, ""
        if path.endswith("/stacks"):
            return 0, self.stacks
        return 1, "", f"gh: unexpected api call {args}"


def project(tmp_path, profile=None, published=True) -> Path:
    """A repository with an origin on GitHub's URL shape and, when `published`, a profile on origin's trunk."""
    root = repo_with_origin(tmp_path)
    git(root, "config", "user.email", "me@example.invalid")
    git(root, "config", "user.name", "me")
    if published:
        (root / ".flotilla").mkdir()
        data = profile or {**PROFILE, "pr": {"merge_method": "merge"}}
        (root / ".flotilla" / "project.toml").write_text(_toml(data), encoding="utf-8")
        git(root, "add", ".flotilla")
        commit(root, "chore: onboard flotilla (profile and posts)")
        git(root, "push", "-q", "origin", "main")
    return root


def _toml(data: dict) -> str:
    from flotilla.onboard.tomlw import render_toml
    return render_toml(data)


def as_json(value) -> str:
    return json.dumps(value)


def stacked_branches(root, *names, start="main", push=True):
    """Branches each cut from the one before (the first from `start`), one commit each; pushed to origin.
    Returns {name: head sha}."""
    heads, base = {}, start
    for name in names:
        git(root, "checkout", "-q", base)
        git(root, "checkout", "-q", "-b", name)
        heads[name] = commit(root, f"work on {name}", name.replace("/", "_") + ".txt")
        base = name
    git(root, "checkout", "-q", "main")
    if push:
        git(root, "push", "-q", "origin", *names)
    return heads


def chain_pulls(heads, *, start="main", author="me"):
    """The pull requests of a chain made by `stacked_branches`, numbered from 42."""
    found, base = [], start
    for number, (name, head) in enumerate(heads.items(), start=42):
        found.append(pull(number, name, base, head, author=author))
        base = name
    return found


MERGE_PROFILE = {**PROFILE, "pr": {"merge_method": "merge"}}
