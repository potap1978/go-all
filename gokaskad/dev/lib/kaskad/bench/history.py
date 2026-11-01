"""The history of the checks of the node — metrics.db (``mode=ro``) and the queue together
(k3/handoff/SPEC-node-score-schedule-share.md §3.3, §4, NS-S9, NS-S17; §2.8 п. 6).

* Only reads: the base by a read-only connection of its own (a check written meanwhile is seen whole or not at all —
  WAL), the queue by ``spool.read_all`` (nothing moved). A check in both (the collector has written it and not yet
  removed the file) — the row of the base; nothing twice.
* Without the gateway (no collector, NS-D1) the queue is the whole history: the events of a new base ``cpu``/``mem``/
  ``engines`` are drawn from the inventories of its checks (``spool.epoch_kinds`` by ``score.inv_changes``), the
  tariff — from its files (NS-S17, З-10); the events ``bench_drop`` come from the files as the runner wrote them.
* ``runs``/``run``/``compare`` — the screen «История» and its API (§3.4, B13).
* The score (§4) is ``kaskad.score`` only (B4; coordinator 19.10): fresh values, «usual», the days, the hour, ▲/▼,
  the drops. Here — the raw material in its shape: ``sample`` -> {"runs", "epochs", "events"}; ``view`` — a thin
  wrapper that hands it to ``score.view`` (``events`` from the base, never replayed).
"""

import datetime
import importlib
import json
import os
import time

from .. import tz
from . import spool

DAY = 86400
MAX_LIMIT = 500
BY_TEXT = ("schedule", "panel", "bot", "cli", "mcp", "ssh", "auto")    # host_sched.BY: one catalog host_sched.by.*
REL_INV = ("nproc", "smt", "cpu", "mem_gb", "crash_mb", "virt")   # run["inv"] of score_run: the inventory of the
                                                                  # check (virt — steal in a container, C6)


def _score():
    """The module ``kaskad.score`` (the rules of the history: rel, ▲/▼) or None."""
    try:
        return importlib.import_module("kaskad.score")
    except ImportError:
        return None


def _loads(text, default):
    if not text:
        return default
    try:
        v = json.loads(text)
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


# ── reading the base and the queue ─────────────────────────────────────────

def _db(lo, hi, rid=None):
    """(runs {id: run with "tests"}, epochs [(ts, kind, args)], read) of the base; read False — no sqlite3, no base or
    no table of SCHEMA 3 yet (the collector has not opened the base since the update)."""
    from ..gw import metrics
    conn = metrics.reader()
    if conn is None:
        return {}, [], False
    try:
        if rid is not None:
            rs = conn.execute("SELECT %s FROM bench_run WHERE id = ?" % ", ".join(spool.RUN_COLS), (rid,)).fetchall()
        else:
            rs = conn.execute("SELECT %s FROM bench_run WHERE ts >= ? AND ts < ?" % ", ".join(spool.RUN_COLS),
                              (lo, hi)).fetchall()
        runs = dict((r[0], dict(zip(spool.RUN_COLS, r), tests=[], drops=[])) for r in rs)
        if runs:
            ids = sorted(runs)
            for i in range(0, len(ids), 500):
                part = ids[i:i + 500]
                for t in conn.execute("SELECT %s FROM bench WHERE run IN (%s) ORDER BY run, ts, test" % (
                        ", ".join(spool.TEST_COLS), ",".join("?" * len(part))), part):
                    runs[t[0]]["tests"].append(dict(zip(spool.TEST_COLS, t)))
                try:                                 # a table of its own: never the reason the checks are not read
                    for d in conn.execute("SELECT %s FROM bench_drop WHERE run IN (%s) ORDER BY run, link" % (
                            ", ".join(spool.DROP_COLS), ",".join("?" * len(part))), part):
                        runs[d[0]]["drops"].append(dict(zip(spool.DROP_COLS, d)))
                except Exception:                    # noqa: BLE001
                    pass
        eps = [(ts, k, _loads(a, {})) for ts, k, a in conn.execute("SELECT ts, kind, args FROM bench_epoch")]
        return runs, eps, True
    except Exception:                                # sqlite3.Error: no such table (a base of SCHEMA 2), busy …
        return {}, [], False
    finally:
        try:
            conn.close()
        except Exception:                            # noqa: BLE001
            pass


def _merged(lo, hi, rid=None):
    """The base and the queue together -> (runs {id: run}, epochs sorted, db read). A run of the queue only gets
    ``"queued": True``; the events of a new base of the queue's checks are drawn here (no collector to write them)."""
    # the queue FIRST, the base second (З-1 круг 2): a check the collector carries over in between is then in the base
    # (and maybe in the read queue too — the base wins by id), never in neither
    if rid is not None:                              # one check: its own file only (П-2)
        one = spool.read_one(os.path.join(spool.spool_dir(), rid + ".json"))
        qruns, qeps = ([one] if one is not None else []), []
    else:
        qruns, qeps = spool.read_all()
    runs, eps, read = _db(lo, hi, rid)
    for r in runs.values():
        r["queued"] = False
    queued = {}
    for run, tests, drops in qruns:
        if run["id"] in runs or (rid is not None and run["id"] != rid):
            continue
        r = dict(run, tests=tests, drops=drops, queued=True)
        queued[r["id"]] = r
    have = set((ts, k) for ts, k, _a in eps)
    for ts, k, a in qeps:
        if (ts, k) not in have:
            eps.append((ts, k, a))
            have.add((ts, k))
    if queued and rid is None:                       # NS-S17 without the collector: against the previous good one
        allgood = dict((i, r) for i, r in runs.items() if r["status"] in spool.GOOD and r.get("inv"))
        more, _e, _r = _db(lo - spool.KEEP_S, lo) if lo > 0 else ({}, [], False)
        allgood.update((i, r) for i, r in more.items() if r["status"] in spool.GOOD and r.get("inv"))
        allgood.update((i, r) for i, r in queued.items() if r["status"] in spool.GOOD and r.get("inv"))
        from .. import score
        known = None                                 # the last known value of every key (REV-ns-b6 З-2, NS-S17)
        for r in sorted(allgood.values(), key=lambda r: (r["ts"], r["id"])):
            if r.get("queued") and known is not None:
                for k, a in spool.epoch_kinds(known, r["inv"]):
                    if (r["ts"], k) not in have:
                        eps.append((r["ts"], k, a))
                        have.add((r["ts"], k))
            known = score.inv_known(known, spool._inv_of(r["inv"]))
    for i, r in queued.items():
        if lo <= r["ts"] < hi or rid is not None:
            runs[i] = r
    eps.sort(key=lambda e: (e[0], e[1]))
    return runs, eps, read


def _bound(v, end=False):
    """A moment from a ts or a local day 'YYYY-MM-DD' (``end`` — the start of the next day)."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    y, m, d = (int(x) for x in str(v).split("-"))
    lo = tz.to_utc(y, m, d, 0, 0)
    return tz.to_utc(*_next_day(y, m, d), 0, 0) if end else lo


def _next_day(y, m, d):
    t = datetime.date(y, m, d) + datetime.timedelta(days=1)
    return t.year, t.month, t.day


def _why_text(r, lang):
    """The text of ``why`` (SS §2.5: a code): B1's ``bench.run.why_text`` — the one catalog ``bench_run.why.<code>``,
    short args give the shorter text (never a raw «{cpu}»); None — no code, or the module is not there."""
    w = r.get("why")
    if not w:
        return None
    a = _loads(r.get("why_args"), {})
    try:
        return importlib.import_module("kaskad.bench.run").why_text(w, a if isinstance(a, dict) else {}, lang)
    except (ImportError, AttributeError):
        return None


def _drops(r):
    """The sharp drops this check found (bench_drop, NS-D33): [{link, rel, test, value, usual, unit}] — the mark «резкое падение» of the
    screen «История» and its filter «С проблемами» (B15); a row that does not read is skipped."""
    out = []
    for d in r.get("drops") or ():
        try:
            e = spool.drop_event(d)
        except Exception:                            # noqa: BLE001 — a broken row never hides the check
            continue
        if e["cond"] is None:                       # a drop of the old scale 0…1000 (NS-D34): its now is not rel
            continue
        out.append({"link": e["link"], "rel": e.get("now"), "test": e.get("test"), "value": e.get("value"),
                    "usual": e.get("usual"), "unit": e.get("unit")})
    return out


def by_text(by, lang=None):
    """«Кто запустил» (SS §3.5) — ONE catalog for the panel, the CLI and MCP: ``host_sched.by.<by>`` (the panel's
    dg.by.* are the same words, tests/test_bench_by_labels.py); an unknown code — None."""
    if by not in BY_TEXT:
        return None
    from ..i18n import tr
    return tr("host_sched.by." + by, lang)


def _row(r, lang=None):
    return {"id": r["id"], "ts": r["ts"], "end_ts": r.get("end_ts"), "mode": r["mode"], "by": r["by"],
            "by_text": by_text(r["by"], lang),
            "sched_id": r.get("sched_id"), "retry_of": r.get("retry_of"), "status": r["status"], "why": r.get("why"), "why_text": _why_text(r, lang), "rx": r.get("rx"),
            "tx": r.get("tx"), "dur_s": r.get("dur_s"), "cpu_s": r.get("cpu_s"), "under_load": r.get("under_load"),
            "drops": _drops(r)}


def span(frm=None, to=None, now=None):
    """[lo, hi) of ``frm``/``to`` (ts or local days, ``to`` inclusive): by default the last 30 days up to ``now``."""
    now = int(time.time() if now is None else now)
    lo = _bound(frm)
    hi = _bound(to, end=True)
    hi = now + 1 if hi is None else hi
    lo = hi - 30 * DAY if lo is None else lo
    return lo, hi


def runs(frm=None, to=None, mode=None, by=None, status=None, limit=200, before=None, now=None, lang=None, rel=False):
    """The list of the checks (§3.3): {"runs": [{id, ts, end_ts, mode, by, by_text, status, why, why_text, rx, tx,
    dur_s, cpu_s, under_load, drops}], "more", "db"}, the newest first; ``frm``/``to`` — ts or local days
    (``to`` inclusive), by default the last 30 days; ``before`` — a ts (exclusive) for the next page; ``limit`` 1–500.
    ``rel`` — every row also has ``rel``: rel_total of the check against «обычно» at it (``kaskad.score.check_rel``,
    as ``rel_pair``; one ``sample`` for the page); None — not a good check, too little history, older than 400 days."""
    lo, hi = span(frm, to, now)
    if before is not None:
        hi = min(hi, int(before))
    try:
        limit = min(max(int(limit), 1), MAX_LIMIT)
    except (TypeError, ValueError):
        limit = 200
    got, _eps, read = _merged(lo, hi)
    rs = [r for r in got.values() if r["ts"] >= lo and (mode is None or r["mode"] == mode)
          and (by is None or r["by"] == by) and (status is None or r["status"] == status)]
    rs.sort(key=lambda r: (r["ts"], r["id"]), reverse=True)
    out = [_row(r, lang) for r in rs[:limit]]
    if rel:
        got = _rels([r["id"] for r in out], now)
        for r in out:
            r["rel"] = (got.get(r["id"]) or {}).get("rel_total")
    return {"runs": out, "more": len(rs) > limit, "db": read}


def _geo_text(geo, lang):
    key = spool.GEO_TEXT.get(geo) if isinstance(geo, str) else None
    if not key:
        return None
    from .. import i18n
    return i18n.tr(key, lang)


def _test(t, lang=None):
    ex = _loads(t.get("extra"), {})
    ex = ex if isinstance(ex, dict) else {}
    out = dict((k, t.get(k)) for k in ("test", "ts", "value", "unit", "mode", "ok", "why", "under_load",
                                       "value_corr", "people", "ca"))
    out["dns_src"] = ex.get("dns_src")          # what the DNS test asked
    out["target"] = ex.get("target") if isinstance(ex.get("target"), str) else None   # the point of test 8/8b
    out["geo"] = ex.get("geo") if ex.get("geo") in spool.GEO else None
    out["geo_text"] = _geo_text(out["geo"], lang)
    w = ex.get("win")                           # 8c over a short window (Н-1): its seconds — the hint of the panel
    if isinstance(w, (int, float)) and not isinstance(w, bool) and 0 < w <= 60:
        out["win"] = w
    return out


def run(rid, lang=None, rel=False):
    """One check (§3.3): the row + ``tests`` [{test, ts, value, unit, mode, ok, why, under_load, value_corr,
    people, ca, dns_src, target, geo, geo_text, win}] (``win`` — only a row of 8c over a short window, Н-1;
    ``geo`` — «unknown» / «unknown_ru»: the country of the node was not known when test 8 ran,
    ``geo_text`` — its note for a person, REV-ns-b3-2 З-1) + ``cost_real`` {rx, tx, cpu_s, dur_s, estimate}; None — no such check (or a bad id).
    ``estimate`` — the estimate of that moment, not kept yet (None). ``rel`` — also ``rel``: {"rel_total", "tests":
    {test: {"usual", "usual_n", "rel"}}} of ``kaskad.score.check_rel`` («обычно» at the check, its base days, rel of
    the value); None — not a good check or older than 400 days."""
    if not isinstance(rid, str) or not spool.ID.fullmatch(rid):
        return None
    got, _eps, _read = _merged(0, 2 ** 32, rid)
    r = got.get(rid)
    if r is None:
        return None
    out = _detail(r, lang)
    if rel:
        c = _rels([rid]).get(rid)
        out["rel"] = None if c is None else {"rel_total": c["rel_total"], "tests": dict(
            (t, {"usual": None if x["usual"] is None else round(x["usual"], 3), "usual_n": x["usual_n"],
                 "rel": x["rel"]}) for t, x in sorted(c["tests"].items()))}
    return out


def details(frm=None, to=None, now=None, lang=None):
    """Every check of [frm, to] (as ``runs``) in the shape of ``run`` — one read of the base and the queue, the oldest
    first (the export, §3.4). -> ({"runs": [...], "db": bool})."""
    lo, hi = span(frm, to, now)
    got, _eps, read = _merged(lo, hi)
    rs = sorted((r for r in got.values() if lo <= r["ts"] < hi), key=lambda r: (r["ts"], r["id"]))
    return {"runs": [_detail(r, lang) for r in rs], "db": read}


EXPORT_TEST = ("test", "ts", "value", "unit", "mode", "ok", "why", "under_load", "value_corr", "people", "ca",
               "dns_src", "geo")


def for_export(runs_):
    """The checks of ``details`` for the file of the export (SS §3.4; the coordinator 19.10): a row of a test keeps
    ``EXPORT_TEST`` only — the code ``geo`` (``unknown``) without its text ``geo_text`` (a file is data, not a screen
    in one language). -> new list."""
    out = []
    for r in runs_:
        r = dict(r)
        r.pop("by_text", None)                       # «кто запустил» — код ``by``; текст — только why_text (§3.4)
        if isinstance(r.get("tests"), list):
            r["tests"] = [dict((k, t.get(k)) for k in EXPORT_TEST) for t in r["tests"] if isinstance(t, dict)]
        out.append(r)
    return out


def _detail(r, lang=None):
    out = _row(r, lang)
    out["sched_id"] = r.get("sched_id")
    out["retry_of"] = r.get("retry_of")
    out["tests"] = [_test(t, lang) for t in sorted(r["tests"], key=lambda t: (t["ts"], t["test"]))]
    out["cost_real"] = {"rx": r.get("rx"), "tx": r.get("tx"), "cpu_s": r.get("cpu_s"), "dur_s": r.get("dur_s"),
                        "estimate": None}
    return out


def rel_pair(a, b, now=None):
    """(rel_total of the check ``a``, of ``b``) — each against «обычно» at it (``kaskad.score.check_rel``, NS-D33);
    None — not counted (too little history, a failed check, no such check in the last 400 days)."""
    sc = _score()
    if sc is None:
        return None, None
    smp = sample(now)
    by_id = dict((r["id"], r) for r in smp["runs"])
    spec = sc.load_spec()
    out = []
    for rid in (a, b):
        r = by_id.get(rid)
        out.append(sc.check_rel(spec, smp["runs"], r, epochs=smp["epochs"])["rel_total"] if r else None)
    return tuple(out)


_REL = {"cur": (None, {})}         # (key, {id: check_rel}) of ONE history (the content of the sample) — the list asks
                                   # the same page again (the screen opened, «ещё»): not counted twice; one tuple — a
                                   # thread never sees the key of one history with the answers of another


def reset():
    """Forget the counted rel of the checks (tests)."""
    _REL["cur"] = (None, {})


def _rel_key(sc, smp):
    """The content of what check_rel reads — every check (values too) and event of the sample, the zone of the
    days, the catalogue: any change is another history (nothing guessed from counts)."""
    import hashlib
    z = tz.zone()
    doc = repr((smp["runs"], smp["epochs"], getattr(z, "name", None), getattr(z, "ok", None),
                len(getattr(z, "trans", ()) or ()), sc.SPEC.get("version")))
    return hashlib.sha256(doc.encode("utf-8", "replace")).hexdigest()


def _rels(ids, now=None):
    """{id: ``kaskad.score.check_rel``} of the good checks ``ids`` — one ``sample`` (the last 400 days), the series of
    the tests read once for all of them (``score.rel_cx``: the same answer as one by one), kept while the history is
    the same (``_REL``); a check not good or not in the sample — not in the answer. The answers are shared: read only."""
    sc = _score()
    if sc is None or not ids:
        return {}
    smp = sample(now)
    key = _rel_key(sc, smp)
    had, rels = _REL["cur"]
    rels = rels if had == key else {}
    by_id = dict((r["id"], r) for r in smp["runs"])
    spec = cx = None
    out = {}
    for rid in ids:
        r = by_id.get(rid)
        if r is None or not sc.good_run(r):
            continue
        if rid not in rels:
            if cx is None:
                spec, cx = sc.load_spec(), sc.rel_cx(smp["runs"])
            rels[rid] = sc.check_rel(spec, smp["runs"], r, epochs=smp["epochs"], _cx=cx)
        out[rid] = rels[rid]
    _REL["cur"] = (key, rels)
    return out


def compare(a, b, lang=None, rel=None):
    """Two checks side by side (§3.3; NS-D33): {"a": run(a), "b": run(b), "mode_differs", "rows": [{test, a, b, delta,
    delta_pct, unit, dir}], "rel_a", "rel_b"} + "better" ("a" | "b" | "same" by rel_total) only when both have rel_total;
    ``delta`` = b − a in the unit of the test, ``delta_pct`` — the same in % of ``a`` (None when a is 0); ``rel`` —
    (rel_a, rel_b) already counted (tests), else ``rel_pair``. None — either is not there."""
    ra, rb = run(a, lang), run(b, lang)
    if ra is None or rb is None:
        return None
    ta = dict((t["test"], t) for t in ra["tests"])
    tb = dict((t["test"], t) for t in rb["tests"])
    order = [t["test"] for t in ra["tests"]] + [t["test"] for t in rb["tests"] if t["test"] not in ta]
    sc = _score()
    tmap = sc.tests_of(sc.load_spec()) if sc is not None else {}
    rows = []
    for k in order:
        x, y = ta.get(k) or {}, tb.get(k) or {}
        va, vb = x.get("value"), y.get("value")
        d = (vb - va) if va is not None and vb is not None else None
        rows.append({"test": k, "a": va, "b": vb, "delta": d,
                     "delta_pct": round(100.0 * d / va, 1) if d is not None and va else None,
                     "unit": x.get("unit") or y.get("unit"),
                     # UI-C: «больше — лучше» (higher) или «меньше — лучше» (lower) — из каталога ядра; ▲/▼ разницы
                     # на экране; тест вне каталога — None (без стрелки)
                     "dir": (tmap.get(k) or {}).get("better")})
    rel_a, rel_b = rel_pair(a, b) if rel is None else rel
    out = {"a": ra, "b": rb, "mode_differs": ra["mode"] != rb["mode"], "rows": rows, "rel_a": rel_a, "rel_b": rel_b}
    if rel_a is not None and rel_b is not None:
        out["better"] = "same" if rel_a == rel_b else ("a" if rel_a > rel_b else "b")
    return out


# ── the export (§3.4) ───────────────────────────────────────────────────────

CSV_COLS = ("run_id", "run_ts_iso", "mode", "by", "status", "why", "test", "value", "unit", "under_load")
_FORMULA = ("=", "+", "-", "@", "\t", "\r")      # a cell a spreadsheet would take for a formula gets «'» first


def iso(ts, z=None):
    """``ts`` as the local time of the node with its offset: 2026-10-19T03:45:12+03:00."""
    t = tz.local(ts, z)
    off = t.tm_gmtoff
    return "%s%s%02d:%02d" % (time.strftime("%Y-%m-%dT%H:%M:%S", t), "+" if off >= 0 else "-", abs(off) // 3600,
                              abs(off) % 3600 // 60)


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, float):
        v = repr(round(v, 6))
    v = str(v)
    return "'" + v if v.startswith(_FORMULA) else v


def to_csv(runs_, z=None):
    """The rows of ``details`` as CSV text (§3.4): the English names of the fields as the header, a row per test; a
    check without tests (a skip, a failure before the first test) — one row with empty test, value, unit.
    Only numbers and codes; a cell starting with = + - @ (or a tab, a CR) gets «'» in front."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(CSV_COLS)
    for r in runs_:
        head = [r.get("id"), iso(r["ts"], z), r.get("mode"), r.get("by"), r.get("status"), r.get("why")]
        tests = r.get("tests") or [{}]
        for t in tests:
            w.writerow([_cell(v) for v in head + [t.get("test"), t.get("value"), t.get("unit"),
                                                   t.get("under_load", r.get("under_load"))]])
    return buf.getvalue()


# ── the raw material of the score (§4; the rules are kaskad.score) ──────────

def score_run(r):
    """A check in exactly the shape ``kaskad.score`` takes (its docstring «History»; круг 2, С-1): {id, ts, mode, by,
    retry_of, status, under_load True | False | None, vals: {test: {v, mode, ok: False (failed), why, corr,
    people, + bench.extra: n, lo, points, src, wait, catalog, budget}, "_engines", "_cpu_flags" (bench_run.meta: the
    runner's engines/cpu_flags, else inv.engines/inv.flags)}} —
    the reverse of ``spool.check_doc``; no rule applied here."""
    ul = r.get("under_load")
    vals = {}
    for t in r.get("tests") or ():
        m = {"v": t.get("value"), "mode": t.get("mode") or r["mode"]}
        if t.get("ok") == 0:                         # failed as the runner said (NULL — not said: no key)
            m["ok"] = False
        for k, src in (("corr", "value_corr"), ("people", "people"), ("why", "why")):
            if t.get(src) is not None:
                m[k] = t[src]
        for k, x in _loads(t.get("extra"), {}).items():
            if k in spool.EXTRA_KEYS:
                m[k] = x
        vals[t["test"]] = m
    meta = _loads(r.get("meta"), {})
    inv = meta.get("inv") if isinstance(meta.get("inv"), dict) else {}
    for k, f in spool.VALS_META.items():             # the runner's meta first, else the inventory (B1: inv only)
        got = meta.get(f) if isinstance(meta.get(f), list) else inv.get(spool.INV_OF_META[f])
        if isinstance(got, list):
            vals[k] = list(got)
    out = {"id": r["id"], "ts": r["ts"], "mode": r["mode"], "by": r["by"], "retry_of": r.get("retry_of"),
           "status": r["status"], "under_load": None if ul is None else bool(ul), "vals": vals}
    rinv = dict((k, inv[k]) for k in REL_INV if inv.get(k) is not None)
    if rinv:
        out["inv"] = rinv                            # cores, threads, model, memory, kdump, virt
    return out


def sample(now=None, days=400):
    """Every check of the last ``days`` (the base and the queue) as ``kaskad.score`` takes them: {"runs": [score_run],
    the oldest first, "epochs": [{ts, kind, args}], "events": [the stored bench_drop events, oldest first], "db"}."""
    now = int(time.time() if now is None else now)
    got, eps, read = _merged(now - days * DAY, now + 1)
    rs = sorted(got.values(), key=lambda r: (r["ts"], r["id"]))
    evs = (spool.drop_event(d) for r in rs for d in r.get("drops") or ())
    evs = sorted((e for e in evs if e["cond"] is not None),                # old-scale rows (cond None) — not events
                 key=lambda e: (e["ts"], str(e["run"]), e["link"]))
    return {"runs": [score_run(r) for r in rs], "epochs": [{"ts": ts, "kind": k, "args": a} for ts, k, a in eps],
            "events": evs, "db": read}


def view(spec, now=None, **kw):
    """A thin wrapper: ``kaskad.score.view(spec, runs, now, epochs=…, events=…, **kw)`` over ``sample`` — the events are
    the stored ones (no replay of drop_events per request). None — no module of the score yet (B4 comes with the merge)."""
    sc = _score()
    if sc is None or not hasattr(sc, "view"):
        return None
    now = int(time.time() if now is None else now)
    smp = sample(now)
    kw.setdefault("epochs", smp["epochs"])
    kw.setdefault("events", smp["events"])
    return sc.view(spec, smp["runs"], now, **kw)
