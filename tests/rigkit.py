"""Doubles for the rig: each rental service's API as its adapter meets it, without a network or money.

Every double takes the same knobs, so the contract suite runs over all of them: `instances` (id -> {"label", ...},
the other fields being the service's own: vast's "actual_status" and "dph_total", DigitalOcean's "status" and
"hourly"), `sticky` (answer a destroy and stay listed), `page` (page size), `listing_status` and
`destroy_status` (answer with that HTTP status and an error body), `garbage` (an unparsable body), `leak` (a
credential in every row and every error body, in a field the adapter must not pass on), `credit` (what the account
holds, said the way the service says it) and `user_status` (the account's answer, which always carries more of the
account than a number - as vast's does). What differs between services - an offer's rows and ids, an image, an
address, the credit's request - each double states as class attributes (`OFFERS`, `WITHIN`, `IMAGE`, `RUNNING`,
`CREDIT`).
"""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path


def fake_key(tmp_path: Path, mode: int = 0o600, text: str = "account-key-0123456789", provider: str = "vast") -> Path:
    path = tmp_path / "config" / "flotilla" / "rig" / f"{provider}.key"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


class FakeVast:
    def __init__(self, instances=None, *, sticky=(), page=25, listing_status=200, destroy_status=200, garbage=False,
                 leak="", offers=(), boot_polls=0, create_status=200, credit=41.2, user_status=200):
        self.instances = dict(instances or {})
        self.sticky = set(sticky)
        self.page = page
        self.listing_status = listing_status
        self.destroy_status = destroy_status
        self.garbage = garbage
        self.leak = leak
        self.requests: list[tuple[str, str, dict]] = []
        self.offers = list(offers)
        self.boot_polls = boot_polls
        self.create_status = create_status
        self.ssh_keys: dict = {}
        self.created = 0
        self.polls: dict = {}
        self.last_query = None
        self.credit = credit
        self.user_status = user_status

    def _error(self, status):
        body = {"error": True, "msg": "Invalid user key", **({"api_key": self.leak} if self.leak else {})}
        return status, json.dumps(body).encode()

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers)))
        parsed = urllib.parse.urlparse(url)
        if method == "GET" and parsed.path == "/api/v1/instances/":
            if self.garbage:
                return 200, b"<html>maintenance</html>"
            if self.listing_status != 200:
                return self._error(self.listing_status)
            query = urllib.parse.parse_qs(parsed.query)
            after = int(query.get("after_token", ["0"])[0])
            ids = sorted(self.instances, key=int)
            chunk = ids[after:after + self.page]
            for i in chunk:
                self.polls[i] = self.polls.get(i, 0) + 1
                if self.instances[i].get("actual_status", "") is None and self.polls[i] > 1:
                    self.instances[i]["actual_status"] = "loading"   # measured 2026-10-07: null, then loading
                if self.instances[i].get("actual_status") == "loading" and self.polls[i] > self.boot_polls:
                    self.instances[i]["actual_status"] = "running"
            rows = [{"id": int(i), "label": self.instances[i].get("label"),
                     "actual_status": self.instances[i].get("actual_status", "running"),
                     "cur_state": "running", "intended_status": "running",   # what vast intends, from the first second
                     "dph_total": self.instances[i].get("dph_total", 0.30),
                     "ssh_host": self.instances[i].get("ssh_host"), "ssh_port": self.instances[i].get("ssh_port"),
                     **({"instance_api_key": self.leak} if self.leak else {})} for i in chunk]
            more = after + self.page < len(ids)
            return 200, json.dumps({"instances": rows, "next_token": str(after + self.page) if more else None}).encode()
        if method == "GET" and parsed.path == "/api/v0/users/current/":
            if self.user_status != 200:
                return self._error(self.user_status)
            return 200, json.dumps({"id": 7, "credit": self.credit, "balance": 0, "api_key": "account-api-key-0f0f0f0f",
                                    "sid": "session-id-1a2b3c4d", "email": "person@example.com",
                                    "billaddress_line1": "1 Street", "total_spend": 3.5}).encode()
        if method == "DELETE" and parsed.path.startswith("/api/v0/instances/"):
            if self.destroy_status != 200:
                return self._error(self.destroy_status)
            instance = parsed.path.rstrip("/").rsplit("/", 1)[-1]
            if instance not in self.sticky:
                self.instances.pop(instance, None)
            return 200, json.dumps({"success": True}).encode()
        if method == "POST" and parsed.path == "/api/v0/bundles/":
            self.last_query = json.loads(body)
            return 200, json.dumps({"offers": self.offers}).encode()
        if method == "PUT" and parsed.path.startswith("/api/v0/asks/"):
            if self.create_status != 200:
                return self._error(self.create_status)
            payload = json.loads(body or b"{}")
            self.created += 1
            instance = str(900 + self.created)
            self.instances[instance] = {"label": payload.get("label"), "actual_status": None, "dph_total": 0.20,
                                        "ssh_host": "ssh4.vast.ai", "ssh_port": 30000 + self.created,
                                        "payload": payload}
            return 200, json.dumps({"success": True, "new_contract": int(instance),
                                    **({"instance_api_key": self.leak} if self.leak else {})}).encode()
        if method == "POST" and parsed.path.startswith("/api/v0/instances/") and parsed.path.endswith("/ssh/"):
            instance = parsed.path.split("/")[4]
            self.ssh_keys.setdefault(instance, []).append(json.loads(body)["ssh_key"])
            return 200, json.dumps({"success": True}).encode()
        return 404, b'{"error": true, "msg": "no such endpoint"}'


OFFERS = [
    {"id": 53776176, "gpu_name": "RTX 2080 Ti", "dph_total": 0.137, "reliability2": 0.999, "hosting_type": 1,
     "disk_space": 120.0, "instance_api_key": "should-never-leave"},
    {"id": 44053836, "gpu_name": "RTX 5060 Ti", "dph_total": 0.182, "reliability2": 0.983, "hosting_type": 1,
     "disk_space": 60.0},
    {"id": 1, "gpu_name": "RTX 4090", "dph_total": 0.10, "reliability2": 0.999, "hosting_type": 0, "disk_space": 60.0},
    {"id": 2, "gpu_name": "A100", "dph_total": 1.20, "reliability2": 0.999, "hosting_type": 1, "disk_space": 60.0},
]


#: What the contract suite asks of each double besides the knobs: the service's own rows for an offer search with
#: the ids the suite's filter must answer, an image the service could run, an instance that runs with the address
#: it must read as, and the request its credit is read with.
FakeVast.OFFERS = OFFERS
FakeVast.WITHIN = ["53776176", "44053836"]
FakeVast.IMAGE = "img:1"
FakeVast.RUNNING = ({"ssh_host": "ssh4.vast.ai", "ssh_port": 30123}, "ssh4.vast.ai:30123")
FakeVast.CREDIT = ("GET", "https://console.vast.ai/api/v0/users/current/")


def public_key(seed: bytes = b"k") -> str:
    """An ed25519 public key in the shape ssh-keygen writes, for the services that name a key by its fingerprint."""
    import base64
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + (seed * 32)[:32]
    return f"ssh-ed25519 {base64.b64encode(blob).decode()} flotilla rig"


PUBLIC_KEY = public_key()


class FakeDigitalOcean:
    """DigitalOcean's API as its published specification describes it (read 2026-11-18; nothing here was measured
    against the service): GPU Droplets listed only under `type=gpus`, pages by number with a `next` link, money as
    text, a delete answered 204 with no body, errors as `id` and `message`. An instance's knobs: `label` (its
    flotilla tag), `status` (`new`, `active`, `off`), `hourly`, `ip`, `gpu` (listed only among GPU Droplets), `tags`
    (others it carries). `keys`: the fingerprints the account holds, or None for any."""

    def __init__(self, instances=None, *, sticky=(), page=25, listing_status=200, destroy_status=200, garbage=False,
                 leak="", offers=(), boot_polls=0, create_status=200, credit=41.2, user_status=200, keys=None):
        self.instances = dict(instances or {})
        self.sticky = set(sticky)
        self.page = page
        self.listing_status = listing_status
        self.destroy_status = destroy_status
        self.garbage = garbage
        self.leak = leak
        self.requests: list[tuple[str, str, dict]] = []
        self.offers = list(offers)
        self.boot_polls = boot_polls
        self.create_status = create_status
        self.created = 0
        self.polls: dict = {}
        self.last_query = None
        self.credit = credit
        self.user_status = user_status
        self.keys = keys

    _WORDS = {401: ("unauthorized", "Unable to authenticate you"), 403: ("forbidden", "You are not authorized"),
              404: ("not_found", "The resource you were accessing could not be found."),
              422: ("unprocessable_entity", "The request could not be processed."),
              429: ("too_many_requests", "API Rate limit exceeded.")}

    def _error(self, status):
        code, said = self._WORDS.get(status, ("server_error", "Unexpected server-side error"))
        body = {"id": code, "message": said, "request_id": "4d9d8375-3c56-4925-a3e7-eb137fed17e9",
                **({"api_token": self.leak} if self.leak else {})}
        return status, json.dumps(body).encode()

    def _paged(self, field, rows, query):
        number, size = int(query.get("page", ["1"])[0]), min(int(query.get("per_page", ["20"])[0]), self.page)
        chunk = rows[(number - 1) * size:number * size]
        more = number * size < len(rows)
        links = {"pages": {"next": f"https://elsewhere.example/v2/{field}?page={number + 1}"}} if more else {}
        return chunk, {field: None, "links": links, "meta": {"total": len(rows)}}

    def _row(self, i):
        knobs = self.instances[i]
        self.polls[i] = self.polls.get(i, 0) + 1
        if knobs.get("status") == "new" and self.polls[i] > self.boot_polls:
            knobs["status"] = "active"
        status = knobs.get("status", "active")
        ip = knobs.get("ip") if status != "new" else None   # an address comes with the boot
        networks = {"v4": ([{"ip_address": "10.108.0.2", "type": "private"}, {"ip_address": ip, "type": "public"}]
                           if ip else []), "v6": []}
        tags = [*knobs.get("tags", []), *([knobs["label"]] if knobs.get("label") else [])]
        return {"id": int(i), "name": f"droplet-{i}", "status": status, "locked": False, "tags": tags,
                "size_slug": "gpu-a1x1-16gb", "networks": networks,
                "size": {"slug": "gpu-a1x1-16gb", "price_hourly": knobs.get("hourly", 0.30), "price_monthly": 223.2},
                **({"api_token": self.leak} if self.leak else {})}

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers)))
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        if method == "GET" and parsed.path == "/v2/droplets":
            if self.garbage:
                return 200, b"<html>maintenance</html>"
            if self.listing_status != 200:
                return self._error(self.listing_status)
            gpus = query.get("type", ["droplets"])[0] == "gpus"
            ids = [i for i in sorted(self.instances, key=int) if bool(self.instances[i].get("gpu")) is gpus]
            chunk, answer = self._paged("droplets", ids, query)
            answer["droplets"] = [self._row(i) for i in chunk]
            return 200, json.dumps(answer).encode()
        if method == "GET" and parsed.path == "/v2/customers/my/balance":
            if self.user_status != 200:
                return self._error(self.user_status)
            number = isinstance(self.credit, (int, float)) and not isinstance(self.credit, bool)
            owed = str(-self.credit) if number else self.credit   # the service answers what is owed, as text
            return 200, json.dumps({"month_to_date_balance": owed, "account_balance": "0.00",
                                    "month_to_date_usage": "3.50", "generated_at": "2026-11-18T10:00:00Z",
                                    "api_token": "account-api-key-0f0f0f0f"}).encode()
        if method == "DELETE" and parsed.path.startswith("/v2/droplets/"):
            if self.destroy_status != 200:
                return self._error(self.destroy_status)
            instance = parsed.path.rsplit("/", 1)[-1]
            if instance not in self.instances:
                return self._error(404)
            if instance not in self.sticky:
                self.instances.pop(instance)
            return 204, b""
        if method == "GET" and parsed.path == "/v2/sizes":
            self.last_query = {name: values[0] for name, values in query.items()}
            chunk, answer = self._paged("sizes", self.offers, query)
            answer["sizes"] = chunk
            return 200, json.dumps(answer).encode()
        if method == "GET" and parsed.path.startswith("/v2/account/keys/"):
            named = urllib.parse.unquote(parsed.path.rsplit("/", 1)[-1])
            if self.keys is not None and named not in self.keys:
                return self._error(404)
            return 200, json.dumps({"ssh_key": {"id": 512189, "fingerprint": named, "name": "flotilla rig"}}).encode()
        if method == "POST" and parsed.path == "/v2/droplets":
            if self.create_status != 200:
                return self._error(self.create_status)
            payload = json.loads(body or b"{}")
            self.created += 1
            instance = str(900 + self.created)
            price = next((row["price_hourly"] for row in self.offers if row.get("slug") == payload.get("size")), 0.20)
            self.instances[instance] = {"label": (payload.get("tags") or [None])[0], "status": "new",
                                        "hourly": price, "ip": f"203.0.113.{self.created}", "gpu": True,
                                        "payload": payload}
            return 202, json.dumps({"droplet": {"id": int(instance), "name": payload.get("name"), "status": "new",
                                                "tags": payload.get("tags"), "networks": {"v4": [], "v6": []}},
                                    "links": {"actions": [{"id": 7515, "rel": "create",
                                                           "href": "https://elsewhere.example/v2/actions/7515"}]},
                                    **({"api_token": self.leak} if self.leak else {})}).encode()
        return self._error(404)


def _size(slug, price, regions, *, model="", count=1, disk=120, available=True, **more):
    return {"slug": slug, "memory": 16384, "vcpus": 8, "disk": disk, "transfer": 10.0, "price_monthly": price * 744,
            "price_hourly": price, "regions": list(regions), "available": available, "description": "GPU",
            **({"gpu_info": {"count": count, "model": model, "vram": {"amount": 16, "unit": "gib"}}} if model else {}),
            **more}


#: A size listing as the specification shapes it. The two MI300X slugs are the documented ones; every other size,
#: every region and every price here is the double's own, not the service's.
SIZES = [
    _size("gpu-a1x1-16gb", 0.137, ["tor1"], model="vendor_a1", api_token="should-never-leave"),
    _size("gpu-b2x1-24gb", 0.182, ["nyc2", "atl1"], model="vendor_b2"),
    _size("gpu-c3x1-8gb", 0.10, [], model="vendor_c3"),                          # listed in no region: no offer
    _size("gpu-d4x1-8gb", 0.05, ["nyc2"], model="vendor_d4", available=False),   # not for new Droplets
    _size("s-1vcpu-1gb", 0.007, ["nyc2", "atl1"]),                               # no GPU
    _size("gpu-e5x1-8gb", 0.20, ["nyc2"], model="vendor_e5", disk=20),           # less disk than asked
    _size("gpu-mi300x1-192gb", 1.99, ["atl1"], model="amd_mi300x", disk=720),
    _size("gpu-mi300x8-1536gb", 15.92, ["atl1"], model="amd_mi300x", count=8, disk=1920),
]
FakeDigitalOcean.OFFERS = SIZES
FakeDigitalOcean.WITHIN = ["gpu-a1x1-16gb@tor1", "gpu-b2x1-24gb@atl1", "gpu-b2x1-24gb@nyc2"]
FakeDigitalOcean.IMAGE = "gpu-image-1"
FakeDigitalOcean.RUNNING = ({"ip": "203.0.113.7"}, "203.0.113.7:22")
FakeDigitalOcean.CREDIT = ("GET", "https://api.digitalocean.com/v2/customers/my/balance")


DOUBLES = {"vast": FakeVast, "digitalocean": FakeDigitalOcean}
