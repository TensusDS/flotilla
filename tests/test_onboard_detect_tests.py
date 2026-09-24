import json

from flotilla.onboard.detect_tests import detect_tiers


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_empty_repository_has_no_tiers(tmp_path):
    assert detect_tiers(tmp_path) == ([], [])


def test_uv_project(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.pytest.ini_options]\n")
    write(tmp_path, "uv.lock", "")
    tiers, _ = detect_tiers(tmp_path)
    assert tiers == [{"name": "python", "command": "uv run pytest", "source": "pyproject.toml + uv.lock"}]


def test_poetry_project(tmp_path):
    write(tmp_path, "pyproject.toml", "[tool.poetry.group.dev.dependencies]\npytest = '*'\n")
    write(tmp_path, "poetry.lock", "")
    assert detect_tiers(tmp_path)[0][0]["command"] == "poetry run pytest"


def test_plain_pyproject_with_a_tests_directory(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    (tmp_path / "tests").mkdir()
    assert detect_tiers(tmp_path)[0][0]["command"] == "python3 -m pytest"


def test_pyproject_without_pytest_is_noted_not_proposed(tmp_path):
    write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    tiers, notes = detect_tiers(tmp_path)
    assert tiers == [] and "no sign of pytest" in notes[0]


def test_npm_placeholder_is_not_a_tier(tmp_path):
    write(tmp_path, "package.json", json.dumps(
        {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
    tiers, notes = detect_tiers(tmp_path)
    assert tiers == [] and "placeholder" in notes[0]


def test_pnpm_project(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    write(tmp_path, "pnpm-lock.yaml", "")
    assert detect_tiers(tmp_path)[0] == [
        {"name": "node", "command": "pnpm test", "source": "package.json + pnpm-lock.yaml"}]


def test_invalid_package_json_is_noted(tmp_path):
    write(tmp_path, "package.json", "{not json")
    tiers, notes = detect_tiers(tmp_path)
    assert tiers == [] and "not valid JSON" in notes[0]


def test_rust_go_and_make_in_order(tmp_path):
    write(tmp_path, "Cargo.toml", "[package]\nname = 'x'\n")
    write(tmp_path, "go.mod", "module x\n")
    write(tmp_path, "Makefile", "build:\n\ttrue\ntest:\n\ttrue\n")
    assert [t["name"] for t in detect_tiers(tmp_path)[0]] == ["rust", "go", "make"]
