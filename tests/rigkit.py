"""Doubles for the rig: each rental service's API as its adapter meets it, without a network or money.

Every double takes the same knobs, so the contract suite runs over all of them: `instances` (id -> {"label",
"actual_status", "dph_total"}), `sticky` (answer a destroy and stay listed), `page` (page size), `listing_status` and
`destroy_status` (answer with that HTTP status and an error body), `garbage` (an unparsable body), `leak` (a
credential in every row and every error body, in a field the adapter must not pass on).
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
                 leak="", offers=(), boot_polls=0, create_status=200):
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
                if self.instances[i].get("actual_status") == "loading" and self.polls[i] > self.boot_polls:
                    self.instances[i]["actual_status"] = "running"
            rows = [{"id": int(i), "label": self.instances[i].get("label"),
                     "actual_status": self.instances[i].get("actual_status", "running"),
                     "dph_total": self.instances[i].get("dph_total", 0.30),
                     "ssh_host": self.instances[i].get("ssh_host"), "ssh_port": self.instances[i].get("ssh_port"),
                     **({"instance_api_key": self.leak} if self.leak else {})} for i in chunk]
            more = after + self.page < len(ids)
            return 200, json.dumps({"instances": rows, "next_token": str(after + self.page) if more else None}).encode()
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
            self.instances[instance] = {"label": payload.get("label"), "actual_status": "loading", "dph_total": 0.20,
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


DOUBLES = {"vast": FakeVast}
