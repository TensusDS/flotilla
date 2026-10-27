from flotilla.ledger import approval
from flotilla.onboard.detect_code import code_paths
from ledgerkit import commit, git, repo_with_origin

FILES = ["src/core/storm-wall.ts", "src/render/sky-shaders.ts", "src/data/credits.ts", "src/data/table.f16",
         "scripts/credits-md.mjs", "tests/storm.test.ts", "docs/research/far-land.md", "public/logo.png",
         "vite.config.ts", "README.md", "package.json"]


def project(tmp_path, files=FILES):
    root = repo_with_origin(tmp_path)
    for name in files:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x\n", encoding="utf-8")
        git(root, "add", name)
    commit(root, "the project")
    return root


def asks(found, name):
    """What the ledger asks the person about: the patterns, and what always waits whatever they say."""
    return approval.matches(name, found) or approval.matches(name, approval.ALWAYS)


def test_everything_waits_for_the_person_but_what_is_plainly_not_code(tmp_path):
    """The person, 2026-10-27: the paths that need a yes are asked in onboarding from the project and where its code
    lies, so the question is one and its answer is ready."""
    found = code_paths(project(tmp_path))
    assert found == ["**", "!docs/**", "!public/**", "!src/data/**", "!tests/**", "!*.md"]
    waiting = [name for name in FILES if asks(found, name)]
    assert waiting == ["src/core/storm-wall.ts", "src/render/sky-shaders.ts", "scripts/credits-md.mjs",
                       "vite.config.ts", "package.json"]


def test_what_the_project_adds_later_waits_for_the_person(tmp_path):
    """Security review of the change, 5: a snapshot of today's code let a new directory or a new build file through."""
    found = code_paths(project(tmp_path))
    for name in ("lib2/new.ts", "Makefile", ".github/workflows/ci.yml", ".claude/settings.json", ".mcp.json",
                 "src/data/package.json", ".vscode/tasks.json"):
        assert asks(found, name), name
    assert not asks(found, "docs/new.md") and not asks(found, "notes/new.md")


def test_a_name_that_reads_as_a_pattern_is_never_excluded(tmp_path):
    """Security review of the change, 6: a directory named `*` or `!x`, written as an exclude, would let code by."""
    found = code_paths(project(tmp_path, ["src/a.ts", "src/*/data/x.json", "src/!x/docs/y.md"]))
    assert found == ["**", "!*.md"] and asks(found, "src/*/data/x.json")


def test_a_project_without_code_has_no_code_paths(tmp_path):
    assert code_paths(project(tmp_path, ["docs/a.md", "notes.txt"])) == []
