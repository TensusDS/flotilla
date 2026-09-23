import subprocess
from pathlib import Path

import pytest

from flotilla.core import config


def write_project(root: Path, text: str) -> Path:
    (root / ".flotilla").mkdir(parents=True, exist_ok=True)
    path = root / ".flotilla" / "project.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_inactive_when_nothing_is_onboarded(tmp_path):
    assert config.find_project(tmp_path) is None


def test_found_from_a_nested_directory(tmp_path):
    write_project(tmp_path, "schema = 1\n")
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert config.find_project(nested) == tmp_path.resolve()


def test_directory_without_the_file_is_not_a_project(tmp_path):
    (tmp_path / ".flotilla").mkdir()
    assert config.find_project(tmp_path) is None


def test_load_valid_project(tmp_path):
    write_project(tmp_path, 'schema = 1\n[trunk]\nbranch = "main"\n')
    project = config.load_project(tmp_path)
    assert project.schema == 1 and project.data["trunk"]["branch"] == "main"


def test_malformed_toml_names_file_and_line(tmp_path):
    path = write_project(tmp_path, 'schema = 1\n[trunk\nbranch = "main"\n')
    with pytest.raises(config.ConfigError) as err:
        config.load_project(tmp_path)
    assert str(path) in str(err.value)
    assert "line 2" in str(err.value)


def test_missing_schema_is_named(tmp_path):
    write_project(tmp_path, '[trunk]\nbranch = "main"\n')
    with pytest.raises(config.ConfigError, match="missing `schema`"):
        config.load_project(tmp_path)


def test_newer_schema_asks_for_an_update(tmp_path):
    write_project(tmp_path, "schema = 2\n")
    with pytest.raises(config.ConfigError, match="update the plugin"):
        config.load_project(tmp_path)


@pytest.mark.parametrize("value", ["0", "true", '"1"'])
def test_invalid_schema_values(tmp_path, value):
    write_project(tmp_path, f"schema = {value}\n")
    with pytest.raises(config.ConfigError):
        config.load_project(tmp_path)


def test_project_found_from_a_linked_worktree(tmp_path):
    main = tmp_path / "app"
    main.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=main, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    write_project(main, "schema = 1\n")
    run("add", ".flotilla/project.toml")
    run("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "onboard")
    tree = tmp_path / "app-review-1"
    run("worktree", "add", "-q", "-b", "fleet/review-1", str(tree))
    assert config.find_project(tree) == tree.resolve()
    assert config.load_project(tree).schema == 1
