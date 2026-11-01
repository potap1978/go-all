"""3.3.7.15 F12а and F13, the rollback (the owner's rule 24.10: «старые данные → новая» и откат в каждом выпуске): the side
that runs in a tree of its own — the RELEASED 3.3.7.13 / 3.3.7.14 (``tests/test_r15_roles_rollback.py`` copies this
file into the trees of bc541e29 and fc5ef683) or this version (the same file, this tree). ``R15R_IO`` — the folder of
the exchange, ``R15R_OUT`` — the name of the answer.

The fake node of tests/test_mip_s34.Node (A — the address of the route, B — the second one of eth0; wg and awg2 as
dummies whose .conf names ``Endpoint = <gateway.host>:<port>``, the forward of 443, VLESS_A + the leg kawg2) with the
IPv6 2001:db8:10::5 on eth0 (the source of the IPv6 route), applied:

* ``null`` — none of the new keys: gw_post of the boot file, the outbound «direct» of xray, the subscription;
* ``give`` (the IPv6), ``out`` [A, B] and ``direct`` B written into state.json the way this version writes them:
  ``model.validate``, ``gw apply``, the subscription (its IPv6 lines), gw_post (the SNAT «напрямую»), «direct» of
  xray, the keys still in state.json after all of it.

Only what 3.3.7.13 has is used. Not a test of this tree: no ``test_`` in the name, skipped without ``R15R_OUT``."""

import base64
import ipaddress
import json
import os
import re
import unittest
from unittest import mock

from kaskad import cli, paths, state
from kaskad.gw import model, registry, xray
from kaskad.gw.inbounds import tunnel
from kaskad.gw.inbounds.base import DummyInbound
from tests.fakes_nft import F443
from tests.gwcase import GwCase
from tests.test_mip_s34 import A_IP, B_IP, leg_conf, two_addresses
from kaskad.gw import exits

IO = os.environ.get("R15R_IO", "")
OUT = os.environ.get("R15R_OUT", "")
X6 = "2001:db8:10::5"
IP6 = ("2: eth0    inet6 2001:db8:10::5/64 scope global dynamic mngtmpaddr \\       valid_lft 86313sec "
       "preferred_lft 14313sec\n")
ROUTE6 = "2606:4700:4700::1111 from :: via fe80::1 dev eth0 proto ra src 2001:db8:10::5 metric 1024 pref medium\n"


class EpDummy(DummyInbound):
    """WireGuard / AmneziaWG as a dummy whose .conf has the form of the real one (``[Interface]`` … ``Endpoint =
    host:port`` — brackets around an IPv6, as ``tunnel.hostport``)."""

    def issue(self, st, sec, device, os=None):
        port = (model.inbound(st, self.kind) or {}).get("port") or self._port or 51820
        body = "[Interface]\nPrivateKey = k-%s\n\n[Peer]\nPublicKey = SERVER-%s\nEndpoint = %s\n" % (
            device["id"], self.kind, tunnel.hostport(tunnel.host(st), port))
        rec = {"format": "conf", "title": self.title, "body": body, "filename": "%s.conf" % self.kind, "notes": []}
        return dict(rec, inbound=self.kind, items=[rec])


def _doc_addresses(case):
    """2001:db8::/32 as the external IPv6 of the server in this version (3.3.7.13/14 have no ``is_public6``)."""
    from kaskad.gw import addresses
    if not hasattr(addresses, "is_public6"):
        return
    doc = ipaddress.IPv6Network("2001:db8::/32")
    p = mock.patch.object(addresses, "is_public6",
                          lambda ip: addresses._v6(ip) is not None and (addresses._v6(ip).is_global or
                                                                        addresses._v6(ip) in doc))
    p.start()
    case.addCleanup(p.stop)


def chain(name):
    text = open(paths.nft_file(), encoding="utf-8").read()
    m = re.search(r"\n\tchain %s \{\n(.*?)\n\t\}" % re.escape(name), text, re.S)
    return [ln.strip() for ln in m.group(1).splitlines()[1:]] if m else []


def direct_out():
    cfg = json.load(open(xray.config_path(), encoding="utf-8"))
    return [o for o in cfg["outbounds"] if o.get("tag") == "direct"][0]


def ipv6_lines(links):
    out = []
    for l in links or []:
        if l.startswith("amneziawg://"):
            tok = l[len("amneziawg://"):].partition("#")[0]
            body = base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)).decode("utf-8")
            if "[%s]" % X6 in body:
                out.append(l)
        elif "[%s]" % X6 in l:
            out.append(l)
    return out


class Side(GwCase):
    def dummies(self):
        out = {}
        for kind, port in (("wg", 51820), ("awg2", 41023)):
            out[kind] = EpDummy(kind=kind, default_port=port, ifaces=["kgw-%s" % kind])
            registry.register(out[kind])
        return out

    def run_cli(self, *argv):
        try:
            return cli.main(list(argv))
        except SystemExit as e:
            return e.code

    def sub(self):
        from kaskad.panel import userpage
        got = userpage.build_sub({"id": "u-00000001"}, self.w.runner)
        return got[1] if got else None

    def with_ipv6(self):
        ip = self.w.ip

        def answer(argv, input):
            if list(argv[1:]) == ["-o", "addr", "show"]:
                rc, out, err = ip(argv, input)
                return rc, out + IP6, err
            if list(argv[1:4]) == ["-6", "route", "get"]:
                return 0, ROUTE6, ""
            return ip(argv, input)
        self.w.runner._handlers.insert(0, (["ip"], answer))

    def test_side(self):
        if not OUT:
            raise unittest.SkipTest("the side of tests/test_r15_roles_rollback.py")
        _doc_addresses(self)
        two_addresses(self)
        self.scenario(forwards=[F443])
        self.w.leg(2)
        aid = [e for e in exits.pool(self.w.runner) if e.get("kind") == "awg"][0]
        leg_conf(aid["iface"])
        self.with_ipv6()
        doc = {"host": self.run_cli("gw", "host", A_IP), "apply0": self.run_cli("gw", "apply")}
        from kaskad.gw import addresses
        addresses.collect(self.w.runner, net=False)     # the cache of «Адреса сервера» (the collector keeps it)
        doc["null"] = {"post": chain("gw_post"), "direct": direct_out(), "sub": self.sub()}
        with state.locked():
            st = state.load()
            a = st["gateway"].setdefault("addresses", {})
            a["give"] = {"items": [{"ip": X6, "n": 1}]}
            a["out"] = {"*": [A_IP, B_IP]}
            a["direct"] = B_IP
            state.save(st)
        try:
            model.validate(state.load())
            doc["validate"] = ""
        except Exception as e:                          # noqa: BLE001 — said, not raised: the answer is the test
            doc["validate"] = "%s: %s" % (type(e).__name__, e)
        doc["apply1"] = self.run_cli("gw", "apply")
        sub1 = self.sub()
        doc["sub1"] = sub1
        doc["ipv6_lines"] = len(ipv6_lines(sub1))
        doc["post1"] = chain("gw_post")
        doc["direct1"] = direct_out()
        doc["kept"] = dict((k, v) for k, v in (state.load()["gateway"].get("addresses") or {}).items()
                           if k in ("give", "out", "direct"))
        with open(os.path.join(IO, OUT + ".json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, sort_keys=True, indent=1)


if __name__ == "__main__":
    unittest.main()
