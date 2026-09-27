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

## Open questions (for the foundation spec)

- What happens to `${CLAUDE_PLUGIN_DATA}` on plugin **uninstall** — the ledger must not vanish silently.
- Does a `--bg` session auto-create its own worktree before editing (per research) on top of the one flotilla cut?
  Verify with a live spawn.
- License (MIT / Apache-2.0) and repo home (e.g. `TensusDS/flotilla`); name availability on PyPI/GitHub unchecked
  (not present in `claude-plugins-official` marketplace.json, 540 names, local copy).
- Read Gas Town and multiclaude (role-based prior art) before the posts spec.
