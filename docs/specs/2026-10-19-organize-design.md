# Organize: adopting work the ledger never saw - design

Date: 2026-10-19. Status: for the person's review. Agreed with the person in the session of 2026-10-19, part by
part; this document collects those parts. Marked **(session's call)** where the session decided and the person has
not yet confirmed.

## 1. What it is for

A project rarely meets flotilla empty. Work started before the plugin - open pull requests, branches, commits on
the local trunk, edits never committed - is invisible to the ledger, and the fleet only moves what the ledger
carries. Since 0.19.0 onboarding names open pull requests no ledger row knows and offers a `claim` command for the
first of them. That is a warning, not a way through: it says nothing of anything but pull requests, and its
command is wrong for stacked pull requests.

**Organize** walks everything of that kind in the repository, asks the person what to do with each, and does it:
adopt into the ledger, ignore, or what the person describes in words. It runs inside onboarding and as its own
skill, `/flotilla:organize`, at any later time.

**The person's scenario (the measured case, `echoes-of-the-ancients`, 2026-10-19).** The person installed flotilla
in a project worked on for two months. Onboarding wrote a profile on branch `chore/flotilla-onboard` and stopped
short of publishing. On GitHub three pull requests stood stacked since 2026-09-13: #42 `docs/relations-model` into
`main` (19 commits of its own), #43 `chore/serena-enforcement` into #42's branch (5), #44 `docs/value-axes` into
#43's branch (12; 36 above `main` in all). A second worktree, `echoes-of-the-ancients-codex-skills`, held a branch 60
commits behind `main` with uncommitted edits and Codex files (`AGENTS.md`, `.agents/`, `CODEX-TO-CLAUDE.md`). The
plugin "did not pick the work up well": onboarding (0.17.0) said nothing of the pull requests, and no ledger file
for the project existed at all.

With organize, the person sees:

```
work the ledger does not know:
  a stack of 3 pull requests (bottom first):
    #42 docs/relations-model     -> main                      19 commits of its own
    #43 chore/serena-enforcement -> docs/relations-model       5
    #44 docs/value-axes          -> chore/serena-enforcement  12
  not touched (looks like another AI's - settle it with that tool):
    tree echoes-of-the-ancients-codex-skills, branch docs/codex-project-skills - AGENTS.md, .agents/
  already on main, may be deleted: feat/spike-factors
```

and is asked about the stack: adopt, ignore, or own words; on adopt, what the stack is (section 5). On "separate
works" three ledger rows appear, each going to review over its own commits only, landing in order.

## 2. Scope

- **Claude's ecosystem only.** Work that belongs to another AI tool is shown and never touched (section 3).
- **The repository the profile describes**, and its worktrees. Other repositories of a multi-repo profile are
  walked the same way, each on its own (session's call).
- **Not**: rewriting history the person did not ask for, closing or merging anything on GitHub without a yes,
  judging the quality of adopted work (that is review's job once it is a row).

## 3. What organize finds

Each kind below is found by asking git and GitHub, never by memory of earlier runs (except the ignore record,
section 8).

| kind | how it is found | asked about |
|---|---|---|
| open pull requests | `gh pr list --state open` with head, base, fork flag, title (0.19.0's `detect_prs`, extended with `baseRefName`) | yes |
| stacks | a pull request whose base is another open pull request's head | yes, as one item |
| branches with commits not on trunk and no open pull request (pushed or local) | `git for-each-ref refs/heads refs/remotes/origin` against trunk | yes |
| commits on the local trunk that origin's trunk lacks | `git rev-list origin/<trunk>..<trunk>` | yes (section 6) |
| uncommitted edits in a worktree of Claude's | `git status --porcelain` per worktree | yes (section 7) |
| branches wholly on trunk | `git branch --merged <trunk>` | no - listed as "may be deleted" |
| tool caches (`.serena/` and the like) | untracked directories a known tool writes | no - at most a hint to add them to `.gitignore` |
| another AI's work | traces: `AGENTS.md`, `.agents/`, `.codex/`, files named `CODEX-*`, in the tree or the branch's own commits | no - listed as "not touched (looks like another AI's)" |

Excluded always: onboarding's own branch, branches the ledger already knows (an open row's branch), trunk itself.
Fork pull requests are listed; whether they can be adopted at all is left to the builder (their head lives in
another repository) - at minimum they are shown and ignorable.

**Another AI's work is highlighted, never touched** (the person, 2026-10-19). Git keeps no record of which tool
wrote what, so the line is drawn by traces. A trace-matched branch is adopted only when the person names it
explicitly (`/flotilla:organize docs/codex-project-skills`); without the name it is listed again and left.

## 4. The answers

For every item asked about, three answers:

- **Adopt** - bring it into the ledger. What that means per kind is in sections 5-7.
- **Ignore** - leave it; flotilla does not touch it and does not ask again (section 8).
- **Own words** - the person describes what they want ("#43 is stale, close it", "I merge #42 myself now", "move
  #44 onto main and take it as one work"). The session reads it and proposes concrete steps.

**What the session does without asking and what it shows first (session's call).** Moves that live only in the
ledger (a row, a link between rows) are made at once. Anything that leaves the ledger - closing, merging or
retargeting a pull request on GitHub, deleting a branch, a reset, a commit of the person's files - is shown as the
exact command and done only on the person's yes, except where the person has already chosen "organize at Claude's
discretion" (section 7), which is that yes for the commits it covers.

## 5. Pull requests, and stacks

A single open pull request, on adopt, becomes one row: its branch, base = the fork point from trunk.

**A stack is asked about once, as a whole: "Are these separate works, one work in parts, one work whole, or should
Claude work it out?"** No answer is marked recommended - the person takes this on themselves (the person, 2026-10-19). **If the person gives
no answer, the default is "one work whole".**

1. **Separate works.** One row per pull request. Each row's base is the head of the pull request below it, so its
   review reads only its own commits. Each row goes `--after` the one below: the sender does not land #43 before
   #42 is on trunk. When the lower one lands, the upper row's base moves to trunk's new tip and its pull request's
   base becomes trunk (GitHub retargets it by itself when the lower branch is deleted on merge; otherwise the
   sender does it - builder's call how).
2. **One work in parts** - for a person sure of how their work is cut. One row per pull request, bases as in 1,
   each reviewed over its own commits, linked as parts with the ledger's existing `--requires`, **with its meaning
   today and no new rule** (the person, 2026-10-19): an upper part is not queued before the part below it is
   delivered, and the judge waits for the whole path; a lower part may land on its own before the parts above it
   are accepted. Organize says this in one line when the person picks it, so "parts" is not read as "they land
   together".
3. **One work whole.** One row on the top branch, base = the fork point from trunk, one review over everything.
   The top pull request is retargeted to trunk; the lower ones are closed as included once it lands. Both are
   GitHub actions and follow section 4's rule.
4. **Let Claude work it out** (the person, 2026-10-19). The session reads the stack - titles and descriptions,
   commit messages, which files each pull request touches, whether they refer to each other - and proposes how the
   work is cut, a hybrid where that fits, one line of reason per group:

   ```
   proposed:
     #42 docs/relations-model             - separate work: the relations model, a topic of its own
     #43 + #44 serena-enforcement,        - one work whole: #44 edits what #43 introduced,
               value-axes                   neither reads on its own
   ```

   The person accepts it, corrects it in words, or picks another answer. Here the session does say "proposed": the
   person handed it the call, so the no-recommendation rule (which is about the question itself) does not apply.
   A hybrid is built from the three shapes above (session's call). The texts it reads are other people's words
   (section 10).

**Stacks the three answers do not fit (session's calls).** These are offered only "ignore" and "own words", with
one line saying why:

- the profile merges by squash or rebase (`pr.merge_method`): after the lower part lands, its commits are not on
  trunk under their own ids, the upper branch carries them again and conflicts, and the way out is a force-push of
  that branch, which organize never does. "One work whole" stays offered: it lands one branch.
- a stack that forks (two pull requests on the same lower one): "the top branch" is not one branch.
- a stack whose bottom pull request's base is neither trunk nor another open pull request's head (a release
  branch, a branch with no pull request): the sender lands into trunk only, so the rows could never land.

**Adopted work enters by the ledger's existing rules, not around them (session's call).** No guarantee is
weakened for it:

- **The handover receipt is never skipped.** Organize runs the profile's handover tiers over each adopted tip (in a
  tree of its own, checked out at that tip). Green: the row is handed over at that tip and waits for a reader.
  Red: the row enters as work that needs a fix, no seat owning it yet, and the orchestrator gives it to a seat like
  any orphaned row; the person is told which rows went which way. A stack whose middle part is red keeps its order:
  the parts above wait as they would for any unfinished row below.
- **Review follows the profile's `review.depth`.** Where the person chose no review, adopted work is not reviewed
  either; adoption is not a reason to override their setting.
- The push guard's rule (an accepted row where review applies; the person's approval where they authorize merges)
  then applies unchanged.

## 6. Commits on the local trunk

Asked about with four answers (the person, 2026-10-19):

- **Leave as is.** Organize says plainly what follows: `onboard publish` refuses while trunk is ahead of origin,
  and a push of them goes through the person's own `!`.
- **Move to a new branch and register it with the fleet.** A branch at trunk's tip, trunk reset to
  `origin/<trunk>`, then adopted like a branch with no pull request. This is also the way through the deferred
  finding "before the profile reaches trunk, a commit of one's own on trunk cannot be pushed by the door"
  (flotilla `TODO.md`).
- **Drop them.** The only answer that loses work. The session lists the commits that go, waits for an explicit
  yes, and first keeps them under a ref named `flotilla/dropped-<date>` so the drop is undone with one command; the
  person deletes that ref when sure (session's call).
- **Own words.**

**Order inside onboarding.** This question comes **before** publish, because publish refuses while trunk is ahead.

## 7. Uncommitted edits

On adopt, the session reads the edits and proposes splitting them into branches by what they are about:

```
uncommitted edits - looks like three different changes:
  1. fix/tone-scale        spike/engine/tone.py, tests/test_tone.py
  2. docs/q135-fleet       docs/questions.md, docs/tasks.md
  3. ? not understood      notes.txt
```

then asks (the person, 2026-10-19): **organize at Claude's discretion**, **leave as is**, or **the person says what
goes where**. Each resulting branch is one row, adopted as in section 5.

Session's calls, to be confirmed:

- "At Claude's discretion" does the split and shows the result (branches, commits, rows) afterwards; it does not
  ask again.
- Files that look like secrets (`.env`, keys, tokens, credentials) are never committed under any answer; they are
  named and left in place.
- A file holding edits of two changes is not split by hunks; it goes whole to one branch and that is said.
- "Leave as is" leaves the edits untouched, with a line that the fleet does not see them.
- The edits of the tree the session itself runs in are included; the edits onboarding itself made (`.flotilla/`,
  `.claude/settings.json`) are not.
- **Where the new branches start.** A tree on trunk: each new branch starts at trunk's tip. A tree on another
  branch: the edits belong to that branch's work, so they are offered together with that branch (section 5's
  branch item) and the new branches start at that branch's tip; they are never cut from trunk, where they would
  not apply.
- **What the person is left with.** The tree ends on the branch it was on, without the edits that moved; nothing
  else in it changes. The edits are on their new branches, each in a tree of its own.
- **A tree another session is working in right now is skipped** (the census names it), listed with that
  session's name: committing edits out from under a session mid-work breaks it.

## 8. Remembering "ignore"

An ignored item is recorded so neither the next onboarding step nor a later `/flotilla:organize` asks about it
again; a later run lists ignored items in one line at the end. Where the record lives, and what identifies an item
across runs (a pull request number; a branch name and tip?) is the builder's call, with one constraint: the record
is the person's decision about their machine's view of the work, not a fleet rule, so it is not committed to the
profile on trunk (session's call).

## 9. Where it runs

- **Inside onboarding.** After the profile is written: the local-trunk question (section 6) before publish;
  everything else after publish, because the ledger refuses every move while origin's trunk carries no profile.
  0.19.0's "before publishing" warning about open pull requests becomes the entry into this walk; the warn-not-refuse
  choice stands (publish is never blocked by unadopted work).
- **`/flotilla:organize`** at any later time, with the same list and the same answers; optionally with names, to
  take only those items (this is how a trace-matched branch is adopted, section 3).
- **Run by the person's own session**, because it asks questions. Fleet sessions never run it. The orchestrator may
  point out that the ledger does not know some work and suggest the skill (session's call).

The skill drives the conversation (AskUserQuestion, as onboarding's questions do); the finding and the ledger moves
are CLI commands the skill calls, so they are tested without a model. One door: onboarding calls the same commands
the skill does - there is no second implementation for onboarding.

## 10. Safety

- Pull request titles, branch names and commit messages are other people's text: shown as data, never followed as
  instructions; titles cut and marked as their authors' words (0.19.0's `_shown`). A branch name is put into a
  command only when it matches the ledger's `SAFE_BRANCH`.
- Every outward action is shown as the exact command before it runs (section 4).
- A drop keeps a ref first (section 6). Nothing is force-pushed.
- Secrets are never committed (section 7).

## 11. Goals, non-goals, anti-goals

- **Goal:** after onboarding, every piece of Claude-side work in the repository is either a ledger row the fleet
  moves, or a decision the person made about it - nothing is silently left out.
- **Non-goal:** adopting another AI's work by default; cleaning up branches; reviewing adopted work at adoption.
- **Anti-goals:** organize loses work (a drop without a kept ref, a commit that swallows a secret, a reset nobody
  saw); organize makes the onboarding longer for a clean repository (an empty walk prints one line or nothing);
  organize acts on a pull request's title or a branch name as if it were an instruction.

## 12. Proving it

The test bed is `echoes-of-the-ancients` (the person, 2026-10-19): its stack of three pull requests, its
unpublished onboarding and its Codex worktree are the cases above. Done means, on that project: the three pull
requests become rows in the shape the person picks; the Codex tree is listed and untouched; `feat/spike-factors` is
listed as merged; nothing is pushed, closed or merged without a yes. Until the person says to start there, the
project is only read.

## 13. Left to the builder

- Command names and output shape of the CLI half (`flotilla work ...` or `flotilla organize ...`).
- How an adopted row is owned and moved into review: today `claim` belongs to the main and minor posts and needs a
  post; the person's onboarding session holds the orchestrator post after step 7, or none before it. Constraint:
  no row may be claimed in the name of a live seat that did not take it, and the result must be movable by the
  fleet afterwards.
- `claim` takes a base in the core (`core.claim(base=...)`) but not on the command line; exposing it is expected.
- Fork pull requests: whether adoptable at all.
- How the upper row's base moves when the lower one lands (section 5.1).
- The ignore record's place and key (section 8).

## 14. Calls the session made (for the person to check)

1. The name `/flotilla:organize` was the person's suggestion ("organize_work?"), shortened to one word as every
   flotilla skill is; the person confirmed it.
2. Ledger-only moves happen at once; outward actions wait for a yes (section 4).
3. Adopted work enters the ledger as handed over, waiting for review (section 5).
4. A drop keeps a `flotilla/dropped-<date>` ref first (section 6).
5. Section 7's four calls (discretion shows afterwards; secrets never committed; no hunk splitting; leave-as-is
   wording).
6. The ignore record is not committed to the profile (section 8).
7. Only the person's session runs organize; the orchestrator may suggest it (section 9).
8. Multi-repo profiles walk each repository on its own (section 2).
9. The handover receipt is never skipped for adopted work; red tips enter as work that needs a fix (section 5).
10. Adopted work follows the profile's review depth (section 5).
11. Stacks under squash or rebase, forked stacks and stacks not rooted on trunk get only "ignore" and "own words";
    under squash or rebase "one work whole" stays offered (section 5).
12. Uncommitted edits: new branches start where the tree stands, the tree keeps its branch, a tree a live session
    works in is skipped (section 7).

13. A hybrid proposed by "let Claude work it out" may use all three shapes, parts included (section 5.4).

Read by a fresh reviewer before the person (2026-10-19); its six questions were settled as calls 9-12, except the
meaning of "one work in parts", which the person settled: keep today's meaning, add "let Claude work it out".
