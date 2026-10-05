import io
from contextlib import redirect_stdout

import pytest

from flotilla import cli
from flotilla.onboard.tomlw import render_toml
from flotilla.posts import install_templates
from ledgerkit import commit, git, repo_with_origin

PROFILE = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"},
           "permissions": {"mode": "auto"}, "fleet": {"default": {"main": 1, "review": 1}, "model": "one"}}


@pytest.fixture(autouse=True)
def ready_checkout(monkeypatch):
    """Never ask the real `claude plugin list` or read ~/.claude.json here: on a machine where claude never ran,
    that call creates Claude Code's own files. The setup checks are tested in test_claude_state.py."""
    from flotilla.core import claude_state
    from flotilla.fleet import plugins
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))
    monkeypatch.setattr(plugins, "listing", lambda run, cwd, **kw: [])


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def onboarded(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text(render_toml(PROFILE), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


def test_dry_run_without_the_census_warns(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    assert code == 0
    assert "census: unknown" in out and "names may collide" in out
    assert "review session 1" in out and "main session 1" in out
    assert "--permission-mode auto" in out and "fleet/reviewer-1" in out
    assert not (tmp_path / "app-reviewer-1").exists()


def test_spawn_needs_a_composition_and_refuses_both_kinds_at_once(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--dry-run", "--root", str(root))
    assert code == 2 and "name a composition" in out
    code, out = run_cli("spawn", "--default", "-r", "1", "--dry-run", "--root", str(root))
    assert code == 2 and "either --default, --fill, --recommended or counts" in out


def test_spawn_without_the_census_refuses_to_launch(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "-M", "1", "--root", str(root))
    assert code == 2 and "census" in out


def test_the_fleet_is_listed_even_when_the_census_is_unknown(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "--root", str(root))
    assert code == 0 and "no post rows" in out


def test_retire_of_an_unknown_name_is_refused(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("retire", "main session 7", "--root", str(root))
    assert code == 2 and "no post row for `main session 7`" in out


def test_a_custom_count_is_given_by_post_name(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--post", "minor=2", "--dry-run", "--root", str(root))
    assert code == 0 and "minor session 1" in out and "minor session 2" in out
    code, out = run_cli("spawn", "--post", "minor", "--dry-run", "--root", str(root))
    assert code == 2 and "NAME=N" in out


def test_fleet_down_without_the_census_refuses_and_stops_nothing(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "down", "--root", str(root))
    assert code == 2 and "nothing was stopped or released" in out


def test_fleet_down_inside_an_unidentified_claude_session_refuses(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    code, out = run_cli("fleet", "down", "--root", str(root))
    assert code == 2 and "cannot tell which session runs this" in out


def test_a_dry_run_says_where_the_numbering_continues_from(tmp_path, monkeypatch):
    from flotilla.core import claude_state
    from fleetkit import session
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [session("review session 37", "abc123")])
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))
    code, out = run_cli("spawn", "--dry-run", "--post", "reviewer=1", "--root", str(root))
    assert code == 0 and "review session 38" in out
    assert "note: reviewer numbering continues after review session 37 (alive on this machine" in out


def plugin_listing(tmp_path, monkeypatch, entries):
    from flotilla.fleet import plugins
    asked = []

    def listing(run, cwd, **kw):
        asked.append(str(cwd))
        return entries
    monkeypatch.setattr(plugins, "listing", listing)
    return asked


def mcp_entry(tmp_path, plugin_id):
    folder = tmp_path / "plugin-cache" / plugin_id.split("@")[0]
    folder.mkdir(parents=True)
    (folder / ".mcp.json").write_text('{"s": {"command": "s"}}', encoding="utf-8")
    return {"id": plugin_id, "scope": "user", "enabled": True, "installPath": str(folder)}


def seat_block(out, name):
    """The lines a dry run prints for one seat: its heading and the indented lines under it."""
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(name + "  "))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if not line.startswith("    "):
            break
        block.append(line)
    return "\n".join(block)


def test_a_dry_run_names_the_plugins_each_seat_turns_off(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    asked = plugin_listing(tmp_path, monkeypatch, [mcp_entry(tmp_path, "playwright@claude-plugins-official"),
                                                   mcp_entry(tmp_path, "serena@official")])
    code, out = run_cli("spawn", "--dry-run", "--post", "reviewer=1", "--post", "judge=1", "--root", str(root))
    assert code == 0, out
    assert "turns off: playwright@claude-plugins-official, serena@official" in seat_block(out, "review session 1")
    assert "turns off: serena@official" in seat_block(out, "acceptance judge 1")
    assert asked == [str(root.resolve())]


def test_a_judge_post_without_plugins_shows_its_browser_turned_off(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    judge = root / ".flotilla" / "posts" / "judge.md"
    judge.write_text(judge.read_text(encoding="utf-8").replace("plugins: [playwright@claude-plugins-official]\n", ""),
                     encoding="utf-8")
    git(root, "add", ".flotilla")
    commit(root, "the judge keeps no plugin")
    git(root, "push", "-q", "origin", "main")   # the rules that count are trunk's
    plugin_listing(tmp_path, monkeypatch, [mcp_entry(tmp_path, "playwright@claude-plugins-official")])
    code, out = run_cli("spawn", "--dry-run", "--post", "judge=1", "--root", str(root))
    assert code == 0, out
    assert "turns off: playwright@claude-plugins-official" in seat_block(out, "acceptance judge 1")


def test_a_dry_run_says_when_the_plugin_set_cannot_be_narrowed(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    plugin_listing(tmp_path, monkeypatch, None)
    code, out = run_cli("spawn", "--dry-run", "--post", "reviewer=1", "--root", str(root))
    assert code == 0, out
    assert "turns off: unknown" in seat_block(out, "review session 1")
    assert "warning:" in out and "plugin set not narrowed" in out


def test_the_fleet_names_a_stranger_in_a_seat_tree(tmp_path, monkeypatch):
    import dataclasses
    from fleetkit import FakeClaude, session
    from flotilla.fleet import spawn
    from flotilla.ledger.commands import open_ledger
    from flotilla.core.storage import LocalLogStore
    root = onboarded(tmp_path, monkeypatch)
    fake = FakeClaude()
    ledger = open_ledger(root)
    ledger.run = fake
    raised, _ = spawn.spawn(ledger, {"main": 1}, census=fake.census, store=LocalLogStore(tmp_path / "names"),
                            caller="test", wait=0.1, poll=0.05, sleep=lambda s: None)
    tree = raised[0].seat.tree
    stranger = dataclasses.replace(session(f"{tree.name}-3", "ffffff"), cwd=str(tree / "src"))
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: fake.census() + [stranger])
    code, out = run_cli("fleet", "--root", str(root))
    assert code == 0, out
    assert f"{tree.name}-3  not a fleet session (started in {tree})" in out
    assert out.count("main session 1") == 1


def _a_session(name, cwd):
    from flotilla.core.census import Session
    return Session(name=name, session_id=f"sid-{name}", kind="background", pid=None, short_id=name[-6:], status=None,
                   state="idle", cwd=str(cwd), started_at_ms=None)


def test_lead_reserves_the_orchestrators_name_for_the_persons_session(tmp_path, monkeypatch):
    """Review of 0.5.0, I3: the name the person typed with /rename came from a dry run, which reserves nothing, so a
    later spawn could issue the same `orchestrator N` again and two sessions' records would read as one."""
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    monkeypatch.setattr("flotilla.core.caller.person_refusal", lambda what: "")
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code == 0 and "/rename orchestrator 1" in out and "spawn --fill" in out
    code, out = run_cli("spawn", "--dry-run", "-o", "1", "--root", str(root))
    assert code == 0 and "orchestrator 2" in out and "orchestrator 1 " not in out


def test_lead_is_the_persons(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    monkeypatch.setattr("flotilla.core.caller.person_refusal",
                        lambda what: f"`main session 2` is a background session; only a person {what}")
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code == 2 and "background session" in out


def test_lead_takes_no_counts(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.core.caller.person_refusal", lambda what: "")
    code, out = run_cli("spawn", "--lead", "-M", "1", "--root", str(root))
    assert code == 2 and "--lead" in out


def test_fill_counts_only_this_projects_sessions(tmp_path, monkeypatch):
    """Review of 0.5.0, M3: the census is machine-wide, so another project's live reviewer stopped `--fill` from
    raising this project's."""
    root = onboarded(tmp_path, monkeypatch)
    elsewhere = tmp_path / "another-project"
    elsewhere.mkdir()
    monkeypatch.setattr("flotilla.fleet.commands.census",
                        lambda: [_a_session("review session 4", elsewhere), _a_session("main session 3", root)])
    code, out = run_cli("spawn", "--fill", "--dry-run", "--root", str(root))
    assert code == 0 and "(reviewer)" in out and "(main)" not in out


def test_fleet_clean_previews_then_removes_what_is_sure(tmp_path, monkeypatch):
    """Decision 206: a sweep for what forced stops and older fleets left; it shows its plan unless told --yes."""
    root = onboarded(tmp_path, monkeypatch)
    tree = tmp_path / "app-main-9"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-9", str(tree))
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    code, out = run_cli("fleet", "clean", "--root", str(root))
    assert code == 0 and "would remove" in out and "--yes" in out and tree.exists()
    code, out = run_cli("fleet", "clean", "--yes", "--root", str(root))
    assert code == 0 and "removed" in out and not tree.exists()


def test_fleet_clean_needs_the_census(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "clean", "--yes", "--root", str(root))
    assert code == 2 and "census" in out


def roomy(monkeypatch):
    from flotilla.core import resources
    monkeypatch.setattr(resources, "available_mb", lambda **kw: 64_000)
    monkeypatch.setattr(resources, "free_disk_mb", lambda path: 500_000)

    def never(*a, **kw):
        raise AssertionError("fleet size must raise nothing")
    monkeypatch.setattr("flotilla.fleet.spawn.spawn", never)
    monkeypatch.setattr("flotilla.fleet.launch.launch", never, raising=False)


def test_fleet_size_prints_the_recommendation_and_its_cap(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    roomy(monkeypatch)
    code, out = run_cli("fleet", "size", "--root", str(root))
    assert code == 0, out
    assert out.startswith("recommended: orchestrator 1 (this session), main 1")
    assert "limited by backlog" in out and "code: not measured in this version" in out


def test_fleet_size_json_and_named_tasks_move_the_backlog_cap(tmp_path, monkeypatch):
    import json
    root = onboarded(tmp_path, monkeypatch)
    roomy(monkeypatch)
    code, out = run_cli("fleet", "size", "--json", "--tasks", "4", "--root", str(root))
    assert code == 0, out
    data = json.loads(out)
    assert {"counts", "caps", "binding", "authors", "lines", "raise_nothing"} <= set(data)
    assert data["caps"]["backlog"] == 4 and data["counts"]["main"] == 4


def test_fleet_size_reads_a_tasks_file(tmp_path, monkeypatch):
    import json
    root = onboarded(tmp_path, monkeypatch)
    roomy(monkeypatch)
    tasks = tmp_path / "tasks.txt"
    tasks.write_text("one\ntwo\n[minor] three\n")
    code, out = run_cli("fleet", "size", "--json", "--tasks-file", str(tasks), "--root", str(root))
    data = json.loads(out)
    assert (data["counts"]["main"], data["counts"]["minor"]) == (2, 1)


def test_spawn_recommended_plans_the_recommendation_beside_the_posts_held(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    roomy(monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    code, out = run_cli("spawn", "--recommended", "--tasks", "2", "--dry-run", "--root", str(root))
    assert code == 0, out
    assert "recommended: orchestrator 1 (this session), main 2" in out
    assert "main session 1" in out and "main session 2" in out and "review session 1" in out
    assert "sender session 1" in out or "sender" in out
    assert "orchestrator session" not in out.split("recommended:")[1].split("\n", 1)[1]
    assert "dry run: 4 session(s) planned" in out


def test_spawn_recommended_takes_no_counts_beside_it(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    roomy(monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])   # so only the mix can refuse it
    code, out = run_cli("spawn", "--recommended", "-M", "1", "--dry-run", "--root", str(root))
    assert code == 2 and "--recommended or counts, not more than one" in out


def test_spawn_recommended_without_the_census_refuses(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    roomy(monkeypatch)
    code, out = run_cli("spawn", "--recommended", "--dry-run", "--root", str(root))
    assert code == 2 and "census" in out


def test_spawn_recommended_with_no_room_refuses_unless_anyway(tmp_path, monkeypatch):
    from flotilla.core import resources
    root = onboarded(tmp_path, monkeypatch)
    roomy(monkeypatch)
    monkeypatch.setattr(resources, "available_mb", lambda **kw: 2100)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    code, out = run_cli("spawn", "--recommended", "--dry-run", "--root", str(root))
    assert code == 2 and "raise nothing now" in out
    code, out = run_cli("spawn", "--recommended", "--dry-run", "--anyway", "--root", str(root))
    assert code == 0 and "dry run:" in out
