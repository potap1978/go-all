"""The data of ``GET /api/host/score`` and ``GET /api/host/day/<YYYY-MM-DD>`` (k3/handoff/SPEC-node-score.md §6.2 B6,
C6; SPEC-node-score-schedule-share.md §4): the node against its OWN history — the raw numbers, «обычно», ▲/▼, the
relative score ``rel`` / ``rel_total`` (NS-D30, NS-D31). No absolute score, band, capacity, «годится для…» or budget of
the month (the owner 23.10, NS-D34).

Every rule is ``kaskad.score`` (view, day_color, day_devs, tested_links, offline_minutes, loss_value); here —
the shapes only. Pure (rows and the clock as parameters, no gw/panel): ``probe_inputs`` (the light probes of
host_ping_h1 / the steal of host_m10 → ``probes`` windows and ``probe_days`` of ``score.view``), ``day_series`` (96
points of 15 min), ``day_metrics``, ``offline_spans``, ``valid_day``. Wiring: ``read`` (metrics.db by a read-only
connection), ``score_doc``, ``day_doc`` (the checks by ``bench.history.sample`` — the base and the queue, the stored
events bench_drop; the line «Связь с людьми» by ``gw.hostpeople.summary``, not in the score, NS-D26).

Loss (NS-S10): the counts of every anchor (n, lost, runs as the events) summed per window / day → ``loss_value``
(«seen by 2 of 3»); without anchor rows — the node row ``*``. Delay and jitter — the node row ``*``; the collector
writes only the loss there (``gw.hostmon.ping_rows``), so ``read`` fills its empty rtt / jitter from the anchor rows of
the same ts by the rule of that row (``node_fill``, круг 1 Н-4: «Отклик» / «Дрожание» stood «ждём проб» for good).
"""

import bisect
import calendar
import datetime
import math
import re
import time

from .. import score as S
from .. import tz
from ..i18n import fmt_num, lookup, tr

DAY = 86400
STRIP_DAYS = 30
M10_KEEP = 14 * DAY                       # host_m10 / host_ping_m10 (gw.metrics.KEEP)
SPAN = (STRIP_DAYS + S.PROBE_BASE_DAYS + 5) * DAY   # rows read back: the strip + the base of the probes + the windows
NODE = "*"
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")     # ASCII digits only: \d takes any Unicode digit
LINK_FIELDS = ("id", "source", "rel", "test", "fresh_raw", "fresh_n", "unit", "usual_raw", "usual_n", "usual_mode",
               "p25_raw", "p75_raw", "now_id", "now_day", "dev", "dev_test", "dev_pending", "dev_pending_kind", "parts",
               "why", "why_text")
DAY_LINK_FIELDS = ("id", "rel", "test", "fresh_raw", "unit", "usual_raw", "usual_n", "now_day", "dev")
RAW_FIELDS = ("fresh_raw", "usual_raw", "p25_raw", "p75_raw")     # rounded as the series (3 decimals)
SERIES_TESTS = ("net_down", "net_up", "rtt", "jitter", "loss", "chacha_1t", "aes_1t", "cpu_mt")   # «series» (UI-C)
SERIES_PROBES = ("rtt", "jitter", "loss")            # … of them the daily values of the probes; the rest — of the checks
DAY_M = ("rtt", "rtt_p90", "loss", "loss_peak", "jit", "off")       # days[].m: the numbers of the hint of a day
DAY_CHECKS_MAX = 8                                   # days[].checks: the last 8 checks of a day


# ── pure ───────────────────────────────────────────────────────────────────

def valid_day(s, today):
    """``YYYY-MM-DD`` of a real date within the strip (today − 29 … today), else None."""
    if not isinstance(s, str) or not DATE.fullmatch(s):
        return None
    try:
        d = datetime.date(int(s[:4]), int(s[5:7]), int(s[8:]))
        t = datetime.date(int(today[:4]), int(today[5:7]), int(today[8:]))
    except ValueError:
        return None
    return s if 0 <= (t - d).days < STRIP_DAYS else None


def days_back(today, n=STRIP_DAYS):
    t = datetime.date(int(today[:4]), int(today[5:7]), int(today[8:]))
    return [(t - datetime.timedelta(days=i)).isoformat() for i in range(n - 1, -1, -1)]


def day_span(day, z=None):
    """[start, end) UTC of the local day."""
    d = datetime.date(int(day[:4]), int(day[5:7]), int(day[8:]))
    n = d + datetime.timedelta(days=1)
    return tz.to_utc(d.year, d.month, d.day, 0, 0, z), tz.to_utc(n.year, n.month, n.day, 0, 0, z)


def _wall0(day):
    return calendar.timegm((int(day[:4]), int(day[5:7]), int(day[8:]), 0, 0, 0, 0, 0, 0))


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _wavg(rows, col, wcol="n"):
    s = w = 0.0
    for r in rows:
        v, k = _num(r.get(col)), _num(r.get(wcol))
        if v is not None and k:
            s, w = s + v * k, w + k
    return s / w if w else None


def p95(xs):
    xs = sorted(x for x in (_num(v) for v in xs) if x is not None)
    return xs[max(0, int(math.ceil(0.95 * len(xs))) - 1)] if xs else None


def _r(x, d=3):
    return None if x is None else round(x, d)


def _loss(rows):
    """{anchor: {n, k, e}} of ping rows (anchors first, the node row only without them)."""
    out = {}
    use = [r for r in rows if r.get("anchor") != NODE] or [r for r in rows if r.get("anchor") == NODE]
    for r in use:
        n = _num(r.get("n")) or 0
        if n <= 0:
            continue
        s = out.setdefault(r["anchor"], {"n": 0.0, "k": 0.0, "e": 0.0})
        s["n"] += n
        s["k"] += _num(r.get("lost")) or 0
        s["e"] += _num(r.get("runs")) or 0
    return out


NODE_FILL = (("rtt_med", None), ("rtt_p90", None), ("jit", "jit_n"))


def node_fill(rows):
    """Rows of host_ping_h1 / host_ping_m10 (any order) -> the same rows, a node row ``*`` with no rtt_med / rtt_p90 /
    jit taking it from the anchor rows of its ts (n > 0, the value there) by the rule of the node row of the collector
    (``gw.hostmon._reduce``, §2 loss: the worst ⌊(k−1)/2⌋ dropped, the mean of the rest; 2 — the better; 1 — it);
    ``jit_n`` of a filled jitter — the sum of those anchors (the weight of the window, ``min_n`` of the test). A value
    of the row itself is kept; the stored rows are not changed (new dicts), the base is read only."""
    from ..gw.hostmon import _reduce
    by_ts = {}
    for r in rows:
        if r.get("anchor") != NODE and (_num(r.get("n")) or 0) > 0:
            by_ts.setdefault(r.get("ts"), []).append(r)
    out = []
    for r in rows:
        anchors = by_ts.get(r.get("ts")) if r.get("anchor") == NODE else None
        if anchors and any(_num(r.get(c)) is None for c, _ in NODE_FILL):
            r = dict(r)
            for col, wcol in NODE_FILL:
                if _num(r.get(col)) is not None:
                    continue
                use = [a for a in anchors if _num(a.get(col)) is not None and (wcol is None or (_num(a.get(wcol))
                                                                                                  or 0) > 0)]
                if use:
                    r[col] = _reduce([_num(a.get(col)) for a in use])
                    if wcol:
                        r[wcol] = sum(_num(a.get(wcol)) for a in use)
        out.append(r)
    return out


def _probe_vals(ping, steal, canary=()):
    """The values of the probe tests over a set of rows -> ({test: value} (only those with data), hours of probes,
    quiet hours of the canary); canary — [(ts, canary_ms, busy)] of host_m10 (C6: the CV of the 10-min samples,
    score.canary_cv; REV-ns-c6 З-2)."""
    node = [r for r in ping if r.get("anchor") == NODE]
    hours = len(set(r["ts"] for r in node if (_num(r.get("n")) or 0) > 0))
    out = {}
    rtt = _wavg(node, "rtt_med")
    if rtt is not None:
        out["rtt"] = rtt
    jit = _wavg(node, "jit", "jit_n")
    if jit is not None:
        out["jitter"] = {"v": jit, "n": sum(_num(r.get("jit_n")) or 0 for r in node)}
    st = p95(steal)
    if st is not None:
        out["steal_p95"] = st
    cv, can_n = S.canary_cv(canary)
    if cv is not None:
        out["cpu_canary_cv"] = cv
    return out, hours, can_n


def probe_inputs(ping, steal, now, day_of, upto=None, canary=()):
    """ping — rows of host_ping_h1 {anchor, ts, n, lost, runs, rtt_med, jit, jit_n}; steal — [(ts, steal %)] (host_m10,
    older — host_h1); canary — [(ts, canary_ms, busy)] of host_m10, its rows without a canary too (C6). -> (windows,
    probe_days) of ``score.view`` at ``now``: 3 sliding days [now − 24 h·(i+1), now − 24 h·i), newest first (``hours``
    of probes, ``canary_h`` — hours with a canary at all, ``up_h`` — with rows of host_m10: the node lived); the daily
    values of the local days not later than ``upto`` (the last whole day); a day of the canary has the ``hours`` of
    its quiet samples, not of the probes (REV-ns-c6-2 З-1)."""
    wins = []
    for i in range(3):
        lo, hi = now - (i + 1) * DAY, now - i * DAY
        pr = [r for r in ping if lo <= r["ts"] < hi]
        cn = [c for c in canary if lo <= c[0] < hi]
        vals, hours, _cn = _probe_vals(pr, [v for ts, v in steal if lo <= ts < hi], cn)
        w = dict(vals, day=day_of(hi - 1), hours=hours, canary_h=S.canary_hours(cn), up_h=S.up_hours(cn))
        ls = _loss(pr)
        if ls:
            w["loss"] = ls
        wins.append(w)
    by_day = {}
    for r in ping:
        by_day.setdefault(day_of(r["ts"]), ([], [], []))[0].append(r)
    for ts, v in steal:
        by_day.setdefault(day_of(ts), ([], [], []))[1].append(v)
    for c in canary:
        by_day.setdefault(day_of(c[0]), ([], [], []))[2].append(c)
    days = {"rtt": [], "jitter": [], "steal_p95": [], "loss": [], "cpu_canary_cv": []}
    for d in sorted(by_day):
        if upto is not None and d > upto:
            continue
        pr, st, cn = by_day[d]
        vals, hours, can_n = _probe_vals(pr, st, cn)
        for tid in ("rtt", "steal_p95", "cpu_canary_cv"):
            if tid in vals:
                days[tid].append({"day": d, "v": vals[tid], "hours": can_n if tid == "cpu_canary_cv" else hours})
        if "jitter" in vals:
            days["jitter"].append(dict(vals["jitter"], day=d, hours=hours))
        ls = _loss(pr)
        if ls:
            lv = S.loss_value([ls[a] for a in sorted(ls)])
            if lv.get("v") is not None:
                days["loss"].append({"day": d, "v": lv["v"], "n": lv["n"], "lo": lv["lo"], "hours": hours})
    return wins, days


def day_series(rows, day, length_min, z=None):
    """96 points of the local day (00:00, 00:15 … 23:45): a row [t, t + length) goes to the points it meets with the
    weight of its share; loss = 100·Σ(w·lost)/Σ(w·n), rtt = Σ(w·n·rtt_med)/Σ(w·n); none — None. The skipped hour of
    spring has no rows (None), the doubled hour of autumn falls on the same points."""
    acc = [[0.0, 0.0, 0.0, 0.0] for _ in range(96)]
    w0 = _wall0(day)
    for r in rows:
        n = _num(r.get("n")) or 0
        if n <= 0:
            continue
        a = (r["ts"] + tz.offset(r["ts"], z) - w0) / 60.0
        b = a + length_min
        for i in range(max(0, int(a // 15)), min(96, int(math.ceil(b / 15.0)))):
            ov = min(b, 15.0 * (i + 1)) - max(a, 15.0 * i)
            if ov <= 0:
                continue
            w = ov / length_min
            acc[i][0] += w * n
            acc[i][1] += w * (_num(r.get("lost")) or 0)
            rtt = _num(r.get("rtt_med"))
            if rtt is not None:
                acc[i][2] += w * n * rtt
                acc[i][3] += w * n
    return {"step": 15 if length_min <= 15 else 60,
            "loss": [_r(100.0 * a[1] / a[0]) if a[0] > 0 else None for a in acc],
            "rtt": [_r(a[2] / a[3], 2) if a[3] > 0 else None for a in acc]}


def offline_spans(events):
    """[(ts, key, args)] of node_offline / node_online, oldest first -> [(off, on | None)]; an online without its
    offline (before the rows read) — from its ``since``."""
    out, cur = [], None
    for ts, key, args in sorted(events, key=lambda e: e[0]):
        args = args if isinstance(args, dict) else {}
        if key == "node_offline":
            if cur is None:
                cur = _num(args.get("since")) or ts
        elif key == "node_online":
            end = _num(args.get("until")) or ts
            start = cur if cur is not None else _num(args.get("since"))
            if start is not None and end > start:
                out.append((start, end))
            cur = None
    if cur is not None:
        out.append((cur, None))
    return out


def _hhmm(ts, z=None):
    return time.strftime("%H:%M", tz.local(ts, z))


def day_metrics(host, ping_node, steal, series, offline_min):
    """The metrics of a day (§6.2): host — rows of host_h1 of the day, ping_node — rows ``*`` of host_ping_h1."""
    n = sum(_num(r.get("n")) or 0 for r in ping_node)
    lost = sum(_num(r.get("lost")) or 0 for r in ping_node)
    pk = [(v, i) for i, v in enumerate(series["loss"]) if v is not None]
    peak = max(pk, key=lambda x: (x[0], -x[1])) if pk else None
    mx = lambda c, f: (lambda xs: f(xs) if xs else None)([x for x in (_num(r.get(c)) for r in host) if x is not None])
    return {"busy": _r(_wavg(host, "busy"), 1), "busy_max": _r(mx("busy_max", max), 1), "steal_p95": _r(p95(steal), 2),
            "mem_avail_min": _r(mx("mem_avail_min", min), 1), "disk": _r(mx("disk", max), 1),
            "disk_free": mx("disk_free", min), "loss": _r(100.0 * lost / n) if n else None,
            "loss_peak": peak[0] if peak else None,
            "loss_peak_at": "%02d:%02d" % divmod(15 * peak[1], 60) if peak else None,
            "rtt_med": _r(_wavg(ping_node, "rtt_med"), 2), "rtt_p90": _r(mx_rows(ping_node, "rtt_p90"), 2),
            "jit": _r(_wavg(ping_node, "jit", "jit_n"), 2), "offline_min": int(round(offline_min)),
            "warn_min": int(sum(_num(r.get("warn_min")) or 0 for r in host)),
            "alarm_min": int(sum(_num(r.get("alarm_min")) or 0 for r in host))}


def mx_rows(rows, col):
    xs = [x for x in (_num(r.get(col)) for r in rows) if x is not None]
    return max(xs) if xs else None


# ── words ──────────────────────────────────────────────────────────────────

def _fmt(v):
    v = _num(v)
    if v is None:
        return "?"
    return fmt_num(v, digits=0 if abs(v) >= 100 else (1 if abs(v) >= 1 else 2))


def reason_msg(reason):
    """The words of a reason of a day colour (RU + EN by keys of score.json)."""
    if not reason:
        return reason
    a = dict(reason.get("args") or {})
    code = reason["code"]
    kw = {}
    if code in ("offline", "alarm", "warn"):
        kw["min"] = fmt_num(a.get("min") or 0)
    elif code == "loss":
        kw.update(pct=fmt_num(a.get("pct") or 0, digits=1), thr=fmt_num(a.get("thr") or 0))
    elif code in ("drop", "link_down"):
        kw["link"] = tr("score.link." + str(a.get("link")))
        unit = tr("score.unit." + a["test"]) if a.get("test") else ""
        kw["now"] = (_fmt(a.get("now")) + (" " + unit if unit else "")) if a.get("now") is not None else ""
        kw["usual"] = (_fmt(a.get("usual")) + (" " + unit if unit else "")) if a.get("usual") is not None else ""
        if code == "link_down" and not kw["now"]:
            code = "link_down_bare"
        if code == "drop" and not kw["now"]:        # NS-D33: a group or crypto — its rel, else the link alone
            code = "drop_rel" if _num(a.get("rel")) is not None else "drop_bare"
            kw["rel"] = fmt_num(int(_num(a.get("rel")) or 0))
    return dict(reason, msg=tr("score.day." + code, **kw))


# a stored why code -> its key in score.json: "few" would read as a plural form (.few) to the catalog checks
PEOPLE_WHY_KEY = {"few": "too_few"}


def people_row(rows):
    """rows.people: ``hostpeople.summary`` of the last 24 h with its words; None — no rows (B17 not on)."""
    from ..gw import hostpeople
    s = hostpeople.summary(rows)
    if s is None:
        return None
    why = s.get("why")
    s["why_text"] = tr("score.people.why." + PEOPLE_WHY_KEY.get(why, why)) if why else None
    s["partial_text"] = tr("score.people.partial") if s.get("partial") else None
    return s


# ── wiring ─────────────────────────────────────────────────────────────────

PING_SEL = ("anchor", "ts", "n", "lost", "runs", "rtt_med", "rtt_p90", "jit", "jit_n")
HOST_SEL = ("ts", "n", "busy", "busy_max", "steal", "mem_avail_min", "disk", "disk_free", "warn_min", "alarm_min")


def read(lo, hi):
    """The rows of metrics.db in [lo, hi): {"ok", "ping", "ping_m10", "host", "steal", "canary", "events", "people",
    "first"}; first — the ts of the oldest row of host_h1 / host_m10 in the whole base (the age of the history: the
    budget of offline, REV-ns-c5 З-3), None — no rows;
    ok False — no sqlite3, no base or no tables (the lists empty). canary — [(ts, canary_ms, busy)] of host_m10 only
    (A.1: the spread of the samples every 10 min; host_m10 keeps 14 d — older days have no canary, never hourly means
    of host_h1, REV-ns-c6 З-2); the rows without a canary too (canary_ms None): the hours the node lived (``up_h``,
    REV-ns-c6-2 З-1)."""
    from ..gw import metrics
    out = {"ok": False, "ping": [], "ping_m10": [], "host": [], "steal": [], "canary": [], "events": [],
           "people": [], "first": None}
    conn = metrics.reader()
    if conn is None:
        return out
    try:
        q = lambda sql, *a: conn.execute(sql, a).fetchall()
        out["ping"] = node_fill([dict(zip(PING_SEL, r)) for r in q(
            "SELECT %s FROM host_ping_h1 WHERE ts >= ? AND ts < ?" % ", ".join(PING_SEL), lo, hi)])
        out["ping_m10"] = [r for r in node_fill([dict(zip(PING_SEL, r)) for r in q(       # the anchors: node_fill
            "SELECT %s FROM host_ping_m10 WHERE ts >= ? AND ts < ?" % ", ".join(PING_SEL),
            max(lo, hi - M10_KEEP - DAY), hi)]) if r.get("anchor") == NODE]
        out["host"] = [dict(zip(HOST_SEL, r)) for r in q(
            "SELECT %s FROM host_h1 WHERE ts >= ? AND ts < ?" % ", ".join(HOST_SEL), lo, hi)]
        m10 = q("SELECT ts, steal FROM host_m10 WHERE ts >= ? AND ts < ?", lo, hi)
        first = min([ts for ts, _ in m10] or [hi])
        out["steal"] = [(r["ts"], r["steal"]) for r in out["host"] if r["ts"] < first] + list(m10)
        out["canary"] = [tuple(r) for r in q("SELECT ts, canary_ms, busy FROM host_m10 WHERE (n > 0 OR "
                                             "canary_ms IS NOT NULL) AND ts >= ? AND ts < ?", lo, hi)]
        out["events"] = [(ts, k, _json(a)) for ts, k, a in q(
            "SELECT ts, key, args FROM events WHERE key IN ('node_offline', 'node_online') AND ts >= ? AND ts < ? "
            "ORDER BY ts", lo - 7 * DAY, hi)]
        old = [r[0] for t in ("host_h1", "host_m10") for r in q("SELECT MIN(ts) FROM %s" % t) if r[0] is not None]
        out["first"] = min(old) if old else None
        out["ok"] = True
        try:
            out["people"] = [dict(r) for r in _dict_rows(conn, "SELECT * FROM host_people_h1 WHERE ts >= ? AND ts < ?",
                                                         (hi - DAY, hi))]
        except Exception:                            # noqa: BLE001 — a base before SCHEMA 3: no line
            out["people"] = []
    except Exception:                                # noqa: BLE001 — sqlite3.Error: a base of stage 1, busy …
        out["ok"] = False
    finally:
        try:
            conn.close()
        except Exception:                            # noqa: BLE001
            pass
    return out


def _json(a):
    import json
    try:
        v = json.loads(a) if isinstance(a, str) else a
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _dict_rows(conn, sql, args):
    cur = conn.execute(sql, args)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


_DAYS = {}                                 # (day, fingerprint) -> days[] entry of a day closed for a day already
_DAYS_MAX = 64


def reset():
    """Forget the cached days of the strip (tests)."""
    _DAYS.clear()


class Ctx(object):
    """One request: the checks and the rows read once; the views at the ends of days computed once each."""

    def __init__(self, spec, now, z=None, rule=None):
        from . import history
        z = z or tz.zone()                           # once: tz.zone() reads the config at every call
        self.spec, self.now, self.z = spec, int(now), z
        self.rule = S.valid_dev_rule(rule) if rule is not None else dict(S.DEV_RULE)   # ▼ colours a day (П-3)
        days = {}

        def day_of(ts):                              # the offset of a zone changes at most on a quarter of an hour
            k = int(ts) // 900
            if k not in days:
                days[k] = tz.day(k * 900, z)
            return days[k]
        self.day_of = day_of
        self.hhmm_of = lambda ts: _hhmm(ts, z)
        self.today = self.day_of(self.now)
        smp = history.sample(self.now)
        self.runs, self.epochs, self.events = smp["runs"], smp["epochs"], smp["events"]
        self.rows = read(self.now - SPAN, self.now + 1)
        self.spans = offline_spans(self.rows["events"])
        self._views, self._wins = {}, {}
        self.rows["ping"].sort(key=lambda r: r["ts"])
        self.rows["steal"] = sorted((x for x in self.rows["steal"] if _num(x[1]) is not None), key=lambda x: x[0])
        self.canary = sorted((c for c in self.rows.get("canary") or () if _num(c[0]) is not None), key=lambda c: c[0])
        self._pts = [r["ts"] for r in self.rows["ping"]]
        self.rows["ping_m10"].sort(key=lambda r: r["ts"])
        self._mts = [r["ts"] for r in self.rows["ping_m10"]]
        self._sts = [x[0] for x in self.rows["steal"]]
        self._cts = [c[0] for c in self.canary]
        self._pdays = probe_inputs(self.rows["ping"], self.rows["steal"], self.now, self.day_of,
                                   canary=self.canary)[1]

    def view(self, t):
        """score.view at ``t`` (the end of a day or now) with the probes of the rows."""
        if t not in self._views:
            last_whole = self.day_of(t) if self.day_of(t + 1) != self.day_of(t) else self.day_of(t - DAY)
            lo = t - 3 * DAY
            ping = self.rows["ping"][bisect.bisect_left(self._pts, lo):bisect.bisect_left(self._pts, t)]
            steal = self.rows["steal"][bisect.bisect_left(self._sts, lo):bisect.bisect_left(self._sts, t)]
            canary = self.canary[bisect.bisect_left(self._cts, lo):bisect.bisect_left(self._cts, t)]
            wins = probe_inputs(ping, steal, t, self.day_of, upto="", canary=canary)[0]     # the windows only
            self._wins[t] = wins
            pdays = {tid: [x for x in recs if x["day"] <= last_whole] for tid, recs in self._pdays.items()}
            self._views[t] = S.view(self.spec, self.runs, t, day_of=self.day_of, hhmm_of=self.hhmm_of,
                                    probes=wins if self.rows["ok"] else None,
                                    probe_days=pdays if self.rows["ok"] else None,
                                    epochs=self.epochs, events=self.events, rule=self.rule,
                                    steal_7d=self.steal_7d(t))
        return self._views[t]

    def probe_hours(self, t):
        """The most hours of probes in one of the 3 windows of ``score.view`` at ``t`` (a window counts from
        S.WINDOW_HOURS_MIN hours); None — no base of metrics."""
        if not self.rows["ok"]:
            return None
        self.view(t)
        return max([int(w.get("hours") or 0) for w in self._wins.get(t) or ()] or [0])

    def steal_7d(self, t):
        """steal p95 over the 7 d before ``t`` — only when its rows reach back to t − 7 d (+ an hour of slack): half a
        day of rows is not «7 days» (RS 3.4, REV-ns-c6 С-2); None otherwise."""
        lo = t - 7 * DAY
        i = bisect.bisect_left(self._sts, lo)
        if i >= len(self._sts) or self._sts[i] >= t or self._sts[i] > lo + S.STEAL_7D_SLACK:
            return None
        return self.steal_p95(lo, t)

    def steal_p95(self, lo, hi):
        """steal p95 of the rows in [lo, hi) (host_m10, older — host_h1); None — no rows."""
        return p95(v for _ts, v in self.rows["steal"][bisect.bisect_left(self._sts, lo):bisect.bisect_left(self._sts,
                                                                                                         hi)])

    def end(self, day):
        start, end = day_span(day, self.z)
        return start, end, min(end - 1, self.now)

    def day_rows(self, x):
        """(rows ``*`` of the probes, their length in min) of the curve of a day (``day_series``): host_ping_m10 while it
        keeps the day, else host_ping_h1 (+ the hour before: a row covers its hour)."""
        if x["start"] >= self.now - M10_KEEP:
            m10 = self.rows["ping_m10"]
            return m10[bisect.bisect_left(self._mts, x["start"] - 600):bisect.bisect_left(self._mts, x["end"])], 10
        ping = self.rows["ping"][bisect.bisect_left(self._pts, x["start"] - 3600):bisect.bisect_left(self._pts,
                                                                                                    x["start"])]
        return list(x["node"]) + [r for r in ping if r.get("anchor") == NODE], 60

    def day(self, day, window=False):
        """(entry of days[], extra of the day page) of a day of the strip."""
        start, end, t = self.end(day)
        host = [r for r in self.rows["host"] if start <= r["ts"] < end]
        node = [r for r in self.rows["ping"] if r.get("anchor") == NODE and start <= r["ts"] < end]
        off = S.offline_minutes(self.spans, start, end, now=self.now)
        test = any(r["status"] in S.GOOD_STATUS and self.day_of(r["ts"]) == day for r in self.runs)
        # «• — день с полным тестом» легенды полосы (6C, QUEUE 19.10 18:48; HANDOFF: точка — полный тест дня);
        # автоповтор (by == "auto") мерит только упавшие звенья — не полный тест (REV-ns-p4a П-1, как day_score)
        full = any(r["status"] in S.GOOD_STATUS and r.get("mode") == "full" and r.get("by") != "auto"
                   and self.day_of(r["ts"]) == day for r in self.runs)
        d = {"hours": len([r for r in host if (_num(r.get("n")) or 0) > 0]), "test": test, "offline_min": off,
             "lost": sum(_num(r.get("lost")) or 0 for r in node), "n": sum(_num(r.get("n")) or 0 for r in node),
             "alarm_min": sum(_num(r.get("alarm_min")) or 0 for r in host),
             "warn_min": sum(_num(r.get("warn_min")) or 0 for r in host)}
        drops = [e for e in self.events if e.get("day") == day]
        key = None
        if end + DAY <= self.now:                    # a closed day (its late repeat too): the same inputs, the same entry
            rs = [(r["id"], r["ts"], r["status"]) for r in self.runs if r["ts"] < end + DAY]
            key = (day, self.spec.get("version"), len(rs), rs[-1] if rs else None,
                   sum(1 for e in self.events if e.get("ts", 0) < end + DAY),
                   sum(1 for e in self.epochs if e["ts"] < end), tuple(sorted(d.items())), len(drops), self.z.name
                   if hasattr(self.z, "name") else None, tuple(sorted(self.rule.items())))   # REV-ns-b6 П-3: the rule
            if key in _DAYS:
                return _DAYS[key], {"start": start, "end": end, "t": t, "host": host, "node": node, "off": off}
        color, partial, reason = S.day_color(d, [], drops)
        if color == "green":                         # ▼ of a link only decides a day nothing else coloured
            devs = S.day_devs(self.view(t), S.tested_links(self.spec, self.runs, day, day_of=self.day_of))
            color, partial, reason = S.day_color(d, devs, drops)
        entry = {"day": day, "color": color, "reason": reason, "test": test, "full": full, "partial": partial}
        if key is not None:
            if len(_DAYS) >= _DAYS_MAX:
                _DAYS.clear()
            _DAYS[key] = entry
        return entry, {"start": start, "end": end, "t": t, "host": host, "node": node, "off": off}


def _said(entry):
    return dict(entry, reason=reason_msg(entry["reason"]))


def _links(v, fields):
    out = []
    for lk in v["links"]:
        row = dict((k, lk.get(k)) for k in fields)
        for k in RAW_FIELDS:
            if k in row:
                row[k] = _r(_num(row[k]))
        if row.get("parts") is not None:
            row["parts"] = [dict(p, fresh_raw=_r(_num(p.get("fresh_raw"))), usual_raw=_r(_num(p.get("usual_raw"))))
                            for p in row["parts"]]
        if "dev_pending" in fields:
            a = (v.get("dev_args") or {}).get(lk["id"]) if lk.get("dev") else \
                (v.get("pend_args") or {}).get(lk["id"]) if lk.get("dev_pending_kind") == "day" else \
                (v.get("kind_args") or {}).get(lk["id"]) if lk.get("dev_pending_kind") else None
            row["hint"] = None if a is None else _hint(a)
        out.append(row)
    return out


# Why a link (or a test of a group) has no fresh value — круг 1 №8: «Сейчас» says it instead of «—». Codes:
# probes — a test of the background probes with no value yet; never — no good check at all; not_run — no good check
# measured this test; failed — the latest check that ran it gave no value (its own ``why`` of the runner, when the
# catalog has it: «Не измерено: сервер в России, а точки для замера отдачи нет»); load — that check was under load and
# its value is not taken; stale — there is a value, but older than the window of «now».
# The runner's own reason of a channel test (``bench.channel._plan``) is passed as its code — круг 1 Л3 №8: «ошибка
# замера» was shown on a node in Russia where the upload is simply not measured: no_upload_ru — no upload point for a
# node in Russia; excluded — the test was switched off for the check; no_target / no_iperf3 — no point to measure to;
# upload_off — the person turned the upload of a full check off at its start (D-144).
# Their text is the runner's (``bench_run.why.<code>``).
# young — the first hours of a new node (Т-3312 «Мелочь 2»): a test of YOUNG_TESTS with no value while the probes have
# not filled a window yet (``_young``, D-141: S.WINDOW_HOURS_MIN) — «ещё измеряется: N ч из 12», not «ждём проб»;
# its text — ``score.link.why.young`` with the hours of ``provisional`` (as ``synth_doc``).
RUN_WHY = ("no_upload_ru", "excluded", "no_target", "no_iperf3", "upload_off")
WHY_CODES = ("probes", "never", "not_run", "failed", "load", "stale", "young") + RUN_WHY


def why_missing(cx, tid, young=None):
    """(code, text) of a test without a fresh value at ``cx.now`` (see WHY_CODES); ``young`` — ``_young`` of the node
    (None — the window is there or the probes do not run here)."""
    if young and tid in YOUNG_TESTS:
        return "young", _young_text(young)
    if tid in S.PROBE_TESTS and cx.rows["ok"]:
        return "probes", tr("score.link.why.probes")
    goods = sorted((r for r in cx.runs if S.good_run(r) and r["ts"] <= cx.now), key=lambda r: r["ts"], reverse=True)
    if not goods:
        return "never", tr("score.link.why.never")
    last = next((r for r in goods if tid in (r.get("vals") or {})), None)
    if last is None:
        return "not_run", tr("score.link.why.not_run")
    if S.usable(last, tid) is not None:
        return "stale", tr("score.link.why.stale")
    if last.get("under_load") and S.usable(dict(last, under_load=False), tid) is not None:
        return "load", tr("score.link.why.load")         # measured, but under load only the channel counts (NS-S16)
    m = (last.get("vals") or {}).get(tid)
    why = m.get("why") if isinstance(m, dict) else None
    key = "bench_run.why.%s" % why if isinstance(why, str) and re.match(r"^[a-z0-9_]{1,40}$", why) else ""
    raw = lookup(key) if key else None
    # a text with numbers of a whole check («{mbps}») is not of a test row: skipped before tr() — its strict mode
    # (K3_TR_STRICT) refuses a «{name}» left unfilled
    text = tr(key) if raw and "{" not in raw else ""
    if why in RUN_WHY and text:
        return why, text
    return "failed", text or tr("score.link.why.failed")


def _synth_why(cx, vals, lid):
    """(why, why_text) of a link of the synthesis (``score_abs.LINKS``) without a fresh score: the reason of its first
    test without a fresh value (why_missing); every test has one — (None, None): the link has no score for another
    cause (too few samples), the panel says «нет данных»."""
    from .. import score_abs as SA
    b = next((x for x in SA.LINKS if x["id"] == lid), {})
    tids = list(b.get("tests") or ()) + [t["id"] for g in SA.GROUPS if g["id"] in (b.get("groups") or ())
                                         for t in g["tests"]]
    for tid in tids:
        m = vals.get(tid)
        if (m.get("v") if isinstance(m, dict) else m) is None:
            return why_missing(cx, tid)
    return None, None


def _with_why(cx, links, young=None):
    """links[] (and their parts) with ``why`` / ``why_text`` where ``fresh_raw`` is null (None elsewhere); ``young`` —
    as ``why_missing``."""
    for row in links:
        for x in [row] + list(row.get("parts") or ()):
            x["why"], x["why_text"] = None, None
            tid = x.get("test")
            if x.get("fresh_raw") is None and tid:
                x["why"], x["why_text"] = why_missing(cx, tid, young)
    return links


def stab_doc(cx, v):
    """``stab`` of /api/host/score (NS C6): the stability of the history — steal p95 over 24 h and 7 d (null in a
    container and when hidden), the canary and the spread of the channel (the fresh values of the tests, with ``why``
    and its words), minutes without network over 24 h and 7 d (node_offline / node_online)."""
    tests = v.get("stab") or {}
    hid = (tests.get("steal_p95") or {}).get("why") in ("container", "hidden")
    now = cx.now
    out = {"tests": {}, "steal_p95_24h": None if hid else _r(cx.steal_p95(now - DAY, now), 2),
           "steal_p95_7d": None if hid else _r(cx.steal_7d(now), 2),         # null until the rows cover 7 d (С-2)
           "offline_min_24h": _r(S.offline_minutes(cx.spans, now - DAY, now, now=now), 1) if cx.rows["ok"] else None,
           "offline_min_7d": _r(S.offline_minutes(cx.spans, now - 7 * DAY, now, now=now), 1) if cx.rows["ok"]
           else None}
    for tid, m in tests.items():
        why = m.get("why")
        out["tests"][tid] = dict(m, why_text=tr("score.stab.why." + why) if why in S.STAB_WHY else None)
    return out


def share_days(days):
    """The strip for «Поделиться» (C9, SS §5.1): only {"day", "color", "test", "partial"} of every day of ``days`` —
    no reasons, scores or links go out."""
    return [{"day": d["day"], "color": d["color"], "test": bool(d.get("test")), "partial": bool(d.get("partial"))}
            for d in days or ()]


def _hint(a):
    """links[].hint (SS §4, REV-ns-b6 З-1, П-2): the numbers of the texts of NS-D25, rounded as the series."""
    days = a.get("days")
    return {"test": a.get("test"), "now": _r(_num(a.get("now"))), "usual": _r(_num(a.get("usual"))),
            "unit": a.get("unit"), "days": None if days is None else [[d, _r(_num(x))] for d, x in days]}


def _val(m):
    """The raw value of a test of a check for the screens: a finite number, not failed (``ok: False``); else None."""
    if not isinstance(m, dict) or m.get("ok") is False:
        return None
    return _num(m.get("v"))


def series_doc(cx):
    """``series`` of /api/host/score (UI-C, the line of 30 days of a link): {test: [[x, v], …]} of every SERIES_TESTS
    (no points — []), oldest first, the days of the strip only. The tests of the checks: x — the ts of a good check
    (S.GOOD_STATUS, its automatic repeats too) not later than now, v — its raw value (a finite number, not failed);
    the probes: x — the day of the node, v — its daily value (the same values «обычно» takes, today's part of a day
    too). v is rounded as the raw numbers (3 decimals)."""
    first = days_back(cx.today)[0]
    out = dict((t, []) for t in SERIES_TESTS)
    for r in cx.runs:
        if r.get("status") not in S.GOOD_STATUS or r["ts"] > cx.now or cx.day_of(r["ts"]) < first:
            continue
        vals = r.get("vals") or {}
        for t in SERIES_TESTS:
            v = None if t in SERIES_PROBES else _val(vals.get(t))
            if v is not None:
                out[t].append([int(r["ts"]), _r(v)])
    for t in SERIES_PROBES:
        out[t] = [[x["day"], _r(_num(x.get("v")))] for x in cx._pdays.get(t) or ()
                  if first <= x["day"] <= cx.today and _num(x.get("v")) is not None]
    return out


def day_m(cx, day, x):
    """days[].m of /api/host/score (UI-C, the hint of a day of the strip): as ``day_metrics`` of the day page — the
    delay (median ``rtt``, the worst 10 % ``rtt_p90``), the loss of the day and its peak of 15 min, the jitter, the
    whole minutes without network ``off``; None — not measured (no base: all None)."""
    if not cx.rows["ok"]:
        return dict.fromkeys(DAY_M, None)
    rows, length = cx.day_rows(x)
    m = day_metrics([], x["node"], [], day_series(rows, day, length, cx.z), x["off"])
    return {"rtt": m["rtt_med"], "rtt_p90": m["rtt_p90"], "loss": m["loss"], "loss_peak": m["loss_peak"],
            "jit": m["jit"], "off": m["offline_min"]}


def day_checks(cx, runs):
    """days[].checks: the checks of a day of the node (every status), oldest first, the last DAY_CHECKS_MAX —
    [{"hhmm", "mode", "by", "status", "down", "up"}]; down/up — the raw net_down / net_up of the check (None — not
    measured or failed)."""
    out = []
    for r in list(runs)[-DAY_CHECKS_MAX:]:
        vals = r.get("vals") or {}
        out.append({"hhmm": cx.hhmm_of(r["ts"]), "mode": r.get("mode"), "by": r.get("by"), "status": r.get("status"),
                    "down": _r(_val(vals.get("net_down"))), "up": _r(_val(vals.get("net_up")))})
    return out


def score_doc(spec, now=None, z=None, rule=None, probing=False):
    """The answer of GET /api/host/score (SS §4; NS-D30–NS-D34): ``summary`` (``rel_total`` — 100 is «как обычно у
    этого сервера», the times of the checks, the repeat), ``links`` (the raw numbers, «обычно», ``rel``, ▲/▼), the row
    «Связь с людьми», the stability of the history, the 30 days (+ ``m``, ``checks`` of every day), ``series`` (the
    line of 30 days of the tests, UI-C); rule — gateway.hostmon.dev (None — the default). ``provisional`` — as
    ``synth_doc`` (``probing``: the probes run here): while it is there, a test of YOUNG_TESTS without a value has
    why "young" with its hours, not "probes" (Т-3312 «Мелочь 2»)."""
    cx = Ctx(spec, time.time() if now is None else now, z, rule)
    v = cx.view(cx.now)
    young = _young(cx, probing)
    days = []
    if cx.rows["ok"] or cx.runs:
        by_day = {}
        for r in cx.runs:                            # oldest first (history.sample)
            by_day.setdefault(cx.day_of(r["ts"]), []).append(r)
        for d in days_back(cx.today):
            entry, x = cx.day(d)                     # m and checks — never of the cache of the closed days
            days.append(dict(_said(entry), m=day_m(cx, d, x), checks=day_checks(cx, by_day.get(d, ()))))
    people = people_row(cx.rows["people"]) if cx.rows["ok"] else None
    return {"ok": True, "charts": cx.rows["ok"], "summary": v["summary"], "dev_rule": v["dev_rule"],
            "links": _with_why(cx, _links(v, LINK_FIELDS), young), "provisional": young,
            "rows": {"people": people, "load": None}, "stab": stab_doc(cx, v), "days": days, "series": series_doc(cx)}


def node_doc(now=None):
    """GET /api/host/score of this node — one answer for the panel, ``kaskadctl host score`` and MCP: the catalogue of
    ``kaskad.score`` and the rule of ▲/▼ of the state (NS-D25, ``gateway.hostmon.dev``)."""
    from .. import state
    from ..gw import model
    st = state.load()
    return score_doc(S.load_spec(), now=now, rule=model.dev_rule(st), probing=probes_run(st))


def day_doc(spec, day, now=None, z=None, rule=None):
    """The answer of GET /api/host/day/<day> (NS §6.2 «Данные одного дня»); None — not a day of the strip."""
    now = int(time.time() if now is None else now)
    if valid_day(day, tz.day(now, z)) is None:
        return None
    cx = Ctx(spec, now, z, rule)
    if not cx.rows["ok"] and not cx.runs:
        return {"ok": True, "charts": False}
    entry, x = cx.day(day)
    entry = _said(entry)
    v = cx.view(x["t"])
    rows, length = cx.day_rows(x)
    series = day_series(rows, day, length, cx.z)
    steal = [s for ts, s in cx.rows["steal"] if x["start"] <= ts < x["end"]]
    reason = entry["reason"]
    if reason:
        win = None
        if reason["code"] == "offline":
            sp = [(max(a, x["start"]), min(b if b is not None else min(x["end"], cx.now), x["end"]))
                  for a, b in cx.spans]
            sp = [s for s in sp if s[1] > s[0]]
            if sp:
                a, b = max(sp, key=lambda s: s[1] - s[0])
                win = {"from": _hhmm(a, cx.z), "to": "24:00" if b >= x["end"] else _hhmm(b, cx.z)}
        elif reason["code"] == "loss" and any(p is not None for p in series["loss"]):
            m = day_metrics([], [], [], series, 0)
            h, mi = int(m["loss_peak_at"][:2]), int(m["loss_peak_at"][3:])
            e = h * 60 + mi + 15
            win = {"from": m["loss_peak_at"], "to": "%02d:%02d" % divmod(e, 60) if e < 1440 else "24:00"}
        reason = dict(reason, window=win)
    return dict(entry, ok=True, charts=cx.rows["ok"], reason=reason, links=_links(v, DAY_LINK_FIELDS),
                series=series, metrics=day_metrics(x["host"], x["node"], steal, series, x["off"]))


# ── сухая синтетика 0–1000 (решение владельца 27.10, Т1 круга 1; ``kaskad.score_abs``) ──────────────────────────────

SYNTH_DAYS = 60             # «Обычно · до 60 дней»: проверки не старше 60 дней
SYNTH_CHECKS = 30           # точки ряда «Итог по проверкам» и база «обычно»
SYNTH_FRESH = 3             # «Свежие · по 3 последним»: «обычно» — без 3 последних проверок
SYNTH_USUAL_MIN = 3         # «обычно» звена — из ≥ 3 проверок (как rel, NS-D30)
SYNTH_BAND = 8              # обычный разброс (p25–p75) — из ≥ 8 проверок у каждого звена
SYNTH_PROBE_BACK = 3        # значения лёгких проб для проверки — её дня, иначе ближайшего раньше, не старше 3 дней
# первые часы нового узла (владелец 28.10, решение 4, вариант А R-it1-probe): пока ни в одном окне проб нет
# S.WINDOW_HOURS_MIN часов, «Сеть» и «Потери» ещё измеряются — итог по остальным звеньям, «предварительно»
YOUNG_LINKS = ("net_q", "loss")
# their tests of the background probes (S.PROBE_TESTS) — the rows «Отклик», «Дрожание», «Потери» of «Сейчас»
YOUNG_TESTS = ("rtt", "jitter", "loss")


def _young(cx, probing):
    """{"why": "net_young", "have_h", "need_h"} while the light probes of a node that runs them have not filled a
    window of S.WINDOW_HOURS_MIN hours yet (the first 12–14 h: the hourly rows come up to ~2 h late, R-it1-probe);
    None — the probes are off, the gateway (the collector) is off, no base of metrics, or the window is there."""
    if not probing:
        return None
    have = cx.probe_hours(cx.now)
    if have is None or have >= S.WINDOW_HOURS_MIN:
        return None
    return {"why": "net_young", "have_h": have, "need_h": S.WINDOW_HOURS_MIN}


def _young_text(young):
    """«Ещё измеряется: фоновые пробы набрали N ч из 12 …» of ``_young`` — the hours of the rule, not of the text."""
    return tr("score.link.why.young", have=str(young["have_h"]), need=str(young["need_h"]))


def _probe_of_day(pdays, day):
    """{test: value} of the light probes (rtt, jitter, steal_p95, cpu_canary_cv, loss) of the local ``day`` — the
    daily value of that day, else of the nearest day before it within SYNTH_PROBE_BACK days; loss — {v, n, lo}."""
    out = {}
    lo = (datetime.date.fromisoformat(day) - datetime.timedelta(days=SYNTH_PROBE_BACK)).isoformat()
    for tid, recs in (pdays or {}).items():
        got = [x for x in recs or () if isinstance(x, dict) and lo <= str(x.get("day")) <= day]
        if not got:
            continue
        x = max(got, key=lambda r: str(r.get("day")))
        out[tid] = dict((k, x[k]) for k in ("v", "n", "lo") if k in x)
    return out


def synth_doc(spec, now=None, z=None, rule=None, probing=False):
    """The answer of GET /api/host/synth (the panel only): the score 0–1000 of the 8 links and the total
    (``kaskad.score_abs``; the anchors of RESEARCH A, the processor ones calibrated by К1 — ``calibrated`` true).

    ``fresh`` — of the fresh values of ``score.view`` (the 3 last good values of every test, NS-S10) — the layer
    «Свежие · по 3 последним»; ``checks`` — every good check of the last SYNTH_DAYS days (newest first, at most
    SYNTH_CHECKS; an automatic repeat — inside its check, ``score.check_vals``) with its links and total: the probe
    tests by the day of the check. ``links[]`` — {id, fresh, usual, lo, hi, n, dev}: ``usual`` — the median of the link
    over the checks without the SYNTH_FRESH newest (≥ SYNTH_USUAL_MIN of them, else null — «мало истории»), ``lo`` /
    ``hi`` — their p25 / p75 from SYNTH_BAND checks, ``dev`` — ▲/▼ of /api/host/score (up | down | null), ``why`` /
    ``why_text`` — why ``fresh`` is null (WHY_CODES, why_missing; null when there is a value).
    ``totals`` — {usual, min, max, n} of the totals of the checks. No price, tariff, capacity or «годится для…».
    Calibration (владелец 28.10, решение 5): ``links[].calib`` — the link has the anchors of the reference servers
    (``score_abs.CALIB_LINKS``; the others — «предварительно»), ``calibrated`` — the whole scale is (false while any
    link is not). The reference servers themselves (``score_abs.REFS``: hoster, tariff, CPU, city) stay inside the
    code — no ``refs`` here (владелец 29.10, D-142). ``provisional`` — {why "net_young",
    have_h, need_h} while the probes of this node (``probing``: they run here) have not filled a window yet (решение 4,
    вариант А): ``fresh`` leaves out YOUNG_LINKS (why "young"), null otherwise; ``net_window_h`` — that rule's hours
    (S.WINDOW_HOURS_MIN) for «Как считается балл»; ``weak_gap`` — the rule of the weak link (D-143: more than this %
    below the mean of the links of the check), ``fresh.weak`` / ``checks[].weak`` — null when there is no such link."""
    from .. import score_abs as SA
    cx = Ctx(spec, time.time() if now is None else now, z, rule)
    v = cx.view(cx.now)
    young = _young(cx, probing)
    skip = YOUNG_LINKS if young else ()
    fresh = SA.total(v["vals"], skip=skip)
    by_id = {r.get("id"): r for r in cx.runs}
    runs = [r for r in cx.runs if S.good_run(r) and not S.is_repeat(r, by_id) and cx.now - r["ts"] <= SYNTH_DAYS * DAY
            and r["ts"] <= cx.now]
    runs.sort(key=lambda r: (r["ts"], str(r.get("id"))), reverse=True)
    checks = []
    for r in runs[:SYNTH_CHECKS]:
        vals = S.check_vals(spec, cx.runs, r, cx.now)
        day = cx.day_of(r["ts"])
        for tid, m in _probe_of_day(cx._pdays, day).items():
            vals.setdefault(tid, m)
        t = SA.total(vals)
        checks.append({"id": r.get("id"), "ts": int(r["ts"]), "day": day, "mode": r.get("mode") or "full",
                       "by": r.get("by"), "total": t["total"], "raw": t["raw"], "cap": t["cap"], "weak": t["weak"],
                       "limited": t["limited"], "why": t["why"], "links": t["links"]})
    devs = dict((x["id"], x.get("dev")) for x in v["links"])
    base = checks[SYNTH_FRESH:]
    links = []
    for lid in SA.LINK_IDS:
        hist = [c["links"].get(lid) for c in base if c["links"].get(lid) is not None]
        q = SA.quartiles(hist) if len(hist) >= SYNTH_BAND else None
        if lid in skip:
            why = ("young", _young_text(young))
        else:
            why = _synth_why(cx, v["vals"], lid) if fresh["links"].get(lid) is None else (None, None)
        links.append({"id": lid, "fresh": fresh["links"].get(lid), "calib": SA.link_calibrated(lid),
                      "usual": SA.median(hist) if len(hist) >= SYNTH_USUAL_MIN else None,
                      "lo": q[0] if q else None, "hi": q[1] if q else None, "n": len(hist),
                      "dev": devs.get(lid) if devs.get(lid) in ("up", "down") else None,
                      "why": why[0], "why_text": why[1]})
    tots = [c["total"] for c in checks if c["total"] is not None]
    usual_t = [c["total"] for c in base if c["total"] is not None]
    return {"ok": True, "charts": cx.rows["ok"], "version": SA.VERSION, "calibrated": SA.CALIBRATED,
            "anchors": list(SA.A), "margin": SA.MARGIN, "weak_gap": SA.WEAK_GAP, "provisional": young,
            "net_window_h": S.WINDOW_HOURS_MIN,
            "fresh": {"total": fresh["total"], "raw": fresh["raw"], "cap": fresh["cap"], "weak": fresh["weak"],
                      "limited": fresh["limited"], "why": fresh["why"], "measured": fresh["measured"]},
            "links": links, "checks": checks,
            "totals": {"usual": SA.median(usual_t) if len(usual_t) >= SYNTH_USUAL_MIN else None,
                       "min": min(tots) if tots else None, "max": max(tots) if tots else None, "n": len(tots)},
            "last_at": v["summary"].get("last_at"), "stale": bool(v["summary"].get("stale"))}


def probes_run(st):
    """The light probes run on this node: the collector works (the gateway on, engine nft — ``collect.tick``) and
    ``gateway.hostmon`` is on with ``probes`` true (``model.hostmon_flags``)."""
    from ..gw import hostprobe, model
    return hostprobe._collector_on(st) and model.hostmon_flags(st) == (True, True)


def node_synth(now=None):
    """GET /api/host/synth of this node (the panel; not CLI, MCP, the bot or «Поделиться»)."""
    from .. import state
    from ..gw import model
    st = state.load()
    return synth_doc(S.load_spec(), now=now, rule=model.dev_rule(st), probing=probes_run(st))
