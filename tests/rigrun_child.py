"""A `flotilla rig run` in a process of its own, for tests where two runs share a machine: each run must have its
own pid. The seats' seams arrive through the environment; the machine was raised by the parent test, so this child
must never reach the provider."""

import os
import sys

from flotilla import cli
from flotilla.rig import commands, remote, run


def no_provider(_name):
    raise AssertionError("a background run must not reach the provider")


remote.PROGRAM = os.environ["RIG_TEST_SSH"]
remote.BASE = os.environ["RIG_TEST_BASE"]
remote.BEAT = os.environ["RIG_TEST_BEAT"]
run.KEY_PATH = os.environ["RIG_TEST_KEY"]
run.POLL = run.ASK_EVERY = 0.2
commands.PROVIDERS = no_provider
if os.environ.get("RIG_TEST_CLOCK"):   # the parent's clock: a journal written at its time must be read at its time
    import datetime as dt
    commands.CLOCK = lambda: dt.datetime.fromisoformat(os.environ["RIG_TEST_CLOCK"])
if os.environ.get("RIG_TEST_NO_ROOM"):
    run.READ = lambda box: {"mem_kb": 0, "mem_total_kb": 0, "cpus": 8, "gpu_free_mb": -1, "gpu_total_mb": -1}
sys.exit(cli.main(sys.argv[1:]))
