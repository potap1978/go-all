"""``kaskadctl gw ...`` — the gateway (docs/k3/SPEC-wave2-gateway.md §7).

    gw status [--json]                         on/off, network, inbounds, exits, DNS, xray, problems
    gw enable [--base 10.77.0.0/16] [--host HOST] / gw disable
    gw host [HOST] [--detect] [--force]        gateway.host — the address clients connect to (in their configs); an IPv4
                                               only one of «Адреса сервера» unless --force (PLAN-multi-ip Ш2, Л3)
    gw addresses [--refresh] / add IP [--local L] / remove IP
                                               «Адреса сервера» (gw/addresses.py): found on the machine, NAT pairs,
                                               added by hand (PLAN-multi-ip Ш1)
    gw addresses in IP… [--yes] | --all        the inbounds take connections on these addresses only / on every one
                                               (Ш3, gw/mip.py: the checks of «Защита доступа»; --yes — the lock of the
                                               panel's domain confirmed, 3.3.7.15 F11; --all — IPv6 too)
    gw addresses in6 IPv6… | --all | --none [--yes]
                                               3.3.7.15 F11: the IPv6 of this server that take connections (an
                                               operational command of the tests and the stand, hidden from MCP)
    gw addresses give6 IPv6|off [--yes]        3.3.7.15 F13: the IPv6 the subscription gives as an extra line (the
                                               same kind of command: tests and the stand, hidden from MCP)
    gw addresses exit ID|--group G IP|main     the address an exit is reached from (Ш4: sendThrough, SNAT of a leg)
    gw apply                                   everything from state
    gw check [--repair] [--quiet]              the gateway part of the watchdog (``net check`` calls it too)
    gw collect [--once] [--interval 10]        the collector (kaskad-gw-collect.service): endless; --once — one pass
    gw sessions                                sessions of the inbounds
    gw exits / gw exit <id> [--enable|--disable] [--name N]
    gw exit <id> --standby | --restore | --maintenance | --accept-country | --enable | --disable [--dry] [--rev N]
    gw exit <id> --quota-gb N [--quota-period month|day] [--quota-reset-day D] [--quota-warn P]
               [--quota-action remove|standby|push] | --no-quota      (stage 1: gw/cli_policy.py, gw.policy_ops)
    gw exit-add [--public-only] / gw exit-preview   a key of an exit from STDIN (never argv): into the pool / what it
                                               holds; --public-only: a subscription link is downloaded here (gw/subfetch.py)
    gw exit-add [--group G]                    stage 1: the new keys into the group G (default — the group of the
                                               new keys); a Russian group (made in the panel) — upstream-ru.txt (T15)
    gw exit-del <id> [--dry] [--rev N]         the key of an exit out of the pool (the way the panel does it)
    gw groups | policy | policy-reset | exit-move | group-del | group-flap-standby | exit-history | exit-speedtest
                                               the exit policy and the groups of stage 1 (gw/cli_policy.py)
    gw exit-sub add [--manual] | list | del <sub> [--keep-keys] | refresh [<sub>] [--auto] [--quiet]
               | restore <sub> | auto <sub> on|off             subscriptions of the exits (gw/exitsubs.py): the address on STDIN (a
                                               secret), the daily timer kaskad-exit-sub, only the keys of the
                                               subscription change; <sub> — s-xxxxxx, its number or its domain
    gw scheme                                  people → devices → inbounds → rules → routes → exits, compact (read-only)
    gw route <via> | --via V [--fail block|direct]   the default route (pool, group:<group id>, exit:<exit id>,
                                               direct, block); «block» of a route through the exits — as its group
    gw hook <kind> up|down <identity> <ip>     for daemons (strongSwan updown, ocserv connect)
    gw inbound list | install <kind> [--port N] [--force] [--new-only] | uninstall <kind> | enable <kind>
               | disable <kind> | port <kind> <N> [--proto udp|tcp] [--force]
               | plan [kind ...]           ports the install wizard proposes and what holds them (read-only)
               | option <kind> <key> on|off   l2tp plain (L2TP without IPsec), socks5 udp
    gw user add <name> [--route V] | del <user> [--force] | list | route <user> <via> | enable | disable
    gw device add <user> <name> --kind K --platform P [--access a,b] [--route V] | del | list [--user U]
               | grant <dev> <kind> | revoke <dev> <kind> | route <dev> <via> | enable | disable
               | reset <dev> [--inbound K]     new keys, passwords, certificates; the device's sessions are cut
    gw issue <dev> [--inbound K] [--os O] [--png] [--where W]   settings for the device — the ONLY command printing
                                               secrets; --png: QR codes as PNG in base64 (records qr and link: png_b64);
                                               --where: whose words the how-to speaks (menu, panel, page, bot, agent)
    gw pki rotate ikev2 --yes                  a new IKEv2 CA (constrained to gateway.host): issue IKEv2 again
    gw vless show | masks [--check] | mask <site|own|auto> [--no-check] | rekey --yes
                                               VLESS Reality (docs/k3/SPEC-vless-inbound.md): what the clients get (no
                                               secrets), the cover sites (checked from the node: TLS 1.3), the cover
                                               site of the inbound, a new key and shortId of the node
    gw offers --platform P                     what to give a device of the platform first (spec §4)
    gw trace <device|person|-> <domain|IP> [--inbound K]   «Куда пойдёт трафик?»: the path through ONE inbound of
                                               the device (default: the first enabled), step by step (gw/trace.py)
    gw probe                                   can the gateway be switched on here (code 5: no)
    gw purge [--users] --yes                   the whole gateway away; users and devices only with --users
    gw accel status | on | off                 AmneziaWG on the kernel module (DKMS) or amneziawg-go (gw/awgkmod.py)
             | boot [--quiet] | check FILE     at boot (kaskad-gw-awgkmod.service); a leg's config fits the module
             | upgrade [--quiet]               after an update: the module without the set of 3.1 gets it
    gw awg status | check                      the engine of AmneziaWG (gw/awgengine.py): module of the kernel or the
           | engine kernel|program [--force] [--dry-run] [--why W]   program; the switch (backup, check, rollback)
           | first [--program] | offer       the first AmneziaWG inbound/leg (install) / after an update (pending)
           | export --file F | import --file F   people, devices, AmneziaWG inbounds and their keys (0600, secrets)
    gw mtu [show] | reissue <kind> | s4 awg2 [--yes] | keep <kind>   safe MTU (gw/mtu.py, §6.1): the card, the
                                               budget, server/client MTU of the inbounds and the legs; a card too narrow
                                               for the clients — new settings with a smaller MTU; a server below 1232 —
                                               new parameters with a smaller S4 (the devices first); or as it is

Every command takes --json (exactly one JSON document). Exit codes — kaskad.errors (3 = port conflict,
5 = not available here / engine legacy). Texts are short Russian; secrets never appear except in issue.
"""

import argparse
import calendar
import os
import re
import sys
import time

from .. import errors, qr, state, sysrun, util
from ..cli import add_json_flag, emit
from ..i18n import fmt_datetime, fmt_num, lower_first, tr, trn
from . import awgengine, awgkmod, collect, exits, exitsubs, hooks, model, ops, pending, rename
from . import mtu as gwmtu

_now = time.time                    # tests patch it


def _r():
    return sysrun.default_runner()


def since_text(value, now=None):
    """The «с» column of ``gw sessions``: ``HH:MM`` today, otherwise kaskad.i18n.fmt_datetime (local time). Adapters
    give epoch seconds (an int, maybe as a string), hooks an ISO time in UTC (``2026-10-04T12:00:00Z``);
    anything else is shown as it is, nothing — as an empty column. JSON keeps the raw value."""
    if value is None or value == "" or isinstance(value, bool):
        return ""
    ts = None
    if isinstance(value, (int, float)):
        ts = float(value)
    else:
        s = str(value).strip()
        if re.match(r"^\d+(\.\d+)?$", s):
            ts = float(s)
        else:
            m = re.match(r"^(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?(?:\.\d+)?Z$", s)
            if m:
                try:
                    ts = calendar.timegm(tuple(int(x or 0) for x in m.groups()) + (0, 0, 0))
                except (ValueError, OverflowError):
                    ts = None
            if ts is None:
                return s
    if ts <= 0:
        return ""
    try:
        t = time.localtime(ts)
        today = time.localtime(_now() if now is None else now)
    except (ValueError, OverflowError, OSError):
        return str(value)
    if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("%H:%M", t)
    return fmt_datetime(ts, now=_now() if now is None else now)       # «04.10, 12:30» / «Oct 4, 12:30»


def _warn(text):
    """A warning to stderr: «Внимание: …» / «Warning: …» for a person; with K3_MARKS=1 (the SSH menu parses stderr,
    k3/handoff/I18N-3.3.2.md «Обмен меню ↔ ядро») the mark «@K3:warn …» instead of a word of a language."""
    if _marks():
        sys.stderr.write("@K3:warn %s\n" % text)
    else:
        sys.stderr.write(tr("cli.warning", text=text) + "\n")


def _warnings(res):
    for w in ((res or {}).get("warnings") or []) + (((res or {}).get("apply") or {}).get("warnings") or []):
        _warn(w)


def fail_shown(r):
    """The fail policy is said only where it acts — a route through the exits (pool, group:<gid>, exit:<id>:
    ``exits.via_exits``, REV-s1-t4 З6). «block» and «direct» may keep a policy (model.kept_fail: it comes back with the
    pool) — it means nothing there and is not shown."""
    return exits.via_exits(r)


USER_INHERIT = "gw_cli.route.inherit_node"          # a key: tr() at the time of output


def _route_text(r, inherit="gw_cli.route.inherit_person", st=None):
    """A route for people; ``r`` None — no own route: a device takes its person's («как у человека»), a person —
    the node's default (``inherit`` «как по умолчанию узла»). The fail step as the decision takes it: the route's own
    policy or the one of its group (``exits.fail_of``, REV-s1-b2 З1) — ``st`` the state (None: read when needed)."""
    if not r:
        return tr(inherit)
    direct = False
    if fail_shown(r) or str(r.get("via") or "").startswith(model.GROUP_VIA):
        st = st if st is not None else state.load()
    if fail_shown(r):
        direct = r.get("fail") == "direct" or exits.fail_of(st, r) == "direct"
    via = str(r.get("via") or "")
    if via.startswith(model.GROUP_VIA):                 # Н8 (T-KT1): the name of the group, as «gw trace», not group:g-…
        from . import policy as P
        via = tr("gw_trace.via.group", name=P.label(P.group(P.load(st), via[len(model.GROUP_VIA):])
                                                    or {"id": via[len(model.GROUP_VIA):]}))
    return "%s%s" % (via, tr("gw_cli.route.fail_direct") if direct else "")


def exit_line(e):
    """«AmneziaWG 3.1 · 203.0.113.141 — не работает: нет ответа 35 с — клиенты переходят на другой выход»,
    «Вена (VLESS · 198.51.100.31) — работает» — an ``ops.exits_view`` row for people."""
    # К2-7 (T-KT2): off for the gateway — only that (not checked: «работает (выключен…)» told an old probe)
    text = exits.state_text({"enabled": False}) if e.get("enabled", True) is False and not e.get("problem") \
        else (e.get("text") or exits.state_text(e))
    return "%s%s — %s%s" % (e["name"], (" (%s)" % e["where"]) if e.get("named") and e.get("where") else "", text,
                            " [%s]" % e["source"]["name"] if e.get("source") else "")


def _core_text(row):
    """«, ядро» / «, программа» of an AmneziaWG inbound (the kernel module or amneziawg-go), else nothing."""
    core = row.get("core")
    return (", " + awgkmod.CORE_TEXT[core]) if core in awgkmod.CORE_TEXT else ""


def _on_off(on):
    """«включён» / «выключен» (masculine: the gateway, an inbound, VLESS)."""
    return tr("gw_cli.word.on") if on else tr("gw_cli.word.off")


def _active_text(active):
    return tr("gw_cli.state.running") if active else tr("gw_cli.state.not_running")


def _run_text(r):
    """«выключен» / «работает» / «НЕ работает» of an installed inbound."""
    if not r["enabled"]:
        return tr("gw_cli.word.off")
    return tr("gw_cli.state.running") if r["running"] else tr("gw_cli.state.down")


# ── on/off, status ─────────────────────────────────────────────────────────

def cmd_status(args):
    doc = ops.status(_r())
    lines = [tr("gw_cli.status.gateway", state=_on_off(doc["enabled"])),
             tr("gw_cli.status.net", base=doc["net"]["base"], dns=doc["net"]["dns"],
                host=doc["host"] or tr("gw_cli.host.unset")),
             tr("gw_cli.status.counts", users="%d" % doc["users"], devices="%d" % doc["devices"])]
    inst = [r for r in doc["inbounds"] if r["installed"]]
    lines.append(tr("gw_cli.inbounds", list=tr("gw_cli.word.none") if not inst else ""))
    for r in inst:
        lines.append("  " + tr("gw_cli.status.inbound", title=r["title"], kind=r["kind"], state=_run_text(r),
                               core=_core_text(r), ports=", ".join(r["ports"]) or "—", sessions="%d" % r["sessions"],
                               devices="%d" % r["devices"]))
        for w in r.get("warnings") or []:
            lines.append("    ! %s" % w)
    doc["accel"] = {"enabled": awgkmod.enabled(), "loaded": awgkmod.module_loaded()}
    eng = awgengine.status(_r())
    doc["awg_engine"] = eng
    doc["accel"]["warnings"] = eng["warnings"]
    if eng["has_awg"] or doc["accel"]["enabled"]:
        lines.append(lower_first(eng["text"]))
        for w in eng["warnings"]:
            lines.append("  ! %s" % w)
        if eng["offer"]["show"] and not eng["offer"]["blocked"]:
            lines.append("  " + tr("gw_cli.status.kernel_offer"))
    if doc.get("mtu") and (doc["mtu"]["inbounds"] or doc["mtu"]["legs"]):
        lines += gwmtu.status_lines(doc["mtu"], with_text=False)
    ex = doc.get("exits") or []
    lines.append(tr("gw_cli.status.exits", total="%d" % len(ex), working="%d" % sum(1 for e in ex if e.get("usable")),
                    error=(" (%s)" % doc["exits_error"]) if doc.get("exits_error") else ""))
    for e in ex:
        lines.append("  " + exit_line(e))
    lines.append("DNS: %s%s" % (_active_text(doc["dns"]["active"]),
                                tr("gw_cli.status.aaaa") if doc["dns"].get("filter_aaaa") else ""))
    if doc["dns"].get("note"):
        lines.append("  %s" % doc["dns"]["note"])
    lines.append("xray: %s" % _active_text(doc["xray"]["active"]))
    if doc.get("rules"):
        lines.append(tr("gw_cli.status.rules", line=doc["rules"].get("line", "")))
    for p in doc.get("problems") or []:
        lines.append(tr("gw_cli.status.problem", detail=p["detail"]))
    # stage 1 (T10, the screen «Состояние» 17 → 5): where the default route goes when its exits fail — its group's
    # step («как у группы (блок | с сервера напрямую)»), as in 17 → 3; unreadable — None, the screen of 3.3.5
    st = state.load()
    doc["default_route"] = safe_route_doc(st)
    doc["groups"] = groups_brief(st)
    emit(args, doc, lines)
    return errors.OK


def cmd_enable(args):
    res = ops.enable(_r(), base=args.base, host=args.host, known_only=not args.force)
    _warnings(res)
    emit(args, dict(res, ok=True), tr("gw_cli.enabled", base=res["base"], dns=_dns_of_base(res["base"]),
                                      host=res["host"] or tr("gw_cli.host.unset")))
    return errors.OK


def cmd_host(args):
    res = ops.host(_r(), value=args.value, detect=args.detect, known_only=not args.force)
    _warnings(res)
    if args.value or args.detect:
        text = tr("gw_cli.host.changed", host=res["host"])
    else:
        text = tr("gw_cli.host.show", host=res["host"] or tr("gw_cli.host.unset"),
                  auto=tr("gw_cli.host.auto") if res["auto"] else "")
    emit(args, res, text)
    return errors.OK


def cmd_addresses(args):
    from . import addresses, mip
    doc = addresses.collect(_r(), refresh=args.refresh)
    out = dict(doc, ok=True, addresses=[dict(it, text=addresses.item_text(it)) for it in doc["addresses"]])
    st = state.load()
    pool = _exit_pool()
    md = mip.status(st, pool_exits=pool)                # Ш3/Ш4: where the inbounds take connections, the exits
    names = dict((e["id"], exits.label(st, e)) for e in pool)
    out["mip"] = dict(md, lines=mip.lines(md, names), in_lines=mip.in_lines(md), src_lines=mip.src_lines(md, names),
                      pool=[{"id": e["id"], "name": names[e["id"]], "kind": e.get("kind"),
                             "src": mip.exit_src(st, e["id"])} for e in pool if e.get("kind") in ("awg", "xray")])
    emit(args, out, addresses.lines(doc) + mip.lines(md, names))
    return errors.OK


def _exit_pool():
    try:
        return exits.pool(_r(), roles=exits.ROLES)
    except errors.KaskadError:
        return []


def cmd_addresses_in(args):
    """Ш3: ``gw addresses in IP… | --all`` — the inbounds take connections on these addresses / on every one again."""
    from . import mip
    if args.all and args.ips:
        raise errors.KaskadError(tr("gw_mip.err.in_both"), code=errors.USAGE)
    res = mip.set_in(_r(), ips=args.ips, every=bool(args.all), yes=bool(args.yes))
    _warnings(res)
    emit(args, res, res["text"])
    return errors.OK


def cmd_addresses_in6(args):
    """F11: ``gw addresses in6 IPv6… | --all | --none [--yes]`` — the IPv6 of this server the inbounds take connections
    on (``mip.set_in6``)."""
    from . import mip
    res = mip.set_in6(_r(), ips=args.ips, every=bool(args.all), none=bool(args.none), yes=bool(args.yes))
    _warnings(res)
    emit(args, res, res["text"])
    return errors.OK


def cmd_addresses_give6(args):
    """F13: ``gw addresses give6 IPv6|off [--yes]`` — the IPv6 of this server the subscription gives as an extra line
    (``mip.set_give6``)."""
    from . import mip
    res = mip.set_give6(_r(), ip=args.ip, yes=bool(args.yes))
    _warnings(res)
    emit(args, res, res["text"])
    return errors.OK


def cmd_addresses_exit(args):
    """Ш4: ``gw addresses exit ID|--group G IP|main`` — the address an exit (every exit of a group) is reached from."""
    from . import mip
    if (args.group is None) == (args.id is None):
        raise errors.KaskadError(tr("gw_mip.err.exit_or_group"), code=errors.USAGE)
    res = mip.set_src(_r(), [args.id] if args.id else [], ip=args.ip, group=args.group)
    _warnings(res)
    emit(args, res, res["text"])
    return errors.OK


def cmd_addresses_add(args):
    from . import addresses
    res = addresses.add(_r(), args.ip, local=args.local)
    emit(args, res, res["text"])
    return errors.OK


def cmd_addresses_remove(args):
    from . import addresses
    res = addresses.remove(args.ip)
    emit(args, res, res["text"])
    return errors.OK


def _dns_of_base(base):
    from . import addr
    return addr.dns_ip({"gateway": {"net": {"base": base}}})


def cmd_disable(args):
    res = ops.disable(_r())
    _warnings(res)
    emit(args, res, tr("gw_cli.disabled"))
    return errors.OK


def cmd_apply(args):
    res = ops.apply_all(_r())
    _warnings(res)
    emit(args, dict(res, ok=True), tr("gw_cli.applied"))
    return errors.OK


def cmd_check(args):
    runner = _r()
    if args.repair:
        with state.locked():
            st = state.load()
            probs = ops.check(st, runner, repair=True)
    else:
        st = state.load()
        probs = ops.check(st, runner, repair=False)
        # «нет интернета у устройств» — то же правило, что красная строка меню 17; не чинится само, поэтому только
        # в проверке без --repair (сторож с --repair не будет каждую минуту писать «не исправлено»)
        try:
            dead = ops.no_exits_problem(st, ops.exits_view(runner, st))
        except errors.KaskadError:
            dead = None
        if dead:
            probs.append(dead)
    ok = all(p["repaired"] for p in probs) if args.repair else not probs
    doc = {"ok": ok, "problems": probs}
    if args.json:
        emit(args, doc)
    elif args.quiet:
        for p in probs:
            if args.repair and not p["repaired"]:
                sys.stderr.write(tr("gw_cli.check.not_repaired_line", detail=p["detail"],
                                    error=(" (%s)" % p["error"]) if p.get("error") else "") + "\n")
    else:
        if not probs:
            lines = [tr("gw_cli.check.ok")]
        else:
            lines = [tr("gw_cli.check.problems")]
            for p in probs:
                mark = ""
                if args.repair:
                    mark = tr("gw_cli.check.repaired") if p["repaired"] else tr(
                        "gw_cli.check.not_repaired", error=(": %s" % p["error"]) if p.get("error") else "")
                lines.append("  • %s%s" % (p["detail"], mark))
        emit(args, doc, lines)
    return errors.OK if ok else errors.ERROR


# Texts of the module as names (COLLECT_HELP, KEY_PROMPT …): a tr() in the language of the moment (module __getattr__
# below), never frozen at import
_TEXTS = {"COLLECT_HELP": ("gw_cli.collect.help", ""), "COLLECT_TTY_TEXT": ("gw_cli.collect.tty", "\n"),
          "KEY_PROMPT": ("gw_cli.exit.key_prompt", "\n"), "SUB_PROMPT": ("gw_cli.sub.prompt", "\n")}


def __getattr__(name):
    if name in _TEXTS:
        key, tail = _TEXTS[name]
        return tr(key) + tail
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def cmd_collect(args):
    runner = _r()
    if args.once:
        res = collect.tick(runner)
        emit(args, dict(res, ok=True), tr("gw_cli.collect.done") if res.get("enabled") else tr("gw_cli.gateway_off"))
        return errors.OK
    try:
        if sys.stderr.isatty():                     # a person ran it by hand (T-X6: it «hung» twice) — say so at once
            sys.stderr.write(tr("gw_cli.collect.tty") + "\n")
            sys.stderr.flush()
    except (AttributeError, ValueError):
        pass
    collect.loop(runner, interval=args.interval)
    return errors.OK


def cmd_sessions(args):
    rows = ops.sessions(_r())
    lines = [tr("gw_cli.sessions.none")] if not rows else ["%-8s %-16s %-24s %s" % (
        tr("gw_cli.sessions.col.inbound"), tr("gw_cli.sessions.col.address"), tr("gw_cli.sessions.col.device"),
        tr("gw_cli.sessions.col.since"))]
    now = _now()
    for s in rows:
        lines.append("%-8s %-16s %-24s %s" % (s["kind"], s.get("ip") or "", s.get("name") or s.get("device") or "?",
                                              since_text(s.get("since"), now)))
    emit(args, {"ok": True, "sessions": rows}, lines)
    return errors.OK


# ── the activity of devices (k3/handoff/SPEC-activity.md §7): read only, from metrics.db of this node ──

ACT_RANGES = ("1d", "7d", "30d")


def _act_who(st, ref):
    """A person (u-…, name) or a device (d-…, login, name) -> ("user", u) | ("device", d): an id is exact; a login of
    a device, then a name of a person, then a name of a device; none or ambiguous -> NotFound."""
    ref = str(ref)
    u = model.find_user(st, ref)
    d = model.find_device(st, ref)
    if u is not None and u.get("id") == ref:
        return "user", u
    if d is not None and (d.get("id") == ref or d.get("login") == ref):
        return "device", d
    if u is not None:
        return "user", u
    if d is not None:
        return "device", d
    raise model.NotFound(tr("gw_cli.activity.not_found", ref=ref))


def _act_bytes(n):
    n = max(0, int(n or 0))
    if n >= 2 ** 30:
        return tr("gw_cli.activity.gb", n=fmt_num(n / 2.0 ** 30, digits=1))
    if n >= 2 ** 20:
        v = n / 2.0 ** 20
        return tr("gw_cli.activity.mb", n=fmt_num(v, digits=1 if v < 10 else 0))
    return tr("gw_cli.activity.kb", n="%d" % int(round(n / 1024.0)))


def _act_buckets(h, rng):
    """The points of a history -> [(bytes, no data)] by the hour (1d: 24) or by the day of the zone of the node (7d,
    30d: 7, 30) — the text line of §7."""
    from .. import tz
    z = tz.zone()
    out, keys = {}, []
    for i in range(h["n"]):
        ts = h["t0"] + i * h["step"]
        k = ts // 3600 if rng == "1d" else tz.day(ts, z)
        if k not in out:
            out[k] = [0, True]
            keys.append(k)
        out[k][0] += h["rx"][i] + h["tx"][i]
        out[k][1] = out[k][1] and not h["cover"][i]
    want = {"1d": 24, "7d": 7, "30d": 30}[rng]
    return [tuple(out[k]) for k in keys[-want:]]


def _act_state(d, live_dev, last, now):
    """«в сети с 15:05» / «был в 13:40» / «был вчера в 13:40» / «был 3 дня назад» / «ещё не подключалось» /
    «выключено» — by the clock of the node."""
    from .. import tz
    if d.get("enabled") is False:
        return tr("gw_cli.activity.off_dev")
    if live_dev.get("online") and live_dev.get("since"):
        return tr("gw_cli.activity.online", t=time.strftime("%H:%M", tz.local(live_dev["since"])))
    if not last:
        return tr("gw_cli.activity.never")
    days = (tz.to_utc(*tz.local(now)[:3], 0, 0) - tz.to_utc(*tz.local(last)[:3], 0, 0)) // 86400
    t = time.strftime("%H:%M", tz.local(last))
    if days <= 0:
        return tr("gw_cli.activity.was", t=t)
    if days == 1:
        return tr("gw_cli.activity.was_y", t=t)
    return trn("gw_cli.activity.was_n", int(days))


def cmd_activity(args):
    """``gw activity <человек|устройство> [--range 1d|7d|30d] [--json]`` (SPEC-activity §7): the totals of the period
    and a line of 24 hours (or 7 / 30 days) a device; ``--json`` — the answer of the API (§5.2) with ``who``."""
    from . import live as glive, metrics
    st = state.load()
    kind, obj = _act_who(st, args.who)
    who = {"kind": kind, "id": obj.get("id"), "name": obj.get("name")}
    if not model.activity_keep(st) or not metrics.available():
        why = "off" if not model.activity_keep(st) else "nosqlite"
        emit(args, {"ok": True, "activity": False, "why": why, "who": who}, tr("gw_cli.activity." + why))
        return errors.OK
    devs = [obj] if kind == "device" else model.devices(st, user=obj.get("id"))
    ids = set(d.get("id") for d in devs if d.get("id"))
    h = metrics.dev_history(ids, args.range, per_device=kind == "user")
    emit_doc = dict(h, ok=True, activity=True, who=who, range=args.range)
    if args.json:
        emit(args, emit_doc)
        return errors.OK
    now = _now()
    doc, _age = glive.read()
    lv = (doc or {}).get("devices") or {}
    lasts = metrics.dev_summary(ids, now).get("devices") or {}
    rx, tx = sum(h["rx"]), sum(h["tx"])
    head = [obj.get("name") or obj.get("id")]
    if kind == "user":
        head.append(trn("gw_cli.activity.devices", len(devs)))
    else:
        owner = model.find_user(st, obj.get("user"))
        if owner is not None:
            head.append(owner.get("name") or owner.get("id"))
    head.append(tr("gw_cli.activity.total_" + args.range, v=_act_bytes(rx + tx), dn=_act_bytes(tx), up=_act_bytes(rx)))
    head.append(tr("gw_cli.activity.on_for", d=glive._dur(sum(h["on_s"]), None) or "0"))
    head.append(tr("gw_cli.activity.act_for", d=glive._dur(sum(h["act_s"]), None) or "0"))
    lines = [" · ".join(head)]
    if h["since"] and h["since"] > h["t0"]:
        from .. import tz
        lines.append("  " + tr("gw_cli.activity.since", d=time.strftime("%Y-%m-%d %H:%M", tz.local(h["since"]))))
    from .. import activity as A
    step = 3600 if args.range == "1d" else 86400
    rows = []
    for d in sorted(devs, key=lambda x: str(x.get("name") or "").casefold()):
        one = h if kind == "device" else metrics.dev_history({d.get("id")}, args.range)
        b = _act_buckets(one, args.range)
        rows.append((str(d.get("name") or d.get("id")),
                     _act_state(d, lv.get(d.get("id")) or {}, (lasts.get(d.get("id")) or {}).get("last"), now),
                     A.spark([x[0] for x in b], [x[1] for x in b], step), _act_bytes(sum(one["rx"]) + sum(one["tx"]))))
    if not rows:
        lines.append("  " + tr("gw_cli.activity.no_devices"))
    wn = max([len(r[0]) for r in rows] + [1])
    ws = max([len(r[1]) for r in rows] + [1])
    for name, st_text, line, vol in rows:
        lines.append("  %s  %s  %s  %s" % (name.ljust(wn), st_text.ljust(ws), line, vol))
    emit(args, emit_doc, lines)
    return errors.OK


# ── exits and routes ───────────────────────────────────────────────────────

def default_route_doc(st):
    """The default route for the SSH menu and agents (T10): ``exits.route_view`` (``fail_eff`` — where it goes when
    the exits fail, as it acts) with ``group`` and ``group_name`` — the group it resolves to (the pool — «main»,
    exit:E — the group of E) — and ``group_fail``: the step of that group itself («Как у группы»)."""
    from . import policy as P
    pol = exits.local_policy(st)
    r = exits.default_route(st)
    out = exits.route_view(st, r, pol)
    if isinstance(out, dict) and exits.via_exits(r):
        gid, grp, _missing = exits.route_group(st, r, {"policy": pol, "states": {}})
        out["group"] = gid
        out["group_name"] = P.label(grp or {"id": gid})
        out["group_fail"] = (grp or {}).get("fail") if (grp or {}).get("fail") in model.FAILS else "block"
    return out


def groups_brief(st):
    """The groups for the SSH menu (T10, §7.6): {id, label, role, fail} — «Как у группы (блок)», a route group:<gid> by
    its name; the group of each exit is the ``group`` of its row."""
    from . import policy as P, policy_ops as pops
    try:
        doc = pops._base(st)
    except Exception:                                   # noqa: BLE001 — no policy readable: the words of 3.3.5
        return []
    return [{"id": g.get("id"), "label": P.label(g), "role": g.get("role", "abroad"),
             "fail": g.get("fail") if g.get("fail") in model.FAILS else "block"}
            for g in doc.get("groups") or [] if isinstance(g, dict) and g.get("id")]


def cmd_exits(args):
    from . import policy_ops as pops
    st = state.load()
    # T-3379 М-1: the Russian exits (upstream-ru.txt) too — apart, in ``ru_exits`` and under their own line: ``exits``
    # stays the foreign pool its readers (the SSH menu: routes «только через выход», on/off, the wizard; MCP) expect
    both = ops.exits_view(_r(), st, roles=exits.ROLES)
    rows = [e for e in both if e.get("role") != "ru"]
    ru_rows = [e for e in both if e.get("role") == "ru"]
    try:
        pops.exit_fields(st, both)                      # stage 1: health, reasons, group, tier, devices, rtt … (§7.7)
    except Exception as e:                              # noqa: BLE001 — the rows of 3.3.5 still show
        util.log_action("GW exits: the fields of stage 1 not read (%s)" % type(e).__name__)
    if not rows:
        lines = [tr("gw_cli.exits.none", hint=exits.add_hint(st)["text"])]
        if exits.offline_empty(st, 0):
            lines.append(exits.NO_EXITS_OFFLINE)
    else:
        lines = []
        for e in rows:
            lines.append("%s %-2s %s%s" % (e["id"], e["index"], exit_line(e), stage1_marks(e)))
        # 3.3.5 №4: one key twice (by IP and by domain — a pool of 3.3.4): told, never removed by itself
        lines.extend(exits.same_peer_problems(st, rows))
    if ru_rows:
        lines.append(tr("gw_cli.exits.ru_head", n=len(ru_rows)))
        for e in ru_rows:
            lines.append("%s %-2s %s%s" % (e["id"], e["index"], exit_line(e), stage1_marks(e)))
    subs = exitsubs.listing()
    for r in subs:
        if r["stale"]:
            # "Subscription subscription 1:" (stand T-334): the word is in the text already — the number or the domain
            lines.append(tr("gw_cli.exits.sub_stale", name=r.get("domain") or r.get("n") or r["name"], text=r["text"]))
    emit(args, {"ok": True, "exits": rows, "ru_exits": ru_rows, "subs": subs, "default_route": safe_route_doc(st),
                "groups": groups_brief(st)}, lines)
    return errors.OK


def safe_route_doc(st):
    """``default_route_doc`` for a listing: a policy it cannot read (``tiers[].exits`` a string — a hand edit, a
    foreign copy) is a line in the journal and None, never the whole list down (REV-s1-t10-sec №3, as ``exit_fields``)."""
    try:
        return default_route_doc(st)
    except Exception as e:                              # noqa: BLE001 — the rows still show
        util.log_action("GW exits: the default route of stage 1 not read (%s)" % type(e).__name__)
        return None


def stage1_marks(e):
    """The marks of stage 1 after the state of an exit, in words from its fields (правка 5, З1; not from events):
    «отвечает снова · новые устройства — с ЧЧ:ММ» (``recovering``), «в запасе — подтвердите (новый ключ подписки)»
    (``manual_why`` sub_confirm), «вручную: в запасе» / «обслуживание» (``manual``). «нестабилен» is said by the text
    of the state already (ops.reserve_text)."""
    from . import live
    out = []
    if e.get("recovering") and e.get("off_until"):
        out.append(tr("ep.show.recovering", time=live._clock(e["off_until"])))
    if e.get("manual_why") == "sub_confirm":
        out.append(tr("gw_cli.exits.sub_confirm"))
    elif e.get("manual") in ("standby", "maintenance"):
        out.append(tr("gw_cli.exits.manual_" + e["manual"]))
    return "".join(" · " + x for x in out)


def cmd_exit(args):
    from . import cli_policy
    acts = [f for f in ("enable", "disable", "standby", "restore", "maintenance", "accept_country", "no_quota")
            if getattr(args, f, False)] + (["quota"] if cli_policy.quota_given(args) else [])
    if len(acts) > 1 or (acts and args.name is not None and acts[0] not in ("enable", "disable")):
        raise errors.KaskadError(tr("gw_cli.exit.one_flag"), code=errors.USAGE)
    if args.name is not None and (args.dry or args.rev is not None):
        raise errors.KaskadError(tr("gw_cli.exit.one_flag"), code=errors.USAGE)
    if not acts and args.name is None:
        raise errors.KaskadError(tr("gw_cli.exit.need_flag"), code=errors.USAGE)
    if args.name is not None and acts:
        # --enable/--disable with a name (REV-3371 П-2): the name checked first (refused — nothing done), then the
        # action through the button of the panel (REV-s1-t10 №1), then the light rename of T13; both results said
        rename.checked_exit_name(args.id, args.name, args.allow_duplicate)
        return cli_policy.exit_action(args, then=lambda: ops.exit_set(
            _r(), args.id, name=args.name, allow_duplicate=args.allow_duplicate, via=args.via)["rename"])
    if args.name is not None:
        # the name alone — the path of T13 (ops.exit_set → rename.rename: the light write, the check of a duplicate,
        # --via in the journal)
        res = ops.exit_set(_r(), args.id, name=args.name, allow_duplicate=args.allow_duplicate, via=args.via)
        _warnings(res)
        if res.get("rename"):                       # the result of the rename, its hint
            emit(args, dict(res["rename"], pref=res["pref"]), rename.said(res["rename"]))
            return errors.OK
        emit(args, res, tr("gw_cli.exit.saved", name=cli_policy._exit_name(args.id)))
        return errors.OK
    # every action of the exit — --enable/--disable too, with or without --dry/--rev — through policy_ops, the path of
    # the button of the panel (/enabled: the bot's proxy rebuilt, «GW policy» in the journal; REV-s1-t10 №1)
    return cli_policy.exit_action(args)


# ── renames (T13, SPEC-stage1-rename §4.2): the name changes, the id never ─────────────────────────────────────────

def _renamed(args, what, ref):
    res = rename.rename(_r(), what, ref, args.name, allow_duplicate=args.allow_duplicate, via=args.via)
    emit(args, res, rename.said(res))
    return errors.OK


def cmd_user_rename(args):
    return _renamed(args, "user", args.user)


def cmd_device_rename(args):
    return _renamed(args, "device", args.device)


def cmd_inbound_label(args):
    return _renamed(args, "inbound", args.kind)


def cmd_exit_sub_rename(args):
    return _renamed(args, "exitsub", args.sub)


def rename_args(p, required=True):
    """--name=<new> (a leading «-» — through «=» or after «--»), --allow-duplicate, --via (who asks: the journal)."""
    p.add_argument("--name", required=required, default=None, help=tr("gw_cli.help.rename_name"))
    p.add_argument("--allow-duplicate", action="store_true", help=tr("gw_cli.help.allow_duplicate"))
    p.add_argument("--via", default="cli", choices=rename.VIA, help=tr("gw_cli.help.via"))
    return p


def _key_from_stdin():
    """The key of an exit from stdin only (``/proc/<pid>/cmdline`` is open to everyone): at most KEY_MAX + 1 characters
    — longer is refused by ``ops.key_text`` without reading the rest."""
    if sys.stdin is None:
        raise errors.KaskadError(tr("gw_cli.exit.key_stdin"), code=errors.USAGE)
    try:
        if sys.stdin.isatty():
            sys.stderr.write(tr("gw_cli.exit.key_prompt") + "\n")
            sys.stderr.flush()
    except (AttributeError, ValueError):
        pass
    return sys.stdin.read(ops.KEY_MAX + 1)


def cmd_exit_add(args):
    pin = tofu_pin(args.pin_tofu) if args.pin_tofu is not None else None
    text = _key_from_stdin()
    if getattr(args, "public_only", False):
        # ссылки подписки скачиваются здесь, безопасно (gw/subfetch.py), и дальше идёт уже текст ключей: install.sh
        # качал бы их curl -L — за редиректами и с новым поиском адреса (стенд T-M6, F1)
        from . import subfetch
        text = subfetch.expand(text)
    res = ops.exit_add(_r(), text, group=args.group or None, pin_tofu=pin, by=args.pin_by if pin else "cli")
    text = None
    for w in res.get("warnings") or []:
        _warn(w)
    lines = [res["text"]] + ["  %s — %s" % (a["id"], a["name"]) for a in res["added"]] + list(res.get("notes") or [])
    emit(args, res, lines)
    return errors.OK


def tofu_pin(value):
    """``--pin-tofu`` (§7.7, §9.1): the SHA-256 fingerprint the preview showed (``gw exit-preview --tofu``), 64 hex,
    colons allowed; anything else — USAGE before the key is read. The core takes the fingerprint again and compares it
    (``gw.tofu.consent``): another one — a refusal, nothing pinned."""
    v = str(value or "").strip().lower().replace(":", "")
    if not re.match(r"^[0-9a-f]{64}$", v):
        raise errors.KaskadError(tr("gw_tofu.bad_pin"), code=errors.USAGE, data={"code": "bad_pin"})
    return v


def tofu_lines(t):
    """The certificate of a key over TLS without its own check (``gw exit-preview --tofu``; gw.tofu.check) in words:
    «ask» — the fingerprint and how to agree, «verified» — the chain checked out, «failed» — no answer."""
    st = (t or {}).get("status")
    if st == "ask":
        warn = tr("gw_tofu.warn_name", name=t.get("name"), sha256=t.get("sha256")) if t.get("name") \
            else tr("gw_tofu.warn", sha256=t.get("sha256"))
        return [warn, tr("gw_cli.exit.tofu_how", sha256=t.get("sha256"))]
    if st == "verified":
        return [tr("gw_cli.exit.tofu_verified")]
    if st == "failed":
        return [tr("gw_tofu.failed", why=t.get("why") or "?")]
    return []


def cmd_exit_preview(args):
    res = ops.exit_preview(_r(), _key_from_stdin(), tofu_by=args.tofu_by if args.tofu else None)
    lines = [res["text"]] if res.get("text") else []
    for x in res["items"]:
        lines.append("%s %s%s%s%s" % (x.get("id") or "—", x.get("title") or "", (" · %s" % x["where"]) if x.get("where") else "",
                                      tr("gw_cli.exit.in_pool") if x.get("in_pool") else "",
                                      (" — %s" % x["problem"]) if x.get("problem") else ""))
        lines.extend("  " + w for w in tofu_lines(x.get("tofu")))
    emit(args, res, lines or [tr("gw_cli.exit.no_keys")])
    return errors.OK


def cmd_tg_proxy_indexes(args):
    """The lines of upstream.txt of the bot's proxy (SPEC-stage1-exits §2.9): «1 3 4» (blank — none); ``--label`` — the
    name of their group on the second line (the header of the menu «Прокси для API Telegram»), only with a policy of
    the exits (REV-s1-t11 З3: a node of 3.x without groups has no header). «Напрямую» (SPEC-tg-proxy Р1) — the one
    word «direct», no label."""
    from . import tgproxy
    res = tgproxy.indexes()
    if res.get("direct"):
        emit(args, res, ["direct"])
        return errors.OK
    lines = [" ".join("%d" % n for n in res["indexes"])]
    if args.label and res.get("policy"):
        lines.append(" ".join(str(res["label"]).split()))
    emit(args, res, lines)
    return errors.OK


def cmd_exit_del(args):
    res = ops.exit_del(_r(), args.id, want_rev=args.rev, dry=args.dry)
    if res.get("dry"):
        from . import cli_policy
        return cli_policy._done(args, res, "")
    for w in res.get("warnings") or []:
        _warn(w)
    text = tr("gw_cli.exit.deleted", id=res["id"], left="%d" % res["left"])
    lines = ([text, res["text"]] if res.get("text") else [text]) + list(res.get("notes") or [])  # routes moved; good news
    emit(args, res, lines if len(lines) > 1 else text)
    return errors.OK


def cmd_exit_handover(args):
    """install.sh, right before line N leaves the pool (egress_remove_key_now; --dry — the question of the menu): its
    routes go to the other copy of the same key (REV-335-2). Says the line for people, or nothing."""
    res = ops.exit_handover(args.n, args.hex or "", dry=args.dry)
    emit(args, res, res["text"] or [])
    return errors.OK


# ── subscriptions of the exits (gw/exitsubs.py) ────────────────────────────

def _url_from_stdin():
    """The address of a subscription from stdin only (it is a secret: /proc/<pid>/cmdline is open to everyone)."""
    if sys.stdin is None:
        raise errors.KaskadError(tr("gw_cli.sub.stdin"), code=errors.USAGE)
    try:
        if sys.stdin.isatty():
            sys.stderr.write(tr("gw_cli.sub.prompt") + "\n")
            sys.stderr.flush()
    except (AttributeError, ValueError):
        pass
    return sys.stdin.read(exitsubs.URL_MAX + 2)


def _sub_line(r):
    return "%-9s %s — %s%s" % (r["id"], r["name"], r["text"], "" if r["auto"] else tr("gw_cli.sub.manual"))


def cmd_exit_sub_add(args):
    res = exitsubs.add(_r(), _url_from_stdin(), auto=not args.manual)
    for w in res.get("warnings") or []:
        _warn(w)
    emit(args, res, [res["text"]] + list(res.get("notes") or []))      # good news of the keys (REV-s1-b4 П-3)
    return errors.OK


def cmd_exit_sub_list(args):
    rows = exitsubs.listing()
    emit(args, {"ok": True, "subs": rows},
         [_sub_line(r) for r in rows] or [tr("gw_cli.sub.none")])
    return errors.OK


def cmd_exit_sub_del(args):
    res = exitsubs.delete(_r(), args.sub, keep_keys=args.keep_keys)
    emit(args, res, res["text"])
    return errors.OK


def cmd_exit_sub_refresh(args):
    res = exitsubs.refresh(_r(), args.sub, auto_only=args.auto)
    if args.json:
        emit(args, res)
    elif not args.quiet or not res["ok"]:
        lines = []
        for r in res["results"]:                       # good news of the keys after each (REV-s1-b6 П3)
            lines += [r["text"]] + [n for n in r.get("notes") or [] if isinstance(n, str) and n]
        emit(args, res, lines or [res["text"]])
    return errors.OK if res["ok"] else errors.ERROR


def cmd_exit_sub_restore(args):
    res = exitsubs.restore(_r(), args.sub)
    for w in (res.get("result") or {}).get("warnings") or []:
        _warn(w)
    emit(args, res, res["text"])
    return errors.OK if res["ok"] else errors.ERROR


def cmd_exit_sub_auto(args):
    res = exitsubs.set_auto(_r(), args.sub, args.value == "on")
    emit(args, res, "%s: %s." % (res["sub"]["name"], tr("gw_cli.sub.daily") if res["sub"]["auto"]
                                 else tr("gw_cli.sub.by_button")))
    return errors.OK


def scheme_doc(runner, st=None):
    """The scheme for a reader without the panel (the MCP agent, the support): the node's default route and rules, the
    inbounds, the exits, and every person with his devices — each device with its inbounds, its effective route (and
    where it comes from: device | user | default) and its rules profile. Ids and names only: no keys, no passwords.
    Every route carries ``fail_eff`` — where it goes when its exits fail, as it ACTS (its own ``fail`` or its group's,
    ``exits.fail_of``; REV-s1-b4 З-1) — and, for ``group:<gid>``, ``group_name``."""
    st = state.load() if st is None else st
    g = model.peek(st)
    pol = exits.local_policy(st)
    try:
        ex = [dict((k, e.get(k)) for k in ("id", "name", "kind", "proto", "role", "where", "enabled", "usable", "text"))
              for e in ops.exits_view(runner, st)]
        ex_error = None
    except errors.KaskadError as e:
        ex, ex_error = [], str(e)
    inbounds = [dict((k, r.get(k)) for k in ("kind", "title", "enabled", "ports", "devices"))
                for r in ops.inbound_list(runner, st, with_status=False) if r["installed"]]
    people = []
    for u in model.users(st):
        devs = []
        for d in model.devices(st, u["id"]):
            devs.append({"id": d["id"], "name": d.get("name", ""), "login": d.get("login", ""),
                         "enabled": d.get("enabled", True), "kind": d.get("kind"), "platform": d.get("platform"),
                         "access": list(d.get("access") or []),
                         "route": exits.route_view(st, model.route_of(st, d), pol),
                         "rules": model.profile_of(st, d)})
        people.append({"id": u["id"], "name": u.get("name", ""), "enabled": u.get("enabled", True),
                       "route": exits.route_view(st, u.get("route"), pol), "rules": u.get("rules"), "devices": devs})
    doc = {"ok": True, "enabled": bool(g["enabled"]),
           "default_route": exits.route_view(st, model.route_of(st, {"user": None}), pol),
           "rules": model.rules_conf(st), "inbounds": inbounds, "exits": ex, "people": people}
    if ex_error:
        doc["exits_error"] = ex_error
    return doc


def scheme_lines(doc, st=None):
    st = st if st is not None else state.load()
    lines = [tr("gw_cli.scheme.head", state=_on_off(doc["enabled"]), route=_route_text(doc["default_route"], st=st),
                rules=doc["rules"]["default"])]
    lines.append(tr("gw_cli.inbounds", list=", ".join(r["kind"] + ("" if r["enabled"] else tr("gw_cli.mark.off"))
                                                      for r in doc["inbounds"]) or tr("gw_cli.word.none")))
    lines.append(tr("gw_cli.scheme.exits", list=", ".join("%s %s%s" % (e["id"], e.get("name") or "", "" if e.get("usable")
                                                                       else tr("gw_cli.mark.down"))
                                                          for e in doc["exits"]) or tr("gw_cli.word.none")))
    for u in doc["people"]:
        lines.append("%s %s%s" % (u["id"], u["name"], "" if u["enabled"] else tr("gw_cli.mark.off")))
        for d in u["devices"]:
            lines.append("  " + tr("gw_cli.scheme.device", id=d["id"], name=d["name"], access=",".join(d["access"]) or "—",
                                   profile=d["rules"]["profile"], route=_route_text(d["route"], st=st),
                                   route_from=d["route"].get("from"), rules_from=d["rules"]["from"],
                                   off="" if d["enabled"] else tr("gw_cli.mark.off_n")))
    return lines


def cmd_scheme(args):
    st = state.load()
    doc = scheme_doc(_r(), st)
    emit(args, doc, scheme_lines(doc, st))
    return errors.OK


def route_via(args):
    """The ``via`` of a route command: the word (as before) or ``--via`` (§7.7), one of them."""
    pos, opt = getattr(args, "via", None), getattr(args, "via_opt", None)
    if pos is not None and opt is not None and pos != opt:
        raise errors.KaskadError(tr("gw_cli.route.via_twice"), code=errors.USAGE)
    via = opt if opt is not None else pos
    if via is None:
        raise errors.KaskadError(tr("gw_cli.route.via_need"), code=errors.USAGE)
    return via


def cmd_route(args):
    res = ops.default_route(_r(), route_via(args), args.fail)
    _warnings(res)
    emit(args, res, tr("gw_cli.route.default", route=_route_text(res["route"])))
    return errors.OK


def cmd_hook(args):
    res = hooks.hook(args.kind, args.action, args.identity, args.ip, _r())
    emit(args, res, None)
    return errors.OK if res["ok"] else errors.ERROR


def cmd_hook_reset(args):
    """The service of these inbounds stopped (restart, crash, install of a sibling): its sessions are gone, and
    its ip-down hooks never ran — drop their records, else they stay «в сети» for ever (стенд T-O, O-9)."""
    bad = [k for k in args.kinds if k not in model.INBOUND_KINDS]
    if bad:
        raise errors.KaskadError(tr("gw_cli.hook.unknown", kinds=", ".join(bad)), code=errors.USAGE)
    for k in args.kinds:
        hooks.forget(k)
    emit(args, {"ok": True, "reset": list(args.kinds)}, None)
    return errors.OK


# ── inbounds ───────────────────────────────────────────────────────────────

def cmd_inbound_list(args):
    rows = ops.inbound_list(_r())
    lines = []
    for r in rows:
        if not r["available"]:
            st = tr("gw_cli.inbound.unavailable")
        elif not r["installed"]:
            st = tr("gw_cli.inbound.not_installed")
        elif not r["enabled"]:
            st = tr("gw_cli.word.off")
        else:
            st = _run_text(r) + _core_text(r)
        lines.append("%-8s %-28s %-18s %-12s %s" % (
            r["kind"], r["title"][:28], st, ", ".join(r["ports"]) or "—",
            tr("gw_cli.inbound.row_counts", devices="%d" % r["devices"], sessions="%d" % r["sessions"])))
    emit(args, {"ok": True, "inbounds": rows}, lines)
    return errors.OK


def cmd_inbound_install(args):
    if getattr(args, "new_only", False) and model.inbound(state.load(), args.kind) is not None:
        raise errors.ConflictError(tr("gw_cli.inbound.new_only", kind=args.kind),
                                   data={"installed": True, "kind": args.kind})
    res = ops.inbound_install(_r(), args.kind, port=args.port, force=args.force, awg_engine=args.awg_engine,
                              progress=_progress)
    _warnings(res)
    for n in res["notes"]:
        _warn(n)
    prev = res.get("previous") or {}
    if prev.get("text"):                               # a reinstall that could not keep the port or the keys
        _warn(prev["text"])
    lines = [tr("gw_cli.inbound.installed", kind=args.kind)]
    if prev.get("restored") and prev.get("devices"):
        lines.append(tr("gw_cli.inbound.restored", n="%d" % len(prev["devices"])))
    emit(args, res, lines)
    return errors.OK


def cmd_inbound_uninstall(args):
    res = ops.inbound_uninstall(_r(), args.kind)
    _warnings(res)
    emit(args, res, [tr("gw_cli.inbound.uninstalled", kind=args.kind), tr("gw_cli.inbound.kept")])
    return errors.OK


def cmd_inbound_onoff(args):
    on = args.inbound_cmd == "enable"
    res = ops.inbound_enable(_r(), args.kind, on)
    _warnings(res)
    emit(args, res, tr("gw_cli.inbound.enabled" if on else "gw_cli.inbound.disabled", kind=args.kind))
    return errors.OK


def cmd_inbound_port(args):
    res = ops.inbound_port(_r(), args.kind, args.port, proto=args.proto, force=args.force)
    _warnings(res)
    emit(args, res, [tr("gw_cli.inbound.port", kind=args.kind, port="%d" % res["port"])] +
         ([res["text"]] if res.get("text") else []))           # Hysteria2: whom to give the links again
    return errors.OK


def _mtu_arg(v):
    """--mtu: digits -> int; anything else goes as it is — the core refuses it with the range of the protocol."""
    return int(v) if v is not None and re.match(r"^[0-9]{1,5}$", str(v)) else v


def cmd_inbound_set(args):
    """«Настройки протокола» (3.3.7.15 F3, решение 13: for the menu and the tests; new settings — in the panel):
    ``--dry`` — «Что изменится»; a change that needs a confirmation without ``--yes`` — the plan and CONFLICT."""
    try:
        res = ops.inbound_settings(_r(), args.kind, port=args.port, proto=args.proto, mtu=_mtu_arg(args.mtu),
                                   tls=args.tls, dry=args.dry, yes=args.yes, force=args.force, safe=args.safe,
                                   who=pending.who_ssh())
    except errors.ConflictError as e:
        if (e.data or {}).get("need") != "yes":
            raise
        raise errors.ConflictError("%s %s" % (e, tr("gw_cli.settings.need_yes")), data=e.data)   # the panel asks itself
    if args.dry:
        plan = res["plan"]
        lines = [tr("gw_cli.settings.plan", title=plan["title"], what=ops.settings_changed_text(plan["changed"]))
                 if plan["changed"] else tr("gw_ops.settings.nothing", title=plan["title"])]
        lines += ["  " + t for t in ops.plan_lines(plan)] if plan["changed"] else []
        emit(args, dict(res, lines=lines), lines)            # ``lines`` — «Что изменится» for the SSH menu (F9)
        return errors.OK
    _warnings(res)
    emit(args, res, [res["text"]] if res.get("text") else [])
    return errors.OK


def cmd_inbound_undo(args):
    """«Вернуть прежние настройки» (F3); 3.3.7.15 П6: ``--dry`` — what restarts and whom it cuts; a restart of a service
    other protocols share (accel-ppp with live sessions, the xray of the gateway) or the cut of the administrator's own
    link (П7: SSH_CONNECTION in a pool of the protocol) without ``--yes`` — the plan and CONFLICT, as ``set``."""
    try:
        res = ops.inbound_settings_undo(_r(), args.kind, yes=args.yes, dry=args.dry, who=pending.who_ssh())
    except errors.ConflictError as e:
        if (e.data or {}).get("need") != "yes":
            raise
        raise errors.ConflictError("%s %s" % (e, tr("gw_cli.settings.undo_need_yes")), data=e.data)
    if args.dry:
        plan = res["plan"]
        lines = [tr("gw_cli.settings.undo_plan", title=plan["title"], what=res["what"])]
        emit(args, res, lines + ["  " + x for x in res["lines"]])
        return errors.OK
    _warnings(res)
    emit(args, res, [res["text"]] if res.get("text") else [])
    return errors.OK


def cmd_inbound_show(args):
    """The settings of a protocol for the menu (F9) and the stand: the port, the MTU with its range, the TLS versions
    (F4), whom to give the settings again (F1), the last application («Вернуть прежние»), «порт закрыт?» (Е4)."""
    v = ops.inbound_settings_view(_r(), args.kind)
    lines = [tr("gw_cli.settings.head", title=v["title"], kind=v["kind"])]
    p = v["port"] or {}
    if p.get("port") and not p.get("fixed"):
        lines.append(tr("gw_cli.settings.port", port="%d" % p["port"], protos="+".join(p.get("protos") or [])))
    m = v["mtu"]
    if m:
        lines.append(tr("gw_cli.settings.mtu", cur=m["cur"], min=m["min"], limit=m["limit"], server=m["server"])
                     if m["open"] else tr("gw_cli.settings.mtu_closed", cur=m["cur"], why=m["why"]))
    t = v.get("tls")
    if t:                                                   # F4: VLESS TLS, SSTP, OpenConnect
        lines.append(tr("gw_cli.settings.tls", cur=ops.tls_text(t["cur"]), choices=", ".join(
            c["text"] + (" — " + c["hint"] if c["hint"] else "") for c in t["choices"] if c["value"] != t["cur"])))
        if t.get("note"):
            lines.append("  " + t["note"])
    r = v["reissue"] or {}
    if r.get("hard"):
        lines.append(tr("gw_cli.settings.hard", names=", ".join(d["name"] for d in r["hard"])))
    if r.get("soft"):
        lines.append(tr("gw_cli.settings.soft", names=", ".join(d["name"] for d in r["soft"])))
    if v["last"]:
        lines.append(tr("gw_cli.settings.last", what=v["last"]["text"], kind=v["kind"]))
    if v["closed"]:
        lines.append(tr("gw_cli.settings.closed_undo" if v["last"] else "gw_cli.settings.closed", kind=v["kind"]))
    emit(args, v, lines)
    return errors.OK


def _plan_line(r):
    if not r["supported"]:
        return "%-8s %s" % (r["kind"], tr("gw_cli.plan.unsupported", title=r["title"],
                                          why=r["why"] or tr("gw_cli.plan.no_support")))
    parts = []
    p = r.get("port")
    if p:
        text = "%d/%s" % (p["port"], "+".join(p["protos"]))
        if p["conflicts"]:
            text += tr("gw_cli.plan.busy_free", who="; ".join(c["detail"] for c in p["conflicts"]), free="%d" % p["free"])
        parts.append(text)
    for f in r.get("fixed") or []:
        text = "%d/%s" % (f["port"], f["proto"])
        if f["conflicts"]:
            text += tr("gw_cli.plan.busy", who="; ".join(c["detail"] for c in f["conflicts"]))
        parts.append(text)
    return "%-8s %s: %s%s" % (r["kind"], r["title"], ", ".join(parts) or tr("gw_cli.plan.no_port"),
                              tr("gw_cli.plan.installed") if r["installed"] else "")


def cmd_inbound_plan(args):
    rows = ops.inbound_plan(_r(), args.kinds or list(model.INBOUND_KINDS))
    emit(args, {"ok": True, "inbounds": rows}, [_plan_line(r) for r in rows])
    return errors.OK


def cmd_inbound_option(args):
    res = ops.inbound_option(_r(), args.kind, args.key, args.value)
    _warnings(res)
    if args.kind == ops.HY2 and args.key in ("hop", "cert"):
        lines = [tr("gw_cli.hy2.hop", hop=res.get("hop") or tr("gw_cli.word.off")) if args.key == "hop" else
                 tr("gw_cli.hy2.cert", cert=_hy2_cert(res.get("cert_mode"), res.get("cert")))]
        if res.get("text"):
            lines.append(res["text"])
        emit(args, res, lines)
        return errors.OK
    emit(args, res, [tr("gw_cli.inbound.option", kind=args.kind, option=ops.OPTIONS[(args.kind, args.key)],
                        state=tr("gw_cli.inbound.option_on") if res["value"] else tr("gw_cli.inbound.option_off"))] +
         ([res["text"]] if res.get("text") else []))
    return errors.OK


# ── the menu: probe, offers, purge ─────────────────────────────────────────

def cmd_probe(args):
    doc = ops.probe(_r())
    if not doc["supported"]:
        text = tr("gw_cli.probe.no", reason=doc["reason"])
    elif doc["engine"] != "nft":
        text = tr("gw_cli.probe.nft")
    else:
        text = tr("gw_cli.probe.ok")
    emit(args, doc, text)
    return errors.OK if doc["supported"] else errors.UNSUPPORTED


def cmd_offers(args):
    doc = ops.offers(args.platform)
    lines = []
    for o in doc["offers"]:
        lines.append("%-8s %-16s %-10s %s" % (o["inbound"], o["title"][:16],
                                              tr("gw_cli.state.running") if o["enabled"] else (
                                                  tr("gw_cli.word.off") if o["installed"] else tr("gw_cli.word.none")),
                                              o["note"]))
    lines.extend("• %s" % n for n in doc["notes"])
    emit(args, doc, lines or [tr("gw_cli.offers.none")])
    return errors.OK


def _marks():
    """K3_MARKS=1 (the SSH menu parses the output): machine lines «@K3:<code> …» — not translated, never shown
    (k3/handoff/I18N-3.3.2.md, «Обмен меню ↔ ядро»)."""
    return bool(os.environ.get("K3_MARKS"))


def cmd_trace(args):
    from . import trace
    st = state.load()
    res = trace.trace(st, args.who, args.target, inbound=getattr(args, "inbound", None))
    lines = trace.lines(res)
    if _marks():                                  # the menu colours the summary by it, not by its words
        lines = ["@K3:verdict %s" % (res.get("verdict") or "")] + lines
    emit(args, res, lines)
    return errors.OK


def cmd_pki_rotate(args):
    if not args.yes:
        raise errors.KaskadError(tr("gw_cli.pki.need_yes", kind=args.kind), code=errors.USAGE)
    res = ops.pki_rotate(_r(), args.kind)
    _warnings(res)
    n = len(res["devices"])
    emit(args, res, [tr("gw_cli.pki.rotated", kind=args.kind, host=res["host"] or tr("gw_cli.pki.this_node")),
                     tr("gw_cli.pki.reissue", kind=args.kind, n="%d" % n)])
    return errors.OK


# ── VLESS Reality ──────────────────────────────────────────────────────────

MASK_WORDS = {"own": "gw_cli.mask.own", "list": "gw_cli.mask.list", "custom": "gw_cli.mask.custom"}   # keys


def _mask_word(mask):
    return tr(MASK_WORDS[mask]) if mask in MASK_WORDS else mask


def _check_text(c):
    if not isinstance(c, dict) or c.get("ok") is None:
        return tr("gw_cli.site.unchecked")
    if c.get("ok"):
        return tr("gw_cli.site.ok", h2=", HTTP/2" if c.get("h2") else tr("gw_cli.site.no_h2"))
    return tr("gw_cli.site.bad", why=c.get("why") or tr("gw_cli.site.no_answer"))


# the reason of Reality (front.reason) -> its words in ``gw vless show`` (the card: vl.reason.*); {why} — the core's text
_VLESS_WHY = {"no_domain": "gw_cli.vless.why.no_domain", "no_cert": "gw_cli.vless.why.no_cert",
              "busy_443": "gw_cli.vless.why.busy_443", "no_site": "gw_cli.vless.why.no_site",
              "tls_failed": "gw_cli.vless.why.tls_failed", "ready": "gw_cli.vless.why.ready",
              "chosen": "gw_cli.vless.why.chosen", "consent_bad": "gw_cli.vless.why.consent_bad",
              "legacy": "gw_cli.vless.why.legacy", "vless_off": "gw_cli.vless.why.vless_off"}


def _vless_why(res):
    """The lines «Почему Reality: …» and «Как перейти на TLS: …» of the text ``gw vless show`` (what ``--json`` gives
    as reason / fix / recommend): [] — no reason known (the line of ``tls_block`` as before)."""
    r = res.get("reason") or ""
    key = _VLESS_WHY.get(r)
    if not key:
        return []
    c = res.get("consent") or {}
    if r == "no_domain" and res.get("want") == "reality_nodomain":
        key = "gw_cli.vless.why.nodomain_chosen"
    text = tr(key, why=(res.get("reason_text") or "—").rstrip(" ."),
              err=((res.get("tls_error") or {}).get("why") or res.get("reason_text") or "—").rstrip(" ."),
              at=fmt_datetime(c.get("at")) or "—", who=c.get("who") or c.get("via") or "—")
    out = [tr("gw_cli.vless.why", text=text)]
    fix, rec = res.get("fix") or {}, res.get("recommend") or {}
    if rec.get("text"):
        out.append(tr("gw_cli.vless.how_rec", text=rec["text"].strip()))
    elif fix.get("do") == "mode_tls":
        out.append(tr("gw_cli.vless.how_tls"))
    elif fix.get("text"):
        out.append(tr("gw_cli.vless.how", text=fix["text"].rstrip(" .")))
    return out


def cmd_vless_show(args):
    res = ops.vless_show(_r())
    head = tr("gw_cli.vless.head", state=_on_off(res["enabled"]), port=res["port"], devices="%d" % res["devices"])
    if res.get("security") == "tls":
        lines = [head, tr("gw_cli.vless.mode_tls", domain=res["domain"]),
                 "fingerprint: %s   flow: %s" % (res["fp"], res["flow"])]
    else:
        why = _vless_why(res)                           # T-3378 Н-16, Н-5, Н-14: by the reason, as the card
        lines = [head, tr("gw_cli.vless.mode_reality") + ("" if why else (
                     tr("gw_cli.vless.tls_block", why=res["tls_block"]) if res.get("tls_block") else ""))] + why + [
                 tr("gw_cli.vless.mask", sni=res["sni"], how=_mask_word(res["mask"]),
                    check=_check_text(res.get("mask_check"))),
                 tr("gw_cli.vless.pub", pub=res["pub"] or "—"),
                 "shortId: %s   fingerprint: %s   flow: %s" % (res["short_id"] or "—", res["fp"], res["flow"])]
    pend = res.get("pending")
    if pend:                                            # §4.2: идёт смена режима | брошенная — откатить
        lines.append("⚠ " + (tr("gw_in_vless.busy", at=pend.get("at") or "—") if pend.get("busy")
                             else tr("gw_in_vless.pending_left")))
    emit(args, res, lines)
    return errors.OK


def _hy2_cert(mode, pref=None):
    """«домен» / «самоподписанный ⚠» (+ «всегда», when the person chose it)."""
    if mode == "domain":
        return tr("gw_cli.hy2.cert_domain")
    return tr("gw_cli.hy2.cert_self_pref") if pref == "self" else tr("gw_cli.hy2.cert_self")


def cmd_hy2_show(args):
    res = ops.hy2_show(_r())
    if not res.get("installed"):
        emit(args, res, tr("gw_cli.hy2.not_installed"))
        return errors.OK
    ports = ", ".join("%s/%s" % (p["port"], p["proto"]) for p in res.get("ports_view") or [])
    lines = [tr("gw_cli.hy2.head", state=_on_off(res["enabled"]), ports=ports or "—", devices="%d" % res["devices"]),
             tr("gw_cli.hy2.obfs", state=tr("gw_cli.word.yes") if res["obfs"] else tr("gw_cli.word.no")),
             tr("gw_cli.hy2.hop", hop=res.get("hop") or tr("gw_cli.word.off")),
             tr("gw_cli.hy2.cert", cert=_hy2_cert(res["cert_mode"], res.get("cert")))]
    if res["cert_mode"] == "domain":
        lines.append(tr("gw_cli.hy2.domain", domain=res.get("domain") or "—"))
    else:
        lines.append(tr("gw_cli.hy2.pin", pin=res.get("pin") or "—"))
    lines.extend("⚠ " + w for w in res.get("warnings") or [])
    if res.get("note"):
        lines.append(res["note"])
    emit(args, res, lines)
    return errors.OK


def cmd_hy2_set(args):
    if args.hop is None and args.cert is None:
        raise errors.KaskadError(tr("gw_cli.hy2.set_nothing"), code=errors.USAGE)
    res = ops.hy2_set(_r(), hop=args.hop, cert=args.cert, force=args.force)
    _warnings(res)
    lines = []
    if args.hop is not None:
        lines.append(tr("gw_cli.hy2.hop", hop=res.get("hop") or tr("gw_cli.word.off")))
    if args.cert is not None:
        lines.append(tr("gw_cli.hy2.cert", cert=_hy2_cert(res.get("cert_mode"), res.get("cert"))))
    if res.get("text"):
        lines.append(res["text"])
    emit(args, res, lines)
    return errors.OK


def cmd_hy2_rekey(args):
    if not args.yes:
        raise errors.KaskadError(tr("gw_cli.hy2.rekey_confirm"), code=errors.USAGE)
    res = ops.hy2_rekey(_r())
    emit(args, res, [tr("gw_cli.hy2.rekeyed", pin=res.get("pin") or "—")] + ([res["text"]] if res.get("text") else []))
    return errors.OK


def cmd_vless_sync(args):
    res = ops.vless_sync(_r())
    _warnings(res)
    if res.get("changed"):
        lines = [tr("gw_cli.vless.mode", mode=tr("gw_cli.vless.tls_own") if res["security"] == "tls" else "Reality")]
    else:
        lines = [tr("gw_cli.vless.mode_same", mode="TLS" if res.get("security") == "tls" else "Reality",
                    why=tr("gw_cli.vless.tls_no", why=res["why"]) if res.get("why") else "")]
    if res.get("error"):
        lines.append(res["error"])
    if res.get("text"):
        lines.append(res["text"])
    emit(args, res, lines)
    return errors.OK


def _pick_site():
    """``--site auto`` at a terminal: the first cover that passes the check from the node (``gw vless masks --check``)."""
    for r in ops.vless_masks(_r(), check=True).get("masks") or []:
        if (r.get("check") or {}).get("ok"):
            return r["sni"]
    raise errors.KaskadError(tr("gw_in_vless.pick_first"), code=errors.USAGE)


def cmd_vless_mode(args):
    """``gw vless mode tls|reality`` (SPEC-vless-tls-default §4.2): the consent to Reality with a domain
    (``--accept-risks``; at a terminal — the text of the risks and a question with numbers), the price of new links
    (``--yes``; at a terminal — a question). ``--via`` is only a label of the journal; ``mcp`` -> ``who`` "mcp" and
    ``--risks``/``--site`` must name the text shown."""
    from ..site import cli as site_cli
    via = args.via or "cli"
    if args.recover:
        res = ops.vless_mode(_r(), None, recover=True, via=via)
        emit(args, res, [res["text"]])
        return errors.OK
    if not args.mode:
        raise errors.KaskadError(tr("gw_in_vless.bad_mode", mode="—"), code=errors.USAGE)
    tty = via == "cli" and not getattr(args, "json", False)
    site = args.site
    if site == "auto" and tty and _isatty():
        site = _pick_site()
    kw = {"site": site, "accept": bool(args.accept_risks), "risks": args.risks, "via": via,
          "no_check": bool(args.no_check)}

    def call(yes):
        try:
            return ops.vless_mode(_r(), args.mode, yes=yes, **kw)
        except errors.ConflictError as e:
            d = getattr(e, "data", None) or {}
            if d.get("need") != "reality_consent" or kw["accept"] or not tty or not d.get("block") \
                    or not d.get("site") or not _ask_risks(d):
                raise
            kw.update(accept=True, risks=d.get("risks"), site=d.get("site"))
            return ops.vless_mode(_r(), args.mode, yes=yes, **kw)
    res = site_cli.with_price(args, call)
    lines = [tr("gw_cli.vless.mode", mode=tr("gw_cli.vless.tls_own") if res.get("security") == "tls" else "Reality")
             if res.get("changed") else tr("gw_cli.vless.mode_same", mode="TLS" if res.get("security") == "tls"
                                           else "Reality", why="")]
    for k in ("error", "note", "text"):
        if res.get(k):
            lines.append(res[k])
    emit(args, res, lines)
    return errors.OK if res.get("ok", True) else errors.ERROR


def _isatty():
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _ask_risks(d):
    """The text of the risks of Reality and «1) Понимаю риски, выбрать Reality  2) Назад  Выбор [2]:» (§4.2)."""
    if not _isatty():
        return False
    sys.stderr.write("%s\n\n%s\n%s " % (d.get("title") or "", d.get("block") or "", tr("gw_cli.vless.ask_risks")))
    sys.stderr.flush()
    return sys.stdin.readline().strip() == "1"


def cmd_vless_masks(args):
    res = ops.vless_masks(_r(), check=args.check)
    lines = [tr("gw_cli.masks.head", list=res.get("list_title") or tr("gw_cli.masks.list"),
                country=(res.get("country") or {}).get("code") or tr("gw_cli.masks.no_country"),
                check="" if args.check else tr("gw_cli.masks.check_hint"))]
    for i, r in enumerate(res["masks"], 1):
        lines.append("%2d) %s — %s%s%s" % (i, r["sni"], r["title"], tr("gw_cli.masks.current") if r.get("current") else "",
                                         ("  · " + _check_text(r["check"])) if r.get("check") else ""))
    emit(args, res, lines)
    return errors.OK


def cmd_vless_mask(args):
    """A consent to the own site (r1b) and a foreign cover now (SEC-vless-sh12 №7): the text of variant A and the
    question at a terminal, as ``gw vless mode`` does; else the refusal of the core with the block."""
    via = getattr(args, "via", None) or "cli"
    kw = {"check": not args.no_check, "accept": bool(getattr(args, "accept_risks", False)),
          "risks": getattr(args, "risks", None), "via": via}
    try:
        res = ops.vless_mask(_r(), args.site, **kw)
    except errors.ConflictError as e:
        d = getattr(e, "data", None) or {}
        if d.get("need") != "reality_consent" or kw["accept"] or via != "cli" or getattr(args, "json", False) \
                or not d.get("block") or not _ask_risks(d):
            raise
        kw.update(accept=True, risks=d.get("risks"))
        res = ops.vless_mask(_r(), args.site, **kw)
    _warnings(res)
    lines = [tr("gw_cli.mask.set", sni=res["sni"], how=_mask_word(res["mask"]),
                same="" if res["changed"] else tr("gw_cli.unchanged"))]
    if res.get("text"):
        lines.append(res["text"])
    emit(args, res, lines)
    return errors.OK


def cmd_vless_rekey(args):
    if not args.yes:
        raise errors.KaskadError(tr("gw_cli.vless.rekey_need_yes"), code=errors.USAGE)
    res = ops.vless_rekey(_r())
    _warnings(res)
    emit(args, res, [tr("gw_cli.vless.rekeyed") + (" " + res["text"] if res.get("text") else "")])
    return errors.OK


def cmd_purge(args):
    if not args.yes:
        raise errors.KaskadError(tr("gw_cli.purge.need_yes"), code=errors.USAGE)
    res = ops.purge(_r(), users=args.users)
    _warnings(res)
    emit(args, res, tr("gw_cli.purge.done", users=tr("gw_cli.purge.users") if args.users
                       else tr("gw_cli.purge.users_kept")))
    return errors.OK


# ── users ──────────────────────────────────────────────────────────────────

def cmd_user_add(args):
    u = ops.user_add(_r(), args.name, args.route, args.fail)
    emit(args, {"ok": True, "user": u}, tr("gw_cli.user.added", name=u["name"], id=u["id"]))
    return errors.OK


def cmd_user_del(args):
    res = ops.user_del(_r(), args.user, force=args.force)
    _warnings(res)
    emit(args, res, tr("gw_cli.user.deleted", name=res["user"]["name"], devices=tr(
        "gw_cli.user.deleted_devices", n="%d" % len(res["devices"])) if res["devices"] else ""))
    return errors.OK


def route_views(st):
    """route -> ``exits.route_view`` for the lists of people (17 → 2, agents): ``fail_eff`` — where a route through the
    exits goes when they fail, its own step or its group's — and ``group_name`` of ``group:<gid>`` (T10, круг 2: the
    screen says what will be). A policy not readable — the routes as they are (the words of 3.3.5)."""
    try:
        pol = exits.local_policy(st)
    except Exception:                                   # noqa: BLE001
        return lambda r: r

    def view(r):
        try:
            return exits.route_view(st, r, pol)
        except Exception:                               # noqa: BLE001
            return r
    return view


def cmd_user_list(args):
    st = state.load()
    rows = []
    view = route_views(st)
    for u in model.users(st):
        # действующий маршрут человека (свой или по умолчанию узла) — меню 17 → 2 показывает его, как панель (M6 332c №10)
        row = dict(u, devices=len(model.devices(st, u["id"])), route_effective=view(model.route_of(st, {"user": u["id"]})))
        if isinstance(u.get("route"), dict):
            row["route"] = view(u["route"])
        rows.append(row)
    lines = [tr("gw_cli.user.none")] if not rows else []
    for u in rows:
        lines.append("%s  %-20s %s" % (u["id"], u["name"][:20], tr(
            "gw_cli.user.row", devices="%d" % u["devices"], route=_route_text(u.get("route"), USER_INHERIT, st),
            off="" if u.get("enabled", True) else tr("gw_cli.mark.off_comma"))))
    emit(args, {"ok": True, "users": rows}, lines)
    return errors.OK


def cmd_user_route(args):
    res = ops.user_route(_r(), args.user, route_via(args), args.fail)
    _warnings(res)
    emit(args, res, tr("gw_cli.route.of", name=res["user"]["name"], route=_route_text(res["user"].get("route"),
                                                                                        USER_INHERIT)))
    return errors.OK


def cmd_user_onoff(args):
    on = args.user_cmd == "enable"
    res = ops.user_enable(_r(), args.user, on)
    _warnings(res)
    emit(args, res, tr("gw_cli.user.enabled" if on else "gw_cli.user.disabled", name=res["user"]["name"]))
    return errors.OK


# ── devices ────────────────────────────────────────────────────────────────

def _access(text):
    return [k.strip() for k in (text or "").split(",") if k.strip()]


def cmd_device_add(args):
    res = ops.device_add(_r(), args.user, args.name, kind=args.kind, platform=args.platform,
                         access=_access(args.access), via=args.route, fail=args.fail)
    _warnings(res)
    d = res["device"]
    emit(args, res, tr("gw_cli.device.added", name=d["name"], id=d["id"], login=d["login"],
                       access=", ".join(d["access"]) or "—"))
    return errors.OK


def cmd_device_del(args):
    res = ops.device_del(_r(), args.device)
    _warnings(res)
    emit(args, res, tr("gw_cli.device.deleted", name=res["device"]["name"]))
    return errors.OK


def cmd_device_list(args):
    st = state.load()
    uid = model.get_user(st, args.user)["id"] if args.user else None
    rows = []
    view = route_views(st)
    for d in model.devices(st, uid):
        r = dict(d)
        r["route_effective"] = model.route_of(st, d)
        r["user_name"] = (model.find_user(st, d.get("user")) or {}).get("name", "")
        rows.append(r)
    lines = [tr("gw_cli.device.none")] if not rows else []
    for r in rows:
        lines.append("%s  %-18s %-10s %-14s %-12s %s %-16s %s %s  %s %s%s" % (
            r["id"], r["name"][:18], r["user_name"][:10], r["login"], "%s/%s" % (r["kind"], r["platform"]),
            tr("gw_cli.device.col.inbounds"), ",".join(r.get("access") or []) or "—",
            tr("gw_cli.device.col.addresses"), ",".join(model.device_ips(r)) or "—",
            tr("gw_cli.device.col.route"), _route_text(r["route_effective"], st=st),
            "" if r.get("enabled", True) else tr("gw_cli.mark.off_n")))
    for r in rows:                                      # the words of 17 → 2: fail_eff, group_name (T10, круг 2)
        r["route_effective"] = view(r["route_effective"])
        if isinstance(r.get("route"), dict):
            r["route"] = view(r["route"])
    emit(args, {"ok": True, "devices": rows}, lines)
    return errors.OK


def cmd_device_grant(args):
    on = args.device_cmd == "grant"
    res = ops.device_grant(_r(), args.device, args.kind, on=on)
    _warnings(res)
    emit(args, res, tr("gw_cli.device.granted" if on else "gw_cli.device.revoked", name=res["device"]["name"],
                       kind=args.kind))
    return errors.OK


def cmd_device_route(args):
    res = ops.device_route(_r(), args.device, route_via(args), args.fail)
    _warnings(res)
    emit(args, res, tr("gw_cli.route.of", name=res["device"]["name"], route=_route_text(res["device"].get("route"))))
    return errors.OK


def cmd_device_reset(args):
    res = ops.device_reset(_r(), args.device, inbound=args.inbound)
    _warnings(res)
    d = res["device"]
    lines = [tr("gw_cli.device.reset", name=d["name"], kinds=", ".join(res["kinds"]))]
    if res["also"]:
        lines.append(tr("gw_cli.device.reset_also", also=", ".join(res["also"])))
    lines.append(tr("gw_cli.device.reissue", id=d["id"]))
    emit(args, res, lines)
    return errors.OK


def cmd_device_onoff(args):
    on = args.device_cmd == "enable"
    res = ops.device_enable(_r(), args.device, on)
    _warnings(res)
    emit(args, res, tr("gw_cli.device.enabled" if on else "gw_cli.device.disabled", name=res["device"]["name"]))
    return errors.OK


def _body_lines(it):
    """Text of one issued record: a string as is; "fields" — label: value; "qr" (a list of payloads, one
    QR code each) — the payloads in order (the menu draws them as QR codes)."""
    body = it.get("body")
    if not isinstance(body, list):
        return [str(body if body is not None else "").rstrip("\n")]
    if it.get("format") == "fields":
        return ["%s: %s" % (b[0], b[1]) if isinstance(b, (list, tuple)) and len(b) == 2 else str(b) for b in body]
    out = []
    for i, b in enumerate(body):
        if len(body) > 1:
            out.append(tr("gw_cli.issue.part", i="%d" % (i + 1), n="%d" % len(body)))
        out.append(str(b).rstrip("\n"))
    return out


def cmd_issue(args):
    res = ops.issue(_r(), args.device, inbound=args.inbound, os_name=args.os, where=getattr(args, "where", None) or "menu")
    if getattr(args, "png", False):
        qr.add_pngs(res)
    lines = ["%s — %s (%s)" % (res["name"], res["kind"], res["os"])]
    sup = res.get("support") or {}
    if sup.get("hints"):                                 # таблица поддержки: обходной путь, «Подойдёт:», условие
        lines.append("")
        lines.extend("! %s" % x if i == 0 else "  %s" % x for i, x in enumerate(sup["hints"]))
    if sup.get("level") == "none":                       # платформа протокол не умеет: ни шагов, ни записей
        emit(args, res, lines)
        return errors.OK
    if res.get("howto"):
        lines.append("")
        lines.append(tr("gw_cli.issue.howto"))
        lines.extend("%d. %s" % (i, s) for i, s in enumerate(res["howto"], 1))
    for it in res["items"]:
        lines.append("")
        lines.append("== %s%s" % (it["title"], (" (%s)" % it["filename"]) if it.get("filename") else ""))
        lines.extend(_body_lines(it))
        for note in it.get("notes") or []:
            lines.append("• %s" % note)
    emit(args, res, lines)
    return errors.OK


# ── acceleration of AmneziaWG (the kernel module) ──────────────────────────

def _progress(text):
    sys.stderr.write("%s\n" % text)
    sys.stderr.flush()


def cmd_accel_status(args):
    doc = awgkmod.status(_r())
    emit(args, doc, awgkmod.lines(doc))
    return errors.OK


def cmd_accel_on(args):
    res = awgkmod.install(_r(), progress=_progress, force=args.force)
    _warnings(res)
    emit(args, dict(res, ok=True), _on_text(res))
    return errors.OK


def _on_text(res):
    what = tr("gw_cli.accel.what_set") if res.get("set") else tr("gw_cli.accel.what_noset")
    return tr("gw_cli.accel.on", what=what)


def cmd_accel_upgrade(args):
    res = awgkmod.upgrade(_r(), progress=_progress)          # the progress — to stderr (the journal of its unit)
    if not args.quiet or args.json:
        _warnings(res)
        emit(args, res, _on_text(res) if res.get("ran") else tr("gw_cli.accel.no_rebuild", why=res.get("why")))
    return errors.OK


def cmd_accel_off(args):
    res = awgkmod.remove(_r(), progress=_progress)
    _warnings(res)
    emit(args, dict(res, ok=True), tr("gw_cli.accel.off"))
    return errors.OK


def cmd_accel_boot(args):
    res = awgkmod.boot(_r())
    if not args.quiet:
        emit(args, res, tr("gw_cli.accel.boot_off") if not res.get("enabled") else (
            tr("gw_cli.accel.boot_loaded") if res.get("loaded") else
            tr("gw_cli.accel.boot_fallback", reason=res["fallback"]["reason"])))
    return errors.OK


def cmd_accel_check(args):
    probs = awgkmod.check_file(args.conf)
    if args.json:
        emit(args, {"ok": not probs, "problems": probs})
    else:
        for p in probs:
            sys.stderr.write("%s\n" % p)
    return errors.OK if not probs else errors.ERROR


# ── the engine of AmneziaWG ────────────────────────────────────────────────

def cmd_awg_status(args):
    doc = awgengine.status(_r())
    lines = [doc["text"]]
    for w in doc["warnings"] + doc["mix"]:
        lines.append("  ! %s" % w)
    if doc["offer"]["show"] and not doc["offer"]["blocked"]:
        lines.append("  " + tr("gw_cli.awg.kernel_offer"))
    emit(args, doc, lines)
    return errors.OK


def cmd_awg_check(args):
    doc = awgengine.check(_r())
    emit(args, doc, awgengine.check_lines(doc))
    return errors.OK


def cmd_awg_engine(args):
    res = awgengine.switch(_r(), args.engine, force=args.force, dry_run=args.dry_run, why=args.why or "CLI",
                           progress=None if args.json and args.dry_run else _progress)
    _warnings(res)
    if res.get("dry_run"):
        if args.engine == "kernel":
            text = awgengine.check_lines(res["check"])
        else:
            text = tr("gw_cli.awg.program_ok", alive="%d" % res["check"]["alive"], reconnect=awgengine.RECONNECT)
        emit(args, res, text)
        return errors.OK
    if res.get("rolled_back"):
        emit(args, res, tr("gw_cli.awg.not_switched", reason=res["reason"]))
        return errors.ERROR
    if res.get("unchanged"):
        text = tr("gw_cli.awg.already", title=awgengine.TITLE[res["engine"]])
    else:
        text = tr("gw_cli.awg.switched", title=awgengine.TITLE[res["engine"]], backup=res["backup"])
    emit(args, res, text)
    return errors.OK


def cmd_awg_first(args):
    res = awgengine.first(_r(), choice="program" if args.program else None, progress=_progress,
                          why=args.why or None) or {"engine": awgengine.active(), "warnings": [], "skipped": True}
    _warnings(res)
    emit(args, dict(res, ok=True), tr("gw_cli.awg.engine", title=awgengine.TITLE[res["engine"]]))
    return errors.OK


def cmd_awg_offer(args):
    res = awgengine.offer(_r())
    if not args.quiet:
        emit(args, res, tr("gw_cli.awg.offer_set") if res.get("pending") else
             (tr("gw_cli.awg.offer_no", reason=res["reason"]) if res.get("reason") else tr("gw_cli.awg.offer_nothing")))
    return errors.OK


def cmd_awg_export(args):
    res = awgengine.export(args.file)
    emit(args, res, tr("gw_cli.awg.exported", file=res["file"], inbounds=", ".join(res["inbounds"]) or tr(
        "gw_cli.word.none"), users="%d" % res["users"], devices="%d" % res["devices"]))
    return errors.OK


def cmd_awg_import(args):
    if args.check:
        res = awgengine.check_export(args.file)
        emit(args, res, tr("gw_cli.awg.export_file", inbounds=", ".join(res["inbounds"]) or tr("gw_cli.word.none"),
                           users="%d" % res["users"], devices="%d" % res["devices"]))
        return errors.OK
    res = awgengine.import_(_r(), args.file)
    _warnings(res)
    emit(args, res, tr("gw_cli.awg.imported", inbounds=", ".join(res["inbounds"]) or tr("gw_cli.word.none"),
                       users="%d" % res["users"], devices="%d" % res["devices"]))
    return errors.OK


# ── safe MTU (§6.1) ────────────────────────────────────────────────────────

def cmd_pending_confirm(args):
    """F7: ``gw pending confirm [--token T]`` — keep the new settings (the menu and a new SSH session; refused 409
    ``pending_gone`` when they were reverted already)."""
    from . import pending
    res = pending.confirm(_r(), args.token)
    emit(args, res, res["text"])
    return errors.OK


def cmd_pending_expire(args):
    """F7: ``gw pending expire --token T`` — called by the timer of systemd (never by a person)."""
    from . import pending
    res = pending.expire(_r(), args.token)
    emit(args, res, tr("gw_pending.cli.expire", done=pending.done_text(res.get("done"))))
    return errors.OK


def cmd_mtu_show(args):
    doc = ops.mtu_view()
    emit(args, dict(doc, ok=True), gwmtu.status_lines(doc))
    return errors.OK


def cmd_mtu_reissue(args):
    res = ops.mtu_reissue(_r(), args.kind)
    _warnings(res)
    emit(args, dict(res, ok=True), res["text"])
    return errors.OK


def cmd_mtu_keep(args):
    res = ops.mtu_keep(_r(), args.kind)
    if res["what"] == "s4":
        text = tr("gw_cli.mtu.keep_s4", kind=args.kind, s4="%d" % res["s4"])
    else:
        text = tr("gw_cli.mtu.keep_nic", kind=args.kind, nic="%d" % res["nic"])
    emit(args, dict(res, ok=True), text)
    return errors.OK


def cmd_mtu_s4(args):
    res = ops.mtu_s4(_r(), args.kind, yes=args.yes)
    _warnings(res)
    lines = [res["text"]]
    if not res["done"] and not res.get("nothing"):
        # the menu asks itself: the hint is a machine line for it (it drops every «@K3:» line)
        lines.append("@K3:confirm kaskadctl gw mtu s4 %s --yes" % args.kind if _marks() else
                     tr("gw_cli.mtu.confirm", kind=args.kind))
    emit(args, dict(res, ok=True), lines)
    return errors.OK


# ── parser ─────────────────────────────────────────────────────────────────

def _route_args(p):
    p.add_argument("--fail", default=None, choices=model.FAILS, help=tr("gw_cli.help.fail"))


def _via_args(p):
    """``<via>`` as before, or ``--via`` (§7.7): pool, group:<gid>, exit:<id>, direct, block."""
    p.add_argument("via", nargs="?", default=None, help=tr("gw_cli.help.route_via"))
    p.add_argument("--via", dest="via_opt", metavar="VIA", default=None, help=tr("gw_cli.help.route_via"))
    _route_args(p)


def register(subparsers):
    p = subparsers.add_parser("gw", help=tr("gw_cli.help.gw"))
    sub = p.add_subparsers(dest="gw_cmd", metavar=tr("cli.metavar_command"))
    sub.required = True

    add_json_flag(sub.add_parser("status", help=tr("gw_cli.help.status"))).set_defaults(func=cmd_status)
    pe = add_json_flag(sub.add_parser("enable", help=tr("gw_cli.help.enable")))
    pe.add_argument("--base", default=None, help=tr("gw_cli.help.base"))
    pe.add_argument("--host", default=None, help=tr("gw_cli.help.host_arg"))
    pe.add_argument("--force", action="store_true", help=tr("gw_cli.help.host_force"))
    pe.set_defaults(func=cmd_enable)
    pp = add_json_flag(sub.add_parser("host", help=tr("gw_cli.help.host")))
    pp.add_argument("value", nargs="?", default=None)
    pp.add_argument("--detect", action="store_true", help=tr("gw_cli.help.detect"))
    pp.add_argument("--force", action="store_true", help=tr("gw_cli.help.host_force"))
    pp.set_defaults(func=cmd_host)
    pa = sub.add_parser("addresses", help=tr("gw_cli.help.addresses"))
    add_json_flag(pa).set_defaults(func=cmd_addresses)
    pa.add_argument("--refresh", action="store_true", help=tr("gw_cli.help.addresses_refresh"))
    asub_a = pa.add_subparsers(dest="addr_cmd", metavar=tr("cli.metavar_command"))
    p1 = add_json_flag(asub_a.add_parser("add", help=tr("gw_cli.help.addresses_add")))
    p1.add_argument("ip")
    p1.add_argument("--local", default=None, help=tr("gw_cli.help.addresses_local"))
    p1.set_defaults(func=cmd_addresses_add)
    p1 = add_json_flag(asub_a.add_parser("remove", help=tr("gw_cli.help.addresses_remove")))
    p1.add_argument("ip")
    p1.set_defaults(func=cmd_addresses_remove)
    p1 = add_json_flag(asub_a.add_parser("in", help=tr("gw_cli.help.addresses_in")))
    p1.add_argument("ips", nargs="*", metavar="IP")
    p1.add_argument("--all", action="store_true", help=tr("gw_cli.help.addresses_in_all"))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.addresses_yes"))
    p1.set_defaults(func=cmd_addresses_in)
    p1 = add_json_flag(asub_a.add_parser("in6", help=tr("gw_cli.help.addresses_in6")))
    p1.add_argument("ips", nargs="*", metavar="IPv6")
    p1.add_argument("--all", action="store_true", help=tr("gw_cli.help.addresses_in6_all"))
    p1.add_argument("--none", action="store_true", help=tr("gw_cli.help.addresses_in6_none"))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.addresses_yes"))
    p1.set_defaults(func=cmd_addresses_in6)
    p1 = add_json_flag(asub_a.add_parser("give6", help=tr("gw_cli.help.addresses_give6")))
    p1.add_argument("ip", metavar="IPv6|off")
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.addresses_yes"))
    p1.set_defaults(func=cmd_addresses_give6)
    p1 = add_json_flag(asub_a.add_parser("exit", help=tr("gw_cli.help.addresses_exit")))
    p1.add_argument("id", nargs="?", default=None)
    p1.add_argument("ip", help=tr("gw_cli.help.addresses_exit_ip"))
    p1.add_argument("--group", default=None, help=tr("gw_cli.help.addresses_exit_group"))
    p1.set_defaults(func=cmd_addresses_exit)
    add_json_flag(sub.add_parser("disable", help=tr("gw_cli.help.disable"))).set_defaults(func=cmd_disable)
    add_json_flag(sub.add_parser("apply", help=tr("gw_cli.help.apply"))).set_defaults(func=cmd_apply)
    pc = add_json_flag(sub.add_parser("check", help=tr("gw_cli.help.check")))
    pc.add_argument("--repair", action="store_true")
    pc.add_argument("--quiet", action="store_true")
    pc.set_defaults(func=cmd_check)
    pl = add_json_flag(sub.add_parser("collect", help=tr("gw_cli.collect.help"), description=tr("gw_cli.collect.help")))
    pl.add_argument("--once", action="store_true", help=tr("gw_cli.help.once"))
    pl.add_argument("--interval", type=int, default=collect.INTERVAL)
    pl.set_defaults(func=cmd_collect)
    add_json_flag(sub.add_parser("sessions", help=tr("gw_cli.help.sessions"))).set_defaults(func=cmd_sessions)
    pa = add_json_flag(sub.add_parser("activity", help=tr("gw_cli.help.activity"), description=tr("gw_cli.help.activity")))
    pa.add_argument("who", help=tr("gw_cli.help.activity_who"))
    pa.add_argument("--range", choices=ACT_RANGES, default="1d", help=tr("gw_cli.help.activity_range"))
    pa.set_defaults(func=cmd_activity)
    add_json_flag(sub.add_parser("exits", help=tr("gw_cli.help.exits"))).set_defaults(func=cmd_exits)
    px = add_json_flag(sub.add_parser("exit", help=tr("gw_cli.help.exit")))
    px.add_argument("id")
    g = px.add_mutually_exclusive_group()
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    rename_args(px, required=False)
    from . import cli_policy
    cli_policy.exit_args(px, g)
    px.set_defaults(func=cmd_exit)
    px = add_json_flag(sub.add_parser("exit-add", help=tr("gw_cli.help.exit_add")))
    px.add_argument("--public-only", action="store_true",
                    help=tr("gw_cli.help.public_only"))
    px.add_argument("--group", default=None, help=tr("gw_cli.help.exit_add_group"))
    # TOFU (§7.7, §9.1): the fingerprint the preview showed; --pin-by — who agreed, for the record (MCP passes «mcp»)
    px.add_argument("--pin-tofu", default=None, metavar="SHA256", help=tr("gw_cli.help.pin_tofu"))
    px.add_argument("--pin-by", default="cli", choices=("cli", "mcp"), help=argparse.SUPPRESS)
    px.set_defaults(func=cmd_exit_add)
    px = add_json_flag(sub.add_parser("exit-preview", help=tr("gw_cli.help.exit_preview")))
    px.add_argument("--tofu", action="store_true", help=tr("gw_cli.help.preview_tofu"))
    px.add_argument("--tofu-by", default="cli", choices=("cli", "mcp"), help=argparse.SUPPRESS)
    px.set_defaults(func=cmd_exit_preview)
    px = add_json_flag(sub.add_parser("tg-proxy-indexes", help=tr("gw_cli.help.tg_proxy_indexes")))
    px.add_argument("--label", action="store_true", help=tr("gw_cli.help.tg_proxy_label"))
    px.set_defaults(func=cmd_tg_proxy_indexes)
    px = add_json_flag(sub.add_parser("exit-del", help=tr("gw_cli.help.exit_del")))
    px.add_argument("id")
    cli_policy._rev_arg(cli_policy._dry_arg(px))
    px.set_defaults(func=cmd_exit_del)
    px = add_json_flag(sub.add_parser("exit-handover", help=tr("gw_cli.help.exit_handover")))
    px.add_argument("n", type=int)
    px.add_argument("hex", nargs="?", default="")
    px.add_argument("--dry", action="store_true", help=tr("gw_cli.help.exit_handover_dry"))
    px.set_defaults(func=cmd_exit_handover)
    ps = sub.add_parser("exit-sub", help=tr("gw_cli.help.exit_sub"))
    ssub = ps.add_subparsers(dest="exit_sub_cmd", metavar=tr("cli.metavar_command"))
    ssub.required = True
    p1 = add_json_flag(ssub.add_parser("add", help=tr("gw_cli.help.sub_add")))
    p1.add_argument("--manual", action="store_true", help=tr("gw_cli.help.manual"))
    p1.set_defaults(func=cmd_exit_sub_add)
    add_json_flag(ssub.add_parser("list", help=tr("gw_cli.help.sub_list"))).set_defaults(
        func=cmd_exit_sub_list)
    p1 = add_json_flag(ssub.add_parser("del", help=tr("gw_cli.help.sub_del")))
    p1.add_argument("sub", help=tr("gw_cli.help.sub_arg"))
    p1.add_argument("--keep-keys", action="store_true", help=tr("gw_cli.help.keep_keys"))
    p1.set_defaults(func=cmd_exit_sub_del)
    p1 = add_json_flag(ssub.add_parser("refresh", help=tr("gw_cli.help.sub_refresh")))
    p1.add_argument("sub", nargs="?", default=None, help=tr("gw_cli.help.sub_arg"))
    p1.add_argument("--auto", action="store_true", help=tr("gw_cli.help.sub_auto_only"))
    p1.add_argument("--quiet", action="store_true", help=tr("gw_cli.help.sub_quiet"))
    p1.set_defaults(func=cmd_exit_sub_refresh)
    p1 = add_json_flag(ssub.add_parser("restore", help=tr("gw_cli.help.sub_restore")))
    p1.add_argument("sub", help=tr("gw_cli.help.sub_arg"))
    p1.set_defaults(func=cmd_exit_sub_restore)
    p1 = add_json_flag(ssub.add_parser("rename", help=tr("gw_cli.help.sub_rename")))
    p1.add_argument("sub", help=tr("gw_cli.help.sub_arg"))
    rename_args(p1)
    p1.set_defaults(func=cmd_exit_sub_rename)
    p1 = add_json_flag(ssub.add_parser("auto", help=tr("gw_cli.help.sub_auto")))
    p1.add_argument("sub", help=tr("gw_cli.help.sub_arg"))
    p1.add_argument("value", choices=("on", "off"))
    p1.set_defaults(func=cmd_exit_sub_auto)
    add_json_flag(sub.add_parser("scheme", help=tr("gw_cli.help.scheme"))) \
        .set_defaults(func=cmd_scheme)
    pr = add_json_flag(sub.add_parser("route", help=tr("gw_cli.help.route")))
    _via_args(pr)
    pr.set_defaults(func=cmd_route)
    cli_policy.register(sub)                            # stage 1 (T10): groups, policy, buttons over one object
    ph = add_json_flag(sub.add_parser("hook", help=tr("gw_cli.help.hook")))
    ph.add_argument("kind")
    ph.add_argument("action", choices=("up", "down"))
    ph.add_argument("identity")
    ph.add_argument("ip")
    ph.set_defaults(func=cmd_hook)
    phr = add_json_flag(sub.add_parser("hook-reset", help=tr("gw_cli.help.hook_reset")))
    phr.add_argument("kinds", nargs="+")
    phr.set_defaults(func=cmd_hook_reset)

    pi = sub.add_parser("inbound", help=tr("gw_cli.help.inbound"))
    isub = pi.add_subparsers(dest="inbound_cmd", metavar=tr("cli.metavar_command"))
    isub.required = True
    add_json_flag(isub.add_parser("list", help=tr("gw_cli.help.inbound_list"))).set_defaults(func=cmd_inbound_list)
    p1 = add_json_flag(isub.add_parser("install", help=tr("gw_cli.help.install")))
    p1.add_argument("kind")
    p1.add_argument("--port", default=None)
    p1.add_argument("--force", action="store_true", help=tr("gw_cli.help.force_port"))
    p1.add_argument("--awg-engine", default=None, choices=("kernel", "program"),
                    help=tr("gw_cli.help.awg_engine_arg"))
    p1.add_argument("--new-only", action="store_true", help=tr("gw_cli.help.new_only"))
    p1.set_defaults(func=cmd_inbound_install)
    p1 = add_json_flag(isub.add_parser("uninstall", help=tr("gw_cli.help.uninstall")))
    p1.add_argument("kind")
    p1.set_defaults(func=cmd_inbound_uninstall)
    for verb in ("enable", "disable"):
        p1 = add_json_flag(isub.add_parser(verb, help=tr("gw_cli.help.inbound_enable") if verb == "enable"
                                           else tr("gw_cli.help.inbound_disable")))
        p1.add_argument("kind")
        p1.set_defaults(func=cmd_inbound_onoff)
    p1 = add_json_flag(isub.add_parser("port", help=tr("gw_cli.help.port")))
    p1.add_argument("kind")
    p1.add_argument("port")
    p1.add_argument("--proto", default=None, choices=("udp", "tcp"))
    p1.add_argument("--force", action="store_true")
    p1.set_defaults(func=cmd_inbound_port)
    # 3.3.7.15 F3 (решение 13): «Настройки протокола» — for the SSH menu (F9) and the tests; new settings — the panel
    p1 = add_json_flag(isub.add_parser("set", help=tr("gw_cli.help.inbound_set")))
    p1.add_argument("kind")
    p1.add_argument("--port", default=None)
    p1.add_argument("--proto", default=None, choices=("udp", "tcp"))
    p1.add_argument("--mtu", default=None)
    p1.add_argument("--tls", default=None, choices=("1.2-1.3", "1.3", "1.2"))
    p1.add_argument("--dry", action="store_true", help=tr("gw_cli.help.settings_dry"))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.settings_yes"))
    p1.add_argument("--force", action="store_true", help=tr("gw_cli.help.force_port"))
    p1.add_argument("--safe", action="store_true", help=tr("gw_cli.help.settings_safe"))
    p1.set_defaults(func=cmd_inbound_set)
    p1 = add_json_flag(isub.add_parser("undo", help=tr("gw_cli.help.inbound_undo")))
    p1.add_argument("kind")
    p1.add_argument("--dry", action="store_true", help=tr("gw_cli.help.settings_dry"))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.undo_yes"))
    p1.set_defaults(func=cmd_inbound_undo)
    p1 = add_json_flag(isub.add_parser("show", help=tr("gw_cli.help.inbound_show")))
    p1.add_argument("kind")
    p1.set_defaults(func=cmd_inbound_show)
    p1 = add_json_flag(isub.add_parser("plan", help=tr("gw_cli.help.plan")))
    p1.add_argument("kinds", nargs="*", metavar="kind")
    p1.set_defaults(func=cmd_inbound_plan)
    p1 = add_json_flag(isub.add_parser("label", help=tr("gw_cli.help.inbound_label")))
    p1.add_argument("kind")
    rename_args(p1)
    p1.set_defaults(func=cmd_inbound_label)
    p1 = add_json_flag(isub.add_parser("option", help=tr("gw_cli.help.option")))
    p1.add_argument("kind")
    p1.add_argument("key")
    p1.add_argument("value", help=tr("gw_cli.help.on_off"))
    p1.set_defaults(func=cmd_inbound_option)

    pu = sub.add_parser("user", help=tr("gw_cli.help.users"))
    usub = pu.add_subparsers(dest="user_cmd", metavar=tr("cli.metavar_command"))
    usub.required = True
    p1 = add_json_flag(usub.add_parser("add", help=tr("gw_cli.help.user_add")))
    p1.add_argument("name")
    p1.add_argument("--route", default=None)
    _route_args(p1)
    p1.set_defaults(func=cmd_user_add)
    p1 = add_json_flag(usub.add_parser("del", help=tr("gw_cli.help.user_del")))
    p1.add_argument("user")
    p1.add_argument("--force", action="store_true", help=tr("gw_cli.help.with_devices"))
    p1.set_defaults(func=cmd_user_del)
    add_json_flag(usub.add_parser("list", help=tr("gw_cli.help.users"))).set_defaults(func=cmd_user_list)
    p1 = add_json_flag(usub.add_parser("rename", help=tr("gw_cli.help.user_rename")))
    p1.add_argument("user")
    rename_args(p1)
    p1.set_defaults(func=cmd_user_rename)
    p1 = add_json_flag(usub.add_parser("route", help=tr("gw_cli.help.user_route")))
    p1.add_argument("user")
    _via_args(p1)
    p1.set_defaults(func=cmd_user_route)
    for verb in ("enable", "disable"):
        p1 = add_json_flag(usub.add_parser(verb))
        p1.add_argument("user")
        p1.set_defaults(func=cmd_user_onoff)

    pd = sub.add_parser("device", help=tr("gw_cli.help.devices"))
    dsub = pd.add_subparsers(dest="device_cmd", metavar=tr("cli.metavar_command"))
    dsub.required = True
    p1 = add_json_flag(dsub.add_parser("add", help=tr("gw_cli.help.device_add")))
    p1.add_argument("user")
    p1.add_argument("name")
    p1.add_argument("--kind", default="other", choices=model.DEVICE_KINDS)
    p1.add_argument("--platform", default="other", choices=model.PLATFORMS)
    p1.add_argument("--access", default="", help=tr("gw_cli.help.access"))
    p1.add_argument("--route", default=None)
    _route_args(p1)
    p1.set_defaults(func=cmd_device_add)
    p1 = add_json_flag(dsub.add_parser("del", help=tr("gw_cli.help.device_del")))
    p1.add_argument("device")
    p1.set_defaults(func=cmd_device_del)
    p1 = add_json_flag(dsub.add_parser("rename", help=tr("gw_cli.help.device_rename")))
    p1.add_argument("device")
    rename_args(p1)
    p1.set_defaults(func=cmd_device_rename)
    p1 = add_json_flag(dsub.add_parser("list", help=tr("gw_cli.help.devices")))
    p1.add_argument("--user", default=None)
    p1.set_defaults(func=cmd_device_list)
    for verb in ("grant", "revoke"):
        p1 = add_json_flag(dsub.add_parser(verb, help=tr("gw_cli.help.grant") if verb == "grant"
                                           else tr("gw_cli.help.revoke")))
        p1.add_argument("device")
        p1.add_argument("kind")
        p1.set_defaults(func=cmd_device_grant)
    p1 = add_json_flag(dsub.add_parser("route", help=tr("gw_cli.help.device_route")))
    p1.add_argument("device")
    _via_args(p1)
    p1.set_defaults(func=cmd_device_route)
    p1 = add_json_flag(dsub.add_parser("reset", help=tr("gw_cli.help.reset")))
    p1.add_argument("device")
    p1.add_argument("--inbound", default=None, help=tr("gw_cli.help.only_inbound"))
    p1.set_defaults(func=cmd_device_reset)
    for verb in ("enable", "disable"):
        p1 = add_json_flag(dsub.add_parser(verb))
        p1.add_argument("device")
        p1.set_defaults(func=cmd_device_onoff)

    p1 = add_json_flag(sub.add_parser("issue", help=tr("gw_cli.help.issue")))
    p1.add_argument("device")
    p1.add_argument("--inbound", default=None)
    p1.add_argument("--os", default=None, choices=model.PLATFORMS)
    p1.add_argument("--png", action="store_true", help=tr("gw_cli.help.png"))
    p1.add_argument("--where", default=None, choices=("menu", "panel", "page", "bot", "agent"),
                    help=tr("gw_cli.help.where"))
    p1.set_defaults(func=cmd_issue)

    pk = sub.add_parser("pki", help=tr("gw_cli.help.pki"))
    ksub = pk.add_subparsers(dest="pki_cmd", metavar=tr("cli.metavar_command"))
    ksub.required = True
    p1 = add_json_flag(ksub.add_parser("rotate", help=tr("gw_cli.help.rotate")))
    p1.add_argument("kind", choices=ops.PKI_KINDS)
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.yes"))
    p1.set_defaults(func=cmd_pki_rotate)

    pv = sub.add_parser("vless", help=tr("gw_cli.help.vless"))
    vsub = pv.add_subparsers(dest="vless_cmd", metavar=tr("cli.metavar_command"))
    vsub.required = True
    add_json_flag(vsub.add_parser("show", help=tr("gw_cli.help.vless_show"))).set_defaults(func=cmd_vless_show)
    p1 = add_json_flag(vsub.add_parser("masks", help=tr("gw_cli.help.masks")))
    p1.add_argument("--check", action="store_true", help=tr("gw_cli.help.masks_check"))
    p1.set_defaults(func=cmd_vless_masks)
    p1 = add_json_flag(vsub.add_parser("mask", help=tr("gw_cli.help.mask")))
    p1.add_argument("site", help=tr("gw_cli.help.mask_site"))
    p1.add_argument("--no-check", action="store_true", help=tr("gw_cli.help.no_check"))
    p1.add_argument("--accept-risks", action="store_true", help=tr("gw_cli.help.vless_mode_accept"))
    p1.add_argument("--risks", default=None, help=tr("gw_cli.help.vless_mode_risks"))
    p1.add_argument("--via", default=None, choices=("panel", "ssh", "cli", "mcp", "setup"),
                    help=tr("gw_cli.help.vless_mode_via"))
    p1.set_defaults(func=cmd_vless_mask)
    p1 = add_json_flag(vsub.add_parser("rekey", help=tr("gw_cli.help.rekey")))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.yes"))
    p1.set_defaults(func=cmd_vless_rekey)
    add_json_flag(vsub.add_parser("sync", help=tr("gw_cli.help.sync"))).set_defaults(func=cmd_vless_sync)
    p1 = add_json_flag(vsub.add_parser("mode", help=tr("gw_cli.help.vless_mode")))
    p1.add_argument("mode", nargs="?", choices=("tls", "reality"))
    p1.add_argument("--site", default=None, help=tr("gw_cli.help.vless_mode_site"))
    p1.add_argument("--accept-risks", action="store_true", help=tr("gw_cli.help.vless_mode_accept"))
    p1.add_argument("--risks", default=None, help=tr("gw_cli.help.vless_mode_risks"))
    p1.add_argument("--via", default=None, choices=("panel", "ssh", "cli", "mcp", "setup"),
                    help=tr("gw_cli.help.vless_mode_via"))
    p1.add_argument("--no-check", action="store_true", help=tr("gw_cli.help.no_check"))
    p1.add_argument("--yes", action="store_true", help=tr("site_cli.help.vless_yes"))
    p1.add_argument("--recover", action="store_true", help=tr("gw_cli.help.vless_mode_recover"))
    p1.set_defaults(func=cmd_vless_mode)

    ph2 = sub.add_parser("hy2", help=tr("gw_cli.help.hy2"))
    hsub = ph2.add_subparsers(dest="hy2_cmd", metavar=tr("cli.metavar_command"))
    hsub.required = True
    add_json_flag(hsub.add_parser("show", help=tr("gw_cli.help.hy2_show"))).set_defaults(func=cmd_hy2_show)
    p1 = add_json_flag(hsub.add_parser("set", help=tr("gw_cli.help.hy2_set")))
    p1.add_argument("--hop", default=None, help=tr("gw_cli.help.hy2_hop"))
    p1.add_argument("--cert", default=None, choices=ops.HY2_CERTS, help=tr("gw_cli.help.hy2_cert"))
    p1.add_argument("--force", action="store_true", help=tr("gw_cli.help.force_port"))
    p1.set_defaults(func=cmd_hy2_set)
    p1 = add_json_flag(hsub.add_parser("rekey", help=tr("gw_cli.help.hy2_rekey")))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.yes"))
    p1.set_defaults(func=cmd_hy2_rekey)

    p1 = add_json_flag(sub.add_parser("offers", help=tr("gw_cli.help.offers")))
    p1.add_argument("--platform", required=True, choices=model.PLATFORMS)
    p1.set_defaults(func=cmd_offers)
    add_json_flag(sub.add_parser("probe", help=tr("gw_cli.help.probe"))).set_defaults(func=cmd_probe)
    p1 = add_json_flag(sub.add_parser("trace", help=tr("gw_cli.help.trace")))
    p1.add_argument("who", metavar=tr("gw_cli.help.who_metavar"), help=tr("gw_cli.help.who"))
    p1.add_argument("target", metavar=tr("gw_cli.help.target_metavar"), help=tr("gw_cli.help.target"))
    p1.add_argument("--inbound", choices=model.INBOUND_KINDS, default=None,
                    help=tr("gw_cli.help.trace_inbound"))
    p1.set_defaults(func=cmd_trace)
    p1 = add_json_flag(sub.add_parser("purge", help=tr("gw_cli.help.purge")))
    p1.add_argument("--users", action="store_true", help=tr("gw_cli.help.purge_users"))
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.yes"))
    p1.set_defaults(func=cmd_purge)

    pm = sub.add_parser("mtu", help=tr("gw_cli.help.mtu"))
    add_json_flag(pm).set_defaults(func=cmd_mtu_show)
    msub = pm.add_subparsers(dest="mtu_cmd", metavar=tr("cli.metavar_command"))
    add_json_flag(msub.add_parser("show", help=tr("gw_cli.help.mtu_show"))).set_defaults(
        func=cmd_mtu_show)
    p1 = add_json_flag(msub.add_parser("reissue", help=tr("gw_cli.help.mtu_reissue")))
    p1.add_argument("kind", choices=gwmtu.KINDS)
    p1.set_defaults(func=cmd_mtu_reissue)
    p1 = add_json_flag(msub.add_parser("s4", help=tr("gw_cli.help.mtu_s4")))
    p1.add_argument("kind", choices=gwmtu.S4_KINDS)
    p1.add_argument("--yes", action="store_true", help=tr("gw_cli.help.mtu_s4_yes"))
    p1.set_defaults(func=cmd_mtu_s4)
    p1 = add_json_flag(msub.add_parser("keep", help=tr("gw_cli.help.mtu_keep")))
    p1.add_argument("kind", choices=gwmtu.KINDS)
    p1.set_defaults(func=cmd_mtu_keep)

    pa = sub.add_parser("accel", help=tr("gw_cli.help.accel"))
    asub = pa.add_subparsers(dest="accel_cmd", metavar=tr("cli.metavar_command"))
    asub.required = True
    add_json_flag(asub.add_parser("status", help=tr("gw_cli.help.accel_status"))).set_defaults(
        func=cmd_accel_status)
    p1 = add_json_flag(asub.add_parser("on", help=tr("gw_cli.help.accel_on")))
    p1.add_argument("--force", action="store_true", help=tr("gw_cli.help.accel_force"))
    p1.set_defaults(func=cmd_accel_on)
    add_json_flag(asub.add_parser("off", help=tr("gw_cli.help.accel_off"))).set_defaults(
        func=cmd_accel_off)
    p1 = add_json_flag(asub.add_parser("boot", help=tr("gw_cli.help.accel_boot")))
    p1.add_argument("--quiet", action="store_true")
    p1.set_defaults(func=cmd_accel_boot)
    p1 = add_json_flag(asub.add_parser("upgrade", help=tr("gw_cli.help.accel_upgrade")))
    p1.add_argument("--quiet", action="store_true")
    p1.set_defaults(func=cmd_accel_upgrade)
    p1 = add_json_flag(asub.add_parser("check", help=tr("gw_cli.help.accel_check")))
    p1.add_argument("conf")
    p1.set_defaults(func=cmd_accel_check)

    pw = sub.add_parser("awg", help=tr("gw_cli.help.awg"))
    wsub = pw.add_subparsers(dest="awg_cmd", metavar=tr("cli.metavar_command"))
    wsub.required = True
    add_json_flag(wsub.add_parser("status", help=tr("gw_cli.help.awg_status"))).set_defaults(
        func=cmd_awg_status)
    add_json_flag(wsub.add_parser("check", help=tr("gw_cli.help.awg_check"))).set_defaults(
        func=cmd_awg_check)
    p1 = add_json_flag(wsub.add_parser("engine", help=tr("gw_cli.help.awg_engine")))
    p1.add_argument("engine", choices=awgengine.ENGINES)
    p1.add_argument("--force", action="store_true", help=tr("gw_cli.help.awg_force"))
    p1.add_argument("--dry-run", action="store_true", help=tr("gw_cli.help.dry_run"))
    p1.add_argument("--why", default=None, help=tr("gw_cli.help.why"))
    p1.set_defaults(func=cmd_awg_engine)
    p1 = add_json_flag(wsub.add_parser("first", help=tr("gw_cli.help.awg_first")))
    p1.add_argument("--program", action="store_true", help=tr("gw_cli.help.program"))
    p1.add_argument("--why", default=None)
    p1.set_defaults(func=cmd_awg_first)
    p1 = add_json_flag(wsub.add_parser("offer", help=tr("gw_cli.help.awg_offer")))
    p1.add_argument("--quiet", action="store_true")
    p1.set_defaults(func=cmd_awg_offer)
    p1 = add_json_flag(wsub.add_parser("export", help=tr("gw_cli.help.export")))
    p1.add_argument("--file", required=True)
    p1.set_defaults(func=cmd_awg_export)
    p1 = add_json_flag(wsub.add_parser("import", help=tr("gw_cli.help.import")))
    p1.add_argument("--file", required=True)
    p1.add_argument("--check", action="store_true", help=tr("gw_cli.help.import_check"))
    p1.set_defaults(func=cmd_awg_import)

    # F7 (3.3.7.15, SPEC-R1 решение 13): only the operational commands — the menu and the timer; new things — the panel
    pp = sub.add_parser("pending", help=tr("gw_pending.help.pending"))
    psub = pp.add_subparsers(dest="pending_cmd", metavar=tr("cli.metavar_command"))
    psub.required = True
    p1 = add_json_flag(psub.add_parser("confirm", help=tr("gw_pending.help.confirm")))
    p1.add_argument("--token", default=None, help=tr("gw_pending.help.token"))
    p1.set_defaults(func=cmd_pending_confirm)
    p1 = add_json_flag(psub.add_parser("expire", help=tr("gw_pending.help.expire")))
    p1.add_argument("--token", required=True, help=tr("gw_pending.help.token"))
    p1.set_defaults(func=cmd_pending_expire)
