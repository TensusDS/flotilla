"""The disk's state, gc design section 8: low is free space under the smaller of a share of the volume and a size,
no room is under a floor, and a disk that cannot be asked refuses nothing."""

from collections import namedtuple

import pytest

from flotilla.gc import disk

GB = 2**30
Usage = namedtuple("Usage", "total used free")


def usage(total_gb, free_gb):
    return lambda path: Usage(int(total_gb * GB), int((total_gb - free_gb) * GB), int(free_gb * GB))


def test_low_is_the_smaller_of_the_share_and_the_size():
    assert not disk.state("/", {}, usage=usage(301, 25)).low
    assert disk.state("/", {}, usage=usage(301, 19)).low
    assert disk.state("/", {}, usage=usage(100, 9)).low
    assert not disk.state("/", {}, usage=usage(100, 11)).low


def test_no_room_is_under_two_gigabytes():
    full = disk.state("/", {}, usage=usage(100, 1.9))
    assert full.no_room and full.low
    assert not disk.state("/", {}, usage=usage(100, 2.1)).no_room
    assert "disk_no_room_mb" in disk.refusal(full) and "2048 MB" in disk.refusal(full)
    assert disk.refusal(disk.state("/", {}, usage=usage(100, 2.1))) == ""


def test_the_profile_moves_the_thresholds():
    assert disk.state("/", {"gc": {"disk_no_room_mb": 4096}}, usage=usage(100, 3)).no_room
    assert disk.state("/", {"gc": {"disk_low_percent": 50}}, usage=usage(1000, 30)).low is False
    assert disk.state("/", {"gc": {"disk_low_mb": 40960}}, usage=usage(1000, 30)).low


@pytest.mark.parametrize("key, value", [("disk_low_mb", "lots"), ("disk_low_percent", 150),
                                        ("disk_no_room_mb", -1)])
def test_a_bad_threshold_falls_back_with_a_note(key, value):
    found = disk.state("/", {"gc": {key: value}}, usage=usage(100, 9))
    assert found.low and any(f"gc.{key}" in note for note in found.notes), found.notes


def test_a_disk_that_cannot_be_asked_refuses_nothing():
    def broken(path):
        raise OSError(2, "No such file or directory")
    found = disk.state("/gone", {}, usage=broken)
    assert found.free_mb is None and not found.low and not found.no_room
    assert disk.refusal(found) == "" and "could not be asked" in found.text
