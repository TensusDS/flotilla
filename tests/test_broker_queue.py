import os

import pytest

from flotilla.broker import queue

KEY = "repo"


def ask(tmp_path, now=1000.0, session="main session 1", wait=540, pid=None):
    return queue.ask(tmp_path, KEY, session=session, session_id=f"id-{session}", tool="Bash",
                     tool_input={"command": "touch x"}, suggestions=[], wait=wait, now=now, pid=pid)


def test_a_question_is_live_until_it_is_answered(tmp_path):
    q = ask(tmp_path)
    assert [item.id for item in queue.live(tmp_path, KEY, now=1001)] == [q.id]
    queue.answer(tmp_path, KEY, q.id, queue.ALLOW, mark=queue.mark_of(q), now=1002)
    assert queue.answer_of(tmp_path, KEY, q.id)["choice"] == "allow"
    assert queue.live(tmp_path, KEY, now=1003) == []


def test_questions_come_oldest_first(tmp_path):
    later = ask(tmp_path, now=1005, session="minor session 1")
    earlier = ask(tmp_path, now=1000)
    assert [q.id for q in queue.live(tmp_path, KEY, now=1010)] == [earlier.id, later.id]


def test_a_question_past_its_deadline_is_withdrawn_not_answerable(tmp_path):
    q = ask(tmp_path, wait=30)
    assert queue.live(tmp_path, KEY, now=1031) == []
    with pytest.raises(queue.QueueRefused, match="withdrawn"):
        queue.answer(tmp_path, KEY, q.id, queue.ALLOW, mark=queue.mark_of(q), now=1031)


def test_an_abandoned_question_is_not_live_and_not_answerable(tmp_path):
    q = ask(tmp_path)
    assert queue.live(tmp_path, KEY, now=1001, alive=lambda pid: False) == []
    with pytest.raises(queue.QueueRefused, match="abandoned"):
        queue.answer(tmp_path, KEY, q.id, queue.ALLOW, mark=queue.mark_of(q), now=1001, alive=lambda pid: False)


def test_a_question_is_answered_once(tmp_path):
    q = ask(tmp_path)
    queue.answer(tmp_path, KEY, q.id, queue.DENY, why="not on main", now=1001)
    with pytest.raises(queue.QueueRefused, match="already closed: deny not on main"):
        queue.answer(tmp_path, KEY, q.id, queue.ALLOW, mark=queue.mark_of(q), now=1002)


def test_an_answer_and_a_withdrawal_cannot_both_count(tmp_path):
    first = ask(tmp_path)
    assert queue.withdraw(tmp_path, KEY, first.id, "no answer in time", now=1540)
    with pytest.raises(queue.QueueRefused):
        queue.answer(tmp_path, KEY, first.id, queue.ALLOW, mark=queue.mark_of(first), now=1001)
    second = ask(tmp_path, now=2000, session="minor session 1")
    queue.answer(tmp_path, KEY, second.id, queue.ALLOW, mark=queue.mark_of(second), now=2001)
    assert queue.withdraw(tmp_path, KEY, second.id, "no answer in time", now=2540) is False
    assert queue.answer_of(tmp_path, KEY, second.id)["choice"] == "allow"


def test_an_unknown_answer_or_question_is_refused(tmp_path):
    q = ask(tmp_path)
    with pytest.raises(queue.QueueRefused, match="not an answer"):
        queue.answer(tmp_path, KEY, q.id, "maybe", now=1001)
    with pytest.raises(queue.QueueRefused, match="no question"):
        queue.answer(tmp_path, KEY, "0000000000000-1", queue.ALLOW, now=1001)


def test_this_process_is_alive():
    assert queue.is_alive(os.getpid())


def test_an_allow_names_the_question_it_allows_by_its_mark(tmp_path):
    """Claude Code's prompt shows the person only an id: the answer carries the mark of what was shown, and a mark of
    anything else - another question, or this one changed since - is refused (security review of 203ac1c)."""
    q = ask(tmp_path)
    with pytest.raises(queue.QueueRefused, match="mark"):
        queue.answer(tmp_path, KEY, q.id, queue.ALLOW, now=1002)
    with pytest.raises(queue.QueueRefused, match="mark"):
        queue.answer(tmp_path, KEY, q.id, queue.SESSION, mark="0000000000", now=1002)
    queue.answer(tmp_path, KEY, q.id, queue.ALLOW, mark=queue.mark_of(q), now=1002)
    assert queue.answer_of(tmp_path, KEY, q.id)["mark"] == queue.mark_of(q)


def test_a_deny_needs_no_mark(tmp_path):
    q = ask(tmp_path)
    queue.answer(tmp_path, KEY, q.id, queue.DENY, why="no", now=1002)


def test_the_same_call_from_another_session_or_tree_is_another_question(tmp_path):
    """Two sessions asking the same command in different trees got one mark, so an allow shown for one could be
    recorded on the other (review of the mark)."""
    a = queue.ask(tmp_path, KEY, session="main session 1", session_id="s1", tool="Bash", tool_input={"command": "rm x"},
                  suggestions=[], wait=540, now=1000, cwd="/w/one")
    b = queue.ask(tmp_path, KEY, session="main session 2", session_id="s2", tool="Bash", tool_input={"command": "rm x"},
                  suggestions=[], wait=540, now=1001, cwd="/w/two")
    assert queue.mark_of(a) != queue.mark_of(b)
    with pytest.raises(queue.QueueRefused, match="mark"):
        queue.answer(tmp_path, KEY, b.id, queue.ALLOW, mark=queue.mark_of(a), now=1002)


@pytest.mark.parametrize("field", ["session_id", "cwd"])
def test_a_question_differing_only_in_one_field_has_another_mark(tmp_path, field):
    base = dict(session="main session 1", session_id="s1", tool="Bash", tool_input={"command": "rm x"},
                suggestions=[], wait=540, cwd="/w/one")
    a = queue.ask(tmp_path, KEY, now=1000, **base)
    b = queue.ask(tmp_path, KEY, now=1001, **{**base, field: base[field] + "-other"})
    assert queue.mark_of(a) != queue.mark_of(b)


def test_closed_questions_older_than_a_day_are_not_kept(tmp_path):
    """Every tool input asked about stayed on disk for ever, secrets included (security review F16)."""
    old = ask(tmp_path, now=1000.0)
    queue.answer(tmp_path, KEY, old.id, queue.DENY, why="no", now=1001.0)
    queue.ask(tmp_path, KEY, session="main session 1", session_id="s", tool="Bash", tool_input={"command": "ls"},
              suggestions=[], wait=540, now=1000.0 + 2 * 86400)
    base = queue.folder(tmp_path, KEY)
    assert not (base / f"q-{old.id}.json").exists() and not (base / f"a-{old.id}.json").exists()


def test_a_question_nobody_answered_is_forgotten_a_day_after_its_deadline(tmp_path):
    old = ask(tmp_path, now=1000.0, wait=540)
    queue.ask(tmp_path, KEY, session="main session 1", session_id="s", tool="Bash", tool_input={"command": "ls"},
              suggestions=[], wait=540, now=1540.0 + 2 * 86400)
    assert not (queue.folder(tmp_path, KEY) / f"q-{old.id}.json").exists()


def test_an_abandoned_question_loses_its_call_when_the_queue_is_next_read(tmp_path):
    """A hook killed while it waited answers nothing and cleans nothing; its question's call goes once its deadline
    passed, the next time anyone reads the queue - not only when another question is asked."""
    import json
    old = ask(tmp_path, now=1000.0, wait=540)
    queue.live(tmp_path, KEY, now=1600.0)
    kept = json.loads((queue.folder(tmp_path, KEY) / f"q-{old.id}.json").read_text(encoding="utf-8"))
    assert kept["tool_input"] == {} and kept["suggestions"] == []


@pytest.mark.parametrize("errno_name", ["EPERM", "ENOTSUP"])
def test_a_filesystem_without_hard_links_still_answers_once(tmp_path, monkeypatch, errno_name):
    """FUSE, SMB and exFAT mounts refuse `os.link` with EPERM or ENOTSUP, not FileExistsError: the answer was a
    traceback (TODO, broker final review). An exclusive create keeps "the first answer wins"."""
    import errno
    def no_links(src, dst):
        raise OSError(getattr(errno, errno_name), "links not supported")
    monkeypatch.setattr(os, "link", no_links)
    q = ask(tmp_path)
    queue.answer(tmp_path, KEY, q.id, queue.DENY, why="no", now=1001)
    assert queue.answer_of(tmp_path, KEY, q.id)["choice"] == queue.DENY
    with pytest.raises(queue.QueueRefused, match="already closed"):
        queue.answer(tmp_path, KEY, q.id, queue.DENY, why="again", now=1002)
    assert not list(queue.folder(tmp_path, KEY).glob(".*.tmp"))


def test_pid_zero_or_less_is_not_alive():
    assert queue.is_alive(0) is False and queue.is_alive(-1) is False


def test_a_failed_write_leaves_no_staged_file_and_old_ones_are_swept(tmp_path, monkeypatch):
    base = queue.folder(tmp_path, KEY)
    base.mkdir(parents=True)
    stale = base / ".q-old-1.tmp"
    stale.write_text("{}", encoding="utf-8")
    os.utime(stale, (1000.0 - queue.KEEP_CLOSED - 10,) * 2)
    def full_disk(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", full_disk)
    with pytest.raises(OSError):
        ask(tmp_path, now=1000.0)
    assert sorted(path.name for path in base.glob(".*.tmp")) == []   # the stale one swept, the new one removed
