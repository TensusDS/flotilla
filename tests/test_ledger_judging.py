import sys

import pytest

from flotilla.ledger import delivery, handover, judging, reading
from flotilla.ledger import tree as tree_mod
from flotilla.ledger.errors import MoveRefused
from ledgerkit import PROFILE, actor, commit, git, make_ledger, merge, repo_with_origin, shipped_direct

JUDGE, SENDER, OWNER = "acceptance judge 1", "sender 1", "main session 1"
DIRECT = {**PROFILE, "flow": {"mode": "direct"}}
TICKETS = {**DIRECT, "evidence": {"close": {"field": "ref", "pattern": "^[A-Z]+-\\d+$", "required": True}}}


def world(tmp_path, profile=DIRECT):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    return root, ledger, shipped_direct(root, ledger)


def first_commit(root):
    return git(root, "rev-list", "--max-parents=0", "HEAD")


def test_a_walk_over_a_build_containing_the_work_is_recorded(tmp_path):
    root, ledger, row = world(tmp_path)
    walked = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="opened the page",
                            saw="the export button works")
    assert walked.state == "walked"
    assert walked.history[-1]["evidence"]["deployed"].startswith("not asked")


def test_a_walk_over_a_build_without_the_work_is_refused(tmp_path):
    root, ledger, row = world(tmp_path)
    with pytest.raises(MoveRefused, match="does not contain the shipped commit"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build=first_commit(root), steps="s", saw="s")


def test_a_walk_must_be_over_the_deployed_revision(tmp_path):
    root = repo_with_origin(tmp_path)
    initial = first_commit(root)
    profile = {**DIRECT, "deploy": {"revision_command": f"{sys.executable} -c \"print('{initial}')\""}}
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    shipped_direct(root, ledger)
    with pytest.raises(MoveRefused, match="walk what is deployed"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="s")


def test_a_walk_records_steps_and_what_was_seen(tmp_path):
    root, ledger, row = world(tmp_path)
    with pytest.raises(MoveRefused, match="--steps"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="", saw="fine")


def test_a_required_judge_walks_before_close(tmp_path):
    root, ledger, row = world(tmp_path, {**DIRECT, "judge": {"required": True}})
    with pytest.raises(MoveRefused, match="not legal from `shipped`"):
        judging.close(ledger, actor(ledger, OWNER), "feat/x")
    judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="s")
    assert judging.close(ledger, actor(ledger, OWNER), "feat/x").state == "closed"


def test_close_is_the_authors(tmp_path):
    root, ledger, row = world(tmp_path)
    with pytest.raises(MoveRefused, match="the author closes"):
        judging.close(ledger, actor(ledger, "minor session 1"), "feat/x")
    assert judging.close(ledger, actor(ledger, OWNER), "feat/x").state == "closed"


def test_a_ticket_the_profile_requires_is_checked_or_its_absence_explained(tmp_path):
    root, ledger, row = world(tmp_path, TICKETS)
    with pytest.raises(MoveRefused, match="--why"):
        judging.close(ledger, actor(ledger, OWNER), "feat/x")
    with pytest.raises(MoveRefused, match="does not match"):
        judging.close(ledger, actor(ledger, OWNER), "feat/x", ref="lin 12")
    closed = judging.close(ledger, actor(ledger, OWNER), "feat/x", ref="LIN-12")
    assert (closed.state, closed.ref) == ("closed", "LIN-12")


def test_close_without_a_ticket_records_why(tmp_path):
    root, ledger, row = world(tmp_path, TICKETS)
    closed = judging.close(ledger, actor(ledger, OWNER), "feat/x", why="a fleet chore, no ticket")
    assert closed.history[-1]["evidence"]["without_ref"] == "a fleet chore, no ticket"


def test_broke_files_the_fix_row_for_the_author(tmp_path):
    root, ledger, row = world(tmp_path)
    with pytest.raises(MoveRefused, match="--where"):
        judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="", saw="nothing happens")
    broken, fix = judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="Settings > Export",
                                saw="the button does nothing")
    assert (broken.state, broken.broken) == ("shipped", "Settings > Export")
    assert (fix.state, fix.branch, fix.owner, fix.fixes, fix.tree) == ("claimed", "fix/x", OWNER, broken.id, "")


def test_a_broken_row_is_neither_walked_nor_closed_until_its_fix_ships(tmp_path):
    root, ledger, row = world(tmp_path)
    judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="Settings > Export", saw="nothing")
    with pytest.raises(MoveRefused, match="broke at Settings > Export"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="s")
    with pytest.raises(MoveRefused, match="broke at Settings > Export"):
        judging.close(ledger, actor(ledger, OWNER), "feat/x")


def test_the_fix_row_is_cut_shipped_and_then_the_walk_clears_the_break(tmp_path):
    root, ledger, row = world(tmp_path)
    broken, fix = judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="Export", saw="nothing")
    cut = tree_mod.cut(ledger, actor(ledger, OWNER), "fix/x", tmp_path / "fix-tree")
    assert cut.id == fix.id and cut.tree
    tip = commit(tmp_path / "fix-tree", "fix the export", "fix.txt")
    handover.hand(ledger, actor(ledger, OWNER), "fix/x")
    reading.take(ledger, actor(ledger, "review session 1"), "fix/x")
    reading.accept(ledger, actor(ledger, "review session 1"), "fix/x", reviewed=tip)
    delivery.queue(ledger, actor(ledger, SENDER), "fix/x")
    merge(root, "fix/x")
    delivery.land(ledger, actor(ledger, SENDER), "fix/x")
    git(root, "push", "-q", "origin", "main")
    delivery.ship(ledger, actor(ledger, SENDER), "fix/x")
    walked = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="works now")
    assert (walked.state, walked.broken) == ("walked", "")
    assert judging.close(ledger, actor(ledger, OWNER), "feat/x").state == "closed"


JUDGED = {**DIRECT, "judge": {"required": True}}


def part_and_whole(tmp_path):
    from flotilla.ledger import core
    from ledgerkit import branch
    root, ledger, part = world(tmp_path, JUDGED)
    branch(root, "feat/whole", "builds on the part")
    core.claim(ledger, actor(ledger, OWNER), "feat/whole", requires=[part.id])
    return root, ledger, part


def test_a_part_is_not_walked_while_the_row_building_on_it_is_not_shipped(tmp_path):
    root, ledger, part = part_and_whole(tmp_path)
    with pytest.raises(MoveRefused, match="feat/whole.*walkable"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="w")


def test_the_judge_holds_no_move_on_a_part_yet(tmp_path):
    from flotilla.ledger import views
    root, ledger, part = part_and_whole(tmp_path)
    rows = ledger.rows()
    shipped = next(row for row in rows.values() if row.branch == "feat/x")
    assert views.who_moves(shipped, JUDGED, rows) == ""
    assert views.who_moves(shipped, JUDGED) == views.JUDGE   # without rows, as before


def test_the_orchestrator_marks_a_part_walkable_on_its_own(tmp_path):
    from flotilla.ledger import steering
    root, ledger, part = part_and_whole(tmp_path)
    steering.walkable(ledger, actor(ledger, "orchestrator 1"), "feat/x", why="the logic has its own CLI")
    walked = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="w")
    assert walked.state == "walked"


def test_a_lone_shipped_row_is_the_judges_to_walk(tmp_path):
    from flotilla.ledger import views
    root, ledger, row = world(tmp_path, JUDGED)
    rows = ledger.rows()
    assert views.who_moves(rows[row.id], JUDGED, rows) == views.JUDGE


def test_a_row_nothing_requires_is_walked_as_before(tmp_path):
    root, ledger, row = world(tmp_path, JUDGED)
    assert judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="w").state == "walked"


def test_a_part_waits_for_the_whole_chain_building_on_it_not_only_the_next_link(tmp_path):
    import dataclasses

    from flotilla.ledger import core, views
    from ledgerkit import branch
    root, ledger, part = world(tmp_path, JUDGED)
    branch(root, "feat/mid", "builds on the part")
    mid = core.claim(ledger, actor(ledger, OWNER), "feat/mid", requires=[part.id])
    branch(root, "feat/top", "builds on the middle, holds the entry point")
    core.claim(ledger, actor(ledger, OWNER), "feat/top", requires=[mid.id])
    rows = ledger.rows()
    rows[mid.id] = dataclasses.replace(rows[mid.id], state="shipped")   # the middle link already shipped
    assert [row.branch for row in views.pending_dependents(rows, rows[part.id], JUDGED)] == ["feat/top"]
    assert views.who_moves(rows[part.id], JUDGED, rows) == ""


def test_the_judge_records_no_wait_on_a_part_it_does_not_walk_yet(tmp_path):
    root, ledger, part = part_and_whole(tmp_path)
    with pytest.raises(MoveRefused, match="records a wait"):
        handover.wait(ledger, actor(ledger, JUDGE), "feat/x", on="the person", why="no build")


def test_a_walkable_mark_is_taken_back_and_the_part_waits_again(tmp_path):
    from flotilla.ledger import steering
    root, ledger, part = part_and_whole(tmp_path)
    steering.walkable(ledger, actor(ledger, "orchestrator 1"), "feat/x", why="the logic has its own CLI")
    cleared = steering.walkable(ledger, actor(ledger, "orchestrator 1"), "feat/x", clear=True)
    assert cleared.walkable == ""
    with pytest.raises(MoveRefused, match="feat/whole"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="w")


def test_the_walkable_cli_takes_the_mark_back(tmp_path):
    from flotilla import cli
    parsed = cli.build_parser().parse_args(["work", "walkable", "feat/x", "--clear"])
    assert parsed.clear and not parsed.why


def test_the_broken_row_is_walked_again_once_a_settling_row_delivered_the_fix(tmp_path):
    from flotilla.ledger import core
    root, ledger, row = world(tmp_path)
    broken, fix = judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="Export", saw="nothing")
    shipped_direct(root, ledger, "fix/other")   # the fix, delivered by another row
    core.release(ledger, actor(ledger, OWNER), fix.branch, settled_by="fix/other")
    walked = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="works now")
    assert (walked.state, walked.broken) == ("walked", "")


def test_a_part_may_be_broken_while_the_whole_is_not_shipped(tmp_path):
    root, ledger, part = part_and_whole(tmp_path)
    broken, fix = judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="the storm wall", saw="a seam")
    assert broken.broken == "the storm wall" and fix.fixes == broken.id


def test_a_fix_branch_drops_the_type_prefix(tmp_path):
    root, ledger, row = world(tmp_path)
    _, fix = judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="start", saw="nothing")
    assert fix.branch == "fix/x"


def test_a_fix_branch_name_is_numbered_when_taken(tmp_path):
    from ledgerkit import branch
    root, ledger, row = world(tmp_path)
    branch(root, "fix/x", "someone else's fix")
    rows = ledger.rows()
    assert judging.fix_branch_name(rows, "feat/x") == "fix/x"          # the row table does not know the branch
    with pytest.raises(MoveRefused, match="already exists"):
        judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="start", saw="nothing")


def broken_world(tmp_path):
    root, ledger, row = world(tmp_path, JUDGED)
    judging.broke(ledger, actor(ledger, JUDGE), "feat/x", where="start", saw="stale preview")
    return root, ledger


def test_the_judge_takes_a_false_broke_back_once_the_fix_row_is_released(tmp_path):
    from flotilla.ledger import core
    root, ledger = broken_world(tmp_path)
    core.release(ledger, actor(ledger, OWNER), "fix/x", why="false broke: a stale preview")
    row = judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why="walked a foreign preview on port 4173")
    assert (row.state, row.broken) == ("shipped", "")
    assert row.history[-1]["evidence"]["why"].startswith("walked a foreign")
    walked = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="the eclipse")
    assert walked.state == "walked"


def test_unbroke_refuses_while_a_fix_row_is_open(tmp_path):
    root, ledger = broken_world(tmp_path)
    with pytest.raises(MoveRefused, match="fix/x.*release it first"):
        judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why="false")
    assert next(r for r in ledger.rows().values() if r.branch == "feat/x").broken == "start"


def test_unbroke_needs_a_reason_and_a_broken_row(tmp_path):
    root, ledger, row = world(tmp_path, JUDGED)
    with pytest.raises(MoveRefused, match="is not broken"):
        judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why="x")
    with pytest.raises(MoveRefused, match="--why"):
        judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why=" ")


def test_only_a_post_that_may_unbroke_makes_the_move(tmp_path):
    root, ledger = broken_world(tmp_path)
    with pytest.raises(MoveRefused):
        judging.unbroke(ledger, actor(ledger, OWNER), "feat/x", why="x")


def test_the_unbroke_cli_needs_a_reason(tmp_path):
    from flotilla import cli
    parsed = cli.build_parser().parse_args(["work", "unbroke", "feat/x", "--why", "a foreign preview"])
    assert (parsed.branch, parsed.why) == ("feat/x", "a foreign preview")
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["work", "unbroke", "feat/x"])


def test_a_row_claimed_only_after_a_shipped_row_does_not_make_it_a_part(tmp_path):
    from flotilla.ledger import core, views
    from ledgerkit import branch
    root, ledger, row = world(tmp_path, JUDGED)
    branch(root, "tool/measure-x", "records the shipped work, never ships")
    core.claim(ledger, actor(ledger, OWNER), "tool/measure-x", after=[row.id])
    rows = ledger.rows()
    assert views.pending_dependents(rows, rows[row.id], JUDGED) == []
    assert views.who_moves(rows[row.id], JUDGED, rows) == views.JUDGE
    walked = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build="main", steps="s", saw="w")
    assert walked.state == "walked"


def test_unbroke_with_a_delivered_fix_row_says_release_or_close_it(tmp_path):
    root, ledger = broken_world(tmp_path)
    cut = tmp_path / "fix-tree"
    tree_mod.cut(ledger, actor(ledger, OWNER), "fix/x", cut)
    tip = commit(cut, "fix the start", "fix.txt")
    handover.hand(ledger, actor(ledger, OWNER), "fix/x")
    reading.take(ledger, actor(ledger, "review session 1"), "fix/x")
    reading.accept(ledger, actor(ledger, "review session 1"), "fix/x", reviewed=tip)
    delivery.queue(ledger, actor(ledger, SENDER), "fix/x")
    merge(root, "fix/x")
    delivery.land(ledger, actor(ledger, SENDER), "fix/x")
    git(root, "push", "-q", "origin", "main")
    delivery.ship(ledger, actor(ledger, SENDER), "fix/x")
    with pytest.raises(MoveRefused, match=r"fix/x.*release or close it"):
        judging.unbroke(ledger, actor(ledger, JUDGE), "feat/x", why="false")


def _deployed_from_file(tmp_path):
    path = tmp_path / "deployed.txt"
    command = f"{sys.executable} -c \"print(open(r'{path}').read().strip())\""
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**DIRECT, "deploy": {"revision_command": command}})
    path.write_text(first_commit(root), encoding="utf-8")
    shipped_direct(root, ledger)
    return root, ledger, path


def test_a_build_that_differs_from_the_deployed_only_in_the_fleets_rules_is_the_same_build(tmp_path):
    """Twosuns field test of 0.6.7, W6: a profile commit moved trunk, and the judge's walk of the build before it was
    refused - one rebuild and one walk spent on two lines of `.flotilla/project.toml`."""
    root, ledger, path = _deployed_from_file(tmp_path)
    walked_on = git(root, "rev-parse", "main")
    (root / ".flotilla").mkdir(exist_ok=True)
    (root / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    git(root, "add", ".flotilla")
    commit(root, "profile")
    path.write_text(git(root, "rev-parse", "main"), encoding="utf-8")
    row = judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build=walked_on, steps="s", saw="s")
    assert row.state == "walked" and ".flotilla/" in row.history[-1]["evidence"]["deployed"]


def test_a_build_that_differs_from_the_deployed_in_the_product_is_still_refused(tmp_path):
    root, ledger, path = _deployed_from_file(tmp_path)
    walked_on = git(root, "rev-parse", "main")
    (root / ".flotilla").mkdir(exist_ok=True)
    (root / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    (root / "app.txt").write_text("changed\n", encoding="utf-8")
    git(root, "add", ".")
    commit(root, "profile and product")
    path.write_text(git(root, "rev-parse", "main"), encoding="utf-8")
    with pytest.raises(MoveRefused, match="walk what is deployed"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build=walked_on, steps="s", saw="s")


def test_a_product_file_renamed_into_the_rules_is_not_rules_only(tmp_path):
    """Review of 0.6.9, I2: git's rename detection listed only `.flotilla/app.txt` for a rename out of the product,
    and the walk passed a build whose product had lost a file."""
    root, ledger, path = _deployed_from_file(tmp_path)
    (root / "app.txt").write_text("the product\n" * 20, encoding="utf-8")
    git(root, "add", "app.txt")
    commit(root, "product file")
    path.write_text(git(root, "rev-parse", "main"), encoding="utf-8")   # deployed: the product has app.txt
    (root / ".flotilla").mkdir(exist_ok=True)
    git(root, "mv", "app.txt", ".flotilla/app.txt")
    commit(root, "move it out of the product")
    walked_on = git(root, "rev-parse", "main")                          # walked: the product lost it
    with pytest.raises(MoveRefused, match="walk what is deployed"):
        judging.walked(ledger, actor(ledger, JUDGE), "feat/x", build=walked_on, steps="s", saw="s")
