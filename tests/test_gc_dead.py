"""Dead seats, gc design 4 e, g and i: a seat whose session is gone is retired when nothing works in its tree, and
asked about - stop or keep, with each process's evidence - when something does. No case is silent."""

import os

import pytest

from flotilla.fleet import helpers
from flotilla.gc import dead, questions
from flotilla.ledger import steering
from ledgerkit import PROFILE, actor, git, make_ledger, repo_with_origin
from test_fleet_leftovers import fake_proc
from test_gc_trees import session

OWNER = "main session 1"


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=PROFILE, live=())
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    with ledger.session() as s:
        s.append(actor(ledger, OWNER), "r1", "reserve", "reserved",
                 fields={"branch": "fleet/main-1", "owner": OWNER, "tree": str(tree)})
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "uptime").write_text("100000.00 0.00\n", encoding="utf-8")
    return root, ledger, tree, proc


def holder(proc, pid, cwd, *, command="node vite", state="S", ppid=1, tty=0, uid=None, utime=0, start=1000,
           cgroup=None):
    """A process in a fake /proc whose stat carries the state, tty and CPU ticks a holder is judged by."""
    more = {} if cgroup is None else {"cgroup": cgroup}
    fake_proc(proc, pid, ppid=ppid, cwd=cwd, command=command, tty=tty, uid=uid, start=start, **more)
    (proc / str(pid) / "stat").write_text(
        f"{pid} ({command.split()[0]}) {state} {ppid} {pid} {pid} {tty} -1 0 0 0 0 0 {utime} 0 0 0 20 0 1 0 {start} "
        "0 0\n", encoding="utf-8")
    with open(proc / str(pid) / "status", "a", encoding="utf-8") as status:
        status.write("VmRSS:\t317440 kB\n")


def seats(ledger, proc, sessions=(), data=None):
    return dead.dead_seats(ledger, list(sessions), proc_root=proc, data=data if data is not None else {})


def test_a_seat_whose_session_is_gone_is_dead(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    found = seats(ledger, proc)
    assert [seat.row.owner for seat in found] == [OWNER] and found[0].holders == []


def test_a_live_seat_is_not_listed(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    assert seats(ledger, proc, [session(OWNER, tmp_path / "elsewhere")]) == []


def test_a_seat_with_a_process_in_its_tree_is_asked_not_retired(tmp_path):
    """Review Focus 1: a running session the census missed for a moment still has its process in the tree."""
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree)
    found = seats(ledger, proc)
    assert len(found[0].holders) == 1 and found[0].holders[0].pid == 4242
    lines = dead.retire_dead(ledger, found, census=lambda: [], caller="test")
    assert tree.exists() and not any("retired" in line for line in lines)
    item = dead.seat_items(found)[0]
    assert item.key == f"seat:{OWNER}" and item.offered == ["stop", "keep"]
    assert any("4242" in line and "node vite" in line for line in item.evidence)


def test_a_zombie_does_not_hold_a_seat(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    entry = proc / "77"
    entry.mkdir()
    (entry / "stat").write_text("77 (node) Z 4000 77 77 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 1000 0 0\n", encoding="utf-8")
    (entry / "status").write_text(f"Name:\tnode\nState:\tZ (zombie)\nUid:\t{os.getuid()}\t0\t0\t0\n",
                                  encoding="utf-8")   # no cwd: a zombie's is gone (measured on Linux, ENOENT)
    found = seats(ledger, proc)
    assert found[0].holders == []
    lines = dead.retire_dead(ledger, found, census=lambda: [], caller="test")
    assert not tree.exists() and lines[0] == "dead seats: 1 retired"


def test_unreadable_processes_ask_retire_or_keep(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    found = seats(ledger, tmp_path / "absent")
    assert found[0].holders is None
    assert dead.retire_dead(ledger, found, census=lambda: [], caller="test") == [] and tree.exists()
    item = dead.seat_items(found)[0]
    assert item.offered == ["retire", "keep"] and "does not show processes" in " ".join(item.evidence)


@pytest.mark.parametrize("kind, said, offered, proposed", [
    ("other user", "not ours to stop", ["keep"], "keep"),
    ("service", "not ours to stop", ["keep"], "keep"),
    ("terminal", "someone may be using it", ["stop", "keep"], "keep"),
    ("busy", "may still be working", ["stop", "keep"], "keep"),
    ("idle", "likely not needed", ["stop", "keep"], "stop"),
    ("uninterruptible", "a stop may not reach it", ["stop", "keep"], "stop"),
])
def test_assessment(tmp_path, kind, said, offered, proposed):
    root, ledger, tree, proc = world(tmp_path)
    more = {"other user": {"uid": os.getuid() + 1}, "service": {"cgroup": "0::/system.slice/vite.service\n"},
            "terminal": {"tty": 34816}, "busy": {"utime": 30 * 100 * 1000}, "uninterruptible": {"state": "D"},
            "idle": {}}[kind]
    holder(proc, 4242, tree, **more)
    item = dead.seat_items(seats(ledger, proc))[0]
    assert said in item.evidence[0] and item.offered == offered and item.proposed == proposed


def test_cpu_since_the_last_look_is_measured(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree, utime=100)
    data = {}
    first = seats(ledger, proc, data=data)[0].holders[0]
    assert first.cpu_since_s is None and data["holders"]
    data["holders"]["4242:1000"]["at"] -= 2 * 86400   # the baseline is two days old
    holder_again = proc / "4242" / "stat"
    holder_again.write_text(holder_again.read_text(encoding="utf-8").replace(" 100 0 0 0 20", " 3100 0 0 0 20"),
                            encoding="utf-8")
    later = seats(ledger, proc, data=data)[0].holders[0]
    assert later.cpu_since_s == pytest.approx(30.0)
    assert "may still be working" in dead.assess([later])


def test_a_seat_answer_is_bound_to_the_processes_shown(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree)
    shown = dead.seat_items(seats(ledger, proc))[0]
    holder(proc, 4343, tree, command="python serve.py", start=2000)
    now = dead.seat_items(seats(ledger, proc))[0]
    assert questions.fingerprint(shown) != questions.fingerprint(now)


def test_accept_all_never_covers_a_stop(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree)
    item = dead.seat_items(seats(ledger, proc))[0]
    other = questions.Item("seat:helper 9", trees=["/x"], proposed="stop", offered=["stop", "keep"],
                           evidence=["likely not needed"], shown=["helper 9", [[1, "5"]]])
    asked = questions.all_questions([item, other], {"answers": {}})
    assert not any(question["id"].startswith(questions.ALL) for question in asked)
    assert all(question["header"] == "Seats" for question in asked)


def test_claimed_work_of_a_gone_owner_is_not_a_dead_seat(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    with ledger.session() as s:
        s.append(actor(ledger, "minor session 2"), "r2", "claim", "claimed",
                 fields={"branch": "feat/x", "owner": "minor session 2", "tree": str(tree)})
    assert [seat.row.owner for seat in seats(ledger, proc)] == [OWNER]


def test_retire_dead_retires_and_removes_the_clean_tree(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    lines = dead.retire_dead(ledger, seats(ledger, proc), census=lambda: [], caller="test")
    assert not tree.exists() and lines[0] == "dead seats: 1 retired" and any(OWNER in line for line in lines)
    assert not [row for row in ledger.rows().values() if row.is_open and row.owner == OWNER]


def test_a_finished_helpers_tree_goes_once_its_session_is_gone(tmp_path):
    """Occasion g."""
    from fleetkit import FakeClaude
    from flotilla.core.storage import LocalLogStore
    from ledgerkit import branch
    fake = FakeClaude()
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "permissions": {"mode": "auto"}}, run=fake,
                         census=fake.census)
    branch(root, "feat/x", "the parent's work")
    from flotilla.ledger import core
    core.claim(ledger, actor(ledger, OWNER), "feat/x")
    raised = helpers.raise_helper(ledger, actor(ledger, OWNER), "feat/x", task="tests", census=fake.census,
                                  store=LocalLogStore(ledger.state_dir / "fleet"), caller="helper raise",
                                  wait=0.1, poll=0.05, sleep=lambda seconds: None)
    helpers.done(ledger, actor(ledger, "helper 1"), summary="done")
    proc = tmp_path / "proc"
    proc.mkdir()
    found = dead.dead_seats(ledger, [session(OWNER, root)], proc_root=proc, data={})
    assert [seat.row.owner for seat in found] == ["helper 1"]
    assert raised.seat.tree.exists()


def test_an_adopted_tree_is_not_judged(tmp_path):
    """Occasion i: the work of a gone owner handed to a live session."""
    root, ledger, tree, proc = world(tmp_path)
    with ledger.session() as s:
        s.append(actor(ledger, "minor session 2"), "r2", "claim", "claimed",
                 fields={"branch": "feat/y", "owner": "minor session 2", "tree": str(tree)})
    heirs = make_ledger(root, tmp_path / "state", profile=PROFILE, live=("orchestrator 1", "main session 3"))
    steering.adopt(heirs, actor(heirs, "orchestrator 1"), "feat/y", to="main session 3")
    live = [session("main session 3", tmp_path / "elsewhere")]
    assert dead.dead_seats(ledger, live, proc_root=proc, data={}) == []
    from flotilla.gc import sweep
    report = sweep.run_pass(ledger, sessions=live, act=False, proc_root=proc)
    assert any("is alive and works in it" in line for line in sweep.lines(report)), sweep.lines(report)


def answer(found, data, value):
    asked = questions.all_questions(found, data)
    return questions.record(found, data, asked[0]["id"], [value])


def killer(proc):
    sent = []

    def kill(pid, signal):
        sent.append(pid)
        import shutil
        shutil.rmtree(proc / str(pid))   # the process exits
    return kill, sent


def test_stop_stops_the_processes_shown_and_retires_the_seat(tmp_path):
    from flotilla.gc import apply
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree)
    data = {"answers": {}}
    found = dead.seat_items(seats(ledger, proc))
    answer(found, data, "stop")
    kill, sent = killer(proc)
    lines, complete = apply.apply(ledger, data, items=dead.seat_items(seats(ledger, proc)), sessions=[],
                                  proc_root=proc, census=lambda: [], kill=kill)
    assert sent == [4242] and not tree.exists() and complete, lines
    assert any("retired main session 1" in line for line in lines)


def test_stop_stops_only_what_was_shown(tmp_path):
    from flotilla.gc import apply
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree)
    data = {"answers": {}}
    answer(dead.seat_items(seats(ledger, proc)), data, "stop")
    holder(proc, 4343, tree, command="python serve.py", start=2000)   # started after the question
    kill, sent = killer(proc)
    now = dead.seat_items(seats(ledger, proc))
    lines, complete = apply.apply(ledger, data, items=now, sessions=[], proc_root=proc, census=lambda: [],
                                  kill=kill)
    assert sent == [] and tree.exists() and not complete
    assert len(questions.all_questions(now, data)) == 1 and "4343" in questions.all_questions(now, data)[0]["question"]


def test_a_process_that_does_not_stop_keeps_the_seat(tmp_path):
    from flotilla.gc import apply
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree)
    data = {"answers": {}}
    answer(dead.seat_items(seats(ledger, proc)), data, "stop")
    lines, complete = apply.apply(ledger, data, items=dead.seat_items(seats(ledger, proc)), sessions=[],
                                  proc_root=proc, census=lambda: [], kill=lambda pid, signal: None, wait=0)
    assert tree.exists() and not complete and any("4242 did not stop" in line for line in lines)


def test_keep_is_not_asked_again_until_the_holders_change(tmp_path):
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree)
    data = {"answers": {}}
    answer(dead.seat_items(seats(ledger, proc)), data, "keep")
    assert questions.all_questions(dead.seat_items(seats(ledger, proc)), data) == []
    holder(proc, 4343, tree, command="python serve.py", start=2000)
    assert len(questions.all_questions(dead.seat_items(seats(ledger, proc)), data)) == 1


def test_retire_answer_for_an_unreadable_machine(tmp_path):
    from flotilla.gc import apply
    root, ledger, tree, proc = world(tmp_path)
    absent = tmp_path / "absent"
    data = {"answers": {}}
    answer(dead.seat_items(seats(ledger, absent)), data, "retire")
    lines, complete = apply.apply(ledger, data, items=dead.seat_items(seats(ledger, absent)), sessions=[],
                                  proc_root=absent, census=lambda: [])
    assert not tree.exists() and complete, lines


@pytest.mark.parametrize("value, more", [("stop", {"uid": os.getuid() + 1}), ("junk", {}), ("park", {})])
def test_an_answer_the_question_did_not_offer_does_nothing(tmp_path, value, more):
    """Security review of b7aad03: the answer file is any session's to write, so a recorded value is carried out
    only when the question offered it."""
    from flotilla.gc import apply
    root, ledger, tree, proc = world(tmp_path)
    holder(proc, 4242, tree, **more)
    items = dead.seat_items(seats(ledger, proc))
    data = {"answers": {items[0].key: {"value": value, "seen": questions.fingerprint(items[0])}}}
    kill, sent = killer(proc)
    lines, complete = apply.apply(ledger, data, items=items, sessions=[], proc_root=proc, census=lambda: [],
                                  kill=kill)
    assert sent == [] and tree.exists() and not complete
    assert any("not an answer" in line for line in lines) and items[0].key not in data["answers"]
