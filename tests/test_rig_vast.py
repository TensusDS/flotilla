from flotilla.rig.providers import vast
from rigkit import FakeVast


def test_the_listing_is_vasts_v1_instances_with_a_bearer_key(monkeypatch):
    fake = FakeVast({"101": {"label": "flotilla:0123456789ab:m1", "dph_total": 0.41}, "102": {"label": None}})
    monkeypatch.setattr(vast, "SEND", fake)
    rows = vast.instances("account-key-0123456789")
    assert rows == [{"instance": "101", "label": "flotilla:0123456789ab:m1", "status": "running", "hourly": 0.41,
                     "address": ""},
                    {"instance": "102", "label": "", "status": "running", "hourly": 0.30, "address": ""}]
    method, url, headers = fake.requests[0]
    assert method == "GET" and url.startswith("https://console.vast.ai/api/v1/instances/?")
    assert headers["Authorization"] == "Bearer account-key-0123456789" and "account-key" not in url


def test_a_destroy_is_a_delete_of_that_instance(monkeypatch):
    fake = FakeVast({"101": {"label": "x"}})
    monkeypatch.setattr(vast, "SEND", fake)
    vast.destroy("k" * 12, "101")
    assert fake.requests[0][:2] == ("DELETE", "https://console.vast.ai/api/v0/instances/101/")


def test_a_redirect_is_never_followed_with_the_key():
    import urllib.request
    handler = vast._NoRedirect()
    request = urllib.request.Request("https://console.vast.ai/api/v1/instances/",
                                     headers={"Authorization": "Bearer k"})
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/") is None


def test_the_base_url_ignores_the_environment(monkeypatch):
    monkeypatch.setenv("VAST_URL", "https://attacker.example")
    fake = FakeVast({})
    monkeypatch.setattr(vast, "SEND", fake)
    vast.instances("k" * 12)
    assert fake.requests[0][1].startswith("https://console.vast.ai/")


def test_a_key_echoed_late_in_an_error_is_masked_before_it_is_cut(monkeypatch):
    """Cut first and a key straddling the cut leaves its first characters behind (final review of 0.8.0)."""
    import json
    key = "account-key-0123456789"
    monkeypatch.setattr(vast, "SEND", lambda *a: (401, json.dumps({"msg": "x" * 190 + key}).encode()))
    try:
        vast.instances(key)
    except vast.AdapterError as err:
        assert key[:8] not in str(err)
    else:
        raise AssertionError("a 401 must raise")


def test_offers_ask_vast_for_datacenter_hosts_cheapest_first(monkeypatch):
    from rigkit import OFFERS
    fake = FakeVast(offers=OFFERS)
    monkeypatch.setattr(vast, "SEND", fake)
    vast.offers("k" * 12, {"gpus": ["RTX 2080 Ti"], "max_hourly": 0.6, "min_reliability": 0.98, "disk_gb": 30,
                           "limit": 64})
    assert fake.requests[0][:2] == ("POST", "https://console.vast.ai/api/v0/bundles/")
    query = fake.last_query
    assert query["datacenter"] == {"eq": True} and query["reliability2"] == {"gte": 0.98}
    assert query["gpu_name"] == {"in": ["RTX 2080 Ti"]} and query["order"] == [["dph_total", "asc"]]


def test_create_asks_for_ssh_with_the_label_the_image_and_the_watchdog(monkeypatch):
    fake = FakeVast()
    monkeypatch.setattr(vast, "SEND", fake)
    instance = vast.create("k" * 12, "53776176", image="mcr.microsoft.com/playwright:v1.48.0-jammy", disk_gb=30,
                           env={"FLOTILLA_WATCHDOG_MINUTES": "45"}, onstart=vast.onstart(),
                           label="flotilla:0123456789ab:m1")
    payload = fake.instances[instance]["payload"]
    assert fake.requests[0][:2] == ("PUT", "https://console.vast.ai/api/v0/asks/53776176/")
    assert payload["runtype"] == "ssh_proxy" and payload["label"] == "flotilla:0123456789ab:m1"
    assert payload["env"] == {"FLOTILLA_WATCHDOG_MINUTES": "45"} and "flotilla-heartbeat" in payload["onstart"]


def test_the_ssh_key_is_attached_to_that_instance(monkeypatch):
    fake = FakeVast({"901": {"label": "x"}})
    monkeypatch.setattr(vast, "SEND", fake)
    vast.attach_ssh("k" * 12, "901", "ssh-ed25519 AAAAC3 flotilla rig")
    assert fake.ssh_keys == {"901": ["ssh-ed25519 AAAAC3 flotilla rig"]}


def test_the_watchdog_destroys_then_stops_with_the_instances_own_key_and_a_lost_heartbeat_is_old():
    script = vast.onstart()
    assert "call DELETE" in script and '"state":"stopped"' in script
    assert "$CONTAINER_API_KEY" in script and "$CONTAINER_ID" in script and "10_nvidia.json" in script
    assert "|| echo 0" in script                    # a missing heartbeat file reads as the epoch: old, not new
    assert "--max-time 30" in script and "AbortSignal.timeout(30000)" in script   # a hung call never stops the loop
