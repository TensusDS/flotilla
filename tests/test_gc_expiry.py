"""The archive's lifetime, gc design section 7: media leaves the archive a month after it came, only on the person's
recorded yes, asked three days ahead; three ignored days keep it another month; a low disk asks at once. Scripts,
logs, notes and parked commits never expire."""

import json
from datetime import datetime, timedelta, timezone

from flotilla.gc import archive, expiry
from ledgerkit import PROFILE, git, make_ledger, repo_with_origin

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=PROFILE, live=())


def archived(ledger, tmp_path, name, files: dict, *, days_old: int):
    tree = tmp_path / f"app-{name}"
    if not tree.exists():
        git(ledger.root, "worktree", "add", "-q", "-b", f"fleet/{name}", str(tree))
    for path, data in files.items():
        (tree / path).parent.mkdir(parents=True, exist_ok=True)
        (tree / path).write_bytes(data)
    entry = archive.put(ledger, entry=f"work-{name}", tree=tree, files=list(files), tier="2", approved_by="test",
                        branch=f"fleet/{name}", head=git(tree, "rev-parse", "HEAD"))
    path = archive.root_of(ledger) / "entries" / entry.id / "entry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["when"] = (NOW - timedelta(days=days_old)).isoformat(timespec="seconds")
    path.write_text(json.dumps(data), encoding="utf-8")
    return tree, entry.id


def due(ledger, data, *, now=NOW, low=False):
    return expiry.due(archive.entries(ledger), now=now, low=low, data=data)


def say(ledger, data, value, *, now=NOW, low=False):
    found = due(ledger, data, now=now, low=low)
    asked = expiry.question(found, entries=archive.entries(ledger), now=now, low=low)
    return expiry.record(data, found, asked["id"], [value], now=now)


def test_media_is_due_three_days_before_the_month_ends(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"p" * 100, "notes.json": b"{}"}, days_old=27)
    found = due(ledger, {})
    assert [record["from"].rsplit("/", 1)[-1] for _, record in found] == ["render.png"]
    assert due(ledger, {}, now=NOW - timedelta(days=1)) == []
    asked = expiry.question(found, entries=archive.entries(ledger), now=NOW)
    assert asked["id"].startswith("expiry@") and "in 3 days I remove" in asked["question"]


def test_low_disk_raises_the_question_at_once(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"clip.mp4": b"v" * 100}, days_old=2)
    assert due(ledger, {}) == [] and len(due(ledger, {}, low=True)) == 1


def test_three_ignored_days_keep_the_archive_another_month(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"p"}, days_old=28)
    data = {}
    found = due(ledger, data)
    days = [(NOW + timedelta(days=step)) for step in range(4)]
    assert [expiry.raised(data, found, now=day, low=False) for day in days[:3]] == [True, True, True]
    assert expiry.raised(data, found, now=days[3], low=False) is False
    assert data["expiry"]["until"].startswith((days[3] + timedelta(days=30)).date().isoformat())
    assert due(ledger, data, now=days[3]) == []


def test_low_disk_asks_even_after_three_ignored_days(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"p"}, days_old=28)
    data = {}
    found = due(ledger, data)
    for step in range(4):
        expiry.raised(data, found, now=NOW + timedelta(days=step), low=False)
    later = NOW + timedelta(days=5)
    low = due(ledger, data, now=later, low=True)
    assert low and expiry.raised(data, low, now=later, low=True) is True


def test_a_yes_removes_only_what_was_shown(tmp_path):
    """Review Focus 4."""
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"first"}, days_old=31)
    data = {}
    say(ledger, data, "yes")
    archived(ledger, tmp_path, "b", {"other.png": b"second"}, days_old=31)
    lines = expiry.carry_out(ledger, data, now=NOW)
    assert any("removed" in line and "1 file" in line for line in lines), lines
    objects = archive.root_of(ledger) / "objects"
    left = {path.name for path in objects.rglob("*") if path.is_file()}
    import hashlib
    assert hashlib.sha256(b"second").hexdigest() in left and hashlib.sha256(b"first").hexdigest() not in left
    still = due(ledger, data)
    assert [record["from"].rsplit("/", 1)[-1] for _, record in still] == ["other.png"]
    assert expiry.question(still, entries=archive.entries(ledger), now=NOW) is not None


def test_the_yes_waits_for_the_day_it_named(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"first"}, days_old=28)
    data = {}
    say(ledger, data, "yes")
    assert expiry.carry_out(ledger, data, now=NOW) == []
    assert any("removed" in line for line in expiry.carry_out(ledger, data, now=NOW + timedelta(days=3)))


def test_a_shared_object_stays_while_another_record_needs_it(tmp_path):
    """Review Focus 3."""
    import hashlib
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"same"}, days_old=31)
    archived(ledger, tmp_path, "b", {"copy.png": b"same"}, days_old=1)
    data = {}
    say(ledger, data, "yes")
    expiry.carry_out(ledger, data, now=NOW)
    digest = hashlib.sha256(b"same").hexdigest()
    assert (archive.root_of(ledger) / "objects" / digest[:2] / digest).exists()
    old = next(entry for entry in archive.entries(ledger) if entry.id == "work-a")
    assert old.files[0]["expired"]["approved_by"].startswith("expiry@")
    assert "expired" in (archive.root_of(ledger) / "MANIFEST").read_text(encoding="utf-8")


def test_nothing_expires_without_a_yes(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"p", "run.log": b"l"}, days_old=40)
    data = {}
    assert expiry.carry_out(ledger, data, now=NOW) == []
    assert all("expired" not in record for entry in archive.entries(ledger) for record in entry.files)


def test_keep_moves_the_month(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"p"}, days_old=31)
    data = {}
    say(ledger, data, "keep")
    expiry.carry_out(ledger, data, now=NOW)
    assert due(ledger, data) == [] and due(ledger, data, now=NOW + timedelta(days=28))


def test_restore_names_an_expired_file(tmp_path):
    root, ledger = world(tmp_path)
    tree, entry_id = archived(ledger, tmp_path, "a", {"render.png": b"p", "notes.txt": b"n"}, days_old=31)
    data = {}
    say(ledger, data, "yes")
    expiry.carry_out(ledger, data, now=NOW)
    lines = archive.restore(ledger, entry_id)
    assert (tree / "notes.txt").read_bytes() == b"n" and not (tree / "render.png").exists()
    assert any("render.png" in line and "removed at expiry" in line for line in lines), lines


# Security review of 96debba: the answer store is a file, so what it says is checked against the archive itself.

def test_a_yes_whose_record_does_not_hold_together_removes_nothing(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"p"}, days_old=31)
    data = {}
    say(ledger, data, "yes")
    data["expiry"]["answer"]["seen"] = "0" * 64   # the id and the files shown no longer agree with it
    assert expiry.carry_out(ledger, data, now=NOW) == []
    assert all("expired" not in record for entry in archive.entries(ledger) for record in entry.files)
    assert "answer" not in data["expiry"]


def test_a_yes_never_removes_what_is_not_media(tmp_path):
    root, ledger = world(tmp_path)
    archived(ledger, tmp_path, "a", {"render.png": b"p", "notes.json": b"{}"}, days_old=31)
    data = {}
    say(ledger, data, "yes")
    entry = archive.entries(ledger)[0]
    answer = data["expiry"]["answer"]
    answer["shown"] = sorted(list(item) for item in (expiry._triple(entry, record) for record in entry.files))
    answer["seen"] = expiry.fingerprint([(entry, record) for record in entry.files])
    answer["id"] = "expiry@" + answer["seen"][:12]
    expiry.carry_out(ledger, data, now=NOW)
    kept = next(record for record in archive.entries(ledger)[0].files if record["from"].endswith("notes.json"))
    assert "expired" not in kept


def test_an_entry_whose_id_is_not_its_folder_is_not_read(tmp_path):
    root, ledger = world(tmp_path)
    tree, entry_id = archived(ledger, tmp_path, "a", {"render.png": b"p"}, days_old=31)
    path = archive.root_of(ledger) / "entries" / entry_id / "entry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id"] = "../../outside"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert archive.entries(ledger) == []
