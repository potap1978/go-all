"""The country of the node, kept (the coordinator 19.10; RS §2 «узел в России», SS §2.3, NS-S5):
``state.json → gateway.hostmon.geo`` = ``{"country": "RU" | <2 letters> | null, "ru": bool, "src": "exits_geo" | "rtt",
"ts": <the moment it was found>, "mid": <machine-id>}``.

* **Who writes.** The collector — by the geo pass of the exits (``direct_loc``, the country the node itself is seen
  from, ``src: exits_geo``; ``gw.collect.exits_tick``, under the lock of the state it already holds); Cloudflare's
  ``XX`` («the country is unknown») is no country and is never kept (REV-ns-join П-4). The check of the server — when
  it found the country by the delays (``channel.ru_by_rtt``, ``src: rtt``: Russia or not, no letters) and nothing
  better is kept: ``remember_rtt`` writes the answer into its own small file ``bench/geo-rtt.json`` (no lock of
  ``state.json`` from the unit with ``CPUWeight=1`` and ``CPUSchedulingPolicy=idle`` — SEC-ns-join П-3), the collector
  carries it under its lock (``take_rtt``) at its next round. The setting ``gateway.hostmon.bench.ru`` («yes» | «no»)
  is never copied here: it is read as it is (``src: setting``).
* **Who reads** (``country``): the channel of the check (B3: the targets of the download, the upload of a node in
  Russia), the price of the schedule (B13, ``panel/host.py``) and with it C2, the planner of the repeat (B11: a link
  the repeat cannot measure again is not repeated). Order: ``exits_geo`` (≤ ``GEO_KEEP_S``, 90 d — SEC-ns-join П-1)
  -> the setting -> ``rtt`` that says Russia (≤ ``RTT_KEEP_S``, 30 d; a kept «abroad» is never read — it could only
  widen the upload of a node in Russia, SEC-ns-join З-2) -> unknown (the schedule and the repeat take it as Russia).
* **Another machine.** The record belongs to the machine (``mid``): the restore of a copy never takes the record of
  the copy — the record of this machine in the state as it is stays, or none (``restore``; SEC-ns-join З-1: the same
  machine-id of two servers of one template), a record of another machine-id (a cloned disk) is not read.
* **Checked at every read**: the letters ``[A-Z]{2}`` (not ``XX``), ``ru`` a bool equal to «country is RU» when there
  are letters, ``src`` of the list, ``ts`` an int not later than now + 1 day. Anything else — no record.
"""

import json
import os
import re
import time

SRC = ("exits_geo", "setting", "rtt")
STORED = ("exits_geo", "rtt")
RTT_KEEP_S = 30 * 86400             # the delays may move with the routes: a month
GEO_KEEP_S = 90 * 86400             # the geo pass of the exits (SEC-ns-join П-1): then the setting and the delays
NOT_A_COUNTRY = ("XX",)             # Cloudflare cdn-cgi/trace: «unknown» (T1 — Tor — is not two letters)
RTT_FILE_MAX = 4096
REFRESH_S = 6 * 3600                # the same country again: written at most this often (no state.json a pass)
TIMEOUT = 2.0                       # the lock of the state for the check of the server (as its marks)
_CC = re.compile(r"[A-Z]{2}")
_HERE = object()


def _hm(st):
    g = st.get("gateway") if isinstance(st, dict) else None
    hm = g.get("hostmon") if isinstance(g, dict) else None
    return hm if isinstance(hm, dict) else None


def _here(here):
    if here is _HERE:
        from . import machine
        return machine.mid()
    return here


def stored(st, here=_HERE, now=None):
    """The record of ``gateway.hostmon.geo`` checked (see the head) -> {"country", "ru", "src", "ts"} | None."""
    hm = _hm(st)
    g = hm.get("geo") if hm is not None else None
    if not isinstance(g, dict):
        return None
    src, ts, ru, cc = g.get("src"), g.get("ts"), g.get("ru"), g.get("country")
    if src not in STORED or type(ts) is not int or ts <= 0 or type(ru) is not bool:
        return None
    if cc is not None and not (is_country(cc) and (cc == "RU") == ru):
        return None
    if src == "exits_geo" and cc is None:
        return None
    now = int(time.time() if now is None else now)
    if ts > now + 86400:
        return None                             # the clock ran ahead once: never «known» for ever
    here = _here(here)
    mid = g.get("mid")
    if here is not None:
        from . import machine
        if not (isinstance(mid, str) and machine.same(mid, here)):
            return None
    return {"country": cc, "ru": ru, "src": src, "ts": ts}


def is_country(cc):
    """Two capital letters of a country — not Cloudflare's «unknown» (REV-ns-join П-4)."""
    return isinstance(cc, str) and bool(_CC.fullmatch(cc)) and cc not in NOT_A_COUNTRY


def _fresh(rec, now):
    keep = GEO_KEEP_S if rec["src"] == "exits_geo" else RTT_KEEP_S
    return now - rec["ts"] <= keep


def _setting(st):
    hm = _hm(st)
    b = hm.get("bench") if hm is not None else None
    v = b.get("ru") if isinstance(b, dict) else None
    return v if v in ("yes", "no") else None


def country(st, here=_HERE, now=None):
    """The country as the product knows it WITHOUT the network -> {"ru": True | False | None, "country": letters |
    None, "src": "exits_geo" | "setting" | "rtt" | None, "ts": int | None}."""
    now = int(time.time() if now is None else now)
    rec = stored(st, here, now)
    if rec is not None and rec["src"] == "exits_geo" and _fresh(rec, now):
        return rec
    v = _setting(st)
    if v is not None:
        return {"ru": v == "yes", "country": "RU" if v == "yes" else None, "src": "setting", "ts": None}
    if rec is not None and rec["src"] == "rtt" and rec["ru"] is True and _fresh(rec, now):
        return rec                              # SEC-ns-join З-2: the delays kept only tighten (Russia)
    return {"ru": None, "country": None, "src": None, "ts": None}


def put(st, country_code, src, now, here=_HERE, ru=None):
    """Write the record into ``st`` (the caller holds the lock of the state and saves it). ``src: exits_geo`` — the
    letters (``ru`` follows them; ``XX`` — nothing); ``src: rtt`` — ``ru`` (True | False), no letters, and never
    over a fresh ``exits_geo`` of this machine. The same answer again within ``REFRESH_S`` — nothing. -> True when
    ``st`` changed."""
    if src not in STORED:
        raise ValueError("src")
    now = int(now)
    if src == "exits_geo":
        if not (isinstance(country_code, str) and _CC.fullmatch(country_code.upper())):
            raise ValueError("country")
        cc = country_code.upper()
        if not is_country(cc):
            return False                        # «unknown» is no country (REV-ns-join П-4)
        ru = cc == "RU"
    else:
        if type(ru) is not bool:
            raise ValueError("ru")
        cc = None
    here = _here(here)
    old = stored(st, here, now)
    if old is not None:
        if src == "rtt" and old["src"] == "exits_geo" and _fresh(old, now):
            return False
        if (old["src"], old["country"], old["ru"]) == (src, cc, ru) and 0 <= now - old["ts"] < REFRESH_S:
            return False
    g = st.setdefault("gateway", {})
    hm = g.get("hostmon")
    if not isinstance(hm, dict):
        hm = g["hostmon"] = {}
    hm["geo"] = {"country": cc, "ru": ru, "src": src, "ts": now, "mid": here}
    return True


def rtt_path():
    from . import run
    return os.path.join(run.bench_dir(), "geo-rtt.json")


def remember_rtt(ru, now=None, here=_HERE):
    """The check of the server found the country by the delays: keep it (``src: rtt``) unless something better is
    there. No lock of ``state.json`` (SEC-ns-join П-3: the unit has ``CPUWeight=1``, ``CPUSchedulingPolicy=idle`` — a
    lock held there may be held long): the answer goes to ``rtt_path()`` (atomic, 0600, in the root's 0700 directory of
    the checks), the collector carries it under its own lock (``take_rtt``). Never raises (a line of the journal): the
    record is an extra, the check goes on. -> True written."""
    from .. import state, util
    if type(ru) is not bool:
        return False
    now = int(time.time() if now is None else now)
    try:
        here = _here(here)
        if not put(state.load(), None, "rtt", now, here=here, ru=ru):
            return False                        # the same answer is kept, or the geo pass of the exits is
        util.atomic_write(rtt_path(), json.dumps({"ru": ru, "ts": now, "mid": here}, sort_keys=True), 0o600)
        return True
    except Exception as e:                       # noqa: BLE001 — never a stop of the check
        try:
            util.log_tr("bench_run.log.geo_not_kept", why=type(e).__name__)
        except Exception:                        # noqa: BLE001
            pass
        return False


def take_rtt(st, now, here=_HERE):
    """The collector, under the lock of the state it holds: the answer ``remember_rtt`` left -> ``put`` into ``st``
    (the caller saves). The file is taken by ``rename`` first (a newer answer written meanwhile is never removed
    unread), read without following a link, ≤ 4 KiB, checked: ``ru`` a bool, ``ts`` an int within ``RTT_KEEP_S`` and
    not later than now + 1 day, ``mid`` of this machine. Anything else — dropped. -> True when ``st`` changed."""
    p = rtt_path()
    t = p + ".take"
    try:
        os.rename(p, t)
    except OSError:
        return False                            # none (the usual case: one rename a round)
    try:
        fd = os.open(t, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as f:
            raw = f.read(RTT_FILE_MAX + 1)
    except OSError:
        return False
    finally:
        try:
            os.unlink(t)
        except OSError:
            pass
    if len(raw) > RTT_FILE_MAX:
        return False
    try:
        doc = json.loads(raw.decode("utf-8"))
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    ru, ts, mid = doc.get("ru"), doc.get("ts"), doc.get("mid")
    now = int(now)
    if type(ru) is not bool or type(ts) is not int or not now - RTT_KEEP_S <= ts <= now + 86400:
        return False
    here = _here(here)
    if here is not None:
        from . import machine
        if not (isinstance(mid, str) and machine.same(mid, here)):
            return False
    return put(st, None, "rtt", ts, here=here, ru=ru)


def restore(g_file, st_now, here=_HERE, now=None):
    """The restore of a copy (``backup``, under the lock of the state): ``hostmon.geo`` of the copy never comes (SEC-
    ns-join З-1 — two servers of one template may share the machine-id; the country of another server would choose the
    targets of the channel, the upload and the price here); the record of THIS machine in the state as it is now stays
    (``st_now``), or none — the collector finds it again by the next geo pass of the exits, the check by its delays.
    -> True when the copy had a record."""
    hm = g_file.get("hostmon") if isinstance(g_file, dict) else None
    had = isinstance(hm, dict) and "geo" in hm
    if had:
        hm.pop("geo", None)
    if not isinstance(g_file, dict) or stored(st_now, here, now) is None:
        return had
    if not isinstance(hm, dict):
        hm = g_file["hostmon"] = {}
    hm["geo"] = dict(_hm(st_now)["geo"])
    return had
