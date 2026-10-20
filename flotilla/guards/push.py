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
import re
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


PULL_URL = re.compile(r"https?://[^/\s]+/[^/\s]+/[^/\s]+/pull/\d+", re.IGNORECASE)   # gh lowers the scheme
#: The options of each gh door that take a value, and the short letters among them: what gh reads as a value is
#: neither a flag nor a positional of the command, whatever it looks like.
GH_VALUES = {
    "gh pr merge": ((*MERGE_VALUES, "-R", "--repo"), "tbFAR"),
    "gh pr create": (("-a", "--assignee", "-B", "--base", "-b", "--body", "-F", "--body-file", "-H", "--head", "-l",
                      "--label", "-m", "--milestone", "-p", "--project", "--recover", "-r", "--reviewer", "-T",
                      "--template", "-t", "--title", "-R", "--repo"), "aBbFHlmprTtR"),
    "gh workflow run": (("-f", "--raw-field", "-F", "--field", "-r", "--ref", "-R", "--repo"), "fFrR"),
}


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


def _own_words(args, values, shorts: str, *, positionals: bool = False) -> list[str]:
    """The words that are flags or positionals of their own, not the value of the option before them: `-b --help`
    is a body of "--help", and so is `-sb --help` - a cluster whose last letter takes a value. Read as git and gh
    read them, so a harmless word frees a door only where the program would see it (security review of 236).
    `positionals`: with the words after `--`, which are positionals and never flags."""
    def takes_value(word: str) -> bool:
        if word in values or (len(word) > 2 and word[0] == "-" and word[1] != "-" and word[-1] in shorts):
            return True   # git also takes a long option by any prefix that names it: `--push-o -n`
        return len(word) > 2 and word.startswith("--") and any(value.startswith(word) for value in values)
    args, after = list(args), []
    if "--" in args:   # what follows `--` is never a flag
        args, after = args[:args.index("--")], args[args.index("--") + 1:]
    return [arg for i, arg in enumerate(args) if not (i and takes_value(args[i - 1]))] + (after if positionals else [])


def _repo_flag(kind: str, words) -> bool:
    """Whether a gh door names its repository with -R/--repo, in any form gh takes: `-R o/r`, `--repo=o/r`,
    `-Ro/r`, and `-sR o/r` - a cluster in which R is a flag, not the value of the letter before it."""
    values, shorts = GH_VALUES[kind]
    for word in _own_words(words, values, shorts):
        if word in ("-R", "--repo") or word.startswith("--repo="):
            return True
        if len(word) > 2 and word[0] == "-" and word[1] != "-":
            for letter in word[1:]:
                if letter == "R":
                    return True
                if letter in shorts:   # the rest of the cluster is this letter's value
                    break
    return False


def door(segment) -> Door | None:
    found = segment.git()
    if found is not None:
        verb, args, directory = found
        own = _own_words(args, PUSH_VALUES, "o")   # git takes no `--dry-run=false`; gh does, below
        if verb != "push" or (any(arg in HARMLESS for arg in own)
                              and not any(arg.startswith("--no-d") for arg in own)):   # `-n --no-dry-run` pushes
            return None
        return Door("git push", segment, directory)
    words = segment.words
    if segment.program == "gh" and len(words) >= 3:
        kind = f"gh {words[1]} {words[2]}"
        if kind in ("gh pr create", "gh pr merge", "gh workflow run"):
            own = _own_words(words[3:], *GH_VALUES[kind])
            if any(arg in ("-h", "--help", "--dry-run") for arg in own) and not any(
                    arg.startswith(("--help=", "--dry-run=", "-h=")) for arg in own):   # `--dry-run=false` cancels it
                return None
            flags = [word for word in words[3:] if word.startswith("-")]
            if kind == "gh pr merge" and flags == ["--disable-auto"] and len(words[3:]) <= 2:
                return None   # `gh pr merge [<pr>] --disable-auto` turns auto-merge off and merges nothing; beside
                #               any other flag, or as an option's value, it frees nothing (0.7.9 security review)
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
    if any("*" in spec for spec in refspecs):   # a pattern may carry trunk: asked as a broad push
        return [("HEAD", _need(_resolve(directory, "HEAD", run), "HEAD"))]
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
    if _repo_flag(d.kind, rest):
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
        # every merge is judged as landing on trunk: its base cannot be pinned as its head is, and trunk's name can
        # come from a tree the session edits (0.7.9 security review)
        argv = ["gh", "pr", "view", *([target] if target else []), "--json", "headRefOid", "-q", ".headRefOid"]
        try:
            done = run(argv, cwd=str(d.directory), capture_output=True, text=True, check=False, timeout=10)
            sha = done.stdout.strip() if done.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            sha = ""
        sha = _need(sha, "the pull request's head (gh pr view)")
        if sum(1 for word in rest if word.split("=", 1)[0] == "--match-head-commit") > 1:
            raise Unknown("names --match-head-commit more than once; gh takes the last, so name it once")
        pinned = _value(rest, "--match-head-commit")
        if pinned != sha:   # gh merges the head the PR has when it merges; --auto, a later one (F20, F22)
            raise Unknown(f"merges whatever head the pull request has then, not the one whose receipt was checked; "
                          f"pin it: add --match-head-commit {sha}")
        # a base other than trunk lands the work in the lower branch, not trunk (organize spec, section 5); the
        # receipt is still asked whatever gh says - a base can change after this - this refuses only what gh names
        argv = ["gh", "pr", "view", *([target] if target else []), "--json", "baseRefName", "-q", ".baseRefName"]
        try:
            asked = run(argv, cwd=str(d.directory), capture_output=True, text=True, check=False, timeout=10)
            base = asked.stdout.strip() if asked.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            base = ""
        if base and base != trunk:
            raise Unknown(f"merges into `{base}`, not trunk `{trunk}`: the work would land in the lower branch; "
                          "retarget the pull request to trunk first (a taken-in row's `queue` does it), or merge it "
                          "yourself after `!`")
        return [(f"PR {target or '(this branch)'}", sha)]
    return [("HEAD", _need(_resolve(d.directory, "HEAD", run), "HEAD"))]


def workflow_at(directory, sha, *, run=subprocess.run) -> str | None:
    """The workflow digest at a revision, computed exactly as onboarding computes it from the working tree."""
    listing = _git(directory, "ls-tree", "-z", "--full-tree", sha, "--", ".github/workflows/", run=run)
    files = []
    for line in (listing or "").split("\0"):   # -z: a path is never quoted, non-ASCII ones included (0.7.9)
        if not line:
            continue
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


def _ask_origin(root, refs: list[str], run, *, own: bool = True) -> str:
    """What origin answers to `git ls-remote --symref <origin> <refs>`. A repository other than the session's own
    is named by a command nobody has allowed yet, so its config must not get a program run (scan of 0.6.10, F3):
    origin is asked there only by a plain URL, with a config that holds nothing that runs code, and with no
    repository hook."""
    command = ["git", "-C", str(root), "ls-remote", "--symref", "origin", *refs]
    if not own:
        from flotilla.guards.run import _config_is_plain, _empty_hooks, _plain_url
        urls = (_git(root, "config", "--get-all", "remote.origin.url", run=run) or "").splitlines()
        if len(urls) != 1 or not _plain_url(urls[0]) or not _config_is_plain(Path(root), run):
            raise Unknown(f"origin could not be asked safely: {root} is not this session's repository, and its own "
                          "git config holds keys that can run a program or redirect git, or origin's URL is not a "
                          "plain one, so it is not asked before the command is allowed")
        command = ["git", "-C", str(root), "-c", f"core.hooksPath={_empty_hooks()}", "ls-remote", "--symref", urls[0],
                   *refs]
    done = run(command, capture_output=True, text=True, check=False, timeout=20)
    if done.returncode != 0:
        raise Unknown(f"origin could not be asked ({(done.stderr or '').strip()[:120]}), so the rules this push is "
                      "judged by cannot be read")
    return done.stdout


#: Git reads history through replace refs and grafts by default; the guards read what a push really sends (review
#: of the scan of 0.7.0, C1). Set for every git a guard process starts.
NO_REWRITES = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_GRAFT_FILE": "/nonexistent/flotilla-no-grafts"}


def no_rewrites() -> None:
    os.environ.update(NO_REWRITES)


def _project(root):
    from flotilla.core import config
    found = config.find_project(Path(root))
    if found is None:
        raise Unknown(f"not onboarded: no .flotilla/project.toml at or above {Path(root).resolve()}")
    return found


def _tree_profile(found) -> dict:
    from flotilla.core import config
    try:
        return config.load_project(found).data
    except config.ConfigError as err:
        raise Unknown(f"this tree's .flotilla/project.toml cannot be read ({err})") from err


def _tree_trunk(found) -> str:
    """The trunk the tree names: only ever a fallback for whether a push lands on trunk, never for its rules."""
    try:
        return str((_tree_profile(found).get("trunk") or {}).get("branch") or "main")
    except Unknown:
        return "main"


def _ls_remote(found, refs, run, *, own: bool, url: str) -> str | None:
    """What origin answers, or None where there is no origin. `url` is the destination git itself names to a
    pre-push hook (after pushurl and every rewrite, measured on git 2.53): it is asked from outside any repository,
    so no repository config redirects the question (review of the scan of 0.7.0, C3, C4)."""
    if url:
        from flotilla.core import paths
        outside = paths.state_dir()
        outside.mkdir(parents=True, exist_ok=True)
        done = run(["git", "-C", str(outside), "ls-remote", "--symref", url, *refs], capture_output=True, text=True,
                   check=False, timeout=20)
        if done.returncode != 0:
            raise Unknown(f"the destination could not be asked ({(done.stderr or '').strip()[:120]}), so the rules "
                          "this push is judged by cannot be read")
        return done.stdout
    if not _git(found, "config", "--get-all", "remote.origin.url", run=run):
        return None
    return _ask_origin(found, refs, run, own=own)


def _heads(listed: str) -> tuple[str, dict[str, str]]:
    default, shas = "", {}
    for line in listed.splitlines():
        words = line.split()
        if len(words) == 3 and words[0] == "ref:" and words[2] == "HEAD" and words[1].startswith("refs/heads/"):
            default = words[1].removeprefix("refs/heads/")
        elif len(words) == 2:
            shas[words[1]] = words[0]
    return default, shas


def _show_profile(found, sha: str, run) -> dict | None:
    shown = run(["git", "-C", str(found), "show", f"{sha}:.flotilla/project.toml"], capture_output=True, text=True,
                check=False, timeout=10)
    if shown.returncode != 0:
        return None
    try:
        return tomllib.loads(shown.stdout)
    except tomllib.TOMLDecodeError as err:
        raise Unknown(f"the profile on origin's trunk at {sha[:7]} cannot be read ({err}); a person fixes it on "
                      "trunk, pushing with `git push --no-verify` from their own terminal") from err


def _have(found, sha: str, ref: str, run, *, url: str) -> bool:
    """Whether this checkout has origin's revision; asks origin for it once if not (a quiet fetch of that ref)."""
    if _resolve(found, sha, run):
        return True
    run(["git", "-C", str(found), "fetch", "--quiet", "--no-tags", url or "origin", ref], capture_output=True,
        text=True, check=False, timeout=30)
    return bool(_resolve(found, sha, run))


@dataclass(frozen=True)
class OriginTrunk:
    trunk: str
    base: str                       # origin's revision of trunk; "" where origin has none
    unasked: Exception | None = None   # why origin could not be asked: `trunk` is then the tree's, for landing only
    url: str = ""


def origin_trunk(root, run=subprocess.run, *, own: bool = True, url: str = "") -> OriginTrunk:
    """Which branch is trunk, asked of origin (scan of 0.7.0, F1): origin's default branch, or the trunk the profile
    on that branch names. The tree's word counts only where origin has no trunk, or cannot be asked - and then only
    to tell whether a push lands on trunk, never for the rules it is judged by."""
    found = _project(root)
    try:
        listed = _ls_remote(found, ["HEAD"], run, own=own, url=url)
    except Unknown as err:
        return OriginTrunk(_tree_trunk(found), "", err, url)
    if listed is None:
        return OriginTrunk(_tree_trunk(found), "", None, url)
    default, shas = _heads(listed)
    head = shas.get("HEAD", "")
    if not default or not head:   # an empty origin: no trunk yet
        return OriginTrunk(_tree_trunk(found), "", None, url)
    if _tree_trunk(found) == default:
        return OriginTrunk(default, head, None, url)
    # the tree names another trunk: only the profile on origin's default branch can confirm it
    if not _have(found, head, f"refs/heads/{default}", run, url=url):
        return OriginTrunk(default, head, Unknown(f"origin's default branch `{default}` is at {head[:7]}, which "
                                                  "this checkout could not fetch, so which branch is trunk cannot "
                                                  "be read"), url)
    carried = _show_profile(found, head, run)
    named = str(((carried or {}).get("trunk") or {}).get("branch") or default)
    if named == default:
        return OriginTrunk(default, head, None, url)
    listed = _ls_remote(found, [f"refs/heads/{named}"], run, own=own, url=url) or ""
    return OriginTrunk(named, _heads(listed)[1].get(f"refs/heads/{named}", ""), None, url)


def rules_at(root, where: OriginTrunk, run=subprocess.run) -> tuple[dict, str]:
    """(the profile a push to trunk is judged by, a note): the one trunk carries on origin. Until it carries one -
    the onboarding's own first push - or where origin has no trunk, the tree's, under trunk's real name. Raises
    Unknown where origin could not be asked or its revision cannot be had: rules nobody can read decide nothing."""
    found = _project(root)
    if where.unasked is not None:
        raise where.unasked if isinstance(where.unasked, Unknown) else Unknown(str(where.unasked))
    forced = {"branch": where.trunk}
    if not where.base:
        tree = _tree_profile(found)
        return {**tree, "trunk": {**(tree.get("trunk") or {}), **forced}}, "origin has no trunk: this tree's profile"
    if not _have(found, where.base, f"refs/heads/{where.trunk}", run, url=where.url):
        raise Unknown(f"origin's trunk `{where.trunk}` is at {where.base[:7]}, which this checkout could not fetch, "
                      "so the rules this push is judged by cannot be read")
    carried = _show_profile(found, where.base, run)
    if carried is None:
        tree = _tree_profile(found)
        return ({**tree, "trunk": {**(tree.get("trunk") or {}), **forced}},
                f"origin's `{where.trunk}` carries no profile yet: this tree's profile")
    if str((carried.get("trunk") or {}).get("branch") or where.trunk) != where.trunk:
        raise Unknown(f"the profile on origin's `{where.trunk}` names another trunk, so which branch the rules guard "
                      "cannot be told")
    return {**carried, "trunk": {**(carried.get("trunk") or {}), **forced}}, ""


def _unapproved(root, profile: dict, pairs, since: str = "") -> list[str]:
    """Where a person authorizes merges, what goes to trunk is work they approved (F24): each revision is asked of
    the ledger, which counts only approved rows; a commit no approved row covers closes the door."""
    if (profile.get("flow") or {}).get("merge_authorized_by") != "human" or not pairs:
        return []
    from flotilla.ledger import batch
    from flotilla.ledger.commands import open_ledger
    ledger = open_ledger(Path(root))
    found = []
    for label, sha in pairs:
        loose = batch.unaccounted(ledger, ledger.rows(), sha, since=since)
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
    if d.directory is None:
        return (root, profile) if root is not None else None
    found = find_project(d.directory)
    if found is None:
        return None
    if root is not None and found.resolve() == Path(root).resolve():
        return root, profile
    return found, {}   # its tree's profile is not read: a push is judged by origin's, and a file a seat writes into
    #                    a directory must not be able to fell the guard before it asks who lands (review of 236)


def _own_repository(home_root, root, run=subprocess.run) -> bool:
    """Whether the door acts on the session's own repository - a checkout its repository lists as a worktree - rather
    than one the command merely names. Asked of the session's repository, never of the named directory: a directory
    whose `.git/commondir` names the session's repository shares its common dir and still reads its own
    `config.worktree` (final review of the scan fixes, I4)."""
    if root is None:
        return False
    home = Path(home_root).resolve()
    if home == Path(root).resolve():
        return True
    listed = _git(root, "worktree", "list", "--porcelain", run=run) or ""
    return home in {Path(line[len("worktree "):]).resolve() for line in listed.splitlines()
                    if line.startswith("worktree ")}


def _names_repo(d: Door, env) -> bool:
    """A gh door that names its repository acts on it whatever directory it runs in (F21)."""
    if not d.kind.startswith("gh "):
        return False
    words = list(d.segment.words[3:])
    if _repo_flag(d.kind, words) or bool(d.segment.assignments.get("GH_REPO")):
        return True
    # a merge may name its pull request by URL, and that names the repository too. Only the pull request itself: a
    # link in a body or a workflow input names nothing (second review of 236)
    return d.kind == "gh pr merge" and any(
        PULL_URL.match(word) for word in _own_words(words, *GH_VALUES[d.kind], positionals=True))
    # a GH_REPO exported in the person's environment names no door of this command: work outside flotilla stays open


LAND = "land"   # the ledger move that puts work on trunk: a post that has it is the one that pushes there


def _posts_at(root, where: OriginTrunk, run) -> dict:
    """The posts origin's trunk carries - {} where it has no trunk yet, or carries no profile: the onboarding's own
    first push is judged by no post. Read from that revision alone, like the profile a push is judged by: the tree's
    own files - which a seat edits, and may break - decide nothing here."""
    import tempfile
    from flotilla.ledger.commands import fleet_name
    from flotilla.posts import POSTS_DIR, load_posts
    found = _project(root)
    carried = _show_profile(found, where.base, run) if where.base else None
    if carried is None:
        return {}
    # --full-tree: the path is trunk's, whatever directory the door stands in (a subdirectory with a project file of
    # its own would otherwise list its own, empty, posts - and switch the rule off)
    listed = _git(found, "ls-tree", "-r", "--full-tree", where.base, "--", f"{POSTS_DIR}/", run=run)
    if listed is None:   # not "no posts": a rule that could not be read is not a rule that is off
        raise Unknown(f"git could not list {POSTS_DIR}/ at {where.base[:7]}")
    with tempfile.TemporaryDirectory(prefix="flotilla-posts-") as tmp:
        folder = Path(tmp) / POSTS_DIR
        folder.mkdir(parents=True)
        for line in listed.splitlines():
            meta, _, path = line.partition("\t")
            name = path.removeprefix(f"{POSTS_DIR}/")
            if "/" in name or not name.endswith(".md"):
                continue
            if meta.split()[0] == "120000":
                raise Unknown(f"{path} is a symlink on origin's trunk; posts are read only from real files")
            shown = run(["git", "-C", str(found), "show", f"{where.base}:{path}"], capture_output=True, text=True,
                        check=False, timeout=10)
            if shown.returncode != 0:
                raise Unknown(f"git could not read {path} at {where.base[:7]}, so who may land cannot be told")
            (folder / name).write_text(shown.stdout, encoding="utf-8")
        try:
            return load_posts(Path(tmp), project=fleet_name(Path(found), carried))
        except Exception as err:  # noqa: BLE001 - said by trunk's file, not by the copy it was read from
            at = f"origin's `{where.trunk}` at {where.base[:7]}: "
            raise Unknown(str(err).replace(f"{tmp}{os.sep}", at)) from err


LANDS = ("git push", "gh pr merge")   # the doors that put work on trunk or a tag; the others publish, and land nothing


def _may_not_land(root, where: OriginTrunk, who, run, *, hook: bool = False) -> str:
    """Why this call may not put work on trunk, or "". A session that holds a post lands only if its post has the
    `land` move; one that holds none - a person's own, another project's seat - is judged as before, by the receipt
    and the approval. Where no post lands at all there is no fleet to ask. The posts are origin's trunk's, never
    the tree's: a seat cannot grant itself the move. `who()` gives every name the call answers to and every live
    session; where it cannot, the push is closed - who may land is not guessed. `hook`: asked by git's own hook,
    where the one reading may be the person at their terminal."""
    from flotilla.posts import PostError, post_for_session
    posts = _posts_at(root, where, run)
    landing = sorted(post.name for post in posts.values() if LAND in post.may)
    if not landing:
        return ""
    rule = (f"only a session whose post may land ({', '.join(f'`{name}`' for name in landing)}) pushes to "
            f"`{where.trunk}` or a tag, or merges a pull request")
    try:
        names, live = who()
    except Exception as err:  # noqa: BLE001 - whoever it is, it is not known to be one that lands
        return f"who is calling could not be asked ({err}), and {rule}. Run it again; {_persons_way(hook)}"
    held = [(name, post) for name in names if (post := post_for_session(posts, name)) is not None]
    barred = [(name, post) for name, post in held if LAND not in post.may]
    if not barred:
        return ""
    name, post = barred[0]

    def holds(other: str, move: str) -> bool:
        try:
            found = post_for_session(posts, other)
        except PostError:
            return False
        return found is not None and move in found.may

    others = sorted({other for other in live if other and other not in names})
    landers = [other for other in others if holds(other, LAND)]
    said = f"{name} holds the post `{post.name}`, which may not land: {rule}."
    hands = "hand" in post.may   # a reviewer, an orchestrator, a helper and a judge have no work of their own to hand
    if landers:
        who_lands = landers[0] if len(landers) == 1 else "one of " + ", ".join(landers)
        if hands:
            return (f"{said}\n  Hand the work over (`flotilla work hand <branch>`); once it is accepted, {who_lands} "
                    "lands it.")
        return f"{said}\n  It is not yours to land: {who_lands} lands work that was handed over and accepted."
    absent = "No live session of this fleet holds a post that may land"
    if "assign" in post.may:   # the one who leads: the person hears it from this session
        return (f"{said}\n  {absent}, so tell the person: the fleet has nobody to merge, and `flotilla spawn -s 1` "
                "raises a sender.")
    leads = [other for other in others if holds(other, "assign")]
    waits = "; the work waits handed over (`flotilla work hand <branch>`)" if hands else ""
    if leads:
        return (f"{said}\n  {absent}. Tell {', '.join(leads)} (SendMessage), so that the person hears the fleet has "
                f"nobody to merge{waits}.")
    return (f"{said}\n  {absent}, and nobody leads it either. Say so where the person will read it: they raise a "
            f"sender with `flotilla spawn -s 1`{waits}.")


def _persons_way(hook: bool) -> str:
    if hook:   # the one reading may be the person, at the very terminal the push was typed in
        return "a person who knows why pushes from their own terminal with `git push --no-verify`."
    return "a person pushes with `!` in their own session, or from a terminal."


def _post_refusal(root, where: OriginTrunk, who, run, *, hook: bool = False) -> str:
    """`_may_not_land`, for a guard: never raises. Where the question itself fails - posts that cannot be read, a
    name two posts match - the push is closed like one whose rules cannot be read; an override waives a receipt,
    never who may land (security review of decision 236)."""
    try:
        return _may_not_land(root, where, who, run, hook=hook)
    except Exception as err:  # noqa: BLE001 - not guessed, whatever broke
        return f"who may land could not be told ({err}), and it is not guessed. Until it can, {_persons_way(hook)}"


def _asked_of_own(d: Door, root, who, run) -> str:
    """The caller's post, asked of the project the session stands in, for a door that lands and whose own project
    cannot be told - above all a merge from a directory in no project, which has no second barrier and many
    spellings (`-R`, a URL, an exported GH_REPO, a scratch repository). The post alone: the receipts and the
    approval of the session's project say nothing about work in another repository (third reading of 236)."""
    if root is None or who is None or d.kind not in LANDS:
        return ""
    try:
        where = origin_trunk(root, run)
        profile, _ = rules_at(root, where, run)
    except Exception as err:  # noqa: BLE001 - rules nobody can read decide nothing, and no override waives that
        return (f"the rules of the project this session stands in could not be read from origin ({err}), so who may "
                f"land cannot be told. Until they can, {_persons_way(False)}")
    if not (profile.get("guards") or {}).get(GUARD):
        return ""
    return _post_refusal(root, where, who, run)


def _when_untold(d: Door, home_root, where: OriginTrunk, who, run, err) -> tuple[list[str], str, list[str]]:
    """(rules that could not be read, the caller's post, the person's approval) for a door that lands and fell
    before its revisions were told - a merge no revision pins, a tree named through a variable, a git that timed
    out. It is judged as landing: none of the three is a receipt, and an override waives a receipt alone (final
    review of decision 236: `FLOTILLA_GATE_OVERRIDE=x gh pr merge 5` passed a builder, and passed unapproved
    work). `err`: what the guard says fell."""
    if d.kind not in LANDS:
        return [], "", []
    try:
        profile, _ = rules_at(home_root, where, run)
    except Exception as unread:  # noqa: BLE001 - rules nobody can read decide nothing, however the reading fell
        return [str(unread)], "", []
    if not (profile.get("guards") or {}).get(GUARD):
        return [], "", []
    outsider = _post_refusal(home_root, where, who, run) if who is not None else ""
    unapproved = []
    if (profile.get("flow") or {}).get("merge_authorized_by") == "human":
        unapproved = [f"whether the person approved this could not be asked: {err}"]
    return [], outsider, unapproved


def identities(sessions, session_id: str = "", *, chain=()) -> tuple[list[str], list[str]]:
    """(every name this call answers to, the names of every live session). A call answers to the session whose id
    made it, and to every background session its process runs under: a session a seat started inside itself is
    that seat's, whatever it is named, and a nearer one does not hide it. An interactive session above - the
    person's own - is named only by its id: their `!` and their terminal stay theirs."""
    names = [item.name for item in sessions if session_id and item.session_id == session_id]
    names += [item.name for item in chain if item.kind == "background"]
    return [name for name in dict.fromkeys(names) if name], [item.name for item in sessions if item.name]


def callers(session_id: str = "") -> tuple[list[str], list[str]]:
    """`identities`, asked of the census and of this process's parents: the hook's process is a child of the
    session that fired it, and git's of the session a hidden push runs under. Raises where the census cannot be
    asked: who may land is not guessed."""
    from flotilla.core import platform as plat
    from flotilla.core.caller import sessions_above
    from flotilla.core.census import read_census
    sessions = read_census(timeout=8)
    source = plat.probe().parent_pid_source
    chain = sessions_above(sessions, parent_of=lambda pid: plat.parent_pid(pid, source))
    return identities(sessions, session_id, chain=chain)


def guard(segment, *, root, profile, env=os.environ, run=subprocess.run, who=None) -> Finding | None:
    """`who` says which sessions the call answers to (`callers`); without it nobody's post is asked."""
    from flotilla.core import paths, repo
    d = door(segment)
    if d is None:
        return None
    state, key, unapproved, closed, outsider = paths.state_dir(env), "unknown", [], [], ""
    home_root = where = None
    asked = False   # whether the caller's post was reached: a door that fell before it is asked where it fell
    try:
        home = _home(d, root, profile, run)
        if home is None:
            if d.kind == "git push" and d.directory is None:
                raise Unknown("could not tell which repository this pushes (GIT_DIR, a git dir named elsewhere); "
                              "run it as `git -C <tree> push`")
            if not _names_repo(d, env):
                if d.kind != "gh pr merge":
                    return None
                stands = _asked_of_own(d, root, who, run)   # a seat's merge is asked for its post wherever it is
                return Finding(GUARD, True, f"flotilla push: `{segment.text}` is closed.\n  {stands}") if stands \
                    else None
            raise Unknown("names a repository with -R/--repo, GH_REPO or its pull request's URL from outside any "
                          "project, so which project's receipts it needs cannot be told; run it from that "
                          "repository's tree")
        home_root = home[0]
        where = origin_trunk(home_root, run, own=_own_repository(home_root, root, run))   # scan of 0.7.0, F1
        trunk = where.trunk
        pairs = revisions(d, trunk, run=run)
        if not pairs:   # lands on no trunk and no tag: nothing to judge, whatever the rules say
            return None
        try:
            home_profile, _ = rules_at(home_root, where, run)
        except Unknown as err:   # rules nobody can read decide nothing, and have no override
            closed = [str(err)]
            raise
        if not (home_profile.get("guards") or {}).get(GUARD):
            return None
        key = repo.identify(Path(home_root)).key
        asked = True
        if who is not None and d.kind in LANDS:
            outsider = _post_refusal(home_root, where, who, run)
        try:   # by itself: a receipt check that fell must not skip the approval asked below (security review)
            failures = [] if outsider else _failures(pairs, directory=d.directory, profile=home_profile,
                                                     state_dir=state, repo_key=key, run=run)
        except Unknown as err:
            failures = [str(err)]
        except Exception as err:  # noqa: BLE001 - the push guard's own failure refuses (spec, section 10)
            failures = [f"the guard failed: {err}"]
        landing = [(label, sha) for label, sha in pairs if d.kind == "gh pr merge" or label in (trunk, "HEAD")]
        if d.kind in ("git push", "gh pr merge") and landing and \
                (home_profile.get("flow") or {}).get("merge_authorized_by") == "human":
            try:
                unapproved = _unapproved(home_root, home_profile, landing, since=where.base)
            except Exception as err:  # noqa: BLE001 - what stands in for the approval has no override either (C2)
                unapproved = [f"whether the person approved this could not be asked: {err}"]
    except Unknown as err:
        failures = [str(err)]
    except Exception as err:  # noqa: BLE001 - the push guard's own failure refuses (spec, section 10)
        failures = [f"the guard failed: {err}"]
    if failures and not asked and not closed:   # it fell before the post was asked
        if where is None:   # which project it acts on could not be told: a seat is asked by the one it stands in
            outsider = _asked_of_own(d, root, who, run)
        else:
            closed, outsider, unapproved = _when_untold(d, home_root, where, who, run, failures[0])
        if outsider:   # the seat still learns how to make the door one that can be judged
            outsider += f"\n  What it carries could not be told: {failures[0]}"
    if outsider and d.kind == "git push":
        broad = [word for word in BROAD if word in d.segment.git()[1]]
        if broad:   # the seat pushed its own branch, and one word made it a push of everything
            outsider += (f"\n  {', '.join(f'`{word}`' for word in broad)} may carry trunk or a tag: push your "
                         "branch by its name, without it.")
    if closed:
        return Finding(GUARD, True, f"flotilla push: `{segment.text}` is closed: the rules it is judged by could not "
                                    "be read from origin.\n" + "\n".join(f"  {item}" for item in closed))
    if outsider:   # no override either: an override a seat can type turns the rule back into advice
        return Finding(GUARD, True, f"flotilla push: `{segment.text}` is closed.\n  {outsider}")
    if unapproved:   # the person's approval has no override: a session cannot waive what only a person gives
        return Finding(GUARD, True, f"flotilla push: `{segment.text}` is closed: it carries work the person has "
                                    "not approved.\n" + "\n".join(f"  {item}" for item in unapproved))
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


def pre_push(root, stdin_text: str, *, env=os.environ, run=subprocess.run, url: str = "") -> tuple[int, str]:
    """The second barrier: git names what is pushed, and where - `url`, the destination after every rewrite - so a
    push hidden from the command line, or sent somewhere a repository's config disguises, is judged too."""
    from flotilla.core import paths, repo
    from flotilla.guards.overrides import record_override
    where = origin_trunk(root, run, url=url)   # trunk and its rules as the destination has them (scan of 0.7.0, F1)
    trunk = where.trunk
    pairs, remote_had = [], ""
    for line in (stdin_text or "").splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[1] == ZERO:
            continue
        remote = fields[2]
        if remote.startswith("refs/heads/") and remote.removeprefix("refs/heads/") != trunk:
            continue
        pairs.append((remote, _resolve(root, fields[1], run) or fields[1]))
        if remote == f"refs/heads/{trunk}" and fields[3] != ZERO:
            remote_had = fields[3]   # what the destination has, as git says it: no local ref stands in for it
    if not pairs:
        return 0, ""
    try:
        profile, _ = rules_at(root, where, run)
    except Unknown as err:   # no override: rules nobody can read decide nothing
        return 1, f"flotilla pre-push: the push is closed: the rules it is judged by could not be read.\n  {err}"
    if not (profile.get("guards") or {}).get(GUARD):
        return 0, ""
    outsider = _post_refusal(root, where, callers, run, hook=True)   # the seats this git runs under
    if outsider:   # no override, as on the command line
        return 1, f"flotilla pre-push: the push is closed.\n  {outsider}"
    state, key = paths.state_dir(env), repo.identify(Path(root)).key
    failures = _failures(pairs, directory=Path(root), profile=profile, state_dir=state, repo_key=key, run=run)
    try:
        unapproved = _unapproved(root, profile, [(remote, sha) for remote, sha in pairs
                                                 if remote == f"refs/heads/{trunk}"], since=remote_had)
    except Exception as err:  # noqa: BLE001 - a guard that cannot ask refuses (spec, section 10)
        unapproved = [f"whether the person approved this could not be asked: {err}"]
    if unapproved:   # no override for the person's approval
        return 1, ("flotilla pre-push: the push is closed: it carries work the person has not approved.\n"
                   + "\n".join(f"  {item}" for item in unapproved))
    if not failures:
        return 0, ""
    reason = env.get(OVERRIDE, "").strip()
    if reason:
        record_override(state, key, GUARD, reason, [f"{sha[:12]} {remote}" for remote, sha in pairs])
        return 0, f"flotilla pre-push: override recorded ({reason})"
    return 1, ("flotilla pre-push: the push is closed.\n" + "\n".join(f"  {failure}" for failure in failures)
               + f'\nRun `flotilla receipt run --purpose push`, or, knowingly: {OVERRIDE}="<why>" git push ... '
                 "(recorded).")
