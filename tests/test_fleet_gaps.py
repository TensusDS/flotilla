"""A fleet nobody leads, raised without a word (twosuns field test of 0.6.7, W3 and the person's remark: "the session
honestly says there is no orchestrator - it should offer to become one, or to raise one; I know what to do, a new
user may not"). A profile written before the orchestrator and the sender were always part of the composition raised
`main, review, judge`, and the seats waited for work nobody routes. `spawn` now says, in its dry run and after a
launch, when the fleet it leaves has nobody to lead it or nobody to merge, naming both ways out."""

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import lead
from test_fleet_cli import PROFILE, _a_session, run_cli


def _onboarded(tmp_path, monkeypatch, default, flow=None):
    from flotilla.onboard.tomlw import render_toml
    from flotilla.posts import install_templates
    from ledgerkit import commit, git, repo_with_origin
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    profile = {**PROFILE, "fleet": {"default": default, "model": "one"}, "flow": flow or PROFILE["flow"]}
    (root / ".flotilla" / "project.toml").write_text(render_toml(profile), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


def _census(monkeypatch, *sessions):
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: list(sessions))


def test_a_default_without_an_orchestrator_names_both_ways_to_lead(tmp_path, monkeypatch):
    root = _onboarded(tmp_path, monkeypatch, {"main": 1, "review": 1})
    _census(monkeypatch, _a_session("twosuns-7c", root))
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    assert code == 0 and "nobody leads" in out, out
    assert "flotilla spawn --lead" in out and "flotilla spawn -o 1" in out


def test_a_live_orchestrator_or_one_in_the_composition_leads(tmp_path, monkeypatch):
    root = _onboarded(tmp_path, monkeypatch, {"main": 1, "review": 1})
    _census(monkeypatch, _a_session("orchestrator 3", root))
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    assert code == 0 and "nobody leads" not in out, out
    _census(monkeypatch)
    code, out = run_cli("spawn", "-o", "1", "-M", "1", "--dry-run", "--root", str(root))
    assert code == 0 and "nobody leads" not in out, out


def test_a_recorded_lead_of_a_live_session_leads(tmp_path, monkeypatch):
    root = _onboarded(tmp_path, monkeypatch, {"main": 1, "review": 1})
    me = _a_session("twosuns-7c", root)
    _census(monkeypatch, me)
    lead.record(LocalLogStore(tmp_path / "state" / "fleet"), me.session_id, "orchestrator 1", now="t")
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    assert code == 0 and "nobody leads" not in out, out


def test_a_fleet_whose_sender_merges_needs_a_sender(tmp_path, monkeypatch):
    root = _onboarded(tmp_path, monkeypatch, {"orchestrator": 1, "main": 1},
                      flow={"mode": "direct", "merge_authorized_by": "sender"})
    _census(monkeypatch)
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    assert code == 0 and "nobody leads" not in out and "nobody merges" in out, out
    assert "flotilla spawn -s 1" in out
    code, out = run_cli("spawn", "-o", "1", "-M", "1", "-s", "1", "--dry-run", "--root", str(root))
    assert code == 0 and "nobody merges" not in out, out


def test_a_fleet_where_the_person_merges_needs_no_sender(tmp_path, monkeypatch):
    root = _onboarded(tmp_path, monkeypatch, {"orchestrator": 1, "main": 1},
                      flow={"mode": "pr", "merge_authorized_by": "human"})
    _census(monkeypatch)
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    assert code == 0 and "nobody merges" not in out, out


def test_the_gap_is_said_after_a_real_launch_too(tmp_path, monkeypatch):
    from flotilla.fleet import spawn
    root = _onboarded(tmp_path, monkeypatch, {"main": 1})
    _census(monkeypatch, _a_session("twosuns-7c", root))
    monkeypatch.setattr(spawn, "spawn", lambda ledger, counts, **kw: ([], []))
    code, out = run_cli("spawn", "--default", "--root", str(root))
    assert code == 0 and "nobody leads" in out, out


def test_lead_refuses_while_another_session_leads(tmp_path, monkeypatch):
    """Review of 0.6.8, I2: `/flotilla:spawn` recommends "this session leads it", and `--lead` only noted a live
    orchestrator - two sessions would route the same fleet's work."""
    root = _onboarded(tmp_path, monkeypatch, {"orchestrator": 1, "main": 1})
    me = _a_session("twosuns-7c", root)
    monkeypatch.setattr("flotilla.fleet.commands.calling_session", lambda sessions: me)
    monkeypatch.setattr("flotilla.core.caller.person_refusal", lambda what: "")
    _census(monkeypatch, me, _a_session("orchestrator 2", root))
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code != 0 and "orchestrator 2" in out and "already leads" in out, out
    other = _a_session("twosuns-9d", root)
    lead.record(LocalLogStore(tmp_path / "state" / "fleet"), other.session_id, "orchestrator 3", now="t")
    _census(monkeypatch, me, other)
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code != 0 and "twosuns-9d" in out, out
    _census(monkeypatch, me)
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code == 0, out


def test_fill_with_nothing_to_raise_still_names_a_gap(tmp_path, monkeypatch):
    """Review of 0.6.8, M1: an old default whose seats are all alive returned "nothing to raise" before the gap."""
    root = _onboarded(tmp_path, monkeypatch, {"main": 1})
    _census(monkeypatch, _a_session("main session 1", root))
    code, out = run_cli("spawn", "--fill", "--root", str(root))
    assert code == 0 and "nothing to raise" in out and "nobody leads" in out, out


def test_gaps_count_a_live_reviewer_under_its_alias(tmp_path, monkeypatch):
    """Review of 0.6.8, M8: the default names `review`, the post is `reviewer`."""
    root = _onboarded(tmp_path, monkeypatch, {"orchestrator": 1, "review": 1})
    _census(monkeypatch, _a_session("orchestrator 1", root), _a_session("review session 1", root))
    code, out = run_cli("spawn", "--fill", "--root", str(root))
    assert code == 0 and "nothing to raise" in out and "gap:" not in out, out


def test_the_dry_run_says_what_each_seat_is_allowed(tmp_path, monkeypatch):
    """0.6.10: seats are launched with allow rules; the person sees them before raising anything."""
    root = _onboarded(tmp_path, monkeypatch, {"orchestrator": 1, "sender": 1, "main": 1},
                      flow={"mode": "direct", "merge_authorized_by": "sender"})
    _census(monkeypatch)
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    allows = [line for line in out.splitlines() if "allows:" in line]
    assert code == 0 and len(allows) == 3, out
    assert sum("a push of `main`" in line for line in allows) == 1
