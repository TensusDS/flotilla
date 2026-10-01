"""The push receipt guard: nothing reaches trunk without a green run over exactly what reaches it (spec, section 10).

A door is `git push`, `gh pr create`, `gh pr merge` or `gh workflow run`. Each carries revisions: what a push
takes to trunk or to a tag, the head of the pull request, HEAD for a workflow run. Every such revision needs a
green `push` receipt over exactly it, and the CI workflow at that revision must be the one its profile records. A
push to a branch other than trunk needs none: it lands nowhere, and the receipt is asked where it lands.

`FLOTILLA_GATE_OVERRIDE="<why>"` set at the head of the door's own segment, or in the environment, lets a push
through knowingly, and the reason is recorded; anywhere else in the line it opens nothing. The guard's own failure
refuses: what was shipped cannot be taken back. What the text hides (a push inside a script) is caught by the git
`pre-push` hook, which asks git what is pushed (`pre_push`).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

from flotilla.guards import Finding

GUARD = "push_receipt"
OVERRIDE = "FLOTILLA_GATE_OVERRIDE"
ZERO = "0" * 40
HARMLESS = ("-n", "--dry-run", "-d", "--delete", "-h", "--help")
PUSH_VALUES = ("-o", "--push-option", "--repo", "--receive-pack", "--exec")
BROAD = ("--all", "--mirror", "--tags", "--follow-tags")
MERGE_VALUES = ("-t", "--subject", "-b", "--body", "-F", "--body-file", "-A", "--author-email",
                "--match-head-commit")


class Unknown(RuntimeError):
    """The guard could not tell which revisions a door carries."""


@dataclass(frozen=True)
class Door:
    kind: str
    segment: object
    directory: Path | None


def _git(directory, *args, run):
    done = run(["git", "-C", str(directory), *args], capture_output=True, text=True, check=False, timeout=10)
    return done.stdout.strip() if done.returncode == 0 else None


def _resolve(directory, rev, run) -> str | None:
    return _git(directory, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", run=run) or None


def _need(sha, what) -> str:
    if not sha:
        raise Unknown(f"git could not resolve `{what}`")
    return sha


def _value(args, *names):
    for i, arg in enumerate(args):
        for name in names:
            if arg == name and i + 1 < len(args):
                return args[i + 1]
            if name.startswith("--") and arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


def door(segment) -> Door | None:
    found = segment.git()
    if found is not None:
        verb, args, directory = found
        if verb != "push" or any(arg in HARMLESS for arg in args):
            return None
        return Door("git push", segment, directory)
    words = segment.words
    if segment.program == "gh" and len(words) >= 3:
        kind = f"gh {words[1]} {words[2]}"
        if kind in ("gh pr create", "gh pr merge", "gh workflow run"):
            if any(arg in ("-h", "--help", "--dry-run") for arg in words[3:]):
                return None
            return Door(kind, segment, segment.cwd)
    return None


def _push_revisions(args, directory, trunk, run) -> list[tuple[str, str]]:
    positional, broad, i = [], False, 0
    while i < len(args):
        arg = args[i]
        if arg in PUSH_VALUES:
            i += 2
            continue
        broad = broad or arg in BROAD
        if not arg.startswith("-"):
            positional.append(arg)
        i += 1
    branch = _git(directory, "symbolic-ref", "-q", "--short", "HEAD", run=run)
    if broad:
        return [("HEAD", _need(_resolve(directory, "HEAD", run), "HEAD"))]
    refspecs = positional[1:]
    if not refspecs:
        if branch and branch != trunk:
            return []
        return [(branch or "HEAD", _need(_resolve(directory, "HEAD", run), "HEAD"))]
    found = []
    for spec in refspecs:
        src, sep, dst = spec.lstrip("+").partition(":")
        if sep and not src:
            continue                     # `:branch` deletes it; nothing is carried
        dst = dst if sep else src
        if dst == "HEAD":
            dst = branch or trunk        # detached: where it lands cannot be told, so it is asked as trunk
        dst = dst.removeprefix("refs/heads/")
        tag = dst.startswith("refs/tags/") or _git(directory, "show-ref", "--verify", "--quiet",
                                                    f"refs/tags/{dst}", run=run) is not None
        if not tag and dst != trunk:
            continue                     # a branch other than trunk lands nowhere
        found.append((dst, _need(_resolve(directory, src, run), src)))
    return found


def revisions(d: Door, trunk: str, *, run=subprocess.run) -> list[tuple[str, str]]:
    """(label, sha) for every revision this door carries where a receipt is asked; raises Unknown."""
    if d.directory is None:
        raise Unknown("could not tell which tree this runs in (a directory named through a variable); run it "
                      "with `git -C <path>`, or from the tree")
    words = list(d.segment.words)
    if d.kind == "git push":
        return _push_revisions(d.segment.git()[1], d.directory, trunk, run)
    rest = words[3:]
    if _value(rest, "-R", "--repo") is not None:
        raise Unknown("names a repository with -R/--repo; run it from that repository's tree, where its receipts "
                      "are")
    if d.kind == "gh pr create":
        head = _value(rest, "-H", "--head")
        rev = head.split(":", 1)[-1] if head else "HEAD"
        return [(f"PR head {rev}", _need(_resolve(d.directory, rev, run), rev))]
    if d.kind == "gh pr merge":
        target, i = None, 0
        while i < len(rest):
            if rest[i] in MERGE_VALUES:
                i += 2
                continue
            if not rest[i].startswith("-") and target is None:
                target = rest[i]
            i += 1
        argv = ["gh", "pr", "view", *([target] if target else []), "--json", "headRefOid", "-q", ".headRefOid"]
        try:
            done = run(argv, cwd=str(d.directory), capture_output=True, text=True, check=False, timeout=10)
            sha = done.stdout.strip() if done.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            sha = ""
        sha = _need(sha, "the pull request's head (gh pr view)")
        pinned = _value(rest, "--match-head-commit")
        if pinned != sha:   # gh merges the head the PR has when it merges; --auto, a later one (F20, F22)
            raise Unknown(f"merges whatever head the pull request has then, not the one whose receipt was checked; "
                          f"pin it: add --match-head-commit {sha}")
        return [(f"PR {target or '(this branch)'}", sha)]
    return [("HEAD", _need(_resolve(d.directory, "HEAD", run), "HEAD"))]


def workflow_at(directory, sha, *, run=subprocess.run) -> str | None:
    """The workflow digest at a revision, computed exactly as onboarding computes it from the working tree."""
    listing = _git(directory, "ls-tree", "--full-tree", sha, "--", ".github/workflows/", run=run)
    files = []
    for line in (listing or "").splitlines():
        meta, _, path = line.partition("\t")
        mode, kind, obj = meta.split()
        if kind == "blob" and mode != "120000" and path.endswith((".yml", ".yaml")):
            files.append((path, obj))
    if not files:
        return None
    digest = hashlib.sha256()
    for path, obj in sorted(files):
        blob = run(["git", "-C", str(directory), "cat-file", "blob", obj], capture_output=True, check=False,
                   timeout=10)
        digest.update(path.encode("utf-8") + b"\0")
        digest.update((blob.stdout if blob.returncode == 0 else b"unreadable") + b"\0")
    return "sha256:" + digest.hexdigest()


def workflow_drift(directory, sha, *, run=subprocess.run) -> str:
    done = run(["git", "-C", str(directory), "show", f"{sha}:.flotilla/project.toml"], capture_output=True,
               text=True, check=False, timeout=10)
    if done.returncode != 0:
        return ""
    try:
        recorded = (tomllib.loads(done.stdout).get("ci") or {}).get("workflow_fingerprint")
    except tomllib.TOMLDecodeError:
        return ""
    if not recorded or workflow_at(directory, sha, run=run) == recorded:
        return ""
    return (f"the CI workflow at {sha[:7]} is not the one its .flotilla/project.toml records; run /flotilla:check "
            "and update the profile in the same revision")


def _failures(pairs, *, directory, profile, state_dir, repo_key, run) -> list[str]:
    from flotilla.ledger import receipts
    found = []
    for label, sha in pairs:
        ok, why = receipts.check_receipt(state=Path(state_dir), repo_key=repo_key, sha=sha, purpose="push",
                                         profile=profile)
        if not ok:
            found.append(f"{label}: {why}")
        drift = workflow_drift(directory, sha, run=run)
        if drift:
            found.append(f"{label}: {drift}")
    return found


def _unapproved(root, profile: dict, pairs) -> list[str]:
    """Where a person authorizes merges, what goes to trunk is work they approved (F24): each revision is asked of
    the ledger, which counts only approved rows; a commit no approved row covers closes the door."""
    if (profile.get("flow") or {}).get("merge_authorized_by") != "human" or not pairs:
        return []
    from flotilla.ledger import batch
    from flotilla.ledger.commands import open_ledger
    ledger = open_ledger(Path(root))
    found = []
    for label, sha in pairs:
        loose = batch.unaccounted(ledger, ledger.rows(), sha)
        if loose is None:
            found.append(f"{label}: which commits it carries could not be told, so whether the person approved "
                         "them could not be asked")
        elif loose:
            named = ", ".join(f"{item[:7]} {batch.subject(ledger, item)}" for item in loose[:5])
            more = f" and {len(loose) - 5} more" if len(loose) > 5 else ""
            found.append(f"{label}: carries work the person has not approved: {named}{more}; the person runs "
                         "`flotilla work approve <branch>`")
    return found


def _home(d: Door, root, profile: dict, run):
    """(project root, profile) the door is judged by, or None when it acts outside any flotilla project. The
    project is the one the door acts on, not the one the session stands in (F18)."""
    from flotilla.core.config import find_project
    from flotilla.guards.rules import rules_for
    if d.directory is None:
        return (root, profile) if root is not None else None
    found = find_project(d.directory)
    if found is None:
        return None
    if root is not None and found.resolve() == Path(root).resolve():
        return root, profile
    return found, rules_for(found, run=run)[0]


def _names_repo(d: Door, env) -> bool:
    """A gh door that names its repository acts on it whatever directory it runs in (F21)."""
    return d.kind.startswith("gh ") and (_value(list(d.segment.words[3:]), "-R", "--repo") is not None
                                         or bool(d.segment.assignments.get("GH_REPO") or env.get("GH_REPO")))


def guard(segment, *, root, profile, env=os.environ, run=subprocess.run) -> Finding | None:
    from flotilla.core import paths, repo
    d = door(segment)
    if d is None:
        return None
    state, key = paths.state_dir(env), "unknown"
    try:
        home = _home(d, root, profile, run)
        if home is None:
            if not _names_repo(d, env):
                return None
            raise Unknown("names a repository with -R/--repo or GH_REPO from outside any project, so which "
                          "project's receipts it needs cannot be told; run it from that repository's tree")
        home_root, home_profile = home
        if not (home_profile.get("guards") or {}).get(GUARD):
            return None
        key = repo.identify(Path(home_root)).key
        trunk = (home_profile.get("trunk") or {}).get("branch", "main")
        pairs = revisions(d, trunk, run=run)
        failures = _failures(pairs, directory=d.directory, profile=home_profile, state_dir=state, repo_key=key,
                             run=run)
        if d.kind in ("git push", "gh pr merge"):   # what lands on trunk; a tag or a PR opened merges nothing
            failures += _unapproved(home_root, home_profile, [(label, sha) for label, sha in pairs
                                                              if d.kind == "gh pr merge" or label == trunk])
    except Unknown as err:
        failures = [str(err)]
    except Exception as err:  # noqa: BLE001 - the push guard's own failure refuses (spec, section 10)
        failures = [f"the guard failed: {err}"]
    if not failures:
        return None
    reason = (segment.assignments.get(OVERRIDE) or env.get(OVERRIDE) or "").strip()
    if reason:
        from flotilla.guards.overrides import record_override
        record_override(state, key, GUARD, reason, [segment.text, *failures])
        return Finding(GUARD, False, f"flotilla push receipt: override recorded ({reason}) for `{segment.text}`: "
                                     + "; ".join(failures))
    return Finding(GUARD, True, f"flotilla push receipt: `{segment.text}` is closed.\n"
                                + "\n".join(f"  {failure}" for failure in failures)
                                + "\nRun `flotilla receipt run --purpose push` in that tree, or, knowingly: "
                                  f'{OVERRIDE}="<why>" {segment.text} (recorded).')


def pre_push(root, stdin_text: str, *, env=os.environ, run=subprocess.run) -> tuple[int, str]:
    """The second barrier: git names what is pushed, so a push hidden from the command line is judged too."""
    from flotilla.core import paths, repo
    from flotilla.guards.overrides import record_override
    from flotilla.guards.rules import rules_for
    profile, _ = rules_for(root, run=run)
    if not (profile.get("guards") or {}).get(GUARD):
        return 0, ""
    trunk = (profile.get("trunk") or {}).get("branch", "main")
    pairs = []
    for line in (stdin_text or "").splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[1] == ZERO:
            continue
        remote = fields[2]
        if remote.startswith("refs/heads/") and remote.removeprefix("refs/heads/") != trunk:
            continue
        pairs.append((remote, _resolve(root, fields[1], run) or fields[1]))
    if not pairs:
        return 0, ""
    state, key = paths.state_dir(env), repo.identify(Path(root)).key
    failures = _failures(pairs, directory=Path(root), profile=profile, state_dir=state, repo_key=key, run=run)
    try:
        failures += _unapproved(root, profile, [(remote, sha) for remote, sha in pairs
                                                if remote == f"refs/heads/{trunk}"])
    except Exception as err:  # noqa: BLE001 - a guard that cannot ask refuses (spec, section 10)
        failures.append(f"whether the person approved this could not be asked: {err}")
    if not failures:
        return 0, ""
    reason = env.get(OVERRIDE, "").strip()
    if reason:
        record_override(state, key, GUARD, reason, [f"{sha[:12]} {remote}" for remote, sha in pairs])
        return 0, f"flotilla pre-push: override recorded ({reason})"
    return 1, ("flotilla pre-push: the push is closed.\n" + "\n".join(f"  {failure}" for failure in failures)
               + f'\nRun `flotilla receipt run --purpose push`, or, knowingly: {OVERRIDE}="<why>" git push ... '
                 "(recorded).")
