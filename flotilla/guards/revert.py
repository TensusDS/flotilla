"""The revert guard: a command that would destroy uncommitted work does not run blind (spec, section 10).

`git checkout -- <file>` and `git restore <file>` copy the index over the working tree, so work that was never
added is gone, with no reflog and no stash to bring it back. `git add` first makes the index the save point, and
the same command then reverts only what changed after it. A command that names a source (`git checkout HEAD --
f`, `git restore --source=X f`, `git restore --staged --worktree f`) overwrites the index too, so there `git add`
saves nothing: the work has to be committed or stashed. `git reset --hard`, `git checkout -f <branch>` and `git
clean -f` are the same for the whole tree and for untracked files.

The guard refuses only when git says there is something to lose, names the fix that works for that form, and
never makes the save point itself. Its own failure, and a tree it cannot name, let the command run with a
warning: a false refusal costs more than the rare miss (spec, section 10). Ceiling: `flotilla.guards.CEILING`.
"""

from __future__ import annotations

import subprocess

from flotilla.guards import Finding

GUARD = "revert"
VERBS = ("checkout", "restore", "reset", "clean")
SHOWN = 5


#: The guard asks git about the tree a command names before anyone allowed the command, so that tree's config must
#: not get a program run: no fsmonitor hook, no index refresh written back (F19). A clean or smudge filter could
#: still run on a racy index entry; that needs a filter set up in the tree's config and .gitattributes first.
SAFE_GIT = ("git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "--no-optional-locks")


def _git(directory, *args, run):
    return run([*SAFE_GIT, "-C", str(directory), *args], capture_output=True, text=True, check=False, timeout=10)


def _letters(args) -> set[str]:
    """Short flags, clusters included (`-SW` is S and W); a value-taking `-s` ends its cluster."""
    found: set[str] = set()
    for arg in args:
        if arg.startswith("-") and not arg.startswith("--") and arg != "-":
            for ch in arg[1:]:
                found.add(ch)
                if ch == "s":
                    break
    return found


def _source(args) -> tuple[str | None, set[int]]:
    for i, arg in enumerate(args):
        if arg.startswith("--source="):
            return arg.split("=", 1)[1], {i}
        if arg in ("--source", "-s") and i + 1 < len(args):
            return args[i + 1], {i, i + 1}
        if arg.startswith("-s") and len(arg) > 2:
            return arg[2:], {i}
    return None, set()


def _from_file(args) -> bool:
    return any(arg == "--pathspec-from-file" or arg.startswith("--pathspec-from-file=") for arg in args)


def _is_commit(directory, word, run) -> bool:
    return _git(directory, "rev-parse", "--verify", "--quiet", f"{word}^{{commit}}", run=run).returncode == 0


def _plan(verb, args, directory, run):
    """What the command overwrites: ("paths", paths, index_saves), ("tracked", None, False),
    ("untracked", what to list, False), or None when it overwrites nothing uncommitted."""
    if any(arg in ("-p", "--patch") for arg in args):
        return None
    if verb == "checkout":
        if "--" in args:
            head, paths = args[:args.index("--")], args[args.index("--") + 1:]
            source = next((arg for arg in head if not arg.startswith("-")), None)
        else:
            if any(arg in ("-b", "-B", "--orphan") for arg in args):
                return None
            source, paths = None, []
            for arg in args:
                if arg.startswith("-"):
                    continue
                if source is None and not paths and not (directory / arg).exists() and _is_commit(directory, arg,
                                                                                                    run):
                    source = arg
                else:
                    paths.append(arg)
        if _from_file(args):
            paths = ["."]
        if not paths:
            return ("tracked", None, False) if any(arg in ("-f", "--force") for arg in args) else None
        return "paths", paths, source is None
    if verb == "restore":
        source, taken = _source(args)
        letters = _letters(args)
        staged = "--staged" in args or "S" in letters
        worktree = "--worktree" in args or "W" in letters or not staged
        if not worktree:
            return None
        paths = ["."] if _from_file(args) else [arg for i, arg in enumerate(args)
                                                 if i not in taken and not arg.startswith("-")]
        if not paths:
            return None
        return "paths", paths, source is None and not staged
    if verb == "reset":
        return ("tracked", None, False) if "--hard" in args else None
    return _clean(args)


def _long(arg: str, option: str) -> bool:
    """git takes any unambiguous prefix of a long option: `--forc` is `--force`, `--no-dry` is `--no-dry-run`."""
    name = arg.split("=", 1)[0]
    return name == option or (len(name) > 3 and option.startswith(name))


def _clean(args):
    """A forced `git clean`, read for what it would remove: which files (untracked, ignored too, or only ignored),
    which exclusions and which paths - and nothing else, because the guard never runs `git clean` itself (F11)."""
    letters = _letters(args)
    if any(_long(arg, "--interactive") for arg in args) or "i" in letters:
        return None
    dry = "n" in letters or any(_long(arg, "--dry-run") for arg in args)
    if any(_long(arg, "--no-dry-run") for arg in args):
        dry = False   # undoes -n wherever it stands; read the worst case
    if dry or not ("f" in letters or any(_long(arg, "--force") for arg in args)):
        return None
    excludes, paths, index = [], [], 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            paths += args[index + 1:]
            break
        if _long(arg, "--exclude"):
            if "=" in arg:
                excludes.append(arg.split("=", 1)[1])
            elif index + 1 < len(args):
                excludes.append(args[index + 1])
                index += 1
        elif arg.startswith("-") and not arg.startswith("--") and "e" in arg[1:]:
            value = arg[arg.index("e", 1) + 1:]
            if value:
                excludes.append(value)
            elif index + 1 < len(args):
                excludes.append(args[index + 1])
                index += 1
        elif not arg.startswith("-"):
            paths.append(arg)
        index += 1
    mode = "only-ignored" if "X" in letters else "with-ignored" if "x" in letters else "untracked"
    return "untracked", {"mode": mode, "excludes": excludes, "paths": paths}, False


def _at_risk(kind, detail, index_saves, directory, run) -> list[str] | None:
    """What would be lost, as git names it; None when git could not say."""
    if kind == "paths" and index_saves:
        done = _git(directory, "diff", "--name-only", "--", *detail, run=run)
        return done.stdout.splitlines() if done.returncode == 0 else None
    if kind in ("paths", "tracked"):
        done = _git(directory, "status", "--porcelain", "--untracked-files=no", "--",
                    *(detail if kind == "paths" else []), run=run)
        return [line[3:] for line in done.stdout.splitlines()] if done.returncode == 0 else None
    # `git ls-files` lists and changes nothing; a dry run of `git clean` with the command's own arguments did not
    # stay dry (`--no-dry-run`), so the guard deleted what it was judging (F11). Every untracked file is named,
    # inside directories too, which can only overstate the loss.
    listing = ["ls-files", "--others", "--full-name"]
    if detail["mode"] == "untracked":
        listing.append("--exclude-standard")
    elif detail["mode"] == "only-ignored":
        listing += ["--ignored", "--exclude-standard"]
    listing += [f"--exclude={pattern}" for pattern in detail["excludes"]]
    done = _git(directory, *listing, "--", *detail["paths"], run=run)
    if done.returncode != 0:
        return None
    return [line for line in done.stdout.splitlines() if line.strip()]


def _fix(kind, index_saves) -> str:
    if kind == "paths" and index_saves:
        return ("`git add` those files first: the index becomes the save point, and the same command then reverts "
                "only what changed after it")
    if kind == "untracked":
        return "move them, add them, or `git stash push --include-untracked`, before cleaning"
    return "this form overwrites the index too, so `git add` saves nothing: commit, or `git stash`, first"


def check(segment, run=subprocess.run) -> Finding | None:
    found = segment.git()
    if found is None or found[0] not in VERBS:
        return None
    verb, args, directory = found
    if directory is None:
        return Finding(GUARD, False, f"flotilla revert guard: `{segment.text}`: could not tell which tree this runs "
                                     "in, so uncommitted work was not checked; `git add` or commit it first if "
                                     "there is some")
    try:
        inside = _git(directory, "rev-parse", "--is-inside-work-tree", run=run)
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return None
        plan = _plan(verb, args, directory, run)
        if plan is None:
            return None
        kind, detail, index_saves = plan
        lost = _at_risk(kind, detail, index_saves, directory, run)
    except (OSError, subprocess.SubprocessError):
        lost = None
    if lost is None:
        return Finding(GUARD, False, f"flotilla revert guard: `{segment.text}`: git could not say whether "
                                     "uncommitted work is at stake; look at `git status` before relying on it")
    if not lost:
        return None
    shown = ", ".join(lost[:SHOWN]) + (f" and {len(lost) - SHOWN} more" if len(lost) > SHOWN else "")
    return Finding(GUARD, True, f"flotilla revert guard: `{segment.text}` would destroy uncommitted work "
                                f"({shown}). {_fix(kind, index_saves)}.")
