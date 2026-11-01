"""Watchdog of engine nft: ``kaskadctl net check [--repair]`` (docs/k3/SPEC-wave1.md §3.5).

Runs every minute (kaskad-net-watch.timer) and at boot (ExecStartPost of kaskad-net.service).
Only when ``engine == "nft"``; on engine legacy it is a no-op that runs no command at all.

    1. no table ip kaskad          -> nft.restore (kaskad.nft; no file -> rendered from state)
    2. live forwards != state      -> nft.apply (someone edited the table, ``nft flush ruleset``)
    3. no iptables compat rule     -> compat.ensure (``iptables -F``, ``ufw reload`` drop it)
    4. net.ipv4.ip_forward = 0     -> back to 1 (+ /etc/sysctl.d/90-kaskad.conf)
    5. /usr/local/bin/gokaskad lost ``KASKAD_VERSION="3.`` while /opt/kaskad/bin/gokaskad has it
       (the free script or 2.x was run on top) -> the copy is put back (temp file + rename, 0755)
    6. counters saved into kaskad.nft at most every 10 minutes
    7. the gateway (wave 2), only when it is enabled: its part of the table is already covered by
       step 2 (nft.verify includes the gateway section); here — ip rule/table 200/kgw0, the ip6 table,
       ``kaskad:compat-in``, its services and inbounds (``kaskad.gw.ops.check``)
    8. /usr/sbin/policy-rc.d of Kaskad left by an install of packages that was killed (SIGKILL, OOM, a
       power cut) while apt and dpkg are idle -> removed (aptlock.policy_cleanup): while it stays, no
       package service starts on install or upgrade, openssh-server included
    9. the cover site is on but nginx is stopped or not enabled -> ``systemctl enable --now --no-block nginx``
       (site.ops.nginx_problems; not while kaskad-net is starting at boot)

Without ``repair`` nothing is changed (report only). With ``repair`` the state lock is taken
with a short timeout; if another kaskadctl holds it, the run is skipped (next minute will do).

Log lines (/var/log/kaskad.log): ``NET restore: …`` and ``NET repair: …``. The same kind of
event is logged at most once an hour (spec §3.5 asks it for item 3; applied to every item so a
fight with another tool cannot write a line a minute); the next line tells how many repeats were
not logged. Stamps live in /etc/kaskad/net/watch.json. Right after a reboot (``kaskad.boot.pending``:
no mark in /run yet) what every reboot loses — ip rules and routes of the gateway, the compat rules,
gw_route/gw_live of the boot file older than the exits watchdog's map — is the restore, not a
breakdown: one calm line «NET после перезагрузки: правила шлюза восстановлены (…)» (stand T-G1, R-1).
What could not be repaired is reported as always.
"""

import importlib
import json
import os
import re
import time

from . import __version__, aptlock, boot, compat, nft, paths, state, util
from .errors import KaskadError, LockedError, StateError
from .i18n import get_lang, tr, trn

LOG_EVERY = 3600
COUNTERS_EVERY = 50          # the timer runs every minute; save when counters changed
LOCK_TIMEOUT = 5.0

_V3 = re.compile(r'^KASKAD_VERSION="3\.', re.M)
_VER = re.compile(r'^KASKAD_VERSION="([^"]*)"', re.M)

_now = time.time      # tests patch this


def stamp_file():
    return os.path.join(paths.net_dir(), "watch.json")


class _Stamps(object):
    def __init__(self):
        self.data = {"log": {}, "counters_at": 0}
        self.dirty = False
        raw = util.read_text(stamp_file())
        if raw:
            try:
                d = json.loads(raw)
                if isinstance(d, dict):
                    self.data["log"] = d.get("log") if isinstance(d.get("log"), dict) else {}
                    self.data["counters_at"] = float(d.get("counters_at") or 0)
            except (ValueError, TypeError):
                pass

    def log(self, kind, text, now):
        rec = self.data["log"].get(kind) if isinstance(self.data["log"].get(kind), dict) else {}
        last = float(rec.get("at") or 0)
        skipped = int(rec.get("skipped") or 0)
        if now - last >= LOG_EVERY:
            if skipped:
                text += " " + trn("netcheck.log.repeats", skipped)
            util.log_action(text)
            self.data["log"][kind] = {"at": now, "skipped": 0}
        else:
            self.data["log"][kind] = {"at": last, "skipped": skipped + 1}
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        try:
            util.atomic_write(stamp_file(), util.json_text(self.data), 0o600)
        except OSError:
            pass


def _read(path, limit=4 << 20):
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except (OSError, IOError):
        return None


def command_problem():
    """What replaced the 3.x command, or None if nothing is wrong / nothing to restore from."""
    copy = _read(paths.script_copy())
    if copy is None or not _V3.search(copy.decode("utf-8", "replace")):
        return None
    cur = _read(paths.command())
    if cur is None:
        return tr("netcheck.command.missing")
    text = cur.decode("utf-8", "replace")
    if _V3.search(text):
        return None
    m = _VER.search(text)
    return ("Kaskad %s" % m.group(1)) if m else tr("netcheck.command.free")


def restore_command():
    data = _read(paths.script_copy())
    util.atomic_write(paths.command(), data, 0o755)


WATCH_V = 1


def write_report(problems, now=None):
    """The last report of the minute watchdog (``net check --repair``) for the header of the SSH menu (kaskadctl
    summary): what it found and could NOT repair — {"v", "ts", "problems": [{"kind", "detail", "error"?}]}. The check
    takes seconds; the menu reads this file instead. Nothing secret: the same words the journal gets. The words are in
    the language of the watchdog (the server's); ``lang`` says which, so a reader in another one skips them."""
    left = [dict((k, p[k]) for k in ("kind", "detail", "error") if p.get(k)) for p in problems if not p.get("repaired")]
    try:
        util.atomic_write(paths.watch_json(), json.dumps({"v": WATCH_V, "ts": round(_now() if now is None else now, 3),
                                                          "lang": get_lang(), "problems": left}, ensure_ascii=False), 0o600)
    except OSError:
        pass


def read_report():
    """(problems, age in seconds) of the last report, or (None, None) — none, foreign or broken. A report in another
    language than the reader's (the language was just switched; a report before 3.3.2 is Russian) gives no problems
    (None, age) — as a stale one: the watchdog writes the next within a minute, in the new language."""
    raw = util.read_text(paths.watch_json())
    try:
        doc = json.loads(raw) if raw else None
    except ValueError:
        doc = None
    if not isinstance(doc, dict) or doc.get("v") != WATCH_V or not isinstance(doc.get("problems"), list):
        return None, None
    ts = doc.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return None, None
    age = max(0.0, _now() - float(ts))
    if (doc.get("lang") or "ru") != get_lang():
        return None, age
    probs = [p for p in doc["problems"] if isinstance(p, dict) and isinstance(p.get("detail"), str)]
    return probs, age


def _gateway():
    """kaskad.gw.ops, or None when the gateway is not shipped (imported lazily: it imports nft)."""
    try:
        return importlib.import_module("kaskad.gw.ops")
    except ModuleNotFoundError as e:
        if e.name not in ("kaskad.gw", "kaskad.gw.ops"):
            raise
        return None


def _problem(kind, detail):
    return {"kind": kind, "detail": detail, "repaired": False}


def _fail(p, e):
    p["error"] = str(e)


def check(runner, repair=False):
    """Check (and with ``repair`` fix) the engine. Returns
    ``{"ok": bool, "engine": str, "problems": [{"kind", "detail", "repaired", "error"?}], "skipped": bool}``.
    Report mode: ok = no problems. Repair mode: ok = every problem repaired.
    Kinds: state, nft, table_missing, drift, compat_missing, ip_forward, command, policy_rc_d; the gateway adds
    gw_routing, gw_ip6, gw_compat_in, gw_service, gw_inbound, gw_data (and gw if it could not be checked)."""
    try:
        st = state.load()
    except StateError as e:
        return {"ok": False, "engine": "", "skipped": False,
                "problems": [_problem("state", tr("netcheck.state", error=e))]}
    if st["engine"] != "nft":
        return {"ok": True, "engine": st["engine"], "problems": [], "skipped": False}
    if not repair:
        return _run(st, runner, False)
    try:
        with state.locked(timeout=LOCK_TIMEOUT):
            st = state.load()
            if st["engine"] != "nft":
                return {"ok": True, "engine": st["engine"], "problems": [], "skipped": False}
            res = _run(st, runner, True)
    except LockedError:
        return {"ok": True, "engine": "nft", "problems": [], "skipped": True}
    return _vless_watch(runner, res)


def _vless_watch(runner, res):
    """10. A transition of the VLESS mode left by a dead process (SPEC-vless-tls-default §3.5 п.7, С-1): undone under its
    own lock without waiting (taken — the next run); not while kaskad-net is starting at boot. AFTER the lock of the
    state is let go (SEC-vless-sh12 №1): the undo takes it itself (``gw.ops._change``) — inside it, a nested lock."""
    gw = _gateway()
    if gw is None or gw.booting(runner):
        return res
    try:
        from .site import front
        got = front.recover_watch(runner)
        if got is None:
            return res
        p = _problem("vless_pending", tr("netcheck.vless_pending"))
        if got.get("restored"):
            p["repaired"] = True
        else:
            p["error"] = got.get("why") or "—"
    except (ImportError, KaskadError) as e:
        p = _problem("vless_pending", tr("netcheck.site", error=e))
    res["problems"].append(p)
    res["ok"] = res["ok"] and p["repaired"]
    if not p["repaired"]:
        write_report(res["problems"])
    return res


_DYNAMIC = ("gw_route ", "gw_live ", "gw_rd ")      # the boot file's map of the clients: older than the watchdog's


def _run(st, runner, repair):
    problems = []
    stamps = _Stamps()
    now = _now()
    gw = _gateway()
    after_boot = repair and boot.pending()
    calm = []                       # what a reboot lost and this run put back: one line at the end

    def note(kind, text):
        if after_boot and kind in boot.KINDS:
            calm.append(boot.KINDS[kind])
        else:
            stamps.log(kind, text, now)

    # 1. table present?
    live_error = None
    try:
        live = nft.read_live(runner)
    except KaskadError as e:
        live, live_error = None, e
    # the gateway part missing now (nft flush ruleset, a failed boot load): once it is back, the
    # connections the clients made meanwhile are flushed (step 7 of the gateway, ops.restored)
    gw_missing = live_error is None and gw is not None and gw.model.enabled(st) and not gw.net.part_present(live)
    if live_error is not None:
        problems.append(_problem("nft", tr("netcheck.nft", error=live_error)))
    elif live is None:
        p = _problem("table_missing", tr("netcheck.table_missing"))
        problems.append(p)
        if repair:
            try:
                if nft.restore(runner):
                    live = nft.read_live(runner)
                    p["repaired"] = live is not None
                    if p["repaired"]:
                        stamps.log("table_missing", tr("netcheck.log.restore", file=paths.nft_file()), now)
            except KaskadError as e:
                _fail(p, e)

    # 2. live table == state? (also builds a table that restore could not bring back)
    if live_error is None:
        diffs = nft.verify(st, live)
        p = None
        if diffs and live is not None:
            p = _problem("drift", tr("netcheck.drift", diffs="; ".join(diffs[:5])))
            problems.append(p)
        elif diffs:
            p = problems[-1]            # still the table_missing problem of step 1
        if repair and p is not None:
            # the table was applied by another version (the update replaced the code, the new one wants other rules):
            # planned work after an update, not a breakdown (Н-6)
            was = nft.applied_version()
            planned = p["kind"] == "drift" and was != __version__
            try:
                nft.apply(st, runner)
            except KaskadError as e:
                _fail(p, e)
            else:
                p["repaired"] = True
                if planned:
                    p["planned"] = True
                    stamps.log("update", tr("netcheck.log.update_from", was=was, now=__version__) if was
                               else tr("netcheck.log.update_to", now=__version__), now)
                elif p["kind"] == "drift" and all(d.startswith(_DYNAMIC) for d in diffs):
                    note("drift", tr("netcheck.log.reapplied", detail=p["detail"]))
                else:
                    stamps.log(p["kind"], tr("netcheck.log.reapplied", detail=p["detail"]), now)
                try:
                    live = nft.read_live(runner)
                except KaskadError:
                    live = None

    # 3. iptables compat rule
    if compat.available(runner) and not compat.present(runner):
        p = _problem("compat_missing", tr("netcheck.compat_missing", comment=compat.COMMENT))
        problems.append(p)
        if repair:
            try:
                compat.ensure(runner)
                p["repaired"] = True
                note("compat_missing", tr("netcheck.log.compat"))
            except KaskadError as e:
                _fail(p, e)

    # 4. ip_forward
    r = runner.run(["sysctl", "-n", "net.ipv4.ip_forward"])
    if r.ok and r.out.strip() == "0":
        p = _problem("ip_forward", tr("netcheck.ip_forward"))
        problems.append(p)
        if repair:
            changed, warn = nft.ensure_ip_forward(runner)
            if changed:
                p["repaired"] = True
                note("ip_forward", tr("netcheck.log.ip_forward"))
            else:
                p["error"] = warn

    # 5. the command
    what = command_problem()
    if what:
        p = _problem("command", tr("netcheck.command.replaced", what=what))
        problems.append(p)
        if repair:
            try:
                restore_command()
                p["repaired"] = True
                stamps.log("command", tr("netcheck.log.command"), now)
            except (OSError, TypeError) as e:
                _fail(p, e)

    # 7. the gateway (wave 2): routing, ip6 table, compat-in, services, inbounds — when enabled
    if gw is not None:
        try:
            if repair and gw_missing and gw.net.part_present(live):
                gw.restored(st, runner, log=lambda kind, text: stamps.log(kind, text, now))
            problems.extend(gw.check(st, runner, repair=repair, log=note))
        except KaskadError as e:
            problems.append(_problem("gw", tr("netcheck.gw", error=e)))

    # 8. policy-rc.d of Kaskad left by a killed install of packages (no install of Kaskad runs, apt idle)
    if aptlock.policy_leftover():
        p = _problem("policy_rc_d", tr("netcheck.policy_rc_d", path=paths.policy_rc_d()))
        problems.append(p)
        if repair:
            if aptlock.policy_cleanup():
                p["repaired"] = True
                stamps.log("policy_rc_d", tr("netcheck.log.policy_rc_d", path=paths.policy_rc_d()), now)
            else:
                p["error"] = tr("netcheck.policy_rc_d_busy")

    # 9. the cover site: nginx of a site that is on runs and starts with the system (3.3.0 T-M6 №2). Not while
    #    kaskad-net is still starting at boot: nginx comes up after the network by itself.
    site_on = isinstance(st.get("site"), dict) and st["site"].get("mode") == "site"
    if site_on and not (gw is not None and gw.booting(runner)):
        try:
            from .site import ops as site_ops
            problems.extend(site_ops.nginx_problems(runner, st, repair=repair, log=note))
        except (ImportError, KaskadError) as e:
            problems.append(_problem("site_nginx", tr("netcheck.site", error=e)))

    # 6. counters into kaskad.nft: every run (about once a minute) if they changed, so a crash or a
    #    power cut loses at most a minute of traffic numbers (ExecStop covers clean shutdowns)
    if repair and live is not None and now - stamps.data["counters_at"] >= COUNTERS_EVERY:
        try:
            cur = dict((k, v) for k, v in (live.get("counters") or {}).items() if k.startswith("fwd_"))
            if cur != nft.saved_counters():
                nft.save_counters(runner)
                stamps.data["counters_at"] = now
                stamps.dirty = True
        except KaskadError:
            pass

    if repair:
        line = boot.summary(calm, "NET")
        if line:
            util.log_action(line)
        stamps.save()
        write_report(problems, now)
    ok = all(p["repaired"] for p in problems) if repair else not problems
    return {"ok": ok, "engine": "nft", "problems": problems, "skipped": False}
