# flotilla — design

- **Status:** design approved section by section (2026-09-22); this document awaits review as a whole.
- **Decision trail:** `2026-09-22-decisions-log.md` (numbered decisions, in the order they were taken).
- **Sources:** `../research/2026-09-22-skills-and-plugins.md`, `../research/2026-09-22-session-orchestration.md`,
  and a live probe of Claude Code 2.1.280 (section 13).

---

## 1. What flotilla is

flotilla is a Claude Code **plugin** that coordinates several **independent, peer Claude Code sessions** working on
the same codebase on one machine. It raises a fleet of named background sessions, gives each a **post**
(orchestrator, sender, reviewer, acceptance judge, main and minor implementers), cuts each its own git worktree,
and provides the machinery that keeps their work honest: a **work ledger** whose every move costs evidence, a
**lane** that books the machine for long runs, **guards** on dangerous commands, and **watchers** that deliver
"whose move is it" into the sessions themselves.

It is extracted from the fleet tooling of the ai-os project, where up to twenty sessions ran a day on one machine.
Everything project-specific is removed; the structure and the lessons stay.

### 1.1 Positioning

| need | use |
|---|---|
| helpers inside one session, results reported back | subagents |
| a lead session with teammates in one process tree | agent teams (experimental) |
| independent sessions, each in its own worktree, with review, merge and shipping accounted for | **flotilla** |

These are layers, not rivals. A flotilla `main` session may itself lead an agent team or run subagents; flotilla
sees one branch and one ledger row.

What flotilla adds that neither native feature has: work states anchored to git revisions (a verdict is over a
revision, "shipped" is asked of origin), an independent reader enforced by the ledger, a single writer of one-copy
resources (trunk, version, CI queue), per-session worktrees, and state that survives every session.

### 1.2 Principles

1. **State lives in the ledger, not in letters.** Cross-session messages are lossy by documentation (receiver
   queue capped at 50, held-message store at 100, a 5-minute approval deadline, duplicate drops). A letter is a
   notification of a ledger move, never the carrier of state. In the ai-os fleet 44 % of 1,599 letters in one
   shift carried nothing but state.
2. **Every move costs the evidence that makes it true.** Handing over needs the tip; accepting needs the revision
   read, equal to the tip handed over; "shipped" is asked of origin or of the PR, never typed.
3. **One writer per one-copy resource.** Trunk, version counter, CI queue: the sender only.
4. **A check is computed from the ledger and git; it is delivered by a hook into the session, not into a file.**
   A watcher that writes a file nobody opens is silent.
5. **Two answers at every fork:** "works the same" or "refuses out loud, naming the cause and the fix". An
   instrument that could not ask says "unknown", never "no" and never "green".
6. **Installed ≠ active.** Nothing happens in a project until it is onboarded.
7. **Supported surfaces first.** `claude agents --json`, hooks input, `claude plugin` commands. Internal files
   (`~/.claude/sessions/`, transcripts) are never parsed.

### 1.3 Non-goals (v1)

- Coordination across machines (the ledger storage sits behind an interface; a shared ledger is on the roadmap).
- Native Windows (WSL counts as Linux).
- Reading or mining session transcripts (format is internal; directory policy restricts conversation data).
- Installing OS schedulers (cron / launchd) — roadmap item, with consent.
- Replacing agent teams or subagents.

---

## 2. Audience, platforms, distribution

- **Public.** Target listing: the `claude-community` marketplace (third-party submissions land there after
  `claude plugin validate` and automated safety screening). `claude-plugins-official` is curated by Anthropic.
- **Plugin slug:** `flotilla` (immutable once published). Repository directory: `flotilla`.
- **Platforms:** Linux and macOS, both in CI. Native Windows is explicitly unsupported and refuses out loud.
- **Runtime:** Python ≥ 3.11 (stdlib only; `tomllib` for TOML). Onboarding verifies `python3` is a real 3.11+
  (the macOS Command Line Tools python is believed to be 3.9 — unverified on a live Mac).
- **Claude Code:** a minimum version is enforced by `flotilla doctor` (cross-session messaging needs 2.1.224+ per
  research; the exact floor is set in the foundation spec).
- **Language:** English everywhere in the repository (section 12).

---

## 3. Architecture

### 3.1 Three homes

```
PLUGIN   (ships with the install; identical for everyone; its directory changes on every update)
├── .claude-plugin/plugin.json         defaultEnabled: false
├── skills/flotilla/SKILL.md           the arrangement: census, claim, handover, shipping
│   └── references/                    why.md · macos.md · linux.md · posts.md
├── skills/flotilla-onboard/SKILL.md   onboarding
├── templates/posts/*.md               post templates (agent-definition format)
├── hooks/hooks.json                   session hooks → scripts/flotilla <entry>
├── scripts/flotilla                   the single entry point
└── flotilla/                          Python package
    ├── core/     platform · storage · config · census · identity
    ├── ledger/   moves, evidence, dependencies, events, metrics
    ├── watch/    whose move, deviations, dropped balls, trunk health
    ├── spawn/    names, worktrees, launch
    ├── lane/     machine booking and runs
    ├── guards/   command guards, Stop guard, git hooks
    └── onboard/  machine measurement, project detection, questionnaire

PROJECT  .flotilla/   (in the repository, committed, shared by the team)
├── project.toml     trunk · repos · test tiers · CI · release · fleet · guards · evidence · judge · deploy
├── posts/*.md       the project's posts: template copies plus custom posts
└── events/          scripts run on ledger moves

MACHINE  ${CLAUDE_PLUGIN_DATA}/   (per person; survives plugin updates)
├── machine.toml     capabilities measured by onboarding
├── ledger/          one append-only move log per repository, keyed by origin URL
├── lane/            bookings and run records
├── receipts/        test-tier receipts per revision
└── names.json       issued session numbers
```

Post templates live in `templates/`, **not** in the plugin's `agents/`: anything there is registered as a
subagent for every user of the plugin, onboarded or not. Exposing posts as subagent types (`.claude/agents/`) is
the human's opt-in during onboarding.

### 3.2 Dependencies

```
skills ──→ scripts/flotilla ──→ ledger ─┐
                               watch ───┤
                               spawn ───┼──→ core
                               lane ────┤
                               guards ──┤
                               onboard ─┘
spawn  ──→ ledger   (a spawned session gets a "post held" row)
watch  ──→ ledger, lane, core.census
guards ──→ ledger, lane (push receipt, file reservation)
```

Boundaries held by construction:

- **Skills call only `flotilla`** — no raw `git`, `sed`, `timeout` in skill text; platform differences live in code.
- **The ledger knows nothing of spawn.** Sessions opened by hand can use `flotilla work` just the same.
- **`core.storage` is the only writer of ledger files.** A shared ledger later replaces one class.
- **Code branches on capabilities, not on the OS name** (`proc_start = "ps"`, not `os = "darwin"`).

### 3.3 How a move runs

```
flotilla work hand feat/export
  1. transition legal for this profile?                  no → refuse + list legal moves from here
  2. caller's post allows this move (`may`)?             no → refuse, name the post that may
  3. evidence present and true?                          no → refuse with the reason
  4. dependencies satisfied (for moves they gate)?       no → refuse, name the row waited on
  5. `.flotilla/events/pre-handed` (if any)              exit 2 → move rejected, script text shown
                                                         crash/timeout → move refused, script named (6.8)
  6. append the move under a lock
  7. `post-handed` events (notifications; cannot undo)
  8. print the new state and whose move is next
```

---

## 4. Profiles and onboarding

### 4.1 Rule

The questionnaire asks only what is a human's **choice**. Everything findable in files is found and shown as a
prefilled "(detected)" answer: stack, test commands, trunk name, commit convention, version files, CI jobs,
merge methods allowed, OS and tools.

### 4.2 Steps

1. **Machine** (once per computer, no questions): OS, arch, python, git, gh (installed, authenticated), claude
   version and whether `claude agents --json` answers, process-start method (`procfs` or `ps`), `timeout` /
   `gtimeout` / none, memory and cores. Written to `machine.toml` as capabilities.
2. **Project** (once per repository; re-run with `flotilla onboard --check`): detection, then the questionnaire.
3. **First run:** every test tier runs once through the lane; `measured_seconds` is recorded. A red tier is **not**
   recorded as working — onboarding shows the tail and asks whether the command or the project is wrong.
4. **Composition:** suggested by size — start with main 1 + reviewer 1; add orchestrator 1 + sender 1 once the
   fleet exceeds three sessions; acceptance judge offered when a deployment is detected.
5. **Guards and git hooks:** each named in one line (what it refuses and why), each enabled by its own "yes".
   Onboarding states that Claude Code's background-isolation guard (`worktree.bgIsolation`) stays on.

### 4.3 Questionnaire

AskUserQuestion takes up to four questions per call; the core fits two rounds.

**Core — always asked**

| # | question | options | configures |
|---|---|---|---|
| 1 | How does work reach trunk? | **PR, merged by the sender after green checks (recommended)** · PR, merged by a human · direct push to trunk — for experienced users or simple projects: CI runs after the code is already in trunk · local only | sender post; meaning of `shipped` |
| 2 | Who authorizes the merge? | a human, per batch · the sender, after a green receipt | whether `brief` waits for a "yes" |
| 3 | Review depth | every branch by an independent reader · main work only · none | whether `accepted` is required |
| 4 | Background sessions and permissions | they will ask you (and wait) · allow rules already set · auto mode | spawn flags; warning that a session may stall on a prompt |
| 5 | CI | detected: GitHub Actions · own gate command · none | the gate of `shipped` (6.3) |
| 6 | Where does CI run? | cloud · self-hosted **on this machine** | lane also asks the CI queue |
| 7 | Test tiers required before shipping (multi-select) | detected tiers | `required_for` per tier |
| 8 | Where are tasks tracked? | nowhere · GitHub Issues · Jira/Linear (id pattern) · own register | `[evidence]`, events |

**Conditional — asked only when detection finds a reason**

| question | trigger |
|---|---|
| Several repositories — push order? | a path dependency on a sibling repo in a lockfile |
| Versions and tags — who bumps, which scale? | `vX.Y.Z` tags in history or version files found |
| A deployment the fleet touches? what surface (web / CLI / API)? | `deploy/`, systemd units, compose files, a dev-server script → offers the acceptance judge |
| Shared append-only files? | `CHANGELOG.md`, `TODO.md` and the like → file reservation |
| Sequential numbers in files? | migration or ADR directories → number claims (two branches both computing "max + 1" collide) |
| Can two full runs fit at once? | slow first run or little memory |
| Merge method? | more than one allowed by the repository settings |
| Model per post | always offered, default "one model for all" |

A simple project answers the eight core questions and sees no conditionals.

### 4.4 `project.toml`

```toml
schema = 1

[trunk]
branch = "main"

[[repos]]
name = "app"
path = "."
push_after = []

[[tests.tier]]
name = "unit"
command = "uv run pytest -q tests/unit"
required_for = ["handover", "push"]
measured_seconds = 212

[[tests.tier]]
name = "e2e"
command = "uv run pytest -q tests/e2e"
required_for = ["push"]
measured_seconds = 540

[ci]
provider = "github"                  # or "command" with gate_command, or "none"
required_jobs = ["lint", "test-py", "test-ts", "e2e"]
workflow_fingerprint = "sha256:…"

[pr]
opened_by = "sender"                 # or "author"
merge_method = "squash"              # detected

[release]
version_files = ["pyproject.toml", "package.json"]
tag = "v{version}"                   # annotated

[fleet]
default = { main = 1, review = 1 }

[guards]
revert = true
line_edit = true
push_receipt = true

[evidence]
close = { field = "ref", pattern = "^[A-Z]+-\\d+$", required = false }

[judge]
required = false

[deploy]
revision_command = ""                # prints the deployed revision; used by the judge
```

### 4.5 Profile rules

1. **Posts are template copies with a template version.** On plugin update `onboard --check` shows the diff and
   asks; it never overwrites a hand-edited post.
2. **A profile knows when it is stale.** The CI workflow fingerprint is stored; a job absent from `required_jobs`
   closes the push door by name.
3. **Machine facts never go into the project.** Two people on one repository share `project.toml` and have
   different `machine.toml`.

### 4.6 CI, and the lack of it

- **Required jobs:** jobs triggered by push or PR to trunk are proposed; schedule-only, manual-only and jobs with
  an `if:` onboarding cannot evaluate are left out **and named**; matrices expand to per-variant names. The sender
  checks every required job by name, never the run's overall conclusion alone.
- **Three cases:**

| project has | `shipped` stands on | the row records |
|---|---|---|
| origin + CI (GitHub or `gate_command`) | green required jobs over the shipped commit | `gate: github run <id>` |
| origin, no CI | the local push receipt only | `gate: local receipt over <sha>` |
| no origin | `shipped` does not exist; the path ends `landed → closed` | `no remote` |

- Absent CI never reads as green; `brief` says "no CI, verified locally over <sha>" out loud.
- `gate_command <sha>` exits 0 green, 1 red, 2 pending. No command and no GitHub → the sender asks the human.

---

## 5. Pull requests

The PR is the default and recommended path.

- **`shipped` in PR mode asks the PR's state** (`gh pr view --json state,mergeCommit`), never ancestry: squash and
  rebase merges never make the branch tip an ancestor of trunk. Ancestry is used for direct push only.
- **The sender opens PRs at batch time** (default): every outward write stays with one session, one human "yes" per
  batch. Option `pr.opened_by = "author"`: the handover pushes the branch and opens a draft PR so CI runs in parallel
  with review.
- **v1 merges one PR at a time:** checks green → branch up to date → merge → update the next → wait. A bisecting
  merge queue is on the roadmap.
- **Review lives in the ledger;** the PR is transport. Posting the verdict as a GitHub review from the human's
  account is an outward action, off by default.

---

## 6. The work ledger

### 6.1 Walk-through

```
flotilla tree cut feat/export --ref LIN-123         → claimed   (worktree cut, claim filed by the cut)
flotilla work hand feat/export                      → handed    (tip 3f9a2c1; handover tiers green over it)
  orchestrator: flotilla work assign feat/export --reader "review session 1"
flotilla work fix feat/export --why "no test for an empty file"   → fixing
flotilla work hand feat/export                      → handed    (tip 7d01e44, back to the same reader)
flotilla work accept feat/export --reviewed 7d01e44 → accepted  (reader ≠ author; revision = handed tip)
  sender: flotilla work queue feat/export           → queued    (PR #42 opened)
  (nobody types "shipped": a hook or `watch` asks the PR) → shipped (gate: github run 81234)
  judge:  flotilla work walked feat/export --build <sha> --steps "…"   → walked
flotilla work close feat/export                     → closed    (ref LIN-123 checked against the profile)
```

### 6.2 States

```
reserved ─→ claimed ─→ handed ⇄ fixing
 (post held)              │
                          ↓
                      accepted ─→ queued ─→ [landed] ─→ shipped ─→ [walked] ─→ closed
                                               │           │           │
                                 direct push only   absent without   judge only;
                                                    origin           `broke` → linked fix row
from any state before shipped:  → released (abandoned, with a reason)
outside the ledger:              offledger (reached trunk without the ledger; merge commit + witness)
```

Legal transitions are **computed from the profile**:

| profile | effect |
|---|---|
| review "none" | `claimed → queued` |
| review "main only" | minor posts skip `handed/accepted` |
| PR mode | `queued → shipped` (no `landed`) |
| direct push | `queued → landed → shipped` |
| no origin | `landed → closed` |
| `judge.required` | `closed` requires `walked` |

A refusal names the cause and what is legal from here: "`shipped` is unavailable: the project has no origin.
Legal from here: `closed`, `released`."

### 6.3 Evidence per move

| move | by (post `may`) | evidence |
|---|---|---|
| `claimed` | the worktree cut, itself | base from `origin/<trunk>` |
| `handed` | author | tip = branch HEAD; **`handover` tiers green over that tip** (receipt) |
| `fixing` | reader | `--why`, stored in the row |
| `accepted` | reader | `--reviewed` = handed tip; **reader ≠ owner** |
| `queued` | sender | PR number (PR mode) |
| `landed` | sender | merge commit (direct push) |
| `shipped` | **nobody by hand** | asked of the PR or origin; records **which gate** |
| `walked` | judge | build revision (= deployed, compared with shipped), steps, observation |
| `broke` | judge | `--where "<place in the product>"`; refused without it; creates a linked fix row |
| `closed` | author | `[evidence]` fields from the profile |
| `released` | owner or orchestrator | reason |
| `offledger` | sender | merge commit + a named witness |
| `wait` | holder of the move | `--on <whom>` `--why` — the ball visibly lies elsewhere |

### 6.4 Dependencies

`flotilla tree cut feat/export-ui --requires feat/export`. Default: work may start at once (stacking is allowed),
but the dependent row cannot be `queued` until its dependency is `shipped`. The roster shows "blocked on
feat/export"; the owner is told when it unblocks.

### 6.5 Duplicates

A second claim with the same `--ref` while a row is open is **refused**, naming the holder. `--also "<why>"`
overrides and is recorded. (A warning-only door let three ai-os sessions write the same work within ~14 hours.)

### 6.6 Storage

One **append-only JSONL move log** per repository under a file lock; the current state is a fold of the log.
Consequences: metrics, "what did I accept last week" and history per row come for free; a torn last line is
detected and discarded alone.

### 6.7 Views

`roster` (idle · working · waiting on whom · blocked on what · orphaned), `brief` (a batch for the human's "yes"
in one block: every branch, whose, accepted by whom over which revision, which gate), `stalled N`, `show <branch>`,
`metrics` (time in state, return rate, reviewer throughput, event failures). `adopt` hands orphaned work to a live
session, only when the previous owner is absent from the census.

### 6.8 Events

`.flotilla/events/pre-<move>` and `post-<move>`. Input: JSON on stdin, `event_schema = 1`, generated from code and
documented. `pre-`: exit 0 allows, exit 2 rejects with the script's text. **A crash or timeout refuses the move and
names the script.** Such refusals must be rare:

- `flotilla events check` (executable bit, shebang interpreter exists, a run on sample input) at onboarding and on
  `SessionStart`, so a broken script is visible before a real move hits it;
- the refusal prints the tail and a reproduce command (`flotilla events run pre-accepted --row <branch>`);
- `pre-` runs before the write, so a fixed script simply retries;
- emergency `--skip-event <name> --why` on a human's decision, recorded;
- event failures are counted in `metrics`.

`post-` events cannot undo anything; their failures are printed.

### 6.9 Whose move — the broken chain of responsibility

Case: the reader's test run finishes; the reader stays silent; the author and the sender believe the run is still
going; work stands. One computation — **whose move is it, per row** — read by three layers:

1. **The run is a ledger fact.** `flotilla lane run --for <branch> -- <cmd>` ties start, end, exit code, revision
   and summary line to the row. The roster says "reader's run finished green 12 min ago; waiting on the verdict".
2. **Stop guard — do not fall silent holding the ball.** On `Stop`: if this session holds a move, has no
   `background_tasks` that will wake it (Stop hook input) and has recorded no `wait` → block, printing the legal
   next moves. `stop_hook_active` → never block twice; record the break for layer 3.
3. **Dropped-ball watcher.** Ledger says "your move" + `claude agents --json` says `idle` + no `wait` → delivered by
   hook to the orchestrator, who messages the holder. Holder absent from the census → orphaned → `adopt`.

---

## 7. Spawn and posts

### 7.1 Where a session lives (scheme A, verified by probe)

```
~/work/app/                  main checkout — belongs to nobody
                               edits from a background session → refused by Claude Code itself
                               reads (git -C …, origin/main) → allowed
~/work/app-review-1/         branch fleet/review-1   home of "review session 1"
~/work/app-main-2/           branch feat/export      a task tree of "main session 2"
~/work/app-sender-1/         branch fleet/sender-1   the sender's intake tree (merges, version, tag)
```

flotilla cuts its own linked worktrees (sibling directories), also in other repositories a task touches. Claude
Code's background-isolation guard stays on and enforces "the main checkout belongs to nobody" for free: it accepts
edits inside any linked worktree, including ones made with `git worktree add` (section 13).

### 7.2 Spawn

```
flotilla spawn -r 2 -M 1 --dry-run
flotilla spawn --default
```

```
cd <main checkout> && claude --bg \
  -n "<name>" \
  --add-dir <tree> \
  --permission-mode <questionnaire Q4> \
  --append-system-prompt "<post body + name + post + tree + absolute path of the flotilla CLI>" \
  "Invoke the flotilla skill: census, announce, wait for a task."
```

- names come from the issued-numbers journal, never "highest live + 1";
- the post sets the name pattern;
- acceptors (orchestrator, sender, readers, judge) start before producers;
- "address not read" ≠ "did not start": the census is asked before failure is declared, or a second process with
  the same name follows;
- `--dry-run` shows names, trees and branches and changes nothing.

### 7.3 A post is an agent definition

```markdown
---
name: reviewer
description: Independent reader of one handed-over branch
model: inherit
name_pattern: "review session {n}"
may: [accept, fix, wait]
writes_one_copy: false
---
You are the independent reader, and that is the entire product of your post. …
```

The ledger checks **who** makes a move. The caller is identified by walking up from its own process to a pid that
`claude agents --json` lists (verified by probe; the session process `comm` is the version number, so match by pid,
never by program name).

| post | does | `may` | default count |
|---|---|---|---|
| orchestrator | routes readers, answers what is where, wakes dropped balls; writes nothing | assign, wait | 1 when the fleet exceeds three sessions |
| sender | PRs, merges, version, tag, gate; one "yes" per batch | queue, land, ship-check, release | 1 when the fleet exceeds three sessions; **never more than one** |
| reviewer | reads a handed branch, runs tiers, verdict as a move | accept, fix, wait | 1 |
| acceptance judge | walks the human path on the live build | walked, broke, wait | 0; offered when a deployment is found |
| main | large work | claim, hand, close, wait | 1 |
| minor | small work, same handover form | claim, hand, close, wait | 0–1 |

Custom posts are files in `.flotilla/posts/`.

### 7.4 The acceptance judge

The only post asking "does it reach a human". Before walking it compares `deploy.revision_command` with the shipped
revision; a verdict names the build it walked; a walk spanning a restart measured two builds and is not recorded.
It must not read the code before the attempt (otherwise it completes a missing button with imagination, as the
author did); whether path-scoped deny rules (`Read(src/**)`) hold for a spawned background session is an open
question. Tools follow the surface: browser (Playwright or Chrome MCP), terminal, `curl`.

### 7.5 Life of the fleet

| action | how |
|---|---|
| add a post mid-day | `flotilla spawn -r 1` |
| revive a crashed session | native `claude respawn`; flotilla checks the name and tree are kept |
| retire a session | `flotilla retire <name>`: waits for the tree lock to clear, frees the post row, leaves unfinished work orphaned for `adopt` |

---

## 8. Watchers and hooks

**Principle:** a check is computed from the ledger and git; it is delivered by a hook into the session.

| event | says | to |
|---|---|---|
| `SessionStart` | your name and post, live peers, what you inherited broken; `doctor` and `events check` quietly | every session |
| `UserPromptSubmit` (throttled, no thresholds, age printed) | what waits for your move | every session |
| same | fleet-wide deviations: branch without a reader, dropped balls, orphaned work | orchestrator only |
| `PreToolUse` Bash | guards (section 10), one process | every session |
| `Stop` | the ball guard (6.9) | every session |

All hooks exit silently when the project has no `.flotilla/`. flotilla never writes the user's `settings.json`.

**Git hooks** (`pre-commit` file reservation, `pre-push` second push barrier) are repository hooks; onboarding
installs them per clone with consent, each named.

**Cron: none in v1.** `flotilla watch --once` exits with a meaningful code for any scheduler. Onboarding-installed
schedules (crontab on Linux, launchd on macOS, with consent) are the first roadmap item.

---

## 9. The lane

```
flotilla lane run --for feat/export -- uv run pytest
flotilla lane take --wait --note "measuring a race"
flotilla lane
flotilla lane sweep
```

- **Not a lock**, and documented as such: nothing stops a peer from running a suite without asking. The lane makes
  "is the machine busy" survive the session that answered.
- Four questions, and a refusal names which one is in the way:

| question | answered by |
|---|---|
| does a peer hold the lane | the booking log |
| does a foreign run exist | `pgrep` |
| is it computing | two CPU-time samples (procfs or `ps -o time=`, per machine capability) |
| has CI on this machine taken it | `gh run list`, only when the profile says CI is self-hosted here |

- `lane run` takes the command's own exit code (never a pipeline's), tells a signal kill (137 / -9) from a failure
  and records "killed — no verdict", and writes the summary line ("212 passed") into the row.
- No clock expiry. `sweep` removes only rows with no process behind them.
- Capacity (how many long runs fit) comes from `machine.toml`, measured at onboarding.

---

## 10. Guards

One `flotilla guard` process per Bash call runs every enabled check.

| guard | refuses | escape |
|---|---|---|
| revert | `git checkout -- <f>`, `reset --hard`, `clean -f`, `restore` when they would destroy uncommitted work; names the fix (`git add`, then retry) | none needed |
| line-number edit | `sed -i` with numeric addresses — a line number goes stale silently and lands on the neighbour | address by text |
| push receipt | `git push`, `gh pr create`, `gh pr merge`, `gh workflow run` without a green `push`-tier receipt over the revisions being pushed | `FLOTILLA_GATE_OVERRIDE="<why>"`, recorded |

Also: the `Stop` ball guard (6.9); git `pre-commit` file reservation (a rewrite of a shared file under another open
row's reservation is refused; appends always pass); git `pre-push` second barrier (catches pushes hidden in scripts).

- **A guard's own failure is decided by reversibility:** revert and line-edit allow with a warning (a false refusal
  costs more than the rare miss); push receipt refuses (shipping cannot be taken back). Claude Code treats a
  timed-out hook as "allow", so guards must stay fast: budget ~200 ms per call, tested.
- **Every guard documents its ceiling** — what it cannot see (e.g. commands inside `eval`), in the docs and `--help`.
- A receipt is valid only over the exact revision; a moved HEAD, a changed tier command or a changed CI workflow
  fingerprint voids it.

---

## 11. Verification of flotilla itself

| layer | how | when |
|---|---|---|
| code | pytest (ported suites) | every commit, CI |
| platforms | CI matrix Ubuntu + macOS × Python 3.11 / 3.12 / 3.13 | every commit — macOS is supported while its runner is green |
| seams | `event_schema` golden generated from code, read by docs and example scripts; `claude agents --json` parsing tested on **recorded** outputs of several Claude Code versions | every commit |
| guards | each seen red by an injected regression of two shapes: removal (is it alive) and a plausible neighbour (is it precise) | when the guard is written |
| plugin | `claude plugin validate` | every commit |
| language | no Cyrillic (U+0400–U+04FF) anywhere in the repository | every commit |
| skill text | `claude plugin eval` against a no-plugin baseline (e.g. "you are the reviewer, a branch was handed to you" → the transcript contains `flotilla work accept … --reviewed <sha>`); a triggering eval whose negatives are near misses (subagent and worktree skills) | before release (billed) |
| the whole fleet | a manual e2e smoke modelled on the 2026-09-22 probe: throwaway repo, two background sessions (main + reviewer), handover, acceptance, merge; ledger states and disk checked | before release (billed) |

`flotilla doctor`: Claude Code version floor, `claude agents --json` alive, profiles valid, event scripts sound,
guards installed.

---

## 12. Language

English everywhere in this repository: identifiers, comments, docstrings, CLI output, post templates, skills,
README, specs, examples, guard messages and commit messages. A CI check refuses Cyrillic.

---

## 13. Evidence: live probe, 2026-09-22

Claude Code 2.1.280, Linux, throwaway repository, two background sessions in `auto` mode, removed afterwards.

**Scheme A** — flotilla-cut sibling worktree, `--add-dir`, session launched from the main checkout:

- the edit landed in our tree; the session did **not** move into `.claude/worktrees/`;
- an edit into the main checkout was **refused**: "This background session hasn't isolated its changes yet. Call
  EnterWorktree first … (a path inside a linked git worktree, including one you create with `git worktree add`,
  is accepted). (To disable this guard for this repo, set `"worktree": {"bgIsolation": "none"}` …)";
- `git -C <main checkout>` reads and compound git commands **worked**.

**Scheme B** — native `--worktree probe-b`:

- tree at `.claude/worktrees/probe-b` inside the repository (the main checkout gains an untracked `.claude/`),
  branch `worktree-probe-b`, locked;
- `git -C <main checkout>` **refused**: "a worktree-isolated session's git operations must target its own worktree";
- a compound command naming git **refused**: "names git in a form too complex to verify".

**Identity:** walking up from the Bash `$$`, the first parent is the pid `claude agents --json` lists for the
session; its `comm` is `2.1.280`.

**Side notes:** probe A ended `state: blocked, status: idle` after the refused edit (not investigated);
`claude rm` right after `claude stop` was refused while the native tree was still locked, a retry succeeded.

**Conclusion:** scheme A; B is unfit for a fleet (no trunk reads, fixed branch naming, one tree in one repo).

---

## 14. Porting from ai-os

- Code is **ported, not rewritten**: its conditions encode real failures, and its tests move with it.
- Out of v1: `session_inbox` and `fleet-probe` (they parse transcripts). Their roles are covered by the ledger
  (state), metrics (cost) and hooks (delivery).
- Each module is ported in **two separate steps**: (1) move as is, tests green, behaviour unchanged; (2) translate
  output and its asserts **in one commit**, reviewed for meaning, not only by a green run.
- Rationale docstrings are **rewritten** for a general reader; ai-os measured precedents move to
  `skills/flotilla/references/why.md`.
- Measured on ai-os `main`: the 15 code files to port carry 6,572 Cyrillic lines of 12,412 (including ~540 lines of
  Cyrillic identifiers by a rough heuristic); their 12 test files 9,058 of 18,678, with ~1,220 asserts comparing
  Russian output. Translation is a task per module, roughly the size of the port.

---

## 15. Build order

Released together; built as sub-projects, each with its own plan:

1. **Foundation** — plugin skeleton, `core` (platform, storage, config, census, identity), activation rule,
   `doctor`, CI (matrix, validate, no-Cyrillic).
2. **Onboarding** — machine measurement, project detection, questionnaire, first run, profile writing.
3. **Ledger** — moves, evidence, profile-computed transitions, dependencies, events, views, metrics.
4. **Spawn and posts** — names, worktrees, launch, templates, `may`, retire/adopt.
5. **Lane** — bookings, runs, receipts.
6. **Guards and watchers** — command guards, Stop guard, git hooks, session hooks, `watch`.

Then: skill text and evals, the e2e smoke, README with three working example prompts (directory policy).

## 16. Publication criterion and roadmap

**Before submitting:** ai-os runs on flotilla for several real shifts — ledger imported, judge and triage holder as
custom posts, register closing and the board as events.

**Roadmap:** OS schedules installed by onboarding (crontab / launchd); a machine resource broker (ports and database
names per worktree); a bisecting merge queue; a shared ledger across machines (behind the storage interface).

## 17. Open questions (resolved in the foundation spec unless noted)

1. What happens to `${CLAUDE_PLUGIN_DATA}` on plugin **uninstall** — the ledger must not vanish silently.
2. Exact Claude Code version floor.
3. Which `--permission-mode` values a `--bg` session honours, and how a stalled permission prompt is surfaced.
4. Whether path-scoped deny rules hold for spawned background sessions (judge, section 7.4).
5. Why probe A ended `state: blocked`.
6. License (MIT or Apache-2.0) and GitHub home; name availability on GitHub/PyPI (absent from the official
   marketplace catalogue, local copy).
7. Read Gas Town and multiclaude before the posts sub-project.
