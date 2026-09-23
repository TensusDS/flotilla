import subprocess
from pathlib import Path

import pytest

from flotilla.core import repo


@pytest.mark.parametrize("url", [
    "git@github.com:Owner/app.git",
    "ssh://git@github.com/Owner/app.git",
    "https://github.com/Owner/app",
    "https://user@GitHub.com/Owner/app.git/",
    "ssh://git@github.com:22/Owner/app.git",
])
def test_origin_forms_of_one_repository_normalize_alike(url):
    assert repo.normalize_origin(url) == "github.com/Owner/app"


def test_local_path_origin(tmp_path):
    assert repo.normalize_origin(str(tmp_path / "origin.git")) == f"path:{(tmp_path / 'origin.git').resolve()}"


def test_key_is_a_safe_slug_with_a_digest():
    key = repo.repo_key("github.com/Owner/app")
    assert key.startswith("github-com-owner-app-")
    assert len(key.rsplit("-", 1)[1]) == 12


def test_distinct_repositories_get_distinct_keys():
    assert repo.repo_key("github.com/a/app") != repo.repo_key("github.com/b/app")


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture()
def checkout(tmp_path):
    main = tmp_path / "app"
    main.mkdir()
    git("init", "-q", "-b", "main", cwd=main)
    git("-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i", cwd=main)
    git("remote", "add", "origin", "git@github.com:Owner/app.git", cwd=main)
    return main


def test_identify_reads_origin_and_root(checkout):
    ident = repo.identify(checkout / ".")
    assert ident.root == checkout.resolve()
    assert ident.origin == "git@github.com:Owner/app.git"
    assert ident.key == repo.repo_key("github.com/Owner/app")


def test_linked_worktree_has_the_main_checkout_key(checkout, tmp_path):
    tree = tmp_path / "app-main-2"
    git("worktree", "add", "-q", "-b", "feat/x", str(tree), cwd=checkout)
    assert repo.identify(tree).key == repo.identify(checkout).key
    assert repo.identify(tree).root == tree.resolve()


def test_repository_without_origin_is_keyed_by_its_common_dir(tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    git("init", "-q", "-b", "main", cwd=lone)
    ident = repo.identify(lone)
    assert ident.origin is None
    assert ident.key == repo.repo_key(f"local:{(lone / '.git').resolve()}")


def test_not_a_repository_is_named(tmp_path):
    with pytest.raises(repo.NotARepository):
        repo.identify(tmp_path)
