"""`flotilla rig run`: one command on a rented machine (rig design, section 7).

Order: the gate (nothing spent yet), a place in the session's line, room on a machine (two runs always; past them,
room the machine's readings show), the tree by revision, the run's own setup, the command, what comes back, the
record. Every path from the line onward ends in `finish_run`, so the queue never keeps a run this process left; a
signal (the Bash tool's timeout, a TaskStop) ends the run on the machine before the process goes.

A connection that breaks is asked about, never assumed: the machine says how the run ended, a run still going is
stopped there, and a machine that cannot be asked for `ASK_FOR` seconds is drained rather than handed to the next run.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from flotilla.lane import run as lane_run
from flotilla.lane import signature
from flotilla.rig import commands, journal as j, reaper, remote, sshkey, transfer
from flotilla.rig import settings as rs

RENEW = 300.0
POLL = 10.0
SLEEP = time.sleep
EXECUTE = lane_run.execute
ASK_FOR = 120.0
ASK_EVERY = 10.0
MARGIN_MB = 2048
GPU_MARGIN_MB = 1024
DISK_MARGIN_MB = 2048
KEEP_REVISIONS = 5
READ = None        # None: the READINGS script over ssh
CEILING = None     # None: the profile's max_run_seconds
KEY_PATH = None    # None: the rig's own key
BACKSTOP = 30      # the machine's own limit runs this much longer than ours: only a backstop for a dead field side
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SECRET = ("KEY", "TOKEN", "SECRET", "PASSWORD")
HOST_CHANGED = "REMOTE HOST IDENTIFICATION HAS CHANGED"
_cleaning = False


class Refused(RuntimeError):
    pass


class Lost(RuntimeError):
    """The machine cannot be asked: drained, never handed on."""


def _leave(number, _frame):
    if _cleaning:
        return
    raise SystemExit(128 + number)


def _say(text: str) -> None:
    line = f"flotilla: {text}"
    print(line, flush=True)
    print(line, file=sys.stderr, flush=True)


def main(state: Path, settings: rs.RigSettings, args) -> int:
    global _cleaning
    _cleaning = False
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGHUP)}
    for number in previous:
        signal.signal(number, _leave)
    try:
        return _main(state, settings, args)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _pairs(texts) -> list[str]:
    pairs = []
    for text in texts:
        name, eq, _ = text.partition("=")
        if not eq or not _NAME.fullmatch(name):
            raise Refused(f"--env {text!r} is not NAME=VALUE")
        if any(word in name.upper() for word in _SECRET):
            raise Refused(f"--env {name}: a name holding KEY, TOKEN, SECRET or PASSWORD never crosses")
        pairs.append(text)
    return pairs


class Box:
    """The ssh calls to one machine."""

    def __init__(self, state: Path, machine: j.Machine):
        self.machine = machine
        hosts = state / "rig" / "hosts"
        hosts.mkdir(parents=True, exist_ok=True)
        self.known = hosts / machine.id
        self.key = Path(KEY_PATH) if KEY_PATH else sshkey.paths()[0]

    def argv(self, script: str, *args, stdin: bool = False) -> list[str]:
        return remote.ssh_argv(self.machine.address, self.key, self.known, remote.line(script, *args), stdin=stdin)

    def call(self, script: str, *args, data=None, timeout=remote.SHORT) -> subprocess.CompletedProcess:
        argv = self.argv(script, *args, stdin=data is not None)
        try:
            if data is None:
                done = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
            elif isinstance(data, Path):
                with open(data, "rb") as handle:
                    done = subprocess.run(argv, stdin=handle, capture_output=True, timeout=timeout)
            else:
                done = subprocess.run(argv, input=data, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 255, b"", b"timed out")
        if HOST_CHANGED.encode() in (done.stderr or b""):
            raise Lost("the machine's host key changed")
        return done


def _readings(box: Box) -> dict:
    if READ is not None:
        return READ(box)
    done = box.call(remote.READINGS, remote.BASE)
    try:
        return {k: int(v) for k, v in (item.split("=") for item in done.stdout.decode().split())}
    except ValueError:
        return {}


def _status(text: str) -> dict:
    return dict(item.split("=", 1) for item in text.split() if "=" in item)


class Run:
    def __init__(self, state, settings, args):
        self.state, self.settings, self.args = state, settings, args
        self.rig = commands._rig(state)
        self.alive = commands._alive()
        self.run = self.machine = self.box = self.paths = None
        self.verdict, self.code, self.reason = "refused", 2, ""
        self.measured: dict = {}
        self.started = None
        self.sent_stop = False
        self.lost = False
        self.renewing = threading.Event()

    # -- the line and the room ------------------------------------------------------------------------------
    def take_room(self, session, deadline) -> bool:
        while True:
            head = self.rig.head(session.id, self.alive)
            if head is not None and head.id == self.run.id:
                machines = [m for m in self.rig.machines().values() if m.session == session.id]
                for machine in machines:
                    if machine.state not in (j.READY, j.BUSY) or not machine.address:
                        continue
                    roomy = False
                    if len(self.rig.on(machine.id)) >= j.FLOOR:
                        roomy = self.roomy(machine)
                    if self.rig.start_run(self.run.id, machine.id, alive=self.alive, roomy=roomy):
                        self.machine = self.rig.machines()[machine.id]
                        return True
                if not any(m.state in (j.READY, j.BUSY) for m in machines):
                    code = commands.raise_machine(self.state, self.settings, who=self.run.who,
                                                  why=self.args.why, root=self.args.root,
                                                  wait=max(1.0, min(deadline - time.monotonic(), 100.0)),
                                                  watchdog=45)
                    if code == 2:
                        self.reason = "no machine could be raised"
                        return False
                    if code == 0:
                        continue
            if time.monotonic() >= deadline:
                self.verdict, self.code, self.reason = "waited", 3, "no room within --wait"
                return False
            SLEEP(POLL)

    def roomy(self, machine) -> bool:
        readings = _readings(Box(self.state, machine))
        if not readings:
            return False
        if machine.cpus is None and readings.get("cpus"):
            gpu = readings.get("gpu_total_mb", -1)
            self.rig.note(machine.id, cpus=readings["cpus"], ram_mb=readings.get("mem_total_kb", 0) // 1024,
                          gpu_total_mb=None if gpu < 0 else gpu)
        gpu_free = readings.get("gpu_free_mb", -1)
        return readings.get("mem_kb", 0) // 1024 >= MARGIN_MB and (gpu_free < 0 or gpu_free >= GPU_MARGIN_MB)

    # -- renewal --------------------------------------------------------------------------------------------
    def renew(self):
        while True:
            runs = self.rig.runs()
            if runs.get(self.run.id) is None or runs[self.run.id].state != j.RUNNING:
                return
            self.rig.renew([self.machine.id])
            try:
                self.box.call(remote.TOUCH, remote.BEAT, timeout=30)
            except Lost:
                return
            if self.renewing.wait(RENEW):
                return

    # -- phases ---------------------------------------------------------------------------------------------
    def checked(self, done: subprocess.CompletedProcess, what: str) -> subprocess.CompletedProcess:
        if done.returncode == 255:
            self.ask(None)
            raise _Ended()
        if done.returncode != 0:
            self.verdict, self.code = "refused", 2
            self.reason = f"{what} failed: {(done.stderr or b'').decode(errors='replace').strip()[:200]}"
            raise _Ended()
        return done

    def ask(self, status_file):
        """After ssh's 255: how did it end? A status line - its own verdict; still running - stopped, cut;
        vanished - cut; no answer within ASK_FOR - lost, and the machine drains."""
        end = time.monotonic() + ASK_FOR
        while True:
            done = self.box.call(remote.STATUS, status_file or f"{self.paths.run}.none")
            if done.returncode != 255:
                said = done.stdout.decode().strip()
                if said.startswith("exit="):
                    self.read_status(said, stopped_here=False)
                elif said.startswith("running"):
                    self.stop()
                    self.verdict, self.code = "cut", 75
                else:
                    self.verdict, self.code = "cut", 75
                return
            if time.monotonic() >= end:
                raise Lost("the machine did not answer after the connection broke")
            SLEEP(ASK_EVERY)

    def read_status(self, said: str, *, stopped_here: bool):
        fields = _status(said)
        value = fields.get("exit", "")
        if value == "timeout":
            self.verdict, self.code = "ceiling", 124
        elif value == "stopped":
            self.verdict, self.code = ("stopped", 130) if stopped_here else ("cut", 75)
        elif value.lstrip("-").isdigit():
            self.code = int(value)
            self.verdict = "green" if self.code == 0 else "red"
        try:
            seconds = float(fields.get("seconds", "0"))
            cpu = float(fields.get("cpu_s", "0"))
            self.measured = {"peak_mb": int(fields.get("peak_kb", "0")) // 1024,
                             "gpu_mb": int(fields.get("gpu_mb", "0")),
                             "cores": round(cpu / seconds, 2) if seconds > 0 else 0.0}
        except ValueError:
            pass

    def stop(self):
        """End the run on the machine. Marked sent only once both calls returned: a STOP cut short by a second
        signal is sent again by the finally (review of 0.10.0)."""
        self.box.call(remote.STOP, self.paths.run_status, self.run.tag)
        self.box.call(remote.STOP, self.paths.setup_status, self.run.tag)
        self.sent_stop = True

    def execute(self, script, status_file, argv, ceiling) -> bool:
        """Run a phase streaming its output. True when it ended on the machine with a status to read."""
        line = self.box.argv(script, self.paths.run, status_file, self.run.tag, self.paths.cache,
                             int(ceiling) + BACKSTOP, *argv)
        try:
            result = EXECUTE(line, ceiling=ceiling)
        except (SystemExit, KeyboardInterrupt):
            self.stop()
            self.verdict, self.code = "stopped", 130
            raise
        if result.at_ceiling:
            self.stop()
            self.verdict, self.code = "ceiling", 124
            return False
        if result.exit == 255:
            self.ask(status_file)
            return False
        done = self.box.call(remote.STATUS, status_file)
        said = done.stdout.decode().strip()
        if said.startswith("exit="):
            self.read_status(said, stopped_here=self.sent_stop)
        else:
            self.code = result.exit
            self.verdict = "green" if result.exit == 0 else "red"
        return True

    def gets(self, gets, root):
        if not gets or self.lost:
            return
        argv = self.box.argv(remote.PACK, self.paths.run, *gets)
        cap = transfer.CAP + 2 ** 20
        with tempfile.NamedTemporaryFile(prefix="flotilla-get-", suffix=".tar", delete=False) as handle:
            spool = Path(handle.name)
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL)
            size = 0
            for chunk in iter(lambda: process.stdout.read(65536), b""):
                size += len(chunk)
                if size > cap:
                    process.kill()
                    break
                handle.write(chunk)
            process.wait(remote.LONG)
        try:
            if size > cap:
                print("flotilla: what came back is over the cap; nothing was placed", flush=True)
                return
            placed = transfer.unpack(root, spool, gets)
            if placed:
                print(f"flotilla: brought back {', '.join(placed)}", flush=True)
        except transfer.Refused as err:
            print(f"flotilla: nothing was placed: {err}", flush=True)
        finally:
            spool.unlink(missing_ok=True)


class _Ended(Exception):
    """A phase ended the run; the verdict is already set."""


def _main(state: Path, settings: rs.RigSettings, args) -> int:
    global _cleaning
    command = list(args.run_command or [])
    if command[:1] == ["--"]:
        command = command[1:]
    try:
        if not settings.on:
            raise Refused(rs.OFF_LINE)
        if not command:
            raise Refused("name the command after --: flotilla rig run [options] -- COMMAND")
        pairs = _pairs(args.env or [])
        root = transfer.same_repository(args.root, Path.cwd())
        puts = [transfer.relative(root, p) for p in args.put or []]
        gets = transfer.check_gets(root, [transfer.relative(root, g) for g in args.get or []])
        revision = transfer.gate(root, puts)
        key, profile = commands._project(root)
        who = commands._who(args.as_name)
    except (Refused, transfer.Refused, commands.MoveRefused) as err:
        _say(f"rig run: refused (exit 2) - {err}")
        return 2
    task = Run(state, settings, args)
    rig = task.rig
    session = next((s for s in rig.sessions().values() if s.state == j.OPEN), None)
    if session is None:
        asked = rig.ask(who, args.why, project=key)
        _say(f"rig run: refused (exit 2) - no rig session is open; request {asked.id} is recorded for the "
             f"orchestrator, who asks the person to paste: ! flotilla rig open --hours 2 --budget 1 --for {asked.id}")
        return 2
    one_pass = reaper.PASS.total_seconds() / 3600
    ahead = rig.spent(session.id) + (rig.rate(session.id) + settings.max_hourly) * one_pass
    if session.budget is not None and ahead >= reaper.DRAIN_AT * session.budget - reaper.CENT:
        _say(f"rig run: refused (exit 2) - session {session.id} would pass 90 % of its budget within one pass")
        return 2
    from flotilla.lane.procs import ProcessTable
    mark = ProcessTable.for_machine().start_mark(os.getpid()) or ""
    program = os.path.basename(command[0])
    ladder = signature.ladder(command, tree=str(root), project=key)
    task.run = rig.queue_run(session.id, who=who, project=key, revision=revision, program=program, ladder=ladder,
                             pid=os.getpid(), mark=mark)
    renewer = None
    try:
        if not task.take_room(session, time.monotonic() + float(args.wait)):
            return task.code
        task.started = time.monotonic()
        task.box = Box(state, task.machine)
        task.paths = remote.paths(transfer.project_slug(root, key), revision, task.run.tag)
        renewer = threading.Thread(target=task.renew, daemon=True)
        renewer.start()
        ceiling = float(CEILING or profile.max_run_seconds)
        _phases(task, root, revision, puts, gets, pairs, command, profile, ceiling)
        return task.code
    except _Ended:
        return task.code
    except (SystemExit, KeyboardInterrupt):
        task.verdict, task.code = "stopped", 130   # a signal in any phase: the finally ends the run on the machine
        raise
    except Lost as err:
        task.lost = True
        task.verdict, task.code, task.reason = "lost", 75, str(err)
        if task.machine is not None:
            with contextlib.suppress(j.RigError):
                rig.move(task.machine.id, j.DRAINING, reason=f"run {task.run.id}: {err}")
        return task.code
    finally:
        _cleaning = True
        task.renewing.set()
        if renewer is not None:
            renewer.join(35)
        if task.box is not None and not task.lost:
            with contextlib.suppress(Lost):
                if not task.sent_stop:
                    task.stop()
                task.box.call(remote.DROP, task.paths.run, task.paths.setup_status, task.paths.run_status)
        seconds = round(time.monotonic() - task.started, 1) if task.started else None
        hourly = task.machine.hourly if task.machine is not None else None
        cost = round(hourly * seconds / 3600, 6) if hourly is not None and seconds is not None else None
        rig.finish_run(task.run.id, task.verdict, exit=task.code, seconds=seconds, cost=cost, reason=task.reason,
                       **{k: v for k, v in task.measured.items() if v is not None})
        on = f" on {task.machine.id}" if task.machine is not None else ""
        _say(f"rig run {task.run.id}: {task.verdict} (exit {task.code}) in {seconds or 0:g} s{on}, "
             f"{cost or 0:.4f} $" + (f" - {task.reason}" if task.reason else ""))


def _phases(task: Run, root, revision, puts, gets, pairs, command, profile, ceiling):
    box, paths, rig = task.box, task.paths, task.rig
    for done in rig.runs().values():   # runs whose field side died outright: end what they left on this machine
        if done.machine == task.machine.id and done.state == j.DONE and done.verdict == "gone" and done.tag:
            box.call(remote.STOP, f"{paths.project}/runs/{done.tag}.run", done.tag)
    pruned = task.checked(box.call(remote.PRUNE, paths.project, KEEP_REVISIONS, paths.cache, DISK_MARGIN_MB),
                          "pruning")
    free = _status(pruned.stdout.decode()).get("free_mb", "0")
    if not free.isdigit() or int(free) < DISK_MARGIN_MB:
        task.reason = f"the machine's disk is full ({free} MB free)"
        raise _Ended()
    said = task.checked(box.call(remote.CHECK, paths.rev), "the revision check").stdout.decode().split("\n")
    if "gnu-tar no" in said:
        task.reason = "the image has no GNU tar"
        raise _Ended()
    if said[0].strip() == "absent":
        with tempfile.NamedTemporaryFile(prefix="flotilla-tree-", suffix=".tar", delete=False) as handle:
            tree = Path(handle.name)
        try:
            made = subprocess.run(["git", "-C", str(root), "archive", "--format=tar", "-o", str(tree), revision],
                                  capture_output=True, text=True)
            if made.returncode != 0:
                task.reason = f"git archive failed: {made.stderr.strip()[:200]}"
                raise _Ended()
            digest = hashlib.sha256(tree.read_bytes()).hexdigest()
            task.checked(box.call(remote.RECEIVE, paths.rev, digest, tree.stat().st_size, data=tree,
                                  timeout=remote.LONG), "sending the tree")
        finally:
            tree.unlink(missing_ok=True)
    task.checked(box.call(remote.STAGE, paths.rev, paths.run, timeout=remote.LONG), "staging the run")
    if puts:
        data = transfer.put_tar(root, puts)
        task.checked(box.call(remote.PUT, paths.run, hashlib.sha256(data).hexdigest(), len(data), data=data,
                              timeout=remote.LONG), "sending --put")
    began = time.monotonic()
    if profile.setup:
        if not task.execute(remote.SETUP, paths.setup_status, ["0", "bash", "-c", profile.setup], ceiling):
            raise _Ended()
        if task.verdict != "green":
            task.verdict, task.code, task.reason = "setup failed", 2, f"setup exited {task.code}"
            raise _Ended()
    remaining = max(1.0, ceiling - (time.monotonic() - began))
    task.rig.command_started(task.run.id)
    task.execute(remote.RUN, paths.run_status, [str(len(pairs)), *pairs, *command], remaining)
    task.gets(gets, root)
