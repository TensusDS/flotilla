"""`flotilla work …`, `flotilla tree cut` and `flotilla receipt …`.

The rules a move is checked against (the profile and the posts) are read from trunk, not from the caller's tree:
an uncommitted edit in a branch must not drop a handover tier or widen a post for the branch that made it. The
event records the trunk revision the rules came from.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.core.text import strip_ansi, visible
from flotilla.ledger import (core, delivery, events, findings, gitq, handover, judging, letters, outside, reading,
                             receipts, report, steering, views)
from flotilla.ledger import tree as tree_mod
from flotilla.ledger.actor import resolve_actor
from flotilla.lane.acquire import LaneRefused
from flotilla.ledger.errors import MoveRefused, NotYet
from flotilla.ledger.model import LedgerVersionError, Row
from flotilla.posts import PostError, load_posts


@dataclass(frozen=True)
class Rules:
    profile: dict
    posts: dict
    events: dict
    label: str
    tree: str = ""   # the git tree id of `.flotilla` they were read from: what a move was checked against, by content


def _show_bytes(root: Path, sha: str, path: str) -> bytes:
    done = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{path}"], capture_output=True, check=False)
    if done.returncode != 0:
        raise config.ConfigError(f"git could not read {path} at {sha[:7]}")
    return done.stdout


def tree_events(root: Path) -> dict:
    """The event scripts in this working tree (for `events check --tree`), with their executable bit."""
    folder = Path(root) / events.EVENTS_DIR
    if not folder.is_dir():
        return {}
    return {path.name: (path.read_bytes(), os.access(path, os.X_OK)) for path in sorted(folder.iterdir())
            if path.is_file()}


def _project(root: Path) -> config.Project:
    found = config.find_project(root)
    if found is None:
        raise config.ConfigError(f"not onboarded: no .flotilla/project.toml at or above {Path(root).resolve()}; "
                                 "run /flotilla:onboard")
    return config.load_project(found)


def _show_file(root: Path, sha: str, path: str) -> str:
    done = subprocess.run(["git", "-C", str(root), "show", f"{sha}:{path}"], capture_output=True, text=True,
                          check=False)
    if done.returncode != 0:
        raise config.ConfigError(f"git could not read {path} at {sha[:7]}: {done.stderr.strip()}")
    return done.stdout


def _trunk_named_on(root: Path, ref: str) -> str:
    """The trunk the profile committed on `ref` names, or "" when it carries none."""
    import tomllib
    shown = subprocess.run(["git", "-C", str(root), "show", f"{ref}:.flotilla/project.toml"], capture_output=True,
                           text=True, check=False)
    try:
        return (tomllib.loads(shown.stdout).get("trunk") or {}).get("branch", "main") if shown.returncode == 0 else ""
    except tomllib.TOMLDecodeError:
        return ""


def trunk_rules(root: Path, *, at: str = "") -> Rules:
    """The profile and posts as trunk carries them, and the label `<ref>@<sha>` they were read at. `at` is a revision
    origin named as its trunk: rules that grant are read there, never at the local ref, which any session can repoint
    (scan of 0.6.10, F1)."""
    local = _project(root)
    trunk = (local.data.get("trunk") or {}).get("branch", "main")
    # the tree's own profile names trunk, and a seat edits its tree: origin's default branch has the last word, so a
    # session cannot point the rules - its posts, their modes and prompts - at a branch of its own (F6)
    default = subprocess.run(["git", "-C", str(local.root), "symbolic-ref", "-q", "--short",
                              "refs/remotes/origin/HEAD"], capture_output=True, text=True, check=False).stdout.strip()
    if default.startswith("origin/") and default[len("origin/"):] != trunk and _trunk_named_on(local.root,
                                                                                           default) != trunk:
        raise config.ConfigError(f"this tree names trunk `{trunk}`, but origin's default branch is "
                                 f"`{default[len('origin/'):]}` and its profile does not name `{trunk}`; the rules are "
                                 "read from the real trunk. If the default branch moved, `git remote set-head origin "
                                 "-a`; if trunk is another branch, name it in the profile on the default branch too")
    ref = f"origin/{trunk}" if at else gitq.trunk_ref(local.root, trunk)
    sha = gitq.resolve(local.root, f"{at or ref}^{{commit}}")
    if sha is None:
        raise config.ConfigError(f"git could not resolve trunk `{ref}`; the ledger reads its rules from trunk")
    listing = subprocess.run(["git", "-C", str(local.root), "ls-tree", "-r", sha, "--", ".flotilla/"],
                             capture_output=True, text=True, check=False)
    entries = {}
    for line in listing.stdout.splitlines():
        meta, _, path = line.partition("\t")
        entries[path] = meta.split()[0]
    if ".flotilla/project.toml" not in entries:
        raise config.ConfigError(f"`{ref}` carries no .flotilla/project.toml; the ledger reads its rules from "
                                 "trunk, so commit the onboarding and bring it to trunk first")
    scripts = {}
    prefix = events.EVENTS_DIR + "/"
    for path, mode in entries.items():
        if path.startswith(prefix) and "/" not in path[len(prefix):]:
            if mode == "120000":
                raise config.ConfigError(f"{path} is a symlink on `{ref}`; event scripts are real files")
            scripts[path[len(prefix):]] = (_show_bytes(local.root, sha, path), mode == "100755")
    with tempfile.TemporaryDirectory(prefix="flotilla-rules-") as tmp:
        for path, mode in entries.items():
            if path != ".flotilla/project.toml" and not (path.startswith(".flotilla/posts/") and path.endswith(".md")):
                continue
            if mode == "120000":
                raise config.ConfigError(f"{path} is a symlink on `{ref}`; rules are read only from real files")
            target = Path(tmp) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_show_file(local.root, sha, path), encoding="utf-8")
        try:
            profile = config.load_project(Path(tmp)).data
            posts = load_posts(Path(tmp), project=fleet_name(local.root, profile))
        except (config.ConfigError, PostError) as err:
            raise type(err)(str(err).replace(f"{tmp}/", f"{ref}:")) from err
    if (profile.get("trunk") or {}).get("branch", "main") != trunk:
        raise config.ConfigError(f"this tree names trunk `{trunk}`, but `{ref}` names another; the rules on trunk "
                                 "are the ones that count")
    tree = subprocess.run(["git", "-C", str(local.root), "rev-parse", "--verify", "--quiet", f"{sha}:.flotilla"],
                          capture_output=True, text=True, check=False).stdout.strip()
    return Rules(profile, posts, scripts, f"{ref}@{sha[:12]}", tree)


def fleet_name(root: Path, profile: dict) -> str:
    """The word this project's seat names start with, or "" when the profile names no fleet (W11): unique on the
    machine, so a project that wants a word another project holds gets `-2`."""
    wanted = (profile.get("fleet") or {}).get("name")
    if not isinstance(wanted, str) or not wanted.strip():
        return ""
    from flotilla.fleet import project_name
    store = LocalLogStore(paths.state_dir() / "fleet")
    return project_name.resolve(store, repo.identify(Path(root)).key, wanted)[0]


def fleet_note(root: Path, profile: dict) -> str:
    """What the person is told when the fleet name the profile wants is another project's on this machine."""
    wanted = (profile.get("fleet") or {}).get("name")
    if not isinstance(wanted, str) or not wanted.strip():
        return ""
    from flotilla.fleet import project_name
    store = LocalLogStore(paths.state_dir() / "fleet")
    return project_name.resolve(store, repo.identify(Path(root)).key, wanted)[1]


def open_ledger(root: Path, *, skip_events: dict | None = None, rules: Rules | None = None) -> core.Ledger:
    rules = rules or trunk_rules(Path(root))
    ident = repo.identify(Path(root))
    state = paths.state_dir()
    return core.Ledger(store=LocalLogStore(state / "ledger"), root=ident.root, repo_key=ident.key,
                       profile=rules.profile, posts=rules.posts, state_dir=state, rules=rules.label,
                       rules_tree=rules.tree,
                       events=rules.events, skip_events=skip_events)


def summary(row: Row) -> str:
    reading = " (reading)" if row.taken and row.state == "handed" else ""   # `taken` outlives the verdict (F13)
    reader = f", reader {row.reader}{reading}" if row.reader else ""
    return f"{row.id} {row.branch}: {row.state} (owner {row.owner}{reader})"


def _base(ledger: core.Ledger, row: Row) -> str:
    """A row filed before its branch exists has no base yet; "unknown" would read as a git failure (F22)."""
    if row.base:
        return f"base {row.base}"
    if gitq.branch_tip(ledger.root, row.branch, run=ledger.run) is None:
        return "base: no branch yet"
    return "base unknown"


def _show(ledger: core.Ledger, branch: str) -> int:
    matches = [row for row in ledger.rows().values() if row.branch == branch]
    if not matches:
        print(f"no ledger row for `{branch}`")
        return 2
    row = matches[-1]
    print(summary(row))
    print(f"  {_base(ledger, row)} | tip {row.tip or '-'} | verdict {row.verdict or '-'} | ref {row.ref or '-'}")
    if row.waiting_on:
        print(f"  waiting on {row.waiting_on}: {row.note}")
    if row.why:
        print(f"  last return: {row.why}")
    if row.merge or row.gate:
        print(f"  trunk commit {row.merge[:7] or '-'} | gate {row.gate or '-'} | PR {row.pr or '-'}")
    if row.held_until:
        print(f"  held by {row.held_by} until {row.held_until}: {row.held_why}")
    if row.urgent_at:
        print(f"  urgent since {row.urgent_at}: {row.urgent_why or 'no reason given'}")
    if row.broken:
        print(f"  broke at {row.broken}")
    print("history:")
    for entry in row.history:
        called = entry.get("caller") or ""
        also = f" (called by {called})" if called and called != entry["by"] else ""
        print(f"  {entry['move']:<8} {entry['state']:<9} {entry['by']}{also}  {entry['at']}")
    return 0


#: Printed after every refusal, so no refusal leaves a session without a way forward (field test F17).
STUCK = ("next: if no move named above is yours to make, record whom you wait on (`flotilla work wait <branch> "
         "--on \"<whom>\" --why \"<why>\"`) and tell the orchestrator. A refusal that names no way forward is a "
         "flotilla defect: send it to the orchestrator verbatim, and never work around flotilla with git plumbing.")
#: Printed after an error that is not a move refused: no ledger move helps, so none is suggested.
CANNOT_RUN = ("next: flotilla cannot run here as things stand; send this text to the orchestrator verbatim, and never "
              "work around flotilla with git plumbing.")

def _adopt(ledger, actor, args):
    if bool(args.branch) == bool(getattr(args, "from_", None)):
        raise MoveRefused("name one branch, or --from <gone session> for all of its open work - not both")
    if args.branch:
        return steering.adopt(ledger, actor, args.branch, to=args.to)
    moved, refused = steering.adopt_from(ledger, actor, args.from_, to=args.to)
    lines = []
    if moved:
        lines.append("adopted: " + ", ".join(f"`{row.branch}` ({row.state})" for row in moved) + f" -> {args.to}")
    lines += [f"NOT adopted: `{branch}`: {why}" for branch, why in refused]
    if not moved:
        raise MoveRefused("; ".join(lines))
    return moved[-1], "\n".join(lines)


MOVES = {
    "claim": lambda l, a, x: core.claim(l, a, x.branch, tree=x.tree, ref=x.ref, requires=x.requires, after=x.after,
                                        also=x.also),
    "reserve": lambda l, a, x: core.reserve(l, a, x.branch, tree=x.tree),
    "release": lambda l, a, x: core.release(l, a, x.branch, why=x.why, settled_by=x.settled_by),
    "hand": lambda l, a, x: handover.hand(l, a, x.branch, tip=x.tip),
    "moved": lambda l, a, x: handover.moved(l, a, x.branch, tip=x.tip, agreed_by=x.agreed_by),
    "wait": lambda l, a, x: handover.wait(l, a, x.branch, on=x.on, why=x.why, clear=x.clear),
    "take": lambda l, a, x: reading.take(l, a, x.branch),
    "recuse": lambda l, a, x: reading.recuse(l, a, x.branch),
    "fix": lambda l, a, x: reading.fix(l, a, x.branch, why=x.why),
    "assign": lambda l, a, x: reading.assign(l, a, x.branch, reader=x.reader)[0],   # letters prints its letter
    "accept": lambda l, a, x: reading.accept(l, a, x.branch, reviewed=x.reviewed),
    "queue": lambda l, a, x: delivery.queue(l, a, x.branch, pr=x.pr),
    "land": lambda l, a, x: delivery.land(l, a, x.branch, merge=x.merge),
    "ship": lambda l, a, x: delivery.ship(l, a, x.branch),
    "return": lambda l, a, x: delivery.send_back(l, a, x.branch, why=x.why),
    "vouch": lambda l, a, x: outside.vouch(l, a, x.branch, commit=x.commit),
    "inbatch": lambda l, a, x: outside.inbatch(l, a, x.branch, commit=x.commit, read_by=x.read_by, why=x.why),
    "offledger": lambda l, a, x: outside.offledger(l, a, x.branch, merge=x.merge, witness=x.witness,
                                                   attested=x.attested),
    "walked": lambda l, a, x: judging.walked(l, a, x.branch, build=x.build, steps=x.steps, saw=x.saw),
    "broke": lambda l, a, x: _broke(l, a, x),
    "unbroke": lambda l, a, x: judging.unbroke(l, a, x.branch, why=x.why),
    "close": lambda l, a, x: judging.close(l, a, x.branch, ref=x.ref, why=x.why),
    "adopt": lambda l, a, x: _adopt(l, a, x),
    "hold": lambda l, a, x: steering.hold(l, a, x.branch, until=x.until, why=x.why),
    "unhold": lambda l, a, x: steering.unhold(l, a, x.branch),
    "urgent": lambda l, a, x: steering.urgent(l, a, x.branch, why=x.why, cancel=x.cancel),
    "walkable": lambda l, a, x: steering.walkable(l, a, x.branch, why=x.why, clear=x.clear),
}


def _receipt(args) -> int:
    tree = Path(args.tree)
    from flotilla.guards.rules import rules_for
    profile, note = rules_for(tree)   # the rules the guards obey, so a receipt answers the guard that asks
    if note:
        print(f"rules: {note}")
    ident = repo.identify(tree)
    state = paths.state_dir()
    if args.action == "run":
        from flotilla.lane.commands import _on_signals
        _on_signals()   # a stop reaches the tier as an exception, so its processes are stopped too (review of 0.6.2)
        if not args.timeout:   # a tier that will not end is stopped at the lane's ceiling (worldcore W21)
            from flotilla.lane.commands import ceiling_for
            args.timeout = float(ceiling_for(tree, profile, tiers=True))
        planned = receipts.to_run(ident.root, state=state, repo_key=ident.key, purpose=args.purpose,
                                  profile=profile)
        nothing = not planned
        if args.no_lane or nothing:
            why = ("--no-lane" if args.no_lane else "nothing to run: every tier is already green over these files"
                   if receipts.tiers_for(profile, args.purpose) else f"no {args.purpose} tiers configured")
            print(f"lane: not booked ({why})")
            result = receipts.run_receipt(ident.root, state=state, repo_key=ident.key, purpose=args.purpose,
                                          profile=profile, timeout=args.timeout, setup=args.setup)
        else:
            from flotilla.lane.commands import booked
            with booked(tree, note=f"{args.purpose} receipt", wait=args.lane_wait, will_run=planned,
                        purpose=args.purpose) as grant:
                import time
                began = time.monotonic()
                try:
                    result = receipts.run_receipt(ident.root, state=state, repo_key=ident.key,
                                                  purpose=args.purpose, profile=profile, timeout=args.timeout,
                                                  setup=args.setup)
                except BaseException as err:   # stopped, or refused mid-way: it held the lane all the same
                    grant.measured.update({"seconds": round(time.monotonic() - began, 2),
                                           "verdict": "refused" if isinstance(err, receipts.ReceiptRefused)
                                           else "killed"})
                    raise
                grant.measured.update(receipts.measured_of(result))
        for tier in result["tiers"]:
            print(f"{tier['status']:<9} {tier['name']}: {tier['summary']}")
            if tier["status"] != "green":   # why, not only that (worldcore field test W23)
                code = tier.get("exit")
                print(f"          {'exit ' + str(code) if code is not None else 'stopped for its time'}; "
                      "last lines:")
                for line in (tier.get("tail") or "(it printed nothing)").splitlines()[-15:]:
                    print(f"          | {visible(line)}")
        if not result["tiers"]:
            print(f"no {args.purpose} tiers configured; nothing to run")
        print(f"{args.purpose} receipt over {result['sha'][:7]}")
        return 0 if all(tier["status"] == "green" for tier in result["tiers"]) else 1
    sha = gitq.resolve(ident.root, args.rev)
    if sha is None:
        print(f"git could not resolve `{args.rev}`")
        return 2
    asked = [args.purpose] if args.purpose else list(receipts.PURPOSES)
    tiered = held = 0
    for purpose in asked:
        ok, why = receipts.check_receipt(state=state, repo_key=ident.key, sha=sha, purpose=purpose,
                                         profile=profile)
        print(f"{'ok' if ok else 'no':<3} {why}")
        if receipts.tiers_for(profile, purpose):   # "no tiers configured" is valid, and is no receipt
            tiered += 1
            held += ok
    return 0 if held or not tiered else 1   # a script reads the code: nothing green where tiers are set is a failure


def _events(args) -> int:
    if args.action == "schema":
        print(json.dumps(events.schema(), indent=2, sort_keys=True))
        return 0
    root = Path(args.root)
    if args.action == "check":
        scripts = tree_events(_project(root).root) if args.tree else trunk_rules(root).events
        where = "this tree" if args.tree else "trunk"
        if not scripts:
            print(f"no event scripts in {events.EVENTS_DIR} on {where}")
            return 0
        found = events.check(scripts, cwd=repo.identify(root).root)
        for name, status, text in found:
            print(f"{status:<12} {name}: {text}")
        return 0 if all(status == events.OK for _, status, _ in found) else 1
    ledger = open_ledger(root)
    script = ledger.events.get(args.name)
    if script is None:
        print(f"no `{events.EVENTS_DIR}/{args.name}` on trunk")
        return 2
    matches = [row for row in ledger.rows().values() if row.branch == args.row]
    if not matches:
        print(f"no ledger row for `{args.row}`")
        return 2
    row = matches[-1]
    data = events.payload(event=args.name, move="manual", state=args.name.split("-", 1)[-1],
                          repo=ledger.repo_key, trunk=ledger.trunk, by="(flotilla events run)", post="",
                          row=events.row_view(row, row.id, {}), evidence={})
    outcome = events.run_event(args.name, script, data, cwd=ledger.root)
    print(f"{outcome.status}: {outcome.text or '(no output)'}")
    return {events.OK: 0, events.REJECTED: 2}.get(outcome.status, 1)


def _broke(ledger, caller, args) -> tuple[Row, str]:
    broken, fix = judging.broke(ledger, caller, args.branch, where=args.where, saw=args.saw,
                                fix_branch=args.fix_branch)
    try:
        gone = fix.owner not in ledger.live_names()
    except MoveRefused:
        gone = False   # liveness unknown: the author may well be there
    if gone:   # a session of a past fleet takes nothing in a home tree (twosuns field test of 0.6.7)
        return broken, (f"fix row {fix.id} `{fix.branch}` filed; its author {fix.owner} is not alive, so the "
                        "orchestrator gives it an owner")
    return broken, (f"fix row {fix.id} `{fix.branch}` filed for {fix.owner}: take it in the home tree with "
                    f"`flotilla tree switch {fix.branch}`")


def reconcile_exit(lines: list[str]) -> int:
    """The worst of what reconcile found: a move owed - a refusal, or a pushed row to `land` - (2) outweighs a row not
    proved yet (3), which outweighs none."""
    if any(line.startswith(("refused ", "pushed, not landed ")) for line in lines):   # the second needs `land`
        return 2
    if any(line.startswith("not yet ") for line in lines):
        return 3
    return 0


def _skips(args) -> dict:
    name = getattr(args, "skip_event", None)
    if not name:
        return {}
    if name not in events.event_names():
        raise MoveRefused(f"--skip-event {name}: not an event name (pre-<state> or post-<state>)")
    why = (getattr(args, "skip_why", "") or "").strip()
    if not why:
        raise MoveRefused("--skip-event is a person's decision and says why (--skip-why \"<reason>\")")
    return {name: why}


def _now(ledger: core.Ledger) -> dt.datetime:
    return ledger.clock() if ledger.clock else dt.datetime.now(dt.timezone.utc)


def _stacked_under(ledger: core.Ledger, row: Row, tip: str) -> list[str]:
    """Tips of other open rows whose commits are theirs, not this row's (H4): every open row claimed before this one,
    wherever its branch has moved since (the part's author keeps working), and a row claimed after it only when its
    tip lies strictly under this one. A row stacked on this one never takes this row's commits away."""
    rows = ledger.rows()
    order = list(rows)
    mine = order.index(row.id) if row.id in order else len(order)
    under = []
    for other in rows.values():
        if not other.is_open or other.branch == row.branch or not other.branch:
            continue
        theirs = gitq.branch_tip(ledger.root, other.branch, run=ledger.run)
        if not theirs:
            continue
        earlier = order.index(other.id) < mine
        if earlier or (theirs != tip and gitq.is_ancestor(ledger.root, theirs, tip, run=ledger.run) is True):
            under.append(theirs)
    return under


def _finished(ledger: core.Ledger, row: Row) -> bool:
    if not receipts.tiers_for(ledger.profile, "handover"):
        return False
    tip = gitq.branch_tip(ledger.root, row.branch, run=ledger.run)
    if tip is None:
        return False
    if handover.has_own_commits(ledger, tip, beside=_stacked_under(ledger, row, tip)) is not True:
        return False
    ok, _ = receipts.check_receipt(state=ledger.state_dir, repo_key=ledger.repo_key, sha=tip, purpose="handover",
                                   profile=ledger.profile)
    return ok


def _status(ledger: core.Ledger, args) -> int:
    rows = ledger.rows()
    try:
        live = ledger.live_names()
        print(f"census: {len(live)} live session(s)")
    except MoveRefused as err:
        live = None
        print(f"census: unknown ({err}); liveness is not asserted")
    print("roster:")
    entries = views.roster(rows, ledger.profile, live)
    if not entries:
        print("  nobody holds open work")
    for entry in entries:
        holds = ", ".join(f"{row.branch} ({row.state})" for row in entry["rows"]) or "-"
        reading = f"; reading {', '.join(row.branch for row in entry['reading'])}" if entry["reading"] else ""
        blocked = f"; blocked on {', '.join(entry['blocked_on'])}" if entry["blocked_on"] else ""
        print(f"  {entry['who']}: {entry['state']} - {holds}{reading}{blocked}")
    print("whose move:")
    open_rows = [row for row in rows.values() if row.is_open]
    if not open_rows:
        print("  no open rows")
    for row in open_rows:
        mover = views.who_moves(row, ledger.profile, rows) or views.waits_on(row, rows, ledger.profile) \
            or "nobody named"
        pending = views.pending_dependents(rows, row, ledger.profile) \
            if row.state == "shipped" and (ledger.profile.get("judge") or {}).get("required") and not row.walkable \
            else []
        if pending:
            mover += f" (the judge walks it after {', '.join(o.branch or o.id for o in pending)} ship)"
        wait = f" (waiting on {row.waiting_on}: {row.note})" if row.waiting_on else ""
        held = f" (held until {row.held_until}: {row.held_why})" if row.held_until else ""
        ran = f" (last run: {strip_ansi(row.last_run)})" if row.last_run else ""
        print(f"  {row.id} {row.branch}: {row.state} -> {mover}{wait}{held}{ran}")
    from flotilla.posts import post_or_former
    for title, items in (("deviations", views.deviations(rows, ledger.profile, live,
                                                         finished=lambda row: _finished(ledger, row),
                                                         post_of=lambda name: _strict_post(ledger, name),
                                                         former_of=lambda name: post_or_former(ledger.posts, name))),
                         ("findings", findings.findings(ledger, rows))):
        print(f"{title}:" if items else f"{title}: none")
        for item in items:
            print(f"  {item['branch']}: {item['kind']} - {item['why']}")
    if (ledger.profile.get("reservation") or {}).get("files"):
        from flotilla.guards import reserve
        held = reserve.live(ledger.state_dir, ledger.repo_key, rows, ledger.profile)
        print("reservations:" if held else "reservations: none")
        for path, record in sorted(held.items()):
            print(f"  {path}: {record['by']} for {record['branch']} since {record['at']}")
    if args.stalled is not None:
        late = views.stalled(rows, args.stalled, _now(ledger))
        print(f"stalled over {args.stalled:g} h:" if late else f"stalled over {args.stalled:g} h: none")
        for row in late:
            print(f"  {row.id} {row.branch}: {row.state} since {row.updated_at}")
    return 0


def _brief(ledger: core.Ledger, args) -> int:
    print("\n".join(report.brief(ledger)))
    return 0


def _metrics(ledger: core.Ledger, args) -> int:
    failures = LocalLogStore(ledger.state_dir / "events").read(ledger.repo_key).records
    print("\n".join(report.format_metrics(report.metrics(ledger.rows(), failures))))
    return 0


VIEWS = {"status": _status, "brief": _brief, "metrics": _metrics}


#: Refusals of a move: the ledger stands and another move may be legal, so STUCK names the way forward.
MOVE_REFUSALS = (MoveRefused, PostError, receipts.ReceiptRefused, LaneRefused)


def run_ledger_command(args) -> int:
    ledger = None
    try:
        if args.command == "events":
            return _events(args)
        if args.command == "receipt":
            return _receipt(args)
        ledger = open_ledger(Path(args.root), skip_events=_skips(args))
        if args.command in VIEWS:
            return VIEWS[args.command](ledger, args)
        if args.command == "work" and args.move == "show":
            return _show(ledger, args.branch)
        if args.command == "work" and args.move == "approve":   # the person's move: no session, no post
            row = delivery.approve(ledger, args.branch)
            print(summary(row))
            return 0
        caller = resolve_actor(ledger.posts, as_name=args.as_name)
        before = ledger.rows()
        if args.command == "work" and args.move == "reconcile":
            lines = delivery.reconcile(ledger, caller)
            print("\n".join(lines) if lines else "nothing is queued or landed")
            _letters(ledger, caller, before)
            return reconcile_exit(lines)
        if args.command == "tree" and args.action == "switch":
            row = tree_mod.switch(ledger, caller, args.branch, ref=args.ref, requires=args.requires,
                                  after=args.after, also=args.also)
        elif args.command == "tree":
            row = tree_mod.cut(ledger, caller, args.branch, Path(args.tree), expect=args.expect, ref=args.ref,
                               requires=args.requires, after=args.after, also=args.also)
        else:
            result = MOVES[args.move](ledger, caller, args)
            if isinstance(result, tuple):
                row, text = result
                print(summary(row))
                print(text)
                _letters(ledger, caller, before)
                _notices(ledger)
                return 0
            row = result
    except NotYet as err:
        print(f"not yet: {err}")
        return 3
    except (MoveRefused, PostError, receipts.ReceiptRefused, config.ConfigError, repo.NotARepository,
            StorageCorrupt, LedgerVersionError, LaneRefused) as err:
        print(f"refused: {err}")
        if ledger is not None:
            _notices(ledger)
        print(STUCK if isinstance(err, MOVE_REFUSALS) else CANNOT_RUN)
        return 2
    print(summary(row))
    for other in (row.history[-1].get("evidence") or {}).get("stacked_on") or []:
        print(f"note: stacked on `{other}`, which is not accepted yet")
    _letters(ledger, caller, before)
    _notices(ledger)
    return 0


def _strict_post(ledger: core.Ledger, name: str) -> str:
    from flotilla.posts import PostError, post_for_session
    try:
        found = post_for_session(ledger.posts, name)
    except PostError:
        return ""
    return found.name if found is not None else ""


def _letters(ledger: core.Ledger, caller, before: dict) -> None:
    """Print the letters a recorded move owes. The move is already in the ledger, so a failure here is a note,
    never a refusal: a session that read "refused" would make the move again."""
    def live():   # this project's sessions: a sender of another repository is no recipient (F20)
        try:
            sessions = ledger.live_sessions()
        except MoveRefused:
            return None
        from flotilla.ledger import project
        roots = project.roots(ledger.root, ledger.run)
        return {session.name for session in project.members(sessions, ledger.rows(), roots) if session.name}
    try:
        due = letters.changed(before, ledger.rows(), ledger.profile, ledger.posts, caller.name, live)
    except Exception as err:  # noqa: BLE001 - the move stands; say what could not be done
        print(f"note: the move is recorded; its letters could not be computed: {err}")
        return
    for letter in due:
        print("\n".join(letters.render(letter)))


def _notices(ledger: core.Ledger) -> None:
    for notice in ledger.notices:
        print(f"note: {notice}")
