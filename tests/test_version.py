"""The version is one string in three carriers, and a release that changed code without moving it is refused."""

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_version  # noqa: E402


def git(cwd, *args) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def commit(cwd, message, name, text="x\n") -> None:
    path = Path(cwd) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", message)


def plugin_repo(tmp_path, version="1.2.3") -> Path:
    root = Path(tmp_path) / "plugin"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "init", ".claude-plugin/plugin.json", json.dumps({"name": "p", "version": version}) + "\n")
    return root


def test_the_three_carriers_hold_one_version():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    package = re.search(r'^__version__ = "([^"]+)"', (ROOT / "flotilla" / "__init__.py").read_text(encoding="utf-8"),
                        re.M)
    assert pyproject and package
    assert manifest == pyproject.group(1) == package.group(1)


def test_a_missing_tag_is_refused_with_the_tag_to_make(tmp_path, capsys):
    root = plugin_repo(tmp_path)
    assert check_version.main(root) == 1
    assert "tag v1.2.3" in capsys.readouterr().err


def test_a_tag_on_head_passes(tmp_path):
    root = plugin_repo(tmp_path)
    git(root, "tag", "v1.2.3")
    assert check_version.main(root) == 0


def test_code_changed_since_the_tag_asks_for_a_bump(tmp_path, capsys):
    root = plugin_repo(tmp_path)
    git(root, "tag", "v1.2.3")
    commit(root, "code", "flotilla/x.py")
    assert check_version.main(root) == 1
    err = capsys.readouterr().err
    assert "bump the version" in err
    assert "flotilla/x.py" in err


def test_docs_changed_since_the_tag_pass(tmp_path):
    root = plugin_repo(tmp_path)
    git(root, "tag", "v1.2.3")
    commit(root, "docs", "docs/x.md")
    assert check_version.main(root) == 0


def test_a_directory_that_is_not_a_repository_is_refused_without_a_traceback(tmp_path, capsys):
    root = Path(tmp_path) / "bare"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.3"}\n', encoding="utf-8")
    assert check_version.main(root) == 1
    assert "tag v1.2.3" in capsys.readouterr().err
