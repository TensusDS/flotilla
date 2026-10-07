from flotilla.rig import profile
from flotilla.rig.settings import DEFAULT_IMAGE


def test_defaults_when_the_profile_says_nothing():
    got = profile.read({})
    assert (got.image, got.gpus, got.disk_gb, got.problems) == (DEFAULT_IMAGE, (), 30, ())


def test_a_named_image_gpus_and_disk():
    got = profile.read({"rig": {"image": "ghcr.io/me/shoot:1.2", "gpu": ["RTX 2080 Ti", "RTX 4090"], "disk_gb": 60}})
    assert (got.image, got.gpus, got.disk_gb) == ("ghcr.io/me/shoot:1.2", ("RTX 2080 Ti", "RTX 4090"), 60)


def test_malformed_values_fall_back_and_say_why():
    got = profile.read({"rig": {"image": "x; rm -rf /", "gpu": ["ok", "bad;name"], "disk_gb": 9000}})
    assert got.image == DEFAULT_IMAGE and got.gpus == ("ok",) and got.disk_gb == 30 and len(got.problems) == 3
