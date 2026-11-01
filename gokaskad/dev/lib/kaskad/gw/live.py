"""The live snapshot of the gateway for the web panel and the bot: /run/kaskad/live.json (docs/k3/SPEC-wave3.md §1,
§9.2). Only the collector (kaskad-gw-collect, ``collect.tick`` and the pause between its rounds) writes it,
atomically (temp file + rename), 0600; readers take it through ``read()`` (the shared cache of kaskad.snapshot):

    {"v": 1, "ts": 1791190000.5, "seq": 412,
     "gateway": {"enabled": true, "problems": ["…"], "issues": [{"kind": "gw_inbound", "detail": "…"}],
                 "services": {"dns": true, "xray": null}},
     "inbounds": {"wg": {"enabled": true, "running": true, "sessions": 2, "ports": "51820/udp",
                         "rx": 1234, "tx": 5678, "warnings": [], "why": null}},
     "exits": {"e-41a19b9cbe": {"alive": true, "kind": "xray", "label": "Вена", "state": "ok|dead|unknown",
                               "since": 1791189000, "enabled": true, "detail": "сервер выхода отвечает",
                               "handshake_at": null, "silent_since": null, "fails": 0, "dies_after": 2}},
     "devices": {"d-7ae67ae0": {"online": true, "inbound": "wg", "addr": "10.77.1.10", "since": 1791188000,
                                "rx": 123456, "tx": 654321}},
     "forwards": {"tcp:443": {"packets": 7, "bytes": 700}},
     "activity": {"ts": 1791189900},   (SPEC-activity §5.1: the stamp of the newest window of activity written — the
                                        panel reads GET /api/gw/activity again when it moves; null — nothing written
                                        yet; no ``activity`` — the history is off or no sqlite3)
                  3.3.7.10, the open window of 5 min (kaskad.activity.open_view): "w" — its start, "at" — the last
                  round, "secs" — the seconds the rounds saw, "cur": {"d-…": [on_s, act_s, rx, tx]} of the devices
                  with anything in it; the panel adds it to the summary between the windows written
     "metrics": {"ts": 1791190260},    (3.3.7.10: the minute of metrics.db (m1) written last — the panel reads the line
                                        of «Выходы», GET /api/gw/exits/activity, again when it moves)
                3.3.7.11, the open minute of the exits: "at" — when their counters were read, "cur": {"e-…": [rx, tx]}
                — the bytes since the point ``ts`` (the same counters and rule of deltas as the next point of m1; an exit
                whose counter was not read is not there); the panel adds them to the line and the day of «Выходы»
     "host": {"cpu": 3.1, "mem_total": 2063990784, "mem_used": 431000000, "load": [0.1, 0.2, 0.2], "uptime": 3600,
              "cores": 2, "busy": 2.8, "steal": 0.3, "iowait": 0.1, "softirq": 0.2, "health": {…}},
                                 (A5, SPEC-node-score §3.6: the fields after «uptime» — of the last full round, from
                                 ``hostmon``; the fast rounds change only cpu/mem/load/uptime and keep the rest)
     "events": [{"id": 12, "ts": 1791189999, "kind": "exit_dead", "text": "…", "key": "gw_live.event.exit_dead",
                 "args": {"name": "Вена"}, "exit": "e-…"}],
     "i18n": {"lang": "ru", "problems": [{"key", "args"}], "why": {"wg": {"key", "args"}}}}     (3.3.2, see msg())

Unknown is not zero: what nobody measured is ``null`` («—» on the screen). ``services``: ``null`` — the unit is
not there (DNS) or not needed (xray: no usable exit, no SOCKS5, no VLESS). Inbound ``rx``/``tx`` — byte counters of its
interfaces (/sys/class/net, from the node's side: rx is what the clients sent), for an inbound without interfaces what
its status counts (VLESS: xray's per-user counters), else ``null`` (IKEv2, SOCKS5). Device ``rx``/``tx`` — what its inbound reports for the session (WireGuard/AmneziaWG
``transfer``, accel-ppp, strongSwan, ocserv, OpenVPN), ``null`` for sessions only the hooks know of. Inbound
``warnings`` — what its adapter's status of the round said (``gw status`` shows the same: plain L2TP, an old CA of
IKEv2, acceleration fallen back…); ``why`` — for an enabled inbound that does not run, what systemd says about its
units («служба … завершилась с ошибкой (код 1)»: one ``systemctl show`` per such inbound and round, none while
everything runs), else ``null``. The panel shows them without running anything for a request. Exit
``handshake_at`` — when the leg of an AmneziaWG exit last made a handshake (``null``: never, or not AmneziaWG);
``silent_since`` — the first of the failed probes in a row (``null`` while it answers), ``fails`` of
``dies_after`` — readers say «нет ответа 35 с» and «рукопожатие 40 с назад» by their own clock.

Three kinds of rounds:
  * full — ``collect.tick`` every 10 s: the state, the adapters' status of the round (one call per inbound, shared
    with sessions.json), the hooks' sessions, the exits snapshot of the round, the nft counters the round read
    anyway, ``systemctl is-active`` of the DNS and xray units, /proc and /sys;
  * live — every 2 s between the full ones while a panel watches (the beacon /run/kaskad/live.want is younger
    than 30 s; the panel touches it while it has SSE subscribers): the last full snapshot plus ``<wg|awg> show
    <iface> transfer`` of every running WireGuard-style inbound (one call per interface), interface counters and
    /proc — nothing else runs;
  * light — 3.3.7.11 (the owner 27.10: what «Пользователи» and «Выходы» show moves at least every 5 s while a panel
    watches): in the pause between the full rounds, instead of a live round when the data shown would otherwise be
    older than ``collect.LIGHT_MAX`` s — the status of every inbound (the same one call each as the full round): who
    is online, the open window of activity, and the counters of the exits (/sys, one ``/debug/vars``) — the open
    minute of «Выходы». No probes, no nft, no lock: the state of an exit stays the one of its last check.

Device ``since`` — when the collector saw the device come online, kept while it stays online («в сети с 14:05» of
the panel): adapters give the latest handshake for WireGuard and AmneziaWG (it moves every two minutes), the start
of the session for the others; the first round that sees the device takes that, the next ones keep it until the
device is gone (stand T-G1b, N3). A collector started again takes it over from a file younger than SINCE_KEEP.

``events`` — transitions the collector sees: ``exit_dead`` / ``exit_alive`` (the exits watchdog), ``inbound_down``
/ ``inbound_up`` (an enabled inbound stopped or came back between two full rounds), ``gw_part_restored`` (the
round put the gateway part of the table or its routing back). The last 50 within 24 h; ``id`` — milliseconds of
the clock at the event (+1 within one): it grows across restarts of the collector and reboots, so the panel and
the bot tell new events from old ones by it. The events (and the running states behind inbound_down/up) are
taken over from the file when the collector starts again. 3.3.5 (B7, the coordinator 15.10): only repeats are held
back — one ``exit_dead`` / ``exit_alive`` of a kind per exit in EVENT_QUIET s (15 min); when the window (from the
last published event) ends and the state is not the one the feed says, the state now is told with the drops of the
window («не работает (обрывов за 15 мин: 3)»). The state in «Выходы» / ``gw exits`` is never held back. What the feed
keeps of each exit (the events told, the window, the held back and the drops) is in /run/kaskad/live-feed.json — a
restarted collector goes on with it — and the last event told of each exit also in /etc/kaskad/gw/exits-told.json (a
write per event told): the first round after a start compares it with the state of the exit and tells the state once
when they differ, so neither a restart of the collector nor a reboot leaves the feed and the bot saying the opposite
(REV-335 №1).
"""

import calendar
import glob
import json
import math
import os
import re
import time

from .. import paths, snapshot, util
from ..errors import KaskadError
from ..i18n import fmt_date, fmt_num, get_lang, has, lower_first, tr, trn
from . import exits, hostmon, model

V = 1
FAST = 2.0                  # s between live rounds while a panel watches
WANT_FRESH = 30.0           # s: a beacon younger than this means «a panel watches»
STALE = 30.0                # s: readers take an older snapshot for «the collector does not write» (unknown)
EVENTS_MAX = 500           # stage 1 (SE §3.6): 500 events of 7 days (3.3.5: 50 of 24 h)
EVENTS_AGE = 7 * 24 * 3600
SINCE_KEEP = 180.0          # s: a restarted collector keeps «online since» from a file this fresh (handshake window)
EVENT_QUIET = 15 * 60       # s: one event of a kind per exit at most this often (3.3.5, B7) — the others are counted
EXIT_KINDS = ("exit_dead", "exit_alive")
EXIT_STATE = {"alive": "ok", "dead": "dead", "unknown": "unknown"}
FAILOVER_COALESCE_S = 60    # ``exit_failover`` of one group within a minute — one message, the moved summed (SE §8.2)
GROUP_KEY = "group:"        # the key of a group in the windows of the feed (``group_last_step`` / ``group_restored``)
FEED_V = 2                  # live-feed.json: the windows on the monotonic clock (3.3.5 — v1, the wall clock)
DNS_UNIT = "kaskad-dns.service"
XRAY_UNIT = "kaskad-gw-xray.service"

_now = time.time            # tests patch it
_mono = time.monotonic      # the clock of the windows of the feed (SE §3.6, правка 6) — tests patch it too


def __getattr__(name):
    """NO_POOL_TEXT — in the language of the reader (a module constant would freeze the language of the import)."""
    if name == "NO_POOL_TEXT":
        return tr("gw_live.no_pool")
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


# ── texts in two languages ─────────────────────────────────────────────────
# The collector writes the texts of live.json in its language (the server's) and keeps how to say them again:
# events — ``key`` + ``args`` (plain values: the bot renders ``tr(key, **args)``, kaskad.bot.monitor.event_text);
# the problems of the gateway and the ``why`` of the inbounds — ``i18n`` {"lang", "problems": [msg], "why": {kind:
# msg}}, a msg is {"key", "args"} whose args may be msgs too. ``read()`` gives a reader in another language (the
# language was just switched) the same document with these texts in its own.

def msg(key, **args):
    return {"key": key, "args": args}


def render(m, lang=None):
    """The text of a msg ({"key", "args"}; args may be msgs) in ``lang`` (None: the language of the caller)."""
    if not isinstance(m, dict) or not isinstance(m.get("key"), str):
        return "" if m is None else str(m)
    if m["key"].startswith("gw_host."):
        return hostmon.say(m, lang)                 # the health of the node (A5): its numbers, times and forms
    args = m.get("args") if isinstance(m.get("args"), dict) else {}
    kw = dict((k, render(v, lang) if isinstance(v, dict) else v) for k, v in args.items() if k != "detail_rec")
    rec = args.get("detail_rec")
    if isinstance(rec, dict):
        # the detail of an exit: its code and arguments (gw/exits.py), said again in ``lang``; ``detail`` beside it —
        # the same words in the language of the collector, for readers that only call tr(key, **args)
        from ..i18n import use_lang
        with use_lang(lang):
            kw["detail"] = exits.detail_text(rec) or kw.get("detail") or ""
    return tr(m["key"], lang, **kw) if kw else tr(m["key"], lang)


BENCH_KINDS = ("bench_done",)       # B7: the check of the server (bench.spool.drain → collect → here); its switch


def _rel(a):
    x = a.get("rel")
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def bench_key(a):
    """The words of ``bench_done`` by its args (NS-D34: by the status of the check, never by a verdict): the failure
    with its reason, a partial check, the end with «N от обычного» (``rel``, NS-D31) or without a number."""
    if a.get("status") == "failed":
        return "gw_live.event.bench_failed"
    if a.get("status") == "partial":
        return "gw_live.event.bench_partial"
    w = _worse(a)
    if w is not None:                                # a confirmed ▼ after the check (the design r1, «Сверка 24.10» п. 6)
        return "gw_live.event.bench_worse" if w["test"] else "gw_live.event.bench_worse_bare"
    if _rel(a) is not None:
        return "gw_live.event.bench_done"
    return "gw_live.event.bench_done_bare"


def _worse(a):
    """``args.worse`` of bench_done checked again (it comes back from live.json): a link of score.SPEC, a test of it
    with both numbers, or the link alone; anything else — None (the news without it)."""
    from ..bench import donepush
    from .. import score as S
    w = a.get("worse")
    if not isinstance(w, dict) or w.get("link") not in donepush.LINKS:
        return None
    t, now, usual = w.get("test"), donepush._fin(w.get("now")), donepush._fin(w.get("usual"))
    if t not in S.tests_of(S.SPEC) or not has("score.unit." + str(t)) or now is None or usual is None:
        return {"link": w["link"], "test": None}
    return {"link": w["link"], "test": t, "now": now, "usual": usual}


def _num_unit(v, test, lang=None):
    d = 0 if abs(v) >= 100 else 1 if abs(v) >= 1 else 2
    return fmt_num(v, lang, digits=d) + " " + tr("score.unit." + test, lang)


def bench_args(a, lang=None):
    """The args of ``bench_done`` for the feed: the codes as they are and the words as msgs (the reader's language);
    ``worse`` — the link as a msg and its numbers with the unit in ``lang``."""
    out = dict((k, a.get(k)) for k in ("ts", "id", "mode", "by", "status", "why"))
    rel = _rel(a)
    out["rel"] = None if rel is None else int(round(rel))
    if out["mode"] in ("quick", "full"):
        out["mode_text"] = {"key": "host_bench.mode_in." + out["mode"]}
    if out["why"]:
        out["why_text"] = {"key": "bench_run.why." + str(out["why"])}
    w = _worse(a)
    if w is not None:
        from ..bench import donepush
        out["worse"] = w
        group = w["link"] in donepush.GROUPS and w["test"] and has("host_sched.test." + w["test"])
        out["link"] = {"key": "host_sched.test." + w["test"] if group else "score.link." + w["link"]}   # «Задержка»
        if w["test"]:
            out["now"], out["usual"] = _num_unit(w["now"], w["test"], lang), _num_unit(w["usual"], w["test"], lang)
    return out


def event_text(ev, lang=None):
    """The text of an event of live.json in ``lang``: its ``key``/``args`` (3.3.2), else its ``text``. Stage 1 (SE §3.6,
    §8; T9): the new kinds are told from their kind and codes (``_said_s1``: the variant by ``why``/``step``/``back``,
    the metric with its unit, times on the server's clock — the language and the time zone of the reader, never the
    collector's words), a summary «нестабилен» by ``flaps``; an exit of a group other than ``main`` gets the group in
    front («Стриминг»: выход … ). A 3.3.5 event (``exit_dead``/``exit_alive`` of ``main``, no ``flaps``) — its key, as
    before. A kind this version does not know with a key its catalog has not (an event of a newer version after a
    rollback: REV-3379, ``node_ru_*`` on 3.3.7.8) — the ``text`` its writer stored, never the raw key."""
    if isinstance(ev, dict) and isinstance(ev.get("key"), str):
        args = ev.get("args") if isinstance(ev.get("args"), dict) else {}
        kind = str(ev.get("kind") or "")
        if kind in hostmon.HOST_KINDS:
            return hostmon.event_text(ev, lang)       # A5: «Сервер: …» — no exit, no group
        if kind == "bench_done":
            # by today's rules from the stored codes, not the stored key: an event of before the cut (dev-3374:
            # bench_barely*, {total}/{band}) — «закончена» without a number, never a raw key or «{rel}» (REV-score-cut З-2)
            said = render({"key": bench_key(args), "args": bench_args(args, lang)}, lang)
        else:
            said = _said_s1(kind, args, lang)
            if said is None:
                if not has(ev["key"]) and str(ev.get("text") or "").strip():
                    return str(ev["text"])            # the writer's words (its language): a newer version's event
                said = render({"key": ev["key"], "args": args}, lang)
        return _in_group(kind, args, said, lang)
    return str((ev or {}).get("text") or "")


# ── the events of stage 1 (SE §3.6, §8.1–8.3; T9) ─────────────────────────
# What the event layer (T4) puts in ``args`` — plain values; a name may be a msg ({"key", "args"}: a default name
# such as «Основная» / «Уровень 2», said in the reader's language):
#   exits — ``id``, ``name``, ``group``, ``group_name``, ``role`` (SE §3.6); ``flaps`` — the summary «нестабилен»;
#   exit_degraded / exit_rule_push — ``rule`` (+ ``rule_name``), ``metric``, ``value``, ``threshold``, ``unit`` (the
#     unit of the condition, §3.3; none — the metric's usual one), ``service`` for ``metric = service``;
#   exit_recovered — ``from`` (degraded|standby|removed|off), ``after_s``;
#   exit_standby — ``why`` (rule:<id>|manual|sub_confirm|unstable; + ``rule_name``, ``sub``, ``sub_name``, ``flaps``,
#     ``until``); exit_removed — ``why`` (dead|quota|country|maintenance|rule:<id>|disabled);
#   exit_failover — ``from_tier``, ``to_tier`` (ids of the levels), ``from_label``/``to_label`` (the owner's names of
#     the levels), ``from_names``/``to_names`` (their exits by name, T15 Q5), ``moved``, ``back``;
#   exit_quota — ``used_gb``, ``limit_gb``, ``pct``, ``reset`` (YYYY-MM-DD); exit_country_changed — ``old``, ``new``,
#     ``all_exits``; speedtest_budget — ``period`` (day|month);
#   group_last_step — ``group``, ``name`` (of the group), ``step`` (block|direct), ``devices``; ``direct`` — devices
#     whose own last step is «напрямую» in a group that blocks (SE §8.1: both lines in one push); ``fallen`` — names
#     of the group's exits down within the last 60 s; group_restored — ``group``, ``name``, ``exit`` (its name),
#     ``after_s``;
#   xray_rejected — ``error`` (+ ``stopped``: the closing stopped xray); xray_ru_open / xray_ru_closed — ``n`` (paths; B7: a closing did not stop xray / it is
#   closed; ``why`` of closed: stopped | restarted | allowed); ru_guard — ``n``, ``where`` (xray | nft; D-136);
#   node_offline — ``since``; node_online — ``since``, ``until``, ``minutes``, ``restart``
#   (REV-s1-t4 З1: the collector was restarted while offline and found the network there — ``until`` is its last round
#   before the restart, the real end is then or later).
# Order (coordinator, З2): in one round of the collector ``node_online`` is written before the other events of that
# round (they are news after the outage, not «за это время»); two ``node_offline`` of one outage (the collector
# restarted while offline) — ``node_online.since`` is the ``since`` of the first one (the bot takes the first too).
# ``live.policy.node_offline`` (§7.9) is true while the node is offline: the bot does not give up waiting for
# ``node_online`` while it is.
EXIT_STATE_KINDS = ("exit_dead", "exit_alive", "exit_degraded", "exit_recovered", "exit_standby", "exit_removed")
GROUP_PREFIX_KINDS = EXIT_STATE_KINDS + ("exit_failover", "exit_rule_push", "exit_quota", "exit_country_changed",
                                         "speedtest_budget")
SAID_KINDS = EXIT_STATE_KINDS + ("group_last_step", "group_restored")   # the kinds the windows of the feed know
# К2-6 (T-KT2): the last step «напрямую» is other news than the block — its own key of the antispam: a block told
# minutes ago (then «снова через…») never holds back «весь трафик идёт напрямую» (a policy changed to «напрямую»)
DIRECT_STEP = "group_last_step_direct"
TOLD_KINDS = SAID_KINDS + (DIRECT_STEP,)
# UI-2 круг 3 (REV-ui2-2 С2): все виды событий ленты (``events``) — у каждого тон в панели (EV_LOOK в app.js: тревога и сбой —
# danger, предупреждение — warning, восстановление — success); новый вид — сюда и в EV_LOOK (tests/test_ui2_layout.py
# сверяет и этот список с теми, кто пишет события: _event здесь, collect.py, health.py, model.NOTIFY_PUSH)
FEED_KINDS = EXIT_STATE_KINDS + ("exit_failover", "exit_rule_push", "exit_quota", "exit_country_changed", "speedtest_budget",
                                 "group_last_step", "group_restored", "inbound_down", "inbound_up", "gw_part_restored",
                                 "xray_rejected", "xray_ru_open", "xray_ru_closed", "ru_guard", "node_offline",
                                 "node_online", "node_ru_offline", "node_ru_online") + hostmon.HOST_KINDS + BENCH_KINDS + \
    ("bench_skipped_day",) + ("group_regrouped",) + \
    ("settings_reverted", "settings_revert_failed")   # F14 (the feed only, the bot is silent); F7 (pending.EVENT_KINDS)
# T15 Q5 (SPEC-groups §10): the outage of the Russian addresses — its start and its summary; the start closed by the
# summary is no line of its own in the feed (as node_offline, ``feed_events``)
OUTAGE_PAIRS = {"node_offline": "node_online", "node_ru_offline": "node_ru_online"}


def told_kind(kind, args):
    """The key of the antispam of an event: its kind; the last step of a group «напрямую» — its own."""
    if kind == "group_last_step" and (args or {}).get("step") == "direct":
        return DIRECT_STEP
    return kind
_STATE_NOW = {"exit_dead": "dead", "exit_alive": "ok", "exit_recovered": "ok", "exit_degraded": "degraded",
              "exit_standby": "standby", "exit_removed": "removed"}
_UNIT_OF = {"rtt": "ms", "loss": "pct", "jitter": "ms", "speed": "mbps", "traffic": "gb_day"}
_TZ_FALLBACK = 3 * 3600     # s: UTC+03:00 — what kaskad.tz says when it cannot read the zone (SE §3.7)


def _txt(v, lang):
    """A name of the args: a msg (a default name) said in ``lang``, else the value as text."""
    if isinstance(v, dict):
        return render(v, lang)
    return "" if v is None else str(v)


def _count(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return int(v)


def _num(v, lang):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return _txt(v, lang)
    return fmt_num(v, lang, digits=0 if float(v).is_integer() else 1)


def _clock(ts):
    """HH:MM of ``ts`` on the server's clock (KASKAD_TZ, kaskad.tz — SE §3.7); without it — UTC+03:00."""
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return ""
    try:
        from .. import tz as ktz
        t = ktz.local(ts)
    except (ImportError, AttributeError, TypeError, ValueError, OSError, OverflowError):
        try:
            t = time.gmtime(ts + _TZ_FALLBACK)
        except (ValueError, OSError, OverflowError):
            return ""
    return "%02d:%02d" % (t.tm_hour, t.tm_min)


def _dur(seconds, lang):
    """«меньше минуты» / «12 мин» / «2 ч 5 мин»."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
        return ""
    m = int(round(seconds / 60.0))
    if m < 1:
        return tr("gw_live.dur.under_min", lang)
    if m < 60:
        return tr("gw_live.dur.min", lang, n=m)
    h, m = divmod(m, 60)
    return tr("gw_live.dur.h_min", lang, h=h, n=m) if m else tr("gw_live.dur.h", lang, h=h)


def _day(v, lang):
    """The date of ``reset`` (YYYY-MM-DD) in ``lang``: «01.11» / «Nov 1»."""
    m = re.match(r"^(\d{4})-(\d\d)-(\d\d)$", str(v or ""))
    if not m:
        return _txt(v, lang)
    try:
        import datetime
        return fmt_date(datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))), lang, short=True)
    except ValueError:
        return str(v)


def _names(items, lang):
    """«X» и «Y» / “X” and “Y”."""
    said = [tr("gw_live.quoted", lang, name=_txt(x, lang)) for x in items]
    if len(said) < 2:
        return "".join(said)
    return tr("gw_live.and", lang, head=", ".join(said[:-1]), last=said[-1])


def _rule(a, lang):
    rid = a.get("rule")
    if not rid and str(a.get("why") or "").startswith("rule:"):
        rid = str(a["why"])[5:]
    return _txt(a.get("rule_name"), lang) or _txt(rid, lang)


def _metric(a, lang):
    """What the condition measured: «отклик 350 мс (порог 300 мс)»."""
    metric = str(a.get("metric") or "")
    val, thr = _num(a.get("value"), lang), _num(a.get("threshold"), lang)
    if metric == "service":
        key = "gw_live.metric.service_up" if a.get("value") is True else "gw_live.metric.service_down"
        return tr(key, lang, service=_txt(a.get("service"), lang) or "—")
    if metric == "country" and thr:              # the condition «Страна не та»: country != <the expected one>
        return tr("gw_live.metric.country_expected", lang, value=val, threshold=thr)
    if metric in ("country", "country_changed"):
        return tr("gw_live.metric." + metric, lang, value=val, threshold=thr)
    unit = str(a.get("unit") or _UNIT_OF.get(metric, ""))
    key = "gw_live.metric.%s_%s" % (metric, unit)
    if metric and has(key, lang):
        return tr(key, lang, value=val, threshold=thr)
    return tr("gw_live.metric.other", lang, value=val, threshold=thr)    # no internal code of the metric to a person


def _group_label(a, lang):
    name = _txt(a.get("name"), lang) or _txt(a.get("group_name"), lang)
    if not name and a.get("group") in (None, "", "main"):
        name = tr("gw_live.group.main", lang)
    return name or _txt(a.get("group"), lang)


def _in_group(kind, a, said, lang):
    """An exit of a group other than ``main``: «Стриминг»: выход «X» … (one group — «Как раньше» — keeps 3.3.5 words)."""
    group = a.get("group")
    if kind not in GROUP_PREFIX_KINDS or not group or group == "main" or not said:
        return said
    if kind == "exit_standby" and a.get("why") == "sub_confirm":     # the group is in its words
        return said
    if a.get("role") == "ru" and not unstable_summary({"kind": kind, "args": a}) and _said_ru(kind, a, lang) is not None:
        return said                                                   # T15 Q5: «… (группа «Дом»)» — in its words
    name = _txt(a.get("group_name"), lang) or _txt(group, lang)
    return tr("gw_live.event.in_group", lang, group=name, text=lower_first(said))


def unstable_summary(ev):
    """Is the event said as the summary «нестабилен» (SE §8.2)? An exit state event (not standby) with a number
    ``flaps``. The bot gives it its own icon by the same test."""
    ev = ev if isinstance(ev, dict) else {}
    a = ev.get("args") if isinstance(ev.get("args"), dict) else {}
    kind = str(ev.get("kind") or "")
    return _count(a.get("flaps")) is not None and kind in EXIT_STATE_KINDS and kind != "exit_standby"


def held_text(ev, lang=None):
    """A ``node_offline`` whose ``node_online`` did not come in time (the bot's hold, SE §8.3): in the past tense —
    the reader may read it when the network is back."""
    a = (ev or {}).get("args") if isinstance((ev or {}).get("args"), dict) else {}
    since = _clock(a.get("since") if _count(a.get("since")) is not None else (ev or {}).get("ts"))
    return tr("gw_live.event.node_offline_held" + ("" if since else "_now"), lang, since=since)


def _said_s1(kind, a, lang):
    """The words of an event of stage 1 from its kind and args, or None (said by its key, as in 3.3.5)."""
    name = _txt(a.get("name"), lang)
    flaps = _count(a.get("flaps"))
    if unstable_summary({"kind": kind, "args": a}):
        # the summary «нестабилен» (SE §8.2, ``flap_summary``): the kind is the state now; without flap_summary the
        # event layer gives no ``flaps`` and the 3.3.5 words with the count stay (``_n``)
        return tr("gw_live.event.unstable", lang, name=name, flaps=trn("gw_live.n.drops", flaps, lang),
                  state=tr("gw_live.state_now." + _STATE_NOW[kind], lang))
    if a.get("role") == "ru":
        said = _said_ru(kind, a, lang)
        if said is not None:
            return said
    if kind == "exit_degraded":
        return tr("gw_live.event.exit_degraded", lang, name=name, what=_metric(a, lang))
    if kind == "exit_rule_push":
        return tr("gw_live.event.exit_rule_push", lang, name=name, rule=_rule(a, lang), what=_metric(a, lang))
    if kind == "exit_recovered":
        was = str(a.get("from") or "")
        was = "removed" if was == "off" else was
        if was not in ("degraded", "standby", "removed"):
            return tr("gw_live.event.exit_recovered", lang, name=name)
        after = _dur(a.get("after_s"), lang)
        key = "gw_live.event.exit_recovered_after" if after else "gw_live.event.exit_recovered_was"
        return tr(key, lang, name=name, was=tr("gw_live.was." + was, lang), after=after)
    if kind == "exit_standby":
        why = str(a.get("why") or "")
        if why.startswith("rule:"):
            return tr("gw_live.event.exit_standby_rule", lang, name=name, rule=_rule(a, lang))
        if why == "unstable":
            until = _clock(a.get("until"))
            key = "gw_live.event.exit_standby_unstable" + ("" if until else "_now")
            return tr(key, lang, name=name, flaps=trn("gw_live.n.drops", flaps or 0, lang), until=until)
        if why == "sub_confirm":
            sub = _txt(a.get("sub_name"), lang) or _txt(a.get("sub"), lang)
            group = _txt(a.get("group_name"), lang) or (_txt(a.get("group"), lang)
                                                        if a.get("group") not in (None, "", "main") else "")
            key = "gw_live.event.exit_standby_sub_confirm" + ("_group" if group else "")
            return tr(key, lang, name=name, sub=sub or "—", group=group)
        if why == "manual":
            return tr("gw_live.event.exit_standby_manual", lang, name=name)
        return tr("gw_live.event.exit_standby", lang, name=name)
    if kind == "exit_removed":
        why = str(a.get("why") or "")
        if why.startswith("rule:"):
            return tr("gw_live.event.exit_removed_rule", lang, name=name, rule=_rule(a, lang))
        if why == "dead":
            return tr("gw_live.event.exit_dead", lang, name=name)
        if why in ("quota", "country", "maintenance", "disabled"):
            return tr("gw_live.event.exit_removed_" + why, lang, name=name)
        return tr("gw_live.event.exit_removed", lang, name=name)
    if kind == "exit_failover":
        moved = _count(a.get("moved")) or 0
        to, frm = _txt(a.get("to_tier"), lang), _txt(a.get("from_tier"), lang)
        if a.get("back") is True:
            if not moved:                         # «(0 устройств)» says nothing
                return tr("gw_live.event.exit_failover_back_none", lang, to=to)
            return tr("gw_live.event.exit_failover_back", lang, to=to, devices=trn("gw_live.n.devices", moved, lang))
        key = "gw_live.event.exit_failover" + ("" if frm else "_to")
        return tr(key, lang, to=to, **{"from": frm, "devices": trn("gw_live.n.devices_moved", moved, lang)})
    if kind == "exit_quota":
        pct = _count(a.get("pct"))
        reset = _day(a.get("reset"), lang) if a.get("reset") else ""
        key = "gw_live.event.exit_quota_full" if pct is not None and pct >= 100 else "gw_live.event.exit_quota"
        return tr(key, lang, name=name, pct=_num(pct, lang) if pct is not None else "—",
                  used=_num(a.get("used_gb"), lang), limit=_num(a.get("limit_gb"), lang),
                  reset=tr("gw_live.event.quota_reset", lang, day=reset) if reset else "")
    if kind == "exit_country_changed":
        key = "gw_live.event.exit_country_changed" + ("_all" if a.get("all_exits") is True else "")
        return tr(key, lang, name=name, old=_txt(a.get("old"), lang) or "—", new=_txt(a.get("new"), lang) or "—")
    if kind == "speedtest_budget":
        period = "month" if a.get("period") == "month" else "day"
        return tr("gw_live.event.speedtest_budget_" + period, lang, name=name)
    if kind == "group_last_step":
        group = _group_label(a, lang)
        n = _count(a.get("devices")) or 0
        direct = _count(a.get("direct")) or 0
        if a.get("role") == "ru":
            # T-q Н-text: a Russian group carries the Russian sites only — the foreign ones go on by their own group
            key = "gw_live.event.group_last_step_ru_direct" if a.get("step") == "direct" else \
                "gw_live.event.group_last_step_ru"
            text = tr(key, lang, name=group, devices=trn("gw_live.n.devices_ru", n, lang))
        elif a.get("step") == "direct":
            text = tr("gw_live.event.group_last_step_direct", lang, name=group,
                      devices=trn("gw_live.n.devices_direct", n, lang))
        else:
            text = tr("gw_live.event.group_last_step", lang, name=group, devices=trn("gw_live.n.devices_of", n, lang))
            if direct:
                text += tr("gw_live.event.group_also_direct", lang,
                           devices=trn("gw_live.n.devices_direct", direct, lang))
        fallen = [x for x in a.get("fallen") or [] if x] if isinstance(a.get("fallen"), list) else []
        if fallen:
            text += trn("gw_live.event.group_fallen", len(fallen), lang, list=_names(fallen, lang))
        return text
    if kind == "group_restored":
        group = _group_label(a, lang)
        exit_name = _txt(a.get("exit_name"), lang) or _txt(a.get("exit"), lang)
        after = _dur(a.get("after_s"), lang)
        key = "gw_live.event.group_restored" + ("_via" if exit_name else "")
        text = tr(key, lang, name=group, exit=exit_name)
        return text + (tr("gw_live.event.after", lang, after=after) if after else "")
    if kind in ("settings_reverted", "settings_revert_failed"):    # F7: the timer of the safe apply
        from . import pending
        return pending.event_text(kind, a, lang)
    if kind == "group_regrouped":              # F14 «Разложить заново»: the result of the round that did it
        group = _group_label(a, lang)
        if a.get("late"):
            return tr("gw_live.event.group_regrouped_late", lang, name=group)
        if a.get("frozen"):
            return tr("gw_live.event.group_regrouped_frozen" + ("_ru" if a.get("frozen") == "ru_offline" else ""),
                      lang, name=group)
        n = _count(a.get("moved")) or 0
        if not n:
            return tr("gw_live.event.group_regrouped_none", lang, name=group)
        return tr("gw_live.event.group_regrouped", lang, name=group, devices=trn("gw_live.n.devices_moved", n, lang))
    if kind == "xray_rejected":
        return tr("gw_live.event.xray_rejected" + ("_stopped" if a.get("stopped") else ""), lang,
                  error=_txt(a.get("error"), lang) or "—")
    if kind == "xray_ru_closed":               # REV-s1-b7 №2: stopped by the closing — SOCKS5 and VLESS are down
        why = a.get("why")                     # REV-s1-b7-2 №2: «allowed» — the choice or the consent came back
        return tr("gw_live.event.xray_ru_closed" + ("_" + why if why in ("stopped", "allowed") else ""), lang)
    if kind == "ru_guard":                     # D-136: the last line dropped a direct path
        return tr("gw_live.event.ru_guard_" + ("nft" if a.get("where") == "nft" else "xray"), lang,
                  n=_count(a.get("n")) or 0)
    if kind == "node_offline":
        since = _clock(a.get("since"))
        return tr("gw_live.event.node_offline" + ("" if since else "_now"), lang, since=since)
    if kind == "node_ru_offline":              # T15 Q5 (SPEC-groups §10, SE §4.6): the Russian addresses
        since = _clock(a.get("since"))
        return tr("gw_live.event.node_ru_offline" + ("" if since else "_now"), lang, since=since)
    if kind == "node_ru_online":
        since, until = _clock(a.get("since")), _clock(a.get("until"))
        minutes = _count(a.get("minutes"))
        if minutes is None and _count(a.get("since")) is not None and _count(a.get("until")) is not None:
            minutes = max(0, int(round((a["until"] - a["since"]) / 60.0)))
        if not since or not until:
            return tr("gw_live.event.node_ru_online_back", lang)
        return tr("gw_live.event.node_ru_online" + ("_restart" if a.get("restart") else ""), lang, since=since,
                  until=until, minutes=_dur((minutes or 0) * 60, lang))
    if kind == "node_online":
        since, until = _clock(a.get("since")), _clock(a.get("until"))
        minutes = _count(a.get("minutes"))
        if minutes is None and _count(a.get("since")) is not None and _count(a.get("until")) is not None:
            minutes = max(0, int(round((a["until"] - a["since"]) / 60.0)))
        if not since or not until:
            return tr("gw_live.event.node_online_back", lang)
        return tr("gw_live.event.node_online" + ("_restart" if a.get("restart") else ""), lang, since=since,
                  until=until, minutes=_dur((minutes or 0) * 60, lang))
    return None


def _said_ru(kind, a, lang):
    """T15 Q5 (SPEC-groups §10): the words of an event of a RUSSIAN exit or group that name it so — «Российский выход
    «Роутер» (группа «Дом») недоступен», ««Дом»: российские сайты переключились …» (only the Russian sites go another
    way, the foreign ones keep their group); None — the common words (``_in_group`` puts the group in front)."""
    name = _txt(a.get("name"), lang)
    group = _txt(a.get("group_name"), lang) or _txt(a.get("group"), lang)
    if not group:
        return None                            # a Russian exit outside the groups: no traffic, the common words
    n = _count(a.get("n"))
    tail = tr("gw_live.event.drops_n", lang, n=n) if n else ""
    if kind == "exit_dead" or (kind == "exit_removed" and a.get("why") == "dead"):
        why = bool(a.get("detail") or isinstance(a.get("detail_rec"), dict))
        key = "gw_live.event.exit_dead_ru" + ("_why" if why else "")
        args = dict((k, v) for k, v in a.items() if k in ("detail", "detail_rec"))
        return render({"key": key, "args": dict(args, name=name, group=group)}, lang) + tail
    if kind == "exit_alive":
        return tr("gw_live.event.exit_alive_ru", lang, name=name, group=group) + tail
    if kind == "exit_failover":
        moved = _count(a.get("moved")) or 0
        to, frm = _tier_word(a, "to_", lang), _tier_word(a, "from_", lang)
        if a.get("back") is True:
            if not moved:
                return tr("gw_live.event.exit_failover_ru_back_none", lang, group=group, to=to)
            return tr("gw_live.event.exit_failover_ru_back", lang, group=group, to=to,
                      devices=trn("gw_live.n.devices", moved, lang))
        key = "gw_live.event.exit_failover_ru" + ("" if frm else "_to")
        return tr(key, lang, group=group, to=to, **{"from": frm, "devices": trn("gw_live.n.devices_moved", moved, lang)})
    if kind == "exit_country_changed" and a.get("all_exits") is not True:
        return tr("gw_live.event.exit_country_changed_ru", lang, name=name, group=group,
                  old=_txt(a.get("old"), lang) or "—", new=_txt(a.get("new"), lang) or "—")
    return None


def _tier_word(a, side, lang):
    """A level of a Russian group in a push (T15 Q5): the owner's name of the level, else its keys by name (in
    ``ru_home`` one key a level), else the old args (an event of an earlier version: the value as it was)."""
    label = a.get(side + "label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    got = [n for n in a.get(side + "names") or [] if isinstance(n, str) and n]
    if got:
        return ", ".join(got[:3]) + (", …" if len(got) > 3 else "")
    return _txt(a.get(side + "tier"), lang)


_loc = [None, None, None]   # [source doc, lang, localized copy]: the last one (readers share it, never change it)


def localize(doc, lang=None):
    """``doc`` (live.json) with its texts in ``lang`` (None: the language of the caller): the same object when it is
    in that language already (or has nothing to say again: a collector before 3.3.2), else a copy (cached)."""
    if not isinstance(doc, dict):
        return doc
    lang = lang or get_lang()
    loc = doc.get("i18n") if isinstance(doc.get("i18n"), dict) else None
    if loc is None:
        return doc
    if _loc[0] is doc and _loc[1] == lang:
        return _loc[2]
    # events — always by their key: the collector keeps them from round to round, and one told before the language
    # of the server changed keeps its words (живая проверка 3.3.3, C: «lang» of the file says nothing about them)
    evs = doc.get("events") or []
    said = [dict(e, text=event_text(e, lang)) if isinstance(e, dict) and e.get("key") else e for e in evs]
    if loc.get("lang") == lang:
        out = doc if said == evs else dict(doc, events=said)
        _loc[0], _loc[1], _loc[2] = doc, lang, out
        return out
    out = dict(doc)
    out["events"] = said
    gw = dict(doc.get("gateway") or {})
    msgs = loc.get("problems") if isinstance(loc.get("problems"), list) else []
    probs, issues = list(gw.get("problems") or []), [dict(x) if isinstance(x, dict) else x
                                                     for x in gw.get("issues") or []]
    for i, m in enumerate(msgs):
        if m is None:
            continue
        text = render(m, lang)
        if i < len(probs):
            probs[i] = text
        if i < len(issues) and isinstance(issues[i], dict):
            issues[i]["detail"] = text
    gw.update(problems=probs, issues=issues)
    out["gateway"] = gw
    whys = loc.get("why") if isinstance(loc.get("why"), dict) else {}
    inb = {}
    for kind, rec in (doc.get("inbounds") or {}).items():
        if isinstance(rec, dict) and whys.get(kind) and rec.get("why"):
            rec = dict(rec, why=_why_text(whys[kind], lang))
        inb[kind] = rec
    out["inbounds"] = inb
    host = doc.get("host")
    if isinstance(host, dict) and isinstance(host.get("health"), dict):
        out["host"] = dict(host, health=hostmon.health_in(host["health"], lang))     # A5, §3.6
    out["i18n"] = dict(loc, lang=lang)
    _loc[0], _loc[1], _loc[2] = doc, lang, out
    return out
_ISO = re.compile(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?(?:\.\d+)?Z$")


# ── readers ────────────────────────────────────────────────────────────────

def read(now=None):
    """(snapshot or None, age in seconds or None) through the shared read cache. A missing, broken or foreign
    file gives (None, None). The value is shared: never change it."""
    doc = snapshot.read_json(paths.live_json())
    if not isinstance(doc, dict) or doc.get("v") != V:
        return None, None
    doc = localize(doc)                             # the texts in the language of the reader
    ts = doc.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return doc, None
    now = _now() if now is None else now
    return doc, max(0.0, now - float(ts))


def feed_events(events):
    """The events of the feed for people (Н9, T-KT1; SE §8.3 «после возврата — одно сообщение»): a ``node_offline`` that
    a later ``node_online`` closed is not a line of its own — the summary «Сервер был без связи с … до …» says it. The
    file keeps both (the bot holds the events of the outage by them, a restarted collector finds the open one); an
    outage going on stays in the feed."""
    out, open_ = [], dict((k, []) for k in OUTAGE_PAIRS)
    ends = dict((v, k) for k, v in OUTAGE_PAIRS.items())
    for e in events or []:
        kind = e.get("kind") if isinstance(e, dict) else None
        if kind in open_:
            open_[kind].append(len(out))
        elif kind in ends and open_[ends[kind]]:          # T15 Q5: node_ru_online closes node_ru_offline the same way
            gone = set(open_[ends[kind]])
            out = [x for i, x in enumerate(out) if i not in gone]
            open_ = dict((k, [i - len([j for j in gone if j < i]) for i in v]) for k, v in open_.items())
            open_[ends[kind]] = []
        out.append(e)
    return out


def outage_open():
    """The outage of the network of the node the previous collector process left open in live.json (REV-s1-t4 З1: it
    was restarted while offline): {"since": its first ``node_offline``, "last": the ``ts`` of that file — its last
    round}, or None (no file, no open ``node_offline``). Read raw: no cache, no localizing."""
    try:
        with open(paths.live_json(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("v") != V:
        return None
    first = None
    for e in doc.get("events") or []:
        if not isinstance(e, dict):
            continue
        if e.get("kind") == "node_online":
            first = None
        elif e.get("kind") == "node_offline" and first is None:
            first = e
    off = bool((doc.get("policy") or {}).get("node_offline")) if isinstance(doc.get("policy"), dict) else False
    if first is None and not off:
        return None
    last = _int(doc.get("ts"))
    since = (_int(((first or {}).get("args") or {}).get("since")) or _int((first or {}).get("ts"))) if first else None
    since = since or last
    if not since:
        return None
    return {"since": since, "last": max(since, last or since)}


def ru_outage_open():
    """T15 Q3 (SE §4.6): the outage of the Russian addresses the previous collector process left open in live.json —
    {"since": its first ``node_ru_offline``, "last": the ``ts`` of that file}, or None. Read raw, as ``outage_open``."""
    try:
        with open(paths.live_json(), encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("v") != V:
        return None
    first = _open_of(doc.get("events"), "node_ru_offline")
    if first is None:
        return None
    last = _int(doc.get("ts"))
    since = _int((first.get("args") or {}).get("since")) or _int(first.get("ts")) or last
    if not since:
        return None
    return {"since": since, "last": max(since, last or since)}


def _open_of(events, kind):
    """The first ``kind`` (an outage's start, OUTAGE_PAIRS) with no end after it in ``events``, or None."""
    first = None
    for e in events or []:
        if not isinstance(e, dict):
            continue
        if e.get("kind") == OUTAGE_PAIRS[kind]:
            first = None
        elif e.get("kind") == kind and first is None:
            first = e
    return first


def fresh(age):
    return age is not None and age <= STALE


def want_fresh(now=None):
    """A panel watches: /run/kaskad/live.want is younger than WANT_FRESH."""
    try:
        m = os.stat(paths.live_want()).st_mtime
    except OSError:
        return False
    now = _now() if now is None else now
    return now - m <= WANT_FRESH


def touch_want():
    """The panel's beacon: «2 s rounds, please» (it calls this every 10 s while it has subscribers)."""
    p = paths.live_want()
    util.ensure_run_dir(os.path.dirname(p))         # /run/kaskad: 0711, never 0700 (nginx → panel socket)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    os.utime(p, None)


# ── small parsers ──────────────────────────────────────────────────────────

def _int(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    s = str(v).strip()
    return int(s) if s.isdigit() else None


def epoch(v):
    """Epoch seconds (int) of what adapters and hooks give as «since»: a number, digits, ISO UTC; else None."""
    if isinstance(v, bool) or v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v) if v > 0 else None
    s = str(v).strip()
    if re.match(r"^\d+(\.\d+)?$", s):
        n = int(float(s))
        return n if n > 0 else None
    m = _ISO.match(s)
    if not m:
        return None
    try:
        return calendar.timegm(tuple(int(x or 0) for x in m.groups()) + (0, 0, 0))
    except (ValueError, OverflowError):
        return None


def _read(path):
    try:
        with open(path, encoding="ascii", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# ── the node: /proc, /sys ──────────────────────────────────────────────────

def uptime():
    """Seconds since the node booted (/proc/uptime), None when unknown."""
    f = (_read(paths.P("/proc/uptime")) or "").split()
    try:
        return float(f[0]) if f else None
    except ValueError:
        return None


class Host(object):
    """CPU (% of the whole node since the previous sample), memory, load, uptime — /proc only, no commands."""

    def __init__(self):
        self._prev = None

    @staticmethod
    def cpu_times():
        text = _read(paths.P("/proc/stat")) or ""
        for line in text.splitlines():
            f = line.split()
            if f and f[0] == "cpu" and len(f) >= 5:
                try:
                    vals = [int(x) for x in f[1:9]]
                except ValueError:
                    return None
                total = sum(vals)
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                return total - idle, total
        return None

    def sample(self):
        out = {"cpu": None, "mem_total": None, "mem_used": None, "load": None, "uptime": None}
        cur = self.cpu_times()
        if cur is not None and self._prev is not None and cur[1] > self._prev[1]:
            busy = max(0, cur[0] - self._prev[0])
            out["cpu"] = round(100.0 * busy / (cur[1] - self._prev[1]), 1)
        if cur is not None:
            self._prev = cur
        mem = {}
        for line in (_read(paths.P("/proc/meminfo")) or "").splitlines():
            k, _, v = line.partition(":")
            f = v.split()
            if f and f[0].isdigit():
                mem[k.strip()] = int(f[0]) * 1024
        if "MemTotal" in mem:
            out["mem_total"] = mem["MemTotal"]
            avail = mem.get("MemAvailable")
            if avail is None and "MemFree" in mem:
                avail = mem["MemFree"] + mem.get("Buffers", 0) + mem.get("Cached", 0)
            if avail is not None:
                out["mem_used"] = max(0, mem["MemTotal"] - avail)
        f = (_read(paths.P("/proc/loadavg")) or "").split()
        try:
            out["load"] = [float(x) for x in f[:3]] if len(f) >= 3 else None
        except ValueError:
            out["load"] = None
        up = uptime()
        out["uptime"] = int(up) if up is not None else None
        return out


def iface_bytes(patterns):
    """(rx, tx) summed over the interfaces matching ``patterns`` (``kgw-wg``, ``kgw-l2tp*``) — /sys, no
    commands; (None, None) when none of them exists."""
    rx = tx = None
    seen = set()
    base = paths.P("/sys/class/net")
    for pat in patterns or []:
        if not re.match(r"^[A-Za-z0-9_.-]{1,15}\*?$", str(pat)):
            continue
        for d in sorted(glob.glob(os.path.join(base, pat))):
            name = os.path.basename(d)
            if name in seen:
                continue
            seen.add(name)
            r = _int((_read(os.path.join(d, "statistics", "rx_bytes")) or "").strip())
            t = _int((_read(os.path.join(d, "statistics", "tx_bytes")) or "").strip())
            if r is None or t is None:
                continue
            rx, tx = (rx or 0) + r, (tx or 0) + t
    return rx, tx


def _ifaces(ad):
    try:
        return list(ad.ifaces() or []) if ad is not None else []
    except Exception:                               # noqa: BLE001 — an adapter of another work package
        return []


def _wg_style(ad):
    """A WireGuard-style adapter (wg, awg1–3): one interface, ``<tool> show <iface> transfer``, peers.json."""
    return ad is not None and isinstance(getattr(ad, "iface", None), str) \
        and callable(getattr(ad, "tool", None)) and callable(getattr(ad, "peers_path", None))


def transfer(runner, ad):
    """{public key: (rx, tx)} of the interface of a WireGuard-style adapter (one command), None on failure."""
    try:
        tool = ad.tool(runner)
    except Exception:                               # noqa: BLE001
        return None
    res = runner.run([tool, "show", ad.iface, "transfer"], timeout=5)
    if not res.ok:
        return None
    out = {}
    for line in res.out.splitlines():
        f = line.split()
        if len(f) >= 3 and f[1].isdigit() and f[2].isdigit():
            out[f[0]] = (int(f[1]), int(f[2]))
    return out


def _peers(ad):
    """public key -> device id of a WireGuard-style adapter (its peers.json; the shared read cache)."""
    try:
        idx = snapshot.read_json(ad.peers_path(), {})
    except Exception:                               # noqa: BLE001
        return {}
    if not isinstance(idx, dict):
        return {}
    return dict((k, (v or {}).get("device")) for k, v in idx.items() if isinstance(v, dict))


def _unit_active(runner, unit):
    res = runner.run(["systemctl", "is-active", unit])
    return res.out.strip() == "active"


def _unit_file(unit):
    return os.path.exists(os.path.join(paths.systemd_dir(), unit))


_UNIT_NAME = re.compile(r"^[A-Za-z0-9@._:-]{1,128}$")
_RESULTS = ("exit-code", "signal", "core-dump", "timeout", "start-limit-hit", "resources", "watchdog", "oom-kill",
            "exec-condition")                       # Result= of systemd with a text of their own (gw_live.result.*)
WHY_MAX = 200


def _why_text(m, lang=None):
    return util.redact(render(m, lang))[:WHY_MAX]


def down_reason(runner, units):
    """Why an inbound that should run does not, for people — ONE ``systemctl show`` of its units (the collector
    asks only about enabled inbounds whose status says «not running»): «служба kaskad-gw-wg.service завершилась с
    ошибкой (код 1)», «службы … нет на узле»; "" when systemd has nothing to add (or the call failed)."""
    m = down_reason_msg(runner, units)
    return _why_text(m) if m else ""


def down_reason_msg(runner, units):
    """``down_reason`` as a msg ({"key", "args"}), None when there is nothing to say."""
    names = [u for u in units or [] if isinstance(u, str) and _UNIT_NAME.match(u)]
    if not names:
        return None
    res = runner.run(["systemctl", "show", "-p", "Id", "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                      "-p", "Result", "-p", "ExecMainStatus"] + names, timeout=5)
    if not res.ok:
        return None
    for block in (res.out or "").split("\n\n"):
        p = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if not p or p.get("ActiveState") == "active":
            continue
        name = p.get("Id") or "?"
        if p.get("LoadState") == "not-found":
            return msg("gw_live.why.not_found", unit=name)
        result, code = p.get("Result") or "", p.get("ExecMainStatus") or ""
        if result in ("", "success"):
            state = msg("gw_live.why.starting" if p.get("ActiveState") == "activating" else "gw_live.why.stopped")
        else:
            state = msg("gw_live.result." + result.replace("-", "_")) if result in _RESULTS else \
                msg("gw_live.result.other", result=result)
            if result == "exit-code" and code.isdigit() and code != "0":
                state = msg("gw_live.why.with_code", state=state, code=code)
            if p.get("SubState") == "auto-restart":
                state = msg("gw_live.why.restarting", state=state)
        return msg("gw_live.why.unit", unit=name, state=state)
    return None


def _units_of(ad, status):
    units = (status or {}).get("units")
    if isinstance(units, list):
        return units
    try:
        return list(ad.units() or []) if ad is not None and callable(getattr(ad, "units", None)) else []
    except Exception:                               # noqa: BLE001 — an adapter of another work package
        return []


# ── the snapshot ───────────────────────────────────────────────────────────

def sessions_of(st, stats, hook_sessions):
    """Sessions merged as ``collect.sessions`` merges them (the adapters first, then hook records nobody
    reported), with ``rx``/``tx`` None where nobody measured them."""
    out, seen = [], set()
    for kind in model.enabled_kinds(st):
        s = stats.get(kind)
        for x in (s or {}).get("sessions") or []:
            if not isinstance(x, dict):
                continue
            rec = {"kind": kind, "device": x.get("device"), "ip": x.get("ip"), "since": epoch(x.get("since")),
                   "rx": _int(x.get("rx")), "tx": _int(x.get("tx"))}
            out.append(rec)
            seen.add((kind, rec["ip"]))
    for x in hook_sessions or []:
        if (x.get("kind"), x.get("ip")) not in seen:
            out.append({"kind": x.get("kind"), "device": x.get("device"), "ip": x.get("ip"),
                        "since": epoch(x.get("since")), "rx": None, "tx": None})
    return out


def devices_of(st, sess):
    """{device id: {"online", "inbound", "addr", "since", "rx", "tx"}} of every device of the state."""
    by = {}
    for s in sess:
        if s.get("device"):
            by.setdefault(s["device"], []).append(s)
    out = {}
    for d in model.devices(st):
        did = d.get("id")
        if not did:
            continue
        mine = by.get(did) or []
        if not mine:
            out[did] = {"online": False, "inbound": None, "addr": None, "since": None, "rx": None, "tx": None}
            continue
        last = max(mine, key=lambda s: s.get("since") or 0)
        rx = [s["rx"] for s in mine if s.get("rx") is not None]
        tx = [s["tx"] for s in mine if s.get("tx") is not None]
        out[did] = {"online": True, "inbound": last.get("kind"), "addr": last.get("ip"), "since": last.get("since"),
                    "rx": sum(rx) if rx else None, "tx": sum(tx) if tx else None}
    return out


def forwards_of(st, counters):
    """{"tcp:443": {"packets", "bytes"}} of every forward; None values without a read of the table."""
    from .. import nft
    out = {}
    for f in st.get("forwards") or []:
        c = (counters or {}).get(nft.counter_name(f["proto"], f["port"])) if counters is not None else None
        out["%s:%d" % (f["proto"], int(f["port"]))] = {
            "packets": _int((c or {}).get("packets")) if c is not None else None,
            "bytes": _int((c or {}).get("bytes")) if c is not None else None}
    return out


class Live(object):
    """What the collector keeps between rounds: the last full snapshot, the sessions of the round, the CPU
    sample, the running states of the inbounds (transitions), the events."""

    def __init__(self):
        self.seq = 0
        self.fed = 0                # rounds whose events are in the feed (``publish`` done): the spool of F7 goes then
        self.events = []
        self.next_id = 1
        self.running = {}
        self.online = {}            # device id -> when this collector saw it come online (kept while it stays)
        # 3.3.5 (B7, the coordinator 15.10): the feed of an exit — kept in /run (feed_path: a restarted collector goes
        # on with it) and «said» on the disk too (told_path); the first round compares «said» with the state (REV-335 №1)
        self.told = {}              # (exit id, kind) -> ts of its last published event of that kind
        self.said = {}              # exit id -> (ts, kind) of its last published event: what the feed says of it now
        self.window = {}            # exit id -> start of its window (the last published event, or the last check)
        self.held = {}              # exit id -> transitions held back in the window
        self.drops = {}             # exit id -> [ts] of its «не работает» transitions (told or not), 2 × EVENT_QUIET s
        self._settle = True         # the first round with a known pool: «said» against the state
        self._feed_text = None      # what feed_path() holds (written when it changes)
        self._told_dirty = False    # «said» changed: told_path() is written at the end of the round
        self.said_why = {}          # key -> ``why`` of its last published event (a manual state: «Вернуть» goes at once)
        self.last_args = {}         # key -> (kind, args) of the last change held back (told when the window ends)
        self.failover = {}          # (group, back) -> {"at", "args"}: exit_failover being summed (FAILOVER_COALESCE_S)
        self.published = []         # events published since ``take_published`` (metrics.db.events)
        self._texts = {}            # event id -> (lang, text): told once an event, again only in another language
        self.host = Host()
        self.doc = None
        self.st = None
        self.sessions = []
        self._loaded = False
        self.activity_ts = None     # the stamp of the newest window of activity written (SPEC-activity §5.1; collect)
        self.activity_open = None   # the open window of activity (kaskad.activity.open_view; collect), 3.3.7.10
        self.metrics_ts = None      # the minute of m1 written last (collect._minute), 3.3.7.10
        self.metrics_open = None    # {"at", "cur": {eid: [rx, tx]}} — the bytes of the exits since it (collect), 3.3.7.11

    # ── the previous file (a restart of the collector keeps events, ids, transitions, «online since») ──
    def _load(self, now=None):
        if self._loaded:
            return
        self._loaded = True
        self._load_feed()
        try:
            with open(paths.live_json(), encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError):
            return
        if not isinstance(doc, dict) or doc.get("v") != V:
            return
        self.seq = _int(doc.get("seq")) or 0
        evs = [e for e in doc.get("events") or [] if isinstance(e, dict) and _int(e.get("id"))]
        self.events = evs
        self.next_id = max([_int(e.get("id")) for e in evs] + [0]) + 1
        for e in evs:                       # an exit no feed file knows (an update from 3.3.4): its last event told
            ts = _ts(e.get("ts"))
            if e.get("kind") in EXIT_KINDS and isinstance(e.get("exit"), str) and ts is not None:
                if ts >= (self.said.get(e["exit"]) or (0, None))[0]:
                    self.said[e["exit"]] = (ts, e["kind"])
        for kind, rec in (doc.get("inbounds") or {}).items():
            if isinstance(rec, dict) and isinstance(rec.get("running"), bool) and rec.get("enabled"):
                self.running[kind] = rec["running"]
        ts = doc.get("ts")
        now = _now() if now is None else now
        if isinstance(ts, (int, float)) and not isinstance(ts, bool) and 0 <= now - ts <= SINCE_KEEP:
            for did, rec in (doc.get("devices") or {}).items():       # a short restart: still the same session
                since = rec.get("since") if isinstance(rec, dict) and rec.get("online") else None
                if isinstance(since, (int, float)) and not isinstance(since, bool) and 0 < since <= now:
                    self.online[did] = int(since)

    def _load_feed(self):
        """The feed of the exits from /run (a restart of the collector), «said» also from the disk (a reboot). A broken
        feed file (not JSON, «exits» not a map — of any kind, an empty list or null too: REV-335-4 №1) goes aside with a
        line in the journal, the feed starts anew — the events of live.json and «said» on the disk are read all the same
        (REV-335-3 №6). No file, an empty one or no «exits» at all — no feed yet, nothing broken."""
        raw = util.read_text(feed_path())
        self._feed_text = None
        try:
            doc = json.loads(raw or "{}")
            feed = doc.get("exits", {}) if isinstance(doc, dict) else None
            if not isinstance(feed, dict):
                raise ValueError("not a feed")
            self._feed_of(feed, mono=doc.get("v") == FEED_V)
        except (ValueError, TypeError, AttributeError, KeyError):
            for d in (self.told, self.said, self.window, self.held, self.drops):
                d.clear()
            self._feed_aside()
        try:
            disk = json.loads(util.read_text(told_path()) or "{}")
        except ValueError:
            disk = {}
        for eid, v in (disk.items() if isinstance(disk, dict) else ()):
            s = _said_of(v)
            if s and s[0] >= (self.said.get(eid) or (0, None))[0]:
                self.said[eid] = s

    def _feed_aside(self):
        path = feed_path()
        aside = path + ".bad"
        try:
            os.replace(path, aside)
        except OSError:
            return
        try:
            util.log_tr("gw_live.log.feed_bad", path=path, aside=aside)
        except Exception:                           # noqa: BLE001 — the journal never stops the collector
            pass

    def _feed_of(self, exits_doc, mono=True):
        """The feed of the keys from /run. ``mono`` False — a file of 3.3.5 (its windows on the wall clock): only «said»
        is taken, the windows start anew."""
        for eid, r in exits_doc.items():
            if not isinstance(r, dict):
                continue
            if _said_of(r.get("said")):
                self.said[eid] = _said_of(r.get("said"))
            if isinstance(r.get("why"), str):
                self.said_why[eid] = r["why"]
            if not mono:
                continue
            for kind, ts in (r.get("told") if isinstance(r.get("told"), dict) else {}).items():
                if kind in TOLD_KINDS and _num0(ts) is not None:
                    self.told[(eid, kind)] = ts
            if _num0(r.get("window")) is not None:
                self.window[eid] = r["window"]
            if _int(r.get("held")):
                self.held[eid] = _int(r["held"])
            drops = [x for x in r.get("drops") or [] if _num0(x) is not None] if isinstance(r.get("drops"), list) else []
            if drops:
                self.drops[eid] = drops

    def _save_feed(self):
        """/run/kaskad/live-feed.json when it changed; /etc/kaskad/gw/exits-told.json when «said» changed. Never fails."""
        out = {}
        for (eid, kind), ts in self.told.items():
            out.setdefault(eid, {}).setdefault("told", {})[kind] = ts
        for eid, (ts, kind) in self.said.items():
            out.setdefault(eid, {})["said"] = [ts, kind]
        for eid, why in self.said_why.items():
            if why:
                out.setdefault(eid, {})["why"] = why
        for eid, ts in self.window.items():
            out.setdefault(eid, {})["window"] = ts
        for eid, n in self.held.items():
            out.setdefault(eid, {})["held"] = n
        for eid, ds in self.drops.items():
            if ds:
                out.setdefault(eid, {})["drops"] = list(ds)
        text = util.json_text({"v": FEED_V, "exits": out})
        if text != self._feed_text:
            try:
                util.atomic_write(feed_path(), text, 0o600)
                self._feed_text = text
            except OSError:
                pass
        if self._told_dirty:
            try:
                util.atomic_write(told_path(), util.json_text(dict((eid, [ts, kind])
                                                                   for eid, (ts, kind) in self.said.items())), 0o600)
                self._told_dirty = False
            except OSError:
                pass

    def _since(self, devs, now, remember=True):
        """«Online since» of ``devs`` (``devices_of``) in place: the moment this collector first saw each device online,
        kept while it stays online — not the latest handshake of WireGuard, which moves every two minutes. The first
        round takes the session's own start (or the handshake, at most ``ACTIVE_SECONDS`` old) — else now. With
        ``remember`` (a full round) the devices that are gone are forgotten: back online, they start again."""
        keep = {}
        for did, d in devs.items():
            if not d.get("online"):
                continue
            start = self.online.get(did)
            if start is None:
                s = d.get("since")
                start = int(s) if isinstance(s, (int, float)) and not isinstance(s, bool) and 0 < s <= now \
                    else int(now)
            keep[did] = start
            d["since"] = start
        if remember:
            self.online = keep
        return devs

    def _event(self, kind, key, args, now, **extra):
        # ids are milliseconds of the clock (and +1 within one): they grow across restarts of the collector and
        # across reboots (/run is empty again), so readers never take a new event for an old one. ``text`` in the
        # language of the collector (readers before 3.3.2) — ``event_text`` (T9: the words of stage 1 from the kind and
        # its codes); ``key``/``args`` — the same in any language
        self.next_id = max(self.next_id, int(now * 1000))
        ev = {"id": self.next_id, "ts": int(now), "kind": kind, "text": "", "key": key, "args": args}
        ev.update(extra)
        ev["text"] = event_text(ev)
        self.next_id += 1
        self.events.append(ev)
        self.published.append(ev)
        return ev

    def take_published(self):
        """The events published since the last call (the collector writes them to metrics.db.events, §3.6)."""
        out, self.published = self.published, []
        return out

    # ── the layer of the events of the exits and groups (3.3.5 + stage 1, SE §3.6, §8.2) ──
    # One rule for the feed and the bot: a REPEAT of a kind within EVENT_QUIET s of the last one told of that key (an
    # exit, or «group:<gid>») is held back and counted; the first event of another kind goes at once; when the window
    # (EVENT_QUIET s from the last one told) ends with something held back, the state now is told when it is not the one
    # told last («не работает (обрывов за 15 мин: 3)»); with ``flap_summary`` — «нестабилен» by the falls of health even
    # when it is. Manual actions of the owner go at once. The windows are on the monotonic clock (``_mono``).

    def _key_kind_ok(self, key, kind, m):
        last = self.told.get((key, kind))
        return last is None or not 0 <= m - last < EVENT_QUIET

    def _exit_event(self, eid, kind, names, snap, now, args=None, manual=False):
        """A change of an exit for the feed and the bot: a repeat of its kind within EVENT_QUIET s is held back
        (counted), the first of another kind is told at once; ``manual`` (an action of the owner) — at once always."""
        m = _m(now)
        if kind == "exit_dead":
            self.drops[eid] = [x for x in self.drops.get(eid) or [] if 0 <= m - x <= 2 * EVENT_QUIET] + [m]
        if not manual and not self._key_kind_ok(eid, kind, m):
            self.held[eid] = self.held.get(eid, 0) + 1
            self.window.setdefault(eid, m)
            if args is not None:
                self.last_args[eid] = (kind, args)
            return None
        return self._publish(eid, kind, names, snap, now, args=args)

    def _publish(self, eid, kind, names, snap, now, forced=False, args=None, flaps=None):
        """Tell ``kind`` of ``eid`` now. ``forced`` — not a change of this round: the state told when the window ended
        or after a start (the feed said the opposite). ``args`` — of the event (stage 1); ``flaps`` — the summary
        «нестабилен»."""
        m = _m(now)
        # the drops of the last 15 min, told or not (REV-335 №6); when the window ends — of the whole window, the drop
        # that opened it too (it is EVENT_QUIET s old by then)
        start = m - EVENT_QUIET
        if forced and self.window.get(eid) is not None:
            start = min(start, self.window[eid])
        n = len([x for x in self.drops.get(eid) or [] if start <= x <= m])
        self.held.pop(eid, None)
        self.last_args.pop(eid, None)
        if eid.startswith(GROUP_KEY):
            a = dict(args or {})
            ev = self._event(kind, "gw_live.event." + kind, a, now)
        else:
            name = names.get(eid, eid)
            srec = (snap.get("exits") or {}).get(eid) or {}
            drec = dict((k, srec[k]) for k in exits.DETAIL_KEYS if k in srec)   # its code: told again in any language
            detail = exits.detail_text(drec) if drec and kind == "exit_dead" else ""
            a = dict(args or {})
            a["name"] = name
            tkey = "gw_live.event." + kind
            if detail:
                a.update(detail=detail, detail_rec=drec)
                tkey = "gw_live.event.exit_dead_why"
            if flaps is not None:
                a["flaps"] = flaps
            elif kind in EXIT_KINDS and (n >= 2 or (forced and n)):   # «(обрывов за 15 мин: 3)» — of the last 15 min
                a["n"] = n
                tkey += "_n"
            ev = self._event(kind, tkey, a, now, exit=eid)
        self.told[(eid, told_kind(kind, args))] = m
        self.said[eid] = (now, told_kind(kind, args))      # a group: with its step (К2-6, REV-s1-kt2-final З-2)
        self.said_why[eid] = (args or {}).get("why")
        self.window[eid] = m
        self._told_dirty = True
        return ev

    def _cat_now(self, snap, eid, decision=None):
        """What the state of a key is now, as the kind of an event: an exit — by its health (a record of 3.3.5 — its
        alive); a group — on its last step or not, as the key of the antispam (``told_kind``: «напрямую» is not the
        block, К2-6). None — unknown."""
        if eid.startswith(GROUP_KEY):
            g = ((decision or {}).get("groups") or {}).get(eid[len(GROUP_KEY):])
            if g is None:
                return None
            if not g.get("last_step"):
                return "group_restored"
            return told_kind("group_last_step", {"step": g.get("last_step")})
        rec = (snap.get("exits") or {}).get(eid)
        if not isinstance(rec, dict):
            return None
        s, shown = exits.health_of(rec)
        s = shown if s == "unknown" else s
        if s in ("ok", "degraded"):
            said = (self.said.get(eid) or (0, None))[1]
            if s == "degraded" and "state" in rec:
                return "exit_degraded"
            return "exit_alive" if said in (None, "exit_dead") or "state" not in rec else "exit_recovered"
        if s == "off":
            return "exit_dead" if "dead" in (rec.get("reasons") or ["dead"]) else "exit_removed"
        if s == "standby":
            return "exit_standby"
        return None

    @staticmethod
    def _same(a, b):
        """Two kinds say the same state (good: alive / recovered)."""
        good = ("exit_alive", "exit_recovered")
        return a == b or (a in good and b in good)

    def _group_args(self, gid, kind, decision, names, glabels):
        """The args of a group's event told at the end of its window with nothing held of that kind (the collector was
        restarted inside the window — ``last_args`` is not kept; or the one held was of the other step): from the
        decision of the tick and the names of the groups now, as ``assign`` tells them — the number of devices on the
        last step, the step, the name (REV-338 З-3: not «0 устройств» and another group's name)."""
        dec = decision if isinstance(decision, dict) else {}
        g = (dec.get("groups") or {}).get(gid)
        g = g if isinstance(g, dict) else {}
        ev = {"kind": "group_restored" if kind == "group_restored" else "group_last_step", "group": gid}
        grp = [x for x in (dec.get("policy") or {}).get("groups") or [] if isinstance(x, dict) and x.get("id") == gid]
        if grp:                          # T-q круг 3, П-3: a Russian group — «российские сайты …» by the end of a window
            ev["role"] = grp[0].get("role", "abroad")
        if ev["kind"] == "group_last_step":
            steps = [s for s in (g.get("steps") or {}).values() if isinstance(s, str)] \
                if isinstance(g.get("steps"), dict) else []
            ev.update(step="direct" if kind == DIRECT_STEP else "block",
                      devices=len([s for s in steps if s.startswith("last_")]),
                      direct=len([s for s in steps if s == "last_direct"]))
            via = dec.get("dns_via")
            if gid == "main" and isinstance(via, str):
                ev["dns_via"] = via if via != "main" else "none"
        return self._ev_args(ev, names or {}, glabels or {})

    def _window_end(self, names, snap, now, decision=None, flaps=None, glabels=None):
        """The window of a key (EVENT_QUIET s from its last published event) is over and changes were held back: the
        feed must not stay wrong — the state now is told (with the drops of the window) when it is not the one the feed
        says; else the window starts again quietly. With ``flaps`` ({eid: falls of 15 min} — ``flap_summary``) an exit
        with 3 or more is told «нестабилен» even when the state is the one told (never one gone «в запас» as unstable:
        its ``exit_standby`` said it). Only with a known pool (``names``)."""
        m = _m(now)
        for eid in list(self.held):
            if not eid.startswith(GROUP_KEY) and eid not in names:
                continue                                    # the exit left the pool: ``_forget``
            if m - self.window.get(eid, m) < EVENT_QUIET:
                continue
            kind = self._cat_now(snap, eid, decision)
            said = (self.said.get(eid) or (0, None))[1]
            held = self.last_args.get(eid)
            args = dict(held[1]) if held and told_kind(*held) == kind else None
            if eid.startswith(GROUP_KEY):
                args = args or (self._group_args(eid[len(GROUP_KEY):], kind, decision, names, glabels)
                                if kind else None)
                kind = "group_last_step" if kind == DIRECT_STEP else kind   # the event; ``said`` keeps the step
            f = (flaps or {}).get(eid)
            unstable = "unstable" in (((snap.get("exits") or {}).get(eid) or {}).get("reasons") or [])
            if kind and not self._same(told_kind(kind, args), said):
                self._publish(eid, kind, names, snap, now, forced=True, args=args,
                              flaps=f if f is not None and f >= 3 and not unstable else None)
            elif kind and f is not None and f >= 3 and not unstable and kind != "exit_standby":
                self._publish(eid, kind, names, snap, now, forced=True, args=args, flaps=f)
            else:
                self.held.pop(eid, None)
                self.last_args.pop(eid, None)
                self.window[eid] = m

    def _settle_feed(self, names, snap, now):
        """The first round after a start (a restart of the collector, a reboot, an update): what the feed said last of
        each exit against its state — when they differ, the state is told once (REV-335 №1)."""
        for eid in names:
            said = self.said.get(eid)
            kind = self._cat_now(snap, eid)
            if said and kind and not self._same(said[1], kind):
                self._publish(eid, kind, names, snap, now, forced=True)

    def _forget(self, names, now):
        """Exits that left the pool are forgotten (told, said, the window, the held back and the drops); old drops
        go. The keys of the groups stay (a group is not an exit of the pool)."""
        m = _m(now)
        for d in (self.held, self.window, self.said, self.drops, self.said_why, self.last_args):
            for eid in [x for x in d if x not in names and not x.startswith(GROUP_KEY)]:
                d.pop(eid, None)
                if d is self.said:
                    self._told_dirty = True
        for k in [k for k in self.told if k[0] not in names and not k[0].startswith(GROUP_KEY)]:
            self.told.pop(k, None)
        for eid in list(self.drops):
            self.drops[eid] = [x for x in self.drops[eid] if 0 <= m - x <= 2 * EVENT_QUIET]
            if not self.drops[eid]:
                self.drops.pop(eid, None)

    def _manual(self, eid, kind, args):
        """An action of the owner (SE §3.6): «В запас», «Обслуживание», вкл/выкл, the confirmation of a key of a
        subscription — and «Вернуть» after one of them (the state told last was manual)."""
        why = (args or {}).get("why")
        if kind == "exit_standby" and why in ("manual", "sub_confirm"):
            return True
        if kind == "exit_removed" and why in ("maintenance", "disabled"):
            return True
        if kind in ("exit_recovered", "exit_alive") and self.said_why.get(eid) in ("manual", "sub_confirm",
                                                                                     "maintenance", "disabled"):
            return True
        return False

    def _ev_args(self, ev, names, glabels):
        """The args of a published event of health/assign (SE §3.6): the event's own codes + ``id``, ``name``,
        ``group``, ``group_name`` (``policy.label``), ``role``."""
        skip = ("kind", "exit", "request")
        a = dict((k, v) for k, v in ev.items() if k not in skip)
        eid = ev.get("exit")
        if eid:
            a["id"] = eid
            if "name" not in a:         # T-3379 М-2: «Russian exit “”» — exit_country_changed, exit_quota … had no name
                a["name"] = names.get(eid, eid)
        gid = ev.get("group")
        if gid:
            a["group_name"] = glabels.get(gid, gid)
            if ev.get("kind") in ("group_last_step", "group_restored"):
                a["name"] = glabels.get(gid, gid)
        if ev.get("kind") == "group_restored" and ev.get("exit"):
            a["exit_name"] = names.get(ev["exit"], ev["exit"])
        if ev.get("kind") == "group_last_step" and a.get("dns_via") not in (None, "direct", "none"):
            a["dns_via"] = glabels.get(a["dns_via"], a["dns_via"])
        if ev.get("kind") == "exit_failover":         # T15 Q5: the exits of the levels by name (assign._tier_said)
            for side in ("from_", "to_"):
                ids = a.pop(side + "exits", None)
                if isinstance(ids, list):
                    a[side + "names"] = [str(names.get(e, e)) for e in ids]
        return a

    def publish(self, events, names, glabels, snap, now, notify=None):
        """The events of a tick (``collect.exits_tick``: health, assign, the network of the node, xray) through the layer:
        ``node_online`` first (З2), the changes of the exits and groups by the antispam, ``exit_failover`` of one group
        within FAILOVER_COALESCE_S summed (``notify.push.coalesce``), the rest as they come. ``transition`` is no
        published event (metrics.db only)."""
        notify = notify or {}
        coalesce = ((notify.get("push") or {}).get("coalesce", True)) is not False
        order = sorted(events or [], key=lambda e: 0 if e.get("kind") == "node_online" else 1)
        m = _m(now)
        for ev in order:
            kind = ev.get("kind")
            if kind in (None, "transition") or ev.get("request"):
                continue
            if kind == "node_offline":
                if not self._open_offline():
                    self._event(kind, "gw_live.event.node_offline", {"since": ev.get("since")}, now)
                continue
            if kind == "node_online":
                first = self._open_offline()
                a = {"since": ev.get("since"), "until": ev.get("until"), "minutes": ev.get("minutes")}
                if ev.get("restart"):
                    a["restart"] = True                # back before the restarted collector saw it (REV-s1-t4 З1)
                if first is not None:                  # the outage began with its first node_offline (З2)
                    since = _int(((first.get("args") or {}).get("since"))) or _int(first.get("ts"))
                    if since:
                        a.update(since=since, minutes=int(max(0, (ev.get("until") or now) - since) // 60))
                self._event(kind, "gw_live.event.node_online", a, now)
                continue
            if kind == "node_ru_offline":               # T15 Q5: one start an outage of the Russian addresses
                if _open_of(self.events, kind) is None:
                    self._event(kind, "gw_live.event.node_ru_offline", {"since": ev.get("since")}, now)
                continue
            if kind == "node_ru_online":
                first = _open_of(self.events, "node_ru_offline")
                a = {"since": ev.get("since"), "until": ev.get("until"), "minutes": ev.get("minutes")}
                if ev.get("restart"):
                    a["restart"] = True
                since = _int(((first or {}).get("args") or {}).get("since")) or _int((first or {}).get("ts"))
                if since and since < (_int(ev.get("since")) or since + 1):
                    a.update(since=since, minutes=int(max(0, (ev.get("until") or now) - since) // 60))
                self._event(kind, "gw_live.event.node_ru_online", a, now)
                continue
            if kind in hostmon.HOST_KINDS:               # A5, §3.5: the node — no exit, no group (no _ev_args)
                self._event(kind, ev.get("key") or "gw_host.event.raise", dict(ev.get("args") or {}), now)
                continue
            if kind in BENCH_KINDS:                      # B7: a check of the server ended — the node, no exit
                self._event(kind, bench_key(ev.get("args") or {}), bench_args(ev.get("args") or {}), now)
                continue
            if kind == "bench_skipped_day":                # B11, SS §2.5 п. 7: the node, as the health (no _ev_args)
                self._event(kind, ev.get("key") or "bench.skipday.feed", dict(ev.get("args") or {}), now)
                continue
            a = self._ev_args(ev, names, glabels)
            if kind in EXIT_STATE_KINDS and ev.get("exit"):
                eid = ev["exit"]
                self._exit_event(eid, kind, names, snap, now, args=a, manual=self._manual(eid, kind, a))
            elif kind in ("group_last_step", "group_restored") and ev.get("group"):
                key = GROUP_KEY + ev["group"]
                # «при отказе» changed by the owner on the last step (KT3-3): an action of the owner — at once
                manual = kind == "group_last_step" and a.get("why") == "fail_changed"
                if not manual and not self._key_kind_ok(key, told_kind(kind, a), m):
                    self.held[key] = self.held.get(key, 0) + 1
                    self.window.setdefault(key, m)
                    self.last_args[key] = (kind, a)
                else:
                    self._publish(key, kind, names, snap, now, args=a)
            elif kind == "exit_failover" and coalesce:
                gid = ev.get("group") or "main"
                pend = self.failover.get((gid, bool(ev.get("back"))))
                if pend is None:
                    self.failover[(gid, bool(ev.get("back")))] = {"at": m, "args": a}
                else:
                    pend["args"]["moved"] = int(pend["args"].get("moved") or 0) + int(a.get("moved") or 0)
                    pend["args"]["to_tier"] = a.get("to_tier")
                    for k in ("to_label", "to_names"):
                        pend["args"].pop(k, None)
                        if k in a:
                            pend["args"][k] = a[k]
            else:
                self._event(kind, "gw_live.event." + kind, a, now, **({"exit": ev["exit"]} if ev.get("exit") else {}))
        for k in sorted(self.failover):
            pend = self.failover[k]
            if m - pend["at"] >= FAILOVER_COALESCE_S or not coalesce:
                del self.failover[k]
                self._event("exit_failover", "gw_live.event.exit_failover", pend["args"], now)
        self.fed += 1

    def _open_offline(self):
        """The first ``node_offline`` of the outage going on (none after the last ``node_online``), or None."""
        first = None
        for e in self.events:
            if e.get("kind") == "node_online":
                first = None
            elif e.get("kind") == "node_offline" and first is None:
                first = e
        return first

    def _said(self):
        """The events with their words in the language of this round: kept from round to round, one told before the
        language of the server changed must not stay in the old one under ``i18n.lang`` of the new (3.3.3, C). Told once
        an event (SE §3.6): again only when the language changed."""
        lang = get_lang()
        out = []
        for e in self.events:
            if isinstance(e, dict) and e.get("key"):
                got = self._texts.get(e.get("id"))
                if got is None or got[0] != lang:
                    got = (lang, event_text(e))
                    self._texts[e.get("id")] = got
                e = dict(e, text=got[1])
            out.append(e)
        keep = set(e.get("id") for e in self.events if isinstance(e, dict))
        for k in [k for k in self._texts if k not in keep]:
            self._texts.pop(k, None)
        self.events = out
        return list(out)

    def _prune(self, now):
        """The last EVENTS_MAX within EVENTS_AGE (SE §3.6: 500, 7 days); a ``node_offline`` of the outage going on is
        never pushed out (the bot sums the outage from it, REV-s1-t9-2 №5)."""
        keep = self._open_offline()
        ru = _open_of(self.events, "node_ru_offline")            # T15 Q5: the open Russian outage too
        evs = [e for e in self.events if now - (_int(e.get("ts")) or 0) <= EVENTS_AGE or e is keep or e is ru]
        if len(evs) > EVENTS_MAX:
            extra = len(evs) - EVENTS_MAX
            out = []
            for e in evs:
                if extra and e is not keep and e is not ru:
                    extra -= 1
                    continue
                out.append(e)
            evs = out
        self.events = evs

    # ── rounds ──
    def full(self, st, runner, stats, round_res, pool, now=None, pool_known=True):
        """The snapshot of a full round (``collect.tick``). ``stats`` — {kind: adapter.status() or None (failed)}
        of the enabled inbounds; ``round_res`` — what ``exits_tick`` returned (changes, repaired, error,
        counters); ``pool`` — the exits of the round (``pool_known`` False: it could not be read — a broken parser —
        and the feed of the exits waits: nothing is forgotten, told or compared). Returns the document (``write``)."""
        from . import hooks, registry
        now = _now() if now is None else now
        self._load(now)
        round_res = round_res or {}
        snap = exits.load_snapshot()
        probed = round_res.get("probed") or {}
        names = {}
        ex = {}
        for e in pool or []:
            row = exits.public(e, st, snap)
            names[e["id"]] = row["name"]
            p = probed.get(e["id"]) or {}
            hs = p.get("handshake") if e.get("kind") == "awg" else None
            rec = (snap.get("exits") or {}).get(e["id"])
            hstate, shown = exits.health_of(rec)
            usable = exits.usable_rec(rec)
            ex[e["id"]] = {"alive": usable, "kind": e.get("kind"), "proto": row.get("proto") or "",
                           "label": row["name"], "health": hstate, "shown": shown,
                           "reasons": list((rec or {}).get("reasons") or []) if isinstance(rec, dict) else [],
                           "recovering": bool((rec or {}).get("recovering")), "unstable": bool((rec or {}).get("unstable")),
                           "state": "ok" if usable else EXIT_STATE.get(row.get("state"), "unknown")
                           if "state" not in (rec or {}) else ("unknown" if hstate == "unknown" else "dead"),
                           "since": epoch(row.get("since")),
                           "enabled": bool(row.get("enabled", True)),
                           "detail": p.get("detail") or row.get("detail") or "",
                           "handshake_at": int(now - hs) if isinstance(hs, (int, float)) and not isinstance(hs, bool)
                           else None,
                           "silent_since": epoch(row.get("silent_since")), "fails": row.get("fails") or 0,
                           "dies_after": row.get("dies_after")}
        dec = round_res.get("decision")
        glabels = _group_labels(dec, st)
        notify = model.notify(st) if isinstance(st, dict) else {}
        if pool_known:
            self._forget(names, now)
            if self._settle:
                self._settle = False
                self._settle_feed(names, snap, now)
        self.publish(round_res.get("events") or [], names, glabels, snap, now, notify)
        if pool_known and not round_res.get("node_offline"):
            # Н9 (T-KT1): the node without a network — the states are frozen (§4.6): the summary of a window is not told
            # now («снова работает (обрывов: 2)» during the outage); the held changes wait for the network
            self._window_end(names, snap, now, dec, _flaps(snap, now) if notify.get("flap_summary") else None, glabels)
        self._save_feed()
        for what in round_res.get("repaired") or []:
            if what in (round_res.get("hushed") or []):
                continue                                   # the same restore after a reboot, told once (T-M6-qs330c №6)
            if what == "table":
                self._event("gw_part_restored", "gw_live.event.table_restored", {}, now)
            elif what == "routing":
                self._event("gw_part_restored", "gw_live.event.routing_restored", {}, now)
        try:
            hook_sessions = hooks.sessions()
        except (KaskadError, OSError, ValueError):
            hook_sessions = []
        sess = sessions_of(st, stats, hook_sessions)
        gw_on = model.enabled(st)
        inbounds = {}
        problems = []
        issues = []                                        # the same, with kinds (summary: the SSH menu)
        msgs, whys = [], {}                                # how to say them in another language (``localize``)

        def problem(kind, key, **args):
            said(kind, msg(key, **args))

        def said(kind, m):                                 # a ready msg: its args never become our keywords
            text = render(m)
            msgs.append(m)
            problems.append(text)
            issues.append({"kind": kind, "detail": text})

        on_kinds = model.enabled_kinds(st)
        for kind in model.installed_kinds(st):
            ad = registry.get(kind)
            title = (getattr(ad, "title", "") if ad is not None else "") or kind
            enabled = gw_on and kind in on_kinds
            if not enabled:
                running, count = False, 0
            elif kind not in stats or stats.get(kind) is None:
                running, count = None, None
            else:
                running = bool(stats[kind].get("running"))
                count = len([s for s in sess if s.get("kind") == kind])
            try:
                from . import ops as gops                  # late: ops imports live
                ports = ", ".join(gops.ports_shown(ad, st)) if ad is not None else ""
            except Exception:                              # noqa: BLE001 — an adapter of another work package
                ports = ""
            rx, tx = iface_bytes(_ifaces(ad))
            if rx is None and enabled and isinstance(stats.get(kind), dict):     # VLESS: xray's counters
                rx, tx = _int(stats[kind].get("rx")), _int(stats[kind].get("tx"))
            warns, why = [], None
            if enabled and running is not None:
                s = stats.get(kind) or {}
                warns = [util.redact(w)[:WHY_MAX] for w in s.get("warnings") or [] if isinstance(w, str)][:5]
                if running is False:
                    wm = down_reason_msg(runner, _units_of(ad, s))
                    why = _why_text(wm) if wm else None
                    if why:
                        whys[kind] = wm
            inbounds[kind] = {"enabled": enabled, "running": running, "sessions": count, "ports": ports,
                              "title": title, "rx": rx, "tx": tx, "warnings": warns, "why": why}
            if enabled and running is False:
                problem("gw_inbound", "gw_live.inbound_down", title=title)
            was = self.running.get(kind)
            if enabled and running is not None:
                if was is True and running is False:
                    self._event("inbound_down", "gw_live.inbound_down", {"title": title}, now, inbound=kind)
                elif was is False and running is True:
                    self._event("inbound_up", "gw_live.event.inbound_up", {"title": title}, now, inbound=kind)
                self.running[kind] = running
            elif not enabled:
                self.running.pop(kind, None)
        for kind in list(self.running):
            if kind not in inbounds:
                self.running.pop(kind, None)
        try:                                               # 3.3.3: a session after an issue — the port is open
            from . import inlog
            inlog.note_sessions([dict(x, kind=k, since=epoch(x.get("since"))) for k in on_kinds
                                 for x in (stats.get(k) or {}).get("sessions") or [] if isinstance(x, dict)]
                                + [x for x in hook_sessions or [] if isinstance(x, dict)], now=now)
        except (OSError, ValueError):
            pass
        services = {"dns": None, "xray": None}
        if gw_on:
            if _unit_file(DNS_UNIT):
                services["dns"] = _unit_active(runner, DNS_UNIT)
                if not services["dns"]:
                    problem("gw_service", "gw_live.dns_down", unit=DNS_UNIT)
            from . import xray
            need_xray = bool(xray._usable(st, pool or [])) or any(k in model.XRAY_KINDS for k in on_kinds)
            if need_xray:
                services["xray"] = _unit_active(runner, XRAY_UNIT)
                if not services["xray"]:
                    problem("gw_service", "gw_live.xray_down", unit=XRAY_UNIT)
        if round_res.get("error"):
            problem("gw_collect", "gw_live.collector_error", error=util.redact(round_res["error"]))
        if gw_on and round_res.get("clock_unsynced"):          # REV-s1-t4 З3: quotas wait for the clock
            problem("gw_clock", "gw_live.clock_unsynced")
        usable = [x for x in ex.values() if exits.parsed(x) and x["enabled"]]
        if gw_on and usable and not any(x["alive"] for x in usable):
            problem("exits_dead", no_pool_key(dec))
        hr = round_res.get("host") if isinstance(round_res.get("host"), dict) else None
        clock_said = gw_on and bool(round_res.get("clock_unsynced"))
        for a in (hr or {}).get("alarms") or []:          # A5, NS-D4: the alarms of the node (shown ≥ alarm)
            if not a.get("problem") or (a.get("id") == "clock" and clock_said):
                continue                                   # services / the site's certificate / gw_clock: said already
            m = a.get("msg") if isinstance(a.get("msg"), dict) and isinstance(a["msg"].get("key"), str) \
                else hostmon.msg("gw_host.check." + str(a.get("id")))
            said(a.get("kind") or "host", {"key": m["key"], "args": dict(m["args"]) if isinstance(m.get("args"), dict)
                                           else {}})              # args «kind»/«key» of hostmon.json (REV-ns-a5-sec З-1)
        host = self.host.sample()
        if hr:
            host.update(dict((k, v) for k, v in hr.items() if k != "alarms"))
        self._prune(now)
        self.seq += 1
        doc = {"v": V, "ts": round(now, 3), "seq": self.seq,
               "gateway": {"enabled": gw_on, "problems": problems, "issues": issues, "services": services},
               "inbounds": inbounds, "exits": ex, "devices": self._since(devices_of(st, sess), now),
               "no_exit": no_exit(dec), "no_exit_ru": no_exit(dec, ru=True), "groups": groups_now(dec),
               "policy": {"node_offline": bool(round_res.get("node_offline")),
                          "node_ru_offline": bool(round_res.get("node_ru_offline"))},
               "forwards": forwards_of(st, round_res.get("counters")),
               "host": host, "events": self._said(),
               "i18n": {"lang": get_lang(), "problems": msgs, "why": whys}}
        self._marks(doc)
        self.doc, self.st, self.sessions = doc, st, sess
        return doc

    def _marks(self, doc):
        """The marks of the lines in ``doc`` in place — of every kind of round, so a live or light round carries what
        the collector counted since the full one (3.3.7.11): ``activity`` — the panel reads the summary of
        «Пользователи» again when ``ts`` moves and adds the open window to it (3.3.7.10); ``metrics`` — the line of
        «Выходы» again when ``ts`` moves, the open minute added to it."""
        doc.pop("activity", None)
        doc.pop("metrics", None)
        if self.activity_ts is not None or self.activity_open is not None:
            doc["activity"] = dict(self.activity_open or {}, ts=self.activity_ts)
        if self.metrics_ts is not None:
            doc["metrics"] = dict(self.metrics_open or {}, ts=self.metrics_ts)
        return doc

    def light(self, st, stats, now=None):
        """The light round between two full ones while a panel watches (3.3.7.11): ``stats`` — {kind: adapter.status()
        or None} taken now (``collect.inbound_statuses``): who is online, since when, rx/tx, and the marks of the lines;
        the inbounds, the exits, the events and the health stay the ones of the full round. None before the first."""
        from . import hooks
        if self.doc is None:
            return None
        now = _now() if now is None else now
        try:
            hook_sessions = hooks.sessions()
        except (KaskadError, OSError, ValueError):
            hook_sessions = []
        sess = sessions_of(st, stats, hook_sessions)
        self.seq += 1
        doc = dict(self.doc)
        doc.update({"ts": round(now, 3), "seq": self.seq, "devices": self._since(devices_of(st, sess), now),
                    "host": dict(self.doc.get("host") or {}, **self.host.sample())})
        self._marks(doc)
        self.doc, self.st, self.sessions = doc, st, sess
        return doc

    def fast(self, runner, now=None):
        """The live round between two full ones: rx/tx of the WireGuard-style peers (one ``transfer`` per
        running interface), interface counters, /proc. None before the first full round."""
        from . import registry
        if self.doc is None or self.st is None:
            return None
        now = _now() if now is None else now
        sess = [dict(s) for s in self.sessions]
        inbounds = {}
        for kind, rec in (self.doc.get("inbounds") or {}).items():
            rec = dict(rec)
            ad = registry.get(kind)
            if rec.get("running") and _wg_style(ad):
                table = transfer(runner, ad)
                if table is not None:
                    peers = _peers(ad)
                    by_dev = {}
                    for pub, rt in table.items():
                        dev = peers.get(pub)
                        if dev:
                            by_dev[dev] = rt
                    for s in sess:
                        if s.get("kind") == kind and s.get("device") in by_dev:
                            s["rx"], s["tx"] = by_dev[s["device"]]
            if _ifaces(ad):                         # no interfaces (VLESS): the counters of the full round stay
                rec["rx"], rec["tx"] = iface_bytes(_ifaces(ad))
            inbounds[kind] = rec
        self.seq += 1
        doc = dict(self.doc)
        doc.update({"ts": round(now, 3), "seq": self.seq, "inbounds": inbounds,
                    "devices": self._since(devices_of(self.st, sess), now, remember=False),
                    "host": dict(self.doc.get("host") or {}, **self.host.sample())})     # A5: health stays (§3.6)
        self._marks(doc)
        self.doc, self.sessions = doc, sess
        return doc


def _group_labels(dec, st):
    """{gid: the shown name of the group} (``policy.label``: «Основная»/«Main» of «main» without a name, §3.1)."""
    from . import policy as P
    pol = (dec or {}).get("policy")
    if pol is None:
        try:
            pol = P.load(st or {})
        except Exception:                                # noqa: BLE001 — a name is not worth a failed round
            pol = {}
    return dict((g["id"], P.label(g)) for g in (pol or {}).get("groups") or [] if isinstance(g, dict) and g.get("id"))


def _flaps(snap, now):
    """{exit: falls of «dead» within the last EVENT_QUIET s} by health (``falls`` of the snapshot) — the count of the
    summary «нестабилен» and of the mark §4.3 (SE §8.2, правка 6, З1-5)."""
    out = {}
    for eid, rec in (snap.get("exits") or {}).items():
        falls = [t for t in (rec or {}).get("falls") or [] if isinstance(t, (int, float)) and 0 <= now - t < EVENT_QUIET]
        if falls:
            out[eid] = len(falls)
    return out


def no_pool_key(dec):
    """К2-5 (T-KT2): the words of «no exit of the pool works» say what happens to the clients — by the last step of
    the decision of the tick: every device on «напрямую» — they go out from the address of the server; some of them —
    both; else — they are blocked. No device on a last step — by the last steps of the groups themselves (every group
    on «напрямую» — «напрямую», some — both); no group on one, no decision — blocked (REV-s1-kt2-final П-2)."""
    steps = set(v["step"] for v in no_exit(dec).values())
    if not steps:
        rus = _ru_gids(dec)                             # the pool is the foreign one: a Russian group says nothing here
        steps = set("last_" + str(g.get("last_step")) for gid, g in ((dec or {}).get("groups") or {}).items()
                    if isinstance(g, dict) and gid not in rus and g.get("last_step") in ("block", "direct"))
    if steps == {"last_direct"}:
        return "gw_live.no_pool_direct"
    if "last_direct" in steps:
        return "gw_live.no_pool_mixed"
    return "gw_live.no_pool"


def _ru_gids(dec):
    """The ids of the Russian groups of the policy of a decision (none without one: every group foreign, as before)."""
    pol = (dec or {}).get("policy") if isinstance(dec, dict) else None
    return set(g.get("id") for g in (pol or {}).get("groups") or [] if isinstance(g, dict) and g.get("role") == "ru")


RANK_TIERS = 10                     # policy.MAX_TIERS: the rows of at most so many tiers of a group in live.json
RANK_EXITS = 120                    # exits of one tier (rows + out): exitsubs.POOL_MAX and some — never a bigger file
RANK_BY = ("even", "rtt", "x_norm", "weights", "capacity", "manual", "per_connection")


def _fin(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _rank_v(v, by):
    if not _fin(v):
        return None
    v = round(float(v), 2 if by == "x_norm" else 1)
    return int(v) if v == int(v) else v


def ranks_view(ranks):
    """The rows of «strength» of a group's tiers (``assign.rows``) for live.json (PLAN-order Ш2): {tid: {"by",
    "queue", "order", "klass", "nodata", "rows": [[{"id", "v", "share"}…]…], "out": [{"id", "why"}]}} — numbers
    rounded (ms and Mbit/s to 0.1, × norm to 0.01, shares to 0.001), at most RANK_TIERS tiers and RANK_EXITS exits a
    tier; anything not of that shape — left out. Display only: the choice is made by the same rows in the collector."""
    out = {}
    for tid, r in list((ranks if isinstance(ranks, dict) else {}).items())[:RANK_TIERS]:
        if not isinstance(tid, str) or not isinstance(r, dict) or r.get("by") not in RANK_BY:
            continue
        by, left, rows = r["by"], RANK_EXITS, []
        for row in r.get("rows") or []:
            items = []
            for x in row if isinstance(row, list) else ():
                if left <= 0 or not isinstance(x, dict) or not isinstance(x.get("id"), str):
                    continue
                share = x.get("share")
                items.append({"id": x["id"], "v": _rank_v(x.get("v"), by),
                              "share": round(float(share), 3) if _fin(share) else 0.0})
                left -= 1
            if items:
                rows.append(items)
        gone = []
        for x in r.get("out") or []:
            if left > 0 and isinstance(x, dict) and isinstance(x.get("id"), str):
                gone.append({"id": x["id"], "why": str(x.get("why") or "")[:16]})
                left -= 1
        out[tid] = {"by": by, "queue": bool(r.get("queue")), "order": "manual" if r.get("order") == "manual" else "auto",
                    "klass": r.get("klass") if r.get("klass") in ("ok", "degraded", "standby") else None,
                    "nodata": bool(r.get("nodata")), "rows": rows, "out": gone}
    return out


def groups_now(dec):
    """``live.groups`` (SPEC-groups §8.9, T15 Q6): {gid: {"active_tier", "last_step", "since", "ranks"?}} — every
    group of the decision of the tick ({} without one): its working tier (None — none works), its last step (None |
    "direct" | "block") and since when it stands there (``last_since``). The panel paints the step of a group by it —
    «сейчас: через «Роутер»» or «с сервера напрямую — выходы недоступны с 12:03» — without reading the policy again.
    ``ranks`` (PLAN-order Ш2) — the rows of «strength» of its tiers (``ranks_view``), when the decision has them."""
    out = {}
    for gid, g in ((dec or {}).get("groups") or {}).items():
        if not isinstance(g, dict):
            continue
        step = g.get("last_step") if g.get("last_step") in ("direct", "block") else None
        out[gid] = {"active_tier": g.get("active_tier") if isinstance(g.get("active_tier"), str) else None,
                    "last_step": step, "since": _int(g.get("last_since")) if step else None}
        if isinstance(g.get("ranks"), dict):
            out[gid]["ranks"] = ranks_view(g["ranks"])
    return out


def no_exit(dec, ru=False):
    """``live.no_exit`` (§4.4, §7.3): {device id: {"step": last_block | last_direct, "group", "since"}} — the devices
    on the last step of their FOREIGN group by the decision of the tick ({} without one): no internet, or directly from
    the node. ``ru=True`` — ``live.no_exit_ru`` (T15 Q6, SPEC-groups §8.3): the same of the Russian groups — only
    their Russian sites are closed or go directly, the foreign ones work. A device is in one foreign and at most one
    Russian group: two maps, or one entry would hide the other (a Russian last step read as «нет выхода в интернет»)."""
    out = {}
    rus = _ru_gids(dec)
    for gid, g in ((dec or {}).get("groups") or {}).items():
        if (gid in rus) != bool(ru):
            continue
        for did, step in (g.get("steps") or {}).items():
            if step in ("last_block", "last_direct"):
                out[did] = {"step": step, "group": gid, "since": _int(g.get("last_since"))}
    return out


def feed_path():
    """What the feed keeps of each exit (``Live``): /run — a restarted collector goes on with it, a reboot clears it."""
    return os.path.join(paths.run_dir(), "live-feed.json")


def told_path():
    """The last event told of each exit, on the disk: the first round after a reboot compares it with the state."""
    return os.path.join(paths.gw_dir(), "exits-told.json")


def _ts(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def _said_of(v):
    """(ts, kind) of a stored «said» [ts, kind], or None."""
    if isinstance(v, list) and len(v) == 2 and _ts(v[0]) is not None and v[1] in TOLD_KINDS:
        return (v[0], v[1])
    return None


def _m(now):
    """The monotonic clock at the moment ``now`` of the wall clock (the round's own reading, a moment ago): the windows
    of the feed never follow a jump of the wall clock (SE §3.6, правка 6), and an event of a given ``now`` is placed
    where it happened."""
    return _mono() - _now() + now


def _num0(v):
    """A number of the monotonic clock (0 and up), or None."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def write(doc):
    """live.json atomically (temp file in /run/kaskad + rename), 0600, compact."""
    util.atomic_write(paths.live_json(), json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                                    separators=(",", ":")) + "\n", 0o600)
