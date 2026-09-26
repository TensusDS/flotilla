import json
import sys
from pathlib import Path

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.ledger import core, events, handover
from flotilla.ledger.errors import MoveRefused
from ledgerkit import actor, branch, make_ledger, repo_with_origin


def script(code, executable=True):
    return (f"#!{sys.executable}\n{code}\n".encode("utf-8"), executable)


def world_with(tmp_path, scripts, skip=None):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", events=scripts, skip_events=skip)
    branch(root, "feat/x", "work")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    return root, ledger


def hand(ledger):
    return handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")


def test_a_pre_script_can_reject_the_move(tmp_path):
    _, ledger = world_with(tmp_path, {"pre-handed": script("import sys; print('no ticket on this row'); sys.exit(2)")})
    with pytest.raises(MoveRefused, match="rejected the move: no ticket on this row"):
        hand(ledger)
    assert ledger.rows()["r1"].state == "claimed"


def test_a_pre_script_reads_the_move_on_stdin(tmp_path):
    seen = tmp_path / "seen.json"
    code = f"import sys, pathlib; pathlib.Path({str(seen)!r}).write_text(sys.stdin.read())"
    _, ledger = world_with(tmp_path, {"pre-handed": script(code)})
    hand(ledger)
    data = json.loads(seen.read_text(encoding="utf-8"))
    assert (data["event_schema"], data["event"], data["move"], data["state"]) == (1, "pre-handed", "hand", "handed")
    assert data["row"]["branch"] == "feat/x" and data["by"] == "main session 1" and data["row"]["tip"]


def test_a_broken_pre_script_refuses_and_is_counted(tmp_path):
    _, ledger = world_with(tmp_path, {"pre-handed": script("import sys; sys.exit(3)")})
    with pytest.raises(MoveRefused, match=r"failed \(exited 3.*flotilla events run pre-handed --row feat/x"):
        hand(ledger)
    failures = LocalLogStore(ledger.state_dir / "events").read(ledger.repo_key).records
    assert [(f["event"], f["status"], f["branch"]) for f in failures] == [("pre-handed", "broken", "feat/x")]
    assert ledger.rows()["r1"].state == "claimed"


def test_a_hung_pre_script_times_out(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "TIMEOUT", 1.0)
    _, ledger = world_with(tmp_path, {"pre-handed": script("import time; time.sleep(10)")})
    with pytest.raises(MoveRefused, match="did not finish within 1 s"):
        hand(ledger)


def test_a_script_that_is_not_executable_is_broken(tmp_path):
    _, ledger = world_with(tmp_path, {"pre-handed": script("pass", executable=False)})
    with pytest.raises(MoveRefused, match="not executable"):
        hand(ledger)


def test_a_skipped_event_is_recorded_and_the_move_proceeds(tmp_path):
    _, ledger = world_with(tmp_path, {"pre-handed": script("import sys; sys.exit(3)")},
                           skip={"pre-handed": "the script is being fixed"})
    row = hand(ledger)
    assert row.state == "handed"
    assert row.history[-1]["evidence"]["skipped_events"] == {"pre-handed": "the script is being fixed"}


def test_a_failing_post_script_leaves_the_move_standing(tmp_path):
    _, ledger = world_with(tmp_path, {"post-handed": script("import sys; sys.exit(1)")})
    assert hand(ledger).state == "handed"
    assert ledger.rows()["r1"].state == "handed"
    assert any("post-handed" in notice and "the move stands" in notice for notice in ledger.notices)


def test_annotations_fire_no_events(tmp_path):
    marker = tmp_path / "fired"
    code = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')"
    _, ledger = world_with(tmp_path, {})
    ledger.events = {"pre-claimed": script(code), "post-claimed": script(code)}
    handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="Max", why="a decision")
    assert not marker.exists()


def test_a_new_row_fires_the_event_of_the_state_it_opens_in(tmp_path):
    marker = tmp_path / "fired"
    code = f"import pathlib, sys; pathlib.Path({str(marker)!r}).write_text(sys.stdin.read())"
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", events={"post-claimed": script(code)})
    branch(root, "feat/x", "work")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    assert json.loads(marker.read_text(encoding="utf-8"))["event"] == "post-claimed"


ROOT = Path(__file__).resolve().parent.parent


def test_the_committed_schema_is_the_generated_one():
    committed = json.loads((ROOT / "docs" / "events" / "schema.json").read_text(encoding="utf-8"))
    assert committed == events.schema()


def test_the_payload_a_move_sends_has_exactly_the_schema_keys(tmp_path):
    seen = tmp_path / "seen.json"
    code = f"import sys, pathlib; pathlib.Path({str(seen)!r}).write_text(sys.stdin.read())"
    _, ledger = world_with(tmp_path, {"pre-handed": script(code)})
    hand(ledger)
    data = json.loads(seen.read_text(encoding="utf-8"))
    stdin = events.schema()["stdin"]
    assert set(data) == set(stdin) and set(data["row"]) == set(stdin["row"])


def test_the_example_script_follows_the_contract(tmp_path):
    body = (ROOT / "docs" / "events" / "examples" / "pre-closed").read_bytes()
    sample = events.sample_payload("pre-closed")
    assert events.run_event("pre-closed", (body, True), sample, cwd=tmp_path).status == events.REJECTED
    sample["row"]["ref"] = "LIN-12"
    assert events.run_event("pre-closed", (body, True), sample, cwd=tmp_path).status == events.OK


def test_check_names_every_broken_script(tmp_path):
    found = events.check({
        "pre-handed": script("pass"),
        "pre-nonsense": script("pass"),
        "post-closed": script("import sys; sys.exit(5)"),
        "pre-accepted": (b"#!/no/such/python\nprint(1)\n", True),
        "pre-queued": script("pass", executable=False),
    }, cwd=tmp_path)
    assert {name: status for name, status, _ in found} == {
        "pre-handed": "ok", "pre-nonsense": "unknown name", "post-closed": "broken", "pre-accepted": "broken",
        "pre-queued": "broken"}


def test_broke_is_all_or_nothing_when_a_pre_script_refuses_its_fix_row(tmp_path):
    from flotilla.ledger import judging
    from ledgerkit import PROFILE, shipped_direct
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "direct"}})
    shipped_direct(root, ledger)
    ledger.events = {"pre-claimed": script("import sys; print('no fix rows today'); sys.exit(2)")}
    with pytest.raises(MoveRefused, match="no fix rows today"):
        judging.broke(ledger, actor(ledger, "acceptance judge 1"), "feat/x", where="Export", saw="nothing")
    rows = ledger.rows()
    assert rows["r1"].broken == "" and len(rows) == 1


def test_post_script_output_that_is_not_utf8_does_not_crash_the_move(tmp_path):
    _, ledger = world_with(tmp_path, {"post-handed": script("import sys; sys.stdout.buffer.write(b'\\xff\\xfe'); sys.exit(1)")})
    assert hand(ledger).state == "handed"
    assert any("post-handed" in notice for notice in ledger.notices)
