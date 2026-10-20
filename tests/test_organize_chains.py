from flotilla.organize import chains, items
from flotilla.organize.github import Pull


def p(number, branch, base, head="a" * 40):
    return Pull(number=number, branch=branch, head=head, base=base, author="me", draft=False, fork=False,
                title=f"t{number}", body="")


P42 = p(42, "docs/relations-model", "main")
P43 = p(43, "chore/serena-enforcement", "docs/relations-model")
P44 = p(44, "docs/value-axes", "chore/serena-enforcement")


def never(branch):
    return False


def test_the_test_bed_chain_is_one_item_bottom_first():
    found, singles, notes = chains.chains([P44, P42, P43], "main", trunk_contains=never)
    assert [[item.number for item in chain] for chain in found] == [[42, 43, 44]]
    assert singles == [] and notes == []


def test_a_lone_pull_request_is_single():
    found, singles, _ = chains.chains([P42], "main", trunk_contains=never)
    assert found == [] and singles == [P42]


def test_a_forked_chain_gets_no_shapes():
    other = p(45, "docs/other", "docs/relations-model")
    found, _, notes = chains.chains([P42, P43, other], "main", trunk_contains=never)
    assert len(found) == 1 and chains.is_forked(found[0], [P42, P43, other])
    assert chains.offered(found[0], method="merge", forked=True, off_trunk=False) == []


def test_squash_offers_only_one_work_whole():
    found, _, _ = chains.chains([P42, P43, P44], "main", trunk_contains=never)
    assert chains.offered(found[0], method="squash", forked=False, off_trunk=False) == ["whole"]
    assert chains.offered(found[0], method="rebase", forked=False, off_trunk=False) == ["whole"]


def test_merge_commits_offer_every_shape():
    found, _, _ = chains.chains([P42, P43, P44], "main", trunk_contains=never)
    assert chains.offered(found[0], method="merge", forked=False, off_trunk=False) == list(chains.SHAPES)


def test_an_unknown_merge_method_is_treated_as_squash():
    found, _, _ = chains.chains([P42, P43, P44], "main", trunk_contains=never)
    assert chains.offered(found[0], method="", forked=False, off_trunk=False) == ["whole"]


def test_a_bottom_base_merged_into_trunk_is_kept_as_a_chain():
    """A lower pull request merged without deleting its branch: its branch is in trunk, the chain still stands."""
    found, _, _ = chains.chains([P43, P44], "main", trunk_contains=lambda branch: branch == "docs/relations-model")
    assert [[item.number for item in chain] for chain in found] == [[43, 44]]
    assert chains.bottom_base_in_trunk(found[0], "main", trunk_contains=lambda b: b == "docs/relations-model")


def test_a_chain_off_trunk_is_said():
    found, _, _ = chains.chains([P43, P44], "main", trunk_contains=never)
    assert chains.is_off_trunk(found[0], "main", trunk_contains=never)


def test_seen_changes_when_a_head_moves():
    one = items.Item(key="pr:42", kind="pr", phase="after", title="", lines=[], facts={"head": "a" * 40})
    two = items.Item(key="pr:42", kind="pr", phase="after", title="", lines=[], facts={"head": "b" * 40})
    assert items.seen(one) != items.seen(two) and len(items.seen(one)) == 16


def test_shown_keeps_author_text_on_one_safe_line():
    assert items.shown("a `title`\nwith\x1b[31m colour " + "x" * 100) == items.shown("a `title`\nwith\x1b[31m colour "
                                                                                    + "x" * 100)
    said = items.shown("a `title`\nwith colour " + "x" * 100)
    assert "\n" not in said and "`" not in said and len(said) <= 60
