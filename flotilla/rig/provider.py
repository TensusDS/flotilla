"""flotilla's side of a rental service (rig design, sections 2 and 6): the key, read only from a private file named
by the service; every adapter answer scrubbed again; every adapter failure made a `ProviderError` with the key
masked. The adapter decides how to talk to its service; this decides what may leave it."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from flotilla.rig import providers
from flotilla.rig.secrets import scrub, scrub_text
from flotilla.rig.settings import KeyRefused, key_path, read_key


class ProviderError(RuntimeError):
    """The service could not be asked, or its answer cannot be trusted."""


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
        return [item for item in found if item.datacenter and item.offer.isascii() and item.offer.isdigit()]

    def create(self, offer: str, *, image: str, disk_gb: float, env: dict, onstart: str, label: str) -> str:
        found = self._ask(lambda key: self.adapter.create(key, offer, image=image, disk_gb=disk_gb, env=env,
                                                           onstart=onstart, label=label))
        if not isinstance(found, str) or not found.isascii() or not found.isdigit():
            raise ProviderError(f"{self.name} answered no instance id for offer {offer}")
        return found

    def attach_ssh(self, instance: str, public_key: str) -> None:
        self._ask(self.adapter.attach_ssh, instance, public_key)

    def onstart(self) -> str:
        return self.adapter.onstart()


def provider_for(name: str, *, env=os.environ) -> Rented:
    if name not in providers.KNOWN:
        raise ProviderError(f"no provider `{name}`; this version knows {', '.join(providers.KNOWN)}")
    return Rented(name, key=key_path(name, env))
