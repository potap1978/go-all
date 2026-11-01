"""SOCKS5 inbound (``socks5``): no daemon of its own — an inbound of the gateway xray (work package 4,
kaskad/gw/xray.py, service kaskad-gw-xray.service) that this adapter describes:

    xray_inbounds(st, sec)          -> [inbound]  protocol socks, auth password, accounts = logins and
                                       passwords of the devices with socks5 in their access, UDP if
                                       ``inbounds.socks5.udp``; no sniffing: the gateway xray routes with
                                       IPOnDemand, which would check a sniffed SNI/Host instead of the
                                       real destination (its block of private addresses, gw/xray.py)
    xray_routing(st, sec, target)   -> [rule]     one chain per user, as VLESS (stage 1, SPEC-stage1-exits §2.4):
                                       its wave-3 rules (``user_chain``; [] with the shared lists ``ls-``),
                                       then ``user -> target(route, device)``, tagged ``sk-<login>-<hash>-<n>``;
                                       no closing rule — xray's first outbound of a hot inbound is ``block``;
                                       the UDP of the inbound (no user in xray) — one rule ``sk-_udp-…`` to the
                                       target all the accounts share (``udp_target``, T-mip-lab Н1)

xray gives a SOCKS user to routing as its login (proxy/socks: ``MemoryUser{Email: username}``; a socks
account has no email field), so the rules match logins. ``target(route, device)`` turns a route into
``outboundTag``/``balancerTag``; the default ``route_target`` uses the tags ``pool`` (balancer), ``direct``,
``block`` and ``exit-<id>`` — xray.py passes its own function if its tags differ.

apply(): passwords that are missing are created — SOCKS5's own ``dev.<id>.socks5.password``: SOCKS5 sends it
in plain text, so the device's shared password (L2TP, SSTP, IKEv2, OpenConnect) is never used here; the login is
the device login. /etc/kaskad/gw/socks5/socks5.json keeps port, UDP and the account count for status(); UFW gets
the TCP port (and UDP when on). The gateway xray must be re-rendered after it — that is the core's apply. Stage 1
(§2.4): the inbound is «hot» — a route or a pin of an account moves through xray's API (its chain), no restart; a NEW
or a removed account (or a new password) restarts xray: ``adu`` on socks is refused (T0 С0.7, «proxy is not a
UserManager»), so ``xray_add_user`` is None.
Issuance: ``socks5://login:pass@host:port`` and the Telegram links ``tg://socks?…`` / ``https://t.me/socks?…``.
"""

import hashlib
import json
import os
from urllib.parse import quote

from kaskad.gw import issue_os, registry
from kaskad.gw.inbounds import tunnel
from kaskad import util
from kaskad.gw.inbounds.base import Inbound, InboundError
from kaskad.gw.inbounds.texts import lazy_texts
from kaskad.i18n import retr, tr, trs

TAG = "gw-socks5"
RULE_PREFIX = "sk-"                 # ruleTag of the per-user chains (gw/xray.py hot_plan swaps them by it)
XRAY_UNIT = "kaskad-gw-xray.service"
__getattr__ = lazy_texts(__name__, {"OWN": "gw_in_socks5.own"})        # socks5.OWN: words of the moment


def password_key(device_id):
    return "dev.%s.socks5.password" % device_id


def _chain_tag(login, chain):
    """``sk-<login>-<hash of the chain>-<n>`` — the hash as VLESS (``vless._chain_tag``): a changed chain is a new set of
    tags, swapped whole (§2.4)."""
    h = hashlib.sha256(json.dumps(chain, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    return ["%s%s-%s-%d" % (RULE_PREFIX, login, h, i) for i in range(len(chain))]


UDP_LOGIN = "_udp"                  # the ruleTag of the UDP rule: «sk-_udp-…» — no login has «_» (model._LOGIN)


def _route_key(route):
    """A route as compared for the UDP rule: {"via", "fail"} with the defaults (``route_of`` may give a string)."""
    r = route if isinstance(route, dict) else {"via": route}
    return (str(r.get("via") or "pool"), str(r.get("fail") or "block"))


def route_target(route, device=None):
    """Route ({"via": …, "fail": …} or "pool"/"direct"/"block"/"exit:<id>") -> xray rule target."""
    via = route.get("via") if isinstance(route, dict) else route
    via = str(via or "pool")
    if via == "pool":
        return {"balancerTag": "pool"}
    if via in ("direct", "block"):
        return {"outboundTag": via}
    if via.startswith("exit:") and len(via) > 5:
        return {"outboundTag": "exit-" + via[5:]}
    return {"outboundTag": "block"}


class Socks5Inbound(Inbound):
    kind = "socks5"
    xray_hot = True                   # stage 1 (§2.4): routes and pins of accounts move through xray's API
    xray_hot_prefix = RULE_PREFIX
    xray_add_user = None              # a new account — a restart (adu on socks: «proxy is not a UserManager», T0 С0.7)
    engine = "xray"
    title = "SOCKS5"

    def dir(self):
        return tunnel.kind_dir("socks5")

    def state_path(self):
        return os.path.join(self.dir(), "socks5.json")

    def default_port(self):
        return 1080

    def ifaces(self):
        return []

    def addr_kinds(self, st=None):
        return []

    def cfg(self, st):
        return tunnel.require_cfg(st, self.kind, self.title)

    def port(self, st):
        return tunnel.port_value(self.cfg(st), "port", self.default_port())

    def udp(self, st):
        return self.cfg(st).get("udp", True) is not False

    def accounts(self, st, sec):
        """[(device, login, password)] of the devices ready to use the proxy."""
        out, seen = [], set()
        for d in tunnel.devices(st, self.kind):
            lg = tunnel.login(d)
            pw = tunnel.sget(sec, password_key(d["id"]))
            if lg and pw and lg not in seen:            # xray keys the accounts by login
                seen.add(lg)
                out.append((d, lg, pw))
        return out

    def prepare(self, st, sec, runner=None):
        cfg = self.cfg(st)
        done = []
        if tunnel.port_value(cfg, "port", 0) == 0:
            cfg["port"] = self.default_port()
            done.append(trs("gw_in.made.port", port=cfg["port"]))
        if "udp" not in cfg:
            cfg["udp"] = True
        for d in tunnel.devices(st, self.kind):
            if not tunnel.login(d):
                tunnel.log_tr(self.kind, "gw_in.log.skipped_login", device=d["id"])
                continue
            if tunnel.ensure_secret(sec, password_key(d["id"]))[1]:
                done.append(trs("gw_in_socks5.made.password", device=d["id"]))
        return done

    # ── for the gateway xray (WP4) ──
    def xray_inbounds(self, st, sec):
        if not tunnel.wanted(st, self.kind):
            return []
        acc = self.accounts(st, sec)
        if not acc:
            return []
        return [{
            "tag": TAG,
            "listen": "0.0.0.0",
            "port": self.port(st),
            "protocol": "socks",
            "settings": {"auth": "password", "udp": self.udp(st),
                         "accounts": [{"user": lg, "pass": pw} for _, lg, pw in sorted(acc, key=lambda a: a[1])]},
        }]

    def xray_routing(self, st, sec, target=None):
        """One chain per account, as VLESS: its wave-3 rules (``rules.apply.user_chain`` — [] with the shared lists of
        stage 1), then ``inboundTag [gw-socks5], user [login] -> target(route, device)``; every rule tagged
        ``sk-<login>-<hash of the chain>-<n>``. No closing ``gw-socks5 -> block``: the core puts the chains last and
        xray's first outbound of a hot inbound is ``block`` — what no chain routes goes there (T0 С0.10); a closing
        rule in the shared rules would stand before every chain and block all of SOCKS5."""
        if not tunnel.wanted(st, self.kind):
            return []
        target = target or route_target
        from kaskad.rules import apply as rules_apply
        man, ok = rules_apply.manifest(), rules_apply.ready()
        rules = []
        for d, lg, _ in sorted(self.accounts(st, sec), key=lambda a: a[1]):
            chain = rules_apply.user_chain(st, target, [TAG], lg, d, man=man, is_ready=ok)
            last = {"type": "field", "inboundTag": [TAG], "user": [lg]}
            last.update(target(tunnel.route_of(st, d), d))
            chain.append(last)
            for rule, tag in zip(chain, _chain_tag(lg, chain)):
                rule["ruleTag"] = tag
                rules.append(rule)
        t = self.udp_target(st, sec, target)
        if t is not None:                               # T-mip-lab Н1: the UDP of SOCKS5 has no user (below)
            rule = {"type": "field", "inboundTag": [TAG], "network": "udp"}
            rule.update(t)
            rule["ruleTag"] = _chain_tag(UDP_LOGIN, [rule])[0]
            rules.append(rule)
        return rules

    def udp_target(self, st, sec, target=None):
        """Where the UDP of SOCKS5 goes (T-mip-lab Н1). xray hands a UDP packet of SOCKS5 to the routing without the
        user — UDP ASSOCIATE carries no login; xray takes UDP only from the addresses that made an ASSOCIATE with a
        password (its UDPFilter) — so no chain of an account matches it, and it went to the first outbound «block»
        (every UDP of SOCKS5 was lost). One rule for the UDP of the inbound, after the chains: the target every account
        shares by its device (one pin), else by the route alone (the same route: its group's balancer); accounts with
        different routes — None: no rule, UDP stays closed (never the route of somebody else), the issuance says so
        (``gw_in_socks5.udp_mixed``). UDP off — None."""
        if not tunnel.wanted(st, self.kind) or not self.udp(st):
            return None
        acc = sorted(self.accounts(st, sec), key=lambda a: a[1])
        if not acc:
            return None
        target = target or route_target
        by_dev = [target(tunnel.route_of(st, d), d) for d, _, _ in acc]
        if all(t == by_dev[0] for t in by_dev):
            return by_dev[0]
        routes = [_route_key(tunnel.route_of(st, d)) for d, _, _ in acc]
        if all(r == routes[0] for r in routes):
            return target(tunnel.route_of(st, acc[0][0]), None)
        return None

    # ── interface of §3 ──
    def render(self, st, sec):
        info = {"port": self.port(st), "udp": self.udp(st), "accounts": len(self.accounts(st, sec))}
        return {self.state_path(): (json.dumps(info, sort_keys=True) + "\n", 0o600)}

    def requirements(self, runner):
        port = self.default_port()
        return {"kind": self.kind, "engine": self.engine, "packages": [], "modules": [],
                "ports": [{"proto": "tcp", "port": port}, {"proto": "udp", "port": port}],
                "supported": True, "why": "", "needs": [XRAY_UNIT]}

    def install(self, runner):
        tunnel.ensure_dir(self.dir())
        tunnel.log_tr(self.kind, "gw_in.log.installed_xray")

    def uninstall(self, runner):
        tunnel.close_ufw(runner, self.dir(), self.kind)
        tunnel.remove_tree(self.dir())
        tunnel.log_tr(self.kind, "gw_in.log.removed")

    def apply(self, st, sec, runner):
        self.cfg(st)
        prepared = self.prepare(st, sec, runner)
        tunnel.ensure_dir(self.dir())
        changed = tunnel.write_files(self.render(st, sec))
        want = []
        if tunnel.wanted(st, self.kind):
            want.append((self.port(st), "tcp"))
            if self.udp(st):
                want.append((self.port(st), "udp"))
        tunnel.sync_ufw(runner, self.dir(), self.kind, want)
        if changed or prepared:
            with util.journal_lang():
                tunnel.log(self.kind, tr("gw_in_socks5.log.applied", accounts=len(self.accounts(st, sec)),
                                         created=tr("gw_in.log.created", items=", ".join(retr(prepared))) if prepared else ""))
        return {"action": "xray" if want else "off", "changed": [os.path.basename(p) for p in changed],
                "prepared": prepared}

    def status(self, runner):
        """Running = the gateway xray serves the inbound. With no account yet xray has nothing of SOCKS5 to serve
        (the port opens with the first device): that is idle, not broken — no «НЕ работает», no repair."""
        info = tunnel.read_json(self.state_path(), None)
        installed = isinstance(info, dict)
        idle = installed and not info.get("accounts")
        running = installed and (idle or tunnel.is_active(runner, XRAY_UNIT))
        out = {"kind": self.kind, "installed": installed, "running": running,
               "port": info.get("port") if installed else None, "sessions": [], "units": [XRAY_UNIT]}
        if idle:
            out["idle"] = True
            out["warnings"] = [tr("gw_in_socks5.idle")]
        return out

    # ── issuance ──
    def _udp_key(self, st, sec):
        if not self.udp(st):
            return "gw_in_socks5.udp_off"
        return "gw_in_socks5.udp_on" if self.udp_target(st, sec) is not None else "gw_in_socks5.udp_mixed"

    def _udp_lines(self, st, sec):
        """The lines of UDP in the issuance; UDP on — also that it skips the lists of the user (REV-mip-s34 З-3: xray
        gives UDP of SOCKS5 to the routing without the user, the rules «Россия напрямую» and the lists match the user)."""
        key = self._udp_key(st, sec)
        return [tr(key)] + ([tr("gw_in_socks5.udp_lists")] if key == "gw_in_socks5.udp_on" else [])

    def issue(self, st, sec, device, os=None):
        dev = tunnel.find_device(st, device)
        found = [a for a in self.accounts(st, sec) if a[0].get("id") == dev.get("id")]
        if not found:
            raise InboundError(tr("gw_in.err.not_user_pw", device=tunnel.device_name(dev), title="SOCKS5"))
        _, lg, pw = found[0]
        host, port = tunnel.host(st), self.port(st)
        h = ("[%s]" % host) if ":" in host else host
        q = lambda v: quote(str(v), safe="")      # noqa: E731
        url = "socks5://%s:%s@%s:%d" % (q(lg), q(pw), h, port)
        tg_query = "server=%s&port=%d&user=%s&pass=%s" % (q(host), port, q(lg), q(pw))
        link = tunnel.record("link", tr("gw_in.rec.link", title="SOCKS5"), url, None, [tr("gw_in_socks5.link_note")])
        tg = tunnel.record("link", tr("gw_in_socks5.rec.tg"), "tg://socks?" + tg_query, None,
                           [tr("gw_in_socks5.tg_note"), tr("gw_in_socks5.tg_browser", url="https://t.me/socks?" + tg_query)])
        fields = tunnel.record("fields", tr("gw_in.rec.fields", title="SOCKS5"),
                               [[tr("gw_in.label.server"), host], [tr("gw_in.label.port"), str(port)],
                                [tr("gw_in.label.login"), lg], [tr("gw_in.label.password"), pw]], None,
                               self._udp_lines(st, sec) + [tr("gw_in_socks5.own")])
        p = issue_os.platform_of(dev, os)
        recs = [tg, link, fields] if p in ("ios", "android") else [link, tg, fields]
        return tunnel.result(self.kind, recs)


registry.register(Socks5Inbound)
