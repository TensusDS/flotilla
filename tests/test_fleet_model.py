"""The fleet's model: names, families, where the default comes from, and the local note (fleet model design, 5)."""

import datetime as dt

import pytest

from flotilla.fleet import model

NOW = dt.datetime(2026, 11, 13, 20, 0, tzinfo=dt.timezone.utc)


def test_family_matches_ids_and_variants():
    assert model.same("opus", "claude-opus-5-5") and model.same("opus[1m]", "opus") and model.same("opusplan", "opus")
    assert model.same("Opus", "opus")
    assert not model.same("claude-opus-5-5", "claude-opus-4-1") and not model.same("opus", "fable")


def test_family_of_an_unknown_name_is_the_name():
    assert model.family("my-proxy-model") == "my-proxy-model"
    assert model.family("claude-fable-5-2") == "fable"


def test_legacy_words_name_no_default(tmp_path):
    for word in ("one", "reviewer-strongest", ""):
        assert model.default({"fleet": {"model": word}}, state=tmp_path, main=tmp_path / "p") == ("", "")
    assert model.default({}, state=tmp_path, main=tmp_path / "p") == ("", "")


def test_a_named_profile_is_the_default(tmp_path):
    assert model.default({"fleet": {"model": "opus"}}, state=tmp_path, main=tmp_path / "p") == ("opus", "profile")


def test_the_note_answers_until_trunk_names_one(tmp_path):
    main = tmp_path / "p"
    model.write_note(tmp_path, main, "opus", replaced="one", now=NOW)
    got = model.default({"fleet": {"model": "one"}}, state=tmp_path, main=main)
    assert got == ("opus", "local note (not on trunk yet)")


def test_trunk_wins_once_it_names_one(tmp_path):
    main = tmp_path / "p"
    model.write_note(tmp_path, main, "opus", replaced="sonnet", now=NOW)
    assert model.default({"fleet": {"model": "sonnet"}}, state=tmp_path, main=main)[0] == "opus"   # not landed yet
    assert model.default({"fleet": {"model": "haiku"}}, state=tmp_path, main=main) == ("haiku", "profile")
    assert model.note_for(tmp_path, main) is None


def test_a_note_is_kept_per_checkout(tmp_path):
    model.write_note(tmp_path, tmp_path / "a", "opus", replaced="one", now=NOW)
    model.write_note(tmp_path, tmp_path / "b", "sonnet", replaced="one", now=NOW)
    assert model.note_for(tmp_path, tmp_path / "a")["model"] == "opus"
    assert model.note_for(tmp_path, tmp_path / "b")["model"] == "sonnet"


def test_an_unreadable_note_reads_as_none(tmp_path):
    (tmp_path / model.NOTE).write_text("{not json", encoding="utf-8")
    assert model.note_for(tmp_path, tmp_path / "p") is None
    assert model.default({"fleet": {"model": "one"}}, state=tmp_path, main=tmp_path / "p") == ("", "")


def test_strongest_reviewer_keeps_opus():
    assert model.reviewers({"fleet": {"model": "reviewer-strongest"}}) == "opus"
    assert model.reviewers({"fleet": {"model": "opus", "reviewers": "fable"}}) == "fable"
    assert model.reviewers({"fleet": {"model": "opus"}}) == ""


def test_resolved_names_the_default_and_the_reviewers(tmp_path):
    main = tmp_path / "p"
    model.write_note(tmp_path, main, "sonnet", replaced="reviewer-strongest", now=NOW)
    profile = {"fleet": {"model": "reviewer-strongest", "name": "x"}}
    got = model.resolved(profile, state=tmp_path, main=main)
    assert got["fleet"] == {"model": "sonnet", "reviewers": "opus", "name": "x"}
    assert profile["fleet"]["model"] == "reviewer-strongest"   # a copy: the ledger's profile is not changed


def test_resolved_refuses_without_a_default(tmp_path):
    with pytest.raises(model.ModelUnnamed, match="flotilla fleet model"):
        model.resolved({"fleet": {"model": "one"}}, state=tmp_path, main=tmp_path / "p")


import tomllib


BASE = 'schema = 1\n[trunk]\nbranch = "main"\n'


@pytest.mark.parametrize("fleet", [
    '[fleet] # the fleet\nname = "app"\nmodel = "one"\n',
    '[ fleet ]\nname = "app"\nmodel = "one"\n',
    '[fleet]\nname = "app"\n"model" = "one"\n',
    "[fleet]\nname = 'app'\nmodel = 'reviewer-strongest'\n",
    '[fleet]\nname = "app"\nmodel = "one"\ntags = [\n["a"],\n]\n',
    '[fleet]\nname = "app"\nmodel = "one"\nnote = """\n[x]\n"""\n',
])
def test_with_model_writes_valid_toml_or_refuses(fleet):   # review of the 0.32 branch, I2
    text = BASE + fleet
    before = tomllib.loads(text)
    try:
        out = model.with_model(text, "sonnet")
    except model.ProfileEdit:
        return   # refused: the person makes the one-line edit
    after = tomllib.loads(out)
    assert after["fleet"]["model"] == "sonnet"
    rest = {key: value for key, value in after["fleet"].items() if key not in ("model", "reviewers")}
    assert rest == {key: value for key, value in before["fleet"].items() if key != "model"}
    assert {key: value for key, value in after.items() if key != "fleet"} == \
        {key: value for key, value in before.items() if key != "fleet"}
    if before["fleet"]["model"] == "reviewer-strongest":
        assert after["fleet"]["reviewers"] == "opus"


@pytest.mark.parametrize("text", ['fleet = { model = "one" }\n' + BASE, 'fleet.model = "one"\n' + BASE,
                                  BASE + '[[fleet]]\nmodel = "one"\n'])
def test_with_model_refuses_what_it_cannot_edit_safely(text):
    with pytest.raises(model.ProfileEdit, match='model = "sonnet"'):
        model.with_model(text, "sonnet")


def test_with_model_keeps_crlf():
    text = (BASE + '[fleet]\nname = "app"\nmodel = "one"\n').replace("\n", "\r\n")
    out = model.with_model(text, "sonnet")
    assert "\r\n" in out and "\n" not in out.replace("\r\n", "")
    assert tomllib.loads(out)["fleet"]["model"] == "sonnet"


def test_family_of_an_older_id_with_its_version_first():   # review M9
    assert model.family("claude-3-5-sonnet-20241022") == "sonnet"
    assert model.same("claude-3-5-sonnet-20241022", "sonnet")
