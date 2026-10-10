"""The contract every rental-service adapter keeps (rig design, section 2), run over each through its own double.
A new adapter is one file in flotilla/rig/providers/, one entry in KNOWN and DOUBLES, and this suite green."""

import ast
import sys

import pytest

from flotilla.rig import providers
from rigkit import DOUBLES

ADAPTERS = providers.KNOWN


@pytest.fixture(params=ADAPTERS)
def adapter(request, monkeypatch):
    module = providers.load(request.param)

    def make(**knobs):
        double = DOUBLES[request.param](**knobs)
        monkeypatch.setattr(module, "SEND", double)
        return module, double
    return make


def test_every_known_adapter_has_a_double():
    assert set(ADAPTERS) == set(DOUBLES)


@pytest.mark.parametrize("path", providers.files(), ids=lambda p: p.stem)
def test_an_adapter_is_one_standalone_stdlib_file(path):
    tree = ast.parse(path.read_text())
    names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert "flotilla" not in names
    assert names <= set(sys.stdlib_module_names) | {"__future__"}, names - set(sys.stdlib_module_names)


def test_rows_carry_exactly_the_five_fields_and_nothing_of_a_credential(adapter):
    module, double = adapter(instances={"101": {"label": "flotilla:0123456789ab:m1"}}, leak="instance-key-abcdef0123")
    rows = module.instances("account-key-0123456789")
    assert rows and all(set(row) == {"instance", "label", "status", "hourly", "address"} for row in rows)
    assert "instance-key-abcdef0123" not in repr(rows)


def test_every_page_is_read(adapter):
    module, double = adapter(instances={str(n): {"label": f"l{n}"} for n in range(1, 30)}, page=7)
    assert sorted(int(row["instance"]) for row in module.instances("k" * 12)) == list(range(1, 30))


@pytest.mark.parametrize("status", [401, 403, 404, 500, 503])
def test_a_refused_listing_is_an_error_carrying_its_status(adapter, status):
    module, _ = adapter(listing_status=status)
    with pytest.raises(module.AdapterError) as err:
        module.instances("k" * 12)
    assert err.value.status == status


def test_an_unparsable_listing_is_an_error_not_an_empty_list(adapter):
    module, _ = adapter(garbage=True)
    with pytest.raises(module.AdapterError):
        module.instances("k" * 12)


@pytest.mark.parametrize("failure", [TimeoutError("timed out"), ConnectionResetError(),
                                     __import__("http.client").client.IncompleteRead(b"")])
def test_a_transport_failure_of_any_kind_is_an_error(adapter, monkeypatch, failure):
    module, _ = adapter()

    def down(*args):
        raise failure
    monkeypatch.setattr(module, "SEND", down)
    with pytest.raises(module.AdapterError):
        module.instances("k" * 12)


def test_a_destroy_removes_the_instance(adapter):
    module, double = adapter(instances={"101": {"label": "x"}})
    module.destroy("k" * 12, "101")
    assert double.instances == {}


@pytest.mark.parametrize("status", [401, 404, 500])
def test_a_refused_destroy_is_an_error_not_a_success(adapter, status):
    module, _ = adapter(instances={"101": {"label": "x"}}, destroy_status=status)
    with pytest.raises(module.AdapterError):
        module.destroy("k" * 12, "101")


@pytest.mark.parametrize("bad", ["101/../../users", "", "../101", "101?x=1", "\uff11\uff10\uff11"])
def test_an_id_the_service_could_not_have_issued_is_never_sent(adapter, bad):
    module, double = adapter()
    with pytest.raises(module.AdapterError):
        module.destroy("k" * 12, bad)
    assert double.requests == []


def test_the_key_never_reaches_an_error_text(adapter):
    module, _ = adapter(listing_status=401, leak="account-key-0123456789")
    with pytest.raises(module.AdapterError) as err:
        module.instances("account-key-0123456789")
    assert "account-key-0123456789" not in str(err.value)


WANT = {"gpus": [], "max_hourly": 0.60, "min_reliability": 0.98, "disk_gb": 30, "limit": 64}


def test_offers_are_datacenter_within_the_price_and_carry_exactly_five_fields(adapter):
    from rigkit import OFFERS
    module, _ = adapter(offers=OFFERS)
    rows = module.offers("k" * 12, WANT)
    assert [row["offer"] for row in rows] == ["53776176", "44053836"]
    assert all(set(row) == {"offer", "gpu", "hourly", "reliability", "datacenter"} for row in rows)
    assert "should-never-leave" not in repr(rows)


def test_a_create_answers_the_new_instance_and_nothing_of_its_key(adapter):
    module, double = adapter(leak="instance-key-abcdef0123")
    instance = module.create("k" * 12, "53776176", image="img:1", disk_gb=30, env={"A": "1"}, onstart="echo hi",
                             label="flotilla:0123456789ab:m1")
    assert instance.isdigit() and double.instances[instance]["label"] == "flotilla:0123456789ab:m1"
    assert "instance-key-abcdef0123" not in repr(instance)


@pytest.mark.parametrize("status", [401, 404, 500])
def test_a_refused_create_is_an_error(adapter, status):
    module, _ = adapter(create_status=status)
    with pytest.raises(module.AdapterError):
        module.create("k" * 12, "53776176", image="img:1", disk_gb=30, env={}, onstart="", label="l")


@pytest.mark.parametrize("bad", ["53776176/../x", "", "５３"])
def test_an_offer_id_the_service_could_not_have_issued_is_never_sent(adapter, bad):
    module, double = adapter()
    with pytest.raises(module.AdapterError):
        module.create("k" * 12, bad, image="img:1", disk_gb=30, env={}, onstart="", label="l")
    assert double.requests == []


def test_rows_carry_an_address_once_the_instance_runs(adapter):
    module, _ = adapter(instances={"7": {"label": "x", "ssh_host": "ssh4.vast.ai", "ssh_port": 30123}})
    assert module.instances("k" * 12)[0]["address"] == "ssh4.vast.ai:30123"


def test_the_start_script_carries_the_watchdog(adapter):
    module, _ = adapter()
    script = module.onstart()
    assert "FLOTILLA_WATCHDOG_MINUTES" in script and "flotilla-heartbeat" in script


def test_the_account_credit_is_one_number_and_nothing_of_the_account(adapter):
    """The service's answer carries the account's key, session and address; one number leaves the adapter (0.13.0)."""
    module, double = adapter(credit=41.2)
    found = module.credit("account-key-0123456789")
    assert type(found) is float and found == 41.2
    assert double.requests[0][:2] == ("GET", "https://console.vast.ai/api/v0/users/current/")


@pytest.mark.parametrize("bad", [None, "41.2", True, [41.2], {"credit": 1}])
def test_a_credit_that_is_not_a_number_is_an_error(adapter, bad):
    module, _ = adapter(credit=bad)
    with pytest.raises(module.AdapterError):
        module.credit("k" * 12)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_a_refused_credit_is_an_error_carrying_its_status(adapter, status):
    module, _ = adapter(user_status=status)
    with pytest.raises(module.AdapterError) as err:
        module.credit("k" * 12)
    assert err.value.status == status


def test_a_credit_too_large_for_a_float_is_an_adapter_error(adapter):
    """Not an OverflowError, which no caller expects (security review of 0.13.0)."""
    module, _ = adapter(credit=10 ** 400)
    with pytest.raises(module.AdapterError):
        module.credit("k" * 12)


def test_the_credit_is_asked_with_a_shorter_timeout_than_the_listing(adapter, monkeypatch):
    """Under the reaper's lock a slow account must not add a whole listing's wait (review of 0.13.0, I-3)."""
    module, double = adapter()
    seen = []
    monkeypatch.setattr(module, "SEND", lambda *a: (seen.append(a[4]), double(*a))[1])
    module.credit("k" * 12)
    module.instances("k" * 12)
    assert seen[0] < seen[1]
