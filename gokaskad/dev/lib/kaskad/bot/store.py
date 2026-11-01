"""Files of the Python bot in /etc/kaskad/bot_state (0700; files 0600, written atomically).

    <chat>.json      the dialog of a chat: {"v": 1, "state", "data", "ts"} — lives 30 minutes (TTL)
    offset.json      {"v": 1, "offset", "bot", "last_poll"} — written AFTER the updates were handled
    strangers.json   {chat: ts of the last «Нет доступа»} — one answer a day per chat
    monitor.json     what the monitoring thread already said (alerted targets, the last gateway event)
    expiring.json    messages to delete later: [[due, chat, message], …] (the whole address of the panel)

The bash bot's files there (``<chat>`` without an extension, ``.offset``, ``system`` with UPDATE_TOKEN) belong
to 2.13 and are only read (``.offset`` once, when offset.json does not exist yet), never written.
"""

import json
import os
import re
import threading

from .. import paths, util

TTL = 30 * 60
_CHAT = re.compile(r"^-?\d{1,20}$")


def path(name):
    return os.path.join(paths.bot_state(), name)


def ensure_dir():
    util.ensure_dir(paths.bot_state(), 0o700)


def read_json(p, default=None):
    raw = util.read_text(p)
    if raw is None:
        return default
    try:
        doc = json.loads(raw)
    except ValueError:
        return default
    return doc


def write_json(p, obj):
    ensure_dir()
    util.atomic_write(p, json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n", 0o600)


def remove(p):
    try:
        os.unlink(p)
        return True
    except OSError:
        return False


class Dialogs(object):
    """Dialog of each chat. ``get`` -> (state, data) or (None, None); an expired one is removed and reported
    once through ``expired`` (the handler says «диалог устарел»)."""

    def __init__(self, clock, ttl=TTL):
        self._clock = clock
        self.ttl = ttl

    def _file(self, chat_id):
        c = str(chat_id)
        if not _CHAT.match(c):
            raise ValueError("chat id %r" % (chat_id,))
        return path("%s.json" % c)

    def get(self, chat_id):
        """(state, data, expired): state None when there is no live dialog; ``expired`` — there was one, too old."""
        p = self._file(chat_id)
        doc = read_json(p)
        if not isinstance(doc, dict) or not doc.get("state"):
            return None, None, False
        ts = doc.get("ts")
        if not isinstance(ts, (int, float)) or self._clock() - ts > self.ttl or ts > self._clock() + 300:
            remove(p)
            return None, None, True
        data = doc.get("data")
        return str(doc["state"]), (data if isinstance(data, dict) else {}), False

    def set(self, chat_id, state, data=None):
        write_json(self._file(chat_id), {"v": 1, "state": state, "data": data or {}, "ts": self._clock()})

    def clear(self, chat_id):
        p = self._file(chat_id)
        if os.path.exists(p):
            remove(p)


class Expiring(object):
    """Messages the bot deletes later (the whole address of the panel): expiring.json [[due, chat, message], …].
    On disk, so a restart still deletes them; at most ``limit`` (the oldest are dropped)."""

    def __init__(self, limit=50):
        self._lock = threading.Lock()
        self.limit = limit

    def _load(self):
        doc = read_json(path("expiring.json"), [])
        out = []
        for x in doc if isinstance(doc, list) else []:
            if isinstance(x, list) and len(x) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                           for v in x):
                out.append([float(x[0]), int(x[1]), int(x[2])])
        return out

    def add(self, due, chat_id, message_id):
        with self._lock:
            items = self._load() + [[float(due), int(chat_id), int(message_id)]]
            write_json(path("expiring.json"), items[-self.limit:])

    def take(self, now=None):
        """The entries due at ``now`` (None — all of them), removed from the file."""
        with self._lock:
            items = self._load()
            if not items:
                return []
            due = [x for x in items if now is None or x[0] <= now]
            if due:
                rest = [x for x in items if x not in due]
                if rest:
                    write_json(path("expiring.json"), rest)
                else:
                    remove(path("expiring.json"))
            return due


class OffsetStore(object):
    def __init__(self):
        self._lock = threading.Lock()

    def load(self, bot_id=""):
        """(offset, last_poll or None). Another bot's offset (the token changed) does not count. Without
        offset.json — the bash bot's ``.offset`` (only read)."""
        doc = read_json(path("offset.json"))
        if isinstance(doc, dict) and isinstance(doc.get("offset"), int):
            if not bot_id or doc.get("bot") in (None, "", bot_id):
                lp = doc.get("last_poll")
                return doc["offset"], (lp if isinstance(lp, (int, float)) else None)
            return 0, None
        raw = (util.read_text(path(".offset")) or "").strip()
        if re.match(r"^\d{1,20}$", raw):
            return int(raw), None
        return 0, None

    def save(self, offset, last_poll, bot_id=""):
        with self._lock:
            write_json(path("offset.json"), {"v": 1, "offset": int(offset), "bot": bot_id,
                                             "last_poll": last_poll})


class Strangers(object):
    """One «Нет доступа» per chat per ``window`` seconds (persisted: a restart does not reset it)."""

    LIMIT = 2000

    def __init__(self, clock, window=86400):
        self._clock = clock
        self.window = window
        self._lock = threading.Lock()
        doc = read_json(path("strangers.json"), {})
        self._seen = dict((k, v) for k, v in (doc.items() if isinstance(doc, dict) else [])
                          if isinstance(v, (int, float)))

    def due(self, chat_id, window=None, kind=""):
        """True — answer this chat now (and remember it); False — it was answered within the window. ``kind``
        keeps separate counters (the unbound bot's hint vs «Нет доступа»)."""
        window = self.window if window is None else window
        now = self._clock()
        key = kind + str(chat_id)
        with self._lock:
            last = self._seen.get(key)
            if last is not None and 0 <= now - last < window:
                return False
            self._seen[key] = now
            if len(self._seen) > self.LIMIT:
                for k in sorted(self._seen, key=self._seen.get)[:len(self._seen) - self.LIMIT]:
                    del self._seen[k]
            snap = dict((k, v) for k, v in self._seen.items() if now - v < max(window, self.window))
        try:
            write_json(path("strangers.json"), snap)
        except OSError:
            pass
        return True
