"""A stand-in for the OpenSSH client, for tests that must not touch a network (rig design, section 10).

It reads the remote line after `root@host`, as ssh does, and runs it the way sshd would: in a child with a session
of its own and an environment of only PATH, HOME and LANG. Its own stdout and stderr are pipes it relays, so when it
dies the caller reads EOF while the child lives on - as with a real client whose connection broke. On SIGTERM it
exits 255 and leaves the child alone, as the OpenSSH client does. Files in the box steer it: `.drop` holds
`<marker> <seconds>` (a remote line carrying that marker loses its connection after that long), `.unreachable`
makes every call after a drop exit 255 at once.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from flotilla.rig import remote

STAND_IN = r'''#!{python}
import os, signal, subprocess, sys, threading, time
args = sys.argv[1:]
VALUE = {{"-o", "-F", "-i", "-p", "-l", "-c", "-m", "-b", "-e", "-J", "-L", "-R", "-D", "-S", "-W", "-w", "-E"}}
i = 0
while i < len(args) and not args[i].startswith("root@"):
    i += 2 if args[i] in VALUE else 1
options, line = args[:i], (args[i + 1] if i + 1 < len(args) else "")
box = {box!r}
if os.path.exists(box + "/.dropped") and os.path.exists(box + "/.unreachable"):
    sys.exit(255)
drop = None
if os.path.exists(box + "/.drop"):
    marker, seconds = open(box + "/.drop").read().rsplit(" ", 1)
    if marker in line:
        drop = float(seconds)
env = {{"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": box + "/root", "LANG": "C"}}
child = subprocess.Popen(["bash", "-c", line], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL if "-n" in options else None, start_new_session=True)

def gone(*_):
    os._exit(255)

signal.signal(signal.SIGTERM, gone)

def relay(src, dst):
    for chunk in iter(lambda: src.read1(65536), b""):
        dst.write(chunk)
        dst.flush()

threads = [threading.Thread(target=relay, args=(child.stdout, sys.stdout.buffer), daemon=True),
           threading.Thread(target=relay, args=(child.stderr, sys.stderr.buffer), daemon=True)]
for t in threads:
    t.start()
if drop is not None:
    time.sleep(drop)
    if child.poll() is None:
        open(box + "/.dropped", "w").close()
        if os.path.exists(box + "/.drop-by-kill"):
            os.kill(os.getpid(), signal.SIGKILL)   # the client killed outright, as by OOM or kill -9 (live check 2)
        os._exit(255)
code = child.wait()
for t in threads:
    t.join()          # as the OpenSSH client does: the channel closes when every process holding it has let go
sys.exit(255 if code < 0 else code)   # a remote killed by a signal: the client reports 255
'''


def machine(tmp_path: Path, monkeypatch) -> Path:
    box = tmp_path / "box"
    (box / "root").mkdir(parents=True, exist_ok=True)
    (box / "work").mkdir(parents=True, exist_ok=True)
    program = tmp_path / "bin" / "ssh"
    program.parent.mkdir(parents=True, exist_ok=True)
    program.write_text(STAND_IN.format(python=sys.executable, box=str(box)))
    program.chmod(0o755)
    monkeypatch.setattr(remote, "PROGRAM", str(program))
    monkeypatch.setattr(remote, "BASE", str(box / "work"))
    monkeypatch.setattr(remote, "BEAT", str(box / "root" / "flotilla-heartbeat"))
    return box


def drop_after(box: Path, marker: str, seconds: float) -> None:
    (box / ".drop").write_text(f"{marker} {seconds}")


def kill_on_drop(box: Path) -> None:
    (box / ".drop-by-kill").write_text("")


def unreachable_after_drop(box: Path) -> None:
    (box / ".unreachable").write_text("")


def _tagged_now(box: Path, tag: str) -> list[int]:
    found, entry = [], f"FLOTILLA_RUN={tag}".encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if entry not in (proc / "environ").read_bytes().split(b"\0"):
                continue
            if not os.path.realpath(proc / "cwd").startswith(str(box)):
                continue
        except OSError:
            continue
        found.append(int(proc.name))
    return found


def tagged(box: Path, tag: str, wait: float = 5.0) -> list[int]:
    """The processes carrying this run's tag whose working directory is in the box; waits up to `wait` seconds for
    them to be gone, since a process just killed may not be reaped yet."""
    end = time.time() + wait
    while True:
        found = _tagged_now(box, tag)
        if not found or time.time() >= end:
            return found
        time.sleep(0.1)
