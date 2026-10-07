# Rig: rented machines for heavy runs - design

Date: 2026-10-06. Status: revised after review (section 12), for the person's review. Agreed with the person section
by section in the session of 2026-10-06; this document collects those sections. Probe behind it (2026-10-06, vast.ai): an RTX A5000 datacenter
instance was up in about 2.5 minutes; WebGL in headless Chromium ran on the NVIDIA GPU with `--use-angle=vulkan` once
`/usr/share/glvnd/egl_vendor.d/10_nvidia.json` existed; the twosuns night scene rendered in 14-18 s against about two
minutes on SwiftShader on the field machine; billing settled 1-2 minutes after the instance was destroyed.

## 1. What it is for

Some of a fleet's work needs more than the machine it runs on: GPU rendering for screenshot jobs, a heavy suite on a
laptop. `flotilla rig` lets a fleet's sessions run such a command on a machine rented by the hour, with the money
spent only by the person's yes and the machine never outliving the session that took it.

**The person's scenario.** At night minor 8 must shoot frames of the night scene. The orchestrator sees no open rig
session and asks the person: "open a GPU session for 3 hours, up to 2 $? `! flotilla rig open --hours 3 --budget 2
--why "night frames"`". The person pastes the line. Minor 8 runs `flotilla rig run --get shots/ -- node
tools/shoot.mjs --scene night`. In about 2.5 minutes a machine is up, ten scenes render in three minutes, the frames
arrive in minor 8's tree. After 15 idle minutes the reaper destroys the machine, checks that the provider no longer
lists it, and records what the session cost. `flotilla fleet` and `watch` showed the orchestrator every state on the
way.

**Off by default.** A fresh install has no key, no provider and no cron; `flotilla rig ...` answers in one line that
rig is off on this machine and how the person turns it on (`flotilla rig enable`, section 4).

## 2. Parts

- **The rig journal** (`<state>/rig/rig.jsonl`, written under a lock like the lane's): sessions, machines, runs and
  their transitions. Every state the orchestrator sees is folded from it.
- **Rental services are adapters, chosen by name** (`rig_provider`, `rig enable --provider <name>`). Nothing else in
  rig knows which service it talks to: the journal, the lease, the reaper, the labels and the budget are the same for
  all. The contract every adapter keeps:
  - `offers(filter)`, `create(offer, image, onstart, label) -> id`, `address(id)` (stage 2);
  - `instances(key)` - **every** instance of the account, each as exactly `instance`, `label`, `status`, `hourly`,
    through every page; nothing else of the service's answer leaves the adapter, so a credential in it never does;
  - `destroy(key, id)` - asks for a destroy; an id the service could not have issued is refused before any request;
  - a failure is an exception, never a value: a non-2xx answer, a timeout, a body that does not parse;
  - the label lives wherever the service keeps a free-text name (vast: `label`; another: a tag or the name);
  - **one standalone file** (`flotilla/rig/providers/<name>.py`), standard library only, importing nothing from
    flotilla: the reaper's launcher loads a copy of the same file when no flotilla is installed (section 5), so the
    code that guards money in the last resort is the code that guarded it every day.

  One shared contract suite runs against every adapter through its own double of the service. A new service is one
  adapter file and that suite passing. The first adapter is vast.ai over its REST API; `ssh` for the person's own
  machine comes in stage 3 (section 9). A generic adapter whose commands the person writes in a config file is not
  offered: such commands run holding a key to money, anyone who can write the config writes them, and nothing could
  check that their "list" is complete, which is what "gone" rests on.
- **`flotilla rig enable` / `disable` / `open` / `close`** - the person's moves (section 4).
- **`flotilla rig up`** - raises one machine inside the person's open session (stage 2a, so a machine can be made and
  watched before any run exists); with no session open it records a request the orchestrator relays.
- **`flotilla rig run`** - the sessions' command; it renews its machine's lease while it runs (stage 2b).
- **`flotilla rig reap`** - the reaper, run by cron through a launcher in the state directory.
- **`flotilla rig`** - what stands where.

## 3. States

**A machine:** `requested` -> `provisioning` -> `ready` <-> `busy` -> `draining` -> `gone`. Side states: `failed` (did
not come up; the provider's id, if any, still goes through `draining` -> `gone`), `stuck` (the reaper could not make it
disappear; money may still be running - loud everywhere). `busy` carries who, which command, since when.

**A session:** `open` (until when, budget, spent so far, who asked, why) -> `closing` -> `closed` (final cost).

A transition is an event in the journal with its time and its reason (`lease expired`, `idle 15 min`, `budget`,
`session ended`, `closed by the person`, `lost`).

**What the orchestrator sees.**

- In `flotilla rig` and in `flotilla fleet`: one line per session and machine, e.g. `rig: session until 23:00, 0.41 of
  2.00 $; machine 54532850 RTX A5000 busy (minor 8, "night frames", 3 min)`.
- In `watch`, separate items:
  - a machine provisioning for over 10 minutes;
  - 80 % of the budget spent;
  - a session ending in 15 minutes with runs still going;
  - a request for rig with no session open;
  - any `stuck` machine (a loud item, repeated until it clears).

## 4. Who may do what

Every session on the machine runs as the same user, so nothing below is a wall against a determined program of that
user's; it is a door each honest path has to pass, the same standard as `flotilla work approve` (decision 183,
`guards/person.py`). Stated plainly, so no one reads it as more:

- **Turning rig on and off** is `flotilla rig enable --provider vast` / `flotilla rig disable`. Both are the person's
  moves: refused to any Claude tool call by the guard hook (a command the person types with `!` never reaches the
  hook), refused to a background session or a process without a terminal (`caller.person_refusal`). They write the
  `rig` keys of `machine.toml`; measuring the machine again keeps them (`PERSON_KEYS`).
- **The files that carry the person's word** - `machine.toml`, the services' keys, the rig's state directory - are
  refused to Claude's `Edit`, `Write`, `MultiEdit` and `NotebookEdit` tools by a guard hook on those tools. Not
  caught, and named as limits: a shell command that writes them; an MCP server's own write tools; a session where
  flotilla's hooks do not load - with a per-project install that is every session outside those projects, so
  `rig enable` and `doctor` recommend installing flotilla for the user once rig is on. The guards fail open (a guard
  that crashes lets the edit through and says so on stderr): they guard the honest path, not a wall.
- **Opening a session** (`flotilla rig open --hours H --budget USD --why TEXT`, stage 2) and **closing one** are
  the person's moves under the same guard. A session that needs a machine and finds none open is refused with a line
  naming the orchestrator, and the refusal is written to the journal as a request. The orchestrator relays it to the
  person as a ready line to paste (`! flotilla rig open ...`). The orchestrator template gets this duty.
- The yes cannot exceed the machine's ceilings (section 5, layer 3); a request above them is refused with the
  ceiling named. Turning rig on is not consent to spend: money is spent only inside a session the person opened.
- **Relayed lines carry a request's id, never a session's text** (review of the 2a plan, 2026-10-07): a seat's reason
  is a string any session typed, and a line the person pastes with `!` runs in their own shell, past every guard. The
  orchestrator passes `! flotilla rig open --hours 2 --budget 1 --for r3`; `rig open --for` reads the reason back from
  the journal. Requests belong to the project that made them, and expire after a day.
- **The image is the person's to allow** (the person's decision, 2026-10-07): `machine.toml` `rig_images`, written
  with the shipped default by `rig enable` and grown by `rig allow-image` (`--for rN` relays a project's request). A
  project's `[rig] image` is only a wish; `rig up` runs nothing the person has not allowed.

## 5. Safeguards: a machine never outlives the side that took it

Three layers; each fires if the one before it did not.

**Layer 1 - a lease renewed by the work itself, and a reaper.**

- **Every machine is written to the journal before the provider is asked for it** (`requested`, with its label),
  so an instance carrying a flotilla label always has a journal entry - unless the journal itself was lost.
- Every machine carries a lease: now + 30 minutes. **Only the work renews it**: a `rig run` while it runs (every 5
  minutes, with its pid and start mark recorded on the machine) and at its end. Nothing renews a lease because a
  session is alive: a lease that tracks the orchestrator's pulse would keep an idle machine paid for as long as the
  orchestrator lives.
- A `busy` machine whose run's process is gone goes back to `ready` (`its run is gone`), so the idle rule applies to
  it, the way the lane sweeps a booking whose process died.
- The reaper drains a machine when any of these holds:
  - its lease expired;
  - it has been `ready` for 15 minutes;
  - its session is not open (ended, closed, over budget);
  - rig was turned off and the machine is not `busy` - off stops new spending at once and lets a running job end.
- **Draining means verifying, one pass apart.** A pass destroys a draining machine's instance; the next pass asks
  the provider's listing. Absent: `gone`. Still listed: the destroy is repeated; still listed after three destroys,
  the machine is `stuck`. A provider still listing an instance seconds after its destroy is therefore not an attempt.
- **A destroy is checked against the label.** The reaper destroys an instance only when the journal's label for that
  machine is this field machine's key with that machine's id, and the listing shows the instance with exactly that
  label. An empty label, another machine's key or another id is never enough. Any other label - the person's own instance named by a damaged
  or forged journal line - is never destroyed; the machine is marked `stuck` with the reason `label mismatch`.
- **Orphans are decided by the label, not by the instance id.** An instance labelled
  `flotilla:<this machine's key>:<machine id>` is an orphan unless that machine is live and holds this very instance,
  or holds none yet (it is being created). So a duplicate made by a retried create, or the real instance a damaged
  line stopped naming, is destroyed rather than left to bill. The listing is taken first and the journal read after it, so a machine written to the journal while the
  listing was taken is still known. **An instance without this machine's flotilla label is never touched**: the
  person's own vast work and another field machine's instances are safe.
- **One reaper at a time.** A pass takes a non-blocking lock; a second pass (cron and a manual `rig reap` together)
  says the reaper is running and exits. Every move the reaper makes is a compare-and-set: re-checked inside the
  journal's transaction against the state and lease it decided on, and skipped if a run renewed it meanwhile.
- **When the journal is damaged**, the reaper does not stop: it destroys every instance carrying this machine's
  label, on every service with a key in place, and says so loudly. Stage 2's `rig open` and `rig run` refuse while the
  journal is damaged, so nothing is created only to be destroyed. A damaged journal can no longer say what is wanted, and money running is the worse error.
- **The machine key** (in the labels) is written once, with a backup beside it. A damaged key file is recovered from
  the backup, never from the journal's labels (any session can write those) and never silently replaced: a new key
  would make every earlier instance an orphan no pass could recognise. With neither copy readable, the pass still
  drains and records, but destroys nothing, and says so loudly until the person restores the key.

**The reaper's launcher.** Cron must keep working when every session is dead and the plugin was updated or removed,
and the plugin cache path is versioned (Claude Code marks superseded versions `.orphaned_at` and deletes them later).
So cron does not run the plugin's path:

- The crontab line runs a launcher written into the state directory: `<state>/rig/reaper.py`, stdlib only, copied
  from the plugin when a machine is first requested and refreshed by every later rig command.
- The launcher trusts only the marketplace recorded beside it when flotilla installed it (`flotilla@flotilla`, say),
  never a plugin of the same name from another marketplace; and an older flotilla never rewrites what a newer one
  installed. It runs the newest trusted flotilla whose directory still exists; one that does not leave a fresh mark of
  a pass (a missing module, a Python older than 3.11) is skipped for the next.
- **With no flotilla that reaps, on two passes in a row** (one miss may be `installed_plugins.json` caught
  mid-rewrite), the launcher is the last resort: for every adapter copied beside it (`<state>/rig/providers/`) whose
  key is in place, it destroys every instance carrying this machine's label, under the same lock as the reaper. When
  every service was listed and none shows such an instance, it removes its own crontab line.
- The line names an interpreter that is checked to exist, to be 3.11 or newer, and not to be a temporary environment
  (`machine.toml`'s measured `python3`, else `/usr/bin/python3`, else the first `python3` on `PATH`); a path containing `%` (which cron
  reads as a newline) is refused. The mark carries a hash of the state directory, so a second state directory's rig
  never removes this one's line.
- **Every pass writes `<state>/rig/last-reap`, with whether it did its work.** While anything lives and the last pass
  is older than 12 minutes, or the last pass could not ask a service, `flotilla rig`, `doctor`, `watch` and the
  session-start hook say so loudly: a crontab line that never fires (a machine with no cron daemon, as on WSL by
  default) or a revoked key is otherwise indistinguishable from a working reaper. `doctor` also names a crontab line
  whose launcher or interpreter is gone.
- **Every edit of the crontab line happens under the reaper's lock**, so a pass that found nothing to guard never
  removes the line a session opening at that moment just needed (stage 2's `rig open` takes the same lock).

**Layer 2 - a watchdog inside the machine**, for the case where the whole field machine died together with the cron.

- vast injects into every container a key scoped to that one instance (`CONTAINER_API_KEY`, with `CONTAINER_ID`;
  vast's FAQ documents stopping the instance with it, and says nothing of destroying it). flotilla puts no key of its
  own on the machine.
- The instance's start script runs a loop over a heartbeat file that our side touches over ssh on every run and
  every lease renewal. When the heartbeat is older than 45 minutes, the watchdog asks vast to destroy the instance
  with that key, and when that is refused, to stop it. The limit is passed as `FLOTILLA_WATCHDOG_MINUTES` (5 to 45;
  `rig up --watchdog-minutes` lowers it for the live check). Which of the two calls the key may make is measured by
  the live check, not assumed.
- Stage 2's live check measures both paths: that a self-destroy ends billing, and what a stopped container costs.

**Layer 3 - ceilings.**

- **The session's budget** comes from the yes. Spending is computed locally - each machine's hourly price times the
  minutes begun since it was created, plus 2 minutes of billing after a destroy - and the price is refreshed from the
  listing's `dph_total` on every pass. The local figure is a floor, not the bill: traffic and storage are billed
  apart. So a session drains at **90 % of its budget, projected to the next pass** (spending now plus one pass at the
  current rate), and `flotilla rig` shows the account's credit at the session's open and now, when the key may read
  it, so the person can see the real bill. The refreshed price is applied to the machine's whole life: the local
  figure is an estimate, the service's bill is the truth.
- **The machine's ceilings** live in `machine.toml` and only the person changes them (section 4):
  - at most 1 machine at a time (`rig_max_machines`);
  - at most 0.60 $/hour (`rig_max_hourly`);
  - a session at most 8 hours (`rig_max_hours`).
- **Offers are filtered.** Datacenter hosts only, verified, reliability >= 0.98, and the project's GPU and disk
  minimum.

**Calling the provider.** The vast adapter talks to vast's REST API directly with the standard library (`urllib`),
not through the `vastai` CLI. Read in the CLI's source (1.6.0): on a 401 "Invalid user key" or an expired 2FA session it
deletes the person's 2FA session file and retries with the person's own account key from `~/.config/vastai/`, which
would silently defeat a scoped key; it exits 0 after an API error; and without `-y` it prompts. Over REST:

- the key is sent only as `Authorization: Bearer`, never in a URL or an argument list;
- the listing is `GET /api/v1/instances/`, followed through every page (`next_token`); a destroy is
  `DELETE /api/v0/instances/<id>/`;
- every request has a 30 s timeout; an HTTP status other than 2xx, a timeout, or a body that does not parse is a
  failure, never a success, and its text is scrubbed before it is shown;
- the base URL is fixed to `https://console.vast.ai`, so no environment variable can send the key elsewhere, and
  redirects are not followed (urllib would carry the `Authorization` header to wherever one points);
- any failure of the request - a refused connection, a cut answer (`http.client` errors are not `OSError`) - is the
  adapter's error, never a crash of the pass; an instance id is ASCII digits only.

## 6. Keys and secrets

- **Each service's key** lives only in `~/.config/flotilla/rig/<service>.key` (vast: `vast.key`): a regular file
  (not a symlink), owned by the user, mode 600. Anything else is a refusal to run. It is never in a project, a journal, a run's environment, an
  argument list, or on the machine. The plugin ships no key and no key field with a value. `machine.toml` carries
  `rig = "off"` and `rig_provider = ""` until the person runs `rig enable`.
- **A scoped key is asked for** in the onboarding text. The permissions it needs are `instance_read`,
  `instance_write`, `misc` (searching offers). The rig's ssh key is registered once by the person with the account
  key, so the scoped key needs no `user_write`.
- **2FA (open).** In the probe, the account key needed a TOTP session. vast's documentation does not say whether a
  scoped key needs it too. Stage 2 measures this with the person's scoped key, and this spec is amended:
  - if it does, `rig open` takes `--totp CODE`, and the code is part of the yes;
  - if it does not, nothing changes.
- **Provider answers are scrubbed** before anything is printed or written. The journal keeps only id, GPU, price,
  state, address and label. Every field whose name says it holds a credential is dropped at any depth, its value is
  masked wherever else it appears, and error text is masked with the key in use and every value the scrub found. In
  the probe the instance's key reached the log.
- **The rig's ssh key** is its own (`~/.ssh/flotilla_rig_ed25519`), made by `rig enable`. A machine's host key is
  recorded on first contact (`StrictHostKeyChecking=accept-new` into a per-machine known_hosts file under the state
  dir). It is checked on every later connection; a changed host key ends the connection and marks the machine `lost`.

## 7. A run

`flotilla rig run [--put PATH]... [--get PATH]... [--env NAME=VALUE]... -- COMMAND`

Revised 2026-10-07 after two reviews of the 2b plan and the person's decisions: several runs share a machine; the
ceiling is the rig's own; setup runs in each run with shared download caches; the refusals on `--put`, `--get` and
`--root` widened.

**Gate.**

- Rig on. A session open with budget left.
- `--root` is the same repository as the caller's working directory (a seat cannot name a peer's tree or a dotfiles
  repository).
- A tree with a committed HEAD. Uncommitted changes to tracked files are refused, with a hint: commit, or pass the
  file with `--put`. A run is tied to a revision so its result can be repeated.

**Several runs on one machine** (the person's decision, 2026-10-07: the machine exists for parallel runs).

- A run waits for **room**, not for a free machine. In 0.10.0 room is: fewer than two runs on the machine (a floor
  that always holds), or, beyond the floor, the machine's live free memory and free GPU memory above their margins
  (read from the machine when the run asks), every run on it in its command for a minute (one that is still in its
  tree or setup has not taken its memory yet), and fewer runs than the machine's CPUs (never under two). Runs are admitted in order of arrival.
- **0.11.0 packs by estimates:** each run on a rig machine is measured (seconds, peak memory of its process group,
  cores, GPU memory) with the machine's shape, under the lane's signature ladder; a run is admitted when its estimate
  fits what the running runs leave; among the runs that fit, the longest goes first and short ones fill the rest;
  a run that waited past a threshold goes next as soon as it fits, so a long run is never starved by short ones.
- With no machine of the session and the machine ceiling not reached, the run requests one (as `rig up`) and waits
  through `provisioning`. A waiting run has a `--wait` (default 900 s).
- `rig run` takes longer than a Bash tool call: seats call it in the background, and the docs and post say so.

**What goes there.**

- `git archive <revision>` is piped over ssh into `/work/<project>/rev/<sha>`, checked against its sha256 and size on
  the machine before it is marked complete; a revision already there is not sent again.
- Each run works in its own copy: `/work/<project>/runs/<run id>`. A `--put` file or an artifact never reaches
  another run.
- `--put PATH` adds files git does not track: a data snapshot, a built asset. Refused:
  - `.env*`, `*.pem`, `id_*`, `credentials*`, `.npmrc`, `.netrc`, `.pypirc`, and any `.git` path component;
  - anything outside the tree, and any real path under flotilla's state directory, `~/.ssh`, `~/.config/flotilla`
    or `~/.claude`;
  - symlinks, which are never dereferenced.
- The setup command (`[rig] setup_command`, else `[tests] setup_command`) runs in each run's own directory, so an
  editable install points at that run's revision. Download caches are shared on the machine (`/work/cache`: npm,
  uv, pip, yarn), so a second run installs from the cache. If a warm install proves slow, a shared tree comes later,
  by measurement.

**Environment.**

- ssh reads no config of the person's (`-F /dev/null`): no agent or X forwarding, no multiplexing, no proxy, no
  `SendEnv`. Its stdin is closed for calls that send no data.
- No variable crosses from the field machine except those named with `--env`. Names containing `KEY`, `TOKEN`,
  `SECRET` or `PASSWORD` are refused. Values are expanded by the seat's own shell before flotilla sees them; the
  docs say so.
- The image and GPU come from the project's profile (`[rig] image`, `gpu`, `disk_gb`). The default image is
  `mcr.microsoft.com/playwright:<pinned tag>-jammy` with `NVIDIA_DRIVER_CAPABILITIES=all`.
- The start script writes the NVIDIA EGL vendor file, so a project's browser flags need not know about it.

**Running.**

- Output streams back line by line. The remote command's exit code is `rig run`'s exit code; the last line names
  the verdict, so a command's own 2, 3, 75 or 124 is told apart from flotilla's.
- Every process of a run carries `FLOTILLA_RUN=<run id>` in its environment, and the run's process group is written
  on the machine; stopping a run ends its group and every process carrying its tag, including those that started a
  session of their own (a browser's helpers).
- **The ceiling is the rig's own**: `[rig] max_run_seconds`, default 1800 (the person's decision, 2026-10-07). At the
  ceiling the run is stopped on the machine; verdict `ceiling`, exit 124.
- **A broken connection is its own outcome**, not a red run. On ssh's 255 a fresh connection asks the machine how the
  run ended: finished - its real verdict; still running - it is stopped. `lost` (exit 75) is when the machine cannot
  be asked for two minutes; that machine is drained, not handed to the next run.
- `rig run` stopped by a signal (the Bash tool's timeout, a TaskStop) stops its run on the machine and records it.
- While a run goes, the machine is `busy`, and the run renews its lease and the heartbeat every 5 minutes.

**What comes back.**

- `--get PATH` packs those paths on the machine after the run and unpacks them at the same paths in the tree.
- Refused: the tree's root, a path git tracks at HEAD, a path under a local symlink, and any path component - in the
  path asked for and in every member of what comes back - where tools run code: `.git`, `.gitattributes`,
  `.gitmodules`, `.claude`, `.flotilla`, `node_modules`, `.venv`, `venv`, `.envrc`, `.direnv`, `.husky`, `.github`,
  `.gitlab-ci.yml`, `.vscode`, `.idea`, `.pre-commit-config.yaml`.
- Unpacking is safe:
  - absolute paths, paths leaving the tree, links and devices are refused;
  - the total is capped (default 500 MB);
  - the archive lands in a temporary directory and is moved in only when it arrived whole, so a cut transfer never
    mixes with old files.
- A red run still brings its artifacts back: red frames are what the person debugs.

**What is recorded:** revision, the command's signature ladder, seconds, verdict, the run's measurements, and its
cost (its share of the hour times the price). The orchestrator sees where a session's money went; it sees a run's
program name, never its command text (the channel closed for `why`, section 4).

**Disk.** A run checks free space on the machine first; run directories end with their run, revisions and caches
beyond a size are pruned oldest first.

## 8. Not in the first version, and why

- **A test tier run on the rig** (a receipt signed by a remote run, `where = "rig"`). It changes who signs a
  revision and needs its own design. Until then a heavy tier may run on the rig as an ordinary `rig run`, which signs
  nothing.
- **Other rented providers** (Hetzner's hourly servers among them): the interface takes them later.

## 9. Onboarding: when a machine is weak

`flotilla onboard machine` already measures cores, memory and disk, and `fleet size` counts the seats that fit. To
that it adds a comparison with **the fleet's base set**:

- an orchestrator, one working seat, one reviewer and one sender;
- one test-tier run beside them.

The tier's cost comes from the lane's measurements when there are any, else from onboarding's first run.

When the base set does not fit, onboarding says so with the figures ("the base set needs about 6 cores and 9 GB;
this machine has 4 and 8 - the fleet will run, its runs will wait for each other") and asks where heavy work can go:

1. **A machine of the person's own.** This is the `ssh` provider: an address and a key, no money, no `create`/
   `destroy`; the lease, queue, transfer and states are the same.
2. **Rent by the hour.** Before anything is turned on, onboarding names:
   - the price: an hour of a suitable offer on vast's public market, asked without a key if the market answers;
     otherwise the spec's range, marked with its date;
   - what that comes to at the person's pace: the lane shows how many hours a week heavy runs took;
   - **the consequences, in plain words:**
     - money runs while a machine lives, idle or not;
     - if the reaper fails, a machine runs until the watchdog's limit after our last heartbeat; if the watchdog
       fails too, until it is destroyed by hand; a container the watchdog only stopped keeps billing its disk until
       it is destroyed;
     - the project's committed code travels to a stranger's host;
     - prices and availability move;
     - the account, its 2FA and its balance are the person's, not the fleet's.

   Then the person sets the ceilings and puts the key in place. Onboarding prints the steps; it never asks for the
   key in the conversation.
3. **No, let it run slower.** Rig stays off; `fleet size` gets an honest ceiling on seats.

`rig`, `rig_provider` and the `rig_max_*` ceilings join `PERSON_KEYS`, so measuring the machine again keeps them.
Turning rig on is `rig enable`, a person's move (section 4).

## 10. Testing

Nothing in the suite spends money, opens a network connection, or touches the person's crontab: an autouse fixture
makes the real crontab and the real provider refuse in every test, the way the suite already keeps tests away from
this machine's processes and census.

- **A vast double** at the HTTP seam (the function that sends a request), keeping its instances in a dict. It
  reproduces what the probe and the CLI's source showed: an answer carrying an instance key in a kept field and in an
  error body; pages (`next_token`); a destroy that answers 200 while the instance stays listed; a 401 and a 404 with
  JSON error bodies; a body that does not parse; a timeout. For each, a test asserts our answer: no key reaches the
  journal, the output or an error text; every page is read; a "successful" destroy that leaves the instance listed
  ends in `stuck`; every non-2xx, timeout and unparsable body is a failure.
- **An ssh double** (stage 2): a fake `ssh` that runs the remote command in a local directory standing for the
  machine - the archive and its reuse per revision, the setup cache, `--put`/`--get` with hostile paths (`../`,
  absolute, symlinks) and a transfer cut in the middle, exit 255 -> `lost`.
- **A fake clock** for the lease, the 15 idle minutes, the session's end and the budget; **a fake process table**
  for a `busy` machine whose run died.
- **A crontab double** for install-once, no duplicates, the state-directory mark, the person's other lines kept,
  `%` refused, and self-removal.
- **The launcher** is run as a real subprocess against a fake `installed_plugins.json`: newest live version chosen,
  a missing directory skipped, and with no flotilla installed the last-resort destroy of labelled instances only.
- **Races**: two reapers at once (the second exits on the lock); a renew landing between the reaper's decision and
  its move (the move is skipped); a machine written to the journal after the listing (never an orphan).
- **Each safeguard is seen red** under an injection that removes it; the report names the assertion that fell:
  - the reaper not verifying absence;
  - the lease not counted;
  - the secret filter off (asserted on the provider's returned structure and on error text, where it shows);
  - the label check on destroy off;
  - the orphan rule keyed by instance id instead of the label's machine id;
  - the orphan rule touching other labels;
  - the budget not counted;
  - the reaper lock off;
  - the person guard on `rig enable` off.
- **One live check, by hand and by the person's yes**, before stage 2's release:
  - a real vast session of about 15 minutes, capped at 0.50 $;
  - the twosuns scene run through `rig run`;
  - then our side "dies" (the session killed, the lease not renewed);
  - the reaper destroys the machine and the provider's list confirms it is gone;
  - the watchdog's self-destroy measured on a second machine with the heartbeat stopped;
  - the account's credit before and after, against the local count.

## 11. Stages - each a release

1. **The mechanism without money:** journal and states with compare-and-set moves; the lease; the reaper with its
   lock, the label check, the orphan rule, the damaged-journal fallback; the launcher with its last resort and its
   crontab line; `last-reap` and its alarm in `flotilla rig` and `doctor`; the REST client for listing and destroy;
   the secret filter; the ceilings; `rig enable`/`disable` behind the person guard and the guard on `Edit`/`Write` of
   the person's files; off by default; `flotilla rig` display. All on doubles.
2. **vast and runs, as two releases** (the person's decision, 2026-10-07):
   - **2a (0.9.0) - a machine made, watched and given back:** the final review's minors; `rig open`/`close` by the
     relayed yes, under the reaper's lock; `rig up`; the vast adapter's offers, create and ssh key; the in-instance
     watchdog; rig lines in `fleet`, `watch` and the session-start hook, and requests relayed by the orchestrator
     (template v15); live check 1 with the person's yes, at most 0.50 $: a machine made, our side "killed", the reaper
     destroys it and the listing confirms; a second machine left to its watchdog; the 2FA question for create.
   - **2b (0.10.0) - runs:** `rig run` over ssh with `--put`/`--get`, several runs per machine with the floor of two
     and admission by the machine's live readings, each run measured; setup per run with shared download caches;
     `lost`, renewing its lease and the heartbeat; live check 2 - the twosuns scene through `rig run`, at most 0.50 $.
   - **2c (0.11.0) - packing:** admission by estimates from the runs measured in 2b; longest first, short ones fill,
     no starvation (the person's decision, 2026-10-07).

   Measured on 2026-10-07 with the person's scoped key, read-only: listing and offer search work without a 2FA code;
   datacenter offers with one GPU, reliability >= 0.98 and price <= 0.60 $/h started at 0.137 $/h.
3. **Onboarding:** the base-set comparison, the three questions, the budget estimate with its consequences, the `ssh`
   provider.
4. Later, each its own conversation: a test tier on the rig.

## 12. Review of 2026-10-06, and what it changed

(After the review, the person asked that rig not be bound to vast: the service became an adapter chosen by name,
with one contract and one suite for all - section 2.)

Two read-only reviews of the first draft and its stage-1 plan (a Claude Code plugin specialist and a DevOps
reviewer), with the critical claims re-checked in the `vastai` 1.6.0 source and the plugin cache:

- **The `vastai` CLI retries with the person's account key** after a 401 or an expired 2FA session, deleting the
  person's 2FA session file, and **exits 0 after an API error**: the provider moved to the REST API (section 5).
- **The cron line named a versioned plugin path**, which a cron-run reap kept rewriting to its own (soon deleted)
  version: the launcher in the state directory, with a last resort when no flotilla is installed (section 5).
- **Orphans were keyed by instance id** and would have destroyed a machine being created; **a destroy did not check
  the label**, so a forged journal line could name the person's own instance: orphans and destroys are both decided
  by the label (section 5).
- **No lock and decisions on stale reads**; **a renew by the orchestrator kept idle machines alive** and a dead run's
  machine stayed `busy` for ever; **turning rig off renewed nothing less**: one reaper at a time, compare-and-set
  moves, a lease renewed only by the work, a dead run's machine back to `ready`, off drains what is not running.
- **Nothing noticed a cron that never fires**: `last-reap` and its alarm.
- **The watchdog's premise was wrong** (vast already injects a per-instance key) and a stopped container bills
  storage without bound: the watchdog destroys (section 5, layer 2).
- **"Only the person turns rig on" did not hold** against a session editing `machine.toml`: `rig enable` behind the
  person guard and a guard on `Edit`/`Write`, with the shell's limit said plainly (section 4).
- **The budget was counted locally and partly**: price refreshed from the listing, drain at 90 % projected, the
  account's credit shown.
- **A damaged journal stopped all cleanup; a damaged machine key was silently replaced**: the fallback and the
  recovery in section 5.
- **The secret filter's injection would have stayed green**: the tests assert on the structure the provider returns.

**Second review, the same day, after the rewrite.** Both reviewers checked their own findings (all closed or
narrowed) and found what the rewrite itself broke: an empty label passed the label check; an instance its machine
no longer named was never an orphan; the person guard read `rig -- enable` as harmless; the launcher trusted any
marketplace's `flotilla`, ran it with no fallback, could act on one bad read, and never left the crontab; the
interpreter's version was not checked; `http.client` errors and non-ASCII digits escaped the adapter; redirects kept
the key; a failed pass looked healthy; the machine key could be adopted from forged labels; the budget test failed on
`0.9 * 0.10`; and one injection could not fail. Each is closed in sections 4 and 5 above and in the plan.

### Live check 1 (2026-10-07, the person's yes, at most 0.50 $)

From the 0.9.0 branch, the person's scoped vast key, `rig_max_hourly` 0.25. Two sessions, two Tesla P100
datacenter machines at 0.107 $/h. Credit 4 -> 3.95 $ as the person read it, rounded; the local floor said at least
0.04 $.

- **Create needs no 2FA** with the scoped key (`instance_read`, `instance_write`, `misc`).
- **vast's `cur_state` and `intended_status` say `running` from the first second**; only `actual_status` reports the
  box: null, then `loading` while the image pulls, then `running` about 2 minutes after create. `rig up` first
  called the machine ready after 3 seconds, reading `cur_state`; fixed before release, and the test fake now answers
  with this sequence.
- **The reaper path:** after `rig close`, one pass sent the destroy; the instance was absent from the listing at the
  first poll, 9 seconds later; the next pass recorded it gone and closed the session.
- **The watchdog path:** with `--watchdog-minutes 5` and the reaper's crontab line removed, the instance left the
  listing about 6 minutes after it ran (the limit plus the 60-second tick). The container's `CONTAINER_API_KEY`
  **may destroy**; no `exited` or `stopped` was seen. The reaper, back, marked the machine suspect, then lost and gone
  on the next pass.
- **Statuses seen:** `null`, `loading`, `running`, then absent. `offline` and `exited` were not seen, so the
  two-pass rule for an unlisted or `offline` machine stays as designed.
- **On the machine** (over ssh with the rig's key): the key is accepted; the start script ran (heartbeat, watchdog,
  `10_nvidia.json`); the Playwright image has curl, node and python3. The watchdog's process sees `CONTAINER_ID`,
  `CONTAINER_API_KEY` and `FLOTILLA_WATCHDOG_MINUTES`; **an ssh login shell does not**, which `rig run` must allow
  for.
- The reaper removed its own crontab line when nothing was rented; the crontab ended identical to its backup. A
  session that expired was closed by the cron reaper on time.

