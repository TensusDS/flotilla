import io
import sys
from contextlib import redirect_stdout

import pytest

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


def one_run_at_a_time(tmp_path):
    """machine.toml `lane_max_runs = 1`: the lane admits by memory, so a refusal by number needs the cap (0.30)."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "machine.toml").write_text("lane_max_runs = 1\n", encoding="utf-8")


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
    assert code == 0 and "held: nobody" in out and "foreign run: ok" in out


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
    one_run_at_a_time(tmp_path)   # beside a take by hand a light run would start in the light reserve
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
    one_run_at_a_time(tmp_path)
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
    """Beta field test W22: to free the lane a seat ran `pkill -f` and killed itself first. Flotilla recorded
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
    """Alpha field test of 0.6.7, W7: a judge waiting for the lane guessed `flotilla lane status` and met argparse's
    "invalid choice"; the status is the bare `flotilla lane`."""
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "status", "--root", str(root))
    assert code == 0 and "cores shared by the kernel" in out and "held: nobody" in out, out


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
    """Gamma, 2026-11-01: two unbooked suites held both slots, and `flotilla lane` said `held: nobody`
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
    assert "foreign run pid 901 `python -m pytest tests/` in /work/game (foreign: its memory is simply not free)" \
        in head   # 0.30: a foreign run takes no slot; its memory is simply not free
    assert "foreign run pid 902 " in head and "in an unknown directory" in head and "\x1b" not in out
    assert "pid 903" not in head   # idle: it takes no slot
    machine = out.split("\nmachine:\n")[1]
    assert all(f"pid {pid}" in machine for pid in (901, 902, 903)) and "/work/site" in machine


def test_a_waiter_says_why_it_waits(tmp_path, monkeypatch):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    root = onboarded(tmp_path, monkeypatch)
    one_run_at_a_time(tmp_path)   # 0.30: beside a take by hand a light run would start in the light reserve
    assert run_cli("lane", "take", "--note", "hands off", "--root", str(root), "--as", "main session 1")[0] == 0
    book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine()).enqueue(
        "review session 1", "suite", pid=None)
    _, out = run_cli("lane", "--root", str(root))
    waiter = [line for line in out.splitlines() if "review session 1" in line]
    assert len(waiter) == 1 and "waits because: runs at once: 1 of 1" in waiter[0], out
    assert "held by main session 1 (hands off)" in waiter[0], out


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
    assert out.splitlines()[1] == f"swept 1 booking(s) whose process is gone: {dead.id}", out   # after the daemon
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
    assert "swept" not in out.splitlines()[0]
    assert any(line.startswith(f"  {doubtful.id} ") and "main session 2" in line for line in out.splitlines()), out


def test_a_lane_that_cannot_be_swept_is_still_shown(tmp_path, monkeypatch):
    from flotilla.lane import book
    root = onboarded(tmp_path, monkeypatch)

    def unwritable(self, **kwargs):
        raise OSError("read-only file system")
    monkeypatch.setattr(book.Book, "sweep", unwritable)
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and "could not sweep" in out and "machine:" in out



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
    _queue(tmp_path, ("alpha main 1", "alpha"), ("alpha main 2", "alpha"), ("void main 1", "void"))
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and "share: fair (machine.toml lane_share)" in out, out
    assert "project alpha: 2 waiting, holds 0, last turn not in 24 h" in out
    assert "project void: 1 waiting, holds 0, last turn not in 24 h" in out


def test_a_waiter_says_which_project_goes_first(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    _machine(tmp_path, lane_share="fair")
    _queue(tmp_path, ("alpha main 1", "alpha"), ("void main 1", "void"), ("alpha main 2", "alpha"))
    _, out = run_cli("lane", "--root", str(root))
    [line] = [line for line in out.splitlines() if "alpha main 2" in line]
    assert "waits because:" in line and "P 0." in line, line   # 0.30: the fair share breaks ties of priority


def test_a_share_not_understood_reads_as_fifo_and_says_so(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    _machine(tmp_path, lane_share="round-robin")
    _, out = run_cli("lane", "--root", str(root))
    assert "share: fifo" in out and "'round-robin' is not understood" in out, out


def test_a_project_name_is_shown_as_data(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    _machine(tmp_path, lane_share="fair")
    _queue(tmp_path, ("main 1", "evil\x1b[2J\nproject alpha: 0 waiting"))
    _, out = run_cli("lane", "--root", str(root))
    assert "\x1b" not in out and "\\x1b[2J\\nproject alpha" in out, out



def test_a_hand_take_books_under_its_project(tmp_path, monkeypatch):
    """Final review of 0.25.0, I2: `lane take` booked with no project, so under fair every hand take of every project
    shared one turn, and it went first."""
    from flotilla.core import repo
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("lane", "take", "--note", "a race", "--root", str(root), "--as", "main session 1")[0] == 0
    [item] = _journal(tmp_path).values()
    assert item.project == repo.identify(root).key



@pytest.mark.parametrize("keys, factor", [({}, 1.5), ({"lane_cpu_factor": 2.0}, 2.0), ({"lane_cpu_factor": 1}, 1.0),
                                          ({"lane_cpu_factor": 9.0}, 1.5), ({"lane_cpu_factor": "x"}, 1.5),
                                          ({"lane_cpu_factor": True}, 1.5)])
def test_cpu_factor_reads_bounds(tmp_path, monkeypatch, keys, factor):
    from flotilla.lane import commands as lane_commands
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    _machine(tmp_path, **keys)
    assert lane_commands.cpu_factor() == factor


def test_the_room_counts_foreign_runs_and_reads_memory_less_the_floor(tmp_path, monkeypatch):
    from flotilla.core import resources
    from flotilla.lane import commands as lane_commands
    from flotilla.lane.machine import Reading
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    _machine(tmp_path)
    monkeypatch.setattr(resources, "available_mb", lambda **k: 9000)
    room = lane_commands.room_for({"lane": {"memory_floor_mb": 1500}})(Reading([], ["a", "b"], []))
    assert room.foreign == 2 and room.memory_left() == 7500 and room.factor == 1.5 and room.cpus >= 1
    monkeypatch.setattr(resources, "available_mb", lambda **k: None)
    assert room.memory_left() is None



# -- light and heavy on screen (lane light runs design, section 5) ---------------------------------------------------

def _lane_book(tmp_path):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    return book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine())


def _taught(lanes, ladder, seconds, cores, peak_mb):
    for _ in range(3):
        item = lanes.enqueue("seed", "", pid=None, ladder=ladder, command="seed")
        lanes.grant(item.id, slots=99)
        lanes.release(item.id, measured={"seconds": seconds, "peak_mb": peak_mb, "cores": cores, "verdict": "green"})


def test_status_marks_light_and_heavy_and_prints_the_heavy_room(tmp_path, monkeypatch):
    from flotilla.core import resources
    from flotilla.lane import book
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr(resources, "available_mb", lambda **k: 9000)
    lanes = _lane_book(tmp_path)
    _taught(lanes, ["exact:receipt"], 660.0, 7.0, 1700)
    _taught(lanes, ["exact:file"], 4.0, 1.0, 300)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: 8000)
    import os   # 0.30: a run has a process; a booking with none is a take by hand, the heavy room whole
    heavy = lanes.enqueue("main session 1", "receipt", pid=os.getpid(), ladder=["exact:receipt"], command="r")
    assert lanes.grant(heavy.id, slots=1, room=room)
    light = lanes.enqueue("main session 2", "one file", pid=os.getpid(), ladder=["exact:file"], command="f")
    assert lanes.grant(light.id, slots=1, room=room)
    lanes.enqueue("main session 3", "receipt", pid=None, ladder=["exact:receipt"], command="r")
    lanes.enqueue("main session 4", "receipt", pid=None, ladder=["exact:receipt"], command="r")
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0, out
    assert "heavy runs: " in out and "(light reserve 20 %)" in out, out   # 0.30: no light budget, no heavy line
    assert f"  {heavy.id} heavy main session 1" in out and f"  {light.id} light main session 2" in out, out
    assert out.count("waits because: ") == 2, out


def test_heavy_starts_serve_the_heavy_line_in_order():
    import datetime as dt
    from flotilla.lane import book, estimate
    now = dt.datetime(2026, 11, 4, 10, 0, tzinfo=dt.timezone.utc)
    sample = estimate.Sample(600.0, 1000, 4.0, 0.5, "green", "")
    hist = {"exact:r": [sample] * 3}
    held = book.Booking(id="b1", state=book.HELD, ladder=["exact:r"], since=(now - dt.timedelta(minutes=4)).isoformat(),
                        path="fifo", rule=3)
    one = book.Booking(id="b2", state=book.WAITING, ladder=["exact:r"], rule=3)
    two = book.Booking(id="b3", state=book.WAITING, ladder=["exact:new"], rule=3)
    three = book.Booking(id="b4", state=book.WAITING, ladder=["exact:r"], rule=3)
    found = {item.id: item for item in (held, one, two, three)}
    starts = book.heavy_starts(found, hist, slots=1, now=now, live=lambda item: True, ceiling=1800)
    assert starts == {"b2": now + dt.timedelta(minutes=6), "b3": now + dt.timedelta(minutes=16),
                      "b4": now + dt.timedelta(minutes=46)}



def test_the_room_counts_the_cores_this_process_may_use(tmp_path, monkeypatch):
    import os as os_module
    from flotilla.lane import commands as lane_commands
    from flotilla.lane.machine import Reading
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    _machine(tmp_path)
    monkeypatch.setattr(os_module, "sched_getaffinity", lambda pid: {0, 1, 2}, raising=False)
    assert lane_commands.room_for({})(Reading([], [], [])).cpus == 3


def test_settings_fall_back_to_their_defaults(monkeypatch):
    from flotilla.lane import commands
    for bad in ({"lane_light_reserve": 2}, {"lane_light_reserve": True}, {"lane_max_runs": 0},
                {"lane_immortal_minutes": "15"}, {"lane_max_runs": 65}, {}):
        monkeypatch.setattr(commands, "_machine", lambda bad=bad: bad)
        assert (commands.light_reserve(), commands.max_runs(), commands.immortal_minutes()) == (0.2, 10, 15)
    monkeypatch.setattr(commands, "_machine", lambda: {"lane_light_reserve": 0.3, "lane_max_runs": 4,
                                                       "lane_immortal_minutes": 30})
    assert (commands.light_reserve(), commands.max_runs(), commands.immortal_minutes()) == (0.3, 4, 30)


def test_lane_freeze_false_turns_freezing_off(monkeypatch):
    from flotilla.lane import commands
    for setting, on in (({"lane_freeze": False}, False), ({"lane_freeze": "false"}, True), ({}, True),
                        ({"lane_freeze": True}, True)):
        monkeypatch.setattr(commands, "_machine", lambda setting=setting: setting)
        assert commands.freeze_on() is on


def test_a_push_or_handover_receipt_books_by_its_purpose(tmp_path):
    from flotilla.lane import commands
    assert commands.kind_of("push", "main session 1", tmp_path) == "push"
    assert commands.kind_of("handover", "main session 1", tmp_path) == "handover"
    assert commands.kind_of("", "main session 1", tmp_path) == "run"


def test_a_reviewer_session_books_as_review(tmp_path, monkeypatch):
    from flotilla.lane import commands
    root = onboarded(tmp_path, monkeypatch)
    assert commands.kind_of("", "review session 3", root) == "review"
    assert commands.kind_of("", "main session 3", root) == "run"


def test_a_freeze_false_tier_pins_the_receipts_booking(tmp_path, monkeypatch):
    from flotilla.lane import commands
    tiers = [{"name": "unit", "command": "true", "required_for": ["handover"]},
             {"name": "e2e", "command": "true", "required_for": ["handover"], "freeze": False}]
    profile = {**PROFILE, "tests": {"tier": tiers}}
    assert commands.pinned_for(profile, "handover", ["unit", "e2e"])
    assert not commands.pinned_for(profile, "handover", ["unit"])


def test_a_lane_run_books_with_its_purpose(tmp_path, monkeypatch):   # no daemon in tests: the fallback admits it
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    root = onboarded(tmp_path, monkeypatch)
    code, _out = run_cli("lane", "run", "--tree", str(root), "--as", "review session 2", "--", sys.executable, "-c",
                         "pass")
    assert code == 0
    found = book.Book(LocalLogStore(tmp_path / "state" / "lane"), None).bookings()
    item = max(found.values(), key=lambda item: book.number(item.id) or 0)
    assert item.kind == "review" and item.path == "fallback: empty lane" and item.asked and item.floor_mb



def test_the_lane_says_how_it_shares_the_machine(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and "cores shared by the kernel; memory the lane can give" in out and "freezing: " in out
    assert "heavy runs: 0 of 12 cores in use" in out or "heavy runs: 0 of" in out
    assert "(light reserve 20 %)" in out and "capacity: 1" not in out


def test_lane_capacity_set_is_said_to_have_no_effect(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "machine.toml").write_text("lane_capacity = 1\n", encoding="utf-8")
    _code, out = run_cli("lane", "--root", str(root))
    assert out.count("lane_capacity = 1 in machine.toml no longer has an effect (0.30); runs share the machine by "
                     "memory and priority") == 1


def test_a_run_reads_its_priority_and_a_frozen_one_says_by_whose_order():
    import datetime as dt
    from flotilla.lane import book, commands, policy
    now = dt.datetime(2026, 11, 6, 10, 30, tzinfo=dt.timezone.utc)
    machine = policy.Machine(cpus=8, give_mb=9000, free_mb=3000, swap_free_mb=8000, swap_total_mb=8000)
    run = policy.Run("b3", policy.RUNNING, 3174, True, 4, kind="run", waited_s=600, ran_s=240, since_start_s=240)
    line = commands.value_of(run, book.Booking(id="b3", heavy=True), machine, now)
    assert "P 0.49 (type 0.06, wait 0.30, run 0.13)" in line and "heavy (memory now 3.1 GB)" in line
    assert "immortal from " in line
    frozen = policy.Run("b4", policy.FROZEN, 900, False, 1, ran_s=120)
    item = book.Booking(id="b4", frozen="frozen", order_by="b14", frozen_at="2026-11-06T10:31:00+00:00")
    assert "frozen since " in commands.value_of(frozen, item, machine, now)
    assert "by b14's order" in commands.value_of(frozen, item, machine, now)
    lapsed = book.Booking(id="b9", unanswered=True)
    assert "b9 did not answer a freeze order" in commands.value_of(run, lapsed, machine, now)


def test_an_older_waiter_is_named_in_the_lane(tmp_path, monkeypatch):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    import os
    root = onboarded(tmp_path, monkeypatch)
    store = LocalLogStore(tmp_path / "state" / "lane")
    with store.transaction(book.KEY) as tx:
        tx.append({"at": "2026-11-06T10:00:00+00:00", "booking": "b1", "state": book.WAITING, "who": "old",
                   "pid": None, "rule": 3})
    _code, out = run_cli("lane", "--root", str(root))
    assert "a seat of a flotilla before 0.30 waits: lane_capacity counts every run until it goes" in out



def test_lane_stop_signals_no_pid_without_a_start_mark(tmp_path, monkeypatch):   # review of 0.30.1, I4
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book, commands
    root = onboarded(tmp_path, monkeypatch)
    store = LocalLogStore(tmp_path / "state" / "lane")
    with store.transaction(book.KEY) as tx:
        tx.append({"at": "2026-11-06T10:00:00+00:00", "booking": "b1", "state": book.HELD, "who": "main session 1",
                   "pid": 4242, "mark": "", "rule": 4})
    sent = []
    monkeypatch.setattr(commands.os, "kill", lambda pid, number: sent.append(pid))
    code, out = run_cli("lane", "stop", "--root", str(root), "--as", "main session 1")
    assert sent == [] and code == 2



def test_a_frozen_scope_names_the_thaw_that_fits_it():   # review of ed148e3, 4
    import datetime as dt
    from flotilla.lane import book, commands, policy
    now = dt.datetime(2026, 11, 6, 10, 30, tzinfo=dt.timezone.utc)
    machine = policy.Machine(cpus=8, give_mb=9000, free_mb=3000, swap_free_mb=8000, swap_total_mb=8000)
    frozen = policy.Run("b4", policy.FROZEN, 900, False, 1, ran_s=120)
    scoped = book.Booking(id="b4", frozen="frozen", order_by="b14", group=4242, unit="flotilla-lane-b4-9-1.scope")
    stopped = book.Booking(id="b4", frozen="frozen", order_by="b14", group=4242)
    assert "flotilla-lane-b4-9-1.scope" in commands.value_of(frozen, scoped, machine, now)
    assert "kill -CONT" not in commands.value_of(frozen, scoped, machine, now)
    assert "kill -CONT -4242" in commands.value_of(frozen, stopped, machine, now)



def test_a_frozen_scopes_hint_writes_the_freeze_file_itself():   # review of the follow-up, declined: systemd's thaw
    import datetime as dt
    from flotilla.lane import book, commands, policy
    now = dt.datetime(2026, 11, 6, 10, 30, tzinfo=dt.timezone.utc)
    machine = policy.Machine(cpus=8, give_mb=9000, free_mb=3000, swap_free_mb=8000, swap_total_mb=8000)
    frozen = policy.Run("b4", policy.FROZEN, 900, False, 1, ran_s=120)
    scoped = book.Booking(id="b4", frozen="frozen", order_by="b14", group=4242, unit="flotilla-lane-b4-9-1.scope")
    line = commands.value_of(frozen, scoped, machine, now)
    assert "cgroup.freeze" in line and "flotilla-lane-b4-9-1.scope" in line



def test_the_freeze_ceiling_is_a_setting(monkeypatch):   # the person's ruling, 2026-11-06
    from flotilla.lane import commands
    for bad in ({"lane_freeze_ceiling_minutes": 1}, {"lane_freeze_ceiling_minutes": 241},
                {"lane_freeze_ceiling_minutes": True}, {"lane_freeze_ceiling_minutes": "10"}, {}):
        monkeypatch.setattr(commands, "_machine", lambda bad=bad: bad)
        assert commands.freeze_ceiling_minutes() == 10
    monkeypatch.setattr(commands, "_machine", lambda: {"lane_freeze_ceiling_minutes": 30})
    assert commands.freeze_ceiling_minutes() == 30
    assert commands.value_for({})(None).freeze_ceiling_s == 1800.0



def test_a_hint_never_names_another_bookings_scope():   # review, minor 5
    import datetime as dt
    from flotilla.lane import book, commands, policy
    now = dt.datetime(2026, 11, 6, 10, 30, tzinfo=dt.timezone.utc)
    machine = policy.Machine(cpus=8, give_mb=9000, free_mb=3000, swap_free_mb=8000, swap_total_mb=8000)
    frozen = policy.Run("b4", policy.FROZEN, 900, False, 1, ran_s=120)
    theirs = book.Booking(id="b4", frozen="frozen", order_by="b14", unit="flotilla-lane-b9-9-1.scope")
    assert "flotilla-lane-b9" not in commands.value_of(frozen, theirs, machine, now)



# --- the lane daemon, as `flotilla lane` shows it (lane daemon design, 4.9) ---

class LiveDaemon:
    """This test process posing as the lane daemon: its heartbeat, its lock held."""

    def __init__(self, root, groups=None, over=None, release="0.31.0"):
        import fcntl
        import json
        import os
        import time
        from flotilla.lane import daemon
        from flotilla.lane.procs import ProcessTable
        root.mkdir(parents=True, exist_ok=True)
        pid = os.getpid()
        (root / daemon.BEAT).write_text(json.dumps({
            "pid": pid, "mark": ProcessTable.for_machine().start_mark(pid), "copy": "", "release": release,
            "protocol": daemon.PROTOCOL, "tick": time.time() - 3, "why": {}, "groups": groups or {},
            "over": over or {}}))
        self.fd = os.open(root / daemon.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def gone(self):
        import os
        os.close(self.fd)


def held_here(tmp_path, booking="b40", **fields):
    import os
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    lanes = book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine())
    pid = os.getpid()
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": booking, "state": book.HELD, "who": "main session 1",
                   "note": "suite", "pid": pid, "mark": ProcessTable.for_machine().start_mark(pid), "rule": 5,
                   "started": lanes._now(), "path": "value: P 0.10, light", **fields})


def test_status_names_the_daemon(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and out.splitlines()[0].startswith("lane daemon: not running - FLOTILLA_LANE_DAEMON=off; "
                                                        "a new run starts only into an empty lane"), out
    monkeypatch.delenv("FLOTILLA_LANE_DAEMON")
    live = LiveDaemon(tmp_path / "state" / "lane")
    try:
        code, out = run_cli("lane", "--root", str(root))
        first = out.splitlines()[0]
        assert first.startswith("lane daemon: running, pid ") and ", 0.31.0, last tick " in first, out
    finally:
        live.gone()


def test_status_prints_ceiling_and_pressure(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.delenv("FLOTILLA_LANE_DAEMON")
    held_here(tmp_path)
    live = LiveDaemon(tmp_path / "state" / "lane", groups={"b40": {"ceiling_mb": 1229, "current_mb": 1100,
                                                                    "pressure": 3.2, "squeezed": True,
                                                                    "pressed": False}})
    try:
        _code, out = run_cli("lane", "--root", str(root))
        line = next(line for line in out.splitlines() if line.strip().startswith("b40"))
        assert "ceiling 1.2 GB, pressure 3 %, squeezed" in line, out
    finally:
        live.gone()


def test_status_prints_over_and_nothing_to_take(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.delenv("FLOTILLA_LANE_DAEMON")
    live = LiveDaemon(tmp_path / "state" / "lane", over={"mb": 700, "s": 14.0, "nothing": 700})
    try:
        _code, out = run_cli("lane", "--root", str(root))
        assert "machine: over its floor by 700 MB for 14 s" in out
        assert "nothing can be frozen: every run over the line is immortal or pinned" in out
    finally:
        live.gone()


def test_status_names_an_older_booking(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    held_here(tmp_path, booking="b41", rule=4)
    _code, out = run_cli("lane", "--root", str(root))
    line = next(line for line in out.splitlines() if line.strip().startswith("b41"))
    assert "(an older flotilla: decides for itself)" in line, out
