"""vast.ai over its REST API - a rig rental-service adapter (rig design, sections 2 and 5).

Standalone: standard library only, nothing from flotilla, because the reaper's launcher loads a copy of this file
when no flotilla is installed. Not through the `vastai` CLI: its source (1.6.0) retries a 401 or an expired 2FA
session with the person's own account key from ~/.config/vastai/, deleting their 2FA session file, and exits 0 after
an API error (review of 2026-10-06).

The contract: `instances(key)` returns every instance of the account through every page, each as exactly
`instance`, `label`, `status`, `hourly` - nothing else of vast's answer leaves this file, so the instance key vast
puts in its rows never does; `destroy(key, id)` asks for a destroy; a non-2xx answer, a timeout or a body that does
not parse raises `AdapterError`. The key goes only into the `Authorization` header; the base URL is a constant.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

NAME = "vast"
BASE = "https://console.vast.ai"
PAGE = 25
MAX_PAGES = 200
TIMEOUT = 30


class AdapterError(Exception):
    def __init__(self, text, status=None):
        super().__init__(text)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None   # urllib would carry the Authorization header to wherever a redirect points


_OPENER = urllib.request.build_opener(_NoRedirect)


def _send(method, url, headers, body, timeout):
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as answer:
            return answer.status, answer.read()
    except urllib.error.HTTPError as err:   # a refused redirect lands here too, as its 3xx status
        return err.code, err.read()


#: The one door to the network; tests replace it, and the suite's conftest makes the real one refuse.
SEND = _send
_ID = re.compile(r"[0-9]{1,15}", re.ASCII)


def _query(query):
    return "&".join(f"{name}={urllib.parse.quote_plus(value if isinstance(value, str) else json.dumps(value))}"
                    for name, value in query.items())


def _call(key, method, path, query=None):
    url = BASE + path + (f"?{_query(query)}" if query else "")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": "flotilla-rig"}
    try:
        status, raw = SEND(method, url, headers, b"{}" if method == "DELETE" else None, TIMEOUT)
    except Exception as err:   # noqa: BLE001 - OSError, http.client's IncompleteRead, anything: a failure, said once
        raise AdapterError(f"vast {method} {path}: {type(err).__name__}") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    if not 200 <= status < 300:
        said = data.get("msg") if isinstance(data, dict) and isinstance(data.get("msg"), str) else ""
        raise AdapterError(f"vast {method} {path} answered {status}: {said.replace(key, '[masked]')[:200]}", status)
    if data is None:
        raise AdapterError(f"vast {method} {path} answered an unparsable body", status)
    return data


def instances(key):
    found, query = [], {"select_filters": {}, "order_by": [{"col": "id", "dir": "asc"}], "limit": PAGE}
    for _ in range(MAX_PAGES):
        page = _call(key, "GET", "/api/v1/instances/", query)
        rows = page.get("instances") if isinstance(page, dict) else None
        if not isinstance(rows, list):
            raise AdapterError("vast's instance listing has no list of instances")
        for row in rows:
            if not isinstance(row, dict) or row.get("id") is None:
                continue
            label, price = row.get("label"), row.get("dph_total")
            found.append({"instance": str(row["id"]), "label": label if isinstance(label, str) else "",
                          "status": str(row.get("actual_status") or row.get("cur_state") or ""),
                          "hourly": float(price) if isinstance(price, (int, float)) and not isinstance(price, bool)
                          else None})
        token = page.get("next_token")
        if not token:
            return found
        query = {**query, "after_token": str(token)}
    raise AdapterError(f"vast's instance listing did not end after {MAX_PAGES} pages")


def destroy(key, instance):
    if not isinstance(instance, str) or not _ID.fullmatch(instance):
        raise AdapterError(f"not a vast instance id: {instance!r}")
    answer = _call(key, "DELETE", f"/api/v0/instances/{instance}/")
    if isinstance(answer, dict) and answer.get("success") is False:
        raise AdapterError(f"vast refused to destroy {instance}")
