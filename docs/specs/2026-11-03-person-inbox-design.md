# What waits on the person reaches the person (0.26.0) - design

Status: draft for the person's approval. Source: the field trace
`docs/field-tests/2026-11-03-alpha-person-relay.md`.

## Why

On alpha, 144 rows waited on the person between 2026-10-04 and 2026-10-16. The person asked "what needs my
attention?" eighteen times, and the replies named 24 of them. After 2026-10-10, 104 were never named again. Then
"nothing urgent" was given as the answer.

No message was lost. Flotilla told the orchestrator about each row once. After that its hook folded the rows into
"N standing item(s), said before and unchanged", and it did so at the very moment the person was typing. "Said
before" meant said to the orchestrator's session. Two prompt rules would have caught it ("brief the person from the
ledger, never from memory"; park with `hold --until "the person"`), and nothing made either rule run. So whether what
waits on the person reached the person depended on prompt text alone. This release makes it a property of flotilla.

A person's path, today and after:

- **Today.** The person types into the orchestrator's session: "anything that needs me?". The hook gives the model
  "and 140 standing item(s), said before and unchanged". The model answers from memory: "nothing urgent".
- **After.** The same prompt makes flotilla print a line on the person's own screen, which the model cannot change
  or withhold: `flotilla: 104 wait on you (3 yes to ship, 2 checks by ear, 99 put off until after the teaser) -
  flotilla inbox`. The model gets the same counts on a line of their own, with the instruction to answer from
  `flotilla inbox`. If the person runs `! flotilla inbox`, they see every row: grouped, aged, and with when flotilla
  last showed it to them.

## Goals

1. **One queue for the person, read from the ledger every time.**
   - `flotilla inbox` lists everything flotilla knows to be waiting on the person. The set is fixed under "Decided".
   - Rows are grouped by what the person is asked to do. Each row gives its age and when flotilla first showed it
     to the person, or "never".
   - The queue is computed and never stored. No memory, file or session can remove a row from it, except a ledger
     move.
2. **The person's waits are never folded.**
   - In the orchestrator's prompt hook, person items leave the "standing" fold. They get their own count line on every
     prompt, whether or not they are new.
   - In `watch --once`, the listing is split into separate counted sections: waits on the person, moves nobody can
     make (no live holder of the post), and gone sessions. One number for all of them is gone.
3. **A channel past the model.**
   - When the person types in a session of the project, the hook's `systemMessage` puts a one-line summary of the
     queue on the person's screen.
   - Flotilla records what it showed and when (its own state file, not a ledger move made by a model). That record
     is the only thing that counts as "shown to the person".
4. **Putting off is a recorded move and stays visible.**
   - A wait on the person leaves the active part of the queue only through a recorded put-off, with its why. The
     orchestrator or the person may make it.
   - A put-off row stays in the queue under "put off", with its why. A summary never drops it.
   - Today's `work hold` accepts only `handed`, `fixing`, `accepted` and `queued` rows. So it cannot put off a judge's
     check on a `shipped` row or a main session's question on a `claimed` row, which is 60 of the trace's 144.
   - The put-off therefore accepts any open row whose wait is on the person.
   - It must not change what a hold means for batching or the reading queue. The builder chooses between widening
     `hold --until "the person"` for such rows and adding a separate recorded mark on the wait.
   - A row the orchestrator "parked" in words is still an active wait, and the person's line counts it as one.
5. **The orchestrator's post says it plainly.** When the person asks about status or about what needs them, the
   answer starts from `flotilla inbox`. The template version moves.
6. **The person chooses how they are asked: two attention modes in this release.**
   - **All** (the default). Everything that waits on the person reaches them. The line shows when the queue changes.
   - **Digest.** Everything still waits on the person, but the line comes once per period, 30 minutes by default and
     set by the person. A blocker does not wait for the period.
   - A mode changes what the person is asked and when. It never changes what they can see: `flotilla inbox` lists
     everything in every mode.

## Non-goals

- One notification per row, Telegram or push messages, or a reminder schedule.
- Recording that a model "told the person" (`work told`). A model writing that it told the person proves nothing.
  The fact that counts is flotilla's own display.
- Repairing an orchestrator's memory files in a project. The project's sessions do that themselves.
- Changing which posts may record a wait on the person, or asking the person from background sessions.
- Item 6 of the trace (a wait "moving to the person" when a session dies). That is a session's convention, not code.
- The "only what matters" and YOLO modes. They come in 0.27 with a design of their own, because they decide who a
  question is for, not when it is shown. Both need a kind recorded on each wait (a blocker, taste, architecture, an
  implementation choice) by the session that asks it. They also need a recorded "decided without you" move that
  the inbox lists, with what was chosen and why. That changes every post's template, which this release does not do.
  Two things are decided already and bind that design:
  - YOLO delegates everything to the fleet, including the person's yes to ship and permission questions.
  - YOLO is always marked: every line flotilla puts on the person's screen says the project runs in YOLO, and so
    does the inbox.

## Anti-goals (the release fails if it does any of these)

- The person gets 144 lines, or the same line at every prompt until they stop reading it.
- The orchestrator can silence any part of the person's line: no flag, no config key it can reach, and no hold hides
  the count. A hold only moves a row from "active" to "put off". The attention mode is the person's alone (see
  "Decided").
- A line counted as shown in a session no person was reading.
- A queue that is cheap to show but wrong: a count the inbox listing would not reproduce.

## Decided

- **Grouping comes from the post that recorded the wait.** Each ledger event carries `by` and `post`:
  - sender → "a yes to ship";
  - judge → "a check only you can make";
  - main/minor → "a question";
  - any other or unknown post → "other";
  - a census session waiting at its prompt → "a session waiting in its own window";
  - a row held until the person → "put off", whatever recorded it.
  Grouping by post is used over grouping by keywords in the why, which the trace found rough. The why text is
  shown verbatim.
- **Urgent** is a row with an `urgent` request. Urgent rows come first in the inbox and get their own count in the
  line.
- **What the queue holds.** Everything that waits on the person, so the line and the listing count the same set:
  - open rows whose wait is on the person;
  - rows held or put off until the person, with or without a recorded wait;
  - rows whose next move is the person's own (a merge only they make, an `approve`);
  - permission questions in the broker's queue (answered with `/flotilla:permit`);
  - rig requests only the person answers (`share`, `machine`);
  - census sessions waiting at their own prompt.
  Each is a group of its own in the listing.
- **Where the line appears:** on the person's prompt in any session of the project they type into, whether it is
  interactive or a background session they attached to.
  - On alpha the person talked to background orchestrators by attaching to them. A rule limited to interactive
    sessions would miss the case this release exists for.
  - A line shown where nobody watches loses nothing. What must not happen there is recording it as shown (see
    below).
  - One throttle per project, not per session, so moving between windows does not repeat it.
- **The model's line** goes to the orchestrator only, on every prompt, unthrottled. It carries the counts on a line
  of their own and "answer the person's status questions from `flotilla inbox`"; it costs the person nothing. Seats
  get the person's line on screen but no instruction, and their templates do not change.
- **What counts as shown.**
  - A row counts as shown the first time a line flotilla put on the person's screen counted it. The listing gives
    each row's first-shown time, and a row never shown says "never".
  - The full listing counts too when the person runs it from a terminal outside every Claude Code session.
  - A run inside a session does not count, `!` included. *Changed while building, 2026-11-03:* measured on Claude Code
    2.1.317, a model's Bash call and the person's `!` run with the same environment and the same parent process, so
    nothing tells them apart there. A hook-side note on the model's calls was built and failed two security reviews:
    keyed on the word `inbox`, `in''box` slipped past it; keyed on a time window, a slow or repeated command did.
    The listing still prints inside a session; the person's line on the screen records "shown" there.
  - A line shown in a background session counts only if the measurement below finds that the prompt was the
    person's.
- **When the line shows.**
  - In "all": when the queue changed since the line last showed (a row came or went), and on the first prompt after
    a break of more than an hour. An unchanged queue does not repeat it otherwise. The one-hour return rule is the
    designer's call.
  - In "digest": on the first prompt after the period has run since the line last showed, and at once for a blocker.
  - A blocker is computed by flotilla, never judged by a model: a row with an `urgent` request, or a wait whose
    session has no other open work, so the fleet stands until the person answers.
- **Several sessions asking at once.** All their waits go into the one queue. The inbox numbers every row, one
  question per numbered row, so the person can answer by number.
- **Who sets the mode: the person only.**
  - The person picks it at onboarding (the custom onboarding asks) and changes it any time with a command of their
    own.
  - It lives in a file the person owns that Claude's edit tools are refused, as `machine.toml` already is
    (`guards/files.py`). The command is refused from the Bash tool, as `work approve` is (`guards/person.py`).
  - No post, no session and no model can change it. That closes the case where an orchestrator switches itself to a
    quieter mode.

## Measured before it is relied on

The Claude Code hook docs say only that `systemMessage` is a "warning message shown to the user". The
`UserPromptSubmit` section does not mention it, and the docs do not say whether it can be combined with
`hookSpecificOutput.additionalContext` in one output.

So the plan's first task measures it on Claude Code 2.1.317:

- whether a `UserPromptSubmit` hook's `systemMessage` shows on screen, in an interactive session and in a background
  session the person attached to;
- whether the model sees it;
- whether it lands in the transcript;
- whether it coexists with `additionalContext`;
- whether the hook can tell the person's own prompt from other turns that fire it: a peer's message, a background
  task's notification, a `/loop` firing. If it cannot, the line still shows, since on screen it harms nothing, and
  "shown" is recorded only in interactive sessions, with the reason written down.

The result goes in the module's docstring with the version, as `guards/person.py` does for `!`.

*Measured while building, 2026-11-03, and what changed with it.* The line shows on screen in both kinds of session,
the model does not see it, and it goes with `additionalContext`. The hook cannot tell the person's prompt from other
turns: a peer's message, a task's end and a subagent's hand-back carry a tag, but a CronCreate or `/loop` firing
arrives as plain text, and the prompt's transcript record, which says `promptSource` typed or system, is written only
after the hook. So the hook shows the line when it is due and leaves the turn pending, and the Stop hook counts it
only when the transcript records the prompt as typed by a human: the line as shown, the person as having prompted. A
background session keeps a throttle of its own, and its first counted prompt, its launch, is never the line shown;
the person's windows share one throttle per project. (Two reviews of the build found a scheduled firing and a seat's
launch spending the person's line where nobody read it.)

**If it does not show, the build stops and the channel comes back to the person as a decision.** The candidates
then are a `terminalSequence` notification or the window title (interactive only), and a SessionStart stderr line
(shown to the user, but only at start).

## Left to the builder

- The inbox's text layout and the exact line wording, within the example above.
- The mode command's name and the key it writes. Whether the mode is one per machine, or one per project with a
  machine default.
- The state file for "shown", keyed per project and per row. It records only flotilla's own displays.
- Whether `watch --wait` also wakes on "person items not shown for 24 h". It may only tell the orchestrator, and it
  must not loop.
- The regression test is on a synthetic ledger with the trace's shape:
  - many rows waiting on the person, recorded by sender, judge and main posts;
  - one hold until the person;
  - one row "parked" in words only;
  - repeated person prompts in the orchestrator's session.
  It must fail on 0.25.0, where the hook folds the rows into "standing" and no line reaches the person. The real
  alpha data stays out of the repository.

## Compatibility

Seats on older flotillas keep today's behaviour. alpha (user-scope 0.21) gets this only when the person updates
it. The hook change is local to each session's flotilla, so mixed versions need no gating.
