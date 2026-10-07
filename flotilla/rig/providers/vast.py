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


def _call(key, method, path, query=None, body=None):
    url = BASE + path + (f"?{_query(query)}" if query else "")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": "flotilla-rig"}
    try:
        payload = json.dumps(body).encode() if body is not None else (b"{}" if method == "DELETE" else None)
        status, raw = SEND(method, url, headers, payload, TIMEOUT)
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
            host, port = row.get("ssh_host"), row.get("ssh_port")
            found.append({"instance": str(row["id"]), "label": label if isinstance(label, str) else "",
                          "status": str(row.get("actual_status") or row.get("cur_state") or ""),
                          "hourly": float(price) if isinstance(price, (int, float)) and not isinstance(price, bool)
                          else None,
                          "address": f"{host}:{port}" if isinstance(host, str) and isinstance(port, int) else ""})
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


_NAME = re.compile(r"[A-Za-z0-9 _.-]{1,40}")
_EGL = '{"file_format_version":"1.0.0","ICD":{"library_path":"libEGL_nvidia.so.0"}}'


def offers(key, want):
    """Datacenter offers with one GPU within the person's price, cheapest first; filtered again here, so an answer
    outside the asked filter never reaches flotilla. vast honours `datacenter` and `reliability2` as filters
    (measured 2026-10-07)."""
    top = float(want["max_hourly"])
    floor = float(want.get("min_reliability", 0.98))
    disk = float(want.get("disk_gb", 30))
    query = {"verified": {"eq": True}, "external": {"eq": False}, "rentable": {"eq": True}, "rented": {"eq": False},
             "datacenter": {"eq": True}, "num_gpus": {"eq": 1}, "reliability2": {"gte": floor},
             "dph_total": {"lte": top}, "disk_space": {"gte": disk}, "order": [["dph_total", "asc"]],
             "type": "on-demand", "limit": int(want.get("limit", 64)), "allocated_storage": disk}
    gpus = [name for name in want.get("gpus") or [] if isinstance(name, str) and _NAME.fullmatch(name)]
    if gpus:
        query["gpu_name"] = {"in": gpus}
    answer = _call(key, "POST", "/api/v0/bundles/", body=query)
    rows = answer.get("offers") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        raise AdapterError("vast's offer search has no list of offers")
    found = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), int):
            continue
        price, sure = row.get("dph_total"), row.get("reliability2") or row.get("reliability")
        if not isinstance(price, (int, float)) or not isinstance(sure, (int, float)):
            continue
        if row.get("hosting_type") != 1 or row.get("num_gpus", 1) != 1 or price > top or sure < floor or (
                gpus and row.get("gpu_name") not in gpus):
            continue
        found.append({"offer": str(row["id"]), "gpu": str(row.get("gpu_name") or "")[:40], "hourly": float(price),
                      "reliability": float(sure), "datacenter": True})
    return sorted(found, key=lambda item: item["hourly"])


def create(key, offer, *, image, disk_gb, env, onstart, label):
    if not isinstance(offer, str) or not _ID.fullmatch(offer):
        raise AdapterError(f"not a vast offer id: {offer!r}")
    body = {"client_id": "me", "image": image, "env": dict(env), "price": None, "disk": float(disk_gb),
            "label": label, "extra": None, "onstart": onstart, "runtype": "ssh_proxy", "image_login": None,
            "python_utf8": False, "lang_utf8": False, "use_jupyter_lab": False, "jupyter_dir": None, "force": False,
            "cancel_unavail": True, "template_hash_id": None, "user": None}
    answer = _call(key, "PUT", f"/api/v0/asks/{offer}/", body=body)
    contract = answer.get("new_contract") if isinstance(answer, dict) else None
    if not isinstance(answer, dict) or answer.get("success") is False or not isinstance(contract, int):
        raise AdapterError(f"vast did not say which instance offer {offer} became")
    return str(contract)


def attach_ssh(key, instance, public_key):
    if not isinstance(instance, str) or not _ID.fullmatch(instance):
        raise AdapterError(f"not a vast instance id: {instance!r}")
    _call(key, "POST", f"/api/v0/instances/{instance}/ssh/", body={"ssh_key": public_key})


def onstart():
    """The start script: the NVIDIA EGL vendor file (WebGL on the GPU, probe of 2026-10-06), a heartbeat, and the
    watchdog. Older than FLOTILLA_WATCHDOG_MINUTES (5-45, default 45) - or missing - it asks vast to destroy this
    instance with the key vast put in the container, and when that is refused, to stop it (rig design, section 5)."""
    return f"""mkdir -p /usr/share/glvnd/egl_vendor.d
[ -f /usr/share/glvnd/egl_vendor.d/10_nvidia.json ] || echo '{_EGL}' > /usr/share/glvnd/egl_vendor.d/10_nvidia.json
touch /root/flotilla-heartbeat
cat > /root/flotilla-watchdog.sh <<'WATCHDOG'
limit=${{FLOTILLA_WATCHDOG_MINUTES:-45}}
case "$limit" in ''|*[!0-9]*) limit=45;; esac
[ "$limit" -lt 5 ] && limit=5
[ "$limit" -gt 45 ] && limit=45
api="{BASE}/api/v0/instances/$CONTAINER_ID/"
call() {{
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 30 -X "$1" -H "Authorization: Bearer $CONTAINER_API_KEY" -H 'Content-Type: application/json' --data "$2" "$api"
  else
    node -e 'fetch(process.argv[1],{{method:process.argv[2],signal:AbortSignal.timeout(30000),headers:{{Authorization:"Bearer "+process.env.CONTAINER_API_KEY,"Content-Type":"application/json"}},body:process.argv[3]}}).then(r=>process.exit(r.ok?0:1),()=>process.exit(1))' "$api" "$1" "$2"
  fi
}}
while sleep 60; do
  age=$(( $(date +%s) - $(stat -c %Y /root/flotilla-heartbeat 2>/dev/null || echo 0) ))
  if [ "$age" -gt $(( limit * 60 )) ]; then
    echo "flotilla watchdog: heartbeat $age s old; destroying"
    call DELETE '{{}}' || call PUT '{{"state":"stopped"}}'
  fi
done
WATCHDOG
nohup sh /root/flotilla-watchdog.sh > /root/flotilla-watchdog.log 2>&1 &
"""
