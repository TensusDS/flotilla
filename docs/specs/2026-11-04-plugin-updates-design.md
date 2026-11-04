# Plugin updates: a release channel, a refused pin explained, one command for every install

Status: design, 2026-11-04. Release: 0.29.0.

## Why

Field finding, 2026-11-04. On a machine where flotilla was once installed for the user with a pin
(`~/.claude/settings.json` declares the `flotilla` marketplace at `v0.26.0`), the README's install of a newer
release into a new project failed in two ways at once:

- `claude plugin marketplace add TensusDS/flotilla#v0.28.1 --scope project` refused: *Cannot add marketplace
  "flotilla": its source doesn't match its extraKnownMarketplaces entry in user or managed settings; add it from
  the source that entry lists, or change the entry.*
- `claude plugin install flotilla@flotilla --scope project`, the README's next line, then printed *Successfully
  installed* - and installed 0.26.0, the release the user's entry pins. Nothing said so.

The person wanted 0.28.1 and got 0.26.0 without a word. The copy that runs (0.26.0) cannot explain it: it does
not know the check, and no hook runs while the person types in a shell. Machines hold many projects (this one: one
user install and over a hundred project records), and moving them is done by hand, one `claude plugin update` each.

The person asked (2026-11-04): the session must understand what happened and offer the two ways out - move the
marketplace's pointer, or raise the version - and flotilla should keep every install on the machine current, the
user's and every project's, so the problem does not come back.

## Measured (Claude Code 2.1.318, isolated `CLAUDE_CONFIG_DIR`, GitHub source)

1. A project-scope `marketplace add` with a ref other than the user entry's is refused (the message above). With
   the same ref it succeeds: *Marketplace 'flotilla' already on disk - declared in project settings*.
2. A user-scope `marketplace add` with a new ref is refused the same way while the user entry names the old ref.
3. Editing the user entry's `ref` by hand alone breaks the shell commands: `claude plugin marketplace update
   flotilla` says *Marketplace 'flotilla' not found*, `claude plugin update` says *Plugin "flotilla" not found*.
4. Editing the user entry's `ref`, then `claude plugin marketplace add TensusDS/flotilla#<ref>` (user scope) moves
   the machine's marketplace (`known_marketplaces.json` follows the new ref) and keeps every install. Then
   `claude plugin update flotilla@flotilla --scope user`, and the same with `--scope project` run in a project,
   each move their install to the new release, even where that project's own settings still pin the old ref.
5. That `marketplace add` rewrites the user entry and drops `autoUpdate`. Written back afterwards, `autoUpdate:
   true` survives `marketplace update` and `plugin update`.

From Anthropic's documentation (code.claude.com/docs/en/plugins/loading.md, "When auto-update runs"): auto-update
is off by default for a third-party marketplace; `autoUpdate` on the marketplace's `extraKnownMarketplaces` entry
decides first; the pass runs in an interactive session up to ten minutes after the first message, refreshes the
marketplace and updates the copies on disk; the running session keeps what it loaded. A marketplace pinned to a tag
refreshes to the same tag, so auto-update moves only a marketplace that follows a branch.

Not measured, and the design does not lean on them: whether the background pass updates project-scope installs
too (part C does it explicitly), and what a session start does in a project whose committed settings pin another
ref than the machine follows (part C reports such pins; the field test watches for it).

## Goals

- A person can take every release automatically, and only releases - not every commit on `main`, because
  flotilla's hooks run on every Bash call of every session.
- When a pin holds a machine back, the next session start and `/flotilla:doctor` say so in plain words, with the
  two ways out and the exact commands.
- One command shows every flotilla install on the machine and its version, and on the person's yes moves the
  marketplace to the channel and brings every install up.

## Non-goals and anti-goals

- flotilla never changes the person's settings, or runs an update, without their yes in an interactive session.
  A background session is refused, as with every other person's move.
- No edit of Claude Code's own records (`known_marketplaces.json`, `installed_plugins.json`): only settings the
  documentation names and `claude plugin` commands.
- No uninstall, no `marketplace remove` (it uninstalls every plugin from the marketplace in every scope).
- A marketplace that is not `TensusDS/flotilla` on GitHub (a fork, a local directory used for development) is
  reported, never rewritten.

## A. The release channel `stable`

A branch `stable` in TensusDS/flotilla that always points at the newest release tag. It moves at a release, after
the tag's CI is green, by a fast-forward push: `git push origin v<X.Y.Z>^{commit}:refs/heads/stable`. Nothing else
is ever committed to it.

A CI job on a push to `stable` fails unless the branch's tip is exactly a `v*` tag whose version equals
`.claude-plugin/plugin.json`'s - `tools/check_version.py --stable`. The repository's protection on `stable`
(fast-forward only, no deletion) is the maintainer's to set on GitHub, and the release notes say so.

## B. Telling the person (doctor and session start)

`claude_state.updates(...)` reads three things and decides nothing else:

- the user entry: `extraKnownMarketplaces.flotilla` in `<config>/settings.json` (source, ref, autoUpdate);
- the newest release: `git ls-remote --tags --refs https://github.com/TensusDS/flotilla "v*"`, highest version,
  and `refs/heads/stable`; run with no repository and no hooks, five seconds at most. The answer is kept in the
  state directory (`releases.json`) for a day; doctor always asks afresh;
- the running copy's version.

Findings, each with its fix:

| case | status | says |
|---|---|---|
| user entry pins a tag older than the newest release | warn | the user settings pin the flotilla marketplace to vA, the newest is vB; while that entry stands, a project cannot pin another release (Claude Code refuses `marketplace add` with "doesn't match its extraKnownMarketplaces entry") and `plugin install` takes vA silently. Two ways: follow `stable` with auto-update (recommended), or move the pin to vB - `/flotilla:update` does either on your yes |
| user entry follows `stable` without `autoUpdate: true` | info | releases arrive only when you update by hand; `/flotilla:update` turns auto-update on |
| user entry follows the default branch | info | every commit on `main` reaches this machine; `/flotilla:update` moves it to `stable` |
| no user entry; the project pins a tag older than the newest | info | a newer release exists; `/flotilla:update` |
| newest release unknown (offline) | - | nothing at session start; doctor says it could not ask |

A session start prints the warn row only, at most once a day on a machine (`updates-said` in the state
directory), so a fleet of twelve seats does not repeat it twelve times. Doctor prints every row.

## C. `flotilla update` and `/flotilla:update`

`flotilla update` reads and prints, changing nothing:

- the channel: the user entry (source, ref, autoUpdate) and what the machine's marketplace follows;
- the newest release and the commit `stable` names;
- every install: the user's, and each project or local install whose directory exists and is a main checkout
  (a `.git` directory; worktree records are left out - a seat's tree loads its main checkout's settings), with its
  version and whether it is behind;
- each of those projects whose committed `.claude/settings.json` pins the `flotilla` marketplace to a ref other
  than the channel, with the exact edit (not made: it is a change to that repository, its owner's to commit).

It ends with a plan and a mark (as `onboard write` does): the steps below that apply.

`flotilla update apply --confirm <mark> [--pin <ref>]` carries the plan out, the person's move, refused from a
background session and by the guard as `work approve` is:

1. Back up `<config>/settings.json` to the state directory, with the time in its name.
2. Set the user entry's `source.ref` to `stable` (or `--pin v<X.Y.Z>`), keeping every other key of the file.
3. `claude plugin marketplace add TensusDS/flotilla#<ref>` (user scope) - measured 4.
4. Write `autoUpdate: true` back on the entry when following `stable` - measured 5.
5. Read both back: the settings entry and `claude plugin marketplace list` name the new ref, else stop and say
   which step did not take, with the backup's path.
6. `claude plugin update flotilla@flotilla --scope user` when a user install exists; then in each listed project
   behind the newest, `claude plugin update flotilla@flotilla --scope <project|local>` run there. Each result is
   printed; a failure does not stop the others.
7. Say that running sessions keep what they loaded; a session started after this runs the new release.

A changed plan changes the mark, so the yes covers exactly what was shown.

`/flotilla:update` runs `flotilla update`, shows the report, asks one question - "Follow stable with automatic
updates (Recommended)" / "Move the pin to v<newest> only" / "Leave it" - and on a choice that changes something,
shows the plan and runs `apply --confirm` on the person's yes.

## D. Install instructions

README's install becomes:

    claude plugin marketplace add TensusDS/flotilla#stable --scope project
    claude plugin install flotilla@flotilla --scope project

then `/flotilla:update` to turn on automatic updates for the machine. Pinning a tag stays documented as the way to
choose when you move, with the conflict above and its way out. Troubleshooting gains the refusal's text and
`/flotilla:update`.

## What is left to the builder

Names of functions and files; the wording of messages within what the table says; how the report is laid out.

## Release

0.29.0 (a new command and a new channel). After the tag's CI is green: create `stable` at the tag. Then, on this
machine, with the person's yes already given (2026-11-04): `/flotilla:update` moves the user entry to `stable`
with auto-update and brings the user install up; the field test with two small projects follows.
