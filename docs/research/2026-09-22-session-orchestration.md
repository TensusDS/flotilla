# Orchestrating concurrent Claude Code sessions — state of practice (researched 2026-09-22)

Legend: **[R]** read directly in the cited source · **[I]** my inference from what was read · **[V]** volatile fact
(version, date, star count — re-check before relying on it). Local context: this machine runs Claude Code
**2.1.280** [R, `claude --version`, 2026-09-22]. Docs were read on 2026-09-22; code.claude.com pages change per release.

---

## 1. What Claude Code provides natively

### 1.1 The official map: five ways to run work in parallel
`/docs/en/agents` ("Run agents in parallel") lists five approaches — **subagents, agent view (background
sessions), agent teams, dynamic workflows, projects (cloud threads)** — plus three supporting tools: **worktrees,
cross-session messaging, `/batch`** [R] https://code.claude.com/docs/en/agents. Its decision rule [R]:
"Who coordinates the work?" (Claude in one conversation → subagents; you → agent view; a lead Claude → agent teams;
a script → workflows); "Do the workers need to talk?" (cross-session messaging between sessions *you* run);
"Do the tasks touch the same files? Isolate the work with worktrees … **Agent teams don't isolate teammates in
worktrees**, so partition the work".

### 1.2 Background sessions / agent view (`claude --bg`, `attach`, `logs`, `agents --json`)
Source: https://code.claude.com/docs/en/agent-view
- Commands [R]: `claude --bg "<prompt>"`, `claude --bg --name <n>`, `/bg`, `claude agents` (TUI),
  `claude attach <id>`, `claude logs <id>`, `claude stop|kill|respawn|rm <id>`, `claude daemon status|stop --any`.
- A **supervisor daemon** hosts background sessions as separate processes; state in `~/.claude/daemon/roster.json`
  and `~/.claude/jobs/<id>/state.json` [R].
- **Isolation is automatic** [R]: "Before editing files, Claude moves the session into an isolated git worktree
  under `.claude/worktrees/`, so parallel sessions can read the same checkout but each writes to its own."
- **Scriptable interface** [R]: `claude agents --json` (fields `cwd, kind, startedAt, id, state
  (working|blocked|done|failed|stopped), pid, status (busy|waiting|idle), waitingFor, sessionId, name`).
  Docs say: "To read session state from a script or another program, use `claude agents --json` rather than the
  files under `~/.claude/jobs/`" and "**The files under `~/.claude/jobs/<id>/` are not a stable interface.**"
- Status [R]: "Agent view is in **research preview**. The interface and keyboard shortcuts may change." [V]
- Limits [R]: machine shutdown stops sessions (shown failed within 48 h, stopped after); `CLAUDE_CONFIG_DIR`
  gives a separate supervisor instance.

### 1.3 Session naming (`-n` / `--name`, `/rename`)
Source: https://code.claude.com/docs/en/sessions
- [R] `claude -n auth-refactor`; `/rename`. Since **v2.1.232** a name collision with another *live* session gets a
  variant suffix (`auth-refactor-graceful-unicorn`) — **but not** for `--bg` or `-p` sessions at startup, not for
  generated titles, not against older versions [R]. So **two live sessions can still share a name** [R].
- Unnamed interactive sessions get a default display name (`my-app-3f`) that is **not a resume handle** [R].
- Cross-session messaging addresses by name; on ambiguity Claude "adds a short identifier to each row" [R]
  (https://code.claude.com/docs/en/cross-session-messaging).

### 1.4 Inter-session messaging (`ListAgents` / `SendMessage`)
Source: https://code.claude.com/docs/en/cross-session-messaging ; tool rows: https://code.claude.com/docs/en/tools-reference
- Requires **v2.1.224+** (macOS/Linux/WSL2), v2.1.234+ native Windows; on by default [R][V].
- Same machine: per-session **Unix domain socket** (never via Anthropic servers); path exposed as
  `CLAUDE_CODE_MESSAGING_SOCKET`, plus `CLAUDE_CODE_MESSAGING_TOKEN` so a hook/script can post into its own
  session [R]. "Each session registers itself in files on disk" [R] — the file format is not specified [I].
- Delivery [R]: read **between tool calls** during a turn; an idle session gets a new turn. Plain text only;
  the receiver is told it came from another session and it **cannot approve prompts or change config** [R].
- Inbound control `crossSessionInbound = accept|hold|refuse`; default depends on permission-mode class
  (a bypass-permissions receiver **holds** messages for approval; hold dialog expires after `dialogExpiry`,
  default 5 min, message then **dropped**) [R].
- **Loss/refusal paths, documented** [R]: size cap (~1M chars), burst refusal at sender, per-sender rate limit,
  duplicate-drop within a short window, receiving queue capped at **50** accepted messages, held-message store
  capped at **100** (drops oldest). "Before v2.1.236, Claude Code reported those sends as sent while the receiving
  session dropped them." `notify_when_idle` (v2.1.236+) gives a one-shot idle/exit notice, expires after 12 h [R].
- Not reachable across container boundary or WSL2↔Windows; `--bare` sessions bind no inbox [R].

### 1.5 Agent teams (lead + teammates)
Source: https://code.claude.com/docs/en/agent-teams
- **Experimental, disabled by default** (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) [R][V]. Interactive only —
  not spawned under `-p`/Agent SDK [R].
- Architecture [R]: lead + teammates + **shared task list** (`~/.claude/tasks/{team}/`, states pending /
  in progress / completed, dependencies; "Task claiming uses **file locking** to prevent race conditions") +
  **mailbox** (`~/.claude/teams/{team}/inboxes/{agent}.json`). Hooks `TeammateIdle`, `TaskCreated`,
  `TaskCompleted` (exit 2 = block with feedback) serve as quality gates [R].
- Role reuse [R]: a teammate can be spawned from a subagent definition (`security-reviewer`, `test-runner`).
- Documented limitations [R]: no resumption of in-process teammates; "**Task status can lag**: teammates
  sometimes fail to mark tasks as completed, which blocks dependent tasks"; one team per session; no nested
  teams; lead fixed for life; teammates **not** worktree-isolated ("Two teammates editing the same file leads to
  overwrites"); plan approvals auto-granted by the lead; token cost scales linearly; "Start with 3-5 teammates".
- Side effect [R]: with teams enabled, any *named* subagent launches as a teammate, so teams "can form even
  when you didn't ask for one".

### 1.6 Subagents and worktree isolation (`isolation: worktree`, `EnterWorktree`)
Source: https://code.claude.com/docs/en/worktrees
- `claude --worktree <name>` → `.claude/worktrees/<name>/`, branch `worktree-<name>`; base = remote default
  branch (`worktree.baseRef: "fresh"`) or local HEAD (`"head"`) [R]. `.worktreeinclude` copies gitignored files
  (`.env`) [R]. `EnterWorktree`/`ExitWorktree` tools; `isolation: worktree` in subagent frontmatter [R].
- **Enforced isolation** [R]: blocks Edit/Write into the main checkout, Bash whose cwd is the main checkout,
  git redirects (`git -C`, `GIT_DIR`, `cd` then git) and commands whose git target cannot be verified.
- Held `git worktree lock` while an agent runs; periodic sweep removes Claude-created worktrees older than
  `cleanupPeriodDays` unless they hold work; worktrees you created by hand are never swept [R].
- **Shared, not isolated** [R]: the `.git` directory, project plugins, permission approvals (saved to the main
  checkout), untracked `.claude/skills|agents|commands`. Ports, databases, caches are not mentioned — the
  isolation is for **files and git**, not for runtime resources [I].
- `-p` worktrees are not auto-cleaned [R].

### 1.7 Headless (`claude -p`) and dynamic workflows (`Workflow` tool)
- `claude -p` with `--output-format json|stream-json` is the documented scripting surface; `claude -p --resume
  <id>` to query a session; fan-out loop over `claude -p` with `--allowedTools` [R]
  https://code.claude.com/docs/en/best-practices. `-p` sessions bind an inbox socket and can receive messages [R].
- Dynamic workflows [R] https://code.claude.com/docs/en/workflows: a JavaScript script (`agent()`, `pipeline()`,
  `parallel()`, `phase()`) run by a runtime in the background; **up to 16 concurrent agents** by default
  (`CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS`, 1–256, v2.1.269+), **1,000 agents per run**, 4,096 items per
  `parallel()`/`pipeline()`, no mid-run user input, **resumable only within the same session**, deterministic
  replay (`Date.now()`/`Math.random()` throw). Saved to `.claude/workflows/`. Not a peer-session coordinator:
  the script holds the plan, workers are subagents [R/I].
- `/batch` [R]: splits one change into 5–30 worktree-isolated subagents, each opening a PR.

### 1.8 Which on-disk formats are a public interface?
| Artifact | Documented status | Source |
|---|---|---|
| Transcript JSONL `~/.claude/projects/<p>/<id>.jsonl` | **Explicitly internal**: "The entry format is internal to Claude Code and changes between versions, so scripts that parse these files directly can break on any release. … use `/export` or the script interfaces instead." [R] | https://code.claude.com/docs/en/sessions |
| `~/.claude/sessions/` | Described only as "one small file per running session, used to detect concurrent sessions and crashes"; removed on exit. **No format, no stability promise** [R] → treat as internal [I] | https://code.claude.com/docs/en/claude-directory |
| `~/.claude/jobs/<id>/` | "not a stable interface" [R] | agent-view |
| `~/.claude/teams/.../config.json` | "don't edit it by hand or pre-author it" [R]; mailbox path documented, format not [R] | agent-teams |
| **Supported surfaces** | `claude agents --json`, `claude -p --output-format json/stream-json`, hook input `transcript_path`, `/export`, Agent SDK, `CLAUDE_CODE_MESSAGING_SOCKET` for posting [R] | sessions, agent-view, cross-session-messaging |
Note: `claude-directory` lists `jobs/`, `daemon/` under "Keep these state files" — that is a *don't-delete*
advice, not a stability promise [R/I].

---

## 2. Anthropic's own guidance on multi-agent / parallel work
- **Best practices** [R] https://code.claude.com/docs/en/best-practices: worktrees + messaging + agent view as
  the parallel menu; **Writer/Reviewer** pattern ("A fresh context improves code review since Claude won't be
  biased toward code it just wrote"); test-writer / implementer split; "Add an adversarial review step" with a
  warning that a gap-seeking reviewer "will usually report some, even when the work is sound".
- **"Building a C compiler with a team of parallel Claudes"** (Nicholas Carlini, 2026-02-05) [R]
  https://www.anthropic.com/engineering/building-c-compiler: 16 agents, one Docker container each, a bare
  upstream repo; **task locks = text files in `current_tasks/`**, git push races decide ownership;
  "Merge conflicts are frequent, but Claude is smart enough to figure that out"; role agents (dedup, performance,
  code-quality critic, docs); `--fast` 1%/10% random test sample to protect context; observed failure: "each agent
  would hit the same bug, fix that bug, and then overwrite each other's changes"; "**I don't use an orchestration
  agent**". ~2,000 sessions, ~$20k [R via InfoQ summary, https://www.infoq.com/news/2026/02/claude-built-c-compiler/] [V].
- **"Harness design for long-running application development"** (2026-03-24) [R]
  https://anthropic.com/engineering/harness-design-long-running-apps: planner / generator / evaluator; agents
  communicate **through files** and negotiate a "sprint contract" of what "done" means; separate evaluator because
  self-evaluating agents "respond by confidently praising the work". Solo run $9/20 min vs harness $200/6 h [V].
- **"Effective harnesses for long-running agents"** (2025-11-26): initializer agent writes feature list +
  progress file + git; coding agent advances incrementally [I from search summaries; not re-read directly]
  https://anthropic.com/engineering/effective-harnesses-for-long-running-agents.
- **"How we built our multi-agent research system"** (2025-06-13) [R]
  https://www.anthropic.com/engineering/multi-agent-research-system: "multi-agent systems use about 15× more
  tokens than chats"; "most coding tasks involve fewer truly parallelizable tasks than research".

Common thread [I]: Anthropic's own large runs coordinate through **files in git + CI**, not chat; they add a
separate verifier role; they bound test output; and the in-product features (teams, messaging) are young
(experimental / research preview, versions 2.1.2xx).

---

## 3. Community tools
Metadata via GitHub API on 2026-09-22 [R][V]; descriptions from each README [R] unless marked.

| Tool | Isolation | Roles? | State / queue | Review / merge step | License | Stars [V] | Last push [V] |
|---|---|---|---|---|---|---|---|
| **claude-squad** (smtg-ai) | git worktree + tmux per instance | no | local instance store; no queue | "Review changes before applying them, checkout changes before pushing" | AGPL-3.0 | 8.5k | 2026-08-20 |
| **Conductor** (conductor.build) | git worktree per workspace; `setup` script; `$CONDUCTOR_PORT` per workspace | no | app-managed; PR state linked | diff viewer, checks, PR create, merge, archive | closed source, Mac app [I: no repo found] | — | — |
| **Crystal → Nimbalyst** (stravu) | git worktrees | no | desktop app | compare approaches | MIT | 3.1k | 2026-02-26; **deprecated Feb 2026** |
| **container-use** (dagger) | **container + git branch per agent**, MCP server | no | branch = state; command log | `git checkout <branch>` to review | Apache-2.0 | 4.0k | 2026-09-21; "experimental" |
| **uzi** (devflowinc) | worktree + tmux; **dev-server port management** | no | tmux sessions | "checkpoint and merge" | MIT | 0.6k | 2025-06-04 (stale) |
| **ccmanager** (kbwo) | worktrees, devcontainer option | no | per-tool **state detection** (busy/waiting/idle), status-change hooks; can **copy Claude session data between worktrees** | merge/delete worktree in-app | MIT | 1.2k | 2026-09-13 |
| **vibe-kanban** (BloopAI) | workspace = branch + terminal + dev server | no (kanban assignee) | **kanban issues** as task queue | inline diff comments to agent, PR, merge | Apache-2.0 | 28k | 2026-09-19; **"sunsetting"** |
| **multiclaude** (dlorenc) | worktree + tmux window per agent | **supervisor, merge-queue, PR-shepherd, worker, reviewer, workspace** (markdown-defined) | daemon + tmux; "CI is the ratchet" | merge-queue auto-merges on green CI (single player) / PR-shepherd waits for humans | none declared | 0.6k | 2026-01-28 |
| **Gas Town** (gastownhall, Steve Yegge) | git worktrees ("hooks") per worker; Docker option | **Mayor (coordinator), polecats (workers), Witness (per-rig stuck-agent monitor), Deacon (supervisor patrol), Refinery (merge queue), crew (human)** | **Beads** git/Dolt-backed issue ledger; convoys; escalation by severity; `scheduler.max_polecats` concurrency governor | Refinery: "Bors-style bisecting queue", verification gates | MIT | 18k | 2026-09-18 |
| **Sculptor** (imbue-ai) | "workspace (an isolated copy of your code)" | skills (spec/mocks/fix-bug) not roles | desktop app | review then merge to main | MIT | 0.2k | 2026-09-22; "research preview" |
| **agent-deck** (asheshgoplani) | TUI session manager | no | — | — | MIT | 0.9k | 2026-09-21 |
| **ruflo** (ex claude-flow, ruvnet) | swarm framework | swarm "agents" | own memory/RAG | — | MIT | 73k (star count unusual; not assessed) | 2026-09-22 |

Sources: https://github.com/smtg-ai/claude-squad · https://www.conductor.build/docs/concepts/git-worktrees ·
https://www.conductor.build/docs/reference/scripts/setup · https://github.com/stravu/crystal ·
https://github.com/dagger/container-use · https://github.com/devflowinc/uzi · https://github.com/kbwo/ccmanager ·
https://github.com/BloopAI/vibe-kanban · https://github.com/dlorenc/multiclaude · https://github.com/gastownhall/gastown ·
https://github.com/imbue-ai/sculptor · https://github.com/asheshgoplani/agent-deck · https://github.com/ruvnet/ruflo

Observations [I]:
- Two families. **Session managers** (claude-squad, ccmanager, agent-deck, uzi, Conductor, Crystal) solve
  *isolation + visibility*: worktree per task, TUI/desktop status, a diff to review. They hold **no task queue and
  no roles** — the human is the orchestrator and the merger. **Orchestrators** (Gas Town, multiclaude,
  vibe-kanban) add a work ledger and, in Gas Town/multiclaude, explicit **coordinator / worker / monitor /
  merge-queue / reviewer** roles.
- Every role-based system puts the **merge in one dedicated role gated by CI** (Refinery, merge-queue), and adds
  a **liveness monitor** (Witness, supervisor "nudges stuck agents") — i.e. they treat "stuck" and "who merges"
  as first-class, not as etiquette.
- Work state lives **outside agent memory and outside chat**: Beads ledger in git/Dolt (Gas Town), GitHub PRs +
  CI (multiclaude), kanban DB (vibe-kanban), lock files in git (Anthropic compiler). Gas Town's README frames the
  problem exactly: "Work state lost in agent memory → Work state stored in Beads ledger" [R].
- State detection in session managers is **screen-scraping of the TUI** ("configurable state detection
  strategies", ccmanager) [R/I] — now partly superseded by the official `claude agents --json` [I].
- Churn is high: Crystal deprecated (Feb 2026), vibe-kanban sunsetting, uzi idle since mid-2025 [R][V].

---

## 4. Recurring failure modes and mitigations

| Failure | Evidence | Mitigations seen |
|---|---|---|
| **Duplicate work / same bug fixed N times** | Carlini: agents "hit the same bug, fix that bug, and then overwrite each other's changes" [R]; multiclaude accepts it: "They might duplicate effort … *This is fine*" [R] | claim-before-work lock (`current_tasks/` files in git [R]; team task list with file locking [R]); split monolithic tasks; or accept redundancy and let CI arbitrate (multiclaude) |
| **File overwrites / merge conflicts** | agent-teams: "Two teammates editing the same file leads to overwrites" [R]; "Merge conflicts are frequent" [R, compiler] | worktree per agent (native, enforced); partition files per owner; single merge role with CI gate (Refinery, merge-queue) |
| **Shared runtime resources** (ports, DBs, dev servers, CPU/RAM for test runs) | worktree docs list only git/plugins/approvals as shared [R]; tools add `$CONDUCTOR_PORT` [R], uzi "port management" [R], container-use full containers [R]; workflows cap 16 concurrent agents "Bounds local resource use" [R] | per-workspace ports, containers, concurrency caps (Gas Town `max_polecats` [R]), sampled test runs (`--fast`) [R] |
| **Config corruption from concurrent processes** | many issues on `~/.claude.json` corrupted by concurrent sessions, e.g. #40226 (closed 2026-06-11), #28922 "reported 8 times since June 2025" [R, GitHub API] https://github.com/anthropics/claude-code/issues/40226 | upgrade; atomic write fix proposed in issues [R]; `CLAUDE_CONFIG_DIR` per tenant [R] |
| **Lost / silently dropped messages** | pre-v2.1.236 sends reported success while receiver dropped [R]; hold-then-expire after 5 min; queue caps 50/100; mailbox corruption blocked delivery before v2.1.207 [R] | upgrade; `crossSessionInbound=accept` for unattended `-p` workers [R]; don't carry state in messages — put it in a ledger [I] |
| **Stale state / status lag** | "Task status can lag … blocks dependent tasks" [R]; lead "stop[s] early, deciding the team is finished" [R] | `TaskCompleted`/`TeammateIdle` hooks as gates [R]; liveness monitors (Witness) [R]; `notify_when_idle` [R] |
| **Name ambiguity** | duplicates still possible for `--bg`/`-p`/older versions [R] | address by id suffix; name every session explicitly [R] |
| **Lost work across branches** | Willison's "parallel agent psychosis", recovered from session logs (community, 2025-10-05) https://simonwillison.net/2025/Oct/5/parallel-coding-agents/ | worktree sweep keeps dirty worktrees [R]; ledger of what lives where [I] |
| **Review bottleneck** | Willison: reviewer speed is the natural limit (community) [R via search summary]; vibe-kanban: "the most impactful way to ship more is to get faster at planning and review" [R] | dedicated reviewer/evaluator role (Anthropic harness [R]); inline diff comments to agent (vibe-kanban) [R] |
| **Token cost** | ~15× tokens for multi-agent [R]; teams scale linearly [R]; harness 22× cost of solo run [R] | size guidelines, small teams (3–5), cheaper models for workers [R] |

Community claim to treat with caution: some blogs say agent-team teammates each get an isolated worktree
(e.g. MindStudio, https://www.mindstudio.ai/blog/claude-code-agent-teams-shared-task-list) — **contradicted** by
the official docs ("Agent teams don't isolate teammates in worktrees") [R].

---

## 5. Gaps / what I could not establish
- `~/.claude/sessions/*.json` format and the cross-session registry format: searched docs pages
  `claude-directory`, `sessions`, `cross-session-messaging`, `agent-view`; **no schema or stability statement found**.
- Conductor license/source: only docs site found (queries: "conductor.build docs …"); no public repo located.
- Whether agent teams' task-list file locking is safe across *separate* sessions: docs scope a team to one
  session ("You can't … share a team across sessions") [R], so no cross-session task list exists natively.
- No official "merge queue" or "reviewer" role primitive exists in Claude Code; roles are user-defined via
  subagent definitions [R/I].
Queries run (WebSearch): "anthropic.com engineering parallel Claude agents C compiler lock files git";
"anthropic engineering blog long-running agents harness multi-agent coding"; "conductor.build docs workspace setup
script CONDUCTOR_PORT git worktree"; "Simon Willison parallel coding agents lifestyle worktrees problems";
"github anthropics claude-code issue concurrent sessions corrupt .claude.json …"; "claude code agent teams
experience problems teammates duplicate work file conflicts review 2026".
