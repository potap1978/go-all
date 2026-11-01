"""Monitoring inside the bot process (docs/k3/SPEC-wave3.md §4.5) — replaces ``gokaskad --monitor-daemon``.

Servers: every ``monitors/<ip>.conf`` (2.x format) is checked every MON_INTERVAL seconds — ICMP (``ping -c1
-W3``), and for a TCP forward to that server a TCP connect to its port. A failure is no answer or a ping
above MON_THRESHOLD. 3 failures in a row -> «⚠️ ALERT» to BOT_CHAT_ID; while it stays down the alert repeats
every MON_COOLDOWN seconds; the first good check after an alert -> «восстановлено». What was said is recorded
only AFTER Telegram accepted the message (2.x wrote ``.last_alert`` first: a lost alert was never repeated);
``.last_alert_<ip>`` keeps the 2.x cooldown file.

Gateway: the events of ``live.json`` (the collector: exit died / came back, inbound down / up, gateway part
restored) are sent once each — by their ``ts``: events of one ts go in one message, the last sent ts is kept
in bot_state/monitor.json; the events that were there when the bot first looked are history, not news.
Stage 1 (docs/k3/SPEC-stage1-exits.md §3.6, §8; T9): the bot sends the events the event layer published — no
gluing of its own (the anti-spam of 3.3.5 is the layer's, one rule for the feed and the bot). The switches
``gateway.notify.push`` of state.json (§8.1) silence a kind in the bot only: the feed of the panel keeps the event
(and the bot does not send it later when the switch is turned on — it is behind). While the server is offline
(``node_offline`` with no ``node_online`` after it) nothing is sent (§8.3); ``node_online`` comes as one message —
«Сервер был без связи с 12:03 до 12:10 (7 мин). За это время: …» — instead of the N events of the outage. A summary
shorter than a minute (``BLIP_S``: one or a few rounds of the collector, «(0 мин)») is not sent; the events held during
such a blip go as usual lines, the feed keeps the summary (REV-3379 П-3).

⚠РФ (docs/k3/SPEC-stage1-rf-mark-builtin.md §5.3, §6; R7): once a minute the bot reads the mode of the consent
(``rfgate.state`` — the state and the kept scan, no process) and says ONE message when «Россия напрямую» enters
``legacy`` (it works as before the update, not confirmed), ``need`` (chosen, not on) or a new organisation appears in
``uncovered`` — the text of §7.4 and the button of the panel sign-in, no buttons of confirmation (only a person in the
panel or the SSH menu confirms). What was said — ``rf_sent`` in monitor.json (the mode, for ``uncovered`` with its
organisations), written only after Telegram took the message; a calm mode (full, none) is remembered too, so a new
entry is news again. Organisations once named stay named while the consent is in force (``full``): the lists drop one
and bring it back, or the full mode calms down between — no repeat, only a really new one is said (REV-s1-r67 П-4). Always sent (no switch), only on a node with the gateway set up.

The health of the node (SPEC-node-score §3.5, §2.1; A6): an event ``host_*`` carries ``args.push`` — the decision of
``hostmon`` (the switch, the alarm of the rule, ``host_level``); the bot sends exactly by it (``sends``), the icon by
``args.level``. Without the gateway the bot itself is the owner of the light health (``host_tick``, bot/hostwatch.py).
"""

import concurrent.futures
import os
import threading
import time

from .. import paths, snapshot, util
from ..bench import donepush, skipday
from . import aliases, data, hostwatch, menus, monitors, store
from .lang import tr, trn, use_lang
from .probe import fmt_ms
from .telegram import ApiError, NetworkError, h, inline

FAILS = 3
TICK = 1.0
GW_EVERY = 10.0
GW_MAX_AGE = 3600
RF_EVERY = 60.0             # s: the mode ⚠РФ changes with a write of the state or with the lists — a minute is enough
GW_ICONS = {"exit_dead": "🔴", "exit_alive": "🟢", "inbound_down": "🔴", "inbound_up": "🟢",
            "gw_part_restored": "🛠",
            # stage 1 (SE §3.6)
            "exit_degraded": "🟡", "exit_recovered": "🟢", "exit_standby": "⏸", "exit_removed": "⛔",
            "exit_failover": "🔀", "exit_rule_push": "🔔", "exit_quota": "📊", "exit_country_changed": "🌍",
            "group_last_step": "🚫", "group_restored": "🟢", "node_offline": "📴", "node_online": "📶",
            "node_ru_offline": "🚧", "node_ru_online": "📶",   # T15 Q5: the Russian addresses (SPEC-groups §10)
            "speedtest_budget": "⏱", "xray_rejected": "⚠️",
            "xray_ru_open": "🚨", "xray_ru_closed": "🔒",       # B7: always sent (no switch), REV-s1-b6-sec Н-2
            "ru_guard": "🛑",                                  # D-136: the last line of «Россия напрямую»
            # the health of the node (SPEC-node-score §3.5, A6): the icon goes by args.level (HOST_ICONS)
            "host_cpu": "⚪", "host_steal": "⚪", "host_io": "⚪", "host_mem": "⚪", "host_disk": "⚪", "host_net": "⚪",
            "host_conntrack": "⚪", "host_clock": "⚪", "host_cert": "⚪", "host_loss": "⚪",
            "bench_done": "📊",                                # B7: a check of the server ended
            "bench_skipped_day": "⏭",                         # the schedule of the checks (SS §2.5 п. 7, B11)
            # 3.3.7.15 F9: the safe application of F7 (the words — gw_pending.event.*, lane Г) and the prefix of F10;
            # no switch of their own: always sent (a revert nobody confirmed is news for the owner)
            "settings_reverted": "↩️", "settings_revert_failed": "❗", "ip6_prefix_changed": "🌐"}
HOST_ICONS = {"warn": "🟡", "crit": "🔴", "ok": "🟢", "na": "⚪"}
GW_UNSTABLE_ICON = "🟠"     # the summary «нестабилен» (an exit state event with ``flaps``, SE §8.2)
GW_KINDS = ("exit_dead", "exit_alive", "inbound_down", "inbound_up", "gw_part_restored",   # texts: bot.mon.gw_<kind>
            "exit_degraded", "exit_recovered", "exit_standby", "exit_removed", "exit_failover", "exit_rule_push",
            "exit_quota", "exit_country_changed", "group_last_step", "group_restored", "node_offline", "node_online",
            "node_ru_offline", "node_ru_online",
            "speedtest_budget", "xray_rejected", "xray_ru_open", "xray_ru_closed", "ru_guard",
            "host_cpu", "host_steal", "host_io", "host_mem", "host_disk", "host_net", "host_conntrack", "host_clock",
            "host_cert", "host_loss", "bench_done", "bench_skipped_day",
            "settings_reverted", "settings_revert_failed", "ip6_prefix_changed")
HOST_KINDS = tuple(k for k in GW_KINDS if k.startswith("host_"))      # = gw.hostmon.HOST_KINDS (a test compares them)
NODE_KINDS = HOST_KINDS + ("bench_done",)      # the lines of the node itself: without the title «🛡 Шлюз» (HostLine)
HOST_EVERY = hostwatch.HOST_EVERY
# ``gateway.notify.push`` of state.json (SE §8.1): a kind -> sent by the bot? A missing switch (a server before stage 1,
# a new kind) — this default. Kinds without a switch (inbound_down/up, gw_part_restored — 3.3.5) are always sent.


def _push_default():
    """The defaults of the switches — ONE source, ``gw.model.NOTIFY_PUSH`` (SE §8.1; REV-s1-t2 З9, T7): the API and the
    bot read the same table; the bot is no source of defaults."""
    from ..gw.model import NOTIFY_PUSH
    return NOTIFY_PUSH


PUSH_OF = {"node_online": "node_offline",       # the kind whose switch rules this one (the summary of an outage)
           "node_ru_online": "node_ru_offline"}
NO_PUSH = ("xray_rejected",                     # the feed of the panel and the journal only (SE §3.6)
           "group_regrouped")                   # F14: «Разложить заново» — the owner pressed it in the panel
NODE_HOLD = GW_MAX_AGE      # s: an outage with no ``node_online`` this long — the bot says what it holds (a lost event)
# T15 Q5 (SPEC-groups §10, SE §8.2): ``node_ru_offline`` is not sent at once — a short outage of the Russian addresses is
# one summary ``node_ru_online``; one still going on after RU_HOLD s is said once while it lasts (the bot has its network:
# the last push must match the state within 15 min), the summary comes when it ends
RU_HOLD = 15 * 60
# REV-3379 П-3: an outage summary (``node_online``, ``node_ru_online``) shorter than BLIP_S s is not sent — one or a few
# failed rounds of the collector (INTERVAL 10 s) would else say «📶 … (0 мин)». The feed keeps it; ``node_ru_offline``
# is not sent at once anyway, and the events held during a ``node_offline`` blip go as usual lines
BLIP_S = 60
# The hold is not let go while the node itself says it is offline (``live.policy.node_offline``, SE §7.9): a long real
# outage waits for its node_online however long it is. The said hold is news at ``off + NODE_HOLD`` (or when the node
# last said it was offline) — older than GW_MAX_AGE (the bot was down) it is not sent, as any old event (3.3.5).


def _store_path():
    return store.path("monitor.json")


class Monitor(object):
    def __init__(self, app, workers=4, sync=False):
        self.app = app
        self.sync = sync
        self._lock = threading.Lock()
        self._state = {}                # ip -> {"fails", "alerted", "last_alert", "last_check", "running", "status"}
        self._pool = None if sync else concurrent.futures.ThreadPoolExecutor(workers)
        self._thread = None
        self._gw_at = 0.0
        self._confs = (None, [])        # (signature of the monitors directory, parsed confs)
        doc = store.read_json(_store_path(), {})
        doc = doc if isinstance(doc, dict) else {}
        alerted = doc.get("alerted") if isinstance(doc.get("alerted"), dict) else {}
        self._alerted = dict((ip, v) for ip, v in alerted.items() if data.valid_ip(ip) and isinstance(v, (int, float)))
        gw = doc.get("gw_ts")
        self._gw_seen = gw if isinstance(gw, (int, float)) and not isinstance(gw, bool) else None
        self._gw_offline_at = 0         # when live.policy last said the node is offline (memory only)
        rf = doc.get("rf_sent")
        self._rf_sent = rf if isinstance(rf, str) else None
        ru = doc.get("ru_told")         # T15 Q5: the ``ts`` of the node_ru_offline said by ``ru_hold_tick``
        self._ru_told = ru if isinstance(ru, (int, float)) and not isinstance(ru, bool) else None
        self._rf_at = 0.0
        self.host = hostwatch.HostWatch(app, self._send, gw_line)
        self._host_at = time.monotonic()    # the light health without the gateway: the first minute after the start
        self._skip_key = None           # skip_tick: (mtime of the queue, the day, the mark) of its last full look
        self._skip_told = set()         # the days of the push of the skips decided by this bot (memory only)
        self._done_key = None           # done_tick: (mtime of the queue, the mark) of its last full look
        self._done_told = set()         # (ts, id) of the checks whose push this bot decided (memory only)

    # ── thread ──
    def start(self):
        self._thread = threading.Thread(target=self._loop, name="kaskad-bot-monitor")
        self._thread.daemon = True
        self._thread.start()

    def stop(self):
        if self._thread is not None:
            self._thread.join(3)
        if self._pool is not None:
            self._pool.shutdown(wait=False)

    def _loop(self):
        while not self.app.stop_event.wait(TICK):
            try:
                with use_lang(self.app.lang()):          # alerts speak the server's language
                    self.tick()
            except Exception as e:                   # noqa: BLE001 — the thread must live on
                self.app._worker_error(e)

    # ── persisted state ──
    def _persist(self):
        with self._lock:
            doc = {"v": 1, "alerted": dict(self._alerted), "gw_ts": self._gw_seen, "rf_sent": self._rf_sent,
                   "ru_told": self._ru_told}
        try:
            store.write_json(_store_path(), doc)
        except OSError as e:
            self.app.note_error(tr("bot.log.monitor_json", why=e))

    def _st(self, ip):
        st = self._state.get(ip)
        if st is None:
            last = max(monitors.last_alert(ip), self._alerted.get(ip, 0))
            st = {"fails": 0, "alerted": ip in self._alerted, "last_alert": last, "last_check": None,
                  "running": False, "status": None}
            self._state[ip] = st
        return st

    def forget(self, ip=None):
        """A monitor was removed or changed: its counters (and its «alerted» mark) go."""
        with self._lock:
            if ip is None:
                self._state.clear()
                self._alerted.clear()
            else:
                self._state.pop(ip, None)
                self._alerted.pop(ip, None)
        self._persist()

    def state_of(self, ip):
        with self._lock:
            st = self._state.get(ip)
            if st is None:
                return "down" if ip in self._alerted else None
            return "down" if st["alerted"] else st["status"]

    # ── servers ──
    def confs(self):
        sig = snapshot.signature(paths.monitors())
        if sig is None:
            return []
        if self._confs[0] != sig:
            self._confs = (sig, monitors.read_all())
        return self._confs[1]

    def tick(self, now=None):
        now = self.app.clock() if now is None else now
        mons = self.confs()
        due = []
        with self._lock:
            live_ips = set(m["ip"] for m in mons)
            for ip in list(self._state):
                if ip not in live_ips:
                    del self._state[ip]
            for m in mons:
                st = self._st(m["ip"])
                if st["running"]:
                    continue
                if st["last_check"] is None or now - st["last_check"] >= m["interval"]:
                    st["last_check"] = now
                    st["running"] = True
                    due.append(m)
        if due:
            _, rows = data.forwards(self.app.runner)
            for m in due:
                port, proto = data.port_for(rows, m["ip"])
                if self.sync:
                    self._check(m, port, proto)
                else:
                    self._pool.submit(self._check, m, port, proto)
        if time.monotonic() - self._gw_at >= GW_EVERY or self.sync:
            self._gw_at = time.monotonic()
            self.gw_tick(now)
        if time.monotonic() - self._rf_at >= RF_EVERY or self.sync:
            self._rf_at = time.monotonic()
            self.rf_tick()
        if time.monotonic() - self._host_at >= HOST_EVERY:
            self._host_at = time.monotonic()
            self.host_tick(now)

    def _check(self, m, port, proto):
        try:
            r = self.app.prober.smart(m["ip"], 3, port, proto)
            with use_lang(self.app.lang()):
                self.judge(m, r, self.app.clock())
        except Exception as e:                       # noqa: BLE001
            self.app._worker_error(e)
        finally:
            with self._lock:
                st = self._state.get(m["ip"])
                if st is not None:
                    st["running"] = False

    def judge(self, m, r, now):
        """One check result of monitor ``m``: count, alert after FAILS, recover."""
        ip = m["ip"]
        ok = r is not None and r[1] <= m["threshold"]
        recover = alert = False
        with self._lock:
            st = self._st(ip)
            if ok:
                st["fails"] = 0
                st["status"] = "up"
                recover = st["alerted"]
            else:
                st["fails"] += 1
                if st["fails"] >= FAILS:
                    st["status"] = "down"
                    alert = now - st["last_alert"] >= m["cooldown"]
        label = menus.ip_short_html(ip, aliases.read_all()) if recover or alert else ""
        if recover:
            text = tr("bot.mon.recovered", server=label, ms=fmt_ms(r[1]), how=h(r[0]))
            if self._send(text):
                with self._lock:
                    st["alerted"] = False
                    self._alerted.pop(ip, None)
                self._persist()
                util.log_action("MONITOR OK: %s ping=%sms" % (ip, fmt_ms(r[1])))
        elif alert:
            fails = st["fails"]
            checks = trn("bot.mon.checks", fails)
            if r is None:
                ping = "TIMEOUT"
                text = tr("bot.mon.alert_timeout", server=label, threshold=m["threshold"], checks=checks)
            else:
                ping = fmt_ms(r[1])
                text = tr("bot.mon.alert_slow", server=label, ms=ping, threshold=m["threshold"], checks=checks)
            if self._send(text):
                with self._lock:
                    st["alerted"] = True
                    st["last_alert"] = now
                    self._alerted[ip] = now
                self._persist()
                monitors.set_last_alert(ip, now)
                util.log_action("ALERT: %s ping=%s threshold=%dms" % (ip, ping if ping == "TIMEOUT" else ping + "ms",
                                                                      m["threshold"]))
        return ok

    def _send(self, text, markup=None):
        chat = self.app.cfg.chat_id
        if not chat or not self.app.cfg.token:
            return False
        try:
            if markup is None:
                self.app.api.send_message(chat, text)
            else:
                self.app.api.send_message(chat, text, markup)
            return True
        except (ApiError, NetworkError) as e:
            self.app.note_error(tr("bot.log.alert_not_sent", why=e))
            return False

    # ── ⚠РФ ──
    def rf_tick(self):
        """One message on entering legacy / need / a new organisation (module docstring); None — nothing to read."""
        sig, text = rf_news(self._rf_sent)
        if sig is None or sig == self._rf_sent:
            return
        if text is not None:
            if not self._send(text, inline([[(tr("bot.gw.btn_panel"), "gwl")]])):
                return                           # not taken — the next round says it again
            util.log_action("BOT rf: %s" % sig)
        self._rf_sent = sig
        self._persist()

    # ── gateway ──
    def gw_tick(self, now):
        live = data.live_doc()
        if "events" not in live:
            return
        evs = []
        for e in live.get("events") or []:
            ts = e.get("ts") if isinstance(e, dict) else None
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                evs.append(e)
        if self._gw_seen is None:                     # the first look: what is there is history
            self._gw_seen = max([e["ts"] for e in evs] + [0])
            self._persist()
            return
        groups = {}
        for e in evs:
            if e["ts"] > self._gw_seen:
                groups.setdefault(e["ts"], []).append(e)
        push = push_switches()
        seen = self._gw_seen
        node_off = node_offline_now(live)
        if node_off:
            self._gw_offline_at = now
        # an outage of the server (SE §8.3): from node_offline to node_online nothing is sent — the events are held and
        # counted, node_online brings them in one message; ``seen`` does not pass the outage before that message went.
        # In one round of the collector node_online comes before the other events of the round; of two node_offline of
        # one outage (the collector restarted while offline) the first one gives «с …» (coordinator, З2)
        off, held, carry = None, [], []
        marks = []                                # the days of bench_skipped_day in the lines not sent yet
        dmarks = []                               # (ts, id) of bench_done of this look: done-push.json after it (П-1)
        for ts in sorted(groups):
            grp = sorted(groups[ts], key=lambda e: e.get("kind") != "node_online")
            if off is None and not any(e.get("kind") == "node_online" for e in grp):
                # a round that lost the network: all of it is the outage
                off = next((e for e in grp if e.get("kind") == "node_offline"), None)
            lines = []
            for e in grp:
                kind = str(e.get("kind") or "")
                if kind == "node_offline":
                    off = off or e
                    continue
                if kind == "node_ru_offline":     # T15 Q5: not at once — node_ru_online, or ``ru_hold_tick``
                    continue
                if kind == "node_online":
                    one = since_of_first(e, off)
                    if not push_on(kind, push):
                        pass
                    elif not blip(one):
                        lines.append(gw_line(one))
                        if held:
                            lines.append(meanwhile_text(held))
                    else:                         # REV-3379 П-3: a blip — no summary, what it held goes as usual
                        lines.extend(HostLine(gw_line(x)) if x.get("kind") in NODE_KINDS else gw_line(x)
                                     for x in held)
                    off, held = None, []
                    continue
                if kind == "node_ru_online" and blip(e):
                    continue                      # REV-3379 П-3: «(0 мин)» is no news; the feed keeps it
                if kind == SKIP_KIND:             # B11 (SS §2.5 п. 7): the mark skip-push.json decides, the bot writes it
                    day = (e.get("args") or {}).get("day") if isinstance(e.get("args"), dict) else None
                    if skipday.decided(day) or day in marks:
                        continue
                    if not push_on(kind, push):
                        _skip_mark(day, False)    # «not to send» is final too
                        continue
                    marks.append(day)
                    lines.append(HostLine("%s %s" % (GW_ICONS[SKIP_KIND], h(skipday.text(
                        day, (e.get("args") or {}).get("n") or skipday.MIN, now)))))
                    continue
                if kind == donepush.KIND:         # П-1: the queue gave it again (a file not removed) — said once
                    a = e.get("args") if isinstance(e.get("args"), dict) else {}
                    ts = a.get("ts")
                    if isinstance(ts, (int, float)) and not isinstance(ts, bool):   # an event before круг 2: no ts
                        one = (int(ts), str(a.get("id") or ""))
                        if donepush.told(*one) or one in dmarks:
                            continue
                        dmarks.append(one)
                if not sends(e, push):            # the switch silences the bot only: the feed keeps the event
                    continue
                if off is not None:
                    held.append(e)
                    continue
                lines.append(HostLine(gw_line(e)) if kind in NODE_KINDS else gw_line(e))
            if off is not None:
                if not self._outage_over(groups, ts, off, now, node_off, self._gw_offline_at):
                    break                         # the outage goes on: the bot waits, seen stays before it
                carry += lines                    # closed later in this look (or given up): sent with that message
                continue
            lines, carry = carry + lines, []
            sent = bool(lines) and now - ts <= GW_MAX_AGE
            if sent and not self._send(gw_message(lines)):
                break
            for day in marks:                     # sent, or too old to be news: decided either way
                _skip_mark(day, sent)
            marks = []
            for one in dmarks:
                _done_mark(*one)
            dmarks = []
            seen = ts
        else:
            if off is not None:                   # no node_online for NODE_HOLD: what was held — in the past tense
                lines = carry
                if push_on("node_offline", push):
                    lines = [held_line(off)] + ([meanwhile_text(held)] if held else []) + carry
                news = max(off.get("ts") or 0, self._gw_offline_at) + NODE_HOLD    # when the hold ran out
                if now - news > GW_MAX_AGE:       # the bot was down: an old hold is not news (3.3.5)
                    lines = []
                if not lines or self._send(gw_message(lines)):
                    for day in marks:             # the skips went with «Сервер был без связи» (or too old): decided
                        _skip_mark(day, bool(lines))
                    for one in dmarks:            # the checks too: said in «За это время» (REV-ns-b7-2 П-6)
                        _done_mark(*one)
                    seen = max(groups)
        if seen != self._gw_seen:
            self._gw_seen = seen
            self._persist()
        self.ru_hold_tick(live, now, push, node_off)

    def ru_hold_tick(self, live, now, push=None, node_off=False):
        """T15 Q5 (SPEC-groups §10): an outage of the Russian addresses still open RU_HOLD s after it began — its
        ``node_ru_offline`` said ONCE (the mark ``ru_told`` in monitor.json); never while the node itself is offline
        (the bot holds everything then), never older news than GW_MAX_AGE (the bot was down), not with its switch off."""
        first = open_ru(live.get("events") if isinstance(live, dict) else None)
        if first is None or node_off:
            return
        ts = first.get("ts")
        since = _since(first)
        if ts == self._ru_told or since is None or now - since < RU_HOLD:
            return
        if now - (since + RU_HOLD) <= GW_MAX_AGE and push_on("node_ru_offline", push_switches() if push is None else push):
            if not self._send(gw_message([gw_line(first)])):
                return                           # not taken — the next look says it again
        self._ru_told = ts
        self._persist()

    # ── the light health of a node without the gateway (SPEC-node-score §3.9) ──
    def host_tick(self, now, mono=None):
        """Once a minute (HOST_EVERY): the bot is the owner of the light health when the collector does not work
        (bot/hostwatch.py) — and then of the push of the skips of a day too (SS §2.5 п. 7, B11). An error is the
        thread's error; the round of the monitor lives on."""
        try:
            with use_lang(self.app.lang()):
                mine = self.host.tick(now, mono)
                if mine:
                    self.skip_tick(now)
                    self.done_tick(now)
                return mine
        except Exception as e:                       # noqa: BLE001
            self.app._worker_error(e)
            return None

    def skip_tick(self, now):
        """Without the gateway: the skips of the queue — «yesterday», then «today»; ≥ 2 and later than the mark —
        the push (the switch on) and the mark (only grows)."""
        from ..bench import history
        if _skip_key(now) == self._skip_key:         # SEC П-4: the queue, the day and the mark as at the last look
            return
        try:
            runs = history.runs(frm=now - 3 * 86400, now=now, by="schedule", status="skipped", limit=500)["runs"]
        except Exception:                            # noqa: BLE001 — no history: nothing to count
            return
        push = push_switches()
        for day, n in skipday.days_to_tell(runs, now):
            if day in self._skip_told:               # REV П-3: decided, the mark not written (a full disk) — not again
                continue
            if not push_on(SKIP_KIND, push):
                _skip_mark(day, False)
                self._skip_told.add(day)
                continue
            line = "%s %s" % (GW_ICONS[SKIP_KIND], h(skipday.text(day, n, now)))
            if not self._send(line):
                return                               # REV П-2: «yesterday» not sent — «today» waits (the mark grows)
            _skip_mark(day, True)
            self._skip_told.add(day)
        self._skip_key = _skip_key(now)

    def done_tick(self, now):
        """Without the gateway (З-4 REV-ns-b7, the same path as ``skip_tick``): the checks of the queue that ended
        within an hour and are later than done-push.json — the push «📊 Проверка сервера …» (the switch on) and the
        mark (only grows; the switch off — decided too)."""
        from ..bench import history
        key = _done_key()
        if key == self._done_key:                    # the queue and the mark as at the last look
            return
        try:
            runs = history.runs(frm=now - donepush.MAX_AGE - 900, now=now, limit=50)["runs"]
        except Exception:                            # noqa: BLE001 — no history: nothing to tell
            return
        push = push_switches()
        for r in donepush.to_tell(runs, now):
            one = (r["ts"], str(r.get("id") or ""))
            if one in self._done_told:               # decided, the mark not written (a full disk) — not again
                continue
            if push_on(donepush.KIND, push):
                a = donepush.args_of(r)
                try:
                    a = donepush.with_rel(a)
                except Exception:                    # noqa: BLE001 — the news of the check without its number
                    pass
                from ..gw.live import bench_args, bench_key
                e = {"kind": donepush.KIND, "key": bench_key(a), "args": bench_args(a)}
                if not self._send(gw_message([HostLine(gw_line(e))])):
                    return                           # not sent — the next look tries again (the mark did not grow)
            _done_mark(*one)
            self._done_told.add(one)
        self._done_key = _done_key()

    @staticmethod
    def _outage_over(groups, ts, off, now, node_off=False, offline_at=0):
        """``off`` (node_offline) is still open after the events of ``ts``: does a later node_online close it in this look,
        or has the bot waited NODE_HOLD for it while the node does not say it is offline (a lost event must not silence
        the bot for good; a long real outage is not a lost event)? The hour counts from the later of ``off`` and the
        last time the node said it is offline (``offline_at``): a collector that clears ``policy.node_offline`` before
        it writes node_online does not get the hold out ahead of its summary (REV-s1-t9-2 п. 1)."""
        for later in groups:
            if later > ts and any(e.get("kind") == "node_online" for e in groups[later]):
                return True
        if node_off:
            return False
        return now - max(off.get("ts") or 0, offline_at or 0) > NODE_HOLD


def rf_news(said=None):
    """(the mark of what is to be said, the text or None) of ⚠РФ now; (None, None) — the state is not read (no news).
    ``said`` — the mark said before: in ``full`` the organisations it named are not news again (module docstring)."""
    from .. import state as kstate
    from ..errors import KaskadError
    from ..gw import model
    from ..rules import rfgate
    try:
        st = kstate.load()
        if not (model.enabled(st) or model.installed_kinds(st)):
            return "nogw", None
        rf = rfgate.state(st)
    except (KaskadError, OSError, ValueError):
        return None, None
    m = rf.get("mode")
    orgs = rf.get("orgs") or []
    if m == "legacy":
        return "legacy", tr("bot.mon.rf_legacy", names=h(", ".join(o["name"] for o in orgs)))
    if m == "need":
        return "need", tr("bot.mon.rf_need")
    unc = sorted(rf.get("uncovered") or [])
    known = set()
    if m == "full" and isinstance(said, str) and said.startswith("uncovered:"):
        known = set(x for x in said[len("uncovered:"):].split(",") if x)
    new = [o for o in unc if o not in known]
    if m == "full" and (unc or known):
        sig = "uncovered:" + ",".join(sorted(known | set(unc)))
        if not new:
            return sig, None                     # the same, fewer or none again — said already
        names = ", ".join(o["name"] for o in orgs if o.get("id") in new) or ", ".join(new)
        return sig, tr("bot.mon.rf_uncovered", names=h(names))
    return str(m), None


def open_ru(events):
    """The first ``node_ru_offline`` with no ``node_ru_online`` after it (the outage of the Russian addresses going on),
    or None."""
    first = None
    for e in events or []:
        if not isinstance(e, dict):
            continue
        if e.get("kind") == "node_ru_online":
            first = None
        elif e.get("kind") == "node_ru_offline" and first is None and isinstance(e.get("ts"), (int, float)):
            first = e
    return first


def node_offline_now(live):
    """``live.policy.node_offline`` (SE §7.9, written by the event layer): True only when the node says so."""
    pol = live.get("policy") if isinstance(live, dict) else None
    return isinstance(pol, dict) and pol.get("node_offline") is True


def _since(e):
    a = e.get("args") if isinstance(e.get("args"), dict) else {}
    v = a.get("since")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        v = e.get("ts")
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def blip(e):
    """An outage summary (``node_online``/``node_ru_online``) shorter than BLIP_S s (REV-3379 П-3): its length from
    ``since``/``until`` (``since_of_first`` may have moved ``since``), else ``minutes`` (0 — a blip); unknown — no."""
    a = e.get("args") if isinstance(e.get("args"), dict) else {}
    since, until = a.get("since"), a.get("until")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)    # noqa: E731
    if num(since) and num(until):
        return until - since < BLIP_S
    m = a.get("minutes")
    return num(m) and m < 1


def since_of_first(online, off):
    """node_online that closes ``off``: «с …» from the first node_offline of the outage (З2) when it is earlier."""
    first = _since(off) if off is not None else None
    a = online.get("args") if isinstance(online.get("args"), dict) else {}
    have = a.get("since")
    if first is None or (isinstance(have, (int, float)) and not isinstance(have, bool) and have <= first):
        return online
    args = dict(a, since=first)
    args.pop("minutes", None)                     # the length again from since/until
    return dict(online, args=args)


SKIP_KIND = "bench_skipped_day"


def _skip_key(now):
    """What the count of the skips without the gateway depends on: the queue (its mtime), the day, the mark."""
    from .. import tz
    from ..bench import spool
    try:
        q = os.stat(spool.spool_dir()).st_mtime_ns
    except OSError:
        q = None
    return q, tz.day(now), skipday.mark(now)


def _done_key():
    """What the push of the checks without the gateway depends on: the queue (its mtime), the mark."""
    from ..bench import spool
    try:
        q = os.stat(spool.spool_dir()).st_mtime_ns
    except OSError:
        q = None
    return q, donepush.mark()


def _done_mark(ts, rid):
    try:
        donepush.write(ts, rid)
    except OSError as e:                             # the next look decides again; one line
        util.log_action("BOT bench done mark: %s" % type(e).__name__)


def _skip_mark(day, sent):
    try:
        skipday.write(day, sent)
    except OSError as e:                             # the next look decides again; one line
        util.log_action("BOT bench skip mark: %s" % type(e).__name__)


def push_switches():
    """``gateway.notify.push`` of state.json (SE §8.1), {} when there is none (a server before stage 1)."""
    st = data.state_doc()
    gw = st.get("gateway") if isinstance(st.get("gateway"), dict) else {}
    notify = gw.get("notify") if isinstance(gw.get("notify"), dict) else {}
    push = notify.get("push")
    return push if isinstance(push, dict) else {}


def push_on(kind, push):
    """Does the bot send an event of ``kind``? Its switch (or the switch it goes by), else the default; kinds without
    a switch — yes; ``xray_rejected`` — never (the feed only)."""
    if kind in NO_PUSH:
        return False
    sw = PUSH_OF.get(kind, kind)
    defaults = _push_default()
    if sw not in defaults:
        return True
    v = push.get(sw)
    return v if isinstance(v, bool) else defaults[sw]


def sends(e, push):
    """Does the bot send this event? A kind ``host_*`` with ``args.push`` — exactly by it (REV-ns-a5 С-1: the hint of
    the probes and a warn under ``host_level = crit`` are ``push: false``); one without it (an older writer) — by its
    switch, as every other kind (``push_on``)."""
    kind = str(e.get("kind") or "")
    a = e.get("args") if isinstance(e.get("args"), dict) else {}
    if kind in HOST_KINDS and "push" in a:
        return a["push"] is True
    return push_on(kind, push)


def gw_icon(e):
    kind = str(e.get("kind") or "")
    if kind in HOST_KINDS:                       # the health of the node: by its level (⚪ — not checked)
        a = e.get("args") if isinstance(e.get("args"), dict) else {}
        return HOST_ICONS.get(a.get("level"), GW_ICONS[kind])
    try:
        from ..gw.live import unstable_summary
    except ImportError:
        unstable_summary = None
    if unstable_summary is not None and unstable_summary(e):   # the same test as the words «нестабилен»
        return GW_UNSTABLE_ICON
    return GW_ICONS.get(kind, "ℹ️")


class HostLine(str):
    """A line of the node's own health (``host_*``): it goes without the title «🛡 Шлюз», as the bot sends it on a
    node without the gateway (bot/hostwatch.py) — one alert looks the same with the gateway and without it
    (REV-ns-a6 П-1)."""


def gw_message(lines):
    """The message of one look: the lines of the gateway under its title, the lines of the node after them (an
    empty line between) — without a title of their own: «Сервер: …» says whose they are."""
    gw = [ln for ln in lines if not isinstance(ln, HostLine)]
    host = [ln for ln in lines if isinstance(ln, HostLine)]
    head = [tr("bot.mon.gw_title")] + gw if gw else []
    return "\n".join(head + ([""] if head and host else []) + host)


def gw_line(e):
    return "%s %s" % (gw_icon(e), h(event_text(e)))


def held_line(off):
    """The outage whose node_online did not come (NODE_HOLD): in the past tense — the bot does not know it is over."""
    try:
        from ..gw.live import held_text
    except ImportError:
        held_text = None
    said = held_text(off) if held_text is not None else ""
    return "%s %s" % (gw_icon(off), h(said or event_text(off)))


def meanwhile_text(held):
    """«За это время: выход не работает — 3, выход снова работает — 2» — the events of an outage, by kind."""
    counts, order = {}, []
    for e in held:
        kind = str(e.get("kind") or "")
        if kind not in counts:
            order.append(kind)
        counts[kind] = counts.get(kind, 0) + 1
    parts = [tr("bot.mon.gw_count", what=tr("bot.mon.gw_" + k) if k in GW_KINDS else k, n=counts[k]) for k in order]
    return h(tr("bot.mon.gw_meanwhile", list=", ".join(parts)))

def event_text(e):
    """The text of a gateway event of live.json in the current language: ``kaskad.gw.live.event_text`` (it retells
    the reason in the reader's language from the event's codes) when the core has it; else the catalog key of the
    event (``key`` + ``args``), its text (the collector's language), the kind's."""
    try:
        from ..gw.live import event_text as core_text
    except ImportError:
        core_text = None
    if core_text is not None:
        said = core_text(e)
        if said:                     # an event without key and text (an old collector): the kind's words below
            return said
    key, args = e.get("key"), e.get("args")
    if isinstance(key, str) and key:
        return tr(key, **(args if isinstance(args, dict) else {}))
    kind = str(e.get("kind") or "")
    if e.get("text"):
        return str(e["text"])
    return tr("bot.mon.gw_" + kind) if kind in GW_KINDS else kind
