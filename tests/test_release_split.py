import json
import subprocess
from pathlib import Path

import pytest

from flotilla.fleet import spawn
from flotilla.fleet.spawn import release_split as REAL   # before conftest's double


def records(config: Path, *items):
    (config / "plugins").mkdir(parents=True, exist_ok=True)
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"flotilla@flotilla": list(items)}}), encoding="utf-8")


def install(scope, version, project=None):
    found = {"scope": scope, "version": version, "installPath": f"/cache/flotilla/{version}"}
    if project:
        found["projectPath"] = str(project)
    return found


class Ledger:
    def __init__(self, root):
        self.root = root

    def run(self, argv, **kw):   # `git rev-parse --git-common-dir` for the main checkout
        return subprocess.CompletedProcess(argv, 0, str(self.root / ".git") + "\n", "")


@pytest.fixture
def machine(tmp_path):
    root = tmp_path / "proj"
    (root / ".git").mkdir(parents=True)
    config = tmp_path / "claude"
    return Ledger(root), config, {"CLAUDE_CONFIG_DIR": str(config)}


def test_a_project_install_on_another_release_is_a_split(machine):
    ledger, config, env = machine
    records(config, install("project", "0.30.0", ledger.root), install("user", "0.29.0"))
    said = REAL(ledger, running="0.29.0", env=env)
    assert "0.30.0" in said and "0.29.0" in said and "--anyway" in said
    assert "start this session again" in said


def test_with_no_project_install_the_users_decides(machine):
    ledger, config, env = machine
    records(config, install("user", "0.30.0"), install("project", "0.29.0", "/elsewhere"))
    assert "0.30.0" in REAL(ledger, running="0.29.0", env=env)


def test_the_same_release_or_an_unknown_one_says_nothing(machine):
    ledger, config, env = machine
    assert REAL(ledger, running="0.29.0", env=env) == ""        # no records at all
    records(config, install("project", "0.29.0", ledger.root))
    assert REAL(ledger, running="0.29.0", env=env) == ""
    records(config, install("project", "a1b2c3d4e5f6", ledger.root))
    assert REAL(ledger, running="0.29.0", env=env) == ""
    (config / "plugins" / "installed_plugins.json").write_text("{broken", encoding="utf-8")
    assert REAL(ledger, running="0.29.0", env=env) == ""


SPLIT = "a seat raised now would load flotilla 0.30.0 ... `--anyway` to raise it beside the rest"


def test_spawn_raises_nothing_on_a_split_unless_anyway(tmp_path, monkeypatch):
    from tests.test_fleet_spawn import FakeClaude, run, world
    fake = FakeClaude()
    _, ledger, store = world(tmp_path, fake)
    monkeypatch.setattr(spawn, "release_split", lambda *args, **kwargs: SPLIT)
    with pytest.raises(spawn.SpawnRefused, match="0.30.0"):
        run(ledger, store, fake, {"main": 1})
    assert fake.launched == []
    raised, _ = run(ledger, store, fake, {"main": 1}, anyway=True)
    assert len(raised) == 1


def test_a_helper_is_not_raised_on_a_split_unless_anyway(tmp_path, monkeypatch):
    from flotilla.ledger.errors import MoveRefused
    from tests.test_fleet_helpers import PROFILE, raise_one, world
    _, ledger, fake, _, _ = world(tmp_path, {**PROFILE, "permissions": {"mode": "auto"}})
    monkeypatch.setattr(spawn, "release_split", lambda *args, **kwargs: SPLIT)
    with pytest.raises(MoveRefused, match="0.30.0"):
        raise_one(ledger, fake)
    assert raise_one(ledger, fake, anyway=True).seat.name == "helper 1"


def records_of(config: Path, by_id: dict):
    (config / "plugins").mkdir(parents=True, exist_ok=True)
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": by_id}),
                                                               encoding="utf-8")


def enable(path: Path, **ids):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"enabledPlugins": {key.replace("__", "@").replace("_", "-"): on
                                                   for key, on in ids.items()}}), encoding="utf-8")


def test_a_record_of_a_marketplace_the_project_does_not_enable_is_not_what_loads(machine):   # field test, item 5
    # the 11-07 field test: a stale `flotilla@flotilla` 0.29.3 record for the project, while the project enables only
    # `flotilla@flotilla-local` 0.30.1 and turns the user's `flotilla@flotilla` off
    from flotilla.core import claude_state
    ledger, config, env = machine
    records_of(config, {"flotilla@flotilla": [install("project", "0.29.3", ledger.root), install("user", "0.29.3")],
                        "flotilla@flotilla-local": [install("project", "0.30.1", ledger.root)]})
    enable(config / "settings.json", flotilla__flotilla=True)
    enable(ledger.root / ".claude" / "settings.json", flotilla__flotilla_local=True)
    enable(ledger.root / ".claude" / "settings.local.json", flotilla__flotilla=False)
    assert claude_state.loads_in(ledger.root, env) == "0.30.1"
    assert REAL(ledger, running="0.30.1", env=env) == ""


def test_two_enabled_marketplaces_on_two_releases_are_named_as_a_double_load(machine):   # field test, item 5
    from flotilla.core import claude_state
    ledger, config, env = machine
    records_of(config, {"flotilla@flotilla": [install("user", "0.29.3")],
                        "flotilla@flotilla-local": [install("project", "0.30.1", ledger.root)]})
    enable(config / "settings.json", flotilla__flotilla=True)
    enable(ledger.root / ".claude" / "settings.json", flotilla__flotilla_local=True)
    assert sorted(claude_state.loads_all(ledger.root, env)) == [("flotilla@flotilla", "0.29.3"),
                                                                ("flotilla@flotilla-local", "0.30.1")]
    said = REAL(ledger, running="0.30.1", env=env)
    assert "two releases" in said and "0.29.3" in said and "flotilla@flotilla" in said and "--anyway" in said, said
    assert "enabledPlugins" in said


def test_an_enabled_id_whose_only_record_is_another_paths_does_not_load(machine):   # reviews of 424c879, 0fb0c07
    # a moved repository or a second clone: `claude plugin list` reports such an id enabled, but a session started
    # here does not load it ("not cached", measured with `claude -p --debug`, Claude Code 2.1.319)
    from flotilla.core import claude_state
    ledger, config, env = machine
    records_of(config, {"flotilla@flotilla": [install("user", "0.29.3")],
                        "flotilla@flotilla-local": [install("project", "0.30.1", "/the/old/clone")]})
    enable(config / "settings.json", flotilla__flotilla=True)
    enable(ledger.root / ".claude" / "settings.json", flotilla__flotilla_local=True)
    assert claude_state.loads_all(ledger.root, env) == [("flotilla@flotilla", "0.29.3")]
    assert REAL(ledger, running="0.29.3", env=env) == ""


def test_a_release_named_by_a_commit_is_never_taken_for_the_oldest(machine):   # review of 0fb0c07, L1
    from flotilla.core import claude_state
    ledger, config, env = machine
    records_of(config, {"flotilla@flotilla": [install("user", "0.31.0")],
                        "flotilla@flotilla-local": [install("project", "a1b2c3d4e5f6", ledger.root)]})
    enable(config / "settings.json", flotilla__flotilla=True)
    enable(ledger.root / ".claude" / "settings.json", flotilla__flotilla_local=True)
    assert claude_state.loads_in(ledger.root, env) == "0.31.0"


def test_with_two_releases_loading_the_older_is_what_every_copy_has_reached(machine):   # review of 424c879, L1
    # a deferred update waits until every copy a session loads has reached it, not the newest one
    from flotilla.core import claude_state
    ledger, config, env = machine
    records_of(config, {"flotilla@flotilla": [install("user", "0.31.0")],
                        "flotilla@flotilla-local": [install("project", "0.30.1", ledger.root)]})
    enable(config / "settings.json", flotilla__flotilla=True)
    enable(ledger.root / ".claude" / "settings.json", flotilla__flotilla_local=True)
    assert claude_state.loads_in(ledger.root, env) == "0.30.1"


def test_the_first_record_that_applies_here_is_the_one_that_loads(machine):   # review of 7e6a9b8, F1
    # measured with `claude -p --debug` (Claude Code 2.1.319): the first record in the id's list that is the user's
    # or this path's loads, whatever its scope; `claude plugin install` appends, so a user install made first wins
    from flotilla.core import claude_state
    ledger, config, env = machine
    records(config, install("user", "0.29.0"), install("project", "0.30.0", ledger.root))
    assert claude_state.loads_in(ledger.root, env) == "0.29.0"
    assert REAL(ledger, running="0.29.0", env=env) == ""
    records(config, install("project", "0.30.0", "/another/clone"), install("local", "0.30.1", ledger.root),
            install("project", "0.30.0", ledger.root))
    assert claude_state.loads_in(ledger.root, env) == "0.30.1"
