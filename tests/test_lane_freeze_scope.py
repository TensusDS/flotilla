"""The real seam: a command in a user systemd scope, frozen, its memory moved to swap, thawed, and stopped while
frozen (lane shared machine design, section 4.2). Runs only where this machine's user manager has the memory
controller and can make a scope; elsewhere it says why it skipped."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from flotilla.lane import freeze, peak, run

HOLD = Path(__file__).parent / "fixtures" / "hold_memory.py"
KIND, WHY = freeze.backend()
pytestmark = pytest.mark.skipif(KIND != freeze.SCOPE, reason=f"no user scope here: {WHY}")


def _start(s):
    argv = s.contain([sys.executable, "-u", str(HOLD), "256"], booking="t1")
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, text=True, start_new_session=True)
    assert process.stdout.readline().strip() == "ready"
    s.attach(process.pid)
    return process


def _unit_state(unit):
    return subprocess.run(["systemctl", "--user", "show", "-p", "ActiveState", "--value", unit],
                          capture_output=True, text=True).stdout.strip()


def test_a_command_in_its_scope_freezes_gives_its_memory_to_swap_and_thaws():
    s = freeze.Slot(freeze.SCOPE)
    process = _start(s)
    try:
        assert s.unit and s.unit in Path(f"/proc/{process.pid}/cgroup").read_text()
        before = peak.tree_rss_mb(process.pid)
        assert before is not None and before >= 200
        found = s.freeze(300)
        assert found["freeze_kind"] == "scope" and found["unit"] == s.unit and found["held_mb"] >= 200
        assert peak.tree_rss_mb(process.pid) < 64
        s.thaw()
        assert not s.frozen()
    finally:
        process.kill()
        process.wait()


def test_stopping_a_frozen_command_ends_it_and_its_unit_goes():   # review focus 3
    s = freeze.Slot(freeze.SCOPE)
    process = _start(s)
    freeze.SLOT = s
    try:
        s.freeze(0)
        began = time.monotonic()
        run._stop(process)
        assert time.monotonic() - began < 6 and process.returncode is not None
    finally:
        freeze.SLOT = None
        if process.poll() is None:
            process.kill()
            process.wait()
    for _ in range(50):
        if _unit_state(s.unit) in ("inactive", ""):
            break
        time.sleep(0.1)
    assert _unit_state(s.unit) in ("inactive", "")
