# Seats compact early and carry their working memory across (issue #9, items 1 and 2)

Status: design, 2026-11-20. Target release: 0.34.0.

## Why

A fleet's seats live for days and their context grows until Claude Code compacts it near the 1M window. Every step a
seat takes re-reads that context from cache. Measured on one machine over three days (issue #9, 16-21 live seats,
flotilla 0.31.0): 91.5% of the machine's spend was in seats; context per request had a median of ~530k tokens and a
p90 of ~850k; cache reads were 76% of the spend; the same requests with context capped at 200k would have cost ~58%
less. 825 turns were acknowledgements of another session's message, ending within two requests and costing almost
nothing but the re-read of that context.

The person accepts that a fleet costs more than one session: inter-session communication is the product. What must
not happen is a fleet using up a week's limit of the top subscription plan in a few days. The person's second
concern is quality: frequent compaction must not leave seats forgetting what they were doing.

## Goals

1. **A seat compacts at a window its post sets**: 200k tokens for every post, 400k for the orchestrator, whose
   memory holds the fleet together. The person can change it per post, or turn it off.
2. **What a seat knows about its work survives compaction**, from three sources:
   - what flotilla already re-injects (the seat's name, post and the rows in its name: the SessionStart hook runs on
     every source, `compact` included, and the post text rides in the system prompt, which compaction keeps);
   - a compaction summary that flotilla steers with its own instructions (`PreCompact`);
   - **a working note the seat keeps in the ledger**, on the row it works on or on its post row, re-injected verbatim
     after compaction. This is the part that does not depend on the summary's quality.
3. **No turn exists only to acknowledge** (issue #9's item 2): a seat does not answer a message that asks nothing
   and gives no task, and starting up does not wake every live peer.

## Non-goals

- Retiring a seat and raising a fresh one with a handoff (full rotation). The working note is built so rotation can
  use it later, but rotation is not in this release.
- Batching watcher notifications, a model per kind of work (#10), Claude Code mods. All are in the backlog.
- Measuring a seat's context size. flotilla reads no transcript; it sets the window and lets Claude Code compact.

## Anti-goals (a failure even if it works)

- A seat stopped by Claude Code's compaction thrash breaker (below) because flotilla set the window too low.
- A working note acting as instructions: it is data the seat wrote for itself, shown as such, and nobody else can
  write it.
- flotilla reading a transcript or a compaction summary.
- A note-writing habit that adds turns: every extra request re-reads the whole context, so a note rides on a move
  the seat makes anyway wherever possible.

## What Claude Code gives (2.1.329, checked 2026-11-20)

From the docs (code.claude.com: settings-reference, hooks, model-config, agent-view, changelog) and a headless test
in a scratch project (`claude -p` with `--settings` carrying `autoCompactWindow: 100000`, a `PreCompact` and a
`SessionStart` hook, reading ~140k tokens of files):

- **`autoCompactWindow`** (setting; also `--autocompact`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW`), at least 100k; default
  ~967k on 1M models. Passed in `--settings`, which flotilla already gives each seat (`launch.argv`). A background
  session keeps the flags it was dispatched with across supervisor restarts (agent-view, "Settings and provider").
  A plugin cannot ship default settings, so flotilla must pass it at launch; **a seat raised before this release
  keeps the 1M window until it is raised again.**
- **Compaction starts below the window.** Measured: a 100k window compacted at 71-77k tokens, i.e. ~25-30k below it,
  and left 21-26k after compaction (a scratch session; a seat's system prompt and skills add to that). So 200k means
  compaction near ~170k; 400k near ~370k.
- **`PreCompact`** fires with `{"trigger": "auto"|"manual", "custom_instructions": ...}`. Its stdout (exit 0, up to
  10,000 characters per hook) becomes instructions for the summary. **Verified**: a line the hook asked to keep was
  in each of three summaries.
- **`SessionStart` fires with `"source": "compact"`** after each compaction, and its stdout reaches the session.
  **Verified**: the session saw the hook's text after compaction.
- **The thrash breaker**: when the context refills to the limit within 3 turns of a compaction, three times in a
  row, Claude Code ends the turn with an error ("Autocompact is thrashing ... A file being read or a tool output is
  likely too large"; `terminal_reason: rapid_refill_breaker`). Reproduced at 100k with ~25k-token file reads. With a
  200k window a seat would need roughly 120k+ tokens of tool output within three turns, three times in a row.
- No hook can trigger `/compact` or `/clear`; a message saying `/compact` arrives as text. Each compaction is itself
  a request (reads the context, writes a summary; ~16 s measured).
- **Not verified in a `claude --bg` session**: the test could not run there, because a background session refuses a
  directory whose trust was never accepted, and accepting it writes the person's own Claude Code state. The docs say
  hooks run in every kind of session. flotilla's hook trace (`watch/fired`) records the first real `PreCompact` of a
  seat, which settles it on a live fleet.

## Approaches

- **A. Steered summary only.** PreCompact gives flotilla's checklist for the summary; SessionStart(compact) adds a
  line telling the seat to check its row and `git status`. Cheapest (1-2 days); quality is Claude Code's summary,
  better aimed.
- **B. A plus a working note in the ledger** - **chosen by the person.** The seat writes a short note on its row at
  moments it already makes a move; the note is re-injected verbatim after compaction. What the seat wrote survives
  without loss, the orchestrator sees it when a seat dies, and full rotation can later hand it to a fresh seat.
- Rejected: blocking compaction in PreCompact to make the seat write a note first (a hook cannot make the model act
  and then compact; a blocked compaction only lets the context grow); keeping Claude Code's summary from PostCompact
  (reading session content, against flotilla's policy, and nothing gained over A).

## Design

### 1. The window, per post

- A post's frontmatter may name `compact_window`: a whole number of tokens, or `off` for Claude Code's own default.
  The shipped templates say `compact_window: 400000` for `orchestrator.md` and `200000` for every other post.
- A post file that does not name it (a project whose posts predate this release) gets the same built-in defaults by
  post name: 400k for the orchestrator post, 200k otherwise. The person turns it off with `off`.
- **flotilla's floor is 150,000** (Claude Code's is 100,000): below it, ordinary tool output in a seat risks the
  thrash breaker. A lower value is refused when the posts are loaded, naming the floor and why.
- **No ceiling is needed** (Q6, answered by the docs): Claude Code caps `autoCompactWindow` at the model's context
  window (settings-reference, `autoCompactWindow`), so a 400k orchestrator on a 200k model compacts at its window.
- The value rides in the seat's `--settings` JSON as `autoCompactWindow`, merged with what that JSON already carries
  (the MCP plugins a post does not keep). Every seat flotilla launches gets it: `spawn`, borrowed seats and helpers
  (a helper's window is its post's). `spawn --lead` names the person's own session, which flotilla does not launch:
  the window cannot reach it, and `spawn --lead` says so, with the command the person can type there
  (`/autocompact 400k`).
- The dry run and the launch table show each seat's window, since `--settings` is hidden from the printed command.
- The seat's post row records the window it was raised with, so `flotilla fleet` can name seats still on the 1M
  window after an upgrade ("raised before compaction windows; raise it again to apply").

### 2. The working note

- **A memo belongs to the session that wrote it** (builder check, Q1 and Q4 - my call). The latest memo of each
  writer on each row is kept. The field `note` stays the wait's reason.
- **Memos live in their own log beside the ledger** (`<state>/memos`, keyed by the project like the ledger), not as
  row fields or ledger events (found while planning): a row's last update and last event drive the dropped-ball and
  late-row clocks and a release's `settled_by`, so a note written as an event would reset a stalled row's age and
  could hide what the last real move said. A memo changes no row's state, age or history. So a reviewer's
  memo on a row it reads is its own, never the author's; nobody can write or overwrite another session's memo, which
  keeps the anti-goal ("nobody else can write it") without an owner-only rule that `fix` could never pass.
- **Size and shape** (Q2 - my call): at most 1,500 characters and 8 lines. Line breaks are allowed; every other
  control or hidden character is refused (`core.text.has_hidden` with `\n` let through). Wherever a memo is shown it
  is rendered as a quoted block - each line prefixed `  | ` under a header naming its writer and age - so no memo
  line can pose as a `flotilla:` line of a hook or as an instruction.
- **Writing it**: `flotilla work note <branch> --text '<note>'` on a row the caller owns or reads (`--memo` too:
  a mover that records a wait keeps no note there; a `fix` on a row nobody reads yet makes the caller its reader);
  `flotilla work note --post --text '<note>'` on the caller's post row (the orchestrator's note lives there: what it
  leads, whom it waits on, what the person decided). The move changes no state and needs no `may` entry. Each note
  replaces the caller's last one; the events keep the history.
- **Riding on moves already made**: `work hand`, `work wait` and `work fix` take `--memo '<note>'`, written as the
  caller's memo on that row, so the commonest note costs no extra request. The standalone command is for moments
  with no move.
- **Adoption** (Q4 - my call): the memos stay on the row with their writers' names. The heir sees them in
  `work show` and, after its own compaction, under "left by <gone seat>" - never as its own. `work adopt` prints the
  inherited memos to the orchestrator, which already sends the heir a letter; the letter says to read `work show`.
- **What a note holds** (the post templates say it): what is done, what is in flight, the next step, files touched
  and not yet committed, decisions and their reason, who the seat waits on. One to eight lines.
- **When a seat is asked to write one**: with the hand, wait and fix moves; after a decision that changes the plan;
  for the orchestrator, after the person decides something and after assigning work. Not after every step.
- **Where that habit is taught** (Q5 - my call): projects copy their posts once and update them by hand, so a rule
  only in the templates would never reach an existing fleet. The habit and the no-acknowledgement rule (section 5)
  ride in the text every seat's system prompt carries (`launch.py`, beside `REFUSAL`) and in the `flotilla` skill;
  the templates add only what is particular to a post (the orchestrator's moments).
- **Who reads it**: the owner after compaction (below); `work show <branch>`; the seat that adopts the row (`work
  adopt` prints the memo it inherits); the orchestrator, through `work show`.

### 2a. The person's own session leading the fleet (`spawn --lead`)

The person talks with the orchestrator a lot, so its memory matters most whichever way it runs. A background
orchestrator is a seat like any other (window 400k, post row, memos). A leading session that is the person's own has
no post row and is not launched by flotilla; its hooks still run, so it gets the steered summary and the re-shown
rows. Its post-level memo (`work note --post`) is kept in flotilla's state directory, keyed by the project and the
leading name, instead of the ledger, and is re-shown after its compaction the same way; `work show` does not list it
(builder check Q3 - my call after the person's answer that the orchestrator is the session they talk to most).
`spawn --lead` tells the person to type `/autocompact 400k` in that session, since flotilla cannot set it there.

### 3. Before compaction (`PreCompact`)

- `hooks/hooks.json` gains `PreCompact` -> `flotilla hook pre-compact` (no matcher: manual and auto).
- In a flotilla project, for a session that holds a post, it prints the summary instructions: keep the open rows
  this seat owns with their branch and state; decisions made and why; files touched and whether they are
  committed; approaches that failed; messages the seat owes or awaits; the exact next step. It also says that the
  working notes will be shown again after compaction, so the summary need not copy them. Kept under ~2,000
  characters: it is read on every compaction.
- Outside a project or for a session with no post, it prints nothing. It never blocks compaction, and a failure
  prints nothing (an empty instruction is Claude Code's default, not a broken session).

### 4. After compaction (`SessionStart`, source `compact`)

- Besides what it prints today (identity, the rows in the seat's name), the hook prints, for a seat: a line saying
  the session was compacted and that the notes below are data written earlier, to check against `git status` and
  `flotilla work show` before acting; then its own memo on each open row it owns or reads and on its post row, and
  under "left by <session>" the memos of a row it adopted - each as a quoted block with its age.
- Kept short on purpose: what the hook prints after compaction counts toward the refill the thrash breaker watches.
  Rows without a memo are listed by branch only.

### 5. No acknowledgement turns

- **Starting up** (skill `flotilla`, "When you start"): steps 2 and 3 become one message to the orchestrator - name,
  post, home tree, what was inherited. No message to every live peer: `flotilla fleet` shows the fleet to anyone
  who needs it. The orchestrator itself tells the person, as today.
- **A rule in the skill and in each post template**: a message that asks nothing and gives no task needs no reply;
  never reply only to acknowledge, thank or confirm receipt. A letter is answered by making its move (the ledger
  shows it); a task, by doing it. A message that needs no answer says so in its last line ("no reply needed").
- **The orchestrator template**: it does not acknowledge a seat's report or a moved row; it answers only to give a
  task, a decision or a question's answer.
- Templates move their `template_version`; `tests/fixtures/post-moves.json` follows.

### 6. Seats it does not reach yet

The window applies from the next raise. The CHANGELOG and `flotilla fleet` say which live seats are on the old
window, and that raising them again (retire, then spawn) applies it. flotilla does not retire anyone on its own.

## What the person sees

- In `flotilla spawn --dry-run`: each seat's compaction window (`compacts near 200k`).
- In a seat: nothing new until its first compaction; then the seat carries on, its notes re-shown to it.
- In `flotilla work show <branch>`: the row's latest working note.
- In `flotilla fleet`: seats raised before this release, named as still on the old window.
- Over days: a smaller context per request, so a slower draw on the plan's limits. Anthropic does not publish how
  cache reads weigh against limits, so the release promises the context size, not a number of days.

## Left to the builder

- The exact CLI shape of `work note` (`--post` vs. a branch argument) and its refusals' wording.
- Whether the doctor's checks still run on `source: compact` (they cost nothing in tokens if they print nothing).
- How the post row records the window (an event field at raise, or the evidence of the reserve move).
- The test layout. The thrash floor and the window defaults are rulings, not choices.

## Open after this release (to TODO.md)

- Verify on a live fleet that `PreCompact` and `SessionStart(compact)` fire in `--bg` seats (the hook trace shows
  it), and the share of context per request before and after.
- What flotilla does when the thrash breaker stops a seat: which hook, if any, sees it, and whether the orchestrator
  should be told.
- The backlog from the research of 2026-11-20 (wake-up batching, full rotation, model per post, output hygiene,
  shared system-prompt cache, mods).
