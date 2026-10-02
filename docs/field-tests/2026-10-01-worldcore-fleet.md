# Field test 4: worldcore, flotilla 0.4.3

A fresh project set up the way README.md tells a newcomer, to try 0.4.x on a working fleet before the plugin
directory submission: the person-only moves (onboarding answers and write with `--confirm`, permission answers with
`--mark`, `work approve` where a person authorizes merges), the narrowed MCP set per post, retire's leftover handling.

## Setup

- Project: `worldcore`, a renderer-free physics core for generated worlds, seeded from twosuns `src/core` at
  `ac92dd4` (54 modules, 37 test files green, 458 tests); ten test files mixing core and game wiring set aside in
  `tests/twosuns-wiring/` as task 0. Brief: `docs/brief.md` in that repository.
- Origin: a local bare repository `/home/max/workspace/worldcore-origin.git`.
- Same machine as the running twosuns fleet (about 8.5 GB available at the start).

## Findings

W1. **README drift - installing at project scope does not commit a marketplace entry when the marketplace is already
known to the user.** README says `--scope project` "commits a marketplace entry that points at this repository, so
everyone who opens the project and trusts it installs flotilla from here". In worldcore,
`claude plugin install flotilla@flotilla --scope project` wrote only `{"enabledPlugins": {"flotilla@flotilla": true}}`
to `.claude/settings.json`; the marketplace had been added at user scope first, as the README's own first command
does. A teammate opening the project on another machine gets no marketplace to install from. To check: what a
project-scope install writes on a machine without the marketplace; then the README says what is true, or the install
step adds `extraKnownMarketplaces` to the project settings by hand.

W2. **gap - the composition onboarding suggests has no orchestrator and no sender.** `suggest_composition` writes
`[fleet.default] main = 1, review = 1, judge = 1` (onboard/questions.py), and `spawn` warns about a missing
orchestrator or sender only above `SOLO_ABOVE` sessions (fleet/compose.py). In worldcore the person answered
"ask" for permissions and "a human, per batch" for merges: a fleet raised from that default has nobody to route a
permission question to (each is refused at once), nobody who may `queue`, `land` or `ship`, and nobody to show the
person a batch to approve. README's step 2 says `/flotilla:spawn` with no arguments "raises the composition onboarding
suggested". Fix direction: the default includes an orchestrator whenever permissions are `ask` or merges need a
person, and a sender whenever work reaches trunk through the fleet; spawn refuses, not warns, a fleet that cannot
deliver.

W3. **defect - the model question accepts free text that becomes a broken `--model`.** The person answered the
`model` question in words ("orchestrator and reviewers on Opus, the rest on Sonnet"); the onboarding session saw that
`flotilla` would pass the whole text to `claude --model` and no seat would start, and worked around it by writing
`model: claude-opus-5-5` into orchestrator.md and reviewer.md and `[fleet] model = "claude-sonnet-5-5"`. Nothing in
flotilla refused the answer. Fix direction: validate the answer as a model name or an option value, and offer the
per-post split the person asked for ("strongest for the orchestrator and reviewers") as an option.

W4. **friction - the line-edit guard refuses `sed -i '0,/re/s//x/'`.** The onboarding session's edit was refused as a
line-number edit; `0,/re/` is GNU sed's "up to the first match", addressed by content. The session fell back to its
edit tool, so nothing broke, but the refusal is wrong for that form. Fix direction: read `0,/re/` (and `N,/re/` where
the end is a pattern) as content-addressed, or say in the refusal that a pattern-ended range is allowed in another form.

W5. **friction - the next steps onboarding prints mislead a project with no CI.** The session told the person to
commit `.flotilla/` (but not to push it - rules are read from origin's trunk) and to set a CI gate command "or nothing
checks shipped", for a project whose origin is a local bare repository with no CI at all; the CI question offered
"set it later" but no "there is no CI". Fix direction: the closing message says "commit and push `.flotilla/` to
origin's trunk", and the CI question has a "no CI" answer that the profile and `ship` understand.

W6. **friction - another plugin's review sessions read as the person's sessions.** Committing in worldcore from a
session with the `security-guidance` plugin started two short Agent SDK sessions (`entrypoint: sdk-py`, about 30
seconds each) in worldcore's directory, to review the diff. The census listed them as `interactive`, named
`worldcore-xx`, beside the person's one session; the person asked why three sessions were open when they had opened
one. They hold no post, so `--fill` does not count them, but `flotilla fleet` and the check after `/rename` show
them as live interactive sessions of the project. Fix direction: `flotilla fleet` marks a session whose transcript's
entrypoint is an SDK one as a tool's service session, not a person's.

W7. **friction - quick onboarding asked for the yes without saying the answers.** On 0.5.0 the session ran
`onboard quick`, then `onboard write`, and asked "Write this profile? Confirming runs `npm test` once" - the answer
list was only in the raw command output. Quick had set `merge_auth: sender`: merges into trunk go without the person's
approval. That is the recommended answer, but it is the one choice a person must hear in words before saying yes.
Fix direction: the skill shows the answer list in its own message before the question, and names who authorizes
merges in plain words; `quick` could print a one-line meaning beside each answer.

W8. **friction - the lead offer promises approvals the profile does not ask for.** After publish (which worked on the
first try: `.flotilla/` and the settings' marketplace entry committed, `npm test` green over that commit, pushed to
origin's main) the skill offered "This session leads it - ... answer its permission questions and approve its merges
here", while quick had set `merge_auth: sender`, so no merge waits on the person. Fix direction: the skill reads
`flow.merge_authorized_by` and mentions approvals only when it is `human`.

W9. **defect - `/rename` does not wake the session, so leading stalls.** `spawn --lead` reserved `orchestrator 5` and
the session asked the person to type `/rename orchestrator 5` "as your next message", then to wait while it checked
and raised the rest. The rename took effect (the census lists `orchestrator 5`), but `/rename` is a local command:
the model got only a system note, no turn, so it never checked the name or ran `spawn --fill`. The person sees an
idle session and no fleet. Fix direction: the skill asks for `/rename orchestrator N` and then any message ("done");
`spawn --fill` run before the rename could also refuse with that instruction instead of raising seats beside an
unnamed orchestrator.

W10. **defect - the one-copy check counts every project's sender.** After `/rename` and a nudge, `orchestrator 5` ran
`flotilla spawn --fill`; it was refused: "post `sender` writes one-copy resources and is held by one session at most;
this would make 2 (1 alive)", the one alive being twosuns' `sender 9`. 0.5.0 scoped `--fill`'s count to the project
(review finding M3) but not `spawn.plan`'s one-copy check, which still counts the machine's census. A sender's
one-copy resources (trunk, the batch) are its project's. The refusal also listed all sixteen live sessions of the
machine, which sent the orchestrator into twosuns' sessions and flotilla's code to find out why. Fix direction: the
one-copy check counts this project's members, and the refusal lists only them.

W11. **friction - seat numbers run across every project on the machine.** The names journal is one per machine, so
worldcore's first orchestrator is `orchestrator 5` and its seats continue twosuns' numbers; to understand a name or a
refusal the orchestrator read other projects' sessions and the journal. Session names are machine-wide addresses
(messages are delivered by name), which is why the journal is machine-wide. Fix direction (the person's request):
number per project and carry the project in the name, so names stay unique on the machine while each project counts
from 1.

W12. **friction - the skill's "say the answers in words first" is not followed (0.6.0).** The 0.6.0 skill tells the
session to show the quick answers in plain words and name who authorizes merges in its own message before asking to
write (the W7 fix). The fresh session ran `quick`, then `write`, and asked "write this profile? it runs `npm test`
once" with no message between: the person again did not hear that merges go to trunk without them. An instruction
the model may skip is not a guarantee. Fix direction: the command prints it - `onboard quick` and `write` end with a
plain-words summary (who merges, what is reviewed, what asks the person), so it reaches the person through the
command's output whatever the model retells.

W13. **defect - seats raised before the lead's name lands are blocked by the broker (0.6.0).** The skill runs
`spawn --fill` right after `spawn --lead`, so the seats start while the leading session still carries `worldcore-0c`.
The profile asks the person for every permission; each seat's first Bash call (`flotilla status`) went to the broker,
which looks for a live session named by the orchestrator post, found none, and refused: "no live orchestrator to put
this question to". The seats could not take a step until the person's next message renamed the leader. Fix
direction: the broker counts a recorded lead of a live session of the project as the orchestrator (as `--fill` does).

W14. **defect - seats learned the leader's old name.** Raised in that gap, the seats addressed their first letters to
`worldcore-0c`; once the hook renamed it, "No agent named 'worldcore-0c' is reachable". Fix direction: with W13 fixed
the seats ask the census for the orchestrator post's holder, and a recorded lead answers with its coming name.

W15. **friction - `flotilla fleet` does not list the leading session.** It lists spawned seats' post rows; the person's
own session holds the orchestrator post without one, so the orchestrator concluded it was missing from the fleet and
read flotilla's source to find out why. Fix direction: `fleet` lists the live holder of the orchestrator post (or a
recorded lead) as the orchestrator, marked as the person's session.

W16. **friction - quick's `permissions: ask` puts every seat's flotilla call to the person.** Quick recommends `ask`
("every permission asked of you"). In a running fleet each background seat's Bash call goes through the broker to the
orchestrator and to the person - including flotilla's own `status`, `fleet` and `work show`; the person was asked
six times in a minute while three seats took their first census. A person's yes on a flotilla command protects
nothing: every ledger move is checked by flotilla itself (post, evidence, revision). Fix direction: seats run
flotilla's own command line without a question (an allow rule written at spawn for the CLI path), and quick
recommends a mode that asks the person only what the classifier finds risky.

## What 0.5.0 does about them

- W1: the README adds the marketplace with `--scope project`, so the project's settings carry the marketplace entry
  the README promises (decision 194).
- W2: the suggested composition always carries an orchestrator, a sender and a main session (decision 190).
- W3: a model answer must be a model name; a sentence is refused with the options named. The per-post split stays
  the existing "strongest reviewer" option; an orchestrator on a stronger model is a post's own `model` line.
- W4: `0,/re/` and `1,/re/` pass the line-edit guard; a counted start (`3,/re/`) is still refused (decision 193).
- W5: quick onboarding answers "no CI" where there is no GitHub CI, and onboarding publishes the profile itself:
  `flotilla onboard publish` commits, runs the tiers over the commit and pushes trunk; `write` names it (decision 190).

Beyond the findings, onboarding was simplified on the person's request: quick or custom, one yes, and an offer to
lead the fleet from the onboarding session (decisions 190-192).

## What 0.6.0 does about W6-W11

- W7, W8: the skill shows the quick answers in words, says who authorizes merges, and promises approvals only where
  the person gives them (decision 197).
- W9: no `/rename` - `spawn --lead` records the lead and the prompt hook names the session on the person's next
  message; `--fill` counts it meanwhile (decision 197).
- W10, W11: seat names carry the project (`worldcore-orchestrator 1`), numbers run per project, and spawn's one-copy
  check and its refusal see only the project's sessions (decision 196).
- W6 waits (ruling in decision 197).

## What 0.6.1 does about W12-W16

- W12: `onboard write` prints the profile in plain words before the yes; the skill shows those lines (decision 199).
- W13, W15: a leading session not yet named is the orchestrator for the broker and for `permit answer`, and
  `flotilla fleet` lists it.
- W14: left to W13's fix and to auto mode (ruling in decision 199).
- W16: quick answers auto mode; in `ask` mode flotilla's own safe commands pass without a question; `spawn` warns about
  auto mode on a model that was seen to lack it.

W17. **friction - `watch` alerts the leading session about its own question to the person.** The orchestrator's
`flotilla watch --wait` fired "worldcore-orchestrator 1 waits on the person (census: waiting); answer it in its
session" while the orchestrator was the one asking the person (fleet size, AskUserQuestion). For an interactive
leading session, waiting on the person is its normal state, not a deviation. Fix direction: `watch` does not report
the census state `waiting` of an interactive session - the person is in front of it.

W18. **friction - seats call flotilla in a form the own-command pass will not take.** The seats write
`cd <tree> && F=<cli path>; $F status; $F fleet`. 0.6.1's pass for flotilla's own commands takes only the plain
command line by its full path, and rightly refuses `&&`, `;` and `$F`, so in `ask` mode those calls still reach the
person. Under the auto default it does not matter. Fix direction: the `flotilla:flotilla` skill tells seats to run
the command line as the whole command, by its full path, one command per call, with `--root`/`--tree` instead of `cd`.

W19. **defect - a leading session waiting on the person starves the fleet's permission questions.** The orchestrator
(the person's own session) asked the person three API questions and waited on the answer. Meanwhile
`worldcore-sender 1` asked, through the broker, to read the `core-tests` row before pushing the accepted task 0;
the question sat in the queue, nobody showed it, and the broker refused it after 540 s - task 0 did not reach trunk.
A background orchestrator never waits on the person, so it never had this gap. Fix direction: the orchestrator post
checks `flotilla permit next` before every question it puts to the person and puts waiting permission questions in
the same AskUserQuestion call; and `auto` mode, now the default, routes no such questions at all.

W20. **friction - a late yes is thrown away.** Three of `worldcore-sender 1`'s permission questions in a row (to read
the `core-tests` row, to read it again, to `git fetch origin`) expired after 540 s while the person was answering the
orchestrator's other questions; the third time the person answered about a minute late and was told "question ... is
already closed: withdrawn no answer in time". Task 0, accepted, did not reach trunk for over half an hour. Fix
direction: an answer to a question withdrawn in the last minutes is kept and answers the same question when the same
session asks it again; and the auto default routes none of these.

W21. **defect - a hung run holds the machine's lane for every fleet, with no limit.** `worldcore-main session 4`
started the full vitest suite through `flotilla lane run` at 15:01; one worker sat at 100% CPU with no output for 18
minutes (the whole suite takes about 35 s; likely an infinite loop in the new weather code - a synchronous loop also
blocks vitest's own test timeout). The lane is one per machine (capacity 1), so behind it waited worldcore's handover
receipt for task 2 and twosuns' `sender 9` push receipt and `main session 21`: one project's hung test stopped another
project's delivery. Nothing flagged it - `lane` lists the holder as alive. Fix direction: a run in the lane has a
ceiling (per tier, from its measured time, with a margin) after which it is stopped and recorded as killed, not
green; `lane` and `watch` name a run past its expected time.

W22. **friction - stopping one's own hung run is left to `pkill`.** To free the lane (W21), `worldcore-main session 4`
ran `pkill -f` with a pattern that matched its own command line and killed itself first ("pkill killed itself: the
pattern matched my own command"). Flotilla knows the run's pid - `lane` prints it. Fix direction: `flotilla lane stop
<booking>` stops the holder's own run by the pid flotilla recorded, so no session has to find processes by text.
