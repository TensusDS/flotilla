import stat

from flotilla.organize import store


def test_round_trip(tmp_path):
    data = store.load(tmp_path, "k")
    data["answers"]["pr:42"] = {"value": "take", "seen": "abc"}
    data["ignored"]["branch:x"] = "def"
    data["publish_named"] = ["a" * 40]
    store.save(tmp_path, "k", data)
    again = store.load(tmp_path, "k")
    assert again["answers"]["pr:42"] == {"value": "take", "seen": "abc"}
    assert again["ignored"] == {"branch:x": "def"}
    assert store.publish_named(tmp_path, "k") == ["a" * 40]


def test_a_missing_or_broken_file_is_empty(tmp_path):
    assert store.load(tmp_path, "k") == store.empty()
    path = tmp_path / "organize" / "k.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert store.load(tmp_path, "k") == store.empty()
    path.write_text('["a list"]', encoding="utf-8")
    assert store.load(tmp_path, "k") == store.empty()


def test_forget_drops_an_items_answers_and_proposal(tmp_path):
    data = store.empty()
    data["answers"].update({"chain:42-43": {"value": "take"}, "chain:42-43:shape": {"value": "claude"},
                            "chain:42-430": {"value": "take"}})
    data["proposals"]["chain:42-43"] = {"shape": "whole"}
    store.forget(data, "chain:42-43")
    assert data["answers"] == {"chain:42-430": {"value": "take"}}
    assert data["proposals"] == {}


def test_the_file_is_the_users_alone(tmp_path):
    store.save(tmp_path, "k", store.empty())
    mode = stat.S_IMODE((tmp_path / "organize" / "k.json").stat().st_mode)
    assert mode & 0o077 == 0


def test_numbers_named_by_the_store(tmp_path):
    data = store.empty()
    data["answers"].update({"chain:42-43": {"value": "take"}, "pr:7:steps": {"value": "yes"}, "branch:x": {}})
    data["proposals"]["stack:9"] = {"shape": "whole", "pulls": [50, 51]}
    assert store.numbers(data) == {7, 42, 43, 50, 51}
