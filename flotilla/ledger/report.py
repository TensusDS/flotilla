"""The batch for one "yes", and what the ledger's history measures (spec, section 6.7).

`brief` lists every row ready to ship — accepted, queued or landed — urgent requests first, oldest request first,
each with whose it is, who accepted it over which revision, and its PR or landed commit; then what is held back and
why (a dependency not delivered, a branch that moved since the verdict); then the gate those commits will stand
on, said out loud when it is none. `metrics` reads the history: median time in each state, how many handovers came
back, accepts per reader, and event scripts that broke.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import Counter, defaultdict

from flotilla.ledger import batch, gitq, receipts
from flotilla.ledger.model import Row, blocked_by
from flotilla.ledger.views import approve_command

READY = ("accepted", "queued", "landed")


def _gate_line(ledger) -> str:
    ci = ledger.profile.get("ci") or {}
    provider = ci.get("provider", "none")
    if provider == "github":
        jobs = ", ".join(ci.get("required_jobs") or []) or "none named - run /flotilla:check"
        return f"gate: GitHub CI, required jobs: {jobs}"
    if provider == "command":
        return f"gate: the gate command `{ci.get('gate_command') or '(none set; the sender asks the person)'}`"
    if receipts.tiers_for(ledger.profile, "push"):
        return "gate: no CI - verified locally by the push tiers over each revision above"
    return "gate: none - no CI and no push tiers; nothing verifies these commits"


def _held_back(ledger, rows: dict[str, Row], row: Row) -> list[str]:
    reasons = []
    waiting = blocked_by(rows, row, ledger.profile)
    if waiting:
        reasons.append("blocked on " + ", ".join(f"`{other.branch or other.id}` ({other.state or 'unknown'})"
                                                 for other in waiting))
    current = gitq.branch_tip(ledger.root, row.branch, run=ledger.run)
    if row.state == "accepted" and row.verdict and current and current != row.verdict:
        reasons.append(f"moved since acceptance ({row.verdict[:7]} -> {current[:7]})")
    return reasons


def brief(ledger, rows: dict[str, Row] | None = None) -> list[str]:
    rows = ledger.rows() if rows is None else rows
    ready, held = [], []
    for row in rows.values():
        if row.is_open and row.state in READY and not (row.state == "landed" and ledger.mode == "local"):
            reasons = _held_back(ledger, rows, row)
            (held if reasons else ready).append((row, reasons))
    ready.sort(key=lambda item: (0, item[0].urgent_at) if item[0].urgent_at else (1, item[0].updated_at))
    depth = (ledger.profile.get("review") or {}).get("depth", "every")
    lines = [f"Batch for approval: {ledger.repo_key}, trunk `{ledger.trunk}`, flow {ledger.mode}"]
    if not ready:
        lines.append("  nothing is ready to ship")
    push_tiers = bool(receipts.tiers_for(ledger.profile, "push"))
    human = (ledger.profile.get("flow") or {}).get("merge_authorized_by") == "human"
    for number, (row, _) in enumerate(ready, 1):
        read = batch.revision_of(row)
        who = (f"accepted by {row.reader} over {row.verdict[:7]}" if row.verdict else
               f"not reviewed (review depth `{depth}`), tip {row.tip[:7]}")
        extras = []
        if row.pr:
            extras.append(f"PR #{row.pr}")
        if row.merge:
            extras.append(f"landed {row.merge[:7]}")
        if push_tiers and read:
            ok, _ = receipts.check_receipt(state=ledger.state_dir, repo_key=ledger.repo_key, sha=read,
                                           purpose="push", profile=ledger.profile)
            extras.append(f"push receipt {'green' if ok else 'missing'} over {read[:7]}")
        lines.append(f"  {number}. `{row.branch}` ({row.state}) by {row.owner}; {who}"
                     + (f"; {'; '.join(extras)}" if extras else ""))
        if row.urgent_at:
            lines.append(f"     urgent since {row.urgent_at}: {row.urgent_why or 'no reason given'}")
        if human and not (read and row.approved == read):
            lines.append(f"     approve: ! {approve_command(ledger, row.branch)}")
    if held:
        lines.append("Not in this batch:")
        lines.extend(f"  `{row.branch}` ({row.state}): {'; '.join(reasons)}" for row, reasons in held)
    lines.append(_gate_line(ledger))
    if ready:
        lines.append(f"Answer yes to ship {len(ready)} row{'s' if len(ready) != 1 else ''}.")
    return lines


def _seconds(start: str, end: str) -> float | None:
    try:
        return (dt.datetime.fromisoformat(end) - dt.datetime.fromisoformat(start)).total_seconds()
    except (TypeError, ValueError):
        return None


def metrics(rows: dict[str, Row], failures=()) -> dict:
    durations: dict[str, list[float]] = defaultdict(list)
    moves: Counter = Counter()
    accepts: Counter = Counter()
    for row in rows.values():
        current, entered = None, ""
        for entry in row.history:
            moves[entry["move"]] += 1
            if entry["move"] == "accept":
                accepts[entry["by"]] += 1
            if entry["state"] != current:
                if current is not None:
                    seconds = _seconds(entered, entry["at"])
                    if seconds is not None:
                        durations[current].append(seconds)
                current, entered = entry["state"], entry["at"]
    broken = Counter(item.get("event", "?") for item in failures if item.get("status") == "broken")
    return {"time_in_state": {state: statistics.median(values) for state, values in sorted(durations.items())},
            "return_rate": moves["fix"] / moves["hand"] if moves["hand"] else None,
            "accepts_by_reader": dict(sorted(accepts.items())),
            "event_failures": dict(sorted(broken.items()))}


def _human(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def format_metrics(found: dict) -> list[str]:
    lines = ["time in state (median):"]
    lines += [f"  {state:<9} {_human(value)}" for state, value in found["time_in_state"].items()] or ["  no moves yet"]
    rate = found["return_rate"]
    lines.append("return rate: no handovers yet" if rate is None else
                 f"return rate: {rate:.0%} of handovers came back")
    lines.append("accepts by reader:")
    lines += [f"  {who}: {count}" for who, count in found["accepts_by_reader"].items()] or ["  none yet"]
    lines.append("event script failures:")
    lines += [f"  {name}: {count}" for name, count in found["event_failures"].items()] or ["  none"]
    return lines
