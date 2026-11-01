"""Is the key of an AI agent used: «подключено» in the panel.

The MCP server (kaskad.mcp.server.Audit) writes a line per call and per ``initialize`` with the SSH address and port
of the client (``from``, ``port`` of SSH_CONNECTION). sshd logged which key opened that connection:
``Accepted publickey for root from IP port P ssh2: ED25519 SHA256:…``.

What is kept in STATE (/var/lib/kaskad/mcp/seen.json, survives a restart of the panel and a rotation of the audit):
  pairs  "ip port" -> {"fp": fingerprint | null (not in the logs), "init": the connection's initialize}. Only the
         pairs that are still in the audit tail: a pair that left it is dropped (no stale pair for a reused NAT port);
         a newer ``initialize`` on the same pair is a new connection and is looked up again.
  last   fingerprint -> {"at", "client"}: the latest PROVEN call of each current key (the audit rotates weekly).
  keys   fingerprint -> [[added, gone|null], …]: when each key of an agent was in authorized_keys
         (kaskad.mcp.keys.add/delete note it at once; a key found in the file without a note — from the moment it is
         seen). Closed intervals older than KEEP go and move ``horizon`` forward.
  scan   when the logs may be read again, how many reads failed in a row.

The logs are read ONCE for every pair not looked up yet (journalctl -g "Accepted publickey" since the oldest of them,
at most DEPTH back, and the tail of /var/log/auth.log), at most once in SCAN_EVERY seconds, and only when there is such
a pair. A pair not found is final (a fresh one — FRESH seconds — is tried again: the journal may lag); a pair older
than DEPTH is not looked for at all. A read that failed is retried with a growing pause (FAIL_MIN … FAIL_MAX); so is
one where journalctl failed but auth.log was read: what auth.log named is taken, the rest is not final.

A call whose key was not found («?») goes to a key only by elimination: its connection was opened (its
``initialize`` is in the audit tail) when that key — and no other — was an agent's key (after ``horizon``): a
connection keeps the key it was opened with. An old call, or a call of a long session of a deleted key, never
makes a new key «подключено».

    for_keys(fingerprints, runner) -> {fingerprint: {"at": epoch, "client": "claude-code/1.2"} | None}
    seen(runner) -> {fingerprint: {...}, "?": the latest unknown call}       (no keys: what the logs prove)
    note_added(fp) / note_gone(fp)                                           (kaskad.mcp.keys)
"""

import calendar
import fcntl
import json
import math
import os
import re
import threading
import time

from .. import paths, util

AUDIT_TAIL = 256 * 1024
AUTH_TAIL = 1024 * 1024
EVERY = 5.0                  # s: the answer is remembered this long
SCAN_EVERY = 30.0            # s: the logs are read at most this often (and only when a pair waits for it)
FRESH = 120.0                # s: a connection this young not found in the logs is looked up again
DEPTH = 14 * 86400           # s: the logs are not read further back; a call older than that is not looked up
SLACK = 600.0                # s: sshd accepts the connection a little before its initialize
LONG = 3 * 86400             # s: a connection without its initialize in the tail (Claude Desktop holds it for days)
FAIL_MIN, FAIL_MAX = 60.0, 3600.0
KEEP = 30 * 86400            # s: closed intervals of the keys kept this long
UNKNOWN = "?"
AUTH_LOG = "/var/log/auth.log"

_now = time.time
_lock = threading.Lock()
_cache = {"at": None, "keys": None, "value": None}
_ACCEPT = re.compile(r"Accepted publickey for \S+ from (\S+) port (\d+) ssh2: \S+ (SHA256:[A-Za-z0-9+/]{43})")
_TS = re.compile(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)Z$")


def state_path():
    return paths.P("/var/lib/kaskad/mcp/seen.json")


def forget():
    """Drop the answer remembered in memory (tests; the state file stays)."""
    _cache.update(at=None, keys=None, value=None)


def audit_path():
    from .server import DEFAULT_AUDIT_LOG, env_name
    return os.environ.get(env_name("AUDIT")) or paths.P(DEFAULT_AUDIT_LOG)      # as the server takes it (KASKAD_MCP_AUDIT)


def _tail(path, size):
    """The last ``size`` bytes of the file as lines (the first, cut, line dropped); [] when there is no file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - size))
            data = f.read()
    except OSError:
        return []
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[1:] if end > size else lines


def _epoch(ts):
    m = _TS.match(str(ts or ""))
    if not m:
        return None
    try:
        return calendar.timegm(tuple(int(x) for x in m.groups()) + (0, 0, 0))
    except (ValueError, OverflowError):
        return None


def _calls():
    """{"ip port": {"at", "client", "first", "init"}} — each SSH connection of the audit tail: its latest call, the
    client, its earliest call and its latest ``initialize`` (None when it left the tail)."""
    out = {}
    for line in _tail(audit_path(), AUDIT_TAIL):
        try:
            rec = json.loads(line)
        except (ValueError, RecursionError):                    # a spoiled line (R-333 П2″): skipped
            continue
        if not isinstance(rec, dict) or rec.get("iface") != "mcp":
            continue
        ip, port = str(rec.get("from") or ""), str(rec.get("port") or "")
        if not ip or ip == "local" or " " in ip or not port.isdigit():
            continue
        at = _epoch(rec.get("ts"))
        if at is None:
            continue
        key = ip + " " + port
        was = out.get(key)
        if was is None:
            was = out[key] = {"at": at, "client": "", "first": at, "init": None}
        if rec.get("kind") == "initialize" and (was["init"] is None or at > was["init"]):
            was["init"] = at
        was["first"] = min(was["first"], at)
        client = rec.get("client") if rec.get("client") not in (None, "", "-") else ""
        if at >= was["at"]:
            was["at"] = at
            if client:
                was["client"] = str(client)[:100]
        elif client and not was["client"]:
            was["client"] = str(client)[:100]
    return out


# ── the state file ─────────────────────────────────────────────────────────

def _empty(now):
    return {"v": 1, "horizon": now, "pairs": {}, "last": {}, "keys": {}, "scan": {"next": 0, "fail": 0}}


def _read(now):
    return _load(now)[0]


def _load(now):
    """(the state, what is in the file now as _dump makes it). A file that is spoiled — not json, another version,
    a field of a wrong type — gives the repaired state and a text that differs from it, so _write puts it right."""
    try:
        with open(state_path(), "rb") as f:
            raw = f.read()
    except OSError:
        st = _empty(now)
        return st, _dump(st)                                   # no file: nothing to repair
    # bytes that are not UTF-8 (UnicodeDecodeError is a ValueError) and a nesting deeper than the parser's recursion
    # limit are a spoiled file too: repaired by the next write, not an error of every answer (R-333 П2″)
    try:
        st = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return _empty(now), None
    if not isinstance(st, dict) or st.get("v") != 1:
        return _empty(now), None
    try:
        was = _dump(st)
    except (ValueError, RecursionError):                        # NaN / Infinity (allow_nan is off in _dump)
        was = None
    return _clean(st, now), was


def _num(v):
    """A finite number of the file (not a bool, not NaN or Infinity — json reads those), else None."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _clean(st, now):
    """The state as the code expects it: a field of a wrong type, at any depth, goes (or takes its default), so a
    file spoiled by hand or by a crash is repaired by the next write instead of breaking every answer."""
    out = _empty(now)
    h = _num(st.get("horizon"))
    if h is not None:
        out["horizon"] = h
    keys = st.get("keys") if isinstance(st.get("keys"), dict) else {}
    for fp, iv in keys.items():
        if not isinstance(fp, str) or not isinstance(iv, list):
            continue
        good = [[x[0], x[1]] for x in iv if isinstance(x, list) and len(x) == 2 and _num(x[0]) is not None
                and (x[1] is None or _num(x[1]) is not None)]
        if good:
            out["keys"][fp] = good
    pairs = st.get("pairs") if isinstance(st.get("pairs"), dict) else {}
    for k, p in pairs.items():
        if (isinstance(k, str) and isinstance(p, dict) and (p.get("fp") is None or isinstance(p.get("fp"), str))
                and (p.get("init") is None or _num(p.get("init")) is not None)):
            out["pairs"][k] = {"fp": p.get("fp") or None, "init": p.get("init")}       # a bad pair is looked up again
    last = st.get("last") if isinstance(st.get("last"), dict) else {}
    for fp, r in last.items():
        if isinstance(fp, str) and isinstance(r, dict) and _num(r.get("at")) is not None:
            c = r.get("client")
            out["last"][fp] = {"at": r["at"], "client": str(c)[:100] if isinstance(c, str) else ""}
    sc = st.get("scan") if isinstance(st.get("scan"), dict) else {}
    nxt, fail = _num(sc.get("next")), _num(sc.get("fail"))
    if nxt is not None and nxt <= now + FAIL_MAX:            # a pause from far in the future would stop the reads
        out["scan"]["next"] = nxt
    if fail is not None and 0 <= fail <= 64:
        out["scan"]["fail"] = int(fail)
    return out


class _Locked(object):
    """flock of the state file for a read-modify-write (the panel and ``kaskadctl mcp key add`` share it)."""

    def __enter__(self):
        p = state_path() + ".lock"
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            os.makedirs(d, 0o700)
        self.fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def _write(st, was):
    text = _dump(st)
    if text != was:
        util.atomic_write(state_path(), text + "\n", mode=0o600)


def _dump(st):
    return json.dumps(st, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sync_keys(st, keys, now):
    """Open an interval for a current key without one, close it for a key that is gone; drop old closed ones."""
    ks = st["keys"]
    cur = set(keys)
    for fp in cur:
        iv = ks.setdefault(fp, [])
        if not iv or iv[-1][1] is not None:
            iv.append([now, None])
    for fp, iv in list(ks.items()):
        if fp not in cur and iv and iv[-1][1] is None:
            iv[-1][1] = now
        keep = []
        for a, g in iv:
            if g is not None and g < now - KEEP:
                st["horizon"] = max(st["horizon"], g)        # what was before is not known any more
            else:
                keep.append([a, g])
        if keep:
            ks[fp] = keep
        else:
            del ks[fp]
    for fp in list(st["last"]):
        if fp not in cur:
            del st["last"][fp]


def _note(fp, added):
    now = _now()
    with _Locked():
        st, was = _load(now)
        iv = st["keys"].setdefault(fp, [])
        if added and (not iv or iv[-1][1] is not None):
            iv.append([now, None])
        elif not added and iv and iv[-1][1] is None:
            iv[-1][1] = now
        if not added:
            st["last"].pop(fp, None)
        _write(st, was)
    _cache.update(at=None, keys=None, value=None)


def note_added(fp):
    """kaskad.mcp.keys.add: the key is an agent's from now on (best effort: «подключено» must not break the key)."""
    try:
        _note(fp, True)
    except (OSError, ValueError):
        pass


def note_gone(fp):
    try:
        _note(fp, False)
    except (OSError, ValueError):
        pass


# ── the logs of sshd ───────────────────────────────────────────────────────

def _accepts(lines, into):
    for line in lines:
        m = _ACCEPT.search(line)
        if m:
            into[m.group(1) + " " + m.group(2)] = m.group(3)        # the latest wins: lines go oldest first


def _scan(runner, since):
    """({"ip port": fingerprint} of the Accepted lines since ``since``, complete) or (None, False) when no log could
    be read. ``complete`` is False when journalctl is there but failed (a timeout on a big journal): what the tail of
    auth.log named is taken, a pair it does not name is not final — the journal may name it next time."""
    found, read, complete = {}, False, True
    if os.path.exists(paths.P(AUTH_LOG)):
        _accepts(_tail(paths.P(AUTH_LOG), AUTH_TAIL), found)
        read = True
    if runner.which("journalctl"):
        r = runner.run(["journalctl", "-q", "--no-pager", "-o", "cat", "-t", "sshd", "-t", "sshd-session",
                        "-g", "Accepted publickey", "--since", "@%d" % int(since)], timeout=15)
        if r.rc in (0, 1):                     # 1: nothing matched
            _accepts((r.out or "").splitlines(), found)
            read = True
        else:
            complete = False
    return (found, complete) if read else (None, False)


def _resolve(runner, st, calls, now):
    """Look up the pairs of the audit not looked up yet (one read of the logs); prune pairs that left the tail."""
    pairs = st["pairs"]
    for k in list(pairs):
        if k not in calls:
            del pairs[k]
    todo = []
    for k, c in calls.items():
        p = pairs.get(k)
        if p is not None and not (c["init"] is not None and (p.get("init") or 0) < c["init"]):
            continue
        if c["at"] < now - DEPTH:
            pairs[k] = {"fp": None, "init": c["init"]}          # older than the logs we read: not looked for
            continue
        todo.append(k)
    sc = st["scan"]
    if not todo or now < sc.get("next", 0):
        return
    since = min((calls[k]["init"] - SLACK) if calls[k]["init"] is not None else (calls[k]["first"] - LONG)
                for k in todo)
    found, complete = _scan(runner, max(since, now - DEPTH))
    if found is None or not complete:
        sc["fail"] = int(sc.get("fail", 0)) + 1
        sc["next"] = now + min(FAIL_MAX, FAIL_MIN * 2 ** (sc["fail"] - 1))
    else:
        sc.update(fail=0, next=now + SCAN_EVERY)
    for k in todo:
        fp = (found or {}).get(k)
        # the age of the CONNECTION (its initialize, else its earliest call in the tail), not of its last call: a
        # session that keeps calling is still final in FRESH seconds. A clock far ahead is not «fresh» for ever.
        opened = calls[k]["init"] if calls[k]["init"] is not None else calls[k]["first"]
        if fp or (complete and found is not None and abs(now - opened) >= FRESH):
            pairs[k] = {"fp": fp, "init": calls[k]["init"]}


def _view(st, calls):
    """(proven {fp: {"at", "client"}}, unknown [{"at", "client"}] newest first) of the audit tail. A pair not looked
    up yet is neither: the journal may name its key a moment later."""
    proven, unknown = {}, []
    for k, c in calls.items():
        rec = {"at": c["at"], "client": c["client"]}
        if k not in st["pairs"]:
            continue
        fp = st["pairs"][k].get("fp")
        if fp:
            if fp not in proven or rec["at"] > proven[fp]["at"]:
                proven[fp] = rec
        else:
            unknown.append(dict(rec, init=c["init"]))
    unknown.sort(key=lambda r: -r["at"])
    return proven, unknown


def _owner(st, at):
    """The only key that was an agent's at ``at`` (after the horizon), else None."""
    if at < st["horizon"]:
        return None
    who = [fp for fp, iv in st["keys"].items() if any(a <= at and (g is None or at < g) for a, g in iv)]
    return who[0] if len(who) == 1 else None


def _newer(a, b):
    return a if b is None or a["at"] > b["at"] else b


def for_keys(keys, runner):
    """{fingerprint: {"at", "client"} | None} of the agent's keys."""
    keys = list(keys)
    now = _now()
    with _lock:
        if (_cache["at"] is not None and now - _cache["at"] < EVERY and _cache["keys"] == keys
                and _cache["value"] is not None):
            return dict(_cache["value"])
        calls = _calls()
        snap = _read(now)
        _resolve(runner, snap, calls, now)                  # the logs are read outside the file lock
        with _Locked():
            st, was = _load(now)
            st["pairs"], st["scan"] = snap["pairs"], snap["scan"]
            _sync_keys(st, keys, now)
            proven, unknown = _view(st, calls)
            last = st["last"]
            for fp, rec in proven.items():
                if fp in keys:
                    last[fp] = dict(_newer(rec, last.get(fp)))
            for rec in unknown:
                # by elimination only a connection OPENED (its initialize in the tail) when that key alone was an
                # agent's: a session of a deleted key held for days keeps calling, and its calls must not light up
                # the key added after it (R-333 З3′)
                if rec["init"] is None:
                    continue
                fp = _owner(st, rec["init"])
                if fp in keys:
                    last[fp] = dict(_newer({"at": rec["at"], "client": rec["client"]}, last.get(fp)))
            _write(st, was)
        out = dict((fp, dict(last[fp]) if fp in last else None) for fp in keys)
        _cache.update(at=now, keys=keys, value=out)
        return dict(out)


def seen(runner):
    """What the logs prove of the audit tail, without the keys: {fingerprint: {...}, "?": the latest unknown call}."""
    now = _now()
    with _lock:
        calls = _calls()
        snap = _read(now)
        _resolve(runner, snap, calls, now)
        with _Locked():
            st, was = _load(now)
            st["pairs"], st["scan"] = snap["pairs"], snap["scan"]
            _write(st, was)
        proven, unknown = _view(st, calls)
    if unknown:
        proven[UNKNOWN] = {"at": unknown[0]["at"], "client": unknown[0]["client"]}
    return proven
