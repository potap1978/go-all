"""The bot process (docs/k3/SPEC-wave3.md §4.3–4.4): receiver, access control, stale updates, dispatch.

Receiver (the main thread): long polling ``getUpdates`` (timeout 30, socket 35 s, only message and
callback_query). Each answer is a batch: every update goes to its chat's lane in the scheduler; the receiver
waits for the lane items of the batch (at most BATCH_WAIT s) and only THEN saves ``offset.json`` — a crash in
the middle re-delivers the batch instead of losing it (2.x saved the offset before handling). Failures: ×2
backoff from 1 s up to 60 s, a 429 waits exactly ``retry_after``; errors are logged without the token, once
per change (and every 10 minutes while they last).

Access: only ``BOT_CHAT_ID`` and ``BOT_ADMINS`` (chat ids). A stranger gets «Нет доступа» at most once a day
per chat; whatever happens to that answer, the update is never handled. An unbound bot (no chat id) only
tells people their chat id and how to bind it (SSH menu 8).

Stale updates: a message older than 10 minutes is not executed (one notice per chat and batch). A press has
no time of its own: the presses that were waiting when the bot polled after more than 10 minutes without a
successful poll (downtime of the bot, an outage of the network; the time of the last poll survives a restart
in offset.json) are answered «кнопка устарела» and not executed. Without such a gap a press is at most a
couple of minutes old (one poll plus one batch), so it is executed even when Telegram says it is too late to
answer it (its spinner has just timed out).

Handlers run in the lanes (one item of a chat at a time); long actions (ping, issuing) are the chat's job
(``Context.job``: one per chat). Errors of a handler: KaskadError — its text to the chat; anything else —
the traceback (redacted) to the journal and a short line to the chat.
"""

import json
import logging
import os
import signal
import sys
import threading
import time
import traceback

from .. import __version__, paths, sysrun, util
from ..errors import KaskadError
from ..net import socks
from . import config as bcfg
from . import menus, probe, registry, store
from .lang import server_lang, tr, use_lang
from .scheduler import Batch, Scheduler
from .telegram import Api, ApiError, NetworkError, h

log = logging.getLogger("kaskad.bot")
log.addHandler(logging.NullHandler())          # quiet unless the daemon sets up its stderr handler

STALE = 600                 # s: older messages and presses are not executed
BATCH_WAIT = 90.0           # s the receiver waits for the lane items of a batch before saving the offset
POLL_TIMEOUT = 30
BACKOFF_MAX = 60
QUICK = 3.0                 # s: a poll answered faster than this returned updates that were already waiting
PERSIST_EVERY = 300         # s: last_poll is saved at least this often while the polls are empty
STATUS_EVERY = 30           # s between writes of /run/kaskad/bot.json
EDITS_KEPT = 512             # messages whose edits are counted (a late redraw of a screen asks: still mine?)
UNBOUND_WINDOW = 60         # s between the «not bound yet» answers to one chat
COMMANDS = ("start", "menu", "help", "id", "lang", "inline")       # the menu of commands; texts — bot.cmd.<name>
BASH_MONITOR = "kaskad-monitor.service"
# «Прокси для API Telegram» не везёт — откат на прямой путь (SPEC-tg-proxy Р3): FALLBACK_FAILS опросов подряд через
# прокси не прошли, и с первого из них прошло FALLBACK_AFTER с — getMe напрямую (FALLBACK_PROBE с); прошёл — бот ходит
# напрямую и раз в FALLBACK_RETRY с пробует прокси; не прошёл — прямой путь снова не раньше чем через FALLBACK_RETRY с
FALLBACK_FAILS = 3
FALLBACK_AFTER = 90.0
FALLBACK_RETRY = 300.0
FALLBACK_PROBE = 10


def commands(lang):
    """[(command, description)] of setMyCommands in ``lang``."""
    return [(c, tr("bot.cmd." + c, lang)) for c in COMMANDS]


def status_path():
    return paths.P("/run/kaskad/bot.json")


class _Interrupt(BaseException):
    """Raised by the signal handler to cut a long poll short."""


class RedactFilter(logging.Filter):
    def filter(self, record):
        record.msg = util.redact(record.getMessage())
        record.args = None
        return True


def setup_logging(stream=None):
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RedactFilter())
    log.handlers[:] = [handler]
    log.setLevel(logging.INFO)
    log.propagate = False


class Context(object):
    """One update being handled: where to answer and how."""

    def __init__(self, app, chat_id, message_id=None, user_id=None, new=False):
        self.app = app
        self.chat_id = chat_id
        self.message_id = message_id        # the message whose button was pressed (edited by reply())
        self.user_id = user_id
        self.new = new                      # «_new» / reply keyboard: answer with a new message

    @property
    def api(self):
        return self.app.api

    @property
    def runner(self):
        return self.app.runner

    def send(self, text, markup=None, preview=True, protect=False):
        if protect:
            return self.app.api.send_message(self.chat_id, text, markup, preview, protect=True)
        return self.app.api.send_message(self.chat_id, text, markup, preview)

    def edit(self, message_id, text, markup=None, preview=True):
        self.app.note_edit(self.chat_id, message_id)
        try:
            return self.app.api.edit_message(self.chat_id, message_id, text, markup, preview)
        except ApiError as e:
            if e.cant_edit:
                return self.send(text, markup, preview)
            raise

    def reply(self, text, markup=None, preview=True):
        """Edit the message of the pressed button; a new message for typed text, the reply keyboard, «_new»."""
        if self.message_id is not None and not self.new:
            return self.edit(self.message_id, text, markup, preview)
        return self.send(text, markup, preview)

    def set_state(self, state, data=None):
        self.app.dialogs.set(self.chat_id, state, data)

    def clear_state(self):
        self.app.dialogs.clear(self.chat_id)

    def job(self, name, fn):
        """Run ``fn()`` as the chat's long action. False (and a short notice) when one is already running."""
        app, chat = self.app, self.chat_id

        def run():
            app.guard(Context(app, chat), fn)

        running = app.scheduler.start_job(chat, name, run)
        if running is not None:
            app.safe(self.send, tr("bot.menu.busy", what=h(running)))
            return False
        return True


class App(object):
    def __init__(self, cfg=None, runner=None, clock=None, sleep=None, api_base=None, tls_context=None, workers=4,
                 prober=None, my_ip=None, poll_timeout=POLL_TIMEOUT, manage_units=False, monitor=True):
        self.clock = clock or time.time
        self.stop_event = threading.Event()
        self.sleep = sleep or self.stop_event.wait
        self.runner = runner or sysrun.default_runner()
        self.api_base = api_base or os.environ.get("KASKAD_TG_API") or None
        self.tls_context = tls_context
        self.poll_timeout = poll_timeout
        self.manage_units = manage_units
        self.with_monitor = monitor
        self.cfg = cfg if cfg is not None else bcfg.load()
        self.proxy_error = ""
        self.via = "direct"             # "proxy" | "direct" | "fallback" (BOT_PROXY set, the bot went straight: Р3)
        self.api = self._make_api(self.cfg)
        self.dialogs = store.Dialogs(self.clock)
        self.offsets = store.OffsetStore()
        self.strangers = store.Strangers(self.clock)
        self.expiring = store.Expiring()
        self.scheduler = Scheduler(workers, on_error=self._worker_error)
        self.edits = {}                 # (chat, message_id) -> edits by Context.edit (the last EDITS_KEPT messages)
        self._edits_lock = threading.Lock()
        self.prober = prober or probe.Prober(self.runner)
        self.my_ip = my_ip
        self.monitor = None
        self.backoff = 0
        self.username = ""
        self.commands_lang = None       # the language of the menu of commands Telegram has (None — not set yet)
        self.commands_wanted = False    # the daemon keeps the menu of commands in the server's language
        self.started = self.clock()
        self.last_error = ""
        self._logged = ("", 0.0)
        self._status_at = 0.0
        self._in_poll = False
        self.offset, self.last_poll = self.offsets.load(self.api.bot_id)
        self._persisted = self.last_poll or 0.0
        registry.load()

    # ── setup ──
    def _make_api(self, cfg):
        proxy = None
        self.proxy_error = ""
        try:
            proxy = socks.parse_proxy(cfg.proxy)
        except ValueError as e:
            self.proxy_error = str(e)
        self.via = "proxy" if proxy else "direct"
        self._px_fails, self._px_since, self._direct_at, self._proxy_at, self.fallback_since = 0, None, None, None, None
        return self._new_api(cfg, proxy)

    def _new_api(self, cfg, proxy):
        # the token goes to api.telegram.org only (or KASKAD_TG_API of the tests), by TLS with a checked certificate —
        # through the proxy and straight alike
        return Api(cfg.token, proxy=proxy, base=self.api_base, context=self.tls_context, sleep=self.sleep)

    # ── the way to Telegram: the proxy of the group, straight while it does not carry (SPEC-tg-proxy Р3) ──
    def _proxy_failed(self, e):
        """A poll through the proxy failed (no answer: NetworkError): FALLBACK_FAILS in a row for FALLBACK_AFTER s —
        getMe straight; it answers — the bot goes straight until the proxy carries again (``_maybe_back``)."""
        if self.via == "fallback":
            self._proxy_at = None               # the straight way fails too: the proxy is tried before the next poll
            return
        if self.via != "proxy" or not isinstance(e, NetworkError):
            return
        now = self.clock()
        self._px_fails += 1
        if self._px_since is None:
            self._px_since = now
        if self._px_fails < FALLBACK_FAILS or now - self._px_since < FALLBACK_AFTER:
            return
        if self._direct_at is not None and now - self._direct_at < FALLBACK_RETRY:
            return
        self._direct_at = now
        api = self._new_api(self.cfg, None)
        try:
            api.get_me(timeout=FALLBACK_PROBE)
        except (ApiError, NetworkError):
            return
        self.api, self.via, self.fallback_since, self._proxy_at = api, "fallback", now, now
        log.warning(tr("bot_proxy.log_fallback"))
        util.log_action("Bot: %s" % tr("bot_proxy.log_fallback", "en"))
        self.write_status(force=True)

    def _maybe_back(self):
        """While straight (``fallback``): the proxy again every FALLBACK_RETRY s (at once when the straight way fails
        too); it answers — back to it."""
        if self.via != "fallback":
            return False
        now = self.clock()
        if self._proxy_at is not None and now - self._proxy_at < FALLBACK_RETRY:
            return False
        self._proxy_at = now
        try:
            proxy = socks.parse_proxy(self.cfg.proxy)
        except ValueError:
            return False
        api = self._new_api(self.cfg, proxy)
        try:
            api.get_me(timeout=FALLBACK_PROBE)
        except (ApiError, NetworkError):
            return False
        self.api, self.via, self.fallback_since = api, "proxy", None
        self._px_fails, self._px_since, self._direct_at = 0, None, None
        log.info(tr("bot_proxy.log_back"))
        util.log_action("Bot: %s" % tr("bot_proxy.log_back", "en"))
        self.write_status(force=True)
        return True

    def refresh_config(self):
        """Re-read /etc/kaskad/config when it changed (token, chat, admins, proxy, menu style)."""
        if not bcfg.changed(self.cfg):
            return False
        old, new = self.cfg, bcfg.load()
        self.cfg = new
        if (new.token, new.proxy) != (old.token, old.proxy):
            old_bot = self.api.bot_id
            self.api = self._make_api(new)
            if self.api.bot_id != old_bot:
                self.offset, self.last_poll = self.offsets.load(self.api.bot_id)
        return True

    def start(self):
        self.scheduler.start()
        if self.with_monitor:
            from .monitor import Monitor
            self.monitor = Monitor(self)
            self.monitor.start()
        t = threading.Thread(target=self._startup_tasks, name="kaskad-bot-start")
        t.daemon = True
        t.start()

    def _startup_tasks(self):
        if self.manage_units:
            self._bash_monitor_off()
        if self.my_ip is None:
            self.my_ip = detect_ip(self.runner)
        if not self.cfg.token or self.proxy_error:
            return
        self.commands_wanted = True
        self.sync_commands(force=True)
        try:
            self.api.set_chat_menu_button()
        except (ApiError, NetworkError) as e:
            self.note_error("Telegram (setChatMenuButton): %s" % e)
        try:
            me = self.api.get_me() or {}
            self.username = str(me.get("username") or "")
        except (ApiError, NetworkError) as e:
            self.note_error("Telegram (getMe): %s" % e)

    # ── language: the server's ──
    def lang(self):
        """The language the bot speaks now: KASKAD_LANG of the server (bot.lang.server_lang)."""
        return server_lang(self.cfg)

    def sync_commands(self, force=False):
        """The menu of commands (setMyCommands, default scope) in the server's language — at the start and after
        the language changed (the bot's «Язык / Language», the SSH menu, the panel). False on a failure (retried
        after the next poll)."""
        lang = self.lang()
        if not force and lang == self.commands_lang:
            return True
        try:
            self.api.set_my_commands(commands(lang))
        except (ApiError, NetworkError) as e:
            self.note_error("Telegram (setMyCommands): %s" % e)
            return False
        self.commands_lang = lang
        return True

    def _bash_monitor_off(self):
        """Monitoring lives in this process now (§4.5): the 2.x monitoring daemon must not alert twice."""
        if not self.runner.which("systemctl"):
            return
        if self.runner.run(["systemctl", "is-active", BASH_MONITOR]).out.strip() == "active" or \
                self.runner.run(["systemctl", "is-enabled", BASH_MONITOR]).out.strip() == "enabled":
            self.runner.run(["systemctl", "disable", "--now", BASH_MONITOR], timeout=60)
            util.log_action(tr("bot.log.monitor_moved", unit=BASH_MONITOR))

    # ── messages that delete themselves ──
    def expire_later(self, chat_id, message_id, seconds):
        """Delete the bot's message ``message_id`` in ``seconds`` (checked after every poll, ≤ 30 s late; all of
        them at shutdown — a secret is better gone early than left)."""
        try:
            self.expiring.add(self.clock() + seconds, chat_id, message_id)
        except (OSError, ValueError) as e:
            self.note_error(tr("bot.log.expire_not_saved", why=e))

    def expire_due(self, everything=False):
        try:
            items = self.expiring.take(None if everything else self.clock())
        except (OSError, ValueError) as e:
            self.note_error(tr("bot.log.expire_failed", why=e))
            return 0
        for i, (due, chat, mid) in enumerate(items):
            try:
                self.api.delete_message(chat, mid)
            except ApiError:
                pass                                    # deleted by hand or too old: nothing to do
            except NetworkError as e:
                self.note_error("Telegram (deleteMessage): %s" % e)
                for rest in items[i:]:                  # no network: keep them for the next time
                    self.expiring.add(*rest)
                return i
        return len(items)

    # ── logging and status ──
    def note_error(self, text):
        text = self.api.clean(text)
        self.last_error = text
        last, at = self._logged
        now = time.monotonic()
        if text != last or now - at > 600:
            log.warning(text)
            self._logged = (text, now)

    def _worker_error(self, e):
        log.error(tr("bot.log.thread_error") + " %s", self.api.clean("".join(traceback.format_exception(type(e), e,
                                                                                              e.__traceback__))))

    def safe(self, fn, *args, **kw):
        """Call a Telegram method; a failure is logged, never raised."""
        try:
            return fn(*args, **kw)
        except (ApiError, NetworkError) as e:
            self.note_error("Telegram: %s" % e)
            return None

    def guard(self, ctx, fn, *args):
        """``fn(*args)`` in the server's language; its errors to the chat (KaskadError) or to the journal."""
        with use_lang(self.lang()):
            try:
                fn(*args)
            except KaskadError as e:
                self.safe(ctx.reply, "❌ %s" % h(util.redact(str(e))), menus.back())
            except (ApiError, NetworkError) as e:
                self.note_error("Telegram: %s" % e)
            except Exception as e:                     # noqa: BLE001 — a bug must not kill the worker
                self._worker_error(e)
                self.safe(ctx.send, tr("bot.menu.bot_error"), menus.back())

    def write_status(self, running=True, force=False):
        now = time.monotonic()
        if not force and now - self._status_at < STATUS_EVERY:
            return
        self._status_at = now
        doc = {"v": 1, "pid": os.getpid(), "version": __version__, "started": self.started, "running": running,
               "last_poll": self.last_poll, "last_error": self.last_error, "offset": self.offset,
               "username": self.username, "bound": self.cfg.bound, "proxy": bool(self.cfg.proxy),
               "proxy_error": self.proxy_error, "via": self.via, "fallback_since": self.fallback_since,
               "written": self.clock()}
        try:
            util.ensure_run_dir()                      # 3.2.21 made it 0700 every 30 s: nginx lost the panel (502)
            util.atomic_write(status_path(), json.dumps(doc, ensure_ascii=False, sort_keys=True) + "\n", 0o600)
        except OSError:
            pass

    def save_offset(self):
        try:
            self.offsets.save(self.offset, self.last_poll, self.api.bot_id)
            self._persisted = self.last_poll or 0.0
        except OSError as e:
            self.note_error(tr("bot.log.offset_not_written", why=e))

    # ── receiving ──
    def poll_once(self):
        """One getUpdates, its batch handled, the offset saved. Returns the number of updates, None on failure
        (after waiting the backoff or retry_after)."""
        started = self.clock()
        t0 = time.monotonic()
        self._in_poll = True
        try:
            updates = self.api.get_updates(self.offset, timeout=self.poll_timeout)
        except (ApiError, NetworkError) as e:
            self._in_poll = False
            self._poll_failed(e)
            return None
        finally:
            self._in_poll = False
        quick = time.monotonic() - t0 < QUICK
        gap = None if self.last_poll is None else started - self.last_poll
        backlog = bool(updates) and quick and (gap is None or gap > STALE)
        self.backoff = 0
        self._px_fails, self._px_since = 0, None
        if self.last_error:
            log.info(tr("bot.log.reachable"))
            self.last_error = ""
            self._logged = ("", 0.0)
        if not isinstance(updates, list):
            updates = []
        if updates:
            self.handle_batch(updates, stale_presses=backlog)
        self.expire_due()
        if self.commands_wanted:
            self.sync_commands()                    # the server's language changed: the menu of commands follows
        self.last_poll = self.clock()
        if updates or self.last_poll - self._persisted >= PERSIST_EVERY:
            self.save_offset()
        self.write_status()
        return len(updates)

    def _poll_failed(self, e):
        self._proxy_failed(e)
        if isinstance(e, ApiError) and e.retry_after:
            wait = min(max(1, e.retry_after), 3600)
            self.note_error(tr("bot.log.wait_429", sec=wait))
        else:
            self.backoff = 1 if not self.backoff else min(self.backoff * 2, BACKOFF_MAX)
            wait = self.backoff
            if isinstance(e, ApiError) and e.code == 409:
                self.note_error(tr("bot.log.conflict_409"))
            elif isinstance(e, ApiError) and e.code in (401, 404):
                self.note_error(tr("bot.log.token_refused", code=e.code))
                wait = max(wait, BACKOFF_MAX)
            else:
                self.note_error("getUpdates: %s" % e)
        self.write_status()
        self.sleep(wait)

    def handle_batch(self, updates, stale_presses=False):
        batch = Batch()
        told = set()
        top = None
        for upd in updates:
            if not isinstance(upd, dict):
                continue
            uid = upd.get("update_id")
            if isinstance(uid, int) and not isinstance(uid, bool):
                top = uid if top is None else max(top, uid)
            try:
                self._route(upd, batch, stale_presses, told)
            except (ApiError, NetworkError) as e:
                self.note_error("Telegram: %s" % e)
            except Exception as e:                 # noqa: BLE001 — one strange update must not stop the rest
                self._worker_error(e)
        batch.wait(BATCH_WAIT)
        if top is not None:
            self.offset = max(self.offset, top + 1)

    def access(self, chat_id):
        if not self.cfg.bound:
            return "unbound"
        return "ok" if self.cfg.allowed(chat_id) else "stranger"

    def _refuse(self, chat, access, batch, callback_id=None):
        lang = self.lang()
        if access == "unbound":
            if not self.strangers.due(chat, UNBOUND_WINDOW, kind="u:"):
                return
            text, toast = menus.not_bound(chat, lang), tr("bot.menu.not_bound_toast", lang)
        else:
            if not self.strangers.due(chat):
                return
            text, toast = menus.no_access(chat, lang), tr("bot.menu.no_access_toast", lang)
            util.log_action(tr("bot.log.foreign_chat", lang, chat=chat))
        if callback_id is not None:
            self.safe(self.api.answer_callback, callback_id, toast)
        else:
            self.scheduler.submit(chat, lambda: self.safe(self.api.send_message, chat, text), batch)

    def _stale(self, chat, batch, told, text):
        if chat in told:
            return
        told.add(chat)
        self.scheduler.submit(chat, lambda: self.safe(self.api.send_message, chat, text), batch)

    def _route(self, upd, batch, stale_presses, told):
        cq = upd.get("callback_query")
        if isinstance(cq, dict):
            msg = cq.get("message") if isinstance(cq.get("message"), dict) else {}
            chat = (msg.get("chat") or {}).get("id")
            data, cid = cq.get("data"), cq.get("id")
            if not isinstance(chat, int) or not isinstance(data, str) or not cid:
                if cid:
                    self.safe(self.api.answer_callback, cid)
                return
            acc = self.access(chat)
            if acc != "ok":
                self._refuse(chat, acc, batch, callback_id=cid)
                return
            if stale_presses:
                try:
                    self.api.answer_callback(cid, menus.stale_toast(self.lang()))
                except (ApiError, NetworkError):
                    self._stale(chat, batch, told, menus.stale_button(self.lang()))   # too old even for a toast
                return
            try:
                self.api.answer_callback(cid)
            except ApiError as e:
                if not e.too_old:                              # too old to answer, not to execute (see above)
                    self.note_error("Telegram: %s" % e)
            except NetworkError as e:
                self.note_error("Telegram: %s" % e)
            user = (cq.get("from") or {}).get("id")
            mid = msg.get("message_id")
            self.scheduler.submit(chat, lambda: self.dispatch_callback(chat, user, mid, data), batch)
            return
        msg = upd.get("message")
        if not isinstance(msg, dict):
            return
        chat = (msg.get("chat") or {}).get("id")
        text = msg.get("text")
        if not isinstance(chat, int) or not isinstance(text, str) or not text.strip():
            return
        acc = self.access(chat)
        if acc != "ok":
            self._refuse(chat, acc, batch)
            return
        date = msg.get("date")
        if isinstance(date, (int, float)) and self.clock() - date > STALE:
            self._stale(chat, batch, told, menus.stale_message(self.lang()))
            return
        user = (msg.get("from") or {}).get("id")
        self.scheduler.submit(chat, lambda: self.dispatch_message(chat, user, text), batch)

    # ── which screen a message shows: a late redraw (the check of templates) asks before it draws ──
    def note_edit(self, chat, message_id):
        with self._edits_lock:
            key = (chat, message_id)
            self.edits[key] = self.edits.pop(key, 0) + 1
            if len(self.edits) > EDITS_KEPT:
                for k in list(self.edits)[:EDITS_KEPT // 2]:
                    del self.edits[k]

    def edit_mark(self, chat, message_id):
        with self._edits_lock:
            return self.edits.get((chat, message_id), 0)

    # ── dispatch (in a lane) ──
    def dispatch_callback(self, chat, user, message_id, data):
        new = False
        if data.endswith("_new"):
            data, new = data[:-4], True
        ctx = Context(self, chat, message_id=message_id, user_id=user, new=new)
        fn, dialog = registry.find_callback(data)
        if fn is None:
            with use_lang(self.lang()):
                self.safe(ctx.reply, menus.old_button(), menus.back())
            return
        if not dialog:
            self.dialogs.clear(chat)
        self.guard(ctx, fn, ctx, data)

    def dispatch_message(self, chat, user, text):
        ctx = Context(self, chat, user_id=user)
        t = text.strip()
        if t.startswith("/"):
            cmd = t.split()[0].split("@", 1)[0].lower()
            fn = registry.COMMANDS.get(cmd)
            if fn is not None:
                self.guard(ctx, fn, ctx, t)
                return
            self.safe(ctx.send, tr("bot.menu.start_or_menu", self.lang()))
            return
        fn = registry.REPLIES.get(t)
        if fn is not None:
            self.dialogs.clear(chat)
            ctx.new = True
            self.guard(ctx, fn, ctx, t)
            return
        state, data, expired = self.dialogs.get(chat)
        if state is not None:
            fn = registry.STATES.get(state)
            if fn is not None:
                self.guard(ctx, fn, ctx, t, data)
                return
            self.dialogs.clear(chat)
        with use_lang(self.lang()):
            if expired:
                self.safe(ctx.send, menus.dialog_expired())
                return
            self.safe(ctx.send, tr("bot.menu.start_or_menu"))

    # ── the daemon ──
    def _on_signal(self, signum, frame):
        self.stop_event.set()
        if self._in_poll:
            raise _Interrupt()

    def run(self):
        """``kaskadctl bot run``: until SIGTERM/SIGINT. Returns the exit code."""
        old = [(s, signal.signal(s, self._on_signal)) for s in (signal.SIGTERM, signal.SIGINT)]
        try:
            return self._run()
        finally:
            for s, handler in old:
                signal.signal(s, handler)

    def _run(self):
        util.log_action("Bot daemon started (Python %s, PID %d)" % (__version__, os.getpid()))
        log.info(tr("bot.log.started", version=__version__))
        self.start()
        idle = ""
        try:
            while not self.stop_event.is_set():
                try:
                    self.refresh_config()
                    why = (tr("bot.log.no_token") if not self.cfg.token else self.proxy_error)
                    if why:
                        if why != idle:
                            log.warning(tr("bot.log.waiting", why=why))
                            self.last_error = idle = why
                            self.write_status(force=True)
                        self.sleep(10)
                        continue
                    idle = ""
                    self._maybe_back()
                    self.poll_once()
                except _Interrupt:
                    break
        except _Interrupt:
            pass
        finally:
            self.shutdown()
        return 0

    def shutdown(self):
        self.stop_event.set()
        if self.monitor is not None:
            self.monitor.stop()
        self.scheduler.stop(timeout=5)
        if self.cfg.token and not self.proxy_error:
            self.expire_due(everything=True)
        if self.last_poll is not None:
            self.save_offset()
        self.write_status(running=False, force=True)
        util.log_action("Bot daemon stopped (Python)")


def detect_ip(runner):
    """The node's address for the menu text (2.x showed ``curl ifconfig.me``): a domain for clients as it is, else
    «Адреса сервера» (gw/addresses.py, PLAN-multi-ip Ш1, Л4 — the address for clients when it is one of the server's,
    else the primary one), else the gateway host, else what the core detects (route source address / api.ipify.org).
    "" when unknown."""
    try:
        from ..gw import addresses, model, net
        from .data import state_doc
        st = state_doc()
        host = model.public_host(st)
        if host and not addresses.is_public4(host):
            return host
        try:
            shown = addresses.collect(runner, st, net=False)["show"]
        except Exception:                           # noqa: BLE001 — no ip, a broken cache: the older ways
            shown = ""
        if shown:
            return shown
        if host:
            return host
        found, _ = net.detect_host(runner)
        return found or ""
    except Exception:                               # noqa: BLE001 — only a label
        return ""
