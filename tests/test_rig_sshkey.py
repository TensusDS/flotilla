import os
import subprocess

import pytest

from flotilla.rig import sshkey


def keygen(calls):
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        path = argv[argv.index("-f") + 1]
        with open(path, "w") as handle:
            handle.write("PRIVATE\n")
        os.chmod(path, 0o600)
        with open(path + ".pub", "w") as handle:
            handle.write("ssh-ed25519 AAAAC3Nza flotilla rig\n")
        return subprocess.CompletedProcess(argv, 0, "", "")
    return run


def test_the_pair_is_made_once_with_no_passphrase_and_no_prompt(tmp_path):
    calls = []
    first = sshkey.ensure(home=tmp_path, run=keygen(calls))
    second = sshkey.ensure(home=tmp_path, run=keygen(calls))
    assert first == second == "ssh-ed25519 AAAAC3Nza flotilla rig" and len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:3] == ["ssh-keygen", "-t", "ed25519"] and argv[argv.index("-N") + 1] == ""
    assert argv[argv.index("-f") + 1] == str(tmp_path / ".ssh" / "flotilla_rig_ed25519")
    assert kwargs.get("stdin") == subprocess.DEVNULL


def test_a_stray_public_half_is_set_aside_before_making_the_pair(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "flotilla_rig_ed25519.pub").write_text("old\n")
    assert sshkey.ensure(home=tmp_path, run=keygen([])) == "ssh-ed25519 AAAAC3Nza flotilla rig"


def test_a_private_key_others_can_read_is_refused(tmp_path):
    sshkey.ensure(home=tmp_path, run=keygen([]))
    os.chmod(tmp_path / ".ssh" / "flotilla_rig_ed25519", 0o644)
    with pytest.raises(sshkey.KeyError_, match="chmod 600"):
        sshkey.ensure(home=tmp_path, run=keygen([]))


def test_a_failed_keygen_is_an_error(tmp_path):
    def broken(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "no ed25519 here")
    with pytest.raises(sshkey.KeyError_, match="no ed25519"):
        sshkey.ensure(home=tmp_path, run=broken)
