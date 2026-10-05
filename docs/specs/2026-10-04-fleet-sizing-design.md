# Fleet sizing - design

Date: 2026-10-04. Status: draft for the person's review. Research behind it:
`docs/research/2026-10-04-fleet-sizing-complexity.md` (complexity, churn, coupling, review cost) and the Claude Code
documentation of the status line input (`code.claude.com/docs/en/statusline`) and of background sessions
(`code.claude.com/docs/en/agent-view`).

## 1. What it is for

Today `flotilla spawn` raises the composition it is told (`-M 2 -r 1`, or the profile's `[fleet.default]`) and
refuses when memory is short. It never says how many seats would serve this project on this machine with this much
work, nor which posts. People raise too many (nine seats, five idle, one seat holding five rows - twosuns,
2026-10-03) or too few, and on usage credits a large fleet spends money fast: background sessions use quota
independently, "running ten agents in parallel uses quota roughly ten times as fast as running one" (agent-view).

**The person's scenario.** Max opens a project and types `/flotilla:spawn`. Before anything is raised he sees:

```
recommended: orchestrator 1 (this session), main 3, minor 1, reviewer 2, sender 1, judge 1
  authors 4 = min(independent areas 6, backlog 9, test runs that fit 4, disk 11) - limited by test runs:
    one full run takes 2.1 GB and 4 min; two fit beside the seats, so ~30 handovers an hour at most
  reviewers 2: 4 authors at ~1.5 handovers an hour each, ~20 min a review; hotspots concentrated (top 5% of files
    hold 61% of the change) -> one more
  judge 1: the project has a runnable build (`npm run dev`)
  money: Max plan, 5-hour window 23% used (resets 18:40) - no cap
```

He says yes, or edits the counts. Later, while the fleet works, the orchestrator's watch says "the backlog has grown
to 14 items and memory allows one more author: raise one?" or "2 authors idle for 40 minutes with an empty queue:
retire them?" Nothing is raised or retired without a yes.

On usage credits the money line reads `money: billed per use (no subscription limits reported) - the fleet is kept
to the smallest that works: orchestrator, 1 author, 1 reviewer, sender`, and the recommendation is that minimum
whatever the other signals say, unless the person sets `[fleet.sizing] money = "open"`.

## 2. Signals

Four groups. Every signal says where it came from and when it was taken; a signal that could not be read is named
as unknown, and the recommendation then rests on the others (never silently on a default).

### 2.1 Machine

| Signal | Source | Note |
|---|---|---|
| free memory | `MemAvailable` (Linux); `vm_stat` + `hw.memsize` (macOS) | already read by the lane on Linux; macOS is new |
| seat cost | `[fleet] seat_cost_mb`, default 800 MB (twosuns H49) | later: measured from live seats' RSS |
| full test run: time, peak memory | receipts already record each tier's seconds; **peak memory is new**: the lane records the run's maximum resident set (`getrusage(RUSAGE_CHILDREN).ru_maxrss` after the group exits, and on Linux the summed RSS of the group sampled each second, the larger kept) | a tier never measured is unknown, not zero |
| lane capacity | `machine.toml` `lane_capacity` (default 1) | |
| free disk, worktree size | `shutil.disk_usage` on the trees' parent; a worktree's size = the checkout's tracked bytes plus build artefacts, measured once on an existing seat tree | |

### 2.2 Code (research section 5, S1-S3)

Window: the last 90 days of the default branch, falling back to the last 500 commits when 90 days hold fewer than
50; merges, bot authors (`[bot]`, `noreply` service accounts) and commits touching more than 30 files are left out;
generated, vendored and lock files are excluded (`.gitattributes` `linguist-generated`/`linguist-vendored`, known
lock file names, files under `node_modules`, `vendor`, `dist`, `build`). One `git log --name-only --no-renames`
call; no `--numstat` (0.31 s against 0.72 s on this repository).

- **Independent areas `K`.** Files collapsed to directory depth 2; two areas are coupled when they changed together
  in at least 3 commits and in at least 30% of the commits of either; connected components of that graph are the
  areas; `K_eff = exp(entropy of commit share per component)`, so one busy area and five dormant ones count as about
  one. Basis: coupling drives coordination cost (Cataldo 2008), overlap drives conflicts (Brindescu 2020, Dias 2020);
  the entropy form and every threshold are heuristics.
- **Hotspots.** Per file: changes in the window x indentation complexity (indent depth summed over non-blank lines,
  tabs as 4). `H` = the share of the total held by the top 5% of files. Basis: churn and hotspots predict defects
  (Nagappan & Ball 2005, 89%; Tornhill); thresholds are heuristics.
- **Volume** (bytes of tracked text, after exclusions) is shown, not used in the formula: static size mostly
  restates line count (Herraiz & Hassan 2010).
- Cyclomatic, cognitive and Halstead measures are not used: they need a parser per language, and on the evidence
  add little beyond size.

### 2.3 Work

The backlog `B` is the union of four sources, each counted once and each named in the output:

1. **The ledger's open rows** that are not yet anyone's: rows reserved for a post are seats, not work.
2. **Tasks the person named**: the orchestrator passes them, `flotilla spawn --tasks N` (or `--tasks-file <path>`, one
   task per line), when it has broken the person's request into parts.
3. **An external tracker**: `[fleet.sizing] tracker = "github"` counts open GitHub issues (`gh issue list --state
   open --json number,labels`), filtered by `labels` when set; other trackers later.
4. **TODO-like files**: `[fleet.sizing] backlog_files = ["TODO.md", "docs/**/*.todo.txt"]` (globs from the repository
   root). An item is an unchecked task line (`- [ ]`, `* [ ]`) or, in a file with no checkboxes at all, a top-level
   bullet under a heading; an item marked done (`- [x]`, struck through, or inside a section titled Done) is not
   counted. flotilla's own `TODO.md` is an example that would count its open bullets.

Items are split into **main** (substantial) and **minor** (small) only where the source says so - a label
(`size:small`, set in `[fleet.sizing] minor_labels`) or a `[minor]` prefix on the line - and otherwise counted as
main: guessing size from wording would be a number nobody can check.

### 2.4 Money

From the **status line input**, a documented interface: Claude Code passes a JSON object to the status line command
on stdin, with `rate_limits.five_hour.used_percentage`, `rate_limits.seven_day.used_percentage` and their `resets_at`
(present "only for claude.ai Pro and Max subscribers, or behind a Claude apps gateway that sets a spend limit",
after the first API response), `context_window.context_window_size` (200000 or 1000000), `model`, and
`cost.total_cost_usd`.

- **How flotilla reads it.** Seats are launched with `--settings`; their settings gain a status line command,
  `flotilla statusline record`, that writes the JSON it receives to `<state>/statusline/<session_id>.json` (the newest
  only, a few hundred bytes) and prints a short line. The person's own session is touched only on their yes
  (`/flotilla:onboard` offers it); flotilla never replaces a status line the person set - it wraps it, calling theirs
  after recording.
- **Precondition, to be measured first:** whether a background session (`claude --bg`) runs its status line command
  at all. If it does not, the money signal comes from the person's own session only, and with that declined, it is
  unknown.
- **Billing mode.** `rate_limits` present → subscription (Pro/Max), and the remaining window is known; a recorded
  input after the first response without `rate_limits` → billed per use (API key or credits), unless `spend_limit`
  says a gateway limit applies; `ANTHROPIC_API_KEY` in the environment → billed per use (authentication precedence,
  `code.claude.com/docs/en/authentication`); nothing recorded → unknown.
- **What it changes.** Billed per use: the recommendation is the minimum fleet (orchestrator, 1 author, 1 reviewer,
  sender; a judge only if the profile requires one) unless `[fleet.sizing] money = "open"`. Subscription: no cap
  while the 5-hour window is under 60% used; above it, authors are capped to half and the line says when the window
  resets; above 90%, "raise nothing now". Unknown: no cap, said plainly.
- **Context window** is shown with the model ("sonnet, 200k"); it does not change the counts in this design.

## 3. The rule

```
authors   = max(1, min(K_eff, B, runs_cap, memory_cap, disk_cap, money_cap))
  runs_cap   = handovers the lane can verify per hour / handovers one author makes per hour
               (lane runs that fit: floor((free - seats x seat_cost) / run_peak_mem), capped by lane_capacity;
                per hour: runs x 3600 / run_seconds; one author's handovers per hour: measured from the ledger, 1 by
                default)
  memory_cap = floor((free - floor - run_peak_mem) / seat_cost) - the other posts
  disk_cap   = floor(0.5 x free_disk / worktree_size) - the other posts
main/minor  = authors split by the backlog's main/minor mix (at least 1 main when any main item exists)
reviewers   = max(1, ceil(authors x handovers per author-hour x review hours)) + (1 if H > 0.5)
              (review hours: measured from the ledger's hand->accept times, 0.33 h by default)
sender      = 1;  orchestrator = 1 (the person's session when it leads)
judge       = 1 when the profile requires one, or the project has a runnable build or a deploy target
```

The output names every term and **the binding cap** - the one that set the number. A term that is unknown is left out
of `min` and named ("disk: unknown, not counted").

## 4. Interfaces

- `flotilla fleet size` - prints the recommendation and its reasoning; `--json` for the skill. Read-only; one census
  call, one `git log`, `gh` only when a tracker is configured.
- `flotilla spawn --recommended` - raises the recommendation (dry run first, as every spawn); `/flotilla:spawn` with
  no arguments shows it and asks "raise this fleet?", with the counts editable.
- `flotilla spawn --tasks N` / `--tasks-file <path>` - the orchestrator's named tasks, for the work signal.
- `[fleet.sizing]` in the profile: `tracker`, `labels`, `backlog_files`, `money` (`"auto"` default, `"open"`),
  `window_cap_percent` (60), `max_seats` (a ceiling the person sets; never exceeded).
- The watch (orchestrator) gains two items, each a question, never an action: **grow** (backlog over the authors'
  capacity and a cap allows more: "raise 1 main?") and **shrink** (an author idle past `IDLE_SEAT_MINUTES` with the
  backlog empty, or the money window over its cap: "retire main session 3?"). Each names the signal that moved.

## 5. Stages - each a release

1. **Machine and work** - the rule with `runs_cap`, `memory_cap`, `disk_cap` and `B` from all four sources;
   `fleet size`, `spawn --recommended`, `--tasks`; peak memory recorded by the lane; macOS memory.
2. **Code** - `K_eff` and hotspots from one `git log`.
3. **Money** - first the measurement (does a background session run its status line?); then `statusline record`,
   the seats' settings, the person's opt-in, the money cap.
4. **During work** - the watch's grow and shrink questions.

Each stage ships with tests first, injections for every rule, a review, and the measured numbers it rests on.

## 6. Testing and calibration

- Every signal is a pure function over recorded inputs (a `git log` text, a meminfo text, a status line JSON, a
  ledger), tested on fixtures that include the empty, the unknown and the adversarial case (a repository of one
  commit, a lock file in every commit, a bot that touches everything, a TODO with only done items).
- The rule is tested as a table: inputs -> counts and binding cap.
- Thresholds start at the research's values and are named constants; the field tests (twosuns, worldcore) give the
  first calibration: compare the recommendation with the fleet that actually ran and how it fared (idle seats,
  conflicts, return rates). The decisions log records each change of a threshold with its evidence.

## 7. Pitfalls

Generated and vendored files, lock files forming one false area, binary files, renames (`--no-renames` loses some
history; accepted), squash-merged histories (one commit per feature: fewer, larger commits - the 30-file cutoff still
applies), shallow clones (the window falls back to what is there and says so), young repositories (under 50 commits:
`K` unknown, not 1), monorepos (depth-2 directories may be too coarse; `[fleet.sizing] area_depth` overrides),
reformatting commits (they touch everything: the 30-file cutoff drops them).

## 8. Out of scope

Raising or retiring anything without a yes; reading any session's transcript (policy); estimating tokens per task;
pricing in currency for subscriptions; trackers other than GitHub in the first version.

## 9. Open questions

1. Does a background session run its status line command? (Measured in stage 3 before anything is built on it.)
2. Should `fleet size` cache the code signals (they change slowly) - per trunk revision, in the state directory?
3. Is a seat's memory better measured live (RSS of its process tree) than configured?
