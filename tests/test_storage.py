import json
import subprocess
import sys
from pathlib import Path

import pytest

from flotilla.core.storage import LocalLogStore, StorageCorrupt

ROOT = Path(__file__).resolve().parent.parent


def test_missing_log_reads_empty(tmp_path):
    result = LocalLogStore(tmp_path).read("app-0123456789ab")
    assert result.records == [] and result.torn_tail is False


def test_append_then_read_in_order(tmp_path):
    store = LocalLogStore(tmp_path)
    store.append("k", {"n": 1})
    store.append("k", {"n": 2, "text": "caf\u00e9"})
    assert [r["n"] for r in store.read("k").records] == [1, 2]
    assert store.read("k").records[1]["text"] == "caf\u00e9"


def test_torn_tail_is_left_out_and_reported(tmp_path):
    (tmp_path / "k.jsonl").write_text('{"n":1}\n{"n":2', encoding="utf-8")
    result = LocalLogStore(tmp_path).read("k")
    assert result.records == [{"n": 1}] and result.torn_tail is True


def test_complete_json_without_newline_is_still_torn(tmp_path):
    # A writer that died between the record and its newline never committed it.
    (tmp_path / "k.jsonl").write_text('{"n":1}\n{"n":2}', encoding="utf-8")
    result = LocalLogStore(tmp_path).read("k")
    assert result.records == [{"n": 1}] and result.torn_tail is True


def test_damaged_middle_line_is_corruption_not_a_skip(tmp_path):
    (tmp_path / "k.jsonl").write_text('{"n":1}\nnot json\n{"n":3}\n', encoding="utf-8")
    with pytest.raises(StorageCorrupt, match=r"k\.jsonl:2"):
        LocalLogStore(tmp_path).read("k")


def test_append_after_torn_tail_sets_fragment_aside(tmp_path):
    (tmp_path / "k.jsonl").write_text('{"n":1}\n{"n":2', encoding="utf-8")
    store = LocalLogStore(tmp_path)
    store.append("k", {"n": 3})
    assert [r["n"] for r in store.read("k").records] == [1, 3]
    assert store.read("k").torn_tail is False
    assert (tmp_path / "k.torn").read_text(encoding="utf-8") == '{"n":2\n'


def test_transaction_reads_and_appends_under_one_lock(tmp_path):
    store = LocalLogStore(tmp_path)
    with store.transaction("k") as tx:
        assert tx.read().records == []
        tx.append({"n": 1})
        assert tx.read().records == [{"n": 1}]


@pytest.mark.parametrize("key", ["", "../escape", "a/b", "UPPER", ".hidden"])
def test_unsafe_keys_are_refused(tmp_path, key):
    with pytest.raises(ValueError):
        LocalLogStore(tmp_path).append(key, {"n": 1})


WRITER = """
import sys
sys.path.insert(0, {root!r})
from flotilla.core.storage import LocalLogStore
store = LocalLogStore({dir!r})
for n in range({count}):
    store.append("k", {{"writer": {writer}, "n": n, "pad": "x" * 2000}})
"""


def test_concurrent_appends_keep_every_line_whole(tmp_path):
    writers, count = 4, 200
    procs = [subprocess.Popen([sys.executable, "-c", WRITER.format(
        root=str(ROOT), dir=str(tmp_path), count=count, writer=w)]) for w in range(writers)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    result = LocalLogStore(tmp_path).read("k")
    assert result.torn_tail is False
    assert len(result.records) == writers * count
    for w in range(writers):
        assert [r["n"] for r in result.records if r["writer"] == w] == list(range(count))
