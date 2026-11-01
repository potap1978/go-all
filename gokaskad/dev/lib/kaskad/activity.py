"""The activity of devices — the line «кардиограмма» of «Люди» (k3/handoff/SPEC-activity.md ACT-1…ACT-14, AC1).

A portable block (k3/handoff/process/BLOCKS.md): pure functions over a plain dict, no ``kaskad.gw`` — the binding
(``gw/collect.py``) passes the sessions of a round and writes the windows where it keeps them (``gw/metrics.py``).
Kept only (ACT-6): the id of a device, the time of a window, the seconds «на связи», the seconds «активен», the bytes
rx and tx — no address, login, name, destination or time of a single connection.

The accumulator (JSON as it is — the binding keeps it in /run between restarts of its process, ACT-5)::

    {"v": 1, "bucket": <ts of the 5-min window>, "at": <ts of the last round>,
     "prev": {"<dev>|<kind>|<ip or ->": [rx, tx, seen_ts]},       # the last counters of a session
     "cur": {"<dev>": [on_s, act_s, rx, tx]},                      # the window not closed yet
     "rounds": <rounds of the window>, "secs": <seconds of the window the rounds saw>,
     "wait": [<window>, …]}                                        # closed, not written yet (≤ WAIT_MAX)

A window — ``{"ts", "rounds", "secs", "rows": {dev: [on_s, act_s, rx, tx]}}`` (rows of devices with anything only).

The rules (ACT-4 — the trap is a false peak after a restart):
* the deltas go by session (``dev|kind|ip``): the sum of a device would drop when one of its sessions goes (a peer of
  WireGuard silent for 3 min) and «smaller -> take it as it is» would count the whole counter again;
* a session met for the first time without a saved past counts 0, not its whole counter; a counter that went down
  (xray restarted, an interface made anew, a reboot) counts what it shows now;
* zeros (rx = tx = 0: what only the hooks know, or a counter not read) are no reading — the past stays;
* a delta above ``MAX_RATE`` over the time since the session was last seen is no traffic (a counter gone wrong): 0;
* «на связи» once a device a round (several sessions — once), «активен» when the bytes of the round reach
  ``act_round_bytes`` per ``ROUND_S`` s; a round counts at most ``gap_cap`` s (a skipped round does not invent time);
* the clock back a little (≤ one window) — the round counts nothing, the window waits; back more — the window open
  is dropped and a new one starts at the new time (its stamp was wrong; the counters stay — no byte counted twice).
"""

import json
import math

V = 1
WINDOW = 300                  # s: the window of the base (dev_m5)
ROUND_S = 10                  # s: a full round of the collector (act_round_bytes is per such round; a shorter round —
                              # the look of a light round on a copy while a panel watches, 3.3.7.11 — takes its share:
                              # the threshold goes by dt)
ACT_ROUND_BYTES = 4096        # B a round: ≈ 3 kbit/s — keepalive and handshakes of WireGuard stay below
GAP_CAP = 30                  # s: a round counts at most this much time
PREV_TTL = 86400              # s: a session not seen this long is forgotten (its next sight — a first one)
MAX_RATE = 1.25e9             # B/s: 10 Gbit/s — above it a delta is a counter gone wrong, not traffic
WAIT_MAX = 12                 # windows closed and not written (the clock not synchronised, the base failing): ≤ 1 h
KEEP_FILE_S = 900             # s: a saved accumulator older than this is not taken (its counters are too far behind)
MAX_PREV = 20000              # sessions remembered at most (garbage or a huge node: the oldest go first)
_KEY_MAX = 200
_N_MAX = 2 ** 53


def new(now=None):
    """An empty accumulator; ``now`` — the window starts there (None: at the first round)."""
    b = None if now is None else int(now) // WINDOW * WINDOW
    return {"v": V, "bucket": b, "at": None, "prev": {}, "cur": {}, "rounds": 0, "secs": 0.0, "wait": []}


def _n(v, lo=0, hi=_N_MAX, kind=int):
    """A number of the accumulator within [lo, hi], else None (bool, text, NaN, ±inf are no numbers)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    v = kind(v)
    return v if lo <= v <= hi else None


def _row(r):
    """[on_s, act_s, rx, tx] of a device, numbers ≥ 0 — else None."""
    if not isinstance(r, list) or len(r) != 4:
        return None
    out = [_n(r[0], kind=float), _n(r[1], kind=float), _n(r[2]), _n(r[3])]
    return None if None in out else out


def _window(w):
    """A closed window as kept, checked; None — garbage."""
    if not isinstance(w, dict):
        return None
    ts, rounds, secs = _n(w.get("ts")), _n(w.get("rounds")), _n(w.get("secs"), kind=float)
    rows = w.get("rows")
    if ts is None or rounds is None or secs is None or not isinstance(rows, dict):
        return None
    out = {}
    for dev, r in rows.items():
        r = _row(r)
        if isinstance(dev, str) and 0 < len(dev) <= _KEY_MAX and r is not None:
            out[dev] = r
    return {"ts": ts // WINDOW * WINDOW, "rounds": rounds, "secs": secs, "rows": out}


def load(doc, now, max_age=KEEP_FILE_S):
    """The accumulator saved by ``dump`` -> a checked copy; anything else (no file, another version, garbage, older
    than ``max_age`` s or from the future) -> ``new()`` (its first sights count 0, ACT-4)."""
    if isinstance(doc, (str, bytes)):
        try:
            doc = json.loads(doc)
        except ValueError:
            doc = None
    if not isinstance(doc, dict) or doc.get("v") != V:
        return new()
    at = _n(doc.get("at"), kind=float)
    if at is None or not -60 <= float(now) - at <= max_age:
        return new()
    acc = new()
    acc["at"] = at
    acc["bucket"] = _n(doc.get("bucket"))
    if acc["bucket"] is not None:
        acc["bucket"] = acc["bucket"] // WINDOW * WINDOW
    acc["rounds"] = _n(doc.get("rounds")) or 0
    acc["secs"] = _n(doc.get("secs"), kind=float) or 0.0
    prev = doc.get("prev") if isinstance(doc.get("prev"), dict) else {}
    for k, p in prev.items():
        if not isinstance(k, str) or not 0 < len(k) <= _KEY_MAX or not isinstance(p, list) or len(p) != 3:
            continue
        rx, tx, seen = _n(p[0]), _n(p[1]), _n(p[2], kind=float)
        if None not in (rx, tx, seen):
            acc["prev"][k] = [rx, tx, seen]
    cur = doc.get("cur") if isinstance(doc.get("cur"), dict) else {}
    for dev, r in cur.items():
        r = _row(r)
        if isinstance(dev, str) and 0 < len(dev) <= _KEY_MAX and r is not None:
            acc["cur"][dev] = r
    for w in doc.get("wait") if isinstance(doc.get("wait"), list) else []:
        w = _window(w)
        if w is not None:
            acc["wait"].append(w)
    acc["wait"] = sorted(acc["wait"], key=lambda w: w["ts"])[-WAIT_MAX:]
    _trim_prev(acc)
    return acc


def dump(acc):
    """The accumulator as JSON text (to /run; ``load`` takes it back)."""
    return json.dumps(acc, sort_keys=True, separators=(",", ":"))


def after_flushed(acc, flushed):
    """The base already holds the windows up to ``flushed`` (the stamp of the last one written): a window saved before
    its write and found again after a restart is not written twice — the open one starts empty, the waiting ones go."""
    f = _n(flushed)
    if f is None:
        return acc
    acc["wait"] = [w for w in acc["wait"] if w["ts"] > f]
    if acc["bucket"] is not None and acc["bucket"] <= f:
        acc["cur"], acc["rounds"], acc["secs"] = {}, 0, 0.0
        acc["bucket"] = None
    return acc


def _delta(cur, last):
    """The rule of deltas (as ``gw.metrics.delta``): what grew, or the counter as it is when it went down."""
    return cur - last if cur >= last else cur


def _int0(v):
    v = _n(v)
    return 0 if v is None else v


def _trim_prev(acc):
    if len(acc["prev"]) > MAX_PREV:
        keep = sorted(acc["prev"].items(), key=lambda kv: kv[1][2])[-MAX_PREV:]
        acc["prev"] = dict(keep)


def close(acc):
    """The open window as a closed one (None — not a round and not a row in it); the open one starts empty."""
    rows = dict((d, [round(r[0], 1), round(r[1], 1), int(r[2]), int(r[3])])
                for d, r in sorted(acc["cur"].items()) if r[0] > 0 or r[1] > 0 or r[2] > 0 or r[3] > 0)
    w = None
    if acc["bucket"] is not None and (acc["rounds"] or rows):
        w = {"ts": acc["bucket"], "rounds": int(acc["rounds"]), "secs": round(acc["secs"], 1), "rows": rows}
    acc["cur"], acc["rounds"], acc["secs"] = {}, 0, 0.0
    return w


OPEN_MAX = 2000               # devices of the open window shown live at most (a huge node: the busiest first)


def open_view(acc, keep=None):
    """The open window as the panel shows it live (3.3.7.10: the line and the bytes of the day move every round, not
    every 5 min): {"w": <start of the window>, "at": <the last round>, "secs": <seconds the rounds saw>, "cur": {dev:
    [on_s, act_s, rx, tx]}} — rows with anything only, of the devices ``keep`` (None — all); None — no window yet. The
    same fields as a window of the base (ACT-6): no address, login, name or destination."""
    if not isinstance(acc, dict) or acc.get("bucket") is None or acc.get("at") is None:
        return None
    rows = [(d, r) for d, r in (acc.get("cur") or {}).items()
            if (keep is None or d in keep) and (r[0] > 0 or r[1] > 0 or r[2] > 0 or r[3] > 0)]
    if len(rows) > OPEN_MAX:
        rows = sorted(rows, key=lambda dr: dr[1][2] + dr[1][3])[-OPEN_MAX:]
    return {"w": int(acc["bucket"]), "at": int(acc["at"]), "secs": int(round(acc.get("secs") or 0)),
            "cur": dict((d, [int(round(r[0])), int(round(r[1])), int(r[2]), int(r[3])]) for d, r in rows)}


def step(acc, sessions, now, act_round_bytes=ACT_ROUND_BYTES, gap_cap=GAP_CAP, prev_ttl=PREV_TTL):
    """One round of the collector (a full one; a light one while a panel watches steps a copy, 3.3.7.11): ``sessions`` —
    [{"device", "kind", "ip", "rx", "tx"}] (``collect.sessions``), ``now`` — the wall clock of the round. -> (acc, the window closed by this round or None). The round that crossed
    the border of a window goes whole into the new one."""
    now = float(now)
    at = acc.get("at")
    out = None
    b = int(now) // WINDOW * WINDOW
    if at is None:
        dt = 0.0
    elif now < at:
        dt = 0.0
        if at - now > WINDOW:                          # the clock back by more than a window: its stamp was wrong
            acc["cur"], acc["rounds"], acc["secs"] = {}, 0, 0.0
            acc["bucket"] = b
            acc["wait"] = [w for w in acc["wait"] if w["ts"] <= b]
    else:
        dt = min(now - at, float(gap_cap))
    if acc["bucket"] is None:
        acc["bucket"] = b
    elif b > acc["bucket"]:
        out = close(acc)
        acc["bucket"] = b
    # b < bucket (the clock back a little): the window waits until the clock passes it again
    seen = {}
    prev = acc["prev"]
    for s in sessions or ():
        if not isinstance(s, dict):
            continue
        dev = s.get("device")
        if not isinstance(dev, str) or not 0 < len(dev) <= 64:
            continue
        key = "%s|%s|%s" % (dev, str(s.get("kind") or "")[:32], str(s.get("ip") or "-")[:64])
        rx, tx = _int0(s.get("rx")), _int0(s.get("tx"))
        got = seen.setdefault(dev, [0, 0])
        p = prev.get(key)
        if rx == 0 and tx == 0:                        # zeros are no reading: the past counter stays
            if p is not None:
                p[2] = now
            continue
        if p is None:                                  # the first sight without a past (ACT-4): 0
            prev[key] = [rx, tx, now]
            continue
        drx, dtx = _delta(rx, p[0]), _delta(tx, p[1])
        cap = MAX_RATE * max(now - p[2], float(ROUND_S))
        if drx + dtx > cap:                            # a counter gone wrong: no traffic, the new one is the base
            drx = dtx = 0
        prev[key] = [rx, tx, now]
        got[0] += drx
        got[1] += dtx
    thr = act_round_bytes * dt / float(ROUND_S)
    for dev, (drx, dtx) in seen.items():
        r = acc["cur"].setdefault(dev, [0.0, 0.0, 0, 0])
        r[0] += dt
        if dt > 0 and drx + dtx >= thr:
            r[1] += dt
        r[2] += drx
        r[3] += dtx
    acc["rounds"] += 1
    acc["secs"] += dt
    acc["at"] = now
    for k in [k for k, p in prev.items() if now - p[2] > prev_ttl]:
        del prev[k]
    _trim_prev(acc)
    return acc, out


def hold(acc, win, cap=WAIT_MAX):
    """A closed window not written (the clock not synchronised, the base failing) waits in the accumulator; above
    ``cap`` the two oldest are merged into the newer of them — the bytes stay, their place in time moves."""
    if win is None:
        return acc
    acc["wait"].append(win)
    acc["wait"].sort(key=lambda w: w["ts"])
    while len(acc["wait"]) > cap:
        a, b = acc["wait"][0], acc["wait"][1]
        for dev, r in a["rows"].items():
            x = b["rows"].setdefault(dev, [0.0, 0.0, 0, 0])
            for i in range(4):
                x[i] += r[i]
        b["rounds"] += a["rounds"]
        b["secs"] += a["secs"]
        del acc["wait"][0]
    return acc


def take(acc):
    """The waiting windows, the oldest first; the accumulator keeps none of them."""
    out, acc["wait"] = acc["wait"], []
    return out


def wipe(acc, at):
    """«Удалить историю активности» (№41) at ``at`` (a stamp; None or garbage — nothing): the windows that began at or
    before it go — the waiting ones, and the open one when it began then (emptied; while the clock is in it, it stays
    empty: it holds time before the wipe, so none of it is written — the history starts again with the next window). The
    counters of the sessions stay (``prev``): the next round counts only what came after it — no false peak. -> acc."""
    a = _n(at)
    if a is None:
        return acc
    acc["wait"] = [w for w in acc["wait"] if w["ts"] > a]
    if acc["bucket"] is not None and acc["bucket"] <= a:
        acc["cur"], acc["rounds"], acc["secs"] = {}, 0, 0.0
    return acc


def forget(acc, keep):
    """Only the devices of ``keep`` (the ids of the state): the deleted ones leave the counters, the open window and
    the waiting ones (their history in the base — the binding)."""
    keep = set(keep or ())
    acc["prev"] = dict((k, p) for k, p in acc["prev"].items() if k.split("|", 1)[0] in keep)
    acc["cur"] = dict((d, r) for d, r in acc["cur"].items() if d in keep)
    for w in acc["wait"]:
        w["rows"] = dict((d, r) for d, r in w["rows"].items() if d in keep)
    return acc


# ── the line in text (CLI, MCP; the panel draws its own — static/spark.js) ──

BLOCKS = "▁▂▃▄▅▆▇█"
NO_DATA = "·"
LO = 20 * 1024                 # B in 30 min: below it — the bottom (keepalive is not «activity», ACT-9)
HI = 500 * 1024 * 1024         # B in 30 min: the top (the scale of the panel, UI-B)


def level(v, step=1800, lo=LO, hi=HI):
    """0–7 of the bytes ``v`` of a point of ``step`` s: a log scale between ``lo`` and ``hi`` (per 30 min, scaled to
    the step) — one scale for every line, so lines compare."""
    k = step / 1800.0
    lo, hi = lo * k, hi * k
    if not v or v < lo:
        return 0
    if v >= hi:
        return len(BLOCKS) - 1
    return max(1, min(len(BLOCKS) - 1, int(math.log(v / lo) / math.log(hi / lo) * (len(BLOCKS) - 1) + 0.5)))


def spark(values, nodata=None, step=1800):
    """The text line of ``values`` (bytes a point); ``nodata[i]`` true — «·» (no round of the collector)."""
    out = []
    for i, v in enumerate(values):
        out.append(NO_DATA if nodata and nodata[i] else BLOCKS[level(v, step)])
    return "".join(out)
