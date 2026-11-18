"""DigitalOcean over its public REST API - a rig rental-service adapter (rig design, sections 2 and 5).

It is also the way to an AMD Instinct MI300X: the AMD Developer Cloud is built on DigitalOcean, and DigitalOcean's
own pages for it create its GPU Droplets with `POST https://api.digitalocean.com/v2/droplets` and a personal access
token (docs.digitalocean.com/products/amd/how-to/create, read 2026-11-18). So one adapter serves both an ordinary
DigitalOcean team and an AMD Developer Cloud one; which of them it talks to is decided by the token alone.

Standalone: standard library only, nothing from flotilla, because the reaper's launcher loads a copy of this file
when no flotilla is installed. Everything below was read in DigitalOcean's published API specification
(github.com/digitalocean/openapi, 2026-11-18) and nothing was measured against the live service: no request with a
token was ever made while this was written.

How the contract maps onto Droplets:

- **The label is a tag.** A Droplet's name takes letters, digits, dots and dashes only, so `flotilla:<key>:<id>`
  cannot be one; a tag takes colons. `create` tags the Droplet with the label and names it after it; `instances`
  answers the tag that starts with `flotilla:` as the label. The token needs `tag:create` and `tag:read`: without
  the second the listing carries no tags, every label reads empty, and the reaper calls its own machine STUCK
  rather than destroy an instance it cannot tell apart.
- **`instances` asks twice.** `GET /v2/droplets` answers only non-GPU Droplets unless `type=gpus` is asked, so both
  listings are read, each through every page. The price is the Droplet's own `size.price_hourly`.
- **`destroy` deletes.** A Droplet is billed while it exists - powered off too ("Powering off a GPU Droplet does not
  stop billing ... To end billing, destroy the GPU Droplet", docs.digitalocean.com/products/droplets/details/pricing).
  Nothing here powers a Droplet off, and one the service lists `off` is answered as `stopped`, which the reaper
  drains at once. A `404 not_found` to the delete means the Droplet is already gone and is a success; every other
  answer but a 2xx is a failure.
- **Offers are GPU sizes in the regions that have them**, one offer per size and region, named `<size>@<region>`,
  at the price `GET /v2/sizes` gives - never a price written here. A size with no region, or not `available`, is
  no offer: nothing guesses a region for it. `reliability` is 1.0 and `datacenter` true for every offer: the
  service publishes no such figure, and every Droplet is in its own datacenter.
- **The ssh key is named when the Droplet is made** (`ssh_keys`, by fingerprint): the API has no call that adds a
  key to a Droplet afterwards. So `create` takes the rig's public key (`KEY_AT_CREATE`), asks first that the
  account holds it - the person adds it once in the control panel; the token then needs only `ssh_key:read` - and
  `attach_ssh` asks nothing of the service.
- **The start script travels as user data**, a script cloud-init runs as root on first boot, with the variables a
  container service would put in the environment exported at its top.
- **There is no watchdog that gives the machine back.** DigitalOcean puts no key of its own on a Droplet, and
  flotilla puts none of the person's there, so nothing on the machine can destroy it; powering it off would only
  hide a machine that still costs money. The start script keeps the heartbeat file and writes to its log when the
  heartbeat is older than the limit. Only the reaper, or the person, gives a Droplet back.
- **The credit is the balance, negated.** `GET /v2/customers/my/balance` answers what the account owes
  (`month_to_date_balance`: the last invoice's balance plus this month's usage), so its negation falls as money is
  spent, as a credit does. Whether a promotional credit - an AMD Developer Cloud grant among them - is counted in
  that figure the specification does not say. It needs `billing:read`; a token without it shows no credit.

The key goes only into the `Authorization` header; the base URL is a constant; a page's `next` link is never
followed, since it is an address the answer chose.
"""

import base64
import hashlib
import json
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request

NAME = "digitalocean"
BASE = "https://api.digitalocean.com"
PAGE = 200            # the largest page the API gives
MAX_PAGES = 50
TIMEOUT = 30
CREDIT_TIMEOUT = 10   # read under the reaper's lock, which interactive commands wait on: never a listing's wait
#: `create` needs the rig's public key: this service embeds ssh keys only while it makes the machine.
KEY_AT_CREATE = True
#: What the person is told when they turn the rig on with this service.
NOTES = (
    "digitalocean puts no key of its own on a Droplet, so no watchdog on the machine can give it back: only the "
    "reaper on this machine destroys it, and a Droplet that is powered off is billed until it is destroyed",
    "a Droplet runs a DigitalOcean image, not a container image: the project names one in `[rig] image` (an image "
    "slug or id) and the person allows it with `flotilla rig allow-image`",
    "the rig's public ssh key must be among the account's SSH keys before a machine is made; the token needs the "
    "scopes droplet:read, droplet:create, droplet:delete, sizes:read, regions:read, image:read, ssh_key:read, "
    "tag:read, tag:create, and billing:read to show the account's balance",
)
#: The service publishes no reliability figure for a size; every offer is its own datacenter's.
RELIABILITY = 1.0
USER_DATA_LIMIT = 64 * 1024   # "may not exceed 64 KiB in size"


class AdapterError(Exception):
    def __init__(self, text, status=None, code=""):
        super().__init__(text)
        self.status = status
        self.code = code   # the service's own word for the error: `not_found`, `unauthorized`, ...


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
_SLUG = r"[a-z0-9][a-z0-9.-]{0,62}"
_REGION = r"[a-z][a-z0-9]{1,11}"
_OFFER = re.compile(rf"({_SLUG})@({_REGION})", re.ASCII)
_IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", re.ASCII)
_TAG = re.compile(r"[A-Za-z0-9:_-]{1,255}", re.ASCII)   # what a tag may hold
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}", re.ASCII)
_IPV4 = re.compile(r"(?:[0-9]{1,3}\.){3}[0-9]{1,3}", re.ASCII)
_MONEY = re.compile(r"-?[0-9]{1,7}(?:\.[0-9]{1,6})?", re.ASCII)
_PUBLIC = re.compile(r"(?:ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) ([A-Za-z0-9+/]{16,4000}={0,2})"
                     r"(?: [ -~]{0,200})?", re.ASCII)
_GPU_NAME = re.compile(r"[A-Za-z0-9 _.-]{1,40}")
LABEL_PREFIX = "flotilla:"
#: The service's words for a Droplet's state, in the rig's: `off` and `archive` still cost money and run nothing,
#: so they read as `stopped`, which the reaper drains at once.
_STATUS = {"new": "loading", "active": "running", "off": "stopped", "archive": "stopped"}


def _call(key, method, path, query=None, body=None, timeout=TIMEOUT):
    url = BASE + path + (f"?{urllib.parse.urlencode(query)}" if query else "")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json",
               "User-Agent": "flotilla-rig"}
    try:
        payload = json.dumps(body).encode() if body is not None else None
        status, raw = SEND(method, url, headers, payload, timeout)
    except Exception as err:   # noqa: BLE001 - OSError, http.client's IncompleteRead, anything: a failure, said once
        raise AdapterError(f"digitalocean {method} {path}: {type(err).__name__}") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = None
    if not 200 <= status < 300:
        code, said = (data.get("id"), data.get("message")) if isinstance(data, dict) else ("", "")
        code, said = code if isinstance(code, str) else "", said if isinstance(said, str) else ""
        text = f"{code}: {said}" if code and said else code or said
        raise AdapterError(f"digitalocean {method} {path} answered {status}: {text.replace(key, '[masked]')[:200]}",
                           status, code)
    if data is None:
        if method == "DELETE" and not raw.strip():   # "a 204 status code with no body"
            return {}
        raise AdapterError(f"digitalocean {method} {path} answered an unparsable body", status)
    return data


def _pages(key, path, field, query=None):
    """Every row of a listing, page by page. The page number is counted here: a `next` link is an address the
    answer chose, and following it would carry the key there."""
    for number in range(1, MAX_PAGES + 1):
        page = _call(key, "GET", path, {**(query or {}), "page": number, "per_page": PAGE})
        rows = page.get(field) if isinstance(page, dict) else None
        if not isinstance(rows, list):
            raise AdapterError(f"digitalocean's listing {path} has no list of {field}")
        yield from rows
        links = page.get("links")
        pages = links.get("pages") if isinstance(links, dict) else None
        if not isinstance(pages, dict) or not pages.get("next"):
            return
    raise AdapterError(f"digitalocean's listing {path} did not end after {MAX_PAGES} pages")


def _number(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _address(row):
    networks = row.get("networks")
    for entry in (networks.get("v4") if isinstance(networks, dict) else None) or []:
        if isinstance(entry, dict) and entry.get("type") == "public" and isinstance(entry.get("ip_address"), str) \
                and _IPV4.fullmatch(entry["ip_address"]):
            return f"{entry['ip_address']}:22"
    return ""


def instances(key):
    found = {}
    for query in (None, {"type": "gpus"}):   # GPU Droplets are left out of the listing unless asked for by type
        for row in _pages(key, "/v2/droplets", "droplets", query):
            number = row.get("id") if isinstance(row, dict) else None
            if not isinstance(number, int) or isinstance(number, bool):
                continue
            tags = row.get("tags")
            labels = sorted(tag for tag in tags if isinstance(tag, str) and tag.startswith(LABEL_PREFIX)) \
                if isinstance(tags, list) else []
            size, status = row.get("size"), row.get("status")
            status = status if isinstance(status, str) else ""
            found[str(number)] = {"instance": str(number), "label": labels[0] if labels else "",
                                  "status": _STATUS.get(status, status),
                                  "hourly": _number(size.get("price_hourly")) if isinstance(size, dict) else None,
                                  "address": _address(row)}
    return list(found.values())


def credit(key):
    answer = _call(key, "GET", "/v2/customers/my/balance", timeout=CREDIT_TIMEOUT)
    owed = answer.get("month_to_date_balance") if isinstance(answer, dict) else None
    if not isinstance(owed, str) or not _MONEY.fullmatch(owed):   # the API gives money as text
        raise AdapterError("digitalocean's balance answer has no month_to_date_balance")
    return 0.0 - float(owed)


def destroy(key, instance):
    if not isinstance(instance, str) or not _ID.fullmatch(instance):
        raise AdapterError(f"not a digitalocean droplet id: {instance!r}")
    try:
        _call(key, "DELETE", f"/v2/droplets/{instance}")
    except AdapterError as err:
        if err.status == 404 and err.code == "not_found":
            return   # already gone: what was asked for holds, and the reaper still checks the listing
        raise


def _plain(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _is(wanted, model, slug):
    """Whether a GPU the project names is this size's. Names are compared by their letters and digits alone: the
    name within the service's model or the size's slug (`MI300X` in `gpu-mi300x1-192gb`), or every word of the
    service's model that carries a digit - its designation, not its maker - within the name (`mi300x` of
    `amd_mi300x` in `AMD Instinct MI300X`)."""
    name = _plain(wanted)
    if not name:
        return False
    if name in _plain(model) or name in _plain(slug):
        return True
    words = [word for word in re.split(r"[^a-z0-9]+", model.lower()) if re.search(r"[0-9]", word)]
    return bool(words) and all(word in name for word in words)


def offers(key, want):
    """GPU sizes within the person's price in the regions that have them, cheapest first; filtered here, so an
    answer outside the asked filter never reaches flotilla. A size the service lists in no region is no offer."""
    top = float(want["max_hourly"])
    floor = float(want.get("min_reliability", 0.98))
    disk = float(want.get("disk_gb", 30))
    gpus = [name for name in want.get("gpus") or [] if isinstance(name, str) and _GPU_NAME.fullmatch(name)]
    found = []
    for row in _pages(key, "/v2/sizes", "sizes", {"gpu_minimum": 1}):
        if not isinstance(row, dict) or not isinstance(row.get("slug"), str) or row.get("available") is not True:
            continue
        slug, gpu, price, room = row["slug"], row.get("gpu_info"), _number(row.get("price_hourly")), \
            _number(row.get("disk"))
        count = gpu.get("count") if isinstance(gpu, dict) else None
        if not isinstance(count, int) or isinstance(count, bool) or count < 1 or not re.fullmatch(_SLUG, slug):
            continue
        if price is None or not 0 < price <= top or room is None or room < disk or RELIABILITY < floor:
            continue
        model = gpu.get("model") if isinstance(gpu.get("model"), str) else ""
        if gpus and not any(_is(name, model, slug) for name in gpus):
            continue
        shown = re.sub(r"[^A-Za-z0-9 _.-]", "", model or slug)[:34] + (f" x{count}" if count > 1 else "")
        regions = row.get("regions")
        for region in sorted({name for name in regions if isinstance(name, str)}) if isinstance(regions, list) else []:
            if re.fullmatch(_REGION, region):
                found.append({"offer": f"{slug}@{region}", "gpu": shown, "hourly": price,
                              "reliability": RELIABILITY, "datacenter": True})
    return sorted(found, key=lambda item: (item["hourly"], item["offer"]))[:max(int(want.get("limit", 64)), 0)]


def fingerprint(public_key):
    """The MD5 fingerprint the service names an ssh key by, or "" when the text is not a public key."""
    found = _PUBLIC.fullmatch(public_key.strip()) if isinstance(public_key, str) else None
    if found is None:
        return ""
    try:
        blob = base64.b64decode(found.group(1), validate=True)
    except ValueError:
        return ""
    digest = hashlib.md5(blob, usedforsecurity=False).hexdigest()   # the service's name for the key, not a guard
    return ":".join(digest[at:at + 2] for at in range(0, 32, 2))


def _user_data(env, onstart):
    lines = ["#!/bin/bash"]
    for name, value in dict(env).items():
        if not isinstance(name, str) or not _ENV.fullmatch(name) or not isinstance(value, str):
            raise AdapterError(f"not a variable a start script can carry: {name!r}")
        lines.append(f"export {name}={shlex.quote(value)}")
    text = "\n".join(lines) + "\n" + (onstart if isinstance(onstart, str) else "")
    if len(text.encode("utf-8")) > USER_DATA_LIMIT:
        raise AdapterError("the start script is over the 64 KiB a Droplet's user data may hold")
    return text


def create(key, offer, *, image, disk_gb, env, onstart, label, public_key=""):
    """Make one Droplet of the offer's size in the offer's region. `disk_gb` is not sent: a size's disk is fixed,
    and `offers` already left out the sizes with less. Everything is checked before the one request that spends."""
    found = _OFFER.fullmatch(offer) if isinstance(offer, str) else None
    if found is None:
        raise AdapterError(f"not a digitalocean offer (<size>@<region>): {offer!r}")
    if not isinstance(image, str) or not _IMAGE.fullmatch(image):
        raise AdapterError(f"not a digitalocean image slug or id: {image!r} - a Droplet runs a DigitalOcean image, "
                           "not a container image; the project names one in `[rig] image`")
    if not isinstance(label, str) or not _TAG.fullmatch(label):
        raise AdapterError(f"not a label a digitalocean tag can hold: {label!r}")
    named = fingerprint(public_key)
    if not named:
        raise AdapterError("digitalocean puts an ssh key on a Droplet only while it makes it: create needs the rig's "
                           "public key, and none was given")
    script = _user_data(env, onstart)
    try:
        _call(key, "GET", f"/v2/account/keys/{named}")
    except AdapterError as err:
        if err.status == 404:
            raise AdapterError(f"the rig's ssh key ({named}) is not among the account's SSH keys, so a Droplet made "
                               "now could not be reached: the person adds the rig's public key to the account once "
                               "(control panel, Settings, Security); nothing was made", 404, err.code) from None
        raise
    body = {"name": re.sub(r"[^A-Za-z0-9.-]", "-", label).strip("-.")[:63] or "flotilla",
            "region": found.group(2), "size": found.group(1), "image": int(image) if _ID.fullmatch(image) else image,
            "ssh_keys": [named], "tags": [label], "user_data": script, "backups": False, "ipv6": False,
            "monitoring": False}
    answer = _call(key, "POST", "/v2/droplets", body=body)
    droplet = answer.get("droplet") if isinstance(answer, dict) else None
    number = droplet.get("id") if isinstance(droplet, dict) else None
    if not isinstance(number, int) or isinstance(number, bool):
        raise AdapterError(f"digitalocean did not say which Droplet offer {offer} became")
    return str(number)


def attach_ssh(key, instance, public_key):
    """Nothing is asked of the service: it has no call that puts a key on a Droplet that exists, and `create`
    already named the key (`KEY_AT_CREATE`). The id is still checked, as every call's is."""
    if not isinstance(instance, str) or not _ID.fullmatch(instance):
        raise AdapterError(f"not a digitalocean droplet id: {instance!r}")


def onstart():
    """The start script: a heartbeat file, and a loop that says in its log when the heartbeat is older than
    FLOTILLA_WATCHDOG_MINUTES (5-45, default 45) - or missing. It cannot give the machine back: the service puts no
    key on a Droplet, and powering off does not end its cost. Only the reaper does (rig design, section 5)."""
    return """mkdir -p /work
touch /root/flotilla-heartbeat
cat > /root/flotilla-watchdog.sh <<'WATCHDOG'
limit=${FLOTILLA_WATCHDOG_MINUTES:-45}
case "$limit" in ''|*[!0-9]*) limit=45;; esac
[ "$limit" -lt 5 ] && limit=5
[ "$limit" -gt 45 ] && limit=45
said=0
while sleep 60; do
  age=$(( $(date +%s) - $(stat -c %Y /root/flotilla-heartbeat 2>/dev/null || echo 0) ))
  if [ "$age" -gt $(( limit * 60 )) ]; then
    [ "$said" = 1 ] || echo "flotilla watchdog: heartbeat $age s old at $(date -u +%FT%TZ); this machine cannot give itself back - it is billed, powered off too, until the reaper or the person destroys it"
    said=1
  else
    said=0
  fi
done
WATCHDOG
nohup sh /root/flotilla-watchdog.sh > /root/flotilla-watchdog.log 2>&1 &
"""
