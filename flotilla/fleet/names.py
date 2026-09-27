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


def next_names(post, count: int, *, taken: set[str], store, reserve: bool, now: str = "") -> list[str]:
    with store.transaction(KEY) as tx:
        issued = [record.get("n", 0) for record in tx.read().records if record.get("post") == post.name]
        known = [number_of(post, name) or 0 for name in taken]
        number = max([0, *issued, *known])
        found: list[str] = []
        while len(found) < count:
            number += 1
            name = post.name_pattern.replace("{n}", str(number))
            if name in taken:
                continue
            found.append(name)
            if reserve:
                tx.append({"post": post.name, "n": number, "name": name, "at": now})
    return found
