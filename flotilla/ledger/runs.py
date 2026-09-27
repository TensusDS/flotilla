"""A run's result, recorded on the row it was run for (spec, section 6.9, layer 1).

`flotilla lane run --for <branch>` ties the start, the end, the exit code, the revision and the summary line to the
row, so the roster can say "the reader's run finished green twelve minutes ago; waiting on the verdict" instead of
everyone believing the run is still going. It is a fact about the machine, not a decision about the work, so no
post check applies; the session that ran it is identified as for any move, and the row's state does not change.
"""

from __future__ import annotations

from flotilla.ledger.actor import Actor
from flotilla.ledger.core import Ledger
from flotilla.ledger.model import Row


def record_run(ledger: Ledger, actor: Actor, branch: str, *, verdict: str, summary: str, revision: str,
               evidence: dict) -> Row:
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "run")
        text = f"{verdict}: {summary} over {revision[:7] or 'unknown'} at {ledger.now()}"
        return s.append(actor, row.id, "run", state, fields={"last_run": text}, evidence=evidence)
