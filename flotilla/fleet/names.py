"""Names for new sessions: from the issued-numbers journal, never "highest live + 1" (spec, section 7.2).

Live sessions are a snapshot of a minute; by morning they are gone, and a counter taken from them would reissue
names that yesterday's records already carry, so two different sessions' work would read as one. The journal is
append-only, one per machine (the census is machine-wide), and locked: two spawns in one second get different
numbers. A new number is above every number issued for the post and above every taken name's number; a name is
never equal to a taken one.
"""

from __future__ import annotations

import re

KEY = "names"


def number_of(post, name: str) -> int | None:
    regex = "^" + re.escape(post.name_pattern).replace(re.escape("{n}"), r"(\d+)") + "$"
    found = re.match(regex, name)
    return int(found.group(1)) if found else None


def _same_series(record: dict, post) -> bool:
    """Whether an issued number counts against this post's next one: a project's fleet name gives its posts their own
    series (W11); a record from before names carried patterns belongs to the machine-wide, unprefixed series."""
    if "pattern" in record:
        return record["pattern"] == post.name_pattern
    return record.get("post") == post.name and not getattr(post, "project", "")


def next_names(post, count: int, *, taken: set[str], store, reserve: bool, now: str = "", occupied=None,
               skipped: list | None = None) -> list[str]:
    """`occupied(name)` says why a name's seat cannot be made here (its tree is already on disk), or ""; such a
    number is stepped over and noted in `skipped` - never reused for a seat, never touched (twosuns, 2026-10-03)."""
    with store.transaction(KEY) as tx:
        issued = [record.get("n", 0) for record in tx.read().records if _same_series(record, post)]
        known = [number_of(post, name) or 0 for name in taken]
        number = max([0, *issued, *known])
        found: list[str] = []
        while len(found) < count:
            number += 1
            name = post.name_pattern.replace("{n}", str(number))
            if name in taken:
                continue
            why = occupied(name) if occupied else ""
            if why:
                if skipped is not None:
                    skipped.append(f"skipped `{name}`: {why}")
                continue
            found.append(name)
            if reserve:
                tx.append({"post": post.name, "pattern": post.name_pattern, "n": number, "name": name, "at": now})
    return found


def numbered_after(post, *, taken: set[str], live: set[str], store) -> str:
    """Why a post's next number is not 1 (field test F7): names are machine-wide addresses, so another project's
    live session holds its number here too. "" when numbering starts at 1."""
    issued = max((record.get("n", 0) for record in store.read(KEY).records if _same_series(record, post)),
                 default=0)
    known = {name: number_of(post, name) or 0 for name in taken}
    top_known = max(known.values(), default=0)
    if top_known == 0 and issued == 0:
        return ""
    if top_known >= issued:
        name = max(known, key=known.get)
        where = ("alive on this machine; names are machine-wide addresses" if name in live
                 else "named in this ledger")
        return f"{post.name} numbering continues after {name} ({where})"
    return (f"{post.name} numbering continues after number {issued}, issued by an earlier spawn "
            "(an issued number is never reused)")
