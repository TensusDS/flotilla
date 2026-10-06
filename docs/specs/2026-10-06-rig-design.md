# Rig: rented machines for heavy runs - design

Date: 2026-10-06. Status: draft for the person's review. Agreed with the person section by section in the session of
2026-10-06; this document collects those sections. Probe behind it (2026-10-06, vast.ai): an RTX A5000 datacenter
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
rig is off on this machine and how to turn it on (section 9).

## 2. Parts

- **The rig journal** (`<state>/rig/journal.jsonl`, written under a lock like the lane's): sessions, machines, runs
  and their transitions. Every state the orchestrator sees is folded from it.
- **A provider behind an interface**: `find(filter) -> offers`, `create(offer, image, onstart, label) -> id`,
  `status(ids) -> states`, `listed(label) -> ids`, `destroy(id)`, `ssh_address(id)`. The first provider is vast.ai
  through the `vastai` CLI. A second one, `ssh`, is the person's own machine: `create`/`destroy` do nothing, there is
  no money (section 9).
- **`flotilla rig open` / `close`** - the person's commands.
- **`flotilla rig run`** - the sessions' command.
- **`flotilla rig renew`, `reap`** - the lease and the reaper.
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

## 4. The person's yes

`flotilla rig open --hours H --budget USD --why TEXT` is refused to any fleet session, by the same check as
`onboard` and `approve` (`caller.person_refusal`). A session that needs a machine and finds none open is refused with
a line naming the orchestrator. The refusal is also written to the journal as a request. The orchestrator relays it
to the person as a ready line to paste. The orchestrator template (posts) gets this duty.

The yes cannot exceed the machine's ceilings (section 5, layer 3); a request above them is refused with the ceiling named.
`flotilla rig close` ends a session at once and drains its machines.

Turning rig on (section 9) is not consent to spend; money is spent only inside a session the person opened.

## 5. Safeguards: a machine never outlives the side that took it

Three layers; each fires if the one before it did not.

**Layer 1 - a renewed lease and a reaper.**

- Every machine carries a lease: now + 30 minutes.
- What renews it:
  - every `rig run`, at its start and at its end;
  - `flotilla rig renew`, which the orchestrator calls on each `watch` cycle while the session is wanted.
- Opening the first machine installs a user crontab line marked with a comment (`# flotilla rig reaper`), every 5
  minutes: `flotilla rig reap`. It is installed once and never duplicated. It removes itself when no machine and no
  open session remain.
- The reaper drains a machine when any of these holds:
  - its lease expired;
  - it has been idle 15 minutes;
  - its session ended;
  - its session's budget is spent.
- **Draining means verifying.** The reaper calls `destroy`, then asks the provider for its list of instances. Only a
  machine absent from the list becomes `gone`. After three failed attempts it becomes `stuck`.
- **Orphans.** Every instance flotilla creates carries the label `flotilla:<machine-key>:<journal id>`. An instance
  with a flotilla label that the journal does not know, or knows as `gone`, is an orphan: a crash between `create` and
  the journal write. The reaper drains it. **An instance without the flotilla label is never touched**, so the
  person's own vast work, made by hand, is safe from the reaper.

**Layer 2 - a watchdog inside the machine**, for the case where the whole field machine died together with the cron.

- The instance's start script runs a loop over a heartbeat file. Our side touches the file over ssh on every run and
  every renewal.
- When the heartbeat is older than 45 minutes, the watchdog stops the container. The provider then stops charging
  for the GPU; storage costs cents an hour.
- The watchdog cannot destroy the instance: no key is ever put on the machine. The vast per-instance key (returned by
  `create`) is deliberately not used for self-destruction: a key on a stranger's host is surface, and the remainder is
  cents. The reaper destroys the stopped instance once the field machine is back.

**Layer 3 - ceilings.**

- **The session's budget** comes from the yes. Spending is computed locally: the hourly price known at `create`,
  times the time since `create` (rounded up to the minute), plus a margin for billing settling after destroy (2
  minutes per machine). At 80 % `watch` warns. At 100 % new runs are refused and the machines drained.
- **The machine's ceilings** live in `machine.toml` and only the person may change them:
  - at most 1 machine at a time (`rig_max_machines`);
  - at most 0.60 $/hour (`rig_max_hourly`);
  - a session at most 8 hours (`rig_max_hours`).
- **Offers are filtered.** Datacenter hosts only, verified, reliability >= 0.98, and the project's GPU and disk
  minimum.

**Calling the provider.**

- Every `vastai` call runs with `-y` where it asks, `--raw` JSON output, and a 60 s timeout.
- A timeout, a non-zero exit or unparsable output is a failure, never a success. The probe showed `destroy` without
  `-y` hanging on a prompt; such a hang would have read as "destroyed".

## 6. Keys and secrets

- **The provider's key** lives only in `~/.config/flotilla/vast_api_key`, mode 600. Wider permissions are a refusal
  to run. It is never in a project, a journal, a run's environment, or on the machine. The plugin ships no key and no
  key field with a value. `machine.toml` carries `rig = "off"` and `rig_provider = ""` until the person changes them.
- **A scoped key is asked for** in the onboarding text. The permissions it needs are `instance_read`,
  `instance_write`, `misc` (searching offers). The rig's ssh key is registered once by the person with the account
  key, so the scoped key needs no `user_write`.
- **2FA (open).** In the probe, the account key needed a TOTP session (`vastai tfa login`). vast's documentation does
  not say whether a scoped key needs it too. Stage 2 measures this with the person's scoped key, and this spec is
  amended:
  - if it does, `rig open` takes `--totp CODE`, and the code is part of the yes;
  - if it does not, nothing changes.
- **Provider output is filtered** before anything is printed or written. The journal keeps only id, GPU, price,
  state, address and label. Every field whose name contains `key`, `token`, `secret` or `password` is dropped, and the
  value of `instance_api_key` is masked wherever it appears. In the probe that key reached the log.
- **The rig's ssh key** is its own (`~/.ssh/flotilla_rig_ed25519`), made when rig is turned on. A machine's host key
  is recorded on first contact (`StrictHostKeyChecking=accept-new` into a per-machine known_hosts file under the
  state dir). It is checked on every later connection; a changed host key ends the connection and marks the machine
  `lost`.

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
- While a run goes, the machine is `busy` and its lease and heartbeat are renewed every 5 minutes.

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

`rig`, `rig_provider` and the `rig_max_*` ceilings join `PERSON_KEYS`, so rewriting `machine.toml` keeps them and no
fleet session can turn rig on.

## 10. Testing

Nothing in the suite spends money or touches the person's crontab.

- **A vast double:** a fake `vastai` on the tests' `PATH`, keeping its instances in a file. It reproduces what the
  probe saw:
  - `create` answers with JSON carrying an instance key;
  - `destroy` without `-y` hangs;
  - an instance comes up only after a delay;
  - `destroy` reports success while the instance stays listed.

  For each, a test asserts our answer: the key never reaches the journal or the output; the hang is cut by the
  timeout and counts as a failure; a "successful" destroy that leaves the instance listed ends in `stuck`.
- **An ssh double:** a fake `ssh` that runs the remote command in a local directory standing for the machine. It
  covers:
  - the archive and its reuse per revision;
  - the setup cache;
  - `--put`/`--get` with hostile paths (`../`, absolute, symlinks) and a transfer cut in the middle;
  - exit 255 -> `lost`.
- **A fake clock** for the lease, the 15 idle minutes, the session's end and the budget.
- **A crontab double** for install-once, no duplicates and self-removal.
- **Each safeguard is seen red** under an injection that removes it:
  - the reaper not verifying absence;
  - the lease not renewed;
  - the secret filter off;
  - the `--put .env` refusal off;
  - the budget not counted;
  - the orphan rule touching unlabelled instances.

  Each injection must fail its own test, and the report names the assertion that fell.
- **One live check, by hand and by the person's yes**, before stage 2's release:
  - a real vast session of about 15 minutes, capped at 0.50 $;
  - the twosuns scene run through `rig run`;
  - then our side "dies" (the session killed, the lease not renewed);
  - the reaper destroys the machine and the provider's list confirms it is gone.

## 11. Stages - each a release

1. **The mechanism without money:** journal and states, lease, reaper and its cron, orphan rule, secret filter,
   ceilings, off by default, `flotilla rig` display. All on doubles.
2. **vast and runs:**
   - `rig open`/`close` by the relayed yes;
   - the vast provider and the ssh transfer;
   - `rig run` with `--put`/`--get`;
   - the in-instance watchdog;
   - lines in `fleet` and `watch`;
   - the orchestrator template's duty;
   - the 2FA measurement;
   - the live check.
3. **Onboarding:** the base-set comparison, the three questions, the budget estimate with its consequences, the `ssh`
   provider.
4. Later, each its own conversation: a test tier on the rig; several runs on one machine.
