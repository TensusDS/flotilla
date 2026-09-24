"""Which moves are legal from which state, computed from the project profile (spec, section 6.2).

The base graph is the same for every project; the profile removes edges. A pull-request project ships from the
queue; a direct-push project lands first; a project without origin ends at landed; review depth decides whether a
claim may skip review; a required judge must walk before close. Annotations (wait, hold, unhold, adopt) apply to any
open row and leave its state alone.
"""

from __future__ import annotations

from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import TERMINAL

BASE = {
    "reserved": {"release": "released"},
    "claimed": {"hand": "handed", "queue": "queued", "release": "released"},
    "handed": {"moved": "handed", "assign": "handed", "recuse": "handed", "take": "handed", "fix": "fixing",
               "accept": "accepted", "release": "released"},
    "fixing": {"hand": "handed", "assign": "fixing", "recuse": "fixing", "release": "released"},
    "accepted": {"queue": "queued", "release": "released"},
    "queued": {"land": "landed", "ship": "shipped", "release": "released"},
    "landed": {"ship": "shipped", "close": "closed"},
    "shipped": {"walked": "walked", "broke": "shipped", "close": "closed"},
    "walked": {"close": "closed"},
}
ANNOTATIONS = ("wait", "hold", "unhold", "adopt")


def moves_from(state: str, profile: dict, owner_post: str = "") -> dict[str, str]:
    legal = dict(BASE.get(state, {}))
    mode = (profile.get("flow") or {}).get("mode", "local")
    depth = (profile.get("review") or {}).get("depth", "every")
    if state == "claimed":
        skips_review = depth == "none" or (depth == "main-only" and owner_post != "main")
        if not skips_review:
            legal.pop("queue", None)
    if state == "queued":
        legal.pop("land" if mode == "pr" else "ship", None)
    if state == "landed":
        legal.pop("ship" if mode == "local" else "close", None)
    if state == "shipped" and (profile.get("judge") or {}).get("required"):
        legal.pop("close", None)
    if state not in TERMINAL and state in BASE:
        for annotation in ANNOTATIONS:
            legal[annotation] = state
    return legal


def next_state(state: str, move: str, profile: dict, owner_post: str = "") -> str:
    legal = moves_from(state, profile, owner_post)
    if move not in legal:
        options = ", ".join(sorted(legal)) or "none (the row is finished)"
        raise MoveRefused(f"`{move}` is not legal from `{state}` in this project. Legal from here: {options}")
    return legal[move]
