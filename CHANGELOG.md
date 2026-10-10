# Changelog

Every release is a tagged commit on GitHub. flotilla is pre-1.0: a minor version may change an interface. Why
each change was made is in [docs/specs/2026-09-22-decisions-log.md](docs/specs/2026-09-22-decisions-log.md).

## 0.13.0 - 2026-10-10

- **The account's credit beside the local count** (rig design, section 5, promised since 0.9.0): `flotilla rig`
  shows the vast account's credit at the session's open and now, and what the account says was spent. It is read
  when the session opens and on every reaper pass while a session is open or closing - last in the pass, and never
  a failure of it: it is shown, it decides nothing; the 90 % drain still runs on the local count. Only the number
  leaves the adapter (vast's answer also carries the account's own key, session and address); a key that may not
  read the account (401/403) is said, not an error.

## 0.12.0 - 2026-10-09

- **`flotilla rig down`**: a seat gives the session's idle machine back at once instead of after 15 idle minutes
  (the person's decision). One reaper pass destroys it; then only the provider's listing is asked, so a slow
  provider is never counted as failed destroys. It is refused while a run is on the machine or waits for one, within
  5 minutes of the machine coming up with no run on it, and within 5 minutes of another seat's run ending on it -
  checked under the journal's lock, like starting a run, so no run is handed a machine being given back. The
  session stays open; a `rig run` that needs a machine meanwhile waits for it to go and raises a new one.
- The main and minor posts tell seats to call it when their run has ended and they foresee no rig run of theirs in
  the next five minutes.

## 0.11.1 - 2026-10-09

- 0.11.0 did not import on Python 3.11: an f-string in the packing broke a line inside its braces, which only 3.12
  allows. Found by CI on 0.11.0; the module now compiles on 3.11 to 3.13.

## 0.11.0 - 2026-10-09

Rig stage 2c: runs on a rented machine are packed by estimates (the person's decisions of 2026-10-07 and 2026-10-09).
- **Estimates** come from the runs of the same command measured on rig machines before (the lane's signature ladder,
  never the project-wide step): median duration of green runs, maxima of cores, memory and GPU memory. A command
  never measured takes 4 cores, 2 GB of memory and 2 GB of GPU memory. GPU memory measured beside another run may
  raise an estimate, never lower it.
- **Fit:** memory and GPU memory bind from the first run; cores bind past two runs (the floor of two stays: CPU
  over-commit only slows runs). Margins as before: 2 GB of memory, 1 GB of GPU memory.
- **Order:** the longest run that fits goes first, short ones fill the rest; a run waiting 10 minutes is senior and
  later runs leave it the room it needs (the whole machine, if it is bigger than the machine); a run bigger than the
  machine runs alone once it is empty.
- **The live readings stay** as the second barrier past two runs, now counting reclaimable page cache as free.
- **The machine's size is its container's:** the cgroup's memory limit and CPU quota (v1 and v2), not the host's;
  a GPU whose NVML reports no memory is treated as no GPU, and a machine without `nvidia-smi` measures no GPU.
- **Every waiting `rig run` tries;** only the one the estimates pick reads the machine. A waiting run says why it
  waits, and `flotilla rig` shows each waiting run's reason, estimate and seniority.
- Runs finished before 0.11.0 teach estimates too (a run's end is taken from its first `done` record).
- **The posts say who gives a machine back:** a seat never releases a machine or closes the rig session - a
  machine with no run for 15 minutes goes back on its own, and only the person ends a session sooner
  (`flotilla rig close`). The orchestrator brings that line when the person wants the rig closed.

## 0.10.5 - 2026-10-09

- A `rig run` signalled in the instant between taking its machine and knowing its paths on it ended with no verdict
  in the journal (its cleanup tripped on the missing paths). The paths are now known first, and the cleanup asks
  for both. Found by CI on 0.10.4, reproduced by a test.
- Tests no longer see this machine's own runs as the lane's foreign runs. A test that took its isolated lane read
  the real process table, so a full suite under `flotilla lane run` waited on any pytest queued behind it - which
  waited on the suite. Measured on 2026-10-09: the suite resumed the second the queued run was stopped.

## 0.10.4 - 2026-10-09

From the twosuns fleet's field use of 0.10.2:
- **`flotilla rig run --max S`**: one run's ceiling in seconds, at most the profile's `[rig] max_run_seconds`.
- **`flotilla rig stop jN`**: a seat stops its own run (the person may stop any). Its `rig run` ends it on the machine
  and records it `stopped`; when that process is already gone, the run is stopped on the machine through its own
  status files and recorded. A machine that cannot be reached leaves the run `gone`, for the next run there to end,
  and exits 1; a `rig run` still cleaning up after 90 s is left to record its own verdict. Whose run it is comes from
  the census: outside any listed session `--as` proves nothing, and only the person may stop.
- The hang twosuns reported (a script's background vite holding ssh's channel until the 30-minute ceiling) was fixed
  in 0.10.3.

## 0.10.3 - 2026-10-08

- **A run's measurements are right for 0.11.0's packing** (live check 2 read cores 0.22 and GPU memory 0 for a
  browser run): CPU now counts helpers that left the run's group; GPU memory is the machine's peak against its start,
  and is not kept as the run's own when another run was on the machine.
- **What a run left running ends with it**: a server or a browser the command leaves behind no longer holds ssh's
  channel open until the ceiling.
- A gone run is stopped on its machine once, then marked swept; the sampler ends with its run however the run ends;
  a `--get` placement that fails midway puts every old path back; a staged rename names both files; a `--put`
  directory covers the changed files under it that exist (a file deleted under it is still refused: the machine would
  keep it from the revision); CPU is read right for processes whose name holds spaces (Firefox's `Web Content`).

## 0.10.2 - 2026-10-08

- **`rig run --get` brings back only artifact types** (the person's decision): images, video, sound, `json`, `csv`,
  `tsv`, `txt`, `log`, `md`, `pdf`. Any other file - code, a config, a file with no extension - stays on the machine
  and is named in the output; a project adds its own types with `[rig] get_types`. A list of names that tools run can
  never be whole, and the files in what comes back are written by the command and everything it installed, not only
  by the host.

## 0.10.1 - 2026-10-08

From a security review of the pushed 0.10.0:
- **What `rig run --get` brings back is checked harder:** a member named as a file tools run by name alone
  (`conftest.py`, `*.pth`, `sitecustomize.py`, `usercustomize.py`, `.npmrc`, `.yarnrc`, `.yarnrc.yml`,
  `.pnpmfile.cjs`) is refused; no file comes back executable; the archive must be plain tar (a compressed one could
  unfold past the 500 MB cap), at most 100 000 members, and no sparse file.

## 0.10.0 - 2026-10-07

- **`flotilla rig run`: a command on a rented machine, its output, its exit code and its artifacts.** Several runs
  share a machine - two always, more when its memory, GPU memory and CPUs leave room - in a line per session. The
  revision travels once and is checked by sha256; each run has its own copy and its own setup with shared download
  caches; `--put` refuses secrets, symlinks and hard links, `--get` refuses tracked paths and places that run code;
  ssh reads none of the person's config. A broken connection is asked about, not assumed; a signal stops the run on the machine; every run renews
  the lease and the heartbeat, is measured there, and leaves its verdict and cost in the journal.
- The person guard reads a rig line with bash's quoting: a live `$(..)`, backtick or process substitution anywhere in
  it, or in an unquoted heredoc of any flotilla line, is refused; what follows `rig run`'s `--` is the remote command's data.
- **The default image is `mcr.microsoft.com/playwright:v1.64.0-noble` (Node 24)**: v1.48's Node 20.18 is too old for
  vite 8 (live check 2). Machines that already allowed the old image allow the new one once with
  `! flotilla rig allow-image mcr.microsoft.com/playwright:v1.64.0-noble`.
- **Existing projects:** posts `main` v7 and `minor` v5 say how seats call `rig run`; `flotilla doctor` names older
  copies.

## 0.9.0 - 2026-10-07

- **`flotilla rig`: a rented machine made, watched and given back.** The person opens a session with
  `rig open --hours --budget` and closes it with `rig close`; a seat that needs a machine outside one is refused and
  its request relayed by the orchestrator as a line carrying only the request's id (`--for rN`). `rig up` raises one
  machine in short calls that resume, from images the person allowed (`rig_images`, grown by `rig allow-image`), on
  vast datacenter hosts, within the session's budget. A watchdog on the machine asks the service to destroy it, or to stop
  it, a set time after our last heartbeat (nothing renews the heartbeat before `rig run`; it needs curl, node or
  python3 in the image). A machine is called ready only once the rig's ssh key is on it and vast
  reports it running, not merely intended to run. A live check on vast (the person's yes, about 0.05 $) measured the
  reaper and the watchdog destroying machines; the spec's section 12 has the numbers. The reaper also drains a machine the service stopped, no longer lists on two passes, or that
  took over 15 minutes to come up. `flotilla fleet` shows the session, the machines and loud lines first.
- From the final review of 0.8.0 and the branch's security reviews: the person guard reads the words bash hands
  flotilla (quotes, escapes, redirections and subshell parentheses resolved, measured against bash) and asks
  flotilla's own parser for the move; it refuses a glob or a variable in a command word, a brace that may expand into
  flotilla or its command, and, beside a program named through a variable, a glob that may be `rig`/`work` or any
  word built at run time next to a guarded move; crontab lines
  split on newlines only; the launcher's last resort keeps its line unless a service was listed; a reap that steps
  aside on the lock is not a miss.
- **Existing projects:** copy the new `orchestrator.md` (template v15) over `.flotilla/posts/orchestrator.md` on
  trunk; `flotilla doctor` names it.

## 0.8.0 - 2026-10-07

- **`flotilla rig`: the safeguards for rented machines, before any machine is rented.** Off by default; the person
  turns it on with `flotilla rig enable --provider <service>`, which a Claude tool call cannot run. Rental services
  are adapters with one contract and one test suite; vast.ai, over its REST API, is the first. A journal of sessions
  and machines whose moves are compare-and-set; a lease renewed only by the work; a reaper in the person's crontab
  (through a launcher in the state directory that survives plugin updates and, with flotilla removed, destroys this
  machine's labelled instances itself) that drains on an expired lease, 15 idle minutes, the session's end or 90 % of
  its budget, destroys only instances listed under the label flotilla gave them, believes "gone" only from a later
  listing, says STUCK after three passes, and is checked for silence by `flotilla rig` and `doctor`. Service answers
  are scrubbed of credentials. Renting and running come next.
## 0.7.17 - 2026-10-06

From a running fleet's notes:
- **`flotilla watch --wait` exits 0 when it wakes on news**, so Claude Code no longer shows each wake as a failed
  task; its first line says `attention (new):` or `nothing new`. `--once` keeps 0/1/2.
- **All the work of a gone session moves in one command**: `flotilla work adopt --from "<gone>" --to "<live>"`,
  naming any row it could not move. The empty-seats line says `flotilla spawn --fill` raises them again.
- **A release the person put off stays out of the batch**: `flotilla work hold <branch> --until "the person"` on an
  accepted branch keeps it out of `flotilla brief`, and `queue`/`land` refuse it until it is unheld.
- **A seat the census calls blocked, with no question in flotilla's queue, reads as idle.**
- **The orchestrator's prompt hook says what is new** and counts the standing items in one line, instead of
  repeating the whole list.
- **Existing projects:** copy the new `orchestrator.md` (template v14) over `.flotilla/posts/orchestrator.md` on
  trunk; `flotilla doctor` names it.

## 0.7.16 - 2026-10-05

- **The lane measures what it held but did not record**: a receipt's setup and its red and timed-out tiers count in
  how long it held the lane; a run stopped mid-way is recorded as killed with its time; a receipt run inside a
  `lane run` teaches its tiers' history; an estimate whose exact command never ran green takes its duration from a
  coarser match and says so.

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
