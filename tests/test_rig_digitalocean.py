"""The DigitalOcean adapter against a double of the API as its specification describes it; the contract every
adapter keeps is in test_rig_contract.py. No test here reaches the service or holds a real token."""

import datetime as dt
import io
import json
import pathlib
import shutil
import subprocess
import time
from contextlib import redirect_stdout

import pytest

from flotilla import cli
from flotilla.core import paths
from flotilla.core.storage import LocalLogStore
from flotilla.onboard.tomlw import render_toml
from flotilla.rig import commands, cron
from flotilla.rig import journal as j
from flotilla.rig import provider as pv
from flotilla.rig.providers import digitalocean
from flotilla.rig.reaper import reap
from rigkit import PUBLIC_KEY, SIZES, FakeDigitalOcean, fake_key

BASE = "https://api.digitalocean.com"
#: `ssh-keygen -E md5 -lf` of rigkit.PUBLIC_KEY, taken once with the real tool (2026-11-18).
FINGERPRINT = "6a:64:33:c8:1d:7d:36:7b:6d:d2:e9:fa:08:04:b3:c1"
WANT = {"gpus": ["MI300X"], "max_hourly": 20.0, "min_reliability": 0.98, "disk_gb": 30, "limit": 64}


@pytest.fixture
def fake(monkeypatch):
    def make(*args, **knobs):
        double = FakeDigitalOcean(*args, **knobs)
        monkeypatch.setattr(digitalocean, "SEND", double)
        return double
    return make


def make_one(offer="gpu-mi300x1-192gb@atl1", **over):
    words = {"image": "gpu-image-1", "disk_gb": 30, "env": {"FLOTILLA_WATCHDOG_MINUTES": "45"},
             "onstart": digitalocean.onstart(), "label": "flotilla:0123456789ab:m1", "public_key": PUBLIC_KEY, **over}
    return digitalocean.create("k" * 12, offer, **words)


# ---- the listing

def test_the_listing_asks_for_plain_and_gpu_droplets_with_a_bearer_key(fake):
    double = fake({"101": {"label": "flotilla:0123456789ab:m1", "hourly": 1.99, "gpu": True, "ip": "203.0.113.7"},
                   "102": {"label": None}})
    rows = digitalocean.instances("account-key-0123456789")
    assert sorted(rows, key=lambda row: row["instance"]) == [
        {"instance": "101", "label": "flotilla:0123456789ab:m1", "status": "running", "hourly": 1.99,
         "address": "203.0.113.7:22"},
        {"instance": "102", "label": "", "status": "running", "hourly": 0.30, "address": ""}]
    asked = [url for _, url, _ in double.requests]
    assert all(url.startswith(f"{BASE}/v2/droplets?") for url in asked)
    assert [("type=gpus" in url) for url in asked] == [False, True]   # GPU Droplets are listed only when asked for
    assert all(headers["Authorization"] == "Bearer account-key-0123456789" and "account-key" not in url
               for _, url, headers in double.requests)


def test_a_gpu_droplet_the_plain_listing_leaves_out_is_still_found(fake):
    fake({str(n): {"label": f"flotilla:0123456789ab:m{n}", "gpu": True} for n in range(1, 4)})
    assert sorted(row["instance"] for row in digitalocean.instances("k" * 12)) == ["1", "2", "3"]


def test_pages_are_counted_here_and_a_next_link_is_never_followed(fake):
    """The double's `next` links point at another host: followed, the key would travel there."""
    double = fake({str(n): {"label": f"l{n}", "gpu": n % 2 == 0} for n in range(1, 30)}, page=4)
    assert len(digitalocean.instances("k" * 12)) == 29
    assert all(url.startswith(f"{BASE}/v2/droplets?") for _, url, _ in double.requests)
    assert "elsewhere.example" not in repr(double.requests)


def test_a_listing_that_never_ends_is_an_error(fake, monkeypatch):
    monkeypatch.setattr(digitalocean, "MAX_PAGES", 3)
    fake({str(n): {"label": "x"} for n in range(1, 30)}, page=2)
    with pytest.raises(digitalocean.AdapterError, match="did not end"):
        digitalocean.instances("k" * 12)


def test_the_label_is_the_flotilla_tag_among_the_droplets_tags(fake):
    fake({"7": {"label": "flotilla:0123456789ab:m2", "tags": ["env:prod", "web"]}, "8": {"tags": ["env:prod"]}})
    labels = {row["instance"]: row["label"] for row in digitalocean.instances("k" * 12)}
    assert labels == {"7": "flotilla:0123456789ab:m2", "8": ""}


@pytest.mark.parametrize("status,said,address", [("new", "loading", ""), ("active", "running", "203.0.113.7:22"),
                                                 ("off", "stopped", "203.0.113.7:22"),
                                                 ("archive", "stopped", "203.0.113.7:22")])
def test_a_droplets_state_is_said_in_the_rigs_words(fake, status, said, address):
    """`off` still costs money and runs nothing: it reads as `stopped`, which the reaper drains at once."""
    fake({"7": {"label": "x", "status": status, "ip": "203.0.113.7"}}, boot_polls=99)
    [row] = digitalocean.instances("k" * 12)
    assert (row["status"], row["address"]) == (said, address)


def test_a_droplet_without_a_priced_size_has_no_price_rather_than_a_guessed_one(monkeypatch):
    rows = {"droplets": [{"id": 7, "status": "active", "tags": [], "size": {"price_hourly": "1.99"}},
                         {"id": 8, "status": "active", "tags": [], "size": None},
                         {"id": True, "status": "active"}, "junk"], "links": {}}
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (200, json.dumps(rows).encode()))
    assert [(row["instance"], row["hourly"]) for row in digitalocean.instances("k" * 12)] == [("7", None), ("8", None)]


def test_an_address_that_is_not_a_public_ipv4_is_not_an_address(monkeypatch):
    nets = {"v4": [{"ip_address": "10.0.0.5", "type": "private"}, {"ip_address": "-oProxyCommand=x", "type": "public"}]}
    rows = {"droplets": [{"id": 7, "status": "active", "tags": [], "networks": nets}], "links": {}}
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (200, json.dumps(rows).encode()))
    assert digitalocean.instances("k" * 12)[0]["address"] == ""


# ---- destroy: a delete, never a power-off

def test_a_destroy_is_a_delete_of_that_droplet_and_an_empty_204_is_its_success(fake):
    double = fake({"101": {"label": "x", "gpu": True}})
    digitalocean.destroy("k" * 12, "101")
    assert double.requests[0][:2] == ("DELETE", f"{BASE}/v2/droplets/101") and double.instances == {}


def test_a_droplet_already_gone_is_a_destroy_that_succeeded(fake):
    double = fake({})
    digitalocean.destroy("k" * 12, "101")   # the service answers 404 not_found: there is nothing left to bill
    assert double.requests[0][0] == "DELETE"


@pytest.mark.parametrize("status,body", [
    (404, b"<html>no such page</html>"),                                  # a 404 that is not the service's own word
    (404, b'{"id": "forbidden", "message": "x"}'),
    (403, b'{"id": "not_found", "message": "x"}'),
    (200, b"<html>maintenance</html>"),                                   # a 2xx that says nothing is no proof
    (302, b""), (429, b'{"id": "too_many_requests", "message": "API Rate limit exceeded."}'),
])
def test_any_other_answer_to_a_destroy_is_a_failure(monkeypatch, status, body):
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (status, body))
    with pytest.raises(digitalocean.AdapterError):
        digitalocean.destroy("k" * 12, "101")


def test_nothing_in_the_adapter_powers_a_droplet_off_or_asks_for_an_action():
    """A Droplet is billed while it exists, powered off too: the only way this adapter ends a machine is the delete."""
    source = pathlib.Path(digitalocean.__file__).read_text()
    code = source.split('"""', 2)[2]   # past the module's docstring, which names the rule
    for word in ("/actions", "power_off", "shutdown", "poweroff", "halt"):
        assert word not in code, word
    assert code.count('"DELETE"') == 2 and '"DELETE", f"/v2/droplets/{instance}"' in code


def test_a_destroyed_droplet_the_service_still_lists_ends_stuck_not_gone(fake, tmp_path):
    """The reaper never takes the delete's answer for the fact: the listing decides (rig design, section 5)."""
    double = fake({}, sticky={"101"})
    rig = j.Rig(LocalLogStore(tmp_path / "rig"), clock=lambda: dt.datetime(2026, 11, 18, 20, tzinfo=dt.timezone.utc))
    rented = pv.Rented("digitalocean", key=fake_key(tmp_path, provider="digitalocean"))
    rig.open_session("person", "x", hours=3, budget=9.0)
    machine = rig.add_machine("s1", "digitalocean", lambda mid: f"flotilla:0123456789ab:{mid}")
    double.instances["101"] = {"label": machine.label, "gpu": True, "hourly": 1.99, "ip": "203.0.113.7"}
    rig.move(machine.id, j.PROVISIONING, instance="101", hourly=1.99, created=rig.now().isoformat())
    rig.move(machine.id, j.DRAINING, reason="test")
    for _ in range(5):
        reap(rig, lambda name: rented, services=("digitalocean",), machine_key="0123456789ab")
    assert rig.machines()[machine.id].state == j.STUCK
    assert sum(1 for method, *_ in double.requests if method == "DELETE") >= 3


def test_a_droplet_the_service_lists_powered_off_is_drained_and_deleted(fake, tmp_path):
    clock = {"at": dt.datetime(2026, 11, 18, 20, tzinfo=dt.timezone.utc)}
    double = fake({})
    rig = j.Rig(LocalLogStore(tmp_path / "rig"), clock=lambda: clock["at"])
    rented = pv.Rented("digitalocean", key=fake_key(tmp_path, provider="digitalocean"))
    rig.open_session("person", "x", hours=3, budget=9.0)
    machine = rig.add_machine("s1", "digitalocean", lambda mid: f"flotilla:0123456789ab:{mid}")
    double.instances["101"] = {"label": machine.label, "gpu": True, "hourly": 1.99, "status": "off"}
    rig.move(machine.id, j.PROVISIONING, instance="101", hourly=1.99, created=rig.now().isoformat())
    rig.move(machine.id, j.READY)
    for _ in range(3):
        clock["at"] += dt.timedelta(minutes=5)
        reap(rig, lambda name: rented, services=("digitalocean",), machine_key="0123456789ab")
    assert rig.machines()[machine.id].state == j.GONE and double.instances == {}


# ---- the door

def test_a_redirect_is_never_followed_with_the_key():
    import urllib.request
    handler = digitalocean._NoRedirect()
    request = urllib.request.Request(f"{BASE}/v2/droplets", headers={"Authorization": "Bearer k"})
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/") is None


def test_the_base_url_is_the_public_api_and_ignores_the_environment(fake, monkeypatch):
    for name in ("DIGITALOCEAN_API_URL", "DIGITALOCEAN_URL", "DO_API_URL"):
        monkeypatch.setenv(name, "https://attacker.example")
    double = fake({})
    digitalocean.instances("k" * 12)
    assert all(url.startswith("https://api.digitalocean.com/v2/") for _, url, _ in double.requests)


def test_a_key_echoed_late_in_an_error_is_masked_before_it_is_cut(monkeypatch):
    key = "account-key-0123456789"
    body = json.dumps({"id": "unauthorized", "message": "x" * 180 + key}).encode()
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (401, body))
    with pytest.raises(digitalocean.AdapterError) as err:
        digitalocean.instances(key)
    assert key[:8] not in str(err.value) and err.value.status == 401 and err.value.code == "unauthorized"


def test_every_request_carries_a_timeout(fake, monkeypatch):
    double = fake({"101": {"label": "x"}}, offers=SIZES)
    seen = []
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (seen.append(a[4]), double(*a))[1])
    digitalocean.instances("k" * 12)
    digitalocean.offers("k" * 12, WANT)
    make_one()
    digitalocean.destroy("k" * 12, "101")
    digitalocean.credit("k" * 12)
    assert seen and all(isinstance(limit, int) and 0 < limit <= 30 for limit in seen)


# ---- offers

def test_asking_for_the_mi300x_answers_its_sizes_alone_at_the_services_price(fake):
    double = fake(offers=SIZES)
    rows = digitalocean.offers("k" * 12, WANT)
    assert rows == [
        {"offer": "gpu-mi300x1-192gb@atl1", "gpu": "amd_mi300x", "hourly": 1.99, "reliability": 1.0,
         "datacenter": True},
        {"offer": "gpu-mi300x8-1536gb@atl1", "gpu": "amd_mi300x x8", "hourly": 15.92, "reliability": 1.0,
         "datacenter": True}]
    method, url, _ = double.requests[0]
    assert method == "GET" and url.startswith(f"{BASE}/v2/sizes?") and double.last_query["gpu_minimum"] == "1"


def test_the_persons_hourly_ceiling_decides_between_one_card_and_eight(fake):
    fake(offers=SIZES)
    assert [row["offer"] for row in digitalocean.offers("k" * 12, {**WANT, "max_hourly": 2.5})] == \
        ["gpu-mi300x1-192gb@atl1"]
    assert digitalocean.offers("k" * 12, {**WANT, "max_hourly": 0.60}) == []


def test_the_price_is_whatever_the_service_says_now(fake):
    """No price is written in the adapter: the service's pages have named 2.59 $ and an account has seen 1.99 $."""
    double = fake(offers=[{**row} for row in SIZES])
    next(row for row in double.offers if row["slug"] == "gpu-mi300x1-192gb")["price_hourly"] = 2.59
    assert digitalocean.offers("k" * 12, WANT)[0]["hourly"] == 2.59
    source = pathlib.Path(digitalocean.__file__).read_text()
    assert "1.99" not in source and "2.59" not in source


@pytest.mark.parametrize("name", ["MI300X", "mi300x", "AMD Instinct MI300X", "amd mi300x", "Instinct MI300X"])
def test_a_card_is_found_by_the_names_a_person_writes(fake, name):
    fake(offers=SIZES)
    rows = digitalocean.offers("k" * 12, {**WANT, "gpus": [name]})
    assert [row["offer"] for row in rows] == ["gpu-mi300x1-192gb@atl1", "gpu-mi300x8-1536gb@atl1"]


def test_a_card_the_service_does_not_list_is_no_offer(fake):
    fake(offers=SIZES)
    assert digitalocean.offers("k" * 12, {**WANT, "gpus": ["MI325X"]}) == []
    assert digitalocean.offers("k" * 12, {**WANT, "gpus": ["RTX 4090"]}) == []


def test_a_size_the_service_lists_in_no_region_is_no_offer_and_no_region_is_guessed(fake):
    """A report from the field: the MI300X size can come back with an empty `regions`."""
    sizes = [{**row, "regions": []} if row["slug"].startswith("gpu-mi300x") else row for row in SIZES]
    double = fake(offers=sizes)
    assert digitalocean.offers("k" * 12, WANT) == []
    assert len(double.requests) == 1   # nothing else was asked: no region listing, no create


@pytest.mark.parametrize("change", [{"available": False}, {"gpu_info": None}, {"gpu_info": {"count": 0}},
                                    {"price_hourly": None}, {"price_hourly": "1.99"}, {"price_hourly": 0},
                                    {"price_hourly": True}, {"regions": None}, {"regions": ["ATL 1", 7, "../x"]},
                                    {"slug": "gpu-MI300X/../x"}, {"disk": 10}])
def test_a_size_that_cannot_be_rented_as_asked_is_left_out(fake, change):
    fake(offers=[{**SIZES[6], **change}])
    assert digitalocean.offers("k" * 12, WANT) == []


def test_with_no_card_named_every_gpu_size_within_the_price_is_offered_and_never_a_plain_one(fake):
    fake(offers=SIZES)
    rows = digitalocean.offers("k" * 12, {**WANT, "gpus": [], "max_hourly": 100.0, "limit": 3})
    assert [row["offer"] for row in rows] == ["gpu-a1x1-16gb@tor1", "gpu-b2x1-24gb@atl1", "gpu-b2x1-24gb@nyc2"]
    every = digitalocean.offers("k" * 12, {**WANT, "gpus": [], "max_hourly": 100.0})
    assert not any(row["offer"].startswith("s-") for row in every)
    assert [row["hourly"] for row in every] == sorted(row["hourly"] for row in every)


def test_an_offer_search_without_a_list_of_sizes_is_an_error(monkeypatch):
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (200, b'{"sizes": null}'))
    with pytest.raises(digitalocean.AdapterError):
        digitalocean.offers("k" * 12, WANT)


# ---- create

def test_the_fingerprint_is_the_one_ssh_keygen_gives():
    assert digitalocean.fingerprint(PUBLIC_KEY) == FINGERPRINT
    for text in ("", "ssh-ed25519", "ssh-ed25519 AAAA", "ssh-ed25519 not*base64*at*all== x", None,
                 "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI flotilla rig"):
        assert digitalocean.fingerprint(text) == ""


def test_create_names_the_size_the_region_the_image_the_tag_and_the_key(fake):
    double = fake(offers=SIZES)
    instance = make_one()
    payload = double.instances[instance]["payload"]
    assert [(method, url) for method, url, _ in double.requests] == [
        ("GET", f"{BASE}/v2/account/keys/{FINGERPRINT}"), ("POST", f"{BASE}/v2/droplets")]
    assert (payload["size"], payload["region"], payload["image"]) == ("gpu-mi300x1-192gb", "atl1", "gpu-image-1")
    assert payload["tags"] == ["flotilla:0123456789ab:m1"] and payload["name"] == "flotilla-0123456789ab-m1"
    assert payload["ssh_keys"] == [FINGERPRINT]
    assert (payload["backups"], payload["ipv6"], payload["monitoring"]) == (False, False, False)
    assert set(payload) == {"name", "region", "size", "image", "ssh_keys", "tags", "user_data", "backups", "ipv6",
                            "monitoring"}


def test_the_start_script_travels_as_user_data_with_the_variables_at_its_top(fake):
    double = fake(offers=SIZES)
    payload = double.instances[make_one()]["payload"]
    lines = payload["user_data"].split("\n")
    assert lines[:2] == ["#!/bin/bash", "export FLOTILLA_WATCHDOG_MINUTES=45"]
    assert "flotilla-heartbeat" in payload["user_data"]


def test_a_variables_value_cannot_become_shell(tmp_path):
    value = "it's $(touch pwned) `id` \"x\"\n; rm -rf /"
    script = tmp_path / "s.sh"
    script.write_text(digitalocean._user_data({"A": value}, 'printf %s "$A"\n'))
    done = subprocess.run([shutil.which("sh"), str(script)], capture_output=True, text=True, timeout=20, cwd=tmp_path)
    assert done.stdout == value and not (tmp_path / "pwned").exists()


@pytest.mark.parametrize("over,said", [
    ({"public_key": ""}, "public key"), ({"public_key": "ssh-ed25519 AAAA"}, "public key"),
    ({"image": "mcr.microsoft.com/playwright:v1.64.0-noble"}, "not a container image"),
    ({"image": "ghcr.io/me/x@sha256:0f0f"}, "image"), ({"image": ""}, "image"),
    ({"label": "flotilla 0123:m1"}, "label"), ({"label": ""}, "label"),
    ({"env": {"A B": "1"}}, "variable"), ({"env": {"A": 1}}, "variable"),
    ({"onstart": "x" * (64 * 1024)}, "64 KiB"),
])
def test_what_the_service_could_not_take_is_refused_before_any_request(fake, over, said):
    double = fake(offers=SIZES)
    with pytest.raises(digitalocean.AdapterError, match=said):
        make_one(**over)
    assert double.requests == []


def test_a_key_the_account_does_not_hold_is_refused_before_anything_is_made(fake):
    double = fake(offers=SIZES, keys=set())
    with pytest.raises(digitalocean.AdapterError, match="not among the account's SSH keys.*nothing was made"):
        make_one()
    assert [method for method, *_ in double.requests] == ["GET"] and double.created == 0


def test_a_key_lookup_that_fails_any_other_way_makes_nothing_either(fake, monkeypatch):
    double = fake(offers=SIZES)
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (500, b'{"id": "server_error", "message": "x"}'))
    with pytest.raises(digitalocean.AdapterError) as err:
        make_one()
    assert err.value.status == 500 and double.created == 0


def test_an_image_named_by_its_number_is_sent_as_a_number(fake):
    double = fake(offers=SIZES)
    assert double.instances[make_one(image="194589274")]["payload"]["image"] == 194589274


@pytest.mark.parametrize("body", [b'{"droplet": {}}', b'{"droplet": {"id": "901"}}', b'{"droplets": [{"id": 901}]}',
                                  b'{"droplet": {"id": true}}', b"[]"])
def test_an_answer_that_names_no_droplet_is_an_error(fake, monkeypatch, body):
    double = fake()
    monkeypatch.setattr(digitalocean, "SEND",
                        lambda method, *rest: (202, body) if method == "POST" else double(method, *rest))
    with pytest.raises(digitalocean.AdapterError, match="did not say which Droplet"):
        make_one()


def test_no_refusal_of_a_create_is_read_as_the_offer_being_gone(fake):
    """The service's words for a size it can no longer place are not in its specification, so none is guessed: any
    refused create is a failure, and no second offer is tried on a reading that could be wrong."""
    fake(offers=SIZES, create_status=422)
    assert not hasattr(digitalocean, "OfferGone")
    with pytest.raises(digitalocean.AdapterError) as err:
        make_one()
    assert err.value.status == 422


def test_the_ssh_key_is_not_attached_afterwards_because_the_service_cannot(fake):
    double = fake({"901": {"label": "x"}})
    assert digitalocean.KEY_AT_CREATE is True
    digitalocean.attach_ssh("k" * 12, "901", PUBLIC_KEY)
    assert double.requests == []
    with pytest.raises(digitalocean.AdapterError):
        digitalocean.attach_ssh("k" * 12, "901/../x", PUBLIC_KEY)


# ---- the credit

def test_the_credit_is_what_the_account_owes_negated(fake):
    double = fake(credit=-23.44)   # the account owes 23.44 $
    assert digitalocean.credit("k" * 12) == -23.44
    assert double.requests[0][:2] == ("GET", f"{BASE}/v2/customers/my/balance")


def test_nothing_owed_is_a_credit_of_zero(monkeypatch):
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (200, b'{"month_to_date_balance": "0.00"}'))
    assert repr(digitalocean.credit("k" * 12)) == "0.0"


@pytest.mark.parametrize("owed", [23.44, "1e3", "23.44 USD", " 23.44", "nan", "inf", "0x10", "１２"])
def test_a_balance_that_is_not_money_as_text_is_an_error(monkeypatch, owed):
    monkeypatch.setattr(digitalocean, "SEND", lambda *a: (200, json.dumps({"month_to_date_balance": owed}).encode()))
    with pytest.raises(digitalocean.AdapterError):
        digitalocean.credit("k" * 12)


# ---- the start script

def test_the_start_script_keeps_a_heartbeat_and_gives_nothing_back():
    script = digitalocean.onstart()
    assert "touch /root/flotilla-heartbeat" in script and "FLOTILLA_WATCHDOG_MINUTES" in script
    assert "|| echo 0" in script                    # a missing heartbeat file reads as the epoch: old, not new
    for word in ("curl", "shutdown", "poweroff", "halt", "DELETE", "Authorization", "api.digitalocean.com"):
        assert word not in script, word


def test_the_start_script_is_a_shell_script():
    done = subprocess.run([shutil.which("sh"), "-n"], input=digitalocean._user_data({"A": "1"}, digitalocean.onstart()),
                          capture_output=True, text=True, timeout=20)
    assert done.returncode == 0, done.stderr


def test_the_watchdog_says_once_that_the_machine_cannot_give_itself_back(tmp_path):
    """The real loop under sh, heartbeat missing, three ticks: one line, not one a minute."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name in ("date", "stat"):
        (bin_ / name).write_text(f'#!/bin/sh\nexec {shutil.which(name)} "$@"\n')
    (bin_ / "sleep").write_text('#!/bin/sh\necho x >> "$T/ticks"\n[ "$(wc -l < "$T/ticks")" -le 3 ]\n')
    (bin_ / "wc").write_text(f'#!/bin/sh\nexec {shutil.which("wc")} "$@"\n')
    for path in bin_.iterdir():
        path.chmod(0o755)
    text = digitalocean.onstart().split("<<'WATCHDOG'\n", 1)[1].split("\nWATCHDOG\n", 1)[0]
    script = tmp_path / "w.sh"
    script.write_text(text.replace("/root/flotilla-heartbeat", str(tmp_path / "hb")))
    done = subprocess.run([shutil.which("sh"), str(script)], capture_output=True, text=True, timeout=20,
                          env={"PATH": str(bin_), "T": str(tmp_path), "FLOTILLA_WATCHDOG_MINUTES": "5"})
    assert done.stdout.count("cannot give itself back") == 1 and "billed, powered off too" in done.stdout


# ---- through flotilla: the key file, the wrapper, `rig up`

T0 = dt.datetime(2026, 11, 18, 20, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def world(tmp_path, monkeypatch, request):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    request.addfinalizer(time.tzset)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    fake_key(tmp_path, provider="digitalocean")
    clock = {"at": T0}
    double = FakeDigitalOcean(offers=SIZES, boot_polls=2)
    monkeypatch.setattr(digitalocean, "SEND", double)

    class Table:
        text = None

        def __call__(self, argv, *, input=None, **kwargs):
            if argv == ["crontab", "-l"]:
                return subprocess.CompletedProcess(argv, 0 if self.text is not None else 1, self.text or "", "")
            self.text = input
            return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(commands, "CRON_RUN", Table())
    monkeypatch.setattr(commands, "CLOCK", lambda: clock["at"])
    monkeypatch.setattr(commands, "ALIVE", lambda pid, mark: True)
    monkeypatch.setattr(commands, "SSH_KEY", lambda: PUBLIC_KEY)
    monkeypatch.setattr(commands, "SLEEP", lambda seconds: clock.update(at=clock["at"] + dt.timedelta(seconds=seconds)))
    monkeypatch.setattr(cron, "interpreter", lambda state, **kw: "/usr/bin/python3")
    state = paths.state_dir()
    state.mkdir(parents=True, exist_ok=True)

    def machine(**keys):
        (state / "machine.toml").write_text(render_toml({"rig": "on", "rig_provider": "digitalocean",
                                                         "rig_images": ["gpu-image-1"], **keys}), encoding="utf-8")
    machine(rig_max_hourly=2.5)
    project = tmp_path / "project"
    (project / ".flotilla").mkdir(parents=True)
    (project / ".flotilla" / "project.toml").write_text(
        render_toml({"schema": 1, "rig": {"image": "gpu-image-1", "gpu": ["MI300X"]}}))
    return {"state": state, "fake": double, "clock": clock, "root": str(project), "machine": machine}


def up(world, *extra):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["rig", "up", "--root", world["root"], "--as", "main session 3", *extra])
    return code, out.getvalue()


def journal(world):
    return j.Rig(LocalLogStore(world["state"] / "rig"), clock=lambda: world["clock"]["at"])


def test_up_raises_one_mi300x_within_the_ceiling_and_waits_for_it(world):
    journal(world).open_session("the person", "a port to time", hours=3, budget=9.0)
    code, out = up(world, "--why", "timing")
    machine = journal(world).machines()["m1"]
    assert code == 0 and machine.state == j.READY and machine.address == "203.0.113.1:22"
    assert (machine.gpu, machine.hourly) == ("amd_mi300x", 1.99)
    payload = world["fake"].instances[machine.instance]["payload"]
    assert (payload["size"], payload["region"], payload["tags"]) == ("gpu-mi300x1-192gb", "atl1", [machine.label])
    assert payload["ssh_keys"] == [FINGERPRINT] and "export FLOTILLA_WATCHDOG_MINUTES=45" in payload["user_data"]
    assert world["fake"].created == 1


def test_up_rents_nothing_while_the_hourly_ceiling_is_under_the_cards_price(world):
    world["machine"]()   # the default ceiling, 0.60 $/h
    journal(world).open_session("the person", "a port to time", hours=3, budget=9.0)
    code, out = up(world, "--why", "timing")
    assert code == 2 and "no offer within the ceilings (0.60 $/h" in out
    assert world["fake"].created == 0 and journal(world).machines()["m1"].state == j.GONE


def test_up_with_the_card_in_no_region_rents_nothing(world):
    world["fake"].offers = [{**row, "regions": []} for row in SIZES]
    journal(world).open_session("the person", "a port to time", hours=3, budget=9.0)
    code, out = up(world, "--why", "timing")
    assert code == 2 and "no offer within the ceilings" in out and world["fake"].created == 0


def test_up_with_the_default_container_image_is_refused_by_name_and_makes_nothing(world):
    world["machine"](rig_max_hourly=2.5, rig_images=["mcr.microsoft.com/playwright:v1.64.0-noble"])
    pathlib.Path(world["root"], ".flotilla", "project.toml").write_text(
        render_toml({"schema": 1, "rig": {"gpu": ["MI300X"]}}))
    journal(world).open_session("the person", "a port to time", hours=3, budget=9.0)
    code, out = up(world, "--why", "timing")
    assert code == 2 and "not a container image" in out and world["fake"].created == 0
    assert not any(method == "POST" for method, *_ in world["fake"].requests)


def test_a_refused_create_tries_no_second_offer(world):
    world["machine"](rig_max_hourly=20.0)
    world["fake"].create_status = 422
    journal(world).open_session("the person", "a port to time", hours=3, budget=90.0)
    code, out = up(world, "--why", "timing")
    assert code == 2 and "422" in out and journal(world).machines()["m1"].state == j.FAILED
    assert sum(1 for method, *_ in world["fake"].requests if method == "POST") == 1
