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


def test_the_code_paths_are_where_the_product_code_lies(tmp_path):
    """The person, 2026-10-27: the paths that need a yes are asked in onboarding from the project and where its code
    lies, so the question is one and its answer is ready."""
    found = code_paths(project(tmp_path))
    assert found == ["package.json", "/vite.config.ts", "scripts/**", "src/**", "!src/data/**"]


def test_what_the_detected_paths_ask_the_person_about(tmp_path):
    found = code_paths(project(tmp_path))
    asks = [name for name in FILES if approval.matches(name, found)]
    assert asks == ["src/core/storm-wall.ts", "src/render/sky-shaders.ts", "scripts/credits-md.mjs", "vite.config.ts",
                    "package.json"]


def test_a_project_without_code_has_no_code_paths(tmp_path):
    assert code_paths(project(tmp_path, ["docs/a.md", "notes.txt"])) == []



def test_what_runs_without_a_code_suffix_is_code_too(tmp_path):
    """Security review of the code-paths change, 4: CI, hooks, manifests and build files run, whatever their suffix."""
    found = code_paths(project(tmp_path, ["src/a.ts", ".github/workflows/ci.yml", ".claude/settings.json",
                                          "package.json", "Makefile", "app/Dockerfile", "docs/a.md"]))
    for name in (".github/workflows/ci.yml", ".claude/settings.json", "package.json", "Makefile", "app/Dockerfile"):
        assert approval.matches(name, found), name
    assert not approval.matches("docs/a.md", found)
