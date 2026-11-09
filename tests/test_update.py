import json
import subprocess
from pathlib import Path

import pytest

from flotilla import update
from flotilla.core import caller
from flotilla.core.census import Session
from flotilla.core.releases import Releases

NEWEST = Releases("0.29.0", "c" * 40, "2026-11-05T12:00:00+00:00")


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path.resolve()


def session(name, cwd, kind="background", short="s1"):
    return Session(name=name, session_id=name, kind=kind, pid=None, short_id=short, status=None, state="working",
                   cwd=str(cwd), started_at_ms=None)


class Machine:
    """A Claude config, a few projects, and a `claude` that records what it is asked and writes what it would."""

    def __init__(self, tmp_path, *, user_ref="v0.26.0", user_version="0.26.0"):
        self.home = tmp_path
        self.config = tmp_path / "claude"
        (self.config / "plugins").mkdir(parents=True)
        self.state = tmp_path / "state"
        self.env = {"CLAUDE_CONFIG_DIR": str(self.config), "FLOTILLA_STATE_DIR": str(self.state)}
        self.calls = []
        self.fail = set()
        self.delivers = "0.29.0"
        self.settings = {"theme": "dark", "extraKnownMarketplaces": {
            "flotilla": {"source": {"source": "github", "repo": "TensusDS/flotilla", "ref": user_ref}},
            "other": {"source": {"source": "github", "repo": "x/y"}}}}
        self.write_settings()
        self.records = [{"scope": "user", "version": user_version, "installPath": "/cache/u"}]
        self.sessions = []
        self.write_records()

    def write_settings(self):
        (self.config / "settings.json").write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    def read_settings(self):
        return json.loads((self.config / "settings.json").read_text(encoding="utf-8"))

    def write_records(self):
        (self.config / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"flotilla@flotilla": self.records}}), encoding="utf-8")

    def project(self, name, version="0.26.0", pin=None):
        root = git_repo(self.home / name)
        self.records.append({"scope": "project", "version": version, "installPath": f"/cache/{version}",
                             "projectPath": str(root)})
        self.write_records()
        if pin:
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.json").write_text(json.dumps({"extraKnownMarketplaces": {"flotilla": {
                "source": {"source": "github", "repo": "TensusDS/flotilla", "ref": pin}}}}), encoding="utf-8")
        return root

    def run(self, argv, **kw):
        if argv[0] == "git":
            return subprocess.run(argv, **kw)
        self.calls.append((argv[1:], kw.get("cwd")))
        words = " ".join(argv[1:])
        if any(word in words for word in self.fail):
            return subprocess.CompletedProcess(argv, 1, "", "refused")
        if argv[1:4] == ["plugin", "marketplace", "add"]:
            ref = argv[4].split("#", 1)[1]
            entry = self.read_settings()["extraKnownMarketplaces"]["flotilla"]
            if entry["source"].get("ref") != ref:
                return subprocess.CompletedProcess(argv, 1, "", "doesn't match its extraKnownMarketplaces entry")
            data = self.read_settings()
            data["extraKnownMarketplaces"]["flotilla"] = {"source": entry["source"]}   # drops autoUpdate (measured)
            (self.config / "settings.json").write_text(json.dumps(data), encoding="utf-8")
            self.known = ref
            return subprocess.CompletedProcess(argv, 0, "Successfully added marketplace: flotilla", "")
        if argv[1:4] == ["plugin", "marketplace", "list"]:   # the shape measured on 2.1.318
            return subprocess.CompletedProcess(argv, 0, f"  \u276f flotilla\n    Source: GitHub (TensusDS/flotilla@"
                                                        f"{getattr(self, 'known', '?')})\n\n  \u276f other\n"
                                                        "    Source: GitHub (x/y)\n", "")
        if argv[1:3] == ["plugin", "update"]:
            scope = argv[argv.index("--scope") + 1]
            for item in self.records:
                if item["scope"] == scope and (scope == "user" or item.get("projectPath") == kw.get("cwd")):
                    item["version"] = self.delivers
            self.write_records()
            return subprocess.CompletedProcess(argv, 0, f"updated to {self.delivers}", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def census(self):
        return list(self.sessions)

    def report(self):
        return update.report(run=self.run, census=self.census, env=self.env, releases=lambda: NEWEST)


def test_the_report_lists_the_user_install_and_each_existing_main_checkout(tmp_path):
    m = Machine(tmp_path)
    a = m.project("a")
    m.records.append({"scope": "project", "version": "0.26.0", "projectPath": str(tmp_path / "gone")})
    worktree = tmp_path / "a-main-1"
    subprocess.run(["git", "-C", str(a), "commit", "-q", "--allow-empty", "-m", "x"], check=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"})
    subprocess.run(["git", "-C", str(a), "worktree", "add", "-q", str(worktree)], check=True)
    m.records.append({"scope": "project", "version": "0.26.0", "projectPath": str(worktree)})
    m.write_records()
    rep = m.report()
    assert [(item.scope, item.project) for item in rep.installs] == [("user", None), ("project", a)]
    assert all(item.behind for item in rep.installs)


def test_a_project_pin_off_the_channel_is_named_with_its_edit_and_not_planned(tmp_path):
    m = Machine(tmp_path)
    a = m.project("a", pin="v0.26.0")
    rep = m.report()
    assert rep.pins == [(a, "v0.26.0")]
    text = "\n".join(update.render(rep, update.plan(rep)))
    assert str(a / ".claude" / "settings.json") in text and '"ref": "stable"' in text


def test_a_working_fleet_must_be_stopped_or_deferred_before_there_is_a_mark(tmp_path):
    m = Machine(tmp_path)
    a = m.project("a")
    m.sessions = [session("a-main session 1", a), session("a-orchestrator 1", a, kind="interactive", short="s2")]
    rep = m.report()
    assert [(fleet.project, fleet.seats, fleet.interactive) for fleet in rep.fleets] == \
        [(a, ["a-main session 1"], ["a-orchestrator 1"])]
    undecided = update.plan(rep)
    assert undecided.mark == "" and undecided.undecided == [a]
    assert update.plan(rep, stop={a}).mark and update.plan(rep, defer={a}).mark
    assert update.plan(rep, stop={a}).mark != update.plan(rep, defer={a}).mark


def test_a_fleet_on_the_user_copy_decides_for_the_user_install(tmp_path):
    m = Machine(tmp_path)
    b = git_repo(tmp_path / "b")                     # no install of its own: its seats load the user's copy
    m.sessions = [session("b-main session 1", b)]
    rep = m.report()
    assert rep.fleets[0].project == b and rep.fleets[0].on_user_copy
    deferred = update.plan(rep, defer={b})
    assert all(item.scope != "user" for item in deferred.updates)


def test_apply_moves_the_channel_keeps_other_settings_and_brings_installs_up(tmp_path):
    m = Machine(tmp_path)
    a = m.project("a")
    rep = m.report()
    chosen = update.plan(rep)
    lines = update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    settings = m.read_settings()
    assert settings["theme"] == "dark" and "other" in settings["extraKnownMarketplaces"]
    assert settings["extraKnownMarketplaces"]["flotilla"] == {
        "source": {"source": "github", "repo": "TensusDS/flotilla", "ref": "stable"}, "autoUpdate": True}
    asked = [" ".join(argv) for argv, _ in m.calls]
    assert "plugin marketplace add TensusDS/flotilla#stable" in asked
    assert "plugin update flotilla@flotilla --scope user" in asked
    assert ("plugin update flotilla@flotilla --scope project".split(), str(a)) in \
        [(argv, cwd) for argv, cwd in m.calls]
    assert list((m.state / "backups").glob("settings.json.*"))
    assert any("start" in line for line in lines)


def test_apply_refuses_a_changed_plan_and_a_background_caller(tmp_path, monkeypatch):
    m = Machine(tmp_path)
    rep = m.report()
    chosen = update.plan(rep)
    with pytest.raises(update.UpdateRefused, match="changed"):
        update.apply(rep, chosen, "0" * 12, run=m.run, census=m.census, env=m.env, state=m.state)
    monkeypatch.setattr(caller, "person_refusal", lambda what: f"a background session; only a person {what}")
    with pytest.raises(update.UpdateRefused, match="background"):
        update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert m.calls == []


def test_a_failed_marketplace_add_stops_before_any_update_and_names_the_backup(tmp_path):
    m = Machine(tmp_path)
    m.project("a")
    m.fail = {"marketplace add"}
    rep = m.report()
    chosen = update.plan(rep)
    with pytest.raises(update.UpdateRefused) as err:
        update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert "backups" in str(err.value) and "put back" in str(err.value)
    assert not any(argv[:2] == ["plugin", "update"] for argv, _ in m.calls)


def test_one_failed_project_update_does_not_stop_the_others(tmp_path):
    m = Machine(tmp_path)
    m.project("a")
    m.project("b")
    m.fail = {"--scope user"}
    rep = m.report()
    chosen = update.plan(rep)
    lines = update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert sum(argv[:2] == ["plugin", "update"] for argv, _ in m.calls) == 3
    assert any("refused" in line for line in lines)


def test_a_deferral_is_recorded_and_said_until_the_update_is_done(tmp_path):
    m = Machine(tmp_path)
    a = m.project("a")
    m.sessions = [session("a-main session 1", a)]
    rep = m.report()
    chosen = update.plan(rep, defer={a})
    update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert not any(cwd == str(a) for _, cwd in m.calls)
    assert "0.29.0" in update.deferred(m.state, a, env=m.env)
    assert "/flotilla:update" in update.deferred(m.state, a, env=m.env)
    m.sessions = []
    rep = m.report()
    chosen = update.plan(rep)
    update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert update.deferred(m.state, a, env=m.env) == ""


def test_another_marketplace_source_is_reported_never_rewritten(tmp_path):
    m = Machine(tmp_path)
    m.settings["extraKnownMarketplaces"]["flotilla"]["source"] = {"source": "directory", "path": "/src/flotilla"}
    m.write_settings()
    rep = m.report()
    chosen = update.plan(rep)
    assert chosen.ref is None and rep.foreign
    update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert m.read_settings()["extraKnownMarketplaces"]["flotilla"]["source"]["source"] == "directory"
    assert not any(argv[:3] == ["plugin", "marketplace", "add"] for argv, _ in m.calls)


def test_a_stopped_fleet_is_stood_down_keeping_the_persons_own_session_then_updated(tmp_path, monkeypatch):
    m = Machine(tmp_path)
    a = m.project("a")
    m.sessions = [session("a-main session 1", a), session("a-orchestrator 1", a, kind="interactive", short="s2")]
    downs = []
    monkeypatch.setattr(update, "stand_down", lambda project, *, keep, census: downs.append((project, keep)) or
                        (["retired a-main session 1"], 0))
    rep = m.report()
    chosen = update.plan(rep, stop={a})
    lines = update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert downs == [(a, frozenset({"a-orchestrator 1"}))]
    assert ("plugin update flotilla@flotilla --scope project".split(), str(a)) in [(x, c) for x, c in m.calls]
    assert any("a-orchestrator 1" in line and "old release" in line for line in lines)
    assert any("/flotilla:spawn" in line for line in lines)


def test_a_skipped_project_is_left_out_and_changes_the_mark(tmp_path):
    m = Machine(tmp_path)
    a = m.project("a")
    b = m.project("b")
    rep = m.report()
    every, skipped = update.plan(rep), update.plan(rep, skip={a})
    assert [item.project for item in skipped.updates] == [None, b]
    assert every.mark != skipped.mark


def test_the_cli_prints_the_plan_and_confirms_only_with_the_mark(tmp_path, monkeypatch, capsys):
    from flotilla import cli
    m = Machine(tmp_path)
    m.project("a")
    found = m.report()
    monkeypatch.setattr(update, "report", lambda **kw: found)
    monkeypatch.setattr("flotilla.fleet.commands.census", m.census)
    assert cli.main(["update"]) == 0
    out = capsys.readouterr().out
    mark = next(line.split(": ", 1)[1] for line in out.splitlines() if line.startswith("mark: "))
    monkeypatch.setattr(update, "apply", lambda rep, chosen, given, **kw: ["applied " + given])
    assert cli.main(["update", "--confirm", mark]) == 0
    assert f"applied {mark}" in capsys.readouterr().out
    assert cli.main(["update", "--pin", "latest"]) == 2


def test_a_failed_marketplace_add_puts_the_settings_back(tmp_path):
    m = Machine(tmp_path)
    m.fail = {"marketplace add"}
    before = (m.config / "settings.json").read_bytes()
    rep = m.report()
    chosen = update.plan(rep)
    with pytest.raises(update.UpdateRefused, match="put back"):
        update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert (m.config / "settings.json").read_bytes() == before


def test_a_marketplace_list_that_cannot_be_read_does_not_pass_for_a_move(tmp_path):
    m = Machine(tmp_path)
    m.fail = {"marketplace list"}
    rep = m.report()
    chosen = update.plan(rep)
    with pytest.raises(update.UpdateRefused, match="could not be confirmed"):
        update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert not any(argv[:2] == ["plugin", "update"] for argv, _ in m.calls)


def test_an_update_that_did_not_move_the_release_is_not_called_updated(tmp_path):
    m = Machine(tmp_path)
    m.project("a")
    m.delivers = "0.26.0"                       # stable lags, or the marketplace was not refreshed
    rep = m.report()
    chosen = update.plan(rep)
    lines = update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert not any(line.startswith("updated:") for line in lines)
    assert sum(line.startswith("refused:") and "still 0.26.0" in line for line in lines) == 2


def test_keeping_an_older_pin_plans_no_update_past_it(tmp_path):
    m = Machine(tmp_path)                        # the user entry pins v0.26.0
    m.project("a", version="0.20.0")
    rep = m.report()
    kept = update.plan(rep, keep_channel=True)
    assert [(item.scope, item.version) for item in kept.updates] == [("project", "0.20.0")]
    assert any("v0.26.0" in line and "at most" in line for line in update.render(rep, kept))


def test_a_deferral_on_the_user_copy_ends_when_the_user_install_is_updated(tmp_path):
    m = Machine(tmp_path)
    b = git_repo(tmp_path / "b")
    m.sessions = [session("b-main session 1", b)]
    rep = m.report()
    chosen = update.plan(rep, defer={b})
    update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert update.deferred(m.state, b, env=m.env)
    m.sessions = []
    rep = m.report()
    chosen = update.plan(rep)
    update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert update.deferred(m.state, b, env=m.env) == ""


def test_a_deferral_is_not_said_once_the_project_loads_the_release(tmp_path):
    m = Machine(tmp_path)
    a = m.project("a")
    m.records.insert(0, m.records.pop())   # the project's install recorded first: that is the copy its sessions load
    m.write_records()
    update._write_deferrals(m.state, {str(a): {"to": "0.29.0", "at": "2026-11-05T12:00:00+00:00"}})
    assert update.deferred(m.state, a, env=m.env)
    m.records[0]["version"] = "0.29.0"          # Claude Code's automatic update moved it
    m.write_records()
    assert update.deferred(m.state, a, env=m.env) == ""


def test_a_deferral_stays_while_the_project_rides_the_users_copy(tmp_path):   # review of 7e6a9b8, F1
    # the user's install recorded first loads in the project too (measured, Claude Code 2.1.319): its own record
    # moving does not move what its sessions run
    m = Machine(tmp_path)
    a = m.project("a")
    update._write_deferrals(m.state, {str(a): {"to": "0.29.0", "at": "2026-11-05T12:00:00+00:00"}})
    m.records[-1]["version"] = "0.29.0"
    m.write_records()
    assert update.deferred(m.state, a, env=m.env)
    m.records[0]["version"] = "0.29.0"
    m.write_records()
    assert update.deferred(m.state, a, env=m.env) == ""


def test_an_entry_of_another_shape_is_refused_and_left_as_it_is(tmp_path):
    m = Machine(tmp_path)
    m.settings["extraKnownMarketplaces"]["flotilla"] = "TensusDS/flotilla"
    m.write_settings()
    before = (m.config / "settings.json").read_bytes()
    rep = m.report()
    chosen = update.plan(rep)
    with pytest.raises(update.UpdateRefused, match="not an object"):
        update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert (m.config / "settings.json").read_bytes() == before


def test_settings_kept_through_a_symlink_stay_a_symlink(tmp_path):
    m = Machine(tmp_path)
    real = tmp_path / "dotfiles" / "claude-settings.json"
    real.parent.mkdir()
    (m.config / "settings.json").rename(real)
    real.chmod(0o600)
    (m.config / "settings.json").symlink_to(real)
    rep = m.report()
    chosen = update.plan(rep)
    update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert (m.config / "settings.json").is_symlink()
    assert json.loads(real.read_text())["extraKnownMarketplaces"]["flotilla"]["autoUpdate"] is True
    assert real.stat().st_mode & 0o777 == 0o600


def test_a_fleet_stood_down_only_in_part_is_not_updated(tmp_path, monkeypatch):
    m = Machine(tmp_path)
    a = m.project("a")
    m.sessions = [session("a-main session 1", a)]
    monkeypatch.setattr(update, "stand_down", lambda project, *, keep, census: (["refused a-main session 1"], 1))
    rep = m.report()
    chosen = update.plan(rep, stop={a})
    lines = update.apply(rep, chosen, chosen.mark, run=m.run, census=m.census, env=m.env, state=m.state)
    assert not any(cwd == str(a) for argv, cwd in m.calls if argv[:2] == ["plugin", "update"])
    assert any("not updated" in line for line in lines)


def test_a_seat_raised_after_the_plan_was_shown_changes_the_mark(tmp_path):
    m = Machine(tmp_path)
    a = m.project("a")
    m.sessions = [session("a-main session 1", a)]
    first = update.plan(m.report(), stop={a})
    m.sessions.append(session("a-main session 2", a, short="s9"))
    assert update.plan(m.report(), stop={a}).mark != first.mark


def test_a_deferred_fleet_riding_the_users_copy_holds_it_though_it_has_a_record_of_its_own(tmp_path):   # 7e6a9b8 F1
    # its own install recorded after the user's: its sessions load the user's copy (measured), so updating that copy
    # would move the fleet the person asked to leave alone
    m = Machine(tmp_path)
    a = m.project("a")
    m.sessions = [session("a-main session 1", a)]
    rep = m.report()
    assert [fleet.on_user_copy for fleet in rep.fleets] == [True]
    chosen = update.plan(rep, defer={a})
    assert not any(item.scope == "user" for item in chosen.updates)


def test_an_updates_stand_down_stops_a_seat_held_on_a_start_up_question(tmp_path, monkeypatch):
    """Review of bedb2a3, finding 2: `/flotilla:update` stood a fleet down on the plain census, so a held seat was
    retired "(it was not running)" and left waiting - the very case `fleet down` was fixed for."""
    from fleetkit import held_seat_world
    ledger, fake, plain = held_seat_world(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.ledger.commands.open_ledger", lambda project: ledger)
    lines, refused = update.stand_down(ledger.root, keep=frozenset(), census=plain)
    assert fake.stopped == ["000001"] and "retired main session 1 (stopped 000001)" in lines, lines
