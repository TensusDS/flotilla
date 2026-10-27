"""Branch verdicts (gc design, sections 5.2 and 5.3): a branch is judged on its own; the sender's by the ledger's
merge proof, never by a name; a declared trunk never; branches on origin are listed, never deleted."""

from flotilla.gc import branches, refs
from ledgerkit import IDENTITY, PROFILE, actor, commit, git, make_ledger, repo_with_origin


def world(tmp_path, profile=None):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=profile or PROFILE, live=())


def landed(root, name, message="work"):
    """A branch with one commit, merged into trunk and pushed to origin."""
    git(root, "checkout", "-q", "-b", name)
    tip = commit(root, message)
    git(root, "checkout", "-q", "main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-edit", name)
    git(root, "push", "-q", "origin", "HEAD:main")
    return tip


def verdicts(ledger, *, staying=(), ours=()):
    trunks, _ = refs.trunks(ledger)
    found = branches.judge_branches(ledger, trunks, staying_checkouts=set(staying), ours=set(ours))
    return {item.name: item for item in found}


def test_a_landed_fleet_branch_is_deleted(tmp_path):
    root, ledger = world(tmp_path)
    landed(root, "fleet/main-1")
    found = verdicts(ledger)["fleet/main-1"]
    assert found.delete and found.park == []


def test_a_landed_branch_checked_out_in_a_staying_tree_is_kept(tmp_path):
    root, ledger = world(tmp_path)
    landed(root, "fleet/main-1")
    found = verdicts(ledger, staying={"fleet/main-1"})["fleet/main-1"]
    assert not found.delete and "a tree that stays" in found.reason


def test_a_sender_branch_is_judged_by_the_ledger_proof_not_its_name(tmp_path):
    root, ledger = world(tmp_path)
    landed(root, "feat/y")
    merge = git(root, "rev-parse", "HEAD")
    git(root, "branch", "batch/1", merge)
    git(root, "branch", "batch/2", merge)
    with ledger.session() as s:
        s.append(actor(ledger, "sender 1"), "r5", "ship", "shipped", fields={"branch": "feat/y", "merge": merge})
    git(root, "update-ref", "refs/heads/batch/2", git(root, "rev-parse", "HEAD~1"))
    found = verdicts(ledger)
    assert found["batch/1"].delete
    assert not found["batch/2"].delete and found["batch/2"].reason == "flotilla did not make it"


def test_a_squash_shipped_branch_is_deleted_and_its_tip_parked(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "fleet/main-2")
    tip = commit(root, "squashed")
    git(root, "checkout", "-q", "main")
    with ledger.session() as s:
        s.append(actor(ledger, "sender 1"), "r6", "ship", "shipped", fields={"branch": "fleet/main-2", "tip": tip})
    found = verdicts(ledger)["fleet/main-2"]
    assert found.delete and found.park == [tip]


def test_reflog_only_commits_of_a_branch_are_parked(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "fleet/main-3")
    dropped = commit(root, "a trial")
    git(root, "reset", "-q", "--hard", "HEAD~1")
    commit(root, "the work")
    git(root, "checkout", "-q", "main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-edit", "fleet/main-3")
    git(root, "push", "-q", "origin", "HEAD:main")
    found = verdicts(ledger)["fleet/main-3"]
    assert found.delete and found.park == [dropped]


def test_a_declared_trunk_is_never_judged(tmp_path):
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"trunks": ["fleet/line"]}})
    git(root, "branch", "fleet/line")
    assert "fleet/line" not in verdicts(ledger)


def test_an_unlanded_branch_is_kept(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "fleet/main-4")
    commit(root, "not merged")
    git(root, "checkout", "-q", "main")
    found = verdicts(ledger)["fleet/main-4"]
    assert not found.delete and "not on any trunk" in found.reason


def test_origin_that_cannot_be_asked_keeps_every_branch(tmp_path):
    root, ledger = world(tmp_path)
    landed(root, "fleet/main-5")
    git(root, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    found = verdicts(ledger)["fleet/main-5"]
    assert not found.delete and "origin could not be asked" in found.reason


def test_remote_branches_are_listed_not_deleted(tmp_path):
    root, ledger = world(tmp_path)
    landed(root, "feat/z")
    git(root, "push", "-q", "origin", "feat/z")
    git(root, "fetch", "-q", "origin")
    trunks, _ = refs.trunks(ledger)
    assert branches.landed_remote_branches(ledger, trunks) == ["origin/feat/z"]


def test_a_closed_row_that_never_shipped_is_not_trunk(tmp_path):
    """I4: in local flow a row closes after landing on the local trunk only; nothing reached origin."""
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "feat/x")
    tip = commit(root, "work")
    git(root, "checkout", "-q", "main")
    with ledger.session() as s:
        s.append(actor(ledger, "main session 1"), "r9", "close", "closed", fields={"branch": "feat/x", "tip": tip})
    found = verdicts(ledger, ours={"feat/x"})["feat/x"]
    assert not found.delete and "not on any trunk" in found.reason
