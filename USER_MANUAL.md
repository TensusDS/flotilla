# flotilla - user manual

The short reference. What flotilla is and why it works the way it does: [README.md](README.md).

## Skills (slash commands)

You run these in an interactive Claude Code session in the project's main checkout.

| command | what it does |
|---|---|
| `/flotilla:doctor` | checks Python, git, Claude Code, the session census, the state directory and the project profile |
| `/flotilla:onboard` | sets the project up: reads what it declares, asks what it cannot read, shows the profile, writes `.flotilla/` |
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

The CLI is `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla`. Every command takes `--help`.

**Fleet**

    flotilla spawn [-o N] [-s N] [-r N] [-j N] [-M N] [-m N] [--post NAME=N] [--dry-run] [--anyway]
    flotilla fleet                 the seats, their trees and their work
    flotilla fleet down            retire every seat but your own
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
| orchestrator | `assign --reader "<session>"`, `hold --until <time> --why "<why>"`, `unhold`, `adopt --to "<session>"`, `urgent`, `walkable`, `release` |
| the person | `approve` |

Worktrees and receipts:

    flotilla tree cut <branch> --tree <path>     a new tree for a new claim
    flotilla tree switch <branch>                switch your home tree to another of your branches
    flotilla receipt run --purpose handover|push  run the test tiers over the current revision

**Machine and guards**

    flotilla lane                         who holds the machine
    flotilla lane run --for <branch> -- <command>
    flotilla guard status                 guards on, hooks installed
    flotilla permit next | list | answer <id> allow|session --mark <mark> | answer <id> deny --why "<why>"

## Daily operation

1. **Start:** `/flotilla:spawn`, then `claude attach <orchestrator id>` and tell the orchestrator what to build.
2. **While it runs:** `/flotilla:status` for the picture, `/flotilla:watch` for what needs you. Answer permission
   questions with `/flotilla:permit` in the orchestrator's session.
3. **Merges you authorize:** the orchestrator shows the batch; you run `flotilla work approve <branch>` for each branch
   you agree to, yourself (with `!` in the orchestrator's prompt, or in a terminal).
4. **A session done or stuck:** `/flotilla:retire "<name>"`. Its open rows are named; the orchestrator adopts them.
5. **End of day:** `/flotilla:down`. Worktrees stay until you remove them with `git worktree remove <path>`.
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
| a run looks green but did not finish | it was killed (often out of memory) and the wrapper returned 0 | read the tail of its output for the passed count; use `flotilla lane run` |
| a permission question is refused after nine minutes | nobody answered within `[broker] wait_seconds` | answer sooner with `/flotilla:permit`, or add an allow rule for that command |
| `permit answer` refused: mark | the answer did not carry the mark of the question shown | run `flotilla permit next` and use the command it prints |
| `onboard`, `permit answer` or `approve` refused: not a person | it was run from a background session or a process with no terminal | run it in your own Claude Code session or in a terminal |
| `onboard write` stops and prints a mark | it shows the whole profile first | read it; if it is right, run `write --confirm <mark>` |
| a push is refused: receipt | no green push receipt over exactly what is pushed | `flotilla receipt run --purpose push` in that tree, then push |
| a push is refused: not approved | a person authorizes merges and has not approved that work | `flotilla work approve <branch>` by the person |
| `gh pr merge` is refused | it does not pin the checked head | add `--match-head-commit <sha>` as the refusal names it |
| a retired session is alive again | a message to a stopped background session resumes it | `claude stop <id>`; address letters to the live holder of a post |
| the hooks say nothing | `python3` on PATH is older than 3.11, or the project is not onboarded | `/flotilla:doctor` |
| `could not tell who calls` / census errors | `claude agents --json` did not answer | wait and retry; `/flotilla:doctor` checks the census |

Where to look when something is unclear: `flotilla work show <branch>` (a row's full history), `flotilla status`
(the whole fleet), `/flotilla:doctor` (the machine), and the state directory `flotilla doctor` prints.
