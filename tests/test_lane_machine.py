import json
import subprocess

from flotilla.core.storage import LocalLogStore
from flotilla.lane import book, machine
from flotilla.lane.procs import Proc


class Table:
    """A process table with CPU time that grows only where told to."""

    def __init__(self, procs, *, growing=(), ages=None, readable=True):
        self.procs = list(procs)
        self.growing = set(growing)
        self.ages = ages or {}
        self.readable = readable
        self.samples = {}

    def list(self):
        return list(self.procs) if self.readable else None

    def cpu_seconds(self, pid):
        self.samples[pid] = self.samples.get(pid, 0) + (1 if pid in self.growing else 0)
        return float(self.samples[pid])

    def age_seconds(self, pid):
        return self.ages.get(pid)

    def ancestors(self, pid):
        chain, by = [], {p.pid: p for p in self.procs}
        while pid in by:
            chain.append(pid)
            pid = by[pid].ppid
        return chain or [pid]

    def alive(self, pid, mark):
        return True


PROCS = [Proc(1, 0, "init"), Proc(10, 1, "bash -c uv run pytest"), Proc(11, 10, "uv run pytest -q"),
         Proc(20, 1, "python -m pytest tests"), Proc(30, 1, "vim notes.txt")]


def test_foreign_runs_match_patterns_and_skip_shells_and_the_caller():
    runs = machine.foreign_runs(Table(PROCS), machine.DEFAULT_PATTERNS, exclude={20})
    assert [p.pid for p in runs] == [11]


def test_a_profile_replaces_the_patterns():
    assert machine.patterns_for({"lane": {"run_patterns": [r"\bmake check\b"]}}) == [r"\bmake check\b"]
    assert machine.patterns_for({}) == list(machine.DEFAULT_PATTERNS)


def test_a_growing_or_young_run_is_computing():
    table = Table(PROCS, growing={11}, ages={11: 99999.0, 20: 60.0})
    busy, idle = machine.split_computing(table, [PROCS[2], PROCS[3]], sleep=lambda s: None)
    assert [p.pid for p in busy] == [11, 20] and idle == []


def test_an_old_idle_run_is_named_but_does_not_block(tmp_path):
    table = Table(PROCS, ages={11: 9 * 86400.0, 20: 9 * 86400.0})
    lanes = book.Book(LocalLogStore(tmp_path), table)
    reading = machine.read(lanes, table, {}, own_pid=999, root=tmp_path, sleep=lambda s: None)
    assert reading.computing == [] and sorted(p.pid for p in reading.idle) == [11, 20]
    runs = next(a for a in reading.answers if a.question == "foreign run")
    assert runs.blocks is False and "do not block" in runs.text


def test_an_unreadable_process_table_is_unknown(tmp_path):
    table = Table(PROCS, readable=False)
    lanes = book.Book(LocalLogStore(tmp_path), table)
    reading = machine.read(lanes, table, {}, own_pid=999, root=tmp_path, sleep=lambda s: None)
    runs = next(a for a in reading.answers if a.question == "foreign run")
    assert runs.blocks is None and "could not" in runs.text


def test_a_booked_run_is_not_a_foreign_run(tmp_path):
    table = Table(PROCS, growing={11, 20}, ages={})
    lanes = book.Book(LocalLogStore(tmp_path), table)
    mine = lanes.enqueue("main session 1", "", pid=10, mark="m")
    lanes.grant(mine.id, slots=1)
    reading = machine.read(lanes, table, {}, own_pid=999, root=tmp_path, sleep=lambda s: None)
    assert [p.pid for p in reading.computing] == [20]


def gh_answers(runs, jobs):
    def run(cmd, **kwargs):
        if cmd[:3] == ["gh", "run", "list"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(runs), "")
        if cmd[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps({"jobs": jobs}), "")
        return subprocess.CompletedProcess(cmd, 1, "", "no")
    return run


HERE = {"ci": {"provider": "github", "runs_on": "this-machine"}}


def test_ci_elsewhere_is_not_asked(tmp_path):
    answer = machine.ci_here({"ci": {"provider": "github", "runs_on": "cloud"}}, run=None, root=tmp_path)
    assert answer.blocks is False


def test_self_hosted_ci_running_here_blocks(tmp_path):
    run = gh_answers([{"status": "in_progress", "databaseId": 7}], [{"labels": ["self-hosted", "linux"]}])
    assert machine.ci_here(HERE, run=run, root=tmp_path).blocks is True


def test_self_hosted_ci_with_unknown_labels_blocks(tmp_path):
    run = gh_answers([{"status": "queued", "databaseId": 7}], [{"labels": []}])
    answer = machine.ci_here(HERE, run=run, root=tmp_path)
    assert answer.blocks is None and "not asked is not free" in answer.text


def test_an_idle_ci_queue_does_not_block(tmp_path):
    run = gh_answers([{"status": "completed", "databaseId": 7}], [])
    assert machine.ci_here(HERE, run=run, root=tmp_path).blocks is False
