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

## Open questions (for the foundation spec)

- What happens to `${CLAUDE_PLUGIN_DATA}` on plugin **uninstall** — the ledger must not vanish silently.
- Does a `--bg` session auto-create its own worktree before editing (per research) on top of the one flotilla cut?
  Verify with a live spawn.
- License (MIT / Apache-2.0) and repo home (e.g. `TensusDS/flotilla`); name availability on PyPI/GitHub unchecked
  (not present in `claude-plugins-official` marketplace.json, 540 names, local copy).
- Read Gas Town and multiclaude (role-based prior art) before the posts spec.
