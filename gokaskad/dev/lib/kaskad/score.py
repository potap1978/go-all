"""The checks of the node against its OWN history (k3/handoff/SPEC-node-score.md, the owner's decision 23.10 and
NS-D30–NS-D34; SPEC-node-score-schedule-share.md §2.8, §4, NS-S10, NS-S15–S17; NS-D25). There is no absolute score, no
scale, no bands, no «weak link»: the raw values of the tests, «обычно» of this very node, ▲/▼ and the relative score
``rel`` (100 — as usual for this node). ONLY pure functions: no file, no database, no network, no clock — the time comes
as a parameter (``now``; ``day_of(ts)`` gives the local day ``YYYY-MM-DD`` of the node, ``hhmm_of(ts)`` its ``HH:MM``;
by default — ``kaskad.tz``).

``SPEC`` (``load_spec()``) — the catalogue of the tests (id, unit, direction ``better``, the floor of a value, loss per
sample) in groups, and the links (``links``: a test, a group of tests, crypto — the cipher of the node's engines).

History (B5 gives it from metrics.db; here — lists):
    run = {"id", "ts", "mode", "by" ("auto" = automatic repeat, NS-S15), "retry_of", "status" (ok|partial|…),
           "under_load": True|False|None (None — no accounting, «quiet: unknown»: taken as measured),
           "vals": {test: number | {"v", "mode", "n", "lo", "ok": False (failed), "corr" (NS-S16), "people"}},
           "inv": {"nproc", "smt", "cpu", "mem_gb", "crash_mb"} (optional: the inventory, «новая база» NS-S17)}
    probes  = windows of the light probes for «fresh» of the probe tests (NS-S10), newest first:
              [{"day", "hours", "rtt": x, "jitter": {"v", "n"}, "steal_p95": x, "loss": {anchor: {"n", "k", "e"}}}]
    probe_days = {test: [{"day", "v", "n", "lo", "hours"}]} — daily values of the probe tests for «usual» and ▲/▼
    epochs  = [{"ts", "kind": cpu|mem|tariff|engines|openssl}] — «новая база» (NS-S17)
    events  = drop_events(...) — sharp drops (NS-D33)

    fresh(spec, runs, now, ...)      the fresh values F (median of the 3 last good raw values, NS-S10)
    check_vals(spec, runs, run)      the values of a check with its automatic repeat (the better of the two per test)
    view(spec, runs, now, ...)       "summary", "dev_rule", "links" of /api/host/score (SS §4; rel — NS-D30, rel_total —
                                     NS-D31); at the end of a day — the links of /api/host/day/<date>
    rel_of / rel_total               the relative score of a value against «обычно» and the total of the links
    drop_events / retry_wanted       «резкое падение» and the automatic repeat: rel of a link ≤ 50 (NS-D33)
    check_rel                        rel of one check against «обычно» at it (the comparison of two checks, NS-D33);
                                     rel_cx — what check_rel of many checks shares (the list of the checks)
    quiet_level / load_correct       the channel under load (NS-S16)
    day_color / day_devs / offline_minutes   the colour of a day of the 30-day strip (NS C6)
"""

import bisect
import copy
import datetime
import functools
import math
import statistics

Z = 1.96                            # Wilson lower bound for the loss link (one-sided 97.5 %)
ALL_ENGINES = ("awg", "wg", "vless")

GOOD_STATUS = ("ok", "partial")
CHANNEL = ("net_down", "net_up")
PROBE_TESTS = ("rtt", "loss", "jitter", "steal_p95", "cpu_canary_cv")   # source "probes" (NS-S10): host_ping_h1 /
                                                                        # host_h1 (steal, the canary — C6)
HISTORY_TESTS = PROBE_TESTS + ("net_cv",)             # made from the history, never from the values of one check (C6)
PROBE_LINKS = ("loss", "net_q", "stab")                 # links of the probes: «source: probes», a ▼ colours every day
TEST_LINKS = ("down", "up", "crypto", "net_bw", "cpu")  # links of the tests: automatic repeat and bench_drop (§2.8 п. 1)
DAY = 86400
FRESH_DAYS = 45                     # NS-S10: a value older than this is not fresh
FRESH_FULL_DAYS = 30                # the channel: a full value is fresh <= 30 d (A.1 modes.fresh_full_days)
QUICK_OK = 0.8                      # the quick median does not contradict the full one while >= 80 % of it
WINDOW_HOURS_MIN = 12               # a day (window) of probes with < 12 h of rows does not count
BASE_DAYS = 60                      # NS-D25: the base of the tests — 60 d before the first day of the window
BASE_MAX = 10                       # … the last 10 daily values at most
PROBE_BASE_DAYS = 30                # … of the probes — 30 d (max(30, 8 k_p) when k_p > 1)
N_BASE_MIN = 4                      # «мало истории» below (▲/▼, NS-D25)
REL_N_MIN = 3                       # NS-D30, NS-D35: rel only when «обычно» has >= 3 base DAYS («… история N из 3 дней»)
REL_MAX = 300                       # NS-D30: rel is kept within 0…300
REL_TOTAL_MIN = 3                   # NS-D31: rel_total — of >= 3 links with rel
DROP_REL = 50                       # NS-D33: «резкое падение» and the automatic repeat — rel of a link <= 50
DROP_COND = "rel"                   # bench_drop.cond of NS-D33 (the one condition)
SPREAD_MIN = 8                      # p25/p75 only from 8 base points (П3)
DEV_SPACING_DAYS = 1                # k of the tests (B9 measures lag1; until then 1)
DEV_SPACING_DAYS_PROBES = 1         # k_p of the probes (until B9 — 1)
DEV_RULE = {"pct": 10, "use_spread": True}
PCT_MIN, PCT_MAX = 5, 30
PUSH_DAYS = 7                       # a push of a pair (link, condition) at most once in 7 d
FIRST_S = DAY                       # «first»: < 24 h since the first good check
NOW_DAYS = 30                       # NS §6.2: a link without a measurement for 30 d — now: null «не измерено»
PARTS = ((0, 7, "night"), (7, 12, "morning"), (12, 18, "day"), (18, 24, "evening"))
# the stability from the history of A (NS C6; RS 3.4, 3.5)
CONTAINER_VIRT = ("openvz", "lxc", "lxc-libvirt", "systemd-nspawn", "docker", "podman", "rkt", "wsl", "proot", "pouch")
HIDDEN_STEAL_P95 = 0.2              # RS 3.4: steal p95 over 7 d below this …
HIDDEN_CANARY_CV = 10.0             # … while the canary jumps more than this (%, quiet hours) — steal looks hidden
CANARY_BUSY = 50.0                  # an hour of the canary counts while the node is busy < 50 % (RS 3.5)
CANARY_HOURS_MIN = 12               # a day (window) of the canary: >= 12 clock hours with its quiet samples
CANARY_DAY_H = 24                   # A.1: the canary goes to the total after >= 24 h with it (canary_h of the windows)
STEAL_7D_SLACK = 3600               # steal over 7 d is known when its rows reach back to now − 7 d + this (С-2)
NET_CV_MIN = 3                      # RS 3.5: the spread of the channel from >= 3 heavy tests of one mode …
NET_CV_MAX = 10                     # … the last 10 of them, one per clock hour
STAB_WHY = ("container", "hidden", "short_history", "no_canary", "few_hours", "few_tests")   # why of the stability
                                                                                               # tests (C6)
USUAL_AS_FRESH = ("net_cv",)        # a test made of the history itself: «обычно» = its fresh value (REV-ns-c6 С-1)

# K = t(1 − 0.025/m; df) — the Student quantile (NS-D25; the standard library has none). df 3..9 = the table of the ТЗ
# (printed by k3/handoff/data/node-score-samples/dev_calc.py), df 10..29 — the same function (dev_calc.tq) for the base
# of the probes (up to 30 daily values); df > 29 takes df 29 (a wider gap — fewer false marks).
T_TABLE = {
    3: (3.182, 4.177, 4.857, 5.392, 5.841), 4: (2.776, 3.495, 3.961, 4.315, 4.604),
    5: (2.571, 3.163, 3.534, 3.810, 4.032), 6: (2.447, 2.969, 3.287, 3.521, 3.707),
    7: (2.365, 2.841, 3.128, 3.335, 3.499), 8: (2.306, 2.752, 3.016, 3.206, 3.355),
    9: (2.262, 2.685, 2.933, 3.111, 3.250), 10: (2.228, 2.634, 2.870, 3.038, 3.169),
    11: (2.201, 2.593, 2.820, 2.981, 3.106), 12: (2.179, 2.560, 2.779, 2.934, 3.055),
    13: (2.160, 2.533, 2.746, 2.896, 3.012), 14: (2.145, 2.510, 2.718, 2.864, 2.977),
    15: (2.131, 2.490, 2.694, 2.837, 2.947), 16: (2.120, 2.473, 2.673, 2.813, 2.921),
    17: (2.110, 2.458, 2.655, 2.793, 2.898), 18: (2.101, 2.445, 2.639, 2.775, 2.878),
    19: (2.093, 2.433, 2.625, 2.759, 2.861), 20: (2.086, 2.423, 2.613, 2.744, 2.845),
    21: (2.080, 2.414, 2.601, 2.732, 2.831), 22: (2.074, 2.405, 2.591, 2.720, 2.819),
    23: (2.069, 2.398, 2.582, 2.710, 2.807), 24: (2.064, 2.391, 2.574, 2.700, 2.797),
    25: (2.060, 2.385, 2.566, 2.692, 2.787), 26: (2.056, 2.379, 2.559, 2.684, 2.779),
    27: (2.052, 2.373, 2.552, 2.676, 2.771), 28: (2.048, 2.368, 2.546, 2.669, 2.763),
    29: (2.045, 2.364, 2.541, 2.663, 2.756),
}


# The catalogue of the tests and the links (was the part of score-curves.json the history needs; the scale itself — the
# anchors, the curves, the bands, the weights, the capacity — is gone, NS-D34). floor — the least value a test can have
# (x_eff = max(x, floor), the same numbers as before: ▲/▼ do not change, NS-D32); per_sample — loss: the floor is also
# 100/n; clamp_max — the most it can be; rel_floor — the floor of rel (NS-D30, NS-D35): EVERY test where less is better
# has one — loss 0.1 %, delay 1 ms, jitter 0.5 ms, dns 1 ms, steal 1 %, cpu_cv and cpu_canary_cv 1 %, fsync 0.1 ms
# (the least value the panel shows: one decimal); both values below it — rel 100: 0 % of loss against 0 % usually is
# 100, not «no number» or infinity; noise of tenths of a percent of steal is not «10 of usual». net_cv has no rel
# (USUAL_AS_FRESH) — no floor.
# unit — the unit as the core keeps it (RU, a key: the screens put their own words), hence «i18n: skip».
SPEC = {
    "version": "rel-1",
    "groups": [
        {"id": "net_bw", "tests": [
            {"id": "net_down", "unit": "Мбит/с", "better": "higher", "floor": 0.01},  # i18n: skip
            {"id": "net_up", "unit": "Мбит/с", "better": "higher", "floor": 0.01}]},  # i18n: skip
        {"id": "net_q", "tests": [
            {"id": "rtt", "unit": "мс", "better": "lower", "floor": 0.05, "rel_floor": 1.0},  # i18n: skip
            {"id": "loss", "unit": "%", "better": "lower", "floor": 0.005, "per_sample": True, "rel_floor": 0.1},
            {"id": "jitter", "unit": "мс", "better": "lower", "floor": 0.05, "rel_floor": 0.5},  # i18n: skip
            {"id": "dns", "unit": "мс", "better": "lower", "floor": 0.05, "rel_floor": 1.0}]},  # i18n: skip
        {"id": "cpu", "tests": [
            {"id": "cpu_1t", "unit": "событий/с", "better": "higher", "floor": 0.00025},  # i18n: skip
            {"id": "cpu_mt", "unit": "событий/с", "better": "higher", "floor": 0.00025},  # i18n: skip
            {"id": "chacha_1t", "unit": "МБ/с", "better": "higher", "floor": 0.00015},  # i18n: skip
            {"id": "aes_1t", "unit": "МБ/с", "better": "higher", "floor": 0.0003}]},  # i18n: skip
        {"id": "stab", "tests": [
            {"id": "steal_p95", "unit": "%", "better": "lower", "floor": 0.05, "rel_floor": 1.0},
            {"id": "cpu_cv", "unit": "%", "better": "lower", "floor": 0.3, "rel_floor": 1.0},
            {"id": "cpu_sustain", "unit": "×", "better": "higher", "floor": 5e-07, "clamp_max": 1.0},
            {"id": "cpu_canary_cv", "unit": "%", "better": "lower", "floor": 0.5, "rel_floor": 1.0},
            {"id": "net_cv", "unit": "%", "better": "lower", "floor": 1}]},
        {"id": "mem", "tests": [
            {"id": "mem_gb", "unit": "ГБ", "better": "higher", "floor": 5e-07},  # i18n: skip
            {"id": "mem_bw", "unit": "МиБ/с", "better": "higher", "floor": 0.0015}]},  # i18n: skip
        {"id": "disk", "tests": [
            {"id": "disk_rr", "unit": "IOPS", "better": "higher", "floor": 0.001},
            {"id": "disk_rw", "unit": "IOPS", "better": "higher", "floor": 0.0005},
            {"id": "disk_fsync", "unit": "мс", "better": "lower", "floor": 0.02, "rel_floor": 0.1}]},  # i18n: skip
    ],
    # the links, in the order of the screen; a link of groups — every test of them; crypto — the cipher the node's
    # engines really use (ChaCha20 for AmneziaWG/WireGuard and VLESS without the flag aes; AES-GCM for VLESS with it)
    "links": [
        {"id": "net_bw", "groups": ["net_bw"]},
        {"id": "net_q", "groups": ["net_q"]},
        {"id": "cpu", "groups": ["cpu"]},
        {"id": "stab", "groups": ["stab"]},
        {"id": "down", "tests": ["net_down"]},
        {"id": "up", "tests": ["net_up"]},
        {"id": "loss", "tests": ["loss"]},
        {"id": "crypto", "tests": ["chacha_1t", "aes_1t"],
         "only_if": {"chacha_1t": [{"engine": ["awg", "wg"]}, {"engine": ["vless"], "no_cpu_flag": "aes"}],
                     "aes_1t": [{"engine": ["vless"], "cpu_flag": "aes"}]}},
    ],
}


def load_spec():
    """A copy of ``SPEC`` (a caller may change its copy)."""
    return copy.deepcopy(SPEC)


def tests_of(spec):
    """{id: test} of the groups that hold their tests (not ``tests_from_abs``)."""
    return {t["id"]: t for g in spec["groups"] for t in g.get("tests", ())}


def _group_tests(g, tests_by_id):
    return g.get("tests") or [tests_by_id[i] for i in g["tests_from_abs"]]


# ── one test, one check (RESEARCH A.4) ─────────────────────────────────────

def _u(t, v):
    v = math.log(v)
    return v if t["better"] == "higher" else -v


def floor_of(t):
    return t.get("floor") or 1e-12


def wilson_lo(k, n, z=Z):
    """Lower Wilson bound of k successes in n trials (0 when k == 0)."""
    if n <= 0 or k <= 0:
        return 0.0
    p = k / float(n)
    return max(0.0, (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n))


def best2(xs):
    """'Seen by >= 2 of 3 anchors': the worst anchor is dropped, the mean of the other two; 2 -> the better; 1 -> it."""
    xs = sorted(xs)
    keep = xs[:2] if len(xs) == 3 else xs[:1]
    return sum(keep) / len(keep)


def loss_value(points):
    """points: per anchor {"n": sent, "k": lost, "e": events} -> {"v": node loss %, "n": sent, "lo": the link value %}
    (RS A.4: the share in packets, events only widen the Wilson bound)."""
    pts = [p for p in points if p["n"] > 0]
    if not pts:
        return {"v": None, "why": "no_probes"}

    def lo(p):
        if p["k"] <= 0:
            return 0.0
        d = max(1.0, p["k"] / max(p["e"], min(p["k"], 1.0)))      # packets per event, >= 1; no events given = one
        return 100.0 * wilson_lo(p["k"] / d, p["n"] / d)
    return {"v": best2([100.0 * p["k"] / p["n"] for p in pts]), "n": sum(p["n"] for p in pts),
            "lo": best2([lo(p) for p in pts]), "points": points}


def _mode(m):
    return (m.get("mode") or "full") if isinstance(m, dict) else "full"


def _nonfinite(x):
    """A number that is NaN or ±inf (or too large for a float): such a value is «не измерено», never a score."""
    if x is None or isinstance(x, bool):
        return False
    try:
        return not math.isfinite(float(x))
    except OverflowError:
        return True
    except (TypeError, ValueError):
        return False


def _clean(m):
    """A value dict (a copy) without non-finite numbers: v, lo, n not finite -> None (not measured); corr, people not
    finite -> dropped (no corrected value)."""
    if any(_nonfinite(m.get(f)) for f in ("v", "lo", "n")):
        return None
    for f in ("corr", "people"):
        if _nonfinite(m.get(f)):
            m.pop(f)
    return m


def _applies(conds, engines, flags):
    """A test is in a link when one of its conditions holds (no conditions = always)."""
    if not conds:
        return True
    for c in conds:
        if "engine" in c and not set(c["engine"]) & set(engines):
            continue
        if "cpu_flag" in c and c["cpu_flag"] not in flags:
            continue
        if "no_cpu_flag" in c and c["no_cpu_flag"] in flags:
            continue
        return True
    return False


def with_mode(vals, mode):
    """The rule of moving the samples (SS §4, З-11): every value of a check of ``mode`` gets ``"mode"``."""
    out = {}
    for k, m in vals.items():
        if k.startswith("_") or m is None:
            out[k] = m
        elif isinstance(m, dict):
            out[k] = dict(m, mode=mode)
        else:
            out[k] = {"v": m, "mode": mode}
    return out


def _pos(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and not _nonfinite(x) and x > 0


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and not _nonfinite(x)


# ── history: days, usable values, «новая база» ─────────────────────────────

def _default_day(ts):
    from kaskad import tz
    return tz.day(ts)


def _default_hhmm(ts):
    import time
    from kaskad import tz
    return time.strftime("%H:%M", tz.local(ts))


@functools.lru_cache(maxsize=8192)
def _ord(day):
    y, m, d = (int(x) for x in day.split("-"))
    return datetime.date(y, m, d).toordinal()


def _memo(day_of):
    """day_of with a cache by ts (a replay of the history asks the same moments many times)."""
    day_of = day_of or _default_day
    if getattr(day_of, "_kaskad_memo", False):
        return day_of
    cache = {}

    def f(ts):
        v = cache.get(ts)
        if v is None:
            v = cache[ts] = day_of(ts)
        return v
    f._kaskad_memo = True
    return f


def _iso(o):
    return datetime.date.fromordinal(o).isoformat()


def valid_dev_rule(doc):
    """gateway.hostmon.dev = {"pct": 5…30, "use_spread": bool} -> the clean rule; ValueError with a code otherwise."""
    if not isinstance(doc, dict) or set(doc) - {"pct", "use_spread"}:
        raise ValueError("dev_rule")
    pct = doc.get("pct", DEV_RULE["pct"])
    spread = doc.get("use_spread", DEV_RULE["use_spread"])
    if isinstance(pct, bool) or not isinstance(pct, int) or not PCT_MIN <= pct <= PCT_MAX:
        raise ValueError("dev_pct")
    if not isinstance(spread, bool):
        raise ValueError("dev_spread")
    return {"pct": pct, "use_spread": spread}


def epoch_since(spec, epochs, day_of=None, now=None):
    """NS-S17: {test: first day (ordinal) of its base}; cpu -> groups cpu and stab, mem -> mem, tariff -> all,
    openssl -> the tests of the link crypto (решение координатора 19.10), engines -> nothing. An event after ``now``
    does not exist yet."""
    day_of = day_of or _default_day
    groups = {g["id"]: [t["id"] for t in g.get("tests", ())] for g in spec["groups"]}
    every = [t for ts in groups.values() for t in ts]
    crypto = [t for b in spec["links"] if b["id"] == "crypto" for t in b.get("tests", ())]
    hit = {"cpu": groups.get("cpu", []) + groups.get("stab", []), "mem": groups.get("mem", []), "tariff": every,
           "openssl": crypto}
    since = {}
    for ev in epochs or ():
        if now is not None and ev["ts"] > now:
            continue
        o = _ord(day_of(ev["ts"]))
        for t in hit.get(ev.get("kind"), ()):
            since[t] = max(since.get(t, o), o)
    return since


def inv_changes(prev, cur):
    """NS-S17: the kinds of «новая база» between the inventory of the last good check and this one (test 0, bench_run.inv:
    cpu, nproc, mem_gb, engines, openssl_ver): cpu — the model or nproc changed; mem — MemTotal by more than ± 10 %;
    engines — the set of engines; openssl — the version of OpenSSL changed (only when both inventories have it; the
    crypto tests depend on it, решение координатора 19.10). The tariff comes from C1, not from here."""
    if not prev or not cur:
        return []
    out = []
    known = lambda k: prev.get(k) is not None and cur.get(k) is not None   # unknown (null) is not «changed»
    if any(known(k) and prev[k] != cur[k] for k in ("cpu", "nproc")):
        out.append("cpu")
    a, b = prev.get("mem_gb"), cur.get("mem_gb")
    if a and b and round(abs(b / float(a) - 1.0), 9) > 0.10:   # exactly ±10 % is no change either way (floats)
        out.append("mem")
    if known("engines") and sorted(prev["engines"]) != sorted(cur["engines"]):
        out.append("engines")
    va, vb = prev.get("openssl_ver"), cur.get("openssl_ver")
    if va and vb and va != vb:
        out.append("openssl")
    return out


def inv_known(prev, cur):
    """NS-S17 along a chain of checks (REV-ns-b6 З-2): the last KNOWN value of every key — ``cur``'s non-null keys over
    ``prev`` (an older inventory, or the known one so far). A check whose model or engines were not read (null) does not
    break the chain: A(EPYC) → B(null) → C(Xeon) is a change of the CPU; compare inv_changes(known before, C)."""
    out = dict(prev or {})
    out.update((k, v) for k, v in (cur or {}).items() if v is not None)
    return out or None


def base_since(epochs, day_of=None, now=None):
    """score.base_since: the latest event of each kind not after ``now``, [{"kind", "day"}] by day."""
    day_of = day_of or _default_day
    last = {}
    for ev in epochs or ():
        if now is not None and ev["ts"] > now:
            continue
        if ev.get("kind") in ("cpu", "mem", "tariff", "engines", "openssl") and ev["ts"] >= last.get(ev["kind"], -1):
            last[ev["kind"]] = ev["ts"]
    return [{"kind": k, "day": day_of(ts)} for k, ts in sorted(last.items(), key=lambda kv: (kv[1], kv[0]))]


def good_run(run):
    return run.get("status", "ok") in GOOD_STATUS


def quiet(run):
    """Not under load by the accounting of NS-S8 (None — no accounting: not known to be quiet)."""
    return "under_load" not in run or (run["under_load"] is not None and not run["under_load"])


def not_loaded(run):
    """«Годная» for the last check: not under load (no accounting — taken as measured)."""
    return not run.get("under_load")


def run_vals(run):
    """The values of a check by itself: failed or not measured values are not
    measured; under load the channel goes by its corrected value (or not at all); the wait and catalog marks stay."""
    out = {}
    for k, m in (run.get("vals") or {}).items():
        if k.startswith("_"):
            out[k] = m
            continue
        m = _clean(dict(m) if isinstance(m, dict) else {"v": m})
        if m is None or m.get("ok") is False:
            continue                                # failed, or not a finite number: not measured
        m.setdefault("mode", run.get("mode") or "full")
        if run.get("under_load") and k in CHANNEL and m.get("v") is not None:
            if m.get("corr") is None:
                continue                            # «под нагрузкой, не годен»
            m["raw"], m["v"] = m["v"], m["corr"]
        out[k] = m
    return out


def usable(run, tid):
    """The value of a test in a run that may go to «fresh» and to the base, or None: a good run, measured, not failed;
    a run under load gives only the channel, and only its corrected value (NS-S16)."""
    if not good_run(run) or tid == "net_cv":       # net_cv is made from the history (C6), never from one check
        return None
    m = (run.get("vals") or {}).get(tid)
    if m is None:
        return None
    m = _clean(dict(m) if isinstance(m, dict) else {"v": m})
    if m is None or m.get("v") is None or m.get("ok") is False or m.get("wait"):
        return None
    m.setdefault("mode", run.get("mode") or "full")
    if run.get("under_load"):
        if tid not in CHANNEL or m.get("corr") is None:
            return None
        m["raw"], m["v"] = m["v"], m["corr"]
    return m


def _points(runs, tid, now, day_of, mode=None, since=None, quiet_only=False, before=None, cx=None):
    """Usable values of a test, oldest first: [{"run", "ts", "day" (ordinal; a repeat — the day of its original),
    "m", "auto", "orig"}]. ``before`` — only runs with ts < before."""
    by_id = cx["by_id"] if cx else {r.get("id"): r for r in runs}
    use = cx["use"] if cx else None
    out = []
    for r in runs:
        if r["ts"] > now or (before is not None and r["ts"] >= before):
            continue
        if quiet_only and not quiet(r):
            continue
        if use is None:
            m = usable(r, tid)
        else:
            key = (r.get("id"), r["ts"], tid)
            if key not in use:
                use[key] = usable(r, tid)
            m = use[key]
        if m is None or (mode and m["mode"] != mode):
            continue
        auto = r.get("by") == "auto" and r.get("retry_of") in by_id
        day_ts = by_id[r["retry_of"]]["ts"] if auto else r["ts"]
        o = _ord(day_of(day_ts))
        if since is not None and o < since:
            continue
        out.append({"run": r.get("id"), "ts": r["ts"], "day": o, "m": m, "auto": auto,
                    "orig": r.get("retry_of") if auto else None, "quiet": quiet(r)})
    out.sort(key=lambda p: (p["ts"], str(p["run"])))
    return out


def _uv(t, m):
    """u of a value for ▲/▼: by lo for the per-sample tests (loss), clamped like the curve."""
    x = m.get("lo", m["v"]) if t.get("per_sample") else m["v"]
    x = min(max(float(x), floor_of(t), 1e-12), t.get("clamp_max", float("inf")))
    return _u(t, x)


def pick(t, ms):
    """NS-S10 of up to 3 values (any order): 3 -> the median, 2 -> the better, 1 -> it; [] -> None."""
    if not ms:
        return None
    srt = sorted(ms, key=lambda m: _uv(t, m))
    return srt[1] if len(srt) == 3 else srt[-1]


# ── fresh (NS-S10) ─────────────────────────────────────────────────────────

def _fresh_full(t, pts, now):
    """The pick of the full values of a channel test fresh at ``now`` (<= FRESH_FULL_DAYS): (value, [points])."""
    fulls = [p for p in pts if p["m"]["mode"] == "full" and now - p["ts"] <= FRESH_FULL_DAYS * DAY][-3:]
    return pick(t, [p["m"] for p in fulls]), fulls


def _fresh_test(t, pts, now):
    """-> (value dict, [points used]) or (None, [])."""
    if t["id"] in CHANNEL:
        f, fulls = _fresh_full(t, pts, now)
        quicks = [p for p in pts if p["m"]["mode"] != "full" and now - p["ts"] <= FRESH_DAYS * DAY][-3:]
        q = pick(t, [p["m"] for p in quicks])
        if f is not None and (q is None or q["v"] >= QUICK_OK * f["v"] or q.get("budget")):
            return f, fulls
        if q is not None:
            return q, quicks
        return None, []
    use = [p for p in pts if now - p["ts"] <= FRESH_DAYS * DAY][-3:]
    return pick(t, [p["m"] for p in use]), use


def fresh_probes(windows, tmap=None):
    """NS-S10 for the probe tests: the 3 sliding days, newest first; a window with < 12 h of rows does not count.
    rtt, steal_p95, cpu_canary_cv — the pick of the daily values; jitter {v, n} — v picked, n summed; loss — counts
    summed per anchor over the windows -> loss_value. The canary — only after CANARY_DAY_H hours with it in the 3
    windows (``canary_h``; A.1, REV-ns-c6 З-1): a window of 12 h has a value, the total waits for a day. The canary
    goes by its own hours, not by those of the probes (REV-ns-c6-2 З-1): it runs with the probes off or offline too.
    -> ({test: value}, {test: [days]})."""
    ws = (windows or ())[:3]
    use = [w for w in ws if w.get("hours", 24) >= WINDOW_HOURS_MIN]
    can_use = [w for w in ws if _canary_h(w) >= CANARY_HOURS_MIN]
    can_day = sum(_canary_h(w) for w in ws) >= CANARY_DAY_H
    vals, days = {}, {}
    for tid in ("rtt", "steal_p95", "jitter", "cpu_canary_cv"):
        if tid == "cpu_canary_cv" and not can_day:
            continue
        got = [(w, w[tid]) for w in (can_use if tid == "cpu_canary_cv" else use) if w.get(tid) is not None and
               (not isinstance(w[tid], dict) or w[tid].get("v") is not None) and
               (_clean(dict(w[tid]) if isinstance(w[tid], dict) else {"v": w[tid]}) is not None)]
        if not got:
            continue
        ms = [m if isinstance(m, dict) else {"v": m} for _w, m in got]
        v = dict(pick((tmap or tests_of(SPEC))[tid], ms))
        if tid == "jitter":
            v["n"] = sum(m.get("n") or 0 for m in ms)
        vals[tid] = v
        days[tid] = [w.get("day") for w, _m in got]
    sums = {}
    loss_days = []
    for w in use:
        if not w.get("loss"):
            continue
        loss_days.append(w.get("day"))
        for a, p in sorted(w["loss"].items()):
            if any(_nonfinite(p.get(f)) for f in ("n", "k", "e")):
                continue
            s = sums.setdefault(a, {"n": 0.0, "k": 0.0, "e": 0.0})
            for f in ("n", "k", "e"):
                s[f] += p.get(f, 0)
    if sums:
        lv = loss_value([sums[a] for a in sorted(sums)])
        if lv.get("v") is not None:
            lv.pop("points", None)
            vals["loss"] = lv
            days["loss"] = loss_days
    return vals, days


def _canary_h(w):
    """Hours with the canary in a window (``canary_h``; none — 0: the canary has no day without it)."""
    return w.get("canary_h") or 0


def _up_h(w):
    """Hours the node lived in a window: rows of host_m10 (``up_h``), never fewer than the hours of probes."""
    return max(w.get("up_h") or 0, w.get("hours") or 0)


def canary_cv(samples):
    """The canary of a day or window (RS 3.5, NS-D7, A.1): [(ts, canary_ms, busy)] of host_m10 — one sample of the
    canary every 10 min with the busy of the same row (REV-ns-c6 З-2: the spread of the samples, not of hourly means);
    the samples with the node busy < CANARY_BUSY -> (CV % of them, n clock hours they cover); fewer than
    CANARY_HOURS_MIN hours — (None, n)."""
    xs, hrs = [], set()
    for ts, ms, busy in samples or ():
        if _pos(ms) and _finite(ts) and (busy is None or (not _nonfinite(busy) and busy < CANARY_BUSY)):
            xs.append(float(ms))
            hrs.add(int(ts) // 3600)
    if len(hrs) < CANARY_HOURS_MIN:
        return None, len(hrs)
    mean = statistics.mean(xs)
    return 100.0 * statistics.stdev(xs) / mean, len(hrs)


def canary_hours(samples):
    """Clock hours with a sample of the canary at all (busy or not) — ``canary_h`` of a window (A.1 «>= 24 h»)."""
    return len(set(int(ts) // 3600 for ts, ms, _b in samples or () if _pos(ms) and _finite(ts)))


def up_hours(samples):
    """Clock hours with a row of host_m10 at all (the canary run or not) — ``up_h`` of a window: the node lived."""
    return len(set(int(ts) // 3600 for ts, _ms, _b in samples or () if _finite(ts)))


def net_cv_value(pts, now, since=None):
    """The spread of the channel (RS 3.5, test net_cv): CV % of the raw download of the heavy tests of one mode — not
    an automatic repeat, not under load, the budget not hit, fresh (<= FRESH_DAYS), of the new base; one test per
    clock hour (the last), the last NET_CV_MAX; the mode with more tests (full on a tie). pts — _points of net_down.
    -> ({"v", "n", "of_mode"}, [points]) or ({"v": None, "why": "few_tests", "n"}, []) — not measured, not «wait»:
    until 3 tests the total is «предварительно: разброс канала появится после 3 тестов» (RS 3.5)."""
    by_mode = {}
    for p in pts:
        m = p["m"]
        if p["auto"] or "raw" in m or m.get("budget") or now - p["ts"] > FRESH_DAYS * DAY or \
                (since is not None and p["day"] < since) or not _pos(m.get("v")):
            continue
        by_mode.setdefault(m["mode"], {})[p["ts"] // 3600] = p        # the last test of a clock hour
    best = max(sorted(by_mode), key=lambda md: (len(by_mode[md]), md == "full")) if by_mode else None
    use = [by_mode[best][h] for h in sorted(by_mode[best])][-NET_CV_MAX:] if best else []
    if len(use) < NET_CV_MIN:
        return {"v": None, "why": "few_tests", "n": len(use)}, []
    xs = [float(p["m"]["v"]) for p in use]
    mean = statistics.mean(xs)
    return {"v": 100.0 * statistics.stdev(xs) / mean, "n": len(use), "of_mode": best}, use


def stab_fix(F, meta, inv, windows, steal_7d):
    """The stability tests of the history (NS C6; RS 3.4, 3.5) in the fresh values F, in place:
    steal_p95 — null ``container`` in a container (lxc/openvz/…: its steal is the node's of the hoster, or none), null
    ``hidden`` when steal p95 over 7 d < 0.2 % while the canary jumps > 10 % (a hypervisor hides steal); ``steal_7d``
    is None until the rows of steal cover the 7 d (the caller, REV-ns-c6 С-2), and the canary has a value only after
    a day with it (fresh_probes, З-1) — so «hidden» never comes of half a day of history;
    cpu_canary_cv — without a value, by the hours of the canary itself and of the node, never of the probes (they may
    be off; REV-ns-c6-2 З-1) (windows: ``up_h`` — hours with rows of host_m10, ``canary_h`` — with a canary at all):
    ``short_history`` (wait) — < 24 h with the canary while the node lived < 24 h or the canary ran at least in every
    other hour it lived (the history is young); ``no_canary`` — it mostly did not run (the collector skips it while
    the server is busy ≥ 50 %) or a window had ≥ 12 h of it, none of them quiet; ``few_hours`` — a day with it, but no
    window has ≥ 12 hours of it (the node was off much of every day)."""
    virt = (inv or {}).get("virt")
    if "cpu_canary_cv" not in F and windows is not None:
        ws = windows[:3]
        up = sum(_up_h(w) for w in ws)
        can_h = sum(_canary_h(w) for w in ws)
        if can_h < CANARY_DAY_H and (up < CANARY_DAY_H or 2 * can_h >= up):
            F["cpu_canary_cv"] = {"v": None, "wait": True, "why": "short_history"}
        elif can_h < CANARY_DAY_H or any(_canary_h(w) >= CANARY_HOURS_MIN for w in ws):
            F["cpu_canary_cv"] = {"v": None, "why": "no_canary"}
        else:
            F["cpu_canary_cv"] = {"v": None, "why": "few_hours"}
        meta.pop("cpu_canary_cv", None)
    cv = F.get("cpu_canary_cv")
    cv = cv.get("v") if isinstance(cv, dict) else cv
    why = None
    if isinstance(virt, str) and virt in CONTAINER_VIRT:
        why = "container"
    elif _pos(cv) and cv > HIDDEN_CANARY_CV and steal_7d is not None and not _nonfinite(steal_7d) \
            and steal_7d < HIDDEN_STEAL_P95:
        why = "hidden"
    if why:
        F["steal_p95"] = {"v": None, "why": why}
        meta.pop("steal_p95", None)
    return why


def fresh(spec, runs, now, *, day_of=None, probes=None, epochs=(), tests_by_id=None, _cx=None, steal_7d=None):
    """The fresh values F (NS-S10) at ``now``. -> {"vals": F (+ the wait values and ctx of the last good run),
    "meta": {test: {"fresh_n", "runs": [ids], "ts"}}, "from": {"tests": [run ids], "days": [probe days]}}.
    The stability of the history (NS C6): net_cv from the checks (net_cv_value), the canary from the probes, steal
    null in a container or when hidden (stab_fix; ``steal_7d`` — steal p95 over the 7 d before ``now``).
    Probe tests come from ``probes`` windows when given, else like any other test."""
    day_of = _memo(day_of)
    since = epoch_since(spec, epochs, day_of, now)
    F, meta, used_runs, used_days = {}, {}, [], set()
    down_pts = None
    pv, pdays = fresh_probes(probes, dict(tests_of(spec), **(tests_by_id or {}))) if probes is not None else ({}, {})
    for g in spec["groups"]:
        for t in _group_tests(g, tests_by_id):
            tid = t["id"]
            if probes is not None and tid in PROBE_TESTS:
                if tid in pv:
                    F[tid] = pv[tid]
                    meta[tid] = {"fresh_n": len(pdays[tid]), "runs": [], "days": pdays[tid]}
                    used_days.update(d for d in pdays[tid] if d)
                continue
            if tid == "net_cv":
                continue                            # from the checks of net_down below
            pts = _points(runs, tid, now, day_of, since=since.get(tid), cx=_cx)
            if tid == "net_down":
                down_pts = pts
            v, used = _fresh_test(t, pts, now)
            if v is None:
                continue
            F[tid] = v
            meta[tid] = {"fresh_n": len(used), "runs": [p["run"] for p in used], "ts": [p["ts"] for p in used]}
            for p in used:
                if p["run"] not in used_runs:
                    used_runs.append(p["run"])
    tids = set(t["id"] for g in spec["groups"] for t in _group_tests(g, tests_by_id))
    if "net_cv" in tids:
        if down_pts is None:
            down_pts = _points(runs, "net_down", now, day_of, since=since.get("net_down"), cx=_cx)
        v, used = net_cv_value(down_pts, now, since.get("net_cv"))
        F["net_cv"] = v
        if used:
            meta["net_cv"] = {"fresh_n": len(used), "runs": [p["run"] for p in used], "ts": [p["ts"] for p in used]}
    last = last_good(runs, now)
    if probes is not None and tids & {"steal_p95", "cpu_canary_cv"}:
        stab_fix(F, meta, (last or {}).get("inv"), probes, steal_7d)
    if last is not None:                            # S.waiting and S.ctx — of the last good check
        lv = last.get("vals") or {}
        for k in ("_cpu_flags", "_engines"):
            if k in lv:
                F[k] = lv[k]
        for tid, m in lv.items():
            if tid not in F and isinstance(m, dict) and (m.get("wait") or m.get("catalog")):
                F[tid] = m
    order = {r.get("id"): r["ts"] for r in runs}
    used_runs.sort(key=lambda i: order.get(i, 0))
    return {"vals": F, "meta": meta, "from": {"tests": used_runs, "days": sorted(used_days)}}


def is_repeat(run, by_id):
    """An automatic repeat (NS-S15) of a known good original: it holds only the tests of the fallen links (§2.8 п. 2),
    so it is never «the last check» by itself — it goes together with its original (check_vals)."""
    if run.get("by") != "auto" or run.get("retry_of") not in by_id:
        return False
    return good_run(by_id[run["retry_of"]])


def _checks(runs):
    """{"by_id", "rep": {original id: [its good repeats]}} for check_vals."""
    by_id = {r.get("id"): r for r in runs}
    rep = {}
    for r in runs:
        if is_repeat(r, by_id) and good_run(r):
            rep.setdefault(r["retry_of"], []).append(r)
    return {"by_id": by_id, "rep": rep}


def _measured(m):
    return isinstance(m, dict) and m.get("v") is not None and not m.get("wait")


def check_vals(spec, runs, run, now=None, *, tests_by_id=None, _ck=None, src=None):
    """The values of a check together with its automatic repeat (§2.8 п. 6): a test of
    the repeat — the better of the two values, every other test, the wait/catalog marks, _cpu_flags and _engines — of
    the original. ``run`` may be the original or its repeat; repeats after ``now`` do not exist yet. ``src`` — a dict
    filled with {test: the run that gave its value}."""
    ck = _ck or _checks(runs)
    orig = ck["by_id"][run["retry_of"]] if is_repeat(run, ck["by_id"]) else run
    out = run_vals(orig)
    if src is not None:
        src.update((k, orig) for k in out if not k.startswith("_"))
    tmap = dict(tests_of(spec), **(tests_by_id or {}))
    for r in sorted(ck["rep"].get(orig.get("id"), ()) if orig.get("id") is not None else (),
                    key=lambda x: (x["ts"], str(x.get("id")))):
        if now is not None and r["ts"] > now:
            continue
        for k, m in run_vals(r).items():
            if k.startswith("_"):
                out.setdefault(k, m)
                continue
            a = out.get(k)
            if not _measured(m):
                continue
            if not _measured(a) or (k in tmap and _uv(tmap[k], m) > _uv(tmap[k], a)):
                out[k] = m
                if src is not None:
                    src[k] = r
    return out


def last_good(runs, now):
    """The last good check (status ok/partial, not under load, not an automatic repeat — that one goes with its
    original, check_vals) at ``now``."""
    by_id = {r.get("id"): r for r in runs}
    rs = [r for r in runs if r["ts"] <= now and good_run(r) and not_loaded(r) and not is_repeat(r, by_id)]
    return max(rs, key=lambda r: (r["ts"], str(r.get("id")))) if rs else None


# ── ▲/▼ by days (NS-D25) ───────────────────────────────────────────────────

def _day_values(t, pts):
    """[(day, u, floor, v)] oldest first: a pair «original + its automatic repeat» -> the better of the two, then the
    median of u of the day (of two — the mean). floor — of the per-sample tests (max(floor, 100/n)), else None.
    v — the value shown in the hint: of the per-sample tests (loss) the median of v of the same values (u goes by lo,
    NS-D25 decides by the lower bound), else None (the raw of u)."""
    by_run = {p["run"]: p for p in pts}
    paired = {p["orig"] for p in pts if p["auto"] and p["orig"] in by_run}
    days = {}
    for p in pts:
        if p["run"] in paired:
            continue                                # goes with its repeat
        items = [p] if not p["auto"] or p["orig"] not in by_run else [p, by_run[p["orig"]]]
        days.setdefault(p["day"], []).append(items)
    out = []
    for o in sorted(days):
        us, vs, fl = [], [], None
        for items in days[o]:
            best = max(items, key=lambda q: _uv(t, q["m"]))
            us.append(_uv(t, best["m"]))
            vs.append(float(best["m"]["v"]))
            if t.get("per_sample"):
                for q in items:
                    n = q["m"].get("n")
                    f = max(floor_of(t), 100.0 / n) if n else floor_of(t)
                    fl = f if fl is None else max(fl, f)
        out.append((o, statistics.median(us), fl, statistics.median(vs) if t.get("per_sample") else None))
    return out


def _probe_day_values(t, recs):
    out = []
    for r in recs or ():
        if r.get("v") is None or r.get("hours", 24) < WINDOW_HOURS_MIN or _clean(dict(r)) is None:
            continue
        fl = None
        if t.get("per_sample"):
            n = r.get("n")
            fl = max(floor_of(t), 100.0 / n) if n else floor_of(t)
        out.append((_ord(r["day"]), _uv(t, r), fl, float(r["v"]) if t.get("per_sample") else None))
    out.sort()
    return out


def take(days, k):
    """Thinning: from the last day back, a day is taken when it is at least k days before the last taken one."""
    out = []
    for d in reversed(days):
        if not out or d[0] <= out[-1][0] - k:
            out.append(d)
    return out[::-1]


def _k(df, m):
    return T_TABLE[min(max(df, 3), 29)][min(max(m, 1), 5) - 1]


def dev_core(t, days, *, upper, lower, rule, m=1, k=1, base_max=None, since=None):
    """NS-D25 for one series of daily values [(day, u, floor[, v])] -> {"n", "dev", "pending", "d", "d_last", "c", "s",
    "gap", "gap1", "usual_raw", "usual_v", "base_raw", "last": [(day, shown), (day, shown)]}: usual_raw, base_raw —
    by u (of loss — by lo, they give the scores); usual_v and last — the values shown (of loss — v, NS-D25). The two compared days — the 2 last taken;
    the base — taken days in [lower, upper) (and >= since), the last ``base_max``."""
    taken = take(days, k)
    out = {"n": 0, "dev": None, "pending": None, "d": None, "d_last": None, "usual_raw": None, "usual_v": None,
           "rel_usual": None, "base_raw": [],
           "last": [], "last_day": days[-1][0] if days else None}
    if len(taken) < 2:
        return out
    last2 = taken[-2:]
    up = min(upper, last2[0][0])                    # the window — not less than the 2 last taken days
    base = [d for d in taken if lower <= d[0] < up and (since is None or d[0] >= since)]
    if base_max:
        base = base[-base_max:]
    if t.get("per_sample"):                         # the common lower bound F of the base and the pair (З-8)
        fl = max([d[2] for d in base + last2 if d[2] is not None] or [floor_of(t)])
        sign = 1 if t["better"] == "higher" else -1
        fix = lambda u: _u(t, max(math.exp(sign * u), fl))
        base = [(d[0], fix(d[1]), d[2], d[3]) for d in base]
        last2 = [(d[0], fix(d[1]), d[2], d[3]) for d in last2]
    sign = 1 if t["better"] == "higher" else -1
    raw = lambda u: math.exp(sign * u)
    shown_v = lambda d: d[3] if t.get("per_sample") and len(d) > 3 and d[3] is not None else raw(d[1])
    out["last"] = [(d[0], shown_v(d)) for d in last2]
    n = len(base)
    out["n"] = n
    if n >= REL_N_MIN:                              # «обычно» of rel (NS-D30): the same median as usual_raw / usual_v
        out["rel_usual"] = statistics.median(shown_v(x) for x in base) if t.get("per_sample") else \
            raw(statistics.median(d[1] for d in base))
    if n < N_BASE_MIN:
        return out
    ub = [d[1] for d in base]
    c, s = statistics.median(ub), statistics.stdev(ub)
    e = math.log(1 + rule["pct"] / 100.0)
    kk = _k(n - 1, m)
    gap = max(e, kk * s * math.sqrt(0.5 + math.pi / (2 * n))) if rule["use_spread"] else e
    gap1 = max(e, kk * s * math.sqrt(1 + math.pi / (2 * n))) if rule["use_spread"] else e
    ds = [d[1] - c for d in last2]
    d = sum(ds) / 2
    dev = "down" if d < -gap and max(ds) < -e else ("up" if d > gap and min(ds) > e else None)
    pend = None
    if dev is None:
        pend = "down" if ds[-1] < -gap1 else ("up" if ds[-1] > gap1 else None)
    out.update(dev=dev, pending=pend, d=d, d_last=ds[-1], c=c, s=s, gap=gap, gap1=gap1, usual_raw=raw(c),
               base_raw=sorted(raw(u) for u in ub), usual_v=statistics.median(shown_v(x) for x in base))
    return out


def _series_dev(t, pts, rule, m, since, k=None):
    """A series of a test from the checks: the window — the days of its 3 last values, not less than the 2 last
    taken days; the base — 60 d before the first day of the window, the last 10."""
    days = _day_values(t, pts)
    if not days:
        return dev_core(t, [], upper=0, lower=0, rule=rule)
    k = DEV_SPACING_DAYS if k is None else k
    first = min(p["day"] for p in pts[-3:])
    taken = take(days, k)
    start = min(first, taken[-2][0]) if len(taken) >= 2 else first   # the first day of the window (NS-D25)
    return dev_core(t, days, upper=first, lower=start - BASE_DAYS, rule=rule, m=m, k=k, base_max=BASE_MAX,
                    since=since)


def _probe_dev(t, recs, rule, m, since, k=None):
    k = DEV_SPACING_DAYS_PROBES if k is None else k
    days = _probe_day_values(t, recs)
    if not days:
        return dev_core(t, [], upper=0, lower=0, rule=rule)
    first = days[-1][0] - 2                         # the 3 fresh days end with the last full day
    win = max(PROBE_BASE_DAYS, 8 * k) if k > 1 else PROBE_BASE_DAYS
    return dev_core(t, days, upper=first, lower=first - win, rule=rule, m=m, k=k, since=since)


# ── the relative score: this node against its own history (NS-D30, NS-D31) ─

def _half_up(x):
    return int(math.floor(x + 0.5))


def rel_of(t, fresh, usual):
    """NS-D30: ``round(100 · fresh / usual)``; for a test where less is better —
    ``round(100 · max(usual, floor) / max(fresh, floor))`` with ``floor`` = ``rel_floor`` of the test (NS-D35: every
    such test of SPEC has one; both below it — 100); within 0…REL_MAX. None — a value missing, not a finite number
    ≥ 0, or a zero denominator (more is better and usual 0)."""
    vals = []
    for x in (fresh, usual):
        if x is None or isinstance(x, bool) or not isinstance(x, (int, float)) or _nonfinite(x) or x < 0:
            return None
        vals.append(float(x))
    if t["better"] == "higher":
        num, den = vals
    else:
        fl = float(t.get("rel_floor") or 0.0)
        num, den = max(vals[1], fl), max(vals[0], fl)
    if den <= 0:
        return None
    r = 100.0 * num / den
    return REL_MAX if r >= REL_MAX else max(0, _half_up(r))


def rel_total(rels):
    """NS-D31: the median of the rel of the links that have one; fewer than REL_TOTAL_MIN such links — None."""
    xs = [r for r in rels if r is not None]
    return _half_up(statistics.median(xs)) if len(xs) >= REL_TOTAL_MIN else None


def _raw(m):
    """The raw value of a fresh value / a value of a check (loss — v, not lo); None — not measured."""
    v = m.get("v") if isinstance(m, dict) else m
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or _nonfinite(v):
        return None
    return float(v)


def link_tests(spec, b, engines=ALL_ENGINES, flags=("aes",), tests_by_id=None):
    """-> (tests, group) of a link: a link of groups — every test of them (group True); crypto — the tests of the
    node's engines and cpu flags; a link of one test — it."""
    if "groups" in b:
        return [t["id"] for g in spec["groups"] if g["id"] in b["groups"] for t in _group_tests(g, tests_by_id)], True
    return [i for i in b["tests"] if _applies(b.get("only_if", {}).get(i), engines, flags)], False


def test_series(t, runs, now, day_of, *, since=None, fresh_mode=None, rule=None, m=1, k=None, cx=None, points=None):
    """dev_core of a test of the checks at ``now`` (NS-D25): the channel — the series of the mode of its fresh value,
    the other mode when that one has fewer than N_BASE_MIN base days and the other has them; other tests — all their
    values. + "mode" (the channel's), "source": "tests". ``points(mode)`` — the points of the test instead of
    ``_points`` (check_rel of many checks, ``rel_cx``: the same series, read once)."""
    rule = rule or DEV_RULE
    tid = t["id"]
    modes = (None,)
    if tid in CHANNEL:
        fm = fresh_mode or "full"
        modes = (fm, "quick" if fm == "full" else "full")
    got = None
    for md in modes:
        pts = points(md) if points is not None else _points(runs, tid, now, day_of, mode=md, since=since, cx=cx)
        r = _series_dev(t, pts, rule, m, since, k) if pts else dev_core(t, [], upper=0, lower=0, rule=rule)
        r["mode"], r["source"] = md, "tests"
        if got is None:
            got = r                                 # «мало истории» — of the series of the fresh value
        if md is not None and r["n"] >= N_BASE_MIN:
            got = r
            break
    return got


def _ctx_of(F):
    """(engines, cpu flags) of the values: of the check, else all engines and the flag aes."""
    e, f = F.get("_engines"), F.get("_cpu_flags")
    return (list(e) if isinstance(e, (list, tuple)) else list(ALL_ENGINES),
            list(f) if isinstance(f, (list, tuple)) else ["aes"])


def link_rel(spec, b, F, series, *, tests_by_id=None):
    """The relative score of the link ``b`` -> (rel, parts, group). ``F`` — the values {test: value} (fresh ones, or
    those of one check); ``series(test)`` — its dev_core (with "rel_usual", "n"). parts — [{"test", "fresh_raw",
    "usual_raw", "usual_n", "unit", "rel"}] of the tests of the link (crypto — of the node's engines); rel of the link:
    one test — its own; a group — the median of its tests' rel; crypto — the least of them (the cipher the node counts
    on). None — no test of the link has rel (NS-D30: usual_n < 3, no value, a zero denominator)."""
    tmap = dict(tests_of(spec), **(tests_by_id or {}))
    engines, flags = _ctx_of(F)
    tids, group = link_tests(spec, b, engines, flags, tests_by_id)
    parts = []
    for tid in tids:
        if tid in USUAL_AS_FRESH:                   # net_cv is the spread of the last checks itself: no «обычно» of
            continue                                # its own, so no rel (its value — stab of the answer)
        t = tmap[tid]
        r = series(tid)
        fv = _raw(F.get(tid))
        u = r.get("rel_usual") if r["n"] >= REL_N_MIN else None
        parts.append({"test": tid, "fresh_raw": fv, "usual_raw": u, "usual_n": r["n"], "unit": t["unit"],
                      "rel": rel_of(t, fv, u)})
    rels = [p["rel"] for p in parts if p["rel"] is not None]
    if not rels:
        rel = None
    elif group:
        rel = _half_up(statistics.median(rels))
    else:
        rel = min(rels)
    return rel, parts, group


def one_part(parts, group):
    """The test of a link whose numbers a line shows: a link of one test — it; crypto — the one its rel is of (else
    the first with a value); a group — none."""
    if group or not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    with_rel = [p for p in parts if p["rel"] is not None]
    if with_rel:
        return min(with_rel, key=lambda p: p["rel"])
    return next((p for p in parts if p["fresh_raw"] is not None), parts[0])


# ── automatic repeat and bench_drop (NS-D33) ───────────────────────────────

def _recent(runs, now):
    """The runs the rel of a link at ``now`` needs (NS-D30): the fresh values (NS-S10: 45 d) and «обычно» — the base of
    BASE_DAYS before the first day of the window of the 3 last values. While those 3 values are fresh, the rel of the
    replay is the rel view() shows at that moment (a cut at 45 d gave a shorter base: another «обычно», or none)."""
    return [r for r in runs if now - (FRESH_DAYS + BASE_DAYS + 1) * DAY <= r["ts"] <= now]


def _test_links_of(spec):
    """The links of the tests (TEST_LINKS, in their order) that ``spec`` has."""
    have = {b["id"]: b for b in spec["links"]}
    return [have[lk] for lk in TEST_LINKS if lk in have]


def _rels_at(spec, runs, now, day_of, epochs, vals=None, cx=None):
    """{link: (rel, parts, group)} of the links of the tests at ``now``: the values ``vals`` (one check), else the fresh
    ones (the median of the 3 last, NS-S10), against «обычно» of the same moment (the base before the 3 last values)."""
    since = epoch_since(spec, epochs, day_of, now)
    tmap = tests_of(spec)
    F = vals if vals is not None else fresh(spec, runs, now, day_of=day_of, epochs=epochs, _cx=cx)["vals"]
    memo = {}

    def series(tid):
        if tid not in memo:
            m = F.get(tid)
            memo[tid] = test_series(tmap[tid], runs, now, day_of, since=since.get(tid),
                                    fresh_mode=m.get("mode") if isinstance(m, dict) else None, cx=cx)
        return memo[tid]
    return dict((b["id"], link_rel(spec, b, F, series)) for b in _test_links_of(spec))


def drop_events(spec, runs, *, day_of=None, epochs=(), until=None, _pre=None):
    """NS-D33: replays the good checks in order; a link of the tests is «ниже» when its fresh rel (the median of the 3
    last values against «обычно» before them) is ≤ DROP_REL, «выше» otherwise; going from «выше» to «ниже» is the event
    bench_drop. The first rel of a link (none without history: usual_n < 3) — no event. An original and its repeat
    count a drop once (the state is already «ниже»). -> (events, state): events [{"link", "run", "ts", "day", "cond":
    "rel", "matched": ["rel"], "push", "auto", "now" (rel), "test", "value", "usual", "unit"}] (test/value/usual — the
    raw numbers of a link of one test; crypto and the groups — None); push — at most once in 7 d per link. state — {link: True when
    «выше»}. ``_pre`` — {"ts": t} (B11): gets ``"state"`` — the state before the first good check at or after ``t``."""
    day_of = _memo(day_of)
    links = _test_links_of(spec)
    if not links:
        return [], {}
    good = sorted([r for r in runs if good_run(r) and (until is None or r["ts"] <= until)],
                  key=lambda r: (r["ts"], str(r.get("id"))))
    by_id = {r.get("id"): r for r in runs}
    cx = {"by_id": by_id, "use": {}}
    state, pushed, events = {}, {}, []
    for i, r in enumerate(good):
        if _pre is not None and "state" not in _pre and r["ts"] >= _pre["ts"]:
            _pre["state"] = dict(state)
        rels = _rels_at(spec, _recent(good[:i + 1], r["ts"]), r["ts"], day_of, epochs, cx=cx)
        orig = by_id.get(r.get("retry_of")) if r.get("by") == "auto" else None
        day = day_of((orig or r)["ts"])
        for b in links:
            link = b["id"]
            rel, parts, group = rels[link]
            if rel is None:
                continue
            above = rel > DROP_REL
            was = state.get(link)
            state[link] = above
            if was is None or was == above or above:
                continue                            # the first rel, no change, or back «выше»
            push = r["ts"] - pushed.get(link, -10 ** 12) >= PUSH_DAYS * DAY
            if push:
                pushed[link] = r["ts"]
            p = None if link == "crypto" else one_part(parts, group)
            events.append({"link": link, "run": r.get("id"), "ts": r["ts"], "day": day, "cond": DROP_COND,
                           "matched": [DROP_COND], "push": push, "auto": orig is not None, "now": rel,
                           "test": p["test"] if p else None, "value": p["fresh_raw"] if p else None,
                           "usual": p["usual_raw"] if p else None, "unit": p["unit"] if p else None})
    if _pre is not None and "state" not in _pre:
        _pre["state"] = dict(state)
    return events, state


def retry_wanted(spec, runs, run, *, day_of=None, epochs=(), _state=None):
    """NS-D33 (§2.8 п. 1): the links of the tests whose rel by this check's own values (against «обычно» at the check)
    is ≤ DROP_REL, while ``drop_events`` still has them «выше» (or does not know them). No «обычно» (usual_n < 3) — no
    repeat: without history there is no sharp drop, only a failed test. Not after a repeat. (The limits of a day, the
    schedule window and the switch — the runner, B11.) ``_state`` — the state of ``drop_events`` before this check,
    already worked out (``drop_events(..., _pre=…)``): no second replay."""
    day_of = _memo(day_of)
    if run.get("by") == "auto" or not good_run(run):
        return []
    hist = [r for r in runs if r["ts"] < run["ts"] and good_run(r)]
    state = drop_events(spec, hist, day_of=day_of, epochs=epochs)[1] if _state is None else _state
    rels = _rels_at(spec, _recent(hist + [run], run["ts"]), run["ts"], day_of, epochs, vals=run_vals(run))
    return [lk for lk in TEST_LINKS if lk in rels and rels[lk][0] is not None and rels[lk][0] <= DROP_REL
            and state.get(lk, True)]


def check_rel(spec, runs, run, *, day_of=None, epochs=(), _cx=None):
    """The relative score of one check (NS-D33, the comparison of two checks): its own values together with its
    repeat (check_vals) against «обычно» at the check (the base before the window of the 3 last values up to it).
    -> {"rel_total", "links": {link: rel | None}, "tests": {test: {"usual", "usual_n", "rel"}}}; ``tests`` — the
    tests of the links (``link_rel`` parts: «обычно» of rel, its base days, rel of the value); a failed check — all
    None. ``_cx`` — ``rel_cx(runs)`` for many checks of the same ``runs`` (the same answer, the series read once)."""
    day_of = _cx["day_of"] if _cx is not None else _memo(day_of)
    upto = [r for r in runs if r["ts"] <= run["ts"]] if _cx is None else None
    vals = check_vals(spec, runs, run, _ck=_cx["ck"] if _cx is not None else None) if good_run(run) else {}
    since = epoch_since(spec, epochs, day_of, run["ts"])
    tmap = tests_of(spec)
    memo = {}

    def series(tid):
        if tid not in memo:
            m = vals.get(tid)
            pts = None if _cx is None else functools.partial(_rel_points, _cx, tid, run["ts"], since.get(tid))
            memo[tid] = test_series(tmap[tid], upto, run["ts"], day_of, since=since.get(tid),
                                    fresh_mode=m.get("mode") if isinstance(m, dict) else None, points=pts)
        return memo[tid]
    rels, tests = {}, {}
    for b in spec["links"]:
        rels[b["id"]], parts, _group = link_rel(spec, b, vals, series)
        for p in parts:                             # a test of two links (net_down: net_bw, down) — the same numbers
            tests.setdefault(p["test"], {"usual": p["usual_raw"], "usual_n": p["usual_n"], "rel": p["rel"]})
    return {"rel_total": rel_total(rels.values()), "links": rels, "tests": tests}


def rel_cx(runs, day_of=None):
    """What ``check_rel`` of many checks of the same ``runs`` shares (``_cx``): the day of a moment, the repeats, the
    usable values and the points of every test (all modes) — read once, not once per check (the list of the checks
    with rel, ``bench.history._rels``)."""
    by_id = {r.get("id"): r for r in runs}
    return {"day_of": _memo(day_of), "runs": runs, "ck": _checks(runs), "by_id": by_id, "use": {}, "pts": {}}


def _rel_points(cx, tid, now, since, mode):
    """``_points([r of runs with ts <= now], tid, now, mode=mode, since=since)`` out of the points of every run read
    once — only the points ``_series_dev`` looks at: with DEV_SPACING_DAYS 1 its base is the last BASE_MAX days in
    [start − BASE_DAYS, start), start — the first day of the window (the days of the 3 last values, not later than
    the day before the last one); an older day changes nothing of it (the days of a check are whole: a repeat goes to
    the day of its original), so the scan from the newest stops where no older point has a later day (``top`` — the
    latest day of the points up to each: a repeat may go to the day of an original later than itself). A repeat whose
    original is later than ``now`` is not a repeat at ``now`` (its original is not in the runs up to it) — the day of
    its own. The points are shared by the checks (read only)."""
    got = cx["pts"].get((tid, mode))
    if got is None:
        pts = _points(cx["runs"], tid, float("inf"), cx["day_of"], mode=mode, cx=cx)
        tdays = [_ord(cx["day_of"](p["ts"])) for p in pts]          # the day of its own moment
        top, m = [], None
        for p, d in zip(pts, tdays):
            m = max(d, p["day"]) if m is None else max(m, d, p["day"])
            top.append(m)
        got = cx["pts"][(tid, mode)] = (pts, [p["ts"] for p in pts], tdays, top,
                                        [cx["by_id"][p["orig"]]["ts"] if p["auto"] else None for p in pts])
    pts, tss, tdays, top, orig_ts = got
    rev, days, first, bound = [], set(), None, None
    for i in range(bisect.bisect_right(tss, now) - 1, -1, -1):
        if bound is not None and top[i] < bound:
            break                                    # every older point is of a day before the bound
        p = pts[i]
        if p["auto"] and orig_ts[i] > now:
            p = dict(p, auto=False, orig=None, day=tdays[i])
        if since is not None and p["day"] < since:
            continue
        rev.append(p)
        if DEV_SPACING_DAYS != 1:
            continue                                 # another thinning: every point (no bound)
        if len(rev) == 3:
            first = min(q["day"] for q in rev)       # the days of the 3 last values
        elif p["day"] in days:
            continue                                 # no new day: the bound stays
        days.add(p["day"])
        if first is None or len(days) < 2:
            continue
        start = min(first, sorted(days)[-2])         # the window is not shorter than the 2 last days
        low = sorted(d for d in days if d < start)
        bound = max(start - BASE_DAYS, low[-BASE_MAX]) if len(low) >= BASE_MAX else start - BASE_DAYS
    if bound is None:
        return rev[::-1]
    origs = set(p["orig"] for p in rev if p["auto"] and p["day"] >= bound)   # a repeat of a repeat: its original too
    return [p for p in reversed(rev) if p["day"] >= bound or p["run"] in origs]


# ── the channel under load (NS-S16) ────────────────────────────────────────

def quiet_level(spec, runs, tid, mode, ts, *, day_of=None, epochs=(), rule=None):
    """L — the quiet level of the series of this mode before a measurement at ``ts``: the larger of usual_raw (the base
    median of NS-D25, >= 4 days before the window) and fresh_quiet (the pick of the 3 last quiet values not older than
    30 d for full, 45 d for quick). None — neither."""
    day_of = _memo(day_of)
    t = tests_of(spec)[tid]
    since = epoch_since(spec, epochs, day_of, ts).get(tid)
    pts = _points(runs, tid, ts, day_of, mode=mode, since=since, before=ts)
    lim = (FRESH_FULL_DAYS if mode == "full" else FRESH_DAYS) * DAY
    q = [p["m"] for p in pts if p["quiet"] and ts - p["ts"] <= lim][-3:]
    fq = pick(t, q)
    usual = _series_dev(t, pts, rule or DEV_RULE, 1, since)["usual_raw"] if pts else None
    got = [x for x in (usual, fq["v"] if fq else None) if x is not None]
    return max(got) if got else None


def load_correct(value, people, level, port=None):
    """value_corr = min(value + people, max(value, L)), not above the port of the tariff; None — «под нагрузкой, не
    годен» (people > value, or no L)."""
    if value is None or people is None or _nonfinite(value) or _nonfinite(people) or _nonfinite(level):
        return None
    if people > value or level is None:
        return None
    v = min(value + people, max(value, level))
    if port:
        v = min(v, float(port))
    return v


# ── the view: /api/host/score (SS §4) and the links of a day ───────────────

def _part(hhmm):
    h = int(hhmm[:2])
    for a, b, name in PARTS:
        if a <= h < b:
            return name
    return "night"


def checked(times):
    """score.checked from the HH:MM of the checks of fresh_from.tests (oldest first)."""
    if not times:
        return None
    parts = {_part(x) for x in times}
    return {"part": parts.pop() if len(parts) == 1 else "mixed", "hhmm": times[-1]}


def view(spec, runs, now, *, day_of=None, hhmm_of=None, probes=None, probe_days=None, epochs=(), rule=None,
         retry=None, events=None, tests_by_id=None, k=None, k_probes=None, steal_7d=None):
    """{"summary", "dev_rule", "links"} of SS §4 at ``now`` (the end of a day for /api/host/day/<date>).
    retry — {"ts", "links"} while an automatic repeat waits (score.retry); events — drop_events() (computed when None).
    Everything after ``now`` (runs, epochs, events, probe days) is left out, except a repeat after midnight of a check
    not later than ``now`` and its events: they belong to the day of the original (SS §2.8 п. 6, the page of that day).
    A link (``links[]``): the raw numbers of its test (``test``, ``fresh_raw``, ``usual_raw``, ``unit``; a group —
    none, its tests are in ``parts``), ``rel`` (NS-D30: 100 — as usual; None — «сравнивать пока не с чем: usual_n из
    3»), ``p25_raw``/``p75_raw`` (the spread of «обычно», from 8 base days), ▲/▼ (NS-D25, unchanged). ``summary`` —
    ``rel_total`` (NS-D31) and the times of the checks. No absolute score, no band, no weak link (NS-D34).
    Besides the contract: "dev_args" — {link: {"link", "test", "now", "usual", "unit"}} of the links with ▲/▼ for
    day_devs / day_color (now/usual None — the hint without numbers, NS-D25: loss whose v of the two days does not agree
    with the mark by lo); "pend_args" — the same of «ждёт подтверждения» of kind ``day`` (now — the last day; of loss
    decided by lo, the numbers by v — none when they disagree, REV-ns-b4-3 П-1); "days" of both — the shown values of
    the two days (one of «ждёт») [["YYYY-MM-DD", v]]; "kind_args" — the same of kinds retry_wait (now — the value of the
    latest check), retry_confirmed and drop (now — the value of the event; crypto — None), REV-ns-b6 З-1.
    ``steal_7d`` — steal p95 over the 7 d before ``now`` (hidden steal, NS C6); "stab" — the fresh values of the
    stability tests {test: {"v" (rounded), "n", "why"}} (stab_view); "vals" — the fresh values F themselves."""
    day_of = _memo(day_of)
    hhmm_of = hhmm_of or _default_hhmm
    rule = valid_dev_rule(rule) if rule is not None else dict(DEV_RULE)
    today = day_of(now)
    all_by_id = {r.get("id"): r for r in runs}
    late = set()                                    # repeats after midnight of a check not later than now (§2.8 п. 6)
    for r in runs:
        if r["ts"] > now and is_repeat(r, all_by_id):
            o = all_by_id[r["retry_of"]]
            if o["ts"] <= now and day_of(r["ts"]) > day_of(o["ts"]):
                late.add(r.get("id"))
    real_ts = {r.get("id"): r["ts"] for r in runs if r.get("id") in late}
    # a late repeat is seen by every computation of the page as a check at ``now`` (REV-ns-b4-3 З-1: fresh, the value
    # of the day, ▲/▼, stale — _points cuts what is after now); its own time stays for the hour of «checked»
    runs = [r if r["ts"] <= now else dict(r, ts=now) for r in runs
            if r["ts"] <= now or (r.get("id") in late and r.get("id") is not None)]
    epochs = [ev for ev in epochs or () if ev["ts"] <= now]
    if probes is not None:
        probes = [w for w in probes if w.get("day") is None or w["day"] <= today]
    if probe_days is not None:
        probe_days = {tid: [x for x in recs or () if x.get("day") is None or x["day"] <= today]
                      for tid, recs in probe_days.items()}
    tmap = dict(tests_of(spec), **(tests_by_id or {}))
    since = epoch_since(spec, epochs, day_of, now)
    if events is None:                              # runs are cut above
        events = drop_events(spec, runs, day_of=day_of, epochs=epochs)[0] if _test_links_of(spec) else []
    else:
        events = [ev for ev in events if ev.get("ts") is None or ev["ts"] <= now or
                  (ev.get("run") in late and ev.get("run") is not None)]
    fr = fresh(spec, runs, now, day_of=day_of, probes=probes, epochs=epochs, tests_by_id=tests_by_id,
               steal_7d=steal_7d)
    F, meta = fr["vals"], fr["meta"]
    if probe_days is not None and isinstance(F.get("steal_p95"), dict) and F["steal_p95"].get("why") in STAB_WHY:
        probe_days = dict(probe_days, steal_p95=[])     # a steal not seen (container, hidden) has no «обычно» either
    last = last_good(runs, now)
    ck = _checks(runs)
    test_src = [tid for tid in tmap if not (probes is not None and tid in PROBE_TESTS)]
    stale = last is not None and not any(
        p for tid in test_src for p in _points(runs, tid, now, day_of) if now - p["ts"] <= FRESH_DAYS * DAY)
    goods = [r for r in runs if good_run(r)]
    first = bool(goods) and now - min(r["ts"] for r in goods) < FIRST_S

    # ▲/▼ of every test that may need it
    devs = {}

    def test_dev(tid, m):
        key = (tid, m)
        if key in devs:
            return devs[key]
        t = tmap[tid]
        if probes is not None and tid in PROBE_TESTS:
            r = _probe_dev(t, (probe_days or {}).get(tid), rule, m, since.get(tid), k_probes)
            r["mode"], r["source"] = None, "probes"
        else:
            fm = F.get(tid)
            r = test_series(t, runs, now, day_of, since=since.get(tid), rule=rule, m=m, k=k,
                            fresh_mode=fm.get("mode") if isinstance(fm, dict) else None)
        devs[key] = r
        return r

    drop_days = {}                                  # {link: {day: True when a repeat gave the event}}
    drop_ev = {}                                    # {link: {day: the event of the hint}} — the repeat's, else the last
    for ev in events or ():
        dd = drop_days.setdefault(ev["link"], {})
        dd[ev["day"]] = dd.get(ev["day"], False) or bool(ev.get("auto"))
        de = drop_ev.setdefault(ev["link"], {})
        if ev.get("auto") or not (de.get(ev["day"]) or {}).get("auto"):
            de[ev["day"]] = ev
    dev_args, pend_args, kind_args = {}, {}, {}
    retry_links = set((retry or {}).get("links") or ())
    links = []
    by_time = sorted(runs, key=lambda r: (r["ts"], str(r.get("id"))), reverse=True)
    own = {}
    for b in spec["links"]:
        lid = b["id"]
        src = "probes" if lid in PROBE_LINKS else "tests"
        rel, parts, group = link_rel(spec, b, F, lambda tid: test_dev(tid, 1), tests_by_id=tests_by_id)
        if stale and src == "tests":                # the probes run all the time: their values stay
            rel = None
            parts = [dict(p, fresh_raw=None, rel=None) for p in parts]
        one = one_part(parts, group)
        tids = [p["test"] for p in parts] if group else ([one["test"]] if one else [])   # ▲/▼ — as before (NS-D32)
        row = {"id": lid, "source": src, "rel": rel, "test": one["test"] if one else None,
               "fresh_raw": one["fresh_raw"] if one else None, "fresh_n": None, "unit": one["unit"] if one else None,
               "usual_raw": one["usual_raw"] if one else None, "usual_n": 0, "usual_mode": None,
               "p25_raw": None, "p75_raw": None, "now_id": None, "now_day": None,
               "dev": None, "dev_test": None, "dev_pending": None, "dev_pending_kind": None,
               "parts": parts}
        fn = [meta[i]["fresh_n"] for i in tids if i in meta]
        row["fresh_n"] = min(fn) if fn else 0
        # the last check (with its repeat) that gave a value of a test of the link, not older than 30 d (NS §6.2)
        for r in by_time:
            if now - r["ts"] > NOW_DAYS * DAY:
                break
            if good_run(r) and any(usable(r, i) is not None for i in tids):
                key = r.get("id") if r.get("id") is not None else id(r)
                if key not in own:
                    got_src = {}
                    own[key] = (check_vals(spec, runs, r, tests_by_id=tests_by_id, _ck=ck, src=got_src), got_src)
                vals, got_src = own[key]
                got = [got_src[i] for i in tids if i in got_src and _measured(vals.get(i))]
                if got:
                    orig = ck["by_id"][r["retry_of"]] if is_repeat(r, ck["by_id"]) else r
                    rep_ = [g for g in got if g is not orig]  # a value of the link from the repeat — its id
                    row["now_id"], row["now_day"] = (rep_[-1] if rep_ else orig).get("id"), day_of(orig["ts"])
                    break
        # ▲/▼, usual
        cand = [i for i in tids if test_dev(i, 1)["n"] >= N_BASE_MIN]
        m = max(1, len(cand)) if group else 1
        rs = {i: test_dev(i, m) for i in tids}
        based = [i for i in cand]
        if not group and tids:
            row["usual_mode"] = rs[tids[0]]["mode"] if row["usual_raw"] is not None else None
        if based:
            if group:
                row["usual_n"] = min(rs[i]["n"] for i in based)
            else:
                r = rs[based[0]]
                row["usual_n"] = r["n"]
                if not tmap[based[0]].get("per_sample") and len(r["base_raw"]) >= SPREAD_MIN:
                    q = statistics.quantiles(r["base_raw"], n=4, method="inclusive")   # the spread of «обычно»
                    row["p25_raw"], row["p75_raw"] = min(q[0], q[2]), max(q[0], q[2])
            marks = [(i, rs[i]) for i in based if rs[i]["dev"]]
            down = [x for x in marks if x[1]["dev"] == "down"]
            pick_m = down or marks
            if pick_m:
                row["dev"] = pick_m[0][1]["dev"]
                row["dev_test"] = max(pick_m, key=lambda x: abs(x[1]["d"]))[0]
                rd, td = rs[row["dev_test"]], tmap[row["dev_test"]]
                a = {"link": lid, "test": row["dev_test"], "now": rd["last"][-1][1] if rd["last"] else None,
                     "usual": rd["usual_v"] if td.get("per_sample") else rd["usual_raw"], "unit": td["unit"],
                     "days": _hint_days(rd["last"])}  # the two days of «520 и 540 Мбит/с» (REV-ns-b6 З-1)
                if td.get("per_sample") and not _shown_agrees(td, rd["dev"], rd["last"], rd["usual_v"]):
                    a["now"] = a["usual"] = a["days"] = None    # loss: v of the two days against the mark by lo
                dev_args[lid] = a
            else:
                pend = [(i, rs[i]) for i in based if rs[i]["pending"]]
                pdown = [x for x in pend if x[1]["pending"] == "down"]
                if pdown or pend:
                    pi, rp = (pdown or pend)[0]
                    row["dev_pending"] = rp["pending"]
                    row["dev_pending_kind"] = "day"
                    tp = tmap[pi]
                    a = {"link": lid, "test": pi, "now": rp["last"][-1][1] if rp["last"] else None,
                         "usual": rp["usual_v"] if tp.get("per_sample") else rp["usual_raw"], "unit": tp["unit"],
                         "days": _hint_days(rp["last"][-1:])}
                    if tp.get("per_sample") and not _shown_agrees(tp, rp["pending"], rp["last"][-1:], rp["usual_v"],
                                                                  need=1):
                        a["now"] = a["usual"] = a["days"] = None    # loss: decided by lo, the hint by v (NS-D25, П-1)
                    pend_args[lid] = a
        else:
            row["usual_n"] = max([rs[i]["n"] for i in tids] or [0])
        if row["dev"] is None:
            last_day = max([rs[i]["last_day"] for i in tids if rs[i]["last_day"] is not None] or [None]) \
                if tids and any(rs[i]["last_day"] is not None for i in tids) else None
            one_t = tids[0] if tids and not group and lid != "crypto" else None   # as drop_events: crypto — no number
            if lid in retry_links:
                row["dev_pending"], row["dev_pending_kind"] = "down", "retry_wait"
                val = next((mv["v"] for mv in (usable(r, one_t) for r in by_time) if mv is not None), None) \
                    if one_t else None
                kind_args[lid] = {"link": lid, "test": one_t, "now": val, "usual": row["usual_raw"],
                                  "unit": tmap[one_t]["unit"] if one_t else None, "days": None}   # «… в последней проверке: 5»
            elif last_day is not None and _iso(last_day) in drop_days.get(lid, {}):
                by_repeat = drop_days[lid][_iso(last_day)]
                row["dev_pending"], row["dev_pending_kind"] = "down", "retry_confirmed" if by_repeat else "drop"
                ev = drop_ev[lid][_iso(last_day)]   # «… подтвердила падение: 5 Мбит/с» — the value of the event (З-1)
                kind_args[lid] = {"link": lid, "test": one_t, "now": ev.get("value"), "usual": row["usual_raw"],
                                  "unit": ev.get("unit"), "days": None}
            elif last_day is not None and _refuted(runs, tids, last_day, day_of):
                row["dev_pending"], row["dev_pending_kind"] = None, None
        links.append(row)

    # «Последняя проверка: отдача 5 Мбит/с. Проверю ещё раз около 15:40» (SS §2.8 п. 7; 6C): the first link of the repeat
    # with the value of the latest check — its numbers, the words are the screen's
    ra = next((kind_args[x] for x in (retry.get("links") or ()) if x in kind_args
               and kind_args[x].get("now") is not None), None) if retry else None
    rels = [row["rel"] for row in links]
    summary = {
        # NS-D31: the median of the links' rel — 100 is «как обычно у этого сервера»; none — fewer than 3 such links
        "rel_total": rel_total(rels), "rel_n": len([x for x in rels if x is not None]), "rel_need": REL_N_MIN,
        "last_id": last.get("id") if last else None,
        # the time of the last good check: «давно не проверялся · последняя проверка 8 октября» (REV-ns-p4b З-1)
        "last_at": int(last["ts"]) if last else None,
        "fresh_from": fr["from"], "first": first, "stale": stale,
        "checked": checked([hhmm_of(real_ts.get(i, next(r["ts"] for r in runs if r.get("id") == i)))
                            for i in fr["from"]["tests"]]),
        "retry": {"at": hhmm_of(retry["ts"]), "links": list(retry.get("links") or ()),
                  "link": ra["link"] if ra else None, "test": ra["test"] if ra else None,
                  "value": round(float(ra["now"]), 3) if ra else None, "unit": ra["unit"] if ra else None}
        if retry else None,
        "base_since": base_since(epochs, day_of, now),
    }
    return {"summary": summary, "dev_rule": rule, "links": links, "dev_args": dev_args, "pend_args": pend_args,
            "kind_args": kind_args, "stab": stab_view(spec, F, tests_by_id), "vals": F}


def stab_view(spec, F, tests_by_id=None):
    """The fresh values of the tests of the group «Стабильность» (NS C6): {test: {"v" (rounded), "n", "why"}} — why: a
    code of STAB_WHY, "not_measured" or None."""
    out = {}
    for g in spec["groups"]:
        if g["id"] != "stab":
            continue
        for t in _group_tests(g, tests_by_id):
            m = F.get(t["id"])
            m = m if isinstance(m, dict) else ({"v": m} if m is not None else {})
            v = m.get("v")
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and not _nonfinite(v)
            out[t["id"]] = {"v": round(float(v), 3) if ok else None, "n": m.get("n") if ok else None,
                            "why": None if ok else (m.get("why") or "not_measured")}
    return out


def _hint_days(last):
    """[(day ordinal, shown value)] of dev_core -> [["YYYY-MM-DD", value]] of the hint (REV-ns-b6 З-1)."""
    return [[_iso(d) if isinstance(d, int) else d, v] for d, v in last or ()]


def _shown_agrees(t, dev, last, usual, need=2):
    """NS-D25: the shown values of the two days (of loss — v) are on the side of the mark ▲/▼ from «обычно»; ``need``
    — 1 for «ждёт подтверждения» (one last day)."""
    if dev not in ("up", "down") or usual is None or len(last) < need:
        return False
    worse = (lambda x: x < usual) if t["better"] == "higher" else (lambda x: x > usual)
    better = (lambda x: x > usual) if t["better"] == "higher" else (lambda x: x < usual)
    side = worse if dev == "down" else better
    return all(x is not None and side(x) for _d, x in last)


def _refuted(runs, tids, day, day_of):
    """In the day ``day`` (ordinal) an automatic repeat of a test of the link that confirmed nothing (no bench_drop of
    the link that day is checked by the caller first)."""
    by_id = {r.get("id"): r for r in runs}
    for r in runs:
        if r.get("by") != "auto" or r.get("retry_of") not in by_id or not good_run(r):
            continue
        if _ord(day_of(by_id[r["retry_of"]]["ts"])) != day:
            continue
        if any(usable(r, i) is not None for i in tids):
            return True
    return False


# ── the colour of a day (NS C6) ────────────────────────────────────────────

def offline_minutes(spans, start, end, now=None):
    """Minutes of [node_offline, node_online) intersected with the day [start, end); an open span lasts to the end of
    the day or to ``now``. spans — [(off_ts, on_ts | None)]. The sum over the day, not «in a row»."""
    total_s = 0.0
    for a, b in spans or ():
        if b is None:
            b = end if now is None else min(end, now)
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            total_s += hi - lo
    return total_s / 60.0


def tested_links(spec, runs, day, day_of=None, tests_by_id=None):
    """The links whose tests got a value from a good check of the day ``YYYY-MM-DD`` (a repeat — by the day of its
    original): a ▼ of such a link colours the day (NS C6, круг 3, придирка 12)."""
    day_of = day_of or _default_day
    by_id = {r.get("id"): r for r in runs}
    got = set()
    for r in runs:
        orig = by_id.get(r.get("retry_of")) if r.get("by") == "auto" else None
        if day_of((orig or r)["ts"]) != day:
            continue
        got.update(t for t in (r.get("vals") or {}) if not t.startswith("_") and usable(r, t) is not None)
    out = set()
    for b in spec["links"]:
        ts = link_tests(spec, b, tests_by_id=tests_by_id)[0] if "groups" in b else b["tests"]
        if got & set(ts):
            out.add(b["id"])
    return out


def day_devs(view_doc, tested):
    """devs of a day for day_color: the dev of every link at the end of the day; a link of the checks counts only when
    a good check of that day gave a value of a test of the link (tested — the set of such link ids). view_doc — the
    result of view() (or its "links"). -> [{"dev", "link", "test", "now", "usual", "unit"}] (test — the test that gave
    the mark; now/usual — its raw values, in its unit; of loss — v; None — the hint without numbers, NS-D25)."""
    if isinstance(view_doc, dict):
        links, args = view_doc["links"], view_doc.get("dev_args") or {}
    else:
        links, args = view_doc, {}
    out = []
    for lk in links:
        if lk.get("dev") and (lk["id"] in PROBE_LINKS or lk["id"] in tested):
            a = args.get(lk["id"]) or {}
            out.append({"dev": lk["dev"], "link": lk["id"], "test": a.get("test", lk.get("dev_test")),
                        "now": a.get("now"), "usual": a.get("usual"), "unit": a.get("unit")})
    return out


def day_color(d, devs, drops):
    """(color, partial, reason) of a day. d = {"hours", "test", "offline_min", "lost", "n", "alarm_min", "warn_min"};
    devs — day_devs() (or bare "down"/"up"); drops — bench_drop events of the day [{"link", "test", "value", "usual",
    "unit", "now" (rel)}] (NS-D33: «Сервер заметно хуже, чем обычно»)."""
    hours = d.get("hours") or 0
    partial = 0 < hours < 12
    if hours == 0 and not d.get("test"):
        return "grey", partial, {"code": "no_data", "args": {}}
    off = d.get("offline_min") or 0
    if off >= 5:
        return "red", partial, {"code": "offline", "args": {"min": int(round(off))}}
    n = d.get("n") or 0
    if n > 0:
        pct = 100.0 * (d.get("lost") or 0) / n
        if pct >= 2:
            return "red", partial, {"code": "loss", "args": {"pct": round(pct, 1), "thr": 2}}
    if (d.get("alarm_min") or 0) >= 5:
        return "red", partial, {"code": "alarm", "args": {"min": d["alarm_min"]}}
    if (d.get("warn_min") or 0) >= 15:
        return "yellow", partial, {"code": "warn", "args": {"min": d["warn_min"]}}
    if drops:
        ev = drops[0]
        return "yellow", partial, {"code": "drop", "args": {"link": ev.get("link"), "test": ev.get("test"),
                                                             "now": ev.get("value"), "usual": ev.get("usual"),
                                                             "unit": ev.get("unit"), "rel": ev.get("now")}}
    for dv in devs or ():
        if (dv.get("dev") if isinstance(dv, dict) else dv) == "down":
            args = {k: dv.get(k) for k in ("link", "test", "now", "usual", "unit")} if isinstance(dv, dict) else {}
            return "yellow", partial, {"code": "link_down", "args": args}
    return "green", partial, None
