# Organize: taking in work the ledger never saw - design

Date: 2026-10-19, revised 2026-10-20. Status: third version, after two rounds of specialist review and a probe of
GitHub's native stacks (section 15), for the person's review. Agreed with the person part by part. Marked
**(session's call)** where the session decided and the person has not confirmed.

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
stopped short of publishing: the profile commit `2d6d904` sits directly on `main` (`5c4f839`), and above it the same
branch carries a second commit of the person's, `4eb46da` (task T-053 in the project's docs). The tree stands on
that branch, not on `main`. On GitHub three pull requests stood stacked since 2026-09-13: #42
`docs/relations-model` into `main` (19 commits of its own), #43 `chore/serena-enforcement` into #42's branch (5),
#44 `docs/value-axes` into #43's branch (12; 36 above `main` in all) - a plain chain, not a native GitHub stack
(GitHub lists no stacks for the repository). T-053 conflicts with all three pull requests' tips; the profile commit
alone merges cleanly with all three. The repository does not delete branches on merge and `main` is unprotected.
A second worktree, `echoes-of-the-ancients-codex-skills`, stands on `docs/codex-project-skills`, a branch with no
commits of its own, 60 behind `main`, holding uncommitted edits and Codex files (`.agents/`, `AGENTS.md`,
`CODEX-TO-CLAUDE.md`). The main tree has an untracked `.serena/`. Onboarding said nothing of the pull requests; no
ledger file for the project existed.

With organize, the person sees, before publishing:

```
before publishing:
  .serena/ - a tool's cache, untracked: add it to .git/info/exclude?
  chore/flotilla-onboard also carries 4eb46da "docs: open T-053 ..." (yours) -
    it conflicts with #42, #43 and #44: land it with the profile (all three then go back
    for a merge with main), or move it to a branch of its own?
```

and after publishing:

```
your work the ledger does not know:
  a chain of 3 pull requests (bottom first):
    #42 docs/relations-model     -> main                      19 commits of its own
    #43 chore/serena-enforcement -> docs/relations-model       5
    #44 docs/value-axes          -> chore/serena-enforcement  12
not touched (not yours, or another AI's - name it to take it in):
  tree echoes-of-the-ancients-codex-skills (docs/codex-project-skills) - uncommitted edits, .agents/, CODEX-TO-CLAUDE.md
no commits of their own, may be deleted: feat/spike-factors
```

On "separate works" three ledger rows appear, each reviewed over its own commits, landing in order.

## 2. Scope: the person's own work

- **Organize takes in only the work of the person running it** (the person, 2026-10-19). Everyone else's work -
  another person's or another AI's - is shown and never touched, unless the person names it explicitly
  (`/flotilla:organize <branch or #pr>`). Claude sessions working for the person count as the person.
- **The repository the profile describes**, and its worktrees. Other repositories of a multi-repo profile are
  walked each on its own (session's call).
- **Not**: rewriting history the person did not ask for, closing or merging anything on GitHub without the
  person's answer, judging the quality of taken-in work (review's job once it is a row), building native GitHub
  stacks.

## 3. What organize finds

It starts with `git fetch --prune`, so nothing is judged from stale remote refs. Each kind below is found by asking
git and GitHub, never by memory of earlier runs (except the ignore record, section 8).

| kind | found by | asked about |
|---|---|---|
| open pull requests | `gh pr list --state open` with number, head, head sha, base, author, draft flag, fork flag, title (0.19.0's `detect_prs`, extended). Its `LIMIT = 100` cuts silently today: organize pages through all, or says it stopped at N | yes |
| chains | a pull request whose base is another open pull request's head | yes, as one item |
| native GitHub stacks | `GET /repos/{owner}/{repo}/stacks` (open stacks, members bottom first, each with head ref and sha, state, draft flag); per pull request, GraphQL `PullRequest.stack` | yes, as one item (section 5) |
| branches with commits not on trunk and no open pull request (on origin or local only) | refs against trunk | yes |
| commits on the local trunk that origin's trunk lacks | `git rev-list origin/<trunk>..<trunk>` | yes (section 6) |
| commits on onboarding's branch beside the profile commit | the branch's range minus the commit that adds `.flotilla/project.toml` | yes (section 6) |
| uncommitted edits in a worktree | `git status --porcelain` per worktree | yes (section 7) |
| tool caches (`.serena/` and the like) | untracked directories a known tool writes | yes, before publish only: exclude or leave (section 9) |
| branches with no commits of their own | tip contained in trunk and never ahead of it (`git branch --merged` with no merged pull request for it) | no - listed as "no commits of their own, may be deleted" |
| branches already merged | `git branch --merged` with a merged pull request, plus patch-id against trunk (`git cherry`, or flotilla's `patch_fingerprint`) and merged pull requests from `gh`, so squash- and rebase-merged branches are recognized | no - listed as "merged, may be deleted" |

Excluded always: trunk, branches an open ledger row knows, pull requests a row records as ones it closes
(section 5), flotilla's own refs (`refs/flotilla/...`), onboarding's profile commit (identified by the commit that
adds `.flotilla/project.toml`, since nothing records the branch), and - from both "may be deleted" lists - every
branch checked out in any worktree (the Codex tree's branch on the test bed has no commits of its own and is in
live use).

**Whose work it is.** The person is identified by `git config user.email` and the GitHub login (`gh api user`).

- A pull request is theirs when its author is the person; a branch, when its own commits (fork point..tip) are
  authored by the person.
- Positive signs that a Claude session wrote it for the person: `Co-Authored-By: Claude` or `Claude-Session:`
  trailers, `claude/*` or `worktree-*` branches, trees under `.claude/worktrees/`, "Generated with Claude Code" in a
  pull request body.
- Signs of another AI: `.agents/`, `.codex/`, files named `CODEX-*`, and other tools' trailers - counted only where
  the branch's own range adds or changes them, or where they are untracked or modified in a tree, never because
  trunk already carries them. **`AGENTS.md` alone is not a sign**: since v2.1.277 Claude Code reads it as project
  instructions when a project has no `CLAUDE.md`, so it may be the person's own instructions for Claude.
- Work that mixes authors, or carries another AI's signs, counts as not the person's (session's call): shown, taken
  in only when named.

**Trees other than the organizing session's own are entered only on a yes for that tree** (session's call, after
review). A worktree lock does not say a tree is idle: Claude Code locks only its agent, background and `-p` trees,
not an interactive session's, and other tools lock nothing. So, for each other tree:

- it is **in use**, never offered, when `git worktree list --porcelain` shows it locked, it is a flotilla seat tree,
  or a live process has its working directory inside it (read from procfs, as `fleet/leftovers.py` already does;
  where there is no procfs, organize says it could not tell and treats the tree as in use);
- otherwise its items are listed under the tree's path, and organize asks once, per tree, before reading its edits
  or moving anything in it.

The census is an extra signal only: flotilla's seats all report the main checkout as their directory. The tree the
organizing session runs in is handled explicitly (section 7).

## 4. The answers

For every item asked about:

- **Take in** - bring it into the ledger. What that means per kind is in sections 5-7.
- **Ignore** - leave it; flotilla does not touch it and does not ask again (section 8). "Ignore the rest" settles
  every remaining item at once.
- **Own words** - the person describes what they want ("#43 is stale, close it", "I merge #42 myself now"). This is
  the question's free-text entry, which Claude Code adds to every question. The session reads it and proposes
  concrete steps; steps organize knows (take in with a shape, close, retarget, unstack, ignore) are recorded as the
  answer once the person confirms them; anything else is printed as a command for the person to run after `!`.
- **No answer** - a dismissed or timed-out question changes nothing; the item is asked about again next time
  (the person, 2026-10-19). A default never moves a row or touches GitHub. Claude Code's question timeout
  (`askUserQuestionTimeout`) is off by default; when it is set and fires, Claude Code submits whatever options were
  already selected and adds a notice that the person was away. A result carrying that notice is no answer: the
  skill records nothing for it. No organize question has a preselected or recommended option, so an expired one
  normally carries nothing anyway.

**Questions fit the question door.** The door takes 2-4 options per question and at most four questions per round
(`MAX_PER_ROUND`). Each item's first question is take in / ignore / ignore the rest (own words is the free-text
entry); a chain or stack that is taken in gets a second question, its shape (section 5). Items are paged into
rounds of four, the person's answers to one round deciding which questions the next holds.

**Who acts, and on what authority (session's call, after review).** Claude Code's interactive sessions start in
auto mode since v2.1.283: a command left out of a skill's `allowed-tools` goes to the classifier, not to the
person, so leaving it out is no gate (and an `allowed-tools` grant lasts one turn only). So the authority is the
person's recorded answer, and only flotilla acts on it:

- `flotilla organize answer` records answers; it is a person-class command, refused from background sessions by
  `caller.person_refusal`, as `work approve` is.
- Moves that live only in the ledger are made as soon as the answer is recorded.
- Anything that leaves the ledger - closing, merging, retargeting or unstacking a pull request, deleting a branch,
  moving trunk, committing the person's files - is done only by flotilla's own command for the item (`flotilla
  organize apply`, names the builder's), which carries out exactly what the recorded answer names, after checking
  that what the person was shown still holds (same pull request heads, same bases, same tree state); anything
  moved means the item is asked again. The skill never builds these commands itself. There is no second
  permission prompt: the answer is the yes.
- **The guard backs it**: in a repository with a flotilla profile, `gh pr close`, `gh pr edit --base`, the stack
  endpoints (`gh api` with a non-GET method on `.../stacks...`) and `gh stack` commands that push, merge or unstack
  are refused from a session when they touch a pull request a row or an open organize item names, and the refusal
  names `flotilla organize apply`. Where `gh` cannot answer whether they do, the guard refuses. (Today `gh pr edit`
  and `gh pr close` are not doors at all, and `gh stack` pushes, opens and merges pull requests without the text
  `git push`, `gh pr create` or `gh pr merge` the guard reads.)
- **Dropping commits, the one answer that loses work, is never run by flotilla or the session**: organize keeps
  them first (section 6), prints the command, and the person types it after `!`, as `work approve` already does.

## 5. Pull requests, chains and stacks

**How a taken-in row enters the ledger (session's call, after review).** Through a new person-class ledger move
(the "take in" move; name the builder's), checked by `caller.person_refusal` and not by a post. Three reasons it is
not `claim`: the onboarding session holds no post until `spawn --lead`; the orchestrator's post may not `claim`;
and post templates are copied once at onboarding and never overwritten, so a new `may` entry would not reach
existing projects. The move writes:

- state `claimed`, owner a reserved name no census lists, which the orchestrator gives to a seat the way it gives
  an orphaned row; the seat runs the handover tiers over the tip and hands it over. Why not `handed` at once: a
  draft pull request or a spike is not finished, and running every tip's tiers inside onboarding is slow (three
  runs of the test bed's gate). The handover receipt is never skipped;
- **the post the row is judged as**, pinned at take-in, so review follows the profile's `review.depth` for that
  post and is never skipped by accident (under `main-only`, review is skipped for some posts);
- the pull request's number, so the sender does not open a second one;
- its base as a commit sha, never a branch name and never empty. A branch present only on origin is first created
  locally at the pull request's head sha; a local branch that differs from that head is asked about;
- **the adoption point**: origin's trunk sha at take-in, a field of its own rather than an overloaded base.
  Commits under it date from before flotilla: they do not surface as "direct commits" for the person to vouch for
  (`findings._horizon` reads the adoption point), and do not count as read by the row's verdict (`batch.py`);
- **what the answer authorized outside the ledger**: the retarget target, and the numbers of the lower pull
  requests to close (below). These numbers also keep a later run from asking about #42 and #43 again.

**Before the person picks a shape**, organize checks each tip merges cleanly with trunk (`gitq.merges_cleanly`) and
names predicted conflicts, with trunk as it will stand after publishing. The check writes objects into the
repository (`git merge-tree --write-tree`); they are unreferenced and git collects them. On a repository that must
stay untouched it runs in a throwaway clone.

A single pull request, taken in, is one row; base = its fork point from trunk.

**A chain is asked about once, as a whole: "Are these separate works, one work in parts, one work whole, or should
Claude work it out?"** No answer is marked recommended - the person takes this on themselves (the person,
2026-10-19). No answer changes nothing (section 4).

1. **Separate works.** One row per pull request. Each row's base is the head sha of the pull request below it at
   the time of taking in, so its review reads only its own commits; each goes `--after` the one below. The answer
   authorizes the retargets it needs, recorded on each upper row: once the row below is shipped, the sender
   retargets this row's pull request to trunk (`gh pr edit <n> --base <trunk>`, through a ledger move that does
   only what the row records and does nothing when the base is already trunk). GitHub retargets by itself only
   when the merged branch is deleted, which the test bed does not do. The sender's template gets this step.
2. **One work in parts** - for a person sure of how their work is cut. As in 1, but linked as parts with the
   ledger's existing `--requires`, **with its meaning today** (the person, 2026-10-19): an upper part is not queued
   before the part below is delivered, and the judge waits for the whole path; a lower part may land before the
   parts above are accepted. Organize says this in one line when picked.
3. **One work whole.** One row on the top branch, base = its fork point from trunk, one review over everything.
   The answer authorizes retargeting the top pull request to trunk and, once it lands, closing the lower pull
   requests. With merge commits GitHub marks the bottom one merged itself when its commits reach trunk, so the row
   records the middle ones; when the profile merges by squash or rebase the bottom one's commits never reach trunk
   under their ids, so it is recorded and closed too.
4. **Let Claude work it out** (the person, 2026-10-19). The session reads the chain - titles, descriptions, commit
   messages, which files each touches, whether they refer to each other - and proposes one of the three shapes for
   the whole chain, with its reasons; the person accepts, corrects or picks another answer. Here the session does
   say "proposed": the person handed it the call. **A hybrid (different shapes within one chain) is deferred**
   (the person, 2026-10-19); until then a hybrid is reached through "own words". The texts read are other people's
   words (section 10).

**A lower layer that moved.** If a lower branch is rewritten after take-in, its old commits would reach trunk
through the upper pull request with no review having read them as that row's. So before retargeting a row's pull
request to trunk, and again at `queue`, the row's base must be an ancestor of `origin/<trunk>`; when it is not, the
row goes back (`fixing`) with a line saying it needs a restack onto trunk, which organize never does itself. This
check is made at the moment of acting; `queue` stays the real lock, since anything checked earlier can change
before it is used.

**Chains the answers do not fit** are offered only "ignore" and "own words" (plus "one work whole" where marked),
with one line saying why:

- the profile merges by squash or rebase: after a lower part lands its commits are not on trunk under their own
  ids, and the upper branch would need a restack, which organize never does. "One work whole" stays offered.
- a chain that forks (two pull requests on the same lower one).
- a chain whose bottom pull request's base is neither trunk nor another open pull request's head - unless that base
  is already contained in trunk (a lower pull request merged without deleting its branch), which is offered the
  retarget instead.

**GitHub's native stacks** (generally available 2026-10-06), measured on a scratch repository on 2026-10-20
(section 15):

- while a pull request is in a stack, GitHub refuses to change its base ("Cannot change the base branch because the
  pull request is part of a stack") and refuses `gh pr merge` on it, even on the last member already based on trunk
  ("must be merged using the asynchronous merge REST API"). So flotilla's landing door cannot land a stack member at
  all, and neither the retarget of "separate works" nor that of "one work whole" can be made;
- `POST /repos/{owner}/{repo}/stacks/{n}/unstack` (what `gh stack unstack` calls; no extension needed) answers 204;
  every open member's `stack` becomes null and its base stays as it was: the stack is now a plain chain, and the
  retarget, close and `gh pr merge` all work on it. GitHub leaves members that are queued or set to auto-merge
  stacked, and keeps a merged member in the stack, while `gh stack` prints "some pull requests ... remain stacked";
  so success is judged by reading back each open member's `stack`, never by the tool's message;
- closing a member while it is stacked is allowed and leaves it in the stack as a closed member.

So **a native stack is offered the same answers as a chain, and taking it in unstacks it first** (session's call,
from the probe): the take-in question says in one line that taking it in turns it into a plain chain on GitHub
(reviewers there no longer see it as a stack), and the answer authorizes the unstack. After the unstack, read back,
it is handled as a chain in the shape the person picks; if any open member is still stacked, nothing else is done
and organize says which and why. "Ignore" leaves the stack as it is. Organize never stacks anything. `gh stack
merge` (an atomic merge of the stack up to a member, with the next member retargeted to trunk and kept stacked) is
not a path flotilla takes: it lands without a receipt and outside the ledger.

**If a row's pull request is stacked later** (the person builds a native stack while the row is in flight), `queue`
refuses it, naming the unstack, since the landing door would fail on it.

**A guard beside it:** the push guard refuses `gh pr merge` on a pull request whose base is not trunk - with no
retarget such a merge lands in the lower branch, not trunk.

## 6. Commits on the local trunk, and onboarding's branch

**Commits on the local trunk** that origin lacks, three answers and own words (the person, 2026-10-19):

- **Leave as is.** Organize says what follows: publish refuses while trunk is ahead of origin with commits the
  person did not name; the person pushes them after `!`.
- **Move to a new branch and take it in.** Fetch; create the branch at trunk's tip and check it holds them; move
  trunk to `origin/<trunk>` with `git branch -f` when trunk is checked out nowhere, `git reset --keep` when it is
  checked out in the organizing session's tree, and refuse when it is checked out in another tree. Never
  `--hard`. Then the branch is taken in like a branch with no pull request. This is also the way through the
  deferred finding "before the profile reaches trunk, a commit of one's own on trunk cannot be pushed by the door".
- **Drop them.** The commits that go are listed, kept first under a ref named by the kept commit's own sha
  (`refs/flotilla/dropped/<sha>`, outside `refs/heads`, so no later run finds it as work), and the person runs the
  drop after `!` (section 4).

**Onboarding's branch beside its profile commit** - the person's own work riding on that branch, like T-053 on the
test bed - is asked about before publish with two answers and own words (session's call): **land it with the
profile**, or **move it to a branch of its own** and take that branch in after publish. The conflicts it would
bring into trunk against open pull requests are named, with what follows: on the test bed, landing T-053 sends all
three pull requests' rows back for a merge with trunk.

**Publishing a profile that is already committed** (session's call, after review). Today publish commits
uncommitted `.flotilla/` and `.claude/settings.json` on a tree standing on trunk, and refuses a tree on another
branch and a trunk ahead of origin. On the test bed the profile is already a commit on a side branch, and checking
out `main` would drop `.flotilla/`. So publish accepts a profile commit that sits directly on `origin/<trunk>`:

1. on "a branch of its own", the person's commits above the profile commit get a branch (organize proposes a name
   from their subject; T-053's would be its own), trunk is fast-forwarded to the profile commit (`2d6d904`); on
   "land it with the profile", trunk is fast-forwarded to the branch's tip (`4eb46da`). Trunk moves by
   `git branch -f` when it is checked out nowhere, as on the test bed, and is refused when another tree has it
   checked out; it is never rewritten, only fast-forwarded;
2. the organizing tree switches to trunk, which now carries the profile;
3. publish runs as today, with its ahead-of-origin refusal relaxed for exactly the commits the person named: the
   profile commit, and on "land it with the profile" the commits above it. Any other commit ahead of origin is still
   refused. The tiers run over trunk's new tip and the push goes through the door as today.

If the trunk push fails, the profile stays committed and trunk stays where it was moved; the after-publish half of
organize (pull requests, chains, branches) waits for a later `/flotilla:organize`, and onboarding says so in one
line, since the ledger takes no moves before the profile is on origin's trunk.

## 7. Uncommitted edits

On take in, the session reads the edits and proposes splitting them into branches by what they are about:

```
uncommitted edits - looks like three different changes:
  1. fix/tone-scale        spike/engine/tone.py, tests/test_tone.py
  2. docs/q135-fleet       docs/questions.md, docs/tasks.md
  3. ? not understood      notes.txt
```

then asks (the person, 2026-10-19): **organize at Claude's discretion**, **leave as is**, or **the person says what
goes where** (the free-text entry). "At Claude's discretion" shows the plan once and carries out exactly that plan
(session's call, after review); each resulting branch is one row, taken in as in section 5.

How it is done, so no edit is lost (session's calls, after review):

- **First, keep everything.** `git stash create` does not keep untracked files (in git 2.53 `stash create -u`
  takes `-u` as the stash message), so the kept commit is built with a temporary index: `GIT_INDEX_FILE=<tmp> git
  add -A` (ignored files are not taken, and organize never touches them), `git write-tree`, then `git commit-tree`
  with `HEAD` and a commit of the real index as parents, so staged and unstaged states both survive. The ref is
  named by that commit's own sha (`refs/flotilla/kept/<sha>`) and written create-only (`git update-ref <ref> <sha>
  0000000000000000000000000000000000000000`), so no run overwrites another's.
- **Where branches start**: a tree on trunk - at trunk's tip; a tree on another branch - the edits belong to that
  branch's work and are offered with it, and the new branches start at its tip.
- Each branch's commit is made with plain `git commit` in a tree of its own, so the repository's hooks run; a hook
  that fails stops organize with everything kept and nothing removed. Organize's own trees live under flotilla's
  state directory, like seat trees, never under `.claude/worktrees/`, which Claude Code treats as its own.
- Each committed path is compared with the working file as git would store it (`git hash-object --path <path>
  <file>` against the blob in the new commit, so filters and line-ending rules apply); only then do those paths
  leave the original tree. The original tree stays on its branch, without the edits that moved.
- A file holding edits of two changes is not split by hunks; it goes whole to one branch, and that is said.
- **Secrets are never committed under any answer**: files are scanned by content (betterleaks, or gitleaks, when
  installed; otherwise a short set of high-signal token patterns) and by name (`.env`, keys, credentials); hits are
  named and left in place.
- "Leave as is" leaves the edits untouched, with a line that the fleet does not see them.
- The organizing session's own tree is included; onboarding's own edits (`.flotilla/`, `.claude/settings.json`)
  are not; another tree only on its yes, and never one in use (section 3).

## 8. Remembering "ignore"

An ignored item is recorded so neither the next onboarding step nor a later `/flotilla:organize` asks again; a later
run lists ignored items in one line at the end. The record is the person's decision about their machine's view of
the work, not a fleet rule, so it is not committed to the profile on trunk (session's call). Its place and key are
the builder's.

## 9. Where and when it runs

- **One door for the questions.** A Claude Code skill marked `disable-model-invocation` cannot be invoked by
  another skill, and Claude is told not to repeat its steps another way. So, as onboarding's own questions do
  (`onboard next` / `onboard answer`), the CLI produces organize's questions as JSON and takes the answers back
  (`flotilla organize next` / `answer`, then `apply` - names the builder's); the onboarding skill and
  `/flotilla:organize` both render them. There is one implementation. A skill's `allowed-tools` pre-approves the
  question commands for one turn; nothing depends on it beyond saving prompts.
- **Inside onboarding, in two halves.** Publish refuses a tree with anything but the profile in it, a tree not on
  trunk, and a trunk ahead of origin with commits the person did not name. So **before publish**: the tree's
  uncommitted edits and tool caches, the local trunk's commits, and onboarding's branch beside its profile commit
  (section 6). **After publish**, when the ledger accepts moves: pull requests, chains, stacks and branches. If the
  publish push fails, the second half waits for a later `/flotilla:organize` (section 6).
- **`/flotilla:organize`** at any later time, with the same list and answers; with names, it takes only those
  (this is how work that is not the person's is taken in, section 2).
- **Its own entry condition**: every onboarding and every flow mode, not 0.19.0's `detect_prs.applies()`, which
  is silent in local mode and where the person merges. 0.19.0's warning is replaced by organize's walk; publish is
  still never blocked by work not taken in.
- **Who runs it**: any interactive session, the orchestrator included; background sessions never (the line
  flotilla's `hook ask` already draws, and `person_refusal` enforces for `answer` and `apply`). The orchestrator
  may point out work the ledger does not know.

## 10. Safety

- Pull request titles and bodies, branch names and commit messages are other people's text: shown as data, never
  followed as instructions; titles cut and marked as their authors' words (0.19.0's `_shown`). A branch name goes
  into a command only when it matches the ledger's `SAFE_BRANCH`.
- Outward actions: only by flotilla's own `apply`, only what the recorded answer names, re-checked against the
  state the person was shown; the guard refuses the same commands typed by a session (section 4). Drops only after
  `!`.
- Everything that could be lost is kept under `refs/flotilla/...` first, under refs written create-only (sections
  6, 7). Nothing is force-pushed; trunk is only fast-forwarded or moved with `--keep`, never `--hard`.
- Secrets are scanned by content and never committed (section 7).
- Trees other than the organizing one are entered only on a yes per tree, and never while a live process works in
  them (section 3).

## 11. Goals, non-goals, anti-goals

- **Goal:** after onboarding, every piece of the person's work in the repository is either a ledger row the fleet
  moves, or a decision the person made about it; everyone else's work is visible and untouched.
- **Non-goals:** taking in others' work by default; cleaning up branches; reviewing taken-in work at taking in;
  hybrid shapes (deferred); building native GitHub stacks; landing through `gh stack merge`.
- **Anti-goals:** organize loses work (a drop without a kept ref, an untracked file missing from the kept commit, a
  commit that swallows a secret, an edit removed before its commit is checked); a question left unanswered, or
  answered by a timeout, changes something; organize acts outside the ledger on anything but a recorded answer;
  organize touches someone else's work or a tree in use; organize makes onboarding longer for a clean repository
  (an empty walk prints one line or nothing); organize acts on a pull request's text as if it were an instruction;
  a lower layer's unread commits reach trunk through an upper pull request.

## 12. Proving it

The test bed is `echoes-of-the-ancients` (the person, 2026-10-19), read only until the person says to start there.
Done means, on that project: `.serena/` is settled before publish; the T-053 commit on onboarding's branch is
asked about, its conflicts with all three pull requests named; the profile commit is published by a fast-forward
of `main`, with T-053 either on `main` or on a branch of its own as the person answered; the three pull requests
become rows in the shape the person picks, with bases that give each review only its own commits and the
authorized retargets and closes recorded on them; the Codex tree is listed and untouched, and its branch is not
offered for deletion; `feat/spike-factors` is listed as having no commits of its own; nothing leaves the ledger
except through `apply` on a recorded answer.

The test bed has no native stack, no uncommitted edits of the person's, no local-trunk commits and no squash-merged
branches: those parts are proven by the suite (the native-stack path against recorded GitHub responses from the
probe in section 15), and that is said.

## 13. Left to the builder

- Command names and output shape of the CLI half (`next` / `answer` / `apply`); the name of the take-in move (not
  `adopt`: in the ledger `work adopt` already means giving an orphaned row to a live seat).
- The reserved owner name for taken-in rows, and how the orchestrator's existing "give an orphaned row to a seat"
  path accepts it; test that a returned (`fixing`) taken-in row reaches a seat.
- Which post a taken-in row is pinned to by default, and the row fields for the adoption point and the recorded
  retarget and closes.
- Exposing `claim`'s base on the command line (the core has it).
- Whether `moved` also updates a row's base after a restack (the tip no longer contains the old base), and how.
- Fork pull requests: whether takeable at all (their head lives in another repository).
- The exact shape of the guard's new doors (section 4), and how it tells which pull request a `gh api` call names.
- The ignore record's place and key; the high-signal secret patterns.

## 14. Calls the session made (for the person to check)

1. The name `/flotilla:organize` (the person confirmed).
2. The recorded answer is the authority: flotilla's `apply` acts outside the ledger, only on it, re-checked; no
   second permission prompt; the guard refuses the same commands typed by a session; drops only after `!`
   (section 4).
3. Taken-in rows enter through a person-class take-in move, as work in progress under a reserved owner, with the
   post pinned, the adoption point and the authorized retargets and closes recorded (section 5).
4. Bases are commit shas, never empty; accounting starts at the adoption point (section 5).
5. Conflicts with trunk predicted before the shape is picked (section 5).
6. "Separate works" and "one work whole" authorize the retargets and closes they need; under squash or rebase the
   bottom pull request is closed too (section 5).
7. A row whose base is not on `origin/<trunk>` goes back for a restack before a retarget and at `queue` (section 5).
8. A native stack gets the chain's answers, and taking it in unstacks it first, judged by reading back each member
   (section 5, from the probe).
9. Squash/rebase, forked and off-trunk chains get the narrower answers; a guard refuses `gh pr merge` into a
   non-trunk base (section 5).
10. "Let Claude work it out" proposes one shape for the whole chain (section 5).
11. Onboarding's branch beside its profile commit is an item, with "land it with the profile" or "a branch of its
    own"; publish accepts a committed profile by fast-forwarding trunk (section 6).
12. Trunk is only fast-forwarded or moved by `branch -f` / `reset --keep`, never `--hard`; kept refs named by their
    sha, written create-only (sections 6, 7).
13. Uncommitted edits: keep everything first with a temporary index, commit with hooks in a tree of its own under
    flotilla's state directory, compare as git stores files before removing, scan content for secrets; "at
    Claude's discretion" shows the plan once and does exactly it (section 7).
14. Questions: take in / ignore / ignore the rest first, the shape second, paged into rounds of four; a timed-out
    answer is no answer (section 4).
15. Mixed-author work and work with another AI's signs count as not the person's; `AGENTS.md` alone is not a sign
    (section 3).
16. Other trees only on a yes per tree, never with a live process inside; branches checked out anywhere are never
    offered for deletion; "no commits of their own" and "merged" are told apart (section 3).
17. The ignore record is not committed to the profile (section 8).
18. Multi-repo profiles walk each repository on its own (section 2).

## 15. Reviews

- **A fresh builder's read** (2026-10-19, before the person's first review): six questions, settled as the session's
  calls then; the meaning of "one work in parts" was the person's (today's meaning kept, "let Claude work it out"
  added).
- **Two specialist reviews, first round** (2026-10-19, after the person accepted the first version): a Claude Code
  plugin specialist and a code-review / delivery / git specialist, each first checking how things stand in October
  2026, then the plugin and this design. Both: approve with changes. The person decided three (2026-10-19): no
  answer changes nothing; others' work - people's or AIs' - is not touched; hybrids deferred.
- **The same two specialists, second round** (2026-10-20, over the second version). Both: approve with changes;
  the plugin specialist with six required findings, the delivery specialist with seven, several shared. Folded in
  above: outward actions were not gated in auto mode (section 4); `claim` could not be the take-in move (section
  5); the test bed's profile could not be published (section 6); a stack question had five answers where the door
  takes four (section 4); a timed-out question submits its selections (section 4); interactive sessions do not lock
  their trees (section 3); `stash create` keeps no untracked files (section 7); a rewritten lower layer could reach
  trunk unread (section 5); the retarget and close had no recorded authority (section 5); a native stack could not
  be delivered (section 5); the Codex branch would have been offered for deletion (section 3); and minor ones
  (where organize's trees live, `allowed-tools` lasting one turn, `AGENTS.md` wording, the silent 100-PR limit,
  objects written by the merge check, the restack check being time-of-check). One fact was corrected: T-053
  conflicts with all three pull requests, not two.
- **The person's decision on native stacks** (2026-10-20): prove the path on a scratch repository before deciding,
  then delete it. **The probe** (2026-10-20, private repository `TensusDS/flotilla-stack-probe`, `gh` 2.95.0 with
  the official `gh-stack` 0.2.1 extension, removed afterwards): three stacks built with `gh stack link`; measured
  that a stack member's base cannot be changed and `gh pr merge` refuses it even when it is the last member on
  trunk; that `gh stack merge` merges up to a member and retargets the next one to trunk, still stacked; that a
  closed member stays in its stack; that `POST /repos/{owner}/{repo}/stacks/{n}/unstack` returns 204 and leaves
  every open member unstacked on its old base, after which the retarget to trunk, the close and `gh pr merge` all
  worked; that a merged member stays in its stack and makes `gh stack` print that some remain stacked; that `GET
  /repos/{owner}/{repo}/stacks` lists stacks with their members (zero for the test bed). Section 5 is written from
  these. Not measured: whether GitHub rewrites upper members' heads on a lower merge with squash or rebase.
