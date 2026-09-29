import multiprocessing

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import names
from flotilla.posts import TEMPLATE_DIR, load_post

REVIEWER = load_post(TEMPLATE_DIR / "reviewer.md")
MAIN = load_post(TEMPLATE_DIR / "main.md")


def test_a_name_carries_its_number():
    assert names.number_of(REVIEWER, "review session 12") == 12
    assert names.number_of(REVIEWER, "main session 12") is None


def test_names_are_issued_in_order_and_never_twice(tmp_path):
    store = LocalLogStore(tmp_path)
    assert names.next_names(REVIEWER, 2, taken=set(), store=store, reserve=True) == ["review session 1",
                                                                                     "review session 2"]
    assert names.next_names(REVIEWER, 1, taken=set(), store=store, reserve=True) == ["review session 3"]


def test_a_new_name_is_above_every_live_or_known_number(tmp_path):
    store = LocalLogStore(tmp_path)
    found = names.next_names(REVIEWER, 1, taken={"review session 43", "main session 90"}, store=store, reserve=True)
    assert found == ["review session 44"]


def test_a_dry_run_issues_nothing(tmp_path):
    store = LocalLogStore(tmp_path)
    assert names.next_names(MAIN, 1, taken=set(), store=store, reserve=False) == ["main session 1"]
    assert names.next_names(MAIN, 1, taken=set(), store=store, reserve=True) == ["main session 1"]


def _issue(path, queue):
    queue.put(names.next_names(REVIEWER, 1, taken=set(), store=LocalLogStore(path), reserve=True)[0])


def test_two_callers_never_get_the_same_name(tmp_path):
    queue = multiprocessing.Queue()
    workers = [multiprocessing.Process(target=_issue, args=(tmp_path, queue)) for _ in range(6)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
    issued = [queue.get(timeout=5) for _ in workers]
    assert len(set(issued)) == 6


def test_numbering_after_a_live_session_elsewhere_says_so(tmp_path):
    said = names.numbered_after(REVIEWER, taken={"review session 37"}, live={"review session 37"},
                                store=LocalLogStore(tmp_path))
    assert "review session 37" in said and "alive on this machine" in said and "machine-wide" in said


def test_numbering_after_a_name_spawn_issued_says_so(tmp_path):
    store = LocalLogStore(tmp_path)
    names.next_names(REVIEWER, 1, taken=set(), store=store, reserve=True)
    assert "issued by an earlier spawn" in names.numbered_after(REVIEWER, taken=set(), live=set(), store=store)


def test_a_fresh_post_says_nothing(tmp_path):
    assert names.numbered_after(REVIEWER, taken={"main session 9"}, live=set(), store=LocalLogStore(tmp_path)) == ""
