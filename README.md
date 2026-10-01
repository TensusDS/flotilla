# flotilla

Run a fleet of Claude Code sessions on one project, on one machine, without them stepping on each other.

flotilla gives each session a **post** (a role with a name and a fixed set of moves), its own **git worktree**, and a
shared **work ledger** where every handover, review verdict and merge is recorded with the evidence that makes it
true. It books the machine for long test runs so two suites do not starve each other, guards a few commands that do
silent damage, and routes the questions only a person can answer to one place.

- **What it is for:** a project with more work in flight than one session can hold - several features at once, each
  reviewed by a session that did not write it, merged by one session that owns trunk, and checked on the running
  build by another.
- **What it is not:** a framework for agents inside your product, a CI system, or a sandbox. Every session is an
  ordinary Claude Code session running as you.

Short reference of every command and skill: [USER_MANUAL.md](USER_MANUAL.md).

**Status:** pre-1.0. It has run three field fleets on real projects, the largest about a dozen sessions over two
days; their findings are in `docs/field-tests/`, and each fix landed with a test that failed first. Interfaces may
still change between minor versions.

---

## Contents

1. [How a fleet works](#how-a-fleet-works)
2. [Requirements](#requirements)
3. [Install](#install)
4. [Your first fleet, step by step](#your-first-fleet-step-by-step)
5. [The posts](#the-posts)
6. [The work ledger](#the-work-ledger)
7. [The person's part](#the-persons-part)
8. [Long runs and the lane](#long-runs-and-the-lane)
9. [Guards](#guards)
10. [What it costs](#what-it-costs)
11. [What flotilla does on your machine](#what-flotilla-does-on-your-machine)
12. [Security model and its limits](#security-model-and-its-limits)
13. [Updating, and removing flotilla](#updating-and-removing-flotilla)
14. [Development](#development)

---

## How a fleet works

A typical day, in a project onboarded to flotilla:

1. You start one **orchestrator** session and tell it what you want built. It splits the work into tasks.
2. It raises the sessions the work needs: two or three **main** sessions to build, a **reviewer**, a **sender** that
   owns trunk, and, if your project has a running build, an **acceptance judge**.
3. A main session **claims** a branch in its own worktree, builds, runs the project's tests, and **hands** the branch
   over at its tip.
4. The reviewer **takes** it, reads it, and either **accepts** that exact revision or returns it with what must change.
5. The sender **queues** accepted work, merges it, pushes it, and the ledger asks origin whether it **shipped**.
6. The judge **walks** the shipped work on the deployed build, the way a person would, and records what broke.
7. When a session needs you - a permission it cannot get, a decision only you can make, a batch you must approve -
   the orchestrator puts it to you, one question at a time.

Nobody keeps state in their head or in chat. "Where does this stand" is answered by `flotilla status`, read from the
ledger, and every move is refused unless its evidence holds: you cannot hand work over without a green test receipt
over its tip, accept a revision you did not read, or say "shipped" before origin says so.

## Requirements

- Linux or macOS (both run in CI on every commit)
- Python 3.11 or newer as `python3` on your PATH - the hooks run through it
- git
- Claude Code 2.1.280 or newer, with background sessions (`claude --bg`)
- `gh` (the GitHub CLI), only if your project ships through GitHub pull requests or CI
- Enough memory for the fleet you raise - see [What it costs](#what-it-costs)

## Install

flotilla is its own marketplace. In the project a fleet will work on:

    claude plugin marketplace add TensusDS/flotilla
    claude plugin install flotilla@flotilla --scope project

`--scope project` records it in the project's `.claude/settings.json`, so every session started in the main
checkout - the ones `flotilla spawn` raises included - loads flotilla. That file enables flotilla and commits a
marketplace entry that points at this repository, so everyone who opens the project and trusts it installs flotilla
from here.

Then check the machine:

    /flotilla:doctor

It names anything missing (an old Python, an old Claude Code, a state directory it cannot write) and the fix.

## Your first fleet, step by step

### 1. Onboard the repository (once)

In an interactive Claude Code session in the project's main checkout:

    /flotilla:onboard

flotilla reads what the repository already declares - test commands, CI workflows, the trunk branch, version files -
and asks you only what it cannot read: how work reaches trunk (pull requests, direct push, or local only), who
authorizes a merge, how much work is reviewed, how background sessions get permission for their tools, and which test
commands must be green before shipping.

Before it writes anything, it shows you the whole profile it will write and every shell command in it, and waits for
your yes. Then it runs each test tier once on this machine, so a tier that is red today is caught now and not by the
first session that hands work over. The result is `.flotilla/project.toml` plus the post templates in
`.flotilla/posts/`. Commit both and bring them to trunk: **every session reads its rules from trunk**, not from its
own tree, so a session cannot change the rules it works under.

The permission question matters most:

| answer | what background sessions do | when to choose it |
|---|---|---|
| **ask** | stop at every permission prompt; flotilla routes the question to you through the orchestrator | you want to see what the fleet does, or you are trying flotilla for the first time |
| **rules** | run what your Claude Code allow rules permit, and are refused the rest | you have rules for the commands your project needs |
| **auto** | run in Claude Code's auto mode; its classifier decides | you trust the fleet with the project and want it to run unattended |

### 2. Raise the fleet

    /flotilla:spawn

With no arguments it raises the composition onboarding suggested (`[fleet] default` in the profile). You can name one:
`-o 1 -s 1 -r 1 -M 2` is one orchestrator, one sender, one reviewer and two main sessions. Add `--dry-run` to see the
names, trees, commands and the plugins each seat will have turned off, without raising anything.

Each session is a background Claude Code session (`claude --bg`), named by its post (`main session 3`, `review
session 1`), with its own worktree beside the repository (`<repo>-main-3`). Each starts only the MCP servers its post
declares, so a reviewer does not carry a browser it never uses. `flotilla spawn` refuses to raise seats when free
memory would fall below `fleet.memory_floor_mb` (2000 MB by default), counting about 800 MB per seat it is about to
raise; `--anyway` overrides.

### 3. Talk to the orchestrator

    claude attach <the orchestrator's id>

`flotilla spawn` prints the id. Tell it what to build. It assigns tasks, raises helpers when a session needs one,
relays your answers, and tells you when the queue is empty and the work is ready for you to check.

### 4. Watch

    /flotilla:status     who is idle, working, waiting on whom; whose move each open row is
    /flotilla:watch      what needs attention: dropped balls, deviations, a move nobody alive can make
    /flotilla:lane       who holds the machine for long runs, and why a run waits

### 5. Stand it down

    /flotilla:down

It retires every session but your own, keeps their worktrees, and names the work left open so the next fleet can
pick it up with `flotilla work adopt`.

## The posts

A post is a file in `.flotilla/posts/`: a name pattern, the ledger moves the post may make, the model it runs, and the
instructions its session starts with. The templates:

| post | name | what it does | moves it may make |
|---|---|---|---|
| orchestrator | `orchestrator N` | splits work into tasks, assigns readers, relays the person's answers, raises and retires seats | assign, hold, unhold, adopt, release, urgent, walkable, wait |
| main | `main session N` | builds large work in its own tree, hands it over green | claim, hand, moved, close, release, wait |
| minor | `minor session N` | small fixes, the same moves as main | claim, hand, moved, close, release, wait |
| reviewer | `review session N` | reads handed work and accepts a revision or returns it | take, accept, fix, recuse, vouch, wait |
| sender | `sender N` | the only session that writes trunk, the version counter and the CI queue | queue, land, ship, inbatch, offledger, return, release, wait |
| acceptance judge | `acceptance judge N` | walks shipped work on the running build, records what broke | walked, broke, unbroke, wait |
| helper | `helper N` | a short-lived session raised by another for a piece of its work, in its own tree | release, wait |

Every post may also `reserve` its home branch. A post's file is yours to edit: change its instructions, its `model`,
the `plugins` it keeps, or narrow its permission mode (a post can narrow the profile's mode, never widen it).

**Plugin updates change the templates, not your posts.** When you update flotilla, compare `.flotilla/posts/` with the
new templates (`template_version` in each file's header says which you have) and bring them up to date, keeping your
own edits. A post older than the plugin may lack moves the plugin now expects.

## The work ledger

Every piece of work is a **row**: a branch, its owner, its state, and the history of moves that brought it there. The
states, in order:

    claimed -> handed -> accepted -> queued -> landed -> shipped -> walked -> closed

with `fixing` when a reviewer returns work, and `released`, `offledger` and `inbatch` for work that will not happen,
reached trunk outside the ledger, or was born while merging a batch.

Each move asks for the evidence that makes it true:

- **hand** needs a green **receipt**: `flotilla receipt run --purpose handover`, which runs the project's test tiers
  over exactly that revision. A receipt over another revision does not count.
- **accept** names the revision the reviewer read; if the branch moves afterwards, the verdict no longer stands.
- **queue** needs the accepted revision, and - where a person authorizes merges - your approval of it.
- **land** names the trunk commit that carries the work, and is refused when the batch carries commits nobody read.
- **ship** asks origin or the pull request; nobody types "shipped".
- **close** belongs to the owner, once the work is delivered.

`flotilla work show <branch>` prints a row and its history. `flotilla status --stalled 2` adds the rows that have not
moved for two hours. The ledger lives in the state directory, not in git, and every session reads the same one.

## The person's part

flotilla is built so that you are asked as rarely as possible, and so that what you are asked is exactly what you
approve.

- **Permission questions.** In `ask` mode a background session cannot show you a prompt; its question goes to the
  orchestrator, which shows it to you with `/flotilla:permit`: who asks, the full command or input, the directory it
  runs in, and three answers - allow once, allow for the session (what that adds is said in words), or deny with a
  reason. Everything a question shows was written by another session, so it is printed as data: control characters
  and hidden Unicode are made visible, and nothing is cut. An allow carries a mark over exactly what you were shown, so
  it cannot be recorded for another question, or for this one changed since. A question nobody answers in nine
  minutes (`[broker] wait_seconds`) is refused on its own.
- **Approving merges.** If onboarding recorded that a person authorizes merges, no work reaches trunk until you run
  `flotilla work approve <branch>` yourself, for the revision the reviewer accepted. The orchestrator shows you the
  batch (`/flotilla:brief`) and the exact command. The sender's queue, the merge check, the push guard and the
  pre-push hook all ask for your approval, and there is no override for it.
- **Onboarding.** Only you record onboarding answers and write the profile; a background session is refused.
- **Questions of judgement.** A session that needs your decision records `flotilla work wait <branch> --on "the
  person" --why "<what>"` and tells the orchestrator, which brings it to you.

Never answer Claude Code's prompt for `flotilla permit answer` or `flotilla work approve` with "don't ask again":
that turns every later answer into the model's.

## Long runs and the lane

Two full test suites at once can exhaust a machine's memory, and a run killed that way can look green to the command
that started it. The **lane** books the machine for long runs:

    flotilla lane run --for <branch> -- <command>

It waits its turn, runs the command, releases the booking and records the run on the row. `/flotilla:lane` shows who
holds it, who waits, unbooked runs computing, low memory, and CI using this machine. How many long runs may share the
machine is `lane_capacity` in the machine profile (`machine.toml` in the state directory, 1 by default). The lane is a
booking, not a lock: a run started outside it is not stopped - the command guard warns about it.

## Guards

Before a Bash command runs, flotilla refuses a few commands that do silent damage:

- **revert** - a checkout, restore, reset or clean that would destroy uncommitted work; it names the fix that works
  for that form. It reads `git clean` the way git does (abbreviations, `--no-dry-run`, `-e` values) and lists what
  would be removed without ever running a clean itself.
- **line edit** - an in-place `sed` edit addressed by line number, which lands on the wrong line after any other edit.
- **push receipt** - a push to trunk or a tag, `gh pr create`, `gh pr merge` or `gh workflow run` without a green
  push receipt over exactly what is pushed. `gh pr merge` must name the checked head with `--match-head-commit`, so a
  later push to the pull request cannot be merged in its place. A door is judged by the project it acts on, wherever
  the session stands. `FLOTILLA_GATE_OVERRIDE="<why>"` at the head of a push lets a missing receipt through and
  records the reason; it never waives a missing approval.
- **lane** - a warning, not a refusal, when a long run starts outside the lane.

Two git hooks back them, installed only with your yes, one each (`/flotilla:guard`): `pre-commit` refuses a rewrite of
a shared file another open row has reserved (appends always pass), and `pre-push` asks git what is actually pushed,
so a push hidden inside a script is judged too.

The command guards read the command line, not the shell: a command inside `eval`, `sh -c`, `$( )` or a script is not
seen. The pre-push hook covers pushes; nothing covers `gh api` calls that merge or move refs directly.

## What it costs

- **Memory.** Each background session is a full Claude Code process. On the field fleets a seat took about 0.8 GB
  with its MCP servers narrowed to what its post declares, and several times that when every seat started every MCP
  server the user had enabled.
- **Usage.** Each session is billed like any Claude Code session you start yourself; a fleet of six costs roughly what
  six sessions cost. Reviews read whole branches; onboarding can put reviewers on the strongest model, which costs
  more per review.
- **Your attention.** In `ask` mode, expect permission questions until your allow rules cover the commands the project
  needs; `/flotilla:permit` answers them one at a time.

## What flotilla does on your machine

So nothing comes as a surprise:

- **Hooks.** It runs on Claude Code's session start, before each prompt, before each Bash command (the guards), when a
  session stops, and on permission requests. They read the ledger and print a few lines; the Bash hook may refuse a
  command.
- **Background sessions.** `flotilla spawn` starts `claude --bg` sessions with the permission mode your profile names
  and each post's instructions as their system prompt. They run as you, with your credentials.
- **Worktrees.** Each seat gets a git worktree beside the repository (`<repo>-<post>-<n>`). Retiring a seat keeps its
  tree; remove it with `git worktree remove` when nothing in it is needed.
- **Stopping processes.** When you retire a seat, flotilla stops the session, then sends SIGTERM to processes the
  session left running detached - a dev server or a browser started with `nohup` - in its tree or its job directory.
  It spares anything started after the stop, anything another user owns, systemd services, anything with a terminal,
  and anything under a live session, and it names what it stopped and what it spared.
- **State.** The ledger, receipts, permission questions and override records live in `${FLOTILLA_STATE_DIR}` if set,
  else `${XDG_STATE_HOME:-~/.local/state}/flotilla/`, closed to other users (0700). Closed permission questions are
  deleted a day after their answer. The directory survives plugin updates and uninstall on purpose; `flotilla doctor`
  prints its path.
- **Git hooks.** `pre-commit` and `pre-push` are written into `.git/hooks/` only when you say yes in `/flotilla:guard`.
- **Network.** Only what your project's own commands do, plus `git` and `gh` asking origin and GitHub about what
  shipped. flotilla itself sends nothing anywhere.

## Security model and its limits

flotilla assumes a fleet session can be **overeager or misled** - it read a malicious file, a web page, or a message
from another session - and keeps such a session from acting for you: it cannot answer permission questions, record
onboarding answers, approve merges, widen its own permission mode, push to trunk without a receipt over what it
pushes, or have its work recorded as read by someone who did not read it. Text one session writes reaches you and
other sessions as data, never as instructions.

It does **not** defend against a determined program running as your user. Every session runs as you, so a session
that writes the ledger file directly, detaches a process with a pseudo-terminal of its own, or drives the model in
your own interactive session can still pass for you. Against that, the last gate is Claude Code's own permission
prompt in `ask` mode. If you need isolation from the code a fleet works on, run Claude Code inside a sandbox.

The 2026-10-01 security review, its 25 findings and how each was fixed are recorded in
`docs/specs/2026-09-22-decisions-log.md` (decisions 171-188).

## Updating, and removing flotilla

**Update.** Finish or stand down the running fleet first, then:

    claude plugin marketplace update flotilla
    claude plugin update flotilla@flotilla --scope project

Restart the sessions so they load the new version, and bring `.flotilla/posts/` up to the new templates (see
[The posts](#the-posts)). Release notes are the tagged commits on GitHub.

**Remove.**

    claude plugin uninstall flotilla@flotilla --scope project

then, if you want nothing left: delete `.flotilla/` from the project, remove the seat worktrees (`git worktree list`
names them), delete `.git/hooks/pre-commit` and `.git/hooks/pre-push` if you installed them, and delete the state
directory.

## Development

```bash
uv run --with pytest python -m pytest -p no:cacheprovider tests/                   # the suite
uv run --python 3.11 --with pytest python -m pytest -p no:cacheprovider tests/     # the lowest supported Python
GIT_CONFIG_GLOBAL=/dev/null uv run --with pytest python -m pytest -p no:cacheprovider tests/   # no git config
python3 tools/check_no_cyrillic.py                                                 # the English-only gate
python3 tools/check_version.py                                                     # the version moved with the code
claude plugin validate .                                                           # the manifest
```

The design is in `docs/specs/2026-09-22-flotilla-design.md`; every decision since, with its reason, is in
`docs/specs/2026-09-22-decisions-log.md`.

## License

MIT - see `LICENSE`.
