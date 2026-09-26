"""Event scripts: `.flotilla/events/pre-<state>` and `post-<state>` (spec, section 6.8).

A move that changes a row's state runs the project's `pre-<state>` script before its event is written and the
`post-<state>` script after. The move reaches the script as JSON on stdin (`event_schema` 1, documented in
`docs/events/schema.json`, generated from this module). A `pre-` script exits 0 to allow the move and 2 to reject
it, its output shown; any other exit, a crash, a timeout, or a file that is not executable refuses the move and
names the script, because a check that silently stopped running reads exactly like a check that passed. A `post-`
script cannot undo anything; its failure is printed and counted. Scripts are read from trunk.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from flotilla.ledger.model import ROW_FIELDS, STATES, Row

EVENT_SCHEMA = 1
EVENTS_DIR = ".flotilla/events"
TIMEOUT = 30.0
OK, REJECTED, BROKEN = "ok", "rejected", "broken"
PHASES = ("pre", "post")


@dataclass(frozen=True)
class Outcome:
    status: str
    text: str


def event_names() -> list[str]:
    return [f"{phase}-{state}" for state in STATES for phase in PHASES]


def row_view(before: Row | None, row_id: str, fields: dict) -> dict:
    """The row as the move leaves it: its fields before the move, with the move's fields applied."""
    base = before if before is not None else Row(id=row_id)
    view = {"id": row_id, **{name: getattr(base, name) for name in ROW_FIELDS}}
    view.update({key: value for key, value in fields.items() if key in ROW_FIELDS})
    return view


def payload(*, event: str, move: str, state: str, repo: str, trunk: str, by: str, post: str, row: dict,
            evidence: dict) -> dict:
    return {"event_schema": EVENT_SCHEMA, "event": event, "move": move, "state": state, "repo": repo,
            "trunk": trunk, "by": by, "post": post, "row": row, "evidence": evidence}


def _tail(text: str, lines: int = 10, chars: int = 1500) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])[-chars:]


def run_event(name: str, script: tuple[bytes, bool], data: dict, *, cwd, run=subprocess.run,
              timeout: float | None = None) -> Outcome:
    body, executable = script
    if not executable:
        return Outcome(BROKEN, "not executable on trunk; make it executable (`git update-index --chmod=+x`) and "
                               "commit")
    limit = TIMEOUT if timeout is None else timeout
    with tempfile.TemporaryDirectory(prefix="flotilla-event-") as tmp:
        path = Path(tmp) / name
        path.write_bytes(body)
        path.chmod(0o700)
        try:
            done = run([str(path)], input=json.dumps(data), cwd=str(cwd), capture_output=True, text=True,
                       errors="replace", check=False, timeout=limit)
        except subprocess.TimeoutExpired:
            return Outcome(BROKEN, f"did not finish within {limit:g} s")
        except OSError as err:
            return Outcome(BROKEN, f"could not start: {err}")
    text = _tail((done.stdout or "") + (done.stderr or ""))
    if done.returncode == 0:
        return Outcome(OK, text)
    if done.returncode == 2:
        return Outcome(REJECTED, text)
    return Outcome(BROKEN, f"exited {done.returncode}" + (f": {text}" if text else ""))


TYPES = {"str": "string", "list": "array", "bool": "boolean"}


def schema() -> dict:
    """The event contract. `docs/events/schema.json` is this, generated; a test keeps them equal."""
    sample = Row(id="")
    return {
        "event_schema": EVENT_SCHEMA,
        "events": event_names(),
        "stdin": {
            "event_schema": "integer: the version of this contract",
            "event": "string: the script's own name, pre-<state> or post-<state>",
            "move": "string: the ledger move, for example hand or ship",
            "state": "string: the state the move enters",
            "repo": "string: the repository key",
            "trunk": "string: the trunk branch",
            "by": "string: the session that made the move",
            "post": "string: its post",
            "row": {"id": "string", **{name: TYPES[type(getattr(sample, name)).__name__] for name in ROW_FIELDS}},
            "evidence": "object: the move's evidence",
        },
        "exit": {
            "0": "allow the move (pre-); nothing (post-)",
            "2": "reject the move (pre- only); what the script printed is shown",
            "other": "the script is broken: a pre- script refuses the move and is named; a post- failure is "
                     "printed and counted",
        },
        "timeout_seconds": TIMEOUT,
    }


def sample_payload(name: str) -> dict:
    state = name.split("-", 1)[1] if "-" in name else "claimed"
    row = row_view(None, "r1", {"branch": "feat/example", "owner": "main session 1"})
    return payload(event=name, move="example", state=state, repo="example", trunk="main", by="main session 1",
                   post="main", row=row, evidence={})


def _interpreter_problem(body: bytes) -> str:
    first = body.split(b"\n", 1)[0].decode("utf-8", "replace")
    if not first.startswith("#!"):
        return ""
    words = first[2:].split()
    if not words:
        return "an empty shebang line"
    if Path(words[0]).name == "env" and len(words) > 1:
        return "" if shutil.which(words[1]) else f"interpreter `{words[1]}` is not on PATH"
    return "" if Path(words[0]).exists() else f"interpreter {words[0]} not found"


def check(scripts: dict, *, cwd, run=subprocess.run) -> list[tuple[str, str, str]]:
    """Each script: a known name, an interpreter that exists, and a clean answer to a sample move."""
    names = set(event_names())
    found = []
    for name in sorted(scripts):
        if name not in names:
            found.append((name, "unknown name", "not pre-<state> or post-<state>; flotilla never runs it"))
            continue
        problem = _interpreter_problem(scripts[name][0])
        if problem:
            found.append((name, BROKEN, problem))
            continue
        outcome = run_event(name, scripts[name], sample_payload(name), cwd=cwd, run=run)
        if outcome.status == BROKEN:
            found.append((name, BROKEN, outcome.text))
        else:
            found.append((name, OK, f"answers a sample move ({outcome.status})"))
    return found
