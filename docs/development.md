# Developing flotilla

How to change flotilla, test it, and release it. The README's [Development](../README.md#development) section has
the short list of commands; this page says what each is for and what has gone wrong without it.

## Where things are

| Path | What it holds |
|---|---|
| `flotilla/` | the package, one directory per area: `core` (census, state, Claude Code's own files), `fleet` (spawn, sizing, retire), `ledger` (work rows and their moves), `lane` (the machine's run queue and its daemon), `guards` and `broker` (what a hook allows or refuses), `watch`, `rig` (rented machines), `gc`, `onboard`, `organize` |
| `templates/` | the post templates a project's `.flotilla/posts/` is made from; each carries a `template_version` |
| `skills/`, `hooks/`, `bin/` | what Claude Code loads: the skills, the hook entry points, the `flotilla` command |
| `tests/` | the suite; `tests/conftest.py` keeps every test off the machine's real state (below) |
| `tools/` | the checks CI runs: `check_no_cyrillic.py`, `check_version.py` |
| `docs/specs/` | one design per feature, with the person's rulings; `2026-09-22-decisions-log.md` holds every decision since the first design, with its reason |
| `docs/plans/`, `docs/superpowers/plans/` | the implementation plans the designs were built from |
| `TODO.md` | deferred findings, each with where it came from and when it was last checked against the code |

## Tests

```bash
uv run --with pytest python -m pytest -p no:cacheprovider tests/                   # the suite
uv run --python 3.11 --with pytest python -m pytest -p no:cacheprovider tests/     # the lowest supported Python
GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest python -m pytest -p no:cacheprovider tests/   # no git config
```

**The full suite takes about a quarter of an hour on an 8-core machine** (4,743 tests at 0.31.0). When other sessions
share the machine, run it through the lane, so it waits for room instead of starving theirs:

```bash
bin/flotilla lane run --for <branch> -- uv run --with pytest python -m pytest -p no:cacheprovider tests/
```

**Run a full suite in a tree nobody is editing.** A suite run while files change under it measures a mix of two
revisions; a commit, then a run in a separate worktree at that commit, gives a verdict about one.

**Read the verdict from the last line** (`N passed`), not from an exit code: a run killed for memory prints no
failure, and a pipe into `tail` returns `tail`'s code.

**What `tests/conftest.py` does for every test**, so a test never reaches the machine it runs on: its own state
directory (`FLOTILLA_STATE_DIR` in `tmp_path`), no call to GitHub, no real process stopped, no memory reading of this
machine, no census seat held at start, the machine's installed plugins left out. A test that needs one of these
switches it on itself. **Keep test code under `tests/`**: a script run from elsewhere does not get these fixtures,
and one such script once wrote the machine's live rig journal.

**Proving that a test guards what it claims.** Break the code the test is about, watch the test fail on the
assertion you meant, then restore the file and check it:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --with pytest python -m pytest -p no:cacheprovider tests/test_x.py   # injected
git show HEAD:flotilla/x.py > flotilla/x.py
sha256sum flotilla/x.py; git show HEAD:flotilla/x.py | sha256sum                                       # equal
```

Without `PYTHONDONTWRITEBYTECODE`, a same-length edit reverted within the same second can keep running from its
cached bytecode. `git checkout -- <path>` restores from the index, so a staged injection survives it.

**Platforms.** CI runs Ubuntu and macOS on Python 3.11, 3.12 and 3.13. Two things have broken there and nowhere else:
Python 3.11 does not accept a line break inside an f-string's braces, and Apple silicon's memory pages are 16 KB, so a
test that turns page counts into bytes pins `os.sysconf("SC_PAGE_SIZE")` rather than assume 4 KB.

## Rules every change keeps

- **English only**, in every file git knows, tracked or new (`tools/check_no_cyrillic.py`; CI's `language` job).
- **No private names.** This repository is public: no names of the projects a field test ran on, no people's
  names, no home directories in code, tests, specs, plans, the changelog or commit messages. Cite a field test as
  "a field test" or "a demo fleet". Field-test reports stay outside the repository (`docs/field-tests/` is ignored).
- **The version moves with the code** (`tools/check_version.py`; CI's `version` job): a change under `flotilla/`,
  `templates/`, `skills/`, `hooks/`, `bin/` or `.claude-plugin/` needs a new version, because Claude Code caches an
  installed plugin by its version. Tests and docs do not.
- **A post template that changes moves its `template_version`**, and `tests/fixtures/post-moves.json` names every
  move each template grants at its version.
- **Every change is read by a reviewer who did not write it**, security-relevant ones always, and the commits that
  fix a review's findings are read again before they ship.
- Commit messages follow Conventional Commits: `fix(lane): ...`, `feat(fleet): ...`, `docs(todo): ...`.

## Releasing

1. Move the version everywhere it is written - `.claude-plugin/plugin.json`, `pyproject.toml`,
   `flotilla/__init__.py` (`tests/test_version.py` holds these equal) and the README's pinned install line
   (`TensusDS/flotilla#vX.Y.Z`) - and add `## X.Y.Z - YYYY-MM-DD` to the top of `CHANGELOG.md`, written for the
   person who runs flotilla: what they now see, and why.
2. Commit as `chore(release): X.Y.Z`, tag it annotated (`git tag -a vX.Y.Z -m "X.Y.Z"`), push the branch and the
   tag. `check_version.py` fails while the tag `vX.Y.Z` does not exist, and when plugin code changed after it.
3. Watch CI by job, not only its total.
4. **`stable` is the channel machines follow with automatic updates**, and it names a release and nothing else.
   It moves only forward, onto a release tag:

   ```bash
   git push origin vX.Y.Z^{commit}:refs/heads/stable
   ```

   CI's `stable` job checks that the branch's tip is exactly the tag of the version it carries.

A push that changes `.github/workflows/` needs a token with GitHub's `workflow` scope; the GitHub CLI's token has
it (`git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push ...`).

## Trying a change in a real Claude Code

Use a scratch project, never one you work in, and install the checkout into it as a directory marketplace:

```bash
claude plugin marketplace add <path to your checkout> --scope project
claude plugin marketplace list                     # the name it was added under
claude plugin install flotilla@<that name> --scope project
```

The checkout's own `marketplace.json` is named `flotilla`, like the published one; on a machine that already has
the published marketplace, add the checkout through a directory of its own with a differently named
`marketplace.json`, so the two do not collide.

Two things to know (both in `TODO.md`, measured with Claude Code 2.1.319): a directory marketplace loads the plugin
from its folder whatever version its install record names, and `claude plugin update` can keep a stale cache of a
plugin whose version did not move - reinstall, or move the version. A `.mcp.json` in the project or a folder above
it makes Claude Code ask every new session about it at start; a background seat then waits on a question nobody
sees (flotilla names it since 0.31.0).

## Working on flotilla with a flotilla fleet

A fleet can build flotilla. Three things differ from building any other project with it:

- **A seat runs the flotilla installed for the project, not the code in its tree.** A guard broken on a branch
  cannot lock the fleet out; a fix reaches the fleet only after a release and `/flotilla:update`.
- **Much of this repository is the person's to approve** in a fleet where merges are authorized by the person:
  `.claude-plugin/`, `pyproject.toml`, `tests/conftest.py`, `.github/`, `.claude/` (`flotilla/ledger/approval.py`,
  `ALWAYS`). Every release touches the first two.
- **Lane tests start real processes and systemd scopes.** A suite killed mid-run can leave empty
  `flotilla-lane-*.scope` units behind (`TODO.md`); `systemctl --user list-units 'flotilla-lane*'` shows them.
