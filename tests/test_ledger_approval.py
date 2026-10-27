import pytest

from flotilla.ledger import approval
from ledgerkit import branch, commit, git, repo_with_origin

CODE = ["src/**", "!src/data/**", "*.py"]


@pytest.mark.parametrize("path, expected", [
    ("src/core/storm-wall.ts", True),
    ("src/render/sky-shaders.ts", True),
    ("src/data/credits.ts", False),       # data under the code tree ships on its own
    ("src/data/deep/table.f16", False),
    ("docs/research/far-land-layers.md", False),
    ("setup.py", True),                   # a pattern without a slash matches the name anywhere, as in .gitignore
    ("tools/x/y.py", True),
    ("srcfoo/a.ts", False),               # `src/**` is the directory, never a prefix of a name
    ("README.md", False),
])
def test_a_path_needs_the_person_when_an_include_matches_and_no_exclude_does(path, expected):
    assert approval.matches(path, CODE) is expected


def test_no_patterns_match_nothing():
    assert approval.matches("src/a.ts", []) is False


def put(root, path):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    return commit(root, f"add {path}", path, "x\n")


def _world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, git(root, "rev-parse", "HEAD")


def test_without_a_person_authorizing_merges_nothing_needs_the_person(tmp_path):
    root, base = _world(tmp_path)
    tip = put(root, "src/a.ts")
    assert approval.needs_person(root, {"flow": {"merge_authorized_by": "sender", "approve_paths": CODE}},
                                 base, tip) is False


def test_a_person_authorizing_every_merge_approves_everything(tmp_path):
    root, base = _world(tmp_path)
    tip = put(root, "docs/n.md")
    assert approval.needs_person(root, {"flow": {"merge_authorized_by": "human"}}, base, tip) is True


def test_with_approve_paths_only_work_touching_them_waits_for_the_person(tmp_path):
    """twosuns field report B1, the person's decision 2026-10-27: code ships on a yes; docs, credits and data do not
    ask, so the questions put to the person stay few."""
    root, base = _world(tmp_path)
    profile = {"flow": {"merge_authorized_by": "human", "approve_paths": CODE}}
    docs = branch(root, "docs/x", "notes")   # ledgerkit writes a file at the root named after the commit
    code = put(root, "src/core/n.ts")
    data = put(root, "src/data/credits.ts")
    assert approval.needs_person(root, profile, base, docs) is False
    assert approval.needs_person(root, profile, base, code) is True
    assert approval.needs_person(root, profile, code, data) is False
    assert approval.touched(root, profile, base, data) == ["src/core/n.ts"]


def test_when_git_cannot_say_what_changed_the_person_is_asked(tmp_path):
    root, base = _world(tmp_path)
    profile = {"flow": {"merge_authorized_by": "human", "approve_paths": CODE}}
    assert approval.needs_person(root, profile, base, "0" * 40) is True
    assert approval.needs_person(root, profile, "", base) is True


def test_code_added_and_taken_back_still_waits_for_the_person(tmp_path):
    """The first commit can reach trunk alone, so the net diff does not decide."""
    root, base = _world(tmp_path)
    profile = {"flow": {"merge_authorized_by": "human", "approve_paths": CODE}}
    put(root, "src/core/n.ts")
    git(root, "rm", "-q", "src/core/n.ts")
    back = commit(root, "take it back")
    assert approval.needs_person(root, profile, base, back) is True


def test_what_trunk_already_holds_is_no_part_of_the_work(tmp_path):
    root, base = _world(tmp_path)
    profile = {"flow": {"merge_authorized_by": "human", "approve_paths": CODE}}
    on_trunk = put(root, "src/core/n.ts")
    git(root, "checkout", "-q", "-b", "docs/y", base)
    git(root, "merge", "-q", "--no-edit", "main")
    tip = put(root, "docs/y.md")
    assert approval.needs_person(root, profile, base, tip) is True
    assert approval.needs_person(root, profile, base, tip, trunk=on_trunk) is False



def test_a_merge_that_drops_trunks_code_still_waits_for_the_person(tmp_path):
    """Security review of the code-paths change, 2: `--cc` lists only what a merge resolved apart from both parents,
    so a merge keeping one side whole - dropping trunk's code - showed nothing; landing it takes that code out."""
    root, base = _world(tmp_path)
    profile = {"flow": {"merge_authorized_by": "human", "approve_paths": CODE}}
    on_trunk = put(root, "src/core/n.ts")
    git(root, "checkout", "-q", "-b", "docs/z", base)
    put(root, "docs/z.md")
    git(root, "merge", "-q", "-s", "ours", "--no-edit", "main")
    tip = git(root, "rev-parse", "HEAD")
    assert approval.needs_person(root, profile, base, tip, trunk=on_trunk, against=on_trunk) is True
    assert approval.touched(root, profile, base, tip, trunk=on_trunk, against=on_trunk) == ["src/core/n.ts"]



def test_the_fleets_own_rules_always_wait_for_the_person(tmp_path):
    """Security review of the code-paths change, 4: a row editing .flotilla/ could take the gate itself away with no
    yes; the fleet's rules are always the person's, whatever the patterns or their excludes say."""
    root, base = _world(tmp_path)
    profile = {"flow": {"merge_authorized_by": "human", "approve_paths": ["src/**", "!.flotilla/**"]}}
    tip = put(root, ".flotilla/project.toml")
    assert approval.needs_person(root, profile, base, tip) is True


def test_a_vouched_merge_is_judged_by_what_it_changes_on_its_own_line(tmp_path):
    """Security review of the code-paths change, 3: a merge whose tree drops its first parent's code shows nothing
    under --cc, since it equals the other parent there."""
    from types import SimpleNamespace
    import subprocess
    from flotilla.ledger import batch
    root, base = _world(tmp_path)
    put(root, "src/core/n.ts")
    git(root, "checkout", "-q", "-b", "side", base)
    put(root, "docs/side.md")
    git(root, "checkout", "-q", "main")
    git(root, "merge", "-q", "--no-edit", "side")
    git(root, "rm", "-q", "src/core/n.ts")
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--amend", "--no-edit")
    merged = git(root, "rev-parse", "HEAD")
    ledger = SimpleNamespace(root=root, run=subprocess.run,
                             profile={"flow": {"merge_authorized_by": "human", "approve_paths": CODE}})
    assert batch.commit_needs_person(ledger, merged) is True



def test_a_type_exclude_covers_files_never_a_directory_named_like_one():
    """Security review of the code-paths change, 8: `!*.md` read as gitignore covered a directory `x.md/` and the
    code in it."""
    patterns = ["**", "!*.md"]
    assert approval.matches("notes/a.md", patterns) is False
    assert approval.matches("x.md/evil.ts", patterns) is True


@pytest.mark.parametrize("name", ["CLAUDE.md", "src/CLAUDE.md", "CLAUDE.local.md", "AGENTS.md", "docs/AGENTS.md",
                                  "GEMINI.md", ".cursorrules", ".windsurfrules", "CMakeLists.txt",
                                  "constraints-dev.txt"])
def test_what_instructs_an_agent_or_a_build_always_waits(name):
    """Security review of the code-paths change, 7 and 9: agent instructions steer the fleet as much as its rules."""
    assert approval.matches(name, approval.ALWAYS)
