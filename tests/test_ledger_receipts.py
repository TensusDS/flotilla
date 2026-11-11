import sys

import pytest

from flotilla.ledger import receipts
from ledgerkit import commit, git, repo_with_origin

KEY = "k-000000000000"
GREEN = f"{sys.executable} -c \"print('2 passed')\""


def profile(command=None):
    if command is None:
        return {"schema": 1}
    return {"schema": 1, "tests": {"tier": [{"name": "unit", "command": command, "required_for": ["handover", "push"]}]}}


def test_no_tiers_configured_is_valid_and_says_so(tmp_path):
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha="a" * 40, purpose="handover",
                                     profile=profile())
    assert ok and "no handover tiers" in why


def test_a_green_run_is_a_valid_receipt(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN), timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover",
                                     profile=profile(GREEN))
    assert ok and sha[:7] in why


def test_a_dirty_tree_gets_no_receipt(tmp_path):
    root = repo_with_origin(tmp_path)
    (root / "wip.txt").write_text("x", encoding="utf-8")
    with pytest.raises(receipts.ReceiptRefused, match="uncommitted"):
        receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN),
                             timeout=60)


def test_a_red_tier_is_named(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    red = profile("exit 1")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=red, timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover", profile=red)
    assert not ok and "unit" in why


def test_a_changed_tier_command_voids_the_receipt(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN), timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover",
                                     profile=profile(GREEN + "  # changed"))
    assert not ok and "changed" in why


def test_a_receipt_describes_one_revision(tmp_path):
    root = repo_with_origin(tmp_path)
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN), timeout=60)
    later = commit(root, "later")
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=later, purpose="handover",
                                     profile=profile(GREEN))
    assert not ok and "no handover receipt" in why


def test_a_duplicate_tier_name_cannot_hide_a_red_run(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    both = {"schema": 1, "tests": {"tier": [
        {"name": "unit", "command": "exit 1", "required_for": ["handover"]},
        {"name": "unit", "command": GREEN, "required_for": ["handover"]}]}}
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=both, timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover", profile=both)
    assert not ok and "unit" in why


def _counting(tmp_path):
    """A green tier that counts its own runs in a file outside the repository."""
    counter = tmp_path / "runs.txt"
    return f"{sys.executable} -c \"open(r'{counter}', 'a').write('x'); print('2 passed')\"", counter


def test_the_same_files_are_not_tested_twice(tmp_path):
    """The slowdown measured on the alpha fleet (2026-10-02): the path from hand to trunk grew tenfold, almost all of
    it in the machine's one-at-a-time lane. A sender's merge commit often carries exactly the files the author's tip
    did; a green tier over the same files and the same command is that tier's answer, whoever asks."""
    root = repo_with_origin(tmp_path)
    command, counter = _counting(tmp_path)
    tip = git(root, "rev-parse", "HEAD")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(command),
                         timeout=60)
    commit(root, "a merge that changes no file")   # no file changes: the helper commits with --allow-empty
    merge = git(root, "rev-parse", "HEAD")
    assert merge != tip
    receipt = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push", profile=profile(command),
                                   timeout=60)
    assert counter.read_text() == "x"   # run once, not twice
    assert receipt["tiers"][0]["status"] == "green" and tip[:7] in receipt["tiers"][0]["summary"]
    ok, _ = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=merge, purpose="push",
                                   profile=profile(command))
    assert ok


def test_other_files_or_another_command_run_again(tmp_path):
    root = repo_with_origin(tmp_path)
    command, counter = _counting(tmp_path)
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(command),
                         timeout=60)
    commit(root, "a change", "src.txt", "new\n")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push", profile=profile(command),
                         timeout=60)
    assert counter.read_text() == "xx"
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push",
                         profile=profile(command + " --again"), timeout=60)
    assert counter.read_text() == "xxx"


def test_a_red_tier_is_never_reused_and_says_why(tmp_path):
    """Beta field test W23: a red push receipt said `node: red` and nothing else; the sender looked for a log that
    did not exist. A red tier carries its exit code and the last lines it printed."""
    root = repo_with_origin(tmp_path)
    red = f"{sys.executable} -c \"import sys; print('sh: 1: vitest: not found'); sys.exit(127)\""
    first = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(red),
                                 timeout=60)
    tier = first["tiers"][0]
    assert tier["status"] == "red" and tier["exit"] == 127 and "vitest: not found" in tier["tail"]
    again = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push", profile=profile(red),
                                 timeout=60)
    assert "reused" not in again["tiers"][0]["summary"]


def _with_setup(tmp_path, root, command):
    counter = tmp_path / "setup-runs.txt"
    setup = f"{sys.executable} -c \"open(r'{counter}', 'a').write('s')\""
    data = profile(command)
    data["tests"]["setup_command"] = setup
    (root / "package-lock.json").write_text('{"v": 1}', encoding="utf-8")
    git(root, "add", "package-lock.json")
    commit(root, "a lockfile")
    return data, counter


def test_a_fresh_tree_is_set_up_before_its_tiers_once_per_lockfile(tmp_path):
    root = repo_with_origin(tmp_path)
    tiers, _ = _counting(tmp_path)
    data, counter = _with_setup(tmp_path, root, tiers)
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "s"
    commit(root, "a change", "src.txt", "new\n")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "s"                  # the lockfile did not change: nothing to set up
    (root / "package-lock.json").write_text('{"v": 2}', encoding="utf-8")
    git(root, "add", "package-lock.json")
    commit(root, "a new dependency")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "ss"


def test_a_failed_setup_is_named_not_reported_as_a_red_tier(tmp_path):
    root = repo_with_origin(tmp_path)
    data, _ = _with_setup(tmp_path, root, GREEN)
    data["tests"]["setup_command"] = f"{sys.executable} -c \"import sys; print('npm ERR! network'); sys.exit(1)\""
    with pytest.raises(receipts.ReceiptRefused, match="setup") as refused:
        receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert "npm ERR! network" in str(refused.value) and "exit 1" in str(refused.value)


def test_a_tree_that_moved_during_the_run_gets_no_receipt_and_teaches_nothing(tmp_path):
    """Review of 0.6.2, I1: HEAD and cleanliness were checked only before the tiers; a tree edited during the run
    could record green for files never tested, and reuse then carried that green to every commit with those files."""
    root = repo_with_origin(tmp_path)
    edits = f"{sys.executable} -c \"open('wip.txt', 'w').write('edited while testing'); print('2 passed')\""
    with pytest.raises(receipts.ReceiptRefused, match="changed while"):
        receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(edits),
                             timeout=60)
    (root / "wip.txt").unlink()
    command, counter = _counting(tmp_path)
    assert not list((tmp_path / "s" / "receipts" / KEY).glob("files-*.json"))
    moves = f"{sys.executable} -c \"import subprocess; subprocess.run(['git', '-c', 'user.email=t@x', '-c', " \
            f"'user.name=t', 'commit', '-q', '--allow-empty', '-m', 'during']); print('2 passed')\""
    with pytest.raises(receipts.ReceiptRefused, match="changed while"):
        receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(moves),
                             timeout=60)


@pytest.mark.parametrize("launch", ["", "exec "])   # macOS's /bin/sh (bash) execs a lone command, as `exec` does
def test_a_tier_interrupted_takes_its_processes_with_it(tmp_path, launch):
    """Review of 0.6.2, I3: a receipt stopped with SIGTERM died, and its tier - started in its own session - kept
    computing, unbooked. Whatever interrupts a tier stops the tier's whole group."""
    import os
    import signal
    import threading
    import time
    from flotilla.onboard.firstrun import run_tier
    child = tmp_path / "tier.pid"
    command = f"{launch}{sys.executable} -c \"import os, time; open(r'{child}', 'w').write(str(os.getpid())); " \
              "time.sleep(60)\""

    def interrupt():
        for _ in range(100):
            if child.exists() and child.read_text():
                break
            time.sleep(0.05)
        signal.pthread_kill(main, signal.SIGUSR1)   # to the main thread, where Python runs its handlers
    main = threading.main_thread().ident
    previous = signal.signal(signal.SIGUSR1, lambda *a: (_ for _ in ()).throw(SystemExit(143)))
    try:
        threading.Thread(target=interrupt, daemon=True).start()
        with pytest.raises(SystemExit):
            run_tier("unit", command, tmp_path, timeout=60)
    finally:
        signal.signal(signal.SIGUSR1, previous)
    pid = int(child.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the tier outlived the interrupted receipt")


def test_a_tree_cut_again_at_the_same_path_is_set_up_again(tmp_path):
    """Review of 0.6.2, I4: the setup marker was keyed on the tree's path, and seat trees live at fixed paths; a seat
    retired and raised again got a fresh tree that skipped its setup."""
    root = repo_with_origin(tmp_path)
    tiers, _ = _counting(tmp_path)
    data, counter = _with_setup(tmp_path, root, tiers)
    seat = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(seat))
    receipts.run_receipt(seat, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "s"
    git(root, "worktree", "remove", "--force", str(seat))
    git(root, "branch", "-D", "fleet/main-1")
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(seat))
    commit(seat, "new work", "w.txt", "x\n")
    receipts.run_receipt(seat, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "ss"


def test_a_red_tier_after_a_skipped_setup_says_so(tmp_path):
    root = repo_with_origin(tmp_path)
    data, _ = _with_setup(tmp_path, root, GREEN)
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    data["tests"]["tier"][0]["command"] = "exit 1"
    commit(root, "more", "m.txt", "m\n")
    red = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert "setup" in red["tiers"][0]["summary"] and "--setup" in red["tiers"][0]["summary"]


def test_a_seat_tree_is_not_set_up_again_for_its_own_commits(tmp_path):
    """The other side of the re-cut test: a seat's tree making commits is the same tree, and its setup holds."""
    root = repo_with_origin(tmp_path)
    tiers, _ = _counting(tmp_path)
    data, counter = _with_setup(tmp_path, root, tiers)
    seat = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(seat))
    receipts.run_receipt(seat, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    for n in range(3):
        commit(seat, f"work {n}", f"w{n}.txt", "x\n")
        receipts.run_receipt(seat, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "s"


def test_the_first_green_receipt_measures_a_tier_this_machine_never_measured(tmp_path):
    """Alpha field test of 0.6.7, W2: only `onboard write` recorded tier times, so `onboard check` said "never run
    green on this machine" with no way out but importing flotilla's internals - and the lane's ceiling had no time to
    derive from. A green tier a receipt ran records its time once; a measurement already there is kept."""
    from flotilla.onboard.firstrun import TierRun, load_measurements, save_measurements
    state = tmp_path / "s"
    two = {"schema": 1, "tests": {"tier": [{"name": "unit", "command": GREEN, "required_for": ["handover"]},
                                           {"name": "lint", "command": GREEN, "required_for": ["handover"]},
                                           {"name": "e2e", "command": "exit 1", "required_for": ["handover"]}]}}
    save_measurements(state, KEY, [TierRun("lint", "green", 77.0, "", "")])
    root = repo_with_origin(tmp_path)
    receipts.run_receipt(root, state=state, repo_key=KEY, purpose="handover", profile=two, timeout=60)
    measured = load_measurements(state, KEY)
    assert measured["lint"] == 77.0                      # onboarding's measurement stands
    assert isinstance(measured.get("unit"), float)       # the first green run measured it
    assert "e2e" not in measured                         # a red run measures nothing


def test_a_torn_measurements_file_never_costs_a_green_receipt(tmp_path):
    """Review of 0.6.8, I1: measuring is a side effect; a half-written measurements file raised out of
    run_receipt, so every later receipt on the repository failed until someone fixed the file by hand."""
    from flotilla.onboard import firstrun
    state = tmp_path / "s"
    path = firstrun._path(state, KEY)
    path.parent.mkdir(parents=True)
    path.write_text("[seconds]\nunit = ", encoding="utf-8")
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    receipts.run_receipt(root, state=state, repo_key=KEY, purpose="handover", profile=profile(GREEN), timeout=60)
    ok, why = receipts.check_receipt(state=state, repo_key=KEY, sha=sha, purpose="handover", profile=profile(GREEN))
    assert ok, why


def test_measurements_are_written_whole_or_not_at_all(tmp_path, monkeypatch):
    """Review of 0.6.8, I1: two seats' first receipts at once could read each other's half-written file."""
    import os
    from flotilla.onboard import firstrun
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append((str(a), str(b))), real(a, b))[1])
    firstrun.measure_once(tmp_path, KEY, {"unit": 3.0})
    assert seen and seen[-1][1] == str(firstrun._path(tmp_path, KEY))
    assert firstrun.load_measurements(tmp_path, KEY) == {"unit": 3.0}


@pytest.mark.parametrize("field, other", [("sha", "b" * 40), ("purpose", "push")])
def test_a_receipt_file_under_another_name_is_not_green_for_it(tmp_path, field, other):
    """The file name is where a receipt is looked up; what it vouches for is the revision and purpose written in
    it. A copy under another revision's or purpose's name vouches for nothing there (TODO, ledger part A)."""
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN),
                         timeout=60)
    source = receipts._path(tmp_path / "s", KEY, sha, "handover")
    wanted = {"sha": sha, "purpose": "handover", field: other}
    receipts._path(tmp_path / "s", KEY, wanted["sha"], wanted["purpose"]).write_text(source.read_text("utf-8"),
                                                                                       encoding="utf-8")
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=wanted["sha"],
                                     purpose=wanted["purpose"], profile=profile(GREEN))
    assert not ok and "another" in why


def test_a_green_receipt_records_the_tiers_peak_memory(tmp_path):
    """Fleet sizing, section 2.1: how many test runs fit beside the seats is read from each tier's peak memory, and
    receipts are where tiers run day to day - onboarding measures once, the receipt keeps the worst run seen."""
    from flotilla.onboard.firstrun import load_peaks
    state = tmp_path / "s"
    heavy = f'"{sys.executable}" -c "import time; b = bytearray(60_000_000); time.sleep(1.5)"'
    data = {"schema": 1, "tests": {"tier": [{"name": "unit", "command": heavy, "required_for": ["handover"]}]}}
    root = repo_with_origin(tmp_path)
    receipts.run_receipt(root, state=state, repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert load_peaks(state, KEY).get("unit", 0) >= 45


def test_a_receipts_measurement_counts_only_the_tiers_that_ran():
    """A reused green ran nothing: it must not add its old seconds to what the booking took, or be learned twice."""
    result = {"tiers": [
        {"name": "lint", "status": "green", "seconds": 3.0, "summary": "reused: green over the same files at abc1234"},
        {"name": "unit", "status": "green", "seconds": 40.0, "peak_mb": 1700, "cores": 6.8, "busy": 0.5,
         "summary": "12 passed"}]}
    measured = receipts.measured_of(result)
    assert measured["seconds"] == 40.0 and [t["name"] for t in measured["ran"]] == ["unit"]
    assert receipts.measured_of({"tiers": [result["tiers"][0]]}) == {}


def test_a_receipts_hold_time_counts_setup_and_every_tier_that_ran():
    """Review of stage 1: setup held the lane unmeasured, and a red or timed-out tier left the hold unknown."""
    result = {"setup": {"name": "setup", "status": "green", "seconds": 20.0, "elapsed": 20.0, "peak_mb": 900,
                        "cores": 2.0, "busy": 0.3, "summary": ""},
              "tiers": [
                  {"name": "unit", "status": "green", "seconds": 40.0, "elapsed": 40.0, "peak_mb": 1700, "cores": 6.8,
                   "busy": 0.9, "summary": "12 passed"},
                  {"name": "e2e", "status": "red", "seconds": None, "elapsed": 5.0, "summary": "1 failed"},
                  {"name": "slow", "status": "timed-out", "seconds": None, "elapsed": 600.0, "summary": ""}]}
    measured = receipts.measured_of(result)
    assert measured["seconds"] == 665.0 and measured["verdict"] == "red" and measured["peak_mb"] == 1700
    by = {t["name"]: t for t in measured["ran"]}
    assert set(by) == {"setup", "unit", "e2e", "slow"} and by["setup"]["kind"] == "setup"
    assert by["slow"]["seconds"] == 600.0 and by["slow"]["status"] == "timed-out"   # at least its time
    assert by["e2e"]["seconds"] is None                                            # an early end says nothing


def test_a_receipt_reports_its_setup_run(tmp_path):
    data = {"schema": 1, "tests": {"setup_command": "true",
                                   "tier": [{"name": "unit", "command": GREEN, "required_for": ["handover"]}]}}
    root = repo_with_origin(tmp_path)
    result = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data,
                                  timeout=60)
    assert result["setup"]["name"] == "setup" and result["setup"]["status"] == "green"
    assert [t["name"] for t in receipts.measured_of(result)["ran"]] == ["setup", "unit"]


def _branch_profile(root, data, text=None):
    from flotilla.onboard.tomlw import render_toml
    (root / ".flotilla").mkdir(exist_ok=True)
    (root / ".flotilla" / "project.toml").write_text(text if text is not None else render_toml(data), encoding="utf-8")
    git(root, "add", ".flotilla")
    commit(root, "the branch's profile")
    return git(root, "rev-parse", "HEAD")


def _with_server(command=GREEN, unit=GREEN):
    data = profile(unit)
    data["tests"]["tier"].append({"name": "server", "command": command, "required_for": ["handover"]})
    return data


def test_a_tier_only_the_branch_adds_runs_in_its_receipt(tmp_path):
    """Gamma, 2026-11-01: `server/solution` added a `server` tier; both receipts ran only trunk's tiers,
    said nothing, and were green - the server tests ran because two sessions ran them by hand."""
    root = repo_with_origin(tmp_path)
    _branch_profile(root, _with_server())
    effective, problem, _ = receipts.with_branch_tiers(root, "HEAD", profile(GREEN))
    assert problem == ""
    receipt = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=effective,
                                   timeout=60)
    assert [tier["name"] for tier in receipt["tiers"]] == ["unit", "server"]


def test_a_receipt_without_the_branch_tier_does_not_pass_the_check(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = _branch_profile(root, _with_server())
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN),
                         timeout=60)   # trunk's tiers only, as before 0.24.2
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover",
                                     profile=profile(GREEN), root=root)
    assert not ok and "server" in why


def test_a_branch_cannot_change_a_trunk_tier(tmp_path):
    root = repo_with_origin(tmp_path)
    weak = f"{sys.executable} -c \"print('0 passed')\""
    _branch_profile(root, profile(weak))
    effective, problem, notes = receipts.with_branch_tiers(root, "HEAD", profile(GREEN))
    assert problem == "" and receipts.tiers_for(effective, "handover")[0]["command"] == GREEN
    assert any("`unit`" in note and "trunk's command" in note for note in notes)


def test_a_branch_profile_that_does_not_parse_is_not_green(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = _branch_profile(root, None, text="[tests\nbroken = ")
    _, problem, _ = receipts.with_branch_tiers(root, sha, profile(GREEN))
    assert ".flotilla/project.toml" in problem
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN),
                         timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover",
                                     profile=profile(GREEN), root=root)
    assert not ok and ".flotilla/project.toml" in why


@pytest.mark.parametrize("text", ['schema = 1\ntests = "pytest"\n', "schema = 1\n[[tests]]\nname = 1\n",
                                  'schema = 1\n[tests]\ntier = "unit"\n'])
def test_a_branch_profile_of_the_wrong_shape_is_not_green_and_never_a_traceback(tmp_path, text):
    """Final review of 0.24.2, I1: a profile that parses but holds `tests` or `tier` of the wrong type raised out of
    check_receipt, and with it `work status` for every session, the sender's brief and the pre-push hook."""
    root = repo_with_origin(tmp_path)
    sha = _branch_profile(root, None, text=text)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover",
                                     profile=profile(GREEN), root=root)
    assert not ok and ".flotilla/project.toml" in why


def test_a_branch_profile_that_is_not_utf8_is_not_green(tmp_path):
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_bytes(b"schema = 1\n# \xff\xfe\n")
    git(root, "add", ".flotilla")
    sha = commit(root, "a profile that is not text")
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover",
                                     profile=profile(GREEN), root=root)
    assert not ok and ".flotilla/project.toml" in why


def test_a_tier_trunk_renamed_after_the_branch_was_cut_is_not_added(tmp_path):
    """Final review of 0.24.2, M4: tiers the branch adds are counted against the profile it was cut from; a tier
    trunk renamed later is trunk's change, not the branch's, and must not make every older branch red."""
    root = repo_with_origin(tmp_path)
    old = {"schema": 1, "tests": {"tier": [{"name": "process", "command": GREEN, "required_for": ["handover"]}]}}
    _branch_profile(root, old)
    git(root, "push", "-q", "origin", "main")
    git(root, "checkout", "-q", "-b", "feat/x")
    tip = commit(root, "branch work")
    git(root, "checkout", "-q", "main")
    renamed = {"schema": 1, "tests": {"tier": [{"name": "unit", "command": GREEN, "required_for": ["handover"]}]}}
    _branch_profile(root, renamed)
    git(root, "push", "-q", "origin", "main")
    effective, problem, _ = receipts.with_branch_tiers(root, tip, renamed)
    assert problem == "" and [t["name"] for t in receipts.tiers_for(effective, "handover")] == ["unit"]


def test_a_tiers_peak_cores_reaches_the_lanes_journal():
    from flotilla.ledger.receipts import measured_of
    found = measured_of({"tiers": [{"name": "unit", "status": "green", "seconds": 30.0, "elapsed": 30.0,
                                    "peak_mb": 400, "cores": 1.0, "busy": 0.3, "peak_cores": 5.5}]})
    assert found["peak_cores"] == 5.5 and found["ran"][0]["peak_cores"] == 5.5


def test_a_receipt_tier_carries_its_frozen_time():
    from flotilla.ledger.receipts import _tier_result
    from flotilla.onboard.firstrun import TierRun
    found = _tier_result(TierRun("unit", "red", None, "2 failed", "", 1, frozen_s=120.0))
    assert found["frozen_s"] == 120.0


def test_a_receipt_tier_red_after_a_freeze_says_so():
    from flotilla.ledger.receipts import tier_line
    assert tier_line({"name": "unit", "status": "red", "summary": "2 failed", "frozen_s": 120.0}) == \
        "red       unit: 2 failed (red after a freeze of 2 min)"
    assert tier_line({"name": "unit", "status": "green", "summary": "ok", "frozen_s": 120.0}) == \
        "green     unit: ok"


@pytest.mark.parametrize("value", [False, "false", 0])
def test_a_tier_that_reads_the_history_is_never_reused(tmp_path, value):
    """A field fleet's tiers that check commit messages and what a merge commit carries were answered from the
    author's tip: the sender's merge changes no file, so its own commit was never read (2026-11-11). `reuse` other
    than true runs the tier over every commit; a value it cannot read runs it too."""
    root = repo_with_origin(tmp_path)
    command, counter = _counting(tmp_path)
    history = {"schema": 1, "tests": {"tier": [
        {"name": "commits", "command": command, "required_for": ["handover", "push"], "reuse": value}]}}
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=history, timeout=60)
    commit(root, "a merge that changes no file")
    assert receipts.to_run(root, state=tmp_path / "s", repo_key=KEY, purpose="push", profile=history) == ["commits"]
    receipt = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push", profile=history,
                                   timeout=60)
    assert counter.read_text() == "xx" and "reused" not in receipt["tiers"][0]["summary"]


def test_reuse_true_is_the_default():
    tier = {"name": "unit", "command": "x", "required_for": ["push"]}
    assert receipts.reusable(tier) and receipts.reusable({**tier, "reuse": True})
    assert not receipts.reusable({**tier, "reuse": False})
