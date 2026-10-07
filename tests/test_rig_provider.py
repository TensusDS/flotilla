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
