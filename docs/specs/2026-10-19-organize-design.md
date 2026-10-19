# Organize: taking in work the ledger never saw - design

Date: 2026-10-19. Status: revised after two specialist reviews (section 15), for the person's review. Agreed with
the person in the session of 2026-10-19, part by part. Marked **(session's call)** where the session decided and
the person has not confirmed.

## 1. What it is for

A project rarely meets flotilla empty. Work started before the plugin - open pull requests, branches, commits on
the local trunk, edits never committed - is invisible to the ledger, and the fleet only moves what the ledger
carries. Since 0.19.0 onboarding names open pull requests no ledger row knows and offers a `claim` command for the
first of them. That is a warning, not a way through: it says nothing of anything but pull requests, its command
leaves the row's base empty when the branch exists only on origin, and it is wrong for stacked pull requests.

**Organize** walks everything of that kind in the repository, asks the person what to do with each piece of
**their own** work, and does it: take it into the ledger, ignore it, or what the person describes in words. Work
that is not theirs - another person's or another AI's - is shown and not touched. Organize runs inside onboarding
and as its own skill, `/flotilla:organize`, at any later time.

**The person's scenario (the measured case, `echoes-of-the-ancients`, 2026-10-19).** The person installed flotilla
in a project worked on for two months. Onboarding (0.17.0) wrote a profile on branch `chore/flotilla-onboard` and
stopped short of publishing; the same branch carries a second commit of the person's (`4eb46da`, task T-053 in the
project's docs) that conflicts with the open pull requests. On GitHub three pull requests stood stacked since
2026-09-13: #42 `docs/relations-model` into `main` (19 commits of its own), #43 `chore/serena-enforcement` into
#42's branch (5), #44 `docs/value-axes` into #43's branch (12; 36 above `main` in all). The repository does not
delete branches on merge. A second worktree, `echoes-of-the-ancients-codex-skills`, stands on a branch with no
commits of its own, 60 behind `main`, holding uncommitted edits and Codex files (`.agents/`,
`CODEX-TO-CLAUDE.md`). The main tree has an untracked `.serena/`. Onboarding said nothing of the pull requests; no
ledger file for the project existed.

With organize, the person sees, before publishing:

```
before publishing:
  .serena/ - a tool's cache, untracked: add it to .git/info/exclude?
  chore/flotilla-onboard also carries 4eb46da "docs: open T-053 ..." (yours) -
    it conflicts with #42 and #44: land it with the profile, move it to its own branch, or your words?
```

and after publishing:

```
your work the ledger does not know:
  a stack of 3 pull requests (bottom first):
    #42 docs/relations-model     -> main                      19 commits of its own
    #43 chore/serena-enforcement -> docs/relations-model       5
    #44 docs/value-axes          -> chore/serena-enforcement  12
not touched (not yours, or another AI's - name it to take it in):
  tree echoes-of-the-ancients-codex-skills - uncommitted edits, .agents/, CODEX-TO-CLAUDE.md
already on main, may be deleted: feat/spike-factors
```

On "separate works" three ledger rows appear, each reviewed over its own commits, landing in order.

## 2. Scope: the person's own work

- **Organize takes in only the work of the person running it** (the person, 2026-10-19). Everyone else's work -
  another person's or another AI's - is shown and never touched, unless the person names it explicitly
  (`/flotilla:organize <branch or #pr>`). Claude sessions working for the person count as the person.
- **The repository the profile describes**, and its worktrees. Other repositories of a multi-repo profile are
  walked each on its own (session's call).
- **Not**: rewriting history the person did not ask for, closing or merging anything on GitHub without a yes,
  judging the quality of taken-in work (review's job once it is a row).

## 3. What organize finds

It starts with `git fetch --prune`, so nothing is judged from stale remote refs. Each kind below is found by asking
git and GitHub, never by memory of earlier runs (except the ignore record, section 8).

| kind | found by | asked about |
|---|---|---|
| open pull requests | `gh pr list --state open` with number, head, head sha, base, author, draft flag, fork flag, title (0.19.0's `detect_prs`, extended) | yes |
| stacks | a pull request whose base is another open pull request's head | yes, as one item |
| branches with commits not on trunk and no open pull request (on origin or local only) | refs against trunk | yes |
| commits on the local trunk that origin's trunk lacks | `git rev-list origin/<trunk>..<trunk>` | yes (section 6) |
| commits on onboarding's branch beside the profile commit | the branch's range minus the commit that adds `.flotilla/project.toml` | yes (section 6) |
| uncommitted edits in a worktree | `git status --porcelain` per worktree | yes (section 7) |
| tool caches (`.serena/` and the like) | untracked directories a known tool writes | yes, before publish only: exclude or leave (section 9) |
| branches already on trunk | `git branch --merged`, plus patch-id against trunk (`git cherry`, or flotilla's `patch_fingerprint`) and merged pull requests from `gh`, so squash- and rebase-merged branches are recognized | no - listed as "may be deleted" |

Excluded always: trunk, branches an open ledger row knows, flotilla's own refs (`refs/flotilla/...`), and onboarding's
profile commit (identified by the commit that adds `.flotilla/project.toml`, since nothing records the branch).

**Whose work it is.** The person is identified by `git config user.email` and the GitHub login (`gh api user`).

- A pull request is theirs when its author is the person; a branch, when its own commits (fork point..tip) are
  authored by the person.
- Positive signs that a Claude session wrote it for the person: `Co-Authored-By: Claude` or `Claude-Session:`
  trailers, `claude/*` or `worktree-*` branches, trees under `.claude/worktrees/`, "Generated with Claude Code" in a
  pull request body.
- Signs of another AI: `.agents/`, `.codex/`, files named `CODEX-*`, and other tools' trailers - counted only where
  the branch's own range adds or changes them, or where they are untracked or modified in a tree, never because
  trunk already carries them. **`AGENTS.md` alone is not a sign**: Claude Code reads it as project instructions
  since v2.1.277 (2026-09-18).
- Work that mixes authors, or carries another AI's signs, counts as not the person's (session's call): shown, taken
  in only when named.

**Trees in use are not touched.** A tree is in use when `git worktree list --porcelain` shows it `locked` (Claude
Code locks its agent and background trees; flotilla locks its seat trees) or it is a flotilla seat tree. The
census is an extra signal only: flotilla's seats all report the main checkout as their directory. The tree the
organizing session runs in is handled explicitly (section 7).

**GitHub's native stacked pull requests** (generally available 2026-10-06) are recognized as such and handled as
section 5 says.

## 4. The answers

For every item asked about:

- **Take in** - bring it into the ledger. What that means per kind is in sections 5-7.
- **Ignore** - leave it; flotilla does not touch it and does not ask again (section 8). "Ignore the rest" settles
  every remaining item at once.
- **Own words** - the person describes what they want ("#43 is stale, close it", "I merge #42 myself now"). The
  session reads it and proposes concrete steps.
- **No answer** - a dismissed or timed-out question changes nothing; the item is asked about again next time
  (the person, 2026-10-19). A default never moves a row or touches GitHub. (In Claude Code a timed-out question
  lets Claude "proceed on its own judgment"; the skill says plainly that here that judgment is "change nothing".)

**What happens at once and what waits for a yes.** Moves that live only in the ledger are made at once. Anything that
leaves it - closing, merging or retargeting a pull request, deleting a branch, moving trunk, committing the
person's files - is shown as the exact command and done on the person's yes; an answer that names such an action
is that yes for exactly the actions it names (sections 5 and 7). The commands that act outside the ledger are kept
out of the skill's `allowed-tools`, so each asks the person's permission prompt too. **Dropping commits, the one
answer that loses work, is never run by the session**: it prints the command and the person types it after `!`, as
`work approve` already does.

## 5. Pull requests, and stacks

**How a taken-in row enters the ledger (session's call, after review).** As work in progress (`claimed`), owned by a
reserved owner name that no census lists, which the orchestrator gives to a seat the way it gives an orphaned row.
The seat that takes it runs the handover tiers over the tip and hands it over; review then follows the profile's
`review.depth`, with the row's post named so `main-only` is decided, not accidental. Why not "handed" at once: a
draft pull request or a spike is not finished, and running every tip's tiers inside onboarding is slow (three runs
of the test bed's gate). No guarantee is weakened: the handover receipt is never skipped.

Each row carries its pull request's number from the start, so the sender does not open a second one, and its base
as a commit sha, never a branch name and never empty. A branch present only on origin is first created locally at
the pull request's head sha; a local branch that differs from that head is asked about.

**Accounting starts at the adoption point.** Commits under a taken-in row's base date from before flotilla; they
must not surface as "direct commits" for the person to vouch for (`findings._horizon`), nor count as read by the
row's verdict when the base is far back (`batch.py`).

**Before the person picks a shape**, organize checks each tip merges cleanly with trunk (`gitq.merges_cleanly`) and
names predicted conflicts, with trunk as it will stand after publishing.

A single pull request, taken in, is one row; base = its fork point from trunk.

**A stack is asked about once, as a whole: "Are these separate works, one work in parts, one work whole, or should
Claude work it out?"** No answer is marked recommended - the person takes this on themselves (the person,
2026-10-19). No answer changes nothing (section 4).

1. **Separate works.** One row per pull request. Each row's base is the head sha of the pull request below it at
   the time of taking in, so its review reads only its own commits; each goes `--after` the one below. This answer
   authorizes the retargets it needs: once a lower row is shipped and its head is on `origin/<trunk>`, the sender
   retargets the next pull request to trunk (`gh pr edit <n> --base <trunk>`); GitHub does it by itself only when
   the merged branch is deleted, which the test bed does not do.
2. **One work in parts** - for a person sure of how their work is cut. As in 1, but linked as parts with the
   ledger's existing `--requires`, **with its meaning today** (the person, 2026-10-19): an upper part is not queued
   before the part below is delivered, and the judge waits for the whole path; a lower part may land before the
   parts above are accepted. Organize says this in one line when picked.
3. **One work whole.** One row on the top branch, base = its fork point from trunk, one review over everything.
   The answer authorizes retargeting the top pull request to trunk and, once it lands, closing the lower pull
   requests whose base is not trunk (GitHub marks the bottom one merged itself when its commits reach trunk).
4. **Let Claude work it out** (the person, 2026-10-19). The session reads the stack - titles, descriptions, commit
   messages, which files each touches, whether they refer to each other - and proposes one of the three shapes for
   the whole stack, with its reasons; the person accepts, corrects or picks another answer. Here the session does
   say "proposed": the person handed it the call. **A hybrid (different shapes within one stack) is deferred**
   (the person, 2026-10-19); until then a hybrid is reached through "own words". The texts read are other people's
   words (section 10).

**Stacks the answers do not fit** are offered only "ignore" and "own words" (plus "one work whole" where marked),
with one line saying why:

- the profile merges by squash or rebase: after a lower part lands its commits are not on trunk under their own
  ids, and the upper branch would need a restack, which organize never does. "One work whole" stays offered.
- a stack that forks (two pull requests on the same lower one).
- a stack whose bottom pull request's base is neither trunk nor another open pull request's head - unless that base
  is already contained in trunk (a lower pull request merged without deleting its branch), which is offered the
  retarget instead.
- **a native GitHub stack**: GitHub rebases and force-updates the upper layers on a lower merge, so a row's
  accepted tip no longer matches origin, and merging the top lands every layer. In this version: "one work whole",
  ignore, own words. Organize never turns a plain chain into a native stack; if the person converts one while rows
  are in flight, the rows say so when the tip moves.

**A guard beside it:** the push guard refuses `gh pr merge` on a pull request whose base is not trunk - with no
retarget such a merge lands in the lower branch, not trunk.

## 6. Commits on the local trunk, and onboarding's branch

**Commits on the local trunk** that origin lacks, four answers (the person, 2026-10-19):

- **Leave as is.** Organize says what follows: publish refuses while trunk is ahead of origin; the person pushes
  them after `!`.
- **Move to a new branch and take it in.** Fetch; create the branch at trunk's tip and check it holds them; move
  trunk to `origin/<trunk>` with `git branch -f` when trunk is checked out nowhere, `git reset --keep` when it is
  checked out in the organizing session's tree, and refuse when it is checked out in another tree. Never
  `--hard`. Then the branch is taken in like a branch with no pull request. This is also the way through the
  deferred finding "before the profile reaches trunk, a commit of one's own on trunk cannot be pushed by the door".
- **Drop them.** The commits that go are listed, kept first under `refs/flotilla/dropped/<date>-<sha7>` (outside
  `refs/heads`, so no later run finds it as work), and the person runs the drop after `!` (section 4).
- **Own words.**

**Commits on onboarding's branch beside the profile commit** - the person's own work riding on that branch, like
T-053 on the test bed - are asked about the same way, with "land it with the profile" in place of "leave as is"
(session's call). Conflicts they would bring into trunk against open pull requests are named (section 5).

## 7. Uncommitted edits

On take in, the session reads the edits and proposes splitting them into branches by what they are about:

```
uncommitted edits - looks like three different changes:
  1. fix/tone-scale        spike/engine/tone.py, tests/test_tone.py
  2. docs/q135-fleet       docs/questions.md, docs/tasks.md
  3. ? not understood      notes.txt
```

then asks (the person, 2026-10-19): **organize at Claude's discretion**, **leave as is**, or **the person says what
goes where**. "At Claude's discretion" shows the plan once and carries out exactly that plan (session's call, after
review); each resulting branch is one row, taken in as in section 5.

How it is done, so no edit is lost (session's calls, after review):

- **First, keep everything**: the whole dirty state goes under `refs/flotilla/kept/<date>-<sha7>` (`git stash
  create` + `update-ref`), staged and unstaged, before anything moves.
- **Where branches start**: a tree on trunk - at trunk's tip; a tree on another branch - the edits belong to that
  branch's work and are offered with it, and the new branches start at its tip.
- Each branch's commit is made with plain `git commit` in a tree of its own, so the repository's hooks run; ignored
  files are never added.
- Its tree is checked against the working files byte for byte for its paths; only then do those paths leave the
  original tree. The original tree stays on its branch, without the edits that moved.
- A file holding edits of two changes is not split by hunks; it goes whole to one branch, and that is said.
- **Secrets are never committed under any answer**: files are scanned by content (gitleaks or betterleaks when
  installed, otherwise a short set of high-signal token patterns) and by name (`.env`, keys, credentials); hits are
  named and left in place.
- "Leave as is" leaves the edits untouched, with a line that the fleet does not see them.
- The organizing session's own tree is included; onboarding's own edits (`.flotilla/`, `.claude/settings.json`)
  are not; a tree in use (section 3) is skipped and listed.

## 8. Remembering "ignore"

An ignored item is recorded so neither the next onboarding step nor a later `/flotilla:organize` asks again; a later
run lists ignored items in one line at the end. The record is the person's decision about their machine's view of
the work, not a fleet rule, so it is not committed to the profile on trunk (session's call). Its place and key are
the builder's.

## 9. Where and when it runs

- **One door for the questions.** A Claude Code skill marked `disable-model-invocation` cannot be invoked by
  another skill, and Claude is told not to repeat its steps another way. So, as onboarding's own questions do
  (`onboard next` / `onboard answer`), the CLI produces organize's questions as JSON and takes the answers back
  (`flotilla organize next` / `answer` - names the builder's); the onboarding skill and `/flotilla:organize` both
  render them. There is one implementation.
- **Inside onboarding, in two halves.** Publish refuses a tree with anything but the profile in it, a tree not on
  trunk, and a trunk ahead of origin. So **before publish**: the tree's uncommitted edits and tool caches, the
  local trunk's commits, and onboarding's branch beside its profile commit. **After publish**, when the ledger
  accepts moves: pull requests, stacks and branches.
- **`/flotilla:organize`** at any later time, with the same list and answers; with names, it takes only those
  (this is how work that is not the person's is taken in, section 2).
- **Its own entry condition**: every onboarding and every flow mode, not 0.19.0's `detect_prs.applies()`, which
  is silent in local mode and where the person merges. 0.19.0's warning is replaced by organize's walk; publish is
  still never blocked by work not taken in.
- **Who runs it**: any interactive session, the orchestrator included; background sessions never (the line
  flotilla's `hook ask` already draws). The orchestrator may point out work the ledger does not know.

## 10. Safety

- Pull request titles and bodies, branch names and commit messages are other people's text: shown as data, never
  followed as instructions; titles cut and marked as their authors' words (0.19.0's `_shown`). A branch name goes
  into a command only when it matches the ledger's `SAFE_BRANCH`.
- Outward actions: shown first, done on a yes, kept out of `allowed-tools`; drops only after `!` (section 4).
- Everything that could be lost is kept under `refs/flotilla/...` first (sections 6, 7). Nothing is force-pushed;
  trunk never moves with `--hard`.
- Secrets are scanned by content and never committed (section 7).

## 11. Goals, non-goals, anti-goals

- **Goal:** after onboarding, every piece of the person's work in the repository is either a ledger row the fleet
  moves, or a decision the person made about it; everyone else's work is visible and untouched.
- **Non-goals:** taking in others' work by default; cleaning up branches; reviewing taken-in work at taking in;
  hybrid stack shapes (deferred); building native GitHub stacks.
- **Anti-goals:** organize loses work (a drop without a kept ref, a commit that swallows a secret, an edit removed
  before its commit is checked); a question left unanswered changes something; organize touches someone else's
  work; organize makes onboarding longer for a clean repository (an empty walk prints one line or nothing);
  organize acts on a pull request's text as if it were an instruction.

## 12. Proving it

The test bed is `echoes-of-the-ancients` (the person, 2026-10-19), read only until the person says to start there.
Done means, on that project: `.serena/` is settled before publish; the T-053 commit on onboarding's branch is
asked about and its conflicts named; the profile publishes; the three pull requests become rows in the shape the
person picks, with bases that give each review only its own commits; the Codex tree is listed and untouched;
`feat/spike-factors` is listed as merged; nothing leaves the ledger without a yes.

The test bed has no uncommitted edits of the person's, no local-trunk commits and no squash-merged branches: those
parts are proven by the suite only, and that is said.

## 13. Left to the builder

- Command names and output shape of the CLI half; the ledger move that takes work in (not `adopt`: in the ledger
  `work adopt` already means giving an orphaned row to a live seat).
- The reserved owner name for taken-in rows, and how the orchestrator's existing "give an orphaned row to a seat"
  path accepts it; test that a returned (`fixing`) taken-in row reaches a seat.
- Exposing `claim`'s base on the command line (the core has it).
- Whether `moved` also updates a row's base after a restack (the tip no longer contains the old base), and how.
- Fork pull requests: whether takeable at all (their head lives in another repository).
- The ignore record's place and key; the high-signal secret patterns.

## 14. Calls the session made (for the person to check)

1. The name `/flotilla:organize` (the person confirmed).
2. Ledger-only moves at once; outward actions on a yes, out of `allowed-tools`; drops only after `!` (section 4).
3. Taken-in rows enter as work in progress under a reserved owner; a seat runs the tiers and hands over (section 5).
4. Bases are commit shas, never empty; accounting starts at the adoption point (section 5).
5. Conflicts with trunk predicted before the shape is picked (section 5).
6. "Separate works" and "one work whole" authorize the retargets and closes they need (section 5).
7. Squash/rebase, forked, off-trunk and native GitHub stacks get the narrower answers; a guard refuses
   `gh pr merge` into a non-trunk base (section 5).
8. "Let Claude work it out" proposes one shape for the whole stack (section 5).
9. Onboarding's branch beside its profile commit is an item, with "land it with the profile" (section 6).
10. Trunk moves by `branch -f` or `reset --keep`, never `--hard`; kept refs under `refs/flotilla/` (sections 6, 7).
11. Uncommitted edits: keep everything first, commit with hooks in a tree of its own, check bytes before removing,
    scan content for secrets; "at Claude's discretion" shows the plan once and does exactly it (section 7).
12. Mixed-author work and work with another AI's signs count as not the person's; `AGENTS.md` alone is not a sign
    (section 3).
13. Trees in use detected by worktree locks and seat trees, not the census (section 3).
14. The ignore record is not committed to the profile (section 8).
15. Multi-repo profiles walk each repository on its own (section 2).

## 15. Reviews

- **A fresh builder's read** (2026-10-19, before the person's first review): six questions, settled as the session's
  calls then; the meaning of "one work in parts" was the person's (today's meaning kept, "let Claude work it out"
  added).
- **Two specialist reviews** (2026-10-19, after the person accepted the first version): a Claude Code plugin
  specialist and a code-review / delivery / git specialist, each first checking how things stand in October 2026
  (GitHub's native stacked pull requests GA 2026-10-06; Claude Code reading `AGENTS.md` since v2.1.277; skills
  marked `disable-model-invocation` not callable from another skill; a timed-out question letting Claude proceed on
  its own judgment), then the plugin and this design. Both: approve with changes. Their findings are folded in
  above. The person decided three (2026-10-19): no answer changes nothing; others' work - people's or AIs' - is
  not touched; hybrids deferred.
