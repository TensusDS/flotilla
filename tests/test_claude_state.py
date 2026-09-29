import json
import subprocess
from pathlib import Path

from flotilla.core import claude_state


def answering(stdout, code=0):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, code, stdout, "")
    return run


def listing(*entries):
    return json.dumps([{"id": pid, "enabled": on, "scope": "project"} for pid, on in entries])


def test_the_plugin_is_enabled_here():
    run = answering(listing(("other@x", True), ("flotilla@flotilla", True)))
    assert claude_state.plugin_enabled(Path("/p"), run=run) is True


def test_the_plugin_is_installed_but_not_enabled_here():
    assert claude_state.plugin_enabled(Path("/p"), run=answering(listing(("flotilla@local", False)))) is False
    assert claude_state.plugin_enabled(Path("/p"), run=answering(listing(("other@x", True)))) is False


def test_an_unreadable_plugin_list_is_unknown():
    assert claude_state.plugin_enabled(Path("/p"), run=answering("not json")) is None
    assert claude_state.plugin_enabled(Path("/p"), run=answering("", code=1)) is None
    def missing(cmd, **kwargs):
        raise FileNotFoundError("claude")
    assert claude_state.plugin_enabled(Path("/p"), run=missing) is None


def test_trust_is_read_from_claude_json(tmp_path):
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {
        str((tmp_path / "repo").resolve()): {"hasTrustDialogAccepted": True},
        str((tmp_path / "other").resolve()): {"hasTrustDialogAccepted": False}}}), encoding="utf-8")
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is True
    assert claude_state.trusted(tmp_path / "other", home=tmp_path) is False


def test_trust_is_unknown_without_the_file_or_the_entry(tmp_path):
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is None
    (tmp_path / ".claude.json").write_text("{broken", encoding="utf-8")
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is None
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is None


def test_setup_problems_refuse_what_is_known_missing_and_warn_on_unknown(tmp_path):
    refusals, warnings = claude_state.setup_problems(tmp_path, run=answering(listing(("flotilla@x", False))),
                                                     home=tmp_path)
    assert any("claude plugin install flotilla@x --scope project" in line for line in refusals)
    assert any("could not tell whether" in line and "trusted" in line for line in warnings)


def test_a_malformed_projects_section_is_unknown(tmp_path):
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": ["not", "a", "dict"]}), encoding="utf-8")
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is None


def test_the_fix_names_the_marketplace_flotilla_is_listed_from(tmp_path):
    refusals, _ = claude_state.setup_problems(tmp_path, run=answering(listing(("flotilla@flotilla-local", False))),
                                              home=tmp_path)
    assert any("claude plugin install flotilla@flotilla-local --scope project" in line for line in refusals)


def test_with_no_flotilla_listed_the_fix_adds_the_marketplace_first(tmp_path):
    refusals, _ = claude_state.setup_problems(tmp_path, run=answering(listing(("other@x", True))), home=tmp_path)
    assert any("claude plugin marketplace add TensusDS/flotilla" in line
               and "claude plugin install flotilla@flotilla --scope project" in line for line in refusals)
