import pytest

from flotilla.rig import provider as pv
from flotilla.rig.providers import vast
from rigkit import FakeVast, fake_key


@pytest.fixture
def rented(tmp_path, monkeypatch):
    def make(fake, mode=0o600):
        monkeypatch.setattr(vast, "SEND", fake)
        return pv.Rented("vast", key=fake_key(tmp_path, mode))
    return make


def test_rows_become_listed_entries(rented):
    fake = FakeVast({"101": {"label": "flotilla:0123456789ab:m1", "dph_total": 0.41}})
    assert rented(fake).instances() == [pv.Listed("101", "flotilla:0123456789ab:m1", "running", 0.41)]


def test_a_key_others_can_read_is_refused_before_any_request(rented):
    fake = FakeVast({})
    with pytest.raises(pv.ProviderError, match="chmod 600"):
        rented(fake, mode=0o644).instances()
    assert fake.requests == []


def test_an_adapter_error_becomes_a_provider_error_with_the_key_masked(rented):
    fake = FakeVast({}, listing_status=401)
    with pytest.raises(pv.ProviderError, match="401"):
        rented(fake).instances()


def test_the_wrapper_masks_its_key_even_if_an_adapter_let_it_through(rented, monkeypatch):
    def leaky(key):
        raise vast.AdapterError(f"bad key {key}", 401)
    monkeypatch.setattr(vast, "instances", leaky)
    with pytest.raises(pv.ProviderError) as err:
        rented(FakeVast({})).instances()
    assert "account-key-0123456789" not in str(err.value)


def test_the_wrapper_scrubs_rows_even_if_an_adapter_let_a_credential_through(rented, monkeypatch):
    monkeypatch.setattr(vast, "instances", lambda key: [
        {"instance": "1", "label": "key instance-key-abcdef0123", "status": "running", "hourly": 0.3,
         "api_key": "instance-key-abcdef0123"}])
    assert "instance-key-abcdef0123" not in repr(rented(FakeVast({})).instances())


def test_a_destroy_goes_through_the_adapter(rented):
    fake = FakeVast({"101": {"label": "x"}})
    rented(fake).destroy("101")
    assert fake.instances == {}


def test_the_key_file_is_named_by_the_service(tmp_path):
    assert pv.provider_for("vast", env={"XDG_CONFIG_HOME": str(tmp_path)}).key == \
        tmp_path / "flotilla" / "rig" / "vast.key"


def test_an_unknown_provider_is_refused_naming_the_known_ones():
    with pytest.raises(pv.ProviderError, match="no provider `hetzner`.*vast"):
        pv.provider_for("hetzner")


def test_offers_and_create_go_through_the_wrapper(rented):
    from rigkit import OFFERS
    fake = FakeVast(offers=OFFERS)
    provider = rented(fake)
    found = provider.offers({"gpus": [], "max_hourly": 0.6, "min_reliability": 0.98, "disk_gb": 30, "limit": 64})
    assert found[0] == pv.Offer("53776176", "RTX 2080 Ti", 0.137, 0.999, True)
    instance = provider.create(found[0].offer, image="img:1", disk_gb=30, env={}, onstart="", label="l")
    provider.attach_ssh(instance, "ssh-ed25519 AAAA")
    assert fake.ssh_keys[instance] == ["ssh-ed25519 AAAA"]


def test_every_known_services_key_file_is_named_by_it(tmp_path):
    from flotilla.rig import providers
    for name in providers.KNOWN:
        assert pv.provider_for(name, env={"XDG_CONFIG_HOME": str(tmp_path)}).key == \
            tmp_path / "flotilla" / "rig" / f"{name}.key"
    with pytest.raises(pv.ProviderError, match="no provider `hetzner`.*vast, digitalocean"):
        pv.provider_for("hetzner")


@pytest.mark.parametrize("offer,kept", [("53776176", True), ("gpu-mi300x1-192gb@atl1", True), ("a" * 80, True),
                                        ("a" * 81, False), ("", False), ("size@atl1/../x", False),
                                        ("size atl1", False), ("-rf", False), ("size@atl1;id", False),
                                        ("５３", False), ("size@atl1\n", False)])
def test_an_offer_is_kept_only_under_a_name_safe_to_print_and_to_send(rented, monkeypatch, offer, kept):
    """Digits on one service, a size in a region on another; never text that could be read as anything else."""
    monkeypatch.setattr(vast, "offers", lambda key, want: [
        {"offer": offer, "gpu": "x", "hourly": 0.1, "reliability": 0.99, "datacenter": True}])
    assert [item.offer for item in rented(FakeVast()).offers({})] == ([offer] if kept else [])


def test_the_public_key_goes_to_create_only_where_the_service_takes_keys_while_it_makes_the_machine(
        tmp_path, monkeypatch):
    from flotilla.rig.providers import digitalocean
    from rigkit import PUBLIC_KEY, SIZES, FakeDigitalOcean
    early, late = FakeDigitalOcean(offers=SIZES), FakeVast()
    monkeypatch.setattr(digitalocean, "SEND", early)
    monkeypatch.setattr(vast, "SEND", late)
    words = {"disk_gb": 30, "env": {}, "onstart": "", "label": "l", "public_key": PUBLIC_KEY}
    there = pv.Rented("digitalocean", key=fake_key(tmp_path, provider="digitalocean"))
    instance = there.create("gpu-mi300x1-192gb@atl1", image="gpu-image-1", **words)
    assert early.instances[instance]["payload"]["ssh_keys"] == [digitalocean.fingerprint(PUBLIC_KEY)]
    there.attach_ssh(instance, PUBLIC_KEY)                       # asks nothing: the key is already on the machine
    assert [method for method, *_ in early.requests] == ["GET", "POST"]
    here = pv.Rented("vast", key=fake_key(tmp_path))
    instance = here.create("53776176", image="img:1", **words)   # vast's create takes no key: it is attached after
    assert "public_key" not in late.instances[instance]["payload"] and late.ssh_keys == {}
    with pytest.raises(pv.ProviderError, match="public key"):    # and without one, nothing is asked of the service
        there.create("gpu-mi300x1-192gb@atl1", image="gpu-image-1", **{**words, "public_key": ""})


def test_a_services_notes_are_its_adapters_words_and_none_where_it_has_none(tmp_path):
    assert pv.Rented("vast", key=fake_key(tmp_path)).notes() == ()
    notes = pv.Rented("digitalocean", key=fake_key(tmp_path, provider="digitalocean")).notes()
    assert any("powered off is billed until it is destroyed" in text for text in notes)


def test_the_credit_is_read_through_the_wrapper(rented):
    assert rented(FakeVast(credit=41.2)).credit() == 41.2


@pytest.mark.parametrize("status", [401, 403])
def test_a_key_that_may_not_read_the_account_gives_no_credit(rented, status):
    assert rented(FakeVast(user_status=status)).credit() is None


def test_a_credit_the_service_could_not_answer_is_a_provider_error(rented):
    with pytest.raises(pv.ProviderError, match="500"):
        rented(FakeVast(user_status=500)).credit()


def test_the_wrapper_lets_only_a_number_out_even_if_an_adapter_passed_the_account(rented, monkeypatch):
    monkeypatch.setattr(vast, "credit", lambda key: {"credit": 41.2, "api_key": "account-api-key-0f0f0f0f"})
    with pytest.raises(pv.ProviderError) as err:
        rented(FakeVast()).credit()
    assert "account-api-key" not in str(err.value)
