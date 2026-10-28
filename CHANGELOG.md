# Changelog

Every release is a tagged commit on GitHub. flotilla is pre-1.0: a minor version may change an interface. Why
each change was made is in [docs/specs/2026-09-22-decisions-log.md](docs/specs/2026-09-22-decisions-log.md).

## 0.23.0 - 2026-10-28

- **Leftovers are settled, not just kept (garbage collection, stage 2).** A file with a byte-identical copy that
  stays - in the main checkout, in a store you declare under `[gc] stores`, in the archive, in a commit a ref still
  reaches - lets its tree go. Small unique outputs (`[gc] archive_tree_mb`, 100; `[gc] archive_pass_mb`, 500) move to
  `<project>-gc-archive/` while the tree is removed, and the report tells you after, with `flotilla gc restore`;
  identical files from many trees are stored once, and the report keeps space freed apart from space moved.
- **You answer the rest once per piece of work.** Uncommitted work, files only your own excludes ignore, larger
  outputs, a nested repository or a commit no ref holds wait for `/flotilla:gc`: one question per piece of work across
  its trees - archive and remove, junk, keep, or your own words - with "accept all as proposed" for the archive.
  `flotilla gc apply` carries out exactly what you answered and nothing that moved since you were shown it.
  `answer`, `apply` and `restore` are yours: a background session is refused. Orchestrator template 20 sends you
  there instead of answering for you.
- **A guard against removing the fleet's work by hand.** A session's `git worktree remove --force` of a seat tree,
  `git worktree prune`, forced `git branch` deletion of a fleet branch, deletion of a parked ref or `rm -r` of a seat
  tree or the archive is refused.
- Not yet: the dead-session and daily passes, the archive's lifetime, a low disk (stage 3).

## 0.22.0 - 2026-10-27

- **Finished sessions clean up after themselves (garbage collection, stage 1).** A tree goes when it holds nothing
  that exists nowhere else, and its branch is judged on its own: a clean tree of a seat that died on an unlanded
  branch goes, the branch keeps the commits. Every ref holds a commit - tags, the remote's branches, your own
  `refs/attic/*`. Commits only a reflog reaches are parked under `refs/flotilla/parked/` (they never expire) instead
  of keeping the tree. A branch has landed on origin's trunk, on the local trunk in local flow, or on a long-lived
  line you declare under `[gc] trunks`; the sender's batch branches are recognised by the ledger's merge record,
  never by their name; branches on origin are listed, never deleted. An ignored directory is judged by what is in
  it (a folder holding only `__pycache__` no longer keeps a tree); `[gc] regenerable` adds names; Claude Code's
  runtime state under `.claude/` is regenerable, granted permissions are not. A tree is removed under the ledger's
  lock, judged again first; a submodule, a process working in the tree or an operation in progress keeps it.
- **The orchestrator cleans, you never do.** `fleet down` ends with one cleaning pass and its report - what was
  removed, parked and kept, one line per reason. The orchestrator runs it when you ask to stand the fleet down, and
  once more as its own last step before it closes (orchestrator template 19). `flotilla fleet clean` stays as the
  manual sweep.
- Not yet: the archive for unique ignored files, questions to you with recorded answers, the dead-session and
  daily passes, a low disk (stages 2 and 3 of the design). Until then what is not plainly garbage stays, named.

## 0.21.0 - 2026-10-27

- **Your yes is asked only of code.** Where you authorize merges, `approve_paths` under `[flow]` - gitignore-like,
  `!` excludes - narrows it to work touching those paths: `queue` refuses such work without your `approve` and names
  the files, the brief marks which rows ship without your yes, and the push guard counts unapproved work off the
  paths. Every commit of the work counts, not only the net diff; work on `.flotilla/` always waits for you.
  Onboarding offers "a human, for code": everything waits for you but what it finds plainly not code - docs, tests,
  data and assets, text and media - so a directory added later is yours too; CI, hooks, manifests and build files
  always wait.
- **A wait is not a fault.** Work waiting on other work - a dependency not delivered, a part whose whole has not
  shipped - prints `not yet` and says nothing is wrong, instead of a refusal that told the session to report it. A
  guard that cannot read a command says it is a guard; the skill lists what is not a fault, Claude Code's own
  behaviour included.
- **Landing no longer stops on a binary diff.** A file git diffs as text but whose bytes are not UTF-8 (a float16
  table) crashed the batch accounting for every row; diffs and patch ids now travel as bytes, and command output is
  read with replacement.
- **An order behind released work no longer waits for ever.** A row going after a released row is queued; a released
  row settled by delivered work stands for it; work built on released work is refused with the way out.
- **Moves that were missing.** A reader takes back its own acceptance before the queue (`fix` from `accepted`); a judge
  records a walk the person reversed as a break (`broke` from `walked`); a refused `close` or `hand` names `release` or
  `moved --tip`.
- **Adopt frees the gone owner's tree**, as release does, so the heir can switch to the branch.
- **Rig:** `open` passes a session already past its end; `--get` of a type that never comes back is refused before a
  machine is rented, naming `[rig] get_types`.
- **Smaller:** a tree switch held up by untracked files names them and suggests `.gitignore`; a session in a seat's
  tree that holds no post is named as the subagent or fork it most often is.

## 0.20.0 - 2026-10-20

- **Organize: the work already here is taken in.** `/flotilla:organize`, run by onboarding before and after
  publishing and on its own at any time, walks what was made before flotilla - open pull requests, chains and native
  GitHub stacks of them, branches, commits on your local trunk, commits riding beside the profile on onboarding's
  branch, uncommitted edits - and asks you about each piece of your own: take it in, ignore it, or your own words.
  Other people's and other AIs' work is listed and never touched unless you name it. A chain is asked about once:
  separate works, one work in parts, one work whole, or Claude works it out and proposes one, with no answer
  recommended. Nothing leaves the ledger but through `flotilla organize apply`, from your recorded answer; a dismissed
  or timed-out question changes nothing, and an item that moved since you were asked is asked again.
- **Taken-in work is reviewed like any other.** A new person-class move, take-in, opens the row as work in progress
  under `(taken in)`, pinned to the post its review follows, with the adoption point where accounting starts and the
  retarget and closes your answer authorized; the orchestrator gives it to a seat. `queue` retargets its pull request
  to trunk itself, and refuses one whose base is not on origin's trunk (a restack) or that GitHub holds in a native
  stack; `ship` closes the lower pull requests the row records.
- **A native stack is unstacked before it is taken in**, through GitHub's REST endpoint (no extension needed), and
  read back: a member GitHub leaves stacked stops it, said by number. Measured on a scratch repository: a stack
  member can be neither retargeted nor merged with `gh pr merge`.
- **Nothing is lost on the way.** Uncommitted edits are split into branches only by a plan you confirmed: everything
  is kept first under `refs/flotilla/kept/<sha>` (untracked files included), each branch is committed with the
  repository's hooks in a tree of its own and compared byte for byte before anything leaves the tree, and secrets are
  never committed (betterleaks or gitleaks when installed, high-signal patterns otherwise). Dropping commits or
  deleting a branch is kept under `refs/flotilla/dropped/<sha>` and left to you to type after `!`.
- **Publish takes a profile already committed**, by fast-forwarding trunk - the shape a half-finished onboarding
  leaves - and the commits beside it you named; any other commit ahead of origin is still refused. The 0.19.0 warning
  about pull requests outside the ledger is replaced by organize.
- **`gh pr merge` into a base other than trunk is refused** by the push guard: with no retarget such a merge lands
  the work in the lower branch.
- **A guard keeps sessions' hands off tracked pull requests**: `gh pr close`, `gh pr edit --base`, REST and GraphQL
  writes to them, and `gh stack` are refused from a session; a word the shell expands is refused as not told.
- The sender and orchestrator post templates gain one line each (versions 10 and 18). README, the user manual (four
  troubleshooting rows) and both skills say all of this.

## 0.19.0 - 2026-10-19

- **Onboarding says who merges, not only who approves.** The plain words before the yes now name the session that
  runs the merge - of the fleet's seats only the sender runs `gh pr merge` or pushes to trunk, the orchestrator never
  does, and whether the push guard holds them to it - and, where you merge pull requests yourself, that no session
  merges them.
- **It says whom the guards judge.** Every Claude Code session that has flotilla on and works in the repository, your
  own interactive one included, even one that holds no post: with the push guard on, its pushes to trunk or a tag,
  `gh pr create` and `gh pr merge` need a green push receipt, and where you authorize merges a push to trunk and
  `gh pr merge` also need your approval of the work's ledger row - so work no row carries you merge yourself after
  `!`. What you type after `!` is not asked; the `pre-push` hook, where installed, asks your own git pushes.
- **Open pull requests made outside the ledger are named.** Where a session merges pull requests and the push guard is
  on, `onboard write` names the open pull requests on GitHub that no open ledger row knows before the yes, and
  `onboard publish` again once the rules are on trunk, with the ways out: merge them first, have a builder claim the
  branch, or merge it yourself with `!`. A warning, not a refusal; a `gh` that cannot answer is said. Titles are cut
  short and marked as their authors' words; a fork's pull request, a branch named like trunk, or a branch name a
  shell would read gets no suggested command.
- **The onboarding's own pull request.** Until origin's trunk carries a profile the guards judge by the tree's own,
  so a pull request with `.flotilla/` needs a push receipt - and where you authorize merges, no session can merge it,
  since your approval cannot be asked yet: `publish` and the docs now say to merge it yourself. README, the user
  manual (with three troubleshooting rows) and the onboard skill say all of this.

## 0.18.0 - 2026-10-18

- **A post can say how hard its seats think.** `effort:` in a post's header - `low`, `medium`, `high`, `xhigh` or
  `max` - starts its seats with `claude --effort` at that level. Onboarding asks once: Claude Code's default, the
  reviewer at max, every post at high, or your own `post=level` pairs; the answer goes to the profile as
  `[fleet.effort]`, one level per post, and a post's own `effort` wins. The templates name none, so nothing changes
  until you set one. Claude Code runs an unknown level at its default with a warning nobody sees, so flotilla checks
  it: a post naming one does not load, and an unknown level or a key naming no post in `[fleet.effort]` refuses the
  spawn before any seat is raised. `spawn --dry-run` shows each seat's level.
- **A spawn that the launch would refuse raises nothing.** A post asking for a permission mode wider than the
  profile's was refused at its own launch, after the seats before it were already up; spawn and `helper raise` now
  ask every launch check first.

## 0.17.0 - 2026-10-16

- **A stuck builder gets help in a set form.** The main and minor posts say when a session counts as stuck on a
  technical problem - one goal has not given way to three different approaches, or 45 minutes brought no new result
  from a probe - and what it does then: marks its row `wait --on "consult"`, writes down the facts (commands and their
  output, no guesses), and raises two in-session subagents that get the facts and its tree and none of its guesses -
  an expert with up to three explanations, each with a probe, and a researcher who names the failure. It runs the
  probes itself, names the explanation that held in the fix's commit message, and after two rounds without one takes
  the question to the person through the orchestrator. The move stays the builder's under a `consult` mark: the stop
  guard does not let it stop with nothing in flight, and the orchestrator's watch calls it a dropped ball if it sits
  idle. The posts also say which signs a reason in a flotilla command may hold - any other, a question mark too,
  makes the call a permission question for the person. Post templates main v10, minor v8 and orchestrator v17:
  compare your project's posts with them.

## 0.16.2 - 2026-10-16

- **A ceiling you set to something unusable says so.** `rig_max_hours = inf` in `machine.toml` reads as the
  default 8 hours, as any value that is not a positive finite number does - and `rig open --hours 10` then refused
  with "over the 8-hour ceiling (machine.toml rig_max_hours)", pointing at a file that says inf. The refusal now
  says the key is not a usable number and the default applies, and `flotilla rig` lists every such key under its
  ceilings. A key left out is the default by choice and is not listed.
- **A very high hours ceiling no longer crashes `rig open`.** With `rig_max_hours` set high, `--hours 1e8` passed
  the ceiling and the session's end fell past year 9999 (`--hours 1e11` past what a time span holds); `rig open`
  died with a traceback. East of UTC an end in the last hours of 9999 was even written first, and `flotilla rig`
  crashed showing it until the session was closed. `rig open` now refuses an end within a day of the last date
  the journal can write.

## 0.16.1 - 2026-10-15

- **A stopped lane run stops all of it.** Past its ceiling, or on `flotilla lane stop`, the lane sent SIGTERM to
  the run's group and waited for its first process only. A shell script dies on SIGTERM at once, so nothing was
  killed after it, and a process inside that held SIGTERM - a test runner writing to a file - kept computing after
  the lane said the run was stopped; the next run then waited behind it as a foreign run. The lane now waits until
  the whole group is gone and kills what is left after 5 s - and returns only once that is done, where the
  ceiling's stop ran in a thread `lane run` exited without. A `lane stop` that came while the lane waited for a
  command that had closed its output stopped nothing; it now stops the group too, and a second signal during the
  5 s kills what is left at once.
- **A rented machine's run counts only its own machine's runs as neighbours.** A run's GPU figure is marked shared
  when another run is on the machine. Any tagged process on the host counted, which on a rented machine is right,
  but two test suites side by side on one computer saw each other's runs.
- **The test suite no longer swallows SIGTERM**, and a killed rig run whose verdict goes missing in CI now shows
  its own output in the failure (it went missing twice on ubuntu with Python 3.13 and could not be reproduced).

## 0.16.0 - 2026-10-15

- **The hours and budget in the line you paste are yours to set.** When a seat asks for a machine and no session
  is open, the orchestrator hands you `! flotilla rig open --hours 2 --budget 1 --for rN`; the 2 and the 1 were
  written into the code in three places. They are now `rig_default_hours` and `rig_default_budget` in
  `machine.toml`, kept when the machine is measured again, and one function writes the line for all three. The
  suggested hours never go above `rig_max_hours`, and the numbers read back exactly as set, so the line is never
  refused by the ceiling it came from. `rig open` still takes `--hours` and `--budget` from you every time: the
  suggestion fills in the line, it opens nothing.
- **A ceiling or suggestion that is not a positive finite number reads as its default.** TOML spells infinity
  `inf`: `rig_max_hours = inf` was taken as written, so no session was ever too long, and `rig_max_hourly = inf`
  refused every machine with a misleading "would pass 90 % of its budget" (`rig_max_machines = inf` already read
  as 1). Both now read as their defaults, 8 hours and 0.60 $. An integer too large for a float, which TOML also
  allows, made every reader of the settings fail - the orchestrator's attention list among them; it reads as its
  default too.

## 0.15.0 - 2026-10-14

From a field report on twosuns (flotilla 0.10.2, 2026-10-14): two runs a minute apart failed on the same stale vast
offer, each leaving a failed machine that held the one-machine ceiling; a run told to wait 900 s was refused in 0 s;
and `flotilla rig` called the reaper silent while cron was firing.

- **A stale offer is passed over.** `rig up` and `rig run` took the cheapest offer and nothing else: when vast
  answered that it no longer rents it ("no_such_ask ... is not available") while its search still listed it, every
  run failed the same way until vast dropped it. Now the next offer is tried, up to five, all within the same
  ceilings, and the refusal says how many were tried. Any other failure is refused at once as before: it may come
  after vast made the machine, and a second try could pay for two.
- **A try that rented nothing frees the machine ceiling at once.** A machine refused for no offer, or for offers
  that were all gone, was left failed until the next reaper pass, holding the one-machine ceiling; vast never made
  it, so it now leaves at once. A failure that may have made a machine still waits for the reaper to look for it.
- **`rig run --wait` waits for an offer too.** It waited only for room on a machine, so an empty market came back
  in 0 s. Now it asks the market again every minute until its `--wait`, says so while it waits, never tries again
  an offer it found gone, and is refused (exit 2) "no offer within the ceilings within --wait" if nothing turned
  up; a run whose last wait was for room still ends "no room within --wait" (exit 3). Both name the seconds waited,
  which the last line's own count leaves out. Nothing is spent while it waits.
- **The reaper is no longer called silent just after it was armed again.** With nothing to guard it removes its own
  crontab line - in the report, at 05:05, when the last session closed - and the evening's session put it back;
  until its first pass, `flotilla rig`, `flotilla doctor` and the fleet's attention list compared now with 05:05
  and asked whether cron was running. Silence is now counted from the later of the last pass and the opening of
  the session that needed the reaper again; a mark from a clock that ran ahead counts for nothing.

## 0.14.0 - 2026-10-13

- **Only a post that may land pushes to trunk or a tag, or merges a pull request.** The sender was "the only
  session that writes trunk" in the README and in its post's instructions, but the push guard asked only for a
  green receipt: any seat holding one could push to trunk. Now a session that holds one of the project's posts
  lands - `git push` to trunk or a tag, `gh pr merge` - only if its post has the `land` move. A call answers to
  the session that made it and to every seat its process runs under, so a session a seat starts inside itself is
  judged as that seat. The refusal names the live sender the work goes to once it is handed over and accepted;
  with no live sender, the seat is told to tell the orchestrator, and the orchestrator to tell you. There is no
  override: you can still push with `!` or from a terminal, raise a sender (`flotilla spawn -s 1`) or have the
  work adopted. The `pre-push` hook asks the same of the background sessions a push hidden in a script runs
  under.
- Not judged by a post: a session that holds none of the project's posts (your own, another project's seat), and
  a project whose trunk carries no post that lands. **If your own Claude Code session leads the fleet as its
  orchestrator, a push the model types there is now refused; you push with `!`.** A push hidden in a script in
  your own session is not told apart from yours. Where the session census cannot be asked, a push that lands is
  closed until it can - at your terminal, `git push --no-verify` is your way through.
- **Fixed: an override could waive your approval of a merge.** Where you authorize merges
  (`merge_authorized_by = "human"`), `FLOTILLA_GATE_OVERRIDE="..." gh pr merge 5` - a merge no revision pins -
  was recorded as overridden and ran: the approval was never asked. Such a merge is now closed until it names
  `--match-head-commit`; so is a push whose tree could not be told, and one whose receipt check fell. For a seat
  whose post may not land, an override no longer opens any push or merge whose revision could not be told.
- A harmless word that is an option's value frees nothing: `gh pr merge 5 -b --help` and `-sb --help` are merges
  with a body of "--help", `git push -o -n origin main` and `--push-o -n` are pushes with an option of "-n",
  `gh pr create --title --dry-run` opens a pull request titled "--dry-run", and each is judged as what it is.
  `-sR o/r` and `-Ro/r` name a repository as `-R o/r` does; `git push -n --no-dry-run` and
  `gh pr create --dry-run --dry-run=false` are what they end as, not dry runs. A merge
  that names its pull request by URL from outside any project is refused rather than let through unjudged.
- A seat's merge made from a directory outside every project - however it names the repository, or without
  naming it - is asked for its post by the project the seat stands in: a merge has no second barrier. Only the
  post: that project's receipts and approval say nothing about work in another repository. Where that project's
  own rules cannot be read from origin, such a merge is closed, as a push to its trunk would be.
- A guard that fell before it could ask who calls - a git that timed out, a project file a seat wrote into a
  subdirectory - is judged like a door whose revision could not be told.
- **A post on origin's trunk that cannot be read now closes every push that lands, for everyone**, as a broken
  profile there already did: who may land is not guessed. You fix it from your terminal with
  `git push --no-verify`.
- `flotilla guard check` asks which seats the call runs under, as the hook does.

## 0.13.0 - 2026-10-10

- **The account's credit beside the local count** (rig design, section 5, promised since 0.9.0): `flotilla rig`
  shows the vast account's credit at the session's open and now, and how far it moved - for the whole account,
  so another machine on it or a top-up moves it too. It is read when the session opens and at the end of every
  reaper pass while a session is open or closing, with a 10-second limit and not at all when the account's listing
  failed in that pass. It decides nothing - the 90 % drain still runs on the local count - so nothing it does fails
  a pass, delays a session's close or becomes the reason a pass is shown failed. Only the number leaves the adapter
  (vast's answer also carries the account's own key, session and address); a key that may not read the account
  (401/403) is said, not an error.

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
