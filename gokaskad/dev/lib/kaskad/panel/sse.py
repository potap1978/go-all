"""Server-sent events of the web panel: ``GET /api/events`` (docs/k3/SPEC-wave3.md §3.3).

``text/event-stream``, ``X-Accel-Buffering: no``, ``retry: 3000`` first, a comment ``: hb`` after HEARTBEAT s of
silence. At most MAX_STREAMS streams per process (the next one gets 503 and the page polls ``GET /api/overview``).
Events (data is one line of JSON):

    live    the live snapshot of the collector (/run/kaskad/live.json, kaskad.gw.live) WITHOUT ``events`` and
            ``seq``. The first one after connecting carries the whole document and ``"full": true``; then each
            one is a JSON merge patch (RFC 7386) against the previous state of this stream — only the fields
            that changed, ``null`` for a field that is gone (or became unknown). No FRESH data — no live.json yet,
            or it is older than ``live.STALE`` (the collector is stopped or hangs; the same rule as the REST
            views): ``{"full": true}`` without anything else, sent again when the file goes stale while the
            stream is open; the next fresh write comes as a full event again. Data always carries ``ts``.
    state   something the sections show changed: ``{"gen": N, "parts": ["state", "exits", "pool", "appearance",
            "layout", "panel"]}`` — a change made by this panel (``kaskad.snapshot.generation``) or by anybody
            else (the signature of state.json, exits.json, upstream.txt, appearance.json, layout.json); the page
            re-reads what it shows.
    alert   one event of live.json that appeared after the stream opened: ``{"id", "ts", "kind": "exit_dead|
            exit_alive|inbound_down|inbound_up|gw_part_restored", "text", "exit"?, "inbound"?}``; stage 1
            (SPEC-stage1-exits §7.9, T7) — every kind of the feed passes as it is: ``exit_degraded``,
            ``exit_recovered``, ``exit_standby``, ``exit_removed``, ``exit_failover``, ``exit_quota``,
            ``exit_country_changed``, ``group_last_step``, ``group_restored``, ``node_offline``, ``node_online``
            (and ``group``, ``role`` of the event). ``live`` carries ``exits[id]`` with ``health``, ``reasons``,
            ``recovering``, ``unstable`` (gw/live.py, T4), ``no_exit``, ``no_exit_ru``, ``groups`` = {gid:
            {active_tier, last_step, since}} and ``policy`` = ``{node_offline, node_ru_offline}`` (T15 Q6); the
            version ``rev`` never comes in SSE (the panel takes it from its reads, §7).

One hub thread (only while somebody listens) stats the watched files every POLL s — a changed live.json is
parsed once (kaskad.snapshot) for every stream, and its age is looked at on every poll (a file nobody writes any
more goes stale without changing) — and touches the beacon /run/kaskad/live.want every WANT_EVERY s: the collector
then writes live.json every 2 s instead of 10 (no subscribers for 30 s — back to 10). Streams sleep on a condition
between changes; nothing here runs a command.

``state_gen()`` — the same «something changed» as a token for ``GET /api/overview`` (``gen``): the fallback poll
of a page without SSE re-reads its sections when it changes.
"""

import hashlib
import json
import threading
import time

from .. import paths, snapshot
from ..gw import live
from . import auth, conf

HEARTBEAT = 15.0
POLL = 1.0
WANT_EVERY = 10.0
MAX_STREAMS = 8
IDLE_EXIT = 5.0             # s without streams before the hub thread ends
HISTORY = 32                # state changes remembered for the parts of a late stream
DROP = ("events", "seq", "i18n")    # not in ``live``: events go as ``alert``, seq changes every write; i18n — service
#                                    field of live.json (3.3.2: the language of the record), live.read() retells for the reader


def _watched():
    from ..gw import exits
    return (("state", paths.state()), ("exits", exits.snapshot_path()), ("pool", paths.egress_up()),
            ("appearance", conf.appearance_path()), ("layout", conf.layout_path()))


def state_key():
    """What the sections show, as a key: the generation of this panel's changes and the signatures of the watched
    files (a change by the bot, the menu or another tab). Stats only."""
    return (snapshot.generation(),) + tuple(snapshot.signature(p) for _, p in _watched())


_POLL_WANT = [0.0]           # when the fallback poll last touched the beacon (``poll_seen``)


def poll_seen(now=None):
    """3.3.7.11: the fallback poll of a page (GET /api/overview without the stream) is a panel that watches too —
    the beacon /run/kaskad/live.want at most every WANT_EVERY s, as the hub does for its streams: the collector keeps
    what «Пользователи» and «Выходы» show fresh (``collect.LIGHT_MAX``) for it as well. -> True touched."""
    now = time.time() if now is None else now
    if 0 <= now - _POLL_WANT[0] < WANT_EVERY:
        return False
    _POLL_WANT[0] = now
    try:
        live.touch_want()
    except OSError:
        return False
    return True


def state_gen():
    """``state_key()`` as a short token (``gen`` of GET /api/overview): it changes exactly when the hub would send
    a ``state`` event, also while nobody listens to SSE (the fallback poll compares it between two answers)."""
    raw = json.dumps(state_key(), separators=(",", ":"), default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _fresh(doc, age):
    """The snapshot when it is fresh (``live.fresh``: the rule of the REST views), else None — unknown, not old."""
    return doc if doc is not None and live.fresh(age) else None


def merge_patch(old, new):
    """The JSON merge patch (RFC 7386) that turns ``old`` into ``new``: changed and new keys, ``None`` for the
    keys that went away; {} when nothing changed. Lists and scalars are replaced whole."""
    patch = {}
    for k, v in new.items():
        if k not in old:
            patch[k] = v
            continue
        ov = old[k]
        if isinstance(v, dict) and isinstance(ov, dict):
            sub = merge_patch(ov, v)
            if sub:
                patch[k] = sub
        elif v != ov or type(v) is not type(ov):
            patch[k] = v
    for k in old:
        if k not in new:
            patch[k] = None
    return patch


def _view(doc):
    if not isinstance(doc, dict):
        return None
    return dict((k, v) for k, v in doc.items() if k not in DROP)


def event(name, data):
    return ("event: %s\ndata: %s\n\n" % (name, json.dumps(data, ensure_ascii=False, sort_keys=True,
                                                         separators=(",", ":")))).encode("utf-8")


class Hub(object):
    """What every stream of this process shares: the last live snapshot, the state generation of the panel,
    the subscribers. ``check()`` is one poll (the hub thread runs it; tests call it directly)."""

    def __init__(self):
        self.cond = threading.Condition()
        self.version = 0            # +1 on every change a stream should look at
        self.kicks = 0              # +1 on every kick: a stream that took its count before a kick never sleeps through it
        self.raw = None             # the last parsed live.json, whatever its age (its events; shared, never changed)
        self.live = None            # ``raw`` while it is fresh, else None — what ``live`` events show
        self.gen = 0                # state generation of the streams
        self.history = []           # [(gen, parts)]
        self.streams = 0
        self.stopped = False
        self._check_lock = threading.Lock()
        self._live_sig = False      # False: never looked
        self._state_key = None
        self._pw_sig = False        # the signature of password.json (False: never looked)
        self._want_at = 0.0
        self._wake = threading.Event()
        self._thread = None
        self._idle_since = None

    # ── subscribers ──
    def subscribe(self):
        with self.cond:
            if self.stopped or self.streams >= MAX_STREAMS:
                return False
            self.streams += 1
            first = self.streams == 1
        if first:
            self._want_at = 0.0             # the collector switches to 2 s rounds right away
        self.check()
        self._ensure_thread()
        return True

    def unsubscribe(self):
        with self.cond:
            self.streams = max(0, self.streams - 1)

    def poke(self):
        """A change was made here (a mutation): look now, not in POLL s."""
        self._wake.set()

    def kick(self):
        """Wake every stream now (a session was dropped, a listener closed, the settings changed): each one checks
        that its right is still there."""
        with self.cond:
            self.kicks += 1
            self.cond.notify_all()

    def stop(self):
        with self.cond:
            self.stopped = True
            self.cond.notify_all()
        self._wake.set()

    def reset(self):
        """Back to a fresh hub (tests; a panel that starts again in the same process)."""
        with self.cond:
            self.stopped = False
            self.streams = 0
            self.version += 1
            self.cond.notify_all()
        self._wake.set()

    # ── the thread ──
    def _ensure_thread(self):
        with self._check_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._idle_since = None
            self._thread = threading.Thread(target=self._run, name="kaskad-panel-sse", daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            self._wake.wait(POLL)
            self._wake.clear()
            with self.cond:
                if self.stopped:
                    return
                streams = self.streams
            if streams == 0:
                now = time.monotonic()
                if self._idle_since is None:
                    self._idle_since = now
                elif now - self._idle_since >= IDLE_EXIT:
                    with self._check_lock:
                        if self.streams == 0:
                            self._thread = None
                            return
            else:
                self._idle_since = None
            try:
                self.check()
            except Exception:                       # noqa: BLE001 — the hub must outlive one bad poll
                pass

    def _state_now(self):
        return state_key()

    def check(self):
        """One poll: the beacon, live.json (a new write, or its age crossing ``live.STALE``), the watched files and
        the generation. Wakes the streams on a change."""
        with self._check_lock:
            now = time.time()
            if self.streams and now - self._want_at >= WANT_EVERY:
                try:
                    live.touch_want()
                except OSError:
                    pass
                self._want_at = now
            changed = False
            sig = snapshot.signature(paths.live_json())
            doc, age = live.read()                      # the shared cache: a stat when the file did not change
            cur = _fresh(doc, age)
            if sig != self._live_sig or (cur is None) != (self.live is None):
                self._live_sig = sig
                self.raw = doc
                self.live = cur
                changed = True
            key = self._state_now()
            if key != self._state_key:
                old, self._state_key = self._state_key, key
                if old is not None:
                    parts = [name for (name, _), a, b in zip(_watched(), old[1:], key[1:]) if a != b]
                    if old[0] != key[0]:
                        parts.append("panel")
                    with self.cond:
                        self.gen += 1
                        self.history = (self.history + [(self.gen, parts)])[-HISTORY:]
                    changed = True
            # the password changed outside the panel (``kaskadctl panel password``, the SSH menu, the wizard): the
            # sessions of the old one are gone — their streams end now, not on their next wake (F19b)
            pw = snapshot.signature(auth.password_path())
            revoked = self._pw_sig is not False and pw != self._pw_sig
            self._pw_sig = pw
            if changed:
                with self.cond:
                    self.version += 1
                    self.cond.notify_all()
            if revoked:
                self.kick()

    def parts_since(self, gen):
        with self.cond:
            hist = [h for h in self.history if h[0] > gen]
            if not hist or hist[0][0] != gen + 1:
                return [name for name, _ in _watched()] + ["panel"]
        out = []
        for _, parts in hist:
            for p in parts:
                if p not in out:
                    out.append(p)
        return out


HUB = Hub()


class Stream(object):
    """The response of ``GET /api/events``: the server sends ``status``/``headers`` and then calls ``run(write)``
    in the request's thread; ``run`` returns when the client goes away (a write fails) or the hub stops."""

    status = 200
    headers = {"Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store",
               "X-Accel-Buffering": "no"}

    def __init__(self, hub, alive=None):
        self.hub = hub
        # alive() -> bool: the session of this stream is still there; False — the stream ends at once (F19: «Выйти»
        # in one tab ends the live data of every tab of that session, not only of the tab that pressed it)
        self.alive = alive
        self.gates = []                 # more such checks (F19b): the listener is still open, the tunnel still admin

    def gate(self, check):
        """One more condition of this stream (callable -> bool), looked at with ``alive``."""
        self.gates.append(check)
        return self

    def allowed(self):
        """Every check of this stream holds: before the first byte and on every wake, BEFORE the data is read
        (F19b: a stream whose right is gone sends nothing more — not one snapshot, not a heartbeat)."""
        if self.alive is not None and not self.alive():
            return False
        return all(check() for check in self.gates)

    def run(self, write):
        hub = self.hub
        try:
            with hub.cond:
                version, doc, raw, gen, kicks = hub.version, hub.live, hub.raw, hub.gen, hub.kicks
            sent = _view(doc)
            # events already in the file (fresh or not) are history, not news: only newer ids become alerts
            last_id = max([e.get("id") or 0 for e in (raw or {}).get("events") or [] if isinstance(e, dict)] + [0])
            first = dict(sent or {})
            first["full"] = True
            if not self.allowed():          # the session went between the request's check and here (F19b, finding 3)
                return
            write(b"retry: 3000\n\n" + event("live", first))
            last_write = time.monotonic()
            while True:
                with hub.cond:
                    if hub.version == version and hub.kicks == kicks and not hub.stopped:
                        hub.cond.wait(max(0.05, HEARTBEAT - (time.monotonic() - last_write)))
                    if hub.stopped:
                        return
                    version, doc, raw, new_gen, kicks = hub.version, hub.live, hub.raw, hub.gen, hub.kicks
                if not self.allowed():
                    return
                out = []
                view = _view(doc)
                if view != sent:
                    if sent is None or view is None:            # data came back, or went stale: a whole picture
                        patch = dict(view or {})
                        patch["full"] = True
                    else:
                        patch = merge_patch(sent, view)
                    if patch:
                        out.append(event("live", patch))
                    sent = view
                evs = [e for e in (raw or {}).get("events") or [] if isinstance(e, dict) and isinstance(e.get("id"), int)]
                if evs and last_id > max(e["id"] for e in evs):
                    last_id = 0                     # the ids went back (the clock): take the file as it is
                for ev in evs:
                    if ev["id"] > last_id:
                        out.append(event("alert", ev))
                        last_id = ev["id"]
                if new_gen != gen:
                    out.append(event("state", {"gen": new_gen, "parts": hub.parts_since(gen)}))
                    gen = new_gen
                if out:
                    write(b"".join(out))
                    last_write = time.monotonic()
                elif time.monotonic() - last_write >= HEARTBEAT - 0.01:
                    write(b": hb\n\n")
                    last_write = time.monotonic()
        finally:
            hub.unsubscribe()


def open_stream(alive=None):
    """A Stream, or None when MAX_STREAMS are open already. ``alive`` — see ``Stream``."""
    if not HUB.subscribe():
        return None
    return Stream(HUB, alive)


def counts():
    with HUB.cond:
        return {"streams": HUB.streams, "max": MAX_STREAMS}
