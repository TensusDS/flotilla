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
- **A provider behind an interface**: `find(filter) -> offers`, `create(offer, image, onstart, label) -> id`,
  `instances() -> listed`, `destroy(id)`, `ssh_address(id)`. The first provider is vast.ai over its REST API. A second
  one, `ssh`, is the person's own machine: `create`/`destroy` do nothing, there is no money (section 9).
- **`flotilla rig enable` / `disable` / `open` / `close`** - the person's moves (section 4).
- **`flotilla rig run`** - the sessions' command; it renews its machine's lease while it runs.
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
- **The files that carry the person's word** - `machine.toml`, the provider key, the rig's state directory - are
  refused to Claude's `Edit`, `Write`, `MultiEdit` and `NotebookEdit` tools by a guard hook on those tools. A shell
  command that writes them is not caught: that is the limit named in the README's security model.
- **Opening a session** (`flotilla rig open --hours H --budget USD --why TEXT`, stage 2) and **closing one** are
  the person's moves under the same guard. A session that needs a machine and finds none open is refused with a line
  naming the orchestrator, and the refusal is written to the journal as a request. The orchestrator relays it to the
  person as a ready line to paste (`! flotilla rig open ...`). The orchestrator template gets this duty.
- The yes cannot exceed the machine's ceilings (section 5, layer 3); a request above them is refused with the
  ceiling named. Turning rig on is not consent to spend: money is spent only inside a session the person opened.

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
  the provider's listing. Absent: `gone`. Still listed: one more attempt is counted and the destroy repeated; at three
  the machine is `stuck`. A provider still listing an instance seconds after its destroy is therefore not an attempt.
- **A destroy is checked against the label.** The reaper destroys an instance only when the listing shows it with
  exactly the label the journal holds for that machine. Any other label - the person's own instance named by a damaged
  or forged journal line - is never destroyed; the machine is marked `stuck` with the reason `label mismatch`.
- **Orphans are decided by the label, not by the instance id.** An instance labelled
  `flotilla:<this machine's key>:<machine id>` is an orphan when the journal does not know that machine id, or knows it
  as `gone`. The listing is taken first and the journal read after it, so a machine written to the journal while the
  listing was taken is still known. **An instance without this machine's flotilla label is never touched**: the
  person's own vast work and another field machine's instances are safe.
- **One reaper at a time.** A pass takes a non-blocking lock; a second pass (cron and a manual `rig reap` together)
  says the reaper is running and exits. Every move the reaper makes is a compare-and-set: re-checked inside the
  journal's transaction against the state and lease it decided on, and skipped if a run renewed it meanwhile.
- **When the journal is damaged**, the reaper does not stop: it destroys every instance carrying this machine's
  label, and says so loudly. A damaged journal can no longer say what is wanted, and money running is the worse error.
- **The machine key** (in the labels) is written once, with a backup beside it. A damaged key file is recovered from
  the backup or from the labels in the journal, never silently replaced: a new key would make every earlier instance
  an orphan no pass could recognise.

**The reaper's launcher.** Cron must keep working when every session is dead and the plugin was updated or removed,
and the plugin cache path is versioned (Claude Code marks superseded versions `.orphaned_at` and deletes them later).
So cron does not run the plugin's path:

- The crontab line runs a launcher written into the state directory: `<state>/rig/reaper.py`, stdlib only, copied
  from the plugin when a machine is first requested and refreshed by every later rig command.
- The launcher reads Claude Code's `installed_plugins.json`, takes the newest installed flotilla whose directory
  exists and carries `flotilla/rig/`, and runs its `rig reap`.
- **With no flotilla installed at all**, the launcher is the last resort: it reads the key and destroys every
  instance carrying this machine's label through the provider's REST API, writes what it did to `reap.log`, and
  leaves the crontab line in place until no labelled instance is listed.
- The line names an interpreter that is checked to exist and is not a temporary environment (`machine.toml`'s
  measured `python3`, else `/usr/bin/python3`, else the first `python3` on `PATH`); a path containing `%` (which cron
  reads as a newline) is refused. The mark carries a hash of the state directory, so a second state directory's rig
  never removes this one's line.
- **Every pass writes `<state>/rig/last-reap`.** While anything lives and the last pass is older than 12 minutes,
  `flotilla rig`, `doctor`, `watch` and the session-start hook say so loudly: a crontab line that never fires (a
  machine with no cron daemon, as on WSL by default) is otherwise indistinguishable from a working one.

**Layer 2 - a watchdog inside the machine**, for the case where the whole field machine died together with the cron.

- vast injects into every container a key that can only start, stop or destroy that one instance
  (`CONTAINER_API_KEY`; vast's FAQ). flotilla puts no key of its own on the machine.
- The instance's start script runs a loop over a heartbeat file that our side touches over ssh on every run and
  every lease renewal. When the heartbeat is older than 45 minutes, the watchdog destroys the instance with that
  injected key; if the destroy fails, it stops the container.
- Stage 2's live check measures both paths: that a self-destroy ends billing, and what a stopped container costs.

**Layer 3 - ceilings.**

- **The session's budget** comes from the yes. Spending is computed locally - each machine's hourly price times the
  minutes begun since it was created, plus 2 minutes of billing after a destroy - and the price is refreshed from the
  listing's `dph_total` on every pass. The local figure is a floor, not the bill: traffic and storage are billed
  apart. So a session drains at **90 % of its budget, projected to the next pass** (spending now plus one pass at the
  current rate), and `flotilla rig` shows the account's credit at the session's open and now, when the key may read
  it, so the person can see the real bill.
- **The machine's ceilings** live in `machine.toml` and only the person changes them (section 4):
  - at most 1 machine at a time (`rig_max_machines`);
  - at most 0.60 $/hour (`rig_max_hourly`);
  - a session at most 8 hours (`rig_max_hours`).
- **Offers are filtered.** Datacenter hosts only, verified, reliability >= 0.98, and the project's GPU and disk
  minimum.

**Calling the provider.** flotilla talks to vast's REST API directly with the standard library (`urllib`), not
through the `vastai` CLI. Read in the CLI's source (1.6.0): on a 401 "Invalid user key" or an expired 2FA session it
deletes the person's 2FA session file and retries with the person's own account key from `~/.config/vastai/`, which
would silently defeat a scoped key; it exits 0 after an API error; and without `-y` it prompts. Over REST:

- the key is sent only as `Authorization: Bearer`, never in a URL or an argument list;
- the listing is `GET /api/v1/instances/`, followed through every page (`next_token`); a destroy is
  `DELETE /api/v0/instances/<id>/`;
- every request has a 30 s timeout; an HTTP status other than 2xx, a timeout, or a body that does not parse is a
  failure, never a success, and its text is scrubbed before it is shown;
- the base URL is fixed to `https://console.vast.ai`, so no environment variable can send the key elsewhere.

## 6. Keys and secrets

- **The provider's key** lives only in `~/.config/flotilla/vast_api_key`: a regular file (not a symlink), owned by
  the user, mode 600. Anything else is a refusal to run. It is never in a project, a journal, a run's environment, an
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

**Gate.**

- Rig on.
- A session open with budget left.
- A tree with a committed HEAD. Uncommitted changes to tracked files are refused, with a hint: commit, or pass the
  file with `--put`. A run is tied to a revision so its result can be repeated.

**Order.**

- One run per machine at a time. Further runs wait in a queue in the journal, in order of arrival, and
  `flotilla rig` says who waits behind whom.
- With no machine ready and the machine ceiling not reached, the run requests one and waits through `provisioning`.
- A waiting run has a `--wait` like the lane's (default 900 s).

**What goes there.**

- `git archive HEAD` is piped over ssh into `/work/<project>/<sha>`; a revision already there is not sent again.
- `--put PATH` adds files git does not track: a data snapshot, a built asset. Refused:
  - `.env*`, `*.pem`, `id_*`, `credentials*`;
  - anything outside the tree;
  - symlinks, which are never dereferenced.
- The profile's `setup_command` runs once per hash of the lock files on that machine. Its result is kept in
  `/work/<project>/setup/<hash>` and linked into the revision's directory. In the probe `npm ci` took longer than the
  scene.

**Environment.**

- No variable crosses from the field machine except those named with `--env`. Names containing `KEY`, `TOKEN`,
  `SECRET` or `PASSWORD` are refused.
- The image and GPU come from the project's profile (`[rig] image`, `gpu`, `disk_gb`). The default image is
  `mcr.microsoft.com/playwright:<pinned tag>-jammy` with `NVIDIA_DRIVER_CAPABILITIES=all`.
- The start script writes the NVIDIA EGL vendor file, so a project's browser flags need not know about it.

**Running.**

- Output streams back line by line. The remote command's exit code is `rig run`'s exit code.
- **A broken connection is its own outcome**, not a red run. `ssh` exits 255; `rig run` exits 75 with the verdict
  `lost`. The machine is marked suspect and the reaper checks it.
- The ceiling on a run's time is the lane's ceiling for the project.
- While a run goes, the machine is `busy` and the run itself renews its lease and the heartbeat every 5 minutes.

**What comes back.**

- `--get PATH` packs those paths on the machine after the run and unpacks them at the same paths in the tree.
- Unpacking is safe:
  - absolute paths, paths leaving the tree and symlinks are refused;
  - the total is capped (default 500 MB);
  - the archive lands in a temporary directory and is moved in only when it arrived whole, so a cut transfer never
    mixes with old files.
- A red run still brings its artifacts back: red frames are what the person debugs.

**What is recorded:** revision, command, seconds, verdict, and the run's cost (its share of the hour times the price).
The orchestrator sees where a session's money went.

## 8. Not in the first version, and why

- **A test tier run on the rig** (a receipt signed by a remote run, `where = "rig"`). It changes who signs a
  revision and needs its own design. Until then a heavy tier may run on the rig as an ordinary `rig run`, which signs
  nothing.
- **Several runs on one machine at once.** A GPU shares badly; it needs a measurement first.
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
     - if all three safeguards fail, a machine can run to its session's end, never past the 8-hour ceiling;
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
2. **vast and runs:**
   - `rig open`/`close` by the relayed yes;
   - the vast provider's offers and create, and the ssh transfer;
   - `rig run` with `--put`/`--get`, renewing its lease;
   - the in-instance watchdog;
   - lines in `fleet` and `watch`, and the stale-reaper alarm in `watch` and the session-start hook;
   - the orchestrator template's duty;
   - the 2FA measurement;
   - the live check.
3. **Onboarding:** the base-set comparison, the three questions, the budget estimate with its consequences, the `ssh`
   provider.
4. Later, each its own conversation: a test tier on the rig; several runs on one machine.

## 12. Review of 2026-10-06, and what it changed

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
