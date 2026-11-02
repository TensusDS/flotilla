import io
import sys
from contextlib import redirect_stdout

from flotilla import cli
from flotilla.onboard.tomlw import render_toml
from flotilla.posts import install_templates
from ledgerkit import commit, git, repo_with_origin

PROFILE = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"},
           "lane": {"run_patterns": [r"\bno-such-runner-in-flotilla-tests\b"]}}


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def onboarded(tmp_path, monkeypatch, profile=PROFILE):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text(render_toml(profile), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


def test_the_lane_is_shown_empty_on_a_quiet_machine(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and "capacity: 1" in out and "held: nobody" in out and "foreign run: ok" in out


def test_a_lane_taken_by_hand_refuses_the_next_taker_until_released(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "take", "--note", "measuring a race", "--root", str(root), "--as", "main session 1")
    assert code == 0 and "booking b1" in out
    code, out = run_cli("lane", "take", "--root", str(root), "--as", "review session 1")
    assert code == 2 and "held by main session 1 (measuring a race)" in out
    code, out = run_cli("lane", "release", "--root", str(root), "--as", "main session 1")
    assert code == 0 and "released b1" in out
    assert run_cli("lane", "take", "--root", str(root), "--as", "review session 1")[0] == 0


def test_a_run_for_a_branch_records_its_result_on_the_row(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    git(root, "checkout", "-q", "-b", "feat/x")
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("lane", "run", "--for", "feat/x", "--tree", str(root), "--as", "main session 1", "--",
                        sys.executable, "-c", "print('===== 3 passed in 0.10s =====')")
    assert code == 0 and "lane run: green - 3 passed in 0.10s" in out
    code, out = run_cli("status", "--root", str(root))
    assert "last run: green: 3 passed in 0.10s" in out
    assert "held: nobody" in run_cli("lane", "--root", str(root))[1]


def test_a_killed_run_exits_non_zero_and_says_so(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--tree", str(root), "--as", "main session 1", "--", sys.executable, "-c",
                        "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    assert code == 137 and "killed by signal 9 - no verdict" in out


def test_a_run_waits_its_turn_and_is_refused_when_the_wait_runs_out(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    run_cli("lane", "take", "--note", "hands off", "--root", str(root), "--as", "main session 1")
    code, out = run_cli("lane", "run", "--wait", "0", "--tree", str(root), "--as", "review session 1", "--",
                        sys.executable, "-c", "print('never')")
    assert code == 2 and "held by main session 1 (hands off)" in out and "never" not in out


def test_a_run_needs_a_command(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--tree", str(root), "--as", "main session 1")
    assert code == 2 and "name the command after --" in out


def test_a_receipt_takes_the_lane(tmp_path, monkeypatch):
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})   # something to run: it queues
    run_cli("lane", "take", "--note", "hands off", "--root", str(root), "--as", "main session 1")
    code, out = run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root), "--lane-wait", "0")
    assert code == 2 and "held by main session 1 (hands off)" in out


def test_sweep_says_when_there_is_nothing_to_sweep(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "sweep", "--root", str(root))
    assert code == 0 and "nothing to sweep" in out


def test_a_run_is_recorded_over_the_revision_it_started_from(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("work", "claim", "main", "--root", str(root), "--as", "main session 1")[0] == 0
    before = git(root, "rev-parse", "HEAD")
    script = "import subprocess; subprocess.run(['git', '-c', 'user.email=t@x', '-c', 'user.name=t', 'commit', " \
             "'-q', '--allow-empty', '-m', 'during the run']); print('1 passed')"
    run_cli("lane", "run", "--for", "main", "--tree", str(root), "--as", "main session 1", "--", sys.executable, "-c",
            script)
    code, out = run_cli("work", "show", "main", "--root", str(root))
    assert f"over {before[:7]}" in run_cli("status", "--root", str(root))[1]


def test_a_run_over_uncommitted_work_says_so(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("work", "claim", "main", "--root", str(root), "--as", "main session 1")[0] == 0
    (root / "draft.txt").write_text("unsaved\n", encoding="utf-8")
    run_cli("lane", "run", "--for", "main", "--tree", str(root), "--as", "main session 1", "--", sys.executable, "-c",
            "print('1 passed')")
    assert "plus uncommitted changes" in run_cli("status", "--root", str(root))[1]


def test_a_run_from_another_branch_is_not_recorded(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("lane", "run", "--for", "feat/x", "--tree", str(root), "--as", "main session 1", "--",
                        sys.executable, "-c", "print('1 passed')")
    assert code == 0 and "not recorded" in out and "is on main, not feat/x" in out
    assert "last run" not in run_cli("status", "--root", str(root))[1]


def test_release_ends_a_waiter_named_by_its_booking(tmp_path, monkeypatch):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    root = onboarded(tmp_path, monkeypatch)
    stuck = book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine()).enqueue(
        "main session 2", "waiting", pid=None)
    code, out = run_cli("lane", "release", "--booking", stuck.id, "--root", str(root), "--as", "orchestrator 1")
    assert code == 0 and f"ended {stuck.id}" in out


def test_lane_run_takes_a_ceiling(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--max", "1", "--tree", str(root), "--as", "main session 1", "--",
                        sys.executable, "-c", "import time; time.sleep(30)")
    assert code != 0 and "ceiling of 1 s" in out


def test_the_default_ceiling_follows_what_the_tiers_were_measured_at(tmp_path, monkeypatch):
    """For a receipt, whose commands are the measured tiers: ten times the slowest, never under ten minutes. For any
    other run in the lane, never under half an hour - a measured project must not get less room than an unmeasured
    one (review of 0.6.2, I5). The profile may name its own."""
    from flotilla.lane import commands
    assert commands.ceiling({}, {}) == 1800
    assert commands.ceiling({}, {"node": 34.0}, tiers=True) == 600
    assert commands.ceiling({}, {"node": 34.0}) == 1800
    assert commands.ceiling({}, {"node": 34.0, "e2e": 300.0}) == 3000
    assert commands.ceiling({}, {"node": 34.0, "e2e": 300.0}, tiers=True) == 3000
    assert commands.ceiling({"lane": {"max_run_seconds": 90}}, {"node": 34.0}) == 90


def test_lane_stop_stops_the_holders_own_run(tmp_path, monkeypatch):
    """Worldcore field test W22: to free the lane a seat ran `pkill -f` and killed itself first. Flotilla recorded
    the run's pid; `lane stop` uses it."""
    import os
    import subprocess
    import time
    root = onboarded(tmp_path, monkeypatch)
    env = {**os.environ, "FLOTILLA_STATE_DIR": str(tmp_path / "state"), "FLOTILLA_NO_CENSUS": "1"}
    cli_path = os.path.join(os.path.dirname(__file__), "..", "bin", "flotilla")
    runner = subprocess.Popen([sys.executable, cli_path, "lane", "run", "--tree", str(root), "--as", "main session 7",
                               "--", sys.executable, "-c", "import time; time.sleep(60)"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if "main session 7" in run_cli("lane", "--root", str(root))[1]:
            break
        time.sleep(0.1)
    code, out = run_cli("lane", "stop", "--root", str(root), "--as", "main session 8")
    assert code == 2 and "main session 8" in out          # not someone else's run
    code, out = run_cli("lane", "stop", "--root", str(root), "--as", "main session 7")
    assert code == 0 and "stopped" in out, out
    said, _ = runner.communicate(timeout=20)
    assert runner.returncode != 0 and "killed" in said
    assert "held: nobody" in run_cli("lane", "--root", str(root))[1]


def test_lane_stop_does_not_take_another_sessions_word(tmp_path, monkeypatch):
    """Review of 0.6.2, I2: the lane fell back to the name given with --as even when the census said the caller is
    another session, so `lane stop --as <peer>` stopped the peer's run (and `release` released it)."""
    from flotilla.ledger.errors import ActorMismatch
    from flotilla.lane import commands
    root = onboarded(tmp_path, monkeypatch)

    def refuse(posts, as_name=None, **kw):
        raise ActorMismatch(f"this process runs inside `seat A`, which cannot act as `{as_name}`")
    monkeypatch.setattr(commands, "resolve_actor", refuse)
    for action in (["stop"], ["release"]):
        code, out = run_cli("lane", *action, "--root", str(root), "--as", "seat B")
        assert code == 2 and "cannot act as" in out


def test_lane_status_is_the_bare_lane(tmp_path, monkeypatch):
    """Twosuns field test of 0.6.7, W7: a judge waiting for the lane guessed `flotilla lane status` and met argparse's
    "invalid choice"; the status is the bare `flotilla lane`."""
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "status", "--root", str(root))
    assert code == 0 and "capacity: 1" in out and "held: nobody" in out, out


def test_a_peers_booking_note_cannot_forge_lines_or_move_the_cursor(tmp_path, monkeypatch):
    """Scan of 0.7.0, F2: a note (or `--for`) is text another session writes into the shared booking log, and the
    lane printed it as it was - a newline forged status lines, an escape rewrote the terminal. Every booking field a
    caller wrote is made visible where the log is read, so every place that prints it is covered."""
    root = onboarded(tmp_path, monkeypatch)
    note = "tests\n  waiting: nobody\nmachine:\x1b[2J\x1b]0;owned\x07"
    code, _ = run_cli("lane", "take", "--note", note, "--root", str(root), "--as", "main session 1")
    assert code == 0
    _, out = run_cli("lane", "--root", str(root))
    held = [line for line in out.splitlines() if "main session 1" in line]
    assert len(held) == 1 and "\\n  waiting: nobody" in held[0]
    assert "\x1b" not in out and "\x07" not in out
    code, out = run_cli("lane", "take", "--root", str(root), "--as", "review session 1")   # the refusal names it too
    refused = [line for line in out.splitlines() if "held by main session 1" in line]
    assert code == 2 and "\x1b" not in out and len(refused) == 1 and "\\n  waiting: nobody" in refused[0]


def test_a_name_with_a_hidden_character_still_releases_its_own_booking(tmp_path, monkeypatch):
    """Review of the scan fixes of 0.7.0, M1: booking names are made visible where the log is read, and release
    compared that with the raw name - a name with a tab or a joiner could no longer release its own booking."""
    root = onboarded(tmp_path, monkeypatch)
    name = "main\tsession 1"
    assert run_cli("lane", "take", "--root", str(root), "--as", name)[0] == 0
    code, out = run_cli("lane", "release", "--root", str(root), "--as", name)
    assert code == 0 and "released b1" in out


def _journal(tmp_path):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    return book.fold(LocalLogStore(tmp_path / "state" / "lane").read(book.KEY).records)


def test_a_lane_run_records_its_command_and_measurement(tmp_path, monkeypatch):
    from flotilla.lane import book
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--tree", str(root), "--", sys.executable, "-c", "print('1 passed')")
    assert code == 0, out
    [item] = [b for b in _journal(tmp_path).values() if b.command]
    assert sys.executable in item.command and item.ladder[-1].startswith("project:")
    assert item.verdict == "green" and item.seconds is not None and item.rule == book.RULE


def test_a_receipt_records_the_tiers_it_ran(tmp_path, monkeypatch):
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})
    code, out = run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root))
    assert code == 0, out
    [item] = [b for b in _journal(tmp_path).values() if b.note == "handover receipt"]
    assert item.will_run == ["unit"] and item.ladder[0].startswith("receipt:")
    assert [tier["name"] for tier in item.ran] == ["unit"]
    assert all("seconds" in tier and "peak_mb" in tier for tier in item.ran)
    assert item.verdict == "green" and item.seconds is not None


def test_a_nested_run_is_not_measured_twice(tmp_path, monkeypatch):
    """A `lane run` inside a booking is part of it: the outer booking is measured, the inner never books."""
    from pathlib import Path
    root = onboarded(tmp_path, monkeypatch)
    flotilla = Path(__file__).resolve().parent.parent / "bin" / "flotilla"
    inner = f"{flotilla} lane run --tree {root} --wait 5 -- {sys.executable} -c pass"   # a broken rule waits, not hangs
    code, out = run_cli("lane", "run", "--tree", str(root), "--", "sh", "-c", inner)
    assert code == 0, out
    journal = _journal(tmp_path)
    [outer] = [b for b in journal.values() if not b.inside]
    nested = [b for b in journal.values() if b.inside]
    assert outer.seconds is not None and all(b.inside == outer.id and b.state == "released" for b in nested)
    assert len(nested) == 1   # the inner run is recorded beside its parent, never booked a second time


def test_the_lane_shows_each_bookings_estimate(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    for _ in range(3):
        run_cli("lane", "run", "--tree", str(root), "--", sys.executable, "-c", "print('1 passed')")
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book

    class Alive:
        def alive(self, pid, mark):
            return True
    lanes = book.Book(LocalLogStore(tmp_path / "state" / "lane"), Alive())
    known = next(b for b in lanes.bookings().values() if b.command)
    lanes.enqueue("main session 2", "again", pid=None, mark="", command=known.command, ladder=known.ladder,
                  project=known.project)
    code, out = run_cli("lane", "--root", str(root))
    waiting = out.split("waiting:", 1)[1]
    assert "estimate:" in waiting and "3 runs (exact match)" in waiting, out


def test_an_older_booking_says_it_has_no_estimate():
    from flotilla.lane import book, commands
    assert commands.describe_estimate(book.Booking(id="b1"), {}) == "estimate: none (an older flotilla booked it)"


def test_an_estimate_names_unknown_parts():
    from flotilla.lane import book, commands
    item = book.Booking(id="b2", ladder=["exact:x", "project:q"], project="q")
    assert commands.describe_estimate(item, {}) == "estimate: ? s, 4 cores, 2.0 GB (fixed prior: 4 cores, 2 GB)"


def test_a_booking_taken_by_hand_says_so():
    from flotilla.lane import book, commands
    assert commands.describe_estimate(book.Booking(id="b1", rule=1), {}) == "estimate: none (taken by hand)"


def test_an_interrupted_run_records_that_it_was_killed(tmp_path, monkeypatch):
    """Review of stage 1: a run stopped by Ctrl-C or `lane stop` was released with nothing - it held the lane."""
    root = onboarded(tmp_path, monkeypatch)
    from flotilla.lane import run as runner

    def stopped(*a, **kw):
        raise SystemExit(143)
    monkeypatch.setattr(runner, "execute", stopped)
    run_cli("lane", "run", "--tree", str(root), "--", sys.executable, "-c", "pass")
    [item] = list(_journal(tmp_path).values())
    assert item.verdict == "killed" and item.seconds is not None


def test_an_interrupted_receipt_records_that_it_was_killed(tmp_path, monkeypatch):
    import pytest
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})
    from flotilla.ledger import receipts

    def stopped(*a, **kw):
        raise SystemExit(143)
    monkeypatch.setattr(receipts, "run_receipt", stopped)
    with pytest.raises(SystemExit):
        run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root))
    [item] = list(_journal(tmp_path).values())
    assert item.verdict == "killed" and item.seconds is not None


def test_a_receipt_inside_a_lane_run_records_its_tiers(tmp_path, monkeypatch):
    """Review of stage 1: a receipt nested in a booking wrote nothing, so its tiers never learned."""
    from pathlib import Path
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})
    flotilla = Path(__file__).resolve().parent.parent / "bin" / "flotilla"
    inner = f"{flotilla} receipt run --purpose handover --tree {root} --lane-wait 5"
    code, out = run_cli("lane", "run", "--tree", str(root), "--", "sh", "-c", inner)
    assert code == 0, out
    journal = _journal(tmp_path)
    [outer] = [b for b in journal.values() if not b.inside]
    [nested] = [b for b in journal.values() if b.inside]
    assert nested.inside == outer.id and nested.state == "released"
    assert [t["name"] for t in nested.ran] == ["unit"] and nested.ladder[0].startswith("receipt:")


def test_a_receipt_refused_mid_way_says_refused_not_killed(tmp_path, monkeypatch):
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})
    from flotilla.ledger import receipts

    def refused(*a, **kw):
        raise receipts.ReceiptRefused("the tree changed while its tiers ran")
    monkeypatch.setattr(receipts, "run_receipt", refused)
    run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root))
    [item] = list(_journal(tmp_path).values())
    assert item.verdict == "refused"


def _lane_with(monkeypatch, reading, cwds):
    from flotilla.lane import commands
    from flotilla.lane.procs import ProcessTable
    monkeypatch.setattr(commands, "_reader", lambda *args, **kwargs: (lambda: reading))
    monkeypatch.setattr(ProcessTable, "cwd", lambda self, pid: cwds.get(pid))


def test_foreign_runs_are_named_where_the_slots_are(tmp_path, monkeypatch):
    """Void-lanes-online, 2026-11-01: two unbooked suites held both slots, and `flotilla lane` said `held: nobody`
    while a waiter waited - the reader saw nothing in the way. A foreign run is shown where the slots are."""
    from flotilla.lane.machine import Answer, Reading
    from flotilla.lane.procs import Proc
    root = onboarded(tmp_path, monkeypatch)
    busy = [Proc(901, 1, "python -m pytest tests/"), Proc(902, 1, "cargo test\x1b[2J")]
    reading = Reading([Answer("foreign run", True, "2 unbooked run(s) computing"), Answer("ci", False, "none")],
                      busy, [Proc(903, 1, "pytest --collect-only")])
    _lane_with(monkeypatch, reading, {901: "/work/game", 903: "/work/site"})
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and "held: nobody" not in out
    head = out.split("waiting:")[0]
    assert "foreign run pid 901 `python -m pytest tests/` in /work/game (takes a slot, no booking)" in head
    assert "foreign run pid 902 " in head and "in an unknown directory" in head and "\x1b" not in out
    assert "pid 903" not in head   # idle: it takes no slot
    machine = out.split("machine:")[1]
    assert all(f"pid {pid}" in machine for pid in (901, 902, 903)) and "/work/site" in machine


def test_a_waiter_says_why_it_waits(tmp_path, monkeypatch):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("lane", "take", "--note", "hands off", "--root", str(root), "--as", "main session 1")[0] == 0
    book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine()).enqueue(
        "review session 1", "suite", pid=None)
    _, out = run_cli("lane", "--root", str(root))
    waiter = [line for line in out.splitlines() if "review session 1" in line]
    assert len(waiter) == 1 and "waits because: held by main session 1 (hands off)" in waiter[0], out


def test_showing_the_lane_sweeps_dead_bookings(tmp_path, monkeypatch):
    import subprocess
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    root = onboarded(tmp_path, monkeypatch)
    ended = subprocess.Popen([sys.executable, "-c", "pass"])
    ended.wait()
    dead = book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine()).enqueue(
        "main session 2", "waiting", pid=ended.pid, mark="gone")
    _, out = run_cli("lane", "--root", str(root))
    assert out.splitlines()[0] == f"swept 1 booking(s) whose process is gone: {dead.id}", out
    assert "waiting: nobody" in out


def test_the_lane_reads_its_journal_the_same_number_of_times_however_many_wait(tmp_path, monkeypatch):
    """Final review of 0.24.1, I3: each waiter's reason re-read the whole journal twice."""
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    root = onboarded(tmp_path, monkeypatch)
    lanes = book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine())
    folds = []
    real = book.fold
    monkeypatch.setattr(book, "fold", lambda *a, **k: folds.append(1) or real(*a, **k))
    lanes.enqueue("main session 2", "one", pid=None)
    run_cli("lane", "--root", str(root))   # the first look also does one-off work; count from the second
    folds.clear()
    run_cli("lane", "--root", str(root))
    one = len(folds)
    for n in range(3):
        lanes.enqueue(f"main session {n + 3}", "more", pid=None)
    folds.clear()
    run_cli("lane", "--root", str(root))
    assert len(folds) == one


def test_showing_the_lane_keeps_a_booking_whose_process_still_exists(tmp_path, monkeypatch):
    """Final review of 0.24.1, M4: a look that cannot confirm a process (a failed `ps`, a start mark that differs)
    must not release a live holder for good; only a process that does not exist is swept when the lane is shown."""
    import os
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    root = onboarded(tmp_path, monkeypatch)
    doubtful = book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine()).enqueue(
        "main session 2", "waiting", pid=os.getpid(), mark="not-its-mark")
    _, out = run_cli("lane", "--root", str(root))
    assert "swept" not in out.splitlines()[0] and f"{doubtful.id} main session 2" in out


def test_a_lane_that_cannot_be_swept_is_still_shown(tmp_path, monkeypatch):
    from flotilla.lane import book
    root = onboarded(tmp_path, monkeypatch)

    def unwritable(self, **kwargs):
        raise OSError("read-only file system")
    monkeypatch.setattr(book.Book, "sweep", unwritable)
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and "could not sweep" in out and "capacity:" in out



def _machine(tmp_path, **keys):
    from flotilla.onboard.machine import MACHINE_FILE
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / MACHINE_FILE).write_text(render_toml(keys), encoding="utf-8")


def _queue(tmp_path, *people):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    lanes = book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine())
    return [lanes.enqueue(who, "suite", pid=None, project=project) for who, project in people]


def test_the_lane_shows_its_mode_and_each_projects_waiting(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    _machine(tmp_path, lane_share="fair")
    _queue(tmp_path, ("twosuns main 1", "twosuns"), ("twosuns main 2", "twosuns"), ("void main 1", "void"))
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and "share: fair (machine.toml lane_share)" in out, out
    assert "project twosuns: 2 waiting, holds 0, last turn not in 24 h" in out
    assert "project void: 1 waiting, holds 0, last turn not in 24 h" in out


def test_a_waiter_says_which_project_goes_first(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    _machine(tmp_path, lane_share="fair")
    _queue(tmp_path, ("twosuns main 1", "twosuns"), ("void main 1", "void"), ("twosuns main 2", "twosuns"))
    _, out = run_cli("lane", "--root", str(root))
    [line] = [line for line in out.splitlines() if "twosuns main 2" in line]
    assert "`twosuns` goes first" in line or "`void` goes first" in line, line


def test_a_share_not_understood_reads_as_fifo_and_says_so(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    _machine(tmp_path, lane_share="round-robin")
    _, out = run_cli("lane", "--root", str(root))
    assert "share: fifo" in out and "'round-robin' is not understood" in out, out


def test_a_project_name_is_shown_as_data(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    _machine(tmp_path, lane_share="fair")
    _queue(tmp_path, ("main 1", "evil\x1b[2J\nproject twosuns: 0 waiting"))
    _, out = run_cli("lane", "--root", str(root))
    assert "\x1b" not in out and "\\x1b[2J\\nproject twosuns" in out, out



def test_a_hand_take_books_under_its_project(tmp_path, monkeypatch):
    """Final review of 0.25.0, I2: `lane take` booked with no project, so under fair every hand take of every project
    shared one turn, and it went first."""
    from flotilla.core import repo
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("lane", "take", "--note", "a race", "--root", str(root), "--as", "main session 1")[0] == 0
    [item] = _journal(tmp_path).values()
    assert item.project == repo.identify(root).key
