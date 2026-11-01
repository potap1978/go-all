"""The history of exits for charts and the card of an exit — /var/lib/kaskad/metrics.db (docs/k3/SPEC-stage1-exits.md
§3.5, §3.6, §7.3, §7.9).

* sqlite3 of the standard library; WAL, ``synchronous=NORMAL``, ``busy_timeout=2000``.
* One writer — the collector (``open_writer``: an exclusive flock on ``metrics.db.lock``, a second writer gets None).
  The unit of probes writes its file (probe-results.json), the panel only reads (``history``, ``transitions``, ``feed``:
  ``file:…?mode=ro``, its own connection per request) — but for «Удалить историю активности» (№41, ``wipe_dev``): one
  short transaction of its own beside the collector (sqlite takes turns; the collector never writes a window of before
  it — ``meta.dev_wiped``, read in the transaction of ``write_dev``).
* No module sqlite3 or the base does not open -> charts off (``"charts": false``), everything else works.
* ``m1`` once in 60 s in one transaction; thinning m1 → m10 → h1 and the cleaning — once an hour (``maintain``).
  Kept: m1 48 h, m10 14 days, h1 90 days, speed/svc/geo 90 days, traffic 400 days, events 90 days. Above
  ``metrics_max_mb`` (64 by default) h1 and events are cut from the oldest end, the bigger first, never shorter than
  7 days (h1) and 30 days (events) — still above it: a warning in the journal; then VACUUM once a day, a line in the
  journal.
* ``rx``/``tx`` of a point are deltas by the rule of deltas (``delta``): the collector passes them in.
* SCHEMA 2 — the health of the node (k3/handoff/SPEC-node-score.md §3.4): ``host_m1/m10/h1`` (a minute of the node,
  ``write_host_minute``) and ``host_ping_m1/m10/h1`` (light probes per anchor, ``write_ping_minute``), thinned and kept
  like m1/m10/h1 (``HOST_COLS``/``PING_COLS`` say how), ``host_h1`` under the cap with a floor of 30 days (the band
  of 30 days, C6), ``host_ping_h1`` of 7; each family — exits, node, probes — thinned and cleaned in a transaction of
  its own; read by ``host_history``/``ping_history``. The tables of stage 1 are not altered; the number in meta only
  grows; the code of stage 1 (SCHEMA 1) opens a base of SCHEMA 2 and leaves the tables it does not know alone.
* SCHEMA 3 — the checks of the node (k3/handoff/SPEC-node-score-schedule-share.md §3.1, §3.2, NS-S9, NS-S17):
  ``bench_run`` (a check whole), ``bench`` (its tests) and ``bench_drop`` (its events «обвал звена», §2.8 п. 6 — worked
  out by the runner, kept so that no request replays the history) from the queue ``bench/spool`` (``write_bench``: a file — one
  transaction, the events of a new base ``cpu``/``mem``/``engines`` against the previous good check), ``bench_epoch``
  (``add_epoch``: the tariff file of the queue), ``host_people_h1`` (the hour of «Связь с людьми», NS B17,
  ``write_people_hour``). Way up from 2 — only CREATE TABLE IF NOT EXISTS, no ALTER; kept 400 days (host_people_h1 90),
  under the cap with floors of 90 days (bench) and 30 (host_people_h1), bench cut last. The code of SCHEMA 2 opens a
  base of 3 and never reads or touches these tables; going up again drops what is older than kept.
* SCHEMA 4 — the activity of devices, «кардиограмма» of «Люди» (k3/handoff/SPEC-activity.md §4, ACT-2, ACT-3, ACT-6;
  the core — ``kaskad.activity``): ``dev_m5`` (5 min, 25 h), ``dev_h1`` (an hour, 30 days), ``dev_d1`` (a day of the
  zone of the node, 400 days — the totals for the limits of traffic of stage 3, decision 4 of the coordinator 20.10),
  ``dev_node`` (the windows the collector worked: rounds and seconds by step 300 and 3600, ACT-12). Each row — the id
  of a device, the start of its window, seconds «на связи» and «активен», bytes rx and tx: nothing else (ACT-6). A
  closed window of the collector goes to all four in ONE transaction (``write_dev``: added, UPDATE + INSERT OR IGNORE —
  no UPSERT in sqlite 3.22), so nothing is thinned: ``maintain`` only drops what is older than kept; dev_h1 and dev_d1
  are under the cap with floors of 7 and 90 days (dev_node of an hour goes with dev_h1). ``drop_dev`` (the history
  off) and ``forget_dev`` (the devices of the state only) delete; ``dev_summary`` / ``dev_history`` / ``dev_info`` read.
  Way up from 3 — only CREATE TABLE IF NOT EXISTS; the code of SCHEMA 3 opens a base of 4 and never touches dev_*.

The way up (any old number → SCHEMA) is ONE transaction (``open_writer``: BEGIN IMMEDIATE, every CREATE, the number,
COMMIT): sqlite3 of Python runs a DDL outside a transaction of its own, so without it a failure or a kill in the middle
left a part of the new tables with the old number. Now it is all or nothing; the next start does it again.
"""

import fcntl
import json
import math
import os
import re
import time

try:
    import sqlite3
except ImportError:                                # a Python built without it: charts off, the rest works
    sqlite3 = None

from .. import paths, tz, util
from ..bench import spool
from .policy import repeat_id

SCHEMA = 4
DAY = 86400
KEEP = {"m1": 2 * DAY, "m10": 14 * DAY, "h1": 90 * DAY, "speed": 90 * DAY, "svc": 90 * DAY, "geo": 90 * DAY,
        "traffic": 400 * DAY, "events": 90 * DAY,
        "host_m1": 2 * DAY, "host_m10": 14 * DAY, "host_h1": 90 * DAY,
        "host_ping_m1": 2 * DAY, "host_ping_m10": 14 * DAY, "host_ping_h1": 90 * DAY,
        "bench_run": 400 * DAY, "bench": 400 * DAY, "bench_epoch": 400 * DAY, "bench_drop": 400 * DAY,
        "host_people_h1": 90 * DAY,
        "dev_m5": 25 * 3600, "dev_h1": 30 * DAY, "dev_d1": 400 * DAY}
DEV_TABLES = ("dev_m5", "dev_h1", "dev_d1")
DEV_WIPED = "dev_wiped"                            # meta: the stamp of «Удалить историю активности» (№41)
WIPE_WAIT = 5.0                                    # s: the wipe of the panel waits for a transaction of the collector
DEV_STEP = {"dev_m5": 300, "dev_h1": 3600}         # dev_node keeps the windows of these two steps
DEV_RANGES = {"1d": ("dev_m5", 300, 288), "7d": ("dev_h1", 3600, 168), "30d": ("dev_h1", 3600, 720)}
DEV_SUM_STEP, DEV_SUM_N = 1800, 48                 # the summary of «Люди»: 48 windows of 30 min (§5.1)
DEV_IN_MAX = 500                                   # devices in one query of the history (a person)
DEV_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")         # an id of a device of the state (d-…)
BENCH_TABLES = ("bench_run", "bench", "bench_epoch", "bench_drop")
HOST_TABLES = ("host_m1", "host_m10", "host_h1")
PING_TABLES = ("host_ping_m1", "host_ping_m10", "host_ping_h1")
STEP = {"m1": 60, "m10": 600, "h1": 3600}
RANGES = {"1d": (DAY, "m1"), "7d": (7 * DAY, "m10"), "90d": (90 * DAY, "h1")}
MAX_POINTS = 1440
MAX_MB = 64
MAINT_S = 3600
VACUUM_S = DAY
CAP_FLOOR = {"h1": 7 * DAY, "events": 30 * DAY,    # the cap never cuts these tables shorter
             "host_h1": 30 * DAY, "host_ping_h1": 7 * DAY,  # host_h1: the band of 30 days (C6), ~0.2 MB
             "host_people_h1": 30 * DAY,          # as host_h1
             "bench_run": 90 * DAY,               # with its rows of bench; cut last of all (``_cap``), ≲ 5 MB in 400 days
             "dev_h1": 7 * DAY,                   # SCHEMA 4 (SPEC-activity §4): with dev_node of an hour
             "dev_d1": 90 * DAY}                  # the totals of a day: ≈ 2,5 MB in 400 days on 150 devices
_CAP_SIZE = {                                      # bytes a table holds, roughly: the bigger is cut first
    "h1": "SELECT SUM(LENGTH(exit) + 64 + IFNULL(LENGTH(state), 0)) FROM h1",
    "events": "SELECT SUM(LENGTH(IFNULL(args, '')) + LENGTH(IFNULL(key, '')) + IFNULL(LENGTH(exit), 0) + 24) "
              "FROM events",
    "host_h1": "SELECT COUNT(*) * 250 FROM host_h1",      # live-like values: ~220–245 B a full row (REV-ns-a4 З-2)
    "host_ping_h1": "SELECT SUM(LENGTH(anchor) + IFNULL(LENGTH(via), 0) + 52) FROM host_ping_h1",
    "host_people_h1": "SELECT SUM(IFNULL(LENGTH(inputs), 0) + 90) FROM host_people_h1",
    "bench_run": "SELECT IFNULL((SELECT SUM(IFNULL(LENGTH(inv), 0) + IFNULL(LENGTH(links), 0) + "
                 "IFNULL(LENGTH(why_args), 0) + IFNULL(LENGTH(meta), 0) + 120) FROM bench_run), 0) + "
                 "IFNULL((SELECT SUM(IFNULL(LENGTH(extra), 0) + 90) FROM bench), 0)",
    "dev_h1": "SELECT COUNT(*) * 48 FROM dev_h1",          # ~ a row of 6 numbers and an id of 10 (SPEC-activity §4)
    "dev_d1": "SELECT COUNT(*) * 48 FROM dev_d1"}
COLS = ("rtt_med", "rtt_p90", "loss", "jitter", "ok", "n", "rx", "tx", "devices", "state")
STATES = ("unknown", "ok", "degraded", "standby", "off")      # a coarser point shows the worst state of its minutes
_SEV = "CASE state WHEN 'off' THEN 4 WHEN 'standby' THEN 3 WHEN 'degraded' THEN 2 WHEN 'ok' THEN 1 ELSE 0 END"

# The columns of the node (§3.4): (name, type, how a coarser row gets it). sum — counters, minutes, n; wavg — the
# average weighted by n (by jit_n for jit: the jitter of a row is the mean of its jit_n samples); max / min — the worst
# of the bucket; avg — a plain average (NULL not counted: canary_ms is NULL but in the minute of the canary); worst —
# the value farthest from 0, its sign kept (clock_off: an offset of the clock, ± seconds); last — the value of the
# newest row of the bucket that has one (via: MAX('icmp', 'dns') would always be 'icmp', REV П-1).
HOST_COLS = (
    ("n", "INTEGER", "sum"), ("busy", "REAL", "wavg"), ("busy_max", "REAL", "max"), ("steal", "REAL", "wavg"),
    ("iowait", "REAL", "wavg"), ("softirq", "REAL", "wavg"), ("load_core", "REAL", "wavg"),
    ("psi_cpu", "REAL", "wavg"), ("psi_mem", "REAL", "wavg"), ("psi_io", "REAL", "wavg"),
    ("mem_avail", "REAL", "wavg"), ("mem_avail_min", "REAL", "min"), ("swap", "REAL", "wavg"),
    ("oom", "INTEGER", "sum"), ("disk", "REAL", "max"), ("disk_free", "INTEGER", "min"), ("inodes", "REAL", "max"),
    ("rx", "INTEGER", "sum"), ("tx", "INTEGER", "sum"), ("rx_peak", "REAL", "max"), ("tx_peak", "REAL", "max"),
    ("pkts", "INTEGER", "sum"), ("drops", "INTEGER", "sum"), ("errs", "INTEGER", "sum"),
    ("softnet", "INTEGER", "sum"), ("retrans", "INTEGER", "sum"), ("out_segs", "INTEGER", "sum"),
    ("listen", "INTEGER", "sum"), ("conntrack", "REAL", "max"), ("fd", "REAL", "max"),
    ("clock_ok", "INTEGER", "min"), ("clock_off", "REAL", "worst"), ("canary_ms", "REAL", "avg"),
    ("level", "INTEGER", "max"), ("warn_min", "INTEGER", "sum"), ("alarm_min", "INTEGER", "sum"))
PING_COLS = (
    ("via", "TEXT", "last"), ("n", "INTEGER", "sum"), ("lost", "REAL", "sum"), ("runs", "INTEGER", "sum"),
    ("rtt_med", "REAL", "wavg"), ("rtt_p90", "REAL", "max"), ("jit", "REAL", "wavg:jit_n"),
    ("jit_n", "INTEGER", "sum"), ("excl", "INTEGER", "sum"), ("outage", "INTEGER", "sum"))
PING_READ = ("n", "lost", "rtt_med", "jit")        # a point of ping_history: [ts, n, lost, rtt_med, jit]
_FLAGS = ("clock_ok", "warn_min", "alarm_min")     # 0 / 1 in a minute row
# «Связь с людьми» (NS B17): an hour of the node, no addresses or ports of people — counts, delays, bytes
PEOPLE_COLS = (("conns", "INTEGER"), ("rtt_med", "REAL"), ("rtt_p90", "REAL"), ("rtt_min_med", "REAL"),
               ("sent", "INTEGER"), ("retr", "INTEGER"), ("dsack", "INTEGER"))
# why: few — no numbers; no_tcp_inbound — nothing to measure; shared_443 — the only TCP input is VLESS sharing 443
# with the site; the reason no TCP input could be read the whole hour (B17 gw/hostpeople.py: no sock_diag, tcp_info
# without bytes_sent, cgroup v1, no cgroup of the unit, the descriptors not readable)
PEOPLE_WHY = ("few", "no_tcp_inbound", "shared_443", "no_diag", "old_kernel", "old_cgroup", "no_cgroup", "no_access")
PEOPLE_INPUT = ("ok", "few", "shared_port", "udp", "no_data")


class PeopleRowInvalid(ValueError):
    """``write_people_hour``: the row can never be written — its data (a bad ts, a number too big for SQLite, a
    constraint): the collector of B17 drops it with a line in the journal and goes on. The base failing is another
    error (RuntimeError): the hour waits for the next minute (contract with B17, круг 2)."""


# the errors of the data of a row (not of the base): OverflowError — an int above 2^63, IntegrityError, DataError,
# InterfaceError — a value SQLite cannot bind; TypeError/ValueError/KeyError — the row itself
PEOPLE_DATA_ERRORS = (OverflowError, TypeError, ValueError, KeyError) + ((
    sqlite3.IntegrityError, sqlite3.DataError, sqlite3.InterfaceError) if sqlite3 is not None else ())
_INT_MAX = 2 ** 53             # |a counter| of a row: far above any real one; SUM() of ≤ 1023 rows stays in int64
_REAL_MAX = 1e12               # |a REAL| of a row: far above any real one; × n (≤ 2^53) stays finite (1e308 → inf)

_DDL = ["CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)"] + [
    "CREATE TABLE IF NOT EXISTS %s (exit TEXT, ts INTEGER, rtt_med REAL, rtt_p90 REAL, loss REAL, jitter REAL, "
    "ok INTEGER, n INTEGER, rx INTEGER, tx INTEGER, devices INTEGER, state TEXT, PRIMARY KEY (exit, ts)) WITHOUT ROWID"
    % t for t in ("m1", "m10", "h1")] + [
    "CREATE TABLE IF NOT EXISTS speed (exit TEXT, ts INTEGER, mbps REAL, ttfb_ms REAL, tls_ms REAL, bytes INTEGER, "
    "host TEXT, ok INTEGER, why TEXT, PRIMARY KEY (exit, ts)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS svc (exit TEXT, svc TEXT, ts INTEGER, ok INTEGER, code INTEGER, ms REAL, "
    "PRIMARY KEY (exit, svc, ts)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS geo (exit TEXT, ts INTEGER, ip TEXT, loc TEXT, PRIMARY KEY (exit, ts)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS traffic (exit TEXT, day TEXT, rx INTEGER, tx INTEGER, probe INTEGER, "
    "PRIMARY KEY (exit, day)) WITHOUT ROWID",
    "CREATE TABLE IF NOT EXISTS events (ts INTEGER, key TEXT, exit TEXT, args TEXT)",
    "CREATE INDEX IF NOT EXISTS events_ts ON events(ts)"] + [
    "CREATE TABLE IF NOT EXISTS %s (ts INTEGER PRIMARY KEY, %s)" % (t, ", ".join("%s %s" % c[:2] for c in HOST_COLS))
    for t in HOST_TABLES] + [
    "CREATE TABLE IF NOT EXISTS %s (anchor TEXT, ts INTEGER, %s, PRIMARY KEY (anchor, ts)) WITHOUT ROWID"
    % (t, ", ".join("%s %s" % c[:2] for c in PING_COLS)) for t in PING_TABLES] + [
    # SCHEMA 3 (SS §3.1): the checks of the node, their new bases, the hour of «Связь с людьми»
    "CREATE TABLE IF NOT EXISTS bench_run (id TEXT PRIMARY KEY, ts INTEGER, end_ts INTEGER, mode TEXT, by TEXT, "
    "sched_id TEXT, retry_of TEXT, inv TEXT, status TEXT, why TEXT, why_args TEXT, rx INTEGER, tx INTEGER, "
    "dur_s REAL, cpu_s REAL, under_load INTEGER, pkgs INTEGER, score INTEGER, scale_version TEXT, links TEXT, "
    "meta TEXT)",
    "CREATE TABLE IF NOT EXISTS bench (run TEXT, test TEXT, ts INTEGER, value REAL, unit TEXT, scale_version TEXT, "
    "mode TEXT, under_load INTEGER, ok INTEGER, why TEXT, score INTEGER, value_corr REAL, people REAL, ca TEXT, "
    "extra TEXT, PRIMARY KEY (run, test)) WITHOUT ROWID",
    "CREATE INDEX IF NOT EXISTS bench_run_ts ON bench_run(ts)",
    "CREATE TABLE IF NOT EXISTS bench_epoch (ts INTEGER, kind TEXT, args TEXT, PRIMARY KEY (ts, kind)) WITHOUT ROWID",
    # the events bench_drop (§2.8 п. 6) of a check, one a link: worked out once by the runner (score.drop_events state),
    # stored here — never replayed over the whole history on a request (coordinator 19.10)
    "CREATE TABLE IF NOT EXISTS bench_drop (run TEXT, link TEXT, ts INTEGER, day TEXT, cond TEXT, args TEXT, "
    "PRIMARY KEY (run, link)) WITHOUT ROWID",
    # partial (B17 круг 2): samples of the hour that did not see everything; last, as in SS §3.1
    "CREATE TABLE IF NOT EXISTS host_people_h1 (ts INTEGER PRIMARY KEY, %s, why TEXT, inputs TEXT, partial INTEGER)"
    % ", ".join("%s %s" % c for c in PEOPLE_COLS)] + [
    # SCHEMA 4 (SPEC-activity §4, ACT-3): the key (ts, dev) WITHOUT ROWID, no second index — a write goes to the end
    # of the tree; dev_d1.ts — the start of the day of the zone of the node (UTC seconds)
    "CREATE TABLE IF NOT EXISTS %s (ts INTEGER, dev TEXT, on_s INTEGER, act_s INTEGER, rx INTEGER, tx INTEGER, "
    "PRIMARY KEY (ts, dev)) WITHOUT ROWID" % t for t in DEV_TABLES] + [
    "CREATE TABLE IF NOT EXISTS dev_node (step INTEGER, ts INTEGER, rounds INTEGER, secs INTEGER, "
    "PRIMARY KEY (step, ts)) WITHOUT ROWID"]


def _agg(col, rule):
    """The SQL of a coarser value of ``col`` by its ``rule`` (HOST_COLS, PING_COLS); ``last`` is the caller's."""
    if rule.startswith("wavg"):
        w = "(CASE WHEN {0} > 0 THEN {0} ELSE 1 END)".format(rule.partition(":")[2] or "n")   # no n: a weight of 1
        return "SUM({c} * {w}) / SUM(CASE WHEN {c} IS NOT NULL THEN {w} END)".format(c=col, w=w)
    if rule == "worst":
        return "CASE WHEN MAX({0}) >= -MIN({0}) THEN MAX({0}) ELSE MIN({0}) END".format(col)
    return {"sum": "SUM(%s)", "max": "MAX(%s)", "min": "MIN(%s)", "avg": "AVG(%s)"}[rule] % col


def _cell(v, typ, name=None):
    """A value of a row of the node for its column: TEXT — a string of 1–16; a number or a numeric string within
    the limits (``_val``), else NULL (a word, NaN, ±inf, |v| above 2^53 or 1e12, a level not 0–2); flags — 0 / 1."""
    if typ == "TEXT":
        return v if isinstance(v, str) and 0 < len(v) <= 16 else None
    if name in _FLAGS and isinstance(v, bool):
        v = int(v)
    v = _val(v, int if typ == "INTEGER" else float)
    if v is None or typ == "REAL":
        return v
    if name in _FLAGS:
        return 1 if v > 0 else 0
    if name == "level":
        return v if 0 <= v <= 2 else None
    return v


def delta(cur, last):
    """The rule of deltas (§3.5): ``cur − last`` when the counter grew, else ``cur`` (xray restarted, kawgN made anew,
    a reboot) — for the points of the history and the quota counters of traffic.json alike."""
    cur, last = int(cur or 0), int(last or 0)
    return cur - last if cur >= last else cur


def available():
    """The module sqlite3 is there."""
    return sqlite3 is not None


def _mono():
    return time.monotonic()


def lock_path():
    return paths.metrics_db() + ".lock"


def _num(v, kind=float):
    if v is None or isinstance(v, bool):
        return None
    try:
        v = kind(v)
    except (TypeError, ValueError, OverflowError):  # int(float("inf")) is an OverflowError
        return None
    return None if v != v else v


def _val(v, kind=float):
    """A value of a row to keep (``_num`` within the limits): |int| ≤ 2^53, |float| ≤ 1e12, else None — garbage would
    make SUM() of thinning overflow (and stop it) or an average turn inf (REV-ns-a4 З-1, П-2)."""
    v = _num(v, kind)
    if v is None:
        return None
    if kind is int:
        return v if -_INT_MAX <= v <= _INT_MAX else None
    return v if math.isfinite(v) and -_REAL_MAX <= v <= _REAL_MAX else None


class Writer(object):
    """The connection of the collector. Every method swallows its errors — sqlite or bad values (a line in the
    journal, False): the history must never stop a tick."""

    def __init__(self, conn, lock_fd, path):
        self.conn = conn
        self.path = path
        self._lock_fd = lock_fd
        self._maint = None                         # monotonic time of the last maintain
        self._said = set()

    # -- plumbing --
    def close(self):
        try:
            self.conn.close()
        finally:
            if self._lock_fd is not None:
                try:
                    os.close(self._lock_fd)
                except OSError:
                    pass
                self._lock_fd = None

    def _err(self, where, e):
        if where not in self._said:                # one line per kind of failure, not one per tick
            self._said.add(where)
            util.log_action("GW metrics.db: %s failed: %s" % (where, e))
        return False

    def _tx(self, where, fn):
        try:
            with self.conn:
                fn(self.conn)
            return True
        except Exception as e:                    # sqlite3.Error, a bad ts or args (TypeError, ValueError, ...)
            return self._err(where, e)

    def meta(self, k, default=None):
        try:
            row = self.conn.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        except sqlite3.Error:
            return default
        return row[0] if row else default

    def _set_meta(self, c, k, v):
        c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, str(v)))

    # -- writes --
    def write_minute(self, ts, rows):
        """One point of every exit: ``rows`` — {exit id: {rtt_med, rtt_p90, loss, jitter, ok, n, rx, tx, devices,
        state}} (missing fields — NULL); ``ts`` is floored to the minute. One transaction."""
        def go(c):
            t = int(ts) // 60 * 60
            data = []
            for eid, r in sorted((rows or {}).items()):
                r = r or {}
                st = r.get("state") if r.get("state") in STATES else None
                data.append((eid, t, _val(r.get("rtt_med")), _val(r.get("rtt_p90")), _val(r.get("loss")),
                             _val(r.get("jitter")), _val(r.get("ok"), int), _val(r.get("n"), int),
                             _val(r.get("rx"), int), _val(r.get("tx"), int), _val(r.get("devices"), int), st))
            c.executemany("INSERT OR REPLACE INTO m1 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", data)
        return self._tx("m1", go)

    def write_host_minute(self, ts, row):
        """The minute of the node (§3.4): ``row`` — {column of HOST_COLS: value} (missing or bad — NULL); ``ts`` is
        floored to the minute. One transaction."""
        def go(c):
            t = int(ts) // 60 * 60
            r = row if isinstance(row, dict) else {}
            c.execute("INSERT OR REPLACE INTO host_m1 (ts, %s) VALUES (?%s)" % (
                ", ".join(n for n, _, _ in HOST_COLS), ",?" * len(HOST_COLS)),
                [t] + [_cell(r.get(n), typ, n) for n, typ, _ in HOST_COLS])
        return self._tx("host_m1", go)

    def write_ping_minute(self, ts, rows):
        """The light probes of the minute (§3.3, §3.4): ``rows`` — {anchor: {via, n, lost, …}} or [{anchor, via, n, …}]
        (anchor "*" — the node); a row without an anchor (a string of 1–64) is skipped, a bad value is NULL; ``ts`` is
        floored to the minute. One transaction."""
        def go(c):
            t = int(ts) // 60 * 60
            items = rows.items() if isinstance(rows, dict) else (
                (r.get("anchor"), r) for r in (rows or ()) if isinstance(r, dict))
            data = []
            for a, r in items:
                if not isinstance(a, str) or not 0 < len(a) <= 64 or not isinstance(r, dict):
                    continue
                data.append([a, t] + [_cell(r.get(n), typ, n) for n, typ, _ in PING_COLS])
            c.executemany("INSERT OR REPLACE INTO host_ping_m1 (anchor, ts, %s) VALUES (?,?%s)" % (
                ", ".join(n for n, _, _ in PING_COLS), ",?" * len(PING_COLS)), data)
        return self._tx("host_ping_m1", go)

    def add_speed(self, eid, ts, mbps=None, ttfb_ms=None, tls_ms=None, nbytes=None, host=None, ok=True, why=None):
        return self._tx("speed", lambda c: c.execute(
            "INSERT OR REPLACE INTO speed VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, int(ts), _num(mbps), _num(ttfb_ms), _num(tls_ms), _num(nbytes, int), host, 1 if ok else 0, why)))

    def add_svc(self, eid, svc, ts, ok, code=None, ms=None):
        return self._tx("svc", lambda c: c.execute(
            "INSERT OR REPLACE INTO svc VALUES (?,?,?,?,?,?)",
            (eid, svc, int(ts), 1 if ok else 0, _num(code, int), _num(ms))))

    def add_geo(self, eid, ts, ip, loc):
        return self._tx("geo", lambda c: c.execute("INSERT OR REPLACE INTO geo VALUES (?,?,?,?)",
                                                   (eid, int(ts), ip, loc)))

    def add_traffic(self, eid, day, rx=0, tx=0, probe=0):
        """Add the bytes of a point to the day of the exit (``day`` — 'YYYY-MM-DD' by the zone of the node)."""
        def go(c):
            v = (int(rx or 0), int(tx or 0), int(probe or 0), eid, day)
            c.execute("UPDATE traffic SET rx = rx + ?, tx = tx + ?, probe = probe + ? WHERE exit = ? AND day = ?", v)
            c.execute("INSERT OR IGNORE INTO traffic VALUES (?,?,?,?,?)", (eid, day) + v[:3])
        return self._tx("traffic", go)

    def add_event(self, ts, key, eid=None, args=None):
        """A published event (after the antispam) or a transition (``key = "transition"``, before it; §3.6)."""
        return self._tx("events", lambda c: c.execute("INSERT INTO events VALUES (?,?,?,?)", (
            int(ts), key, eid, json.dumps(args or {}, ensure_ascii=False, sort_keys=True))))

    def add_transition(self, ts, eid, frm, to, why=None, args=None):
        """Every change of the state of an exit, before the antispam (the card of the exit, §7.3)."""
        a = dict(args or {})
        a.update({"from": frm, "to": to, "why": why})
        return self.add_event(ts, "transition", eid, a)

    # -- the checks of the node (SCHEMA 3) --
    def write_bench(self, doc):
        """A file of the queue (``bench.spool``) into bench_run + bench + bench_drop — one transaction, again and again harmless (the
        rows of the check are replaced whole). A good check with an inventory gets the events of a new base against the
        previous good one (``spool.epoch_kinds``: ``cpu``, ``mem``, ``engines`` at the moment of this check, NS-S17).
        -> True written; None — the file is bad (``spool.rows`` said no: it goes aside, a retry will not help); False —
        the base failed (the file stays for the next minute)."""
        try:
            run, tests = spool.rows(doc)
            drops = spool.drops(doc, run)
        except Exception as e:                        # noqa: BLE001 — any bad field (С-2): a bad file, never raises
            util.log_action("GW metrics.db: a bad check file: %s" % spool._why(e))
            return None

        def go(c):
            c.execute("INSERT OR REPLACE INTO bench_run (%s) VALUES (%s)" % (
                ", ".join(spool.RUN_COLS), ",".join("?" * len(spool.RUN_COLS))), [run[k] for k in spool.RUN_COLS])
            c.execute("DELETE FROM bench WHERE run = ?", (run["id"],))
            c.executemany("INSERT INTO bench (%s) VALUES (%s)" % (
                ", ".join(spool.TEST_COLS), ",".join("?" * len(spool.TEST_COLS))),
                [[t[k] for k in spool.TEST_COLS] for t in tests])
            c.execute("DELETE FROM bench_drop WHERE run = ?", (run["id"],))
            c.executemany("INSERT INTO bench_drop (%s) VALUES (%s)" % (
                ", ".join(spool.DROP_COLS), ",".join("?" * len(spool.DROP_COLS))),
                [[d[k] for k in spool.DROP_COLS] for d in drops])
            if run["status"] in spool.GOOD and run["inv"]:
                for kind, a in spool.epoch_kinds(spool.known_before(c, run), run["inv"]):
                    c.execute("INSERT OR IGNORE INTO bench_epoch VALUES (?,?,?)", (
                        run["ts"], kind, json.dumps(a, sort_keys=True)))
        return self._tx("bench", go)

    def add_epoch(self, ts, kind, args=None):
        """A new base (NS-S17): the tariff file of the queue — (ts, kind) once (INSERT OR IGNORE)."""
        if kind not in spool.EPOCH_KINDS:
            return False
        return self._tx("bench_epoch", lambda c: c.execute("INSERT OR IGNORE INTO bench_epoch VALUES (?,?,?)", (
            int(ts), kind, json.dumps(spool.args(args), sort_keys=True))))

    def write_people_hour(self, ts, row):
        """The hour of «Связь с людьми» (NS B17, ``gw/hostpeople.py`` ``row()``): ``row`` — {conns, rtt_med, rtt_p90,
        rtt_min_med, sent, retr, dsack, why, inputs: {input key: ok | few | shared_port | udp | no_data}, partial}
        (inputs kept as JSON); ``ts`` floored to the hour — the key: the same hour again replaces the row. A bad value
        is NULL, an input with another key or status is dropped (no ports, no addresses). One transaction. -> True.
        Unlike the
        other writes it RAISES: ``PeopleRowInvalid`` — the data of the row (a ts not a moment, OverflowError,
        IntegrityError …): B17 drops the row; ``RuntimeError`` — the base failing: B17 keeps the hour for the next
        minute (``_people_flush``)."""
        t = _num(ts, int)                             # None, a word, NaN, ±inf -> None
        if t is None or not spool.TS_MIN <= t <= spool.TS_MAX:
            raise PeopleRowInvalid("ts")
        try:
            with self.conn:
                self._people_row(self.conn, t, row)
            return True
        except PEOPLE_DATA_ERRORS as e:               # the row, not the base: never retried
            util.log_action("GW metrics.db: host_people_h1 — a row refused for its data: %s" % type(e).__name__)
            raise PeopleRowInvalid(type(e).__name__)
        except Exception as e:                        # noqa: BLE001 — the base failing: the hour waits
            self._err("host_people_h1", e)
            raise RuntimeError("metrics.db: host_people_h1 not written")

    def _people_row(self, c, ts, row):
        r = row if isinstance(row, dict) else {}
        ins = {}
        if isinstance(r.get("inputs"), dict):
            for k in sorted(r["inputs"]):
                v = r["inputs"][k]
                if isinstance(k, str) and spool.ID_KEY.fullmatch(k) and v in PEOPLE_INPUT and len(ins) < 64:
                    ins[k] = v
        c.execute("INSERT OR REPLACE INTO host_people_h1 (ts, %s, why, inputs, partial) VALUES (?%s,?,?,?)" % (
            ", ".join(n for n, _ in PEOPLE_COLS), ",?" * len(PEOPLE_COLS)),
            [int(ts) // 3600 * 3600] + [_val(r.get(n), int if typ == "INTEGER" else float) for n, typ in PEOPLE_COLS]
            + [r.get("why") if r.get("why") in PEOPLE_WHY else None,
               json.dumps(ins, sort_keys=True) if ins else None, _val(r.get("partial"), int)])

    # -- the activity of devices (SCHEMA 4, SPEC-activity §4) --
    def write_dev(self, win, z=None):
        """A closed window of ``kaskad.activity`` — {ts, rounds, secs, rows: {dev: [on_s, act_s, rx, tx]}} — into
        dev_m5, dev_h1, dev_d1 (the day of the zone ``z`` of the node) and dev_node, ONE transaction, added to what
        the rows hold (a window merged after a wait lands in its hour and day). Seconds — at most the window; a bad
        id or number is skipped. ``meta.dev_flush`` — the newest window written, ``dev_since`` — the first. A window
        that began at or before ``meta.dev_wiped`` (№41, ``wipe_dev``) is not written (nothing to retry).
        -> True written (or nothing to write); False — the base failed (the caller keeps the window)."""
        ts = _val((win or {}).get("ts"), int)
        if ts is None or not spool.TS_MIN <= ts <= spool.TS_MAX:
            return True                                   # garbage: nothing to keep, nothing to retry
        ts = ts // 300 * 300
        hour = ts // 3600 * 3600
        lt = tz.local(ts, z)
        day = tz.to_utc(lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, z)
        rows = []
        for dev, r in sorted(((win or {}).get("rows") or {}).items()):
            if not isinstance(dev, str) or not DEV_ID.fullmatch(dev) or not isinstance(r, (list, tuple)) \
                    or len(r) != 4:
                continue
            on, act = _val(r[0], int), _val(r[1], int)
            rx, tx = _val(r[2], int), _val(r[3], int)
            if None in (on, act, rx, tx) or min(on, act, rx, tx) < 0:
                continue
            rows.append((min(on, 300), min(act, on, 300), rx, tx, dev))
        rounds = max(0, _val(win.get("rounds"), int) or 0)
        secs = min(300, max(0, _val(win.get("secs"), int) or 0))

        def go(c):
            # №41: the mark of «Удалить историю» read under the write lock of THIS transaction — a wipe of the panel is
            # either before it (a window that began at or before the wipe is not written: it holds time before it) or
            # after it (and deletes what this one wrote); never a window of before the wipe written after it
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT v FROM meta WHERE k = ?", (DEV_WIPED,)).fetchone()
            cut = _num(row[0], int) if row else None
            if cut is not None and ts <= cut:
                return
            for t, at in (("dev_m5", ts), ("dev_h1", hour), ("dev_d1", day)):
                data = [x + (at,) for x in rows]
                c.executemany("UPDATE %s SET on_s = on_s + ?, act_s = act_s + ?, rx = rx + ?, tx = tx + ? "
                              "WHERE dev = ? AND ts = ?" % t, data)
                c.executemany("INSERT OR IGNORE INTO %s (on_s, act_s, rx, tx, dev, ts) VALUES (?,?,?,?,?,?)" % t, data)
            for step, at in ((300, ts), (3600, hour)):
                c.execute("UPDATE dev_node SET rounds = rounds + ?, secs = secs + ? WHERE step = ? AND ts = ?",
                          (rounds, secs, step, at))
                c.execute("INSERT OR IGNORE INTO dev_node VALUES (?,?,?,?)", (step, at, rounds, secs))
            self._set_meta(c, "dev_flush", ts)            # the last written (in order; after a clock back — the new one)
            c.execute("INSERT OR IGNORE INTO meta VALUES ('dev_since', ?)", (str(ts),))
        return self._tx("dev", go)

    def drop_dev(self):
        """The history of activity off (ACT-8): every row of dev_* and their marks go. -> the rows deleted, None —
        the base failed."""
        n = [0]

        def go(c):
            for t in DEV_TABLES + ("dev_node",):
                n[0] += c.execute("DELETE FROM %s" % t).rowcount
            c.execute("DELETE FROM meta WHERE k IN ('dev_flush', 'dev_since')")
        return n[0] if self._tx("dev drop", go) else None

    def forget_dev(self, keep):
        """Only the devices of ``keep`` (the ids of the state) keep their history (§3.2: a device deleted, a .kbk
        restored without it). -> the rows deleted, None — the base failed."""
        keep = set(keep or ())
        n = [0]

        def go(c):
            for t in DEV_TABLES:
                gone = [d for (d,) in c.execute("SELECT DISTINCT dev FROM %s" % t).fetchall() if d not in keep]
                for d in gone:
                    n[0] += c.execute("DELETE FROM %s WHERE dev = ?" % t, (d,)).rowcount
        return n[0] if self._tx("dev forget", go) else None

    # -- thinning, cleaning, the size --
    def maybe_maintain(self, now=None, max_mb=MAX_MB):
        """``maintain`` at most once an hour (by the monotonic clock)."""
        mono = _mono()
        if self._maint is not None and mono - self._maint < MAINT_S:
            return False
        self._maint = mono
        try:
            return self.maintain(now, max_mb)
        except Exception as e:                    # a bad now / max_mb: the tick goes on
            return self._err("maintain", e)

    def maintain(self, now=None, max_mb=MAX_MB):
        """Thin m1 → m10 → h1 (finished buckets only, again and again harmless), drop what is older than kept, keep
        the base under ``max_mb``. Each family — the exits of stage 1, the node, the probes — in a transaction of its
        own: a failure of one (garbage, REV-ns-a4 З-1) stops only it; the cap goes on anyway. True — all went."""
        now = int(time.time() if now is None else now)
        old = time.strftime("%Y-%m-%d", time.gmtime(now - KEEP["traffic"]))

        def family(pre, thin, kept):
            def go(c):
                for src, dst in (("m1", "m10"), ("m10", "h1")):
                    step, back, src, dst = STEP[dst], KEEP[pre + src], pre + src, pre + dst
                    end = now // step * step                   # the bucket of now is not finished
                    done = _num(self.meta("thin_" + dst), int)
                    start = done if done is not None and end - back <= done <= end else end - back
                    thin(c, src, dst, step, start, end)
                    self._set_meta(c, "thin_" + dst, end)
                for t in kept:
                    c.execute("DELETE FROM %s WHERE ts < ?" % t, (now - KEEP[t],))
                if not pre:
                    c.execute("DELETE FROM traffic WHERE day < ?", (old,))
            return go

        ok = True
        for pre, thin, kept in (("", self._thin, ("m1", "m10", "h1", "speed", "svc", "geo", "events")),
                                ("host_", self._thin_host, HOST_TABLES), ("host_ping_", self._thin_ping, PING_TABLES)):
            ok = self._tx(("maintain " + pre.rstrip("_")).strip(), family(pre, thin, kept)) and ok

        def bench(c):                                  # SCHEMA 3: nothing to thin, only the age
            edge = now - KEEP["bench_run"]
            c.execute("DELETE FROM bench WHERE run IN (SELECT id FROM bench_run WHERE ts < ?)", (edge,))
            c.execute("DELETE FROM bench_drop WHERE run IN (SELECT id FROM bench_run WHERE ts < ?)", (edge,))
            c.execute("DELETE FROM bench_run WHERE ts < ?", (edge,))
            c.execute("DELETE FROM bench_drop WHERE ts < ?", (now - KEEP["bench_drop"],))
            c.execute("DELETE FROM bench WHERE ts < ?", (now - KEEP["bench"],))
            c.execute("DELETE FROM bench_epoch WHERE ts < ?", (now - KEEP["bench_epoch"],))
            c.execute("DELETE FROM host_people_h1 WHERE ts < ?", (now - KEEP["host_people_h1"],))
        ok = self._tx("maintain bench", bench) and ok

        def dev(c):                                    # SCHEMA 4: written whole by ``write_dev``, only the age
            for t in DEV_TABLES:
                c.execute("DELETE FROM %s WHERE ts < ?" % t, (now - KEEP[t],))
            for t, step in sorted(DEV_STEP.items()):
                c.execute("DELETE FROM dev_node WHERE step = ? AND ts < ?", (step, now - KEEP[t]))
        ok = self._tx("maintain dev", dev) and ok
        self._cap(now, max_mb)
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        return ok

    @staticmethod
    def _thin(c, src, dst, step, start, end):
        if end <= start:
            return
        c.execute(
            "INSERT OR REPLACE INTO {dst} "
            "SELECT exit, ts / {step} * {step}, AVG(rtt_med), AVG(rtt_p90), "
            "CASE WHEN SUM(n) > 0 THEN SUM(loss * n) / SUM(CASE WHEN loss IS NOT NULL THEN n END) ELSE AVG(loss) END, "
            "AVG(jitter), SUM(ok), SUM(n), SUM(rx), SUM(tx), MAX(devices), "
            "CASE MAX({sev}) WHEN 4 THEN 'off' WHEN 3 THEN 'standby' WHEN 2 THEN 'degraded' WHEN 1 THEN 'ok' "
            "ELSE CASE WHEN COUNT(state) > 0 THEN 'unknown' END END "
            "FROM {src} WHERE ts >= ? AND ts < ? GROUP BY exit, ts / {step}".format(
                dst=dst, src=src, step=step, sev=_SEV), (start // step * step, end))

    @staticmethod
    def _thin_host(c, src, dst, step, start, end):
        """host_m1 → host_m10 → host_h1 by the rules of HOST_COLS (§3.4)."""
        if end <= start:
            return
        c.execute("INSERT OR REPLACE INTO {dst} (ts, {cols}) SELECT ts / {step} * {step}, {aggs} FROM {src} "
                  "WHERE ts >= ? AND ts < ? GROUP BY ts / {step}".format(
                      dst=dst, src=src, step=step, cols=", ".join(n for n, _, _ in HOST_COLS),
                      aggs=", ".join(_agg(n, rule) for n, _, rule in HOST_COLS)), (start // step * step, end))

    @staticmethod
    def _thin_ping(c, src, dst, step, start, end):
        """host_ping_m1 → m10 → h1 per anchor by the rules of PING_COLS; ``via`` — of the newest row that has one."""
        if end <= start:
            return
        last = ("(SELECT s.{c} FROM {src} s WHERE s.anchor = o.anchor AND s.ts >= o.ts / {step} * {step} AND "
                "s.ts < o.ts / {step} * {step} + {step} AND s.{c} IS NOT NULL ORDER BY s.ts DESC LIMIT 1)")
        aggs = [last.format(c=n, src=src, step=step) if rule == "last" else _agg(n, rule) for n, _, rule in PING_COLS]
        c.execute("INSERT OR REPLACE INTO {dst} (anchor, ts, {cols}) SELECT anchor, ts / {step} * {step}, {aggs} "
                  "FROM {src} o WHERE ts >= ? AND ts < ? GROUP BY anchor, ts / {step}".format(
                      dst=dst, src=src, step=step, cols=", ".join(n for n, _, _ in PING_COLS), aggs=", ".join(aggs)),
                  (start // step * step, end))

    def used_bytes(self):
        """Bytes the base holds (pages in use) — the file stays big until VACUUM."""
        try:
            ps = self.conn.execute("PRAGMA page_size").fetchone()[0]
            pc = self.conn.execute("PRAGMA page_count").fetchone()[0]
            fl = self.conn.execute("PRAGMA freelist_count").fetchone()[0]
            return (pc - fl) * ps
        except sqlite3.Error:
            return 0

    def _size(self, t):
        try:
            return self.conn.execute(_CAP_SIZE[t]).fetchone()[0] or 0
        except sqlite3.Error:
            return 0

    def _cap(self, now, max_mb):
        """Above ``max_mb``: cut the oldest tenth of the biggest of h1 / events / host_h1 / host_ping_h1 at a time, not
        past its floor (``CAP_FLOOR``); still above — a warning (the limit is too small for the history kept), the floors stay."""
        cap = float(max_mb or MAX_MB) * 1024 * 1024
        if self.used_bytes() <= cap:
            self._said.discard("cap_small")            # back under it: a new excess is told again (REV-s1-t3b Н-б)
            return False
        cut = 0
        # a tenth of what is left a round, one table a round: 90 days → 7 take ~25 rounds a table, 90 → 30 ~11;
        # 30 a table (120 for the four of SCHEMA 2) reach every floor in one run
        for _i in range(30 * len(CAP_FLOOR)):
            if self.used_bytes() <= cap * 0.9:
                break
            moved = False
            # the history of the checks (bench_run with its bench) goes last: the others first, to their floors
            for t in sorted(CAP_FLOOR, key=lambda t: (t == "bench_run", -self._size(t))):
                floor = now - CAP_FLOOR[t]
                try:
                    with self.conn:
                        lo, hi = self.conn.execute("SELECT MIN(ts), MAX(ts) FROM %s" % t).fetchone()
                        if lo is None or lo >= floor:
                            continue                   # nothing older than its floor
                        edge = min(lo + max(1, (hi - lo) // 10), floor)
                        if t == "bench_run":
                            for dep in ("bench", "bench_drop"):
                                cut += self.conn.execute("DELETE FROM %s WHERE run IN (SELECT id FROM bench_run "
                                                         "WHERE ts < ?)" % dep, (edge,)).rowcount
                        if t == "dev_h1":                  # the hours cut are «no data» again, not «not online»
                            cut += self.conn.execute("DELETE FROM dev_node WHERE step = 3600 AND ts < ?",
                                                     (edge,)).rowcount
                        cut += self.conn.execute("DELETE FROM %s WHERE ts < ?" % t, (edge,)).rowcount
                        moved = True
                except sqlite3.Error as e:
                    return self._err("cap", e)
                break                                  # one table a round: the bigger that can give
            if not moved:
                break
        last = _num(self.meta("vacuum_at"), int)
        if last is None or last > now or now - last >= VACUUM_S:
            try:
                with self.conn:
                    self._set_meta(self.conn, "vacuum_at", now)
                self.conn.execute("VACUUM")
            except sqlite3.Error as e:
                return self._err("vacuum", e)
        still = self.used_bytes() > cap
        # at the floors only the hour that crossed them goes: the line once with the warning, not every hour (REV П2)
        if cut and not (still and "cap_small" in self._said):
            # every table the cap cuts (П-3 круг 2: the owner sees that the history of the checks went too); the start
            # of the text stays as it was — tests and journals look for «host_ping_h1 dropped»
            util.log_action("GW metrics.db: over %s MB, %d old rows of h1/events/host_h1/host_ping_h1 dropped (with "
                            "host_people_h1, bench_run, bench, bench_drop, dev_h1, dev_d1)" % (max_mb or MAX_MB, cut))
        if still and "cap_small" not in self._said:
            self._said.add("cap_small")
            util.log_action("GW metrics.db: still over %s MB — the limit is too small for the history kept "
                            "(h1, host_ping_h1 7 days, host_h1 30 days, events 30 days, host_people_h1 30 days, bench 90 days, "
                            "dev_h1 7 days, dev_d1 90 days at least); raise metrics_max_mb" % (max_mb or MAX_MB))
        return True


def _connect(path, ro=False):
    if ro:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2.0)
    else:
        conn = sqlite3.connect(path, timeout=2.0)
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def open_writer(path=None):
    """The writer of the collector -> Writer | None (no sqlite3, the base will not open, another writer holds it)."""
    if sqlite3 is None:
        return None
    path = path or paths.metrics_db()
    try:
        if not os.path.isdir(os.path.dirname(path)):   # a missing one 0700; /var/lib/kaskad keeps its mode
            util.ensure_dir(os.path.dirname(path), 0o700)
        fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        util.log_action("GW metrics.db: does not open, charts off: %s" % e)
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None                                # the one writer is somebody else
    conn = None
    try:
        conn = _connect(path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        with conn:
            # one transaction for the whole way up: the module would run each CREATE on its own (autocommit)
            conn.execute("BEGIN IMMEDIATE")
            for q in _DDL:
                conn.execute(q)
            # the number only grows (§3.4, REV С-7): a base of stage 1 gets 2, one of a newer code keeps its own
            was = conn.execute("SELECT v FROM meta WHERE k = 'schema'").fetchone()
            was = _num(was[0], int) if was else None
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (str(max(was or 0, SCHEMA)),))
            conn.execute("INSERT OR IGNORE INTO meta VALUES ('created', ?)", (str(int(time.time())),))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('tz', ?)", (tz.name(),))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return Writer(conn, fd, path)
    except sqlite3.Error as e:
        util.log_action("GW metrics.db: does not open, charts off: %s" % e)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        os.close(fd)
        return None


def _reader():
    """A read-only connection for one request of the panel; None — no sqlite3 or no base."""
    if sqlite3 is None:
        return None
    path = paths.metrics_db()
    if not os.path.exists(path):
        return None
    try:
        return _connect(path, ro=True)
    except sqlite3.Error:
        return None


def reader():
    """A read-only connection (``file:…?mode=ro``) for one request; None — no sqlite3 or no base. The caller closes it."""
    return _reader()


def history(eid, rng="1d", now=None):
    """``GET /api/gw/exits/<id>/history`` (§7.9): {charts, step, cols, points: [[ts, …]], states: [[from, to, state]]},
    at most 1440 points; the table by the range (1d — m1, 7d — m10, 90d — h1 in buckets of 2 h); ``mbps`` — the
    speed tests of a bucket (null when none). A repeated key of the pool («e-…-201», ``policy.repeat_id``) has no
    history of its own: no point (the ones 3.3.7.6–3.3.7.10 wrote are not shown, T-3379 Н-3)."""
    span, table = RANGES.get(rng, RANGES["1d"])
    step = STEP[table]
    while span // step > MAX_POINTS:
        step *= 2
    cols = ["rtt_med", "rtt_p90", "loss", "jitter", "rx", "tx", "devices", "mbps"]
    out = {"charts": False, "step": step, "cols": cols, "points": [], "states": []}
    conn = _reader()
    if conn is None:
        return out
    if repeat_id(eid):
        _close(conn)
        out["charts"] = True
        return out
    now = int(time.time() if now is None else now)
    lo = now - span
    try:
        rows = conn.execute(
            "SELECT ts / {s} * {s} AS b, AVG(rtt_med), AVG(rtt_p90), AVG(loss), AVG(jitter), SUM(rx), SUM(tx), "
            "MAX(devices), MAX({sev}) FROM {t} WHERE exit = ? AND ts >= ? AND ts <= ? GROUP BY b ORDER BY b".format(
                s=step, t=table, sev=_SEV), (eid, lo, now)).fetchall()
        speed = dict(conn.execute(
            "SELECT ts / {s} * {s} AS b, AVG(mbps) FROM speed WHERE exit = ? AND ok = 1 AND ts >= ? AND ts <= ? "
            "GROUP BY b".format(s=step), (eid, lo, now)).fetchall())
    except sqlite3.Error:
        conn.close()
        return out
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    out["charts"] = True
    names = ("unknown", "ok", "degraded", "standby", "off")
    have = set()
    for r in rows:
        b = r[0]
        have.add(b)
        out["points"].append([b] + [_round(v) for v in r[1:8]] + [_round(speed.get(b))])
        st = names[r[8]] if r[8] is not None else None
        if st is None:
            continue
        if out["states"] and out["states"][-1][2] == st and out["states"][-1][1] == b:
            out["states"][-1][1] = b + step
        else:
            out["states"].append([b, b + step, st])
    for b in sorted(set(speed) - have):            # a speed test in a bucket without a point
        out["points"].append([b, None, None, None, None, None, None, None, _round(speed[b])])
    out["points"].sort(key=lambda p: p[0])
    out["points"] = out["points"][-MAX_POINTS:]
    return out


EXA_STEP, EXA_N, EXA_MAX = 1800, 48, 500           # the summary of «Выходы»: 48 windows of 30 min, ≤ 500 exits


def exit_summary(now=None):
    """The line of «Выходы» (3.3.7.10, SPEC-activity «кардиограмма и для выходов»): the last 24 h of every exit of m1
    in 48 windows of 30 min of the clock — ONE query for all exits instead of the history of each (``history``: 1440
    points an exit). -> {charts, step, t0, n, now, exits: {id: {kb: [KiB], nd: [0|1], st: [0–3], rx, tx, off, deg}}}.
    ``kb`` — rx + tx of a window; ``nd`` 1 — no point of the collector with counters in it (the key added later, the
    collector stopped); ``st`` — the worst state of its minutes (1 works, 2 degraded, 3 down; standby and unknown 0);
    ``off`` / ``deg`` — seconds «down» / «degraded» of the 24 h (a minute of m1 is 60 s). ``now`` — the end shown:
    the last window is the part of the window up to it. No sqlite3 or no base -> {charts: False}. A repeated key
    («e-…-201», ``policy.repeat_id``) is left out (T-3379 Н-3)."""
    now = int(time.time() if now is None else now)
    end = now // EXA_STEP * EXA_STEP
    t0 = end - (EXA_N - 1) * EXA_STEP
    out = {"charts": False, "step": EXA_STEP, "t0": t0, "n": EXA_N, "now": now, "exits": {}}
    conn = _reader()
    if conn is None:
        return out
    try:
        rows = conn.execute(
            "SELECT exit, (ts - ?) / ?, SUM(rx), SUM(tx), SUM(rx IS NOT NULL OR tx IS NOT NULL), "
            "MAX(CASE state WHEN 'off' THEN 3 WHEN 'degraded' THEN 2 WHEN 'ok' THEN 1 ELSE 0 END), "
            "SUM(state = 'off'), SUM(state = 'degraded') FROM m1 WHERE ts >= ? AND ts < ? GROUP BY exit, (ts - ?) / ?",
            (t0, EXA_STEP, t0, now, t0, EXA_STEP)).fetchall()
    except sqlite3.Error:
        return out
    finally:
        _close(conn)
    out["charts"] = True
    by = out["exits"]
    for eid, i, rx, tx, has, st, off, deg in rows:
        if not isinstance(eid, str) or repeat_id(eid) or not 0 <= i < EXA_N or (eid not in by and len(by) >= EXA_MAX):
            continue
        e = by.setdefault(eid, {"kb": [0] * EXA_N, "nd": [1] * EXA_N, "st": [0] * EXA_N, "rx": 0, "tx": 0, "off": 0,
                                "deg": 0})
        rx, tx = int(rx or 0), int(tx or 0)
        e["kb"][i] = int(round((rx + tx) / 1024.0))
        e["nd"][i] = 0 if has else 1
        e["st"][i] = int(st or 0)
        e["rx"] += rx
        e["tx"] += tx
        e["off"] += 60 * int(off or 0)
        e["deg"] += 60 * int(deg or 0)
    return out


EXH_N = 60                                         # «Выходы · час» на Сводке: 60 минут m1


def exit_hour(now=None):
    """«Выходы · час» on the Сводка (круг 1, Т1): the last 60 minutes of m1 of every exit by the minute — ONE query for
    all exits (``history`` gives 1440 points of one exit). -> {charts, step: 60, t0, n: 60, now, exits: {id: {rtt:
    [ms | null], loss: [% | null], mbps: [Мбит/с | null], st: [0–3]}}}: ``rtt`` — rtt_med, ``loss`` — loss of the
    minute, ``mbps`` — (rx + tx) · 8 / 60 s / 10⁶; ``st`` — 1 works, 2 degraded, 3 down, 0 standby / unknown / no
    row. Minute i covers [t0 + 60 i, t0 + 60 (i + 1)); the last one is the minute ``now`` is in (part of it). A
    repeated key («e-…-201», ``policy.repeat_id``) is left out; at most EXA_MAX exits. No sqlite3 or no base ->
    {charts: False}."""
    now = int(time.time() if now is None else now)
    t0 = (now // 60 - (EXH_N - 1)) * 60
    out = {"charts": False, "step": 60, "t0": t0, "n": EXH_N, "now": now, "exits": {}}
    conn = _reader()
    if conn is None:
        return out
    try:
        rows = conn.execute(
            "SELECT exit, (ts - ?) / 60, AVG(rtt_med), AVG(loss), SUM(rx), SUM(tx), SUM(rx IS NOT NULL OR tx IS NOT "
            "NULL), MAX(CASE state WHEN 'off' THEN 3 WHEN 'degraded' THEN 2 WHEN 'ok' THEN 1 ELSE 0 END) FROM m1 "
            "WHERE ts >= ? AND ts <= ? GROUP BY exit, (ts - ?) / 60", (t0, t0, now, t0)).fetchall()
    except sqlite3.Error:
        return out
    finally:
        _close(conn)
    out["charts"] = True
    by = out["exits"]
    for eid, i, rtt, loss, rx, tx, has, st in rows:
        if not isinstance(eid, str) or repeat_id(eid) or not 0 <= i < EXH_N or (eid not in by and len(by) >= EXA_MAX):
            continue
        e = by.setdefault(eid, {"rtt": [None] * EXH_N, "loss": [None] * EXH_N, "mbps": [None] * EXH_N,
                                "st": [0] * EXH_N})
        e["rtt"][i] = round(rtt, 1) if _finite(rtt) and rtt >= 0 else None
        e["loss"][i] = round(loss, 2) if _finite(loss) and 0 <= loss <= 100 else None
        b = (rx or 0) + (tx or 0)
        e["mbps"][i] = round(b * 8 / 60.0 / 1e6, 3) if has and _finite(b) and b >= 0 else None
        e["st"][i] = int(st or 0)
    return out


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _round(v):
    return round(v, 3) if isinstance(v, float) else v


def _span(rng, now):
    """(lo, now, step, table suffix) of a range, the step doubled until at most MAX_POINTS points."""
    span, table = RANGES.get(rng, RANGES["1d"])
    step = STEP[table]
    while span // step > MAX_POINTS:
        step *= 2
    now = int(time.time() if now is None else now)
    return now - span, now, step, table


def _rows(sql, args):
    """Rows of a read-only query; None — no sqlite3, no base, or the table is not there (a base of stage 1)."""
    conn = _reader()
    if conn is None:
        return None
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def host_history(rng="1d", now=None):
    """The history of the node for the panel (§3.4): {charts, step, cols, points: [[ts, …]]} — ``cols`` are the
    columns of HOST_COLS, a point groups its rows by their rules; 1d — host_m1, 7d — host_m10, 90d — host_h1 in
    buckets of 2 h; at most MAX_POINTS points."""
    lo, now, step, table = _span(rng, now)
    cols = [n for n, _, _ in HOST_COLS]
    out = {"charts": False, "step": step, "cols": cols, "points": []}
    rows = _rows("SELECT ts / {s} * {s} AS b, {aggs} FROM host_{t} WHERE ts >= ? AND ts <= ? GROUP BY b "
                 "ORDER BY b".format(s=step, t=table, aggs=", ".join(_agg(n, r) for n, _, r in HOST_COLS)), (lo, now))
    if rows is None:
        return out
    out["charts"] = True
    out["points"] = [[r[0]] + [_round(v) for v in r[1:]] for r in rows][-MAX_POINTS:]
    return out


def ping_history(rng="1d", now=None):
    """The light probes for the panel (§3.4): {charts, step, anchors: {addr: [[ts, n, lost, rtt_med, jit]]}} — the
    node is the anchor "*"; the tables and the step as in ``host_history``, at most MAX_POINTS points an anchor."""
    lo, now, step, table = _span(rng, now)
    out = {"charts": False, "step": step, "anchors": {}}
    rule = dict((n, r) for n, _, r in PING_COLS)
    rows = _rows("SELECT anchor, ts / {s} * {s} AS b, {aggs} FROM host_ping_{t} WHERE ts >= ? AND ts <= ? "
                 "GROUP BY anchor, b ORDER BY anchor, b".format(
                     s=step, t=table, aggs=", ".join(_agg(n, rule[n]) for n in PING_READ)), (lo, now))
    if rows is None:
        return out
    out["charts"] = True
    for r in rows:
        out["anchors"].setdefault(r[0], []).append([r[1]] + [_round(v) for v in r[2:]])
    for a in out["anchors"]:
        out["anchors"][a] = out["anchors"][a][-MAX_POINTS:]
    return out


def transitions(eid, days=7, limit=100, before=None, now=None):
    """``GET /api/gw/exits/<id>/transitions`` (§7.9): {db, items: [{ts, from, to, why, args}], next}, the newest first;
    ``next`` — the ``before`` of the next page (exclusive), None at the end. The cursor is a second: a second is never
    split between pages — when one second fills the whole page, the page takes all of it (beyond ``limit``)."""
    days = min(max(int(days or 7), 1), 90)
    limit = min(max(int(limit or 100), 1), 500)
    out = {"db": False, "items": [], "next": None}
    conn = _reader()
    if conn is None:
        return out
    now = int(time.time() if now is None else now)
    top = int(before) if before is not None else now + 1
    try:
        rows = conn.execute("SELECT ts, args FROM events WHERE key = 'transition' AND exit = ? AND ts >= ? AND ts < ? "
                            "ORDER BY ts DESC, rowid DESC LIMIT ?", (eid, now - days * DAY, top, limit + 1)).fetchall()
    except sqlite3.Error:
        return out
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    out["db"] = True
    more = len(rows) > limit
    if more:
        edge = rows[limit][0]                      # the first row of the next page
        cut = [r for r in rows[:limit] if r[0] != edge]
        if rows[limit - 1][0] != edge:
            rows, out["next"] = rows[:limit], rows[limit - 1][0]
        elif cut:                                  # a second split by the page: all of it goes to the next one
            rows, out["next"] = cut, edge + 1
        else:                                      # one second fills the page: take it whole
            rows = _second(eid, edge)
            if rows is None:
                return {"db": False, "items": [], "next": None}
            out["next"] = edge if _older(eid, now - days * DAY, edge) else None
    for ts, text in rows:
        try:
            a = json.loads(text or "{}")
        except ValueError:
            a = {}
        a = a if isinstance(a, dict) else {}
        out["items"].append({"ts": ts, "from": a.pop("from", None), "to": a.pop("to", None),
                             "why": a.pop("why", None), "args": a})
    return out


def _second(eid, ts):
    """All transitions of ``eid`` in second ``ts``, the newest first; None — the base did not answer."""
    conn = _reader()
    if conn is None:
        return None
    try:
        return conn.execute("SELECT ts, args FROM events WHERE key = 'transition' AND exit = ? AND ts = ? "
                            "ORDER BY rowid DESC", (eid, ts)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def _older(eid, lo, ts):
    conn = _reader()
    if conn is None:
        return False
    try:
        return conn.execute("SELECT 1 FROM events WHERE key = 'transition' AND exit = ? AND ts >= ? AND ts < ? "
                            "LIMIT 1", (eid, lo, ts)).fetchone() is not None
    except sqlite3.Error:
        return False
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass


def feed(since=0, limit=500):
    """Published events (not transitions) newer than ``since`` — the feed of the panel after a reboot (§3.6):
    [{ts, key, exit, args}], the oldest first; None — no base. ``limit`` 1–500."""
    try:
        limit = min(max(int(limit), 1), 500)
    except (TypeError, ValueError):
        limit = 500
    conn = _reader()
    if conn is None:
        return None
    try:
        rows = conn.execute("SELECT ts, key, exit, args FROM (SELECT rowid AS r, * FROM events WHERE key != "
                            "'transition' AND ts > ? ORDER BY ts DESC, r DESC LIMIT ?) ORDER BY ts, r",
                            (int(since or 0), int(limit))).fetchall()
    except sqlite3.Error:
        return None
    finally:
        try:
            conn.close()
        except sqlite3.Error:
            pass
    out = []
    for ts, key, eid, text in rows:
        try:
            a = json.loads(text or "{}")
        except ValueError:
            a = {}
        out.append({"ts": ts, "key": key, "exit": eid, "args": a if isinstance(a, dict) else {}})
    return out


# ── the activity of devices for the panel, the CLI and MCP (SCHEMA 4, SPEC-activity §5) ──

def _dev_conn():
    """(connection, None) to read dev_*; (None, why) — ``nosqlite`` (no module) or ``nodb`` (no base yet)."""
    if sqlite3 is None:
        return None, "nosqlite"
    conn = _reader()
    return (conn, None) if conn is not None else (None, "nodb")


def _close(conn):
    try:
        conn.close()
    except sqlite3.Error:
        pass


def _dev_meta(conn):
    """(dev_flush, dev_since) — ints or None; a base without them (older, or nothing written yet) — None."""
    try:
        got = dict(conn.execute("SELECT k, v FROM meta WHERE k IN ('dev_flush', 'dev_since')").fetchall())
    except sqlite3.Error:
        return None, None
    return _num(got.get("dev_flush"), int), _num(got.get("dev_since"), int)


def dev_flush():
    """``meta.dev_flush`` — the stamp of the newest window written (the key of the cache and the ETag of the panel);
    None — no base, no window yet, no sqlite3."""
    conn, _why = _dev_conn()
    if conn is None:
        return None
    try:
        return _dev_meta(conn)[0]
    finally:
        _close(conn)


def dev_anchor(flush, now):
    """The end of what the readers show: the end of the last window written while the collector writes (it is at
    most a quarter of an hour behind), else the 5-minute mark of ``now`` — the windows after it say «нет данных» (no
    round of the collector, ACT-12); a stamp from the future (the clock went back) is not taken."""
    now = int(now)
    end = None if flush is None else flush + 300
    if end is not None and 0 <= now - end < 900:
        return end
    return now // 300 * 300


def _cover(secs, span):
    """The share of ``span`` s the collector saw, in tenths 0–10: anything above none is at least 1."""
    if not secs or span <= 0:
        return 0
    return max(1, min(10, int(round(10.0 * secs / span))))


_READ = object()


def dev_summary(devs, now=None, flush=_READ):
    """The summary of «Люди» (§5.1): the last 24 h in 48 windows of 30 min for the devices ``devs`` (the ids of the
    state — no other id ever comes out). -> {activity, step, t0, n, now, since, cover, devices} or
    {activity: False, why: "nosqlite"} — no sqlite3. A device: {on: [0–10], kb: [KiB], rx, tx, on_s, act_s, last}
    for the 24 h; only {last} — nothing in 24 h but «на связи» within 30 days; none — nothing in 30 days.
    ``flush`` — ``dev_flush()`` the caller read already (its cache key): the end shown is the same."""
    now = int(time.time() if now is None else now)
    step, n = DEV_SUM_STEP, DEV_SUM_N
    conn, why = _dev_conn()
    if why == "nosqlite":
        return {"activity": False, "why": "nosqlite"}
    got, since = (None, None) if conn is None else _dev_meta(conn)
    anchor = dev_anchor(got if flush is _READ else flush, now)
    t0 = (anchor - 1) // step * step - (n - 1) * step
    out = {"activity": True, "step": step, "t0": t0, "n": n, "now": anchor, "since": None, "cover": [0] * n,
           "devices": {}}
    if conn is None:
        return out
    devs = set(devs or ())
    try:
        rows = conn.execute("SELECT dev, (ts - ?) / ?, SUM(on_s), SUM(act_s), SUM(rx), SUM(tx) FROM dev_m5 "
                            "WHERE ts >= ? AND ts < ? GROUP BY dev, (ts - ?) / ?",
                            (t0, step, t0, anchor, t0, step)).fetchall()
        cov = conn.execute("SELECT (ts - ?) / ?, SUM(secs) FROM dev_node WHERE step = 300 AND ts >= ? AND ts < ? "
                           "GROUP BY (ts - ?) / ?", (t0, step, t0, anchor, t0, step)).fetchall()
        last5 = dict(conn.execute("SELECT dev, MAX(ts) + 300 FROM dev_m5 WHERE on_s > 0 AND ts < ? GROUP BY dev",
                                  (anchor,)).fetchall())
        last1 = dict(conn.execute("SELECT dev, MAX(ts) + 3600 FROM dev_h1 WHERE on_s > 0 AND ts >= ? AND ts < ? "
                                  "GROUP BY dev", (now - 30 * DAY, t0)).fetchall())
        first = conn.execute("SELECT MIN(ts) FROM dev_node WHERE step = 300").fetchone()
    except sqlite3.Error:
        return out
    finally:
        _close(conn)
    out["since"] = first[0] if first and first[0] is not None else since
    span = [min(step, anchor - (t0 + i * step)) for i in range(n)]
    for i, secs in cov:
        if 0 <= i < n:
            out["cover"][i] = _cover(secs, span[i])
    by = {}
    for dev, i, on, act, rx, tx in rows:
        if dev not in devs or not 0 <= i < n:
            continue
        d = by.setdefault(dev, {"on": [0] * n, "kb": [0] * n, "rx": 0, "tx": 0, "on_s": 0, "act_s": 0})
        d["on"][i] = _cover(on, span[i])
        d["kb"][i] = int(round(((rx or 0) + (tx or 0)) / 1024.0))
        d["rx"] += rx or 0
        d["tx"] += tx or 0
        d["on_s"] += on or 0
        d["act_s"] += act or 0
    for dev in devs:
        seen = [v for v in (last5.get(dev), last1.get(dev)) if v is not None]
        last = max(seen) if seen else None
        if dev in by:
            out["devices"][dev] = dict(by[dev], last=last)
        elif last is not None:
            out["devices"][dev] = {"last": last}
    return out


def day_marks(t0, step, n, z=None):
    """[[ts, 'YYYY-MM-DD'], …] — the days of the node (ACT-14: ``tz.zone()``) of the points ``t0 + i·step``, i = 0…n
    (one more: the panel adds the open window and may move the series by a step): the stamp of the first point of
    every day and the day. A point belongs to the last mark not after it — as ``kaskadctl gw activity`` counts."""
    from .. import tz
    z = z or tz.zone()
    out = []
    for i in range(n + 1):
        ts = t0 + i * step
        d = tz.day(ts, z)
        if not out or out[-1][1] != d:
            out.append([ts, d])
    return out


def dev_history(devs, rng="1d", now=None, per_device=False, z=None):
    """A device or a person (§5.2): ``devs`` — the ids (of the state), ``rng`` — 1d (dev_m5, 288 × 5 min) | 7d (dev_h1,
    168 × 1 h) | 30d (dev_h1, 720 × 1 h). -> {step, t0, n, on_s, act_s, rx, tx, cover, since, now} — arrays without stamps
    (a point ``i`` is ``t0 + i·step``); several devices — bytes summed, seconds the most of them (ACT-11: «на связи»
    of a person is at least the most of its devices); ``per_device`` — also ``devices``: [{id, rx, tx, on_s, act_s}]
    of the period; 30d — also ``days`` (``day_marks``: the days of «30 дней» by the zone of the node ``z``, not of the
    browser, ACT-14). None — no sqlite3."""
    table, step, n = DEV_RANGES[rng]
    now = int(time.time() if now is None else now)
    conn, why = _dev_conn()
    if why == "nosqlite":
        return None
    flush = None if conn is None else _dev_meta(conn)[0]
    anchor = dev_anchor(flush, now)
    t0 = (anchor - 1) // step * step - (n - 1) * step
    out = {"step": step, "t0": t0, "n": n, "on_s": [0] * n, "act_s": [0] * n, "rx": [0] * n, "tx": [0] * n,
           "cover": [0] * n, "since": None, "now": anchor}       # now — the end shown (3.3.7.10: the panel adds the open window)
    if rng == "30d":
        out["days"] = day_marks(t0, step, n, z)
    if per_device:
        out["devices"] = []
    devs = sorted(set(devs or ()))[:DEV_IN_MAX]
    if conn is None:
        return out
    marks = ",".join("?" * len(devs))
    try:
        rows = conn.execute("SELECT (ts - ?) / ?, SUM(rx), SUM(tx), MAX(on_s), MAX(act_s) FROM %s WHERE dev IN (%s) "
                            "AND ts >= ? AND ts < ? GROUP BY ts" % (table, marks),
                            [t0, step] + devs + [t0, anchor]).fetchall() if devs else []
        per = conn.execute("SELECT dev, SUM(rx), SUM(tx), SUM(on_s), SUM(act_s) FROM %s WHERE dev IN (%s) AND "
                           "ts >= ? AND ts < ? GROUP BY dev" % (table, marks),
                           devs + [t0, anchor]).fetchall() if devs and per_device else []
        cov = conn.execute("SELECT (ts - ?) / ?, secs FROM dev_node WHERE step = ? AND ts >= ? AND ts < ?",
                           (t0, step, step, t0, anchor)).fetchall()
        first = conn.execute("SELECT MIN(ts) FROM dev_node WHERE step = ?", (step,)).fetchone()
    except sqlite3.Error:
        return out
    finally:
        _close(conn)
    out["since"] = first[0] if first else None
    for i, rx, tx, on, act in rows:
        if 0 <= i < n:
            out["rx"][i], out["tx"][i] = rx or 0, tx or 0
            out["on_s"][i], out["act_s"][i] = on or 0, act or 0
    for i, secs in cov:
        if 0 <= i < n:
            out["cover"][i] = _cover(secs, min(step, anchor - (t0 + i * step)))
    if per_device:
        out["devices"] = [{"id": d, "rx": rx or 0, "tx": tx or 0, "on_s": on or 0, "act_s": act or 0}
                          for d, rx, tx, on, act in per]
    return out


def dev_info():
    """For «Настройки › Хранение данных» (SPEC-activity §6.4, Н1): {db, since, flush, bytes} — bytes held by dev_*
    (roughly, as the cap counts them); db False — no sqlite3 or no base."""
    conn, _why = _dev_conn()
    out = {"db": False, "since": None, "flush": None, "bytes": 0}
    if conn is None:
        return out
    try:
        flush, since = _dev_meta(conn)
        first = conn.execute("SELECT MIN(ts) FROM dev_node").fetchone()
        rows = sum(conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in DEV_TABLES)
        nodes = conn.execute("SELECT COUNT(*) FROM dev_node").fetchone()[0]
    except sqlite3.Error:
        return out
    finally:
        _close(conn)
    return {"db": True, "since": first[0] if first and first[0] is not None else since, "flush": flush,
            "bytes": _dev_bytes(rows, nodes)}


def _dev_bytes(rows, nodes):
    """Bytes dev_* hold, roughly (as the cap counts them): ``rows`` of dev_m5/h1/d1, ``nodes`` of dev_node."""
    return rows * 48 + nodes * 24


def wipe_dev(at, path=None):
    """«Удалить историю активности» (№41) while it is kept: every row of dev_* and their marks go, ``meta.dev_wiped`` =
    ``at`` — ONE transaction (BEGIN IMMEDIATE) on a connection of its own (the collector keeps its writer open: one
    short write beside it, sqlite takes turns; a transaction of the collector — waited up to WIPE_WAIT s). A base that
    is not there is not made (``mode=rw``). After it the collector writes no window that began at or before ``at``
    (``Writer.write_dev``) and drops such windows from its accumulator (``kaskad.activity.wipe``).
    -> ({"rows", "nodes", "bytes"} deleted, None) or (None, why): "nosqlite", "nodb", "busy" (the base locked or
    failing — nothing deleted)."""
    if sqlite3 is None:
        return None, "nosqlite"
    path = path or paths.metrics_db()
    if not os.path.exists(path):
        return None, "nodb"
    import urllib.parse
    try:
        conn = sqlite3.connect("file:%s?mode=rw" % urllib.parse.quote(path), uri=True, timeout=WIPE_WAIT,
                               isolation_level=None)
    except sqlite3.Error:
        return None, "nodb"
    try:
        conn.execute("PRAGMA busy_timeout=%d" % int(WIPE_WAIT * 1000))
        conn.execute("BEGIN IMMEDIATE")
        try:
            have = set(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall())
            if "meta" not in have:
                conn.execute("ROLLBACK")
                return None, "nodb"
            rows = sum(conn.execute("DELETE FROM %s" % t).rowcount for t in DEV_TABLES if t in have)
            nodes = conn.execute("DELETE FROM dev_node").rowcount if "dev_node" in have else 0
            conn.execute("DELETE FROM meta WHERE k IN ('dev_flush', 'dev_since')")
            conn.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (DEV_WIPED, str(int(at))))
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    except sqlite3.Error as e:
        util.log_action("GW metrics.db: wipe of the activity failed: %s" % e)
        return None, "busy"
    finally:
        _close(conn)
    return {"rows": rows, "nodes": nodes, "bytes": _dev_bytes(rows, nodes)}, None
