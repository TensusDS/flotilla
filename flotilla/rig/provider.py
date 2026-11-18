"""flotilla's side of a rental service (rig design, sections 2 and 6): the key, read only from a private file named
by the service; every adapter answer scrubbed again; every adapter failure made a `ProviderError` with the key
masked. The adapter decides how to talk to its service; this decides what may leave it."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

from flotilla.rig import providers
from flotilla.rig.secrets import scrub, scrub_text
from flotilla.rig.settings import KeyRefused, key_path, read_key


#: An offer's name as a service gives it - digits (vast), or a size in a region (`gpu-mi300x1-192gb@atl1`): safe to
#: print in a line and to put in a URL's path, whatever the adapter answered.
OFFER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._-]{0,79}", re.ASCII)


class ProviderError(RuntimeError):
    """The service could not be asked, or its answer cannot be trusted."""


class OfferGone(ProviderError):
    """The service says the offer can no longer be rented: nothing was made, so another offer may be tried."""


@dataclass(frozen=True)
class Listed:
    instance: str
    label: str
    status: str
    hourly: float | None = None
    address: str = ""


@dataclass(frozen=True)
class Offer:
    offer: str
    gpu: str
    hourly: float
    reliability: float
    datacenter: bool


class Rented:
    def __init__(self, name: str, *, key: Path):
        self.name = name
        self.adapter = providers.load(name)
        self.key = Path(key)

    def _secret(self) -> str:
        try:
            return read_key(self.key)
        except KeyRefused as err:
            raise ProviderError(str(err)) from None

    def _ask(self, call, *args):
        secret = self._secret()
        try:
            return call(secret, *args)
        except self.adapter.AdapterError as err:
            raise ProviderError(scrub_text(str(err), [secret])) from None

    def instances(self) -> list[Listed]:
        rows, _ = scrub(self._ask(self.adapter.instances))
        found = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or not isinstance(row.get("instance"), str):
                continue
            hourly = row.get("hourly")
            found.append(Listed(row["instance"], str(row.get("label") or ""), str(row.get("status") or ""),
                                float(hourly) if isinstance(hourly, (int, float)) and not isinstance(hourly, bool)
                                else None, str(row.get("address") or "")))
        return found

    def credit(self) -> float | None:
        """The account's credit, or None when the key may not read the account (rig design, section 5): whether it
        may is the person's choice of key, not a failure. Only a number leaves, whatever the adapter answered."""
        secret = self._secret()
        try:
            found = self.adapter.credit(secret)
        except self.adapter.AdapterError as err:
            if err.status in (401, 403):
                return None
            raise ProviderError(scrub_text(str(err), [secret])) from None
        if not isinstance(found, (int, float)) or isinstance(found, bool) or not math.isfinite(found):
            raise ProviderError(f"{self.name} answered no credit")
        return float(found)

    def destroy(self, instance: str) -> None:
        self._ask(self.adapter.destroy, instance)

    def offers(self, want: dict) -> list[Offer]:
        rows, _ = scrub(self._ask(self.adapter.offers, want))
        found = []
        for row in rows if isinstance(rows, list) else []:
            try:
                found.append(Offer(str(row["offer"]), str(row["gpu"]), float(row["hourly"]),
                                   float(row["reliability"]), bool(row["datacenter"])))
            except (KeyError, TypeError, ValueError):
                continue
        return [item for item in found if item.datacenter and OFFER_ID.fullmatch(item.offer)]

    def create(self, offer: str, *, image: str, disk_gb: float, env: dict, onstart: str, label: str,
               public_key: str = "") -> str:
        """`public_key` goes only to an adapter whose service puts ssh keys on a machine while it makes it
        (`KEY_AT_CREATE`); for the others `attach_ssh` follows the create."""
        gone = getattr(self.adapter, "OfferGone", None)
        secret = self._secret()
        early = {"public_key": public_key} if self.key_at_create() else {}
        try:
            found = self.adapter.create(secret, offer, image=image, disk_gb=disk_gb, env=env, onstart=onstart,
                                        label=label, **early)
        except self.adapter.AdapterError as err:
            said = scrub_text(str(err), [secret])
            raise (OfferGone(said) if gone is not None and isinstance(err, gone) else ProviderError(said)) from None
        if not isinstance(found, str) or not found.isascii() or not found.isdigit():
            raise ProviderError(f"{self.name} answered no instance id for offer {offer}")
        return found

    def attach_ssh(self, instance: str, public_key: str) -> None:
        self._ask(self.adapter.attach_ssh, instance, public_key)

    def onstart(self) -> str:
        return self.adapter.onstart()

    def key_at_create(self) -> bool:
        """Whether the service puts ssh keys on a machine only while it makes it, from the keys the account holds."""
        return getattr(self.adapter, "KEY_AT_CREATE", False) is True

    def notes(self) -> tuple:
        """What the person should know of this service before they rent from it, in the adapter's words."""
        found = getattr(self.adapter, "NOTES", ())
        return tuple(text for text in found if isinstance(text, str)) if isinstance(found, (tuple, list)) else ()


def provider_for(name: str, *, env=os.environ) -> Rented:
    if name not in providers.KNOWN:
        raise ProviderError(f"no provider `{name}`; this version knows {', '.join(providers.KNOWN)}")
    return Rented(name, key=key_path(name, env))
