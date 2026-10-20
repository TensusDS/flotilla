import pytest

from flotilla.onboard.questions import AnswerError
from flotilla.organize import questions, store
from flotilla.organize.items import Item, seen
from flotilla.organize.walk import Walk

SHAPES = ["separate", "parts", "whole", "claude"]


def chain(key="chain:42-43-44", offered=SHAPES, kind="chain"):
    return Item(key=key, kind=kind, phase="after", title="a chain of 3 pull requests", lines=["#42", "#43", "#44"],
                facts={"pulls": [{"number": 42}]}, offered=list(offered))


def item(key, kind, **facts):
    return Item(key=key, kind=kind, phase="after", title=key, lines=[], facts=facts)


def everything():
    return Walk(phase="after", items=[
        chain(), chain("stack:7", kind="stack"), item("pr:50", "pr"), item("branch:feat/x", "branch"),
        item("trunk:main", "trunk"), item("edits:/app", "edits", tree="/app"), item("cache:.serena/", "cache"),
        item("onboard:chore/flotilla-onboard", "onboard")])


def ids(page):
    return [question["id"] for question in page]


def answer_all_first(walk, data):
    for found in walk.items:
        first = next(q for q in questions.all_questions(walk, data) if q["id"] == found.key)
        questions.record(walk, data, found.key, [first["options"][1]["value"]])   # the second option: never "take"


def test_every_question_has_two_to_four_options_and_none_recommended():
    walk, data = everything(), store.empty()
    for question in questions.all_questions(walk, data):
        assert 2 <= len(question["options"]) <= 4, question["id"]
        assert len(question["header"]) <= 12, question["header"]
        assert not any("recommend" in option["label"].lower() for option in question["options"])
        assert question["free_text"] is True


def test_a_round_holds_at_most_four():
    walk = Walk(phase="after", items=[item(f"branch:b{n}", "branch") for n in range(10)])
    assert len(questions.next_round(walk, store.empty())) == questions.MAX_PER_ROUND


def test_a_chain_asks_take_first_and_its_shape_next():
    walk, data = Walk(phase="after", items=[chain()]), store.empty()
    assert ids(questions.next_round(walk, data)) == ["chain:42-43-44"]
    questions.record(walk, data, "chain:42-43-44", ["take"])
    page = questions.next_round(walk, data)
    assert ids(page) == ["chain:42-43-44:shape"]
    assert [option["value"] for option in page[0]["options"]] == SHAPES
    questions.record(walk, data, "chain:42-43-44:shape", ["separate"])
    assert questions.next_round(walk, data) == [] and questions.done(walk, data)


def test_a_chain_offered_only_one_work_whole_takes_that_shape_without_asking():
    walk, data = Walk(phase="after", items=[chain(offered=["whole"])]), store.empty()
    questions.record(walk, data, "chain:42-43-44", ["take"])
    assert questions.next_round(walk, data) == [] and questions.done(walk, data)
    assert questions.shape_of(walk.items[0], data) == "whole"


def test_a_chain_offered_no_shape_cannot_be_taken():
    walk = Walk(phase="after", items=[chain(offered=[])])
    first = questions.next_round(walk, store.empty())[0]
    assert [option["value"] for option in first["options"]] == ["ignore", "ignore-rest"]


def test_ignore_the_rest_settles_every_unanswered_item():
    walk, data = everything(), store.empty()
    questions.record(walk, data, "pr:50", ["take"])
    questions.record(walk, data, "branch:feat/x", ["ignore-rest"])
    assert data["answers"]["pr:50"]["value"] == "take"
    assert set(data["ignored"]) == {found.key for found in walk.items} - {"pr:50"}
    assert questions.done(walk, data)


def test_ignore_records_what_the_item_was():
    walk, data = everything(), store.empty()
    questions.record(walk, data, "pr:50", ["ignore"])
    assert data["ignored"]["pr:50"] == seen(walk.items[2])


def test_a_native_stack_says_taking_it_in_unstacks_it():
    stack = chain("stack:7", kind="stack")
    stack.title = "a native GitHub stack - taking it in unstacks it on GitHub"
    first = questions.next_round(Walk(phase="after", items=[stack]), store.empty())[0]
    assert "unstacks it on GitHub" in first["question"]


def test_claude_works_it_out_asks_the_proposal_with_the_proposed_shape_first():
    walk, data = Walk(phase="after", items=[chain()]), store.empty()
    questions.record(walk, data, "chain:42-43-44", ["take"])
    questions.record(walk, data, "chain:42-43-44:shape", ["claude"])
    assert questions.next_round(walk, data) == []
    assert not questions.done(walk, data)
    assert questions.waiting(walk, data) == ["chain:42-43-44: read it (`flotilla organize show chain:42-43-44`) and "
                                             "propose a shape (`flotilla organize propose chain:42-43-44 --shape "
                                             "<shape> --why \"...\"`)"]
    data["proposals"]["chain:42-43-44"] = {"shape": "whole", "why": "one feature in three steps"}
    page = questions.next_round(walk, data)
    assert ids(page) == ["chain:42-43-44:proposal"]
    options = page[0]["options"]
    assert options[0]["value"] == "whole" and options[0]["label"].endswith("(proposed)")
    assert "one feature in three steps" in options[0]["description"]
    assert [option["value"] for option in options] == ["whole", "separate", "parts"]
    questions.record(walk, data, "chain:42-43-44:proposal", ["parts"])
    assert questions.shape_of(walk.items[0], data) == "parts" and questions.done(walk, data)


def test_own_words_wait_for_steps_then_a_yes():
    walk, data = Walk(phase="after", items=[chain()]), store.empty()
    questions.record(walk, data, "chain:42-43-44", ["#43", "is", "stale,", "close", "it"])
    assert data["answers"]["chain:42-43-44"]["words"] == "#43 is stale, close it"
    assert not questions.done(walk, data)
    data["proposals"]["chain:42-43-44"] = {"steps": [{"do": "close", "pr": 43}]}
    assert ids(questions.next_round(walk, data)) == ["chain:42-43-44:steps"]
    questions.record(walk, data, "chain:42-43-44:steps", ["yes"])
    assert questions.done(walk, data)


def test_edits_at_claudes_discretion_wait_for_a_plan_then_a_yes():
    walk, data = Walk(phase="after", items=[item("edits:/app", "edits", tree="/app")]), store.empty()
    questions.record(walk, data, "edits:/app", ["discretion"])
    assert questions.next_round(walk, data) == [] and not questions.done(walk, data)
    data["proposals"]["edits:/app"] = {"plan": [{"branch": "fix/a", "paths": ["a.py"]}]}
    page = questions.next_round(walk, data)
    assert ids(page) == ["edits:/app:plan"] and "fix/a" in page[0]["question"]


def test_a_trees_yes_comes_before_its_items():
    edits = item("edits:/other", "edits", tree="/other", needs_yes=True)
    walk, data = Walk(phase="after", items=[edits]), store.empty()
    assert ids(questions.next_round(walk, data)) == ["tree:/other"]
    questions.record(walk, data, "tree:/other", ["yes"])
    assert data["trees"] == ["/other"]
    assert ids(questions.next_round(walk, data)) == ["edits:/other"]


def test_a_tree_answered_no_leaves_its_items_alone():
    edits = item("edits:/other", "edits", tree="/other", needs_yes=True)
    walk, data = Walk(phase="after", items=[edits]), store.empty()
    questions.record(walk, data, "tree:/other", ["no"])
    assert questions.next_round(walk, data) == [] and questions.done(walk, data)


def test_an_answer_to_a_question_that_does_not_apply_is_refused():
    walk, data = Walk(phase="after", items=[chain()]), store.empty()
    with pytest.raises(AnswerError):
        questions.record(walk, data, "chain:42-43-44:shape", ["separate"])
    with pytest.raises(AnswerError):
        questions.record(walk, data, "pr:99", ["take"])


def test_a_label_is_not_a_value():
    walk, data = Walk(phase="after", items=[chain()]), store.empty()
    label = questions.next_round(walk, data)[0]["options"][0]["label"]
    with pytest.raises(AnswerError, match="label"):
        questions.record(walk, data, "chain:42-43-44", [label])
