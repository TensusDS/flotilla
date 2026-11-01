"""The disk's state, gc design section 8. Low is free space under the smaller of a share of the volume and a size;
no room is under a floor. A disk that cannot be asked is unknown, and unknown refuses nothing."""

import shutil
from dataclasses import dataclass

from flotilla.core.config import whole_number

LOW_PERCENT = 10
LOW_MB = 20480
NO_ROOM_MB = 2048


@dataclass(frozen=True)
class Disk:
    free_mb: int | None
    total_mb: int | None
    low: bool
    no_room: bool
    text: str
    notes: tuple[str, ...] = ()
    floor_mb: int = NO_ROOM_MB


def _percent(value) -> tuple[int, str]:
    if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 100:
        return value, ""
    if isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal() and 0 < int(value) <= 100:
        return int(value), ""
    return LOW_PERCENT, f"`gc.disk_low_percent` = {value!r} is not a whole percent from 1 to 100; {LOW_PERCENT} is used"


def state(path, profile: dict, *, usage=shutil.disk_usage) -> Disk:
    config = profile.get("gc") or {}
    notes = []
    percent, note = _percent(config.get("disk_low_percent", LOW_PERCENT))
    notes.append(note)
    low_mb, note = whole_number(config.get("disk_low_mb", LOW_MB), LOW_MB, "gc.disk_low_mb")
    notes.append(note)
    floor, note = whole_number(config.get("disk_no_room_mb", NO_ROOM_MB), NO_ROOM_MB, "gc.disk_no_room_mb")
    notes.append(note)
    notes = tuple(note for note in notes if note)
    try:
        found = usage(path)
    except OSError as err:
        return Disk(None, None, False, False, f"the disk could not be asked ({err})", notes, floor)
    free, total = found.free // 2**20, found.total // 2**20
    threshold = min(total * percent // 100, low_mb)
    no_room = free < floor
    low = no_room or free < threshold
    text = f"{free} MB free of {total} MB" + (" (no room)" if no_room else " (low)" if low else "")
    return Disk(free, total, low, no_room, text, notes, floor)


def refusal(disk: Disk) -> str:
    """Why no new work starts, or ""."""
    if not disk.no_room:
        return ""
    return (f"{disk.free_mb} MB free on this disk, under the {disk.floor_mb} MB floor (`[gc] disk_no_room_mb`): no new "
            "work starts until space is freed elsewhere or the archive is settled (`/flotilla:gc` in the person's own "
            "session)")
