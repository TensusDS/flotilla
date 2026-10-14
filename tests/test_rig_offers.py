"""A stale offer, an empty market and a reaper armed again (field report of 2026-10-14, flotilla 0.10.2 on twosuns):
`rig up` took the first offer only, so an ask vast still listed but could no longer rent failed every run; a failed
machine with no instance held the one-machine ceiling until a reap; `rig run --wait` waited for room but never for an
offer; and `flotilla rig` called the reaper silent while cron was firing - it had only just been armed again."""

import datetime as dt
import io
import time
from contextlib import chdir, redirect_stdout

import pytest

from flotilla import cli
from flotilla.rig import commands, health
from flotilla.rig import journal as j
from flotilla.rig import provider
from flotilla.rig.providers import vast as vast_adapter
from rigkit import OFFERS, fake_key
from test_rig_up import T0, journal, opened, up, world  # noqa: F401 - the rig up world: fake vast, fake clock, state

CHEAPEST, NEXT = str(OFFERS[0]["id"]), str(OFFERS[1]["id"])


def stale(fake, *offers):
    """These asks still list, but vast no longer rents them: its answer as seen on 2026-10-14."""
    real = fake.__call__

    def send(method, url, headers, body, timeout):
        ask = url.rstrip("/").rsplit("/", 1)[-1]
        if method == "PUT" and "/api/v0/asks/" in url:
            fake.asked.append(ask)
            if ask in offers:
                fake.requests.append((method, url, dict(headers)))
                return 400, (b'{"error": true, "msg": "error 404/3603: no_such_ask Instance type by id '
                             + ask.encode() + b' is not available."}')
        return real(method, url, headers, body, timeout)
    fake.asked = []
    return send


def test_a_stale_first_offer_is_passed_over_for_the_next(world, monkeypatch):
    opened(world)
    monkeypatch.setattr(vast_adapter, "SEND", stale(world["fake"], CHEAPEST))
    code, out = up(world)
    assert code == 0 and world["fake"].created == 1
    assert world["fake"].asked == [CHEAPEST, NEXT]
    assert f"offer {CHEAPEST} is gone" in out
    assert journal(world).machines()["m1"].state == j.READY


def test_when_every_offer_is_gone_the_refusal_counts_them_and_the_machine_frees_the_ceiling(world, monkeypatch):
    opened(world)
    monkeypatch.setattr(vast_adapter, "SEND", stale(world["fake"], CHEAPEST, NEXT))
    code, out = up(world)
    assert code == 2 and "2 offers tried, all gone" in out and world["fake"].created == 0
    assert journal(world).machines()["m1"].state == j.GONE      # it never had an instance: nothing to reap
    monkeypatch.setattr(vast_adapter, "SEND", world["fake"])    # the market moved on
    code, out = up(world)
    assert code == 0 and journal(world).machines()["m2"].state == j.READY   # the ceiling was free at once


def test_at_most_five_offers_are_tried(world, monkeypatch):
    opened(world)
    many = [{**OFFERS[0], "id": 70000 + n, "dph_total": 0.10 + n / 100} for n in range(7)]
    world["fake"].offers = many
    monkeypatch.setattr(vast_adapter, "SEND", stale(world["fake"], *(str(item["id"]) for item in many)))
    code, out = up(world)
    assert code == 2 and len(world["fake"].asked) == 5 and "5 offers tried, all gone" in out


def test_any_other_create_failure_tries_no_second_offer_and_waits_for_the_reaper(world):
    """A refusal that does not say the offer is gone may come after vast made the machine: a second create could
    pay for two. It is refused as before, and the machine stays failed for the reaper to look for by its label."""
    opened(world)
    world["fake"].create_status = 500
    code, out = up(world)
    puts = [url for method, url, _ in world["fake"].requests if method == "PUT"]
    assert code == 2 and len(puts) == 1 and journal(world).machines()["m1"].state == j.FAILED


def test_no_offer_within_the_ceilings_frees_the_ceiling_at_once(world):
    opened(world)
    world["fake"].offers = [OFFERS[3]]
    code, out = up(world)
    assert code == 2 and "no offer" in out and journal(world).machines()["m1"].state == j.GONE


@pytest.mark.parametrize("status,said,gone", [
    (400, "error 404/3603: no_such_ask Instance type by id 7 is not available.", True),
    (404, "Instance type by id 7 is not available.", True),
    (400, "Invalid user key", False),
    (500, "no_such_ask", False),
])
def test_the_adapter_says_an_offer_is_gone_only_when_vast_does(tmp_path, monkeypatch, status, said, gone):
    import json
    monkeypatch.setattr(vast_adapter, "SEND", lambda *args: (status, json.dumps({"msg": said}).encode()))
    found = provider.Rented("vast", key=fake_key(tmp_path))
    with pytest.raises(provider.ProviderError) as err:
        found.create("7", image="i", disk_gb=10, env={}, onstart="", label="flotilla:abcdefabcdef:m1")
    assert isinstance(err.value, provider.OfferGone) is gone


# ---- the reaper armed again: silence is measured from when it was needed, not from its last pass

def test_a_reaper_armed_again_moments_ago_is_not_called_silent(world):
    health.stamp(world["state"], T0 - dt.timedelta(hours=15), ok=True, note="")   # the session that ended at 05:05
    opened(world)
    world["fake"].offers = [OFFERS[3]]
    up(world)                                       # a refused try left nothing live; ask the status as the seat did
    rig = journal(world)
    rig.add_machine("s1", "vast", lambda mid: f"flotilla:abcdefabcdef:{mid}")
    world["clock"]["at"] = T0 + dt.timedelta(minutes=4)
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["rig"])
    assert "REAPER SILENT" not in out.getvalue()
    assert not [item for item in health.findings(world["state"], now=world["clock"]["at"], run=commands.CRON_RUN)
                if "silent" in item[1]]
    from flotilla.rig import surface   # the orchestrator's attention list says the same
    assert not [item for item in surface.items(world["state"], world["clock"]["at"]) if "silent" in str(item)]
    world["clock"]["at"] = T0 + dt.timedelta(minutes=13)
    out = io.StringIO()
    with redirect_stdout(out):
        cli.main(["rig"])
    assert "REAPER SILENT" in out.getvalue()


# ---- rig run --wait waits for an offer too

def test_rig_run_asks_the_market_again_until_its_deadline_and_then_says_why(world, monkeypatch, tmp_path):
    """The evening of the report: nothing within 1.20 $/h, a run with --wait 900 refused in 0 s. It waits now, asking
    again every MARKET_EVERY seconds; each try that found nothing frees the ceiling at once."""
    from flotilla.rig import run as run_module
    from rigtree import git_tree
    monkeypatch.setattr(run_module, "POLL", 0.05)
    monkeypatch.setattr(run_module, "MARKET_EVERY", 0.2)
    opened(world)
    tree = git_tree(tmp_path / "tree", {"package.json": "{}", ".flotilla/project.toml": "schema = 1\n"})
    world["fake"].offers = []
    started = time.monotonic()
    out = io.StringIO()
    with redirect_stdout(out), chdir(tree):
        code = cli.main(["rig", "run", "--root", str(tree), "--as", "minor 8", "--wait", "1", "--", "true"])
    said = out.getvalue()
    assert code == 2 and "no offer within the ceilings within --wait" in said and "waits: no offer" in said
    assert time.monotonic() - started >= 0.9
    rows = journal(world).machines().values()
    assert len(rows) >= 2 and all(item.state == j.GONE for item in rows)   # each try freed the ceiling
