# flotilla

Run a fleet of Claude Code sessions on one project, on one machine, without them stepping on each other.

**Claude Code only.** flotilla needs the Bash tool, git worktrees and background sessions (`claude --bg`), so it
runs in the Claude Code CLI, the IDE extensions and the desktop app's Code tab. claude.ai chat and Cowork do not
install it.

flotilla gives each session a **post** (a role with a name and a fixed set of moves), its own **git worktree**, and a
shared **work ledger** where every handover, review verdict and merge is recorded with the evidence that makes it
true. It books the machine for long test runs so two suites do not starve each other, guards a few commands that do
silent damage, and routes the questions only a person can answer to one place.

- **What it is for:** a project with more work in flight than one session can hold - several features at once, each
  reviewed by a session that did not write it, merged by one session that owns trunk, and checked on the running
  build by another.
- **What it is not:** a framework for agents inside your product, a CI system, or a sandbox. Every session is an
  ordinary Claude Code session running as you.

**Why not Claude Code's agent teams?** Agent teams (experimental, off by default as of Claude Code 2.1.289) are
teammates spawned by one lead session: they share its working tree, in-process teammates are not brought back when the
lead is resumed, and a session has one team. flotilla coordinates **independent sessions** instead - each started on its
own with `claude --bg`, in its own git worktree, with its own permission mode, settings and plugins - through the work
ledger and Claude Code's cross-session messaging. A seat outlives the orchestrator, can be attached to and talked to
directly, and keeps the project context it built up over many tasks. Its post is enforced by the ledger, not only
described in a prompt: nobody accepts their own work, and nothing reaches trunk unaccepted.

Short reference of every command and skill: [USER_MANUAL.md](USER_MANUAL.md).

**Status:** pre-1.0. It has run three field fleets on real projects, the largest about a dozen sessions over two
days; their findings are in `docs/field-tests/`, and each fix landed with a test that failed first. Interfaces may
still change between minor versions.

## Requirements

- Linux or macOS (both run in CI on every commit). Two protections need Linux's `/proc` and are skipped on macOS,
  saying so: the memory floor before raising seats and before long runs, and stopping the processes a retired seat
  left running.
- Python 3.11 or newer as `python3` on your PATH - the hooks run through it
- git
- Claude Code 2.1.280 or newer, with background sessions (`claude --bg`)
- `gh` (the GitHub CLI), only if your project ships through GitHub pull requests or CI
- Enough memory for the fleet you raise - see [What it costs](#what-it-costs)

## Install

flotilla is its own marketplace. In the project a fleet will work on:

    claude plugin marketplace add TensusDS/flotilla --scope project
    claude plugin install flotilla@flotilla --scope project

`--scope project` on both lines records flotilla in the project's `.claude/settings.json`, so every session started in
the main checkout - the ones `flotilla spawn` raises included - loads it. That file enables flotilla and commits a
marketplace entry that points at this repository, so everyone who opens the project and trusts it installs flotilla
from here. Without `--scope project` on the first line the marketplace goes into your own settings only, and the
project's file names a plugin nobody else can find.

Then check the machine:

    /flotilla:doctor

It names anything missing (an old Python, an old Claude Code, a state directory it cannot write) and the fix.

## Your first fleet, step by step

A word you do not know yet (trunk, seat, receipt, tier) is in [Words used here](#words-used-here).

### 1. Onboard the repository (once)

In an interactive Claude Code session in the project's main checkout:

    /flotilla:onboard

flotilla reads what the repository already declares - test commands, CI workflows, the trunk branch, version files -
and then asks one question: **quick or custom**.

- **Quick** takes what it found and the recommended answer to everything else: every branch reviewed by another
  session, Claude Code's auto mode for background sessions (its classifier decides each call; no question waits on
  you),
  every guard on, one model for every seat. It says in plain words what that means - who merges into trunk, what is
  reviewed, what you are asked - shows every shell command the profile will run, and asks for one yes.
- **Custom** asks the few things it cannot read, one at a time: how work reaches trunk (pull requests, direct push, or
  local only), who authorizes a merge, how much work is reviewed, how background sessions get permission for their
  tools, which test commands must be green before shipping, and which models the seats run.

Either way nothing is written before your yes. Then it runs each test tier once on this machine, so a tier that is red
today is caught now and not by the first session that hands work over, and writes `.flotilla/project.toml` plus the
post templates in `.flotilla/posts/`.

Next it offers to publish them: commit `.flotilla/`, run the tiers over that commit, and push it to trunk on origin.
That step matters: **every session reads its rules from origin's trunk**, not from its own tree, so a session cannot
change the rules it works under - and until the profile is there, every ledger move is refused with that reason. It
pushes the profile and nothing else: if your tree has uncommitted work of your own, trunk has commits origin has not
(or the reverse), or trunk only takes pull requests, it says so and pushes nothing.

The permission question matters most:

| answer | what background sessions do | when to choose it |
|---|---|---|
| **auto** (recommended) | run in Claude Code's auto mode; its classifier decides each call, and no question waits on you | most projects: the fleet works without a question per command |
| **ask** | stop at every permission prompt; flotilla routes the question to you through the orchestrator - flotilla's own commands (status, fleet, ledger moves) included, unless you opt in (below) | you want to see every command the fleet runs, and will answer often |
| **rules** | run what your Claude Code allow rules permit, and are refused the rest | you have rules for the commands your project needs |

Auto mode is not offered for every model: Claude Code has said "auto mode unavailable for this model" for Haiku, and
`flotilla spawn` warns when a seat would run auto mode on such a model.

**Letting checked commands past auto mode's classifier - off unless you turn it on.** In auto mode Claude Code's
classifier judged flotilla's own commands by the conversation around them and refused some - a sender's push of
reviewed work as "Merge Without Review", then a plain ledger record. flotilla can answer "allow" for these itself,
which skips the classifier for them. It does so only when you opt in, in the profile on trunk: in the
`[permissions]` table onboarding wrote, change

```toml
skip_classifier_for_checked = false
```

to `true` (a profile written before 0.7.0 has no such line: add it under `[permissions]`, beside `mode` - never a
second `[permissions]` header, which makes the profile unreadable), commit, and bring it to trunk.

**Not recommended if you are new to Claude Code or to auto mode.** The classifier is one of Claude Code's own
safety checks; this replaces it, for these commands only, with flotilla's checks. Turn it on once you know what the
fleet does and want it to run without those refusals. With it off, a refused command waits for you as before.

The same opt-in governs `ask` mode: with it, flotilla answers the permission question for its own commands itself
instead of putting it to you; without it, those questions reach you like any other - a fleet makes dozens of
ledger moves an hour, so expect many.

When you have opted in, flotilla's guard hook, which runs before every Bash call, answers "allow" in auto mode only,
after every guard has run and found nothing, and for two things:

- one of flotilla's own commands, judged as flotilla's parser reads it: a listed subcommand on this project's own
  repository, no character a shell would expand, no option that steps around a check - never `approve`, `spawn`,
  `fleet down`, `lane` or a run of an arbitrary command;
- the sender's push of accounted work: typed as exactly `git -C <its tree> push origin HEAD:<trunk>` by the session
  that owns that tree, in a repository whose own git config holds nothing that runs code or sends a push elsewhere,
  with a green receipt and every commit past origin's trunk accounted for by the ledger. The rules that grant it -
  the flow, the posts, whether a person approves merges - are read at the revision origin names as its trunk, and
  the checkout's `origin/<trunk>` must be that revision. A reviewed row counts only if every move on it was made
  under that same `.flotilla` (so work recorded before 0.6.11, or before the last change to `.flotilla`, is the
  classifier's to judge). It runs rewritten: the commit that was checked, pushed with no repository hook.

Anything else is the classifier's to decide, as before; unreviewed work still meets it. The opt-in counts only as
origin's trunk carries it: before each such allow flotilla asks origin for its default branch and that branch's
revision (`git ls-remote`, with no repository hook) and reads the profile there, so a session cannot switch it on by
editing its own tree or moving a local ref. Offline, or with a repository config that could run code on that call,
nothing is allowed and the classifier decides. Your own `deny` rules still
win over this allow, and in `ask` mode nothing changes. This leans on Claude Code honouring a hook's allow, deny and
rewritten input, measured on 2.1.288.

You can change any answer later by editing `.flotilla/project.toml`; a change takes effect once it is on trunk.

### 2. Raise the fleet

Onboarding ends by offering to raise the fleet now, three ways:

- **This session leads it** (recommended). The session you onboarded in becomes the orchestrator: flotilla gives it
  the orchestrator's name (`flotilla spawn --lead`; Claude Code shows the name with your next message) and, on that
  message, raises the rest of the fleet in the background with `flotilla spawn --fill`. You talk to the fleet and answer its permission
  questions right here.
- **A background orchestrator.** Every seat, the orchestrator included, starts in the background; you attach to talk.
- **Not now.** Raise it later:

      /flotilla:spawn

With no arguments, and no live leader, it first asks who leads - this session (`flotilla spawn --lead`, then the rest
on your next message) or a background orchestrator - and raises the composition onboarding suggested (`[fleet] default`
in the profile: one orchestrator, one sender, one main session, and a reviewer when work is reviewed); when this
session leads, it is that orchestrator. When the fleet would have nobody to lead it or nobody to merge - a profile
written by an older flotilla may lack both - `flotilla spawn` prints a `gap:` line, and `/flotilla:spawn` asks you how
to fill it. `--fill` raises only the seats of that composition
this project's live sessions do not hold yet. You can name one: `-o 1 -s 1 -r 1 -M 2` is one orchestrator, one sender, one reviewer and two main
sessions. Add `--dry-run` to see the names, trees, commands and the plugins each seat will have turned off, without
raising anything.

Each session is a background Claude Code session (`claude --bg`), named by the project and its post
(`worldcore-main session 3`, `worldcore-review session 1`), with its own worktree beside the repository
(`<repo>-main-3`). Numbers run per project. Session names are addresses on the whole machine - messages between
sessions are delivered by name - so the project word (`[fleet] name`, set at onboarding from the directory) is unique
on the machine: a second project that wants a word already taken gets `worldcore-2`, and flotilla says so. Plugins that bring MCP servers are turned
off for a seat unless its post declares them, so a reviewer does not carry a browser it never uses; MCP servers you
configured outside plugins still start in every seat. `flotilla spawn` refuses to raise seats when free
memory would fall below `fleet.memory_floor_mb` (2000 MB by default), counting about 800 MB per seat it is about to
raise; `--anyway` overrides.

**How big a fleet.** `flotilla fleet size` recommends one from this machine and this project, and names the cap that
set it; `/flotilla:spawn` with no composition shows it and asks before raising anything:

```
recommended: orchestrator 1 (this session), main 2, minor 1, reviewer 1, sender 1
authors 3 = max(1, min(backlog 3, memory 3, test runs 3, disk 200)) - limited by backlog
```

Authors are the smallest of five caps: the **backlog** (ledger rows claimed or being fixed, tasks the orchestrator
names with `--tasks N` or `--tasks-file`, open GitHub issues when `[fleet.sizing] tracker = "github"`, and open items
in TODO files), **memory** (free memory above the floor, plus what this project's running seats already hold, a
seat's cost for each seat the fleet brings, and room for one test run), **test runs** (how many handovers the lane can
verify in an hour - from the time and peak memory the receipts recorded on this machine, the peak counting every
process a tier starts, a browser in its own process group included - against how fast an author hands over,
measured from the ledger), **disk** (half the free disk, in seat trees) and **max seats** (12 seats on a machine
unless `max_seats` in `machine.toml`, the person's own file, says otherwise; a profile may only lower it). Reviewers
grow with the authors and the measured review time, never past the number of authors; a judge comes when the profile
requires one or has a deploy target. Free memory is the room under a container's or systemd slice's memory limit
where that is smaller than the host's - for the sizing, for spawn's seat check and for the lane's floor alike. A signal
that could not be read is named and left out, never guessed; with free
memory unknown, `spawn --recommended` raises nothing without `--anyway`. An item counts as minor only where its source
says so (a `[minor]` prefix, a `size:small` label). The recommendation is the whole fleet: `flotilla spawn
--recommended` raises it less the posts live sessions already hold. Not measured yet: how the code splits into
independent areas, and subscription limits - later releases.

`[fleet.sizing]` in the profile: `tracker` (`"github"`), `labels` (issues with any of them), `minor_labels`
(`["size:small"]`), `backlog_files` (globs from the repository root over the files git tracks or would track,
`["TODO.md"]` by default, `[]` for none), `minor_prefix` (`"[minor]"`), `max_seats` (lower than the machine's
ceiling, never higher).

### 3. Talk to the orchestrator

If this session leads the fleet, you are already talking to it. Otherwise:

    claude attach <the orchestrator's id>

`flotilla spawn` prints the id; `flotilla fleet` lists every seat with its id later. Tell it what to build. It assigns tasks, raises helpers when a session needs one,
relays your answers, and tells you when the queue is empty and the work is ready for you to check.

### 4. Watch

    /flotilla:status     who is idle, working, waiting on whom; whose move each open row is
    /flotilla:watch      what needs attention: dropped balls, deviations, a move nobody alive can make
    /flotilla:lane       who holds the machine for long runs, and why a run waits

### 5. Stand it down

    /flotilla:down

It retires every session but your own and names the work left open so the next fleet can pick it up with
`flotilla work adopt`. A seat's tree and branch go with it when their work is surely on trunk - nothing uncommitted,
untracked or stashed, nothing ignored but what an install or a build makes again, no open row, every commit on
origin's trunk (or a squash whose exact tip the ledger shipped), no commit only its reflog reaches. Anything in doubt
stays, and says why. `flotilla fleet clean` sweeps the same way for what a forced stop or an older fleet left - only
what the fleet made, never a tree or branch of your own: a plan first, `--yes` to act.

## Examples

Four things to try once the repository is onboarded. Each is typed in Claude Code; what happens is what flotilla
does today.

1. **Raise a fleet.** Type `/flotilla:spawn`. It asks who leads - this session or a background orchestrator - shows
   the plan first (seat names, worktrees, branches, the permission mode, any warning), asks about anyone the fleet
   would lack, and raises the seats on your yes. Each gets its own worktree and its post's instructions.
2. **Hand the fleet a feature.** To the orchestrator: *"Add CSV export to the reports page. Build the API and the UI
   in parallel, and have each reviewed by a session that did not write it."* It assigns the parts; each builder
   claims a branch in its own tree, hands it over with the revision it handed, a reviewer reads exactly that
   revision, and the sender ships what was accepted. Every step lands in the ledger with its evidence.
3. **Ask where things stand.** Type `/flotilla:status`, or ask the orchestrator *"Who is waiting on whom?"*. You get
   each session's state - idle, working, waiting and on whom, blocked - and whose move each open piece of work is.
4. **Answer once for a batch.** Type `/flotilla:brief`. It shows every piece of work ready to ship, who accepted it
   over which revision, what is held back and why, and what verifies it. Answer yes once; where your profile has
   you approve merges, each branch's approve command is printed ready to type.

---

## Contents

Setup is above. The rest of this file:

1. [How a fleet works](#how-a-fleet-works)
2. [Words used here](#words-used-here)
3. [The posts](#the-posts)
4. [The work ledger](#the-work-ledger)
5. [The person's part](#the-persons-part)
6. [Long runs and the lane](#long-runs-and-the-lane)
7. [Guards](#guards)
8. [What it costs](#what-it-costs)
9. [What flotilla does on your machine](#what-flotilla-does-on-your-machine)
10. [Known limitations](#known-limitations)
11. [Security model and its limits](#security-model-and-its-limits)
12. [Updating, and removing flotilla](#updating-and-removing-flotilla)
13. [Development](#development)
14. [Support, privacy and security](#support-privacy-and-security)
15. [License](#license)

---

## How a fleet works

A typical day, in a project onboarded to flotilla:

1. You raise the fleet with one command: an **orchestrator**, two or three **main** sessions to build, a
   **reviewer**, a **sender** that owns trunk, and, if your project has a running build, an **acceptance judge**.
2. You tell the orchestrator what you want built. It splits the work into tasks, hands them out, and raises more
   sessions if the work needs them.
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

## Words used here

- **trunk** - the branch work is delivered to (`main`, usually).
- **seat** - one fleet session holding one post, with its own worktree.
- **post** - a role: what a session is for and which ledger moves it may make.
- **ledger** - the shared record of every piece of work and every move on it.
- **row** - one piece of work in the ledger: a branch, its owner, its state.
- **test tier** - one of the project's test commands that must be green, named at onboarding.
- **receipt** - a record that the tiers ran green over one exact revision; a move that needs one asks for it.
- **census** - Claude Code's list of live sessions (`claude agents`), which flotilla asks to tell who is calling.
- **walk** - the judge using shipped work on the running build the way a person would.

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
the `plugins` it keeps, or narrow its permission mode (a post can narrow the profile's mode, never widen it). One
exception to a post's own `model`: when onboarding chose the strongest model for reviewers, every post that may accept
runs on it, whatever its file says.

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
  batch (`/flotilla:brief`) and the exact command; you type it with `!` in front in your own Claude Code session -
  the orchestrator's session too, when it is yours - or run it in a terminal. Claude never runs it for you: the
  guard refuses it as a tool call, because a model in your session would otherwise pass for you. A background
  orchestrator's prompt is not yours: `!` there runs in a background session and is refused. The sender's queue, the merge check, the push guard and the
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

The lane is also where a fleet loses its time. Measured on a fleet that ran three days: the way from "handed over"
to "on trunk" grew about tenfold after the first hours, almost all of it waiting for the lane, while the model's own
turns slowed about twofold as sessions' contexts grew. So flotilla spends the lane carefully:

- **nothing is tested twice** - a test tier already green over the same files (an author's tip and a sender's merge
  that changes no file) is reused, and a receipt with nothing left to run does not queue at all;
- **a run has a ceiling** - for a receipt, ten times the slowest tier measured on this machine and at least ten
  minutes; for any other run, at least half an hour; `[lane] max_run_seconds` in the profile or `--max` for one run
  set another. Past it the run is stopped with every process it started and recorded as killed. `flotilla lane stop`
  stops your own run sooner;
- **a fresh tree is set up once** - the profile's `tests.setup_command` (`npm ci` beside a `package-lock.json`, found
  at onboarding) runs before a tree's first receipt and again only when its lockfile changes.

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

Before you start, know plainly:

- **Sessions keep running** after you close the terminal you raised them from; they stop when you retire them or
  stand the fleet down. A message sent to a retired background session wakes it again.
- **There is no usage cap in flotilla.** A fleet spends until you stand it down; watch your plan's usage.
- **`auto` mode means unattended commands run as you**, decided by Claude Code's classifier, in your project and with
  your credentials. Start with `ask` until you have seen what the fleet does.

## What flotilla does on your machine

So nothing comes as a surprise:

- **Hooks.** At session start and before each prompt it prints a few lines: who the session is and whose move it
  holds. Before each Bash command it runs the guards, which may refuse the command. When a background session tries to
  stop while it holds a move and records no wait, it is asked once to move or record the wait. A background session
  other than the orchestrator that tries to ask you a question (`AskUserQuestion`) is refused and told to send it to
  the orchestrator, since nobody is attached to answer it. In `ask` mode a background session's permission request
  goes to the orchestrator; it is refused when nobody answers in nine minutes, at once when no orchestrator is alive,
  and always for the orchestrator's own requests - answer those by attaching to the orchestrator.
- **Background sessions.** `flotilla spawn` starts `claude --bg` sessions with the permission mode your profile names
  and each post's instructions as their system prompt. They run as you, with your credentials.
- **Worktrees.** Each seat gets a git worktree beside the repository (`<repo>-<post>-<n>`). Retiring a seat keeps its
  tree; remove it with `git worktree remove` when nothing in it is needed.
- **Stopping processes.** When you retire a seat, flotilla stops the session, then sends SIGTERM to processes the
  session left running detached - a dev server or a browser started with `nohup` - in its tree or its job directory.
  It spares anything started after the stop, anything another user owns, systemd services, anything with a terminal,
  and anything under a live session, and it names what it stopped and what it spared.
- **State.** The ledger, receipts, permission questions and override records live in `${FLOTILLA_STATE_DIR}` if set,
  else `${XDG_STATE_HOME:-~/.local/state}/flotilla/`, closed to other users (0700). A permission question carries
  the call it asks about (a command, a file's new content); that call is dropped as soon as the asking session has
  its answer or stops waiting, and the rest of the record - who asked, when, the answer - a day later. The directory survives plugin updates and uninstall on purpose; `flotilla doctor`
  prints its path.
- **Git hooks.** `pre-commit` and `pre-push` are written into `.git/hooks/` only when you say yes in `/flotilla:guard`.
- **Network.** `git` to your repository's origin (`ls-remote`, `fetch`, and the one push of `onboard publish`) and
  `gh` to GitHub (pull requests, CI runs), with your credentials; nothing to any other host. The full list is in
  [PRIVACY.md](PRIVACY.md#network).

## Known limitations

What flotilla does not do yet, or does only partly. Each one was met in a field test; the test reports are in
`docs/field-tests/`.

- **No Windows.** Linux and macOS only; see [Requirements](#requirements) for what macOS skips.
- **One lane per machine, shared by every project's fleet.** At the default `lane_capacity` of 1, two fleets on one
  machine wait for each other's long runs. The lane spends its time carefully (see [Long runs and the lane](#long-runs-and-the-lane)), but it does not
  split the machine between projects.
- **Long-lived seats get slower.** A model turn over 600k tokens of context took about twice as long as one under
  100k. flotilla does not replace a seat by itself yet; after many tasks, retire a seat that holds no open work
  with `/flotilla:retire` and raise a fresh one with `flotilla spawn --fill`. Your own leading session is not
  replaced that way - start a fresh session and lead from it.
- **In `ask` mode, permission questions wait for the orchestrator** - and when the orchestrator is your own session
  and is itself waiting on you, a seat's question waits too and is refused, after about nine minutes by default; an
  answer you give after that is not kept, and the seat has to ask again. `auto` mode, which onboarding recommends,
  routes no such questions.
- **The leading session's name comes through a hook** that measured working on Claude Code 2.1.287. If your version
  does not show the name after three messages, flotilla tells the session to ask you for `/rename <name>`.
- **flotilla reads three of Claude Code's own records**: the session list of `claude agents --json` (a published
  interface), and two files that are not - the session registry in `~/.claude/sessions/` and the trust record in
  `~/.claude.json`. A Claude Code release that changes one of them does not break flotilla with an error; it blinds
  one of its checks. After updating Claude Code, run `flotilla doctor`: its `census-shape`, `registry` and
  `trust-record` lines compare each record with the shape measured on Claude Code 2.1.289 and name what goes blind.
- **Auto mode is not offered for every model.** Claude Code has refused it for some (seen with Haiku);
  `flotilla spawn --dry-run` warns about such a seat, and `flotilla spawn` warns again after raising it - pick
  another model or `ask` mode for that post.
- **Most of a project's rules are read from origin's trunk as this checkout last fetched it** - the local
  `refs/remotes/origin/<trunk>`, or the tree's own profile before onboarding reaches trunk. A session that moves
  that ref, or renames trunk in its own profile, changes the rules the next flotilla command reads: the revert and
  line-number guards, the lane, file reservations, event scripts. The classifier sees the command that does it.
  The push guard, the `pre-push` hook, the person's approval and every allow ask origin instead - but a session can
  still reconfigure its own checkout around a client-side guard; see
  [Security model and its limits](#security-model-and-its-limits) for what protects trunk.
- **A broken profile on trunk closes trunk.** If the profile on origin's trunk cannot be read, every push to trunk
  is refused, the one that would fix it included. You fix it from your own terminal with `git push --no-verify`.
- **It does not isolate sessions from you or from each other** - see the next section.

## Security model and its limits

flotilla assumes a fleet session can be **overeager or misled** - it read a malicious file, a web page, or a message
from another session - and keeps such a session from acting for you: it cannot answer permission questions, record
onboarding answers, approve merges, widen its own permission mode, push to trunk without a receipt over what it
pushes, or record a move under another session's name. Text one session writes reaches you and other sessions as
data, never as instructions.

It does **not** defend against a determined program running as your user. Every session runs as you, so a session
that writes the ledger file directly, detaches a process with a pseudo-terminal of its own, or drives the model in
your own interactive session can still pass for you. Against that, the last gate is Claude Code's own permission
prompt in `ask` mode. If you need isolation from the code a fleet works on, run Claude Code inside a sandbox.

**The push guards are a check on what a session pushes, not a lock on your repository.** They read trunk and its
rules from origin, read git's history with replace refs and grafts switched off, and the `pre-push` hook asks the
destination git itself names. They catch a session that pushes unreviewed or unapproved work by mistake, or because
it was told to. A session set on getting around them still can: it owns its checkout - its git config, its remotes,
its objects, `git push --no-verify` - and a guard running on that checkout can only judge what it is shown. The lock
is on the server: **protect your trunk on GitHub** (Settings → Rules → Rulesets, or Branches → branch protection) so
it accepts changes only through pull requests with the reviews and checks you require. The pull-request flows work
with that on; the `direct` flow pushes to trunk itself, so pick a pull-request flow, or let the ruleset allow the
sender's pushes knowing what that leaves to flotilla. With such protection, the push guards are a second, earlier
line rather than the only one.

The 2026-10-01 security review, its 25 findings and how each was fixed are recorded in
`docs/specs/2026-09-22-decisions-log.md` (decisions 171-189).

## Updating, and removing flotilla

**Update.** Finish or stand down the running fleet first, then:

    claude plugin marketplace update flotilla
    claude plugin update flotilla@flotilla --scope project

Restart the sessions so they load the new version, and bring `.flotilla/posts/` up to the new templates (see
[The posts](#the-posts)). What changed in each release: [CHANGELOG.md](CHANGELOG.md).

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

## Support, privacy and security

- Questions and defects: [GitHub issues](https://github.com/TensusDS/flotilla/issues) - see [SUPPORT.md](SUPPORT.md).
- What flotilla reads, stores and sends - only to your own origin and GitHub: [PRIVACY.md](PRIVACY.md).
- A vulnerability: report it privately - see [SECURITY.md](SECURITY.md).

## License

MIT - see `LICENSE`.
