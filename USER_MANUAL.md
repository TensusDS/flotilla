# flotilla - user manual

The short reference. What flotilla is and why it works the way it does: [README.md](README.md).

## Skills (slash commands)

You run these in an interactive Claude Code session in the project's main checkout.

| command | what it does |
|---|---|
| `/flotilla:doctor` | checks Python, git, Claude Code, the session census, the state directory and the project profile |
| `/flotilla:onboard` | sets the project up: reads what it declares, takes the recommended answers (quick) or asks you each one (custom), shows the profile, writes `.flotilla/`, pushes it to trunk, and offers to raise the fleet with your session as orchestrator |
| `/flotilla:check` | reports drift between the profile and the repository as it is now (CI, trunk, test tiers) |
| `/flotilla:spawn` | raises background sessions, each with a post and its own worktree |
| `/flotilla:status` | who is idle, working, waiting, blocked; whose move each open row is; what deviates |
| `/flotilla:watch` | what needs attention now: dropped balls, deviations, a move nobody alive can make |
| `/flotilla:brief` | the batch ready to ship, in one block, for one yes |
| `/flotilla:permit` | background sessions' permission questions, one at a time |
| `/flotilla:lane` | who holds the machine for long runs and why a run waits |
| `/flotilla:guard` | which command guards are on; installs the git hooks, one yes each |
| `/flotilla:retire` | stops one session and releases its post; its worktree and work stay |
| `/flotilla:down` | retires the whole fleet but your own session |

`flotilla:flotilla` is not for you: it is the arrangement every fleet session follows.

## Command line

The CLI is `${CLAUDE_PLUGIN_ROOT}/bin/flotilla`. Every command takes `--help`.

**Fleet**

    flotilla spawn [-o N] [-s N] [-r N] [-j N] [-M N] [-m N] [--post NAME=N] [--dry-run] [--anyway]
    flotilla spawn --default | --fill    the profile's composition; --fill raises only the seats nobody here holds
    flotilla spawn --lead                your own session leads the fleet; its name comes with your next message
    flotilla fleet                 the seats, their trees and their work
    flotilla fleet down            retire every seat but your own
    flotilla fleet clean [--yes]   remove trees and branches whose work is surely on trunk (a plan without --yes)
    flotilla retire "<name>"       stop one session, release its post
    flotilla helper raise --for <branch> --task "<what>"     a helper in its own tree (from a working session)
    flotilla helper done --summary "<what was done>"         finish as a helper

**Looking**

    flotilla status [--stalled HOURS]
    flotilla watch [--once]        exit 0: nothing needs attention, 1: something does, 2: could not ask
    flotilla brief
    flotilla work show <branch>
    flotilla metrics

**The work ledger** (`flotilla work <move> <branch>`; each post may make only its own moves)

| who | moves |
|---|---|
| main, minor | `claim`, `hand`, `moved --tip <sha>`, `close`, `release`, `wait --on "<whom>" --why "<what>"` |
| reviewer | `take`, `accept --reviewed <sha>`, `fix --why "<what must change>"`, `recuse`, `vouch --commit <sha>` |
| sender | `queue`, `land`, `ship`, `inbatch`, `return --why`, `offledger`, `release` |
| judge | `walked --build <b> --steps "<what>" --saw "<what>"`, `broke --where "<where>" --saw "<what>"`, `unbroke --why "<why>"` |
| orchestrator | `assign --reader "<session>"`, `hold --until <branch or session or "the person"> --why "<why>"`, `unhold`, `adopt --to "<session>"` (or `adopt --from "<gone session>" --to "<session>"` for all the work it owned), `urgent`, `walkable`, `release` |
| the person | `approve` |

Worktrees and receipts:

    flotilla tree cut <branch> --tree <path>     a new tree for a new claim
    flotilla tree switch <branch>                switch your home tree to another of your branches
    flotilla receipt run --purpose handover|push  run the test tiers over the current revision

**Onboarding** (the person only; `/flotilla:onboard` runs these for you)

    flotilla onboard detect | next | check          read-only
    flotilla onboard quick                          the recommended answer to every open question
    flotilla onboard answer <id> <value>            one answer
    flotilla onboard write [--confirm <mark>]       show the profile, then write it after your yes
    flotilla onboard publish                        commit .flotilla/, run the tiers over it, push it to trunk

**Machine and guards**

    flotilla lane                         who holds the machine
    flotilla lane run --for <branch> [--max SECONDS] -- <command>
    flotilla lane stop                    stop your own run in the lane
    flotilla guard status                 guards on, hooks installed
    flotilla permit next | list | answer <id> allow|session --mark <mark> | answer <id> deny --why "<why>"

**Rented machines** (off until the person runs `rig enable`; the README's "Rented machines (rig)" says what it guards)

    flotilla rig                          sessions, machines, what they cost at least, when the reaper last ran;
                                          each waiting run with why it waits and what it is estimated to take
    flotilla rig reap                     drain what should not run, verify it is gone (cron runs it every 5 min)
    flotilla rig enable --provider vast   the person only: turn rented machines on (a Claude tool call is refused)
    flotilla rig disable                  the person only: turn them off; the reaper drains what is still live
    flotilla rig open --hours H --budget USD (--why TEXT | --for rN)
                                          the person only: open a session that may spend; --for answers a request
    flotilla rig close                    the person only: close the open session; the reaper drains its machines
    flotilla rig allow-image IMAGE [--for rN]
                                          the person only: allow one more image for rented machines
    flotilla rig up [--wait S] [--watchdog-minutes M]
                                          a seat raises one machine in the open session; call again to resume
    flotilla rig stop jN                  a seat stops its own run (the person: any); it ends on the machine
    flotilla rig run [--put P]... [--get P]... [--env N=V]... [--wait S] [--max S] -- COMMAND
                                          a seat runs COMMAND on a rented machine and gets its artifacts back
                                          (minutes: call it in the background; the last line names the verdict)

## Daily operation

1. **Start:** if your own session leads the fleet (onboarding offers it), `flotilla spawn --lead` gives it the
   orchestrator's name - shown with your next message - and after that message you raise the rest with
   `flotilla spawn --fill`, so the seats learn the new name.
   Otherwise `/flotilla:spawn`, then `claude attach <orchestrator id>` (`flotilla fleet` lists the ids). Tell the
   orchestrator what to build. The project's `.flotilla/` must be on origin's trunk first.
2. **While it runs:** `/flotilla:status` for the picture, `/flotilla:watch` for what needs you. Answer permission
   questions with `/flotilla:permit` in the orchestrator's session.
3. **Merges you authorize:** the orchestrator shows the batch and the command; for each branch you agree to, you type
   `! <command>` in your own Claude Code session (the orchestrator's, when your session leads the fleet) or run it in a
   terminal. Claude's own Bash call of it is refused, and so is `!` in a background orchestrator's prompt.
4. **A session done or stuck:** `/flotilla:retire "<name>"`. Its open rows are named; the orchestrator adopts them, all
   at once with `flotilla work adopt --from "<name>" --to "<session>"`.
5. **End of day:** `/flotilla:down`. Trees and branches whose work is surely on trunk go with their seats; the rest
   stay and say why. `flotilla fleet clean` shows what is left to remove, `--yes` removes it.
6. **After a plugin update:** stand the fleet down, update, bring `.flotilla/posts/` up to the new templates, raise
   the fleet again.

## Troubleshooting

| what you see | why | what to do |
|---|---|---|
| a move is refused with a reason | the ledger checks evidence; the reason names what is missing | do what it names: run the receipt, record the moved tip, ask the reader |
| `post X may not <move>` for a move the docs list | the project's posts are older than the plugin | bring `.flotilla/posts/` up to the templates of the installed version, commit to trunk |
| a seat waits on `tree switch` | another worktree still has that branch checked out | `git -C <old tree> switch --detach` in the old tree (if it is clean), or retire its session |
| `spawn` refuses: memory | free memory would fall below `fleet.memory_floor_mb` | retire idle seats, or `--anyway` if you know better |
| a test run waits a long time | the lane is held, or memory is low | `/flotilla:lane` names the holder and what it waits for |
| a run in the lane says "stopped at the ceiling" | it ran past ten times the slowest measured tier (at least 10 min), or past `--max` | a hang: find it; a long run: `--max <seconds>` or `[lane] max_run_seconds` in the profile |
| a receipt refuses: "the tree setup ... failed" | the profile's `tests.setup_command` (e.g. `npm ci`) failed in that tree | read the lines it prints; fix the dependency or the network, then run the receipt again |
| a run looks green but did not finish | it was killed (often out of memory) and the wrapper returned 0 | read the tail of its output for the passed count; use `flotilla lane run` |
| a permission question is refused after nine minutes | nobody answered within `[broker] wait_seconds` | answer sooner with `/flotilla:permit`, or add an allow rule for that command |
| `permit answer` refused: mark | the answer did not carry the mark of the question shown | run `flotilla permit next` and use the command it prints |
| `onboard`, `approve` or `--as` refused: not a person | it was run from a background session (the orchestrator included) or a process with no terminal | run it in your own Claude Code session or in a terminal |
| every move refused: no profile on trunk | `.flotilla/` is committed locally but not on origin's trunk | `flotilla onboard publish`, or push it to origin's trunk yourself |
| `work approve` refused as a tool call | approving is the person's own move; Claude's Bash call never is | type `! <command>` in your own session, or run it in a terminal |
| `onboard publish` refuses: trunk is ahead or behind origin | publish pushes only the profile | push or move your own trunk commits, or `git pull`, then publish again |
| `onboard publish` refuses: your own changes | the tree has uncommitted work besides `.flotilla/` | commit or stash it, then publish again |
| `onboard publish` could not push | origin moved meanwhile, or trunk takes pull requests only | pull and publish again, or open a pull request with `.flotilla/` and merge it |
| the orchestrator's own permission request is refused | it cannot put a question to itself | attach to the orchestrator and answer there |
| `onboard write` stops and prints a mark | it shows the whole profile first | read it; if it is right, run `write --confirm <mark>` |
| auto mode refuses the sender's push ("Merge Without Review") or a ledger move | Claude Code's classifier judges it; flotilla lets its own checked commands past only where you opted in | answer the refusal yourself, or - knowing what it means - set `skip_classifier_for_checked = true` under `[permissions]` in the profile and bring it to trunk (README, "Letting checked commands past auto mode's classifier") |
| a push is refused: receipt | no green push receipt over exactly what is pushed | `flotilla receipt run --purpose push` in that tree, then push |
| a push is refused: not approved | a person authorizes merges and has not approved that work | `flotilla work approve <branch>` by the person |
| `gh pr merge` is refused | it does not pin the checked head | add `--match-head-commit <sha>` as the refusal names it |
| a retired session is alive again | a message to a stopped background session resumes it | `claude stop <id>`; address letters to the live holder of a post |
| the hooks say nothing | `python3` on PATH is older than 3.11, or the project is not onboarded | `/flotilla:doctor` |
| `could not tell who calls` / census errors | `claude agents --json` did not answer | wait and retry; `/flotilla:doctor` checks the census |

Where to look when something is unclear: `flotilla work show <branch>` (a row's full history), `flotilla status`
(the whole fleet), `/flotilla:doctor` (the machine), and the state directory `flotilla doctor` prints.
