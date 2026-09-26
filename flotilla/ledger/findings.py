"""What git shows that the ledger does not (spec, section 6.7).

Five findings, each a question to git the ledger alone cannot answer:
- vanished: an open row whose branch is gone, locally and on origin. Merged-and-deleted and abandoned look the
  same, so it is a person's decision, never a move made for them;
- after_close: a finished row whose branch moved on and did not ship: work done after the row ended, seen by no one;
- unread_in_trunk: open work, not accepted, already in trunk;
- direct_commit: a commit on trunk since the oldest recorded base that no verdict covers (not a merge, not a
  release, not the same change as reviewed work);
- inbatch_not_pushed: work born in a batch that origin does not have.
A question git cannot answer produces no finding rather than a false one.
"""

from __future__ import annotations

from flotilla.ledger import batch, gitq
from flotilla.ledger.model import Row

UNACCEPTED = ("claimed", "handed", "fixing")
ENDED = ("closed", "released", "offledger", "shipped", "walked")
SCAN = 200


def _item(kind: str, row: Row, sha: str, why: str) -> dict:
    return {"kind": kind, "branch": row.branch, "sha": sha, "why": why}


def _horizon(ledger, rows: dict[str, Row], trunk: str) -> str:
    """The oldest recorded base that trunk contains: commits after it happened while the ledger was kept."""
    bases = {row.base for row in rows.values() if row.base}
    oldest = ""
    for base in bases:
        if gitq.is_ancestor(ledger.root, base, trunk, run=ledger.run) is not True:
            continue
        if not oldest or gitq.is_ancestor(ledger.root, base, oldest, run=ledger.run) is True:
            oldest = base
    return oldest


def findings(ledger, rows: dict[str, Row] | None = None) -> list[dict]:
    rows = ledger.rows() if rows is None else rows
    found: list[dict] = []
    trunk = gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
    origin = gitq.resolve(ledger.root, f"refs/remotes/origin/{ledger.trunk}", run=ledger.run)
    latest: dict[str, Row] = {}
    for row in rows.values():
        latest[row.branch] = row
    for row in rows.values():
        if not row.is_open or row.state == "reserved":
            continue
        local = gitq.branch_tip(ledger.root, row.branch, run=ledger.run)
        if not (row.fixes and not row.tree) and local is None and \
                gitq.resolve(ledger.root, f"refs/remotes/origin/{row.branch}", run=ledger.run) is None:
            found.append(_item("vanished", row, "", f"row {row.id} is {row.state}, and its branch is gone locally "
                                                    "and on origin; release it, or record it with offledger"))
        if row.state in UNACCEPTED and local and local != row.base and \
                gitq.is_ancestor(ledger.root, local, trunk, run=ledger.run) is True:
            found.append(_item("unread_in_trunk", row, local, f"row {row.id} is {row.state}, and its tip "
                                                              f"{local[:7]} is already in `{trunk}`: it reached trunk "
                                                              "without a verdict; record it with offledger and a "
                                                              "witness"))
    for branch, row in latest.items():
        if row.state not in ENDED or not row.tip:
            continue
        current = gitq.branch_tip(ledger.root, branch, run=ledger.run)
        if current is None or current == row.tip:
            continue
        if origin and gitq.is_ancestor(ledger.root, current, origin, run=ledger.run) is True:
            continue
        found.append(_item("after_close", row, current, f"`{branch}` moved to {current[:7]} after row {row.id} "
                                                        f"ended ({row.state} at {row.tip[:7]}); claim the new work"))
    horizon = _horizon(ledger, rows, trunk)
    if horizon:
        done = ledger.run(["git", "-C", str(ledger.root), "rev-list", "--no-merges", "--reverse", f"-{SCAN}",
                           trunk, f"^{horizon}"], capture_output=True, text=True, check=False)
        for sha in (done.stdout.split() if done.returncode == 0 else []):
            if batch.account(ledger, rows, sha) is None:
                row = Row(id="", branch=ledger.trunk)
                found.append(_item("direct_commit", row, sha, f"{sha[:7]} {batch.subject(ledger, sha)}: on "
                                                              f"`{trunk}`, and no verdict covers it"))
    if origin:
        for row in rows.values():
            if row.state == "inbatch" and row.merge and \
                    gitq.is_ancestor(ledger.root, row.merge, origin, run=ledger.run) is False:
                found.append(_item("inbatch_not_pushed", row, row.merge, f"{row.merge[:7]} was born in the batch "
                                                                         "and origin does not have it"))
    return found
