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
    assert judge("gh pr merge 12 --squash", root, tmp_path, run=fake_gh((0, head + "\n"))) is None
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
    assert "GH_TOKEN=<redacted>" in stored and "https://max:<redacted>@github.com/a/b" in stored
