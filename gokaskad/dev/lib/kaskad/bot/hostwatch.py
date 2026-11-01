"""The light health of a node WITHOUT the gateway, in the bot (SPEC-node-score §3.9, §2.1 «Смена хозяина»; A6).

The collector does not work (its own check is false: ``model.enabled(st) and engine == "nft"``, as
``gw/collect.py`` ``tick``), ``gateway.hostmon.enabled`` is true and the bot has a chat to tell: once a minute the
monitor thread calls ``HostWatch.tick`` — ``hostprobe.read`` (the minute set, the hour set once an hour) →
``hostmon.evaluate_instant`` (the rules ``instant``; the rules with a history go «na no_gateway» once) → the events
whose ``args.push`` is true go straight to the owner (no feed: there is no live.json without the gateway), the state
of the automaton — /var/lib/kaskad/hostmon.json (``owner: "bot"``), the result — /run/kaskad/host-health.json
(``hostprobe.read_health`` takes it ≤ 180 s).

``gateway.hostmon.enabled: false`` keeps the bot the owner, as the collector stays one (REV-ns-a6 З-2): nothing is
read, every rule goes «na off» — one event for an alarm said, ``said = null`` in hostmon.json, ``level: off`` in
host-health.json. A file that cannot be written (the disk or the inodes full — the very alarm of such a node) never
loses the alert: the events go to the owner anyway, the error is one line of the journal while it lasts (REV-ns-a6
С-1, as ``collect._host_eval``).

Who owns the file is chosen by the settings, never by the age of live.json (two writers would argue). Each time the
bot becomes the owner it reads hostmon.json again and keeps nothing between two periods (REV-2 З-6): the gateway off
→ on → off in one process — the second time the bot goes on from what the collector said and sent.
"""

import json
import logging
import os
import time

from .. import util
from ..i18n import tr
from . import data

log = logging.getLogger("kaskad.bot")
FILES = {"hostmon_json": "hostmon.json", "host_health": "host-health.json"}

HOST_EVERY = 60.0           # s: the monitor thread calls ``tick`` this often (§3.9 п. 1)
HOUR = 3600.0               # s: the hour set of hostprobe (the certificates)
DISK_EVERY = 3600.0         # s: hostmon.json at least this often (and at every change of the levels said)
SEND_MAX_AGE = 3600         # s: an alert Telegram did not take is tried again this long (= monitor.GW_MAX_AGE)


def _sig(state):
    """What makes hostmon.json worth writing (= ``collect._host_sig``): the levels said, ``pending``, ``pushed``,
    the last event, the parts."""
    out = []
    for rid, rs in sorted(((state or {}).get("rules") or {}).items()):
        sub = rs.get("sub") if isinstance(rs.get("sub"), dict) else {}
        out.append((rid, rs.get("said"), rs.get("pending"), rs.get("pushed"), rs.get("last_ev"),
                    tuple(sorted(sub.items()))))
    return tuple(out)


class HostWatch(object):
    def __init__(self, app, send, line):
        self.app = app
        self.send = send        # text -> bool (Telegram took it)
        self.line = line        # event -> the line of the message (monitor.gw_line: the icon by the level)
        self.errors = {}        # file -> the error said last (one line of the journal per change, REV-ns-a6 С-1)
        self.reset()

    def reset(self):
        """Not the owner: nothing kept (the next period reads hostmon.json again)."""
        self.state = None       # the state of hostmon; None — not the owner / the file not read yet
        self.hint = None        # «probes_hint» of the file: written back as it was (the collector's mark)
        self.sig = None
        self.disk_at = None
        self.hour_at = None
        self.level = None       # (level, since) of host-health.json
        self.unsent = []        # [(ts, line)] the alerts Telegram did not take yet

    def config(self, st):
        """``model.hostmon(st)`` when the bot is the owner now, else None. ``enabled: false`` — still the owner
        (REV-ns-a6 З-2): ``tick`` reads nothing and says the alarms «na off», as the collector does."""
        from ..gw import model
        if model.enabled(st) and st.get("engine") == "nft":
            return None                               # the collector works: it is the owner (gw/collect.py tick)
        cfg = model.hostmon(st)
        if not self.app.cfg.chat_id or not self.app.cfg.token:
            return None                               # nobody to tell: no pushes, no host-health.json (§3.9 п. 1)
        return cfg

    def tick(self, now, mono=None):
        """One minute of the owner. Returns True when the bot was the owner."""
        mono = time.monotonic() if mono is None else mono
        st = data.state_doc()
        cfg = self.config(st)
        if cfg is None:
            if self.state is not None or self.unsent:
                self.reset()
            return False
        from ..gw import hostmon, hostprobe, model
        if self.state is None:
            self._load(mono, now)
        if cfg["enabled"]:
            every = ["minute"]
            if self.hour_at is None or not 0 <= mono - self.hour_at < HOUR:
                every.append("hour")
                self.hour_at = mono
            raw = hostprobe.read({"st": st}, mono, every)
        else:                                         # off: nothing is read (§4 A5 п. 1), the rules go «na off»
            raw, self.hour_at = {}, None              # (= collect._host_eval); on again — the hour set at once
        notify = model.notify(st)
        lvl = notify.get("host_level")
        ctx = {"owner": "bot", "push": notify.get("push") or {}, "host_level": lvl if lvl in ("crit", "warn")
               else "crit", "node_offline": False, "uptime": raw.get("uptime"), "virt": raw.get("virt"),
               # the services of the gateway do not run without it — the summary checks them only with the gateway
               # on (gw/live.py services): nothing to ask systemctl here, the rule waits
               "services": None, "inbounds_why": None, "probes": None, "probes_on": False}
        state, events = hostmon.evaluate_instant(self.state, raw, cfg, ctx, now, mono)
        self.state = state
        # the decision first: a restart does not tell it again
        self._file("hostmon_json", self._save, state, now, mono)
        self._file("host_health", self._health, state, now)
        for e in events:                              # written or not: the alert is not lost (REV-ns-a6 С-1)
            a = e.get("args") if isinstance(e.get("args"), dict) else {}
            if a.get("push") is True:                 # hostmon decided (the switch, the alarm, host_level — §2.1)
                self.unsent.append((now, self.line(e)))
        self._flush(now)
        return True

    # ── the files ──
    def _file(self, what, fn, *args):
        """Write a file; a failure (ENOSPC, the inodes) never stops the minute nor the alerts: one line of the
        journal when the error changes (not a trace each minute onto a full disk), none while it lasts; a success
        clears it (the next failure is said again). The decision not written — a restart may tell it once more
        (§3.9 п. 6: «один повтор»)."""
        try:
            fn(*args)
        except Exception as e:                       # noqa: BLE001 — the file never stops the minute
            said = "%s: %s" % (type(e).__name__, getattr(e, "errno", None) or "")
            if self.errors.get(what) != said:
                self.errors[what] = said
                try:                                 # the journal only: not ``note_error`` — Telegram did not fail
                    log.warning(self.app.api.clean(tr("bot.log.host_file", file=FILES.get(what, what),
                                                      why="%s: %s" % (type(e).__name__, util.redact(e)))))
                except Exception:                    # noqa: BLE001 — the journal never stops the minute
                    pass
            return False
        self.errors.pop(what, None)
        return True

    def _load(self, mono, now):
        """hostmon.json again (§2.1 «Смена хозяина»): the collector's or the bot's of the last period. A broken file
        goes aside with a line in the journal (as the collector does), the automaton starts anew."""
        from ..gw import hostmon, hostprobe
        self.state, self.hint, self.sig, self.disk_at = {}, None, None, mono
        path = hostprobe.hostmon_path()
        raw = util.read_text(path)
        if not raw:
            return
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict) or not isinstance(doc.get("rules") or {}, dict):
                raise ValueError("not hostmon.json")
        except ValueError:
            try:
                os.replace(path, path + ".bad")
                util.log_tr("gw_host.log.state_bad", path=path, aside=path + ".bad")
            except Exception:                        # noqa: BLE001 — the journal never stops the minute
                pass
            return
        self.state = hostmon.from_disk(doc, mono, now)
        h = doc.get("probes_hint")
        self.hint = h if isinstance(h, (int, float)) and not isinstance(h, bool) else None
        self.sig = _sig(self.state)

    def _save(self, state, now, mono):
        from ..gw import hostmon, hostprobe
        sig = _sig(state)
        if sig == self.sig and self.disk_at is not None and 0 <= mono - self.disk_at < DISK_EVERY:
            return False
        doc = hostmon.to_disk(state)
        doc.update(owner="bot", probes_hint=self.hint)
        util.atomic_write(hostprobe.hostmon_path(), json.dumps(doc, ensure_ascii=False, sort_keys=True), 0o600)
        self.sig, self.disk_at = sig, mono
        return True

    def _health(self, state, now):
        """/run/kaskad/host-health.json — ``host.health`` of a node without the gateway (§3.6, §3.9 п. 3)."""
        from ..gw import hostmon, hostprobe
        lt = hostmon.light(state)
        if self.level is None or self.level[0] != lt["level"]:
            self.level = (lt["level"], int(now))
        health = {"v": hostmon.V, "at": int(now), "level": lt["level"], "since": self.level[1], "instant": True,
                  "by": "bot", "cost": None, "hint": None, "top": lt["top"], "info": lt["info"],
                  "checks": lt["checks"], "probes": None}
        util.atomic_write(hostprobe.health_path(), json.dumps({"v": 1, "at": now, "health": health},
                                                              ensure_ascii=False, sort_keys=True), 0o600)

    # ── the owner's chat ──
    def _flush(self, now):
        self.unsent = [(ts, ln) for ts, ln in self.unsent if 0 <= now - ts <= SEND_MAX_AGE]
        if self.unsent and self.send("\n".join(ln for _ts, ln in self.unsent)):
            self.unsent = []
