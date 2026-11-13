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
