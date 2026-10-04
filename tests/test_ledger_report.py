from flotilla.ledger import report, steering
from flotilla.ledger.model import Row
from ledgerkit import PROFILE, actor, commit, drive, git, make_ledger, repo_with_origin

DIRECT = {**PROFILE, "flow": {"mode": "direct"}}


def test_the_brief_lists_what_is_ready_urgent_first_and_says_what_is_not(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    drive(root, ledger, "feat/a")
    drive(root, ledger, "feat/b")
    steering.urgent(ledger, actor(ledger, "orchestrator 1"), "feat/b", why="a client is waiting")
    drive(root, ledger, "feat/c", to="claimed")
    drive(root, ledger, "feat/d", requires=["feat/c"])
    drive(root, ledger, "feat/e")
    git(root, "checkout", "-q", "feat/e")
    commit(root, "after acceptance", "late.txt")
    git(root, "checkout", "-q", "main")
    text = "\n".join(report.brief(ledger))
    assert text.index("`feat/b`") < text.index("`feat/a`")
    assert "urgent since" in text and "a client is waiting" in text
    assert "`feat/d` (accepted): blocked on `feat/c` (claimed)" in text
    assert "`feat/e` (accepted): moved since acceptance" in text
    assert "gate: none - no CI and no push tiers; nothing verifies these commits" in text
    assert text.rstrip().endswith("Answer yes to ship 2 rows.")


def test_an_empty_brief_says_so(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    assert "  nothing is ready to ship" in report.brief(ledger)


def entry(at, move, state, by="a"):
    return {"at": f"2026-09-26T{at}:00+00:00", "move": move, "state": state, "by": by, "caller": "", "evidence": {}}


def test_metrics_read_the_history():
    history = [entry("10:00", "claim", "claimed"), entry("11:00", "hand", "handed"),
               entry("11:30", "fix", "fixing", by="review session 1"), entry("12:00", "hand", "handed"),
               entry("12:10", "take", "handed", by="review session 1"),
               entry("12:30", "accept", "accepted", by="review session 1")]
    found = report.metrics({"r1": Row(id="r1", history=history)},
                           failures=[{"event": "pre-handed", "status": "broken"},
                                     {"event": "pre-handed", "status": "rejected"}])
    assert found["time_in_state"]["claimed"] == 3600
    assert found["time_in_state"]["handed"] == 1800   # median of 30 and 30 minutes; take stays inside handed
    assert found["return_rate"] == 0.5
    assert found["accepts_by_reader"] == {"review session 1": 1}
    assert found["event_failures"] == {"pre-handed": 1}
    lines = report.format_metrics(found)
    assert "  claimed   1.0 h" in lines and "  fixing    30 min" in lines and "return rate: 50% of handovers came back" in lines


def test_metrics_of_an_empty_ledger_say_there_is_nothing_yet():
    lines = report.format_metrics(report.metrics({}))
    assert "return rate: no handovers yet" in lines


def test_without_origin_landed_work_is_delivered_not_ready_to_ship(tmp_path):
    from flotilla.ledger import delivery
    from ledgerkit import merge
    root = tmp_path / "solo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "init", "a.txt")
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "local"}})
    drive(root, ledger)
    delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x")
    merge(root, "feat/x")
    delivery.land(ledger, actor(ledger, "sender 1"), "feat/x")
    assert "  nothing is ready to ship" in report.brief(ledger)


HUMAN = {**PROFILE, "flow": {"mode": "direct", "merge_authorized_by": "human"}}


def test_where_a_person_approves_the_brief_prints_each_approve_command_ready_to_type(tmp_path):
    """Scan of 0.6.10, F2: the orchestrator assembled `<flotilla> work approve <branch>` from a branch name a session
    chose, and the person ran it with `!` in their own shell. The CLI prints the command itself, shell-quoted, so
    the orchestrator relays a line instead of building one - an older row's name with `$(...)` in it included."""
    import dataclasses
    import shlex
    from flotilla.guards.githooks import link_path
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=HUMAN)
    drive(root, ledger, "feat/a")
    rows = ledger.rows()
    row_id = next(key for key, row in rows.items() if row.branch == "feat/a")
    text = "\n".join(report.brief(ledger, rows))
    assert "approve: ! flotilla work approve feat/a" in text   # no stable link yet: the name on PATH
    link = link_path(ledger.state_dir)
    link.parent.mkdir(parents=True)
    link.symlink_to("/bin/true")
    git(root, "branch", "feat/x$(touch-pwned)", "feat/a")   # a real branch: one git cannot find is held back
    rows[row_id] = dataclasses.replace(rows[row_id], branch="feat/x$(touch-pwned)")
    text = "\n".join(report.brief(ledger, rows))
    assert f"approve: ! {shlex.quote(str(link))} work approve 'feat/x$(touch-pwned)'" in text


def test_where_the_sender_merges_the_brief_prints_no_approve_command(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    drive(root, ledger, "feat/a")
    assert "work approve" not in "\n".join(report.brief(ledger))


def test_a_change_born_in_the_batch_gets_its_approve_line_too(tmp_path):
    """Final review of the scan fixes, minor: a change born in the batch waits for the person's approve like any
    other row where a person authorizes merges, and the orchestrator copies approve lines only from the brief - so
    the brief prints one for it, and stops once it is approved."""
    from flotilla.ledger import delivery, outside
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=HUMAN)
    fix = commit(root, "a fix inside the batch", "fix.txt")
    outside.inbatch(ledger, actor(ledger, "sender 1"), "batch/fix", commit=fix, read_by="review session 1",
                    why="a fix")
    text = "\n".join(report.brief(ledger))
    assert "`batch/fix` (inbatch)" in text and "approve: ! flotilla work approve batch/fix" in text
    delivery.approve(ledger, "batch/fix")
    assert "batch/fix" not in "\n".join(report.brief(ledger))


def test_an_accepted_row_whose_branch_is_gone_is_held_not_ready(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    drive(root, ledger, "feat/a")
    git(root, "branch", "-q", "-D", "feat/a")
    text = "\n".join(report.brief(ledger))
    assert "nothing is ready to ship" in text and "`feat/a` (accepted): git cannot find the branch" in text


def test_an_accepted_row_whose_branch_is_only_on_origin_names_the_command(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    drive(root, ledger, "feat/a")
    git(root, "push", "-q", "origin", "feat/a")
    git(root, "fetch", "-q", "origin")
    git(root, "branch", "-q", "-D", "feat/a")
    assert "git branch feat/a origin/feat/a" in "\n".join(report.brief(ledger))
