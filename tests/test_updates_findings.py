import datetime as dt
import json

from flotilla import doctor
from flotilla.core import claude_state, updates
from flotilla.core.releases import Releases

NEWEST = Releases("0.28.1", "a" * 40, "2026-11-04T12:00:00+00:00")
UNKNOWN = Releases(None, None, "2026-11-04T12:00:00+00:00", "offline")


def entry(ref=None, auto=None, repo="TensusDS/flotilla", kind="github"):
    source = {"source": kind, "repo": repo}
    if ref:
        source["ref"] = ref
    found = {"source": source}
    if auto is not None:
        found["autoUpdate"] = auto
    return found


def statuses(found):
    return [status for status, _, _ in found]


def test_a_user_pin_older_than_the_newest_release_is_a_warning_with_both_ways_out():
    found = claude_state.update_findings(entry("v0.26.0"), "", NEWEST, "0.26.0")
    assert statuses(found) == ["warn"]
    _, detail, fix = found[0]
    assert "v0.26.0" in detail and "0.28.1" in detail
    assert "extraKnownMarketplaces" in detail            # the text Claude Code refuses with, so it can be recognised
    assert "stable" in fix and "v0.28.1" in fix and "/flotilla:update" in fix


def test_a_user_pin_on_the_newest_release_is_ok():
    assert statuses(claude_state.update_findings(entry("v0.28.1"), "", NEWEST, "0.28.1")) == ["ok"]


def test_stable_without_automatic_updates_is_info_and_with_them_ok():
    assert statuses(claude_state.update_findings(entry("stable"), "", NEWEST, "0.28.1")) == ["info"]
    assert statuses(claude_state.update_findings(entry("stable", auto=False), "", NEWEST, "0.28.1")) == ["info"]
    assert statuses(claude_state.update_findings(entry("stable", auto=True), "", NEWEST, "0.28.1")) == ["ok"]


def test_the_default_branch_is_info():
    found = claude_state.update_findings(entry(), "", NEWEST, "0.28.1")
    assert statuses(found) == ["info"] and "main" in found[0][1]


def test_no_user_entry_and_an_older_project_pin_is_info():
    found = claude_state.update_findings(None, "v0.26.0", NEWEST, "0.26.0")
    assert statuses(found) == ["info"] and "0.28.1" in found[0][1]
    assert claude_state.update_findings(None, "v0.28.1", NEWEST, "0.28.1") == []
    assert claude_state.update_findings(None, "", NEWEST, "0.28.1") == []


def test_another_source_is_reported_never_planned():
    found = claude_state.update_findings(entry("v0.1.0", repo="someone/fork"), "", NEWEST, "0.1.0")
    assert statuses(found) == ["info"] and "someone/fork" in found[0][1] and "/flotilla:update" not in found[0][2]


def test_nothing_is_said_when_the_newest_release_is_unknown():
    assert claude_state.update_findings(entry("v0.26.0"), "", UNKNOWN, "0.26.0") == []


def test_user_entry_reads_the_users_settings_and_survives_odd_shapes(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    env = {"CLAUDE_CONFIG_DIR": str(config)}
    assert claude_state.user_entry(env) is None
    (config / "settings.json").write_text("{broken", encoding="utf-8")
    assert claude_state.user_entry(env) is None
    (config / "settings.json").write_text(json.dumps({"extraKnownMarketplaces": ["flotilla"]}), encoding="utf-8")
    assert claude_state.user_entry(env) is None
    (config / "settings.json").write_text(json.dumps({"extraKnownMarketplaces": {"flotilla": entry("v0.26.0")}}),
                                          encoding="utf-8")
    assert claude_state.user_entry(env)["source"]["ref"] == "v0.26.0"


def _machine(tmp_path, ref="v0.26.0"):
    config, state = tmp_path / "config", tmp_path / "state"
    config.mkdir()
    (config / "settings.json").write_text(json.dumps({"extraKnownMarketplaces": {"flotilla": entry(ref)}}),
                                          encoding="utf-8")
    return {"CLAUDE_CONFIG_DIR": str(config), "FLOTILLA_STATE_DIR": str(state)}


def test_the_session_start_line_says_the_warning_once_a_day(tmp_path):
    env = _machine(tmp_path)
    now = dt.datetime(2026, 11, 4, 12, tzinfo=dt.timezone.utc)
    ask = lambda **_: NEWEST                              # noqa: E731
    first = updates.lines(tmp_path, env=env, releases=ask, now=now)
    assert len(first) == 1 and "v0.26.0" in first[0]
    assert updates.lines(tmp_path, env=env, releases=ask, now=now + dt.timedelta(hours=3)) == []
    assert len(updates.lines(tmp_path, env=env, releases=ask, now=now + dt.timedelta(days=1, hours=1))) == 1


def test_the_session_start_line_says_nothing_but_a_warning(tmp_path):
    env = _machine(tmp_path, ref="stable")
    now = dt.datetime(2026, 11, 4, 12, tzinfo=dt.timezone.utc)
    assert updates.lines(tmp_path, env=env, releases=lambda **_: NEWEST, now=now) == []


def test_doctor_lists_every_update_finding(tmp_path):
    env = _machine(tmp_path, ref="stable")
    found = doctor.update_findings(tmp_path, env=env, releases=lambda **_: NEWEST)
    assert [(item.status, item.check) for item in found] == [("info", "updates")]


def test_github_is_not_asked_when_there_is_no_pin_to_compare(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    env = {"CLAUDE_CONFIG_DIR": str(config), "FLOTILLA_STATE_DIR": str(tmp_path / "state")}

    def never(**_):
        raise AssertionError("GitHub asked with nothing to compare")
    assert updates.lines(tmp_path, env=env, releases=never) == []
    assert doctor.update_findings(tmp_path, env=env, releases=never) == []
