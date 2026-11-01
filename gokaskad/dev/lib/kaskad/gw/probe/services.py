"""The check of a service of a group through an exit (docs/k3/SPEC-stage1-exits §3.2, §5.4; stage 1, T6).

``safe_get`` with ``max_mb = 0,25``; good when the code is one of ``expect`` and the request to the service (its hops,
without the resolver through the exit) took ≤ ``max_ms``.
Ready ones: YouTube and Google ``generate_204`` (204), Cloudflare ``cp.cloudflare.com/generate_204`` (204).
The result is a row of ``metrics.db.svc``: {"svc", "ok", "code", "ms"} (+ ``why`` of a failure, ``host``) and
``bytes`` — every byte received through the exit for the check, DoH too (the traffic of the probes, §5.6, §6).
"""

from . import fetch
from .speed import failed

MAX_MB = 0.25
READY = {
    "yt": {"id": "yt", "name": "YouTube", "url": "https://www.youtube.com/generate_204", "expect": [204],
           "max_ms": 3000, "every_min": 30},
    "google": {"id": "google", "name": "Google", "url": "https://www.google.com/generate_204", "expect": [204],
               "max_ms": 3000, "every_min": 30},
    "cf": {"id": "cf", "name": "Cloudflare", "url": "https://cp.cloudflare.com/generate_204", "expect": [204],
           "max_ms": 3000, "every_min": 30},
}


def limits_of(svc):
    max_ms = svc.get("max_ms") or 3000
    return fetch.limits(max_mb=MAX_MB, max_s=min(max(max_ms / 1000.0 * 2, 3.0), 15.0))


def worst_s(svc):
    """The longest a check of ``svc`` may take (the deadline of its request)."""
    return fetch.total_s(limits_of(svc))


def check(via, svc, guard=None, deadline=None):
    """One check of ``svc`` ({"id", "url", "expect", "max_ms"}) through ``via``. Never raises."""
    max_ms = svc.get("max_ms") or 3000
    expect = [int(c) for c in (svc.get("expect") or [200, 204]) if isinstance(c, int)]
    out = {"svc": svc.get("id"), "ok": False, "code": None, "ms": None, "bytes": 0, "host": fetch.shown(svc.get("url"))}
    try:
        r = fetch.safe_get(svc.get("url"), via, limits_of(svc), guard, deadline=deadline)
    except Exception as e:                              # noqa: BLE001 — one check never breaks the run
        out = failed(out, e)
        out["bytes"] = out.pop("rx", 0)
        return out
    out.update(code=r["status"], ms=r["ms"], host=r["shown"], bytes=r["rx"])
    if r["status"] not in expect:
        out["why"] = "status"
    elif r["ms"] > max_ms:
        out["why"] = "slow"
    else:
        out["ok"] = True
    return out
