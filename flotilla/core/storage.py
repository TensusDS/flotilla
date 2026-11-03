"""Append-only move logs, one file per key, written under an exclusive file lock.

This is the only module that touches log files. A log is JSON Lines; a line counts only once its
newline is on disk. The reader distinguishes a torn tail (a writer died mid-record: the fragment
was never committed, so it is left out and reported) from a damaged middle line (history itself is
broken: that is an error, never a silent skip).
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class StorageCorrupt(RuntimeError):
    """A line other than the last does not parse: history is damaged, not torn."""


@dataclass(frozen=True)
class ReadResult:
    records: list[dict]
    torn_tail: bool


def _read(path: Path) -> ReadResult:
    # Bytes first, decoding per committed line: a tear may cut a multi-byte character, and that
    # must read as a torn tail, not as a decoding error of the whole file.
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ReadResult([], False)
    lines = data.split(b"\n")
    tail = lines.pop()  # b"" when the file ends with a newline
    records = []
    for number, raw in enumerate(lines, start=1):
        try:
            records.append(json.loads(raw.decode("utf-8")))
        except UnicodeDecodeError as err:
            raise StorageCorrupt(f"{path}:{number}: not UTF-8 ({err.reason})") from err
        except json.JSONDecodeError as err:
            raise StorageCorrupt(f"{path}:{number}: {err.msg}") from err
    return ReadResult(records, tail != b"")


def _digest(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=16).digest()


def _read_after(path: Path, offset: int, identity: tuple | None) -> tuple[list[dict], int, tuple, bool]:
    """The committed lines after `offset`, the offset after them, what identifies the file as read, and whether it was
    read from the start. A log is only ever appended to (a torn tail past the committed lines is the one thing cut), so
    a reader that remembers where it stopped reads on from there; a file that is another one - replaced, shortened, or
    no longer holding the bytes it held up to `offset`, an edit in place included - is read from the start. The check
    hashes what was read before: a read of the file, not the parse that is most of a fold."""
    try:
        with open(path, "rb") as handle:
            stat = os.fstat(handle.fileno())
            whole = handle.read()
    except FileNotFoundError:
        return [], 0, (None, None, b""), True
    end = whole.rfind(b"\n") + 1   # what follows the last newline was never committed
    start = 0
    if identity is not None and tuple(identity[:2]) == (stat.st_dev, stat.st_ino) and 0 < offset <= end \
            and _digest(whole[:offset]) == identity[2]:
        start = offset
    records = []
    for number, raw in enumerate(whole[start:end].split(b"\n")[:-1], start=1):
        try:
            records.append(json.loads(raw.decode("utf-8")))
        except UnicodeDecodeError as err:
            raise StorageCorrupt(f"{path}: line {number} after byte {start}: not UTF-8 ({err.reason})") from err
        except json.JSONDecodeError as err:
            raise StorageCorrupt(f"{path}: line {number} after byte {start}: {err.msg}") from err
    return records, end, (stat.st_dev, stat.st_ino, _digest(whole[:end])), start == 0


class LogTransaction:
    """Reads and appends while the caller holds the key's lock."""

    def __init__(self, path: Path):
        self._path = path

    def read(self) -> ReadResult:
        return _read(self._path)

    def read_after(self, offset: int, identity: tuple | None = None) -> tuple[list[dict], int, tuple, bool]:
        return _read_after(self._path, offset, identity)

    def replace(self, records: list[dict]) -> None:
        """Rewrite the log whole: written beside it, synced, renamed over it, so a reader holds either the old file or
        the new one and a reader that remembered an offset sees another file and reads it from the start. A failure
        leaves the log as it was."""
        tmp = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                for record in records:
                    handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        finally:
            tmp.unlink(missing_ok=True)

    def append(self, record: dict) -> None:
        line = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self._set_torn_tail_aside()
        with open(self._path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _set_torn_tail_aside(self) -> None:
        """Move an uncommitted fragment out of the log, so the next record is not glued to it."""
        try:
            data = self._path.read_bytes()
        except FileNotFoundError:
            return
        if not data or data.endswith(b"\n"):
            return
        cut = data.rfind(b"\n") + 1
        with open(self._path.with_suffix(".torn"), "ab") as aside:
            aside.write(data[cut:] + b"\n")
        with open(self._path, "r+b") as handle:
            handle.truncate(cut)


class LogStore(Protocol):
    def transaction(self, key: str) -> contextlib.AbstractContextManager[LogTransaction]: ...
    def read(self, key: str) -> ReadResult: ...
    def append(self, key: str, record: dict) -> None: ...


class LocalLogStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.match(key):
            raise ValueError(f"unsafe log key: {key!r}")
        return self.root / f"{key}.jsonl"

    @contextlib.contextmanager
    def transaction(self, key: str) -> Iterator[LogTransaction]:
        path = self._path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / f"{key}.lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield LogTransaction(path)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self, key: str) -> ReadResult:
        """Lock-free: a reader may see a record being written as a torn tail."""
        return _read(self._path(key))

    def read_after(self, key: str, offset: int, identity: tuple | None = None) -> tuple[list[dict], int, tuple, bool]:
        """Lock-free, like `read`: the committed lines after `offset` of the file `identity` names."""
        return _read_after(self._path(key), offset, identity)

    def append(self, key: str, record: dict) -> None:
        with self.transaction(key) as tx:
            tx.append(record)
