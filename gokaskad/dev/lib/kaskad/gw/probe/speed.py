"""The speed test of one exit (docs/k3/SPEC-stage1-exits §5.3; stage 1, T6).

The target by default — ``https://speed.cloudflare.com/__down?bytes=<max>``; a group may set its own ``https://`` link
(``checks.speed.url``, checked as any link of §5.4). The download goes through ``fetch.safe_get`` (the name through the
exit, the checked IP, the limits) and stops at ``max_mb`` or ``max_s`` with a reset. Metrics: ``ttfb``, ``tls``, the
speed = the bytes after one second of warm-up / the time of that window. The time of the connection through SOCKS is not
measured (xray says «success» before it connects).

The result is a row of ``metrics.db.speed`` (§3.5): {"mbps", "ttfb_ms", "tls_ms", "bytes", "host", "ok", "why"} and
``rx`` — every byte received through the exit for the test (DoH too: the traffic of the probes, §5.6, §6), ``why_args``
— the details of a failure for its text.
"""

from . import fetch

DEFAULT = "default"
DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes=%d"


def target(url, max_bytes):
    """The URL of the test: the group's own link, or Cloudflare's for ``max_bytes`` (the cap of one test)."""
    if isinstance(url, str) and url.startswith("https://"):
        return url
    return DEFAULT_URL % int(max_bytes)


def worst_s(max_mb, max_s):
    """The longest a test of these limits may take (the deadline of its request)."""
    return fetch.total_s(fetch.limits(max_mb=max_mb, max_s=max_s))


def failed(out, e):
    """``out`` of a failure: the code and its details of a FetchError, the bytes received; anything else — ``error``."""
    if isinstance(e, fetch.FetchError):
        out["why"] = e.code
        if e.args_:
            out["why_args"] = dict(e.args_)
        out["rx"] = int(getattr(e, "rx", 0) or 0)
    else:
        out["why"] = "error"
        out["why_args"] = {"what": type(e).__name__}
    return out


def run(via, url, max_mb, max_s, guard=None, deadline=None):
    """One test through ``via``. Never raises: a failure is ``ok: False`` with ``why`` (a code of ``gw_probe.why``)."""
    lim = fetch.limits(max_mb=max_mb, max_s=max_s)
    u = target(url, lim["max_bytes"])
    out = {"mbps": None, "ttfb_ms": None, "tls_ms": None, "bytes": 0, "rx": 0, "host": fetch.shown(u), "ok": False,
           "why": None}
    try:
        r = fetch.safe_get(u, via, lim, guard, deadline=deadline)
    except Exception as e:                              # noqa: BLE001 — one test never breaks the run
        return failed(out, e)
    out.update(host=r["shown"], bytes=r["bytes"], rx=r["rx"], ttfb_ms=r["ttfb_ms"], tls_ms=r["tls_ms"], mbps=r["mbps"])
    if r["status"] != 200:
        out["why"] = "status"
        out["code"] = r["status"]
    elif not r["bytes"] or r["mbps"] is None:
        out["why"] = "empty"
    else:
        out["ok"] = True
    return out
