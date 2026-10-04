import json
import subprocess
from types import SimpleNamespace

from flotilla.fleet import backlog

TODO = """# Backlog

- [ ] a
* [ ] b
- [x] c
~~- [ ] d~~
- [ ] ~~struck~~
- [ ] [minor] small fix
  - [ ] e
    - [ ] deeper

```
- [ ] inside a fence
```

## Done

- [ ] finished but never ticked

## Later

- [ ] f
"""


def test_todo_items_skip_done_nested_and_code():
    """Unchecked top-level items count; done, struck, nested, fenced and Done-section ones do not; `[minor]` is minor."""
    assert backlog.todo_items(TODO) == (3, 1)   # a, b, f main; the [minor] one minor


def test_a_file_with_no_checkboxes_counts_top_level_bullets_under_headings():
    text = "intro\n- not under a heading\n\n# Ideas\n- one\n- two\n  - nested\n* three\n- ~~gone~~\n\n## Done\n- old\n"
    assert backlog.todo_items(text) == (3, 0)


def test_a_custom_minor_prefix():
    assert backlog.todo_items("- [ ] (s) tiny\n- [ ] big\n", minor_prefix="(s)") == (1, 1)


def test_an_empty_or_all_done_todo_is_zero():
    assert backlog.todo_items("") == (0, 0)
    assert backlog.todo_items("- [x] one\n- [X] two\n") == (0, 0)


def test_from_files_sums_matching_files(tmp_path):
    (tmp_path / "TODO.md").write_text("- [ ] a\n- [ ] [minor] b\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.todo.txt").write_text("- [ ] c\n")
    src = backlog.from_files(tmp_path, ["TODO.md", "docs/**/*.todo.txt"])
    assert (src.main, src.minor) == (2, 1)


def test_from_files_matching_nothing_is_a_known_zero(tmp_path):
    src = backlog.from_files(tmp_path, ["TODO.md"])
    assert (src.main, src.minor) == (0, 0) and "no file" in src.note


def test_from_files_refuses_a_glob_leaving_the_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "TODO.md").write_text("- [ ] outside\n")
    for glob in ("../TODO.md", "/etc/*", "../**/*.md"):
        src = backlog.from_files(root, [glob])
        assert src.main is None and src.minor is None and glob in src.note


def test_from_files_skips_a_link_pointing_out_of_the_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (tmp_path / "secret.md").write_text("- [ ] outside\n")
    (root / "TODO.md").symlink_to(tmp_path / "secret.md")
    src = backlog.from_files(root, ["TODO.md"])
    assert (src.main, src.minor) == (0, 0)


def _gh(stdout="", code=0, raises=None):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, code, stdout, "gh: not logged in" if code else "")
    run.calls = calls
    return run


def test_a_tracker_gh_cannot_ask_is_unknown(tmp_path):
    """No gh, or gh not logged in: the tracker is unknown and says why; the other sources still stand."""
    missing = backlog.from_github(tmp_path, labels=[], minor_labels=["size:small"], run=_gh(raises=FileNotFoundError("gh")))
    assert missing.main is None and "gh" in missing.note
    refused = backlog.from_github(tmp_path, labels=[], minor_labels=[], run=_gh(code=4))
    assert refused.main is None and "not logged in" in refused.note
    garbled = backlog.from_github(tmp_path, labels=[], minor_labels=[], run=_gh(stdout="not json"))
    assert garbled.main is None


def test_github_issues_split_by_minor_label(tmp_path):
    issues = [{"number": 1, "labels": []}, {"number": 2, "labels": [{"name": "size:small"}]},
              {"number": 3, "labels": [{"name": "bug"}]}]
    run = _gh(stdout=json.dumps(issues))
    src = backlog.from_github(tmp_path, labels=[], minor_labels=["size:small"], run=run)
    assert (src.main, src.minor) == (2, 1)
    assert run.calls[0][:4] == ["gh", "issue", "list", "--state"]


def test_github_labels_filter_the_issues(tmp_path):
    issues = [{"number": 1, "labels": [{"name": "fleet"}]}, {"number": 2, "labels": [{"name": "other"}]}]
    src = backlog.from_github(tmp_path, labels=["fleet"], minor_labels=[], run=_gh(stdout=json.dumps(issues)))
    assert (src.main, src.minor) == (1, 0)


def test_from_tasks_count_and_file(tmp_path):
    assert (backlog.from_tasks(5, None).main, backlog.from_tasks(5, None).minor) == (5, 0)
    f = tmp_path / "tasks.txt"
    f.write_text("first\n\n[minor] second\nthird\n   \n")
    src = backlog.from_tasks(None, f)
    assert (src.main, src.minor) == (2, 1)


def test_from_tasks_none_given_is_absent_and_a_missing_file_is_unknown(tmp_path):
    assert backlog.from_tasks(None, None) is None
    src = backlog.from_tasks(None, tmp_path / "absent.txt")
    assert src.main is None and "absent.txt" in src.note


def test_from_ledger_counts_claimed_and_fixing_rows_only():
    rows = {str(i): SimpleNamespace(id=str(i), state=s) for i, s in
            enumerate(["claimed", "fixing", "handed", "reserved", "closed", "accepted", "claimed"])}
    src = backlog.from_ledger(rows, {})
    assert (src.main, src.minor) == (3, 0)


def test_backlog_sums_known_sources_and_names_unknown():
    b = backlog.Backlog([backlog.Source("ledger", 2, 0, ""), backlog.Source("github", None, None, "gh missing"),
                         backlog.Source("files", 3, 1, "")])
    assert (b.main, b.minor, b.unknown) == (5, 1, ["github"])


def test_gather_asks_only_the_configured_sources(tmp_path):
    (tmp_path / "TODO.md").write_text("- [ ] a\n")
    run = _gh(raises=AssertionError("gh must not be asked without a tracker"))
    b = backlog.gather(tmp_path, {}, {}, tasks=None, tasks_file=None, run=run)
    assert [s.name for s in b.sources] == ["ledger", "TODO files"]
    assert b.main == 1
    b = backlog.gather(tmp_path, {"fleet": {"sizing": {"tracker": "github", "backlog_files": []}}}, {},
                       tasks=4, tasks_file=None, run=_gh(stdout="[]"))
    assert [s.name for s in b.sources] == ["ledger", "named tasks", "GitHub issues"]
    assert b.main == 4
