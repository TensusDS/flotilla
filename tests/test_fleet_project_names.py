"""Seat names carry the project, and numbers run per project (worldcore field test W10, W11; the person's request,
2026-10-01: "worldcore-orchestrator 1, 2, 3"). Session names stay machine-wide addresses - messages are delivered by
name - so the project's fleet name is unique on the machine: a second project that wants a taken one gets `-2`."""

import pytest

from flotilla.core.census import Session
from flotilla.core.storage import LocalLogStore
from flotilla.fleet import names, project_name, spawn
from flotilla.posts import TEMPLATE_DIR, load_posts
from fleetkit import FakeClaude
from ledgerkit import PROFILE, git, make_ledger, repo_with_origin


@pytest.fixture(autouse=True)
def ready_checkout(monkeypatch):
    from flotilla.core import claude_state
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))


def at(name, cwd, sid=None):
    return Session(name=name, session_id=sid or f"sid-{name}", kind="background", pid=None, short_id=name[-6:],
                   status=None, state="idle", cwd=str(cwd), started_at_ms=None)


def posts_of(tmp_path, project=""):
    root = tmp_path / "p"
    (root / ".flotilla" / "posts").mkdir(parents=True)
    for template in TEMPLATE_DIR.glob("*.md"):
        (root / ".flotilla" / "posts" / template.name).write_text(template.read_text(encoding="utf-8"),
                                                                    encoding="utf-8")
    return load_posts(root, project=project)


def test_a_projects_posts_carry_its_name(tmp_path):
    posts = posts_of(tmp_path, project="worldcore")
    assert posts["orchestrator"].matches("worldcore-orchestrator 1")
    assert not posts["orchestrator"].matches("orchestrator 1")
    assert not posts["orchestrator"].matches("twosuns-orchestrator 1")
    assert names.number_of(posts["main"], "worldcore-main session 3") == 3


def test_a_profile_without_a_fleet_name_keeps_the_old_names(tmp_path):
    posts = posts_of(tmp_path)
    assert posts["orchestrator"].matches("orchestrator 1")


def test_numbers_run_per_project(tmp_path):
    store = LocalLogStore(tmp_path / "fleet")
    old = posts_of(tmp_path / "a")["orchestrator"]
    names.next_names(old, 4, taken=set(), store=store, reserve=True)       # an older fleet took 1..4 machine-wide
    world = posts_of(tmp_path / "b", project="worldcore")["orchestrator"]
    suns = posts_of(tmp_path / "c", project="twosuns")["orchestrator"]
    assert names.next_names(world, 1, taken=set(), store=store, reserve=True) == ["worldcore-orchestrator 1"]
    assert names.next_names(suns, 1, taken=set(), store=store, reserve=True) == ["twosuns-orchestrator 1"]
    assert names.next_names(world, 1, taken=set(), store=store, reserve=True) == ["worldcore-orchestrator 2"]
    assert names.next_names(old, 1, taken=set(), store=store, reserve=True) == ["orchestrator 5"]


def test_a_fleet_name_is_unique_on_the_machine(tmp_path):
    store = LocalLogStore(tmp_path / "fleet")
    assert project_name.resolve(store, "repo-a", "app") == ("app", "")
    name, note = project_name.resolve(store, "repo-b", "app")
    assert name == "app-2" and "app" in note and "taken" in note
    assert project_name.resolve(store, "repo-a", "app") == ("app", "")          # stable for its owner
    assert project_name.resolve(store, "repo-b", "app")[0] == "app-2"            # and for the second one


@pytest.mark.parametrize("raw, clean", [("worldcore", "worldcore"), ("My Game!", "My-Game"),
                                        ("  ", "project"), ("a/b", "a-b")])
def test_a_fleet_name_is_cleaned_to_an_address_word(raw, clean):
    assert project_name.clean(raw) == clean


def test_the_ledger_reads_posts_under_the_profiles_fleet_name(tmp_path, monkeypatch):
    from flotilla.ledger.commands import trunk_rules
    from flotilla.onboard.tomlw import render_toml
    from flotilla.posts import install_templates
    from ledgerkit import commit
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    profile = {**PROFILE, "fleet": {"name": "worldcore", "default": {"main": 1}}}
    (root / ".flotilla" / "project.toml").write_text(render_toml(profile), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    assert trunk_rules(root).posts["sender"].matches("worldcore-sender 1")


def world(tmp_path, sessions, fleet_name="worldcore"):
    root = repo_with_origin(tmp_path)
    fake = FakeClaude(sessions)
    project = posts_of(tmp_path / "posts", project=fleet_name)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "permissions": {"mode": "auto"}},
                         posts=project, run=fake, census=fake.census)
    return root, ledger, LocalLogStore(tmp_path / "state" / "fleet"), fake


def test_another_projects_sender_does_not_hold_this_ones(tmp_path):
    """W10: twosuns' live `sender 9` refused worldcore's sender: a sender's one-copy resources are its project's."""
    elsewhere = tmp_path / "twosuns"
    elsewhere.mkdir()
    root, ledger, store, fake = world(tmp_path, [at("sender 9", elsewhere)])
    seats, _ = spawn.plan(ledger, {"sender": 1}, census=fake.census, store=store, reserve=False)
    assert [seat.name for seat in seats] == ["worldcore-sender 1"]


def test_this_projects_sender_still_holds_it_and_the_refusal_names_only_this_project(tmp_path):
    elsewhere = tmp_path / "twosuns"
    elsewhere.mkdir()
    root = tmp_path / "app"
    sessions = [at("worldcore-sender 1", root), at("main session 19", elsewhere)]
    root, ledger, store, fake = world(tmp_path, sessions)
    with pytest.raises(spawn.SpawnRefused, match="one-copy") as refused:
        spawn.plan(ledger, {"sender": 1}, census=fake.census, store=store, reserve=False)
    assert "worldcore-sender 1" in str(refused.value) and "main session 19" not in str(refused.value)


def test_numbering_steps_over_a_seat_branch_an_older_fleet_left(tmp_path):
    """A project that moves to fleet names counts from 1 again, but its old seats' branches are still there."""
    root, ledger, store, fake = world(tmp_path, [])
    git(root, "branch", "fleet/main-1")
    git(root, "branch", "fleet/main-2")
    seats, _ = spawn.plan(ledger, {"main": 1}, census=fake.census, store=store, reserve=False)
    assert [seat.name for seat in seats] == ["worldcore-main session 3"]


def test_onboarding_names_the_fleet_after_the_project(tmp_path):
    from flotilla.onboard.profile import build_profile
    det = {"root": str(tmp_path / "worldcore"), "trunk": "main", "remote": "", "tests": [], "ci": {"provider": "none"}}
    profile = build_profile(det, {"flow": "local", "review": "every", "permissions": "ask", "tiers": ["none"],
                                  "model": "one"})
    assert profile["fleet"]["name"] == "worldcore"
