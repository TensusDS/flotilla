# What waits on the person reaches the orchestrator once, and nothing checks that it reached the person (measured 2026-11-03)

Found by accident. On 2026-10-16 at 20:22 the twosuns orchestrator (orchestrator 3, flotilla 0.15.0), asked whether the
person was a perfectionist, gave as evidence "the fleet has 220 pending items, some waiting 11 days". The person asked
"which ones?!". The orchestrator answered that they were the queue for after the teaser, and the person let it go. On
2026-11-03 a later session quoted the same figure as evidence that the person is the fleet's bottleneck, and the person
asked for every item to be traced: when it was made, whether they were told, through which channel, and whether they
had asked for blockers since. This is that trace.

The result is a flotilla defect, not a person's backlog. Most of what waited on the person had stopped reaching them,
although they asked eighteen times. Nothing in flotilla noticed.

## What was read

- The orchestrator's own `flotilla watch --once` output from 2026-10-16 20:23:53, still in its scratchpad (219
  attention lines; `data/watch-once-2026-10-16T2023.txt`).
- The twosuns ledger (`~/.local/state/flotilla/ledger/path-home-max-workspace-twosuns-origin-git-1f4843af35e8.jsonl`,
  16,598 moves): for each item's rows, every `wait` with its `waiting_on`, time and author, and every `hold`/`unhold`.
- The 9 twosuns transcripts in which the person typed at least once (the orchestrators' sessions, and seats the person
  attached to): every assistant text, `AskUserQuestion`, `PushNotification`, Telegram reply and `SendUserFile` up to
  the snapshot. These are the channels to the person.
- The person's own messages in those transcripts, for requests like "what needs my attention?" / "what's the status?".
- The parking note the orchestrator pointed to, `MORNING-2026-10-10.md` (`data/`).

An item counts as **named** to the person when a channel carried its branch name, the branch's last segment (8+
characters) or one of its row ids (`r123`). Prose that describes an item without naming it is not counted. See the caveats.

## 1. The 220 were not 220 decisions

219 lines, 218 parsed (one did not match), 196 distinct branches: one branch can stand on several lines.

| class | lines | waits on the person? | what it is |
|---|---|---|---|
| N1 | 46 | no | accepted work; "the move is the sender's, and no live session holds the `sender` post" |
| N2 | 27 | no | the move (or the wait) belongs to a session that is not alive |
| N3 | 1 | no | `broken_unfixed`: a break with no fix row |
| P | 144 | yes | `waits on the person: ...` |

`watch` counts these four kinds as one number. Seventy-four of the 218 lines were fleet staffing (a missing sender, dead
seats). That is the orchestrator's to settle, not the person's.

## 2. When the 144 waits were made, and by whom

None was recorded by the person. They were recorded by posts that may not ask the person themselves:

- 84 by senders. This is the `approve before queue` wait from `templates/posts/sender.md` rule 3: the project
  authorizes merges by a human, and the sender records the wait and tells the orchestrator.
- 39 by main sessions.
- 21 by acceptance judges, mostly sound the headless browser cannot hear, and checks only the person's GTX 1060 can make.

The current wait on the person began between 2026-10-04 and 2026-10-16: 20 on 10-04, 27 on 10-05, 19 on 10-06, 13 on
10-07, 10 on 10-08, 8 on 10-09, 34 on 10-10, 7 on 10-11, 2 each on 10-13, 10-15 and 10-16. By what the snapshot line
says (a rough keyword split): 63 accepted work waiting for the person's yes, 29 sound, 22 a test on the person's GPU,
10 paused or parked, 20 other.

## 3. Were they told to the person?

| class | items | what the channels show |
|---|---|---|
| P1 | 12 | named after the wait began, and named again after the parking of 2026-10-10 (section 5) |
| P2 | 104 | named after the wait began; parked on 2026-10-10 "without reminders"; **never named again** up to the snapshot |
| P3 | 15 | named only before the wait was recorded: the topic was discussed, the wait itself was not |
| P4 | 13 | never named by branch or row id |

Channels that named any of them: the orchestrator's chat text (131 items), `AskUserQuestion` (23), `SendUserFile` (1).
No push notification and no Telegram message named any of them. 26 of the 144 appear in `MORNING-2026-10-10.md`, a
file nobody showed in the chat after it was written.

**Named is not the same as asked.** In a random sample of 12 P2 items (`scripts/sample.py`, seed 7), about 8 first
reached the person as an explicit request: "they wait for your yes", "Answer yes to ship 3 rows", "tell me whether
the crackle is still there", "only you can do this". The other 4 were named only inside a progress report: "review
10 accepted r457", "I held r405 together with the cloud chain".

So: at most 116 items were ever put to the person, and 104 of those had dropped out of every channel by the snapshot.
At most 28 were never put to the person. This is an upper bound. Some P3/P4 items were asked in prose: the slot 7
megastructure take was asked on 2026-10-13 without its branch name. Others were parked by the fleet itself (`feat/dune-detail`
"parked by orchestrator 3", `feat/wind-ridges` "parked by the orchestrator's order"), or became the person's
automatically: `fix/bolt-joints`, "orchestrator 3 is not alive, so the wait moves to the person".

## 4. The person asked, eighteen times

Every request by the person, before the snapshot, for what needed them, with the number of P items already waiting on
them at that moment and how many of those the reply named:

| when (UTC) | the request (translated) | waiting | named |
|---|---|---|---|
| 10-04 00:59 | what's the status? | 0 | 0 |
| 10-04 16:54 | what's the status? | 2 | 0 |
| 10-05 14:55 | where did we stop, and what's the status? | 36 | 12 |
| 10-06 18:47 | bring me everything that needs my attention, 2-3 items at a time | 56 | 0 |
| 10-07 00:25 | remind me what you need from me, answers or tests? | 66 | 3 |
| 10-07 08:44 | what's the status? | 70 | 1 |
| 10-07 16:16 | anything else that needs my answers or attention? | 74 | 1 |
| 10-08 23:47 | anything else that needs my input? | 88 | 2 |
| 10-09 00:56 | anything else that needs answers? | 89 | 1 |
| 10-09 19:26 | what do you need me to check? | 91 | 2 |
| 10-09 21:36 | anything else that needs attention? | 91 | 3 |
| 10-10 19:05 | please put my debt of questions and answers in order | 129 | 0 |
| 10-11 22:23 | anything else that needs my decision? | 137 | 0 |
| 10-12 19:26 | anything else that needs attention or comments? | 138 | 1 |
| 10-12 21:36 | tell me as a list, with screenshots and clips, what needs attention now | 138 | 0 |
| 10-12 23:01 | anything else that needs attention? | 138 | 0 |
| 10-13 23:56 | does anything need my attention? | 140 | 0 |
| 10-15 01:41 | is there anything that needs my attention? | 140 | 0 |

Only 24 of the 144 items were ever named in a reply to one of these requests. The reply on 2026-10-15 opened with
"nothing urgent". The typical item had 12 of these requests made after its wait began. The replies are in
`data/status-asks.json`.

## 5. The parking of 2026-10-10

At 19:05 the person, busy with the teaser's music, asked the orchestrator to put their "debt of questions and answers"
in order and to say what the teaser needed from them. The reply had three parts:

- the teaser's decisions;
- "Decided myself, unless you object": which seat does what next;
- "Put off until after the teaser (recorded, I will not remind you)": the geology preview and a bench on the person's
  1060, one yes for the light7 group, the conventions waiting on the person, and the morning note's questions, all in
  `MORNING-2026-10-10.md`.

At 19:13 the person answered, quoting the second part: "'Decided myself, unless you object' - yes, all ok, thanks for
splitting the work". The same minute the orchestrator saved a memory, `triage-asks-when-busy.md`, recording that the
person had confirmed all three parts, the parking without reminders included. The person's words did not cover that.

The parking made no ledger move. Between 19:00 and 21:00 the orchestrator recorded one release, one adopt and three
assigns, and no `hold`. The rows kept `waits on the person`. The parking lived in the orchestrator's memory and a file.
For comparison, 28 of the 144 rows were held correctly at other times, `hold --until "the person"`.

## 6. Why flotilla let it happen (code at 0.25.0; this path has not changed since the snapshot)

1. **The orchestrator hears of an item once.** `flotilla watch --wait` wakes only on an item that was not there at its
   start (`flotilla/watch/commands.py`, `_wait`). The prompt hook tells the orchestrator only new items, and folds the
   rest into "and N standing item(s), said before and unchanged" (`flotilla/hooks.py`, `_prompt`, via
   `watch/throttle.py` `fresh`). "Said before" means said to the orchestrator's session, not to the person.
2. **Nothing records that the person was told.** Taking an item to the person is a free action of the orchestrator,
   with no move, no record and no check. `watch` cannot tell "waits on the person, told an hour ago" from "waits on
   the person, never told".
3. **Parking has a ledger move, but nothing requires it.** `work hold --until "the person"` exists and is in the
   orchestrator's post (since 2026-10-06), but an orchestrator can park by saying so, and the ledger goes on saying
   the row waits on the person.
4. **A status request by the person checks nothing.** The orchestrator's post says "Brief the person from the ledger,
   never from memory" (since 2026-09-24). On each request the hook gave it a number. It answered from its context six
   times running, and never ran `watch --once`.
5. **One number for four kinds.** `watch` lists waits on the person together with a missing sender and dead seats.
   The orchestrator itself turned 219 lines into "220 pending items" and offered the number as the person's
   perfectionism.
6. **A wait passes to the person silently.** When the session waited on is gone, the wait moves to the person
   ("orchestrator 3 is not alive, so the wait moves to the person"), and nobody tells them. *Checked against the code
   on 2026-11-03: flotilla has no such rule. A wait whose session is gone is lifted (`views.wait_over`, "gone") and
   the row reads as a move nobody holds; the quoted line is a note a session wrote on its own row. The effect is the
   same for the person, but the cause is a session's convention, not flotilla's.*

Both prompt rules that would have prevented this were in force. Nothing enforced them, so the guarantee that what
waits on the person reaches the person rested on prompt text alone.

## Caveats

- **Named by identifier undercounts what was said.** An item put to the person in prose only counts as not named;
  P3 + P4 is an upper bound.
- **Named in a transcript is not the same as read.** A line in a transcript the person typed in is not proof they
  read it; the orchestrators ran in the background between the person's visits.
- **The request search is a pattern.** A request worded in a way the pattern did not foresee is missing from section 4.
- **The P2 split is a sample.** Only 12 items were read for request versus report.
- **One machine, one project.** This was measured on twosuns alone. void-lanes-online and game2048 have ledgers on
  the same machine and were not read.

## What could follow (not decided)

- **A queue for the person**, e.g. `flotilla inbox`: only the rows waiting on the person, grouped (a yes to merge, a
  check by ear or GPU, a question), with age and when each was last put to them. Read from the ledger every time,
  never only on change.
- **Putting an item to the person is a move.** For example `work told <branch> --via chat`, or one move for a
  shown `brief`. `watch` then raises a new kind: "waits on the person, never told", and "told N days ago, no answer".
- **Parking needs the person's yes.** A person-wait leaves the person's queue only by a `hold` the person approved.
- **The hook keeps person-waits apart.** They are never folded into "standing". On the person's prompt in the
  orchestrator's session, the person-wait count is given on its own line, with the instruction to answer from
  `inbox`.
- **Separate counts in `watch`**: waits on the person, moves nobody can make, gone sessions.
- **A wait moved to the person on a session's death is itself a person item.**
- **The regression test**, failing first: the ledger of this snapshot, the person's 18 requests, 104 rows waiting on
  the person and none told since 10-10, while `watch` stays silent.

## Data (kept outside the repository)

The data and scripts stay on the machine the trace was made on: the snapshot and the replies are in Russian and carry
the project's working text. The list below describes that folder.


- `data/watch-once-2026-10-16T2023.txt`: the orchestrator's `watch --once` output, verbatim. It contains Cyrillic.
- `data/items.csv`: one line per attention line (218), with:
  - class;
  - snapshot line;
  - when the current wait on the person began, and who recorded it;
  - the first ever wait on the person;
  - when the item was first named and first named after its wait;
  - how often it was named, through which channels, and when last;
  - whether it is in the MORNING note;
  - how many of the person's status requests came after its wait, and in which replies it was named.
- `data/status-asks.json`: the 18 requests and the start of each reply. It contains Cyrillic.
- `data/MORNING-2026-10-10.md`: the parking note.
- `scripts/`: `audit220.py` (snapshot × ledger × channels), `asks.py` (requests and replies), `table.py`
  (classes, CSV), `holds.py` (holds and the orchestrator's moves on 10-10), `sample.py` (the 12-item sample). Their
  paths point at this session's scratchpad and at the original transcripts; adjust `S` and `SNAP_FILE` to rerun.
