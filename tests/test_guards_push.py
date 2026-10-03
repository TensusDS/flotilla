import json
import subprocess

import pytest

from flotilla.guards import overrides, push, rules, shell
from flotilla.core import repo
from guardkit import IDENTITY, first, git, onboarded, receipt


def env(tmp_path):
    return {"FLOTILLA_STATE_DIR": str(tmp_path / "state")}


def judge(command, root, tmp_path, run=subprocess.run, cwd=None):
    profile, _ = rules.rules_for(root)
    return push.guard(first(command, cwd or root), root=root, profile=profile, env=env(tmp_path), run=run)


@pytest.mark.parametrize("command,kind", [
    ("git push origin main", "git push"),
    ("git -C . push", "git push"),
    ("gh pr create --fill", "gh pr create"),
    ("gh pr merge 12 --squash", "gh pr merge"),
    ("gh workflow run ci.yml", "gh workflow run"),
])
def test_doors_are_recognised(command, kind, tmp_path):
    assert push.door(first(command, tmp_path)).kind == kind


@pytest.mark.parametrize("command", ["git push --dry-run origin main", "git push origin --delete old",
                                     "git push -h", "gh pr create --help", "git log --grep=push"])
def test_forms_that_publish_nothing_are_not_doors(command, tmp_path):
    segment = first(command, tmp_path)
    assert push.door(segment) is None


def test_a_push_to_trunk_without_a_receipt_is_refused(tmp_path):
    root = onboarded(tmp_path)
    found = judge("git push origin main", root, tmp_path)
    assert found.refuse and "receipt" in found.text and "FLOTILLA_GATE_OVERRIDE" in found.text


def test_a_green_receipt_opens_the_push(tmp_path):
    root = onboarded(tmp_path)
    receipt(root, tmp_path / "state")
    assert judge("git push origin main", root, tmp_path) is None


def test_a_branch_other_than_trunk_needs_no_receipt(tmp_path):
    root = onboarded(tmp_path)
    git(root, "checkout", "-q", "-b", "feat/x")
    assert judge("git push -u origin feat/x", root, tmp_path) is None
    assert judge("git push", root, tmp_path) is None


def test_a_branch_pushed_onto_trunk_needs_a_receipt_over_that_branch(tmp_path):
    root = onboarded(tmp_path)
    git(root, "checkout", "-q", "-b", "feat/x")
    (root / "g.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "g.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "x")
    assert judge("git push origin feat/x:main", root, tmp_path).refuse


def test_a_tag_needs_a_receipt(tmp_path):
    root = onboarded(tmp_path)
    git(root, *IDENTITY, "tag", "-a", "v1", "-m", "v1")
    assert judge("git push origin v1", root, tmp_path).refuse
    receipt(root, tmp_path / "state")
    assert judge("git push origin v1", root, tmp_path) is None


def test_an_override_at_the_doors_head_opens_it_and_is_recorded(tmp_path):
    root = onboarded(tmp_path)
    found = judge('FLOTILLA_GATE_OVERRIDE="hotfix, tiers run by hand" git push origin main', root, tmp_path)
    assert not found.refuse and "override recorded" in found.text
    saved = overrides.recorded(tmp_path / "state", repo.identify(root).key)
    assert saved[-1]["reason"] == "hotfix, tiers run by hand" and saved[-1]["guard"] == "push_receipt"


def test_an_override_in_a_comment_opens_nothing(tmp_path):
    root = onboarded(tmp_path)
    assert judge("git push origin main # FLOTILLA_GATE_OVERRIDE=forgot", root, tmp_path).refuse


def test_an_override_on_a_neighbour_opens_nothing(tmp_path):
    root = onboarded(tmp_path)
    segments = shell.segments('FLOTILLA_GATE_OVERRIDE="dry" echo x && git push origin main', root)
    profile, _ = rules.rules_for(root)
    found = [push.guard(s, root=root, profile=profile, env=env(tmp_path), run=subprocess.run) for s in segments]
    assert any(f is not None and f.refuse for f in found)


def test_a_tree_that_cannot_be_named_is_refused(tmp_path):
    root = onboarded(tmp_path)
    found = judge("cd $D && git push origin main", root, tmp_path)
    assert found.refuse and "which tree" in found.text


def test_a_workflow_the_profile_does_not_record_voids_the_receipt(tmp_path):
    root = onboarded(tmp_path, extra='\n[ci]\nprovider = "github"\nworkflow_fingerprint = "sha256:old"\n')
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    git(root, "add", ".github")
    git(root, *IDENTITY, "commit", "-q", "-m", "ci")
    receipt(root, tmp_path / "state")
    found = judge("git push origin main", root, tmp_path)
    assert found.refuse and "CI workflow" in found.text


def test_the_workflow_digest_at_a_revision_is_onboardings_digest(tmp_path):
    from flotilla.onboard.detect_ci import fingerprint, workflow_files
    root = onboarded(tmp_path)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (root / ".github" / "workflows" / "b.yaml").write_text("on: pull_request\n", encoding="utf-8")
    git(root, "add", ".github")
    git(root, *IDENTITY, "commit", "-q", "-m", "ci")
    sha = git(root, "rev-parse", "HEAD")
    assert push.workflow_at(root, sha) == fingerprint(root, workflow_files(root))


def fake_gh(answer):
    def run(cmd, **kwargs):
        if cmd[0] == "gh":
            code, out = answer
            return subprocess.CompletedProcess(cmd, code, out, "" if code == 0 else "gh: no")
        return subprocess.run(cmd, **kwargs)
    return run


def test_a_merge_asks_the_pr_for_its_head(tmp_path):
    root = onboarded(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    assert judge("gh pr merge 12 --squash", root, tmp_path, run=fake_gh((0, head + "\n"))).refuse
    receipt(root, tmp_path / "state")
    assert judge(f"gh pr merge 12 --squash --match-head-commit {head}", root, tmp_path,
                 run=fake_gh((0, head + "\n"))) is None
    found = judge("gh pr merge 12", root, tmp_path, run=fake_gh((1, "")))
    assert found.refuse and "gh pr view" in found.text


def test_another_repository_named_with_R_is_refused(tmp_path):
    root = onboarded(tmp_path)
    assert judge("gh pr merge 12 -R someone/else", root, tmp_path).refuse


def test_before_onboarding_reaches_trunk_the_trees_profile_is_obeyed(tmp_path):
    root = onboarded(tmp_path, push=False)
    profile, note = rules.rules_for(root)
    assert (profile.get("guards") or {}).get("push_receipt") and "carries no profile" in note


def lines(root, *pairs):
    return "".join(f"{local} {sha} {remote} {'0' * 40}\n" for local, sha, remote in pairs)


def test_pre_push_asks_git_what_is_pushed(tmp_path):
    root = onboarded(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    code, text = push.pre_push(root, lines(root, ("refs/heads/main", head, "refs/heads/main")), env=env(tmp_path))
    assert code == 1 and head[:7] in text
    assert push.pre_push(root, lines(root, ("refs/heads/x", head, "refs/heads/x")), env=env(tmp_path))[0] == 0
    assert push.pre_push(root, lines(root, ("(delete)", "0" * 40, "refs/heads/main")), env=env(tmp_path))[0] == 0
    receipt(root, tmp_path / "state")
    assert push.pre_push(root, lines(root, ("refs/heads/main", head, "refs/heads/main")), env=env(tmp_path))[0] == 0


def test_pre_push_override_is_recorded(tmp_path):
    root = onboarded(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    code, text = push.pre_push(root, lines(root, ("refs/heads/main", head, "refs/heads/main")),
                               env={**env(tmp_path), "FLOTILLA_GATE_OVERRIDE": "release by hand"})
    assert code == 0 and "override recorded" in text
    assert overrides.recorded(tmp_path / "state", repo.identify(root).key)[-1]["what"][0].startswith(head[:12])


def test_pre_push_obeys_the_guard_being_off(tmp_path):
    root = onboarded(tmp_path, guards=("revert",))
    head = git(root, "rev-parse", "HEAD")
    assert push.pre_push(root, lines(root, ("refs/heads/main", head, "refs/heads/main")), env=env(tmp_path))[0] == 0


def test_a_push_from_a_subdirectory_reads_the_workflow_of_the_whole_tree(tmp_path):
    from flotilla.onboard.detect_ci import fingerprint, workflow_files
    root = onboarded(tmp_path)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    with open(root / ".flotilla" / "project.toml", "a", encoding="utf-8") as profile:
        profile.write(f'\n[ci]\nworkflow_fingerprint = "{fingerprint(root, workflow_files(root))}"\n')
    (root / "sub").mkdir()
    (root / "sub" / "keep.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, *IDENTITY, "commit", "-q", "-m", "ci")
    receipt(root, tmp_path / "state")
    assert judge("git push origin main", root, tmp_path, cwd=root / "sub") is None


def test_the_first_push_after_onboarding_can_get_its_receipt(tmp_path, monkeypatch):
    from flotilla import cli
    root = onboarded(tmp_path, push=False)
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["receipt", "run", "--purpose", "push", "--tree", str(root), "--no-lane"]) == 0
    assert judge("git push origin main", root, tmp_path) is None


def human_project(tmp_path):
    root = onboarded(tmp_path)
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text().replace('[flow]\nmode = "direct"\n',
                                                   '[flow]\nmode = "direct"\nmerge_authorized_by = "human"\n'),
                       encoding="utf-8")
    git(root, "add", ".flotilla")
    git(root, *IDENTITY, "commit", "-q", "-m", "a person authorizes merges")
    git(root, "push", "-q", "origin", "main")
    (root / "w.txt").write_text("work nobody approved\n", encoding="utf-8")
    git(root, "add", "w.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "unapproved work")
    receipt(root, tmp_path / "state")
    return root


def test_where_a_person_authorizes_merges_a_push_of_unapproved_work_to_trunk_is_refused(tmp_path):
    """queue waited for the person's approve, but a push to trunk asked only for a receipt (review of the approve
    move): the sender could push unapproved work and record it afterwards."""
    root = human_project(tmp_path)
    found = judge("git push origin main", root, tmp_path)
    assert found is not None and found.refuse and "not approved" in found.text
    head = git(root, "rev-parse", "HEAD")
    code, text = push.pre_push(root, lines(root, ("refs/heads/main", head, "refs/heads/main")), env=env(tmp_path))
    assert code == 1 and "not approved" in text


def test_where_the_sender_authorizes_merges_the_same_push_opens(tmp_path):
    root = onboarded(tmp_path)
    (root / "w.txt").write_text("work\n", encoding="utf-8")
    git(root, "add", "w.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "work")
    receipt(root, tmp_path / "state")
    assert judge("git push origin main", root, tmp_path) is None


def test_an_override_record_keeps_no_token(tmp_path):
    """The override log stored the command text whole, so `GH_TOKEN=...` or a token in a remote URL sat in a state
    file (security review F23)."""
    overrides.record_override(tmp_path / "state", "k", "push_receipt", "hotfix",
                              ["GH_TOKEN=ghp_secret123 git push https://max:tok456@github.com/a/b main"])
    stored = json.dumps(overrides.recorded(tmp_path / "state", "k"))
    assert "ghp_secret123" not in stored and "tok456" not in stored
    assert "GH_TOKEN=<redacted>" in stored and "https://<redacted>@github.com/a/b" in stored



def test_a_merge_is_pinned_to_the_head_whose_receipt_was_checked(tmp_path):
    """The receipt was checked over the PR's head at hook time, but gh merged whatever head the PR had then - and
    with --auto, whatever it had later (security review F20, F22)."""
    root = onboarded(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    receipt(root, tmp_path / "state")
    found = judge("gh pr merge 12 --squash --auto", root, tmp_path, run=fake_gh((0, head + "\n")))
    assert found.refuse and f"--match-head-commit {head}" in found.text
    found = judge(f"gh pr merge 12 --match-head-commit {'0' * 40}", root, tmp_path, run=fake_gh((0, head + "\n")))
    assert found.refuse and "--match-head-commit" in found.text
    assert judge(f"gh pr merge 12 --auto --match-head-commit={head}", root, tmp_path,
                 run=fake_gh((0, head + "\n"))) is None


def test_a_gh_door_naming_a_repository_from_outside_any_project_is_refused(tmp_path):
    """`cd /tmp && gh pr merge 12 -R owner/repo` was judged by the shell's directory, which is no project, so it
    went unchecked (security review F21)."""
    root = onboarded(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    found = judge("gh pr merge 12 -R someone/repo", root, tmp_path, cwd=outside)
    assert found is not None and found.refuse


def test_a_push_into_a_project_is_judged_by_that_project_from_anywhere(tmp_path):
    """The guards ran only for the project the session stands in: a push into another onboarded project, from a
    directory outside any, skipped its receipt (security review F18)."""
    import io
    from flotilla import hooks
    root = onboarded(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = {"cwd": str(outside), "tool_input": {"command": f"git -C {root} push origin main"}}
    out = io.StringIO()
    hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out)
    said = json.loads(out.getvalue() or "{}").get("hookSpecificOutput") or {}
    assert said.get("permissionDecision") == "deny" and "receipt" in said.get("permissionDecisionReason", "")


def test_a_push_from_a_project_without_the_guard_into_one_with_it_is_judged(tmp_path):
    from flotilla.guards.run import evaluate
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    here = onboarded(tmp_path / "a", guards=("revert",))
    there = onboarded(tmp_path / "b")
    found = evaluate(f"git -C {there} push origin main", here, here, env=env(tmp_path))
    assert any(item.refuse and "receipt" in item.text for item in found)


def test_the_override_opens_a_missing_receipt_never_a_missing_approval(tmp_path):
    """The override is for a receipt the person chose to skip; it opened the approval gate too, and the refusal
    told the session how (review of the low-finding fixes)."""
    root = human_project(tmp_path)
    found = judge('FLOTILLA_GATE_OVERRIDE="hotfix" git push origin main', root, tmp_path)
    assert found is not None and found.refuse and "not approved" in found.text
    head = git(root, "rev-parse", "HEAD")
    code, text = push.pre_push(root, lines(root, ("refs/heads/main", head, "refs/heads/main")),
                               env={**env(tmp_path), "FLOTILLA_GATE_OVERRIDE": "hotfix"})
    assert code == 1 and "not approved" in text


def test_what_goes_to_trunk_is_measured_against_the_remote_not_a_local_ref(tmp_path):
    """`git update-ref refs/remotes/origin/main HEAD` made nothing outgoing, and both barriers passed."""
    root = human_project(tmp_path)
    remote_sha = git(root, "rev-parse", "refs/remotes/origin/main")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert judge("git push origin main", root, tmp_path).refuse
    head = git(root, "rev-parse", "HEAD")
    pushed = f"refs/heads/main {head} refs/heads/main {remote_sha}\n"
    code, text = push.pre_push(root, pushed, env=env(tmp_path))
    assert code == 1 and "not approved" in text


@pytest.mark.parametrize("command", ["git push --all origin", "git push --mirror origin",
                                     "git push origin 'refs/heads/*:refs/heads/*'"])
def test_a_push_that_may_carry_trunk_is_asked_for_approval(tmp_path, command):
    root = human_project(tmp_path)
    found = judge(command, root, tmp_path)
    assert found is not None and found.refuse and "not approved" in found.text


def test_a_repeated_pin_is_refused(tmp_path):
    """gh takes the last --match-head-commit; the guard read the first."""
    root = onboarded(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    receipt(root, tmp_path / "state")
    found = judge(f"gh pr merge 12 --match-head-commit {head} --match-head-commit {'1' * 40}", root, tmp_path,
                  run=fake_gh((0, head + "\n")))
    assert found.refuse


def test_a_push_through_git_dir_is_not_waved_through(tmp_path):
    root = onboarded(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    found = judge(f"GIT_DIR={root}/.git git push origin main", root, tmp_path, cwd=outside)
    assert found is not None and found.refuse


def test_a_global_gh_repo_does_not_block_work_outside_flotilla(tmp_path):
    root = onboarded(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    profile, _ = rules.rules_for(root)
    found = push.guard(first("gh pr create --fill", outside), root=None, profile={},
                       env={**env(tmp_path), "GH_REPO": "someone/else"})
    assert found is None


@pytest.mark.parametrize("command,secret", [
    ("git push https://ghp_tokenAsUser@github.com/a/b main", "ghp_tokenAsUser"),
    ("curl -H 'Authorization: Bearer abc123def' https://x", "abc123def"),
    ('GH_TOKEN="two words" git push', "two words"),
])
def test_an_override_record_keeps_no_secret_in_any_common_form(tmp_path, command, secret):
    overrides.record_override(tmp_path / "state", "k", "push_receipt", "why", [command])
    assert secret not in json.dumps(overrides.recorded(tmp_path / "state", "k"))


def test_a_foreign_repositorys_config_runs_nothing_before_its_push_is_allowed(tmp_path):
    """Scan of 0.6.10, F3: the guard asked origin in a directory the command names - one nobody has allowed yet -
    under that repository's own config, so a downloaded repository's `uploadpack` ran a program inside the hook
    before the person said anything. Outside the session's own repository origin is asked only when the config is
    plain; otherwise the push is refused, and the guard says why."""
    (tmp_path / "downloads").mkdir()
    (tmp_path / "mine").mkdir()
    foreign = human_project(tmp_path / "downloads")
    mine = onboarded(tmp_path / "mine")
    marker = tmp_path / "ran"
    git(foreign, "config", "remote.origin.uploadpack", f"touch {marker}; git-upload-pack")
    found = judge(f"git -C {foreign} push origin main", mine, tmp_path)
    assert not marker.exists()
    assert found is not None and found.refuse and "asked safely" in found.text


def test_a_foreign_repository_with_a_plain_config_is_still_judged(tmp_path):
    (tmp_path / "downloads").mkdir()
    (tmp_path / "mine").mkdir()
    foreign = human_project(tmp_path / "downloads")
    mine = onboarded(tmp_path / "mine")
    found = judge(f"git -C {foreign} push origin main", mine, tmp_path)
    assert found is not None and found.refuse and "not approved" in found.text


def test_the_sessions_own_repository_is_asked_whatever_its_config_holds(tmp_path):
    """Ordinary local keys (an editor's `branch.<name>.vscode-merge-base`, `pull.rebase`) would close every push
    if the own repository's config had to be plain; its config is the person's, and the door is their own."""
    root = human_project(tmp_path)
    git(root, "config", "branch.main.vscode-merge-base", "origin/main")
    other = tmp_path / "sibling"
    git(root, "worktree", "add", "-q", "-b", "fleet/x", str(other), "main")
    for command in ("git push origin main", f"git -C {other} push origin HEAD:main"):
        found = judge(command, root, tmp_path)
        assert found is not None and found.refuse and "unapproved work" in found.text, command   # the commit, named
        assert "asked safely" not in found.text, command


def test_an_override_never_waives_the_approval_that_could_not_be_asked(tmp_path):
    """Final review of the scan fixes, I2: where origin could not be asked safely, which commits a person approved
    could not be told - and that refusal came through as an ordinary failure, which FLOTILLA_GATE_OVERRIDE lets past.
    The person's approval has no override, so neither has the question that stands in for it."""
    (tmp_path / "downloads").mkdir()
    (tmp_path / "mine").mkdir()
    foreign = human_project(tmp_path / "downloads")
    mine = onboarded(tmp_path / "mine")
    git(foreign, "config", "pull.rebase", "true")   # an ordinary key, but not a plain one
    profile, _ = rules.rules_for(mine)
    found = push.guard(first(f"git -C {foreign} push origin main", mine), root=mine, profile=profile,
                       env={**env(tmp_path), "FLOTILLA_GATE_OVERRIDE": "the session says so"})
    assert found is not None and found.refuse and "asked safely" in found.text


def test_a_directory_pointing_its_git_dir_at_the_sessions_repository_is_not_its_own(tmp_path):
    """Final review of the scan fixes, I4: "own" was judged by the git common directory, and a downloaded directory
    whose `.git/commondir` names the session's repository shares it - while its own `config.worktree` (read where
    the repository sets extensions.worktreeConfig, as sparse-checkout does) ran a pack command inside the hook. Own
    is a checkout the session's repository lists as its worktree."""
    import shutil
    root = human_project(tmp_path)
    git(root, "config", "extensions.worktreeConfig", "true")
    marker = tmp_path / "ran"
    evil = tmp_path / "evil"
    (evil / ".git").mkdir(parents=True)
    (evil / ".git" / "commondir").write_text(f"{root / '.git'}\n", encoding="utf-8")
    (evil / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (evil / ".git" / "config.worktree").write_text(
        f'[remote "origin"]\n\tuploadpack = touch {marker}; git-upload-pack\n', encoding="utf-8")
    shutil.copytree(root / ".flotilla", evil / ".flotilla")
    found = judge(f"git -C {evil} push origin main", root, tmp_path)
    assert not marker.exists()
    assert found is not None and found.refuse


@pytest.mark.parametrize("edit", ['branch = "nope"', 'branch = "main-old"'])
def test_a_tree_that_renames_trunk_does_not_switch_off_the_push_guard(tmp_path, edit):
    """Scan of 0.7.0, F1: the guards took trunk's name from the tree's own profile, and when the local ref of that
    name carried no profile they obeyed the tree's file - so a session renamed trunk in an uncommitted edit, and a
    push to the real trunk was judged as a push to some other branch: no receipt, no person's approval. Trunk is
    origin's default branch, and the rules are the profile it carries."""
    root = human_project(tmp_path)   # origin: a person approves merges; the tree carries unapproved work
    profile = root / ".flotilla" / "project.toml"
    profile.write_text(profile.read_text(encoding="utf-8").replace('branch = "main"', edit), encoding="utf-8")
    found = judge("git push origin main", root, tmp_path)
    assert found is not None and found.refuse and "not approved" in found.text
    # the approval is closed - judged unapproved, or (the tree's renamed trunk keeps the ledger from opening) not
    # askable, which closes it just the same - and no override opens it
    overridden = judge('FLOTILLA_GATE_OVERRIDE="hotfix" git push origin main', root, tmp_path)
    assert overridden is not None and overridden.refuse
    head = git(root, "rev-parse", "HEAD")
    code, text = push.pre_push(root, lines(root, ("refs/heads/main", head, "refs/heads/main")),
                               env={**env(tmp_path), "FLOTILLA_GATE_OVERRIDE": "hotfix"})
    assert code == 1 and "not approved" in text


def test_before_trunk_carries_a_profile_the_trees_own_is_obeyed(tmp_path):
    """The first push of an onboarding has no rules on origin to be judged by: the tree's profile counts, and says
    so - but trunk is still origin's default branch, whatever the tree names."""
    root = onboarded(tmp_path, push=False)
    found = judge("git push origin main", root, tmp_path)
    assert found is not None and found.refuse and "receipt" in found.text   # the tree's push_receipt guard is on


def test_a_trunk_that_is_not_origins_default_branch_keeps_its_guard(tmp_path):
    """Review of the scan fixes of 0.7.0, I1: the profile's trunk was overwritten with origin's default branch, so a
    project whose trunk is `develop` lost its guard there without a word. The profile on origin's default branch
    says which branch is trunk."""
    root = human_project(tmp_path)
    profile = root / ".flotilla" / "project.toml"
    git(root, "switch", "-q", "-c", "develop", "origin/main")
    profile.write_text(profile.read_text(encoding="utf-8").replace('branch = "main"', 'branch = "develop"'),
                       encoding="utf-8")
    git(root, *IDENTITY, "commit", "-q", "-am", "trunk is develop")
    git(root, "push", "-q", "origin", "develop")
    git(root, "push", "-q", "origin", "develop:main")   # the default branch's profile names develop too
    (root / "d.txt").write_text("unapproved on develop\n", encoding="utf-8")
    git(root, "add", "d.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "unapproved on develop")
    receipt(root, tmp_path / "state")
    found = judge("git push origin develop", root, tmp_path)
    assert found is not None and found.refuse and "not approved" in found.text


def test_a_branch_push_needs_neither_a_reachable_origin_nor_a_fetched_trunk(tmp_path):
    """Review of the scan fixes of 0.7.0, I2: every push door asked origin and wanted its trunk fetched before
    looking at what was pushed, so a feature branch could not be pushed with origin moved or out of reach. What
    lands on no trunk and no tag is judged by nothing, as before."""
    root = human_project(tmp_path)
    git(root, "switch", "-q", "-c", "feat/x")
    assert judge("git push -u origin feat/x", root, tmp_path) is None
    git(root, "config", "remote.origin.url", str(tmp_path / "gone.git"))   # origin out of reach
    assert judge("git push -u origin feat/x", root, tmp_path) is None
