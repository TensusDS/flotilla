"""Work taken into the ledger from before flotilla (spec 2026-10-19-organize-design, section 5): the row's pinned
post, its adoption point, and the take-in move itself."""

import pytest

from flotilla.ledger import batch, findings
from flotilla.ledger.actor import Actor
from flotilla.ledger.model import Row
from flotilla.ledger.transitions import moves_from
from ledgerkit import PROFILE, commit, git, make_ledger, repo_with_origin

DIRECT = {**PROFILE, "flow": {"mode": "direct"}}
PERSON = Actor("the person", None, "person", "the person")


def ledger_with(tmp_path, review="every", profile=None):
    root = repo_with_origin(tmp_path)
    data = profile or {**PROFILE, "review": {"depth": review}}
    return root, make_ledger(root, tmp_path / "state", profile=data)


def write_row(ledger, **fields):
    """A row as the take-in move writes one, appended directly (the move is tested below)."""
    state = fields.pop("state", "claimed")
    with ledger.session() as s:
        return s.append(PERSON, f"r{len(s.rows) + 1}", "take-in", state, fields=fields)


def test_a_pinned_post_decides_review_whatever_the_owner(tmp_path):
    """main-only: a minor seat's row skips review - unless the row was taken in and pinned to main."""
    _, ledger = ledger_with(tmp_path, review="main-only")
    plain = Row(id="r1", owner="minor session 1", state="claimed")
    pinned = Row(id="r2", owner="minor session 1", state="claimed", judged_as="main")
    assert "queue" in moves_from("claimed", ledger.profile, ledger.owner_post(plain))
    assert ledger.owner_post(pinned) == "main"
    assert "queue" not in moves_from("claimed", ledger.profile, ledger.owner_post(pinned))


def _older_branch_and_a_trunk_commit(root):
    """`feat/old` forks from the first commit; trunk then gains A, made before flotilla, and is pushed."""
    fork = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "feat/old")
    commit(root, "old work", "old.txt")
    git(root, "checkout", "-q", "main")
    before = commit(root, "made before flotilla", "a.txt")
    git(root, "push", "-q", "origin", "main")
    return fork, before


def test_trunk_commits_before_the_adoption_point_are_not_direct_commits(tmp_path):
    root, ledger = ledger_with(tmp_path, profile=DIRECT)
    fork, before = _older_branch_and_a_trunk_commit(root)
    write_row(ledger, branch="feat/old", owner="(taken in)", base=fork, adopted_at=before)
    named = [item["sha"] for item in findings.findings(ledger) if item["kind"] == "direct_commit"]
    assert before not in named


def test_a_far_back_base_does_not_vouch_for_trunk_merged_in_below_the_adoption_point(tmp_path):
    root, ledger = ledger_with(tmp_path, profile=DIRECT)
    fork, before = _older_branch_and_a_trunk_commit(root)
    git(root, "checkout", "-q", "feat/old")
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "merge", "-q", "--no-ff", "-m", "merge trunk",
        "main")
    tip = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "main")
    write_row(ledger, state="accepted", branch="feat/old", owner="(taken in)", base=fork, adopted_at=before,
              verdict=tip, reader="review session 1")
    assert before not in batch.Accounting(ledger, ledger.rows()).read
    assert tip in batch.Accounting(ledger, ledger.rows()).read


def test_the_new_fields_fold_into_the_row(tmp_path):
    _, ledger = ledger_with(tmp_path)
    row = write_row(ledger, branch="feat/a", owner="(taken in)", base="b" * 40, judged_as="main",
                    adopted_at="a" * 40, retarget="main", closes=[42, 43])
    assert (row.judged_as, row.adopted_at, row.retarget, row.closes) == ("main", "a" * 40, "main", [42, 43])


@pytest.mark.parametrize("name", ["judged_as", "adopted_at", "retarget", "closes"])
def test_every_new_field_is_a_row_field(name):
    from flotilla.ledger.model import ROW_FIELDS
    assert name in ROW_FIELDS
