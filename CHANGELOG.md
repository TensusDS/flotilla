# Changelog

Every release is a tagged commit on GitHub. flotilla is pre-1.0: a minor version may change an interface. Why
each change was made is in [docs/specs/2026-09-22-decisions-log.md](docs/specs/2026-09-22-decisions-log.md).

## 0.7.15 - 2026-10-05

- **Every run in the lane is measured** - its time, peak memory (every process it started), the cores it used and
  how busy the machine was meanwhile - and `flotilla lane` shows each booking's estimate and where it comes from.
  Runs of one command are joined across seat trees and wrappers (`timeout`, `sh -c "cd X && ..."`, `env`). This is
  the first stage of admitting runs by a budget of cores and memory; admission itself is unchanged.
- **The lane's journal survives a bad line**: a hand-written entry of the wrong type no longer stops `flotilla lane`
  for every session, and a command's text is bounded.

## 0.7.14 - 2026-10-05

- **`flotilla fleet size` recommends a fleet for this machine and this backlog, and names the cap that set it.**
  Authors are the smallest of the backlog (ledger rows in flight, tasks the orchestrator names with `--tasks`, open
  GitHub issues when `[fleet.sizing] tracker = "github"`, open items in TODO files), memory, how many handovers the
  lane can verify in an hour, disk, and a ceiling of 12 seats (`max_seats` in `machine.toml`; a profile may only lower
  it). Reviewers follow the authors and the measured review time, never more than the authors. A signal that could
  not be read is named, never guessed. `flotilla spawn --recommended` raises it, less the posts already held, and
  `/flotilla:spawn` with no composition shows it and asks first.
- **Receipts record each tier's peak memory beside its time**, counting every process the tier starts - a browser in
  a process group of its own included.
- **Free memory is the room under a container's or systemd slice's memory limit** where that is smaller than the
  host's, for the sizing, spawn's seat check and the lane's floor alike.
- **The lane no longer waits on a browser an MCP server opened** - a session's tool, not a run with an end - and a
  torn `machine.toml` leaves it at one slot instead of failing every run. Re-measuring the machine keeps the
  `lane_capacity` and `max_seats` you set.

## 0.7.13 - 2026-10-04

- **A seat asking a permission question while the census is down is refused with the reason, not left hanging.**
  Spawn now keeps each seat's Claude Code session id on its post row, so the broker knows a seat without the census.
- **`flotilla doctor` names each post older than the template flotilla ships** under its name, with both versions
  and where the new text is.

## 0.7.12 - 2026-10-04

- **A refusal only you can lift reaches you with what to run.** A seat refused by auto mode's classifier or a
  permission rule sends the orchestrator the refusal's text and a proposal - the command, ready to paste with `!`,
  and the command that checks it worked. The orchestrator shows it as that session's proposal, says what it changes,
  and does not recommend one that widens a permission, disables a guard or skips a check; an approve still comes
  only from its own brief. **Existing projects:** copy the new `orchestrator.md` (template v13) over
  `.flotilla/posts/orchestrator.md` on trunk - post files are copied at onboarding and never overwritten.

## 0.7.11 - 2026-10-04

- **The lane no longer drops a live run when a CPU sample fails**, finds an older CI run still going behind newer
  ones, and reads a `queue_command` that is not text or not found as a lasting unknown instead of crashing or waiting.
- **The permission broker works where hard links are refused** (FUSE, SMB, exFAT): an answer is still written once.
  A pid of 0 is not read as alive, and staged files a killed process left are swept.
- **Two `flotilla spawn` at once raise one sender**: a one-copy post is checked again under the ledger's lock.

## 0.7.10 - 2026-10-04

Delivery says only what it asked:
- **GitHub that could not be asked is "not yet" (exit 3), not "refused".** A PR GitHub says does not exist is still
  refused; gh missing or hanging is "not yet" too.
- **A failed fetch is said, never "(fetched)"**, and a commit git cannot place is "could not tell", not "does not
  have".
- **A PR closed without merging names the legal moves**: release the row, claim the work again.
- **`flotilla work reconcile` exits by its worst line** - 2 for a refusal or a pushed row to `land`, 3 for one not
  proved yet - and takes the `--skip-event` it advises.
- **`flotilla receipt show` exits 1** when no purpose that has tiers holds a green receipt, and takes `--purpose`.
- **The brief holds back an accepted row whose branch git cannot find**, and names `git branch <b> origin/<b>` when
  only origin has it.
- **`vanished` no longer fires for work that reached trunk** (landed, shipped, walked, queued and on origin), where
  its advice would have dropped delivered work.

## 0.7.9 - 2026-10-04

- **`git switch -f` and `--discard-changes` meet the revert guard**, like `git checkout -f <branch>`: they drop
  every change to tracked files. A forced checkout is now read in every spelling git takes (`-qf`, `--for`).
- **A guard hook that fails before judging refuses a push** instead of letting it run unchecked - inside the hook,
  and before flotilla even loads (an interpreter below the floor, an import error).
- **A receipt vouches only for the revision and purpose written in it**, not for whatever its file is named.
- **A symlinked workflow file, or one with a non-ASCII name, no longer refuses every push as a workflow drift.**
  Projects onboarded with one: run `/flotilla:check` once to record the workflow digest again.
- **`gh pr merge [<pr>] --disable-auto` asks no receipt**: it merges nothing.

## 0.7.8 - 2026-10-04

- README says, on its first screen, how flotilla differs from Claude Code's agent teams.

## 0.7.7 - 2026-10-04

- **`flotilla doctor` says when Claude Code changed a record flotilla reads.** Three new lines - `census-shape`,
  `registry`, `trust-record` - compare `claude agents --json`, the session registry and the trust record in
  `~/.claude.json` with the shapes measured on Claude Code 2.1.280 and 2.1.289, and name what goes blind when one
  drifts. Run `flotilla doctor` after updating Claude Code.

## 0.7.6 - 2026-10-04

- **A wait whose object is gone no longer silences the watch.** A row waiting on a branch with no open row left, or
  on a session that is no longer alive, is a deviation with the line that clears the wait, its holder is told, and
  the orchestrator's watch reads it as an ordinary row again.
- **Work piled on one seat is named.** When a live session holds three or more rows in its own hands (claimed, or
  returned for fixes) and a live session of the same post holds none, the orchestrator's watch says so.

## 0.7.5 - 2026-10-03

- The listing icon stands in a terminal window and has four legs.

## 0.7.4 - 2026-10-03

- A listing icon: `.claude-plugin/icon.png`, a brain in an admiral's bicorne, named in `plugin.json`.

## 0.7.3 - 2026-10-03

- The public tree names no person, home directory, email or other project of the people who built it: the field
  tests, plans and tests read "the person", `/home/user` and neutral names.

## 0.7.2 - 2026-10-03

- **A seat number whose tree an earlier fleet left is skipped.** `spawn` and `helper raise` stepped over the numbers of older seats'
  branches, not of their trees: a tree left with no branch beside it (twosuns, after an older fleet named without the
  project) stopped the whole spawn. The number is skipped, the plan says so and points at `flotilla fleet clean`,
  and the tree is never touched.

## 0.7.1 - 2026-10-03

Closes the findings of a scan of the fleet, lane, onboarding and guard code:
- **A push is judged by origin's trunk.** The push guard, the `pre-push` hook and the person's approval took trunk's
  name from the tree's own profile and fell back to that file when the ref carried none, so an uncommitted edit
  could turn a push to the real trunk into a push "to another branch". Trunk is now origin's default branch (or the
  trunk the profile there names) and the rules are the profile trunk carries; git's history is read with replace
  refs and grafts off; `pre-push` asks the destination git names, after every URL rewrite. A push that lands on
  trunk is refused with no override when its rules cannot be read; a branch push needs nothing of origin.
- **Where the line is.** README and SECURITY.md now say plainly that the push guards check what a session pushes
  and are not a lock on the repository: protect trunk on your Git host.
- **Text another session wrote cannot forge lines.** A lane booking's note, name and `--for`, and a leftover
  process's command line in `retire`, are shown with control characters made visible.

## 0.7.0 - 2026-10-03

- **Claude Code only, said and enforced.** The command line moves from `scripts/flotilla` to `bin/flotilla`, which
  Claude Code puts on the Bash tool's PATH; claude.ai chat and Cowork do not install a plugin with `bin/`. Every
  skill's `compatibility` says Claude Code only.
- **Skipping auto mode's classifier is opt-in.** flotilla answers "allow" for its own checked commands and the
  sender's checked push only with `[permissions] skip_classifier_for_checked = true` in the profile on trunk; off
  unless set. Not recommended for people new to Claude Code or auto mode.
  Onboarding writes the key, off; the opt-in counts only as origin's trunk carries it (flotilla asks origin), and a
  session is told once when only the missing opt-in keeps a command from the allow. The same opt-in governs `ask`
  mode: without it, flotilla's own commands are put to you like any other call.
- **A permission question keeps the call it asked about only while it waits.** The command or file content is
  dropped as soon as the asking session has its answer or stops waiting.
- **No session transcript is read.** How a session was started comes from Claude Code's session registry.
- Privacy policy ([PRIVACY.md](PRIVACY.md)), support ([SUPPORT.md](SUPPORT.md)) and security reporting
  ([SECURITY.md](SECURITY.md)); the plugin manifest links them for the directory listing.

## 0.6.11 - 2026-10-03

Closes the findings of a security scan of 0.6.10 and of the review of those fixes: the sender's push allow reads
its rules at the revision origin names and counts only moves made under them; a repository a command merely names
is asked of origin only safely; a branch name a shell would expand never reaches the person's approve command.

## 0.6.10 - 2026-10-03

Auto mode lets flotilla's own moves and the sender's checked push past the classifier.

## 0.6.0 to 0.6.9 - 2026-10-01 to 2026-10-03

- 0.6.9: a direct commit on trunk can be read and closed; orphaned work names who can take it.
- 0.6.8: a fleet nobody leads is a question, not a silence; receipts measure tiers.
- 0.6.7: seats rise only once the leading session's name shows; known limitations named.
- 0.6.4 to 0.6.6: a seat's tree and branch go when their work is surely on trunk, never a live seat's.
- 0.6.2, 0.6.3: the lane spent carefully - nothing run twice, a ceiling, a stop, set-up trees.
- 0.6.1: auto mode by default; flotilla's own commands never wait on the person.
- 0.6.0: seat names carry the project, and your own session leads without a rename.

Earlier releases: the tags before `v0.6.0`.
