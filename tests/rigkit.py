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
                 leak=""):
        self.instances = dict(instances or {})
        self.sticky = set(sticky)
        self.page = page
        self.listing_status = listing_status
        self.destroy_status = destroy_status
        self.garbage = garbage
        self.leak = leak
        self.requests: list[tuple[str, str, dict]] = []

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
            rows = [{"id": int(i), "label": self.instances[i].get("label"),
                     "actual_status": self.instances[i].get("actual_status", "running"),
                     "dph_total": self.instances[i].get("dph_total", 0.30),
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
        return 404, b'{"error": true, "msg": "no such endpoint"}'


DOUBLES = {"vast": FakeVast}
