"""The queue of the checks of the node — /var/lib/kaskad/bench/spool (k3/handoff/SPEC-node-score-schedule-share.md
§3.1, §3.2, NS-S9, NS-S17).

* The runner of a check (B1) writes ``<id>.json`` when it ends, a skipped one too: ``{"v": 1, "run": {the fields of
  bench_run}, "tests": [{the fields of bench}]}`` (``put``: checked by ``rows`` first, 0600, atomic). Saving the
  tariff (C1) writes ``epoch-<ts>-tariff.json`` = ``{"v": 1, "args": {…}}`` (``put_epoch``).
* metrics.db has one writer — the collector: once a minute it carries the files into the base, a transaction a file,
  and deletes them (``gw.collect``); a broken file goes to ``spool/bad/`` with a line in the journal (``aside``).
* Without the gateway there is no collector (NS-D1): the queue is the history itself; the reader (``bench.history``)
  takes the base and the queue together. ``prune`` is the one cleaning (REV-ns-b12-2): the runner of a check calls
  it at its start and the timer of the schedule at every firing, with the gateway or without — checks older than
  400 days go, at most 3000 stay (the oldest go first), the tariff files the same age and at most 200; a broken
  file goes to bad/.
* A name is only ``fullmatch`` of ``RUN_NAME`` / ``EPOCH_NAME``: the temporary files of ``util.atomic_write``
  (``.tmp.*``), ``results.json`` and anything else are never read. A file is opened without following a link, must be
  a regular file of at most ``MAX_BYTES``.
* ``rows`` is the one check of the format, for the writer of the base and the reader of the queue alike (круг 2, С-2;
  договор с B1 19.10, SS §3.2): a required field missing or wrong, a KNOWN field of a wrong type, out of its range or
  above its size — the whole file is bad (bad/ with a line in the journal; the reader skips it with a line); an
  UNKNOWN key is not kept, the file is (a line once per key); null is NULL. Texts are codes and numbers only — no
  paths (``why_args``, ``extra``). ``put`` checks the same before writing: the runner gets ValueError, never a file
  the collector would refuse.
* What ``kaskad.score`` reads of a check besides the columns of SS §3.1 — ``bench_run.meta`` (the runner's
  ``engines``/``cpu_flags``, B1's whole inventory ``inv`` and ``series``) and ``bench.extra`` (``n``, ``lo``,
  ``points``, ``src``, ``wait``, ``catalog``, ``budget``; B1's ``dns_src``; B3's ``geo``); ``check_doc`` makes the file from the
  values in the shape of score, ``history.score_run`` gives them back.
"""

import json
import math
import os
import re
import stat
import time

from .. import paths, util

V = 1
DAY = 86400
KEEP_S = 400 * DAY              # as KEEP of bench_run in metrics.db
MAX_FILES = 3000                # check files of the queue at most (§3.2)
MAX_EPOCH_FILES = 200           # files of the tariff at most
MAX_BAD = 100                   # files kept in bad/ at most (the oldest go)
MAX_BYTES = 32 * 1024           # a file of a check: ~2 KB in life (≈ 15 tests)
MAX_TESTS = 64

RUN_NAME = re.compile(r"([0-9a-f]{10})\.json")
EPOCH_NAME = re.compile(r"epoch-([0-9]{10})-tariff\.json")
DROP_SUFFIX = ".drop"           # a file taken by the collector for its removal (``_drop``)
ID = re.compile(r"[0-9a-f]{10}")
_CODE = re.compile(r"[a-z0-9_.]{1,64}")                 # why: a code of SS §2.5 (busy, locked, killed, no_pkgs …)
_KEY = re.compile(r"[a-z_][a-z0-9_]{0,23}")             # a key of why_args / of an epoch's args
_WORD = re.compile(r"[A-Za-z0-9_.:+-]{0,64}")           # a text value of args: a code, never a path (no «/»)
_TEST = re.compile(r"[a-z0-9_]{1,32}")
_SCHED = re.compile(r"[A-Za-z0-9_-]{1,32}")
ID_KEY = re.compile(r"[A-Za-z0-9_-]{1,32}")              # a key of a table of codes (the inputs of host_people_h1)
_SCALE = re.compile(r"[0-9A-Za-z._-]{1,16}")
_CA = re.compile(r"[a-z0-9_]{1,16}")
_ENGINE = re.compile(r"[a-z0-9_]{1,16}")
_FLAG = re.compile(r"[a-z0-9_]{1,32}")                  # a flag of /proc/cpuinfo: aes, avx2 …
_SRC = re.compile(r"[a-z_]{1,16}")                      # user | catalog | inventory; the DNS: kaskad | system
_IFACE = re.compile(r"[A-Za-z0-9_.:@-]{1,32}")          # a network interface of the inventory
_VIRT = re.compile(r"[A-Za-z0-9-]{1,32}")               # systemd-detect-virt
_LOGKEY = re.compile(r"[A-Za-z0-9_.-]{1,32}")            # an unknown key named in the journal (else «?»)
MODES = ("quick", "full")
BY = ("schedule", "panel", "bot", "cli", "mcp", "ssh", "auto")
STATUS = ("ok", "partial", "skipped", "failed", "cancelled")
GOOD = ("ok", "partial")                                # a check whose values count (§4)
EPOCH_KINDS = ("cpu", "mem", "tariff", "engines", "openssl")   # openssl: B4 круг 3 (score.inv_changes)
TS_MIN, TS_MAX = 10 ** 9, 2 ** 32                       # a moment of the clock: 2001 … 2106
_INT_MAX = 2 ** 53
_REAL_MAX = 1e12

RUN_COLS = ("id", "ts", "end_ts", "mode", "by", "sched_id", "retry_of", "inv", "status", "why", "why_args", "rx",
            "tx", "dur_s", "cpu_s", "under_load", "pkgs", "score", "scale_version", "links", "meta")
TEST_COLS = ("run", "test", "ts", "value", "unit", "scale_version", "mode", "under_load", "ok", "why", "score",
             "value_corr", "people", "ca", "extra")
DROP_COLS = ("run", "link", "ts", "day", "cond", "args")
# the known fields of a file (договор с B1, координатор 19.10; SS §3.1): any other key is not kept (a line once a key);
# ``series`` — B1's «now» series -> bench_run.meta["series"]; ``run`` of a test repeats the id (ignored); ``dns_src`` of
# a test — what the DNS test asked (kaskad | system) -> bench.extra["dns_src"]
DOC_KEYS = ("v", "run", "tests", "drops", "series")
RUN_KEYS = RUN_COLS
TEST_KEYS = TEST_COLS + ("dns_src",)
DNS_SRC = ("kaskad", "system")
# bench.extra["geo"] of the channel: the country of the node was not known (B3, history only). «unknown» — a check
# before П3-б: the button measured abroad, the schedule as Russia, which one is not kept; «unknown_ru» — since П3-б
# every check with no country is measured as Russia (REV-ns-p3a П-2: the old rows are not signed «as Russia» after it)
GEO = ("unknown", "unknown_ru")
GEO_TEXT = {"unknown": "bench_run.geo.unknown", "unknown_ru": "bench_run.geo.unknown_ru"}
EXTRA_SHOWN = ("dns_src", "geo", "target", "win")   # bench.extra kept for the history only — score never reads them
_TARGET = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")   # the id of the point of test 8/8b (channel-targets.json, «allowed»)
DONE_STATUS = ("ok", "partial", "failed")   # bench_done (B7): a check that ended, not skipped or stopped
DROP_KEYS = ("run", "link", "cond", "ts", "day", "matched", "push", "auto", "now", "test", "value", "usual", "unit")
INV_KEYS = ("cpu", "nproc", "mem_gb", "engines", "openssl_ver")   # bench_run.inv: what score.inv_changes compares
# what kaskad.score reads of a check besides its values (круг 2, С-1; the code of score.py d2af76d5):
# vals["_engines"] / vals["_cpu_flags"] -> bench_run.meta; a value's fields besides v, mode, ok, why, corr, people ->
# bench.extra (``n``/``lo`` — loss, jitter, waits(); ``points`` — loss per anchor {n, k, e}; ``src`` — tests with a
# tariff; ``wait``/``catalog`` — fresh() and preliminary; ``budget`` — the channel)
META_KEYS = ("engines", "cpu_flags")
VALS_META = {"_engines": "engines", "_cpu_flags": "cpu_flags"}
INV_OF_META = {"engines": "engines", "cpu_flags": "flags"}   # the same in the inventory of B1
EXTRA_KEYS = ("n", "lo", "points", "src", "wait", "catalog", "budget")
VALUE_KEYS = ("v", "mode", "ok", "why", "corr", "people") + EXTRA_KEYS
MAX_META = 4096                 # bytes of JSON of the runner's meta (engines, cpu_flags)
MAX_INV = 4096                  # bytes of JSON of the inventory (bench_run.meta["inv"])
MAX_SERIES = 4096               # bytes of JSON of B1's series (bench_run.meta["series"])
MAX_EXTRA = 1024                # bytes of JSON of bench.extra
MAX_POINTS = 8
DROP_CONDS = ("rel",)                                    # NS-D33: rel of a link <= 50 (score.DROP_COND)
LEGACY_DROP_CONDS = (100, 300, "100", "300", "fall")     # the conditions of the removed scale 0…1000 (NS-D34)
MAX_DROPS = 16
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def spool_dir():
    return paths.bench_spool()


def bad_dir():
    return os.path.join(paths.bench_spool(), "bad")


# ── the format ─────────────────────────────────────────────────────────────

def _num(v, kind=float, lo=None, hi=None):
    """A number within the limits or None (bool, NaN, ±inf, a word, |int| above 2^53, |real| above 1e12)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        v = kind(v)
    except (TypeError, ValueError, OverflowError):
        return None
    if kind is float and not (math.isfinite(v) and -_REAL_MAX <= v <= _REAL_MAX):
        return None
    if kind is int and not -_INT_MAX <= v <= _INT_MAX:
        return None
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        return None
    return v


def _real(v, lo=None, hi=None):
    """A number of the file (int or float, not a numeric string, not bool) within the limits -> float or None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return _num(v, float, lo, hi)


def _int(v, lo=None, hi=None):
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if not isinstance(v, int) or isinstance(v, bool):
        return None
    return _num(v, int, lo, hi)


def _flag(v):
    if isinstance(v, bool):
        return int(v)
    return v if v in (0, 1) and not isinstance(v, float) else None


def _re(v, rx):
    return v if isinstance(v, str) and rx.fullmatch(v) else None


def _ts(v):
    return _int(v, TS_MIN, TS_MAX)


def _unit(v):
    if not isinstance(v, str) or not 0 < len(v) <= 16:
        return None
    return v if all(c.isprintable() and c not in "\r\n\t" for c in v) else None


def _text(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if v else None


def args(v, keys=16, deep=True):
    """``why_args`` / the args of an epoch: {key: a number, a code or (one level) such an object — B1's ``forced``},
    at most ``keys``; anything else is dropped (a path, a text with spaces, null, deeper nesting) -> dict (maybe
    empty). The cleaner for a writer; reading a file is strict (``args_strict``)."""
    out = {}
    if not isinstance(v, dict):
        return out
    for k in sorted(v, key=str):
        if len(out) >= keys:
            break
        if not isinstance(k, str) or not _KEY.fullmatch(k):
            continue
        x = v[k]
        if isinstance(x, bool):
            out[k] = int(x)
        elif isinstance(x, (int, float)):
            n = _num(x, int if isinstance(x, int) else float)
            if n is not None:
                out[k] = n
        elif isinstance(x, str) and _WORD.fullmatch(x):
            out[k] = x
        elif isinstance(x, dict) and deep:
            out[k] = args(x, keys, deep=False)
    return out


def _bad(where):
    raise ValueError(where)


_told_keys = set()


def _only(d, keys, where):
    """The договор with B1 (координатор 19.10): a key the format does not know is not stored and never makes the file
    bad — a line in the journal once per (place, key) and process. -> the dict of the known keys only."""
    out = {}
    for k, x in d.items():
        if k in keys:
            out[k] = x
            continue
        name = k if isinstance(k, str) and _LOGKEY.fullmatch(k) else "?"
        if (where, name) not in _told_keys and len(_told_keys) < 1024:
            _told_keys.add((where, name))
            util.log_action("bench spool: an unknown field %s.%s is not kept" % (where, name))
    return out


def _opt(d, key, conv, where):
    """An optional field: absent or null -> None; present -> ``conv(v)``, None from it — the file is bad."""
    v = d.get(key)
    if v is None:
        return None
    out = conv(v)
    if out is None:
        _bad("%s.%s" % (where, key))
    return out


def _bool(v):
    return v if isinstance(v, bool) else None


def _codes(rx, most):
    def conv(v):
        if not isinstance(v, (list, tuple)) or len(v) > most or not all(_re(x, rx) for x in v):
            return None
        return sorted(set(v))
    return conv


def _printable(most):
    return lambda x: x if isinstance(x, str) and 0 < len(x) <= most and x.isprintable() else None


def _obj(fields, where):
    """A nested object of known fields {key: conv}: unknown keys dropped (a line), a known one of a wrong type or out of
    its range — the file is bad; null stays null (B1 writes ``"na": null``)."""
    def conv(v):
        if not isinstance(v, dict):
            return None
        d = _only(v, fields, where)
        out = {}
        for k, c in fields.items():
            if k in d:
                out[k] = None if d[k] is None else _opt(d, k, c, where)
        return out
    return conv


def _list(conv, most):
    def f(v):
        if not isinstance(v, list) or len(v) > most:
            return None
        out = []
        for x in v:
            y = conv(x)
            if y is None:
                return None
            out.append(y)
        return out
    return f


def _nonneg(x):
    return x if _real(x, 0) is not None else None


def _finite(x):
    return x if _real(x) is not None else None


def _count(x):
    return _int(x, 0)


_DISK = {"rota": lambda x: x if _flag(x) is not None else None, "size": _count}
_OS = {"id": _printable(64), "version_id": _printable(64)}
# the inventory of test 0 (B1 bench/suite.inventory, договор 19.10): every known field is checked; ALL of it is kept in
# bench_run.meta["inv"] (≤ 4 KB); bench_run.inv keeps what score.inv_changes compares (INV_KEYS)
INV_FIELDS = {
    "cpu": _printable(128), "nproc": lambda x: _int(x, 1, 4096), "mem_gb": lambda x: _real(x, 0, 65536),
    "engines": _codes(_ENGINE, 16), "openssl_ver": _printable(64),
    "flags": _codes(_FLAG, 512), "smt": lambda x: _int(x, 1, 64), "crash_mb": lambda x: _int(x, 0, 1048576),
    "disks": _list(_obj(_DISK, "run.inv.disks"), 64),
    "nic": lambda x: x if isinstance(x, dict) and len(x) <= 32 and all(
        _re(k, _IFACE) and (y is None or _real(y, 0) is not None) for k, y in x.items()) else None,
    "virt": lambda x: _re(x, _VIRT), "kernel": _printable(64), "os": _obj(_OS, "run.inv.os")}
_ANCHOR = {"addr": _printable(64), "n": _count, "lost": _count, "rtt_med": _nonneg, "na": lambda x: _re(x, _CODE)}
# the «now» series of the light probes of the check (B1 test 1, договор 19.10) — kept in bench_run.meta["series"]
SERIES_FIELDS = {"anchors": _list(_obj(_ANCHOR, "series.anchors"), 16)}


def inv(v, where="run.inv"):
    """The inventory of test 0, checked (NS-S17; договор с B1 19.10) -> the dict of its known fields (nulls dropped) or
    None; ValueError — not an object, a known field of a wrong type or out of its range, above MAX_INV bytes."""
    if not isinstance(v, dict):
        _bad(where)
    d = _only(v, INV_FIELDS, where)
    out = {}
    for k, conv in INV_FIELDS.items():
        x = _opt(d, k, conv, where)
        if x is not None:
            out[k] = x
    if len(_text(out) or "") > MAX_INV:
        _bad("%s: above %d bytes" % (where, MAX_INV))
    return out or None


def series(v):
    """B1's series of the check (``doc["series"]``) checked -> dict or None; ValueError as ``inv``."""
    if not isinstance(v, dict):
        _bad("series")
    d = _only(v, SERIES_FIELDS, "series")
    out = dict((k, _opt(d, k, c, "series")) for k, c in SERIES_FIELDS.items() if d.get(k) is not None)
    if len(_text(out) or "") > MAX_SERIES:
        _bad("series: above %d bytes" % MAX_SERIES)
    return out or None


def meta(v):
    """``run.meta`` given by the runner (``check_doc``): ``engines`` (``vals["_engines"]``) and ``cpu_flags``
    (``vals["_cpu_flags"]``) -> dict or None; ValueError — a bad list, above MAX_META bytes of JSON."""
    if not isinstance(v, dict):
        _bad("run.meta")
    d = _only(v, META_KEYS, "run.meta")
    out = {}
    e = _opt(d, "engines", _codes(_ENGINE, 16), "run.meta")
    f = _opt(d, "cpu_flags", _codes(_FLAG, 512), "run.meta")
    if e is not None:
        out["engines"] = e
    if f is not None:
        out["cpu_flags"] = f
    if len(_text(out) or "") > MAX_META:
        _bad("run.meta: above %d bytes" % MAX_META)
    return out or None


def _point(p):
    if not isinstance(p, dict) or not p:
        return None
    d = _only(p, ("n", "k", "e"), "test.extra.points")
    return d if all(_nonneg(d[k]) is not None for k in d) else None


def _points(v):
    if not isinstance(v, list) or len(v) > MAX_POINTS:
        return None
    ps = [_point(p) for p in v]
    return None if any(p is None for p in ps) else ps


_EXTRA_CONV = {"n": _nonneg, "lo": _finite, "points": _points, "src": lambda x: _re(x, _SRC),
               "wait": _bool, "catalog": _bool, "budget": _bool, "dns_src": lambda x: x if x in DNS_SRC else None,
               "geo": lambda x: x if x in GEO else None, "target": lambda x: _re(x, _TARGET),
               "win": lambda x: x if _real(x, 0, 60) is not None else None}   # s of a short window of 8c (Н-1)


def extra(v, where="test.extra"):
    """``bench.extra``: the fields of a value ``kaskad.score`` reads besides v, mode, ok, why, corr, people (круг 2,
    С-1) — ``n`` (samples, ≥ 0), ``lo``, ``points`` (loss: [{n, k, e}] of the anchors), ``src`` (user | catalog |
    inventory), ``wait``, ``catalog``, ``budget`` — and ``dns_src`` of B1 (kaskad | system: what the DNS test asked)
    and ``geo`` of B3 (unknown: the country of the node was not known), ``target`` of test 8, ``win`` of 8c (the s of
    its short window, 0–60: the upload hit the cap early, Н-1) — history only, score does not read them; a number is
    kept as it is -> dict or None; ValueError — a bad value, above
    MAX_EXTRA bytes of JSON; an unknown key is dropped with a line."""
    if not isinstance(v, dict):
        _bad(where)
    d = _only(v, EXTRA_KEYS + EXTRA_SHOWN, where)
    out = {}
    for k in EXTRA_KEYS + EXTRA_SHOWN:
        x = _opt(d, k, _EXTRA_CONV[k], where)
        if x is not None:
            out[k] = x
    if len(_text(out) or "") > MAX_EXTRA:
        _bad("%s: above %d bytes" % (where, MAX_EXTRA))
    return out or None


def args_strict(v, where, depth=2):
    """``why_args`` / the args of an epoch read from a file (договор с B1 19.10): {key: a number, a code, true/false,
    null or — two levels at most — such an object (B1's ``forced``)} at most 16 a level -> the same dict (nulls kept:
    «not known»); ValueError — a bad key, a text that is not a code (a path, spaces), deeper nesting, a list."""
    if not isinstance(v, dict) or len(v) > 16:
        _bad(where)
    out = {}
    for k in sorted(v, key=str):
        x = v[k]
        if not isinstance(k, str) or not _KEY.fullmatch(k):
            _bad(where)
        if x is None or isinstance(x, bool) or (isinstance(x, str) and _WORD.fullmatch(x)):
            out[k] = x
        elif isinstance(x, int) and _num(x, int) is not None:
            out[k] = x
        elif isinstance(x, float) and _num(x, float) is not None:
            out[k] = x
        elif isinstance(x, dict) and depth > 1:
            out[k] = args_strict(x, where, depth - 1)
        else:
            _bad(where)
    return out


def _links(v):
    if not isinstance(v, dict) or len(v) > 32:
        return None
    out = {}
    for k in sorted(v):
        s = _int(v[k], 0, 1000)
        if not _re(k, _TEST) or s is None:
            return None
        out[k] = s
    return out


def _mode(v):
    return v if v in MODES else None


def rows(doc):
    """A file of the queue -> (run, [test]) — dicts by RUN_COLS / TEST_COLS, ready for the base (``inv``, ``meta``,
    ``why_args``, ``links``, ``extra`` as JSON text). Договор с B1 (координатор 19.10): every KNOWN field is checked
    strictly — a required one missing or wrong, a known one of a wrong type, out of its range or above its size, more
    than MAX_TESTS tests, a test without its id or twice: ValueError, the file is bad (bad/); an UNKNOWN key is not
    kept, a line in the journal once per key. null is NULL. ``bench_run.meta`` = {engines, cpu_flags (the runner's
    meta), inv (the whole inventory), series (B1's series)}; ``run`` of a test repeats the id — ignored."""
    if not isinstance(doc, dict) or type(doc.get("v")) is not int or doc["v"] != V:
        raise ValueError("not a check of v %d" % V)
    doc = _only(doc, DOC_KEYS, "file")
    r = doc.get("run")
    if not isinstance(r, dict):
        raise ValueError("no run")
    r = _only(r, RUN_KEYS, "run")
    run = {"id": _re(r.get("id"), ID), "ts": _ts(r.get("ts")), "mode": _mode(r.get("mode")),
           "by": r.get("by") if r.get("by") in BY else None,
           "status": r.get("status") if r.get("status") in STATUS else None}
    for k in ("id", "ts", "mode", "by", "status"):
        if run[k] is None:
            raise ValueError("run.%s" % k)
    o = lambda key, conv: _opt(r, key, conv, "run")                 # noqa: E731
    full = inv(r["inv"]) if r.get("inv") is not None else None
    m = dict(meta(r["meta"]) or {}) if r.get("meta") is not None else {}
    if full:
        m["inv"] = full
    s = series(doc["series"]) if doc.get("series") is not None else None
    if s:
        m["series"] = s
    run.update({
        "end_ts": o("end_ts", _ts), "sched_id": o("sched_id", lambda x: _re(x, _SCHED)),
        "retry_of": o("retry_of", lambda x: _re(x, ID)),
        "inv": _text(dict((k, x) for k, x in (full or {}).items() if k in INV_KEYS)),
        "why": o("why", lambda x: _re(x, _CODE)),
        "why_args": _text(args_strict(r["why_args"], "run.why_args")) if r.get("why_args") is not None else None,
        "rx": o("rx", lambda x: _int(x, 0)), "tx": o("tx", lambda x: _int(x, 0)),
        "dur_s": o("dur_s", lambda x: _real(x, 0)), "cpu_s": o("cpu_s", lambda x: _real(x, 0)),
        "under_load": o("under_load", _flag), "pkgs": o("pkgs", _flag),
        "score": o("score", lambda x: _int(x, 0, 1000)), "scale_version": o("scale_version", lambda x: _re(x, _SCALE)),
        "links": _text(o("links", _links)), "meta": _text(m)})
    tests = doc.get("tests") if doc.get("tests") is not None else []
    if not isinstance(tests, list) or len(tests) > MAX_TESTS:
        raise ValueError("tests")
    out, seen = [], set()
    for t in tests:
        tid = _re(t.get("test"), _TEST) if isinstance(t, dict) else None
        if tid is None or tid in seen:
            raise ValueError("test id")
        seen.add(tid)
        t = _only(t, TEST_KEYS, "test")
        w = "test %s" % tid
        ex = t.get("extra")
        if t.get("dns_src") is not None:             # B1: the source of the DNS next to the fields of the row
            if ex is not None and not isinstance(ex, dict) or (ex or {}).get("dns_src", t["dns_src"]) != t["dns_src"]:
                raise ValueError("%s.dns_src" % w)
            ex = dict(ex or {}, dns_src=t["dns_src"])
        o = lambda key, conv: _opt(t, key, conv, w)                  # noqa: E731
        value = o("value", _real)
        ok = o("ok", _flag)
        ul = o("under_load", _flag)
        out.append({
            "run": run["id"], "test": tid, "ts": o("ts", _ts) or run["ts"], "value": value,
            "unit": o("unit", _unit),
            "scale_version": o("scale_version", lambda x: _re(x, _SCALE)) or run["scale_version"],
            "mode": o("mode", _mode) or run["mode"],
            "under_load": ul if ul is not None else run["under_load"],
            # ok: as the runner said; unsaid — 1 with a value, NULL without one (not «failed»: score.run_vals drops a
            # value with ok False, and a value of «wait»/«catalog» must stay — the round trip of check_doc is exact)
            "ok": ok if ok is not None else (1 if value is not None else None),
            "why": o("why", lambda x: _re(x, _CODE)),
            "score": o("score", lambda x: _int(x, 0, 1000)), "value_corr": o("value_corr", lambda x: _real(x, 0)),
            "people": o("people", lambda x: _real(x, 0)), "ca": o("ca", lambda x: _re(x, _CA)),
            "extra": _text(extra(ex, w + ".extra")) if ex is not None else None})
    return run, out


def check_doc(run, vals, more=None, drops=None):
    """The file of a check from its values in the shape of ``kaskad.score`` (B1/B11 write it with ``put``; the
    reverse is ``bench.history.score_run``, so ``score.view`` over the history sees what the runner saw): ``run`` — the
    fields of bench_run (no ``meta``: it comes from ``vals``); ``vals`` — {test: number | {"v", "mode", "ok", "why",
    "corr", "people", + EXTRA_KEYS}, "_engines": [...], "_cpu_flags": [...]}; ``more`` — {test: {unit, score, ca, ts,
    scale_version, under_load}} of bench; ``drops`` — events of ``score.drop_events`` of this check. ValueError — a key
    of a value or a ``_`` key the history cannot keep (nothing is dropped silently)."""
    r = dict(run or {})
    m = {}
    tests = []
    for k in sorted(vals or {}):
        x = vals[k]
        if k.startswith("_"):
            if k not in VALS_META:
                _bad("vals.%s: no place in the history" % k[:32])
            m[VALS_META[k]] = list(x) if isinstance(x, (list, tuple)) else x
            continue
        t = {"test": k}
        if isinstance(x, dict):
            for f in x:
                if f not in VALUE_KEYS:
                    _bad("vals.%s: a field with no place in the history" % k[:32])
            t["value"] = x.get("v")
            for f, col in (("mode", "mode"), ("why", "why"), ("corr", "value_corr"), ("people", "people")):
                if x.get(f) is not None:
                    t[col] = x[f]
            if "ok" in x and x["ok"] is not None:
                t["ok"] = x["ok"]
            ex = dict((f, x[f]) for f in EXTRA_KEYS if x.get(f) is not None)
            if ex:
                t["extra"] = ex
        elif x is not None:
            t["value"] = x
        t.update((more or {}).get(k) or {})
        tests.append(t)
    if m:
        r["meta"] = m
    doc = {"v": V, "run": r, "tests": tests}
    if drops:
        doc["drops"] = [dict((f, e[f]) for f in DROP_KEYS if e.get(f) is not None) for e in drops]
    return doc


def epoch_kinds(prev, cur):
    """The events of a new base between two inventories (NS-S17; JSON text of bench_run.inv or dicts): [(kind, args)].
    The rule is ``score.inv_changes`` (B4, one source); the args here are for the line of the screen."""
    from .. import score
    prev, cur = _inv_of(prev), _inv_of(cur)
    if not prev or not cur:
        return []
    kinds = score.inv_changes(prev, cur)
    a = {"cpu": {"nproc": cur.get("nproc"), "was_nproc": prev.get("nproc")},
         "mem": {"mem_gb": cur.get("mem_gb"), "was_mem_gb": prev.get("mem_gb")},
         "engines": {"n": len(cur.get("engines") or ()), "was_n": len(prev.get("engines") or ())},
         "openssl": dict((k, x) for k, x in (("ver", cur.get("openssl_ver")), ("was", prev.get("openssl_ver")))
                         if isinstance(x, str) and _WORD.fullmatch(x))}
    return [(k, dict((x, y) for x, y in a.get(k, {}).items() if y is not None)) for k in kinds]


def known_before(conn, run, limit=500):
    """The inventory known before ``run`` in bench_run (NS-S17, REV-ns-b6 З-2): every key — from the latest good check
    where it is not null (``score.inv_known`` back along the chain, at most ``limit`` checks), or None."""
    from .. import score
    known = None
    cur = conn.execute("SELECT inv FROM bench_run WHERE status IN ('ok', 'partial') AND inv IS NOT NULL AND id != ? "
                       "AND (ts < ? OR (ts = ? AND id < ?)) ORDER BY ts DESC, id DESC LIMIT ?",
                       (run["id"], run["ts"], run["ts"], run["id"], int(limit)))
    for (v,) in cur:
        known = score.inv_known(_inv_of(v), known)     # newer known values win over older ones
        if known and all(k in known for k in INV_KEYS):
            break
    return known


def _inv_of(v):
    """An inventory already kept (the base) or put (a file): JSON text or a dict -> dict or None (never raises)."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    try:
        return inv(v) if isinstance(v, dict) else None
    except ValueError:
        return None


def _cond(v):
    return v if isinstance(v, str) and v in DROP_CONDS else None


def _legacy(v):
    """A condition of the removed scale (NS-D34): a queue file or a row of bench_drop written before the update."""
    return not isinstance(v, (bool, float)) and v in LEGACY_DROP_CONDS


def _conds(v):
    if not isinstance(v, list) or not 0 < len(v) <= len(DROP_CONDS):
        return None
    cs = [_cond(x) for x in v]
    return None if any(c is None for c in cs) else cs


def drops(doc, run=None):
    """The events bench_drop of a check (§2.8 п. 6; worked out by the runner from ``score.drop_events``, stored by the
    one writer, never replayed on a request): ``doc["drops"]`` = [{link, cond: "rel", ts, day, matched, push, auto,
    now (rel 0…300), test, value, usual, unit}] -> rows by DROP_COLS (``args`` — JSON of
    matched/push/auto/now/test/value/usual/unit).
    ValueError (strict, круг 2) — not a list, more than MAX_DROPS, a link wrong or twice, a condition not of DROP_CONDS,
    any other field of a wrong type or range; an unknown field of an event is not kept (one line of the journal, the
    event stays — the contract of SS §3.2). An event of the old scale (LEGACY_DROP_CONDS, a queue file of before the
    update) is skipped, not an error: the check itself is kept, not sent to bad/."""
    run = run or rows(doc)[0]
    ds = doc.get("drops") if isinstance(doc, dict) and doc.get("drops") is not None else []
    if not isinstance(ds, list) or len(ds) > MAX_DROPS:
        raise ValueError("drops")
    out, seen = [], set()
    for d in ds:
        if isinstance(d, dict) and _legacy(d.get("cond")):
            continue                                # a drop by the old scale: means nothing now, the check stays
        link = _re(d.get("link"), _TEST) if isinstance(d, dict) else None
        cond = _cond(d.get("cond")) if isinstance(d, dict) else None
        if link is None or link in seen or cond is None:
            raise ValueError("drop")
        seen.add(link)
        d = _only(d, DROP_KEYS, "drop")
        if d.get("run") is not None and d["run"] != run["id"]:
            raise ValueError("drop.run")
        o = lambda key, conv: _opt(d, key, conv, "drop")             # noqa: E731
        a = {"matched": o("matched", _conds) or [cond], "push": bool(o("push", _bool)), "auto": bool(o("auto", _bool)),
             "now": o("now", lambda x: x if _real(x, 0, 300) is not None else None),      # rel of the link (NS-D33)
             "test": o("test", lambda x: _re(x, _TEST)), "value": o("value", _finite),
             "usual": o("usual", _finite), "unit": o("unit", _unit)}
        out.append({"run": run["id"], "link": link, "ts": o("ts", _ts) or run["ts"],
                    "day": o("day", lambda x: _re(x, _DAY)),
                    "cond": str(cond), "args": json.dumps(dict((k, v) for k, v in a.items() if v is not None),
                                                         ensure_ascii=False, sort_keys=True)})
    return out


def drop_event(row):
    """A row of bench_drop -> the event of ``score.drop_events`` ({link, run, ts, day, cond, matched, push, auto, now,
    test, value, usual, unit}) — for ``score.view(events=…)``. A row of the old scale (stored before the update) —
    cond None: the callers skip it (its ``now`` is a score 0…1000, not rel)."""
    a = row.get("args")
    try:
        a = json.loads(a) if isinstance(a, str) else dict(a or {})
    except ValueError:
        a = {}
    cond = _cond(row.get("cond"))
    return {"link": row["link"], "run": row["run"], "ts": row["ts"], "day": row.get("day"), "cond": cond,
            "matched": a.get("matched") or [cond], "push": bool(a.get("push")), "auto": bool(a.get("auto")),
            "now": a.get("now"), "test": a.get("test"), "value": a.get("value"), "usual": a.get("usual"),
            "unit": a.get("unit")}

# ── the files ──────────────────────────────────────────────────────────────

def _ensure():
    util.ensure_dir(os.path.dirname(spool_dir()), 0o700)
    util.ensure_dir(spool_dir(), 0o700)


def put(doc):
    """Write a check into the queue (the runner, B1) -> the path. ValueError — ``rows`` says no (nothing written)."""
    run, _ = rows(doc)
    drops(doc, run)
    data = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(data.encode("utf-8")) > MAX_BYTES:
        raise ValueError("a check above %d bytes" % MAX_BYTES)
    _ensure()
    path = os.path.join(spool_dir(), run["id"] + ".json")
    util.atomic_write(path, data, 0o600)
    return path


def put_epoch(ts, a=None):
    """A new base by the tariff (C1, NS-S17): ``epoch-<ts>-tariff.json`` -> the path. ValueError — a bad moment or
    args that are not {key: a number or a code} (``args_strict``; ``args(a)`` cleans them first if need be)."""
    t = _ts(ts)
    if t is None or t >= 10 ** 10:
        raise ValueError("ts")
    a = args_strict(a, "args") if a is not None else {}
    _ensure()
    path = os.path.join(spool_dir(), "epoch-%010d-tariff.json" % t)
    util.atomic_write(path, json.dumps({"v": V, "args": a}, sort_keys=True), 0o600)
    return path


def scan():
    """The files of the queue by name -> ([(path, id)], [(path, ts)]); no folder — two empty lists."""
    runs, epochs = [], []
    try:
        names = sorted(os.listdir(spool_dir()))
    except OSError:
        return runs, epochs
    for n in names:
        m = RUN_NAME.fullmatch(n)
        if m:
            runs.append((os.path.join(spool_dir(), n), m.group(1)))
            continue
        m = EPOCH_NAME.fullmatch(n)
        if m:
            epochs.append((os.path.join(spool_dir(), n), int(m.group(1))))
    return runs, epochs


def sig_of(st):
    """The identity of a file as read: (device, inode, mtime ns, size) — a replace by ``util.atomic_write`` (a new inode)
    or a rewrite in place never keeps all three."""
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def load(path, sig=None):
    """The JSON object of a file of the queue. OSError — gone (or not readable); ValueError — not a regular file,
    above MAX_BYTES, not JSON, not an object. ``sig`` — a list: gets ``sig_of`` of the file read (the collector removes
    only that very file, B11 З-1)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if sig is not None:
            sig.append(sig_of(st))
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("not a regular file")
        if st.st_size > MAX_BYTES:
            raise ValueError("above %d bytes" % MAX_BYTES)
        data = b""
        while len(data) <= MAX_BYTES:
            got = os.read(fd, MAX_BYTES + 1 - len(data))
            if not got:
                break
            data += got
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        raise ValueError("above %d bytes" % MAX_BYTES)
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("not JSON")
    if not isinstance(doc, dict):
        raise ValueError("not an object")
    return doc


def load_run(path):
    """``rows`` of a check file (OSError / ValueError as ``load`` and ``rows``); the id must be the name's."""
    run, tests, _d = load_full(path)
    return run, tests


def load_full(path):
    """(run, tests, drops) of a check file — ``rows`` and ``drops``; the id must be the name's."""
    doc = load(path)
    run, tests = rows(doc)
    m = RUN_NAME.fullmatch(os.path.basename(path))
    if not m or m.group(1) != run["id"]:
        raise ValueError("the id is not the name")
    return run, tests, drops(doc, run)


def load_epoch(path):
    """(ts, "tariff", args) of a tariff file (OSError / ValueError)."""
    m = EPOCH_NAME.fullmatch(os.path.basename(path))
    doc = load(path)
    if not m or type(doc.get("v")) is not int or doc["v"] != V:
        raise ValueError("not an epoch of v %d" % V)
    _only(doc, ("v", "args"), "epoch")
    return int(m.group(1)), "tariff", args_strict(doc["args"], "epoch.args") if doc.get("args") is not None else {}


def aside(path, why):
    """A broken file into bad/ (0700; at most MAX_BAD, the oldest go) with a line in the journal (as live-feed.json,
    D-088). Never raises."""
    name = os.path.basename(path)
    try:
        util.ensure_dir(bad_dir(), 0o700)
        os.replace(path, os.path.join(bad_dir(), name))
        util.log_action("bench spool: %s is broken (%s), moved to %s" % (name, why, bad_dir()))
    except OSError:
        try:
            os.unlink(path)
            util.log_action("bench spool: %s is broken (%s), removed" % (name, why))
        except OSError:
            return
    try:
        bad = sorted(os.listdir(bad_dir()), key=lambda n: _mtime(os.path.join(bad_dir(), n)))
        for n in bad[:max(0, len(bad) - MAX_BAD)]:
            os.unlink(os.path.join(bad_dir(), n))
    except OSError:
        pass


def _mtime(p):
    try:
        return os.lstat(p).st_mtime
    except OSError:
        return 0


def _drop(path, sig=None):
    """Remove a file written into the base. ``sig`` — ``sig_of`` of the version read: another one under the path (the
    runner wrote the check again with its ``drops`` after the reading — SS §3.2, B11 REV-ns-b11-2 З-1) stays for the
    next minute (its rows replace the ones written now whole). No window between the look and the removal (REV-ns-join
    П-2, SEC-ns-join П-2): the version under the path is taken by ``rename`` into ``<name>.drop`` first and looked at
    there; a newer one goes back by ``link`` (never over a still newer one), the taken one is removed."""
    try:
        if sig is None:
            os.unlink(path)
            return 1
        if sig_of(os.lstat(path)) != sig:
            return 0                                 # the usual case of a second write: no rename at all
        trash = path + DROP_SUFFIX
        os.rename(path, trash)
    except OSError:
        return 0
    try:
        if sig_of(os.lstat(trash)) == sig:
            return 1
        try:
            os.link(trash, path)                     # a newer version came in the window: back
        except FileExistsError:
            pass                                     # a still newer one is there: it stays
        except OSError:
            if not os.path.lexists(path):
                os.rename(trash, path)
        return 0
    except OSError:
        return 0
    finally:
        try:
            os.unlink(trash)
        except OSError:
            pass


def _old_drops(now):
    """``<name>.drop`` left by a collector cut between its ``rename`` and its ``unlink`` (an hour old by ``ctime``)."""
    gone = 0
    try:
        names = os.listdir(spool_dir())
    except OSError:
        return 0
    for n in names:
        if not n.endswith(DROP_SUFFIX) or not (RUN_NAME.fullmatch(n[:-len(DROP_SUFFIX)])
                                               or EPOCH_NAME.fullmatch(n[:-len(DROP_SUFFIX)])):
            continue
        p = os.path.join(spool_dir(), n)
        try:
            if os.lstat(p).st_ctime < now - 3600:
                os.unlink(p)
                gone += 1
        except OSError:
            pass
    return gone


def prune(now=None):
    """The runner at its start (§3.2): checks older than KEEP_S (by ``run.ts``) go, at most MAX_FILES stay (the oldest
    go); tariff files — the same age, at most MAX_EPOCH_FILES; a broken file goes to bad/. -> files removed."""
    now = int(time.time() if now is None else now)
    runs, epochs = scan()
    kept, gone = [], 0
    for path, _id in runs:
        try:
            run, _ = load_run(path)
        except OSError:
            continue
        except Exception as e:                       # noqa: BLE001 — any bad field: the file is bad (С-2)
            aside(path, _why(e))
            continue
        if run["ts"] < now - KEEP_S:
            gone += _drop(path)
        else:
            kept.append((run["ts"], path))
    kept.sort()
    for _ts_, path in kept[:max(0, len(kept) - MAX_FILES)]:
        gone += _drop(path)
    ep = sorted((ts, path) for path, ts in epochs)
    for i, (ts, path) in enumerate(ep):
        if ts < now - KEEP_S or i < len(ep) - MAX_EPOCH_FILES:
            gone += _drop(path)
    return gone + _old_drops(now)


def read_all():
    """Everything the queue holds, for the reader (no file is moved or removed): ([(run, tests, drops)],
    [(ts, kind, args)]); broken and vanished files are skipped."""
    runs, epochs = scan()
    out, eps = [], []
    for path, _id in runs:
        got = read_one(path)
        if got is not None:
            out.append(got)
    for path, _ts_ in epochs:
        try:
            eps.append(load_epoch(path))
        except OSError:
            continue
        except Exception as e:                       # noqa: BLE001
            _tell(path, e)
    return out, eps


def read_one(path):
    """(run, tests, drops) of one check file for the reader, or None: vanished — quietly; broken (any error of its
    fields, С-2) — a line in the journal once per file and state, the file stays (the reader never moves a file)."""
    try:
        return load_full(path)
    except OSError:
        return None
    except Exception as e:                           # noqa: BLE001
        _tell(path, e)
        return None


_told = set()


def _why(e):
    """The reason of a bad file for the journal: the class and the field (never the value)."""
    return ("%s: %s" % (type(e).__name__, e))[:120] if isinstance(e, ValueError) else type(e).__name__


def _tell(path, e):
    try:
        st = os.lstat(path)
        key = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        return
    if key in _told:
        return
    if len(_told) >= 4096:
        _told.clear()
    _told.add(key)
    util.log_action("bench spool: %s is broken (%s), skipped by the reader" % (os.path.basename(path), _why(e)))


# ── the collector: the queue into the base (§3.2) ──────────────────────────

PER_MINUTE = 200                                        # files a minute at most: a backlog of 3000 — 15 minutes
SKIP_COUNTED = ("busy", "locked")                       # «the server was busy» (§2.5 п. 7, B1's codes); not the rest


def skip_push_path():
    return os.path.join(paths.bench_dir(), "skip-push.json")


def _push_day():
    """``day`` of skip-push.json (written by the bot only; the collector reads it) or "" — the one reading of the
    mark is ``bench.skipday.mark`` (a day later than tomorrow is no mark, SEC П-2)."""
    from . import skipday
    return skipday.mark()


def _skips_of_day(conn, day):
    from .. import tz
    rows = conn.execute("SELECT ts FROM bench_run WHERE status = 'skipped' AND \"by\" = 'schedule' AND why IN (%s) "
                        "AND ts >= ? AND ts < ?" % ",".join("?" * len(SKIP_COUNTED)),
                        SKIP_COUNTED + (_day_lo(day), _day_lo(day) + 3 * DAY))
    return sum(1 for (ts,) in rows if tz.day(ts) == day)


def _day_lo(day):
    """A moment surely before the local day ``day`` (zones are within ± 14 h of UTC)."""
    import calendar
    y, m, d = (int(x) for x in day.split("-"))
    return calendar.timegm((y, m, d, 0, 0, 0)) - DAY


def drain(w, now=None, limit=PER_MINUTE):
    """The collector's minute (``gw.collect._minute``): the tariff files, then the checks oldest first, a transaction a
    file (``w.write_bench`` / ``w.add_epoch``), each file removed once written; a broken one goes to bad/; the base
    failing — the rest wait for the next minute. A skipped check of «busy»/«locked» that brought the count of its
    nominal day (``tz.day(run.ts)``) from 1 to 2, the day later than skip-push.json, gives an event
    ``{"kind": "bench_skipped_day", "args": {"day", "n": 2}}`` (§2.5 п. 7; the collector never writes the mark). A
    check that ended (``ok``/``partial``/``failed``; not an automatic repeat — its news is ``bench_drop``) gives
    ``{"kind": "bench_done", "args": {"ts", "id", "mode", "by", "status", "why"}}`` (B7: the collector publishes it) —
    only when the check is later than done-push.json (``bench.donepush``: a file not removed is taken again, П-1),
    ended within ``donepush.MAX_AGE`` (an old one is no news — the collector was down, REV-ns-b7-2 З-7) and its file
    is removed (a file left gives no event every minute, П-7).
    -> {"taken", "bad", "events"}."""
    from .. import tz
    out = {"taken": 0, "bad": 0, "events": []}
    runs, epochs = scan()
    for path, ts in epochs:
        try:
            t, kind, a = load_epoch(path)
        except OSError:
            continue
        except Exception as e:                       # noqa: BLE001 — any bad field (С-2): aside, the queue goes on
            aside(path, _why(e))
            out["bad"] += 1
            continue
        if not w.add_epoch(t, kind, a):
            return out
        out["taken"] += _drop(path)
    todo = []
    for path, rid in runs:
        got = []
        try:
            doc = load(path, got)
            run, _ = rows(doc)
            drops(doc, run)
            if run["id"] != rid:
                raise ValueError("the id is not the name")
        except OSError:
            continue
        except Exception as e:                       # noqa: BLE001 — any bad field (С-2): aside, the queue goes on
            aside(path, _why(e))
            out["bad"] += 1
            continue
        todo.append((run["ts"], rid, path, doc, run, got[0]))
    todo.sort(key=lambda x: (x[0], x[1]))
    mark = None
    from . import donepush
    done_mark = donepush.mark()
    for _t, rid, path, doc, run, sig in todo[:max(0, int(limit))]:
        counted = run["status"] == "skipped" and run["why"] in SKIP_COUNTED and run["by"] == "schedule"
        got = w.write_bench(doc)
        if got is None:
            aside(path, "rows")
            out["bad"] += 1
            continue
        if not got:
            return out
        gone = _drop(path, sig)
        out["taken"] += gone
        if (gone and donepush.ended(run) and donepush.fresh(run, now)       # REV-ns-b7-2 П-7, З-7
                and not donepush.told(run["ts"], run["id"], done_mark or (0, ""))):
            out["events"].append({"kind": "bench_done", "args": donepush.args_of(run)})
        if counted:                                  # taken again after a crash: skip-push.json stops a 2nd push
            day = tz.day(run["ts"])
            mark = _push_day() if mark is None else mark
            if day > mark and _skips_of_day(w.conn, day) == 2:
                out["events"].append({"kind": "bench_skipped_day", "args": {"day": day, "n": 2}})
    return out
