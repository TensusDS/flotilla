from flotilla.rig.providers import vast
from rigkit import FakeVast


def test_the_listing_is_vasts_v1_instances_with_a_bearer_key(monkeypatch):
    fake = FakeVast({"101": {"label": "flotilla:0123456789ab:m1", "dph_total": 0.41}, "102": {"label": None}})
    monkeypatch.setattr(vast, "SEND", fake)
    rows = vast.instances("account-key-0123456789")
    assert rows == [{"instance": "101", "label": "flotilla:0123456789ab:m1", "status": "running", "hourly": 0.41},
                    {"instance": "102", "label": "", "status": "running", "hourly": 0.30}]
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
