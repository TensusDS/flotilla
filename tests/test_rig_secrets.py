from flotilla.rig.secrets import MASK, scrub, scrub_text, secret_name

CREATE = {"success": True, "new_contract": 54532850, "instance_api_key": "a1b2c3d4e5f6a7b8c9d0",
          "extra": {"Auth_Token": "tok-0123456789", "note": "use key a1b2c3d4e5f6a7b8c9d0 to self-destroy"},
          "list": [{"ssh_key": "ssh-ed25519 AAAAC3Nza", "label": "flotilla:0123456789ab:m1"}]}


def test_secret_named_fields_are_dropped_at_every_depth():
    clean, values = scrub(CREATE)
    assert "instance_api_key" not in clean and "Auth_Token" not in clean["extra"]
    assert "ssh_key" not in clean["list"][0] and clean["list"][0]["label"] == "flotilla:0123456789ab:m1"
    assert clean["new_contract"] == 54532850
    assert "a1b2c3d4e5f6a7b8c9d0" in values


def test_a_dropped_value_is_masked_where_it_appears_elsewhere():
    clean, _ = scrub(CREATE)
    assert clean["extra"]["note"] == f"use key {MASK} to self-destroy"


def test_text_is_masked_by_the_values_scrub_found():
    _, values = scrub(CREATE)
    assert scrub_text("error: bad key a1b2c3d4e5f6a7b8c9d0", values) == f"error: bad key {MASK}"


def test_short_values_are_not_used_as_masks():
    clean, values = scrub({"password": "1", "id": 1, "note": "1 machine"})
    assert values == [] and clean == {"id": 1, "note": "1 machine"}


def test_names_that_are_secret():
    for name in ("api_key", "instance_api_key", "TOKEN", "client_secret", "Password", "ssh_key", "apikey"):
        assert secret_name(name)
    for name in ("label", "gpu_name", "dph_total", "ssh_host", "ssh_port", "id", "actual_status"):
        assert not secret_name(name)
