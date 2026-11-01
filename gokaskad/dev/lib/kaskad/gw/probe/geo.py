"""The country of the IP of an exit (docs/k3/SPEC-stage1-exits §4.3 «Страна», §5.5; stage 1, T6).

``GET https://www.cloudflare.com/cdn-cgi/trace`` through the exit -> ``ip``, ``loc``. ``geo.enabled = false`` of a group
— no request to cloudflare.com through its exits at all (neither planned nor extra; ``run`` never calls ``trace`` then).

The signs of a broken measure after the extra pass (``suspect``): the change at **every** exit of the pass (≥ 2), or the
country of the exit = the country of the node's direct path, unless that is the exit's expected country. Nothing is taken
out then; the collector pushes ``exit_country_changed(all_exits=true)`` once a day.
"""

import re

from . import fetch
from .speed import failed

TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
_LOC = re.compile(r"^[A-Z]{2}$")


def _limits():
    return fetch.limits(max_mb=0.064, max_s=10)


WORST_S = fetch.total_s(_limits())       # the longest one reading may take (the deadline of its request)


def parse(text):
    """{"ip", "loc"} of the body of ``cdn-cgi/trace`` (``key=value`` lines); a missing or odd value — None."""
    out = {"ip": None, "loc": None}
    for line in (text or "").splitlines():
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k == "ip" and v and len(v) <= 45:
            out["ip"] = v
        elif k == "loc" and _LOC.match(v.upper()):
            out["loc"] = v.upper()
    return out


def trace(via, guard=None, deadline=None):
    """{"ip", "loc", "ok", "bytes", "why"?} through ``via`` (``bytes`` — received through the exit, DoH too).
    Never raises."""
    try:
        r = fetch.safe_get(TRACE_URL, via, _limits(), guard, keep=4096, deadline=deadline)
    except Exception as e:                              # noqa: BLE001 — one reading never breaks the run
        out = failed({"ip": None, "loc": None, "ok": False}, e)
        out["bytes"] = out.pop("rx", 0)
        return out
    if r["status"] != 200:
        return {"ip": None, "loc": None, "ok": False, "why": "status", "code": r["status"], "bytes": r["rx"]}
    got = parse(r["body"].decode("utf-8", "replace"))
    got["bytes"] = r["rx"]
    got["ok"] = bool(got["loc"])
    if not got["ok"]:
        got["why"] = "empty"
    return got


def expected_country(checks, pref):
    """The expected country (§5.5): ``checks.geo.expect`` of the group, else the first seen (``exit_pref.country``)."""
    exp = ((checks or {}).get("geo") or {}).get("expect")
    if isinstance(exp, str) and len(exp) == 2:
        return exp.upper()
    c = (pref or {}).get("country") or {}
    e = c.get("expected") if isinstance(c, dict) else None
    return e.upper() if isinstance(e, str) and len(e) == 2 else None


def suspect(eid, loc, expected, locs, direct_loc, role=None):
    """The sign of a broken measure of the extra pass, or None (§4.3 п. 2) — one function for the unit (``run``) and
    the collector (``health._geo``):

    * ``all_exits`` — ≥ 2 exits answered and each one's country differs from its expected one; an exit without an
      expected country counts as changed (``l != expected.get(e)`` — None never equals a country);
    * ``node_loc`` — the exit's country (other than its expected one) is the country of the node's direct path; not for
      a RUSSIAN exit (``role`` "ru") of a node that is itself in RU (SPEC-groups §6.2): a home router in Russia shows the
      node's country by its nature — the owner's ``geo.expect`` other than RU is then told as a real change.

    ``locs`` {exit: loc} of the pass, ``expected`` {exit: expected country or None}, ``direct_loc`` — the node's own."""
    seen = dict((e, l) for e, l in (locs or {}).items() if l)
    changed = [e for e, l in seen.items() if l != (expected or {}).get(e)]
    if len(seen) >= 2 and len(changed) == len(seen):
        return "all_exits"
    if direct_loc and loc and loc == direct_loc and loc != (expected or {}).get(eid) and \
            not (role == "ru" and direct_loc == "RU"):
        return "node_loc"
    return None
