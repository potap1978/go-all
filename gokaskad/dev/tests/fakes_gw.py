"""Test doubles of work package 4 (the gateway): ip (rules, routes, links, addresses), awg, curl,
dpkg-query/apt-get, ufw, xray, dnsmasq, modprobe and the gokaskad script (``--egress-awg-up``,
``--egress-ensure-parser``) — wired into one FakeRunner by ``GwWorld`` on top of the wave-1 ``World``.

Also: the egress parser taken from install.sh (``install_parser``), synthetic pool keys (``VLESS_A``,
``VLESS_B``, ``awg_line``), ``write_pool`` and a ready scenario (``scenario_state``). Create a GwWorld
INSIDE a test (after RootCase.setUp): the paths of the awg tool, xray and the script copy are resolved
under KASKAD_ROOT.
"""

import collections
import ipaddress
import json
import os
import re

from kaskad import paths
from kaskad.gw import exits
from tests import realbin
from tests.fakes_nft import DEFAULT_BINARIES, World

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SH = os.path.join(ROOT, "install.sh")
GW_BINARIES = DEFAULT_BINARIES + ("ip", "curl", "dpkg-query", "apt-get", "env", "modprobe", "dnsmasq", "conntrack")

VLESS_A = ("vless://11111111-2222-3333-4444-555555555555@198.51.100.20:443?type=tcp&security=reality"
           "&pbk=8FXcm8haAt5zxGJFC6-71alFTj_LhAL5hbOx7QBE6lc&sid=80cd0a54&sni=www.microsoft.com&fp=chrome"
           "&flow=xtls-rprx-vision#Vienna")
VLESS_B = ("vless://66666666-7777-8888-9999-000000000000@198.51.100.21:8443?type=ws&security=tls"
           "&path=%2Fws&sni=example.org#Riga")


# the lines egress_replace_now of install.sh takes as keys (stage 1, §9.1: every xray scheme and AmneziaWG)
POOL_SCHEMES = ("vless://", "hy2://", "hysteria2://", "ss://", "trojan://", "vmess://", "awg://")


def parser_source():
    """egress_parse.py exactly as install.sh writes it (the heredoc of egress_ensure_parser)."""
    with open(INSTALL_SH, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"cat > \"\$tmp\" <<'PYEOF'\n(.*?)\nPYEOF\n", text, re.S)
    if not m:
        raise AssertionError("egress parser heredoc not found in install.sh")
    return m.group(1) + "\n"


def install_parser():
    p = paths.egress_parser()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(parser_source())
    os.chmod(p, 0o600)
    return p


def _key(seed):
    import base64
    return base64.b64encode(bytes((seed * 7 + i) % 256 for i in range(32))).decode()


def awg_conf(n=1, endpoint="198.51.100.31:51820", address="10.8.1.5/32", line="1.0"):
    iface = [("Jc", "4"), ("Jmin", "40"), ("Jmax", "70"), ("S1", "116"), ("S2", "61"),
             ("H1", "1234567801"), ("H2", "1234567802"), ("H3", "1234567803"), ("H4", "1234567804")]
    if line == "3.1":
        iface = [("Jc", "5"), ("Jmin", "10"), ("Jmax", "50"), ("S1", "12"), ("S2", "12"), ("S3", "12"),
                 ("S4", "12"), ("H1", "1"), ("H2", "2"), ("H3", "3"), ("H4", "4"),
                 ("HeaderProtectionKey", _key(50 + n)), ("RandomTrailers", "on"), ("DisableCookies", "on")]
    out = ["[Interface]", "Address = %s" % address, "DNS = 1.1.1.1", "PrivateKey = %s" % _key(n)]
    out += ["%s = %s" % kv for kv in iface]
    out += ["", "[Peer]", "PublicKey = %s" % _key(100 + n), "PresharedKey = %s" % _key(200 + n),
            "AllowedIPs = 0.0.0.0/0, ::/0", "Endpoint = %s" % endpoint, "PersistentKeepalive = 25"]
    return "\n".join(out) + "\n"


def awg_line(n=1, name="Amsterdam", **kw):
    """A pool line ``awg://<base64 json>`` made by the parser itself (as egress_import stores it)."""
    parser = exits.load_parser()
    rec = parser.awg_from_conf(awg_conf(n, **kw), name)
    return "awg://" + parser._b64e(json.dumps(rec))


def strip_awg_keepalive(cfg):
    """Н3 (T-KT1): the one change of the render of an updated node against the code before — ``tcpKeepAliveIdle`` of the
    AmneziaWG outbounds of the gateway (``exits.quiet_keepalive``: the probes of the observatory, no route changes).
    Taken out of ``cfg`` in place; -> how many outbounds had it (the references of «Как раньше» compare the rest)."""
    n = 0
    for ob in (cfg or {}).get("outbounds") or []:
        so = ((ob or {}).get("streamSettings") or {}).get("sockopt") or {}
        if ob.get("protocol") == "freedom" and so.get("mark") and so.get("tcpKeepAliveIdle") == exits.AWG_KEEPALIVE_IDLE:
            del so["tcpKeepAliveIdle"]
            n += 1
    return n


def write_pool(lines):
    p = paths.egress_up()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(l + "\n" for l in lines))
    os.chmod(p, 0o600)


def write_ru_pool(lines):
    """upstream-ru.txt (T15 Q4): the keys of the Russian groups."""
    p = paths.egress_up_ru()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write("".join(l + "\n" for l in lines))
    os.chmod(p, 0o600)


FAKE_XRAY = b"#!/bin/sh\n"            # what touch_exec writes: GwCase takes it for the pinned xray (3.3.4)


def touch_exec(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(FAKE_XRAY)
    os.chmod(path, 0o755)


# ── ip ─────────────────────────────────────────────────────────────────────

class FakeIp(object):
    def __init__(self, node_ip="203.0.113.10", extra_routes=(), extra_addrs=()):
        self.rules = [(0, "from all lookup local"), (32766, "from all lookup main"), (32767, "from all lookup default")]
        self.routes = collections.OrderedDict([("main", ["default via 10.0.0.1 dev eth0 onlink"] + list(extra_routes)),
                                               ("local", ["local %s dev eth0 proto kernel scope host src %s"
                                                          % (node_ip, node_ip)])])
        self.links = collections.OrderedDict()
        self._idx = 0
        self.add_link("lo", "loopback", ["127.0.0.1/8"])
        self.add_link("eth0", "ether", ["%s/32" % node_ip] + list(extra_addrs))
        self.calls = []

    def add_link(self, name, kind="ether", addrs=(), up=True):
        self._idx += 1
        self.links[name] = {"idx": self._idx, "type": kind, "up": up, "addrs": list(addrs)}

    def drop_link(self, name):
        """The interface goes — and with it its routes, as in the kernel."""
        self.links.pop(name, None)
        for rs in self.routes.values():
            rs[:] = [r for r in rs if (" %s " % r).find(" dev %s " % name) < 0]

    def rule_texts(self):
        return [t for _, t in self.ordered()]

    def ordered(self):
        """The rules in the kernel's order: by pref, a rule added later after the ones of the same pref."""
        return sorted(self.rules, key=lambda r: r[0])

    def default_pref(self):
        """fib_default_rule_pref: the pref of the SECOND rule of the list minus 1, 0 if that pref is 0."""
        o = self.ordered()
        return (o[1][0] - 1) if len(o) > 1 and o[1][0] > 0 else 0

    # ── ``ip route get <dst> from <src> iif <dev> [mark M]``: the FIB rules and tables of this fake ──

    @staticmethod
    def _rule_parts(text):
        """(negated, selector {from, to, iif, fwmark, suppress}, action (kind, table)) of an ``ip rule show`` text."""
        tok = text.split()
        neg = tok[:1] == ["not"]
        tok = tok[1:] if neg else tok
        sel, act, i = {}, None, 0
        while i < len(tok):
            t = tok[i]
            if t in ("from", "to", "iif", "oif", "fwmark") and i + 1 < len(tok):
                sel[t] = tok[i + 1]
                i += 2
            elif t in ("lookup", "table") and i + 1 < len(tok):
                act = ("lookup", tok[i + 1])
                i += 2
            elif t == "suppress_prefixlength" and i + 1 < len(tok):
                sel["suppress"] = int(tok[i + 1])
                i += 2
            elif t in ("unreachable", "blackhole", "prohibit"):
                act = (t, None)
                i += 1
            else:
                i += 1
        return neg, sel, act

    @staticmethod
    def _in(a, net):
        return net == "all" or ipaddress.ip_address(a) in ipaddress.ip_network(net, strict=False)

    def _matches(self, sel, src, dst, iif, mark):
        if "from" in sel and not self._in(src, sel["from"]):
            return False
        if "to" in sel and not self._in(dst, sel["to"]):
            return False
        if "iif" in sel and sel["iif"] != iif:
            return False
        if "fwmark" in sel:
            v, _, m = sel["fwmark"].partition("/")
            if mark & (int(m, 0) if m else 0xffffffff) != int(v, 0):
                return False
        return True

    def _table_routes(self, table):
        rs = list(self.routes.get(table, []))
        if table == "local":                   # the kernel keeps a local route for every address of the node
            for name, l in self.links.items():
                rs += ["local %s dev %s scope host" % (a.split("/")[0], name) for a in l["addrs"]]
        return rs

    def _lookup(self, table, dst):
        """(prefix length, kind, dev, route) of the best route of ``table`` for dst — longest prefix, lowest
        metric; routes through a device that is gone do not count — or None."""
        best = None
        for r in self._table_routes(table):
            tok = r.split()
            kind = tok[0] if tok[0] in ("local", "unreachable", "blackhole", "prohibit", "throw") else "unicast"
            pfx = tok[1] if kind != "unicast" else tok[0]
            try:
                net = ipaddress.ip_network("0.0.0.0/0" if pfx == "default" else pfx, strict=False)
            except ValueError:
                continue
            if ipaddress.ip_address(dst) not in net:
                continue
            dev = tok[tok.index("dev") + 1] if "dev" in tok[:-1] else None
            if dev is not None and dev not in self.links:
                continue
            key = (net.prefixlen, -(int(tok[tok.index("metric") + 1]) if "metric" in tok[:-1] else 0))
            if best is None or key > best[0]:
                best = (key, kind, dev, r)
        return None if best is None else (best[0][0], best[1], best[2], best[3])

    def fib(self, src, dst, iif, mark):
        """The rule walk of the kernel: (kind, table, dev) — kind unicast/local or unreachable/blackhole/prohibit
        (table None when a rule's action decided)."""
        for _, text in self.ordered():
            neg, sel, act = self._rule_parts(text)
            hit = self._matches(sel, src, dst, iif, mark)
            if (not hit if neg else hit) is False or act is None:
                continue
            if act[0] != "lookup":
                return act[0], None, None
            found = self._lookup(act[1], dst)
            if found is None:
                continue
            plen, kind, dev, _ = found
            if "suppress" in sel and plen <= sel["suppress"]:
                continue
            if kind == "throw":
                continue
            return kind, act[1], dev
        return "unreachable", None, None

    def rp_filter(self, dev):
        """max(all, dev) of rp_filter (the sysctl fake of the world, when there is one)."""
        vals = getattr(self, "sysctl_values", None) or {}
        return max(int(vals.get("net.ipv4.conf.all.rp_filter", "0")),
                   int(vals.get("net.ipv4.conf.%s.rp_filter" % dev, "0")))

    def route_get(self, a):
        args = dict(zip(a[3::2], a[4::2]))
        dst, src, iif = a[2], args.get("from"), args.get("iif")
        mark = int(args.get("mark", "0"), 0)
        if iif not in self.links:
            return 1, "", 'Cannot find device "%s"\n' % iif
        kind, table, dev = self.fib(src, dst, iif, mark)
        if kind in ("unreachable", "blackhole", "prohibit"):
            return 2, "", {"unreachable": "RTNETLINK answers: Network is unreachable\n",
                           "blackhole": "RTNETLINK answers: Invalid argument\n",
                           "prohibit": "RTNETLINK answers: Permission denied\n"}[kind]
        rp = self.rp_filter(iif)
        if rp:                                  # the reverse path check: from <dst> to <src> with the same mark
            vals = getattr(self, "sysctl_values", None) or {}
            rmark = mark if vals.get("net.ipv4.conf.all.src_valid_mark") == "1" else 0
            rkind, _, rdev = self.fib(dst, src, "lo", rmark)
            if rkind == "local":
                return 2, "", "RTNETLINK answers: Invalid argument\n"          # martian
            if rkind != "unicast" or (rp == 1 and rdev != iif):
                return 2, "", "RTNETLINK answers: Invalid cross-device link\n"
        out = ("local " if kind == "local" else "") + "%s from %s dev %s" % (dst, src, dev or "lo")
        if table not in ("main", "254"):
            out += " table %s" % table
        if mark:
            out += " mark 0x%x" % mark
        return 0, out + " \n    cache iif %s \n" % iif, ""

    @staticmethod
    def _rule_spec(args):
        """(pref or None, text as ``ip rule show`` prints it) of ``[pref N] [from X] [to Y] [fwmark M[/K]]
        lookup|table T`` (iproute2: a zero mark is printed ``0``, a mask unless it is 0xffffffff)."""
        m = dict(zip(args[::2], args[1::2]))
        out = ["from %s" % m.get("from", "all")]
        if "to" in m:
            out.append("to %s" % m["to"])
        if "fwmark" in m:
            mark, _, mask = m["fwmark"].partition("/")
            mark = int(mark, 0)
            text = ("0x%x" % mark) if mark else "0"
            if mask and int(mask, 0) != 0xffffffff:
                text += "/0x%x" % int(mask, 0)
            out.append("fwmark %s" % text)
        out.append("lookup %s" % (m.get("lookup") or m.get("table")))
        return (int(m["pref"]) if "pref" in m else None), " ".join(out)

    def _rule_text(self, args):
        return self._rule_spec(args)[1]

    def _link_line(self, name):
        l = self.links[name]
        flags = ["BROADCAST", "NOARP"] + (["UP", "LOWER_UP"] if l["up"] else [])
        return "%d: %s: <%s> mtu 1500 qdisc noqueue state UNKNOWN mode DEFAULT group default qlen 1000\\" \
               "    link/ether 00:00:00:00:00:%02x brd ff:ff:ff:ff:ff:ff" % (l["idx"], name, ",".join(flags), l["idx"])

    def _addr_lines(self, names):
        out = []
        for n in names:
            l = self.links[n]
            for a in l["addrs"]:
                out.append("%d: %s    inet %s scope global %s\\       valid_lft forever preferred_lft forever"
                           % (l["idx"], n, a, n))
        return out

    def __call__(self, argv, input):
        self.calls.append(list(argv))
        a = list(argv[1:])
        if a[:1] == ["-4"]:
            a = a[1:]
        if a[:1] == ["-o"]:
            a = a[1:]
        if a[:2] == ["rule", "show"]:
            return 0, "".join("%d:\t%s\n" % r for r in self.ordered()), ""
        if a[:2] == ["rule", "add"]:
            pref, text = self._rule_spec(a[2:])
            if pref is None:                     # the kernel: the pref of the second rule of the list, minus 1
                pref = self.default_pref()
            if (pref, text) in self.rules:
                return 2, "", "RTNETLINK answers: File exists\n"
            self.rules.append((pref, text))      # after the rules of the same pref (ordered() is stable)
            return 0, "", ""
        if a[:2] == ["rule", "del"]:
            pref, text = self._rule_spec(a[2:])
            for r in self.ordered():
                if r[1] == text and (pref is None or r[0] == pref):
                    self.rules.remove(r)
                    return 0, "", ""
            return 2, "", "RTNETLINK answers: No such file or directory\n"
        if a[:2] == ["route", "get"] and "iif" in a:
            return self.route_get(a)
        if a[:2] == ["route", "get"]:
            src = self.links["eth0"]["addrs"][0].split("/")[0]
            return 0, "%s via 10.0.0.1 dev eth0 src %s uid 0 \n    cache \n" % (a[2], src), ""
        if a[:3] == ["route", "show", "table"]:
            t = a[3]
            if t == "all":
                out = []
                for name, rs in self.routes.items():
                    for r in rs:
                        out.append(r if name == "main" or " table " in r else "%s table %s" % (r, name))
                return 0, "".join(x + "\n" for x in out), ""
            return 0, "".join(x + "\n" for x in self.routes.get(t, [])), ""
        if a[:2] == ["route", "add"]:
            # the kernel: a route with the same prefix and metric is EEXIST WHATEVER its type (``replace`` would
            # take its place — F1, finding 10)
            spec = a[2:]
            t = spec[spec.index("table") + 1] if "table" in spec else "main"
            body = [x for i, x in enumerate(spec) if not (x == "table" or (i > 0 and spec[i - 1] == "table"))]
            text = " ".join(body).replace("0.0.0.0/0", "default")
            if body[:1] == ["local"]:
                text += " scope host"

            def key(r):
                tok = r.split()
                typed = tok[0] in ("local", "unreachable", "blackhole", "prohibit", "throw")
                return (tok[1] if typed else tok[0]).replace("0.0.0.0/0", "default"), \
                    (tok[tok.index("metric") + 1] if "metric" in tok[:-1] else "0")
            rs = self.routes.setdefault(t, [])
            if any(key(r) == key(text) for r in rs):
                return 2, "", "RTNETLINK answers: File exists\n"
            rs.append(text)
            return 0, "", ""
        if a[:2] == ["route", "replace"]:
            spec = a[2:]
            t = spec[spec.index("table") + 1] if "table" in spec else "main"
            body = [x for i, x in enumerate(spec) if not (x == "table" or (i > 0 and spec[i - 1] == "table"))]
            text = " ".join(body).replace("0.0.0.0/0", "default")
            if body[:1] == ["local"]:
                text += " scope host"
            rs = self.routes.setdefault(t, [])
            key = " ".join(text.split()[:2])
            rs[:] = [r for r in rs if " ".join(r.split()[:2]) != key] + [text]
            return 0, "", ""
        if a[:2] == ["route", "del"]:
            spec = a[2:]
            t = spec[spec.index("table") + 1] if "table" in spec else "main"
            body = [x for i, x in enumerate(spec) if not (x == "table" or (i > 0 and spec[i - 1] == "table"))]
            key = " ".join(body).replace("0.0.0.0/0", "default")
            rs = self.routes.get(t, [])
            for r in list(rs):
                if r == key or r.startswith(key + " "):
                    rs.remove(r)
                    return 0, "", ""
            return 2, "", "RTNETLINK answers: No such process\n"
        if a[:3] == ["route", "flush", "table"]:
            self.routes[a[3]] = []
            return 0, "", ""
        if a[:2] == ["link", "show"]:
            if len(a) >= 4 and a[2] == "dev":
                if a[3] not in self.links:
                    return 1, "", 'Device "%s" does not exist.\n' % a[3]
                return 0, self._link_line(a[3]) + "\n", ""
            return 0, "".join(self._link_line(n) + "\n" for n in self.links), ""
        if a[:2] == ["link", "add"]:
            if a[2] in self.links:
                return 2, "", "RTNETLINK answers: File exists\n"
            self.add_link(a[2], a[4] if len(a) > 4 else "dummy", up=False)
            return 0, "", ""
        if a[:2] == ["link", "del"]:
            if a[2] not in self.links:
                return 1, "", 'Cannot find device "%s"\n' % a[2]
            del self.links[a[2]]
            return 0, "", ""
        if a[:2] == ["link", "set"]:
            if a[2] not in self.links:
                return 1, "", 'Cannot find device "%s"\n' % a[2]
            self.links[a[2]]["up"] = "up" in a[3:]
            return 0, "", ""
        if a[:2] == ["addr", "show"]:
            if len(a) >= 4 and a[2] == "dev":
                if a[3] not in self.links:
                    return 1, "", 'Device "%s" does not exist.\n' % a[3]
                return 0, "".join(x + "\n" for x in self._addr_lines([a[3]])), ""
            return 0, "".join(x + "\n" for x in self._addr_lines(list(self.links))), ""
        if a[:2] in (["addr", "add"], ["addr", "del"]):
            cidr, dev = a[2], a[4]
            if dev not in self.links:
                return 1, "", 'Cannot find device "%s"\n' % dev
            l = self.links[dev]
            if a[1] == "add":
                if cidr in l["addrs"]:
                    return 2, "", "RTNETLINK answers: File exists\n"
                l["addrs"].append(cidr)
            else:
                if cidr not in l["addrs"]:
                    return 2, "", "RTNETLINK answers: Cannot assign requested address\n"
                l["addrs"].remove(cidr)
            return 0, "", ""
        return 1, "", "fake ip does not understand %r\n" % (argv,)


# ── awg, curl ──────────────────────────────────────────────────────────────

class FakeAwg(object):
    def __init__(self, ip, now):
        self.ip = ip
        self.now = now               # callable
        self.handshake_age = {}      # iface -> seconds ago (None: never)

    def __call__(self, argv, input):
        a = list(argv[1:])
        if a[:1] == ["show"] and len(a) == 3 and a[2] == "latest-handshakes":
            iface = a[1]
            if iface not in self.ip.links:
                return 1, "", "Unable to access interface: No such device\n"
            age = self.handshake_age.get(iface)
            ts = 0 if age is None else int(self.now() - age)
            return 0, "UEVFUktFWQ==\t%d\n" % ts, ""
        return 1, "", "fake awg does not understand %r\n" % (argv,)


class FakeCurl(object):
    """``--interface kawgN`` probes answer 204 for legs in ``legs_ok`` (and ``GwWorld.connect`` — the connections of
    the exits watchdog through a leg — succeed for them); the metrics URL of the gateway xray answers
    ``observatory`` (tag -> alive) while kaskad-gw-xray.service is active."""

    def __init__(self, sysd):
        self.sysd = sysd
        self.legs_ok = set()
        self.observatory = {}
        self.echo_ip = "198.51.100.200"          # what an IP echo service answers (1:1 NAT clouds)
        self.calls = []
        self.now = None                          # GwWorld's clock: last_try_time of every probe of xray (3.3.5)
        self.try_at = None                       # a fixed last_try_time instead: a frozen probe (the same one read again)
        self.every = None                        # xray probes every N s (its real cycle 10–15 s): a new probe — new
        self._probes = {}                        # last_try_time and the alive of the moment; between them the same one
        self.error = "context deadline exceeded"  # last_error_reason of a dead outbound

    def __call__(self, argv, input):
        self.calls.append(list(argv))
        a = list(argv[1:])
        url = a[-1]
        if "--interface" in a:
            iface = a[a.index("--interface") + 1]
            return (0, "204", "") if iface in self.legs_ok else (28, "000", "")
        if url == "https://api.ipify.org":
            return (0, self.echo_ip, "") if self.echo_ip else (6, "", "curl: (6) Could not resolve host\n")
        if url == "http://%s/debug/vars" % exits.METRICS_ADDR:
            if not (self.sysd.units.get("kaskad-gw-xray.service") or {}).get("active"):
                return 7, "", "curl: (7) Failed to connect to 127.0.0.1 port 10852\n"
            obs = {}
            for tag, alive in self.observatory.items():
                at = self.try_at if self.try_at is not None else int(self.now()) if self.now else 1790000000
                if self.every and self.try_at is None:
                    at = int(at // self.every * self.every)
                    last = self._probes.get(tag)
                    if last is not None and last[0] == at:
                        alive = last[1]                  # read again between two probes: what the last one said
                    else:
                        self._probes[tag] = (at, alive)
                rec = {"outbound_tag": tag, "delay": 120 if alive else 5000, "last_try_time": at}
                if alive:
                    rec["alive"] = True
                else:
                    rec["last_error_reason"] = self.error
                obs[tag] = rec
            return 0, json.dumps({"cmdline": ["xray"], "observatory": obs}), ""
        return 6, "", "curl: (6) not mocked %s\n" % url


# ── packages, ufw, xray, dnsmasq, the script ───────────────────────────────

class FakePackages(object):
    def __init__(self, runner, installed=()):
        self.runner = runner
        self.installed = set(installed)
        self.installs = []
        self.policy_seen = []
        self.fail = None

    def dpkg(self, argv, input):
        pkg = argv[-1]
        if pkg in self.installed:
            return 0, "install ok installed", ""
        return 1, "", "dpkg-query: no packages found matching %s\n" % pkg

    def apt(self, argv, input):
        a = list(argv)
        if a[:1] == ["env"]:                                 # env VAR=value ... apt-get
            a = a[1:]
            while a and "=" in a[0] and not a[0].startswith("-"):
                a = a[1:]
        if a[:2] == ["apt-get", "update"]:
            return 0, "", ""
        if a[:2] == ["apt-get", "install"]:
            if self.fail:
                return 100, "", self.fail
            pkgs = [x for x in a[2:] if not x.startswith("-") and "::" not in x and x not in ("DPkg::Lock::Timeout=120",)]
            self.policy_seen.append(os.path.exists(paths.policy_rc_d()))
            self.installs.append(pkgs)
            for p in pkgs:
                self.installed.add(p)
                if p == "dnsmasq-base":
                    self.runner.binaries.add("dnsmasq")
            return 0, "", ""
        return 1, "", "fake apt does not understand %r\n" % (argv,)


class FakeUfw(object):
    """``ufw status|allow|delete allow``. Every change rewrites /etc/ufw/user.rules (``allow``, ``delete``,
    ``reset``) or /etc/ufw/ufw.conf (``enable``, ``disable``) under KASKAD_ROOT, as ufw does — F2 compares their
    mtime and size. ``denied`` — specs the owner denies (``ufw deny``)."""

    def __init__(self, active=True):
        self.active = active
        self.rules = []          # [(spec, comment)]
        self.denied = []
        self.calls = []
        self._n = 0

    def _touch(self, name):
        self._n += 1
        p = paths.P("/etc/ufw/" + name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write("# fake ufw %d\n%s\n" % (self._n, "\n".join(s for s, _ in self.rules)))
        os.utime(p, (1790000000 + self._n, 1790000000 + self._n))

    def enable(self):
        self.active = True
        self._touch("ufw.conf")

    def disable(self):
        self.active = False
        self._touch("ufw.conf")

    def reset(self):
        """``ufw --force reset``: every rule gone, UFW off."""
        self.rules, self.denied = [], []
        self._touch("user.rules")
        self.disable()

    def deny(self, spec):
        self.denied.append(spec)
        self._touch("user.rules")

    def __call__(self, argv, input):
        a = list(argv[1:])
        self.calls.append(a)
        if a == ["status"]:
            if not self.active:
                return 0, "Status: inactive\n", ""
            out = ["Status: active", "", "To                         Action      From",
                   "--                         ------      ----", "22/tcp                     ALLOW       Anywhere"]
            for spec in self.denied:
                out.append("%-26s DENY        Anywhere" % spec)
            for spec, c in self.rules:
                src = spec if spec.startswith("Anywhere/") else "Anywhere"      # real ufw: «Anywhere/esp» in From too
                out.append("%-26s ALLOW       %-26s%s" % (spec, src, ("# " + c) if c else ""))
                out.append("%-26s ALLOW       %-26s%s" % (spec + " (v6)", src + " (v6)", ("# " + c) if c else ""))
            return 0, "\n".join(out) + "\n", ""
        if a[:1] == ["allow"]:
            comment = a[a.index("comment") + 1] if "comment" in a else ""
            spec = a[1] if a[1] != "proto" else "Anywhere/%s" % a[2]
            self.rules = [r for r in self.rules if r[0] != spec] + [(spec, comment)]
            self._touch("user.rules")
            return 0, "Rule added\nRule added (v6)\n", ""
        if a[:2] == ["delete", "allow"]:
            spec = a[2] if a[2] != "proto" else "Anywhere/%s" % a[3]
            before = len(self.rules)
            self.rules = [r for r in self.rules if r[0] != spec]
            if len(self.rules) != before:
                self._touch("user.rules")
            return (0, "Rule deleted\n", "") if len(self.rules) != before else (1, "", "Could not delete non-existent rule\n")
        return 1, "", "fake ufw does not understand %r\n" % (argv,)


class FakeXray(object):
    """``xray -test -c FILE``: JSON with unique outbound tags and routing that points at existing
    outbounds/balancers."""

    def __init__(self):
        self.fail = None
        self.tested = []
        self.stats = None             # ``api statsquery``: {login: (uplink, downlink)}; None — xray does not answer
        self.queries = []
        self.api = []                 # ``api adu|rmu|adrules|rmrules|ado|adi|rmo|rmi|bo``: (verb, args without files,
        #                               [file docs])
        self.override = {}            # ``api bo``: {balancer: tag} — ``api bi`` prints «Selecting Override: <tag>»
        self.api_fail = None          # a verb that fails (an old xray without it, xray down)
        self.real = []                # verdicts of the real xray (tests/realbin.py) on the configs tested here

    @staticmethod
    def _balancer_known(a):
        """``bo`` of the gateway's xray (port 10854): its balancer is in the routing it runs — the file on the disk
        (the hot path writes it before the API; a restart reads it). No file — not known here: taken."""
        if "--server=127.0.0.1:10854" not in a:
            return True
        try:
            with open(os.path.join(paths.gw_dir(), "xray.json"), encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            return True
        bal = a[a.index("-b") + 1]
        return any(b.get("tag") == bal for b in (cfg.get("routing") or {}).get("balancers") or [])

    def __call__(self, argv, input):
        a = list(argv[1:])
        if a[:2] == ["api", "bi"]:
            self.api.append(("bi", a[2:], []))
            if self.api_fail == "bi":
                return 1, "", "failed to dial\n"
            bal = a[-1]
            got = self.override.get(bal)
            return 0, ("Balancer %s\n  Selecting Override: %s\n" % (bal, got)) if got else "Balancer %s\n" % bal, ""
        if a[:1] == ["api"] and a[1:2] and a[1] in ("adu", "rmu", "adrules", "rmrules", "ado", "adi", "rmo", "rmi", "bo"):
            if a[1] == "adrules" and "-append" not in a:
                self.override.clear()            # T0: all the rules anew — the override of gw-all is gone
            if a[1] == "bo" and self.api_fail != "bo" and not self._balancer_known(a):
                # as the real xray (KT3-7/8): a balancer the running routing has not — «app/router: cannot find tag»
                self.api.append(("bo", [x for x in a[2:]], []))
                return 1, "", ("failed to perform balancer health checks: rpc error: code = Unknown desc = "
                               "app/router: cannot find tag\n")
            if a[1] == "bo" and self.api_fail != "bo":
                i = a.index("-b")
                if "-r" in a:
                    self.override.pop(a[i + 1], None)
                else:
                    self.override[a[i + 1]] = a[i + 2]
            files = [x for x in a[2:] if x.endswith(".json")]
            docs = []
            for f in files:
                if os.stat(f).st_mode & 0o077:
                    return 1, "", "fake xray: %s is readable by others\n" % f
                with open(f, encoding="utf-8") as fh:
                    docs.append(json.load(fh))
            self.api.append((a[1], [x for x in a[2:] if x not in files], docs))
            if self.api_fail == a[1]:
                return 1, "", "rpc error: code = Unimplemented desc = unknown service\n"
            return 0, "", ""
        if a[:2] == ["api", "statsquery"]:
            self.queries.append(a)
            if self.stats is None:
                return 1, "", "failed to dial 127.0.0.1:10854: connection refused\n"
            stat = []
            for lg, (up, down) in sorted(self.stats.items()):       # protobuf JSON: int64 as a string, 0 omitted
                stat.append({"name": "user>>>%s>>>traffic>>>uplink" % lg, "value": str(up)} if up else
                            {"name": "user>>>%s>>>traffic>>>uplink" % lg})
                stat.append({"name": "user>>>%s>>>traffic>>>downlink" % lg, "value": str(down)} if down else
                            {"name": "user>>>%s>>>traffic>>>downlink" % lg})
            return 0, json.dumps({"stat": stat}), ""
        if a[:2] == ["-test", "-c"] and len(a) == 3:
            with open(a[2], encoding="utf-8") as f:
                cfg = json.load(f)
            self.tested.append(cfg)                                  # every call counted, a refused one too (REV-s1-t5 З1)
            if self.fail:
                return 23, "", self.fail
            tags = [o["tag"] for o in cfg["outbounds"]]
            if len(set(tags)) != len(tags):
                return 23, "", "duplicate outbound tag\n"
            if cfg.get("metrics"):
                tags.append(cfg["metrics"]["tag"])
            if cfg.get("api"):                                       # the commander is an outbound of its tag
                tags.append(cfg["api"]["tag"])
            bals = set(b["tag"] for b in cfg.get("routing", {}).get("balancers", []))
            for b in cfg.get("routing", {}).get("balancers", []):
                if b.get("fallbackTag") and b["fallbackTag"] not in tags:      # «without probes» (§2.9): none
                    return 23, "", "balancer %s: no fallback %s\n" % (b["tag"], b["fallbackTag"])
            for r in cfg["routing"]["rules"]:
                if "outboundTag" in r and r["outboundTag"] not in tags:
                    return 23, "", "rule to unknown outbound %s\n" % r["outboundTag"]
                if "balancerTag" in r and r["balancerTag"] not in bals:
                    return 23, "", "rule to unknown balancer %s\n" % r["balancerTag"]
            if realbin.in_fakes():                 # T-M6-qs330b №1: the real xray has the last word when it is here
                ok, text = realbin.xray_check(cfg)
                self.real.append(ok)
                if not ok:
                    return 23, "", text
            return 0, "Xray 26.3.27 (Xray, Penetrates Everything.)\nConfiguration OK.\n", ""
        return 1, "", "fake xray does not understand %r\n" % (argv,)


class FakeDnsmasq(object):
    """``dnsmasq --version`` (``version``, default 2.90; ``nftset`` — the «Compile time options» line says nftset /
    no-nftset, None — no such line) and ``--test --conf-file=F`` (an older dnsmasq refuses filter-AAAA, one without
    nftset refuses nftset= lines — as the real ones do with an unknown or unsupported option)."""

    def __init__(self, version="2.90", nftset=True):
        self.version = version
        self.nftset = nftset

    def __call__(self, argv, input):
        a = list(argv[1:])
        if a == ["--version"]:
            out = "Dnsmasq version %s  Copyright (c) 2000-2024 Simon Kelley\n" % self.version
            if self.nftset is not None:
                out += ("Compile time options: IPv6 GNU-getopt DBus no-UBus i18n IDN2 DHCP DHCPv6 no-Lua TFTP "
                        "conntrack ipset %s auth cryptohash DNSSEC loop-detect inotify dumpfile\n"
                        % ("nftset" if self.nftset else "no-nftset"))
            return 0, out, ""
        if a[:1] == ["--test"] and a[1].startswith("--conf-file="):
            with open(a[1].split("=", 1)[1], encoding="utf-8") as f:
                text = f.read()
            if "listen-address=" not in text:
                return 1, "", "dnsmasq: bad config\n"
            if "\nfilter-AAAA\n" in text and tuple(int(x) for x in self.version.split(".")) < (2, 89):
                return 1, "", "dnsmasq: bad option at line 10 of %s\n" % a[1].split("=", 1)[1]
            if "\nnftset=" in text and not self.nftset:
                return 1, "", "dnsmasq: recompile with HAVE_NFTSET defined to enable nftset directives\n"
            return 0, "", "dnsmasq: syntax check OK.\n"
        return 1, "", "fake dnsmasq does not understand %r\n" % (argv,)


fake_dnsmasq = FakeDnsmasq()


class FakeUsers(object):
    """``useradd --system ... NAME`` and the lookup kaskad.gw.net does (``pwd.getpwnam``, patched by GwCase)."""

    def __init__(self):
        self.uids = {}
        self.calls = []

    def useradd(self, argv, input):
        self.calls.append(list(argv))
        name = argv[-1]
        if name in self.uids:
            return 9, "", "useradd: user '%s' already exists\n" % name
        self.uids[name] = 990 - len(self.uids)
        return 0, "", ""

    def userdel(self, argv, input):
        self.calls.append(list(argv))
        name = argv[-1]
        if name not in self.uids:
            return 6, "", "userdel: user '%s' does not exist\n" % name
        del self.uids[name]
        return 0, "", ""

    def getpwnam(self, name):
        if name not in self.uids:
            raise KeyError("getpwnam(): name not found: '%s'" % name)
        return collections.namedtuple("pw", "pw_name pw_uid pw_gid")(name, self.uids[name], self.uids[name])


class FakeConntrack(object):
    """``conntrack -D -s NET`` and ``conntrack -D --mark VALUE/MASK``: says how many flows it deleted (on stderr,
    as conntrack-tools does). ``marked`` — {ct mark: flows} the --mark deletions take from."""

    def __init__(self):
        self.calls = []
        self.flows = 3
        self.marked = {}
        self.dns_flows = {}        # SEC-3372 П-1: {(src, proto): flows} DNATed to the direct dnsmasq (reply sport 5364)
        self.legs = set()          # Ш4: {(Endpoint ip, port as text)} — the flows of the legs

    def __call__(self, argv, input):
        self.calls.append(list(argv[1:]))
        a = list(argv[1:])
        if a[:1] == ["-L"] and a[1:3] in (["-p", "udp"], ["-p", "tcp"]) and a[3:4] == ["--reply-port-src"]:
            rows = ["%s      %d 25 src=%s dst=10.77.0.1 sport=40000 dport=53 src=10.77.0.1 dst=%s sport=%s dport=40000 "
                    "mark=1258291200 use=1" % (proto, 17 if proto == "udp" else 6, src, src, a[4])
                    for (src, proto), n in sorted(self.dns_flows.items()) if proto == a[2] for _ in range(n)]
            return 0, "".join(r + "\n" for r in rows), ("conntrack v1.4.7 (conntrack-tools): %d flow entries have been "
                                                      "shown.\n" % len(rows))
        if a[:2] == ["-D", "-s"] and a[3:5] in (["-p", "udp"], ["-p", "tcp"]) and a[5:6] == ["--reply-port-src"]:
            n = self.dns_flows.pop((a[2], a[4]), 0)
            return (0 if n else 1), "", "conntrack v1.4.7 (conntrack-tools): %d flow entries have been deleted.\n" % n
        if argv[1:3] == ["-D", "-s"]:
            n, self.flows = self.flows, 0
            return 0, "", "conntrack v1.4.7 (conntrack-tools): %d flow entries have been deleted.\n" % n
        if a[:3] == ["-D", "-p", "udp"] and a[3:4] == ["-d"] and a[5:6] == ["--dport"] and len(a) == 7:
            n = 1 if (a[4], a[6]) in self.legs else 0      # Ш4: the flow of a leg to its Endpoint (gw/mip.py forget)
            return (0 if n else 1), "", "conntrack v1.4.7 (conntrack-tools): %d flow entries have been deleted.\n" % n
        if argv[1:3] == ["-D", "--mark"] and len(argv) == 4:
            value, mask = [int(x, 16) for x in argv[3].split("/")]
            hit = [m for m in self.marked if m & mask == value]
            n = sum(self.marked.pop(m) for m in hit)
            return (0 if n else 1), "", "conntrack v1.4.7 (conntrack-tools): %d flow entries have been deleted.\n" % n
        return 1, "", "fake conntrack does not understand %r\n" % (argv,)

    def mark_calls(self):
        return [c[2] for c in self.calls if c[:2] == ["-D", "--mark"]]


class FakeScript(object):
    """/opt/kaskad/bin/gokaskad: ``--egress-awg-up`` brings kawgN legs up like egress_awg_sync — every leg
    recreated (link, ip rule fwmark 256+N, table 256+N with the unreachable backstop); ``--egress-awg-heal``
    like egress_awg_heal — a leg without its interface is brought up alone, a leg with one gets its rule and
    routes back in place, no working leg is touched; ``--egress-ensure-parser`` writes the parser. The leg
    modes share ``route_leg`` — as they share egress_awg_route_leg."""

    def __init__(self, ip):
        self.ip = ip
        self.calls = []
        self.fail_legs = set()           # indices of legs that do not come up
        self.heal_rc = None              # an int: --egress-awg-heal does nothing and exits with it
        self.brought_up = []             # interfaces created (by up or heal), in order
        self.xray_ok = True              # --egress-ensure-xray: the download works

    def awg_indices(self):
        """The legs of both files (T15 Q4): upstream.txt 1…, upstream-ru.txt 201… (install.sh egress_awg_lists)."""
        out = []
        for path, base in ((paths.egress_up(), 0), (paths.egress_up_ru(), 200)):
            try:
                with open(path, encoding="utf-8") as f:
                    lines = [l.strip() for l in f if l.strip()]
            except OSError:
                lines = []
            out += [base + i for i, l in enumerate(lines, 1) if l.startswith("awg://") and (not base or i <= 32)]
        return out

    def route_leg(self, i, dev=True):
        """egress_awg_route_leg: the rule only if missing (an existing one is never deleted) — in its slot of the
        gateway's block (the pref of the reply rule + 4 + N, T-3379 З-2), without a pref when there is no block —
        the backstop and, with the interface up, the default route through it (``ip route replace``: in place)."""
        name, mark = "kawg%d" % i, 256 + i
        rule = "from all fwmark 0x%x lookup %d" % (mark, mark)
        if rule not in [t for _, t in self.ip.rules]:
            reply = [p for p, t in self.ip.ordered() if t == "from all fwmark 0x10000/0x10000 lookup main"]
            self.ip.rules.append((reply[0] + 4 + i if reply and 1 <= i <= 255 else self.ip.default_pref(), rule))
        rs = self.ip.routes.setdefault(str(mark), [])
        if "unreachable default metric 1000" not in rs:
            rs.insert(0, "unreachable default metric 1000")
        if dev and name in self.ip.links and not [r for r in rs if r.startswith("default dev %s" % name)]:
            rs.append("default dev %s scope link" % name)

    def __call__(self, argv, input):
        self.calls.append(list(argv[1:]))
        if argv[1:] == ["--egress-ensure-parser"]:
            install_parser()
            return 0, "", ""
        if argv[1:] == ["--egress-ensure-xray"]:
            if not self.xray_ok:
                return 1, "[*] Загрузка отдельного xray для egress (v26.3.27)...\nНе удалось скачать xray.\n", ""
            touch_exec(paths.egress_xray())
            return 0, "[*] Загрузка отдельного xray для egress (v26.3.27)...\n", ""
        if argv[1:] == ["--egress-awg-up"]:
            for i in self.awg_indices():
                self.route_leg(i, dev=False)               # fail closed before the leg is recreated
                self.leg_up(i)
            return 0, "", ""
        if argv[1:] == ["--egress-awg-heal"]:
            if self.heal_rc is not None:
                return self.heal_rc, "", "" if self.heal_rc == 0 else "Unknown option: --egress-awg-heal\n"
            rc = 0
            held = set((exits.ru_hold() or {}).get("legs") or ())
            for i in self.awg_indices():
                if "kawg%d" % i in held:                    # REV-s1-q4-2 С-1: waits for the apply — stays closed
                    self.route_leg(i, dev=False)
                    continue
                if "kawg%d" % i in self.ip.links:          # alive: its routing back in place
                    self.route_leg(i)
                    continue
                self.route_leg(i, dev=False)               # missing: closed, then brought up alone
                if not self.leg_up(i):
                    rc = 1
            return rc, "", ""
        if argv[1:] == ["--egress-add"]:
            return self.egress_add(input)
        if argv[1:2] == ["--egress-del"] and len(argv) == 4:
            return self.egress_del(argv[2], argv[3])
        if argv[1:2] == ["--egress-replace"] and len(argv) == 3:
            return self.egress_replace(argv[2], input)
        if argv[1:2] == ["--egress-ru-apply"] and len(argv) <= 3:
            return self.egress_ru_apply(argv[2] if len(argv) == 3 else "", input)
        return 2, "", "Unknown option\n"

    # --egress-ru-apply [hex16] (install.sh egress_ru_apply, T15 Q4): upstream-ru.txt from stdin when it is still the
    # one the core saw; then its legs and gw apply — never the bot (``ru_inputs`` — what came, ``ru_rc`` — its code)
    ru_rc = None

    def egress_ru_apply(self, want, text):
        import hashlib
        self.ru_inputs = getattr(self, "ru_inputs", []) + [text if want else None]
        if self.ru_rc is not None:
            return self.ru_rc, "", ""
        if want:
            try:
                with open(paths.egress_up_ru(), encoding="utf-8") as f:
                    cur = [l.strip() for l in f if l.strip()]
            except OSError:
                cur = []
            if hashlib.sha1("\n".join(cur).encode("utf-8")).hexdigest()[:16] != want:
                return 3, "Пул изменился — повторите.\n", ""
            keys = [l.strip() for l in (text or "").splitlines() if l.strip()]
            if not all(k.startswith(POOL_SCHEMES) for k in keys):
                return 1, "В новом пуле строка, которая не ключ, — пул не тронут.\n", ""
            if len(keys) > 32:
                return 5, "Достигнут предел 32 ключей.\n", ""
            write_ru_pool(keys)
        return 0, "", ""

    # --egress-replace <hex16> (install.sh egress_panel_replace): the whole pool from stdin when the pool is still the one
    # the core saw (sha1 of its non-blank stripped lines); ``replace_rc`` — its exit code instead of the work;
    # ``replace_race`` — a list of pools: each call first finds the pool changed by another window (written, rc 3)
    replace_rc = None
    replace_race = None

    def egress_replace(self, want, text):
        import hashlib
        self.replace_inputs = getattr(self, "replace_inputs", []) + [text]
        if self.replace_rc is not None:
            return self.replace_rc, "\x1b[0;31mКонфиг xray невалиден:\x1b[0m\n", ""
        if self.replace_race:
            self._write(self.replace_race.pop(0))
        cur = [l.strip() for l in self._pool() if l.strip()]
        if hashlib.sha1("\n".join(cur).encode("utf-8")).hexdigest()[:16] != want:
            return 3, "\x1b[1;33mПул изменился — повторите.\x1b[0m\n", ""
        keys = [l.strip() for l in (text or "").splitlines() if l.strip()]
        if not all(k.startswith(POOL_SCHEMES) for k in keys):
            return 1, "В новом пуле строка, которая не ключ, — пул не тронут.\n", ""
        if len(keys) > 100:
            return 5, "Достигнут предел 100 ключей.\n", ""
        self._write(keys)
        return 0, "\x1b[0;32mПул обновлён: ключей %d.\x1b[0m\n" % len(keys), ""

    # the modes of the panel (install.sh egress_panel_add / egress_panel_del): ``add_result`` — (rc, out, err) instead
    # of the work; else a line vless:// or awg:// is a key, the rest «Не удалось разобрать ключи.» (rc 1), keys the pool
    # has already — rc 4; ``del_rc`` — the exit code of --egress-del instead of the work
    add_result = None
    del_rc = None
    del_rebuild = None                   # (rc, out): the line is removed, then the rebuild fails like that

    def _pool(self):
        try:
            with open(paths.egress_up(), encoding="utf-8") as f:
                return f.read().splitlines()
        except OSError:
            return []

    def _write(self, lines):
        write_pool([l for l in lines])

    def egress_add(self, text):
        self.add_inputs = getattr(self, "add_inputs", []) + [text]
        if self.add_result is not None:
            return self.add_result
        keys = [l.strip() for l in (text or "").splitlines() if l.strip()]
        if not keys or not all(k.startswith(POOL_SCHEMES) for k in keys):
            return 1, "\x1b[0;31mНе удалось разобрать ключи.\x1b[0m\n", ""
        pool = self._pool()
        new = [k for k in keys if k not in [l.strip() for l in pool]]
        if not new:
            return 4, "\x1b[1;33mНовых ключей не добавлено (дубликаты или пусто).\x1b[0m\n", ""
        self._write([l for l in pool if l.strip()] + new)
        return 0, "\x1b[0;32mДобавлено ключей: %d.\x1b[0m\n" % len(new), ""

    def egress_del(self, n, hexid):
        if self.del_rc is not None:
            return self.del_rc, "", ""
        import hashlib
        pool = self._pool()
        live = [i for i, l in enumerate(pool) if l.strip()]
        k = int(n)
        if not 1 <= k <= len(live):
            return 1, "Неверный номер ключа.\n", ""
        line = pool[live[k - 1]]
        if hashlib.sha1(line.strip().encode("utf-8")).hexdigest()[:10] != hexid:
            return 3, "\x1b[1;33mПул изменился — обновите страницу.\x1b[0m\n", ""
        moved = self.handover(k, hexid)         # as install.sh egress_handover (REV-335-3): the routes first
        if isinstance(moved, tuple):
            return moved
        pool = self._pool()
        del pool[live[k - 1]]
        if os.path.exists(os.path.join(paths.egress_dir(), "subs.json")):     # as install.sh egress_pool_drop
            with open(os.path.join(paths.egress_dir(), "subs-excluded.pending"), "a", encoding="utf-8") as f:
                f.write("e-%s\n" % hexid)
        if not [l for l in pool if l.strip()]:
            self._write([])
            return 0, "Список опустел — выход отключён.\nВыход отключён — бот ходит в Telegram напрямую.\n", ""
        self._write(pool)
        if self.del_rebuild is not None:
            return self.del_rebuild[0], self.del_rebuild[1] + moved, ""
        return 0, moved + "\x1b[0;32mКлюч удалён.\x1b[0m\n", ""

    def handover(self, n, hexid):
        """install.sh egress_handover in the mode of the panel: ``gw exit-handover N hex`` of the core (in this process);
        moved — the mark ``egress_handover:<n>``; refused (the other copy may not take them — code 3), the state busy
        (code 6) or another failure of the core (3.3.6: its words, the mark ``handover_broken``) — code 7 and the mark
        ``egress_fail:handover[_unfit|_broken]``, the line stays."""
        from kaskad.errors import ConflictError, KaskadError, LockedError
        from kaskad.gw import ops
        try:
            res = ops.exit_handover(n, hexid)
        except ConflictError:
            return 7, "Маршруты не перешли: …\n@K3:egress_fail:handover_unfit\n", ""
        except LockedError:
            return 7, "Маршруты не перешли — удаление отменено, повторите.\n@K3:egress_fail:handover\n", ""
        except KaskadError as e:
            return 7, "\x1b[1;33m%s\x1b[0m\n@K3:egress_fail:handover_broken\n" % e, ""
        return "@K3:egress_handover:%d\n" % res["routes"] if res.get("routes") else ""

    def leg_up(self, i):
        """egress_awg_leg_up: this one leg (re)created; False if it does not come up (``fail_legs``)."""
        name = "kawg%d" % i
        self.ip.drop_link(name)
        if i in self.fail_legs:
            return False
        self.ip.add_link(name, "none", ["10.8.%d.5/32" % i])
        self.brought_up.append(name)
        self.route_leg(i)
        return True


class GwWorld(World):
    """World + every fake of the gateway. ``now`` is a callable shared with exits._now in tests."""

    def __init__(self, ufw=None, dnsmasq=True, now=None, **kw):
        kw.setdefault("binaries", GW_BINARIES if dnsmasq else tuple(b for b in GW_BINARIES if b != "dnsmasq"))
        super().__init__(**kw)
        self.runner.binaries = set(self.runner.binaries)
        self.now = now or (lambda: 1790000000.0)
        self.ip = FakeIp()
        self.ip.sysctl_values = self.sysctl.values           # rp_filter / src_valid_mark of ``ip route get``
        self.awg = FakeAwg(self.ip, lambda: self.now())
        self.curl = FakeCurl(self.sysd)
        self.curl.now = self.now
        self.pkgs = FakePackages(self.runner, installed=() if not dnsmasq else ("dnsmasq-base",))
        self.ufw = ufw
        self.xray = FakeXray()
        self.script = FakeScript(self.ip)
        self.dnsmasq = FakeDnsmasq()
        self.users = FakeUsers()
        self.conntrack = FakeConntrack()
        self.modprobe_fail = set()
        self.connects = []                   # marks of every round of connections through the legs
        self.journal = {}                    # unit -> what ``journalctl -u UNIT -o cat`` prints (F2: why it is down)
        touch_exec(paths.egress_awg_tool())
        touch_exec(paths.egress_xray())
        r = self.runner
        r.on(["ip"], self.ip)
        r.on([paths.egress_awg_tool()], self.awg)
        r.on(["curl"], self.curl)
        r.on(["dpkg-query"], self.pkgs.dpkg)
        r.on(["env", "DEBIAN_FRONTEND=noninteractive", "NEEDRESTART_SUSPEND=1", "apt-get"], self.pkgs.apt)
        r.on([paths.egress_xray()], self.xray)
        r.on([os.path.join(paths.opt(), "gw", "xray")], self.xray)          # the copy the service runs
        r.on(["/usr/sbin/dnsmasq"], self.dnsmasq)
        r.on([paths.script_copy()], self.script)
        r.on(["useradd"], self.users.useradd)
        r.on(["userdel"], self.users.userdel)
        r.on(["conntrack"], self.conntrack)
        r.on(["journalctl"], lambda argv, inp: (0, self.journal.get(argv[argv.index("-u") + 1], "")
                                                if "-u" in argv else "", ""))
        r.on(["modprobe"], lambda argv, inp: (1, "", "FATAL: Module %s not found\n" % argv[1])
             if argv[1] in self.modprobe_fail else (0, "", ""))
        if ufw is not None:
            r.binaries.add("ufw")
            r.on(["ufw"], ufw)
        r._handlers.insert(0, (["ss"], self.ss_with_xray))

    XRAY_PID = 4242

    def ss_with_xray(self, argv, inp):
        """``ss``: the listeners of the test (``self.ss``) plus, while kaskad-gw-xray.service is active, the public
        inbounds of its config (VLESS, SOCKS5) — what the real node shows (gw status checks VLESS by the fact)."""
        out = self.ss
        if (self.sysd.units.get("kaskad-gw-xray.service") or {}).get("active"):
            try:
                with open(os.path.join(paths.gw_dir(), "xray.json"), encoding="utf-8") as f:
                    cfg = json.load(f)
            except (OSError, ValueError):
                cfg = {}
            for ib in cfg.get("inbounds") or []:
                if ib.get("listen") in ("127.0.0.1", "::1"):
                    continue
                addr = "[::]" if ib.get("listen") == "::" else "0.0.0.0"
                if ib.get("protocol") == "hysteria":            # Hysteria2: UDP, a socket per port of «443,20000-20099»
                    for part in str(ib.get("port") or "").split(","):
                        lo, _, hi = part.partition("-")
                        for p in range(int(lo), int(hi or lo) + 1):
                            out += ('udp   UNCONN 0      0      %s:%d    %s:*    users:(("xray",pid=%d,fd=8))\n'
                                    % (addr, p, addr, self.XRAY_PID))
                    continue
                if not isinstance(ib.get("port"), int):
                    continue
                out += ('tcp   LISTEN 0      4096   %s:%d    %s:*    users:(("xray",pid=%d,fd=7))\n'
                        % (addr, ib["port"], addr, self.XRAY_PID))
        return 0, self._ss_kind(argv, out), ""

    @staticmethod
    def _ss_kind(argv, out):
        """As the real ss (iproute2 of Ubuntu 24.04, KT3-4): asked for ONE kind (``-Hlnup``, ``-Hltnp``) it prints only
        those sockets and no Netid column; for both kinds (``-Hltnup``) — with Netid."""
        flags = "".join(a[1:] for a in (argv or [])[1:] if a.startswith("-") and not a.startswith("--"))
        kinds = [k for k, f in (("tcp", "t"), ("udp", "u")) if f in flags]
        if len(kinds) != 1:
            return out
        rows = []
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2 and parts[0] == kinds[0]:
                rows.append(parts[1] + "\n")
        return "".join(rows)

    def connect(self, marks):
        """``exits.connect_through`` of this node: {mark: a connection through the leg 256+N succeeded} — the leg's
        interface exists and its far end answers (``legs_ok``); ``connects`` counts the rounds."""
        self.connects.append(sorted(marks))
        return dict((m, "kawg%d" % (m - exits.AWG_MARK_BASE) in self.ip.links
                     and "kawg%d" % (m - exits.AWG_MARK_BASE) in self.curl.legs_ok) for m in marks)

    def leg(self, n, handshake=30, probe=True):
        """Bring leg n up (as --egress-awg-up would: interface, rule, routes) with a handshake ``handshake`` s
        ago (None: never); ``probe`` — connections through it succeed (its far end answers)."""
        name = "kawg%d" % n
        if name not in self.ip.links:
            self.ip.add_link(name, "none", ["10.8.%d.5/32" % n])
        self.script.route_leg(n)
        self.awg.handshake_age[name] = handshake
        if probe:
            self.curl.legs_ok.add(name)
        else:
            self.curl.legs_ok.discard(name)

    def kill_leg(self, n):
        """The interface of leg n is gone (amneziawg-go died): its device route goes with it, the rule and the
        backstop stay."""
        name = "kawg%d" % n
        self.ip.drop_link(name)
        self.curl.legs_ok.discard(name)

    def networkd_restart(self):
        """What a restart of systemd-networkd without its 90-kaskad.conf does (stand, Ubuntu 26.04): every ip
        rule it did not create goes (here: all but the kernel's three) and every route without a device;
        device routes on links it does not manage stay."""
        self.ip.rules = [r for r in self.ip.rules
                         if r[1] in ("from all lookup local", "from all lookup main", "from all lookup default")]
        for table, rs in self.ip.routes.items():
            if table not in ("main", "local"):
                rs[:] = [r for r in rs if " dev " in " %s " % r]


# ── a ready gateway for tests ──────────────────────────────────────────────

T0 = 1790000000.0
ADDED = "2026-10-04T12:00:00Z"


def register_dummies(*specs):
    """Register DummyInbound adapters: specs are kinds or (kind, {kwargs})."""
    from kaskad.gw import registry
    from kaskad.gw.inbounds.base import DummyInbound
    out = {}
    for spec in specs:
        kind, kw = (spec, {}) if isinstance(spec, str) else spec
        ad = DummyInbound(kind=kind, **kw)
        registry.register(ad)
        out[kind] = ad
    return out


def scenario_state(enabled=True, forwards=(), alive=True):
    """Two inbounds (wg, awg2), three devices, the pool VLESS (line 1) + AmneziaWG (line 2 -> kawg2,
    mark 0x102). Returns (st, vless_id, awg_id). Writes the parser, upstream.txt and the exits snapshot."""
    from tests.fakes_nft import make_state
    install_parser()
    awg = awg_line(2, name="Amsterdam")
    write_pool([VLESS_A, awg])
    vid, aid = exits.exit_id(VLESS_A), exits.exit_id(awg)
    st = make_state(forwards)
    st["gateway"] = {
        "enabled": enabled,
        "net": {"base": "10.77.0.0/16", "dns": "10.77.0.1"},
        "inbounds": {"wg": {"enabled": True, "port": 51820, "subnet": "10.77.1.0/24"},
                     "awg2": {"enabled": True, "port": 41023, "subnet": "10.77.3.0/24", "line": "2.0"}},
        "users": [{"id": "u-00000001", "name": "Мама", "route": None, "enabled": True, "added": ADDED},
                  {"id": "u-00000002", "name": "Дача", "route": {"via": "exit:" + aid, "fail": "block"},
                   "enabled": True, "added": ADDED}],
        "devices": [
            {"id": "d-00000001", "user": "u-00000001", "name": "Телефон", "kind": "phone", "platform": "android",
             "route": None, "enabled": True, "added": ADDED, "login": "telefon-0001",
             "ips": {"wg": "10.77.1.10"}, "access": ["wg"]},
            {"id": "d-00000002", "user": "u-00000002", "name": "Роутер на даче", "kind": "router",
             "platform": "keenetic", "route": None, "enabled": True, "added": ADDED, "login": "router-0002",
             "ips": {"awg2": "10.77.3.10", "wg": "10.77.1.11"}, "access": ["awg2", "wg"]},
            {"id": "d-00000003", "user": "u-00000001", "name": "Ноутбук", "kind": "laptop", "platform": "linux",
             "route": {"via": "exit:" + vid, "fail": "direct"}, "enabled": True, "added": ADDED,
             "login": "noutbuk-0003", "ips": {"wg": "10.77.1.12"}, "access": ["wg"]}],
        "routes": {"default": {"via": "pool", "fail": "block"}},
        "exits": {vid: {"enabled": True, "name": "Вена"}},
    }
    exits.save_snapshot({"at": T0, "pool": [vid, aid],
                         "exits": {vid: {"alive": alive, "detail": "xray: доступен", "since": T0},
                                   aid: {"alive": alive, "detail": "рукопожатие 30 с назад", "since": T0}}})
    return st, vid, aid
