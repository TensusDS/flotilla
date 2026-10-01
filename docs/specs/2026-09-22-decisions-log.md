# flotilla — decisions log (brainstorming, 2026-09-22)

Working log of decisions taken with Max before the spec. Not a spec. Research sources beside it:
`research-skills.md`, `research-orchestration.md` (and the saved docs pages `*.md`).

## Decided

1. **What**: a standalone, project-agnostic Claude Code **plugin** named `flotilla` — spawns a fleet of
   peer sessions with posts (orchestrator, sender, readers/reviewers, main, minor implementers) and ships
   the machinery for work between sessions. Structure of our ai-os fleet is kept; everything ai-os-specific goes.
2. **Audience**: public. Target listing: `claude-community` marketplace (third-party submissions land there
   after `claude plugin validate` + automated safety screening — plugins.md:362, :371). `claude-plugins-official`
   is curated by Anthropic; "Anthropic Verified" is a separate, non-guaranteed review.
3. **Platforms**: Linux + macOS. Native Windows explicitly unsupported (WSL = Linux). Every platform fork has two
   answers only: works the same, or refuses out loud — never "works worse, silently".
4. **Onboarding** exists and produces two profiles:
   - **project profile** (in repo, committed, shared): test commands, CI, release/versioning, default composition;
   - **machine profile** (per machine, in `${CLAUDE_PLUGIN_DATA}`): OS, available utilities, memory → lane capacity.
   Skill texts never carry raw platform-specific shell; they call `flotilla` commands, which read the machine profile.
   Variant references (`references/macos.md` / `linux.md`) only for what the model must *know*, not execute.
5. **Toggle**: "installed ≠ active". `defaultEnabled: false` (plugins-ref.md:557) + hooks exit silently when the
   project has no `.flotilla/` config + narrow matchers. Onboarding names each guard and asks before enabling it.
6. **v1 scope = A (everything)**: onboarding, spawn + posts, work ledger, lane (machine booking), guard hooks —
   built as sub-projects, released together. Build order: foundation → onboarding → ledger → spawn/posts → lane → guards.
   **Out of v1**: anything that parses transcripts (`session_inbox`, `fleet-probe`) — transcript format is internal
   (docs: sessions page, per research, not re-verified by me) and the directory policy restricts conversation-data
   collection (per research summary, not re-verified).
7. **Dogfooding**: ai-os migrates onto flotilla. Hence **extension points in the config format from day one**:
   custom posts (judge, prod companion, triage holder live as custom posts), custom evidence fields on ledger moves
   (our register obligation code; someone else's Jira ticket; or nothing).
8. **Fleet = one machine.** Ledger storage behind an interface. Shared ledger via a service → TODO (future).
   Shared ledger via git refs → considered, not chosen.
9. **Approach 1**: one `flotilla` CLI + thin skills; code **ported** from ai-os `tools/` with its tests, not rewritten.
   - CLI lives in `scripts/`, not `bin/` (bin/ is rejected by claude.ai org distribution — mkt.md:861);
     skills reference it via `${CLAUDE_PLUGIN_ROOT}` placeholder (skills.md:413); hooks get it as env (hooks.md:497);
     the spawner writes the absolute path into post prompts.
   - Skills: `flotilla` (the arrangement) + `flotilla-onboard`. Posts are **templates**, not skills.
   - Census via `claude agents --json` (supported surface); `~/.claude/sessions/*.json` only as a fallback with
     an honest refusal.
   - Guards are a seatbelt, not a lock: a timed-out hook does not block (hooks.md:873) — must be fast, documented so.
   - Python ≥3.10, stdlib only; onboarding checks `python3` is real (macOS shim).
   - CI: GitHub Actions built in (via `gh`); other CI via a gate-status command in the project profile; absent →
     the sender asks the human, never assumes green.

10. **Positioning against agent teams — layers, not rivals** (Max's correction): agent teams coordinate agents
    *inside* one lead session (one process tree, one lifetime); flotilla coordinates *independent* sessions, each
    in its own worktree, through the census and a ledger no session owns. A flotilla `main` session may itself
    lead an agent team or subagents; flotilla sees one branch and one ledger row. The comparison table is a list
    of what to borrow, not a verdict. README says: helpers inside a session → subagents / agent teams; peers
    across sessions → flotilla.
11. **Added to v1** (format-defining, cannot change after publication):
    1. a post IS a Claude Code agent definition (`agents/*.md` format: frontmatter `tools`, `model` + body) —
       usable both as a flotilla post and as an agent-team teammate type;
    2. events on ledger moves (`handed`, `accepted`, `shipped`, …): user scripts, exit 2 rejects the move —
       the same extension point as custom evidence fields;
    3. dependencies between ledger rows with automatic unblocking (borrowed from agent teams);
    4. "do not accept your own work" enforced by the accept door, not only by the post text;
    5. metrics derived from the ledger (time in state, return rate, reviewer throughput) — replaces `fleet-probe`,
       reads no transcripts.
    **Roadmap (README, not v1)**: machine resource broker (ports / DB names per worktree), stuck-session watcher
    via `claude agents --json` status, merge queue that bisects a red batch, shared ledger across machines.

12. **Design section 1 (parts and boundaries) — approved with additions.** Three homes: plugin (same for everyone),
    project `.flotilla/` (committed: `project.toml`, `posts/*.md`, `events/`), machine `${CLAUDE_PLUGIN_DATA}/`
    (`machine.toml`, `ledger/` per repo keyed by origin URL, `lane/`, `names.json`). Post templates live in
    `templates/posts/`, NOT the plugin's `agents/` (that would register them as subagents for everyone, breaking
    "installed ≠ active"); exposing them under `.claude/agents/` is the human's opt-in. Boundaries: skills call only
    `flotilla`; the ledger knows nothing of spawn; `core.storage` is the only writer of ledger files. A move runs:
    transition legal → evidence present → dependencies met → `pre-` event (exit 2 rejects) → write under lock →
    print the new state and whose move is next.
13. **Watchers, crons and hooks** (lesson of `your-move.py`: a watcher that writes a file nobody opens is silent —
    a branch waited 14 h and another 15 h with every instrument present). Principle: **a check is computed from the
    ledger and git; it is delivered by a hook into the session, not into a file.**
    - component `watch/`: deviations, whose move, stalled handovers, is trunk green — functions, no schedule;
    - session hooks (silent without `.flotilla/`): `SessionStart` (name, post, live peers, inherited breaks);
      `UserPromptSubmit`, throttled, no thresholds (whose move is it; fleet-wide deviations to the orchestrator only);
      `PreToolUse` Bash → **one** `flotilla guard` process running all enabled guards;
    - guards in v1: revert, line-number edit, **push receipt** (no push without a green run over this exact
      revision, recorded override) — the push receipt had been missing from the list;
    - git hooks (`pre-commit` file reservation, `pre-push` second push barrier) installed by onboarding with
      consent, each named;
    - user extension = ledger-move events; flotilla never writes the user's `settings.json`;
    - **cron: option A** — none in v1; `flotilla watch --once` with a meaningful exit code for any scheduler.
      Onboarding-installed schedule (crontab on Linux, launchd on macOS, with consent) → first roadmap item.

14. **Design section 2 (profiles and onboarding) — approved.** Python **≥ 3.11 + TOML** (`tomllib`; onboarding
    checks the version and says how to get one — macOS CLT python is believed to be 3.9, unverified on a live Mac).
    Onboarding steps: machine (measured, no questions) → project (detected, then **confirmed** — nothing decided
    silently) → composition (suggested by size: start main 1 + review 1; orchestrator + sender once >3 sessions)
    → guards and git hooks (each named, each its own yes). Code branches on **capabilities**
    (`proc_start = "ps"`, `timeout = "none"`), never on the OS name. `project.toml` carries `schema = 1`.
    Posts are template copies with a template version; `onboard --check` shows the diff on plugin update, never
    overwrites. The CI fingerprint is stored; a job absent from `required_jobs` closes the push door by name.
    - **Test tiers with a purpose** instead of fixed fast/full: `[[tests.tier]] name, command, required_for =
      ["handover" | "push"], measured_seconds`. Long tiers go through the lane.
    - **Onboarding runs every tier once** (through the lane) and records `measured_seconds`; a red tier is NOT
      recorded as working — onboarding shows the tail and asks whether the command or the project is wrong.
    - **Required jobs**: jobs triggered by push/PR to trunk are proposed; schedule-only, manual-only and
      uncomputable `if:` jobs are left out and named; matrices expand to per-variant names. The sender checks
      each required job by name via `gh run view`, never the run's overall conclusion alone.
    - **No CI / no origin**: origin+CI → `shipped` over green required jobs; origin without CI → `shipped` over
      the **local push receipt** only; no origin → `shipped` does not exist, the path ends `landed → closed`.
      Every gate-dependent move records **which gate** it stood on; `--brief` says "no CI, verified locally over
      <sha>" out loud. Other CI via `gate_command <sha>` → exit 0 green / 1 red / 2 pending; absent → the sender
      asks the human. Consequence for section 3: legal transitions are computed from the project profile.

15. **Onboarding questionnaire — approved.** Rule: the questionnaire asks only what is a human's *choice*;
    everything findable in files is found and shown as a prefilled "(detected)" answer (stack, test commands,
    trunk name, commit convention, version files, CI jobs, OS/tools).
    - **Core, always** (two rounds of 4, AskUserQuestion's limit): (1) how work reaches trunk; (2) who authorizes
      the merge; (3) review depth — every branch / only main / none; (4) background sessions and permissions
      (supported `--bg` permission modes to be verified in the spec, not invented); (5) CI detected / own gate
      command / none; (6) CI runs in the cloud or self-hosted **on this machine** (then the lane also asks the CI
      queue); (7) test tiers, multi-select what is required before shipping; (8) where tasks are tracked —
      none / GitHub Issues / Jira-Linear pattern / own register → evidence field and events.
    - **Conditional, only when detection finds a reason**: several repos and push order (path dependency on a
      sibling repo in a lockfile); versions and tags (tags `vX.Y.Z` or version files found); a deployment the fleet
      touches (`deploy/`, systemd units, compose → prod-companion and judge as custom posts); shared append-only
      files (CHANGELOG/TODO → file reservation); sequential numbers in files (migrations, ADRs → number claims —
      the ai-os `claim-code` mechanism generalises to DB migration numbering); can two full runs fit (slow first run
      or little memory); model per post (default one for all).
    - Simple project: 8 core questions, no conditionals. ai-os: 8 + 6, answers become custom posts and evidence fields.
16. **Pull request is the default and recommended path**; direct push to trunk is offered with the note "for
    experienced users or simple projects — CI runs after the code is already in trunk". Consequences:
    - `shipped` in PR mode asks **the PR's state** (`gh pr view --json state,mergeCommit`), not ancestry — squash
      and rebase merges never make the branch tip an ancestor of trunk; ancestry stays for direct push only;
    - merge method is detected (`gh repo view` allowed methods); one conditional question if several are allowed;
    - v1 sender merges PRs **one at a time**: checks green → branch up to date → merge → update the next → wait
      (the batch becomes a queue; bisecting merge queue stays on the roadmap);
    - review lives in the ledger (`accepted` over a revision); the PR is transport; posting the verdict as a GitHub
      review from the human's account is an outward action, off by default, its own checkbox;
    - question 2 becomes "who authorizes the **merge**".
    - **Who opens the PR: the sender, at batch time** (default) — every outward write (branches on origin, PRs,
      merges) stays with one session, one human "yes" per batch. Option `pr.opened_by = "author"`: the author's
      handover pushes the branch and opens a draft PR so CI runs in parallel with review — for teams with long CI
      whose branch pushes need no permission.

17. **Design section 3 (ledger) — approved.** Event-sourced storage (append-only JSONL of moves per repo, current
    state = fold) → metrics, "what did I accept" and recovery by construction. Legal transitions computed from the
    profile (review none/main-only/all; PR vs direct push; no origin). New doors vs ai-os: `handed` requires the
    `handover` tiers green over the tip; `fixing` carries `--why` in the row; `accepted` refuses reviewer == owner;
    `closed` validates `[evidence]` from the profile. Dependencies: start any time (stacking allowed), but no
    `queued` until the dependency is `shipped`; roster shows "blocked on X"; the owner is told when unblocked.
    **Duplicate `--ref` among open rows is refused** (override `--also "<why>"`, recorded) — ai-os's warning-only
    door let three sessions write the same work in ~14 h.
    - **A broken event script (crash / timeout) refuses the move and names the script** (Max: A) — and must be
      rare: `flotilla events check` (executable, shebang interpreter exists, sample-input run) at onboarding and
      `SessionStart`; versioned input contract `event_schema = 1`, generated by flotilla code; the refusal prints
      the tail and a reproduce command (`flotilla events run pre-accepted --row <branch>`); `pre-` runs before the
      write, so a fixed script just retries; emergency `--skip-event <name> --why` by a human decision, recorded;
      event failures counted in metrics.
18. **Broken chain of responsibility** (Max's case: reader's run finished, reader silent, author and sender believe
    the run is still going, work stands). Three breaks, three cures, one shared computation — **"whose move"** per row:
    - **the run is a ledger fact**: `flotilla lane run --for <branch> -- <cmd>` ties start, end, exit code and
      revision to the row; roster says "reader's run finished green 12 min ago, waiting on the verdict";
    - **Stop guard — do not fall silent holding the ball**: on `Stop`, if this session holds a move, has no
      `background_tasks` that will wake it (hooks.md:2554) and no recorded wait → block with the next legal moves;
      legitimate waiting is itself a move, `flotilla work wait --on <whom> --why`, so the ball visibly lies with
      the human; `stop_hook_active` → do not block twice, record the break for layer 3;
    - **dropped-ball watcher** (pulled from roadmap into v1): ledger says "your move" + `claude agents --json`
      says `idle` (verified live: `status` ∈ busy/idle over 16 sessions) + no wait recorded → delivered by hook to
      the orchestrator, who messages the holder; holder absent from the census → orphaned → `adopt`.

19. **Live probe, 2026-09-22, Claude Code 2.1.280** (throwaway repo in scratchpad, two `--bg` sessions, `auto` mode;
    removed afterwards). Scheme A = flotilla-cut sibling worktree + `--add-dir`, launched from the main checkout;
    scheme B = native `--worktree`.
    - A: the edit landed in our tree; the session did NOT move into `.claude/worktrees/`. An edit into the main
      checkout was **refused**: "This background session hasn't isolated its changes yet… a path inside a linked
      git worktree, including one you create with `git worktree add`, is accepted" (switch: `worktree.bgIsolation:
      "none"`). `git -C <main checkout>` reads and compound git commands **worked**.
    - B: tree `.claude/worktrees/probe-b` inside the repo (main checkout gains untracked `.claude/`), branch
      `worktree-probe-b`, `locked`. `git -C <main checkout>` **refused** ("a worktree-isolated session's git operations
      must target its own worktree"); compound git commands **refused** ("too complex to verify").
    - Identity: walking up from the Bash `$$`, the first parent is the pid that `claude agents --json` lists for the
      session. The process `comm` is the version (`2.1.280`), not `claude` → match by census pid, never by name.
    - Side notes: probe A ended as `state: blocked, status: idle` after the refused edit (not investigated);
      `claude rm` right after `claude stop` refused while the native tree was still locked, a retry succeeded →
      `flotilla retire` must wait for the lock, not fail first time.
20. **Section 4.2 → scheme A** (recommended from the probe): flotilla cuts its own linked worktrees; Claude Code's
    background-isolation guard stays ON and becomes a free platform-enforced "the shared checkout belongs to nobody";
    onboarding says so and never disables `bgIsolation`. B is unfit for the fleet (no trunk reads via `git -C`,
    fixed branch naming, one tree, one repo); C unnecessary. Caller identity for `may` (post move permissions) via
    ancestor walk + census pid — a supported surface, no internal registry.

21. **Design section 4 (spawn and posts) — approved, scheme A, with the acceptance judge.**
    - Spawn ported from `spawn_fleet.py`: names from the issued-numbers journal; `name_pattern` from the post;
      acceptors first; "address not read" ≠ "did not start" (ask the census before declaring failure); `--dry-run`.
      Launch: `cd <main checkout> && claude --bg -n <name> --add-dir <tree> --permission-mode <Q4>
      --append-system-prompt <post + name + tree + absolute CLI path> "<invoke flotilla, census, announce, wait>"`.
    - Post = agent definition + flotilla keys (`name_pattern`, `may: [moves]`, `writes_one_copy`). The ledger checks
      **who** makes a move (caller identified by ancestor walk → census pid): `accepted` needs `accept` in `may`;
      queue/merge only for `writes_one_copy: true`.
    - Life of the fleet: add with `flotilla spawn -r 1`; revive with native `claude respawn` (flotilla checks name
      and tree kept); `flotilla retire <name>` waits for the tree lock, frees the post row, leaves unfinished work
      orphaned → `adopt`.
    - **Acceptance judge is a STANDARD template** (reverses the earlier "ai-os custom post"): the only post asking
      "does it reach a human"; 0 by default, offered when onboarding finds a deployment or a dev server; new moves
      after `shipped`: `walked` (build revision, steps, observed) and `broke --where "<place in the UI>"` (refused
      without a place; spawns a linked fix row assigned to the author); `judge.required = true` → no `closed`
      without `walked`; profile gains `deploy.revision_command`; the judge compares deployed vs shipped revision
      and a verdict names the build it walked; a walk spanning a restart is not recorded. "No reading code before
      the attempt" possibly enforceable via path-scoped deny rules (`Read(src/**)`) — unverified for spawned `--bg`
      sessions, open question. Tools by surface: browser (Playwright / Chrome MCP), terminal (CLI), curl (API).
    - Remaining ai-os custom posts: prod companion, triage holder.

22. **Design section 5 (lane, guards, verification) — approved.**
    - Lane ported from `lane.py`: honest "not a lock"; four questions (peer booking / a foreign run exists via
      `pgrep` / is it computing via two CPU-time samples, procfs or `ps -o time=` by machine capability / CI gate
      on this machine via `gh run list`, only when the profile says self-hosted here). `lane run --for <row> -- <cmd>`
      takes the command's own exit code (not a pipeline's), tells signal kills (137 / -9) from failures and records
      "killed, no verdict", writes the summary line ("212 passed") into the row. No clock expiry; `lane sweep` removes
      only rows with no process behind them.
    - Guards in one `flotilla guard` process: revert, line-number edit, push receipt (`git push`, `gh pr create`,
      `gh pr merge`, `gh workflow run`; override `FLOTILLA_GATE_OVERRIDE="<why>"`, recorded). Plus the `Stop` guard and
      git hooks `pre-commit` (file reservation) and `pre-push` (second barrier, catches pushes hidden in scripts).
      **A guard's own failure is decided by reversibility**: revert and line-edit allow with a warning; push receipt
      refuses. Every guard documents its ceiling. Budget ~200 ms per call, tested.
    - Verification: pytest (ported); CI matrix Ubuntu + **macOS** × Python 3.11/3.12/3.13 (macOS is supported while its
      runner is green); generated `event_schema` golden; `claude agents --json` parsing tested on **recorded** outputs
      of several Claude Code versions; each guard seen red by removal and by a plausible neighbour; `claude plugin
      validate` in CI; `claude plugin eval` with baseline and a triggering eval (near-miss negatives) before release;
      a manual e2e smoke modelled on the 2026-09-22 probe before release; `flotilla doctor`.
    - **Publication criterion**: ai-os runs on flotilla for several real shifts first (ledger imported, judge and
      triage holder as custom posts, register closing and the board as events).
23. **English everywhere in flotilla** (Max): identifiers, comments, docstrings, CLI output, post templates, skills,
    README, spec, examples, guard messages, commit messages. A CI check refuses Cyrillic (U+0400–U+04FF) in the repo.
    Measured on ai-os `main` in the shared checkout: the 15 code files to port carry 6,572 Cyrillic lines of 12,412
    (docstrings, comments, output, ~540 lines of Cyrillic identifiers by a rough heuristic); their 12 test files carry
    9,058 of 18,678, with ~1,220 asserts comparing Russian output; `fleet-roles.md` 30 of 227, and the spawner looks up
    sections by Russian titles. Consequences:
    - each module is ported in **two separate steps**: (1) move as is, tests green, behaviour unchanged; (2) translate
      output and its asserts **in one commit**, reviewed for meaning, not only by a green run;
    - rationale docstrings are **rewritten** for a general reader; ai-os measured precedents move to
      `references/why.md` as the evidence base;
    - translation is a separate task per module in the plan, roughly the size of the port itself.

24. **`inbatch` creates its own row, before the push.** Work born inside the sender's batch has no branch and no
   claim; `flotilla work inbatch <label> --commit <sha> --read-by <name> --why "<what>"` records a finished row
   whose commit must be on the local trunk and whose reader is not the sender. The land door then accounts that
   commit. ai-os recorded it after the push, which left the push door unable to see it (its own docstring names the
   gap); a commit that never reaches origin shows up as a finding instead. Direct push and local only: in PR mode
   every change reaches trunk through a PR. (executor's decision, ledger part B plan, 2026-09-26)
25. **`broke` creates the linked fix row at once** (spec 6.3, literally): a `claimed` row on `fix/<branch>` owned by
   the original owner, with `fixes` pointing at the broken row and no tree yet. `flotilla tree cut fix/<branch>` by
   that owner picks the row up instead of refusing it. The broken row cannot be walked or closed until a fix row is
   delivered, and the walk must be over a build containing the fix. (executor's decision, ledger part B plan, 2026-09-26)
26. **Events are named by the state a move enters** (`pre-handed`, `post-shipped`), as in the spec's examples, and
   fire only on moves that change the state. Annotations (`wait`, `hold`, `take`, `assign`, `moved` inside
   `handed`, ...) fire nothing. Scripts are read from trunk, like the profile and posts (part A's I4 fix), so a
   branch cannot switch a check off for itself. A `pre-` script runs inside the log's lock (bounded by a 30 s
   timeout), because running it outside would let the row change between the check and the write. (executor's decision, ledger part B plan, 2026-09-26)
27. **A moved tip after acceptance goes back to `handed`** (new edge `accepted --moved--> handed`, verdict cleared,
   the reader's agreement required as for any taken branch). Without it an accepted branch that moved could only be
   released. (executor's decision, ledger part B plan, 2026-09-26)
28. **Out-of-turn is a move, `urgent`**, an annotation the orchestrator makes (`--why`, `--cancel`). It changes
   nothing but the order of the sender's brief, oldest request first. Added to the orchestrator's `may`. (executor's decision, ledger part B plan, 2026-09-26)
29. **"Read after merge" is the `offledger` witness.** Work that reached trunk outside the ledger is recorded only
   with a named witness who is not its owner; the proof that the named commit carried the work is measured where
   git can say (the merge brought the tip; or the commit carries the same change, by `git patch-id`) and otherwise
   recorded as `--attested` text, marked unmeasured. The ai-os "records ride without a reader" bypass stays dropped. (executor's decision, ledger part B plan, 2026-09-26)
30. **Absent CI never reads as green.** With no CI provider and no push tiers, `shipped` stands on origin alone and
   its gate says `none: no CI and no push tiers — nothing verified this commit`; `brief` prints it out loud. (executor's decision, ledger part B plan, 2026-09-26)
31. **No thresholds in deviations; stalled is the only timed view.** Deviations are states from which the expected
   move is impossible right now (nobody named, the mover is gone, a hold whose condition is met or cannot be asked).
   "Has not moved for N hours" is `status --stalled N`, asked by a person. (executor's decision, ledger part B plan, 2026-09-26)

32. **Permission modes from the questionnaire:** "they ask me" → `manual`; "allow rules exist" → `dontAsk` (a call
   the rules do not allow is refused, visibly, instead of stalling on a prompt nobody sees); "auto mode" → `auto`.
   A post may override with an optional `permission_mode` key (any value `claude --permission-mode` accepts). (executor's decision, spawn and posts plan, 2026-09-27)
33. **"Strongest reviewer"** (`fleet.model = "reviewer-strongest"`) gives every post that may `accept` the `opus`
   alias; otherwise a post's `model` key is passed when it is not `inherit`. (executor's decision, spawn and posts plan, 2026-09-27)
34. **Names are machine-wide**: the census is machine-wide, so the issued-numbers journal is one per machine
   (`<state>/fleet/names.jsonl`). A new name is above every number ever issued for that post, above every live
   session's and every ledger name's number, and never equal to a taken name. (executor's decision, spawn and posts plan, 2026-09-27)
35. **The spawner records the post row, not the new session.** The reserve row is written with the new session's
   name as `by`, `via: spawn`, and the real caller in `caller` — part A's rule that a session never acts under
   another's name stays whole, because the spawner is not a session acting, it is the one who creates it. (executor's decision, spawn and posts plan, 2026-09-27)
36. **The session is found in the census, not in `claude --bg`'s printed text.** Printed text is not a supported
   surface; `claude agents --json` is. A session launched but not listed within the wait is reported as "launched,
   not yet seen — do not launch it again", never as a failure (spec 7.2: "address not read ≠ did not start"). (executor's decision, spawn and posts plan, 2026-09-27)
37. **Retire stops, never deletes.** It stops the session, waits for the census to agree, unlocks the tree, releases
   the post row, and prints the tree's uncommitted file count and every open row the session still owns (orphaned,
   for `adopt`). Removing the tree is a person's step, printed as a command. (executor's decision, spawn and posts plan, 2026-09-27)
38. **One-copy posts stay single:** a spawn that would leave two live sessions holding a post with
   `writes_one_copy: true` (the sender) is refused naming the one alive. (executor's decision, spawn and posts plan, 2026-09-27)
39. **The census cannot tell "waiting for a task" from "waiting on a permission prompt"** (both read
   `state: blocked`, measured 2026-09-27 on 2.1.283 over eight background sessions). `flotilla fleet` prints the
   state as the census gives it; telling them apart belongs to the watchers part. (executor's decision, spawn and posts plan, 2026-09-27)

40. **Tasks are taken in the home tree** (Max, 2026-09-27, after the spawn review): a spawned session may edit only
   the directory it was launched with, so `flotilla tree switch <branch>` moves its clean home tree to a new
   branch from trunk and claims it; reviewers read a handed tip there detached. A permission broker (the
   `PermissionRequest` hook queueing questions to the orchestrator, one at a time, oldest first) is planned for
   the watchers-and-guards part, starting with a live measurement.

41. **One booking log per machine**, in the state directory (`<state>/lane/lane.jsonl`), event-sourced like the
   ledger: the machine is the resource, and a record kept in a repository would be invisible to a branch next to it. (executor's decision, lane plan, 2026-09-27)
42. **The process table is read from procfs or from `ps -A -o pid= -o ppid= -o command=`**, never `pgrep`: macOS
   `pgrep -a` means "include ancestors", not "print the command", so a pgrep-based reader would silently mean two
   things. A reused pid is told apart by the process start mark (procfs start time, or `ps -o lstart=`). (executor's decision, lane plan, 2026-09-27)
43. **Run patterns** are a default list (pytest, playwright, vitest, jest, `cargo test`, `go test`) that a profile
   replaces with `[lane] run_patterns`. Processes whose argv0 is a shell are skipped (the run they wrap is its own
   process and is seen), as are the caller's own ancestors and the processes of live bookings. (executor's decision, lane plan, 2026-09-27)
44. **"Computing"** = CPU time grew over a two-second sample, or the process is younger than 30 minutes (a suite
   pausing on I/O is not idle). An older process that did not compute is reported and does not block: ai-os found
   three stray runs alive for nine days on ten seconds of CPU. (executor's decision, lane plan, 2026-09-27)
45. **Capacity** comes from `machine.toml` `lane_capacity` (default 1); computing foreign runs use slots; CI on this
   machine blocks outright, and a CI queue that could not be asked blocks too ("not asked is not free"). (executor's decision, lane plan, 2026-09-27)
46. **`lane run --for <branch>` records the result on that branch's open ledger row** as an annotation (`run`,
   field `last_run`: verdict, summary, revision, time). It is a fact about the machine, not a decision, so no post
   check applies; the caller is identified as for any move. (executor's decision, lane plan, 2026-09-27)
47. **Receipts take the lane themselves** (`flotilla receipt run` waits up to 30 minutes, `--lane-wait`, or
   `--no-lane` recorded in its output), because a receipt's tiers are exactly the long runs the lane exists for. (executor's decision, lane plan, 2026-09-27)
48. **No clock expiry.** `--wait` bounds only the caller's own waiting; `sweep` removes only bookings whose process
   is gone; a booking taken by hand has no process and stays until released by hand. (executor's decision, lane plan, 2026-09-27)

49. **A hook finds its session by the `session_id` Claude Code passes it**, matched to the census `sessionId`
   (measured 2026-09-27: this session's census `sessionId` is its conversation id); failing that, by the parent-pid
   walk the ledger already uses. A session the census does not list is told so, and no move is computed for it. (executor's decision, watchers plan, 2026-09-27)
50. **The prompt hook speaks when what it would say changed, or 30 minutes after it last spoke**; with nothing to say
   it is silent and forgets its stamp, so the next thing is said at once. Ages are printed but are not part of
   "changed". A recorded wait is not repeated at every prompt (it is said at session start). (executor's decision, watchers plan, 2026-09-27)
51. **The Stop guard blocks only when it knows all of it:** a background session (an interactive one has a person in
   front of it); a move it holds (a ball, or claimed work) with no recorded wait and no hold; `background_tasks`
   and `session_crons` both present and both empty (absent means the task registry was unreachable, and then
   nothing says the session will not be woken). Anything it could not ask lets the session stop: a guard that
   cannot ask must not trap a session. `[watch] stop_guard = false` in the profile turns it off. (executor's decision, watchers plan, 2026-09-27)
52. **A second stop in a row (`stop_hook_active`) is let through and recorded as a break**, in
   `<state>/watch/breaks/<repo key>.jsonl`. A break stays open while the row is still that session's move, has no
   recorded wait, and has not moved since the break; no clock closes it. (executor's decision, watchers plan, 2026-09-27)
53. **A dropped ball** = the move is a live session's, it recorded no wait, the row is not held, and the census says
   the session is not working (background `state` blocked or done; interactive `status` idle). `blocked` also means
   "waiting on a permission prompt" (measured 2026-09-27), so the item says both. A move named for a post ("the
   sender", "the judge") belongs to the live sessions of that post; none alive is its own item. (executor's decision, watchers plan, 2026-09-27)
54. **Only the orchestrator hears the fleet** (deviations, dropped balls, a post nobody holds, breaks), at session
   start and in its prompt hook; every session hears its own moves. (executor's decision, watchers plan, 2026-09-27)
55. **`flotilla watch --once` exits 0 when nothing needs attention, 1 when something does, 2 when the census or the
   ledger could not be asked** (printed, never "none"). Without `--once` it refuses: v1 has no schedule of its own. (executor's decision, watchers plan, 2026-09-27)
56. **The session-start hook checks event scripts without running them** (a known name, an executable file, an
   interpreter that exists): a sample run can outlast the hook's timeout. `flotilla events check` stays the full
   check and is named in the line. (executor's decision, watchers plan, 2026-09-27)
57. **`[ci] queue_command`** answers "is CI using this machine right now": exit 0 no, exit 1 yes, anything else (or
   not runnable, or 25 s) could not ask — an unknown the lane waits on, not a lasting one, since the queue may
   answer later. When set it is asked for any provider; unset with a gate command on this machine stays the lasting
   unknown, now naming the key. Onboarding writes it empty for a gate command on this machine, so the key is found. It runs through the shell, like the gate command (final review, 2026-09-27). (executor's decision, watchers plan, 2026-09-27)

58. **One process, cheap common path.** `PreToolUse` on `Bash` runs `flotilla hook guard` for every Bash call. A
   command that names none of `checkout restore reset clean sed push gh` exits before a project is looked up or a
   guard module imported; only a command that names one reads the rules. (executor's decision, guards plan, 2026-09-27)
59. **The command line is matched, not parsed** (as in ai-os, where a real parser was measured to buy one command in
   1,941). Segments split at `&& || ; | &` and newlines, twice (plainly and respecting quotes), the union counting;
   heredoc bodies dropped; leading `NAME=value`, `env`, `sudo command nice nohup time exec` and shell keywords
   (`if then else elif do while until ! { (`) peeled; `cd <literal>` followed. A directory named through a variable
   is unknown. The ceiling is written in the module, in `flotilla guard --help` and in the README. (executor's decision, guards plan, 2026-09-27)
60. **The revert guard refuses only when git says there is something to lose**, and names the fix that works for
   that form: `git add` where the index is the source (`checkout -- f`, `restore f`); commit or stash where the
   command overwrites the index too (a named source, `restore --staged --worktree`, `reset --hard`, `checkout -f`);
   move or stash untracked files before `clean -f` (the list comes from `git clean -n` with the same flags). It never
   makes the save point itself. (executor's decision, guards plan, 2026-09-27)
61. **The line-number guard refuses `sed`/`gsed` in place (`-i`, `-i.bak`, `-i ''`, `--in-place`, clusters) when a
   script command starts with a line number** (`12d`, `3,5s…`, `1~2p`, `10i…`, `5!d`, `7{`). `$`, `/regex/` and a
   digit inside a substitution pass. A script read with `-f` is not opened. (executor's decision, guards plan, 2026-09-27)
62. **A push to a branch other than trunk needs no receipt** (ai-os, Max's decision of 2026-09-06): it lands nowhere,
   and the receipt is asked where it lands — a push to trunk or a tag, `gh pr create` (the PR head), `gh pr merge`
   (the head `gh pr view` names), `gh workflow run` (HEAD). A push the text cannot pin (`--all`, `--mirror`,
   `--tags`, `--follow-tags`) asks for HEAD, and the `pre-push` hook asks git for each ref. (executor's decision, guards plan, 2026-09-27)
63. **A receipt is valid only if the CI workflow at that revision is the one the profile at that revision records**
   (`ci.workflow_fingerprint`, same digest as onboarding's). Reading the profile at the revision, not on trunk, lets
   a branch that changes the workflow update the fingerprint in the same commit instead of being locked out. (executor's decision, guards plan, 2026-09-27)
64. **`FLOTILLA_GATE_OVERRIDE="<why>"` is read from the door's own leading assignment or the environment**, never from
   anywhere else in the line (a comment or a neighbouring segment must not open a push), and is recorded in
   `<state>/guards/<repo key>.jsonl`. Revert and line-number have no override: their fix always exists. (executor's decision, guards plan, 2026-09-27)
65. **Failure is decided by reversibility.** Revert and line-number allow with a warning; the push receipt refuses,
   including when the rules cannot be read or the tree cannot be named. A door in a repository that is not a
   flotilla project is not judged; in another flotilla project it is judged by that project's rules and receipts. (executor's decision, guards plan, 2026-09-27)
66. **Rules come from trunk, except before onboarding reached trunk**, when the tree's own profile is obeyed and the
   guard says so (otherwise the push that brings the profile to trunk would be judged by no rules at all). Receipts obey the same rules, or the first push's receipt could not be run (final review). (executor's decision, guards plan, 2026-09-27)
67. **Reservations** are a log in `<state>/reservations/<repo key>.jsonl`. A staged change deleting more than three
   lines of a file matching `[reservation] files` is a rewrite; the first open row to commit one holds the file
   until the row closes or is delivered. A merge neither needs nor takes a reservation for what it brings in.
   `FLOTILLA_RESERVE_OVERRIDE="<why>"` lets a refused commit through, recorded. `flotilla status` lists live
   reservations when the profile names reserved files. (executor's decision, guards plan, 2026-09-27)
68. **Git hooks call a stable link**, `<state>/bin/flotilla`, which every session start (and every install) points at
   the running plugin's entry: the plugin's path moves with each update, a hook file does not. Install never
   overwrites a hook flotilla did not write and never writes into `core.hooksPath`; it prints the line to add
   instead. Hooks go to the common git directory, so one install covers every worktree. A `pre-push` that finds no
   link refuses (an override passes, unrecorded, and says so); a `pre-commit` that finds none passes. (executor's decision, guards plan, 2026-09-27)
69. **The `pre-push` hook obeys `guards.push_receipt`** like the command guard; `/flotilla:guard` offers `pre-push`
   when that guard is on and `pre-commit` when the profile names reserved files, one yes each. (executor's decision, guards plan, 2026-09-27)

70. **Live probe, 2026-09-27, Claude Code 2.1.283: the permission broker, the hooks, the census** (four `--bg`
   sessions on haiku, launched with `--settings` carrying logging hooks; removed afterwards).
   - `PermissionRequest` fires in a background session; `permission_suggestions` offered `addDirectories` and
     `setMode: acceptEdits`. Allow and deny both arrive; a deny's `message` reaches the session verbatim.
   - An allow without `updatedPermissions` is one-time: the next `touch` asked again. `updatedPermissions` with
     `destination: "session"` applies at once: `setMode` switched the session to `acceptEdits`, and an exact `addRules`
     entry (`Bash`, `touch omega.txt`) let that command run twice without asking.
   - While the hook holds its answer the census shows `state: blocked, status: waiting`, then `working, busy` again.
   - **A hook that reaches its timeout does not deny: the session hung on an unanswerable prompt, `blocked, waiting`,
     for four minutes until stopped.** The broker must answer, deny included, before its own timeout.
   - Census: a background session reports `status` too — `busy` working, `idle` idle (`state` blocked or done),
     `waiting` on a prompt. The hook's `session_id` equals the census `sessionId`.
   - A `Stop` block reaches a background session as a continuation (it ran the command the reason named); the second
     stop carried `stop_hook_active: true`; `background_tasks` and `session_crons` arrived as empty lists.
   - A `PreToolUse` deny reaches a background session with its reason.
   - `claude stop <id>` removes the session from the census at once; `claude stop <name>` answers "No job matching".
   - `claude --bg` refuses an untrusted directory ("Workspace not trusted"), and trust is not inherited from a trusted
     parent: a spawn needs a directory the person has accepted once.
   - In `default` mode `touch` asked for permission and `mkdir` did not.

71. **The broker is on exactly when the profile's `permissions.mode` is `ask`** (sessions spawned in `manual`), unless
   `[broker] enabled = false`. In `rules` or `auto` mode sessions do not wait on a person, so there is nothing to
   broker. No new onboarding question. (executor's decision, broker plan, 2026-09-27)
72. **Only background sessions are brokered.** An interactive session has a person in front of its dialog; a session
   the census cannot place (census down, not listed) is left to the dialog too: a deny there could refuse a person
   who was about to answer. (executor's decision, broker plan, 2026-09-27)
73. **Questions are files, answers are written once.** `<state>/broker/<repo key>/q-<id>.json` per question and at
   most one `a-<id>.json`, created with `os.link` so that an answer and the hook's own withdrawal racing at the
   deadline cannot both count. The id sorts by time, so "oldest first" is the file order. (executor's decision, broker plan, 2026-09-27)
74. **The hook waits at most `[broker] wait_seconds`** (default 540, clamped to 30–590) and the hook's timeout in
   `hooks.json` is 600: the hook withdraws the question and denies with a reason before Claude Code would kill it,
   because a killed hook leaves the session hanging (measured). Final review: the budget counts on a monotonic clock from the hook's own start, and the ceiling is 570 s. (executor's decision, broker plan, 2026-09-27)
75. **No live orchestrator is an immediate deny** naming the fix (spawn one, or give the session a rule). The
   orchestrator's own question (a background orchestrator) is denied too, naming `claude attach`: it cannot answer
   a question that blocks it. (executor's decision, broker plan, 2026-09-27)
76. **Three answers.** `allow` (once), `session` (allow, and apply Claude Code's own `permission_suggestions` with
   `destination: "session"`, or, when it offered none, an exact rule for that Bash command or the tool), `deny`
   (with the person's reason, which reaches the session verbatim — measured). Final review: "for the session" keeps rules and directories only, never a mode switch, and gives no rule for a Bash command holding `*`. (executor's decision, broker plan, 2026-09-27)
77. **A question is live while its hook still waits**: the hook's process is alive and its deadline has not passed.
   A closed, withdrawn or abandoned question is never shown, and answering it is refused with the reason, so the
   person never answers a question nobody is waiting for. (executor's decision, broker plan, 2026-09-27)
78. **The orchestrator watches with a background command**: `flotilla permit next --wait 3600` run in the background
   returns when a question appears, which wakes the orchestrator; it asks the person with AskUserQuestion, records
   the answer with `flotilla permit answer`, and runs the wait again. A question that arrives while another is
   being answered waits its turn (Max, 2026-09-27). (executor's decision, broker plan, 2026-09-27)
79. **The fleet view lists live questions first**, and a session with a live question is not reported as a dropped
   ball. A background session the census shows as `status: waiting` with no question queued is reported as "waiting
   on a permission prompt nobody answers". (executor's decision, broker plan, 2026-09-27)
80. **A failing permission hook gives no decision** (stderr only): the dialog then decides, which for a background
   session means the hang the dropped-ball item reports. Denying on the hook's own failure could refuse a person
   at an interactive dialog. Final review: once a session is known to be a background one, a failure is a deny with its reason, and so are rules that cannot be read. (executor's decision, broker plan, 2026-09-27)

81. **`land` in direct-push mode accepts a merge reachable from `origin/<trunk>`** (Max). The local trunk, checked
   out in the main checkout that belongs to nobody, is not needed and is never moved. The sender merges in its own
   tree on a branch from `origin/<trunk>`, takes the push receipt, pushes `HEAD:<trunk>`, then records `land` and
   `ship`. Pushing before `land` is therefore the sequence, not a fault (F15). (field test 2026-09-27; Max's triage 2026-09-28)
82. **The unread-work check still runs for a merge already on origin**, counted from what origin held when the sender
   merged (the landed commit's first parent; the row's base only when it has none) up to origin's head: a push that
   carried work nobody read, below or above the merge it names, is refused at `land` with the `inbatch` fix, even
   though it is already on origin — the ledger must not record it as read. Work origin already had before the merge
   (the person's own push) is not this batch's. Known limit: a commit somebody else pushes between the sender's push
   and its `land` is counted too, and refused; the refusal names it. (executor's decision, field fixes part 1,
   2026-09-28; range corrected after the branch review)
83. **`wait` is allowed to the row's mover** as well as its owner and reader (F24): the session `who_moves` names, or
   a session of the post a post-named mover ("the sender", "the judge") stands for. (executor's decision, field fixes part 1, 2026-09-28)
84. **A tip already on trunk has no commits of its own**: `hand` refuses it, naming `release --settled-by`, and the
   "finished_not_handed" deviation never fires for it (F18). (executor's decision, field fixes part 1, 2026-09-28)
85. **`release --settled-by <branch|row>`** closes a row whose purpose another delivered row fulfilled (F26); the
   other row must be delivered; the reason defaults to "settled by <branch>". (executor's decision, field fixes part 1, 2026-09-28)
86. **The judge waits for the rows that build on a row** (Max): while an open row that `requires` this one, directly
   or through other rows, is not delivered, `who_moves` names nobody for the judge's move, and `walked` / `broke` refuse, naming those rows.
   **`flotilla work walkable <branch> --why`**, an annotation the orchestrator may make, lets that row be walked on
   its own. (field test 2026-09-27; Max's triage 2026-09-28)
87. **`land` without `--merge` in direct-push mode records the earliest commit on origin's first-parent line that
   carries the revision read**, not origin's head: a row landed after another row was pushed on top names its own
   merge, not a commit carrying both. (executor's decision after the branch review, field fixes part 1, 2026-09-28)
88. **A fix row settled by another delivered row is its owner's to close**: `release` still refuses a live owner's
   row to anyone else, so the sender tells the orchestrator instead of releasing it; `--settled-by` given a branch
   name used more than once takes the delivered row. (executor's decision after the branch review, field fixes
   part 1, 2026-09-28)
89. **`walkable --clear` takes the orchestrator's word back**: the part waits for the rows building on it again;
   clearing a row that is not marked is refused. (executor's decision, R2 of the part-1 review, 2026-09-28)
90. **Only the orchestrator talks to the person** (Max): every other post sends its question to the orchestrator
   (SendMessage) and records `flotilla work wait <branch> --on "the person" --why "<the question>"` on the row it
   holds up; the sender's batch goes to the orchestrator too, which relays the person's yes (F12, F16, F17).
   (Max's triage 2026-09-28)
91. **A guard on AskUserQuestion** (Max): a PreToolUse hook, matcher `AskUserQuestion`, denies the question in a
   background session whose post is not `orchestrator`, naming the live orchestrators and the wait to record. An
   interactive session, a session with no post, and the orchestrator ask freely; a census or ledger it could not
   ask lets the question through with a note. (Max's triage 2026-09-28; the pass-through, executor's decision,
   field fixes part 2, 2026-09-28)
92. **Posts accept a task routed by the orchestrator** (Max), after checking that `flotilla fleet` lists the sender
   of the message as the live holder of the orchestrator post; any other peer's message stays information.
   (Max's triage 2026-09-28)
93. **Every ledger refusal ends with the way forward** (Max): one fixed line after every `refused:` — record the
   wait and tell the orchestrator; a refusal with no way forward is a flotilla defect, sent to the orchestrator
   verbatim; never git plumbing. Printed at the one print site, so no future refusal can miss it. (Max's triage
   2026-09-28; the single print site, executor's decision, field fixes part 2, 2026-09-28)
94. **A move that passes work on prints the letter; the post obliges sending it** (Max): the command line compares
   whose move every row was before and after the command and prints a letter for each session a row passed to,
   addressed from the census or, when it cannot be asked, by post. flotilla never sends it: `claude` has no command
   line to message a session. (Max's triage 2026-09-28; comparing every row, executor's decision, field fixes
   part 2, 2026-09-28)
95. **The orchestrator keeps one background `flotilla watch --wait 3600`** (Max): it returns when an attention item
   appears that was not there at its start — a question, a dropped ball, a break, orphaned work — and replaces the
   orchestrator's `permit next --wait`. (Max's triage 2026-09-28; one wait for all, executor's decision, field
   fixes part 2, 2026-09-28)
96. **A row waiting on "the person" is an attention item** for `flotilla watch` and the orchestrator's `watch --wait`,
   so a question a guarded session could not ask is carried even when its SendMessage was lost or no orchestrator
   lived; the ask route says what to do with no row (write the question as the last message) and names
   `flotilla status`. (executor's decision after the branch review and the live probe, field fixes part 2,
   2026-09-29)
97. **An attention item is the same item across polls by whom it is about, not by its text**: the text carries the
   census word, which flickers (F10). `watch --wait` forgets what went away, so a ball dropped again is news.
   (executor's decision after the branch review, field fixes part 2, 2026-09-29)
98. **The way forward (decision 93) follows move refusals only**; an error that is not a move refused (not
   onboarded, a damaged log, a ledger version) says flotilla cannot run here. A letter that cannot be computed
   after a recorded move is a note, never a refusal. (executor's decision after the branch review, field fixes
   part 2, 2026-09-29)
99. **The sessions of this project** (Max: "live sessions of this project are the holders of this ledger's post
   rows"): a live session named in this ledger's open rows — owner or reader, post rows included — or working in
   this repository's main checkout or a worktree of it. A post-named mover resolves among them only; the greeting,
   `watch`, the ask guard and the letters use them (F9, F20). (Max's triage 2026-09-28; the working-directory and
   row-owner widening, executor's decision, field fixes part 3, 2026-09-29)
100. **A gone seat is one quiet line, not a mover alarm** (Max): a `reserved` row whose session is not alive is the
   deviation `seat_empty`, and the fleet view gathers every empty seat into one item naming them and
   `flotilla fleet down` (F8, F27). (Max's triage 2026-09-28)
101. **Spawn raises the orchestrator last**, so its session-start report sees every seat already raised (F8).
   (executor's decision, field fixes part 3, 2026-09-29)
102. **`watch` confirms a dropped ball with a second census sample** a few seconds on before it reports or wakes;
   the hooks do not sample twice (F10). (executor's decision, field fixes part 3, 2026-09-29)
103. **`flotilla fleet down` retires every seat of this ledger but the caller's own** (Max), as `retire` does; a
   census that cannot be asked refuses the whole command; `/flotilla:down` is the person's command and asks first
   (F27). (Max's triage 2026-09-28; skipping the caller's seat, executor's decision, field fixes part 3, 2026-09-29)
104. **Run summaries carry no terminal escapes**: stripped when recorded and again when shown (F19). (executor's
   decision, field fixes part 3, 2026-09-29)
105. **A session is of this project by the repository it works in, not by a path prefix**: the nearest directory
   holding `.git` above its working directory must be the main checkout or a worktree of this repository, so a
   repository nested inside the checkout is not this one; a relative working directory is not inside. The permission
   broker's "a live orchestrator exists" is asked of this project's sessions too. (executor's decision after the
   branch review, field fixes part 3, 2026-09-29)
106. **`fleet down` refuses inside a Claude Code session it cannot identify**, since stopping the session that
   runs it would cut the command short; a refused seat of any kind never stops the others. (executor's decision
   after the branch review, field fixes part 3, 2026-09-29)
107. **The flotilla repository is its own marketplace** (`.claude-plugin/marketplace.json`, one plugin sourced from
   the root); the README gives the two install commands. With a marketplace manifest present,
   `claude plugin validate .` checks only the marketplace, so CI validates `plugin.json` separately (F1).
   (Max's triage 2026-09-28; the CI step, executor's decision, field fixes part 4, 2026-09-29)
108. **`doctor` and `spawn` check the plugin is enabled and the main checkout trusted** — asked of
   `claude plugin list --json` run there and of `~/.claude.json`; spawn refuses on a known gap and warns on an
   unknown; hooks skip both checks (F2, F3). (Max's triage 2026-09-28)
109. **Every project is asked about the judge**; a surface chosen means `judge.required = true`, and a project with
   no deployment found gets trunk on origin as its deployed build (F4). (Max's triage 2026-09-28; judge required,
   executor's decision, field fixes part 4, 2026-09-29)
110. **`fleet.model` may name a model** that every post without its own runs on (F5). (Max's triage 2026-09-28)
111. **A typed tier is named after its command** — the runner it calls, else its first word; clashes get `-2`
   (F6). (Max's triage 2026-09-28)
112. **`spawn --dry-run` says where each post's numbering continues from** (F7). (Max's triage 2026-09-28)
113. **Every hook records when it last ran for the session**, and `flotilla guard status` shows it per live session
   of this project; the Bash guard records only when a command reached the guards (F11). (Max's triage
   2026-09-28; the per-session trace, executor's decision, field fixes part 4, 2026-09-29)
114. **`doctor` checks the main checkout spawn launches from**, not the directory it runs in (a fleet worktree is
   never trusted itself); the fix it names installs from the marketplace flotilla is already listed from, or adds
   flotilla's own first. A local flow is not asked about a judge (it never ships, so a required judge would leave
   every row unclosable), and spawn warns when the profile requires a judge the fleet will not hold. (executor's
   decision after the branch review, field fixes part 4, 2026-09-29)
115. **A hook's trace is one file per session and event**, replaced whole, so hooks of one session firing at once
   never overwrite each other's record. (executor's decision after the branch review, field fixes part 4,
   2026-09-29)
116. **`tree switch` takes an open row of yours that has no branch** (a fix row a judge filed): it cuts the branch
   from trunk in the home tree and records the base, making no new row (G6). (executor's decision, field fixes
   part 5, 2026-09-29)
117. **A fix settled by a delivered row is delivered**: a fix row `released` with `settled_by` naming a delivered
   row stands, for its broken row, as that row — the walked build must carry its merge, and `broken_unfixed` does
   not fire (G9, G10). (executor's decision, field fixes part 5, 2026-09-29)
118. **A session the census lists is running, whatever its state**: a background session whose turn is done is
   still a process, and `retire` / `fleet down` stop it (G12). (executor's decision, field fixes part 5,
   2026-09-29)
119. **The posts say four more things**: announce to the fleet's seats, not to every session on the machine (G1);
   an orchestrator splitting dependent work has the later row claimed with `--requires` (G3); a change to what the
   person asked is put to the person before it is built (G11); the sender lands each branch at its own merge commit
   (G5). The README says what `--scope project` commits (S7). (executor's decision, field fixes part 5,
   2026-09-29)
120. **A broken row is nobody's move until a fix for it is delivered** (directly or by settlement): then the mover
   becomes the judge and the letter wakes it to walk again; `tree switch` never moves a row past its claim back to
   `claimed`; a seat whose turn is done holds its post at spawn as it does at retire. (executor's decision after the
   branch review, field fixes part 5, 2026-09-29)
121. **`broke` on a part is legal** (H38): the part gate ("walk a path, not a part") exists so a part is not
   accepted as the whole; a defect seen on a part is a defect whatever ships later. `walked` keeps the gate.
   (planner's decision, field fixes part 6 track B, 2026-09-30)
122. **A fix branch drops the broken branch's type prefix** (H26a): `feat/stage-2-two-suns` becomes
   `fix/stage-2-two-suns`, not `fix/feat/stage-2-two-suns`; a branch with no `/` gets `fix/<branch>` as before, and a
   name already taken by a row is numbered. (planner's decision, field fixes part 6 track B, 2026-09-30)
123. **The judge may take a `broke` back** with `flotilla work unbroke <branch> --why "<why>"` (H27): legal on a
   shipped, broken row with no open fix row; it clears `broken`, keeps the history, and the row is the judge's to
   walk again. An open fix row must be released first, and the move says so. (planner's decision, field fixes part 6 track B, 2026-09-30)
124. **`whose move` names what a broken row waits on** (H26b): "the fix `fix/x` (main session 6)" when a fix row is
   open, "nobody: it broke and no fix row is open" when none is. (planner's decision, field fixes part 6 track B, 2026-09-30)
125. **Three findings stop firing falsely** (H5, H4, H32a): `unread_in_trunk` is not raised for a branch whose tip is
   on trunk's first-parent line (a fresh branch moved to trunk, not work that reached trunk unread; a commit made
   there is `direct_commit`'s); `finished_not_handed` needs a commit of the row's own, so a tip reachable from trunk
   or from another open row's branch carries nothing to hand; `after_close` is not raised when another open row's
   branch contains the moved tip, because the work went on in that row. (planner's decision, field fixes part 6 track B, 2026-09-30)
126. **`watch` says when a wait's reason has moved on, and raises a session waiting on the person** (H30, H42, H25b):
   a wait whose note names a row (`r32`, a whole token) or a branch that moved since the wait was recorded reads
   "(since then: r32 shipped)"; a live fleet session whose census status is `waiting` and whose question is not in
   the broker queue is a person item, "<name> waits on the person (census: waiting); answer it in its session";
   the empty-seat line prints no age, because the ledger knows when a seat was reserved, not when its session left.
   (planner's decision, field fixes part 6 track B, 2026-09-30)
127. **The lane summary skips a wrapper's error line** (H32b): when no line counts tests, the summary is the last
   line that is not a shell's own error (`bash: ...`, `sh: ...`, `zsh: ...`, `kill: ...`); a run whose only lines are
   such errors keeps its last line. (planner's decision, field fixes part 6 track B, 2026-09-30)
128. **The judge and main posts say four more things** (H16, H27, H39, H17): the judge asks for a way to start near
   what it must see (a position, a time, a weather by URL) rather than walking a different path; starts its own
   preview on a port of its own (`--port <free> --strictPort`) and reads the page's revision stamp before walking;
   and records what the test browser cannot perceive (sound, frame rate) for the person through the orchestrator,
   never walked as if heard. The main post claims a row that wires in a part another row builds with
   `--requires <that branch>`. (planner's decision, field fixes part 6 track B, 2026-09-30)
129. **"A commit of the row's own" is measured against the rows claimed before it** (narrows decision 125's "another
   open row's branch"): for `finished_not_handed`, a row's tip is compared with trunk, with the current tips of
   every open row claimed before it (wherever the part's author has moved them since), and with the tip of a row
   claimed after it only when that tip lies strictly under this one. Subtracting every other open row's tip would
   call the row that made the commit unfinished as soon as a branch is stacked on it; subtracting only the tips
   under this one would call the stacked row finished as soon as the part's author commits again. (executor's
   decision after the branch review, field fixes part 6 track B, 2026-09-30)

130. **A merge inside a reviewed row's read range is read**: batch accounting asks the read ranges before judging
   a merge by whether it is clean, so a conflict the author resolved by hand and the reader read lands (H20).
   (planner's decision, field fixes part 6 track A, 2026-09-30)
131. **`land` counts what the landed merge brought over its first parent, whatever the main checkout holds**; on
   the local trunk it adds what the push will carry. A pulled main checkout can no longer turn the unread-work check
   off (H29). (planner's decision, field fixes part 6 track A, 2026-09-30)
132. **Batch work is vouched for by its reader**: `vouch <branch> --commit <sha>` is the reader's own annotation
   move and counts the commit as read wherever it lies; the sender asks for it and never writes a reader's name
   itself, which the auto-mode classifier rightly reads as self-approval (H21, H24). (planner's decision, field
   fixes part 6 track A, 2026-09-30)
133. **The sender returns an accepted or queued row to its author** with `return --why`, `-> fixing`, the verdict
   cleared: a row that no longer merges has a legal way back instead of a release and a new claim (H26c).
   (planner's decision, field fixes part 6 track A, 2026-09-30)
134. **`moved` needs the reader's agreement only while that reader is alive**: when the census says the reader is
   gone, the moved tip goes back to `handed` with no reader, for the orchestrator to assign; a census that cannot
   be asked keeps the refusal (H28). (planner's decision, field fixes part 6 track A, 2026-09-30)
135. **`accept` refuses a tip that does not merge with trunk** (`git merge-tree --write-tree` exit 1): the
   reviewer returns it with `fix`, so the sender never has to resolve a conflict nobody reads; a git that cannot
   say does not refuse (H43). (planner's decision, field fixes part 6 track A, 2026-09-30)
136. **The version moves with every release, and CI says when it did not**: `.claude-plugin/plugin.json` holds the
   version, `pyproject.toml` and `flotilla/__init__.py` carry the same string (a test pins it), and
   `tools/check_version.py`, run by CI on pushes to `main`, fails when the plugin's code changed since the tag
   `v<version>` or when that tag is missing. Claude Code caches a plugin by its version, so code shipped under an
   unmoved one replaces every project's copy at once (T2). This part's release is 0.2.0, bumped and tagged by the
   coordinator at merge. (planner's decision, field fixes part 7 track B, 2026-09-30)
137. **A long run started outside the lane is warned about, not refused**: the Bash guard's `lane` rule, on unless
   the profile says `lane = false`, warns when a segment runs a tier command or a lane run pattern (by its program,
   behind launchers such as `uv run` and `npx`) outside `flotilla lane run` / `flotilla receipt run`, and names the
   `lane run` form. A warning, because the lane is not a lock and refusing an ordinary test run would cost more than
   the wait (H19). (planner's decision, field fixes part 7 track B, 2026-09-30)
138. **A headless browser is a run**: `chrome-headless-shell` and `headless_shell` join the lane's default run
   patterns; a full `chrome` does not, because a browser tool server keeps one alive for hours (H22). (planner's decision, field fixes part 7 track B, 2026-09-30)
139. **The lane asks memory**: `MemAvailable` in `/proc/meminfo` under `[lane] memory_floor_mb` (default 1500, or a
   quarter of `MemTotal` where that is less, so a small machine's ordinary state does not close it; 0 turns it
   off) holds the lane outright, like CI on this machine. Where it cannot be read (macOS) the answer is "not
   asked" and does not block, because an unknown that waiting cannot change would close the lane for ever (H41). (planner's decision, field fixes part 7 track B, 2026-09-30)
140. **`--after` is an ordering-only link**: `claim`, `tree cut` and `tree switch` take `--after <row or branch>`,
   stored in the row field `after`; `queue` waits for it as for `requires`, an unknown name is refused at claim, and
   the part gate reads `requires` only, so a measurement or tool row never holds the walk of the row it records
   (H44). (planner's decision, field fixes part 7 track B, 2026-09-30)
141. **Part 6's review minors**: a vouch counts only while its row is open or delivered (a released or returned
   row's vouch accounts no commit); `return` clears `vouched`, and on a row with a PR says the PR stays open and
   names it; `unbroke`'s refusal of a delivered fix row says "release or close it"; `waits_on` stops naming a fix
   that has arrived. (planner's decision, field fixes part 7 track B, 2026-09-30)

146. **A background session with no process behind it is gone**: the census drops an entry that reports no status
   and has no pid or a pid naming no running process, so a seat the daemon retired is seen empty everywhere (H48). A
   session that reports a status is kept whatever its pid looks like from here: a pid this process cannot see
   proves nothing.
   (planner's decision, field fixes part 7 track A, 2026-09-30)
147. **Releasing a seat frees its tree** — by `work release` or `retire`: a clean seat worktree whose session is gone
   is detached, so the branch it held can be taken up in another tree. Never the main checkout, never a path git
   resolves to another repository, never a live owner's tree, never a dirty one; the release says which (H36). (planner's decision, field fixes part 7
   track A, 2026-09-30)
148. **watch tells the orchestrator about a seat with no work** past `watch.idle_seat_minutes` (default 60), for
   posts that may claim (H45). (planner's decision, field fixes part 7 track A, 2026-09-30)
149. **watch tells the orchestrator when the queue has drained** — no open work once some has been done — and the
   orchestrator closes the task: the person checks the result, then is offered `fleet down` (H12, H46). (planner's
   decision, field fixes part 7 track A, 2026-09-30)
150. **spawn refuses to raise a seat under a memory floor** (`fleet.memory_floor_mb`, default 2000; `--anyway`
   overrides; skipped where memory cannot be read), and the orchestrator post says to raise readers, the sender and
   the judge before another implementer (H47, H9). (planner's decision, field fixes part 7 track A, 2026-09-30)
151. **fleet down names what stays alive**: census sessions working in the project or its worktrees that are not
   seats it retired, each with the command that stops it (H12). (planner's decision, field fixes part 7 track A,
   2026-09-30)

152. **flotilla is MIT-licensed**, so anyone may use it as they like; the repository home is `TensusDS/flotilla`.
   (the person's decision, 2026-09-30)

153. **A seat's plugin set is narrowed by `--settings` with `enabledPlugins: false`**, chosen by measurement on a real
   `claude --bg` session (Claude Code 2.1.286, `tools/probe_seat_plugins.py`, 2026-09-30, from `/home/max/workspace`
   with 17 enabled plugins that bring MCP servers). Processes under the session's pid, once its tree held still:

   | variant | plugin MCP processes | their RSS (MB) | other descendants | their RSS (MB) |
   |---|---|---|---|---|
   | a: no extra flags | 14 | 1167 | 2 | 118 |
   | b: `--settings` turning off every enabled MCP plugin | 0 | 0 | 2 | 118 |
   | c: `--strict-mcp-config` alone | 0 | 0 | 0 | 0 |

   Both mechanisms stop plugin MCP servers; (b) is taken because it keeps what a post declares by leaving that
   plugin out of the map, while (c) keeps nothing unless flotilla rebuilds each kept server's definition for
   `--mcp-config`. The "other descendants" are a server from the person's own MCP configuration, which (b) leaves
   running: the person chose it for every session. Settings given by `--settings` sit above user, project and
   local files, so a plugin enabled in the main checkout's `.claude/settings.local.json` is turned off too (H49).
   (planner's decision, field fixes part 8 track B, 2026-10-01)
154. **A post keeps only the plugins it declares, among those that bring MCP servers**: the post key `plugins:` (a
   list of plugin ids, default empty). Every other enabled plugin with MCP servers is turned off for its seats;
   plugins without MCP servers (skills, commands, hooks only) are left alone, because they cost no resident
   process and turning off a hook changes behaviour the person chose. flotilla itself is never turned off (H49).
   (planner's decision, field fixes part 8 track B, 2026-10-01)
155. **Which plugins bring MCP servers is asked of `claude plugin list --json` in the main checkout**, once per
   spawn: its `enabled` holds for the directory it runs in, whatever scope enabled the plugin, so a plugin enabled
   only in the project's own settings is turned off too. A plugin brings servers when the listing names them or
   its `.mcp.json` or `plugin.json` `mcpServers` does; one whose install path is missing is left enabled (doubt
   keeps behaviour). A listing that fails or times out narrows nothing and warns "plugin set not narrowed" — never
   a refused spawn; a kept plugin that is not installed, or not enabled there, is named in a warning. The map rides
   as one argv element made by `json.dumps`. (planner's decision, field fixes part 8 track B, 2026-10-01)
156. **The judge template keeps `playwright@claude-plugins-official`; every other template keeps none.** A project
   may edit its posts; a judge at the current template without it has no browser MCP, and the dry run shows that.
   A project post that predates the key (no `plugins:` written, `template_version` older than the shipped
   template's) keeps what the shipped template keeps, and spawn says so, so an upgrade does not silently take an
   onboarded judge's browser (final review, 2026-10-01).
   (planner's decision, field fixes part 8 track B, 2026-10-01)
157. **The dry run names, per seat, the plugins it will turn off** ("turns off: <ids>", or "unknown" when the
   listing cannot be asked); and **a session in a seat's tree that holds no post** — a hook's headless `claude`, a
   session opened there by hand — is listed by `flotilla fleet` and `watch` as "not a fleet session (started in
   <tree>)" and counted nowhere a seat is counted (H7). (planner's decision, field fixes part 8 track B, 2026-10-01)
158. **Part 7's review minors**: the lane guard ignores a line inside a quoted string (a commit message's body),
   a version or help question and `playwright install`; `lane.memory_floor_mb`, `fleet.memory_floor_mb` and the
   new `fleet.seat_cost_mb` fall back to their defaults and say so when they are not whole numbers; spawn's memory
   floor counts every seat it is about to raise at `fleet.seat_cost_mb` (default 800, a seat with its servers in
   the twosuns run) rather than free memory once; `check_version` without git prints a message.
   (planner's decision, field fixes part 8 track B, 2026-10-01)
159. **A fleet session may raise a helper for a piece of its own work** (`flotilla helper raise --for <branch>
   --task`): the owner of an open row in `claimed` or `fixing`; at most `fleet.helpers_per_seat` (default 2) live
   helpers per row; the memory floor applies (H8). (planner's decision, field fixes part 8 track A, 2026-10-01)
160. **A helper works in its own tree**, cut from the tip of the branch it helps, on its seat branch
   `fleet/helper-<n>`; its seat row names the parent row (`helper_of`) — so edits never race (the person's decision,
   2026-09-30).
161. **A helper never delivers**: its post may reserve, release and wait only; the parent merges the helper's branch
   and hands the whole over, so the reviewer reads the helper's commits in the parent's range. (planner's decision,
   field fixes part 8 track A, 2026-10-01)
162. **A helper leaves a record**: `flotilla helper done --summary` records its tip and summary as the release of its
   seat and prints a letter for the parent (merge, then retire). (planner's decision, field fixes part 8 track A,
   2026-10-01)
163. **watch tells the orchestrator about a finished helper still running and about an orphaned helper** (its
   parent row has no live owner). (planner's decision, field fixes part 8 track A, 2026-10-01)
164. **Reading and research stay with in-session subagents**; a helper session is for work that edits files
   (H40). (planner's decision, field fixes part 8 track A, 2026-10-01)
165. **Retire stops what the session left running detached in its tree** (H50): after the session has left the
   census, the processes whose working directory is inside the seat's tree and whose parent is pid 1 are sent
   SIGTERM, each with every process under it. An orphan is the mark, not the tree alone: a person's shell or
   anything a live process still owns has a living parent and is left. (planner's decision, release 0.3.1,
   2026-10-01)
166. **An orphan that serves a terminal or a live session is spared**: a subtree holding a process with a controlling
   terminal (a `tmux` server started in the tree), a live census session, or the process running the retire is left
   running. A terminal alone does not mark a person's process, since background sessions run under a pty host; that
   is why the test is on the subtree of an orphan. An orphan that a systemd service runs (its cgroup is a `.service`
   unit: a deployment's server, a CI runner) or that belongs to another user is never a leftover, and a process is
   signalled only while its start time is still the one read at the scan, so a reused pid is left alone.
   (planner's decision, release 0.3.1, 2026-10-01)
167. **Where there is no procfs the leftovers are not looked for, and retire says so**; it never guesses through
   `ps`. Nor are they looked for in a tree that is not a seat's own worktree: a relative path, the main checkout or
   a directory git does not name as its own toplevel (the check `free_seat_tree` already made, now one function). A detached process of another session that worked in this seat's tree is stopped with it: a tree is its
   seat's home, and a session that works in another's tree starts its servers there at its own risk. (planner's
   decision, release 0.3.1, 2026-10-01)
168. **Retire also looks at the job directory of the session it stops** (H50, second form): `jobs/<id>/` under the
   Claude Code configuration directory (`CLAUDE_CONFIG_DIR`, else `~/.claude`), with the same rules as the tree. The
   id comes from the census at the moment retire stops the session; a session already gone left no id behind, so
   its job directory is not looked at, and retire says so. The directory's place is Claude Code's, not a documented
   interface: where it does not exist, nothing is assumed, and retire says it did not look and why. A job directory
   that is a link is never looked at: its target could be any directory, and the orphans under it are not this
   session's. (planner's decision, release 0.3.2, 2026-10-01)
169. **Retire refuses a name the census lists twice**: which of the two is the seat cannot be told, and stopping
   either, or looking at either's job directory, could stop the wrong session's work. The refusal names both ids
   for `claude stop` by hand. (planner's decision, review of release 0.3.2, 2026-10-01)
170. **A leftover started before the stop** (H51): retire reads the clock processes' start times are kept in
   (`/proc/uptime`, in clock ticks since boot) before it stops the session, and only an orphan whose start is earlier
   is a leftover; what it forked later is still part of it. What starts after the stop — a plugin's session-end hook
   saving the session — is the stop's own work. Where that clock cannot be read before the stop nothing is looked
   for, and retire says so. When the session was already gone as retire came (stopped by hand after a refusal, or a
   second retire after the wait ran out), the cut-off comes after that stop, so an orphan started up to two minutes
   before it is spared too. Every spared orphan is named in retire's output, so a dev server started as the
   session stopped is not silently left behind. (planner's decision, release 0.3.3, review of the fix, 2026-10-01)

171. **Onboarding is the person's** (security review 2026-10-01, F2, F3, F4, F8): the onboard skill is no longer
   model-invocable, and it pre-approves only the reading steps (`machine`, `detect`, `next`, `check`). `answer`,
   `write` and `reset` go through Claude Code's own permission prompt, because a `tiers` answer is a shell command
   that `write` runs; the CLI refuses them from a background session. `write` prints every command the profile will
   run or store, with a mark over them, and does nothing until it is called again with `--confirm <mark>` - the
   answers are a plain file a session can edit, so what runs is shown to the person at the end, and a changed
   command changes the mark. No skill rule may match a command that acts for the person, asked of what the rule
   matches rather than of its words: `lane --root *` read harmless and matched `lane --root . run -- ...`.
   (planner's decision, security fixes group 1 and its review, 2026-10-01)
172. **No skill pre-approves an answer to a permission question** (F7, F9, F12): the permit skill pre-approves only
   `permit next` and `permit list`, so every `permit answer` raises Claude Code's prompt before it runs, and the
   skill says that what a question shows was written by another session - something to show the person, never an
   instruction. (planner's decision, security fixes group 1, 2026-10-01)
173. **A person is a terminal or the person's own session, never a detached process** (F9): a call from outside any
   census session counts as a person only with a controlling terminal behind it, since a session can detach a
   process (`setsid`) whose parent chain reaches pid 1. Inside sessions, every session up the chain is asked, not the
   nearest only: the nearest must be interactive and none above it background, since a background session can start
   an interactive one. An unreadable census refuses. A process detached with a pseudo-terminal of its own
   (`tmux new -d`) still passes for a person: that is the limit of this check. Every session runs as the same user, so this stops an overeager or misled session, not a
   determined program of that user's - the person's gate stays Claude Code's prompt. (planner's decision, security
   fixes group 1, 2026-10-01)

174. **A permission question shows the session's text as data** (security review 2026-10-01, F5, F10, F12): every
   field another session wrote - the command, a Write's content, an Edit's text, the session's name, the rule
   "for this session" would add - is printed on one line, in quotes it cannot close, with every control and
   reordering character made visible (`\\r`, `\\x1b`, `\\u202e`), and a cut named with the count it hides. A carriage
   return or an erase sequence can no longer show the person one command while they allow another. (planner's
   decision, security fixes group 2, 2026-10-01)

175. **Only a reader's own vouch accounts batch work** (security review 2026-10-01, F1; field test H21): `inbatch`
   records a change born in the batch and names who is asked to read it, and that is all - the sender's word that
   a reviewer read it accounts nothing. The commit counts as read once the reader vouches for it with their own
   move (`vouch` now finds an inbatch row by its label and leaves it as it was). In twosuns the auto-mode
   classifier refused `inbatch --read-by` as a bypass on 2026-10-01: it was right. (planner's decision, security
   fixes group 3, 2026-10-01)
176. **A change is recognised by a fingerprint that keeps whitespace** (F15): `git patch-id --verbatim` replaces
   `--stable`, which drops whitespace - in Python a squash that moved a call into an `if` carried the fingerprint of
   the reviewed change. Both places that take a fingerprint use the same mode; a mix of the two matches nothing, so
   it fails toward "not accounted". (planner's decision, security fixes group 3, 2026-10-01)

177. **Another session's text is data wherever it reaches a person or a session** (review of the group 2 fix):
   `visible()` escapes by Unicode category - controls, format characters (bidi, zero-width, tags), line and
   paragraph separators, private use, unassigned, spaces other than " ", and letters that render blank - rather than
   by a list of code points. Every line the hook block and `watch` print passes through it, so a newline in a note or
   a why cannot forge a heading; a letter shows the reader's why the same way; a free-text onboarding answer with
   such a character is refused, since it is printed back before it runs. (planner's decision, security fixes,
   2026-10-01)

178. **What the person approves is named by a mark over exactly what they saw** (review of the group 1 and 2
   fixes): `onboard write` shows the whole profile it will write, not only its commands, and its mark covers that
   whole text - switching background sessions to auto mode in the answers file changes no command but changes the
   mark. An allow of a permission question carries the question's mark (tool, input and suggested rules), which
   `permit next` prints: an allow without it, or with another question's, is refused, and the hook allows only when
   the mark matches the call it holds, so a question file changed after the hook wrote it cannot carry the person's
   yes to another call. A deny needs no mark. A mark proves that nothing changed since it was printed, not that a
   person read it: in auto mode, or after "don't ask again" on the prompt, the model makes both calls - the person's
   gate stays Claude Code's prompt in ask mode. (planner's decision, security fixes, 2026-10-01)

179. **Smaller holes from the same review, and what stays open** (2026-10-01): every session up the caller's chain
   must be interactive - a session of any other or unknown kind anywhere above refuses, not only a background one;
   the directories a "for this session" rule would add and the "answered ... is told" line are shown as data. Left
   as they are, with their reason: a skill rule with a trailing `*` (`status*`, `fleet*`) is not narrowed against a
   chained command (`status; permit answer ...`), because Claude Code splits compound commands before matching and
   approves each part on its own; the census keeps an interactive session's pid after it ends, so a detached process
   that lands on a reused pid could pass for that session - it needs a pid race and a pre-approved wrapper first.
   (planner's decision, security fixes, 2026-10-01)

180. **A guard never runs what it judges, and never lets the judged tree run a program** (security review
   2026-10-01, F11, F19): the revert guard showed what a forced `git clean` would remove by running `git clean -n`
   with the command's own arguments, and git takes `--forc` for `--force` and lets `--no-dry-run` undo `-n` - so the
   guard deleted the files it was judging, before anyone allowed the command. It now reads a forced clean by an
   allow-list (untracked, with ignored, or only ignored; exclusions; paths), lists the files with `git ls-files
   --others`, which changes nothing, and reads git's abbreviations and `--no-dry-run` as git does. Its git calls run
   with `core.fsmonitor` off and no optional locks, since the tree's own config could otherwise run a program while
   the guard asks; a clean or smudge filter on a racy index entry stays possible and needs that tree set up first.
   (planner's decision, security fixes, 2026-10-01)

181. **A post narrows the permission mode, never widens it; trunk is origin's default branch** (security review
   2026-10-01, F6): a post's `permission_mode` came from a file a session can edit and went past the profile's ask,
   rules or auto - `bypassPermissions` included. Now a post may only narrow the profile's mode (plan, manual,
   dontAsk, acceptEdits, auto in that order), and bypassPermissions is never launched. The tree's own profile names
   trunk, and the rules - posts, modes, prompts - are read from it; when origin has a default branch and the tree
   names another, the rules are refused, so a seat cannot point them at a branch of its own. Without an origin
   default the check cannot be made: a session that rewrites the main checkout's profile and commits it to trunk is
   beyond what flotilla can stop in one user's account. (planner's decision, security fixes, 2026-10-01)

182. **The answer and the trunk check, tightened after their review** (2026-10-01): a question's mark also covers
   the asking session and the directory the call runs in, and the directory is shown - the same command from
   another tree is another question; no field of a question is cut any more, since the mark covers all of it. The
   trunk check accepts a trunk other than origin's default branch when the profile committed on that default
   branch names it (a project on `develop`), and its refusal says how to fix a moved default. Its ceiling stays as
   it is: `refs/remotes/origin/HEAD` and `origin/<trunk>` are local refs a session can repoint, so the check binds a
   name an honest tree carries, not a ref no session can move. A post may also ask for `default`, the same width as
   `manual`. (planner's decision, security fixes, 2026-10-01)

183. **Where a person authorizes merges, the ledger holds it** (security review 2026-10-01, F24; the person's
   decision): `flow.merge_authorized_by = human` was held only by the sender post's text, and a yes arrived as a
   message anyone could write. The person's yes is now their own move, `flotilla work approve <branch>`, recorded
   at the revision the reader accepted; only a person's session or terminal makes it (decision 173), and `queue`
   refuses until the row carries an approval of its current revision - a moved and re-read branch needs another.
   The orchestrator shows the person the batch and the command; it never approves. (the person's decision,
   2026-10-01)
185. **Every door to trunk asks for the person's approval, not only queue** (review of decision 183): where a person
   authorizes merges, the batch accounting counts a row's commits only when the person approved it at the revision
   it carries, so `land` refuses unapproved work riding in a batch; the push guard and the pre-push hook ask the
   ledger about every revision going to trunk (`git push`, `gh pr merge`), so work cannot be pushed first and
   recorded afterwards; `offledger` is the person's move there; work born in the batch is approved by its label;
   work in a project that skips review is approved at its tip. An `approved` field counts only from the person's
   approve move, not from any line appended to the ledger. What stays: the ledger is a file every session of the
   same user can write, so a session that forges a whole approve event, or the model in the person's own
   interactive session, can still pass for the person - the last gate is Claude Code's permission prompt for
   `flotilla work approve` in the person's session, which nobody should answer "don't ask again".
   (planner's decision, security fixes, 2026-10-01)

184. **Lower findings of the security review, group C** (2026-10-01): a seat tree must be one of this project's
   worktrees - a git toplevel of another repository is not one, so a forged row cannot have retire signal another
   project's processes or detach its branch (F13); other processes' command lines reach the lane's answer as data
   (F14); `tree cut` checks the path again under the ledger lock, and a failed `worktree add` removes only the
   branch it made, never a tree on that path, which may be another session's (F17). (planner's decision, security
   fixes, 2026-10-01)

186. **What flotilla keeps on disk is its user's alone, and keeps no secret it does not need** (security review
   2026-10-01, F16, F23, F25): the state directory - ledger, receipts, permission questions, override records - is
   made 0700 at every command's start, and an existing one is closed down; that closes everything under it,
   whatever each file's own mode. An override record no longer keeps a NAME=value or a URL's password from the
   command it names, and a closed permission question's files are deleted a day after its answer, since a question
   carries whatever the call carried. (planner's decision, security fixes, 2026-10-01)

187. **The push guard judges a door by the project it acts on, and pins a merge to what it checked** (security
   review 2026-10-01, F18, F20, F21, F22): `gh pr merge` passes only with `--match-head-commit` naming the head whose
   receipt was checked, so neither a later push to the PR nor `--auto` merges another revision; the guard runs for
   every door and asks the door's own project whether its guard is on, including from a session standing in no
   project at all; a gh door naming its repository (`-R`, `--repo`, `GH_REPO`) from outside every project is
   refused, since whose receipts it needs cannot be told. (planner's decision, security fixes, 2026-10-01)

188. **The person's approval has no override, and what goes to trunk is measured against origin itself** (review
   of the low-finding fixes, 2026-10-01): `FLOTILLA_GATE_OVERRIDE` waives a missing receipt, never a missing
   approval - a session cannot waive what only the person gives; the push guard asks origin for trunk's revision
   (`git ls-remote`) and pre-push takes it from what git says origin has, since a session can repoint the local
   `origin/<trunk>` and make nothing look outgoing; `--all`, `--mirror` and pattern refspecs are asked as pushes
   that may carry trunk; a repeated `--match-head-commit` is refused (gh takes the last); a push through `GIT_DIR`
   or `GIT_WORK_TREE` names no tree and is refused; a vouch made after the person's approve does not ride under it.
   Left open, named: `gh api` calls that merge or move refs are no door the guard reads; a push with no refspec is
   read by flotilla's own reading of push defaults, and only the pre-push hook, where installed, sees what git
   actually sends. (planner's decision, security fixes, 2026-10-01)

189. **`--as` without the census's word is a person's word or nobody's** (review of the README, 2026-10-01): a move
   named with `--as` was recorded under that name whenever the census could not place the caller - outside every
   session, the census down, or `FLOTILLA_NO_CENSUS` set - so a process a session detached could record a reading in
   a reviewer's name, the F9 hole in the ledger. It is now taken only with a terminal behind it (decision 173); a
   session names itself through the census. The orchestrator post no longer tells the person to approve with `!` in
   its prompt: that runs inside the orchestrator's background session and is refused. (planner's decision, security
   fixes, 2026-10-01)

190. **Quick onboarding: the recommended answers, one yes, published by onboarding itself** (worldcore field test,
   the person's request, 2026-10-01: "the settings stay for those who want to tune; the rest just onboard"). The
   onboard skill asks first: quick or custom. Quick (`flotilla onboard quick`) records the recommended answer to every
   question that applies - what detection found (trunk, test commands, GitHub CI), and otherwise every branch
   reviewed, every permission asked of the person, every guard on, one model; flow is pull requests through a sender
   where GitHub Actions workflows were found and a direct push otherwise, and a remote without them gets "no CI", not
   an empty gate command (W5). The
   person sees the short list and every shell command, and says yes once; `write --confirm` is unchanged. Then
   `flotilla onboard publish` commits `.flotilla/` (and `.claude/settings.json` when it changed) on trunk, runs the
   tiers over that commit and pushes it - so onboarding no longer ends at "commit" while sessions read origin's trunk
   (W5). It refuses a tree with the person's own uncommitted work and pushes nothing on a red tier; a trunk that takes
   only pull requests is named, not worked around. `write` ends by naming `publish`. The suggested composition always
   carries an orchestrator, a sender and a main session (W2), and a model answer must be a model name, not a sentence
   (W3). (planner's decision, 2026-10-01)

191. **The onboarding session may lead the fleet** (the person's request, 2026-10-01). Onboarding ends by offering to
   raise the fleet: this session leads it (recommended), a background orchestrator, or not now. To lead, the person
   types `/rename orchestrator N` - only a person can rename an interactive session, and the census then names it;
   `flotilla spawn --lead` issues that name from the names journal first, so it is never issued again - and
   `flotilla spawn --fill` raises the rest of `[fleet] default`: only the seats nobody holds, so not a second
   orchestrator. The session then follows the orchestrator post. An interactive orchestrator keeps Claude Code's own
   permission prompt for itself; the fleet's questions reach it as before. (planner's decision, 2026-10-01)

192. **Claude's tool call never approves, wherever it runs** (review of decision 191): with the person's own session
   leading the fleet, the person check passes that session's model - it runs in an interactive session - so the model
   holding the orchestrator post could approve its own batch, with only a permission prompt in the way (the limit
   decision 185 named). The two doors are told apart by the hook: a model's Bash call fires `PreToolUse`, a command
   the person types with `!` does not (measured on Claude Code 2.1.287 in a throwaway interactive session: a logging
   hook saw the model's `echo` and not the person's `!echo`). So the guard hook refuses `flotilla work approve` as a
   tool call, always: inside and outside a project, whatever guards the profile turns on, with no override. The person
   types `! <command>` in their own session or runs it in a terminal. What stays: a call the guard's text matcher
   cannot see (the shell ceiling: `sh -c`, `$( )`, a script) still meets only the permission prompt, and a `!` typed in
   a background orchestrator's prompt is still refused by the person check. (planner's decision, 2026-10-01)

193. **A sed range from the top to a pattern is not a line-number edit** (worldcore field test W4): `0,/re/` (GNU) and
   `1,/re/` run from the top of the file to the first match; line 1 is the top whatever was edited before, and the end
   is found by its text, so neither goes stale. Any other counted start (`3,/re/`) is still refused. (planner's
   decision, 2026-10-01)

194. **The install line declares the marketplace in the project** (worldcore field test W1): `claude plugin marketplace
   add` writes to the person's own settings by default, so the project's `.claude/settings.json` enabled a plugin
   from a marketplace nobody else knew, and the README's "commits a marketplace entry" was false. The README now adds
   the marketplace with `--scope project`, which writes `extraKnownMarketplaces` beside `enabledPlugins` (measured
   2026-10-01 in a throwaway repository). (planner's decision, 2026-10-01)

195. **Review of 0.5.0: publish carries only the profile; the approve guard reads the move, not the first word**
   (2026-10-01). `onboard publish` checks everything before it commits - the tree clean but for `.flotilla/` and
   `.claude/settings.json`, trunk equal to origin's (ahead: the unpushed commits are named and nothing is pushed;
   behind: pull first) - and commits with a pathspec, so neither a staged file of the person's nor an unpushed
   commit rides to trunk unseen (C1, I1); an ignored `.flotilla/` is refused, not claimed (M7). The approve guard
   finds the CLI anywhere in a segment - behind an interpreter or a wrapper, or as `-m flotilla.cli` - and reads the
   move as the first word after `work` that names one (a test holds its list equal to the CLI's), so a branch or a
   reason named `approve` passes; the hook's text prefilter also wakes on `flotilla`, since the word `approve` can be
   quoted apart (I2, M2). `--fill` counts only this project's live sessions (M3). A from-the-top sed range no longer
   hides a counted line in the block it opens (M4). Quick names the detected test commands a round left out (M6).
   Ruling: `onboard publish` stays a model-run step in the person's session, gated by the person check and by Claude
   Code's permission prompt (it is not pre-approved), like `answer` and `write`: onboarding is the person's own act
   through the skill, and a refused tool call would leave the skill unable to finish it. (planner's decision,
   2026-10-01)

## Open questions (for the foundation spec)

- What happens to `${CLAUDE_PLUGIN_DATA}` on plugin **uninstall** — the ledger must not vanish silently.
- Does a `--bg` session auto-create its own worktree before editing (per research) on top of the one flotilla cut?
  Verify with a live spawn.
- Name availability on PyPI/GitHub unchecked
  (not present in `claude-plugins-official` marketplace.json, 540 names, local copy).
- Read Gas Town and multiclaude (role-based prior art) before the posts spec.
