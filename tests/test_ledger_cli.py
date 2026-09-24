import io
import re
import sys
from contextlib import redirect_stdout

import pytest

from flotilla import cli
from flotilla.onboard.tomlw import render_toml
from flotilla.posts import TEMPLATE_DIR, install_templates
from ledgerkit import commit, git, repo_with_origin

GREEN = f"{sys.executable} -c \"print('1 passed')\""
CALL = re.compile(r"`flotilla ([a-z-]+)(?: ([a-z-]+))?")


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def subcommands(parser):
    action = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return action.choices


def onboarded(tmp_path, monkeypatch, profile):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text(render_toml(profile), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


PLAIN = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"}}


def test_a_review_cycle_through_the_cli(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    tree = tmp_path / "app-main-1"
    code, out = run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")
    assert code == 0, out
    tip = commit(tree, "work", "work.txt")
    assert run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")[0] == 0
    assert run_cli("work", "take", "feat/x", "--root", str(root), "--as", "review session 1")[0] == 0
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", tip[:8], "--root", str(root),
                        "--as", "review session 1")
    assert code == 0 and "accepted" in out
    code, out = run_cli("work", "show", "feat/x", "--root", str(root))
    history = out.split("history:", 1)[1].split()
    assert [word for word in history if word in ("claim", "hand", "take", "accept")] == ["claim", "hand", "take", "accept"]


def test_a_refusal_exits_2_with_the_reason(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("work", "accept", "feat/x", "--reviewed", "HEAD", "--root", str(root),
                        "--as", "main session 1")
    assert code == 2 and "may not `accept`" in out


def test_outside_an_onboarded_project(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    root = repo_with_origin(tmp_path)
    code, out = run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")
    assert code == 2 and "not onboarded" in out


def test_receipt_then_hand(tmp_path, monkeypatch):
    profile = {**PLAIN, "tests": {"tier": [{"name": "unit", "command": GREEN, "required_for": ["handover"]}]}}
    root = onboarded(tmp_path, monkeypatch, profile)
    tree = tmp_path / "app-main-1"
    assert run_cli("tree", "cut", "feat/x", "--tree", str(tree), "--root", str(root), "--as", "main session 1")[0] == 0
    commit(tree, "work", "work.txt")
    code, out = run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")
    assert code == 2 and "receipt run" in out
    code, out = run_cli("receipt", "run", "--purpose", "handover", "--tree", str(tree))
    assert code == 0, out
    assert run_cli("work", "hand", "feat/x", "--root", str(tree), "--as", "main session 1")[0] == 0


def test_a_broken_post_file_refuses_by_name(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch, PLAIN)
    post = root / ".flotilla" / "posts" / "main.md"
    post.write_text(post.read_text(encoding="utf-8").replace("may: [", "may: [merge, "), encoding="utf-8")
    code, out = run_cli("work", "claim", "feat/y", "--root", str(root), "--as", "main session 1")
    assert code == 2 and "main.md" in out and "merge" in out


def test_every_cli_call_in_the_post_templates_exists():
    top = subcommands(cli.build_parser())
    for path in sorted(TEMPLATE_DIR.glob("*.md")):
        for command, sub in CALL.findall(path.read_text(encoding="utf-8")):
            assert command in top, f"{path.name}: `flotilla {command}`"
            if sub and command in ("work", "tree", "receipt", "onboard"):
                assert sub in subcommands(top[command]), f"{path.name}: `flotilla {command} {sub}`"
