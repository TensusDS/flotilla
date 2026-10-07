"""The rig's own ssh key (rig design, section 6): made once, with no passphrase because cron and background seats use
it, and refused when others may read it. Its public half is attached to each instance before it is called ready; the person's own
keys are never offered to a rented machine."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

NAME = "flotilla_rig_ed25519"


class KeyError_(RuntimeError):
    """The rig's ssh key cannot be made or used."""


def paths(home: Path | None = None) -> tuple[Path, Path]:
    private = Path(home or Path.home()) / ".ssh" / NAME
    return private, private.with_name(NAME + ".pub")


def ensure(home: Path | None = None, run=subprocess.run) -> str:
    private, public = paths(home)
    if not private.exists():
        private.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if public.exists():   # a public half without its private one would make ssh-keygen ask to overwrite
            public.rename(public.with_name(NAME + ".pub.stray"))
        try:
            done = run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "flotilla rig", "-f", str(private), "-q"],
                       capture_output=True, text=True, timeout=60, check=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as err:
            raise KeyError_(f"ssh-keygen could not be run: {err}") from None
        if done.returncode != 0:
            raise KeyError_(f"ssh-keygen failed: {(done.stderr or done.stdout or '').strip()[:200]}")
    info = os.lstat(private)
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise KeyError_(f"{private} is not a private regular file; `chmod 600 {private}`")
    try:
        text = public.read_text(encoding="utf-8").strip()
    except OSError as err:
        raise KeyError_(f"{public} cannot be read: {err}") from None
    if not text.startswith("ssh-ed25519 "):
        raise KeyError_(f"{public} is not an ed25519 public key")
    return text
