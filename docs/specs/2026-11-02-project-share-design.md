# Projects sharing one machine: a fair lane and a shared rig session - design

Status: proposed for 0.25.0. Asked by the person on 2026-11-01 and 2026-11-02, from two field fleets that share one
machine (twosuns and void-lanes-online), and decided with them on 2026-11-02: the lane's `fair` mode first, `slots`
later; void-lanes-online moves to 0.25 when it ships, twosuns stays on its install for now.

## 1. What it is for

One machine, two projects, one lane. At `lane_capacity = 1` the lane is strict first come, first served across
every project, so a project with a deep backlog holds every other project's work behind it. Measured on 2026-10-31
(16:00-23:40 UTC): void-lanes-online made 26 bookings with a median run of 7.1 s and waited a median 42.8 min for
each; its work used 3 minutes of lane time in 7.5 hours, and a handover green at commit time waited about 45 minutes
for its receipt. twosuns made 117 bookings in the same hours.

The rig has the same shape one level up. A rig session is opened by the person for one project's request, and today
any project's run takes it: on 2026-11-01 (23:19-23:30) the person agreed to share session s79 with
void-lanes-online, and the two orchestrators agreed slots between themselves by letters, with one ceiling and one
budget and no record of which project spent what.

The person's words: one project's fleet should not wait on another's at all, even when both have only long runs.

## 2. Goals, non-goals, anti-goals

Goals:
- **Lane, `fair` mode**: when a slot frees, it goes to the oldest waiter of the project served least recently, not
  to the oldest waiter overall; within one project, order stays first come, first served; with one project waiting,
  this is plain first come, first served.
- **Rig**: a session records the projects it serves; a run from another project is refused with the reason and the
  line that would add it; `rig status` shows what each project spent.
- Both are the person's choice and both are visible: the mode and each admission's path are printed and recorded.

Non-goals (other items of `TODO.md`, each its own release):
- short runs overtaking long ones (lane mode 1: budget admission, the head's shadow);
- the `slots` variant (a guaranteed share of the capacity per project, an idle slot lent);
- seniority kept across an expired `--wait`; `lane bump`; expected start times;
- the rig queue with hand-to-next, run classes, the owner's exclusive runs first, offload hints;
- the journal's compaction and a waiter woken by a journal change.

Anti-goals - it fails, even if it works, if:
- a machine that did not opt in behaves differently in any way;
- a seat on an older flotilla can stall the lane: an empty slot while someone waits is the one outcome worse than
  today's;
- the lane admits more runs than `lane_capacity` allows, or the rig spends past a session's budget;
- a run is refused on the rig without being told what would let it run, or a project is let onto a session the
  person did not share.

## 3. The lane's `fair` mode

**The setting.** `machine.toml` `lane_share = "fifo" | "fair"`, default `"fifo"`; anything else reads as `"fifo"`
and `flotilla lane` says the value was not understood. It is the person's key, like `lane_capacity`, and like it is
read on every poll (0.24.1), so a change reaches waiters already queued.

**Who was served.** A project was served at the moment one of its bookings went from waiting to held. The journal
already has it: a booking that is held, or released after being held, carries the time it was granted (`since`);
a booking written already released by a nested run (`inside`) was never granted and does not count. A booking with
no project (written by a flotilla before the project field) belongs to the project `""`. Only the last 24 hours are
read; a project not served in them was served "never" and goes first.

**The choice.** When slots are free, the waiter that gets one is the oldest live waiter of the project that holds
the fewest live slots now, and among those the one whose last service is the earliest (never first); a tie goes to
the oldest waiter. *(Builder's call, 2026-11-02: a project holding a slot is being served now. By grant time alone,
at a capacity above 1 a project with a 40-minute run would win the slot a short run of another project just freed,
and hold both.)* The capacity count is unchanged: live holders
plus computing foreign runs against `lane_capacity`, under the journal's lock.

**Mixed versions.** Every booking records the rule it waits under (`rule`). 0.25 writes `rule = 2`. A seat on an
older flotilla waits first come, first served and never yields, and it does not know that a newer seat may yield to
it - so a newer seat that yielded to an older one would wait for a waiter that waits for it. The rule that keeps the
slot from standing empty:
- a waiter under rule 2 is granted when slots are free and either it is the fair choice, or the fair choice is a
  waiter under an older rule and it would be granted first come, first served (no live waiter ahead of it);
- a waiter under an older rule behaves as it does today.

So older seats keep their order among themselves and take a slot whenever they are first in line, and newer seats
take their project's turn without ever leaving a slot to a waiter that cannot take it. On a machine where every
waiting seat is older, nothing changes. This is why void-lanes-online moves to 0.25: the protection reaches only the
seats that run it.

**What is recorded and shown.** Each `held` event records the path it took: `fifo`, or `fair` with the project that
was served least recently and when. `flotilla lane` prints the mode, then for each project with waiters its waiting
count and last service, and each waiter's reason in fair terms ("project X was served 3 min ago; `twosuns` goes
first: served 40 min ago").

**Version gating against the old reader.** A flotilla before 0.25 reading a journal with `rule = 2` bookings and
fair `held` events folds them as today: the fields it does not know are kept, nothing is renamed.

## 4. The rig session's projects

**What a session serves.** `rig open --for <request>` records the request's project as the session's first project;
`rig open` without a request records the project of the repository it is run in, and outside a repository the
session serves every project until the first `rig share`, whose output names who it serves from then on. *(Builder's
call, 2026-11-02.)*
`rig share <project>` adds a project to the open session, and `rig unshare <project>` takes one off; both are the
person's moves (refused from a session's tool call, like `rig open` and `rig allow-image`), and each is recorded.
A project is named by its key (the `project` the journal already carries) or by its repository root path; `rig
status` lists the keys. A session opened before 0.25 has no list and serves every project, as today, until the
person shares or unshares; the first `rig share` on such a session fixes the list to the project that opened it,
if known, plus the one named, and says plainly when the opener's project is not known and so is not on the list.

**A run from another project.** The list gates `rig run`, `rig up` (it spends the session's budget) and `rig down`
(it stops the session's machine) alike *(builder's call, 2026-11-02)*. Each is refused before anything is spent:
"session s79 serves twosuns; the person adds this project with `! flotilla rig share <key>`", and a request is
recorded for the orchestrator, as a run with no session records one today. A run already going when a project is
unshared finishes.

**What each project spent.** `rig status` shows the session's total spend as today, and under it each project's
share and the machine's idle time - not attributed to a project. A run records the machine's full price for its
seconds (`Run.cost`), and runs share a machine, so the shares are computed when shown: each minute of a machine is
split evenly among the runs that ran on it in that minute, and a minute with none is idle; the shares and the idle
time add up to the session's spend. `Run.cost` keeps its meaning, since older seats keep writing it. *(Builder's call,
2026-11-02.)*

**Mixed versions.** A seat on an older flotilla does not read the list and runs as today. The refusal holds for the
seats that run 0.25; the person is told in `rig share`'s output when an older seat of another project used the
session (a run whose project is not on the list).

## 5. What is left to the builder

- the names of the new fields and events, the exact wording of the lines, and where the per-project lines sit in
  `flotilla lane` and `rig status`;
- how the 24-hour window is read (the cached bookings carry everything needed);
- whether `rig share` takes a path or only a key, if a path makes the refusal's line ambiguous.

## 6. Testing

- **Fair, the field case**: two projects, ten waiters of one and one of the other, capacity 1: the single waiter is
  granted at the next release.
- **Fair, within a project**: three waiters of one project are granted in their order.
- **Fair, one project**: plain first come, first served, granted in the same order as `fifo`.
- **Mixed versions, the stall**: a rule-2 waiter ahead whose project was served recently, and a rule-1 waiter of
  another project behind it: the rule-2 waiter takes the slot; no slot stands empty while either waits.
- **Mixed versions, older first**: a rule-1 waiter first in line takes the slot as today.
- **Capacity**: under `fair`, no more live holders than `lane_capacity` less computing foreign runs, with several
  waiters polling at once.
- **A value not understood** reads as `fifo` and is said.
- **Rig**: a run from a project not on the list is refused with the line; `rig share` adds it and the next run goes;
  `rig share` and `unshare` refused from a session's tool call; an unshared project's `rig up` and `rig down` refused;
  a pre-0.25 session serves every project; two overlapping runs of two projects split their machine's minutes, and
  the shares plus idle add up to the session's spend.
- **Fair at capacity 2**: project A holds a long run; project B's short run ends: B's next waiter, not A's, gets the
  freed slot.
